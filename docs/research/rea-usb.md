# REA, G60 USB, and firmware research

Research date: 2026-10-23. Scope: offline source/package inspection; no hardware access, app execution, flashing, global configuration changes, or analysis-engine installation by this researcher. The parent owns controller implementation and hardware experiments. This report incorporates the parent's later live evidence separately from static findings.

## Decision for implementation

**Use BLE for normal lighting control. USB is a confirmed data connection with identification replies, but normal brightness/CCT control over USB remains unverified.** It is incorrect to call USB power-only. It is also premature to promise a fully functional USB lighting controller.

- **Official documentation:** the G60's USB-C port supports power and firmware upgrades. The manual identifies it as a “Type-C Power Supply/Firmware Upgrade Port” (printed p. 5), describes PD power (p. 6), and computer-based `.ptz` upgrades (pp. 22–23). The product page describes Bluetooth mesh app control. Neither source documents a normal USB brightness/CCT API. These are documented uses, not proof that undocumented USB commands cannot exist. [Official manual][Z1], [official product description][Z2].
- **Parent live evidence, supplied after this offline investigation:** BLE information, device-ID, brightness, and CCT reads succeeded on `F3:58:E9:18:35:EA`; device ID was `0`, readback `5% / 5000 K`. Service `0000fee9-0000-1000-8000-00805f9b34fb`, write `d44bc439-abfd-45a2-b575-925416129600`, notify `d44bc439-abfd-45a2-b575-925416129601`; no pairing, mesh provisioning, or account key was needed for these operations. Exact frames and payload layouts are in [the shared API contract](../api-contract.md).
- **Parent live USB evidence, supplied in the research handoff:** `fff8:0180`, `/dev/ttyACM0`, 115200 baud; the same ZYBL information and device-ID queries replied, while brightness/CCT read queries did not. This establishes identification over USB, not lighting-control support or its impossibility. The parent message is newer than [local-live.md](local-live.md), whose initial permission/no-commands note was still stale when read for this report.

For Python/TypeScript SDKs, the local bridge, browser/OBS clients, and Expo Android: implement against the shared contract; treat readback-confirmed BLE as the primary transport. A mesh-looking advertisement does not make mesh provisioning necessary for the verified direct GATT endpoint. A USB connection or information reply must not be reported as successful lighting control. This pass did not verify BLE writes; implementation agents should use the parent's current write/readback evidence and tests.

## REA checkout and applicable instructions

Shallow, single-branch clone at `vendor/rea`, no submodules initialized. Inspected commit: **`5f702dd6ccd9e8e505e224854b91678439c3b41f`**, package version `6.7.0`.

Read the user-level `AGENTS.md`, REA's [`AGENTS.md`][R1], its [`reverse-engineer-anything/SKILL.md`][R2], and the Android/native reference guides before applying their workflow. Relevant rules: separate observations/inferences/unknowns, preserve artifact identity, follow focused searches from an entry point, and retain limitations. Setup writes agent configuration and installs workflow instructions; it is unnecessary for CLI use and was not run. The cloned skill was read in place, not installed globally.

The useful workflow is **inventory → targeted class/symbol search → method/function body → callers/references → corroborating readback**. Retain APK/binary digest, selected architecture, addresses, engine version, and incomplete coverage. Static pseudocode is not an execution trace. For MCP, discover actual tools and `binary_session` availability instead of assuming repository main equals the installed release. [REA skill][R2], [native reference][R3], [CLI guide][R7].

## Reusable tools on Linux

