# Existing implementations: Zhiyun MOLUS G60 / ZY Vega

Researched 2026-10-23. Five public repositories were cloned under
`vendor/protocol-research/`; exact revisions and license-file inventory are in
[sources.json](../../vendor/protocol-research/sources.json). This research task
did not scan, connect to, pair, provision, reset, or write to local hardware.
Hardware observations below are attributed to the parent task or upstream
authors. No gimbal implementation is used as evidence of light compatibility.

## Implementation decision for this project

Implement the direct **FEE9 GATT** path with request type `0x0100`, device ID
`0`, read operation `0`, write operation `1`, float32 brightness in percent,
and uint16 Kelvin. These are supported by the PL103 implementation and the
parent's live G60 readbacks. The parent reports successful BLE info, ID,
brightness, and CCT queries, device ID `0`, and actual state **5%, 5000 K**;
no pairing, mesh key, or provisioning action was needed for those queries.
See the captured frames and current contract in
[api-contract.md](../api-contract.md#direct-ble-android--web-bluetooth).
Readback success alone does not establish that a write physically changed output.

The parent's latest update also reports USB `fff8:0180`, `/dev/ttyACM0`,
115200 baud: the same framed info/ID queries respond, but brightness/CCT reads
do not. Do not advertise USB brightness/CCT support based on BLE results.
[local-live.md](local-live.md) contains earlier discovery evidence; its initial
serial-access restriction is superseded by the parent's latest update.

For power, follow the shared contract: brightness `0` means off and on restores
the last nonzero brightness. Upstream command `0x1008` has conflicting power
semantics and is unnecessary for that contract. The existing fixture advertises
Mesh Proxy `1828`; needing no new provisioning action on this fixture does not
establish behavior on a factory-reset, unprovisioned light.

## Sources, scope, and licenses

| Repository and inspected commit | Evidence specific to this task | License at that revision |
| --- | --- | --- |
| [monneyboi/zyvega][zy-root], `b19f0e67bdb33f7d4a29ee850bb5fe8264b868d8` | Linux BlueZ GATT CLI targets PL103; author describes Android `com.zhiyun.vega` / `libzylink.so` reverse engineering and reports hardware verification of target `0`, operation `1`. Separate BlueZ mesh controller. [README][zy-readme], [research notes][zy-notes]. | **No explicit open-source license found.** No LICENSE/COPYING file; README's License section identifies the proprietary vendor app, without granting rights to this repository's code. Treat as protocol evidence; do not copy implementation. |
| [robinebers/zhiyun-cli][cli-root], `6133a30832c800684e11862e27cfa072f814e1d1` | Bun/JavaScript macOS CLI and persistent BLE gateway explicitly target G60; README identifies raw device number `0x8003`. Has pure codec tests. [G60 notes][cli-readme], [protocol][cli-protocol]. | **No explicit license found**, including no license field in [package.json][cli-package]. Public source is not a copying grant. |
| [makerwolf/Light-Bridge][swift-root], `24b502cc4b6f7602ed98349adb7da17b89de84c2` | Swift/CoreBluetooth implementation says its protocol came from an iOS Vega sniff of **PL105**. Corroborates framing and value encoding; its scope is not independent proof of G60 compatibility. [Protocol constants][swift-protocol]. | [README declares MIT][swift-license], but no standalone LICENSE or complete notice is present in the tracked tree. Preserve that distinction when assessing reuse. |
| [didim99/HA-Zhiyun-Light][ha-root], `7ca51ea6e172e2f45d8b78ae09e09b12f226cbad` | Actual Home Assistant custom integration, derived from Light-Bridge. README reports testing on **X100**, HA 2026.3.1. Its [catalog][ha-protocol] maps PL103 to G60, but that catalog is not a G60 test report. | [MIT LICENSE][ha-license], copyright didim99 2026. Preserve notice on reuse; document its stated Swift ancestry. |
| [aaditagrawal/zhiyun-light-control][sdk-root], `00748ac38d24f86cc1d4a02b4ad135f503f16889` | Python USB/BLE SDK with PB-GATT provisioning, mesh cryptography/configuration, and detailed G60 1.6.4 field notes. Later notes report physical BLE scene control after mesh setup, using object `1`, op `0x33`; distinguish this from the other clients. [BLE field notes][sdk-ble], [hardware notes][sdk-hardware]. | [MIT LICENSE][sdk-license], copyright 2026. A usable licensed reference, though broad README/earlier notes retain experimental and outdated status language. |

Zhiyun's own [G60 download page][official-download] labels firmware 1.6.4 as
PL103 and describes Bluetooth improvements. The linked manufacturer
[user guide, PDF page 28][official-manual] specifies PL103, brightness 0–100%,
and 2700–6500 K. A local copy and SHA-256 are preserved in the research directory.
This establishes the model identity without relying on other Zhiyun products.

Searches covered GitHub repository metadata, indexed source/README pages, and
Home Assistant queries for Zhiyun, MOLUS G60, PL103, ZY Vega, and FEE9. The HA
integration above was the relevant integration located; this is a bounded
research result, not a claim that no other implementation exists.

## Exact runtime transport and framing

Four codec files inspected, including related implementations, agree on these
runtime UUIDs:

| Role | UUID |
| --- | --- |
| Service | `0000fee9-0000-1000-8000-00805f9b34fb` |
| Command write | `d44bc439-abfd-45a2-b575-925416129600` |
| Notification response | `d44bc439-abfd-45a2-b575-925416129601` |

Evidence: [PL103 GATT constants][zy-gatt], [JavaScript constants][cli-protocol],
[HA constants][ha-protocol], and [SDK transport constants][sdk-transport].
The parent independently verified these on the local G60, including write with
response support. FEE9 may not be included in advertisements: select PL103/name
candidates, then confirm the connected GATT service and characteristics.
Do not replace these with the SDK's default Nordic UART `6e400001…` profile:
its own G60 report identifies FEE9, named **legacy** in that SDK. The `FFE0`
and Nordic UART profiles are not evidence of G60 support. [SDK BLE profiles][sdk-profiles]

Let `N` be payload length. Runtime fields are little-endian:

| Byte offset | Size | Contents |
| --- | --- | --- |
| 0 | 2 | Magic `24 3c` |
| 2 | 2 | Body length `6 + N`; excludes magic, length, CRC |
| 4 | 2 | Request type/first word `0x0100`, bytes `00 01` |
| 6 | 2 | Sequence number |
| 8 | 2 | Command ID |
| 10 | N | Payload |
| 10 + N | 2 | CRC, low byte first |

Total length is `N + 12`, or declared body length + 6. The Swift source defines
response type `0x0001` (`01 00`); that is also observed in the parent's live
responses. Preserve this field in decoded messages rather than assuming requests
and replies have the same type. [Swift constants][swift-protocol], [Swift builder][swift-builder],
[HA builder][ha-protocol], [JavaScript framing][cli-protocol]

Checksum is **CRC-16/XMODEM**: polynomial `0x1021`, initial value `0x0000`,
no reflection, no final XOR. Cover only bytes starting at offset 4 through the
last payload byte; exclude the first four bytes and the CRC itself. Append the
16-bit result little-endian. Reference: ASCII `123456789` gives `0x31c3`.
The SDK uses `binascii.crc_hqx(body, 0)`; its result agrees with the other codecs.
[PL103 CRC][zy-gatt], [JS CRC and test][cli-test], [SDK codec][sdk-protocol]

The PL103 CLI calls byte 3 padding and uses a one-byte length, whereas HA,
Swift, and both newer codecs use uint16 length. They produce identical small
command frames. Implement uint16 length with a bounded receive buffer, reject
body lengths below 6, validate CRC, and handle partial/multiple frames across
notifications. The project contract requires a streaming parser. Increment a
per-session uint16 sequence and match replies by **both sequence and command**;
the G60 JS gateway demonstrates this matching. [Gateway][cli-gateway]

Brightness frames are 19 bytes; CCT 17; power 16; empty global queries 12.
These basic requests fit a 20-byte ATT payload. Larger info responses still
require proper receive handling; do not infer a negotiated MTU from this fact.

## Commands and the important profile differences

The functional payload shape is:

```text
target/object/raw-device-number: uint16 LE
operation:                      uint8
value:                          command-specific
```

| Command | Value encoding / query |
| --- | --- |
| `0x1001` brightness | IEEE-754 float32 LE, **percent 0–100**, not a byte or normalized 0–1. Query: operation `00` plus four zero bytes. |
| `0x1002` CCT | uint16 LE, **Kelvin 2700–6500**, not mireds. Query: operation `00` plus two zero bytes. `2700 = 8c 0a`, `3200 = 80 0c`, `6500 = 64 19`. |
| `0x2003` info | Empty payload. Response contains null-delimited ASCII identity fields. Keep raw strings: upstream parsers disagree on their exact labels/order. |
| `0x2005` device ID | Empty payload. Parent's G60 returns uint16 `0`. |
| `0x8001` firmware | Empty global query in JS/HA; null-terminated text is the expected parser representation. |
| `0x1008` power/sleep | uint8 value after the target and operation. Conflicting semantic interpretations below; exclude from the v1 power abstraction. |

Evidence: [PL103 commands][zy-gatt], [G60 JS builders][cli-protocol],
[JS response parsing][cli-commands], [SDK payload builders][sdk-protocol].
Brightness/CCT responses use target + operation + value; value begins at
payload offset 3. Reject nonfinite or out-of-range decoded values before updating
state. Clamp or reject writes at the public API boundary consistently.

| Profile | Functional target | Write operation | Evidence / limit |
| --- | --- | --- | --- |
| PL103 CLI / current project | `0x0000` | `0x01` | PL103 source explicitly distinguishes device ID from advertisement manufacturer/model ID `0x0905`. Parent's G60 validates ID and readback on this path. |
| G60 macOS JS CLI | `0x8003` | `0x01` | Explicit G60 README default; encoders preserve the full raw value. Source names its high bit `responseFlag`, but that interpretation is not independently verified here. |
| Swift / HA | Hardcoded bytes `03 80` | `0x01` | Equivalent raw uint16 `0x8003`; sources call this a subcommand. Mainly PL105/X100 provenance. |
| G60 Python SDK field report | Object `1` | `0x33` | Author reports physical control after provisioning/config. SDK defaults to `0x33` and keeps `0x01` as legacy. |

See [PL103 target selection][zy-target], [G60 CLI notes][cli-readme],
[HA encoders][ha-protocol], [SDK operation defaults][sdk-protocol], and
[SDK G60 control report][sdk-hardware]. Do not replace current project ID `0`
with `0x0905` or `0x8003` merely because another implementation uses it.

There is a direct research disagreement: monneyboi's notes say app config
`controlMode: 0x33` becomes boolean `1` in JNI; aaditagrawal's notes say it is
transmitted as operation byte `0x33`. The latter supplies a reported physical
G60 scene; the former reports hardware verification of operation `1`.
The checked-in original APK/native disassembly/transcripts are insufficient to
independently resolve every firmware/state variation. Keep operation/target
configurable internally, but use the project's device-specific contract.
[PL103 analysis][zy-notes], [SDK protocol analysis][sdk-doc-protocol]

## Exact offline packet vectors

All examples below use request type `0x0100` and sequence **1 individually**.
They are calculated codec vectors, **not newly captured hardware traffic**.
The actual live captures are in [api-contract.md](../api-contract.md).

```text
device ID, empty query:
24 3c 06 00 00 01 01 00 05 20 72 07

target=0, op=1, brightness=50.0:
24 3c 0d 00 00 01 01 00 01 10 00 00 01 00 00 48 42 19 4b

target=0, op=1, CCT=2700:
24 3c 0b 00 00 01 01 00 02 10 00 00 01 8c 0a 8e 36

target=0, op=1, CCT=6500:
24 3c 0b 00 00 01 01 00 02 10 00 00 01 64 19 c7 8d

target=0x8003, op=1, brightness=50.0 (G60 JS / HA):
24 3c 0d 00 00 01 01 00 01 10 03 80 01 00 00 48 42 bb 47

object=1, op=0x33, brightness=50.0 (provisioned SDK profile):
24 3c 0d 00 00 01 01 00 01 10 01 00 33 00 00 48 42 15 bb
```

[offline-vectors.json](../../vendor/protocol-research/offline-vectors.json)
contains 27 examples across these three profiles, including reads, endpoints,
and raw `0x1008` values. The [verification script](../../vendor/protocol-research/verify_vectors.py)
checks all frames against four upstream pure codecs and independently against
stdlib `binascii`, plus the CRC check string and rejection of a corrupt CRC.
It extracts only pure functions from the BlueZ implementation, without importing
D-Bus/GLib or any transport. Commands actually run:

```sh
uv run --no-project python vendor/protocol-research/verify_vectors.py
# PASS: 27 frames match four upstream codecs and stdlib CRC.

cd vendor/protocol-research/zhiyun-cli
bun test ./test/protocol.test.js
# 6 pass, 0 fail; no dependency installation or hardware access.
```

This validates encoding consistency, not device behavior or provisioning crypto.

## Initialization, replies, and pitfalls in existing code

- **No universal startup handshake established.** Swift sends `2005`, `2003`,
  `8001`, `0006` with `03 80 00 00`, `1009` with that same payload, then a CCT
  read, at roughly 100 ms intervals. HA omits the final CCT query. Both describe
  parts of this sequence as necessary, but the PL103 CLI and G60 JS gateway
  connect/subscribe and issue commands without that sequence. Do not impose
  X100 startup requirements on the live G60. [Swift init][swift-init],
  [HA init][ha-client], [G60 connection path][cli-gateway]
- **Command names disagree.** HA calls `0x1009` a brightness query, whereas the
  Python SDK calls it `MAX_MODE`. Query brightness with `0x1001`, operation `0`,
  which is supported by PL103 code and current live evidence. `0x0006` is called
  read-state by Swift/HA but register-default-group by the SDK; the latter packs
  two uint16 values. Avoid sending it merely as a harmless status query.
  [HA enums][ha-protocol], [SDK enums/register][sdk-protocol]
- **Power is disputed.** JS/HA use `0x1008` value `1` as enabled and `0` as off;
  the SDK's reported lit scene starts with `sleep=0`. Preserve raw values for
  research; use brightness-based power for this project's API. [JS power][cli-commands],
  [G60 scene report][sdk-ble]
- **HA/Swift power decoding appears inconsistent with their own payload shape.**
  HA returns `payload[2] == 1`, reading the operation byte; JS reads the actual
  power value at `payload[3]` after requiring four bytes. Do not transplant
  that HA decoder without confirmation. [HA decoder][ha-protocol],
  [JS decoder][cli-commands]
- **Write success and physical success differ.** HA writes without ATT response
  and optimistically updates state. JS chooses a supported write mode and
  prefers write with response; the parent confirmed that property locally.
  An ATT write response only confirms transport acceptance. Poll brightness/CCT
  afterward and report a control error when state cannot be verified, as the
  shared contract requires. [HA writes][ha-client], [JS transport][cli-ble]
- **Do not reuse the Swift receive parser as-is.** It checks magic but does not
  verify declared length or CRC. Its minimum-size guard is 10 despite the full
  empty frame being 12 bytes. The JS decoder has a similar minimum guard before
  later field reads. Use bounded streaming parsing and strict structural checks.
  [Swift receive parser][swift-receive], [JS parser][cli-protocol]

## Provisioning, authentication, and mesh evidence

Runtime FEE9 packets contain no application token, login challenge, or encryption
wrapper in the inspected clients. This describes the **application protocol**;
it does not prove all devices' link-security settings or fresh-device setup.
The parent reports no pairing or mesh keys needed for current G60 readback.

The two repositories with mesh implementations separate these channels:

| Channel | Service | Write / notify |
| --- | --- | --- |
| PB-GATT provisioning | `00001827-0000-1000-8000-00805f9b34fb` | `00002adb-0000-1000-8000-00805f9b34fb` / `00002adc-0000-1000-8000-00805f9b34fb` |
| Mesh Proxy configuration | `00001828-0000-1000-8000-00805f9b34fb` | `00002add-0000-1000-8000-00805f9b34fb` / `00002ade-0000-1000-8000-00805f9b34fb` |
| Zhiyun runtime | FEE9 above | custom `…9600` / `…9601` above |

[SDK UUID constants][sdk-transport] and [G60 field notes][sdk-ble] support this
distinction. Do not send `$<` runtime frames to `1827`, and do not interpret
that service's timeout as proof that FEE9 cannot control the light.

The MIT SDK contains a concrete No-OOB provisioning path:

1. PB-GATT first byte is `(SAR << 6) | messageType`; complete provisioning
   uses `03`. Invite with attention 5 is `03 00 05`; reported G60 capabilities
   are `03 01 01 00 01 00 01 00 00 00 00 00 00`. The source decodes one element,
   P-256, static-OOB capability, and no input/output OOB. Static-OOB capability
   does not mean this implementation selects static OOB. [Builders][sdk-mesh-builders],
   [reported exchange][sdk-ble]
2. No-OOB Start is `03 02 00 00 00 00 00`. Public key is `03 03` + 64-byte
   P-256 X||Y, with each coordinate big-endian. AuthValue is sixteen zeros.
   Confirmation/random/session derivation occurs in one connection; the author
   reports replay across sessions failing with `03 09 04` (confirmation failed).
   [Mesh code][sdk-mesh-builders], [ECDH and derivation][sdk-mesh-crypto], [field notes][sdk-ble]
3. Confirmations use AES-CMAC. Session derivation uses labels `prck`, `prsk`,
   `prsn`, `prdk`; the nonce is 13 bytes. Provisioning plaintext is NetKey(16),
   key index(2 BE), flags(1), IV index(4 BE), unicast address(2 BE). Encrypt
   using AES-CCM with an 8-byte MIC; send `03 07` + ciphertext/MIC. Completion
   is `03 08`. These are mesh fields, so the runtime little-endian rule does
   not apply to them. [Crypto implementation][sdk-mesh-crypto], [data PDU][sdk-mesh-builders]
4. SDK post-provisioning config access payloads are `80 08 ff` (composition),
   `80 0c` (TTL get), `80 24 0a` (network transmit), and `00` + packed key
   indexes + AppKey (AppKey Add). They require encrypted mesh Network PDUs
   over `1828`, not raw GATT writes of those access payloads. The implementation
   includes segmented AppKey Add generation. [Config builders][sdk-mesh-config]

The older PL103 repository instead delegates provisioning and crypto to BlueZ
meshd via D-Bus: create/attach network, scan unprovisioned nodes, No-OOB AddNode,
get composition, create/distribute AppKey index 0, then vendor ModelAppBind
`0x803d`. It reports company `0x0059`, vendor model `0x0002`. Its notes claim
mesh is unnecessary for day-to-day control, but also describe provisioning as
an earlier completed phase. That does **not** establish factory-reset behavior.
[Mesh code][zy-mesh], [PL103 findings][zy-notes]

The SDK preserves app-derived provisioner UUID
`9ee44bef-29fc-41e8-9e53-ee567a2118df`, provisioner address `0x0001`, group
`0xc000`, and default light address `0x0005`. These are implementation defaults,
not the local light's measured runtime ID. Its constant named provisioner device
key is not a universal light authentication key: the actual light DeviceKey is
derived from the provisioning session. [Constants][sdk-mesh], [derivation][sdk-mesh-crypto]

For Lighttime, implement the live-confirmed FEE9 route first. Keep mesh setup
outside normal connect/control, preserve existing device state, and do not
perform provisioning or reset as a retry. Make readback verification, serialized
writes, command/sequence matching, and truthful transport-specific errors common
to Python, TypeScript, browser/OBS, and Expo Android implementations.

[official-download]: https://zyserver1.zhiyun-tech.com/en/download/software/757/1/show?page=second_nav&source=download&type=website
[official-manual]: https://zhiyun-website-shenzhen.oss-cn-shenzhen.aliyuncs.com/instruction/20260723/1784775325362-WAMQYeP8.pdf#page=28
[zy-root]: https://github.com/monneyboi/zyvega/tree/b19f0e67bdb33f7d4a29ee850bb5fe8264b868d8
[zy-readme]: https://github.com/monneyboi/zyvega/blob/b19f0e67bdb33f7d4a29ee850bb5fe8264b868d8/README.md
[zy-notes]: https://github.com/monneyboi/zyvega/blob/b19f0e67bdb33f7d4a29ee850bb5fe8264b868d8/CLAUDE.md#L96-L242
[zy-gatt]: https://github.com/monneyboi/zyvega/blob/b19f0e67bdb33f7d4a29ee850bb5fe8264b868d8/gatt.py#L15-L187
[zy-target]: https://github.com/monneyboi/zyvega/blob/b19f0e67bdb33f7d4a29ee850bb5fe8264b868d8/gatt.py#L554-L597
[zy-mesh]: https://github.com/monneyboi/zyvega/blob/b19f0e67bdb33f7d4a29ee850bb5fe8264b868d8/zyvega.py#L183-L209
[cli-root]: https://github.com/robinebers/zhiyun-cli/tree/6133a30832c800684e11862e27cfa072f814e1d1
[cli-readme]: https://github.com/robinebers/zhiyun-cli/blob/6133a30832c800684e11862e27cfa072f814e1d1/README.md#L1-L32
[cli-package]: https://github.com/robinebers/zhiyun-cli/blob/6133a30832c800684e11862e27cfa072f814e1d1/package.json
[cli-protocol]: https://github.com/robinebers/zhiyun-cli/blob/6133a30832c800684e11862e27cfa072f814e1d1/src/protocol.js#L3-L193
[cli-test]: https://github.com/robinebers/zhiyun-cli/blob/6133a30832c800684e11862e27cfa072f814e1d1/test/protocol.test.js#L16-L64
[cli-commands]: https://github.com/robinebers/zhiyun-cli/blob/6133a30832c800684e11862e27cfa072f814e1d1/src/commands.js#L121-L171
[cli-ble]: https://github.com/robinebers/zhiyun-cli/blob/6133a30832c800684e11862e27cfa072f814e1d1/src/ble.js#L113-L144
[cli-gateway]: https://github.com/robinebers/zhiyun-cli/blob/6133a30832c800684e11862e27cfa072f814e1d1/src/gateway.js#L114-L190
[swift-root]: https://github.com/makerwolf/Light-Bridge/tree/24b502cc4b6f7602ed98349adb7da17b89de84c2
[swift-license]: https://github.com/makerwolf/Light-Bridge/blob/24b502cc4b6f7602ed98349adb7da17b89de84c2/README.md#L132-L134
[swift-protocol]: https://github.com/makerwolf/Light-Bridge/blob/24b502cc4b6f7602ed98349adb7da17b89de84c2/Light%20Bridge/Managers/BluetoothDeviceManager.swift#L5-L53
[swift-builder]: https://github.com/makerwolf/Light-Bridge/blob/24b502cc4b6f7602ed98349adb7da17b89de84c2/Light%20Bridge/Managers/BluetoothDeviceManager.swift#L353-L405
[swift-init]: https://github.com/makerwolf/Light-Bridge/blob/24b502cc4b6f7602ed98349adb7da17b89de84c2/Light%20Bridge/Managers/BluetoothDeviceManager.swift#L1015-L1069
[swift-receive]: https://github.com/makerwolf/Light-Bridge/blob/24b502cc4b6f7602ed98349adb7da17b89de84c2/Light%20Bridge/Managers/BluetoothDeviceManager.swift#L657-L709
[ha-root]: https://github.com/didim99/HA-Zhiyun-Light/tree/7ca51ea6e172e2f45d8b78ae09e09b12f226cbad
[ha-license]: https://github.com/didim99/HA-Zhiyun-Light/blob/7ca51ea6e172e2f45d8b78ae09e09b12f226cbad/LICENSE
[ha-protocol]: https://github.com/didim99/HA-Zhiyun-Light/blob/7ca51ea6e172e2f45d8b78ae09e09b12f226cbad/custom_components/zhiyun_ble/protocol.py#L24-L202
[ha-client]: https://github.com/didim99/HA-Zhiyun-Light/blob/7ca51ea6e172e2f45d8b78ae09e09b12f226cbad/custom_components/zhiyun_ble/zhiyun_ble.py#L197-L295
[sdk-root]: https://github.com/aaditagrawal/zhiyun-light-control/tree/00748ac38d24f86cc1d4a02b4ad135f503f16889
[sdk-license]: https://github.com/aaditagrawal/zhiyun-light-control/blob/00748ac38d24f86cc1d4a02b4ad135f503f16889/LICENSE
[sdk-ble]: https://github.com/aaditagrawal/zhiyun-light-control/blob/00748ac38d24f86cc1d4a02b4ad135f503f16889/docs/ble.md#L157-L334
[sdk-profiles]: https://github.com/aaditagrawal/zhiyun-light-control/blob/00748ac38d24f86cc1d4a02b4ad135f503f16889/docs/ble.md#L7-L39
[sdk-hardware]: https://github.com/aaditagrawal/zhiyun-light-control/blob/00748ac38d24f86cc1d4a02b4ad135f503f16889/docs/hardware-notes.md#L221-L241
[sdk-doc-protocol]: https://github.com/aaditagrawal/zhiyun-light-control/blob/00748ac38d24f86cc1d4a02b4ad135f503f16889/docs/protocol.md#L30-L70
[sdk-protocol]: https://github.com/aaditagrawal/zhiyun-light-control/blob/00748ac38d24f86cc1d4a02b4ad135f503f16889/src/zhiyun_light_control/protocol.py#L11-L291
[sdk-transport]: https://github.com/aaditagrawal/zhiyun-light-control/blob/00748ac38d24f86cc1d4a02b4ad135f503f16889/src/zhiyun_light_control/transports/ble.py#L21-L103
[sdk-mesh]: https://github.com/aaditagrawal/zhiyun-light-control/blob/00748ac38d24f86cc1d4a02b4ad135f503f16889/src/zhiyun_light_control/mesh.py#L9-L47
[sdk-mesh-builders]: https://github.com/aaditagrawal/zhiyun-light-control/blob/00748ac38d24f86cc1d4a02b4ad135f503f16889/src/zhiyun_light_control/mesh.py#L372-L513
[sdk-mesh-crypto]: https://github.com/aaditagrawal/zhiyun-light-control/blob/00748ac38d24f86cc1d4a02b4ad135f503f16889/src/zhiyun_light_control/mesh.py#L757-L905
[sdk-mesh-config]: https://github.com/aaditagrawal/zhiyun-light-control/blob/00748ac38d24f86cc1d4a02b4ad135f503f16889/src/zhiyun_light_control/mesh.py#L615-L700
