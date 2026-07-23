"""Static guards for the chat-only Redo From Here action."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
UI_JS = (REPO / "static" / "ui.js").read_text(encoding="utf-8")
I18N_JS = (REPO / "static" / "i18n.js").read_text(encoding="utf-8")


def _function_body(src: str, name: str) -> str:
    needle_async = f"async function {name}"
    needle_sync = f"function {name}"
    start = src.index(needle_async) if needle_async in src else src.index(needle_sync)
    brace = src.index("{", start)
    depth = 0
    for idx in range(brace, len(src)):
        if src[idx] == "{":
            depth += 1
        elif src[idx] == "}":
            depth -= 1
            if depth == 0:
                return src[start : idx + 1]
    raise AssertionError(f"function {name!r} body not found")


def test_redo_from_here_button_renders_on_user_messages():
    render_body = _function_body(UI_JS, "renderMessages")

    assert "const redoBtn  = isUser ?" in render_body
    assert "onclick=\"redoFromMessage(this)\"" in render_body
    assert "redo_from_here" in render_body


def test_redo_from_here_uses_truncate_then_send_flow():
    body = _function_body(UI_JS, "redoFromMessage")

    assert "showConfirmDialog" in body
    assert "_ensureAllMessagesLoaded" in body
    assert "absoluteMsgIdx" in body
    assert "Number(_oldestIdx)" in body
    assert "'/api/session/truncate'" in body
    assert "keep_count: absoluteMsgIdx" in body
    assert "_setComposerForRedo(redoText, redoParts)" in body
    assert "await send()" in body


def test_redo_from_here_preserves_browser_context_parts_but_clears_pending_files():
    body = _function_body(UI_JS, "redoFromMessage")
    composer_body = _function_body(UI_JS, "_setComposerForRedo")

    assert "originalMessage.parts" in body
    assert "originalMessage.browser_context_parts" in body
    assert "S.pendingFiles=[]" in body
    assert "S.pendingContextItems=[]" in body
    assert "_composerSetBrowserContextParts" in composer_body


def test_redo_from_here_i18n_keys_exist():
    assert "redo_from_here:" in I18N_JS
    assert "redo_from_here_confirm:" in I18N_JS
    assert "redo_from_here_started:" in I18N_JS
    assert "redo_from_here_failed:" in I18N_JS
