import pytest

from app.config import (
    ModelUpdateError,
    load_config,
    update_provider_enabled,
    update_provider_model,
    update_provider_params,
)
from tests.conftest import TEST_YAML


@pytest.fixture
def yaml_path(tmp_path):
    p = tmp_path / "providers.yaml"
    p.write_text(TEST_YAML)
    return p


def test_updates_only_the_target_providers_model(yaml_path):
    before = yaml_path.read_text()
    update_provider_model("deepseek", "deepseek/deepseek-v5", path=yaml_path)
    after = yaml_path.read_text()

    cfg = load_config(yaml_path)
    assert cfg.providers["deepseek"].model == "deepseek/deepseek-v5"
    assert cfg.providers["anthropic"].model == "claude-test"  # untouched
    assert cfg.providers["openai"].model == "gpt-test"  # untouched

    before_lines = before.splitlines()
    after_lines = after.splitlines()
    changed = [i for i, (b, a) in enumerate(zip(before_lines, after_lines)) if b != a]
    assert changed == [before_lines.index('    model: "deepseek-test"')]


def test_preserves_line_endings_and_line_count(yaml_path):
    with open(yaml_path, newline="") as f:
        before = f.read()
    update_provider_model("anthropic", "claude-opus-5", path=yaml_path)
    with open(yaml_path, newline="") as f:
        after = f.read()
    assert before.count("\n") == after.count("\n")
    assert before.count("\r\n") == after.count("\r\n")


def test_handles_quotes_in_new_model_name(yaml_path):
    update_provider_model("anthropic", 'weird"model', path=yaml_path)
    cfg = load_config(yaml_path)
    assert cfg.providers["anthropic"].model == 'weird"model'


def test_unknown_provider_raises(yaml_path):
    with pytest.raises(ModelUpdateError, match="not found"):
        update_provider_model("nonexistent", "x", path=yaml_path)


def test_toggle_enabled_replaces_existing_field(yaml_path):
    update_provider_enabled("anthropic", False, path=yaml_path)
    assert load_config(yaml_path).providers["anthropic"].enabled is False
    update_provider_enabled("anthropic", True, path=yaml_path)
    assert load_config(yaml_path).providers["anthropic"].enabled is True
    # other providers untouched
    assert load_config(yaml_path).providers["openai"].enabled is True


def test_sampling_params_insert_update_and_clear(yaml_path):
    # deepseek has no temperature/top_p line in the fixture yaml — inserting
    update_provider_params("deepseek", temperature=0.7, top_p=0.9, path=yaml_path)
    cfg = load_config(yaml_path)
    assert cfg.providers["deepseek"].extra["temperature"] == 0.7
    assert cfg.providers["deepseek"].extra["top_p"] == 0.9

    # update in place
    update_provider_params("deepseek", temperature=0.2, top_p=0.9, path=yaml_path)
    cfg = load_config(yaml_path)
    assert cfg.providers["deepseek"].extra["temperature"] == 0.2

    # clearing both removes them from extra entirely
    update_provider_params("deepseek", temperature=None, top_p=None, path=yaml_path)
    cfg = load_config(yaml_path)
    assert "temperature" not in cfg.providers["deepseek"].extra
    assert "top_p" not in cfg.providers["deepseek"].extra

    # unrelated fields still intact
    assert cfg.providers["deepseek"].model == "deepseek-test"
