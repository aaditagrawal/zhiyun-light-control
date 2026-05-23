# Protocol Notes

This package implements the USB runtime frame and a BLE transport for the same command layer.

## Runtime Frame

```text
24 3c
len_lo len_hi
type_lo type_hi    # observed 0x0100, serialized as 00 01
seq_lo seq_hi
cmd_lo cmd_hi
payload...
crc_lo crc_hi      # CRC-16/CCITT, initial 0, over body only
```

Known global runtime commands:

| Command | Name | Status |
| --- | --- | --- |
| `0x2003` | Device info | Verified USB |
| `0x8001` | Firmware version | Verified USB |
| `0x2001` | Voltage/status | Verified USB |
| `0x2005` | Device id | Verified USB after firmware `1.6.4`; returned `0` in the current session |
| `0x0006` | Register to default group | Verified USB ACK |

Known object commands:

| Command | Name | Payload shape | Current USB status |
| --- | --- | --- | --- |
| `0x1001` | Brightness | `u16 obj, u8 op, float value` | `sent_no_response` on G60 `1.6.4` |
| `0x1002` | CCT | `u16 obj, u8 op, u16 kelvin` | `sent_no_response` on G60 `1.6.4` |
| `0x1003` | RGB | `u16 obj, u8 op, u16 r, u16 g, u16 b` | `sent_no_response` on G60 `1.6.4` |
| `0x1008` | Sleep/power | `u16 obj, u8 op, u8 value` | `sent_no_response` on G60 `1.6.4` |
| `0x100a` | HSI | `u16 obj, u8 op, float h, float s, u16 intensity` | `sent_no_response` on G60 `1.6.4` |
| `0x100b` | Brightness plus mode | `u16 obj, u8 op, float brightness, i8 mode` | `sent_no_response` on G60 `1.6.4` |
| `0x1101` | Identify | `u16 obj` | `sent_no_response` on G60 `1.6.4` |
| `0x1201` | Voltage by object | `u16 obj` | `sent_no_response` on G60 `1.6.4` |
| `0x1202` | Firmware by object | `u16 obj` | `sent_no_response` on G60 `1.6.4` |
| `0x1203` | Device mode | `u16 obj` | `sent_no_response` on G60 `1.6.4` |

Object reads still need more live validation. On the upgraded G60, registration
ACKs over USB, but object reads tested for object ids `0`, `1`, `2`, `100`,
`0x8001`, `0x8064`, and `0xffff` did not respond. The `zlight discover-usb`
matrix also tested first-word values `0x0100`, `0x0101`, `0x0103`, and
`0x0301`; only `0x0301` produced exact echoes for object read and control
probes, not device ACKs. Optional runtime control candidates for sleep,
brightness, CCT, and brightness-plus-mode still timed out over USB. A later
narrowed matrix confirmed `register_default_group_dev0_group0` and
`register_default_group_dev1_group0`, then `set_sleep_obj1` still timed out.
After registering device id `1`, a probe reported `device_id: 1`; registering
device id `0` restored the original reported id.

`discover-usb --allow-control` can separately vary control object ids and
control frame first words with `--control-object-ids` and
`--control-first-words`. This keeps broad read discovery separate from the
smaller write matrix needed to investigate object-control routing. It can also
vary the registration prelude with `--register-device-ids` and
`--register-group-ids`, and restrict state-changing probes with
`--control-kinds sleep,brightness,cct,brightness-with-mode`. Because alternate
registration ids are visible in later probes, re-register the intended id after
experiments.

The official Vega Android package includes `base/assets/pl103/1.6.4.config`.
For PL103 it lists optional control commands `0x1001`, `0x1002`, `0x1008`,
`0x1101`, `0x1201`, and `0x1202` with `controlMode: "0x33"`, plus CCT range
`2700..6500`. That is protocol evidence for the G60 feature set, but the current
USB frame builder has not reproduced the route that makes those object commands
ACK over USB.

The library exposes object-control commands through `CommandResult` objects so integrations can inspect `tx_hex`, `rx_hex`, parsed frames, echo detection, and timeout/ACK status. This is useful while the exact object-control behavior is still being validated across USB and BLE. `transport_status` is `acknowledged`, `sent_no_response`, `echoed_write`, or `response_without_matching_ack`.

`zlight validate` and `validate_sync_light()` build on `CommandResult` to produce
a hardware evidence report. A primitive is `confirmed` only when the device
returns a matching ACK frame with a valid CRC. A transmitted object-control frame
that receives no response remains `sent_no_response`, not confirmed. An exact
write echo is reported as `echoed_write` and is also not confirmed. Use
`zlight validate --strict` in automation when unconfirmed attempted primitives
should fail the run.

The direct CLI commands `register`, `read`, `set`, and `apply` use the same ACK
definition for their process exit status. They print the full `CommandResult`
payload either way, but unacknowledged transmissions return exit code `1`.
`zlight frame` exposes the lower-level `exchange_frame()` primitive for direct
bench checks; it requires `--yes` and exits non-zero unless the light returns a
matching ACK.

