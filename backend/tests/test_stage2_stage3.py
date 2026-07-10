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
