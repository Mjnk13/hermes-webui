'use strict';

// Browser Workbench intentionally loads arbitrary websites. Their CSP is
// controlled by each site, not by Hermes, and Electron's development-only CSP
// warning would otherwise appear for every site without one. Keep this scoped
// to the sandboxed browser-page renderer so warnings in Hermes-owned renderers
// remain visible.
window.ELECTRON_DISABLE_SECURITY_WARNINGS = true;