USB transports take an advisory file lock before opening the serial device and
hold it until close. This serializes independent CLI or bridge processes that
target the same `/dev/cu.usbmodem*` path and avoids interleaved request/response
frames. `--usb-lock-timeout` controls the wait: `0` fails fast and `none` waits
indefinitely.

## Media Integration Surface

The local HTTP bridge is intentionally small and JSON-only:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Process health |
| `GET` | `/openapi.json` | Machine-readable bridge schema |
| `GET` | `/probe` | Global light probe |
| `GET` | `/validate` | Read-only hardware validation report |
| `GET` | `/commands` | List bridge commands |
| `GET` | `/presets` | List loaded named scene presets |
| `GET` | `/state` | Last accepted scene/control request |
| `POST` | `/validate` | Hardware validation report with optional object-read and write checks |
| `POST` | `/register` | Register default group |
| `POST` | `/brightness` | Set brightness |
| `POST` | `/cct` | Set color temperature |
| `POST` | `/sleep` | Set sleep/power value |
| `POST` | `/rgb` | Set RGB values |
| `POST` | `/hsi` | Set HSI values |
| `POST` | `/frame` | Exchange one raw frame for bench/integration tooling |
| `POST` | `/scene` | Apply several properties in order |
| `POST` | `/transition` | Apply a timed sequence from one requested scene to another |
| `POST` | `/preset` | Apply a loaded named preset with optional overrides |

Control endpoints require `zlight serve --allow-control`. `POST /validate` can
run read-only checks without that flag, but its `allow_control` write checks are
also gated by it. Responses include command result details instead of hiding
timeouts, because some endpoints are still experimental on the current G60.
`POST /frame` accepts `first_word`, `command`, `payload_hex`, and `timeout`;
it is deliberately behind the same control gate because arbitrary frames can be
state-changing. The matching CLI form is:

```sh
zlight frame --first-word 0x0100 --command 0x2001 --payload-hex "" --yes
```

On the attached G60, live HTTP bridge checks confirmed `/probe` and `/register`
while `/sleep` and `/brightness` returned `sent_no_response`.
The HTTP bridge sends CORS headers by default for browser-based local control
surfaces; configure them with `--cors-origin`, or disable them with
`--cors-origin none`.

The local bridges default to USB, but `serve`, `osc-serve`, `artnet-serve`, and
`sacn-serve` also accept `--transport ble` with `--address` or
`--name-contains`. BLE bridge mode uses the same command payload builders as USB
and adapts the async BLE client behind the synchronous stdlib bridge servers.
Like one-shot BLE CLI commands, bridge BLE uses worker-isolated exchanges by
default; pass `--unsafe-in-process` only when direct bleak is stable enough for a
long-running media-control process. The bridge CLI keeps one light context open
by default. USB and direct BLE use that to avoid reconnecting for every request;
worker-isolated BLE still performs each raw BLE exchange in a child process. Use
`--no-persistent-light` to restore per-request contexts.

The OSC bridge is UDP-based and dependency-free:

| Address | Arguments |
| --- | --- |
| `/zhiyun/probe` | none |
| `/zhiyun/register` | `i device_id` |
| `/zhiyun/brightness` | `f value`, optional trailing `i obj` |
| `/zhiyun/cct` | `i kelvin`, optional trailing `i obj` |
| `/zhiyun/sleep` | `i value`, optional trailing `i obj` |
| `/zhiyun/rgb` | `i red, i green, i blue`, optional trailing `i obj` |
| `/zhiyun/hsi` | `f hue, f saturation, i intensity`, optional trailing `i obj` |
| `/zhiyun/scene` | `f brightness, i kelvin, i sleep`, optional trailing `i obj` |
| `/zhiyun/preset` | `s name`, optional trailing `i obj` |

The `/light/...` prefix is an alias. Control endpoints require `zlight osc-serve --allow-control`. The server replies to each datagram with `/zhiyun/result` containing success flag, action, and error text.

Named presets are loaded from JSON with `--preset-file`. Files can either be a
top-level mapping of names to scene objects or an object with a `scenes` mapping:

```json
{
  "scenes": {
    "key": {"brightness": 35, "kelvin": 5600},
    "blackout": {"sleep": 1}
  }
}
```

CLI scene fields override preset values when supplied, and HTTP `/preset`
accepts the same scene fields plus `name`. Use `zlight apply --dry-run` to
resolve a preset and override set without opening a USB/BLE connection.

Bridge state is in-memory and intentionally reports requested state, not a
device-confirmed physical measurement. It includes the last scene payload,
source protocol, action name, timestamp, ACK-based applied flag, optional
reason, and transport status strings from any command results. `applied` is
`true` only when all command results for that scene were acknowledged by the
light; unconfirmed writes keep the requested scene visible but report
`applied: false` with a reason such as `sent_no_response` or `echoed_write`.
This gives media tools a stable polling surface without hiding weak hardware
evidence.

