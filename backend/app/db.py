"""SQLite persistence layer.

Schema is intentionally denormalized/simple: one row per provider call at
each stage, keyed by run_id, so a run's progress can be inspected or
resumed by querying what already exists before spending money re-running
a stage.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(os.environ.get("AI_ROUTER_DB", Path(__file__).resolve().parent.parent / "data" / "ai_router.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    skip_stage2 INTEGER NOT NULL DEFAULT 0,
    stage2_mode TEXT,
    synthesis_provider TEXT,
    fact_checkers TEXT,                -- JSON list of provider keys; NULL = use the deployment's configured default
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS stage1_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL,              -- ok | error | timeout
    request_json TEXT,
    response_text TEXT,
    thinking_text TEXT,
    raw_response_json TEXT,
    error TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms REAL,
    created_at REAL NOT NULL,
    UNIQUE(run_id, provider)
);

CREATE TABLE IF NOT EXISTS fact_check_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    checker_provider TEXT NOT NULL,
    subject_provider TEXT NOT NULL,     -- the stage1 answer being reviewed
    status TEXT NOT NULL,               -- ok | error | timeout
    claims_json TEXT,                   -- structured list of {claim, verdict, confidence, correction}
    raw_response_json TEXT,
    error TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms REAL,
    created_at REAL NOT NULL,
    UNIQUE(run_id, checker_provider, subject_provider)
);

CREATE TABLE IF NOT EXISTS synthesis_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL,
    status TEXT NOT NULL,
    synthesis_text TEXT,
    thinking_text TEXT,
    raw_response_json TEXT,
    error TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms REAL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS citation_verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    url TEXT NOT NULL,
    found_in_sources INTEGER NOT NULL DEFAULT 0,
    http_status INTEGER,
    verified INTEGER NOT NULL DEFAULT 0,
    method TEXT,
    error TEXT,
    checked_at REAL NOT NULL,
    UNIQUE(run_id, url)
);

-- Post-synthesis "go deeper" chat with just the synthesis model. turn_index
-- increments per run (0, 1, 2...) with a user row and its assistant reply
-- sharing consecutive indices, so ordering is stable without relying on
-- created_at (two calls in the same millisecond would otherwise tie).
CREATE TABLE IF NOT EXISTS followup_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    role TEXT NOT NULL,                -- user | assistant
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok', -- ok | error | timeout (user rows are always 'ok')
    error TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_ms REAL,
    created_at REAL NOT NULL
);
"""


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    """Adds `column` to `table` if an existing (pre-upgrade) database's copy
    doesn't have it yet — `CREATE TABLE IF NOT EXISTS` in SCHEMA only
    applies to brand-new databases, so a column added to that string later
    needs an explicit ALTER TABLE fallback for anyone upgrading in place.
    """
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "synthesis_results", "thinking_text", "TEXT")
        _ensure_column(conn, "runs", "fact_checkers", "TEXT")


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def create_run(prompt: str, skip_stage2: bool, stage2_mode: str, synthesis_provider: str,
                fact_checkers: list[str] | None = None) -> str:
    run_id = new_run_id()
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, prompt, status, skip_stage2, stage2_mode, synthesis_provider, "
            "fact_checkers, created_at, updated_at) VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?)",
            (run_id, prompt, int(skip_stage2), stage2_mode, synthesis_provider,
             json.dumps(fact_checkers) if fact_checkers is not None else None, now, now),
        )
    return run_id


def update_run_status(run_id: str, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
            (status, time.time(), run_id),
        )


