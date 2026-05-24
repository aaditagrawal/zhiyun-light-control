# HTTP Bridge

The local HTTP bridge is intentionally small, JSON-only, and suitable as a
process boundary for media controllers and dashboards.

Run a JSON HTTP bridge:

```sh
uv run zlight serve --transport usb --host 127.0.0.1 --port 8765 --allow-control
```

Use the Python HTTP client from another process:

```python
from zhiyun_light_control import LightBridgeClient, Scene

bridge = LightBridgeClient(
    "http://127.0.0.1:8765",
    require_acknowledged_controls=True,
)

status = bridge.status()
result = bridge.apply_scene(Scene(brightness=20, kelvin=5600))
print(status["ok"], result["applied"])
```

## Endpoints

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
| `POST` | `/execute-plan` | Execute serialized scene/preset/transition/sequence plan frames |
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
Write endpoints, `/scene`, `/transition`, `/preset`, `/sequence`, and
write-enabled `/validate` accept optional `control_mode`; it defaults to `0x33`
and can be sent as an integer or an integer string such as `"0x01"`.

`POST /frame` accepts `first_word`, `command`, `payload_hex`, and `timeout`.
It is deliberately behind the same control gate because arbitrary frames can be
state-changing. The matching CLI form is:

```sh
zlight frame --first-word 0x0100 --command 0x2001 --payload-hex "" --yes
```

The HTTP bridge sends CORS headers by default for browser-based local control
surfaces. Configure them with `--cors-origin`, or disable them with
`--cors-origin none`.

## Readiness And Metadata

`GET /status`, `zlight status`, `read_sync_status()`, and `read_async_status()`
are read-only and return parsed identity/status fields alongside the raw
`CommandResult` for each global read. This gives integrations a stable polling
surface without using the gated raw-frame endpoint.

`GET /manifest` is a machine-readable integration map. It groups the bridge's
HTTP read/control/bench endpoints, local CLI preflight commands, state
stream/history paths, OSC addresses, Art-Net and sACN defaults, BLE
authorization commands, scene fields, loaded preset/cue names, and ACK evidence
semantics into one response for show-control setup tools.

`GET /capabilities` and `/manifest` both include `primitive_requirements`, a
transport-neutral map from public primitive names and aliases to setup-profile
capabilities. For example, `brightness` and `set_brightness` require
`control_writes`, while `read_brightness` requires `object_reads` and `status`
requires `read_status`. The Python HTTP client exposes that metadata through
`primitive_requirements_map()` and `primitive_requirements(name)`.

`GET /ready` is a dashboard/controller preflight that combines the same
ACK-backed status read with non-scanning device discovery, the current requested
bridge state, and explicit booleans for `read_status`, `control_requests`, and
`confirmed_control`. It does not run a BLE scan or send write commands. It
returns normalized `actions` with stable ids, readiness booleans, and endpoint
or command hints for setup UIs. It also includes `requirements`, keyed by
`ready_for` capability, so a controller can show the exact pending action ids.

When the bridge is configured as `transport=ble` with
`ble_backend=macos-app`, the embedded `devices.ble.macos_status` field includes
the helper authorization/state report without scanning.

The Python `LightBridgeClient` exposes readiness fields as helper methods, and
the module also includes `devices_*` helper functions that normalize either a
`/devices` payload or the nested `devices` object from `/ready`.
It also exposes `devices_selected_usb_port()`, `devices_usb_available()`,
`devices_ble_authorization()`, `devices_ble_state()`, `devices_ble_blocker()`,
and BLE scan helpers for transport preflight payloads.

## Devices And Discovery

HTTP `/devices` exposes transport discovery for dashboards and controller setup
flows. It always returns USB `/dev/cu.usbmodem*` ports and the configured USB
port, without opening the light. On macOS, each port can include descriptor
metadata from IOKit such as `vendor_id_hex`, `product_id_hex`, `product_name`,
`vendor_name`, and `location_id_hex`.

Add `include_ble=true` to run a bounded BLE scan with `ble_backend=worker`,
`macos-app`, or `direct`; BLE scan errors are returned in the `ble.scan` object
with the same `ok`, `error`, `returncode`, and `signal` fields as
`zlight scan-ble`. Add `include_ble_status=true` to run the macOS helper status
check and return `ble.macos_status` with Bluetooth state and authorization
fields.

Scan devices include advertised service UUIDs in `services` when the selected
backend reports them, plus `suggested_profile` when those services match
`direct`, `legacy`, or `yc`. The response also includes `ble.macos_helper`,
which names the cached `ZhiyunBleScan.app`, bundle id, app path, status command,
and settings hint needed for macOS Bluetooth authorization.

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
`post_register_reads: true` to include the post-registration object-read pass
and its nested summary.