- **Already usable here:** `7z`, `file`, GNU `strings`, GNU `nm -C`, GNU `objdump -d -C`, `readelf`, `pdftotext`, and standard-library Python through `uv`. In this environment, Homebrew GNU binutils **2.47.20260726** successfully reads and disassembles the updater's Mach-O. A macOS machine, Xcode, Wine, and Qt runtime are unnecessary for these static operations. Commands below use the verified binaries on the existing PATH.
- **REA native analysis:** Linux Ghidra can analyze a thin x86-64 Mach-O updater and extracted ARM ELF libraries. This checkout accepts Ghidra `12.1.x`, verified against `12.1.4`, with a matching native decompiler and full JDK allowed by that installation (currently 21+). `GHIDRA_INSTALL_DIR` selects an existing installation. Universal Mach-O must first be thinned for this provider. Ghidra was not installed or run here. Its ephemeral project does not modify executable bytes. [Provider guide][R4], [installation][R5], [target admission source][R8].
- **REA Android analysis:** requires a **standalone APK**, a full JDK 17+, and **`jadx-headless-mcp 0.7.1`**, selected by `REA_JADX_MCP_JAR`. That is a different artifact from the parent's standalone JADX `1.5.6` distribution. The pinned MCP JAR digest is `6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75`. Then use `inspect_android_package`, `search_android_classes`, `inspect_android_class`, `inspect_android_method`, and incoming `trace_android_references`; no emulator/device/Android SDK is needed. [Android guide][R6], [release identity][R9].
- **AAB boundary:** the official ZY Vega Android download is an `.aab`; REA's Android family explicitly does **not** handle AAB/split APK inputs. Do not rename it `.apk`. Inventory/extract the AAB as ZIP, use the parent's existing standalone JADX for its DEX, or obtain a real standalone APK before REA Android calls. A bundletool conversion is an optional later step, not required to begin. The local AAB contains `base/dex/classes.dex` through `classes6.dex` and `libzylink.so` for arm64-v8a and armeabi-v7a. [REA boundary][R6], [official Android artifact][Z6].
- **Firmware analysis:** REA offers caller-supplied Binwalk `3.1.x` region inspection and Unblob `26.6.x` extraction on Linux, with `prlimit`; verified parser versions are 3.1.0 / 26.6.4. They inventory files, not a connected lamp. Neither engine is installed here, and neither is needed to parse the small PTZ wrapper. Do not feed an unknown opaque payload to a guessed raw-ARM profile. [Firmware guide][R10], [raw-binary requirements][R11].
- **Host checks:** Node `v24.22.1`, Bun `1.4.3`, uv `0.13.4`, full OpenJDK `27` (`jdk.compiler` present), and procps-compatible `ps` were observed. REA, Ghidra, Binwalk, Unblob, and the REA JADX MCP JAR were not found on the checked PATH. JDK 27 was not tested with either analysis engine. REA's supervised Linux workers require procps `ps`; setting `JAVA_HOME`/`REA_*` for a single process avoids global config changes. [Linux prerequisite][R5].

No REA npm dependencies were installed and no REA CLI/MCP analysis was executed. Repository source is sufficient for this capability audit. The source checkout preserves `package-lock.json` and `npm@11.16.0`; it was not migrated to Bun. For later package use, `bunx rea-agents@6.7.0` invokes the published Node executable without `setup`. Registry metadata confirms this release exists (28,575,703 unpacked bytes before dependencies), but its older Node engine declaration differs from current main. Re-check release capabilities before using main-only tools. [Published metadata][R12], [release boundary][R5].

Relevant validation workflows were inspected, not run: `verify:android`, `verify:ghidra:cross-format`, and `verify:firmware`; `docs/testing.md` states their real-engine prerequisites. The Ubuntu `ghidra-bridge-sources` workflow downloads a complete Ghidra SDK and compiles/probes the bridge, so it is not a lightweight setup recipe. `real-hopper-linux` also has a self-hosted Hopper lane. [Testing guide][R13], [Ghidra workflow][R14], [Hopper Linux workflow][R15].

## Official artifacts and provenance

The parent had already downloaded the manual, macOS updater, USB driver, and Android AAB into `artifacts/apps`; those files were reused read-only. Their official URLs are retained in `instructionData.json`, `upgradeData.json`, `driverData.json`, `hardwareData.json`, and `softwareData.json` there, extracted from the [official G60 downloads page][Z3]. The researcher separately downloaded only the **235,713-byte firmware ZIP**. Binary identity is SHA-256, not a claim of signature verification.

