# Python reliability review — independent round 2

Reviewed 2026-10-23. Read both `python-review.md` and
`python-review-response.md` in full, every file in `src/lighttime`, and
`tests/test_core.py`, `tests/test_reliability.py`, and `tests/test_obs.py`.
Also checked the revised API contract and the installed Bleak 3.0.2 BlueZ
scanner/manager implementation. This review includes the parent's subsequent
Linux `DuplicateData=True` change and the corrected `IgnoresWrites.send`
signature.

**Verdict:** the five original failure paths are addressed, with one concrete
P2 residual in the new scanner-cleanup ownership mechanism. Scanner operations
are bounded, but cleanup can falsely succeed after cancelling the real BlueZ
backend's stop operation. The fake scanner does not model this behavior.

## Disposition of the original findings

1. **P1, unbounded scanner startup/shutdown: boundedness resolved.**
   `transport.py:62`–`:98`, `:114`–`:129`, `:211`–`:294`, and `:412`–`:443`
   apply deadlines to backend tasks, startup, scanning, connect, scanner stop,
   notification setup, discovery and cleanup. Backend cancellation handlers no
   longer have to finish before the caller's bounded wait returns. The shared
   connection deadline also covers retries, and scanning remains active during
   BLE connection. The initial/manual HTTP, SDK and retry paths use this same
   transport implementation. The new cleanup-completeness claim has the P2
   exception detailed below; this does not reintroduce an unbounded wait.
2. **P2, poll/disconnect race and skipped shutdown cleanup: resolved.**
   `bridge.py:323`–`:352` captures/rechecks the client while holding the lock,
   continues polling when it is absent, and closes candidates/current clients
   in shutdown `finally` paths. The tests reproduce the queued-poll race, then
   reconnect and verify a refreshed publication. Both failed-background-task
   shutdown cases close the client.
3. **P2, discarded live BLE client on disconnect failure: resolved for the
   original client-handle finding.** `transport.py:318`–`:345` retains failed
   client cleanup, reports failure, checks `is_connected` after a successful
   disconnect call, and blocks replacement. `bridge.py:196`–`:245` retains the
   failed candidate or active client until close succeeds. Generation/client
   checks reject old disconnect and notification callbacks. Tests cover errors,
   cancellation-resistant timeout, successful-return-but-still-connected, retry
   and old callbacks. Scanner ownership is separately incomplete as described
   in R2-1.
4. **P2, failed reconnect INFO/DEVICE_ID reuses old identity/state: resolved.**
   `client.py:59`–`:88`, `:97`–`:150`, and `:170`–`:180` centralize guarded
   connection/identification and invalidate identity/state before connecting
   or disconnecting. Identity is assigned only after both queries succeed.
   Read and setter retries with bad INFO, bad ID, either query timeout, and
   cancellation all leave an unverified/disconnected snapshot. Later reads or
   setters cannot send control frames until explicit identification succeeds.
5. **P3, serial runtime connection advertised by HTTP contract: resolved.**
   `docs/api-contract.md:20`–`:25` advertises BLE and opt-in mock, explicitly
   documents serial runtime HTTP 400, and points to CLI `usb-info`. The serial
   rejection/discovery regression passes. USB identity remains distinct from
   verified runtime control.

## Remaining finding

### R2-1 — P2: cancelled BlueZ scanner stop can be declared cleaned up without stopping discovery

**Locations:** `src/lighttime/transport.py:71`–`:94`, `:117`–`:120`,
`:326`–`:329`, `:406`–`:409`.

`_BleOperations.run()` cancels a timed-out backend call. On a later cleanup,
`drain()` removes completed tasks without distinguishing successful completion
from cancellation/failure. `_ScannerSession._stop()` then calls `scanner.stop()`
again and marks the session closed if that call returns.

This is unsafe with the installed Linux backend. Bleak 3.0.2
`bleak/backends/bluezdbus/scanner.py:139`–`:144` executes
`stop, self._stop = self._stop, None` **before** awaiting the stop closure.
The manager closure removes local callbacks, then awaits its D-Bus lock
(`manager.py:479`–`:498`) before sending `StopDiscovery`. If the operation times
out while waiting for that lock, cancellation completes normally but
`StopDiscovery` was never sent. A subsequent public `scanner.stop()` returns
immediately because its stop handle is already `None`.

The application consequently sets `session.closed = True`, drops the scanner
from `_discovery_scanners` or `BleTransport._scanner`, and permits replacement
even though its own discovery session was never stopped. This defeats the new
ownership/replacement guarantee and can leave this process's BlueZ discovery
session and filters active without a retryable stop handle. The same problem
applies when the first stop raises after consuming the handle. This is about
cleanup of this process's scanner, independent of another application's G60
connection or the separate unchanged-advertisement issue.

**Offline reproduction:** the following runs the installed, unmodified Bleak
BlueZ scanner and manager against an in-memory fake bus. It never initializes
a real D-Bus connection, enumerates hardware, or sends a Bluetooth operation.
The manager lock models the exact await before `StopDiscovery` in production.
Run from the repository root:

