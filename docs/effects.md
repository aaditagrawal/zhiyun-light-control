# Bridge effects

The bridge runs one effect independently of the browser, using the G60's
verified brightness/CCT setters. It does not invoke vendor firmware effects.
Run `uv run --frozen lighttime` and open `http://localhost:8877/`: the default
bridge discovers one nearby PL103/G60 and opens controls without an address or
token setup. The same routes work through a configured private HTTPS bridge.
See [the canonical shared contract](effects-contract.md) for the exact wire API.

```python
from lighttime import BridgeClient

async with BridgeClient() as light:
    await light.connect()
    await light.start_effect(
        "breathe", duration=None, period=4,
        minBrightness=0, maxBrightness=10, cct=4300,
    )
    status = await light.effect()
    await light.stop_effect()  # Cancel, then verify original brightness/CCT.
```

The default skips token-file creation and token-path output. Origin checks
still apply. `serve --auth` opts into bearer authentication; existing
`serve --no-auth` commands remain compatible. Python clients omit Authorization
when `token` is absent or empty. `GET /v1/health` includes `authRequired` and,
in no-auth mode, `defaultAddress` startup/reconnect metadata. Authenticated
health omits that address. For remote clients, pass a reachable private bridge
URL to `BridgeClient`; see [website architecture](integrating-sites.md#choose-where-the-site-runs)
and [Python deployment](python.md).

Fourteen patterns are available: `blink`, `breathe`, `fade-in`, `fade-out`,
`cct-sweep`, `pulse`, `flicker`, `sunrise`, `lightning`, `emergency`,
`paparazzi`, `tv`, `morse` and `keyframes`. The light is white-only, so every
pattern uses brightness and correlated colour temperature; `emergency`
alternates warm and cool white rather than colours.
[The expanded contract](effects-programs-contract.md) defines their fields;
[Program reference](#program-reference) below gives the exact formulas.

```python
await light.start_effect("flicker", style="fire", duration=None,
                         minBrightness=8, maxBrightness=45)
await light.start_effect("morse", text="SOS", unit=0.2, maxBrightness=20)
await light.start_effect("keyframes", loop=False, keyframes=[
    {"at": 0, "brightness": 5, "cct": 2700},
    {"at": 3, "brightness": 40, "cct": 5600, "ease": "smooth"},
])
```

Blink starts at maximum and is on for `duty` (default 0.5) of each period.
Breathe starts at minimum and follows a raised cosine through maximum and
back. CCT sweep starts at `cct`, reaches `cctEnd` halfway through its period,
and returns, holding maximum brightness. Fades interpolate minimum to maximum
or maximum to minimum over `duration`, then hold the exact endpoint unless
restoration was requested. Temperature tuning also remains available through
ordinary `PATCH /v1/state` or `BridgeClient.set(cct=4300)`.

`duration` defaults to 10 seconds (range 1–3600); sunrise defaults to 900.
Fades, sunrise, and non-looping Morse/keyframes reject `duration: null`; all
other patterns accept it as until stopped. Without `duration`, non-looping
Morse/keyframes end at their natural length, and a longer explicit duration
still ends there. `period` defaults to 4 seconds (blink 0.2–60, otherwise
1–60) and is ignored by patterns without a period. Brightness bounds are whole
percent 0–100, minimum defaults to 0, and maximum defaults to the verified
nonzero baseline brightness or 5. Maximum must be at least minimum. The
original five patterns, `pulse` and `morse` default `cct` to the verified
baseline; the other new patterns use the constants listed below. `cctEnd`
defaults to 6500 unless listed. `restoreOnComplete` defaults to false for
fades, sunrise and non-looping Morse/keyframes, otherwise true. `seed` is an
unsigned 32-bit integer (default 1). `curve` is `linear` (default) or
`perceptual` (sunrise default). Common fields are accepted by every pattern.
A field belonging to another pattern, an unknown field, a null other than
`duration`, unsupported resolution, a boolean in a numeric field, or a
non-finite input is rejected before output writes.

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

Frames use monotonic elapsed time and whole-percent/100 K quantization. Write
starts are at least 0.1 seconds apart, measured from start rather than
completion: at most ten verified updates per second, including effect
restoration and replacement. Each write still waits for full readback, so a
slow link lowers the rate. The runner sleeps until the program's next possible
change: an event edge, or 0.1 seconds for continuously varying output. Constant
output is rechecked at least once per second without writing. Identical
confirmed quantized targets cause no ordinary frame write. Only changed fields
are sent through `LightClient.set` for those frames.
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
continues to report actual verified snapshots. Its ordinary two-second
hardware poll is skipped while the published state comes from a verified
readback under two seconds old, as during an active effect. `GET /v1/state`
still performs a fresh hardware read. Status fields are exactly
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

Offline tests cover the HTTP/SDK contract, every pattern's formulas and
validation, seeded determinism, endpoints and looping, write-start spacing,
skipped targets, timing/quantization, restoration, readback/transport failures,
manual overrides, replacement, SSE poll skipping, and shutdown ownership,
using ephemeral loopback mock bridges. Shared Python/TypeScript vectors live in
`tests/fixtures/effect-vectors.json`. Optical timing, BLE reliability and
deployment remain separate hardware validation tasks.

## Program reference

`src/lighttime/programs.py` is the pure Python reference. The SDK
implementation must produce identical quantized output. `t` is elapsed
seconds; `low`/`high` are min/max brightness and `Δ = high − low`.

- **Quantization.** `QB(x) = clamp(floor(x + 0.5), 0, 100)` and
  `QK(k) = clamp(100·floor(k/100 + 0.5), 2700, 6500)`.
- **Curves and eases.** `linear(u) = u`, `perceptual(u) = u^2.2`,
  `smooth(u) = u²(3 − 2u)`, `cosine(u) = (1 − cos πu)/2`. `curve` affects
  breathe, the fades and sunrise only; other patterns accept and ignore it.
- **Randomness.** All arithmetic is modulo 2³². Use `Math.imul` and `>>> 0` in
  TypeScript.
  - `h32(x)` is: `x ^= x>>>16; x *= 0x7feb352d; x ^= x>>>15; x *= 0x846ca68b; x ^= x>>>16`.
  - `rnd(seed, stream, n) = h32(seed ^ h32(stream·0x9e3779b9 + n)) / 2³²`.
  - `expGap(m, u, lo, hi) = clamp(−m·ln(1 − u), lo, hi)`.
  - Value noise:
    `V(t; h, s) = r_n + (r_(n+1) − r_n)·cosine(x − n)`, where `x = t/h`,
    `n = floor(x)` and `r_n = rnd(seed, s, n)`.
- **Event timelines.** Stochastic events are renewals: `t_i = t_(i−1) + gap(i)`
  from `t_(−1) = 0`, or with `t_0 = 0` for television cuts. Times accumulate
  in that order, so a sample depends only on seed and `t`. Implementations may
  keep a forward cursor and must recompute from event 0 when seeking backwards.
- **Blink.** On while `(t/period) mod 1 < duty`. Both `duty·period` and
  `(1 − duty)·period` must be at least 0.1 s, with a 1e-9 tolerance.
- **Pulse.**
  - Set `T = 60/bpm`, `k = floor(t/T)` and `x = t − kT`. Amplitude `a` is 1
    when `accentEvery` is 0 or `k mod accentEvery = 0`, otherwise `accentRatio`.
  - `beat`: `a·e^(−x/(decay·T))`.
  - `heartbeat`: set `d = clamp(0.33T, 0.2, 0.35)`; output is
    `max(a·e^(−x/0.12), 0.65a·e^(−(x−d)/0.12))`, where the second term
    applies only when `x ≥ d`. Heartbeat requires bpm ≤ 120 and ignores `decay`.
  - Brightness is `QB(low + Δ·envelope)` at constant `cct`.
- **Flicker.**
  - Defaults: `cct` 2700, `cctEnd` 3200. Style defaults for depth/gusts/coupling
    are 0.25/0.15/0 for candle and 0.55/0.5/0.5 for fire.
  - `h1 = max(0.2, base/speed)`, with base 0.45 (candle) or 0.30 (fire).
  - `n = 0.6·V(t; h1, 1) + 0.4·V(t; 0.2, 2)`.
  - Gust starts use `expGap(8 or 3, rnd(seed,3,i), 0.5, 30)`. Gust `i` lasts
    `0.3 + 0.5·rnd(seed,4,i)` with strength `sin²(π(t − start)/length)`; the
    latest and previous gusts are both considered.
  - `dip = clamp(depth·n + 0.35·gusts·g, 0, 1)` and `B = QB(high − Δ·dip)`.
  - `K = QK(cct + (cctEnd − cct)·cctCoupling·(1 − V(t; 1.5, 5)))`.
- **Sunrise.** Defaults: `cct` 2700, `cctEnd` 6500. Set `u = clamp(t/D, 0, 1)`,
  with `w = u` for rise or `1 − u` for set. Then
  `B = QB(low + Δ·curve(w))` and `K = QK(cct + (cctEnd − cct)·smooth(w))`.
- **Lightning.**
  - Default `cct` 6500, constant for the whole run; ambient is `low`.
  - Strike gaps are `expGap(interval, rnd(seed,1,i), 1.5, 4·interval)`. A strike
    has `1 + floor(flashesMax·rnd(seed,2,i))` flashes; with `j` the flash index,
    `key = 16i + j`.
  - Each flash is on from `begin` to `end = begin + 0.1` at
    `low + Δ·(0.6 + 0.4·rnd(seed,3,key))`.
  - Between flashes, the next `begin = end + (0.1 + 0.15·rnd(seed,4,key))`.
  - A 0.1 s afterglow at `low + 0.25Δ` follows the last flash directly.
- **Emergency.**
  - Defaults: sides `cct` 2700 and `cctEnd` 6500. `flashOn` and `flashOff` are
    0.1–10 s; `gap` is 0.2–30 s.
  - Set `cycle = flashOn + flashOff` and `half = flashes·cycle + gap`.
  - Flash `j` of each half is on from `j·cycle` for `flashOn` at `high`.
  - The CCT switches to the other side at
    `(flashes − 1)·cycle + flashOn + 0.1` within each half, evaluated left to
    right, while dark. Sides alternate every half.
- **Paparazzi.**
  - Default `cct` 5600; rest is `low`.
  - Flash gaps are `expGap(interval, rnd(seed,1,i), 0.2, 6·interval)`.
  - A flash lasts 0.1 s at `low + Δ·(0.8 + 0.2·rnd(seed,2,i))`.
  - When `rnd(seed,3,i) < burst`, a second flash at the same level starts 0.2 s
    later. Overlapping flashes use the higher level.
- **Television.**
  - Defaults: `cct` 5000, `cctEnd` 6500. Cut gaps are
    `expGap(interval, rnd(seed,1,i), 0.4, 4·interval)` with scene 0 at 0.
  - Scene `i` has level `L = low + Δ·(0.25 + 0.75·rnd(seed,2,i))` and
    `K = QK(cct + (cctEnd − cct)·rnd(seed,3,i))`.
  - Brightness is `QB(clamp(L·(1 + 0.15·motion·(2V(t; 0.25, 4) − 1)), low, high))`.
- **Morse.**
  - Text is 1–64 ASCII characters from `A–Z a–z 0–9 space . , ? / = + - @`,
    with at least one symbol. Runs of spaces form one word gap.
  - In whole units: dot 1, dash 3, symbol gap 1, letter gap 3, word gap 7.
    Loops add a 7-unit gap.
  - Edges are `units·unit` seconds; on is `high`, off is `low`.
- **Keyframes.**
  - Omitted `ease` is `linear`; the first frame's `ease` is unused.
  - Omitted first-frame values come from the verified baseline, and later omitted
    values carry forward. Common brightness/CCT fields are accepted but don't
    change keyframe values.
  - The segment arriving at frame `k` uses that frame's ease on its progress
    `p`. `hold` keeps the previous value until the frame time.
  - A loop's length is the last frame time. Running samples wrap there to the
    first frame's values.
- **Endpoints.** At `t ≥ duration` the target is the exact endpoint. For looping
  Morse/keyframes ending exactly on a loop boundary, the endpoint is the end of
  that loop (last frame or trailing gap), not the start of the next. The
  original five patterns keep their v1 phase behaviour (a blink ending on a
  whole period is on).
