# Creative G60 effects: limits, extended contract and scheduling

Research date: 2026-10-23. **Preliminary design for parent agreement; nothing here is
implemented.** Inputs: `src/lighttime/{effects,client,transport,bridge,protocol}.py`,
`docs/effects-contract.md`, `docs/latency.md`, the parent's direct-BLE sweep
(`artifacts/hardware/blink-limits/report.json`, read only), and offline text search of the
saved vendor analysis under `artifacts/apps/`. This researcher did not access hardware,
send device commands, or edit SDK, source, service or UI files. Nothing from the vendor
files was executed. The ignored helper is `artifacts/effects-design/tools/disasm_range.py`.

## 1. Recommended decisions

1. **Build effects only from the verified brightness (`0x1001`) and CCT (`0x1002`)
   setters.** The official app disables every FX feature for PL103, and the native FX
   commands don't appear in any bundled model config (§3). No vendor-supported
   autonomous fast-blink path exists to adopt or probe.
2. **The public engine stays verified.** Changes:
   - Space write *starts* at least 100 ms apart. That allows at most 10 updates/s,
     scheduled from frame start rather than 200 ms after completion.
   - Each write gets full readback before the next one starts.
   - Missed frames are never replayed. Collapsed events and overruns are counted.
   - Shortest blink period is 0.2 s (5 Hz), labelled best-effort.
3. **Keep ACK-only output internal.** Run it as an experiment at no more than 20 writes/s
   with periodic readback checkpoints. Leave it out of public types and docs until
   optical proof exists (§7).
4. **Add 9 patterns to the existing 5,** in two waves. Wave A: `keyframes`, `pulse`,
   `flicker`, `sunrise`, `lightning`, `emergency`. Wave B: `paparazzi`, `tv`, `morse`.
   Use one pure program layer with seeded deterministic randomness, implemented in
   Python and TypeScript with shared golden fixtures (§4–§5).
5. **Gate rapid flashing.** A compiled timeline with more than 3 flashes in any 1 s window
   requires `allowRapidFlash: true` (§8).
6. **Bridge contention:** skip the 2 s SSE poll while a verified snapshot is under 2 s old.
   Optionally, serve `GET /v1/state` during an effect from the last frame readback (§6.4).

## 2. Measured limits and the transaction model

Parent's sweep (blink 8% ↔ 0%, baseline and final verified at 7% / 6100 K). Each
ACK-only case lasted 3 s.

| Requested half-period | Submission spacing p50 / p95 / max (ms) | ACK op p50 / p95 / max (ms) |
| ---: | --- | --- |
| 500 | 501.8 / 506.3 / 506.3 | 28.5 / 35.4 / 35.4 |
| 250 | 251.1 / 252.8 / 252.8 | 28.6 / 35.6 / 35.6 |
| 125 | 125.9 / 127.8 / 128.0 | 27.8 / 36.7 / 38.5 |
| 100 | 101.1 / 105.1 / 105.6 | 27.6 / 45.0 / 46.3 |
| 75 | 76.1 / 80.2 / 81.7 | 26.5 / 35.8 / 36.9 |
| 50 | 51.0 / 53.0 / 54.5 | 25.9 / 32.9 / 40.9 |
| 25 (saturated) | 30.0 / 31.1 / 45.0 | 30.0 / 31.0 / 44.9 |
| verified (setter + readback) | 90.0 / 91.6 / 173.9 | 90.0 / 91.5 / 173.9 (min 88.7) |

In the verified case, 32 of 33 transitions took 88.7–91.6 ms. Only the first took
173.9 ms, so report first transitions separately. Bridge loopback PATCH figures (from
`docs/latency.md`) are p50 84.5 ms for one field, 114.3 ms for both fields, and 59.5 ms
for a fresh GET.

**Transaction model (inference, consistent with every figure but not proven):**

- An acknowledged write, or a query plus its notification, takes about two connection
  events. At an inferred ~15 ms connection interval, that is about **30 ms per
  transaction**.
