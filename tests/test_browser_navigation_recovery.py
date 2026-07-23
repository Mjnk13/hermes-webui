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


def test_limited_iframe_backend_does_not_show_browser_view_unavailable_warning():
    js = _read("static/browser_workbench.js")
    apply_capabilities = _js_function(js, "applyCapabilities")
    supports_view = _js_function(js, "browserWorkbenchCapabilityPayloadSupportsView")
    program = "\n".join(
        [
            "const events=[];let workbenchUiEnabled=false;let workbenchCapabilities={};",
            "const tab={id:'browser-tab-1'};const getActiveWorkbenchTab=()=>tab;",
            "const applyBrowserWorkbenchAvailability=value=>events.push(`availability:${value}`);",
            "const setStatus=message=>events.push(`status:${message}`);",
            "const browserWorkbenchClearStatus=(_tab,options)=>events.push(`clear:${options.owner}`);",
            supports_view,
            apply_capabilities,
            "applyCapabilities({enabled:false,ui_enabled:true,status:'limited',backend:'session-shell',capabilities:{session_lifecycle:true,navigation:true,iframe_bridge:true}});",
            "console.log(JSON.stringify(events));",
        ]
    )

    assert _run_node_json(program) == [
        "availability:true",
        "clear:availability",
    ]


def test_capability_refresh_resolves_cleanly_for_usable_limited_iframe_backend():
    js = _read("static/browser_workbench.js")
    refresh = "async " + _js_function(js, "refreshBrowserWorkbenchCapabilities")
    supports_view = _js_function(js, "browserWorkbenchCapabilityPayloadSupportsView")
    program = "\n".join(
        [
            "const resolutions=[];const active={id:'browser-tab-1'};",
            "const payload={enabled:false,ui_enabled:true,status:'limited',backend:'session-shell',capabilities:{session_lifecycle:true,navigation:true,iframe_bridge:true}};",
            "const getActiveWorkbenchTab=()=>active;const setStatus=()=> 'availability-token';",
            "const prepareDesktopBrowserBridge=async()=>{};const fetchCapabilities=async()=>payload;",
            "const applyCapabilities=data=>data;",
            "const browserWorkbenchResolveStatus=(token,message,options)=>resolutions.push({token,message,options:options||null});",
            supports_view,
            refresh,
            "refreshBrowserWorkbenchCapabilities().then(()=>console.log(JSON.stringify(resolutions))).catch(error=>{console.error(error);process.exit(1);});",
        ]
    )

    assert _run_node_json(program) == [
        {"token": "availability-token", "message": "", "options": None}
    ]


def test_capability_view_support_fails_closed_without_a_render_transport():
    js = _read("static/browser_workbench.js")
    supports_view = _js_function(js, "browserWorkbenchCapabilityPayloadSupportsView")
    program = "\n".join(
        [
            supports_view,
            "const base={ui_enabled:true,enabled:false,capabilities:{session_lifecycle:true,navigation:true}};",
            "console.log(JSON.stringify({none:browserWorkbenchCapabilityPayloadSupportsView(base),iframe:browserWorkbenchCapabilityPayloadSupportsView({...base,capabilities:{...base.capabilities,iframe_bridge:true}}),disabled:browserWorkbenchCapabilityPayloadSupportsView({...base,ui_enabled:false,capabilities:{...base.capabilities,iframe_bridge:true}})}));",
        ]
    )

    assert _run_node_json(program) == {
        "none": False,
        "iframe": True,
        "disabled": False,
    }


def test_restored_suspended_tab_reload_materializes_from_saved_url():
    js = _read("static/browser_workbench.js")

    assert "function browserWorkbenchCanReload" in js
    assert "workbenchCapabilities.navigation===true&&browserWorkbenchCanReload(active)" in js
    assert "if(normalized==='reload'&&!target.sessionId)" in js
    assert "return navigateBrowserWorkbenchToUrl(target.id,retryUrl)" in js
    assert "Open a Browser Workbench session before using navigation controls." not in js[js.index("async function navigateBrowserWorkbenchHistory"):js.index("async function maybeStartBrowserWorkbenchInitialLoadOnActivation")]


def test_iframe_reload_uses_live_frame_location_instead_of_stale_backend_url():
    js = _read("static/browser_workbench.js")
    navigate_iframe_history = _js_function(js, "navigateBrowserWorkbenchIframeHistory")
    program = "\n".join(
        [
            "const calls=[];",
            "const frame={contentWindow:{location:{reload:()=>calls.push('reload')},history:{back:()=>calls.push('back'),forward:()=>calls.push('forward')}}};",
            "const activeBrowserWorkbenchIframe=()=>frame;",
            navigate_iframe_history,
            "const tab={renderer:'iframe-bridge',surfaceNode:{}};",
            "console.log(JSON.stringify({reload:navigateBrowserWorkbenchIframeHistory(tab,'reload'),back:navigateBrowserWorkbenchIframeHistory(tab,'back'),forward:navigateBrowserWorkbenchIframeHistory(tab,'forward'),calls}));",
        ]
    )

    assert _run_node_json(program) == {
        "reload": True,
        "back": True,
        "forward": True,
        "calls": ["reload", "back", "forward"],
    }


def test_iframe_metadata_updates_live_history_button_capabilities():
    js = _read("static/browser_workbench.js")
    handler = _js_function(js, "handleBrowserWorkbenchIframeBridgeMessage")
    program = "\n".join(
        [
            "const source={};",
            "const BROWSER_WORKBENCH_HISTORY_LIMIT=80;",
            "const window={location:{href:'http://127.0.0.1:8788/'}};",
            "const browserWorkbenchIsBlankUrl=value=>!String(value||'').trim();",
            "const tab={id:'browser-tab-1',sessionId:'bw_history',renderer:'iframe-bridge',loadStatus:'success',canGoBack:false,canGoForward:false,title:'',faviconUrl:'',historyEntries:[],historyIndex:-1};",
            "const getActiveWorkbenchTab=()=>tab;",
            "const activeBrowserWorkbenchIframe=()=>({contentWindow:source});",
            "const browserWorkbenchDevtoolsLiteState=()=>({frameId:'',targetUrl:''});",
            "const syncBrowserWorkbenchTabLocation=(_tab,url)=>{tab.url=url;};",
            "const syncBrowserWorkbenchIframeSurfaceLocation=()=>{};",
            "const renderBrowserWorkbenchTabs=()=>{};const persistBrowserWorkbenchTabs=()=>{};",
            "const renderActiveBrowserWorkbenchView=()=>{};",
            "const updateBrowserWorkbenchActionMenuCapabilities=()=>{};",
            "const setBrowserWorkbenchLoadStatus=()=>{};",
            _js_function(js, "browserWorkbenchSafeHistoryUrl"),
            _js_function(js, "normalizeBrowserWorkbenchTabHistory"),
            _js_function(js, "applyBrowserWorkbenchTabHistoryCapabilities"),
            _js_function(js, "syncBrowserWorkbenchTabHistory"),
            _js_function(js, "syncBrowserWorkbenchNativeHistoryNeighbors"),
            handler,
            "handleBrowserWorkbenchIframeBridgeMessage({source,data:{source:'hermes-browser-workbench-bridge',type:'metadata',sessionId:'bw_history',frameId:'history-frame',url:'https://example.test/next',proxy_url:'/browser-proxy/next',can_go_back:true,can_go_forward:false}});",
            "console.log(JSON.stringify({url:tab.url,canGoBack:tab.canGoBack,canGoForward:tab.canGoForward}));",
        ]
    )

    assert _run_node_json(program) == {
        "url": "https://example.test/next",
        "canGoBack": True,
        "canGoForward": False,
    }


