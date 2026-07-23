"""Real-browser regression coverage for lazy legacy Worklog hydration.

Loading or switching to a tool-heavy conversation used to synchronously call
``buildToolCard`` for every historical legacy activity card even though each
Worklog was collapsed.  The renderer could spend tens of seconds building DOM
that a later Anchor-scene pass immediately replaced.
"""

import pytest


def test_collapsed_legacy_worklog_materializes_only_on_expand(base_url):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - optional browser dependency
        pytest.skip("playwright is unavailable; run the Worklog browser test")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as exc:  # pragma: no cover - browser binary missing
            pytest.skip(f"playwright chromium is unavailable: {exc}")

        page = browser.new_page(viewport={"width": 1100, "height": 800})
        page.goto(base_url, wait_until="domcontentloaded")
        page.wait_for_function(
            "typeof renderMessages==='function' && "
            "typeof _toggleActivityGroup==='function' && "
            "typeof buildToolCard==='function'"
        )
        result = page.evaluate(
            """() => {
              const sid = 'legacy-worklog-lazy-browser-test';
              window.chatActivityMode = () => 'compact_worklog';
              window.isTransparentStream = () => false;
              window._virtualizeTranscript = false;
              window._loadingSessionId = null;
              if (typeof _sessionHtmlCache !== 'undefined') _sessionHtmlCache.clear();

              S.session = {session_id: sid, tool_calls: []};
              S.messages = [
                {role: 'user', content: 'Inspect the repository.'},
                {role: 'assistant', content: 'I will inspect the relevant files.'},
                {role: 'assistant', content: 'The inspection is complete.'},
              ];
              S.toolCalls = Array.from({length: 12}, (_, index) => ({
                name: index % 2 ? 'terminal' : 'search_files',
                tid: `legacy-lazy-tool-${index}`,
                assistant_msg_idx: 1,
                args: index % 2
                  ? {command: `printf tool-${index}`}
                  : {path: 'static', query: `needle-${index}`},
                snippet: `tool-${index} result\\n${'output '.repeat(80)}`,
                done: true,
                status: 'completed',
              }));
              S.busy = true;
              S.activeStreamId = null;
              delete INFLIGHT[sid];

              renderMessages();

              const group = document.querySelector(
                '[data-activity-disclosure-key="assistant:1"]'
              );
              if (!group) return {found: false};
              const summary = group.querySelector(
                '.tool-worklog-summary,.tool-call-group-summary'
              );
              const sourceSegment = document.querySelector(
                '.assistant-segment[data-msg-idx="1"]'
              );
              const finalSegment = document.querySelector(
                '.assistant-segment[data-msg-idx="2"]'
              );
              const before = {
                collapsed: group.classList.contains('tool-call-group-collapsed'),
                deferred: group.getAttribute('data-worklog-rows-deferred'),
                legacyDeferred: group.getAttribute(
                  'data-legacy-worklog-steps-deferred'
                ),
                cardCount: group.querySelectorAll('.tool-card-row').length,
                sourceFolded: !!sourceSegment &&
                  sourceSegment.classList.contains('assistant-segment-worklog-source') &&
                  sourceSegment.hidden,
                finalFolded: !!finalSegment &&
                  finalSegment.classList.contains('assistant-segment-worklog-source'),
              };

              _toggleActivityGroup(summary);

              return {
                found: true,
                before,
                after: {
                  collapsed: group.classList.contains('tool-call-group-collapsed'),
                  deferred: group.getAttribute('data-worklog-rows-deferred'),
                  legacyDeferred: group.getAttribute(
                    'data-legacy-worklog-steps-deferred'
                  ),
                  cardCount: group.querySelectorAll('.tool-card-row').length,
                  sourceFolded: !!sourceSegment &&
                    sourceSegment.classList.contains('assistant-segment-worklog-source') &&
                    sourceSegment.hidden,
                  text: group.textContent,
                },
              };
            }"""
        )
        browser.close()

    assert result["found"] is True
    assert result["before"] == {
        "collapsed": True,
        "deferred": "1",
        "legacyDeferred": "1",
        "cardCount": 0,
        "sourceFolded": True,
        "finalFolded": False,
    }
    assert result["after"]["collapsed"] is False
    assert result["after"]["deferred"] is None
    assert result["after"]["legacyDeferred"] is None
    assert result["after"]["cardCount"] == 12
    assert result["after"]["sourceFolded"] is True
    assert "tool-11 result" in result["after"]["text"]
