"""Real-browser coverage for the incremental running shell-output renderer."""

import pytest


def test_running_shell_output_stays_incremental_and_keeps_component_identity(base_url):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - optional browser dependency
        pytest.skip("playwright is unavailable; run the live shell-output browser test")

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
            "typeof buildToolCard==='function' && typeof appendLiveToolOutputChunk==='function' && typeof _autoScrollFollow!=='undefined'"
        )
        result = page.evaluate(
            """async () => {
              const priorTurn = document.getElementById('liveAssistantTurn');
              if (priorTurn) priorTurn.id = 'liveAssistantTurn-shell-stream-prior';
              const priorSession = S.session;
              const priorStream = S.activeStreamId;
              const sessionId = 'shell-stream-browser-test';
              const streamId = 'shell-stream-browser-test-stream';
              S.session = {session_id: sessionId};
              S.activeStreamId = streamId;

              const tc = {
                name: 'terminal',
                tid: 'shell-stream-browser-tool',
                args: {command: 'pnpm exec tsx --test tests/*.test.ts'},
                done: false,
                result_metadata: {stdout: ''},
              };
              const turn = document.createElement('div');
              turn.id = 'liveAssistantTurn';
              const row = buildToolCard(tc);
              row.dataset.liveTid = tc.tid;
              const card = row.querySelector('.tool-output-card');
              if (card) card.classList.add('open');
              turn.appendChild(row);
              document.body.appendChild(turn);
              const viewport = row.querySelector('[data-tool-output-stream="stdout"] .tool-output-terminal-output');
              viewport.style.height = '220px';
              viewport.style.maxHeight = '220px';
              viewport.style.overflow = 'auto';
              const code = viewport.querySelector('code');

              let formatterCalls = 0;
              const originalFormatter = _toolOutputFormattedHtml;
              _toolOutputFormattedHtml = text => {
                formatterCalls += 1;
                return originalFormatter(text);
              };
              const heartbeatGaps = [];
              const visibleTails = [];
              let lastBeat = performance.now();
              const heartbeat = setInterval(() => {
                const now = performance.now();
                heartbeatGaps.push(now - lastBeat);
                lastBeat = now;
                visibleTails.push(code.textContent.slice(-80));
              }, 16);

              let expectedWindow = '';
              const emit = async (start, count, delay = 1) => {
                for (let index = start; index < start + count; index += 1) {
                  const chunk = Array.from({length: 60}, (_, line) =>
                    `PASS src/Test${index}-${line}.test.ts:42:17 completed successfully\\n`
                  ).join('');
                  expectedWindow = (expectedWindow + chunk).slice(-450000);
                  appendLiveToolOutputChunk(tc, 'stdout', chunk, {sessionId, streamId});
                  await new Promise(resolve => setTimeout(resolve, delay));
                }
              };

              await emit(0, 130);
              await new Promise(resolve => setTimeout(resolve, 180));
              flushLiveToolOutputChunks(tc, {sessionId, streamId});
              const orderedInitialTail = expectedWindow.endsWith(row._liveOutputTextStates.stdout.textNode.data);

              viewport.scrollTop = Math.max(1, Math.floor((viewport.scrollHeight - viewport.clientHeight) / 2));
              const inspectedTop = viewport.scrollTop;
              await emit(130, 20, 2);
              await new Promise(resolve => setTimeout(resolve, 120));
              flushLiveToolOutputChunks(tc, {sessionId, streamId});
              const preservedManualScroll = Math.abs(viewport.scrollTop - inspectedTop) <= 1;

              viewport.scrollTop = viewport.scrollHeight;
              await emit(150, 40, 2);
              await new Promise(resolve => setTimeout(resolve, 120));
              flushLiveToolOutputChunks(tc, {sessionId, streamId});
              const followsTail = viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop <= 32;
              const orderedFinalTail = expectedWindow.endsWith(row._liveOutputTextStates.stdout.textNode.data);

              appendLiveToolOutputChunk(tc, 'stdout', '\\u001b[32mBuild ', {sessionId, streamId});
              appendLiveToolOutputChunk(tc, 'stdout', 'succeeded\\u001b[0m\\n', {sessionId, streamId});
              appendLiveToolOutputChunk(tc, 'stdout', 'last pending line\\n', {sessionId, streamId});
              flushLiveToolOutputChunks(tc, {sessionId, streamId, final: true});
              const ansiSafeWhileRunning = code.textContent.includes('Build succeeded')
                && code.textContent.includes('last pending line')
                && !code.textContent.includes('\\u001b');

              clearInterval(heartbeat);
              const runningFormatterCalls = formatterCalls;
              _toolOutputFormattedHtml = originalFormatter;
              const beforePre = viewport;
              const finalStdout = _materializeLiveToolOutputRawPreview(row);
              const completed = {
                ...tc,
                done: true,
                status: 'completed',
                snippet: finalStdout,
                result_metadata: {exit_code: 0},
              };
              const replacement = buildToolCard(completed);
              replacement.dataset.liveTid = tc.tid;
              const reconciled = _reconcileLiveStructuredCommandCard(row, replacement);
              const afterPre = row.querySelector('[data-tool-output-stream="stdout"] .tool-output-terminal-output');

              const value = {
                orderedInitialTail,
                orderedFinalTail,
                preservedManualScroll,
                followsTail,
                ansiSafeWhileRunning,
                runningFormatterCalls,
                visibleFlushes: visibleTails.reduce(
                  (count, value, index) => count + (index > 0 && value !== visibleTails[index - 1] ? 1 : 0),
                  0,
                ),
                maxHeartbeatGap: Math.max(0, ...heartbeatGaps),
                childNodesWhileRunning: code.childNodes.length,
                reconciled,
                sameViewportAfterCompletion: beforePre === afterPre,
                settledStateReleased: !row._liveOutputScheduleState && !row._liveOutputRawPreviewState,
                completedStatus: row.getAttribute('data-tool-execution-status'),
                rawPreservedAfterCompletion: row._toolOutputRaw === finalStdout,
              };
              turn.remove();
              if (priorTurn) priorTurn.id = 'liveAssistantTurn';
              S.session = priorSession;
              S.activeStreamId = priorStream;
              return value;
            }"""
        )
        browser.close()

    assert result["orderedInitialTail"] is True
    assert result["orderedFinalTail"] is True
    assert result["preservedManualScroll"] is True
    assert result["followsTail"] is True
    assert result["ansiSafeWhileRunning"] is True
    assert result["runningFormatterCalls"] == 0
    assert result["visibleFlushes"] >= 8
    assert result["maxHeartbeatGap"] < 200
    assert result["childNodesWhileRunning"] <= 2
    assert result["reconciled"] is True
    assert result["sameViewportAfterCompletion"] is True
    assert result["settledStateReleased"] is True
    assert result["completedStatus"] == "completed"
    assert result["rawPreservedAfterCompletion"] is True


