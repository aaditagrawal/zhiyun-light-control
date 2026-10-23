# Android preview APK build

## Result

**Built and inspected locally:**
`artifacts/android-build/lighttime-g60-arm64-preview.apk`
— 22,695,025 bytes (about 21.64 MiB). The APK, generated native project,
toolchains and logs are ignored local artifacts, not checked-in downloads.

```text
d81e6e443ad6458ad3c7650e4aaa0bfdd37e955b0ae3ff3ad40340fd81b9b62e
```

The SHA-256 matches on the build host and the copied validation artifact. The build
finished with `BUILD SUCCESSFUL in 12m 50s`, 642 executed tasks, exit code 0.
The APK is `arm64-v8a` only, R8-minified `release`, package
`dev.lighttime.android`, version `0.1.0` / version code 1, minimum API 24 and
target API 36. It embeds a 1,414,828-byte Hermes bundle and needs **no Metro**.
The embedded bundle contains the corrected 5% restore explanation.

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
