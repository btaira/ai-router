"""Stage 1 — parallel fan-out to all enabled providers.

Each provider call is isolated: a per-provider timeout wraps the request so
one slow/hung reasoning model can't block the others, and any exception is
caught and recorded as an `error` row rather than raising out of the batch.
Resumable: providers that already have an `ok` row for this run_id are
skipped so iterating on later stages doesn't re-spend stage 1 money.

Each provider is marked `running` in SQLite the instant its call is
dispatched, and its final row is written the instant *that* call finishes —
not after the whole batch completes — so a client polling `GET /runs/{id}`
mid-flight sees providers flip from running to ok/error/timeout one at a
time instead of all at once at the end.
"""
from __future__ import annotations

import asyncio

import httpx

from .. import db
from ..config import AppConfig
from ..providers import ProviderResult, get_adapter


async def _dispatch_one(provider_key: str, client: httpx.AsyncClient, cfg: AppConfig, prompt: str, run_id: str) -> None:
    pcfg = cfg.providers[provider_key]
    adapter = get_adapter(pcfg)
    try:
        result = await asyncio.wait_for(adapter.generate(client, prompt), timeout=cfg.stages.stage1_timeout)
    except asyncio.TimeoutError:
        result = ProviderResult(
            provider=provider_key, model=pcfg.model, status="timeout",
            error=f"exceeded {cfg.stages.stage1_timeout}s stage-1 timeout",
        )
    except Exception as exc:  # noqa: BLE001 - never let one provider's bug take down the batch
        result = ProviderResult(provider=provider_key, model=pcfg.model, status="error", error=f"{type(exc).__name__}: {exc}")

    db.upsert_stage1_response(
        run_id=run_id, provider=result.provider, model=result.model, status=result.status,
        request=result.request_body, response_text=result.text, thinking_text=result.thinking_text,
        raw_response=result.raw, error=result.error, input_tokens=result.input_tokens,
        output_tokens=result.output_tokens, cost_usd=result.cost_usd, latency_ms=result.latency_ms,
    )


async def run_stage1(run_id: str, prompt: str, cfg: AppConfig, force: bool = False) -> list[dict]:
    existing = {r["provider"]: r for r in db.get_stage1_responses(run_id)} if not force else {}
    enabled_providers = [key for key, pcfg in cfg.providers.items() if pcfg.enabled]

    to_call = [p for p in enabled_providers if existing.get(p, {}).get("status") != "ok"]

    if to_call:
        for provider_key in to_call:
            pcfg = cfg.providers[provider_key]
            db.upsert_stage1_response(
                run_id=run_id, provider=provider_key, model=pcfg.model, status="running",
                request={}, response_text=None, thinking_text=None, raw_response=None,
                error=None, input_tokens=None, output_tokens=None, cost_usd=None, latency_ms=None,
            )

        async with httpx.AsyncClient(timeout=cfg.stages.stage1_timeout + 5) as client:
            await asyncio.gather(
                *(_dispatch_one(p, client, cfg, prompt, run_id) for p in to_call),
                return_exceptions=True,
            )

    return db.get_stage1_responses(run_id)