- The 17.9 ms minimum ACK rules out an interval ≥ 30 ms with a next-event response.
- Back-to-back saturation lands at exactly 2 × 15 ms.
- The model predicts the measured costs:
  - ACK-only setter: 1 transaction ≈ 30 ms, so ~33 writes/s and 16.7 blink cycles/s.
    This is command level only.
  - Verified one-field set (setter + two queries): 3 transactions ≈ 90 ms.
  - Verified two-field set: 4 transactions ≈ 114–120 ms. **This does not fit a 100 ms
    frame.** Patterns should rarely change both fields in one frame (§6.3).
  - Fresh GET: 2 transactions ≈ 60 ms. An SSE poll landing between frames therefore
    causes an overrun.
  - Changed-field-only verification: 2 transactions ≈ 60 ms. **Predicted, not
    measured.** Keep it a later optimisation (latency.md proposal 3).

The parent can confirm the interval passively with a `btmon` capture (LE Connection
Complete or Update), without sending anything to the light. Connection-parameter tuning
and write-without-response are separate experiments and are out of scope here.

**Optical truth is not established.** The camera shows segment transitions ramping over
several 30 fps frames. That could be a physical driver ramp or the camera ISP's temporal
response. A 30 ms ACK is not proof of an optical pulse.

## 3. Vendor native effects (offline static evidence)

| Finding | Evidence |
| --- | --- |
| Native FX API exists | Java `ZYLightClient.setEffectsByConfig(String)`, `setEffectsByFile(String)`, `setEffectsControlMode(PLAY=0, PAUSE=1, STOP=2)` (`ZYEffectInfo.ControlMode`). |
| FX control command `0x0A01`, u8 mode payload | `zy::EffectSender::PackEffectsModeSet(u8)` @ `0x167b94` → `PackUint8PayloadRequest(0xa01, mode)`. `ZYLightClient::SetEffects(int)` @ `0x1789a0` queues it with `GetTimeout(0xa01)`. |
| FX definition command `0x0A02`, string payload | `PackEffectsModify(string)` @ `0x167b88` → `PackStringPayloadRequest(0xa02, json)`. `ZYLightClient::AddEffects` @ `0x178580` sends JSON of ≤ 270 bytes (`cmp x8, #0x10e`) inline through `ModifyEffects` (`0xa02`). Larger definitions go through `EffectSender::AddEffects` @ `0x167d60` → `ZYFileTransfer(type 3)` + `SendBuffer`. |
| Firmware-side playback model | `assets/autoFx/effect/*.json`: units with `light{mode cct/hsi, int, cct}`, `motion{activeTime, sleepTime, strobe, fadeIn/fadeOut curve}`, `loop`. UI strobe ranges are 0.1–15 (party 0.1–36), so the light plays these autonomously. |
| **Not offered for G60** | Every bundled autoFx effect declares `"models": ["PL113"]` (`LightModel.PL113` = CINEPEER C100). The `DeviceConfig` getters `getEnableAutoFx`, `ManualFx`, `FxPair`, `FxProgram`, `SoundFx` and `DynDim` require `content.effect == 0`, unless the untraced app override map `C1050i` says otherwise. PL103's bundled `light_static_configuration.json` has **no `effect` key**. No bundled `.config` lists `0x0A01`/`0x0A02` for any model, PL103 1.6.3/1.6.4 included. |
| Identify / "search" | `PackIdentifySet(u16)` @ `0x169de8` → command **`0x1101`**, u16 payload. PL103 configs list `0x1101` in `opt`, and `getEnableSearch()` is `flicker != null` (PL103: `flicker: [0]`). The app probably offers a firmware locate-blink for G60. That link is based on the method name and was not traced. The pattern, duration and restore behaviour are unknown and untested. |
| No ramp or "fast" flag in the setters | `PackLightSet(u16, float)` @ `0x1686e0` builds `0x1001` with `id + op 1 + f32`. `PackLightSet(u16, float, int)` @ `0x1688a0` is a different command, **`0x100B`**: `id + op 1 + f32 + int8 mode`, where mode is the `DeviceModeValue` (OTHER 0, CCT 1, RGB 2, HSI 3, FX 4). That byte selects a light mode, not a transition time, and `0x100B` is absent from PL103 configs. The CCT setter is `id + op + u16` only. No transition-time or fade parameter was found. |

