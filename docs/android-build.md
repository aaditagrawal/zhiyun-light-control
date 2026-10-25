# Android preview APK build

## Interactive model preview — 2026-10-25

`0.3.0-preview.2` / Android version code **5** adds the offline G60 showcase to
Welcome, Light and Device. The actual embedded GLB supports turn, tilt, pinch
zoom, Front/Back/Reset and a slow spin toggle. It never reads or writes the
physical light; the illustrative rear LCD is blank in the viewer.

- Package `dev.lighttime.android`; ARM64 and x86-64; **42,612,982 bytes**.
- SHA-256: `22eb48229c0cafa1a5104ff173608cc3d1f6cd8a4f3a2abde91681a59860fe90`.
- Existing test certificate remains `fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c`.
- Signature, ZIP CRC, 16 KB ZIP layout and all 36 native ELF alignments pass.
- The four unchanged fonts, two complete OFL licenses and complete Three.js
  MIT license are packaged. The embedded GLB matches its source hash.
- The viewer batches 248 model parts into 15 meshes, caps pixel density at 1.5,
  pauses in the background and releases its WebView on closing. These are
  implementation limits, not measured phone performance claims.

The final native build verified **215 checkout paths** before
and after compilation, from 12:04:18 to 12:12:57 UTC. It reused the isolated
Android toolchain, pinned Bun 1.4.3 and the original signing key. Release records
in these guides were added after the build. The ignored artifact and build
records are under `artifacts/showcase-20261025/`, with the immutable APK named
`lighttime-0.3.0-preview.2-showcase-final6.apk`.

TypeScript, **68 tests / 1617 assertions** and Hermes export pass. The component
fixture exercises the real viewer in a sandboxed iframe: rotation, zoom,
Front/Back, idle rendering, pause/resume, reduced motion and disposal. Its scene
Stop checks use mock state. A local error boundary contains viewer module
failures without unmounting the Bluetooth owner. Page messages only update the
demo; they cannot trigger hardware writes.

The exact APK was gated by package, version, signer and SHA-256 before install;
the installed `base.apk` hash matched. Native Android API 35 checks passed:
offline Welcome entry, model readiness at 1× and 2× text, Pause, Front/Back,
blank rear LCD, one-finger rotation, Reset, HOME/resume and hardware Back to
Welcome. At 2× text the footer scroll exposed the complete Reset target at
`[40,1066][680,1192]`, above the gesture inset, and Reset restarted spin. There
was no new Lighttime ANR in either pass. The original 720×1280 / density 320 /
font 1.0 settings were restored and the owned emulator exited cleanly.

The Android ANR trace from an earlier candidate pointed to `loadDataWithBaseURL`
on the UI thread. This build instead opens an immutable bundled HTML asset;
its large model is absent from the Hermes bundle. File access and universal
file-URL access stay disabled. The viewer uses the default native compositor.
Confirmed software GPUs use DPR 1, no multisampling or realtime shadow passes,
and a 30 fps ceiling; the ruby environment, geometry and contact shadow remain.
Real GPUs retain the original DPR 1.5, antialiasing and shadow quality. The native
log confirmed SwiftShader selected the software path without an override.

The software emulator observed ready after 21.1 seconds at 2× text and 17.3
seconds at 1×, including polling/command overhead. These are not phone timings
or a speed-improvement claim. Progress renews a 20-second stall window within
a 60-second active-time cap; background time is excluded. The original fixed
timeout and the earlier ANR candidate were not published. The first fresh native
build hit its 4 GiB memory limit; the successful retry used one worker and a
6 GiB limit, reusing the generated native project and original signing key.

Physical phone/G60, phone GPU performance, native pinch and TalkBack remain
unverified. The previous redesign's permission evidence remains separate below.
The dependency graph retains the two existing high Expo CLI build-tool
advisories described in that record. Native evidence is in
`artifacts/showcase-20261025/asset-final-*`; earlier reports are dated baselines.