- [Current official manual][Z1], local `artifacts/apps/molus-g60-user-guide-20260723.pdf`: `485f1a1362890ad95d347ae2a065cbec0df7c5dc0d719f51f4446cd1cd226d3e`.
- [macOS Zhiyun Led Tools V1.01, 2023-02-24][Z4], local `artifacts/apps/zhiyun-led-tools-mac-v1.01.zip`: `5dd3fd42b3c814db9dc72f79c91a4fac2f87119b93427b3f197b4c6471532d04`.
- Extracted `ptz-led.app/Contents/MacOS/ZhiyunGimbalTools`: `2de730dbf347a7d30095eb1d3db9948ea98ca7bfb1fbbbc5f490f0c33c9bde19`; 10,955,184 bytes, **thin x86-64 Mach-O**, Qt/QMake application, minimum macOS 10.12 from `Info.plist`.
- [Official Windows USB driver][Z5], local `artifacts/apps/zhiyun-usb-driver-windows.zip`: `f6dfd3c3d8da545905a0c2f67b59c0653c040c5d4ffe49d4f8091970143275b3`. Its `ZYCDC_Setup.exe` is Inno Setup 5.5.7; installed `7z` could not unpack it. It was not executed; no INF or installed-driver behavior was recovered.
- [G60/PL103 V1.6.4 firmware, 2024-04-02][Z7], local `vendor/rea/.research/g60/g60-v1.6.4.zip`: `44c2b1af5c847076f15d3c44059659f8f70735d328e2bbf135a980bc5bfb4c31`.
- Its `PL103 Firmware V1.6.4/firmware.ptz`: `79a940b993969ce714a9fc15898a39e8ec9bb63f365efec2afae8bbf5d6a4bdc`; **234,934 bytes**. Official release notes describe improved individual/joint Bluetooth control and stability, without a USB lighting API. [Release listing][Z3].

## What the macOS updater establishes

These are static observations in the exact updater above; addresses are preferred-image virtual addresses, not live process addresses. Evidence is retained under `vendor/rea/.research/g60/` and can be reproduced with the commands below. Source attribution: [official updater ZIP][Z4].

- `MainWindow::serialDeviceMatch` at **`0x100038790`** loops over three VID/PID pairs in `serialDeviceInfo` at **`0x100a3c088`**: `10c4:ea60`, `1a86:7523`, **`fff8:0180`**. The latter agrees with the parent's observed G60. These are updater-accepted IDs; the table alone does not prove the other two occur on this lamp. See `serial-match.disasm.txt`, `serial-ids.hex.txt`.
- `MainWindow::cdcDeviceMatch` at **`0x100038d80`** explicitly compares `fff8` and `0180`; enumeration also contains the `AppleUSBCDC` string. This supports a CDC serial interpretation, corroborated by the parent's actual Linux `cdc_acm` enumeration. See `cdc-match.disasm.txt`.
- Constructor calls at **`0x100036a65`–`0x100036ac9`** select baud `0x1c200` = **115200**, data bits `8`, and zero-valued stop/parity/flow enum settings. These are consistent with 8N1/no flow control; baud is directly established. See `serial-settings.disasm.txt`.
- `Zyled_link::transmit` at **`0x100028630`** builds `24 3c`, a little-endian body length, six bytes of metadata plus caller payload, and little-endian CRC16 over the body. `crc16_calc` / `crc16_update` at **`0x10000c0f0` / `0x10000c050`** implement initial zero, polynomial `0x1021`, augmented with two zero bytes. This matches the structure independently established by the parent's BLE frames. See `led-link.disasm.txt`, `transport-crc.disasm.txt`, [API contract](../api-contract.md).
- There are **multiple protocol paths**: `zyled::transmit` at **`0x100013c30`** starts its body with `03 01 13 14`; `Zyled_link` is a separate implementation. Do not mix a legacy DFU body with the verified six-byte message/sequence/command body. See `legacy-led-transmit.disasm.txt`.
- `MainWindow::zyled_main_thread` at **`0x10003a030`** calls `Zyled_dfu::chipSync`; the binary exposes `readsn`, `writesn`, `dfu_start`, `chipWrite`, `chipCheck`, `chipRun`, `parameter_save`, and firmware download state machines. Firmware, reboot, serial-number writes, calibration, and parameter persistence belong outside the normal-control SDK. Generic `ParamForm` code includes gimbal motor controls, so a parameter form is not proof of a G60 brightness control. See `main-led-state.disasm.txt`, `mac-symbols.txt`.

This limited analysis recovered an update/identification transport. It did not recover a verified USB brightness/CCT handler, USB mode-switch requirement, or complete firmware command map. The parent's nonreply observations remain the strongest current evidence about those USB lighting requests.

