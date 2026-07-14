"""Post-synthesis "go deeper" follow-up chat.

After stage 3 produces a synthesized answer, the user can keep talking to
just that synthesis model to refine or expand on it — not all six stage-1
models. Each turn gets the same citation-verification treatment as the
initial synthesis (a model's claimed URL is never trusted without a live
HTTP check), and always uses the provider's default sampling, consistent
with stage 2/3 never inheriting a stage-1 temperature/top_p experiment.
"""
from __future__ import annotations

import asyncio

import httpx

from .. import db
from ..config import AppConfig, strip_sampling_overrides
from ..providers import get_adapter
from . import citations


async def run_followup(run_id: str, message: str, cfg: AppConfig) -> dict:
    run = db.get_run(run_id)
    if not run:
        raise ValueError(f"unknown run: {run_id}")

    synthesis = db.get_synthesis_result(run_id)
    if not synthesis or synthesis["status"] != "ok" or not synthesis.get("synthesis_text"):
        raise ValueError("run has no synthesized answer yet")

    provider_key = run.get("synthesis_provider") or cfg.stages.synthesis_provider
    if provider_key not in cfg.providers:
        raise ValueError(f"unknown synthesis_provider: {provider_key}")

    turn_index = db.next_followup_turn_index(run_id)
    db.add_followup_message(run_id, turn_index, "user", message)

    # Concise history: the original question and the synthesized answer
    # (not the full six-way stage-1 dump — that would make every follow-up
    # turn as expensive as the original synthesis), plus any prior
    # follow-up turns so the conversation stays coherent.
    history = [
        {"role": "user", "content": run["prompt"]},
        {"role": "assistant", "content": synthesis["synthesis_text"]},
    ]
    for m in db.get_followup_messages(run_id):
        if m["turn_index"] == turn_index and m["role"] == "user":
            continue  # this is the message we're about to send, not history
        if m["status"] != "ok":
            continue
        history.append({"role": m["role"], "content": m["content"]})

    pcfg = strip_sampling_overrides(cfg.providers[provider_key])
    adapter = get_adapter(pcfg)
    async with httpx.AsyncClient(timeout=cfg.stages.stage3_timeout + 5) as client:
        try:
            result = await asyncio.wait_for(
                adapter.generate(client, message, history=history), timeout=cfg.stages.stage3_timeout
            )
        except asyncio.TimeoutError:
            result = None

    if result is None:
        db.add_followup_message(
            run_id, turn_index, "assistant", "", status="timeout",
            error=f"exceeded {cfg.stages.stage3_timeout}s follow-up timeout",
        )
    elif result.status != "ok" or not result.text:
        db.add_followup_message(
            run_id, turn_index, "assistant", result.text or "", status=result.status,
            error=result.error, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            cost_usd=result.cost_usd, latency_ms=result.latency_ms,
        )
    else:
        db.add_followup_message(
            run_id, turn_index, "assistant", result.text, status="ok",
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            cost_usd=result.cost_usd, latency_ms=result.latency_ms,
        )
        await citations.verify_citations(run_id, cfg, text=result.text)

    return {
        "turn_index": turn_index,
        "messages": db.get_followup_messages(run_id),
        "citation_verifications": db.get_citation_verifications(run_id),
    }
