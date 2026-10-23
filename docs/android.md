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

## Standalone Android preview APK

The minified **arm64-v8a release preview** was built successfully on an isolated
Linux build host on 2026-10-23. The local validation artifact is:

`artifacts/android-build/lighttime-g60-arm64-preview.apk`

The APK and build logs are ignored local artifacts, not committed downloads.
Build from source using the instructions below and the build record.

It is 22,779,861 bytes, package `dev.lighttime.android`, version `0.1.0` (1),
minimum Android API 24, target API 36. **JavaScript/Hermes and assets are
embedded; Metro and a development server are not required.**

```text
SHA-256: b981eb0633ae7770e9f972a8314086f6d40b543a9e2d26609d2e74f6233ef3a0
```

The APK uses the generated public **debug/test keystore**, alias
`androiddebugkey`, even though its build type is `release`. APK Signature
Scheme v2 verification passed. This is test signing, not production or Play
signing. The release manifest is not debuggable. All 18 native libraries and
the APK's native-library ZIP layout passed 16 KB alignment checks.

**Physical Android installation, startup, permission prompts and G60 BLE use
remain untested.** Stock `react-native-ble-plx` 3.5.1 compiled successfully with
RN 0.88, and its module/methods remain in the final DEX after R8. This is native
compile/package evidence, not proof of runtime interoperability. Close other
direct controllers before connecting the phone. Physical validation is deferred
until the user connects an authorized Android phone. If a bridge user service
owns the BLE connection, stop your configured unit before connecting, for
example `systemctl --user stop lighttime`, then start it again when done.

Install on a connected, authorized Android phone with:

```sh
adb install -r artifacts/android-build/lighttime-g60-arm64-preview.apk
```

Then open Lighttime, grant Nearby devices access and select the PL103 light.
The SDK allows one active direct controller at a time. See
[build evidence and reproduction](android-build.md)
for toolchain hashes, bounded build commands, signing details and logs.

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
launcher, monochrome and splash resources from `assets/lighttime-mark.xml`.

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

1. Switch on the G60 and release any other active BLE connection to it.
2. Tap **Find my light**. Android 12+ requests Nearby devices permissions
   (`BLUETOOTH_SCAN` and `BLUETOOTH_CONNECT`). Older supported Android versions
   request fine location permission for scanning and may require Location to
   be switched on. The app does not derive or collect physical location. This
   follows [Android's BLE permission rules](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions)
   using [React Native runtime permission APIs](https://reactnative.dev/docs/permissionsandroid).
3. Select the discovered **MOLUS G60**. The list shows its Bluetooth-name suffix
   and signal strength; raw identity stays in Connection details. Search lasts
   ten seconds; scan filtering uses the `PL103` name, because FEE9
   is not always advertised. Denied permissions, unavailable native modules,
   unsupported BLE hardware, Bluetooth off, empty scans and connection errors
   have separate messages. Permanently denied permissions have an app-settings
   button.
4. Wait for device information, device ID, brightness and CCT readback. Controls
   are enabled only after successful readback.
5. Set brightness **0–100%, in whole percentages**, and CCT **2700–6500 K, in
   100 K steps**. Slider movement changes a draft; releasing it commits one
   operation. The brightness fader illustrates intensity; the warm-to-cool
   white-balance strip and swatch illustrate temperature. Draft/requested values
   remain separate from actual readback. Presets use the same path. Fractional brightness and non-100 K
   values are unsupported because the actual G60 quantizes them; the SDK
   rejects them before any write. Float32 remains the brightness wire format.
6. A write is followed by separate queries. The readback panel shows actual
   hardware values and the time they were read. No write is labeled confirmed
   until readback matches. An error leaves controls disabled; **Read light**
   refreshes the actual state and allows recovery. There is no background poll.
7. Open **Patterns** to choose one of eleven shared scene presets or the five
   original effects. Selection and tuning change requested settings only;
   **Start pattern** begins output. The panel supports brightness, CCT,
   duration, cyclic period, blink duty, curves, seed, heartbeat BPM, Morse text
   and an editable keyframe sequence. SDK-sampled previews change with tuning.
   Switching between Controls and Patterns preserves the form. **Stop &
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

## Validation and remaining prerequisites

Offline verification on 2026-10-24 passes **40 Bun tests / 1394 assertions**,
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

**The arm64 release APK was compiled and inspected; no physical Android/G60
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

`bun audit` reports one high-severity transitive Expo CLI finding in
`node-forge@1.4.0`: [GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv).
The unchanged lock was re-audited on 2026-10-24; the same finding remains.
The earlier `bun audit fix` reported no published fixed version. No audit
exception, TLS relaxation or unsupported override has been added. Recheck when
the Expo/tooling dependency is updated.
