import json

import pytest
import respx
from httpx import Response

from app import db
from app.pipeline import citations
from app.pipeline.followup import run_followup


def _seed_run(prompt="how many people live in paris?"):
    run_id = db.create_run(
        prompt=prompt, skip_stage2=True, stage2_mode="designated_fact_checkers",
        synthesis_provider="anthropic",
    )
    db.upsert_synthesis_result(
        run_id=run_id, provider="anthropic", status="ok",
        synthesis_text="Paris has about 2.1 million people.",
        raw_response={}, error=None, input_tokens=50, output_tokens=20,
        cost_usd=0.02, latency_ms=100,
    )
    return run_id


def _anthropic_body(text: str):
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 15, "output_tokens": 25},
    }


# --- db-level CRUD -----------------------------------------------------

def test_add_and_get_followup_messages(test_db):
    run_id = db.create_run(prompt="x", skip_stage2=True, stage2_mode="designated_fact_checkers",
                            synthesis_provider="anthropic")
    db.add_followup_message(run_id, 0, "user", "go deeper on that")
    db.add_followup_message(run_id, 0, "assistant", "sure, here's more detail", status="ok",
                             input_tokens=10, output_tokens=20, cost_usd=0.005, latency_ms=80)

    messages = db.get_followup_messages(run_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["cost_usd"] == 0.005


def test_next_followup_turn_index_increments(test_db):
    run_id = db.create_run(prompt="x", skip_stage2=True, stage2_mode="designated_fact_checkers",
                            synthesis_provider="anthropic")
    assert db.next_followup_turn_index(run_id) == 0
    db.add_followup_message(run_id, 0, "user", "q1")
    db.add_followup_message(run_id, 0, "assistant", "a1")
    assert db.next_followup_turn_index(run_id) == 1


def test_run_cost_summary_includes_followup(test_db):
    run_id = db.create_run(prompt="x", skip_stage2=True, stage2_mode="designated_fact_checkers",
                            synthesis_provider="anthropic")
    db.add_followup_message(run_id, 0, "user", "q1")
    db.add_followup_message(run_id, 0, "assistant", "a1", status="ok", cost_usd=0.03)

    summary = db.run_cost_summary(run_id)
    assert summary["followup_usd"] == pytest.approx(0.03)
    assert summary["total_usd"] == pytest.approx(0.03)


def test_run_cost_by_provider_attributes_followup_to_synthesis_provider(test_db):
    run_id = db.create_run(prompt="x", skip_stage2=True, stage2_mode="designated_fact_checkers",
                            synthesis_provider="anthropic")
    db.add_followup_message(run_id, 0, "user", "q1")
    db.add_followup_message(run_id, 0, "assistant", "a1", status="ok",
                             input_tokens=10, output_tokens=20, cost_usd=0.03)

    breakdown = db.run_cost_by_provider(run_id)
    assert breakdown["anthropic"]["followup"]["cost_usd"] == pytest.approx(0.03)
    assert breakdown["anthropic"]["total"]["cost_usd"] == pytest.approx(0.03)


# --- citations.verify_citations(text=...) -------------------------------

async def test_verify_citations_accepts_explicit_text(test_db, test_config):
    run_id = db.create_run(prompt="x", skip_stage2=True, stage2_mode="designated_fact_checkers",
                            synthesis_provider="anthropic")
    with respx.mock(assert_all_called=True) as mock:
        mock.head("https://example.com/followup-source").mock(return_value=Response(200))
        results = await citations.verify_citations(
            run_id, test_config, text="See (https://example.com/followup-source) for more."
        )
    assert results[0]["verified"] == 1
    assert results[0]["url"] == "https://example.com/followup-source"


# --- run_followup pipeline ----------------------------------------------

async def test_run_followup_happy_path_sends_concise_history(test_db, test_config):
    run_id = _seed_run()

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json=_anthropic_body("Here is more detail on Paris's population."))
        )
        result = await run_followup(run_id, "tell me more", test_config)

    sent_body = json.loads(route.calls[0].request.content)
    assert sent_body["messages"] == [
        {"role": "user", "content": "how many people live in paris?"},
        {"role": "assistant", "content": "Paris has about 2.1 million people."},
        {"role": "user", "content": "tell me more"},
    ]

    messages = result["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "tell me more"
    assert messages[1]["status"] == "ok"
    assert "more detail" in messages[1]["content"]


async def test_run_followup_second_turn_includes_prior_turn_in_history(test_db, test_config):
    run_id = _seed_run()

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json=_anthropic_body("first follow-up answer"))
        )
        await run_followup(run_id, "first question", test_config)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json=_anthropic_body("second follow-up answer"))
        )
        await run_followup(run_id, "second question", test_config)

    sent_body = json.loads(route.calls[0].request.content)
    roles_and_content = [(m["role"], m["content"]) for m in sent_body["messages"]]
    assert ("user", "first question") in roles_and_content
    assert ("assistant", "first follow-up answer") in roles_and_content
    assert roles_and_content[-1] == ("user", "second question")


async def test_run_followup_verifies_citations_in_reply(test_db, test_config):
    run_id = _seed_run()

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(
            return_value=Response(200, json=_anthropic_body(
                "More detail (https://example.com/followup-citation)."
            ))
        )
        mock.head("https://example.com/followup-citation").mock(return_value=Response(200))
        result = await run_followup(run_id, "tell me more", test_config)

    urls = {c["url"]: c for c in result["citation_verifications"]}
    assert urls["https://example.com/followup-citation"]["verified"] == 1


async def test_run_followup_records_provider_error(test_db, test_config):
    run_id = _seed_run()

    with respx.mock(assert_all_called=True) as mock:
        mock.post("https://fake-anthropic.test/v1/messages").mock(return_value=Response(500, text="boom"))
        result = await run_followup(run_id, "tell me more", test_config)

    assert result["messages"][-1]["status"] == "error"


async def test_run_followup_unknown_run_raises(test_db, test_config):
    with pytest.raises(ValueError, match="unknown run"):
        await run_followup("nonexistent-run", "hi", test_config)


async def test_run_followup_without_synthesis_raises(test_db, test_config):
    run_id = db.create_run(prompt="x", skip_stage2=True, stage2_mode="designated_fact_checkers",
                            synthesis_provider="anthropic")
    with pytest.raises(ValueError, match="no synthesized answer"):
        await run_followup(run_id, "hi", test_config)
