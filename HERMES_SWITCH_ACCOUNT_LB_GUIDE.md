# Agent prompt: integrate a provider/account route into Hermes WebUI config

You are a coding agent working inside the `hermes-webui` repository. Your task is to add a new provider/account route to Hermes WebUI configuration by following the exact pattern used by the current `codex-lb` diff: configuration belongs in `config.yaml`, secrets belong in `.env`, WebUI can switch routes with `/account use <name>`, the current session syncs its `model_provider`, and streaming runtime uses the account `base_url`/API key instead of falling back to the previous provider.

## 0. Context to read before editing

Before changing code, read these files:

- `AGENTS.md`
- `README.md`
- `CONTRIBUTING.md`
- `docs/CONTRACTS.md`
- `CHANGELOG.md`

Then inspect the related diff/pattern in the current repo:

- `api/accounts.py`
- `api/config.py`
- `api/routes.py`
- `api/commands.py`
- `api/streaming.py`
- `static/commands.js`
- `static/messages.js`
- `static/sessions.js`
- `static/i18n.js`
- related tests:
  - `tests/test_accounts.py`
  - `tests/test_commands_endpoint.py`
  - `tests/test_model_resolver.py`
  - `tests/test_issue2518_active_provider_fallback.py`

Do not guess API/config shapes. Search for symbols before editing, for example:

- `apply_active_account_to_model_config`
- `resolve_active_account_runtime`
- `_resolve_custom_provider_runtime_overrides`
- `_should_attach_codex_provider_context`
- `_AGENT_COMMANDS_RUN_ON_WEBUI`
- `SLASH_SUBARG_SOURCES`
- `_runAgentCommandTransport`
- `_session_model_state_from_request`

## 1. Target behavior

After the change, the operator can configure an account route like this in a Hermes profile `config.yaml`:

```yaml
model:
  default: <fallback-model>
  provider: <fallback-provider>

accounts:
  <account-name>:
    provider: <provider-slug>
    model: <model-id>
    base_url: <openai-compatible-base-url>
    key_env: <ENV_VAR_NAME>
    api_mode: <optional-api-mode>
    auth_type: api_key

active_account: <account-name>
```

The secret must not be stored in `config.yaml`. The secret must live in the `.env` file for the correct Hermes profile:

```dotenv
<ENV_VAR_NAME>=<secret-api-key>
```

Example pattern currently applied for `codex-lb`:

```yaml
model:
  default: gpt-5.4
  provider: openai-codex

accounts:
  codex-lb:
    provider: codex-lb
    model: gpt-5.5
    base_url: https://cigro-codex.million.tk/v1
    key_env: CODEX_LB_API_KEY
    api_mode: codex_responses
    auth_type: api_key

active_account: codex-lb
```

## 1.1. WebUI environment variables to configure

When testing or deploying the account route, make the WebUI process start with the same Hermes state and agent checkout you expect. Configure these variables in the shell before launching WebUI, inline on the launch command, or in the WebUI repo `.env` file that `start.sh`/`bootstrap.py` load.

Important distinction:

- Provider secrets such as `CODEX_LB_API_KEY` belong in the Hermes profile `.env` next to that profile's `config.yaml`.
- Launcher/runtime variables such as `HERMES_WEBUI_AGENT_DIR`, `HERMES_WEBUI_PORT`, and `HERMES_HOME` can be exported in the shell or placed in the WebUI repo `.env`.

Variables:

| Variable | What it controls | Default/discovery |
|---|---|---|
| `HERMES_HOME` | Base Hermes state directory. This controls where WebUI/Hermes look for `config.yaml`, `.env`, `auth.json`, `state.db`, profiles, skills, and other Hermes state unless a more specific override is set. | POSIX default `~/.hermes`; Windows default `%LOCALAPPDATA%\\hermes`. |
| `HERMES_WEBUI_AGENT_DIR` | Path to the `hermes-agent` source checkout used by WebUI for agent imports/runtime integration. For this setup, use the checkout under the user's Hermes home. | Set explicitly to `/Users/harry/.hermes/hermes-agent`. |
| `HERMES_WEBUI_PORT` | HTTP port used by the WebUI server and related helpers. | Set explicitly to fixed port `8788` for this setup. |

