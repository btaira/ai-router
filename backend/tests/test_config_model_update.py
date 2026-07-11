import pytest

from app.config import ModelUpdateError, load_config, update_provider_model
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
