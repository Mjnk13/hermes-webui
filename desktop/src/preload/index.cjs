'use strict';

const { contextBridge, ipcRenderer } = require('electron');

function readBridgeConfig() {
  const prefix = '--hermes-desktop-bridge=';
  const arg = process.argv.find((value) => String(value || '').startsWith(prefix));
  if (!arg) return { bridgeUrl: '', bridgeToken: '' };
  try {
    return JSON.parse(Buffer.from(arg.slice(prefix.length), 'base64url').toString('utf8'));
  } catch (_) {
    return { bridgeUrl: '', bridgeToken: '' };
  }
}

const bridgeConfig = readBridgeConfig();

async function registerDesktopBridge() {
  if (!bridgeConfig.bridgeUrl || !bridgeConfig.bridgeToken || !window.fetch) return null;
  const res = await window.fetch('/api/browser-workbench/desktop-bridge', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      bridge_url: bridgeConfig.bridgeUrl,
      bridge_token: bridgeConfig.bridgeToken,
    }),
  });
  let body = {};
  try { body = await res.json(); } catch (_) { body = {}; }
  if (!res.ok) {
    const err = new Error(body.error || body.message || `Desktop bridge registration failed (${res.status})`);
    err.data = body;
    throw err;
  }
  return body;
}

const browserBridge = {
  isDesktop: true,
  renderer: 'electron-native',
  bridgeUrl: bridgeConfig.bridgeUrl,
  registerBridge: registerDesktopBridge,
  setBounds(payload) {
    return ipcRenderer.invoke('browser-workbench:set-bounds', payload || {});
  },
  setOverlaySuppressed(payload) {
    return ipcRenderer.invoke('browser-workbench:set-overlay-suppressed', payload || {});
  },
  captureOverlaySnapshot(payload) {
    return ipcRenderer.invoke('browser-workbench:capture-overlay-snapshot', payload || {});
  },
  showUrlSuggestions(payload) {
    return ipcRenderer.invoke('browser-workbench:show-url-suggestions', payload || {});
  },
  updateUrlSuggestions(payload) {
    return ipcRenderer.invoke('browser-workbench:update-url-suggestions', payload || {});
  },
  hideUrlSuggestions(payload) {
    return ipcRenderer.invoke('browser-workbench:hide-url-suggestions', payload || {});
  },
  showActionsMenu(payload) {
    return ipcRenderer.invoke('browser-workbench:show-actions-menu', payload || {});
  },
  updateActionsMenu(payload) {
    return ipcRenderer.invoke('browser-workbench:update-actions-menu', payload || {});
  },
  hideActionsMenu(payload) {
    return ipcRenderer.invoke('browser-workbench:hide-actions-menu', payload || {});
  },
  startAreaCapture(payload) {
    return ipcRenderer.invoke('browser-workbench:start-area-capture', payload || {});
  },
  findInPage(payload) {
    return ipcRenderer.invoke('browser-workbench:find-in-page', payload || {});
  },
  stopFindInPage(payload) {
    return ipcRenderer.invoke('browser-workbench:stop-find-in-page', payload || {});
  },
  onUrlSuggestionAction(callback) {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on('browser-workbench:url-suggestion-action', listener);
    return () => ipcRenderer.removeListener('browser-workbench:url-suggestion-action', listener);
  },
  onActionsMenuAction(callback) {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on('browser-workbench:actions-menu-action', listener);
    return () => ipcRenderer.removeListener('browser-workbench:actions-menu-action', listener);
  },
  onNativeSurfaceInteraction(callback) {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on('browser-workbench:native-surface-interaction', listener);
    return () => ipcRenderer.removeListener('browser-workbench:native-surface-interaction', listener);
  },
  onFindRequested(callback) {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on('browser-workbench:find-requested', listener);
    return () => ipcRenderer.removeListener('browser-workbench:find-requested', listener);
  },
  onFindResult(callback) {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on('browser-workbench:find-result', listener);
    return () => ipcRenderer.removeListener('browser-workbench:find-result', listener);
  },
  onNativeSelection(callback) {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on('browser-workbench:native-selection', listener);
    return () => ipcRenderer.removeListener('browser-workbench:native-selection', listener);
  },
  onNavigation(callback) {
    if (typeof callback !== 'function') return () => {};
    const listener = (_event, payload) => callback(payload || {});
    ipcRenderer.on('browser-workbench:navigation', listener);
    return () => ipcRenderer.removeListener('browser-workbench:navigation', listener);
  },
  invoke(method, payload) {
    if (method === 'setBounds') return ipcRenderer.invoke('browser-workbench:set-bounds', payload || {});
    if (method === 'setOverlaySuppressed') return ipcRenderer.invoke('browser-workbench:set-overlay-suppressed', payload || {});
    if (method === 'captureOverlaySnapshot') return ipcRenderer.invoke('browser-workbench:capture-overlay-snapshot', payload || {});
    if (method === 'showUrlSuggestions') return ipcRenderer.invoke('browser-workbench:show-url-suggestions', payload || {});
    if (method === 'updateUrlSuggestions') return ipcRenderer.invoke('browser-workbench:update-url-suggestions', payload || {});
    if (method === 'hideUrlSuggestions') return ipcRenderer.invoke('browser-workbench:hide-url-suggestions', payload || {});
    if (method === 'showActionsMenu') return ipcRenderer.invoke('browser-workbench:show-actions-menu', payload || {});
    if (method === 'updateActionsMenu') return ipcRenderer.invoke('browser-workbench:update-actions-menu', payload || {});
    if (method === 'hideActionsMenu') return ipcRenderer.invoke('browser-workbench:hide-actions-menu', payload || {});
    if (method === 'startAreaCapture') return ipcRenderer.invoke('browser-workbench:start-area-capture', payload || {});
    if (method === 'findInPage') return ipcRenderer.invoke('browser-workbench:find-in-page', payload || {});
    if (method === 'stopFindInPage') return ipcRenderer.invoke('browser-workbench:stop-find-in-page', payload || {});
    if (method === 'bridgeInfo') return ipcRenderer.invoke('browser-workbench:bridge-info');
    return Promise.reject(new Error(`Unknown Hermes desktop browser method: ${method}`));
  },
};

contextBridge.exposeInMainWorld('hermesDesktop', {
  platform: process.platform,
  browser: browserBridge,
});

window.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    registerDesktopBridge().then((payload) => {
      window.dispatchEvent(new CustomEvent('hermes-desktop-browser-bridge-ready', { detail: payload || {} }));
      if (typeof window.refreshBrowserWorkbenchCapabilities === 'function') window.refreshBrowserWorkbenchCapabilities();
    }).catch((err) => {
      window.dispatchEvent(new CustomEvent('hermes-desktop-browser-bridge-error', { detail: { message: err && err.message ? err.message : String(err) } }));
      console.warn('[Hermes Desktop] Browser bridge registration failed:', err);
    });
  }, 50);
});