## Planning And Execution

HTTP `/plan` is the non-hardware planning surface for controllers. It accepts
the same scene, preset, transition, and sequence shapes used by the write
endpoints, resolves presets and implicit transition starts, and returns
`dry_run: true` plus the target scene data, command plan, frame hex, and carried
sequence numbers without opening USB/BLE or requiring `--allow-control`.

`LightBridgeClient.execute_plan(...)` posts a raw serialized plan or
`SerializedPlanBundle` to HTTP `/execute-plan`; the bridge then uses
`exchange_prebuilt_frame` so the sequence number, first word, command, and
payload bytes from the planner are the bytes sent over the configured USB or BLE
transport.

For shell-driven systems, `zlight plan --output plan.json` creates the same
bundle without opening a transport, and `zlight execute-plan plan.json` sends it
over direct USB/BLE or through HTTP `/execute-plan` when `--base-url` is set.

## State, Events, And History

Bridge state is in-memory and intentionally reports requested state, not a
device-confirmed physical measurement. It includes the last scene payload,
source protocol, action name, timestamp, ACK-based applied flag, optional
reason, transport status strings, and `result_summaries` from command results.

`applied` is `true` only when all command results for that scene were
acknowledged by the light; unconfirmed writes keep the requested scene visible
but report `applied: false` with a reason such as `sent_no_response` or
`echoed_write`.

The Python client exposes `bridge_response_applied()`,
`bridge_response_statuses()`, and `bridge_response_reason()` so controller code
can apply the same interpretation to single command results, scenes,
transitions, sequences, state snapshots, and history events.

HTTP `/events` streams the same state model as Server-Sent Events for dashboards
and automation panels. By default it sends the current snapshot first, then
future updates. `limit`, `timeout`, and `initial=false` query parameters make it
usable in finite scripts and tests; browsers can consume it directly with
`EventSource`.

HTTP `/history` returns recent requested-state events with the same version
numbers used by `/events`. Use `after=<version>` and `limit=<n>` when a
dashboard, show controller, or automation process reconnects and needs to catch
up on missed cue/control requests before opening a new event stream.

## Scenes, Transitions, And Cues

HTTP `/transition` accepts either explicit `from` and `to` scene objects or
top-level target scene fields. If `from` is omitted, the bridge uses the last
requested state for the same object id; if no state exists yet, unknown starting
fields are omitted until the final update. Empty intermediate updates are
dropped, so a transition with no known starting values sends the final scene
immediately. Supported transition fields: `steps`, `duration`, and `easing`
(`linear`, `ease-in`, `ease-out`, `ease-in-out`).
The same transition planner is exposed in Python through `SceneTransition`,
`scene_transition()`, `ZhiyunLight.transition_scene()`, and
`AsyncZhiyunLight.transition_scene()`.

HTTP `/sequence` accepts an ordered `steps` array for cue-style integrations.
Each step can be a scene step (`{"scene": {...}}` or top-level scene fields), a
preset step (`{"preset": "key", "overrides": {...}}`), or a transition step
(`{"to": {...}, "steps": 8, "duration": 2.0}`). The response includes per-step
`applied` and `reason` fields plus an aggregate sequence result. Pass
`stop_on_unconfirmed: true` to stop executing after the first unacknowledged
step.

When the bridge is started with `--cue-file`, `GET /cues` exposes the loaded
definitions and `POST /cue` runs one by `name` or `cue` through the same
sequencer. The named-cue endpoint records bridge state with action `cue` while
preserving the same per-step and aggregate ACK evidence as `/sequence`.

## BLE Bridge Mode

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

## Bridge Setup Profiles

When a host talks to a long-running HTTP bridge instead of embedding the
transport directly, `LightBridgeClient.setup_report`,
`LightBridgeClient.setup_profile`, and `LightBridgeClient.save_setup_profile`
produce the same profile shape from bridge `/integration` and `/validate`
evidence.

`LightBridgeClient.setup_primitive_readiness` and
`setup_primitive_readiness_map` expose the setup gates directly, while
`bridge_setup_report`, `bridge_setup_primitive_readiness`, and
`bridge_connection_config` expose that normalization for clients that fetch the
JSON themselves.

`LightBridgeClient.with_setup_profile(profile, require_controls=true)` attaches
saved setup evidence to a bridge client and applies the same primitive guard
before POSTing control requests. Per-call `require_setup_profile=true` is
available for hosts that only want this check around specific bridge commands.