## Previous published redesign — 2026-10-25

`0.3.0-preview.1` / Android version code **4** is the standalone scarlet-and-glass
redesign, published under the existing private `lighttime` feed. It includes
guided setup, explicit discovery, a brightness dial, white-balance ruler and
all sixteen scenes. Its scrolling layout leaves room for soft light falloff;
running Stop and navigation remain pinned.

- Package `dev.lighttime.android`; minimum API 24; target API 36.
- ARM64 and x86-64; **41,202,502 bytes**.
- SHA-256: `168e4a51367cc4a5f579c9f186dc744de935bb04b2277e1369b5431e8c2454d3`.
- Existing test signer: `fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c`.
- ZIP CRC, signature, 16 KB ZIP alignment and all 36 native ELF load alignments pass.
- Four unmodified local fonts and both complete OFL licenses are included.
  The two ruby G60 stills are bundled; the GLB and a 3D runtime are not.

The final build verified a captured manifest of 202 checkout inputs before and
after compilation. It reused the isolated Android toolchain and pinned Bun
1.4.3, completing in 79 seconds. Release metadata in this guide was added after
the build. The ignored APK is
`artifacts/redesign-20261025/lighttime-0.3.0-preview.1-visual-final3.apk`;
the manifest, signing and packaging records are in that artifact directory.

Publication reused the existing package, signer and `lighttime` feed. The
server reports version code 4, and the published APK was downloaded and checked
against the size and SHA-256 above. The app page, update feed and Obtainium
import configuration all point to that artifact.

Local checks pass: **63 Android tests / 1590 assertions**, TypeScript and Hermes
export. Independent component-fixture reviews checked all 16 scene artworks
at 320 and 393 widths, soft shadow gutters, enlarged text, actual/requested
separation, recovery, persistent Stop and retained drafts. Fixture controls use
controlled data and do not access Bluetooth.

The exact APK was hash/signature gated into an isolated Android API 35 emulator.
Native checks confirmed the continuous background, bundled typography and
lighting; scroll-reachable Welcome, Prepare and Access actions at 2× text;
horizontal step digits; and real Nearby permission grant automatically starting
discovery. Android Back returned from Discover to Access. The crash buffer was
empty. Original display settings were restored and the emulator stopped.

Three genuine cold starts measured ActivityManager TotalTime **1229, 1304 and
1205 ms**. These measure the first frame, not completed font/content rendering.
The software-rendered emulator shared a heavily loaded host; these are not
phone benchmarks or evidence of a speed improvement. Empty results and the
native app-settings round-trip passed on the preceding candidate with identical
setup/Bluetooth logic. Mid-scan Cancel could not be observed reliably on this
emulator; unit tests and earlier functional checks cover cancellation.

Native connected controls, TalkBack and physical phone-to-G60 operation remain
untested. The native report records invalid, mislabelled captures separately.
The dependency audit also retains two existing high advisories in Expo's CLI
tree (`node-forge` and `zod`); no compatible upstream fix was available in the
pinned graph. The audit is not clean. Evidence is retained under
`artifacts/redesign-20261025/`.

## Previous published preview — 2026-10-24

`0.2.0-preview.1` / Android version code **2** was built on Linux from a captured
manifest of 165 source/config hashes. The standalone release contains
the new discovery flow, indicative sliders, SDK scene previews and editable
keyframes. It needs no Metro, bridge, account or token.

Local artifact:
`artifacts/android-build/preview-20261024/lighttime-0.2.0-preview.1-android64.apk`

- Package: `dev.lighttime.android`; minimum API 24; target API 36.
- Architectures: `arm64-v8a` and `x86_64`, in one APK; no 32-bit ABI.
- Size: **40,150,127 bytes**.
- SHA-256: `d9100068f52b54f475cc50393ff5b1d93e4d3d62a4334497ea0459e825219059`.
- Embedded Hermes bundle: 1,531,496 bytes; SHA-256
  `7b9390adf2d0574bcbde914b1f20e2991d33913f913700feae53bd3d9edab0d7`.