def test_iframe_open_tab_message_creates_only_a_valid_workbench_navigation():
    js = _read("static/browser_workbench.js")
    handler = _js_function(js, "handleBrowserWorkbenchIframeBridgeMessage")
    program = "\n".join(
        [
            "const source={};const wrongSource={};const opened=[];const overlong='https://example.test/'+('a'.repeat(5000));",
            "const window={location:{href:'http://127.0.0.1:8788/'}};",
            "const browserWorkbenchIsBlankUrl=value=>!String(value||'').trim();",
            "const active={id:'browser-tab-1',sessionId:'bw_source',renderer:'iframe-bridge'};",
            "const getActiveWorkbenchTab=()=>active;",
            "const activeBrowserWorkbenchIframe=()=>({contentWindow:source});",
            "const browserWorkbenchDevtoolsLiteState=()=>null;",
            "const openBrowserWorkbenchUrlInNewTab=url=>opened.push(url);",
            _js_function(js, "browserWorkbenchSafeHistoryUrl"),
            handler,
            "handleBrowserWorkbenchIframeBridgeMessage({source,data:{source:'hermes-browser-workbench-bridge',type:'open-tab',sessionId:'bw_source',url:'https://example.test/docs'}});",
            "handleBrowserWorkbenchIframeBridgeMessage({source,data:{source:'hermes-browser-workbench-bridge',type:'open-tab',sessionId:'bw_source',url:'javascript:alert(1)'}});",
            "handleBrowserWorkbenchIframeBridgeMessage({source,data:{source:'hermes-browser-workbench-bridge',type:'open-tab',sessionId:'bw_source',url:'https://user:secret@example.test/private'}});",
            "handleBrowserWorkbenchIframeBridgeMessage({source,data:{source:'hermes-browser-workbench-bridge',type:'open-tab',sessionId:'bw_source',url:overlong}});",
            "handleBrowserWorkbenchIframeBridgeMessage({source,data:{source:'hermes-browser-workbench-bridge',type:'open-tab',sessionId:'bw_other',url:'https://example.test/wrong-session'}});",
            "handleBrowserWorkbenchIframeBridgeMessage({source:wrongSource,data:{source:'hermes-browser-workbench-bridge',type:'open-tab',sessionId:'bw_source',url:'https://example.test/wrong-frame'}});",
            "console.log(JSON.stringify(opened));",
        ]
    )

    assert _run_node_json(program) == ["https://example.test/docs"]


def test_open_tab_navigation_creates_activates_and_navigates_a_workbench_tab():
    js = _read("static/browser_workbench.js")
    open_new_tab = "async " + _js_function(js, "openBrowserWorkbenchUrlInNewTab")
    program = "\n".join(
        [
            "const calls=[];const window={location:{href:'http://127.0.0.1:8788/'}};",
            "const browserWorkbenchIsBlankUrl=value=>!String(value||'').trim();const wireDom=()=>{};",
            "const createBrowserWorkbenchTabRecord=(options={})=>{calls.push(['create',options.url||'']);return {id:'browser-tab-7'};};",
            "const activateBrowserWorkbenchTab=(id,options)=>calls.push(['activate',id,options.switchPanel]);",
            "const switchPanel=async panel=>calls.push(['panel',panel]);",
            "const navigateBrowserWorkbenchToUrl=async(id,url)=>calls.push(['navigate',id,url]);",
            _js_function(js, "browserWorkbenchSafeHistoryUrl"),
            open_new_tab,
            "(async()=>{const invalid=await openBrowserWorkbenchUrlInNewTab('javascript:alert(1)');const overlong=await openBrowserWorkbenchUrlInNewTab('https://example.test/'+('a'.repeat(5000)));const opened=await openBrowserWorkbenchUrlInNewTab('https://example.test/docs');console.log(JSON.stringify({invalid,overlong,opened:opened&&opened.id,calls}));})().catch(error=>{console.error(error);process.exit(1);});",
        ]
    )

    assert _run_node_json(program) == {
        "invalid": None,
        "overlong": None,
        "opened": "browser-tab-7",
        "calls": [
            ["create", ""],
            ["activate", "browser-tab-7", False],
            ["panel", "browser"],
            ["navigate", "browser-tab-7", "https://example.test/docs"],
        ],
    }


