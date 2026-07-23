"""Source-contract coverage for the live Changed This Turn workspace surface."""

from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
INDEX_HTML = (REPO / "static" / "index.html").read_text(encoding="utf-8")
MESSAGES_JS = (REPO / "static" / "messages.js").read_text(encoding="utf-8")
SESSIONS_JS = (REPO / "static" / "sessions.js").read_text(encoding="utf-8")
STYLE_CSS = (REPO / "static" / "style.css").read_text(encoding="utf-8")
UI_JS = (REPO / "static" / "ui.js").read_text(encoding="utf-8")
WORKSPACE_JS = (REPO / "static" / "workspace.js").read_text(encoding="utf-8")


def _function_block(src: str, name: str) -> str:
    marker = f"function {name}("
    start = src.find(marker)
    assert start != -1, f"{name}() not found"
    params_end = src.find("){", start)
    assert params_end != -1, f"{name}() body not found"
    brace = params_end + 1
    depth = 0
    for idx in range(brace, len(src)):
        ch = src[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start : idx + 1]
    raise AssertionError(f"{name}() body did not close")


def test_changed_this_turn_panel_is_present_above_workspace_tabs():
    panel_idx = INDEX_HTML.find('id="changedTurnPanel"')
    tabs_idx = INDEX_HTML.find('class="workspace-panel-tabs"')
    assert panel_idx != -1
    assert tabs_idx != -1
    assert panel_idx < tabs_idx, "Changed This Turn should sit above Workspace tabs"
    assert 'id="changedTurnCount"' in INDEX_HTML
    assert 'id="changedTurnList"' in INDEX_HTML
    assert 'Changed This Turn' in INDEX_HTML


def test_changed_this_turn_state_tracks_mutation_paths_and_renders_rows():
    assert "const _changedThisTurnItems = new Map();" in WORKSPACE_JS
    block = _function_block(WORKSPACE_JS, "noteWorkspaceMutationsFromToolCall")
    compact = block.replace(" ", "")
    assert "_artifactCandidatesFromToolCall(tc)" in block
    assert "_turnMutatedPreviewPaths.add(path);" in block
    assert "_changedThisTurnItems.set(path" in compact
    assert "renderChangedThisTurn();" in block

    render_block = _function_block(WORKSPACE_JS, "renderChangedThisTurn")
    assert "changedTurnPanel" in render_block
    assert "changedTurnList" in render_block
    assert "changedTurnCount" in render_block
    assert "openChangedTurnPath" in render_block
    assert "No file edits detected yet." in render_block


def test_changed_this_turn_resets_on_new_stream_and_session_switch():
    reset_block = _function_block(WORKSPACE_JS, "resetTurnWorkspaceMutations")
    assert "_turnMutatedPreviewPaths.clear();" in reset_block
    assert "_changedThisTurnItems.clear();" in reset_block
    assert "renderChangedThisTurn();" in reset_block

    stream_block = _function_block(MESSAGES_JS, "attachLiveStream")
    assert "resetTurnWorkspaceMutations" in stream_block

    assert "currentSid!==sid&&typeof resetTurnWorkspaceMutations==='function'" in SESSIONS_JS


def test_changed_this_turn_visibility_follows_busy_state():
    busy_block = _function_block(UI_JS, "setBusy")
    assert "renderChangedThisTurn" in busy_block

    active_block = _function_block(WORKSPACE_JS, "_isChangedTurnActive")
    assert "S.busy" in active_block
    assert "S.activeStreamId" in active_block


def test_changed_this_turn_open_path_reuses_artifact_opening():
    open_block = _function_block(WORKSPACE_JS, "openChangedTurnPath")
    compact = open_block.replace(" ", "")
    assert "openArtifactPath(path);" in compact


def test_changed_this_turn_git_diff_action_uses_explicit_workspace_baseline():
    render_block = _function_block(WORKSPACE_JS, "renderChangedThisTurn")
    assert "openWorkspaceGitDiff" in render_block
    assert "View current git diff vs HEAD" in render_block
    diff_block = _function_block(WORKSPACE_JS, "openWorkspaceGitDiff")
    assert "/api/workspace/git-diff?" in diff_block
    assert "new URLSearchParams({workspace,path})" in diff_block
    modal_block = _function_block(WORKSPACE_JS, "_renderWorkspaceGitDiff")
    assert "Current workspace git diff vs HEAD" in modal_block
    assert "not a per-turn checkpoint diff" in modal_block