The existing public debug/test certificate is preserved, with SHA-256
`fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c`.
This is a test-signed, R8-minified release, not production signing.
`apksigner` verification, ZIP CRC, 16 KB ZIP alignment, all 36 native ELF
alignments, BLE DEX entry points and embedded UI labels passed.
`apkanalyzer` reports `debuggable=false`. The manifest includes Nearby devices,
the never-for-location scan flag, legacy location capped at API 30, and no
camera or microphone permission.

The requested canonical clone was used for compilation, reusing the previous
isolated JDK/SDK and Gradle caches. All **165 source/config hashes** matched
before and after compilation. The native build completed in **5m 56s** with
614 executed and 32 up-to-date tasks. The wrapper's post-build `rg` lookup
initially lacked the build host's tool directory; that PATH was corrected and
the remaining verification completed separately without rebuilding the APK.
The original wrapper exit and successful follow-up checks are retained.

Software validation: **40 Android tests / 1394 assertions**, TypeScript,
Hermes bundling and GitHub CI pass. Browser fixture checks cover all 16 scenes,
narrow-screen layout, connection states and the keyframe draft correction;
they use a fake hook and web slider shim. See the
[UI review](research/android-ui-review.md) and
[response](research/android-ui-review-response.md).

Published to the owner's private Unobtainium server under the stable slug
`lighttime`. The app page `/apps/lighttime`, feed `/feed/lighttime` and
Obtainium import configuration were verified. The feed/import use Android
version code **2**; downloading the published APK over the private network
produced the same SHA-256 as the build. Obtainium can track later previews
through the same entry. This is private test distribution, with no app-store
release.

Physical phone/G60 discovery, connection and output validation remain pending.
Native emulator checks are separate from a real light session. Earlier build
evidence is retained in the following sections.

## Previous 0.1.0 preview — 2026-10-23

**Built and inspected locally:**
`artifacts/android-build/lighttime-g60-arm64-preview.apk`
— 22,779,861 bytes (about 21.72 MiB). The APK, generated native project,
toolchains and logs are ignored local artifacts, not checked-in downloads.

```text
b981eb0633ae7770e9f972a8314086f6d40b543a9e2d26609d2e74f6233ef3a0
```

The SHA-256 matches on the build host and the copied validation artifact. The
initial native build took 12m 50s. The final effects/lifecycle rebuild finished
with `BUILD SUCCESSFUL in 24s`, 11 executed tasks and 631 up-to-date, exit code 0.
The APK is `arm64-v8a` only, R8-minified `release`, package
`dev.lighttime.android`, version `0.1.0` / version code 1, minimum API 24 and
target API 36. It embeds a 1,499,664-byte Hermes bundle and needs **no Metro**.
The embedded bundle contains the corrected 5% restore explanation, fourteen
programs, eleven scene presets and the direct runner's lifecycle corrections.
Pattern/control strings were checked in Hermes using its ASCII and UTF-16
string encodings. The final bundle SHA-256 is
`526787bdcb173ee6bc5da3add2992a90aecf4a17d82bbac61917e1bde1269aac`.

All **44 final app/SDK input hashes** matched before transfer, on the build
host and after compilation. Final software checks passed 123 SDK tests / 3151
assertions and 24 Android tests / 204 assertions, TypeScript and Android Hermes
export. The rebuild reused the existing isolated official toolchain, native
project, signing key and caches; no dependency lock changed.

Signing uses the generated public **debug/test key** (`androiddebugkey`), not
a production key. `apksigner verify --verbose --print-certs` passed APK
Signature Scheme v2 verification. Signer certificate SHA-256:

```text
fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c
```

`apkanalyzer` reports `debuggable=false`. APK ZIP integrity, 16 KB ZIP
alignment, and all 18 native libraries' ELF load-segment alignment passed.
The final manifest retains Nearby devices permissions, `neverForLocation`,
and legacy Bluetooth/location permissions capped at API 30; camera and audio
recording permissions are absent.

