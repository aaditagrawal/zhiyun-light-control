# Python reliability review — independent round 3

Reviewed 2026-10-23. Read the four preceding Python review/response documents,
the current transport/client/bridge, relevant CLI/service configuration and
core/reliability tests. Inspected the installed Bleak 3.0.2 BlueZ scanner and
manager, dbus-fast call implementation, and aiohttp shutdown ordering. This
includes the subsequent SSE shutdown patch and the three-attempt/40-second
connection adjustment, with its 55-second initial-connect wrapper.

**Verdict:** R2-1 is resolved for an already-started scanner. The SSE patch
resolves the open-stream shutdown reproduction. Two supported residual P2
defects remain: cancelled scanner startup can still falsely establish cleanup,
and active HTTP client operations can prevent shutdown from reaching its
request-drain timeout.

## Findings

### R3-1 — P2: startup cancellation can discard an active scanner before its stop handle is assigned

**Locations:** `src/lighttime/transport.py:74`, `:77`–`:79`, `:89`–`:95`,
`:116`–`:127`, `:142`; ownership release in `_close_scanner()` and
`discover()`'s `finally`.

Scanner startup still uses cancellable `_BleOperations.run()`. In the installed
BlueZ backend, `start()` assigns `self._stop` only **after** awaiting
`manager.active_scan()`. That manager first sends `SetDiscoveryFilter` and
`StartDiscovery`. If BlueZ has accepted the latter but the reply is delayed,
the application's startup deadline or caller cancellation cancels the await
before the stop closure is returned. The manager removes its callbacks on
cancellation, but sends neither `StopDiscovery` nor filter removal.

`drain()` then discards the completed, cancelled startup task. The new persistent
stop task calls public `scanner.stop()` with `_stop is None`, returns normally,
and sets `session.closed = True`. Discovery does not retain the session;
transport cleanup clears `_scanner` and permits replacement. The original stop
fix cannot protect a stop closure that was never delivered by startup.

This is a separate remaining startup path, not a failure of preserving an
already-created stop task. It is also not an indefinitely pending cancellation
handler: the real backend accepts cancellation promptly. The existing fake
scanner can stop even if startup was cancelled before returning, so the startup
tests do not model this BlueZ behavior.

**Offline reproduction:** unmodified installed Bleak scanner/manager with an
in-memory bus. The fake represents a daemon accepting `StartDiscovery` while
the reply remains pending. This matches dbus-fast's send-then-await-reply
ordering; cancelling the local reply future does not retract a sent method.
No system bus is initialized.

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
        self.entered = asyncio.Event()

    async def call(self, message):
        self.calls.append(message.member)
        if message.member == "StartDiscovery":
            self.active = True
            self.entered.set()
            await asyncio.Event().wait()
        elif message.member == "StopDiscovery":
            self.active = False
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN)

async def scenario(cancel_caller):
    manager = BlueZManager()
    manager._properties["/org/bluez/offline"] = {}
    bus = FakeBus()
    manager._bus = bus

    def factory(**kwargs):
        options = dict(kwargs.get("bluez", {}), adapter="offline")
        return BleakScanner(backend=BleakScannerBlueZDBus, bluez=options)

    with patch(
        "bleak.backends.bluezdbus.scanner.get_global_bluez_manager",
        AsyncMock(return_value=manager),
    ), patch.object(transport, "BleakScanner", factory):
        session = transport._ScannerSession()
        task = asyncio.create_task(session.start(transport._deadline(0.02)))
        await bus.entered.wait()
        if cancel_caller:
            task.cancel()
        try:
            await task
        except (transport.TransportError, asyncio.CancelledError):
            pass
        await session.close(transport._deadline(0.1))
        print(cancel_caller, session.closed, bus.active, bus.calls)
        assert session.closed and bus.active
        assert session.scanner._backend._stop is None
        assert "StopDiscovery" not in bus.calls

async def main():
    await scenario(False)
    await scenario(True)