Shell export example:

```bash
export HERMES_HOME=/Users/harry/.hermes
export HERMES_WEBUI_AGENT_DIR=/Users/harry/.hermes/hermes-agent
export HERMES_WEBUI_PORT=8788
./start.sh
```

Inline launch example:

```bash
HERMES_HOME=/Users/harry/.hermes \
HERMES_WEBUI_AGENT_DIR=/Users/harry/.hermes/hermes-agent \
HERMES_WEBUI_PORT=8788 \
python3 bootstrap.py --no-browser
```

WebUI repo `.env` example:

```dotenv
HERMES_HOME=/Users/harry/.hermes
HERMES_WEBUI_AGENT_DIR=/Users/harry/.hermes/hermes-agent
HERMES_WEBUI_PORT=8788
```

After changing these variables, restart the WebUI process. They are process startup settings; changing them after the server is already running will not reliably move existing sessions to the new home/agent/port.

Runtime requirements:

1. `get_effective_default_model()` must return the active account model when one is configured.
2. `resolve_model_provider(<model>)` must return the active account provider/base_url.
3. `get_available_models()` must read model config after applying the active account overlay.
4. The streaming worker must use the active account `base_url` and API key, even when Hermes CLI/runtime provider does not yet know the new provider slug.
5. `/account` lists configured accounts.
6. `/account use <name>` persists `active_account`, reloads config, invalidates the model cache, and does not write secrets to config.
7. The frontend runs `/account use <name>` through WebUI command transport, then syncs the current session: update the dropdown, persisted model state, `S.session.model`, `S.session.model_provider`, and call `/api/session/update`.
8. If the new provider is a GPT/OpenAI-family route but the model id is a bare `gpt-*`, session creation/provider-context repair must not fall back to the wrong `openai` or old `openai-codex` provider.

## 1.2. Compression fallback follows the active account

Keep Gemini as the explicit primary model for context-compression summaries:

```yaml
compression:
  abort_on_summary_failure: false

auxiliary:
  compression:
    provider: gemini
    model: gemini-3-flash-preview
    timeout: 120
```

Do not add a fixed `fallback_chain` entry solely to repeat the model from the
currently active account. For eligible request-time capacity, quota, rate-limit,
or connection failures, Hermes uses the live main agent provider and model as
the final model-based safety net for an explicit auxiliary provider. Therefore,
after `/account use <name>` synchronizes the current WebUI session's `model` and
`model_provider`, compression fallback must follow that active account. For
example, when `codex-lb` is active and its model is `gpt-5.6-sol`, the expected
summary route is:

```text
gemini-3-flash-preview
  -> active account model (gpt-5.6-sol in this example)
  -> deterministic local context marker if model summarization is exhausted
```

The result depends on where the route succeeds:

1. If Gemini succeeds, Hermes stores the real Gemini-generated summary.
2. If Gemini returns `429 RESOURCE_EXHAUSTED`, Hermes tries the synchronized
   active-account model.
3. If the active-account model succeeds, compression stores its real summary;
   a final `Compression summary failed` warning should not be emitted.
4. If model summarization is exhausted with a generic error such as `Your
   request was blocked` or a terminal quota response, and
   `abort_on_summary_failure` is `false`, Hermes inserts a bounded deterministic
   context marker, removes the compressible middle window, and increments the
   compression count.

Consequently, a message such as the following reports the completed local
marker fallback; it does not mean Hermes is only now starting the account-model
fallback:

```text
Compression summary failed: Your request was blocked.
Inserted a fallback context marker.
```

Genuine credential failures (for example HTTP 401 or an invalid API key) and
certain terminal network failures preserve the transcript unchanged instead of
dropping context. After changing source code or `config.yaml`, restart the
relevant CLI, gateway, or WebUI process so it loads the new behavior. A normal
WebUI `/account use <name>` switch should synchronize the open session without a
process restart when the account-switch implementation satisfies the runtime
requirements above.

