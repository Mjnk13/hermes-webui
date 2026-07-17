"""Regression coverage for a focused chat stream that runs beyond ten minutes."""

import json
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text()
UI_JS = (ROOT / "static" / "ui.js").read_text()


def _function_source(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    brace = source.index("{", start)
    depth = 0
    quote = None
    escaped = False
    for index in range(brace, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated function {name}")


def test_anchor_registry_lease_survives_fifteen_minute_focused_stream():
    """Advance a deterministic clock past the old 600000ms failure boundary."""

    active_fn = _function_source(MESSAGES_JS, "_anchorRegistryStreamIsActive")
    cleanup_fn = _function_source(MESSAGES_JS, "_scheduleAnchorRegistryCleanup")
    harness = f"""
const assert = require('assert');
let now = 0;
let nextTimer = 1;
const timers = new Map();
function setTimeout(callback, delay) {{
  const id = nextTimer++;
  timers.set(id, {{at: now + Number(delay || 0), callback}});
  return id;
}}
function clearTimeout(id) {{ timers.delete(id); }}
function advance(ms) {{
  const target = now + ms;
  while (true) {{
    const due = [...timers.entries()]
      .filter(([, timer]) => timer.at <= target)
      .sort((a, b) => a[1].at - b[1].at)[0];
    if (!due) break;
    timers.delete(due[0]);
    now = due[1].at;
    due[1].callback();
  }}
  now = target;
}}
const activeSid = 'focused-thread';
const streamId = 'long-stream';
const registry = {{id: 'registry'}};
const _anchorRegistry = registry;
const _anchorRegistryMap = new Map([[streamId, registry]]);
const EventSource = {{CLOSED: 2}};
const LIVE_STREAMS = {{[activeSid]: {{streamId, source: {{readyState: 1}}}}}};
let _terminalStateReached = false;
let _streamFinalized = false;
let _anchorRegistryCleanupTimer = null;
const diagnostics = [];
function _streamDiagnostic(event, details) {{ diagnostics.push({{event, details}}); }}
{active_fn}
{cleanup_fn}
_scheduleAnchorRegistryCleanup(600000);
advance(15 * 60 * 1000);
assert.strictEqual(_anchorRegistryMap.get(streamId), registry,
  'an active registry must survive a 15-minute uninterrupted run');
assert.strictEqual(timers.size, 1,
  'renewing the lease must keep exactly one timer rather than leaking timers');
assert.ok(diagnostics.some(entry => entry.event === 'anchor-registry-lease-renewed'));
_terminalStateReached = true;
_scheduleAnchorRegistryCleanup(120000);
advance(120000);
assert.strictEqual(_anchorRegistryMap.has(streamId), false,
  'the same registry must still be cleaned after terminal settlement');
assert.strictEqual(timers.size, 0);
console.log(JSON.stringify({{ok: true, renewals: diagnostics.length}}));
"""
    result = subprocess.run(
        ["node", "-e", harness],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["ok"] is True


def test_render_scheduler_has_one_shot_lost_frame_recovery():
    schedule = _function_source(MESSAGES_JS, "_scheduleRender")
    cancel = _function_source(MESSAGES_JS, "_cancelAnimationFramePendingStreamRender")
    assert "_pendingRenderDelayHandle" in schedule
    assert "_pendingRenderWatchdogHandle" in schedule
    assert "render-watchdog-flush" in schedule
    assert "scheduleConsumed" in schedule
    assert "scheduleGeneration!==_renderScheduleGeneration" in schedule
    assert "_doRender('watchdog')" in schedule
    assert "clearTimeout(_pendingRenderDelayHandle)" in cancel
    assert "clearTimeout(_pendingRenderWatchdogHandle)" in cancel


def test_stream_diagnostics_cover_delivery_store_render_and_lifecycle_layers():
    required_message_events = {
        "stream-event-received",
        "store-update-committed",
        "active-thread-selector-notification",
        "render-scheduled",
        "render-committed",
        "anchor-registry-lease-renewed",
    }
    for event in required_message_events:
        assert event in MESSAGES_JS
    for event in (
        "render-started",
        "virtual-window-evaluated",
        "virtual-row-invalidated",
        "visibility-change",
        "window-focus",
        "route-popstate",
    ):
        assert event in UI_JS
    assert "_STREAM_UI_DIAGNOSTICS_MAX_ENTRIES=2000" in UI_JS
    assert "setStreamUiDiagnostics" in UI_JS
    assert "getStreamUiDiagnostics" in UI_JS


def test_live_anchor_projection_reads_the_registry_kept_by_the_lease():
    project = _function_source(UI_JS, "_projectLiveAnchorActivitySceneForStream")
    assert "window._liveAnchorRegistries" in project
    assert "map.get(streamId)" in project
    assert "if(!api||!registry" in project

