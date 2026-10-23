# Validation record

Date: 2026-10-23. Host: validation laptop, Fedora Linux 45, Intel AX201 Bluetooth,
integrated Syntek camera. Python pin: 3.14.8. The actual lamp identifies as
ZHIYUN MOLUS G60 / PL103, protocol device ID 0.

## Actual hardware

`scripts/hardware_verify.py` used the public `LightClient` SDK, GATT writes with
response, and separate brightness/CCT queries. For each requested setting it
captured a fresh 640×480 JPEG from `/dev/video0` using FFmpeg. The original
captures and machine-readable report are in local `artifacts/hardware/`.
Camera images, private device logs and build artifacts are not committed to
this repository; the protocol captures used by tests are retained in source.

| Step | Requested setting | Actual device readback | Camera observation |
| --- | --- | --- | --- |
| Dim | 2%, 5000 K | 2.0%, 5000 K | Desk and bag dimly illuminated |
| Bright | 12% | 12.0%, 5000 K | Clearly increased illumination |
| Off | Power false | 0.0%, 5000 K | Scene almost entirely dark |
| Warm | 5%, 2700 K | 5.0%, 2700 K | Strong warm/orange illumination |
| Cool | 6500 K | 5.0%, 6500 K | Clearly cool/blue illumination |
| Restore | Original setting | 5.0%, 5000 K | Restoration confirmed by readback |

The same full image region was compared after JPEG decoding to RGB. Mean luma
(0–255) was 36.44 at 2%, 79.80 at 12%, and 0.70 at off. Mean red/blue ratio
was 2.93 in the warm image and 0.56 in the cool image. These support the visual
change; autoexposure/white balance and scene reflectance prevent treating them
as calibrated light-output or Kelvin measurements.

### Protocol/USB

The official Android AAB and Mac updater were downloaded and inspected offline.
Their native/Java code confirmed the FEE9 endpoint, frame structure and
CRC-16/XMODEM, float32 brightness and uint16 Kelvin command payloads. Captured
device replies match those facts and are embedded in `tests/test_core.py` and
[the API contract](api-contract.md). No firmware was flashed or Bluetooth
provisioning reset. The target vendor apps were analyzed as data, not executed
on Linux.

Linux already loaded `cdc_acm` for USB `fff8:0180`. `lighttime usb-info` returns
the real model/serial and device ID at 115200 baud. Brightness/CCT reads over
USB received no reply. Later isolated writes established the verified USB/BLE
hybrid described below; USB-only readback remains unsupported.

### Shared bridge

The actual BLE G60 was connected by `lighttime serve`. The public Python
`BridgeClient` read 5% / 5000 K and set the same values through authenticated
HTTP; hardware readback confirmed them. The local access token is outside the
project in an owner-only config file and is never included in logs or examples.

The built public TypeScript SDK passed its opt-in live bridge check: authenticated
refresh, PATCH to the 5% / 5000 K baseline, SSE event, and final readback. The
OBS worker script was imported with an `obspython` stub and then used against
the **real bridge/G60**, successfully reading and setting 5% / 5000 K. This
validates its HTTP worker path, while the actual OBS UI remains untested.

A second OBS worker test changed the real light to **9% / 4100 K**, captured
`artifacts/hardware/obs-9percent-4100K.jpg`, then restored the actual starting
state of 5% / 5000 K through the same worker. Readbacks are saved in
`artifacts/hardware/obs-bridge.json`.

The actual browser controller was opened in Chromium and authenticated. Its
sliders applied **8% / 3200 K** through the TypeScript SDK and bridge, with
verified hardware readback. A new camera photo is saved as
`artifacts/hardware/browser-8percent-3200K.jpg`. The browser's off button read
back 0%, restore returned 8%, and the sliders restored the original 5% / 5000 K.
Live SSE state updates were observed in the browser. Browser integration found
and fixed a native `fetch` receiver-binding bug that did not appear in Bun.

After the browser lifecycle/order fixes, the newly rebuilt bundle repeated the
same live 8% / 3200 K, off, 8% restore and 5% / 5000 K restoration sequence.
Every step displayed actual readback. An independent browser harness passed
15 cases for late HTTP/SSE responses, page restoration, cancellation and late
Bluetooth setup/cleanup; it uses synthetic page lifecycle events, so actual
Chromium BFcache admission is not claimed.

Final explicit bridge disconnect/reconnect cycles retained 7% / 4400 K and
6% / 5200 K, respectively, taking **29.43 and 10.33 seconds**. The starting
5% / 5000 K was restored. The report is
`artifacts/hardware/reconnect-report.json`.

