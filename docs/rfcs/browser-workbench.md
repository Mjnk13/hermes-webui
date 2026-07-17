# RFC: Browser Workbench

- **Status:** Proposed
- **Author:** @franksong2702
- **Created:** 2026-06-17
- **Tracking issue:** TBD
- **Related docs:** [`docs/UIUX-GUIDE.md`](../UIUX-GUIDE.md), [`DESIGN.md`](../../DESIGN.md), [`hermes-run-adapter-contract.md`](hermes-run-adapter-contract.md)
- **Related external references:** Cursor Browser docs, Cursor Browser help, Cursor visual editor blog, Hermes Agent browser automation docs, Chrome DevTools Protocol, MDN `X-Frame-Options`, MDN CSP `frame-ancestors`, Electron embedded web-content APIs

## RFC Positioning

This RFC defines the intended product and architecture direction for a
Cursor-style browser surface inside Hermes WebUI.

The previous Browser Inspector bookmarklet/console-snippet prototype is being
removed so the product direction can stay focused on the embedded Browser
Workbench. The target experience is a real browser tab/pane inside Hermes WebUI
with a URL bar, interactive viewport, inspect tools, diagnostics, and a direct
element-ping-to-composer workflow.

This RFC is a design direction and implementation gate. It does not authorize a
large speculative browser rewrite in one PR. The implementation should proceed in
small, reversible slices, preserving WebUI's no-build-step Python + vanilla JS
architecture unless a later accepted slice explicitly justifies a new runtime
boundary or desktop shell.

## Problem

Cursor's browser workflow is fast because the user, browser, codebase, and agent
share one visual debugging loop:

1. Open the app in a browser pane inside the editor.
2. Interact with the page directly.
3. Point at or inspect a visible element.
4. Prompt the agent with precise visual/DOM context.
5. Let the agent locate and edit the relevant component.
6. Reload or hot-reload the same browser pane for verification.

Hermes WebUI currently has two separate pieces that do not yet produce this
workflow:

- Hermes Agent has browser automation tools for the agent. These expose snapshots,
  screenshots, ref IDs, and browser actions to the agent, but not a first-class
  user-facing browser tab in WebUI.
- The removed Browser Inspector bookmarklet prototype proved that element
  metadata is useful in the composer, but it required copying scripts into an
  external browser and did not provide an embedded browser, URL bar,
  devtools-style logs, shared viewport, or reliable CSP-safe interaction model.

The missing primitive is a WebUI-owned Browser Workbench session that both the
human and agent can reference.

## Research Summary

Primary sources checked while shaping this RFC:

- Cursor Browser docs: `https://cursor.com/docs/agent/tools/browser.md`
- Cursor Browser help: `https://cursor.com/help/ai-features/browser.md`
- Cursor visual editor blog: `https://cursor.com/blog/browser-visual-editor`
- Hermes Agent browser automation docs:
  `https://hermes-agent.nousresearch.com/docs/user-guide/features/browser`
- Chrome DevTools Protocol: `https://chromedevtools.github.io/devtools-protocol/`
- MDN `X-Frame-Options`:
  `https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Frame-Options`
- MDN CSP `frame-ancestors`:
  `https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy/frame-ancestors`
- Electron embedded web content docs:
  `https://www.electronjs.org/docs/latest/api/webview-tag` and
  `https://www.electronjs.org/docs/latest/api/browser-view`

Key findings:

- Cursor Browser is not just a bookmarklet. Its docs describe a native/inline
  browser integration with screenshots, console logs, network traffic, and browser
  actions surfaced in chat.
- Cursor's visual editor goes further: web app, codebase, and visual controls live
  in the same window; users can drag/drop DOM elements, inspect components and
  props, adjust styles, and click an element before prompting changes.
- Hermes Agent already has browser automation backends, including local
  Chromium-family CDP attachment, but those are currently agent-facing tools, not
  a WebUI browser workspace.
- A plain iframe preview cannot be the primary architecture because many apps set
  `X-Frame-Options` or CSP `frame-ancestors`, and cross-origin iframe DOM access
  cannot support reliable inspection, devtools, or source mapping.
- Until an Electron/CDP renderer lands, a plain iframe preview may be used as a
  clearly-labeled compatibility fallback for frameable local development pages;
  it must not be presented as the full Browser Workbench renderer.