For full diagnosis, recovery steps, and the deterministic-marker behavior, see
the companion guide at `~/.hermes/HERMES_COMPRESSION_ISSUES.md` (on this
installation: `/Users/harry/.hermes/HERMES_COMPRESSION_ISSUES.md`).

## 2. Backend config/account layer

If the repo does not yet have `api/accounts.py`, create this module using the current pattern. If it already exists, extend it carefully.

The module needs these main helpers:

- `AccountRuntime` dataclass:
  - `name`
  - `provider`
  - `auth_type`
  - `model`
  - `base_url`
  - `key_env`
  - `api_key`
  - `api_mode`
  - optional `default_headers`
  - optional `extra_body`

- `get_accounts(config=None)`:
  - read `config["accounts"]`
  - normalize account names and string fields
  - do not return secret values

- `get_active_account_name(config=None)`

- `get_active_account(config=None)`

- `_get_env_value(key)`:
  - prefer `os.getenv(key)`
  - fall back to reading `.env` next to the profile `config.yaml`
  - do not log or return the secret in public payloads

- `resolve_active_account_runtime(config=None)`:
  - resolve the active account into `AccountRuntime`
  - load `api_key` from env using `key_env`

- `apply_active_account_to_model_config(config)`:
  - clone `config["model"]`
  - overlay fields from the active account:
    - `default` <- `runtime.model`
    - `provider` <- `runtime.provider`
    - `base_url` <- `runtime.base_url`
    - `key_env` <- `runtime.key_env`
    - `api_mode` <- `runtime.api_mode`
  - if there is no active account, keep the original model config

- `format_accounts(config=None)`:
  - output text for `/account`
  - display provider/model/endpoint/auth
  - never display the API key

- `set_active_account(name)`:
  - load the correct profile config path with the existing `_get_config_path()` helper
  - validate that the name exists in `accounts`
  - set `active_account`
  - call `_save_yaml_config_file(...)`
  - call `reload_config()`
  - call `invalidate_models_cache()`
  - return `AccountRuntime`
  - never write secrets to config

- `accounts_payload(config=None)`:
  - return `{accounts: [...], active: <name>}` for the frontend
  - each account includes `name`, `active`, `provider`, `model`, `base_url`, `auth_type`, `key_env`, `api_mode`, `api_key_configured`
  - do not include the raw API key

In `api/config.py`:

1. `_apply_config_defaults(config_data)` must set defaults:
   - `accounts: {}`
   - `active_account: ""`

2. `get_config_for_profile_home(...)` must call `_apply_config_defaults(loaded)` after loading another profile's yaml, so account defaults also exist when reading a profile outside ambient context.

3. Add the new provider to `_PROVIDER_DISPLAY`:

```python
"<provider-slug>": "<Human Label>",
```

4. Add the new provider to `_PROVIDER_MODELS` if the dropdown/model catalog should know its static models:

```python
"<provider-slug>": [
    {"id": "<model-id>", "label": "<Model Label>"},
]
```

5. In model-config read paths, apply the active account overlay first:

- `resolve_model_provider(model_id)`
- `get_effective_default_model(config_data=None)`
- `get_available_models(prefer_cache=False)`

Pattern:

```python
try:
    from api.accounts import apply_active_account_to_model_config
    model_cfg = apply_active_account_to_model_config(cfg_or_active_cfg)
except Exception:
    model_cfg = cfg_or_active_cfg.get("model", {})
```

Keep the fallback exception-safe so WebUI does not crash if account runtime fails to load.

## 3. Backend routes and slash command

In `api/routes.py`, add a GET endpoint:

- `GET /api/accounts`
  - import `accounts_payload`
  - return the JSON payload

Add a POST endpoint:

- `POST /api/account/switch`
  - body `{name: "<account-name>"}`
  - validate that name is present
  - call `set_active_account(name)`
  - return `{ok, active, provider, model, base_url, api_mode}`
  - `ValueError` -> 404 or safe bad-request message
  - `RuntimeError` -> sanitized 500

In `api/commands.py`:

1. Add `account` to `_ALLOWED_AGENT_COMMANDS`.
2. Dispatch it in `execute_agent_command`:

```python
if canonical == "account":
    return _run_account_command(arg_string)
```

