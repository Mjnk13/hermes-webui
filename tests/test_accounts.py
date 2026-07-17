import yaml


def test_openai_pro_account_uses_codex_oauth_provider():
    from api import accounts

    configured = accounts.get_accounts({
        "accounts": {
            "openai-pro": {
                "provider": "openai",
                "model": "gpt-5.4",
            },
        },
    })

    assert configured["openai-pro"]["provider"] == "openai-codex"


def test_openai_oauth_account_uses_codex_provider_without_name_heuristic():
    from api import accounts

    configured = accounts.get_accounts({
        "accounts": {
            "primary": {
                "provider": "openai",
                "auth_type": "oauth",
                "model": "gpt-5.4",
            },
        },
    })

    assert configured["primary"]["provider"] == "openai-codex"


def test_openai_api_key_account_keeps_explicit_provider():
    from api import accounts

    configured = accounts.get_accounts({
        "accounts": {
            "openai-api-route": {
                "provider": "openai",
                "auth_type": "api_key",
                "key_env": "OPENAI_API_KEY",
            },
        },
    })

    assert configured["openai-api-route"]["provider"] == "openai"


def test_set_active_account_persists_name_without_secret(monkeypatch, tmp_path):
    from api import accounts

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n"
        "  default: gpt-5.4\n"
        "  provider: openai-codex\n"
        "accounts:\n"
        "  codex-lb:\n"
        "    provider: codex-lb\n"
        "    base_url: https://cigro-codex.million.tk/v1\n"
        "    key_env: CODEX_LB_API_KEY\n"
        "    api_mode: codex_responses\n"
        "    model: gpt-5.5\n"
        "active_account: openai-pro\n",
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(accounts, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(accounts, "reload_config", lambda: calls.append("reload"))
    monkeypatch.setattr(accounts, "invalidate_models_cache", lambda: calls.append("invalidate"))
    monkeypatch.setenv("CODEX_LB_API_KEY", "sk-clb-secret")

    runtime = accounts.set_active_account("codex-lb")

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["active_account"] == "codex-lb"
    assert "sk-clb-secret" not in config_path.read_text(encoding="utf-8")
    assert runtime.api_key == "sk-clb-secret"
    assert calls == ["reload", "invalidate"]


def test_set_active_openai_pro_resolves_existing_codex_auth(monkeypatch, tmp_path):
    from api import accounts

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "model:\n"
        "  default: gpt-5.4\n"
        "  provider: openai-codex\n"
        "accounts:\n"
        "  openai-pro:\n"
        "    provider: openai\n"
        "    model: gpt-5.4\n"
        "active_account: codex-lb\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(accounts, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(accounts, "reload_config", lambda: None)
    monkeypatch.setattr(accounts, "invalidate_models_cache", lambda: None)

    runtime = accounts.set_active_account("openai-pro")

    saved = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert saved["active_account"] == "openai-pro"
    assert saved["accounts"]["openai-pro"]["provider"] == "openai"
    assert runtime.provider == "openai-codex"
    assert runtime.base_url == ""
    assert runtime.api_key == ""


def test_accounts_payload_reports_key_from_profile_env_file(monkeypatch, tmp_path):
    from api import accounts

    config_path = tmp_path / "config.yaml"
    (tmp_path / ".env").write_text("CODEX_LB_API_KEY=sk-from-env-file\n", encoding="utf-8")
    monkeypatch.delenv("CODEX_LB_API_KEY", raising=False)
    monkeypatch.setattr(accounts, "_get_config_path", lambda: config_path)

    payload = accounts.accounts_payload({
        "accounts": {
            "codex-lb": {
                "provider": "codex-lb",
                "base_url": "https://cigro-codex.million.tk/v1",
                "key_env": "CODEX_LB_API_KEY",
            },
        },
        "active_account": "codex-lb",
    })

    assert payload["active"] == "codex-lb"
    assert payload["accounts"][0]["api_key_configured"] is True
    assert "sk-from-env-file" not in str(payload)


def test_runtime_can_read_key_from_explicit_profile_env(monkeypatch, tmp_path):
    from api import accounts

    profile_env = tmp_path / "profile-a" / ".env"
    profile_env.parent.mkdir()
    profile_env.write_text("SHARED_PROVIDER_KEY=profile-a-key\n", encoding="utf-8")
    monkeypatch.delenv("SHARED_PROVIDER_KEY", raising=False)

    runtime = accounts.resolve_active_account_runtime(
        {
            "accounts": {
                "account-a": {
                    "provider": "openrouter",
                    "key_env": "SHARED_PROVIDER_KEY",
                },
            },
            "active_account": "account-a",
        },
        env_path=profile_env,
    )

    assert runtime is not None
    assert runtime.api_key == "profile-a-key"
