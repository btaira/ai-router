import asyncio

import httpx
import respx
from httpx import Response

from app import db
from app.config import AppConfig, NOT_CONFIGURED_MODEL, ProviderConfig, StageConfig, load_config
from app.pipeline import stage1_dispatch
from app.providers import get_adapter

LOCAL_YAML = """
providers:
  local1:
    enabled: false
    display_name: "Local LLM 1 (LM Studio)"
    base_url: "http://host.docker.internal:1234/v1/chat/completions"
    api_key_env: "LMSTUDIO_API_KEY"
    model: "not-configured"
    max_tokens: 2048
    request_style: openai_chat
    default_temperature: 1.0
    default_top_p: 1.0
    local: true

pipeline:
  stage1:
    timeout_seconds: 5
  stage2:
    enabled: false
    mode: designated_fact_checkers
    fact_checkers: []
    timeout_seconds: 5
  stage3:
    synthesis_provider: local1
    timeout_seconds: 5
  citations:
    timeout_seconds: 5
    retries: 0
    user_agent: "test-agent/1.0"
"""


def _local_provider_config(**overrides):
    defaults = dict(
        key="local1", enabled=True, display_name="Local LLM 1 (LM Studio)",
        base_url="http://localhost:9999/v1/chat/completions", api_key_env="LMSTUDIO_API_KEY",
        model="my-local-model", request_style="openai_chat", max_tokens=2048,
        pricing={"input_per_million": 0.0, "output_per_million": 0.0}, local=True,
    )
    defaults.update(overrides)
    return ProviderConfig(**defaults)


def test_local_flag_parses_from_yaml(tmp_path):
    p = tmp_path / "providers.yaml"
    p.write_text(LOCAL_YAML)
    cfg = load_config(p)
    provider = cfg.providers["local1"]
    assert provider.local is True
    assert provider.pricing == {"input_per_million": 0.0, "output_per_million": 0.0}


async def test_local_provider_succeeds_without_api_key(monkeypatch):
    monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
    cfg = _local_provider_config()
    adapter = get_adapter(cfg)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("http://localhost:9999/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "hi from local model"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            })
        )
        async with httpx.AsyncClient() as client:
            result = await adapter.generate(client, "hello")

    assert result.status == "ok"
    assert result.text == "hi from local model"
    assert result.cost_usd == 0.0  # $0/M pricing — local inference
    sent_headers = route.calls[0].request.headers
    assert "authorization" not in sent_headers  # no key configured, none sent


async def test_local_provider_sends_key_when_one_is_set(monkeypatch):
    monkeypatch.setenv("LMSTUDIO_API_KEY", "lm-studio")
    cfg = _local_provider_config()
    adapter = get_adapter(cfg)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("http://localhost:9999/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })
        )
        async with httpx.AsyncClient() as client:
            await adapter.generate(client, "hello")

    assert route.calls[0].request.headers["authorization"] == "Bearer lm-studio"


async def test_non_local_provider_still_requires_api_key(monkeypatch):
    monkeypatch.delenv("SOME_MISSING_KEY_FOR_TEST", raising=False)
    cfg = _local_provider_config(
        key="deepseek", display_name="DeepSeek", api_key_env="SOME_MISSING_KEY_FOR_TEST", local=False,
    )
    adapter = get_adapter(cfg)
    async with httpx.AsyncClient() as client:
        result = await adapter.generate(client, "hello")

    assert result.status == "error"
    assert "missing API key" in result.error


async def test_local_provider_reports_actual_model_from_response(monkeypatch):
    # A local server doesn't validate the requested `model` — it'll happily
    # serve whatever it has loaded and echo *that* back in the response,
    # which matters most when the configured model isn't actually what's
    # loaded right now (a stale pick, or the server swapped models under
    # a concurrent request — see the stage1_dispatch serialization tests).
    monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
    cfg = _local_provider_config(model="requested-model-not-actually-loaded")
    adapter = get_adapter(cfg)

    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://localhost:9999/v1/chat/completions").mock(
            return_value=Response(200, json={
                "model": "gemma-4-12b-it",
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })
        )
        async with httpx.AsyncClient() as client:
            result = await adapter.generate(client, "hello")

    assert result.model == "gemma-4-12b-it"


async def test_local_provider_falls_back_to_configured_model_if_response_omits_it(monkeypatch):
    monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
    cfg = _local_provider_config(model="my-local-model")
    adapter = get_adapter(cfg)

    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://localhost:9999/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })
        )
        async with httpx.AsyncClient() as client:
            result = await adapter.generate(client, "hello")

    assert result.model == "my-local-model"


