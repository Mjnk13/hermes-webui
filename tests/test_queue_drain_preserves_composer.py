"""Queued-message drain must not borrow or clear the visible composer."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _extract_fn(source: str, name: str, prefix: str = "function ") -> str:
    start = source.index(f"{prefix}{name}(")
    params = source.index("(", start)
    parens = 0
    params_end = -1
    for idx in range(params, len(source)):
        if source[idx] == "(":
            parens += 1
        elif source[idx] == ")":
            parens -= 1
            if parens == 0:
                params_end = idx
                break
    assert params_end >= 0
    brace = source.index("{", params_end)
    depth = 0
    for idx in range(brace, len(source)):
        if source[idx] == "{":
            depth += 1
        elif source[idx] == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise AssertionError(f"{name} body not closed")


def test_queue_drain_passes_payload_directly_without_mutating_composer():
    set_busy = _extract_fn(UI_JS, "setBusy")
    script = f"""
const input={{value:'draft currently being typed'}};
const queued={{
  text:'queued prompt',
  files:[{{name:'queued.txt'}}],
  context_items:[{{type:'file',path:'queued.js'}}],
  browser_context_parts:[{{type:'text',content:'queued prompt'}}],
  model:'queued-model',
  model_provider:'queued-provider',
}};
const originalFiles=[{{name:'draft.txt'}}];
const originalContext=[{{type:'file',path:'draft.js'}}];
const S={{
  busy:true,
  session:{{session_id:'sid-1',model:'old-model'}},
  pendingFiles:originalFiles,
  pendingContextItems:originalContext,
}};
let _queueDrainSid='sid-1';
let sent=[];
let shifts=0;
function $(id){{return id==='msg'?input:null;}}
function updateSendBtn(){{}}
function renderChangedThisTurn(){{}}
function _clearActivityElapsedTimer(){{}}
function setStatus(){{}}
function setComposerStatus(){{}}
function updateQueueBadge(){{}}
function shiftQueuedSessionMessage(){{shifts+=1;return queued;}}
function queueSessionMessage(){{throw new Error('must not requeue');}}
function setTimeout(fn){{fn();return 1;}}
function send(options){{sent.push(options);return Promise.resolve();}}
function _applyModelToDropdown(){{}}
function syncModelChip(){{}}
function renderTray(){{}}
function autoResize(){{}}
global.window={{}};
{set_busy}
setBusy(false);
process.stdout.write(JSON.stringify({{
  input:input.value,
  sameFiles:S.pendingFiles===originalFiles,
  sameContext:S.pendingContextItems===originalContext,
  shifts,
  sendCalls:sent.length,
  queuedPayload:sent[0]&&sent[0].queuedMessage,
  queueDrain:sent[0]&&sent[0].queueDrain,
}}));
"""
    result = subprocess.run(
        [NODE, "-e", script],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "input": "draft currently being typed",
        "sameFiles": True,
        "sameContext": True,
        "shifts": 1,
        "sendCalls": 1,
        "queuedPayload": {
            "text": "queued prompt",
            "files": [{"name": "queued.txt"}],
            "context_items": [{"type": "file", "path": "queued.js"}],
            "browser_context_parts": [{"type": "text", "content": "queued prompt"}],
            "model": "queued-model",
            "model_provider": "queued-provider",
        },
        "queueDrain": True,
    }


def test_queue_drain_send_path_does_not_clear_visible_composer_or_draft():
    send = _extract_fn(MESSAGES_JS, "send", prefix="async function ")

    assert "const queueDrain=!!(options&&options.queueDrain&&queuedMessage);" in send
    assert "if(!queueDrain) _flushSelectionBlocksToComposer();" in send
    assert "const _submittedDraftTextForClear=queueDrain?'':($('msg').value||'');" in send
    assert "if(!queueDrain){$('msg').value='';autoResize();}" in send
    assert "if (!queueDrain && activeSid && typeof _clearComposerDraft === 'function')" in send
