# TypeScript SDK and browser controller

`packages/sdk` is the independent, MIT-licensed `@lighttime/sdk` package. It implements the documented protocol facts and hardware captures in [api-contract.md](api-contract.md), without copying unlicensed vendor implementations. There are no runtime dependencies. Core framing, transport and controller code uses `Uint8Array` / `DataView`, with no Node `Buffer` or Node builtins.

## Build and verify

Use Bun 1.4.3 (pinned in the SDK package) and its independent lockfile:

```sh
cd packages/sdk
bun install --frozen-lockfile
bun run typecheck
bun test
bun run build
bun run smoke
bun audit
bun run build:browser
```

`build` bundles browser-compatible ESM to `dist/index.js` with Bun and emits declarations with `tsc`. A small declaration pass adds `.js` references for NodeNext consumers, while source imports remain compatible with Metro. The package export's `react-native` condition selects `src/index.ts`; npm ESM/types consumers select `dist`. Expo can depend on `"@lighttime/sdk": "file:../../packages/sdk"` without building dist first when Metro resolves the `react-native` condition. The package also has the conventional `react-native` field. Exported web types are structural; no Web Bluetooth type dependency is required.

Tests use an in-memory BLE adapter, the actual captured frames, and injected HTTP/SSE responses. They never access BLE. `bun run smoke` imports the public built package rather than its source.

### Install in another project

The package is built locally; it has not been published to npm. To create a
portable archive from `packages/sdk`, run:

```sh
bun run build
bun pm pack --destination ../../artifacts/distributions
```

Then, from your consuming website or Node project, install the resulting file:

```sh
bun add /absolute/path/to/lighttime-sdk-0.1.0.tgz
```

Import from `@lighttime/sdk` as shown below. The archive includes its ESM bundle,
TypeScript declarations, React Native source export and MIT license. A static
website can instead deploy the three built browser-example files. No bridge
token belongs in the deployment bundle.

An explicitly opt-in integration check is available as `bun run smoke:bridge`. It reads the token through `Bun.file` from `~/.config/lighttime/bridge.token` (override `LIGHTTIME_TOKEN_FILE`), accesses the already-connected bridge (override `LIGHTTIME_BRIDGE_URL`), checks the existing **5% / 5000 K** baseline, PATCHes that same baseline, receives authenticated SSE and confirms final readback. It does not scan, connect, disconnect, or access BLE directly, and never prints the token. Do not run it against a light intentionally set to another baseline.

## Supported G60 behavior

Brightness targets are **whole percentages, 0–100**. CCT targets are **multiples of 100 K, 2700–6500 K**. Hardware truncates finer settings; `validatePatch`, `LightController.setState`, `BridgeClient.setState`, and the value-payload helper reject unsupported resolution before sending. Brightness remains float32 on the wire. Unknown patch keys and contradictory brightness/power combinations are rejected.

`power: false` writes brightness zero; `power: true` restores the last observed nonzero brightness during this controller's lifetime, with a **5% fallback** when the initial light is off. This is output control, not mains power. Only the confirmed PL103 / MOLUS G60 brightness and CCT commands are used; no registration, provisioning, mesh-key, sleep, firmware or USB light-control behavior is assumed.

Direct writes use `transport.send(...)`, which waits for the adapter's GATT write-with-response completion without expecting a proprietary setter response. `LightController` then queries actual brightness and CCT. A setter succeeds only if these values match. It performs up to three readback attempts by default, with 100 ms between mismatches; persistent mismatch throws `VerificationError`. A failure can leave a partial hardware change: refresh before retrying. Cancellation and timeouts cannot undo a write already accepted by the device.

## HTTP bridge

```ts
import { BridgeClient, BridgeError } from '@lighttime/sdk';

// Supply a token for secured bridges; omit it for an explicit --no-auth bridge.
const bridge = new BridgeClient({
  baseUrl: 'http://127.0.0.1:8877',
  token: tokenFromUserInput,
});
try {
  // Attach to the existing bridge-owned connection without another BLE owner.
  const actual = await bridge.refresh();
  const verified = await bridge.setState({ brightness: 5, cct: 5000 });
  console.log(actual.brightness, verified.cct);
} catch (error) {
  if (error instanceof BridgeError) console.error(error.status, error.code);
  else throw error;
} finally {
  bridge.dispose(); // closes this client's requests/streams, not shared hardware
}
```

