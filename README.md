# Zhiyun Light Control

Python SDK tooling for controlling Zhiyun MOLUS lights over USB CDC and, where
the local Bluetooth stack is stable, BLE. The project started from live protocol
work against a MOLUS G60 on macOS and is built for local media-production
automation: Python APIs first, plus command line control, HTTP, OSC, Art-Net,
and sACN adapters.

The current verified target is a MOLUS G60 on firmware `1.6.4`, visible on
macOS as `Zhiyun Virtual ComPort` (`fff8:0180`) at `/dev/cu.usbmodem21301`.

## Status

Verified over USB on the G60:

- Device probe, firmware, voltage/status, and device id reads.
- Register-to-default-group command.
- Firmware identity reads used by Zhiyun's updater flow.
- Runtime command framing, CRC, ACK parsing, and command diagnostics.

Implemented and still experimental:

- Brightness, CCT, sleep/power, RGB, HSI, and scene application.
- BLE transport using the direct ZY light service and characteristics seen in
  ZY Vega, plus a macOS bundled CoreBluetooth helper backend.
- Local HTTP, OSC, Art-Net, and sACN bridges for production tools.
- Named scene presets loaded from JSON.
- Requested-state tracking for bridge clients.
- Timed scene transitions for cue-style fades over USB or BLE.

Firmware flashing is intentionally not implemented here. Use Zhiyun's official
updater for firmware writes.

## Requirements

- macOS, Linux, or Windows with Python `>=3.10`. This repository defaults to
  Python `3.12` through `.python-version`.
