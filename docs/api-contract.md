# Lighttime v1 integration contract

This is the shared contract for the Python bridge, TypeScript SDK, OBS and Expo.
The hardware is a bi-color G60: brightness 0–100 in whole percent, CCT 2700–6500 K
in 100 K steps. The wire uses float32 for brightness, but actual G60 readback
truncates fractional percentages and non-100 K temperatures. Public setters
reject unsupported resolution before writing. Power off is implemented as brightness 0; power on restores the last
nonzero brightness for the lifetime of the controller. This is not mains power.

## Local HTTP bridge

Run `uv run --frozen lighttime`: no arguments means `serve`, bound to loopback
at `http://127.0.0.1:8877`. The default bridge needs no token and discovers a
single nearby PL103/G60 once at startup. `serve --address IDENTIFIER` selects
one explicitly; `--no-auto-connect` defers startup discovery/connection.
Multiple candidates require explicit selection, never an arbitrary first match.

JSON request/response bodies. Errors:
`{ "error": { "code": "...", "message": "..." } }`.
Same-origin requests need no CORS configuration; separately served browser
apps need exact `--allow-origin` values. Use a reachable private HTTPS bridge
URL or same-origin proxy for remote apps; a browser's localhost refers to its
own device. See [website integration](integrating-sites.md).

- GET `/v1/health`: `{ "name": "lighttime", "version": "0.1.0",
  "authRequired": boolean }`. In default no-auth mode additionally returns
  `"defaultAddress": string | null` as startup/reconnect address metadata.
  Authenticated health omits that address.
- GET `/v1/devices?timeout=5`: `{ "devices": [ Device ] }`.
- POST `/v1/connect`: `{ "transport"?: "ble" | "mock",
  "address"?: "BLE address/id (or mock identifier)", "deviceId"?: 0 }`. Returns State.
  `{}` reuses the existing connection and performs fresh readback. Otherwise it
  discovers and connects a single PL103/G60. No candidate or multiple candidates
  returns an actionable error; clients can list devices and send an explicit
  selected address. Reusing the current connection does not replace its BLE owner.
  Mock is only enabled by an explicit bridge startup flag. BLE is the default.
  Serial runtime connections return HTTP 400 `invalid_request`; serial devices
  remain in discovery results for USB identification with `lighttime usb-info`.
  A bridge configured with `--usb-writes PORT` routes setters through USB and
  retains BLE identity/readback. Clients still connect with `transport: "ble"`;
  the verified State reports `transport: "usb+ble"`. The USB and BLE serial
  numbers must match before output is permitted.
  The device ID is always queried; a supplied `deviceId` cannot override it.
- POST `/v1/disconnect`: empty object. Returns State.
- GET `/v1/state`: refreshes actual hardware readback, returns State.
- PATCH `/v1/state`: `{ "brightness"?: number, "cct"?: number,
  "power"?: boolean }`. Returns verified State. On transport failure returns an
  error and never reports successful hardware control. Brightness and power in
  one request must agree (off with positive brightness is rejected).
- GET `/v1/events`: SSE, each event `event: state` followed by a JSON State.
  SDKs use fetch streaming, carrying a header when optional auth is configured.

The browser controller attaches automatically to the default bridge without a
token or address form. Python `BridgeClient().connect()` and TypeScript
`new BridgeClient()` followed by `connect()` use addressless connect. The SDK
default URL stays `http://127.0.0.1:8877`; a bridge-served site uses
`{ baseUrl: location.origin }`. The bridge also serves a self-contained SDK
ES module at `GET /lighttime.js`; `serve --web-root PATH` serves custom site
assets under `/ui/` with a root entry point. This allows static HTML to import
`{ BridgeClient, EFFECT_PRESETS }` from `/lighttime.js` with no package install.

