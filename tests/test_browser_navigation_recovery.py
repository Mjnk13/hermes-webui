import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _js_function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated JavaScript function: {name}")


def _run_node_json(program: str):
    result = subprocess.run(
        ["node", "-e", program],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_restored_suspended_tab_reload_materializes_from_saved_url():
    js = _read("static/browser_workbench.js")

    assert "function browserWorkbenchCanReload" in js
    assert "workbenchCapabilities.navigation===true&&browserWorkbenchCanReload(active)" in js
    assert "if(normalized==='reload'&&!target.sessionId)" in js
    assert "return navigateBrowserWorkbenchToUrl(target.id,retryUrl)" in js
    assert "record = ensureTab({ ...recoveryPayload, session_id: sessionId })" in _read("desktop/src/main/index.cjs")
    assert "Open a Browser Workbench session before using navigation controls." not in js[js.index("async function navigateBrowserWorkbenchHistory"):js.index("async function maybeStartBrowserWorkbenchInitialLoadOnActivation")]


def test_restored_spa_navigation_uses_latest_committed_url_instead_of_original_request():
    js = _read("static/browser_workbench.js")
    program = "\n".join(
        [
            "const getActiveWorkbenchTab=()=>null;",
            _js_function(js, "browserWorkbenchActivationUrl"),
            _js_function(js, "markBrowserWorkbenchLoadCommitted"),
            "const restored={requestedUrl:'https://example.test/old',url:'https://example.test/latest',currentUrl:'https://example.test/latest',lastLoadedUrl:'https://example.test/old'};",
            "const committed={requestedUrl:'https://example.test/old',url:'https://example.test/latest',currentUrl:'https://example.test/old',lastLoadedUrl:'https://example.test/old'};",
            "const activation=browserWorkbenchActivationUrl(restored);",
            "markBrowserWorkbenchLoadCommitted(committed,'https://example.test/latest');",
            "console.log(JSON.stringify({activation,committed}));",
        ]
    )

    result = _run_node_json(program)
    assert result["activation"] == "https://example.test/latest"
    assert result["committed"]["requestedUrl"] == "https://example.test/latest"
    assert result["committed"]["currentUrl"] == "https://example.test/latest"
    assert result["committed"]["lastLoadedUrl"] == "https://example.test/latest"


def test_restore_migrates_stale_requested_url_to_visible_current_url():
    js = _read("static/browser_workbench.js")
    normalize = _js_function(js, "normalizePersistedTab")
    program = "\n".join(
        [
            "const BROWSER_WORKBENCH_TAB_ID_PREFIX='browser-tab-';",
            "const BROWSER_WORKBENCH_MIN_DEVTOOLS_WIDTH=280;",
            "const BROWSER_WORKBENCH_DEFAULT_DEVTOOLS_WIDTH=420;",
            "const normalizeBrowserWorkbenchLoadStatus=value=>String(value||'idle');",
            normalize,
            "const restored=normalizePersistedTab({id:'browser-tab-1',number:1,url:'https://example.test/latest',current_url:'https://example.test/latest',requested_url:'https://example.test/old'});",
            "console.log(JSON.stringify(restored));",
        ]
    )

    restored = _run_node_json(program)
    assert restored["url"] == "https://example.test/latest"
    assert restored["currentUrl"] == "https://example.test/latest"
    assert restored["requestedUrl"] == "https://example.test/latest"


def test_navigation_entry_points_clear_error_and_share_navigation_lifecycle():
    js = _read("static/browser_workbench.js")

    assert "function beginBrowserWorkbenchNavigation" in js
    assert "target.navigationError=null" in js
    assert "beginBrowserWorkbenchNavigation(target,requested" in js
    assert "void navigateBrowserWorkbenchToUrl(undefined,suggestion.url)" in js
    assert "if(requestId!==target.navigationRequestId)return null" in js


def test_restored_navigation_content_states_are_mutually_exclusive():
    js = _read("static/browser_workbench.js")
    transition_program = "\n".join(
        [
            "const BROWSER_WORKBENCH_CONTENT_STATES=new Set(['restored','idle','loading','loaded','error']);",
            _js_function(js, "normalizeBrowserWorkbenchLoadStatus"),
            _js_function(js, "normalizeBrowserWorkbenchContentState"),
            _js_function(js, "nextBrowserWorkbenchContentState"),
            "const restored={contentState:'restored',hasStartedLoad:false,hasCommittedNavigation:false,navigationError:null};",
            "const failed={contentState:'error',hasStartedLoad:true,hasCommittedNavigation:false,navigationError:{chromium_error:'ERR_CONNECTION_REFUSED'}};",
            "console.log(JSON.stringify({restored:nextBrowserWorkbenchContentState(restored,'idle'),loading:nextBrowserWorkbenchContentState(restored,'loading'),error:nextBrowserWorkbenchContentState(restored,'error'),retry:nextBrowserWorkbenchContentState(failed,'loading'),loaded:nextBrowserWorkbenchContentState(failed,'success'),errorIdle:nextBrowserWorkbenchContentState(failed,'idle')}));",
        ]
    )

    assert _run_node_json(transition_program) == {
        "restored": "restored",
        "loading": "loading",
        "error": "error",
        "retry": "loading",
        "loaded": "loaded",
        "errorIdle": "error",
    }


def test_restored_placeholder_is_only_rendered_by_restored_content_state():
    js = _read("static/browser_workbench.js")
    restore = js[js.index("function restoreBrowserWorkbenchTabs") : js.index("function handleBrowserWorkbenchShortcut")]
    create = js[js.index("function createBrowserWorkbenchTabRecord") : js.index("function reorderBrowserWorkbenchTab")]
    begin = _js_function(js, "beginBrowserWorkbenchNavigation")
    render = _js_function(js, "renderActiveBrowserWorkbenchView")

    assert "contentState:browserWorkbenchIsBlankUrl(tab.url)?'idle':'restored'" in restore
    assert "Restored history URL" not in create
    assert js.count("Restored history URL:") == 1
    assert "if(state==='restored')" in js
    assert begin.index("setBrowserWorkbenchContentState(target,'loading')") < begin.index("markBrowserWorkbenchLoadStarted")
    assert "contentState==='error'&&active.navigationError" in render
    assert "contentState==='restored'||contentState==='error'" in render
    restored_branch = "else if(active&&(contentState==='restored'||contentState==='error'))"
    native_render_branch = "else if(active&&active.renderer==='electron-native'&&active.sessionId)"
    assert render.index(restored_branch) < render.index(native_render_branch)


def test_element_highlight_label_formatter_preserves_complete_tag_names():
    js = _read("static/browser_workbench.js")
    css = _read("static/style.css")
    desktop_main = _read("desktop/src/main/index.cjs")
    tags = ["section", "span", "div", "button", "input", "article", "header", "main", "svg", "path", "linearGradient"]
    formatter_program = "\n".join(
        [
            _js_function(js, "browserWorkbenchHtmlTagName"),
            _js_function(js, "browserWorkbenchElementLabel"),
            f"const tags={json.dumps(tags)};",
            "console.log(JSON.stringify(tags.map((tag)=>({tag,normalized:browserWorkbenchHtmlTagName(tag),label:browserWorkbenchElementLabel('ReactComponentName',tag,'fallback')}))));",
        ]
    )

    formatted = _run_node_json(formatter_program)
    assert formatted == [
        {"tag": tag, "normalized": tag, "label": f"ReactComponentName · {tag}"}
        for tag in tags
    ]

    long_component_program = "\n".join(
        [
            _js_function(js, "browserWorkbenchHtmlTagName"),
            _js_function(js, "browserWorkbenchElementLabel"),
            "console.log(JSON.stringify(browserWorkbenchElementLabel('Component'.repeat(30),'section','fallback')));",
        ]
    )
    assert _run_node_json(long_component_program).endswith(" · section")

    overlay_render = _js_function(js, "renderBrowserWorkbenchOverlay")
    assert ".slice(0,96)" not in overlay_render
    assert overlay_render.index("tag.append(componentPart,separatorPart,tagPart)") < overlay_render.index("positionBrowserWorkbenchOverlayLabel")
    assert ".browser-workbench-selection-overlay-tag{flex:0 0 auto" in css
    assert "componentPart.style.cssText = 'flex:1 1 auto;min-width:0;overflow:hidden" in desktop_main
    assert desktop_main.index("renderElementLabel(state.label, selection)") < desktop_main.index("positionSelectionLabel(state.label, rect)")


def test_electron_main_frame_failures_publish_structured_error_without_error_url_history():
    desktop_main = _read("desktop/src/main/index.cjs")
    api_py = _read("api/browser_workbench.py")

    assert "function handleRecordMainFrameFailure" in desktop_main
    assert "if (isMainFrame === false || isExpectedNavigationAbort(errorCode, errorDescription)) return" in desktop_main
    assert "navigation_error: record.navigationError" in desktop_main
    assert "validated_url" in desktop_main
    assert "chromium_error" in desktop_main
    assert "record.navigationError = null" in desktop_main
    failure_handler = desktop_main[desktop_main.index("function handleRecordMainFrameFailure"):desktop_main.index("function markRecordLoading")]
    assert "loadURL(" not in failure_handler
    assert '"navigation_error",' in api_py


def test_browser_error_page_is_retryable_and_theme_aware():
    js = _read("static/browser_workbench.js")
    css = _read("static/style.css")

    assert "function renderBrowserWorkbenchNavigationError" in js
    assert "This site can’t be reached" in js
    assert "browser-workbench-error-retry" in js
    assert "navigateBrowserWorkbenchHistory('reload',tab.id)" in js
    assert "browser-workbench-error-page" in css
    assert "var(--text)" in css
    assert "var(--surface)" in css


def test_same_url_retry_is_not_suppressed_after_failure():
    desktop_main = _read("desktop/src/main/index.cjs")

    assert "const alreadyAtUrl = currentUrl === nextUrl" not in desktop_main
    assert "startRecordUrlLoad(record, nextUrl, 'load-url')" in desktop_main
    assert "isSupersededMainFrameFailure" in desktop_main
