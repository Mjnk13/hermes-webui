'use strict';

const { app, BrowserWindow, WebContentsView, ipcMain } = require('electron');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const DEFAULT_WEBUI_URL = 'http://127.0.0.1:8789';
const NATIVE_SELECTION_CONSOLE_PREFIX = '__HERMES_BROWSER_WORKBENCH_SELECTION__';
const URL_SUGGESTION_CONSOLE_PREFIX = '__HERMES_BROWSER_WORKBENCH_URL_SUGGESTION__';
const ACTIONS_MENU_CONSOLE_PREFIX = '__HERMES_BROWSER_WORKBENCH_ACTIONS_MENU__';
const LOAD_STATUS_USABLE_AFTER_MS = 10000;
const LOAD_STATUS_TIMEOUT_MS = 45000;
const LOAD_STATUS_MAIN_FRAME_SETTLE_MS = 250;
const LOAD_EVENT_LOG_LIMIT = 20;
const STABLE_SURFACE_BACKGROUND = '#0D0D1A';
const bridgeToken = crypto.randomBytes(32).toString('base64url');
const tabs = new Map();

let mainWindow = null;
let bridgeServer = null;
let bridgeUrl = '';
let activeSessionId = '';
let urlSuggestionOverlay = null;
let actionsMenuOverlay = null;
let appIsQuitting = false;
let applicationOverlaySuppression = { suppressed: false, generation: 0, overlayCount: 0 };

function desktopAppPidFilePath() {
  const raw = String(process.env.HERMES_WEBUI_DESKTOP_APP_PID_FILE || '').trim();
  if (!raw) return '';
  return path.resolve(raw);
}

function writeDesktopAppPidFile() {
  const pidFile = desktopAppPidFilePath();
  if (!pidFile) return;
  try {
    fs.mkdirSync(path.dirname(pidFile), { recursive: true });
    fs.writeFileSync(pidFile, `${process.pid}\n`, { encoding: 'utf8' });
  } catch (err) {
    console.warn('[Hermes Desktop] Could not write app PID file:', err && err.message ? err.message : err);
  }
}

function removeDesktopAppPidFile() {
  const pidFile = desktopAppPidFilePath();
  if (!pidFile) return;
  try {
    const raw = fs.readFileSync(pidFile, 'utf8').trim();
    if (raw === String(process.pid)) fs.rmSync(pidFile, { force: true });
  } catch (_) {}
}

function jsonResponse(res, status, payload) {
  const body = JSON.stringify(payload || {});
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8');
      if (!raw) return resolve({});
      try { resolve(JSON.parse(raw)); }
      catch (err) { reject(err); }
    });
    req.on('error', reject);
  });
}

function normalizeUrl(raw) {
  const value = String(raw || '').trim();
  if (!value) return 'about:blank';
  if (/^[a-z][a-z0-9+.-]*:/i.test(value)) return value;
  return `http://${value}`;
}

function safeFaviconUrl(raw) {
  const value = String(raw || '').trim();
  if (!value) return '';
  if (/^data:image\//i.test(value)) return value.slice(0, 4096);
  try {
    const parsed = new URL(value);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.toString() : '';
  } catch (_) {
    return '';
  }
}

function firstFaviconUrl(favicons) {
  const list = Array.isArray(favicons) ? favicons : [];
  for (const icon of list) {
    const safe = safeFaviconUrl(icon);
    if (safe) return safe;
  }
  return '';
}

function assertSafeBrowserUrl(raw) {
  const nextUrl = normalizeUrl(raw);
  if (nextUrl === 'about:blank') return nextUrl;
  let parsed;
  try {
    parsed = new URL(nextUrl);
  } catch (err) {
    throw new Error(`invalid browser URL: ${err && err.message ? err.message : String(err)}`);
  }
  if (parsed.username || parsed.password) throw new Error('credential-bearing browser URLs are not allowed');
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new Error(`unsupported browser URL scheme: ${parsed.protocol.replace(/:$/, '') || 'unknown'}`);
  }
  return parsed.toString();
}

function isPublicBrowserNavigationUrl(raw) {
  const value = String(raw || '').trim();
  return value === 'about:blank' || /^https?:\/\//i.test(value);
}

function normalizeZoom(raw, fallback = 1) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) return fallback;
  return Math.max(0.25, Math.min(3, value));
}

function applyRecordPayload(record, payload) {
  if (!record || !payload || typeof payload !== 'object') return;
  record.tabId = String(payload.tab_id || payload.tabId || record.tabId || '');
  if (payload.url !== undefined && String(payload.url || '').trim()) record.url = String(payload.url).trim();
  if (payload.title !== undefined) record.title = String(payload.title || '');
  if (payload.favicon_url !== undefined || payload.faviconUrl !== undefined) record.faviconUrl = safeFaviconUrl(payload.favicon_url || payload.faviconUrl);
  if (payload.viewport && typeof payload.viewport === 'object') record.viewport = payload.viewport;
  if (payload.zoom !== undefined) {
    record.zoom = normalizeZoom(payload.zoom, record.zoom || 1);
    record.view.webContents.setZoomFactor(record.zoom);
  }
}

function rememberRecordLoadEvent(record, event, detail) {
  if (!record) return;
  const item = {
    event: String(event || 'unknown'),
    at: Date.now(),
    detail: detail && typeof detail === 'object' ? detail : {},
  };
  record.lastLoadEvent = item.event;
  record.loadEvents = Array.isArray(record.loadEvents) ? record.loadEvents : [];
  record.loadEvents.push(item);
  if (record.loadEvents.length > LOAD_EVENT_LOG_LIMIT) record.loadEvents.splice(0, record.loadEvents.length - LOAD_EVENT_LOG_LIMIT);
}

function setRecordLoadStatus(record, status, error) {
  if (!record) return;
  const raw = String(status || 'idle').toLowerCase();
  const next = ['idle', 'loading', 'success', 'error'].includes(raw) ? raw : 'idle';
  record.loadStatus = next;
  record.loadError = next === 'error' ? String(error || 'Load failed') : '';
  if (next === 'loading') {
    record.loadStartedAt = record.loadStartedAt || Date.now();
    record.loadFailed = false;
  } else {
    record.loadStartedAt = 0;
    record.loadFailed = next === 'error';
  }
}

function isExpectedNavigationAbort(errorCode, errorDescription) {
  return Number(errorCode) === -3 || String(errorDescription || '').toUpperCase() === 'ERR_ABORTED';
}

function comparableNavigationUrl(raw) {
  const value = String(raw || '').trim();
  if (!value) return '';
  try { return new URL(value).toString(); }
  catch (_) { return value; }
}

function isSupersededMainFrameFailure(record, validatedURL) {
  const failedUrl = comparableNavigationUrl(validatedURL);
  const requestedUrl = comparableNavigationUrl(record && record.loadRequestedUrl);
  return !!(record && failedUrl && requestedUrl && failedUrl !== requestedUrl);
}

function chromiumErrorName(errorCode, errorDescription) {
  const description = String(errorDescription || '').trim().toUpperCase();
  if (/^ERR_[A-Z0-9_]+$/.test(description)) return description;
  const known = {
    '-105': 'ERR_NAME_NOT_RESOLVED',
    '-106': 'ERR_INTERNET_DISCONNECTED',
    '-118': 'ERR_CONNECTION_TIMED_OUT',
    '-130': 'ERR_PROXY_CONNECTION_FAILED',
    '-102': 'ERR_CONNECTION_REFUSED',
  };
  return known[String(Number(errorCode))] || description || `ERR_FAILED_${Number(errorCode) || 0}`;
}

function handleRecordMainFrameFailure(record, errorCode, errorDescription, validatedURL, isMainFrame, source) {
  rememberRecordLoadEvent(record, source || 'did-fail-load', { errorCode, errorDescription, validatedURL, isMainFrame });
  if (isMainFrame === false || isExpectedNavigationAbort(errorCode, errorDescription)) return;
  if (isSupersededMainFrameFailure(record, validatedURL)) return;
  const failedUrl = String(validatedURL || record.loadRequestedUrl || record.url || '').trim();
  if (failedUrl) record.url = failedUrl;
  record.navigationError = {
    error_code: Number(errorCode) || 0,
    error_description: String(errorDescription || ''),
    chromium_error: chromiumErrorName(errorCode, errorDescription),
    validated_url: failedUrl,
    is_main_frame: true,
  };
  setRecordLoadStatus(record, 'error', String(errorDescription || chromiumErrorName(errorCode, errorDescription) || 'Load failed'));
  sendBrowserNavigationUpdate(record, source || 'did-fail-load');
}

function markRecordLoading(record, reason, url) {
  if (!record) return;
  record.loadStartedAt = Date.now();
  record.loadRequestId = (record.loadRequestId || 0) + 1;
  record.loadRequestedUrl = url ? String(url) : record.loadRequestedUrl || record.url || '';
  record.title = '';
  record.faviconUrl = '';
  record.documentReadyState = 'loading';
  record.navigationError = null;
  rememberRecordLoadEvent(record, reason || 'loading', { url: record.loadRequestedUrl });
  setRecordLoadStatus(record, 'loading');
}

function markRecordReady(record, reason) {
  if (!record || record.loadFailed) return;
  record.lastMainFrameReadyAt = Date.now();
  record.documentReadyState = record.documentReadyState && record.documentReadyState !== 'loading' ? record.documentReadyState : 'complete';
  rememberRecordLoadEvent(record, reason || 'ready', { readyState: record.documentReadyState });
  setRecordLoadStatus(record, 'success');
}

function markRecordReadyIfMainFrameSettled(record, reason) {
  if (!record || record.loadStatus !== 'loading' || record.loadFailed) return false;
  const mainFrameLoading = isRecordMainFrameLoading(record);
  const waitingForResponse = isRecordWaitingForResponse(record);
  const elapsed = record.loadStartedAt ? Date.now() - record.loadStartedAt : 0;
  if (mainFrameLoading === false && waitingForResponse !== true && elapsed >= LOAD_STATUS_MAIN_FRAME_SETTLE_MS) {
    markRecordReady(record, reason || 'main-frame-settled');
    return true;
  }
  return false;
}

function scheduleRecordReady(record, reason, delay) {
  if (!record || record.loadStatus !== 'loading') return;
  const requestId = record.loadRequestId || 0;
  setTimeout(() => {
    if (!record || record.loadStatus !== 'loading' || (record.loadRequestId || 0) !== requestId) return;
    markRecordReady(record, reason || 'scheduled-ready');
  }, Math.max(0, Number(delay) || LOAD_STATUS_MAIN_FRAME_SETTLE_MS));
}

function isRecordMainFrameLoading(record) {
  const wc = record && record.view && record.view.webContents;
  if (!wc || wc.isDestroyed()) return false;
  try {
    if (typeof wc.isLoadingMainFrame === 'function') return wc.isLoadingMainFrame();
  } catch (_) {}
  return null;
}

function isRecordWebContentsLoading(record) {
  const mainFrameLoading = isRecordMainFrameLoading(record);
  if (mainFrameLoading !== null) return mainFrameLoading;
  const wc = record && record.view && record.view.webContents;
  if (!wc || wc.isDestroyed()) return false;
  try {
    if (typeof wc.isLoading === 'function') return wc.isLoading();
  } catch (_) {}
  return false;
}

