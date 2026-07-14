"""Google Gemini native `generateContent` adapter."""
from __future__ import annotations

from .base import BaseAdapter


class GoogleNativeAdapter(BaseAdapter):
    def build_request(self, prompt: str, history: list[dict] | None = None) -> tuple[str, dict, dict]:
        cfg = self.cfg
        url = f"{cfg.base_url}/{cfg.model}:generateContent"
        headers = {
            "x-goog-api-key": cfg.api_key,
            "content-type": "application/json",
        }
        # Gemini calls the AI's turn "model", not "assistant" — map the
        # generic history shape onto that.
        contents = [
            {"role": "model" if turn["role"] == "assistant" else "user", "parts": [{"text": turn["content"]}]}
            for turn in (history or [])
        ]
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        body: dict = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": cfg.max_tokens},
        }
        thinking_level = cfg.extra.get("thinking_level")
        if thinking_level:
            body["generationConfig"]["thinkingConfig"] = {
                "thinkingLevel": thinking_level,
                "includeThoughts": True,
            }
        tools = cfg.extra.get("tools")
        if tools:
            body["tools"] = tools
        if "temperature" in cfg.extra:
            body["generationConfig"]["temperature"] = cfg.extra["temperature"]
        if "top_p" in cfg.extra:
            body["generationConfig"]["topP"] = cfg.extra["top_p"]
        return url, headers, body

    def parse_response(self, data: dict) -> tuple[str, str | None, int | None, int | None]:
        text_parts = []
        thinking_parts = []
        candidates = data.get("candidates", [])
        if candidates:
            for part in candidates[0].get("content", {}).get("parts", []):
                if part.get("thought"):
                    thinking_parts.append(part.get("text", ""))
                elif "text" in part:
                    text_parts.append(part["text"])
        usage = data.get("usageMetadata", {})
        in_tok = usage.get("promptTokenCount")
        out_tok = usage.get("candidatesTokenCount")
        return "\n".join(text_parts).strip(), ("\n".join(thinking_parts).strip() or None), in_tok, out_tok
