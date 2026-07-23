# RFC: Browser Workbench

- **Status:** Proposed
- **Author:** @franksong2702
- **Created:** 2026-06-17
- **Tracking issue:** TBD
- **Related docs:** [`docs/UIUX-GUIDE.md`](../UIUX-GUIDE.md), [`DESIGN.md`](../../DESIGN.md), [`hermes-run-adapter-contract.md`](hermes-run-adapter-contract.md), [`chii-devtools-css-parity-research.md`](chii-devtools-css-parity-research.md)
- **Related external references:** Hermes Agent browser automation docs, Chrome DevTools Protocol, MDN `X-Frame-Options`, MDN CSP `frame-ancestors`

## RFC Positioning

This RFC defines the intended product and architecture direction for an
embedded browser surface inside Hermes WebUI.

The previous Browser Inspector bookmarklet/console-snippet prototype is being
removed so the product direction can stay focused on the embedded Browser
Workbench. The target experience is a real browser tab/pane inside Hermes WebUI
with a URL bar, interactive viewport, inspect tools, diagnostics, and a direct
element-ping-to-composer workflow.

This RFC is a design direction and implementation gate. It does not authorize a
large speculative browser rewrite in one PR. The implementation should proceed in
small, reversible slices, preserving WebUI's no-build-step Python + vanilla JS
architecture unless a later accepted slice explicitly justifies a new runtime
boundary.

## Problem

A browser workbench is most useful when the user, browser, codebase, and agent
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

- Hermes Agent browser automation docs:
  `https://hermes-agent.nousresearch.com/docs/user-guide/features/browser`
- Chrome DevTools Protocol: `https://chromedevtools.github.io/devtools-protocol/`
- MDN `X-Frame-Options`:
  `https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Frame-Options`
- MDN CSP `frame-ancestors`:
  `https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy/frame-ancestors`

Key findings:

- A first-class browser workspace should surface screenshots, console logs,
  network traffic, and browser actions in the same authenticated UI.
- Visual inspection is most useful when the web app, codebase, and inspection
  controls share one workspace and selected elements can be attached to prompts.
- Hermes Agent already has browser automation backends, including local
  Chromium-family CDP attachment, but those are currently agent-facing tools, not
  a WebUI browser workspace.
- A plain iframe preview cannot be the primary architecture because many apps set
  `X-Frame-Options` or CSP `frame-ancestors`, and cross-origin iframe DOM access
  cannot support reliable inspection, devtools, or source mapping.
- A plain iframe preview may be used as a
  clearly-labeled compatibility fallback for frameable local development pages;
  it must not be presented as the full Browser Workbench renderer.
- A CDP-backed in-browser workbench can provide the richer interactive renderer
  while keeping Browser Workbench inside the existing web app.

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

- Do not build a full drag/drop visual editor in the first implementation slice.
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
instead of an interactive browser.

The UI should make this clear when latency is present. It should avoid pretending
that a remote screencast is a native DOM iframe when it is actually a streamed
browser.

During early slices, a restored or navigated session may show an iframe fallback
for the current URL so frameable local apps can be viewed before the CDP
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
CDP work.

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
- Browser Workbench tabs should share one dedicated persistent profile so
  same-origin cookies, localStorage, IndexedDB, Cache Storage, service workers,
  and HTTP cache behave like normal browser tabs. Session storage, navigation
  history, scroll, zoom, focus, and current URL remain target-local. The shared
  Workbench profile must remain isolated from the Hermes shell and the user's
  normal Chrome profile.
- The normal-browser iframe-proxy fallback provides the cookie subset of that
  profile contract. One backend-owned target cookie jar is shared across iframe
  tabs, while normal cookie domain/path/secure/expiry rules continue to isolate
  target origins. The jar is atomically persisted with owner-only permissions
  under the active WebUI state directory, survives WebUI page and backend
  restarts, and is cleared profile-wide only by the explicit clear-cookies
  action. Closing one lifecycle tab must not clear it. Hermes/WebUI cookies are
  never copied into this target jar or forwarded to target servers. Other site
  storage such as localStorage, IndexedDB, Cache Storage, service workers, and
  HTTP cache remains outside the iframe fallback's persistent-profile contract;
  a CDP-backed renderer owns that full behavior.
- The iframe proxy keeps outbound waits bounded without treating ordinary local
  dev-server compilation as a bad gateway. Loopback targets receive a 120-second
  cold-start deadline, while non-loopback targets retain the shorter 15-second
  deadline. The proxy does not retry or replay a timed-out request, including
  mutations; a target that exceeds its applicable deadline still receives the
  existing diagnostic 502 response.