The app uses 200–500 ms step intervals in `fx_combination_configuration.json` (Flash 500,
Chase 300, Alternate 200, Random/Cross 250). Those presets are also FX-gated. They only
suggest that the vendor treats ~200 ms as a practical app-driven step.

**Recommendation:** don't send `0x0A01`, `0x0A02`, `0x100B` or `0x1101` to the G60.
Firmware handling is unknown, and the app itself doesn't offer FX on this model. Runtime
cloud-delivered configs could differ; offline analysis can't rule that out. Treat a
future single, user-authorised identify test as its own decision. Full native-FX research
on a PL113-class light is a separate project.

## 4. Extended contract (v2)

Keep the v1 endpoints (`GET /v1/effects`, `POST /v1/effects/start` / `stop`) and keep
all existing fields and semantics. `EffectConfig` becomes a union keyed by `pattern`.
Unknown fields are rejected **per pattern**: for example, `text` on `blink` is HTTP 400.
Validation always finishes before any write. Times are in **seconds**, as in v1.

Shared fields (existing unless marked new):

| Field | Meaning / default |
| --- | --- |
| `duration` | 1–3600 s, default 10. `null` = until stopped, allowed for cyclic/stochastic patterns only. `sunrise`, fades and non-looping `keyframes`/`morse` require a finite value. |
| `minBrightness`, `maxBrightness` | Whole percent. Min defaults to 0. Max defaults to the verified nonzero baseline or 5. Min ≤ max. |
| `cct`, `cctEnd` | 2700–6500 K in 100 K steps. The default is a per-pattern constant where the pattern's identity depends on it (table in §5), otherwise the baseline CCT / 6500. |
| `restoreOnComplete` | Default true for cyclic patterns. Default false for fades, `sunrise`, and non-looping `keyframes`/`morse`. |
| `seed` (new) | uint32 for stochastic patterns. Default: random per start, echoed in status for reproduction. |
| `allowRapidFlash` (new) | Default false. Required when the compiled timeline has more than 3 flashes in any 1 s window (§8). |
| `curve` (new) | `linear` (v1 behaviour) or `perceptual`. Applies to breathe, fades and sunrise (§5.0). |

Proposed `EffectStatus` additions. They are additive, but the v1 "exactly these fields"
sentence and its tests must change.

```ts
seed: number | null;
config: ResolvedEffectConfig | null;  // echo with defaults applied
timing: null | {
  mode: 'verified';                    // 'ack' only in the experimental build
  minWriteSpacingMs: 100;
  writes: number;                      // completed verified writes
  overruns: number;                    // write took longer than its frame slot
  droppedEvents: number;               // due events collapsed into a later output
  anchorLateP95Ms: number | null;      // onset lateness, rolling window of 100
  writeP50Ms: number | null; writeP95Ms: number | null;
  lastVerifiedAt: string | null;       // readback time, never request time
};
```

The SDKs get `EffectPattern`/`EffectConfig` discriminated unions, `PRESETS` constants
(§5.10), and a pure `effectSample(config, baseline, tSeconds)` for browser preview. The
Python and TS samplers must agree on shared golden JSON fixtures.

## 5. Pattern formulas (brightness + white CCT only; no RGB)

### 5.0 Shared definitions

- `t` is monotonic elapsed seconds; `φ = (t / period) mod 1`; `B0 = minBrightness`;
  `B1 = maxBrightness`; `ΔB = B1 − B0`.
- Quantise identically in both languages. Python's `round()` uses banker's rounding;
  don't use it.
  - `QB(x) = clamp(floor(x + 0.5), 0, 100)`
  - `QK(k) = clamp(100·floor(k/100 + 0.5), 2700, 6500)`