def test_changed_this_turn_styles_are_defined():
    for selector in [
        ".changed-turn-panel",
        ".changed-turn-header",
        ".changed-turn-list",
        ".changed-turn-row",
        ".changed-turn-file",
        ".changed-turn-diff",
        ".changed-turn-kind",
        ".workspace-diff-modal",
        ".workspace-diff-baseline",
        ".workspace-diff-body",
    ]:
        assert selector in STYLE_CSS


def test_settled_assistant_modified_files_extracts_from_message_metadata():
    assert "function _coerceArtifactToolCall" in WORKSPACE_JS
    block = _function_block(WORKSPACE_JS, "assistantMessageModifiedFiles")
    assert "Array.isArray(msg.tool_calls)" in block
    assert "block.type !== 'tool_use'" in block
    assert "assistant_msg_idx" in block
    assert "_artifactCandidatesFromToolCall(fakeTc)" in block
    assert "_workspaceToolCallDiffText(fakeTc,path)" in block
    assert "mutation_preview" in block
    assert "item.diff=diff" in block
    assert "item.raw_output=rawOutput" in block
    assert "return items.slice(0, 24);" in block


def test_settled_assistant_modified_files_render_code_review_cards():
    summary_block = _function_block(UI_JS, "_assistantModifiedFilesSummaryHtml")
    assert 'class="assistant-modified-file-card"' in summary_block
    assert "assistantModifiedDiffHtml(diff,{maxLines:120,allowExpand:true,simulateStream:!!opts.simulateDiffStream})" in summary_block
    assert "loadAssistantModifiedDiff" in summary_block
    assert "Full git diff" not in summary_block
    assert "openWorkspaceGitDiff" not in summary_block
    assert "_assistantModifiedFileStatsHtml" in summary_block
    assert "Tool output diff" not in summary_block

    diff_html_block = _function_block(WORKSPACE_JS, "assistantModifiedDiffHtml")
    diff_rows_html_block = _function_block(WORKSPACE_JS, "_assistantModifiedDiffRowsHtml")
    line_rows_block = _function_block(WORKSPACE_JS, "_assistantDiffLineNumberRows")
    assert "assistant-code-diff-added" in line_rows_block
    assert "assistant-code-diff-removed" in line_rows_block
    assert "assistant-code-diff-gutter assistant-code-diff-old" in diff_rows_html_block
    assert "assistant-code-diff-gutter assistant-code-diff-new" in diff_rows_html_block
    assert "assistant-code-diff-marker" in diff_rows_html_block
    assert "Diff truncated" in diff_html_block
    assert "view_more" in diff_html_block
    assert "view_less" in diff_html_block
    assert "_assistantDiffHunkParts" in line_rows_block
    assert "gap>gapThreshold" in line_rows_block
    assert "oldLine+=1" in line_rows_block
    assert "newLine+=1" in line_rows_block

    raw_block = _function_block(UI_JS, "_assistantModifiedFilesRawOutputHtml")
    assert "View raw output" in raw_block
    assert "assistant-modified-raw-output" in raw_block

    load_block = _function_block(WORKSPACE_JS, "loadAssistantModifiedDiff")
    assert "/api/workspace/git-diff?" in load_block
    assert "No workspace selected" in load_block
    set_panel_block = _function_block(WORKSPACE_JS, "_setAssistantModifiedDiffPanel")
    assert "_assistantModifiedFileStatsHtml({stats},label)" in set_panel_block
    assert "Captured from the file mutation tool output" not in set_panel_block


def test_settled_assistant_modified_files_render_once_on_turn_final_segment():
    turn_block = _function_block(UI_JS, "_assistantTurnModifiedFilesByFinalRawIdx")
    assert "assistantMessageModifiedFiles(entry.m, entry.rawIdx)" in turn_block
    assert "_assistantMergeFinalModifiedFileItems" in turn_block
    assert "byFinal.set(run[run.length-1].rawIdx" in turn_block

    summary_block = _function_block(UI_JS, "_assistantModifiedFilesSummaryHtml")
    assert 'class="assistant-modified-files${recapClass}"' in summary_block
    assert "Modified Files (${count})" in summary_block
    assert "Changed files in this prompt (${count})" in summary_block
    assert "assistant-modified-files-final-recap" in summary_block
    assert "openChangedTurnPath" in summary_block

    assert "const assistantModifiedFilesByFinalRawIdx=_assistantTurnModifiedFilesByFinalRawIdx(renderVisWithIdx);" in UI_JS
    assert "_assistantModifiedFilesSummaryHtml(assistantModifiedFilesByFinalRawIdx.get(rawIdx)||[],{finalRecap:true})" in UI_JS
    assert "(!m._live&&isTurnFinalAssistant&&!_assistantMessageHasAnchorMutationFiles(m))" in UI_JS
    assert "!modifiedFilesHtml&&_assistantMessageBelongsInWorklog" in UI_JS
    assert "${bodyPart}${modifiedFilesHtml}${footHtml}" in UI_JS
    assert "assistant-modified-open" in summary_block
    assert "openWorkspaceGitDiff" not in summary_block
    assert "ontoggle" in summary_block


