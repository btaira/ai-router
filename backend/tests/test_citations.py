import respx
from httpx import Response

from app import db
from app.pipeline import citations


async def _seed_synthesis(test_db, text):
    db.upsert_synthesis_result(
        run_id="run1", provider="anthropic", status="ok", synthesis_text=text,
        raw_response={}, error=None, input_tokens=10, output_tokens=10,
        cost_usd=0.01, latency_ms=100,
    )


async def test_verified_citation_marked_ok(test_db, test_config):
    await _seed_synthesis(test_db, "See (https://example.com/real-page) for details.")

    with respx.mock(assert_all_called=True) as mock:
        mock.head("https://example.com/real-page").mock(return_value=Response(200))
        results = await citations.verify_citations("run1", test_config)

    assert len(results) == 1
    assert results[0]["verified"] == 1
    assert results[0]["http_status"] == 200


async def test_404_citation_marked_removed(test_db, test_config):
    await _seed_synthesis(test_db, "See (https://example.com/missing-page) for details.")

    with respx.mock(assert_all_called=True) as mock:
        mock.head("https://example.com/missing-page").mock(return_value=Response(404))
        results = await citations.verify_citations("run1", test_config)

    assert results[0]["verified"] == 0
    assert results[0]["http_status"] == 404


async def test_head_unsupported_falls_back_to_get(test_db, test_config):
    await _seed_synthesis(test_db, "See (https://example.com/head-blocked) now.")

    with respx.mock(assert_all_called=True) as mock:
        mock.head("https://example.com/head-blocked").mock(return_value=Response(405))
        mock.get("https://example.com/head-blocked").mock(return_value=Response(200))
        results = await citations.verify_citations("run1", test_config)

    assert results[0]["verified"] == 1
    assert results[0]["method"] == "GET"


async def test_private_host_is_blocked_without_network_call(test_db, test_config):
    await _seed_synthesis(test_db, "See (http://localhost:9999/internal) for the admin panel.")

    with respx.mock(assert_all_called=False) as mock:
        results = await citations.verify_citations("run1", test_config)
        assert mock.calls.call_count == 0  # SSRF guard must reject before any HTTP call

    assert results[0]["verified"] == 0
    assert "private" in results[0]["error"] or "internal" in results[0]["error"]


async def test_found_in_sources_flag(test_db, test_config):
    db.upsert_stage1_response(
        run_id="run1", provider="anthropic", model="claude-test", status="ok",
        request={}, response_text="Reference: https://example.com/real-page has the data.",
        thinking_text=None, raw_response={}, error=None,
        input_tokens=5, output_tokens=5, cost_usd=0.001, latency_ms=50,
    )
    await _seed_synthesis(test_db, "See (https://example.com/real-page) for details.")

    with respx.mock(assert_all_called=True) as mock:
        mock.head("https://example.com/real-page").mock(return_value=Response(200))
        results = await citations.verify_citations("run1", test_config)

    assert results[0]["found_in_sources"] == 1


async def test_no_synthesis_returns_empty(test_db, test_config):
    results = await citations.verify_citations("nonexistent-run", test_config)
    assert results == []