def test_restored_tab_route_history_round_trips_through_local_storage_payload():
    js = _read("static/browser_workbench.js")
    persisted = _js_function(js, "persistedTabsPayload")
    normalize = _js_function(js, "normalizePersistedTab")
    program = "\n".join(
        [
            "const BROWSER_WORKBENCH_TAB_ID_PREFIX='browser-tab-';",
            "const BROWSER_WORKBENCH_MIN_DEVTOOLS_WIDTH=280;",
            "const BROWSER_WORKBENCH_DEFAULT_DEVTOOLS_WIDTH=420;",
            "const BROWSER_WORKBENCH_HISTORY_LIMIT=80;",
            "const window={location:{href:'http://127.0.0.1:8788/'}};",
            "const browserWorkbenchIsBlankUrl=value=>!String(value||'').trim();",
            "const activeBrowserWorkbenchTabId='browser-tab-1';",
            "const nextBrowserWorkbenchTabNumber=2;",
            "const workbenchCapabilities={navigation:true};",
            "const normalizeBrowserWorkbenchLoadStatus=value=>String(value||'idle');",
            "const document={querySelector:()=>null};",
            "const workbenchTabs=new Map([['browser-tab-1',{id:'browser-tab-1',number:1,label:'Browser',url:'https://example.test/c',title:'C',faviconUrl:'',zoom:1,loadStatus:'success',currentUrl:'https://example.test/c',requestedUrl:'https://example.test/c',lastLoadedUrl:'https://example.test/c',hasStartedLoad:true,hasCommittedNavigation:true,lastError:'',devtoolsOpen:false,devtoolsUrl:'',devtoolsWidth:420,canGoBack:true,canGoForward:false,historyEntries:['https://example.test/a','https://example.test/b','https://example.test/c'],historyIndex:2}]]);",
            _js_function(js, "browserWorkbenchSafeHistoryUrl"),
            _js_function(js, "normalizeBrowserWorkbenchTabHistory"),
            _js_function(js, "browserWorkbenchHistoryControlEnabled"),
            persisted,
            normalize,
            "const raw=persistedTabsPayload().tabs[0];",
            "const restored=normalizePersistedTab(raw);",
            "console.log(JSON.stringify({rawEntries:raw.history_entries,rawIndex:raw.history_index,restoredEntries:restored.historyEntries,restoredIndex:restored.historyIndex,restoredBackEnabled:browserWorkbenchHistoryControlEnabled({...restored,sessionId:''},'back'),restoredForwardEnabled:browserWorkbenchHistoryControlEnabled({...restored,sessionId:''},'forward')}));",
        ]
    )

    assert _run_node_json(program) == {
        "rawEntries": [
            "https://example.test/a",
            "https://example.test/b",
            "https://example.test/c",
        ],
        "rawIndex": 2,
        "restoredEntries": [
            "https://example.test/a",
            "https://example.test/b",
            "https://example.test/c",
        ],
        "restoredIndex": 2,
        "restoredBackEnabled": True,
        "restoredForwardEnabled": False,
    }


def test_iframe_back_metadata_keeps_persisted_forward_route_enabled():
    js = _read("static/browser_workbench.js")
    handler = _js_function(js, "handleBrowserWorkbenchIframeBridgeMessage")
    program = "\n".join(
        [
            "const source={};",
            "const BROWSER_WORKBENCH_HISTORY_LIMIT=80;",
            "const window={location:{href:'http://127.0.0.1:8788/'}};",
            "const browserWorkbenchIsBlankUrl=value=>!String(value||'').trim();",
            "const tab={id:'browser-tab-1',sessionId:'bw_history',renderer:'iframe-bridge',loadStatus:'success',canGoBack:true,canGoForward:true,title:'',faviconUrl:'',historyEntries:['https://example.test/a','https://example.test/b','https://example.test/c'],historyIndex:1};",
            "const getActiveWorkbenchTab=()=>tab;",
            "const activeBrowserWorkbenchIframe=()=>({contentWindow:source});",
            "const browserWorkbenchDevtoolsLiteState=()=>({frameId:'',targetUrl:''});",
            "const syncBrowserWorkbenchTabLocation=(_tab,url)=>{tab.url=url;};",
            "const syncBrowserWorkbenchIframeSurfaceLocation=()=>{};",
            "const renderBrowserWorkbenchTabs=()=>{};const persistBrowserWorkbenchTabs=()=>{};",
            "const renderActiveBrowserWorkbenchView=()=>{};",
            "const updateBrowserWorkbenchActionMenuCapabilities=()=>{};",
            "const setBrowserWorkbenchLoadStatus=()=>{};",
            _js_function(js, "browserWorkbenchSafeHistoryUrl"),
            _js_function(js, "normalizeBrowserWorkbenchTabHistory"),
            _js_function(js, "applyBrowserWorkbenchTabHistoryCapabilities"),
            _js_function(js, "syncBrowserWorkbenchTabHistory"),
            _js_function(js, "syncBrowserWorkbenchNativeHistoryNeighbors"),
            handler,
            "handleBrowserWorkbenchIframeBridgeMessage({source,data:{source:'hermes-browser-workbench-bridge',type:'metadata',sessionId:'bw_history',frameId:'history-frame',url:'https://example.test/b',proxy_url:'/browser-proxy/b',can_go_back:true,can_go_forward:false}});",
            "console.log(JSON.stringify({url:tab.url,historyIndex:tab.historyIndex,canGoBack:tab.canGoBack,canGoForward:tab.canGoForward}));",
        ]
    )

    assert _run_node_json(program) == {
        "url": "https://example.test/b",
        "historyIndex": 1,
        "canGoBack": True,
        "canGoForward": True,
    }


def test_tab_history_traversal_preserves_forward_tail_until_new_navigation():
    js = _read("static/browser_workbench.js")
    program = "\n".join(
        [
            "const BROWSER_WORKBENCH_HISTORY_LIMIT=80;",
            "const window={location:{href:'http://127.0.0.1:8788/'}};",
            "const browserWorkbenchIsBlankUrl=value=>!String(value||'').trim();",
            "const getActiveWorkbenchTab=()=>null;",
            "const clearTimeout=()=>{};",
            _js_function(js, "browserWorkbenchSafeHistoryUrl"),
            _js_function(js, "normalizeBrowserWorkbenchTabHistory"),
            _js_function(js, "applyBrowserWorkbenchTabHistoryCapabilities"),
            _js_function(js, "clearBrowserWorkbenchPendingHistoryTraversal"),
            _js_function(js, "syncBrowserWorkbenchTabHistory"),
            "const tab={historyEntries:['https://example.test/a','https://example.test/b','https://example.test/c'],historyIndex:2,pendingHistoryTraversal:{index:1,url:'https://example.test/b'},historyTraversalTimer:null,nativeCanGoBack:false,nativeCanGoForward:false};",
            "syncBrowserWorkbenchTabHistory(tab,'https://example.test/b');",
            "const afterBack={entries:[...tab.historyEntries],index:tab.historyIndex,canForward:tab.canGoForward};",
            "syncBrowserWorkbenchTabHistory(tab,'https://example.test/d');",
            "const afterNewNavigation={entries:[...tab.historyEntries],index:tab.historyIndex,canForward:tab.canGoForward};",
            "syncBrowserWorkbenchTabHistory(tab,'https://example.test/b',{mode:'traverse',nativePreviousUrl:'https://example.test/a',nativeNextUrl:'https://example.test/d'});",
            "const afterPageTraversal={entries:[...tab.historyEntries],index:tab.historyIndex,canForward:tab.canGoForward};",
            "console.log(JSON.stringify({afterBack,afterNewNavigation,afterPageTraversal}));",
        ]
    )

    assert _run_node_json(program) == {
        "afterBack": {
            "entries": [
                "https://example.test/a",
                "https://example.test/b",
                "https://example.test/c",
            ],
            "index": 1,
            "canForward": True,
        },
        "afterNewNavigation": {
            "entries": [
                "https://example.test/a",
                "https://example.test/b",
                "https://example.test/d",
            ],
            "index": 2,
            "canForward": False,
        },
        "afterPageTraversal": {
            "entries": [
                "https://example.test/a",
                "https://example.test/b",
                "https://example.test/d",
            ],
            "index": 1,
            "canForward": True,
        },
    }