def test_live_mutation_tools_render_as_assistant_diff_cards_instead_of_raw_tool_rows():
    upsert_block = _function_block(MESSAGES_JS, "upsertLiveToolCall")
    assert "d.mutation_preview" in upsert_block

    live_items_block = _function_block(UI_JS, "_assistantLiveMutationItemsFromToolCall")
    assert "assistantMessageModifiedFiles(msg)" in live_items_block
    assert "_assistantMutationItemsWithEventKey" in live_items_block
    assert "mutationEventKey" in live_items_block

    append_block = _function_block(UI_JS, "_appendLiveModifiedFilesCard")
    assert "_assistantModifiedFilesSummaryHtml(merged,{open:true,openFiles:true,simulateDiffStream:true})" in append_block
    assert "data-mutation-event-key" in append_block
    assert "_mergeLiveAssistantModifiedItems" in append_block
    assert "assistant-timeline-mutation-event" in append_block

    worklog_step_block = _function_block(UI_JS, "_appendWorklogStep")
    assert "tcMutationItems=_assistantLiveMutationItemsFromToolCall(tc)" in worklog_step_block
    assert "regularToolCards.push(tc)" in worklog_step_block
    assert "collapsed:!!(opts&&opts.live===false)" in worklog_step_block
    assert "simulateDiffStream:!!(opts&&opts.live)" in worklog_step_block
    assert "for(const tc of regularToolCards) tools.appendChild(buildToolCard(tc))" in worklog_step_block

    live_tool_block = _function_block(UI_JS, "appendLiveToolCard")
    assert "_appendLiveModifiedFilesCard(tc,{inner,anchor" in live_tool_block
    assert "return;" in live_tool_block[live_tool_block.find("_appendLiveModifiedFilesCard") : live_tool_block.find("if(isTransparentStream())")]

    clear_block = _function_block(UI_JS, "clearLiveToolCards")
    assert "_resetLiveAssistantModifiedFiles" in clear_block
    assert "assistant-live-modified-files-row" in clear_block

    workspace_diff_block = _function_block(WORKSPACE_JS, "_workspaceToolCallDiffText")
    assert "preview.files" in workspace_diff_block
    assert "match.diff" in workspace_diff_block


