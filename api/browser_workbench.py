"""Browser Workbench capability and lifecycle route handlers.

The Browser Workbench is the embedded browser panel for Hermes WebUI. Routes
expose a default-on lifecycle/navigation shell plus a CDP
control backend when a local Chromium-family browser is available. Public
payloads intentionally stay scoped and sanitized so raw CDP/debugger endpoints
never leak to the browser UI. The embedded viewport intentionally does not use
base64 screenshot/image fallbacks; renderers must be real iframe/native/streamed
browser surfaces.
"""

from __future__ import annotations

import base64
import atexit
import contextlib
import html
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import socket
import sys
import struct
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit
from xml.sax.saxutils import escape as _xml_escape

from api.helpers import bad, j

_FULL_BACKEND_CAPABILITIES = {
    "session_lifecycle": False,
    "navigation": False,
    "stop_loading": False,
    "interactive_viewport": False,
    "inspect": False,
    "console": False,
    "network": False,
    "screenshot_crop": False,
    "iframe_bridge": False,
    "agent_input": False,
    "native_devtools": False,
    "chii_devtools": False,
    "docked_devtools": False,
    "popout_devtools": False,
}
_SESSION_SHELL_CAPABILITIES = {
    **_FULL_BACKEND_CAPABILITIES,
    "session_lifecycle": True,
    "navigation": True,
    "stop_loading": True,
    "iframe_bridge": True,
    "chii_devtools": True,
    "docked_devtools": True,
    "popout_devtools": True,
}
_CDP_BROWSER_CAPABILITIES = {
    **_SESSION_SHELL_CAPABILITIES,
    "interactive_viewport": True,
    "screenshot_crop": True,
    "inspect": True,
    "native_devtools": True,
    "chii_devtools": False,
    "docked_devtools": True,
    "popout_devtools": False,
}
_DEFAULT_VIEWPORT = {"width": 1440, "height": 900, "device_pixel_ratio": 1}
_SESSION_PREFIX = "bw_"
_MAX_CONTEXT_TEXT_LENGTH = 500

_UNAVAILABLE_MESSAGE = "Browser is disabled."
_LIMITED_MESSAGE = "Browser is ready."
_DISABLED_MESSAGE = "Browser is disabled."
_ALLOWED_SCHEMES = {"http", "https"}
_MAX_BROWSER_URL_LENGTH = 4096
_BROWSER_PROXY_PREFIX = "/browser-proxy/"
_BROWSER_PROXY_CONTEXT_PREFIX = "_hermes/"
_BROWSER_PROXY_OPAQUE_FRAME_SUFFIX = "~opaque"
_BROWSER_PROXY_TIMEOUT_SECONDS = 15
_BROWSER_PROXY_MAX_BODY_BYTES = 25 * 1024 * 1024
_BROWSER_PROXY_COOKIE_JAR_RELATIVE_PATH = Path("browser-workbench") / "cookies.txt"
_BROWSER_PROXY_STRIP_RESPONSE_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "content-type",
    "content-security-policy",
    "content-security-policy-report-only",
    "cross-origin-embedder-policy",
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
    "keep-alive",
    "permissions-policy",
    "proxy-authenticate",
    "proxy-authorization",
    "public-key-pins",
    "strict-transport-security",
    "transfer-encoding",
    "upgrade",
    "x-content-security-policy",
    "x-frame-options",
    "x-webkit-csp",
}
_BROWSER_PROXY_FORWARD_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "content-type",
    "next-router-prefetch",
    "next-router-state-tree",
    "next-url",
    "purpose",
    "range",
    "rsc",
    "user-agent",
}
_BROWSER_PROXY_SUBRESOURCE_DESTINATIONS = {
    "audio",
    "embed",
    "font",
    "image",
    "manifest",
    "object",
    "paintworklet",
    "script",
    "sharedworker",
    "style",
    "track",
    "video",
    "worker",
}
_BROWSER_PROXY_NAVIGATION_DESTINATIONS = {"document", "iframe"}
_PRIVATE_BACKEND_PAYLOAD_KEYS = {"cdp_endpoint", "debugger_url", "screenshot_data_url"}
_BROWSER_BINARY_ENV = "HERMES_WEBUI_BROWSER_WORKBENCH_BROWSER"
_RENDERER_ENV = "HERMES_WEBUI_BROWSER_WORKBENCH_RENDERER"
_CHII_PACKAGE_VERSION = "1.15.5"
_CHII_PORT_ENV = "HERMES_WEBUI_CHII_PORT"
_CHII_COMMAND_ENV = "HERMES_WEBUI_CHII_COMMAND"
_CHII_START_TIMEOUT_SECONDS = 35
_CHII_REQUEST_TIMEOUT_SECONDS = 5
_CHII_BASE_PATH = "/api/browser-workbench/chii"
_CHII_BOOTSTRAP_PATH = f"{_CHII_BASE_PATH}/target.js"
_CHII_RUNTIME_PATH = f"{_CHII_BASE_PATH}/target-runtime.js"
_CHII_PROCESS: subprocess.Popen | None = None
_CHII_BASE_URL = ""
_CHII_LOCK = threading.RLock()
_LOGGER = logging.getLogger(__name__)

_CDP_BROWSER_CANDIDATES = (
    "/Applications/Opera GX.app/Contents/MacOS/Opera",
    "/Applications/Opera.app/Contents/MacOS/Opera",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
    "chromium-browser",
)


def _env_flag(value: object) -> str:
    return str(value or "").strip().lower()


def _now() -> float:
    return time.time()


def browser_workbench_ui_enabled(settings: dict | None = None, environ: dict[str, str] | None = None) -> bool:
    """Return whether the Browser Workbench launcher should be visible.

    Browser Workbench defaults on.  HERMES_WEBUI_BROWSER_WORKBENCH can force it
    off with 0/false or force it on with 1/true; persisted legacy settings do
    not hide the launcher.
    """
    source = os.environ if environ is None else environ
    explicit = _env_flag(source.get("HERMES_WEBUI_BROWSER_WORKBENCH"))
    if explicit in {"0", "false"}:
        return False
    if explicit in {"1", "true"}:
        return True
    return True


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _bounded_float(value, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return default
    return max(minimum, min(maximum, parsed))


def _sanitize_viewport(value) -> dict:
    if not isinstance(value, dict):
        return dict(_DEFAULT_VIEWPORT)
    width = _bounded_int(value.get("width"), default=_DEFAULT_VIEWPORT["width"], minimum=320, maximum=7680)
    height = _bounded_int(value.get("height"), default=_DEFAULT_VIEWPORT["height"], minimum=240, maximum=4320)
    dpr = _bounded_float(
        value.get("device_pixel_ratio"),
        default=_DEFAULT_VIEWPORT["device_pixel_ratio"],
        minimum=0.25,
        maximum=8,
    )
    return {"width": width, "height": height, "device_pixel_ratio": dpr}


def _truncate_text(value, limit: int = _MAX_CONTEXT_TEXT_LENGTH) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _truncate_raw_text(value, limit: int = _MAX_CONTEXT_TEXT_LENGTH) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit]


def _sanitize_html_tag_name(value) -> str:
    tag = str(value or "").strip()[:64]
    if not tag or tag.lower() == "unknown" or any(char.isspace() or char in '<>"\'' for char in tag):
        return ""
    return tag


def _browser_element_display_label(component: object, tag: object, fallback: object = "") -> str:
    component_name = _truncate_text(component, 120)
    if component_name.lower() == "unknown":
        component_name = ""
    tag_name = _sanitize_html_tag_name(tag)
    if component_name and tag_name:
        suffix = f" · {tag_name}"
        return f"{_truncate_text(component_name, max(1, 120 - len(suffix)))}{suffix}"
    return _truncate_text(component_name or tag_name or fallback or "Browser element", 120)


def _sanitize_frame_context(value) -> dict:
    if not isinstance(value, dict):
        return {}
    clean: dict[str, object] = {}
    selector = _truncate_text(value.get("selector") or value.get("css_selector") or value.get("path"), 320)
    src = _truncate_text(value.get("src") or value.get("url"), 512)
    if selector:
        clean["selector"] = selector
    if src:
        clean["src"] = src
    if "sameOrigin" in value or "same_origin" in value:
        clean["sameOrigin"] = bool(value.get("sameOrigin") or value.get("same_origin"))
    return clean


def _sanitize_inspect_point(body: dict | None) -> tuple[float, float, dict]:
    request = body if isinstance(body, dict) else {}
    viewport = _sanitize_viewport(request.get("viewport"))
    x = _bounded_float(request.get("x"), default=0, minimum=0, maximum=float(viewport["width"]))
    y = _bounded_float(request.get("y"), default=0, minimum=0, maximum=float(viewport["height"]))
    return x, y, viewport


def _sanitize_interaction(body: dict | None) -> dict:
    request = body if isinstance(body, dict) else {}
    viewport = _sanitize_viewport(request.get("viewport"))
    action = str(request.get("action") or request.get("type") or "").strip().lower()
    if action not in {"click", "double_click", "wheel", "key", "text"}:
        raise ValueError("browser workbench interaction action must be click, double_click, wheel, key, or text")
    payload: dict[str, object] = {
        "action": action,
        "viewport": viewport,
        "zoom": _bounded_float(request.get("zoom"), default=1, minimum=0.25, maximum=3),
        "x": _bounded_float(request.get("x"), default=0, minimum=0, maximum=float(viewport["width"])),
        "y": _bounded_float(request.get("y"), default=0, minimum=0, maximum=float(viewport["height"])),
    }
    if action == "wheel":
        payload["delta_x"] = _bounded_float(request.get("delta_x"), default=0, minimum=-10000, maximum=10000)
        payload["delta_y"] = _bounded_float(request.get("delta_y"), default=0, minimum=-10000, maximum=10000)
    if action in {"key", "text"}:
        payload["key"] = _truncate_text(request.get("key"), 64)
        payload["code"] = _truncate_text(request.get("code"), 64)
        payload["text"] = _truncate_raw_text(request.get("text"), 512)
        payload["alt_key"] = bool(request.get("alt_key"))
        payload["ctrl_key"] = bool(request.get("ctrl_key"))
        payload["meta_key"] = bool(request.get("meta_key"))
        payload["shift_key"] = bool(request.get("shift_key"))
    return payload


def _normalize_browser_context_items(raw_items) -> list[dict]:
    if not isinstance(raw_items, list):
        return []
    normalized: list[dict] = []
    for item in raw_items[:8]:
        if not isinstance(item, dict):
            continue
        raw_payload = item.get("payload")
        payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
        merged = {**payload, **item}
        kind_candidates = (
            item.get("kind"),
            item.get("entityType"),
            item.get("entity_type"),
            item.get("type"),
            payload.get("kind"),
            payload.get("entityType"),
            payload.get("entity_type"),
            payload.get("type"),
        )
        if not any(str(value or "").strip().lower().replace("_", "-") == "browser-element" for value in kind_candidates):
            continue
        selector = _truncate_text(merged.get("selector") or merged.get("css_selector") or merged.get("path"), 320)
        url = _truncate_text(merged.get("url"), 512)
        if not selector and not url:
            continue
        tag = _sanitize_html_tag_name(merged.get("tag") or merged.get("tagName") or merged.get("htmlTag") or merged.get("nodeName"))
        component = _truncate_text(merged.get("component") or merged.get("componentName"), 160)
        requested_display_label = _truncate_text(merged.get("displayLabel") or merged.get("display_label"), 120)
        if tag and (not requested_display_label or not any(separator in requested_display_label for separator in (" · ", " • "))):
            display_label = _browser_element_display_label(component, tag, requested_display_label or selector or url)
        else:
            display_label = requested_display_label or _browser_element_display_label(component, tag, selector or url)
        clean: dict[str, object] = {
            "type": "browser_element",
            "kind": "browser-element",
            "display_label": display_label,
            "tab": _truncate_text(merged.get("tab") or merged.get("tab_label"), 80),
            "url": url,
            "session_id": _truncate_text(merged.get("session_id"), 80),
            "selector": selector,
            "component": component,
            "tag": tag,
            "source": _truncate_text(merged.get("source") or merged.get("file") or merged.get("pathHint"), 240),
            "text": _truncate_text(merged.get("text") or merged.get("label"), 500),
        }
        rect = merged.get("rect")
        if isinstance(rect, dict):
            rect_payload = {}
            for key in ("x", "y", "top", "left", "width", "height"):
                value = rect.get(key)
                if isinstance(value, (int, float)):
                    rect_payload[key] = round(float(value), 2)
            if rect_payload:
                clean["rect"] = rect_payload
        point = merged.get("point")
        if isinstance(point, dict):
            point_payload = {}
            for key in ("x", "y"):
                value = point.get(key)
                if isinstance(value, (int, float)):
                    point_payload[key] = round(float(value), 2)
            if point_payload:
                clean["point"] = point_payload
        frame_payload = _sanitize_frame_context(merged.get("frame"))
        frames_payload: list[dict] = []
        frames = merged.get("frames")
        if isinstance(frames, list):
            for frame in frames[:5]:
                clean_frame = _sanitize_frame_context(frame)
                if clean_frame:
                    frames_payload.append(clean_frame)
        if frame_payload:
            clean["frame"] = frame_payload
        elif frames_payload:
            clean["frame"] = frames_payload[-1]
        if frames_payload:
            clean["frames"] = frames_payload
        normalized.append({key: value for key, value in clean.items() if value not in ("", None)})
    return normalized


def _xml_text(value) -> str:
    return _xml_escape(str(value), {'"': "&quot;"})


def _format_browser_context_items_for_prompt(items: list[dict]) -> str:
    if not items:
        return ""
    blocks = ["<browser_workbench_context>"]
    for index, item in enumerate(items, start=1):
        blocks.append(f'  <selected_browser_element index="{index}">')
        for key, label in (
            ("display_label", "label"),
            ("tab", "tab"),
            ("url", "url"),
            ("session_id", "session_id"),
            ("selector", "selector"),
            ("component", "component"),
            ("tag", "tag"),
            ("source", "source"),
            ("text", "text"),
        ):
            value = item.get(key)
            if value:
                blocks.append(f"    <{label}>{_xml_text(value)}</{label}>")
        if item.get("rect"):
            blocks.append(f"    <rect>{_xml_text(json.dumps(item['rect'], sort_keys=True))}</rect>")
        if item.get("point"):
            blocks.append(f"    <click_point>{_xml_text(json.dumps(item['point'], sort_keys=True))}</click_point>")
        if item.get("frame"):
            blocks.append(f"    <frame>{_xml_text(json.dumps(item['frame'], sort_keys=True))}</frame>")
        if item.get("frames"):
            blocks.append(f"    <frames>{_xml_text(json.dumps(item['frames'], sort_keys=True))}</frames>")
        blocks.append("  </selected_browser_element>")
    blocks.append("</browser_workbench_context>")
    return "\n".join(blocks)


def _normalize_initial_url(raw_url) -> str:
    raw = str(raw_url or "").strip()
    if not raw:
        return ""
    if "\x00" in raw:
        raise ValueError("browser workbench URL contains an invalid null byte")
    if len(raw) > _MAX_BROWSER_URL_LENGTH:
        raise ValueError("browser workbench URL is too long")
    candidate = raw if "://" in raw else f"http://{raw}"
    parsed = urlsplit(candidate)
    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError("browser workbench URL must use http or https")
    if parsed.username or parsed.password:
        raise ValueError("browser workbench URL must not include credentials")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("browser workbench URL must include a host")
    parsed = parsed._replace(scheme=scheme)
    return urlunsplit(parsed)


def _is_loopback_browser_host(hostname: str | None) -> bool:
    host = str(hostname or "").strip().strip("[]").rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False



def _find_free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _sanitize_chii_target_id(value: object) -> str:
    raw = str(value or "").strip()
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", raw)[:96].strip("_")
    return clean or f"bw_{secrets.token_urlsafe(8).replace('-', '_')}"


def _chii_target_id_for_session(session_id: object) -> str:
    return "hermes_bw_" + _sanitize_chii_target_id(session_id)


def _chii_client_id() -> str:
    return "hermes_client_" + secrets.token_urlsafe(6).replace("-", "_")


def _chii_process_alive() -> bool:
    return _CHII_PROCESS is not None and _CHII_PROCESS.poll() is None


def _chii_healthcheck(base_url: str, *, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/targets", timeout=timeout) as response:
            return 100 <= int(getattr(response, "status", 200) or 200) < 500
    except Exception:
        return False


def _chii_command() -> list[str]:
    configured = str(os.environ.get(_CHII_COMMAND_ENV) or "").strip()
    if configured:
        return configured.split()
    installed = shutil.which("chii")
    if installed:
        return [installed, "start"]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", f"chii@{_CHII_PACKAGE_VERSION}", "start"]
    raise RuntimeError("Chii requires either the `chii` CLI or `npx` to be available on PATH")


def _ensure_chii_service() -> str:
    """Start (or return) the local Chii sidecar used by iframe-proxy DevTools."""
    global _CHII_PROCESS, _CHII_BASE_URL
    with _CHII_LOCK:
        configured_port = str(os.environ.get(_CHII_PORT_ENV) or "").strip()
        if _CHII_BASE_URL and (_chii_process_alive() or _chii_healthcheck(_CHII_BASE_URL)):
            return _CHII_BASE_URL
        port = _bounded_int(configured_port, default=_find_free_loopback_port(), minimum=1024, maximum=65535)
        base_url = f"http://127.0.0.1:{port}/"
        if _chii_healthcheck(base_url):
            _CHII_BASE_URL = base_url
            return _CHII_BASE_URL
        command = _chii_command() + ["-p", str(port), "-h", "127.0.0.1", "-d", f"127.0.0.1:{port}"]
        _CHII_PROCESS = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            cwd=tempfile.gettempdir(),
            start_new_session=(sys.platform != "win32"),
        )
        deadline = time.monotonic() + _CHII_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if _CHII_PROCESS.poll() is not None:
                raise RuntimeError("Chii sidecar exited before becoming ready")
            if _chii_healthcheck(base_url):
                _CHII_BASE_URL = base_url
                return _CHII_BASE_URL
            time.sleep(0.2)
        raise RuntimeError("Timed out starting the Chii sidecar")


def _stop_chii_service() -> None:
    global _CHII_PROCESS, _CHII_BASE_URL
    with _CHII_LOCK:
        process = _CHII_PROCESS
        _CHII_PROCESS = None
        _CHII_BASE_URL = ""
    if process is not None and process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def _chii_devtools_url(session_id: object) -> str:
    base_url = _ensure_chii_service()
    parsed = urlsplit(base_url)
    host = parsed.netloc
    target_id = _chii_target_id_for_session(session_id)
    client_url = f"{host}/client/{quote(_chii_client_id(), safe='')}?target={quote(target_id, safe='')}"
    return f"{base_url}front_end/chii_app.html?ws={quote(client_url, safe='')}&rtc=false"


