import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI_PATH = ROOT / "static" / "ui.js"
NODE = shutil.which("node")


def _extract_function(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    quote = None
    escaped = False
    for idx in range(brace, len(source)):
        ch = source[idx]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("'", '"', "`"):
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise AssertionError(f"unterminated function {name}")


def test_elapsed_timer_is_immediately_left_of_context_indicator():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    style = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

    timer_pos = index.index('id="threadElapsedTimer"')
    context_pos = index.index('id="ctxIndicatorWrap"')
    compression_pos = index.index('id="ctxCompressionCount"')
    assert timer_pos < context_pos < compression_pos
    assert ".thread-elapsed-timer" in style
    assert "font-variant-numeric:tabular-nums" in style


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_progressive_elapsed_format_and_single_timestamp_scheduler():
    source = UI_PATH.read_text(encoding="utf-8")
    names = (
        "_formatThreadElapsed",
        "_threadElapsedNowSeconds",
        "_renderThreadElapsedTimer",
        "showThreadElapsedTimer",
        "_startLiveRunStatusTimer",
        "_clearLiveRunStatusTimer",
    )
    functions = "\n".join(_extract_function(source, name) for name in names)
    script = f"""
const timerEl={{style:{{display:'none'}},textContent:'',title:'',setAttribute(k,v){{this[k]=v;}},removeAttribute(k){{delete this[k];}}}};
const S={{busy:true,activeStreamId:'stream-1',session:{{session_id:'sid-1',active_stream_id:'stream-1'}}}};
const _liveRunStatusTimers={{}};
let _threadElapsedTimerSessionId=null;
let nowMs=112000;
let created=0;
let cleared=0;
let callbacks=[];
function $(id){{return id==='threadElapsedTimer'?timerEl:null;}}
const Date={{now:()=>nowMs}};
function setInterval(fn){{created+=1;callbacks.push(fn);return created;}}
function clearInterval(){{cleared+=1;}}
{functions}
const formats=[12,204,8100,100800].map(_formatThreadElapsed);
showThreadElapsedTimer('sid-1',100);
showThreadElapsedTimer('sid-1',100);
const initial={{created,text:timerEl.textContent}};
nowMs=304000;
callbacks[0]();
const advanced=timerEl.textContent;
S.busy=false;S.activeStreamId=null;S.session.active_stream_id=null;
callbacks[0]();
process.stdout.write(JSON.stringify({{formats,initial,advanced,cleared,remaining:Object.keys(_liveRunStatusTimers).length,display:timerEl.style.display}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    import json

    data = json.loads(result.stdout)
    assert data["formats"] == ["12s", "3m 24s", "2h 15m", "1d 4h"]
    assert data["initial"] == {"created": 1, "text": "12s"}
    assert data["advanced"] == "3m 24s"
    assert data["cleared"] == 1
    assert data["remaining"] == 0
    assert data["display"] == "none"


def test_timer_lifecycle_uses_process_timestamp_and_only_updates_timer_node():
    ui = UI_PATH.read_text(encoding="utf-8")
    messages = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    render_fn = _extract_function(ui, "_renderThreadElapsedTimer")

    assert "S.session.pending_started_at" in messages
    assert "if(S.session) S.session.pending_started_at=Date.now()/1000;" in messages
    assert "showThreadElapsedTimer(activeSid,S.session&&S.session.pending_started_at)" in messages
    assert "showThreadElapsedTimer(sid,S.session&&S.session.pending_started_at)" in messages
    assert "hideThreadElapsedTimer(sid)" in ui
    assert "existing&&Math.abs(Number(existing.startedAt)-start)<0.001" in ui
    assert "el.textContent=text" in render_fn
    assert "renderMessages" not in render_fn
    assert "innerHTML" not in render_fn