function isRecordWaitingForResponse(record) {
  const wc = record && record.view && record.view.webContents;
  if (!wc || wc.isDestroyed()) return false;
  try {
    if (typeof wc.isWaitingForResponse === 'function') return wc.isWaitingForResponse();
  } catch (_) {}
  return false;
}

function readRecordDocumentReadyState(record, reason) {
  const wc = record && record.view && record.view.webContents;
  if (!wc || wc.isDestroyed()) return;
  const requestId = record.loadRequestId || 0;
  wc.executeJavaScript('document.readyState', true).then((state) => {
    if ((record.loadRequestId || 0) !== requestId) return;
    const readyState = String(state || '').toLowerCase();
    if (['loading', 'interactive', 'complete'].includes(readyState)) record.documentReadyState = readyState;
    rememberRecordLoadEvent(record, reason || 'ready-state', { readyState: record.documentReadyState || readyState });
    refreshRecordLoadStatus(record);
    if (readyState === 'interactive' || readyState === 'complete') {
      scheduleRecordReady(record, `${reason || 'ready-state'}-settled`, LOAD_STATUS_MAIN_FRAME_SETTLE_MS);
    }
  }).catch((err) => {
    rememberRecordLoadEvent(record, `${reason || 'ready-state'}-failed`, { error: err && err.message ? err.message : String(err || '') });
    markRecordReadyIfMainFrameSettled(record, `${reason || 'ready-state'}-fallback`);
  });
}

function refreshRecordLoadStatus(record) {
  if (!record || record.loadStatus !== 'loading') return;
  const wc = record.view && record.view.webContents;
  if (!wc || wc.isDestroyed()) return;
  const currentUrl = wc.getURL() || record.url;
  if (currentUrl === 'about:blank') {
    setRecordLoadStatus(record, 'idle');
    return;
  }
  const elapsed = record.loadStartedAt ? Date.now() - record.loadStartedAt : 0;
  const readyState = String(record.documentReadyState || '').toLowerCase();
  const documentReady = readyState === 'interactive' || readyState === 'complete';
  const mainFrameLoading = isRecordMainFrameLoading(record);
  const waitingForResponse = isRecordWaitingForResponse(record);
  if (documentReady && elapsed >= LOAD_STATUS_MAIN_FRAME_SETTLE_MS) {
    markRecordReady(record, 'document-ready-settled');
    return;
  }
  if (documentReady && mainFrameLoading !== true && waitingForResponse !== true) {
    markRecordReady(record, 'main-frame-document-ready');
    return;
  }
  if (mainFrameLoading === false && waitingForResponse !== true && elapsed >= LOAD_STATUS_MAIN_FRAME_SETTLE_MS) {
    markRecordReady(record, 'main-frame-settled');
    return;
  }
  if (documentReady && elapsed >= LOAD_STATUS_USABLE_AFTER_MS && waitingForResponse !== true) {
    markRecordReady(record, 'document-ready-watchdog');
    return;
  }
  if (mainFrameLoading === false && waitingForResponse !== true && (record.lastMainFrameReadyAt || elapsed >= LOAD_STATUS_USABLE_AFTER_MS)) {
    markRecordReady(record, 'main-frame-not-loading');
    return;
  }
  if (elapsed >= LOAD_STATUS_TIMEOUT_MS) {
    setRecordLoadStatus(record, 'error', `Page load timed out waiting for the main frame${currentUrl ? `: ${currentUrl}` : ''}`);
  }
}

function assertWebContentsViewAvailable() {
  if (!WebContentsView) {
    throw new Error('Electron WebContentsView is unavailable. Use Electron 30+ for Hermes Browser Workbench native mode.');
  }
}

function createMainWindow() {
  const payload = Buffer.from(JSON.stringify({ bridgeUrl, bridgeToken })).toString('base64url');
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 980,
    minHeight: 680,
    title: 'Hermes WebUI',
    backgroundColor: STABLE_SURFACE_BACKGROUND,
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      additionalArguments: [`--hermes-desktop-bridge=${payload}`],
    },
  });

  try { mainWindow.setBackgroundColor(STABLE_SURFACE_BACKGROUND); } catch (_) {}
  try { mainWindow.webContents.setBackgroundColor(STABLE_SURFACE_BACKGROUND); } catch (_) {}

  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    resetApplicationOverlaySuppression();
    const reason = String(details && details.reason || 'unknown');
    const exitCode = details && details.exitCode !== undefined ? ` exitCode=${details.exitCode}` : '';
    console.warn(`[Hermes Desktop] WebUI renderer process gone: ${reason}${exitCode}`);
    if (appIsQuitting || !mainWindow || mainWindow.isDestroyed()) return;
    // A user/system restart of the Electron shell can briefly tear down the
    // renderer with a clean/killed reason. Treat only actual renderer crashes as
    // fatal; otherwise keep the native shell alive instead of making restart look
    // like an app crash.
    if (reason === 'crashed' || reason === 'oom' || reason === 'launch-failed') {
      app.quit();
    }
  });
  mainWindow.webContents.on('did-start-loading', () => resetApplicationOverlaySuppression());
  mainWindow.on('closed', () => {
    mainWindow = null;
    hideUrlSuggestionOverlay();
    hideActionsMenuOverlay();
    closeAllNativeTabs();
    if (bridgeServer) bridgeServer.close();
  });
  mainWindow.on('blur', () => { hideUrlSuggestionOverlay(); hideActionsMenuOverlay(); });

  const webuiUrl = process.env.HERMES_WEBUI_URL || DEFAULT_WEBUI_URL;
  mainWindow.loadURL(webuiUrl).catch((err) => {
    console.error('Failed to load Hermes WebUI:', err);
    app.quit();
  });
}

function sanitizeUrlSuggestionItems(items) {
  return (Array.isArray(items) ? items : []).slice(0, 5).map((item, index) => ({
    id: String(item && item.id !== undefined ? item.id : index).slice(0, 32),
    title: String(item && item.title || item && item.url || '').replace(/\s+/g, ' ').trim().slice(0, 160),
    url: String(item && item.url || '').replace(/\s+/g, ' ').trim().slice(0, 2048),
  })).filter((item) => item.url);
}

function normalizeAnchorRect(raw) {
  const rect = raw && typeof raw === 'object' ? raw : {};
  return {
    x: Math.max(0, Math.round(Number(rect.x) || 0)),
    y: Math.max(0, Math.round(Number(rect.y) || 0)),
    width: Math.max(1, Math.round(Number(rect.width) || 1)),
    height: Math.max(1, Math.round(Number(rect.height) || 1)),
  };
}

function makeFloatingOverlayViewTransparent(view) {
  if (!view) return;
  try { view.setBackgroundColor('#00000000'); } catch (_) {}
}

function resizeUrlSuggestionOverlayToContent(overlay, bounds) {
  if (!overlay || !overlay.view || !bounds || !overlay.visible) return;
  overlay.view.webContents.executeJavaScript(`(() => {
    const box = document.getElementById('box');
    if (!box || !box.getBoundingClientRect) return 0;
    return Math.ceil(box.getBoundingClientRect().height || 0);
  })()`, true).then((height) => {
    if (!overlay.visible || !overlay.view) return;
    const measured = Math.round(Number(height) || 0);
    if (measured <= 0) return;
    const nextBounds = Object.assign({}, bounds, {
      height: Math.max(1, Math.min(Math.round(bounds.height), measured)),
    });
    overlay.bounds = nextBounds;
    try { overlay.view.setBounds(nextBounds); } catch (_) {}
  }).catch(() => {});
}

