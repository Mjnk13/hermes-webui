from pathlib import Path

from api.models import Session
from api.streaming import _merge_display_messages_after_agent_result


ROOT = Path(__file__).resolve().parents[1]


def test_combined_queue_promotion_keeps_one_stable_identity_and_context_parts():
    ui = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    messages = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")

    assert "client_message_id:_newClientMessageId(sid,'queue-combined')" in ui
    assert "combined_queue_message_ids:combinedMessageIds" in ui
    assert "snapshot.flatMap(e=>_entryBrowserContextParts(e))" in ui
    assert "parts:combinedBrowserContextParts" in ui
    assert "client_message_id:clientMessageId" in messages
    assert "client_message_id:clientMessageId," in messages


def test_queue_requeue_is_idempotent_and_preserves_fifo_position():
    ui = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    messages = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")

    assert "item.client_message_id===entry.client_message_id" in ui
    assert "q.unshift(entry)" in ui
    assert "_queueDrainPromotionPending" in ui
    assert messages.count("restoreShiftedQueuedSessionMessage(_queueSid,queuedMessage)") == 2


def test_confirmed_current_turn_keeps_identity_and_browser_context():
    old = {
        "role": "user",
        "content": "same text",
        "client_message_id": "message:s:old",
        "timestamp": 1,
    }
    prior_assistant = {"role": "assistant", "content": "prior answer", "timestamp": 2}
    browser_parts = [
        {"type": "text", "content": "same text "},
        {"type": "browser_context", "url": "https://example.test", "label": "Example"},
    ]
    result = [
        old.copy(),
        prior_assistant.copy(),
        {"role": "user", "content": "same text", "timestamp": 3},
        {"role": "assistant", "content": "new answer", "timestamp": 4},
    ]

    merged = _merge_display_messages_after_agent_result(
        [old.copy(), prior_assistant.copy()],
        [old.copy(), prior_assistant.copy()],
        result,
        "same text",
        current_context_items=[{"kind": "browser", "url": "https://example.test"}],
        current_browser_context_parts=browser_parts,
        current_client_message_id="queue-combined:s:new",
    )

    users = [message for message in merged if message.get("role") == "user"]
    assert len(users) == 2  # identical text is allowed across intentional turns
    assert users[-1]["client_message_id"] == "queue-combined:s:new"
    assert users[-1]["browser_context_parts"] == browser_parts
    assert users[-1]["parts"] == browser_parts


def test_pending_client_identity_survives_session_serialization(tmp_path, monkeypatch):
    monkeypatch.setattr("api.models.SESSION_DIR", tmp_path)
    session = Session(
        session_id="queueidentity",
        pending_user_message="queued prompt",
        pending_context_items=[{"kind": "browser"}],
        pending_browser_context_parts=[{"type": "browser_context", "url": "https://example.test"}],
        pending_client_message_id="queue-combined:queueidentity:abc",
    )
    session.save(skip_index=True)

    restored = Session.load("queueidentity")
    assert restored is not None
    assert restored.pending_client_message_id == "queue-combined:queueidentity:abc"
    assert restored.compact()["pending_client_message_id"] == "queue-combined:queueidentity:abc"


def test_frontend_reconciliation_prefers_client_identity_over_text():
    sessions = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
    ui = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")

    assert "if(aClientId||bClientId) return !!(aClientId&&bClientId&&aClientId===bClientId)" in sessions
    assert "_mergeTranscriptMessageMetadata(existing,candidate)" in sessions
    assert "pending_client_message_id" in ui
    assert "browser_context_parts:browserContextParts.length?browserContextParts:undefined" in ui
    assert "parts:browserContextParts.length?browserContextParts:undefined" in ui