- Curves:
  - `lin(u) = u`
  - `perceptual(u) = u^2.2`
  - `smooth(u) = u²(3 − 2u)`
  - `cosine(u) = (1 − cos πu)/2`
- **Deterministic RNG.** It is counter-based, keyed by event index, so overruns and frame
  timing never change the sequence. All arithmetic is mod 2³²; in TS use `Math.imul` and
  `>>> 0`.

  ```
  h32(x): x ^= x>>>16; x = x*0x7feb352d; x ^= x>>>15; x = x*0x846ca68b; x ^= x>>>16
  rnd(seed, stream, n) = h32(seed ^ h32(stream*0x9e3779b9 + n)) / 2^32  ∈ [0,1)
  expGap(μ, u, lo, hi) = clamp(−μ·ln(1 − u), lo, hi)
  ```

- **Value noise:** `V(t; h, s) = r_n + (r_{n+1} − r_n)·cosine(f)`, where `x = t/h`,
  `n = ⌊x⌋`, `f = x − n` and `r_n = rnd(seed, s, n)`. With 10 Hz frames, `h ≥ 0.2 s` is
  required for the noise to be representable.
- **Program kinds.** *Continuous* programs are sampled on the frame grid. *Event*
  programs are piecewise-constant keyframe timelines. Either kind may declare **anchors**
  (onsets) that the scheduler prefers to hit (§6). Every hold in an event program must be
  ≥ 0.1 s, or the request is rejected.

### 5.1 Existing patterns (semantics unchanged)

- `blink`: `period` ≥ 0.2 s (was 1). New `duty` (default 0.5), with on and off segments
  each ≥ 0.1 s. Compiles to the events [0 → B1, duty·period → B0]. A period under
  1/3 s needs `allowRapidFlash`.
- `breathe`, `fade-in`, `fade-out`, `cct-sweep` keep their v1 formulas and period ≥ 1 s.
  They gain the optional `curve`.

### 5.2 `pulse`: beat and heartbeat

- Fields: `bpm` 20–180 (default 60); `style` `beat` or `heartbeat`; `accentEvery` 0–16
  (0 = no accent); `accentRatio` 0.2–1 (0.6); `decay` 0.05–1 as a fraction of the beat
  (0.3).
- Formula: `T = 60/bpm`, `k = ⌊t/T⌋`, `x = t − kT`.
  - `a = 1` if `accentEvery = 0` or `k mod accentEvery = 0`; otherwise `a = accentRatio`.
  - `beat`: `env = a·e^(−x/(decay·T))`.
  - `heartbeat`: `d = clamp(0.33T, 0.2, 0.35)`, `τ = 0.12 s`,
    `env = max(a·e^(−x/τ), 0.65·a·e^(−(x−d)/τ)·[x ≥ d])`. Allowed for `bpm ≤ 120`
    (T ≥ 0.5 s).
  - `B = QB(B0 + ΔB·env)`. CCT is fixed (single-field writes).
- Anchors: every onset `kT`, plus `kT + d` for heartbeat.
- `leadMs` latency compensation and wall-clock `startAt` sync are deferred until optical
  latency has been measured.

### 5.3 `flicker`: candle and fire

| | `candle` default | `fire` default |
| --- | --- | --- |
| `depth` (0–0.6) | 0.25 | 0.55 |
| knot periods `h1`, `h2` | 0.45/`speed`, 0.2 s | 0.30/`speed`, 0.2 s |
| `gusts` (0–1), mean gust gap | 0.15, 8 s | 0.5, 3 s |
| `cctCoupling` (0–1) | 0 | 0.5 |
| `cct` / `cctEnd` defaults | 2700 / 3200 | 2700 / 3200 |

- Noise: `n(t) = 0.6·V(t; h1, 1) + 0.4·V(t; h2, 2)`.
- Gusts: start times come from `expGap(mean, rnd(seed,3,i), 0.5, 30)`. Each gust lasts
  `dg = 0.3 + 0.5·rnd(seed,4,i)`, with shape `g = sin²(π(t − tg)/dg)` and amplitude
  `0.35·gusts`.
