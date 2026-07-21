import json

import httpx
import respx
from httpx import Response

from app.config import ProviderConfig
from app.providers import get_adapter


def _provider_config_with_tools(**overrides):
    defaults = dict(
        key="deepseek", enabled=True, display_name="OpenRouter 1",
        base_url="https://fake-openrouter.test/v1/chat/completions", api_key_env="OPENROUTER_API_KEY",
        model="some/niche-model", request_style="openai_chat", max_tokens=2048,
        pricing={"input_per_million": 0.1, "output_per_million": 0.2},
        extra={"tools": [{"type": "openrouter:web_search"}]},
    )
    defaults.update(overrides)
    return ProviderConfig(**defaults)


async def test_retries_without_tools_and_succeeds(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = _provider_config_with_tools()
    adapter = get_adapter(cfg)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://fake-openrouter.test/v1/chat/completions")
        route.side_effect = [
            Response(500, json={"error": {"message": "Internal Server Error", "code": 500}}),
            Response(200, json={
                "choices": [{"message": {"content": "hi from fallback"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }),
        ]
        async with httpx.AsyncClient() as client:
            result = await adapter.generate(client, "hello")

    assert result.status == "ok"
    assert result.text == "hi from fallback"
    assert len(route.calls) == 2
    first_body = json.loads(route.calls[0].request.content)
    second_body = json.loads(route.calls[1].request.content)
    assert "tools" in first_body
    assert "tools" not in second_body


async def test_retries_without_tools_but_still_fails(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = _provider_config_with_tools()
    adapter = get_adapter(cfg)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://fake-openrouter.test/v1/chat/completions")
        route.side_effect = [
            Response(500, json={"error": {"message": "Internal Server Error", "code": 500}}),
            Response(429, json={"error": {"message": "rate limited", "code": 429}}),
        ]
        async with httpx.AsyncClient() as client:
            result = await adapter.generate(client, "hello")

    assert result.status == "error"
    assert len(route.calls) == 2
    assert "also failed without the web-search tool" in result.error


async def test_no_retry_attempted_when_no_tools_configured(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = _provider_config_with_tools(extra={})  # no tools configured
    adapter = get_adapter(cfg)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://fake-openrouter.test/v1/chat/completions").mock(
            return_value=Response(500, json={"error": {"message": "Internal Server Error", "code": 500}})
        )
        async with httpx.AsyncClient() as client:
            result = await adapter.generate(client, "hello")

    assert result.status == "error"
    assert len(route.calls) == 1  # no wasted retry when there was nothing to strip
    assert "also failed without" not in result.error


async def test_no_retry_when_first_call_succeeds(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    cfg = _provider_config_with_tools()
    adapter = get_adapter(cfg)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://fake-openrouter.test/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })
        )
        async with httpx.AsyncClient() as client:
            result = await adapter.generate(client, "hello")

    assert result.status == "ok"
    assert len(route.calls) == 1
