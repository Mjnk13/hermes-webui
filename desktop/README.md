# Hermes WebUI Desktop Shell

Root-level Electron/electron-vite package for the Browser Workbench native renderer.

The normal Hermes WebUI remains Python + vanilla JS with no browser bundler. This package is an optional desktop shell that loads an already-running Hermes WebUI URL and overlays Electron `WebContentsView` browser tabs inside the existing Browser Workbench viewport.

Usage:

```bash
cd desktop
npm install
HERMES_WEBUI_URL=http://127.0.0.1:8789 npm run dev
```

Foreground bootstrap autostarts the Electron shell after `/health` is ready:

```bash
python3 bootstrap.py --no-browser --foreground --host 127.0.0.1 8788
```

`./ctl.sh start` and `./ctl.sh restart` use the same foreground bootstrap path.
Browser Workbench is enabled in WebUI by default and can be turned off with
`HERMES_WEBUI_BROWSER_WORKBENCH=0` or `false`. The Electron shell is not opened
unless explicitly requested; set `HERMES_WEBUI_DESKTOP_SHELL=1` or `true` to
enable the sidecar for a launch.

Lifecycle contract:

- The shell does not replace the WebUI layout. It loads Hermes WebUI and keeps the left sidebar, right workspace, and bottom composer in the same WebUI shell.
- The native browser surface exists only while the desktop shell and WebUI window are alive.
- If the WebUI renderer is closed or crashes, the Electron app closes and the local bridge server stops.
- Browser tabs are owned by `/api/browser-workbench/*`; the desktop bridge only supplies native rendering/input for those sessions.
- The WebUI shell keeps live agent, tool, and command rendering active while
  its window is hidden, minimized, or occluded by another macOS Space. Returning
  to the app must not wait for a throttled renderer queue to catch up. Embedded
  websites keep Chromium's normal background-tab throttling behavior.
- Browser tabs use one dedicated persistent Electron profile. Same-origin cookies,
  local storage, IndexedDB, Cache Storage, service workers, and HTTP cache are
  shared across Workbench tabs. Chromium-persistent site data is retained across
  app restarts, while session storage, navigation history, scroll, zoom, focus,
  and the current URL remain tab-local.

Security contract:

- The desktop bridge binds to `127.0.0.1` on an ephemeral port.
- Requests require a random bearer token generated per app launch.
- The preload registers the bridge with the WebUI through the same-origin `/api/browser-workbench/desktop-bridge` endpoint, so normal WebUI CSRF protection still applies.
- The shared Browser Workbench profile remains isolated from both the Hermes
  shell session and the user's normal Chrome/Chromium profile.