Earlier runs included both successful cycles and exhausted connection attempts;
restoration connections succeeded. The diagnostic trace captured BlueZ
`le-connection-abort-by-local` retries. The Linux scanner now requests duplicate
advertisements, and Bleak's per-attempt timeout matches the surrounding
15-second deadline. Up to three attempts share a 40-second connection budget.
These are bounded recovery measures, not proof that radio interference or
another controller cannot interrupt a future session. Use the persistent bridge
to share an established connection. Diagnostic reports remain local under
`artifacts/hardware/`; nearby-device debug logs and camera photos are excluded
from distributable source.

With an authenticated SSE stream open, an actual systemd service restart then
finished in **2.54 seconds**, the old stream reached EOF, and Bluetooth/identity
readback became ready after **33.99 seconds** total. The new process read the
same 5% / 5000 K. `artifacts/hardware/service-restart.json` and the service
journal record a clean stop/start. Offline active-request shutdown regressions
also cover cancellation during connect, identification, reads, writes and
discovery, including explicit unresolved ownership for a hung backend.

The running bridge also answered its public health endpoint from a second
Linux host over the verified LAN route, independently of the laptop's loopback
requests. This verifies network reachability, not remote browser Bluetooth.

Additional real-device tests found brightness 5.5 -> 5.0 and 6.7 -> 6.0;
CCT 4501, 4550 and 4599 each read back 4500 K. Public G60 setters now reject
non-whole-percent and non-100 K inputs **before writing**; wire formats remain
float32 percent / uint16 Kelvin. The original settings were restored after
these resolution experiments.

## Automated checks

Python tests cover captured packets, fragmented/coalesced/corrupt frames, wrong
sequence/command filtering, timeout cleanup, reconnection, failed setter
verification, power restore, strict request validation, auth, CORS and SSE.
OBS tests use an `obspython` stub and actual local test HTTP servers; they do
not claim an OBS GUI test. Test discovery is restricted to this project's
`tests/`, excluding downloaded upstream research trees.

Recorded baseline before the shared-effects addition: **115 Python tests
passed, 34 subtests passed**, including 28 OBS tests and 13 bridge shutdown
tests. Ruff lint/format and full `ty check`
passed. Twenty real-Bleak/fake-bus cases cover one-shot scanner startup/stop
ownership; they do not initialize system D-Bus or touch hardware.

At that baseline, the TypeScript SDK passed 30 tests / 496 assertions, its ESM
build, declaration typecheck and public package import smoke. The Expo Android
adapter/app passed 13 tests / 142 assertions and TypeScript checks. The native
APK build and its toolchain evidence are recorded separately in
[android-build.md](android-build.md).
The arm64 release preview APK was compiled, test-signed and inspected for
embedded Hermes, BLE module retention, permissions and 16 KiB alignment.
No Android device was attached when `adb devices -l` was checked. Physical
Android installation, startup, permission flow and BLE testing remain deferred
until the user connects a phone.

Python's frozen dependency audit reports no known vulnerabilities in 34
packages; the SDK's Bun audit reports none in five packages. The Android
dependency tree has one unresolved high-severity transitive Expo CLI
`node-forge@1.4.0` advisory, documented in [android.md](android.md). No dependency
override or disabled integrity check hides it.

Mocks are explicit and identify themselves as `MOCK-NOT-HARDWARE`; they are
never a fallback for failed lamp communication.

### Shared effects and CI scope

All five bridge-generated patterns were subsequently exercised through the
public Python `BridgeClient` against the actual BLE G60. Blink and breathe
varied between 1% and 8%; fade-in held its 8% endpoint and fade-out held 1%.
A CCT sweep at 8% produced sampled readbacks from 2700 K to 6400 K and back.
The configured 6500 K turning point was not sampled exactly; BLE scheduling
and quantization make this a host-timed effect, not a precise strobe clock.
Cyclic completion restored the freshly captured 5% / 5000 K baseline.
Explicit stop restored 6% / 4200 K, and a manual 3% / 4700 K adjustment stopped
an unbounded breathe without a later frame overriding it. The session finally
restored its original 5% / 5000 K output.

The laptop camera recorded a 35-second MJPEG video during the repeated effect
sequence, and extracted JPEGs were inspected. The video shows the changing
illumination of the same desk/bag scene; it is relative visual evidence, with
the same autoexposure/white-balance limitations as the earlier captures.
`artifacts/hardware/effects/report.json` contains 82 sampled readbacks across
the five patterns plus stop/manual-override checks. The video, JPEGs and report
remain local and excluded from source distributions.

