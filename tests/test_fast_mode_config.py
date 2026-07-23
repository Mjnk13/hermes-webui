"""Fast-mode WebUI config plumbing tests."""

from pathlib import Path

import pytest

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
        lambda model_id, *args, **kwargs: {"service_tier": "priority"},
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
        lambda model_id, *args, **kwargs: {"speed": "fast", "extra_body": {"fast": True}},
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

    def fake_fast_mode_overrides(model_id, *args, **kwargs):
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


def test_direct_openai_fast_mode_is_provider_verified():
    capability = cfg.resolve_fast_mode_capability("gpt-5.6", "openai")
    assert capability["supported"] is True
    assert capability["request_overrides"] == {"service_tier": "priority"}


def test_custom_codex_lb_does_not_infer_fast_from_gpt_name():
    config_data = {
        "custom_providers": [
            {
                "name": "codex-lb",
                "base_url": "https://proxy.example/v1",
                "api_mode": "codex_responses",
                "models": {"gpt-5.6-sol": {}},
            }
        ]
    }
    capability = cfg.resolve_fast_mode_capability(
        "gpt-5.6-sol", "codex-lb", config_data=config_data
    )
    assert capability["supported"] is False
    assert capability["request_overrides"] == {}
    assert "did not advertise" in capability["reason"]


def test_custom_provider_can_explicitly_declare_fast_wire_parameter():
    config_data = {
        "custom_providers": [
            {
                "name": "priority-proxy",
                "fast_mode": {"parameter": "service_tier", "value": "priority"},
                "models": {"gpt-5.6": {}},
            }
        ]
    }
    capability = cfg.resolve_fast_mode_capability(
        "gpt-5.6", "custom:priority-proxy", config_data=config_data
    )
    assert capability["supported"] is True
    assert capability["request_overrides"] == {"service_tier": "priority"}


def test_saved_fast_is_not_reported_effective_for_unsupported_provider(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
agent:
  service_tier: fast
model:
  default: gpt-5.6-sol
  provider: codex-lb
custom_providers:
  - name: codex-lb
    base_url: https://proxy.example/v1
    models:
      gpt-5.6-sol: {}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "_get_config_path", lambda: config_path)
    status = cfg.get_fast_mode_status()
    assert status["configured_fast_mode"] == "fast"
    assert status["fast_mode"] == "normal"
    assert status["supports_fast_mode"] is False
    assert status["service_tier"] == ""
    assert status["diagnostics"]["fallback"]


def test_enabling_fast_is_rejected_for_undeclared_custom_provider(monkeypatch, tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model:
  default: gpt-5.6-sol
  provider: codex-lb
custom_providers:
  - name: codex-lb
    base_url: https://proxy.example/v1
    models:
      gpt-5.6-sol: {}
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "_get_config_path", lambda: config_path)
    with pytest.raises(ValueError, match="did not advertise"):
        cfg.set_fast_mode("fast")


def test_runtime_does_not_send_fast_override_to_undeclared_custom_provider():
    config_data = {
        "agent": {"service_tier": "fast"},
        "model": {"default": "gpt-5.6-sol", "provider": "codex-lb"},
        "custom_providers": [
            {"name": "codex-lb", "models": {"gpt-5.6-sol": {}}}
        ],
    }
    assert cfg._main_model_request_overrides(
        config_data,
        effective_model="gpt-5.6-sol",
        effective_provider="codex-lb",
    ) == {}


def test_fast_chip_uses_effective_backend_capability_and_explains_unsupported():
    source = (Path(__file__).parent.parent / "static" / "ui.js").read_text(encoding="utf-8")
    assert "meta.unsupported_reason" in source
    assert "Fast Mode is not supported by this provider" in source
    assert "if(_currentFastSupported===false)" in source
    assert "Object.assign({mode:mode},_fastModeContext())" in source