- The iframe proxy owns request rewriting for both server-rendered URLs and
  runtime-created subresources. Root-relative script/style/worker/media requests
  stay unchanged so hydration sees the same attributes the target server
  rendered (including Next/Turbopack `/_next` assets). They may be recovered only when their
  same-origin referrer identifies a live proxy session and the browser marks the
  request as a non-document subresource. Ordinary WebUI routes and document
  navigations must not be internally served by this recovery path. Once an
  external stylesheet is recovered or proxied, every nested CSS `url(...)`
  reference must be rewritten to an explicit session/frame-scoped proxy URL.
  Otherwise a root-relative font or image uses the clean stylesheet URL as its
  referrer, loses the owning proxy session, and falls through to the WebUI origin.
  This nested rewrite must not mutate framework-owned HTML attributes or inline
  style text in ordinary same-origin frames. If a target
  app uses an unpatchable clean `location.href`/`location.assign` navigation,
  a document or iframe request with a same-origin live proxy referrer is instead
  redirected to its canonical `/browser-proxy/` URL before shell routing. This
  keeps the browser location, later referrers, and target request on one
  authoritative proxy URL. When the target itself redirects a document, the
  iframe proxy likewise redirects the browser to the final target's canonical
  proxy URL before serving its HTML; it must not hydrate final-route markup
  under the originally requested pathname.
- An iframe-proxied target must not observe the Hermes shell origin in object
  URLs created for device files or non-executable preview blobs. Before target
  scripts run, the bridge exposes a `blob:<target-origin>/...` alias while
  retaining the browser-created shell-origin object URL as document-local
  transport state. Fetch/XHR, common DOM resource properties and attributes,
  inline/computed/constructable CSS URL values, media/download elements, and
  worker constructors resolve only aliases created by that frame back to their
  native owner; reads map them back to the target alias. Arbitrary text and
  `data-*` attributes are not browser URL consumers and must remain untouched.
  Alias IDs come only from a cryptographically secure browser RNG; creation
  fails closed if one cannot be allocated. `URL.revokeObjectURL()` removes the
  active resolver entries and revokes the native URL. A string-only reverse
  display tombstone remains until document teardown so an existing DOM/CSS
  getter cannot reveal the Hermes origin after revoke; it retains neither the
  `File`/`Blob` object nor its bytes. Unknown aliases fail closed, and neither
  active mappings, display tombstones, nor blob bytes are persisted in
  backend/session state or shared with another frame. Multipart/FormData uploads
  continue to carry the original `File` or `Blob` bytes and filename rather than
  either URL. Generated executable Blob modules retain their native object URL
  because language-level dynamic import cannot cross the alias boundary; a
  device `File` remains target-aliased regardless of its declared MIME type.
- A nested preview iframe whose `sandbox` omits `allow-same-origin` has an opaque
  browser origin and therefore cannot supply the referrer needed by that normal
  root-relative recovery path. The proxy must not weaken the target sandbox.
  Instead it marks that subframe in path-scoped transport state and gives only
  that opaque frame's initial script/style/media URLs explicit proxy context.
  This applies to server-rendered frames, dynamically inserted frames, and
  `srcdoc` previews. Non-opaque frames keep their original SSR attributes for
  framework hydration. The bridge must be injected before target body scripts
  even when a document omits `<head>`, so dynamic preview creation crosses this
  boundary before its first navigation.
- Proxy session/frame identity lives in the `/browser-proxy/_hermes/.../`
  transport path, not `location.search`. The visible query and hash belong only
  to the target URL, so app routers cannot ingest or forward Hermes metadata.
  Legacy query-metadata proxy URLs remain readable for in-flight tabs, but new
  URLs and form actions use path-scoped transport identity.
- Framework-owned link and form attributes remain target-relative through
  hydration. The `/browser-proxy/` URL is transport state only: native history,
  fetch, form, and unclaimed-link boundaries add it, while the proxy removes it
  before contacting the target server. If an App Router hook composes an intended
  route with the transport pathname, both bridge and backend canonicalize a
  same-target nested URL and reject a nested URL that names another origin.
- In the path-scoped iframe fallback, a successful, non-prefetch Next RSC GET
  navigation whose target differs from the current target route is materialized
  as a canonical proxy document navigation. Only the latest RSC navigation may
  commit, so a superseded response cannot replace a newer locale or route.
  Same-route refreshes, prefetches, POST/server actions, non-RSC responses, and
  cross-origin targets remain on their existing fetch paths.
- Every same-origin proxy iframe load reconciles its actual transport location
  back to the target URL after validating the active path-scoped session ID.
  This is the URL fallback when a non-HTML or failed destination cannot execute
  the injected metadata bridge; bridge metadata remains authoritative for richer
  title, favicon, and native-history capabilities when it is available.