- Brightness: `dip = clamp(depth·n + amplitude·g, 0, 1)`, `B = QB(B1 − ΔB·dip)`.
- Coupled CCT uses slow noise so it changes at most about once per second:
  `K = QK(cct + (cctEnd − cct)·cctCoupling·(1 − V(t; 1.5, 5)))`.
- `speed` ranges 0.25–2.
- Quantisation note: `ΔB·depth` should be ≥ 6–8 points, or the flicker shows only 2–3
  discrete levels.

### 5.4 `sunrise`: sunrise and sunset

- Fields: `direction` `rise` or `set`. Finite `duration` (default 900 s); the parent may
  want a 7200 s maximum here. `curve` defaults to `perceptual`. `cct` 2700 and
  `cctEnd` 6500 by default.
- Formula: `u = clamp(t/D, 0, 1)`, and `w = u` for `rise` or `1 − u` for `set`. Sunset is
  sunrise played backwards.
  - `B = QB(B0 + ΔB·curve(w))`
  - `K = QK(cct + (cctEnd − cct)·smooth(w))`
- The pattern holds its endpoint (`restoreOnComplete` false). Writes happen only when a
  quantised value changes: at most ~138 writes over the whole run.

### 5.5 `lightning`: storm strikes

- Fields: `interval` 2–60 s, mean gap between strikes (default 6); `flashesMax` 1–4
  (default 3; 4 needs `allowRapidFlash`). Ambient level is `B0`. `cct` is set once at
  start (default 6500), so all later writes are brightness-only.
- Strike timeline: strike `i` follows the previous one by
  `expGap(interval, rnd(seed,1,i), 1.5, 4·interval)`, with `m = 1 + ⌊flashesMax·rnd(seed,2,i)⌋`
  flashes.
- Flash `j`:
  - On for 0.1 s at `B0 + ΔB·(0.6 + 0.4·rnd(seed,3,16i+j))`.
  - Dark for `0.1 + 0.15·rnd(seed,4,16i+j)` s.
  - After the last flash: a 0.1 s afterglow at `B0 + 0.25ΔB`, then ambient.
- Anchors: flash onsets. With `flashesMax ≤ 3` and gaps ≥ 1.5 s, the 3-flashes-per-second
  rule holds by construction.

### 5.6 `emergency`: alternating warm/cool white

- Label it in docs and UI as "emergency-style alternating white". The light has no red or
  blue output.
- Fields:
  - `flashes` 1–4 (default 2), `flashOn` ≥ 0.1 s (default 0.15), `flashOff` ≥ 0.1 s
    (default 0.15), `gap` ≥ 0.2 s (default 0.3).
  - The two sides are `cct` (default 2700) and `cctEnd` (default 6500). Rest level is
    `B0` (default 0).
- Half-cycle length: `H = flashes·(flashOn + flashOff) + gap`. The CCT side alternates
  each half-cycle.
- **Dark-time pre-positioning:** the program emits the CCT change as its own event 0.1 s
  into the dark gap. No frame ever writes both fields at a flash edge.
- The defaults give 2.2 flashes/s.

### 5.7 `paparazzi` (Wave B)

- Fields: `interval` mean seconds between flashes, 0.33–5 (default 0.8); `burst`
  probability of a double flash, 0–1 (0.25); `cct` 5600. Rest level is `B0`.
- Flash gaps are `expGap(interval, rnd(seed,1,i), 0.2, 6·interval)`. Each flash is one
  0.1 s frame at `B0 + ΔB·(0.8 + 0.2·rnd(seed,2,i))`. A burst adds a second flash 0.2 s
  later.
- Without `allowRapidFlash`, a sliding-window limiter drops any flash that would exceed
  3 in a second.

### 5.8 `tv`: screen glow (Wave B)

- Fields: `interval` mean seconds between scene cuts, 0.5–10 (default 2.5); `motion`
  0–1 (default 0.3); `cct`/`cctEnd` range (default 5000/6500).