asyncio.run(main())
PY
```

Both cases produce `closed=True`, `active=True`, and only
`['SetDiscoveryFilter', 'StartDiscovery']`. Additional runs through both public
owners (`discover()` and `BleTransport.connect()`), with startup deadlines and
caller cancellation, also left scanning active with no retained discovery or
transport scanner and no `StopDiscovery`. The connect deadline case allocated
a replacement scanner object after falsely successful cleanup.

**Required correction:** retain startup's ability to finish and return its
cleanup handle across caller deadlines/cancellation, then stop that same
scanner. Bound callers' waits without treating cancelled/failed startup plus
a public no-op stop as proof of disposal. If startup's side effects cannot be
resolved, retain an explicit unresolved owner and block replacement. Add the
real-backend delayed-`StartDiscovery` cases for both owners, including eventual
successful startup followed by exactly one actual stop.

### R3-2 — P2: shutdown waits on active HTTP operations before the configured request-drain timeout begins

**Locations:** `src/lighttime/bridge.py:342`–`:364`, specifically awaits of
`_connecting_client.close()` and `client.close()`; `src/lighttime/client.py:128`–
`:130`; `src/lighttime/cli.py:79`–`:80`.

The shutdown hook cancels only `connection_task` and `poll_task`. An active
manual HTTP connect, refresh or setter still owns `LightClient._lock`.
`LightClient.close()` waits for that lock before reaching the bounded BLE
cleanup. Thus `Bridge.shutdown()` waits for the complete HTTP operation,
including any connection/reconnect and identification/readback, rather than
just its five-second cleanup budget.

Installed aiohttp's `BaseRunner.cleanup()` awaits the application shutdown
hook **before** calling `self._server.shutdown(self._shutdown_timeout)`.
Consequently `run_app(shutdown_timeout=5)` cannot cancel/drain the request
that this hook is waiting for. The new `_stopping` checks terminate/reject SSE
and suppress publication, but do not cancel active JSON requests.

**Offline verification:** a real ephemeral aiohttp AppRunner with the existing
FakeBleak, an already-connected client, and an HTTP `GET /v1/state` whose mock
GATT write waits on an event. With `shutdown_timeout=0.01`, cleanup was still
pending after 0.1 seconds, `_stopping` was true, and the client lock was held.
Releasing the event let the request and cleanup finish. The write remained
inside the real transport's ordinary request timeout throughout.

A second reproduction exercised successful manual `POST /v1/connect`, without
an indefinite gate, using all timing budgets scaled by 1:100:

- Connection/scanner/connect/notify/cleanup limits: 0.25/0.05/0.15/0.05/0.05 s.
- Successful scanner start, BLE connect, scanner stop and notify took
  0.035/0.135/0.035/0.020 s respectively, all within the shared connection limit.
- INFO, DEVICE_ID, brightness and CCT writes each took 0.028 s, within the
  scaled request timeout of 0.03 s; each returned its normal mock reply.
- AppRunner request drain was 0.05 s. Cleanup started as scanner startup began,
  remained pending at 0.31 s, and finished around 0.34 s after the HTTP request
  succeeded. This exceeds the scaled 30-second unit limit of 0.30 s even with
  the former 25-second connection budget.

This establishes the shutdown ordering defect without hardware. An actual
30-second systemd timeout was not exercised by this reviewer. The new
40-second connection budget further exceeds the unit's 30-second allowance
before identification/readback; a pending HTTP operation is not covered by
the proposed arithmetic of two five-second cleanups plus request drain.

**Required correction:** cancel/drain tracked HTTP device operations before
waiting on their client locks, or move final client cleanup to a phase after
aiohttp has drained/cancelled handlers. Preserve failed-cleanup ownership.
Reject newly admitted device operations while stopping. Add a real mock-server
shutdown case with an active manual connect/read/set, asserting bounded
completion and no backend allocated or connected after final cleanup.

## Verified corrections and limits

- **R2-1:** all eight real-Bleak/fake-bus stop cases pass. Deadline expiry and
  caller cancellation leave the original stop uncancelled and owned. Releasing
  the manager lock causes exactly one `StopDiscovery` and filter removal, then
  permits a fresh connection. Immediate/delayed actual stop errors remain
  unresolved; subsequent cleanup does not call public stop again or allocate a
  replacement. Retention of a permanently failed one-shot stop is intentional.
- **SSE shutdown:** both new AppRunner cases pass with an empty or full
  subscriber queue. The sentinel wakes a blocked reader, `_stopping` prevents
  replacing it through publish, and the stream reaches EOF promptly. Moving
  `prepare()` inside `try/finally` also covers subscriber removal if preparation
  fails. CLI request drain is five seconds and the repository unit says
  `TimeoutStopSec=30`. R3-2 concerns an additional active JSON operation.
- **Three attempts:** the final source uses three attempts under one 40-second
  connection deadline, keeps cleanup before replacement and scan/connect
  overlap, and uses the 55-second initial-connect wrapper. The new regression
  requires two cleaned-up failed backends before a successful third connection.
  Empty backend exception text now falls back to its exception type. No
  additional supported defect was established in this adjustment itself.
- Failed reconnect identification still invalidates identity/snapshot before
  reconnect and cannot authorize later commands with the old device ID. The
  queued poll/disconnect regression and old-generation callback protections
  still pass. No new supported residual was found in these paths.
- Parent-supplied hardware evidence is mixed: two earlier explicit reconnect
  cycles reportedly passed in 10.37 s and 17.42 s; a later connect exhausted two
  attempts, followed by a successful restoration connect to 5%/5000 K. The
  parent also reported the earlier 15-second service-stop overrun with an open
  live SSE stream. These observations were not reproduced or accessed here;
  they do not prove the advertisement filter or retry increase eliminates
  intermittent hardware failures. Live validation remains with the parent.

## Validation

- Before the final retry adjustment, focused tests: **61 passed**, including
  eight real-Bleak stop cases and two new SSE shutdown cases. Full suite:
  **89 passed, 34 subtests passed**.
- `uv run --frozen pytest -q` after the final retry adjustment:
  **90 passed, 34 subtests passed in 9.20 s**. This includes the final third-attempt
  regression, all core/reliability tests, and offline OBS mock tests.
- `uv run --frozen ruff check src/lighttime tests`: passed after the final retry
  adjustment.
- `uv run --frozen ruff format --check src/lighttime/transport.py src/lighttime/client.py src/lighttime/bridge.py src/lighttime/cli.py tests/test_reliability.py`:
  passed, five files already formatted.
- `uv run --frozen ty check src/lighttime tests`: passed after the final retry
  adjustment.
- Additional in-memory real-Bleak startup reproductions and ephemeral mock
  AppRunner active-request shutdown reproductions established R3-1 and R3-2.
  The existing passing suite does not cover those interleavings.

Only this report was written. No implementation/test edits, commits, nested
agents, hardware/Bluetooth/camera access, live-server requests, credential-file
access, existing-process operations or systemd operations were performed.
HTTP experiments used their own ephemeral mock servers; timing limits were
patched only within those Python processes. The Linux backend tests do not
establish behavior on other operating systems or actual radio/daemon timing.
