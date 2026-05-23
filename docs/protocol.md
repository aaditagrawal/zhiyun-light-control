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
brightness, CCT, and brightness-plus-mode still timed out over USB with both
the Vega `controlMode` operation byte `0x33` and the legacy operation byte
`0x01`. A later
narrowed matrix confirmed `register_default_group_dev0_group0` and
`register_default_group_dev1_group0`, then `set_sleep_obj1_mode0x33` still
timed out.
After registering device id `1`, a probe reported `device_id: 1`; registering
device id `0` restored the original reported id.
A later sleep-only matrix tested control first words `0x0001`, `0x0100`,
`0x0000`, `0x0101`, and `0x0301` for object ids `0` and `1`. `0x0301` still
returned exact write echoes, while the other first words timed out; none were
ACK-confirmed object control.

`discover-usb --allow-control` can separately vary control object ids and
control frame first words with `--control-object-ids` and
`--control-first-words`. This keeps broad read discovery separate from the
smaller write matrix needed to investigate object-control routing. It can also
vary the registration prelude with `--register-device-ids` and
`--register-group-ids`, and restrict state-changing probes with
`--control-kinds sleep,brightness,cct,brightness-with-mode`. `--control-modes`
defaults to `0x33,0x01` so one run compares the official Vega write operation
byte with the older operation byte. Because alternate registration ids are
visible in later probes, re-register the intended id after experiments.
Discovery reports include `summary.status_counts`, `confirmed_names`,
`echoed_write_names`, and `summary.control` so automated setup tools can
distinguish confirmed control from transport echoes without parsing every
attempt.

The official Vega Android package includes `base/assets/pl103/1.6.4.config`.
For PL103 it lists optional control commands `0x1001`, `0x1002`, `0x1008`,
`0x1101`, `0x1201`, and `0x1202` with `controlMode: "0x33"`, plus CCT range
`2700..6500`. Disassembly of Vega's `libzylink.so` shows that `controlMode` is
the `u8 op` field inside the functional payload, not the frame first word.
Accordingly, library writes now default to op `0x33`; object reads still use op
`0x00`, and `control_mode=0x01` is available for reproducing legacy probes.
That is protocol evidence for the G60 feature set, but the current USB route has
not produced ACK-confirmed object control on the attached light.

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
`zlight status` exposes the same ACK-backed global reads as HTTP `/status` and
is read-only on both USB and BLE transports.

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
| `GET` | `/status` | ACK-backed global status reads with raw command evidence |
| `GET` | `/validate` | Read-only hardware validation report |
| `GET` | `/commands` | List bridge commands |
| `GET` | `/capabilities` | Discover primitives, fields, and evidence statuses |
| `GET` | `/diagnostics` | Check bridge transport readiness and next steps |
| `GET` | `/ready` | One-call controller preflight with status, devices, state, and warnings |
| `GET` | `/devices` | Discover local USB ports and optional BLE scan results |
| `GET` | `/events` | Server-Sent Events stream of requested bridge state |
| `GET` | `/history` | Recent requested-state event history for reconnect recovery |
| `GET` | `/presets` | List loaded named scene presets |
| `GET` | `/cues` | List loaded named cue sequences |
| `GET` | `/state` | Last accepted scene/control request |
| `POST` | `/validate` | Hardware validation report with optional object-read and write checks |
| `POST` | `/plan` | Resolve a scene/preset/transition/sequence without writes |
| `POST` | `/inspect-ble` | Inspect BLE GATT services and characteristics |
| `POST` | `/test-ble-endpoints` | Confirm suggested BLE endpoints with read-only `DEVICE_INFO` ACK probes |
| `POST` | `/discover-usb` | Bounded USB primitive discovery matrix with per-attempt evidence |
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
| `POST` | `/sequence` | Run ordered scene, preset, and transition cue steps |
| `POST` | `/cue` | Run a loaded named cue sequence |

Control endpoints require `zlight serve --allow-control`. `POST /validate` can
run read-only checks without that flag, but its `allow_control` write checks are
also gated by it. Responses include command result details instead of hiding
timeouts, because some endpoints are still experimental on the current G60.
Write endpoints, `/scene`, `/transition`, `/preset`, `/sequence`, and write-enabled
`/validate` accept optional `control_mode`; it defaults to `0x33` and can be
sent as an integer or an integer string such as `"0x01"`.
`GET /status`, `zlight status`, `read_sync_status()`, and `read_async_status()`
are read-only and return parsed identity/status fields alongside the raw
`CommandResult` for each global read. This gives integrations a stable polling
surface without using the gated raw-frame endpoint.

`GET /ready` is a dashboard/controller preflight that combines the same
ACK-backed status read with non-scanning device discovery, the current requested
bridge state, and explicit booleans for `read_status`, `control_requests`, and
`confirmed_control`. It does not run a BLE scan or send write commands. It also
returns normalized `actions` with stable ids, readiness booleans, and endpoint or
command hints for setup UIs. The Python `LightBridgeClient` exposes those as
`readiness_actions()`, `readiness_action(id)`, and
`pending_readiness_actions()`.

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