3. Implement `_run_account_command(arg_string)`:

- `/account` -> `format_accounts()`
- `/account use <name>` -> `set_active_account(name)` and return text:
  - `Switched account to <name> (<provider> · <model>).`
  - include endpoint if one exists
- invalid args -> `Usage: /account [use <name>]`
- never expose secrets

## 4. Runtime streaming: route the new provider through base_url/API key

In `api/streaming.py`, find `_resolve_custom_provider_runtime_overrides(...)`.

Extend the function signature so it accepts `account_config: dict | None = None`.

If the new provider is not natively understood by Hermes CLI/runtime provider, map it to a custom OpenAI-compatible runtime at streaming time:

```python
if str(resolved_provider or "").strip().lower() == "<provider-slug>":
    from api.accounts import resolve_active_account_runtime
    account = resolve_active_account_runtime(account_config)
    if account is not None and account.provider.strip().lower() == "<provider-slug>":
        if not resolved_api_key and account.api_key:
            resolved_api_key = account.api_key
        if not resolved_base_url and account.base_url:
            resolved_base_url = account.base_url
        if resolved_base_url:
            return "custom", resolved_api_key or _KEYLESS_CUSTOM_API_KEY, resolved_base_url
```

After `_run_agent_streaming` loads config for the session profile (`_cfg = get_config_for_profile_home(...)` or equivalent), call this override with `account_config=_cfg`. Important: the streaming worker is a detached thread and must not rely on the process-default profile; always pass the profile config loaded for the session.

In `_apply_profile_home_context_to_streaming_model(...)`, when reading the profile provider/default model, use `apply_active_account_to_model_config(_pp_cfg)` instead of reading `_pp_cfg["model"]` directly.

## 5. Provider-context repair for GPT-family bare models

If the new provider is OpenAI/Codex-compatible and the model id is a bare `gpt-*`, update the logic to avoid falling back to the wrong provider:

In `api/routes.py`, find `_should_attach_codex_provider_context(model, raw_active_provider, catalog)`.

- Update the docstring to state that the new provider is also a Codex/account route.
- Include the new provider in the provider condition:

```python
if raw_active_provider not in {"openai-codex", "<provider-slug>"}:
    return False
```

- When iterating catalog groups, compare with `raw_active_provider`; do not hardcode `openai-codex`.

In the frontend `static/sessions.js`, where provider family is normalized for new-session fallback, treat the new provider as OpenAI-family:

```javascript
if(s.startsWith('openai') || s === '<provider-slug>') return 'openai';
```

Also add the new provider case to the follow-up provider fallback tests.

## 6. Frontend slash command integration

In `static/messages.js`:

- Add `account` to `_AGENT_COMMANDS_RUN_ON_WEBUI` so `/account` is not sent into the agent chat as a normal prompt.

In `static/i18n.js`:

- Add this string:

```javascript
cmd_account: 'List or switch account profiles',
```

In `static/commands.js`:

1. Add slash subarg source:

```javascript
account:{desc:t('cmd_account'), subArgs:'accounts'},
```

2. Add account caches:

- `_slashAccountCache`
- `_slashAccountCachePromise`

3. Implement `_loadSlashAccountSubArgs(force=false)`:

- GET `/api/accounts`
- default values include `use`
- add `use <account.name>` for each account
- dedupe/sort
- fallback to `['use']`

4. Handle `spec === 'accounts'` in `_getSlashSubArgOptions(spec)`.

5. After WebUI command transport runs `/account use <name>`, sync the current session:

- parse the command with a strict regex:

```javascript
/^\/?account\s+use\s+([a-z0-9_-]+)\s*$/i
```

- GET `/api/accounts`, confirm the active account matches the requested name.
- load the active account.
- set `window._activeProvider = provider` if present.
- update the model dropdown with `_applyModelToDropdown(nextModel, sel, nextProvider)` if available.
- call `_writePersistedModelState(nextModel, nextProvider)`.
- if `S.session.session_id` exists, update it:

```javascript
await api('/api/session/update', {
  method:'POST',
  body: JSON.stringify({
    session_id: S.session.session_id,
    model: S.session.model,
    model_provider: nextProvider,
  }),
})
```

