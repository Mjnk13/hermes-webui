from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def _function_block(src: str, name: str) -> str:
    start = src.find(f"def {name}")
    assert start != -1, f"{name} not found"
    next_def = src.find("\n            def ", start + 1)
    assert next_def != -1, f"end of {name} not found"
    return src[start:next_def]


def test_tool_start_callback_emits_existing_tool_sse_event_with_tool_id():
    src = _read("api/streaming.py")
    block = _function_block(src, "on_tool_start")

    assert "put('tool'" in block, (
        "The dedicated Hermes Agent tool_start_callback must emit the existing "
        "tool SSE event; otherwise WebUI stays visually silent while tools run."
    )
    assert "'event_type': 'tool.started'" in block
    assert "'tid': tool_call_id" in block, (
        "Live frontend cards need the tool_call_id so tool_complete can update "
        "the running card in place."
    )
    assert "_live_tool_event_start_ids" in block, (
        "Tool start SSE emission should be idempotent per callback id."
    )
    assert "STREAM_LIVE_TOOL_CALLS" in block and "'done': False" in block


def test_tool_complete_callback_emits_existing_tool_complete_sse_event_with_tool_id():
    src = _read("api/streaming.py")
    block = _function_block(src, "on_tool_complete")

    assert "put('tool_complete'" in block, (
        "The dedicated Hermes Agent tool_complete_callback must emit the existing "
        "tool_complete SSE event so the frontend can settle the running tool card."
    )
    assert "'event_type': 'tool.completed'" in block
    assert "'tid': tool_call_id" in block
    assert "_live_tool_event_complete_ids" in block, (
        "Tool completion SSE emission should be idempotent per callback id."
    )
    assert "result_snippet = _tool_result_snippet(function_result)" in block
    assert "structured_result = _tool_result_structured_payload(function_result)" in block
    assert "payload['result'] = structured_result" in block, (
        "bounded JSON wrappers must survive the live completion event so the "
        "formatted body and exact raw drawer use separate sources"
    )
    assert "_checkpoint_activity[0] += 1" in block
    assert "_live_tool_output_coalescer.flush(tool_call_id)" in block, (
        "buffered terminal output must be delivered before tool completion"
    )


def test_legacy_progress_events_are_suppressed_when_structured_callbacks_are_wired():
    src = _read("api/streaming.py")
    block = _function_block(src, "on_tool")

    assert "event_type in (None, 'tool.started') and 'tool_start_callback' in _agent_params and not mutation_preview" in block
    assert "event_type == 'tool.completed' and 'tool_complete_callback' in _agent_params" in block
    assert block.index("'tool_start_callback' in _agent_params") < block.index("put('tool'")
    assert block.index("'tool_complete_callback' in _agent_params") < block.index("put('tool_complete'")


def test_mutation_progress_starts_are_not_suppressed_and_write_file_starts_pending():
    src = _read("api/streaming.py")
    on_tool = _function_block(src, "on_tool")
    preview = _function_block(src, "_live_mutation_preview_from_tool")

    assert "and not mutation_preview" in on_tool, (
        "File mutation progress starts must still emit a tool SSE event even "
        "when structured callbacks are wired, otherwise real patch/write_file "
        "runs stay visually silent until completion."
    )
    assert "'pending': True" in preview
    assert "'status': 'Writing file…'" in preview
    assert "result is None and ('write' in tool_name or 'create' in tool_name)" in preview

def test_tool_callback_events_keep_existing_frontend_event_contract():
    messages = _read("static/messages.js")
    ui = _read("static/ui.js")

    assert "source.addEventListener('tool',e=>{" in messages
    assert "source.addEventListener('tool_complete',e=>{" in messages
    assert "String(d&&d.tid" in messages or "explicitTid=String(d&&d.tid" in messages, (
        "frontend tool handlers must still consume explicit server tid when present"
    )
    assert "upsertLiveToolCall(d,'start',reconnecting)" in messages
    assert "upsertLiveToolCall(d,'complete',reconnecting)" in messages
    assert "data-live-tid" in ui
    assert "existing.replaceWith(replacement)" in ui


def test_live_tool_output_callback_is_capability_gated_for_old_agents():
    src = _read("api/streaming.py")
    block = _function_block(src, "on_tool_output")

    assert "_live_tool_output_coalescer.append(" in block
    assert "put('tool_output'" not in block, (
        "the callback must not flood Electron with one SSE frame per output line"
    )
    assert "if 'tool_output_callback' in _agent_params:" in src
    assert "_agent_kwargs['tool_output_callback'] = on_tool_output" in src
    assert "if hasattr(agent, 'tool_output_callback'):" in src


def test_frontend_appends_live_output_without_rebuilding_the_command_card():
    messages = _read("static/messages.js")
    ui = _read("static/ui.js")

    assert "source.addEventListener('tool_output',e=>{" in messages
    assert "appendLiveToolOutputState(d)" in messages
    assert "appendLiveToolOutputChunk(update.tc,update.stream,update.text" in messages
    assert "function appendLiveToolOutputChunk(tc,stream,chunk)" in ui
    append_block = ui[ui.index("function appendLiveToolOutputChunk(tc,stream,chunk)"):ui.index("// ── Live tool card helpers")]
    assert "data-tool-output-stream" in append_block
    assert "_scheduleLiveToolOutputRowFlush(row)" in append_block
    assert "_appendBoundedLiveToolRawPreview(row,value)" in append_block
    assert "_toolOutputFormattedHtml" not in append_block, (
        "running output must stay on the incremental text-node path instead "
        "of semantically reformatting the growing visible tail"
    )
    assert "buildToolCard(tc)" not in append_block
    assert "existing.replaceWith" not in append_block
    assert "flushLiveToolOutputChunks(d,{sessionId:activeSid,streamId,final:true})" in messages


def test_completion_event_remains_the_authoritative_legacy_fallback():
    messages = _read("static/messages.js")
    streaming = _read("api/streaming.py")

    assert "source.addEventListener('tool_complete',e=>{" in messages
    assert "upsertLiveToolCall(d,'complete',reconnecting)" in messages
    assert "put('tool_complete', payload)" in streaming
    assert "if 'tool_complete_callback' in _agent_params:" in streaming
    assert "if(isComplete&&tc._liveOutputMetadataOwned" in messages
    assert "delete tc.result_metadata.stdout" in messages
    assert "delete tc.result_metadata.stderr" in messages
