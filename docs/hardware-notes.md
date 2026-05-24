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
`1827` on the local G60. On `FEE9`, device-info, firmware, and register frames
ACKed; legacy op `0x01` brightness/sleep controls timed out. Writing raw
runtime frames to `1827/2ADB` disconnected immediately.

Direct Swift/Python processes without an app bundle were killed by macOS TCC
before scan results were returned, which matches the bleak worker `SIGABRT`
diagnostics. Use `zlight ble-helper --ensure --open-settings` to build the
cached helper and open the Bluetooth privacy settings for the exact bundle id
used by scans.

Current local BLE scan validation:

| Runtime | BLE stack | Result |
| --- | --- | --- |
| Python 3.13 | `bleak 3.0.2`, PyObjC `12.1` | Worker terminates with `SIGABRT` before returning devices |
| Python 3.12 | `bleak 3.0.2`, PyObjC `12.1` | Worker terminates with `SIGABRT` before returning devices |

The failure appears to be below the package's Python transport layer. The
worker wrapper reports `worker_python`, `returncode`, and `signal` so callers can
surface this separately from ordinary zero-device scans.