**No physical Android installation, startup, BLE scan/connection/control or
G60 session was tested.** Compilation and APK inspection do not prove native
module runtime behavior. No emulator or phone was used. Physical tests are
deferred until the user connects an authorized phone. Release any existing
bridge-owned G60 connection before a direct Android BLE test.

Build date: 2026-10-23, Linux x86_64. After two local resource failures, the
build moved to an isolated workspace on a second Linux host. Examples below
use `/path/to/lighttime` for that workspace. The build used no EAS, production
key, publishing, phone connection, Bluetooth or camera access.

## Toolchain

All downloaded tools and the Gradle cache live beneath the ignored
`artifacts/android-build/` directory. No system packages, shell profiles, or
global Java/PATH settings were changed. `env.sh` selects tools only in the build
subprocess.

The installed app requests Expo 58.0.17, React Native 0.88.0 and
`react-native-ble-plx` 3.5.1. The generated native files and installed RN version
catalog request Gradle 9.4.1, AGP 9.2.1, Build Tools 37.0.0, compile SDK 37,
target SDK 36, minimum SDK 24, and NDK 27.1.12297006. Installed official packages:

- Eclipse Temurin OpenJDK 17.0.21+9, Linux x64.
- Android command-line tools `15859902` from the official downloads page.
- Android SDK platform `platforms;android-37.0`, revision 2. The current SDK
  repository spells the platform with `.0`.
- Android Build Tools 37.0.0 and 36.0.0; platform-tools 37.0.1. AGP installed
  36.0.0 automatically for library projects using its default Build Tools.
- Android NDK 27.1.12297006 and CMake 3.30.5. AGP also installed CMake 3.22.1
  for the Expo core library's default native build configuration.

Gradle resolves Kotlin 2.2.10 through AGP and Expo selects that loaded version;
the RN catalog declares 2.2.0. This is reported by Expo's root plugin in the
build log and matches AGP 9.2's documented minimum KGP version.

No emulator, system images, or additional Android target ABIs were selected.
The official NDK and upstream prebuilt AARs include their own target libraries;
the application build is restricted to `arm64-v8a`.