async def test_openrouter_style_provider_also_prefers_response_model(monkeypatch):
    # DeepSeek/MiniMax/Moonshot are routed through OpenRouter using the same
    # request_style: openai_chat as local providers — OpenRouter enforces
    # the requested model in practice, but it's still a router in front of
    # multiple upstream infra providers, so preferring its own response
    # here is the same "verify, don't just trust the config" posture as
    # citation checking elsewhere in this app.
    monkeypatch.setenv("SOME_KEY_FOR_TEST", "key-value")
    cfg = _local_provider_config(
        key="deepseek", display_name="DeepSeek", api_key_env="SOME_KEY_FOR_TEST",
        model="deepseek/deepseek-v4-pro", local=False,
    )
    adapter = get_adapter(cfg)

    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://localhost:9999/v1/chat/completions").mock(
            return_value=Response(200, json={
                "model": "deepseek/deepseek-v4-pro",
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })
        )
        async with httpx.AsyncClient() as client:
            result = await adapter.generate(client, "hello")

    assert result.model == "deepseek/deepseek-v4-pro"


async def test_directly_hosted_vendor_ignores_response_model_field(monkeypatch):
    # A directly-hosted vendor (Anthropic/OpenAI/Google — not proxied
    # through OpenRouter or a local server) always answers with the exact
    # model it was asked for, so there's nothing to "verify" here — the
    # configured string stays authoritative regardless of what a response
    # happens to report.
    monkeypatch.setenv("SOME_KEY_FOR_TEST", "key-value")
    cfg = _local_provider_config(
        key="anthropic", display_name="Anthropic", api_key_env="SOME_KEY_FOR_TEST",
        model="claude-sonnet-5", request_style="anthropic", local=False,
    )
    adapter = get_adapter(cfg)

    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://localhost:9999/v1/chat/completions").mock(
            return_value=Response(200, json={
                "model": "some-other-model-id",
                "content": [{"type": "text", "text": "hi"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            })
        )
        async with httpx.AsyncClient() as client:
            result = await adapter.generate(client, "hello")

    assert result.model == "claude-sonnet-5"


async def test_local_provider_refuses_to_run_unconfigured_model():
    cfg = _local_provider_config(model=NOT_CONFIGURED_MODEL)
    adapter = get_adapter(cfg)

    with respx.mock(assert_all_called=False) as mock:
        async with httpx.AsyncClient() as client:
            result = await adapter.generate(client, "hello")
        assert mock.calls.call_count == 0  # never even tries the network call

    assert result.status == "error"
    assert "no model selected" in result.error


def _stage_config(**overrides):
    defaults = dict(
        stage1_timeout=5, stage2_enabled=False, stage2_mode="designated_fact_checkers",
        fact_checkers=[], stage2_timeout=5, synthesis_provider="local1", stage3_timeout=5,
        citation_timeout=5, citation_retries=0, citation_user_agent="test-agent/1.0",
    )
    defaults.update(overrides)
    return StageConfig(**defaults)


async def test_stage1_serializes_local_providers_sharing_a_base_url(test_db):
    # Two local slots pointed at the same LM Studio instance is the normal
    # setup — if their requests aren't serialized, a slow model-swap on one
    # can overlap with the other's request and both risk answering with
    # whichever model happened to be active at that moment (the actual bug
    # this test guards against).
    shared_url = "http://shared-local-server:9999/v1/chat/completions"
    in_flight = {"count": 0, "max_seen": 0}

    async def slow_responder(request):
        in_flight["count"] += 1
        in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["count"])
        await asyncio.sleep(0.05)
        in_flight["count"] -= 1
        return Response(200, json={
            "model": "whatever-is-loaded",
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    providers = {
        "local1": _local_provider_config(key="local1", base_url=shared_url, enabled=True),
        "local2": _local_provider_config(key="local2", base_url=shared_url, enabled=True),
    }
    cfg = AppConfig(providers=providers, stages=_stage_config(), raw={})
    run_id = db.create_run(prompt="x", skip_stage2=True, stage2_mode="designated_fact_checkers", synthesis_provider="local1")

    with respx.mock(assert_all_called=True) as mock:
        mock.post(shared_url).mock(side_effect=slow_responder)
        await stage1_dispatch.run_stage1(run_id, "hello", cfg)

    assert in_flight["max_seen"] == 1  # never more than one in-flight request to the shared server


async def test_stage1_does_not_serialize_across_different_local_servers(test_db):
    # Two local providers pointed at *different* servers shouldn't wait on
    # each other — only same-base_url calls need to be serialized.
    in_flight = {"count": 0, "max_seen": 0}

    async def slow_responder(request):
        in_flight["count"] += 1
        in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["count"])
        await asyncio.sleep(0.05)
        in_flight["count"] -= 1
        return Response(200, json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })

    providers = {
        "local1": _local_provider_config(key="local1", base_url="http://server-a:9999/v1/chat/completions", enabled=True),
        "local2": _local_provider_config(key="local2", base_url="http://server-b:9999/v1/chat/completions", enabled=True),
    }
    cfg = AppConfig(providers=providers, stages=_stage_config(), raw={})
    run_id = db.create_run(prompt="x", skip_stage2=True, stage2_mode="designated_fact_checkers", synthesis_provider="local1")

    with respx.mock(assert_all_called=True) as mock:
        mock.post("http://server-a:9999/v1/chat/completions").mock(side_effect=slow_responder)
        mock.post("http://server-b:9999/v1/chat/completions").mock(side_effect=slow_responder)
        await stage1_dispatch.run_stage1(run_id, "hello", cfg)

    assert in_flight["max_seen"] == 2  # both ran concurrently
