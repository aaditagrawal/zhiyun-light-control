# Python controller and bridge review

Reviewed 2026-10-23: `src/lighttime/{transport,client,bridge,cli,protocol,__init__}.py`, `tests/test_core.py`, `scripts/hardware_verify.py`, and `docs/api-contract.md`. This is a review of the source after the 15 s connect / 5 s notify / 5 s disconnect / 35 s initial-connect changes. No implementation files were changed. No hardware, Bluetooth, camera, existing server, credentials, or live API writes were accessed. Reproductions below use in-memory fake transports and fake Bleak classes.

## Findings

### P1 — Scanner startup/shutdown can still hang connection and discovery indefinitely

**Locations:** `src/lighttime/transport.py:103`, `:122`, `:123`, `:227`; `src/lighttime/bridge.py:227`, `:304`.

The 15 s timeout covers only `BleakClient.connect()`. Entering and leaving `async with BleakScanner()` occurs outside it. In particular, scanner shutdown happens **after** BLE connect and service discovery, and **before** `start_notify()`. A stalled `StopDiscovery` therefore leaves exactly the observable condition of BlueZ connected/services resolved while the application never finishes connecting. Neither direct SDK/CLI connections nor manual HTTP connections have an outer connection timeout. Reconnects from refresh/set have the same exposure. The initial background attempt has a 35 s wrapper, but subsequent controller retries do not. `discover()` similarly trusts Bleak's timeout, which does not bound scanner startup/shutdown.

**Offline repro:** Replace the scanner with one advertising the requested address whose `__aexit__` sets an event and awaits an unset `asyncio.Event`. Replace BleakClient with an immediately successful fake. Start `BleTransport.connect()`, wait for the exit event, then inspect the task. Result: task remains pending, fake BLE client is connected, and notification setup has never started. Cancellation was used to clean up the fake task. Installed Bleak 3.0.2 confirms that scanner exit awaits `stop()`; the BlueZ backend awaits `StopDiscovery` and its manager lock without a local timeout.

**Fix guidance:** Apply an end-to-end connection budget shared by initial, manual, CLI, and reconnect paths. Bound scanner start and stop as well as BLE connect/notify, including scanner cleanup when connection fails or is cancelled. Bound the complete discovery lifecycle too. Preserve the scan/connect overlap that fixes disappearing advertisements. Convert budget expiry to `TransportError` so the bridge returns its structured 503 response and releases its lock. Add fake-scanner regression cases for hung start, hung stop after successful connect, and cancellation during failed-connect cleanup; assert later requests can acquire the bridge lock.

### P2 — A queued poll can dereference a client removed by disconnect, permanently stopping updates

**Location:** `src/lighttime/bridge.py:314`–`:318`, with shutdown impact at `:325`–`:330`.

`poll()` tests `self.client` before acquiring `self.lock`, then uses it after the await without rechecking. A disconnect or failed replacement connection can set `self.client = None` while the poll waits for that lock. The resulting `AttributeError` escapes the suppression block and ends the only polling task. Future SSE clients receive keepalives and initial snapshots, but no periodic readback. At shutdown, awaiting that failed task re-raises its exception before the final client-close block.

**Offline repro:** Connect a MockTransport client, add a subscriber queue, acquire `bridge.lock`, start `bridge.poll()`, and let its 2 s sleep elapse. While retaining the lock, close the mock client and set `bridge.client = None`, then release it. Awaiting the poll produces `AttributeError: 'NoneType' object has no attribute 'refresh'`.

**Fix guidance:** Recheck/capture the current client inside the bridge lock and skip the iteration if absent. Keep expected transport/protocol failure handling inside the loop so one failure cannot stop polling. Make shutdown close the client in a `finally` even if a background task already failed. Test the queued-poll/disconnect interleaving and a subsequent successful mock connection that continues to publish refreshed state.

### P2 — Disconnect failure discards the only handle to a potentially live BLE connection

**Location:** `src/lighttime/transport.py:153`–`:158`; replacement at `:121`.

`disconnect()` clears logical state, suppresses every ordinary backend error/timeout, and unconditionally drops `_client`. If the backend is still connected after the 5 s deadline, the transport loses its cleanup handle and reports normal completion. The next connection creates a new client. A still-connected G60 may no longer advertise, so retries can repeatedly report “not advertising” while this process still owns the old connection. This is particularly relevant to repeated bridge restarts and failed startup recovery.

**Offline repro:** Assign a fake `_client` with `is_connected = True` whose `disconnect()` raises `TimeoutError`. After `await transport.disconnect()`, the fake remains connected, `transport.connected` is false, and `transport._client` is `None`. The installed Bleak BlueZ backend also has cancellation paths during its Disconnect D-Bus call that exit before its subsequent bus-disconnect cleanup; cancelling its coroutine is not proof of cleanup.

