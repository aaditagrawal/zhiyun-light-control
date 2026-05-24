# Hardware Notes

These notes preserve observed behavior from the local MOLUS G60 bench setup.
They are useful evidence, not a compatibility guarantee for every firmware or
transport route.

## Verified Target

- Verified target: Zhiyun MOLUS G60 on firmware `1.6.4`.
- Verified path: USB probe, firmware/status/device-id reads, registration, and
  updater identity reads.
- Experimental path: object-scoped control writes and BLE command routing.
- Firmware flashing is intentionally not implemented. Use Zhiyun's official
  updater for firmware writes.

On macOS, the light should usually stay on its PD power supply while the Mac is
connected with USB-C for data. The G60 presents as a USB CDC serial device.

## USB Descriptor

For the attached G60, macOS IOKit metadata identified `Zhiyun Virtual ComPort`
at `VID 0xfff8`, `PID 0x0180`.

## Object Read And Control Discovery

Object reads still need more live validation. On the upgraded G60, registration
ACKs over USB, but object reads tested for object ids `0`, `1`, `2`, `100`,
`0x8001`, `0x8064`, and `0xffff` did not respond.

The `zlight discover-usb` matrix also tested first-word values `0x0100`,
`0x0101`, `0x0103`, and `0x0301`; only `0x0301` produced exact echoes for
object read and control probes, not device ACKs.

Optional runtime control candidates for sleep, brightness, CCT, and
brightness-plus-mode timed out over USB with both the Vega `controlMode`
operation byte `0x33` and the legacy operation byte `0x01`. A later narrowed
matrix confirmed `register_default_group_dev0_group0` and
`register_default_group_dev1_group0`, then `set_sleep_obj1_mode0x33` still timed
out.

After registering device id `1`, a probe reported `device_id: 1`; registering
device id `0` restored the original reported id.

A later sleep-only matrix tested control first words `0x0001`, `0x0100`,
`0x0000`, `0x0101`, and `0x0301` for object ids `0` and `1`. `0x0301` still
returned exact write echoes, while the other first words timed out; none were
ACK-confirmed object control.

On 2026-05-24, a narrowed USB sleep probe using first word `0x0301`, object id
`1`, and op bytes `0x33`/`0x01` physically blinked the attached G60 while the
transport result remained `echoed_write`. A follow-up warm scene command
(`sleep=0`, `brightness=50`, `kelvin=3200`) returned `echoed_write` for all
three frames but did not visibly change the light.

A broader state-changing matrix using first word `0x0301`, object ids `0`, `1`,
and `2`, op bytes `0x33` and `0x01`, and target values `brightness=20` /
`kelvin=2700` was physically observed to reach `2700K` at `20%`. The matrix
still produced only `echoed_write` for object-control candidates, so the exact
minimal write route remains unconfirmed. For now, repeat brightness/CCT writes
across the responsive `0x0301` object/mode candidates when a stable setting is
more important than identifying the smallest route.

A post-registration read pass then registered device id `1` to group `0` and
immediately retried all object-read candidates for object id `1`; registration
ACKed, but all nine post-register object reads still returned
`sent_no_response`. Device id `0` was re-registered afterward and verified by
status.

The official Vega Android package includes `base/assets/pl103/1.6.4.config`.
For PL103 it lists optional control commands `0x1001`, `0x1002`, `0x1008`,
`0x1101`, `0x1201`, and `0x1202` with `controlMode: "0x33"`, plus CCT range
`2700..6500`. Disassembly of Vega's `libzylink.so` shows that `controlMode` is
the `u8 op` field inside the functional payload, not the frame first word.
Accordingly, library writes default to op `0x33`; object reads still use op
`0x00`, and `control_mode=0x01` is available for reproducing legacy probes.

That is protocol evidence for the G60 feature set, but the current USB route
has not produced ACK-confirmed object control on the attached light.

On the attached G60, live HTTP bridge checks confirmed `/probe` and `/register`
while `/sleep` and `/brightness` returned `sent_no_response`.

## Discovery Tooling Notes

`discover-usb --allow-control` can separately vary control object ids and
control frame first words with `--control-object-ids` and
`--control-first-words`. This keeps broad read discovery separate from the
smaller write matrix needed to investigate object-control routing.

It can also vary the registration prelude with `--register-device-ids` and
`--register-group-ids`, and restrict state-changing probes with
`--control-kinds sleep,brightness,cct,brightness-with-mode`. `--control-modes`
defaults to `0x33,0x01` so one run compares the official Vega write operation
byte with the older operation byte.

`--post-register-reads` re-runs object read candidates after each
register-default-group attempt, which tests the hypothesis that registration
unlocks object-scoped reads. Because alternate registration ids are visible in
later probes, re-register the intended id after experiments.

Discovery reports include `summary.status_counts`, `confirmed_names`,
`echoed_write_names`, `summary.control`, and `summary.post_register_reads` so
automated setup tools can distinguish confirmed control from transport echoes
without parsing every attempt.

## BLE Field Notes

Native bundled CoreBluetooth inspection with an
`NSBluetoothAlwaysUsageDescription` plist found service `FEE9` plus mesh service
`1827` on the local G60. The compiled Swift helper app reaches macOS Bluetooth
authorization `allowed`; the older script-launch helper could remain
`not_determined`/`unauthorized` without appearing in Settings. On the local
G60, `FEE9` ACKs read-only global identity/status frames, while `1827` times out
for the same `DEVICE_INFO` probe. Object reads, register, and
brightness/CCT/sleep writes still time out on the confirmed `FEE9` endpoint, so
BLE control routing remains unidentified.