HTTP `/plan` is the non-hardware planning surface for controllers. It accepts
the same scene, preset, transition, and sequence shapes used by the write
endpoints, resolves presets and implicit transition starts, and returns
`dry_run: true` plus the target scene data without opening USB/BLE or requiring
`--allow-control`.

HTTP `/inspect-ble` is the BLE endpoint-discovery surface for setup tools. It
connects through `worker`, `macos-app`, or `direct`, resolves by `address` or
`name_contains`, and returns GATT services, characteristics, and characteristic
properties without sending Zhiyun runtime frames or requiring `--allow-control`.
It also returns `endpoint_candidates`: exact built-in profile matches first,
then lower-confidence writable/notify characteristic pairs with CLI override
arguments for custom routing.

HTTP `/test-ble-endpoints` is the safe BLE endpoint-confirmation step. It runs
inspection, selects the top endpoint candidates, and sends only the read-only
runtime `DEVICE_INFO` command to each candidate. Responses include the raw BLE
exchange plus the normalized `CommandResult`; a candidate is usable evidence
only when `acknowledged` is `true` and it appears in `confirmed_candidates`.

HTTP `/devices` exposes transport discovery for dashboards and controller
setup flows. It always returns USB `/dev/cu.usbmodem*` ports and the configured
USB port, without opening the light. On macOS, each port can include descriptor
metadata from IOKit such as `vendor_id_hex`, `product_id_hex`, `product_name`,
`vendor_name`, and `location_id_hex`; for the attached G60 this identifies
`Zhiyun Virtual ComPort` at `VID 0xfff8`, `PID 0x0180`. Add `include_ble=true`
to run a bounded BLE scan with `ble_backend=worker`, `macos-app`, or `direct`;
BLE scan errors are returned in the `ble.scan` object with the same `ok`,
`error`, `returncode`, and `signal` fields as `zlight scan-ble`. Scan devices
include advertised service UUIDs in `services` when the selected backend reports
them, plus `suggested_profile` when those services match `direct`, `legacy`, or
`yc`. The response also includes `ble.macos_helper`, which names the cached
`ZhiyunBleScan.app`, bundle id, app path, and settings hint needed for macOS
Bluetooth authorization.

HTTP `/discover-usb` exposes the same bounded primitive matrix as
`zlight discover-usb` for dashboard-driven bench work. The endpoint returns the
object ids, first words, control candidate settings, summary counts, notes, and
every `CommandResult`. Read-only discovery can run without `--allow-control`;
control/register candidates are only included when the bridge has
`--allow-control` and the request body sets `allow_control: true`.

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
| `/zhiyun/cue` | `s name`, optional trailing `i obj` |

The `/light/...` prefix is an alias. Control endpoints require `zlight osc-serve --allow-control`. Add `--cue-file` to load named cues for `/zhiyun/cue`. The server replies to each datagram with `/zhiyun/result` containing success flag, action, and error text.

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
reason, transport status strings, and `result_summaries` from any command
results. Those summaries preserve command ids, ACK booleans, transport
statuses, and raw tx/rx evidence for reconnecting controllers and dashboards.
`applied` is `true` only when all command results for that scene were
acknowledged by the light; unconfirmed writes keep the requested scene visible
but report `applied: false` with a reason such as `sent_no_response` or
`echoed_write`.
The Python client exposes `bridge_response_applied()`,
`bridge_response_statuses()`, and `bridge_response_reason()` so controller code
can apply the same interpretation to single command results, scenes,
transitions, sequences, state snapshots, and history events.
This gives media tools a stable polling surface without hiding weak hardware
evidence.

HTTP `/events` streams the same state model as Server-Sent Events for dashboards
and automation panels. By default it sends the current snapshot first, then
future updates. `limit`, `timeout`, and `initial=false` query parameters make it
usable in finite scripts and tests; browsers can consume it directly with
`EventSource`.

HTTP `/history` returns the bridge's recent requested-state events with the same
version numbers used by `/events`. Use `after=<version>` and `limit=<n>` when a
dashboard, show controller, or automation process reconnects and needs to catch
up on missed cue/control requests before opening a new event stream.

HTTP `/transition` accepts either explicit `from` and `to` scene objects or
top-level target scene fields. If `from` is omitted, the bridge uses the last
requested state for the same object id; if no state exists yet, unknown starting
fields are omitted until the final update. Empty intermediate updates are
dropped, so a transition with no known starting values sends the final scene
immediately. Supported transition fields: `steps`, `duration`, and `easing`
(`linear`, `ease-in`, `ease-out`, `ease-in-out`). The same transition planner is
exposed in Python through `SceneTransition`, `scene_transition()`,
`ZhiyunLight.transition_scene()`, and `AsyncZhiyunLight.transition_scene()`.

