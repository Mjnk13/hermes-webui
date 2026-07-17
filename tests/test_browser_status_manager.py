from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_browser_statuses_are_owned_typed_and_generation_guarded():
    js = _read("static/browser_workbench.js")

    assert "function browserWorkbenchSetStatus" in js
    assert "function browserWorkbenchResolveStatus" in js
    assert "function browserWorkbenchClearStatus" in js
    assert "BROWSER_WORKBENCH_STATUS_PRIORITY" in js
    assert "entry.id!==token.id" in js
    assert "kind==='temporary'" in js
    assert "BROWSER_WORKBENCH_STATUS_FEEDBACK_MS" in js
    assert "return browserWorkbenchSetStatus(message,{" in js
    assert "entry.kind==='persistent'||entry.kind==='error'" in js
    assert "const newest=b.id-a.id" in js
    assert js.count("statusEl.textContent=") == 1
    assert 'id="browserWorkbenchStatus" aria-live="polite"></div>' in _read("static/index.html")


def test_selection_and_area_capture_messages_follow_mode_lifecycle():
    js = _read("static/browser_workbench.js")

    assert "owner:'selection',kind:'persistent'" in js
    assert "browserWorkbenchClearStatus(previousSelectionTab,{owner:'selection'})" in js
    assert "browserWorkbenchClearStatus(previousSelectionTab,{owner:'selection-action'})" in js
    assert "owner:'area-capture',kind:'persistent'" in js
    assert "browserWorkbenchClearStatus" in js[js.index("function cancelBrowserWorkbenchAreaCapture"):js.index("function updateBrowserWorkbenchAreaBox")]


def test_progress_is_cleared_on_tab_change_navigation_and_teardown():
    js = _read("static/browser_workbench.js")

    activate = js[js.index("function activateBrowserWorkbenchTab"):js.index("async function openBrowserWorkbenchTab")]
    close = js[js.index("async function closeBrowserWorkbenchTab"):js.index("function syncBrowserWorkbenchTabActive")]
    begin_navigation = js[js.index("function beginBrowserWorkbenchNavigation"):js.index("function browserWorkbenchStatusState")]
    assert "kinds:['progress']" in activate
    assert "all:true" in close
    assert "owner:'navigation',kind:'progress'" in begin_navigation
    assert "setBrowserWorkbenchSelectionMode(false)" in begin_navigation
    assert "cancelBrowserWorkbenchAreaCapture(target)" in begin_navigation


def test_user_facing_copy_avoids_internal_browser_implementation_terms():
    sources = "\n".join(
        (
            _read("static/browser_workbench.js"),
            _read("static/index.html"),
            _read("api/browser_workbench.py"),
            _read("desktop/src/main/index.cjs"),
        )
    )

    assert "Restored history URL:" in sources
    forbidden = (
        "recreates its scoped Browser Workbench session",
        "lifecycle/navigation session",
        "checking capabilities",
        "Browser Workbench is checking capabilities",
        "iframe proxy renderer is live",
        "scoped to iframe-proxy page",
        "Electron native browser surface",
        "Chromium Browser Workbench stream is live",
    )
    for phrase in forbidden:
        assert phrase not in sources


def test_zoom_and_one_time_confirmations_are_temporary():
    js = _read("static/browser_workbench.js")

    assert "`Zoom set to ${Math.round(active.zoom*100)}%.`,'ready',active,{owner:'zoom',kind:'temporary'}" in js
    assert "owner:'clipboard',kind:url?'temporary':'error'" in js
    assert "attached.`,{kind:'temporary'" in js


def test_progress_entries_have_a_central_safety_timeout():
    js = _read("static/browser_workbench.js")

    assert "BROWSER_WORKBENCH_STATUS_PROGRESS_TIMEOUT_MS" in js
    assert "kind==='temporary'||kind==='progress'" in js
