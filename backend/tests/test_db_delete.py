def test_delete_run_removes_run_and_all_child_rows(test_db):
    run_id = test_db.create_run(prompt="x", skip_stage2=False, stage2_mode="designated_fact_checkers",
                                 synthesis_provider="anthropic")
    test_db.upsert_stage1_response(
        run_id=run_id, provider="anthropic", model="claude-test", status="ok",
        request={}, response_text="hi", thinking_text=None, raw_response={},
        error=None, input_tokens=1, output_tokens=1, cost_usd=0.01, latency_ms=10,
    )
    test_db.upsert_fact_check_result(
        run_id=run_id, checker_provider="anthropic", subject_provider="anthropic", status="ok",
        claims=[], raw_response={}, error=None, input_tokens=1, output_tokens=1, cost_usd=0.01, latency_ms=10,
    )
    test_db.upsert_synthesis_result(
        run_id=run_id, provider="anthropic", status="ok", synthesis_text="hi",
        raw_response={}, error=None, input_tokens=1, output_tokens=1, cost_usd=0.01, latency_ms=10,
    )
    test_db.upsert_citation_verification(
        run_id=run_id, url="https://example.com", found_in_sources=True,
        http_status=200, verified=True, method="HEAD", error=None,
    )

    # a second, unrelated run — must survive the delete untouched
    other_run_id = test_db.create_run(prompt="y", skip_stage2=False, stage2_mode="designated_fact_checkers",
                                       synthesis_provider="anthropic")
    test_db.upsert_stage1_response(
        run_id=other_run_id, provider="anthropic", model="claude-test", status="ok",
        request={}, response_text="hi", thinking_text=None, raw_response={},
        error=None, input_tokens=1, output_tokens=1, cost_usd=0.01, latency_ms=10,
    )

    deleted = test_db.delete_run(run_id)
    assert deleted is True

    assert test_db.get_run(run_id) is None
    assert test_db.get_stage1_responses(run_id) == []
    assert test_db.get_fact_check_results(run_id) == []
    assert test_db.get_synthesis_result(run_id) is None
    assert test_db.get_citation_verifications(run_id) == []

    # the other run's data is untouched
    assert test_db.get_run(other_run_id) is not None
    assert len(test_db.get_stage1_responses(other_run_id)) == 1


def test_delete_nonexistent_run_returns_false(test_db):
    assert test_db.delete_run("does-not-exist") is False
