"""
Tests for session queue persistence across page refresh and tab restore.

#660 introduced sessionStorage persistence. #3108 hardens it by mirroring queue
state to localStorage and restoring from the durable copy when sessionStorage is
missing after browser tab/process restore.
"""
import json
import pathlib
import shutil
import subprocess

import pytest

UI_JS = pathlib.Path(__file__).parent.parent / 'static' / 'ui.js'
SESSIONS_JS = pathlib.Path(__file__).parent.parent / 'static' / 'sessions.js'
PANELS_JS = pathlib.Path(__file__).parent.parent / 'static' / 'panels.js'

ui_src = UI_JS.read_text(encoding='utf-8')
sess_src = SESSIONS_JS.read_text(encoding='utf-8')
panels_src = PANELS_JS.read_text(encoding='utf-8')
NODE = shutil.which("node")


def _extract_fn(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
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
    brace = source.index("{", params_end)
    depth = 0
    for idx in range(brace, len(source)):
        if source[idx] == "{":
            depth += 1
        elif source[idx] == "}":
            depth -= 1
            if depth == 0:
                return source[start:idx + 1]
    raise AssertionError(f"{name} body not closed")


class TestQueuePersistence:
    """queueSessionMessage persists through the shared dual-storage helper."""

    def test_queue_storage_helpers_exist(self):
        """Queue persistence must be centralized so write/delete paths stay symmetric."""
        assert "function _queueStorageKey(sid)" in ui_src
        assert "function _persistSessionQueueStorage(sid, queue)" in ui_src
        assert "function _readPersistedSessionQueue(sid)" in ui_src
        assert "function _clearPersistedSessionQueue(sid)" in ui_src

    def test_queue_writes_to_session_and_local_storage(self):
        """queueSessionMessage must mirror queue state to sessionStorage and localStorage."""
        helper_start = ui_src.find("function _persistSessionQueueStorage(sid, queue)")
        helper_end = ui_src.find("function _readPersistedSessionQueue(sid)", helper_start)
        assert helper_start != -1 and helper_end != -1, "_persistSessionQueueStorage helper not found"
        helper = ui_src[helper_start:helper_end]
        assert "sessionStorage.setItem(key,payload)" in helper
        assert "localStorage.setItem(key,payload)" in helper

    def test_queue_stamps_queued_at_timestamp(self):
        """Each queue entry needs a stable timestamp for edit/reorder identity."""
        assert '_queued_at' in ui_src

    def test_shift_uses_shared_persist_and_clear_helpers(self):
        """shiftQueuedSessionMessage must update/remove both storage layers through helpers."""
        start = ui_src.find("function shiftQueuedSessionMessage(sid)")
        end = ui_src.find("function getQueuedSessionCount(sid)", start)
        assert start != -1 and end != -1, "shiftQueuedSessionMessage block not found"
        body = ui_src[start:end]
        assert "_clearPersistedSessionQueue(sid)" in body
        assert "_persistSessionQueueStorage(sid,q)" in body

    def test_queue_card_edit_paths_use_shared_helpers(self):
        """Queue edit/combine/delete paths must not leave localStorage stale."""
        assert "_saveAndRefresh()" in ui_src
        assert "_persistSessionQueueStorage(sid,liveQ)" in ui_src
        assert "_clearPersistedSessionQueue(sid)" in ui_src


class TestQueueRestore:
    """Queue is rehydrated from storage when a chat thread is revisited."""

    def test_restore_reads_shared_helper(self):
        """sessions.js must use the shared helper so localStorage fallback is reachable."""
        assert "_hydrateSessionQueueFromStorage(sid)" in sess_src
        hydrate = _extract_fn(ui_src, "_hydrateSessionQueueFromStorage")
        assert "_readPersistedSessionQueue(sid)" in hydrate

    def test_read_helper_falls_back_to_local_storage(self):
        """The helper must fall back to localStorage and re-mirror sessionStorage."""
        start = ui_src.find("function _readPersistedSessionQueue(sid)")
        end = ui_src.find("function queueSessionMessage(sid", start)
        assert start != -1 and end != -1, "_readPersistedSessionQueue block not found"
        body = ui_src[start:end]
        assert "const sessionValue=read(sessionStorage)" in body
        assert "if(sessionValue&&sessionValue.length) return sessionValue;" in body
        assert "const localValue=read(localStorage)" in body
        assert "if(localValue&&localValue.length)" in body
        assert "sessionStorage.setItem(key,JSON.stringify(localValue))" in body

    def test_restore_rehydrates_the_session_queue(self):
        """A remount must rebuild SESSION_QUEUES so the complete list renders again."""
        assert "function _hydrateSessionQueueFromStorage(sid)" in ui_src
        assert "SESSION_QUEUES[sid]=stored" in ui_src
        assert "_hydrateSessionQueueFromStorage(sid)" in sess_src

    def test_restore_refreshes_the_queue_ui(self):
        """Both active and idle load paths repaint the restored queue list."""
        assert sess_src.count("updateQueueBadge(sid);") >= 2

    def test_same_thread_reselection_reconciles_queue_ui_before_noop_return(self):
        """Selecting the open thread must repair a card hidden by tab navigation."""
        guard = "if(currentSid===sid && !forceReload && (!_loadingSessionId || _loadingSessionId===sid)){"
        start = sess_src.find(guard)
        end = sess_src.find("return;", start)
        assert start != -1 and end != -1
        body = sess_src[start:end]
        assert "_hydrateSessionQueueFromStorage(sid)" in body
        assert "updateQueueBadge(sid)" in body

    def test_queue_render_cache_requires_matching_live_dom(self):
        """A stale fingerprint must not suppress rows after the queue DOM was replaced."""
        start = ui_src.find("function _renderQueueChips(sid)")
        end = ui_src.find("function _updateQueuePill(sid,count)", start)
        assert start != -1 and end != -1
        body = ui_src[start:end]
        assert "const cachedDomMatches=" in body
        assert "inner.childElementCount>0" in body
        assert "&&cachedDomMatches" in body

    def test_restore_does_not_replace_the_visible_composer(self):
        """Restoring queue UI must not put item one into a draft or discard later items."""
        assert "_msg.value=_first.text" not in sess_src

    def test_restore_keeps_persisted_queue_until_an_explicit_queue_mutation(self):
        """Navigation/remount alone must not consume the durable queue."""
        restore_start = sess_src.find("// Restore the complete queued-message list")
        restore_end = sess_src.find("// Reconstruct tool calls", restore_start)
        assert restore_start != -1 and restore_end != -1
        assert "_clearPersistedSessionQueue(sid)" not in sess_src[restore_start:restore_end]

    def test_restore_runs_for_streaming_and_idle_threads(self):
        """Queue visibility must not depend on whether the revisited agent is still active."""
        session_assign = sess_src.find("S.session=data.session;")
        reconcile_pos = sess_src.find("_reconcileActiveSessionQueueUi('session-metadata-loaded')", session_assign)
        inflight_pos = sess_src.find("if(INFLIGHT[sid]){", session_assign)
        assert session_assign != -1 and reconcile_pos != -1 and inflight_pos != -1
        assert reconcile_pos < inflight_pos, "active INFLIGHT threads must repaint queue UI before branch selection"

    def test_returning_to_chat_panel_reconciles_active_queue(self):
        """Panel navigation does not call loadSession, so chat entry must repaint explicitly."""
        switch = _extract_fn(panels_src, "switchPanel")
        assert "_reconcileActiveSessionQueueUi('chat-panel-entered')" in switch

    def test_focus_and_visibility_return_reconcile_without_polling(self):
        """Electron/app-tab return paths should repaint once, not periodically refresh."""
        bind = _extract_fn(ui_src, "_bindActiveSessionQueueUiReconciliation")
        assert "window.addEventListener('focus'" in bind
        assert "window.addEventListener('pageshow'" in bind
        assert "document.addEventListener('visibilitychange'" in bind
        assert "setInterval" not in bind

    def test_queue_actions_are_not_mistaken_for_active_inline_edits(self):
        """Clicking Combine must repaint immediately even though its button has focus."""
        render = _extract_fn(ui_src, "_renderQueueChips")
        assert ".queue-card-text[contenteditable=\"true\"]" in render
        assert "if(activeQueueEditor) return;" in render
        assert "inner.contains(document.activeElement)&&document.activeElement!==inner" not in render

    def test_old_exit_timer_cannot_clear_returned_active_queue(self):
        clear = _extract_fn(ui_src, "_clearQueueCardDisplay")
        assert "_activeSid===_sid&&getQueuedSessionCount(_sid)>0" in clear

    def test_delete_session_clears_persisted_queue_after_success(self):
        """Deleting a session must clear localStorage-backed queue state after the API succeeds."""
        start = sess_src.find("async function deleteSession(sid, beforeDelete=null)")
        end = sess_src.find("// ── Project helpers", start)
        assert start != -1 and end != -1, "deleteSession block not found"
        body = sess_src[start:end]
        clear_pos = body.find("if(typeof _clearPersistedSessionQueue==='function') _clearPersistedSessionQueue(sid);")
        error_pos = body.find("if(deleteResult&&deleteResult.error){")
        success_pos = body.find("const response=deleteResult&&deleteResult.response;")
        assert error_pos != -1 and success_pos != -1 and clear_pos != -1
        assert success_pos < clear_pos, "queue cleanup should run only after delete success"

    def test_restore_does_not_duplicate_an_existing_memory_queue(self):
        """Normal SPA tab switches retain memory state and must not append storage copies."""
        start = ui_src.find("function _hydrateSessionQueueFromStorage(sid)")
        end = ui_src.find("function queueSessionMessage(sid", start)
        assert start != -1 and end != -1
        body = ui_src[start:end]
        assert "const existing=_getSessionQueue(sid,false);" in body
        assert "if(existing.length) return existing;" in body

    @pytest.mark.skipif(NODE is None, reason="node not on PATH")
    def test_restore_keeps_each_thread_queue_isolated(self):
        """Hydrating thread A must neither read nor mutate thread B's queue."""
        functions = "\n".join(_extract_fn(ui_src, name) for name in (
            "_getSessionQueue",
            "_queueStorageKey",
            "_readPersistedSessionQueue",
            "_hydrateSessionQueueFromStorage",
        ))
        script = f"""
const SESSION_QUEUES={{}};
function storage(initial){{
  return {{
    data:{{...initial}},
    getItem(key){{return Object.prototype.hasOwnProperty.call(this.data,key)?this.data[key]:null;}},
    setItem(key,value){{this.data[key]=String(value);}},
    removeItem(key){{delete this.data[key];}},
  }};
}}
const sessionStorage=storage({{
  'hermes-queue-thread-a':JSON.stringify([{{text:'a1'}},{{text:'a2'}}]),
  'hermes-queue-thread-b':JSON.stringify([{{text:'b1'}}]),
}});
const localStorage=storage({{}});
{functions}
const firstA=_hydrateSessionQueueFromStorage('thread-a');
const secondA=_hydrateSessionQueueFromStorage('thread-a');
const beforeB=SESSION_QUEUES['thread-b'];
const loadedB=_hydrateSessionQueueFromStorage('thread-b');
process.stdout.write(JSON.stringify({{
  a:firstA.map(x=>x.text),
  aSameReference:firstA===secondA,
  bBefore:beforeB===undefined,
  b:loadedB.map(x=>x.text),
  aAfter:SESSION_QUEUES['thread-a'].map(x=>x.text),
}}));
"""
        result = subprocess.run(
            [NODE, "-e", script], text=True, capture_output=True,
            check=False, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "a": ["a1", "a2"],
            "aSameReference": True,
            "bBefore": True,
            "b": ["b1"],
            "aAfter": ["a1", "a2"],
        }