For deeper analysis, the official Android AAB's extracted arm64 `libzylink.so` has SHA-256 `79e690928aa830689f6969e2f1c1587610d647918fb14534f4e448de7bae873a`. `readelf -Ws --demangle` exposes immediate entry points: `ZYLightClient::SetLight(unsigned short,float,...)` **`0x171c34`** (another overload at `0x171740`), `GetLight` **`0x171fbc`**, `SetColorTemp` **`0x17256c`**, `GetColorTemp` **`0x172fe0`**, and `JNIZYClientListener::OnLinkDataSend` **`0x195548`**. These are ELF symbol addresses and investigation seeds, not recovered setter semantics or runtime observations. [Official AAB][Z6].

## PTZ wrapper and firmware architecture limits

`PtzFile::unpack` (**`0x10000d2d0`**), `calcCRC16` (**`0x10000cc60`**), and `updateCRC16` (**`0x10000da00`**) describe a 16-byte header, length-delimited elements, and final checksum. A standard-library reader was written at `vendor/rea/.research/g60/inspect_ptz.py`; it reads a local file and emits JSON without extraction or execution. Its output is retained as `firmware-inventory.json`. [Updater source artifact][Z4], [firmware source artifact][Z7].

Observed results:

- Header magic `PTZ`, format byte `1`, little-endian total length **234934**, field at offset 8 = `2`, updater `BOOT` ID `0x0003`, `ARCH` ID **`0x0541`**, `PACK` value **164**.
- First element begins at **16**, total length **89130**, ID `0x0840`, version value `164`; payload `[24, 89144)`, **89120 bytes**.
- Second begins at **89146**, total length **145786**, ID `0x0850`, version value `158`; payload `[89154, 234930)`, **145776 bytes**.
- Walking those element lengths ends exactly at the final two-byte CRC. The whole-file trailer **`0xbf51` matches** the updater-derived CRC calculation over all preceding bytes. Trying that algorithm over each stored payload does **not** match its element trailer; their checksum/transform semantics remain unresolved, not evidence of corruption.
- Payload entropy was **7.997653 / 7.998776 bits per byte**; both payload lengths are divisible by 16. Those facts do not distinguish encryption, compression, or another transformation. The CPU ISA, flash layout, chip roles, load addresses, and whether the two elements represent different chips remain **unknown**. `ARCH=0x0541` is an updater identifier, not established ARM architecture evidence.

The installed `file` utility misidentified the opaque PTZ as HFS+ with implausible geometry; its first bytes and the matching updater parser identify a PTZ container. Do not mount it or follow that signature guess. No flash image was decoded, no payload was executed, and no complete firmware architecture is claimed.

## Concrete remaining work

1. **Implementation can start now:** use the shared framing/GATT contract and independent code. Python BLE plus a local HTTP bridge serves Python/TypeScript SDKs and OBS/browser integration; Expo Android can use the verified direct GATT service. Preserve response correlation, notification reassembly, CRC checks, timeouts, and actual readback. Successful identification is insufficient to mark a transport ready for dimming.
2. **Confirm normal BLE setters in the parent's authorized hardware work:** establish brightness/CCT write acknowledgment and readback, then reconnect/error behavior. This researcher will not perform those experiments. Avoid designing mesh provisioning/account flows for the already verified endpoint.
3. **If USB control remains a goal:** trace the Android normal-control command path into its native `libzylink.so`, compare serial and BLE dispatch, and inspect the updater's callers of `Zyled_link::send`. Seek an explicit handler, gate, transport capability, or documented mode distinction. Distinguish no response, negative response, and write success. Do not try DFU/reboot/calibration commands as discovery probes.
4. **Use deeper tools only for a concrete unresolved function:** reuse the parent's JADX tree first. If needed, select an existing Ghidra installation for the thin Mach-O or native ELF. Acquire the separate REA JADX MCP JAR only if a real APK and a REA-specific query justify it. No automatic Ghidra/Android SDK/Qt/Rosetta installation is needed.
5. **Firmware is optional follow-up:** map PTZ IDs and transforms from updater code before choosing CPU/base addresses. If a recognizable native image is recovered, retain its hash/ranges and then choose a disassembler profile. A blind Binwalk/Unblob installation does not resolve encrypted/opaque element semantics.

## Exact next useful commands

All commands below are offline and avoid hardware. Run from the project root.

