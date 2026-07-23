from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
STYLE = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
WORKBENCH = (ROOT / "static" / "browser_workbench.js").read_text(encoding="utf-8")
PRELOAD = (ROOT / "desktop" / "src" / "preload" / "index.cjs").read_text(encoding="utf-8")
DESKTOP = (ROOT / "desktop" / "src" / "main" / "index.cjs").read_text(encoding="utf-8")


def test_native_page_find_shortcut_is_intercepted_only_by_the_focused_browser_surface():
    assert "function isBrowserFindShortcut(input)" in DESKTOP
    assert "record.visible && activeSessionId === record.id" in DESKTOP
    assert "event.preventDefault();" in DESKTOP
    assert "browser-workbench:find-requested" in DESKTOP
    assert "mainWindow.webContents.focus()" in DESKTOP


def test_find_in_page_uses_electron_api_and_forwards_match_results():
    assert "record.view.webContents.findInPage" in DESKTOP
    assert "record.view.webContents.stopFindInPage" in DESKTOP
    assert "view.webContents.on('found-in-page'" in DESKTOP
    assert "browser-workbench:find-result" in DESKTOP
    assert "findInPage(payload)" in PRELOAD
    assert "stopFindInPage(payload)" in PRELOAD
    assert "onFindRequested(callback)" in PRELOAD
    assert "onFindResult(callback)" in PRELOAD


def test_find_controls_are_a_fixed_height_toolbar_surface_not_a_browser_placeholder():
    for element_id in (
        "browserWorkbenchFind",
        "browserWorkbenchFindMatches",
        "browserWorkbenchFindPrevious",
        "browserWorkbenchFindNext",
        "browserWorkbenchFindClose",
    ):
        assert f'id="{element_id}"' in INDEX
    assert ".browser-workbench-find" in STYLE
    assert "position:absolute" in STYLE[STYLE.index(".browser-workbench-find"):]
    assert "browser-workbench-viewport" not in INDEX[
        INDEX.index('id="browserWorkbenchFind"'):INDEX.index('id="browserWorkbenchFindClose"')
    ]


def test_find_ui_supports_query_navigation_close_and_native_events():
    assert "function openBrowserWorkbenchFind" in WORKBENCH
    assert "function runBrowserWorkbenchFind" in WORKBENCH
    assert "function closeBrowserWorkbenchFind" in WORKBENCH
    assert "onFindRequested" in WORKBENCH
    assert "onFindResult" in WORKBENCH
    assert "event.key==='Enter'" in WORKBENCH
    assert "event.shiftKey" in WORKBENCH
    assert "event.key==='Escape'" in WORKBENCH
    assert "focusPage:true" in WORKBENCH


def test_find_session_is_cleared_when_tab_or_page_lifecycle_changes():
    navigation = WORKBENCH[
        WORKBENCH.index("function applyBrowserWorkbenchNativeNavigationUpdate"):
        WORKBENCH.index("function setTabState")
    ]
    activation = WORKBENCH[
        WORKBENCH.index("function activateBrowserWorkbenchTab"):
        WORKBENCH.index("async function openBrowserWorkbenchTab")
    ]
    closing = WORKBENCH[
        WORKBENCH.index("async function closeBrowserWorkbenchTab"):
        WORKBENCH.index("function syncBrowserWorkbenchTabActive")
    ]
    assert "closeBrowserWorkbenchFind" in navigation
    assert "closeBrowserWorkbenchFind" in activation
    assert "closeBrowserWorkbenchFind" in closing
