from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

import api.browser_workbench as browser_workbench
import api.config as config
import api.routes as routes


@pytest.fixture(autouse=True)
def _isolated_settings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER", "session-shell")
    browser_workbench.reset_browser_workbench_sessions_for_tests()


class _FakeHandler:
    def __init__(self, body: dict | bytes | None = None, *, headers: dict[str, str] | None = None):
        if isinstance(body, bytes):
            raw = body
        elif body is None:
            raw = b""
        else:
            raw = json.dumps(body).encode("utf-8")
        base_headers = {"Content-Length": str(len(raw)), "Accept-Encoding": "identity"}
        if headers:
            base_headers.update(headers)
        self.headers = base_headers
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None
        self.response_headers = []
        self.path = "/"
        self.close_connection = False

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers.append((key, value))

    def end_headers(self):
        pass

    def json_body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def _parsed(path: str):
    return urlparse(path)


def test_browser_workbench_capabilities_route_is_default_on_and_hides_private_backend_details():
    handler = _FakeHandler()

    assert routes.handle_get(handler, _parsed("/api/browser-workbench/capabilities")) is True

    body = handler.json_body()
    assert handler.status == 200
    assert body["ok"] is True
    assert body["enabled"] is False
    assert body["ui_enabled"] is True
    assert body["status"] == "limited"
    assert body["backend"] == "session-shell"
    assert body["message"]
    assert body["capabilities"]["session_lifecycle"] is True
    assert body["capabilities"]["navigation"] is True
    assert body["capabilities"]["iframe_bridge"] is True
    assert "cdp_endpoint" not in body
    assert "debugger_url" not in body


def test_browser_workbench_setting_defaults_on_and_legacy_false_does_not_hide_ui():
    assert config.load_settings()["browser_workbench_enabled"] is True
    assert browser_workbench.browser_workbench_ui_enabled({"browser_workbench_enabled": False}, {}) is True

    saved = config.save_settings({"browser_workbench_enabled": False})

    assert saved["browser_workbench_enabled"] is True
    assert config.load_settings()["browser_workbench_enabled"] is True
    assert browser_workbench.browser_workbench_ui_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE"])
def test_browser_workbench_env_can_turn_launcher_off(monkeypatch, value):
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH", value)

    assert config.load_settings()["browser_workbench_enabled"] is False
    assert browser_workbench.browser_workbench_ui_enabled() is False

    handler = _FakeHandler()
    assert routes.handle_get(handler, _parsed("/api/browser-workbench/capabilities")) is True
    body = handler.json_body()
    assert body["ui_enabled"] is False
    assert body["status"] == "unavailable"
    assert body["message"] == "Browser is disabled."


@pytest.mark.parametrize("value", ["1", "true", "TRUE"])
def test_browser_workbench_env_can_force_launcher_on(monkeypatch, value):
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH", value)

    assert config.load_settings()["browser_workbench_enabled"] is True
    assert browser_workbench.browser_workbench_ui_enabled() is True


def test_browser_workbench_capabilities_surface_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH", "true")

    handler = _FakeHandler()

    assert routes.handle_get(handler, _parsed("/api/browser-workbench/capabilities")) is True
    body = handler.json_body()
    assert body["ui_enabled"] is True
    assert body["enabled"] is False
    assert body["status"] == "limited"
    assert body["backend"] == "session-shell"
    assert body["capabilities"]["session_lifecycle"] is True
    assert body["capabilities"]["navigation"] is True
    assert body["capabilities"]["chii_devtools"] is True
    assert body["capabilities"]["native_devtools"] is False
    assert "cdp_endpoint" not in body
    assert "debugger_url" not in body


def test_browser_workbench_capabilities_are_enabled_by_default(monkeypatch):
    handler = _FakeHandler()

    assert routes.handle_get(handler, _parsed("/api/browser-workbench/capabilities")) is True

    body = handler.json_body()
    assert body["ui_enabled"] is True
    assert body["enabled"] is False
    assert body["status"] == "limited"
    assert body["capabilities"]["session_lifecycle"] is True
    assert body["capabilities"]["chii_devtools"] is True
    assert body["capabilities"]["popout_devtools"] is True


def test_browser_workbench_uses_session_shell_backend_adapter(monkeypatch):

    backend = browser_workbench.get_browser_workbench_backend()
    capabilities = backend.capabilities()

    assert backend.name == "session-shell"
    assert backend.embedded_browser_enabled is False
    assert capabilities["session_lifecycle"] is True
    assert capabilities["navigation"] is True
    assert capabilities["stop_loading"] is True
    assert capabilities["iframe_bridge"] is True
    assert capabilities["native_view"] is False
    assert capabilities["agent_input"] is False
    assert capabilities["native_devtools"] is False
    assert capabilities["chii_devtools"] is True
    assert capabilities["docked_devtools"] is True
    assert capabilities["popout_devtools"] is True

    handler = _FakeHandler()
    assert routes.handle_get(handler, _parsed("/api/browser-workbench/capabilities")) is True
    body = handler.json_body()
    assert body["backend"] == backend.name
    assert body["enabled"] is False
    assert body["capabilities"] == capabilities