`devices(scanSeconds = 5)` accepts 0.1–30 seconds. `connect({ transport, address, deviceId? })` calls the bridge's connect route with a **60-second default timeout**, because BLE setup can take over 30 seconds. Effect calls also default to 60 seconds. Other requests default to 15 seconds; scan defaults allow the scan duration plus five seconds. Per-call `{ signal, timeoutMs }` overrides are supported; finite deadlines must be positive and no more than 300000 ms. Connect, disconnect, state and effect mutations share one serialized queue per client, with deadlines covering queueing; a queued request that expires is never sent. Calls preserve contract State field names such as `updatedAt`, and validate JSON response shapes.

When a nonempty token is supplied, requests carry `Authorization: Bearer …` in
a header. The constructor accepts an omitted or empty token for an explicitly
configured `--no-auth` bridge and omits Authorization entirely for JSON and SSE.
All requests use `credentials: 'omit'` and refuse redirects. Tokens are not
included in URLs. `health()` validates optional `authRequired` and
`defaultAddress` discovery metadata along with `name` and `version`.
Non-2xx responses throw `BridgeError` with HTTP `status` and server `code` /
`message`, with a safe fallback for non-JSON proxy errors. Malformed JSON/state
throws `ProtocolError`; cancellation and deadlines throw `CancelledError` /
`TimeoutError`. Native fetch network errors also propagate. Handle rejected
promises; do not infer success from draft UI values.

SSE uses fetch streaming instead of `EventSource`, retaining the auth header:

```ts
const cancel = new AbortController();
try {
  for await (const state of bridge.events({
    signal: cancel.signal,
    idleTimeoutMs: 45_000,
  })) {
    renderActualState(state);
  }
} finally {
  cancel.abort();
  bridge.dispose();
}
```

Abort from the UI to stop a blocked event read. Breaking from the loop also releases the reader. The parser handles fragmented UTF-8, CR/LF/CRLF lines, comments and multiline data, and bounds an event to 64 KiB of decoded text. EOF, idle timeout and malformed events are errors; there is no hidden reconnect loop. Show stale/lost-stream status and reconnect explicitly. The bridge should send heartbeat comments within the idle timeout. React Native callers need a fetch implementation with streaming response bodies and TextDecoder to use SSE; a streaming-capable fetch can be injected in the constructor. Basic bridge JSON calls only require normal fetch support.

### Bridge light patterns

Patterns use the existing authenticated bridge connection. The bridge generates
verified brightness/CCT commands; the SDK does not issue vendor firmware effect
commands or run local BLE patterns. See [effects-contract.md](effects-contract.md)
for the shared endpoint and runner contract.

```ts
import type { EffectPattern, EffectConfig, EffectStatus } from '@lighttime/sdk';

const pattern: EffectPattern = 'breathe';
const config: EffectConfig = {
  pattern,
  duration: 30,        // 1–3600 seconds; null means until stopped for cyclic runs
  period: 4,           // 1–60 seconds; fades use duration instead
  minBrightness: 0,    // whole percentages, 0–100
  maxBrightness: 20,   // must be at least the minimum
  cct: 5000,           // 2700–6500 K in 100 K steps
  restoreOnComplete: true,
};
const accepted: EffectStatus = await bridge.startEffect(config);
const observed = await bridge.effect(); // GET /v1/effects; poll at most once/sec
await bridge.stopEffect({ restore: true }); // default true; verified baseline restore
```

`EffectPattern` supports `blink`, `breathe`, `fade-in`, `fade-out`, and
`cct-sweep`. Sweeps interpolate from `cct` to `cctEnd` (default 6500 K) and back,
holding maximum brightness. Optional config defaults are duration 10 seconds,
period 4 seconds, minimum 0%, maximum verified nonzero baseline brightness or
5%, and baseline CCT. Restoration defaults to true for cyclic patterns and false
for fades. Finite fades hold their exact endpoint unless restoration is requested.
Only cyclic patterns allow `duration: null`. Unknown fields, non-finite numbers,
invalid bounds/resolution and non-boolean restoration flags reject before sending.

`EffectStatus` contains `running`, nullable `id`, `pattern`, `startedAt`, `endsAt`,
`progress` (0–1 for a finite run; null for idle/unbounded), and `lastError`.
The SDK validates required fields, timestamps, progress and running identity;
malformed status throws `ProtocolError`. Status describes the bridge runner,
while normal state readback/SSE describes actual light output. Keep requested
settings separate from both. A runner failure remains visible in `lastError`.

