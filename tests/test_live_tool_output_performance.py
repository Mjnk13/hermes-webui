import ast
import json
from pathlib import Path
import re
import shutil
import subprocess
import textwrap
from types import SimpleNamespace

import pytest

from api.streaming import (
    _LiveToolOutputCoalescer,
    _session_payload_with_terminal_window,
)


ROOT = Path(__file__).resolve().parents[1]
STREAMING_SOURCE = (ROOT / "api" / "streaming.py").read_text(encoding="utf-8")


def _nested_function_source(name: str) -> str:
    tree = ast.parse(STREAMING_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            lines = STREAMING_SOURCE.splitlines()
            return textwrap.dedent("\n".join(lines[node.lineno - 1 : node.end_lineno]))
    raise AssertionError(f"missing function {name}")


def test_live_tool_output_burst_is_coalesced_before_sse_delivery():
    """A noisy command must not enqueue one browser event for every line."""
    delivered = []
    put = lambda event, payload: delivered.append((event, payload))
    coalescer = _LiveToolOutputCoalescer(
        put,
        interval_seconds=60,
        max_batch_chars=1_000_000,
    )
    namespace = {
        "_live_tool_output_coalescer": coalescer,
    }
    exec(_nested_function_source("on_tool_output"), namespace)
    callback = namespace["on_tool_output"]

    for index in range(1_000):
        callback("tool-perf", "terminal", "stdout", f"line {index}\n")
    coalescer.flush("tool-perf")

    tool_events = [payload for event, payload in delivered if event == "tool_output"]
    assert len(tool_events) <= 20, (
        f"1,000 output lines produced {len(tool_events)} SSE frames; this floods "
        "Electron's renderer event loop while the command is running"
    )
    assert "".join(payload["text"] for payload in tool_events) == "".join(
        f"line {index}\n" for index in range(1_000)
    )


def test_live_tool_output_batches_keep_sequence_and_stream_order():
    delivered = []
    coalescer = _LiveToolOutputCoalescer(
        lambda event, payload: delivered.append((event, payload)),
        interval_seconds=60,
        max_batch_chars=1_000_000,
    )

    coalescer.append("tool-order", "terminal", "stdout", "first\n")
    coalescer.flush("tool-order")
    coalescer.append("tool-order", "terminal", "stdout", "second\n")
    coalescer.flush("tool-order")

    payloads = [payload for event, payload in delivered if event == "tool_output"]
    assert [payload["sequence"] for payload in payloads] == [1, 2]
    assert [payload["text"] for payload in payloads] == ["first\n", "second\n"]


def test_live_tool_output_burst_is_memory_bounded_without_hiding_omission():
    delivered = []
    coalescer = _LiveToolOutputCoalescer(
        lambda event, payload: delivered.append((event, payload)),
        interval_seconds=60,
        max_batch_chars=1_024,
    )

    coalescer.append("tool-large", "terminal", "stdout", "x" * 10_000)
    coalescer.flush("tool-large")

    payload = delivered[0][1]
    assert "live output characters omitted" in payload["text"]
    assert payload["text"].endswith("x" * 1_024)
    assert len(payload["text"]) < 1_200


def test_live_tool_output_close_flushes_once_and_rejects_late_chunks():
    delivered = []
    coalescer = _LiveToolOutputCoalescer(
        lambda event, payload: delivered.append((event, payload)),
        interval_seconds=60,
    )

    coalescer.append("tool-close", "terminal", "stdout", "before close\n")
    coalescer.close()
    coalescer.append("tool-close", "terminal", "stdout", "too late\n")
    coalescer.flush()

    assert [payload["text"] for _, payload in delivered] == ["before close\n"]


def test_terminal_payload_uses_paginated_tail_instead_of_full_transcript():
    messages = []
    tool_calls = []
    for index in range(100):
        messages.append({"role": "user", "content": f"request {index}"})
        assistant_index = len(messages)
        messages.append({
            "role": "assistant",
            "content": f"reply {index}",
            "tool_calls": [{"id": f"call-{index}", "name": "terminal"}],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": f"call-{index}",
            "content": f"output {index}",
        })
        tool_calls.append({
            "tid": f"call-{index}",
            "name": "terminal",
            "assistant_msg_idx": assistant_index,
        })

    session = SimpleNamespace(
        messages=messages,
        compact=lambda: {"session_id": "large-session", "message_count": 1},
    )
    payload = _session_payload_with_terminal_window(session, tool_calls=tool_calls)

    assert payload["_messages_truncated"] is True
    assert payload["_messages_offset"] > 0
    assert payload["message_count"] == len(messages)
    assert len(payload["messages"]) < len(messages)
    assert payload["messages"][-1] == messages[-1]
    assert payload["tool_calls"]
    assert all(
        0 <= tool_call["assistant_msg_idx"] < len(payload["messages"])
        for tool_call in payload["tool_calls"]
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node required")
def test_legacy_full_done_payload_is_bounded_without_mutating_source():
    messages_source = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    function_source = _nested_function_source_from(
        messages_source,
        "_terminalSessionDisplayWindow",
    )
    harness = textwrap.dedent(f"""
        {function_source}
        const messages=[];
        const toolCalls=[];
        for(let index=0;index<20;index++){{
          messages.push({{role:'user',content:'request '+index}});
          const assistantIdx=messages.length;
          messages.push({{role:'assistant',content:'reply '+index}});
          messages.push({{role:'tool',content:'output '+index}});
          toolCalls.push({{tid:'call-'+index,assistant_msg_idx:assistantIdx}});
        }}
        const source={{messages,message_count:messages.length,tool_calls:toolCalls}};
        const bounded=_terminalSessionDisplayWindow(source,6);
        console.log(JSON.stringify({{
          sourceLength:source.messages.length,
          boundedLength:bounded.messages.length,
          messageCount:bounded.message_count,
          offset:bounded._messages_offset,
          truncated:bounded._messages_truncated,
          toolIndexes:bounded.tool_calls.map(tc=>tc.assistant_msg_idx),
        }}));
    """)
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["sourceLength"] == 60
    assert output["boundedLength"] < output["sourceLength"]
    assert output["messageCount"] == output["sourceLength"]
    assert output["offset"] > 0
    assert output["truncated"] is True
    assert all(0 <= index < output["boundedLength"] for index in output["toolIndexes"])


@pytest.mark.skipif(shutil.which("node") is None, reason="node required")
def test_legacy_line_events_buffer_preview_without_copying_growing_string_each_time():
    messages_source = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    append_source = _nested_function_source_from(
        messages_source,
        "_appendBoundedLiveOutputPreview",
    )
    materialize_source = _nested_function_source_from(
        messages_source,
        "_materializeLiveToolOutputPreview",
    )
    harness = textwrap.dedent(f"""
        {append_source}
        {materialize_source}
        const tc={{result_metadata:{{}}}};
        for(let index=0;index<10000;index++){{
          _appendBoundedLiveOutputPreview(tc,'stdout',String(index).padStart(5,'0')+'\\n',1000);
        }}
        const before=tc.result_metadata.stdout;
        const display=_materializeLiveToolOutputPreview(tc,'stdout');
        console.log(JSON.stringify({{
          before,
          length:display.length,
          matchesMetadata:display===tc.result_metadata.stdout,
          endsWithLastLine:display.endsWith('09999\\n'),
          partCount:tc._liveOutputPreviewState.stdout.parts.length,
        }}));
    """)
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output.get("before") is None
    assert output["length"] == 1000
    assert output["matchesMetadata"] is True
    assert output["endsWithLastLine"] is True
    assert output["partCount"] == 1


def test_running_thread_journal_replay_does_not_snapshot_and_persist_every_tool_event():
    messages_source = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    sessions_source = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")

    tool_start = messages_source.index("source.addEventListener('tool',e=>{")
    tool_output = messages_source.index("source.addEventListener('tool_output',e=>{", tool_start)
    tool_block = messages_source[tool_start:tool_output]
    complete_start = messages_source.index("source.addEventListener('tool_complete',e=>{", tool_output)
    todo_start = messages_source.index("source.addEventListener('todo_state',e=>{", complete_start)
    complete_block = messages_source[complete_start:todo_start]
    upsert_source = _nested_function_source_from(messages_source, "upsertLiveToolCall")
    close_start = messages_source.index("function closeLiveStream(")
    close_others_start = messages_source.index("function closeOtherLiveStreams(", close_start)
    close_others_end = messages_source.index(
        "const _TERMINAL_SESSION_VISIBLE_MESSAGE_LIMIT",
        close_others_start,
    )
    close_source = messages_source[close_start:close_others_start]
    close_others_source = messages_source[close_others_start:close_others_end]

    assert "if(reconnecting)_throttledSnapshotLiveTurn();elsesnapshotLiveTurn();" in re.sub(
        r"\s+", "", tool_block
    )
    assert "if(reconnecting)_throttledSnapshotLiveTurn();elsesnapshotLiveTurn();" in re.sub(
        r"\s+", "", complete_block
    )
    assert "if(deferPersistence)_throttledPersist();elsepersistInflightState();" in re.sub(
        r"\s+", "", upsert_source
    )
    assert "skipDomSnapshot" in close_source
    assert "closeLiveStream(sid,null,null,options)" in re.sub(r"\s+", "", close_others_source)
    assert "closeOtherLiveStreams(sid,{skipDomSnapshot:true})" in re.sub(
        r"\s+", "", sessions_source
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node required")
def test_restored_anchor_scene_is_not_rebuilt_for_each_persisted_tool_call():
    """A recovered scene already owns every visible tool row.

    Replaying the persisted tool list after that scene is mounted routes every
    item back through ``appendLiveToolCard``.  The anchor-owner branch rebuilds
    the complete scene, so a 32-tool recovery snapshot becomes 32 synchronous
    transcript rebuilds and blocks Electron's renderer for several seconds.
    """
    sessions_source = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
    helper_source = _nested_function_source_from(
        sessions_source,
        "_shouldReplayRestoredLiveToolCards",
    )
    harness = textwrap.dedent(f"""
        {helper_source}
        const cases=[
          [true,true,false],
          [true,true,true],
          [true,false,false],
          [false,true,false],
        ];
        console.log(JSON.stringify(cases.map(args=>_shouldReplayRestoredLiveToolCards(...args))));
    """)
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [True, False, False, False]

    compact = re.sub(r"\s+", "", sessions_source)
    assert (
        "if(_shouldReplayRestoredLiveToolCards(restoredLiveTurn,didReconnect,restoredAnchorScene))"
        in compact
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node required")
def test_new_recovery_scene_replaces_stale_anchor_activity_instead_of_appending():
    """Returning to a long-running thread must not grow its live registry forever.

    A newer run-journal scene is an authoritative recovery window.  Appending
    all of its rows to the registry retained from the previous selection makes
    every subsequent live event rebuild both the stale and recovered rows.
    """
    messages_source = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    helper_source = _nested_function_source_from(
        messages_source,
        "_resetAnchorRegistryActivityForRecoveryScene",
    )
    harness = textwrap.dedent(f"""
        {helper_source}
        const dedupeSet=new Set(['old-event']);
        const registry={{
          anchor:{{activity_events:[{{local_id:'old-1'}},{{local_id:'old-2'}}]}},
          event_index:{{dedupe_keys:['old-event'],dedupe_key_set:dedupeSet}},
        }};
        const reset=_resetAnchorRegistryActivityForRecoveryScene(registry);
        console.log(JSON.stringify({{
          reset,
          activityCount:registry.anchor.activity_events.length,
          dedupeKeys:registry.event_index.dedupe_keys.length,
          dedupeSet:registry.event_index.dedupe_key_set.size,
        }}));
    """)
    result = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "reset": True,
        "activityCount": 0,
        "dedupeKeys": 0,
        "dedupeSet": 0,
    }

    hydrate_source = _nested_function_source_from(
        messages_source,
        "_hydrateAnchorRegistryFromActivityScene",
    )
    assert "_resetAnchorRegistryActivityForRecoveryScene(_anchorRegistry)" in hydrate_source
    assert hydrate_source.index("_hydrated_activity_scene_key===sceneKey") < hydrate_source.index(
        "_resetAnchorRegistryActivityForRecoveryScene(_anchorRegistry)"
    )


def _nested_function_source_from(source: str, name: str) -> str:
    marker = f"function {name}"
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"missing function {name}")
