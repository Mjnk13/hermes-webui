from __future__ import annotations

import io
import json
import subprocess
import threading
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

import api.browser_workbench as browser_workbench
import api.config as config
import api.routes as routes


@pytest.fixture(autouse=True)
def _isolated_settings_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(tmp_path / "webui-state"))
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


def _js_arrow(source: str, name: str) -> str:
    start = source.index(f"const {name} =")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1] + ";"
    raise AssertionError(f"unterminated JavaScript arrow function: {name}")


def _run_node_json(program: str):
    result = subprocess.run(
        ["node", "-e", program],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class _FormProbe(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "form":
            self._current = {"attrs": dict(attrs), "inputs": [], "controls": []}
            self.forms.append(self._current)
        elif tag.lower() in {"button", "input"} and self._current is not None:
            control = dict(attrs)
            self._current["controls"].append(control)
            if tag.lower() == "input":
                self._current["inputs"].append(control)

    def handle_endtag(self, tag):
        if tag.lower() == "form":
            self._current = None


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




def test_browser_workbench_loopback_session_uses_iframe_bridge_renderer(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)

    handler = _FakeHandler({"url": "127.0.0.1:5173/app?x=1"})

    assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is True
    body = handler.json_body()

    assert handler.status == 200
    assert body["ok"] is True
    assert body["renderer"] == "iframe-bridge"
    assert body["bridge_url"].startswith("/browser-proxy/_hermes/bw_")
    assert body["bridge_url"].endswith("/http://127.0.0.1:5173/app?x=1")
    assert "__hermes_bw_" not in _parsed(body["bridge_url"]).query
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
    assert "__HERMES_CHII_CSS_EDITING_V1__" in runtime


def test_browser_workbench_chii_runtime_exposes_values_ranges_and_edits_rules(
    monkeypatch,
):
    """Chii stylesheet rules must behave like editable CDP CSSStyle payloads."""

    runtime_source = r"""
class FakeStyle {
  constructor(entries) { this.setEntries(entries); }
  setEntries(entries) {
    for (let i = 0; i < (this.length || 0); i += 1) delete this[i];
    this._values = {};
    this._priorities = {};
    let index = 0;
    for (const [name, value, priority = ''] of entries) {
      this[index] = name;
      index += 1;
      this._values[name] = value;
      this._priorities[name] = priority;
    }
    this.length = index;
  }
  getPropertyValue(name) { return this._values[name] || ''; }
  getPropertyPriority(name) { return this._priorities[name] || ''; }
  get cssText() {
    return Object.keys(this._values).map(name => `${name}: ${this._values[name]}${this._priorities[name] ? ' !important' : ''};`).join(' ');
  }
  set cssText(text) {
    const entries = String(text || '').split(';').map(part => part.trim()).filter(Boolean).map(part => {
      const colon = part.indexOf(':');
      const name = part.slice(0, colon).trim();
      let value = part.slice(colon + 1).trim();
      const important = /\s*!important\s*$/i.test(value);
      if (important) value = value.replace(/\s*!important\s*$/i, '').trim();
      return [name, value, important ? 'important' : ''];
    });
    this.setEntries(entries);
  }
}
const topRule = {selectorText: '.top', style: new FakeStyle([['color', 'red']])};
const nestedRule = {selectorText: '.nested', style: new FakeStyle([
  ['display', 'flex'],
  ['--space', '2rem'],
  ['color', 'rgb(1, 2, 3)', 'important']
])};
class CSSMediaRule {}
const inactiveMedia = new CSSMediaRule();
inactiveMedia.conditionText = '(max-width: 1px)';
inactiveMedia.cssRules = [{selectorText: '.inactive', style: new FakeStyle([['opacity', '0']])}];
const matchMedia = () => ({matches: false});
const sheet = {href: '/app.css', styleSheetId: 'sheet-1', cssRules: [topRule, {cssRules: [nestedRule]}, inactiveMedia]};
const selectedElement = {matches: selector => selector === '.nested' || selector === '.inactive'};
const document = {styleSheets: [sheet]};
const originalCss = {
  getMatchedStylesForNode: () => ({inlineStyle: {styleSheetId: 'inline-1', cssProperties: [], shorthandEntries: []}, matchedCSSRules: []}),
  getStyleSheetText: () => ({text: ''}),
  setStyleTexts: ({edits}) => ({styles: edits.map(edit => ({styleSheetId: edit.styleSheetId}))})
};
const domains = {
  CSS: originalCss,
  DOM: {getNode: ({nodeId}) => ({node: nodeId === 7 ? selectedElement : null})}
};
const chobitsu = {
  domain: name => domains[name],
  register: (name, methods) => Object.assign(domains[name], methods)
};
const window = {chii: {chobitsu}};
// e.id=w,w||(e.id=w=(0,s.default)(6),p.setItem("chii-id",w))
"""

    class _FakeChiiResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            return runtime_source.encode("utf-8")

    monkeypatch.setattr(
        browser_workbench,
        "_ensure_chii_service",
        lambda: "http://127.0.0.1:18080/",
    )
    monkeypatch.setattr(
        browser_workbench.urllib.request,
        "urlopen",
        lambda url, timeout=None: _FakeChiiResponse(),
    )

    runtime = browser_workbench._chii_target_runtime_script().decode("utf-8")
    observed = _run_node_json(
        runtime
        + r"""
(async () => {
  const before = domains.CSS.getMatchedStylesForNode({nodeId: 7});
  const matched = before.matchedCSSRules[0].rule.style;
  const insertedRule = {selectorText: '.inserted', style: new FakeStyle([['border-width', '1px']])};
  sheet.cssRules.unshift(insertedRule);
  const valueEdit = await domains.CSS.setStyleTexts({edits: [{
    styleSheetId: matched.styleSheetId,
    range: matched.range,
    text: 'display: grid; gap: 4px; --space: 3rem;'
  }]});
  const afterValue = domains.CSS.getMatchedStylesForNode({nodeId: 7});
  const afterValueStyle = afterValue.matchedCSSRules[0].rule.style;
  const keyEdit = await domains.CSS.setStyleTexts({edits: [{
    styleSheetId: afterValueStyle.styleSheetId,
    range: afterValueStyle.range,
    text: 'display: grid; column-gap: 4px; --space: 3rem;'
  }]});
  const afterKey = domains.CSS.getMatchedStylesForNode({nodeId: 7});
  const afterKeyStyle = afterKey.matchedCSSRules[0].rule.style;
  const sheetText = await domains.CSS.getStyleSheetText({styleSheetId: matched.styleSheetId});
  console.log(JSON.stringify({
    matchedSelectors: before.matchedCSSRules.map(item => item.rule.selectorList.text),
    beforeValues: matched.cssProperties.map(prop => [prop.name, prop.value]),
    beforeNoEmptyValues: matched.cssProperties.every(prop => prop.value !== ''),
    importantColorPreserved: matched.cssProperties.find(prop => prop.name === 'color').important === true,
    beforeHasRanges: Boolean(matched.range && matched.cssProperties.every(prop => prop.range && prop.text && prop.parsedOk === true)),
    valueEditValues: valueEdit.styles[0].cssProperties.map(prop => [prop.name, prop.value]),
    afterValueValues: afterValueStyle.cssProperties.map(prop => [prop.name, prop.value]),
    keyEditValues: keyEdit.styles[0].cssProperties.map(prop => [prop.name, prop.value]),
    afterKeyValues: afterKeyStyle.cssProperties.map(prop => [prop.name, prop.value]),
    targetCssText: nestedRule.style.cssText,
    insertedCssText: insertedRule.style.cssText,
    topCssText: topRule.style.cssText,
    sheetText: sheetText.text
  }));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    )

    assert observed == {
        "matchedSelectors": [".nested"],
        "beforeValues": [
            ["display", "flex"],
            ["--space", "2rem"],
            ["color", "rgb(1, 2, 3)"],
        ],
        "beforeNoEmptyValues": True,
        "importantColorPreserved": True,
        "beforeHasRanges": True,
        "valueEditValues": [
            ["display", "grid"],
            ["gap", "4px"],
            ["--space", "3rem"],
        ],
        "afterValueValues": [
            ["display", "grid"],
            ["gap", "4px"],
            ["--space", "3rem"],
        ],
        "keyEditValues": [
            ["display", "grid"],
            ["column-gap", "4px"],
            ["--space", "3rem"],
        ],
        "afterKeyValues": [
            ["display", "grid"],
            ["column-gap", "4px"],
            ["--space", "3rem"],
        ],
        "targetCssText": "display: grid; column-gap: 4px; --space: 3rem;",
        "insertedCssText": "border-width: 1px;",
        "topCssText": "color: red;",
        "sheetText": ".top { color: red; }\n.nested { display: grid; column-gap: 4px; --space: 3rem; }\n.inserted { border-width: 1px; }\n",
    }


def test_browser_workbench_iframe_proxy_strips_frame_headers_and_injects_bridge(monkeypatch):
    captured = {}
    session, status = browser_workbench.create_or_attach_browser_workbench_session({"url": "http://127.0.0.1:5173/app?x=1"})
    assert status == 200
    session_id = session["session_id"]

    class _FakeProxyResponse:
        status = 200
        headers = {
            "Content-Type": "text/html; charset=utf-8",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
            "Set-Cookie": "secret=1",
        }

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            return b'<html><head><title>Dev</title><style>.hero{background:url("/assets/hero.png")}</style></head><body><script src="/assets/app.js"></script><a href="/next">Next</a><form action="/api/auth/login" method="post"></form></body></html>'

    def fake_open(_self, request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["timeout"] = timeout
        return _FakeProxyResponse()

    monkeypatch.setattr(browser_workbench.urllib.request.OpenerDirector, "open", fake_open)
    handler = _FakeHandler(
        headers={
            "Cookie": "hermes_session=secret",
            "Accept": "text/html",
            "RSC": "1",
            "Next-Router-State-Tree": "state-tree",
            "Next-Url": "/app",
        }
    )
    proxy_url = browser_workbench._browser_proxy_url_for_target("http://127.0.0.1:5173/app?x=1", session_id=session_id, frame_id="frame1")

    assert routes.handle_get(handler, _parsed(proxy_url)) is True

    body = handler.wfile.getvalue().decode("utf-8")
    response_headers = {key.lower(): value for key, value in handler.response_headers}
    assert handler.status == 200
    assert captured["url"] == "http://127.0.0.1:5173/app?x=1"
    assert "Cookie" not in captured["headers"]
    forwarded_headers = {key.lower(): value for key, value in captured["headers"].items()}
    assert forwarded_headers["rsc"] == "1"
    assert forwarded_headers["next-router-state-tree"] == "state-tree"
    assert forwarded_headers["next-url"] == "/app"
    assert "x-frame-options" not in response_headers
    assert "content-security-policy" not in response_headers
    assert response_headers["referrer-policy"] == "same-origin"
    assert "set-cookie" not in response_headers
    assert response_headers["x-hermes-browser-proxy-target"] == "http://127.0.0.1:5173/app?x=1"
    assert "hermes-browser-workbench-proxy-bridge" in body
    assert "hermes-browser-workbench-chii-target" in body
    assert f"target_id={browser_workbench._chii_target_id_for_session(session_id)}" in body
    assert "hermes-devtools-agent" in body
    assert f'const sessionId = "{session_id}"' in body
    assert 'const frameId = "frame1"' in body
    assert 'src="/assets/app.js"' in body
    assert 'url("/assets/hero.png")' in body
    assert 'href="/next"' in body
    assert 'action="/api/auth/login"' in body


def test_browser_workbench_proxy_gives_loopback_cold_starts_a_bounded_longer_deadline(
    monkeypatch,
):
    captured_timeouts = {}

    class _FakeProxyResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __init__(self, url):
            self.url = url

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            return b'{"ok":true}'

        def geturl(self):
            return self.url

    def fake_open(_self, request, timeout=None):
        timeout_seconds = float(timeout or 0)
        captured_timeouts[request.full_url] = timeout_seconds
        if request.full_url.startswith("http://localhost:") and timeout_seconds < 120:
            raise TimeoutError("simulated local dev cold start")
        return _FakeProxyResponse(request.full_url)

    monkeypatch.setattr(browser_workbench.urllib.request.OpenerDirector, "open", fake_open)

    local_session, local_status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/api/admin/home"}
    )
    assert local_status == 200
    local_handler = _FakeHandler()
    local_proxy_url = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/api/admin/home",
        session_id=local_session["session_id"],
        frame_id="admin-home-frame",
    )

    assert routes.handle_get(local_handler, _parsed(local_proxy_url)) is True
    assert local_handler.status == 200
    assert captured_timeouts["http://localhost:3000/api/admin/home"] == 120

    remote_session, remote_status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "https://example.test/api/data"}
    )
    assert remote_status == 200
    remote_handler = _FakeHandler()
    remote_proxy_url = browser_workbench._browser_proxy_url_for_target(
        "https://example.test/api/data",
        session_id=remote_session["session_id"],
        frame_id="remote-data-frame",
    )

    assert routes.handle_get(remote_handler, _parsed(remote_proxy_url)) is True
    assert remote_handler.status == 200
    assert captured_timeouts["https://example.test/api/data"] == 15


@pytest.mark.parametrize(
    ("route_handler", "method"),
    [
        (routes.handle_patch, "PATCH"),
        (routes.handle_put, "PUT"),
        (routes.handle_delete, "DELETE"),
    ],
)
def test_browser_workbench_proxy_routes_all_unsafe_methods_before_webui_csrf(
    monkeypatch,
    route_handler,
    method,
):
    captured = {}
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/admin/remote-service"}
    )
    assert status == 200
    session_id = session["session_id"]

    class _FakeProxyResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            return b'{"ok":true}'

    def fake_open(_self, request, timeout=None):
        captured["method"] = request.get_method()
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["headers"] = {
            key.lower(): value for key, value in request.header_items()
        }
        return _FakeProxyResponse()

    monkeypatch.setattr(browser_workbench.urllib.request.OpenerDirector, "open", fake_open)
    request_body = b'{"setting":"unchanged"}'
    proxy_url = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/api/admin/remote-service",
        session_id=session_id,
        frame_id="remote-service-frame",
    )
    handler = _FakeHandler(
        request_body,
        headers={
            "Content-Type": "application/json",
            "Origin": "http://127.0.0.1:8788",
        },
    )

    assert route_handler(handler, _parsed(proxy_url)) is True

    assert handler.status == 200
    assert handler.json_body() == {"ok": True}
    assert captured["method"] == method
    assert captured["url"] == "http://localhost:3000/api/admin/remote-service"
    assert captured["body"] == request_body
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["headers"]["origin"] == "http://localhost:3000"


def test_browser_workbench_proxy_preserves_multipart_file_bytes(monkeypatch):
    captured = {}
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/admin/media"}
    )
    assert status == 200

    class _FakeProxyResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            return b'{"ok":true}'

    def fake_open(_self, request, timeout=None):
        captured["method"] = request.get_method()
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["headers"] = {
            key.lower(): value for key, value in request.header_items()
        }
        return _FakeProxyResponse()

    monkeypatch.setattr(browser_workbench.urllib.request.OpenerDirector, "open", fake_open)
    boundary = "----HermesWorkbenchBinaryBoundary"
    file_bytes = (
        b"\x00device-file\r\nblob:http://127.0.0.1:8788/native-id\r\n"
        b"/browser-proxy/_hermes/not-transport\xff"
    )
    multipart_body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="caption"\r\n\r\n'
        "unchanged\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="device.bin"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + file_bytes + f"\r\n--{boundary}--\r\n".encode()
    proxy_url = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/api/media/upload",
        session_id=session["session_id"],
        frame_id="media-upload-frame",
    )
    content_type = f"multipart/form-data; boundary={boundary}"
    handler = _FakeHandler(
        multipart_body,
        headers={
            "Content-Type": content_type,
            "Origin": "http://127.0.0.1:8788",
        },
    )

    assert routes.handle_post(handler, _parsed(proxy_url)) is True

    assert handler.status == 200
    assert captured["method"] == "POST"
    assert captured["url"] == "http://localhost:3000/api/media/upload"
    assert captured["body"] == multipart_body
    assert captured["headers"]["content-type"] == content_type
    assert captured["headers"]["origin"] == "http://localhost:3000"
    assert captured["headers"]["referer"] == "http://localhost:3000/api/media/upload"


def test_browser_workbench_iframe_proxy_routes_opaque_nested_frame_assets_without_referrer(monkeypatch):
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://127.0.0.1:5173/outer"}
    )
    assert status == 200
    session_id = session["session_id"]

    class _PreviewResponse:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            return self.body

    outer_html = (
        b'<html><body><iframe id="opaque" sandbox="allow-scripts allow-forms" src="/preview"></iframe>'
        b'<iframe id="fully-sandboxed" sandbox src="/locked"></iframe>'
        b'<iframe id="same-origin" sandbox="allow-scripts allow-same-origin" src="/hydrated"></iframe>'
        b'<iframe id="data-only" data-sandbox="allow-scripts" src="/ordinary"></iframe>'
        b'<iframe id="opaque-srcdoc" sandbox="allow-scripts" '
        b'srcdoc="&lt;link rel=&quot;stylesheet&quot; href=&quot;/inline.css&quot;&gt;'
        b'&lt;script src=&quot;/inline.js&quot;&gt;&lt;/script&gt;"></iframe>'
        b'</body></html>'
    )
    preview_html = (
        b'<html><head><link rel="stylesheet" href="/preview.css">'
        b'<script src="/preview.js"></script></head><body>Preview</body></html>'
    )

    def fake_open(_self, request, timeout=None):
        if request.full_url.endswith("/outer"):
            return _PreviewResponse(outer_html)
        if request.full_url.endswith("/preview"):
            return _PreviewResponse(preview_html)
        raise AssertionError(f"unexpected proxy target: {request.full_url}")

    monkeypatch.setattr(
        browser_workbench.urllib.request.OpenerDirector,
        "open",
        fake_open,
    )
    outer_handler = _FakeHandler(headers={"Accept": "text/html", "Sec-Fetch-Dest": "iframe"})
    outer_proxy_url = browser_workbench._browser_proxy_url_for_target(
        "http://127.0.0.1:5173/outer",
        session_id=session_id,
        frame_id="workbench-frame",
    )

    assert routes.handle_get(outer_handler, _parsed(outer_proxy_url)) is True

    outer_body = outer_handler.wfile.getvalue().decode("utf-8")
    assert outer_body.index("hermes-browser-workbench-proxy-bridge") < outer_body.index(
        '<iframe id="opaque"'
    )
    iframe_probe = HTMLParser()
    iframe_attributes = {}

    def capture_starttag(tag, attrs):
        if tag.lower() == "iframe":
            values = dict(attrs)
            iframe_attributes[values.get("id")] = values

    iframe_probe.handle_starttag = capture_starttag
    iframe_probe.feed(outer_body)
    opaque_src = iframe_attributes["opaque"]["src"]
    assert opaque_src.startswith("/browser-proxy/_hermes/")
    assert opaque_src.endswith("/http://127.0.0.1:5173/preview")
    assert iframe_attributes["fully-sandboxed"]["src"].endswith(
        "/http://127.0.0.1:5173/locked"
    )
    assert iframe_attributes["same-origin"]["src"] == "/hydrated"
    assert iframe_attributes["data-only"]["src"] == "/ordinary"
    opaque_srcdoc = iframe_attributes["opaque-srcdoc"]["srcdoc"]
    assert "hermes-browser-workbench-proxy-bridge" in opaque_srcdoc
    assert "/http://127.0.0.1:5173/inline.css" in opaque_srcdoc
    assert "/http://127.0.0.1:5173/inline.js" in opaque_srcdoc

    nested_handler = _FakeHandler(
        headers={
            "Accept": "text/html",
            "Sec-Fetch-Dest": "iframe",
            "Referer": "",
        }
    )
    assert routes.handle_get(nested_handler, _parsed(opaque_src)) is True

    nested_body = nested_handler.wfile.getvalue().decode("utf-8")
    assert nested_handler.status == 200
    assert 'href="/browser-proxy/_hermes/' in nested_body
    assert '/http://127.0.0.1:5173/preview.css"' in nested_body
    assert 'src="/browser-proxy/_hermes/' in nested_body
    assert '/http://127.0.0.1:5173/preview.js"' in nested_body


@pytest.mark.parametrize(
    ("request_headers", "method", "expected_status"),
    [
        ({"Sec-Fetch-Dest": "document"}, "GET", 307),
        ({"Sec-Fetch-Dest": "iframe"}, "GET", 307),
        ({"Sec-Fetch-Dest": "document"}, "POST", 303),
        ({"Accept": "text/html,application/xhtml+xml"}, "GET", 307),
    ],
)
def test_browser_workbench_proxy_canonicalizes_followed_document_redirect(
    monkeypatch,
    request_headers,
    method,
    expected_status,
):
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/admin"}
    )
    assert status == 200
    session_id = session["session_id"]

    class _RedirectedProxyResponse:
        status = 200
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __init__(self):
            self.read_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def geturl(self):
            return "http://localhost:3000/admin/login?from_url=%2Fadmin"

        def read(self, limit=-1):
            self.read_calls += 1
            return b"<html><body>Login</body></html>"

    response = _RedirectedProxyResponse()
    monkeypatch.setattr(
        browser_workbench.urllib.request.OpenerDirector,
        "open",
        lambda _self, request, timeout=None: response,
    )
    proxy_url = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/admin",
        session_id=session_id,
        frame_id="admin-frame",
    )
    handler = _FakeHandler(
        b"submitted=once" if method == "POST" else None,
        headers=request_headers,
    )

    assert browser_workbench.handle_browser_workbench_proxy_request(
        handler,
        _parsed(proxy_url),
        method=method,
    ) is True

    expected = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/admin/login?from_url=%2Fadmin",
        session_id=session_id,
        frame_id="admin-frame",
    )
    assert handler.status == expected_status
    assert dict(handler.response_headers)["Location"] == expected
    assert handler.wfile.getvalue() == b""
    assert response.read_calls == 0


def test_browser_workbench_proxy_canonicalizes_followed_document_redirect_to_error(monkeypatch):
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/moved"}
    )
    assert status == 200
    session_id = session["session_id"]
    final_url = "http://localhost:3000/missing"

    def raise_redirected_error(_self, request, timeout=None):
        raise browser_workbench.urllib.error.HTTPError(
            final_url,
            404,
            "Not Found",
            {"Content-Type": "text/html; charset=utf-8"},
            io.BytesIO(b"<html><body>Missing</body></html>"),
        )

    monkeypatch.setattr(
        browser_workbench.urllib.request.OpenerDirector,
        "open",
        raise_redirected_error,
    )
    proxy_url = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/moved",
        session_id=session_id,
        frame_id="error-frame",
    )
    handler = _FakeHandler(headers={"Sec-Fetch-Dest": "iframe"})

    assert routes.handle_get(handler, _parsed(proxy_url)) is True

    assert handler.status == 307
    assert dict(handler.response_headers)["Location"] == browser_workbench._browser_proxy_url_for_target(
        final_url,
        session_id=session_id,
        frame_id="error-frame",
    )
    assert handler.wfile.getvalue() == b""


def test_browser_workbench_proxy_keeps_followed_subresource_redirect_in_response(monkeypatch):
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/admin"}
    )
    assert status == 200
    session_id = session["session_id"]

    class _RedirectedProxyResponse:
        status = 200
        headers = {"Content-Type": "application/javascript; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def geturl(self):
            return "http://localhost:3000/_next/static/chunks/current.js"

        def read(self, limit=-1):
            return b"self.__chunk_loaded__ = true;"

    monkeypatch.setattr(
        browser_workbench.urllib.request.OpenerDirector,
        "open",
        lambda _self, request, timeout=None: _RedirectedProxyResponse(),
    )
    proxy_url = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/_next/static/chunks/stale.js",
        session_id=session_id,
        frame_id="admin-frame",
    )
    handler = _FakeHandler(headers={"Sec-Fetch-Dest": "script"})

    assert routes.handle_get(handler, _parsed(proxy_url)) is True

    assert handler.status == 200
    assert handler.wfile.getvalue() == b"self.__chunk_loaded__ = true;"
    assert dict(handler.response_headers)["X-Hermes-Browser-Proxy-Target"] == (
        "http://localhost:3000/_next/static/chunks/current.js"
    )


def test_browser_workbench_proxy_keeps_next_runtime_assets_on_clean_shell_root_urls():
    rewritten = browser_workbench._browser_proxy_rewrite_html(
        """<html><head>
        <link rel="stylesheet" href="/_next/static/chunks/app.css">
        <style>.font{src:url('/_next/static/media/font.woff2')}</style>
        </head><body>
        <script src="/_next/static/chunks/runtime.js"></script>
        <a href="/admin">Admin</a>
        <form action="/admin/login"><input type="password" name="password"><button>Sign in</button></form>
        </body></html>""",
        "http://localhost:3000/admin/login",
        session_id="bw_login",
        frame_id="login-frame",
    )

    # Turbopack keys its runtime chunks by their clean root-relative URL. The
    # early route recovery layer forwards these same-origin shell requests to
    # the target by using the live proxy document referrer and session.
    assert 'href="/_next/static/chunks/app.css"' in rewritten
    assert "url('/_next/static/media/font.woff2')" in rewritten
    assert 'src="/_next/static/chunks/runtime.js"' in rewritten
    assert 'href="/admin"' in rewritten
    assert '<form action="/admin/login"><input type="password"' in rewritten


def test_browser_workbench_proxy_transport_metadata_is_invisible_to_target_router():
    proxy_url = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/admin/features?locale=ko",
        session_id="bw_router",
        frame_id="frame-router",
    )
    parsed = _parsed(proxy_url)

    assert parsed.query == "locale=ko"
    assert "__hermes_bw_session" not in parsed.query
    assert "__hermes_bw_frame" not in parsed.query
    assert browser_workbench._browser_proxy_target_from_route(parsed) == (
        "http://localhost:3000/admin/features?locale=ko"
    )


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_browser_workbench_proxy_redirects_live_legacy_transport_to_path_context(monkeypatch, method):
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/admin/features?locale=ko"}
    )
    assert status == 200
    session_id = session["session_id"]
    legacy_url = (
        "/browser-proxy/http://localhost:3000/admin/features%3Flocale%3Dko"
        f"?__hermes_bw_session={session_id}&__hermes_bw_frame=legacy-frame"
    )

    def unexpected_open(*_args, **_kwargs):
        raise AssertionError("legacy transport must redirect before contacting the target")

    monkeypatch.setattr(browser_workbench.urllib.request.OpenerDirector, "open", unexpected_open)
    handler = _FakeHandler(b"email=redacted" if method == "POST" else None)

    assert browser_workbench.handle_browser_workbench_proxy_request(
        handler,
        _parsed(legacy_url),
        method=method,
    ) is True

    expected = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/admin/features?locale=ko",
        session_id=session_id,
        frame_id="legacy-frame",
    )
    assert handler.status == 307
    assert dict(handler.response_headers)["Location"] == expected
    assert _parsed(expected).query == "locale=ko"


def test_browser_workbench_proxy_preserves_root_relative_hydration_attributes():
    rewritten = browser_workbench._browser_proxy_rewrite_html(
        """<html><head>
        <link rel="stylesheet" href="/assets/app.css">
        <style>.hero{background-image:url('/assets/hero.png')}</style>
        </head><body>
        <img src="/assets/logo.png" srcset="/assets/logo.png 1x, /assets/logo@2x.png 2x">
        <script src="/assets/app.js"></script>
        </body></html>""",
        "http://localhost:3000/admin/features",
        session_id="bw_router",
        frame_id="frame-router",
    )

    assert 'href="/assets/app.css"' in rewritten
    assert "url('/assets/hero.png')" in rewritten
    assert 'src="/assets/logo.png"' in rewritten
    assert 'srcset="/assets/logo.png 1x, /assets/logo@2x.png 2x"' in rewritten
    assert 'src="/assets/app.js"' in rewritten


def test_browser_workbench_proxy_preserves_passive_resource_hydration_attributes():
    rewritten = browser_workbench._browser_proxy_rewrite_html(
        """<html><head>
        <link rel="stylesheet" href="https://cdn.example.test/app.css">
        <style>.hero{background-image:url('https://images.example.test/hero.png')}</style>
        </head><body>
        <picture>
          <source srcset="https://images.example.test/card.webp 1x, https://images.example.test/card@2x.webp 2x">
          <img src="https://images.example.test/card.png"
               srcset="https://images.example.test/card.png 1x, https://images.example.test/card@2x.png 2x"
               data-src="https://images.example.test/lazy.png"
               style="background-image:url('https://images.example.test/fallback.png')">
        </picture>
        <video src="https://media.example.test/demo.mp4" poster="https://images.example.test/poster.png">
          <source src="https://media.example.test/demo.webm">
          <track src="https://media.example.test/captions.vtt">
        </video>
        <audio src="https://media.example.test/demo.mp3"></audio>
        <script src="https://cdn.example.test/app.js"></script>
        </body></html>""",
        "http://localhost:3000/en/solutions",
        session_id="bw_hydration",
        frame_id="frame-hydration",
    )

    assert 'src="https://images.example.test/card.png"' in rewritten
    assert (
        'srcset="https://images.example.test/card.png 1x, '
        'https://images.example.test/card@2x.png 2x"'
    ) in rewritten
    assert (
        'srcset="https://images.example.test/card.webp 1x, '
        'https://images.example.test/card@2x.webp 2x"'
    ) in rewritten
    assert 'data-src="https://images.example.test/lazy.png"' in rewritten
    assert "background-image:url('https://images.example.test/fallback.png')" in rewritten
    assert "background-image:url('https://images.example.test/hero.png')" in rewritten
    assert 'src="https://media.example.test/demo.mp4"' in rewritten
    assert 'poster="https://images.example.test/poster.png"' in rewritten
    assert 'src="https://media.example.test/demo.webm"' in rewritten
    assert 'src="https://media.example.test/captions.vtt"' in rewritten
    assert 'src="https://media.example.test/demo.mp3"' in rewritten
    assert 'href="/browser-proxy/_hermes/' in rewritten
    assert '/https://cdn.example.test/app.css"' in rewritten
    assert 'src="/browser-proxy/_hermes/' in rewritten
    assert '/https://cdn.example.test/app.js"' in rewritten

    opaque = browser_workbench._browser_proxy_rewrite_html(
        '<img src="https://images.example.test/card.png">',
        "http://localhost:3000/preview",
        session_id="bw_hydration",
        frame_id="frame-hydration~opaque",
        rewrite_root_relative=True,
    )
    assert 'src="/browser-proxy/_hermes/' in opaque
    assert '/https://images.example.test/card.png"' in opaque


def test_browser_workbench_iframe_bridge_exposes_target_pathname_to_app_runtime():
    bridge = browser_workbench._browser_proxy_bridge_script(
        "http://localhost:3000/admin?locale=ko",
        session_id="bw_router",
        frame_id="frame-router",
    )
    install_shim = _js_arrow(bridge, "installTargetPathnameUrlShim")
    program = "\n".join(
        [
            "const proxyPrefix='/browser-proxy/';",
            "const proxyTransportPath='_hermes/bw_router/frame-router/';",
            "const location={origin:'http://127.0.0.1:8788',href:'http://127.0.0.1:8788/browser-proxy/_hermes/bw_router/frame-router/http://localhost:3000/admin?locale=ko',pathname:'/browser-proxy/_hermes/bw_router/frame-router/http://localhost:3000/admin'};",
            "const window={URL};",
            install_shim,
            "installTargetPathnameUrlShim();",
            "console.log(JSON.stringify({locationPath:location.pathname,proxyPath:new URL(location.href).pathname,proxySearch:new URL(location.href).search,targetPath:new URL('http://localhost:3000/admin/features').pathname}));",
        ]
    )

    assert _run_node_json(program) == {
        "locationPath": "/browser-proxy/_hermes/bw_router/frame-router/http://localhost:3000/admin",
        "proxyPath": "/admin",
        "proxySearch": "?locale=ko",
        "targetPath": "/admin/features",
    }


def test_browser_workbench_bridge_proxies_resources_added_to_same_origin_blank_iframe():
    """A portal-style about:blank preview must not lose the proxy context."""
    playwright = pytest.importorskip("playwright.sync_api")
    bridge = browser_workbench._browser_proxy_bridge_script(
        "http://localhost:3000/admin/product?local=en",
        session_id="bw_nested",
        frame_id="product-frame",
    )
    marker = '<script id="hermes-browser-workbench-proxy-bridge">'
    bridge_source = bridge.split(marker, 1)[1].rsplit("</script>", 1)[0]
    document_html = (
        "<!doctype html><html><head><script>"
        + bridge_source
        + "</script></head><body></body></html>"
    )
    requested_assets = []

    with playwright.sync_playwright() as playwright_context:
        browser = playwright_context.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()
        def fulfill(route):
            url = route.request.url
            if url == "http://127.0.0.1:8788/bridge-test":
                route.fulfill(status=200, content_type="text/html", body=document_html)
                return
            requested_assets.append(url)
            if "/browser-proxy/_hermes/" not in url:
                route.fulfill(status=404, content_type="application/json", body='{"error":"not found"}')
            elif "/_next/static/chunks/app.css" in url:
                route.fulfill(status=200, content_type="text/css", body="body{color:rgb(1,2,3)}")
            elif "/_next/static/chunks/app.js" in url:
                route.fulfill(status=200, content_type="application/javascript", body="window.__nestedScriptLoaded=true")
            elif "/images/preview.svg" in url:
                route.fulfill(
                    status=200,
                    content_type="image/svg+xml",
                    body='<svg xmlns="http://www.w3.org/2000/svg" width="2" height="3"/>',
                )
            else:
                route.fulfill(status=404, content_type="text/plain", body="not found")

        page.route("**/*", fulfill)
        page.goto("http://127.0.0.1:8788/bridge-test")
        result = page.evaluate(
            """() => {
                const frame = document.createElement('iframe');
                frame.setAttribute('title', 'nested preview');
                document.body.appendChild(frame);
                const nestedDocument = frame.contentDocument;
                const stylesheet = nestedDocument.createElement('link');
                stylesheet.rel = 'stylesheet';
                const style = nestedDocument.createElement('style');
                const script = nestedDocument.createElement('script');
                const image = nestedDocument.createElement('img');
                nestedDocument.head.append(stylesheet, style, script);
                nestedDocument.body.append(image);
                stylesheet.href = '/_next/static/chunks/app.css';
                style.textContent = "@font-face{src:url('/fonts/preview.woff2')}";
                script.src = '/_next/static/chunks/app.js';
                image.src = '/images/preview.svg';
                return {
                    stylesheet: stylesheet.getAttribute('href'),
                    style: style.textContent,
                    script: script.getAttribute('src'),
                    image: image.getAttribute('src'),
                };
            }"""
        )
        page.wait_for_function(
            "document.querySelector('iframe').contentWindow.__nestedScriptLoaded === true"
        )
        nested_frame = page.frames[-1]
        nested_frame.wait_for_function("getComputedStyle(document.body).color === 'rgb(1, 2, 3)'")
        nested_frame.wait_for_function("document.images[0].complete && document.images[0].naturalWidth === 2")
        browser.close()

    expected_prefix = "/browser-proxy/_hermes/bw_nested/product-frame-nested-1/"
    assert result["stylesheet"].startswith(expected_prefix + "http://localhost:3000/")
    assert result["script"].startswith(expected_prefix + "http://localhost:3000/")
    assert result["image"] == expected_prefix + "http://localhost:3000/images/preview.svg"
    assert expected_prefix + "http://localhost:3000/fonts/preview.woff2" in result["style"]
    assert requested_assets
    assert all("/browser-proxy/_hermes/" in url for url in requested_assets)


def test_browser_workbench_iframe_bridge_canonicalizes_absolute_target_navigation():
    bridge = browser_workbench._browser_proxy_bridge_script(
        "http://localhost:3000/admin/login",
        session_id="bw_login",
        frame_id="login-frame",
    )
    install_boundary = _js_arrow(bridge, "installProxyNavigationBoundary")
    program = "\n".join(
        [
            "let navigateHandler=null;",
            "const navigation={addEventListener:(name,handler)=>{if(name==='navigate')navigateHandler=handler;}};",
            "const window={navigation};",
            "const targetOrigin='http://localhost:3000';",
            "const proxyPrefix='/browser-proxy/';let proxyNavigationRedirecting=false;",
            "const assigned=[];const location={origin:'http://127.0.0.1:8788',assign:value=>assigned.push(value)};",
            "const toProxyHttp=value=>'/proxy/'+value;",
            install_boundary,
            "const installed=installProxyNavigationBoundary();",
            "const event={destination:{url:'http://localhost:3000/admin'},cancelable:true,prevented:false,preventDefault(){this.prevented=true;}};",
            "navigateHandler(event);",
            "console.log(JSON.stringify({installed,prevented:event.prevented,assigned}));",
        ]
    )

    assert _run_node_json(program) == {
        "installed": True,
        "prevented": True,
        "assigned": ["/proxy/http://localhost:3000/admin"],
    }


def test_browser_workbench_iframe_bridge_canonicalizes_cross_origin_navigation():
    bridge = browser_workbench._browser_proxy_bridge_script(
        "https://first.example/start",
        session_id="bw_external",
        frame_id="external-frame",
    )
    install_boundary = _js_arrow(bridge, "installProxyNavigationBoundary")
    program = "\n".join(
        [
            "let navigateHandler=null;",
            "const navigation={addEventListener:(name,handler)=>{if(name==='navigate')navigateHandler=handler;}};",
            "const window={navigation};",
            "const targetOrigin='https://first.example';",
            "const proxyPrefix='/browser-proxy/';let proxyNavigationRedirecting=false;",
            "const assigned=[];const location={origin:'http://127.0.0.1:8788',assign:value=>assigned.push(value)};",
            "const toProxyHttp=value=>'/proxy/'+value;",
            install_boundary,
            "installProxyNavigationBoundary();",
            "const event={destination:{url:'https://second.example/next'},cancelable:true,prevented:false,preventDefault(){this.prevented=true;}};",
            "navigateHandler(event);",
            "console.log(JSON.stringify({prevented:event.prevented,assigned}));",
        ]
    )

    assert _run_node_json(program) == {
        "prevented": True,
        "assigned": ["/proxy/https://second.example/next"],
    }


def test_browser_workbench_iframe_bridge_does_not_reissue_proxy_history_navigation():
    bridge = browser_workbench._browser_proxy_bridge_script(
        "http://localhost:3000/admin/login?from_url=%2Fadmin",
        session_id="bw_login",
        frame_id="login-frame",
    )
    install_shim = _js_arrow(bridge, "installTargetPathnameUrlShim")
    to_proxy_http = _js_arrow(bridge, "toProxyHttp")
    install_boundary = _js_arrow(bridge, "installProxyNavigationBoundary")
    proxy_url = (
        "http://127.0.0.1:8788/browser-proxy/_hermes/bw_login/login-frame/"
        "http://localhost:3000/admin/login?from_url=%2Fadmin"
    )
    program = "\n".join(
        [
            "let navigateHandler=null;",
            "const navigation={addEventListener:(name,handler)=>{if(name==='navigate')navigateHandler=handler;}};",
            "const window={URL,navigation};",
            "const targetUrl='http://localhost:3000/admin/login?from_url=%2Fadmin';",
            "const targetOrigin='http://localhost:3000';",
            "const proxyPrefix='/browser-proxy/';",
            "const proxyTransportPath='_hermes/bw_login/login-frame/';",
            f"const currentHref={json.dumps(proxy_url)};",
            "const assigned=[];const location={origin:'http://127.0.0.1:8788',href:currentHref,pathname:new URL(currentHref).pathname,assign:value=>assigned.push(value)};",
            "const initialProxyHref=currentHref;",
            install_shim,
            "installTargetPathnameUrlShim();",
            "const resolveBrowserWorkbenchBlobUrl=value=>value;",
            to_proxy_http,
            "let proxyNavigationRedirecting=false;",
            install_boundary,
            "installProxyNavigationBoundary();",
            "const event={destination:{url:currentHref},cancelable:true,prevented:false,preventDefault(){this.prevented=true;}};",
            "navigateHandler(event);",
            "console.log(JSON.stringify({visiblePath:new URL(currentHref).pathname,prevented:event.prevented,assigned}));",
        ]
    )

    assert _run_node_json(program) == {
        "visiblePath": "/admin/login",
        "prevented": False,
        "assigned": [],
    }


def test_browser_workbench_iframe_bridge_metadata_reports_live_history_capabilities():
    bridge = browser_workbench._browser_proxy_bridge_script(
        "https://example.test/current",
        session_id="bw_history",
        frame_id="history-frame",
    )
    target_url_from_proxy_href = _js_arrow(bridge, "targetUrlFromProxyHref")
    metadata = _js_arrow(bridge, "metadata")
    current_proxy = (
        "http://127.0.0.1:8788/browser-proxy/_hermes/bw_history/history-frame/"
        "https://example.test/current"
    )
    program = "\n".join(
        [
            "const posted=[];",
            "const document={querySelector:()=>null,title:'Current',readyState:'complete'};",
            f"const currentProxy={json.dumps(current_proxy)};",
            "const parsedLocation=new URL(currentProxy);",
            "const location={href:currentProxy,origin:parsedLocation.origin,pathname:parsedLocation.pathname,search:parsedLocation.search,hash:parsedLocation.hash};",
            "const targetUrl='https://example.test/current';",
            "const targetOrigin='https://example.test';",
            "const proxyPrefix='/browser-proxy/';",
            "const proxyTransportPath='_hermes/bw_history/history-frame/';",
            "const initialProxyHref=currentProxy;",
            "const previousProxy='http://127.0.0.1:8788/browser-proxy/_hermes/bw_history/history-frame/https://example.test/previous';",
            "const nextProxy='http://127.0.0.1:8788/browser-proxy/_hermes/bw_history/history-frame/https://example.test/next';",
            "const window={frames:[],history:{length:3},navigation:{currentEntry:{index:1},entries:()=>[{index:0,url:previousProxy},{index:1,url:currentProxy},{index:2,url:nextProxy}]}};",
            target_url_from_proxy_href,
            "const currentTargetUrl=()=>targetUrlFromProxyHref(location.href)||targetUrl;",
            "const post=payload=>posted.push(payload);",
            "const devtoolsPost=()=>{};const devtoolsStartedAt=0;",
            metadata,
            "metadata('replace');",
            "console.log(JSON.stringify(posted[0]));",
        ]
    )

    result = _run_node_json(program)
    assert result["can_go_back"] is True
    assert result["can_go_forward"] is True
    assert result["native_history_previous_url"] == "https://example.test/previous"
    assert result["native_history_next_url"] == "https://example.test/next"
    assert result["history_mode"] == "replace"


def test_browser_workbench_iframe_fetch_proxies_target_api_routes():
    bridge = browser_workbench._browser_proxy_bridge_script(
        "http://localhost:3000/login",
        session_id="bw_login",
        frame_id="frame-login",
    )
    to_proxy_http = _js_arrow(bridge, "toProxyHttp")
    to_target_ws = _js_arrow(bridge, "toTargetWs")
    program = "\n".join(
        [
            "const targetUrl='http://localhost:3000/login';",
            "const targetOrigin='http://localhost:3000';",
            "const proxyPrefix='/browser-proxy/';",
            "const proxyTransportPath='_hermes/bw_login/frame-login/';",
            "const sessionId='bw_login';",
            "const frameId='frame-login';",
            "const location={origin:'http://127.0.0.1:8788'};",
            "const resolveBrowserWorkbenchBlobUrl=value=>value;",
            to_proxy_http,
            to_target_ws,
            "console.log(JSON.stringify({login:toProxyHttp('/api/auth/login'),absoluteWebuiApi:toProxyHttp('http://127.0.0.1:8788/api/auth/login'),relative:toProxyHttp('/dashboard'),existingProxy:toProxyHttp('http://127.0.0.1:8788/browser-proxy/http://localhost:3000/dashboard?__hermes_bw_session=bw_login'),relativeWs:toTargetWs('/_next/webpack-hmr'),absoluteShellWs:toTargetWs('ws://127.0.0.1:8788/_next/webpack-hmr'),externalWss:toTargetWs('wss://events.example.test/live')}));",
        ]
    )

    assert _run_node_json(program) == {
        "login": "/browser-proxy/_hermes/bw_login/frame-login/http://localhost:3000/api/auth/login",
        "absoluteWebuiApi": "/browser-proxy/_hermes/bw_login/frame-login/http://localhost:3000/api/auth/login",
        "relative": "/browser-proxy/_hermes/bw_login/frame-login/http://localhost:3000/dashboard",
        "existingProxy": "/browser-proxy/http://localhost:3000/dashboard?__hermes_bw_session=bw_login",
        "relativeWs": "ws://localhost:3000/_next/webpack-hmr",
        "absoluteShellWs": "ws://localhost:3000/_next/webpack-hmr",
        "externalWss": "wss://events.example.test/live",
    }


def test_browser_workbench_iframe_bridge_exposes_target_origin_object_urls_and_resolves_native_consumers():
    bridge = browser_workbench._browser_proxy_bridge_script(
        "http://localhost:3000/admin/media",
        session_id="bw_upload",
        frame_id="frame-upload",
    )
    resolve_blob_url = _js_arrow(bridge, "resolveBrowserWorkbenchBlobUrl")
    expose_blob_url = _js_arrow(bridge, "exposeBrowserWorkbenchBlobUrl")
    replace_blob_urls = _js_arrow(bridge, "replaceBrowserWorkbenchBlobUrls")
    resolve_blob_text = _js_arrow(bridge, "resolveBrowserWorkbenchBlobText")
    expose_blob_text = _js_arrow(bridge, "exposeBrowserWorkbenchBlobText")
    should_expose_object_url = _js_arrow(bridge, "shouldExposeTargetObjectUrl")
    object_url_token = _js_arrow(bridge, "browserWorkbenchObjectUrlToken")
    install_object_url_shim = _js_arrow(bridge, "installTargetObjectUrlShim")
    is_blob_url_attribute = _js_arrow(bridge, "isBrowserWorkbenchBlobUrlAttribute")
    install_consumer_boundary = _js_arrow(bridge, "installTargetBlobUrlConsumerBoundary")
    to_proxy_http = _js_arrow(bridge, "toProxyHttp")
    program = "\n".join(
        [
            "let nativeSequence=0;const revoked=[];",
            "class TargetURL extends URL{}",
            "TargetURL.createObjectURL=()=>`blob:http://127.0.0.1:8788/native-${++nativeSequence}`;",
            "TargetURL.revokeObjectURL=value=>revoked.push(String(value));",
            "class FakeElement{constructor(localName='div'){this.localName=localName;this.attrs={};}setAttribute(name,value){this.attrs[name]=String(value);}getAttribute(name){return this.attrs[name]??null;}}",
            "class FakeImage extends FakeElement{constructor(){super('img');this._src='';}}",
            "class FakeBlob{constructor(type=''){this.type=type;}}",
            "class FakeFile extends FakeBlob{}",
            "class FakeStyle{constructor(){this.values={};this._background='';}setProperty(name,value){this.values[name]=String(value);}getPropertyValue(name){return this.values[name]||'';}}",
            "class FakeStyleSheet{insertRule(rule){this.inserted=String(rule);return 0;}replace(text){this.replaced=String(text);return Promise.resolve(this);}replaceSync(text){this.replacedSync=String(text);}}",
            "Object.defineProperty(FakeImage.prototype,'src',{configurable:true,enumerable:true,get(){return this._src;},set(value){this._src=String(value);}});",
            "const window={URL:TargetURL,Element:FakeElement,HTMLImageElement:FakeImage,CSSStyleDeclaration:FakeStyle,CSSStyleSheet:FakeStyleSheet,Blob:FakeBlob,File:FakeFile,crypto:{randomUUID:()=>`public-${nativeSequence}`}};",
            "const targetOrigin='http://localhost:3000';",
            "const targetUrl='http://localhost:3000/admin/media';",
            "const proxyPrefix='/browser-proxy/';",
            "const proxyTransportPath='_hermes/bw_upload/frame-upload/';",
            "const location={origin:'http://127.0.0.1:8788'};",
            "const browserWorkbenchBlobUrlAliases=new Map();",
            "const browserWorkbenchBlobUrlSources=new Map();",
            "const browserWorkbenchBlobUrlDisplayAliases=new Map();",
            resolve_blob_url,
            expose_blob_url,
            replace_blob_urls,
            resolve_blob_text,
            expose_blob_text,
            should_expose_object_url,
            object_url_token,
            install_object_url_shim,
            is_blob_url_attribute,
            install_consumer_boundary,
            to_proxy_http,
            "const objectInstalled=installTargetObjectUrlShim();",
            "const consumerInstalled=installTargetBlobUrlConsumerBoundary();",
            "const first=window.URL.createObjectURL(new window.File('image/png'));",
            "const second=window.URL.createObjectURL(new window.Blob('text/plain'));",
            "const moduleUrl=window.URL.createObjectURL(new window.Blob('text/javascript'));",
            "const firstNative=resolveBrowserWorkbenchBlobUrl(first);",
            "const image=new window.HTMLImageElement();image.src=first;",
            "const attributeImage=new window.HTMLImageElement();attributeImage.setAttribute('src',first);",
            "const attr=new window.Element();attr.setAttribute('data-preview',first);",
            "const styleAttr=new window.Element();styleAttr.setAttribute('style',`background-image:url(\"${first}\")`);",
            "const style=new window.CSSStyleDeclaration();style.background=`url(\"${first}\")`;style.setProperty('background-image',`url(\"${first}\")`);",
            "const sheet=new window.CSSStyleSheet();sheet.insertRule(`.inserted{background:url(\"${first}\")}`);sheet.replace(`.async{background:url(\"${first}\")}`);sheet.replaceSync(`.sync{background:url(\"${first}\")}`);",
            "const beforeRevoke={first,second,moduleUrl,moduleOrigin:new URL(moduleUrl).origin,firstOrigin:new URL(first).origin,firstNative,firstExposed:exposeBrowserWorkbenchBlobUrl(firstNative),fetchUrl:toProxyHttp(first),imageStored:image._src,imageVisible:image.src,attributeImageStored:attributeImage.attrs.src,attributeImageVisible:attributeImage.getAttribute('src'),attrStored:attr.attrs['data-preview'],attrVisible:attr.getAttribute('data-preview'),styleAttrStored:styleAttr.attrs.style,styleAttrVisible:styleAttr.getAttribute('style'),styleStored:style.values.background||'',styleVisible:style.background,stylePropertyStored:style.values['background-image'],stylePropertyVisible:style.getPropertyValue('background-image'),sheetInserted:sheet.inserted,sheetReplaced:sheet.replaced,sheetReplacedSync:sheet.replacedSync,unknown:resolveBrowserWorkbenchBlobUrl('blob:http://localhost:3000/unknown')};",
            "window.URL.revokeObjectURL(first);",
            "const afterFirstRevoke={resolved:resolveBrowserWorkbenchBlobUrl(first),imageVisible:image.src,attributeImageVisible:attributeImage.getAttribute('src'),styleVisible:style.background,styleAttrVisible:styleAttr.getAttribute('style'),aliases:browserWorkbenchBlobUrlAliases.size,sources:browserWorkbenchBlobUrlSources.size,displays:browserWorkbenchBlobUrlDisplayAliases.size,secondNative:resolveBrowserWorkbenchBlobUrl(second)};",
            "window.URL.revokeObjectURL(second);",
            "window.URL.revokeObjectURL(moduleUrl);",
            "console.log(JSON.stringify({objectInstalled,consumerInstalled,beforeRevoke,afterFirstRevoke,revoked,finalAliases:browserWorkbenchBlobUrlAliases.size,finalSources:browserWorkbenchBlobUrlSources.size,finalDisplays:browserWorkbenchBlobUrlDisplayAliases.size}));",
        ]
    )

    assert _run_node_json(program) == {
        "objectInstalled": True,
        "consumerInstalled": True,
        "beforeRevoke": {
            "first": "blob:http://localhost:3000/public-1",
            "second": "blob:http://localhost:3000/public-2",
            "moduleUrl": "blob:http://127.0.0.1:8788/native-3",
            "moduleOrigin": "http://127.0.0.1:8788",
            "firstOrigin": "http://localhost:3000",
            "firstNative": "blob:http://127.0.0.1:8788/native-1",
            "firstExposed": "blob:http://localhost:3000/public-1",
            "fetchUrl": "blob:http://127.0.0.1:8788/native-1",
            "imageStored": "blob:http://127.0.0.1:8788/native-1",
            "imageVisible": "blob:http://localhost:3000/public-1",
            "attributeImageStored": "blob:http://127.0.0.1:8788/native-1",
            "attributeImageVisible": "blob:http://localhost:3000/public-1",
            "attrStored": "blob:http://localhost:3000/public-1",
            "attrVisible": "blob:http://localhost:3000/public-1",
            "styleAttrStored": 'background-image:url("blob:http://127.0.0.1:8788/native-1")',
            "styleAttrVisible": 'background-image:url("blob:http://localhost:3000/public-1")',
            "styleStored": 'url("blob:http://127.0.0.1:8788/native-1")',
            "styleVisible": 'url("blob:http://localhost:3000/public-1")',
            "stylePropertyStored": 'url("blob:http://127.0.0.1:8788/native-1")',
            "stylePropertyVisible": 'url("blob:http://localhost:3000/public-1")',
            "sheetInserted": '.inserted{background:url("blob:http://127.0.0.1:8788/native-1")}',
            "sheetReplaced": '.async{background:url("blob:http://127.0.0.1:8788/native-1")}',
            "sheetReplacedSync": '.sync{background:url("blob:http://127.0.0.1:8788/native-1")}',
            "unknown": "blob:http://localhost:3000/unknown",
        },
        "afterFirstRevoke": {
            "resolved": "blob:http://localhost:3000/public-1",
            "imageVisible": "blob:http://localhost:3000/public-1",
            "attributeImageVisible": "blob:http://localhost:3000/public-1",
            "styleVisible": 'url("blob:http://localhost:3000/public-1")',
            "styleAttrVisible": 'background-image:url("blob:http://localhost:3000/public-1")',
            "aliases": 1,
            "sources": 1,
            "displays": 2,
            "secondNative": "blob:http://127.0.0.1:8788/native-2",
        },
        "revoked": [
            "blob:http://127.0.0.1:8788/native-1",
            "blob:http://127.0.0.1:8788/native-2",
            "blob:http://127.0.0.1:8788/native-3",
        ],
        "finalAliases": 0,
        "finalSources": 0,
        "finalDisplays": 2,
    }


def test_browser_workbench_iframe_bridge_keeps_app_router_urls_out_of_proxy_transport():
    bridge = browser_workbench._browser_proxy_bridge_script(
        "http://localhost:3000/en",
        session_id="bw_router",
        frame_id="frame-router",
    )
    to_proxy_http = _js_arrow(bridge, "toProxyHttp")
    proxy_native_anchor_navigation = _js_arrow(bridge, "proxyNativeAnchorNavigation")
    program = "\n".join(
        [
            "const targetUrl='http://localhost:3000/en';",
            "const targetOrigin='http://localhost:3000';",
            "const proxyPrefix='/browser-proxy/';",
            "const proxyTransportPath='_hermes/bw_router/frame-router/';",
            "const sessionId='bw_router';",
            "const frameId='frame-router';",
            "const location={origin:'http://127.0.0.1:8788',href:'http://127.0.0.1:8788/browser-proxy/_hermes/bw_router/frame-router/http://localhost:3000/en',pathname:'/browser-proxy/_hermes/bw_router/frame-router/http://localhost:3000/en',search:'',hash:'',assigned:[],assign(value){this.assigned.push(value);}};",
            "const initialProxyHref=location.href;",
            "const resolveBrowserWorkbenchBlobUrl=value=>value;",
            to_proxy_http,
            proxy_native_anchor_navigation,
            "class Anchor{constructor(href){this.href=href;}getAttribute(name){return name==='href'?this.href:'';}setAttribute(name,value){if(name==='href')this.href=String(value);}}",
            "const interceptedAnchor=new Anchor('/jp');const intercepted={defaultPrevented:true,preventDefault(){this.defaultPrevented=true;}};proxyNativeAnchorNavigation(intercepted,interceptedAnchor);",
            "const nativeAnchor=new Anchor('/en/product');const nativeEvent={defaultPrevented:false,preventDefault(){this.defaultPrevented=true;}};proxyNativeAnchorNavigation(nativeEvent,nativeAnchor);",
            "console.log(JSON.stringify({rscTransport:toProxyHttp('/en/product?_rsc=abc'),localeNavigation:toProxyHttp('/jp/browser-proxy/http://localhost:3000/en?_rsc=locale'),interceptedHref:interceptedAnchor.href,nativeHref:nativeAnchor.href,nativePrevented:nativeEvent.defaultPrevented,assigned:location.assigned}));",
        ]
    )

    assert _run_node_json(program) == {
        "rscTransport": "/browser-proxy/_hermes/bw_router/frame-router/http://localhost:3000/en/product?_rsc=abc",
        "localeNavigation": "/browser-proxy/_hermes/bw_router/frame-router/http://localhost:3000/jp?_rsc=locale",
        "interceptedHref": "/jp",
        "nativeHref": "/en/product",
        "nativePrevented": True,
        "assigned": [
            "/browser-proxy/_hermes/bw_router/frame-router/http://localhost:3000/en/product"
        ],
    }


def test_browser_workbench_iframe_bridge_routes_blank_navigation_to_parent():
    bridge = browser_workbench._browser_proxy_bridge_script(
        "http://localhost:3000/en/start",
        session_id="bw_new_tab",
        frame_id="frame-new-tab",
    )
    target_url_from_proxy_href = _js_arrow(bridge, "targetUrlFromProxyHref")
    new_tab_url = _js_arrow(bridge, "browserWorkbenchNewTabUrl")
    request_new_tab = _js_arrow(bridge, "requestBrowserWorkbenchNewTab")
    intercept_new_tab = _js_arrow(bridge, "interceptBrowserWorkbenchNewTab")
    program = "\n".join(
        [
            "const targetUrl='http://localhost:3000/en/start';",
            "const targetOrigin='http://localhost:3000';",
            "const proxyPrefix='/browser-proxy/';",
            "const proxyTransportPath='_hermes/bw_new_tab/frame-new-tab/';",
            "const location={origin:'http://127.0.0.1:8788',href:'http://127.0.0.1:8788/browser-proxy/_hermes/bw_new_tab/frame-new-tab/http://localhost:3000/en/start'};",
            "const initialProxyHref=location.href;const sent=[];const post=payload=>sent.push(payload);",
            target_url_from_proxy_href,
            "const currentTargetUrl=()=>targetUrlFromProxyHref(location.href)||targetUrl;",
            new_tab_url,
            request_new_tab,
            intercept_new_tab,
            "const valid={prevented:false,preventDefault(){this.prevented=true;}};",
            "const invalid={prevented:false,preventDefault(){this.prevented=true;}};",
            "interceptBrowserWorkbenchNewTab(valid,'../docs?from=workbench');",
            "interceptBrowserWorkbenchNewTab(invalid,'javascript:alert(1)');",
            "requestBrowserWorkbenchNewTab('https://example.test/reference');",
            "requestBrowserWorkbenchNewTab('https://user:secret@example.test/private');",
            "['data:text/plain,no','blob:http://localhost:3000/id','mailto:test@example.test','tel:+15551234567','about:blank','http://[::1',''].forEach(requestBrowserWorkbenchNewTab);",
            "requestBrowserWorkbenchNewTab('https://example.test/'+('a'.repeat(5000)));",
            "console.log(JSON.stringify({validPrevented:valid.prevented,invalidPrevented:invalid.prevented,sent}));",
        ]
    )

    assert _run_node_json(program) == {
        "validPrevented": True,
        "invalidPrevented": True,
        "sent": [
            {"type": "open-tab", "url": "http://localhost:3000/docs?from=workbench"},
            {"type": "open-tab", "url": "https://example.test/reference"},
        ],
    }
    assert "window.open = function" in bridge


def test_browser_workbench_iframe_bridge_materializes_next_rsc_navigations():
    bridge = browser_workbench._browser_proxy_bridge_script(
        "http://localhost:3000/admin?locale=ko",
        session_id="bw_router",
        frame_id="frame-router",
    )
    target_url_from_proxy_href = _js_arrow(bridge, "targetUrlFromProxyHref")
    rsc_document_navigation_target = _js_arrow(bridge, "rscDocumentNavigationTarget")
    rsc_document_navigation_commit_target = _js_arrow(
        bridge, "rscDocumentNavigationCommitTarget"
    )
    current_proxy = (
        "http://127.0.0.1:8788/browser-proxy/_hermes/bw_router/frame-router/"
        "http://localhost:3000/admin?locale=ko"
    )
    program = "\n".join(
        [
            "const targetUrl='http://localhost:3000/admin?locale=ko';",
            "const targetOrigin='http://localhost:3000';",
            "const proxyPrefix='/browser-proxy/';",
            "const proxyTransportPath='_hermes/bw_router/frame-router/';",
            f"const currentProxy={json.dumps(current_proxy)};",
            "const parsedLocation=new URL(currentProxy);",
            "const location={href:currentProxy,origin:parsedLocation.origin};",
            "const initialProxyHref=currentProxy;",
            target_url_from_proxy_href,
            "const currentTargetUrl=()=>targetUrlFromProxyHref(location.href)||targetUrl;",
            "const requestUrlOf=input=>typeof input==='string'?input:(input instanceof URL?input.href:(input&&input.url)||'');",
            "const methodOf=(input,init,fallback)=>String((init&&init.method)||(input&&input.method)||fallback||'GET').toUpperCase();",
            rsc_document_navigation_target,
            rsc_document_navigation_commit_target,
            "const rsc={headers:{RSC:'1'}};",
            "const response={ok:true,headers:{get:name=>name==='content-type'?'text/x-component; charset=utf-8':''}};",
            "const localeKo=rscDocumentNavigationTarget('http://127.0.0.1:8788/admin?locale=ko&_rsc=old',rsc);",
            "const localeEn=rscDocumentNavigationTarget('http://127.0.0.1:8788/admin?locale=en&_rsc=new',rsc);",
            "console.log(JSON.stringify({",
            "locale:localeEn,",
            "route:rscDocumentNavigationTarget('http://127.0.0.1:8788/admin/member-record?locale=ko&_rsc=flight',rsc),",
            "same:localeKo,",
            "prefetch:rscDocumentNavigationTarget('http://127.0.0.1:8788/admin?locale=en&_rsc=flight',{headers:{RSC:'1','Next-Router-Prefetch':'1'}}),",
            "purposePrefetch:rscDocumentNavigationTarget('http://127.0.0.1:8788/admin?locale=en&_rsc=flight',{headers:{RSC:'1',Purpose:'prefetch'}}),",
            "secPurposePrefetch:rscDocumentNavigationTarget('http://127.0.0.1:8788/admin?locale=en&_rsc=flight',{headers:{RSC:'1','Sec-Purpose':'prefetch'}}),",
            "post:rscDocumentNavigationTarget('http://127.0.0.1:8788/admin?locale=en&_rsc=flight',{method:'POST',headers:{RSC:'1'}}),",
            "ordinary:rscDocumentNavigationTarget('http://127.0.0.1:8788/admin?locale=en',{headers:{Accept:'text/html'}}),",
            "external:rscDocumentNavigationTarget('https://example.test/admin?locale=en&_rsc=flight',rsc),",
            "stale:rscDocumentNavigationCommitTarget(localeKo,1,2,targetUrl,response,false),",
            "latest:rscDocumentNavigationCommitTarget(localeEn,2,2,targetUrl,response,false),",
            "refresh:rscDocumentNavigationCommitTarget(localeKo,2,2,targetUrl,response,false),",
            "redirecting:rscDocumentNavigationCommitTarget(localeEn,2,2,targetUrl,response,true),",
            "html:rscDocumentNavigationCommitTarget(localeEn,2,2,targetUrl,{ok:true,headers:{get:()=> 'text/html'}},false)",
            "}));",
        ]
    )

    assert _run_node_json(program) == {
        "locale": "http://localhost:3000/admin?locale=en",
        "route": "http://localhost:3000/admin/member-record?locale=ko",
        "same": "http://localhost:3000/admin?locale=ko",
        "prefetch": "",
        "purposePrefetch": "",
        "secPurposePrefetch": "",
        "post": "",
        "ordinary": "",
        "external": "",
        "stale": "",
        "latest": "http://localhost:3000/admin?locale=en",
        "refresh": "",
        "redirecting": "",
        "html": "",
    }


def test_browser_workbench_iframe_bridge_marks_dynamic_opaque_sandbox_frames():
    bridge = browser_workbench._browser_proxy_bridge_script(
        "http://localhost:3000/editor",
        session_id="bw_nested",
        frame_id="frame-main",
    )
    to_proxy_http = _js_arrow(bridge, "toProxyHttp")
    proxy_transport_for_frame = _js_arrow(bridge, "proxyTransportForFrame")
    is_opaque_sandboxed_frame = _js_arrow(bridge, "isOpaqueSandboxedFrame")
    proxy_opaque_frame_source = _js_arrow(bridge, "proxyOpaqueFrameSource")
    prepare_opaque_sandboxed_frame = _js_arrow(bridge, "prepareOpaqueSandboxedFrame")
    program = "\n".join(
        [
            "const proxyPrefix='/browser-proxy/';",
            "const proxyTransportPath='_hermes/bw_nested/frame-main/';",
            "const opaqueFrameSuffix='~opaque';",
            "const sessionId='bw_nested';",
            "const frameId='frame-main';",
            "const targetUrl='http://localhost:3000/editor';",
            "const targetOrigin='http://localhost:3000';",
            "const location={origin:'http://127.0.0.1:8788'};",
            "const resolveBrowserWorkbenchBlobUrl=value=>value;",
            to_proxy_http,
            proxy_transport_for_frame,
            is_opaque_sandboxed_frame,
            proxy_opaque_frame_source,
            prepare_opaque_sandboxed_frame,
            "const makeFrame=(sandbox,src)=>({tagName:'IFRAME',attrs:{sandbox,src},hasAttribute(name){return Object.prototype.hasOwnProperty.call(this.attrs,name);},getAttribute(name){return this.attrs[name]??null;},setAttribute(name,value){this.attrs[name]=String(value);}});",
            "const opaque=makeFrame('allow-scripts allow-forms','/preview');",
            "const sameOrigin=makeFrame('allow-scripts allow-same-origin','/hydrated');",
            "const first=prepareOpaqueSandboxedFrame(opaque);",
            "const second=prepareOpaqueSandboxedFrame(opaque);",
            "const safe=prepareOpaqueSandboxedFrame(sameOrigin);",
            "console.log(JSON.stringify({first,second,safe,opaque:opaque.attrs.src,sameOrigin:sameOrigin.attrs.src}));",
        ]
    )

    assert _run_node_json(program) == {
        "first": True,
        "second": False,
        "safe": False,
        "opaque": (
            "/browser-proxy/_hermes/bw_nested/frame-main~opaque/"
            "http://localhost:3000/preview"
        ),
        "sameOrigin": "/hydrated",
    }


@pytest.mark.parametrize(
    "nested_target",
    [
        "http://localhost:3000/jp/browser-proxy/http://localhost:3000/en",
        "http://localhost:3000/jp/browser-proxy/http:/localhost:3000/en",
        "http://localhost:3000/jp/browser-proxy/_hermes/bw_router/frame-router/http://localhost:3000/en",
    ],
)
def test_browser_workbench_proxy_backend_never_forwards_nested_transport_path(nested_target):
    proxy_url = browser_workbench._browser_proxy_url_for_target(
        nested_target,
        session_id="bw_router",
        frame_id="frame-router",
    )

    assert browser_workbench._browser_proxy_target_from_route(_parsed(proxy_url)) == (
        "http://localhost:3000/jp"
    )


def test_browser_workbench_proxy_backend_rejects_nested_transport_for_another_origin():
    proxy_url = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/jp/browser-proxy/http://example.test/en",
        session_id="bw_router",
        frame_id="frame-router",
    )

    with pytest.raises(ValueError, match="nested browser proxy target origin is invalid"):
        browser_workbench._browser_proxy_target_from_route(_parsed(proxy_url))


def test_browser_workbench_iframe_bridge_hardens_dynamic_native_forms():
    bridge = browser_workbench._browser_proxy_bridge_script(
        "http://localhost:3000/admin/login",
        session_id="bw_login",
        frame_id="login-frame",
    )
    to_proxy_http = _js_arrow(bridge, "toProxyHttp")
    prepare_form = _js_arrow(bridge, "prepareProxyFormSubmission")
    program = "\n".join(
        [
            "const targetUrl='http://localhost:3000/admin/login';",
            "const targetOrigin='http://localhost:3000';",
            "const proxyPrefix='/browser-proxy/';",
            "const proxyTransportPath='_hermes/bw_login/login-frame/';",
            "const sessionId='bw_login';",
            "const frameId='login-frame';",
            "const location={origin:'http://127.0.0.1:8788'};",
            "class Control{constructor(attrs={}){this.attrs={...attrs};this.disabled=false;this.value=attrs.value||'';this.name=attrs.name||'';}getAttribute(name){return this.attrs[name]||'';}setAttribute(name,value){this.attrs[name]=String(value);if(name==='name')this.name=String(value);}}",
            "class Form extends Control{constructor({password=false,attrs={},controls=[]}={}){super(attrs);this.password=password;this.controls=controls;this.action='';}querySelector(selector){return this.password&&selector.includes('password')?{}:null;}querySelectorAll(selector){const match=selector.match(/name=\\\"([^\\\"]+)/);return match?this.controls.filter((control)=>control.name===match[1]):[];}appendChild(control){this.controls.push(control);}}",
            "const document={createElement:()=>new Control()};",
            "const resolveBrowserWorkbenchBlobUrl=value=>value;",
            to_proxy_http,
            prepare_form,
            "const login=new Form({password:true});const loginButton=new Control({formmethod:'get'});prepareProxyFormSubmission(login,loginButton);",
            "const hostile=new Control({name:'__hermes_bw_session',value:'attacker'});const search=new Form({attrs:{method:'get',action:'/search'},controls:[hostile]});prepareProxyFormSubmission(search,null);",
            "console.log(JSON.stringify({loginMethod:loginButton.getAttribute('formmethod'),loginAction:login.getAttribute('action'),loginMetadata:login.controls.map((control)=>({name:control.name,value:control.value,disabled:control.disabled})),searchAction:search.getAttribute('action'),hostileDisabled:hostile.disabled,searchMetadata:search.controls.map((control)=>({name:control.name,value:control.value,disabled:control.disabled}))}));",
        ]
    )

    assert _run_node_json(program) == {
        "loginMethod": "post",
        "loginAction": "/browser-proxy/_hermes/bw_login/login-frame/http://localhost:3000/admin/login",
        "loginMetadata": [],
        "searchAction": "/browser-proxy/_hermes/bw_login/login-frame/http://localhost:3000/search",
        "hostileDisabled": False,
        "searchMetadata": [
            {"name": "__hermes_bw_session", "value": "attacker", "disabled": False},
        ],
    }


def test_browser_workbench_proxy_recovers_runtime_asset_from_live_proxy_referrer(monkeypatch):
    captured = {}
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/admin/login"}
    )
    assert status == 200

    class _FakeProxyResponse:
        status = 200
        headers = {"Content-Type": "application/javascript; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            return b"self.__next_chunk_loaded__ = true;"

    def fake_open(_self, request, timeout=None):
        captured["url"] = request.full_url
        return _FakeProxyResponse()

    monkeypatch.setattr(browser_workbench.urllib.request.OpenerDirector, "open", fake_open)
    proxy_page = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/admin/login",
        session_id=session["session_id"],
        frame_id="login-frame",
    )
    handler = _FakeHandler(
        headers={
            "Host": "127.0.0.1:8788",
            "Referer": f"http://127.0.0.1:8788{proxy_page}",
            "Sec-Fetch-Dest": "script",
        }
    )

    assert routes.handle_get(handler, _parsed("/_next/static/chunks/app.js?build=dev")) is True

    assert handler.status == 200
    assert captured["url"] == "http://localhost:3000/_next/static/chunks/app.js?build=dev"
    assert handler.wfile.getvalue() == b"self.__next_chunk_loaded__ = true;"

    document_request = _FakeHandler(
        headers={
            "Host": "127.0.0.1:8788",
            "Referer": f"http://127.0.0.1:8788{proxy_page}",
            "Sec-Fetch-Dest": "document",
        }
    )
    assert browser_workbench.recover_browser_workbench_proxy_subresource(
        document_request,
        _parsed("/_next/static/chunks/rejected.js"),
    ) is None

    for headers in (
        {
            "Host": "127.0.0.1:8788",
            "Referer": f"http://attacker.invalid{proxy_page}",
            "Sec-Fetch-Dest": "script",
        },
    ):
        rejected = _FakeHandler(headers=headers)
        assert routes.handle_get(rejected, _parsed("/_next/static/chunks/rejected.js")) is False


def test_browser_workbench_proxy_rewrites_nested_assets_in_recovered_stylesheet(monkeypatch):
    captured = {}
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/en/service"}
    )
    assert status == 200

    class _FakeProxyResponse:
        status = 200
        headers = {"Content-Type": "text/css; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, limit=-1):
            return (
                b"@font-face{font-family:magistral_medium;"
                b"src:url('/fonts/magistral/magistral_cond-medium.woff2') format('woff2')}"
            )

    def fake_open(_self, request, timeout=None):
        captured["url"] = request.full_url
        return _FakeProxyResponse()

    monkeypatch.setattr(browser_workbench.urllib.request.OpenerDirector, "open", fake_open)
    proxy_page = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/en/service",
        session_id=session["session_id"],
        frame_id="service-frame",
    )
    handler = _FakeHandler(
        headers={
            "Host": "127.0.0.1:8788",
            "Referer": f"http://127.0.0.1:8788{proxy_page}",
            "Sec-Fetch-Dest": "style",
        }
    )

    assert routes.handle_get(handler, _parsed("/_next/static/chunks/service.css")) is True

    expected_font_url = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/fonts/magistral/magistral_cond-medium.woff2",
        session_id=session["session_id"],
        frame_id="service-frame",
    )
    assert handler.status == 200
    assert captured["url"] == "http://localhost:3000/_next/static/chunks/service.css"
    assert f"url('{expected_font_url}')" in handler.wfile.getvalue().decode("utf-8")


@pytest.mark.parametrize(
    "request_headers",
    [
        {"Sec-Fetch-Dest": "document"},
        {"Sec-Fetch-Dest": "iframe"},
        {"Accept": "text/html,application/xhtml+xml"},
    ],
)
def test_browser_workbench_proxy_redirects_clean_document_navigation_from_live_proxy_referrer(request_headers):
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/admin/login"}
    )
    assert status == 200
    proxy_page = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/admin/login",
        session_id=session["session_id"],
        frame_id="login-frame",
    )
    handler = _FakeHandler(
        headers={
            "Host": "127.0.0.1:8788",
            "Referer": f"http://127.0.0.1:8788{proxy_page}",
            **request_headers,
        }
    )

    assert routes.handle_get(handler, _parsed("/admin")) is True

    assert handler.status == 307
    assert dict(handler.response_headers)["Location"] == browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/admin",
        session_id=session["session_id"],
        frame_id="login-frame",
    )
    assert handler.wfile.getvalue() == b""


def test_browser_workbench_proxy_does_not_redirect_document_without_live_same_origin_referrer():
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/admin/login"}
    )
    assert status == 200
    proxy_page = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/admin/login",
        session_id=session["session_id"],
        frame_id="login-frame",
    )

    attacker = _FakeHandler(
        headers={
            "Host": "127.0.0.1:8788",
            "Referer": f"http://attacker.invalid{proxy_page}",
            "Sec-Fetch-Dest": "iframe",
        }
    )
    assert routes.handle_get(attacker, _parsed("/admin")) is False

    browser_workbench.close_browser_workbench_session(session["session_id"])
    stale = _FakeHandler(
        headers={
            "Host": "127.0.0.1:8788",
            "Referer": f"http://127.0.0.1:8788{proxy_page}",
            "Sec-Fetch-Dest": "iframe",
        }
    )
    assert routes.handle_get(stale, _parsed("/admin")) is False


def test_browser_workbench_proxy_does_not_recover_an_explicit_proxy_asset_twice(monkeypatch):
    captured = {}
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/admin/login"}
    )
    assert status == 200

    class _FakeProxyResponse:
        status = 200
        headers = {"Content-Type": "application/javascript; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def geturl(self):
            return captured["url"]

        def read(self, limit=-1):
            return b"self.__next_chunk_loaded__ = true;"

    def fake_open(_self, request, timeout=None):
        captured["url"] = request.full_url
        return _FakeProxyResponse()

    monkeypatch.setattr(browser_workbench.urllib.request.OpenerDirector, "open", fake_open)
    proxy_page = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/admin/login",
        session_id=session["session_id"],
        frame_id="login-frame",
    )
    proxy_asset = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/_next/static/chunks/app.js",
        session_id=session["session_id"],
        frame_id="login-frame",
    )
    handler = _FakeHandler(
        headers={
            "Host": "127.0.0.1:8788",
            "Referer": f"http://127.0.0.1:8788{proxy_page}",
            "Sec-Fetch-Dest": "script",
        }
    )

    assert routes.handle_get(handler, _parsed(proxy_asset)) is True

    assert handler.status == 200
    assert captured["url"] == "http://localhost:3000/_next/static/chunks/app.js"


def test_browser_workbench_proxy_recovery_does_not_claim_webui_chii_route():
    session, status = browser_workbench.create_or_attach_browser_workbench_session(
        {"url": "http://localhost:3000/admin/login"}
    )
    assert status == 200
    proxy_page = browser_workbench._browser_proxy_url_for_target(
        "http://localhost:3000/admin/login",
        session_id=session["session_id"],
        frame_id="login-frame",
    )
    handler = _FakeHandler(
        headers={
            "Host": "127.0.0.1:8788",
            "Referer": f"http://127.0.0.1:8788{proxy_page}",
            "Sec-Fetch-Dest": "script",
        }
    )

    recovered = browser_workbench.recover_browser_workbench_proxy_subresource(
        handler,
        _parsed("/api/browser-workbench/chii/target.js?session_id=" + session["session_id"]),
    )

    assert recovered is None


def test_browser_workbench_proxy_leaves_ssr_form_attributes_for_bridge_submission():
    rewritten = browser_workbench._browser_proxy_rewrite_html(
        """<html><head></head><body>
        <form method=" GET "><input name="email"><input type="password" name="password"><button formmethod=" GET ">Sign in</button></form>
        <form action=/search method="get"><input type="hidden" name="__hermes_bw_session" value="attacker"><input name="q"><button>Search</button></form>
        </body></html>""",
        "http://localhost:3000/admin/login",
        session_id="bw_login",
        frame_id="login-frame",
    )
    parser = _FormProbe()
    parser.feed(rewritten)

    login_form, search_form = parser.forms
    assert login_form["attrs"]["method"].strip().lower() == "get"
    assert "action" not in login_form["attrs"]
    assert not any(field.get("name") == "__hermes_bw_session" for field in login_form["inputs"])

    hidden_fields = {field.get("name"): field.get("value") for field in search_form["inputs"]}
    assert any(control.get("formmethod", "").strip().lower() == "get" for control in login_form["controls"])
    assert search_form["attrs"]["method"].lower() == "get"
    assert search_form["attrs"]["action"] == "/search"
    assert hidden_fields["__hermes_bw_session"] == "attacker"
    assert "__hermes_bw_frame" not in hidden_fields


def test_browser_workbench_proxy_login_shares_and_restores_profile_cookies(tmp_path, monkeypatch):
    requests = []

    class TargetHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
            requests.append(
                (
                    "POST",
                    self.path,
                    self.headers.get("Cookie", ""),
                    self.headers.get("Origin", ""),
                    body.decode(),
                )
            )
            if self.path != "/api/auth/login":
                self.send_error(404)
                return
            expected_origin = f"http://127.0.0.1:{self.server.server_address[1]}"
            if self.headers.get("Origin") != expected_origin:
                self.send_error(403)
                return
            self.send_response(302)
            self.send_header("Location", "/dashboard")
            self.send_header("Set-Cookie", "target_session=authenticated; Path=/; HttpOnly")
            self.end_headers()

        def do_GET(self):
            cookie = self.headers.get("Cookie", "")
            requests.append(("GET", self.path, cookie, self.headers.get("Origin", ""), ""))
            authenticated = "target_session=authenticated" in cookie
            body = (
                b'<html><head><title>Dashboard</title></head><body>Authenticated dashboard<a href="settings">Settings</a></body></html>'
                if authenticated
                else b"<html><head><title>Login</title></head><body>Missing target cookie</body></html>"
            )
            self.send_response(200 if authenticated else 401)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        first, first_status = browser_workbench.create_or_attach_browser_workbench_session({"url": origin})
        second, second_status = browser_workbench.create_or_attach_browser_workbench_session({"url": origin})
        assert first_status == second_status == 200

        login_url = browser_workbench._browser_proxy_url_for_target(
            f"{origin}/api/auth/login",
            session_id=first["session_id"],
            frame_id="login-frame",
        )
        login = _FakeHandler(
            b"email=user%40example.test&password=secret",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert routes.handle_post(login, _parsed(login_url)) is True
        login_body = login.wfile.getvalue().decode()
        login_headers = {key.lower(): value for key, value in login.response_headers}

        assert login.status == 200
        assert "Authenticated dashboard" in login_body
        assert login_headers["x-hermes-browser-proxy-target"] == f"{origin}/dashboard"
        assert f'const targetUrl = "{origin}/dashboard"' in login_body
        assert 'href="settings"' in login_body
        assert "hermes-browser-workbench-proxy-bridge" in login_body
        assert requests[:2] == [
            ("POST", "/api/auth/login", "", origin, "email=user%40example.test&password=secret"),
            ("GET", "/dashboard", "target_session=authenticated", origin, ""),
        ]
        cookie_jar_path = tmp_path / "webui-state" / "browser-workbench" / "cookies.txt"
        assert cookie_jar_path.is_file()
        assert cookie_jar_path.stat().st_mode & 0o777 == 0o600

        second_tab_url = browser_workbench._browser_proxy_url_for_target(
            f"{origin}/dashboard",
            session_id=second["session_id"],
            frame_id="second-tab-frame",
        )
        second_tab = _FakeHandler()
        assert routes.handle_get(second_tab, _parsed(second_tab_url)) is True
        assert second_tab.status == 200
        assert "Authenticated dashboard" in second_tab.wfile.getvalue().decode()

        other_origin = f"http://localhost:{server.server_address[1]}"
        other, other_status = browser_workbench.create_or_attach_browser_workbench_session({"url": other_origin})
        assert other_status == 200
        other_origin_url = browser_workbench._browser_proxy_url_for_target(
            f"{other_origin}/dashboard",
            session_id=other["session_id"],
            frame_id="other-origin-frame",
        )
        other_origin_request = _FakeHandler()
        assert routes.handle_get(other_origin_request, _parsed(other_origin_url)) is True
        assert other_origin_request.status == 401
        assert "Missing target cookie" in other_origin_request.wfile.getvalue().decode()

        authenticated = _FakeHandler()
        authenticated_url = browser_workbench._browser_proxy_url_for_target(
            f"{origin}/dashboard",
            session_id=first["session_id"],
            frame_id="authenticated-frame",
        )
        assert routes.handle_get(authenticated, _parsed(authenticated_url)) is True
        assert authenticated.status == 200
        assert "Authenticated dashboard" in authenticated.wfile.getvalue().decode()

        _, clear_status = browser_workbench.clear_browser_workbench_cookies(second["session_id"])
        assert clear_status == 200
        after_clear = _FakeHandler()
        assert routes.handle_get(after_clear, _parsed(authenticated_url)) is True
        assert after_clear.status == 401
        assert "Missing target cookie" in after_clear.wfile.getvalue().decode()

        browser_workbench.reset_browser_workbench_sessions_for_tests()
        cleared, cleared_status = browser_workbench.create_or_attach_browser_workbench_session({"url": origin})
        assert cleared_status == 200
        cleared_dashboard_url = browser_workbench._browser_proxy_url_for_target(
            f"{origin}/dashboard",
            session_id=cleared["session_id"],
            frame_id="cleared-restart-frame",
        )
        cleared_after_restart = _FakeHandler()
        assert routes.handle_get(cleared_after_restart, _parsed(cleared_dashboard_url)) is True
        assert cleared_after_restart.status == 401
        assert "Missing target cookie" in cleared_after_restart.wfile.getvalue().decode()

        restarted_login_url = browser_workbench._browser_proxy_url_for_target(
            f"{origin}/api/auth/login",
            session_id=cleared["session_id"],
            frame_id="restarted-login-frame",
        )
        relogin = _FakeHandler(
            b"email=user%40example.test&password=secret",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert routes.handle_post(relogin, _parsed(restarted_login_url)) is True
        assert relogin.status == 200
        assert "Authenticated dashboard" in relogin.wfile.getvalue().decode()

        companion, companion_status = browser_workbench.create_or_attach_browser_workbench_session({"url": origin})
        assert companion_status == 200
        companion_url = browser_workbench._browser_proxy_url_for_target(
            f"{origin}/dashboard",
            session_id=companion["session_id"],
            frame_id="companion-frame",
        )
        request_count_before_close = len(requests)
        browser_workbench.close_browser_workbench_session(cleared["session_id"])
        after_close = _FakeHandler()
        assert routes.handle_get(after_close, _parsed(companion_url)) is True
        assert after_close.status == 200
        assert "Authenticated dashboard" in after_close.wfile.getvalue().decode()

        closed_tab = _FakeHandler()
        closed_session_url = browser_workbench._browser_proxy_url_for_target(
            f"{origin}/dashboard",
            session_id=cleared["session_id"],
            frame_id="closed-frame",
        )
        assert routes.handle_get(closed_tab, _parsed(closed_session_url)) is True
        assert closed_tab.status == 404
        assert len(requests) == request_count_before_close + 1

        browser_workbench.reset_browser_workbench_sessions_for_tests()
        restored, restored_status = browser_workbench.create_or_attach_browser_workbench_session({"url": origin})
        assert restored_status == 200
        restored_url = browser_workbench._browser_proxy_url_for_target(
            f"{origin}/dashboard",
            session_id=restored["session_id"],
            frame_id="restored-frame",
        )
        restored_request = _FakeHandler()
        assert routes.handle_get(restored_request, _parsed(restored_url)) is True
        assert restored_request.status == 200
        assert "Authenticated dashboard" in restored_request.wfile.getvalue().decode()
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def test_browser_workbench_iframe_proxy_returns_diagnostic_page_on_fetch_error(monkeypatch):
    session, status = browser_workbench.create_or_attach_browser_workbench_session({"url": "http://127.0.0.1:9"})
    assert status == 200

    def fake_open(_self, request, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(browser_workbench.urllib.request.OpenerDirector, "open", fake_open)
    handler = _FakeHandler()
    proxy_url = browser_workbench._browser_proxy_url_for_target(
        "http://127.0.0.1:9",
        session_id=session["session_id"],
        frame_id="diagnostic-frame",
    )

    assert routes.handle_get(handler, _parsed(proxy_url)) is True

    body = handler.wfile.getvalue().decode("utf-8")
    assert handler.status == 502
    assert "This page could not be opened" in body
    assert "connection refused" in body


def test_browser_workbench_iframe_proxy_requires_a_live_session():
    handler = _FakeHandler()

    assert routes.handle_get(handler, _parsed("/browser-proxy/http://127.0.0.1:3000")) is True

    assert handler.status == 404
    body = handler.wfile.getvalue().decode("utf-8")
    response_headers = {key.lower(): value for key, value in handler.response_headers}
    assert "session not found" in body.lower()
    assert response_headers["content-type"] == "text/html; charset=utf-8"
    assert "frame-ancestors 'self'" in response_headers["content-security-policy"]
    assert response_headers.get("x-frame-options", "").upper() != "DENY"


def test_browser_workbench_cdp_backend_uses_chromium_stream_for_loopback(monkeypatch):
    monkeypatch.setattr(routes, "_check_csrf", lambda handler: True)
    monkeypatch.setenv("HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER", "cdp-browser")
    monkeypatch.setattr(browser_workbench, "_browser_binary_path", lambda environ=None: "/tmp/fake-browser")
    prepared_targets = []

    def _target_for_session(self, session_id, url):
        prepared_targets.append((session_id, url))
        return "ws://127.0.0.1/devtools/page/fake"

    monkeypatch.setattr(browser_workbench.CdpBrowserWorkbenchBackend, "_target_for_session", _target_for_session)

    handler = _FakeHandler(
        {
            "url": "http://localhost:3000",
            "client_renderer": "chromium-stream",
        }
    )

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

    assert routes.handle_post(handler, _parsed("/api/browser-workbench/session")) is None
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
    assert body["bridge_url"].startswith("/browser-proxy/_hermes/bw_")
    assert body["bridge_url"].endswith("/http://localhost:3000")
    assert "__hermes_bw_" not in _parsed(body["bridge_url"]).query
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
    assert devtools_body["chii_devtools"]["target_id"] == browser_workbench._chii_target_id_for_session(session_id)
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



def test_browser_workbench_static_shell_is_wired_for_web_renderers():
    index = open("static/index.html", encoding="utf-8").read()
    js = open("static/browser_workbench.js", encoding="utf-8").read()
    css = open("static/style.css", encoding="utf-8").read()

    assert 'id="browserWorkbenchTabs"' in index
    assert 'id="workbenchOpenBrowser"' in index
    assert 'id="mainBrowser"' in index
    assert 'id="browserWorkbenchUrl"' in index
    assert 'id="browserWorkbenchReload"' in index
    assert 'id="browserWorkbenchPing"' in index
    assert 'id="browserWorkbenchMenu"' in index
    assert 'static/browser_workbench.js?v=__WEBUI_VERSION__' in index

    assert "function openBrowserWorkbenchTab" in js
    assert "function closeBrowserWorkbenchTab" in js
    assert "function navigateBrowserWorkbenchToUrl" in js
    assert "function navigateBrowserWorkbenchHistory" in js
    assert "function renderBrowserWorkbenchFrame" in js
    assert "function renderBrowserWorkbenchChromiumStream" in js
    assert "function renderBrowserWorkbenchSplitView" in js
    assert "client_renderer:browserWorkbenchPreferredClientRenderer()" in js
    assert "document.createElement('iframe')" in js
    assert "frame.referrerPolicy='same-origin';" in js
    assert "document.createElement('canvas')" in js
    assert "renderer==='iframe-bridge'" in js
    assert "renderer==='chromium-stream'" in js
    assert "window.openBrowserWorkbenchTab" in js
    assert "window.navigateBrowserWorkbenchHistory" in js
    assert "const WORKBENCH_STORAGE_KEY='hermes-browser-workbench-tabs:v1'" in js

    assert ".browser-workbench-shell" in css
    assert ".browser-workbench-frame" in css
    assert ".browser-workbench-stream-canvas" in css
    assert ".browser-workbench-devtools-frame" in css
    assert ".browser-workbench-split-wrap" in css




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
