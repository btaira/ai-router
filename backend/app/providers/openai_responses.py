"""OpenAI Responses API adapter (flagship reasoning models)."""
from __future__ import annotations

from .base import BaseAdapter


class OpenAIResponsesAdapter(BaseAdapter):
    def build_request(self, prompt: str) -> tuple[str, dict, dict]:
        cfg = self.cfg
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "content-type": "application/json",
        }
        body: dict = {
            "model": cfg.model,
            "input": prompt,
            "max_output_tokens": cfg.max_tokens,
        }
        reasoning = cfg.extra.get("reasoning")
        if reasoning:
            body["reasoning"] = reasoning
        tools = cfg.extra.get("tools")
        if tools:
            body["tools"] = tools
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
