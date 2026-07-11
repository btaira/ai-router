from __future__ import annotations

from ..config import ProviderConfig
from .anthropic import AnthropicAdapter, MiniMaxAdapter
from .base import BaseAdapter, ProviderError, ProviderResult, compute_cost
from .google_native import GoogleNativeAdapter
from .openai_chat import OpenAIChatAdapter
from .openai_responses import OpenAIResponsesAdapter

_STYLE_TO_ADAPTER: dict[str, type[BaseAdapter]] = {
    "anthropic": AnthropicAdapter,
    "openai_responses": OpenAIResponsesAdapter,
    "openai_chat": OpenAIChatAdapter,
    "google_native": GoogleNativeAdapter,
}

# minimax reuses the Anthropic wire format but with Bearer auth
_PROVIDER_KEY_OVERRIDES: dict[str, type[BaseAdapter]] = {
    "minimax": MiniMaxAdapter,
}


def get_adapter(cfg: ProviderConfig) -> BaseAdapter:
    adapter_cls = _PROVIDER_KEY_OVERRIDES.get(cfg.key) or _STYLE_TO_ADAPTER.get(cfg.request_style)
    if adapter_cls is None:
        raise ProviderError(f"no adapter registered for request_style={cfg.request_style!r}")
    return adapter_cls(cfg)


__all__ = ["get_adapter", "ProviderResult", "ProviderError", "compute_cost"]