def test_browser_workbench_auto_does_not_select_cdp_stream_when_browser_exists(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER", "auto")
    monkeypatch.setattr(browser_workbench, "_browser_binary_path", lambda environ=None: "/tmp/fake-browser")

    backend = browser_workbench.get_browser_workbench_backend()
    capabilities = backend.capabilities()

    assert backend.name == "session-shell"
    assert backend.embedded_browser_enabled is False
    assert capabilities["session_lifecycle"] is True
    assert capabilities["navigation"] is True
    assert capabilities["stop_loading"] is True
    assert capabilities["screenshot_crop"] is False
    assert capabilities["inspect"] is False
    assert capabilities["interactive_viewport"] is False
    assert capabilities["iframe_bridge"] is True
    assert capabilities["native_view"] is False
    assert capabilities["agent_input"] is False
    assert capabilities["native_devtools"] is False
    assert capabilities["chii_devtools"] is True
    assert capabilities["docked_devtools"] is True
    assert capabilities["popout_devtools"] is True

    handler = _FakeHandler()
    assert routes.handle_get(handler, _parsed("/api/browser-workbench/capabilities")) is True
    body = handler.json_body()
    assert body["backend"] == "session-shell"
    assert body["enabled"] is False
    assert body["capabilities"] == capabilities
    assert body["message"] == "Browser is ready."
    assert "cdp_endpoint" not in body
    assert "debugger_url" not in body


def test_browser_workbench_explicit_cdp_renderer_selects_stream_backend(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER", "cdp-browser")
    monkeypatch.setattr(browser_workbench, "_browser_binary_path", lambda environ=None: "/tmp/fake-browser")

    backend = browser_workbench.get_browser_workbench_backend()
    capabilities = backend.capabilities()

    assert backend.name == "cdp-browser"
    assert backend.embedded_browser_enabled is True
    assert capabilities["session_lifecycle"] is True
    assert capabilities["navigation"] is True
    assert capabilities["stop_loading"] is True
    assert capabilities["screenshot_crop"] is True
    assert capabilities["inspect"] is True
    assert capabilities["interactive_viewport"] is True
    assert capabilities["iframe_bridge"] is True
    assert capabilities["native_view"] is False
    assert capabilities["agent_input"] is False
    assert capabilities["native_devtools"] is True
    assert capabilities["chii_devtools"] is False
    assert capabilities["docked_devtools"] is True
    assert capabilities["popout_devtools"] is False

    handler = _FakeHandler()
    assert routes.handle_get(handler, _parsed("/api/browser-workbench/capabilities")) is True
    body = handler.json_body()
    assert body["backend"] == "cdp-browser"
    assert body["enabled"] is True
    assert body["capabilities"] == capabilities
    assert body["message"] == "Browser is ready."
    assert "cdp_endpoint" not in body
    assert "debugger_url" not in body


def test_browser_workbench_auto_selects_electron_native_backend_when_bridge_exists(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER", "electron-native")
    monkeypatch.setenv("HERMES_WEBUI_DESKTOP_BRIDGE_URL", "http://127.0.0.1:9234")
    monkeypatch.setenv("HERMES_WEBUI_DESKTOP_BRIDGE_TOKEN", "secret-token")
    monkeypatch.setattr(browser_workbench, "_desktop_bridge_is_reachable", lambda bridge_url, bridge_token, **_: True)

    backend = browser_workbench.get_browser_workbench_backend()
    capabilities = backend.capabilities()

    assert backend.name == "electron-native"
    assert backend.embedded_browser_enabled is True
    assert capabilities["session_lifecycle"] is True
    assert capabilities["navigation"] is True
    assert capabilities["stop_loading"] is True
    assert capabilities["interactive_viewport"] is True
    assert capabilities["inspect"] is True
    assert capabilities["native_view"] is True
    assert capabilities["agent_input"] is True
    assert capabilities["iframe_bridge"] is False
    assert capabilities["native_devtools"] is True
    assert capabilities["chii_devtools"] is False
    assert capabilities["docked_devtools"] is True
    assert capabilities["popout_devtools"] is True

    handler = _FakeHandler()
    assert routes.handle_get(handler, _parsed("/api/browser-workbench/capabilities")) is True
    body = handler.json_body()
    assert body["backend"] == "electron-native"
    assert body["enabled"] is True
    assert body["capabilities"] == capabilities
    assert body["message"] == "Browser is ready."
    assert "secret-token" not in json.dumps(body)
    assert "HERMES_WEBUI_DESKTOP_BRIDGE_TOKEN" not in json.dumps(body)


def test_browser_workbench_desktop_bridge_registration_selects_electron_backend(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER", "auto")
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    monkeypatch.setattr(browser_workbench.CdpBrowserWorkbenchBackend, "is_available", classmethod(lambda cls: False))
    monkeypatch.setattr(browser_workbench, "_desktop_bridge_is_reachable", lambda bridge_url, bridge_token, **_: True)
    config.save_settings({"browser_workbench_enabled": True})

    handler = _FakeHandler({"bridge_url": "http://127.0.0.1:9234", "bridge_token": "a" * 32})
    assert routes.handle_post(handler, _parsed("/api/browser-workbench/desktop-bridge")) is True
    body = handler.json_body()

    assert handler.status == 200
    assert body["backend"] == "electron-native"
    assert body["enabled"] is True
    assert body["capabilities"]["native_view"] is True
    assert body["desktop_bridge"] == {"registered": True, "bridge_url": "http://127.0.0.1:9234"}
    assert "a" * 32 not in json.dumps(body)


def test_browser_workbench_auto_mode_ignores_unreachable_stale_electron_bridge(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER", "auto")
    monkeypatch.setenv("HERMES_WEBUI_DESKTOP_BRIDGE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("HERMES_WEBUI_DESKTOP_BRIDGE_TOKEN", "x" * 32)
    monkeypatch.setattr(browser_workbench.CdpBrowserWorkbenchBackend, "is_available", classmethod(lambda cls: False))
    monkeypatch.setattr(browser_workbench, "_desktop_bridge_is_reachable", lambda bridge_url, bridge_token, **_: False)
    config.save_settings({"browser_workbench_enabled": True})

    handler = _FakeHandler({"url": "http://127.0.0.1:5173"})
    assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is True
    body = handler.json_body()

    assert handler.status == 200
    assert body["backend"] == "session-shell"
    assert body["renderer"] == "iframe-bridge"
    assert "Electron native bridge request failed" not in json.dumps(body)


def test_browser_workbench_desktop_bridge_reregistration_preserves_sessions(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER", "auto")
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    monkeypatch.setattr(browser_workbench.CdpBrowserWorkbenchBackend, "is_available", classmethod(lambda cls: False))
    monkeypatch.setattr(browser_workbench, "_desktop_bridge_is_reachable", lambda bridge_url, bridge_token, **_: True)
    config.save_settings({"browser_workbench_enabled": True})

    bridge_calls = []

    def fake_bridge_request(self, method: str, path: str, payload: dict | None = None):
        del self
        bridge_calls.append((method, path, payload))
        return {
            "ok": True,
            "renderer": "electron-native",
            "url": payload.get("url") if payload else "http://localhost:3000",
            "message": "native tab ready",
        }

    monkeypatch.setattr(browser_workbench.ElectronNativeBrowserWorkbenchBackend, "_bridge_request", fake_bridge_request)

    first_registration = _FakeHandler({"bridge_url": "http://127.0.0.1:9234", "bridge_token": "a" * 32})
    assert routes.handle_post(first_registration, _parsed("/api/browser-workbench/desktop-bridge")) is True
    assert first_registration.status == 200

    create_handler = _FakeHandler({"url": "http://localhost:3000"})
    assert routes.handle_post(create_handler, _parsed("/api/browser-workbench/session")) is True
    assert create_handler.status == 200
    session_id = create_handler.json_body()["session_id"]

    second_registration = _FakeHandler({"bridge_url": "http://127.0.0.1:9234", "bridge_token": "a" * 32})
    assert routes.handle_post(second_registration, _parsed("/api/browser-workbench/desktop-bridge")) is True
    assert second_registration.status == 200

    status_handler = _FakeHandler()
    assert routes.handle_get(status_handler, _parsed(f"/api/browser-workbench/session/{session_id}")) is True
    status_body = status_handler.json_body()

    assert status_handler.status == 200
    assert status_body["session_id"] == session_id
    assert status_body["backend"] == "electron-native"
    assert status_body["status"] != "missing"
    assert bridge_calls[0][0:2] == ("POST", "/tabs")


def test_browser_workbench_normal_browser_client_does_not_inherit_registered_electron_bridge(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER", "auto")
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    monkeypatch.setattr(browser_workbench.CdpBrowserWorkbenchBackend, "is_available", classmethod(lambda cls: False))
    monkeypatch.setattr(browser_workbench, "_desktop_bridge_is_reachable", lambda bridge_url, bridge_token, **_: True)
    config.save_settings({"browser_workbench_enabled": True})

    bridge_calls = []

    def fake_bridge_request(self, method: str, path: str, payload: dict | None = None):
        del self
        bridge_calls.append((method, path, payload))
        return {
            "ok": True,
            "renderer": "electron-native",
            "url": payload.get("url") if payload else "http://localhost:3000",
            "message": "native tab ready",
        }

    monkeypatch.setattr(browser_workbench.ElectronNativeBrowserWorkbenchBackend, "_bridge_request", fake_bridge_request)

    registration = _FakeHandler({"bridge_url": "http://127.0.0.1:9234", "bridge_token": "a" * 32})
    assert routes.handle_post(registration, _parsed("/api/browser-workbench/desktop-bridge")) is True
    assert registration.status == 200

    electron_handler = _FakeHandler({"url": "http://localhost:3000", "client_renderer": "electron-native", "electron_native_available": True})
    assert routes.handle_post(electron_handler, _parsed("/api/browser-workbench/session")) is True
    electron_body = electron_handler.json_body()
    assert electron_body["backend"] == "electron-native"
    assert electron_body["renderer"] == "electron-native"
    assert bridge_calls[-1][0:2] == ("POST", "/tabs")

    before_normal_browser_calls = len(bridge_calls)
    normal_handler = _FakeHandler({"url": "http://127.0.0.1:5173", "client_renderer": "iframe-bridge", "electron_native_available": False})
    assert routes.handle_post(normal_handler, _parsed("/api/browser-workbench/session")) is True
    normal_body = normal_handler.json_body()
    normal_session_id = normal_body["session_id"]

    assert normal_handler.status == 200
    assert normal_body["backend"] == "session-shell"
    assert normal_body["renderer"] == "iframe-bridge"
    assert len(bridge_calls) == before_normal_browser_calls

    status_handler = _FakeHandler()
    assert routes.handle_get(status_handler, _parsed(f"/api/browser-workbench/session/{normal_session_id}")) is True
    status_body = status_handler.json_body()

    assert status_handler.status == 200
    assert status_body["backend"] == "session-shell"
    assert status_body["renderer"] == "iframe-bridge"
    assert len(bridge_calls) == before_normal_browser_calls


def test_electron_native_backend_delegates_to_bridge_and_sanitizes_payload(monkeypatch):
    class _BridgeBackend(browser_workbench.ElectronNativeBrowserWorkbenchBackend):
        def __init__(self):
            super().__init__(bridge_url="http://127.0.0.1:9234", bridge_token="secret-token")
            self.calls = []

        def _bridge_request(self, method: str, path: str, payload: dict | None = None):
            self.calls.append((method, path, payload))
            if path.endswith("/inspect"):
                return {
                    "ok": True,
                    "selection": {
                        "type": "browser_element",
                        "selector": "#save",
                        "component": "SaveButton",
                        "tag": "BUTTON",
                        "source": "src/SaveButton.tsx:7",
                        "text": "Save",
                        "cdp_endpoint": "ws://secret",
                    },
                    "debugger_url": "http://secret",
                }
            return {
                "ok": True,
                "renderer": "electron-native",
                "url": payload.get("url") if payload else "http://localhost:3000",
                "message": "native tab ready",
                "cdp_endpoint": "ws://secret",
            }

    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    config.save_settings({"browser_workbench_enabled": True})
    backend = _BridgeBackend()
    browser_workbench.set_browser_workbench_backend_for_tests(backend)

    create_handler = _FakeHandler({"url": "http://localhost:3000"})
    assert routes.handle_post(create_handler, _parsed("/api/browser-workbench/session")) is True
    body = create_handler.json_body()
    session_id = body["session_id"]

    assert create_handler.status == 200
    assert body["backend"] == "electron-native"
    assert body["renderer"] == "electron-native"
    assert body["capabilities"]["native_view"] is True
    assert backend.calls[0][0:2] == ("POST", "/tabs")
    assert backend.calls[0][2]["session_id"] == session_id
    assert "secret-token" not in json.dumps(body)
    assert "cdp_endpoint" not in json.dumps(body)

    inspect_handler = _FakeHandler({"x": 8, "y": 9, "viewport": {"width": 800, "height": 600}})
    assert routes.handle_post(inspect_handler, _parsed(f"/api/browser-workbench/session/{session_id}/inspect")) is True
    inspect_body = inspect_handler.json_body()

    assert inspect_handler.status == 200
    assert inspect_body["selection"]["selector"] == "#save"
    assert inspect_body["selection"]["component"] == "SaveButton"
    assert inspect_body["selection"]["tag"] == "BUTTON"
    assert inspect_body["selection"]["display_label"] == "SaveButton · BUTTON"
    assert "cdp_endpoint" not in json.dumps(inspect_body)
    assert "debugger_url" not in json.dumps(inspect_body)

    stop_handler = _FakeHandler({"zoom": 1})
    assert routes.handle_post(stop_handler, _parsed(f"/api/browser-workbench/session/{session_id}/stop-loading")) is True
    assert stop_handler.status == 200
    assert backend.calls[-1][0:2] == ("POST", f"/tabs/{session_id}/stop-loading")


def test_browser_workbench_adapter_payloads_strip_private_debugger_fields(monkeypatch):
    class _LeakyBackend:
        name = "test-cdp"
        embedded_browser_enabled = True
        message = "test backend"

        def capabilities(self):
            return {
                "session_lifecycle": True,
                "navigation": True,
                "interactive_viewport": True,
                "inspect": True,
                "console": True,
                "network": True,
                "screenshot_crop": True,
            }

        def create_or_attach(self, body):
            return {
                "ok": True,
                "session_id": "bw_test",
                "status": "ready",
                "backend": self.name,
                "cdp_endpoint": "ws://127.0.0.1:9222/devtools/page/secret",
                "debugger_url": "http://127.0.0.1:9222/json/list",
                "nested": {
                    "cdp_endpoint": "ws://nested-secret",
                    "debugger_url": "http://nested-secret",
                    "safe": "kept",
                },
            }, 200

        def get(self, session_id):
            return self.create_or_attach({})

        def close(self, session_id):
            return self.create_or_attach({})

        def reset_for_tests(self):
            pass

    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    config.save_settings({"browser_workbench_enabled": True})
    browser_workbench.set_browser_workbench_backend_for_tests(_LeakyBackend())

    handler = _FakeHandler({"url": "http://localhost:3000"})

    assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is True
    body = handler.json_body()
    assert handler.status == 200
    assert body["ok"] is True
    assert body["backend"] == "test-cdp"
    assert body["nested"] == {"safe": "kept"}
    assert "cdp_endpoint" not in json.dumps(body)
    assert "debugger_url" not in json.dumps(body)


def test_browser_workbench_session_create_ignores_legacy_disabled_setting(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    config.save_settings({"browser_workbench_enabled": False})

    handler = _FakeHandler({"url": "http://localhost:3000"})

    assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is True
    body = handler.json_body()
    assert handler.status == 200
    assert body["ok"] is True
    assert body["session_id"].startswith("bw_")


def test_browser_workbench_session_create_enabled_by_default(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)

    handler = _FakeHandler({"url": "http://localhost:3000"})

    assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is True
    body = handler.json_body()
    assert handler.status == 200
    assert body["ok"] is True
    assert body["status"] == "ready"
    assert body["session_id"].startswith("bw_")
    assert "cdp_endpoint" not in body
    assert "debugger_url" not in body


def test_browser_workbench_stop_loading_route_resets_load_status(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)

    create_handler = _FakeHandler({"url": "http://localhost:3000"})
    assert routes.handle_post(create_handler, _parsed("/api/browser-workbench/session")) is True
    session_id = create_handler.json_body()["session_id"]

    stop_handler = _FakeHandler({"zoom": 1.25})
    assert routes.handle_post(stop_handler, _parsed(f"/api/browser-workbench/session/{session_id}/stop-loading")) is True
    body = stop_handler.json_body()

    assert stop_handler.status == 200
    assert body["ok"] is True
    assert body["load_status"] == "idle"
    assert body["load_error"] == ""
    assert body["zoom"] == 1.25


def test_browser_workbench_stop_loading_action_parser_matches_runtime_error_path():
    path = "/api/browser-workbench/session/bw_d7naEH_tt_ll1pUX/stop-loading"

    assert browser_workbench._extract_session_action(path) == ("bw_d7naEH_tt_ll1pUX", "stop-loading")
    assert browser_workbench._extract_session_id(path) is None


def test_browser_workbench_electron_bridge_errors_are_public_render_errors():
    backend = browser_workbench.ElectronNativeBrowserWorkbenchBackend(
        bridge_url="http://127.0.0.1:9",
        bridge_token="x" * 16,
    )

    state = backend._bridge_public_state(
        {
            "status": "bridge_error",
            "message": "Electron native bridge request failed: timed out",
            "render_error": "timed out",
        }
    )

    assert state["status"] == "bridge_error"
    assert state["render_error"] == "timed out"
    assert state["message"] == "Electron native bridge request failed: timed out"


def test_browser_workbench_loopback_session_uses_iframe_bridge_renderer(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)

    handler = _FakeHandler({"url": "127.0.0.1:5173/app?x=1"})

    assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is True
    body = handler.json_body()

    assert handler.status == 200
    assert body["ok"] is True
    assert body["renderer"] == "iframe-bridge"
    assert body["bridge_url"].startswith("/browser-proxy/http://127.0.0.1:5173/app%3Fx=1?__hermes_bw_session=bw_")
    assert "__hermes_bw_frame=bw_" in body["bridge_url"]
    assert "screenshot_data_url" not in body
    assert "render_error" not in body


def test_browser_workbench_session_shell_chii_devtools_are_session_scoped(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    monkeypatch.setattr(browser_workbench, "_chii_devtools_url", lambda sid: f"http://127.0.0.1:8080/front_end/chii_app.html?ws=client-for-{sid}")

    one = _FakeHandler({"url": "http://127.0.0.1:5173/one"})
    two = _FakeHandler({"url": "http://127.0.0.1:5173/two"})
    assert routes.handle_post(one, _parsed("/api/browser-workbench/session")) is True
    assert routes.handle_post(two, _parsed("/api/browser-workbench/session")) is True
    one_id = one.json_body()["session_id"]
    two_id = two.json_body()["session_id"]

    for session_id in (one_id, two_id):
        handler = _FakeHandler({"mode": "popout"})
        assert routes.handle_post(handler, _parsed(f"/api/browser-workbench/session/{session_id}/devtools")) is True
        body = handler.json_body()
        assert handler.status == 200
        assert body["devtools_url"].endswith(f"client-for-{session_id}")
        assert body["chii_devtools"]["target_id"] == browser_workbench._chii_target_id_for_session(session_id)
        assert body["chii_devtools"]["popout"] is True

    assert one_id != two_id


def test_browser_workbench_chii_bootstrap_and_runtime_are_frame_local(monkeypatch):
    monkeypatch.setattr(browser_workbench, "_ensure_chii_service", lambda: "http://127.0.0.1:18080/")

    bootstrap_handler = _FakeHandler()
    assert routes.handle_get(bootstrap_handler, _parsed("/api/browser-workbench/chii/target.js?session_id=bw_abc&target_id=hermes_bw_bw_abc")) is True
    bootstrap = bootstrap_handler.wfile.getvalue().decode("utf-8")

    assert bootstrap_handler.status == 200
    assert "window.ChiiServerUrl = chiiBaseUrl" in bootstrap
    assert "window.ChiiTargetId = targetId" in bootstrap
    assert "/api/browser-workbench/chii/target-runtime.js" in bootstrap
    assert "target_id=" in bootstrap

    class _FakeChiiResponse:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
        def read(self, limit=-1):
            return b'e.id=w,w||(e.id=w=(0,s.default)(6),p.setItem("chii-id",w))'

    monkeypatch.setattr(browser_workbench.urllib.request, "urlopen", lambda url, timeout=None: _FakeChiiResponse())
    runtime_handler = _FakeHandler()
    assert routes.handle_get(runtime_handler, _parsed("/api/browser-workbench/chii/target-runtime.js?target_id=hermes_bw_bw_abc")) is True
    runtime = runtime_handler.wfile.getvalue().decode("utf-8")

    assert runtime_handler.status == 200
    assert "window.ChiiTargetId||w" in runtime
    assert "target-id patch unavailable" not in runtime


def test_browser_workbench_iframe_proxy_strips_frame_headers_and_injects_bridge(monkeypatch):
    captured = {}

    class _FakeProxyResponse:
        status = 200
        headers = {
            "Content-Type": "text/html; charset=utf-8",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "frame-ancestors 'none'",
            "Set-Cookie": "secret=1",
        }

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            return b'<html><head><title>Dev</title></head><body><script src="/assets/app.js"></script><a href="/next">Next</a></body></html>'

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return _FakeProxyResponse()

    monkeypatch.setattr(browser_workbench.urllib.request, "urlopen", fake_urlopen)
    handler = _FakeHandler(headers={"Cookie": "hermes_session=secret", "Accept": "text/html"})
    proxy_url = browser_workbench._browser_proxy_url_for_target("http://127.0.0.1:5173/app?x=1", session_id="bw_test", frame_id="frame1")

    assert routes.handle_get(handler, _parsed(proxy_url)) is True

    body = handler.wfile.getvalue().decode("utf-8")
    response_headers = {key.lower(): value for key, value in handler.response_headers}
    assert handler.status == 200
    assert captured["url"] == "http://127.0.0.1:5173/app?x=1"
    assert "Cookie" not in captured["headers"]
    assert "x-frame-options" not in response_headers
    assert "content-security-policy" not in response_headers
    assert "set-cookie" not in response_headers
    assert response_headers["x-hermes-browser-proxy-target"] == "http://127.0.0.1:5173/app?x=1"
    assert "hermes-browser-workbench-proxy-bridge" in body
    assert "hermes-browser-workbench-chii-target" in body
    assert "target_id=hermes_bw_bw_test" in body
    assert "hermes-devtools-agent" in body
    assert 'const sessionId = "bw_test"' in body
    assert 'const frameId = "frame1"' in body
    assert 'src="/browser-proxy/http://127.0.0.1:5173/assets/app.js"' in body
    assert 'href="/browser-proxy/http://127.0.0.1:5173/next"' in body


def test_browser_workbench_iframe_proxy_returns_diagnostic_page_on_fetch_error(monkeypatch):

    def fake_urlopen(request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(browser_workbench.urllib.request, "urlopen", fake_urlopen)
    handler = _FakeHandler()

    assert routes.handle_get(handler, _parsed("/browser-proxy/http://127.0.0.1:9")) is True

    body = handler.wfile.getvalue().decode("utf-8")
    assert handler.status == 502
    assert "This page could not be opened" in body
    assert "connection refused" in body


def test_browser_workbench_cdp_backend_uses_chromium_stream_for_loopback(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER", "cdp-browser")
    monkeypatch.setattr(browser_workbench, "_browser_binary_path", lambda environ=None: "/tmp/fake-browser")
    prepared_targets = []

    def _target_for_session(self, session_id, url):
        prepared_targets.append((session_id, url))
        return "ws://127.0.0.1/devtools/page/fake"

    monkeypatch.setattr(browser_workbench.CdpBrowserWorkbenchBackend, "_target_for_session", _target_for_session)

    handler = _FakeHandler({"url": "http://localhost:3000"})

    assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is True
    body = handler.json_body()

    assert handler.status == 200
    assert body["backend"] == "cdp-browser"
    assert body["renderer"] == "chromium-stream"
    assert prepared_targets and prepared_targets[0][1] == "http://localhost:3000"
    assert "bridge_url" not in body
    assert "screenshot_data_url" not in body


def test_browser_workbench_cdp_backend_uses_chromium_stream_for_public_urls(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER", "cdp-browser")
    monkeypatch.setattr(browser_workbench, "_browser_binary_path", lambda environ=None: "/tmp/fake-browser")
    prepared_targets = []

    def _target_for_session(self, session_id, url):
        prepared_targets.append((session_id, url))
        return "ws://127.0.0.1/devtools/page/fake"

    monkeypatch.setattr(browser_workbench.CdpBrowserWorkbenchBackend, "_target_for_session", _target_for_session)

    handler = _FakeHandler({"url": "https://example.com"})

    assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is True
    body = handler.json_body()

    assert handler.status == 200
    assert body["backend"] == "cdp-browser"
    assert body["renderer"] == "chromium-stream"
    assert prepared_targets and prepared_targets[0][1] == "https://example.com"
    assert "screenshot_data_url" not in body
    assert body["message"] == "Page opened."
    assert "bridge_url" not in body


def test_browser_workbench_cdp_navigation_drives_existing_chromium_target(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER", "cdp-browser")
    monkeypatch.setattr(browser_workbench, "_browser_binary_path", lambda environ=None: "/tmp/fake-browser")
    commands = []

    def _target_for_session(self, session_id, url):
        self._target_ws_urls.setdefault(session_id, "ws://127.0.0.1:9222/devtools/page/fake")
        return self._target_ws_urls[session_id]

    class _FakeCdpWebSocket:
        def __init__(self, websocket_url, *, timeout=5.0):
            self.websocket_url = websocket_url

        def command(self, method, params=None, *, timeout=5.0):
            commands.append((self.websocket_url, method, params or {}))
            return {}

        def close(self):
            pass

    monkeypatch.setattr(browser_workbench.CdpBrowserWorkbenchBackend, "_target_for_session", _target_for_session)
    monkeypatch.setattr(browser_workbench, "_CdpWebSocket", _FakeCdpWebSocket)

    create_handler = _FakeHandler({"url": "https://example.com"})
    assert routes.handle_post(create_handler, _parsed("/api/browser-workbench/session")) is True
    session_id = create_handler.json_body()["session_id"]

    navigate_handler = _FakeHandler({"url": "https://www.google.com"})
    assert routes.handle_post(navigate_handler, _parsed(f"/api/browser-workbench/session/{session_id}/navigate")) is True
    body = navigate_handler.json_body()

    assert navigate_handler.status == 200
    assert body["url"] == "https://www.google.com"
    assert body["renderer"] == "chromium-stream"
    assert ("ws://127.0.0.1:9222/devtools/page/fake", "Page.navigate", {"url": "https://www.google.com"}) in commands


def test_browser_workbench_cdp_devtools_uses_local_frontend_url():
    backend = browser_workbench.CdpBrowserWorkbenchBackend(browser_binary="/tmp/fake-browser")

    url = backend._local_devtools_url(
        "ws://127.0.0.1:9222/devtools/page/ABC",
        "https://chrome-devtools-frontend.appspot.com/serve_rev/@rev/inspector.html?ws=127.0.0.1:9222/devtools/page/ABC",
    )

    assert url == "http://127.0.0.1:9222/devtools/inspector.html?ws=127.0.0.1:9222/devtools/page/ABC"
    assert "chrome-devtools-frontend.appspot.com" not in url


def test_browser_workbench_setting_is_exposed_to_boot_settings():
    config.save_settings({"browser_workbench_enabled": True})
    handler = _FakeHandler()

    routes.handle_get(handler, _parsed("/api/settings"))

    body = handler.json_body()
    assert handler.status == 200
    assert body["browser_workbench_enabled"] is True


def test_browser_workbench_session_stub_is_csrf_protected(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: False)

    handler = _FakeHandler({"url": "http://localhost:3000"})

    assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is True
    assert handler.status == 403
    error = handler.json_body()["error"].lower()
    assert "csrf" in error or "cross-origin" in error or "token" in error


def test_browser_workbench_session_lifecycle_create_status_and_close(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    config.save_settings({"browser_workbench_enabled": True})

    handler = _FakeHandler({
        "url": "http://localhost:3000",
        "viewport": {"width": 777, "height": 555, "device_pixel_ratio": 2},
        "zoom": 1.25,
    })

    assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is True
    body = handler.json_body()
    assert handler.status == 200
    assert body["ok"] is True
    assert body["status"] == "ready"
    assert body["backend"] == "session-shell"
    assert body["url"] == "http://localhost:3000"
    assert body["renderer"] == "iframe-bridge"
    assert body["bridge_url"].startswith("/browser-proxy/http://localhost:3000?__hermes_bw_session=bw_")
    assert "__hermes_bw_frame=bw_" in body["bridge_url"]
    assert body["viewport"] == {"width": 777, "height": 555, "device_pixel_ratio": 2.0}
    assert body["zoom"] == 1.25
    assert body["title"] == ""
    assert body["favicon_url"] == ""
    assert body["session_id"].startswith("bw_")
    assert body["capabilities"]["session_lifecycle"] is True
    assert body["capabilities"]["navigation"] is True
    assert "cdp_endpoint" not in body
    assert "debugger_url" not in body

    session_id = body["session_id"]
    status_handler = _FakeHandler()
    assert routes.handle_get(status_handler, _parsed(f"/api/browser-workbench/session/{session_id}")) is True
    status_body = status_handler.json_body()
    assert status_handler.status == 200
    assert status_body["session_id"] == session_id
    assert status_body["status"] == "ready"
    assert "cdp_endpoint" not in status_body
    assert "debugger_url" not in status_body

    close_handler = _FakeHandler()
    assert routes.handle_delete(close_handler, _parsed(f"/api/browser-workbench/session/{session_id}")) is True
    close_body = close_handler.json_body()
    assert close_handler.status == 200
    assert close_body["ok"] is True
    assert close_body["session_id"] == session_id
    assert close_body["status"] == "closed"

    missing_handler = _FakeHandler()
    assert routes.handle_get(missing_handler, _parsed(f"/api/browser-workbench/session/{session_id}")) is True
    assert missing_handler.status == 404

    stale_attach_handler = _FakeHandler({"session_id": session_id, "url": "http://localhost:3001"})
    assert routes.handle_post(stale_attach_handler, _parsed("/api/browser-workbench/session")) is True
    stale_attach_body = stale_attach_handler.json_body()
    assert stale_attach_handler.status == 200
    assert stale_attach_body["ok"] is True
    assert stale_attach_body["session_id"].startswith("bw_")
    assert stale_attach_body["session_id"] != session_id
    assert stale_attach_body["url"] == "http://localhost:3001"


def test_browser_workbench_session_allows_local_and_internet_http_urls(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    config.save_settings({"browser_workbench_enabled": True})

    for raw_url, expected in (
        ("localhost:3000", "http://localhost:3000"),
        ("http://127.0.0.1:5173/path?q=1#section", "http://127.0.0.1:5173/path?q=1#section"),
        ("https://example.com/docs", "https://example.com/docs"),
    ):
        handler = _FakeHandler({"url": raw_url})

        assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is True
        body = handler.json_body()
        assert handler.status == 200
        assert body["ok"] is True
        assert body["url"] == expected
        assert body["capabilities"]["navigation"] is True


def test_browser_workbench_session_rejects_unsafe_initial_urls(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    config.save_settings({"browser_workbench_enabled": True})

    for raw_url in ("file:///etc/passwd", "chrome://version", "https://user:secret@example.com"):
        handler = _FakeHandler({"url": raw_url})

        assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is True
        body = handler.json_body()
        assert handler.status == 400
        assert body["ok"] is False
        assert "session_id" not in body
        assert "cdp_endpoint" not in body
        assert "debugger_url" not in body


def test_browser_workbench_session_navigation_routes_update_url_history(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    config.save_settings({"browser_workbench_enabled": True})

    create_handler = _FakeHandler({"url": "http://localhost:3000"})
    assert routes.handle_post(create_handler, _parsed("/api/browser-workbench/session")) is True
    session_id = create_handler.json_body()["session_id"]

    navigate_handler = _FakeHandler({
        "url": "https://example.com/docs",
        "viewport": {"width": 640, "height": 480, "device_pixel_ratio": 1.5},
        "zoom": 0.9,
    })
    assert routes.handle_post(navigate_handler, _parsed(f"/api/browser-workbench/session/{session_id}/navigate")) is True
    navigate_body = navigate_handler.json_body()
    assert navigate_handler.status == 200
    assert navigate_body["url"] == "https://example.com/docs"
    assert navigate_body["viewport"] == {"width": 640, "height": 480, "device_pixel_ratio": 1.5}
    assert navigate_body["zoom"] == 0.9
    assert navigate_body["can_go_back"] is True
    assert navigate_body["can_go_forward"] is False

    back_handler = _FakeHandler()
    assert routes.handle_post(back_handler, _parsed(f"/api/browser-workbench/session/{session_id}/back")) is True
    back_body = back_handler.json_body()
    assert back_handler.status == 200
    assert back_body["url"] == "http://localhost:3000"
    assert back_body["can_go_back"] is False
    assert back_body["can_go_forward"] is True

    forward_handler = _FakeHandler()
    assert routes.handle_post(forward_handler, _parsed(f"/api/browser-workbench/session/{session_id}/forward")) is True
    forward_body = forward_handler.json_body()
    assert forward_handler.status == 200
    assert forward_body["url"] == "https://example.com/docs"

    reload_handler = _FakeHandler({"viewport": {"width": 900, "height": 600}, "zoom": 1.1})
    assert routes.handle_post(reload_handler, _parsed(f"/api/browser-workbench/session/{session_id}/reload")) is True
    reload_body = reload_handler.json_body()
    assert reload_handler.status == 200
    assert reload_body["url"] == "https://example.com/docs"
    assert reload_body["viewport"]["width"] == 900
    assert reload_body["viewport"]["height"] == 600
    assert reload_body["zoom"] == 1.1

    hard_reload_handler = _FakeHandler({"viewport": {"width": 901, "height": 601}, "zoom": 1.2})
    assert routes.handle_post(hard_reload_handler, _parsed(f"/api/browser-workbench/session/{session_id}/hard-reload")) is True
    hard_reload_body = hard_reload_handler.json_body()
    assert hard_reload_handler.status == 200
    assert hard_reload_body["url"] == "https://example.com/docs"
    assert hard_reload_body["viewport"]["width"] == 901
    assert hard_reload_body["zoom"] == 1.2

    clear_history_handler = _FakeHandler()
    assert routes.handle_post(clear_history_handler, _parsed(f"/api/browser-workbench/session/{session_id}/clear-history")) is True
    clear_history_body = clear_history_handler.json_body()
    assert clear_history_handler.status == 200
    assert clear_history_body["can_go_back"] is False
    assert clear_history_body["can_go_forward"] is False

    for action in ("clear-cookies", "clear-cache"):
        action_handler = _FakeHandler()
        assert routes.handle_post(action_handler, _parsed(f"/api/browser-workbench/session/{session_id}/{action}")) is True
        assert action_handler.status == 200
        assert action_handler.json_body()["session_id"] == session_id

    monkeypatch.setattr(browser_workbench, "_chii_devtools_url", lambda sid: f"http://127.0.0.1:8080/front_end/chii_app.html?target={sid}")
    devtools_handler = _FakeHandler({"mode": "panel"})
    assert routes.handle_post(devtools_handler, _parsed(f"/api/browser-workbench/session/{session_id}/devtools")) is True
    devtools_body = devtools_handler.json_body()
    assert devtools_handler.status == 200
    assert devtools_body["devtools_url"].endswith(f"target={session_id}")
    assert devtools_body["chii_devtools"]["target_id"] == f"hermes_bw_{session_id}"
    assert devtools_body["chii_devtools"]["docked"] is True
    assert devtools_body["chii_devtools"]["popout_supported"] is True


def test_browser_workbench_iframe_screenshot_actions_keep_viewport_and_full_page_separate():
    js = open("static/browser_workbench.js", encoding="utf-8").read()
    api_py = open("api/browser_workbench.py", encoding="utf-8").read()
    index = open("static/index.html", encoding="utf-8").read()

    # UX labels are intentionally distinct: viewport, visible area, and entire scrollable page.
    assert "Take Screenshot" in index
    assert "Capture Area Screenshot" in index
    assert "Take Full Page Screenshot" in index
    assert 'data-browser-action="take-screenshot"' in index
    assert 'data-browser-action="capture-area-screenshot"' in index
    assert 'data-browser-action="take-full-page-screenshot"' in index

    # Default iframe screenshot path must request the visible viewport, never full-page.
    default_call = "return await attachBrowserWorkbenchIframeScreenshot(active,{clip:clip||null,statusToken})"
    default_screenshot_start = js.index("async function attachBrowserWorkbenchScreenshot")
    default_screenshot_body = js[default_screenshot_start:js.index("async function startBrowserWorkbenchAreaCapture", default_screenshot_start)]
    assert default_call in default_screenshot_body
    assert "fullPage:true" not in default_screenshot_body
    assert "return await attachBrowserWorkbenchIframeScreenshot(active,{fullPage:true,statusToken})" in js
    assert "const mode=opts.fullPage===true?'full-page':'viewport'" in js
    assert "mode:String(opts.mode||'viewport')" in js
    assert "if(action==='take-screenshot')return await attachBrowserWorkbenchScreenshot();" in js
    assert "if(action==='take-full-page-screenshot')return await attachBrowserWorkbenchIframeFullPageScreenshot();" in js

    # The injected bridge computes viewport dimensions from innerWidth/innerHeight and only uses
    # document scrollHeight/scrollWidth when the explicit full-page mode is requested.
    assert "const viewportWidth = Math.max(1, Math.round(window.innerWidth || document.documentElement.clientWidth || 1));" in api_py
    assert "const viewportHeight = Math.max(1, Math.round(window.innerHeight || document.documentElement.clientHeight || 1));" in api_py
    assert "const docWidth = Math.max(viewportWidth, document.documentElement.scrollWidth || 0, document.body && document.body.scrollWidth || 0);" in api_py
    assert "const docHeight = Math.max(viewportHeight, document.documentElement.scrollHeight || 0, document.body && document.body.scrollHeight || 0);" in api_py
    assert "const width = mode === 'full-page' ? docWidth : viewportWidth;" in api_py
    assert "const height = mode === 'full-page' ? docHeight : viewportHeight;" in api_py
    assert "const scrollX = mode === 'full-page' ? 0 : originalScrollX;" in api_py
    assert "const scrollY = mode === 'full-page' ? 0 : originalScrollY;" in api_py
    assert "window.scrollTo(originalScrollX, originalScrollY)" in api_py

    # Full-page must be an explicit mode while both capture modes share the settled response.
    assert "String(request.mode || '') === 'full-page' ? 'full-page' : 'viewport'" in api_py
    assert "message:'Screenshot captured.'" in api_py



def test_browser_workbench_static_shell_is_wired_default_off_and_safe():
    index = open("static/index.html", encoding="utf-8").read()
    js = open("static/browser_workbench.js", encoding="utf-8").read()
    css = open("static/style.css", encoding="utf-8").read()
    ui_js = open("static/ui.js", encoding="utf-8").read()
    desktop_main = open("desktop/src/main/index.cjs", encoding="utf-8").read()

    assert 'id="workbenchTabBrowser"' not in index
    assert 'id="browserWorkbenchTabs"' in index
    assert 'id="workbenchOpenBrowser"' in index
    assert 'id="mainBrowser"' in index
    assert 'id="browserWorkbenchPing"' in index
    assert 'id="browserWorkbenchZoomOut"' not in index
    assert 'id="browserWorkbenchZoomInput"' not in index
    assert 'value="100"' in index
    assert 'value="100%"' not in index
    assert 'browser-workbench-toolbar-zoom' not in index
    assert 'browser-workbench-toolbar-zoom-value' not in index
    assert 'id="browserWorkbenchReload"' in index
    assert 'browser-workbench-nav--reload' in index
    assert 'data-load-status="idle"' in index
    assert 'browser-workbench-reload-icon' in index
    assert 'id="browserWorkbenchUrlSuggestions"' in index
    assert 'aria-autocomplete="list"' in index
    assert 'id="browserWorkbenchMenuButton"' in index
    assert 'id="browserWorkbenchMenu"' in index
    assert 'Attach Screenshot to Prompt' not in index
    assert 'Attach Area Screenshot to Prompt' not in index
    assert 'Take Screenshot' in index
    assert 'Capture Area Screenshot' in index
    assert 'Open DevTools Panel' in index
    assert 'Pop Out DevTools' in index
    assert 'browser-workbench-menu-section' in index
    assert 'browser-workbench-menu-label' in index
    assert 'Clear Cookies' in index
    assert 'id="btnBrowserWorkbench"' not in index
    assert 'hidden data-browser-workbench-launcher' not in index
    assert 'data-browser-workbench-opener' in index
    assert 'hidden data-browser-workbench-opener' not in index
    assert 'Open new Browser tab (⇧⌘B)' in index
    assert 'Browse pages without leaving your chat.' in index
    assert 'https://example.com or http://localhost:3000' in index
    assert 'static/browser_workbench.js?v=__WEBUI_VERSION__' in index
    assert 'id="btnBrowserInspector"' not in index
    assert 'browser-inspector' not in index
    assert 'static/browser_inspector.js' not in index
    assert 'data-panel="browser"' not in index
    assert "function openBrowserWorkbenchTab" in js
    assert "function closeBrowserWorkbenchTab" in js
    assert "function pingBrowserWorkbenchSelection" in js
    assert "function navigateBrowserWorkbenchToUrl" in js
    assert "function navigateBrowserWorkbenchHistory" in js
    assert "function setBrowserWorkbenchLoadStatus" in js
    assert "function updateBrowserWorkbenchReloadButton" in js
    assert "function handleBrowserWorkbenchReloadButtonClick" in js
    assert "function stopBrowserWorkbenchLoading" in js
    assert "browserWorkbenchUrlInputEditingTabId" in js
    assert "function isBrowserWorkbenchUrlInputEditing" in js
    assert "if(!isBrowserWorkbenchUrlInputEditing(active))urlInput.value=active?active.url||'':''" in js
    assert "navigateBrowserWorkbenchToUrl(undefined,requested)" in js
    assert "target.viewportMessage='Enter an address to open a page.'" in js
    assert "/stop-loading" in js
    assert "icon.textContent=loading?'✕':'↻'" in js
    assert "'Reload failed':'Reload'" in js
    assert "const stopEnabled=workbenchCapabilities.stop_loading===true" in js
    assert "reloadButton.disabled=!navigationEnabled||(loading&&!stopEnabled)" in js
    assert "Stop is unavailable right now" in js
    assert "BROWSER_WORKBENCH_RELOAD_SUCCESS_MS=700" in js
    assert "frame.addEventListener('load'" in js
    assert "scheduleBrowserWorkbenchLoadStatusPoll(target)" in js
    assert "function restoreBrowserWorkbenchTabs" in js
    assert "function persistBrowserWorkbenchTabs" in js
    assert "function renderBrowserWorkbenchFrame" in js
    assert "function renderBrowserWorkbenchScreenshot" not in js
    assert "function renderBrowserWorkbenchDevtools" in js
    assert "function currentBrowserWorkbenchViewport" in js
    assert "function canInteractWithBrowserWorkbenchViewport(action)" in js
    assert "(!selecting||action==='wheel')" in js
    assert "canInteractWithBrowserWorkbenchViewport('wheel')" in js
    assert "clearBrowserWorkbenchOverlay('hover')" in js
    assert "function browserWorkbenchMenuOverlapRect" not in js
    assert "function browserWorkbenchNativeOverlayOverlapRect" not in js
    assert "function browserWorkbenchNativeOverlayHideReason" not in js
    assert "URL history suggestions are open, so the Electron native page surface is temporarily hidden" not in js
    assert "The Browser actions menu is open, so the Electron native page surface is temporarily hidden" not in js
    assert "return browserWorkbenchMenuOverlapRect(viewportRect);" not in js
    assert "browser-workbench-native-overlay-note" not in js
    assert "!overlayOverlap&&!!tab&&!!tab.sessionId&&tab.renderer==='electron-native'" not in js
    assert "function syncBrowserWorkbenchNativeBoundsAfterMenuToggle" not in js
    assert "function positionBrowserWorkbenchMenu" in js
    assert "function openBrowserWorkbenchMenu" in js
    assert "window.addEventListener('resize',()=>positionBrowserWorkbenchMenu())" in js
    assert "window.addEventListener('scroll',()=>positionBrowserWorkbenchMenu(),true)" in js
    assert "bridge.showActionsMenu" in js
    assert "function browserWorkbenchNativeActionsMenuSupported" in js
    assert "syncBrowserWorkbenchNativeActionsMenu('show')" in js
    assert "syncBrowserWorkbenchNativeActionsMenu('update')" in js
    assert "syncBrowserWorkbenchNativeActionsMenu('hide')" in js
    assert "onActionsMenuAction" in js
    assert "function ensureBrowserWorkbenchUrlSuggestionsPortal" in js
    assert "document.body.appendChild(urlSuggestionsEl)" in js
    assert "function positionBrowserWorkbenchUrlSuggestionsPortal" in js
    assert "function browserWorkbenchNativeUrlSuggestionsSupported" in js
    assert "syncBrowserWorkbenchNativeUrlSuggestions('show')" in js
    assert "syncBrowserWorkbenchNativeUrlSuggestions('update')" in js
    assert "syncBrowserWorkbenchNativeUrlSuggestions('hide')" in js
    assert "onUrlSuggestionAction" in js
    assert "browser-workbench:show-actions-menu" in desktop_main
    assert "browser-workbench:update-actions-menu" in desktop_main
    assert "browser-workbench:hide-actions-menu" in desktop_main
    assert "function showActionsMenuOverlay" in desktop_main
    assert "browser-workbench:actions-menu-action" in desktop_main
    assert "browser-workbench:show-url-suggestions" in desktop_main
    assert "browser-workbench:update-url-suggestions" in desktop_main
    assert "browser-workbench:hide-url-suggestions" in desktop_main
    assert "function showUrlSuggestionOverlay" in desktop_main
    assert "function makeFloatingOverlayViewTransparent" in desktop_main
    assert "view.setBackgroundColor('#00000000')" in desktop_main
    assert "function resizeUrlSuggestionOverlayToContent" in desktop_main
    assert "if (actionsMenuOverlay && actionsMenuOverlay.visible) addActionsMenuOverlayToWindow();" in desktop_main
    assert "new WebContentsView" in desktop_main
    assert "browser-workbench:url-suggestion-action" in desktop_main
    assert "browser-workbench:start-area-capture" in desktop_main
    assert "function captureAreaInNativeView" in desktop_main
    assert "record.view.webContents.capturePage(rect)" in desktop_main
    assert "Menu.buildFromTemplate" not in desktop_main
    assert "function normalizeZoom" in desktop_main
    assert "function applyRecordPayload" in desktop_main
    assert "function setRecordLoadStatus" in desktop_main
    assert "function markRecordLoading" in desktop_main
    assert "function markRecordReady" in desktop_main
    assert "LOAD_STATUS_TIMEOUT_MS = 45000" in desktop_main
    assert "LOAD_STATUS_MAIN_FRAME_SETTLE_MS = 250" in desktop_main
    assert "function markRecordReadyIfMainFrameSettled" in desktop_main
    assert "function scheduleRecordReady" in desktop_main
    assert "markRecordReady(record, 'document-ready-settled')" in desktop_main
    assert "typeof wc.isWaitingForResponse === 'function'" in desktop_main
    assert "executeJavaScript('document.readyState', true)" in desktop_main
    assert "did-start-navigation" in desktop_main
    assert "markRecordLoading(record, 'did-start-navigation'" in desktop_main
    assert "did-start-loading" in desktop_main
    assert "markRecordReadyIfMainFrameSettled(record, 'did-stop-loading')" in desktop_main
    assert "scheduleRecordReady(record, 'dom-ready-settled'" in desktop_main
    assert "scheduleRecordReady(record, 'did-navigate-settled'" in desktop_main
    assert "did-finish-load" in desktop_main
    assert "markRecordReady(record, 'did-finish-load')" in desktop_main
    assert "did-frame-finish-load" in desktop_main
    assert "markRecordReady(record, 'did-frame-finish-load')" in desktop_main
    assert "did-fail-load" in desktop_main
    assert "did-fail-provisional-load" in desktop_main
    assert "did-stop-loading" in desktop_main
    assert "dom-ready" in desktop_main
    assert "function refreshRecordLoadStatus" in desktop_main
    assert "typeof wc.isLoadingMainFrame === 'function'" in desktop_main
    assert "Page load timed out waiting for the main frame" in desktop_main
    assert "markRecordReady(record, 'document-ready-watchdog')" in desktop_main
    assert "function safeFaviconUrl" in desktop_main
    assert "page-favicon-updated" in desktop_main
    assert "favicon_url: record.faviconUrl || ''" in desktop_main
    assert "title: record.loadStatus === 'loading' ? '' : wc.getTitle() || record.title" in desktop_main
    assert "function renderBrowserWorkbenchOverlay" in js
    assert "BROWSER_WORKBENCH_SELECTION_LABEL_SAFE_PADDING=8" in js
    assert "function positionBrowserWorkbenchOverlayLabel" in js
    assert "const aboveTop=targetTop-labelHeight-gap" in js
    assert "label.dataset.placement=placement" in js
    assert "iframe.closest?iframe.closest('.browser-workbench-frame-wrap')" in js
    assert "const positionSelectionLabel = (label, targetRect) =>" in desktop_main
    assert "const safe = 8" in desktop_main
    assert "const belowTop = targetBottom + gap" in desktop_main
    assert "label.dataset.placement = placement" in desktop_main
    assert "const elementLabel = (selection) =>" in desktop_main
    assert "const renderElementLabel = (target, selection) =>" in desktop_main
    assert "display:inline-flex;align-items:center;gap:4px" in desktop_main
    assert "tagPart.textContent = tag" in desktop_main
    assert "const suffix = ' · ' + tag" in desktop_main
    assert "componentPart.style.cssText = 'flex:1 1 auto;min-width:0;overflow:hidden" in desktop_main
    assert "separatorPart.textContent = '·'" in desktop_main
    assert "renderElementLabel(state.label, selection)" in desktop_main
    assert "const frameMetaFor = (frame, sameOrigin)" in desktop_main
    assert "const inspectInDocument = (doc, x, y, topPoint, frames, depth)" in desktop_main
    assert "frame.contentDocument || frame.contentWindow && frame.contentWindow.document" in desktop_main
    assert "nested.rect = addRectOffset(nested.rect, iframeRect.left, iframeRect.top)" in desktop_main
    assert "component = 'iframe · cross-origin'" in desktop_main
    assert "attachFrameListeners()" in desktop_main
    assert "const scrollTargetFor = (doc, x, y, depth)" in desktop_main
    assert "overlay.addEventListener('wheel', state.wheel, { capture: true, passive: false })" in desktop_main
    assert "window.addEventListener('scroll', state.scroll, { capture: true, passive: true })" in desktop_main
    assert "window.removeEventListener('scroll', state.scroll, true)" in desktop_main
    assert "state.scheduleHover(point)" in desktop_main
    assert "setTimeout(() => state.scheduleHover(point), 80)" in desktop_main
    assert "load_status: record.loadStatus || 'idle'" in desktop_main
    assert "setRecordLoadStatus(record, 'idle')" in desktop_main
    assert "const alreadyAtUrl = currentUrl === nextUrl || (record.loadStatus === 'loading' && record.loadRequestedUrl === nextUrl)" not in desktop_main
    assert "startRecordUrlLoad(record, nextUrl, 'load-url')" in desktop_main
    assert "record.view.webContents.loadURL(nextUrl).catch" in desktop_main
    assert "refreshLoadStatus: false" in desktop_main
    assert "applyRecordPayload(record, await readJson(req)); reloadRecord(record)" in desktop_main
    assert "record.view.webContents.stop(); setRecordLoadStatus(record, 'idle')" in desktop_main
    assert "function removeNativeViewFromWindow" in desktop_main
    assert "record.view.setVisible(false)" in desktop_main
    assert "record.view.setVisible(true)" in desktop_main
    assert "mainWindow.contentView.removeChildView(record.view)" in desktop_main
    assert "function handleBrowserWorkbenchMenuAction" in js
    assert ".browser-workbench-menu{position:fixed" in css
    assert ".browser-workbench-menu-section+.browser-workbench-menu-section" in css
    assert ".browser-workbench-menu-item" in css
    assert 'id="browserWorkbenchMenuZoomInput"' in index
    assert "menuZoomInput=document.getElementById('browserWorkbenchMenuZoomInput')" in js
    assert "wireBrowserWorkbenchZoomInput(menuZoomInput)" in js
    assert "document.getElementById('browserWorkbenchZoomInput')" not in js
    assert "document.getElementById('browserWorkbenchZoomOut')" not in js
    assert "document.getElementById('browserWorkbenchZoomIn')" not in js
    assert ".browser-workbench-menu-zoom-row" in css
    assert ".browser-workbench-menu-zoom-value" in css
    assert ".browser-workbench-toolbar-zoom" not in css
    assert "function browserWorkbenchMenuActionKeepsOpen" in js
    assert "action==='zoom-out'||action==='zoom-in'" in js
    assert "const zoomEnabled=!!active" in js
    assert "menuZoomInput.value=String(zoom)" in js
    assert "function applyBrowserWorkbenchSurfaceZoom" in js
    assert "scheduleBrowserWorkbenchNativeBoundsSync()" in js
    assert "runBrowserWorkbenchSessionAction('reload',{zoom:active.zoom})" not in js
    assert "if(active.sessionId)await runBrowserWorkbenchSessionAction('reload'" not in js
    assert "updateBrowserWorkbenchZoomLabel({force:true})" in js
    assert "browserWorkbenchZoomLabel" not in js
    assert "if(!browserWorkbenchMenuActionKeepsOpen(action))closeBrowserWorkbenchMenu()" in js
    assert "event.target.closest('[data-browser-action]'))event.preventDefault()" in js
    assert "function attachBrowserWorkbenchScreenshot" in js
    assert "function attachBrowserWorkbenchIframeFullPageScreenshot" in js
    assert "Take Full Page Screenshot" in index
    assert 'data-browser-action="take-full-page-screenshot"' in index
    assert "startBrowserWorkbenchAreaCapture" in js
    assert "Open a Chromium Browser Workbench stream before capturing an area" not in js
    assert "bridge.startAreaCapture" in js
    assert "function attachmentFromBrowserWorkbenchPayload" in js
    assert "function dataUrlToBrowserWorkbenchFile" not in js
    assert "downloadDataUrl" not in js
    assert "document.createElement('a')" not in js
    assert "window.attachFilesToPrompt([file])" in js
    assert "fileInput" not in js
    assert "attached.`,{kind:'temporary'" in js
    assert "function attachFilesToPrompt" in ui_js
    assert "window.attachFilesToPrompt=attachFilesToPrompt" in ui_js
    assert "addFiles(list)" in ui_js
    assert "function openBrowserWorkbenchDevtools" in js
    assert "function browserWorkbenchRendererCapabilities" in js
    assert "function updateBrowserWorkbenchActionMenuCapabilities" in js
    assert ".browser-workbench-viewport.area-capturing .browser-workbench-frame{pointer-events:none;}" in css
    assert "DevTools opened in a new window." in js
    assert "Capture the visible page." in js
    assert "Capture a selected area." in js
    assert "Capture the full page." in js
    assert "Screenshots are not ready yet." in js
    assert "Area capture is not ready yet." in js
    assert "Full-page capture is not ready yet." in js
    assert "Iframe-proxy area screenshot is planned after viewport DOM capture is stable." not in js
    assert "Capturing screenshot…" in js
    assert "Capturing selected area…" in js
    assert "Capturing full page…" in js
    assert "function requestBrowserWorkbenchIframeScreenshot" in js
    assert "function settleBrowserWorkbenchIframeCapture" in js
    assert "function browserWorkbenchIframeCropFromSurfaceClip" in js
    assert "function browserWorkbenchIframeViewportMetrics" in js
    assert "function browserWorkbenchCropIframeAttachment" in js
    assert "surfaceRect.left+(Number(clip.x)||0)*surfaceRect.width/Math.max(1,Number(viewport.width)||surfaceRect.width)" in js
    assert "documentRect:{x:x+metrics.scrollX,y:y+metrics.scrollY,width,height,scrollX:metrics.scrollX,scrollY:metrics.scrollY}" in js
    assert "const scaleX=imageWidth/Math.max(1,Number(crop.iframeViewport.width)||imageWidth)" in js
    assert "ctx.drawImage(bitmap,sx,sy,sw,sh,0,0,sw,sh)" in js
    assert "BROWSER_WORKBENCH_IFRAME_AREA_CAPTURE_FILENAME='browser-workbench-iframe-area-screenshot.png'" in js
    assert "BROWSER_WORKBENCH_IFRAME_FULL_CAPTURE_FILENAME='browser-workbench-iframe-full-page-screenshot.png'" in js
    assert "BROWSER_WORKBENCH_IFRAME_FULL_CAPTURE_MAX_HEIGHT=12000" in js
    assert "BROWSER_WORKBENCH_IFRAME_FULL_CAPTURE_MAX_PIXELS=25000000" in js
    assert "captureAreaScreenshot:hasSession&&captureReady" in js
    assert "takeFullPageScreenshot:hasSession&&captureReady" in js
    assert "fullPageScreenshotVisible:true" in js
    assert "if(action==='take-full-page-screenshot')return await attachBrowserWorkbenchIframeFullPageScreenshot()" in js
    assert "active.renderer==='iframe-bridge'" in js
    assert "source:'hermes-browser-workbench-parent'" in js
    assert "type:'hermes:capture-screenshot'" in js
    assert "devtoolsPanelLabel:'Open DevTools'" in js
    assert "function syncBrowserWorkbenchIframeDevtoolsLite" in js
    assert "function ensureBrowserWorkbenchSplitViewPreservingSurface" in js
    assert "viewportEl.classList.add('has-devtools-docked')" in js
    assert "setPointerCapture(pointerId)" in js
    assert "browser-workbench-resizing-devtools" in js
    assert "window.addEventListener('pointermove',onMove,{capture:true,passive:false})" in js
    assert "pointer-events:none!important" in css
    assert "touch-action:none" in css
    assert "refreshBrowserWorkbenchDevtoolsLitePanel(tab)" in js
    assert "window.open(active.devtoolsUrl,'_blank','noopener,noreferrer')" in js
    assert "'DevTools'" in js
    assert "handleBrowserWorkbenchDevtoolsAgentMessage" in js
    assert "source!=='hermes-devtools-agent'" in js
    assert "active.renderer==='iframe-bridge'" in js
    assert "DevTools opened." in js
    assert "data-hermes-browser-workbench-iframe-selection-overlay" in open("api/browser_workbench.py", encoding="utf-8").read()
    api_py = open("api/browser_workbench.py", encoding="utf-8").read()
    assert "stopSelectionEvent(event)" in api_py
    assert "handleCaptureRequest" in api_py
    assert "captureIframeViewport" in api_py
    assert "fullCaptureMaxHeight = 12000" in api_py
    assert "fullCaptureMaxPixels = 25000000" in api_py
    assert "Iframe full-page capture is too tall" in api_py
    assert "Iframe full-page capture is too large" in api_py
    assert "window.scrollTo(originalScrollX, originalScrollY)" in api_py
    assert "mode === 'full-page' ? 'iframe-dom-full-page-capture' : 'iframe-dom-capture'" in api_py
    assert "Iframe DOM capture currently supports viewport and full-page screenshots only." in api_py
    assert "message:'Screenshot captured.'" in api_py
    assert "foreignObject" in api_py
    assert "one-time iframe DOM capture" in api_py
    assert "capture-screenshot-result" in api_py
    assert "renderBrowserWorkbenchChiiDevtools(target,panel)" in js
    assert "browser-workbench-chii-devtools-frame" in js
    assert ".browser-workbench-split-wrap" in css
    assert ".browser-workbench-devtools-region{flex:0 0 var(--browser-workbench-devtools-width,420px)" in css
    assert ".browser-workbench-devtools-region--docked" in css
    assert ".browser-workbench-devtools-resizer--docked" in css
    assert "active.devtoolsOpen=true" in js
    assert "browser-workbench-devtools-frame" in js
    assert "DevTools opened in the panel." in js
    assert "If the Electron page surface turns white while DevTools is docked" not in js
    assert "function applyBrowserWorkbenchAvailability" in js
    assert "window.applyBrowserWorkbenchAvailability" in js
    assert "let workbenchUiEnabled=false" in js
    assert "function isBrowserWorkbenchSessionMissingError" in js
    assert "function clearBrowserWorkbenchStaleSession" in js
    assert "function idleBrowserWorkbenchBlankTab" in js
    assert "const startsBlank=browserWorkbenchIsBlankUrl(requestedUrl)" in js
    assert "return idleBrowserWorkbenchBlankTab(target)" in js
    assert "message:'Ready for an address.'" in js
    assert "if(browserWorkbenchIsBlankUrl(requested))" in js
    assert "requestJSON(sessionStatusUrl(closingId),{method:'DELETE'}).catch(()=>{})" in js
    assert "Browser Workbench session expired. Recreating this Browser tab" not in js
    assert "if(isBrowserWorkbenchSessionMissingError(err))" in js
    assert "return startBrowserWorkbenchSession(target.id)" in js
    assert "function maybeStartBrowserWorkbenchInitialLoadOnActivation" in js
    assert "function shouldStartBrowserWorkbenchInitialLoadOnActivation" in js
    assert "if(normalizeBrowserWorkbenchLoadStatus(tab.loadStatus)!=='idle')return false" in js
    assert "if(tab.sessionId)return false" in js
    assert "return tab.hasStartedLoad!==true" in js
    assert "if(!shouldStartBrowserWorkbenchInitialLoadOnActivation(target))return null" in js
    assert "await maybeStartBrowserWorkbenchInitialLoadOnActivation(target.id)" in js
    assert "ensureBrowserWorkbenchSessionOnOpen" in js
    assert "switchPanel('browser')" in js
    assert "switchPanel('chat')" in js
    assert "toggleBrowserWorkbenchSelectionMode" in js
    assert "handleBrowserWorkbenchShortcut" in js
    assert "event.metaKey&&event.shiftKey" in js
    assert "BROWSER_WORKBENCH_TAB_ID_PREFIX='workbench-browser-tab-'" in js
    assert "const WORKBENCH_STORAGE_KEY='hermes-browser-workbench-tabs:v1'" in js
    assert "const WORKBENCH_HISTORY_STORAGE_KEY='hermes-browser-workbench-history:v1'" in js
    assert "const BROWSER_WORKBENCH_SUGGESTION_LIMIT=5" in js
    assert "BROWSER_WORKBENCH_RESTORED_OPEN_DELAY_MS" in js
    assert "function ensureDesktopBrowserBridgeRegistered" in js
    assert "await prepareDesktopBrowserBridge()" in js
    assert "hermes-desktop-browser-bridge-ready" in js
    assert "setTimeout(()=>{" in js
    assert "activateBrowserWorkbenchTab(tabId,{switchPanel:false})" in js
    assert "setTimeout(()=>openBrowserWorkbenchTab(tabId),BROWSER_WORKBENCH_RESTORED_OPEN_DELAY_MS)" not in js
    assert "function recordBrowserWorkbenchHistory" in js
    assert "function browserWorkbenchUrlHistorySuggestions" in js
    assert ".slice(0,BROWSER_WORKBENCH_SUGGESTION_LIMIT)" in js
    assert "let desktopBrowserBridgeRegisteredPayload=null" in js
    assert "if(desktopBrowserBridgeRegisteredPayload)return Promise.resolve(desktopBrowserBridgeRegisteredPayload)" in js
    assert "desktopBrowserBridgeRegisteredPayload=payload||null" in js
    assert "function renderBrowserWorkbenchUrlSuggestions" in js
    assert "function setBrowserWorkbenchUrlSuggestionActive" in js
    assert "function moveBrowserWorkbenchUrlSuggestionSelection" in js
    assert "setBrowserWorkbenchUrlSuggestionActive(-1,{syncNative:false})" in js
    assert "browserWorkbenchUrlSuggestionActiveIndex=Number.isFinite(requested)&&requested>=0&&requested<count?requested:-1" in js
    assert "if(next<0||next>=count)next=-1" in js
    assert "if(browserWorkbenchUrlSuggestionsVisible()&&acceptBrowserWorkbenchUrlSuggestion(true))return" in js
    assert "browserWorkbenchUrlSuggestionsOpen" in js
    assert "function browserWorkbenchUrlSuggestionsOverlapRect" not in js
    assert "function browserWorkbenchNativeOverlayOverlapRect" not in js
    assert "return browserWorkbenchMenuOverlapRect(viewportRect)||browserWorkbenchUrlSuggestionsOverlapRect(viewportRect);" not in js
    assert "const overlayOverlap=visible!==false&&tab&&tab.renderer==='electron-native'?browserWorkbenchNativeOverlayOverlapRect(rect):null" not in js
    assert "const isVisible=visible!==false&&!overlayOverlap" not in js
    assert "const isVisible=visible!==false&&!!tab&&!!tab.sessionId&&tab.renderer==='electron-native'" in js
    assert "urlSuggestionsEl.hidden=useNativeOverlay" in js
    assert "acceptBrowserWorkbenchUrlSuggestion(true)" in js
    assert "acceptBrowserWorkbenchUrlSuggestion(false)" in js
    assert "event.key==='ArrowDown'" in js
    assert "event.key==='ArrowUp'" in js
    assert "event.key==='Tab'&&browserWorkbenchUrlSuggestionsVisible()" in js
    assert "recordBrowserWorkbenchHistory(historyUrl,target.title" in js
    assert "function syncBrowserWorkbenchTabLocation" in js
    assert "syncBrowserWorkbenchTabLocation(active,nextUrl,{committed:true,updateRequested:true,clientNavigation:true})" in js
    assert "function applyBrowserWorkbenchNativeNavigationUpdate" in js
    assert "browserWorkbenchNativeNavigationWired" in js
    assert "onNavigation" in js
    api_py = open("api/browser_workbench.py", encoding="utf-8").read()
    desktop_main = open("desktop/src/main/index.cjs", encoding="utf-8").read()
    desktop_preload = open("desktop/src/preload/index.cjs", encoding="utf-8").read()
    assert "const currentTargetUrl = () =>" in api_py
    assert "history[name] = function(...args)" in api_py
    assert "window.addEventListener('popstate', scheduleRouteMetadata)" in api_py
    assert "window.addEventListener('hashchange', scheduleRouteMetadata)" in api_py
    assert "did-navigate-in-page" in desktop_main
    assert "render-process-gone" in desktop_main
    assert "reason === 'crashed' || reason === 'oom' || reason === 'launch-failed'" in desktop_main
    assert "appIsQuitting" in desktop_main
    assert "requestedActiveIndex" in desktop_main
    assert "? requestedActiveIndex : -1" in desktop_main
    assert "sendBrowserNavigationUpdate(record, 'did-navigate-in-page')" in desktop_main
    assert "browser-workbench:navigation" in desktop_main
    assert "onNavigation(callback)" in desktop_preload
    assert "ipcRenderer.on('browser-workbench:navigation', listener)" in desktop_preload
    assert "closeBrowserWorkbenchUrlSuggestions()" in js
    assert ".browser-workbench-url-suggestions" in css
    assert ".browser-workbench-url-suggestions{position:fixed" in css
    assert ".browser-workbench-url-suggestions--portal" in css
    assert "top:calc(100% + 6px)" not in css
    assert ".browser-workbench-url-suggestion.is-active" in css
    assert "const workbenchTabs=new Map()" in js
    assert "function browserWorkbenchDisplayLabel" in js
    assert "label:'Browser'" in js
    assert "browserWorkbenchSafeFaviconUrl" in js
    assert "new Image(14,14)" in js
    assert "browserWorkbenchDisplayLabel(active)" in js
    assert "favicon_url:tab.faviconUrl||''" in js
    assert "activeBrowserWorkbenchTabId" in js
    assert "data-browser-workbench-tab-id" in js
    assert "function reorderBrowserWorkbenchTab" in js
    assert "el.draggable=true" in js
    assert "handleBrowserWorkbenchTabDrop" in js
    assert "tabsEl.appendChild(tab.tabEl)" in js
    assert ".workbench-tab-status[data-state=\"idle\"]" in css
    assert ".workbench-tab-status[data-state=\"success\"],.workbench-tab-status[data-state=\"ready\"]" in css
    assert ".workbench-tab-status[data-state=\"loading\"]" in css
    assert ".workbench-tab-status[data-state=\"error\"],.workbench-tab-status[data-state=\"warning\"]" in css
    assert ".workbench-tab-browser{flex:0 1 auto;max-width:min(240px,34vw);}" in css
    assert ".workbench-tab-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" in css
    assert "function getBrowserWorkbenchDebugState" in js
    assert "function stashBrowserWorkbenchSurface" in js
    assert "function removeBrowserWorkbenchStoredSurface" in js
    assert "frameWrap.hidden=true" in js
    assert ".browser-workbench-frame-wrap[hidden]{display:none!important;}" in css
    assert "data-browser-workbench-tab-id" in js
    assert "if(tab.surfaceNode){" in js
    assert "lastComposerSelection" in js
    assert "workbenchUiEnabled=data.ui_enabled===true" in js
    assert "if(!workbenchUiEnabled)" in js
    assert "inspectBrowserWorkbenchAt" in js
    assert "inspectBrowserWorkbenchPoint" in js
    assert "canInteractWithBrowserWorkbenchViewport" in js
    assert "sendBrowserWorkbenchInteraction" in js
    assert "handleBrowserWorkbenchViewportClick" in js
    assert "handleBrowserWorkbenchViewportWheel" in js
    assert "handleBrowserWorkbenchViewportKeydown" in js
    assert "BROWSER_WORKBENCH_HOVER_INSPECT_DELAY_MS" in js
    assert "BROWSER_WORKBENCH_CLICK_DELAY_MS" in js
    assert "function browserWorkbenchElementLabel" in js
    assert "selected.displayLabel||selected.selector" in js
    assert "tag:tagName" in js
    assert "frame:raw.frame&&typeof raw.frame==='object'" in js
    assert "frames:Array.isArray(raw.frames)" in js
    assert "browser-workbench-context-hover" in js
    assert "addBrowserContextItem" in js
    assert "kind:'browser-element'" in js
    assert "displayLabel" in js
    messages_js = open("static/messages.js", encoding="utf-8").read()
    sessions_js = open("static/sessions.js", encoding="utf-8").read()
    assert "context_items:outgoingContextItems.length?outgoingContextItems:undefined" in messages_js
    assert "browser_context_parts:outgoingBrowserContextParts.length?outgoingBrowserContextParts:undefined" in messages_js
    assert "parts:outgoingParts.length?outgoingParts:undefined" in messages_js
    assert "console.log(\"submit.parts\", outgoingParts)" in messages_js
    assert "console.log(\"persisted.parts\", userMsg.parts||[])" in messages_js
    assert "context_items:outgoingContextItems,browser_context_parts:outgoingBrowserContextParts,parts:outgoingParts,model" in messages_js
    assert "browser_context_parts:outgoingBrowserContextParts,parts:outgoingParts,model" in messages_js
    assert "S.pendingContextItems = contextItems" in sessions_js
    assert "_composerSetBrowserContextParts" in sessions_js
    assert "function _browserContextMessageHtml" in ui_js
    assert "function _browserContextPayload" in ui_js
    assert "function _browserContextDisplayLabel" in ui_js
    assert "function parseBrowserWorkbenchContext" in ui_js
    assert "function _browserElementPillHtml" in ui_js
    assert "msg-browser-element-pill-label" in ui_js
    assert "function _renderBrowserWorkbenchContextPartsHtml" in ui_js
    assert "function _normalizeBrowserContextPartsForDisplay" in ui_js
    assert "function _composerBrowserContextPartsForSend" in ui_js
    assert "hasParsedBrowserContext ? _renderBrowserWorkbenchContextPartsHtml(browserContextParts, isUser)" in ui_js
    assert "isUser && !hasParsedBrowserContext ? _browserContextMessageHtml(m.context_items)" in ui_js
    assert "function _composerCreateBrowserContextPill" in ui_js
    assert "queue-card-text composer-editor queue-message-editor" in ui_js
    assert "_composerSetBrowserContextParts(msgSpan,_browserContextParts)" in ui_js
    assert "context_items:newContextItems" in ui_js
    assert "browser_context_parts:newParts" in ui_js
    assert "parts:newParts" in ui_js
    assert "function _parseComposerBrowserContextText" in ui_js
    assert "composer-browser-context-pill" in ui_js
    assert "function _composerCreateBrowserContextIcon" in ui_js
    assert "document.createElementNS('http://www.w3.org/2000/svg','svg')" in ui_js
    assert "svg.setAttribute('fill','none')" in ui_js
    assert "svg.setAttribute('stroke','#ffffff')" in ui_js
    assert "svg.setAttribute('stroke-width','1.75')" in ui_js
    assert "M12 17l3 -8l-8 3l3.5 1.5z" in ui_js
    assert "M5 3l14 9-6.5 1.5L9 21 5 3z" not in ui_js
    assert "icon.textContent='⌖'" not in ui_js
    assert "@element(" in ui_js
    assert "msg-browser-context-chip" in ui_js
    assert "_saveComposerDraft(sid, ta?ta.value:'', S.pendingFiles?[...S.pendingFiles]:[], S.pendingContextItems?[...S.pendingContextItems]:[], _composerBrowserContextPartsForSend())" in ui_js
    assert "_currentComposerContextItems" in sessions_js
    assert "context_items: draftContextItems" in sessions_js
    assert "browser_context_parts: draftBrowserContextParts" in sessions_js
    assert "console.log(\"hydrated.parts\", parts)" in sessions_js
    assert "context_items" in messages_js
    assert "CustomEvent('browser-workbench-context-hover'" in ui_js
    commands_js = open("static/commands.js", encoding="utf-8").read()
    assert "steer-body composer-editor steer-message-editor" in commands_js
    assert "context_items:pendingContextItemsSnapshot" in commands_js
    assert "browser_context_parts:pendingBrowserContextPartsSnapshot" in commands_js
    assert "_showSteerIndicator(_steerIndicatorText(originalMsg,pendingFilesSnapshot),pendingContextItemsSnapshot,pendingBrowserContextPartsSnapshot)" in commands_js
    assert "document.getElementById('msg')" in js
    assert "api/browser-workbench/capabilities" in js
    assert "api/browser-workbench/session" in js
    assert "const WORKBENCH_SESSION_URL='/api/browser-workbench/session'" in js
    assert "return api(path,opts||{})" in js
    assert "method:'DELETE'" in js
    assert "/navigate" in js
    assert "/interact" in js
    assert "hard-reload" in js
    assert "/devtools" in js
    assert "browserWorkbenchRequestBody({url:requested,zoom:target.zoom||1})" in js
    assert "['reload','back','forward']" in js
    assert "navigateBrowserWorkbenchHistory('back')" in js
    assert "navigateBrowserWorkbenchHistory('forward')" in js
    assert "navigateBrowserWorkbenchHistory('reload')" in js
    assert "window.openBrowserWorkbenchTab" in js
    assert "window.closeBrowserWorkbenchTab" in js
    assert "window.pingBrowserWorkbenchSelection" in js
    assert "window.toggleBrowserWorkbenchSelectionMode" in js
    assert "window.openBrowserWorkbenchShell" in js
    assert "window.getBrowserWorkbenchDebugState" in js
    assert "window.navigateBrowserWorkbenchToUrl" in js
    assert "window.navigateBrowserWorkbenchHistory" in js
    assert "localStorage" in js
    assert "document.createElement('iframe')" in js
    assert "renderer==='iframe-bridge'" in js
    assert "renderer==='chromium-stream'" in js
    assert "function renderBrowserWorkbenchChromiumStream" in js
    assert "function renderBrowserWorkbenchSplitView" in js
    assert "BROWSER_WORKBENCH_DEVTOOLS_QUIESCE_MS" in js
    assert "active.devtoolsOpen=mode==='panel';\n    stopBrowserWorkbenchChromiumStream();" in js
    assert "await delayBrowserWorkbench(BROWSER_WORKBENCH_DEVTOOLS_QUIESCE_MS);" in js
    assert "document.createElement('canvas')" in js
    assert "target.bridgeUrl=hasSession&&payload.bridge_url" in js
    assert "client_renderer:nativeBridgeAvailable?'electron-native':'iframe-bridge'" in js
    assert "electron_native_available:nativeBridgeAvailable" in js
    assert "function browserWorkbenchProxyUrlForTarget" in js
    assert "Some pages may behave differently in the embedded browser." in js
    assert "document.createElement('img')" not in js
    assert "screenshot_data_url" not in js
    assert "browser-workbench-screenshot.png" in js
    assert "iframe CSP/X-Frame-Options cannot block this preview" not in js
    assert "No image/base64 Browser Workbench fallback is enabled" not in js
    assert "Native/streamed browser rendering is required" not in js
    assert "browser-workbench-frame" in js
    assert "Chromium viewport" in js
    assert "X-Frame-Options or CSP frame-ancestors" not in js
    assert "Electron/CDP rendering and element inspection" not in js
    assert "browserTabOpen" not in js
    assert "!!active.sessionId||workbenchCapabilities.navigation!==true" not in js
    assert "openerButton.hidden=browserTabOpen" not in js
    assert "innerHTML" not in js
    assert "insertAdjacentHTML" not in js
    assert ".browser-workbench-shell" in css
    assert ".browser-workbench-frame" in css
    assert ".browser-workbench-stream-canvas" in css
    assert ".browser-workbench-devtools-frame" in css
    assert ".browser-workbench-split-wrap" in css
    assert ".browser-workbench-devtools-resizer" in css
    assert ".browser-workbench-toolbar-zoom-value" not in css
    assert ".browser-workbench-screenshot" not in css
    assert ".browser-workbench-menu" in css
    assert ".browser-workbench-menu-zoom" in css
    assert ".browser-workbench-toolbar #browserWorkbenchPing{flex:0 0 auto;width:fit-content;padding:8px 12px;white-space:nowrap;}" in css
    assert ".browser-workbench-nav--reload[data-load-status=\"loading\"]" in css
    assert ".browser-workbench-reload-icon" in css
    assert "font-size:15px;transform:none" in css
    assert "animation:spin .7s linear infinite" not in css
    assert ".browser-workbench-viewport.has-rendered-browser" in css
    assert "object-fit:fill" in css
    assert ".browser-workbench-area-capture-box" in css
    assert ".browser-workbench-hover-overlay" in css
    assert "border:1.5px solid #7c3aed" in css
    assert ".browser-workbench-selection-overlay-label" in css
    assert "position:relative" in css
    assert ".browser-workbench-viewport.selecting{cursor:crosshair!important" in css
    assert ".attach-chip--browser-context" in css
    assert ".msg-browser-element-pill" in css
    assert ".msg-browser-element-pill,.composer-browser-context-pill{display:inline-flex;align-items:center" in css
    assert "vertical-align:middle;margin:0 .16em;padding:1px 6px" in css
    assert ".msg-browser-element-pill{display:inline-block" not in css
    assert ".msg-browser-element-pill-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}" in css
    assert ".composer-browser-context-pill" in css
    assert ".composer-browser-context-icon" in css
    assert "fill:none;stroke:#fff" in css
    assert ".msg-browser-context-chip" in css
    assert ".browser-workbench-frame-note" in css
    assert ".workbench-tabstrip" in css
    assert ".workbench-browser-tabs" in css
    assert ".workbench-tab-browser{flex:0 1 auto;max-width:min(240px,34vw);}" in css
    assert ".workbench-tab-favicon{width:14px;height:14px;flex:0 0 auto;border-radius:3px;object-fit:contain;}" in css
    assert ".workbench-tab[hidden]{display:none!important;}" in css
    assert "main.main.showing-browser > #mainChat" in css
    assert "main.main.showing-browser > #mainChat > #mainBrowser" in css
    assert "main.main.showing-browser > #mainChat > .messages-shell" in css
    assert ".browser-workbench-overlay" not in css
    assert "setBrowserWorkbenchSelectionMode(false);\n    setStatus('Selection added" not in js
    assert "Element added. Select another or press Escape to finish." in js
    index_html = open("static/index.html", encoding="utf-8").read()
    assert 'id="msg" class="composer-editor"' in index_html
    assert 'contenteditable="true"' in index_html


def test_shared_app_dialog_uses_global_portal_and_native_surface_suppression():
    ui_js = open("static/ui.js", encoding="utf-8").read()
    workbench_js = open("static/browser_workbench.js", encoding="utf-8").read()
    preload = open("desktop/src/preload/index.cjs", encoding="utf-8").read()
    desktop_main = open("desktop/src/main/index.cjs", encoding="utf-8").read()

    # Confirm and prompt stay in the same top-level application portal used by
    # every other chat overlay. Opening/closing reconciles synchronously so the
    # native page is suppressed before it can cover the centered DOM dialog.
    assert 'id="appDialogOverlay"' in open("static/index.html", encoding="utf-8").read()
    assert 'data-global-overlay="modal"' in open("static/index.html", encoding="utf-8").read()
    assert "if(overlay){overlay.style.display='flex';overlay.setAttribute('aria-hidden','false');}" in ui_js
    assert "if(overlay){overlay.style.display='none';overlay.setAttribute('aria-hidden','true');}" in ui_js
    assert ui_js.count("_reconcileGlobalOverlays();") >= 3

    # No separate native dialog implementation remains. The generic suppression
    # channel owns Browser Workbench occlusion for dialogs and future overlays.
    assert "browserWorkbenchNativeAppDialogSupported" not in workbench_js
    assert "syncBrowserWorkbenchNativeAppDialog" not in workbench_js
    assert "hermes-app-dialog-visibility" not in workbench_js
    assert "showAppDialog(payload)" not in preload
    assert "onAppDialogAction(callback)" not in preload
    assert "APP_DIALOG_CONSOLE_PREFIX" not in desktop_main
    assert "function showAppDialogOverlay" not in desktop_main
    assert "browser-workbench:show-app-dialog" not in desktop_main
    assert "browser-workbench:set-overlay-suppressed" in desktop_main
    assert "function setApplicationOverlaySuppression(payload)" in desktop_main


def test_browser_context_ordered_parts_round_trip_across_render_and_persistence_paths():
    ui_js = open("static/ui.js", encoding="utf-8").read()
    messages_js = open("static/messages.js", encoding="utf-8").read()
    sessions_js = open("static/sessions.js", encoding="utf-8").read()
    routes_py = open("api/routes.py", encoding="utf-8").read()
    models_py = open("api/models.py", encoding="utf-8").read()
    streaming_py = open("api/streaming.py", encoding="utf-8").read()

    assert "function _composerBrowserContextPartsForSend" in ui_js
    assert "function _composerSetBrowserContextParts" in ui_js
    assert "const HERMES_MESSAGE_PARTS_MIME='application/x-hermes-message-parts+json'" in ui_js
    assert "function _installRichMessagePartsClipboard" in ui_js
    assert "function _copyMessagePartsRich" in ui_js
    assert "_browserWorkbenchContextPartsHaveElement(parts)?_copyMessagePartsRich(parts):_copyText(text)" in ui_js
    assert "_composerPasteSegments(_messagePartsToSegments(parts),editor,{addPillSpace:false})" in ui_js
    assert "window._messagePartsFromClipboardData=_messagePartsFromClipboardData" in ui_js
    assert "function _messagePartsForUserMessage" in ui_js
    assert "ta.className = 'msg-edit-area composer-editor'" in ui_js
    assert "await submitEdit(msgIdx, newText, editParts)" in ui_js
    assert "_composerSetBrowserContextParts(editor,parts)" in ui_js
    assert "data-browser-context-payload" in ui_js
    assert "_installChatBubbleMessagePartsClipboard()" in ui_js
    assert "_normalizeBrowserContextPartsForDisplay(parts).map" in ui_js
    assert "const persistedParts = isUser && _browserWorkbenchContextPartsHaveElement(m.parts) ? m.parts : (m&&m.browser_context_parts);" in ui_js
    assert "_browserWorkbenchContextPartsHaveElement(persistedParts)?persistedParts:parseBrowserWorkbenchContext(displayContent)" in ui_js
    assert "isUser && !hasParsedBrowserContext ? _browserContextMessageHtml(m.context_items)" in ui_js

    assert "let outgoingBrowserContextParts=queueDrain" in messages_js
    assert ": (typeof window._composerBrowserContextPartsForSend==='function'?window._composerBrowserContextPartsForSend():[]);" in messages_js
    assert "browser_context_parts:outgoingBrowserContextParts.length?outgoingBrowserContextParts:undefined" in messages_js
    assert "parts:outgoingParts.length?outgoingParts:undefined" in messages_js
    assert "browser_context_parts:outgoingBrowserContextParts,parts:outgoingParts,model" in messages_js

    assert "function _composerDraftBrowserContextParts" in sessions_js
    assert "browser_context_parts: draftBrowserContextParts" in sessions_js
    assert "window._composerSetBrowserContextParts(ta, browserContextParts)" in sessions_js

    assert 'body.get(\n            "parts",' in routes_py
    assert "next_draft[\"browser_context_parts\"] = browser_context_parts" in routes_py
    assert "browser_context_parts=browser_context_parts" in routes_py
    assert "s.pending_browser_context_parts = list(browser_context_parts or [])" in routes_py
    assert 'user_msg["parts"] = list(browser_context_parts)' in routes_py

    assert "pending_browser_context_parts=None" in models_py
    assert "recovered['browser_context_parts'] = list(pending_browser_context_parts)" in models_py

    assert "current_browser_context_parts=None" in streaming_py
    assert "current_user_msg['browser_context_parts'] = current_browser_context_parts" in streaming_py
    assert "current_user_msg['parts'] = current_browser_context_parts" in streaming_py
    assert "m['browser_context_parts'] = _turn_browser_context_parts" in streaming_py


def test_browser_workbench_cdp_launch_allows_local_devtools_origin():
    source = Path(browser_workbench.__file__).read_text(encoding="utf-8")

    assert '"--remote-debugging-address=127.0.0.1"' in source
    assert '"--remote-allow-origins=*"' in source


def test_browser_workbench_inspect_action_returns_sanitized_selection(monkeypatch):
    class _InspectBackend(browser_workbench.SessionShellBrowserWorkbenchBackend):
        name = "test-inspect"

        def inspect_at(self, session_id: str, body: dict | None = None):
            payload, status = self.get(session_id)
            payload["selection"] = {
                "type": "browser_element",
                "selector": "#submit",
                "component": "SubmitButton",
                "tag": "BUTTON",
                "source": "src/App.jsx:12:4",
                "text": "Save changes",
                "rect": {"left": 10.1234, "top": 20, "width": 90, "height": 32},
                "cdp_endpoint": "ws://secret",
            }
            payload["cdp_endpoint"] = "ws://secret"
            return payload, status

    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    config.save_settings({"browser_workbench_enabled": True})
    backend = _InspectBackend()
    browser_workbench.set_browser_workbench_backend_for_tests(backend)

    create_handler = _FakeHandler({"url": "http://localhost:3000"})
    assert routes.handle_post(create_handler, _parsed("/api/browser-workbench/session")) is True
    session_id = create_handler.json_body()["session_id"]

    inspect_handler = _FakeHandler({"x": 12, "y": 34, "viewport": {"width": 800, "height": 600}})
    assert routes.handle_post(inspect_handler, _parsed(f"/api/browser-workbench/session/{session_id}/inspect")) is True
    body = inspect_handler.json_body()

    assert inspect_handler.status == 200
    assert body["selection"]["selector"] == "#submit"
    assert body["selection"]["component"] == "SubmitButton"
    assert body["selection"]["tag"] == "BUTTON"
    assert body["selection"]["display_label"] == "SubmitButton · BUTTON"
    assert "cdp_endpoint" not in json.dumps(body)


def test_browser_workbench_interact_action_returns_sanitized_session_payload(monkeypatch):
    class _InteractBackend(browser_workbench.SessionShellBrowserWorkbenchBackend):
        name = "test-interact"

        def interact(self, session_id: str, body: dict | None = None):
            payload, status = self.get(session_id)
            payload["screenshot_data_url"] = "data:image/png;base64,abc"
            payload["message"] = f"interaction {(body or {}).get('action')} forwarded"
            payload["cdp_endpoint"] = "ws://secret"
            return payload, status

    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    config.save_settings({"browser_workbench_enabled": True})
    backend = _InteractBackend()
    browser_workbench.set_browser_workbench_backend_for_tests(backend)

    create_handler = _FakeHandler({"url": "http://localhost:3000"})
    assert routes.handle_post(create_handler, _parsed("/api/browser-workbench/session")) is True
    session_id = create_handler.json_body()["session_id"]

    interact_handler = _FakeHandler({"action": "click", "x": 12, "y": 34, "viewport": {"width": 800, "height": 600}})
    assert routes.handle_post(interact_handler, _parsed(f"/api/browser-workbench/session/{session_id}/interact")) is True
    body = interact_handler.json_body()

    assert interact_handler.status == 200
    assert "screenshot_data_url" not in body
    assert body["message"] == "interaction click forwarded"
    assert "cdp_endpoint" not in json.dumps(body)


def test_browser_workbench_screenshot_action_returns_attachment_payload(monkeypatch):
    class _ScreenshotBackend(browser_workbench.SessionShellBrowserWorkbenchBackend):
        name = "test-screenshot"

        def capture_screenshot(self, session_id: str, body: dict | None = None):
            payload, status = self.get(session_id)
            payload["attachment"] = {
                "name": "browser-workbench-area.png" if (body or {}).get("clip") else "browser-workbench-screenshot.png",
                "type": "image/png",
                "data": "iVBORw0KGgo=",
                "width": 80,
                "height": 60,
            }
            payload["screenshot_data_url"] = "data:image/png;base64,private"
            payload["cdp_endpoint"] = "ws://secret"
            return payload, status

    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    config.save_settings({"browser_workbench_enabled": True})
    backend = _ScreenshotBackend()
    browser_workbench.set_browser_workbench_backend_for_tests(backend)

    create_handler = _FakeHandler({"url": "http://localhost:3000"})
    assert routes.handle_post(create_handler, _parsed("/api/browser-workbench/session")) is True
    session_id = create_handler.json_body()["session_id"]

    screenshot_handler = _FakeHandler({"clip": {"x": 1, "y": 2, "width": 80, "height": 60}})
    assert routes.handle_post(screenshot_handler, _parsed(f"/api/browser-workbench/session/{session_id}/screenshot")) is True
    body = screenshot_handler.json_body()

    assert screenshot_handler.status == 200
    assert body["attachment"]["name"] == "browser-workbench-area.png"
    assert body["attachment"]["type"] == "image/png"
    assert body["attachment"]["data"] == "iVBORw0KGgo="
    assert "screenshot_data_url" not in body
    assert "cdp_endpoint" not in json.dumps(body)


def test_browser_context_items_are_normalized_and_formatted_for_prompt():
    raw = [
        {
            "type": "mention",
            "kind": "browser-element",
            "displayLabel": "SubmitButton",
            "payload": {
                "tab": "Browser 1",
                "url": "http://localhost:3000",
                "selector": "#submit",
                "component": "SubmitButton",
                "tag": "BUTTON",
                "source": "src/App.jsx:12:4",
                "text": "Save <changes>",
                "rect": {"left": 10.125, "top": 5, "width": 40, "height": 20, "ignored": "x"},
                "point": {"x": 30, "y": 15},
                "frame": {"selector": "iframe#storybook-preview-iframe", "src": "http://localhost:6006/iframe.html?id=button", "sameOrigin": True},
                "frames": [
                    {"selector": "iframe#storybook-preview-iframe", "src": "http://localhost:6006/iframe.html?id=button", "sameOrigin": True}
                ],
            },
            "cdp_endpoint": "ws://secret",
        }
    ]

    normalized = browser_workbench._normalize_browser_context_items(raw)
    block = browser_workbench._format_browser_context_items_for_prompt(normalized)

    assert normalized == [
        {
            "type": "browser_element",
            "kind": "browser-element",
            "display_label": "SubmitButton · BUTTON",
            "tab": "Browser 1",
            "url": "http://localhost:3000",
            "selector": "#submit",
            "component": "SubmitButton",
            "tag": "BUTTON",
            "source": "src/App.jsx:12:4",
            "text": "Save <changes>",
            "rect": {"left": 10.12, "top": 5.0, "width": 40.0, "height": 20.0},
            "point": {"x": 30.0, "y": 15.0},
            "frame": {"selector": "iframe#storybook-preview-iframe", "src": "http://localhost:6006/iframe.html?id=button", "sameOrigin": True},
            "frames": [
                {"selector": "iframe#storybook-preview-iframe", "src": "http://localhost:6006/iframe.html?id=button", "sameOrigin": True}
            ],
        }
    ]
    assert "<browser_workbench_context>" in block
    assert '<selected_browser_element index="1">' in block
    assert "<selector>#submit</selector>" in block
    assert "<label>SubmitButton · BUTTON</label>" in block
    assert "<tag>BUTTON</tag>" in block
    assert "<frame>{&quot;sameOrigin&quot;: true, &quot;selector&quot;: &quot;iframe#storybook-preview-iframe&quot;" in block
    assert "<frames>[{&quot;sameOrigin&quot;: true, &quot;selector&quot;: &quot;iframe#storybook-preview-iframe&quot;" in block
    assert "Save &lt;changes&gt;" in block
    assert "SubmitButton" in block
    assert "cdp_endpoint" not in block


def test_browser_context_label_preserves_detected_tag_without_component():
    normalized = browser_workbench._normalize_browser_context_items(
        [
            {
                "type": "browser_element",
                "kind": "browser-element",
                "url": "http://localhost:3000",
                "selector": "h1",
                "component": "unknown",
                "tagName": "H1",
            }
        ]
    )

    assert normalized[0]["tag"] == "H1"
    assert normalized[0]["display_label"] == "H1"


def test_browser_element_label_formatter_preserves_detected_html_and_svg_tags():
    tags = ["section", "span", "div", "button", "input", "article", "header", "main", "svg", "path", "linearGradient"]

    for tag in tags:
        assert browser_workbench._sanitize_html_tag_name(tag) == tag
        assert browser_workbench._browser_element_display_label("ReactComponentName", tag) == f"ReactComponentName · {tag}"


def test_browser_workbench_boot_settings_drive_launcher_visibility():
    boot = open("static/boot.js", encoding="utf-8").read()

    assert "window._browserWorkbenchEnabled=s.browser_workbench_enabled!==false" in boot
    assert "applyBrowserWorkbenchAvailability" in boot


def test_browser_workbench_message_parts_are_persisted_as_canonical_order():
    routes_py = open("api/routes.py", encoding="utf-8").read()
    streaming_py = open("api/streaming.py", encoding="utf-8").read()
    models_py = open("api/models.py", encoding="utf-8").read()
    gateway_py = open("api/gateway_chat.py", encoding="utf-8").read()
    ui_js = open("static/ui.js", encoding="utf-8").read()

    assert 'body.get(\n            "parts",' in routes_py
    assert '"parts": browser_context_parts or []' in routes_py
    assert 'user_msg["parts"] = list(browser_context_parts)' in routes_py
    assert "current_user_msg['parts'] = current_browser_context_parts" in streaming_py
    assert "display_msg['parts'] = current_browser_context_parts" in streaming_py
    assert "recovered['parts'] = list(pending_browser_context_parts)" in streaming_py
    assert "m['parts'] = _turn_browser_context_parts" in streaming_py
    assert "_user_turn['parts'] = _pending_browser_context_parts" in streaming_py
    assert "recovered['parts'] = list(pending_browser_context_parts)" in models_py
    assert 'user_msg["parts"] = pending_browser_context_parts' in gateway_py
    assert "m.parts" in ui_js
    assert "m&&m.browser_context_parts" in ui_js


def test_browser_workbench_stream_merge_preserves_ordered_parts():
    from api.streaming import _merge_display_messages_after_agent_result

    parts = [
        {"type": "text", "content": "Test feature ping selection, "},
        {"type": "browser_element", "item": {"type": "browser_element", "display_label": "SortableHomeSectionShell • div"}},
        {"type": "text", "content": " test test "},
        {"type": "browser_element", "item": {"type": "browser_element", "display_label": "LinkComponent • span"}},
        {"type": "text", "content": " "},
        {"type": "browser_element", "item": {"type": "browser_element", "display_label": "Button • button"}},
    ]

    merged = _merge_display_messages_after_agent_result(
        previous_display=[],
        previous_context=[],
        result_messages=[{"role": "assistant", "content": "ok"}],
        msg_text="Test feature ping selection, test test",
        current_context_items=[],
        current_browser_context_parts=parts,
    )

    assert merged[0]["role"] == "user"
    assert merged[0]["parts"] == parts
    assert merged[0]["browser_context_parts"] == parts
    assert [part["type"] for part in merged[0]["parts"]] == [
        "text",
        "browser_element",
        "text",
        "browser_element",
        "text",
        "browser_element",
    ]


def test_browser_workbench_eager_and_recovered_messages_persist_ordered_parts():
    class SessionStub:
        def __init__(self):
            self.messages = []
            self.context_messages = [{"role": "system", "content": "context"}]
            self.pending_user_message = "Test feature ping selection, test test"
            self.pending_attachments = []
            self.pending_context_items = []
            self.pending_browser_context_parts = parts
            self.truncation_watermark = None

    from api.models import _append_recovered_pending_turn

    parts = [
        {"type": "text", "content": "Test feature ping selection, "},
        {"type": "browser_element", "item": {"type": "browser_element", "display_label": "SortableHomeSectionShell • div"}},
        {"type": "text", "content": " test test "},
        {"type": "browser_element", "item": {"type": "browser_element", "display_label": "LinkComponent • span"}},
    ]

    eager = SessionStub()
    routes._checkpoint_user_message_for_eager_session_save(
        eager,
        "Test feature ping selection, test test",
        [],
        123,
        [],
        parts,
    )
    assert eager.messages[0]["parts"] == parts
    assert eager.messages[0]["browser_context_parts"] == parts

    recovered = SessionStub()
    _append_recovered_pending_turn(recovered, timestamp=123)
    assert recovered.messages[0]["parts"] == parts
    assert recovered.messages[0]["browser_context_parts"] == parts