`effect(options?)`, `startEffect(config, options?)`, and
`stopEffect({ restore? }, options?)` all accept normal operation options and use
a 60-second deadline unless overridden. Stop without an active effect is
idempotent. A manual `setState` cancels the runner without restoration before
applying its settings. Explicit device disconnect stops the runner. Disposing
the SDK client or closing the page cancels local requests/subscriptions only;
the server pattern continues. Stop polling on detach/pagehide and reject late
results from prior sessions. A cancelled HTTP request cannot undo a start or
stop already accepted by the bridge; reattach and query status when uncertain.

## Direct Web Bluetooth

```ts
import { LightController, WebBluetoothTransport } from '@lighttime/sdk';

connectButton.addEventListener('click', async () => {
  try {
    // Call first, inside the user gesture, before other async work.
    const transport = await WebBluetoothTransport.request();
    const light = new LightController(transport);
    renderActualState(await light.connect());
    renderActualState(await light.setState({ brightness: 5, cct: 5000 }));
    // Retain `light`; call light.disconnect() when this connection is finished.
  } catch (error) {
    showConnectionError(error);
  }
});
```

The picker filters `namePrefix: 'PL103'` and requests FEE9 in `optionalServices`. The G60 may not advertise FEE9, so filtering by advertised service alone can hide the actual light. Write characteristic `…9600` uses writes with response; notify characteristic `…9601` carries fragmented/coalesced framed replies. The controller queries device info and ID rather than assuming a mesh address, and rejects a model other than PL103.

Web Bluetooth requires a secure context, a user gesture, OS Bluetooth access and a supported Chromium/browser/OS combination. `WebBluetoothTransport.available()` checks feature exposure and secure context, not physical compatibility. Chrome/Edge support differs by OS and configuration; Linux installations may need browser/backend configuration. Safari/Firefox and OBS's embedded browser should use the bridge. No universal browser or iOS-web support is promised. A user can cancel the chooser; browser permission errors are surfaced. Close ZY Vega or other direct controllers before taking BLE ownership.

## React Native BLE adapter

`BleProtocolTransport` takes the shared `BleAdapter` interface. Platform BLE libraries handle scanning, permissions, base64 conversion if needed, and native connection setup; these responsibilities are intentionally outside the SDK core:

```ts
import { BleProtocolTransport, LightController } from '@lighttime/sdk';
import type { BleAdapter, Unsubscribe } from '@lighttime/sdk';

const adapter: BleAdapter = {
  address: selectedDeviceId,
  connect: async () => { /* connect and discover FEE9 + characteristics */ },
  write: async (bytes: Uint8Array) => { /* native write-with-response to …9600 */ },
  subscribe: async (onBytes): Promise<Unsubscribe> => {
    // Subscribe …9601 before queries; deliver copied Uint8Array notification data.
    // Return an idempotent function removing the native notification subscription.
    return removeNativeNotificationSubscription;
  },
  onDisconnect: callback => registerNativeDisconnectCallback(callback),
  disconnect: async () => { /* release native connection and outstanding setup */ },
};
const transport = new BleProtocolTransport(adapter, { maxWriteSize: 20 });
const light = new LightController(transport);
await light.connect();
const off = light.subscribe(renderActualState);
await light.setState({ brightness: 5, cct: 5000 });
off();
await light.disconnect();
```

This is an adapter outline, not a bundled BLE library. Use the full UUID exports `G60_SERVICE`, `G60_WRITE`, `G60_NOTIFY` in a real implementation. A 20-byte default chunk size works within the default ATT MTU; adapters may pass a larger supported payload size after negotiating it (maximum 512). Chunked frames never interleave. Use one controller/transport per connected light; do not share raw commands concurrently with controller mutations.

The parser bounds body length to 1024 bytes by default and validates CRC16-XMODEM over the body. Replies require response type `0x0001` and both sequence and command. Pending requests are bounded to 64. Sequences are never reused within a connection; exhausting 65535 requests requires reconnecting. BLE request/write deadlines default to 5 seconds; a whole controller operation, including queueing, defaults to 10 seconds. Explicit abort, setup failure and disconnect clean pending work and notification/disconnect listeners. Native writes that time out close the session so late native completion cannot interleave with subsequent writes. Native adapters must still release their underlying resources. Transports and controllers are single-session objects: create new ones to reconnect.

## Browser example and OBS dock

The browser example allows 60 seconds per bridge request, including automatic
Bluetooth recovery during a refresh or write. The SDK's general default stays
15 seconds for non-connect calls; use `timeoutMs: 60_000` when your application
should wait for that recovery rather than time out first.