The personal controller was opened through private Tailscale Serve HTTPS.
Health discovery automatically attached it with the entire connection/token
section hidden. Its actual form started a four-second fade-in from 2% to 7%
at 4400 K; SSE displayed the intermediate readbacks and the restored 5% /
5000 K baseline. No Authorization header was required. A second Linux host
also read actual state through the HTTPS route with its exact allowed origin.
The bridge stays on loopback and private Tailscale Serve forwards the UI/API.
See [the effect contract](effects-contract.md) for API and timing semantics.

The effects/personal-mode SDK has **37 passing tests / 702 assertions** with
typecheck, ESM/declaration build, RN/public-import smoke and zero SDK audit
findings. **35 real-browser regression cases** passed using fake fetch/GATT,
including the original 15 lifecycle/order cases. The Android adapter's
unchanged direct BLE path still passes 13 tests / 142 assertions and typecheck.

After the effects review fixes, the complete Python suite passed **222 tests
and 34 subtests** on both Python 3.14.8 and an isolated Python 3.13 environment.
Ruff lint/format and full ty checks passed.
The added regressions cover interrupted restoration across shutdown and invalid
manual patches leaving a running effect intact. The `/ui` and `/ui/` paths
also redirect to the controller's index page.

The replacement [CI workflow](../.github/workflows/ci.yml) runs frozen Python
3.13/3.14 tests, Ruff and ty; Bun SDK tests, types, ESM/declaration builds and
package-import smoke; and Android offline tests/types. It does not build a
native APK or access the lamp, camera, phone or vendor apps. Action references
are pinned to the official release commits for
[checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1),
[setup-uv v10.3.2](https://github.com/astral-sh/setup-uv/releases/tag/v10.3.2) and
[setup-bun v2.2.0](https://github.com/oven-sh/setup-bun/releases/tag/v2.2.0),
checked on 2026-10-23. Adding the workflow is separate from observing a hosted
CI run; local validation does not establish GitHub-hosted results.

## Expanded effects and USB write investigation

The final Python implementation passed **414 tests and 34 subtests**,
including 119 pure-program cases and 30 shared-vector cases with 691 expected
targets, plus thirteen POSIX pseudo-terminal and fake-BLE hybrid lifecycle cases.
The browser passed **64 real-browser regressions** with fake fetch/GATT,
including all fourteen forms, eleven presets, malformed keyframes, fast blink
validation, no-auth recovery and hybrid-state parsing. A preset-default CCT
defect found in the first expanded run was fixed before that passing run.
The SDK passed **123 tests / 3,151 assertions**, including all shared program
samples, twenty direct-runner tests and twenty-three native-drain regressions.
Typecheck, ESM/declaration build and public import smoke passed. Android passed
**24 tests / 204 assertions**, typecheck and a Hermes bundle export. These are
offline software checks; no Android phone appeared in ADB.
The final review's three supported lifecycle findings were corrected:
ordinary native runner handoffs preserve an admitted transaction, Android
releases its busy UI after background cancellation, and Web Bluetooth cleanup
finishes after an already disconnected link. Real controller/transport and
actual hook-body regressions cover those paths.

All fourteen programs subsequently ran through the public bridge API on the
actual G60, with 141 state events and camera video retained locally. Explicit
stop and manual override passed, and 7% / 6100 K was restored. After recovering
a slow BLE session by closing its owned connection and reconnecting, the
verified 0.2-second blink cycle produced median 104.9 ms between confirmed
alternating brightness changes. Camera processing and optical contrast remain
separate limits. See [the experiment report](experiments.md).

The USB investigation isolated individual frames: `00 01` absolute brightness
and CCT writes changed output; `01 03` echoes did not. Sixty repeated write
cases all matched fresh Bluetooth readback. USB identity matched the BLE
serial/model, and baseline restoration passed. Pure USB functional reads still
received no reply. [USB research](research/usb-deep.md) records routing evidence.

The integrated hybrid then ran all fourteen programs with **427 state events**
and camera video. Explicit stop, manual override and 7% / 6100 K restoration
passed. Matched HTTPS measurements with SSE open gave median verified PATCH
latencies of **55.1 ms brightness**, **54.7 ms CCT** and **55.8 ms combined**,
versus 84.3, 85.3 and 114.1 ms on the preceding BLE-only run. Each mode used
twenty measured pairs. These laptop API round trips include fresh BLE
readback, not remote-phone latency or optical response; see [latency.md](latency.md).

## Scope of verified compatibility

Linux BLE control and camera-visible output have been tested on this G60.
Pure USB readback, factory-reset provisioning behavior, vendor sleep
commands, Windows/macOS hardware, direct browser Bluetooth, an installed OBS
session, and a physical Android device remain separate validation targets.
The available SDK/app source and compile/test checks must not be mistaken for
physical validation of those targets.
