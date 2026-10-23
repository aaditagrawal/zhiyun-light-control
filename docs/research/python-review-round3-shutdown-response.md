# Python reliability review — round 3 shutdown response

Addressed **R3-2 only**, offline, on 2026-10-23. Read the original review and
response, round 2 review and response, and round 3 review before editing.
Owned changes are limited to `src/lighttime/bridge.py`, `src/lighttime/cli.py`,
new `tests/test_bridge_shutdown.py`, and this response. R3-1 and its transport
tests belong to the other implementation agent.

## Shutdown semantics

`on_shutdown` now calls `begin_shutdown()`: it stops admission, wakes SSE with
the existing sentinel, and requests cancellation of tracked device operations,
initial connection and polling. It does not await client locks. AppRunner then
performs its configured HTTP handler drain. `on_cleanup` invokes `shutdown()`
to drain owned operations before final client/scanner cleanup. Direct callers
of `Bridge.shutdown()` still perform both phases.

Authenticated HTTP discovery/connect/disconnect/read/set handlers run as
explicitly owned tasks. Their HTTP wrappers wait for either completion or the
shutdown signal. Shutdown returns structured HTTP 503 `bridge_stopping` to
these callers without making aiohttp's request drain await an indefinitely
pending backend cancellation handler. New device requests receive the same
503; auth/origin checks still run first. Disconnected state remains immediately
available during normal startup.

Operations recheck stopping under the bridge lock. Connection replacement also
checks after closing the previous owner and before allocating the replacement,
and after connection before promoting its candidate. A late successful connect
remains the owned cleanup candidate. Polling checks stopping under the lock.
Thus queued handlers and delayed body/close completions cannot start a new
backend after shutdown has already finalized cleanup.

Operation drain waits at most **11 seconds**, allowing cancellation to traverse
the transport/client's two possible five-second cleanup attempts. If work is
still pending, shutdown raises `TransportError` with `cleanup unresolved`,
retains its tasks and clients, and does not race final cleanup against an
operation that could still connect/reconnect. Once it settles, an explicit
later shutdown can retry cleanup. No unresolved operation is reported cleaned.

Final cleanup concurrently closes candidate/current clients and retries the
transport's retained discovery cleanup. Client fields clear only after close
succeeds. Final cleanup itself is an owned task with a **six-second caller
wait**; expiry retains that same task and its clients without cancelling it.
A later shutdown waits for it, or retries after a completed failure. Background
task errors still propagate after final cleanup has been attempted.

With the CLI's existing five-second HTTP drain, the configured waits total
**22 seconds** (5 + 11 + 6), below the repository's 30-second unit limit.
This bounds AppRunner/bridge cleanup waits with a responsive event loop, not
physical disposal of a permanently hung backend or all process teardown.
In particular, aiohttp/asyncio's final event-loop teardown can itself wait for
cancellation-resistant tasks. There is no claim of a successful clean stop in
that unresolved case, no automatic adapter reset, and no forced disposal of
another owner's resources. Actual service-stop timing remains untested here.

The SSE stopping guard, publication suppression, queue replacement with the
sentinel, subscriber removal and EOF behavior are preserved. The CLI change
only documents how its existing handler drain fits the lifecycle.

## Offline regressions and validation

The new file contains **13 tests** using real ephemeral `aiohttp.AppRunner`
servers, real `LightClient`/`BleTransport`, and in-memory scanner/GATT mocks.
Ordinary operation deadlines exceed the asserted shutdown bound; successful
shutdown requires cancellation rather than waiting for those deadlines.

- Active manual BLE connect, identification, read, set and discovery: bounded
  cleanup, structured stopping response, released locks, empty operation set,
  no live scanner/backend, and no later allocation/connect/write after cleanup.
- Five queued device requests plus newly arriving device/SSE requests while
  stopping: rejection without additional backend allocation or writes.
- Cancellation-resistant GATT read: HTTP caller completes promptly; cleanup
  fails boundedly with its operation, client and lock still explicitly owned.
  Releasing the mock and retrying cleanup leaves no live backend.
- Late cancellation-resistant BLE connect: failed cleanup preserves the
  original backend and candidate, allocates no replacement, and a retry after
  late connection completion disconnects that same backend.
- Disconnect failure retains the active client until a successful retry.
- Delayed body parsing and replacement-client close that swallow cancellation
  cannot allocate a replacement after stopping.
- Final close timeout retains the original cleanup task/client and finishes
  without a duplicate close when released. A pending discovery stop remains
  owned and is also eventually cleaned after release.

Validation used the existing frozen lock and Python 3.14.8 pin, Ruff 0.17.2 and
ty 0.0.91; no dependencies or lockfiles were changed:

- `uv run --frozen pytest -q tests/test_bridge_shutdown.py`:
  **13 passed in 1.14 s**.
- `uv run --frozen pytest -q tests/test_core.py tests/test_reliability.py tests/test_bridge_shutdown.py`:
  **85 passed in 7.80 s** at the intermediate 11-new-test stage.
- Final `uv run --frozen pytest -q`:
  **115 passed, 34 subtests passed in 10.93 s**. This shared-workspace result
  includes the other agent's concurrent R3-1 changes; only the 13 shutdown
  tests are new in this response. Both existing SSE AppRunner regressions pass.
- `uv run --frozen ruff check src/lighttime tests`: passed.
- `uv run --frozen ruff format --check src/lighttime/bridge.py src/lighttime/cli.py tests/test_bridge_shutdown.py`:
  passed, all three files formatted.
- `uv run --frozen ty check src/lighttime tests`: passed.

No hardware, Bluetooth adapter/system D-Bus, camera, credential/token files,
live API, systemd, existing bridge or other existing process was accessed.
No nested agents or commits were created. Mock servers used their own
ephemeral loopback ports. Live service restart, real SSE restart/EOF validation,
device reconnect/control/readback and baseline restoration remain with the
parent after these final source changes are loaded.