```bash
cd /path/to/zhiyun-light-control

# Inspect the current integration facts before writing SDK or client code.
cat docs/api-contract.md

# Reproduce the PTZ inventory; no dependencies or device are required.
uv run --no-project python vendor/rea/.research/g60/inspect_ptz.py \
  'vendor/rea/.research/g60/firmware/PL103 Firmware V1.6.4/firmware.ptz'

# Read focused evidence, avoiding another whole-binary analysis.
cat vendor/rea/.research/g60/serial-settings.disasm.txt
cat vendor/rea/.research/g60/serial-ids.hex.txt
rg -n 'Zyled_link::send|Zyled_dfu::|zyled::transmit|PtzFile::' \
  vendor/rea/.research/g60/mac-symbols.txt

# Reproduce the modern frame builder from the verified Mach-O.
objdump -d -C --start-address=0x100028630 --stop-address=0x100028830 \
  vendor/rea/.research/g60/mac/ptz-led.app/Contents/MacOS/ZhiyunGimbalTools

# Follow the normal Android transport in the parent's extracted native library.
readelf -Ws --demangle artifacts/apps/vega-aab/base/lib/arm64-v8a/libzylink.so \
  | rg 'ZYLightClient::(SetLight|GetLight|SetColorTemp|GetColorTemp)|OnLinkDataSend'
```

Only if an existing Ghidra installation has already been selected, the next REA commands are:

```bash
# Set GHIDRA_INSTALL_DIR to the actual existing installation first.
test -x "${GHIDRA_INSTALL_DIR:?select an existing Ghidra installation}/support/analyzeHeadless"
bunx rea-agents@6.7.0 doctor --provider ghidra --json
bunx rea-agents@6.7.0 function \
  vendor/rea/.research/g60/mac/ptz-led.app/Contents/MacOS/ZhiyunGimbalTools \
  0x100028830 --provider ghidra --json
```

Those two `bunx` commands were **not executed** here; they may fetch the pinned REA package and dependencies into Bun's cache. They do not invoke `setup` or authorize engine installation. CLI commands start separate processes; save Evidence JSON when a result needs reuse.

Validation performed: offline PTZ inventory completed with matching whole-file CRC and exact element boundaries; the helper passed Ruff; the updater citation was checked against the preserved official download metadata. No firmware or application was executed and no live-device results are attributed to this researcher.

[Z1]: https://zhiyun-website-shenzhen.oss-cn-shenzhen.aliyuncs.com/instruction/20260723/1784775325362-WAMQYeP8.pdf
[Z2]: https://zyserver1.zhiyun-tech.com/en/product/detail/757?page=second_nav&source=detail&type=website
[Z3]: https://zyserver1.zhiyun-tech.com/en/download/software/757/1/show?page=second_nav&source=download&type=website
[Z4]: https://zhiyun-website-shenzhen.oss-cn-shenzhen.aliyuncs.com/upgrade/20230224/1677207915909-aRTneG68.zip
[Z5]: https://zhiyun-website-shenzhen.oss-cn-shenzhen.aliyuncs.com/driver/20210220/1613794586437-d2x67kFR.zip
[Z6]: https://oss.zhiyun-tech.com/zyplay/versions/software/Vega-1-1-7-75-20260319-google-release-aab-19d7661569b.aab
[Z7]: https://zhiyun-website-shenzhen.oss-cn-shenzhen.aliyuncs.com/hardware/20240402/1712027597712-f3TpzJkB.zip
[R1]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/AGENTS.md
[R2]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/.agents/skills/reverse-engineer-anything/SKILL.md
[R3]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/.agents/skills/reverse-engineer-anything/references/native-and-artifacts.md
[R4]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/docs/ghidra-provider.md
[R5]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/docs/installation.md
[R6]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/docs/android-analysis.md
[R7]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/docs/cli.md
[R8]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/src/ghidra/GhidraProvider.ts
[R9]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/src/android/JadxRelease.ts
[R10]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/docs/firmware-analysis.md
[R11]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/docs/raw-binary.md
[R12]: https://registry.npmjs.org/rea-agents/6.7.0
[R13]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/docs/testing.md
[R14]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/.github/workflows/ghidra-bridge-sources.yml
[R15]: https://github.com/morluto/rea/blob/5f702dd6ccd9e8e505e224854b91678439c3b41f/.github/workflows/real-hopper-linux.yml