function urlSuggestionOverlayHtml(payload) {
  const items = sanitizeUrlSuggestionItems(payload && payload.items);
  const requestedActiveIndex = Math.round(Number(payload && payload.activeIndex));
  const activeIndex = Number.isFinite(requestedActiveIndex) && requestedActiveIndex >= 0 && requestedActiveIndex < items.length ? requestedActiveIndex : -1;
  const prefix = URL_SUGGESTION_CONSOLE_PREFIX;
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    :root{color-scheme:dark;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
    html,body{margin:0;padding:0;background:transparent;overflow:hidden;}
    #box{box-sizing:border-box;width:100vw;max-height:100vh;overflow:auto;padding:6px;background:#211a2c;border:1px solid rgba(255,255,255,.10);border-radius:12px;box-shadow:0 18px 52px rgba(0,0,0,.46);color:#f5f0ff;font-size:13px;}
    button{display:flex;flex-direction:column;gap:2px;width:100%;border:0;background:transparent;color:inherit;text-align:left;border-radius:9px;padding:8px 10px;font:inherit;cursor:pointer;}
    button:hover,button.active{background:rgba(255,255,255,.09);}
    .primary{font-weight:650;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
    .secondary{color:rgba(245,240,255,.68);font-size:12px;line-height:1.35;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  </style></head><body><div id="box" role="listbox"></div><script>
    const prefix=${JSON.stringify(prefix)};
    let items=${JSON.stringify(items)};
    let activeIndex=${JSON.stringify(activeIndex)};
    const box=document.getElementById('box');
    const emit=(action,index)=>console.log(prefix+JSON.stringify({action,index}));
    const render=()=>{box.textContent='';items.forEach((item,index)=>{const row=document.createElement('button');row.type='button';row.dataset.index=String(index);row.className=index===activeIndex?'active':'';row.setAttribute('role','option');row.setAttribute('aria-selected',index===activeIndex?'true':'false');const primary=document.createElement('span');primary.className='primary';primary.textContent=item.title||item.url;const secondary=document.createElement('span');secondary.className='secondary';secondary.textContent=item.url;row.append(primary,secondary);row.addEventListener('mouseenter',()=>{activeIndex=index;render();emit('hover',index);});row.addEventListener('mousedown',(event)=>{event.preventDefault();emit('accept',index);});row.addEventListener('click',(event)=>{event.preventDefault();emit('accept',index);});box.appendChild(row);});const active=box.querySelector('.active');if(active&&active.scrollIntoView)active.scrollIntoView({block:'nearest'});};
    window.__hermesUpdateUrlSuggestions=(next)=>{items=Array.isArray(next.items)?next.items:[];const requested=Math.round(Number(next.activeIndex));activeIndex=Number.isFinite(requested)&&requested>=0&&requested<items.length?requested:-1;render();};
    render();
  </script></body></html>`;
}

function ensureUrlSuggestionOverlayView() {
  assertWebContentsViewAvailable();
  if (urlSuggestionOverlay && urlSuggestionOverlay.view && !(urlSuggestionOverlay.view.isDestroyed && urlSuggestionOverlay.view.isDestroyed())) return urlSuggestionOverlay;
  const view = new WebContentsView({
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  makeFloatingOverlayViewTransparent(view);
  view.webContents.on('console-message', (_event, _level, message) => {
    const raw = String(message || '');
    if (!raw.startsWith(URL_SUGGESTION_CONSOLE_PREFIX)) return;
    let data = {};
    try { data = JSON.parse(raw.slice(URL_SUGGESTION_CONSOLE_PREFIX.length)); } catch (_) { return; }
    if (mainWindow && mainWindow.webContents && !mainWindow.webContents.isDestroyed()) {
      mainWindow.webContents.send('browser-workbench:url-suggestion-action', {
        action: String(data.action || ''),
        index: Math.max(0, Math.round(Number(data.index) || 0)),
        session_id: urlSuggestionOverlay && urlSuggestionOverlay.sessionId || '',
        tab_id: urlSuggestionOverlay && urlSuggestionOverlay.tabId || '',
      });
    }
  });
  urlSuggestionOverlay = { view, visible: false, sessionId: '', tabId: '', bounds: { x: 0, y: 0, width: 0, height: 0 } };
  return urlSuggestionOverlay;
}

function addUrlSuggestionOverlayToWindow() {
  if (!urlSuggestionOverlay || !urlSuggestionOverlay.view || !mainWindow || !mainWindow.contentView) return;
  try { mainWindow.contentView.addChildView(urlSuggestionOverlay.view); } catch (_) {}
}

function hideUrlSuggestionOverlay() {
  if (!urlSuggestionOverlay || !urlSuggestionOverlay.view) return { ok: true, visible: false };
  urlSuggestionOverlay.visible = false;
  try { urlSuggestionOverlay.view.setVisible(false); } catch (_) {}
  try { urlSuggestionOverlay.view.setBounds({ x: 0, y: 0, width: 0, height: 0 }); } catch (_) {}
  if (mainWindow && mainWindow.contentView) {
    try { mainWindow.contentView.removeChildView(urlSuggestionOverlay.view); } catch (_) {}
  }
  return { ok: true, visible: false };
}

function showUrlSuggestionOverlay(payload) {
  if (!mainWindow || mainWindow.isDestroyed()) return { ok: false, visible: false };
  const items = sanitizeUrlSuggestionItems(payload && payload.items);
  if (!items.length) return hideUrlSuggestionOverlay();
  const anchor = normalizeAnchorRect(payload && payload.anchorRect);
  const requestedActiveIndex = Math.round(Number(payload && payload.activeIndex));
  const activeIndex = Number.isFinite(requestedActiveIndex) && requestedActiveIndex >= 0 && requestedActiveIndex < items.length ? requestedActiveIndex : -1;
  const height = Math.min(340, Math.max(46, items.length * 58 + 14));
  const bounds = {
    x: anchor.x,
    y: anchor.y + anchor.height + 6,
    width: Math.max(160, anchor.width),
    height,
  };
  const overlay = ensureUrlSuggestionOverlayView();
  overlay.visible = true;
  overlay.sessionId = String(payload && (payload.sessionId || payload.session_id) || activeSessionId || '');
  overlay.tabId = String(payload && (payload.tabId || payload.tab_id) || '');
  overlay.bounds = bounds;
  addUrlSuggestionOverlayToWindow();
  overlay.view.setBounds(bounds);
  try { overlay.view.setVisible(true); } catch (_) {}
  const data = { items, activeIndex };
  const updater = `window.__hermesUpdateUrlSuggestions&&window.__hermesUpdateUrlSuggestions(${JSON.stringify(data)})`;
  if (overlay.loaded) overlay.view.webContents.executeJavaScript(updater, true).then(() => resizeUrlSuggestionOverlayToContent(overlay, bounds)).catch(() => {});
  else {
    overlay.view.webContents.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(urlSuggestionOverlayHtml(data))}`).then(() => {
      overlay.loaded = true;
      resizeUrlSuggestionOverlayToContent(overlay, bounds);
    }).catch(() => {});
  }
  addUrlSuggestionOverlayToWindow();
  return { ok: true, visible: true, bounds };
}

function normalizeActionsMenuBounds(raw) {
  const rect = raw && typeof raw === 'object' ? raw : {};
  return {
    x: Math.max(0, Math.round(Number(rect.x ?? rect.left) || 0)),
    y: Math.max(0, Math.round(Number(rect.y ?? rect.top) || 0)),
    width: Math.max(220, Math.round(Number(rect.width) || 280)),
    height: Math.max(180, Math.round(Number(rect.height) || 360)),
  };
}

function actionsMenuOverlayHtml(payload) {
  const zoom = Math.max(25, Math.min(300, Math.round(Number(payload && payload.zoom) || 100)));
  const prefix = ACTIONS_MENU_CONSOLE_PREFIX;
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    :root{color-scheme:dark;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
    html,body{margin:0;padding:0;background:transparent;overflow:hidden;}
    #box{box-sizing:border-box;width:100vw;max-height:100vh;overflow:auto;padding:7px;background:linear-gradient(180deg,rgba(54,38,64,.98),rgba(34,26,44,.98));border:1px solid rgba(255,255,255,.11);border-radius:13px;box-shadow:0 20px 54px rgba(0,0,0,.48),0 0 0 1px rgba(255,255,255,.03) inset;color:#f5f0ff;font-size:13px;backdrop-filter:blur(18px);}
    .section{padding:4px 0;}.section+.section{border-top:1px solid rgba(255,255,255,.08);}.label{padding:5px 10px 4px;color:rgba(245,240,255,.56);font:700 10px/1.2 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:.08em;text-transform:uppercase;}
    button{width:100%;min-height:34px;border:0;background:transparent;color:inherit;text-align:left;border-radius:9px;padding:8px 10px;font:inherit;cursor:pointer;}button:hover,button:focus-visible{background:rgba(255,255,255,.09);outline:none;}button:focus-visible{box-shadow:0 0 0 2px rgba(124,58,237,.55) inset;}
    .item{display:flex;align-items:center;gap:9px;}.icon{flex:0 0 18px;width:18px;height:18px;border-radius:6px;background:rgba(255,255,255,.08);color:rgba(245,240,255,.78);display:inline-flex;align-items:center;justify-content:center;font-size:12px;line-height:1;}.item span:last-child{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
    .zoom-row{display:grid;grid-template-columns:34px minmax(88px,1fr) 34px;align-items:center;gap:8px;padding:4px 10px 7px;}.zoom-btn{width:34px;min-height:34px;height:34px;padding:0;display:inline-flex;align-items:center;justify-content:center;text-align:center;border:1px solid rgba(255,255,255,.08);background:rgba(255,255,255,.05);}.zoom-btn span{font-size:16px;line-height:1;}.zoom-value{height:34px;min-width:0;display:inline-flex;align-items:center;justify-content:center;gap:3px;border:1px solid rgba(255,255,255,.10);border-radius:9px;background:rgba(0,0,0,.16);color:#f5f0ff;font:650 13px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:0 8px;}.zoom-value:focus-within{outline:2px solid rgba(124,58,237,.55);outline-offset:1px;border-color:rgba(124,58,237,.55);}.zoom-value input{width:48px;height:28px;border:0;background:transparent;color:inherit;font:inherit;text-align:right;padding:0;}.zoom-value input:focus{outline:none;}
  </style></head><body><div id="box" role="menu" aria-label="Browser actions">
    <div class="section" role="none"><div class="label">Capture</div><button class="item" type="button" role="menuitem" data-action="take-screenshot"><span class="icon">□</span><span>Take Screenshot</span></button><button class="item" type="button" role="menuitem" data-action="capture-area-screenshot"><span class="icon">⌗</span><span>Capture Area Screenshot</span></button></div>
    <div class="section" role="none"><div class="label">Page</div><button class="item" type="button" role="menuitem" data-action="hard-reload"><span class="icon">↻</span><span>Hard Reload</span></button><button class="item" type="button" role="menuitem" data-action="copy-url"><span class="icon">⛓</span><span>Copy Current URL</span></button><button class="item" type="button" role="menuitem" data-action="open-devtools-panel"><span class="icon">◧</span><span>Open DevTools Panel</span></button><button class="item" type="button" role="menuitem" data-action="open-devtools-popout"><span class="icon">↗</span><span>Pop Out DevTools</span></button></div>
    <div class="section" role="group" aria-label="Browser zoom controls"><div class="label">Zoom</div><div class="zoom-row" role="none"><button class="zoom-btn" type="button" role="menuitem" data-action="zoom-out" aria-label="Zoom out"><span>−</span></button><label class="zoom-value" aria-label="Browser zoom percentage in menu"><input id="zoom" type="text" inputmode="decimal" value="${zoom}" aria-label="Browser zoom percentage value in menu"><span aria-hidden="true">%</span></label><button class="zoom-btn" type="button" role="menuitem" data-action="zoom-in" aria-label="Zoom in"><span>+</span></button></div></div>
    <div class="section" role="none"><div class="label">Data</div><button class="item" type="button" role="menuitem" data-action="clear-history"><span class="icon">◷</span><span>Clear Browsing History</span></button><button class="item" type="button" role="menuitem" data-action="clear-cookies"><span class="icon">●</span><span>Clear Cookies</span></button><button class="item" type="button" role="menuitem" data-action="clear-cache"><span class="icon">◇</span><span>Clear Cache</span></button></div>
  </div><script>
    const prefix=${JSON.stringify(prefix)};
    const zoomInput=document.getElementById('zoom');
    const emit=(action,value)=>console.log(prefix+JSON.stringify({action,value:value===undefined?'':String(value)}));
    document.addEventListener('click',(event)=>{const button=event.target&&event.target.closest?event.target.closest('[data-action]'):null;if(!button)return;event.preventDefault();emit(button.dataset.action);},true);
    if(zoomInput){zoomInput.addEventListener('focus',()=>zoomInput.select());zoomInput.addEventListener('keydown',(event)=>{if(event.key==='Enter'){event.preventDefault();emit('set-zoom',zoomInput.value);zoomInput.blur();}else if(event.key==='Escape'){event.preventDefault();emit('close');}});zoomInput.addEventListener('blur',()=>emit('set-zoom',zoomInput.value));}
    document.addEventListener('keydown',(event)=>{if(event.key==='Escape'){event.preventDefault();emit('close');}},true);
    window.__hermesUpdateActionsMenu=(next)=>{if(!next||typeof next!=='object'||!zoomInput)return;const zoom=String(Math.max(25,Math.min(300,Math.round(Number(next.zoom)||100))));if(document.activeElement!==zoomInput)zoomInput.value=zoom;};
  </script></body></html>`;
}

function ensureActionsMenuOverlayView() {
  assertWebContentsViewAvailable();
  if (actionsMenuOverlay && actionsMenuOverlay.view && !(actionsMenuOverlay.view.isDestroyed && actionsMenuOverlay.view.isDestroyed())) return actionsMenuOverlay;
  const view = new WebContentsView({
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  makeFloatingOverlayViewTransparent(view);
  view.webContents.on('console-message', (_event, _level, message) => {
    const raw = String(message || '');
    if (!raw.startsWith(ACTIONS_MENU_CONSOLE_PREFIX)) return;
    let data = {};
    try { data = JSON.parse(raw.slice(ACTIONS_MENU_CONSOLE_PREFIX.length)); } catch (_) { return; }
    if (mainWindow && mainWindow.webContents && !mainWindow.webContents.isDestroyed()) {
      mainWindow.webContents.send('browser-workbench:actions-menu-action', {
        action: String(data.action || ''),
        value: String(data.value || ''),
        session_id: actionsMenuOverlay && actionsMenuOverlay.sessionId || '',
        tab_id: actionsMenuOverlay && actionsMenuOverlay.tabId || '',
      });
    }
  });
  actionsMenuOverlay = { view, visible: false, loaded: false, sessionId: '', tabId: '', bounds: { x: 0, y: 0, width: 0, height: 0 } };
  return actionsMenuOverlay;
}

function addActionsMenuOverlayToWindow() {
  if (!actionsMenuOverlay || !actionsMenuOverlay.view || !mainWindow || !mainWindow.contentView) return;
  try { mainWindow.contentView.addChildView(actionsMenuOverlay.view); } catch (_) {}
}

function hideActionsMenuOverlay() {
  if (!actionsMenuOverlay || !actionsMenuOverlay.view) return { ok: true, visible: false };
  actionsMenuOverlay.visible = false;
  try { actionsMenuOverlay.view.setVisible(false); } catch (_) {}
  try { actionsMenuOverlay.view.setBounds({ x: 0, y: 0, width: 0, height: 0 }); } catch (_) {}
  if (mainWindow && mainWindow.contentView) {
    try { mainWindow.contentView.removeChildView(actionsMenuOverlay.view); } catch (_) {}
  }
  return { ok: true, visible: false };
}

function showActionsMenuOverlay(payload) {
  if (!mainWindow || mainWindow.isDestroyed()) return { ok: false, visible: false };
  const bounds = normalizeActionsMenuBounds(payload && payload.menuRect);
  const overlay = ensureActionsMenuOverlayView();
  overlay.visible = true;
  overlay.sessionId = String(payload && (payload.sessionId || payload.session_id) || activeSessionId || '');
  overlay.tabId = String(payload && (payload.tabId || payload.tab_id) || '');
  overlay.bounds = bounds;
  addActionsMenuOverlayToWindow();
  overlay.view.setBounds(bounds);
  try { overlay.view.setVisible(true); } catch (_) {}
  const data = { zoom: Math.max(25, Math.min(300, Math.round(Number(payload && payload.zoom) || 100))) };
  const updater = `window.__hermesUpdateActionsMenu&&window.__hermesUpdateActionsMenu(${JSON.stringify(data)})`;
  if (overlay.loaded) overlay.view.webContents.executeJavaScript(updater, true).catch(() => {});
  else {
    overlay.view.webContents.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(actionsMenuOverlayHtml(data))}`).then(() => { overlay.loaded = true; }).catch(() => {});
  }
  addActionsMenuOverlayToWindow();
  return { ok: true, visible: true, bounds };
}

function normalizeDevtoolsMode(value) {
  const mode = String(value || '').trim().toLowerCase();
  if (mode === 'popout' || mode === 'detach' || mode === 'detached') return 'detach';
  return 'right';
}

function recordHasLiveWebContents(record) {
  if (!record || !record.view || !record.view.webContents) return false;
  try {
    if (typeof record.view.isDestroyed === 'function' && record.view.isDestroyed()) return false;
    if (typeof record.view.webContents.isDestroyed === 'function' && record.view.webContents.isDestroyed()) return false;
  } catch (_) { return false; }
  return true;
}

function ensureTab(payload) {
  assertWebContentsViewAvailable();
  const sessionId = String(payload.session_id || payload.sessionId || '').trim();
  if (!sessionId) throw new Error('session_id is required');
  let record = tabs.get(sessionId);
  if (recordHasLiveWebContents(record)) return record;
  if (record) {
    removeNativeViewFromWindow(record);
    tabs.delete(sessionId);
  }
  const view = new WebContentsView({
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      partition: `persist:hermes-browser-workbench-${sessionId}`,
    },
  });
  try { view.setBackgroundColor(STABLE_SURFACE_BACKGROUND); } catch (_) {}
  try { view.webContents.setBackgroundColor(STABLE_SURFACE_BACKGROUND); } catch (_) {}
  view.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const nextUrl = assertSafeBrowserUrl(url);
      record.url = nextUrl;
      markRecordLoading(record, 'window-open-navigation', nextUrl);
      view.webContents.loadURL(nextUrl).catch((err) => {
        setRecordLoadStatus(record, 'error', err && err.message ? err.message : String(err));
      });
    } catch (_) {}
    return { action: 'deny' };
  });
  view.webContents.on('will-navigate', (event, url) => {
    try { assertSafeBrowserUrl(url); }
    catch (_) { event.preventDefault(); }
  });
  record = {
    id: sessionId,
    tabId: String(payload.tab_id || payload.tabId || ''),
    url: normalizeUrl(payload.url),
    title: String(payload.title || ''),
    faviconUrl: safeFaviconUrl(payload.favicon_url || payload.faviconUrl),
    zoom: normalizeZoom(payload.zoom, 1),
    viewport: payload.viewport || {},
    view,
    visible: false,
    focusBeforeApplicationOverlay: false,
    selectionMode: false,
    loadStatus: 'idle',
    loadError: '',
    loadStartedAt: 0,
    loadRequestId: 0,
    loadRequestedUrl: '',
    loadFailed: false,
    navigationError: null,
    documentReadyState: '',
    lastMainFrameReadyAt: 0,
    lastLoadEvent: '',
    loadEvents: [],
  };
  view.webContents.on('console-message', (_event, _level, message) => forwardNativeSelection(record, message));
  view.webContents.on('focus', () => {
    if (mainWindow && mainWindow.webContents && !mainWindow.webContents.isDestroyed()) {
      mainWindow.webContents.send('browser-workbench:native-surface-interaction', { type: 'pointer', session_id: record.id, tab_id: record.tabId || '' });
    }
  });
  view.webContents.on('before-input-event', (_event, input) => {
    if (String(input && input.key || '') !== 'Escape') return;
    if (mainWindow && mainWindow.webContents && !mainWindow.webContents.isDestroyed()) {
      mainWindow.webContents.send('browser-workbench:native-surface-interaction', { type: 'escape', session_id: record.id, tab_id: record.tabId || '' });
    }
  });
  view.webContents.on('page-title-updated', (_event, title) => { record.title = title || ''; sendBrowserNavigationUpdate(record, 'page-title-updated'); });
  view.webContents.on('page-favicon-updated', (_event, favicons) => { record.faviconUrl = firstFaviconUrl(favicons) || record.faviconUrl || ''; sendBrowserNavigationUpdate(record, 'page-favicon-updated'); });
  view.webContents.on('did-start-navigation', (_event, url, isInPlace, isMainFrame) => {
    rememberRecordLoadEvent(record, 'did-start-navigation', { url, isInPlace, isMainFrame });
    if (isMainFrame !== false && isInPlace !== true && isPublicBrowserNavigationUrl(url)) {
      record.url = url || record.url;
      markRecordLoading(record, 'did-start-navigation', url || record.url);
      sendBrowserNavigationUpdate(record, 'did-start-navigation');
    }
  });
  view.webContents.on('did-navigate', (_event, url) => {
    if (isPublicBrowserNavigationUrl(url)) record.url = url || record.url;
    rememberRecordLoadEvent(record, 'did-navigate', { url: record.url });
    sendBrowserNavigationUpdate(record, 'did-navigate');
    scheduleRecordReady(record, 'did-navigate-settled', LOAD_STATUS_MAIN_FRAME_SETTLE_MS * 3);
    readRecordDocumentReadyState(record, 'did-navigate');
  });
  view.webContents.on('did-navigate-in-page', (_event, url) => {
    record.url = url || record.url;
    rememberRecordLoadEvent(record, 'did-navigate-in-page', { url: record.url });
    sendBrowserNavigationUpdate(record, 'did-navigate-in-page');
  });
  view.webContents.on('did-start-loading', () => { rememberRecordLoadEvent(record, 'did-start-loading'); });
  view.webContents.on('did-stop-loading', () => {
    rememberRecordLoadEvent(record, 'did-stop-loading');
    if (!markRecordReadyIfMainFrameSettled(record, 'did-stop-loading')) refreshRecordLoadStatus(record);
  });
  view.webContents.on('dom-ready', () => {
    rememberRecordLoadEvent(record, 'dom-ready');
    readRecordDocumentReadyState(record, 'dom-ready');
    scheduleRecordReady(record, 'dom-ready-settled', LOAD_STATUS_MAIN_FRAME_SETTLE_MS);
  });
  view.webContents.on('did-finish-load', () => {
    markRecordReady(record, 'did-finish-load');
    if (record.selectionMode) record.view.webContents.executeJavaScript(nativeSelectionScript(record.id, true), true).catch(() => {});
  });
  view.webContents.on('did-frame-finish-load', (_event, isMainFrame) => {
    rememberRecordLoadEvent(record, 'did-frame-finish-load', { isMainFrame });
    if (isMainFrame !== false) markRecordReady(record, 'did-frame-finish-load');
  });
  view.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    handleRecordMainFrameFailure(record, errorCode, errorDescription, validatedURL, isMainFrame, 'did-fail-load');
  });
  view.webContents.on('did-fail-provisional-load', (_event, errorCode, errorDescription, validatedURL, isMainFrame) => {
    handleRecordMainFrameFailure(record, errorCode, errorDescription, validatedURL, isMainFrame, 'did-fail-provisional-load');
  });
  tabs.set(sessionId, record);
  if (mainWindow && mainWindow.contentView) mainWindow.contentView.addChildView(view);
  return record;
}

