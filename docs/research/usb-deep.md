# G60 USB routing and verified hybrid control

Research date: 2026-10-23. Researcher: `lighttime-90` (static analysis only). No device, serial,
Bluetooth, camera, firmware, or vendor-application execution by this researcher; the parent owns
physical tests. Live evidence below is attributed to the parent or to the user's MIT SDK notes.
The parent subsequently ran the bounded identity/read/write probes and a
60-case USB-write/Bluetooth-readback sequence. The implementation is documented
in [the hybrid USB guide](../usb.md). No firmware was changed.

## Current conclusion

- **Evidenced over USB:** CDC-ACM `fff8:0180` at 115200 8N1 carries the same ZYBL frame as BLE.
  Identity reads work: `0x2003`, `0x2005`, `0x2001`, `0x8001` addressed `00 01`; updater identity
  `0x1300` chipSync and `0x1302` readSn addressed `03 01`. The firmware-update family
  (`0x8002`–`0x8004`, `0x7003`/`0x7004`, `0x13xx` writes) is reachable but out of scope.
- **Verified USB output writes:** the BLE-identical `00 01` route applies absolute
  brightness float32 and CCT u16 setters. A single brightness write changed 7%
  to 8%; a single CCT write changed 6100 K to 6200 K. Independent BLE reads
  confirmed each. Sixty further single/combined write cases all matched BLE
  readback, and baseline 7% / 6100 K was restored.
- **USB state reads remain unanswered.** No official client sends lighting
  commands over USB. The new target `0x8003` read probes also received no reply
  on either the `00 01` or `03 01` route. Earlier target-0 and upstream matrix
  reads remain negative evidence for the tested routes, not proof that every
  possible firmware command was exhausted.
- **Echoes do not establish control:** isolated `01 03` writes echoed exactly
  but did not change BLE readback. The updater's `03 01` float brightness write
  requested 10% and produced 0%; its CCT setter changed temperature. Its
  brightness payload semantics remain unresolved and it is excluded from the
  public runtime writer.
- **Implementation:** `UsbBleTransport` matches USB/BLE identity, sends only
  the two allowlisted absolute setters over `00 01`, and delegates reads to BLE.
  `--usb-writes PORT` opts the SDK/bridge into `usb+ble` state. This is verified
  hybrid control, not fully USB-only readback.

Parent evidence remains local under `artifacts/usb-deep/`: `read-probe.log`,
`write-probe.log`, corresponding timestamped JSON captures, and
`runtime-verified.json`. The preliminary direct serial-write/HTTP-readback
experiment measured about 65–67 ms median round trips. Integrated bridge
comparisons are recorded separately in [the latency report](../latency.md).

## Header, body, handshake and gating differences (evidenced)

Bytes 4–5 are a `[dst, src]` node-address pair, not an opaque "type 0x0100". The official updater
writes them little-endian from a u16 (`Zyled_link::transmit` `0x100028630`). Its parser recombines a
reply's bytes 4–5 big-endian (`Zyled_link::parser` `0x100028ee0`). `matchRequestAndResponse`
(`0x100029430`) accepts a reply only if that value equals the request's and the command matches; the
sequence number is ignored. A valid reply therefore swaps the two bytes, as the captures show:
`00 01` → `01 00`.

| Aspect | Our serial INFO/ID (works) | Official USB updater | Official BLE app |
| --- | --- | --- | --- |
| Bytes 4–5 | `00 01` → reply `01 00` | `03 01` → requires reply `01 03` | `00 01` (incl. its BLE light OTA) |
| Commands | `0x2003`, `0x2005` | `0x1300` on Open; `0x1302`; DFU set | `0x1001`/`0x1002`/`0x2xxx`/`0x8001`; OTA `0x8002`–`0x8004` |
| Payload | empty | empty for identity commands | functional: `target u16, op u8, value` |
| Sequence | counter | per-object counter; matcher ignores it | shared counter |
| Handshake | none | Open → `chipSync` ≤10× (20 ms) → coreid match | none required for reads |
| Line state | DTR/RTS low | never set explicitly (OS default on open) | n/a |