**Fix guidance:** Retain ownership of any backend whose cleanup has not completed; do not silently overwrite or abandon it. Use bounded cleanup/retry, surface cleanup failure to the caller, and prevent a replacement session until the previous session is confirmed disconnected or its backend resources are explicitly disposed through a supported mechanism. Clear handles in a `finally` only when disposal is actually complete. Test a disconnect timeout followed by retry, including late disconnect callbacks from the previous generation so they cannot invalidate a new connection. Do not use adapter resets or provisioning as recovery.

### P2 — Failed reconnect identification leaves the old identity/state usable on an unverified session

**Locations:** `src/lighttime/client.py:109`–`:116`, `:143`–`:150`, `:59`–`:62`.

Initial `connect()` disconnects on any identification/refresh failure, but the reconnect branches in `_request()` and `_send()` do not. Exceptions from their `transport.connect()` / `_identify()` calls occur inside the exception handler, outside the protected request/send body. If BLE connection succeeds and identification then fails, the backend remains connected and `_info`/`_state` retain the previous successful session. `state` returns that old connected snapshot. A subsequent refresh/set uses the old device ID without repeating identification. Polling suppresses the resulting protocol error and can publish the stale connected snapshot.

**Offline repro:** A MockTransport succeeds on initial connection, raises `RequestTimeout` on the next read, then returns a non-PL103 INFO response after reconnect. `refresh()` raises the intended `ProtocolError`, but `client.state` still contains `connected: true`, the previous PL103 identity, and 5%/5000 K. A subsequent `refresh()` succeeds without any new identification. An identification-query timeout exercises the same missing cleanup path.

**Fix guidance:** Centralize connect+identify into one guarded operation used by both retries. Invalidate the previous identity and verified snapshot before reconnecting; on any failure or cancellation, perform bounded cleanup and keep state disconnected/unverified. Assign `_info` only after all identity queries succeed. Add read- and setter-retry cases with failed INFO, failed DEVICE_ID, and cancellation; assert no later control request can bypass identification or reuse the old ID.

### P3 — The public HTTP contract still advertises serial runtime connection

**Location:** `docs/api-contract.md:20`–`:22`; actual guard `src/lighttime/bridge.py:200`–`:203`.

The POST `/v1/connect` schema lists `serial` and a serial address as supported inputs, while the implementation deliberately rejects serial runtime control. The later protocol notes correctly limit USB to identity, but the endpoint schema can still lead an SDK implementer to present serial as an available connection.

**Fix guidance:** Document successful runtime connect transports as BLE and opt-in mock, explicitly state serial returns an invalid-request error, and direct USB identification to `lighttime usb-info`. Serial remains appropriate in discovery results. Do not promote USB brightness/CCT support without response/readback evidence.

## Checks and limits

- `uv run --frozen pytest -q tests/test_core.py`: **12 passed**. Those tests cover the codec, basic control/readback, one successful mocked reconnect, auth, CORS preflight, and SSE initial state. They do not exercise scanner lifecycle deadlines, failed reconnect identification, disconnect cleanup failure, or the poll/disconnect race above.
- Auth review found bearer checks on protected routes, explicit origin matching, preflight support for Authorization/Content-Type and private-network requests, and CORS headers on prepared SSE responses. No supported auth/CORS bypass was found. A deployed browser's local-network permission behavior was not tested by these offline checks.
- Setters use GATT write acknowledgement followed by separate readback. Fractional brightness and non-100 K CCT validation happens before control writes. Device ID 0 is supported. These agree with the supplied live-device evidence.
- The hardware verification script attempts baseline restoration in its `finally`, records success only after verified restoration returns, and writes the report even on failure. Its connection/recovery reliability inherits the transport/client findings above. It was not executed.
- No runtime imports or calls into `vendor/` were found. A normalized nonblank-line comparison of the six Python implementation files against 43 local upstream Python/JavaScript reference files found no matching block of five or more lines; inspected unlicensed reference codec/BLE implementations are structurally different. This found no copying evidence, but is not a complete provenance audit.
- The implementation correctly rejects bridge serial runtime control and labels USB identity as unverified for runtime control. The contradictory connect schema is the documentation exception noted above.

Recommended fix order: bound the entire scanner/connection lifecycle, preserve failed-cleanup ownership, guard reconnect identification, then repair poll/shutdown handling. Retest with fakes first; hardware reconnection/restoration verification belongs to the parent.