- Cut `i`: the gap is `expGap(interval, u, 0.4, 4·interval)`. The level is
  `Li = B0 + ΔB·(0.25 + 0.75·rnd(seed,2,i))` and the colour is
  `Ki = QK(cct + (cctEnd − cct)·rnd(seed,3,i))`.
- Between cuts: `B = QB(Li·(1 + 0.15·motion·(2·V(t; 0.25, 4) − 1)))`.
- CCT changes only at cuts, which are rare combined writes counted as overruns.

### 5.9 `morse` and `keyframes`

- **`morse`** (Wave B). Fields: `text` up to 64 characters from `[A-Z0-9 .,?/=+-@]`,
  case-insensitive; `unit` 0.1–2 s (default 0.2 ≈ 6 WPM); `loop`.
  - ITU timing: dot 1 unit, dash 3, intra-character gap 1, letter gap 3, word gap and
    loop gap 7. On is `B1`, off is `B0`.
  - `unit` < 0.167 exceeds 3 flashes/s and needs `allowRapidFlash`.
- **`keyframes`** (Wave A) is the engine core that presets and tests share. Fields:
  - `keyframes`: 2–256 entries `{at, brightness?, cct?, ease?}`, where `at` is seconds,
    starts at 0 and strictly increases, with gaps ≥ 0.1 s.
  - `ease` is `hold`, `linear`, `smooth` or `perceptual`. It applies to the segment
    arriving at that keyframe. An omitted field carries the previous value forward; the
    first keyframe defaults to the baseline.
  - `loop` (default true when `duration` is null). The loop length is the last `at`.

### 5.10 SDK presets (constants, no server state)

```json
{
  "candle":    {"pattern":"flicker","style":"candle","minBrightness":10,"maxBrightness":30,"duration":null},
  "fireplace": {"pattern":"flicker","style":"fire","minBrightness":8,"maxBrightness":45,"duration":null},
  "storm":     {"pattern":"lightning","interval":6,"minBrightness":0,"maxBrightness":70,"duration":null},
  "heartbeat": {"pattern":"pulse","style":"heartbeat","bpm":60,"minBrightness":2,"maxBrightness":40,"duration":null},
  "sunrise15": {"pattern":"sunrise","direction":"rise","duration":900,"minBrightness":0,"maxBrightness":60},
  "emergency": {"pattern":"emergency","minBrightness":0,"maxBrightness":50,"duration":null},
  "sos":       {"pattern":"morse","text":"SOS","unit":0.2,"maxBrightness":40,"duration":null}
}
```

## 6. Verified scheduler (preserves truth and cancellation ownership)

### 6.1 Loop

Ownership is unchanged: `control_lock` drains the runner, then device I/O runs under
`Bridge.lock` with the `_guard` re-checks.

```text
SPACING = 0.100                         # min seconds between write STARTS (≤10/s)
next_start = started
loop until cancelled:
    wake = max(next_start, min(program.next_change_after(t_last), next grid slot))
    sleep until wake                    # cancellable; no device I/O
    async with bridge.lock:
        check_output(run)               # existing ownership/cancel guard
        now = monotonic(); el = now - started
        target, due = program.sample(el, since=t_last)   # due = events since t_last
        dropped += max(0, due - 1)      # collapse: output only the newest state
        t_last = el
        fields = changed(target, confirmed_readback_cache)
        if fields:
            start = monotonic(); next_start = start + SPACING
            await client.set(**fields, _guard=...)        # verified readback
            record latency; if monotonic() > next_start: overruns += 1
            publish()
    finite run: at el >= duration write the exact endpoint (even if late),
                then restore if requested (absolute both fields, verified)
```

- **No catch-up.** Missed grid slots are skipped. Expired events are never replayed.
  Status reports `droppedEvents` and `overruns`, never "the pattern ran as configured".
- **Idle frames cost nothing.** An unchanged quantised target causes no write. Event
  programs sleep until the next keyframe rather than polling the grid.