HTTP `/transition` accepts either explicit `from` and `to` scene objects or
top-level target scene fields. If `from` is omitted, the bridge uses the last
requested state for the same object id; if no state exists yet, unknown starting
fields are omitted until the final update. Empty intermediate updates are
dropped, so a transition with no known starting values sends the final scene
immediately. Supported transition fields: `steps`, `duration`, and `easing`
(`linear`, `ease-in`, `ease-out`, `ease-in-out`). The same transition planner is
exposed in Python through `SceneTransition`, `scene_transition()`,
`ZhiyunLight.transition_scene()`, and `AsyncZhiyunLight.transition_scene()`.

The Art-Net bridge listens for ArtDmx packets, defaults to universe `0`, and maps DMX channels to a `Scene`:

| DMX Channel | Default | Meaning |
| --- | --- | --- |
| 1 | enabled | Brightness, 0-255 mapped to 0-100% |
| 2 | enabled | CCT, 0-255 mapped to 2700-6500K |
| 3 | disabled | Sleep/power, values below 128 map to `1`, values 128+ map to `0` |

Power/sleep is opt-in because that command's exact device semantics still need more live validation. Use `zlight artnet-serve --sleep-channel 3 --allow-control` to enable it. Repeated identical scenes are dropped so a steady DMX stream does not spam the USB/BLE transport.

The sACN/E1.31 bridge listens for DMX data packets on UDP port `5568`, defaults
to universe `1`, and uses the same `DmxMapping` channel map as Art-Net. Use
`zlight sacn-serve --multicast --allow-control` to join the universe multicast
group, or omit `--multicast` for unicast/local test traffic. Repeated identical
scenes are dropped the same way as Art-Net.

## Updater Identity Frame

Updater identity uses the same envelope with a different first word:

```text
24 3c
len_lo len_hi
dev_lo dev_hi      # request uses 0x0103
seq_lo seq_hi
cmd_lo cmd_hi
payload...
crc_lo crc_hi
```

Read-only updater commands implemented:

| Command | Name | Status |
| --- | --- | --- |
| `0x1300` | chipSync | Verified USB |
| `0x1302` | readSn | Verified USB |

Firmware write commands are intentionally not exposed by this package.

## BLE Surfaces

Direct ZY light service:

- Service: `6e400001-b5a3-f393-e0a9-e50e24dcca9e`
- Write characteristic: `6e400002-b5a3-f393-e0a9-e50e24dcca9e`
- Notify/read characteristic: `6e400003-b5a3-f393-e0a9-e50e24dcca9e`

Mesh-related services observed in ZY Vega:

- Provisioning: `00001827-0000-1000-8000-00805f9b34fb`
- Proxy: `00001828-0000-1000-8000-00805f9b34fb`

Older/alternate YC light service:

- Service: `0000ffe0-0000-1000-8000-00805f9b34fb`
- Write characteristic: `0000ffe1-0000-1000-8000-00805f9b34fb`
- Read characteristic: `0000ffe2-0000-1000-8000-00805f9b34fb`

Older direct ZY service observed in prior notes:

- Service: `0000fee9-0000-1000-8000-00805f9b34fb`
- Write characteristic: `d44bc439-abfd-45a2-b575-925416129600`
- Notify/read characteristic: `d44bc439-abfd-45a2-b575-925416129601`

The BLE API names these characteristic sets as profiles: `direct`, `legacy`,
and `yc`. One-shot BLE CLI commands and bridge commands accept
`--ble-profile`, plus `--ble-service-uuid`, `--ble-write-uuid`, and
`--ble-notify-uuid` for custom bench routing. `AsyncZhiyunLight.ble()` and
`AsyncZhiyunLight.isolated_ble()` expose the same profile and UUID override
arguments.

`zlight scan-ble` runs BLE discovery in a worker process by default. This is deliberate: on the local macOS setup, bleak/CoreBluetooth aborts the interpreter during scanning. Isolating the scan keeps API users and long-running bridge processes alive and returns a JSON diagnostic instead.

One-shot BLE command primitives (`probe`, `register`, `read`, `set`, and `apply`) also use worker-isolated raw exchanges by default. The worker connects, writes one frame to the selected profile's write characteristic, waits for notification data, and returns `{address, rx_hex}` to the parent process. The parent then parses that response through the same `CommandResult` path used by USB, so ACKs, timeouts, and echo detection keep the same semantics. Use `--unsafe-in-process` only when you want the direct bleak transport in the parent process.

`zlight validate --transport ble` is intentionally guarded by
`--unsafe-in-process` because direct bleak validation runs in the main process.
Use crash-isolated `scan-ble` first, then direct BLE validation only on a
runtime where scanning/connecting is stable.

Current local BLE scan validation:

| Runtime | BLE stack | Result |
| --- | --- | --- |
| Python 3.13 | `bleak 3.0.2`, PyObjC `12.1` | Worker terminates with `SIGABRT` before returning devices |
| Python 3.12 | `bleak 3.0.2`, PyObjC `12.1` | Worker terminates with `SIGABRT` before returning devices |

The failure appears to be below the package's Python transport layer. The
worker wrapper reports `worker_python`, `returncode`, and `signal` so callers can
surface this separately from ordinary zero-device scans.
