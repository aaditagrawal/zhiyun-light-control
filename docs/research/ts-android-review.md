# TypeScript SDK and Android review

Reviewed 2026-10-23 against `docs/api-contract.md`, including the recorded G60 setter/readback behavior. Scope: `packages/sdk`, `apps/android` (especially `NativeBleAdapter`, `useLight`, `OperationGate`, runtime permissions and config plugins), and the SDK's browser consumer where lifecycle handling lives.

Only this report was added to the repository. No implementation, SDK bundle, Android config/generated project, token file, hardware, camera, existing bridge or existing service was changed or accessed. No agents or commits were created. Browser reproductions used the existing built bundle in a separate blank collaborative-browser tab with an entirely fake fetch implementation and a dummy token; they made no network requests to a bridge.

## Findings

### P2 — Restored browser pages retain a verified-looking connection to a disposed client

**Location:** `examples/browser/app.ts:209–213` (also present in the current `app.js` bundle).

The `pagehide` handler aborts operations and SSE, removes the direct-controller observer, then disposes/disconnects the client. It leaves `client`, cached connected `state`, status text, event status, pending debounce work and the operation AbortController in place. There is no `pageshow` recovery. If the browser restores the document from its back/forward cache, the old connected UI is restored with an unusable client. For direct BLE, the observer has already been removed before disconnect, so that path cannot clear the display either.

**Offline browser repro:** Load the real HTML and built `app.js` into a blank tab after replacing fetch with fake JSON/SSE responses. Attach to a fake connected G60 at 5% / 5000 K, then dispatch `PageTransitionEvent('pagehide', {persisted: true})` followed by `pageshow` with the same flag. Before and after restoration the DOM reports:

```text
status: Bridge connected · actual readback received.
indicator: connected
events: Bridge state events active.
actual brightness: 5%
brightness control enabled: true
```

Clicking Read back then displays `Operation cancelled`; the fake request count remains two (initial state + SSE), confirming the retained client never dispatches a refresh. The event status still says active. This was a real-browser synthetic lifecycle test, not a measurement of that browser's actual cache eligibility. The user-facing trigger is returning with Back/Forward when the browser does restore this document. Pagehide also runs on cache entry, and restoration preserves the JS heap; see the browser team's [back/forward cache lifecycle documentation](https://web.dev/articles/bfcache#apis_to_observe_bfcache).

**Fix guidance:** Invalidate the displayed connection synchronously on pagehide, increment the session epoch, clear timers/pending writes and drop the client/observer references. On persisted pageshow, reset operation cancellation state and require a fresh attach/read or BLE connection before enabling controls or showing verified/live status. The existing `release()` routine has most of this reset behavior, but its asynchronous BLE teardown must not postpone the synchronous UI invalidation. Add an offline pagehide/pageshow regression using both bridge and direct-controller fakes, then rebuild browser assets.

### P2 — Delayed SSE readback can overwrite a newer verified HTTP result

**Location:** `examples/browser/app.ts:98–100`; related mutation rendering at `177–183`.

The event consumer unconditionally calls `render(event)`. The epoch rejects a previous connection's results, but does not order snapshots from the current connection. HTTP responses and SSE use separate requests, so an older state event can arrive after a newer PATCH/read response. The existing bridge captures snapshots in its SSE queue and publishes a PATCH state before returning its JSON response; those separate deliveries provide no cross-request arrival-order guarantee. The UI can replace the newer actual values while leaving “Settings verified by device readback” and “Settings match actual device readback” visible.

**Offline browser repro:** With the real built bundle and fake fetch, attach at brightness 5 with `updatedAt: 2026-10-23T12:00:00Z`. Apply 8; return brightness 8 with timestamp `12:00:01Z` from PATCH. After the UI confirms 8, release an older 5% SSE snapshot with timestamp `12:00:00Z` from the held stream. Observed:

```text
after PATCH: actual 8%, hardware fake 8, last read 17:30:01
after old SSE: actual 5%, hardware fake 8, last read 17:30:00
both: Settings verified by device readback.
both: Settings match actual device readback.
```

The older snapshot is valid protocol data; rejecting malformed frames or stale connection epochs does not address it. A subsequent newer event may correct the values, but the regression remains visible until that delivery and can persist if streaming stalls.

**Fix guidance:** Order state snapshots within a connection before accepting them for display. The existing `updatedAt` can reject older dated connected snapshots; explicit disconnection snapshots with null timestamps need separate treatment. A bridge-issued monotonic revision shared by JSON and SSE would provide stronger ordering. Keep verification/write messages consistent with the accepted state, and add a fake SSE/HTTP delivery-order regression. Rebuild browser assets after fixing the consumer.

### P3 — Android details promise a 100% restore level while the implementation restores 5%

**Location:** `apps/android/App.tsx:189`; actual behavior in `packages/sdk/src/controller.ts:14,72`.

The details panel says, “If connected at 0%, the initial restore level is 100%.” The controller initializes `restoreBrightness` to 5, and the existing initially-off test confirms that Turn on restores 5%. This is a concrete contradiction in the control's explanation.

**Repro:** Connect to an initially-off G60 or the SDK's initially-off fake, open Connection details, then Turn on. The panel promises 100%; verified readback is 5%.

**Fix guidance:** Change the details text to the 5% fallback already defined in the shared contract and controller. Preserve the implementation's safe fallback.

## Verification and reviewed paths

- `bun test` in `packages/sdk`: **30 passed, 496 assertions**.
- `bun test` in `apps/android`: **13 passed, 142 assertions**.
- Ran the two browser reproductions above against the existing built bundle, with native browser Promise/fetch/stream APIs and fake responses only.
- Reviewed source exports and declaration references, byte-array/base64 boundaries, native fetch binding, header-only authentication, cancellation and queued deadlines, disconnect cleanup, notification callbacks, permission API-level branching, and plugin permission bounds.
- Inspected the installed `react-native-ble-plx` 3.5.1 monitor implementation and the pinned RxAndroidBle 1.17.2 source. I did not file the synchronous return of the monitor subscription alone as a readiness defect: the Android implementation initiates CCC configuration through the same serialized GATT operation queue as writes. An arbitrary delayed-ready fake, without modeling that queue, would not establish an actual Android failure.

No additional supported correctness defect was found in the SDK protocol/controller, native adapter, operation gate, permission selection or config plugins during this review. Setter acknowledgement/readback separation, device ID zero, whole-percent / 100 K target validation, timeout rejection, and initially-off 5% fallback match the supplied runtime evidence. This report adds no new claim of physical Android validation; the findings above distinguish source evidence and offline reproduction from hardware execution.
