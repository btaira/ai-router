"""Unified provider interface.

Every adapter implements `generate(client, prompt, history) -> ProviderResult`
so the stage 1 dispatcher, stage 2 fact-checkers, stage 3 synthesis step, and
the post-synthesis follow-up chat can all call any provider identically.
Adapters differ only in how they build the HTTP request and parse the
response for their provider's API shape (`request_style` in providers.yaml
selects which adapter class handles a given provider).

`history` is an optional list of prior turns as
`[{"role": "user"|"assistant", "content": str}, ...]`, used only by the
follow-up chat (stage 1/2/3 never pass it — a fresh conversation each time).
Each adapter maps this generic shape onto its own native multi-turn format.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import ProviderConfig


class ProviderError(Exception):
    """Raised for any non-timeout provider failure (HTTP error, bad payload, etc.)."""


@dataclass
class ProviderResult:
    provider: str
    model: str
    status: str  # ok | error | timeout
    text: str | None = None
    thinking_text: str | None = None
    raw: Any = None
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None
    request_body: dict = field(default_factory=dict)


def compute_cost(cfg: ProviderConfig, input_tokens: int | None, output_tokens: int | None) -> float | None:
    if input_tokens is None or output_tokens is None:
        return None
    rate_in = cfg.pricing.get("input_per_million", 0.0)
    rate_out = cfg.pricing.get("output_per_million", 0.0)
    return (input_tokens / 1_000_000) * rate_in + (output_tokens / 1_000_000) * rate_out


class BaseAdapter:
    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    def build_request(self, prompt: str, history: list[dict] | None = None) -> tuple[str, dict, dict]:
        """Return (url, headers, json_body)."""
        raise NotImplementedError

    def parse_response(self, data: dict) -> tuple[str, str | None, int | None, int | None]:
        """Return (text, thinking_text, input_tokens, output_tokens)."""
        raise NotImplementedError

    async def generate(self, client: httpx.AsyncClient, prompt: str, history: list[dict] | None = None) -> ProviderResult:
        # Local inference servers (LM Studio, etc.) don't check the key at
        # all — nothing to require here for a provider marked `local`.
        if not self.cfg.local and not self.cfg.api_key:
            return ProviderResult(
                provider=self.cfg.key, model=self.cfg.model, status="error",
                error=f"missing API key: set ${self.cfg.api_key_env}",
            )

        url, headers, body = self.build_request(prompt, history)
        start = time.monotonic()
        try:
            resp = await client.post(url, headers=headers, json=body)
            latency_ms = (time.monotonic() - start) * 1000
            if resp.status_code >= 400:
                return ProviderResult(
                    provider=self.cfg.key, model=self.cfg.model, status="error",
                    error=f"HTTP {resp.status_code}: {resp.text[:500]}",
                    raw={"status_code": resp.status_code, "body": resp.text[:2000]},
                    latency_ms=latency_ms, request_body=body,
                )
            data = resp.json()
            text, thinking_text, in_tok, out_tok = self.parse_response(data)
            cost = compute_cost(self.cfg, in_tok, out_tok)
            return ProviderResult(
                provider=self.cfg.key, model=self.cfg.model, status="ok",
                text=text, thinking_text=thinking_text, raw=data,
                input_tokens=in_tok, output_tokens=out_tok, cost_usd=cost,
                latency_ms=latency_ms, request_body=body,
            )
        except httpx.TimeoutException:
            latency_ms = (time.monotonic() - start) * 1000
            return ProviderResult(
                provider=self.cfg.key, model=self.cfg.model, status="timeout",
                error="request timed out", latency_ms=latency_ms, request_body=body,
            )
        except Exception as exc:  # noqa: BLE001 - isolate provider failures from the rest of the run
            latency_ms = (time.monotonic() - start) * 1000
            return ProviderResult(
                provider=self.cfg.key, model=self.cfg.model, status="error",
                error=f"{type(exc).__name__}: {exc}", latency_ms=latency_ms, request_body=body,
            )
