'use strict';

function createShellWebPreferences({ preloadPath, bridgePayload }) {
  return {
    preload: preloadPath,
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: false,
    // On macOS, a window on another Space is treated as occluded. Keep the
    // shell renderer processing live agent/command updates so returning to the
    // app never has to drain a throttled render queue.
    backgroundThrottling: false,
    additionalArguments: [`--hermes-desktop-bridge=${bridgePayload}`],
  };
}

module.exports = { createShellWebPreferences };
