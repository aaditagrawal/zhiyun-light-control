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
A post-registration read pass then registered device id `1` to group `0` and
immediately retried all object-read candidates for object id `1`; registration
ACKed, but all nine post-register object reads still returned
`sent_no_response`. Device id `0` was re-registered afterward and verified by
status.

`discover-usb --allow-control` can separately vary control object ids and
control frame first words with `--control-object-ids` and
`--control-first-words`. This keeps broad read discovery separate from the
smaller write matrix needed to investigate object-control routing. It can also
vary the registration prelude with `--register-device-ids` and
`--register-group-ids`, and restrict state-changing probes with
`--control-kinds sleep,brightness,cct,brightness-with-mode`. `--control-modes`
defaults to `0x33,0x01` so one run compares the official Vega write operation
byte with the older operation byte. `--post-register-reads` re-runs object read
candidates after each register-default-group attempt, which tests the hypothesis
that registration unlocks object-scoped reads. Because alternate registration
ids are visible in later probes, re-register the intended id after experiments.
Discovery reports include `summary.status_counts`, `confirmed_names`,
`echoed_write_names`, `summary.control`, and `summary.post_register_reads` so
automated setup tools can distinguish confirmed control from transport echoes
without parsing every attempt.

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
For dry-run routing, `scene_command_specs()` exposes the ordered runtime
commands for a `Scene`, `scene_frame_specs()` serializes those commands into
frames for a chosen first word and sequence range, and `scene_command_plan()`
groups both views with a serializable scene payload. `transition_command_plans()`
does the same for each generated transition scene while carrying sequence
numbers forward. None of these helpers opens USB or BLE. When a host is ready to
send, `execute_command_plan()` / `execute_async_command_plan()` and the matching
transition helpers execute those same plans against any USB, sync BLE, async BLE,
or custom light object exposing `exchange_runtime()`. For lower-level routing,
`execute_frame_plan()` / `execute_async_frame_plan()` send the exact planned
frame bytes and preserve first-word and sequence-number choices.

`zlight validate` and `validate_sync_light()` build on `CommandResult` to produce
a hardware evidence report. A primitive is `confirmed` only when the device
returns a matching ACK frame with a valid CRC. A transmitted object-control frame
that receives no response remains `sent_no_response`, not confirmed. An exact
write echo is reported as `echoed_write` and is also not confirmed. Use
`zlight validate --strict` in automation when unconfirmed attempted primitives
should fail the run. Validation responses include `summary.status_counts`,
`summary.categories`, and `summary.ready_for` so controllers can decide whether
identity reads, object reads, control setup, and control writes are confirmed
without parsing every raw command result.

The direct CLI commands `register`, `read`, `set`, and `apply` use the same ACK
definition for their process exit status. They print the full `CommandResult`
payload either way, but unacknowledged transmissions return exit code `1`.
`zlight frame` exposes the lower-level `exchange_frame()` primitive for direct
bench checks; it requires `--yes` and exits non-zero unless the light returns a
matching ACK.
`zlight status` exposes the same ACK-backed global reads as HTTP `/status` and
is read-only on both USB and BLE transports.
`zlight ready` is the local CLI equivalent of HTTP `/ready`: it performs the
same read-only transport preflight, returns `ready_for`, `requirements`,
`warnings`, and `actions`, and includes the macOS helper authorization state when
run with `--transport ble --ble-backend macos-app`. It does not start the HTTP
bridge, run a BLE scan, or send control writes.

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
| `GET` | `/manifest` | One-call integration map for media controllers |
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

`GET /manifest` is a machine-readable integration map. It groups the bridge's
HTTP read/control/bench endpoints, local CLI preflight commands, state
stream/history paths, OSC addresses, Art-Net and sACN defaults, BLE
authorization commands, scene fields, loaded preset/cue names, and ACK evidence
semantics into one response for show-control setup tools. `GET /capabilities`
and `/manifest` both include `primitive_requirements`, a transport-neutral map
from public primitive names and aliases to setup-profile capabilities. For
example, `brightness` and `set_brightness` require `control_writes`, while
`read_brightness` requires `object_reads` and `status` requires `read_status`.
The Python HTTP client exposes that metadata through
`primitive_requirements_map()` and `primitive_requirements(name)` for
controllers that use the bridge as the process boundary.

