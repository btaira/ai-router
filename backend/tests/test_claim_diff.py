from app.pipeline.claim_diff import extract_claims, find_disagreement_candidates


def test_extract_claims_keeps_factual_sentences():
    text = "Paris is the capital of France. I like it. It was founded in 250 BC."
    claims = extract_claims(text)
    assert any("Paris" in c for c in claims)
    assert any("250 BC" in c for c in claims)


def test_find_disagreement_candidates_flags_unique_claim():
    answers = {
        "a": "The Eiffel Tower was completed in 1889.",
        "b": "The Eiffel Tower was completed in 1889.",
        "c": "The Eiffel Tower was completed in 1743, according to some records.",
    }
    flagged = find_disagreement_candidates(answers)
    assert "c" in flagged


def test_find_disagreement_candidates_no_flags_when_consistent():
    answers = {
        "a": "The Eiffel Tower was completed in 1889.",
        "b": "The Eiffel Tower was completed in 1889.",
    }
    flagged = find_disagreement_candidates(answers)
    assert flagged == {}
