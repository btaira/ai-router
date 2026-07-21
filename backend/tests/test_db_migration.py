import sqlite3

from app import db as db_module


def test_synthesis_thinking_text_round_trips(test_db):
    run_id = test_db.create_run(prompt="x", skip_stage2=False, stage2_mode="designated_fact_checkers",
                                 synthesis_provider="anthropic")
    test_db.upsert_synthesis_result(
        run_id=run_id, provider="anthropic", status="ok", synthesis_text="answer",
        raw_response={}, error=None, input_tokens=1, output_tokens=1, cost_usd=0.01, latency_ms=10,
        thinking_text="weighed answer A against answer B...",
    )
    result = test_db.get_synthesis_result(run_id)
    assert result["thinking_text"] == "weighed answer A against answer B..."


def test_synthesis_thinking_text_defaults_to_none(test_db):
    run_id = test_db.create_run(prompt="x", skip_stage2=False, stage2_mode="designated_fact_checkers",
                                 synthesis_provider="anthropic")
    test_db.upsert_synthesis_result(
        run_id=run_id, provider="anthropic", status="ok", synthesis_text="answer",
        raw_response={}, error=None, input_tokens=1, output_tokens=1, cost_usd=0.01, latency_ms=10,
    )
    result = test_db.get_synthesis_result(run_id)
    assert result["thinking_text"] is None


def test_run_fact_checkers_round_trips(test_db):
    run_id = test_db.create_run(prompt="x", skip_stage2=False, stage2_mode="designated_fact_checkers",
                                 synthesis_provider="anthropic", fact_checkers=["anthropic", "openai"])
    run = test_db.get_run(run_id)
    assert run["fact_checkers"] == ["anthropic", "openai"]

    runs = test_db.list_runs()
    assert next(r for r in runs if r["run_id"] == run_id)["fact_checkers"] == ["anthropic", "openai"]


def test_run_fact_checkers_defaults_to_none(test_db):
    run_id = test_db.create_run(prompt="x", skip_stage2=False, stage2_mode="designated_fact_checkers",
                                 synthesis_provider="anthropic")
    run = test_db.get_run(run_id)
    assert run["fact_checkers"] is None


def test_fact_check_checker_model_round_trips(test_db):
    run_id = test_db.create_run(prompt="x", skip_stage2=False, stage2_mode="designated_fact_checkers",
                                 synthesis_provider="anthropic")
    test_db.upsert_fact_check_result(
        run_id=run_id, checker_provider="anthropic", subject_provider="openai", status="ok",
        claims=[], raw_response={}, error=None, input_tokens=1, output_tokens=1, cost_usd=0.01, latency_ms=10,
        checker_model="claude-sonnet-5",
    )
    result = test_db.get_fact_check_results(run_id)[0]
    assert result["checker_model"] == "claude-sonnet-5"


def test_fact_check_checker_model_defaults_to_none(test_db):
    run_id = test_db.create_run(prompt="x", skip_stage2=False, stage2_mode="designated_fact_checkers",
                                 synthesis_provider="anthropic")
    test_db.upsert_fact_check_result(
        run_id=run_id, checker_provider="anthropic", subject_provider="openai", status="ok",
        claims=[], raw_response={}, error=None, input_tokens=1, output_tokens=1, cost_usd=0.01, latency_ms=10,
    )
    result = test_db.get_fact_check_results(run_id)[0]
    assert result["checker_model"] is None


def test_init_db_migrates_a_database_created_before_these_columns_existed(tmp_path, monkeypatch):
    # Simulate an existing (pre-upgrade) database: the same runs/
    # synthesis_results/fact_check_results tables, but without
    # fact_checkers/thinking_text/checker_model — as if it were created by
    # an older version of SCHEMA before those columns were added.
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY, prompt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            skip_stage2 INTEGER NOT NULL DEFAULT 0, stage2_mode TEXT, synthesis_provider TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE synthesis_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL UNIQUE, provider TEXT NOT NULL,
            status TEXT NOT NULL, synthesis_text TEXT, raw_response_json TEXT, error TEXT,
            input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, latency_ms REAL, created_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE fact_check_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, checker_provider TEXT NOT NULL,
            subject_provider TEXT NOT NULL, status TEXT NOT NULL, claims_json TEXT, raw_response_json TEXT,
            error TEXT, input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL, latency_ms REAL,
            created_at REAL NOT NULL, UNIQUE(run_id, checker_provider, subject_provider)
        )
    """)
    conn.execute(
        "INSERT INTO runs (run_id, prompt, created_at, updated_at) VALUES ('preexisting', 'old prompt', 1.0, 1.0)"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module.init_db()  # must not raise, and must add the missing columns

    run = db_module.get_run("preexisting")
    assert run["prompt"] == "old prompt"  # pre-existing data survived
    assert run["fact_checkers"] is None  # new column, no value yet — reads back cleanly

    # the new columns are now genuinely usable, not just present
    db_module.upsert_synthesis_result(
        run_id="preexisting", provider="anthropic", status="ok", synthesis_text="answer",
        raw_response={}, error=None, input_tokens=1, output_tokens=1, cost_usd=0.01, latency_ms=10,
        thinking_text="reasoning trace",
    )
    assert db_module.get_synthesis_result("preexisting")["thinking_text"] == "reasoning trace"

    db_module.upsert_fact_check_result(
        run_id="preexisting", checker_provider="anthropic", subject_provider="openai", status="ok",
        claims=[], raw_response={}, error=None, input_tokens=1, output_tokens=1, cost_usd=0.01, latency_ms=10,
        checker_model="claude-sonnet-5",
    )
    assert db_module.get_fact_check_results("preexisting")[0]["checker_model"] == "claude-sonnet-5"
