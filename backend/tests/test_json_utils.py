import pytest

from app.pipeline.json_utils import extract_json


def test_plain_json():
    assert extract_json('{"claims": []}') == {"claims": []}


def test_json_in_code_fence():
    text = 'Here you go:\n```json\n{"claims": [{"claim": "x"}]}\n```\nthanks'
    assert extract_json(text) == {"claims": [{"claim": "x"}]}


def test_json_embedded_in_prose():
    text = 'Sure, the result is {"claims": [{"claim": "x", "verdict": "supported"}]} — hope that helps.'
    assert extract_json(text) == {"claims": [{"claim": "x", "verdict": "supported"}]}


def test_no_json_raises():
    with pytest.raises(ValueError):
        extract_json("no json here at all")