- then call `syncTopbar()` and refresh/render the session list cache.
- frontend sync errors should only `console.warn`; they must not fail the slash command output.

## 7. Required tests

Add/update tests following the current repo pattern. Run them through the repo runner `./scripts/test.sh`; do not use bare `pytest` unless there is a specific reason.

Required coverage:

1. Account persistence and secret safety (`tests/test_accounts.py`):

- `set_active_account("<account-name>")` writes `active_account` to config.
- It does not write the raw secret to `config.yaml`.
- `runtime.api_key` is read from env.
- `reload_config()` and `invalidate_models_cache()` are called.

2. Account payload from profile `.env` (`tests/test_accounts.py`):

- When the env var is absent from process env but present in the profile `.env`, `accounts_payload(...)["accounts"][0]["api_key_configured"] is True`.
- The payload does not contain the raw secret.

3. Backend slash command (`tests/test_commands_endpoint.py`):

- `/account` lists accounts.
- `/account use <name>` calls `set_active_account(name)`.
- Output contains account/provider/model/endpoint.

4. Model resolver (`tests/test_model_resolver.py`):

- active account overlay makes `get_effective_default_model()` return the account model.
- `resolve_model_provider(model)` returns `(model, provider, base_url)` from the account.

5. Provider fallback/session creation (`tests/test_issue2518_active_provider_fallback.py` or the corresponding test file):

- Bare `gpt-*` + the new active provider keeps provider context as the new provider.
- It does not fall back to `openai`, `openai-codex`, or `openrouter`.

If a frontend test harness exists, add unit coverage for the parser/sync behavior. If not, backend + session fallback tests must at least cover the invariant.

## 8. Docs/changelog

If this is user-visible behavior, update `CHANGELOG.md` under `[Unreleased]`, for example:

- Switching the active account to `<provider-slug>` now keeps GPT-family WebUI chats on that account instead of falling back to the default OpenAI/Codex route.

Do not add secrets, local-only notes, or private tokens to tracked docs.

## 9. Verification checklist

Before reporting done:

1. Check `git diff --cached`/`git diff` to verify no secrets are present.
2. Run targeted tests, for example:

```bash
./scripts/test.sh tests/test_accounts.py tests/test_commands_endpoint.py tests/test_model_resolver.py tests/test_issue2518_active_provider_fallback.py
```

3. If frontend command UX changed, manually verify in the browser:
   - `/account` shows the account list.
   - slash autocomplete suggests `account use <name>`.
   - `/account use <name>` switches the account.
   - the model dropdown/topbar changes to the account model/provider.
   - the current session stores the new `model_provider`.
   - the next turn uses the correct endpoint/key.

4. If the provider uses a custom OpenAI-compatible endpoint, verify that requests actually go through the account `base_url` and do not use the old OAuth/provider route.

## 10. Pitfalls to avoid

- Do not store API keys in `config.yaml`; store only `key_env`.
- Do not rely on process-global `cfg` inside the detached streaming thread when profile/session context exists.
- Do not hardcode only `openai-codex` in GPT-family fallback if the new provider is also a Codex/OpenAI-compatible route.
- Do not let `/account` enter the agent chat as a normal user prompt; it must run through WebUI command transport.
- Do not update only the dropdown/localStorage while forgetting `/api/session/update`; the next turn may still use the old `model_provider`.
- Do not merely add the provider to the dropdown; wire resolver + runtime override + session provider-context repair as well.
- Do not break existing named custom providers (`custom:<slug>`); keep the old fallback in `_resolve_custom_provider_runtime_overrides` after the new provider branch.
- Do not log raw secrets in tests, payloads, command output, or error messages.

## 11. Definition of done

The task is done only when all of the following are true:

- Config supports `accounts` + `active_account`.
- The active account consistently overlays model/provider/base_url.
- WebUI can list/switch accounts with `/account`.
- The current session syncs `model_provider` after switching.
- Streaming uses the account `base_url` + key.
- GPT-family bare models do not lose provider context.
- Tests pass through `./scripts/test.sh`.
- Changelog/docs are updated if behavior is user-visible.