```bash
uv run --frozen python - <<'PY'
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bleak import BleakScanner
from bleak.backends.bluezdbus.manager import BlueZManager
from bleak.backends.bluezdbus.scanner import BleakScannerBlueZDBus
from dbus_fast.constants import MessageType

import lighttime.transport as transport

class FakeBus:
    def __init__(self):
        self.calls = []
        self.active = False

    async def call(self, message):
        self.calls.append(message.member)
        if message.member == "StartDiscovery":
            self.active = True
        elif message.member == "StopDiscovery":
            self.active = False
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN)

async def main():
    manager = BlueZManager()
    manager._properties["/org/bluez/offline"] = {}
    bus = FakeBus()
    manager._bus = bus

    def factory(**kwargs):
        bluez = dict(kwargs.get("bluez", {}), adapter="offline")
        return BleakScanner(backend=BleakScannerBlueZDBus, bluez=bluez)

    with patch(
        "bleak.backends.bluezdbus.scanner.get_global_bluez_manager",
        AsyncMock(return_value=manager),
    ), patch.object(transport, "BleakScanner", factory):
        session = transport._ScannerSession()
        await session.start(transport._deadline(0.1))
        await manager._bus_lock.acquire()
        try:
            try:
                await session.close(transport._deadline(0.02))
            except transport.TransportError as exc:
                print(type(exc).__name__, str(exc))
            await asyncio.sleep(0)  # Let the cancelled stop task finish.
        finally:
            manager._bus_lock.release()
        print("stop_handle:", session.scanner._backend._stop)
        transport._discovery_scanners.add(session)
        await transport._retry_discovery_cleanup(transport._deadline(0.1))
        print("closed:", session.closed)
        print("retained:", len(transport._discovery_scanners))
        print("scan_active:", bus.active)
        print("bus_calls:", bus.calls)

asyncio.run(main())
PY
```

Observed:

```text
TransportError Bluetooth scanner stop deadline exceeded; cleanup required
stop_handle: None
closed: True
retained: 0
scan_active: True
bus_calls: ['SetDiscoveryFilter', 'StartDiscovery']
```

**Why the new tests miss it:** `FakeBleak.Scanner.stop()`
(`tests/test_reliability.py:79`–`:85`) can repeat the actual cleanup on every
invocation. It never consumes a one-shot stop handle before awaiting. The
retention cases mostly hold cancellation handlers pending, whereas the real
backend in this reproduction accepts cancellation and loses its stop closure.

**Required correction:** cancellation/failure of a one-shot stop must remain
an unresolved cleanup outcome. A subsequent no-op `stop()` must not establish
successful disposal or unblock replacement. Preserve the original cleanup's
ability to complete, or use a supported disposal path that actually proves
cleanup; otherwise retain an explicit unresolved failure. Add this real-backend,
fake-bus regression (or a faithful one-shot-stop fake). No adapter reset or
disconnect of another controller is required.

## Parent's subsequent advertisement-filter change

The reviewed `_ScannerSession` now passes
`bluez={"filters": {"DuplicateData": True}}` on Linux; other platforms retain
the default constructor. The installed BlueZ backend accepts and forwards this
public filter. An offline run using the real scanner/manager and fake bus
confirmed that `SetDiscoveryFilter` received `DuplicateData=True`. The focused
suite was rerun after this change. It does not change R2-1's stop behavior.

The parent reports successful real control from 5% to 7%/4400 K after service
restart, a first explicit reconnect that returned “not advertising,” and a later
successful connection/restoration to 5%/5000 K. These are parent-supplied results,
not experiments performed by this reviewer. Three reconnect cycles with the
new filter were still pending when this report was written. I do not infer that
the failed real reconnect was caused by R2-1; the reproduction establishes a
separate cleanup defect.

## Validation and limits

- `uv run --frozen pytest -q tests/test_core.py tests/test_reliability.py`:
  **51 passed** after the filter change (12 core, 39 reliability).
- `uv run --frozen ruff check src/lighttime tests`: passed.
- `uv run --frozen ruff format --check src/lighttime/transport.py src/lighttime/client.py src/lighttime/bridge.py tests/test_reliability.py`:
  passed, four files already formatted.
- `uv run --frozen ty check src/lighttime tests`: passed.
- `uv run --frozen ty check`: passed. The parent's test override signature
  correction is present; it is not an outstanding diagnostic or review finding.
- The real-Bleak/fake-bus reproduction above confirmed R2-1, including after the
  Linux filter change. No other concrete residual regression was established
  within the requested scope. The parent's reported full-suite 79 passes/34
  subtests were not independently rerun; OBS tests were read, and the focused
  core/reliability checks were run.

Only this report was written. No implementation/test edits, commits, subagents,
hardware/camera/Bluetooth access, live HTTP writes, existing-process operations,
token-file access or systemd-unit changes were performed. Offline HTTP tests
created their own ephemeral mock servers. Real service restart, repeated
reconnection, control/readback and baseline restoration remain with the parent.
