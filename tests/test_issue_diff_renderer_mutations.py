"""Regression tests for chat-thread mutation DiffCard rendering.

Covers issues.md:
- assistant modified-file code rows must use real unified-diff hunk line numbers
  while the hunk header itself keeps its gutters blank;
- raw patch/output from an "Updated a file" tool row must normalize through the
  shared Modified Files / DiffCard renderer instead of remaining a raw tool card;
- final/hydrated assistant-message metadata must use the same extraction path.
"""
import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
WORKSPACE_JS = REPO_ROOT / "static" / "workspace.js"
UI_JS = REPO_ROOT / "static" / "ui.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


_DRIVER = r"""
const fs = require('fs');
const workspaceSrc = fs.readFileSync(process.argv[2], 'utf8');
const uiSrc = fs.readFileSync(process.argv[3], 'utf8');
const mode = process.argv[4];
const payload = JSON.parse(process.argv[5]);

function extractFunc(src, name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\(');
  const start = src.search(re);
  if (start < 0) throw new Error(name + ' not found');
  let i = src.indexOf('{', start);
  let depth = 1; i++;
  while (depth > 0 && i < src.length) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') depth--;
    i++;
  }
  return src.slice(start, i);
}

// Workspace helpers under test. Slice from _escHtml through the assistant-message
// mutation extractor so constants such as ARTIFACT_MUTATION_TOOLS stay in scope.
var S = {session:{workspace:'/repo'}, toolCalls:[]};
const wsStart = workspaceSrc.indexOf('function _escHtml');
const wsEnd = workspaceSrc.indexOf('function _isOpenPreviewPathMutated');
if (wsStart < 0 || wsEnd < 0) throw new Error('workspace helper slice markers missing');
eval(workspaceSrc.slice(wsStart, wsEnd));

// UI summary renderer helpers under test.
global.esc = (s)=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
global._workspaceDisplayPath = _workspaceDisplayPath;
global._changedTurnKindForSource = _changedTurnKindForSource;
global.assistantModifiedDiffStatsText = assistantModifiedDiffStatsText;
global.assistantModifiedDiffHtml = assistantModifiedDiffHtml;
for (const fn of [
  '_assistantModifiedItemStatsObject',
  '_assistantModifiedItemsSameDiff',
  '_assistantAppendFinalModifiedFileItem',
  '_assistantMergeFinalModifiedFileItems',
  '_assistantTurnModifiedFilesByFinalRawIdx',
  '_assistantModifiedFilesRawOutputHtml',
  '_assistantModifiedPatchFailureHtml',
  '_assistantModifiedFileIconHtml',
  '_assistantModifiedFileStatsHtml',
  '_assistantModifiedFilesSummaryHtml'
]) {
  eval(extractFunc(uiSrc, fn));
}

function rowsFor(diff) {
  return _assistantDiffLineNumberRows(diff, 200).rows.map(r => ({
    cls:r.cls, oldNo:r.oldNo, newNo:r.newNo, marker:r.marker, code:r.code
  }));
}
function diffHtmlFor(diff, maxLines, allowExpand) {
  return assistantModifiedDiffHtml(diff, {
    maxLines,
    allowExpand,
    simulateStream: !!payload.simulateStream,
  });
}
function summaryForItems(items) {
  const html = _assistantModifiedFilesSummaryHtml(items || [], {open:true, openFiles:true});
  return {items, html};
}
function finalRecapForItems(items) {
  const merged = _assistantMergeFinalModifiedFileItems(items);
  const html = _assistantModifiedFilesSummaryHtml(merged, {finalRecap:true});
  return {items:merged, html};
}
function summaryForToolCall(tc) {
  const items = assistantMessageModifiedFiles({tool_calls:[tc]});
  const html = _assistantModifiedFilesSummaryHtml(items, {open:true, openFiles:true});
  return {items, html};
}

let out;
if (mode === 'lineRows') out = rowsFor(payload.diff);
else if (mode === 'diffHtml') out = diffHtmlFor(payload.diff, payload.maxLines || 120, !!payload.allowExpand);
else if (mode === 'diffForPath') out = _workspaceDiffForPath(payload.diff, payload.path);
else if (mode === 'summaryForItems') out = summaryForItems(payload.items || []);
else if (mode === 'finalRecapForItems') out = finalRecapForItems(payload.items || []);
else if (mode === 'summaryForTool') out = summaryForToolCall(payload.toolCall);
else if (mode === 'artifactCandidates') out = _artifactCandidatesFromToolCall(payload.toolCall);
else if (mode === 'summaryForFinalMessage') {
  const items = assistantMessageModifiedFiles(payload.message, payload.rawIdx || 0);
  out = {items, html:_assistantModifiedFilesSummaryHtml(items, {open:true, openFiles:true})};
}
else if (mode === 'mutationEventKeys') {
  eval(extractFunc(uiSrc, '_assistantMutationSemanticKeyFromToolCall'));
  eval(extractFunc(uiSrc, '_assistantMutationEventKeyFromToolCall'));
  global._toolIdentity = (tc)=>tc&&tc.tid?`id:${tc.tid}`:'derived';
  out = (payload.toolCalls || []).map(tc=>_assistantMutationEventKeyFromToolCall(tc, 'fallback'));
}
else throw new Error('unknown mode ' + mode);
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory):
    path = tmp_path_factory.mktemp("diff_renderer_driver") / "driver.js"
    path.write_text(_DRIVER, encoding="utf-8")
    return str(path)


def _run(driver, mode, payload):
    assert NODE is not None
    result = subprocess.run(
        [NODE, driver, str(WORKSPACE_JS), str(UI_JS), mode, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)




def _span_texts(rendered_html, class_name):
    pattern = rf'<span class="{re.escape(class_name)}"[^>]*>(.*?)</span>'
    return [html.unescape(match) for match in re.findall(pattern, rendered_html)]


def _gutter_triplets(rendered_html):
    olds = _span_texts(rendered_html, "assistant-code-diff-gutter assistant-code-diff-old")
    news = _span_texts(rendered_html, "assistant-code-diff-gutter assistant-code-diff-new")
    markers = _span_texts(rendered_html, "assistant-code-diff-marker")
    codes = _span_texts(rendered_html, "assistant-code-diff-code")
    return list(zip(olds, news, markers, codes))

def _changed_rows(rows):
    return [r for r in rows if "assistant-code-diff-added" in r["cls"] or "assistant-code-diff-removed" in r["cls"] or r["marker"] == " "]


def test_unified_diff_single_hunk_line_numbers_use_hunk_header(driver):
    rows = _run(driver, "lineRows", {
        "diff": "--- a/app.py\n+++ b/app.py\n@@ -85,3 +85,4 @@\n context a\n-old b\n context c\n+new d\n"
    })
    hunk = next(r for r in rows if "assistant-code-diff-hunk" in r["cls"])
    assert (hunk["oldNo"], hunk["newNo"], hunk["marker"], hunk["code"]) == (
        "", "", "", "@@ -85,3 +85,4 @@"
    )
    changed = _changed_rows(rows)
    assert changed == [
        {"cls": "assistant-code-diff-line", "oldNo": "85", "newNo": "85", "marker": " ", "code": "context a"},
        {"cls": "assistant-code-diff-line assistant-code-diff-removed", "oldNo": "86", "newNo": "", "marker": "-", "code": "old b"},
        {"cls": "assistant-code-diff-line", "oldNo": "87", "newNo": "86", "marker": " ", "code": "context c"},
        {"cls": "assistant-code-diff-line assistant-code-diff-added", "oldNo": "", "newNo": "87", "marker": "+", "code": "new d"},
    ]


def test_hunk_section_context_is_rendered_after_the_coordinate_header(driver):
    rows = _run(driver, "lineRows", {
        "diff": (
            "@@ -125,21 +124,17 @@ export interface Config {\n"
            " export interface Config {\n"
            "-  legacy: boolean;\n"
            "+  enabled: boolean;\n"
        )
    })

    assert rows[0] == {
        "cls": "assistant-code-diff-line assistant-code-diff-hunk",
        "oldNo": "",
        "newNo": "",
        "marker": "",
        "code": "@@ -125,21 +124,17 @@",
    }
    assert rows[1] == {
        "cls": "assistant-code-diff-line assistant-code-diff-hunk-context",
        "oldNo": "",
        "newNo": "",
        "marker": "",
        "code": "export interface Config {",
    }
    assert (rows[2]["oldNo"], rows[2]["newNo"], rows[2]["code"]) == (
        "125",
        "124",
        "export interface Config {",
    )


def test_unified_diff_multi_hunk_resets_line_numbers(driver):
    rows = _run(driver, "lineRows", {
        "diff": "@@ -10,2 +20,2 @@\n-a\n+b\n@@ -50,2 +60,3 @@\n c\n-d\n+e\n+f\n"
    })
    gaps = [r for r in rows if "assistant-code-diff-gap" in r["cls"]]
    assert [(r["oldNo"], r["newNo"], r["marker"], r["code"]) for r in gaps] == [
        ("", "", "", "39 unmodified lines"),
    ]
    changed = _changed_rows(rows)
    assert [(r["oldNo"], r["newNo"], r["marker"], r["code"]) for r in changed] == [
        ("10", "", "-", "a"),
        ("", "20", "+", "b"),
        ("50", "60", " ", "c"),
        ("51", "", "-", "d"),
        ("", "61", "+", "e"),
        ("", "62", "+", "f"),
    ]


def test_diff_gutters_size_old_and_new_columns_independently(driver):
    rendered = _run(driver, "diffHtml", {
        "diff": "@@ -9998,4 +98,4 @@\n a\n-b\n+c\n d\n",
        "maxLines": 20,
        "allowExpand": False,
    })

    assert 'data-diff-old-digits="5"' in rendered
    assert 'data-diff-new-digits="3"' in rendered
    assert '--diff-old-gutter-width:calc(5ch + 15px)' in rendered
    assert '--diff-new-gutter-width:calc(3ch + 15px)' in rendered


def test_nearby_multi_hunk_diff_does_not_insert_fake_gap(driver):
    rows = _run(driver, "lineRows", {
        "diff": "@@ -10,2 +10,2 @@\n a\n-b\n+c\n@@ -14,1 +14,1 @@\n-d\n+e\n"
    })
    assert [r for r in rows if "assistant-code-diff-gap" in r["cls"]] == []


def test_diff_html_view_more_only_when_full_renderer_data_is_available(driver):
    long_diff = "--- a/file.js\n+++ b/file.js\n@@ -1,130 +1,130 @@\n" + "\n".join(f" line {i}" for i in range(1, 131))
    html = _run(driver, "diffHtml", {"diff": long_diff, "maxLines": 20, "allowExpand": True})
    assert "assistant-modified-diff-wrap" in html
    assert "view_more" in html
    assert "view_less" in html
    assert "is-full" in html

    small_html = _run(driver, "diffHtml", {"diff": "@@ -1,1 +1,1 @@\n-old\n+new\n", "maxLines": 20, "allowExpand": True})
    assert "view_more" not in small_html
    assert "view_less" not in small_html


def test_live_diff_html_streams_only_the_truncated_view_until_view_more(driver):
    long_diff = "--- a/file.js\n+++ b/file.js\n@@ -1,130 +1,130 @@\n" + "\n".join(
        f" line {i}" for i in range(1, 131)
    )
    rendered = _run(
        driver,
        "diffHtml",
        {
            "diff": long_diff,
            "maxLines": 20,
            "allowExpand": True,
            "simulateStream": True,
        },
    )

    assert rendered.count('data-diff-stream="1"') == 1
    assert 'class="assistant-code-diff is-truncated" data-diff-stream="1"' in rendered
    assert 'class="assistant-code-diff is-full" data-diff-stream="1"' not in rendered
    assert 'class="assistant-code-diff is-full"' in rendered
    # The live gutter must grow from mounted rows instead of reserving the
    # complete diff's final width before those rows exist.
    live_pre = rendered.split('<pre class="assistant-code-diff is-full"', 1)[0]
    assert 'data-diff-old-digits="3"' in live_pre
    assert 'data-diff-new-digits="3"' in live_pre
    assert "view_more" in rendered


def test_settled_final_recap_diff_renders_immediately_without_stream_marker(driver):
    diff = "--- a/app.py\n+++ b/app.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
    recap = _run(
        driver,
        "finalRecapForItems",
        {
            "items": [
                {
                    "path": "app.py",
                    "source": "patch",
                    "diff": diff,
                    "stats": {"added": 1, "removed": 1},
                }
            ]
        },
    )

    assert 'data-diff-stream="1"' not in recap["html"]


def test_final_recap_html_keeps_hunk_header_out_of_gutters(driver):
    diff = "--- a/app.py\n+++ b/app.py\n@@ -85,3 +85,4 @@\n context a\n-old b\n context c\n+new d\n"
    out = _run(driver, "finalRecapForItems", {
        "items": [{"path": "app.py", "source": "patch", "diff": diff, "stats": {"added": 1, "removed": 1}}]
    })
    triplets = _gutter_triplets(out["html"])
    assert ("", "", " ", "@@ -85,3 +85,4 @@") in triplets
    assert ("85", "85", " ", "context a") in triplets
    assert ("86", "", "-", "old b") in triplets
    assert ("87", "86", " ", "context c") in triplets
    assert ("", "87", "+", "new d") in triplets
    assert not any(
        code.startswith("@@") and (old or new or marker.strip())
        for old, new, marker, code in triplets
    )


def test_live_and_final_recap_cards_produce_matching_gutters(driver):
    diff = "--- a/app.py\n+++ b/app.py\n@@ -85,3 +85,4 @@\n context a\n-old b\n context c\n+new d\n"
    item = {"path": "app.py", "source": "patch", "diff": diff, "stats": {"added": 1, "removed": 1}}
    live = _run(driver, "summaryForItems", {"items": [item]})
    recap = _run(driver, "finalRecapForItems", {"items": [item]})
    live_rows = [row for row in _gutter_triplets(live["html"]) if row[2] in {"@@", " ", "-", "+"}]
    recap_rows = [row for row in _gutter_triplets(recap["html"]) if row[2] in {"@@", " ", "-", "+"}]
    assert recap_rows == live_rows


def test_final_recap_indented_hunk_header_still_initializes_gutters(driver):
    diff = "  --- a/app.py\n  +++ b/app.py\n  @@ -85,3 +85,4 @@\n   context a\n  -old b\n   context c\n  +new d\n"
    out = _run(driver, "finalRecapForItems", {
        "items": [{"path": "app.py", "source": "patch", "diff": diff, "stats": {"added": 1, "removed": 1}}]
    })
    triplets = _gutter_triplets(out["html"])
    assert ("", "", " ", "@@ -85,3 +85,4 @@") in triplets
    assert ("85", "85", " ", "context a") in triplets
    assert ("86", "", "-", "old b") in triplets
    assert ("", "87", "+", "new d") in triplets


def test_final_recap_view_more_full_diff_keeps_code_gutters(driver):
    diff = "--- a/app.py\n+++ b/app.py\n@@ -85,130 +85,130 @@\n" + "\n".join(f" context {i}" for i in range(130))
    out = _run(driver, "finalRecapForItems", {
        "items": [{"path": "app.py", "source": "patch", "diff": diff, "stats": {"added": 0, "removed": 0}}]
    })
    assert "view_more" in out["html"]
    assert "assistant-code-diff is-truncated" in out["html"]
    assert "assistant-code-diff is-full" in out["html"]
    assert 'old line 85">85' in out["html"]
    assert 'new line 85">85' in out["html"]
    assert 'old line 214">214' in out["html"]
    assert 'new line 214">214' in out["html"]


def test_invalid_hunk_header_falls_back_to_blank_gutters(driver):
    rows = _run(driver, "lineRows", {
        "diff": "@@ -5,1 +8,1 @@\n-ok\n+yes\n@@ invalid hunk @@\n-old\n+new\n context without hunk\n"
    })
    changed = _changed_rows(rows)
    assert [(r["oldNo"], r["newNo"], r["marker"], r["code"]) for r in changed] == [
        ("5", "", "-", "ok"),
        ("", "8", "+", "yes"),
        ("", "", "-", "old"),
        ("", "", "+", "new"),
        ("", "", " ", "context without hunk"),
    ]


def test_updated_file_raw_patch_payload_renders_modified_files_diffcard(driver):
    raw_patch = "*** Begin Patch\n*** Update File: src/app.js\n@@ -85,2 +85,3 @@\n-old line\n+new line\n+extra line\n*** End Patch"
    out = _run(driver, "summaryForTool", {
        "toolCall": {
            "name": "patch",
            "tid": "patch-1",
            "args": {"mode": "patch", "patch": raw_patch},
            "snippet": raw_patch,
            "done": True,
        }
    })
    assert [item["path"] for item in out["items"]] == ["src/app.js"]
    assert out["items"][0]["diff"].startswith("--- src/app.js\n+++ src/app.js")
    assert "*** Begin Patch" not in out["items"][0]["diff"]
    assert "*** Update File" not in out["items"][0]["diff"]
    assert "assistant-modified-files" in out["html"]
    assert "assistant-code-diff" in out["html"]
    assert "assistant-modified-file-stat-added" in out["html"]
    assert "assistant-modified-file-stat-removed" in out["html"]
    assert "+2" in out["html"] and "-1" in out["html"]
    assert "Full git diff" not in out["html"]
    assert "openWorkspaceGitDiff" not in out["html"]
    assert "Tool output diff" not in out["html"]
    assert "Captured from the file mutation tool output" not in out["html"]
    assert "tool-card-result" not in out["html"]
    assert "View raw output" in out["html"]


def test_patch_validation_failure_renders_compact_error_card_without_diff_gutters(driver):
    raw_patch = "*** Begin Patch\n*** Update File: src/app.js\n@@ -85,1 +85,1 @@\n-old\n+new\n*** End Patch"
    details = "Patch validation failed (no files were modified):\n  • src/app.js: hunk not found"
    out = _run(driver, "summaryForTool", {
        "toolCall": {
            "name": "patch",
            "tid": "patch-failed",
            "args": {"mode": "patch", "patch": raw_patch},
            "result": {"success": False, "error": details},
            "done": True,
            "is_error": True,
        }
    })

    assert [item["path"] for item in out["items"]] == ["src/app.js"]
    assert out["items"][0]["failed"] is True
    assert out["items"][0].get("diff", "") == ""
    assert details in out["items"][0]["failure_details"]
    assert 'data-patch-failed="1"' in out["html"]
    assert "Patch could not be applied" in out["html"]
    assert "Failure details" in out["html"]
    assert "assistant-modified-patch-failure" in out["html"]
    assert "assistant-code-diff" not in out["html"]
    assert "assistant-code-diff-gutter" not in out["html"]
    assert "assistant-modified-file-stat-added" not in out["html"]
    assert "assistant-modified-file-stat-removed" not in out["html"]
    assert "View raw output" not in out["html"]


def test_successful_patch_result_replaces_prior_failure_for_same_file(driver):
    failure_patch = "*** Begin Patch\n*** Update File: src/app.js\n@@\n-old\n+new\n*** End Patch"
    completed = "--- a/src/app.js\n+++ b/src/app.js\n@@ -85,1 +85,1 @@\n-old\n+new\n"
    out = _run(driver, "summaryForFinalMessage", {
        "message": {
            "role": "assistant",
            "tool_calls": [
                {
                    "name": "patch",
                    "args": {"patch": failure_patch},
                    "result": {"success": False, "error": "Patch validation failed: hunk not found"},
                    "done": True,
                    "is_error": True,
                },
                {
                    "name": "patch",
                    "args": {"path": "src/app.js", "old_string": "old", "new_string": "new"},
                    "result": {"success": True, "diff": completed},
                    "done": True,
                },
            ],
        },
        "rawIdx": 8,
    })

    assert out["items"][0]["failed"] is False
    assert out["items"][0]["diff"] == completed
    assert "assistant-code-diff" in out["html"]
    assert "assistant-modified-patch-failure" not in out["html"]
    assert ("85", "", "-", "old") in _gutter_triplets(out["html"])


def test_args_only_multi_file_v4a_patch_extracts_each_path(driver):
    raw_patch = "*** Begin Patch\n*** Update File: src/a.ts\n@@ -1,1 +1,1 @@\n-old\n+new\n*** Update File: src/b.ts\n@@ -20,1 +20,2 @@\n x\n+y\n*** End Patch"
    out = _run(driver, "artifactCandidates", {
        "toolCall": {
            "name": "patch",
            "args": {"mode": "patch", "patch": raw_patch},
            "done": True,
        }
    })
    assert [item["path"] for item in out] == ["src/a.ts", "src/b.ts"]


def test_final_hydrated_message_uses_same_mutation_renderer(driver):
    raw_patch = "*** Begin Patch\n*** Update File: src/final.js\n@@ -12,1 +12,2 @@\n-old\n+new\n+more\n*** End Patch"
    out = _run(driver, "summaryForFinalMessage", {
        "message": {
            "role": "assistant",
            "tool_calls": [{
                "id": "call-final",
                "function": {
                    "name": "patch",
                    "arguments": json.dumps({"mode": "patch", "patch": raw_patch}),
                },
                "snippet": raw_patch,
            }],
        },
        "rawIdx": 4,
    })
    assert [item["path"] for item in out["items"]] == ["src/final.js"]
    assert "assistant-modified-files" in out["html"]
    assert "assistant-code-diff" in out["html"]
    assert "tool-card-result" not in out["html"]


def test_final_recap_groups_repeated_edits_by_file_and_keeps_all_hunks(driver):
    out = _run(driver, "finalRecapForItems", {
        "items": [
            {"path": "src/a.ts", "source": "patch", "diff": "--- src/a.ts\n+++ src/a.ts\n@@ -1,1 +1,1 @@\n-old a1\n+new a1\n", "stats": {"added": 1, "removed": 1}},
            {"path": "src/b.ts", "source": "patch", "diff": "--- src/b.ts\n+++ src/b.ts\n@@ -5,1 +5,1 @@\n-old b\n+new b\n", "stats": {"added": 1, "removed": 1}},
            {"path": "src/a.ts", "source": "patch", "diff": "--- src/a.ts\n+++ src/a.ts\n@@ -20,1 +20,2 @@\n-old a2\n+new a2\n+more a2\n", "stats": {"added": 2, "removed": 1}},
        ]
    })
    assert [item["path"] for item in out["items"]] == ["src/a.ts", "src/b.ts"]
    assert "old a1" in out["items"][0]["diff"]
    assert "old a2" in out["items"][0]["diff"]
    assert out["items"][0]["stats"] == {"added": 3, "removed": 2}
    assert "Changed files in this prompt (2)" in out["html"]
    assert "assistant-modified-files-final-recap" in out["html"]
    assert out["html"].count('data-modified-file-path="src/a.ts"') == 1
    assert out["html"].count('data-modified-file-path="src/b.ts"') == 1
    assert '<details class="assistant-modified-files assistant-modified-files-final-recap">' in out["html"]
    file_cards = re.findall(
        r'<details class="assistant-modified-file-card"(.*?) ontoggle=',
        out["html"],
    )
    assert len(file_cards) == 2
    assert all(not re.search(r'\sopen(?:\s|$)', attrs) for attrs in file_cards)


def test_completed_tool_result_diff_overrides_inaccurate_live_preview(driver):
    bad_live_diff = (
        "--- src/sample.ts\n+++ src/sample.ts\n@@ -1,2 +1,4 @@\n"
        " context\n-old line+new line\n+extra one\n+extra two"
    )
    completed_diff = (
        "--- a/src/sample.ts\n+++ b/src/sample.ts\n@@ -130,7 +130,9 @@\n"
        " context 130\n context 131\n context 132\n-old line\n+new line\n"
        "+extra one\n+extra two\n context 134\n context 135\n context 136\n"
    )
    out = _run(driver, "summaryForTool", {
        "toolCall": {
            "name": "patch",
            "args": {"path": "src/sample.ts", "old_string": "old line", "new_string": "new line"},
            "mutation_preview": {
                "files": [{"path": "src/sample.ts", "source": "patch", "diff": bad_live_diff}]
            },
            "snippet": json.dumps({"success": True, "diff": completed_diff}),
            "done": True,
        }
    })

    assert out["items"][0]["diff"] == completed_diff
    triplets = _gutter_triplets(out["html"])
    assert ("133", "", "-", "old line") in triplets
    assert ("", "133", "+", "new line") in triplets
    assert not any(old == "1" or new == "1" for old, new, _, _ in triplets)


def test_authoritative_completion_promotes_positionless_preview_for_same_file(driver):
    positionless = "--- src/sample.ts\n+++ src/sample.ts\n@@\n-old\n+new\n"
    completed = "--- a/src/sample.ts\n+++ b/src/sample.ts\n@@ -155,1 +155,1 @@\n-old\n+new\n"
    args = {
        "mode": "replace",
        "path": "src/sample.ts",
        "old_string": "old",
        "new_string": "new",
    }
    out = _run(driver, "summaryForFinalMessage", {
        "message": {
            "role": "assistant",
            "tool_calls": [
                {
                    "name": "patch",
                    "args": args,
                    "mutation_preview": {"files": [{"path": "src/sample.ts", "diff": positionless}]},
                    "done": False,
                },
                {
                    "name": "patch",
                    "tid": "call-positioned",
                    "args": args,
                    "snippet": json.dumps({"success": True, "diff": completed}),
                    "mutation_preview": {"files": [{"path": "src/sample.ts", "diff": completed}]},
                    "done": True,
                },
            ],
        },
        "rawIdx": 4,
    })

    assert out["items"][0]["diff"] == completed
    assert ("155", "", "-", "old") in _gutter_triplets(out["html"])
    assert ("", "155", "+", "new") in _gutter_triplets(out["html"])


def test_live_mutation_start_and_completion_share_semantic_event_key(driver):
    args = {
        "mode": "replace",
        "path": "src/sample.ts",
        "old_string": "old",
        "new_string": "new",
    }
    keys = _run(driver, "mutationEventKeys", {
        "toolCalls": [
            {"name": "patch", "tid": "live-generated", "args": args},
            {"name": "patch", "tid": "call-provider", "args": args},
        ]
    })

    assert keys[0] == keys[1]
    assert keys[0].startswith("mutation:")


def test_multi_file_completed_diff_selects_only_requested_file_hunk(driver):
    combined = (
        "diff --git a/src/first.ts b/src/first.ts\n"
        "--- a/src/first.ts\n+++ b/src/first.ts\n@@ -10,1 +10,1 @@\n-old first\n+new first\n"
        "diff --git a/src/second.ts b/src/second.ts\n"
        "--- a/src/second.ts\n+++ b/src/second.ts\n@@ -80,1 +80,1 @@\n-old second\n+new second\n"
    )
    selected = _run(driver, "diffForPath", {"diff": combined, "path": "src/second.ts"})
    assert "@@ -80,1 +80,1 @@" in selected
    assert "new second" in selected
    assert "first.ts" not in selected
    assert "old first" not in selected


def test_backend_replace_preview_uses_real_file_position_and_separate_change_rows(tmp_path):
    from api import streaming

    path = tmp_path / "sample.ts"
    lines = [f"line {number}" for number in range(1, 141)]
    lines[132] = "        assert.equal(migrated[0]?.versions, undefined);"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    preview = streaming._live_mutation_preview_from_tool(
        "patch",
        {
            "mode": "replace",
            "path": str(path),
            "old_string": "        assert.equal(migrated[0]?.versions, undefined);",
            "new_string": "        const firstMigrated = migrated[0];",
        },
        workspace=tmp_path,
    )

    diff = preview["files"][0]["diff"]
    assert "@@ -130,7 +130,7 @@" in diff
    assert "-        assert.equal(migrated[0]?.versions, undefined);\n" in diff
    assert "+        const firstMigrated = migrated[0];\n" in diff
    assert "undefined);+        const firstMigrated" not in diff


def test_backend_replace_preview_without_baseline_separates_rows_without_fake_line_one():
    from api import streaming

    preview = streaming._live_mutation_preview_from_tool(
        "patch",
        {
            "mode": "replace",
            "path": "missing/sample.ts",
            "old_string": "old line",
            "new_string": "new line",
        },
        workspace=None,
    )

    diff = preview["files"][0]["diff"]
    assert "@@\n-old line\n+new line\n" in diff
    assert "@@ -1" not in diff
    assert "-old line+new line" not in diff


def test_backend_patch_failure_preview_never_falls_back_to_args_diff():
    from api import streaming

    raw_patch = "*** Begin Patch\n*** Update File: src/app.ts\n@@ -10,1 +10,1 @@\n-old\n+new\n*** End Patch"
    details = "Patch validation failed (no files were modified):\n  • src/app.ts: hunk not found"
    preview = streaming._live_mutation_preview_from_tool(
        "patch",
        {"path": "src/app.ts", "patch": raw_patch},
        result={"success": False, "error": details},
    )

    assert preview["failed"] is True
    assert preview["files"] == [{
        "path": "src/app.ts",
        "source": "patch",
        "failed": True,
        "status": "Patch could not be applied",
        "failure_details": details,
    }]
    assert "diff" not in preview["files"][0]


@pytest.mark.parametrize("as_json_string", [False, True])
def test_backend_extracts_authoritative_diff_from_structured_tool_result(as_json_string):
    from api import streaming

    expected = "--- a/x.ts\n+++ b/x.ts\n@@ -133,1 +133,1 @@\n-old\n+new\n"
    result = {"success": True, "diff": expected}
    if as_json_string:
        result = json.dumps(result)
    assert streaming._live_mutation_result_diff_text(result) == expected
    preview = streaming._live_mutation_preview_from_tool(
        "patch",
        {"path": "x.ts", "old_string": "old", "new_string": "new"},
        result=result,
    )
    assert preview["files"][0]["diff"] == expected
