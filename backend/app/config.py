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
    available_models: list[dict[str, Any]] = field(default_factory=list)
    # Informational only — what the vendor's API itself defaults to when we
    # don't send an explicit temperature/top_p. Never sent in a request; the
    # UI just displays these next to the (optional) override inputs.
    default_temperature: float | None = None
    default_top_p: float | None = None
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
    "request_style", "max_tokens", "max_output_tokens", "pricing", "models",
    "default_temperature", "default_top_p",
}

_ZERO_PRICING = {"input_per_million": 0.0, "output_per_million": 0.0}


def _build_provider_config(key: str, raw: dict[str, Any]) -> ProviderConfig:
    extra = {k: v for k, v in raw.items() if k not in _KNOWN_TOP_LEVEL_KEYS}
    model = raw["model"]
    available_models = raw.get("models", [])

    # Pricing is looked up from the `models:` catalog entry matching the
    # currently selected model, so switching models (even via a plain text
    # edit of `model:`) automatically gets the right rate with no separate
    # pricing field to keep in sync. `pricing:` is only a fallback for a
    # model that isn't in the catalog (e.g. a custom string set directly).
    pricing = next((m["pricing"] for m in available_models if m.get("id") == model), None)
    if pricing is None:
        pricing = raw.get("pricing", _ZERO_PRICING)

    return ProviderConfig(
        key=key,
        enabled=raw.get("enabled", True),
        display_name=raw.get("display_name", key),
        base_url=raw["base_url"],
        api_key_env=raw["api_key_env"],
        model=model,
        request_style=raw["request_style"],
        max_tokens=raw.get("max_tokens") or raw.get("max_output_tokens") or 4096,
        pricing=pricing,
        available_models=available_models,
        default_temperature=raw.get("default_temperature"),
        default_top_p=raw.get("default_top_p"),
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


def _find_provider_block(lines: list[str], provider_key: str, cfg_path: Path) -> tuple[int, int]:
    key_pattern = re.compile(rf"^  {re.escape(provider_key)}:\s*$")
    start = next((i for i, line in enumerate(lines) if key_pattern.match(line)), None)
    if start is None:
        raise ModelUpdateError(f"provider {provider_key!r} not found in {cfg_path}")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^  \S", lines[i]):  # next sibling key (provider or top-level section)
            end = i
            break
    return start, end


def _split_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _render_scalar(value: str | bool | int | float) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace('"', '\\"')
    return f'"{escaped}"'


def set_provider_field(provider_key: str, field_name: str, value: str | bool | int | float | None,
                        path: Path | None = None) -> None:
    """Insert, replace, or delete one top-level scalar field within a
    provider's block in providers.yaml, leaving every other line untouched.

    Does a targeted text substitution rather than a full yaml.safe_load +
    yaml.dump round-trip so the file's comments and formatting survive.
    value=None deletes the field entirely (falls back to whatever default
    the code uses when the key is absent) — used to clear temperature/top_p
    back to "use the provider's default".
    """
    cfg_path = path or CONFIG_PATH
    # newline="" disables universal-newline translation on both ends — without
    # it, writing on Windows silently rewrites every LF in the file to CRLF
    # (not just the line we're touching), turning a one-line edit into a
    # whole-file line-ending change and a noisy git diff.
    with open(cfg_path, newline="") as f:
        text = f.read()
    lines = text.splitlines(keepends=True)

    start, end = _find_provider_block(lines, provider_key, cfg_path)

    field_pattern = re.compile(rf'^(\s*){re.escape(field_name)}:\s*(.*)$')
    target = None
    indent = "    "
    for i in range(start + 1, end):
        body, _ending = _split_ending(lines[i])
        m = field_pattern.match(body)
        if m:
            target = i
            indent = m.group(1)
            break

    if value is None:
        if target is not None:
            del lines[target]
            with open(cfg_path, "w", newline="") as f:
                f.write("".join(lines))
        return

    rendered = _render_scalar(value)
    if target is not None:
        _, ending = _split_ending(lines[target])
        lines[target] = f"{indent}{field_name}: {rendered}{ending}"
    else:
        neighbor = lines[start + 1] if start + 1 < len(lines) else lines[start]
        _, ending = _split_ending(neighbor)
        lines.insert(start + 1, f"{indent}{field_name}: {rendered}{ending or chr(10)}")

    with open(cfg_path, "w", newline="") as f:
        f.write("".join(lines))


def update_provider_model(provider_key: str, new_model: str, path: Path | None = None) -> None:
    set_provider_field(provider_key, "model", new_model, path=path)


def update_provider_enabled(provider_key: str, enabled: bool, path: Path | None = None) -> None:
    set_provider_field(provider_key, "enabled", enabled, path=path)


def update_provider_params(provider_key: str, temperature: float | None, top_p: float | None,
                            path: Path | None = None) -> None:
    set_provider_field(provider_key, "temperature", temperature, path=path)
    set_provider_field(provider_key, "top_p", top_p, path=path)