def test_restored_route_uses_native_history_only_for_the_same_adjacent_url():
    js = _read("static/browser_workbench.js")
    program = "\n".join(
        [
            "const BROWSER_WORKBENCH_HISTORY_LIMIT=80;",
            "const window={location:{href:'http://127.0.0.1:8788/'}};",
            "const browserWorkbenchIsBlankUrl=value=>!String(value||'').trim();",
            "const getActiveWorkbenchTab=()=>null;",
            _js_function(js, "browserWorkbenchSafeHistoryUrl"),
            _js_function(js, "normalizeBrowserWorkbenchTabHistory"),
            _js_function(js, "browserWorkbenchTabHistoryTarget"),
            _js_function(js, "browserWorkbenchCanUseNativeHistoryTarget"),
            "const tab={url:'https://example.test/c',currentUrl:'https://example.test/c',historyEntries:['https://example.test/a','https://example.test/b','https://example.test/c'],historyIndex:2,nativeHistoryPreviousUrl:'',nativeHistoryNextUrl:''};",
            "const back=browserWorkbenchTabHistoryTarget(tab,'back');",
            "const afterReload=browserWorkbenchCanUseNativeHistoryTarget(tab,'back',back);",
            "tab.nativeHistoryPreviousUrl='https://example.test/b';",
            "const liveMatch=browserWorkbenchCanUseNativeHistoryTarget(tab,'back',back);",
            "tab.currentUrl=tab.url='https://example.test/b';tab.historyIndex=1;tab.nativeHistoryNextUrl='';",
            "const forward=browserWorkbenchTabHistoryTarget(tab,'forward');",
            "const forwardAfterReload=browserWorkbenchCanUseNativeHistoryTarget(tab,'forward',forward);",
            "console.log(JSON.stringify({back,afterReload,liveMatch,forward,forwardAfterReload}));",
        ]
    )

    assert _run_node_json(program) == {
        "back": {"index": 1, "url": "https://example.test/b"},
        "afterReload": False,
        "liveMatch": True,
        "forward": {"index": 2, "url": "https://example.test/c"},
        "forwardAfterReload": False,
    }


def test_history_controls_rematerialize_saved_routes_when_native_history_is_empty():
    js = _read("static/browser_workbench.js")
    navigate_history = "async " + _js_function(js, "navigateBrowserWorkbenchHistory")
    program = "\n".join(
        [
            "const calls=[];",
            "const tab={id:'browser-tab-1',sessionId:'',url:'https://example.test/c',currentUrl:'https://example.test/c',loadStatus:'success',nativeCanGoBack:false,nativeCanGoForward:false};",
            "const wireDom=()=>{};const tabById=()=>tab;const getActiveWorkbenchTab=()=>tab;",
            "const browserWorkbenchRetryUrl=()=>tab.currentUrl;",
            "const browserWorkbenchTabHistoryTarget=(_tab,action)=>action==='back'?{index:1,url:'https://example.test/b'}:{index:2,url:'https://example.test/c'};",
            "const browserWorkbenchCanUseNativeHistoryTarget=()=>false;",
            "const navigateBrowserWorkbenchToUrl=async(id,url,options)=>{calls.push({id,url,options});return {ok:true};};",
            navigate_history,
            "(async()=>{await navigateBrowserWorkbenchHistory('back',tab.id);tab.url=tab.currentUrl='https://example.test/b';await navigateBrowserWorkbenchHistory('forward',tab.id);console.log(JSON.stringify(calls));})().catch(error=>{console.error(error);process.exit(1);});",
        ]
    )

    assert _run_node_json(program) == [
        {
            "id": "browser-tab-1",
            "url": "https://example.test/b",
            "options": {
                "historyTraversal": {
                    "index": 1,
                    "url": "https://example.test/b",
                }
            },
        },
        {
            "id": "browser-tab-1",
            "url": "https://example.test/c",
            "options": {
                "historyTraversal": {
                    "index": 2,
                    "url": "https://example.test/c",
                }
            },
        },
    ]


