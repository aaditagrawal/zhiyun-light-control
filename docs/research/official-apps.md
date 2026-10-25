# Official ZHIYUN applications and G60 protocol research

Research date: 2026-10-23. Scope: official downloads, manuals, and offline static analysis for native Linux control of ZHIYUN MOLUS G60. Only this document and `artifacts/apps/` were written by this researcher. No target application was installed or executed, no hardware was accessed, no credentials were obtained, and no firmware was flashed. The user authorized reverse engineering and supplied [morluto/rea](https://github.com/morluto/rea); its README was reviewed as background, without installing or executing REA.

## Findings for implementation

- **Confirmed in app code and by parent hardware observations:** service `0000fee9-0000-1000-8000-00805f9b34fb`, write `d44bc439-abfd-45a2-b575-925416129600`, notify `d44bc439-abfd-45a2-b575-925416129601`. These carry proprietary framed commands. The parent's actual G60 accepts reads without pairing, provisioning, or mesh/account keys. Use these endpoints for direct Python/TypeScript/Android BLE control.
- **Confirmed statically:** the command frame is `24 3c`, a little-endian body length, body, then little-endian CRC-16/XMODEM over the body. Brightness command `0x1001` uses a float32; CCT command `0x1002` uses a uint16 Kelvin value. Detailed formats and source addresses follow.
- **Confirmed statically:** G60 is generation `pl103` / `PL103`, manufacturer ID `0x0905` (2309), brightness range 0–100, CCT 2700–6500 K. The advertised identifier includes a length-prefixed sequence; the app derives its four-character `userId` from that sequence's CRC, rather than reading an account credential from the advertisement.
- **Confirmed in official documentation and Mac binary:** “Zhiyun Led Tools V1.01” is the G60 firmware updater. It is a Qt Intel Mac application using serial-port APIs. This is not evidence for USB brightness/CCT control or an official Mac light-control application.
- **Parent-reported live USB result:** `fff8:0180`, `/dev/ttyACM0`, 115200 baud responds to the same framed device-info and device-ID queries, but brightness/CCT reads do not reply. Do not advertise USB light-control support from updater discovery alone.
- **Download limitation:** the current official Android direct download is an **AAB, not an APK**. The complete official AAB was downloaded and inspected directly. No verified first-party direct `.apk` URL was found; no third-party APK was substituted and no AAB was renamed/repackaged as an official APK.

## Primary sources and direct downloads

The authoritative English G60 product ID is **757**. The same-looking English `/software/758/1/` route identifies G60 but returns empty updater/manual data; the correct English route below returns all downloads. Chinese/Taiwan product IDs differ. Preserve this distinction when refreshing evidence.

1. [Official G60 support downloads](https://www.zhiyun-tech.com/en/download/software/757/1/show?page=second_nav&source=download&type=website). Saved as `artifacts/apps/g60-official-software-en.html`. Its inline `softwareData`, `upgradeData`, `instructionData`, `driverData`, and `hardwareData` JSON provides exact download URLs; those objects are also saved separately under `artifacts/apps/`.
2. [Official G60 downloads landing page](https://www.zhiyun-tech.com/en/downloads/molus-g60), [official manual index](https://www.zhiyun-tech.com/en/download/instruction/757), and [official product overview](https://zyserver1.zhiyun-tech.com/en/product/detail/757?page=second_nav&source=detail&type=website). The overview describes Bluetooth mesh app remote control; this does not imply the direct command channel needs mesh keys.
3. [Official Google Play listing, package `com.zhiyun.vega`](https://play.google.com/store/apps/details?id=com.zhiyun.vega). Developer: Guilin Zhishen Information Technology Co., Ltd. The listing describes ZY Vega as its photography-light controller.
4. **Official Android direct binary:** [Vega 1.1.7, build 75, AAB](https://oss.zhiyun-tech.com/zyplay/versions/software/Vega-1-1-7-75-20260319-google-release-aab-19d7661569b.aab). Support-page publication date 2026-04-10; filename build date 2026-03-19. Saved as `artifacts/apps/zy-vega-1.1.7.aab`. Both English and Chinese support-page Android links point to this same AAB.
5. **Official Mac direct binary:** [Zhiyun Led Tools V1.01 ZIP](https://zhiyun-website-shenzhen.oss-cn-shenzhen.aliyuncs.com/upgrade/20230224/1677207915909-aRTneG68.zip). Published 2023-02-24. Saved as `artifacts/apps/zhiyun-led-tools-mac-v1.01.zip`; extracts to `artifacts/apps/mac-updater/ptz-led.app/`.
6. [Official Windows USB-driver ZIP](https://zhiyun-website-shenzhen.oss-cn-shenzhen.aliyuncs.com/driver/20210220/1613794586437-d2x67kFR.zip), linked by G60 support as ZHIYUN USB Driver. Saved as `artifacts/apps/zhiyun-usb-driver-windows.zip`; contains `ZYCDC_Setup.exe`. It was not executed. The official downloads JavaScript says this model uses driver-free USB under macOS. That is an enumeration/driver claim, not a light-control API claim.
7. [Official G60 User Guide, current English PDF](https://zhiyun-website-shenzhen.oss-cn-shenzhen.aliyuncs.com/instruction/20260723/1784775325362-WAMQYeP8.pdf). Published 2026-07-23. Saved as `artifacts/apps/molus-g60-user-guide-20260723.pdf` with a `pdftotext -layout` transcript beside it. [Earlier official English manual](https://zhiyun-website-shenzhen.oss-cn-shenzhen.aliyuncs.com/instruction/20231229/1703837987498-n25TCbCZ.pdf) was located but not downloaded.
8. [Official Apple App Store destination](https://apps.apple.com/us/app/zy-vega/id6444816284), linked from ZHIYUN support. The fetched listing says “Only for iPhone” and iOS 13.0+. No standalone Mac lighting-control download was found in these examined official sources. Mac compatibility strings belonging to recommended *other* apps must not be misattributed to ZY Vega. Saved listing: `artifacts/apps/vega-app-store.html`.
9. [Official G60 firmware V1.6.4 ZIP](https://zhiyun-website-shenzhen.oss-cn-shenzhen.aliyuncs.com/hardware/20240402/1712027597712-f3TpzJkB.zip), linked by the support page and identified there as **PL103 Firmware V1.6.4**, dated 2024-04-02. Not downloaded or flashed. Its release note concerns Bluetooth individual/joint-control stability.

## Download integrity and provenance

SHA-256 hashes, computed over the downloaded bytes. These establish reproducibility, not independent vendor-signature validation. Sizes are bytes. Machine-readable records: `artifacts/apps/download-manifest.json`.

- `zy-vega-1.1.7.aab` — 48,034,827 bytes; `082e833fa534c2f9ab94efeb0873ca67746b7c10632471f29217e66b701aa5f3`.
- `zhiyun-led-tools-mac-v1.01.zip` — 21,734,145 bytes; `5dd3fd42b3c814db9dc72f79c91a4fac2f87119b93427b3f197b4c6471532d04`.
- `molus-g60-user-guide-20260723.pdf` — 4,282,049 bytes; `485f1a1362890ad95d347ae2a065cbec0df7c5dc0d719f51f4446cd1cd226d3e`.
- `zhiyun-usb-driver-windows.zip` — 796,503 bytes; `f6dfd3c3d8da545905a0c2f67b59c0653c040c5d4ffe49d4f8091970143275b3`.
- Extracted `vega-aab/base/lib/arm64-v8a/libzylink.so` — `79e690928aa830689f6969e2f1c1587610d647918fb14534f4e448de7bae873a`.
- Extracted `vega-aab/base/dex/classes5.dex` — `e588aa0f6c691bc8f7017339edd676b6c241664174669901462b6b9b56972c6f`.
- Extracted Mac `ptz-led.app/Contents/MacOS/ZhiyunGimbalTools` — `2de730dbf347a7d30095eb1d3db9948ea98ca7bfb1fbbbc5f490f0c33c9bde19`.

The AAB contains a v1/JAR signing certificate with subject/issuer `C=86, ST=Guangdong, L=Shenzhen, O=Zhiyun, OU=Zhiyun, CN=Zhiyun`. Certificate SHA-256: `4D:29:78:60:13:CB:87:B3:E3:60:75:B9:7B:AB:A0:82:90:7B:3F:6C:C8:10:96:0E:DE:3D:96:DC:7F:3B:2C:62`. Extracted certificate: `artifacts/apps/vega-signing-cert.pem`. This certificate was not independently matched to Google Play's app-signing certificate; AAB upload signing and distributed APK signing need not match.

## Offline inspection and evidence locations

Downloads and metadata parsing used Python's standard library through `uv run --no-project python`. ZIPs were extracted as data; the manuals were converted with the installed `pdftotext`. AAB inspection used the DEX files directly; APK installation or bundletool conversion was unnecessary.

The known analysis utility [JADX v1.5.6 official release](https://github.com/skylot/jadx/releases/download/v1.5.6/jadx-1.5.6.zip) was downloaded to `artifacts/apps/jadx-1.5.6.zip`. Only JADX, Java, and offline inspection utilities were executed, never ZY Vega or the Mac/Windows target applications. The initial whole-AAB JADX process was killed with exit 137 during memory pressure. The targeted SDK run completed with 63 decompilation errors elsewhere; do not assume every reconstructed Java branch is exact. Critical packet formats below were checked against native AArch64 instructions, rather than relying on decompiled Java alone.

Reproducible targeted command:

```sh
java -Xmx1024m -cp artifacts/apps/jadx-tool/lib/jadx-1.5.6-all.jar \
  jadx.cli.JadxCLI -r -j 1 -d artifacts/apps/vega-sdk-jadx \
  artifacts/apps/vega-aab/base/dex/classes5.dex
```

Evidence roots:

- `artifacts/apps/vega-aab/`: original extracted AAB. It includes `BUNDLE-METADATA/com.android.tools.build.obfuscation/proguard.map` and device-specific assets.
- `artifacts/apps/vega-sdk-jadx/sources/com/zhishen/zylink/`: targeted SDK Java reconstruction; use this over the partial `vega-jadx/` tree.
- `artifacts/apps/libzylink-symbols.txt` and `libzylink-disassembly.txt`: `objdump -T -C` and `objdump -d -C` on the arm64 library. Addresses below refer to this exact library.
- `artifacts/apps/light-packet-disassembly.txt`, `light-functional-disassembly.txt`, `light-command-disassembly.txt`, `light-uuid-disassembly.txt`, `mesh-device-native-disassembly.txt`: focused native excerpts.
- `artifacts/apps/MeshManagerApi.java`: the exact bundled Nordic mesh API class, separately reconstructed from `classes6.dex`.
- `artifacts/apps/mac-updater-strings.txt`: strings of the Mac executable, not runtime logs.

## BLE endpoints and routing

**Confirmed app declarations and send/receive call path:**

- Custom service: `0000fee9-0000-1000-8000-00805f9b34fb`.
- Custom write: `d44bc439-abfd-45a2-b575-925416129600`.
- Custom notify: `d44bc439-abfd-45a2-b575-925416129601`.
- `zyutils/ZYBleClient.java` declares this service. `network/BleMeshManager.java` and `BleMeshProxyManager.java` select it and the custom characteristics. `zylight/ZYLightBleClient.onLinkDataSend()` calls `BleMeshManager.sendCmdData()`; `onZYDataReceived()` passes notifications to `onLinkDataReady()`.
- The app requests ATT MTU 517 and splits command writes into `MTU - 3` chunks. That request is not a requirement: the parent's live reads succeed using the actual negotiated link. Follow discovered characteristic properties. The parent confirmed **write with response** on the actual G60; some decompiled app property checks mention write-without-response and must not override that observation.
- Actual light endpoint declarations are distinct from `FFE0` service / `FFE1` write / `FFE2` receive, which also occur in `yclight/YCLightBleClient.java`, generic SDK classes, and native UUID maps. Those strings alone would have been misleading G60 evidence.
- `ZYLightBleClient.java` also contains Nordic UART UUID constants `6E400001/2/3-B5A3-F393-E0A9-E50E24DCCA9E`. Its actual manager resolves `FEE9`; the constants do not establish that G60 exposes Nordic UART.

**Mesh services, confirmed in app code:** UUID suffixes below use `-0000-1000-8000-00805f9b34fb`.

- Provisioning service `00001827`, Data In `00002adb`, Data Out `00002adc`.
- Proxy service `00001828`, Data In `00002add`, Data Out `00002ade`.
- `ScannerRepository.java` scans for these services, recognizes network-identity/node-identity proxy advertisements using Nordic mesh APIs, and parses manufacturer data separately. A light can advertise `1828` while exposing the custom `FEE9` service after connection.

## Proprietary frame and commands

**Confirmed static reconstruction; parent independently captured matching BLE frames.** All multi-byte integers and float32 values below are little-endian.

```text
offset  size  field
0       2     magic: 24 3c
2       2     body length = 6 + payload length
4       2     message type/flags: requests constructed as 0x0100
6       2     sequence number
8       2     command ID
10      P     payload
10+P    2     CRC-16/XMODEM of bytes [4 : 10+P]
```

Total frame length is `12 + P`, equivalently `6 + body_length`. CRC polynomial `0x1021`, initial value 0, no reflection, no final XOR. The stored CRC is little-endian even though the algorithm shifts most-significant-bit first. Sequence numbers are uint16 and the native constructor increments a shared counter under a mutex. Receive code buffers fragmented data and validates CRC; implement a stream parser rather than treating each notification as one frame.

Native evidence:

- `zy::ZYLightPacket::ZYLightPacket`, `0x18f5ac`: magic `0x3c24` and default request field `0x0100`.
- `ZYLightPacket::SerializeToData`, `0x18f638`: five uint16 fields then payload.
- `ZYLightControllPacket::ZYLightControllPacket`, `0x18f6d8`: sequence allocation.
- `ZYLightControllPacket::SerializeToData`, `0x18f8dc`: length calculation, CRC coverage from offset 4, appended CRC.
- `zy::crc`, `0x14d5b4`: zero-initialized non-reflected CRC; table at `0x99dae` begins `0000 1021 2042 3063 ...`.
- `WriteUInt16ToArray`, `0x1038c0`, and `WriteFloatToArray`, `0x103930`: native little-endian stores.

Functional command payloads:

- **`0x1001` brightness:** `device_id:u16 + operation:u8 + brightness:f32`. Read operation 0 with float value 0; write operation 1 with brightness 0–100. No integer-percent conversion or 0–1 normalization appears in `PackLightSet`. Native builder `zy::FunctionalParser::PackLightSet` at `0x1686e0`; read builder at `0x168a80`.
- **`0x1002` CCT:** `device_id:u16 + operation:u8 + kelvin:u16`. Read operation 0 with Kelvin field 0; write operation 1 with target Kelvin. Native set builder at `0x168b54`; read builder at `0x168d04`.
- **`0x1008` sleep:** native `PackSleepSet` at `0x1698e0` constructs `device_id:u16 + operation:u8 + value:u8`; set operation is 1. The PL103 config lists this CID. Sleep-value semantics and effect on reconnect are **not hardware-validated**. Follow the shared API's verified brightness-zero power behavior rather than guessing this command.
- **`0x0006` registration/default-group command:** `PackRegisterToDefualtGroup`, `0x16a314`, builds `requested_device_id:u16 + 0:u16`. This changes registration/group state and is unnecessary for the parent's proven read path; do not invoke it merely to initialize a client.

Queries whose payload is empty (confirmed native `DeviceInfoParser` wrappers and common-frame builder):

- `0x2001`: local voltage (`0x16590c`).
- `0x2002`: local MTU (`0x165920`).
- `0x2003`: local device information (`0x165928`).
- `0x2004`: extra device information (`0x165930`).
- `0x2005`: device ID (`0x165904`).
- `0x8001`: local firmware version (`0x165938`).

Remote-addressed voltage `0x1201` and firmware version `0x1202` instead carry `device_id:u16`; these are different commands from the empty-payload local queries. Device ID must be queried, not assumed to equal the mesh unicast address. The parent's light returned **0**.

The parent's actual frames and readback are preserved in [the shared integration contract](../api-contract.md), including a brightness read of 5% and CCT read of 5000 K. This researcher did not generate that hardware evidence. Parent observations establish operation without keys and actual response fields; static analysis alone would not establish these facts. Use both sequence and command to correlate replies, and verify writes with readback.

## Advertisements, G60 identity, and mesh/auth details

**Confirmed Java parser:** `network/adapter/ZYMeshDevice.java`, methods `parseScanResult`, `parseSeqNo`, `isZYSupportDevice`.

```text
manufacturer data, including company identifier:
  company_id:u16 LE
  generation: ASCII terminated by 00, e.g. "pl103\0"
  identifier_length:u8
  identifier: identifier_length bytes
  remaining vendor bytes, if any
```

Android's platform manufacturer-data map normally supplies the company identifier separately, so account for that when porting this raw-AD-field parser. `isZYSupportDevice` explicitly checks company ID **2309 = 0x0905** and includes lowercase **`pl103`**. The app hex-encodes the identifier and sets `userId = NetworkUtils.getCrcString(identifier_bytes)`. Native `NetworkUtils_getCrcString` at `0x19a804` calls `zy::crcStr(..., false)` at `0x14d634`, using the same CRC polynomial/initialization. This name `userId` does **not** prove an account-login secret or a required authentication exchange.

**Confirmed device configuration:** `vega-aab/base/assets/pl103/1.6.4.config` declares generation `PL103`, firmware 1.6.4, `mtu: 150`, and `controlMode: "0x33"` for supported controls. `light_static_configuration.json` defines PL103 brightness 0–100 and CCT 2700–6500 K. That config MTU is distinct from ATT's requested 517 and must not be hard-coded as the negotiated BLE MTU. `controlMode` is capability metadata; it is not the read/write operation byte in the command payload. A `pl103/1.6.3.config` is also bundled.

**Confirmed mesh workflow, not required for direct control of the parent's light:**

1. `NrfMeshRepository.identifyNode()` uses the unprovisioned UUID from Mesh Provisioning service data.
2. On provisioning capabilities, it allocates a unicast range and calls `MeshManagerApi.startProvisioning(node)`.
3. The **bundled** `MeshManagerApi.java:1066` calls `startProvisioningNoOOB(node)`. This confirms no-OOB provisioning is the app's selected flow; it is not proof that every firmware accepts arbitrary provisioning at every time.
4. The repository reconnects/configures the provisioned node, reads composition/default TTL, sets network transmit `(2,1)`, and adds an AppKey bound to NetKey index 0 with `ConfigAppKeyAdd`.
5. `ZYLightBleClient.register()` fetches device info, firmware version, and voltage, then calls the native default-group registration command once those results are available.
6. `ZYMeshNetworkGenerator.generateNetworkMap()` generates random UUID, NetKey, and AppKey through Nordic APIs; the network can be imported/exported as mesh CDB JSON. No universal factory NetKey/AppKey was established. No personal keys were read or exported in this research.

The app refuses normal use of a client it considers unprovisioned (`ZYLightBleClient.onDeviceReady`). **This is an application policy, not a demonstrated device-side authentication requirement.** The parent's live G60 already exposes Mesh Proxy and accepts plain proprietary reads without BLE pairing, mesh provisioning, or any keys supplied by the client. Whether an unprovisioned/reset light also accepts this path remains untested; do not reset provisioning to investigate during implementation.

No proprietary payload encryption or authentication is applied by the inspected `FunctionalPacket` → `onLinkDataSend` → `sendCmdData` path: it constructs plain command data plus CRC. Standard mesh provisioning/network PDUs have their separate cryptographic processing in the bundled Nordic library. This distinction does not imply that arbitrary writes, sleep, registration, or firmware operations have been validated.

## Mac software and USB evidence

The official Mac ZIP contains **`ptz-led.app`**, executable **`ZhiyunGimbalTools`**. `file` identifies a **Mach-O x86_64 executable**, not an ARM64/universal app. `Info.plist` declares minimum macOS 10.12 and bundle ID `com.yourcompany.ZhiyunGimbalTools`. No Apple Silicon compatibility or notarization test was performed.

Static executable strings include:

- `Zhiyun Led Tools(Ver1.01)`, `MOLUS G60`, `PL103`.
- `QextSerialPort`, `QextSerialEnumerator`, `IOSerialBSDClient`, `idVendor`, `idProduct`.
- Firmware handling classes `ZyDfu_led`, `Zyled_dfu`, `verifyFirmware`, `.ptz`, upgrade progress/result messages, and USB serial discovery callbacks.

The current official manual labels USB-C as a power/firmware-upgrade port. Printed page 22 instructs use of USB-C and “Zhiyun Led Tools” with a `.ptz` firmware file. Printed page 19 describes Bluetooth reset; printed page 24 gives the CCT range. These are documentary evidence for updater/serial connectivity, not for host brightness control. No reset or update action was performed.

**USB conclusion:** the Mac updater establishes that a USB data path exists and uses serial APIs. Parent live results go further: info/ID packets work at 115200 over Linux CDC ACM, while brightness/CCT queries do not reply. Reliable lighting control should therefore use the verified BLE path; USB support should remain limited to functions actually observed. Serial framing may be shared while command availability depends on the port/firmware path.

## Confidence and remaining limits

- **Confirmed statically:** official URLs/bytes/hashes, UUID declarations and command routing, manufacturer/generation parser, PL103 ranges, exact request packet construction/CRC, no-OOB call, distinct updater role.
- **Confirmed by parent live capture, not by this researcher:** actual `FEE9` GATT endpoints/properties, no pairing/keys needed for reads, device ID 0, 5%/5000 K readback. The parent separately reports working USB info/ID at 115200 and no brightness/CCT response. At the time of writing, `local-live.md` still contains an earlier pre-access serial observation; the parent's newer update takes precedence.
- **Not established:** official direct APK download, standalone Mac lighting-control application, USB brightness/CCT control, behavior after Bluetooth reset, universal mesh keys, sleep semantics, or target firmware write behavior from this researcher's own tests. No target app was run, and Java decompilation is incomplete outside the inspected paths.
- SDK implementation should use the facts in [the shared contract](../api-contract.md) and independent code. Retain vendor binaries/decompiled material under ignored `artifacts/`; do not redistribute vendor source reconstructions as the new SDK.

Offline verification completed: all four official downloads match the manifest's sizes and SHA-256 values; six sampled TX/RX frames from the parent's contract match the reconstructed frame-length rule and `binascii.crc_hqx(body, 0)` CRC. These checks involve saved bytes only.