HTTP `/sequence` accepts an ordered `steps` array for cue-style integrations.
Each step can be a scene step (`{"scene": {...}}` or top-level scene fields), a
preset step (`{"preset": "key", "overrides": {...}}`), or a transition step
(`{"to": {...}, "steps": 8, "duration": 2.0}`). The response includes
per-step `applied` and `reason` fields plus an aggregate sequence result. Pass
`stop_on_unconfirmed: true` to stop executing after the first unacknowledged
step. `zlight cue --cue-file examples/cues.json --cue warm-key --yes` loads the
same structure from a named JSON cue file and posts it to a running HTTP bridge.
When the bridge is started with `--cue-file`, `GET /cues` exposes the loaded
definitions and `POST /cue` runs one by `name` or `cue` through the same
sequencer. The named-cue endpoint records bridge state with action `cue` while
preserving the same per-step and aggregate ACK evidence as `/sequence`.

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

Direct ZY light service used by some Zhiyun devices:

- Service: `6e400001-b5a3-f393-e0a9-e50e24dcca9e`
- Write characteristic: `6e400002-b5a3-f393-e0a9-e50e24dcca9e`
- Notify/read characteristic: `6e400003-b5a3-f393-e0a9-e50e24dcca9e`

Mesh-related services observed in ZY Vega and on the local `PL103_EDFE` G60:

- Provisioning: `00001827-0000-1000-8000-00805f9b34fb`
- Proxy: `00001828-0000-1000-8000-00805f9b34fb`
- Local G60 write characteristic under `1827`: `00002adb-0000-1000-8000-00805f9b34fb`
- Local G60 notify characteristic under `1827`: `00002adc-0000-1000-8000-00805f9b34fb`

Older/alternate YC light service:

- Service: `0000ffe0-0000-1000-8000-00805f9b34fb`
- Write characteristic: `0000ffe1-0000-1000-8000-00805f9b34fb`
- Read characteristic: `0000ffe2-0000-1000-8000-00805f9b34fb`

Older direct ZY service verified on the local `PL103_EDFE` G60:

- Service: `0000fee9-0000-1000-8000-00805f9b34fb`
- Write characteristic: `d44bc439-abfd-45a2-b575-925416129600`
- Notify/read characteristic: `d44bc439-abfd-45a2-b575-925416129601`

The BLE API names these characteristic sets as profiles: `direct`, `legacy`,
and `yc`. One-shot BLE CLI commands and bridge commands accept
`--ble-profile`, plus `--ble-service-uuid`, `--ble-write-uuid`, and
`--ble-notify-uuid` for custom bench routing. `AsyncZhiyunLight.ble()` and
`AsyncZhiyunLight.isolated_ble()` expose the same profile and UUID override
arguments.

`zlight inspect-ble` and HTTP `POST /inspect-ble` enumerate the live GATT table
through the selected backend. Use this after Bluetooth authorization to collect
the exact services, write characteristics, notify characteristics, and
properties exposed by a specific firmware before choosing a custom command
profile. The `endpoint_candidates` array ranks matching built-in profiles ahead
of generic write/notify pairs and includes `cli_args` ready to pass to one-shot
BLE commands or bridge startup.

`zlight test-ble-endpoints` and HTTP `POST /test-ble-endpoints` use those
candidates directly and report ACK evidence for each read-only `DEVICE_INFO`
probe. This is the preferred way to promote a guessed write/notify pair into a
confirmed BLE command route before testing control primitives.

Native bundled CoreBluetooth inspection with an
`NSBluetoothAlwaysUsageDescription` plist found service `FEE9` plus mesh service
`1827` on the local G60. On `FEE9`, device-info, firmware, and register frames
ACKed; legacy op `0x01` brightness/sleep controls timed out. Writing raw runtime
frames to `1827/2ADB` disconnected immediately. Direct Swift/Python processes
without an app bundle were killed by macOS TCC before scan results were
returned, which matches the bleak worker `SIGABRT` diagnostics below. Use
`zlight ble-helper --ensure --open-settings` to build the cached helper and open
the Bluetooth privacy settings for the exact bundle id used by scans.

`zlight scan-ble` runs BLE discovery in a worker process by default. This is deliberate: on the local macOS setup, bleak/CoreBluetooth aborts the interpreter during scanning. Isolating the scan keeps API users and long-running bridge processes alive and returns a JSON diagnostic instead.

One-shot BLE command primitives (`probe`, `status`, `register`, `read`, `set`,
and `apply`) also use worker-isolated raw exchanges by default. The worker
connects, writes one frame to the selected profile's write characteristic, waits
for notification data, and returns `{address, rx_hex}` to the parent process.
The parent then parses that response through the same `CommandResult` path used
by USB, so ACKs, timeouts, and echo detection keep the same semantics. Use
`--unsafe-in-process` only when you want the direct bleak transport in the
parent process.

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
