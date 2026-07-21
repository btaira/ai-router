import json

import respx
from httpx import Response

from app import db
from app.pipeline import stage2_factcheck, stage3_synthesis


def _seed_stage1(run_id="run1"):
    for provider, text in [
        ("anthropic", "Paris has a population of 5 million people."),
        ("openai", "Paris has a population of about 2.1 million people."),
        ("deepseek", "Paris has a population of about 2.1 million people."),
    ]:
        db.upsert_stage1_response(
            run_id=run_id, provider=provider, model=f"{provider}-test", status="ok",
            request={}, response_text=text, thinking_text=None, raw_response={},
            error=None, input_tokens=10, output_tokens=10, cost_usd=0.01, latency_ms=50,
        )


def _anthropic_json_body(payload: dict):
    return {
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "usage": {"input_tokens": 20, "output_tokens": 30},
    }


def _openai_responses_json_body(payload: dict):
    return {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(payload)}]}],
        "usage": {"input_tokens": 8, "output_tokens": 16},
    }


async def test_designated_fact_checker_parses_structured_claims(test_db, test_config):
    _seed_stage1()
    claims_payload = {
        "claims": [
            {"claim": "Paris population is 5 million", "verdict": "contradicted",
             "confidence": 0.9, "correction": "Paris population is about 2.1 million"}
        ]
    }
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json=_anthropic_json_body(claims_payload))
        )
        results = await stage2_factcheck.run_stage2("run1", "how many people live in paris", test_config)

    assert len(results) == 3  # one designated checker (anthropic) x 3 subjects
    by_subject = {r["subject_provider"]: r for r in results}
    assert by_subject["anthropic"]["status"] == "ok"
    claims = json.loads(by_subject["anthropic"]["claims_json"])
    assert claims[0]["verdict"] == "contradicted"


async def test_malformed_json_is_recorded_as_error(test_db, test_config):
    _seed_stage1()
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json={
                "content": [{"type": "text", "text": "sorry, I cannot comply with JSON formatting"}],
                "usage": {"input_tokens": 5, "output_tokens": 5},
            })
        )
        results = await stage2_factcheck.run_stage2("run1", "prompt", test_config)

    assert all(r["status"] == "error" for r in results)
    assert all("parse" in r["error"] for r in results)


async def test_skip_stage2_flag_short_circuits(test_db, test_config):
    run_id = db.create_run(prompt="x", skip_stage2=True, stage2_mode="designated_fact_checkers",
                            synthesis_provider="anthropic")
    _seed_stage1(run_id)
    results = await stage2_factcheck.run_stage2(run_id, "prompt", test_config)
    assert results == []


def test_extract_urls_dedupes_and_strips_trailing_punctuation():
    text = "See https://example.com/a. Also https://example.com/a and https://example.com/b,"
    urls = stage3_synthesis.extract_urls(text)
    assert urls == ["https://example.com/a", "https://example.com/b"]


async def test_synthesis_calls_designated_provider_and_stores_text(test_db, test_config):
    _seed_stage1()
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json={
                "content": [{"type": "text", "text": "Paris has about 2.1 million people (https://example.com/paris-pop)."}],
                "usage": {"input_tokens": 50, "output_tokens": 40},
            })
        )
        result = await stage3_synthesis.run_stage3("run1", "how many people live in paris", test_config)

    assert result["status"] == "ok"
    assert "2.1 million" in result["synthesis_text"]
    assert result["provider"] == "anthropic"


async def test_synthesis_with_no_stage1_answers_errors_gracefully(test_db, test_config):
    result = await stage3_synthesis.run_stage3("empty-run", "prompt", test_config)
    assert result["status"] == "error"
    assert "no successful stage-1" in result["error"]


async def test_synthesis_stores_thinking_text(test_db, test_config):
    _seed_stage1()
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json={
                "content": [
                    {"type": "thinking", "thinking": "Weighing which stage-1 answer is more accurate..."},
                    {"type": "text", "text": "Paris has about 2.1 million people."},
                ],
                "usage": {"input_tokens": 50, "output_tokens": 40},
            })
        )
        result = await stage3_synthesis.run_stage3("run1", "how many people live in paris", test_config)

    assert result["status"] == "ok"
    assert "Weighing which stage-1 answer" in result["thinking_text"]


