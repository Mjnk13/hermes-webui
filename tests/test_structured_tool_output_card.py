import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
WORKSPACE = (ROOT / "static" / "workspace.js").read_text(encoding="utf-8")
STYLE = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
MESSAGES = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    brace = source.index("{", start)
    depth = 0
    quote = None
    regex = False
    regex_class = False
    line_comment = False
    block_comment = False
    escaped = False
    previous_significant = ""
    for idx in range(brace, len(source)):
        ch = source[idx]
        following = source[idx + 1] if idx + 1 < len(source) else ""
        if line_comment:
            if ch == "\n":
                line_comment = False
            continue
        if block_comment:
            if ch == "*" and following == "/":
                block_comment = False
            continue
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if regex:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "[":
                regex_class = True
            elif ch == "]":
                regex_class = False
            elif ch == "/" and not regex_class:
                regex = False
                previous_significant = "/"
            continue
        if ch == "/" and idx > 0 and source[idx - 1] == "*":
            continue
        if ch == "/" and following == "/":
            line_comment = True
            continue
        if ch == "/" and following == "*":
            block_comment = True
            continue
        if ch == "/" and previous_significant in "=(:,![{{;?&|}}":
            regex = True
            regex_class = False
            continue
        if ch in ("'", '"', "`"):
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start : idx + 1]
        if not ch.isspace():
            previous_significant = ch
    raise AssertionError(f"unterminated function {name}")


def _classifier_source() -> str:
    summary = _function(UI, "_toolChangeSummaryData")
    start = UI.index("function _toolDiagnosticRawOutput(")
    end = UI.index("function _toolOutputTokenHtml(", start)
    return (
        summary
        + "\n"
        + UI[start:end]
        + "\n"
        + _function(UI, "_toolOutputSafeJsonParse")
        + "\n"
        + _function(UI, "_toolOutputSafeJsonEnvelope")
        + "\n"
        + _function(UI, "_toolOutputPrismLanguage")
        + "\n"
        + _function(UI, "_toolOutputContentKind")
        + "\n"
        + _function(UI, "_toolOutputStructuredDocument")
    )