Reverse engineering of the official Vega Android bundle then showed that
`1827` is not a Zhiyun runtime-frame channel; it is the Bluetooth Mesh
Provisioning bearer. Sending a PB-GATT wrapped provisioning invite (`030005`) to
`1827/2ADB` produced a valid capabilities response from the attached G60:
`03010100010001000000000000`. Decoded: one element, FIPS P-256 ECDH, no public
key OOB, static OOB supported, and no input/output OOB. This confirms the light is in
the unprovisioned mesh stage and that proper control requires completing the
mesh provisioning / proxy / app-key pipeline before native light commands can be
expected to work.

The SDK now has a `mesh-handshake` probe that sends invite, no-OOB provisioning
start (`03020000000000`), and a generated provisioner public key in one
CoreBluetooth connection. After rebuilding the macOS helper, Bluetooth
authorization returned to `not_determined`, so this probe could not be
re-validated until `ZhiyunBleScan` is allowed again in macOS Privacy & Security
> Bluetooth.

The post-public-key provisioning crypto is implemented in the SDK and verified
with unit tests against the Nordic/Zhiyun flow: confirmation inputs,
confirmation key, provisioner confirmation/random, provisioning salt, session
key, session nonce, device key, and encrypted provisioning data. This is still
setup work; the light output path remains unverified until provisioning,
`1828` proxy reconnect, app-key setup, and Zhiyun native registration are
completed on hardware.

A live replay probe confirmed the provisionee public key changes between
sessions. Reusing a confirmation frame computed from a previous public key
produced provisioning failure `030904`, decoded as `confirmation_failed`. Future
provisioning must therefore compute and send confirmation/random inside one
continuous BLE provisioning session.

The SDK now includes a dynamic `mesh-session` probe for that continuous BLE
session. On 2026-05-24, after the helper was rebuilt for IPC support, macOS
reported `ZhiyunBleScan` Bluetooth authorization as `not_determined` and the
hardware run failed before receiving capabilities with helper error
`no matching BLE device found`. Re-authorize the helper before treating
`mesh-session` hardware results as protocol evidence.

A later broad helper scan with `scan-ble --backend macos-app --include-all`
returned zero advertisements, not just zero Zhiyun-filtered devices. macOS
Bluetooth itself was on according to `system_profiler SPBluetoothDataType`, and
the Python/Bleak worker still aborted with `SIGABRT`. This leaves the current
BLE blocker at the host-helper/fixture-advertising layer, before protocol
frames can be evaluated.

Read-only probes of PL103 required command IDs `0x0001`, `0x0003`, `0x0004`,
`0x0005`, and `0x2004` with empty payloads over the acknowledged USB runtime
frame path timed out. The only required low-numbered command currently ACKing
over USB is still `0x0006` register-default-group.

Direct Swift/Python processes without an app bundle were killed by macOS TCC
before scan results were returned, which matches the bleak worker `SIGABRT`
diagnostics. Use `zlight ble-helper --ensure --open-settings` to build the
cached helper and open the Bluetooth privacy settings for the exact bundle id
used by scans.

On 2026-05-24, the helper builder was updated to ad-hoc sign the full `.app`
bundle after compiling the Swift binary. Before signing, `codesign` reported
the executable identifier as `ZhiyunBleScan` and `Info.plist=not bound`; after
signing it reported `Identifier=local.zhiyun-light-control.ble-scan` with bound
Info.plist entries. System logs then showed `bluetoothd` registering the central
session for `local.zhiyun-light-control.ble-scan` and `tccd` prompting for
`kTCCServiceBluetoothAlways`. If status remains `not_determined`, the next
action is to accept that Bluetooth prompt or allow `ZhiyunBleScan` in Privacy &
Security > Bluetooth.

A dedicated `zlight ble-helper --ensure --authorize --timeout 60 --json` mode
now brings the helper app forward and keeps it alive during the Bluetooth
permission request. In the current local run, System Settings showed
`ZhiyunBleScan` listed and toggled on, but CoreBluetooth still returned
`authorization=not_determined` and system logs continued to show stale code
requirement mismatches for `kTCCServiceBluetoothAlways`. `tccutil reset
Bluetooth local.zhiyun-light-control.ble-scan` also failed, so the remaining
permission repair is a macOS TCC state issue rather than a missing BLE endpoint
implementation.

The helper now signs the app with an explicit designated requirement
`identifier "local.zhiyun-light-control.ble-scan"` even when using ad-hoc
signing, so TCC has a stable requirement rather than only a changing code hash.
The scan helper also reports a Bluetooth state error when CoreBluetooth never
reaches `poweredOn`; earlier broad scans could report `ok=true` with zero
devices even while authorization was still stuck at `not_determined`.

Current local BLE scan validation:

| Runtime | BLE stack | Result |
| --- | --- | --- |
| Python 3.13 | `bleak 3.0.2`, PyObjC `12.1` | Worker terminates with `SIGABRT` before returning devices |
| Python 3.12 | `bleak 3.0.2`, PyObjC `12.1` | Worker terminates with `SIGABRT` before returning devices |

The failure appears to be below the package's Python transport layer. The
worker wrapper reports `worker_python`, `returncode`, and `signal` so callers can
surface this separately from ordinary zero-device scans.