async def test_run_level_fact_checkers_override_the_default(test_db, test_config):
    # TEST_YAML's configured default is fact_checkers: [anthropic] — this run
    # asks for openai instead, and only openai should be called as a checker.
    run_id = db.create_run(prompt="x", skip_stage2=False, stage2_mode="designated_fact_checkers",
                            synthesis_provider="anthropic", fact_checkers=["openai"])
    _seed_stage1(run_id)

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-openai.test/v1/responses").mock(
            return_value=Response(200, json=_openai_responses_json_body({"claims": []}))
        )
        results = await stage2_factcheck.run_stage2(run_id, "how many people live in paris", test_config)

    assert len(results) == 3  # openai x 3 subjects
    assert all(r["checker_provider"] == "openai" for r in results)


async def test_fact_check_prompt_uses_actual_model_names_not_provider_keys(test_db, test_config):
    # Provider keys ("moonshot", "openai") and slot labels mean nothing to
    # the model being asked to reason about "the answer from X", and can
    # drift out of date if a slot is repointed at a different model later
    # — the prompt should identify each answer by the model that actually
    # produced it (seeded as "<provider>-test" by _seed_stage1).
    _seed_stage1()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json=_anthropic_json_body({"claims": []}))
        )
        await stage2_factcheck.run_stage2("run1", "how many people live in paris", test_config)

    sent_bodies = [json.loads(call.request.content) for call in route.calls]
    prompt_texts = [b["messages"][-1]["content"] for b in sent_bodies]
    # anthropic is the sole checker (TEST_YAML default), reviewing all 3 subjects
    assert any("from anthropic-test" in t for t in prompt_texts)  # anthropic checking itself
    assert any("from openai-test" in t for t in prompt_texts)
    assert any("from deepseek-test" in t for t in prompt_texts)
    # and the "other answers" reference block uses model names too
    assert any("[openai-test]" in t or "[deepseek-test]" in t for t in prompt_texts)


async def test_checker_model_is_stored_on_fact_check_result(test_db, test_config):
    _seed_stage1()
    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json=_anthropic_json_body({"claims": []}))
        )
        results = await stage2_factcheck.run_stage2("run1", "prompt", test_config)

    # "claude-test" is anthropic's actual configured model in TEST_YAML —
    # the checker here, distinct from the "<provider>-test" strings
    # _seed_stage1 makes up for the stage-1 answers being reviewed.
    assert all(r["checker_model"] == "claude-test" for r in results)


async def test_synthesis_prompt_uses_actual_model_names_not_provider_keys(test_db, test_config):
    _seed_stage1()
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json={
                "content": [{"type": "text", "text": "Paris has about 2.1 million people."}],
                "usage": {"input_tokens": 50, "output_tokens": 40},
            })
        )
        await stage3_synthesis.run_stage3("run1", "how many people live in paris", test_config)

    sent_body = json.loads(route.calls[0].request.content)
    synth_prompt = sent_body["messages"][-1]["content"]
    assert "[anthropic-test]" in synth_prompt
    assert "[openai-test]" in synth_prompt
    assert "[deepseek-test]" in synth_prompt
    assert "3 independent AI models" in synth_prompt  # dynamic count, not hardcoded "six"


async def test_synthesis_fact_check_block_uses_actual_model_names(test_db, test_config):
    _seed_stage1()
    db.upsert_fact_check_result(
        run_id="run1", checker_provider="anthropic", subject_provider="openai", status="ok",
        claims=[{"claim": "population is 2.1M", "verdict": "supported", "confidence": 0.9, "correction": None}],
        raw_response={}, error=None, input_tokens=10, output_tokens=10, cost_usd=0.01, latency_ms=10,
        checker_model="anthropic-test",
    )
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json={
                "content": [{"type": "text", "text": "Paris has about 2.1 million people."}],
                "usage": {"input_tokens": 50, "output_tokens": 40},
            })
        )
        await stage3_synthesis.run_stage3("run1", "how many people live in paris", test_config)

    sent_body = json.loads(route.calls[0].request.content)
    synth_prompt = sent_body["messages"][-1]["content"]
    assert "anthropic-test reviewing openai-test" in synth_prompt


async def test_run_without_fact_checkers_override_uses_configured_default(test_db, test_config):
    run_id = db.create_run(prompt="x", skip_stage2=False, stage2_mode="designated_fact_checkers",
                            synthesis_provider="anthropic")  # no fact_checkers override
    _seed_stage1(run_id)

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json=_anthropic_json_body({"claims": []}))
        )
        results = await stage2_factcheck.run_stage2(run_id, "how many people live in paris", test_config)

    assert all(r["checker_provider"] == "anthropic" for r in results)  # TEST_YAML's configured default
