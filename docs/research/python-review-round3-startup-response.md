# Python reliability review — R3-1 startup response

Addressed R3-1 only, offline, on 2026-10-23. Read `python-review.md`,
`python-review-response.md`, `python-review-round2.md`,
`python-review-round2-response.md`, and `python-review-round3.md` before editing.
Implementation changes are limited to `src/lighttime/transport.py`,
`tests/test_reliability.py`, and this response. R3-2 belongs to the separate
bridge/CLI work; those files and `tests/test_bridge_shutdown.py` were not edited.

`_ScannerSession` now retains the original startup task separately from
cancellable client operations. Startup deadlines and caller cancellation bound
the wait without cancelling that task. Cleanup waits for that same startup to
succeed before invoking the original public scanner stop, whose existing
one-shot task is also preserved across deadlines/cancellation. This allows a
delayed BlueZ `StartDiscovery` reply to deliver its stop closure after the
original caller has returned. Only successful startup followed by successful
original stop establishes disposal of an attempted startup.

If startup actually fails or is cancelled by the backend after possible side
effects, its completed task and scanner remain owned with an explicit
`cleanup unresolved` error. Cleanup does not invoke a public stop that may be a
no-op, restart startup, or permit a replacement backend. An expired deadline
before any startup invocation can still release the unused scanner. Pending
startup is retained indefinitely if necessary; after a cleanup deadline passes,
a later cleanup call must resume disposal. This deliberately does not claim
recovery from a permanently failed or cancelled startup without a supported
disposal path.

The existing real-Bleak/fake-bus fixture now models the daemon accepting
`StartDiscovery` while withholding its reply. Twelve new cases cover:

- Both public owners (`discover()` and `BleTransport.connect()`), with startup
  deadline expiry and caller cancellation. The same original startup remains
  pending and uncancelled, cleanup/replacement attempts remain bounded, and no
  public stop runs before the reply. Releasing the reply produces exactly one
  `StopDiscovery` and filter removal, then permits replacement.
- Both owners with actual startup failure or backend cancellation, either
  immediately after acceptance or after an earlier deadline. Removing the
  injected failure does not clear the retained failure, call public stop, or
  allocate replacement scanner/client resources.

The eight existing real-backend stop regressions are preserved and pass. The
older generic lifecycle test now expects scanner startup, like scanner stop,
to remain uncancelled. Linux `DuplicateData=True`, three attempts sharing the
40-second connection budget, scan/connect overlap, bounded cleanup, generation
guards, and the policy against automatically disconnecting another process's
link are preserved.

Validation used the existing Python 3.14.8 pin and frozen lockfile:

- Before the implementation change, all twelve new startup cases failed;
  the eight existing real-backend stop cases passed.
- `uv run --frozen pytest -q tests/test_reliability.py -k bluez`:
  **21 passed, 41 deselected** (20 real-backend startup/stop cases plus the
  existing offline stale-link diagnostic case).
- `uv run --frozen pytest -q tests/test_core.py tests/test_reliability.py`:
  **74 passed in 7.44 s**.
- `uv run --frozen pytest -q`:
  **102 passed, 34 subtests passed in 9.81 s**.
- `uv run --frozen ruff check src/lighttime tests`: passed.
- `uv run --frozen ruff format --check src/lighttime/transport.py tests/test_reliability.py`:
  passed, both files formatted.
- `uv run --frozen ty check src/lighttime tests`: passed.

These results describe the shared checkout at validation time; the parent will
load and test the final combined implementation. The BlueZ fixture uses the
installed, unmodified Bleak 3.0.2 scanner and manager with an in-memory bus and
never initializes system D-Bus. Its cases are Linux-specific and do not prove
hardware or other-platform behavior. HTTP tests use their own ephemeral mock
servers. No hardware, Bluetooth adapter, camera, token files, live APIs,
existing processes, bridge service, or systemd units were accessed. No nested
agents were spawned and no commit was made. Live G60 validation remains with
the parent.
