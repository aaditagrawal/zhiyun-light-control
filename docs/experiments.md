# Physical timing, brightness ramps and blink limits

Measured on 2026-10-23 with one MOLUS G60 / PL103, firmware 1.6.4, a Linux
laptop's Bluetooth adapter and its integrated 640×480 camera. These are results
for this unit and setup. Protocol readback confirms settings; camera images
provide relative visual evidence rather than calibrated light output.

## Smooth rise and fall

The public HTTP effect API ran an eight-second fade from 0% to 12%, then an
eight-second fall from 12% to 0%, at a fixed 6100 K. Independent state events
and camera video recorded both runs. Verified completion took 8.210 seconds
for the rise and 8.254 seconds for the fall, including the final readback.
Both endpoints matched. The original 7% / 6100 K was restored and read back.

The camera used fixed white balance, manual 10 ms exposure and disabled
dynamic frame-rate control; original camera settings were restored afterward.
Static holds at 0%, 4%, 8% and 12% established a relative camera reference.
The image signal increased monotonically across those holds but was nonlinear.
The first nonzero 1% step is visible: the G60 only accepts whole percentages,
so software cannot provide continuously variable output below that resolution.

A separate 12%→4% step and 4%→12% step produced camera-observed 10–90%
transitions of approximately 460 ms and 492 ms. Their verified command
round trips were about 119 ms and 97 ms. The video cannot distinguish lamp
driver smoothing from the camera's temporal processing. Those optical values
must not be advertised as the lamp's intrinsic response time.

Ignored local evidence: `artifacts/hardware/ramps/report.json`, `ramps.avi`,
`analysis.json`, and the interactive `measurements.html`. Fifty-one state events
and 921 decoded camera frames were retained. Frame timing was aligned using
FFmpeg's first progress timestamp; host/pipe timing adds uncertainty.

## How fast can it blink?

A separate direct-Bluetooth sweep requested alternating 0% and 8% at half-cycle
intervals of 500, 250, 125, 100, 75, 50 and 25 ms. Each case lasted three
seconds. An additional case used fully verified updates without an added wait.
Every case ended with actual device readback; the starting 7% / 6100 K and
camera controls were restored, and the persistent bridge was restarted.

| Measurement | Observation | Meaning |
| --- | --- | --- |
| ACK-only, requested 25 ms between writes | Median 30.0 ms; p95 31.1 ms | About 33 command submissions/s, not verified optical transitions |
| Fully verified one-field update | 32 of 33 transitions took 88.7–91.6 ms; first took 174 ms | Readback adds two BLE queries |
| Camera capture | Approximately 30 decoded frames/s | Cannot establish high-frequency contrast or rule out aliasing |

The slow pulse train is visible. Faster trains have lower apparent modulation
and do not establish a clean, full-contrast optical blink frequency. The AVI
container's reported 600 fps is erroneous for this capture; decoded frame
timestamps, approximately 31.7 ms apart, were used instead.

The expanded verified effect scheduler requests a minimum 100 ms between write
starts. A 0.2-second blink period therefore requests 0.1 seconds on and 0.1
seconds off, or five complete cycles/s. This is a requested cadence, not a
guarantee of five full-brightness optical flashes/s. Transport contention,
readback and output smoothing can lengthen or attenuate a pulse. No ACK-only
mode is exposed as a verified public effect.

Ignored local evidence: `artifacts/hardware/blink-limits/report.json`, camera
video and `optical-analysis.json`. The approximate video alignment used there
is unsuitable for claiming absolute command-to-photon latency.

## Verified scheduler on a recovered connection

A later four-second run of the public effect API requested a 0.2-second blink
cycle. SSE captured 41 alternating 0%/12% readbacks, including the finite
endpoint, with a median interval of 104.9 ms and maximum 120.1 ms between
confirmed changes. At a 0.3-second cycle the median was 150.0 ms, maximum
164.8 ms. These are intervals between completed readbacks; update starts are
separately limited to 100 ms. Baseline restoration passed after each run.
Local evidence: `artifacts/hardware/blink-limits/verified-engine.json`.

Connection conditions matter. An earlier reconnect in this run produced
roughly 0.5–1.8 second verified setters and much slower state reads. All 14
patterns still completed, but their sampled output was sparse. Closing that
owned connection and reconnecting restored approximately 60 ms fresh state
reads. The cause was not established; the slow distribution is retained as
`artifacts/latency/reconnected-ble.json`, rather than discarded.

On one subsequent connection, eight two-field reads in each of three phases
(acknowledged writes, write-without-response queries, acknowledged writes)
had medians 60.9, 59.9 and 60.3 ms. This did not establish a useful improvement
from removing acknowledgements, so ordinary BLE control retains them.
The G60's readable preferred-connection-parameter characteristic advertised
10–15 ms, zero peripheral latency and a four-second supervision timeout.
Those are preferences, not a measurement of the negotiated connection interval.

The 14-pattern physical sequence retained 141 state events and camera video in
`artifacts/hardware/expanded-effects/`. Explicit stop restored both baseline
fields; a manual setting stopped the runner and remained unchanged afterward.
Every test restored the starting 7% / 6100 K. None establishes precise optical
strobe performance or physical Android compatibility.

After integration, all fourteen programs also ran with USB setters and BLE
readback, retaining 427 state events and another camera recording under
`artifacts/hardware/usb-effects/`. Stop/restore and manual override passed;
the original 7% / 6100 K was restored. This verifies the hybrid program path,
without establishing USB-only operation or precise optical timing.

See [the HTTP latency benchmark](latency.md) for a reproducible SDK round-trip
measurement and [USB findings](research/usb-deep.md) for the separate USB path.
