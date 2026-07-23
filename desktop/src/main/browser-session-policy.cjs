'use strict';

const BROWSER_WORKBENCH_PARTITION = 'persist:hermes-browser-workbench';

function browserWorkbenchPartition(_tabSessionId) {
  // Tab/session identity must not scope browser profile data. Chromium still
  // isolates origin-bound data, while sessionStorage and history stay attached
  // to each WebContents instance.
  return BROWSER_WORKBENCH_PARTITION;
}

module.exports = {
  BROWSER_WORKBENCH_PARTITION,
  browserWorkbenchPartition,
};