def _chii_target_runtime_script() -> bytes:
    base_url = _ensure_chii_service()
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/target.js", timeout=_CHII_REQUEST_TIMEOUT_SECONDS) as response:
        script = response.read(_BROWSER_PROXY_MAX_BODY_BYTES).decode("utf-8", "replace")
    # Chii's bundled target uses sessionStorage('chii-id'), which is origin-scoped
    # for all same-origin /browser-proxy iframes. Prefer a frame-local id so
    # separate Browser Workbench sessions cannot race or mix targets.
    legacy = 'e.id=w,w||(e.id=w=(0,s.default)(6),p.setItem("chii-id",w))'
    patched = 'e.id=window.ChiiTargetId||w,w=e.id,p.setItem("chii-id",w)'
    if legacy in script:
        script = script.replace(legacy, patched, 1)
    else:
        script = "/* Hermes Chii target-id patch unavailable; falling back to sessionStorage id. */\n" + script
    return script.encode("utf-8")


def _chii_bootstrap_script(session_id: str, target_id: str) -> bytes:
    base_url = _ensure_chii_service()
    session_json = json.dumps(str(session_id or ""))
    target_json = json.dumps(_sanitize_chii_target_id(target_id) if target_id else _chii_target_id_for_session(session_id))
    base_json = json.dumps(base_url)
    script = f"""(() => {{
  const sessionId = {session_json};
  const targetId = {target_json};
  const chiiBaseUrl = {base_json};
  try {{ sessionStorage.setItem('chii-id', targetId); }} catch (_) {{}}
  try {{ window.ChiiServerUrl = chiiBaseUrl; window.ChiiTargetId = targetId; window.ChiiTitle = document.title || location.href; }} catch (_) {{}}
  if (window.__HERMES_BROWSER_WORKBENCH_CHII_TARGET__ === targetId) return;
  window.__HERMES_BROWSER_WORKBENCH_CHII_TARGET__ = targetId;
  const script = document.createElement('script');
  script.src = {_CHII_RUNTIME_PATH!r} + '?target_id=' + encodeURIComponent(targetId);
  script.async = true;
  script.setAttribute('data-hermes-browser-workbench-chii-target', targetId);
  script.setAttribute('data-hermes-browser-workbench-session', sessionId);
  (document.head || document.documentElement || document.body).appendChild(script);
}})();
"""
    return script.encode("utf-8")


def handle_browser_workbench_chii_request(handler, parsed) -> bool:
    """Serve a tiny same-origin bootstrap that loads the local Chii target script."""
    if parsed.path not in {_CHII_BOOTSTRAP_PATH, _CHII_RUNTIME_PATH}:
        return False
    if not browser_workbench_ui_enabled():
        return bad(handler, _DISABLED_MESSAGE, status=409) or True
    query = parse_qs(parsed.query or "", keep_blank_values=True)
    session_id = str((query.get("session_id") or [""])[0] or "")
    target_id = str((query.get("target_id") or [""])[0] or "")
    if parsed.path == _CHII_BOOTSTRAP_PATH and not session_id.startswith(_SESSION_PREFIX):
        return bad(handler, "DevTools could not start.", status=400) or True
    try:
        body = _chii_bootstrap_script(session_id, target_id) if parsed.path == _CHII_BOOTSTRAP_PATH else _chii_target_runtime_script()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/javascript; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return True
    except Exception:
        return bad(handler, "DevTools are unavailable.", status=503) or True


atexit.register(_stop_chii_service)

def _browser_proxy_url_for_target(url: str, *, session_id: str = "", frame_id: str = "") -> str:
    try:
        target = _normalize_initial_url(url)
    except ValueError:
        return ""
    if not target:
        return ""
    parsed = urlsplit(target)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or parsed.username or parsed.password:
        return ""
    # Transport identity belongs to the proxy path, never location.search: app
    # routers must see exactly the target query string. Keep the target
    # scheme/host/path readable while leaving its query/hash in their native URL
    # components so useSearchParams/location.hash match the target document.
    context = ""
    if session_id:
        context = (
            _BROWSER_PROXY_CONTEXT_PREFIX
            + quote(str(session_id), safe="")
            + "/"
            + quote(str(frame_id or ""), safe="")
            + "/"
        )
    target_path = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    proxy_path = _BROWSER_PROXY_PREFIX + context + quote(target_path, safe=":/[]@!$&'()*+,;=%")
    return urlunsplit(("", "", proxy_path, parsed.query, parsed.fragment))


def _local_iframe_bridge_url(url: str, *, session_id: str = "", frame_id: str = "") -> str:
    """Return the same-origin iframe proxy URL for a target page."""
    return _browser_proxy_url_for_target(url, session_id=session_id, frame_id=frame_id)


def _browser_proxy_route_parts(parsed) -> tuple[str, str, str, bool]:
    raw_path = ""
    if parsed.path.startswith(_BROWSER_PROXY_PREFIX):
        raw_path = parsed.path[len(_BROWSER_PROXY_PREFIX):]
    if raw_path.startswith(_BROWSER_PROXY_CONTEXT_PREFIX):
        parts = raw_path.split("/", 3)
        if len(parts) != 4 or not parts[1]:
            raise ValueError("browser proxy transport context is invalid")
        return unquote(parts[3]).strip(), unquote(parts[1]), unquote(parts[2]), True
    query = parse_qs(parsed.query or "", keep_blank_values=True)
    return (
        unquote(raw_path).strip(),
        str((query.get("__hermes_bw_session") or [""])[0] or ""),
        str((query.get("__hermes_bw_frame") or [""])[0] or ""),
        False,
    )


def _browser_proxy_target_from_route(parsed) -> str:
    raw, _session_id, _frame_id, path_context = _browser_proxy_route_parts(parsed)
    query = parse_qs(parsed.query or "", keep_blank_values=True)
    proxy_only_keys = {"__hermes_bw_session", "__hermes_bw_frame"}
    external_query = parsed.query or "" if path_context else "&".join(
        f"{quote(str(key), safe='')}={quote(str(item), safe='')}"
        for key, values in query.items()
        if key not in proxy_only_keys
        for item in values
    )
    if not raw:
        raw = (query.get("url", [""])[0] or "").strip()
    elif external_query and "?" not in raw:
        # Tolerate manually-entered unencoded /browser-proxy/http://host/path?x=1,
        # but do not append internal proxy metadata to the target URL.
        raw = f"{raw}?{external_query}"
    if parsed.fragment and "#" not in raw:
        raw = f"{raw}#{parsed.fragment}"
    target = _browser_proxy_canonicalize_nested_target(_normalize_initial_url(raw))
    if re.search(
        r"/browser-proxy/(?:_hermes/[^/]+/[^/]+/)?https?:/",
        urlsplit(target).path,
        flags=re.IGNORECASE,
    ):
        raise ValueError("nested browser proxy target origin is invalid")
    parsed_target = urlsplit(target)
    if parsed_target.username or parsed_target.password:
        raise ValueError("browser proxy URL must not include credentials")
    return target


