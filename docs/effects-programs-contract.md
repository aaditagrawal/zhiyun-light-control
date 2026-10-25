# Expanded shared effect contract

This extends [the existing v1 contract](effects-contract.md). It is the
implementation agreement for Python, TypeScript, the website and Android.
The waveform definitions and parameter ranges in sections 4–5 of
[the design proposal](research/creative-effects.md) are adopted, with the explicit
decisions below taking precedence over that research proposal.

## Patterns and compatibility

Keep `blink`, `breathe`, `fade-in`, `fade-out`, and `cct-sweep`. Add `pulse`,
`flicker`, `sunrise`, `lightning`, `emergency`, `paparazzi`, `tv`, `morse`,
and `keyframes`. The HTTP endpoints and seven existing EffectStatus fields
remain unchanged. All clients validate the expanded pattern union.

Existing configuration remains valid. Brightness and CCT limits and baseline
restoration remain unchanged. All output stays serialized and fully verified;
the experimental ACK-only sweep does not become a public control mode.

## Adopted configuration

Common fields keep their previous meanings. Add `seed`, an unsigned 32-bit
integer defaulting to 1, and `curve`, `linear` or `perceptual`. Default curve
is linear except sunrise, which defaults to perceptual. No additional consent
flag, authorization step or rapid-flash checkbox is introduced.

Pattern-specific fields follow the design proposal:

| Pattern | Additional fields |
| --- | --- |
| blink | `duty`, default 0.5; both phases at least 0.1 s; period 0.2–60 s |
| pulse | `bpm`, `style` (beat/heartbeat), `accentEvery`, `accentRatio`, `decay` |
| flicker | `style` (candle/fire), `depth`, `speed`, `gusts`, `cctCoupling` |
| sunrise | `direction` (rise/set), finite duration; default 900 s, maximum 3600 s |
| lightning | `interval`, `flashesMax` (1–4) |
| emergency | `flashes`, `flashOn`, `flashOff`, `gap`; alternating warm/cool white |
| paparazzi | `interval`, `burst` |
| tv | `interval`, `motion` |
| morse | `text`, `unit`, `loop`; text up to 64 supported characters |
| keyframes | `keyframes`, `loop`; 2–256 frames at increasing times in seconds |

Reject undocumented properties. Accept common legacy fields for compatibility;
reject pattern-specific fields supplied to another pattern. A keyframe is
`{at, brightness?, cct?, ease?}`; `ease` affects the segment arriving at that
frame and is hold/linear/smooth/perceptual. First at=0, each gap at least 0.1 s;
omitted output fields carry forward from baseline/previous frame.

`pulse` defaults to heartbeat, 60 BPM, no accents (`accentEvery=0`), ratio 0.6,
decay 0.3. The waveform's own beat period follows BPM. `flicker` defaults to
candle. The pattern-specific CCT defaults in the design apply to new patterns
only; the original five continue to use baseline CCT when omitted.

Morse and keyframes default `loop=true`. With `loop=false`, an omitted duration
resolves to the program's natural duration; an explicit shorter duration can
truncate it and a longer duration stops at natural completion. Null duration
requires looping. For an exact finite endpoint, do not wrap the sampler to
the next loop before applying completion. Cyclic defaults remain 10 seconds;
sunrise defaults to 900 seconds. Fades/sunrise and non-looping keyframes/Morse
hold their endpoint by default; other cycles restore on completion by default.

Python and TS quantize nonnegative values using floor(x+0.5), including CCT
in 100 K units. Use the design's exact h32/rnd counter-based noise, so seed and
elapsed time define the same output regardless of previous samples or skips.
Do not consume mutable randomness per render tick. Shared golden vectors must
compare both implementations, including endpoints and skipped sampling times.

## Timing and lifecycle

Minimum interval between write starts becomes 0.100 seconds, measured from
start rather than completion. Each write still includes full readback. Sample
the current target after lock/rate waits and skip missed targets; no catch-up
burst. Unchanged targets cause no write and no busy loop. Completion writes
the exact finite endpoint before optional absolute two-field restoration.
Existing cancellation/ownership guards apply between fields, retries and reads.

Skip the ordinary two-second SSE hardware poll when verified output is under
two seconds old. State reads remain actual hardware reads; do not add a silent
cached GET path. Do not add new required status fields or expose timing
guesses as measured values. Timing remains best-effort and transport dependent.

## Presets and UI

Export SDK preset constants for candle, fireplace, storm, heartbeat, sunrise15,
sunset, emergency, paparazzi, television, SOS, and a keyframe demonstration.
Use restrained preview levels (roughly 12–20%) and let users set full 0–100%.
Selecting a preset edits requested configuration; only Start changes output.
The page and native Android app expose preset choice, tuning and stop/restore.
The website additionally offers an advanced keyframe JSON editor. Direct
Android effects run locally over its BLE owner; remote clients use bridge
effects. Manual adjustment stops a running effect before applying its value.
An app background/disconnect cancels its local runner and drains outstanding
work. No Android or optical compatibility is claimed without corresponding
physical validation.
