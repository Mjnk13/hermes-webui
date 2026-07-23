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
            "const stopBrowserWorkbenchChromiumStream=()=>{};",
            "const hideBrowserWorkbenchNativeView=()=>{};",
            "const wireDom=()=>{};",
            "const applyBrowserWorkbenchSurfaceZoom=()=>{};",
            "const browserWorkbenchDisplayLabel=()=>\"Browser\";",
            "const syncBrowserWorkbenchIframeSelectionMode=()=>{};",
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
            "oldFrame.listeners.load[0]();",
            "console.log(JSON.stringify({sameUrlReused:sameUrlSurface===oldSurface,spaSurfaceReused,surfaceUrl:tab.surfaceUrl,wrapperUrl:currentSurface.dataset.browserWorkbenchUrl,frameUrl:currentFrame&&currentFrame.src,statusUpdates}));",
        ]
    )

    result = _run_node_json(program)
    assert result == {
        "sameUrlReused": True,
        "spaSurfaceReused": True,
        "surfaceUrl": "/browser-proxy/http://localhost:39002?frame=second",
        "wrapperUrl": "/browser-proxy/http://localhost:39002?frame=second",
        "frameUrl": "/browser-proxy/http://localhost:39002?frame=second",
        "statusUpdates": [],
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