- A desktop shell such as Electron can provide a native embedded browser view, but
  that is a separate packaging/product decision. WebUI should first validate a
  CDP-backed in-browser workbench that runs in the existing web app.

## Goals

- Provide a Browser panel/workbench inside Hermes WebUI with URL-bar navigation,
  back/forward/reload, viewport presets, and an interactive page surface.
- Let the user inspect and ping an element from that surface into the existing
  composer as reviewable prompt text.
- Capture browser diagnostics that matter for UI debugging: console errors,
  network failures, current URL/title, screenshot or crop, DOM selector, bounds,
  accessibility name/role, and framework/source hints where available.
- Allow the agent to reference the same Browser Workbench state without needing
  the user to copy/paste external-browser artifacts.
- Keep the first slices compatible with the current no-build-step WebUI: Python
  stdlib server shape, vanilla JS, existing auth/CSRF helpers, existing theme
  tokens, and focused tests.
- Make security boundaries explicit before any CDP endpoint or browser-control
  capability is exposed to the WebUI.

## Non-goals

- Do not build Cursor's full drag/drop visual editor in the first implementation
  slice.
- Do not make iframe embedding the primary solution.
- Do not add React, Vite, webpack, or a frontend build step for this workbench.
- Do not expose raw Chrome DevTools Protocol WebSocket URLs to the browser UI.
- Do not let arbitrary public websites send element selections or control events
  into Hermes without an authenticated, scoped workbench session.
- Do not persist browser screenshots, DOM dumps, console logs, network bodies, or
  form values into chat history unless the user explicitly sends a prompt that
  includes a sanitized subset.
- Do not assume automatic component/source mapping is reliable without sourcemaps,
  dev-mode transforms, framework hooks, or repo search evidence.
- Do not replace Hermes Agent's existing browser toolset; the workbench should
  reuse or interoperate with it where practical.

## Terms

### Previous selection prototype

The removed external-browser bookmarklet/console-snippet bridge proved that
element metadata is useful in the composer, but it is not part of the current
Browser Workbench UX. Future ping flows should keep the safe, bounded,
review-before-send selection contract without reintroducing bookmarklet setup UI.

### Browser Workbench

A WebUI panel that owns a browser session, shows an interactive browser viewport,
and exposes navigation, inspection, diagnostics, and prompt-attachment controls.

### Workbench Browser Session

The backend object that represents one browser target/page/context. It owns the
CDP connection or equivalent browser backend, viewport configuration, event cursor,
console/network buffers, current URL/title, and lifecycle state.

### Ping

A user-authored selection of a visible element. A ping should be safe, bounded,
reviewable, and appended to the composer rather than silently sent.

### Agent Control

A mode where an agent turn may navigate, inspect, or interact with the same
browser session. It must be explicit and observable; user interaction remains the
source of truth for human pings.

## Product Proposal

### Top-level placement

Add a `Browser` workspace surface to the existing WebUI shell. The workbench
should feel like a developer pane, not a decorative modal:

- Desktop: open as the primary center surface or a right-side workbench mode,
  depending on the final shell constraints.
- Narrow/mobile: provide an explicit full-screen Browser view or mark the feature
  desktop-only until touch ergonomics are designed.
- Preserve chat as the primary artifact; browser diagnostics should be available
  through panels/drawers, not sprayed into the transcript.

### Workbench chrome

The Browser panel should include:

- tab/session label
- URL input
- Back, Forward, Reload/Stop
- browser actions menu: screenshot, area screenshot, hard reload, copy URL, zoom,
  clear browsing data, and DevTools handoff
- viewport preset control: responsive, desktop, laptop, mobile, custom
- Inspect toggle
- Ping to prompt action/state
- optional Agent control indicator
- diagnostics drawer toggle

### Viewport

The page surface should be an interactive viewport backed by the browser backend.
For a CDP-backed WebUI implementation, the visible surface should be a native or
streamed browser surface with pointer and keyboard events forwarded back to the
browser. A one-off base64 screenshot/image surface is not acceptable as the
embedded Browser Workbench renderer because it feels like a laggy static image
instead of a Cursor-style browser.

The UI should make this clear when latency is present. It should avoid pretending
that a remote screencast is a native DOM iframe when it is actually a streamed
browser.

During early slices, a restored or navigated session may show an iframe fallback
for the current URL so frameable local apps can be viewed before the CDP/Electron
renderer exists. The fallback must keep an explicit warning that sites can block
framing and that element inspection/source mapping still require the real browser
backend.

