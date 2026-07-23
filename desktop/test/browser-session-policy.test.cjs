'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {
  BROWSER_WORKBENCH_PARTITION,
  browserWorkbenchPartition,
} = require('../src/main/browser-session-policy.cjs');

test('Browser Workbench tabs share one persistent browser profile', () => {
  const firstTab = browserWorkbenchPartition('browser-session-a');
  const secondTab = browserWorkbenchPartition('browser-session-b');

  assert.equal(firstTab, secondTab);
  assert.equal(firstTab, BROWSER_WORKBENCH_PARTITION);
  assert.match(firstTab, /^persist:/);
});

test('Browser Workbench profile identity is stable across tab recreation', () => {
  assert.equal(
    browserWorkbenchPartition('browser-session-a'),
    browserWorkbenchPartition('browser-session-a'),
  );
});

test('Browser Workbench profile stays isolated from the default Electron session', () => {
  assert.notEqual(browserWorkbenchPartition('browser-session-a'), '');
  assert.equal(BROWSER_WORKBENCH_PARTITION, 'persist:hermes-browser-workbench');
});
