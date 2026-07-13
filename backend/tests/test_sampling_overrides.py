from app.config import strip_sampling_overrides


def test_strips_temperature_and_top_p(test_config):
    pcfg = test_config.providers["anthropic"]
    pcfg.extra["temperature"] = 0.2
    pcfg.extra["top_p"] = 0.8
    pcfg.extra["thinking"] = {"type": "adaptive"}  # unrelated key, must survive

    stripped = strip_sampling_overrides(pcfg)

    assert "temperature" not in stripped.extra
    assert "top_p" not in stripped.extra
    assert stripped.extra["thinking"] == {"type": "adaptive"}
    # original is untouched
    assert pcfg.extra["temperature"] == 0.2


def test_no_op_when_nothing_to_strip(test_config):
    pcfg = test_config.providers["anthropic"]
    pcfg.extra.pop("temperature", None)
    pcfg.extra.pop("top_p", None)
    stripped = strip_sampling_overrides(pcfg)
    assert stripped is pcfg  # returns the same object, no copy needed