function startRecordUrlLoad(record, url, reason) {
  const nextUrl = assertSafeBrowserUrl(url);
  record.url = nextUrl;
  markRecordLoading(record, reason || 'load-url', nextUrl);
  const requestId = record.loadRequestId;
  record.view.webContents.loadURL(nextUrl).catch((err) => {
    setTimeout(() => {
      if (!record || requestId !== record.loadRequestId || record.navigationError) return;
      const errorCode = Number(err && (err.errno !== undefined ? err.errno : err.code)) || 0;
      const description = String(err && (err.code || err.message) || 'Load failed');
      if (isExpectedNavigationAbort(errorCode, description)) return;
      handleRecordMainFrameFailure(record, errorCode, description, nextUrl, true, 'load-url-rejected');
    }, 0);
  });
}

function reloadRecord(record) {
  const wc = record.view.webContents;
  const savedUrl = assertSafeBrowserUrl(record.navigationError && record.navigationError.validated_url || record.url || wc.getURL());
  const liveUrl = String(wc.getURL() || '');
  record.url = savedUrl;
  if (/^https?:/i.test(liveUrl)) {
    markRecordLoading(record, 'reload', savedUrl);
    wc.reload();
  } else {
    startRecordUrlLoad(record, savedUrl, 'reload-load-url');
  }
}

async function loadRecord(record, payload) {
  const data = payload && typeof payload === 'object' ? payload : { url: payload };
  hideUrlSuggestionOverlay();
  applyRecordPayload(record, data);
  const nextUrl = assertSafeBrowserUrl(data.url || record.url);
  record.url = nextUrl;
  if (nextUrl === 'about:blank') {
    record.navigationError = null;
    setRecordLoadStatus(record, 'idle');
  } else {
    startRecordUrlLoad(record, nextUrl, 'load-url');
  }
  return publicTabState(record, 'Browser is ready.', { refreshLoadStatus: false });
}