The static webroot is **`examples/browser`**, containing **`index.html`, `style.css`, and `app.js`**. `app.ts` is source; rebuild with `bun run build:browser` after SDK or UI edits. The Python bridge can copy/serve those three assets from its own webroot without another runtime. No root JS workspace or lockfile is required.

For a standalone local preview, run `bun run dev:browser` from `packages/sdk` (default port 5187, override `PORT`). The Bun server binds `0.0.0.0`; the local URL is `http://localhost:5187`. Other-device access needs a reachable host address, and ordinary LAN HTTP does **not** qualify as a secure Web Bluetooth context. This static server has no proxy/API; configure the bridge's explicit CORS origin allowlist for a separate preview origin. Serving the example from the bridge itself avoids that cross-origin setup.

A bridge-served `/ui/` page checks same-origin health on load. For an explicit
`--no-auth` bridge reporting `authRequired: false`, it hides the entire setup
section and attaches to the existing bridge connection automatically, without
using a saved token or endpoint. It briefly retries readback during bridge
startup, then offers a simple Reconnect light action using `defaultAddress`
when available. No MAC address, connector or token entry is needed. Sliders,
readback and patterns become available as soon as verified output is received.

Secured bridges retain the generic editable setup UI after the health check;
standalone static pages make no API request before an explicit action.
“Use bridge connection” refreshes the existing connection; “Find lights” and
“Connect selected light” explicitly invoke discovery/connect. The control picker
offers BLE devices only: serial discovery is identity-only and does not establish
G60 output-control support. Controls use whole-percent / 100 K steps, an Apply
button, and optional 200 ms coalescing with at most one write transaction in
flight. Requested settings and actual readback stay separate, failures remain
visible, and the UI never displays a draft as verified state. Direct BLE is an
explicit button on the generic setup UI.

The pattern section offers blink, breathe, fades and CCT sweep with duration,
cycle period, brightness bounds, CCT endpoints and completion restoration.
Attach through the bridge to enable it; direct Bluetooth keeps manual controls.
Actual runner progress/failure appears separately from requested settings and
actual brightness/CCT readback. “Stop & restore” requests the original baseline;
manual Apply cancels the runner through the backend. Runner status is polled
at most once per second while attached, without overlapping requests. Pagehide
clears polling, subscriptions and the visible session without sending a server
stop/disconnect. A restored personal page attaches automatically; secured pages
require a fresh explicit attachment. Both discover any continuing server pattern
with a new session and reject late results from the old one.

A fresh bridge-served `/ui/` page defaults to its exact origin, preserving
HTTP/HTTPS, Tailscale or MagicDNS hostnames and arbitrary ports. A separately
served HTTP(S) preview defaults to port 8877 on its serving host; the Bridge URL
field remains editable on the generic setup UI. For secured bridges, an explicitly
remembered session restores its saved URL, including a previous HTTP endpoint,
without silently replacing it. Edit that field if the bridge has moved to HTTPS
or another port. Personal no-auth pages always use their serving origin for
automatic attachment. No private host address or token is built into the page.

The offline real-browser regression harness is prepared with:

```sh
bun examples/browser/test/prepare.ts > /tmp/lighttime-browser-regression.js
```

Evaluate the resulting expression in a new blank browser tab. It runs the built
bundle against the real DOM with fake fetch/GATT and a private fake session
store, covering state ordering, lifecycle cleanup, runner actions/polling and
same-origin URL defaults. It requires no server or hardware and uses only a
dummy token. Its synthetic page transitions test lifecycle handlers; they do
not establish actual browser BFcache eligibility.

In OBS, add a Custom Browser Dock using the bridge-served page URL. A personal
no-auth controller attaches automatically. For a secured bridge, paste the token
into its field and use the existing bridge connection. Tokens default to memory
only; an explicit checkbox opts into `sessionStorage` for that tab/session. They
are never placed in a URL, localStorage, page source or generated bundle. Session
storage may be blocked or retained/restored differently by embedded-browser
lifecycle; leave it unchecked on shared computers. Both modes still trust all
JavaScript on the serving origin.

A deployed HTTPS website is not a universal frontend for a plain-HTTP local bridge. Depending on the browser, mixed-content rules, local-network permissions/private-network policies, OS access and CORS preflight can block the request. An origin allowlist alone does not override these policies. Prefer a page served by the local bridge, or a deliberately configured trusted HTTPS bridge. `127.0.0.1` / `localhost` always refers to the device running the browser; a remote phone cannot reach the computer's loopback bridge that way. Cross-device bridge access requires an intentionally reachable, authenticated listener and appropriate TLS/origin configuration. Do not expose the bridge token or unauthenticated control publicly.
