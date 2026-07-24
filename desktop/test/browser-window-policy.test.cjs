'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  createBrowserPageWebPreferences,
  createShellWebPreferences,
} = require('../src/main/browser-window-policy.cjs');

test('desktop shell keeps live rendering active while the window is occluded', () => {
  const preferences = createShellWebPreferences({
    preloadPath: '/tmp/hermes-preload.cjs',
    bridgePayload: 'bridge-payload',
  });

  assert.equal(preferences.backgroundThrottling, false);
  assert.equal(preferences.preload, '/tmp/hermes-preload.cjs');
  assert.deepEqual(preferences.additionalArguments, [
    '--hermes-desktop-bridge=bridge-payload',
  ]);
});

test('desktop shell rendering policy preserves renderer isolation settings', () => {
  const preferences = createShellWebPreferences({
    preloadPath: '/tmp/hermes-preload.cjs',
    bridgePayload: 'bridge-payload',
  });

  assert.equal(preferences.contextIsolation, true);
  assert.equal(preferences.nodeIntegration, false);
  assert.equal(preferences.sandbox, false);
});

test('Browser Workbench pages use an isolated security-warning preload', () => {
  const preferences = createBrowserPageWebPreferences({
    preloadPath: '/tmp/hermes-browser-page-security.cjs',
    partition: 'persist:hermes-browser-workbench',
  });

  assert.equal(preferences.contextIsolation, true);
  assert.equal(preferences.nodeIntegration, false);
  assert.equal(preferences.sandbox, true);
  assert.equal(preferences.partition, 'persist:hermes-browser-workbench');
  assert.equal(preferences.preload, '/tmp/hermes-browser-page-security.cjs');
});
