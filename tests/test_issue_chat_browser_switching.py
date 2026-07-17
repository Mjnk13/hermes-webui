from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
DESKTOP_MAIN = (ROOT / "desktop" / "src" / "main" / "index.cjs").read_text(encoding="utf-8")


def test_sidebar_session_selection_activates_chat_before_same_session_noop():
    assert "function _activateChatBodyForSessionSelection()" in SESSIONS_JS
    assert "switchPanel('chat',{fromSessionSelection:true,bypassSettingsGuard:true})" in SESSIONS_JS

    activation = "if(opts.fromSessionSelection) _activateChatBodyForSessionSelection();"
    same_session_noop = "if(currentSid===sid && !forceReload && (!_loadingSessionId || _loadingSessionId===sid)){"
    assert activation in SESSIONS_JS
    assert same_session_noop in SESSIONS_JS
    assert SESSIONS_JS.index(activation) < SESSIONS_JS.index(same_session_noop)


def test_sidebar_navigation_callers_mark_chat_selection_intent():
    assert "_openSidebarSession(s,{fromSessionSelection:true})" in SESSIONS_JS
    assert "_openSidebarSession(seg, {skipLineageResolve:true,fromSessionSelection:true})" in SESSIONS_JS
    assert "_openSidebarSession(childSession, {skipLineageResolve:true,fromSessionSelection:true})" in SESSIONS_JS
    assert "loadSession(next,{fromSessionSelection:true})" in SESSIONS_JS


def test_electron_native_view_is_backgrounded_not_removed_on_chat_switch():
    hide_start = DESKTOP_MAIN.index("function hideRecord(record) {")
    hide_end = DESKTOP_MAIN.index("function setNativeBounds", hide_start)
    hide_record = DESKTOP_MAIN[hide_start:hide_end]

    assert "record.view.setVisible(false)" in hide_record
    assert "record.view.setBounds({ x: 0, y: 0, width: 0, height: 0 })" in hide_record
    assert "removeNativeViewFromWindow(record);" not in hide_record
    assert "Keep the WebContentsView attached" in hide_record

    close_start = DESKTOP_MAIN.index("function closeTab(sessionId) {")
    close_end = DESKTOP_MAIN.index("function closeAllNativeTabs", close_start)
    close_tab = DESKTOP_MAIN[close_start:close_end]
    assert "hideRecord(record);" in close_tab
    assert "removeNativeViewFromWindow(record);" in close_tab


def test_electron_surfaces_have_stable_dark_backgrounds():
    assert "const STABLE_SURFACE_BACKGROUND = '#0D0D1A';" in DESKTOP_MAIN
    assert "backgroundColor: STABLE_SURFACE_BACKGROUND" in DESKTOP_MAIN
    assert "mainWindow.setBackgroundColor(STABLE_SURFACE_BACKGROUND)" in DESKTOP_MAIN
    assert "mainWindow.webContents.setBackgroundColor(STABLE_SURFACE_BACKGROUND)" in DESKTOP_MAIN
    assert "view.setBackgroundColor(STABLE_SURFACE_BACKGROUND)" in DESKTOP_MAIN
    assert "view.webContents.setBackgroundColor(STABLE_SURFACE_BACKGROUND)" in DESKTOP_MAIN
