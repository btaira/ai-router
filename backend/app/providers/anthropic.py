"""Anthropic Messages API adapter."""
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
        tools = cfg.extra.get("tools")
        if tools:
            body["tools"] = tools
        # Note: Anthropic rejects a custom temperature/top_p while extended
        # thinking is enabled (must be left at default) — if set, that
        # surfaces as a normal per-provider API error, not a crash.
        if "temperature" in cfg.extra:
            body["temperature"] = cfg.extra["temperature"]
        if "top_p" in cfg.extra:
            body["top_p"] = cfg.extra["top_p"]
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
