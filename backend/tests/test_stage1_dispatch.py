import respx
from httpx import Response

from app import db
from app.pipeline import stage1_dispatch


def _anthropic_body(text):
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


def _openai_responses_body(text):
    return {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 8, "output_tokens": 16},
    }


async def test_all_providers_succeed(test_db, test_config):
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json=_anthropic_body("anthropic answer"))
        )
        mock.post("https://fake-openai.test/v1/responses").mock(
            return_value=Response(200, json=_openai_responses_body("openai answer"))
        )
        mock.post("https://fake-deepseek.test/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "deepseek answer"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            })
        )
        results = await stage1_dispatch.run_stage1("run1", "what is up", test_config)

    by_provider = {r["provider"]: r for r in results}
    assert by_provider["anthropic"]["status"] == "ok"
    assert by_provider["anthropic"]["response_text"] == "anthropic answer"
    assert by_provider["openai"]["response_text"] == "openai answer"
    assert by_provider["deepseek"]["response_text"] == "deepseek answer"
    assert by_provider["anthropic"]["cost_usd"] > 0


async def test_one_provider_failure_does_not_block_others(test_db, test_config):
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json=_anthropic_body("anthropic answer"))
        )
        mock.post("https://fake-openai.test/v1/responses").mock(return_value=Response(500, text="boom"))
        mock.post("https://fake-deepseek.test/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "deepseek answer"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            })
        )
        results = await stage1_dispatch.run_stage1("run1", "prompt", test_config)

    by_provider = {r["provider"]: r for r in results}
    assert by_provider["anthropic"]["status"] == "ok"
    assert by_provider["deepseek"]["status"] == "ok"
    assert by_provider["openai"]["status"] == "error"
    assert "500" in by_provider["openai"]["error"]


async def test_resumable_skips_already_ok_providers(test_db, test_config):
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json=_anthropic_body("first call"))
        )
        mock.post("https://fake-openai.test/v1/responses").mock(
            return_value=Response(200, json=_openai_responses_body("openai answer"))
        )
        mock.post("https://fake-deepseek.test/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "deepseek answer"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            })
        )
        await stage1_dispatch.run_stage1("run1", "prompt", test_config)

    # second call: anthropic route intentionally NOT mocked — if the dispatcher tried
    # to call it again, respx would raise for the unmocked route and the test would fail.
    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://fake-openai.test/v1/responses").mock(
            return_value=Response(200, json=_openai_responses_body("openai answer 2"))
        )
        mock.post("https://fake-deepseek.test/v1/chat/completions").mock(
            return_value=Response(200, json={
                "choices": [{"message": {"content": "deepseek answer 2"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            })
        )
        results = await stage1_dispatch.run_stage1("run1", "prompt", test_config)

    by_provider = {r["provider"]: r for r in results}
    assert by_provider["anthropic"]["response_text"] == "first call"  # untouched, not re-called


async def test_missing_api_key_reports_error_without_network_call(test_db, test_config, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json=_anthropic_body("ok"))
        )
        mock.post("https://fake-openai.test/v1/responses").mock(
            return_value=Response(200, json=_openai_responses_body("ok"))
        )
        results = await stage1_dispatch.run_stage1("run1", "prompt", test_config)

    by_provider = {r["provider"]: r for r in results}
    assert by_provider["deepseek"]["status"] == "error"
    assert "missing API key" in by_provider["deepseek"]["error"]
