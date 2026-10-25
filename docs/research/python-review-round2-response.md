# Python reliability review — round 2 response

Addressed R2-1 (P2), offline, on 2026-10-23. Read `python-review.md`,
`python-review-response.md`, and `python-review-round2.md` in full before editing.
Changes are limited to `src/lighttime/transport.py`, `tests/test_reliability.py`,
and this response.

`_ScannerSession` now owns one persistent `stop_task`, separate from the
cancellable startup operations. Cleanup still waits only until its deadline,
but neither deadline expiry nor caller cancellation cancels the original
scanner stop. Later cleanup attempts wait for that same task. This preserves
the Bleak 3.0.2 BlueZ stop closure while it waits for the manager lock, even
after the public scanner has consumed its one-shot handle.

Only successful completion of the original stop permits `closed = True` and
release of the discovery/transport scanner owner. An exception or cancellation
of that task remains an explicit `cleanup unresolved` error. The failed task
and scanner remain owned; later cleanup never invokes another public `stop()`
whose no-op return could falsely establish disposal. There is no automatic
recovery from an actually failed one-shot stop without a supported path that
proves disposal. A stop that remains pending can still complete in the
background, and a later successful cleanup permits a fresh connection.

The regression fixture runs the installed, unmodified Bleak 3.0.2 BlueZ scanner
and manager against an in-memory fake bus. It replaces manager lookup before
startup and never initializes a system D-Bus connection. Eight new cases cover
both discovery ownership and `BleTransport` ownership:

- Deadline expiry and caller cancellation while the original stop waits for
  the manager lock. Replacement remains blocked and the original stop is not
  cancelled. Releasing the lock produces exactly one `StopDiscovery` followed
  by filter removal; cleanup then releases ownership and a fresh mocked BLE
  connection succeeds.
- `StopDiscovery` failure both immediately and after an earlier deadline.
  Repeated cleanup and connection/discovery retries preserve the error and
  owner, allocate no replacement backend, and invoke public scanner stop only
  once, even after the injected bus error is removed.

The existing lifecycle regression now verifies that timed-out scanner stop is
retained without cancellation. The existing scanner-error regression now
expects unresolved ownership after a stop failure rather than assuming every
backend supports retrying stop. The Linux `DuplicateData=True` constructor
change is preserved and verified at the real backend's fake-bus filter call.
Scan/connect overlap and the no-reset/no-other-process-disconnect policies
are unchanged.

Validation used the existing Python 3.14.8 pin and frozen lockfile:

- Before the implementation change, the six initial real-backend timeout and
  failure cases failed, including delayed failure being falsely accepted as
  successful cleanup. Caller-cancellation variants were added subsequently.
- `uv run --frozen pytest -q tests/test_core.py tests/test_reliability.py`:
  **59 passed** (12 core, 47 reliability; previous focused baseline: 51).
- `uv run --frozen ruff check src/lighttime/transport.py tests/test_reliability.py`:
  passed.
- `uv run --frozen ruff format --check src/lighttime/transport.py tests/test_reliability.py`:
  passed, both files formatted.
- `uv run --frozen ty check src/lighttime tests/test_reliability.py`: passed.

The previous full-suite result of 79 tests/34 subtests was not rerun or
revalidated; validation here is the targeted core/reliability suite. The
real-backend regression is Linux-specific and skips on other platforms.
An indefinitely pending stop remains owned and blocks replacement, with
bounded errors to callers; an actually failed/cancelled stop remains
unresolved rather than being declared recovered.

No hardware, Bluetooth adapter, camera, token files, systemd units, existing
bridge/live server, or other processes were accessed or restarted. HTTP
regressions used their own ephemeral mock servers. No agents were spawned and
no commit was made. Service restart and hardware reconnect/control/readback/
restoration validation remain with the parent.