`GET /ready` is a dashboard/controller preflight that combines the same
ACK-backed status read with non-scanning device discovery, the current requested
bridge state, and explicit booleans for `read_status`, `control_requests`, and
`confirmed_control`. It does not run a BLE scan or send write commands. It also
returns normalized `actions` with stable ids, readiness booleans, and endpoint or
command hints for setup UIs. It also includes `requirements`, keyed by
`ready_for` capability, so a controller can show the exact pending action ids for
`read_status`, `control_requests`, `confirmed_control`, state events, and device
discovery. When the bridge is configured as `transport=ble` with
`ble_backend=macos-app`, the embedded `devices.ble.macos_status` field includes
the helper authorization/state report without scanning. The Python
`LightBridgeClient` exposes readiness fields as helper methods, and the module
also includes `devices_*` helper functions that normalize either a `/devices`
payload or the nested `devices` object from `/ready`.

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
`dry_run: true` plus the target scene data, command plan, frame hex, and carried
sequence numbers without opening USB/BLE or requiring `--allow-control`.
SDK callers can reach the same surface through `LightController.plan_scene()`,
`plan_preset()`, `plan_transition()`, `plan_sequence()`, or the matching
`LightBridgeClient` helpers when the HTTP bridge is the process boundary.
Embedded hosts that already use `LightIntegration` or `AsyncLightIntegration`
can attach preset/cue libraries there and call the same `plan_*` helpers before
opening USB or BLE. The same integration objects also expose direct primitive
helpers for `register`, `read_brightness`, `read_cct`, `read_sleep`,
`set_brightness`, `set_cct`, `set_sleep`, `set_rgb`, and `set_hsi`, plus
`apply_scene`, `apply_preset`, `run_sequence`, `run_cue`, and `run_named_cue`
control helpers with opt-in readiness checks for embedded hosts that do not run
the HTTP bridge. Primitive responses preserve raw `CommandResult` evidence and
add decoded functional payload fields when an ACK carries brightness, CCT, sleep,
RGB, or HSI values. Direct integration control records into the integration
state tracker so subsequent `state_snapshot`, `state_history`, and
readiness/snapshot payloads include the latest control evidence by default.
One-shot integration helpers close internally owned light factories after each
call; long-lived media hosts should keep an explicit controller or injected
factory when they want connection persistence across commands.
Low-level protocol callers can import `RuntimeCommand`, `UpdaterCommand`,
`build_runtime_frame`, `first_frame`, payload builders, and functional payload
parsers directly from `zhiyun_light_control` when building custom transports or
external SDK adapters.
For transport setup, embedded hosts can call `connection_candidates` and
`with_best_connection` to derive ranked USB/BLE `LightConnectionConfig` objects
from local discovery, or `ble_endpoint_connection_candidates` and
`with_ble_endpoint_connection` to derive BLE configs from endpoint-test evidence.
Hosts that need a route proven by a read-only status ACK can call
`probe_connection_candidates` for `status_probe` evidence on each route, or
`with_confirmed_connection` to select the best route whose identity/status probe
succeeds. That status confirmation is separate from control-write confirmation;
use validation with `allow_control` before enabling production writes.
`setup_report` combines the common host setup flow into one SDK payload:
status-probed routes, the selected config, readiness, validation readiness, and
unconfirmed primitive names. It is intended for setup dashboards and media hosts
that need to decide whether status, object reads, and control writes are
independently usable on the selected transport. Reports now also include
`capabilities`, `primitive_ready_for`, and `primitive_readiness`, which are plain
JSON projections of the SDK primitive gates. That lets a controller arm or block
`status`, `read_brightness`, `set_brightness`, cues, scenes, and transitions
without duplicating the primitive-to-capability map.
`setup_profile` wraps that report in a portable `LightSetupProfile` JSON object
with the selected `LightConnectionConfig`, summary booleans, validation
capabilities, and unconfirmed primitive names. Use
`save_light_setup_profile`/`load_light_setup_profile` when a host needs to carry
the same setup evidence between processes or operating systems. Profiles expose
`ready`, `unready_capabilities`, and `require_ready`, and the integration
facades expose `from_setup_profile`, `from_setup_profile_file`, and
`with_setup_profile` so host applications can rebuild SDK clients from saved
evidence while failing fast on missing capabilities.
Profiles also expose primitive-level checks. `primitive_ready("set_brightness")`
and `require_primitive("read_brightness")` map public SDK operations to the
evidence capabilities they require (`control_writes`, `object_reads`,
`control_setup`, or `read_status`). Standalone helpers
`setup_profile_primitive_readiness` and
`setup_profile_primitive_readiness_map` consume either a `LightSetupProfile`,
profile JSON, or a raw setup report. `LightIntegration` and
`AsyncLightIntegration` instances created from a profile retain that evidence as
`setup_profile_evidence` and expose matching
`setup_profile_primitive_ready`/`require_setup_profile_primitive` helpers. Their
direct primitive helpers also accept `require_setup_profile=true`, and
`with_setup_profile(..., require_controls=true)` or
`from_setup_profile(..., require_controls=true)` makes that saved-profile gate
the default before opening USB/BLE. This is the fail-closed path for production
controllers that should not send a control primitive unless the setup profile
proves the operation was validated on the selected transport.
Embedded integration snapshots add a top-level `client` object containing the
active guard mode and the same compact profile summary used by bridge clients.
That lets a setup dashboard consume one snapshot and confirm whether the host is
currently using saved setup evidence.
When a host talks to a long-running HTTP bridge instead of embedding the
transport directly, `LightBridgeClient.setup_report`,
`LightBridgeClient.setup_profile`, and `LightBridgeClient.save_setup_profile`
produce the same profile shape from bridge `/integration` and `/validate`
evidence. `LightBridgeClient.setup_primitive_readiness` and
`setup_primitive_readiness_map` expose the setup gates directly, while
`bridge_setup_report`, `bridge_setup_primitive_readiness`, and
`bridge_connection_config` expose that normalization for clients that fetch the
JSON themselves.
`LightBridgeClient.with_setup_profile(profile, require_controls=true)` attaches
saved setup evidence to a bridge client and applies the same primitive guard
before POSTing control requests. Per-call `require_setup_profile=true` is
available for hosts that only want this check around specific bridge commands.
`save_light_connection_config` and `load_light_connection_config` serialize the
same config shape to JSON so setup tools can persist a confirmed USB port or BLE
endpoint profile for later SDK sessions.
Rig fixtures can reference the same setup evidence with `profile_path` or an
inline `profile` object. The rig loader resolves relative `profile_path` values
beside the rig JSON file, uses the profile's selected config for the fixture,
and keeps the profile available through `rig.setup_profile(name)` and
`rig.require_setup_profile(name, ...)`. `LightRig` and `AsyncLightRig` also
accept `require_setup_profile_controls=true`, which makes fixture apply helpers
call `require_setup_profile_primitive` before opening the underlying USB/BLE
transport. Per-call `require_setup_profile=true` is available on rig apply
helpers for hosts that only want this guard on selected cues.

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
`error`, `returncode`, and `signal` fields as `zlight scan-ble`. Add
`include_ble_status=true` to run the macOS helper status check and return
`ble.macos_status` with Bluetooth state and authorization fields. Scan devices
include advertised service UUIDs in `services` when the selected backend reports
them, plus `suggested_profile` when those services match `direct`, `legacy`, or
`yc`. The response also includes `ble.macos_helper`, which names the cached
`ZhiyunBleScan.app`, bundle id, app path, status command, and settings hint
needed for macOS Bluetooth authorization.

