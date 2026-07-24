'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('browser-page preload disables only Electron development security warnings', () => {
  const preloadPath = path.resolve(
    __dirname,
    '../src/preload/browser-page-security.cjs',
  );
  const source = fs.readFileSync(preloadPath, 'utf8');
  const context = { window: {} };

  vm.runInNewContext(source, context, { filename: preloadPath });

  assert.deepEqual(
    Object.keys(context.window),
    ['ELECTRON_DISABLE_SECURITY_WARNINGS'],
  );
  assert.equal(context.window.ELECTRON_DISABLE_SECURITY_WARNINGS, true);
});
