"""Guards for checkpoint restore options (files/chat/both)."""

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ROUTES_PY = (REPO / "api" / "routes.py").read_text(encoding="utf-8")
PANELS_JS = (REPO / "static" / "panels.js").read_text(encoding="utf-8")
I18N_JS = (REPO / "static" / "i18n.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO / "static" / "style.css").read_text(encoding="utf-8")


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


def test_checkpoint_restore_route_accepts_files_chat_and_combined_modes():
    assert '_CHECKPOINT_RESTORE_MODES = _CHECKPOINT_RESTORE_CHAT_MODES | _CHECKPOINT_RESTORE_FILE_MODES' in ROUTES_PY
    assert 'mode = str(body.get("mode") or "files_only")' in ROUTES_PY
    assert 'mode must be one of files_only, chat_only, chat_files' in ROUTES_PY
    assert '_checkpoint_chat_restore_plan(session_id, checkpoint)' in ROUTES_PY
    assert 'restore_checkpoint(workspace, checkpoint)' in ROUTES_PY
    assert 'require_complete=True' in ROUTES_PY


def test_checkpoint_restore_panel_shows_mode_picker_and_posts_selected_mode():
    choose_body = _function_body(PANELS_JS, "_chooseCheckpointRestoreMode")
    restore_body = _function_body(PANELS_JS, "_restoreCheckpoint")

    assert 'data-mode="files_only"' in choose_body
    assert 'data-mode="chat_files"' in choose_body
    assert 'data-mode="chat_only"' in choose_body
    assert "checkpoint-restore-options-modal" in choose_body
    assert "const mode=await _chooseCheckpointRestoreMode(label);" in restore_body
    assert "JSON.stringify({workspace,checkpoint,mode,session_id:sid})" in restore_body
    assert "await loadSession(sid,{force:true})" in restore_body


def test_checkpoint_restore_i18n_and_styles_exist():
    for key in (
        "checkpoint_restore_options_title",
        "checkpoint_restore_files_only",
        "checkpoint_restore_chat_files",
        "checkpoint_restore_chat_only",
    ):
        assert f"{key}:" in I18N_JS
    assert ".checkpoint-restore-options-modal" in STYLE_CSS
    assert ".checkpoint-restore-option.danger" in STYLE_CSS


def test_checkpoint_restore_turn_journal_helpers_find_associated_turn():
    from api.routes import (
        _checkpoint_assistant_index_from_events,
        _checkpoint_chat_restore_keep_count,
        _checkpoint_ids_from_turn_journal_event,
    )

    event = {
        "event": "checkpoint_paths",
        "assistant_message_index": 5,
        "checkpoint_id": "ck-single",
        "checkpoints": [{"checkpoint_id": "ck-nested"}],
    }
    assert _checkpoint_ids_from_turn_journal_event(event) == {"ck-single", "ck-nested"}
    assert _checkpoint_assistant_index_from_events([event], "ck-nested") == 5
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first reply"},
        {"role": "user", "content": "turn to remove"},
        {"role": "assistant", "content": "created checkpoint"},
    ]
    assert _checkpoint_chat_restore_keep_count(messages, 3) == 2


def test_checkpoint_restore_file_errors_block_combined_chat_restore(monkeypatch):
    from api import routes

    def fake_restore_checkpoint(workspace, checkpoint):
        return {
            "ok": True,
            "workspace": workspace,
            "checkpoint": checkpoint,
            "files_restored": ["kept.py"],
            "errors": [{"file": "failed.py", "error": "permission denied"}],
        }

    monkeypatch.setattr("api.rollback.restore_checkpoint", fake_restore_checkpoint)

    with pytest.raises(ValueError, match="chat was not truncated"):
        routes._restore_checkpoint_files_for_restore_mode(
            "/tmp/workspace",
            "checkpoint-1",
            require_complete=True,
        )


def test_checkpoint_restore_file_only_mode_allows_partial_file_errors(monkeypatch):
    from api import routes

    def fake_restore_checkpoint(workspace, checkpoint):
        return {
            "ok": True,
            "workspace": workspace,
            "checkpoint": checkpoint,
            "files_restored": ["kept.py"],
            "errors": [{"file": "failed.py", "error": "permission denied"}],
        }

    monkeypatch.setattr("api.rollback.restore_checkpoint", fake_restore_checkpoint)

    result = routes._restore_checkpoint_files_for_restore_mode(
        "/tmp/workspace",
        "checkpoint-1",
        require_complete=False,
    )

    assert result["errors"] == [{"file": "failed.py", "error": "permission denied"}]
