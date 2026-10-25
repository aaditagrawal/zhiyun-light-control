# Android light patterns

The native Android app runs effects directly through its existing G60 Bluetooth
connection. There is no bridge, account or internet requirement. Keep the app
open: its runner belongs to the foreground app session.

## Choose, tune, then start

Connect and wait for verified brightness and CCT readback. The **Scenes**
screen offers the shared presets: Candle, Fireplace, Storm, Heartbeat, Sunrise,
Sunset, Emergency, Paparazzi, Television, SOS / Morse and Scene sequence. Blink,
Breathe, Fade in, Fade out and CCT sweep remain available too.

Scenes are grouped into Atmosphere, Rhythm, Transitions, Signals and Sequence.
Swipe the category tabs and scene cards to browse all sixteen choices. Each
card pairs a direct scene name with a waveform thumbnail; the selected scene's
stage shows native light artwork. The artwork sets a mood; the G60 produces
warm-to-cool white light.

Lighting effects never end at a hard edge. Glows use an eased six-stop radial
falloff, and drop shadows pair a tight contact layer with a wide ambient layer
(`falloff` and `softShadow` in `materials.tsx`). The tab and card rows bleed to
the screen edges and pad their content back in, leaving transparent gutters
sized to that shadow reach. Stage artwork fits its composition inside the
frame's shorter side with a safe inset, so rings, bolts, pills and glow tails
stay whole; the sky and any horizon remain full-bleed.

The selected scene's player sits above the browsing rows, with a central play
button, a concise **Preview**, waveform, brightness range, white temperature
and run time. It uses 64 samples from the same SDK program
that drives the light: height indicates brightness and color indicates white
temperature. Finite fades and sunrise show their whole transition; random
patterns show a short, bounded window. The chart labels its time window and
brightness scale. Preview colors are indicative, and any assumed starting
values remain visible. These are never presented as physical measurements.

The summary shows configured brightness and white-temperature bounds, or
**White at Start** / **White by frame** when those settings are carried. The
waveform's colours show the sampled preview window. A short random window need
not reach the full configured temperature range. Run-time labels use the same
seconds/minutes formatting throughout.

Selecting a pattern or opening **Tune** only changes the requested
form. Press the play button to send output; its accessible label names the
selected scene. Starting another pattern replaces
the previous run without restoring it first, and captures a fresh verified
baseline for the new run.

Category browsing, switching between Light and Scenes, and collapsing
**Tune** preserve the current draft and exact keyframe input text,
including incomplete entries. Selecting a different scene loads its defaults.
Tuning fields and action rows wrap on narrow screens; text scales and tap
targets are at least 48 dp. Native sliders remain the tuning inputs.

The initial preset peaks are deliberately restrained, at 12–20%. Brightness
can be tuned over the full 0–100% range, in whole percentages. CCT uses
2700–6500 K in 100 K steps. Both clients use the shared SDK parser for pattern
defaults: Candle starts at 2700 K with a 3200 K end setting; Sunrise starts at
2700 K and ends at 6500 K. These defaults are independent of the light's
current temperature. Legacy patterns, Heartbeat and Morse can instead use
the verified CCT captured at Start.

The native panel exposes:

- Minimum/maximum brightness and relevant start/end temperatures.
- Finite duration from 1–3600 seconds, or **Until stopped** for cyclic patterns.
- Period for Blink, Breathe and CCT sweep. Blink accepts 0.2–60 seconds and an
  adjustable on-time percentage; each phase must last at least 0.1 seconds. Breathe and
  CCT sweep accept 1–60 seconds.
- Heartbeat speed from 20–120 BPM, Morse text and dot length, and a repeatable
  unsigned 32-bit seed for stochastic patterns.
- **Restore when finished** for finite runs, initialized from shared defaults.
  Fades and Sunrise hold their endpoints by default; cycles restore by default.
- Linear/perceptual brightness curves for fades, breathe and sunrise.

Scene sequence starts with the SDK's five-frame, six-second loop and can be
edited directly on the phone. Add/remove frames, edit time, brightness and
temperature, and choose hold, linear, smooth or perceptual arrival easing.
Blank output fields carry the previous value; the first frame starts at zero.
Shared validation enforces 2–256 frames, timing and output resolution.

Invalid settings appear beside the form and disable Start. Validation uses
`parseEffectConfig`, including whole-percent/100 K resolution, brightness
ordering, finite-only patterns, text restrictions and blink phase lengths.
There is no extra consent or authentication flow.

## Requested settings and actual light

The preview and requested range are labeled separately. The readback above them
continues to show values read from the physical light, with their read time.
The runner publishes its own seven-field `EffectStatus`; the panel displays
that run's pattern, running/stopped state, supplied progress and any error.
It does not estimate progress with a separate UI timer or treat a selected
preset as active output.

Every effect write goes through `LightController` and full readback
verification. Bluetooth and query latency determine the achieved cadence;
missed targets are skipped rather than replayed in a burst. A displayed
percentage is runner progress, not a measurement of optical timing. An error
stops the runner, remains visible and asks for **Read light** before further
adjustment. No automatic restoration follows a runner failure.

## Stop and connection ownership

**Stop & restore** stops the run and restores both the absolute brightness and
CCT captured at Start, with verification. It is available only while a run is
active; stopping an idle runner does not restore an older baseline. Finite completion uses the chosen
restoration setting. Manual brightness, CCT and brightness-based power writes
cancel the runner without restoring it, then apply and verify the manual
request. Moving a slider edits its draft; releasing it commits the request.