function publicTabState(record, message, options) {
  const opts = options && typeof options === 'object' ? options : {};
  if (opts.refreshLoadStatus !== false) refreshRecordLoadStatus(record);
  if (record.loadStatus === 'loading') readRecordDocumentReadyState(record, 'public-tab-state');
  const wc = record.view.webContents;
  const publicUrl = record.navigationError && record.navigationError.validated_url || record.url || wc.getURL();
  return {
    ok: true,
    session_id: record.id,
    status: 'ready',
    renderer: 'electron-native',
    url: publicUrl,
    title: record.loadStatus === 'loading' ? '' : wc.getTitle() || record.title,
    favicon_url: record.faviconUrl || '',
    can_go_back: wc.navigationHistory && typeof wc.navigationHistory.canGoBack === 'function' ? wc.navigationHistory.canGoBack() : wc.canGoBack(),
    can_go_forward: wc.navigationHistory && typeof wc.navigationHistory.canGoForward === 'function' ? wc.navigationHistory.canGoForward() : wc.canGoForward(),
    viewport: record.viewport,
    zoom: record.zoom,
    load_status: record.loadStatus || 'idle',
    load_error: record.loadError || '',
    navigation_error: record.navigationError ? { ...record.navigationError } : null,
    message: message || 'Browser is ready.',
  };
}

function sendBrowserNavigationUpdate(record, reason) {
  if (!record || !mainWindow || !mainWindow.webContents || mainWindow.webContents.isDestroyed()) return;
  try {
    mainWindow.webContents.send('browser-workbench:navigation', Object.assign(
      publicTabState(record, 'Page updated.', { refreshLoadStatus: false }),
      { reason: String(reason || 'navigation'), tab_id: record.tabId || '' }
    ));
  } catch (_) {}
}