The first safe WebUI-owned rendering slice is a local iframe bridge for loopback
URLs (`localhost`, `127.0.0.1`, and other loopback addresses). When an Opera
GX/Opera/Chrome/Chromium binary is available, the backend can instead launch an
isolated browser profile and drive Chrome DevTools Protocol for scoped target
management, a streamed Chromium viewport, pointer/keyboard forwarding, docked
DevTools handoff, bounded element inspection, and screenshot capture. Public
responses must still not include viewport PNG data URLs or other base64 image
fallbacks. Screenshot and crop payloads are temporary capture-to-composer
attachment artifacts only, not the embedded viewport renderer. Safe browser
actions in this slice are screenshot capture, area screenshot capture, hard
reload, copy URL, zoom, clearing scoped browsing data, and an Open DevTools
handoff via the browser's `devtoolsFrontendUrl`. Bounded CDP hit-testing may
collect sanitized selector, bounds, text, attributes, and React Fiber
component/source hints when the page exposes them. Console and network panels,
richer source mapping, and full element-to-code verification require later
CDP/Electron work.

### Inspect mode

Inspect mode changes pointer behavior:

- hovering highlights the element under the pointer,
- the highlight is shown both in the streamed viewport and, when possible, through
  CDP `Overlay` in the real browser target,
- click selects the element instead of activating it,
- Escape exits inspect mode,
- a small selection preview shows the element tag/name/selector before inserting.

### Ping to prompt

A ping appends reviewable context to the existing composer, not directly to chat
history. Current WebUI uses removable browser-element context pills backed by a
mention-style payload (`kind: "browser-element"`, display label, sanitized
payload). The composer UI shows only the pill; on submit the client sends the
structured `context_items` array and the server expands it into bounded
agent-visible context. Older/raw block insertion should remain only a fallback
for clients without the pill renderer.

The submit-time expansion should be concise, escaped, and grep-friendly:

```xml
<browser_workbench_context>
  <selected_browser_element index="1">
    <label>SaveButton</label>
    <url>http://localhost:3000/settings</url>
    <selector>button#save.btn.primary</selector>
    <component>SaveButton</component>
    <source>src/components/settings/SaveButton.tsx:37</source>
    <text>Save changes</text>
    <rect>{"height": 40.0, "left": 842.0, "top": 612.0, "width": 128.0}</rect>
  </selected_browser_element>
</browser_workbench_context>
```

Native/streamed screenshot or crop outputs should be referenced as temporary
workbench artifacts and not persisted as public static assets. The WebUI Browser
Workbench CDP slice exposes screenshot and area-crop payloads only through the
composer attachment tray; it must not re-use those captures as the viewport
surface.

### Diagnostics drawer

The initial diagnostics drawer can be devtools-lite rather than full Chrome
DevTools:

- Console: recent console messages/errors, searchable, redacted/truncated.
- Network: failed requests first, method/status/path/timing, no response bodies by
  default.
- Elements: selected element summary and selector chain, not a full DOM tree in
  the first slice.
- Screenshot: full viewport and selected-element crop actions.
- A11y: role/name/focusability for selected element.

### Relationship to chat

Browser Workbench should integrate with chat through explicit attachments and
agent-visible context, not by dumping every browser event into the transcript.

Recommended flow:

1. User opens Browser panel.
2. User navigates to a local app.
3. User clicks Inspect.
4. User pings the problematic element.
5. WebUI appends a removable browser-element context pill to the composer.
6. User adds the natural-language request and sends.
7. Agent receives the block, can inspect current repo files, and can request or
   use relevant browser evidence.
8. User or agent reloads the Browser panel to verify.

## Architecture Proposal

### High-level shape

The Browser Workbench should have five planes:

1. **Lifecycle plane:** create, attach, list, and close workbench browser sessions.
2. **Navigation plane:** URL-bar navigation, back/forward/reload, target status.
3. **Render/input plane:** stream frames to WebUI and forward pointer/keyboard
   events to the browser target.
4. **Inspection plane:** hit-test coordinates, highlight nodes, collect sanitized
   DOM/a11y/component metadata, capture screenshot crops.
5. **Diagnostics plane:** bounded console/network/log buffers, exposed through UI
   and prompt attachments.

### Backend owner