def _browser_proxy_target_origin(target_url: str) -> str:
    parsed = urlsplit(target_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _browser_proxy_canonicalize_nested_target(target_url: str) -> str:
    """Remove an App Router path accidentally composed with proxy transport."""
    parsed = urlsplit(target_url)
    marker_index = parsed.path.find(_BROWSER_PROXY_PREFIX)
    if marker_index <= 0:
        return target_url
    intended_path = parsed.path[:marker_index] or "/"
    embedded_raw = unquote(parsed.path[marker_index + len(_BROWSER_PROXY_PREFIX):])
    if embedded_raw.startswith(_BROWSER_PROXY_CONTEXT_PREFIX):
        context_parts = embedded_raw.split("/", 3)
        if len(context_parts) != 4:
            return target_url
        embedded_raw = context_parts[3]
    # HTTP request parsing can collapse the nested scheme's ``//`` while the
    # malformed value is still a path. Repair only enough to validate that the
    # embedded origin is the same target origin; never trust it as a new host.
    embedded_raw = re.sub(r"^(https?):/(?!/)", r"\1://", embedded_raw, count=1, flags=re.IGNORECASE)
    embedded = urlsplit(embedded_raw)
    if (
        embedded.scheme.lower() not in _ALLOWED_SCHEMES
        or embedded.username
        or embedded.password
        or _browser_proxy_target_origin(embedded_raw) != _browser_proxy_target_origin(target_url)
    ):
        return target_url
    return urlunsplit((parsed.scheme, parsed.netloc, intended_path, parsed.query, parsed.fragment))


def _browser_proxy_opaque_frame_id(frame_id: str) -> str:
    normalized = str(frame_id or "")
    if normalized.endswith(_BROWSER_PROXY_OPAQUE_FRAME_SUFFIX):
        return normalized
    return normalized + _BROWSER_PROXY_OPAQUE_FRAME_SUFFIX


def _browser_proxy_frame_requires_explicit_assets(frame_id: str) -> bool:
    return bool(str(frame_id or "").endswith(_BROWSER_PROXY_OPAQUE_FRAME_SUFFIX))


def _browser_proxy_tag_attribute_value(tag_text: str, attribute: str) -> str | None:
    pattern = re.compile(
        rf"\s{re.escape(attribute)}(?=\s|=|/?>)(?:\s*=\s*(?:(?P<quote>['\"])(?P<quoted>.*?)(?P=quote)|(?P<bare>[^\s>]+)))?",
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(tag_text)
    if not match:
        return None
    quoted = match.group("quoted")
    if quoted is not None:
        return quoted
    return match.group("bare") or ""


def _browser_proxy_iframe_has_opaque_sandbox(tag_text: str) -> bool:
    sandbox = _browser_proxy_tag_attribute_value(tag_text, "sandbox")
    if sandbox is None:
        return False
    tokens = {token.strip().lower() for token in sandbox.split() if token.strip()}
    return "allow-same-origin" not in tokens


def _browser_proxy_rewrite_url(
    value: str,
    base_url: str,
    *,
    session_id: str = "",
    frame_id: str = "",
    rewrite_root_relative: bool = False,
) -> str:
    raw = str(value or "").strip()
    if not raw:
        return raw
    # Keep Next/Turbopack runtime assets at their clean shell-root URL. The
    # subresource recovery hook uses the live proxy document referrer to route
    # these requests to the target session. Adding proxy session metadata to
    # every script URL prevents Turbopack from matching and consuming its
    # registered chunks even when each response succeeds.
    if raw.startswith("/") and not raw.startswith("//") and not rewrite_root_relative:
        return raw
    lower = raw.lower()
    if (
        lower.startswith("data:")
        or lower.startswith("blob:")
        or lower.startswith("javascript:")
        or lower.startswith("mailto:")
        or lower.startswith("tel:")
        or lower.startswith("#")
        or raw.startswith(_BROWSER_PROXY_PREFIX)
    ):
        return raw
    try:
        absolute = urljoin(base_url, raw)
        parsed = urlsplit(absolute)
        if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
            return raw
        return _browser_proxy_url_for_target(absolute, session_id=session_id, frame_id=frame_id)
    except Exception:
        return raw


def _browser_proxy_rewrite_css(
    text: str,
    base_url: str,
    *,
    session_id: str = "",
    frame_id: str = "",
    rewrite_root_relative: bool = False,
) -> str:
    def repl(match):
        quote_char = match.group(1) or ""
        raw = match.group(2).strip()
        rewritten = _browser_proxy_rewrite_url(
            raw,
            base_url,
            session_id=session_id,
            frame_id=frame_id,
            rewrite_root_relative=rewrite_root_relative,
        )
        return f"url({quote_char}{rewritten}{quote_char})"

    return re.sub(r"url\(\s*(['\"]?)([^)'\"]+)\1\s*\)", repl, text, flags=re.IGNORECASE)


def _browser_proxy_rewrite_html(
    text: str,
    base_url: str,
    *,
    session_id: str = "",
    frame_id: str = "",
    rewrite_root_relative: bool = False,
) -> str:
    attr_pattern = re.compile(
        r"\b(src|srcdoc|href|action|formaction|poster|data-src|srcset)\s*=\s*(['\"])(.*?)\2",
        flags=re.IGNORECASE | re.DOTALL,
    )

    def attr_repl(match, tag_name: str, opaque_iframe: bool):
        attr = match.group(1)
        quote_char = match.group(2)
        raw = match.group(3)
        normalized_attr = attr.lower()
        normalized_tag = tag_name.lower()
        resource_frame_id = _browser_proxy_opaque_frame_id(frame_id) if opaque_iframe else frame_id
        explicit_resource_url = rewrite_root_relative or (
            opaque_iframe and normalized_attr in {"src", "srcdoc", "data-src", "srcset"}
        )
        if normalized_tag == "iframe" and normalized_attr == "srcdoc":
            if not explicit_resource_url:
                return match.group(0)
            rewritten_srcdoc = _browser_proxy_rewrite_html(
                html.unescape(raw),
                base_url,
                session_id=session_id,
                frame_id=resource_frame_id,
                rewrite_root_relative=True,
            )
            return f"{attr}={quote_char}{html.escape(rewritten_srcdoc, quote=True)}{quote_char}"
        # Preserve framework-owned navigation/form attributes through SSR
        # hydration. The bridge transports only an unclaimed native link after
        # framework handlers; forms are hardened immediately before submission.
        if normalized_attr in {"action", "formaction"} or (
            normalized_attr == "href" and normalized_tag in {"a", "area"}
        ):
            return match.group(0)
        if attr.lower() == "srcset":
            parts = []
            for item in raw.split(","):
                item = item.strip()
                if not item:
                    continue
                segments = item.split()
                segments[0] = _browser_proxy_rewrite_url(
                    segments[0],
                    base_url,
                    session_id=session_id,
                    frame_id=resource_frame_id,
                    rewrite_root_relative=explicit_resource_url,
                )
                parts.append(" ".join(segments))
            rewritten = ", ".join(parts)
        else:
            rewritten = _browser_proxy_rewrite_url(
                raw,
                base_url,
                session_id=session_id,
                frame_id=resource_frame_id,
                rewrite_root_relative=explicit_resource_url,
            )
        return f"{attr}={quote_char}{rewritten}{quote_char}"

    tag_pattern = re.compile(
        r"<[a-zA-Z][a-zA-Z0-9:-]*(?:\s[^<>]*?)?>",
        flags=re.DOTALL,
    )

    def tag_repl(match):
        tag_text = match.group(0)
        tag_name_match = re.match(r"<([a-zA-Z][a-zA-Z0-9:-]*)", tag_text)
        tag_name = tag_name_match.group(1) if tag_name_match else ""
        opaque_iframe = tag_name.lower() == "iframe" and _browser_proxy_iframe_has_opaque_sandbox(tag_text)
        return attr_pattern.sub(lambda attr_match: attr_repl(attr_match, tag_name, opaque_iframe), tag_text)

    text = tag_pattern.sub(tag_repl, text)
    text = _browser_proxy_rewrite_css(
        text,
        base_url,
        session_id=session_id,
        frame_id=frame_id,
        rewrite_root_relative=rewrite_root_relative,
    )
    return _browser_proxy_inject_bridge(text, base_url, session_id=session_id, frame_id=frame_id)


def _browser_proxy_bridge_script(target_url: str, *, session_id: str = "", frame_id: str = "") -> str:
    target_json = json.dumps(target_url)
    origin_json = json.dumps(_browser_proxy_target_origin(target_url))
    session_json = json.dumps(session_id)
    frame_json = json.dumps(frame_id)
    proxy_prefix_json = json.dumps(_BROWSER_PROXY_PREFIX)
    opaque_frame_suffix_json = json.dumps(_BROWSER_PROXY_OPAQUE_FRAME_SUFFIX)
    proxy_transport_path_json = json.dumps(
        _BROWSER_PROXY_CONTEXT_PREFIX
        + quote(str(session_id), safe="")
        + "/"
        + quote(str(frame_id or ""), safe="")
        + "/"
        if session_id
        else ""
    )
    chii_target_id = _chii_target_id_for_session(session_id) if session_id else ""
    chii_bootstrap = ""
    if session_id:
        chii_src = f"{_CHII_BOOTSTRAP_PATH}?session_id={quote(session_id, safe='')}&target_id={quote(chii_target_id, safe='')}"
        chii_bootstrap = (
            f'<script id="hermes-browser-workbench-chii-target" '
            f'data-chii-target-id="{_xml_escape(chii_target_id)}" '
            f'data-hermes-browser-workbench-session="{_xml_escape(session_id)}" '
            f'src="{_xml_escape(chii_src)}"></script>'
        )
    script = chii_bootstrap + r"""<script id="hermes-browser-workbench-proxy-bridge">
(() => {
  if (window.__HERMES_BROWSER_WORKBENCH_BRIDGE__) return;
  window.__HERMES_BROWSER_WORKBENCH_BRIDGE__ = true;
  const targetUrl = __TARGET_JSON__;
  const targetOrigin = __ORIGIN_JSON__;
  const proxyPrefix = __PROXY_PREFIX_JSON__;
  const opaqueFrameSuffix = __OPAQUE_FRAME_SUFFIX_JSON__;
  const proxyTransportPath = __PROXY_TRANSPORT_PATH_JSON__;
  const initialProxyHref = location.href;
  const sessionId = __SESSION_JSON__;
  const frameId = __FRAME_JSON__ || (sessionId ? `${sessionId}-${Date.now().toString(36)}` : `frame-${Date.now().toString(36)}`);
  const installTargetPathnameUrlShim = () => {
    try {
      if (window.__HERMES_BROWSER_WORKBENCH_URL_PATHNAME_SHIM__) return true;
      const UrlCtor = window.URL;
      const descriptor = UrlCtor && Object.getOwnPropertyDescriptor(UrlCtor.prototype, 'pathname');
      if (!descriptor || typeof descriptor.get !== 'function') return false;
      Object.defineProperty(UrlCtor.prototype, 'pathname', {
        configurable: descriptor.configurable,
        enumerable: descriptor.enumerable,
        get() {
          const raw = descriptor.get.call(this);
          try {
            if (this.origin !== location.origin || !raw.startsWith(proxyPrefix)) return raw;
            let encodedTarget = raw.slice(proxyPrefix.length);
            if (proxyTransportPath && encodedTarget.startsWith(proxyTransportPath)) {
              encodedTarget = encodedTarget.slice(proxyTransportPath.length);
            }
            return new UrlCtor(decodeURIComponent(encodedTarget)).pathname;
          } catch (_) {
            return raw;
          }
        },
        set: descriptor.set
      });
      window.__HERMES_BROWSER_WORKBENCH_URL_PATHNAME_SHIM__ = true;
      return true;
    } catch (_) {
      return false;
    }
  };
  installTargetPathnameUrlShim();
  const devtoolsMaxArgLength = 600;
  const fullCaptureMaxHeight = 12000;
  const fullCaptureMaxPixels = 25000000;
  const devtoolsStartedAt = Date.now();
  let selectionMode = false;
  let hoverTimer = 0;
  let pendingHoverSelection = null;
  let selectionOverlay = null;
  let previousRootCursor = '';
  let previousBodyCursor = '';
  let networkSeq = 0;
  const safeString = (value, limit) => {
    try {
      if (typeof value === 'string') return value.slice(0, limit);
      if (value instanceof Error) return `${value.name || 'Error'}: ${value.message || ''}`.slice(0, limit);
      if (value === undefined) return 'undefined';
      if (value === null) return 'null';
      return JSON.stringify(value, (_key, item) => {
        if (typeof item === 'function') return `[Function ${item.name || 'anonymous'}]`;
        if (item instanceof Node) return `[Node ${(item.nodeName || '').toLowerCase()}]`;
        return item;
      }).slice(0, limit);
    } catch (_) {
      try { return String(value).slice(0, limit); } catch (__){ return '[unserializable]'; }
    }
  };
  const devtoolsPost = (type, payload, extra) => {
    try {
      parent.postMessage(Object.assign({
        source: 'hermes-devtools-agent',
        sessionId,
        frameId,
        targetUrl,
        type,
        timestamp: Date.now(),
        payload: payload && typeof payload === 'object' ? payload : {}
      }, extra || {}), location.origin);
    } catch (_) {}
  };
  const post = (payload) => {
    try { parent.postMessage(Object.assign({source:'hermes-browser-workbench-bridge', sessionId, frameId, targetUrl}, payload), location.origin); } catch (_) {}
  };
  function scheduleBrowserWorkbenchHoverPost(selection) {
    pendingHoverSelection = selection;
    if (hoverTimer) return;
    hoverTimer = setTimeout(() => {
      hoverTimer = 0;
      const pending = pendingHoverSelection;
      pendingHoverSelection = null;
      if (!selectionMode || !pending) return;
      post({type:'hover', selection:pending});
    }, 60);
  }
  const toProxyHttp = (value, transportPathOverride) => {
    try {
      if (!value) return value;
      const raw = String(value);
      const lower = raw.toLowerCase();
      if (lower.startsWith('data:') || lower.startsWith('blob:') || lower.startsWith('javascript:') || lower.startsWith('mailto:') || lower.startsWith('tel:') || raw.startsWith(proxyPrefix)) return value;
      let absolute = new URL(raw, targetUrl);
      // Next App Router hooks such as usePathname() can observe the iframe's
      // unavoidable same-origin transport pathname. Locale switches and
      // functional redirects then prepend their intended app path to that
      // value, producing /jp/browser-proxy/http://target/current. Recover the
      // intended prefix while accepting only this session's target origin;
      // the target server must never receive the proxy transport path.
      const nestedProxyIndex = absolute.pathname.indexOf(proxyPrefix);
      if (nestedProxyIndex > 0) {
        const intendedPath = absolute.pathname.slice(0, nestedProxyIndex) || '/';
        let embeddedPath = absolute.pathname.slice(nestedProxyIndex + proxyPrefix.length);
        if (proxyTransportPath && embeddedPath.startsWith(proxyTransportPath)) embeddedPath = embeddedPath.slice(proxyTransportPath.length);
        const embeddedRaw = decodeURIComponent(embeddedPath);
        const embedded = new URL(embeddedRaw);
        if (embedded.origin === targetOrigin) {
          absolute = new URL(`${intendedPath}${absolute.search}${absolute.hash}`, targetOrigin);
        }
      }
      if (absolute.origin === location.origin) {
        if (absolute.pathname.startsWith(proxyPrefix)) return `${absolute.pathname}${absolute.search}${absolute.hash}`;
        absolute = new URL(`${absolute.pathname}${absolute.search}${absolute.hash}`, targetOrigin);
      }
      if (absolute.protocol !== 'http:' && absolute.protocol !== 'https:') return value;
      if (absolute.username || absolute.password) return value;
      const targetPath = `${absolute.protocol}//${absolute.host}${absolute.pathname}`;
      const transportPath = transportPathOverride === undefined ? proxyTransportPath : transportPathOverride;
      return proxyPrefix + transportPath + encodeURIComponent(targetPath).replace(/%2F/g,'/').replace(/%3A/g,':') + absolute.search + absolute.hash;
    } catch (_) { return value; }
  };
  const proxyTransportForFrame = (candidateFrameId) => {
    if (!sessionId) return '';
    return `_hermes/${encodeURIComponent(sessionId)}/${encodeURIComponent(candidateFrameId || '')}/`;
  };
  const isOpaqueSandboxedFrame = (candidate) => {
    try {
      if (!candidate || String(candidate.tagName || '').toLowerCase() !== 'iframe') return false;
      if (typeof candidate.hasAttribute !== 'function' || !candidate.hasAttribute('sandbox')) return false;
      const tokens = String(candidate.getAttribute('sandbox') || '').toLowerCase().split(/\s+/).filter(Boolean);
      return !tokens.includes('allow-same-origin');
    } catch (_) { return false; }
  };
  const proxyOpaqueFrameSource = (candidate, value) => {
    try {
      if (!isOpaqueSandboxedFrame(candidate)) return value;
      const raw = String(value || '').trim();
      if (!raw || raw.startsWith(proxyPrefix)) return value;
      const opaqueFrameId = String(frameId || '').endsWith(opaqueFrameSuffix)
        ? String(frameId || '')
        : `${frameId || ''}${opaqueFrameSuffix}`;
      return toProxyHttp(raw, proxyTransportForFrame(opaqueFrameId));
    } catch (_) { return value; }
  };
  const proxyOpaqueCssText = (value, transportPath) => {
    try {
      return String(value || '').replace(/url\(\s*(['"]?)([^)'\"]+)\1\s*\)/gi, (_match, quote, raw) => {
        const proxied = toProxyHttp(String(raw || '').trim(), transportPath);
        return `url(${quote || ''}${proxied}${quote || ''})`;
      });
    } catch (_) { return value; }
  };
  const proxyOpaqueFrameMarkup = (candidate, value) => {
    try {
      if (!isOpaqueSandboxedFrame(candidate) || !window.DOMParser) return value;
      const raw = String(value || '');
      if (!raw.trim()) return value;
      const parsed = new DOMParser().parseFromString(raw, 'text/html');
      if (!parsed || !parsed.documentElement) return value;
      if (parsed.querySelector('#hermes-browser-workbench-proxy-bridge')) return value;
      const opaqueFrameId = String(frameId || '').endsWith(opaqueFrameSuffix)
        ? String(frameId || '')
        : `${frameId || ''}${opaqueFrameSuffix}`;
      const transportPath = proxyTransportForFrame(opaqueFrameId);
      const rewriteAttribute = (element, attribute) => {
        const current = String(element.getAttribute(attribute) || '').trim();
        if (!current) return;
        if (attribute === 'srcset') {
          const rewritten = current.split(',').map((item) => {
            const parts = item.trim().split(/\s+/);
            if (parts[0]) parts[0] = toProxyHttp(parts[0], transportPath);
            return parts.join(' ');
          }).join(', ');
          element.setAttribute(attribute, rewritten);
          return;
        }
        element.setAttribute(attribute, toProxyHttp(current, transportPath));
      };
      [
        ['script[src]','src'], ['link[href]','href'], ['img[src]','src'],
        ['img[srcset]','srcset'], ['source[src]','src'], ['source[srcset]','srcset'],
        ['video[src]','src'], ['video[poster]','poster'], ['audio[src]','src'],
        ['track[src]','src'], ['embed[src]','src'], ['object[data]','data'],
        ['iframe[src]','src']
      ].forEach(([selector, attribute]) => {
        parsed.querySelectorAll(selector).forEach((element) => rewriteAttribute(element, attribute));
      });
      parsed.querySelectorAll('style').forEach((element) => {
        element.textContent = proxyOpaqueCssText(element.textContent, transportPath);
      });
      parsed.querySelectorAll('[style]').forEach((element) => {
        element.setAttribute('style', proxyOpaqueCssText(element.getAttribute('style'), transportPath));
      });
      const sourceBridge = document.getElementById('hermes-browser-workbench-proxy-bridge');
      if (sourceBridge && sourceBridge.textContent) {
        const bridge = parsed.createElement('script');
        bridge.id = 'hermes-browser-workbench-proxy-bridge';
        bridge.textContent = sourceBridge.textContent;
        const head = parsed.head || parsed.documentElement;
        head.insertBefore(bridge, head.firstChild);
      }
      const doctype = parsed.doctype && parsed.doctype.name
        ? `<!doctype ${parsed.doctype.name}>`
        : '<!doctype html>';
      return doctype + parsed.documentElement.outerHTML;
    } catch (_) { return value; }
  };
  const prepareOpaqueSandboxedFrame = (candidate) => {
    try {
      if (!isOpaqueSandboxedFrame(candidate)) return false;
      let changed = false;
      const raw = String(candidate.getAttribute('src') || '').trim();
      if (raw && !raw.startsWith(proxyPrefix)) {
        const proxied = proxyOpaqueFrameSource(candidate, raw);
        if (proxied && proxied !== raw) {
          candidate.setAttribute('src', proxied);
          changed = true;
        }
      }
      const rawSrcdoc = String(candidate.getAttribute('srcdoc') || '');
      if (rawSrcdoc) {
        const proxiedSrcdoc = proxyOpaqueFrameMarkup(candidate, rawSrcdoc);
        if (proxiedSrcdoc && proxiedSrcdoc !== rawSrcdoc) {
          candidate.setAttribute('srcdoc', proxiedSrcdoc);
          changed = true;
        }
      }
      return changed;
    } catch (_) { return false; }
  };
  const prepareOpaqueSandboxedFrameTree = (root) => {
    try {
      if (!root) return;
      prepareOpaqueSandboxedFrame(root);
      if (typeof root.querySelectorAll === 'function') {
        root.querySelectorAll('iframe[sandbox]').forEach(prepareOpaqueSandboxedFrame);
      }
    } catch (_) {}
  };
  const installOpaqueFrameInsertionBoundary = () => {
    try {
      const nodePrototype = window.Node && window.Node.prototype;
      if (!nodePrototype || nodePrototype.__HERMES_OPAQUE_FRAME_BOUNDARY__) return false;
      const elementPrototype = window.Element && window.Element.prototype;
      const iframePrototype = window.HTMLIFrameElement && window.HTMLIFrameElement.prototype;
      const originalSetAttribute = elementPrototype && elementPrototype.setAttribute;
      if (typeof originalSetAttribute === 'function') {
        elementPrototype.setAttribute = function(name, value) {
          const attributeName = String(name || '').toLowerCase();
          let nextValue = value;
          if (attributeName === 'src' && isOpaqueSandboxedFrame(this)) {
            nextValue = proxyOpaqueFrameSource(this, value);
          } else if (attributeName === 'srcdoc' && isOpaqueSandboxedFrame(this)) {
            nextValue = proxyOpaqueFrameMarkup(this, value);
          }
          const result = originalSetAttribute.call(this, name, nextValue);
          if (attributeName === 'sandbox') prepareOpaqueSandboxedFrame(this);
          return result;
        };
      }
      const srcDescriptor = iframePrototype && Object.getOwnPropertyDescriptor(iframePrototype, 'src');
      if (srcDescriptor && typeof srcDescriptor.set === 'function') {
        Object.defineProperty(iframePrototype, 'src', {
          configurable:srcDescriptor.configurable,
          enumerable:srcDescriptor.enumerable,
          get:srcDescriptor.get,
          set(value) {
            let nextValue = value;
            if (isOpaqueSandboxedFrame(this)) {
              nextValue = proxyOpaqueFrameSource(this, value);
            }
            return srcDescriptor.set.call(this, nextValue);
          }
        });
      }
      const srcdocDescriptor = iframePrototype && Object.getOwnPropertyDescriptor(iframePrototype, 'srcdoc');
      if (srcdocDescriptor && typeof srcdocDescriptor.set === 'function') {
        Object.defineProperty(iframePrototype, 'srcdoc', {
          configurable:srcdocDescriptor.configurable,
          enumerable:srcdocDescriptor.enumerable,
          get:srcdocDescriptor.get,
          set(value) {
            const nextValue = isOpaqueSandboxedFrame(this)
              ? proxyOpaqueFrameMarkup(this, value)
              : value;
            return srcdocDescriptor.set.call(this, nextValue);
          }
        });
      }
      const originalAppendChild = nodePrototype.appendChild;
      const originalInsertBefore = nodePrototype.insertBefore;
      const originalReplaceChild = nodePrototype.replaceChild;
      if (typeof originalAppendChild === 'function') {
        nodePrototype.appendChild = function(node) {
          prepareOpaqueSandboxedFrameTree(node);
          return originalAppendChild.call(this, node);
        };
      }
      if (typeof originalInsertBefore === 'function') {
        nodePrototype.insertBefore = function(node, reference) {
          prepareOpaqueSandboxedFrameTree(node);
          return originalInsertBefore.call(this, node, reference);
        };
      }
      if (typeof originalReplaceChild === 'function') {
        nodePrototype.replaceChild = function(node, previous) {
          prepareOpaqueSandboxedFrameTree(node);
          return originalReplaceChild.call(this, node, previous);
        };
      }
      Object.defineProperty(nodePrototype, '__HERMES_OPAQUE_FRAME_BOUNDARY__', {
        configurable:true,
        value:true
      });
      return true;
    } catch (_) { return false; }
  };
  try {
    installOpaqueFrameInsertionBoundary();
    prepareOpaqueSandboxedFrameTree(document.documentElement);
    if (window.MutationObserver) {
      const opaqueFrameObserver = new MutationObserver((records) => {
        records.forEach((record) => {
          if (record.type === 'attributes') prepareOpaqueSandboxedFrame(record.target);
          if (record.addedNodes) record.addedNodes.forEach(prepareOpaqueSandboxedFrameTree);
        });
      });
      opaqueFrameObserver.observe(document.documentElement, {
        subtree:true,
        childList:true,
        attributes:true,
        attributeFilter:['sandbox','src','srcdoc']
      });
    }
  } catch (_) {}
  let proxyNavigationRedirecting = false;
  const installProxyNavigationBoundary = () => {
    try {
      if (!window.navigation || typeof window.navigation.addEventListener !== 'function') return false;
      window.navigation.addEventListener('navigate', (event) => {
        if (proxyNavigationRedirecting || !event || !event.destination || !event.cancelable) return;
        try {
          let destination = new URL(event.destination.url);
          if (destination.origin === location.origin) {
            // URL.pathname is intentionally shimmed for app code, so it cannot
            // identify our own transport here. href remains the raw shell URL.
            if (destination.href.startsWith(`${location.origin}${proxyPrefix}`)) return;
            destination = new URL(`${destination.pathname}${destination.search}${destination.hash}`, targetOrigin);
          } else if (
            destination.protocol !== 'http:'
            && destination.protocol !== 'https:'
          ) {
            return;
          }
          if (destination.username || destination.password) return;
          const proxied = toProxyHttp(destination.href);
          if (!proxied || proxied === event.destination.url) return;
          event.preventDefault();
          proxyNavigationRedirecting = true;
          location.assign(proxied);
        } catch (_) {}
      });
      return true;
    } catch (_) {
      return false;
    }
  };
  installProxyNavigationBoundary();
  const toTargetWs = (value) => {
    try {
      if (!value) return value;
      const raw = String(value);
      const absolute = new URL(raw, targetUrl);
      const shell = new URL(location.origin);
      const target = new URL(targetOrigin);
      if (absolute.host === shell.host) {
        absolute.host = target.host;
        absolute.protocol = target.protocol === 'https:' ? 'wss:' : 'ws:';
      } else {
        absolute.protocol = absolute.protocol === 'https:' || absolute.protocol === 'wss:' ? 'wss:' : 'ws:';
      }
      return absolute.href;
    } catch (_) { return value; }
  };
  const requestUrlOf = (input) => {
    try {
      if (typeof input === 'string') return input;
      if (input instanceof URL) return input.href;
      if (input && typeof input.url === 'string') return input.url;
    } catch (_) {}
    return '';
  };
  const methodOf = (input, init, fallback) => String((init && init.method) || (input && input.method) || fallback || 'GET').toUpperCase();
  const nextRequestId = (prefix) => `${frameId}-${prefix}-${++networkSeq}`;

  const originalConsole = window.console || {};
  ['log','info','warn','error','debug'].forEach((level) => {
    const original = typeof originalConsole[level] === 'function' ? originalConsole[level].bind(originalConsole) : null;
    if (!original) return;
    try {
      window.console[level] = (...args) => {
        devtoolsPost('console', {level, args: args.map((arg) => safeString(arg, devtoolsMaxArgLength)), sourceType: 'console'} , {level});
        return original(...args);
      };
    } catch (_) {}
  });
  window.addEventListener('error', (event) => {
    devtoolsPost('console', {
      level: 'error',
      sourceType: 'window.onerror',
      message: safeString(event && event.message, devtoolsMaxArgLength),
      filename: safeString(event && event.filename, 260),
      lineno: event && event.lineno || 0,
      colno: event && event.colno || 0,
      stack: safeString(event && event.error && event.error.stack, 1200)
    }, {level: 'error'});
  });
  window.addEventListener('unhandledrejection', (event) => {
    devtoolsPost('console', {level: 'error', sourceType: 'unhandledrejection', message: safeString(event && event.reason, 1200)}, {level: 'error'});
  });

  const OriginalFetch = window.fetch;
  if (typeof OriginalFetch === 'function') {
    window.fetch = function(input, init) {
      const rawUrl = requestUrlOf(input);
      const method = methodOf(input, init, 'GET');
      const requestId = nextRequestId('fetch');
      const started = performance.now();
      let proxiedInput = input;
      try {
        if (typeof input === 'string' || input instanceof URL) proxiedInput = toProxyHttp(input.href || input);
        else if (input && typeof input.url === 'string') proxiedInput = new Request(toProxyHttp(input.url), input);
      } catch (_) {}
      devtoolsPost('network', {phase:'start', requestId, requestType:'fetch', method, url:rawUrl || requestUrlOf(proxiedInput), proxiedUrl: requestUrlOf(proxiedInput), startedAt: Date.now()});
      return OriginalFetch.call(this, proxiedInput, init).then((response) => {
        devtoolsPost('network', {phase:'finish', requestId, requestType:'fetch', method, url:rawUrl || requestUrlOf(proxiedInput), status: response.status, ok: response.ok, duration: Math.round(performance.now() - started)});
        return response;
      }, (error) => {
        devtoolsPost('network', {phase:'error', requestId, requestType:'fetch', method, url:rawUrl || requestUrlOf(proxiedInput), error: safeString(error, 600), duration: Math.round(performance.now() - started)});
        throw error;
      });
    };
  }

  const OriginalOpen = window.XMLHttpRequest && window.XMLHttpRequest.prototype && window.XMLHttpRequest.prototype.open;
  const OriginalSend = window.XMLHttpRequest && window.XMLHttpRequest.prototype && window.XMLHttpRequest.prototype.send;
  if (OriginalOpen && OriginalSend) {
    window.XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      this.__hermesDevtools = {requestId: nextRequestId('xhr'), method: String(method || 'GET').toUpperCase(), url: String(url || ''), started: 0};
      return OriginalOpen.call(this, method, toProxyHttp(url), ...rest);
    };
    window.XMLHttpRequest.prototype.send = function(...args) {
      const meta = this.__hermesDevtools || {requestId: nextRequestId('xhr'), method:'GET', url:''};
      meta.started = performance.now();
      devtoolsPost('network', {phase:'start', requestId:meta.requestId, requestType:'xhr', method:meta.method, url:meta.url, startedAt: Date.now()});
      this.addEventListener('loadend', () => {
        devtoolsPost('network', {phase:'finish', requestId:meta.requestId, requestType:'xhr', method:meta.method, url:meta.url, status:this.status || 0, ok:this.status >= 200 && this.status < 400, duration: Math.round(performance.now() - meta.started)});
      }, {once:true});
      this.addEventListener('error', () => {
        devtoolsPost('network', {phase:'error', requestId:meta.requestId, requestType:'xhr', method:meta.method, url:meta.url, error:'XMLHttpRequest error', duration: Math.round(performance.now() - meta.started)});
      }, {once:true});
      return OriginalSend.apply(this, args);
    };
  }

  const OriginalWebSocket = window.WebSocket;
  if (typeof OriginalWebSocket === 'function') {
    window.WebSocket = function(url, protocols) {
      const requestId = nextRequestId('ws');
      const targetWs = toTargetWs(url);
      devtoolsPost('network', {phase:'start', requestId, requestType:'websocket', method:'WEBSOCKET', url:String(url || ''), proxiedUrl:String(targetWs || '')});
      const socket = protocols === undefined ? new OriginalWebSocket(targetWs) : new OriginalWebSocket(targetWs, protocols);
      socket.addEventListener('open', () => devtoolsPost('network', {phase:'finish', requestId, requestType:'websocket', method:'WEBSOCKET', url:String(url || ''), status:101, ok:true}), {once:true});
      socket.addEventListener('error', () => devtoolsPost('network', {phase:'error', requestId, requestType:'websocket', method:'WEBSOCKET', url:String(url || ''), error:'WebSocket error'}));
      socket.addEventListener('close', (event) => devtoolsPost('network', {phase:'close', requestId, requestType:'websocket', method:'WEBSOCKET', url:String(url || ''), status:event.code || 0, error:event.reason || ''}), {once:true});
      return socket;
    };
    window.WebSocket.prototype = OriginalWebSocket.prototype;
    Object.assign(window.WebSocket, OriginalWebSocket);
  }
  const OriginalEventSource = window.EventSource;
  if (typeof OriginalEventSource === 'function') {
    window.EventSource = function(url, config) { return new OriginalEventSource(toProxyHttp(url), config); };
    window.EventSource.prototype = OriginalEventSource.prototype;
  }
  const cssEscape = (value) => {
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  };
  const selectorFor = (node) => {
    if (!node || node.nodeType !== 1) return 'unknown';
    if (node.id) return '#' + cssEscape(node.id);
    const parts = [];
    let current = node;
    while (current && current.nodeType === 1 && parts.length < 6) {
      let part = current.localName || current.tagName.toLowerCase();
      if (current.classList && current.classList.length) part += '.' + Array.from(current.classList).slice(0, 3).map(cssEscape).join('.');
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((child) => child.localName === current.localName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
      }
      parts.unshift(part);
      current = parent;
    }
    return parts.join(' > ');
  };
  const attrsFor = (node) => {
    const attrs = {};
    try {
      Array.from(node && node.attributes || []).slice(0, 24).forEach((attr) => { attrs[attr.name] = safeString(attr.value, 240); });
    } catch (_) {}
    return attrs;
  };
  const nodeFromSelectionPoint = (event) => {
    if (!event) return null;
    if (!selectionOverlay || event.target !== selectionOverlay) return event.target && event.target.nodeType === 1 ? event.target : null;
    const previous = selectionOverlay.style.pointerEvents;
    selectionOverlay.style.pointerEvents = 'none';
    try { return document.elementFromPoint(event.clientX, event.clientY); }
    catch (_) { return null; }
    finally { selectionOverlay.style.pointerEvents = previous; }
  };
  const selectionFor = (event) => {
    const node = nodeFromSelectionPoint(event);
    const rect = node && node.getBoundingClientRect ? node.getBoundingClientRect() : {left:event.clientX,top:event.clientY,width:1,height:1};
    const className = node && typeof node.className === 'string' ? node.className : '';
    return {
      selector: selectorFor(node),
      text: node && node.innerText ? String(node.innerText).replace(/\s+/g,' ').trim().slice(0,240) : '',
      component: node && (node.getAttribute('data-component') || node.getAttribute('data-testid') || node.getAttribute('aria-label') || node.tagName) || 'unknown',
      tag: node ? String(node.localName || node.tagName || '') : '',
      className,
      classes: className ? className.split(/\s+/).filter(Boolean).slice(0, 12) : [],
      attributes: node ? attrsFor(node) : {},
      source: 'iframe-proxy',
      url: targetUrl,
      session_id: sessionId,
      frame_id: frameId,
      rect: {left:rect.left, top:rect.top, width:rect.width, height:rect.height},
      point: event ? {x:event.clientX, y:event.clientY} : null
    };
  };
  const stopSelectionEvent = (event) => {
    if (!event || !selectionMode) return;
    event.preventDefault();
    event.stopPropagation();
    if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
  };
  const setSelectionMode = (enabled) => {
    selectionMode = enabled === true;
    if (selectionMode) {
      if (!selectionOverlay) {
        previousRootCursor = document.documentElement && document.documentElement.style ? document.documentElement.style.cursor || '' : '';
        previousBodyCursor = document.body && document.body.style ? document.body.style.cursor || '' : '';
        selectionOverlay = document.createElement('div');
        selectionOverlay.setAttribute('data-hermes-browser-workbench-iframe-selection-overlay','true');
        selectionOverlay.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:transparent;cursor:crosshair;pointer-events:auto;user-select:none;touch-action:none;';
        (document.body || document.documentElement).appendChild(selectionOverlay);
      }
      try { if (document.documentElement) document.documentElement.style.setProperty('cursor','crosshair','important'); } catch (_) {}
      try { if (document.body) document.body.style.setProperty('cursor','crosshair','important'); } catch (_) {}
    } else {
      clearTimeout(hoverTimer);
      hoverTimer = 0;
      pendingHoverSelection = null;
      if (selectionOverlay && selectionOverlay.parentNode) selectionOverlay.parentNode.removeChild(selectionOverlay);
      selectionOverlay = null;
      try { if (document.documentElement) document.documentElement.style.cursor = previousRootCursor || ''; } catch (_) {}
      try { if (document.body) document.body.style.cursor = previousBodyCursor || ''; } catch (_) {}
    }
  };
  const targetUrlFromProxyHref = (candidateHref) => {
    try {
      if (candidateHref === undefined || candidateHref === null || String(candidateHref).trim() === '') return '';
      const own = new URL(String(candidateHref), location.href);
      if (own.href === initialProxyHref) return targetUrl;
      if (own.pathname && own.pathname.startsWith(proxyPrefix)) {
        let rawPath = own.pathname.slice(proxyPrefix.length);
        const hasPathContext = !!proxyTransportPath && rawPath.startsWith(proxyTransportPath);
        if (hasPathContext) rawPath = rawPath.slice(proxyTransportPath.length);
        const raw = decodeURIComponent(rawPath);
        const current = new URL(raw);
        if (hasPathContext) {
          current.search = own.search || '';
        } else {
          const ownQuery = new URLSearchParams(own.search || '');
          ownQuery.delete('__hermes_bw_session');
          ownQuery.delete('__hermes_bw_frame');
          if (ownQuery.toString() && !current.search) current.search = ownQuery.toString();
        }
        current.hash = own.hash || current.hash || '';
        return current.href;
      }
      if (own.origin === targetOrigin) return own.href;
      if (own.origin === location.origin) return new URL(`${own.pathname || '/'}${own.search || ''}${own.hash || ''}`, targetOrigin).href;
      return own.href;
    } catch (_) { return ''; }
  };
  const currentTargetUrl = () => targetUrlFromProxyHref(location.href) || targetUrl;
  let routeHistoryMutationMode = '';
  const metadata = (requestedHistoryMode) => {
    const icon = document.querySelector('link[rel~="icon"],link[rel="shortcut icon"],link[rel="apple-touch-icon"]');
    let canGoBack = false;
    let canGoForward = false;
    let nativeHistoryPreviousUrl = '';
    let nativeHistoryNextUrl = '';
    try {
      const liveNavigation = window.navigation;
      const entries = liveNavigation && typeof liveNavigation.entries === 'function' ? liveNavigation.entries() : [];
      const rawIndex = liveNavigation && liveNavigation.currentEntry ? liveNavigation.currentEntry.index : null;
      const currentIndex = rawIndex === null || rawIndex === undefined ? NaN : Number(rawIndex);
      if (Array.isArray(entries) && Number.isFinite(currentIndex) && currentIndex >= 0 && currentIndex < entries.length) {
        canGoBack = currentIndex > 0;
        canGoForward = currentIndex < entries.length - 1;
        if (canGoBack) nativeHistoryPreviousUrl = targetUrlFromProxyHref(entries[currentIndex - 1] && entries[currentIndex - 1].url);
        if (canGoForward) nativeHistoryNextUrl = targetUrlFromProxyHref(entries[currentIndex + 1] && entries[currentIndex + 1].url);
      } else {
        canGoBack = !!(window.history && Number(window.history.length) > 1);
      }
    } catch (_) {}
    const historyMode = requestedHistoryMode === 'replace' || requestedHistoryMode === 'push' || requestedHistoryMode === 'traverse' ? requestedHistoryMode : '';
    const payload = {url: currentTargetUrl(), proxy_url: location.href, title: document.title || '', favicon_url: icon && icon.href || '', readyState: document.readyState, frameCount: window.frames ? window.frames.length : 0, can_go_back:canGoBack, can_go_forward:canGoForward, native_history_previous_url:nativeHistoryPreviousUrl, native_history_next_url:nativeHistoryNextUrl, history_mode:historyMode};
    post(Object.assign({type:'metadata'}, payload));
    devtoolsPost('diagnostic', Object.assign({bridgeInjected:true, renderer:'iframe-proxy/session-shell', proxyUrl: location.href, uptimeMs: Date.now() - devtoolsStartedAt}, payload));
  };
  let routeMetadataTimer = 0;
  const scheduleRouteMetadata = () => {
    clearTimeout(routeMetadataTimer);
    routeMetadataTimer = setTimeout(() => {
      const historyMode = routeHistoryMutationMode;
      routeHistoryMutationMode = '';
      metadata(historyMode);
      setTimeout(metadata, 80);
    }, 0);
  };
  ['pushState','replaceState'].forEach((name) => {
    const original = history && history[name];
    if (typeof original !== 'function') return;
    try {
      history[name] = function(...args) {
        const nextArgs = args.slice();
        if (nextArgs.length > 2 && nextArgs[2] !== undefined && nextArgs[2] !== null) nextArgs[2] = toProxyHttp(nextArgs[2]);
        const result = original.apply(this, nextArgs);
        routeHistoryMutationMode = name === 'replaceState' ? 'replace' : 'push';
        scheduleRouteMetadata();
        return result;
      };
    } catch (_) {}
  });
  window.addEventListener('popstate', () => {
    routeHistoryMutationMode = 'traverse';
    scheduleRouteMetadata();
  });
  window.addEventListener('hashchange', scheduleRouteMetadata);
  window.addEventListener('pageshow', scheduleRouteMetadata);
  const proxyNativeAnchorNavigation = (event, anchor) => {
    if (!event || event.defaultPrevented || !anchor) return false;
    const href = anchor.getAttribute && anchor.getAttribute('href');
    const proxied = toProxyHttp(href || anchor.href);
    if (!proxied || proxied === href) return false;
    event.preventDefault();
    location.assign(proxied);
    return true;
  };
  document.addEventListener('click', (event) => {
    if (!event || event.defaultPrevented || event.button > 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = event.target && event.target.closest ? event.target.closest('a[href]') : null;
    if (!anchor || (anchor.target && anchor.target.toLowerCase() !== '_self')) return;
    // Run after framework/root handlers. React/Next prevents the default for a
    // client transition, in which case its clean target href must stay intact.
    // Only unclaimed native navigation crosses the proxy transport boundary.
    proxyNativeAnchorNavigation(event, anchor);
  });
  const prepareProxyFormSubmission = (form, submitter) => {
    const rawMethod = String(
      submitter && submitter.getAttribute && submitter.getAttribute('formmethod')
      || form.getAttribute('method')
      || 'get'
    ).trim().toLowerCase();
    let method = rawMethod === 'post' || rawMethod === 'dialog' ? rawMethod : 'get';
    const containsPassword = !!(form.querySelector && form.querySelector('input[type="password" i]'));
    if (containsPassword && method === 'get') {
      if (submitter && submitter.getAttribute && submitter.getAttribute('formmethod')) {
        submitter.setAttribute('formmethod', 'post');
      } else {
        form.setAttribute('method', 'post');
      }
      method = 'post';
    }

    const submitterAction = submitter && submitter.getAttribute ? submitter.getAttribute('formaction') : '';
    const proxiedAction = toProxyHttp(submitterAction || form.getAttribute('action') || form.action || targetUrl);
    if (proxiedAction) {
      if (submitterAction) submitter.setAttribute('formaction', proxiedAction);
      else form.setAttribute('action', proxiedAction);
    }

  };
  document.addEventListener('submit', (event) => {
    const form = event && event.target;
    if (!form || String(form.tagName || '').toLowerCase() !== 'form') return;
    prepareProxyFormSubmission(form, event.submitter);
  }, true);
  const blobToDataUrl = (blob) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(reader.error || new Error('Could not read iframe capture image data.'));
    reader.readAsDataURL(blob);
  });
  const imageFromUrl = (url) => new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('Could not decode iframe DOM capture SVG.'));
    img.src = url;
  });
  const inlineStylesheetText = () => {
    const chunks = [];
    try {
      Array.from(document.styleSheets || []).forEach((sheet) => {
        try {
          Array.from(sheet.cssRules || []).forEach((rule) => chunks.push(rule.cssText || ''));
        } catch (_) {}
      });
    } catch (_) {}
    return chunks.join('\n');
  };
  const syncFormState = (sourceRoot, cloneRoot) => {
    try {
      const sources = Array.from(sourceRoot.querySelectorAll('input,textarea,select'));
      const clones = Array.from(cloneRoot.querySelectorAll('input,textarea,select'));
      sources.forEach((source, index) => {
        const clone = clones[index];
        if (!clone) return;
        const tag = (source.tagName || '').toLowerCase();
        if (tag === 'textarea') {
          clone.textContent = source.value || '';
          clone.setAttribute('value', source.value || '');
          return;
        }
        if (tag === 'select') {
          Array.from(source.options || []).forEach((option, optionIndex) => {
            const clonedOption = clone.options && clone.options[optionIndex];
            if (!clonedOption) return;
            if (option.selected) clonedOption.setAttribute('selected', 'selected');
            else clonedOption.removeAttribute('selected');
          });
          return;
        }
        if (source.type === 'checkbox' || source.type === 'radio') {
          if (source.checked) clone.setAttribute('checked', 'checked');
          else clone.removeAttribute('checked');
        }
        clone.setAttribute('value', source.value || '');
      });
    } catch (_) {}
  };
  const inlineCloneImages = async (sourceRoot, cloneRoot) => {
    const sources = Array.from(sourceRoot.querySelectorAll('img'));
    const clones = Array.from(cloneRoot.querySelectorAll('img'));
    await Promise.all(sources.map(async (source, index) => {
      const clone = clones[index];
      if (!clone) return;
      const src = source.currentSrc || source.src || source.getAttribute('src') || '';
      if (!src || src.startsWith('data:')) return;
      try {
        const absolute = new URL(src, location.href).href;
        const response = await fetch(absolute, {credentials:'omit', cache:'force-cache'});
        if (!response.ok) return;
        const dataUrl = await blobToDataUrl(await response.blob());
        if (dataUrl) {
          clone.setAttribute('src', dataUrl);
          clone.removeAttribute('srcset');
          clone.removeAttribute('sizes');
        }
      } catch (_) {}
    }));
  };
  const sanitizeCaptureClone = async (clone) => {
    try {
      clone.querySelectorAll('script,noscript,iframe,link[rel~="stylesheet"],[data-hermes-browser-workbench-iframe-selection-overlay]').forEach((node) => node.remove());
      const bridge = clone.querySelector('#hermes-browser-workbench-proxy-bridge');
      if (bridge) bridge.remove();
      syncFormState(document.documentElement, clone);
      const styleText = inlineStylesheetText();
      if (styleText) {
        const style = document.createElement('style');
        style.setAttribute('data-hermes-browser-workbench-capture-styles', 'true');
        style.textContent = styleText;
        (clone.querySelector('head') || clone).appendChild(style);
      }
      await inlineCloneImages(document.documentElement, clone);
      clone.setAttribute('xmlns', 'http://www.w3.org/1999/xhtml');
    } catch (_) {}
    return clone;
  };
  const captureIframeViewport = async (request) => {
    const mode = request && String(request.mode || '') === 'full-page' ? 'full-page' : 'viewport';
    const viewportWidth = Math.max(1, Math.round(window.innerWidth || document.documentElement.clientWidth || 1));
    const viewportHeight = Math.max(1, Math.round(window.innerHeight || document.documentElement.clientHeight || 1));
    const originalScrollX = Math.max(0, Math.round(window.scrollX || document.documentElement.scrollLeft || document.body.scrollLeft || 0));
    const originalScrollY = Math.max(0, Math.round(window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0));
    const docWidth = Math.max(viewportWidth, document.documentElement.scrollWidth || 0, document.body && document.body.scrollWidth || 0);
    const docHeight = Math.max(viewportHeight, document.documentElement.scrollHeight || 0, document.body && document.body.scrollHeight || 0);
    const width = mode === 'full-page' ? docWidth : viewportWidth;
    const height = mode === 'full-page' ? docHeight : viewportHeight;
    const dpr = mode === 'full-page' ? Math.max(0.5, Math.min(1.5, Number(window.devicePixelRatio) || 1)) : Math.max(0.5, Math.min(2, Number(window.devicePixelRatio) || 1));
    if (mode === 'full-page' && height > fullCaptureMaxHeight) throw new Error(`Iframe full-page capture is too tall (${height}px). Maximum supported height is ${fullCaptureMaxHeight}px.`);
    const pixelCount = Math.round(width * height * dpr * dpr);
    const pixelLimit = mode === 'full-page' ? fullCaptureMaxPixels : 16000000;
    if (pixelCount > pixelLimit) throw new Error(mode === 'full-page' ? `Iframe full-page capture is too large (${pixelCount} pixels). Maximum supported pixel area is ${pixelLimit}.` : 'Iframe DOM capture viewport is too large. Reduce Browser Workbench size or zoom and try again.');
    const scrollX = mode === 'full-page' ? 0 : originalScrollX;
    const scrollY = mode === 'full-page' ? 0 : originalScrollY;
    const background = (() => {
      try { return getComputedStyle(document.body || document.documentElement).backgroundColor || '#ffffff'; }
      catch (_) { return '#ffffff'; }
    })();
    try {
      const clone = await sanitizeCaptureClone(document.documentElement.cloneNode(true));
      clone.style.width = `${docWidth}px`;
      clone.style.minWidth = `${docWidth}px`;
      clone.style.minHeight = `${docHeight}px`;
      const serialized = new XMLSerializer().serializeToString(clone);
      const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><foreignObject width="${width}" height="${height}"><div xmlns="http://www.w3.org/1999/xhtml" style="width:${width}px;height:${height}px;overflow:hidden;background:${background};"><div style="width:${docWidth}px;min-height:${docHeight}px;transform:translate(${-scrollX}px,${-scrollY}px);transform-origin:top left;">${serialized}</div></div></foreignObject></svg>`;
      const svgUrl = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
      const img = await imageFromUrl(svgUrl);
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('This browser cannot create a canvas for one-time iframe DOM capture.');
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL('image/png');
      const data = dataUrl.includes(',') ? dataUrl.split(',').pop() : dataUrl;
      return {
        data,
        type:'image/png',
        name:String(request && request.name || (mode === 'full-page' ? 'browser-workbench-iframe-full-page-screenshot.png' : 'browser-workbench-iframe-screenshot.png')),
        width:canvas.width,
        height:canvas.height,
        css_width:width,
        css_height:height,
        viewport_width:viewportWidth,
        viewport_height:viewportHeight,
        scroll_x:originalScrollX,
        scroll_y:originalScrollY,
        mode,
        method:mode === 'full-page' ? 'iframe-dom-full-page-capture' : 'iframe-dom-capture',
        limitations:['Canvas, video, WebGL, nested iframes, some fonts, sticky/fixed elements, and advanced CSS effects may not appear exactly.']
      };
    } finally {
      try { window.scrollTo(originalScrollX, originalScrollY); } catch (_) {}
    }
  };
  const handleCaptureRequest = async (request) => {
    const requestId = String(request && request.requestId || '');
    try {
      if (request && request.sessionId && sessionId && String(request.sessionId) !== String(sessionId)) return;
      if (request && request.mode && String(request.mode) !== 'viewport' && String(request.mode) !== 'full-page') throw new Error('Iframe DOM capture currently supports viewport and full-page screenshots only.');
      const mode = request && String(request.mode || '') === 'full-page' ? 'full-page' : 'viewport';
      const attachment = await captureIframeViewport(Object.assign({}, request || {}, {mode}));
      post({type:'capture-screenshot-result', requestId, ok:true, mode, attachment, message:'Screenshot captured.'});
    } catch (error) {
      post({type:'capture-screenshot-result', requestId, ok:false, error:'iframe_dom_capture_failed', message:safeString(error && error.message || error, 600)});
    }
  };
  window.addEventListener('message', (event) => {
    const data = event && event.data && typeof event.data === 'object' ? event.data : {};
    if (data.source !== 'hermes-browser-workbench-parent') return;
    if (data.type === 'selection-mode') setSelectionMode(data.enabled === true);
    if (data.type === 'devtools-ping') metadata();
    if (data.type === 'hermes:capture-screenshot' || data.type === 'capture-screenshot') handleCaptureRequest(data);
  });
  ['pointerover','mouseover','pointermove','mousemove'].forEach((name) => document.addEventListener(name, (event) => {
    if (!selectionMode) return;
    const selection = selectionFor(event);
    stopSelectionEvent(event);
    scheduleBrowserWorkbenchHoverPost(selection);
  }, true));
  document.addEventListener('click', (event) => {
    if (!selectionMode) return;
    const selection = selectionFor(event);
    stopSelectionEvent(event);
    post({type:'select', selection});
    devtoolsPost('element', {selection});
  }, true);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', metadata, {once:true}); else metadata();
  setInterval(() => devtoolsPost('heartbeat', {readyState: document.readyState, title: document.title || '', url: targetUrl, frameCount: window.frames ? window.frames.length : 0, uptimeMs: Date.now() - devtoolsStartedAt}), 2000);
  setTimeout(metadata, 250);
})();
</script>"""
    return (
        script.replace("__TARGET_JSON__", target_json)
        .replace("__ORIGIN_JSON__", origin_json)
        .replace("__PROXY_PREFIX_JSON__", proxy_prefix_json)
        .replace("__OPAQUE_FRAME_SUFFIX_JSON__", opaque_frame_suffix_json)
        .replace("__PROXY_TRANSPORT_PATH_JSON__", proxy_transport_path_json)
        .replace("__SESSION_JSON__", session_json)
        .replace("__FRAME_JSON__", frame_json)
    )


def _browser_proxy_inject_bridge(html_text: str, target_url: str, *, session_id: str = "", frame_id: str = "") -> str:
    script = _browser_proxy_bridge_script(target_url, session_id=session_id, frame_id=frame_id)
    if re.search(r"<head[^>]*>", html_text, flags=re.IGNORECASE):
        return re.sub(r"(<head[^>]*>)", lambda match: match.group(1) + script, html_text, count=1, flags=re.IGNORECASE)
    if re.search(r"<body[^>]*>", html_text, flags=re.IGNORECASE):
        return re.sub(r"(<body[^>]*>)", lambda match: match.group(1) + script, html_text, count=1, flags=re.IGNORECASE)
    if re.search(r"<html[^>]*>", html_text, flags=re.IGNORECASE):
        return re.sub(r"(<html[^>]*>)", lambda match: match.group(1) + script, html_text, count=1, flags=re.IGNORECASE)
    return script + html_text


def _browser_proxy_rewrite_body(
    data: bytes,
    content_type: str,
    target_url: str,
    *,
    session_id: str = "",
    frame_id: str = "",
    rewrite_root_relative: bool = False,
) -> tuple[bytes, str]:
    ctype = str(content_type or "").lower()
    if "text/html" in ctype:
        text = data.decode("utf-8", errors="replace")
        return _browser_proxy_rewrite_html(
            text,
            target_url,
            session_id=session_id,
            frame_id=frame_id,
            rewrite_root_relative=rewrite_root_relative,
        ).encode("utf-8"), "text/html; charset=utf-8"
    if "text/css" in ctype or target_url.lower().split("?", 1)[0].endswith(".css"):
        text = data.decode("utf-8", errors="replace")
        return _browser_proxy_rewrite_css(
            text,
            target_url,
            session_id=session_id,
            frame_id=frame_id,
            rewrite_root_relative=rewrite_root_relative,
        ).encode("utf-8"), content_type or "text/css; charset=utf-8"
    return data, content_type


def _browser_proxy_error_page(target_url: str, message: str, *, status: int = 502) -> bytes:
    escaped_target = _xml_escape(str(target_url or ""))
    escaped_message = _xml_escape(str(message or "Page could not be loaded."))
    html = f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><title>Page unavailable</title>
<style>body{{font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:24px;background:#0f172a;color:#e5e7eb}}code{{background:#111827;padding:2px 5px;border-radius:5px}}.card{{max-width:760px;border:1px solid #334155;border-radius:12px;padding:18px;background:#111827}}</style></head>
<body><div class=\"card\"><h1>This page could not be opened</h1><p><strong>Address:</strong> <code>{escaped_target}</code></p><p>{escaped_message}</p><p>Check the address and your connection, then try again.</p><p>Status: {int(status)}</p></div></body></html>"""
    return html.encode("utf-8")


def _browser_proxy_send_error(handler, target_url: str, message: str, *, status: int, method: str = "GET") -> bool:
    data = _browser_proxy_error_page(target_url, message, status=status)
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header(
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'",
    )
    if target_url:
        handler.send_header("X-Hermes-Browser-Proxy-Target", target_url)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    if method.upper() != "HEAD":
        handler.wfile.write(data)
    return True


def _browser_proxy_route_from_live_referrer(handler, parsed, *, allow_api: bool = False):
    """Resolve a clean shell-origin URL against its live proxy referrer."""
    request_headers = getattr(handler, "headers", {}) or {}
    # Recovery can run before the ordinary WebUI/API/proxy route dispatch. Never
    # claim a request that already has an explicit proxy target, otherwise the
    # proxy path is joined onto the target a second time:
    #   /browser-proxy/http://target/asset
    #     -> http://target/browser-proxy/http:/target/asset
    # WebUI-owned API assets (notably the injected Chii bootstrap) must likewise
    # stay on the shell origin instead of being forwarded as subresources.
    requested_path = str(getattr(parsed, "path", "") or "")
    if (
        requested_path == _BROWSER_PROXY_PREFIX.rstrip("/")
        or requested_path.startswith(_BROWSER_PROXY_PREFIX)
        or (not allow_api and requested_path.startswith(_CHII_BASE_PATH + "/"))
    ):
        return None

    raw_referrer = str(request_headers.get("Referer", "") or "").strip()
    if not raw_referrer:
        return None
    try:
        referrer = urlsplit(raw_referrer)
        request_host = str(request_headers.get("Host", "") or "").strip().lower()
        if (
            referrer.scheme.lower() not in _ALLOWED_SCHEMES
            or not request_host
            or referrer.netloc.lower() != request_host
            or not referrer.path.startswith(_BROWSER_PROXY_PREFIX)
        ):
            return None
        _referrer_raw, session_id, frame_id, _path_context = _browser_proxy_route_parts(referrer)
        if not session_id or _browser_proxy_state_for_session(session_id) is None:
            return None
        referrer_target = _browser_proxy_target_from_route(referrer)
        requested = urlunsplit(("", "", parsed.path, parsed.query or "", ""))
        target_url = urljoin(referrer_target, requested)
        proxy_url = _browser_proxy_url_for_target(target_url, session_id=session_id, frame_id=frame_id)
        return urlsplit(proxy_url) if proxy_url else None
    except (TypeError, ValueError):
        return None


def _browser_proxy_request_is_document_navigation(request_headers) -> bool:
    """Classify a navigation even when the browser omits Fetch Metadata."""
    headers = request_headers or {}
    destination = str(headers.get("Sec-Fetch-Dest", "") or "").strip().lower()
    if destination:
        return destination in _BROWSER_PROXY_NAVIGATION_DESTINATIONS
    fetch_mode = str(headers.get("Sec-Fetch-Mode", "") or "").strip().lower()
    if fetch_mode:
        return fetch_mode == "navigate"
    accept = str(headers.get("Accept", "") or "").lower()
    is_rsc = bool(str(headers.get("RSC", "") or "").strip()) or "text/x-component" in accept
    return "text/html" in accept and not is_rsc


def redirect_browser_workbench_proxy_document_navigation(handler, parsed) -> bool:
    """Canonicalize a target navigation that escaped to a clean WebUI path."""
    request_headers = getattr(handler, "headers", {}) or {}
    if not _browser_proxy_request_is_document_navigation(request_headers):
        return False
    proxy_route = _browser_proxy_route_from_live_referrer(handler, parsed, allow_api=True)
    if proxy_route is None:
        return False
    location = urlunsplit(("", "", proxy_route.path, proxy_route.query, proxy_route.fragment))
    handler.send_response(307)
    handler.send_header("Location", location)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", "0")
    handler.end_headers()
    return True


def recover_browser_workbench_proxy_subresource(handler, parsed):
    """Recover root-relative runtime assets that resolved against the WebUI origin."""
    request_headers = getattr(handler, "headers", {}) or {}
    destination = str(request_headers.get("Sec-Fetch-Dest", "") or "").strip().lower()
    if destination not in _BROWSER_PROXY_SUBRESOURCE_DESTINATIONS:
        return None
    return _browser_proxy_route_from_live_referrer(handler, parsed)


def _browser_proxy_redirect_followed_document(
    handler,
    target_url: str,
    final_url: str,
    *,
    session_id: str,
    frame_id: str,
    method: str,
) -> bool:
    """Keep the browser transport URL aligned with a target document redirect."""
    request_headers = getattr(handler, "headers", {}) or {}
    if not _browser_proxy_request_is_document_navigation(request_headers) or final_url == target_url:
        return False
    location = _browser_proxy_url_for_target(
        final_url,
        session_id=session_id,
        frame_id=frame_id,
    )
    if not location:
        return False
    # urllib has already applied the target redirect chain and updated the
    # session cookie jar. Reissue only the final safe document request through
    # its authoritative proxy URL; a non-safe original method must not be
    # replayed after its target-side effects have already occurred.
    status = 307 if method.upper() in {"GET", "HEAD"} else 303
    handler.send_response(status)
    handler.send_header("Location", location)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Hermes-Browser-Proxy-Target", final_url)
    handler.send_header("Content-Length", "0")
    handler.end_headers()
    return True


def _browser_proxy_read_request_body(handler) -> bytes:
    raw_length = handler.headers.get("Content-Length", "0") if hasattr(handler, "headers") else "0"
    try:
        length = int(raw_length or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid Content-Length") from exc
    if length < 0 or length > _BROWSER_PROXY_MAX_BODY_BYTES:
        raise ValueError("Browser proxy request body is too large")
    if length <= 0:
        return b""
    return handler.rfile.read(length)


def handle_browser_workbench_proxy_request(handler, parsed, *, method: str = "GET") -> bool:
    """Proxy a target page through WebUI origin for normal-browser iframe rendering."""
    if not browser_workbench_ui_enabled():
        return _browser_proxy_send_error(handler, "", _DISABLED_MESSAGE, status=409, method=method)
    try:
        target_url = _browser_proxy_target_from_route(parsed)
    except ValueError as exc:
        return _browser_proxy_send_error(handler, "", str(exc), status=400, method=method)
    try:
        _proxy_raw, proxy_session_id, proxy_frame_id, path_context = _browser_proxy_route_parts(parsed)
    except ValueError as exc:
        return _browser_proxy_send_error(handler, target_url, str(exc), status=400, method=method)
    proxy_state = _browser_proxy_state_for_session(proxy_session_id)
    if proxy_state is None:
        return _browser_proxy_send_error(
            handler,
            target_url,
            "Browser Workbench session not found.",
            status=404,
            method=method,
        )
    if proxy_session_id and not path_context:
        canonical_url = _browser_proxy_url_for_target(
            target_url,
            session_id=proxy_session_id,
            frame_id=proxy_frame_id,
        )
        handler.send_response(307)
        handler.send_header("Location", canonical_url)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return True

    rewrite_root_relative = _browser_proxy_frame_requires_explicit_assets(proxy_frame_id)

    outbound_headers = {"Accept-Encoding": "identity"}
    request_headers = getattr(handler, "headers", {}) or {}
    for key, value in request_headers.items():
        lower = str(key).lower()
        if lower in _BROWSER_PROXY_FORWARD_REQUEST_HEADERS and value:
            outbound_headers[str(key)] = str(value)
    # Never leak Hermes/WebUI cookies to the target origin. Target cookies need a
    # future scoped cookie jar/rewrite layer instead of sharing the WebUI cookie.
    outbound_headers.pop("Cookie", None)
    outbound_headers.pop("Host", None)
    target_origin = _browser_proxy_target_origin(target_url)
    if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        outbound_headers["Origin"] = target_origin
    outbound_headers["Referer"] = target_url

    body = b""
    if method.upper() not in {"GET", "HEAD"}:
        try:
            body = _browser_proxy_read_request_body(handler)
        except ValueError as exc:
            return _browser_proxy_send_error(handler, target_url, str(exc), status=413, method=method)
    request = urllib.request.Request(target_url, data=body if body else None, headers=outbound_headers, method=method.upper())
    try:
        with proxy_state.open(request, timeout=_BROWSER_PROXY_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", 200) or 200)
            final_url = _normalize_initial_url(response.geturl() or target_url) if hasattr(response, "geturl") else target_url
            if _browser_proxy_redirect_followed_document(
                handler,
                target_url,
                final_url,
                session_id=proxy_session_id,
                frame_id=proxy_frame_id,
                method=method,
            ):
                return True
            content_type = response.headers.get("Content-Type") or mimetypes.guess_type(urlsplit(final_url).path)[0] or "application/octet-stream"
            data = response.read(_BROWSER_PROXY_MAX_BODY_BYTES + 1)
            if len(data) > _BROWSER_PROXY_MAX_BODY_BYTES:
                raise ValueError("Browser proxy response body is too large")
            data, content_type = _browser_proxy_rewrite_body(
                data,
                content_type,
                final_url,
                session_id=proxy_session_id,
                frame_id=proxy_frame_id,
                rewrite_root_relative=rewrite_root_relative,
            )
            handler.send_response(status)
            for key, value in response.headers.items():
                lower = str(key).lower()
                if lower in _BROWSER_PROXY_STRIP_RESPONSE_HEADERS or lower == "set-cookie":
                    continue
                handler.send_header(str(key), str(value))
            handler.send_header("Content-Type", content_type)
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("X-Hermes-Browser-Proxy-Target", final_url)
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            if method.upper() != "HEAD":
                handler.wfile.write(data)
            return True
    except _BrowserProxySessionClosed:
        return _browser_proxy_send_error(
            handler,
            target_url,
            "Browser Workbench session not found.",
            status=404,
            method=method,
        )
    except urllib.error.HTTPError as exc:
        # Preserve target HTTP status while still stripping frame-blocking headers
        # and injecting diagnostics/rewrite support for HTML error pages.
        status = int(getattr(exc, "code", 502) or 502)
        try:
            final_url = _normalize_initial_url(exc.geturl() or target_url) if hasattr(exc, "geturl") else target_url
        except ValueError:
            return _browser_proxy_send_error(
                handler,
                target_url,
                "Browser proxy redirect URL is invalid.",
                status=502,
                method=method,
            )
        if _browser_proxy_redirect_followed_document(
            handler,
            target_url,
            final_url,
            session_id=proxy_session_id,
            frame_id=proxy_frame_id,
            method=method,
        ):
            return True
        content_type = exc.headers.get("Content-Type") if exc.headers else "text/html; charset=utf-8"
        data = exc.read(_BROWSER_PROXY_MAX_BODY_BYTES + 1)
        if len(data) > _BROWSER_PROXY_MAX_BODY_BYTES:
            data = data[:_BROWSER_PROXY_MAX_BODY_BYTES]
        if data:
            data, content_type = _browser_proxy_rewrite_body(
                data,
                content_type or "text/html; charset=utf-8",
                final_url,
                session_id=proxy_session_id,
                frame_id=proxy_frame_id,
                rewrite_root_relative=rewrite_root_relative,
            )
        else:
            data = _browser_proxy_error_page(final_url, str(exc), status=status)
            content_type = "text/html; charset=utf-8"
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Hermes-Browser-Proxy-Target", final_url)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        if method.upper() != "HEAD":
            handler.wfile.write(data)
        return True
    except Exception as exc:
        data = _browser_proxy_error_page(target_url, str(exc), status=502)
        handler.send_response(502)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Hermes-Browser-Proxy-Target", target_url)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        if method.upper() != "HEAD":
            handler.wfile.write(data)
        return True


def _renderer_for_url(url: str, *, backend_name: str) -> str:
    if backend_name == "cdp-browser":
        return "chromium-stream"
    if _local_iframe_bridge_url(url):
        return "iframe-bridge"
    return "none"


def _sanitize_capture_clip(body: dict | None, viewport: dict) -> dict | None:
    request = body if isinstance(body, dict) else {}
    raw_clip = request.get("clip")
    if not isinstance(raw_clip, dict):
        return None
    width_limit = float(viewport["width"])
    height_limit = float(viewport["height"])
    x = _bounded_float(raw_clip.get("x"), default=0, minimum=0, maximum=width_limit)
    y = _bounded_float(raw_clip.get("y"), default=0, minimum=0, maximum=height_limit)
    width = _bounded_float(raw_clip.get("width"), default=width_limit - x, minimum=1, maximum=width_limit - x)
    height = _bounded_float(raw_clip.get("height"), default=height_limit - y, minimum=1, maximum=height_limit - y)
    return {"x": x, "y": y, "width": width, "height": height, "scale": 1}


def _error_payload(status: str, message: str, *, route: str | None = None) -> dict:
    payload = {
        "ok": False,
        "enabled": False,
        "status": status,
        "backend": "none",
        "error": message,
        "message": message,
    }
    if route:
        payload["route"] = route
    return payload


def _public_payload(value):
    """Strip server-only backend connection details before JSON responses."""
    if isinstance(value, dict):
        return {
            key: _public_payload(item)
            for key, item in value.items()
            if str(key).lower() not in _PRIVATE_BACKEND_PAYLOAD_KEYS
        }
    if isinstance(value, list):
        return [_public_payload(item) for item in value]
    return value


def _public_dict_payload(value) -> dict:
    payload = _public_payload(value)
    return payload if isinstance(payload, dict) else {}


def _normalized_capabilities(value) -> dict:
    raw = value if isinstance(value, dict) else {}
    capabilities = dict(_FULL_BACKEND_CAPABILITIES)
    for key in capabilities:
        capabilities[key] = bool(raw.get(key, capabilities[key]))
    return capabilities


def _browser_binary_path(environ: dict[str, str] | None = None) -> str | None:
    source = os.environ if environ is None else environ
    configured = str(source.get(_BROWSER_BINARY_ENV) or "").strip()
    candidates = (configured,) if configured else _CDP_BROWSER_CANDIDATES
    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isabs(candidate) and os.access(candidate, os.X_OK):
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


class _CdpError(RuntimeError):
    pass


class _CdpWebSocket:
    """Tiny stdlib WebSocket client for Chrome DevTools Protocol JSON commands."""

    def __init__(self, websocket_url: str, *, timeout: float = 5.0) -> None:
        parsed = urlsplit(websocket_url)
        if parsed.scheme != "ws" or not parsed.hostname:
            raise _CdpError("invalid CDP websocket URL")
        self._path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        self._host = parsed.hostname
        self._port = parsed.port or 80
        self._timeout = timeout
        self._socket = socket.create_connection((self._host, self._port), timeout=timeout)
        self._socket.settimeout(timeout)
        self._next_id = 1
        self._handshake()

    def close(self) -> None:
        try:
            self._socket.close()
        except OSError:
            pass

    def _handshake(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self._path} HTTP/1.1\r\n"
            f"Host: {self._host}:{self._port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self._socket.sendall(request)
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._socket.recv(4096)
            if not chunk:
                break
            response += chunk
            if len(response) > 65536:
                break
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise _CdpError("CDP websocket upgrade failed")

    def command(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> dict:
        message_id = self._next_id
        self._next_id += 1
        payload = json.dumps({"id": message_id, "method": method, "params": params or {}}).encode("utf-8")
        self._send_frame(0x1, payload)
        deadline = time.monotonic() + (timeout or self._timeout)
        while True:
            remaining = max(0.1, deadline - time.monotonic())
            self._socket.settimeout(remaining)
            raw = self._recv_text_frame()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if data.get("id") != message_id:
                continue
            if data.get("error"):
                raise _CdpError(str(data["error"]))
            return data.get("result") or {}

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.extend([0x80 | 126])
            header.extend(struct.pack("!H", length))
        else:
            header.extend([0x80 | 127])
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._socket.sendall(bytes(header) + mask + masked)

    def _recv_exact(self, count: int) -> bytes:
        chunks = []
        remaining = count
        while remaining > 0:
            chunk = self._socket.recv(remaining)
            if not chunk:
                raise _CdpError("CDP websocket closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _recv_text_frame(self) -> str:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else b""
            payload = self._recv_exact(length) if length else b""
            if masked:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode == 0x8:
                raise _CdpError("CDP websocket closed")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0x1:
                return payload.decode("utf-8", errors="replace")


class _BrowserProxySessionClosed(RuntimeError):
    pass


def _browser_proxy_cookie_jar_path() -> Path:
    configured_state_dir = str(os.environ.get("HERMES_WEBUI_STATE_DIR") or "").strip()
    if configured_state_dir:
        state_dir = Path(configured_state_dir).expanduser()
    else:
        from api.config import STATE_DIR

        state_dir = Path(STATE_DIR)
    return state_dir / _BROWSER_PROXY_COOKIE_JAR_RELATIVE_PATH


class _BrowserProxyCookieJar(MozillaCookieJar):
    """Mozilla-format jar that exposes whether a response mutated cookies."""

    def __init__(self, filename: str) -> None:
        super().__init__(filename)
        self.revision = 0

    def set_cookie(self, cookie) -> None:
        super().set_cookie(cookie)
        self.revision += 1

    def clear(self, domain=None, path=None, name=None) -> None:
        super().clear(domain, path, name)
        self.revision += 1


class _BrowserProxyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is None:
            return None
        normalized = _normalize_initial_url(redirected.full_url)
        redirected.full_url = normalized
        if req.has_header("Origin"):
            redirected.add_unredirected_header("Origin", _browser_proxy_target_origin(normalized))
        redirected.add_unredirected_header("Referer", req.full_url)
        return redirected


class _BrowserProxyProfileState:
    """Shared, persistent target-cookie state for the iframe proxy profile."""

    def __init__(self, cookie_jar_path: Path | None = None) -> None:
        self._cookie_jar_path = Path(cookie_jar_path or _browser_proxy_cookie_jar_path())
        self._cookie_jar = _BrowserProxyCookieJar(str(self._cookie_jar_path))
        self._load_cookies()
        self._persisted_cookie_revision = self._cookie_jar.revision
        self._opener = urllib.request.build_opener(
            _BrowserProxyRedirectHandler(),
            urllib.request.HTTPCookieProcessor(self._cookie_jar),
        )
        self._lock = threading.RLock()
        self._active = True

    def _load_cookies(self) -> None:
        try:
            self._cookie_jar.load(ignore_discard=True, ignore_expires=False)
        except FileNotFoundError:
            return
        except (LoadError, OSError, UnicodeError) as exc:
            _LOGGER.warning(
                "Browser Workbench cookie profile could not be loaded from %s (%s).",
                self._cookie_jar_path,
                type(exc).__name__,
            )

    def _persist_cookies_locked(self) -> bool:
        if self._persisted_cookie_revision == self._cookie_jar.revision:
            return True
        cookie_dir = self._cookie_jar_path.parent
        temporary_path = None
        try:
            cookie_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor, temporary_path = tempfile.mkstemp(prefix=".cookies-", suffix=".tmp", dir=cookie_dir)
            os.close(descriptor)
            self._cookie_jar.save(
                temporary_path,
                ignore_discard=True,
                ignore_expires=True,
            )
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self._cookie_jar_path)
            self._persisted_cookie_revision = self._cookie_jar.revision
            return True
        except (OSError, UnicodeError, ValueError) as exc:
            _LOGGER.warning(
                "Browser Workbench cookie profile could not be saved to %s (%s).",
                self._cookie_jar_path,
                type(exc).__name__,
            )
            return False
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    @contextlib.contextmanager
    def open(self, request: urllib.request.Request, *, timeout: float):
        with self._lock:
            if not self._active:
                raise _BrowserProxySessionClosed("Browser Workbench session not found.")
            try:
                response = self._opener.open(request, timeout=timeout)
            except urllib.error.HTTPError:
                self._persist_cookies_locked()
                raise
            self._persist_cookies_locked()
        with response:
            yield response

    def clear_cookies(self) -> bool:
        with self._lock:
            self._cookie_jar.clear()
            return self._persist_cookies_locked()

    def close(self) -> None:
        with self._lock:
            self._active = False
            self._persist_cookies_locked()


class SessionShellBrowserWorkbenchBackend:
    """Small in-memory lifecycle/navigation backend used before a CDP adapter exists."""

    name = "session-shell"
    embedded_browser_enabled = False
    message = _LIMITED_MESSAGE

    def __init__(self, *, browser_proxy_cookie_jar_path: Path | None = None) -> None:
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._browser_proxy_cookie_jar_path = browser_proxy_cookie_jar_path
        self._browser_proxy_profile_state: _BrowserProxyProfileState | None = None

    def capabilities(self) -> dict:
        return dict(_SESSION_SHELL_CAPABILITIES)

    def _apply_request_viewport(self, session: dict, request: dict | None) -> None:
        if isinstance(request, dict) and isinstance(request.get("viewport"), dict):
            session["viewport"] = _sanitize_viewport(request.get("viewport"))
        if isinstance(request, dict) and "zoom" in request:
            session["zoom"] = _bounded_float(request.get("zoom"), default=1, minimum=0.25, maximum=3)

    def create_or_attach(self, body: dict | None = None) -> tuple[dict, int]:
        request = body if isinstance(body, dict) else {}
        requested_session_id = str(request.get("session_id") or "").strip()
        with self._lock:
            if requested_session_id:
                existing = self._sessions.get(requested_session_id)
                if existing:
                    existing["updated_at"] = _now()
                    return self._session_response(existing), 200
                # Browser Workbench sessions are process-scoped.  A WebUI reload,
                # backend restart, or stale renderer can ask to attach to a
                # session id that no longer exists while still carrying the last
                # durable URL.  Treat that as a create request instead of trapping
                # the user in a permanent "session not found" state.

        try:
            url = _normalize_initial_url(request.get("url"))
        except ValueError as exc:
            return _error_payload("invalid_url", str(exc)), 400

        current = _now()
        session = {
            "session_id": _new_session_id(),
            "status": "ready",
            "url": url,
            "title": "",
            "favicon_url": "",
            "backend": self.name,
            "created_at": current,
            "updated_at": current,
            "viewport": _sanitize_viewport(request.get("viewport")),
            "zoom": _bounded_float(request.get("zoom"), default=1, minimum=0.25, maximum=3),
            "history": [url] if url else [],
            "history_index": 0 if url else -1,
        }
        with self._lock:
            while session["session_id"] in self._sessions:
                session["session_id"] = _new_session_id()
            self._sessions[session["session_id"]] = session
            return self._session_response(session), 200

    def get(self, session_id: str) -> tuple[dict, int]:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return _error_payload("missing", "Browser tab is no longer available."), 404
            session["updated_at"] = _now()
            return self._session_response(session), 200

    def navigate(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        request = body if isinstance(body, dict) else {}
        try:
            url = _normalize_initial_url(request.get("url"))
        except ValueError as exc:
            return _error_payload("invalid_url", str(exc)), 400
        if not url:
            return _error_payload("invalid_url", "browser workbench URL is required for navigation"), 400

        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return _error_payload("missing", "Browser tab is no longer available."), 404
            history = list(session.get("history") or [])
            try:
                index = int(session.get("history_index", len(history) - 1))
            except (TypeError, ValueError):
                index = len(history) - 1
            if index < len(history) - 1:
                history = history[: index + 1]
            if not history or history[-1] != url:
                history.append(url)
            session["history"] = history
            session["history_index"] = len(history) - 1
            session["url"] = url
            session["title"] = ""
            session["favicon_url"] = ""
            self._apply_request_viewport(session, request)
            session["updated_at"] = _now()
            return self._session_response(session), 200

    def reload(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        request = body if isinstance(body, dict) else {}
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return _error_payload("missing", "Browser tab is no longer available."), 404
            self._apply_request_viewport(session, request)
            session["updated_at"] = _now()
            return self._session_response(session), 200

    def stop_loading(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        request = body if isinstance(body, dict) else {}
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return _error_payload("missing", "Browser tab is no longer available."), 404
            self._apply_request_viewport(session, request)
            session["updated_at"] = _now()
            payload = self._session_response(session)
        payload["load_status"] = "idle"
        payload["load_error"] = ""
        payload["message"] = "Loading stopped."
        return payload, 200

    def go_back(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        return self._move_history(session_id, -1, body)

    def go_forward(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        return self._move_history(session_id, 1, body)

    def clear_history(self, session_id: str) -> tuple[dict, int]:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return _error_payload("missing", "Browser tab is no longer available."), 404
            current = str(session.get("url") or "")
            session["history"] = [current] if current else []
            session["history_index"] = 0 if current else -1
            session["updated_at"] = _now()
            return self._session_response(session), 200

    def clear_cache(self, session_id: str) -> tuple[dict, int]:
        return self.get(session_id)

    def clear_cookies(self, session_id: str) -> tuple[dict, int]:
        state = self.browser_proxy_state(session_id)
        if state is not None:
            state.clear_cookies()
        return self.get(session_id)

    def open_devtools(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        request = body if isinstance(body, dict) else {}
        payload, status = self.get(session_id)
        if status >= 400:
            return payload, status
        if payload.get("renderer") != "iframe-bridge":
            payload["devtools_url"] = ""
            payload["message"] = "DevTools are unavailable for this page."
            return payload, 409
        try:
            payload["devtools_url"] = _chii_devtools_url(session_id)
        except Exception:
            payload["devtools_url"] = ""
            payload["message"] = "DevTools are unavailable."
            return payload, 503
        payload["chii_devtools"] = {
            "target_id": _chii_target_id_for_session(session_id),
            "docked": True,
            "popout": str(request.get("mode") or "panel").lower() == "popout",
            "popout_supported": True,
        }
        payload["message"] = "DevTools opened."
        return payload, 200

    def frame(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        del body
        payload, status = self.get(session_id)
        if status >= 400:
            return payload, status
        payload["frame"] = None
        payload["message"] = "Live page preview is unavailable."
        return payload, 409

    def capture_screenshot(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        del body
        payload, status = self.get(session_id)
        if status >= 400:
            return payload, status
        payload["attachment"] = None
        payload["message"] = "Screenshots are unavailable."
        return payload, 409

    def inspect_at(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        del body
        payload, status = self.get(session_id)
        if status >= 400:
            return payload, status
        payload["selection"] = {
            "selector": "unavailable (inspection backend not connected yet)",
            "component": "unknown",
            "source": "session-shell",
        }
        payload["message"] = "Element selection is unavailable."
        return payload, 409

    def interact(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        del body
        payload, status = self.get(session_id)
        if status >= 400:
            return payload, status
        payload["message"] = "Page interaction is unavailable."
        return payload, 409

    def close(self, session_id: str) -> tuple[dict, int]:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if not session:
            return _error_payload("missing", "Browser tab is no longer available."), 404
        session["status"] = "closed"
        session["updated_at"] = _now()
        return self._session_response(session, status_override="closed"), 200

    def _move_history(self, session_id: str, delta: int, body: dict | None = None) -> tuple[dict, int]:
        request = body if isinstance(body, dict) else {}
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return _error_payload("missing", "Browser tab is no longer available."), 404
            history = list(session.get("history") or [])
            if not history:
                session["updated_at"] = _now()
                return self._session_response(session), 200
            try:
                index = int(session.get("history_index", len(history) - 1))
            except (TypeError, ValueError):
                index = len(history) - 1
            next_index = max(0, min(len(history) - 1, index + delta))
            session["history_index"] = next_index
            session["url"] = history[next_index]
            session["title"] = ""
            session["favicon_url"] = ""
            self._apply_request_viewport(session, request)
            session["updated_at"] = _now()
            return self._session_response(session), 200

    def has_session(self, session_id: str) -> bool:
        with self._lock:
            return str(session_id or "") in self._sessions

    def browser_proxy_state(self, session_id: str, *, create: bool = True) -> _BrowserProxyProfileState | None:
        normalized = str(session_id or "").strip()
        if not normalized:
            return None
        with self._lock:
            session = self._sessions.get(normalized)
            if session is None:
                return None
            state = self._browser_proxy_profile_state
            if isinstance(state, _BrowserProxyProfileState):
                return state
            if not create:
                return None
            state = _BrowserProxyProfileState(self._browser_proxy_cookie_jar_path)
            self._browser_proxy_profile_state = state
            return state

    def reset_for_tests(self) -> None:
        with self._lock:
            self._sessions.clear()
            state = self._browser_proxy_profile_state
            self._browser_proxy_profile_state = None
        if isinstance(state, _BrowserProxyProfileState):
            state.close()

    def _session_response(self, session: dict, *, status_override: str | None = None) -> dict:
        status = status_override or str(session.get("status") or "ready")
        history = list(session.get("history") or [])
        try:
            history_index = int(session.get("history_index", len(history) - 1))
        except (TypeError, ValueError):
            history_index = len(history) - 1
        url = session.get("url") or ""
        renderer = _renderer_for_url(str(url), backend_name=self.name)
        frame_id = f"{session.get('session_id') or 'bw'}-{int(float(session.get('updated_at') or _now()) * 1000)}"
        bridge_url = _local_iframe_bridge_url(str(url), session_id=str(session.get("session_id") or ""), frame_id=frame_id) if renderer == "iframe-bridge" else ""
        payload = {
            "ok": True,
            "session_id": session["session_id"],
            "status": status,
            "url": url,
            "title": session.get("title") or "",
            "favicon_url": session.get("favicon_url") or "",
            "backend": self.name,
            "renderer": renderer,
            "created_at": session.get("created_at"),
            "updated_at": session.get("updated_at"),
            "viewport": dict(session.get("viewport") or _DEFAULT_VIEWPORT),
            "zoom": _bounded_float(session.get("zoom"), default=1, minimum=0.25, maximum=3),
            "can_go_back": history_index > 0,
            "can_go_forward": 0 <= history_index < len(history) - 1,
            "capabilities": self.capabilities(),
            "message": self.message,
        }
        if bridge_url:
            payload["bridge_url"] = bridge_url
            payload["chii_devtools"] = {
                "target_id": _chii_target_id_for_session(session.get("session_id") or ""),
                "target_script_url": _CHII_BOOTSTRAP_PATH,
                "docked": True,
                "popout_supported": True,
            }
        return payload


class CdpBrowserWorkbenchBackend(SessionShellBrowserWorkbenchBackend):
    """CDP-backed Chromium browser stream/control backend."""

    name = "cdp-browser"
    embedded_browser_enabled = True
    message = "Browser is ready."

    def __init__(self, *, browser_binary: str | None = None) -> None:
        super().__init__()
        self._browser_binary = browser_binary or _browser_binary_path()
        self._browser_process: subprocess.Popen | None = None
        self._browser_port: int | None = None
        self._browser_profile_dir: str | None = None
        self._target_ids: dict[str, str] = {}
        self._target_ws_urls: dict[str, str] = {}
        self._target_devtools_urls: dict[str, str] = {}
        self._browser_lock = threading.Lock()
        atexit.register(self.shutdown)

    @classmethod
    def is_available(cls, environ: dict[str, str] | None = None) -> bool:
        return _browser_binary_path(environ) is not None

    def capabilities(self) -> dict:
        return dict(_CDP_BROWSER_CAPABILITIES)

    def shutdown(self) -> None:
        with self._browser_lock:
            process = self._browser_process
            profile_dir = self._browser_profile_dir
            self._browser_process = None
            self._browser_port = None
            self._browser_profile_dir = None
            self._target_ids.clear()
            self._target_ws_urls.clear()
            self._target_devtools_urls.clear()
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)

    def create_or_attach(self, body: dict | None = None) -> tuple[dict, int]:
        payload, status = super().create_or_attach(body)
        return self._render_response(payload, status)

    def navigate(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        payload, status = super().navigate(session_id, body)
        if status < 400 and payload.get("ok") and payload.get("session_id") and payload.get("url"):
            self._navigate_existing_target(str(payload["session_id"]), str(payload["url"]))
        return self._render_response(payload, status)

    def reload(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        payload, status = super().reload(session_id, body)
        return self._render_response(payload, status, reload=True)

    def stop_loading(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        payload, status = super().stop_loading(session_id, body)
        if status < 400 and payload.get("ok"):
            try:
                self._command_for_session(session_id, "Page.stopLoading")
            except Exception:
                pass
            payload["renderer"] = "chromium-stream"
            payload["load_status"] = "idle"
            payload["load_error"] = ""
            payload["message"] = "Loading stopped."
        return payload, status

    def go_back(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        payload, status = super().go_back(session_id, body)
        return self._render_response(payload, status)

    def go_forward(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        payload, status = super().go_forward(session_id, body)
        return self._render_response(payload, status)

    def clear_cache(self, session_id: str) -> tuple[dict, int]:
        payload, status = self.get(session_id)
        if status >= 400:
            return payload, status
        self._command_for_session(session_id, "Network.enable")
        self._command_for_session(session_id, "Network.clearBrowserCache")
        payload["message"] = "Cache cleared."
        return payload, status

    def clear_cookies(self, session_id: str) -> tuple[dict, int]:
        payload, status = self.get(session_id)
        if status >= 400:
            return payload, status
        self._command_for_session(session_id, "Network.enable")
        self._command_for_session(session_id, "Network.clearBrowserCookies")
        payload["message"] = "Cookies cleared."
        return payload, status

    def open_devtools(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        del body
        payload, status = self.get(session_id)
        if status >= 400:
            return payload, status
        url = self._devtools_url_for_session(session_id, str(payload.get("url") or "about:blank"))
        payload["devtools_url"] = url
        payload["message"] = "DevTools opened."
        return payload, status

    def frame(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        payload, status = self.get(session_id)
        if status >= 400:
            return payload, status
        request = body if isinstance(body, dict) else {}
        viewport = _sanitize_viewport(request.get("viewport"))
        zoom = _bounded_float(request.get("zoom"), default=1, minimum=0.25, maximum=3)
        ws_url = self._target_for_session(session_id, str(payload.get("url") or "about:blank"))
        client = _CdpWebSocket(ws_url, timeout=8)
        try:
            self._configure_viewport(client, viewport, zoom)
            frame = self._capture_browser_image(client, viewport=viewport, zoom=zoom, image_format="jpeg", quality=74)
            metadata = self._page_metadata(client)
            self._sync_session_after_interaction(session_id, viewport, zoom, metadata)
        finally:
            client.close()
        payload, status = self.get(session_id)
        if status < 400:
            payload["frame"] = frame
            payload["message"] = "Page preview updated."
        return payload, status

    def capture_screenshot(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        payload, status = self.get(session_id)
        if status >= 400:
            return payload, status
        request = body if isinstance(body, dict) else {}
        viewport = _sanitize_viewport(request.get("viewport"))
        zoom = _bounded_float(request.get("zoom"), default=1, minimum=0.25, maximum=3)
        clip = _sanitize_capture_clip(request, viewport)
        ws_url = self._target_for_session(session_id, str(payload.get("url") or "about:blank"))
        client = _CdpWebSocket(ws_url, timeout=8)
        try:
            self._configure_viewport(client, viewport, zoom)
            image = self._capture_browser_image(client, viewport=viewport, zoom=zoom, clip=clip, image_format="png")
            metadata = self._page_metadata(client)
            self._sync_session_after_interaction(session_id, viewport, zoom, metadata)
        finally:
            client.close()
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        suffix = "area" if clip else "screenshot"
        payload, status = self.get(session_id)
        if status < 400:
            payload["attachment"] = {
                "name": f"browser-workbench-{suffix}-{timestamp}.png",
                "type": "image/png",
                "data": image["data"],
                "width": image.get("width"),
                "height": image.get("height"),
            }
            payload["message"] = "Screenshot captured."
        return payload, status

    def inspect_at(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        payload, status = self.get(session_id)
        if status >= 400:
            return payload, status
        x, y, viewport = _sanitize_inspect_point(body)
        ws_url = self._target_for_session(session_id, str(payload.get("url") or "about:blank"))
        client = _CdpWebSocket(ws_url, timeout=8)
        try:
            client.command("Runtime.enable", timeout=2)
            try:
                client.command(
                    "Emulation.setDeviceMetricsOverride",
                    {
                        "width": viewport["width"],
                        "height": viewport["height"],
                        "deviceScaleFactor": viewport["device_pixel_ratio"],
                        "mobile": False,
                    },
                    timeout=2,
                )
            except Exception:
                pass
            selection = self._evaluate_element_at(client, x, y)
        finally:
            client.close()
        payload["selection"] = selection
        payload["message"] = "Element selected."
        return payload, status

    def interact(self, session_id: str, body: dict | None = None) -> tuple[dict, int]:
        payload, status = self.get(session_id)
        if status >= 400:
            return payload, status
        try:
            interaction = _sanitize_interaction(body)
        except ValueError as exc:
            return _error_payload("invalid_interaction", str(exc)), 400
        viewport = dict(interaction.get("viewport") or _DEFAULT_VIEWPORT)
        zoom = _bounded_float(interaction.get("zoom"), default=1, minimum=0.25, maximum=3)
        ws_url = self._target_for_session(session_id, str(payload.get("url") or "about:blank"))
        client = _CdpWebSocket(ws_url, timeout=8)
        try:
            self._configure_viewport(client, viewport, zoom)
            self._dispatch_interaction(client, interaction)
            time.sleep(0.16 if interaction.get("action") != "wheel" else 0.08)
            metadata = self._page_metadata(client)
            self._sync_session_after_interaction(session_id, viewport, zoom, metadata)
        finally:
            client.close()
        payload, status = self.get(session_id)
        if status < 400:
            payload["message"] = "Page updated."
        return payload, status

    def close(self, session_id: str) -> tuple[dict, int]:
        target_id = self._target_ids.pop(session_id, "")
        self._target_ws_urls.pop(session_id, None)
        self._target_devtools_urls.pop(session_id, None)
        if target_id:
            try:
                self._http_json(f"/json/close/{quote(target_id, safe='')}", timeout=2)
            except Exception:
                pass
        return super().close(session_id)

    def reset_for_tests(self) -> None:
        self.shutdown()
        super().reset_for_tests()

    def _render_response(self, payload: dict, status: int, *, reload: bool = False) -> tuple[dict, int]:
        if status >= 400 or not payload.get("ok") or not payload.get("session_id"):
            return payload, status
        session_id = str(payload["session_id"])
        url = str(payload.get("url") or "")
        if not url:
            return payload, status
        try:
            self._target_for_session(session_id, url)
            if reload:
                try:
                    self._command_for_session(session_id, "Page.reload", {"ignoreCache": True})
                except Exception:
                    pass
            payload["renderer"] = "chromium-stream"
            payload.pop("render_error", None)
            payload["message"] = "Page opened."
        except Exception as exc:
            payload["render_error"] = str(exc)
        return payload, status

    def _ensure_browser(self) -> int:
        if not self._browser_binary:
            raise _CdpError("No Opera GX/Chromium browser binary found for CDP rendering")
        with self._browser_lock:
            if self._browser_process and self._browser_process.poll() is None and self._browser_port:
                return self._browser_port
            profile_dir = tempfile.mkdtemp(prefix="hermes-browser-workbench-cdp-")
            command = [
                self._browser_binary,
                "--headless=new",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                "--remote-allow-origins=*",
                f"--user-data-dir={profile_dir}",
                "about:blank",
            ]
            self._browser_process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
            self._browser_profile_dir = profile_dir
            port_file = os.path.join(profile_dir, "DevToolsActivePort")
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if self._browser_process.poll() is not None:
                    raise _CdpError("CDP browser exited before DevTools became ready")
                try:
                    with open(port_file, encoding="utf-8") as fh:
                        first_line = fh.readline().strip()
                    if first_line:
                        self._browser_port = int(first_line)
                        return self._browser_port
                except (FileNotFoundError, ValueError):
                    pass
                time.sleep(0.05)
            raise _CdpError("Timed out waiting for CDP browser DevTools port")

    def _http_json(self, path: str, *, timeout: float = 5.0, method: str = "GET") -> dict:
        port = self._ensure_browser()
        request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _target_for_session(self, session_id: str, url: str) -> str:
        ws_url = self._target_ws_urls.get(session_id)
        if ws_url:
            return ws_url
        target = self._http_json(f"/json/new?{quote(url or 'about:blank', safe='')}", method="PUT")
        ws_url = str(target.get("webSocketDebuggerUrl") or "")
        target_id = str(target.get("id") or "")
        devtools_path = str(target.get("devtoolsFrontendUrl") or "")
        if not ws_url:
            raise _CdpError("CDP target did not expose a websocket URL")
        self._target_ws_urls[session_id] = ws_url
        if target_id:
            self._target_ids[session_id] = target_id
        if devtools_path:
            self._target_devtools_urls[session_id] = self._local_devtools_url(ws_url, devtools_path)
        return ws_url

    def _absolute_devtools_url(self, devtools_path: str) -> str:
        if devtools_path.startswith("http://") or devtools_path.startswith("https://"):
            return devtools_path
        port = self._ensure_browser()
        path = devtools_path if devtools_path.startswith("/") else f"/{devtools_path}"
        return f"http://127.0.0.1:{port}{path}"

    def _local_devtools_url(self, ws_url: str, devtools_path: str = "") -> str:
        """Return a local DevTools frontend URL that can connect back to local CDP."""
        parsed_ws = urlsplit(ws_url)
        if parsed_ws.scheme != "ws" or not parsed_ws.netloc:
            raise _CdpError("invalid CDP websocket URL")
        path = "/devtools/inspector.html"
        if devtools_path and not devtools_path.startswith(("http://", "https://")):
            parsed_path = urlsplit(devtools_path if devtools_path.startswith("/") else f"/{devtools_path}")
            candidate_path = parsed_path.path or ""
            if candidate_path.startswith("/devtools/") and candidate_path.endswith(".html"):
                path = candidate_path
        return f"http://{parsed_ws.netloc}{path}?ws={parsed_ws.netloc}{parsed_ws.path}"

    def _navigate_existing_target(self, session_id: str, url: str) -> None:
        ws_url = self._target_ws_urls.get(session_id)
        if not ws_url:
            return
        client = _CdpWebSocket(ws_url, timeout=8)
        try:
            client.command("Page.enable", timeout=2)
            client.command("Page.navigate", {"url": url}, timeout=5)
        finally:
            client.close()

    def _devtools_url_for_session(self, session_id: str, url: str) -> str:
        existing = self._target_devtools_urls.get(session_id)
        if existing:
            return existing
        self._target_for_session(session_id, url)
        existing = self._target_devtools_urls.get(session_id)
        if existing:
            return existing
        target_id = self._target_ids.get(session_id)
        if target_id:
            for target in self._http_json("/json/list", timeout=2):
                if str(target.get("id") or "") == target_id:
                    devtools_path = str(target.get("devtoolsFrontendUrl") or "")
                    if devtools_path:
                        ws_url = str(target.get("webSocketDebuggerUrl") or "") or self._target_ws_urls.get(session_id, "")
                        url = self._local_devtools_url(ws_url, devtools_path)
                        self._target_devtools_urls[session_id] = url
                        return url
        raise _CdpError("CDP target did not expose a DevTools frontend URL")

    def _command_for_session(self, session_id: str, method: str, params: dict | None = None) -> dict:
        payload, status = super().get(session_id)
        if status >= 400:
            raise _CdpError("Browser Workbench session not found")
        ws_url = self._target_for_session(session_id, str(payload.get("url") or "about:blank"))
        client = _CdpWebSocket(ws_url, timeout=8)
        try:
            return client.command(method, params or {}, timeout=5)
        finally:
            client.close()

    def _configure_viewport(self, client: _CdpWebSocket, viewport: dict, zoom: float) -> None:
        width = _bounded_int(viewport.get("width"), default=_DEFAULT_VIEWPORT["width"], minimum=320, maximum=3840)
        height = _bounded_int(viewport.get("height"), default=_DEFAULT_VIEWPORT["height"], minimum=240, maximum=2160)
        dpr = _bounded_float(
            viewport.get("device_pixel_ratio"),
            default=_DEFAULT_VIEWPORT["device_pixel_ratio"],
            minimum=0.25,
            maximum=3,
        )
        client.command("Page.enable", timeout=2)
        client.command("Runtime.enable", timeout=2)
        client.command(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": dpr, "mobile": False},
            timeout=2,
        )
        try:
            client.command("Emulation.setPageScaleFactor", {"pageScaleFactor": zoom}, timeout=2)
        except Exception:
            pass

    def _capture_browser_image(
        self,
        client: _CdpWebSocket,
        *,
        viewport: dict,
        zoom: float,
        clip: dict | None = None,
        image_format: str = "png",
        quality: int | None = None,
    ) -> dict:
        params: dict[str, object] = {
            "format": image_format,
            "fromSurface": True,
            "captureBeyondViewport": False,
        }
        if quality is not None and image_format == "jpeg":
            params["quality"] = max(1, min(100, int(quality)))
        if clip:
            params["clip"] = clip
        data = client.command("Page.captureScreenshot", params, timeout=5).get("data")
        if not data:
            raise _CdpError("Chromium did not return image data")
        width = int(round(float((clip or {}).get("width") or viewport.get("width") or _DEFAULT_VIEWPORT["width"])))
        height = int(round(float((clip or {}).get("height") or viewport.get("height") or _DEFAULT_VIEWPORT["height"])))
        return {
            "mime": "image/jpeg" if image_format == "jpeg" else "image/png",
            "data": str(data),
            "width": width,
            "height": height,
            "zoom": zoom,
        }

    def _dispatch_interaction(self, client: _CdpWebSocket, interaction: dict) -> None:
        action = str(interaction.get("action") or "")
        x = float(interaction.get("x") or 0)
        y = float(interaction.get("y") or 0)
        if action in {"click", "double_click"}:
            click_count = 2 if action == "double_click" else 1
            params = {"x": x, "y": y, "button": "left", "clickCount": click_count}
            client.command("Input.dispatchMouseEvent", {**params, "type": "mousePressed"}, timeout=2)
            client.command("Input.dispatchMouseEvent", {**params, "type": "mouseReleased"}, timeout=2)
            return
        if action == "wheel":
            client.command(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseWheel",
                    "x": x,
                    "y": y,
                    "deltaX": float(interaction.get("delta_x") or 0),
                    "deltaY": float(interaction.get("delta_y") or 0),
                },
                timeout=2,
            )
            return
        text = str(interaction.get("text") or "")
        key = str(interaction.get("key") or "")
        if action == "text" or (text and len(text) == 1 and not any(interaction.get(flag) for flag in ("alt_key", "ctrl_key", "meta_key"))):
            if text:
                client.command("Input.insertText", {"text": text}, timeout=2)
            return
        self._dispatch_key(client, interaction, key)

    def _dispatch_key(self, client: _CdpWebSocket, interaction: dict, key: str) -> None:
        key_map = {
            "Enter": (13, "Enter", "Enter"),
            "Backspace": (8, "Backspace", "Backspace"),
            "Tab": (9, "Tab", "Tab"),
            "Escape": (27, "Escape", "Escape"),
            "ArrowLeft": (37, "ArrowLeft", "ArrowLeft"),
            "ArrowUp": (38, "ArrowUp", "ArrowUp"),
            "ArrowRight": (39, "ArrowRight", "ArrowRight"),
            "ArrowDown": (40, "ArrowDown", "ArrowDown"),
            "Delete": (46, "Delete", "Delete"),
            "Home": (36, "Home", "Home"),
            "End": (35, "End", "End"),
            "PageUp": (33, "PageUp", "PageUp"),
            "PageDown": (34, "PageDown", "PageDown"),
        }
        code = str(interaction.get("code") or key_map.get(key, (0, key, ""))[2] or key)
        vk, normalized_key, normalized_code = key_map.get(key, (ord(key.upper()) if len(key) == 1 else 0, key or code, code))
        modifiers = 0
        if interaction.get("alt_key"):
            modifiers |= 1
        if interaction.get("ctrl_key"):
            modifiers |= 2
        if interaction.get("meta_key"):
            modifiers |= 4
        if interaction.get("shift_key"):
            modifiers |= 8
        params = {
            "key": normalized_key,
            "code": normalized_code,
            "windowsVirtualKeyCode": vk,
            "nativeVirtualKeyCode": vk,
            "modifiers": modifiers,
        }
        if len(key) == 1 and not modifiers:
            params["text"] = key
        client.command("Input.dispatchKeyEvent", {**params, "type": "rawKeyDown"}, timeout=2)
        client.command("Input.dispatchKeyEvent", {**params, "type": "keyUp"}, timeout=2)

    def _page_metadata(self, client: _CdpWebSocket) -> dict:
        result = client.command(
            "Runtime.evaluate",
            {
                "expression": "(() => { const icon = document.querySelector('link[rel~=icon], link[rel=\"shortcut icon\"], link[rel=\"apple-touch-icon\"]'); return {url: location.href, title: document.title, readyState: document.readyState, favicon_url: icon && icon.href || ''}; })()",
                "returnByValue": True,
            },
            timeout=2,
        )
        value = ((result.get("result") or {}).get("value") or {})
        return value if isinstance(value, dict) else {}

    def _sync_session_after_interaction(self, session_id: str, viewport: dict, zoom: float, metadata: dict) -> None:
        current_url = str((metadata or {}).get("url") or "")
        parsed = urlsplit(current_url) if current_url else None
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            session["viewport"] = _sanitize_viewport(viewport)
            session["zoom"] = _bounded_float(zoom, default=1, minimum=0.25, maximum=3)
            if current_url and parsed and parsed.scheme.lower() in _ALLOWED_SCHEMES and parsed.netloc:
                previous = str(session.get("url") or "")
                session["url"] = current_url
                history = list(session.get("history") or [])
                try:
                    index = int(session.get("history_index", len(history) - 1))
                except (TypeError, ValueError):
                    index = len(history) - 1
                if current_url != previous:
                    if index < len(history) - 1:
                        history = history[: index + 1]
                    if not history or history[-1] != current_url:
                        history.append(current_url)
                    session["history"] = history
                    session["history_index"] = len(history) - 1
            if metadata.get("title"):
                session["title"] = _truncate_text(metadata.get("title"), 200)
            if metadata.get("favicon_url"):
                session["favicon_url"] = _truncate_text(metadata.get("favicon_url"), 512)
            session["updated_at"] = _now()

    def _evaluate_element_at(self, client: _CdpWebSocket, x: float, y: float) -> dict:
        expression = f"""
(() => {{
  const pointX = {json.dumps(x)};
  const pointY = {json.dumps(y)};
  const clip = (value, limit) => String(value || '').replace(/\\s+/g, ' ').trim().slice(0, limit);
  const esc = (value) => {{
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
    return String(value || '').replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  }};
  function selectorFor(el) {{
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return '';
    if (el.id) return '#'+esc(el.id);
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === Node.ELEMENT_NODE && parts.length < 6) {{
      let part = cur.localName || cur.tagName.toLowerCase();
      const testid = cur.getAttribute('data-testid') || cur.getAttribute('data-test') || cur.getAttribute('data-cy');
      if (testid) {{
        part += `[data-testid="${{String(testid).replace(/"/g, '\\\"')}}"]`;
        parts.unshift(part);
        break;
      }}
      const cls = Array.from(cur.classList || []).filter(Boolean).slice(0, 2);
      if (cls.length) part += '.' + cls.map(esc).join('.');
      const parent = cur.parentElement;
      if (parent) {{
        const same = Array.from(parent.children).filter(child => child.localName === cur.localName);
        if (same.length > 1) part += `:nth-of-type(${{same.indexOf(cur) + 1}})`;
      }}
      parts.unshift(part);
      cur = parent;
    }}
    return parts.join(' > ');
  }}
  function reactFiberFor(el) {{
    let cur = el;
    while (cur) {{
      const key = Object.keys(cur).find(k => k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$'));
      let fiber = key ? cur[key] : null;
      while (fiber) {{
        const type = fiber.elementType || fiber.type;
        const name = type && (type.displayName || type.name) || (fiber._debugOwner && fiber._debugOwner.type && (fiber._debugOwner.type.displayName || fiber._debugOwner.type.name)) || '';
        const source = fiber._debugSource || (fiber._debugOwner && fiber._debugOwner._debugSource) || null;
        if (name || source) {{
          return {{
            component: name || 'unknown',
            source: source && source.fileName ? `${{source.fileName}}:${{source.lineNumber || 1}}:${{source.columnNumber || 1}}` : 'unknown'
          }};
        }}
        fiber = fiber.return;
      }}
      cur = cur.parentElement;
    }}
    return {{component: 'unknown', source: 'unknown'}};
  }}
  const el = document.elementFromPoint(pointX, pointY);
  if (!el) return {{selector: 'unavailable (no element at point)', component: 'unknown', source: 'unknown', point: {{x: pointX, y: pointY}}}};
  const rect = el.getBoundingClientRect();
  const fiber = reactFiberFor(el);
  const attrs = {{}};
  ['id','class','name','role','aria-label','data-testid','data-test','data-cy'].forEach(name => {{
    const value = el.getAttribute && el.getAttribute(name);
    if (value) attrs[name] = clip(value, 160);
  }});
  return {{
    selector: selectorFor(el),
    tag: String(el.localName || el.tagName || ''),
    text: clip(el.innerText || el.textContent || el.getAttribute('aria-label') || '', 500),
    component: fiber.component || 'unknown',
    source: fiber.source || 'unknown',
    attributes: attrs,
    rect: {{x: rect.x, y: rect.y, top: rect.top, left: rect.left, width: rect.width, height: rect.height}},
    point: {{x: pointX, y: pointY}}
  }};
}})()
"""
        result = client.command(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": False},
            timeout=3,
        )
        value = ((result.get("result") or {}).get("value") or {})
        if not isinstance(value, dict):
            raise _CdpError("CDP element inspection returned no value")
        selection = _normalize_browser_context_items([{**value, "type": "browser_element"}])
        if not selection:
            return {
                "selector": "unavailable (no element at point)",
                "component": "unknown",
                "source": "unknown",
                "point": {"x": round(x, 2), "y": round(y, 2)},
            }
        return selection[0]


_DEFAULT_BACKEND = SessionShellBrowserWorkbenchBackend()
_CDP_BACKEND: CdpBrowserWorkbenchBackend | None = None
_SESSION_BACKEND_BY_ID: dict[str, SessionShellBrowserWorkbenchBackend] = {}
_SESSION_BACKEND_LOCK = threading.RLock()
_BACKEND_OVERRIDE_FOR_TESTS = None


def _new_session_id() -> str:
    return _SESSION_PREFIX + secrets.token_urlsafe(12).replace("-", "_")


def get_browser_workbench_backend():
    """Return the active backend adapter for Browser Workbench routes."""
    if _BACKEND_OVERRIDE_FOR_TESTS is not None:
        return _BACKEND_OVERRIDE_FOR_TESTS
    mode = str(os.environ.get(_RENDERER_ENV) or "auto").strip().lower()
    if mode in {"shell", "session-shell", "lifecycle-shell"}:
        return _DEFAULT_BACKEND
    if mode in {"cdp", "cdp-browser", "chromium-stream"} and CdpBrowserWorkbenchBackend.is_available():
        global _CDP_BACKEND
        if _CDP_BACKEND is None:
            _CDP_BACKEND = CdpBrowserWorkbenchBackend()
        return _CDP_BACKEND
    return _DEFAULT_BACKEND


def _client_requested_session_renderer(body: dict | None) -> bool:
    request = body if isinstance(body, dict) else {}
    renderer = str(request.get("client_renderer") or request.get("renderer") or request.get("preferred_renderer") or "").strip().lower()
    if renderer in {"iframe", "iframe-bridge", "session-shell", "normal-browser", "browser"}:
        return True
    return False


def _backend_for_browser_workbench_request(body: dict | None = None):
    if _BACKEND_OVERRIDE_FOR_TESTS is not None:
        return _BACKEND_OVERRIDE_FOR_TESTS
    request = body if isinstance(body, dict) else {}
    requested_session_id = str(request.get("session_id") or "").strip()
    if requested_session_id:
        owner = _backend_for_browser_workbench_session(requested_session_id)
        if owner is not None:
            return owner
    # A client that explicitly asks for the iframe bridge owns a session-shell
    # lifecycle even when an optional CDP backend is configured globally.
    if _client_requested_session_renderer(body):
        return _DEFAULT_BACKEND
    return get_browser_workbench_backend()


def _remember_browser_workbench_session_backend(payload: dict, backend) -> None:
    session_id = str(payload.get("session_id") or "").strip() if isinstance(payload, dict) else ""
    if not session_id:
        return
    with _SESSION_BACKEND_LOCK:
        _SESSION_BACKEND_BY_ID[session_id] = backend


def _forget_browser_workbench_session_backend(session_id: str) -> None:
    with _SESSION_BACKEND_LOCK:
        _SESSION_BACKEND_BY_ID.pop(str(session_id or ""), None)


def _backend_for_browser_workbench_session(session_id: str):
    normalized = str(session_id or "").strip()
    if not normalized:
        return get_browser_workbench_backend()
    with _SESSION_BACKEND_LOCK:
        owner = _SESSION_BACKEND_BY_ID.get(normalized)
    if owner is not None:
        if hasattr(owner, "has_session") and owner.has_session(normalized):
            return owner
        _forget_browser_workbench_session_backend(normalized)
    for candidate in (_DEFAULT_BACKEND, _CDP_BACKEND):
        if candidate is not None and hasattr(candidate, "has_session") and candidate.has_session(normalized):
            _remember_browser_workbench_session_backend({"session_id": normalized}, candidate)
            return candidate
    return get_browser_workbench_backend()


def _browser_proxy_state_for_session(session_id: str) -> _BrowserProxyProfileState | None:
    normalized = str(session_id or "").strip()
    if not normalized:
        return None
    backend = _backend_for_browser_workbench_session(normalized)
    getter = getattr(backend, "browser_proxy_state", None)
    if not callable(getter):
        return None
    return getter(normalized)


def set_browser_workbench_backend_for_tests(backend) -> None:
    """Install a test-only backend adapter behind the public route contract."""
    global _BACKEND_OVERRIDE_FOR_TESTS
    _BACKEND_OVERRIDE_FOR_TESTS = backend


def reset_browser_workbench_sessions_for_tests() -> None:
    """Clear in-memory Browser Workbench sessions and backend test overrides."""
    global _BACKEND_OVERRIDE_FOR_TESTS, _CDP_BACKEND
    _DEFAULT_BACKEND.reset_for_tests()
    if _CDP_BACKEND is not None:
        _CDP_BACKEND.reset_for_tests()
        _CDP_BACKEND = None
    with _SESSION_BACKEND_LOCK:
        _SESSION_BACKEND_BY_ID.clear()
    if _BACKEND_OVERRIDE_FOR_TESTS is not None and hasattr(_BACKEND_OVERRIDE_FOR_TESTS, "reset_for_tests"):
        _BACKEND_OVERRIDE_FOR_TESTS.reset_for_tests()
    _BACKEND_OVERRIDE_FOR_TESTS = None


def _extract_session_id(path: str) -> str | None:
    prefix = "/api/browser-workbench/session/"
    if not path.startswith(prefix):
        return None
    raw_id = unquote(path[len(prefix):]).strip()
    if not raw_id or "/" in raw_id or not raw_id.startswith(_SESSION_PREFIX):
        return None
    return raw_id


def _extract_session_action(path: str) -> tuple[str, str] | None:
    prefix = "/api/browser-workbench/session/"
    if not path.startswith(prefix):
        return None
    raw = path[len(prefix):].strip("/")
    parts = raw.split("/")
    if len(parts) != 2:
        return None
    session_id = unquote(parts[0]).strip()
    action = unquote(parts[1]).strip().lower()
    if not session_id or "/" in session_id or not session_id.startswith(_SESSION_PREFIX):
        return None
    if action not in {
        "navigate",
        "reload",
        "hard-reload",
        "stop-loading",
        "back",
        "forward",
        "clear-history",
        "clear-cookies",
        "clear-cache",
        "devtools",
        "frame",
        "screenshot",
        "inspect",
        "interact",
    }:
        return None
    return session_id, action


def _backend_capability_payload(ui_enabled: bool, backend) -> dict:
    if not ui_enabled:
        return {
            "ok": True,
            "enabled": False,
            "ui_enabled": False,
            "status": "unavailable",
            "backend": "none",
            "message": _UNAVAILABLE_MESSAGE,
            "capabilities": dict(_FULL_BACKEND_CAPABILITIES),
        }
    capabilities = _normalized_capabilities(backend.capabilities())
    embedded_enabled = bool(getattr(backend, "embedded_browser_enabled", False))
    return {
        "ok": True,
        "enabled": embedded_enabled,
        "ui_enabled": True,
        "status": "ready" if embedded_enabled else "limited",
        "backend": str(getattr(backend, "name", "unknown") or "unknown"),
        "message": str(getattr(backend, "message", _LIMITED_MESSAGE) or _LIMITED_MESSAGE),
        "capabilities": capabilities,
    }


def build_browser_workbench_capabilities() -> dict:
    """Return the public Browser Workbench capability payload."""
    ui_enabled = browser_workbench_ui_enabled()
    backend = get_browser_workbench_backend()
    return _public_dict_payload(_backend_capability_payload(ui_enabled, backend))


def create_or_attach_browser_workbench_session(body: dict | None = None) -> tuple[dict, int]:
    """Create or attach to a workbench lifecycle session via the active backend."""
    if not browser_workbench_ui_enabled():
        return _error_payload("disabled", _DISABLED_MESSAGE), 409
    backend = _backend_for_browser_workbench_request(body)
    payload, status = backend.create_or_attach(body)
    if status < 400:
        _remember_browser_workbench_session_backend(payload, backend)
    return _public_dict_payload(payload), status


def get_browser_workbench_session(session_id: str) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.get(session_id)
    if status >= 400:
        _forget_browser_workbench_session_backend(session_id)
    return _public_dict_payload(payload), status


def navigate_browser_workbench_session(session_id: str, body: dict | None = None) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.navigate(session_id, body)
    return _public_dict_payload(payload), status


def reload_browser_workbench_session(session_id: str, body: dict | None = None) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.reload(session_id, body)
    return _public_dict_payload(payload), status


def stop_loading_browser_workbench_session(session_id: str, body: dict | None = None) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.stop_loading(session_id, body)
    return _public_dict_payload(payload), status


def back_browser_workbench_session(session_id: str, body: dict | None = None) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.go_back(session_id, body)
    return _public_dict_payload(payload), status


def forward_browser_workbench_session(session_id: str, body: dict | None = None) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.go_forward(session_id, body)
    return _public_dict_payload(payload), status


def clear_browser_workbench_history(session_id: str) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.clear_history(session_id)
    return _public_dict_payload(payload), status


def clear_browser_workbench_cookies(session_id: str) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.clear_cookies(session_id)
    return _public_dict_payload(payload), status


def clear_browser_workbench_cache(session_id: str) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.clear_cache(session_id)
    return _public_dict_payload(payload), status


def open_browser_workbench_devtools(session_id: str, body: dict | None = None) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.open_devtools(session_id, body)
    return _public_dict_payload(payload), status


def frame_browser_workbench_session(session_id: str, body: dict | None = None) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.frame(session_id, body)
    return _public_dict_payload(payload), status


def capture_browser_workbench_screenshot(session_id: str, body: dict | None = None) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.capture_screenshot(session_id, body)
    return _public_dict_payload(payload), status


def inspect_browser_workbench_session(session_id: str, body: dict | None = None) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.inspect_at(session_id, body)
    public_payload = _public_dict_payload(payload)
    selection = public_payload.get("selection")
    if isinstance(selection, dict):
        normalized = _normalize_browser_context_items([{**selection, "type": "browser_element"}])
        if normalized:
            public_payload["selection"] = normalized[0]
    return public_payload, status


def interact_browser_workbench_session(session_id: str, body: dict | None = None) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.interact(session_id, body)
    return _public_dict_payload(payload), status


def close_browser_workbench_session(session_id: str) -> tuple[dict, int]:
    backend = _backend_for_browser_workbench_session(session_id)
    payload, status = backend.close(session_id)
    _forget_browser_workbench_session_backend(session_id)
    return _public_dict_payload(payload), status


def handle_browser_workbench_get(handler, parsed) -> bool:
    """Handle Browser Workbench GET routes."""
    chii_result = handle_browser_workbench_chii_request(handler, parsed)
    if chii_result is not False:
        return chii_result
    if parsed.path == "/api/browser-workbench/capabilities":
        j(handler, build_browser_workbench_capabilities())
        return True
    session_id = _extract_session_id(parsed.path)
    if session_id:
        payload, status = get_browser_workbench_session(session_id)
        j(handler, payload, status=status)
        return True
    return False


def handle_browser_workbench_post(handler, parsed, body: dict | None = None) -> bool:
    """Handle Browser Workbench POST routes."""
    if parsed.path == "/api/browser-workbench/session":
        payload, status = create_or_attach_browser_workbench_session(body)
        j(handler, payload, status=status)
        return True
    session_action = _extract_session_action(parsed.path)
    if session_action:
        session_id, action = session_action
        if action == "navigate":
            payload, status = navigate_browser_workbench_session(session_id, body)
        elif action == "reload":
            payload, status = reload_browser_workbench_session(session_id, body)
        elif action == "hard-reload":
            clear_browser_workbench_cache(session_id)
            payload, status = reload_browser_workbench_session(session_id, body)
        elif action == "stop-loading":
            payload, status = stop_loading_browser_workbench_session(session_id, body)
        elif action == "back":
            payload, status = back_browser_workbench_session(session_id, body)
        elif action == "forward":
            payload, status = forward_browser_workbench_session(session_id, body)
        elif action == "clear-history":
            payload, status = clear_browser_workbench_history(session_id)
        elif action == "clear-cookies":
            payload, status = clear_browser_workbench_cookies(session_id)
        elif action == "clear-cache":
            payload, status = clear_browser_workbench_cache(session_id)
        elif action == "frame":
            payload, status = frame_browser_workbench_session(session_id, body)
        elif action == "screenshot":
            payload, status = capture_browser_workbench_screenshot(session_id, body)
        elif action == "inspect":
            payload, status = inspect_browser_workbench_session(session_id, body)
        elif action == "interact":
            payload, status = interact_browser_workbench_session(session_id, body)
        else:
            payload, status = open_browser_workbench_devtools(session_id, body)
        j(handler, payload, status=status)
        return True
    if parsed.path.startswith("/api/browser-workbench/"):
        return bad(handler, f"unknown browser workbench endpoint: POST {parsed.path}", status=404) or True
    return False


def handle_browser_workbench_delete(handler, parsed, body: dict | None = None) -> bool:
    """Handle Browser Workbench DELETE routes."""
    del body
    session_id = _extract_session_id(parsed.path)
    if session_id:
        payload, status = close_browser_workbench_session(session_id)
        j(handler, payload, status=status)
        return True
    if parsed.path.startswith("/api/browser-workbench/"):
        return bad(handler, f"unknown browser workbench endpoint: DELETE {parsed.path}", status=404) or True
    return False