function nativeSelectionScript(sessionId, enabled) {
  return `(() => {
    const prefix = ${JSON.stringify(NATIVE_SELECTION_CONSOLE_PREFIX)};
    const sessionId = ${JSON.stringify(sessionId)};
    const key = '__hermesBrowserWorkbenchNativeSelection';
    const clip = (value, max = 500) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, max);
    const esc = (value) => {
      try { if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(String(value)); } catch (_) {}
      return String(value).replace(/[^a-zA-Z0-9_-]/g, (ch) => '\\\\' + ch);
    };
    const selectorFor = (node) => {
      if (!node || node.nodeType !== 1) return 'unavailable';
      if (node.id) return '#' + esc(node.id);
      const named = ['data-testid','data-test','aria-label','name','role'].map((name) => {
        const value = node.getAttribute(name);
        return value ? node.localName + '[' + name + '=\"' + esc(value) + '\"]' : '';
      }).find(Boolean);
      if (named) return named;
      const parts = [];
      let current = node;
      while (current && current.nodeType === 1 && parts.length < 4) {
        let part = current.localName || 'element';
        if (current.classList && current.classList.length) part += '.' + Array.from(current.classList).slice(0, 2).map(esc).join('.');
        const parent = current.parentElement;
        if (parent) {
          const siblings = Array.from(parent.children).filter((child) => child.localName === current.localName);
          if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')';
        }
        parts.unshift(part);
        current = parent;
      }
      return parts.join(' > ');
    };
    const reactInfo = (node) => {
      let current = node;
      while (current) {
        const prop = Object.keys(current).find((name) => name.startsWith('__reactFiber$') || name.startsWith('__reactInternalInstance$'));
        let fiber = prop ? current[prop] : null;
        while (fiber) {
          const type = fiber.elementType || fiber.type || {};
          const name = typeof type === 'function' ? type.displayName || type.name : type.displayName || type.name || '';
          const source = fiber._debugSource ? [fiber._debugSource.fileName, fiber._debugSource.lineNumber, fiber._debugSource.columnNumber].filter(Boolean).join(':') : '';
          if (name || source) return { component: name || 'unknown', source: source || 'unknown' };
          fiber = fiber.return;
        }
        current = current.parentElement;
      }
      return { component: 'unknown', source: 'unknown' };
    };
    const frameMetaFor = (frame, sameOrigin) => ({ selector: selectorFor(frame), src: clip(frame.getAttribute('src') || frame.src || 'about:blank', 512), sameOrigin: sameOrigin === true });
    const addRectOffset = (rect, left, top) => ({
      x: Number(rect && rect.x || 0) + left,
      y: Number(rect && rect.y || 0) + top,
      top: Number(rect && (rect.top ?? rect.y) || 0) + top,
      left: Number(rect && (rect.left ?? rect.x) || 0) + left,
      width: Number(rect && rect.width || 0),
      height: Number(rect && rect.height || 0),
    });
    const topPointForEvent = (event) => {
      let x = Number(event && event.clientX || 0);
      let y = Number(event && event.clientY || 0);
      let doc = event && event.currentTarget && event.currentTarget.nodeType === 9 ? event.currentTarget : event && event.target && event.target.ownerDocument || document;
      let win = doc && doc.defaultView;
      while (win && win !== window) {
        const frame = win.frameElement;
        if (!frame) break;
        const rect = frame.getBoundingClientRect();
        x += Number(rect.left || 0);
        y += Number(rect.top || 0);
        win = win.parent;
      }
      return { x, y };
    };
    const elementSelection = (el, doc, x, y, topPoint, frames) => {
      const rect = el.getBoundingClientRect();
      const info = reactInfo(el);
      const attrs = {};
      for (const name of ['id','class','role','aria-label','data-testid','data-test','name','type','href']) {
        const value = el.getAttribute(name);
        if (value) attrs[name] = clip(value, 240);
      }
      const frameList = Array.isArray(frames) ? frames.filter(Boolean) : [];
      const ownRect = { x: rect.x, y: rect.y, top: rect.top, left: rect.left, width: rect.width, height: rect.height };
      return { type: 'browser_element', selector: selectorFor(el), tag: (el.localName || el.tagName || '').toLowerCase(), text: clip(el.innerText || el.textContent || el.getAttribute('aria-label') || '', 500), component: info.component, source: info.source, attributes: attrs, rect: ownRect, point: { x: topPoint.x, y: topPoint.y }, url: doc && doc.location ? doc.location.href : location.href, session_id: sessionId, frame: frameList.length ? frameList[frameList.length - 1] : null, frames: frameList.length ? frameList : null };
    };
    const inspectInDocument = (doc, x, y, topPoint, frames, depth) => {
      if (!doc || depth > 5) return null;
      const el = doc.elementFromPoint(x, y);
      if (!el) return { type: 'browser_element', selector: 'unavailable (no element at point)', component: 'unknown', source: 'electron-native', url: doc.location ? doc.location.href : location.href, session_id: sessionId, point: { x: topPoint.x, y: topPoint.y } };
      if ((el.localName || '').toLowerCase() === 'iframe') {
        const iframeRect = el.getBoundingClientRect();
        const childX = x - iframeRect.left;
        const childY = y - iframeRect.top;
        let frameMeta = frameMetaFor(el, false);
        try {
          const childDoc = el.contentDocument || el.contentWindow && el.contentWindow.document;
          if (childDoc && childDoc.documentElement) {
            frameMeta = frameMetaFor(el, true);
            const nested = inspectInDocument(childDoc, childX, childY, topPoint, [...(frames || []), frameMeta], depth + 1);
            if (nested) {
              nested.rect = addRectOffset(nested.rect, iframeRect.left, iframeRect.top);
              return nested;
            }
          }
        } catch (err) {
          try { console.debug('Hermes Browser Workbench: iframe content cannot be inspected due to browser security.', err && err.message ? err.message : err); } catch (_) {}
        }
        const fallback = elementSelection(el, doc, x, y, topPoint, frames || []);
        fallback.component = 'iframe · cross-origin';
        fallback.tag = '';
        fallback.source = 'Cross-origin iframe content cannot be inspected due to browser security.';
        fallback.text = 'iframe content cannot be inspected due to browser security';
        fallback.frame = frameMeta;
        fallback.frames = [...(frames || []), frameMeta];
        return fallback;
      }
      return elementSelection(el, doc, x, y, topPoint, frames || []);
    };
    const inspect = (x, y) => {
      const state = window[key];
      const overlay = state && state.overlay;
      const previousPointerEvents = overlay ? overlay.style.pointerEvents : '';
      if (overlay) overlay.style.pointerEvents = 'none';
      const selection = inspectInDocument(document, x, y, { x, y }, [], 0) || { type: 'browser_element', selector: 'unavailable (no element at point)', component: 'unknown', source: 'electron-native', url: location.href, session_id: sessionId, point: { x, y } };
      if (overlay) overlay.style.pointerEvents = previousPointerEvents;
      return selection;
    };
    const elementLabel = (selection) => {
      const component = clip(selection && selection.component, 80);
      const safeComponent = component && component.toLowerCase() !== 'unknown' ? component : '';
      const rawTag = clip(selection && (selection.tag || selection.tagName || selection.htmlTag || selection.nodeName), 64).toLowerCase();
      const tag = rawTag && rawTag !== 'unknown' ? rawTag : '';
      const fallback = clip(selection && (selection.selector || selection.url || 'Browser element'), 80);
      if (safeComponent && tag) return (safeComponent + ' • ' + tag).slice(0, 96);
      return (safeComponent || tag || fallback || 'Browser element').slice(0, 96);
    };
    const renderElementLabel = (target, selection) => {
      if (!target) return;
      const component = clip(selection && selection.component, 80);
      const safeComponent = component && component.toLowerCase() !== 'unknown' ? component : '';
      const rawTag = clip(selection && (selection.tag || selection.tagName || selection.htmlTag || selection.nodeName), 64).toLowerCase();
      const tag = rawTag && rawTag !== 'unknown' ? rawTag : '';
      const label = elementLabel(selection);
      target.title = label;
      target.replaceChildren();
      if (!safeComponent || !tag) {
        target.textContent = label;
        return;
      }
      const componentPart = document.createElement('span');
      componentPart.textContent = safeComponent;
      componentPart.style.cssText = 'display:inline-block;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
      const separatorPart = document.createElement('span');
      separatorPart.textContent = '•';
      separatorPart.style.cssText = 'flex:0 0 auto;opacity:.82;';
      const tagPart = document.createElement('span');
      tagPart.textContent = tag;
      tagPart.style.cssText = 'flex:0 0 auto;white-space:nowrap;';
      target.append(componentPart, separatorPart, tagPart);
    };
    const emit = (action, selection) => { try { console.log(prefix + JSON.stringify({ action, session_id: sessionId, selection })); } catch (_) {} };
    const stop = (event) => {
      if (!event) return;
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
    };
    const withOverlayHidden = (callback) => {
      const state = window[key];
      const overlay = state && state.overlay;
      const previousPointerEvents = overlay ? overlay.style.pointerEvents : '';
      if (overlay) overlay.style.pointerEvents = 'none';
      try { return callback(); }
      finally { if (overlay) overlay.style.pointerEvents = previousPointerEvents; }
    };
    const scrollTargetFor = (doc, x, y, depth) => {
      if (!doc || depth > 5) return null;
      const el = doc.elementFromPoint(x, y);
      if (el && (el.localName || '').toLowerCase() === 'iframe') {
        const rect = el.getBoundingClientRect();
        try {
          const childDoc = el.contentDocument || el.contentWindow && el.contentWindow.document;
          if (childDoc && childDoc.documentElement) {
            const nested = scrollTargetFor(childDoc, x - rect.left, y - rect.top, depth + 1);
            if (nested) return nested;
          }
        } catch (_) {}
      }
      let current = el && el.nodeType === 1 ? el : null;
      while (current && current !== doc.body && current !== doc.documentElement) {
        const style = doc.defaultView && doc.defaultView.getComputedStyle ? doc.defaultView.getComputedStyle(current) : null;
        const overflowY = style ? String(style.overflowY || style.overflow || '') : '';
        const overflowX = style ? String(style.overflowX || style.overflow || '') : '';
        const scrollableY = /(auto|scroll|overlay)/.test(overflowY) && current.scrollHeight > current.clientHeight;
        const scrollableX = /(auto|scroll|overlay)/.test(overflowX) && current.scrollWidth > current.clientWidth;
        if (scrollableY || scrollableX) return current;
        current = current.parentElement;
      }
      return doc.scrollingElement || doc.documentElement || doc.body || null;
    };
    const normalizedWheelDelta = (event, target) => {
      const lineHeight = 16;
      const pageHeight = target && target.clientHeight || window.innerHeight || 800;
      const scale = event && event.deltaMode === 1 ? lineHeight : event && event.deltaMode === 2 ? pageHeight : 1;
      return { x: Number(event && event.deltaX || 0) * scale, y: Number(event && event.deltaY || 0) * scale };
    };
    const clampOverlayValue = (value, min, max) => {
      const floor = Number(min) || 0;
      const ceiling = Number(max);
      if (!Number.isFinite(ceiling) || ceiling < floor) return floor;
      return Math.max(floor, Math.min(ceiling, Number(value) || 0));
    };
    const positionSelectionLabel = (label, targetRect) => {
      if (!label || !targetRect) return;
      const safe = 8;
      const gap = 6;
      const containerWidth = Math.max(0, window.innerWidth || document.documentElement.clientWidth || 0);
      const containerHeight = Math.max(0, window.innerHeight || document.documentElement.clientHeight || 0);
      label.style.maxWidth = Math.max(40, containerWidth - (safe * 2)) + 'px';
      label.style.visibility = 'hidden';
      label.style.display = 'inline-flex';
      const measured = label.getBoundingClientRect ? label.getBoundingClientRect() : { width: 0, height: 0 };
      const labelWidth = Math.max(1, Math.ceil(measured.width || label.offsetWidth || 0));
      const labelHeight = Math.max(1, Math.ceil(measured.height || label.offsetHeight || 0));
      const targetLeft = Math.max(0, Number(targetRect.left ?? targetRect.x ?? 0));
      const targetTop = Math.max(0, Number(targetRect.top ?? targetRect.y ?? 0));
      const targetBottom = targetTop + Math.max(0, Number(targetRect.height || 0));
      const aboveTop = targetTop - labelHeight - gap;
      const belowTop = targetBottom + gap;
      let top = aboveTop;
      let placement = 'above';
      if (top < safe) {
        top = belowTop;
        placement = 'below';
      }
      if (top + labelHeight > containerHeight - safe) {
        const clampedAbove = Math.max(safe, aboveTop);
        if (aboveTop >= safe || clampedAbove + labelHeight <= containerHeight - safe) {
          top = clampedAbove;
          placement = 'above';
        } else {
          top = clampOverlayValue(top, safe, containerHeight - labelHeight - safe);
          placement = 'clamped';
        }
      }
      top = clampOverlayValue(top, safe, containerHeight - labelHeight - safe);
      const left = clampOverlayValue(targetLeft, safe, containerWidth - labelWidth - safe);
      label.style.left = left + 'px';
      label.style.top = top + 'px';
      label.style.visibility = 'visible';
      label.dataset.placement = placement;
    };
    const renderHighlight = (selection) => {
      const state = window[key];
      if (!state || !state.box || !selection || !selection.rect) return;
      const rect = selection.rect;
      state.box.style.left = Math.max(0, Number(rect.left ?? rect.x ?? 0)) + 'px';
      state.box.style.top = Math.max(0, Number(rect.top ?? rect.y ?? 0)) + 'px';
      state.box.style.width = Math.max(8, Number(rect.width || 18)) + 'px';
      state.box.style.height = Math.max(8, Number(rect.height || 18)) + 'px';
      renderElementLabel(state.label, selection);
      state.box.style.display = 'block';
      state.label.style.display = 'inline-flex';
      positionSelectionLabel(state.label, rect);
    };
    const dispose = () => {
      const state = window[key];
      if (state) {
        if (Array.isArray(state.frameCleanups)) state.frameCleanups.forEach((cleanup) => { try { cleanup(); } catch (_) {} });
        state.blockEvents.forEach((name) => document.removeEventListener(name, state.block, true));
        if (state.overlay && state.overlay.parentNode) state.overlay.parentNode.removeChild(state.overlay);
        window.removeEventListener('scroll', state.scroll, true);
        document.removeEventListener('keydown', state.keydown, true);
        document.documentElement.style.cursor = state.cursor || '';
      }
      delete window[key];
    };
    dispose();
    if (${JSON.stringify(enabled === true)} !== true) return { ok: true, selecting: false };
    let raf = 0;
    let lastPoint = null;
    let lastFrameListenerScan = 0;
    const overlay = document.createElement('div');
    overlay.setAttribute('data-hermes-browser-workbench-selection-overlay', 'true');
    overlay.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:transparent;cursor:crosshair;pointer-events:auto;user-select:none;touch-action:none;';
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;display:none;box-sizing:border-box;border:2px solid #7c3aed;background:rgba(124,58,237,.12);box-shadow:0 0 0 1px rgba(255,255,255,.85),0 10px 30px rgba(0,0,0,.28);border-radius:6px;pointer-events:none;z-index:2147483647;';
    const label = document.createElement('span');
    label.style.cssText = 'position:fixed;left:8px;top:8px;display:inline-flex;align-items:center;gap:4px;visibility:hidden;box-sizing:border-box;max-width:min(360px,calc(100vw - 16px));overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:3px 7px;border-radius:999px;background:#7c3aed;color:#fff;font:600 12px/16px -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;box-shadow:0 6px 20px rgba(0,0,0,.25);pointer-events:none;z-index:2147483647;';
    overlay.appendChild(box);
    overlay.appendChild(label);
    (document.body || document.documentElement).appendChild(overlay);
    const state = {
      cursor: document.documentElement.style.cursor || '',
      overlay,
      box,
      label,
      frameCleanups: [],
      attachFrameListeners() {
        if (Array.isArray(state.frameCleanups)) state.frameCleanups.forEach((cleanup) => { try { cleanup(); } catch (_) {} });
        state.frameCleanups = [];
        const seen = [];
        const attach = (doc) => {
          if (!doc || seen.includes(doc)) return;
          seen.push(doc);
          if (doc !== document) {
            for (const name of ['pointermove','mousemove']) {
              doc.addEventListener(name, state.mousemove, true);
              state.frameCleanups.push(() => doc.removeEventListener(name, state.mousemove, true));
            }
            doc.addEventListener('pointerdown', state.pointerdown, true);
            state.frameCleanups.push(() => doc.removeEventListener('pointerdown', state.pointerdown, true));
            state.blockEvents.filter((name) => name !== 'pointerdown').forEach((name) => {
              doc.addEventListener(name, state.block, true);
              state.frameCleanups.push(() => doc.removeEventListener(name, state.block, true));
            });
            doc.addEventListener('keydown', state.keydown, true);
            state.frameCleanups.push(() => doc.removeEventListener('keydown', state.keydown, true));
            const previousCursor = doc.documentElement && doc.documentElement.style ? doc.documentElement.style.cursor || '' : '';
            try { if (doc.documentElement) doc.documentElement.style.cursor = 'crosshair'; } catch (_) {}
            state.frameCleanups.push(() => { try { if (doc.documentElement) doc.documentElement.style.cursor = previousCursor; } catch (_) {} });
          }
          Array.from(doc.querySelectorAll ? doc.querySelectorAll('iframe') : []).forEach((frame) => {
            try {
              const childDoc = frame.contentDocument || frame.contentWindow && frame.contentWindow.document;
              if (childDoc && childDoc.documentElement) attach(childDoc);
            } catch (err) {
              try { console.debug('Hermes Browser Workbench: iframe content cannot be inspected due to browser security.', err && err.message ? err.message : err); } catch (_) {}
            }
          });
        };
        attach(document);
      },
      blockEvents: ['pointerdown','pointerup','mousedown','mouseup','click','dblclick','auxclick','contextmenu','touchstart','touchend'],
      scheduleHover(point) {
        if (!point) return;
        lastPoint = { x: Number(point.x || 0), y: Number(point.y || 0) };
        if (raf) return;
        raf = requestAnimationFrame(() => {
          raf = 0;
          if (!lastPoint) return;
          const selection = inspect(lastPoint.x, lastPoint.y);
          renderHighlight(selection);
          emit('hover', selection);
        });
      },
      block(event) {
        const isOverlayEvent = event && overlay && (event.target === overlay || overlay.contains(event.target));
        if (isOverlayEvent && (event.type === 'pointermove' || event.type === 'mousemove')) return;
        if (isOverlayEvent && event.type === 'pointerdown') {
          state.pointerdown(event);
          return;
        }
        stop(event);
      },
      mousemove(event) {
        stop(event);
        const now = Date.now();
        if (now - lastFrameListenerScan > 1000) {
          lastFrameListenerScan = now;
          state.attachFrameListeners();
        }
        state.scheduleHover(topPointForEvent(event));
      },
      scroll() {
        if (lastPoint) state.scheduleHover(lastPoint);
      },
      wheel(event) {
        const point = topPointForEvent(event);
        const target = withOverlayHidden(() => scrollTargetFor(document, point.x, point.y, 0));
        if (target) {
          const delta = normalizedWheelDelta(event, target);
          try {
            if (typeof target.scrollBy === 'function') target.scrollBy({ left: delta.x, top: delta.y, behavior: 'auto' });
            else { target.scrollLeft += delta.x; target.scrollTop += delta.y; }
          } catch (_) {
            try { target.scrollLeft += delta.x; target.scrollTop += delta.y; } catch (_) {}
          }
        }
        stop(event);
        state.scheduleHover(point);
        setTimeout(() => state.scheduleHover(point), 80);
      },
      pointerdown(event) {
        const point = topPointForEvent(event);
        const selection = inspect(point.x, point.y);
        renderHighlight(selection);
        stop(event);
        emit('select', selection);
      },
      keydown(event) {
        if (event.key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          emit('cancel', { type: 'browser_element', session_id: sessionId, url: location.href });
        }
      },
    };
    window[key] = state;
    state.blockEvents.forEach((name) => document.addEventListener(name, state.block, true));
    overlay.addEventListener('pointermove', state.mousemove, true);
    overlay.addEventListener('mousemove', state.mousemove, true);
    overlay.addEventListener('wheel', state.wheel, { capture: true, passive: false });
    overlay.addEventListener('pointerdown', state.pointerdown, true);
    window.addEventListener('scroll', state.scroll, { capture: true, passive: true });
    document.addEventListener('keydown', state.keydown, true);
    document.documentElement.style.cursor = 'crosshair';
    state.attachFrameListeners();
    return { ok: true, selecting: true };
  })()`;
}

