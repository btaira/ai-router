import os

import pytest
from dotenv import dotenv_values

from app.config import set_provider_api_key
from tests.conftest import TEST_YAML


@pytest.fixture
def yaml_path(tmp_path):
    p = tmp_path / "providers.yaml"
    p.write_text(TEST_YAML)
    return p


@pytest.fixture
def env_path(tmp_path):
    return tmp_path / ".env"


@pytest.fixture(autouse=True)
def _clean_test_env_vars():
    # set_provider_api_key mutates the real os.environ directly (that's the
    # point — the running process needs to see the new key immediately), so
    # make sure nothing written by a test leaks into the next one.
    tracked = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"]
    before = {k: os.environ.get(k) for k in tracked}
    yield
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_setting_key_writes_file_and_updates_process_env(yaml_path, env_path):
    set_provider_api_key("anthropic", "sk-ant-newkey", path=yaml_path, env_path=env_path)

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-newkey"
    assert dotenv_values(env_path)["ANTHROPIC_API_KEY"] == "sk-ant-newkey"


def test_setting_key_creates_env_file_if_missing(yaml_path, env_path):
    assert not env_path.exists()
    set_provider_api_key("openai", "sk-openai-newkey", path=yaml_path, env_path=env_path)
    assert env_path.exists()


def test_setting_key_preserves_other_lines_in_env_file(yaml_path, env_path):
    env_path.write_text("SOME_OTHER_VAR=keep-me\nOPENAI_API_KEY=old-value\n")
    set_provider_api_key("openai", "sk-openai-newkey", path=yaml_path, env_path=env_path)

    values = dotenv_values(env_path)
    assert values["SOME_OTHER_VAR"] == "keep-me"
    assert values["OPENAI_API_KEY"] == "sk-openai-newkey"


def test_clearing_key_removes_from_file_and_process_env(yaml_path, env_path):
    set_provider_api_key("anthropic", "sk-ant-newkey", path=yaml_path, env_path=env_path)
    set_provider_api_key("anthropic", None, path=yaml_path, env_path=env_path)

    assert "ANTHROPIC_API_KEY" not in os.environ
    assert dotenv_values(env_path).get("ANTHROPIC_API_KEY") is None


def test_clearing_replaces_a_preexisting_value_entirely(yaml_path, env_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "deploy-time-key")
    set_provider_api_key("anthropic", "pasted-override-key", path=yaml_path, env_path=env_path)
    assert os.environ["ANTHROPIC_API_KEY"] == "pasted-override-key"

    set_provider_api_key("anthropic", None, path=yaml_path, env_path=env_path)
    # .env is the single source of truth now — clearing really does clear,
    # there's no separate overlay layer left to fall back to within this
    # process (a restart would re-read .env, which no longer has the line).
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_only_the_target_providers_key_is_touched(yaml_path, env_path):
    set_provider_api_key("anthropic", "sk-ant-1", path=yaml_path, env_path=env_path)
    set_provider_api_key("openai", "sk-openai-1", path=yaml_path, env_path=env_path)

    values = dotenv_values(env_path)
    assert values["ANTHROPIC_API_KEY"] == "sk-ant-1"
    assert values["OPENAI_API_KEY"] == "sk-openai-1"

    set_provider_api_key("anthropic", None, path=yaml_path, env_path=env_path)
    values = dotenv_values(env_path)
    assert "ANTHROPIC_API_KEY" not in values
    assert values["OPENAI_API_KEY"] == "sk-openai-1"  # untouched


def test_unknown_provider_raises(yaml_path, env_path):
    with pytest.raises(ValueError, match="unknown provider"):
        set_provider_api_key("nonexistent", "sk-x", path=yaml_path, env_path=env_path)


def test_key_with_special_characters_round_trips(yaml_path, env_path):
    tricky = 'sk-ant-a#b c"d\'e'
    set_provider_api_key("anthropic", tricky, path=yaml_path, env_path=env_path)
    assert dotenv_values(env_path)["ANTHROPIC_API_KEY"] == tricky
    assert os.environ["ANTHROPIC_API_KEY"] == tricky
