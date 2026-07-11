"""YAML-driven configuration loading for providers and pipeline settings."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(os.environ.get("AI_ROUTER_CONFIG", Path(__file__).resolve().parent.parent / "config" / "providers.yaml"))


@dataclass
class ProviderConfig:
    key: str
    enabled: bool
    display_name: str
    base_url: str
    api_key_env: str
    model: str
    request_style: str
    max_tokens: int
    pricing: dict[str, float]
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env)


@dataclass
class StageConfig:
    stage1_timeout: int
    stage2_enabled: bool
    stage2_mode: str
    fact_checkers: list[str]
    stage2_timeout: int
    synthesis_provider: str
    stage3_timeout: int
    citation_timeout: int
    citation_retries: int
    citation_user_agent: str


@dataclass
class AppConfig:
    providers: dict[str, ProviderConfig]
    stages: StageConfig
    raw: dict[str, Any]


_KNOWN_TOP_LEVEL_KEYS = {
    "enabled", "display_name", "base_url", "api_key_env", "model",
    "request_style", "max_tokens", "max_output_tokens", "pricing",
}


def _build_provider_config(key: str, raw: dict[str, Any]) -> ProviderConfig:
    extra = {k: v for k, v in raw.items() if k not in _KNOWN_TOP_LEVEL_KEYS}
    return ProviderConfig(
        key=key,
        enabled=raw.get("enabled", True),
        display_name=raw.get("display_name", key),
        base_url=raw["base_url"],
        api_key_env=raw["api_key_env"],
        model=raw["model"],
        request_style=raw["request_style"],
        max_tokens=raw.get("max_tokens") or raw.get("max_output_tokens") or 4096,
        pricing=raw.get("pricing", {"input_per_million": 0.0, "output_per_million": 0.0}),
        extra=extra,
    )


def load_config(path: Path | None = None) -> AppConfig:
    cfg_path = path or CONFIG_PATH
    with open(cfg_path) as f:
        raw = yaml.safe_load(f)

    providers = {
        key: _build_provider_config(key, pcfg)
        for key, pcfg in raw.get("providers", {}).items()
    }

    pipeline_raw = raw.get("pipeline", {})
    stage1 = pipeline_raw.get("stage1", {})
    stage2 = pipeline_raw.get("stage2", {})
    stage3 = pipeline_raw.get("stage3", {})
    citations = pipeline_raw.get("citations", {})

    stages = StageConfig(
        stage1_timeout=stage1.get("timeout_seconds", 180),
        stage2_enabled=stage2.get("enabled", True),
        stage2_mode=stage2.get("mode", "designated_fact_checkers"),
        fact_checkers=stage2.get("fact_checkers", []),
        stage2_timeout=stage2.get("timeout_seconds", 180),
        synthesis_provider=stage3.get("synthesis_provider", "anthropic"),
        stage3_timeout=stage3.get("timeout_seconds", 180),
        citation_timeout=citations.get("timeout_seconds", 10),
        citation_retries=citations.get("retries", 1),
        citation_user_agent=citations.get("user_agent", "ai-router-citation-checker/1.0"),
    )

    return AppConfig(providers=providers, stages=stages, raw=raw)


_config: AppConfig | None = None


def get_config(refresh: bool = False) -> AppConfig:
    global _config
    if _config is None or refresh:
        _config = load_config()
    return _config