- **Handshake recovered:** `MainWindow::on_openportButton_clicked` (`0x100039570`) opens the port and
  calls `setMainState(1)`. `processMain` (`0x100039f20`) then runs `zyled_main_thread`
  (`0x10003a030`), which sends `Zyled_dfu::chipSync(0x0103, 20 ms)` (`0x10002ad50`, cmd `0x1300`,
  empty payload) up to 10 times.
- **Reply decoding:** `zyled_repon::syncResult` (`0x100027aa0`) needs ≥20 payload bytes: status
  u8, cpuid u32, pagesize u16, pagenum u16, bl u16, **coreid u16 at payload 11–12**, fw/res u16×2,
  sn u32. Coreid is matched against the table built at `0x10003bb50`: **G60/PL103 `0x0541`**,
  X100/PL105 `0x0542`, G100/PL107 `0x0543`. The PTZ firmware header `ARCH` is also `0x0541`.
- **No other handshake was found.** There is no session, authentication, unlock, or "USB control
  mode" command in either official client. The SDK found baud 9600/115200 and every DTR/RTS
  combination irrelevant to replies.
- **Native gating:**
  - *Android (Vega 1.1.7):* no USB-host feature, USB intent filter, or device-filter XML. Its
    `zyusb`/`ZYBaseLink` stack is UDP (`SendBuffer(…, sockaddr_in const&, …)` `0x11cf64`) for
    gimbal/camera sinks, so there is no USB-serial light client to imitate. `ZYLightPacket`
    (`0x18f5ac`) hardcodes `0x0100`. This build never parses the config's `controlMode`.
  - *Updater:* gating is product identity only (coreid/ARCH `0x0541`). The PTZ element is sent
    unchanged in 256-byte `chipWrite` chunks (`download_thread` `0x10002cd10`), so the host does no
    decryption. Device-side command routing cannot be read statically.
- **Official updater command IDs** (`Zyled_dfu`; every call uses `03 01`):
  - **Identity:** `0x1300` chipSync, `0x1302` readsn.
  - **Writes:** `0x1301` writesn, `0x1303` parameter_save, `0x7003` chipWrite.
  - **DFU flow:** `0x7004` chipCheck, `0x8002` dfu_isok, `0x8003` dfu_start, `0x8004` chipRun.
  - The legacy `zyled::transmit` (`0x100013c30`) hardcodes the body prefix `03 01 13 14`.

## Prior live evidence relevant to USB

- **Parent (Linux, 2026-10-23):** `00 01` `0x2003`/`0x2005` replied with `01 00`. `00 01`
  brightness/CCT reads (target 0, op 0) got no reply within 2 s. No USB writes were sent.
  See `artifacts/local/serial-read-probe.json`.
- **User's MIT SDK, G60 firmware 1.6.4 (macOS), `vendor/protocol-research/zhiyun-light-control/docs/hardware-notes.md` lines 28–79, 237–241 and `docs/protocol.md` lines 114–133:**
  - `03 01` chipSync and readSn were verified; readSn decodes to product `0x0541` and identifier
    `08a409e0c1100113`.
  - Object reads addressed `00 01`, `01 01`, `03 01` and `01 03` got no reply for targets
    `0`, `1`, `2`, `100`, `0x8001`, `0x8064` and `0xffff`; only `01 03` frames came back as exact
    echoes. Target `0x8003` was **not** tested.
  - Writes addressed `01 03` were echoed only. Physically: a sleep frame blinked the light, a
    matrix over targets 0/1/2 reached 2700 K at 20%, and a target-1-only scene did not visibly
    change. The minimal effective frame was never isolated.
  - `0x0006` register ACKs and changes the reported device ID (restored to 0 afterwards); this is
    a state change, so it is excluded below.

## Bounded probe plan

Run with `uv run python artifacts/usb-deep/tools/usb_probe.py --tiers 1,2` (`--dtr-rts` optional).
Tier 3 needs `--tiers 3 --allow-state-change --bridge http://127.0.0.1:8877`, which reads BLE state
after each single frame and restores the captured baseline. The runner refuses any command outside
`{0x1300, 0x1302, 0x8001, 0x1001, 0x1002}` and any address pair outside `{00 01, 03 01, 01 03}`.
Vectors come from `artifacts/usb-deep/tools/frames.py`, which first rebuilds the four captured TX
frames byte-exact. Regenerate tier 3 for a different baseline with `frames.py <pct> <kelvin>`.