def test_iframe_surface_is_reused_only_while_its_bridge_url_is_current():
    js = _read("static/browser_workbench.js")
    program = "\n".join(
        [
            "class FakeNode {",
            "  constructor(tag='div'){this.tagName=tag.toUpperCase();this.nodeType=1;this.parentNode=null;this.childNodes=[];this.dataset={};this.hidden=false;this.attributes={};this.listeners={};this.className='';this.classList={add:(name)=>{this.className+=(this.className?' ':'')+name;}};}",
            "  appendChild(node){if(node.parentNode)node.parentNode.removeChild(node);this.childNodes.push(node);node.parentNode=this;return node;}",
            "  removeChild(node){const index=this.childNodes.indexOf(node);if(index!==-1)this.childNodes.splice(index,1);node.parentNode=null;return node;}",
            "  setAttribute(name,value){this.attributes[name]=String(value);}",
            "  addEventListener(type,callback){(this.listeners[type]||(this.listeners[type]=[])).push(callback);}",
            "  matches(selector){return selector==='.browser-workbench-frame-wrap[data-browser-workbench-tab-id]'&&this.className.includes('browser-workbench-frame-wrap')&&!!this.dataset.browserWorkbenchTabId;}",
            "  querySelector(selector){return this.childNodes.find((node)=>node.matches&&node.matches('.browser-workbench-frame-wrap[data-browser-workbench-tab-id]'))||null;}",
            "  querySelectorAll(selector){return this.childNodes.filter((node)=>node.matches&&node.matches(selector));}",
            "}",
            "const CSS={escape:(value)=>String(value)};",
            "const window={location:{href:'http://127.0.0.1:8788/session/test',origin:'http://127.0.0.1:8788'}};",
            "const document={createElement:(tag)=>new FakeNode(tag)};",
            "const viewportEl=null;",
            "let browserWorkbenchOverlayPreview=null;",
            "const stopBrowserWorkbenchChromiumStream=()=>{};",
            "const hideBrowserWorkbenchNativeView=()=>{};",
            "const wireDom=()=>{};",
            "const applyBrowserWorkbenchSurfaceZoom=()=>{};",
            "const browserWorkbenchDisplayLabel=()=>\"Browser\";",
            "const syncBrowserWorkbenchIframeSelectionMode=()=>{};",
            "const loadedLocations=[];",
            "const syncBrowserWorkbenchIframeLoadedLocation=(loadedTab,frame)=>loadedLocations.push({tabId:loadedTab.id,frameUrl:frame.contentWindow.location.href});",
            "const statusUpdates=[];",
            "const setBrowserWorkbenchLoadStatus=(status)=>statusUpdates.push(status);",
            "const textEl=(tag,className)=>{const node=new FakeNode(tag);node.className=className;return node;};",
            "const host=new FakeNode('main');",
            "const tab={id:'browser-tab-1',url:'http://localhost:39001',bridgeUrl:'/browser-proxy/http://localhost:39001?frame=first',renderer:'iframe-bridge',loadStatus:'loading',surfaceNode:null,surfaceUrl:''};",
            "const tabById=(id)=>id===tab.id?tab:null;",
            _js_function(js, "stashBrowserWorkbenchSurface"),
            _js_function(js, "removeBrowserWorkbenchStoredSurface"),
            _js_function(js, "clearBrowserWorkbenchHostForRender"),
            _js_function(js, "syncBrowserWorkbenchIframeSurfaceLocation"),
            _js_function(js, "renderBrowserWorkbenchFrame"),
            "renderBrowserWorkbenchFrame(tab,host);",
            "const oldSurface=tab.surfaceNode;",
            "const oldFrame=oldSurface.childNodes.find((node)=>node.tagName==='IFRAME');",
            "renderBrowserWorkbenchFrame(tab,host);",
            "const sameUrlSurface=tab.surfaceNode;",
            "tab.url='http://localhost:39001/dashboard';",
            "syncBrowserWorkbenchIframeSurfaceLocation(tab,'http://127.0.0.1:8788/browser-proxy/http://localhost:39001/dashboard?__hermes_bw_session=bw_test&__hermes_bw_frame=first');",
            "renderBrowserWorkbenchFrame(tab,host);",
            "const spaSurfaceReused=tab.surfaceNode===oldSurface;",
            "tab.url='http://localhost:39002';",
            "tab.bridgeUrl='/browser-proxy/http://localhost:39002?frame=second';",
            "renderBrowserWorkbenchFrame(tab,host);",
            "const currentSurface=tab.surfaceNode;",
            "const currentFrame=currentSurface.childNodes.find((node)=>node.tagName==='IFRAME');",
            "currentFrame.contentWindow={location:{href:'http://127.0.0.1:8788/browser-proxy/http://localhost:39002'}};",
            "currentFrame.listeners.load[0]();",
            "oldFrame.listeners.load[0]();",
            "console.log(JSON.stringify({sameUrlReused:sameUrlSurface===oldSurface,spaSurfaceReused,surfaceUrl:tab.surfaceUrl,wrapperUrl:currentSurface.dataset.browserWorkbenchUrl,frameUrl:currentFrame&&currentFrame.src,loadedLocations,statusUpdates}));",
        ]
    )

    result = _run_node_json(program)
    assert result == {
        "sameUrlReused": True,
        "spaSurfaceReused": True,
        "surfaceUrl": "/browser-proxy/http://localhost:39002?frame=second",
        "wrapperUrl": "/browser-proxy/http://localhost:39002?frame=second",
        "frameUrl": "/browser-proxy/http://localhost:39002?frame=second",
        "loadedLocations": [
            {
                "tabId": "browser-tab-1",
                "frameUrl": "http://127.0.0.1:8788/browser-proxy/http://localhost:39002",
            }
        ],
        "statusUpdates": ["success"],
    }


def test_iframe_load_recovers_destination_url_without_bridge_metadata():
    js = _read("static/browser_workbench.js")
    program = "\n".join(
        [
            "const window={location:{href:'http://127.0.0.1:8788/session/test',origin:'http://127.0.0.1:8788'}};",
            "const document={activeElement:null};",
            "const activeBrowserWorkbenchTabId='browser-tab-1';",
            "const urlInput={value:'http://localhost:3000/kr/support'};",
            "const browserWorkbenchUrlInputEditingTabId='';",
            "const isBrowserWorkbenchUrlInputEditing=()=>false;",
            "const historyUpdates=[];const renders=[];let persists=0;",
            "const syncBrowserWorkbenchTabHistory=(_tab,url)=>historyUpdates.push(url);",
            "const renderBrowserWorkbenchTabs=()=>renders.push('tabs');",
            "const renderActiveBrowserWorkbenchView=()=>renders.push('active');",
            "const persistBrowserWorkbenchTabs=()=>{persists+=1;};",
            "const getActiveWorkbenchTab=()=>tab;",
            "const tab={id:'browser-tab-1',sessionId:'bw_test',renderer:'iframe-bridge',url:'http://localhost:3000/kr/support',currentUrl:'http://localhost:3000/kr/support',requestedUrl:'http://localhost:3000/kr/support',lastLoadedUrl:'http://localhost:3000/kr/support',surfaceNode:{dataset:{}}};",
            _js_function(js, "syncBrowserWorkbenchTabLocation"),
            _js_function(js, "syncBrowserWorkbenchIframeSurfaceLocation"),
            _js_function(js, "browserWorkbenchTargetUrlFromProxyLocation"),
            _js_function(js, "syncBrowserWorkbenchIframeLoadedLocation"),
            "const mismatch=syncBrowserWorkbenchIframeLoadedLocation(tab,{contentWindow:{location:{href:'http://127.0.0.1:8788/browser-proxy/_hermes/bw_other/frame/http://localhost:3000/ignored'}}});",
            "const proxyUrl='http://127.0.0.1:8788/browser-proxy/_hermes/bw_test/frame/http://localhost:3000/kr/support/faq?category=account#answer';",
            "const matched=syncBrowserWorkbenchIframeLoadedLocation(tab,{contentWindow:{location:{href:proxyUrl}},contentDocument:{title:'FAQ'}});",
            "console.log(JSON.stringify({mismatch,matched,url:tab.url,currentUrl:tab.currentUrl,requestedUrl:tab.requestedUrl,lastLoadedUrl:tab.lastLoadedUrl,clientNavigatedUrl:tab.clientNavigatedUrl,title:tab.title,urlInput:urlInput.value,historyUpdates,renders,persists,bridgeUrl:tab.bridgeUrl,surfaceUrl:tab.surfaceUrl}));",
        ]
    )

    assert _run_node_json(program) == {
        "mismatch": False,
        "matched": True,
        "url": "http://localhost:3000/kr/support/faq?category=account#answer",
        "currentUrl": "http://localhost:3000/kr/support/faq?category=account#answer",
        "requestedUrl": "http://localhost:3000/kr/support/faq?category=account#answer",
        "lastLoadedUrl": "http://localhost:3000/kr/support/faq?category=account#answer",
        "clientNavigatedUrl": "http://localhost:3000/kr/support/faq?category=account#answer",
        "title": "FAQ",
        "urlInput": "http://localhost:3000/kr/support/faq?category=account#answer",
        "historyUpdates": [
            "http://localhost:3000/kr/support/faq?category=account#answer"
        ],
        "renders": ["tabs", "active"],
        "persists": 1,
        "bridgeUrl": "/browser-proxy/_hermes/bw_test/frame/http://localhost:3000/kr/support/faq?category=account#answer",
        "surfaceUrl": "/browser-proxy/_hermes/bw_test/frame/http://localhost:3000/kr/support/faq?category=account#answer",
    }


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
            "const BROWSER_WORKBENCH_HISTORY_LIMIT=80;",
            "const window={location:{href:'http://127.0.0.1:8788/'}};",
            "const browserWorkbenchIsBlankUrl=value=>!String(value||'').trim();",
            "const normalizeBrowserWorkbenchLoadStatus=value=>String(value||'idle');",
            _js_function(js, "browserWorkbenchSafeHistoryUrl"),
            _js_function(js, "normalizeBrowserWorkbenchTabHistory"),
            normalize,
            "const restored=normalizePersistedTab({id:'browser-tab-1',number:1,url:'https://example.test/latest',current_url:'https://example.test/latest',requested_url:'https://example.test/old'});",
            "const legacyBlankUrl=normalizePersistedTab({id:'browser-tab-2',number:2,url:'',current_url:'https://example.test/current-only',requested_url:'https://example.test/old'});",
            "console.log(JSON.stringify({restored,legacyBlankUrl}));",
        ]
    )

    result = _run_node_json(program)
    restored = result["restored"]
    assert restored["url"] == "https://example.test/latest"
    assert restored["currentUrl"] == "https://example.test/latest"
    assert restored["requestedUrl"] == "https://example.test/latest"
    assert result["legacyBlankUrl"]["url"] == "https://example.test/current-only"
    assert result["legacyBlankUrl"]["currentUrl"] == "https://example.test/current-only"
    assert result["legacyBlankUrl"]["requestedUrl"] == "https://example.test/current-only"


