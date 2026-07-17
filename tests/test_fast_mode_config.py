"""Fast-mode WebUI config plumbing tests."""

from api import config as cfg


def test_parse_service_tier_matches_cli_fast_semantics():
    assert cfg.parse_service_tier_config("") is None
    assert cfg.parse_service_tier_config("normal") is None
    assert cfg.parse_service_tier_config("off") is None
    assert cfg.parse_service_tier_config("fast") == "priority"
    assert cfg.parse_service_tier_config("on") == "priority"
    assert cfg.parse_service_tier_config("priority") == "priority"


def test_fast_mode_adds_runtime_request_override(monkeypatch):
    monkeypatch.setattr(
        cfg,
        "_fast_mode_overrides_for_model",
        lambda model_id: {"service_tier": "priority"},
    )
    config_data = {
        "agent": {"service_tier": "fast"},
        "model": {"default": "gpt-5.5"},
    }

    assert cfg._main_model_request_overrides(config_data) == {"service_tier": "priority"}


def test_fast_mode_merges_with_existing_extra_body(monkeypatch):
    monkeypatch.setattr(
        cfg,
        "_fast_mode_overrides_for_model",
        lambda model_id: {"speed": "fast", "extra_body": {"fast": True}},
    )
    config_data = {
        "agent": {"service_tier": "priority"},
        "model": {
            "default": "claude-opus-4.6",
            "extra_body": {"metadata": {"source": "webui"}},
        },
    }

    overrides = cfg._main_model_request_overrides(config_data)

    assert overrides == {
        "speed": "fast",
        "extra_body": {"metadata": {"source": "webui"}, "fast": True},
    }
    assert overrides["extra_body"] is not config_data["model"]["extra_body"]


def test_normal_mode_does_not_add_fast_override(monkeypatch):
    called = False

    def fake_fast_mode_overrides(model_id):
        nonlocal called
        called = True
        return {"service_tier": "priority"}

    monkeypatch.setattr(cfg, "_fast_mode_overrides_for_model", fake_fast_mode_overrides)
    config_data = {
        "agent": {"service_tier": "normal"},
        "model": {"default": "gpt-5.5", "extra_body": {"foo": "bar"}},
    }

    assert cfg._main_model_request_overrides(config_data) == {"extra_body": {"foo": "bar"}}
    assert called is False