def test_anchor_activity_scene_mutation_tools_render_live_diff_cards_not_raw_tool_rows():
    tool_from_row = _function_block(UI_JS, "_anchorSceneToolCallFromRow")
    assert "payload.mutation_preview" in tool_from_row
    assert "payload.mutationPreview" in tool_from_row
    assert "result:tool.result||payload.result" in tool_from_row

    mutation_items = _function_block(UI_JS, "_assistantAnchorSceneMutationItemsFromRow")
    assert "_assistantMutationItemsWithEventKey" in mutation_items
    assert "_anchorSceneToolRowSemanticMutationKey(row)" in mutation_items

    highlight_block = _function_block(UI_JS, "highlightCode")
    copy_block = _function_block(UI_JS, "addCopyButtons")
    assert "!block.closest('.assistant-code-diff')" in highlight_block
    assert "pre.classList.contains('assistant-code-diff')" in copy_block

    render_block = _function_block(UI_JS, "_renderAnchorSceneRowsIntoWorklog")
    assert "rowMutationItems=_assistantAnchorSceneMutationItemsFromRow(row,opts)" in render_block
    assert "? _mergeLiveAssistantModifiedItems(rowMutationItems)" in render_block
    assert ": rowMutationItems" in render_block
    assert "const mutationNode=_assistantModifiedFilesNode" in render_block
    assert "collapsed:!(opts&&opts.live)" in render_block
    assert "simulateDiffStream:!!(opts&&opts.live&&opts.animateMutationDiffs)" in render_block
    assert "assistant-anchor-modified-files-row assistant-timeline-mutation-event" in render_block
    assert "mutationNode.setAttribute('data-anchor-scene-row','1')" in render_block
    # Mutation rows are consumed by the live Modified Files card. The regular
    # renderer is only used when no DiffCard node was produced; a failed DiffCard
    # instead follows the dedicated fail-soft fallback path.
    mutation_idx = render_block.find("rowMutationItems=_assistantAnchorSceneMutationItemsFromRow")
    node_idx = render_block.find("if(!node) node=_anchorSceneNodeForRow")
    assert mutation_idx != -1 and node_idx != -1 and mutation_idx < node_idx
    assert "_anchorSceneFallbackNodeForRenderError(row,opts,error)" in render_block
    assert "node=mutationNode;" in render_block[mutation_idx:node_idx]
    assert "if(isMutationNode)" in render_block[node_idx:]

    transparent_block = _function_block(UI_JS, "_anchorSceneTransparentNodeForRow")
    assert "mutationItems.length?'Modified file'" in transparent_block
    assert "_assistantModifiedFilesNode(" in transparent_block
    assert "collapsed:settled" in transparent_block
    assert "simulateDiffStream:!!(!settled&&opts&&opts.animateMutationDiffs)" in transparent_block

    summary_block = _function_block(UI_JS, "_assistantModifiedFilesSummaryHtml")
    assert "Writing file…" in summary_block
    stats_block = _function_block(UI_JS, "_assistantModifiedFileStatsHtml")
    assert "is-pending" in stats_block
    assert "pendingFile&&pendingFile.status" in WORKSPACE_JS
    merge_block = _function_block(UI_JS, "_mergeLiveAssistantModifiedItems")
    assert "_LIVE_MUTATION_PENDING_MIN_MS" in UI_JS
    assert "shouldInitialDeferDiff" in merge_block
    assert "shouldDeferDiff" in merge_block
    assert "_scheduleLiveAssistantModifiedFilesDeferredDiff" in merge_block
    assert "'done': False" in (REPO / "api" / "streaming.py").read_text(encoding="utf-8")

    style = STYLE_CSS
    assert ".assistant-modified-file-stats.is-pending" in style


def test_mutation_timeline_is_keyed_by_tool_event_not_file_path():
    assert "const _liveAssistantMutationEventsByKey=new Map();" in UI_JS
    assert "_liveAssistantModifiedFilesByPath" not in UI_JS

    key_block = _function_block(UI_JS, "_assistantMutationEventKeyFromToolCall")
    assert "tc.tid||tc.id||tc.tool_call_id||tc.tool_use_id||tc.call_id" in key_block

    with_key_block = _function_block(UI_JS, "_assistantMutationItemsWithEventKey")
    assert "mutationEventKey" in with_key_block

    merge_block = _function_block(UI_JS, "_mergeLiveAssistantModifiedItems")
    assert "const eventKey=_assistantMutationEventKeyFromItems(items)" in merge_block
    assert "_liveAssistantMutationEventsByKey.get(eventKey)" in merge_block
    assert "byPath" in merge_block  # merge only within one tool event for pending->done updates
    assert "_liveAssistantMutationEventsByKey.set(eventKey" in merge_block

    render_block = _function_block(UI_JS, "_renderAnchorSceneRowsIntoWorklog")
    assert "let mutationItems=[]" not in render_block
    assert "mutationNode.innerHTML" not in render_block
    assert render_block.count("_assistantModifiedFilesNode(") == 1

    row_render_block = _function_block(UI_JS, "_anchorSceneRowsForRendering")
    semantic_key_block = _function_block(UI_JS, "_anchorSceneToolRowSemanticMutationKey")
    assert "tool-mutation:${semanticMutationKey}" in row_render_block
    assert "write_file|patch|edit_file|create_file" in semantic_key_block
    assert "args.old_string" in semantic_key_block
    assert "args.new_string" in semantic_key_block
    assert "args.content" in semantic_key_block

    messages_tool_row = _function_block(MESSAGES_JS, "_anchorSceneToolRowFromCall")
    messages_settle_block = _function_block(MESSAGES_JS, "_completeSettledAnchorSceneForTurn")
    assert "mutation_timeline_event" in messages_tool_row
    assert "mutation_preview:mutationPreview" in messages_tool_row
    assert "ui_parts:rows" in messages_settle_block

    routes_src = (REPO / "api" / "routes.py").read_text(encoding="utf-8")
    assert '"mutation_timeline_event"' in routes_src
    assert '"ui_parts"' in routes_src
    assert "_anchor_scene_tool_row_semantic_mutation_key" in routes_src

    worklog_step_block = _function_block(UI_JS, "_appendWorklogStep")
    assert "let mutationItems=[]" not in worklog_step_block
    assert "list.appendChild(_assistantModifiedFilesNode(" in worklog_step_block
    assert "collapsed:!!(opts&&opts.live===false)" in worklog_step_block
    assert "simulateDiffStream:!!(opts&&opts.live)" in worklog_step_block


