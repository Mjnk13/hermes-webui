from api.streaming import (
    _append_checkpoint_paths_turn_journal_event,
    _append_mutation_paths_turn_journal_event,
    checkpoint_events_from_turn_boundary,
    mutation_path_events_from_tool_calls,
)


def test_mutation_path_events_extracts_structured_mutator_args():
    events = mutation_path_events_from_tool_calls([
        {"name": "write_file", "args": {"path": "./static/workspace.js"}},
        {"name": "functions.patch", "args": {"file_path": "~/api/streaming.py"}},
        {"name": "terminal", "args": {"command": "touch ignored.py"}},
    ])

    assert events == [
        {"path": "static/workspace.js", "source": "write_file"},
        {"path": "api/streaming.py", "source": "patch"},
    ]


def test_mutation_path_events_extracts_openai_tool_call_arguments_json():
    events = mutation_path_events_from_tool_calls([
        {
            "function": {
                "name": "edit_file",
                "arguments": '{"path":"docs/CONTRACTS.md"}',
            },
            "id": "call-1",
        }
    ])

    assert events == [{"path": "docs/CONTRACTS.md", "source": "edit_file"}]


def test_mutation_path_events_filters_noise_and_dedupes_paths():
    events = mutation_path_events_from_tool_calls([
        {"name": "write_file", "args": {"path": "node_modules/pkg/index.js"}},
        {"name": "write_file", "args": {"path": "https://example.test/file.py"}},
        {"name": "write_file", "args": {"path": "README.md"}},
        {"name": "patch", "args": {"path": "./README.md"}},
    ])

    assert events == [{"path": "README.md", "source": "write_file"}]


def test_mutation_path_events_extracts_diff_fence_when_structured_args_absent():
    events = mutation_path_events_from_tool_calls([
        {
            "name": "patch",
            "args": {},
            "snippet": "```diff\n--- a/api/routes.py\n+++ b/api/routes.py\n@@\n-old\n+new\n```",
        }
    ])

    assert events == [{"path": "api/routes.py", "source": "diff"}]


def test_append_mutation_paths_turn_journal_event_writes_anchor_payload(monkeypatch):
    captured = {}

    def fake_append(session_id, stream_id, event):
        captured["session_id"] = session_id
        captured["stream_id"] = stream_id
        captured["event"] = event
        return event

    monkeypatch.setattr("api.streaming.append_turn_journal_event_for_stream", fake_append)

    _append_mutation_paths_turn_journal_event(
        "sid-1",
        "stream-1",
        [{"name": "write_file", "args": {"path": "static/workspace.js"}}],
        assistant_message_index=7,
    )

    assert captured["session_id"] == "sid-1"
    assert captured["stream_id"] == "stream-1"
    assert captured["event"]["event"] == "mutation_paths"
    assert captured["event"]["assistant_message_index"] == 7
    assert captured["event"]["paths"] == [
        {"path": "static/workspace.js", "source": "write_file"},
    ]


def test_checkpoint_events_from_turn_boundary_extracts_new_ids_only():
    events = checkpoint_events_from_turn_boundary(
        {"cp-1", "cp-3"},
        {"cp-1", "cp-2", "cp-3", "cp-4"},
    )

    assert events == [
        {"checkpoint_id": "cp-2"},
        {"checkpoint_id": "cp-4"},
    ]


def test_append_checkpoint_paths_turn_journal_event_writes_anchor_payload(monkeypatch):
    captured = {}

    def fake_append(session_id, stream_id, event):
        captured["session_id"] = session_id
        captured["stream_id"] = stream_id
        captured["event"] = event
        return event

    monkeypatch.setattr("api.streaming.append_turn_journal_event_for_stream", fake_append)
    monkeypatch.setattr("api.streaming._checkpoint_ids_for_turn_journal", lambda workspace: {"before", "after"})

    _append_checkpoint_paths_turn_journal_event(
        "sid-1",
        "stream-1",
        "/tmp/workspace",
        {"before"},
        assistant_message_index=9,
    )

    assert captured["session_id"] == "sid-1"
    assert captured["stream_id"] == "stream-1"
    assert captured["event"]["event"] == "checkpoint_paths"
    assert captured["event"]["assistant_message_index"] == 9
    assert captured["event"]["checkpoint_id"] == "after"
    assert captured["event"]["checkpoints"] == [{"checkpoint_id": "after"}]
