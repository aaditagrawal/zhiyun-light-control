# Effects review response

Addressed both supported P2 findings from [effects-review.md](effects-review.md)
offline on 2026-10-23. Changes are limited to `src/lighttime/client.py`,
`src/lighttime/effects.py`, `src/lighttime/bridge.py`, new cases in
`tests/test_effects.py`, and this response. The parent's explicit `/ui` and
`/ui/` index redirects are preserved.

## Cancellation-resistant restoration after shutdown

`LightClient.set` now accepts an optional private `_guard` callback. Existing
public setter arguments and behavior remain compatible; the HTTP/SDK wire API
is unchanged. The effect engine passes its bridge/run/client ownership check,
and manual bridge writes pass `require_running`. Guarded calls also check the
owning asyncio task's cancellation count, so a backend that catches cancellation
cannot authorize another setter simply by eventually returning successfully.

The guard runs before and after each physical CCT/brightness setter and each
verification query, before committing readback state, and across retries and
reconnect identification. A failed setter can still perform required disconnect
cleanup, but cannot start a reconnect after its guard has been revoked. Checks
after successful I/O sit outside the transport-error retry block, so revoking
the guard does not accidentally trigger a retry. Uninterrupted restoration still
force-writes and verifies both baseline fields, independent of cached state.

Cancellation does not discard the already submitted operation. It remains in
its existing owned HTTP/effect task, retaining the bridge/client locks until it
settles. A drain timeout still reports `cleanup unresolved` and retains the
client; final cleanup begins only after owned work drains. No new lock order,
background task or shutdown wait budget was introduced.

Five new ephemeral-server regressions stall cancellation-resistant restoration
at the CCT setter, brightness setter, brightness verification read, and CCT
verification read, plus automatic completion restoration at its first setter.
They record command **entry** with the stopping flag, assert that no next setter
or query is submitted after shutdown, and forbid a post-cancellation verified
snapshot commit. Each also checks prompt HTTP stopping behavior where applicable,
retained ownership during bounded failed cleanup, and successful cleanup after
the original operation is released. Before the fix the first two cases failed
with post-stopping brightness setter/read entries matching the review repro.

## Rejected manual patches

Direct `LightClient.set` and HTTP `PATCH /v1/state` now share the same pure
`_validate_set` helper. The bridge checks complete value types, ranges,
resolution and power/brightness agreement before acquiring the effect control
lock or cancelling the runner. Existing key/null/body validation stays ahead of
that step. Invalid requests therefore return HTTP 400 `invalid_request` while
preserving the active effect ID, running flag, error status and physical output.
Arbitrarily large brightness integers also return this validation error instead
of overflowing conversion. Valid manual patches still cancel/drain without
restoration before applying their verified settings.

Twenty-two new patch cases exercise both direct HTTP and Python BridgeClient
paths, including invalid types, range/resolution failures, mixed valid/invalid
fields, contradictory power values and existing empty/unknown/null rejection.
Each asserts an unchanged active effect and no output writes, then confirms that
a valid manual override stops it and applies the requested settings.

## Validation and limits

Validation uses the frozen lock, Python 3.14.8, Ruff 0.17.2 and ty 0.0.91; no
dependencies or lockfiles changed.

- `uv run --frozen pytest -q tests/test_effects.py -k 'shutdown_mid_restoration or rejected_manual_patch'`:
  **27 passed, 80 deselected in 2.70 s**.
- `uv run --frozen pytest -q tests/test_core.py tests/test_reliability.py tests/test_bridge_shutdown.py`:
  **87 passed in 8.31 s**, preserving scanner/client/shutdown ownership regressions.
- `uv run --frozen pytest -q`: **222 passed, 34 subtests passed in 26.77 s**.
- `uv run --frozen ruff check src/lighttime tests`: passed.
- `uv run --frozen ruff format --check src/lighttime/client.py src/lighttime/effects.py src/lighttime/bridge.py tests/test_effects.py`:
  passed, all four files formatted.
- `uv run --frozen ty check src/lighttime tests`: passed.

These tests use only their own ephemeral loopback servers and in-memory lamp.
No hardware, Bluetooth adapter, live service/API, systemd, credentials, existing
processes or GitHub mutations were accessed. No nested agents or commits were
created. The parent's reported live five-pattern test and 5% / 5000 K baseline
restoration, TypeScript/browser and Android results were not repeated here;
loading these final Python changes and any further hardware validation remain
with the parent.