def test_settled_anchor_scene_owns_mutation_summary_to_avoid_duplicate_cards():
    helper_block = _function_block(UI_JS, "_assistantMessageHasAnchorMutationFiles")
    assert "message._anchor_activity_scene" in helper_block
    assert "_anchorSceneRowsForRendering" in helper_block
    assert "_assistantAnchorSceneMutationItemsFromRow(row,{settled:true}).length>0" in helper_block

    render_guard = "!_assistantMessageHasAnchorMutationFiles(m)"
    summary_call = "_assistantModifiedFilesSummaryHtml(assistantModifiedFilesByFinalRawIdx.get(rawIdx)||[]"
    assert render_guard in UI_JS
    assert UI_JS.find(render_guard) < UI_JS.find(summary_call)


def test_settled_assistant_modified_files_are_not_hidden_by_worklog_reason_mirroring():
    sync_block = _function_block(UI_JS, "_syncWorklogReasonFromAnchor")
    append_block = _function_block(UI_JS, "_appendWorklogReason")
    guard = "anchor.querySelector('.assistant-modified-files')"
    assert guard in sync_block
    assert guard in append_block
    assert "assistant-segment-worklog-source" in sync_block
    assert "assistant-segment-worklog-source" in append_block


def test_modified_file_open_action_reveals_workspace_panel_and_reports_diagnostics():
    open_block = _function_block(WORKSPACE_JS, "openArtifactPath")
    assert "ensureWorkspacePreviewVisible" in open_block
    assert "openWorkspacePanel('preview')" in open_block
    assert "_showWorkspaceOpenFileDiagnostic" in open_block
    assert "file not found" in open_block

    normalize_block = _function_block(WORKSPACE_JS, "_workspaceNormalizeOpenArtifactPath")
    assert "outside workspace" in normalize_block
    assert "workspace not selected" in normalize_block

    diagnostic_block = _function_block(WORKSPACE_JS, "_workspaceOpenFileDiagnostic")
    assert "Could not open this file." in diagnostic_block
    assert "active workspace root" in diagnostic_block
    assert "attempted path" in diagnostic_block


def test_settled_assistant_modified_files_styles_are_defined():
    for selector in [
        ".assistant-modified-files",
        ".assistant-modified-files-final-recap",
        ".assistant-modified-list",
        ".assistant-modified-file-row",
        ".assistant-modified-file",
        ".assistant-modified-file-card",
        ".assistant-modified-file-summary",
        ".assistant-modified-file-stats",
        ".assistant-modified-file-stat-added",
        ".assistant-modified-file-stat-removed",
        ".assistant-modified-diff-panel",
        ".assistant-code-diff-added",
        ".assistant-code-diff-removed",
        ".assistant-code-diff-gutter",
        ".assistant-code-diff-old",
        ".assistant-code-diff-new",
        ".assistant-code-diff-marker",
        ".assistant-code-diff-code",
        ".assistant-modified-diff",
        ".assistant-modified-raw-output",
        ".assistant-modified-file-path",
        ".assistant-modified-file-kind",
        ".assistant-modified-diff-toggle",
    ]:
        assert selector in STYLE_CSS
    assert "minmax(var(--diff-old-gutter-width,calc(3ch + 15px)),max-content)" in STYLE_CSS
    assert "minmax(var(--diff-new-gutter-width,calc(3ch + 15px)),max-content)" in STYLE_CSS
    assert "min-width:var(--diff-old-gutter-width,calc(3ch + 15px))" in STYLE_CSS
    assert "min-width:var(--diff-new-gutter-width,calc(3ch + 15px))" in STYLE_CSS
