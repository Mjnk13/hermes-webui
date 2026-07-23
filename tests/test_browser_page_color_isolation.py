"""Regression coverage for website color isolation in Browser Workbench."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_MAIN = (ROOT / "desktop" / "src" / "main" / "index.cjs").read_text(encoding="utf-8")
DESKTOP_PRELOAD = (ROOT / "desktop" / "src" / "preload" / "index.cjs").read_text(encoding="utf-8")
WORKBENCH_JS = (ROOT / "static" / "browser_workbench.js").read_text(encoding="utf-8")


def _between(start: str, end: str) -> str:
    start_index = DESKTOP_MAIN.index(start)
    end_index = DESKTOP_MAIN.index(end, start_index)
    return DESKTOP_MAIN[start_index:end_index]


def test_visited_pages_have_an_opaque_browser_neutral_backing_surface():
    ensure_tab = _between("function ensureTab(payload) {", "function startRecordUrlLoad")

    assert "const BROWSER_PAGE_BACKGROUND = '#FFFFFF';" in DESKTOP_MAIN
    assert "view.setBackgroundColor(BROWSER_PAGE_BACKGROUND)" in ensure_tab
    assert "view.webContents.setBackgroundColor(BROWSER_PAGE_BACKGROUND)" in ensure_tab
    assert "SHELL_SURFACE_BACKGROUND" not in ensure_tab
    assert "#00000000" not in ensure_tab


def test_visited_page_color_scheme_is_not_overridden_by_hermes():
    ensure_tab = _between("function ensureTab(payload) {", "function startRecordUrlLoad")

    for forbidden in (
        "insertCSS",
        "nativeTheme.themeSource",
        "setEmulatedMedia",
        "force-dark-mode",
        "enable-force-dark",
        "auto-dark-mode",
    ):
        assert forbidden not in ensure_tab
        assert forbidden not in DESKTOP_MAIN
    assert "insertCSS" not in DESKTOP_PRELOAD


def test_ping_overlay_is_inline_scoped_and_fully_disposed():
    selection_script = _between("function nativeSelectionScript(sessionId, enabled) {", "function forwardNativeSelection")

    assert "document.createElement('style')" not in selection_script
    assert "document.documentElement.classList" not in selection_script
    assert "document.body.style" not in selection_script
    assert "data-hermes-browser-workbench-selection-overlay" in selection_script
    assert "state.overlay.parentNode.removeChild(state.overlay)" in selection_script
    assert "document.documentElement.style.cursor = state.cursor || ''" in selection_script
    assert "delete window[key]" in selection_script


def test_main_frame_navigation_exits_ping_mode_before_new_document_finishes():
    start = WORKBENCH_JS.index("function applyBrowserWorkbenchNativeNavigationUpdate(payload){")
    end = WORKBENCH_JS.index("function setTabState", start)
    navigation_update = WORKBENCH_JS[start:end]

    assert "detail.reason==='did-start-navigation'" in navigation_update
    assert "selectionModeTabId===target.id" in navigation_update
    assert "setBrowserWorkbenchSelectionMode(false)" in navigation_update
