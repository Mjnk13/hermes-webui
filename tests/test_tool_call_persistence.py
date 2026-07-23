"""Tests for backend tool-call summary extraction used by WebUI session persistence."""
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

from api.streaming import (
    _extract_tool_calls_from_messages,
    _tool_result_snippet,
    _tool_result_structured_payload,
)


def test_extract_tool_calls_from_openai_message_linkage():
    messages = [
        {"role": "user", "content": "ls"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-1",
                "function": {"name": "terminal", "arguments": '{"command":"ls"}'},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": '{"output":"file.txt","exit_code":0}',
        },
    ]
    result = _extract_tool_calls_from_messages(messages)
    assert len(result) == 1
    assert result[0]["name"] == "terminal"
    assert result[0]["assistant_msg_idx"] == 1
    assert result[0]["snippet"] == "file.txt"


def test_tool_result_snippet_allows_frontend_show_more_threshold_but_stays_bounded():
    """Persisted snippets should be long enough for frontend Show more but capped."""
    medium_output = "m" * 1200
    huge_output = "h" * 5000

    medium_snippet = _tool_result_snippet(json.dumps({"output": medium_output}))
    huge_snippet = _tool_result_snippet(json.dumps({"output": huge_output}))

    assert len(medium_snippet) == 1200
    assert len(medium_snippet) > 800
    assert len(huge_snippet) == 4000


def test_read_file_content_is_unwrapped_before_preview_cap_and_raw_wrapper_is_retained():
    source = "\n".join(
        (
            "108|    canWrite,",
            "109|    className = '',",
            "110|    locale,",
            "111|}: EventPageEditorProps): React.JSX.Element => {",
            "112|    const { t } = useAdminI18n();",
        )
    )
    raw = json.dumps({"content": source})
    path = "src/app/admin/(shell)/events/_components/EventPageEditor/impl.tsx"
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-read",
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"path": path, "offset": 108, "limit": 440}),
                },
            }],
        },
        {"role": "tool", "tool_call_id": "call-read", "content": raw},
    ]

    assert _tool_result_snippet(raw) == source
    assert r"\n" not in _tool_result_snippet(raw)

    result = _extract_tool_calls_from_messages(messages)
    assert result[0]["snippet"] == source
    assert result[0]["result"] == raw
    assert result[0]["args"]["path"] == path


def test_structured_result_payload_preserves_exact_json_string_and_rejects_unsafe_fallbacks():
    raw = ' {\n  "content": "literal \\\\n stays literal"\n} '

    assert _tool_result_structured_payload(raw) == raw
    assert _tool_result_structured_payload("{'content': 'not json'}") is None
    assert _tool_result_structured_payload('{"content":"' + ("x" * 100_001) + '"}') is None


def test_tool_result_snippet_selects_large_dynamic_multiline_value_without_key_allowlist():
    lines = [f"{index}: compiler diagnostic {index}" for index in range(1, 260)]
    report = "\n".join(lines)
    raw = json.dumps({
        "ok": False,
        "target": "src/service.py",
        "compiler_transcript": report,
    })

    snippet = _tool_result_snippet(raw)

    assert snippet == report[:4000]
    assert snippet.startswith("1: compiler diagnostic 1\n2: compiler diagnostic 2")
    assert not snippet.startswith('{"ok"')
    assert r"\n" not in snippet


def test_tool_result_snippet_recurses_through_transport_wrappers_by_value_shape():
    report = "migration 1 complete\nmigration 2 complete\nmigration 3 complete"
    raw = json.dumps({
        "transport": {
            "request_id": "req-1",
            "migration_notes": report,
        },
    })

    assert _tool_result_snippet(raw) == report


def test_extract_tool_calls_persists_show_more_sized_snippets_with_bounded_cap():
    """Tool-call summaries should store >800-char snippets without growing unbounded."""
    long_output = "x" * 1200
    huge_output = "y" * 5000
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-long",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"/tmp/medium.log"}',
                    },
                },
                {
                    "id": "call-huge",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"yes"}',
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-long",
            "content": json.dumps({"output": long_output}),
        },
        {
            "role": "tool",
            "tool_call_id": "call-huge",
            "content": json.dumps({"output": huge_output}),
        },
    ]

    result = _extract_tool_calls_from_messages(messages)

    assert len(result) == 2
    assert len(result[0]["snippet"]) == 1200
    assert len(result[0]["snippet"]) > 800
    assert len(result[1]["snippet"]) == 4000


def test_extract_tool_calls_falls_back_to_live_progress_when_ids_missing():
    messages = [
        {"role": "user", "content": "write spec"},
        {"role": "assistant", "content": "Starting."},
        {"role": "tool", "content": '{"bytes_written":4955}'},
        {"role": "assistant", "content": ""},
    ]
    live_tool_calls = [{"name": "write_file", "args": {"path": "/tmp/SPEC.md"}}]
    result = _extract_tool_calls_from_messages(messages, live_tool_calls=live_tool_calls)
    assert len(result) == 1
    assert result[0]["name"] == "write_file"
    assert result[0]["assistant_msg_idx"] == 1
    assert "bytes_written" in result[0]["snippet"]
    assert result[0]["args"]["path"] == "/tmp/SPEC.md"


def test_extract_tool_calls_preserves_mixed_linked_and_fallback_results():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": '{"command":"pwd"}'}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"output":"/tmp"}'},
        {"role": "assistant", "content": "Next"},
        {"role": "tool", "content": '{"result":"saved"}'},
    ]
    live_tool_calls = [
        {"name": "terminal", "args": {"command": "pwd"}},
        {"name": "write_file", "args": {"path": "/tmp/out.txt"}},
    ]
    result = _extract_tool_calls_from_messages(messages, live_tool_calls=live_tool_calls)
    assert len(result) == 2
    assert result[0]["name"] == "terminal"
    assert result[1]["name"] == "write_file"
    assert result[1]["assistant_msg_idx"] == 2
    assert result[1]["snippet"] == "saved"
