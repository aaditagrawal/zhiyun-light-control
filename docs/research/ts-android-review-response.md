# Browser consumer review response

Implemented and tested on 2026-10-23. Scope: `examples/browser/app.ts`, its rebuilt `app.js`, the browser regression harness and saved results. SDK sources, Android files/config/native project, Python, dependency locks, running servers, real bridge tokens, Bluetooth hardware and camera were not changed or accessed by this implementation task.

## Fixes

- **Page lifecycle:** `release()` now clears actual readback, live-event status, write verification, timers, queued controls, draft state, device choices, subscriptions and client references synchronously, before any asynchronous direct-BLE teardown. Both pagehide and persisted pageshow invalidate the session. Restored controls stay disabled until an explicit bridge attach or Bluetooth chooser establishes fresh readback. There is no automatic reconnect.
- **Late asynchronous results:** connection/scan/read operations capture their session epoch and cancellation signal. Every asynchronous completion, error and busy-state cleanup checks that epoch. Late chooser results dispose their unused transport without opening GATT. Disconnect cleanup retains only the old client and never changes later session DOM or releases a newer client. Selecting a bridge device starts a fresh epoch, including timestamp ordering.
- **JSON/SSE ordering:** connected snapshots need complete brightness/CCT/power readback and a valid date. Older timestamps, conflicting equal timestamps, undated connected states and invalid-date connected states cannot replace accepted readback. Identical equal-timestamp duplicates remain harmless. Each fresh session resets the timestamp watermark. A disconnect event is authoritative even without a timestamp: it ends the session and cancels outstanding work, preventing an older HTTP response from restoring a connected display.
- **Draft and write outcomes:** rejected HTTP results cannot clear a requested draft or claim that the displayed state matches it. Write confirmation compares the requested patch with the currently accepted readback. If newer SSE reports different values before PATCH JSON arrives, the UI retains the newer actual values and the requested draft, keeps Apply available, and reports the difference. Clean drafts follow accepted newer readback; unsent drafts remain intact. Later SSE changes replace old write-verification wording with current readback wording.

The Android review's 5% text correction was already made by the parent. Read-only inspection confirms `apps/android/App.tsx:189` now says 5%. This task did not edit Android or claim physical Android validation.

## Regression evidence

The browser harness runs the real HTML and built application bundle in an isolated iframe per case, using native browser Promise, Response, ReadableStream, EventTarget and PageTransitionEvent APIs. All resource tags are removed before loading, fetch is replaced before the app evaluates, and every chooser/GATT interaction uses a fake. Only a dummy token is supplied. Fake requests include held JSON responses, streamed SSE and protocol-framed GATT notifications; no native fetch or real Bluetooth path is called.

Browser: Chromium 154 on Linux, through the T3 collaborative preview. Only newly created test tab `tab_z_363ef931-953c-4deb-8624-ffe6dcd9b892` was operated. The parent's tab was not operated.

- Initial suite against the preserved pre-fix bundle: **5 passed, 8 failed across 13 cases**. The parent's older-SSE guard and bridge lifecycle reset already passed; direct synchronous invalidation, late chooser/configuration/cleanup, conflicting snapshot and false write-verification regressions failed. Details: [before-results.json](../../artifacts/browser-review/before-results.json).
- Final suite against rebuilt `examples/browser/app.js`: **15 passed, 0 failed**, including two additional selected-device connection cases. Details: [after-results.json](../../artifacts/browser-review/after-results.json).
- `bun run test:sdk`: **30 passed, 0 failed, 496 assertions**.
- `bun run typecheck:sdk`: **passed**, including the browser TypeScript source.
- `bun run build:browser`: **passed**, rebuilt the browser bundle from the final source.

The 15 browser cases cover old SSE after PATCH; equal/undated/invalid connected events; newer SSE before older PATCH; newer SSE during a held refresh with unsent draft; undated disconnect during pending PATCH and debounce; bridge lifecycle restore and timestamp reset; late attach/cancellation while a fresh configuration is pending; late scan; late selected-device connect; selected-device timestamp reset; late explicit bridge disconnect cleanup; direct lifecycle invalidation before teardown yields and immediate bridge attach; late chooser; late GATT setup; and pending direct readback followed by a fresh direct connection. No unhandled rejection was observed in the passing cases.

The lifecycle cases deliberately dispatch synthetic `PageTransitionEvent`s with `persisted: true` against a retained real JS heap. They test the consumer's cache-entry/restoration behavior, not Chromium's actual BFcache admission policy. Physical hardware and deployed assets remain for the parent's live retest.

## Repeating the browser run

1. Run `bun run build:browser`.
2. Run `bun examples/browser/test/prepare.ts`. This prints a self-contained evaluation expression; trim its trailing newline before using it as a tool argument.
3. Use `preview_status`, then `preview_open({reuseExistingTab:false, open:false})`. Keep its returned tab ID and use it on every subsequent call; do not reuse another agent's tab.
4. Inspect the new blank tab with `preview_snapshot`. Pass the generated expression to `preview_evaluate({tabId, expression, awaitPromise:true})` and save its returned value. Expected: `passed: 15, failed: 0`.

Harness: [regression.js](../../examples/browser/test/regression.js), expression builder: [prepare.ts](../../examples/browser/test/prepare.ts). For historical comparison, pass `artifacts/browser-review/before-app.js` to `prepare.ts`; the saved initial result records the original 13-case run, before the last two cases were added.

Final bundle SHA-256: `d48cce154f6acfe00427451da79940cecb639178fa59edbebeea7d755ae3af4b`.

Ordering still depends on `updatedAt`, since the current protocol has no shared monotonic revision across JSON and SSE. Conflicting equal timestamps are rejected conservatively until a later verified readback; an explicit fresh attach resets ordering if the bridge's clock changes. A shared revision would strengthen that protocol, outside this browser-only change. No remaining failure was found in the implemented browser regressions.
