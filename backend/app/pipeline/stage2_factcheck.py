"""Stage 2 — cross-examination / fact-check mesh.

Three configurable modes (`pipeline.stage2.mode` in providers.yaml, or a
per-run override):

- designated_fact_checkers (default): a fixed, small list of models each
  review every stage-1 answer.
- full_mesh: every stage-1 model reviews every stage-1 answer (including its
  own) — most thorough, most expensive.
- diff_then_check: a cheap non-LLM diff (see claim_diff.py) finds claims with
  no close match in any other answer, and only those get sent to the
  designated fact-checkers for adjudication.

Each fact-check call receives the original prompt, the single answer being
checked, and the other answers as reference context, and must return
structured JSON (claim/verdict/confidence/correction) — never free text —
so stage 3 can consume it mechanically.
"""
from __future__ import annotations

import asyncio
import json

import httpx

from .. import db
from ..config import AppConfig, strip_sampling_overrides
from ..providers import ProviderResult, get_adapter
from . import claim_diff
from .json_utils import extract_json

_PROMPT_TEMPLATE = """You are fact-checking one AI model's answer to a user prompt, using other models' answers purely as cross-reference context (they are not guaranteed correct either).

USER PROMPT:
{prompt}

ANSWER TO CHECK (from {subject}):
{subject_answer}

OTHER MODELS' ANSWERS (reference only, for spotting contradictions/consensus):
{others_block}
{focus_block}
Identify factual claims in the ANSWER TO CHECK that are unsupported, contradicted by other sources, or inconsistent with well-established facts. Respond with ONLY valid JSON (no prose, no code fences) matching exactly this schema:

{{"claims": [{{"claim": "<claim text>", "verdict": "supported|contradicted|unsupported|uncertain", "confidence": <0.0-1.0>, "correction": "<corrected text or null>"}}]}}

If there are no material factual claims to flag, return {{"claims": []}}."""


def _build_prompt(prompt: str, subject: str, subject_answer: str, others: dict[str, str],
                   focus_claims: list[str] | None) -> str:
    others_block = "\n\n".join(f"[{p}]:\n{t}" for p, t in others.items()) or "(none)"
    focus_block = ""
    if focus_claims:
        focus_block = (
            "\nFocus specifically on adjudicating these flagged claims (found in no other answer):\n"
            + "\n".join(f"- {c}" for c in focus_claims) + "\n"
        )
    return _PROMPT_TEMPLATE.format(
        prompt=prompt, subject=subject, subject_answer=subject_answer,
        others_block=others_block, focus_block=focus_block,
    )


async def _call_checker(checker: str, subject: str, fc_prompt: str, client: httpx.AsyncClient,
                         cfg: AppConfig) -> tuple[str, str, ProviderResult]:
    # Always use this provider's default sampling here, regardless of any
    # temperature/top_p override configured for stage 1 — a bad override
    # that broke stage 1 shouldn't also take this provider out as a
    # fact-checker.
    pcfg = strip_sampling_overrides(cfg.providers[checker])
    adapter = get_adapter(pcfg)
    try:
        result = await asyncio.wait_for(adapter.generate(client, fc_prompt), timeout=cfg.stages.stage2_timeout)
    except asyncio.TimeoutError:
        result = ProviderResult(
            provider=checker, model=pcfg.model, status="timeout",
            error=f"exceeded {cfg.stages.stage2_timeout}s stage-2 timeout",
        )
    return checker, subject, result


def _select_checkers_and_subjects(mode: str, cfg: AppConfig, ok_providers: list[str],
                                   answers_by_provider: dict[str, str],
                                   fact_checkers: list[str] | None) -> tuple[list[str], list[str], dict[str, list[str]] | None]:
    if mode == "full_mesh":
        return ok_providers, ok_providers, None
    designated = fact_checkers if fact_checkers is not None else cfg.stages.fact_checkers
    if mode == "diff_then_check":
        checkers = [c for c in designated if c in ok_providers] or ok_providers[:1]
        flagged = claim_diff.find_disagreement_candidates(answers_by_provider)
        return checkers, list(flagged.keys()), flagged
    # designated_fact_checkers
    checkers = [c for c in designated if c in ok_providers]
    return checkers, ok_providers, None


async def run_stage2(run_id: str, prompt: str, cfg: AppConfig, force: bool = False) -> list[dict]:
    run = db.get_run(run_id)
    if run and run.get("skip_stage2"):
        return []

    stage1_rows = db.get_stage1_responses(run_id)
    ok_rows = {r["provider"]: r for r in stage1_rows if r["status"] == "ok" and r["response_text"]}
    if len(ok_rows) < 2:
        return []  # nothing meaningful to cross-check

    mode = (run.get("stage2_mode") if run else None) or cfg.stages.stage2_mode
    answers_by_provider = {p: r["response_text"] for p, r in ok_rows.items()}
    checkers, subjects, flagged_by_subject = _select_checkers_and_subjects(
        mode, cfg, list(ok_rows.keys()), answers_by_provider, run.get("fact_checkers") if run else None
    )
    checkers = [c for c in checkers if c in cfg.providers and cfg.providers[c].enabled]

    if not checkers or not subjects:
        return []

    existing = {} if force else {
        (r["checker_provider"], r["subject_provider"]): r for r in db.get_fact_check_results(run_id)
    }

    jobs = []
    for checker in checkers:
        for subject in subjects:
            if existing.get((checker, subject), {}).get("status") == "ok":
                continue
            others = {p: t for p, t in answers_by_provider.items() if p != subject}
            focus = flagged_by_subject.get(subject) if flagged_by_subject else None
            fc_prompt = _build_prompt(prompt, subject, answers_by_provider[subject], others, focus)
            jobs.append((checker, subject, fc_prompt))

    if jobs:
        async with httpx.AsyncClient(timeout=cfg.stages.stage2_timeout + 5) as client:
            outcomes = await asyncio.gather(
                *(_call_checker(c, s, p, client, cfg) for c, s, p in jobs),
                return_exceptions=True,
            )
        for job, outcome in zip(jobs, outcomes):
            checker, subject, _ = job
            if isinstance(outcome, BaseException):
                db.upsert_fact_check_result(
                    run_id=run_id, checker_provider=checker, subject_provider=subject, status="error",
                    claims=None, raw_response=None, error=f"{type(outcome).__name__}: {outcome}",
                    input_tokens=None, output_tokens=None, cost_usd=None, latency_ms=None,
                )
                continue
            _, _, result = outcome
            claims = None
            error = result.error
            if result.status == "ok" and result.text:
                try:
                    parsed = extract_json(result.text)
                    claims = parsed.get("claims", []) if isinstance(parsed, dict) else parsed
                except (ValueError, json.JSONDecodeError) as exc:
                    error = f"could not parse structured JSON output: {exc}"
                    result.status = "error"
            db.upsert_fact_check_result(
                run_id=run_id, checker_provider=checker, subject_provider=subject, status=result.status,
                claims=claims, raw_response={"text": result.text, "raw": result.raw}, error=error,
                input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                cost_usd=result.cost_usd, latency_ms=result.latency_ms,
            )

    return db.get_fact_check_results(run_id)
