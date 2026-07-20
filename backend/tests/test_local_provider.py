import httpx
import respx
from httpx import Response

from app.config import ProviderConfig, load_config
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
    # which matters most when the configured model is a stale/unset
    # placeholder like "not-configured".
    monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
    cfg = _local_provider_config(model="not-configured")
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
