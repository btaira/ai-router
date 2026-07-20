"""YAML-driven configuration loading for providers and pipeline settings."""
from __future__ import annotations

import dataclasses
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# FastAPI runs these sync route handlers in a real OS threadpool, so two
# settings changes submitted close together (e.g. saving several providers'
# Model settings at once) can genuinely execute concurrently — without a
# lock, both threads read the file before either writes, and whichever
# writes second silently clobbers the first thread's change. One lock per
# file serializes the read-modify-write around each.
_providers_yaml_lock = threading.Lock()
_env_file_lock = threading.Lock()

CONFIG_PATH = Path(os.environ.get("AI_ROUTER_CONFIG", Path(__file__).resolve().parent.parent / "config" / "providers.yaml"))
# The actual .env file (repo root) — BYOK keys pasted into the Settings UI
# are written straight into this file, the same one `.env.example` gets
# copied to and the same one main.py loads at startup. docker-compose.yml
# bind-mounts it into the container so a write from inside a running
# container lands on the host and survives an image rebuild.
ENV_PATH = Path(os.environ.get("AI_ROUTER_ENV", Path(__file__).resolve().parent.parent.parent / ".env"))
# Sentinel `model:` value for a local provider (local1/local2 in
# providers.yaml) that hasn't been pointed at a real model yet — checked in
# providers/base.py, which refuses to run a local provider still set to
# this rather than silently sending it as a literal (and meaningless)
# model string to the local server.
NOT_CONFIGURED_MODEL = "not-configured"


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
    # True for providers whose reasoning/thinking mode is always-on and
    # rejects a custom temperature/top_p outright (e.g. Anthropic requires
    # temperature=1 while extended thinking is enabled). When locked, the UI
    # disables the override inputs and the API rejects a PUT that tries to
    # set one, instead of letting an invalid value reach the provider and
    # fail stage 1 with a confusing error.
    sampling_locked: bool = False
    # True for a provider that talks to a local inference server (e.g. LM
    # Studio) rather than a hosted vendor API. Local providers don't require
    # an API key — the running server doesn't check it — and their model
    # list comes from a live query to that server instead of a curated
    # `models:` catalog (see GET /api/config/providers/{key}/local-models).
    local: bool = False
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
    "default_temperature", "default_top_p", "sampling_locked", "local",
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
        sampling_locked=raw.get("sampling_locked", False),
        local=raw.get("local", False),
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


def strip_sampling_overrides(pcfg: ProviderConfig) -> ProviderConfig:
    """Return a copy of pcfg with any temperature/top_p override removed.

    Stage 1 is where a user's experimental sampling override is meant to
    apply. Stage 2 (fact-checking) and stage 3 (synthesis) reuse the same
    provider config, so without this a bad override that broke a provider's
    stage-1 call would break that provider again as a fact-checker or
    synthesizer — always using each provider's own default there means those
    stages stay reliable regardless of what stage 1 is experimenting with.
    """
    if "temperature" not in pcfg.extra and "top_p" not in pcfg.extra:
        return pcfg
    extra = {k: v for k, v in pcfg.extra.items() if k not in ("temperature", "top_p")}
    return dataclasses.replace(pcfg, extra=extra)


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
    with _providers_yaml_lock:
        # newline="" disables universal-newline translation on both ends —
        # without it, writing on Windows silently rewrites every LF in the
        # file to CRLF (not just the line we're touching), turning a
        # one-line edit into a whole-file line-ending change and a noisy
        # git diff.
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


_ENV_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")


def _env_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _set_env_var(path: Path, key: str, value: str) -> None:
    """Set one KEY="value" line in an env file, in place.

    Deliberately not python-dotenv's set_key (temp file + os.replace): that
    fails with "Device or resource busy" when `path` itself — not just its
    parent directory — is a Docker bind mount (docker-compose.yml mounts
    .env directly so BYOK writes reach the host), since you can't atomically
    swap the inode at an active bind-mount point. A plain read/modify/write
    to the same inode has no such restriction — same approach already used
    for providers.yaml in set_provider_field, for the same reason.
    """
    with _env_file_lock:
        lines = path.read_text().splitlines(keepends=True) if path.exists() else []
        new_line = f"{key}={_env_quote(value)}\n"
        for i, line in enumerate(lines):
            m = _ENV_LINE_RE.match(line)
            if m and m.group(1) == key:
                lines[i] = new_line
                break
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(new_line)
        path.write_text("".join(lines))


def _unset_env_var(path: Path, key: str) -> None:
    with _env_file_lock:
        if not path.exists():
            return
        lines = path.read_text().splitlines(keepends=True)
        kept = [line for line in lines if not (_ENV_LINE_RE.match(line) and _ENV_LINE_RE.match(line).group(1) == key)]
        path.write_text("".join(kept))


def set_provider_api_key(provider_key: str, api_key: str | None, path: Path | None = None,
                          env_path: Path | None = None) -> None:
    """Set (or clear) a provider's API key directly in the .env file
    (`env_path`, default ENV_PATH) so it survives a restart, and apply it to
    the current process immediately so the change takes effect without one —
    `ProviderConfig.api_key` always reads live from `os.environ`.

    Clearing (api_key=None) removes the line from .env and the current
    process entirely — there's no separate fallback layer once .env is the
    single source of truth, so this really does clear it, not revert it to
    some other default.
    """
    cfg = load_config(path or CONFIG_PATH)
    if provider_key not in cfg.providers:
        raise ValueError(f"unknown provider: {provider_key}")
    env_var = cfg.providers[provider_key].api_key_env
    dest = env_path or ENV_PATH

    if api_key:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.touch()
        _set_env_var(dest, env_var, api_key)
        os.environ[env_var] = api_key
    else:
        _unset_env_var(dest, env_var)
        os.environ.pop(env_var, None)
