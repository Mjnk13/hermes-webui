# Research: Chii/Chobitsu CSS parity with browser DevTools

Research date: 2026-08-26

## Conclusion

The current symptom is not a DevTools-frontend rendering bug, and recursively
walking nested `cssRules` is only a partial read-side workaround. Chii ships a
Chrome DevTools **frontend**, but the target page is backed by Chobitsu's small
JavaScript emulation of CDP rather than Chromium's inspector backend. The
emulation does not provide the source ranges, stylesheet mutation commands, or
cascade data that the real Styles pane expects.

To make Browser Workbench behave like browser DevTools, the recommended design
is to inspect the same page in a real Chromium target and connect DevTools to
that target's CDP WebSocket. Hermes already has the beginning of this design in
`CdpBrowserWorkbenchBackend`; it should become the authoritative renderer and
inspector for the session. Chii can remain only as an explicitly limited
fallback. Upgrading Chii/Chobitsu alone will not fix the issue: `v1.15.5` and
`v1.8.6` are still their latest tags and both upstream `master` branches are
identical to those tags.

## What Chii actually provides

Chii describes itself as replacing the inspector with the latest Chrome
DevTools **frontend**, while Chobitsu describes itself as a JavaScript
implementation of the Chrome DevTools Protocol ([Chii README], [Chobitsu
README]). Chii 1.15.5 bundles Chobitsu `^1.8.6` at build time ([Chii
package.json]). This distinction matters: the UI looks like Chrome DevTools,
but CSS answers and mutations come from code running inside the inspected page,
not from Blink's inspector.

The official CDP documentation says that Chrome DevTools itself uses CDP to
instrument Chromium/Chrome, and that a remotely debugged page exposes both a
`devtoolsFrontendUrl` and a `/devtools/page/{targetId}` WebSocket endpoint
([CDP overview and endpoints]). In other words, frontend parity does not imply
backend parity.

## Why the current Styles pane is incomplete

| Area | Real CDP / DevTools expectation | Chobitsu 1.8.6 behavior | Consequence |
| --- | --- | --- | --- |
| Matched cascade | `getMatchedStylesForNode` can return applicable rules, inline/attribute styles, pseudo styles, inherited chains, keyframes, registrations, at-rules, and more ([CDP CSS.pdl]). | Returns only `matchedCSSRules` plus inline style ([CSS.ts]). Its stock walker visits only each stylesheet's top-level `cssRules` ([stylesheet.ts]). | Nested rules disappear in stock Chii; even after recursively enumerating them, cascade context remains incomplete. |
| Grouping rules | A returned rule carries media, supports, container, layer, scope, nesting, and ancestor-rule metadata ([CDP CSSRule]). | The formatted rule contains only `styleSheetId`, selector text, and a declaration object ([formatMatchedCssRule]). | A blind recursive walk can include selectors under an inactive `@media`/`@supports` condition and cannot reproduce cascade-layer/container/scope semantics. |
| Property fidelity | `CSSStyle` may contain source `cssText` and a declaration `range`; each property may contain raw `text`, `range`, `important`, `parsedOk`, `disabled`, and longhands ([CDP CSSStyle/CSSProperty]). | Matched properties are reduced to `{name, value}` from CSSOM ([formatStyle], [toCssProperties]). | Disabled, invalid, duplicate, overridden source declarations and exact spelling cannot be reconstructed. A CSSOM lookup that yields `""` is sent as an empty value because there is no raw-source fallback. |
| Edit a declaration | DevTools refuses to set a declaration without both `styleSheetId` and `range`, then calls `CSS.setStyleTexts` ([CSSStyleDeclaration], [CSSModel]). | Matched rule styles have no `range` or `cssText`; `setStyleTexts` explicitly says “Only allow to edit inline style” and returns a placeholder for every other stylesheet ([Chobitsu setStyleTexts]). | Existing rule property names/values cannot be edited and attempts may appear editable before reverting/failing. |
| Edit selectors / add rules | DevTools calls `setRuleSelector`; CDP also defines `createStyleSheet`, `addRule`, `setStyleSheetText`, and `setStyleTexts` ([CDP mutation commands], [DevTools CSSRule]). | Chobitsu's CSS domain exports none of `setRuleSelector`, `createStyleSheet`, `addRule`, or `setStyleSheetText`. | Selector editing and “new rule” workflows cannot work. |
| Stylesheet source | `getStyleSheetText(styleSheetId)` returns the backend's authoritative text and mutations return updated protocol objects ([CDP stylesheet commands]). | For a regular sheet Chobitsu looks up the sheet by id, fetches only `styleSheet.href`, caches the response forever, and falls back to `""` on failure ([getStyleSheetText], [getContent]). It does not read a `<style>` owner or a constructed stylesheet by id. | Inline `<style>`, constructed/adopted sheets, failed/CORS fetches, and post-edit state can be empty, wrong, or stale. |
| Cross-origin CSS | Chromium's privileged inspector backend can report what the engine parsed. | Chobitsu accesses page-level `CSSStyleSheet.cssRules` and silently skips the whole sheet on an exception ([stylesheet.ts]). CSSOM requires `cssRules` to throw `SecurityError` when the sheet is not origin-clean ([CSSOM]). | Cross-origin sheets can never reach parity from an injected page script, regardless of recursive walking. |