def _structured_formatter_source() -> str:
    names = (
        "_toolOutputDisplayText",
        "_toolOutputCollapsedLines",
        "_toolOutputTokenHtml",
        "_toolOutputFormattedHtml",
        "_toolOutputSafeJsonParse",
        "_toolOutputSafeJsonEnvelope",
        "_toolOutputContentKind",
        "_toolOutputStructuredDocument",
        "_toolOutputTestSummary",
        "_toolOutputTestCounts",
        "_toolOutputStructuredFieldLabel",
        "_toolOutputPatchFailures",
        "_toolOutputLooksLikeUrl",
        "_toolOutputBrowserContext",
        "_toolOutputPrismLanguage",
        "_toolOutputNestedStringModel",
        "_toolOutputRunningCommandModel",
        "_toolOutputDisplayModel",
        "_toolOutputNumberedTranscriptModel",
        "_toolOutputNumberedTranscriptHtml",
        "_toolOutputCodeHtml",
        "_toolOutputCommandHtml",
        "_toolOutputAnsiHtml",
        "_toolOutputTestSections",
        "_toolOutputTestResultHtml",
        "_toolOutputJsonHtml",
        "_toolOutputDisplayModelHtml",
    )
    return "\n".join(_function(UI, name) for name in names)


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_output_classification_prefers_metadata_and_never_hijacks_real_patches():
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    patch = "diff --git a/src/app.ts b/src/app.ts\n--- a/src/app.ts\n+++ b/src/app.ts\n@@ -1 +1 @@\n-old\n+new"
    patch_failure = "Patch validation failed\nfile src/a.ts: hunk 5 no matching context found"
    repository = "src/a.ts | 2 +-\n1 file changed, 1 insertion(+), 1 deletion(-)"
    script = f"""
function _toolFullCommandLabel(tc){{return tc.args&&tc.args.cmd||'';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.cmd||'';}}
{detector}
{_classifier_source()}
const patch={json.dumps(patch)};
const cases={{
  patch:_toolOutputKind({{name:'terminal',args:{{cmd:'git diff'}},snippet:patch,done:true}},'shell'),
  patchInfo:_toolStructuredOutputInfo({{name:'terminal',args:{{cmd:'git diff'}},snippet:patch,done:true}},'shell'),
  streamingInfo:_toolStructuredOutputInfo({{name:'terminal',args:{{cmd:'printf value'}},snippet:'{{"content":"incomplete',done:false}},'shell')?.kind||null,
  patchFailure:_toolOutputKind({{name:'apply_patch',snippet:{json.dumps(patch_failure)},result_metadata:{{output_kind:'patch'}},done:true,is_error:true}},'write'),
  error:_toolOutputKind({{name:'terminal',args:{{cmd:'gh auth status'}},snippet:'gh: command not found',result_metadata:{{exit_code:127}},done:true}},'shell'),
  warning:_toolOutputKind({{name:'validate',snippet:'provider.apiKey is missing',metadata:{{severity:'warning'}},done:true}},'unknown'),
  jsonSuccess:_toolOutputKind({{name:'browser_inspect',snippet:'{{"success":true,"title":"DirectCloud"}}',done:true}},'web'),
  sourceWord:_toolOutputKind({{name:'read_file',snippet:'const error = false;',done:true}},'read'),
  repositoryError:_toolOutputKind({{name:'terminal',args:{{cmd:'git diff --stat'}},snippet:'fatal: not a git repository',done:true}},'shell'),
  repository:_toolOutputKind({{name:'terminal',args:{{cmd:'git diff --stat'}},snippet:{json.dumps(repository)},done:true}},'shell')
}};
process.stdout.write(JSON.stringify(cases));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data == {
        "patch": "live-diff",
        "patchInfo": None,
        "streamingInfo": "command-output",
        "patchFailure": "error",
        "error": "error",
        "warning": "warning",
        "jsonSuccess": "success",
        "sourceWord": "raw",
        "repositoryError": "error",
        "repository": "repository-summary",
    }


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_test_counts_and_default_expansion_follow_result_severity():
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    script = f"""
function _toolFullCommandLabel(tc){{return tc.args&&tc.args.cmd||'';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.cmd||'';}}
{detector}
{_classifier_source()}
const failed=_toolStructuredOutputInfo({{name:'terminal',args:{{cmd:'pnpm test'}},snippet:'18 passed, 2 failed, 1 skipped',done:true,is_error:true}},'shell');
const passed=_toolStructuredOutputInfo({{name:'terminal',args:{{cmd:'pytest -q'}},snippet:'18 passed, 1 skipped',done:true,result:{{exit_code:0}}}},'shell');
const shortWarning=_toolStructuredOutputInfo({{name:'validate',snippet:'Deprecated option used',metadata:{{severity:'warning'}},done:true}},'unknown');
const longWarning=_toolStructuredOutputInfo({{name:'validate',snippet:Array(30).fill('Deprecated option used').join('\\n'),metadata:{{severity:'warning'}},done:true}},'unknown');
process.stdout.write(JSON.stringify({{failed,passed,shortWarning,longWarning}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["failed"]["kind"] == "test-result"
    assert data["failed"]["counts"] == {"passed": 18, "failed": 2, "skipped": 1}
    assert data["failed"]["expanded"] is True
    assert data["passed"]["expanded"] is False
    assert data["shortWarning"]["expanded"] is True
    assert data["longWarning"]["expanded"] is False


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_structured_summary_is_compact_and_file_target_is_not_labeled_as_command():
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    raw = json.dumps({"content": "const value = 1;\nreturn value;", "path": "src/value.ts"})
    script = f"""
function _toolFullCommandLabel(){{return '';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.path||'';}}
{detector}
{_classifier_source()}
const info=_toolStructuredOutputInfo({{name:'read_file',args:{{path:'src/value.ts'}},snippet:{json.dumps(raw)},done:true}},'read');
process.stdout.write(JSON.stringify(info));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    info = json.loads(result.stdout)
    assert info["summary"] == "Content · 2 lines"
    assert info["command"] == ""
    assert info["target"] == "src/value.ts"
    assert info["operation"] == "Read file"
    assert info["title"] == "Read file completed"
    assert info["title"] != "Command completed"


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_unknown_completed_tool_keeps_generic_operation_and_invocation_inputs():
    """Invocation context must not depend on a fixed tool-name allowlist."""
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    raw = json.dumps({"status": "ok", "details": "first line\nsecond line"})
    script = f"""
function _toolFullCommandLabel(){{return '';}}
function _toolTargetLabel(){{return '';}}
{detector}
{_classifier_source()}
const tc={{name:'migration_check_v2',args:{{workspace:'/tmp/project',mode:'safe',limit:25}},snippet:{json.dumps(raw)},done:true}};
process.stdout.write(JSON.stringify(_toolStructuredOutputInfo(tc,'unknown')));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    info = json.loads(result.stdout)
    assert info["operation"] == "Migration check v2"
    assert info["title"] == "Migration check v2 completed"
    assert info["command"] == ""
    assert info["invocation"] == {
        "kind": "parameters",
        "label": "Inputs",
        "fields": {"workspace": "/tmp/project", "mode": "safe", "limit": "25"},
    }


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_serialized_newlines_repetition_and_token_highlighting_preserve_raw_output():
    functions = "\n".join(
        _function(UI, name)
        for name in (
            "_toolOutputDisplayText",
            "_toolOutputCollapsedLines",
            "_toolOutputTokenHtml",
            "_toolOutputFormattedHtml",
        )
    )
    raw = "Warning: deprecated option used\nWarning: deprecated option used\nWarning: deprecated option used\nsrc/components/Button.tsx:42:17 failed with HTTP 502\nconfig.yaml\nhttps://example.test/help"
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{functions}
const raw={json.dumps(raw)};
process.stdout.write(JSON.stringify({{
  decoded:_toolOutputDisplayText(JSON.stringify('first\\n  second')),
  literal:_toolOutputDisplayText('first\\\\nsecond'),
  lines:_toolOutputCollapsedLines(raw),
  html:_toolOutputFormattedHtml(raw),
  raw
}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["decoded"] == "first\n  second"
    assert data["literal"] == r"first\nsecond"
    assert data["lines"][0] == {"text": "Warning: deprecated option used", "count": 3}
    assert "Repeated 3 times" in data["html"]
    assert "tool-output-token-warning" in data["html"]
    assert "tool-output-location" in data["html"]
    assert 'data-line="42"' in data["html"]
    assert 'data-column="17"' in data["html"]
    assert 'class="tool-output-path"' in data["html"]
    assert 'data-path="config.yaml"' in data["html"]
    assert "tool-output-token-error" in data["html"]
    assert "tool-output-token-http" in data["html"]
    assert 'href="https://example.test/help"' in data["html"]
    assert data["raw"] == raw


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_semantic_output_colors_cover_status_locations_inline_code_and_metadata():
    functions = "\n".join(
        _function(UI, name)
        for name in (
            "_toolOutputDisplayText",
            "_toolOutputCollapsedLines",
            "_toolOutputTokenHtml",
            "_toolOutputFormattedHtml",
        )
    )
    raw = "\n".join(
        (
            "Error failure denied invalid exception",
            "Warning caution deprecated",
            "Success created updated completed",
            "Info notice checking processing",
            "ERR_CONNECTION_REFUSED Exit 1 HTTP 502",
            "src/components/Button.tsx:42:17",
            "Property `variant` does not exist on type `ButtonProps`.",
            '42 | <Button variant="ghost" />',
            "                   ^^^^^^^",
            "2026-07-21T12:34:56Z pid=4312",
            "18 passed 2 failed 1 skipped",
        )
    )
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{functions}
const raw={json.dumps(raw)};
process.stdout.write(JSON.stringify({{html:_toolOutputFormattedHtml(raw),raw}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    html = data["html"]
    assert html.count('class="tool-output-token-error"') >= 5
    assert html.count('class="tool-output-token-warning"') >= 3
    assert html.count('class="tool-output-token-success"') >= 4
    assert html.count('class="tool-output-token-info"') >= 4
    assert 'class="tool-output-token-status is-error"' in html
    assert 'class="tool-output-token-exit is-error"' in html
    assert 'class="tool-output-token-http is-error"' in html
    assert 'class="tool-output-location-path">src/components/Button.tsx</span>' in html
    assert 'class="tool-output-location-line">42</span>' in html
    assert 'class="tool-output-location-column">17</span>' in html
    assert html.count('class="tool-output-inline-code"') == 2
    assert 'class="tool-output-line is-caret"' in html
    assert html.count('class="tool-output-token-meta"') == 2
    assert 'tool-output-token-test is-passed' in html
    assert 'tool-output-token-test is-failed' in html
    assert 'tool-output-token-test is-skipped' in html
    assert data["raw"] == raw


def test_semantic_output_styles_are_token_scoped_and_theme_aware():
    assert ".tool-output-token-info" in STYLE
    assert ".tool-output-token-status.is-error" in STYLE
    assert ".tool-output-inline-code" in STYLE
    assert ".tool-output-line.is-caret" in STYLE
    assert ".tool-output-token-meta" in STYLE
    assert ".tool-output-location-line" in STYLE
    assert "color-mix(in srgb,var(--error" in STYLE
    assert "color-mix(in srgb,var(--warning" in STYLE
    assert "color-mix(in srgb,var(--success" in STYLE


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_valid_json_is_parsed_once_pretty_colored_and_keeps_literal_backslashes():
    raw = json.dumps(
        {
            "success": True,
            "message": "Line one\nLine two",
            "pattern": r"\n",
            "windowsPath": r"C:\Users\Harry\project",
            "regex": r"\d+\.\d+",
            "path": "src/components/Button.tsx",
            "status": "failed",
            "url": "http://localhost:3000/en/service/directcloud-ai",
            "script": "<script>alert('x')</script>",
            "jsx": '<Button variant="ghost">Open</Button>',
            "nothing": None,
            "count": 42,
        },
        separators=(",", ":"),
    )
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const model=_toolOutputDisplayModel({{name:'inspect'}},{{raw}});
process.stdout.write(JSON.stringify({{model,html:_toolOutputDisplayModelHtml(model),raw}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["model"]["kind"] == "content-wrapper"
    assert data["model"]["metadata"]["message"] == "Line one\nLine two"
    assert data["model"]["metadata"]["pattern"] == r"\n"
    assert data["model"]["metadata"]["windowsPath"] == r"C:\Users\Harry\project"
    assert "tool-output-wrapper-metadata" in data["html"]
    assert "tool-output-text" in data["html"]
    assert "tool-output-json-number" in data["html"]
    assert "tool-output-empty-value" in data["html"]
    assert 'href="http://localhost:3000/en/service/directcloud-ai"' in data["html"]
    assert 'class="tool-output-path"' in data["html"]
    assert 'class="tool-output-value-status is-error"' in data["html"]
    assert "<script>" not in data["html"]
    assert "&lt;script&gt;" in data["html"]
    assert "<Button" not in data["html"]
    assert "&lt;Button" in data["html"]
    assert data["raw"] == raw


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_invalid_json_like_text_falls_back_without_unescaping_or_entity_decoding():
    raw = r'{"pattern":"\d+","windowsPath":"C:\Users\Harry","message":"line\nnext", "html":"&lt;div&gt;"'
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const model=_toolOutputDisplayModel({{name:'inspect'}},{{raw}});
process.stdout.write(JSON.stringify({{model,html:_toolOutputDisplayModelHtml(model)}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["model"]["kind"] == "text"
    assert data["model"]["text"] == raw
    assert r"\d+" in data["model"]["text"]
    assert r"C:\Users\Harry" in data["model"]["text"]
    assert r"line\nnext" in data["model"]["text"]
    assert "&amp;lt;div&amp;gt;" in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_serialized_string_is_decoded_once_and_not_recursively_reparsed():
    decoded_once = r'{"pattern":"\\n","windowsPath":"C:\\Users\\Harry"}'
    raw = json.dumps(decoded_once)
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const model=_toolOutputDisplayModel({{name:'inspect'}},{{raw}});
process.stdout.write(JSON.stringify(model));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    model = json.loads(result.stdout)
    assert model["kind"] == "text"
    assert model["serialized"] is True
    assert model["text"] == decoded_once


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_copy_raw_uses_the_exact_stored_payload():
    copy_function = _function(UI, "copyToolStructuredOutputRaw")
    raw = 'line one\\nline two\nC:\\Users\\Harry\\project\n{"content":"<script>x</script>"}'
    script = f"""
let copied=null;
function _copyText(value){{copied=value;return Promise.resolve();}}
function setTimeout(){{}}
{copy_function}
const row={{_toolOutputRaw:{json.dumps(raw)}}};
const button={{textContent:'Copy raw',closest(){{return row;}}}};
copyToolStructuredOutputRaw(button);
Promise.resolve().then(()=>process.stdout.write(JSON.stringify({{copied}})));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["copied"] == raw


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_copy_structured_content_uses_the_exact_once_parsed_document():
    copy_function = _function(UI, "copyToolStructuredOutputContent")
    content = "---\nname: demo\n---\n\n# Heading\n\n`\\n` C:\\Users\\Harry <script>x</script>"
    script = f"""
let copied=null;
function _copyText(value){{copied=value;return Promise.resolve();}}
function setTimeout(){{}}
{copy_function}
const row={{_toolOutputContentRaw:{json.dumps(content)}}};
const button={{textContent:'Copy content',closest(){{return row;}}}};
copyToolStructuredOutputContent(button);
Promise.resolve().then(()=>process.stdout.write(JSON.stringify({{copied}})));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["copied"] == content


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_content_wrapper_detects_inner_code_without_double_unescaping():
    inner = "485| const rows = Array.isArray(rowsRaw)\n486| const pattern = /\\d+\\.\\d+/g\n487| const literal = '\\\\n'"
    raw = json.dumps({"content": inner, "path": "src/table.ts", "language": "typescript"})
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const model=_toolOutputDisplayModel({{name:'read_file'}},{{raw}});
process.stdout.write(JSON.stringify({{model,html:_toolOutputDisplayModelHtml(model)}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["model"]["kind"] == "content-wrapper"
    assert data["model"]["content"]["kind"] == "code"
    assert data["model"]["content"]["text"] == inner
    assert data["model"]["metadata"]["path"] == "src/table.ts"
    assert "tool-output-code-line-number" in data["html"]
    assert "tool-output-code" in data["html"]
    assert r"\d+\.\d+" in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_actual_tool_result_path_unwraps_object_and_serialized_content_wrapper():
    """Exercise the same info -> display-model seam used by buildToolCard.

    Model-only tests that pass `info.raw` directly miss the reported path where
    the wrapper lives at `tc.result.content` and the raw extractor chooses a
    generic string representation before recursive classification.
    """
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    inner = "\n".join(
        (
            "1|import assert from 'node:assert/strict';",
            "2|import test from 'node:test';",
            "3|",
            "4|import { mergeAdminRealtimeEditorState } from '@/utils/admin/realtime';",
            "5|const pattern = /\\d+\\.\\d+/g;",
        )
    )
    wrapper = {"content": inner}
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function _toolFullCommandLabel(){{return '';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.path||'';}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
const wrapper={json.dumps(wrapper)};
const cases={{
  object:{{name:'read_file',args:{{path:'src/admin/realtime.test.ts'}},result:wrapper,done:true}},
  serialized:{{name:'read_file',args:{{path:'src/admin/realtime.test.ts'}},result:JSON.stringify(wrapper),done:true}},
  mixed:{{name:'read_file',args:{{path:'src/admin/realtime.test.ts'}},snippet:'Completed',result:wrapper,done:true}},
}};
const output={{}};
for(const [name,tc] of Object.entries(cases)){{
  const info=_toolStructuredOutputInfo(tc,'read');
  const model=_toolOutputDisplayModel(tc,info);
  output[name]={{info,model,html:_toolOutputDisplayModelHtml(model)}};
}}
process.stdout.write(JSON.stringify(output));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    for shape in ("object", "serialized", "mixed"):
        case = data[shape]
        assert case["model"]["kind"] == "content-wrapper", shape
        assert case["model"]["content"]["kind"] == "code", shape
        assert case["model"]["content"]["text"] == inner, shape
        assert case["model"]["content"]["language"] == "typescript", shape
        if shape == "serialized":
            assert case["model"]["raw"] == json.dumps(wrapper, separators=(",", ":"))
        else:
            # An already-structured object has no original byte representation;
            # the raw drawer keeps its lossless JSON serialization.
            assert json.loads(case["model"]["raw"]) == wrapper
        assert "tool-output-code-line-number" in case["html"], shape
        assert 'class="language-typescript"' in case["html"], shape
        assert "1|import" not in case["html"], shape
        assert "import assert from" in case["html"], shape


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_dynamic_multiline_result_fields_are_value_classified_on_every_result_path():
    """Structured search output must not depend on a ``content`` field name."""
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    matches = "\n".join(
        (
            "/Users/demo/project/src/components/Picker/impl.tsx",
            "  48: initial,",
            "  49: messages,",
            "  50: mode,",
            "  51: onOpenChange,",
        )
    )
    payload = {
        "total_count": 223,
        "matches_format": "path-grouped: each file path on its own line, followed by indented '<line>: <content>' rows",
        "matches_text": matches,
        "unknown_notes": "First note\n  indented continuation",
        "literal_escape": r"\n",
    }
    serialized = json.dumps(payload, separators=(",", ":"))
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function _toolFullCommandLabel(){{return '';}}
function _toolTargetLabel(){{return '';}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
const payload={json.dumps(payload)};
const serialized={json.dumps(serialized)};
const cases={{
  object:{{name:'search',snippet:'Search completed',result:payload,done:true}},
  serialized:{{name:'search',snippet:'Search completed',result:serialized,done:true}},
  output:{{name:'search',snippet:'Search completed',output:payload,done:true}},
  snippet:{{name:'search',snippet:serialized,done:true}},
  metadata:{{name:'search_files',snippet:'{{"total_count":57,"matches_text":"/Users/demo/project/src/a.ts\\n  12: truncated…',result_metadata:payload,done:true}},
}};
const output={{}};
for(const [shape,tc] of Object.entries(cases)){{
  const info=_toolStructuredOutputInfo(tc,'search');
  const model=_toolOutputDisplayModel(tc,info);
  output[shape]={{info,model,html:_toolOutputDisplayModelHtml(model)}};
}}
process.stdout.write(JSON.stringify(output));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    for shape in ("object", "serialized", "output", "snippet", "metadata"):
        case = data[shape]
        assert case["info"]["label"] == "Success", shape
        expected_title = "Search files completed" if shape == "metadata" else "Search completed"
        assert case["info"]["title"] == expected_title, shape
        assert case["model"]["kind"] == "content-wrapper", shape
        assert case["model"]["content"]["text"] == matches, shape
        assert case["model"]["contentLabel"] == "Matches", shape
        assert case["model"]["metadata"]["total_count"] == 223, shape
        assert "path-grouped" in case["model"]["metadata"]["matches_format"], shape
        assert case["model"]["metadata"]["unknown_notes"] == "First note\n  indented continuation", shape
        assert case["model"]["metadata"]["literal_escape"] == r"\n", shape
        assert "tool-output-wrapper-metadata" in case["html"], shape
        assert "tool-output-text" in case["html"], shape
        assert "  48: initial," in case["html"], shape
        assert "indented continuation" in case["html"], shape
        assert case["html"].count("tool-output-text") >= 2, shape
        assert r"\n" in case["html"], shape
        assert serialized not in case["html"], shape
    assert data["serialized"]["model"]["raw"] == serialized
    assert data["snippet"]["model"]["raw"] == serialized
    assert json.loads(data["object"]["model"]["raw"]) == payload
    assert json.loads(data["output"]["model"]["raw"]) == payload
    assert json.loads(data["metadata"]["model"]["raw"]) == payload


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_shell_result_promotes_dynamic_output_value_and_formats_node_test_failures():
    """Exercise the reported terminal result shape through ``buildToolCard``."""
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    output = "\n".join(
        (
            "src/app/parser.test.ts 110ms",
            "✔ parser preserves valid input (2ms)",
            "✖ parser rejects invalid input (5ms)",
            "ℹ tests 25",
            "ℹ suites 0",
            "ℹ pass 24",
            "ℹ fail 1",
            "ℹ cancelled 0",
            "ℹ skipped 0",
            "ℹ todo 0",
            "ℹ duration_ms 815.521",
            "✖ failing tests:",
            "",
            "test at src/app/parser.test.ts:42:1",
            "✖ parser rejects invalid input (5ms)",
            "  AssertionError [ERR_ASSERTION]: input did not match /\\s*expected/",
            "  Expected pattern: /\\s*expected/",
            '  Actual input: "actual"',
            "  at TestContext.<anonymous> (src/app/parser.test.ts:42:17)",
            '42 | const value = "<script>safe</script>";',
            "                         ^^^^^^",
        )
    )
    wrapper = {"output": output}
    raw = json.dumps(wrapper, separators=(",", ":"))
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function li(name){{return `<svg data-icon="${{name}}"></svg>`;}}
function _toolFullCommandLabel(tc){{return tc.args&&tc.args.command||'';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.command||'';}}
function _toolActionKind(){{return 'shell';}}
function _toolActionLabelText(){{return 'Shell command';}}
function _toolDisplayName(){{return 'Shell command';}}
function _toolDisclosureIdentity(){{return '';}}
function _toolChangeSummaryInfo(){{return null;}}
function _toolCommandDiagnosticInfo(){{return null;}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
{_function(UI, '_buildToolStructuredOutputCard')}
{_function(UI, 'buildToolCard')}
global.document={{createElement(){{return {{className:'',dataset:{{}},innerHTML:'',attrs:{{}},setAttribute(k,v){{this.attrs[k]=String(v);}},removeAttribute(k){{delete this.attrs[k];}}}};}}}};
const output={json.dumps(output)};
const raw={json.dumps(raw)};
const dynamicRaw=JSON.stringify({{test_log:output}});
const cases={{
  resultString:{{name:'terminal',args:{{command:'pnpm exec tsx --test tests/*.test.ts'}},result:raw,snippet:'Completed',done:true,is_error:true,exit_code:1}},
  outputObject:{{name:'terminal',args:{{command:'pnpm exec tsx --test tests/*.test.ts'}},output:{{output}},snippet:'Completed',done:true,is_error:true,exit_code:1}},
  snippet:{{name:'terminal',args:{{command:'pnpm exec tsx --test tests/*.test.ts'}},snippet:raw,done:true,is_error:true,exit_code:1}},
  dynamicField:{{name:'terminal',args:{{command:'pnpm exec tsx --test tests/*.test.ts'}},result:dynamicRaw,snippet:'Completed',done:true,is_error:true,exit_code:1}},
}};
const rendered={{}};
for(const [shape,tc] of Object.entries(cases)){{
  const info=_toolStructuredOutputInfo(tc,'shell');
  const model=_toolOutputDisplayModel(tc,info);
  const row=buildToolCard(tc);
  rendered[shape]={{info,model,html:row.innerHTML,raw:row._toolOutputRaw}};
}}
process.stdout.write(JSON.stringify(rendered));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    for shape, case in data.items():
        assert case["info"]["kind"] == "test-result", shape
        assert case["info"]["counts"] == {"passed": 24, "failed": 1, "skipped": 0}, shape
        assert case["info"]["testSummary"]["total"] == 25, shape
        assert case["info"]["testSummary"]["duration"] == "815.521ms", shape
        assert case["model"]["kind"] == "command-result", shape
        assert case["model"]["output"]["text"] == output, shape
        if shape == "outputObject":
            assert json.loads(case["raw"]) == wrapper
        elif shape == "dynamicField":
            assert json.loads(case["raw"]) == {"test_log": output}
        else:
            assert case["raw"] == raw, shape
        assert "tool-output-test-summary" in case["html"], shape
        assert "25 tests" in case["html"], shape
        assert "24 passed" in case["html"], shape
        assert "1 failed" in case["html"], shape
        assert "815.521ms" in case["html"], shape
        assert "tool-output-line is-test-pass" in case["html"], shape
        assert "tool-output-line is-test-fail" in case["html"], shape
        assert "tool-output-failing-tests" in case["html"], shape
        assert "AssertionError" in case["html"], shape
        assert r"/\s*expected/" in case["html"], shape
        assert 'data-path="src/app/parser.test.ts"' in case["html"], shape
        assert 'data-line="42"' in case["html"], shape
        assert 'data-column="17"' in case["html"], shape
        assert "&lt;script&gt;safe&lt;/script&gt;" in case["html"], shape
        assert '&quot;output&quot;' not in case["html"], shape


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_mixed_json_wrapper_and_trailing_tool_warning_keep_test_output_structured():
    """The reported card is valid JSON followed by a separate loop warning.

    This is the production shape after a repeated tool failure: the command
    wrapper remains the primary result and the warning is appended after it.
    Parsing the whole string as one JSON document fails, so the renderer must
    safely recognize one complete JSON envelope without dropping or rewriting
    the trailing diagnostic.
    """
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    output = "\n".join(
        (
            "✔ Global maps managed SEO cells to their canonical source editor identities (4.875542ms)",
            "✔ Global detects only changed source-backed SEO cells, including noIndex (0.673542ms)",
            "✖ Global fails the aggregate transaction when a changed canonical Payload SEO row is missing (4.497375ms)",
            "ℹ tests 6",
            "ℹ suites 0",
            "ℹ pass 5",
            "ℹ fail 1",
            "ℹ cancelled 0",
            "ℹ skipped 0",
            "ℹ todo 0",
            "ℹ duration_ms 853.390333",
            "",
            "✖ failing tests:",
            "",
            "test at tests/admin-global-realtime-dependency.test.ts:2:4380",
            "✖ Global fails the aggregate transaction when a changed canonical Payload SEO row is missing (4.497375ms)",
            r"  AssertionError [ERR_ASSERTION]: The input did not match /throw new Error\(`M\$\{config\.collection\}:\$\{locale\}`\)/u.",
            r"  actual: \"async function writePayloadSeoSource(\n    config: PayloadSeoSourceConfig,\n): Promise<string | null>\"",
            "  at TestContext.<anonymous> (/Users/harry/Code/directcloud-global-web/tests/admin-global-realtime-dependency.test.ts:122:12)",
        )
    )
    wrapper = {"output": output, "exit_code": 1, "error": None}
    warning = (
        "[Tool loop warning: repeated_exact_failure_warning; count=2; "
        "terminal has failed 2 times with identical arguments. This looks like "
        "a loop; inspect the error and change strategy instead of retrying it unchanged.]"
    )
    raw = json.dumps(wrapper, ensure_ascii=False) + "\n\n" + warning
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function li(name){{return `<svg data-icon="${{name}}"></svg>`;}}
function _toolFullCommandLabel(tc){{return tc.args&&tc.args.command||'';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.command||'';}}
function _toolActionKind(){{return 'shell';}}
function _toolActionLabelText(){{return 'Shell command';}}
function _toolDisplayName(){{return 'Shell command';}}
function _toolDisclosureIdentity(){{return '';}}
function _toolChangeSummaryInfo(){{return null;}}
function _toolCommandDiagnosticInfo(){{return null;}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
{_function(UI, '_buildToolStructuredOutputCard')}
{_function(UI, 'buildToolCard')}
global.document={{createElement(){{return {{className:'',dataset:{{}},innerHTML:'',attrs:{{}},setAttribute(k,v){{this.attrs[k]=String(v);}},removeAttribute(k){{delete this.attrs[k];}}}};}}}};
const output={json.dumps(output)};
const warning={json.dumps(warning)};
const raw={json.dumps(raw)};
const tc={{
  name:'terminal',
  args:{{command:'pnpm exec tsx --test tests/admin-global-realtime-dependency.test.ts'}},
  snippet:raw,
  result:'',
  output:'',
  result_metadata:{{}},
  done:true,
  is_error:false,
}};
const info=_toolStructuredOutputInfo(tc,'shell');
const model=_toolOutputDisplayModel(tc,info);
const row=buildToolCard(tc);
process.stdout.write(JSON.stringify({{info,model,html:row.innerHTML,raw:row._toolOutputRaw,output,warning}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["info"]["kind"] == "test-result"
    assert data["info"]["counts"] == {"passed": 5, "failed": 1, "skipped": 0}
    assert data["info"]["testSummary"]["total"] == 6
    assert data["info"]["testSummary"]["duration"] == "853.390333ms"
    assert data["info"]["exitCode"] == 1
    assert data["model"]["kind"] == "command-result"
    assert data["model"]["output"]["text"] == output
    assert data["model"]["remainder"]["text"] == warning
    assert data["model"]["stdout"] == ""
    assert data["raw"] == raw
    assert "tool-output-test-summary" in data["html"]
    assert "5 passed" in data["html"]
    assert "1 failed" in data["html"]
    assert "tool-output-mixed-remainder is-warning" in data["html"]
    assert "Tool loop" in data["html"]
    assert "repeated_exact_failure_warning" in data["html"]
    assert '&quot;output&quot;' not in data["html"]
    assert r"/throw new Error\(" in data["model"]["output"]["text"]
    assert r"\n    config" in data["model"]["output"]["text"]
    assert "ERR_ASSERTION" in data["html"]
    assert "writePayloadSeoSource" in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_json_envelope_parser_only_accepts_a_complete_delimited_prefix():
    """Mixed-result parsing must stay strict and preserve every source byte."""
    payload = {
        "output": r"literal \n · regex /\s*/ · C:\Users\Harry\project",
        "nested": {"message": "line one\nline two", "items": ["}", "]", '\\"']},
    }
    exact = json.dumps(payload, separators=(",", ":"))
    suffix = "[Tool loop warning: retry with a different strategy.]"
    mixed = exact + "\r\n\r\n" + suffix
    array_json = json.dumps([payload, {"ok": True}], separators=(",", ":"))
    array_mixed = array_json + "\n" + suffix
    script = f"""
{_function(UI, "_toolOutputSafeJsonParse")}
{_function(UI, "_toolOutputSafeJsonEnvelope")}
const exact={json.dumps(exact)};
const mixed={json.dumps(mixed)};
const suffix={json.dumps(suffix)};
const arrayMixed={json.dumps(array_mixed)};
const cases={{
  exact:_toolOutputSafeJsonEnvelope(exact),
  mixed:_toolOutputSafeJsonEnvelope(mixed),
  arrayMixed:_toolOutputSafeJsonEnvelope(arrayMixed),
  glued:_toolOutputSafeJsonEnvelope(exact+'junk'),
  truncated:_toolOutputSafeJsonEnvelope(exact.slice(0,-1)),
  invalid:_toolOutputSafeJsonEnvelope('{{"output":"unterminated}}'),
  plain:_toolOutputSafeJsonEnvelope('prefix '+exact),
}};
process.stdout.write(JSON.stringify({{cases,exact,mixed,suffix,arrayMixed}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["cases"]["exact"]["ok"] is True
    assert data["cases"]["exact"]["value"] == payload
    assert data["cases"]["exact"]["json"] == exact
    assert data["cases"]["exact"]["remainder"] == ""
    assert data["cases"]["mixed"]["ok"] is True
    assert data["cases"]["mixed"]["value"] == payload
    assert data["cases"]["mixed"]["json"] == exact
    assert data["cases"]["mixed"]["remainder"] == "\r\n\r\n" + suffix
    assert data["cases"]["arrayMixed"]["ok"] is True
    assert data["cases"]["arrayMixed"]["value"] == [payload, {"ok": True}]
    assert data["cases"]["arrayMixed"]["remainder"] == "\n" + suffix
    for case in ("glued", "truncated", "invalid", "plain"):
        assert data["cases"][case]["ok"] is False, case
    assert data["exact"] == exact
    assert data["mixed"] == mixed


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_prettier_write_wrapper_promotes_multiline_output_without_test_classification():
    """Prettier timings are shell output, not a test result or JSON preview."""
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    output = "\n".join(
        (
            "static/ui.js 110ms",
            "static/style.css 42ms",
            "tests/test_structured_tool_output_card.py 18ms (unchanged)",
        )
    )
    wrapper = {"output": output}
    raw = json.dumps(wrapper, separators=(",", ":"))
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function li(name){{return `<svg data-icon="${{name}}"></svg>`;}}
function _toolFullCommandLabel(tc){{return tc.args&&tc.args.command||'';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.command||'';}}
function _toolActionKind(){{return 'shell';}}
function _toolActionLabelText(){{return 'Shell command';}}
function _toolDisplayName(){{return 'Shell command';}}
function _toolDisclosureIdentity(){{return '';}}
function _toolChangeSummaryInfo(){{return null;}}
function _toolCommandDiagnosticInfo(){{return null;}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
{_function(UI, '_buildToolStructuredOutputCard')}
{_function(UI, 'buildToolCard')}
global.document={{createElement(){{return {{className:'',dataset:{{}},innerHTML:'',attrs:{{}},setAttribute(k,v){{this.attrs[k]=String(v);}},removeAttribute(k){{delete this.attrs[k];}}}};}}}};
const output={json.dumps(output)};
const raw={json.dumps(raw)};
const cases={{
  serialized:{{name:'terminal',args:{{command:'pnpm exec prettier --write static/ui.js static/style.css'}},result:raw,metadata:{{duration:'170ms'}},snippet:'Completed',done:true,exit_code:0}},
  object:{{name:'terminal',args:{{command:'pnpm exec prettier --write static/ui.js static/style.css'}},output:{{output}},metadata:{{duration:'170ms'}},snippet:'Completed',done:true,exit_code:0}},
  dynamic:{{name:'terminal',args:{{command:'pnpm exec prettier --write static/ui.js static/style.css'}},result:JSON.stringify({{formatted_files:output}}),metadata:{{duration:'170ms'}},snippet:'Completed',done:true,exit_code:0}},
}};
const rendered={{}};
for(const [shape,tc] of Object.entries(cases)){{
  const info=_toolStructuredOutputInfo(tc,'shell');
  const model=_toolOutputDisplayModel(tc,info);
  const row=buildToolCard(tc);
  rendered[shape]={{info,model,html:row.innerHTML,raw:row._toolOutputRaw}};
}}
process.stdout.write(JSON.stringify(rendered));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    for shape, case in data.items():
        assert case["info"]["kind"] == "success", shape
        assert case["model"]["kind"] == "command-result", shape
        assert case["model"]["output"]["text"] == output, shape
        assert "tool-output-test-summary" not in case["html"], shape
        assert 'data-path="static/ui.js"' in case["html"], shape
        assert 'data-path="static/style.css"' in case["html"], shape
        assert 'data-path="tests/test_structured_tool_output_card.py"' in case["html"], shape
        assert "110ms" in case["html"], shape
        assert "42ms" in case["html"], shape
        assert "18ms" in case["html"], shape
        assert case["html"].count("tool-output-line") >= 3, shape
        assert "pnpm exec prettier --write" in case["html"], shape
        assert '&quot;output&quot;' not in case["html"], shape
        assert r"110ms\nstatic/style.css" not in case["html"], shape
    assert data["serialized"]["raw"] == raw
    assert json.loads(data["object"]["raw"]) == wrapper
    assert json.loads(data["dynamic"]["raw"]) == {"formatted_files": output}


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_build_tool_card_routes_read_content_wrapper_to_numbered_source_renderer():
    """Cover live, restored, wrapped, and safely unwrapped read_file paths."""
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    inner = "\n".join(
        (
            "108|    canWrite,",
            "109|    className = '',",
            "110|    locale,",
            "111|}: EventPageEditorProps): React.JSX.Element => {",
            "112|    const { t } = useAdminI18n();",
        )
    )
    wrapper = {"content": inner}
    serialized = json.dumps(wrapper)
    target_path = "src/app/admin/(shell)/events/_components/EventPageEditor/impl.tsx"
    dynamic_matches = "/Users/demo/project/src/a.ts\n  12: first match\n  18: second match"
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function li(name){{return `<svg data-icon="${{name}}"></svg>`;}}
function _toolFullCommandLabel(){{return '';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.path||'';}}
function _toolActionKind(tc){{return tc&&tc.name==='search'?'search':'read';}}
function _toolActionLabelText(){{return 'Read';}}
function _toolDisplayName(){{return 'Read';}}
function _toolDisclosureIdentity(){{return '';}}
function _toolChangeSummaryInfo(){{return null;}}
function _toolCommandDiagnosticInfo(){{return null;}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
{_function(UI, '_buildToolStructuredOutputCard')}
{_function(UI, 'buildToolCard')}
global.document={{createElement(){{return {{className:'',dataset:{{}},innerHTML:'',attrs:{{}},setAttribute(k,v){{this.attrs[k]=String(v);}},removeAttribute(k){{delete this.attrs[k];}}}};}}}};
const inner={json.dumps(inner)};
const wrapper={json.dumps(wrapper)};
const serialized={json.dumps(serialized)};
const targetPath={json.dumps(target_path)};
const dynamicMatches={json.dumps(dynamic_matches)};
const dynamicPayload={{total_count:2,matches_format:'path-grouped matches',matches_text:dynamicMatches}};
const cases={{
  preview:{{name:'read_file',args:{{path:targetPath,offset:108,limit:440}},snippet:serialized,preview:serialized,done:true}},
  object:{{name:'read_file',args:{{path:targetPath,offset:108,limit:440}},result:wrapper,done:true}},
  serialized:{{name:'read_file',args:{{path:targetPath,offset:108,limit:440}},snippet:inner,preview:inner,result:serialized,done:true}},
  unwrapped:{{name:'read_file',args:{{path:targetPath,offset:108,limit:440}},snippet:inner,preview:inner,done:true}},
  dynamic:{{name:'search',snippet:'Search completed',result:dynamicPayload,done:true}},
}};
const output={{}};
for(const [name,tc] of Object.entries(cases)){{
  const row=buildToolCard(tc);
  output[name]={{html:row.innerHTML,attrs:row.attrs,raw:row._toolOutputRaw,content:row._toolOutputContentRaw}};
}}
process.stdout.write(JSON.stringify(output));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    for shape in ("preview", "object", "serialized"):
        case = data[shape]
        assert case["attrs"]["data-tool-structured-output"] == "1", shape
        assert case["attrs"]["data-tool-output-model"] == "content-wrapper", shape
        assert case["content"] == inner, shape
        assert "tool-output-code is-numbered" in case["html"], shape
        assert 'class="language-tsx"' in case["html"], shape
        assert "tool-output-code-line-number" in case["html"], shape
        assert "108|" not in case["html"], shape
        assert "EventPageEditorProps" in case["html"], shape
        assert "View raw output" in case["html"], shape
        assert "Read file" in case["html"] and "completed" in case["html"], shape
        assert "Operation" in case["html"] and "Read file" in case["html"], shape
        assert "Inputs" in case["html"], shape
        assert "Offset" in case["html"] and "Limit" in case["html"], shape
        assert "tool-output-command-prompt" not in case["html"], shape
    assert data["preview"]["raw"] == serialized
    assert data["serialized"]["raw"] == serialized
    assert json.loads(data["object"]["raw"]) == wrapper
    unwrapped = data["unwrapped"]
    assert unwrapped["attrs"]["data-tool-structured-output"] == "1"
    assert unwrapped["attrs"]["data-tool-output-model"] == "code"
    assert unwrapped["raw"] == inner
    assert unwrapped["content"] == ""
    assert "tool-output-code is-numbered" in unwrapped["html"]
    assert 'class="language-tsx"' in unwrapped["html"]
    assert "108|" not in unwrapped["html"]
    assert "EventPageEditorProps" in unwrapped["html"]
    dynamic = data["dynamic"]
    assert dynamic["attrs"]["data-tool-structured-output"] == "1"
    assert dynamic["attrs"]["data-tool-output-model"] == "content-wrapper"
    assert dynamic["content"] == dynamic_matches
    assert "Search" in dynamic["html"]
    assert "completed" in dynamic["html"]
    assert "Total Count" in dynamic["html"]
    assert "  12: first match" in dynamic["html"]
    assert "matches_text" not in dynamic["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_numbered_tool_transcript_recursively_formats_valid_inline_payloads():
    """Numbered content may be a tool transcript rather than source code."""
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    nested_search = json.dumps(
        {
            "total_count": 126,
            "matches_format": "path-grouped",
            "matches_text": "src/app/a.ts\n  48: first match\n  49: second match",
        },
        separators=(",", ":"),
    )
    nested_command = json.dumps(
        {"output": "line one\nline two", "exit_code": 0},
        separators=(",", ":"),
    )
    invalid_nested = '{"total_count":126,"matches_format":"truncated…'
    content = "\n".join(
        (
            f"130|17:39:31 result | search_files ok 0.2s: {nested_search}",
            f"131|17:39:32 result | terminal ok 0.4s: {nested_command}",
            f"132|17:39:33 result | search_files warning 0.1s: {invalid_nested}",
        )
    )
    raw = json.dumps({"content": content}, separators=(",", ":"))
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function li(name){{return `<svg data-icon="${{name}}"></svg>`;}}
function _toolFullCommandLabel(){{return '';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.path||'';}}
function _toolActionKind(){{return 'read';}}
function _toolActionLabelText(){{return 'Read';}}
function _toolDisplayName(){{return 'Read';}}
function _toolDisclosureIdentity(){{return '';}}
function _toolChangeSummaryInfo(){{return null;}}
function _toolCommandDiagnosticInfo(){{return null;}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
{_function(UI, '_buildToolStructuredOutputCard')}
{_function(UI, 'buildToolCard')}
global.document={{createElement(){{return {{className:'',dataset:{{}},innerHTML:'',attrs:{{}},setAttribute(k,v){{this.attrs[k]=String(v);}},removeAttribute(k){{delete this.attrs[k];}}}};}}}};
const raw={json.dumps(raw)};
const content={json.dumps(content)};
const tc={{name:'read_file',args:{{path:'logs/agent-trace.log'}},result:raw,snippet:content,done:true}};
const info=_toolStructuredOutputInfo(tc,'read');
const model=_toolOutputDisplayModel(tc,info);
const row=buildToolCard(tc);
process.stdout.write(JSON.stringify({{info,model,html:row.innerHTML,raw:row._toolOutputRaw}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["model"]["kind"] == "content-wrapper"
    assert data["model"]["content"]["text"] == content
    assert data["raw"] == raw
    assert "tool-output-numbered-transcript" in data["html"]
    assert "tool-output-numbered-record" in data["html"]
    assert "tool-output-record-line-number" in data["html"]
    assert "tool-output-record-tool" in data["html"]
    assert "search_files" in data["html"]
    assert "tool-output-json-tree" in data["html"]
    assert "total_count" in data["html"]
    assert "48: first match" in data["html"]
    assert "line one" in data["html"] and "line two" in data["html"]
    assert "truncated…" in data["html"]
    assert "130|17:39:31" not in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_unwrapped_numbered_read_results_use_path_driven_language_matrix():
    """Language routing must derive from paths, not a TSX-only branch."""
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const cases={{
  python:{{path:'src/check.py',text:'1|def check():\\n2|    return True',language:'python'}},
  rust:{{path:'src/main.rs',text:'1|fn main() {{\\n2|}}',language:'rust'}},
  shell:{{path:'scripts/check.sh',text:'1|#!/usr/bin/env bash\\n2|echo "$HOME"',language:'bash'}},
  powershell:{{path:'scripts/check.ps1',text:'1|param($Path)\\n2|Write-Host $Path',language:'powershell'}},
  json:{{path:'fixtures/value.json',text:'1|{{\\n2|  "ok": true\\n3|}}',language:'json'}},
  markdown:{{path:'docs/result.md',text:'1|# Result\\n2|- item',language:'markdown',kind:'markdown'}},
  vue:{{path:'src/Card.vue',text:'1|<template>\\n2|  <div>Card</div>\\n3|</template>',language:'markup'}},
  cpp:{{path:'src/main.cpp',text:'1|#include <iostream>\\n2|int main() {{ return 0; }}',language:'cpp'}},
  unknown:{{path:'fixtures/result.customext',text:'1|alpha\\n2|  beta',language:''}},
}};
const rendered={{}};
for(const [name,item] of Object.entries(cases)){{
  const tc={{name:'read_file',args:{{path:item.path}},snippet:item.text,done:true}};
  const info={{raw:item.text,kind:'raw',toolKind:'read',executionStatus:'completed'}};
  const model=_toolOutputDisplayModel(tc,info);
  rendered[name]={{model,html:_toolOutputDisplayModelHtml(model),expected:item.language}};
}}
process.stdout.write(JSON.stringify(rendered));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    for name, case in data.items():
        expected_kind = "markdown" if name == "markdown" else "code"
        assert case["model"]["kind"] == expected_kind, name
        if expected_kind == "code":
            assert "tool-output-code is-numbered" in case["html"], name
            assert "1|" not in case["html"], name
        if case["expected"]:
            assert f'class="language-{case["expected"]}"' in case["html"], name
        else:
            assert 'data-language=""' in case["html"], name


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_patch_failures_and_browser_results_get_dedicated_display_models():
    patch_raw = "\n".join(
        (
            "Patch validation failed",
            "file src/a.ts: hunk 5 no matching context found",
            "Hunk 7 expected context was not unique",
            "Found 12 matching lines",
            "file src/b.ts: hunk 2 not found",
        )
    )
    browser_raw = json.dumps(
        {
            "success": True,
            "url": "http://localhost:3000/page",
            "title": "DirectCloud",
            "stealth_warning": "Running without residential proxies",
            "snapshot": "<main>\n  button Open\n</main>",
        }
    )
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const patchRaw={json.dumps(patch_raw)};
const browserRaw={json.dumps(browser_raw)};
const patch=_toolOutputDisplayModel({{name:'apply_patch'}},{{raw:patchRaw,kind:'error'}});
const browser=_toolOutputDisplayModel({{name:'browser_inspect'}},{{raw:browserRaw,kind:'success'}});
process.stdout.write(JSON.stringify({{
  patch,browser,
  patchHtml:_toolOutputDisplayModelHtml(patch),
  browserHtml:_toolOutputDisplayModelHtml(browser)
}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["patch"]["kind"] == "patch-failure"
    assert [item["path"] for item in data["patch"]["files"]] == ["src/a.ts", "src/b.ts"]
    assert [hunk["number"] for hunk in data["patch"]["files"][0]["hunks"]] == [5, 7]
    assert data["browser"]["kind"] == "browser-result"
    assert data["browser"]["metadata"]["title"] == "DirectCloud"
    assert data["browser"]["snapshot"]["text"] == "<main>\n  button Open\n</main>"
    assert "tool-output-patch-file" in data["patchHtml"]
    assert "Hunk 5" in data["patchHtml"]
    assert "tool-output-browser-metadata" in data["browserHtml"]
    assert "Page snapshot" in data["browserHtml"]
    assert "<main>" not in data["browserHtml"]
    assert "&lt;main&gt;" in data["browserHtml"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_large_and_circular_structures_are_bounded_in_the_beautified_view():
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const large=Array.from({{length:1000}},(_,index)=>({{index,value:`item-${{index}}`}}));
const circular={{name:'root'}};circular.self=circular;
const largeHtml=_toolOutputJsonHtml(large);
const circularModel=_toolOutputDisplayModel({{name:'inspect',metadata:{{content:circular,format:'json'}}}},{{raw:'structured result'}});
const circularHtml=_toolOutputDisplayModelHtml(circularModel);
process.stdout.write(JSON.stringify({{largeHtml,circularHtml}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert "800 more; view raw output" in data["largeHtml"]
    assert data["largeHtml"].count("tool-output-json-entry") <= 600
    assert "[Circular]" in data["circularHtml"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_nested_serialized_json_and_numbered_source_expand_recursively():
    numbered = "1|import assert from 'node:assert/strict';\n2|import fs from 'node:fs';\n3|export default fs;"
    nested = json.dumps({"status": "ok", "content": numbered})
    raw = json.dumps({"content": nested, "request_id": "abc-123"})
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const model=_toolOutputDisplayModel({{name:'read_file'}},{{raw}});
process.stdout.write(JSON.stringify({{model,html:_toolOutputDisplayModelHtml(model)}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["model"]["kind"] == "content-wrapper"
    assert data["model"]["content"]["kind"] == "json"
    assert data["model"]["raw"] == raw
    assert data["model"]["content"]["raw"] == nested
    assert "tool-output-nested" in data["html"]
    assert "Nested JSON" in data["html"]
    assert "tool-output-code-line-number" in data["html"]
    assert "language-javascript" in data["html"]
    assert "1|import" not in data["html"]
    assert "node:assert/strict" in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_nonstandard_json_like_text_falls_back_exactly_without_quote_rewriting():
    raw = r"{'pattern': '\\d+\\.\\d+', 'windowsPath': 'C:\\Users\\Harry', 'literal': '\\n', 'html': '<script>x</script>'"
    structured = {
        "pattern": r"\d+\.\d+",
        "windowsPath": r"C:\Users\Harry",
        "literal": r"\n",
        "html": "<script>x</script>",
    }
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const model=_toolOutputDisplayModel({{name:'inspect'}},{{raw}});
const structured={json.dumps(structured)};
const metadataModel=_toolOutputDisplayModel({{name:'inspect',result_metadata:{{data:structured}}}},{{raw,metadata:{{data:structured}}}});
process.stdout.write(JSON.stringify({{model,metadataModel,html:_toolOutputDisplayModelHtml(model),metadataHtml:_toolOutputDisplayModelHtml(metadataModel),raw}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["model"]["kind"] == "text"
    assert data["model"]["text"] == raw
    assert "tool-output-json-tree" not in data["html"]
    assert "<script>" not in data["html"]
    assert "&lt;script&gt;" in data["html"]
    assert data["metadataModel"]["kind"] == "content-wrapper"
    assert data["metadataModel"]["metadata"]["literal"] == r"\n"
    assert data["metadataModel"]["metadata"]["windowsPath"] == r"C:\Users\Harry"
    assert data["metadataModel"]["content"]["text"] == "<script>x</script>"
    assert "tool-output-wrapper-metadata" in data["metadataHtml"]
    assert "<script>" not in data["metadataHtml"]
    assert "&lt;script&gt;" in data["metadataHtml"]
    assert data["raw"] == raw


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_plain_web_url_is_target_url_not_shell_command_and_browser_card_is_named():
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    raw = "http://localhost:6011/directcloud"
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function _toolFullCommandLabel(){{return '';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.url||'';}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const tc={{name:'browser_navigate',args:{{url:'http://localhost:6011/directcloud'}},snippet:raw,done:true}};
const info=_toolStructuredOutputInfo(tc,'web');
const model=_toolOutputDisplayModel(tc,info);
process.stdout.write(JSON.stringify({{info,model,html:_toolOutputDisplayModelHtml(model)}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    info = data["info"]
    assert info["command"] == ""
    assert info["url"] == "http://localhost:6011/directcloud"
    assert info["label"] == "Browser result"
    assert info["title"] == "Browser check completed"
    assert data["model"]["kind"] == "browser-result"
    assert data["model"]["metadata"]["url"] == "http://localhost:6011/directcloud"
    assert "tool-output-command-block" not in data["html"]
    assert data["html"].count('class="tool-output-url"') == 1
    assert "Page snapshot" not in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_browser_result_formats_nested_snapshot_without_duplicate_serialized_payload():
    snapshot = {
        "ready": "complete",
        "rows": [
            {"name": "Documents", "buttons": [{"text": "Open", "enabled": True}]},
            {"name": "Images", "buttons": []},
        ],
    }
    raw = json.dumps(
        {
            "success": True,
            "url": "http://localhost:6011/directcloud",
            "title": "DirectCloud",
            "warning": "Running without residential proxies",
            "snapshot": json.dumps(snapshot),
        },
        separators=(",", ":"),
    )
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const model=_toolOutputDisplayModel({{name:'browser_inspect'}},{{raw,kind:'success'}});
const html=_toolOutputDisplayModelHtml(model);
process.stdout.write(JSON.stringify({{model,html,raw}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["model"]["kind"] == "browser-result"
    assert data["model"]["snapshot"]["kind"] == "json"
    assert data["model"]["snapshot"]["value"] == snapshot
    assert "URL" in data["html"]
    assert "Title" in data["html"]
    assert "Warning" in data["html"]
    assert "Page snapshot" in data["html"]
    assert "tool-output-json-tree" in data["html"]
    assert data["html"].count('class="tool-output-url"') == 1
    assert raw not in data["html"]
    assert data["raw"] == raw


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_shell_command_returning_browser_shaped_json_remains_a_command_result():
    raw = json.dumps(
        {
            "success": True,
            "url": "http://localhost:6011/directcloud",
            "title": "DirectCloud",
            "snapshot": {"ready": "complete"},
        }
    )
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const model=_toolOutputDisplayModel({{name:'terminal'}},{{raw,kind:'success',toolKind:'shell',command:'curl -sS http://localhost:6011/directcloud'}});
process.stdout.write(JSON.stringify({{model,html:_toolOutputDisplayModelHtml(model)}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["model"]["kind"] == "command-result"
    assert data["model"]["command"].startswith("curl -sS")
    assert "tool-output-command-block" in data["html"]
    assert "Page snapshot" not in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_browser_geometry_uses_structured_metadata_for_summary_and_collapsible_rows():
    raw = "{'errors': 0, 'overflow': 8, 'ready': 'complete', 'rows': [...]}"
    geometry = {
        "errors": 0,
        "overflow": 8,
        "ready": "complete",
        "rows": [
            {"x": 10, "y": 20, "width": 320, "height": 48, "buttons": ["Open"]},
            {"x": 10, "y": 76, "width": 320, "height": 48, "buttons": ["Remove"]},
        ],
    }
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const data={json.dumps(geometry)};
const tc={{name:'browser_geometry',result_metadata:{{output_kind:'page_snapshot',data}}}};
const model=_toolOutputDisplayModel(tc,{{raw,kind:'info',metadata:tc.result_metadata}});
process.stdout.write(JSON.stringify({{model,html:_toolOutputDisplayModelHtml(model),raw}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["model"]["kind"] == "browser-result"
    assert data["model"]["metadata"]["status"] == "Complete"
    assert data["model"]["metadata"]["errors"] == 0
    assert data["model"]["metadata"]["overflow"] == 8
    assert data["model"]["metadata"]["rows"] == 2
    assert data["model"]["snapshot"]["value"] == geometry["rows"]
    assert "Page rows" in data["html"]
    assert "tool-output-json-tree" in data["html"]
    assert raw not in data["html"]
    assert data["raw"] == raw


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_command_summary_deduplicates_stdout_and_http_assignment_is_concise():
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    functions = _structured_formatter_source() + "\n" + _function(UI, "_buildToolStructuredOutputCard")
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function li(name){{return `<svg data-icon="${{name}}"></svg>`;}}
function _toolFullCommandLabel(tc){{return tc.args&&tc.args.cmd||'';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.cmd||'';}}
{detector}
{_classifier_source()}
{functions}
const background='Background process started';
const info={{kind:'success',severity:'success',label:'Success',title:'Command completed',summary:background,raw:background,command:'pnpm dev',toolKind:'shell',exitCode:0,httpStatus:null,counts:{{passed:0,failed:0,skipped:0}},url:'',suggestion:'',expanded:true}};
const row={{innerHTML:'',attrs:{{}},setAttribute(k,v){{this.attrs[k]=v;}}}};
_buildToolStructuredOutputCard(row,{{name:'terminal'}},info);
const storybook=_toolStructuredOutputInfo({{name:'terminal',args:{{cmd:'curl -sS http://localhost:6006'}},snippet:'storybook_http=200',result_metadata:{{exit_code:0}},done:true}},'shell');
const storybookModel=_toolOutputDisplayModel({{name:'terminal'}},storybook);
process.stdout.write(JSON.stringify({{html:row.innerHTML,storybook,storybookModel,storybookHtml:_toolOutputDisplayModelHtml(storybookModel),stored:row._toolOutputRaw}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["html"].count("Background process started") == 1
    assert data["stored"] == "Background process started"
    assert data["storybook"]["label"] == "Completed"
    assert data["storybook"]["summary"] == "Storybook available · HTTP 200"
    assert data["storybook"]["httpStatus"] == 200
    assert data["storybookModel"]["stdout"] == ""
    assert "storybook_http=200" not in data["storybookHtml"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_running_shell_command_uses_the_shared_beautified_command_model():
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    command = "env MODE='dark mode' pnpm test -- --runInBand && printf '%s\\n' \"$HOME/path\" | sed -n '1p'"
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function _toolFullCommandLabel(tc){{return (tc.args&&(tc.args.cmd||tc.args.command))||'';}}
function _toolTargetLabel(tc){{return (tc.args&&(tc.args.cmd||tc.args.command))||'';}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
const command={json.dumps(command)};
const tc={{name:'terminal',args:{{command,workdir:'/tmp/project',timeout:120}},snippet:'',done:false,status:'running',result_metadata:{{shell:'bash'}}}};
const info=_toolStructuredOutputInfo(tc,'shell');
const model=info&&_toolOutputDisplayModel(tc,info);
const html=model&&_toolOutputDisplayModelHtml(model);
process.stdout.write(JSON.stringify({{info,model,html}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["info"]["executionStatus"] == "running"
    assert data["info"]["label"] == "Running"
    assert data["model"]["kind"] == "command-result"
    assert data["model"]["command"] == command
    assert data["model"]["status"] == "running"
    assert "tool-output-command-block" in data["html"]
    assert 'class="tool-output-command-prompt"' in data["html"]
    assert "Copy command" in data["html"]
    assert "language-bash" in data["html"]
    assert "Working Directory" in data["html"]
    assert "/tmp/project" in data["html"]
    assert "Timeout" in data["html"]
    assert "120" in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_completed_execute_code_preserves_and_renders_the_original_code_invocation():
    """`execute_code` is shell-like, but its invocation lives in args.code."""
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    code = "\n".join(
        (
            "from hermes_tools import read_file, write_file",
            "path = '/tmp/example.tsx'",
            "content = read_file(path)['content']",
            "result = write_file(path, content)",
            "print({'written': result})",
        )
    )
    output = "{'start': 12773, 'end': 20857, 'written': {'bytes_written': 37823}}"
    script_text = "set -e\nprintf '%s\\n' \"$HOME\""
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function _toolFullCommandLabel(tc){{return (tc.args&&(tc.args.cmd||tc.args.command))||'';}}
function _toolTargetLabel(){{return '';}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
const code={json.dumps(code)};
const output={json.dumps(output)};
const tc={{name:'execute_code',args:{{code}},snippet:output,done:true}};
const info=_toolStructuredOutputInfo(tc,'shell');
const model=_toolOutputDisplayModel(tc,info);
const html=_toolOutputDisplayModelHtml(model);
const runningTc={{name:'execute_code',args:{{code}},snippet:'',done:false,status:'running'}};
const runningInfo=_toolStructuredOutputInfo(runningTc,'shell');
const runningModel=_toolOutputDisplayModel(runningTc,runningInfo);
const scriptText={json.dumps(script_text)};
const scriptTc={{name:'run_script',args:{{script:scriptText,language:'bash'}},snippet:'done',done:true}};
const scriptInfo=_toolStructuredOutputInfo(scriptTc,'shell');
const scriptModel=_toolOutputDisplayModel(scriptTc,scriptInfo);
process.stdout.write(JSON.stringify({{info,model,html,running:{{info:runningInfo,model:runningModel,html:_toolOutputDisplayModelHtml(runningModel)}},script:{{info:scriptInfo,model:scriptModel,html:_toolOutputDisplayModelHtml(scriptModel),text:scriptText}}}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["info"]["title"] == "Code execution"
    assert data["info"]["invocation"]["kind"] == "code"
    assert data["info"]["invocation"]["value"] == code
    assert data["model"]["kind"] == "command-result"
    assert data["model"]["invocation"]["value"] == code
    assert "Copy code" in data["html"]
    assert 'language-python' in data["html"]
    assert "from hermes_tools import" in data["html"]
    assert "tool-output-command-prompt" not in data["html"]
    assert data["running"]["info"]["executionStatus"] == "running"
    assert data["running"]["model"]["invocation"]["value"] == code
    assert "Copy code" in data["running"]["html"]
    assert data["script"]["info"]["title"] == "Script execution"
    assert data["script"]["model"]["invocation"]["value"] == data["script"]["text"]
    assert data["script"]["model"]["invocation"]["language"] == "bash"
    assert "Copy script" in data["script"]["html"]
    assert "tool-output-command-prompt" not in data["script"]["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_build_tool_card_routes_running_shell_directly_to_structured_renderer():
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    command = "FOO='a b' pnpm build && git status --short"
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function li(name){{return `<svg data-icon="${{name}}"></svg>`;}}
function _toolFullCommandLabel(tc){{return (tc.args&&(tc.args.cmd||tc.args.command))||'';}}
function _toolTargetLabel(tc){{return (tc.args&&(tc.args.cmd||tc.args.command))||'';}}
function _toolActionKind(){{return 'shell';}}
function _toolActionLabelText(){{return 'Shell';}}
function _toolDisplayName(){{return 'Shell';}}
function _toolDisclosureIdentity(){{return '';}}
function _toolChangeSummaryInfo(){{return null;}}
function _toolCommandDiagnosticInfo(){{return null;}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
{_function(UI, '_buildToolStructuredOutputCard')}
{_function(UI, 'buildToolCard')}
const attrs={{}};
global.document={{createElement(){{return {{className:'',dataset:{{}},innerHTML:'',setAttribute(k,v){{attrs[k]=String(v);}},removeAttribute(k){{delete attrs[k];}}}};}}}};
const command={json.dumps(command)};
const row=buildToolCard({{name:'terminal',args:{{command}},snippet:'',done:false,status:'running',result_metadata:{{shell:'bash'}}}});
process.stdout.write(JSON.stringify({{html:row.innerHTML,attrs,command:row._toolOutputCommand}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["attrs"]["data-tool-structured-output"] == "1"
    assert data["attrs"]["data-tool-output-model"] == "command-result"
    assert data["attrs"]["data-tool-execution-status"] == "running"
    assert data["command"] == command
    assert "tool-output-command-block" in data["html"]
    assert "tool-card-detail-lead" not in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_shell_execution_states_share_one_command_layout():
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    command = "printf '%s\\n' \"$VALUE\" | sed -n '1p'"
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function _toolFullCommandLabel(tc){{return (tc.args&&(tc.args.cmd||tc.args.command))||'';}}
function _toolTargetLabel(tc){{return (tc.args&&(tc.args.cmd||tc.args.command))||'';}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
const command={json.dumps(command)};
const inputs={{
  running:{{done:false,status:'running'}},
  completed:{{done:true,status:'running',result_metadata:{{exit_code:0}}}},
  failed:{{done:true,is_error:true,result_metadata:{{exit_code:7}}}},
  cancelled:{{done:true,status:'cancelled'}},
}};
const values={{}};
for(const [key,state] of Object.entries(inputs)){{
  const tc={{name:'terminal',args:{{command}},snippet:key==='running'?'':`${{key}} output`,...state}};
  const info=_toolStructuredOutputInfo(tc,'shell');
  const model=_toolOutputDisplayModel(tc,info);
  values[key]={{status:info.executionStatus,label:info.label,title:info.title,kind:model.kind,modelStatus:model.status,command:model.command,html:_toolOutputDisplayModelHtml(model)}};
}}
process.stdout.write(JSON.stringify(values));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    values = json.loads(result.stdout)
    assert [values[key]["status"] for key in ("running", "completed", "failed", "cancelled")] == [
        "running",
        "completed",
        "failed",
        "cancelled",
    ]
    for key, value in values.items():
        assert value["label"] == key.capitalize()
        assert value["title"] == "Shell command"
        assert value["kind"] == "command-result"
        assert value["modelStatus"] == key
        assert value["command"] == command
        assert "tool-output-command-block" in value["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_copy_command_excludes_the_decorative_prompt_and_preserves_exact_text():
    copy_function = _function(UI, "copyToolStructuredOutputCommand")
    command = "A='x y' printf '%s\\n' \"$A\" | sed -n '1p'"
    script = f"""
let copied=null;
function _copyText(value){{copied=value;return Promise.resolve();}}
function setTimeout(){{}}
{copy_function}
const row={{_toolOutputCommand:{json.dumps(command)}}};
const button={{textContent:'Copy command',closest(){{return row;}}}};
copyToolStructuredOutputCommand(button);
Promise.resolve().then(()=>process.stdout.write(JSON.stringify({{copied}})));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["copied"] == command


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_live_completion_reuses_the_already_highlighted_command_subtree():
    preserve = _function(UI, "_preserveLiveStructuredCommandPresentation")
    script = f"""
{preserve}
const moved=[];
const oldSection={{id:'already-highlighted'}};
const nextSection={{replaceWith(value){{moved.push(value);}}}};
const make=(command,language,section)=>({{
  _toolOutputCommand:command,
  _toolOutputCommandLanguage:language,
  getAttribute(name){{return name==='data-tool-output-model'?'command-result':'';}},
  querySelector(selector){{return selector==='.tool-output-terminal-section.is-command'?section:null;}},
}});
const existing=make('pnpm build','bash',oldSection);
const replacement=make('pnpm build','bash',nextSection);
const reused=_preserveLiveStructuredCommandPresentation(existing,replacement);
const changed=_preserveLiveStructuredCommandPresentation(existing,make('pnpm test','bash',nextSection));
process.stdout.write(JSON.stringify({{reused,changed,moved:moved.map(value=>value.id)}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"reused": True, "changed": False, "moved": ["already-highlighted"]}


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_result_card_rebuild_restores_nested_scroll_and_disclosure_state():
    """The shared rebuild lifecycle covers terminal, raw, and nested details."""
    names = (
        "_captureToolResultScrollPosition",
        "_restoreToolResultScrollPosition",
        "_toolResultOwnerKey",
        "_toolResultElementRole",
        "_toolResultScrollKeyForElement",
        "_toolResultDisclosureKeyForElement",
        "_captureToolResultPresentationState",
        "_bindToolResultUserScrollIntent",
        "_toolPresentationRestoreGuardAllowsDisclosure",
        "_toolPresentationRestoreGuardAllowsScroll",
        "_restoreToolResultPresentationState",
    )
    source = "\n".join(_function(UI, name) for name in names)
    script = f"""
const _toolResultScrollableSelector='tool-scroll';
const _toolResultDisclosureSelector='tool-details';
function _worklogDetailTextKey(value){{return String(value||'').trim();}}
{source}
function classes(...values){{values.contains=value=>values.includes(value);return values;}}
function row(raw){{
  return{{
    _toolOutputRaw:raw,
    dataset:{{toolName:'shell'}},
    getAttribute(name){{return name==='data-live-tid'?'tool-1':'';}},
    querySelector(selector){{return selector==='.tool-output-raw-toggle'?this.toggle:null;}},
  }};
}}
function terminal(owner,top,height){{
  const section={{getAttribute(name){{return name==='data-tool-output-stream'?'stdout':'';}}}};
  return{{
    classList:classes('tool-output-terminal-output'),scrollTop:top,scrollLeft:7,
    scrollHeight:height,clientHeight:100,scrollWidth:500,clientWidth:250,
    closest(selector){{if(selector==='[data-tool-output-stream]')return section;return owner;}},
  }};
}}
function raw(owner,top,height,hidden){{
  const code={{textContent:'stale'}};
  return{{
    classList:classes('tool-output-raw'),scrollTop:top,scrollLeft:3,
    scrollHeight:height,clientHeight:100,scrollWidth:400,clientWidth:220,
    hidden,dataset:{{loaded:hidden?'':'1'}},
    closest(){{return owner;}},querySelector(selector){{return selector==='code'?code:null;}},
    removeAttribute(name){{if(name==='data-loaded')delete this.dataset.loaded;}},code,
  }};
}}
function details(owner,open){{
  return{{classList:classes('tool-output-content-document'),open,closest(){{return owner;}}}};
}}
function root(scrollers,disclosures){{
  return{{querySelectorAll(selector){{return selector===_toolResultScrollableSelector?scrollers:selector===_toolResultDisclosureSelector?disclosures:[];}}}};
}}
const oldRow=row('old raw');
const oldTerminal=terminal(oldRow,120,400);
const oldRaw=raw(oldRow,80,300,false);
const state=_captureToolResultPresentationState(root([oldTerminal,oldRaw],[details(oldRow,true)]));
const nextRow=row('new exact raw');
nextRow.toggle={{textContent:'View raw output',setAttribute(name,value){{this[name]=value;}}}};
const nextTerminal=terminal(nextRow,0,500);
const nextRaw=raw(nextRow,0,400,true);
const nextDetails=details(nextRow,false);
_restoreToolResultPresentationState(root([nextTerminal,nextRaw],[nextDetails]),state);
process.stdout.write(JSON.stringify({{
  terminalTop:nextTerminal.scrollTop,
  terminalLeft:nextTerminal.scrollLeft,
  rawTop:nextRaw.scrollTop,
  rawHidden:nextRaw.hidden,
  rawText:nextRaw.code.textContent,
  rawLoaded:nextRaw.dataset.loaded,
  detailsOpen:nextDetails.open,
  toggleText:nextRow.toggle.textContent,
}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "terminalTop": 120,
        "terminalLeft": 7,
        "rawTop": 80,
        "rawHidden": False,
        "rawText": "new exact raw",
        "rawLoaded": "1",
        "detailsOpen": True,
        "toggleText": "Hide raw output",
    }
    for selector in (
        ".tool-output-formatted",
        ".tool-output-raw",
        ".tool-output-terminal-output",
        ".tool-output-content-body",
        ".tool-diagnostic-section > pre",
        ".tool-diagnostic-raw > pre",
    ):
        assert selector in UI


def test_live_command_completion_reconciles_in_place_with_stateful_fallback():
    append_card = _function(UI, "appendLiveToolCard")
    fallback = _function(UI, "_replaceLiveToolCardPreservingResultState")
    assert "_reconcileLiveStructuredCommandCard(existing,replacement)" in append_card
    assert "_replaceLiveToolCardPreservingResultState(existing,replacement)" in append_card
    assert "existing.replaceWith(replacement)" not in append_card
    assert "_reconcileLiveToolResultCardInPlace(existing,replacement)" in fallback


def test_streaming_scene_preserves_every_nested_tool_result_viewport_after_layout_settles():
    """New activity must not reset nested Read/Shell/Run/Raw scroll panes.

    These panes are rebuilt by the anchor-scene renderer, not only by the
    direct tool-completion reconciler.  Capture every currently scrollable
    result variant and use the shared two-phase restore so late Prism/layout
    work cannot clamp the freshly restored viewport back to the top.
    """
    for selector in (
        ".tool-card-detail",
        ".tool-output-text",
        ".tool-output-test-log",
        ".tool-output-test-failure-log",
        ".tool-output-record-payload",
        ".tool-output-numbered-record > pre",
        ".tool-card-result pre",
    ):
        assert selector in UI

    compact_scene = _function(UI, "renderLiveAnchorActivityScene")
    transparent_refresh = _function(UI, "_refreshTransparentLiveRow")
    assert "_restoreLiveToolPresentation(blocks,liveDisclosureState)" in compact_scene.replace(" ", "")
    assert "_restoreLiveToolPresentation(existing,nestedDisclosureState)" in transparent_refresh.replace(" ", "")
    assert "_captureLiveAnchorToolRows(blocks,activeAnchorStreamId)" in compact_scene.replace(" ", "")
    assert "_reuseLiveAnchorToolRows(group,preservedToolRows,activeAnchorStreamId)" in compact_scene.replace(" ", "")
    assert "_reconcileLiveToolResultCardInPlace(existing,node)" in transparent_refresh.replace(" ", "")
    # tool_started -> tool_completed is one stable anchor row lifecycle, not a
    # reason to replace the result card and lose browser-owned scroll state.
    assert "data-anchor-source-event-type" not in _function(UI, "_liveAnchorToolRowKey")
    assert "data-anchor-source-event-type" not in _function(UI, "_transparentLiveRowKey")


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_large_browser_geometry_rows_are_counted_and_bounded():
    rows = [
        {"x": index, "y": index * 4, "width": 320, "height": 40, "buttons": [f"Open {index}"]}
        for index in range(250)
    ]
    raw = "{'errors': 0, 'overflow': 8, 'ready': 'complete', 'rows': [...]}"
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const rows={json.dumps(rows)};
const metadata={{output_kind:'page_snapshot',data:{{errors:0,overflow:8,ready:'complete',rows}}}};
const model=_toolOutputDisplayModel({{name:'browser_geometry',result_metadata:metadata}},{{raw,kind:'info',metadata}});
const html=_toolOutputDisplayModelHtml(model);
process.stdout.write(JSON.stringify({{model,html}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["model"]["metadata"]["rows"] == 250
    assert data["model"]["snapshot"]["value"] == rows
    assert "Page rows" in data["html"]
    assert "50 more; view raw output" in data["html"]
    assert data["html"].count("tool-output-json-entry") <= 600


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_skill_result_projects_metadata_and_markdown_content_without_dense_json():
    markdown = r"""---
name: figma-ui-implementation
description: Implement UI pages from Figma designs in Vue/React/Next apps.
---

# Figma UI Implementation

Use this skill when turning a Figma design into a web UI page/component.

## Core workflow

1. Gather source-of-truth before coding.
   - Fetch the exact Figma node.
   - Keep inline `code` readable.

```tsx
<Button onClick={() => alert('safe')}>Open</Button>
```

Pattern: \d+\.\d+
Windows: C:\Users\Harry\project
Literal escape: \n
<script>alert('never execute')</script>
"""
    payload = {
        "success": True,
        "name": "figma-ui-implementation",
        "description": "Implement UI pages from Figma designs in Vue/React/Next apps.",
        "tags": [],
        "related_skills": [],
        "content": markdown,
    }
    raw = json.dumps(payload, separators=(",", ":"))
    detector = _function(WORKSPACE, "_workspaceDiffLooksUseful")
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function _toolFullCommandLabel(){{return '';}}
function _toolTargetLabel(tc){{return tc.args&&tc.args.name||'';}}
{detector}
{_classifier_source()}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const tc={{name:'skill_manage',args:{{name:'figma-ui-implementation'}},snippet:raw,done:true}};
const info=_toolStructuredOutputInfo(tc,'skill');
const model=_toolOutputDisplayModel(tc,info);
const html=_toolOutputDisplayModelHtml(model);
process.stdout.write(JSON.stringify({{info,model,html,raw}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["info"]["label"] == "Success"
    assert data["info"]["title"] == "Skill created"
    assert data["info"]["summary"] == ""
    assert data["model"]["kind"] == "content-wrapper"
    assert "success" not in data["model"]["metadata"]
    assert data["model"]["metadata"]["name"] == "figma-ui-implementation"
    assert data["model"]["content"]["kind"] == "markdown"
    assert data["model"]["content"]["text"] == markdown
    assert data["model"]["raw"] == raw
    assert "Skill content" in data["html"]
    assert 'class="language-markdown"' in data["html"]
    assert data["html"].count("None") == 2
    assert "tool-output-copy-content" in data["html"]
    assert "\\n" in data["html"]
    assert r"\d+\.\d+" in data["html"]
    assert r"C:\Users\Harry\project" in data["html"]
    assert "<script>" not in data["html"]
    assert "&lt;script&gt;" in data["html"]
    assert raw not in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_stdout_wrapped_structured_skill_result_is_unwrapped_once_not_rendered_as_stdout():
    markdown = "---\nname: reusable-skill\n---\n\n# Reusable skill\n\n- Preserve lists\n- Preserve `code`\n"
    payload = {
        "success": True,
        "name": "reusable-skill",
        "description": "A reusable structured result",
        "tags": [],
        "related_skills": [],
        "content": markdown,
    }
    inner = json.dumps(payload, separators=(",", ":"))
    raw = json.dumps({"stdout": inner, "exit_code": 0}, separators=(",", ":"))
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const model=_toolOutputDisplayModel({{name:'terminal'}},{{raw,kind:'success',toolKind:'shell',command:'hermes skill create',summary:'Skill created',exitCode:0}});
const html=_toolOutputDisplayModelHtml(model);
process.stdout.write(JSON.stringify({{model,html,raw}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["model"]["kind"] == "content-wrapper"
    assert data["model"]["content"]["kind"] == "markdown"
    assert data["model"]["content"]["text"] == markdown
    assert data["model"]["raw"] == raw
    assert "Stdout" not in data["html"]
    assert "tool-output-command-block" not in data["html"]
    assert inner not in data["html"]
    assert "Skill content" in data["html"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_large_structured_string_fields_use_independent_content_classification():
    markdown = "---\ntitle: Generic document\n---\n\n# Heading\n\n1. First\n2. Second\n"
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const markdown={json.dumps(markdown)};
const models={{}};
for(const field of ['content','markdown','document','body','source']){{
  const value={{name:`${{field}}-result`,[field]:markdown}};
  const raw=JSON.stringify(value);
  models[field]=_toolOutputDisplayModel({{name:'inspect'}},{{raw}});
}}
process.stdout.write(JSON.stringify(models));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    models = json.loads(result.stdout)
    for field, model in models.items():
        assert model["kind"] == "content-wrapper", field
        assert model["content"]["kind"] == "markdown", field
        assert model["content"]["text"] == markdown, field


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_shell_command_uses_prism_markup_and_preserves_command_text():
    command = "for url in \"$urlA\" \"$urlB\"; do\n  code=$(curl -sS -L -o /dev/null -w '%{http_code}' \"$url\")\n  printf '%s %s\\n' \"$code\" \"$url\"\ndone"
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_function(UI, '_toolOutputPrismLanguage')}
{_function(UI, '_toolOutputCommandHtml')}
const command={json.dumps(command)};
process.stdout.write(JSON.stringify({{html:_toolOutputCommandHtml(command,'bash'),command}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert 'class="tool-output-command-prompt"' in data["html"]
    assert 'class="language-bash"' in data["html"]
    assert "data-tool-output-prism" in data["html"]
    assert "$urlA" in data["html"]
    assert "curl -sS -L" in data["html"]
    assert data["command"] == command


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_ansi_sgr_is_safely_rendered_without_visible_escape_sequences():
    raw = "\x1b[32mBuild succeeded\x1b[0m\n\x1b[33;1m2 warnings\x1b[0m\n\x1b]0;hostile title\x07done\n\x1b]8;unterminated link\nstill safe"
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_function(UI, '_toolOutputAnsiHtml')}
const raw={json.dumps(raw)};
process.stdout.write(JSON.stringify({{rendered:_toolOutputAnsiHtml(raw),raw}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["rendered"]["hasAnsi"] is True
    assert "tool-output-ansi-fg-green" in data["rendered"]["html"]
    assert "tool-output-ansi-fg-yellow" in data["rendered"]["html"]
    assert "tool-output-ansi-bold" in data["rendered"]["html"]
    assert "Build succeeded" in data["rendered"]["html"]
    assert "2 warnings" in data["rendered"]["html"]
    assert "hostile title" not in data["rendered"]["html"]
    assert "unterminated link" not in data["rendered"]["html"]
    assert "still safe" in data["rendered"]["html"]
    assert "\x1b" not in data["rendered"]["html"]
    assert data["raw"] == raw


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_shell_result_separates_command_stdout_stderr_status_and_metadata():
    raw = "\x1b[32mBuild succeeded\x1b[0m"
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
{_structured_formatter_source()}
const raw={json.dumps(raw)};
const tc={{name:'terminal',metadata:{{stdout:raw,stderr:'warning: cache stale',cwd:'/tmp/project',duration:1.25,shell:'bash'}}}};
const info={{raw,kind:'success',command:'pnpm build && git status --short',exitCode:0,metadata:tc.metadata}};
const model=_toolOutputDisplayModel(tc,info);
process.stdout.write(JSON.stringify({{model,html:_toolOutputDisplayModelHtml(model)}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["model"]["kind"] == "command-result"
    assert data["model"]["command"] == "pnpm build && git status --short"
    assert data["model"]["stdout"] == raw
    assert data["model"]["stderr"] == "warning: cache stale"
    assert data["model"]["metadata"]["cwd"] == "/tmp/project"
    assert "tool-output-command-block" in data["html"]
    assert "Stdout" in data["html"]
    assert "Stderr" in data["html"]
    assert "Exit 0" in data["html"]
    assert "Working Directory" in data["html"]


def test_structured_formatter_contract_preserves_raw_copy_and_live_diff_isolation():
    builder = _function(UI, "_buildToolStructuredOutputCard")
    copy_raw = _function(UI, "copyToolStructuredOutputRaw")
    copy_content = _function(UI, "copyToolStructuredOutputContent")
    copy_command = _function(UI, "copyToolStructuredOutputCommand")
    highlighter = _function(UI, "_toolOutputHighlightSettled")
    ansi = _function(UI, "_toolOutputAnsiHtml")
    color_diff = _function(UI, "_colorDiffLines")
    diff_detector = _function(UI, "_snippetLooksLikeDiff")
    assert "_toolOutputDisplayModel" in builder
    assert "_toolOutputDisplayModelHtml" in builder
    assert "Copy raw" in builder
    assert "row._toolOutputRaw" in copy_raw
    assert "_copyText" in copy_raw
    assert "row._toolOutputContentRaw" in copy_content
    assert "_copyText" in copy_content
    assert "row._toolOutputCommand" in copy_command
    assert "_copyText" in copy_command
    assert "Prism.highlightElement" in highlighter
    assert "data-tool-output-command" in highlighter
    assert "_toolOutputAnsiHtml" not in highlighter
    assert "Prism" not in ansi
    assert "_toolOutputDisplayModel" not in color_diff
    assert "_toolOutputDisplayModel" not in diff_detector
    assert "_toolOutputAnsiHtml" not in color_diff
    assert "_toolOutputAnsiHtml" not in diff_detector
    assert ".tool-output-json-key" in STYLE
    assert ".tool-output-code" in STYLE
    assert ".tool-output-browser-snapshot" in STYLE
    assert ".tool-output-patch-file" in STYLE
    assert ".tool-output-command-block" in STYLE
    assert ".tool-output-terminal-output" in STYLE
    assert ".tool-output-copy-command" in STYLE
    assert ".tool-output-badge.is-execution.is-running" in STYLE
    assert ".tool-output-ansi-fg-green" in STYLE
    assert 'pre.tool-output-code[class*="language-"]' in STYLE
    assert 'pre.tool-output-command-block[class*="language-"]' in STYLE
    command_rule = STYLE.split(".tool-output-command-block>code{", 1)[1].split("}", 1)[0]
    assert "overflow-wrap:normal" in command_rule
    assert "overflow-wrap:anywhere" not in command_rule


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_structured_repository_metadata_routes_to_one_summary_card():
    functions = "\n".join(
        _function(UI, name)
        for name in (
            "_toolDiagnosticRawOutput",
            "_toolOutputStructuredMetadata",
            "_toolChangeSummaryData",
            "_toolChangeSummaryInfo",
        )
    )
    script = f"""
function _toolFullCommandLabel(){{return '';}}
function _toolTargetLabel(){{return '';}}
function _workspaceDiffLooksUseful(){{return false;}}
function _toolOutputStructuredDocument(){{return null;}}
{functions}
const info=_toolChangeSummaryInfo({{
  name:'repository_check',done:true,snippet:'Repository scan completed',
  result_metadata:{{kind:'repository-summary',title:'Repository Changes',file_count:2,insertions:4,deletions:1,files:[
    {{path:'src/a.ts',added:4,removed:1}},{{path:'config.yaml',added:0,removed:0}}
  ]}}
}},'unknown');
process.stdout.write(JSON.stringify(info));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["title"] == "Repository Changes"
    assert data["file_count"] == 2
    assert data["added"] == 4
    assert data["removed"] == 1
    assert [item["path"] for item in data["files"]] == ["src/a.ts", "config.yaml"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_card_keeps_exact_raw_payload_while_summarizing_repeated_display_lines():
    functions = _structured_formatter_source() + "\n" + _function(UI, "_buildToolStructuredOutputCard")
    raw = "fatal: repository unavailable\nfatal: repository unavailable\nfatal: repository unavailable"
    script = f"""
function esc(value){{return String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');}}
function li(name){{return `<svg data-icon="${{name}}"></svg>`;}}
{functions}
const raw={json.dumps(raw)};
const info={{kind:'error',severity:'error',label:'Error',title:'Command failed',summary:'fatal: repository unavailable',raw,command:'git fetch',exitCode:128,httpStatus:null,counts:{{passed:0,failed:0,skipped:0}},url:'',suggestion:'',expanded:true}};
const row={{innerHTML:'',attrs:{{}},setAttribute(k,v){{this.attrs[k]=v;}}}};
_buildToolStructuredOutputCard(row,{{name:'terminal'}},info);
process.stdout.write(JSON.stringify({{html:row.innerHTML,attrs:row.attrs,stored:row._toolOutputRaw}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["stored"] == raw
    assert data["attrs"]["data-tool-output-kind"] == "error"
    assert "tool-output-card is-error open" in data["html"]
    assert "Exit 128" in data["html"]
    assert "Repeated 3 times" in data["html"]
    assert "View raw output" in data["html"]
    assert "assistant-code-diff" not in data["html"]


def test_structured_output_card_contract_is_isolated_from_live_diff_renderer():
    build = _function(UI, "buildToolCard")
    color_diff = _function(UI, "_colorDiffLines")
    diff_detector = _function(UI, "_snippetLooksLikeDiff")
    assert "_toolStructuredOutputInfo" in build
    assert build.index("_toolChangeSummaryInfo") < build.index("_toolStructuredOutputInfo")
    assert "_toolOutputTokenHtml" not in color_diff
    assert "_toolOutputTokenHtml" not in diff_detector
    assert "data-tool-output-kind" in _function(UI, "_buildToolStructuredOutputCard")
    assert "View raw output" in _function(UI, "_buildToolStructuredOutputCard")
    assert ".tool-output-card.is-error" in STYLE
    assert ".tool-output-card.is-warning" in STYLE
    assert ".tool-output-card.is-success" in STYLE
    assert "result_metadata" in MESSAGES
    assert "exit_code" in MESSAGES