Create a dedicated backend module, for example `api/browser_workbench.py`, rather
than expanding `api/routes.py` directly. `api/routes.py` should only dispatch to
that module for `/api/browser-workbench/*` paths.

The backend module owns:

- workbench session registry,
- lifecycle locks,
- CDP/backend client abstraction,
- auth/CSRF checks for mutating endpoints,
- URL policy: accept `http`/`https` local, private-network, and public-web URLs
  for user-authored navigation while continuing to reject file/browser-internal
  schemes and credential-bearing URLs,
- bounded event/log buffers,
- temporary screenshot/crop storage,
- test reset helpers.

### Browser backend selection

The first implementation spike should decide which CDP transport to use. Options:

1. Reuse Hermes Agent's existing local browser/CDP machinery if it can be safely
   imported behind an adapter without leaking raw CDP sockets to WebUI.
2. Use a small optional browser-backend dependency only if the benefit is clear
   and documented. This repo intentionally keeps dependencies minimal.
3. Implement a narrow internal CDP transport only as a last resort; custom
   WebSocket protocol code is easy to get wrong and should not become a broad
   maintenance burden.

The workbench should hide this behind an internal `BrowserWorkbenchBackend`
interface so later slices can swap local CDP, Browserbase, Camofox, or a future
Hermes Agent browser session API.

### Frontend owner

Create a focused frontend module, for example `static/browser_workbench.js`.
It should own:

- Browser panel DOM construction/event wiring,
- URL bar state,
- local persistence of Browser tabs, active Browser tab, and last URL without
  persisting stale backend session IDs,
- canvas/image frame rendering,
- input forwarding,
- inspect mode UI,
- selection preview,
- composer insertion using the existing composer helpers/patterns,
- diagnostics drawer rendering.

CSS should live in `static/style.css` with existing variables/tokens. Avoid
adding decorative colors or nested rounded-card stacks that violate the calm
console direction.

### Application overlays above the native browser

Electron `WebContentsView` surfaces are native child views, so ordinary DOM
`z-index` cannot place chat UI above them. Main-application overlays use one
document-level portal and publish a generation-ordered overlay stack. Anchored
surfaces use the trigger's viewport `getBoundingClientRect()` and fixed
coordinates with flip, shift, and collision handling; full-window dialogs keep
viewport-centered coordinates.

When an application overlay intersects the Browser Workbench viewport, the
desktop bridge temporarily makes the native view invisible. This operation is
visibility-only: it must not change bounds, detach or recreate the view, or
alter the browser tab's URL, scroll position, zoom, session, focus history, or
navigation history. The DOM browser placeholder remains at the same dimensions,
so overlays never resize, crop, split, or push the browser/composer layout.
Generation ordering and the overlay count prevent a stale close event or one
nested overlay from restoring the native view before the last intersecting
overlay closes. Renderer reload/teardown resets suppression as a final cleanup.

Browser-owned native UI such as its URL suggestions remains separate from this
application-overlay portal. New overlays opened by the main Hermes UI should
join the shared portal instead of adding component-specific z-index or browser
bounds workarounds.

### Transport model

Because the current WebUI server is stdlib HTTP and already uses SSE, the first
web implementation should prefer:

- POST endpoints for commands/input,
- GET/SSE endpoint for lifecycle/events/frame notifications,
- separate bounded frame/crop endpoints if embedding frame bytes in SSE becomes
  too heavy.

A later accepted slice may introduce WebSocket or a dedicated browser sidecar, but
that should be justified by measured latency/complexity after the SSE/polling MVP
is evaluated.

### Proposed route sketch

Names are proposed, not final:

| Route | Method | Purpose |
|---|---:|---|
| `/api/browser-workbench/session` | POST | create or attach a browser session |
| `/api/browser-workbench/session/<id>` | GET | read session status/current URL/title/viewport |
| `/api/browser-workbench/session/<id>` | DELETE | close session and cleanup buffers |
| `/api/browser-workbench/session/<id>/navigate` | POST | navigate one scoped session to a URL from the URL bar |
| `/api/browser-workbench/session/<id>/back` | POST | move one scoped session backward in history |
| `/api/browser-workbench/session/<id>/forward` | POST | move one scoped session forward in history |
| `/api/browser-workbench/session/<id>/reload` | POST | reload one scoped session's current URL |
| `/api/browser-workbench/control` | POST | future broader stop/input control surface, if needed |
| `/api/browser-workbench/input` | POST | pointer/keyboard/wheel events |
| `/api/browser-workbench/inspect` | POST | hit-test or select element at coordinates |
| `/api/browser-workbench/ping` | POST | return sanitized composer block for selected element |
| `/api/browser-workbench/events` | GET/SSE | lifecycle, console/network, frame cursor, inspect updates |
| `/api/browser-workbench/frame/<id>` | GET | latest viewport frame or frame by cursor |
| `/api/browser-workbench/crop/<id>/<selection_id>` | GET | temporary selected-element screenshot crop |

