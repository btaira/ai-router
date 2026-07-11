"""Anthropic Messages API adapter.

Also used for MiniMax, which exposes an Anthropic-compatible endpoint
(api.minimax.io/anthropic) — same request/response shape, different base
URL, model name, and auth header, all of which come from providers.yaml.
"""
from __future__ import annotations

from .base import BaseAdapter


class AnthropicAdapter(BaseAdapter):
    def build_request(self, prompt: str) -> tuple[str, dict, dict]:
        cfg = self.cfg
        headers = {
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict = {
            "model": cfg.model,
            "max_tokens": cfg.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        thinking = cfg.extra.get("thinking")
        if thinking:
            body["thinking"] = thinking
        output_config = cfg.extra.get("output_config")
        if output_config:
            body["output_config"] = output_config
        return cfg.base_url, headers, body

    def parse_response(self, data: dict) -> tuple[str, str | None, int | None, int | None]:
        text_parts = []
        thinking_parts = []
        for block in data.get("content", []):
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "thinking":
                thinking_parts.append(block.get("thinking", ""))
        usage = data.get("usage", {})
        in_tok = usage.get("input_tokens")
        out_tok = usage.get("output_tokens")
        return "\n".join(text_parts).strip(), ("\n".join(thinking_parts).strip() or None), in_tok, out_tok


class MiniMaxAdapter(AnthropicAdapter):
    """MiniMax's Anthropic-compatible endpoint; uses Bearer auth instead of x-api-key."""

    def build_request(self, prompt: str) -> tuple[str, dict, dict]:
        url, headers, body = super().build_request(prompt)
        headers.pop("x-api-key", None)
        headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        if self.cfg.extra.get("reasoning_split"):
            body["reasoning_split"] = True
        return url, headers, body
