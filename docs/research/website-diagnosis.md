# Website connection diagnosis — 2026-10-23

## Outcome

The parent confirmed that the former `http://100.64.0.10:8877/` route became unreachable when the backend moved to loopback behind private HTTPS. The user subsequently confirmed the website works after the parent restored access; response-time and real-hardware validation remain with the parent. During diagnosis, this reviewer made no service, Tailscale, BlueZ, GitHub, or source changes. The user's exact failing URL, browser, and error remain pending, so the old-route failure cannot yet be assigned to their specific attempt.

The reviewed frontend's successful personal autoattach **does not use a remembered Bridge URL or token**. Both the health probe and the automatic client use `location.origin`. A stale saved URL can affect **manual setup after a failed probe**, secured-bridge setup, and separately hosted pages. Two recovery gaps were reproduced before the follow-up changes below: a failed startup health probe is never retried automatically, and a lost SSE stream is never restarted automatically.

The diagnostic observations below describe the pre-fix build. Validation hostnames, IPs, device identifiers and the browser tab identifier have been sanitized. The reported asset digests identify that earlier build, not the rebuilt recovery bundle.

The follow-up implementation at the end of this report fixes the health and SSE recovery gaps and reduces redundant slider fields. No live endpoint was called during that implementation or its browser tests.

## Scope and evidence

- Read source under `examples/browser/`, `packages/sdk/src/`, and `src/lighttime/bridge.py`. Only this report was written.
- Used a new incognito T3 preview tab, without opening or touching the parent's tab. The browser identified itself as Linux Chrome 154. This is not a Safari/Android device test.
- Navigated only to the read-only `/ui/style.css` asset. In fresh iframe fixtures, removed all script/link resource loading, replaced `fetch` before evaluating the real packaged JS, supplied fake session storage with dummy credentials, and set `navigator.bluetooth` to unavailable. No fixture request called native fetch or Bluetooth. Unexpected fake endpoints threw.
- Fixtures used the actual HTML and packaged JS, with a lexical `location` argument to exercise URL branches. They recorded every fake request and used real DOM, Response, ReadableStream, AbortController, and PageTransitionEvent implementations. Synthetic visibility/lifecycle dispatch verifies handlers; it does not reproduce OS suspension or prove actual bfcache eligibility.
- Real HTTP access was limited to read-only health and static-asset requests/headers. Neither a real device scan nor a real state, connect, disconnect, effect, or output request was made.
- Local checkout had no resolvable `HEAD` when inspected. `9eab0e1` is the revision supplied in the task, not independently verified here. Live assets were compared directly with local packaged files instead.

At approximately 16:10–16:12 UTC, both `http://100.64.0.10:8877/v1/health` and `https://example.test:10000/v1/health` returned:

```json
{"name":"lighttime","version":"0.1.0","authRequired":false,"defaultAddress":"FAKE-G60"}
```

Fresh HTTPS GETs returned 200 for all three assets and matched their local `src/lighttime/static/` files byte for byte:

```text
app.js     38639 bytes  sha256 908ec68927c7e4d470e232ba1f7c260bfef65d27d573d7591d3a301d596d7aee
index.html  7501 bytes  sha256 7cd131470b29aeca6049126d347e6e49e91bccfa0d0e7385eeddff41bdabb6ac
style.css   5777 bytes  sha256 ead114d8974adbddb99aa84afb20e23a32e2f89771bde12d495d2959900e7b81
```

## Reproduced frontend behavior

### Successful no-auth autoattach ignores saved endpoints

Source: `examples/browser/app.ts:40`, `:156`, `:162`, `:250`, `:287`.

Fixture: page location `https://example.test:10000/ui/index.html`; saved session `{url: "http://100.64.0.10:8877", token: "fake-old-token"}`; fake health reports `authRequired: false`; fake state is connected and complete.

Result: setup hidden, brightness and patterns enabled, status `Light connected · actual readback received.` Every initial request went to the HTTPS page origin:

```text
GET https://example.test:10000/v1/health
GET https://example.test:10000/v1/state
GET https://example.test:10000/v1/events
GET https://example.test:10000/v1/effects
```

All had `credentials: omit`, `cache: no-store`, and no Authorization header. The hidden Bridge URL field still contained the saved HTTP URL, but it did not configure these clients. Thus the proposed `inspectLocalBridge -> attachAutomatically -> stale saved URL` explanation is refuted for the current successful no-auth path.

### Failed startup probe leaves the page in manual mode

Source: `examples/browser/app.ts:287`. Health has a 3,000 ms deadline and one attempt. The separate approximately eight-second state-read retry loop in `attachAutomatically` runs only after health succeeds with explicit `authRequired: false`.

Fixture: fail the first fake health fetch; then make health healthy and dispatch `online`, `focus`, and `visibilitychange`; wait another 1.1 seconds.