All mutating routes should require the same authenticated WebUI session and CSRF
protection pattern as existing WebUI mutating APIs. Selection submissions should stay scoped to authenticated Browser Workbench
sessions and must not be merged with unrelated control routes.

## Data Contracts

### Session status

```json
{
  "ok": true,
  "session_id": "bw_abc123",
  "status": "starting|ready|navigating|crashed|closed|error",
  "url": "http://localhost:3000/",
  "title": "Example App",
  "backend": "local-cdp|agent-browser|browserbase|camofox|unknown",
  "viewport": { "width": 1440, "height": 900, "device_pixel_ratio": 2 },
  "capabilities": {
    "interactive_frames": true,
    "inspect": true,
    "console": true,
    "network": true,
    "screenshot_crop": false,
    "component_hints": false
  }
}
```

### Event envelope

```json
{
  "event_id": "bw_abc123:42",
  "seq": 42,
  "session_id": "bw_abc123",
  "type": "frame|navigation|console|network|inspect|error|closed",
  "created_at": 1781712000.0,
  "payload": {}
}
```

Required semantics:

- `seq` is monotonic per workbench session.
- Events are at-least-once; frontend deduplicates by `session_id + seq`.
- Reconnect can request `after=<seq>` where practical.
- Console/network payloads are redacted and truncated before they enter browser
  state or prompt attachments.

### Selection payload

Reuse the previous safe selection shape where possible so Workbench pings produce
a stable composer format:

```json
{
  "version": 1,
  "source": "workbench-cdp",
  "captured_at": "2026-06-17T12:34:56.789Z",
  "workbench": {
    "session_id": "bw_abc123",
    "selection_id": "sel_456",
    "frame_seq": 42
  },
  "page": {
    "url": "http://localhost:3000/settings",
    "title": "Settings",
    "viewport": { "width": 1440, "height": 900, "device_pixel_ratio": 2 }
  },
  "element": {
    "tag": "button",
    "id": "save",
    "classes": ["btn", "primary"],
    "selector": "button#save.btn.primary",
    "xpath": "/html/body/main/form/button[1]",
    "role": "button",
    "accessible_name": "Save changes",
    "text": "Save changes",
    "attributes": { "data-testid": "settings-save" },
    "bounds": { "x": 842, "y": 612, "width": 128, "height": 40 }
  },
  "component_hints": {
    "framework": "react|vue|svelte|unknown",
    "display_name": "SaveButton",
    "source_file": "src/components/settings/SaveButton.tsx",
    "line": "37",
    "column": "12",
    "confidence": "medium"
  },
  "diagnostics": {
    "recent_console_errors": [],
    "recent_network_errors": [],
    "crop_id": "crop_789"
  }
}
```

## Security and Privacy Model

Browser Workbench is security-sensitive because CDP/browser control can read page
content, inspect DOM, capture screenshots, and interact with local services.

Minimum requirements:

- WebUI must never expose a raw CDP endpoint or browser debugging WebSocket to
  browser JavaScript.
- Every workbench command must be scoped to an authenticated WebUI session.
- Mutating commands must use existing CSRF protections.
- Default navigation should allow localhost, loopback, RFC1918, and explicitly
  user-approved origins. Public internet navigation should be gated by settings
  and clear warnings.
- `file://`, browser-internal pages, local metadata endpoints, and OS-sensitive
  URLs should be blocked by default unless a later explicit developer setting
  allows them.
- Browser profiles should be isolated under `HERMES_WEBUI_STATE_DIR` or a
  dedicated temp/state subdirectory, not the user's normal Chrome profile.
- Screenshot crops and frame buffers should be temporary, bounded, and cleaned up
  when the session closes or expires.
- Form values, password text, cookies, localStorage, authorization headers, and
  response bodies must not be copied into composer blocks by default.