def test_new_anchor_activity_keeps_nested_read_and_raw_scroll_viewports(base_url):
    """A new activity row must not remount already-visible tool result panes."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - optional browser dependency
        pytest.skip("playwright is unavailable; run the tool-scroll browser test")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as exc:  # pragma: no cover - browser binary missing
            pytest.skip(f"playwright chromium is unavailable: {exc}")
        page = browser.new_page(viewport={"width": 1100, "height": 850})
        page.goto(base_url, wait_until="domcontentloaded")
        page.wait_for_function(
            "typeof renderLiveAnchorActivityScene==='function' && typeof buildToolCard==='function'"
        )
        result = page.evaluate(
            """async () => {
              window._autoScrollFollow = false;
              window._messageUserUnpinned = false;
              window._scrollPinned = false;
              window._nearBottomCount = 0;
              const priorTurn = document.getElementById('liveAssistantTurn');
              if (priorTurn) priorTurn.id = 'liveAssistantTurn-scroll-prior';
              const priorSession = S.session;
              const priorStream = S.activeStreamId;
              const priorMode = window.chatActivityMode;
              window.chatActivityMode = () => 'compact_worklog';
              S.session = {session_id: 'nested-scroll-browser-test'};
              S.activeStreamId = 'nested-scroll-browser-stream';

              const payload = count => ({
                total_count: count,
                matches_format: 'path-grouped',
                matches_text: Array.from(
                  {length: count},
                  (_, index) => `  ${index + 1}: ${'matching content '.repeat(20)}`,
                ).join('\\n'),
              });
              const toolRow = count => ({
                row_id: 'search-tool-row',
                role: 'tool',
                source_event_type: 'tool_completed',
                status: 'completed',
                tool_call_id: 'search-tool-call',
                tool: {
                  name: 'search_files',
                  tid: 'search-tool-call',
                  args: {query: 'scroll probe'},
                  result: payload(count),
                  done: true,
                },
              });

              const firstOk = renderLiveAnchorActivityScene(
                S.activeStreamId,
                {version: 'activity_scene_v1', activity_rows: [toolRow(180)]},
                {sessionId: S.session.session_id},
              );
              const first = document.querySelector(
                '#liveAssistantTurn [data-anchor-row-id="search-tool-row"]'
              );
              first.querySelector('.tool-card').classList.add('open');
              first.querySelector('.tool-output-content-document').open = true;
              first.querySelector('.tool-output-raw-toggle').click();
              const body = first.querySelector('.tool-output-content-body');
              const raw = first.querySelector('.tool-output-raw');
              body.scrollTop = 420;
              raw.scrollTop = 150;

              const secondOk = renderLiveAnchorActivityScene(
                S.activeStreamId,
                {
                  version: 'activity_scene_v1',
                  activity_rows: [
                    toolRow(220),
                    {row_id: 'reasoning-2', role: 'thinking', source_event_type: 'reasoning', text: 'Continuing'},
                  ],
                },
                {sessionId: S.session.session_id},
              );
              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
              const current = document.querySelector(
                '#liveAssistantTurn [data-anchor-row-id="search-tool-row"]'
              );
              const nextBody = current.querySelector('.tool-output-content-body');
              const nextRaw = current.querySelector('.tool-output-raw');
              const detail = current.querySelector('.tool-card-detail');
              detail.style.height = '120px';
              detail.style.maxHeight = '120px';
              detail.style.overflow = 'auto';
              detail.scrollTop = 60;
              nextBody.scrollTop = 420;
              nextRaw.scrollTop = 150;

              // A background/throttled Electron renderer can defer the
              // post-layout rAF restore for several seconds. It must not replay
              // the stale pre-interaction scroll state after the user has
              // inspected another position meanwhile.
              detail.scrollTop = 0;
              nextBody.scrollTop = 0;
              nextRaw.scrollTop = 0;
              const staleState = _captureWorklogDetailDisclosureState(current);
              const delayedFrames = [];
              const nativeRequestAnimationFrame = window.requestAnimationFrame;
              window.requestAnimationFrame = callback => {
                delayedFrames.push(callback);
                return delayedFrames.length;
              };
              _restoreLiveToolPresentation(current, staleState);
              detail.dispatchEvent(new WheelEvent('wheel', {bubbles: true, deltaY: 20}));
              nextBody.dispatchEvent(new WheelEvent('wheel', {bubbles: true, deltaY: 20}));
              nextRaw.dispatchEvent(new WheelEvent('wheel', {bubbles: true, deltaY: 20}));
              detail.scrollTop = 60;
              nextBody.scrollTop = 420;
              nextRaw.scrollTop = 150;
              for (const callback of delayedFrames.splice(0)) callback(performance.now());

              const inspectedState = _captureWorklogDetailDisclosureState(current);
              _restoreLiveToolPresentation(current, inspectedState);
              detail.scrollTop = 0;
              nextBody.scrollTop = 0;
              nextRaw.scrollTop = 0;
              await Promise.resolve();
              const microtaskDetailTop = detail.scrollTop;
              const microtaskBodyTop = nextBody.scrollTop;
              const microtaskRawTop = nextRaw.scrollTop;
              // Chromium/Electron can apply another native reset after the
              // microtask/layout pass, so the coalesced frame is a second
              // backstop. Keep both phases covered without changing the
              // authoritative captured state.
              detail.scrollTop = 0;
              nextBody.scrollTop = 0;
              nextRaw.scrollTop = 0;
              for (const callback of delayedFrames.splice(0)) callback(performance.now());
              window.requestAnimationFrame = nativeRequestAnimationFrame;
              const value = {
                firstOk,
                secondOk,
                sameRow: current === first,
                sameBody: nextBody === body,
                sameRaw: nextRaw === raw,
                bodyTop: nextBody.scrollTop,
                rawTop: nextRaw.scrollTop,
                detailsOpen: current.querySelector('.tool-output-content-document').open,
                rawOpen: !nextRaw.hidden,
                updated: nextBody.textContent.includes('220:'),
                delayedDetailTop: detail.scrollTop,
                delayedBodyTop: nextBody.scrollTop,
                delayedRawTop: nextRaw.scrollTop,
                microtaskDetailTop,
                microtaskBodyTop,
                microtaskRawTop,
              };
              document.getElementById('liveAssistantTurn')?.remove();
              if (priorTurn) priorTurn.id = 'liveAssistantTurn';
              S.session = priorSession;
              S.activeStreamId = priorStream;
              window.chatActivityMode = priorMode;
              return value;
            }"""
        )
        browser.close()

    assert result == {
        "firstOk": True,
        "secondOk": True,
        "sameRow": True,
        "sameBody": True,
        "sameRaw": True,
        "bodyTop": 420,
        "rawTop": 150,
        "detailsOpen": True,
        "rawOpen": True,
        "updated": True,
        "delayedDetailTop": 60,
        "delayedBodyTop": 420,
        "delayedRawTop": 150,
        "microtaskDetailTop": 60,
        "microtaskBodyTop": 420,
        "microtaskRawTop": 150,
    }
