"""Regression coverage for a silently stalled, still-OPEN chat EventSource.

The browser does not always emit ``EventSource.onerror`` when a long-lived
connection stops delivering frames.  The backend run journal can continue
advancing while the selected thread remains visually frozen; switching away
and back appears to fix it only because that path opens a replay connection.

The live-stream owner must therefore compare its local journal cursor with the
authoritative status cursor after a quiet period and replay only when the
server is provably ahead.
"""

import json
import pathlib
import subprocess


ROOT = pathlib.Path(__file__).resolve().parents[1]
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")


def _function_source(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    if source[max(0, start - 6) : start] == "async ":
        start -= 6
    params = source.index("(", start)
    paren_depth = 0
    quote = None
    escaped = False
    brace = None
    for index in range(params, len(source)):
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
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth -= 1
            if paren_depth == 0:
                brace = source.index("{", index)
                break
    if brace is None:
        raise AssertionError(f"unterminated parameters for function {name}")
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


def test_recovery_decision_requires_authoritative_server_progress():
    """An idle model is not a stalled connection; only a cursor gap recovers."""

    cursor_fn = _function_source(MESSAGES_JS, "_streamStatusJournalCursor")
    decision_fn = _function_source(MESSAGES_JS, "_shouldRecoverSilentLiveStream")
    harness = f"""
const assert = require('assert');
{cursor_fn}
{decision_fn}
const base = {{
  localSeq: 40,
  quietMs: 20000,
  quietThresholdMs: 12000,
  isVisibleOwner: true,
}};
assert.strictEqual(_shouldRecoverSilentLiveStream({{
  ...base,
  status: {{active:true,journal:{{last_seq:45}}}},
}}), true, 'server cursor ahead must recover');
assert.strictEqual(_shouldRecoverSilentLiveStream({{
  ...base,
  status: {{active:true,journal:{{last_seq:40}}}},
}}), false, 'equal cursor is a legitimately quiet stream');
assert.strictEqual(_shouldRecoverSilentLiveStream({{
  ...base,
  status: {{active:true,journal:{{last_seq:39}}}},
}}), false, 'an older server cursor must never rewind the client');
assert.strictEqual(_shouldRecoverSilentLiveStream({{
  ...base,
  quietMs: 5000,
  status: {{active:true,journal:{{last_seq:45}}}},
}}), false, 'recent activity must suppress recovery');
assert.strictEqual(_shouldRecoverSilentLiveStream({{
  ...base,
  isVisibleOwner: false,
  status: {{active:true,journal:{{last_seq:45}}}},
}}), false, 'a background/non-owner stream must not reconnect');
assert.strictEqual(_shouldRecoverSilentLiveStream({{
  ...base,
  status: {{active:false,replay_available:false,journal:{{last_seq:45}}}},
}}), false, 'missing live/replay authority must not reconnect');
assert.strictEqual(_shouldRecoverSilentLiveStream({{
  ...base,
  status: {{active:false,replay_available:true,journal:{{last_seq:'45'}}}},
}}), true, 'a settled replay with missing terminal frames must recover');
console.log(JSON.stringify({{ok:true}}));
"""
    result = subprocess.run(
        ["node", "-e", harness],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True}


def test_selected_stream_watchdog_replays_a_silent_open_source():
    wire = _function_source(MESSAGES_JS, "_wireSSE")
    probe = _function_source(MESSAGES_JS, "_probeSilentOpenStream")
    remember = _function_source(MESSAGES_JS, "_rememberRunJournalCursor")

    assert "_startStreamLivenessWatchdog(source)" in wire
    assert "_noteRunJournalActivity()" in remember
    assert "/api/chat/stream/status?stream_id=" in probe
    assert "_shouldRecoverSilentLiveStream" in probe
    assert "currentLive.source!==source" in "".join(probe.split())
    assert "_isSessionCurrentPane(activeSid)" in probe
    assert "document.hidden" in probe
    assert "_runJournalReplayParams()" in probe
    assert "_wireSSE(new EventSource" in probe


def test_probe_reopens_with_replay_cursor_when_server_is_ahead():
    """Exercise the production probe rather than only source-locking its wiring."""

    cursor_fn = _function_source(MESSAGES_JS, "_streamStatusJournalCursor")
    decision_fn = _function_source(MESSAGES_JS, "_shouldRecoverSilentLiveStream")
    probe_fn = _function_source(MESSAGES_JS, "_probeSilentOpenStream")
    harness = f"""
const assert = require('assert');
{cursor_fn}
{decision_fn}
{probe_fn}
const activeSid='sid-quiet';
const streamId='stream-quiet';
const S={{
  session:{{session_id:activeSid}},
  activeStreamId:streamId,
}};
class FakeEventSource {{
  static OPEN=1;
  constructor(url,options){{
    this.url=String(url);
    this.options=options;
    this.readyState=FakeEventSource.OPEN;
  }}
}}
const EventSource=FakeEventSource;
const source=new FakeEventSource('http://localhost/original');
const LIVE_STREAMS={{
  [activeSid]:{{streamId,source}},
}};
const document={{
  hidden:false,
  visibilityState:'visible',
  wasDiscarded:false,
  baseURI:'http://localhost/app/',
}};
const location={{href:'http://localhost/app/'}};
let _streamLivenessGeneration=7;
let _terminalStateReached=false;
let _streamFinalized=false;
let _streamLivenessProbePending=false;
let _lastRunJournalEventAt=0;
let _lastRunJournalSeq=40;
const _STREAM_LIVENESS_QUIET_MS=12000;
const _STREAM_LIVENESS_POLL_MS=8000;
let now=20000;
let scheduled=0;
let wired=null;
let composerStatus='';
let status={{active:true,replay_available:true,journal:{{last_seq:45}}}};
function _streamLivenessNow(){{return now;}}
function _scheduleStreamLivenessProbe(){{scheduled+=1;}}
function _isSessionCurrentPane(sid){{return sid===activeSid;}}
function _runJournalReplayParams(){{return '&replay=1&after_seq=40&after_event_id=stream-quiet%3A40';}}
function _streamDiagnostic(){{}}
function setComposerStatus(value){{composerStatus=value;}}
function _wireSSE(next){{wired=next;}}
async function api(){{return status;}}
(async()=>{{
  await _probeSilentOpenStream(source,7);
  assert.ok(wired,'the silent OPEN source must be replaced');
  assert.ok(wired.url.includes('replay=1'));
  assert.ok(wired.url.includes('after_seq=40'));
  assert.strictEqual(composerStatus,'Restoring live updates…');
  assert.strictEqual(_streamLivenessProbePending,false);

  wired=null;
  status={{active:true,replay_available:true,journal:{{last_seq:40}}}};
  await _probeSilentOpenStream(source,7);
  assert.strictEqual(wired,null,'an equal cursor must leave the healthy source alone');
  assert.ok(scheduled>=1,'a healthy quiet stream must keep its later liveness check');
  console.log(JSON.stringify({{ok:true}}));
}})().catch(error=>{{
  console.error(error);
  process.exit(1);
}});
"""
    result = subprocess.run(
        ["node", "-e", harness],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True}


def test_watchdog_is_disposed_with_stream_ownership():
    close_start = MESSAGES_JS.index("function closeLiveStream(")
    close_end = MESSAGES_JS.index("function closeOtherLiveStreams(", close_start)
    close = MESSAGES_JS[close_start:close_end]
    wire = _function_source(MESSAGES_JS, "_wireSSE")

    assert "live.dispose" in close
    assert "dispose=_clearStreamLivenessWatchdog" in "".join(wire.split())
    assert "existingLive.dispose" in wire