- **P0 (host-only):** `lsusb -v -d fff8:0180`, `udevadm info -n /dev/ttyACM0`,
  `lsblk -o NAME,SIZE,TRAN,VENDOR,MODEL`. This records the interface layout, including whether a
  mass-storage interface belongs to the lamp. Do not mount or write.
- **P1, identity (official-equivalent, read-only):**
  - `243c06000301210000131907`: chipSync → node 3. Expect reply bytes `01 03`, ≥20-byte payload,
    and payload bytes 11–12 = `41 05`.
  - `243c0600030122000213a7fa`: readSn → node 3.
  - `243c06000001230001807aa4`: `0x8001` → node 0.
  - `243c0600030124000180b73b`: `0x8001` → node 3. Does node 3 report a different version?
- **P2, untested reads (op 0, zero value, target `0x8003`):**
  - `243c0d000001250001100380000000000034fa`: brightness, addressed `00 01`.
  - `243c0b000001260002100380000000bcdb`: CCT, addressed `00 01`.
  - `243c0d00030127000110038000000000002295`: brightness, addressed `03 01`.
  - `243c0b00030128000210038000000031d0`: CCT, addressed `03 01`.
- **P3, controlled writes (baseline 5% / 5000 K; target 0, op 1):** each frame uses a distinct
  value, so BLE readback identifies exactly which frame, if any, took effect.
  - `243c0d000001290001100000010000c0409ef1`: 6%, addressed `00 01` (identical to BLE).
  - `243c0d0001032a0001100000010000e0400bcc`: 7%, addressed `01 03` (SDK echo route).
  - `243c0d0003012b00011000000100000041fd98`: 8%, addressed `03 01` (updater route).
  - `243c0b0000012c000210000001ec137347`, `243c0b0001032d00021000000150141481`,
    `243c0b0003012e000210000001b4141f0e`: 5100 / 5200 / 5300 K, same three routes.

| Outcome | Conclusion |
| --- | --- |
| P1 chipSync reply `01 03` with coreid `0x0541` | address model confirmed on Linux |
| Any P2 reply with valid CRC | a USB readback path exists, so full USB control becomes plausible |
| BLE readback after one P3 frame equals that frame's value | write-only USB control on that route |
| No P2 reply and no P3 change | USB is identity/DFU only on firmware 1.6.4; mark USB control unsupported |

**Never send** (also enforced by the runner):

- writes: `0x1301`, `0x1303`;
- file transfer and chip write: `0x7001`, `0x7003`, `0x7004`, `0x7011`, `0x7013`, `0x7014`;
- update flow: `0x8002`, `0x8003`, `0x8004`;
- registration/group: `0x0001`–`0x0006`;
- sleep: `0x1008`;
- legacy head-byte bypass/run/reboot frames.

## What would falsify this report

- A P3 frame changing BLE-read state (falsifies "no USB control") or a P2 reply (falsifies "no USB
  readback").
- A chipSync reply without swapped address bytes or with coreid ≠ `0x0541` (falsifies the address
  model or product mapping).
- A capture of an official tool sending lighting commands over USB, or a firmware release note
  adding USB control. None was found in the inspected official artifacts.

## Provenance, artifacts and limits

- Mac updater `ZhiyunGimbalTools` SHA-256 `2de730dbf347a7d30095eb1d3db9948ea98ca7bfb1fbbbc5f490f0c33c9bde19`.
- Android `libzylink.so` (arm64) SHA-256 `79e690928aa830689f6969e2f1c1587610d647918fb14534f4e448de7bae873a`.
- Disassembly excerpts and tools are under ignored `artifacts/usb-deep/` (`_out/`, `tools/`,
  `probe-vectors.json`, `INTERMEDIATE-probe.md`).
- Not yet examined: the official Windows "Zhiyun Led Tools V1.01" build
  (`upgrade/20230215/1676434227243-pkt7tPYm.zip`), which the downloads page lists but which was
  never downloaded.
- Firmware payloads are opaque on the host side, so device-side dispatch remains unverified until
  P2/P3 run.