Result: exactly one health request, no state request, no stream. Status remained `Bridge startup check failed: Failed to fetch (fake startup). Choose a connection below.` Setup remained visible and controls disabled. There are no online, focus, or visibility recovery handlers. This supports a page remaining unusable after a transient initial network/Tailscale failure, even though the backend is healthy later. It does not establish that the user's browser experienced such a failure.

Immediate recovery: reload after the page origin is reachable, or use manual bridge attachment with the correct HTTPS origin. Fix guidance: offer a same-origin retry for failed personal-bridge discovery, with bounded retry on foreground/network restoration. Keep the SDK's explicit errors and the existing epoch/cancellation protection.

### Saved URL is used in the manual fallback

Source: `examples/browser/app.ts:162`, `:204`, `:312`.

Fixture: same saved HTTP endpoint as above; fail the first health probe; then click `Use bridge connection`.

Result: manual `GET /v1/state`, SSE, and effect polling targeted `http://100.64.0.10:8877`, with `Authorization: Bearer fake-old-token`. This establishes the URL-selection path only: fake fetch deliberately accepted it. In an actual HTTPS document, an HTTP-IP fetch is blockable mixed content, independently of whether that HTTP listener has been restored. See [MDN mixed content](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Mixed_content).

Immediate recovery for this exact state: change the visible Bridge URL to `https://example.test:10000` and clear an obsolete token for the current no-auth bridge, or reload into successful autoattach. Fix guidance: make a same-origin retry available without requiring manual setup or silently destroying an intentional saved remote endpoint.

