import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
WORKSPACE = (ROOT / "static" / "workspace.js").read_text(encoding="utf-8")
STYLE = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    brace = source.index("{", start)
    depth = 0
    quote = None
    escaped = False
    for idx in range(brace, len(source)):
        ch = source[idx]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("'", '"', "`"):
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
    raise AssertionError(f"unterminated function {name}")


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_repository_sections_render_as_diagnostic_card_not_modified_files():
    functions = "\n".join(
        _function(UI, name)
        for name in (
            "_toolDiagnosticRawOutput",
            "_toolDiagnosticHeading",
            "_toolDiagnosticIsErrorText",
            "_toolDiagnosticSections",
            "_toolCommandDiagnosticInfo",
            "_buildToolDiagnosticCard",
        )
    )
    diff_detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    raw = """--- Ahead/Behind ---
main...origin/main  [ahead 2, behind 1]
--- Recent commits ---
abc123 Fix queue rendering
--- Remotes ---
origin  git@example.test:repo.git
--- Errors ---
gh: command not found"""
    script = f"""
function esc(value){{return String(value||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}
function li(){{return '<svg></svg>';}}
function _toolFullCommandLabel(tc){{return tc.args&&tc.args.cmd||'';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.cmd||'';}}
{diff_detector}
{functions}
const tc={{name:'terminal',args:{{cmd:'git status && git log && git remote -v && gh auth status'}},snippet:{json.dumps(raw)},done:true,is_error:true}};
const info=_toolCommandDiagnosticInfo(tc,'shell');
const row={{innerHTML:'',attrs:{{}},setAttribute(k,v){{this.attrs[k]=v;}}}};
_buildToolDiagnosticCard(row,tc,info);
process.stdout.write(JSON.stringify({{info,html:row.innerHTML,attrs:row.attrs}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)

    assert data["info"]["title"] == "Git diagnostics"
    assert [section["label"] for section in data["info"]["sections"]] == [
        "Ahead/Behind",
        "Recent commits",
        "Remotes",
        "Errors",
    ]
    assert data["attrs"]["data-tool-diagnostic"] == "1"
    assert "tool-diagnostic-card" in data["html"]
    assert "View raw output" in data["html"]
    assert "Modified Files" not in data["html"]
    assert "Open file" not in data["html"]
    assert "assistant-code-diff" not in data["html"]
    assert "assistant-modified-file-stat" not in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_section_delimiters_are_not_valid_file_diffs_but_real_patches_are():
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    script = f"""
{detector}
const diagnostic='--- Ahead/Behind ---\\nmain...origin/main [ahead 1]\\n--- Recent commits ---\\nabc123 update';
const patch='--- a/src/app.js\\n+++ b/src/app.js\\n@@ -10,1 +10,1 @@\\n-old\\n+new';
const v4a='*** Begin Patch\\n*** Update File: src/app.js\\n@@\\n-old\\n+new\\n*** End Patch';
process.stdout.write(JSON.stringify([_workspaceDiffLooksUseful(diagnostic),_workspaceDiffLooksUseful(patch),_workspaceDiffLooksUseful(v4a)]));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [False, True, True]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_repository_command_output_does_not_produce_modified_file_candidates():
    start = WORKSPACE.index("const ARTIFACT_IGNORE_RE")
    end = WORKSPACE.index("const _turnMutatedPreviewPaths")
    helpers = WORKSPACE[start:end]
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    raw = "--- Ahead/Behind ---\nmain...origin/main [ahead 1]\n--- Recent commits ---\nabc123 update"
    patch = "--- a/src/app.js\n+++ b/src/app.js\n@@ -1,1 +1,1 @@\n-old\n+new"
    script = f"""
const S={{session:{{workspace:'/repo'}}}};
{detector}
{helpers}
const diagnostic=_artifactCandidatesFromToolCall({{name:'terminal',result:{json.dumps(raw)}}});
const actualDiff=_artifactCandidatesFromToolCall({{name:'terminal',result:{json.dumps(patch)}}});
process.stdout.write(JSON.stringify({{diagnostic,actualDiff}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["diagnostic"] == []
    assert [item["path"] for item in data["actualDiff"]] == ["src/app.js"]


def test_diagnostic_card_styles_and_generic_routing_contract_exist():
    assert ".tool-diagnostic-card" in STYLE
    assert ".tool-diagnostic-section.is-error" in STYLE
    assert ".tool-change-summary-card" in STYLE
    assert ".tool-change-summary-file" in STYLE
    assert "typeof _toolCommandDiagnosticInfo==='function'" in UI
    assert "typeof _toolChangeSummaryInfo==='function'" in _function(UI, "buildToolCard")
    assert "if(isDiff)return null" in UI
    assert "View raw output" in _function(UI, "_buildToolDiagnosticCard")


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_generic_cli_failures_are_diagnostics_but_plain_command_output_is_not():
    functions = "\n".join(
        _function(UI, name)
        for name in (
            "_toolDiagnosticRawOutput",
            "_toolDiagnosticHeading",
            "_toolDiagnosticIsErrorText",
            "_toolDiagnosticSections",
            "_toolCommandDiagnosticInfo",
        )
    )
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    script = f"""
function _toolFullCommandLabel(tc){{return tc.args&&tc.args.cmd||'';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.cmd||'';}}
{detector}
{functions}
const failure=_toolCommandDiagnosticInfo({{args:{{cmd:'acme auth status'}},snippet:'acme: command not found',done:true,is_error:true}},'shell');
const ordinary=_toolCommandDiagnosticInfo({{args:{{cmd:'printf hello'}},snippet:'hello',done:true,is_error:false}},'shell');
process.stdout.write(JSON.stringify({{failure,ordinary}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["failure"]["title"] == "Command output"
    assert data["failure"]["error"] is True
    assert data["failure"]["sections"][0]["label"] == "Errors"
    assert data["ordinary"] is None


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_git_diff_stat_renders_one_neutral_change_summary_card():
    functions = "\n".join(
        _function(UI, name)
        for name in (
            "_toolDiagnosticRawOutput",
            "_toolChangeSummaryData",
            "_toolChangeSummaryInfo",
            "_toolChangeSummaryBodyHtml",
            "_buildToolChangeSummaryCard",
        )
    )
    diff_detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    raw = """static/ui.js        | 24 ++++++++++++++++++------
static/workspace.js | 10 ++++++----
2 files changed, 24 insertions(+), 10 deletions(-)"""
    script = f"""
function esc(value){{return String(value||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}
function li(){{return '<svg></svg>';}}
function _toolFullCommandLabel(tc){{return tc.args&&tc.args.cmd||'';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.cmd||'';}}
{diff_detector}
{functions}
const tc={{name:'terminal',args:{{cmd:'git diff --stat'}},snippet:{json.dumps(raw)},done:true}};
const info=_toolChangeSummaryInfo(tc,'shell');
const row={{innerHTML:'',attrs:{{}},setAttribute(k,v){{this.attrs[k]=v;}}}};
_buildToolChangeSummaryCard(row,tc,info);
process.stdout.write(JSON.stringify({{info,html:row.innerHTML,attrs:row.attrs}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)

    assert data["info"]["title"] == "Diff Summary"
    assert data["info"]["file_count"] == 2
    assert data["info"]["added"] == 24
    assert data["info"]["removed"] == 10
    assert data["attrs"]["data-tool-change-summary"] == "1"
    assert data["html"].count("static/ui.js") == 1
    assert data["html"].count("static/workspace.js") == 1
    assert "View raw output" in data["html"]
    assert "Modified Files" not in data["html"]
    assert "Diff on open" not in data["html"]
    assert "assistant-code-diff" not in data["html"]
    assert ">Edited<" not in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_informational_structured_file_metadata_is_not_a_mutation():
    start = WORKSPACE.index("const ARTIFACT_IGNORE_RE")
    end = WORKSPACE.index("const _turnMutatedPreviewPaths")
    helpers = WORKSPACE[start:end]
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    script = f"""
const S={{session:{{workspace:'/repo'}}}};
{detector}
{helpers}
const summary={{
  name:'terminal',
  args:{{cmd:'migration-check --changed-files'}},
  result:'2 files checked',
  mutation_preview:{{source:'repository-inspection',files:[
    {{path:'src/a.js',stats:{{added:3,removed:1}}}},
    {{path:'src/b.js',stats:{{added:0,removed:2}}}}
  ]}}
}};
const realWrite={{
  name:'write_file',args:{{path:'src/a.js',content:'next'}},result:'ok',
  mutation_preview:{{source:'write_file',files:[{{path:'src/a.js',stats:{{added:1,removed:0}}}}]}}
}};
process.stdout.write(JSON.stringify({{
  summary:_artifactCandidatesFromToolCall(summary),
  realWrite:_artifactCandidatesFromToolCall(realWrite)
}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["summary"] == []
    assert [item["path"] for item in data["realWrite"]] == ["src/a.js"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_path_only_output_is_not_a_change_summary_without_inspection_metadata():
    functions = "\n".join(
        _function(UI, name)
        for name in ("_toolDiagnosticRawOutput", "_toolChangeSummaryData", "_toolChangeSummaryInfo")
    )
    diff_detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    script = f"""
function _toolFullCommandLabel(tc){{return tc.args&&tc.args.cmd||'';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.cmd||'';}}
{diff_detector}
{functions}
const ordinary=_toolChangeSummaryInfo({{name:'terminal',args:{{cmd:'printf src/app.js'}},snippet:'src/app.js',done:true}},'shell');
const inspected=_toolChangeSummaryInfo({{name:'terminal',args:{{cmd:'git diff --name-only'}},snippet:'src/app.js',done:true}},'shell');
const status=_toolChangeSummaryInfo({{name:'terminal',args:{{cmd:'git status --short'}},snippet:' M src/app.js\\n?? tests/new_test.py',done:true}},'shell');
const ahead=_toolChangeSummaryInfo({{name:'terminal',args:{{cmd:'git rev-list --left-right --count HEAD...origin/main'}},snippet:'2\\t1',done:true}},'shell');
const log=_toolChangeSummaryInfo({{name:'terminal',args:{{cmd:'git log -1 --oneline'}},snippet:'abc123 Fix renderer',done:true}},'shell');
const migration=_toolChangeSummaryInfo({{
  name:'terminal',args:{{cmd:'migration-check --changed-files'}},snippet:'2 files checked',done:true,
  mutation_preview:{{source:'repository-inspection',files:[
    {{path:'src/a.js',stats:{{added:3,removed:1}}}},{{path:'src/b.js',stats:{{added:0,removed:2}}}}
  ]}}
}},'shell');
process.stdout.write(JSON.stringify({{ordinary,inspected,status,ahead,log,migration}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["ordinary"] is None
    assert data["inspected"]["title"] == "Diff Summary"
    assert [item["path"] for item in data["inspected"]["files"]] == ["src/app.js"]
    assert data["status"]["title"] == "Repository Changes"
    assert [item["path"] for item in data["status"]["files"]] == ["src/app.js", "tests/new_test.py"]
    assert data["ahead"]["title"] == "Change Inspection"
    assert data["ahead"]["file_count"] is None
    assert data["log"] is None
    assert data["migration"]["title"] == "Change Inspection"
    assert data["migration"]["file_count"] == 2
    assert data["migration"]["added"] == 3
    assert data["migration"]["removed"] == 3


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_mixed_summary_and_patches_render_one_overview_before_real_diff_cards():
    functions = "\n".join(
        _function(UI, name)
        for name in (
            "_assistantModifiedFilesRawOutputHtml",
            "_assistantModifiedPatchFailureHtml",
            "_assistantModifiedFileIconHtml",
            "_assistantModifiedFileStatsHtml",
            "_toolChangeSummaryData",
            "_toolChangeSummaryBodyHtml",
            "_assistantModifiedChangeOverview",
            "_assistantModifiedFilesSummaryHtml",
        )
    )
    raw = """src/a.js | 3 ++-
src/b.js | 2 +-
2 files changed, 3 insertions(+), 2 deletions(-)
diff --git a/src/a.js b/src/a.js"""
    script = f"""
function esc(value){{return String(value||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}}
function assistantModifiedDiffStatsText(item){{return `+${{item.stats.added}} -${{item.stats.removed}}`;}}
function assistantModifiedDiffHtml(diff){{return `<div class="assistant-code-diff">${{esc(diff)}}</div>`;}}
function _changedTurnKindForSource(){{return 'Edited';}}
function _workspaceDisplayPath(path){{return path;}}
{functions}
const raw={json.dumps(raw)};
const html=_assistantModifiedFilesSummaryHtml([
  {{path:'src/a.js',source:'patch',diff:'patch-a',stats:{{added:2,removed:1}},raw_output:raw}},
  {{path:'src/b.js',source:'patch',diff:'patch-b',stats:{{added:1,removed:1}},raw_output:raw}}
],{{open:true,openFiles:true}});
process.stdout.write(html);
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    html = result.stdout
    assert html.count('class="assistant-change-overview"') == 1
    assert html.index("Change Overview") < html.index("patch-a")
    assert html.count("2 files changed, 3 insertions(+), 2 deletions(-)") == 1
    assert html.count('class="assistant-code-diff"') == 2
