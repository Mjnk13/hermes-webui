'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const path = require('node:path');
const test = require('node:test');

function loadInlineRendererFactories() {
  const mainPath = path.resolve(__dirname, '../src/main/index.cjs');
  const source = fs.readFileSync(mainPath, 'utf8');
  const localRequire = createRequire(mainPath);
  const electronStub = {
    app: {
      on() {},
      quit() {},
      whenReady() {
        return { then() { return { catch() {} }; } };
      },
    },
    BrowserWindow: { getAllWindows() { return []; } },
    WebContentsView: function WebContentsView() {},
    ipcMain: { handle() {} },
  };
  const requireForTest = (request) => (
    request === 'electron' ? electronStub : localRequire(request)
  );
  const moduleForTest = { exports: {} };
  const exposeFactories = [
    source,
    'module.exports = { urlSuggestionOverlayHtml, actionsMenuOverlayHtml };',
  ].join('\n');

  Function(
    'require',
    'module',
    'exports',
    '__dirname',
    '__filename',
    exposeFactories,
  )(
    requireForTest,
    moduleForTest,
    moduleForTest.exports,
    path.dirname(mainPath),
    mainPath,
  );
  return moduleForTest.exports;
}

function assertStrictInlineRendererCsp(html) {
  const cspMatch = html.match(
    /<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]+)"/i,
  );
  assert.ok(cspMatch, 'inline Electron renderer must define a CSP');

  const policy = cspMatch[1];
  assert.match(policy, /(?:^|;\s*)default-src 'none'(?:;|$)/);
  assert.match(policy, /(?:^|;\s*)object-src 'none'(?:;|$)/);
  assert.match(policy, /(?:^|;\s*)base-uri 'none'(?:;|$)/);
  assert.doesNotMatch(policy, /'unsafe-eval'/);
  assert.doesNotMatch(policy, /script-src[^;]*'unsafe-inline'/);

  const scriptNonce = policy.match(/script-src 'nonce-([^']+)'/);
  const styleNonce = policy.match(/style-src 'nonce-([^']+)'/);
  assert.ok(scriptNonce, 'script-src must use a nonce');
  assert.ok(styleNonce, 'style-src must use a nonce');
  assert.equal(styleNonce[1], scriptNonce[1]);
  assert.match(html, new RegExp(`<script nonce="${scriptNonce[1]}"`));
  assert.match(html, new RegExp(`<style nonce="${styleNonce[1]}"`));
}

test('URL suggestion renderer has a strict nonce-based CSP', () => {
  const { urlSuggestionOverlayHtml } = loadInlineRendererFactories();
  assertStrictInlineRendererCsp(urlSuggestionOverlayHtml({
    items: [{ title: 'Example', url: 'https://example.com/' }],
    activeIndex: 0,
  }));
});

test('actions menu renderer has a strict nonce-based CSP', () => {
  const { actionsMenuOverlayHtml } = loadInlineRendererFactories();
  assertStrictInlineRendererCsp(actionsMenuOverlayHtml({ zoom: 100 }));
});

test('electron-vite fallback renderer disables script execution', () => {
  const rendererPath = path.resolve(__dirname, '../src/renderer/index.html');
  const html = fs.readFileSync(rendererPath, 'utf8');
  const cspMatch = html.match(
    /<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]+)"/i,
  );

  assert.ok(cspMatch, 'fallback Electron renderer must define a CSP');
  assert.match(cspMatch[1], /(?:^|;\s*)default-src 'none'(?:;|$)/);
  assert.match(cspMatch[1], /(?:^|;\s*)script-src 'none'(?:;|$)/);
  assert.doesNotMatch(cspMatch[1], /'unsafe-(?:eval|inline)'/);
});
