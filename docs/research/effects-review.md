# Effects and personal-controller review

Both findings below were subsequently fixed and covered by regressions; see
[the implementation response](effects-review-response.md). The observations
are retained as the review record.

Reviewed the present implementation against `docs/effects-contract.md`, using
offline tests and the existing in-memory lamp fixture. Two supported P2 findings
remain. No source files were edited.

## P2 — A cancelled restoration can submit its second setter after shutdown

Locations: `src/lighttime/effects.py:274–277`,
`src/lighttime/client.py:206–209`, `src/lighttime/bridge.py:455–469`.

`EffectEngine._output()` checks the bridge lifecycle before calling
`LightClient.set(brightness=baseline, cct=baseline)`. That call submits CCT and
brightness separately. If the first setter suppresses cancellation while waiting
for its acknowledgment, `LightClient.set()` proceeds to submit brightness after
`begin_shutdown()` has marked the bridge stopping and cancelled owned work.
There is no lifecycle/cancellation check between those submissions.

Reproduced with the existing `tests/test_effects.py` rig: start an unbounded blink
at 9%, 2700 K from the default 5%, 5000 K baseline; stall the next setter and enable
`rig.ignore_cancel`; start `stop_effect()`; wait for the stalled restoration CCT
setter; call `bridge.begin_shutdown()`; wait for cancellation acknowledgment;
release the stalled setter. Instrumenting entry to `lamp._write()` produced:

```text
setter entry (command, bridge stopping): [('CCT', False), ('BRIGHTNESS', True)]
```

The first CCT write was already submitted and must remain owned until drained.
The brightness write is a new submission after stopping. This contradicts the
explicit requirement that shutdown not start restoration writes while stopping;
it can change output after shutdown cancellation. Existing
`test_shutdown_interrupts_stop_restoration_without_new_writes` cancels before
restoration starts, so it does not exercise this boundary.

Check cancellation/lifecycle between physical setter submissions while retaining
ownership of the already submitted setter. Preserve the force-write of both
baseline fields for normal, uninterrupted restoration. Add a regression that
records setter **entry**, since completion timestamps alone cannot distinguish
an already submitted write from a newly dispatched one.

## P2 — A rejected manual patch silently cancels the active effect

Locations: `src/lighttime/bridge.py:356–372`,
`src/lighttime/client.py:189–198`.

`set_state()` validates keys/nulls, then cancels and drains the effect before
`LightClient.set()` validates numeric ranges, types, resolution, and contradictory
power values. A rejected request therefore mutates the runner even though no
manual setting is applied. This is reachable through the Python SDK as well as
direct HTTP; the TypeScript SDK validates these patches locally.

Reproduction using the existing offline HTTP fixture:

```python
await sdk.start_effect("blink", duration=None, maxBrightness=9, cct=2700)
await eventually(lambda: rig.transports[0].brightness == 9)
response = await http.patch(url + "/v1/state", json={"brightness": 101})
assert response.status == 400
assert (await sdk.effect())["running"] is False
assert (await sdk.effect())["lastError"] is None
```

Observed response: `invalid_request`, with the whole-percent brightness range
error. The active effect became stopped without an error and the lamp held its
last output. An invalid manual request should leave the existing runner intact;
validate the complete patch before cancelling it. This is a user-visible new
side effect of integrating manual control with the engine, rather than a request
for additional input hardening.

## Validation and limits

- `uv run pytest -q tests/test_effects.py tests/test_bridge_shutdown.py`:
  **93 passed**, 14.74 seconds.
- `bun run --cwd packages/sdk test`: **37 passed**, including effect contract,
  mutation ordering, timeout/abort/disposal, and tokenless JSON/SSE cases.
- `bun run --cwd packages/sdk typecheck`: passed.
- Both findings were reproduced separately with ephemeral loopback mock HTTP
  and the existing in-memory lamp; no physical hardware or live services used.
- Final artifact check: `src/lighttime/static/{index.html,style.css,app.js}` are
  byte-identical to `examples/browser` assets. The packaged bundle includes
  no-auth health detection and effect routes; an earlier packaging gap closed
  during review and is not an outstanding finding.
- Read the personal autoattach, reconnect, polling, pagehide/pageshow, session
  epoch, and Python/TypeScript SDK paths. No further supported defect found in
  those paths. Browser regression harness execution was not repeated here.
- The resolved cancelled-setter/cache-baseline issue is not re-reported: the
  existing two-field force-restoration regressions passed. Existing scanner
  ownership and bounded HTTP shutdown behavior were left intact.
- Actual G60/Tailscale behavior belongs to the parent's hardware validation;
  Android native physical tests remain explicitly deferred.
