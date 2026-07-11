"""Best-effort JSON extraction from LLM text output.

Models asked for "JSON only" still sometimes wrap it in prose or code
fences. This pulls out the first balanced {...} or [...] block and parses
it, rather than requiring an exact-match response.
"""
from __future__ import annotations

import json
import re

_CODE_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_json(text: str) -> dict | list:
    text = text.strip()

    fence_match = _CODE_FENCE.search(text)
    if fence_match:
        candidate = fence_match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start_chars = {"{": "}", "[": "]"}
    for i, ch in enumerate(text):
        if ch in start_chars:
            close = start_chars[ch]
            depth = 0
            for j in range(i, len(text)):
                if text[j] == ch:
                    depth += 1
                elif text[j] == close:
                    depth -= 1
                    if depth == 0:
                        candidate = text[i:j + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
            break

    raise ValueError("no valid JSON object/array found in text")