Backgrounding, disconnecting, a lost Bluetooth connection and unmounting
synchronously invalidate the runner. Already submitted native work may still
need to finish. Cleanup drains runner-owned writes before calling
`LightController.disconnect`; unmount destroys `BleManager` only after that
cleanup succeeds. These paths leave the light's current settings in place.
Reconnect explicitly on returning to the app.

`EffectOwnership` enforces drain-before-disconnect ordering. `EffectRetirement`
retains failed or unsettled owners until cleanup succeeds. A later explicit
search, connection or disconnect retries the retained drain; replacement and
manager destruction remain blocked if it fails again. Connection generation
and runner identity guards discard callbacks from an older session, so a
late result cannot overwrite a subsequent connection's UI.

## Validation boundary

On 2026-10-25, the scene redesign passed all 63 Android Bun tests (1590
assertions), TypeScript checking and Android Hermes export. The export used a
fresh `TMPDIR` after the system temporary directory rejected writes. React
Native Web fixture checks reached all sixteen scenes at 320 CSS px, preserved
keyframe spelling across tuning disclosure and Light/Scenes navigation, and
checked 200% text sizes with no page overflow or panel tap targets below
48 px. These fixture measurements do not establish native phone font layout or
physical light behavior.

Offline Bun tests cover all sixteen requested forms, shared temperature
defaults, immutable preset copying, blink boundaries, invalid inputs, synchronous
cancellation, drain ordering and failed-cleanup recovery. Existing permission,
captured-frame native adapter, readback and operation-gate tests remain in place.
TypeScript and Android bundle export are separate integration checks.
The 2026-10-24 Android UI suite passed 40 tests / 1394 assertions, including actual
hook-body cancellation cases that release busy state after background or
explicit disconnect, slider draft/commit/readback behavior, all scene previews
and keyframe editing helpers. TypeScript and Hermes export also passed.

The app's ownership tests use a controlled drain promise to verify cleanup
ordering and recovery. The SDK separately tracks actual controller and native
transport work, including work that outlives its caller's cancellation or
deadline. Final software validation includes those SDK ownership tests and the
direct runner's tests, followed by TypeScript checking and Android bundle
export. Passing the app-side tests alone does not establish that native work
has settled.

On 2026-10-23, the direct runner's 20 focused tests passed (171 assertions),
and the full SDK suite passed 123 tests (3151 assertions), including the
controller/transport native-drain integration tests. SDK TypeScript checking
also passed. These checks establish software behavior with controlled clocks,
fake controllers and fake native transports; Android packaging and physical
phone validation remain separate.

Five runner regressions use the real `LightController` and
`BleProtocolTransport` over a fake native adapter. They hold an admitted GATT
setter to verify live-connection manual/Stop/replacement handoffs, spacing and
readback, plus cancellation/disposal of I/O owned by a superseded command.

These tests do not validate Android runtime permissions, app lifecycle on a
physical phone, BLE reliability or the emitted optical effect. See the
[build record](android-build.md) for native APK and emulator evidence. A
successful source check, UI fixture or bundle export is not physical Android
validation.

See [Android setup](android.md), [shared effect behavior](effects.md) and
[the expanded configuration contract](effects-programs-contract.md).

## Direct SDK runner

`DirectEffectRunner` adds local effects to an already connected
`LightController`; it does not connect or disconnect that controller itself.

```ts
const runner = new DirectEffectRunner(controller);
const unsubscribe = runner.subscribe(status => renderEffectStatus(status));
await runner.start(EFFECT_PRESETS.candle!);
await runner.setState({ brightness: 9 }); // cancels without restoring

// Before replacing the session or destroying its native manager:
await runner.dispose();
await controller.disconnect();
unsubscribe();
```

`start(config)` returns an accepted seven-field status after a fresh verified
`controller.refresh()`. It snapshots caller input and invalidates the prior
producer, then drains its admitted transaction without restoration.
`stop({ restore: true })` drains an active run and
verifies an absolute restoration of both baseline fields; idle stops are
idempotent. Manual `setState(patch)` validates before cancellation, drains the
old work, then applies its own verified request. Invalid input leaves an
active run unchanged.

Ordinary Start, Stop and manual handoffs cancel scheduled output immediately
while allowing an already admitted brightness/CCT/readback transaction to
finish. The new action waits for it before writing or capturing its baseline.
Scheduling cancellation and controller-I/O cancellation use separate signals:
aborting an in-flight GATT chunk would conservatively invalidate the BLE
session, so ordinary handoffs preserve that transaction and the connection.

`cancel()` invalidates synchronously and aborts all owned controller calls,
including a superseded call whose replacement is still queued. It cannot
withdraw an already submitted native write. `dispose()`
closes admission, cancels, waits for runner jobs and `controller.drain()`, and
never starts a restoration or calls `controller.disconnect()` itself. The
transport may close an invalidated BLE session after its owned native work
settles. If a drain times out, retain that
runner/controller and retry disposal after work settles. Do not continue to
native manager destruction or a replacement connection on a failed drain.
New start/manual/stop operations are rejected after disposal has begun, even
when cleanup still needs a retry.

Effect steps and control actions share a serial queue. Writes start at least
100 ms apart, targets are sampled after queue/drain and rate waits, and
unchanged quantized targets are skipped. Finite runs verify their exact
endpoint before optional baseline restoration. Transport or verification
failure stops output without an automatic restore and persists in
`lastError` until a new start is accepted.
