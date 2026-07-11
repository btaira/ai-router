"""OpenAI-compatible Chat Completions adapter.

Used for providers that expose an OpenAI-style `/chat/completions` endpoint,
including OpenRouter (which normalizes reasoning effort across vendors via
`reasoning` and web search via the `openrouter:web_search` tool) as well as
vendor-specific extra fields (DeepSeek's `reasoning_effort`/`thinking`,
Moonshot/Kimi's `preserve_thinking`, etc). All of these are passed through
verbatim from `providers.yaml`'s `extra` config so no code change is needed
when a provider adds/renames one.
"""
from __future__ import annotations

from .base import BaseAdapter

_PASSTHROUGH_EXTRA_KEYS = ("reasoning_effort", "thinking", "preserve_thinking", "reasoning", "tools")


class OpenAIChatAdapter(BaseAdapter):
    def build_request(self, prompt: str) -> tuple[str, dict, dict]:
        cfg = self.cfg
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "content-type": "application/json",
        }
        body: dict = {
            "model": cfg.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": cfg.max_tokens,
        }
        for key in _PASSTHROUGH_EXTRA_KEYS:
            if key in cfg.extra:
                body[key] = cfg.extra[key]
        return cfg.base_url, headers, body

    def parse_response(self, data: dict) -> tuple[str, str | None, int | None, int | None]:
        choices = data.get("choices", [])
        text = ""
        thinking = None
        if choices:
            message = choices[0].get("message", {})
            text = message.get("content", "") or ""
            thinking = message.get("reasoning_content") or message.get("reasoning") or None
        usage = data.get("usage", {})
        in_tok = usage.get("prompt_tokens")
        out_tok = usage.get("completion_tokens")
        return text.strip(), thinking, in_tok, out_tok