- Before framework scripts run, the iframe bridge makes `URL.pathname` report
  the target pathname for shell-proxy URL instances while leaving the actual
  `location` and network transport untouched. This keeps pathname-derived SSR
  active state identical during hydration. Proxy internals must identify their
  own transport from unmodified URL identity such as `href`, never from this
  shimmed pathname. Chromium's Navigation API is also used to cancel same-target
  absolute `location.href`/`location.assign` navigations and reissue them through
  the canonical proxy URL before target frame headers can block the iframe.
  Valid HTTP(S) navigation to another origin follows the same proxy boundary;
  it must not escape into a direct cross-origin iframe load and lose bridge
  metadata. An already-proxied history update must remain same-document.
- Each Browser Workbench tab owns a bounded target-route stack and current
  index. That state is persisted with the tab in WebUI localStorage, contains
  only sanitized HTTP(S) target URLs (never proxy transport URLs), and survives
  a WebUI reload. A committed ordinary navigation truncates the forward tail;
  a back/forward traversal moves the index without truncating that tail.
- Persisted tabs restore as suspended history, not live sessions. Selecting a
  restored tab or reopening the Browser panel after a WebUI/backend restart must
  not create a session or issue a target request. Only an explicit Reload,
  back/forward traversal, or newly submitted URL may materialize that tab.
- New browsing-context requests from an iframe target (`target="_blank"`,
  `window.open`, and equivalent modifier/middle-click link gestures) remain
  inside Browser Workbench. The scoped bridge sends only a sanitized HTTP(S)
  target URL to the parent, which creates, activates, and explicitly navigates a
  new Workbench tab. The iframe sandbox does not retain native popup permission;
  invalid schemes and credential-bearing URLs fail closed rather than escaping
  into the user's Chrome/Opera window.
- A docked DevTools panel and its page surface share the same tab owner. Tab
  activation must reattach that tab's preserved iframe before rendering its
  DevTools panel, so switching tabs cannot pair one tab's page with another
  tab's diagnostics and does not reload either preserved iframe.
- Zoom projection follows that same tab ownership. Adjusting or restoring one
  tab must mutate only the iframe inside that tab's preserved surface, never the
  first cached iframe in the shared viewport host, and must not reload the page.
- Iframe Ping rectangles are reported in the target document's layout-viewport
  CSS pixels. Parent highlights must project them through the rendered iframe's
  visual-to-layout scale, including non-100% zoom, and an already visible hover
  highlight must be reprojected immediately when zoom changes. The transient
  highlight snapshot is cleared with its owning surface/navigation lifecycle and
  is not persisted as durable tab state.
- Reload, back, and forward prefer the live iframe history only when Navigation
  API metadata proves that its adjacent target URL matches the tab-owned route
  entry. The bridge also publishes sanitized adjacent URLs and
  push/replace/traverse mode so same-document app-router history can be
  reconciled without exposing
  proxy paths. After a WebUI reload, or whenever native adjacency is unknown,
  the Workbench rematerializes the saved adjacent target URL while retaining
  traversal intent. Native capability flags are supplemental and cannot erase
  a known back/forward route from the persisted stack; the session-shell
  backend is likewise not authoritative for iframe route history. Explicit
  clear-history resets both renderer/backend history and the tab-owned stack to
  its current target URL.
- Native form fallback must preserve proxy session metadata without mutating SSR
  form attributes before framework hydration. The bridge prepares the form in
  the capture-phase submit handler: the proxied action carries path-scoped
  transport identity, while password-bearing forms are coerced to POST before
  native submission so credentials cannot enter a URL or request log. No
  proxy-only controls are inserted into the target form body or query.
  Proxy-generated error pages use a frame-safe,
  restrictive CSP so a missing/closed session remains visible as a diagnostic
  instead of becoming an opaque iframe block.
- Screenshot crops and frame buffers should be temporary, bounded, and cleaned up
  when the session closes or expires.
- Form values, password text, cookies, localStorage, authorization headers, and
  response bodies must not be copied into composer blocks by default.
- Console/network logs should be redacted and truncated before display or prompt
  attachment.
- The UI must show when the agent has control or has requested browser actions.
- In the public capability payload, top-level `enabled` means a full embedded
  browser renderer is present; it is not the availability verdict for every
  Browser Workbench renderer. The UI treats a `limited` backend as usable only
  when the launcher is enabled and the capability set proves session lifecycle,
  navigation, and at least one render transport such as `iframe_bridge`.
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
an external-browser bridge is the final Browser Workbench workflow.

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