The same discovery payload is available from the CLI with `zlight devices`.
Use `--include-ble-status` for the macOS helper authorization check and
`--include-ble` for a bounded BLE scan. Requested BLE failures are returned as
JSON diagnostics and make the CLI exit with code `2`, while USB-only listing
does not open the light or send protocol frames.

HTTP `/discover-usb` exposes the same bounded primitive matrix as
`zlight discover-usb` for dashboard-driven bench work. The endpoint returns the
object ids, first words, control candidate settings, summary counts, notes, and
every `CommandResult`. Read-only discovery can run without `--allow-control`;
control/register candidates are only included when the bridge has
`--allow-control` and the request body sets `allow_control: true`. Set
`post_register_reads: true` to include the post-registration object-read pass and
its nested summary.

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
It also exposes `devices_selected_usb_port()`, `devices_usb_available()`,
`devices_ble_authorization()`, `devices_ble_state()`, `devices_ble_blocker()`,
and BLE scan helpers for transport preflight payloads.
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
| `0x1302` | readSn | Verified USB; payload decodes to product `0x0541` and runtime identifier `08a409e0c1100113` |

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
the Bluetooth privacy settings for the exact bundle id used by scans. Use
`zlight ble-helper --status --json` or
`GET /devices?include_ble_status=true` to report the current helper Bluetooth
state and authorization status without starting GATT inspection.

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
