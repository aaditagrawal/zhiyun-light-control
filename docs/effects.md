# Bridge effects

The bridge runs one effect independently of the browser, using the G60's
verified brightness/CCT setters. It does not invoke vendor firmware effects.
The same bridge routes work through an already configured Tailscale connection.
This personal testing site uses explicit `--no-auth` mode and opens directly to
controls without tokens, connectors or setup. The normal SDK bridge still
requires authentication by default.
See [the canonical shared contract](effects-contract.md) for the exact wire API.

```python
from lighttime import BridgeClient

async with BridgeClient("https://your-personal-tailnet-host") as light:
    await light.start_effect(
        "breathe", duration=None, period=4,
        minBrightness=0, maxBrightness=10, cct=4300,
    )
    status = await light.effect()
    await light.stop_effect()  # Cancel, then verify original brightness/CCT.
```

Run personal mode with `lighttime serve --no-auth`; it skips token-file creation
and token-path output. Origin checks still apply. The deployment uses loopback
binding behind the existing Tailscale Serve HTTPS route and its exact HTTPS
origin allowlist; the parent owns deployment. `GET /v1/health` always includes
`authRequired`. In personal mode it also includes `defaultAddress`, the
configured startup BLE address or null, so controls can reconnect directly.
Authenticated health omits that address. Python clients may omit `token` or
pass an empty token; neither sends Authorization. Existing authenticated
clients continue to pass `token=token`.

Patterns are `blink`, `breathe`, `fade-in`, `fade-out`, and `cct-sweep`.
Blink starts at maximum, alternating maximum/minimum each half-period.
Breathe starts at minimum and follows a raised cosine through maximum and
back. CCT sweep starts at `cct`, reaches `cctEnd` halfway through its period,
and returns, holding maximum brightness. Fades interpolate minimum to maximum
or maximum to minimum over `duration`, then hold the exact endpoint unless
restoration was requested. Temperature tuning also remains available through
ordinary `PATCH /v1/state` or `BridgeClient.set(cct=4300)`.

`duration` defaults to 10 seconds (range 1–3600). Only cyclic patterns accept
`duration: null`, meaning until stopped. `period` defaults to 4 seconds (range
1–60); fades use duration instead. Brightness bounds are whole percent 0–100,
minimum defaults to 0, and maximum defaults to the verified nonzero baseline
brightness or 5. Maximum must be at least minimum. `cct` defaults to baseline
CCT; `cctEnd` defaults to 6500. Both accept 2700–6500 K in 100 K steps.
`restoreOnComplete` defaults to true for cycles and false for fades. Unknown
fields, nulls other than cyclic duration, unsupported resolution, booleans in
numeric fields, and non-finite inputs are rejected before output writes.

Each accepted start captures freshly verified brightness/CCT. Replacing an
effect cancels and drains its runner without restoration, then captures the
replacement's own baseline from actual readback. Stopping that replacement
restores its baseline, which may be an intermediate output of the earlier
effect. This avoids a restoration flash between effects. Invalid replacement
configuration leaves the existing runner active.

`POST /v1/effects/stop` accepts only an optional boolean `restore` (default
true). It waits for cancellation and requested verified restoration before
returning. `restore: false` holds the last actual output. Stopping an already
completed, failed, or idle effect is idempotent and does not initiate a later
restoration. A valid manual state write cancels/drains the effect without
restoration before applying its setting. Disconnect and device replacement
also cancel/drain without restoration. A late queued frame cannot replay
against a new device or override the manual setting.

Frames use monotonic elapsed time, whole-percent/100 K quantization, and no
more than five output updates per second, including effect restoration and
replacement. Identical confirmed quantized targets cause no ordinary frame
write. Only changed fields are sent through `LightClient.set` for those frames.
Restoration always writes both baseline values and verifies readback, even
when cached values already match: a cancelled setter may have changed physical
output before its acknowledgment/readback updated the cache. Verification is
required before success. The scheduler samples current elapsed time after
waiting for the shared device lock and never catches up by replaying missed
frames. Readback or transport delays can reduce frame rate and delay a finite
endpoint/restoration; this is not a precise strobe timer. An already submitted
hardware write cannot be withdrawn, so cancellation drains in-flight work
before subsequent device mutation or cleanup.

Effect status is queried separately with `GET /v1/effects` or `effect()`;
polling once per second while attached is sufficient. The existing state SSE
continues to report actual verified snapshots. Status fields are exactly
`running`, `id`, `pattern`, `startedAt`, `endsAt`, `progress`, and `lastError`.
Initially only `running` is false; other fields are null. A completed/stopped
run retains its ID, pattern and timestamps, with frozen progress (1 after
finite completion); an unbounded run has null endsAt/progress. A runner or
restoration error stops output and persists in `lastError` until a new start
is accepted. A failed stop restoration also returns the normal structured
HTTP verification/transport error. No automatic restore runs after a failure.

Shutdown rejects effect endpoints with HTTP 503 `bridge_stopping`, cancels
runner/restoration work without starting restoration, and drains owned work
before client cleanup. A cancellation-resistant operation retains ownership
and produces the existing bounded `cleanup unresolved` failure; cleanup can
be retried after it settles. It is never treated as disposed while it could
still perform I/O.

Offline tests cover the HTTP/SDK contract, waveforms, timing/quantization,
finite endpoints, restoration, readback/transport failures, manual overrides,
replacement, SSE, and shutdown ownership using ephemeral loopback mock
bridges. Actual G60 timing, BLE reliability and deployment remain separate
hardware validation tasks.