function forwardNativeSelection(record, message) {
  const raw = String(message || '');
  if (!raw.startsWith(NATIVE_SELECTION_CONSOLE_PREFIX)) return;
  let payload = {};
  try { payload = JSON.parse(raw.slice(NATIVE_SELECTION_CONSOLE_PREFIX.length)); }
  catch (_) { return; }
  const selection = payload.selection && typeof payload.selection === 'object' ? payload.selection : {};
  if (mainWindow && mainWindow.webContents && !mainWindow.webContents.isDestroyed()) {
    mainWindow.webContents.send('browser-workbench:native-selection', {
      action: String(payload.action || 'select'),
      session_id: record.id,
      tab_id: record.tabId || '',
      selection: { ...selection, session_id: record.id, url: selection.url || record.url || '' },
    });
  }
}

function setNativeSelectionMode(record, enabled) {
  if (!record || !record.view || record.view.isDestroyed && record.view.isDestroyed()) return;
  const selecting = enabled === true;
  if (record.selectionMode === selecting) return;
  record.selectionMode = selecting;
  record.view.webContents.executeJavaScript(nativeSelectionScript(record.id, selecting), true).catch(() => {});
}

function removeNativeViewFromWindow(record) {
  if (!record || !record.view || !mainWindow || !mainWindow.contentView) return;
  try { mainWindow.contentView.removeChildView(record.view); } catch (_) {}
}

function addNativeViewToWindow(record) {
  if (!record || !record.view || !mainWindow || !mainWindow.contentView) return;
  try { mainWindow.contentView.addChildView(record.view); } catch (_) {}
}

function hideRecord(record) {
  if (!record || !record.view || record.view.isDestroyed && record.view.isDestroyed()) return;
  if (urlSuggestionOverlay && urlSuggestionOverlay.sessionId === record.id) hideUrlSuggestionOverlay();
  setNativeSelectionMode(record, false);
  record.visible = false;
  try { record.view.setVisible(false); } catch (_) {}
  record.view.setBounds({ x: 0, y: 0, width: 0, height: 0 });
  // Keep the WebContentsView attached while it is merely backgrounded. Removing
  // and re-adding a native child view during Chat↔Browser tab switches can expose
  // Chromium's default white surface for a frame in Electron. Close paths still
  // call removeNativeViewFromWindow(record) explicitly after hideRecord().
}

function applyApplicationOverlaySuppressionToRecord(record, suppressed, wasSuppressed) {
  if (!record || !record.view || record.view.isDestroyed && record.view.isDestroyed()) return;
  const wc = record.view.webContents;
  if (suppressed && !wasSuppressed) {
    try { record.focusBeforeApplicationOverlay = !!(wc && wc.isFocused && wc.isFocused()); }
    catch (_) { record.focusBeforeApplicationOverlay = false; }
  }
  if (record.visible) {
    // This is deliberately visibility-only. The native view stays attached at
    // its exact bounds, so DOM placeholders and browser layout never reflow.
    try { record.view.setVisible(!suppressed); } catch (_) {}
  }
  if (!suppressed && wasSuppressed) {
    const shouldRestoreFocus = record.visible && record.focusBeforeApplicationOverlay === true;
    record.focusBeforeApplicationOverlay = false;
    if (shouldRestoreFocus && wc && !(wc.isDestroyed && wc.isDestroyed())) {
      setTimeout(() => { try { wc.focus(); } catch (_) {} }, 0);
    }
  }
}

function setApplicationOverlaySuppression(payload) {
  const rawGeneration = Number(payload && payload.generation);
  const generation = Number.isFinite(rawGeneration) && rawGeneration >= 0
    ? rawGeneration
    : applicationOverlaySuppression.generation + 1;
  if (generation < applicationOverlaySuppression.generation) {
    return { ok: true, ignored: true, ...applicationOverlaySuppression };
  }
  const wasSuppressed = applicationOverlaySuppression.suppressed === true;
  const suppressed = payload && payload.suppressed === true;
  applicationOverlaySuppression = {
    suppressed,
    generation,
    overlayCount: Math.max(0, Math.round(Number(payload && payload.overlayCount) || 0)),
  };
  if (suppressed !== wasSuppressed) {
    for (const record of tabs.values()) {
      applyApplicationOverlaySuppressionToRecord(record, suppressed, wasSuppressed);
    }
  }
  return { ok: true, ...applicationOverlaySuppression };
}

function resetApplicationOverlaySuppression() {
  const wasSuppressed = applicationOverlaySuppression.suppressed === true;
  applicationOverlaySuppression = { suppressed: false, generation: 0, overlayCount: 0 };
  if (wasSuppressed) {
    for (const record of tabs.values()) {
      applyApplicationOverlaySuppressionToRecord(record, false, true);
    }
  }
}

function setNativeBounds(payload) {
  if (payload && payload.applicationOverlay && typeof payload.applicationOverlay === 'object') {
    setApplicationOverlaySuppression(payload.applicationOverlay);
  }
  const sessionId = String(payload && (payload.sessionId || payload.session_id) || '').trim();
  const visible = !!(payload && payload.visible && sessionId && tabs.has(sessionId));
  for (const [id, record] of tabs) {
    if (!visible || id !== sessionId) hideRecord(record);
  }
  if (!visible) {
    activeSessionId = '';
    return { ok: true, visible: false };
  }
  const record = tabs.get(sessionId);
  const bounds = payload.bounds || {};
  const nextBounds = {
    x: Math.max(0, Math.round(Number(bounds.x) || 0)),
    y: Math.max(0, Math.round(Number(bounds.y) || 0)),
    width: Math.max(1, Math.round(Number(bounds.width) || 1)),
    height: Math.max(1, Math.round(Number(bounds.height) || 1)),
  };
  record.visible = true;
  record.tabId = String(payload.tabId || payload.tab_id || record.tabId || '');
  record.viewport = {
    width: nextBounds.width,
    height: nextBounds.height,
    device_pixel_ratio: Math.max(0.5, Math.min(4, Number(payload.devicePixelRatio) || 1)),
  };
  record.zoom = Math.max(0.25, Math.min(3, Number(payload.zoom) || record.zoom || 1));
  activeSessionId = sessionId;
  addNativeViewToWindow(record);
  record.view.setBounds(nextBounds);
  try {
    if (applicationOverlaySuppression.suppressed === true) record.view.setVisible(false);
    else record.view.setVisible(true);
  } catch (_) {}
  if (urlSuggestionOverlay && urlSuggestionOverlay.visible) addUrlSuggestionOverlayToWindow();
  if (actionsMenuOverlay && actionsMenuOverlay.visible) addActionsMenuOverlayToWindow();
  record.view.webContents.setZoomFactor(record.zoom);
  setNativeSelectionMode(record, payload.selectionMode === true);
  return { ok: true, visible: true, session_id: sessionId, bounds: nextBounds };
}

function closeTab(sessionId) {
  const record = tabs.get(sessionId);
  if (!record) return { ok: true, status: 'closed' };
  hideRecord(record);
  removeNativeViewFromWindow(record);
  if (!record.view.webContents.isDestroyed()) record.view.webContents.close({ waitForBeforeUnload: false });
  tabs.delete(sessionId);
  if (activeSessionId === sessionId) activeSessionId = '';
  return { ok: true, status: 'closed', session_id: sessionId };
}

function closeAllNativeTabs() {
  for (const id of Array.from(tabs.keys())) closeTab(id);
}

async function inspectAt(record, payload) {
  const x = Math.max(0, Number(payload.x) || 0);
  const y = Math.max(0, Number(payload.y) || 0);
  const script = `(() => {
    const pointX = ${JSON.stringify(x)};
    const pointY = ${JSON.stringify(y)};
    const el = document.elementFromPoint(pointX, pointY);
    const clip = (value, max = 500) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, max);
    const selectorFor = (node) => {
      if (!node || node.nodeType !== 1) return 'unavailable';
      if (node.id) return '#' + CSS.escape(node.id);
      const test = ['data-testid','data-test','aria-label','name','role'].map((name) => {
        const value = node.getAttribute(name);
        return value ? '[' + name + '="' + CSS.escape(value) + '"]' : '';
      }).find(Boolean);
      if (test) return node.localName + test;
      const parts = [];
      let current = node;
      while (current && current.nodeType === 1 && parts.length < 4) {
        let part = current.localName;
        if (current.classList && current.classList.length) part += '.' + Array.from(current.classList).slice(0, 2).map((c) => CSS.escape(c)).join('.');
        const parent = current.parentElement;
        if (parent) {
          const siblings = Array.from(parent.children).filter((child) => child.localName === current.localName);
          if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')';
        }
        parts.unshift(part);
        current = parent;
      }
      return parts.join(' > ');
    };
    const reactFiberFor = (node) => {
      let current = node;
      while (current) {
        const key = Object.keys(current).find((name) => name.startsWith('__reactFiber$') || name.startsWith('__reactInternalInstance$'));
        let fiber = key ? current[key] : null;
        while (fiber) {
          const type = fiber.elementType || fiber.type || {};
          const name = typeof type === 'function' ? type.displayName || type.name : type.displayName || type.name || '';
          const source = fiber._debugSource ? [fiber._debugSource.fileName, fiber._debugSource.lineNumber, fiber._debugSource.columnNumber].filter(Boolean).join(':') : '';
          if (name || source) return { component: name || 'unknown', source: source || 'unknown' };
          fiber = fiber.return;
        }
        current = current.parentElement;
      }
      return { component: 'unknown', source: 'unknown' };
    };
    if (!el) return { selector: 'unavailable (no element at point)', component: 'unknown', source: 'electron-native', point: { x: pointX, y: pointY } };
    const rect = el.getBoundingClientRect();
    const fiber = reactFiberFor(el);
    const attrs = {};
    for (const name of ['id','class','role','aria-label','data-testid','data-test','name','type','href']) {
      const value = el.getAttribute(name);
      if (value) attrs[name] = clip(value, 240);
    }
    return {
      type: 'browser_element',
      selector: selectorFor(el),
      tag: (el.localName || el.tagName || '').toLowerCase(),
      text: clip(el.innerText || el.textContent || el.getAttribute('aria-label') || '', 500),
      component: fiber.component,
      source: fiber.source,
      attributes: attrs,
      rect: { x: rect.x, y: rect.y, top: rect.top, left: rect.left, width: rect.width, height: rect.height },
      point: { x: pointX, y: pointY },
    };
  })()`;
  const selection = await record.view.webContents.executeJavaScript(script, true);
  return { ...publicTabState(record, 'Element selected.'), selection };
}

async function interact(record, payload) {
  const wc = record.view.webContents;
  const action = String(payload.action || '').toLowerCase();
  const x = Math.max(0, Math.round(Number(payload.x) || 0));
  const y = Math.max(0, Math.round(Number(payload.y) || 0));
  if (action === 'click' || action === 'double_click') {
    const clickCount = action === 'double_click' ? 2 : 1;
    wc.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount });
    wc.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount });
  } else if (action === 'wheel') {
    wc.sendInputEvent({ type: 'mouseWheel', x, y, deltaX: Number(payload.delta_x) || 0, deltaY: Number(payload.delta_y) || 0 });
  } else if (action === 'text') {
    wc.insertText(String(payload.text || ''));
  } else if (action === 'key') {
    const keyCode = String(payload.key || payload.code || '');
    if (keyCode) {
      const modifiers = [];
      if (payload.alt_key) modifiers.push('alt');
      if (payload.ctrl_key) modifiers.push('control');
      if (payload.meta_key) modifiers.push('meta');
      if (payload.shift_key) modifiers.push('shift');
      wc.sendInputEvent({ type: 'keyDown', keyCode, modifiers });
      wc.sendInputEvent({ type: 'keyUp', keyCode, modifiers });
    }
  } else {
    throw new Error(`unsupported interaction: ${action}`);
  }
  return publicTabState(record, 'Page updated.');
}

