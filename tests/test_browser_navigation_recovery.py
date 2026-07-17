from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_restored_suspended_tab_reload_materializes_from_saved_url():
    js = _read("static/browser_workbench.js")

    assert "function browserWorkbenchCanReload" in js
    assert "workbenchCapabilities.navigation===true&&browserWorkbenchCanReload(active)" in js
    assert "if(normalized==='reload'&&!target.sessionId)" in js
    assert "return navigateBrowserWorkbenchToUrl(target.id,retryUrl)" in js
    assert "record = ensureTab({ ...recoveryPayload, session_id: sessionId })" in _read("desktop/src/main/index.cjs")
    assert "Open a Browser Workbench session before using navigation controls." not in js[js.index("async function navigateBrowserWorkbenchHistory"):js.index("async function maybeStartBrowserWorkbenchInitialLoadOnActivation")]


def test_navigation_entry_points_clear_error_and_share_navigation_lifecycle():
    js = _read("static/browser_workbench.js")

    assert "function beginBrowserWorkbenchNavigation" in js
    assert "target.navigationError=null" in js
    assert "beginBrowserWorkbenchNavigation(target,requested" in js
    assert "void navigateBrowserWorkbenchToUrl(undefined,suggestion.url)" in js
    assert "if(requestId!==target.navigationRequestId)return null" in js


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