Storage is `sessionStorage`, not localStorage or cookies. It survives reloads/restores in the same origin/tab session; changing scheme, hostname, or port selects different storage. A saved endpoint on the old HTTP origin does not automatically migrate to the new HTTPS origin. The stale-URL fixture represents an endpoint previously saved from within the HTTPS origin. Storage-access failures are caught, so unavailable session storage alone does not block autoattach. See [MDN sessionStorage](https://developer.mozilla.org/en-US/docs/Web/API/Window/sessionStorage).

### Lost SSE never reconnects, while controls still work

Source: `packages/sdk/src/bridge.ts:112`, `examples/browser/app.ts:215`, `:104`, `:479`.

Fixture: complete normal no-auth autoattach, then fail the fake ReadableStream with a network error. Dispatch visibility and online events, then click `Read back`.

Result after stream loss:

```text
status: Bridge events lost. Displayed readback may be stale.
events: Live events stopped: Network connection lost (fake). Use Read back to refresh.
```

Brightness, manual controls, and patterns remained enabled. Setup and `Reconnect light` remained hidden because the last accepted state was still connected. Visibility/online events did not issue a replacement SSE request. `Read back` issued one fresh state GET and changed the main status to `Actual device readback received.`, while leaving the old stream-error text and stream count unchanged at one. Effect polling is separate and does not restart state events.

This explains stale readback/error indicators after an interrupted stream. By itself it does **not** explain permanently disabled controls or failed write requests. Immediate recovery for the hidden-setup personal UI is a reload; Read back recovers only a state snapshot. Fix guidance: track event-stream health separately and expose/retry event subscription without disconnecting the shared light or issuing a BLE connect.

The SDK intentionally reports EOF/network/idle errors to its caller rather than silently reconnecting. Its stream idle timeout is 45 seconds; the server sends keepalive comments every 10 seconds (`src/lighttime/bridge.py:392`). A quiet light should therefore not time out on a functioning delivered stream. No live stream failure or Safari-specific streaming bug was observed in this review.

### BFcache restoration has a working recovery path

Source: `examples/browser/app.ts:499`.

Fixture: after no-auth attachment, dispatch synthetic `pagehide(persisted=true)`, followed by `pageshow(persisted=true)`.

Result: pagehide immediately cleared readback and disabled controls. Pageshow attached a fresh same-origin client, enabled controls, and opened a second SSE stream. The stale saved HTTP URL remained ignored. Therefore this specific handler path works in the current bundle.

Returning to a tab/app is not equivalent to a bfcache restore. The source only retries on `pageshow` when `persisted` is true and has no visibility handler. Browser background timer policies and mobile lifecycle delivery vary; this review only establishes the missing handlers, not the user's OS behavior. See [MDN pagehide](https://developer.mozilla.org/en-US/docs/Web/API/Window/pagehide_event) and [Page Visibility API](https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API).

## Defaults, secured UI, and caching

For a path matching `/ui` or `/ui/...`, the current UI keeps the exact page origin and port. The bridge's `/`, `/ui`, and `/ui/` routes redirect to `/ui/index.html` (`src/lighttime/bridge.py:420`), so the normal root URL reaches this path.

For a separately hosted `/index.html`, the default-endpoint code instead replaces the page port with `8877` while retaining the scheme and hostname. The fixture at `https://example.test:10000/index.html` selected `https://example.test:8877`, made no startup health request, and stayed in manual setup. That default does not match the supplied private-HTTPS mapping. This is relevant only if the user is running a separately hosted/copied page or different path; it is not the behavior of the normal bridge URL.

Fake health with `authRequired: true`, or with `authRequired` omitted, showed the editable secured/manual connection UI and preserved remembered settings. Live health currently explicitly reports false. A token field appearing on the page does not prove the live bridge requires authentication: it can also appear after a failed startup probe or with a non-bridge-served page.

Live static GET responses had ETag and Last-Modified validators but no `Cache-Control` or `Expires`. HTML references stable `./app.js` and `./style.css` URLs with no content version. This permits browser heuristic caching; `cache: no-store` on API fetches does not control document/module caching. The standalone Bun example server supplies `Cache-Control: no-store`, but the Python static route does not (`examples/browser/serve.ts`, `src/lighttime/bridge.py:157`). See [MDN HTTP caching](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Caching).

Fresh server assets matched local files, so no stale server deployment was found. An already-open old document or previously cached assets remain possible, not demonstrated on the user's device. A query on `index.html` alone does not change the relative module URL `./app.js`. For a confirmed old UI, force revalidate the page and its assets or clear that origin's cache; future deployments can use revalidated HTML and versioned asset URLs. Current HTML labels manual attachment `Use bridge connection`; it has no literal `Test connection` action. The user's wording alone is insufficient to identify which build or UI they saw.

## Mobile browser findings

Native Web Bluetooth is unavailable in Safari, including Safari on iOS; the specification project's [implementation status](https://github.com/whatwg/bluetooth/blob/main/implementation-status.md#safari) documents this. The direct chooser also requires a secure context and filters device names by `PL103` (`packages/sdk/src/web-bluetooth.ts:72`). An HTTP TailIP page does not supply that secure context.

The bridge UI does not require Web Bluetooth on the visitor's device. In every no-auth fixture, `navigator.bluetooth` was absent: the direct button was disabled while bridge brightness and pattern controls became available. Thus absent Web Bluetooth explains an unavailable direct chooser, but it does not establish a cause for failure of the same-origin HTTPS bridge controls. No real iOS/Android browser was tested. No recommendation to change Bluetooth pairing, scan, or release the healthy shared bridge follows from this evidence.

## Parent handoff

The known old-route outage is confirmed by the parent. Saved-session override of successful current autoattach is disproved by source and browser reproduction. The supported secondary failures are startup-probe fallback retaining a stale manual URL, lack of health-probe retry, and lack of SSE recovery after interruption. Cache and non-`/ui/` defaults remain conditional on the user's actual page. The exact URL and visible status/error are still needed to distinguish those cases; a general mobile incompatibility diagnosis is not supported.

## Follow-up implementation

Changes are limited to `examples/browser/app.ts`, its rebuilt `app.js`, `examples/browser/test/regression.js`, `docs/typescript.md`, and this report. The packaged Python static files are deliberately untouched; deployment and actual-device timing remain with the parent.

Failed same-origin health discovery now keeps token/setup prompts hidden and offers Retry connection. Online and foreground events share a bounded recovery burst: one probe plus at most three retries after 0.5, 1.5 and 3 seconds. Each health request has a three-second deadline. Successful authenticated discovery reveals the normal saved setup; explicit manual attachment supersedes discovery. Saved remote settings are retained rather than overwritten.

SSE interruption now retries the existing client's subscription with the same three backoff delays. Accepted verified state resets the consecutive-failure budget. After exhaustion, Retry connection or a later online/foreground event can start another bounded burst. Recovery never scans, calls the BLE connect endpoint, applies drafts, or rereads edited connection fields. Pagehide and replacement sessions abort pending probes, stream fetches, subscriptions and retry delays. An authoritative disconnected device state still ends the attached session; only the user's explicit Reconnect light action can request BLE connection.

Personal mode retains the parent's `auto.checked = true` and immediate dispatch through the existing one-flight/latest-pending pump. Pending targets retain both desired output fields, and only dispatch-time comparison with the latest accepted state decides which fields to PATCH. Reversals to the old baseline therefore survive an in-flight write; combined latest targets retain both fields. Already-confirmed targets send no empty PATCH, preserve the read timestamp and say they match the last verified readback. Absolute power commands and backend verification are unchanged.

The rebuilt `examples/browser/app.js` is 39,971 bytes, SHA-256 `d696459ae0cc6bca145ea21a20c0fdc43db8df32a97cb9fcf5232564d0eb7106`.

Final validation: `bun run build:browser` and `bun run typecheck` in `packages/sdk` passed. The prepared real-browser suite passed **55/55 cases**, including the parent's immediate-slider case, health recovery/bounds, authenticated/manual preservation, SSE replacement/backoff/exhaustion, timer and in-flight cancellation, stale-response rejection, explicit disconnect/reconnect, single-field and combined coalescing, baseline reversals, and unchanged targets. The final harness was verified to contain the current built bundle. It ran only in a new private blank T3 tab with fake fetch/GATT; browser resource requests were empty and all fixtures were removed after completion. No actual Safari/Android lifecycle or hardware timing claim follows from these tests. No reproduced defect remains unresolved in this scope.
