"""OpenAI Responses API adapter (flagship reasoning models)."""
from __future__ import annotations

from .base import BaseAdapter


class OpenAIResponsesAdapter(BaseAdapter):
    def build_request(self, prompt: str, history: list[dict] | None = None) -> tuple[str, dict, dict]:
        cfg = self.cfg
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "content-type": "application/json",
        }
        # `input` accepts either a plain string (single user turn) or a list
        # of role/content items — use the list form whenever there's history
        # so multi-turn follow-ups actually carry prior context.
        input_value = [*(history or []), {"role": "user", "content": prompt}] if history else prompt
        body: dict = {
            "model": cfg.model,
            "input": input_value,
            "max_output_tokens": cfg.max_tokens,
        }
        reasoning = cfg.extra.get("reasoning")
        if reasoning:
            body["reasoning"] = reasoning
        tools = cfg.extra.get("tools")
        if tools:
            body["tools"] = tools
        # Note: some reasoning-effort configurations reject a custom
        # temperature/top_p — if so, that's a normal per-provider API error,
        # not a crash.
        if "temperature" in cfg.extra:
            body["temperature"] = cfg.extra["temperature"]
        if "top_p" in cfg.extra:
            body["top_p"] = cfg.extra["top_p"]
        return cfg.base_url, headers, body

    def parse_response(self, data: dict) -> tuple[str, str | None, int | None, int | None]:
        text_parts = []
        thinking_parts = []
        for item in data.get("output", []):
            itype = item.get("type")
            if itype == "message":
                for c in item.get("content", []):
                    if c.get("type") in ("output_text", "text"):
                        text_parts.append(c.get("text", ""))
            elif itype == "reasoning":
                for s in item.get("summary", []) or []:
                    if isinstance(s, dict):
                        thinking_parts.append(s.get("text", ""))
                    elif isinstance(s, str):
                        thinking_parts.append(s)
        if not text_parts and data.get("output_text"):
            text_parts.append(data["output_text"])
        usage = data.get("usage", {})
        in_tok = usage.get("input_tokens")
        out_tok = usage.get("output_tokens")
        return "\n".join(text_parts).strip(), ("\n".join(thinking_parts).strip() or None), in_tok, out_tok