async function captureAreaInNativeView(record) {
  if (!record || !record.view || record.view.webContents.isDestroyed()) throw new Error('No browser tab is active.');
  const script = `(() => new Promise((resolve) => {
    const existing = document.getElementById('__hermes_browser_workbench_area_capture');
    if (existing) existing.remove();
    const overlay = document.createElement('div');
    overlay.id = '__hermes_browser_workbench_area_capture';
    overlay.style.cssText = 'position:fixed;inset:0;z-index:2147483647;cursor:crosshair;background:rgba(17,24,39,.08);user-select:none;touch-action:none;';
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;border:2px solid #8b5cf6;background:rgba(139,92,246,.16);box-shadow:0 0 0 9999px rgba(0,0,0,.18);pointer-events:none;';
    const label = document.createElement('div');
    label.textContent = 'Drag to capture area • Esc to cancel';
    label.style.cssText = 'position:fixed;top:12px;left:50%;transform:translateX(-50%);padding:6px 10px;border-radius:999px;background:rgba(17,24,39,.92);color:white;font:12px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;pointer-events:none;';
    overlay.appendChild(box);
    overlay.appendChild(label);
    document.documentElement.appendChild(overlay);
    let start = null;
    const cleanup = () => { window.removeEventListener('keydown', onKey, true); overlay.remove(); };
    const clamp = (value, max) => Math.max(0, Math.min(max, value));
    const point = (event) => ({ x: clamp(event.clientX, window.innerWidth), y: clamp(event.clientY, window.innerHeight) });
    const draw = (a, b) => {
      const left = Math.min(a.x, b.x), top = Math.min(a.y, b.y);
      const width = Math.abs(a.x - b.x), height = Math.abs(a.y - b.y);
      box.style.left = left + 'px'; box.style.top = top + 'px'; box.style.width = width + 'px'; box.style.height = height + 'px';
      return { x: left, y: top, width, height };
    };
    const onKey = (event) => { if (event.key === 'Escape') { event.preventDefault(); cleanup(); resolve({ cancelled: true }); } };
    overlay.addEventListener('pointerdown', (event) => { event.preventDefault(); start = point(event); draw(start, start); overlay.setPointerCapture(event.pointerId); }, true);
    overlay.addEventListener('pointermove', (event) => { if (start) draw(start, point(event)); }, true);
    overlay.addEventListener('pointerup', (event) => {
      if (!start) return;
      event.preventDefault();
      const clip = draw(start, point(event));
      cleanup();
      if (clip.width < 4 || clip.height < 4) resolve({ cancelled: true });
      else resolve({ clip: { x: Math.round(clip.x), y: Math.round(clip.y), width: Math.round(clip.width), height: Math.round(clip.height) } });
    }, true);
    window.addEventListener('keydown', onKey, true);
  }))()`;
  const result = await record.view.webContents.executeJavaScript(script, true);
  if (!result || result.cancelled || !result.clip) return publicTabState(record, 'Area capture cancelled.');
  const clip = result.clip;
  const image = await record.view.webContents.capturePage({
    x: Math.max(0, Math.round(Number(clip.x) || 0)),
    y: Math.max(0, Math.round(Number(clip.y) || 0)),
    width: Math.max(1, Math.round(Number(clip.width) || 1)),
    height: Math.max(1, Math.round(Number(clip.height) || 1)),
  });
  return {
    ...publicTabState(record, 'Area screenshot captured.'),
    attachment: { name: 'browser-workbench-area.png', type: 'image/png', data: image.toPNG().toString('base64') },
  };
}

async function handleBridgeRequest(req, res) {
  if (req.headers.authorization !== `Bearer ${bridgeToken}`) {
    return jsonResponse(res, 401, { ok: false, error: 'unauthorized' });
  }
  const url = new URL(req.url || '/', 'http://127.0.0.1');
  const parts = url.pathname.split('/').filter(Boolean);
  try {
    if (req.method === 'POST' && url.pathname === '/tabs') {
      const payload = await readJson(req);
      const record = ensureTab(payload);
      return jsonResponse(res, 200, await loadRecord(record, payload));
    }
    if (parts[0] === 'tabs' && parts[1]) {
      const sessionId = decodeURIComponent(parts[1]);
      let record = tabs.get(sessionId);
      if (record && !recordHasLiveWebContents(record)) {
        removeNativeViewFromWindow(record);
        tabs.delete(sessionId);
        record = null;
      }
      let recoveryPayload = null;
      const action = String(parts[2] || '');
      if (!record && req.method === 'POST' && (action === 'navigate' || action === 'reload')) {
        recoveryPayload = await readJson(req);
        record = ensureTab({ ...recoveryPayload, session_id: sessionId });
        return jsonResponse(res, 200, await loadRecord(record, { ...recoveryPayload, session_id: sessionId }));
      }
      if (!record) return jsonResponse(res, 404, { ok: false, error: 'tab not found' });
      if (req.method === 'GET' && parts.length === 2) return jsonResponse(res, 200, publicTabState(record));
      if (req.method === 'DELETE' && parts.length === 2) return jsonResponse(res, 200, closeTab(sessionId));
      if (req.method === 'POST' && parts[2] === 'navigate') return jsonResponse(res, 200, await loadRecord(record, recoveryPayload || await readJson(req)));
      if (req.method === 'POST' && parts[2] === 'reload') { applyRecordPayload(record, await readJson(req)); reloadRecord(record); return jsonResponse(res, 200, publicTabState(record, 'Reloading page.')); }
      if (req.method === 'POST' && parts[2] === 'stop-loading') { applyRecordPayload(record, await readJson(req)); record.view.webContents.stop(); setRecordLoadStatus(record, 'idle'); return jsonResponse(res, 200, publicTabState(record, 'Loading stopped.')); }
      if (req.method === 'POST' && parts[2] === 'back') { applyRecordPayload(record, await readJson(req)); if (record.view.webContents.canGoBack()) { markRecordLoading(record, 'go-back', record.view.webContents.getURL() || record.url); record.view.webContents.goBack(); } return jsonResponse(res, 200, publicTabState(record)); }
      if (req.method === 'POST' && parts[2] === 'forward') { applyRecordPayload(record, await readJson(req)); if (record.view.webContents.canGoForward()) { markRecordLoading(record, 'go-forward', record.view.webContents.getURL() || record.url); record.view.webContents.goForward(); } return jsonResponse(res, 200, publicTabState(record)); }
      if (req.method === 'POST' && parts[2] === 'devtools') {
        const payload = await readJson(req);
        const mode = normalizeDevtoolsMode(payload && payload.mode);
        record.view.webContents.openDevTools({ mode });
        return jsonResponse(res, 200, publicTabState(record, mode === 'detach' ? 'DevTools opened in a new window.' : 'DevTools opened.'));
      }
      if (req.method === 'POST' && parts[2] === 'clear-cache') { await record.view.webContents.session.clearCache(); return jsonResponse(res, 200, publicTabState(record, 'Cache cleared.')); }
      if (req.method === 'POST' && parts[2] === 'clear-cookies') { await record.view.webContents.session.clearStorageData({ storages: ['cookies'] }); return jsonResponse(res, 200, publicTabState(record, 'Cookies cleared.')); }
      if (req.method === 'POST' && parts[2] === 'inspect') return jsonResponse(res, 200, await inspectAt(record, await readJson(req)));
      if (req.method === 'POST' && parts[2] === 'interact') return jsonResponse(res, 200, await interact(record, await readJson(req)));
      if (req.method === 'POST' && parts[2] === 'screenshot') {
        const payload = await readJson(req);
        const clip = payload && payload.clip && typeof payload.clip === 'object' ? payload.clip : null;
        const rect = clip ? {
          x: Math.max(0, Math.round(Number(clip.x) || 0)),
          y: Math.max(0, Math.round(Number(clip.y) || 0)),
          width: Math.max(1, Math.round(Number(clip.width) || 1)),
          height: Math.max(1, Math.round(Number(clip.height) || 1)),
        } : undefined;
        const image = rect ? await record.view.webContents.capturePage(rect) : await record.view.webContents.capturePage();
        return jsonResponse(res, 200, { ...publicTabState(record, rect ? 'Area screenshot captured.' : 'Screenshot captured.'), attachment: { name: rect ? 'browser-workbench-area.png' : 'browser-workbench.png', type: 'image/png', data: image.toPNG().toString('base64') } });
      }
    }
    return jsonResponse(res, 404, { ok: false, error: 'not found' });
  } catch (err) {
    return jsonResponse(res, 500, { ok: false, status: 'bridge_error', error: err && err.message ? err.message : String(err) });
  }
}

function startBridgeServer() {
  return new Promise((resolve, reject) => {
    bridgeServer = http.createServer((req, res) => { void handleBridgeRequest(req, res); });
    bridgeServer.on('error', reject);
    bridgeServer.listen(0, '127.0.0.1', () => {
      const address = bridgeServer.address();
      bridgeUrl = `http://127.0.0.1:${address.port}`;
      resolve();
    });
  });
}

ipcMain.handle('browser-workbench:set-bounds', (_event, payload) => setNativeBounds(payload || {}));
ipcMain.handle('browser-workbench:set-overlay-suppressed', (_event, payload) => setApplicationOverlaySuppression(payload || {}));
ipcMain.handle('browser-workbench:show-url-suggestions', (_event, payload) => showUrlSuggestionOverlay(payload || {}));
ipcMain.handle('browser-workbench:update-url-suggestions', (_event, payload) => showUrlSuggestionOverlay(payload || {}));
ipcMain.handle('browser-workbench:hide-url-suggestions', () => hideUrlSuggestionOverlay());
ipcMain.handle('browser-workbench:show-actions-menu', (_event, payload) => showActionsMenuOverlay(payload || {}));
ipcMain.handle('browser-workbench:update-actions-menu', (_event, payload) => showActionsMenuOverlay(payload || {}));
ipcMain.handle('browser-workbench:hide-actions-menu', () => hideActionsMenuOverlay());
ipcMain.handle('browser-workbench:bridge-info', () => ({ bridgeUrl, bridgeToken }));
ipcMain.handle('browser-workbench:start-area-capture', async (_event, payload) => {
  const sessionId = String(payload && (payload.sessionId || payload.session_id) || activeSessionId || '').trim();
  const record = sessionId ? tabs.get(sessionId) : null;
  return captureAreaInNativeView(record);
});

app.whenReady().then(async () => {
  writeDesktopAppPidFile();
  assertWebContentsViewAvailable();
  await startBridgeServer();
  createMainWindow();
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createMainWindow(); });
}).catch((err) => {
  console.error(err);
  app.quit();
});

app.on('before-quit', () => {
  appIsQuitting = true;
});

app.on('window-all-closed', () => {
  hideActionsMenuOverlay();
  closeAllNativeTabs();
  if (bridgeServer) bridgeServer.close();
  if (process.platform !== 'darwin') app.quit();
});
app.on('will-quit', () => {
  removeDesktopAppPidFile();
});