def test_restored_active_tab_remains_suspended_after_webui_reload():
    js = _read("static/browser_workbench.js")
    program = "\n".join(
        [
            "let workbenchUiEnabled=true;const calls=[];const BROWSER_WORKBENCH_CONTENT_STATES=new Set(['restored','idle','loading','loaded','error']);",
            "const restored={id:'browser-tab-1',url:'https://example.test/unrelated',currentUrl:'http://localhost:3000/en/solutions',requestedUrl:'http://localhost:3000/en/solutions',lastLoadedUrl:'http://localhost:3000/en/solutions',loadStatus:'idle',contentState:'restored',hasStartedLoad:true,hasCommittedNavigation:true,sessionId:'',openingPromise:null};",
            "const unrelated={id:'browser-tab-2',url:'https://example.test/unrelated',currentUrl:'https://example.test/unrelated',loadStatus:'success',contentState:'loaded',hasStartedLoad:true,hasCommittedNavigation:true,sessionId:'bw_other'};",
            "const tabs=new Map([[restored.id,restored],[unrelated.id,unrelated]]);",
            "const activeBrowserWorkbenchTabId=restored.id;let editing=false;const urlInput={value:'http://localhost:3000/manually-typed'};",
            "const wireDom=()=>{};const tabById=id=>tabs.get(id);const getActiveWorkbenchTab=()=>restored;const isBrowserWorkbenchUrlInputEditing=()=>editing;",
            "const browserWorkbenchIsBlankUrl=value=>!String(value||'').trim();",
            _js_function(js, "normalizeBrowserWorkbenchLoadStatus"),
            _js_function(js, "normalizeBrowserWorkbenchContentState"),
            _js_function(js, "browserWorkbenchActivationUrl"),
            _js_function(js, "requestedUrlForTab"),
            _js_function(js, "shouldStartBrowserWorkbenchInitialLoadOnActivation"),
            "const refreshBrowserWorkbenchCapabilities=async()=>{};const setStatus=()=>{};",
            "const startBrowserWorkbenchSession=async id=>{const tab=tabById(id);calls.push({id,url:requestedUrlForTab(tab)});return {ok:true};};",
            "async " + _js_function(js, "maybeStartBrowserWorkbenchInitialLoadOnActivation"),
            "maybeStartBrowserWorkbenchInitialLoadOnActivation(restored.id).then(()=>{editing=true;console.log(JSON.stringify({calls,typedUrl:requestedUrlForTab(restored),contentState:restored.contentState}));}).catch(error=>{console.error(error);process.exit(1);});",
        ]
    )

    assert _run_node_json(program) == {
        "calls": [],
        "typedUrl": "http://localhost:3000/manually-typed",
        "contentState": "restored",
    }


def test_switching_devtools_tabs_materializes_the_selected_surface_before_its_panel():
    js = _read("static/browser_workbench.js")
    ensure_split = _js_function(js, "ensureBrowserWorkbenchSplitViewPreservingSurface")
    program = "\n".join(
        [
            "const calls=[];let splitPresent=true;",
            "const node=tag=>({tag,className:'',style:{setProperty(){}},classList:{add(){}},setAttribute(){},addEventListener(){}});",
            "const viewportEl={style:{setProperty(){}},classList:{add(){}},getBoundingClientRect:()=>({width:1200}),querySelector(selector){if(selector==='.browser-workbench-split-wrap'&&splitPresent)return {className:'old-split'};return null;},appendChild(){}};",
            "const document={createElement:node};const wireDom=()=>{};",
            "const detachBrowserWorkbenchSplitPreservingSurface=()=>{splitPresent=false;calls.push('detach');return true;};",
            "const clampBrowserWorkbenchDevtoolsWidth=()=>420;const startBrowserWorkbenchDevtoolsResize=()=>{};",
            "const renderBrowserWorkbenchSurface=tab=>calls.push(`surface:${tab.id}`);",
            "const renderBrowserWorkbenchDevtools=tab=>calls.push(`devtools:${tab.id}`);",
            ensure_split,
            "ensureBrowserWorkbenchSplitViewPreservingSurface({id:'browser-tab-2',renderer:'iframe-bridge',bridgeUrl:'/browser-proxy/two',devtoolsOpen:true,devtoolsUrl:'/chii/two'});",
            "console.log(JSON.stringify(calls));",
        ]
    )

    assert _run_node_json(program) == [
        "detach",
        "surface:browser-tab-2",
        "devtools:browser-tab-2",
    ]

    assert "allow-popups" not in _js_function(js, "renderBrowserWorkbenchFrame")