Authentication is opt-in with `serve --auth` (`Bridge(auth_required=True)`).
Then all `/v1/*` routes except `/v1/health` require
`Authorization: Bearer <token>`. The CLI creates a mode-0600 token file on POSIX;
`--token-file PATH` optionally selects its location. It prints the path, not
the token. Default construction uses `auth_required=False`, creates no token
file and prints no token path. `--no-auth` remains compatible. Clients omit
Authorization when their token is absent/empty. Origin checks apply in both
modes; static assets contain no embedded token. See
[optional authentication](python.md#optional-authentication).

Up to three BLE connection attempts share a 40-second lifecycle budget for scanning,
connection and notification setup, followed by at most 5 seconds of cleanup.
Discovery bounds scanner startup to 5 seconds, scans for the requested timeout,
then allows at most 5 seconds for cleanup. Deadline and cleanup failures return
HTTP 503 `device_unavailable`. A disconnected/unverified State does not prove
the backend has finished cleanup: an error explicitly reports incomplete cleanup,
and its backend/scanner remains owned for retry before replacement. Failed
reconnect identification clears the previous identity and readback snapshot.
Initial autoconnect runs in the background and does not block HTTP readiness or
the disconnected GET `/v1/state` response.

Shutdown ends open event streams and releases the owned Bluetooth connection.
The CLI allows five seconds to drain HTTP handlers after application shutdown;
the Linux service template allows 30 seconds for the complete stop sequence.

On Linux, a G60 may remain connected in BlueZ after its previous controller exits
and therefore stop advertising. If discovery cannot find the explicitly chosen
G60 address, the error includes targeted `bluetoothctl info <address>` and
`bluetoothctl disconnect <address>` instructions for manual recovery after
confirming no other app owns the link. The bridge does not reset the adapter or
automatically disconnect a connection it did not establish.

Device: `{ "transport": "ble" | "serial", "address": string, "name": string,
"rssi"?: number, "accessible"?: boolean }`.

State: `{ "connected": boolean, "transport": "ble" | "usb+ble" | "serial" | "mock" | null,
"address": string | null, "device": { "model": string, "serial": string,
"id": number } | null, "brightness": number | null, "cct": number | null,
"power": boolean | null, "updatedAt": string | null }`.

## Server-driven effects

The [canonical effects contract](effects-contract.md) defines the shared Python,
TypeScript and UI API. Effects use verified brightness/CCT commands, with one
bridge-owned runner independent of its initiating browser. All endpoints use
the existing device authentication and origin policy:

- GET `/v1/effects`: returns `EffectStatus`.
- POST `/v1/effects/start`: accepts `EffectConfig`, returns `EffectStatus`
  after capturing verified baseline state and accepting the task.
- POST `/v1/effects/stop`: `{ "restore"?: boolean }`, default true; returns
  `EffectStatus` after cancellation and any requested verified restoration.
  An idle stop is idempotent.

`EffectConfig` requires `pattern`: `blink`, `breathe`, `fade-in`, `fade-out`,
`cct-sweep`, `pulse`, `flicker`, `sunrise`, `lightning`, `emergency`, `paparazzi`,
`tv`, `morse`, or `keyframes`. Common fields and the per-pattern union are
specified in [the effects contract](effects-contract.md) and its
[expanded program contract](effects-programs-contract.md). Seeded programs
produce the same quantized targets in Python and TypeScript. Brightness stays
whole-percent and CCT uses 100 K steps. Unknown fields, foreign pattern fields
and invalid/non-finite values return HTTP 400 `invalid_request` before output
writes. `period` is a complete cycle; blink accepts 0.2–60 seconds with each
phase at least 0.1 seconds. A requested cadence is not an optical timing promise.

`EffectStatus`: `{ "running": boolean, "id": string | null,
"pattern": EffectPattern | null,
"startedAt": string | null, "endsAt": string | null,
"progress": number | null, "lastError": string | null }`.
Timestamps use ISO 8601; progress is 0–1 for a finite run and null when
idle/unbounded. Finished runs retain their metadata and frozen progress.
Errors stop the runner and remain visible until an accepted new start.

Output uses monotonic-clock scheduling, quantization, serialization with manual
control and full readback. Update starts are spaced at least 100 ms apart;
missed and identical confirmed frame targets are skipped. Restoration always
writes/verifies both baseline fields, since
an interrupted setter can change hardware before its cached readback updates.
Finite fades hold their exact endpoint unless restoration was requested.
A replacement cancels the prior runner without restoration and captures its
own current verified baseline. Manual writes, disconnect and connection
replacement cancel/drain without restoration; shutdown drains owned effects
before client cleanup, starts no restoration and rejects new effect requests
with HTTP 503 `bridge_stopping`. Actual state continues through normal SSE;
effect status is a separate query, suitable for polling once per second.

Python `BridgeClient`: `effect()`, `start_effect(pattern, **options)`,
`stop_effect(restore=True)`. TypeScript: `effect(options?)`,
`startEffect(config, options?)`, `stopEffect({restore?}, options?)`, with
`EffectPattern`, `EffectConfig`, and `EffectStatus` exports.
See [effect behavior and lifecycle details](effects.md).

## Direct BLE (Android / Web Bluetooth)

The actual light was connected and GATT inspected on 2026-10-23:

- Name prefix PL103 (e.g. PL103_EDFE), manufacturer ID 0x0905.
- Service `0000fee9-0000-1000-8000-00805f9b34fb` (may not be advertised).
- Write `d44bc439-abfd-45a2-b575-925416129600`, supports write with response.
- Notify `d44bc439-abfd-45a2-b575-925416129601`.
- No pairing, mesh provisioning or app/account key was required for readback.

Frame: magic `24 3c`, body length u16 LE, body, CRC16-XMODEM u16 LE.
CRC over body only, initial zero. Body: destination byte, source byte, sequence
u16 LE, command u16 LE, payload. The codec's legacy `messageType` u16 represents
those first two bytes: request `00 01` (0x0100), reply `01 00` (0x0001).
Notifications can be fragmented/coalesced: use a streaming parser, enforce a
maximum length, check CRC, correlate both sequence and command.

**Actual setter behavior:** BLE brightness/CCT writes emit no protocol reply.
Send with GATT response, then issue separate read queries and verify values.
Do not await a ZYBL response to a setter. USB identity queries work, but runtime
brightness/CCT reads on USB did not respond in the local test.

- Device info 0x2003, empty payload -> null-delimited serial and model ASCII.
- Device ID 0x2005, empty payload -> u16 LE (actual light returned 0).
- Brightness 0x1001: device ID u16 LE + mode u8 (read=0/write=1) + float32 LE.
- CCT 0x1002: device ID u16 LE + mode u8 + u16 LE Kelvin.
- Read payload value fields are zeroed. Responses contain the same payload
  structure and actual value. Poll after writes to verify device state.

Actual frames captured from the G60:

```
info tx 243c0600000101000320d4ad
info rx 243c22000100010003203038613430396530633131303031313300706c3130330000000000004f58
id tx   243c0600000102000520ae9c
id rx   243c0800010002000520000010f6
dim tx  243c0d000001030001100000000000000018ad
dim rx  243c0d000100030001100000000000a040b4be
cct tx  243c0b000001040002100000000000cd89
cct rx  243c0b000100040002100000008813a28e
```

Use independent code from these protocol facts. Upstream implementations with
no license must not be copied. The official binaries stay in ignored artifacts.