Sources: [Android downloads](https://developer.android.com/studio#command-line-tools-only),
[Adoptium JDK 17 metadata](https://api.adoptium.net/v3/assets/latest/17/hotspot?architecture=x64&image_type=jdk&os=linux),
[AGP 9.2 compatibility](https://developer.android.com/build/releases/agp-9-2-0-release-notes),
and [Expo local release compilation](https://docs.expo.dev/more/expo-cli/#compiling-android).
The generated files are authoritative for this app's versions. AGP's official
compatibility table specifies JDK 17 and Gradle 9.4.1.

## Integrity and local setup

JDK archive SHA-256, checked before extraction:

```text
8b9b611d917e3dfd8419d3566b5949226f69b052fed7e109c1368add1dfe8711
```

Android command-line tools archive SHA-256, checked before extraction:

```text
4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583
```

The JDK hash comes from Adoptium's HTTPS metadata; the command-line tools hash
comes from the official Android downloads page. Downloads, metadata, and
`SHA256SUMS` are retained in `artifacts/android-build/toolchains/downloads/`.
SDK packages were installed with the official SDK Manager, which uses the
archive checksums in Google's HTTPS repository metadata. The exact package
URLs and expected SHA-1 values are retained in `android-package-plan.json`
alongside the downloaded `android-repository-v3.xml`. TLS and integrity checks
were kept enabled.

The generated Gradle wrapper now pins the official distribution SHA-256:

```text
2ab2958f2a1e51120c326cad6f385153bb11ee93b3c216c5fccebfdfbb7ec6cb
```

This comes from
[Gradle's distribution checksum](https://services.gradle.org/distributions/gradle-9.4.1-bin.zip.sha256).
Expo prebuild may replace this ignored generated wrapper configuration.

The scoped setup used a local `env.sh` selecting the JDK, Android SDK and
build-cache directories. That file is an ignored validation artifact; adapt
your own environment before using these SDK Manager commands:

```sh
# Select your scoped JDK and Android SDK environment first.
sdkmanager --sdk_root="$ANDROID_HOME" --licenses
sdkmanager --sdk_root="$ANDROID_HOME" \
  'platforms;android-37.0' 'build-tools;37.0.0' \
  'ndk;27.1.12297006' 'cmake;3.30.5' 'platform-tools'
```

Installation was bounded by a 20-minute timeout and succeeded. SDK license
acceptance is stored only in this local SDK. SDK Manager emits an informational
deprecation warning suggesting `android sdk`; the installed command succeeds.

## Reproduce the build

First follow [Android setup](android.md#build-locally), including the frozen
Bun install, offline tests and Expo prebuild. The recorded build used app-pinned
Bun 1.4.3; typecheck and all 13 Bun tests passed on the isolated host, and both
dependency locks kept their original hashes. Use the scoped JDK/SDK versions
above, and apply the generated-file adjustments described below.

From `apps/android/android`, the successful release command was:

```sh
./gradlew :app:assembleRelease \
  --no-daemon --console=plain --stacktrace --max-workers=2 \
  -Dorg.gradle.parallel=false \
  "-Dorg.gradle.jvmargs=-Xmx2048m -XX:MaxMetaspaceSize=768m -XX:ActiveProcessorCount=2 -Djava.io.tmpdir=$LIGHTTIME_BUILD_ROOT/tmp" \
  -Pkotlin.compiler.execution.strategy=in-process \
  -Pandroid.enableMinifyInReleaseBuilds=true \
  -PreactNativeArchitectures=arm64-v8a
```

The local wrapper had a 40-minute bound. Before running the command, set
`LIGHTTIME_BUILD_ROOT` to your scoped build directory, create its `tmp`
directory, and point `TMPDIR` and `JAVA_TOOL_OPTIONS=-Djava.io.tmpdir=...`
there. Compiler concurrency and memory were bounded. The generated app's
release signing configuration uses its existing `debug.keystore`, alias `androiddebugkey`,
with the standard `android` development passwords. That key is public test
material, unsuitable for production signing. No new credentials are needed.

The generated `android/app/build.gradle` pins the app CMake version to 3.30.5,
sets single-job CMake/Ninja compile/link pools, and passes `--max-workers 1` to
Expo's bundler. The wrapper caps Node's heap at 512 MB and sets
`CMAKE_BUILD_PARALLEL_LEVEL=1`. These
local generated-file adjustments, and the Gradle distribution checksum, must
be reapplied if prebuild replaces the native project.

The isolated-host invocation enforced a 6 GiB cap for the entire build process
group, with a 5 GiB pressure threshold and no swap. This generic example assumes
you have created a local wrapper containing the command and scoped environment;
the validation wrapper is an ignored artifact, not a repository script:

```sh
systemd-run --user --unit=lighttime-android-build \
  -p WorkingDirectory=/path/to/lighttime \
  -p MemoryHigh=5G -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=512 \
  -p Nice=10 -p RuntimeMaxSec=2500 \
  /bin/bash /path/to/build-wrapper.sh
```

This profile preserves Expo 58's default R8 release minification. The earlier
local retry used one worker, 1 GiB heap, 640 MB metaspace and minification off;
the successful build used the two-worker, 2 GiB heap, 768 MB metaspace profile
above. The unsuccessful local build was stopped before the isolated-host build.

Both `TMPDIR` and `JAVA_TOOL_OPTIONS=-Djava.io.tmpdir=...` point into this
workspace, so nested Java/Prefab tools also avoid the system `/tmp` quota.

## Transfer verification

The direct LAN route and the second host's identity, Linux x86_64 architecture,
available RAM and disk were verified before transfer. The target workspace was
absent before creation. Batch SSH retained strict host-key checking; no SSH
identity, authorization, network or service settings were changed.

Only the app, its SDK package, root manifest, public toolchains and portable
Gradle dependency/wrapper caches were copied. No credentials, private keys,
tokens, camera photos, vendor applications, or broad home-directory caches
were transferred. No unrelated remote files were overwritten. All 24,479
public toolchain file hashes matched after transfer; all 30 captured source and
config hashes matched. A source-only `rsync -ani --delete` dry run was empty
after synchronization. Frozen Bun installation subsequently refreshed the
local SDK dependency and dependency metadata without changing either lock.

`assembleRelease` embeds JavaScript/Hermes and assets. Unlike the debug variant,
this release preview is intended to launch without Metro. Native compilation
and packaging do not establish physical BLE operation or successful Android
startup.

## Evidence

`react-native-ble-plx` 3.5.1's stock Java source compiled successfully in
`:react-native-ble-plx:compileReleaseJavaWithJavac` against this RN 0.88 build.
No BLE source patch or dependency override was required. The app uses
`newArchEnabled=true` and Hermes. BLE remains a legacy Java native module
through React Native interoperability; runtime event/callback behavior remains
untested. Autolinking includes `BlePlxPackage`, and APK DEX inspection confirms
`BlePlxModule` plus scan/connect/write/monitor methods survived R8.

Logs are under `artifacts/android-build/logs/`:

- `java-version.log` and `gradle-version.log`: selected JDK and Gradle.
- `sdkmanager-list.log`, `sdk-installed.log`, and `sdkmanager-install.log`:
  available packages and successful local installation.
- `sdkmanager-licenses.log`: local SDK license acceptance.
- `app-checks.log`: TypeScript passed; 13 Bun tests passed, 142 assertions.
- `assemble-release-01.log`: first attempt; daemon disappeared during app
  configuration after the build plugins compiled.
- `kernel-memory-evidence.log`: the kernel confirms PID 1310410 (that Gradle
  daemon) was OOM-killed at 2026-10-23 20:21:42 IST, before BLE compilation.
- `assemble-release-02.log`: retry capped at 2.25 GiB, no swap, one worker,
  1 GiB Java heap. This reached native CMake configuration but Prefab failed with
  `java.io.IOException: Disk quota exceeded` in `/tmp/agp-prefab-staging...`.
  It was not a BLE source compatibility failure. No global temporary files
  were removed. The task ended before transfer; no local Gradle remains.
- `build-resource-limits.log`: the retry scope's enforced memory/task limits.
- The isolated-host release and resource-status logs record the successful
  build, stack traces, unit state and enforced memory limits/usage.
- `remote-app-checks.log` and `remote-bun-install.log`: frozen install and tests.
- `remote-bun-audit.log`: the same existing high-severity Expo CLI transitive
  `node-forge@1.4.0` advisory documented in `android.md`; no lock override.
- `toolchains-transfer-verify.log`: SHA-256 checks for all public tool files.
- `apk-signature.log`, `apk-zipalign.log`, `apk-badging.log`,
  `apk-manifest.xml`, `apk-debuggable.log`, `apk-ble-module-dex.log`: final APK
  checks. `apk-inspection.json` records ABI, Hermes hash, DEX symbols and all
  ELF alignments. `SHA256SUMS` covers the APK.

The generated native configuration and autolinked package list are retained
in `artifacts/android-build/generated-native/`; `r8-mapping.txt` is the release
mapping. The original remote Gradle output remains at
`/path/to/lighttime/apps/android/android/app/build/outputs/apk/release/app-release.apk`.
The isolated build service finished successfully and is inactive. All app/SDK
source/config hashes matched before and after the build; neither Bun lockfile
nor SDK source changed during native compilation.

`build-inputs.json` captures app/SDK source hashes without modifying the SDK.
`bun-lock-before.sha256` records the existing app dependency lock for comparison.
