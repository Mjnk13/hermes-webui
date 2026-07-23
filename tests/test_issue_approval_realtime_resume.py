"""Approval must not leave an already-open live stream bound to stale DOM."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _extract_fn(name: str, prefix: str = "function ") -> str:
    start = MESSAGES_JS.index(f"{prefix}{name}(")
    params = MESSAGES_JS.index("(", start)
    paren_depth = 0
    params_end = -1
    for idx in range(params, len(MESSAGES_JS)):
        if MESSAGES_JS[idx] == "(":
            paren_depth += 1
        elif MESSAGES_JS[idx] == ")":
            paren_depth -= 1
            if paren_depth == 0:
                params_end = idx
                break
    assert params_end >= 0, f"{name} parameters not closed"
    brace = MESSAGES_JS.index("{", params_end)
    depth = 0
    for idx in range(brace, len(MESSAGES_JS)):
        if MESSAGES_JS[idx] == "{":
            depth += 1
        elif MESSAGES_JS[idx] == "}":
            depth -= 1
            if depth == 0:
                return MESSAGES_JS[start:idx + 1]
    raise AssertionError(f"{name} body not closed")


def _run_node(script: str) -> dict:
    assert NODE
    result = subprocess.run(
        [NODE, "-e", script],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_control_resume_reclaims_same_stream_and_rebinds_replaced_view():
    helper = _extract_fn("_resumeVisibleLiveStreamAfterControl")
    script = f"""
let rebinds=0;
let sendUpdates=0;
let topbarUpdates=0;
const S={{
  session:{{session_id:'sid-1',active_stream_id:'stream-1'}},
  activeStreamId:null,
  busy:false,
}};
const LIVE_STREAMS={{
  'sid-1':{{
    streamId:'stream-1',
    source:{{readyState:1}},
    rebindView:()=>{{rebinds+=1;return true;}},
  }},
}};
const EventSource={{CLOSED:2}};
function updateSendBtn(){{sendUpdates+=1;}}
function syncTopbar(){{topbarUpdates+=1;}}
function showLiveRunStatus(){{}}
{helper}
const resumed=_resumeVisibleLiveStreamAfterControl('sid-1');
process.stdout.write(JSON.stringify({{
  resumed,
  activeStreamId:S.activeStreamId,
  sessionStreamId:S.session.active_stream_id,
  busy:S.busy,
  rebinds,
  sendUpdates,
  topbarUpdates,
}}));
"""

    assert _run_node(script) == {
        "resumed": True,
        "activeStreamId": "stream-1",
        "sessionStreamId": "stream-1",
        "busy": True,
        "rebinds": 1,
        "sendUpdates": 1,
        "topbarUpdates": 1,
    }


def test_control_resume_never_steals_a_different_active_stream():
    helper = _extract_fn("_resumeVisibleLiveStreamAfterControl")
    script = f"""
let rebinds=0;
const S={{
  session:{{session_id:'sid-1',active_stream_id:'stream-new'}},
  activeStreamId:'stream-new',
  busy:true,
}};
const LIVE_STREAMS={{
  'sid-1':{{
    streamId:'stream-old',
    source:{{readyState:1}},
    rebindView:()=>{{rebinds+=1;return true;}},
  }},
}};
const EventSource={{CLOSED:2}};
{helper}
const resumed=_resumeVisibleLiveStreamAfterControl('sid-1');
process.stdout.write(JSON.stringify({{
  resumed,
  activeStreamId:S.activeStreamId,
  sessionStreamId:S.session.active_stream_id,
  rebinds,
}}));
"""

    assert _run_node(script) == {
        "resumed": False,
        "activeStreamId": "stream-new",
        "sessionStreamId": "stream-new",
        "rebinds": 0,
    }


def test_successful_approval_resumes_the_existing_stream_without_starting_one():
    body = _extract_fn("respondApproval", prefix="async function ")

    assert "_resumeVisibleLiveStreamAfterControl(sid)" in body
    success = body[body.index("if (result && result.ok)") :]
    resume = success.index("_resumeVisibleLiveStreamAfterControl(sid)")
    assert resume < success.index("return;", resume)
    assert "/api/chat/start" not in success


def test_live_transport_exposes_a_view_rebind_callback():
    attach = _extract_fn("attachLiveStream")

    assert "function _rebindVisibleLiveView()" in attach
    assert "LIVE_STREAMS[activeSid]={streamId,source,rebindView:_rebindVisibleLiveView};" in attach
    assert "rendererNode&&rendererNode.isConnected===false" in attach


def test_same_session_refresh_cannot_overwrite_newer_live_metering():
    assert "const _loadedSessionUsage={...(data.session.last_usage||{})};" in SESSIONS_JS
    assert "sameSessionForceReload&&(S.activeStreamId||data.session.active_stream_id)" in SESSIONS_JS
    assert "_mergeUsageForCtxIndicator(S.lastUsage||{},_loadedSessionUsage)" in SESSIONS_JS
