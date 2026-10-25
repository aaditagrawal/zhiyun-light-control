# Lighttime Android

`apps/android` is an Expo Android app for direct Bluetooth control of a ZHIYUN
MOLUS G60. It uses Expo SDK 58 / React Native 0.88 and `react-native-ble-plx`
3.5.1. It consumes `@lighttime/sdk` through `file:../../packages/sdk`; no root
JavaScript manifest or SDK compilation is required for the app. Metro uses the
SDK's React Native source export, and TypeScript resolves the same source.

**Install a native build. Expo Go cannot load this BLE module.**
The upstream [BLE installation guide](https://github.com/dotintent/react-native-ble-plx#expo-sdk-43)
requires its config plugin and a native build; Expo explains the
[development-build workflow](https://docs.expo.dev/develop/development-builds/introduction/).

## Install the private Android preview

The current test build is **0.3.0-preview.1 (4)**, package
`dev.lighttime.android`. On your private Unobtainium server, open the
**Lighttime** app page (`/apps/lighttime`) and choose **Download APK** or
**Add to Obtainium**. Its stable update feed is `/feed/lighttime`; subsequent
previews update that same entry. Use a server address reachable from your phone.

This is a standalone Expo release: **no Metro, Expo Go, laptop bridge, account
or token is required**. It supports Android 7+ on ARM64 and x86-64. Open
Lighttime → **Find my light**, allow Bluetooth access, then choose your G60.
Disconnect another controller first: the G60 accepts one active direct
Bluetooth owner. Once the app reads current values, Light and Scenes
become available.

The app uses scarlet light, frosted glass and bundled Poppins
and Playfair type, following the
[Android design guide](../apps/android/DESIGN.md). Guided setup covers power,
releasing another controller, Android permissions and explicit discovery.
Returning users can choose **Find my light**. The connected
app has **Light**, **Scenes** and **Device** destinations. A running scene's
**Stop & restore** action stays visible above the scrolling content on every
destination, and unverified readback has an inline recovery action.
Brightness has a neon-ring dial; white balance has a warm-to-cool tick ruler
and swatch. Both use native sliders. Generous spacing and soft light falloff
continue down the scrolling page. Drafts remain separate from verified actual
readings. Sixteen scenes have SDK-sampled previews and editable tuning,
including native keyframe sequences.

The test-signed, R8-minified APK is 41,202,502 bytes. All 36 native libraries
and the APK ZIP layout passed 16 KB alignment checks. The original test
certificate is preserved across updates; it is not production signing.

```text
SHA-256: 168e4a51367cc4a5f579c9f186dc744de935bb04b2277e1369b5431e8c2454d3
```

The ignored validation copy is
`artifacts/redesign-20261025/lighttime-0.3.0-preview.1-visual-final3.apk`.
Install that copy on an authorized connected phone with:

```sh
adb install -r artifacts/redesign-20261025/lighttime-0.3.0-preview.1-visual-final3.apk
```

**Physical phone-to-G60 operation remains unverified.** Compilation, package
inspection, controlled component tests and emulator checks have distinct
boundaries; see [the build record](android-build.md). Release your laptop
controller's Bluetooth connection before testing direct phone control.

## Build locally

Prerequisites: Bun 1.4.3 (pinned in the app manifest), a supported Node LTS
(22.13+ or 24.3+), a JDK, and the Android SDK. Expo's CLI still uses Node;
dependencies and scripts use Bun. The installed React Native version catalog
requests Android platform 37, Build Tools 37.0.0, NDK 27.1.12297006, and supports
Android API 24+. Target SDK is 36. Set `ANDROID_HOME` to your SDK installation
and put its `platform-tools` on `PATH`. Use a toolchain-compatible JDK; the
[React Native environment guide](https://reactnative.dev/docs/set-up-your-environment)
recommends JDK 17. Do not change Gradle or Java globally to run this project.

From the repository root:

```sh
cd apps/android
bun install --frozen-lockfile
bun run typecheck
bun test
bun expo install --check
bun run prebuild
bun run android --device
```

`prebuild` generates the ignored `apps/android/android/` native project. Expo
SDK 58 regenerates native files, so put changes in app config/config plugins,
rather than the generated directory. Run it again after changing native
dependencies or plugins. The local permission plugin limits legacy location
permissions to Android 11 and earlier. The art plugin generates vector
launcher, monochrome and splash resources from `assets/lighttime-mark.xml`
and bundles the complete font licenses with the APK.

Once installed, `bun run start` starts Metro for the development build. The
phone and development machine must be able to reach one another. A normal
Android emulator usually cannot validate communication with a physical BLE
light; use an Android phone with BLE for that test. No EAS account, credentials,
publishing, or cloud build is needed for the local workflow. See Expo's
[current SDK 58 project guidance](https://docs.expo.dev/get-started/create-a-project/).

To assemble an APK without installing it or starting Metro, after prebuild:

```sh
cd android
./gradlew :app:assembleDebug --no-daemon -PreactNativeArchitectures=arm64-v8a
```

The debug APK will be `app/build/outputs/apk/debug/app-debug.apk`; that variant
still needs Metro to load the application. The standalone release preview
above embeds its application bundle; its actual build command and evidence
are in [android-build.md](android-build.md).

If your system temporary directory rejects writes, use a project-local one:

```sh
mkdir -p .cache/tmp
TMPDIR="$PWD/.cache/tmp" bun run prebuild
TMPDIR="$PWD/.cache/tmp" bun run export:android
```

## Use the light

1. Open **Set up my G60** and follow the preparation guide: switch on the G60
   and release any other active BLE connection to it. Continue with
   **My light is on**. Returning users can skip the introduction with
   **Find my light**.
2. Enable Bluetooth in Android quick settings, then choose **Allow access &
   discover**. Android 12+ requests Nearby devices permissions
   (`BLUETOOTH_SCAN` and `BLUETOOTH_CONNECT`). Older supported Android versions
   request fine location permission for scanning and may require Location to
   be switched on. The app does not derive or collect physical location. This
   follows [Android's BLE permission rules](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions)
   using [React Native runtime permission APIs](https://reactnative.dev/docs/permissionsandroid).
3. Select the discovered **MOLUS G60** in Lighttime. No Android settings pairing
   or passkey is needed. The list shows its Bluetooth name
   and signal strength; raw identity stays in Connection details. Search lasts
   ten seconds; scan filtering uses the `PL103` name, because FEE9
   is not always advertised. Denied permissions, unavailable native modules,
   unsupported BLE hardware, Bluetooth off, empty scans and connection errors
   have separate messages. Permanently denied permissions have an app-settings
   button.
4. Wait for device information, device ID, brightness and CCT readback. The
   ready screen confirms these actual settings; choose **Make it yours**.
   Controls are enabled only after successful readback.
5. Set brightness **0–100%, in whole percentages**, and CCT **2700–6500 K, in
   100 K steps**. Slider movement changes a draft; releasing it commits one
   operation. The brightness dial illustrates actual intensity; the warm-to-cool
   ruler and swatch illustrate actual temperature. A ghost marker shows a
   pending request separately. The − and + buttons request 5% or 100 K changes.
   Fractional brightness and non-100 K
   values are unsupported because the actual G60 quantizes them; the SDK
   rejects them before any write. Float32 remains the brightness wire format.
6. A write is followed by separate queries. The instruments show actual
   hardware values; Device shows the time they were read. No write is labeled confirmed
   until readback matches. An error leaves controls disabled; **Read light**
   refreshes the actual state and allows recovery. There is no background poll.
7. Open **Scenes** to choose one of eleven shared scene presets or the five
   original effects. Selection and tuning change requested settings only;
   the central play button begins output (its accessible label names the scene).
   The panel supports brightness, CCT,
   duration, cyclic period, blink duty, curves, seed, heartbeat BPM, Morse text
   and an editable keyframe sequence. SDK-sampled previews change with tuning.
   Switching between Light, Scenes and Device preserves the form. The named
   running scene stays separate from the selected draft. **Stop &
   restore** returns to both brightness and CCT captured at Start. See
   [Android light patterns](android-patterns.md) for controls and lifecycle.

The **Light output** switch sets brightness to zero when off, and restores the last nonzero
brightness observed during this controller session; connecting to an already
dark light starts with a 5% restore default. This does not switch mains
power. Disconnecting leaves the current physical settings unchanged.

Scanning stops on connection, cancellation, timeout, backgrounding and unmount.
Backgrounding the app closes the active connection; reconnect explicitly when
returning. Effects run on this phone while the app is open. Manual slider and
power writes cancel the runner without restoration before applying their own
request. Background, disconnect and unmount invalidate it synchronously and
drain owned writes before controller teardown. Failed cleanup retains its
owner for retry and blocks a replacement connection. Native subscriptions and
stale readback are invalidated; the BLE manager is destroyed after successful
cleanup when the screen unmounts. Rapid UI events cannot queue concurrent
slider/power operations.

## SDK and native transport integration

- `src/ble/NativeBleAdapter.ts` implements the SDK's `BleAdapter` interface:
  `address`, `connect`, `write`, `subscribe`, `onDisconnect`, `disconnect`.
  It discovers the contract's FEE9 service and validates the write-with-response
  and notify/indicate properties. Notifications are decoded from Base64 into
  copied byte arrays. Native writes use ATT write-with-response, with the SDK
  splitting frames into at most 20-byte chunks for the default MTU.
- `BleProtocolTransport` owns streaming frame parsing, CRC validation,
  sequence/command correlation, command timeouts and request cancellation.
  Its `send` method writes brightness/CCT commands **without waiting for a
  protocol reply**, since the actual G60 does not emit one. `request` waits for
  query replies. `LightController` owns model/ID discovery, serialization,
  brightness restore, range/resolution validation, readback and verification.
- The app adds a UI operation gate and a connection generation guard, so a late
  asynchronous result cannot report a disconnected session as successful.
  Each reconnect creates a fresh adapter, transport and controller.
- `DirectEffectRunner` adds local, verified effects to that controller.
  `EffectOwnership` and `EffectRetirement` keep cleanup ordered and retain
  failed drains for retry. Runner callbacks also check connection generation
  and runner identity. Requested forms use shared SDK preset/parser defaults;
  hardware values continue to come only from controller readback.
- Keep native BLE integration in this app and protocol behavior in the SDK.
  If files are added to the local SDK while developing, rerun `bun install` to
  refresh Bun's local file-dependency links. Tests require the SDK source to be
  present, including its whole-percent / 100 K validation.

UUIDs and captured bytes come from [the API contract](api-contract.md), with
supporting [official-app research](research/official-apps.md). The app sends
only discovery queries, brightness and CCT commands. No pairing reset, mesh
provisioning, registration, sleep or firmware commands are exposed. No HTTP
bridge fallback is included; the primary path is fully direct BLE.

## Visual development

The original [G60 model and render assets](../apps/android/assets/g60/README.md)
include a reusable GLB and reproducible build tooling. Android uses static PNGs;
Three.js is restricted to the model tools and is not an app dependency.

The [layout fixture](../scripts/android-ui-qa/README.md) renders the actual
components for screenshot review, with isolated discovery/readback values and
web input/safe-area shims. It never accesses Bluetooth. Native APK checks and
physical phone validation remain separate.

## Validation and remaining prerequisites

Offline verification on 2026-10-25 passes **63 Bun tests / 1590 assertions**,
TypeScript checking and Android Hermes export. Tests exercise Android-version
permission selection; PL103 discovery without
service advertisements; actual captured TX/RX frames through the SDK and fake
native GATT; fragmented Base64 notifications; write-without-protocol-reply plus
readback; unsupported-resolution rejection before writes; ignored write
verification failure; brightness restore; unsupported characteristics;
notification failure; cleanup during a late connect; and concurrent UI-event
suppression. Fixtures in `test/fixtures/g60-reads.json` copy the real captures
from the shared contract. All these tests run without Bluetooth or camera
access.

Pattern/UI coverage includes all sixteen requested forms, shared CCT defaults,
immutable presets, bounded 64-sample previews, finite endpoints, editable
keyframes, curves and blink duty. Slider tests exercise touch drafts, single
commit on release, non-touch adjustments and failed-readback labeling.
Lifecycle tests preserve synchronous cancellation, drain-before-disconnect,
retained-owner retries and busy-state recovery after background/disconnect.
The SDK separately tests underlying native work that outlives a caller's
deadline. Controlled app-side tests alone do not establish native settlement.

The published APK also passed isolated Android 15 setup checks: native rendering,
scroll-reachable actions at 2× text, real Nearby devices permission grant,
automatic discovery and Android Back recovery. The installed and published
downloads match the recorded SHA-256. Connected controls and physical BLE
remain outside those checks; see [the build record](android-build.md).

**The ARM64/x86-64 release APK was compiled and inspected; no physical Android/G60
session was validated.** The build used a scoped, verified JDK 17 and Android
SDK under the ignored build directory; system Java remains unchanged. After
local host resource failures, the successful build ran in an isolated
Linux build workspace. Signature, embedded Hermes bundle, ABI, manifest and
16 KB library alignment checks passed. The recorded G60 BLE/camera results
validate the protocol/hardware findings, not this app's native runtime.
Android 12+ permission flow, older-Android location behavior,
`neverForLocation` discovery visibility, legacy-module interoperability and
actual BLE control still require a physical Android test. The upstream BLE
compatibility table does not independently certify this newest Expo/RN
combination; the successful native build provides compile/package evidence.

`bun audit` on 2026-10-25 reports two existing high-severity Expo CLI findings:
`node-forge@1.4.0` ([GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv))
and `zod@3.25.76` ([GHSA-mrhx-c7xc-m3w5](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5)).
The former has no published fix; the latter requires a major version outside
Expo CLI's declared range. The pinned dependency graph remains unchanged apart
from exposing the existing font package as a direct dependency. No audit
exception, TLS relaxation or unsupported override has been added.