def mark_stray_running_rows_cancelled(run_id: str) -> None:
    """On cancellation, any stage1/fact-check row still stuck at status
    'running' means that provider call was cancelled mid-flight and never
    got to write its own final status — without this it would show as
    "thinking…" forever in the UI. Flip those specifically to 'cancelled'.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE stage1_responses SET status='cancelled', error='cancelled by user' "
            "WHERE run_id=? AND status='running'",
            (run_id,),
        )


def _decode_run_row(row: dict[str, Any]) -> dict[str, Any]:
    row["fact_checkers"] = json.loads(row["fact_checkers"]) if row.get("fact_checkers") else None
    return row


def get_run(run_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return _decode_run_row(dict(row)) if row else None


def delete_run(run_id: str) -> bool:
    """Delete a run and everything logged under it. There's no FOREIGN KEY
    declared between the stage tables and `runs` (they're linked only by a
    plain run_id column, not an actual constraint), so each table is
    cleared explicitly rather than relying on cascading deletes.

    Returns False if the run didn't exist (nothing to delete).
    """
    with get_conn() as conn:
        existed = conn.execute("SELECT 1 FROM runs WHERE run_id = ?", (run_id,)).fetchone() is not None
        if not existed:
            return False
        for table in ("stage1_responses", "fact_check_results", "synthesis_results",
                       "citation_verifications", "followup_messages", "runs"):
            conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
        return True


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_decode_run_row(dict(r)) for r in rows]


def upsert_stage1_response(run_id: str, provider: str, model: str, status: str, request: dict,
                            response_text: str | None, thinking_text: str | None, raw_response: Any,
                            error: str | None, input_tokens: int | None, output_tokens: int | None,
                            cost_usd: float | None, latency_ms: float | None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO stage1_responses
                (run_id, provider, model, status, request_json, response_text, thinking_text,
                 raw_response_json, error, input_tokens, output_tokens, cost_usd, latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, provider) DO UPDATE SET
                model=excluded.model, status=excluded.status, request_json=excluded.request_json,
                response_text=excluded.response_text, thinking_text=excluded.thinking_text,
                raw_response_json=excluded.raw_response_json, error=excluded.error,
                input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens,
                cost_usd=excluded.cost_usd, latency_ms=excluded.latency_ms, created_at=excluded.created_at
            """,
            (run_id, provider, model, status, json.dumps(request), response_text, thinking_text,
             json.dumps(raw_response, default=str) if raw_response is not None else None,
             error, input_tokens, output_tokens, cost_usd, latency_ms, time.time()),
        )


def get_stage1_responses(run_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM stage1_responses WHERE run_id = ? ORDER BY provider", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_fact_check_result(run_id: str, checker_provider: str, subject_provider: str, status: str,
                              claims: list | None, raw_response: Any, error: str | None,
                              input_tokens: int | None, output_tokens: int | None,
                              cost_usd: float | None, latency_ms: float | None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO fact_check_results
                (run_id, checker_provider, subject_provider, status, claims_json, raw_response_json,
                 error, input_tokens, output_tokens, cost_usd, latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, checker_provider, subject_provider) DO UPDATE SET
                status=excluded.status, claims_json=excluded.claims_json,
                raw_response_json=excluded.raw_response_json, error=excluded.error,
                input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens,
                cost_usd=excluded.cost_usd, latency_ms=excluded.latency_ms, created_at=excluded.created_at
            """,
            (run_id, checker_provider, subject_provider, status,
             json.dumps(claims) if claims is not None else None,
             json.dumps(raw_response, default=str) if raw_response is not None else None,
             error, input_tokens, output_tokens, cost_usd, latency_ms, time.time()),
        )


def get_fact_check_results(run_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM fact_check_results WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def upsert_synthesis_result(run_id: str, provider: str, status: str, synthesis_text: str | None,
                             raw_response: Any, error: str | None, input_tokens: int | None,
                             output_tokens: int | None, cost_usd: float | None, latency_ms: float | None,
                             thinking_text: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO synthesis_results
                (run_id, provider, status, synthesis_text, thinking_text, raw_response_json, error,
                 input_tokens, output_tokens, cost_usd, latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                provider=excluded.provider, status=excluded.status, synthesis_text=excluded.synthesis_text,
                thinking_text=excluded.thinking_text,
                raw_response_json=excluded.raw_response_json, error=excluded.error,
                input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens,
                cost_usd=excluded.cost_usd, latency_ms=excluded.latency_ms, created_at=excluded.created_at
            """,
            (run_id, provider, status, synthesis_text, thinking_text,
             json.dumps(raw_response, default=str) if raw_response is not None else None,
             error, input_tokens, output_tokens, cost_usd, latency_ms, time.time()),
        )