- Console/network logs should be redacted and truncated before display or prompt
  attachment.
- The UI must show when the agent has control or has requested browser actions.
- Closing the workbench session must terminate or detach the browser target and
  clear in-memory sensitive buffers.

## Phased Rollout

### Phase 0: RFC and current-state alignment

- Land this RFC and link it from the RFC index/contract docs.
- Remove the previous Browser Inspector bookmarklet/console-snippet prototype so
  the Browser Workbench is the only browser-facing UX direction.

Verification:

- Markdown links resolve locally.
- No runtime behavior changes.

### Phase 1: Browser panel shell without live browser backend

- Add a hidden/default-off Browser panel shell in `static/browser_workbench.js`.
- Add route stubs that return a clear unavailable/capability response.
- Add static tests for script inclusion, route registration, i18n keys, and no
  unsafe HTML sinks.
- Add UI that respects desktop/narrow layouts and existing theme tokens.

Verification:

- `./scripts/test.sh tests/test_browser_workbench_static.py -q`
- `npm run lint:runtime`
- Manual: panel opens/closes without console errors in desktop and mobile widths.

### Phase 2: Local session lifecycle and navigation spike

- Implement a default-off backend selection path for local CDP or reused Hermes
  Agent browser backend. Keep it behind a narrow adapter seam so route/UI
  response contracts do not change when the session-shell backend is swapped out.
- Create/close workbench sessions with isolated profiles.
- Support navigate/back/forward/reload/status.
- Do not stream interactive frames yet if that would force too much transport
  complexity; status and screenshot-on-demand are enough for this slice.

Verification:

- Focused backend tests for session creation/cleanup and blocked URL policy.
- Manual isolated-state launch with a local test page.
- Confirm no raw CDP URL is visible in browser responses.

### Phase 3: Interactive viewport MVP

- Render viewport frames in WebUI using a canvas or image surface.
- Forward click, pointer move, wheel, and keyboard events.
- Cap frame rate and payload sizes.
- Show latency/session status visibly.
- Preserve navigation and reload controls.

Verification:

- Manual local app navigation and interaction.
- Browser console has no uncaught errors.
- CPU/memory/frame-buffer caps are observed during a several-minute session.

### Phase 4: Inspect and ping-to-prompt

- Add inspect mode, hover highlight, hit-test, and click-to-select.
- Reuse the previous safe selection formatter contract where possible.
- Add screenshot crop support as temporary workbench artifact.
- Add console/network nearby summaries into the selection block.
- Insert into composer for user review; do not auto-send.

Verification:

- Focused tests for sanitizer parity and secret/form-value exclusion.
- Manual: select a button, text field, password input, image, and nested element.
- Confirm password input pings omit text/value.
- Confirm public-origin policy works as designed.

### Phase 5: DevTools-lite diagnostics

- Add console and network drawers backed by bounded event buffers.
- Add failed-request filter and console-error filter.
- Add selected-element summary panel.
- Add copy selector / copy prompt block actions.

Verification:

- Tests for truncation/redaction and bounded buffers.
- Manual local app with intentional console error and failed request.

### Phase 6: Agent integration

- Expose Browser Workbench context to agent turns as explicit prompt attachments or
  tool-accessible state.
- Let the agent request screenshots/log snippets from the active workbench session
  through a safe WebUI-mediated tool or prompt-context bridge.
- Show user-visible state when the agent controls or reads from the browser.
- Align with Hermes Agent's browser toolset rather than duplicating semantics.

Verification:

- Agent fixes a local UI bug using a workbench ping and verifies in the same
  Browser panel.
- Logs prove the agent received only sanitized workbench context.

### Phase 7: Visual editor follow-up, if validated

Only after pings, diagnostics, and shared verification are stable:

- component/source mapping through sourcemaps, JSX dev transforms, framework hooks,
  or repo search heuristics,
- style controls for selected elements,
- layout experimentation controls,
- drag/drop DOM reorder experiments,
- agent-applied code changes from visual edits.

This phase should get its own RFC or child RFC because it changes the product from
"browser debugging workbench" to "visual editor."

## Acceptance Criteria for the First Real Workbench Milestone

The first milestone that claims "Browser Workbench MVP" should prove:

- The user can open a Browser panel inside Hermes WebUI.
- The user can navigate to a local development URL through a URL bar.
- The page is visible and interactive from inside Hermes WebUI.
- The user can inspect an element and append a sanitized selection block to the
  composer.
