"""Stage 3 — synthesis.

One designated model combines the stage-1 answers and the stage-2 fact-check
notes into a single consolidated answer. The model's own citations are never
trusted directly: this stage only extracts candidate URLs from the text so
`citations.py` can verify them with real HTTP requests before anything is
shown to the user as "verified".
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx

from .. import db
from ..config import AppConfig
from ..providers import get_adapter

_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+")

_PROMPT_TEMPLATE = """You are synthesizing a single, high-quality answer to a user's prompt from six independent AI models' answers plus fact-check notes flagging claims those models disagreed on or couldn't support.

USER PROMPT:
{prompt}

--- STAGE 1: INDEPENDENT MODEL ANSWERS ---
{answers_block}

--- STAGE 2: FACT-CHECK NOTES (verdict + confidence + suggested correction per flagged claim) ---
{fact_check_block}

Write one consolidated, accurate answer to the user's prompt. Prefer claims that are supported/consensus across models; apply corrections from the fact-check notes; explicitly note any point where models genuinely disagree and it isn't resolved.

If you cite a source, include its literal URL in parentheses right after the claim, e.g. "(https://example.com/page)". Only include a URL if it appeared verbatim in one of the six answers above, or if you are highly confident it is a real, resolvable URL — every URL you output will be programmatically checked with a live HTTP request and removed from the final answer if it doesn't resolve, so do not pad the answer with invented-looking citations."""


def _format_fact_check_block(fact_checks: list[dict]) -> str:
    if not fact_checks:
        return "(fact-check stage was skipped or produced no results)"
    lines = []
    for fc in fact_checks:
        if fc["status"] != "ok" or not fc.get("claims_json"):
            continue
        claims = json.loads(fc["claims_json"])
        if not claims:
            continue
        lines.append(f"[{fc['checker_provider']} reviewing {fc['subject_provider']}]:")
        for c in claims:
            lines.append(
                f"  - claim: {c.get('claim')!r} | verdict: {c.get('verdict')} | "
                f"confidence: {c.get('confidence')} | correction: {c.get('correction')}"
            )
    return "\n".join(lines) or "(no flagged claims)"


def extract_urls(text: str) -> list[str]:
    seen: list[str] = []
    for m in _URL_RE.findall(text or ""):
        url = m.rstrip(".,;:!?")
        if url not in seen:
            seen.append(url)
    return seen


async def run_stage3(run_id: str, prompt: str, cfg: AppConfig, force: bool = False) -> dict:
    existing = db.get_synthesis_result(run_id)
    if existing and existing["status"] == "ok" and not force:
        return existing

    run = db.get_run(run_id)
    provider_key = (run.get("synthesis_provider") if run else None) or cfg.stages.synthesis_provider
    if provider_key not in cfg.providers:
        raise ValueError(f"unknown synthesis_provider: {provider_key}")

    stage1_rows = db.get_stage1_responses(run_id)
    ok_rows = [r for r in stage1_rows if r["status"] == "ok" and r["response_text"]]
    if not ok_rows:
        db.upsert_synthesis_result(
            run_id=run_id, provider=provider_key, status="error", synthesis_text=None,
            raw_response=None, error="no successful stage-1 answers to synthesize from",
            input_tokens=None, output_tokens=None, cost_usd=None, latency_ms=None,
        )
        return db.get_synthesis_result(run_id)

    answers_block = "\n\n".join(f"[{r['provider']}]:\n{r['response_text']}" for r in ok_rows)
    fact_checks = db.get_fact_check_results(run_id)
    fact_check_block = _format_fact_check_block(fact_checks)

    synth_prompt = _PROMPT_TEMPLATE.format(prompt=prompt, answers_block=answers_block, fact_check_block=fact_check_block)

    pcfg = cfg.providers[provider_key]
    adapter = get_adapter(pcfg)
    async with httpx.AsyncClient(timeout=cfg.stages.stage3_timeout + 5) as client:
        try:
            result = await asyncio.wait_for(adapter.generate(client, synth_prompt), timeout=cfg.stages.stage3_timeout)
        except asyncio.TimeoutError:
            result = None

    if result is None:
        db.upsert_synthesis_result(
            run_id=run_id, provider=provider_key, status="timeout", synthesis_text=None,
            raw_response=None, error=f"exceeded {cfg.stages.stage3_timeout}s stage-3 timeout",
            input_tokens=None, output_tokens=None, cost_usd=None, latency_ms=None,
        )
    else:
        db.upsert_synthesis_result(
            run_id=run_id, provider=provider_key, status=result.status, synthesis_text=result.text,
            raw_response=result.raw, error=result.error, input_tokens=result.input_tokens,
            output_tokens=result.output_tokens, cost_usd=result.cost_usd, latency_ms=result.latency_ms,
        )

    return db.get_synthesis_result(run_id)