def test_late_devtools_response_never_projects_an_inactive_tab():
    js = _read("static/browser_workbench.js")
    sync_devtools = _js_function(js, "syncBrowserWorkbenchIframeDevtoolsLite")
    open_devtools = "async " + _js_function(js, "openBrowserWorkbenchDevtools")
    program = "\n".join(
        [
            "const projections=[];let activeBrowserWorkbenchTabId='browser-tab-a';let resolveRequest=null;",
            "const tabA={id:'browser-tab-a',sessionId:'session-a',renderer:'iframe-bridge',devtoolsOpen:false,devtoolsUrl:''};",
            "const tabB={id:'browser-tab-b',sessionId:'session-b',renderer:'iframe-bridge',devtoolsOpen:false,devtoolsUrl:''};",
            "const tabs=new Map([[tabA.id,tabA],[tabB.id,tabB]]);",
            "const wireDom=()=>{};const viewportEl={};const tabById=id=>tabs.get(id)||null;",
            "const getActiveWorkbenchTab=()=>tabById(activeBrowserWorkbenchTabId);",
            "const ensureBrowserWorkbenchSplitViewPreservingSurface=tab=>{projections.push(tab.id);return true;};",
            "const detachBrowserWorkbenchSplitPreservingSurface=()=>true;",
            "const requestJSON=()=>new Promise(resolve=>{resolveRequest=resolve;});",
            "const sessionStatusUrl=id=>'/session/'+id;const browserWorkbenchRequestBody=value=>value;",
            "const setStatus=()=>({});const browserWorkbenchResolveStatus=()=>{};",
            "const persistBrowserWorkbenchTabs=()=>{};const syncBrowserWorkbenchIframeSelectionMode=()=>{};",
            "const activeBrowserWorkbenchIframe=()=>null;",
            "const window={location:{origin:'http://127.0.0.1:8788'},open:()=>null};",
            sync_devtools,
            open_devtools,
            "(async()=>{",
            "const pendingWhileA=openBrowserWorkbenchDevtools({mode:'panel'});",
            "activeBrowserWorkbenchTabId=tabB.id;",
            "resolveRequest({devtools_url:'http://127.0.0.1:8080/chii-a'});",
            "await pendingWhileA;",
            "const afterSwitch={aOpen:tabA.devtoolsOpen,aUrl:tabA.devtoolsUrl,projections:projections.slice()};",
            "activeBrowserWorkbenchTabId=tabA.id;",
            "const pendingWhileActive=openBrowserWorkbenchDevtools({mode:'panel'});",
            "resolveRequest({devtools_url:'http://127.0.0.1:8080/chii-a-current'});",
            "await pendingWhileActive;",
            "console.log(JSON.stringify({afterSwitch,projections}));",
            "})().catch(error=>{console.error(error);process.exitCode=1;});",
        ]
    )

    assert _run_node_json(program) == {
        "afterSwitch": {
            "aOpen": True,
            "aUrl": "http://127.0.0.1:8080/chii-a",
            "projections": [],
        },
        "projections": ["browser-tab-a"],
    }


def test_render_host_cleanup_releases_docked_devtools_layout_without_losing_surfaces():
    js = _read("static/browser_workbench.js")
    stash_surface = _js_function(js, "stashBrowserWorkbenchSurface")
    clear_host = _js_function(js, "clearBrowserWorkbenchHostForRender")
    program = "\n".join(
        [
            "const classValues=new Set(['has-devtools-docked','has-rendered-browser']);",
            "const styleValues=new Map([['--browser-workbench-devtools-width','420px']]);",
            "const host={childNodes:[],classList:{remove:value=>classValues.delete(value)},style:{removeProperty:value=>styleValues.delete(value)},querySelectorAll:()=>host.childNodes.filter(node=>node.isFrame),removeChild:node=>{host.childNodes=host.childNodes.filter(item=>item!==node);node.parentNode=null;}};",
            "const frame={nodeType:1,isFrame:true,parentNode:host,hidden:false,dataset:{browserWorkbenchTabId:'browser-tab-a',browserWorkbenchUrl:'https://example.test/a'},matches:selector=>selector.includes('browser-workbench-frame-wrap'),setAttribute:(name,value)=>{frame.ariaHidden=value;}};",
            "const panel={nodeType:1,isFrame:false,parentNode:host,matches:()=>false};",
            "const resizer={nodeType:1,isFrame:false,parentNode:host,matches:()=>false};",
            "host.childNodes=[frame,panel,resizer];",
            "const tab={id:'browser-tab-a',surfaceNode:null,surfaceUrl:''};",
            "const tabById=id=>id===tab.id?tab:null;const viewportEl=host;",
            stash_surface,
            clear_host,
            "clearBrowserWorkbenchHostForRender(host);",
            "console.log(JSON.stringify({remaining:host.childNodes.map(node=>node===frame?'frame':'other'),frameHidden:frame.hidden,ariaHidden:frame.ariaHidden,surfaceStored:tab.surfaceNode===frame,surfaceUrl:tab.surfaceUrl,docked:classValues.has('has-devtools-docked'),width:styleValues.has('--browser-workbench-devtools-width')}));",
        ]
    )

    assert _run_node_json(program) == {
        "remaining": ["frame"],
        "frameHidden": True,
        "ariaHidden": "true",
        "surfaceStored": True,
        "surfaceUrl": "https://example.test/a",
        "docked": False,
        "width": False,
    }


def test_active_iframe_resolves_the_selected_tab_surface_instead_of_the_first_cached_frame():
    js = _read("static/browser_workbench.js")
    active_iframe = _js_function(js, "activeBrowserWorkbenchIframe")
    program = "\n".join(
        [
            "const frameA={id:'frame-a'};const frameB={id:'frame-b'};",
            "const tabB={id:'browser-tab-b',surfaceNode:{querySelector:()=>frameB}};",
            "const getActiveWorkbenchTab=()=>tabB;",
            "const viewportEl={querySelector:()=>frameA};",
            active_iframe,
            "console.log(JSON.stringify(activeBrowserWorkbenchIframe()));",
        ]
    )

    assert _run_node_json(program) == {"id": "frame-b"}


def test_surface_zoom_mutates_the_selected_tabs_owned_iframe_not_the_first_cached_frame():
    js = _read("static/browser_workbench.js")
    apply_zoom = _js_function(js, "applyBrowserWorkbenchSurfaceZoom")
    program = "\n".join(
        [
            "const frameA={style:{transform:'scale(1.5)',transformOrigin:'0 0',width:'66%',height:'66%',flex:'0 0 auto'}};",
            "const frameB={style:{}};",
            "const tabB={id:'browser-tab-b',zoom:1.25,surfaceNode:{querySelector:selector=>selector==='.browser-workbench-frame'?frameB:null}};",
            "const getActiveWorkbenchTab=()=>tabB;",
            "const viewportEl={querySelector:()=>frameA};",
            apply_zoom,
            "applyBrowserWorkbenchSurfaceZoom(tabB,viewportEl);",
            "console.log(JSON.stringify({frameA:frameA.style,frameB:frameB.style}));",
        ]
    )

    assert _run_node_json(program) == {
        "frameA": {
            "transform": "scale(1.5)",
            "transformOrigin": "0 0",
            "width": "66%",
            "height": "66%",
            "flex": "0 0 auto",
        },
        "frameB": {
            "transform": "scale(1.25)",
            "transformOrigin": "0 0",
            "width": "80%",
            "height": "80%",
            "flex": "0 0 auto",
        },
    }


