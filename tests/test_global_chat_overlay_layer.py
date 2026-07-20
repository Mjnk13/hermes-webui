"""Regression coverage for chat overlays above Electron WebContentsView tabs."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
UI = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
WORKBENCH = (ROOT / "static" / "browser_workbench.js").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
PRELOAD = (ROOT / "desktop" / "src" / "preload" / "index.cjs").read_text(encoding="utf-8")
DESKTOP = (ROOT / "desktop" / "src" / "main" / "index.cjs").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _function(source: str, name: str) -> str:
    start = source.index(f"function {name}(")
    if source[max(0, start - 6) : start] == "async ":
        start -= 6
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


def test_chat_overlays_share_one_document_level_portal():
    assert 'id="globalOverlayLayer"' in INDEX
    assert "function _ensureGlobalOverlayLayer()" in UI
    assert "function _mountGlobalOverlay(el)" in UI
    assert "function _restoreGlobalOverlay(el)" in UI
    assert "layer.appendChild(el)" in UI
    assert ".global-overlay-layer{" in CSS
    assert ".global-overlay-item{" in CSS


def test_overlay_portal_covers_chat_flyouts_popovers_and_lightboxes():
    for element_id in (
        "queueCard",
        "approvalCard",
        "clarifyCard",
        "composerTerminalPanel",
        "handoffHintContainer",
        "cmdDropdown",
        "savedPromptsPopup",
        "composerMobileConfigPanel",
        "composerWsDropdown",
        "composerReasoningDropdown",
        "composerFastDropdown",
        "composerToolsetsDropdown",
        "composerModelDropdown",
        "ctxTooltip",
        "profileDropdown",
        "appDialogOverlay",
    ):
        marker = f'id="{element_id}"'
        start = INDEX.index(marker)
        tag_start = INDEX.rfind("<", 0, start)
        tag_end = INDEX.index(">", start)
        assert "data-global-overlay" in INDEX[tag_start:tag_end], element_id
    assert "lb.dataset.globalOverlay='modal'" in UI


def test_portal_preserves_home_and_publishes_single_native_occlusion_event():
    assert "_globalOverlayHomes.set(el" in UI
    assert "home.parent.insertBefore(el,home.nextSibling)" in UI
    assert "CustomEvent('hermes-global-overlay-change'" in UI
    assert "overlayCount:overlays.length" in UI
    assert "generation:_globalOverlayGeneration" in UI
    assert "window.__hermesGlobalOverlayState=detail" in UI
    assert "MutationObserver" in UI
    assert "function _globalOverlayNativeSurfaceActive()" in UI
    assert "const shouldPortal=_globalOverlayNativeSurfaceActive();" in UI


def test_portal_uses_live_viewport_anchors_with_flip_shift_and_reflow_hooks():
    assert "function _globalOverlayViewport()" in UI
    assert "function _positionAnchoredGlobalOverlay(el,record)" in UI
    assert "function _scheduleGlobalOverlayPositions()" in UI
    assert "anchor.getBoundingClientRect()" in UI
    assert "placement='bottom'" in UI
    assert "viewport.right-margin-width" in UI
    assert "new ResizeObserver(()=>_scheduleGlobalOverlayPositions())" in UI
    assert "document.addEventListener('scroll',_scheduleGlobalOverlayPositions" in UI
    assert "window.addEventListener('resize',_scheduleGlobalOverlayPositions" in UI
    assert "window.visualViewport.addEventListener('scroll',_scheduleGlobalOverlayPositions" in UI
    assert "widthMode:isPhone?'viewport':'natural'" in UI


def test_full_window_overlays_keep_viewport_centering_in_the_shared_portal():
    assert ".app-dialog-overlay{position:fixed;inset:0" in CSS
    assert ".img-lightbox{position:fixed;inset:0" in CSS
    assert "align-items:center;justify-content:center" in CSS
    positioner = _function(UI, "_positionGlobalOverlay")
    assert "if(mode==='modal'||mode==='always')return false" in positioner
    assert "document.body.appendChild(lb);\n  _reconcileGlobalOverlays();" in UI


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_anchor_positioning_math_tracks_trigger_flips_and_shifts():
    functions = "\n".join(
        _function(UI, name)
        for name in (
            "_globalOverlayViewport",
            "_globalOverlayStyle",
            "_positionAnchoredGlobalOverlay",
        )
    )
    script = f"""