- **Anchors.** An onset is sampled at `max(anchor, next_start)`. The lateness feeds
  `anchorLateP95Ms`.
- **Restoration and replacement** still respect `SPACING` from the previous write start.
  Restoration still writes both fields absolutely and verifies them.
- **Field order.** CCT before brightness when brightness rises; brightness before CCT
  when it falls, so a CCT change is never shown at flash level. This needs only an
  internal order flag. The public `LightClient.set` stays unchanged.

### 6.2 Labels

- Document timing as **best-effort**. Frame-start scheduling with ~90 ms verified writes
  uses ~90% of link time at 10 Hz. A 0.2 s blink period is the floor.
- "Reliable" means no drops in normal conditions: blink period ≥ 0.3 s and event holds
  ≥ 0.15 s.

### 6.3 Combined writes

Two-field frames cost ~115–120 ms and spill into the next slot. The programs above avoid
them:

- `emergency` uses dark-time pre-positioning.
- `lightning` and `pulse` hold CCT fixed.
- Coupled `flicker` CCT changes at most ~1/s.
- `sunrise` and `tv` change both fields rarely.

Don't split a combined frame into two half-frames. That would show an intermediate
colour.

### 6.4 Contention

**SSE poll.** Record `verified_at` (monotonic) in `LightClient._refresh`. `Bridge.poll`
skips the refresh and publish when `now − verified_at < 2 s`. The idle 2 s poll otherwise
costs a ~60 ms lock hold per poll.

**Optional (parent decision): GET during an effect.** While an effect is running and the
last frame readback is < 250 ms old, `GET /v1/state` returns that readback. Its
`updatedAt` is the readback time, so the reply stays truthful.

**Later, measure first: changed-field-only verification** (predicted ~60 ms). The
untouched field keeps its own age and is never re-stamped as fresh.

## 7. ACK-only experiment (internal until proven)

- Gate it behind a bridge flag such as `--experimental-ack-effects` plus
  `timing: "ack"` in the request. Leave it out of TS types and public docs.
  `status.timing.mode` reports `"ack"`.
- Rate: write starts ≥ 50 ms apart (≤ 20/s, so ≤ 10 Hz blink). Command-level spacing at
  50 ms was accurate (p95 53 ms). Don't run at the 30 ms saturation point, which leaves
  no room for checkpoints or manual preemption.
- Truth:
  - An ACK means only that GATT delivery succeeded. It doesn't confirm output.
  - The engine keeps a separate **commanded** cache for change detection.
    `LightClient.state` stays readback-only, and SSE never shows commanded values.
  - A checkpoint queries the most recently changed field every 1 s. A mismatch stops the
    effect with `lastError`.
  - Start, stop, restoration and finite endpoints are fully verified.
- **Promotion criteria:**
  1. ≥ 300 consecutive commanded edges produce optical edges, with no drops or
     coalescing.
  2. Optical modulation depth is ≥ 90% of the static level difference at the claimed
     rate.
  3. Optical-edge jitter p95 is reported (target ≤ 10 ms).
  4. A 10-minute soak completes without disconnects.
  5. A manual PATCH preempts within ≤ 150 ms.
  6. The final readback matches.

## 8. Photosensitivity guard

- WCAG 2.3.1 sets a general threshold of 3 flashes in any 1 s window, and a 60 W light
  covers a large visual field. At parse, compile event programs and count flashes. A
  flash is a rise then fall where the peak exceeds the trough by ≥ 20% of the peak and
  ≥ 2 points.
- More than 3 flashes per 1 s window means HTTP 400 unless `allowRapidFlash: true`.
  Stochastic generators enforce the limit with a sliding window.
- The UI shows a warning toggle for that flag.
- `flicker` depth is capped at 0.6 and `tv` uses smooth noise, so they stay outside the
  rule.

## 9. Measurement guidance for the parent's optical work