def test_stop_loading_targets_the_selected_tabs_owned_iframe_not_the_first_cached_frame():
    js = _read("static/browser_workbench.js")
    stop_frame = _js_function(js, "stopBrowserWorkbenchEmbeddedFrame")
    program = "\n".join(
        [
            "const calls=[];",
            "const frameA={contentWindow:{stop:()=>calls.push('frame-a')}};",
            "const frameB={contentWindow:{stop:()=>calls.push('frame-b')}};",
            "const tabB={id:'browser-tab-b',renderer:'iframe-bridge',surfaceNode:{querySelector:selector=>selector==='.browser-workbench-frame'?frameB:null}};",
            "const viewportEl={querySelector:()=>frameA};",
            stop_frame,
            "stopBrowserWorkbenchEmbeddedFrame(tabB);",
            "console.log(JSON.stringify(calls));",
        ]
    )

    assert _run_node_json(program) == ["frame-b"]


def test_iframe_hover_highlight_projects_layout_rect_through_visual_zoom():
    js = _read("static/browser_workbench.js")
    program = "\n".join(
        [
            "const BROWSER_WORKBENCH_SELECTION_LABEL_SAFE_PADDING=8;",
            "const BROWSER_WORKBENCH_SELECTION_LABEL_GAP=6;",
            "let browserWorkbenchOverlayPreview=null;",
            "class FakeNode {",
            "  constructor(tag='div'){this.tagName=tag.toUpperCase();this.className='';this.style={};this.dataset={};this.children=[];this.parentNode=null;this.offsetWidth=80;this.offsetHeight=20;this.rect={left:0,top:0,width:80,height:20};}",
            "  appendChild(node){this.children.push(node);node.parentNode=this;return node;}",
            "  append(...nodes){nodes.forEach(node=>this.appendChild(node));}",
            "  remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(node=>node!==this);this.parentNode=null;}",
            "  getBoundingClientRect(){return this.rect;}",
            "}",
            "const host=new FakeNode('main');host.rect={left:40,top:20,width:1000,height:750};",
            "const iframe={clientWidth:800,clientHeight:600,contentWindow:{innerWidth:800,innerHeight:600},getBoundingClientRect:()=>({left:40,top:20,width:1000,height:750}),closest:()=>host};",
            "const active={id:'browser-tab-b',renderer:'iframe-bridge',zoom:1.25};",
            "const getActiveWorkbenchTab=()=>active;const activeBrowserWorkbenchIframe=()=>iframe;",
            "const currentBrowserWorkbenchViewport=()=>({width:1000,height:750});",
            "const wireDom=()=>{};",
            "const viewportEl={querySelector:()=>null,querySelectorAll:()=>[]};",
            "const document={createElement:tag=>new FakeNode(tag)};",
            "const textEl=(tag,className,text)=>{const node=new FakeNode(tag);node.className=className;node.textContent=text;return node;};",
            _js_function(js, "clearBrowserWorkbenchOverlay"),
            _js_function(js, "clampBrowserWorkbenchOverlayValue"),
            _js_function(js, "positionBrowserWorkbenchOverlayLabel"),
            _js_function(js, "browserWorkbenchHtmlTagName"),
            _js_function(js, "browserWorkbenchElementLabel"),
            _js_function(js, "renderBrowserWorkbenchOverlay"),
            "renderBrowserWorkbenchOverlay({left:80,top:40,width:160,height:80},'Hero · section','hover',{component:'Hero',tag:'section'});",
            "const box=host.children.find(node=>node.className==='browser-workbench-hover-overlay');",
            "console.log(JSON.stringify({left:box.style.left,top:box.style.top,width:box.style.width,height:box.style.height}));",
        ]
    )

    assert _run_node_json(program) == {
        "left": "100px",
        "top": "50px",
        "width": "200px",
        "height": "100px",
    }


def test_zoom_change_immediately_reprojects_the_visible_element_highlight():
    js = _read("static/browser_workbench.js")
    set_zoom = "async " + _js_function(js, "setBrowserWorkbenchZoom")
    program = "\n".join(
        [
            "const calls=[];const active={id:'browser-tab-b',zoom:1};",
            "const getActiveWorkbenchTab=()=>active;",
            "const persistBrowserWorkbenchTabs=()=>calls.push('persist');",
            "const applyBrowserWorkbenchSurfaceZoom=tab=>calls.push(`surface:${tab.zoom}`);",
            "const refreshBrowserWorkbenchOverlayProjection=tab=>calls.push(`overlay:${tab.zoom}`);",
            "const updateBrowserWorkbenchZoomLabel=()=>calls.push('label');",
            "const setStatus=()=>calls.push('status');",
            set_zoom,
            "setBrowserWorkbenchZoom(1.25).then(value=>console.log(JSON.stringify({value,zoom:active.zoom,calls}))).catch(error=>{console.error(error);process.exit(1);});",
        ]
    )

    assert _run_node_json(program) == {
        "value": 1.25,
        "zoom": 1.25,
        "calls": ["persist", "surface:1.25", "overlay:1.25", "label", "status"],
    }


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

    assert "const restoredUrl=browserWorkbenchActivationUrl(tab)" in restore
    assert "contentState:browserWorkbenchIsBlankUrl(restoredUrl)?'idle':'restored'" in restore
    assert "Restored history URL" not in create
    assert js.count("Restored history URL:") == 1
    assert "if(state==='restored')" in js
    assert begin.index("setBrowserWorkbenchContentState(target,'loading')") < begin.index("markBrowserWorkbenchLoadStarted")
    assert "contentState==='error'&&active.navigationError" in render
    assert "contentState==='restored'||contentState==='error'" in render
    restored_branch = "else if(active&&(contentState==='restored'||contentState==='error'))"
    stream_render_branch = "else if(active&&active.renderer==='chromium-stream'&&active.sessionId)"
    assert render.index(restored_branch) < render.index(stream_render_branch)


def test_element_highlight_label_formatter_preserves_complete_tag_names():
    js = _read("static/browser_workbench.js")
    css = _read("static/style.css")
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
