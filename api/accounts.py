"""Account profile helpers for WebUI model/provider runtime selection."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api.config import (
    _get_config_path,
    _load_yaml_config_file,
    _save_yaml_config_file,
    get_config,
    invalidate_models_cache,
    reload_config,
)


@dataclass
class AccountRuntime:
    name: str
    provider: str
    auth_type: str = ""
    model: str = ""
    base_url: str = ""
    key_env: str = ""
    api_key: str = ""
    api_mode: str = ""
    default_headers: dict[str, str] | None = None
    extra_body: dict[str, Any] | None = None


def _normalize_account(raw: Any, *, name: str = "") -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    account: dict[str, Any] = {}
    for key in ("provider", "auth_type", "model", "base_url", "key_env", "api_mode"):
        value = raw.get(key)
        if value is not None:
            account[key] = str(value).strip()
    if account.get("key_env") and not account.get("auth_type"):
        account["auth_type"] = "api_key"

    provider = str(account.get("provider") or "").strip().lower()
    auth_type = str(account.get("auth_type") or "").strip().lower()
    account_name = str(name or "").strip().lower()
    if (
        provider == "openai"
        and (
            auth_type.startswith("oauth")
            or account_name in {"openai-pro", "chatgpt-pro"}
        )
    ):
        # Hermes' provider registry uses ``openai-codex`` for ChatGPT/Codex
        # OAuth. A bare ``openai`` slug is not the OAuth provider and newer
        # agent builds reject it as unknown. Keep accepting the legacy account
        # shape without rewriting the operator's config.yaml.
        account["provider"] = "openai-codex"

    headers = raw.get("default_headers")
    if isinstance(headers, dict):
        account["default_headers"] = {
            str(k): str(v) for k, v in headers.items() if k is not None and v is not None
        }

    extra_body = raw.get("extra_body")
    if isinstance(extra_body, dict):
        account["extra_body"] = dict(extra_body)

    return account


def get_accounts(config: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    source = config if isinstance(config, dict) else get_config()
    raw_accounts = source.get("accounts")
    if not isinstance(raw_accounts, dict):
        return {}

    accounts: dict[str, dict[str, Any]] = {}
    for name, raw_account in raw_accounts.items():
        account_name = str(name or "").strip()
        if not account_name:
            continue
        account = _normalize_account(raw_account, name=account_name)
        if account:
            accounts[account_name] = account
    return accounts


def _legacy_account_from_model_config(config: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    model_cfg = config.get("model")
    if not isinstance(model_cfg, dict):
        return "", None

    provider = str(model_cfg.get("provider") or "").strip()
    model = str(model_cfg.get("default") or model_cfg.get("model") or "").strip()
    base_url = str(model_cfg.get("base_url") or "").strip()
    api_mode = str(model_cfg.get("api_mode") or "").strip()
    key_env = str(model_cfg.get("key_env") or "").strip()

    if not any((provider, model, base_url, key_env)):
        return "", None

    name = "current-config"
    if "cigro-codex.million.tk" in base_url.lower():
        name = "codex-lb"
    elif provider:
        name = f"{provider}-current"

    account: dict[str, Any] = {}
    if provider:
        account["provider"] = provider
    if model:
        account["model"] = model
    if base_url:
        account["base_url"] = base_url
    if api_mode:
        account["api_mode"] = api_mode
    if key_env:
        account["key_env"] = key_env
        account["auth_type"] = "api_key"
    return name, account


def get_active_account_name(config: dict[str, Any] | None = None) -> str:
    source = config if isinstance(config, dict) else get_config()
    raw = source.get("active_account")
    return str(raw or "").strip() if isinstance(raw, str) else ""


def get_active_account(config: dict[str, Any] | None = None) -> tuple[str, dict[str, Any] | None]:
    source = config if isinstance(config, dict) else get_config()
    active_name = get_active_account_name(source)
    if not active_name:
        return "", None
    return active_name, get_accounts(source).get(active_name)


def _read_env_file_value(env_path: Path, key: str) -> str:
    if not env_path.is_file():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            if name.strip().removeprefix("export ").strip() == key:
                return value.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _get_env_value(key: str, *, env_path: Path | None = None) -> str:
    env_key = str(key or "").strip()
    if not env_key:
        return ""
    value = (os.getenv(env_key, "") or "").strip()
    if value:
        return value
    try:
        resolved_env_path = env_path or (_get_config_path().parent / ".env")
        return _read_env_file_value(resolved_env_path, env_key).strip()
    except Exception:
        return ""


def resolve_active_account_runtime(
    config: dict[str, Any] | None = None,
    *,
    env_path: Path | None = None,
) -> AccountRuntime | None:
    source = config if isinstance(config, dict) else get_config()
    name, account = get_active_account(source)
    if not name or not account:
        return None

    key_env = str(account.get("key_env") or "").strip()
    headers = account.get("default_headers")
    extra_body = account.get("extra_body")
    return AccountRuntime(
        name=name,
        provider=str(account.get("provider") or "").strip(),
        auth_type=str(account.get("auth_type") or "").strip(),
        model=str(account.get("model") or "").strip(),
        base_url=str(account.get("base_url") or "").strip(),
        key_env=key_env,
        api_key=_get_env_value(key_env, env_path=env_path),
        api_mode=str(account.get("api_mode") or "").strip(),
        default_headers=dict(headers) if isinstance(headers, dict) else None,
        extra_body=dict(extra_body) if isinstance(extra_body, dict) else None,
    )


def apply_active_account_to_model_config(config: dict[str, Any]) -> dict[str, Any]:
    model_cfg = config.get("model")
    if isinstance(model_cfg, dict):
        merged = dict(model_cfg)
    elif isinstance(model_cfg, str) and model_cfg.strip():
        merged = {"default": model_cfg.strip()}
    else:
        merged = {}

    runtime = resolve_active_account_runtime(config)
    if runtime is None:
        return merged

    if runtime.model:
        merged["default"] = runtime.model
    if runtime.provider:
        merged["provider"] = runtime.provider
    if runtime.base_url:
        merged["base_url"] = runtime.base_url
    if runtime.key_env:
        merged["key_env"] = runtime.key_env
    if runtime.api_mode:
        merged["api_mode"] = runtime.api_mode
    return merged


def format_accounts(config: dict[str, Any] | None = None) -> str:
    source = config if isinstance(config, dict) else get_config()
    accounts = get_accounts(source)
    active = get_active_account_name(source)
    if not accounts:
        legacy_name, legacy_account = _legacy_account_from_model_config(source)
        if not legacy_name or not legacy_account:
            return "No accounts configured."
        accounts = {legacy_name: legacy_account}
        active = legacy_name

    def _display_provider(provider: str) -> str:
        labels = {
            "openai": "OpenAI",
            "openai-api": "OpenAI API",
            "openai-codex": "OpenAI Codex",
            "codex-lb": "codex-lb",
        }
        value = str(provider or "").strip()
        return labels.get(value.lower(), value or "unknown")

    def _display_auth(account: dict[str, Any]) -> str:
        auth_type = str(account.get("auth_type") or "").strip().lower()
        if auth_type == "oauth":
            return "OAuth"
        if auth_type == "api_key" or account.get("key_env"):
            key_env = str(account.get("key_env") or "").strip()
            return f"API Key ({key_env})" if key_env else "API Key"
        return auth_type or "unknown"

    lines = ["Available accounts:", ""]
    for name in sorted(accounts):
        account = accounts[name]
        marker = "*" if name == active else " "
        model = account.get("model") or account.get("default") or ""
        base_url = account.get("base_url") or ""
        lines.append(f"{marker} {name}")
        lines.append(f"  Provider: {_display_provider(account.get('provider') or 'unknown')}")
        if model:
            lines.append(f"  Model: {model}")
        if base_url:
            lines.append(f"  Endpoint: {base_url}")
        lines.append(f"  Auth: {_display_auth(account)}")
        lines.append("")
    if active:
        lines.append(f"Current account: {active}")
    return "\n".join(lines)


def set_active_account(name: str) -> AccountRuntime:
    account_name = str(name or "").strip()
    if not account_name:
        raise ValueError("Account name is required")

    config_path = _get_config_path()
    config_data = _load_yaml_config_file(config_path)
    accounts = get_accounts(config_data)
    if account_name not in accounts:
        available = ", ".join(sorted(accounts)) or "none"
        raise ValueError(f"Unknown account '{account_name}'. Available accounts: {available}")

    config_data["active_account"] = account_name
    _save_yaml_config_file(config_path, config_data)
    reload_config()
    invalidate_models_cache()

    runtime = resolve_active_account_runtime(config_data)
    if runtime is None:
        raise RuntimeError(f"Account '{account_name}' could not be resolved")
    return runtime


def accounts_payload(config: dict[str, Any] | None = None) -> dict[str, Any]:
    source = config if isinstance(config, dict) else get_config()
    active = get_active_account_name(source)
    accounts = []
    for name, account in sorted(get_accounts(source).items()):
        accounts.append({
            "name": name,
            "active": name == active,
            "provider": account.get("provider") or "",
            "model": account.get("model") or account.get("default") or "",
            "base_url": account.get("base_url") or "",
            "auth_type": account.get("auth_type") or ("api_key" if account.get("key_env") else ""),
            "key_env": account.get("key_env") or "",
            "api_mode": account.get("api_mode") or "",
            "api_key_configured": bool(_get_env_value(account.get("key_env") or "")),
        })
    return {"accounts": accounts, "active": active}