The empty/missing values reported in Browser Workbench therefore have a
structural cause: Chobitsu serializes the live CSSOM rather than the stylesheet
source model used by Chromium's inspector. A protocol trace of the specific
page would be needed to name every empty declaration, but it is already proven
that the backend omits the fields DevTools needs to preserve and edit those
declarations.

The prior recursive Hermes patch fixes one item only: it discovers nested style
rules. It does not add source ranges, active grouping context, full cascade
data, or write support. It should not be considered a CSS parity fix.

## Upstream status

- Chii's still-open issue [#5] reports that styles inside `@media` do not appear
  in Elements > Styles. It has been open since 2020.
- Chii's still-open issue [#28] asks to uncheck or modify existing Styles-pane
  declarations. It has been open since 2022. The corresponding Chobitsu issue
  [#8] was closed without an implementation.
- Chobitsu PR [#7] addressed crashes/undefined output around custom properties
  and complex selector lists; its own description says these cases made the
  Styles pane display incorrectly. The current fallback to
  `getPropertyValue()` is visible in `formatStyle`, but it did not add a
  source-aware model or editing.
- Chobitsu 1.8.4 fixed only selector-priority ordering ([changelog], [issue
  #16]); 1.8.5 and 1.8.6 contain no CSS parity work. The upstream CSS test file
  is empty ([CSS.spec.js]).
- As of the research date, [Chobitsu `v1.8.6...master`] and [Chii
  `v1.15.5...master`] both report zero commits. There is no newer upstream code
  to consume.

## Viable architectures

### 1. Real Chromium target over CDP — recommended

Use one Chromium page target as both the Browser Workbench viewport and the
DevTools target:

1. Start a dedicated Chromium profile bound to loopback with a remote-debugging
   port.
2. Create one target per Workbench session and retain its target id and
   `webSocketDebuggerUrl`.
3. Render/interact with that same target (streamed surface or a native browser
   view). Do not show a proxy iframe while inspecting a separate target.
4. Dock Chrome's matching DevTools frontend, or proxy its
   `/devtools/inspector.html?ws=...` frontend and authenticated WebSocket to the
   same target.
5. Keep the raw debugging endpoint server-side, loopback-only, session-scoped,
   and access-controlled; CDP grants DOM, script, network, storage, and CSS
   mutation authority.

Chrome documents that `/json/list` supplies `devtoolsFrontendUrl` and
`webSocketDebuggerUrl`, and that `/devtools/inspector.html` is the frontend that
ships with Chrome ([CDP endpoints]). This path delegates nested rules, source
ranges, editing, inheritance, pseudo styles, cascade layers, adopted sheets,
and engine-specific behavior to Blink—the only practical route to real-browser
parity.

Hermes already launches a real browser with `--remote-debugging-port=0`, creates
targets, records their WebSocket/frontend URLs, and exposes a
`chromium-stream` renderer in [`CdpBrowserWorkbenchBackend`]. The implementation
work should complete and select that path for full DevTools sessions, rather
than extending the Chii string patch. The current session-shell path and an
explicit iframe renderer still choose Chii ([backend selection]); the WebUI
currently sends `client_renderer: 'iframe-bridge'` when creating a session
([client renderer request]). Therefore simply having the CDP class in the
repository does not give the current session real DevTools.

### 2. Chrome extension transport — for inspecting an existing user tab

If Workbench must inspect a tab already open in the user's Chrome rather than a
Hermes-owned browser, a companion extension can use `chrome.debugger`. Chrome's
official docs call it an alternate CDP transport that can attach to tabs and
mutate DOM and CSS; the official CDP page specifically recommends an extension
for a Web IDE that needs live CSS/DOM editing ([chrome.debugger], [CDP extension
guidance]). This requires the powerful `debugger` extension permission, an
explicit install/attach UX, target/frame lifecycle handling, and careful
detachment behavior. It is more operationally complex than a Hermes-owned CDP
browser, but it still uses Chrome's real CSS backend.

### 3. Fork and substantially reimplement Chobitsu — fallback only

A Chobitsu fork could improve the in-page fallback, but this is a protocol
backend project, not a small nested-rule patch. At minimum it would need:

- a stable stylesheet registry covering linked, `<style>`, constructed, and
  adopted sheets;
- a lossless CSS source parser and source-range mapping for selectors,
  declarations, duplicates, comments, invalid/disabled properties, and nested
  at-rules;
- active media/supports/container/scope/layer/nesting context and correct
  cascade ordering;
- complete protocol payloads (`origin`, headers, `cssText`, style/property
  ranges and flags, inherited/pseudo data);
- working `getStyleSheetText`, `setStyleSheetText`, `setStyleTexts`,
  `setRuleSelector`, `createStyleSheet`, and `addRule`, with cache invalidation
  and `styleSheetChanged` events;
- comprehensive browser tests (the upstream CSS test file is currently empty).

Even then, the page-level CSSOM origin-clean restriction prevents true parity
for cross-origin stylesheets, and every DevTools frontend update can introduce
new protocol expectations. This option is reasonable only for a clearly
documented limited/read-only fallback.

## Recommendation for Hermes

Treat “real Styles pane with editing” as a renderer/backend selection problem:

- make the real Chromium CDP session the full-capability mode;
- guarantee viewport and DevTools share the same target;
- keep Chii as a limited compatibility fallback and label its Styles pane as
  incomplete/read-mostly;
- remove or narrow the recursive bundle patch once CDP is authoritative, or at
  least filter grouping-rule applicability so it does not show inactive rules;
- validate parity with protocol-level tests for nested `@layer/@media`, regular
  stylesheet property rename/value edit, selector edit, inline `<style>`,
  constructed/adopted sheets, custom properties, duplicate/disabled/invalid
  declarations, inheritance/pseudo rules, and a cross-origin stylesheet.

No Chii/Chobitsu version bump available today changes this recommendation.

## Implemented bounded fallback after this research

Hermes' session-shell renderer now appends a runtime-local CSS-domain adapter
through Chobitsu's public `register('CSS', …)` extension seam. It provides the
specific fallback behavior needed by the existing iframe Workbench:

- recursively discovers stylesheet declarations nested under grouping rules;
- serializes non-empty CSSOM values, `!important`, declaration text, and source
  ranges in the shape expected by the DevTools frontend;
- applies existing declaration key/value edits back to the live
  `CSSStyleRule.style.cssText` and returns the updated style payload;
- continues delegating inline-style edits to Chobitsu's original handler.

This deliberately does not claim full parity. It cannot recover disabled,
invalid, duplicate, inaccessible cross-origin, constructed/adopted, inherited,
pseudo-element, or exact original-source declarations that page-level CSSOM
does not expose. For those cases the configured `cdp-browser` renderer remains
the full-fidelity mode; the frontend now preserves that renderer selection
instead of forcing every request back to `iframe-bridge`.

## Primary sources

- [Chii README]: https://github.com/liriliri/chii/blob/v1.15.5/README.md
- [Chobitsu README]: https://github.com/liriliri/chobitsu/blob/v1.8.6/README.md
- [Chii package.json]: https://github.com/liriliri/chii/blob/v1.15.5/package.json#L42-L46
- [CSS.ts]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/domains/CSS.ts#L114-L126
- [stylesheet.ts]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/lib/stylesheet.ts#L41-L99
- [formatMatchedCssRule]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/domains/CSS.ts#L195-L225
- [formatStyle]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/lib/stylesheet.ts#L89-L99
- [toCssProperties]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/domains/CSS.ts#L240-L260
- [Chobitsu setStyleTexts]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/domains/CSS.ts#L163-L187
- [getStyleSheetText]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/lib/stylesheet.ts#L123-L138
- [getContent]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/lib/util.ts#L54-L107
- [CDP overview and endpoints]: https://chromedevtools.github.io/devtools-protocol/#endpoints
- [CDP CSS.pdl]: https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/CSS.pdl#L802-L835
- [CDP CSSRule]: https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/CSS.pdl#L166-L204
- [CDP CSSStyle/CSSProperty]: https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/CSS.pdl#L267-L303
- [CDP stylesheet commands]: https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/CSS.pdl#L856-L862
- [CDP mutation commands]: https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/CSS.pdl#L998-L1027
- [CSSStyleDeclaration]: https://github.com/ChromeDevTools/devtools-frontend/blob/31f2967d073b1b5477d8532ffba13bc04b9cd3c8/front_end/core/sdk/CSSStyleDeclaration.ts#L57-L110
- [CSSModel]: https://github.com/ChromeDevTools/devtools-frontend/blob/31f2967d073b1b5477d8532ffba13bc04b9cd3c8/front_end/core/sdk/CSSModel.ts#L186-L201
- [DevTools CSSRule]: https://github.com/ChromeDevTools/devtools-frontend/blob/31f2967d073b1b5477d8532ffba13bc04b9cd3c8/front_end/core/sdk/CSSRule.ts#L166-L200
- [CSSOM]: https://drafts.csswg.org/cssom/#dom-cssstylesheet-cssrules
- [chrome.debugger]: https://developer.chrome.com/docs/extensions/reference/api/debugger#description
- [CDP extension guidance]: https://chromedevtools.github.io/devtools-protocol/#extension
- [#5]: https://github.com/liriliri/chii/issues/5
- [#28]: https://github.com/liriliri/chii/issues/28
- [#8]: https://github.com/liriliri/chobitsu/issues/8
- [#7]: https://github.com/liriliri/chobitsu/pull/7
- [issue #16]: https://github.com/liriliri/chobitsu/issues/16
- [changelog]: https://github.com/liriliri/chobitsu/blob/v1.8.6/CHANGELOG.md#L1-L13
- [CSS.spec.js]: https://github.com/liriliri/chobitsu/blob/v1.8.6/test/CSS.spec.js
- [Chobitsu `v1.8.6...master`]: https://github.com/liriliri/chobitsu/compare/v1.8.6...master
- [Chii `v1.15.5...master`]: https://github.com/liriliri/chii/compare/v1.15.5...master
- [`CdpBrowserWorkbenchBackend`]: ../../api/browser_workbench.py#L3899-L4267
- [backend selection]: ../../api/browser_workbench.py#L4550-L4585
- [client renderer request]: ../../static/browser_workbench.js#L2378-L2388

[Chii README]: https://github.com/liriliri/chii/blob/v1.15.5/README.md
[Chobitsu README]: https://github.com/liriliri/chobitsu/blob/v1.8.6/README.md
[Chii package.json]: https://github.com/liriliri/chii/blob/v1.15.5/package.json#L42-L46
[CDP overview and endpoints]: https://chromedevtools.github.io/devtools-protocol/#endpoints
[CDP endpoints]: https://chromedevtools.github.io/devtools-protocol/#endpoints
[CDP extension guidance]: https://chromedevtools.github.io/devtools-protocol/#extension
[CDP CSS.pdl]: https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/CSS.pdl#L802-L835
[CDP CSSRule]: https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/CSS.pdl#L166-L204
[CDP CSSStyle/CSSProperty]: https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/CSS.pdl#L267-L303
[CDP stylesheet commands]: https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/CSS.pdl#L856-L862
[CDP mutation commands]: https://github.com/ChromeDevTools/devtools-protocol/blob/master/pdl/domains/CSS.pdl#L998-L1027
[CSS.ts]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/domains/CSS.ts#L114-L126
[stylesheet.ts]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/lib/stylesheet.ts#L41-L99
[formatMatchedCssRule]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/domains/CSS.ts#L195-L225
[formatStyle]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/lib/stylesheet.ts#L89-L99
[toCssProperties]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/domains/CSS.ts#L240-L260
[Chobitsu setStyleTexts]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/domains/CSS.ts#L163-L187
[getStyleSheetText]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/lib/stylesheet.ts#L123-L138
[getContent]: https://github.com/liriliri/chobitsu/blob/v1.8.6/src/lib/util.ts#L54-L107
[CSSStyleDeclaration]: https://github.com/ChromeDevTools/devtools-frontend/blob/31f2967d073b1b5477d8532ffba13bc04b9cd3c8/front_end/core/sdk/CSSStyleDeclaration.ts#L57-L110
[CSSModel]: https://github.com/ChromeDevTools/devtools-frontend/blob/31f2967d073b1b5477d8532ffba13bc04b9cd3c8/front_end/core/sdk/CSSModel.ts#L186-L201
[DevTools CSSRule]: https://github.com/ChromeDevTools/devtools-frontend/blob/31f2967d073b1b5477d8532ffba13bc04b9cd3c8/front_end/core/sdk/CSSRule.ts#L166-L200
[CSSOM]: https://drafts.csswg.org/cssom/#dom-cssstylesheet-cssrules
[chrome.debugger]: https://developer.chrome.com/docs/extensions/reference/api/debugger#description
[changelog]: https://github.com/liriliri/chobitsu/blob/v1.8.6/CHANGELOG.md#L1-L13
[issue #16]: https://github.com/liriliri/chobitsu/issues/16
[#5]: https://github.com/liriliri/chii/issues/5
[#28]: https://github.com/liriliri/chii/issues/28
[#8]: https://github.com/liriliri/chobitsu/issues/8
[#7]: https://github.com/liriliri/chobitsu/pull/7
[CSS.spec.js]: https://github.com/liriliri/chobitsu/blob/v1.8.6/test/CSS.spec.js
[Chobitsu `v1.8.6...master`]: https://github.com/liriliri/chobitsu/compare/v1.8.6...master
[Chii `v1.15.5...master`]: https://github.com/liriliri/chii/compare/v1.15.5...master
[`CdpBrowserWorkbenchBackend`]: ../../api/browser_workbench.py#L3899-L4267
[backend selection]: ../../api/browser_workbench.py#L4550-L4585
[client renderer request]: ../../static/browser_workbench.js#L2378-L2388