1. **Keep five clocks separate:**
   - configured (program time);
   - submitted (before write);
   - ACK (write response);
   - readback (query notification): register state, not light;
   - optical.

   Python's `time.monotonic()` / `loop.time()` and V4L2 `CLOCK_MONOTONIC` buffer
   timestamps share a clock domain. Check the buffer flags for start-of-exposure versus
   end-of-frame timestamps. UVC/MJPEG adds pipeline delay.

   Align on V4L2 or `ffmpeg -copyts` wall/monotonic timestamps, not the AVI frame-0
   origin. Bound the residual offset with a host-timed reference, such as a screen flash
   at a known time, or report it as ±1 frame.
2. **Calibrate statics first.** With fixed exposure, gain and WB, capture 0/1/2/4/8/16%
   to build a pixel → percent LUT. Check for PWM banding at short exposure.
3. **Step response at slow rate.** Use 1 s half-periods, ≥ 20 edges each way, from 0%
   and from 2% minimum (driver soft-start from off may differ). This separates a physical
   ramp from ISP smoothing.

   With exposure ≤ 1 ms on a diffuse target filling the frame, rolling-shutter rows sample
   at ~row-time resolution. Calibrate row time from 100 Hz mains-lamp banding. A
   multi-frame ramp that also appears *within* a frame's rows is physical. A step within
   one frame followed by smearing across frames points to the ISP. Also test with
   denoise/auto features off and a lower resolution.
4. **Aliasing.** At 30 fps, frequencies ≤ 14 Hz are not aliased, so 3–10 Hz blinks can be
   verified directly by counting edges and by spectrum. 16.7 Hz shows as 13.3 Hz. To
   resolve frequencies above 15 Hz, use one of:
   - rolling-shutter edge timing;
   - two coprime camera rates (30 and 24/25/20 fps), where the true `f` must satisfy both
     `|f − k·fs|` relations;
   - a photodiode (sound-card or MCU ADC at ≥ 1 kHz);
   - 240 fps phone slow-motion.
5. **Phase-dithered latency.** Randomise command phase relative to the frame clock over
   ≥ 50 slow edges. The distribution of (first changed frame − submit) converges on
   optical latency ± exposure. Report its offset relative to submit and to ACK.
6. **Per-edge accounting** at each rate: commanded, ACKed and optical edge counts,
   modulation depth and optical jitter. That table is what promotes or rejects ACK-only
   rates (§7).

## 10. Proposed work split (after contract agreement)

- **Backend (Python, one agent):**
  - `effects` package: pure `programs` (sampler, RNG, compile/validate, flash counter)
    and a `scheduler` (§6).
  - Per-pattern strict parsing; status `timing`/`seed`/`config`.
  - Poll skip (§6.4).
  - Tests: golden timelines, fake-clock scheduler (overrun and drop accounting, anchors,
    no replay), cancellation/manual-override/restore regressions, rapid-flash gate.
- **SDK/browser (TS, one agent):** discriminated `EffectConfig`, presets, the
  `effectSample` preview checked against the shared golden fixtures, and a UI picker with
  preview sparkline and rapid-flash warning.
- **Android:** no effect work required. Effects stay bridge-owned under the
  one-controller rule. A later direct-BLE port can reuse the TS programs with the same
  scheduler rules.
- **Hardware (parent):** §9 optical protocol. Gate any ACK-only promotion on §7.
- **Native vendor FX:** out of scope for G60 (§3).

## 11. Open questions

- What is the optical response? Is there a physical ramp, and how deep does modulation
  stay at 5 and 10 Hz? This determines whether 0.2 s blink periods are visibly useful.
- Is the connection interval 15 ms (passive `btmon`)? Does a ~174 ms first transition
  recur after idle (peripheral latency)?
- Should sunrise allow 7200 s? Should `leadMs`/`startAt` tempo sync ship after the
  latency measurement?
## Implementation decisions

This document records the exploration proposal. The later
[shared implementation contract](../effects-programs-contract.md) takes
precedence, including its unchanged seven-field status object and absence of
an additional flash-consent flag. Physical ramp and blink evidence is recorded
in [the experiment report](../experiments.md).