def get_synthesis_result(run_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM synthesis_results WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None


def upsert_citation_verification(run_id: str, url: str, found_in_sources: bool, http_status: int | None,
                                  verified: bool, method: str | None, error: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO citation_verifications
                (run_id, url, found_in_sources, http_status, verified, method, error, checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, url) DO UPDATE SET
                found_in_sources=excluded.found_in_sources, http_status=excluded.http_status,
                verified=excluded.verified, method=excluded.method, error=excluded.error,
                checked_at=excluded.checked_at
            """,
            (run_id, url, int(found_in_sources), http_status, int(verified), method, error, time.time()),
        )


def get_citation_verifications(run_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM citation_verifications WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_followup_message(run_id: str, turn_index: int, role: str, content: str, status: str = "ok",
                          error: str | None = None, input_tokens: int | None = None,
                          output_tokens: int | None = None, cost_usd: float | None = None,
                          latency_ms: float | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO followup_messages
                (run_id, turn_index, role, content, status, error, input_tokens, output_tokens, cost_usd,
                 latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, turn_index, role, content, status, error, input_tokens, output_tokens, cost_usd,
             latency_ms, time.time()),
        )


def get_followup_messages(run_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM followup_messages WHERE run_id = ? ORDER BY turn_index, id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def next_followup_turn_index(run_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(turn_index), -1) AS m FROM followup_messages WHERE run_id = ?", (run_id,)
        ).fetchone()
        return row["m"] + 1


def run_cost_summary(run_id: str) -> dict[str, float]:
    with get_conn() as conn:
        s1 = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS c FROM stage1_responses WHERE run_id=?", (run_id,)
        ).fetchone()["c"]
        s2 = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS c FROM fact_check_results WHERE run_id=?", (run_id,)
        ).fetchone()["c"]
        s3 = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS c FROM synthesis_results WHERE run_id=?", (run_id,)
        ).fetchone()["c"]
        fu = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS c FROM followup_messages WHERE run_id=?", (run_id,)
        ).fetchone()["c"]
        return {
            "stage1_usd": s1, "stage2_usd": s2, "stage3_usd": s3, "followup_usd": fu,
            "total_usd": s1 + s2 + s3 + fu,
        }


def _empty_stage_totals() -> dict[str, float]:
    return {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


def run_cost_by_provider(run_id: str) -> dict[str, dict[str, dict[str, float]]]:
    """Per-provider cost/tokens broken down by the stage that spent it.

    stage1: that provider's own answer. stage2: sum of every fact-check call
    where this provider acted as checker (it may check several subjects).
    stage3: only non-zero for whichever provider ran the synthesis step.
    """
    with get_conn() as conn:
        stage1_rows = conn.execute(
            "SELECT provider, input_tokens, output_tokens, cost_usd FROM stage1_responses WHERE run_id=?", (run_id,)
        ).fetchall()
        stage2_rows = conn.execute(
            "SELECT checker_provider, input_tokens, output_tokens, cost_usd FROM fact_check_results WHERE run_id=?",
            (run_id,),
        ).fetchall()
        stage3_rows = conn.execute(
            "SELECT provider, input_tokens, output_tokens, cost_usd FROM synthesis_results WHERE run_id=?", (run_id,)
        ).fetchall()
        followup_rows = conn.execute(
            "SELECT input_tokens, output_tokens, cost_usd FROM followup_messages WHERE run_id=? AND role='assistant'",
            (run_id,),
        ).fetchall()
        run_row = conn.execute("SELECT synthesis_provider FROM runs WHERE run_id=?", (run_id,)).fetchone()

    breakdown: dict[str, dict[str, dict[str, float]]] = {}

    def _stage(provider: str, stage: str) -> dict[str, float]:
        entry = breakdown.setdefault(provider, {
            "stage1": _empty_stage_totals(), "stage2": _empty_stage_totals(),
            "stage3": _empty_stage_totals(), "followup": _empty_stage_totals(),
        })
        return entry[stage]

    def _add(target: dict[str, float], row: Any) -> None:
        target["input_tokens"] += row["input_tokens"] or 0
        target["output_tokens"] += row["output_tokens"] or 0
        target["cost_usd"] += row["cost_usd"] or 0.0

    for r in stage1_rows:
        _add(_stage(r["provider"], "stage1"), r)
    for r in stage2_rows:
        _add(_stage(r["checker_provider"], "stage2"), r)
    for r in stage3_rows:
        _add(_stage(r["provider"], "stage3"), r)
    synthesis_provider = run_row["synthesis_provider"] if run_row else None
    if synthesis_provider and followup_rows:
        for r in followup_rows:
            _add(_stage(synthesis_provider, "followup"), r)

    for stages in breakdown.values():
        stages["total"] = {
            "input_tokens": sum(s["input_tokens"] for s in stages.values()),
            "output_tokens": sum(s["output_tokens"] for s in stages.values()),
            "cost_usd": sum(s["cost_usd"] for s in stages.values()),
        }

    return breakdown
