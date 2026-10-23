# Python reliability review response

Implemented the supported findings from `python-review.md`, offline, on 2026-10-23.

- BLE transport connections now share a 25 s lifecycle deadline across scanner
  startup, advertisement wait, BLE connect, scanner stop, notification setup and
  retries. Cleanup has a separate 5 s bound. Discovery bounds scanner startup and
  shutdown too. Scanning remains active through BLE connection/service discovery.
- Backend operations use owned tasks and bounded waits rather than awaiting an
  indefinitely stalled cancellation handler. Failed cleanup retains the client,
  scanner and pending operations, raises `TransportError`, and blocks replacement
  until cleanup succeeds. Discovery cleanup is retained and retried before later
  discovery or BLE connect. Old disconnect and notification callbacks cannot
  affect a newer connection generation.
- Initial and retried client connections share guarded identification. Identity
  and verified state are invalidated before reconnect/close; INFO/DEVICE_ID errors,
  timeouts and cancellation trigger cleanup. Later reads/setters cannot reuse the
  previous device ID without a successful explicit connect and identification.
- The bridge retains failed connection candidates for cleanup retry, rechecks
  polling clients under its lock, and closes clients in shutdown `finally` paths
  even after a background-task failure. Initial autoconnect remains asynchronous;
  health and disconnected state responses are available immediately.
- The contract advertises BLE and opt-in mock runtime connections. Serial remains
  discoverable and available to CLI `usb-info`; serial runtime connect is HTTP 400.
- For the additional stale-BlueZ-link evidence, a nonadvertising, explicitly chosen
  Linux MAC address now produces precise `bluetoothctl info <address>` and targeted
  `bluetoothctl disconnect <address>` manual instructions after confirming no other
  app owns the link. No automatic disconnect of another owner's connection, cached
  backend reuse, adapter reset or mesh reset was added.

Validation:

- `uv run --frozen pytest -q`: **79 passed, 34 subtests passed**.
- `uv run --frozen pytest -q tests/test_reliability.py`: **39 passed**.
- `uv run --frozen ruff check src/lighttime tests`: passed.
- `uv run --frozen ruff format --check src/lighttime/transport.py src/lighttime/client.py src/lighttime/bridge.py tests/test_reliability.py`: passed.
- `uv run --frozen ty check src/lighttime tests/test_reliability.py`: passed.
- Whole-repository `uv run --frozen ty check` reports one existing diagnostic:
  `tests/test_core.py:82`, `send(self, *args)` is an incompatible override of
  `Transport.send(command, payload)`. That test and the base method signature were
  not changed; the test is outside this task's ownership.

The fake-Bleak regressions cover hung start/stop/connect/notify/disconnect and
cancellation handlers, cleanup failures and retry, cancellation during failed
connect cleanup (including repeated cancellation), stale-generation callbacks,
read/set reconnect INFO/ID failures/timeouts/cancellation, poll/disconnect races
with subsequent refreshed publication, failed background-task shutdown, HTTP
readiness, structured failure/lock release, serial runtime rejection, and the
stale-link manual recovery message.

No live bridge process, systemd unit, hardware, Bluetooth adapter or camera was
touched. No other SDK/Android/OBS implementation paths were changed, no agents
were spawned and no commit was made. A backend that never finishes cancellation
cannot be declared recovered: ownership and the explicit cleanup error remain.
The parent must restart its old imported snapshot and perform real-device
reconnection/control/readback/restoration validation, releasing the known stale
G60 link first as planned.