- [`uv`](https://docs.astral.sh/uv/) for Python runtime and package management.
- A USB data cable for USB control. USB CDC uses pyserial for portable
  Windows/Linux serial access and a direct POSIX serial path on macOS/Linux.
  Some C-to-C charging cables do not enumerate the serial interface; a known
  data-capable USB-A-to-C cable through an adapter is a useful fallback.
- For a MOLUS G60, keep the light on its normal PD power supply while using USB
  data from the computer.

## Quick Start

From this repository:

```sh
cd /Users/mav/Documents/aurion/zhiyun-light-control
uv sync --extra ble --extra dev
uv run zlight devices --include-ble-status --json
uv run zlight probe --transport usb
uv run zlight status --transport usb
```

For USB-only operation, the default dependency set is enough:

```sh
uv sync
uv run zlight probe --transport usb
uv run zlight status --transport usb
```

If the light is not found automatically, pass the serial port explicitly:

```sh
uv run zlight probe --transport usb --port /dev/cu.usbmodem21301
# Windows example:
uv run zlight probe --transport usb --port COM7
# Linux example:
uv run zlight probe --transport usb --port /dev/ttyACM0
```

Register the current session to the default group before object-scoped control:

```sh
uv run zlight register --transport usb --device-id 0 --yes
```

On the currently attached G60, USB registration ACKs but object-scoped
brightness/CCT/sleep writes still time out. Treat `set` and `apply` as bench
commands until `validate --allow-control` reports ACK-confirmed results on your
transport.

Run a structured hardware validation report:

```sh
uv run zlight validate --transport usb
uv run zlight validate --transport usb --allow-control --include-object-reads
```

The validation report separates ACK-confirmed primitives from frames that were
sent but not acknowledged by the device. Use `--strict` when you want a non-zero
exit unless every attempted command was ACK-confirmed. Its `summary` field is
designed for controllers and setup dashboards: it includes aggregate counts,
status counts, per-category confirmation summaries, and `ready_for` booleans for
`read_status`, `object_reads`, `control_setup`, and `control_writes`.

Direct `register`, `read`, `set`, and `apply` commands also exit non-zero when
the transmitted command does not receive an ACK, so shell scripts can distinguish
working primitives from timeouts.
The low-level `frame` command follows the same rule and exposes direct
first-word/command/payload exchange for bench tooling.

USB opens use an advisory process lock keyed by the serial device so parallel
CLI or bridge processes do not interleave frames on the same light. The default
wait is 10 seconds; use `--usb-lock-timeout 0` to fail fast or
`--usb-lock-timeout none` to wait indefinitely.

Run the broader USB discovery matrix while working on unknown primitive shapes:

```sh
uv run zlight discover-usb --object-ids 0,1,2,100,0x8001,0x8064,0xffff
uv run zlight discover-usb --allow-control --timeout 0.5
uv run zlight discover-usb --allow-control --control-object-ids 0,1 --control-first-words 0x0100,0x0301
uv run zlight discover-usb --allow-control --register-device-ids 0,1 --control-object-ids 1 --control-kinds sleep --control-modes 0x33,0x01
uv run zlight discover-usb --allow-control --post-register-reads --register-device-ids 1 --control-object-ids 1 --control-kinds none
```

The same setup/use split is available as a plain Python SDK script:

```sh
uv run python examples/sdk_quickstart.py --config ./zhiyun-light.json
uv run python examples/sdk_quickstart.py --config ./zhiyun-light.json --brightness 35
```

`discover-usb` is for bench work. It records global reads, object-read
candidates, first-word probes, and optional safe control candidates with the
same ACK/timeout/echo evidence model used by validation.
When `--allow-control` is set, control probes default to the same object ids as
`--object-ids`; use `--control-object-ids` and `--control-first-words` to test a
bounded control matrix without expanding read probes. Use
`--register-device-ids`, `--register-group-ids`, `--control-kinds`, and
`--control-modes` to separate registration hypotheses from the specific control
candidates you want to transmit. Add `--post-register-reads` to re-run object
read candidates immediately after each register-default-group attempt, matching
the useful bench sequence for testing whether registration unlocks object reads.

Exchange one raw frame when you need to reproduce protocol evidence directly:

```sh
uv run zlight frame --transport usb --port /dev/cu.usbmodem21301 --first-word 0x0100 --command 0x2001 --payload-hex "" --timeout 0.35 --yes
```

Apply a simple scene:

```sh
uv run zlight apply --obj 1 --sleep 0 --brightness 35 --kelvin 5600 --yes
```

Preview a named preset without sending hardware commands:

```sh
uv run zlight apply --preset-file examples/scenes.json --preset key --dry-run
```

## CLI

List local transport setup without opening the light:

```sh
uv run zlight devices --json
uv run zlight devices --include-ble-status --json
uv run zlight devices --include-ble --include-ble-status --ble-backend macos-app --name-contains PL103 --json
```

`devices` always reports USB serial candidates and marks the configured
`--port` as selected. Add `--include-ble-status` to include the macOS
`ZhiyunBleScan` helper authorization/state report, and `--include-ble` to add a
bounded BLE scan through `worker`, `macos-app`, or `direct`. If a requested BLE
status or scan fails, the command still prints the full JSON diagnostic and exits
with code `2`.

Probe:

```sh
uv run zlight probe --transport usb
uv run zlight probe --transport ble --name-contains MOLUS
```

Read ACK-backed status with raw command evidence:

```sh
uv run zlight ready --transport usb --json
uv run zlight ready --transport ble --ble-backend macos-app --name-contains PL103 --json
uv run zlight integration --transport usb --include-ble-status --ble-backend macos-app --json
uv run zlight integration --transport ble --ble-backend macos-app --name-contains PL103 --json
uv run zlight status --transport usb
uv run --extra ble zlight status --transport ble --name-contains MOLUS
```

`ready` is a read-only one-call preflight for shell scripts and setup tools. It
returns the same `ready_for`, `requirements`, `warnings`, and `actions` model as
HTTP `GET /ready` without starting the bridge. It does not run a BLE scan or send
control writes; in macOS BLE helper mode it includes `devices.ble.macos_status`.
`integration` is the bridge-free setup snapshot for media controllers. It wraps
the local manifest, capabilities, readiness, and device-discovery payloads in the
same `summary` shape as HTTP `GET /integration`; add `--include-ble` when a setup
script should also run a bounded BLE scan.

Scan BLE devices:

```sh
uv run --extra ble zlight scan-ble --timeout 8
uv run zlight scan-ble --backend macos-app --name-contains PL103 --timeout 8
uv run zlight inspect-ble --backend macos-app --name-contains PL103 --timeout 8
uv run zlight test-ble-endpoints --backend macos-app --name-contains PL103 --timeout 5 --max-candidates 4 --json
uv run zlight ble-helper --ensure --open-settings
uv run zlight ble-helper --status --json
```

BLE `probe`, `status`, `register`, `read`, `set`, and `apply` run through a worker
process by default. That keeps the parent CLI/API process alive if
CoreBluetooth aborts below Python. Pass `--unsafe-in-process` only when you
want direct bleak execution on a stable Bluetooth runtime. On macOS, pass
`--backend macos-app` for scanning or `--ble-backend macos-app` for one-shot
commands to use the bundled CoreBluetooth app helper instead of bleak. macOS
must allow `ZhiyunBleScan` under Bluetooth privacy; otherwise the helper reports
`Bluetooth state unauthorized: 3`. `zlight ble-helper --ensure` prints the
exact cached helper app path, bundle id, and Bluetooth settings hint.
`zlight ble-helper --status` runs the helper and returns the current Bluetooth
state plus macOS authorization status without starting endpoint discovery.
`zlight inspect-ble` connects to the matching BLE device and returns GATT
services, characteristics, and properties so unknown write/notify endpoints can
be selected without guessing. Its `endpoint_candidates` field ranks exact
built-in profile matches first, then writable/notify characteristic pairs with
ready-to-use `--ble-service-uuid`, `--ble-write-uuid`, and `--ble-notify-uuid`
CLI overrides.
`zlight test-ble-endpoints` runs the same inspection first, then sends only the
read-only `DEVICE_INFO` command to each suggested endpoint candidate. Its report
marks candidates as confirmed only when the light returns an ACK-backed
`CommandResult`, so it is the next safe step before using those UUIDs for
control.

Full BLE validation sends many exchanges, so run a scan first and choose the
backend explicitly. On macOS use the bundled app helper; on a stable bleak
runtime you can opt into direct in-process BLE:

```sh
uv run zlight validate --transport ble --ble-backend macos-app --name-contains PL103
uv run --extra ble zlight validate --transport ble --address AA:BB:CC:DD:EE:FF --unsafe-in-process
```

Validate the attached USB hardware and generate an evidence report:

```sh
uv run zlight validate --transport usb --allow-control --include-object-reads
```

Send direct controls:

```sh
uv run zlight set brightness --obj 1 --value 35 --yes
uv run zlight set cct --obj 1 --kelvin 5600 --yes
uv run zlight set rgb --obj 1 --red 255 --green 180 --blue 120 --yes
```

Functional writes default to the Vega `controlMode` operation byte `0x33`.
Use `--control-mode 0x01` when reproducing older legacy probes.

Exchange a raw protocol frame:

```sh
uv run zlight frame --transport usb --first-word 0x0100 --command 0x2001 --payload-hex "" --yes
```

Apply presets:

```sh
uv run zlight apply --preset-file examples/scenes.json --preset key --yes
uv run zlight apply --preset-file examples/scenes.json --preset key --brightness 42 --dry-run
```

Commands that change light state require `--yes` so automation cannot
accidentally send hardware writes during a dry run.

## Media Bridges

All bridge commands default to USB and can target BLE with the same control
surface by adding `--transport ble` plus `--address` or `--name-contains`.
Bridge processes keep one light context open by default. USB and direct BLE use
that for lower-latency live control; worker-isolated BLE reconnects per exchange
so a CoreBluetooth abort does not terminate the bridge process. Add
`--ble-backend macos-app` for the bundled macOS CoreBluetooth helper,
`--unsafe-in-process` only on a stable BLE runtime where you want direct bleak
execution, and `--no-persistent-light` when debugging connection setup.

HTTP JSON bridge:

```sh
uv run zlight serve --host 127.0.0.1 --port 8765 --preset-file examples/scenes.json --cue-file examples/cues.json --allow-control
```

The HTTP bridge serves JSON CORS headers for browser dashboards by default.
Use `--cors-origin http://studio.local` to restrict the allowed origin or
`--cors-origin none` to disable CORS headers.

BLE bridge example:

```sh
uv run --extra ble zlight serve --transport ble --name-contains MOLUS --allow-control
uv run zlight serve --transport ble --ble-backend macos-app --name-contains PL103 --allow-control
```

When the HTTP bridge runs with `--transport ble --ble-backend macos-app`,
`GET /ready` includes `devices.ble.macos_status` so a controller can show the
current Bluetooth authorization/state blocker without starting a BLE scan.

Useful HTTP calls:

```sh
curl http://127.0.0.1:8765/probe
curl http://127.0.0.1:8765/status
curl http://127.0.0.1:8765/openapi.json
curl http://127.0.0.1:8765/manifest
curl http://127.0.0.1:8765/validate
curl http://127.0.0.1:8765/commands
curl http://127.0.0.1:8765/capabilities
curl http://127.0.0.1:8765/diagnostics
curl 'http://127.0.0.1:8765/integration?include_ble_status=true'
curl http://127.0.0.1:8765/ready
curl http://127.0.0.1:8765/devices
curl 'http://127.0.0.1:8765/devices?include_ble=true&ble_backend=macos-app&timeout=6&name_contains=PL103'
curl http://127.0.0.1:8765/events?limit=1
curl http://127.0.0.1:8765/history?limit=10
curl http://127.0.0.1:8765/state
curl http://127.0.0.1:8765/presets
curl http://127.0.0.1:8765/cues
curl -X POST http://127.0.0.1:8765/discover-usb \
  -H 'content-type: application/json' \
  -d '{"object_ids": [0, 1], "first_words": ["0x0100", "0x0301"], "timeout": 0.4}'
curl -X POST http://127.0.0.1:8765/validate \
  -H 'content-type: application/json' \
  -d '{"allow_control": true, "include_object_reads": true}'
curl -X POST http://127.0.0.1:8765/plan \
  -H 'content-type: application/json' \
  -d '{"preset": "key", "overrides": {"brightness": 45}}'
curl -X POST http://127.0.0.1:8765/inspect-ble \
  -H 'content-type: application/json' \
  -d '{"backend": "macos-app", "name_contains": "PL103", "timeout": 6}'
curl -X POST http://127.0.0.1:8765/test-ble-endpoints \
  -H 'content-type: application/json' \
  -d '{"backend": "macos-app", "name_contains": "PL103", "timeout": 5, "max_candidates": 4}'
curl -X POST http://127.0.0.1:8765/brightness \
  -H 'content-type: application/json' \
  -d '{"obj": 1, "value": 35}'
curl -X POST http://127.0.0.1:8765/frame \
  -H 'content-type: application/json' \
  -d '{"first_word": "0x0100", "command": "0x2001", "payload_hex": ""}'
curl -X POST http://127.0.0.1:8765/scene \
  -H 'content-type: application/json' \
  -d '{"obj": 1, "sleep": 0, "brightness": 35, "kelvin": 5600}'
curl -X POST http://127.0.0.1:8765/transition \
  -H 'content-type: application/json' \
  -d '{"from": {"brightness": 10}, "to": {"brightness": 60, "kelvin": 5600}, "steps": 8, "duration": 2.0, "easing": "ease-in-out"}'
curl -X POST http://127.0.0.1:8765/preset \
  -H 'content-type: application/json' \
  -d '{"name": "key", "brightness": 45}'
curl -X POST http://127.0.0.1:8765/sequence \
  -H 'content-type: application/json' \
  -d '{"steps": [{"scene": {"brightness": 10}}, {"preset": "key", "overrides": {"brightness": 45}}, {"to": {"brightness": 60}, "steps": 4, "duration": 1.0}]}'
curl -X POST http://127.0.0.1:8765/cue \
  -H 'content-type: application/json' \
  -d '{"name": "warm-key", "stop_on_unconfirmed": true}'
```

Run a named cue from a JSON cue file against a running bridge:

```sh
uv run zlight cue --cue-file examples/cues.json --cue warm-key --base-url http://127.0.0.1:8765 --yes
uv run zlight cue --cue-file examples/cues.json --cue warm-key --dry-run
uv run zlight cue --cue-file examples/cues.json --list
```

For `/transition`, omit `from` to use the bridge's last requested state for the
same object id.

`GET /state` reports the last requested bridge scene and the command transport
statuses behind it. Its `applied` field is ACK-based: it is `true` only when all
command results for that request were acknowledged by the light. If a command
was sent but not confirmed, `applied` is `false` and `reason` carries values
such as `sent_no_response` or `echoed_write`. The response also carries
`result_summaries` with the command ids, ACK flags, transport statuses, and
raw tx/rx evidence from the last request so reconnecting controllers can audit
what actually happened.

`GET /events` streams bridge state as Server-Sent Events for dashboards and
automation panels that should react to cue/control requests without polling.
Use `limit`, `timeout`, and `initial=false` query parameters for finite scripts
and tests.

`GET /history` returns recent requested-state events with their monotonically
increasing versions. Use `after` and `limit` query parameters when a dashboard
or show controller reconnects and needs to catch up before resuming `/events`.

`GET /capabilities` is the discovery endpoint for dashboard, automation, and
show-control clients. It lists every supported read/write primitive, required
payload fields, whether the primitive requires `--allow-control`, scene fields,
loaded preset names, local preflight commands such as `zlight ready`, and the
transport evidence statuses a client should expect. It also includes
`control_guard` readiness rules and `request_templates` for setup, read, and
control requests so external controllers can bootstrap valid JSON bodies without
hard-coding endpoint shapes.

`GET /manifest` is the one-call integration map for media controllers. It lists
HTTP control paths, state/event paths, OSC addresses, Art-Net/sACN defaults, BLE
authorization commands, local CLI preflight commands, scene fields, loaded
preset/cue names, request templates, guard rules, and ACK evidence semantics.

`GET /diagnostics` is the transport diagnostics endpoint for integration
dashboards. It opens the bridge's configured transport, returns ACK-backed
status evidence when
available, echoes the active BLE backend/profile/address filters, and includes
next-step hints for cases such as macOS Bluetooth authorization failures.

`GET /integration` is the controller snapshot endpoint. It combines the
manifest, capabilities, readiness, and local device-discovery payloads under
`payloads`, and exposes a compact `summary` with readiness booleans, pending
setup action IDs, selected USB port, BLE authorization/blocker state, and
ACK-confirmed identity fields.

`GET /ready` is the one-call controller preflight. It combines ACK-backed
transport status, non-scanning device discovery, current requested-state
snapshot, write gate state, and warnings. `ready_for.control_requests` only
turns true when the bridge is connected and started with `--allow-control`;
`ready_for.confirmed_control` only turns true after the bridge has recorded an
ACK-confirmed control request. The response also includes stable `actions`
entries such as `read-status`, `enable-control`, `confirm-control`, and
`authorize-bluetooth`, plus a `requirements` map that groups pending action ids
by each `ready_for` capability for deterministic setup dashboards.

`POST /plan` resolves a scene, preset, transition, or sequence without opening
the light or requiring `--allow-control`. Use it for show-control previews and
setup UIs that need to inspect the exact target scene, ordered command payloads,
frame hex, and sequence numbers before arming writes.

`POST /inspect-ble` is the BLE endpoint-discovery surface. It connects through
the selected backend and returns GATT services, characteristics, and properties
without sending Zhiyun runtime frames or requiring `--allow-control`. The
response includes `endpoint_candidates` so setup dashboards can show the exact
profile/UUID override arguments to try next.

`POST /test-ble-endpoints` is the read-only BLE endpoint-confirmation surface.
It inspects GATT, ranks endpoint candidates, and sends only `DEVICE_INFO` to the
top candidates. Use `confirmed_candidates` from the response to select BLE
profile and UUID overrides for a bridge or show-control process.

`GET /devices` lists local USB serial ports and the bridge's selected USB port.
On macOS it also attaches best-effort USB descriptor metadata such as
`vendor_id_hex`, `product_id_hex`, `product_name`, `vendor_name`, and
`location_id_hex`; this lets setup tools verify that the selected serial device
is the Zhiyun Virtual ComPort. Add `include_ble=true` to run a bounded BLE scan
through the selected `ble_backend` (`worker`, `macos-app`, or `direct`); scan
failures such as macOS Bluetooth authorization errors are returned as JSON
diagnostics. Add `include_ble_status=true` to run the macOS helper status check
and include `ble.macos_status` with Bluetooth state and authorization fields.
BLE scan devices include advertised `services` when the backend reports them and
a best-effort `suggested_profile` (`direct`, `legacy`, or `yc`) when those
services match a supported command profile. The response also includes
`ble.macos_helper` so local dashboards can point users at the exact helper
bundle that needs Bluetooth permission.

`POST /discover-usb` runs the same bounded USB protocol matrix as
`zlight discover-usb` and returns every attempt with ACK/timeout evidence.
Read-only discovery works without `--allow-control`; control candidates require
the bridge to be started with `--allow-control` and the request body to include
`allow_control: true`. The response summary includes `status_counts`,
`confirmed_names`, `echoed_write_names`, and a nested `control` summary so setup
tools can decide whether any write primitive was actually ACK-confirmed without
scraping every raw attempt. Set `post_register_reads: true` to add a nested
post-register object-read summary after the registration prelude.

`GET /status` returns read-only identity/status fields plus the raw
`CommandResult` evidence for global device info, firmware, voltage/status,
device id, updater chip sync, and updater `readSn` identity when that transport
exposes them.

`POST /sequence` is a cue-style orchestration endpoint for media controllers. It
accepts ordered `steps` containing scene steps, preset steps, or transition steps
with `to`, and returns per-step `applied`/`reason` evidence plus an aggregate
sequence result. Add `stop_on_unconfirmed: true` when a cue should stop after
the first unacknowledged write.

`GET /cues` and `POST /cue` expose server-loaded named cues from
`zlight serve --cue-file`. `POST /cue` takes `name` or `cue`, then runs the
stored sequence through the same evidence path as `/sequence`. `zlight cue` can
also load the same shape client-side and post it to a running HTTP bridge. Cue
files can be top-level cue mappings or contain a `cues` object; see
`examples/cues.json`.

`GET /validate` returns the same hardware-evidence report as `zlight validate`
without transmitting control writes. `POST /validate` accepts
`allow_control`, `include_object_reads`, `include_color`, `obj`, and the same
test values as the CLI. Write checks only run when the bridge was started with
`--allow-control`. The response includes `summary.ready_for` and
`summary.categories` so setup tools can decide whether identity reads, object
reads, control setup, or control writes are actually confirmed without parsing
every raw check.

OSC bridge:

```sh
uv run zlight osc-serve --host 127.0.0.1 --port 9000 --preset-file examples/scenes.json --cue-file examples/cues.json --allow-control
```

Supported OSC addresses:

```text
/zhiyun/probe
/zhiyun/register     i:device_id
/zhiyun/brightness   f:value [i:obj]
/zhiyun/cct          i:kelvin [i:obj]
/zhiyun/sleep        i:value [i:obj]
/zhiyun/rgb          i:red i:green i:blue [i:obj]
/zhiyun/hsi          f:hue f:saturation i:intensity [i:obj]
/zhiyun/scene        f:brightness i:kelvin i:sleep [i:obj]
/zhiyun/preset       s:name [i:obj]
/zhiyun/cue          s:name [i:obj]
```

The `/light/...` prefix is accepted as an alias. `/zhiyun/cue` runs a named cue
loaded by `--cue-file` and records requested state with the same ACK evidence as
the HTTP cue endpoints.

Art-Net bridge:

```sh
uv run zlight artnet-serve --host 0.0.0.0 --port 6454 --universe 0 --allow-control
```

sACN/E1.31 bridge:

```sh
uv run zlight sacn-serve --host 0.0.0.0 --port 5568 --universe 1 --multicast --allow-control
```

Default DMX mapping:

```text
Channel 1 -> brightness 0-100%
Channel 2 -> CCT 2700-6500K
```

Power/sleep is disabled by default. To opt in:

```sh
uv run zlight artnet-serve --sleep-channel 3 --allow-control
uv run zlight sacn-serve --sleep-channel 3 --allow-control
```

Use `none` to disable a mapped channel:

```sh
uv run zlight artnet-serve --cct-channel none --allow-control
uv run zlight sacn-serve --cct-channel none --allow-control
```

## Python API

Use the stdlib HTTP client when your production app talks to a running bridge
process instead of opening the USB/BLE transport itself:

```python
from zhiyun_light_control import (
    LightBridgeClient,
    Scene,
    best_connection_config,
    ble_config_from_endpoint_report,
    ble_config_from_scan,
    bridge_response_applied,
    bridge_response_reason,
    bridge_response_statuses,
    devices_ble_authorization,
    devices_ble_blocker,
    devices_selected_usb_port,
    connection_candidates_from_devices,
    usb_config_from_devices,
)

bridge = LightBridgeClient("http://127.0.0.1:8765")

print(bridge.manifest()["osc"]["addresses"])
print(bridge.diagnostics()["connection_confirmed"])
print(bridge.integration(include_ble_status=True)["summary"])
print(bridge.ready()["ready_for"])
print(bridge.pending_readiness_actions())
print(bridge.capabilities()["evidence_statuses"])
print(bridge.cues()["cues"])
devices = bridge.devices(include_ble_status=True)
print(devices_selected_usb_port(devices))
print(devices_ble_authorization(devices), devices_ble_blocker(devices))
route_candidates = connection_candidates_from_devices(devices)
best_config = best_connection_config(route_candidates)
usb_config = usb_config_from_devices(devices)
ble_devices = bridge.devices(include_ble=True, ble_backend="macos-app")
ble_scan_config = ble_config_from_scan(ble_devices)
print([candidate.to_dict() for candidate in route_candidates])
print(best_config.to_dict())
print(usb_config.to_dict())
print(ble_scan_config.to_dict())
ble = bridge.inspect_ble(backend="macos-app", name_contains="PL103")
print(ble["endpoint_candidates"])
ble_endpoint_report = bridge.test_ble_endpoints(
    backend="macos-app",
    name_contains="PL103",
)
ble_endpoint_config = ble_config_from_endpoint_report(ble_endpoint_report)
print(ble_endpoint_config.to_dict())
print(bridge.plan({"preset": "key", "overrides": {"brightness": 45}})["scene"])
print(bridge.discover_usb(object_ids=[0, 1], first_words=["0x0100"])["summary"])
print(next(bridge.state_events(limit=1))["state"])
print(bridge.history(limit=10)["events"])

result = bridge.set_brightness(35, obj=1)
print(result["transport_status"])

scene = bridge.apply_scene(Scene(obj=1, sleep=0, brightness=35, kelvin=5600))
print(scene["results"])
print(bridge_response_applied(scene), bridge_response_statuses(scene))

cue = bridge.run_sequence(
    [
        {"scene": {"brightness": 10}},
        {"preset": "key", "overrides": {"brightness": 45}},
        {"to": {"brightness": 60}, "steps": 4, "duration": 1.0},
    ],
    stop_on_unconfirmed=True,
)
print(cue["applied"], cue["reason"])

named = bridge.run_cue(
    {
        "steps": [{"scene": {"brightness": 10}}],
        "stop_on_unconfirmed": True,
    }
)
print(named["stopped"])
server_named = bridge.run_named_cue("warm-key", stop_on_unconfirmed=True)
print(server_named["cue"], server_named["applied"])
```

This wrapper preserves the bridge's JSON evidence fields, so callers should
still check `acknowledged`, `transport_status`, and `/state` rather than assuming
that a transmitted command was applied.
Use `integration()` when a controller needs one setup payload containing the
bridge manifest, capabilities, readiness, device discovery, and the client-side
control guard configuration.
Use `control_guard()`, `request_templates()`, `request_template(category, name)`,
`request_template_body(category, name)`, `request_template_query(category, name)`,
and `request_template_required_readiness(category, name)` to consume the
machine-readable request metadata without hand-parsing nested JSON.
It also includes `readiness_actions()`, `readiness_action(id)`, and
`pending_readiness_actions()` helpers for setup dashboards that consume
`GET /ready`. Create `LightBridgeClient(..., require_ready_for_controls=True)` to
guard every state-changing helper by default, or pass
`control_readiness=["confirmed_control"]` when your show workflow requires an
ACK-proven control request before running cues. Individual methods such as
`set_brightness()`, `apply_scene()`, and `run_named_cue()` also accept
`require_ready=True` to check `control_requests` before posting the command. Call
`LightBridgeClient(..., require_acknowledged_controls=True)` to fail closed after
posting any state-changing helper when the bridge returns `sent_no_response`,
`echoed_write`, or another unconfirmed status; the raised
`LightBridgeUnconfirmed` includes `payload`, `statuses`, and `reason`.
`require_acknowledged_response(payload)` applies the same ACK check to stored
bridge responses.
Call
`require_readiness("control_requests")` directly for explicit preflights, or
`wait_until_ready("read_status", timeout=5)` when a controller is starting
alongside the bridge. These raise `LightBridgeNotReady` with
`pending_action_ids` and `warnings` for UI prompts. Use `readiness_ready_for()`,
`readiness_ready()`, `readiness_all_ready()`, `readiness_require()`,
`readiness_requirements()`, `readiness_requirement()`,
`readiness_pending_action_ids()`, `readiness_unready_capabilities()`, and
`readiness_warnings()` when consuming preflight results outside the client
class.
Transport discovery payloads from `GET /devices` and the nested `devices` field
inside `GET /ready` have `devices_selected_usb_port()`,
`devices_usb_available()`, `devices_usb_ports()`, `devices_ble_status()`,
`devices_ble_authorization()`, `devices_ble_state()`,
`devices_ble_blocker()`, `devices_ble_scan_ok()`, and
`devices_ble_scan_devices()` helpers.
Use `connection_candidates_from_devices()`,
`connection_candidates_from_endpoint_report()`, `best_connection_config()`,
`usb_config_from_devices()`, `ble_config_from_scan()`,
`ble_config_from_candidate()`, and `ble_config_from_endpoint_report()` when a
host application wants to turn discovery evidence directly into ranked reusable
`LightConnectionConfig` objects for `open_light()`, `AsyncLightIntegration`, or
a rig fixture. Persist those configs with `save_light_connection_config()` and
restore them with `load_light_connection_config()` when an app wants to discover
USB/BLE routes during setup and reopen the same route later:

```python
from zhiyun_light_control import (
    LightIntegration,
    load_light_connection_config,
    save_light_connection_config,
)

config_path = "zhiyun-light.json"
integration = LightIntegration()
config = integration.best_connection_config(include_ble=True, include_ble_status=True)
save_light_connection_config(config, config_path)

integration = integration.with_config(load_light_connection_config(config_path))
print(integration.status()[1])
```

The client also exposes `devices_selected_usb_port()`,
`devices_ble_authorization()`, and `devices_ble_blocker()` convenience methods
that fetch the needed discovery payload before normalizing it.
Use `bridge_response_applied()`, `bridge_response_statuses()`, and
`bridge_response_reason()` to normalize ACK evidence across single commands,
scenes, transitions, sequences, and state/history responses.
Validation reports also have `validation_summary()`, `validation_ready_for()`,
`validation_ready()`, `validation_category()`, and
`validation_unconfirmed_names()` helpers for consuming `summary.ready_for` and
per-category evidence without hand-parsing the nested JSON.
Embedded SDK integrations expose the same guard semantics with
`integration.ready()`, `integration.pending_action_ids()`,
`integration.require_readiness()`, and `integration.require_control_ready()`;
the rig API also has `require_readiness()` and `require_readiness_all()` for
fixture-scoped preflights.
They also expose `connection_candidates()`, `best_connection_config()`,
`with_best_connection()`, `ble_endpoint_connection_candidates()`,
`best_ble_endpoint_config()`, and `with_ble_endpoint_connection()` so embedded
hosts can turn USB/BLE discovery and BLE endpoint ACK evidence into reusable
SDK configs without starting the HTTP bridge.
The integration facade also exposes direct control helpers:
`register()`, `read_brightness()`, `read_cct()`, `read_sleep()`,
`set_brightness()`, `set_cct()`, `set_sleep()`, `set_rgb()`, `set_hsi()`,
`apply_scene()`, `apply_preset()`, `run_sequence()`, `run_cue()`, and
`run_named_cue()`. Pass `require_ready=True` to state-changing helpers to check
`control_requests` before opening the transport, or combine it with
`require_acknowledged=True` to require the stricter `confirmed_control`
readiness preflight. Primitive read responses include decoded `value`, `obj`,
and `operation` fields when an ACK contains a parseable functional payload, while
the raw `CommandResult` evidence remains available under `result`. Direct
integration control updates the integration's own state tracker, so `state()`,
`state_snapshot()`, `state_history()`, and
`wait_for_state_update()` work without manually creating a controller.
For lower-level hosts, protocol primitives such as `RuntimeCommand`,
`UpdaterCommand`, `build_runtime_frame()`, `first_frame()`, and the
brightness/CCT/sleep/RGB/HSI payload parsers are exported from the package root,
so custom transports can stay on the public SDK surface instead of importing
private module internals.

Host applications can get the same setup model without starting the HTTP bridge
or shelling out to the CLI:

```python
from zhiyun_light_control import (
    CueLibrary,
    LightConnectionConfig,
    LightIntegration,
    ScenePresetLibrary,
)

presets = ScenePresetLibrary.from_mapping(
    {"scenes": {"key": {"brightness": 35, "kelvin": 5600}}}
)
cues = CueLibrary.from_mapping({"cues": {"intro": {"steps": [{"preset": "key"}]}}})

integration = LightIntegration(
    config=LightConnectionConfig(transport="usb", port="/dev/cu.usbmodem21301"),
    preset_library=presets,
    cue_library=cues,
)

ready = integration.readiness()
devices = integration.devices(include_ble_status=True)
snapshot = integration.snapshot(include_ble_status=True)
validation = integration.validate(include_object_reads=True)
usb_discovery = integration.discover_usb(object_ids=(0, 1), first_words=(0x0100,))
route_candidates = integration.connection_candidates(include_ble=True)
selected_integration = integration.with_best_connection(
    include_ble=True,
    persistent=True,
)
plan = integration.plan_named_cue("intro", start_seq=1)
integration.require_readiness("read_status")

print(ready["ready_for"])
print(devices["usb"]["selected_port"])
print(snapshot["summary"]["connection_confirmed"])
print(validation["summary"]["ready_for"])
print(usb_discovery["summary"]["confirmed_names"])
print([candidate.to_dict() for candidate in route_candidates])
print(selected_integration.config.to_dict())
print(plan["steps"])

primitive = integration.set_brightness(35, require_ready=True)
print(primitive["transport_status"], integration.state()["action"])
read_brightness = integration.read_brightness()
print(read_brightness.get("value"), read_brightness["transport_status"])

result = integration.apply_scene(
    {"brightness": 35, "kelvin": 5600},
    require_ready=True,
    require_acknowledged=True,
)
print(result["applied"], result["reason"])
print(integration.state_snapshot())
```

Async host applications can use the same setup/preflight model for native BLE
integrations without starting the HTTP bridge:

```python
import asyncio

from zhiyun_light_control import (
    AsyncLightIntegration,
    CueLibrary,
    LightConnectionConfig,
    ScenePresetLibrary,
)

presets = ScenePresetLibrary.from_mapping(
    {"scenes": {"key": {"brightness": 35, "kelvin": 5600}}}
)
cues = CueLibrary.from_mapping({"cues": {"intro": {"steps": [{"preset": "key"}]}}})


async def main() -> None:
    integration = AsyncLightIntegration(
        config=LightConnectionConfig(
            transport="ble",
            name_contains="MOLUS",
            ble_in_process=True,
        ),
        preset_library=presets,
        cue_library=cues,
    )

    ready = await integration.readiness(include_ble=True)
    devices = await integration.devices(include_ble=True)
    ble = await integration.inspect_ble(backend="macos-app")
    endpoint_test = await integration.test_ble_endpoints(backend="macos-app")
    routes = await integration.connection_candidates(include_ble=True)
    ble_routes = await integration.ble_endpoint_connection_candidates(
        backend="macos-app",
        require_confirmed=False,
    )
    selected = await integration.with_ble_endpoint_connection(
        backend="macos-app",
        require_confirmed=False,
    )
    validation = await integration.validate(include_object_reads=True)
    plan = integration.plan_named_cue("intro", start_seq=1)

    print(ready["ready_for"])
    print(devices["ble"]["included"])
    print(ble["endpoint_candidates"])
    print(endpoint_test["confirmed_candidates"])
    print([candidate.to_dict() for candidate in routes])
    print([candidate.to_dict() for candidate in ble_routes])
    print(selected.config.to_dict())
    print(validation["summary"]["ready_for"])
    print(plan["steps"])

    primitive = await integration.set_brightness(35, require_ready=True)
    print(primitive["transport_status"], integration.state()["action"])
    read_brightness = await integration.read_brightness()
    print(read_brightness.get("value"), read_brightness["transport_status"])

    result = await integration.apply_scene(
        {"brightness": 35},
        require_ready=True,
        require_acknowledged=True,
    )
    print(result["applied"], result["reason"])
    print(integration.state_snapshot())


asyncio.run(main())
```

```python
from zhiyun_light_control import (
    execute_command_plan,
    LightConnectionConfig,
    Scene,
    open_light,
    scene_command_plan,
    scene_command_specs,
    scene_frame_specs,
    transition_command_plans,
)

config = LightConnectionConfig(transport="usb", port="/dev/cu.usbmodem21301")
scene = Scene(obj=1, sleep=0, brightness=35, kelvin=5600)

print([command.to_dict() for command in scene_command_specs(scene)])
print([frame.to_dict() for frame in scene_frame_specs(scene, start_seq=1)])
print(scene_command_plan(scene, start_seq=1).to_dict())
print(
    [
        plan.to_dict()
        for plan in transition_command_plans(
            Scene(obj=1, brightness=10),
            scene,
            steps=4,
            start_seq=10,
        )
    ]
)

with open_light(config) as light:
    print(light.probe())
    light.register_confirmed(device_id=0)
    results = execute_command_plan(light, scene_command_plan(scene))
    print([result.to_dict() for result in results])
```

The `_confirmed` SDK helpers are transport-neutral: they work with USB, direct
BLE, isolated BLE, and custom transports, and they raise
`UnconfirmedCommandError` when a command is sent but not ACK-confirmed. Use
`command_results_acknowledged()`, `require_command_result()`, and
`require_command_results()` when you want to apply the same policy to raw
`CommandResult` objects. `open_light(LightConnectionConfig(...))` returns a
context manager with the same sync SDK methods for USB and for BLE via the sync
adapter, so host scripts can switch transports from configuration.
`scene_command_specs()`, `scene_frame_specs()`, `scene_command_plan()`, and
`transition_command_plans()` expose the exact ordered runtime commands and
serialized frame bytes before any transport is opened, so host applications can
preview, audit, log, or route commands through their own media-control systems.
The plan objects are plain Python data with `to_dict()` serializers, which keeps
them useful in CLI tools, daemons, timeline renderers, and custom transports.
`execute_command_plan()` / `execute_async_command_plan()` and the matching
transition helpers send those plan objects through any light object exposing
`exchange_runtime()`, including USB clients, sync BLE adapters, async BLE
clients, and test doubles. Use `execute_frame_plan()` /
`execute_async_frame_plan()` when a custom transport or discovery tool needs to
send the exact serialized frame bytes from the plan, including the planned first
word and sequence numbers.

For media-control code that wants presets and cues without running the HTTP
bridge, use the in-process controller:

```python
from zhiyun_light_control import (
    CueLibrary,
    LightConnectionConfig,
    LightController,
    ScenePresetLibrary,
)

presets = ScenePresetLibrary.from_mapping(
    {"scenes": {"key": {"brightness": 35, "kelvin": 5600}}}
)
cues = CueLibrary.from_mapping(
    {"cues": {"intro": {"steps": [{"preset": "key"}], "stop_on_unconfirmed": True}}}
)

controller = LightController(
    LightConnectionConfig(transport="usb"),
    preset_library=presets,
    cue_library=cues,
)

scene_plan = controller.plan_scene({"brightness": 20, "kelvin": 5600}, start_seq=1)
transition_plan = controller.plan_transition(
    {"brightness": 35, "kelvin": 5600},
    from_scene={"brightness": 20, "kelvin": 5600},
    steps=5,
    start_seq=scene_plan["next_seq"],
)
cue_plan = controller.plan_named_cue("intro", start_seq=transition_plan["next_seq"])
print(scene_plan["command_plan"]["frames"])
print(cue_plan["steps"])
result = controller.run_named_cue("intro", require_acknowledged=True)
print(result["applied"], result["reason"])
print(controller.state_snapshot())
print(controller.state_history(limit=5))
```

When driving a running HTTP bridge from another process, the same dry-run
planning surface is available through `LightBridgeClient.plan_scene()`,
`plan_preset()`, `plan_transition()`, and `plan_sequence()`. These methods only
resolve serialized runtime frames; they do not open the light or issue writes.

For multi-light setups, use named fixtures and a rig controller. Each fixture can
use its own USB or BLE `LightConnectionConfig`, and scene mappings without `obj`
inherit the fixture object id:

```python
from zhiyun_light_control import LightConnectionConfig, LightFixture, LightRig

rig = LightRig(
    [
        LightFixture(
            "key",
            LightConnectionConfig(transport="usb"),
            obj=1,
            tags=("set",),
        ),
        LightFixture(
            "rim",
            LightConnectionConfig(transport="ble", name_contains="MOLUS"),
            obj=1,
            tags=("set",),
        ),
    ],
    require_acknowledged=True,
)

preflight = rig.readiness_all(allow_control=True)
validation = rig.validate_all(include_object_reads=True)
print(preflight["applied"], validation["reason"])

look = rig.apply_scene_map(
    {
        "key": {"brightness": 35, "kelvin": 5600},
        "rim": {"brightness": 20, "kelvin": 3200},
    },
    stop_on_unconfirmed=True,
)
print(look["applied"], look["reason"])
print(rig.blackout(tag="set")["applied"])
```

Rig definitions can also be loaded from JSON so host projects can keep fixture
setup beside their show/media configuration:

```python
from zhiyun_light_control import load_rig

rig = load_rig("examples/rig.json")
print(rig.fixture_names())
print(rig.to_dict()["fixtures"])
```

For host applications that need one structured preflight payload for a full
fixture group, use the rig snapshot API. It returns the same manifest,
capabilities, readiness, device-discovery, and pending-action shape used by the
bridge, keyed by fixture:

```python
snapshot = rig.snapshot_all(allow_control=True, include_ble_status=True)
for name, item in snapshot["fixtures"].items():
    summary = item["snapshot"]["summary"]
    print(name, summary["ready_for"], summary["pending_action_ids"])
```

For event-loop based systems, use the async controller directly. This is the
preferred SDK surface for native BLE integrations on Linux, Windows, or macOS
when the host app already runs asyncio:

```python
import asyncio

from zhiyun_light_control import (
    AsyncLightController,
    CueLibrary,
    LightConnectionConfig,
    ScenePresetLibrary,
)


async def main() -> None:
    presets = ScenePresetLibrary.from_mapping(
        {"scenes": {"key": {"brightness": 35, "kelvin": 5600}}}
    )
    cues = CueLibrary.from_mapping(
        {
            "cues": {
                "intro": {
                    "steps": [{"preset": "key"}],
                    "stop_on_unconfirmed": True,
                }
            }
        }
    )
    config = LightConnectionConfig(transport="ble", name_contains="MOLUS")

    async with AsyncLightController(
        config,
        preset_library=presets,
        cue_library=cues,
        require_acknowledged=True,
    ) as controller:
        result = await controller.run_named_cue("intro")
        print(result["applied"], result["reason"])


asyncio.run(main())
```

Smooth transitions use the same scene model and work with both USB and BLE
clients:

```python
from zhiyun_light_control import Scene, ZhiyunLight

with ZhiyunLight.usb() as light:
    light.transition_scene(
        Scene(obj=1, brightness=10, kelvin=3200),
        Scene(obj=1, brightness=60, kelvin=5600),
        steps=8,
        duration=2.0,
        easing="ease-in-out",
    )
```

Map a DMX frame to the same scene model:

```python
from zhiyun_light_control import DmxMapping, LightConnectionConfig, open_light, scene_from_dmx

scene = scene_from_dmx(bytes([128, 255]), DmxMapping(obj=1))

with open_light(LightConnectionConfig(transport="usb", persistent=True)) as light:
    light.apply_scene(scene)
```

For integration debugging, use `exchange_runtime()` instead of the convenience
methods. It returns a `CommandResult` with the transmitted frame, raw response
bytes, parsed frames, echo detection, matching ACK, and a transport status:
`acknowledged`, `sent_no_response`, `echoed_write`, or
`response_without_matching_ack`.

For bench testing and release checks, use the same evidence model through the
validation API:

```python
from zhiyun_light_control import ZhiyunLight, read_sync_status, validate_sync_light

with ZhiyunLight.usb() as light:
    status = read_sync_status(light)
    report = validate_sync_light(
        light,
        allow_control=True,
        include_object_reads=True,
    )

print(status.to_dict()["connection_confirmed"])
print(report.to_dict()["unconfirmed"])
```

BLE is async. Use `isolated_ble()` for the same worker-protected behavior as
the CLI, `macos_ble_app()` for the bundled CoreBluetooth app helper on macOS,
or `ble()` when you explicitly want direct bleak execution:

```python
import asyncio
from zhiyun_light_control import AsyncZhiyunLight, Scene, read_async_status

async def main():
    async with AsyncZhiyunLight.isolated_ble(
        name_contains="MOLUS",
        profile="legacy",
    ) as light:
        print((await read_async_status(light)).to_dict())
        chip = await light.chip_sync()
        print(chip.updater_firmware if chip else None)
        await light.transition_scene_confirmed(
            Scene(obj=1, brightness=10),
            Scene(obj=1, brightness=35, kelvin=5600),
            steps=6,
            duration=1.5,
        )

asyncio.run(main())
```

`read_async_status()` uses the same updater-frame helpers as USB when the BLE
endpoint accepts arbitrary Zhiyun frames, so BLE status can include `chip_sync`
and `read_sn` evidence after Bluetooth authorization and endpoint discovery.

## Presets

Preset files can be a top-level mapping of names to scenes or an object with a
`scenes` mapping:

```json
{
  "scenes": {
    "key": {"brightness": 35, "kelvin": 5600},
    "blackout": {"sleep": 1}
  }
}
```

CLI fields override preset values when supplied. HTTP `/preset` accepts the same
scene fields plus `name`.

## BLE Notes

`zlight scan-ble` runs discovery in a worker process by default. This is
deliberate: on this Mac, fresh Python `3.13` and `3.12` virtualenvs with
`bleak 3.0.2` and PyObjC `12.1` both terminate CoreBluetooth scanning with
`SIGABRT` before Python can raise an exception. The worker wrapper keeps the main
process alive and reports `worker_python`, `returncode`, and `signal` fields.
One-shot BLE probe/status/control commands use the same worker isolation by
default; `--unsafe-in-process` is available for direct bleak runs on stable
runtimes. On the local Mac, `zlight status --transport ble` also returns a
structured `SIGABRT` worker diagnostic before any BLE ACK is received.
The `macos-app` backend builds a cached `ZhiyunBleScan.app` with
`NSBluetoothAlwaysUsageDescription` and runs a Swift CoreBluetooth helper inside
that bundle. A standalone native probe previously found the G60 as
`PL103_EDFE`; current `macos-app` scans are blocked until macOS Bluetooth
privacy authorizes `ZhiyunBleScan`. Use `zlight ble-helper --status --json` or
`GET /devices?include_ble_status=true` to expose the current helper
authorization state to local setup tools.
Use `inspect-ble --backend macos-app` after authorization to capture the live
GATT surface, including characteristic properties, before trying custom profile
overrides. Use `endpoint_candidates[0]["cli_args"]` as the first generated
command-profile override to test, or run `test-ble-endpoints` to confirm the
candidate with a read-only ACK before sending control frames.

BLE command exchange supports three named characteristic profiles:

- `direct`: Nordic-UART-style Zhiyun service `6e400001...`.
- `legacy`: ZY Vega direct service `0000fee9...` with `d44bc439...` characteristics.
- `yc`: older/alternate `0000ffe0...` light service.

Use `--ble-profile legacy` or `--ble-profile yc` on one-shot BLE commands and
bridge commands. For bench work against another firmware path, override the
selected profile with `--ble-service-uuid`, `--ble-write-uuid`, and
`--ble-notify-uuid`; the Python API exposes the same `profile`, `service_uuid`,
`write_uuid`, and `notify_uuid` arguments.

USB control, bridge code paths, and BLE module imports are verified. BLE control
still needs validation once the macOS helper is authorized or on a
Python/macOS/Bluetooth stack where bleak scanning is stable.

## Firmware

This repository only implements read-only updater identity commands. It does not
flash firmware, package firmware files, or replace Zhiyun's updater.

The locally verified firmware update result is:

```text
firmware: 1.6.4
generation: pl103
device_id: 0
chip_sync core: HDL
product: 0x0541
hardware: 0x0840
updater firmware: 1.64
read_sn product: 0x0541
read_sn device_identifier: 08a409e0c1100113
```

The latest local hardware pass, run against `/dev/cu.usbmodem21301`, confirmed
probe, global firmware/voltage/device-id reads, register-default-group, and
updater chip sync plus `readSn`. `zlight status --transport usb` also returned
ACK-backed status for firmware `1.6.4`, generation `pl103`, device id `0`, and
voltage/status `101`. It did not confirm USB brightness, CCT, sleep, RGB, HSI,
or object reads; runtime writes returned `sent_no_response` even when using the
official Vega `controlMode` byte `0x33`. A raw/default sleep write with
`payload_hex 01003301` was not ACK-confirmed; BLE advertisements disappeared
afterward, which is useful circumstantial evidence but not treated as confirmed
execution. A bounded
`discover-usb --control-first-words 0x0301` run produced exact echoed write
frames for sleep, brightness, CCT, and brightness-plus-mode probes, but those
are not ACKs and are not treated as applied control. Live HTTP bridge checks
confirmed `/probe` and `/register`; `/sleep` and `/brightness` transmitted and
returned `sent_no_response`.
A later bounded run with `--register-device-ids 0,1 --control-kinds sleep`
confirmed both registration ids ACK over USB, but `set_sleep_obj1` still
returned `sent_no_response`. Registering device id `1` changed the next probe's
reported `device_id` to `1`; re-registering device id `0` restored the original
probe state. A subsequent sleep-only matrix against first words `0x0001`,
`0x0100`, `0x0000`, `0x0101`, and `0x0301` confirmed that `0x0301` remains an
echo-only route while the other first words time out for object control. The
post-register read matrix also confirmed that registering device id `1` before
object reads does not unlock brightness, CCT, sleep, RGB, HSI, firmware-by-object,
voltage-by-object, mode, or identify reads on this G60; all nine returned
`sent_no_response`, then device id `0` was restored. macOS USB descriptors show a
single `Zhiyun Virtual ComPort` full-speed device
(`VID 0xfff8`, `PID 0x0180`) exposed as `/dev/cu.usbmodem21301`.

Zhiyun did not expose detailed release notes through the protocol data gathered
here, so behavior claims in this project are based on observed commands rather
than official firmware changelog text.

## Development

Use uv for all local Python work:

```sh
uv sync --extra ble --extra dev
uv run ruff check .
uv run python -m unittest discover -s tests
uv run python -m compileall -q src tests
uv build
```

Useful live checks:

```sh
uv run zlight probe --transport usb
uv run zlight discover-usb --object-ids 0,1
uv run --extra ble zlight scan-ble --timeout 8
```

`uv.lock` is committed so contributors test against the same resolved dependency
set. Avoid hand-managed virtualenv or pip workflows unless you are explicitly
testing downstream package installation.

## Protocol Notes

See [docs/protocol.md](docs/protocol.md) for frame layout, command ids, bridge
surface details, BLE services, and current validation notes.