const window={{innerWidth:900,innerHeight:700,visualViewport:null}};
{functions}
const style={{position:'',left:'',right:'',top:'',bottom:'',width:'',maxWidth:'',maxHeight:''}};
const el={{style,dataset:{{}},scrollHeight:180,offsetHeight:180,getBoundingClientRect(){{
  const width=parseFloat(style.width)||260;
  const maxHeight=parseFloat(style.maxHeight);
  const height=Number.isFinite(maxHeight)?Math.min(180,maxHeight):180;
  const left=parseFloat(style.left)||0;
  const top=parseFloat(style.top)||0;
  return {{left,top,width,height,right:left+width,bottom:top+height}};
}}}};
let trigger={{left:300,top:620,width:120,height:32,right:420,bottom:652}};
const anchor={{isConnected:true,getBoundingClientRect(){{return trigger;}}}};
const record={{anchor,gap:6,placement:'top',align:'start',widthMode:'natural',naturalWidth:260,margin:8,baseMaxHeight:'',cssMaxHeight:null}};
_positionAnchoredGlobalOverlay(el,record);
const anchored={{left:parseFloat(style.left),top:parseFloat(style.top),placement:el.dataset.globalOverlayPlacement}};
trigger={{left:520,top:360,width:120,height:32,right:640,bottom:392}};
_positionAnchoredGlobalOverlay(el,record);
const moved={{left:parseFloat(style.left),top:parseFloat(style.top),placement:el.dataset.globalOverlayPlacement}};
trigger={{left:520,top:10,width:120,height:32,right:640,bottom:42}};
_positionAnchoredGlobalOverlay(el,record);
const flipped={{left:parseFloat(style.left),top:parseFloat(style.top),placement:el.dataset.globalOverlayPlacement}};
window.innerWidth=420;
trigger={{left:370,top:10,width:40,height:32,right:410,bottom:42}};
_positionAnchoredGlobalOverlay(el,record);
const shifted={{left:parseFloat(style.left),right:parseFloat(style.left)+parseFloat(style.width),placement:el.dataset.globalOverlayPlacement}};
process.stdout.write(JSON.stringify({{anchored,moved,flipped,shifted}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["anchored"] == {"left": 300, "top": 434, "placement": "top"}
    assert data["moved"] == {"left": 520, "top": 174, "placement": "top"}
    assert data["flipped"] == {"left": 520, "top": 48, "placement": "bottom"}
    assert data["shifted"] == {"left": 152, "right": 412, "placement": "bottom"}


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_portal_overlay_stack_publishes_every_open_surface_until_last_close():
    functions = "\n".join(
        _function(UI, name)
        for name in (
            "_globalOverlayId",
            "_collectGlobalOverlayState",
            "_emitGlobalOverlayState",
        )
    )
    script = f"""
let _globalOverlayGeneration=1000;
let _globalOverlayLastSignature='';
let _globalOverlayNextId=1;
const _globalOverlayIds=new WeakMap();
const _globalOverlayMounted=new Set();
const events=[];
const window={{dispatchEvent:(event)=>events.push(event.detail)}};
class CustomEvent{{constructor(type,options){{this.type=type;this.detail=options.detail;}}}}
const _globalOverlayIsOpen=()=>true;
const first={{id:'model',isConnected:true,getBoundingClientRect:()=>({{left:20,top:30,width:200,height:100}})}};
const second={{id:'preview',isConnected:true,getBoundingClientRect:()=>({{left:0,top:0,width:900,height:700}})}};
{functions}
_globalOverlayMounted.add(first);
_emitGlobalOverlayState();
_globalOverlayMounted.add(second);
_emitGlobalOverlayState();
_globalOverlayMounted.delete(first);
_emitGlobalOverlayState();
_globalOverlayMounted.delete(second);
_emitGlobalOverlayState();
process.stdout.write(JSON.stringify(events.map(event=>({{count:event.overlayCount,ids:event.overlays.map(item=>item.id),generation:event.generation}}))));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    states = json.loads(result.stdout)
    assert [state["count"] for state in states] == [1, 2, 1, 0]
    assert states[1]["ids"] == ["model", "preview"]
    assert states[2]["ids"] == ["preview"]
    assert [state["generation"] for state in states] == sorted(
        state["generation"] for state in states
    )


def test_browser_surface_is_snapshot_backed_before_scoped_visibility_suppression():
    assert "let browserWorkbenchGlobalOverlayRects=[];" in WORKBENCH
    assert "function browserWorkbenchOverlayIntersections(bounds)" in WORKBENCH
    assert "function browserWorkbenchOverlaySuppression(bounds)" in WORKBENCH
    assert "incomingGeneration<browserWorkbenchGlobalOverlayGeneration)return" in WORKBENCH
    assert "window.addEventListener('hermes-global-overlay-change'" in WORKBENCH
    assert "callDesktopBrowserBridge('setOverlaySuppressed'" in WORKBENCH
    assert "applicationOverlay:browserWorkbenchOverlaySuppression(bounds)" in WORKBENCH
    assert "browserWorkbenchBoundsBelowGlobalOverlays" not in WORKBENCH
    assert "payload.bounds=browserWorkbenchBoundsBelowGlobalOverlays" not in WORKBENCH
    assert "setOverlaySuppressed(payload)" in PRELOAD
    assert "captureOverlaySnapshot(payload)" in PRELOAD
    assert "browser-workbench:set-overlay-suppressed" in PRELOAD
    assert "browser-workbench:capture-overlay-snapshot" in PRELOAD
    assert "function setApplicationOverlaySuppression(payload)" in DESKTOP
    assert "function captureApplicationOverlaySnapshot(payload)" in DESKTOP
    assert "applicationOverlaySuppressionAppliesToRecord(applicationOverlaySuppression, record)" in DESKTOP
    assert ".browser-workbench-native-overlay-snapshot{" in CSS

    suppress = _function(DESKTOP, "applyApplicationOverlaySuppressionToRecord")
    assert "record.view.setVisible(!suppressed)" in suppress
    assert ".setBounds(" not in suppress
    assert "removeChildView" not in suppress
    assert "activeSessionId" not in suppress


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_only_overlays_intersecting_the_native_view_suppress_it():
    functions = "\n".join(
        _function(WORKBENCH, name)
        for name in (
            "browserWorkbenchOverlayIntersections",
            "browserWorkbenchOverlaySuppression",
        )
    )
    script = f"""
let browserWorkbenchGlobalOverlayGeneration=42;
let browserWorkbenchGlobalOverlayRects=[];
{functions}
const bounds={{x:100,y:100,width:600,height:400}};
browserWorkbenchGlobalOverlayRects=[{{x:10,y:10,width:50,height:50}}];
const outside=browserWorkbenchOverlaySuppression(bounds);
browserWorkbenchGlobalOverlayRects.push({{x:650,y:450,width:100,height:100}});
const intersecting=browserWorkbenchOverlaySuppression(bounds);
browserWorkbenchGlobalOverlayRects.shift();
const nested=browserWorkbenchOverlaySuppression(bounds);
browserWorkbenchGlobalOverlayRects=[];
const closed=browserWorkbenchOverlaySuppression(bounds);
process.stdout.write(JSON.stringify({{outside,intersecting,nested,closed}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["outside"] == {"suppressed": False, "overlayCount": 0, "generation": 42}
    assert data["intersecting"] == {"suppressed": True, "overlayCount": 1, "generation": 42}
    assert data["nested"] == {"suppressed": True, "overlayCount": 1, "generation": 42}
    assert data["closed"] == {"suppressed": False, "overlayCount": 0, "generation": 42}


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_native_overlay_suppression_is_generation_safe_and_nested():
    functions = "\n".join(
        _function(DESKTOP, name)
        for name in (
            "applicationOverlaySuppressionAppliesToRecord",
            "applyApplicationOverlaySuppressionToRecord",
            "setApplicationOverlaySuppression",
        )
    )
    script = f"""
const visibility=[];
let focusRestores=0;
const webContents={{isFocused:()=>true,isDestroyed:()=>false,focus:()=>{{focusRestores+=1;}}}};
const view={{webContents,isDestroyed:()=>false,setVisible:(value)=>visibility.push(value),setBounds:()=>{{throw new Error('overlay suppression must not resize');}}}};
const record={{id:'session-1',view,visible:true,focusBeforeApplicationOverlay:false}};
const tabs=new Map([['session-1',record]]);
let applicationOverlaySuppression={{suppressed:false,generation:0,overlayCount:0,sessionId:''}};
const setTimeout=(callback)=>callback();
{functions}
const opened=setApplicationOverlaySuppression({{suppressed:true,generation:10,overlayCount:2,sessionId:'session-1'}});
const staleClose=setApplicationOverlaySuppression({{suppressed:false,generation:9,overlayCount:0,sessionId:'session-1'}});
const oneStillOpen=setApplicationOverlaySuppression({{suppressed:true,generation:11,overlayCount:1,sessionId:'session-1'}});
const closed=setApplicationOverlaySuppression({{suppressed:false,generation:12,overlayCount:0,sessionId:'session-1'}});
process.stdout.write(JSON.stringify({{visibility,focusRestores,opened,staleClose,oneStillOpen,closed}}));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["visibility"] == [False, True]
    assert data["focusRestores"] == 1
    assert data["opened"]["suppressed"] is True
    assert data["staleClose"]["ignored"] is True
    assert data["oneStillOpen"]["suppressed"] is True
    assert data["oneStillOpen"]["overlayCount"] == 1
    assert data["closed"]["suppressed"] is False


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_native_overlay_snapshot_is_ready_before_visibility_suppression():
    """The app renderer must paint the native page capture before hiding it.

    A second/nested overlay reuses the staged capture, while close restores the
    live native view without removing the capture underneath it. Keeping that
    last frame mounted prevents a white compositor gap on close.
    """
    sync = _function(WORKBENCH, "syncBrowserWorkbenchNativeOverlaySuppression")
    script = f"""
const calls=[];
let browserWorkbenchNativeOverlaySyncRequest=0;
let browserWorkbenchNativeOverlaySuppressed=false;
let hasSnapshot=false;
let nextPayload={{
  tabId:'tab-1',sessionId:'session-1',
  applicationOverlay:{{suppressed:true,overlayCount:1,generation:10}},
}};
const canUseElectronNativeBridge=()=>true;
const getActiveWorkbenchTab=()=>({{id:'tab-1',sessionId:'session-1',renderer:'electron-native'}});
const currentNativeBoundsPayload=()=>nextPayload;
const browserWorkbenchHasNativeOverlaySnapshot=()=>hasSnapshot;
const stageBrowserWorkbenchNativeOverlaySnapshot=async()=>{{calls.push('capture');hasSnapshot=true;return true;}};
const callDesktopBrowserBridge=async(method,payload)=>{{
  calls.push(method+':'+String(payload.suppressed));
  return {{ok:true,suppressed:payload.suppressed===true,sessionId:payload.sessionId||''}};
}};
{sync}
(async()=>{{
  await syncBrowserWorkbenchNativeOverlaySuppression();
  nextPayload={{...nextPayload,applicationOverlay:{{suppressed:true,overlayCount:2,generation:11}}}};
  await syncBrowserWorkbenchNativeOverlaySuppression();
  nextPayload={{...nextPayload,applicationOverlay:{{suppressed:false,overlayCount:0,generation:12}}}};
  await syncBrowserWorkbenchNativeOverlaySuppression();
  process.stdout.write(JSON.stringify({{calls,suppressed:browserWorkbenchNativeOverlaySuppressed,hasSnapshot}}));
}})().catch(err=>{{console.error(err);process.exit(1);}});
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data == {
        "calls": [
            "capture",
            "setOverlaySuppressed:true",
            "setOverlaySuppressed:true",
            "setOverlaySuppressed:false",
        ],
        "suppressed": False,
        "hasSnapshot": True,
    }


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_native_overlay_snapshot_helper_decodes_mounts_and_paints_captured_page():
    stage = _function(WORKBENCH, "stageBrowserWorkbenchNativeOverlaySnapshot")
    script = f"""
const events=[];
let browserWorkbenchNativeOverlaySyncRequest=4;
let snapshot=null;
const viewportEl={{
  querySelector:()=>snapshot,
  appendChild:image=>{{snapshot=image;image.isConnected=true;events.push('mount');}},
}};
const wireDom=()=>{{}};
const document={{createElement:tag=>({{
  tag,dataset:{{}},className:'',alt:'',isConnected:false,complete:true,
  setAttribute(name,value){{this[name]=value;}},
  getAttribute(name){{return this[name]||null;}},
  decode:async()=>{{events.push('decode');}},
  remove(){{events.push('remove');this.isConnected=false;}},
}})}};
const window={{requestAnimationFrame:callback=>{{events.push('paint');callback();}}}};
const callDesktopBrowserBridge=async(method,payload)=>{{
  events.push(method);
  return {{ok:true,generation:payload.generation,session_id:payload.sessionId,data_url:'data:image/png;base64,cGFnZQ=='}};
}};
{stage}
(async()=>{{
  const staged=await stageBrowserWorkbenchNativeOverlaySnapshot({{
    sessionId:'session-1',applicationOverlay:{{generation:7}},
  }},4);
  process.stdout.write(JSON.stringify({{
    staged,events,className:snapshot&&snapshot.className,
    sessionId:snapshot&&snapshot.dataset.sessionId,src:snapshot&&snapshot.src,
  }}));
}})().catch(err=>{{console.error(err);process.exit(1);}});
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "staged": True,
        "events": ["captureOverlaySnapshot", "decode", "mount", "paint"],
        "className": "browser-workbench-native-overlay-snapshot",
        "sessionId": "session-1",
        "src": "data:image/png;base64,cGFnZQ==",
    }


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_native_overlay_suppression_is_scoped_to_captured_session():
    """A chat overlay must never globally blank every attached native tab."""
    functions = "\n".join(
        _function(DESKTOP, name)
        for name in (
            "applicationOverlaySuppressionAppliesToRecord",
            "applyApplicationOverlaySuppressionToRecord",
            "setApplicationOverlaySuppression",
        )
    )
    script = f"""
const visibility={{one:[],two:[]}};
const makeRecord=(id,key)=>({{
  id,visible:true,focusBeforeApplicationOverlay:false,
  view:{{
    webContents:{{isFocused:()=>false,isDestroyed:()=>false,focus:()=>{{}}}},
    isDestroyed:()=>false,
    setVisible:value=>visibility[key].push(value),
    setBounds:()=>{{throw new Error('overlay suppression must not resize');}},
  }},
}});
const one=makeRecord('session-1','one');
const two=makeRecord('session-2','two');
const tabs=new Map([['session-1',one],['session-2',two]]);
let applicationOverlaySuppression={{suppressed:false,generation:0,overlayCount:0,sessionId:''}};
const setTimeout=callback=>callback();
{functions}
setApplicationOverlaySuppression({{suppressed:true,generation:10,overlayCount:1,sessionId:'session-1'}});
setApplicationOverlaySuppression({{suppressed:true,generation:11,overlayCount:2,sessionId:'session-1'}});
setApplicationOverlaySuppression({{suppressed:true,generation:12,overlayCount:1,sessionId:'session-2'}});
setApplicationOverlaySuppression({{suppressed:false,generation:13,overlayCount:0,sessionId:'session-2'}});
process.stdout.write(JSON.stringify(visibility));
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"one": [False, True], "two": [False, True]}


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_overlay_snapshot_capture_does_not_mutate_browser_lifecycle_state():
    capture = _function(DESKTOP, "captureApplicationOverlaySnapshot")
    script = f"""
const calls=[];
const record={{
  id:'session-1',visible:true,url:'https://example.test/page',zoom:1.25,
  view:{{
    isDestroyed:()=>false,
    setVisible:()=>calls.push('setVisible'),
    setBounds:()=>calls.push('setBounds'),
    webContents:{{
      isDestroyed:()=>false,
      getURL:()=>record.url,
      capturePage:async()=>({{toDataURL:()=> 'data:image/png;base64,cGFnZQ=='}}),
      loadURL:()=>calls.push('loadURL'),reload:()=>calls.push('reload'),
    }},
  }},
}};
const tabs=new Map([['session-1',record]]);
{capture}
(async()=>{{
  const result=await captureApplicationOverlaySnapshot({{sessionId:'session-1',generation:7}});
  process.stdout.write(JSON.stringify({{result,calls,url:record.url,zoom:record.zoom,visible:record.visible}}));
}})().catch(err=>{{console.error(err);process.exit(1);}});
"""
    result = subprocess.run([NODE, "-e", script], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["calls"] == []
    assert data["url"] == "https://example.test/page"
    assert data["zoom"] == 1.25
    assert data["visible"] is True
    assert data["result"] == {
        "ok": True,
        "generation": 7,
        "session_id": "session-1",
        "data_url": "data:image/png;base64,cGFnZQ==",
    }


def test_portal_does_not_replace_existing_dismissal_handlers():
    # Outside-click and Escape remain owned by the original components; the
    # portal only relocates their live nodes and never clones/replaces them.
    assert "cloneNode" not in UI[UI.index("function _mountGlobalOverlay(el)"):UI.index("function _restoreGlobalOverlay(el)")]
    assert "if(e.key==='Escape')" in UI
    assert "if(e.target===overlay)" in UI
    assert "onNativeSurfaceInteraction(callback)" in PRELOAD
    assert "browser-workbench:native-surface-interaction" in DESKTOP
    assert "view.webContents.on('focus'" in DESKTOP
    assert "view.webContents.on('before-input-event'" in DESKTOP
    assert "new MouseEvent('click'" in UI
    assert "new KeyboardEvent('keydown'" in UI
    # The shared DOM dialog keeps its existing backdrop dismissal path.
    assert "if(e.target===overlay)" in UI


def test_live_portaled_node_keeps_click_outside_and_escape_lifecycle():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - optional browser dependency
        pytest.skip("playwright is unavailable")

    start = UI.index("const _globalOverlayHomes")
    end = UI.index("function _matchBacktickFenceLine", start)
    portal_source = UI[start:end]
    html = """<!doctype html><html><head><style>
      #globalOverlayLayer{position:fixed;inset:0;pointer-events:none}
      .global-overlay-item{pointer-events:auto}
      #home{position:fixed;left:40px;bottom:20px;width:260px;height:40px}
      #menu{display:none;position:absolute;left:0;bottom:44px;width:220px;height:100px}
      #menu.open{display:block}
    </style></head><body>
      <div id="home"><div id="menu" data-global-overlay="open"><button id="inside">inside</button></div></div>
      <button id="outside">outside</button>
      <div id="globalOverlayLayer" class="global-overlay-layer"></div>
    </body></html>"""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 900, "height": 700})
        page.set_content(html)
        page.evaluate("window.hermesDesktop={browser:{renderer:'electron-native',isDesktop:true}};document.body.insertAdjacentHTML('afterbegin','<main class=\"main showing-browser\"></main>')")
        page.add_script_tag(content=portal_source)
        page.evaluate("""() => {
          window.insideClicks=0;
          inside.addEventListener('click',event=>{window.insideClicks+=1;event.stopPropagation();});
          document.addEventListener('click',event=>{if(!event.target.closest('#menu'))menu.classList.remove('open');});
          document.addEventListener('keydown',event=>{if(event.key==='Escape')menu.classList.remove('open');});
          menu.classList.add('open');
        }""")
        page.wait_for_function("menu.parentElement.id==='globalOverlayLayer'")
        page.locator("#inside").click()
        inside_result = page.evaluate("({clicks:insideClicks,open:menu.classList.contains('open'),same:menu.querySelector('#inside')===inside})")
        page.keyboard.press("Escape")
        page.wait_for_function("menu.parentElement.id==='home'")
        escaped = page.evaluate("!menu.classList.contains('open')")
        page.evaluate("menu.classList.add('open')")
        page.wait_for_function("menu.parentElement.id==='globalOverlayLayer'")
        page.locator("#outside").click()
        page.wait_for_function("menu.parentElement.id==='home'")
        outside_closed = page.evaluate("!menu.classList.contains('open')")
        browser.close()

    assert inside_result == {"clicks": 1, "open": True, "same": True}
    assert escaped is True
    assert outside_closed is True


def test_live_portaled_overlay_stays_anchored_and_flips_inside_viewport():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - optional browser dependency
        pytest.skip("playwright is unavailable")

    start = UI.index("const _globalOverlayHomes")
    end = UI.index("function _matchBacktickFenceLine", start)
    portal_source = UI[start:end]
    html = """<!doctype html><html><head><style>
      #globalOverlayLayer{position:fixed;inset:0;pointer-events:none}
      .global-overlay-item{pointer-events:auto}
      #trigger{position:fixed;left:300px;top:620px;width:120px;height:32px}
      #menu{display:none;position:absolute;width:260px;height:180px}
      #menu.open{display:block}
    </style></head><body>
      <main class="main showing-browser"></main>
      <button id="trigger">Model</button>
      <div id="menu" data-global-overlay="open"></div>
      <div id="globalOverlayLayer" class="global-overlay-layer"></div>
    </body></html>"""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 900, "height": 700})
        page.set_content(html)
        page.evaluate("window.hermesDesktop={browser:{renderer:'electron-native',isDesktop:true}}")
        page.add_script_tag(content=portal_source)
        page.evaluate("""() => {
          menu.classList.add('open');
          _positionGlobalOverlayFromAnchor(menu,trigger,6,{placement:'top',align:'start'});
        }""")
        page.wait_for_function("menu.parentElement.id==='globalOverlayLayer'")
        anchored = page.evaluate("""() => {
          const popup=menu.getBoundingClientRect();
          const button=trigger.getBoundingClientRect();
          return {left:popup.left,delta:Math.abs(popup.bottom-(button.top-6)),position:getComputedStyle(menu).position};
        }""")

        page.evaluate("trigger.style.left='520px';trigger.style.top='360px';document.dispatchEvent(new Event('scroll'))")
        page.wait_for_function("Math.abs(menu.getBoundingClientRect().left-520)<2")
        moved = page.evaluate("Math.abs(menu.getBoundingClientRect().bottom-(trigger.getBoundingClientRect().top-6))")

        page.evaluate("trigger.style.top='10px';window.dispatchEvent(new Event('resize'))")
        page.wait_for_function("menu.dataset.globalOverlayPlacement==='bottom'")
        flipped = page.evaluate("""() => Math.abs(menu.getBoundingClientRect().top-(trigger.getBoundingClientRect().bottom+6))""")

        page.set_viewport_size({"width": 420, "height": 700})
        page.evaluate("trigger.style.left='370px';window.dispatchEvent(new Event('resize'))")
        page.wait_for_timeout(50)
        shifted = page.evaluate("""() => ({left:menu.getBoundingClientRect().left,right:menu.getBoundingClientRect().right,width:innerWidth})""")
        browser.close()

    assert anchored["position"] == "fixed"
    assert abs(anchored["left"] - 300) < 2
    assert anchored["delta"] < 2
    assert moved < 2
    assert flipped < 2
    assert shifted["left"] >= 8
    assert shifted["right"] <= shifted["width"] - 8
