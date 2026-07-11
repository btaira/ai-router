"""YAML-driven configuration loading for providers and pipeline settings."""
from __future__ import annotations

import os
import re
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


class ModelUpdateError(Exception):
    pass


def update_provider_model(provider_key: str, new_model: str, path: Path | None = None) -> None:
    """Rewrite just the `model:` line for one provider in providers.yaml in place.

    Does a targeted text substitution rather than a full yaml.safe_load +
    yaml.dump round-trip so the file's comments and formatting survive —
    those comments carry real warnings (model names/params moving fast)
    that a naive re-dump would silently drop.
    """
    cfg_path = path or CONFIG_PATH
    # newline="" disables universal-newline translation on both ends — without
    # it, Path.write_text() on Windows silently rewrites every LF in the file
    # to CRLF (not just the line we're touching), turning a one-line edit
    # into a whole-file line-ending change and a noisy git diff.
    with open(cfg_path, newline="") as f:
        text = f.read()
    lines = text.splitlines(keepends=True)

    key_pattern = re.compile(rf"^  {re.escape(provider_key)}:\s*$")
    start = next((i for i, line in enumerate(lines) if key_pattern.match(line)), None)
    if start is None:
        raise ModelUpdateError(f"provider {provider_key!r} not found in {cfg_path}")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i]
        if stripped.strip() and not stripped.startswith((" ", "\t")):
            end = i
            break
        if re.match(r"^  \S", stripped):  # next sibling key (provider or top-level section)
            end = i
            break

    model_pattern = re.compile(r'^(\s*model:\s*)(".*?"|\S+)([ \t]*)$')
    for i in range(start + 1, end):
        line = lines[i]
        ending = ""
        body = line
        if body.endswith("\r\n"):
            ending, body = "\r\n", body[:-2]
        elif body.endswith("\n"):
            ending, body = "\n", body[:-1]
        m = model_pattern.match(body)
        if m:
            escaped = new_model.replace('"', '\\"')
            lines[i] = f'{m.group(1)}"{escaped}"{m.group(3)}{ending}'
            break
    else:
        raise ModelUpdateError(f"no `model:` line found for provider {provider_key!r} in {cfg_path}")

    cfg_path.write_text("".join(lines), newline="")