- The selection includes URL, title, selector, role/name, text where safe, bounds,
  and a screenshot/crop reference where available.
- Console and network errors near the selection are summarized without leaking
  secrets or response bodies.
- The agent can use the selection block to locate relevant source files through
  normal repo tools.
- The feature works with isolated `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR`.
- Closing the workbench cleans up browser state and sensitive buffers.
- Removed Browser Inspector bookmarklet routes and assets stay absent.

## Testing Strategy

Automated tests should cover:

- route registration and response shape,
- auth/CSRF enforcement,
- URL allow/block policy,
- session registry lifecycle and cleanup,
- bounded frame/log buffers,
- sanitizer parity with the previous safe selection shape,
- no live form value/password capture,
- console/network redaction/truncation,
- static JS runtime guard for `static/browser_workbench.js`,
- UI script inclusion and i18n keys.

Manual/dogfood checks should cover:

- desktop Browser panel open/close,
- narrow/mobile behavior or explicit unavailable state,
- local app navigation,
- mouse/keyboard/scroll interaction,
- inspect hover and select,
- ping to composer and user edit before send,
- console/network drawer on a page with known failures,
- agent fix-and-verify loop using isolated state.

Recommended isolated launch for dogfooding:

```bash
HERMES_HOME=/tmp/hermes-webui-browser-workbench-home \
HERMES_WEBUI_STATE_DIR=/tmp/hermes-webui-browser-workbench-state \
HERMES_WEBUI_PORT=8793 \
python3 bootstrap.py --no-browser --foreground --host 127.0.0.1 8793
```

Use the positional port because repo `.env` can override default env-based ports.
Confirm the actual bound port before interacting.

## Risks and Tradeoffs

### CDP transport complexity

CDP usually speaks over WebSocket. The current WebUI dependency set is tiny and
stdlib HTTP does not include WebSocket support. The first backend spike must avoid
silently adding a broad dependency or custom protocol stack without measuring the
benefit and maintenance cost.

### Latency and frame payload size

A browser screencast over HTTP/SSE can be heavy. The MVP should cap frame rate,
resolution, and buffer sizes. It should degrade to screenshot-on-demand if a live
stream is too expensive on a given machine.

### Security blast radius

Browser control can reach local services. The URL policy, isolated profile,
redaction, and no-raw-CDP boundary are not optional.

### Product confusion with removed Browser Inspector prototype

The UI/docs must keep Browser Workbench as the only browser-facing UX direction.
Avoid reintroducing bookmarklet or console-snippet setup UI that makes users think
an external-browser bridge is the final Cursor-like workflow.

### Desktop-shell temptation

Electron/Tauri could provide a more native embedded browser. That may be useful
later, but it is a separate product and distribution decision. WebUI should first
validate the workbench UX through a web-compatible backend stream.

## Open Questions

- Should Browser Workbench live as a center-tab mode, right-panel workbench, or a
  split view beside chat?
- Should public internet navigation be disabled by default, warning-gated, or
  controlled by a profile setting?
- Which backend should be the first implementation target: local Chromium CDP,
  `agent-browser`, Camofox, Browserbase, or a Hermes Agent browser-session API?
- Can existing Hermes Agent browser sessions be shared safely with WebUI, or must
  WebUI own separate browser sessions?
- What is the minimum acceptable live-frame rate for the MVP?
- Should the first MVP support multi-tab browsing or only one active page?
- What source-mapping contract should a framework-specific follow-up use?
- How should agent-requested browser actions be approved or displayed in chat?

## Contract Routing for Implementation PRs

Implementation PRs against this RFC should include a `Contract Routing` section
that names:

- `docs/rfcs/browser-workbench.md` for Browser Workbench product/architecture,
- `docs/UIUX-GUIDE.md` and `DESIGN.md` for panel/layout behavior,
- `docs/CONTRACTS.md` for review expectations,
- `TESTING.md` for manual browser checks,
- `hermes-run-adapter-contract.md` if agent/runtime-control semantics are changed.

Evidence should include focused tests plus manual UI proof for the relevant
viewport states. Runtime/control PRs must also name the state owner being mutated:
Workbench session registry, backend browser process/context, frame buffer,
selection queue, diagnostics buffer, prompt attachment, or agent-control bridge.
