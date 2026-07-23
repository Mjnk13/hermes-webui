(function(){
  'use strict';

  const WORKBENCH_CAPABILITIES_URL='api/browser-workbench/capabilities';
  const WORKBENCH_SESSION_URL='/api/browser-workbench/session';
  const WORKBENCH_STORAGE_KEY='hermes-browser-workbench-tabs:v1';
  const WORKBENCH_HISTORY_STORAGE_KEY='hermes-browser-workbench-history:v1';
  // Prefix every WebUI-owned tab id so it cannot collide with host-page or
  // browser automation globals.
  const BROWSER_WORKBENCH_TAB_ID_PREFIX='workbench-browser-tab-';
  let tabsEl=null;
  let openerButton=null;
  let titleEl=null;
  let statusEl=null;
  let urlInput=null;
  let urlSuggestionsEl=null;
  let backButton=null;
  let forwardButton=null;
  let reloadButton=null;
  let pingButton=null;
  let menuButton=null;
  let menuEl=null;
  let menuZoomInput=null;
  let viewportEl=null;
  let workbenchUiEnabled=false;
  let workbenchCapabilities={};
  const workbenchTabs=new Map();
  let activeBrowserWorkbenchTabId='';
  let nextBrowserWorkbenchTabNumber=1;
  let restoredBrowserWorkbenchPanel=false;
  let restoringBrowserWorkbenchTabs=false;
  let draggedBrowserWorkbenchTabId='';
  let selectionMode=false;
  let selectionModeTabId='';

  let lastComposerSelection={start:0,end:0};
  let hoverInspectTimer=null;
  let hoverInspectRequestId=0;
  let hoverInspectPointKey='';
  let browserWorkbenchOverlayPreview=null;
  let interactionRequestId=0;
  let pendingClickTimer=null;
  let chromiumFrameTimer=null;
  let chromiumFrameRequestId=0;
  let areaCaptureMode=false;
  let areaCaptureStart=null;
  let areaCaptureBox=null;
  let suppressNextViewportClick=false;
  let browserWorkbenchUrlInputEditingTabId='';
  let browserWorkbenchUrlSuggestionItems=[];
  let browserWorkbenchUrlSuggestionActiveIndex=-1;
  let browserWorkbenchUrlSuggestionsOpen=false;
  let browserWorkbenchActionsMenuOpen=false;
  let browserWorkbenchActionsMenuBounds=null;
  let browserWorkbenchIframeCaptureRequestId=0;
  const browserWorkbenchIframeCapturePending=new Map();
  const BROWSER_WORKBENCH_HOVER_INSPECT_DELAY_MS=90;
  const BROWSER_WORKBENCH_CLICK_DELAY_MS=180;
  const BROWSER_WORKBENCH_MIN_DEVTOOLS_WIDTH=280;
  const BROWSER_WORKBENCH_DEFAULT_DEVTOOLS_WIDTH=420;
  const BROWSER_WORKBENCH_FRAME_POLL_MS=700;
  const BROWSER_WORKBENCH_DEVTOOLS_QUIESCE_MS=350;
  const BROWSER_WORKBENCH_RELOAD_SUCCESS_MS=700;
  const BROWSER_WORKBENCH_RELOAD_ERROR_MS=1600;
  const BROWSER_WORKBENCH_HISTORY_LIMIT=80;
  const BROWSER_WORKBENCH_HISTORY_TRAVERSAL_TIMEOUT_MS=5000;
  const BROWSER_WORKBENCH_SUGGESTION_LIMIT=5;
  const BROWSER_WORKBENCH_RESTORED_OPEN_DELAY_MS=180;
  const BROWSER_WORKBENCH_DEVTOOLS_LITE_EVENT_LIMIT=250;
  const BROWSER_WORKBENCH_DEVTOOLS_LITE_NETWORK_LIMIT=160;
  const BROWSER_WORKBENCH_IFRAME_CAPTURE_TIMEOUT_MS=8000;
  const BROWSER_WORKBENCH_IFRAME_FULL_CAPTURE_MAX_HEIGHT=12000;
  const BROWSER_WORKBENCH_IFRAME_FULL_CAPTURE_MAX_PIXELS=25000000;
  const BROWSER_WORKBENCH_IFRAME_CAPTURE_FILENAME='browser-workbench-iframe-screenshot.png';
  const BROWSER_WORKBENCH_IFRAME_AREA_CAPTURE_FILENAME='browser-workbench-iframe-area-screenshot.png';
  const BROWSER_WORKBENCH_IFRAME_FULL_CAPTURE_FILENAME='browser-workbench-iframe-full-page-screenshot.png';
  const BROWSER_WORKBENCH_SELECTION_LABEL_SAFE_PADDING=8;
  const BROWSER_WORKBENCH_SELECTION_LABEL_GAP=6;
  const BROWSER_WORKBENCH_STATUS_FEEDBACK_MS=2400;
  const BROWSER_WORKBENCH_STATUS_PROGRESS_TIMEOUT_MS=45000;
  // Central lifetime policy: persistent entries follow active modes, progress
  // entries have a safety timeout, temporary feedback expires, and errors stay
  // visible until a retry or successful replacement clears their owner.
  const BROWSER_WORKBENCH_STATUS_PRIORITY={info:10,persistent:20,temporary:30,progress:40,error:50};
  const BROWSER_WORKBENCH_CONTENT_STATES=new Set(['restored','idle','loading','loaded','error']);
  let browserWorkbenchStatusSequence=0;

  function delayBrowserWorkbench(ms){
    return new Promise((resolve)=>setTimeout(resolve,ms));
  }

  function textEl(tag,className,text){
    const el=document.createElement(tag);
    if(className)el.className=className;
    if(text!==undefined)el.textContent=text;
    return el;
  }

















  function ensureBrowserWorkbenchUrlSuggestionsPortal(){
    if(!urlSuggestionsEl)return null;
    if(urlSuggestionsEl.parentElement!==document.body)document.body.appendChild(urlSuggestionsEl);
    urlSuggestionsEl.classList.add('browser-workbench-url-suggestions--portal');
    return urlSuggestionsEl;
  }

  function positionBrowserWorkbenchUrlSuggestionsPortal(){
    if(!urlInput||!urlSuggestionsEl||!browserWorkbenchUrlSuggestionsOpen)return;
    const rect=urlInput.getBoundingClientRect();
    const gap=6;
    const top=Math.max(0,Math.round(rect.bottom+gap));
    const left=Math.max(0,Math.round(rect.left));
    const width=Math.max(160,Math.round(rect.width));
    const maxHeight=Math.max(96,Math.min(340,Math.round(window.innerHeight-top-12)));
    urlSuggestionsEl.style.left=`${left}px`;
    urlSuggestionsEl.style.top=`${top}px`;
    urlSuggestionsEl.style.width=`${width}px`;
    urlSuggestionsEl.style.maxHeight=`${maxHeight}px`;
  }





  function browserWorkbenchIframeCaptureReady(tab){
    const target=tab||getActiveWorkbenchTab();
    if(!target||target.renderer!=='iframe-bridge'||!target.sessionId)return false;
    const frame=activeBrowserWorkbenchIframe();
    if(!frame||!frame.contentWindow)return false;
    const state=target.devtoolsLite&&typeof target.devtoolsLite==='object'?target.devtoolsLite:null;
    return target.iframeBridgeReady===true||target.iframeCaptureReady===true||(state&&state.bridgeInjected===true);
  }

  function browserWorkbenchRendererCapabilities(tab){
    const renderer=tab&&tab.renderer?String(tab.renderer):'';
    const hasSession=!!(tab&&tab.sessionId);
    const base={
      takeScreenshot:false,
      captureAreaScreenshot:false,
      takeFullPageScreenshot:false,
      fullPageScreenshotVisible:false,
      devToolsPanel:false,
      popoutDevTools:false,
      hardReload:hasSession,
      copyUrl:!!(tab&&(tab.url||tab.sessionId)),
      clearHistory:hasSession,
      clearCookies:hasSession,
      clearCache:hasSession,
      zoom:!!tab,
      screenshotMessage:'Open a page before taking a screenshot.',
      areaScreenshotMessage:'Open a page before selecting an area.',
      fullPageScreenshotMessage:'Full-page screenshots are unavailable for this page.',
      devtoolsPanelLabel:'Open DevTools Panel'
    };
    if(renderer==='chromium-stream'){
      return {...base,takeScreenshot:hasSession,captureAreaScreenshot:hasSession,devToolsPanel:hasSession,popoutDevTools:false};
    }
    if(renderer==='iframe-bridge'){
      const captureReady=browserWorkbenchIframeCaptureReady(tab);
      const viewportReadyMessage='Capture the visible page.';
      const areaReadyMessage='Capture a selected area.';
      const fullPageReadyMessage='Capture the full page.';
      return {
        ...base,
        takeScreenshot:hasSession&&captureReady,
        captureAreaScreenshot:hasSession&&captureReady,
        takeFullPageScreenshot:hasSession&&captureReady,
        fullPageScreenshotVisible:true,
        devToolsPanel:hasSession,
        popoutDevTools:hasSession,
        devtoolsPanelLabel:'Open DevTools',
        screenshotMessage:captureReady?viewportReadyMessage:'Screenshots are not ready yet.',
        areaScreenshotMessage:captureReady?areaReadyMessage:'Area capture is not ready yet.',
        fullPageScreenshotMessage:captureReady?fullPageReadyMessage:'Full-page capture is not ready yet.'
      };
    }
    return base;
  }

  function browserWorkbenchActionMenuState(action,capabilities){
    const caps=capabilities||browserWorkbenchRendererCapabilities(getActiveWorkbenchTab());
    if(action==='take-screenshot')return {visible:true,enabled:caps.takeScreenshot,message:caps.screenshotMessage};
    if(action==='take-full-page-screenshot')return {visible:caps.fullPageScreenshotVisible===true,enabled:caps.takeFullPageScreenshot,message:caps.fullPageScreenshotMessage};
    if(action==='capture-area-screenshot')return {visible:true,enabled:caps.captureAreaScreenshot,message:caps.areaScreenshotMessage};
    if(action==='open-devtools-panel')return {visible:caps.devToolsPanel,enabled:caps.devToolsPanel,label:caps.devtoolsPanelLabel};
    if(action==='open-devtools-popout')return {visible:caps.popoutDevTools,enabled:caps.popoutDevTools};
    if(action==='hard-reload')return {visible:true,enabled:caps.hardReload};
    if(action==='copy-url')return {visible:true,enabled:caps.copyUrl};
    if(action==='clear-history')return {visible:true,enabled:caps.clearHistory};
    if(action==='clear-cookies')return {visible:true,enabled:caps.clearCookies};
    if(action==='clear-cache')return {visible:true,enabled:caps.clearCache};
    if(action==='zoom-out'||action==='zoom-in'||action==='set-zoom')return {visible:true,enabled:caps.zoom};
    return {visible:true,enabled:true};
  }

  function updateBrowserWorkbenchActionMenuCapabilities(){
    wireDom();
    if(!menuEl)return;
    const caps=browserWorkbenchRendererCapabilities(getActiveWorkbenchTab());
    menuEl.querySelectorAll('[data-browser-action]').forEach((item)=>{
      const action=String(item.dataset.browserAction||'');
      const state=browserWorkbenchActionMenuState(action,caps);
      item.hidden=state.visible===false;
      item.disabled=state.enabled===false;
      item.setAttribute('aria-disabled',state.enabled===false?'true':'false');
      if(state.message)item.title=state.message;
      else item.removeAttribute('title');
      if(state.label){
        const label=item.querySelector('span:last-child');
        if(label)label.textContent=state.label;
      }
    });
    menuEl.querySelectorAll('.browser-workbench-menu-section').forEach((section)=>{
      const actions=Array.from(section.querySelectorAll('[data-browser-action]'));
      if(actions.length)section.hidden=actions.every((item)=>item.hidden);
    });
  }






  function safeLocalStorage(){
    try{return window.localStorage||null;}catch(_){return null;}
  }

  function browserWorkbenchSafeHistoryUrl(value){
    const raw=String(value||'').trim().slice(0,4096);
    if(!raw||browserWorkbenchIsBlankUrl(raw))return '';
    try{
      const parsed=new URL(raw,window.location.href);
      if(parsed.protocol!=='http:'&&parsed.protocol!=='https:')return '';
      if(parsed.username||parsed.password)return '';
      return parsed.href;
    }catch(_){return '';}
  }

  function normalizeBrowserWorkbenchTabHistory(rawEntries,rawIndex,currentUrl){
    const requestedIndex=Number.parseInt(rawIndex,10);
    let index=-1;
    let entries=[];
    (Array.isArray(rawEntries)?rawEntries:[]).forEach((value,rawEntryIndex)=>{
      const safeUrl=browserWorkbenchSafeHistoryUrl(value);
      if(!safeUrl)return;
      entries.push(safeUrl);
      if(rawEntryIndex===requestedIndex)index=entries.length-1;
    });
    const current=browserWorkbenchSafeHistoryUrl(currentUrl);
    if(!Number.isFinite(index)||index<0||index>=entries.length){
      index=current?entries.lastIndexOf(current):-1;
      if(index<0)index=entries.length-1;
    }
    if(current&&(index<0||entries[index]!==current)){
      const matchingIndex=entries.lastIndexOf(current);
      if(matchingIndex>=0)index=matchingIndex;
      else{
        entries.splice(Math.max(0,index+1));
        entries.push(current);
        index=entries.length-1;
      }
    }
    if(entries.length>BROWSER_WORKBENCH_HISTORY_LIMIT){
      const start=Math.max(0,Math.min(entries.length-BROWSER_WORKBENCH_HISTORY_LIMIT,index-Math.floor(BROWSER_WORKBENCH_HISTORY_LIMIT/2)));
      entries=entries.slice(start,start+BROWSER_WORKBENCH_HISTORY_LIMIT);
      index-=start;
    }
    return {entries,index:entries.length?Math.max(0,Math.min(entries.length-1,index)):-1};
  }

  function applyBrowserWorkbenchTabHistoryCapabilities(tab,nativeCanGoBack,nativeCanGoForward){
    const target=tab||getActiveWorkbenchTab();
    if(!target)return {canGoBack:false,canGoForward:false};
    if(nativeCanGoBack!==undefined)target.nativeCanGoBack=nativeCanGoBack===true;
    if(nativeCanGoForward!==undefined)target.nativeCanGoForward=nativeCanGoForward===true;
    const normalized=normalizeBrowserWorkbenchTabHistory(target.historyEntries,target.historyIndex,'');
    target.historyEntries=normalized.entries;
    target.historyIndex=normalized.index;
    const routeCanGoBack=normalized.index>0;
    const routeCanGoForward=normalized.index>=0&&normalized.index<normalized.entries.length-1;
    target.canGoBack=routeCanGoBack||target.nativeCanGoBack===true;
    target.canGoForward=routeCanGoForward||target.nativeCanGoForward===true;
    return {canGoBack:target.canGoBack,canGoForward:target.canGoForward};
  }

  function clearBrowserWorkbenchPendingHistoryTraversal(tab){
    const target=tab||getActiveWorkbenchTab();
    if(!target)return;
    if(target.historyTraversalTimer){
      clearTimeout(target.historyTraversalTimer);
      target.historyTraversalTimer=null;
    }
    target.pendingHistoryTraversal=null;
  }

  function setBrowserWorkbenchPendingHistoryTraversal(tab,index,url){
    const target=tab||getActiveWorkbenchTab();
    const requestedIndex=Number.parseInt(index,10);
    const requestedUrl=browserWorkbenchSafeHistoryUrl(url);
    clearBrowserWorkbenchPendingHistoryTraversal(target);
    if(!target||!Number.isFinite(requestedIndex)||!requestedUrl)return null;
    target.pendingHistoryTraversal={index:requestedIndex,url:requestedUrl};
    target.historyTraversalTimer=setTimeout(()=>{
      target.historyTraversalTimer=null;
      target.pendingHistoryTraversal=null;
      applyBrowserWorkbenchTabHistoryCapabilities(target);
      if(target.id===activeBrowserWorkbenchTabId)renderActiveBrowserWorkbenchView();
      persistBrowserWorkbenchTabs();
    },BROWSER_WORKBENCH_HISTORY_TRAVERSAL_TIMEOUT_MS);
    return target.pendingHistoryTraversal;
  }

  function syncBrowserWorkbenchTabHistory(tab,url,options){
    const target=tab||getActiveWorkbenchTab();
    const safeUrl=browserWorkbenchSafeHistoryUrl(url);
    if(!target||!safeUrl)return false;
    const opts=options&&typeof options==='object'?options:{};
    const normalized=normalizeBrowserWorkbenchTabHistory(target.historyEntries,target.historyIndex,'');
    let entries=normalized.entries;
    let index=normalized.index;
    const pending=target.pendingHistoryTraversal&&typeof target.pendingHistoryTraversal==='object'
      ?target.pendingHistoryTraversal
      :null;
    if(pending&&Number.isFinite(Number(pending.index))){
      if(entries.length){
        const traversalIndex=Math.max(0,Math.min(entries.length-1,Number(pending.index)));
        entries[traversalIndex]=safeUrl;
        index=traversalIndex;
      }else{
        entries=[safeUrl];
        index=0;
      }
      clearBrowserWorkbenchPendingHistoryTraversal(target);
    }else if(opts.mode==='traverse'&&entries[index]!==safeUrl){
      const previous=browserWorkbenchSafeHistoryUrl(opts.nativePreviousUrl);
      const next=browserWorkbenchSafeHistoryUrl(opts.nativeNextUrl);
      const candidates=[];
      entries.forEach((entry,candidateIndex)=>{
        if(entry!==safeUrl)return;
        let score=0;
        if(previous&&entries[candidateIndex-1]===previous)score+=2;
        if(next&&entries[candidateIndex+1]===next)score+=2;
        candidates.push({index:candidateIndex,score,distance:Math.abs(candidateIndex-index)});
      });
      candidates.sort((left,right)=>right.score-left.score||left.distance-right.distance);
      if(candidates.length){
        index=candidates[0].index;
      }else{
        const nextIndex=next?entries.indexOf(next):-1;
        const previousIndex=previous?entries.lastIndexOf(previous):-1;
        if(nextIndex>=0){
          entries.splice(nextIndex,0,safeUrl);
          index=nextIndex;
        }else if(previousIndex>=0){
          entries.splice(previousIndex+1,0,safeUrl);
          index=previousIndex+1;
        }else{
          entries.splice(Math.max(0,index+1),0,safeUrl);
          index=Math.max(0,index+1);
        }
      }
    }else if(opts.mode==='replace'&&index>=0){
      entries[index]=safeUrl;
    }else if(index<0){
      entries=[safeUrl];
      index=0;
    }else if(entries[index]!==safeUrl){
      entries=entries.slice(0,index+1);
      entries.push(safeUrl);
      index=entries.length-1;
    }
    if(entries.length>BROWSER_WORKBENCH_HISTORY_LIMIT){
      const removed=entries.length-BROWSER_WORKBENCH_HISTORY_LIMIT;
      entries=entries.slice(removed);
      index=Math.max(0,index-removed);
    }
    target.historyEntries=entries;
    target.historyIndex=index;
    applyBrowserWorkbenchTabHistoryCapabilities(target);
    return true;
  }

  function browserWorkbenchTabHistoryTarget(tab,action){
    const target=tab||getActiveWorkbenchTab();
    if(!target)return null;
    const normalized=normalizeBrowserWorkbenchTabHistory(target.historyEntries,target.historyIndex,target.currentUrl||target.url);
    target.historyEntries=normalized.entries;
    target.historyIndex=normalized.index;
    const delta=action==='back'?-1:action==='forward'?1:0;
    const index=normalized.index+delta;
    if(!delta||index<0||index>=normalized.entries.length)return null;
    return {index,url:normalized.entries[index]};
  }

  function browserWorkbenchCanUseNativeHistoryTarget(tab,action,historyTarget){
    const target=tab||getActiveWorkbenchTab();
    if(!target||!historyTarget)return false;
    const adjacent=action==='back'?target.nativeHistoryPreviousUrl:action==='forward'?target.nativeHistoryNextUrl:'';
    return !!adjacent&&browserWorkbenchSafeHistoryUrl(adjacent)===historyTarget.url;
  }

  function syncBrowserWorkbenchNativeHistoryNeighbors(tab,currentUrl,previousUrl,nextUrl){
    const target=tab||getActiveWorkbenchTab();
    if(!target)return false;
    const current=browserWorkbenchSafeHistoryUrl(currentUrl);
    const previous=browserWorkbenchSafeHistoryUrl(previousUrl);
    const next=browserWorkbenchSafeHistoryUrl(nextUrl);
    if(!current)return false;
    const normalized=normalizeBrowserWorkbenchTabHistory(target.historyEntries,target.historyIndex,current);
    const entries=normalized.entries;
    let index=normalized.index;
    let changed=false;
    if(previous&&entries[index-1]!==previous&&entries.indexOf(previous)===-1){
      entries.splice(index,0,previous);
      index+=1;
      changed=true;
    }
    if(next&&entries[index+1]!==next&&entries.indexOf(next)===-1){
      entries.splice(index+1,0,next);
      changed=true;
    }
    if(entries.length>BROWSER_WORKBENCH_HISTORY_LIMIT){
      const removed=entries.length-BROWSER_WORKBENCH_HISTORY_LIMIT;
      target.historyEntries=entries.slice(removed);
      target.historyIndex=Math.max(0,index-removed);
    }else{
      target.historyEntries=entries;
      target.historyIndex=index;
    }
    applyBrowserWorkbenchTabHistoryCapabilities(target);
    return changed;
  }

  function browserWorkbenchHostnameForUrl(value){
    try{return new URL(value).hostname.replace(/^www\./i,'');}catch(_){return '';}
  }

  function normalizeBrowserWorkbenchHistoryEntry(raw){
    if(!raw||typeof raw!=='object')return null;
    const url=browserWorkbenchSafeHistoryUrl(raw.url);
    if(!url)return null;
    const title=browserWorkbenchCleanTitle(raw.title);
    const lastVisitedAt=Number(raw.lastVisitedAt||raw.last_visited_at||0)||0;
    const visitCount=Math.max(1,Number.parseInt(raw.visitCount||raw.visit_count,10)||1);
    return {url,title,lastVisitedAt,visitCount};
  }

  function readBrowserWorkbenchHistory(){
    const storage=safeLocalStorage();
    if(!storage)return [];
    let saved=null;
    try{saved=JSON.parse(storage.getItem(WORKBENCH_HISTORY_STORAGE_KEY)||'null');}catch(_){saved=null;}
    const rawEntries=saved&&Array.isArray(saved.entries)?saved.entries:Array.isArray(saved)?saved:[];
    const seen=new Set();
    const entries=[];
    rawEntries.forEach((entry)=>{
      const clean=normalizeBrowserWorkbenchHistoryEntry(entry);
      if(!clean||seen.has(clean.url))return;
      seen.add(clean.url);
      entries.push(clean);
    });
    return entries.sort((a,b)=>(b.lastVisitedAt||0)-(a.lastVisitedAt||0)).slice(0,BROWSER_WORKBENCH_HISTORY_LIMIT);
  }

  function writeBrowserWorkbenchHistory(entries){
    const storage=safeLocalStorage();
    if(!storage)return;
    try{
      const clean=(Array.isArray(entries)?entries:[]).map(normalizeBrowserWorkbenchHistoryEntry).filter(Boolean).sort((a,b)=>(b.lastVisitedAt||0)-(a.lastVisitedAt||0)).slice(0,BROWSER_WORKBENCH_HISTORY_LIMIT);
      if(clean.length===0)storage.removeItem(WORKBENCH_HISTORY_STORAGE_KEY);
      else storage.setItem(WORKBENCH_HISTORY_STORAGE_KEY,JSON.stringify({version:1,entries:clean}));
    }catch(_){/* localStorage may be disabled or full. */}
  }

  function recordBrowserWorkbenchHistory(url,title,options){
    const safeUrl=browserWorkbenchSafeHistoryUrl(url);
    if(!safeUrl)return '';
    const opts=options&&typeof options==='object'?options:{};
    const countVisit=opts.countVisit!==false;
    const entries=readBrowserWorkbenchHistory();
    const existingIndex=entries.findIndex((entry)=>entry.url===safeUrl);
    const existing=existingIndex>=0?entries.splice(existingIndex,1)[0]:null;
    const cleanTitle=browserWorkbenchCleanTitle(title)||(existing&&existing.title)||'';
    const entry={
      url:safeUrl,
      title:cleanTitle,
      lastVisitedAt:countVisit||!existing?Date.now():existing.lastVisitedAt,
      visitCount:(existing&&existing.visitCount||0)+(countVisit?1:0)
    };
    entries.unshift(entry);
    writeBrowserWorkbenchHistory(entries);
    return safeUrl;
  }

  function browserWorkbenchSuggestionPrimary(entry){
    if(!entry)return '';
    return browserWorkbenchCleanTitle(entry.title)||browserWorkbenchHostnameForUrl(entry.url)||entry.url;
  }

  function browserWorkbenchHistoryRank(entry,query){
    const q=String(query||'').trim().toLowerCase();
    if(!q)return 0;
    const url=String(entry&&entry.url||'').toLowerCase();
    const title=String(entry&&entry.title||'').toLowerCase();
    const hostname=browserWorkbenchHostnameForUrl(entry&&entry.url).toLowerCase();
    const schemeLess=url.replace(/^https?:\/\//,'');
    const hostRoot=hostname.split('.')[0]||hostname;
    if(hostname===q||hostRoot===q)return 0;
    if(url.startsWith(q)||schemeLess.startsWith(q))return 1;
    if(title.indexOf(q)!==-1)return 2;
    if(hostname.indexOf(q)!==-1)return 3;
    if(url.indexOf(q)!==-1)return 4;
    return null;
  }

  function browserWorkbenchUrlHistorySuggestions(query){
    const q=String(query||'').trim().toLowerCase();
    return readBrowserWorkbenchHistory().map((entry)=>({entry,rank:browserWorkbenchHistoryRank(entry,q)})).filter((item)=>item.rank!==null).sort((a,b)=>{
      if(a.rank!==b.rank)return a.rank-b.rank;
      if((b.entry.lastVisitedAt||0)!==(a.entry.lastVisitedAt||0))return (b.entry.lastVisitedAt||0)-(a.entry.lastVisitedAt||0);
      return (b.entry.visitCount||0)-(a.entry.visitCount||0);
    }).slice(0,BROWSER_WORKBENCH_SUGGESTION_LIMIT).map((item)=>item.entry);
  }

  function browserWorkbenchUrlSuggestionsVisible(){
    return browserWorkbenchUrlSuggestionsOpen&&browserWorkbenchUrlSuggestionItems.length>0;
  }

  function closeBrowserWorkbenchUrlSuggestions(){
    browserWorkbenchUrlSuggestionsOpen=false;
    if(urlSuggestionsEl)urlSuggestionsEl.hidden=true;
    browserWorkbenchUrlSuggestionItems=[];
    browserWorkbenchUrlSuggestionActiveIndex=-1;
    if(urlInput){
      urlInput.setAttribute('aria-expanded','false');
      urlInput.removeAttribute('aria-activedescendant');
    }
  }

  function setBrowserWorkbenchUrlSuggestionActive(index){
    if(!urlSuggestionsEl)return;
    const count=browserWorkbenchUrlSuggestionItems.length;
    const requested=Number.parseInt(index,10);
    browserWorkbenchUrlSuggestionActiveIndex=Number.isFinite(requested)&&requested>=0&&requested<count?requested:-1;
    if(urlInput)urlInput.removeAttribute('aria-activedescendant');
    Array.from(urlSuggestionsEl.querySelectorAll('.browser-workbench-url-suggestion')).forEach((row,rowIndex)=>{
      const active=rowIndex===browserWorkbenchUrlSuggestionActiveIndex;
      row.classList.toggle('is-active',active);
      row.setAttribute('aria-selected',active?'true':'false');
      if(active){
        if(urlInput)urlInput.setAttribute('aria-activedescendant',row.id);
        if(typeof row.scrollIntoView==='function')row.scrollIntoView({block:'nearest'});
      }
    });
  }

  function moveBrowserWorkbenchUrlSuggestionSelection(delta){
    const count=browserWorkbenchUrlSuggestionItems.length;
    if(count===0)return;
    const direction=Number(delta)||0;
    let next=browserWorkbenchUrlSuggestionActiveIndex;
    if(next<0)next=direction<0?count-1:0;
    else next+=direction<0?-1:1;
    if(next<0||next>=count)next=-1;
    setBrowserWorkbenchUrlSuggestionActive(next);
  }

  function acceptBrowserWorkbenchUrlSuggestion(navigate){
    const suggestion=browserWorkbenchUrlSuggestionItems[browserWorkbenchUrlSuggestionActiveIndex];
    if(!suggestion||!urlInput)return false;
    urlInput.value=suggestion.url;
    closeBrowserWorkbenchUrlSuggestions();
    const active=getActiveWorkbenchTab();
    if(active)browserWorkbenchUrlInputEditingTabId=active.id;
    if(navigate){
      browserWorkbenchUrlInputEditingTabId='';
      urlInput.blur();
      void navigateBrowserWorkbenchToUrl(undefined,suggestion.url);
    }
    return true;
  }

  function renderBrowserWorkbenchUrlSuggestions(){
    wireDom();
    ensureBrowserWorkbenchUrlSuggestionsPortal();
    if(!urlInput||!urlSuggestionsEl||urlInput.disabled){
      closeBrowserWorkbenchUrlSuggestions();
      return;
    }
    const suggestions=browserWorkbenchUrlHistorySuggestions(urlInput.value);
    browserWorkbenchUrlSuggestionItems=suggestions;
    urlSuggestionsEl.textContent='';
    if(suggestions.length===0){
      closeBrowserWorkbenchUrlSuggestions();
      return;
    }
    suggestions.forEach((entry,index)=>{
      const row=document.createElement('button');
      row.type='button';
      row.className='browser-workbench-url-suggestion';
      row.id=`browserWorkbenchUrlSuggestion-${index}`;
      row.setAttribute('role','option');
      row.dataset.index=String(index);
      row.appendChild(textEl('span','browser-workbench-url-suggestion-primary',browserWorkbenchSuggestionPrimary(entry)));
      row.appendChild(textEl('span','browser-workbench-url-suggestion-secondary',entry.url));
      row.addEventListener('mouseenter',()=>setBrowserWorkbenchUrlSuggestionActive(index));
      row.addEventListener('mousedown',(event)=>event.preventDefault());
      row.addEventListener('click',(event)=>{
        event.preventDefault();
        setBrowserWorkbenchUrlSuggestionActive(index);
        acceptBrowserWorkbenchUrlSuggestion(true);
      });
      urlSuggestionsEl.appendChild(row);
    });
    browserWorkbenchUrlSuggestionsOpen=true;
    positionBrowserWorkbenchUrlSuggestionsPortal();
    urlSuggestionsEl.hidden=false;
    urlSuggestionsEl.setAttribute('aria-hidden','false');
    urlInput.setAttribute('aria-expanded','true');
    setBrowserWorkbenchUrlSuggestionActive(-1);
  }

  function persistedTabsPayload(){
    return {
      version:1,
      active_tab_id:activeBrowserWorkbenchTabId,
      active_panel:document.querySelector('main.main.showing-browser')?'browser':'chat',
      next_tab_number:nextBrowserWorkbenchTabNumber,
      tabs:Array.from(workbenchTabs.values()).map((tab)=>({
        id:tab.id,
        number:tab.number,
        label:tab.label,
        url:tab.url||'',
        title:tab.title||'',
        favicon_url:tab.faviconUrl||'',
        zoom:tab.zoom||1,
        load_status:normalizeBrowserWorkbenchLoadStatus(tab.loadStatus),
        current_url:tab.currentUrl||tab.url||'',
        requested_url:tab.requestedUrl||'',
        last_loaded_url:tab.lastLoadedUrl||'',
        has_started_load:tab.hasStartedLoad===true,
        has_committed_navigation:tab.hasCommittedNavigation===true,
        last_error:tab.lastError||tab.loadError||'',
        devtools_open:tab.devtoolsOpen===true,
        devtools_url:tab.devtoolsUrl||'',
        devtools_width:tab.devtoolsWidth||BROWSER_WORKBENCH_DEFAULT_DEVTOOLS_WIDTH,
        can_go_back:tab.canGoBack===true,
        can_go_forward:tab.canGoForward===true,
        history_entries:Array.isArray(tab.historyEntries)?tab.historyEntries.slice(-BROWSER_WORKBENCH_HISTORY_LIMIT):[],
        history_index:Number.isFinite(Number(tab.historyIndex))?Number(tab.historyIndex):-1,
      })),
    };
  }

  function persistBrowserWorkbenchTabs(){
    if(restoringBrowserWorkbenchTabs)return;
    const storage=safeLocalStorage();
    if(!storage)return;
    try{
      if(workbenchTabs.size===0){
        storage.removeItem(WORKBENCH_STORAGE_KEY);
        return;
      }
      storage.setItem(WORKBENCH_STORAGE_KEY,JSON.stringify(persistedTabsPayload()));
    }catch(_){/* localStorage may be disabled or full. */}
  }

  function normalizePersistedTab(raw){
    if(!raw||typeof raw!=='object')return null;
    const number=Number.parseInt(raw.number,10);
    if(!Number.isFinite(number)||number<1)return null;
    const id=String(raw.id||`${BROWSER_WORKBENCH_TAB_ID_PREFIX}${number}`);
    if(!id.startsWith(BROWSER_WORKBENCH_TAB_ID_PREFIX))return null;
    const currentUrl=String(raw.current_url||raw.currentUrl||raw.url||'').slice(0,4096);
    const history=normalizeBrowserWorkbenchTabHistory(
      raw.history_entries||raw.historyEntries,
      raw.history_index!==undefined?raw.history_index:raw.historyIndex,
      currentUrl
    );
    return {
      id,
      number,
      label:'Browser',
      url:currentUrl,
      title:String(raw.title||'').slice(0,200),
      faviconUrl:String(raw.favicon_url||raw.faviconUrl||'').slice(0,4096),
      zoom:Math.max(0.25,Math.min(3,Number.parseFloat(raw.zoom)||1)),
      loadStatus:normalizeBrowserWorkbenchLoadStatus(raw.load_status),
      currentUrl,
      // Older records can retain the originally requested URL after a
      // pushState/replaceState navigation. The visible committed location is
      // the only safe URL to materialize after a process restart.
      requestedUrl:currentUrl||String(raw.requested_url||raw.requestedUrl||'').slice(0,4096),
      lastLoadedUrl:String(raw.last_loaded_url||raw.lastLoadedUrl||'').slice(0,4096),
      hasStartedLoad:raw.has_started_load===true||raw.hasStartedLoad===true,
      hasCommittedNavigation:raw.has_committed_navigation===true||raw.hasCommittedNavigation===true,
      lastError:String(raw.last_error||raw.lastError||'').slice(0,500),
      devtoolsWidth:Math.max(BROWSER_WORKBENCH_MIN_DEVTOOLS_WIDTH,Math.min(900,Number.parseInt(raw.devtools_width,10)||BROWSER_WORKBENCH_DEFAULT_DEVTOOLS_WIDTH)),
      canGoBack:history.index>0,
      canGoForward:history.index>=0&&history.index<history.entries.length-1,
      historyEntries:history.entries,
      historyIndex:history.index,
    };
  }

  function restoreBrowserWorkbenchTabs(){
    if(workbenchTabs.size>0)return null;
    const storage=safeLocalStorage();
    if(!storage)return null;
    let saved=null;
    try{saved=JSON.parse(storage.getItem(WORKBENCH_STORAGE_KEY)||'null');}catch(_){saved=null;}
    const savedTabs=saved&&Array.isArray(saved.tabs)?saved.tabs:[];
    if(savedTabs.length===0)return saved;
    restoringBrowserWorkbenchTabs=true;
    try{
      let maxNumber=0;
      savedTabs.slice(0,12).forEach((entry)=>{
        const tab=normalizePersistedTab(entry);
        if(!tab)return;
        maxNumber=Math.max(maxNumber,tab.number);
        const restoredUrl=browserWorkbenchActivationUrl(tab);
        createBrowserWorkbenchTabRecord({
          id:tab.id,
          number:tab.number,
          label:tab.label,
          url:restoredUrl,
          title:tab.title,
          faviconUrl:tab.faviconUrl,
          zoom:tab.zoom,
          loadStatus:'idle',
          contentState:browserWorkbenchIsBlankUrl(restoredUrl)?'idle':'restored',
          currentUrl:tab.currentUrl,
          requestedUrl:tab.requestedUrl,
          lastLoadedUrl:tab.lastLoadedUrl,
          hasStartedLoad:tab.hasStartedLoad,
          hasCommittedNavigation:tab.hasCommittedNavigation,
          lastError:'',
          devtoolsWidth:tab.devtoolsWidth,
          canGoBack:tab.canGoBack,
          canGoForward:tab.canGoForward,
          historyEntries:tab.historyEntries,
          historyIndex:tab.historyIndex,
        });
      });
      const requestedActive=String(saved&&saved.active_tab_id||'');
      activeBrowserWorkbenchTabId=workbenchTabs.has(requestedActive)?requestedActive:Array.from(workbenchTabs.keys())[0]||'';
      const savedNext=Number.parseInt(saved&&saved.next_tab_number,10);
      nextBrowserWorkbenchTabNumber=Math.max(
        Number.isFinite(savedNext)?savedNext:1,
        maxNumber+1,
        nextBrowserWorkbenchTabNumber
      );
      restoredBrowserWorkbenchPanel=saved&&saved.active_panel==='browser'&&!!activeBrowserWorkbenchTabId;
      renderBrowserWorkbenchTabs();
      renderActiveBrowserWorkbenchView();
    }finally{
      restoringBrowserWorkbenchTabs=false;
    }
    return saved;
  }

  function iconSvg(){
    const ns='http://www.w3.org/2000/svg';
    const svg=document.createElementNS(ns,'svg');
    svg.classList.add('workbench-tab-fallback-icon');
    svg.setAttribute('width','14');
    svg.setAttribute('height','14');
    svg.setAttribute('viewBox','0 0 24 24');
    svg.setAttribute('fill','none');
    svg.setAttribute('stroke','currentColor');
    svg.setAttribute('stroke-width','2');
    svg.setAttribute('stroke-linecap','round');
    svg.setAttribute('stroke-linejoin','round');
    svg.setAttribute('aria-hidden','true');
    [['circle',{cx:'12',cy:'12',r:'10'}],['path',{d:'M2 12h20'}],['path',{d:'M12 2a15.3 15.3 0 0 1 0 20'}],['path',{d:'M12 2a15.3 15.3 0 0 0 0 20'}]].forEach(([tag,attrs])=>{
      const node=document.createElementNS(ns,tag);
      Object.entries(attrs).forEach(([key,value])=>node.setAttribute(key,value));
      svg.appendChild(node);
    });
    return svg;
  }

  function browserWorkbenchIsBlankUrl(value){
    const text=String(value||'').trim().toLowerCase();
    return !text||text==='about:blank';
  }

  function browserWorkbenchRetryUrl(tab){
    const target=tab||getActiveWorkbenchTab();
    const errorUrl=target&&target.navigationError&&target.navigationError.validated_url;
    return String(errorUrl||browserWorkbenchActivationUrl(target)||'').trim();
  }

  function browserWorkbenchCanReload(tab){
    const raw=browserWorkbenchRetryUrl(tab);
    if(browserWorkbenchIsBlankUrl(raw))return false;
    try{
      const normalized=/^[a-z][a-z0-9+.-]*:/i.test(raw)?raw:`http://${raw}`;
      const parsed=new URL(normalized);
      return (parsed.protocol==='http:'||parsed.protocol==='https:')&&!parsed.username&&!parsed.password&&!!parsed.hostname;
    }catch(_){return false;}
  }

  function browserWorkbenchHistoryControlEnabled(tab,action){
    const target=tab||getActiveWorkbenchTab();
    if(!target||workbenchCapabilities.navigation!==true)return false;
    if(action==='back')return target.canGoBack===true;
    if(action==='forward')return target.canGoForward===true;
    return false;
  }

  function browserWorkbenchCleanTitle(value){
    return String(value||'').replace(/\s+/g,' ').trim().slice(0,120);
  }

  function browserWorkbenchDisplayLabel(tab){
    if(!tab||browserWorkbenchIsBlankUrl(tab.url))return 'Browser';
    return browserWorkbenchCleanTitle(tab.title)||'Browser';
  }

  function browserWorkbenchSafeFaviconUrl(value){
    const raw=String(value||'').trim();
    if(!raw)return '';
    if(/^data:image\//i.test(raw))return raw.slice(0,4096);
    try{
      const parsed=new URL(raw,window.location.href);
      return parsed.protocol==='http:'||parsed.protocol==='https:'?parsed.href:'';
    }catch(_){return '';}
  }

  function browserWorkbenchTabIconNode(tab){
    const faviconUrl=browserWorkbenchIsBlankUrl(tab&&tab.url)?'':browserWorkbenchSafeFaviconUrl(tab&&tab.faviconUrl);
    if(!faviconUrl)return iconSvg();
    const img=new Image(14,14);
    img.className='workbench-tab-favicon';
    img.alt='';
    img.setAttribute('aria-hidden','true');
    img.decoding='async';
    img.referrerPolicy='no-referrer';
    img.src=faviconUrl;
    img.addEventListener('error',()=>{
      if(img.parentNode)img.replaceWith(iconSvg());
    },{once:true});
    return img;
  }

  function updateBrowserWorkbenchTabIcon(tab){
    if(!tab||!tab.tabEl)return;
    const current=tab.tabEl.querySelector('.workbench-tab-favicon,.workbench-tab-fallback-icon');
    const desired=browserWorkbenchTabIconNode(tab);
    const currentSrc=current&&current.classList&&current.classList.contains('workbench-tab-favicon')?current.getAttribute('src')||'':'';
    const desiredSrc=desired.classList&&desired.classList.contains('workbench-tab-favicon')?desired.getAttribute('src')||'':'';
    if(current&&(current.getAttribute('class')||'')===(desired.getAttribute('class')||'')&&currentSrc===desiredSrc)return;
    if(current)current.replaceWith(desired);
    else tab.tabEl.insertBefore(desired,tab.tabEl.firstChild);
  }





  function cacheBrowserWorkbenchDom(){
    tabsEl=document.getElementById('browserWorkbenchTabs');
    openerButton=document.getElementById('workbenchOpenBrowser');
    titleEl=document.getElementById('browserWorkbenchTitle');
    statusEl=document.getElementById('browserWorkbenchStatus');
    urlInput=document.getElementById('browserWorkbenchUrl');
    urlSuggestionsEl=document.getElementById('browserWorkbenchUrlSuggestions');
    backButton=document.getElementById('browserWorkbenchBack');
    forwardButton=document.getElementById('browserWorkbenchForward');
    reloadButton=document.getElementById('browserWorkbenchReload');
    pingButton=document.getElementById('browserWorkbenchPing');
    menuButton=document.getElementById('browserWorkbenchMenuButton');
    menuEl=document.getElementById('browserWorkbenchMenu');
    menuZoomInput=document.getElementById('browserWorkbenchMenuZoomInput');
    viewportEl=document.getElementById('browserWorkbenchViewport');
  }

  function wireBrowserWorkbenchMenuControls(){
    if(menuButton&&!menuButton.dataset.browserWorkbenchWired){
      menuButton.dataset.browserWorkbenchWired='1';
      menuButton.addEventListener('click',(event)=>{
        event.preventDefault();
        event.stopPropagation();
        void toggleBrowserWorkbenchMenu();
      });
    }
    if(menuEl&&!menuEl.dataset.browserWorkbenchWired){
      menuEl.dataset.browserWorkbenchWired='1';
      ['pointerdown','mousedown','mouseup'].forEach((eventName)=>{
        menuEl.addEventListener(eventName,(event)=>{
          event.stopPropagation();
          if(event.target&&event.target.closest&&event.target.closest('[data-browser-action]'))event.preventDefault();
        });
      });
      menuEl.addEventListener('click',(event)=>{
        const action=event.target&&event.target.closest?event.target.closest('[data-browser-action]'):null;
        if(!action)return;
        event.preventDefault();
        event.stopPropagation();
        if(action.disabled||action.hidden){
          const active=getActiveWorkbenchTab();
          if(action.title)setStatus(action.title,'warning',active,{owner:'action',kind:'error'});
          return;
        }
        handleBrowserWorkbenchMenuAction(action.dataset.browserAction);
      });
    }
    if(pingButton&&!pingButton.dataset.browserWorkbenchWired){
      pingButton.dataset.browserWorkbenchWired='1';
      pingButton.addEventListener('click',()=>toggleBrowserWorkbenchSelectionMode());
    }
    wireBrowserWorkbenchZoomInput(menuZoomInput);
  }

  function wireBrowserWorkbenchViewport(){
    if(!viewportEl||viewportEl.dataset.browserWorkbenchWired)return;
    viewportEl.dataset.browserWorkbenchWired='1';
    viewportEl.addEventListener('mousemove',(event)=>{
      updateBrowserWorkbenchHoverOverlay(event);
    });
    viewportEl.addEventListener('mouseleave',()=>{
      cancelBrowserWorkbenchHoverInspect();
      clearBrowserWorkbenchOverlay('hover');
    });
    viewportEl.addEventListener('click',(event)=>{
      if(!selectionMode||selectionModeTabId!==activeBrowserWorkbenchTabId)return;
      event.preventDefault();
      void pingBrowserWorkbenchSelection(event).catch(()=>{
        setBrowserWorkbenchSelectionMode(false);
        setStatus('Element selection failed.','warning',getActiveWorkbenchTab(),{owner:'selection',kind:'error'});
      });
    });
    viewportEl.addEventListener('click',handleBrowserWorkbenchViewportClick);
    viewportEl.addEventListener('dblclick',handleBrowserWorkbenchViewportDoubleClick);
    viewportEl.addEventListener('wheel',handleBrowserWorkbenchViewportWheel,{passive:false});
    viewportEl.addEventListener('keydown',handleBrowserWorkbenchViewportKeydown);
    viewportEl.addEventListener('pointerdown',handleBrowserWorkbenchAreaPointerDown);
    viewportEl.addEventListener('pointermove',handleBrowserWorkbenchAreaPointerMove);
    viewportEl.addEventListener('pointerup',handleBrowserWorkbenchAreaPointerUp);
    viewportEl.addEventListener('pointercancel',()=>cancelBrowserWorkbenchAreaCapture());
  }

  function handleBrowserWorkbenchUrlInputKeydown(event){
    if(event.key==='ArrowDown'){
      if(!browserWorkbenchUrlSuggestionsVisible())renderBrowserWorkbenchUrlSuggestions();
      if(browserWorkbenchUrlSuggestionsVisible()){
        event.preventDefault();
        moveBrowserWorkbenchUrlSuggestionSelection(1);
      }
    }else if(event.key==='ArrowUp'){
      if(!browserWorkbenchUrlSuggestionsVisible())renderBrowserWorkbenchUrlSuggestions();
      if(browserWorkbenchUrlSuggestionsVisible()){
        event.preventDefault();
        moveBrowserWorkbenchUrlSuggestionSelection(-1);
      }
    }else if(event.key==='Enter'){
      event.preventDefault();
      if(browserWorkbenchUrlSuggestionsVisible()&&acceptBrowserWorkbenchUrlSuggestion(true))return;
      browserWorkbenchUrlInputEditingTabId='';
      const requested=urlInput.value;
      urlInput.blur();
      navigateBrowserWorkbenchToUrl(undefined,requested);
    }else if(event.key==='Escape'){
      if(browserWorkbenchUrlSuggestionsVisible()){
        event.preventDefault();
        event.stopPropagation();
        closeBrowserWorkbenchUrlSuggestions();
        return;
      }
      browserWorkbenchUrlInputEditingTabId='';
      const active=getActiveWorkbenchTab();
      urlInput.value=active?active.url||'':'';
      urlInput.blur();
    }else if(event.key==='Tab'&&browserWorkbenchUrlSuggestionsVisible()){
      if(acceptBrowserWorkbenchUrlSuggestion(false))event.preventDefault();
    }
  }

  function wireBrowserWorkbenchUrlControls(){
    if(urlInput&&!urlInput.dataset.browserWorkbenchWired){
      urlInput.dataset.browserWorkbenchWired='1';
      urlInput.addEventListener('focus',()=>{
        const active=getActiveWorkbenchTab();
        browserWorkbenchUrlInputEditingTabId=active?active.id:'';
        renderBrowserWorkbenchUrlSuggestions();
      });
      urlInput.addEventListener('input',()=>{
        const active=getActiveWorkbenchTab();
        if(active)browserWorkbenchUrlInputEditingTabId=active.id;
        renderBrowserWorkbenchUrlSuggestions();
      });
      urlInput.addEventListener('keydown',handleBrowserWorkbenchUrlInputKeydown);
      urlInput.addEventListener('blur',()=>{
        setTimeout(()=>{
          closeBrowserWorkbenchUrlSuggestions();
          browserWorkbenchUrlInputEditingTabId='';
          renderActiveBrowserWorkbenchView();
        },0);
      });
    }
    if(!document.documentElement.dataset.browserWorkbenchUrlSuggestionsPortalWired){
      document.documentElement.dataset.browserWorkbenchUrlSuggestionsPortalWired='1';
      const reposition=()=>{
        if(!browserWorkbenchUrlSuggestionsVisible())return;
        positionBrowserWorkbenchUrlSuggestionsPortal();
      };
      window.addEventListener('resize',reposition,{passive:true});
      window.addEventListener('scroll',reposition,{capture:true,passive:true});
    }
    if(backButton&&!backButton.dataset.browserWorkbenchWired){
      backButton.dataset.browserWorkbenchWired='1';
      backButton.addEventListener('click',()=>navigateBrowserWorkbenchHistory('back'));
    }
    if(forwardButton&&!forwardButton.dataset.browserWorkbenchWired){
      forwardButton.dataset.browserWorkbenchWired='1';
      forwardButton.addEventListener('click',()=>navigateBrowserWorkbenchHistory('forward'));
    }
    if(reloadButton&&!reloadButton.dataset.browserWorkbenchWired){
      reloadButton.dataset.browserWorkbenchWired='1';
      reloadButton.addEventListener('click',handleBrowserWorkbenchReloadButtonClick);
    }
  }

  function wireBrowserWorkbenchComposerSelection(){
    const composer=document.getElementById('msg');
    if(!composer||composer.dataset.browserWorkbenchCursorWired)return;
    composer.dataset.browserWorkbenchCursorWired='1';
    ['focus','click','keyup','mouseup','select','input'].forEach((eventName)=>{
      composer.addEventListener(eventName,rememberComposerSelection);
    });
    rememberComposerSelection();
  }

  function wireBrowserWorkbenchGlobalEvents(){
    if(!document.documentElement.dataset.browserWorkbenchShortcutWired){
      document.documentElement.dataset.browserWorkbenchShortcutWired='1';
      document.addEventListener('keydown',handleBrowserWorkbenchShortcut);
    }
    if(!document.documentElement.dataset.browserWorkbenchIframeBridgeWired){
      document.documentElement.dataset.browserWorkbenchIframeBridgeWired='1';
      window.addEventListener('message',handleBrowserWorkbenchIframeBridgeMessage);
      window.addEventListener('message',handleBrowserWorkbenchDevtoolsAgentMessage);
    }
    if(!document.documentElement.dataset.browserWorkbenchMenuDismissWired){
      document.documentElement.dataset.browserWorkbenchMenuDismissWired='1';
      document.addEventListener('click',(event)=>{
        if(browserWorkbenchActionsMenuOpen&&menuButton&&!menuButton.contains(event.target)&&(!menuEl||menuEl.hidden||!menuEl.contains(event.target)))closeBrowserWorkbenchMenu();
      });
      document.addEventListener('keydown',(event)=>{
        if(event.key==='Escape'){
          closeBrowserWorkbenchMenu();
          if(selectionMode)setBrowserWorkbenchSelectionMode(false);
          if(areaCaptureMode)cancelBrowserWorkbenchAreaCapture();
        }
      });
      window.addEventListener('resize',()=>positionBrowserWorkbenchMenu());
      window.addEventListener('scroll',()=>positionBrowserWorkbenchMenu(),true);
    }
    if(!document.documentElement.dataset.browserWorkbenchContextHoverWired){
      document.documentElement.dataset.browserWorkbenchContextHoverWired='1';
      document.addEventListener('browser-workbench-context-hover',(event)=>{
        const detail=event&&event.detail&&typeof event.detail==='object'?event.detail:{};
        if(detail.visible===false){
          clearBrowserWorkbenchOverlay('hover');
          return;
        }
        previewBrowserWorkbenchSelection(detail.item,true);
      });
    }
  }

  function wireDom(){
    cacheBrowserWorkbenchDom();
    wireBrowserWorkbenchMenuControls();
    wireBrowserWorkbenchViewport();
    wireBrowserWorkbenchUrlControls();
    wireBrowserWorkbenchComposerSelection();
    wireBrowserWorkbenchGlobalEvents();
  }

  function handleBrowserWorkbenchShortcut(event){
    if(!event||event.defaultPrevented)return;
    const key=String(event.key||'').toLowerCase();
    if(key==='b'&&event.metaKey&&event.shiftKey&&!event.ctrlKey&&!event.altKey){
      event.preventDefault();
      openBrowserWorkbenchTab();
    }
  }

  function rememberComposerSelection(){
    const input=document.getElementById('msg');
    if(!input)return;
    const value=input.value||'';
    const start=typeof input.selectionStart==='number'?input.selectionStart:value.length;
    const end=typeof input.selectionEnd==='number'?input.selectionEnd:start;
    lastComposerSelection={start,end};
  }

  function getActiveWorkbenchTab(){
    return activeBrowserWorkbenchTabId?workbenchTabs.get(activeBrowserWorkbenchTabId)||null:null;
  }

  function tabById(tabId){
    return tabId?workbenchTabs.get(String(tabId))||null:null;
  }


  function setTabState(state,tab){
    const target=tab||getActiveWorkbenchTab();
    if(!target)return;
    target.state=state||'idle';
    if(target.statusEl)target.statusEl.dataset.state=target.state;
  }

  function normalizeBrowserWorkbenchLoadStatus(value){
    const status=String(value||'idle').toLowerCase();
    return ['idle','loading','success','error'].indexOf(status)!==-1?status:'idle';
  }

  function normalizeBrowserWorkbenchContentState(value){
    const state=String(value||'idle').toLowerCase();
    return BROWSER_WORKBENCH_CONTENT_STATES.has(state)?state:'idle';
  }

  function nextBrowserWorkbenchContentState(tab,loadStatus){
    const status=normalizeBrowserWorkbenchLoadStatus(loadStatus);
    if(status==='loading')return 'loading';
    if(status==='success')return 'loaded';
    if(status==='error')return 'error';
    if(tab&&tab.contentState==='restored'&&tab.hasStartedLoad!==true)return 'restored';
    if(tab&&tab.navigationError)return 'error';
    if(tab&&tab.hasCommittedNavigation===true)return 'loaded';
    return 'idle';
  }

  function setBrowserWorkbenchContentState(tab,state){
    if(!tab)return 'idle';
    tab.contentState=normalizeBrowserWorkbenchContentState(state);
    return tab.contentState;
  }

  function browserWorkbenchContentPlaceholder(tab){
    if(!tab)return 'Click + Browser to open a Browser Workbench tab.';
    const state=normalizeBrowserWorkbenchContentState(tab.contentState);
    const url=String(tab.requestedUrl||tab.url||tab.currentUrl||'').trim();
    if(state==='restored')return url?`Restored history URL: ${url}`:'Enter an address to open a page.';
    if(state==='loading')return url?`Opening ${url}…`:'Opening page…';
    if(state==='error')return url?`Couldn’t open ${url}.`:'This page could not be opened.';
    if(state==='loaded')return tab.viewportMessage||'Browser view is unavailable.';
    return tab.viewportMessage||'Enter an address to open a page.';
  }

  function normalizeBrowserWorkbenchNavigationError(value){
    if(!value||typeof value!=='object')return null;
    const chromiumError=String(value.chromium_error||value.error_description||'ERR_FAILED').trim().toUpperCase();
    const validatedUrl=String(value.validated_url||value.url||'').trim();
    return {
      error_code:Number(value.error_code)||0,
      error_description:String(value.error_description||''),
      chromium_error:/^ERR_[A-Z0-9_]+$/.test(chromiumError)?chromiumError:'ERR_FAILED',
      validated_url:validatedUrl,
      is_main_frame:value.is_main_frame!==false,
    };
  }

  function beginBrowserWorkbenchNavigation(tab,url,options){
    const target=tab||getActiveWorkbenchTab();
    if(!target)return 0;
    const opts=options&&typeof options==='object'?options:{};
    if(opts.preserveHistoryTraversal!==true)clearBrowserWorkbenchPendingHistoryTraversal(target);
    const requested=String(url||browserWorkbenchRetryUrl(target)||'').trim();
    if(selectionMode&&selectionModeTabId===target.id)setBrowserWorkbenchSelectionMode(false);
    if(areaCaptureMode)cancelBrowserWorkbenchAreaCapture(target);
    target.navigationRequestId=(target.navigationRequestId||0)+1;
    setBrowserWorkbenchContentState(target,'loading');
    target.navigationError=null;
    target.renderError='';
    target.loadError='';
    target.lastError='';
    setTabState('loading',target);
    markBrowserWorkbenchLoadStarted(target,requested);
    setBrowserWorkbenchLoadStatus('loading',target,{autoReset:false,url:requested});
    if(opts.message)target.navigationStatusToken=setStatus(opts.message,'muted',target,{owner:'navigation',kind:'progress',resetTransient:true});
    if(target.id===activeBrowserWorkbenchTabId)renderActiveBrowserWorkbenchView();
    persistBrowserWorkbenchTabs();
    return target.navigationRequestId;
  }

  function browserWorkbenchStatusState(status,tab){
    const normalized=normalizeBrowserWorkbenchLoadStatus(status);
    if(normalized==='loading')return 'loading';
    if(normalized==='success')return 'success';
    if(normalized==='error')return 'error';
    if(tab&&tab.sessionId&&tab.renderError)return 'error';
    return 'idle';
  }

  function browserWorkbenchActivationUrl(tab){
    // A client-side navigation can commit without issuing a new explicit URL
    // request. Prefer the live/committed location so restart and reload never
    // fall back to the older URL that originally created the tab.
    const value=tab&&(tab.currentUrl||tab.url||tab.lastLoadedUrl||tab.requestedUrl||'');
    return String(value||'').trim();
  }

  function shouldStartBrowserWorkbenchInitialLoadOnActivation(tab){
    if(!tab)return false;
    if(normalizeBrowserWorkbenchLoadStatus(tab.loadStatus)!=='idle')return false;
    if(tab.sessionId)return false;
    const requested=browserWorkbenchActivationUrl(tab);
    if(browserWorkbenchIsBlankUrl(requested))return false;
    // Restored history is intentionally suspended. Merely selecting that tab
    // must not issue network requests after a WebUI/service restart; explicit
    // Reload or URL navigation owns materialization.
    if(normalizeBrowserWorkbenchContentState(tab.contentState)==='restored')return false;
    return tab.hasStartedLoad!==true;
  }

  function markBrowserWorkbenchLoadStarted(tab,url){
    if(!tab)return;
    const requested=String(url||tab.url||tab.requestedUrl||'').trim();
    if(requested&&requested!==(tab.currentUrl||'')&&tab.surfaceNode){
      removeBrowserWorkbenchStoredSurface(tab);
    }
    tab.hasStartedLoad=true;
    tab.clientNavigatedUrl='';
    tab.requestedUrl=requested;
    tab.currentUrl=requested||tab.currentUrl||'';
    tab.lastError='';
  }

  function markBrowserWorkbenchLoadCommitted(tab,url){
    if(!tab)return;
    const current=String(url||tab.url||tab.currentUrl||tab.requestedUrl||'').trim();
    tab.hasStartedLoad=true;
    tab.hasCommittedNavigation=true;
    tab.currentUrl=current;
    tab.requestedUrl=current||tab.requestedUrl||'';
    tab.lastLoadedUrl=current||tab.lastLoadedUrl||'';
    tab.lastError='';
  }

  function syncBrowserWorkbenchTabLocation(tab,url,options){
    const target=tab||getActiveWorkbenchTab();
    const nextUrl=String(url||'').trim();
    if(!target||!nextUrl)return false;
    const opts=options&&typeof options==='object'?options:{};
    const previousUrl=target.url||'';
    target.url=nextUrl;
    target.currentUrl=nextUrl;
    if(opts.clientNavigation===true)target.clientNavigatedUrl=nextUrl;
    if(opts.committed===true)target.lastLoadedUrl=nextUrl;
    if(opts.updateRequested===true)target.requestedUrl=nextUrl;
    if(target.renderer==='iframe-bridge'&&opts.updateBridge!==false&&(!target.bridgeUrl||nextUrl!==previousUrl)){
      const proxyUrl=browserWorkbenchProxyUrlForTarget(nextUrl,target);
      if(proxyUrl)target.bridgeUrl=proxyUrl;
    }
    if(urlInput&&target.id===activeBrowserWorkbenchTabId&&!isBrowserWorkbenchUrlInputEditing(target))urlInput.value=nextUrl;
    return true;
  }

  function isBrowserWorkbenchUrlInputEditing(tab){
    return !!urlInput&&!!tab&&document.activeElement===urlInput&&browserWorkbenchUrlInputEditingTabId===tab.id;
  }

  function clearBrowserWorkbenchLoadTimers(tab){
    if(!tab)return;
    if(tab.loadStatusTimer){
      clearTimeout(tab.loadStatusTimer);
      tab.loadStatusTimer=null;
    }
  }


  function setBrowserWorkbenchLoadStatus(status,tab,options){
    const target=tab||getActiveWorkbenchTab();
    if(!target)return;
    const opts=options&&typeof options==='object'?options:{};
    const next=normalizeBrowserWorkbenchLoadStatus(status);
    clearBrowserWorkbenchLoadTimers(target);
    target.loadStatus=next;
    setBrowserWorkbenchContentState(target,nextBrowserWorkbenchContentState(target,next));
    target.loadError=next==='error'?String(opts.message||target.loadError||'Load failed'):'';
    if(next==='loading'){
      markBrowserWorkbenchLoadStarted(target,opts.url||target.requestedUrl||target.url);
      setTabState('loading',target);
    }else if(next==='success'){
      markBrowserWorkbenchLoadCommitted(target,opts.url||target.url||target.currentUrl);
      setTabState('success',target);
    }else if(next==='error'){
      target.hasStartedLoad=true;
      target.lastError=target.loadError;
      setTabState('error',target);
    }else{
      setTabState(browserWorkbenchStatusState(next,target),target);
    }
    if(next==='success'||next==='idle')browserWorkbenchClearStatus(target,{owner:'navigation'});
    else if(next==='error')setStatus('Page couldn’t be loaded.','warning',target,{owner:'navigation',kind:'error'});
    if((next==='success'||next==='error')&&opts.autoReset===true){
      const delay=next==='success'?BROWSER_WORKBENCH_RELOAD_SUCCESS_MS:BROWSER_WORKBENCH_RELOAD_ERROR_MS;
      target.loadStatusTimer=setTimeout(()=>{
        target.loadStatusTimer=null;
        if(target.loadStatus===next)setBrowserWorkbenchLoadStatus('idle',target,{autoReset:false});
      },delay);
    }
    if(target.id===activeBrowserWorkbenchTabId)updateBrowserWorkbenchReloadButton();
    renderBrowserWorkbenchTabs();
  }

  function updateBrowserWorkbenchReloadButton(){
    wireDom();
    if(!reloadButton)return;
    const active=getActiveWorkbenchTab();
    const navigationEnabled=!!active&&workbenchCapabilities.navigation===true&&browserWorkbenchCanReload(active);
    const stopEnabled=workbenchCapabilities.stop_loading===true;
    const loadStatus=normalizeBrowserWorkbenchLoadStatus(active&&active.loadStatus);
    const loading=loadStatus==='loading';
    reloadButton.disabled=!navigationEnabled||(loading&&!stopEnabled);
    reloadButton.dataset.loadStatus=loadStatus;
    reloadButton.setAttribute('aria-busy',loading?'true':'false');
    const icon=reloadButton.querySelector('.browser-workbench-reload-icon');
    if(icon)icon.textContent=loading?'✕':'↻';
    const title=loading?'Stop loading':loadStatus==='success'?'Reload finished':loadStatus==='error'?'Reload failed':'Reload';
    reloadButton.title=navigationEnabled&&(!loading||stopEnabled)?title:loading?'Stop is unavailable right now':'Enter a valid URL before reloading';
    reloadButton.setAttribute('aria-label',loading?'Stop loading':loadStatus==='error'?'Reload failed':'Reload');
  }

  function browserWorkbenchStatusEntries(tab){
    if(!tab)return null;
    if(!(tab.statusEntries instanceof Map))tab.statusEntries=new Map();
    return tab.statusEntries;
  }

  function browserWorkbenchRenderManagedStatus(tab){
    const target=tab||getActiveWorkbenchTab();
    const entries=browserWorkbenchStatusEntries(target);
    const current=entries?Array.from(entries.values()).sort((a,b)=>{
      const newest=b.id-a.id;
      return newest||((BROWSER_WORKBENCH_STATUS_PRIORITY[b.kind]||0)-(BROWSER_WORKBENCH_STATUS_PRIORITY[a.kind]||0));
    })[0]||null:null;
    if(target){
      target.message=current?current.message:'';
      target.tone=current?current.tone:'muted';
    }
    if(target&&target.id!==activeBrowserWorkbenchTabId)return current;
    if(statusEl){
      statusEl.textContent=current?current.message:'';
      statusEl.dataset.tone=current?current.tone:'muted';
      statusEl.dataset.kind=current?current.kind:'';
    }
    return current;
  }

  function browserWorkbenchClearStatus(tab,options){
    const target=tab||getActiveWorkbenchTab();
    const entries=browserWorkbenchStatusEntries(target);
    if(!entries)return false;
    const opts=options&&typeof options==='object'?options:{};
    const kinds=Array.isArray(opts.kinds)?new Set(opts.kinds.map(String)):null;
    let changed=false;
    entries.forEach((entry,owner)=>{
      const matches=opts.all===true||(!opts.owner&&!opts.token&&!kinds)||(opts.owner&&owner===opts.owner)||(opts.token&&entry.id===opts.token.id&&owner===opts.token.owner)||(kinds&&kinds.has(entry.kind));
      if(!matches)return;
      if(entry.timer)clearTimeout(entry.timer);
      entries.delete(owner);
      changed=true;
    });
    if(changed)browserWorkbenchRenderManagedStatus(target);
    return changed;
  }

  function browserWorkbenchSetStatus(message,options){
    wireDom();
    const opts=options&&typeof options==='object'?options:{};
    const target=opts.tab||getActiveWorkbenchTab();
    if(!target)return null;
    const owner=String(opts.owner||'general');
    const kind=String(opts.kind||'info');
    const tone=String(opts.tone|| (kind==='error'?'warning':kind==='temporary'?'ready':'muted'));
    if(opts.resetTransient===true){
      const entries=browserWorkbenchStatusEntries(target);
      entries.forEach((entry,key)=>{
        if(entry.kind==='persistent'||entry.kind==='error')return;
        if(entry.timer)clearTimeout(entry.timer);
        entries.delete(key);
      });
    }
    browserWorkbenchClearStatus(target,{owner});
    const text=String(message||'').trim();
    if(!text)return null;
    const entry={id:++browserWorkbenchStatusSequence,owner,kind,tone,message:text,timer:null};
    browserWorkbenchStatusEntries(target).set(owner,entry);
    if(kind==='temporary'||kind==='progress'){
      const fallback=kind==='progress'?BROWSER_WORKBENCH_STATUS_PROGRESS_TIMEOUT_MS:BROWSER_WORKBENCH_STATUS_FEEDBACK_MS;
      const duration=Math.max(250,Number(opts.duration)||fallback);
      entry.timer=setTimeout(()=>{
        const current=browserWorkbenchStatusEntries(target).get(owner);
        if(!current||current.id!==entry.id)return;
        browserWorkbenchClearStatus(target,{token:{owner,id:entry.id}});
      },duration);
    }
    browserWorkbenchRenderManagedStatus(target);
    return {tabId:target.id,owner,id:entry.id};
  }

  function browserWorkbenchResolveStatus(token,message,options){
    if(!token)return null;
    const target=tabById(token.tabId);
    const entry=target&&browserWorkbenchStatusEntries(target).get(token.owner);
    if(!entry||entry.id!==token.id)return null;
    const opts=options&&typeof options==='object'?options:{};
    browserWorkbenchClearStatus(target,{token});
    if(!message)return null;
    return browserWorkbenchSetStatus(message,{...opts,tab:target,owner:token.owner});
  }

  function browserWorkbenchStatusTokenIsCurrent(token){
    if(!token)return false;
    const target=tabById(token.tabId);
    const entry=target&&browserWorkbenchStatusEntries(target).get(token.owner);
    return !!entry&&entry.id===token.id;
  }

  function setStatus(message,tone,tab,options){
    const opts=options&&typeof options==='object'?options:{};
    const kind=opts.kind|| (tone==='warning'?'error':tone==='ready'?'temporary':'info');
    return browserWorkbenchSetStatus(message,{...opts,tab:tab||getActiveWorkbenchTab(),tone:tone||opts.tone,kind});
  }

  function setBrowserWorkbenchSelectionMode(enabled){
    wireDom();
    const active=getActiveWorkbenchTab();
    const previousSelectionTab=tabById(selectionModeTabId)||active;
    selectionMode=enabled===true&&!!active;
    selectionModeTabId=selectionMode&&active?active.id:'';
    if(!selectionMode){
      cancelBrowserWorkbenchHoverInspect();
      clearBrowserWorkbenchOverlay('hover');
      browserWorkbenchClearStatus(previousSelectionTab,{owner:'selection'});
      browserWorkbenchClearStatus(previousSelectionTab,{owner:'selection-action'});
    }
    if(pingButton){
      pingButton.classList.toggle('active',selectionMode&&selectionModeTabId===activeBrowserWorkbenchTabId);
      pingButton.setAttribute('aria-pressed',selectionMode&&selectionModeTabId===activeBrowserWorkbenchTabId?'true':'false');
      pingButton.textContent=selectionMode&&selectionModeTabId===activeBrowserWorkbenchTabId?'Selecting…':'Ping selection';
    }
    if(viewportEl)viewportEl.classList.toggle('selecting',selectionMode&&selectionModeTabId===activeBrowserWorkbenchTabId);
    syncBrowserWorkbenchIframeSelectionMode(active);
    if(selectionMode&&active){
      setStatus('Select an element on the page. Press Escape to finish.','muted',active,{owner:'selection',kind:'persistent'});
    }
  }

  function toggleBrowserWorkbenchSelectionMode(){
    wireDom();
    const active=getActiveWorkbenchTab();
    if(!active||!active.sessionId){
      setStatus('Open a page before selecting an element.','warning',active,{owner:'selection',kind:'error'});
      return false;
    }
    setBrowserWorkbenchSelectionMode(!(selectionMode&&selectionModeTabId===active.id));
    return selectionMode;
  }

  function stashBrowserWorkbenchSurface(host){
    const targetHost=host||viewportEl;
    if(!targetHost||!targetHost.querySelectorAll)return;
    targetHost.querySelectorAll('.browser-workbench-frame-wrap[data-browser-workbench-tab-id]').forEach((frameWrap)=>{
      const tab=tabById(frameWrap.dataset.browserWorkbenchTabId);
      if(tab){
        tab.surfaceNode=frameWrap;
        tab.surfaceUrl=frameWrap.dataset.browserWorkbenchUrl||'';
      }
      frameWrap.hidden=true;
      frameWrap.setAttribute('aria-hidden','true');
    });
  }

  function removeBrowserWorkbenchStoredSurface(tab){
    if(!tab)return;
    if(tab.surfaceNode&&tab.surfaceNode.parentNode)tab.surfaceNode.parentNode.removeChild(tab.surfaceNode);
    tab.surfaceNode=null;
    tab.surfaceUrl='';
    if(browserWorkbenchOverlayPreview&&browserWorkbenchOverlayPreview.tabId===String(tab.id||''))browserWorkbenchOverlayPreview=null;
  }

  function clearBrowserWorkbenchHostForRender(host){
    if(!host)return;
    stashBrowserWorkbenchSurface(host);
    Array.from(host.childNodes).forEach((node)=>{
      if(node.nodeType===1&&node.matches&&node.matches('.browser-workbench-frame-wrap[data-browser-workbench-tab-id]'))return;
      if(node.parentNode===host)host.removeChild(node);
    });
    if(host===viewportEl){
      viewportEl.classList.remove('has-devtools-docked');
      viewportEl.style.removeProperty('--browser-workbench-devtools-width');
    }
  }

  function setViewportMessage(message){
    wireDom();
    if(!viewportEl)return;
    clearBrowserWorkbenchHostForRender(viewportEl);
    viewportEl.appendChild(textEl('div','browser-workbench-placeholder',message));
  }

  function browserWorkbenchNavigationErrorPresentation(error){
    const code=String(error&&error.chromium_error||'ERR_FAILED').toUpperCase();
    const url=String(error&&error.validated_url||'');
    const hostname=browserWorkbenchHostnameForUrl(url);
    if(code==='ERR_CONNECTION_REFUSED')return {reason:`${hostname||url||'The site'} refused to connect.`,suggestions:['Checking the address','Checking the connection','Checking the proxy and firewall']};
    if(code==='ERR_NAME_NOT_RESOLVED')return {reason:`${hostname||url||'The server'}’s address could not be found.`,suggestions:['Checking the address for typing errors','Checking your DNS and network connection']};
    if(code==='ERR_CONNECTION_TIMED_OUT'||code==='ERR_TIMED_OUT')return {reason:`${hostname||url||'The site'} took too long to respond.`,suggestions:['Checking the connection','Checking the proxy and firewall','Trying again later']};
    if(code==='ERR_INTERNET_DISCONNECTED')return {reason:'Your device appears to be disconnected from the internet.',suggestions:['Checking network cables, Wi-Fi, and router','Reconnecting to the internet']};
    if(code==='ERR_PROXY_CONNECTION_FAILED')return {reason:'The proxy server could not be reached.',suggestions:['Checking the proxy settings','Checking the connection and firewall']};
    if(code.startsWith('ERR_CERT_'))return {reason:`${hostname||url||'The site'} returned a certificate error.`,suggestions:['Checking the device date and time','Checking the certificate or network security settings']};
    const description=String(error&&error.error_description||'').replace(/^ERR_/,'').replace(/_/g,' ').toLowerCase();
    return {reason:description?`${hostname||url||'The site'} could not be reached: ${description}.`:`${hostname||url||'The site'} could not be reached.`,suggestions:['Checking the address','Checking the connection','Trying again']};
  }

  function renderBrowserWorkbenchNavigationError(tab,host){
    const targetHost=host||viewportEl;
    if(!targetHost||!tab||!tab.navigationError)return false;
    stopBrowserWorkbenchChromiumStream();
    clearBrowserWorkbenchHostForRender(targetHost);
    const error=tab.navigationError;
    const presentation=browserWorkbenchNavigationErrorPresentation(error);
    const page=document.createElement('div');
    page.className='browser-workbench-error-page';
    page.setAttribute('role','alert');
    page.appendChild(textEl('div','browser-workbench-error-icon','!'));
    page.appendChild(textEl('h2','browser-workbench-error-heading','This site can’t be reached'));
    page.appendChild(textEl('p','browser-workbench-error-reason',presentation.reason));
    if(error.validated_url)page.appendChild(textEl('div','browser-workbench-error-url',error.validated_url));
    const tryLabel=textEl('div','browser-workbench-error-try-label','Try:');
    page.appendChild(tryLabel);
    const list=document.createElement('ul');
    list.className='browser-workbench-error-suggestions';
    presentation.suggestions.forEach(item=>list.appendChild(textEl('li','',item)));
    page.appendChild(list);
    page.appendChild(textEl('code','browser-workbench-error-code',error.chromium_error||'ERR_FAILED'));
    const retry=textEl('button','browser-workbench-error-retry','Reload');
    retry.type='button';
    retry.addEventListener('click',()=>void navigateBrowserWorkbenchHistory('reload',tab.id));
    page.appendChild(retry);
    targetHost.appendChild(page);
    return true;
  }

  function browserWorkbenchProxyUrlForTarget(url,tab){
    try{
      const absolute=new URL(String(url||''),window.location.href);
      if(absolute.protocol!=='http:'&&absolute.protocol!=='https:')return '';
      const target=tab||getActiveWorkbenchTab();
      const frameId=target&&target.sessionId?target.sessionId+'-'+Date.now():'';
      const transportPath=target&&target.sessionId?`_hermes/${encodeURIComponent(target.sessionId)}/${encodeURIComponent(frameId)}/`:'';
      const targetPath=`${absolute.protocol}//${absolute.host}${absolute.pathname}`;
      return '/browser-proxy/'+transportPath+encodeURIComponent(targetPath).replace(/%2F/g,'/').replace(/%3A/g,':')+absolute.search+absolute.hash;
    }catch(_){return '';}
  }

  function browserWorkbenchTargetUrlFromProxyLocation(proxyUrl,tab){
    const target=tab||getActiveWorkbenchTab();
    try{
      const absolute=new URL(String(proxyUrl||''),window.location.href);
      if(absolute.origin!==window.location.origin||!absolute.pathname.startsWith('/browser-proxy/'))return '';
      let encodedTarget=absolute.pathname.slice('/browser-proxy/'.length);
      if(encodedTarget.startsWith('_hermes/')){
        const parts=encodedTarget.split('/');
        if(parts.length<4)return '';
        const sessionId=decodeURIComponent(parts[1]||'');
        if(!sessionId||(target&&target.sessionId&&sessionId!==target.sessionId))return '';
        encodedTarget=parts.slice(3).join('/');
      }
      if(!encodedTarget)return '';
      const resolved=new URL(decodeURIComponent(encodedTarget));
      if((resolved.protocol!=='http:'&&resolved.protocol!=='https:')||resolved.username||resolved.password)return '';
      resolved.search=absolute.search;
      resolved.hash=absolute.hash;
      return resolved.href;
    }catch(_){return '';}
  }

  function activeBrowserWorkbenchIframe(){
    const active=getActiveWorkbenchTab();
    const owned=active&&active.surfaceNode&&active.surfaceNode.querySelector?active.surfaceNode.querySelector('.browser-workbench-frame'):null;
    if(owned)return owned;
    return viewportEl&&viewportEl.querySelector?viewportEl.querySelector('.browser-workbench-frame-wrap:not([hidden]) .browser-workbench-frame'):null;
  }

  function navigateBrowserWorkbenchIframeHistory(tab,action){
    const target=tab||getActiveWorkbenchTab();
    if(!target||target.renderer!=='iframe-bridge')return false;
    const storedFrame=target.surfaceNode&&target.surfaceNode.querySelector?target.surfaceNode.querySelector('.browser-workbench-frame'):null;
    const frame=storedFrame||activeBrowserWorkbenchIframe();
    if(!frame||!frame.contentWindow)return false;
    try{
      if(action==='reload')frame.contentWindow.location.reload();
      else if(action==='back')frame.contentWindow.history.back();
      else if(action==='forward')frame.contentWindow.history.forward();
      else return false;
      return true;
    }catch(_){return false;}
  }

  function syncBrowserWorkbenchIframeSurfaceLocation(tab,proxyUrl){
    const target=tab||getActiveWorkbenchTab();
    if(!target||target.renderer!=='iframe-bridge'||!target.surfaceNode)return false;
    let normalized='';
    try{
      const absolute=new URL(String(proxyUrl||''),window.location.href);
      if(absolute.origin!==window.location.origin||!absolute.pathname.startsWith('/browser-proxy/'))return false;
      normalized=`${absolute.pathname}${absolute.search}${absolute.hash}`;
    }catch(_){return false;}
    target.bridgeUrl=normalized;
    target.surfaceUrl=normalized;
    target.surfaceNode.dataset.browserWorkbenchUrl=normalized;
    return true;
  }

  function syncBrowserWorkbenchIframeLoadedLocation(tab,frame){
    const target=tab||getActiveWorkbenchTab();
    if(!target||target.renderer!=='iframe-bridge'||!frame||!frame.contentWindow)return false;
    let proxyUrl='';
    try{proxyUrl=String(frame.contentWindow.location&&frame.contentWindow.location.href||'');}
    catch(_){return false;}
    const nextUrl=browserWorkbenchTargetUrlFromProxyLocation(proxyUrl,target);
    if(!nextUrl)return false;
    syncBrowserWorkbenchTabLocation(target,nextUrl,{committed:true,updateRequested:true,clientNavigation:true,updateBridge:false});
    syncBrowserWorkbenchTabHistory(target,nextUrl);
    syncBrowserWorkbenchIframeSurfaceLocation(target,proxyUrl);
    try{
      const title=String(frame.contentDocument&&frame.contentDocument.title||'').trim();
      if(title)target.title=title;
    }catch(_){}
    renderBrowserWorkbenchTabs();
    renderActiveBrowserWorkbenchView();
    persistBrowserWorkbenchTabs();
    return true;
  }

  function syncBrowserWorkbenchIframeSelectionMode(tab){
    const target=tab||getActiveWorkbenchTab();
    if(!target||target.renderer!=='iframe-bridge')return false;
    const frame=activeBrowserWorkbenchIframe();
    if(!frame||!frame.contentWindow)return false;
    try{
      frame.contentWindow.postMessage({source:'hermes-browser-workbench-parent',type:'selection-mode',enabled:selectionMode&&selectionModeTabId===target.id},window.location.origin);
      return true;
    }catch(_){return false;}
  }

  function browserWorkbenchDevtoolsLiteState(tab){
    if(!tab)return null;
    if(!tab.devtoolsLite||typeof tab.devtoolsLite!=='object'){
      tab.devtoolsLite={console:[],network:[],selected:null,diagnostics:{},activePanel:'console',frameId:'',targetUrl:'',bridgeInjected:false,lastHeartbeat:0};
    }
    return tab.devtoolsLite;
  }

  function resetBrowserWorkbenchDevtoolsLite(tab,reason){
    const state=browserWorkbenchDevtoolsLiteState(tab);
    if(!state)return;
    state.console=[];
    state.network=[];
    state.selected=null;
    state.diagnostics={reason:reason||'reset'};
    state.frameId='';
    state.targetUrl='';
    state.bridgeInjected=false;
    state.lastHeartbeat=0;
  }

  function trimBrowserWorkbenchDevtoolsLite(list,limit){
    if(!Array.isArray(list))return [];
    while(list.length>limit)list.shift();
    return list;
  }

  function browserWorkbenchDevtoolsEventAllowed(event,detail,active){
    if(!active||active.renderer!=='iframe-bridge'||!active.sessionId)return false;
    const sessionId=String(detail.sessionId||detail.session_id||'');
    if(sessionId&&sessionId!==active.sessionId)return false;
    const frame=activeBrowserWorkbenchIframe();
    if(frame&&event&&event.source&&frame.contentWindow&&event.source!==frame.contentWindow)return false;
    return true;
  }

  function handleBrowserWorkbenchDevtoolsAgentMessage(event){
    const detail=event&&event.data&&typeof event.data==='object'?event.data:{};
    if(detail.source!=='hermes-devtools-agent')return;
    const active=getActiveWorkbenchTab();
    if(!browserWorkbenchDevtoolsEventAllowed(event,detail,active))return;
    const state=browserWorkbenchDevtoolsLiteState(active);
    if(!state)return;
    const frameId=String(detail.frameId||detail.frame_id||'');
    if(frameId&&state.frameId&&state.frameId!==frameId){
      state.console=[];
      state.network=[];
      state.selected=null;
    }
    if(frameId)state.frameId=frameId;
    if(detail.targetUrl)state.targetUrl=String(detail.targetUrl);
    const payload=detail.payload&&typeof detail.payload==='object'?detail.payload:{};
    const type=String(detail.type||'');
    const timestamp=Number(detail.timestamp)||Date.now();
    if(type==='console'){
      state.console.push({timestamp,level:String(detail.level||payload.level||'log'),sourceType:String(payload.sourceType||'console'),args:Array.isArray(payload.args)?payload.args:[payload.message||''],message:String(payload.message||''),filename:String(payload.filename||''),lineno:payload.lineno||0,colno:payload.colno||0,stack:String(payload.stack||'')});
      trimBrowserWorkbenchDevtoolsLite(state.console,BROWSER_WORKBENCH_DEVTOOLS_LITE_EVENT_LIMIT);
    }else if(type==='network'){
      const requestId=String(payload.requestId||payload.id||`${timestamp}-${state.network.length}`);
      let row=state.network.find((entry)=>entry.requestId===requestId);
      if(!row){
        row={requestId,timestamp,phase:'start',requestType:String(payload.requestType||'request'),method:String(payload.method||'GET'),url:String(payload.url||''),status:'',duration:'',error:'',ok:null};
        state.network.push(row);
        trimBrowserWorkbenchDevtoolsLite(state.network,BROWSER_WORKBENCH_DEVTOOLS_LITE_NETWORK_LIMIT);
      }
      Object.assign(row,{timestamp,phase:String(payload.phase||row.phase||''),requestType:String(payload.requestType||row.requestType||'request'),method:String(payload.method||row.method||'GET'),url:String(payload.url||row.url||''),status:payload.status!==undefined?payload.status:row.status,duration:payload.duration!==undefined?payload.duration:row.duration,error:String(payload.error||''),ok:payload.ok!==undefined?payload.ok:row.ok});
    }else if(type==='element'){
      state.selected=payload.selection&&typeof payload.selection==='object'?payload.selection:payload;
    }else if(type==='diagnostic'||type==='heartbeat'){
      state.bridgeInjected=true;
      state.lastHeartbeat=timestamp;
      state.diagnostics={...(state.diagnostics||{}),...payload,lastEventType:type,timestamp};
    }
    // Chii owns iframe-proxy DevTools UI.  Keep these legacy iframe-scoped
    // diagnostics only as readiness hints for screenshot/ping bridges; never
    // re-render a custom Chii DevTools panel from them.
  }

  async function openBrowserWorkbenchUrlInNewTab(value){
    wireDom();
    const raw=String(value===undefined||value===null?'':value).trim();
    if(!raw||raw.length>4096)return null;
    const requested=browserWorkbenchSafeHistoryUrl(raw);
    if(!requested)return null;
    // Keep the record blank until the browser panel is visible. switchPanel()
    // asks the active tab to materialize; a pre-populated URL here would race
    // that implicit path with the explicit navigation below.
    const target=createBrowserWorkbenchTabRecord();
    activateBrowserWorkbenchTab(target.id,{switchPanel:false});
    if(typeof switchPanel==='function')await switchPanel('browser');
    await navigateBrowserWorkbenchToUrl(target.id,requested);
    return target;
  }

  function handleBrowserWorkbenchIframeBridgeMessage(event){
    const detail=event&&event.data&&typeof event.data==='object'?event.data:{};
    if(detail.source!=='hermes-browser-workbench-bridge')return;
    const active=getActiveWorkbenchTab();
    if(!active||active.renderer!=='iframe-bridge')return;
    const sessionId=String(detail.sessionId||detail.session_id||'');
    if(sessionId&&active.sessionId&&sessionId!==active.sessionId)return;
    const frame=activeBrowserWorkbenchIframe();
    if(frame&&event.source&&frame.contentWindow&&event.source!==frame.contentWindow)return;
    if(detail.type==='open-tab'){
      const raw=String(detail.url===undefined||detail.url===null?'':detail.url).trim();
      if(!raw||raw.length>4096)return;
      const requested=browserWorkbenchSafeHistoryUrl(raw);
      if(requested)void openBrowserWorkbenchUrlInNewTab(requested);
      return;
    }
    const state=browserWorkbenchDevtoolsLiteState(active);
    if(state){
      if(detail.frameId)state.frameId=String(detail.frameId);
      if(detail.targetUrl)state.targetUrl=String(detail.targetUrl);
    }
    if(detail.type==='capture-screenshot-result'){
      active.iframeBridgeReady=true;
      active.iframeCaptureReady=detail.ok===true||active.iframeCaptureReady===true;
      settleBrowserWorkbenchIframeCapture(detail);
      updateBrowserWorkbenchActionMenuCapabilities();
      return;
    }
    if(detail.type==='metadata'){
      active.iframeBridgeReady=true;
      active.iframeCaptureReady=true;
      const nextUrl=String(detail.url||'');
      const nativePreviousUrl=browserWorkbenchSafeHistoryUrl(detail.native_history_previous_url||detail.nativeHistoryPreviousUrl);
      const nativeNextUrl=browserWorkbenchSafeHistoryUrl(detail.native_history_next_url||detail.nativeHistoryNextUrl);
      if(nextUrl)syncBrowserWorkbenchTabLocation(active,nextUrl,{committed:true,updateRequested:true,clientNavigation:true,updateBridge:false});
      if(nextUrl)syncBrowserWorkbenchTabHistory(active,nextUrl,{
        mode:detail.history_mode==='replace'?'replace':detail.history_mode==='traverse'?'traverse':'push',
        nativePreviousUrl,
        nativeNextUrl,
      });
      syncBrowserWorkbenchIframeSurfaceLocation(active,detail.proxy_url||detail.proxyUrl);
      if(nextUrl)syncBrowserWorkbenchNativeHistoryNeighbors(active,nextUrl,nativePreviousUrl,nativeNextUrl);
      active.nativeHistoryPreviousUrl=nativePreviousUrl;
      active.nativeHistoryNextUrl=nativeNextUrl;
      applyBrowserWorkbenchTabHistoryCapabilities(active,detail.can_go_back,detail.can_go_forward);
      active.title=String(detail.title||active.title||'');
      active.faviconUrl=String(detail.favicon_url||detail.faviconUrl||active.faviconUrl||'');
      if(active.loadStatus==='loading')setBrowserWorkbenchLoadStatus('success',active,{message:'Page loaded.'});
      renderBrowserWorkbenchTabs();
      renderActiveBrowserWorkbenchView();
      persistBrowserWorkbenchTabs();
      updateBrowserWorkbenchActionMenuCapabilities();
      return;
    }
    if(detail.type==='hover'&&selectionMode&&selectionModeTabId===active.id){
      previewBrowserWorkbenchSelection(detail.selection,true);
      return;
    }
    if(detail.type==='select'&&selectionMode&&selectionModeTabId===active.id){
      const lite=browserWorkbenchDevtoolsLiteState(active);
      if(lite)lite.selected=detail.selection&&typeof detail.selection==='object'?detail.selection:null;
      void pingBrowserWorkbenchSelection(detail.selection).catch((err)=>{
        setBrowserWorkbenchSelectionMode(false);
        setStatus('Element selection failed.','warning',active,{owner:'selection',kind:'error'});
      });
    }
  }

  function browserWorkbenchDevtoolsTime(timestamp){
    const date=new Date(Number(timestamp)||Date.now());
    return date.toLocaleTimeString([], {hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'});
  }

  function renderBrowserWorkbenchDevtoolsLite(tab,host){
    wireDom();
    const targetHost=host||viewportEl;
    if(!targetHost)return;
    const state=browserWorkbenchDevtoolsLiteState(tab);
    targetHost.textContent='';
    const wrap=document.createElement('div');
    wrap.className='browser-workbench-devtools-wrap browser-workbench-devtools-lite-wrap';
    if(host)wrap.classList.add('browser-workbench-devtools-wrap--side');
    const bar=document.createElement('div');
    bar.className='browser-workbench-devtools-bar browser-workbench-devtools-lite-bar';
    bar.appendChild(textEl('span','browser-workbench-devtools-title','Page diagnostics'));
    const close=textEl('button','browser-workbench-devtools-close','Close DevTools');
    close.type='button';
    close.addEventListener('click',()=>closeBrowserWorkbenchDevtools(tab));
    bar.appendChild(close);
    const panels=document.createElement('div');
    panels.className='browser-workbench-devtools-lite-tabs';
    ['console','network','elements','diagnostics'].forEach((panel)=>{
      const button=textEl('button','browser-workbench-devtools-lite-tab',panel[0].toUpperCase()+panel.slice(1));
      button.type='button';
      button.classList.toggle('active',state.activePanel===panel);
      button.setAttribute('aria-pressed',state.activePanel===panel?'true':'false');
      button.addEventListener('click',()=>{state.activePanel=panel;refreshBrowserWorkbenchDevtoolsLitePanel(tab);});
      panels.appendChild(button);
    });
    const body=document.createElement('div');
    body.className='browser-workbench-devtools-lite-body';
    const panel=state.activePanel||'console';
    if(panel==='console')renderBrowserWorkbenchDevtoolsLiteConsole(state,body);
    else if(panel==='network')renderBrowserWorkbenchDevtoolsLiteNetwork(state,body);
    else if(panel==='elements')renderBrowserWorkbenchDevtoolsLiteElements(state,body);
    else renderBrowserWorkbenchDevtoolsLiteDiagnostics(tab,state,body);
    wrap.appendChild(bar);
    wrap.appendChild(panels);
    wrap.appendChild(body);
    targetHost.appendChild(wrap);
  }

  function renderBrowserWorkbenchDevtoolsLiteEmpty(host,message){
    host.appendChild(textEl('div','browser-workbench-devtools-lite-empty',message));
  }

  function renderBrowserWorkbenchDevtoolsLiteConsole(state,host){
    const toolbar=document.createElement('div');
    toolbar.className='browser-workbench-devtools-lite-toolbar';
    toolbar.appendChild(textEl('span','',`${state.console.length} console event${state.console.length===1?'':'s'}`));
    const clear=textEl('button','browser-workbench-devtools-lite-clear','Clear logs');
    clear.type='button';
    clear.addEventListener('click',()=>{state.console=[];refreshBrowserWorkbenchDevtoolsLitePanel(getActiveWorkbenchTab());});
    toolbar.appendChild(clear);
    host.appendChild(toolbar);
    const list=document.createElement('div');
    list.className='browser-workbench-devtools-lite-list';
    if(!state.console.length)renderBrowserWorkbenchDevtoolsLiteEmpty(list,'No console events yet.');
    state.console.slice().reverse().forEach((entry)=>{
      const row=document.createElement('div');
      row.className=`browser-workbench-devtools-lite-row browser-workbench-devtools-lite-row--${String(entry.level||'log').toLowerCase()}`;
      row.appendChild(textEl('span','browser-workbench-devtools-lite-time',browserWorkbenchDevtoolsTime(entry.timestamp)));
      row.appendChild(textEl('span','browser-workbench-devtools-lite-level',String(entry.level||'log')));
      const msg=textEl('pre','browser-workbench-devtools-lite-message',(Array.isArray(entry.args)&&entry.args.length?entry.args.join(' '):entry.message)||'');
      row.appendChild(msg);
      if(entry.filename)row.appendChild(textEl('span','browser-workbench-devtools-lite-detail',`${entry.filename}:${entry.lineno||0}:${entry.colno||0}`));
      if(entry.stack)row.appendChild(textEl('pre','browser-workbench-devtools-lite-stack',entry.stack));
      list.appendChild(row);
    });
    host.appendChild(list);
  }

  function renderBrowserWorkbenchDevtoolsLiteNetwork(state,host){
    const toolbar=document.createElement('div');
    toolbar.className='browser-workbench-devtools-lite-toolbar';
    toolbar.appendChild(textEl('span','',`${state.network.length} network event${state.network.length===1?'':'s'}`));
    const clear=textEl('button','browser-workbench-devtools-lite-clear','Clear network');
    clear.type='button';
    clear.addEventListener('click',()=>{state.network=[];refreshBrowserWorkbenchDevtoolsLitePanel(getActiveWorkbenchTab());});
    toolbar.appendChild(clear);
    host.appendChild(toolbar);
    const list=document.createElement('div');
    list.className='browser-workbench-devtools-lite-list';
    if(!state.network.length)renderBrowserWorkbenchDevtoolsLiteEmpty(list,'No network events yet.');
    state.network.slice().reverse().forEach((entry)=>{
      const row=document.createElement('div');
      row.className='browser-workbench-devtools-lite-network-row';
      row.appendChild(textEl('span','browser-workbench-devtools-lite-method',entry.method||''));
      row.appendChild(textEl('span','browser-workbench-devtools-lite-type',entry.requestType||''));
      row.appendChild(textEl('span','browser-workbench-devtools-lite-status',entry.status!==''?String(entry.status):entry.phase||''));
      row.appendChild(textEl('span','browser-workbench-devtools-lite-duration',entry.duration!==''?`${entry.duration}ms`:''));
      row.appendChild(textEl('span','browser-workbench-devtools-lite-url',entry.url||''));
      if(entry.error)row.appendChild(textEl('span','browser-workbench-devtools-lite-error',entry.error));
      list.appendChild(row);
    });
    host.appendChild(list);
  }

  function renderBrowserWorkbenchDevtoolsLiteElements(state,host){
    const selected=state.selected;
    if(!selected){
      renderBrowserWorkbenchDevtoolsLiteEmpty(host,'No element selected yet.');
      return;
    }
    const grid=document.createElement('div');
    grid.className='browser-workbench-devtools-lite-kv';
    const add=(key,value)=>{grid.appendChild(textEl('span','browser-workbench-devtools-lite-key',key));grid.appendChild(textEl('pre','browser-workbench-devtools-lite-value',value===undefined||value===null?'':typeof value==='string'?value:JSON.stringify(value,null,2)));};
    add('Tag',selected.tag||'');
    add('Component',selected.component||'unknown');
    add('Selector',selected.selector||'');
    add('Text',selected.text||'');
    add('Classes',Array.isArray(selected.classes)?selected.classes.join(' '):(selected.className||''));
    add('Attributes',selected.attributes||{});
    add('Rect',selected.rect||{});
    add('Point',selected.point||{});
    host.appendChild(grid);
  }

  function renderBrowserWorkbenchDevtoolsLiteDiagnostics(tab,state,host){
    const diag=state.diagnostics||{};
    const grid=document.createElement('div');
    grid.className='browser-workbench-devtools-lite-kv';
    const add=(key,value)=>{grid.appendChild(textEl('span','browser-workbench-devtools-lite-key',key));grid.appendChild(textEl('pre','browser-workbench-devtools-lite-value',value===undefined||value===null?'':String(value)));};
    add('View mode','Embedded page');
    add('Target URL',state.targetUrl||tab.url||'');
    add('Connection',state.bridgeInjected?'Ready':'Waiting');
    add('Last update',state.lastHeartbeat?new Date(state.lastHeartbeat).toLocaleString():'Waiting');
    add('Frame count',diag.frameCount!==undefined?diag.frameCount:'');
    add('Document readyState',diag.readyState||'');
    add('Failure reason',diag.reason||tab.renderError||'');
    host.appendChild(grid);
  }

  function renderBrowserWorkbenchFrame(tab,host){
    if(!host){
      stopBrowserWorkbenchChromiumStream();
    }
    wireDom();
    const targetHost=host||viewportEl;
    if(!targetHost)return;
    const url=tab&&tab.bridgeUrl?String(tab.bridgeUrl):(tab&&tab.url?String(tab.url):'');
    if(!url){
      clearBrowserWorkbenchHostForRender(targetHost);
      targetHost.appendChild(textEl('div','browser-workbench-placeholder',browserWorkbenchContentPlaceholder(tab)));
      return;
    }
    if(tab.surfaceNode&&String(tab.surfaceNode.dataset&&tab.surfaceNode.dataset.browserWorkbenchUrl||'')!==url){
      removeBrowserWorkbenchStoredSurface(tab);
    }
    let existing=targetHost.querySelector&&targetHost.querySelector(`.browser-workbench-frame-wrap[data-browser-workbench-tab-id="${CSS.escape(tab.id)}"]`);
    if(existing&&String(existing.dataset&&existing.dataset.browserWorkbenchUrl||'')!==url){
      if(existing.parentNode)existing.parentNode.removeChild(existing);
      existing=null;
    }
    if(existing){
      stashBrowserWorkbenchSurface(targetHost);
      existing.hidden=false;
      existing.setAttribute('aria-hidden','false');
      tab.surfaceNode=existing;
      tab.surfaceUrl=url;
      applyBrowserWorkbenchSurfaceZoom(tab,targetHost);
      return;
    }
    clearBrowserWorkbenchHostForRender(targetHost);
    if(tab.surfaceNode){
      tab.surfaceUrl=url;
      targetHost.appendChild(tab.surfaceNode);
      tab.surfaceNode.hidden=false;
      tab.surfaceNode.setAttribute('aria-hidden','false');
      applyBrowserWorkbenchSurfaceZoom(tab,targetHost);
      return;
    }
    const frameWrap=document.createElement('div');
    frameWrap.className='browser-workbench-frame-wrap';
    frameWrap.dataset.browserWorkbenchTabId=tab.id;
    frameWrap.dataset.browserWorkbenchUrl=url;
    tab.surfaceNode=frameWrap;
    tab.surfaceUrl=url;
    if(tab&&tab.renderer==='iframe-bridge')frameWrap.classList.add('browser-workbench-frame-wrap--bridge');
    const frame=document.createElement('iframe');
    frame.className='browser-workbench-frame';
    frame.title=`${browserWorkbenchDisplayLabel(tab)} preview`;
    frame.src=url;
    // Clean root-relative iframe requests need the live proxy document referrer
    // so the backend can recover their session/frame transport context. The
    // proxy accepts only same-origin /browser-proxy referrers and normalizes the
    // target page's Referrer-Policy before applying this controlled policy.
    frame.referrerPolicy='same-origin';
    frame.setAttribute('sandbox','allow-forms allow-modals allow-presentation allow-same-origin allow-scripts');
    frame.addEventListener('load',()=>{
      if(tab.surfaceNode!==frameWrap)return;
      syncBrowserWorkbenchIframeLoadedLocation(tab,frame);
      syncBrowserWorkbenchIframeSelectionMode(tab);
      if(tab&&tab.loadStatus==='loading')setBrowserWorkbenchLoadStatus('success',tab,{message:'Browser frame finished loading.'});
    });
    frame.addEventListener('error',()=>{
      if(tab.surfaceNode!==frameWrap)return;
      if(tab&&tab.loadStatus==='loading')setBrowserWorkbenchLoadStatus('error',tab,{message:'Browser frame failed to load.'});
    });
    frameWrap.appendChild(frame);
    const note=tab&&tab.renderer==='iframe-bridge'
      ?'Some pages may behave differently in the embedded browser.'
      :'This page can’t be displayed here.';
    frameWrap.appendChild(textEl('div','browser-workbench-frame-note',note));
    targetHost.appendChild(frameWrap);
    applyBrowserWorkbenchSurfaceZoom(tab,targetHost);
  }

  function stopBrowserWorkbenchChromiumStream(){
    chromiumFrameRequestId+=1;
    if(chromiumFrameTimer){
      clearTimeout(chromiumFrameTimer);
      chromiumFrameTimer=null;
    }
  }

  function renderBrowserWorkbenchChromiumStream(tab,host){
    if(!host){
      stopBrowserWorkbenchChromiumStream();
    }
    wireDom();
    const targetHost=host||viewportEl;
    if(!targetHost)return;
    clearBrowserWorkbenchHostForRender(targetHost);
    const wrap=document.createElement('div');
    wrap.className='browser-workbench-stream-wrap';
    const canvas=document.createElement('canvas');
    canvas.className='browser-workbench-stream-canvas';
    canvas.setAttribute('aria-label',`${browserWorkbenchDisplayLabel(tab)} Chromium viewport`);
    wrap.appendChild(canvas);
    wrap.appendChild(textEl('div','browser-workbench-frame-note','Live page preview.'));
    targetHost.appendChild(wrap);
    void pollBrowserWorkbenchChromiumFrame(tab,canvas,++chromiumFrameRequestId);
  }


  function base64ToBrowserWorkbenchBlob(data,mime){
    const raw=atob(String(data||''));
    const bytes=new Uint8Array(raw.length);
    for(let i=0;i<raw.length;i+=1)bytes[i]=raw.charCodeAt(i);
    return new Blob([bytes],{type:mime||'application/octet-stream'});
  }

  async function drawBrowserWorkbenchFrame(canvas,frame){
    if(!canvas||!frame||!frame.data)return;
    if(typeof createImageBitmap!=='function')throw new Error('This browser cannot decode Chromium frame streams.');
    const blob=base64ToBrowserWorkbenchBlob(frame.data,frame.mime||'image/jpeg');
    const bitmap=await createImageBitmap(blob);
    try{
      canvas.width=bitmap.width||frame.width||canvas.width||1;
      canvas.height=bitmap.height||frame.height||canvas.height||1;
      const ctx=canvas.getContext('2d');
      if(ctx)ctx.drawImage(bitmap,0,0,canvas.width,canvas.height);
    }finally{
      if(bitmap&&typeof bitmap.close==='function')bitmap.close();
    }
  }

  async function pollBrowserWorkbenchChromiumFrame(tab,canvas,requestId){
    if(!tab||!tab.sessionId||requestId!==chromiumFrameRequestId)return;
    try{
      const data=await requestJSON(`${sessionStatusUrl(tab.sessionId)}/frame`,{
        method:'POST',
        body:browserWorkbenchRequestBody({zoom:tab.zoom||1})
      });
      if(requestId!==chromiumFrameRequestId)return;
      if(data&&data.frame){
        await drawBrowserWorkbenchFrame(canvas,data.frame);
        if(tab.loadStatus==='loading')setBrowserWorkbenchLoadStatus('success',tab,{message:'Page loaded.'});
      }
      if(data&&data.url&&data.url!==tab.url)applySessionState(data,tab);
      browserWorkbenchClearStatus(tab,{owner:'renderer'});
    }catch(err){
      if(requestId!==chromiumFrameRequestId)return;
      const message='Browser view stopped updating.';
      setStatus(message,'warning',tab,{owner:'renderer',kind:'error'});
      if(tab.loadStatus==='loading')setBrowserWorkbenchLoadStatus('error',tab,{message});
    }finally{
      if(requestId===chromiumFrameRequestId&&tab&&tab.sessionId&&tab.renderer==='chromium-stream'){
        chromiumFrameTimer=setTimeout(()=>pollBrowserWorkbenchChromiumFrame(tab,canvas,requestId),BROWSER_WORKBENCH_FRAME_POLL_MS);
      }
    }
  }

  function refreshBrowserWorkbenchDevtoolsLitePanel(tab){
    const target=tab||getActiveWorkbenchTab();
    const panel=viewportEl&&viewportEl.querySelector('.browser-workbench-devtools-region');
    if(target&&target.renderer==='iframe-bridge'&&target.devtoolsOpen===true&&panel){
      renderBrowserWorkbenchChiiDevtools(target,panel);
      return true;
    }
    renderActiveBrowserWorkbenchView();
    return false;
  }

  function syncBrowserWorkbenchIframeDevtoolsLite(tab){
    wireDom();
    const target=tab||getActiveWorkbenchTab();
    if(!viewportEl||!target||target.renderer!=='iframe-bridge')return false;
    // The viewport and docked panel are shared DOM. An async response for a
    // background tab may update that tab's state, but it must never project
    // its surface or DevTools over the currently selected tab.
    if(target.id!==activeBrowserWorkbenchTabId)return false;
    if(target.devtoolsOpen!==true){
      detachBrowserWorkbenchSplitPreservingSurface();
      return true;
    }
    return ensureBrowserWorkbenchSplitViewPreservingSurface(target);
  }

  function closeBrowserWorkbenchDevtools(tab){
    stopBrowserWorkbenchChromiumStream();
    const target=tab||getActiveWorkbenchTab();
    if(!target)return;
    target.devtoolsRequestId=(Number(target.devtoolsRequestId)||0)+1;
    target.devtoolsOpen=false;
    if(target.renderer==='iframe-bridge'){
      target.devtoolsUrl='';
      syncBrowserWorkbenchIframeDevtoolsLite(target);
    }else renderActiveBrowserWorkbenchView();
    persistBrowserWorkbenchTabs();
  }

  function renderBrowserWorkbenchChiiDevtools(tab,host){
    wireDom();
    const targetHost=host||viewportEl;
    if(!targetHost)return;
    targetHost.textContent='';
    const wrap=document.createElement('div');
    wrap.className='browser-workbench-devtools-wrap browser-workbench-chii-devtools-wrap';
    if(host)wrap.classList.add('browser-workbench-devtools-wrap--side');
    const bar=document.createElement('div');
    bar.className='browser-workbench-devtools-bar browser-workbench-chii-devtools-bar';
    const title=textEl('span','browser-workbench-devtools-title','DevTools');
    title.title='Developer tools for this page.';
    bar.appendChild(title);
    const close=textEl('button','browser-workbench-devtools-close','Close DevTools');
    close.type='button';
    close.addEventListener('click',()=>closeBrowserWorkbenchDevtools(tab));
    bar.appendChild(close);
    wrap.appendChild(bar);
    if(tab&&tab.devtoolsUrl){
      const frame=document.createElement('iframe');
      frame.className='browser-workbench-devtools-frame browser-workbench-chii-devtools-frame';
      frame.title=`${browserWorkbenchDisplayLabel(tab)} DevTools`;
      frame.src=tab.devtoolsUrl;
      frame.referrerPolicy='no-referrer';
      wrap.appendChild(frame);
    }else{
      wrap.appendChild(textEl('div','browser-workbench-chii-devtools-empty','DevTools is opening…'));
    }
    targetHost.appendChild(wrap);
  }

  function detachBrowserWorkbenchSplitPreservingSurface(){
    wireDom();
    if(!viewportEl)return false;
    const split=viewportEl.querySelector('.browser-workbench-split-wrap');
    if(split){
      const surface=split.querySelector('.browser-workbench-surface-region');
      if(surface){
        Array.from(surface.childNodes).forEach((node)=>viewportEl.insertBefore(node,split));
      }
      split.remove();
    }
    const dockedPanel=viewportEl.querySelector('.browser-workbench-devtools-region--docked');
    const dockedResizer=viewportEl.querySelector('.browser-workbench-devtools-resizer--docked');
    if(dockedPanel)dockedPanel.remove();
    if(dockedResizer)dockedResizer.remove();
    viewportEl.classList.remove('has-devtools-docked');
    viewportEl.style.removeProperty('--browser-workbench-devtools-width');
    return !!(split||dockedPanel||dockedResizer);
  }

  function ensureBrowserWorkbenchSplitViewPreservingSurface(tab){
    wireDom();
    if(!viewportEl||!tab)return false;
    const split=viewportEl.querySelector('.browser-workbench-split-wrap');
    if(split)detachBrowserWorkbenchSplitPreservingSurface();
    // The docked DevTools panel belongs to the selected tab, but so does the
    // left-hand page surface. Reattach that tab's preserved iframe before
    // rendering its panel; otherwise switching tabs leaves the prior page in
    // place while only DevTools changes owner.
    renderBrowserWorkbenchSurface(tab,viewportEl);
    const width=clampBrowserWorkbenchDevtoolsWidth(tab);
    viewportEl.classList.add('has-devtools-docked');
    viewportEl.style.setProperty('--browser-workbench-devtools-width',`${width}px`);
    let resizer=viewportEl.querySelector('.browser-workbench-devtools-resizer--docked');
    if(!resizer){
      resizer=document.createElement('div');
      resizer.className='browser-workbench-devtools-resizer browser-workbench-devtools-resizer--docked';
      resizer.setAttribute('role','separator');
      resizer.setAttribute('aria-orientation','vertical');
      resizer.setAttribute('aria-label','Resize DevTools panel');
      resizer.addEventListener('pointerdown',(event)=>startBrowserWorkbenchDevtoolsResize(event,tab));
      viewportEl.appendChild(resizer);
    }
    let panel=viewportEl.querySelector('.browser-workbench-devtools-region--docked');
    if(!panel){
      panel=document.createElement('div');
      panel.className='browser-workbench-devtools-region browser-workbench-devtools-region--docked';
      viewportEl.appendChild(panel);
    }
    panel.style.setProperty('--browser-workbench-devtools-width',`${width}px`);
    renderBrowserWorkbenchDevtools(tab,panel);
    return true;
  }

  function renderBrowserWorkbenchDevtools(tab,host){
    if(tab&&tab.renderer==='iframe-bridge'){
      renderBrowserWorkbenchChiiDevtools(tab,host);
      return;
    }
    if(!host){
      stopBrowserWorkbenchChromiumStream();
    }
    wireDom();
    const targetHost=host||viewportEl;
    if(!targetHost)return;
    targetHost.textContent='';
    const wrap=document.createElement('div');
    wrap.className='browser-workbench-devtools-wrap';
    if(host)wrap.classList.add('browser-workbench-devtools-wrap--side');
    const bar=document.createElement('div');
    bar.className='browser-workbench-devtools-bar';
    bar.appendChild(textEl('span','browser-workbench-devtools-title','Chrome DevTools'));
    const close=textEl('button','browser-workbench-devtools-close','Close DevTools');
    close.type='button';
    close.addEventListener('click',()=>closeBrowserWorkbenchDevtools(tab));
    bar.appendChild(close);
    const frame=document.createElement('iframe');
    frame.className='browser-workbench-devtools-frame';
    frame.title=`${browserWorkbenchDisplayLabel(tab)} DevTools`;
    frame.src=tab.devtoolsUrl;
    frame.referrerPolicy='no-referrer';
    wrap.appendChild(bar);
    wrap.appendChild(frame);
    targetHost.appendChild(wrap);
  }

  function clampBrowserWorkbenchDevtoolsWidth(tab){
    const viewportRect=viewportEl?viewportEl.getBoundingClientRect():null;
    const maxWidth=Math.max(BROWSER_WORKBENCH_MIN_DEVTOOLS_WIDTH,Math.round(((viewportRect&&viewportRect.width)||960)-320));
    const raw=Number.parseInt(tab&&tab.devtoolsWidth,10)||BROWSER_WORKBENCH_DEFAULT_DEVTOOLS_WIDTH;
    return Math.max(BROWSER_WORKBENCH_MIN_DEVTOOLS_WIDTH,Math.min(maxWidth,raw));
  }

  function renderBrowserWorkbenchSurface(tab,host){
    if(!host)return;
    const contentState=normalizeBrowserWorkbenchContentState(tab&&tab.contentState);
    host.dataset.browserContentState=contentState;
    if(contentState==='error'&&tab&&tab.navigationError)renderBrowserWorkbenchNavigationError(tab,host);
    else if(contentState==='restored'||contentState==='error'){
      host.textContent='';
      host.appendChild(textEl('div','browser-workbench-placeholder',browserWorkbenchContentPlaceholder(tab)));
    }
    else if(tab&&tab.renderer==='chromium-stream'&&tab.sessionId)renderBrowserWorkbenchChromiumStream(tab,host);
    else if(tab&&tab.renderer==='iframe-bridge'&&tab.bridgeUrl)renderBrowserWorkbenchFrame(tab,host);
    else{
      host.textContent='';
      host.appendChild(textEl('div','browser-workbench-placeholder',browserWorkbenchContentPlaceholder(tab)));
    }
  }

  function startBrowserWorkbenchDevtoolsResize(event,tab){
    if(!event||!tab||!viewportEl)return;
    event.preventDefault();
    event.stopPropagation();
    const resizer=event.currentTarget&&event.currentTarget.setPointerCapture?event.currentTarget:null;
    const pointerId=event.pointerId;
    const startX=event.clientX;
    const startWidth=clampBrowserWorkbenchDevtoolsWidth(tab);
    if(resizer&&typeof pointerId==='number'){
      try{resizer.setPointerCapture(pointerId);}catch(_){}
    }
    viewportEl.classList.add('is-resizing-devtools');
    document.body&&document.body.classList&&document.body.classList.add('browser-workbench-resizing-devtools');
    const applyWidth=(clientX)=>{
      const delta=startX-clientX;
      tab.devtoolsWidth=startWidth+delta;
      const width=clampBrowserWorkbenchDevtoolsWidth(tab);
      tab.devtoolsWidth=width;
      viewportEl.style.setProperty('--browser-workbench-devtools-width',`${width}px`);
      const panel=viewportEl.querySelector('.browser-workbench-devtools-region');
      if(panel)panel.style.setProperty('--browser-workbench-devtools-width',`${width}px`);
    };
    const onMove=(moveEvent)=>{
      if(!moveEvent)return;
      moveEvent.preventDefault();
      moveEvent.stopPropagation();
      applyWidth(moveEvent.clientX);
    };
    const cleanup=(upEvent)=>{
      if(upEvent){
        upEvent.preventDefault();
        upEvent.stopPropagation();
      }
      window.removeEventListener('pointermove',onMove,true);
      window.removeEventListener('pointerup',cleanup,true);
      window.removeEventListener('pointercancel',cleanup,true);
      if(resizer&&typeof pointerId==='number'){
        try{resizer.releasePointerCapture(pointerId);}catch(_){}
      }
      if(viewportEl)viewportEl.classList.remove('is-resizing-devtools');
      document.body&&document.body.classList&&document.body.classList.remove('browser-workbench-resizing-devtools');
      persistBrowserWorkbenchTabs();
    };
    window.addEventListener('pointermove',onMove,{capture:true,passive:false});
    window.addEventListener('pointerup',cleanup,{capture:true,once:true,passive:false});
    window.addEventListener('pointercancel',cleanup,{capture:true,once:true,passive:false});
  }

  function renderBrowserWorkbenchSplitView(tab){
    stopBrowserWorkbenchChromiumStream();
    wireDom();
    if(!viewportEl)return;
    viewportEl.textContent='';
    const split=document.createElement('div');
    split.className='browser-workbench-split-wrap';
    const surface=document.createElement('div');
    surface.className='browser-workbench-surface-region';
    const resizer=document.createElement('div');
    resizer.className='browser-workbench-devtools-resizer';
    resizer.setAttribute('role','separator');
    resizer.setAttribute('aria-orientation','vertical');
    resizer.setAttribute('aria-label','Resize DevTools panel');
    resizer.addEventListener('pointerdown',(event)=>startBrowserWorkbenchDevtoolsResize(event,tab));
    const panel=document.createElement('div');
    panel.className='browser-workbench-devtools-region';
    panel.style.setProperty('--browser-workbench-devtools-width',`${clampBrowserWorkbenchDevtoolsWidth(tab)}px`);
    split.appendChild(surface);
    split.appendChild(resizer);
    split.appendChild(panel);
    viewportEl.appendChild(split);
    renderBrowserWorkbenchSurface(tab,surface);
    renderBrowserWorkbenchDevtools(tab,panel);
  }

  function currentBrowserWorkbenchViewport(){
    wireDom();
    const surface=viewportEl&&viewportEl.querySelector('.browser-workbench-surface-region');
    const rect=(surface||viewportEl)?(surface||viewportEl).getBoundingClientRect():null;
    const width=Math.max(320,Math.round((rect&&rect.width)||1440));
    const height=Math.max(240,Math.round((rect&&rect.height)||900));
    const dpr=Math.max(0.5,Math.min(3,Number(window.devicePixelRatio)||1));
    return {width,height,device_pixel_ratio:dpr};
  }

  function browserWorkbenchRequestBody(extra){
    return JSON.stringify({
      viewport:currentBrowserWorkbenchViewport(),
      client_renderer:'iframe-bridge',
      ...(extra||{})
    });
  }

  function attachmentFromBrowserWorkbenchPayload(attachment){
    if(!attachment||!attachment.data)return null;
    const type=String(attachment.type||'image/png');
    const blob=base64ToBrowserWorkbenchBlob(attachment.data,type);
    const name=String(attachment.name||'browser-workbench-screenshot.png');
    try{
      return new File([blob],name,{type,lastModified:Date.now()});
    }catch(_){
      blob.name=name;
      return blob;
    }
  }

  function browserWorkbenchIntersectionRect(a,b){
    if(!a||!b)return null;
    const left=Math.max(a.left,b.left);
    const top=Math.max(a.top,b.top);
    const right=Math.min(a.right,b.right);
    const bottom=Math.min(a.bottom,b.bottom);
    const width=Math.max(0,right-left);
    const height=Math.max(0,bottom-top);
    if(width<=0||height<=0)return null;
    return {left,top,right,bottom,width,height};
  }

  function browserWorkbenchIframeViewportMetrics(frame){
    const win=frame&&frame.contentWindow;
    const doc=win&&win.document;
    const rect=frame&&frame.getBoundingClientRect?frame.getBoundingClientRect():null;
    if(!frame||!win||!rect||rect.width<=0||rect.height<=0)throw new Error('Iframe-proxy area capture is not available because the iframe surface is not ready.');
    const contentWidth=Math.max(1,Number(win.innerWidth)||Number(frame.clientWidth)||Math.round(rect.width));
    const contentHeight=Math.max(1,Number(win.innerHeight)||Number(frame.clientHeight)||Math.round(rect.height));
    const scaleX=rect.width/contentWidth;
    const scaleY=rect.height/contentHeight;
    const scrollX=Math.max(0,Math.round(Number(win.scrollX)||(doc&&doc.documentElement&&doc.documentElement.scrollLeft)||0));
    const scrollY=Math.max(0,Math.round(Number(win.scrollY)||(doc&&doc.documentElement&&doc.documentElement.scrollTop)||0));
    const dpr=Math.max(0.5,Math.min(3,Number(win.devicePixelRatio)||Number(window.devicePixelRatio)||1));
    return {rect,contentWidth,contentHeight,scaleX,scaleY,scrollX,scrollY,devicePixelRatio:dpr};
  }

  function browserWorkbenchIframeCropFromSurfaceClip(clip,tab){
    wireDom();
    const target=tab||getActiveWorkbenchTab();
    if(!target||target.renderer!=='iframe-bridge')throw new Error('Iframe-proxy area capture requires an iframe-proxy Browser Workbench tab.');
    const frame=activeBrowserWorkbenchIframe();
    const surface=viewportEl&&viewportEl.querySelector?viewportEl.querySelector('.browser-workbench-surface-region'):null;
    const surfaceRect=(surface||viewportEl)&&((surface||viewportEl).getBoundingClientRect? (surface||viewportEl).getBoundingClientRect():null);
    const viewport=clip&&clip.viewport&&typeof clip.viewport==='object'?clip.viewport:currentBrowserWorkbenchViewport();
    if(!clip||!surfaceRect||surfaceRect.width<=0||surfaceRect.height<=0)throw new Error('Iframe-proxy area capture could not read the selected rectangle.');
    const metrics=browserWorkbenchIframeViewportMetrics(frame);
    const selected={
      left:surfaceRect.left+(Number(clip.x)||0)*surfaceRect.width/Math.max(1,Number(viewport.width)||surfaceRect.width),
      top:surfaceRect.top+(Number(clip.y)||0)*surfaceRect.height/Math.max(1,Number(viewport.height)||surfaceRect.height),
      right:surfaceRect.left+(Number(clip.x||0)+Number(clip.width||0))*surfaceRect.width/Math.max(1,Number(viewport.width)||surfaceRect.width),
      bottom:surfaceRect.top+(Number(clip.y||0)+Number(clip.height||0))*surfaceRect.height/Math.max(1,Number(viewport.height)||surfaceRect.height)
    };
    selected.width=Math.max(0,selected.right-selected.left);
    selected.height=Math.max(0,selected.bottom-selected.top);
    const iframeRect={left:metrics.rect.left,top:metrics.rect.top,right:metrics.rect.right,bottom:metrics.rect.bottom,width:metrics.rect.width,height:metrics.rect.height};
    const intersection=browserWorkbenchIntersectionRect(selected,iframeRect);
    if(!intersection||intersection.width<4||intersection.height<4)throw new Error('Iframe-proxy area capture failed because the selected rectangle is outside the iframe viewport.');
    const cssX=(intersection.left-metrics.rect.left)/Math.max(0.0001,metrics.scaleX);
    const cssY=(intersection.top-metrics.rect.top)/Math.max(0.0001,metrics.scaleY);
    const cssWidth=intersection.width/Math.max(0.0001,metrics.scaleX);
    const cssHeight=intersection.height/Math.max(0.0001,metrics.scaleY);
    const x=Math.max(0,Math.min(metrics.contentWidth,cssX));
    const y=Math.max(0,Math.min(metrics.contentHeight,cssY));
    const right=Math.max(x,Math.min(metrics.contentWidth,cssX+cssWidth));
    const bottom=Math.max(y,Math.min(metrics.contentHeight,cssY+cssHeight));
    const width=Math.max(1,right-x);
    const height=Math.max(1,bottom-y);
    return {
      x,y,width,height,
      iframeViewport:{width:metrics.contentWidth,height:metrics.contentHeight},
      documentRect:{x:x+metrics.scrollX,y:y+metrics.scrollY,width,height,scrollX:metrics.scrollX,scrollY:metrics.scrollY},
      sourceClip:{x:Number(clip.x)||0,y:Number(clip.y)||0,width:Number(clip.width)||0,height:Number(clip.height)||0,viewportWidth:Number(viewport.width)||0,viewportHeight:Number(viewport.height)||0},
      displayRect:{left:intersection.left,top:intersection.top,width:intersection.width,height:intersection.height},
      scale:{x:metrics.scaleX,y:metrics.scaleY,devicePixelRatio:metrics.devicePixelRatio}
    };
  }

  async function browserWorkbenchCropIframeAttachment(attachment,crop){
    if(!attachment||!attachment.data)throw new Error('Iframe-proxy area capture returned no viewport screenshot data to crop.');
    if(!crop||!crop.iframeViewport)throw new Error('Iframe-proxy area capture has no valid crop rectangle.');
    if(typeof createImageBitmap!=='function')throw new Error('This browser cannot decode iframe DOM screenshots for area cropping.');
    const sourceBlob=base64ToBrowserWorkbenchBlob(attachment.data,attachment.type||'image/png');
    const bitmap=await createImageBitmap(sourceBlob);
    try{
      const imageWidth=Math.max(1,Number(attachment.width)||bitmap.width||1);
      const imageHeight=Math.max(1,Number(attachment.height)||bitmap.height||1);
      const scaleX=imageWidth/Math.max(1,Number(crop.iframeViewport.width)||imageWidth);
      const scaleY=imageHeight/Math.max(1,Number(crop.iframeViewport.height)||imageHeight);
      const sx=Math.max(0,Math.min(imageWidth-1,Math.round(crop.x*scaleX)));
      const sy=Math.max(0,Math.min(imageHeight-1,Math.round(crop.y*scaleY)));
      const sw=Math.max(1,Math.min(imageWidth-sx,Math.round(crop.width*scaleX)));
      const sh=Math.max(1,Math.min(imageHeight-sy,Math.round(crop.height*scaleY)));
      const canvas=document.createElement('canvas');
      canvas.width=sw;
      canvas.height=sh;
      const ctx=canvas.getContext('2d');
      if(!ctx)throw new Error('This browser cannot create a canvas for one-time iframe area cropping.');
      ctx.drawImage(bitmap,sx,sy,sw,sh,0,0,sw,sh);
      const dataUrl=canvas.toDataURL('image/png');
      return {
        data:dataUrl.includes(',')?dataUrl.split(',').pop():dataUrl,
        type:'image/png',
        name:BROWSER_WORKBENCH_IFRAME_AREA_CAPTURE_FILENAME,
        width:sw,
        height:sh,
        method:'iframe-dom-capture-crop',
        crop:{...crop,pixels:{x:sx,y:sy,width:sw,height:sh}},
        limitations:attachment.limitations||['Canvas, video, WebGL, nested iframes, some fonts, and advanced CSS effects may not appear exactly.']
      };
    }finally{
      if(bitmap&&typeof bitmap.close==='function')bitmap.close();
    }
  }

  function settleBrowserWorkbenchIframeCapture(detail){
    const requestId=String(detail&&detail.requestId||'');
    const pending=requestId?browserWorkbenchIframeCapturePending.get(requestId):null;
    if(!pending)return false;
    browserWorkbenchIframeCapturePending.delete(requestId);
    clearTimeout(pending.timeout);
    if(detail&&detail.ok===true&&detail.attachment&&detail.attachment.data){
      pending.resolve(detail);
    }else{
      const message=String(detail&&detail.message||detail&&detail.error||'Iframe-proxy screenshot capture failed.');
      const error=new Error(message);
      error.data=detail||{};
      pending.reject(error);
    }
    return true;
  }

  function requestBrowserWorkbenchIframeScreenshot(tab,options){
    const opts=options&&typeof options==='object'?options:{};
    const target=tab||getActiveWorkbenchTab();
    const frame=activeBrowserWorkbenchIframe();
    if(!target||target.renderer!=='iframe-bridge'||!target.sessionId)throw new Error('Open an iframe-proxy Browser Workbench session before taking screenshots.');
    if(!frame||!frame.contentWindow)throw new Error('Iframe-proxy screenshot is not available because DOM capture bridge is not ready.');
    const requestId=`iframe-capture-${Date.now()}-${++browserWorkbenchIframeCaptureRequestId}`;
    const state=target.devtoolsLite&&typeof target.devtoolsLite==='object'?target.devtoolsLite:{};
    return new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>{
        browserWorkbenchIframeCapturePending.delete(requestId);
        reject(new Error('Iframe-proxy screenshot timed out. The DOM capture bridge did not respond.'));
      },BROWSER_WORKBENCH_IFRAME_CAPTURE_TIMEOUT_MS);
      browserWorkbenchIframeCapturePending.set(requestId,{resolve,reject,timeout,tabId:target.id});
      try{
        frame.contentWindow.postMessage({
          source:'hermes-browser-workbench-parent',
          type:'hermes:capture-screenshot',
          requestId,
          sessionId:target.sessionId,
          frameId:state.frameId||'',
          mode:String(opts.mode||'viewport'),
          name:String(opts.name||BROWSER_WORKBENCH_IFRAME_CAPTURE_FILENAME)
        },window.location.origin);
      }catch(err){
        clearTimeout(timeout);
        browserWorkbenchIframeCapturePending.delete(requestId);
        reject(err);
      }
    });
  }

  async function attachBrowserWorkbenchIframeScreenshot(active,options){
    const opts=options&&typeof options==='object'?options:{};
    const statusToken=opts.statusToken||null;
    const crop=opts.clip?browserWorkbenchIframeCropFromSurfaceClip(opts.clip,active):null;
    const mode=opts.fullPage===true?'full-page':'viewport';
    const name=opts.fullPage===true?BROWSER_WORKBENCH_IFRAME_FULL_CAPTURE_FILENAME:BROWSER_WORKBENCH_IFRAME_CAPTURE_FILENAME;
    const data=await requestBrowserWorkbenchIframeScreenshot(active,{mode,name});
    active.iframeCaptureReady=true;
    let attachment=data&&data.attachment;
    if(crop)attachment=await browserWorkbenchCropIframeAttachment(attachment,crop);
    const file=attachmentFromBrowserWorkbenchPayload(attachment);
    if(!file){
      browserWorkbenchResolveStatus(statusToken,'Screenshot could not be created.',{kind:'error',tone:'warning'});
      return data;
    }
    if(typeof window.attachFilesToPrompt==='function'){
      window.attachFilesToPrompt([file]);
      browserWorkbenchResolveStatus(statusToken,`${file.name||'Screenshot'} attached.`,{kind:'temporary',tone:'ready'});
    }else{
      browserWorkbenchResolveStatus(statusToken,'Screenshot captured, but it could not be attached.',{kind:'error',tone:'warning'});
    }
    return crop?{...data,attachment,clip:crop}:data;
  }
  async function attachBrowserWorkbenchIframeFullPageScreenshot(){
    const active=getActiveWorkbenchTab();
    const capabilities=browserWorkbenchRendererCapabilities(active);
    if(!active||!active.sessionId||active.renderer!=='iframe-bridge'||!capabilities.takeFullPageScreenshot){
      setStatus(capabilities.fullPageScreenshotMessage||'Full-page screenshots are unavailable.','warning',active,{owner:'capture',kind:'error'});
      return null;
    }
    const statusToken=setStatus('Capturing full page…','muted',active,{owner:'capture',kind:'progress',resetTransient:true});
    try{
      return await attachBrowserWorkbenchIframeScreenshot(active,{fullPage:true,statusToken});
    }catch(err){
      const message='Full-page screenshot failed.';
      browserWorkbenchResolveStatus(statusToken,message,{kind:'error',tone:'warning'});
      return err&&err.data?err.data:{ok:false,error:message,message};
    }
  }

  async function attachBrowserWorkbenchScreenshot(clip){
    const active=getActiveWorkbenchTab();
    const capabilities=browserWorkbenchRendererCapabilities(active);
    if(!active||!active.sessionId){
      setStatus(capabilities.screenshotMessage,'warning',active,{owner:'capture',kind:'error'});
      return null;
    }
    if(active.renderer==='iframe-bridge'){
      const capabilityKey=clip?'captureAreaScreenshot':'takeScreenshot';
      const capabilityMessage=clip?capabilities.areaScreenshotMessage:capabilities.screenshotMessage;
      if(!capabilities[capabilityKey]){
        setStatus(capabilityMessage,'warning',active,{owner:'capture',kind:'error'});
        return null;
      }
      const statusToken=setStatus(clip?'Capturing selected area…':'Capturing screenshot…','muted',active,{owner:'capture',kind:'progress',resetTransient:true});
      try{
        return await attachBrowserWorkbenchIframeScreenshot(active,{clip:clip||null,statusToken});
      }catch(err){
        const message='Screenshot failed.';
        browserWorkbenchResolveStatus(statusToken,message,{kind:'error',tone:'warning'});
        return err&&err.data?err.data:{ok:false,error:message,message};
      }
    }
    if(!capabilities.takeScreenshot){
      setStatus(capabilities.screenshotMessage,'warning',active,{owner:'capture',kind:'error'});
      return null;
    }
    const statusToken=setStatus(clip?'Capturing selected area…':'Capturing screenshot…','muted',active,{owner:'capture',kind:'progress',resetTransient:true});
    let data;
    try{
      data=await requestJSON(`${sessionStatusUrl(active.sessionId)}/screenshot`,{
        method:'POST',
        body:browserWorkbenchRequestBody({zoom:active.zoom||1,clip:clip||undefined})
      });
    }catch(err){
      browserWorkbenchResolveStatus(statusToken,'Screenshot failed.',{kind:'error',tone:'warning'});
      throw err;
    }
    applySessionState(data,active);
    const file=attachmentFromBrowserWorkbenchPayload(data&&data.attachment);
    if(!file){
      browserWorkbenchResolveStatus(statusToken,'Screenshot could not be created.',{kind:'error',tone:'warning'});
      return data;
    }
    if(typeof window.attachFilesToPrompt==='function'){
      window.attachFilesToPrompt([file]);
      browserWorkbenchResolveStatus(statusToken,`${file.name||'Screenshot'} attached.`,{kind:'temporary',tone:'ready'});
    }else{
      browserWorkbenchResolveStatus(statusToken,'Screenshot captured, but it could not be attached.',{kind:'error',tone:'warning'});
    }
    return data;
  }

  async function startBrowserWorkbenchAreaCapture(){
    const active=getActiveWorkbenchTab();
    const capabilities=browserWorkbenchRendererCapabilities(active);
    if(!active||!active.sessionId||!capabilities.captureAreaScreenshot){
      setStatus(capabilities.areaScreenshotMessage,'warning',active,{owner:'capture',kind:'error'});
      return false;
    }
    if(active.devtoolsOpen===true&&active.devtoolsUrl){
      setStatus('Close DevTools before selecting an area.','warning',active,{owner:'capture',kind:'error'});
      return false;
    }
    areaCaptureMode=true;
    areaCaptureStart=null;
    suppressNextViewportClick=true;
    if(areaCaptureBox){areaCaptureBox.remove();areaCaptureBox=null;}
    if(viewportEl){
      viewportEl.classList.add('area-capturing');
      viewportEl.focus({preventScroll:true});
    }
    const prompt='Drag over the page to select an area. Press Escape to cancel.';
    setStatus(prompt,'muted',active,{owner:'area-capture',kind:'persistent',resetTransient:true});
    return true;
  }

  function cancelBrowserWorkbenchAreaCapture(tab){
    const target=tab||getActiveWorkbenchTab();
    areaCaptureMode=false;
    areaCaptureStart=null;
    if(areaCaptureBox){areaCaptureBox.remove();areaCaptureBox=null;}
    if(viewportEl)viewportEl.classList.remove('area-capturing');
    browserWorkbenchClearStatus(target,{owner:'area-capture'});
  }

  function updateBrowserWorkbenchAreaBox(point){
    if(!viewportEl||!areaCaptureStart||!point)return null;
    const viewport=currentBrowserWorkbenchViewport();
    const surface=viewportEl.querySelector('.browser-workbench-surface-region');
    const rect=(surface||viewportEl).getBoundingClientRect();
    const left=Math.min(areaCaptureStart.x,point.x);
    const top=Math.min(areaCaptureStart.y,point.y);
    const width=Math.abs(point.x-areaCaptureStart.x);
    const height=Math.abs(point.y-areaCaptureStart.y);
    const cssLeft=left*rect.width/viewport.width;
    const cssTop=top*rect.height/viewport.height;
    const cssWidth=width*rect.width/viewport.width;
    const cssHeight=height*rect.height/viewport.height;
    if(!areaCaptureBox){
      areaCaptureBox=document.createElement('div');
      areaCaptureBox.className='browser-workbench-area-capture-box';
      (surface||viewportEl).appendChild(areaCaptureBox);
    }
    areaCaptureBox.style.left=`${cssLeft}px`;
    areaCaptureBox.style.top=`${cssTop}px`;
    areaCaptureBox.style.width=`${cssWidth}px`;
    areaCaptureBox.style.height=`${cssHeight}px`;
    return {x:left,y:top,width,height,viewport:{width:viewport.width,height:viewport.height,device_pixel_ratio:viewport.device_pixel_ratio},displayRect:{left:rect.left+cssLeft,top:rect.top+cssTop,width:cssWidth,height:cssHeight}};
  }

  function closeBrowserWorkbenchMenu(){
    wireDom();
    const wasOpen=browserWorkbenchActionsMenuOpen||!!(menuEl&&!menuEl.hidden);
    browserWorkbenchActionsMenuOpen=false;
    if(menuEl)menuEl.hidden=true;
    if(menuEl){
      menuEl.style.visibility='';
      menuEl.style.left='';
      menuEl.style.top='';
      menuEl.style.maxHeight='';
      menuEl.removeAttribute('aria-hidden');
    }
    browserWorkbenchActionsMenuBounds=null;
    if(menuButton)menuButton.setAttribute('aria-expanded','false');
  }

  function positionBrowserWorkbenchMenu(){
    wireDom();
    if(!menuEl||!menuButton||!browserWorkbenchActionsMenuOpen)return;
    const rect=menuButton.getBoundingClientRect();
    const margin=8;
    const viewportWidth=Math.max(document.documentElement.clientWidth||0,window.innerWidth||0);
    const viewportHeight=Math.max(document.documentElement.clientHeight||0,window.innerHeight||0);
    const maxHeight=Math.max(180,viewportHeight-margin*2);
    menuEl.hidden=false;
    menuEl.style.visibility='hidden';
    menuEl.style.left='0px';
    menuEl.style.top='0px';
    menuEl.style.maxHeight=`${maxHeight}px`;
    const menuRect=menuEl.getBoundingClientRect();
    const width=Math.min(menuRect.width||280,Math.max(220,viewportWidth-margin*2));
    const height=Math.min(menuRect.height||360,maxHeight);
    const anchorRight=Number.isFinite(rect.right)?rect.right:viewportWidth-margin;
    const anchorBottom=Number.isFinite(rect.bottom)?rect.bottom:margin;
    const anchorTop=Number.isFinite(rect.top)?rect.top:margin;
    const left=Math.max(margin,Math.min(anchorRight-width,viewportWidth-width-margin));
    const belowTop=anchorBottom+6;
    const aboveTop=anchorTop-height-6;
    let top=belowTop;
    if(belowTop+height>viewportHeight-margin&&aboveTop>=margin)top=aboveTop;
    top=Math.max(margin,Math.min(top,viewportHeight-height-margin));
    menuEl.style.left=`${Math.round(left)}px`;
    menuEl.style.top=`${Math.round(top)}px`;
    browserWorkbenchActionsMenuBounds={x:Math.round(left),y:Math.round(top),width:Math.round(width),height:Math.round(height)};
    menuEl.hidden=false;
    menuEl.removeAttribute('aria-hidden');
    menuEl.style.visibility='';
  }

  function openBrowserWorkbenchMenu(){
    wireDom();
    if(!menuEl||!menuButton)return;
    closeBrowserWorkbenchUrlSuggestions();
    menuEl.hidden=false;
    menuButton.setAttribute('aria-expanded','true');
    updateBrowserWorkbenchZoomLabel();
    updateBrowserWorkbenchActionMenuCapabilities();
    browserWorkbenchActionsMenuOpen=true;
    positionBrowserWorkbenchMenu();
  }

  function toggleBrowserWorkbenchMenu(){
    wireDom();
    if(!menuEl||!menuButton)return;
    const opening=!browserWorkbenchActionsMenuOpen;
    if(opening)openBrowserWorkbenchMenu();
    else closeBrowserWorkbenchMenu();
  }

  function browserWorkbenchMenuActionKeepsOpen(action){
    return action==='zoom-out'||action==='zoom-in'||action==='set-zoom';
  }



  function updateBrowserWorkbenchZoomLabel(options){
    wireDom();
    const opts=options&&typeof options==='object'?options:{};
    const active=getActiveWorkbenchTab();
    const zoom=Math.round(((active&&active.zoom)||1)*100);
    if(menuZoomInput&&(opts.force||document.activeElement!==menuZoomInput))menuZoomInput.value=String(zoom);
  }

  function applyBrowserWorkbenchSurfaceZoom(tab,host){
    const target=tab||getActiveWorkbenchTab();
    if(!target)return;
    const zoom=Math.max(0.25,Math.min(3,Number(target.zoom)||1));
    // Preserved Browser tabs share one viewport host. Resolve the iframe from
    // the target tab's owned surface so a hidden earlier tab cannot receive
    // the active tab's zoom mutation.
    const frame=target.surfaceNode&&target.surfaceNode.querySelector
      ?target.surfaceNode.querySelector('.browser-workbench-frame')
      :null;
    if(frame){
      frame.style.transformOrigin='0 0';
      frame.style.transform=zoom===1?'':`scale(${zoom})`;
      frame.style.width=zoom===1?'':`${100/zoom}%`;
      frame.style.height=zoom===1?'':`${100/zoom}%`;
      frame.style.flex=zoom===1?'':'0 0 auto';
    }
  }

  function wireBrowserWorkbenchZoomInput(input){
    if(!input||input.dataset.browserWorkbenchWired)return;
    input.dataset.browserWorkbenchWired='1';
    input.addEventListener('focus',()=>input.select());
    input.addEventListener('keydown',(event)=>{
      if(event.key==='Enter'){
        event.preventDefault();
        void applyBrowserWorkbenchZoomInput(input);
        input.blur();
      }else if(event.key==='Escape'){
        updateBrowserWorkbenchZoomLabel({force:true});
        input.blur();
      }
    });
    input.addEventListener('blur',()=>void applyBrowserWorkbenchZoomInput(input));
  }

  function parseBrowserWorkbenchZoomValue(value){
    const raw=String(value||'').trim().replace(/,/g,'.');
    if(!raw)return null;
    const number=Number.parseFloat(raw.replace(/%/g,''));
    if(!Number.isFinite(number)||number<=0)return null;
    const factor=raw.indexOf('%')!==-1||number>3?number/100:number;
    return Math.max(0.25,Math.min(3,factor));
  }

  async function applyBrowserWorkbenchZoomInput(input){
    wireDom();
    const source=input||menuZoomInput;
    if(!source)return null;
    const next=parseBrowserWorkbenchZoomValue(source.value);
    if(next===null){
      updateBrowserWorkbenchZoomLabel({force:true});
      setStatus('Enter a zoom between 25% and 300%.','warning',getActiveWorkbenchTab(),{owner:'zoom',kind:'error'});
      return null;
    }
    return setBrowserWorkbenchZoom(next);
  }

  async function applyBrowserWorkbenchZoomValue(value){
    const next=parseBrowserWorkbenchZoomValue(value);
    if(next===null){
      updateBrowserWorkbenchZoomLabel({force:true});
      setStatus('Enter a zoom between 25% and 300%.','warning',getActiveWorkbenchTab(),{owner:'zoom',kind:'error'});
      return null;
    }
    return setBrowserWorkbenchZoom(next);
  }

  async function setBrowserWorkbenchZoom(nextZoom){
    const active=getActiveWorkbenchTab();
    if(!active)return;
    active.zoom=Math.max(0.25,Math.min(3,nextZoom));
    persistBrowserWorkbenchTabs();
    applyBrowserWorkbenchSurfaceZoom(active);
    refreshBrowserWorkbenchOverlayProjection(active);
    updateBrowserWorkbenchZoomLabel({force:true});
    setStatus(`Zoom set to ${Math.round(active.zoom*100)}%.`,'ready',active,{owner:'zoom',kind:'temporary'});
    return active.zoom;
  }

  function stopBrowserWorkbenchEmbeddedFrame(tab){
    if(!tab||tab.renderer!=='iframe-bridge')return;
    const frame=tab.surfaceNode&&tab.surfaceNode.querySelector
      ?tab.surfaceNode.querySelector('.browser-workbench-frame')
      :null;
    if(!frame)return;
    try{
      if(frame.contentWindow&&typeof frame.contentWindow.stop==='function')frame.contentWindow.stop();
    }catch(_err){
      // Cross-origin iframes may block direct stop(); backend/native stop-loading still runs.
    }
  }

  async function stopBrowserWorkbenchLoading(tabId){
    wireDom();
    const target=tabById(tabId)||getActiveWorkbenchTab();
    if(!target||!target.sessionId){
      setStatus('Open a page before stopping it.','warning',target,{owner:'navigation',kind:'error'});
      return null;
    }
    if(target.loadStatus!=='loading')return null;
    if(workbenchCapabilities.stop_loading!==true){
      const message='Stop loading is unavailable right now.';
      setStatus(message,'warning',target,{owner:'navigation',kind:'error'});
      return {ok:false,error:message};
    }
    stopBrowserWorkbenchEmbeddedFrame(target);
    const statusToken=setStatus('Stopping page load…','muted',target,{owner:'navigation',kind:'progress',resetTransient:true});
    try{
      const data=await requestJSON(`${sessionStatusUrl(target.sessionId)}/stop-loading`,{
        method:'POST',
        body:browserWorkbenchRequestBody({zoom:target.zoom||1})
      });
      applySessionState(data,target);
      if(target.loadStatus==='loading')setBrowserWorkbenchLoadStatus('idle',target,{autoReset:false});
      browserWorkbenchResolveStatus(statusToken,'Loading stopped.',{kind:'temporary',tone:'ready'});
      return data;
    }catch(err){
      const data=err&&err.data?err.data:null;
      const message='Couldn’t stop loading.';
      browserWorkbenchResolveStatus(statusToken,message,{kind:'error',tone:'warning'});
      setBrowserWorkbenchLoadStatus('error',target,{message});
      return data||{ok:false,error:message};
    }
  }

  function handleBrowserWorkbenchReloadButtonClick(){
    const active=getActiveWorkbenchTab();
    if(active&&active.loadStatus==='loading'){
      if(workbenchCapabilities.stop_loading!==true){
        setStatus('Stop loading is unavailable right now.','warning',active,{owner:'navigation',kind:'error'});
        return;
      }
      void stopBrowserWorkbenchLoading(active.id);
      return;
    }
    void navigateBrowserWorkbenchHistory('reload');
  }



  async function runBrowserWorkbenchSessionAction(action,extra){
    const active=getActiveWorkbenchTab();
    if(!active||!active.sessionId){
      setStatus('Open a page before using this action.','warning',active,{owner:'action',kind:'error'});
      return null;
    }
    const loadAction=action==='reload'||action==='hard-reload';
    if(loadAction&&active.loadStatus==='loading'){
      if(workbenchCapabilities.stop_loading!==true){
        setStatus('Stop loading is unavailable right now.','warning',active,{owner:'navigation',kind:'error'});
        return null;
      }
      return stopBrowserWorkbenchLoading(active.id);
    }
    const actionLabels={'clear-history':'history','clear-cookies':'cookies','clear-cache':'cache'};
    const requestId=loadAction?beginBrowserWorkbenchNavigation(active,browserWorkbenchRetryUrl(active),{message:'Reloading page…'}):active.navigationRequestId;
    const statusToken=!loadAction&&actionLabels[action]?setStatus(`Clearing ${actionLabels[action]}…`,'muted',active,{owner:'maintenance',kind:'progress',resetTransient:true}):null;
    try{
      const data=await requestJSON(`${sessionStatusUrl(active.sessionId)}/${action}`,{
        method:'POST',
        body:browserWorkbenchRequestBody(extra)
      });
      if(loadAction&&requestId!==active.navigationRequestId)return null;
      applySessionState(data,active);
      if(action==='clear-history'){
        clearBrowserWorkbenchPendingHistoryTraversal(active);
        const current=browserWorkbenchSafeHistoryUrl(active.currentUrl||active.url);
        active.historyEntries=current?[current]:[];
        active.historyIndex=current?0:-1;
        active.nativeHistoryPreviousUrl='';
        active.nativeHistoryNextUrl='';
        applyBrowserWorkbenchTabHistoryCapabilities(active,false,false);
        renderActiveBrowserWorkbenchView();
        persistBrowserWorkbenchTabs();
      }
      if(statusToken)browserWorkbenchResolveStatus(statusToken,`${actionLabels[action][0].toUpperCase()+actionLabels[action].slice(1)} cleared.`,{kind:'temporary',tone:'ready'});
      return data;
    }catch(err){
      if(loadAction&&requestId!==active.navigationRequestId)return null;
      const message=(err&&err.data&&(err.data.error||err.data.message))||(err&&err.message)||'Browser action failed.';
      if(loadAction)setBrowserWorkbenchLoadStatus('error',active,{message});
      if(statusToken)browserWorkbenchResolveStatus(statusToken,`Couldn’t clear ${actionLabels[action]}.`,{kind:'error',tone:'warning'});
      throw err;
    }
  }

  async function openBrowserWorkbenchDevtools(options){
    const active=getActiveWorkbenchTab();
    if(!active||!active.sessionId){
      setStatus('Open a page before opening DevTools.','warning',active,{owner:'devtools',kind:'error'});
      return null;
    }
    const opts=options&&typeof options==='object'?options:{};
    const mode=String(opts.mode||'panel').toLowerCase()==='popout'?'popout':'panel';
    const requestedSessionId=active.sessionId;
    const requestId=(Number(active.devtoolsRequestId)||0)+1;
    active.devtoolsRequestId=requestId;
    const requestIsCurrent=()=>tabById(active.id)===active&&active.sessionId===requestedSessionId&&active.devtoolsRequestId===requestId;
    const statusToken=setStatus('Opening DevTools…','muted',active,{owner:'devtools',kind:'progress',resetTransient:true});
    if(active.renderer==='iframe-bridge'){
      try{
        const data=await requestJSON(`${sessionStatusUrl(active.sessionId)}/devtools`,{method:'POST',body:browserWorkbenchRequestBody({mode})});
        if(!requestIsCurrent())return data||null;
        if(data&&data.devtools_url){
          active.devtoolsUrl=String(data.devtools_url);
          if(mode==='popout'){
            const popup=window.open(active.devtoolsUrl,'_blank','noopener,noreferrer');
            active.devtoolsOpen=false;
            persistBrowserWorkbenchTabs();
            browserWorkbenchResolveStatus(statusToken,popup?'DevTools opened in a new window.':'Pop-up blocked. Allow pop-ups or open DevTools in the panel.',{kind:popup?'temporary':'error',tone:popup?'ready':'warning'});
            return data;
          }
          active.devtoolsOpen=true;
          syncBrowserWorkbenchIframeDevtoolsLite(active);
          persistBrowserWorkbenchTabs();
          syncBrowserWorkbenchIframeSelectionMode(active);
          const frame=activeBrowserWorkbenchIframe();
          try{if(frame&&frame.contentWindow)frame.contentWindow.postMessage({source:'hermes-browser-workbench-parent',type:'devtools-ping'},window.location.origin);}catch(_){}
          browserWorkbenchResolveStatus(statusToken,'DevTools opened.',{kind:'temporary',tone:'ready'});
          return data;
        }
        const message='DevTools could not be opened.';
        browserWorkbenchResolveStatus(statusToken,message,{kind:'error',tone:'warning'});
        return data||null;
      }catch(err){
        if(!requestIsCurrent())return null;
        const message='DevTools could not be opened.';
        browserWorkbenchResolveStatus(statusToken,message,{kind:'error',tone:'warning'});
        return null;
      }
    }
    active.devtoolsOpen=mode==='panel';
    stopBrowserWorkbenchChromiumStream();
    try{
      await delayBrowserWorkbench(BROWSER_WORKBENCH_DEVTOOLS_QUIESCE_MS);
      const data=await requestJSON(`${sessionStatusUrl(active.sessionId)}/devtools`,{method:'POST',body:browserWorkbenchRequestBody({mode})});
      if(!requestIsCurrent())return data||null;
      applySessionState(data,active);
      if(data&&data.devtools_url&&mode==='panel'){
        active.devtoolsUrl=String(data.devtools_url);
        active.devtoolsOpen=true;
        renderActiveBrowserWorkbenchView();
        persistBrowserWorkbenchTabs();
        browserWorkbenchResolveStatus(statusToken,'DevTools opened.',{kind:'temporary',tone:'ready'});
      }else if(data&&data.devtools_url){
        active.devtoolsUrl=String(data.devtools_url);
        active.devtoolsOpen=true;
        renderActiveBrowserWorkbenchView();
        persistBrowserWorkbenchTabs();
        browserWorkbenchResolveStatus(statusToken,'DevTools opened in the panel.',{kind:'temporary',tone:'ready'});
      }else{
        active.devtoolsOpen=false;
        browserWorkbenchResolveStatus(statusToken,'DevTools could not be opened.',{kind:'error',tone:'warning'});
      }
      return data;
    }catch(err){
      if(!requestIsCurrent())return null;
      active.devtoolsOpen=false;
      const message='DevTools could not be opened.';
      browserWorkbenchResolveStatus(statusToken,message,{kind:'error',tone:'warning'});
      return null;
    }
  }

  async function handleBrowserWorkbenchMenuAction(action,extra){
    if(!browserWorkbenchMenuActionKeepsOpen(action))closeBrowserWorkbenchMenu();
    const active=getActiveWorkbenchTab();
    const detail=extra&&typeof extra==='object'?extra:{};
    try{
      if(action==='copy-url'){
        const url=(urlInput&&urlInput.value)||(active&&active.url)||'';
        if(navigator.clipboard&&url)await navigator.clipboard.writeText(url);
        setStatus(url?'URL copied.':'No URL to copy.',url?'ready':'warning',active,{owner:'clipboard',kind:url?'temporary':'error'});
        return true;
      }
      if(action==='zoom-out')return await setBrowserWorkbenchZoom(((active&&active.zoom)||1)-0.1);
      if(action==='zoom-in')return await setBrowserWorkbenchZoom(((active&&active.zoom)||1)+0.1);
      if(action==='set-zoom')return await applyBrowserWorkbenchZoomValue(detail.value);
      if(action==='take-screenshot')return await attachBrowserWorkbenchScreenshot();
      if(action==='take-full-page-screenshot')return await attachBrowserWorkbenchIframeFullPageScreenshot();
      if(action==='capture-area-screenshot')return startBrowserWorkbenchAreaCapture();
      if(action==='open-devtools'||action==='open-devtools-panel')return openBrowserWorkbenchDevtools({mode:'panel'});
      if(action==='open-devtools-popout')return openBrowserWorkbenchDevtools({mode:'popout'});
      if(action==='hard-reload')return runBrowserWorkbenchSessionAction('hard-reload');
      if(['clear-history','clear-cookies','clear-cache'].indexOf(action)!==-1)return runBrowserWorkbenchSessionAction(action);
    }catch(err){
      setStatus('The browser action failed.','warning',active,{owner:'action',kind:'error'});
    }
    return null;
  }

  function sessionStatusUrl(sessionId){
    return `/api/browser-workbench/session/${encodeURIComponent(sessionId)}`;
  }

  async function requestJSON(path,opts){
    if(typeof api==='function'){
      return api(path,opts||{});
    }
    const rel=path.startsWith('/')?path.slice(1):path;
    const res=await fetch(rel,{credentials:'include',headers:{'Content-Type':'application/json'},...(opts||{})});
    let data={};
    try{data=await res.json();}catch(_){data={};}
    if(!res.ok){
      const err=new Error(data.error||data.message||`Browser Workbench request failed (${res.status})`);
      err.status=res.status;
      err.data=data;
      throw err;
    }
    return data;
  }

  function isBrowserWorkbenchSessionMissingError(err){
    const data=err&&err.data&&typeof err.data==='object'?err.data:{};
    const text=String((data&&(data.error||data.message))||(err&&err.message)||'').toLowerCase();
    return err&&err.status===404&&(data.status==='missing'||text.indexOf('browser workbench session not found')!==-1);
  }

  function clearBrowserWorkbenchStaleSession(tab){
    const target=tab||getActiveWorkbenchTab();
    if(!target)return;
    clearBrowserWorkbenchLoadTimers(target);
    clearBrowserWorkbenchPendingHistoryTraversal(target);
    if(selectionModeTabId===target.id)setBrowserWorkbenchSelectionMode(false);
    target.sessionId='';
    target.renderer='';
    target.bridgeUrl='';
    target.renderError='';
    target.devtoolsOpen=false;
    target.devtoolsUrl='';
    target.nativeCanGoBack=false;
    target.nativeCanGoForward=false;
    target.nativeHistoryPreviousUrl='';
    target.nativeHistoryNextUrl='';
    applyBrowserWorkbenchTabHistoryCapabilities(target);
    const shouldReopen=target.hasStartedLoad===true&&!browserWorkbenchIsBlankUrl(target.requestedUrl||target.url||target.currentUrl);
    target.loadStatus=shouldReopen?'loading':'idle';
    setBrowserWorkbenchContentState(target,shouldReopen?'loading':'idle');
    target.loadError='';
    target.navigationError=null;
    target.state='idle';
    browserWorkbenchClearStatus(target,{all:true});
    target.viewportMessage=target.url
      ?`Reopening ${target.url}…`
      :'Enter an address to open a page.';
    renderBrowserWorkbenchTabs();
    if(target.id===activeBrowserWorkbenchTabId)renderActiveBrowserWorkbenchView();
    persistBrowserWorkbenchTabs();
  }

  function idleBrowserWorkbenchBlankTab(tab){
    const target=tab||getActiveWorkbenchTab();
    if(!target)return {ok:true,status:'idle',load_status:'idle',url:''};
    clearBrowserWorkbenchLoadTimers(target);
    clearBrowserWorkbenchPendingHistoryTraversal(target);
    if(selectionModeTabId===target.id)setBrowserWorkbenchSelectionMode(false);
    target.sessionId='';
    target.url='';
    target.title='';
    target.faviconUrl='';
    target.renderer='';
    target.bridgeUrl='';
    target.renderError='';
    target.devtoolsOpen=false;
    target.devtoolsUrl='';
    target.canGoBack=false;
    target.canGoForward=false;
    target.nativeCanGoBack=false;
    target.nativeCanGoForward=false;
    target.nativeHistoryPreviousUrl='';
    target.nativeHistoryNextUrl='';
    target.historyEntries=[];
    target.historyIndex=-1;
    target.loadStatus='idle';
    setBrowserWorkbenchContentState(target,'idle');
    target.loadError='';
    target.lastHistoryUrl='';
    target.navigationError=null;
    target.clientNavigatedUrl='';
    target.state='idle';
    browserWorkbenchClearStatus(target,{all:true});
    target.viewportMessage='Enter an address to open a page.';
    renderBrowserWorkbenchTabs();
    if(target.id===activeBrowserWorkbenchTabId)renderActiveBrowserWorkbenchView();
    persistBrowserWorkbenchTabs();
    return {ok:true,status:'idle',load_status:'idle',url:'',message:'Ready for an address.'};
  }

  async function fetchCapabilities(){
    const res=await fetch(WORKBENCH_CAPABILITIES_URL,{credentials:'include'});
    let data={};
    try{data=await res.json();}catch(_){data={};}
    if(!res.ok){
      throw new Error(data.error||data.message||`Browser Workbench capability check failed (${res.status})`);
    }
    return data;
  }

  function browserWorkbenchCapabilityPayloadSupportsView(capabilities){
    const data=capabilities&&typeof capabilities==='object'?capabilities:{};
    const caps=data.capabilities&&typeof data.capabilities==='object'?data.capabilities:{};
    if(data.ui_enabled!==true)return false;
    if(caps.session_lifecycle!==true||caps.navigation!==true)return false;
    return data.enabled===true||caps.iframe_bridge===true||caps.interactive_viewport===true;
  }

  function applyCapabilities(capabilities,options){
    const data=capabilities&&typeof capabilities==='object'?capabilities:{};
    const opts=options&&typeof options==='object'?options:{};
    workbenchUiEnabled=data.ui_enabled===true;
    workbenchCapabilities=data.capabilities&&typeof data.capabilities==='object'?data.capabilities:{};
    applyBrowserWorkbenchAvailability(workbenchUiEnabled);
    const active=getActiveWorkbenchTab();
    if(opts.preserveStatus!==true){
      if(!workbenchUiEnabled)setStatus('Browser is disabled.','warning',active,{owner:'availability',kind:'error'});
      else if(!browserWorkbenchCapabilityPayloadSupportsView(data))setStatus('Browser view is unavailable.','warning',active,{owner:'availability',kind:'error'});
      else browserWorkbenchClearStatus(active,{owner:'availability'});
    }
    return data;
  }

  function applySessionState(data,tab){
    wireDom();
    const target=tab||getActiveWorkbenchTab();
    if(!target)return;
    const payload=data&&typeof data==='object'?data:{};
    const hasSession=!!payload.session_id&&payload.status!=='closed';
    if(Object.prototype.hasOwnProperty.call(payload,'navigation_error'))target.navigationError=normalizeBrowserWorkbenchNavigationError(payload.navigation_error);
    target.sessionId=hasSession?String(payload.session_id):'';
    if(hasSession){
      const payloadUrl=String(payload.url||'');
      const clientNavigatedUrl=String(target.clientNavigatedUrl||'');
      target.url=clientNavigatedUrl&&payloadUrl&&payloadUrl!==clientNavigatedUrl?clientNavigatedUrl:payloadUrl;
      target.currentUrl=target.url||target.currentUrl||'';
      if(target.url)target.requestedUrl=target.requestedUrl||target.url;
    }else{
      target.url=target.url||'';
    }
    target.title=hasSession&&payload.title?String(payload.title):'';
    target.faviconUrl=hasSession&&payload.favicon_url?String(payload.favicon_url):'';
    if(hasSession&&payload.zoom!==undefined)target.zoom=Math.max(0.25,Math.min(3,Number.parseFloat(payload.zoom)||1));
    if(hasSession&&target.url)syncBrowserWorkbenchTabHistory(target,target.url);
    applyBrowserWorkbenchTabHistoryCapabilities(
      target,
      hasSession&&payload.can_go_back===true,
      hasSession&&payload.can_go_forward===true
    );
    const previousBridgeUrl=target.bridgeUrl||'';
    target.renderer=hasSession&&payload.renderer?String(payload.renderer):'';
    target.bridgeUrl=hasSession&&payload.bridge_url?String(payload.bridge_url):'';
    if(hasSession&&target.renderer==='iframe-bridge'&&previousBridgeUrl&&target.bridgeUrl&&previousBridgeUrl!==target.bridgeUrl){
      target.iframeBridgeReady=false;
      target.iframeCaptureReady=false;
      resetBrowserWorkbenchDevtoolsLite(target,'navigation');
    }
    target.renderError=hasSession&&payload.render_error?String(payload.render_error):'';
    if(hasSession&&payload.devtools_url!==undefined)target.devtoolsUrl=String(payload.devtools_url||'');
    if(hasSession&&payload.chii_devtools&&typeof payload.chii_devtools==='object')target.chiiDevtools=payload.chii_devtools;
    if(!hasSession)target.devtoolsOpen=false;
    if(hasSession){
      const historyUrl=browserWorkbenchSafeHistoryUrl(target.url);
      if(historyUrl){
        recordBrowserWorkbenchHistory(historyUrl,target.title,{countVisit:target.lastHistoryUrl!==historyUrl});
        target.lastHistoryUrl=historyUrl;
      }
      const shownUrl=target.url||'about:blank';
      target.viewportMessage=target.renderError?`Couldn’t open ${shownUrl}.`:`Browser view is unavailable for ${shownUrl}.`;
      if(target.renderError)setStatus('Browser view could not be displayed.','warning',target,{owner:'renderer',kind:'error'});
      else browserWorkbenchClearStatus(target,{owner:'renderer'});
      if(payload.load_status!==undefined){
        setBrowserWorkbenchLoadStatus(payload.load_status,target,{message:payload.load_error||payload.render_error||payload.message,url:target.url});
      }else if(target.loadStatus==='loading'){
        if(target.renderError)setBrowserWorkbenchLoadStatus('error',target,{message:target.renderError});
        else if(target.renderer!=='iframe-bridge'&&target.renderer!=='chromium-stream')setBrowserWorkbenchLoadStatus('success',target);
      }else{
        setBrowserWorkbenchContentState(target,target.navigationError||target.renderError?'error':'loaded');
        setTabState(target.renderError?'warning':'ready',target);
      }
    }else{
      if(selectionModeTabId===target.id)setBrowserWorkbenchSelectionMode(false);
      clearBrowserWorkbenchLoadTimers(target);
      if(target.loadStatus!=='error')target.loadStatus='idle';
      setBrowserWorkbenchContentState(target,target.loadStatus==='error'?'error':'idle');
      if(target.loadStatus!=='error')target.loadError='';
      target.nativeCanGoBack=false;
      target.nativeCanGoForward=false;
      target.nativeHistoryPreviousUrl='';
      target.nativeHistoryNextUrl='';
      clearBrowserWorkbenchPendingHistoryTraversal(target);
      applyBrowserWorkbenchTabHistoryCapabilities(target);
      target.renderError='';
      target.renderer='';
      target.bridgeUrl='';
      target.title='';
      target.faviconUrl='';
      target.lastHistoryUrl='';
      target.clientNavigatedUrl='';
      target.navigationError=null;
      target.iframeBridgeReady=false;
      target.iframeCaptureReady=false;
      target.viewportMessage='Enter an address to open a page.';
      if(workbenchUiEnabled)setTabState('idle',target);
    }
    renderBrowserWorkbenchTabs();
    if(target.id===activeBrowserWorkbenchTabId)renderActiveBrowserWorkbenchView();
    persistBrowserWorkbenchTabs();
  }

  async function refreshBrowserWorkbenchCapabilities(){
    const active=getActiveWorkbenchTab();
    const token=setStatus('Checking browser availability…','muted',active,{owner:'availability',kind:'progress',resetTransient:true});
    try{
      const data=applyCapabilities(await fetchCapabilities(),{preserveStatus:true});
      if(browserWorkbenchCapabilityPayloadSupportsView(data))browserWorkbenchResolveStatus(token,'');
      else if(data&&data.ui_enabled!==true)browserWorkbenchResolveStatus(token,'Browser is disabled.',{kind:'error',tone:'warning'});
      else browserWorkbenchResolveStatus(token,'Browser view is unavailable.',{kind:'error',tone:'warning'});
      return data;
    }catch(err){
      workbenchUiEnabled=false;
      workbenchCapabilities={};
      applyBrowserWorkbenchAvailability(false);
      browserWorkbenchResolveStatus(token,'Browser availability check failed.',{kind:'error',tone:'warning'});
      return null;
    }
  }

  function requestedUrlForTab(tab){
    if(tab&&tab.id===activeBrowserWorkbenchTabId&&urlInput&&isBrowserWorkbenchUrlInputEditing(tab)&&urlInput.value)return urlInput.value.trim();
    return browserWorkbenchActivationUrl(tab);
  }

  async function startBrowserWorkbenchSession(tabId,options){
    wireDom();
    const opts=options&&typeof options==='object'?options:{};
    const target=tabById(tabId)||getActiveWorkbenchTab()||createBrowserWorkbenchTabRecord();
    const expectedRequestId=Number(opts.navigationRequestId)||0;
    activateBrowserWorkbenchTab(target.id,{switchPanel:false});
    if(expectedRequestId&&expectedRequestId!==target.navigationRequestId)return null;
    if(!workbenchUiEnabled){
      await refreshBrowserWorkbenchCapabilities();
    }
    if(!workbenchUiEnabled){
      const message='Browser is disabled.';
      setStatus(message,'warning',target,{owner:'availability',kind:'error'});
      renderActiveBrowserWorkbenchView();
      return {ok:false,status:'disabled',error:message,message};
    }
    if(target.sessionId)return refreshBrowserWorkbenchSession(target.id);
    const requestedUrl=requestedUrlForTab(target);
    const startsBlank=browserWorkbenchIsBlankUrl(requestedUrl);
    if(startsBlank){
      return idleBrowserWorkbenchBlankTab(target);
    }else{
      target.url=requestedUrl;
      markBrowserWorkbenchLoadStarted(target,requestedUrl);
      setTabState('loading',target);
      setBrowserWorkbenchLoadStatus('loading',target,{autoReset:false,url:requestedUrl});
      target.navigationStatusToken=setStatus('Opening page…','muted',target,{owner:'navigation',kind:'progress',resetTransient:true});
    }
    try{
      const data=await requestJSON(WORKBENCH_SESSION_URL,{
        method:'POST',
        body:browserWorkbenchRequestBody({url:requestedUrl,zoom:target.zoom||1})
      });
      if(expectedRequestId&&expectedRequestId!==target.navigationRequestId){
        if(data&&data.session_id)requestJSON(sessionStatusUrl(data.session_id),{method:'DELETE'}).catch(()=>{});
        return null;
      }
      applySessionState(data,target);
      return data;
    }catch(err){
      if(expectedRequestId&&expectedRequestId!==target.navigationRequestId)return null;
      const data=err&&err.data?err.data:null;
      const message='Couldn’t open the page.';
      target.sessionId='';
      target.renderer='';
      target.bridgeUrl='';
      target.renderError=message;
      target.viewportMessage=message;
      setBrowserWorkbenchLoadStatus('error',target,{message,url:requestedUrl});
      renderActiveBrowserWorkbenchView();
      persistBrowserWorkbenchTabs();
      return data||{ok:false,error:message};
    }
  }

  async function refreshBrowserWorkbenchSession(tabId,options){
    wireDom();
    const opts=options&&typeof options==='object'?options:{};
    const target=tabById(tabId)||getActiveWorkbenchTab();
    if(!target||!target.sessionId)return null;
    try{
      const data=await requestJSON(sessionStatusUrl(target.sessionId),{method:'GET'});
      applySessionState(data,target);
      return data;
    }catch(err){
      if(isBrowserWorkbenchSessionMissingError(err)){
        clearBrowserWorkbenchStaleSession(target);
        if(opts.recreate!==false)return startBrowserWorkbenchSession(target.id);
        return null;
      }
      setStatus('Browser connection was lost.','warning',target,{owner:'availability',kind:'error'});
      target.sessionId='';
      applySessionState({status:'closed'},target);
      return null;
    }
  }

  async function navigateBrowserWorkbenchToUrl(tabId,url,options){
    wireDom();
    const target=tabById(tabId)||getActiveWorkbenchTab();
    if(!target){
      return null;
    }
    const opts=options&&typeof options==='object'?options:{};
    const requested=String(url!==undefined?url:(urlInput&&target.id===activeBrowserWorkbenchTabId?urlInput.value:target.url)||'').trim();
    closeBrowserWorkbenchUrlSuggestions();
    if(browserWorkbenchIsBlankUrl(requested)){
      target.navigationRequestId=(target.navigationRequestId||0)+1;
      target.navigationError=null;
      const closingId=target.sessionId;
      const idle=idleBrowserWorkbenchBlankTab(target);
      if(closingId){
        requestJSON(sessionStatusUrl(closingId),{method:'DELETE'}).catch(()=>{});
      }
      return idle;
    }
    const traversal=opts.historyTraversal&&typeof opts.historyTraversal==='object'?opts.historyTraversal:null;
    if(traversal)setBrowserWorkbenchPendingHistoryTraversal(target,traversal.index,traversal.url||requested);
    const requestId=beginBrowserWorkbenchNavigation(target,requested,{message:'Opening page…',preserveHistoryTraversal:!!traversal});
    if(!target.sessionId){
      target.url=requested;
      renderActiveBrowserWorkbenchView();
      return startBrowserWorkbenchSession(target.id,{navigationRequestId:requestId});
    }
    if(requestId!==target.navigationRequestId)return null;
    try{
      const data=await requestJSON(`${sessionStatusUrl(target.sessionId)}/navigate`,{
        method:'POST',
        body:browserWorkbenchRequestBody({url:requested,zoom:target.zoom||1})
      });
      if(requestId!==target.navigationRequestId)return null;
      applySessionState(data,target);
      return data;
    }catch(err){
      if(requestId!==target.navigationRequestId)return null;
      if(isBrowserWorkbenchSessionMissingError(err)){
        const pending=target.pendingHistoryTraversal?{...target.pendingHistoryTraversal}:null;
        clearBrowserWorkbenchStaleSession(target);
        if(pending)setBrowserWorkbenchPendingHistoryTraversal(target,pending.index,pending.url);
        target.url=requested;
        renderActiveBrowserWorkbenchView();
        return startBrowserWorkbenchSession(target.id,{navigationRequestId:requestId});
      }
      const data=err&&err.data?err.data:null;
      const message='Couldn’t open the page.';
      clearBrowserWorkbenchPendingHistoryTraversal(target);
      setBrowserWorkbenchLoadStatus('error',target,{message,url:requested});
      renderActiveBrowserWorkbenchView();
      return data||{ok:false,error:message};
    }
  }

  async function navigateBrowserWorkbenchHistory(action,tabId){
    wireDom();
    const target=tabById(tabId)||getActiveWorkbenchTab();
    const normalized=String(action||'').toLowerCase();
    if(!target){
      return null;
    }
    if(['reload','back','forward'].indexOf(normalized)===-1)return null;
    const retryUrl=browserWorkbenchRetryUrl(target);
    const historyTarget=normalized==='back'||normalized==='forward'
      ?browserWorkbenchTabHistoryTarget(target,normalized)
      :null;
    if(normalized==='reload'&&!target.sessionId){
      if(!browserWorkbenchCanReload(target)){
        setStatus('Enter a valid URL before reloading.','warning',target,{owner:'navigation',kind:'error'});
        return null;
      }
      return navigateBrowserWorkbenchToUrl(target.id,retryUrl);
    }
    if(!target.sessionId){
      if(historyTarget)return navigateBrowserWorkbenchToUrl(target.id,historyTarget.url,{historyTraversal:historyTarget});
      setStatus('Open a page before using back or forward.','warning',target,{owner:'navigation',kind:'error'});
      return null;
    }
    if(normalized==='reload'&&target.loadStatus==='loading')return stopBrowserWorkbenchLoading(target.id);
    const nativeCapability=normalized==='back'?target.nativeCanGoBack===true:normalized==='forward'?target.nativeCanGoForward===true:true;
    const useNativeHistory=historyTarget
      ?browserWorkbenchCanUseNativeHistoryTarget(target,normalized,historyTarget)
      :nativeCapability;
    if(historyTarget&&!useNativeHistory){
      return navigateBrowserWorkbenchToUrl(target.id,historyTarget.url,{historyTraversal:historyTarget});
    }
    const label=normalized==='reload'?'Reloading page…':normalized==='back'?'Going back…':'Going forward…';
    if(historyTarget)setBrowserWorkbenchPendingHistoryTraversal(target,historyTarget.index,historyTarget.url);
    const requestId=beginBrowserWorkbenchNavigation(target,retryUrl,{message:label,preserveHistoryTraversal:!!historyTarget});
    if(navigateBrowserWorkbenchIframeHistory(target,normalized)){
      persistBrowserWorkbenchTabs();
      return {ok:true,status:'loading',url:target.currentUrl||target.url||retryUrl};
    }
    if(historyTarget&&target.renderer==='iframe-bridge'){
      return navigateBrowserWorkbenchToUrl(target.id,historyTarget.url,{historyTraversal:historyTarget});
    }
    try{
      const data=await requestJSON(`${sessionStatusUrl(target.sessionId)}/${normalized}`,{
        method:'POST',
        body:browserWorkbenchRequestBody({zoom:target.zoom||1})
      });
      if(requestId!==target.navigationRequestId)return null;
      applySessionState(data,target);
      return data;
    }catch(err){
      if(requestId!==target.navigationRequestId)return null;
      if(isBrowserWorkbenchSessionMissingError(err)){
        const pending=target.pendingHistoryTraversal?{...target.pendingHistoryTraversal}:null;
        clearBrowserWorkbenchStaleSession(target);
        if(pending)setBrowserWorkbenchPendingHistoryTraversal(target,pending.index,pending.url);
        if(normalized==='reload'&&target.url)return startBrowserWorkbenchSession(target.id,{navigationRequestId:requestId});
        if(pending)return navigateBrowserWorkbenchToUrl(target.id,pending.url,{historyTraversal:pending});
        return null;
      }
      const data=err&&err.data?err.data:null;
      const message='Navigation failed.';
      clearBrowserWorkbenchPendingHistoryTraversal(target);
      setBrowserWorkbenchLoadStatus('error',target,{message,url:target.url||target.currentUrl});
      renderActiveBrowserWorkbenchView();
      return data||{ok:false,error:message};
    }
  }

  async function maybeStartBrowserWorkbenchInitialLoadOnActivation(tabId){
    wireDom();
    let target=tabById(tabId)||getActiveWorkbenchTab();
    if(!target)return null;
    if(!shouldStartBrowserWorkbenchInitialLoadOnActivation(target))return null;
    if(target.openingPromise)return target.openingPromise;
    target.openingPromise=(async()=>{
      if(!workbenchUiEnabled)await refreshBrowserWorkbenchCapabilities();
      if(!workbenchUiEnabled){
        setStatus('Browser is disabled.','warning',target,{owner:'availability',kind:'error'});
        return null;
      }
      if(!shouldStartBrowserWorkbenchInitialLoadOnActivation(target))return null;
      return startBrowserWorkbenchSession(target.id);
    })();
    try{
      return await target.openingPromise;
    }finally{
      target.openingPromise=null;
    }
  }

  async function ensureBrowserWorkbenchSessionOnOpen(tabId){
    wireDom();
    let target=tabById(tabId)||getActiveWorkbenchTab();
    if(!target)target=createBrowserWorkbenchTabRecord();
    activateBrowserWorkbenchTab(target.id,{switchPanel:false});
    return maybeStartBrowserWorkbenchInitialLoadOnActivation(target.id);
  }

  function createBrowserWorkbenchTabRecord(options){
    wireDom();
    const opts=options&&typeof options==='object'?options:{};
    const requestedNumber=Number.parseInt(opts.number,10);
    const number=Number.isFinite(requestedNumber)&&requestedNumber>0?requestedNumber:nextBrowserWorkbenchTabNumber++;
    if(number>=nextBrowserWorkbenchTabNumber)nextBrowserWorkbenchTabNumber=number+1;
    const currentUrl=String(opts.currentUrl||opts.current_url||opts.url||'');
    const history=normalizeBrowserWorkbenchTabHistory(
      opts.historyEntries||opts.history_entries,
      opts.historyIndex!==undefined?opts.historyIndex:opts.history_index,
      currentUrl
    );
    const tab={
      id:String(opts.id||BROWSER_WORKBENCH_TAB_ID_PREFIX+number),
      number,
      label:'Browser',
      sessionId:'',
      url:String(opts.url||''),
      title:String(opts.title||''),
      faviconUrl:String(opts.faviconUrl||opts.favicon_url||''),
      zoom:Math.max(0.25,Math.min(3,Number.parseFloat(opts.zoom)||1)),
      canGoBack:history.index>0,
      canGoForward:history.index>=0&&history.index<history.entries.length-1,
      nativeCanGoBack:false,
      nativeCanGoForward:false,
      nativeHistoryPreviousUrl:'',
      nativeHistoryNextUrl:'',
      historyEntries:history.entries,
      historyIndex:history.index,
      pendingHistoryTraversal:null,
      historyTraversalTimer:null,
      renderer:'',
      bridgeUrl:'',
      renderError:'',
      devtoolsUrl:String(opts.devtools_url||opts.devtoolsUrl||''),
      devtoolsOpen:opts.devtools_open===true||opts.devtoolsOpen===true,
      devtoolsLite:null,
      chiiDevtools:null,
      iframeBridgeReady:false,
      iframeCaptureReady:false,
      devtoolsWidth:Math.max(BROWSER_WORKBENCH_MIN_DEVTOOLS_WIDTH,Math.min(900,Number.parseInt(opts.devtoolsWidth||opts.devtools_width,10)||BROWSER_WORKBENCH_DEFAULT_DEVTOOLS_WIDTH)),
      message:'',
      tone:'muted',
      statusEntries:new Map(),
      state:'idle',
      loadStatus:normalizeBrowserWorkbenchLoadStatus(opts.loadStatus||opts.load_status),
      contentState:normalizeBrowserWorkbenchContentState(opts.contentState||opts.content_state),
      loadError:String(opts.loadError||opts.load_error||''),
      navigationError:normalizeBrowserWorkbenchNavigationError(opts.navigationError||opts.navigation_error),
      navigationRequestId:0,
      currentUrl,
      requestedUrl:String(opts.requestedUrl||opts.requested_url||''),
      lastLoadedUrl:String(opts.lastLoadedUrl||opts.last_loaded_url||''),
      hasStartedLoad:opts.hasStartedLoad===true||opts.has_started_load===true,
      hasCommittedNavigation:opts.hasCommittedNavigation===true||opts.has_committed_navigation===true,
      lastError:String(opts.lastError||opts.last_error||''),
      loadStatusTimer:null,
      lastHistoryUrl:'',
      clientNavigatedUrl:'',
      surfaceNode:null,
      surfaceUrl:'',
      viewportMessage:'Enter an address to open a page.',
      openingPromise:null,
      tabEl:null,
      statusEl:null,
    };
    if(!opts.contentState&&!opts.content_state)tab.contentState=nextBrowserWorkbenchContentState(tab,tab.loadStatus);
    tab.state=browserWorkbenchStatusState(tab.loadStatus,tab);
    workbenchTabs.set(tab.id,tab);
    renderBrowserWorkbenchTabs();
    persistBrowserWorkbenchTabs();
    return tab;
  }

  function reorderBrowserWorkbenchTab(dragId,targetId,placeAfter){
    const dragged=tabById(dragId);
    const target=tabById(targetId);
    if(!dragged||!target||dragged.id===target.id)return false;
    const ordered=Array.from(workbenchTabs.values()).filter((entry)=>entry.id!==dragged.id);
    const targetIndex=ordered.findIndex((entry)=>entry.id===target.id);
    if(targetIndex<0)return false;
    ordered.splice(targetIndex+(placeAfter?1:0),0,dragged);
    workbenchTabs.clear();
    ordered.forEach((entry)=>workbenchTabs.set(entry.id,entry));
    renderBrowserWorkbenchTabs();
    persistBrowserWorkbenchTabs();
    return true;
  }

  function clearBrowserWorkbenchTabDragState(){
    draggedBrowserWorkbenchTabId='';
    workbenchTabs.forEach((entry)=>{
      if(entry.tabEl)entry.tabEl.classList.remove('dragging','drop-before','drop-after');
    });
  }

  function handleBrowserWorkbenchTabDragStart(event,tab){
    draggedBrowserWorkbenchTabId=tab.id;
    if(event.dataTransfer){
      event.dataTransfer.effectAllowed='move';
      event.dataTransfer.setData('text/plain',tab.id);
    }
    if(tab.tabEl)tab.tabEl.classList.add('dragging');
  }

  function handleBrowserWorkbenchTabDragOver(event,tab){
    if(!draggedBrowserWorkbenchTabId||draggedBrowserWorkbenchTabId===tab.id)return;
    event.preventDefault();
    if(event.dataTransfer)event.dataTransfer.dropEffect='move';
    const rect=tab.tabEl&&tab.tabEl.getBoundingClientRect?tab.tabEl.getBoundingClientRect():null;
    const after=!!rect&&event.clientX>rect.left+rect.width/2;
    if(tab.tabEl){
      tab.tabEl.classList.toggle('drop-after',after);
      tab.tabEl.classList.toggle('drop-before',!after);
    }
  }

  function handleBrowserWorkbenchTabDrop(event,tab){
    if(!draggedBrowserWorkbenchTabId||draggedBrowserWorkbenchTabId===tab.id)return;
    event.preventDefault();
    const rect=tab.tabEl&&tab.tabEl.getBoundingClientRect?tab.tabEl.getBoundingClientRect():null;
    const placeAfter=!!rect&&event.clientX>rect.left+rect.width/2;
    reorderBrowserWorkbenchTab(draggedBrowserWorkbenchTabId,tab.id,placeAfter);
    clearBrowserWorkbenchTabDragState();
  }

  function createBrowserWorkbenchTabElement(tab){
    const el=document.createElement('div');
    el.className='workbench-tab workbench-tab-browser';
    el.id=tab.id;
    el.setAttribute('role','tab');
    el.setAttribute('tabindex','0');
    el.setAttribute('aria-controls','mainBrowser');
    el.draggable=true;
    el.dataset.browserWorkbenchLauncher='';
    el.setAttribute('data-browser-workbench-tab-id',tab.id);
    el.appendChild(browserWorkbenchTabIconNode(tab));
    el.appendChild(textEl('span','workbench-tab-label',browserWorkbenchDisplayLabel(tab)));
    const status=textEl('span','workbench-tab-status','');
    status.setAttribute('aria-hidden','true');
    status.dataset.state=tab.state||'idle';
    el.appendChild(status);
    const close=textEl('button','workbench-tab-close','×');
    close.type='button';
    close.setAttribute('aria-label',`Close ${browserWorkbenchDisplayLabel(tab)} session`);
    close.title=`Close ${browserWorkbenchDisplayLabel(tab)} tab`;
    close.addEventListener('click',(event)=>{
      event.stopPropagation();
      closeBrowserWorkbenchTab(tab.id);
    });
    el.appendChild(close);
    el.addEventListener('click',()=>openBrowserWorkbenchTab(tab.id));
    el.addEventListener('dragstart',(event)=>handleBrowserWorkbenchTabDragStart(event,tab));
    el.addEventListener('dragover',(event)=>handleBrowserWorkbenchTabDragOver(event,tab));
    el.addEventListener('dragleave',()=>el.classList.remove('drop-before','drop-after'));
    el.addEventListener('drop',(event)=>handleBrowserWorkbenchTabDrop(event,tab));
    el.addEventListener('dragend',clearBrowserWorkbenchTabDragState);
    el.addEventListener('keydown',(event)=>{
      if(event.key==='Enter'||event.key===' '){
        event.preventDefault();
        openBrowserWorkbenchTab(tab.id);
      }
    });
    tab.tabEl=el;
    tab.statusEl=status;
    return el;
  }

  function renderBrowserWorkbenchTabs(){
    wireDom();
    if(!tabsEl)return;
    workbenchTabs.forEach((tab)=>{
      if(!tab.tabEl)createBrowserWorkbenchTabElement(tab);
      tabsEl.appendChild(tab.tabEl);
      const displayLabel=browserWorkbenchDisplayLabel(tab);
      const label=tab.tabEl.querySelector('.workbench-tab-label');
      if(label)label.textContent=displayLabel;
      tab.tabEl.title=displayLabel;
      updateBrowserWorkbenchTabIcon(tab);
      const close=tab.tabEl.querySelector('.workbench-tab-close');
      if(close){
        close.setAttribute('aria-label',`Close ${displayLabel} session`);
        close.title=`Close ${displayLabel} tab`;
      }
      tab.tabEl.classList.toggle('active',tab.id===activeBrowserWorkbenchTabId);
      tab.tabEl.setAttribute('aria-selected',tab.id===activeBrowserWorkbenchTabId?'true':'false');
      tab.tabEl.hidden=false;
      if(tab.statusEl)tab.statusEl.dataset.state=browserWorkbenchStatusState(tab.loadStatus,tab);
    });
    if(openerButton)openerButton.hidden=false;
  }

  function renderActiveBrowserWorkbenchView(){
    wireDom();
    const active=getActiveWorkbenchTab();
    if(titleEl)titleEl.textContent=active?browserWorkbenchDisplayLabel(active):'Browser';
    if(urlInput){
      if(!isBrowserWorkbenchUrlInputEditing(active))urlInput.value=active?active.url||'':'';
      urlInput.disabled=!active||!workbenchUiEnabled;
    }
    if(backButton)backButton.disabled=!browserWorkbenchHistoryControlEnabled(active,'back');
    if(forwardButton)forwardButton.disabled=!browserWorkbenchHistoryControlEnabled(active,'forward');
    updateBrowserWorkbenchReloadButton();
    updateBrowserWorkbenchActionMenuCapabilities();
    if(menuButton)menuButton.disabled=!active||!active.sessionId;
    const zoomEnabled=!!active;
    if(menuZoomInput)menuZoomInput.disabled=!zoomEnabled;
    updateBrowserWorkbenchZoomLabel();
    if(pingButton){
      pingButton.disabled=!active||!active.sessionId;
      pingButton.classList.toggle('active',selectionMode&&active&&selectionModeTabId===active.id);
      pingButton.setAttribute('aria-pressed',selectionMode&&active&&selectionModeTabId===active.id?'true':'false');
      pingButton.textContent=selectionMode&&active&&selectionModeTabId===active.id?'Selecting…':'Ping selection';
    }
    const contentState=normalizeBrowserWorkbenchContentState(active&&active.contentState);
    if(viewportEl){
      viewportEl.dataset.browserContentState=contentState;
      viewportEl.classList.toggle('selecting',selectionMode&&active&&selectionModeTabId===active.id);
      viewportEl.classList.toggle('has-rendered-browser',!!active&&contentState!=='restored'&&contentState!=='error'&&(active.renderer==='iframe-bridge'||active.renderer==='chromium-stream'));
      viewportEl.classList.toggle('has-navigation-error',!!active&&contentState==='error');
      viewportEl.classList.toggle('has-iframe-bridge',!!active&&active.renderer==='iframe-bridge');
      viewportEl.classList.toggle('has-chromium-stream',!!active&&active.renderer==='chromium-stream');
      viewportEl.classList.toggle('area-capturing',areaCaptureMode);
      viewportEl.classList.toggle('has-devtools',!!active&&active.devtoolsOpen===true&&!!active.devtoolsUrl);
    }
    if(active&&contentState==='error'&&active.navigationError)renderBrowserWorkbenchNavigationError(active);
    else if(active&&(contentState==='restored'||contentState==='error')){
      stopBrowserWorkbenchChromiumStream();
      setViewportMessage(browserWorkbenchContentPlaceholder(active));
    }
    else if(active&&active.renderer==='iframe-bridge'&&active.bridgeUrl&&active.devtoolsOpen===true&&active.devtoolsUrl){
      if(!syncBrowserWorkbenchIframeDevtoolsLite(active))renderBrowserWorkbenchSplitView(active);
    }else if(active&&active.devtoolsOpen===true&&active.devtoolsUrl)renderBrowserWorkbenchSplitView(active);
    else if(active&&active.renderer==='chromium-stream'&&active.sessionId)renderBrowserWorkbenchChromiumStream(active);
    else if(active&&active.renderer==='iframe-bridge'&&active.bridgeUrl)renderBrowserWorkbenchFrame(active);
    else{
      stopBrowserWorkbenchChromiumStream();
      setViewportMessage(browserWorkbenchContentPlaceholder(active));
    }
    browserWorkbenchRenderManagedStatus(active);
    renderBrowserWorkbenchTabs();
  }

  function activateBrowserWorkbenchTab(tabId,opts){
    const options=opts||{};
    const target=tabById(tabId);
    if(!target)return null;
    const previous=getActiveWorkbenchTab();
    if(previous&&previous.id!==target.id){
      if(areaCaptureMode)cancelBrowserWorkbenchAreaCapture(previous);
      browserWorkbenchClearStatus(previous,{kinds:['progress']});
      browserWorkbenchClearStatus(previous,{owner:'area-capture'});
    }
    activeBrowserWorkbenchTabId=target.id;
    if(selectionMode&&selectionModeTabId!==target.id)setBrowserWorkbenchSelectionMode(false);
    renderActiveBrowserWorkbenchView();
    persistBrowserWorkbenchTabs();
    if(options.switchPanel!==false&&typeof switchPanel==='function')switchPanel('browser');
    return target;
  }

  async function openBrowserWorkbenchTab(tabId){
    wireDom();
    const target=tabById(tabId)||createBrowserWorkbenchTabRecord();
    activateBrowserWorkbenchTab(target.id,{switchPanel:false});
    if(typeof switchPanel==='function')await switchPanel('browser');
    await maybeStartBrowserWorkbenchInitialLoadOnActivation(target.id);
    renderActiveBrowserWorkbenchView();
    return target;
  }

  async function closeBrowserWorkbenchTab(tabId){
    wireDom();
    const target=tabById(tabId)||getActiveWorkbenchTab();
    if(!target)return {ok:true,status:'closed'};
    const closingId=target.sessionId;
    if(closingId){
      setTabState('loading',target);
      const statusToken=setStatus('Closing tab…','muted',target,{owner:'teardown',kind:'progress',resetTransient:true});
      try{
        await requestJSON(sessionStatusUrl(closingId),{method:'DELETE'});
      }catch(err){
        if(isBrowserWorkbenchSessionMissingError(err)){
          browserWorkbenchClearStatus(target,{owner:'teardown'});
        }else{
          const message='Couldn’t close the tab.';
          browserWorkbenchResolveStatus(statusToken,message,{kind:'error',tone:'warning'});
          return {ok:false,error:message};
        }
      }
    }
    if(selectionModeTabId===target.id)setBrowserWorkbenchSelectionMode(false);
    if(areaCaptureMode)cancelBrowserWorkbenchAreaCapture(target);
    clearBrowserWorkbenchLoadTimers(target);
    clearBrowserWorkbenchPendingHistoryTraversal(target);
    const ids=Array.from(workbenchTabs.keys());
    const closedIndex=ids.indexOf(target.id);
    removeBrowserWorkbenchStoredSurface(target);
    browserWorkbenchClearStatus(target,{all:true});
    if(target.tabEl&&target.tabEl.parentNode)target.tabEl.parentNode.removeChild(target.tabEl);
    workbenchTabs.delete(target.id);
    if(activeBrowserWorkbenchTabId===target.id){
      const remaining=Array.from(workbenchTabs.keys());
      activeBrowserWorkbenchTabId=remaining[closedIndex]||remaining[closedIndex-1]||remaining[0]||'';
    }
    if(activeBrowserWorkbenchTabId){
      activateBrowserWorkbenchTab(activeBrowserWorkbenchTabId,{switchPanel:false});
      if(typeof switchPanel==='function')await switchPanel('browser');
    }else{
      renderActiveBrowserWorkbenchView();
      if(typeof switchPanel==='function')await switchPanel('chat');
    }
    persistBrowserWorkbenchTabs();
    return {ok:true,status:'closed'};
  }

  function syncBrowserWorkbenchTabActive(activePanel){
    wireDom();
    const isBrowser=activePanel==='browser';
    const chatTab=document.getElementById('workbenchTabChat');
    if(chatTab){
      chatTab.classList.toggle('active',!isBrowser);
      chatTab.setAttribute('aria-selected',isBrowser?'false':'true');
    }
    if(isBrowser&&!getActiveWorkbenchTab()&&workbenchTabs.size>0){
      activeBrowserWorkbenchTabId=Array.from(workbenchTabs.keys())[0];
    }
    renderBrowserWorkbenchTabs();
    if(isBrowser)renderActiveBrowserWorkbenchView();
    else{
      const active=getActiveWorkbenchTab();
      if(selectionMode)setBrowserWorkbenchSelectionMode(false);
      if(areaCaptureMode)cancelBrowserWorkbenchAreaCapture(active);
      browserWorkbenchClearStatus(active,{kinds:['progress']});
    }
    persistBrowserWorkbenchTabs();
  }

  function browserWorkbenchPointFromEvent(event){
    wireDom();
    if(!viewportEl||!event)return null;
    const viewport=currentBrowserWorkbenchViewport();
    const surface=viewportEl.querySelector('.browser-workbench-surface-region');
    const rect=(surface||viewportEl).getBoundingClientRect();
    if(!rect||rect.width<=0||rect.height<=0)return null;
    const x=Math.max(0,Math.min(viewport.width,(event.clientX-rect.left)*viewport.width/rect.width));
    const y=Math.max(0,Math.min(viewport.height,(event.clientY-rect.top)*viewport.height/rect.height));
    return {x,y,viewport,displayRect:rect};
  }

  function handleBrowserWorkbenchAreaPointerDown(event){
    if(!areaCaptureMode)return;
    const point=browserWorkbenchPointFromEvent(event);
    if(!point)return;
    event.preventDefault();
    event.stopPropagation();
    suppressNextViewportClick=true;
    areaCaptureStart={x:point.x,y:point.y};
    updateBrowserWorkbenchAreaBox(point);
    try{if(viewportEl&&viewportEl.setPointerCapture)viewportEl.setPointerCapture(event.pointerId);}catch(_){}
  }

  function handleBrowserWorkbenchAreaPointerMove(event){
    if(!areaCaptureMode||!areaCaptureStart)return;
    const point=browserWorkbenchPointFromEvent(event);
    if(!point)return;
    event.preventDefault();
    updateBrowserWorkbenchAreaBox(point);
  }

  function handleBrowserWorkbenchAreaPointerUp(event){
    if(!areaCaptureMode||!areaCaptureStart)return;
    const point=browserWorkbenchPointFromEvent(event);
    event.preventDefault();
    event.stopPropagation();
    try{if(viewportEl&&viewportEl.releasePointerCapture)viewportEl.releasePointerCapture(event.pointerId);}catch(_){}
    const clip=updateBrowserWorkbenchAreaBox(point);
    cancelBrowserWorkbenchAreaCapture();
    suppressNextViewportClick=true;
    if(!clip||clip.width<4||clip.height<4){
      setStatus('Select a larger area and try again.','ready',getActiveWorkbenchTab(),{owner:'capture',kind:'temporary'});
      return;
    }
    void attachBrowserWorkbenchScreenshot(clip).catch((err)=>{
      const active=getActiveWorkbenchTab();
      const message=(err&&err.data&&(err.data.error||err.data.message))||(err&&err.message)||'Area screenshot capture failed.';
      setStatus('Area screenshot failed.','warning',active,{owner:'capture',kind:'error'});
    });
  }

  function canInteractWithBrowserWorkbenchViewport(action){
    const active=getActiveWorkbenchTab();
    const selecting=selectionMode&&active&&selectionModeTabId===active.id;
    return !!active&&!!active.sessionId&&workbenchCapabilities.interactive_viewport===true&&active.renderer==='chromium-stream'&&!areaCaptureMode&&(!selecting||action==='wheel');
  }

  async function sendBrowserWorkbenchInteraction(action,extra){
    const active=getActiveWorkbenchTab();
    if(!active||!active.sessionId)return null;
    const requestId=++interactionRequestId;
    browserWorkbenchClearStatus(active,{owner:'interaction'});
    setTabState('loading',active);
    try{
      const data=await requestJSON(`${sessionStatusUrl(active.sessionId)}/interact`,{
        method:'POST',
        body:browserWorkbenchRequestBody({action,zoom:active.zoom||1,...(extra||{})})
      });
      if(requestId===interactionRequestId)applySessionState(data,active);
      return data;
    }catch(err){
      const data=err&&err.data?err.data:null;
      const message='Page interaction failed.';
      if(requestId===interactionRequestId){
        setStatus(message,'warning',active,{owner:'interaction',kind:'error'});
        setTabState('warning',active);
      }
      return data||{ok:false,error:message};
    }
  }

  function handleBrowserWorkbenchViewportClick(event){
    if(suppressNextViewportClick){
      suppressNextViewportClick=false;
      if(event){event.preventDefault();event.stopPropagation();}
      return;
    }
    if(!canInteractWithBrowserWorkbenchViewport('click'))return;
    const point=browserWorkbenchPointFromEvent(event);
    if(!point)return;
    event.preventDefault();
    if(viewportEl)viewportEl.focus({preventScroll:true});
    if(pendingClickTimer)clearTimeout(pendingClickTimer);
    pendingClickTimer=setTimeout(()=>{
      pendingClickTimer=null;
      void sendBrowserWorkbenchInteraction('click',{x:point.x,y:point.y});
    },BROWSER_WORKBENCH_CLICK_DELAY_MS);
  }

  function handleBrowserWorkbenchViewportDoubleClick(event){
    if(!canInteractWithBrowserWorkbenchViewport('double_click'))return;
    const point=browserWorkbenchPointFromEvent(event);
    if(!point)return;
    event.preventDefault();
    if(viewportEl)viewportEl.focus({preventScroll:true});
    if(pendingClickTimer){clearTimeout(pendingClickTimer);pendingClickTimer=null;}
    void sendBrowserWorkbenchInteraction('double_click',{x:point.x,y:point.y});
  }

  function handleBrowserWorkbenchViewportWheel(event){
    if(!canInteractWithBrowserWorkbenchViewport('wheel'))return;
    const point=browserWorkbenchPointFromEvent(event);
    if(!point)return;
    event.preventDefault();
    if(selectionMode&&selectionModeTabId===activeBrowserWorkbenchTabId){
      cancelBrowserWorkbenchHoverInspect();
      clearBrowserWorkbenchOverlay('hover');
    }
    void sendBrowserWorkbenchInteraction('wheel',{x:point.x,y:point.y,delta_x:event.deltaX||0,delta_y:event.deltaY||0});
  }

  function handleBrowserWorkbenchViewportKeydown(event){
    if(areaCaptureMode&&event&&event.key==='Escape'){
      event.preventDefault();
      cancelBrowserWorkbenchAreaCapture();
      setStatus('Area capture canceled.','muted',getActiveWorkbenchTab(),{owner:'capture',kind:'temporary'});
      return;
    }
    if(!canInteractWithBrowserWorkbenchViewport('key')||!event)return;
    const activeElement=document.activeElement;
    if(activeElement&&activeElement!==viewportEl&&activeElement.closest&&activeElement.closest('input,textarea,select,[contenteditable="true"]'))return;
    const key=String(event.key||'');
    if(!key)return;
    event.preventDefault();
    const printable=key.length===1&&!event.ctrlKey&&!event.metaKey&&!event.altKey;
    void sendBrowserWorkbenchInteraction(printable?'text':'key',{
      key,
      code:String(event.code||''),
      text:printable?key:'',
      alt_key:event.altKey===true,
      ctrl_key:event.ctrlKey===true,
      meta_key:event.metaKey===true,
      shift_key:event.shiftKey===true,
    });
  }

  function clearBrowserWorkbenchOverlay(kind){
    wireDom();
    if(!viewportEl)return;
    const overlayKind=kind==='selection'?'selection':'hover';
    const selector=overlayKind==='selection'?'.browser-workbench-selection-overlay,.browser-workbench-selection-overlay-label':'.browser-workbench-hover-overlay,.browser-workbench-hover-overlay-label';
    viewportEl.querySelectorAll(selector).forEach((node)=>node.remove());
    if(browserWorkbenchOverlayPreview&&browserWorkbenchOverlayPreview.kind===overlayKind)browserWorkbenchOverlayPreview=null;
  }

  function cancelBrowserWorkbenchHoverInspect(){
    if(hoverInspectTimer){
      clearTimeout(hoverInspectTimer);
      hoverInspectTimer=null;
    }
    hoverInspectPointKey='';
    hoverInspectRequestId+=1;
  }

  function clampBrowserWorkbenchOverlayValue(value,min,max){
    const floor=Number(min)||0;
    const ceiling=Number(max);
    if(!Number.isFinite(ceiling)||ceiling<floor)return floor;
    return Math.max(floor,Math.min(ceiling,Number(value)||0));
  }

  function positionBrowserWorkbenchOverlayLabel(label,target,container){
    if(!label||!target||!container)return;
    const safe=BROWSER_WORKBENCH_SELECTION_LABEL_SAFE_PADDING;
    const gap=BROWSER_WORKBENCH_SELECTION_LABEL_GAP;
    const containerWidth=Math.max(0,Number(container.width)||0);
    const containerHeight=Math.max(0,Number(container.height)||0);
    label.style.maxWidth=`${Math.max(40,containerWidth-(safe*2))}px`;
    label.style.visibility='hidden';
    label.style.display='inline-flex';
    const measured=label.getBoundingClientRect?label.getBoundingClientRect():{width:0,height:0};
    const labelWidth=Math.max(1,Math.ceil(measured.width||label.offsetWidth||0));
    const labelHeight=Math.max(1,Math.ceil(measured.height||label.offsetHeight||0));
    const targetLeft=Number(target.left)||0;
    const targetTop=Number(target.top)||0;
    const targetBottom=targetTop+Math.max(0,Number(target.height)||0);
    const aboveTop=targetTop-labelHeight-gap;
    const belowTop=targetBottom+gap;
    let top=aboveTop;
    let placement='above';
    if(top<safe){
      top=belowTop;
      placement='below';
    }
    if(top+labelHeight>containerHeight-safe){
      const clampedAbove=Math.max(safe,aboveTop);
      if(aboveTop>=safe||clampedAbove+labelHeight<=containerHeight-safe){
        top=clampedAbove;
        placement='above';
      }else{
        top=clampBrowserWorkbenchOverlayValue(top,safe,containerHeight-labelHeight-safe);
        placement='clamped';
      }
    }
    top=clampBrowserWorkbenchOverlayValue(top,safe,containerHeight-labelHeight-safe);
    const left=clampBrowserWorkbenchOverlayValue(targetLeft,safe,containerWidth-labelWidth-safe);
    label.style.left=`${left}px`;
    label.style.top=`${top}px`;
    label.style.visibility='visible';
    label.dataset.placement=placement;
  }

  function renderBrowserWorkbenchOverlay(rect,label,kind,selection){
    wireDom();
    if(!viewportEl||!rect)return;
    const overlayKind=kind==='selection'?'selection':'hover';
    clearBrowserWorkbenchOverlay(overlayKind);
    const surface=viewportEl.querySelector('.browser-workbench-surface-region');
    const active=getActiveWorkbenchTab();
    const iframe=active&&active.renderer==='iframe-bridge'?activeBrowserWorkbenchIframe():null;
    const overlayHost=iframe&&iframe.closest?iframe.closest('.browser-workbench-frame-wrap')||viewportEl:surface||viewportEl;
    const hostFrame=overlayHost.getBoundingClientRect();
    const renderFrame=iframe&&iframe.getBoundingClientRect?iframe.getBoundingClientRect():hostFrame;
    if(!hostFrame||!renderFrame||renderFrame.width<=0||renderFrame.height<=0)return;
    let iframeViewportWidth=0;
    let iframeViewportHeight=0;
    if(iframe){
      try{
        iframeViewportWidth=Number(iframe.contentWindow&&iframe.contentWindow.innerWidth)||0;
        iframeViewportHeight=Number(iframe.contentWindow&&iframe.contentWindow.innerHeight)||0;
      }catch(_err){}
      iframeViewportWidth=iframeViewportWidth||Number(iframe.clientWidth)||Number(iframe.offsetWidth)||0;
      iframeViewportHeight=iframeViewportHeight||Number(iframe.clientHeight)||Number(iframe.offsetHeight)||0;
      const zoom=Math.max(0.25,Math.min(3,Number(active&&active.zoom)||1));
      iframeViewportWidth=iframeViewportWidth||renderFrame.width/zoom;
      iframeViewportHeight=iframeViewportHeight||renderFrame.height/zoom;
    }
    const viewport=iframe
      ?{width:Math.max(1,iframeViewportWidth),height:Math.max(1,iframeViewportHeight)}
      :currentBrowserWorkbenchViewport();
    const scaleX=renderFrame.width/viewport.width;
    const scaleY=renderFrame.height/viewport.height;
    const offsetLeft=renderFrame.left-hostFrame.left;
    const offsetTop=renderFrame.top-hostFrame.top;
    const left=offsetLeft+(Number(rect.left??rect.x??0)*scaleX);
    const top=offsetTop+(Number(rect.top??rect.y??0)*scaleY);
    const width=Math.max(8,Number(rect.width||18)*scaleX);
    const height=Math.max(8,Number(rect.height||18)*scaleY);
    const box=document.createElement('div');
    const overlayClass=overlayKind==='selection'?'browser-workbench-selection-overlay':'browser-workbench-hover-overlay';
    box.className=overlayClass;
    box.style.left=`${Math.max(0,left)}px`;
    box.style.top=`${Math.max(0,top)}px`;
    box.style.width=`${width}px`;
    box.style.height=`${height}px`;
    const tag=document.createElement('span');
    tag.className=`${overlayClass}-label browser-workbench-selection-overlay-label`;
    const elementSelection=selection&&typeof selection==='object'?selection:null;
    const tagName=browserWorkbenchHtmlTagName(elementSelection&&elementSelection.tag);
    const componentName=String(elementSelection&&elementSelection.component||'').replace(/\s+/g,' ').trim();
    const safeComponent=componentName&&componentName.toLowerCase()!=='unknown'?componentName:'';
    const finalLabel=elementSelection?browserWorkbenchElementLabel(safeComponent,tagName,label):String(label||'Browser element');
    tag.title=finalLabel;
    if(safeComponent&&tagName){
      const componentPart=textEl('span','browser-workbench-selection-overlay-component',safeComponent);
      const separatorPart=textEl('span','browser-workbench-selection-overlay-separator','·');
      const tagPart=textEl('span','browser-workbench-selection-overlay-tag',tagName);
      tag.append(componentPart,separatorPart,tagPart);
    }else{
      tag.textContent=finalLabel;
    }
    overlayHost.appendChild(box);
    overlayHost.appendChild(tag);
    positionBrowserWorkbenchOverlayLabel(tag,{left:Math.max(0,left),top:Math.max(0,top),width,height},{width:hostFrame.width,height:hostFrame.height});
    browserWorkbenchOverlayPreview={
      tabId:String(active&&active.id||''),
      rect:{
        left:Number(rect.left??rect.x??0),
        top:Number(rect.top??rect.y??0),
        width:Number(rect.width||18),
        height:Number(rect.height||18),
      },
      label:String(label||''),
      kind:overlayKind,
      selection:elementSelection,
    };
  }

  function refreshBrowserWorkbenchOverlayProjection(tab){
    const target=tab||getActiveWorkbenchTab();
    const preview=browserWorkbenchOverlayPreview;
    if(!target||!preview||preview.tabId!==String(target.id||''))return false;
    renderBrowserWorkbenchOverlay(preview.rect,preview.label,preview.kind,preview.selection);
    return true;
  }

  function updateBrowserWorkbenchHoverOverlay(event){
    if(!selectionMode||selectionModeTabId!==activeBrowserWorkbenchTabId)return;
    const point=browserWorkbenchPointFromEvent(event);
    if(!point)return;
    const pointKey=`${activeBrowserWorkbenchTabId}:${Math.round(point.x/4)*4}:${Math.round(point.y/4)*4}`;
    if(pointKey===hoverInspectPointKey)return;
    hoverInspectPointKey=pointKey;
    if(hoverInspectTimer)clearTimeout(hoverInspectTimer);
    const requestId=++hoverInspectRequestId;
    hoverInspectTimer=setTimeout(()=>{
      hoverInspectTimer=null;
      void inspectBrowserWorkbenchPoint(point).then((rawSelection)=>{
        if(requestId!==hoverInspectRequestId)return;
        const selected=normalizeSelection(rawSelection);
        if(selected.rect){
          renderBrowserWorkbenchOverlay(selected.rect,selected.displayLabel||selected.selector,'hover',selected);
        }else{
          renderBrowserWorkbenchOverlay({left:point.x-9,top:point.y-9,width:18,height:18},'Click to ping element','hover');
        }
      }).catch(()=>{
        if(requestId===hoverInspectRequestId)renderBrowserWorkbenchOverlay({left:point.x-9,top:point.y-9,width:18,height:18},'Click to ping element','hover');
      });
    },BROWSER_WORKBENCH_HOVER_INSPECT_DELAY_MS);
  }

  async function inspectBrowserWorkbenchPoint(point){
    const active=getActiveWorkbenchTab();
    if(!active||!active.sessionId||!point)throw new Error('Open a Browser Workbench session before selecting an element.');
    const data=await requestJSON(`${sessionStatusUrl(active.sessionId)}/inspect`,{
      method:'POST',
      body:browserWorkbenchRequestBody({x:point.x,y:point.y})
    });
    return (data&&data.selection)||{};
  }

  async function inspectBrowserWorkbenchAt(event){
    const point=browserWorkbenchPointFromEvent(event);
    return inspectBrowserWorkbenchPoint(point);
  }

  function insertIntoComposer(text){
    const input=document.getElementById('msg');
    if(!input)return false;
    const value=input.value||'';
    const focused=document.activeElement===input;
    const remembered=lastComposerSelection||{};
    const start=focused&&typeof input.selectionStart==='number'?input.selectionStart:Math.min(remembered.start||0,value.length);
    const end=focused&&typeof input.selectionEnd==='number'?input.selectionEnd:Math.min(remembered.end||start,value.length);
    const prefix=value&&start>0&&!value.slice(0,start).endsWith('\n')?'\n':'';
    const suffix=value.slice(end)&&!String(text).endsWith('\n')?'\n':'';
    const insert=prefix+text+suffix;
    input.value=value.slice(0,start)+insert+value.slice(end);
    const cursor=start+insert.length;
    input.selectionStart=cursor;
    input.selectionEnd=cursor;
    input.dispatchEvent(new Event('input',{bubbles:true}));
    lastComposerSelection={start:cursor,end:cursor};
    return true;
  }

  function browserWorkbenchHtmlTagName(value){
    const tag=String(value||'').trim();
    if(!tag||tag.toLowerCase()==='unknown'||/[\s<>"']/.test(tag))return '';
    return tag.slice(0,64);
  }

  function browserWorkbenchElementLabel(component,tag,fallback){
    const componentName=String(component||'').replace(/\s+/g,' ').trim();
    const safeComponent=componentName&&componentName.toLowerCase()!=='unknown'?componentName:'';
    const tagName=browserWorkbenchHtmlTagName(tag);
    const fallbackLabel=String(fallback||'').replace(/\s+/g,' ').trim();
    if(safeComponent&&tagName){
      const suffix=` · ${tagName}`;
      const available=Math.max(1,96-suffix.length);
      const shownComponent=safeComponent.length<=available?safeComponent:available===1?'…':`${safeComponent.slice(0,available-1)}…`;
      return shownComponent+suffix;
    }
    return (safeComponent||tagName||fallbackLabel||'Browser element').slice(0,96);
  }

  function normalizeSelection(selection){
    const raw=selection&&typeof selection==='object'?selection:{};
    const active=getActiveWorkbenchTab();
    const tagName=browserWorkbenchHtmlTagName(raw.tag||raw.tagName||raw.htmlTag||raw.nodeName);
    const payload={
      tab:active?browserWorkbenchDisplayLabel(active):'Browser',
      url:String(raw.url||active&&active.url||urlInput&&urlInput.value||'about:blank'),
      session_id:String(raw.session_id||active&&active.sessionId||'none'),
      selector:String(raw.selector||raw.cssSelector||raw.path||'unavailable (inspection backend not connected yet)'),
      text:String(raw.text||raw.label||''),
      component:String(raw.component||raw.componentName||'unknown'),
      tag:tagName,
      source:String(raw.source||raw.file||raw.pathHint||'unknown'),
      rect:raw.rect&&typeof raw.rect==='object'?raw.rect:null,
      point:raw.point&&typeof raw.point==='object'?raw.point:null,
      frame:raw.frame&&typeof raw.frame==='object'?{
        selector:String(raw.frame.selector||''),
        src:String(raw.frame.src||''),
        sameOrigin:raw.frame.sameOrigin===true||raw.frame.same_origin===true,
      }:null,
      frames:Array.isArray(raw.frames)?raw.frames.slice(0,5).filter((frame)=>frame&&typeof frame==='object').map((frame)=>({
        selector:String(frame.selector||''),
        src:String(frame.src||''),
        sameOrigin:frame.sameOrigin===true||frame.same_origin===true,
      })):null,
    };
    const displayLabel=browserWorkbenchElementLabel(payload.component,payload.tag,raw.displayLabel||raw.display_label||payload.selector||payload.url);
    return {
      type:'browser_element',
      kind:'browser-element',
      displayLabel,
      payload,
      ...payload,
    };
  }

  async function pingBrowserWorkbenchSelection(eventOrSelection){
    wireDom();
    const active=getActiveWorkbenchTab();
    let rawSelection=eventOrSelection;
    let statusToken=null;
    if(eventOrSelection&&typeof eventOrSelection.clientX==='number'){
      statusToken=setStatus('Selecting element…','muted',active,{owner:'selection-action',kind:'progress'});
      try{
        rawSelection=await inspectBrowserWorkbenchAt(eventOrSelection);
      }catch(err){
        if(!browserWorkbenchStatusTokenIsCurrent(statusToken))return null;
        browserWorkbenchResolveStatus(statusToken,'Element selection failed.',{kind:'error',tone:'warning'});
        throw err;
      }
      if(!browserWorkbenchStatusTokenIsCurrent(statusToken))return null;
    }
    const selected=normalizeSelection(rawSelection);
    clearBrowserWorkbenchOverlay('selection');
    if(typeof addBrowserContextItem==='function'){
      addBrowserContextItem(selected);
    }else{
      insertIntoComposer(`[Browser Workbench selection]\nLabel: ${selected.displayLabel}\nSelector: ${selected.selector}\nComponent: ${selected.component}\nSource: ${selected.source}`);
    }
    browserWorkbenchClearStatus(active,{owner:'selection-action'});
    if(selectionMode&&selectionModeTabId===active.id){
      setStatus('Element added. Select another or press Escape to finish.','muted',active,{owner:'selection',kind:'persistent'});
    }else{
      setStatus('Element added.','ready',active,{owner:'selection',kind:'temporary'});
    }
    return selected;
  }

  function previewBrowserWorkbenchSelection(selection,visible){
    if(visible===false){
      clearBrowserWorkbenchOverlay('hover');
      return false;
    }
    const selected=normalizeSelection(selection);
    const active=getActiveWorkbenchTab();
    if(!active||!selected.rect)return false;
    if(selected.session_id&&active.sessionId&&selected.session_id!==active.sessionId)return false;
    renderBrowserWorkbenchOverlay(selected.rect,selected.displayLabel||selected.selector,'hover',selected);
    return true;
  }

  function wireBrowserWorkbenchLauncher(){
    wireDom();
    restoreBrowserWorkbenchTabs();
    applyBrowserWorkbenchAvailability(window._browserWorkbenchEnabled===true);
    if(window._browserWorkbenchEnabled!==true){
      refreshBrowserWorkbenchCapabilities();
    }
    if(restoredBrowserWorkbenchPanel&&activeBrowserWorkbenchTabId){
      const tabId=activeBrowserWorkbenchTabId;
      restoredBrowserWorkbenchPanel=false;
      setTimeout(()=>{
        activateBrowserWorkbenchTab(tabId,{switchPanel:false});
        if(typeof switchPanel==='function')switchPanel('browser');
      },BROWSER_WORKBENCH_RESTORED_OPEN_DELAY_MS);
    }
  }

  function applyBrowserWorkbenchAvailability(enabled){
    const available=enabled===true;
    window._browserWorkbenchEnabled=available;
    document.documentElement.dataset.browserWorkbench=available?'enabled':'disabled';
    wireDom();
    if(openerButton)openerButton.hidden=false;
    if(!available)setBrowserWorkbenchSelectionMode(false);
    renderActiveBrowserWorkbenchView();
  }

  function getBrowserWorkbenchDebugState(){
    return {
      active_tab_id:activeBrowserWorkbenchTabId,
      tab_count:workbenchTabs.size,
      tabs:Array.from(workbenchTabs.values()).map((tab)=>({id:tab.id,label:browserWorkbenchDisplayLabel(tab),session_id:tab.sessionId,state:tab.state,content_state:normalizeBrowserWorkbenchContentState(tab.contentState),load_status:normalizeBrowserWorkbenchLoadStatus(tab.loadStatus),url:tab.url,current_url:tab.currentUrl||'',requested_url:tab.requestedUrl||'',last_loaded_url:tab.lastLoadedUrl||'',has_started_load:tab.hasStartedLoad===true,has_committed_navigation:tab.hasCommittedNavigation===true,devtools_open:tab.devtoolsOpen===true,favicon_url:tab.faviconUrl||''})),
    };
  }

  window.openBrowserWorkbenchTab=openBrowserWorkbenchTab;
  window.closeBrowserWorkbenchTab=closeBrowserWorkbenchTab;
  window.ensureBrowserWorkbenchSessionOnOpen=ensureBrowserWorkbenchSessionOnOpen;
  window.openBrowserWorkbenchShell=openBrowserWorkbenchTab;
  window.closeBrowserWorkbenchShell=closeBrowserWorkbenchTab;
  window.startBrowserWorkbenchSession=startBrowserWorkbenchSession;
  window.refreshBrowserWorkbenchSession=refreshBrowserWorkbenchSession;
  window.navigateBrowserWorkbenchToUrl=navigateBrowserWorkbenchToUrl;
  window.navigateBrowserWorkbenchHistory=navigateBrowserWorkbenchHistory;
  window.closeCurrentBrowserWorkbenchSession=closeBrowserWorkbenchTab;
  window.pingBrowserWorkbenchSelection=pingBrowserWorkbenchSelection;
  window.previewBrowserWorkbenchSelection=previewBrowserWorkbenchSelection;
  window.toggleBrowserWorkbenchSelectionMode=toggleBrowserWorkbenchSelectionMode;
  window.syncBrowserWorkbenchTabActive=syncBrowserWorkbenchTabActive;
  window.applyBrowserWorkbenchAvailability=applyBrowserWorkbenchAvailability;
  window.getBrowserWorkbenchDebugState=getBrowserWorkbenchDebugState;

  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',wireBrowserWorkbenchLauncher,{once:true});
  }else{
    wireBrowserWorkbenchLauncher();
  }
})();
