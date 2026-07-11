from __future__ import annotations

import textwrap

import pytest

from app import config as config_module
from app import db as db_module

TEST_YAML = textwrap.dedent(
    """
    providers:
      anthropic:
        enabled: true
        display_name: "Anthropic"
        base_url: "https://fake-anthropic.test/v1/messages"
        api_key_env: "ANTHROPIC_API_KEY"
        model: "claude-test"
        max_tokens: 1024
        thinking: {type: "enabled", budget_tokens: 1000}
        request_style: anthropic
        pricing: {input_per_million: 10.0, output_per_million: 30.0}

      openai:
        enabled: true
        display_name: "OpenAI"
        base_url: "https://fake-openai.test/v1/responses"
        api_key_env: "OPENAI_API_KEY"
        model: "gpt-test"
        reasoning: {effort: "xhigh"}
        max_output_tokens: 1024
        request_style: openai_responses
        pricing: {input_per_million: 10.0, output_per_million: 30.0}

      deepseek:
        enabled: true
        display_name: "DeepSeek"
        base_url: "https://fake-deepseek.test/v1/chat/completions"
        api_key_env: "DEEPSEEK_API_KEY"
        model: "deepseek-test"
        reasoning_effort: "max"
        max_tokens: 1024
        request_style: openai_chat
        pricing: {input_per_million: 2.0, output_per_million: 8.0}

    pipeline:
      stage1:
        timeout_seconds: 5
      stage2:
        enabled: true
        mode: designated_fact_checkers
        fact_checkers: [anthropic]
        timeout_seconds: 5
      stage3:
        synthesis_provider: anthropic
        timeout_seconds: 5
      citations:
        timeout_seconds: 5
        retries: 0
        user_agent: "test-agent/1.0"
    """
)


@pytest.fixture
def test_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-openai")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-deepseek")
    yaml_path = tmp_path / "providers.yaml"
    yaml_path.write_text(TEST_YAML)
    return config_module.load_config(yaml_path)


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module.init_db()
    return db_module
