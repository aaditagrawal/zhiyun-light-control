# Zhiyun Light Control

Python tooling for controlling Zhiyun MOLUS lights over USB CDC and, where the
local Bluetooth stack is stable, BLE. The project started from live protocol
work against a MOLUS G60 on macOS and is built for local media-production
automation: command line control, Python APIs, HTTP, OSC, Art-Net, and sACN.

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
- BLE transport using the direct ZY light service and characteristics seen in ZY Vega.
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
- A USB data cable for USB control. Some C-to-C charging cables do not enumerate
  the serial interface; a known data-capable USB-A-to-C cable through an adapter
  is a useful fallback.
- For a MOLUS G60, keep the light on its normal PD power supply while using USB
  data from the computer.

## Quick Start

From this repository:

```sh
cd /Users/mav/Documents/aurion/zhiyun-light-control
uv sync --extra ble --extra dev
uv run zlight probe --transport usb
```

USB-only operation has no runtime dependencies, so this also works for the
smallest environment:

```sh
uv sync
uv run zlight probe --transport usb
```

If the light is not found automatically, pass the serial port explicitly:

```sh
uv run zlight probe --transport usb --port /dev/cu.usbmodem21301
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
exit unless every attempted command was ACK-confirmed.

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
uv run zlight discover-usb --allow-control --register-device-ids 0,1 --control-object-ids 1 --control-kinds sleep
```

`discover-usb` is for bench work. It records global reads, object-read
candidates, first-word probes, and optional safe control candidates with the
same ACK/timeout/echo evidence model used by validation.
When `--allow-control` is set, control probes default to the same object ids as
`--object-ids`; use `--control-object-ids` and `--control-first-words` to test a
bounded control matrix without expanding read probes. Use
`--register-device-ids`, `--register-group-ids`, and `--control-kinds` to
separate registration hypotheses from the specific control candidates you want
to transmit.

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

Probe:

```sh
uv run zlight probe --transport usb
uv run zlight probe --transport ble --name-contains MOLUS
```

Scan BLE devices:

```sh
uv run --extra ble zlight scan-ble --timeout 8
```

BLE `probe`, `register`, `read`, `set`, and `apply` run through a worker
process by default. That keeps the parent CLI/API process alive if
CoreBluetooth aborts below Python. Pass `--unsafe-in-process` only when you
want direct bleak execution on a stable Bluetooth runtime.

Full BLE validation still uses the direct bleak transport. Because the local
macOS CoreBluetooth stack can abort the interpreter, the CLI requires an
explicit opt-in for direct BLE validation:

```sh
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
`--unsafe-in-process` only on a stable BLE runtime where you want direct bleak
execution, and add `--no-persistent-light` when debugging connection setup.

HTTP JSON bridge:

```sh
uv run zlight serve --host 127.0.0.1 --port 8765 --preset-file examples/scenes.json --allow-control
```

The HTTP bridge serves JSON CORS headers for browser dashboards by default.
Use `--cors-origin http://studio.local` to restrict the allowed origin or
`--cors-origin none` to disable CORS headers.

BLE bridge example:

```sh
uv run --extra ble zlight serve --transport ble --name-contains MOLUS --allow-control
```

Useful HTTP calls:

```sh
curl http://127.0.0.1:8765/probe
curl http://127.0.0.1:8765/status
curl http://127.0.0.1:8765/openapi.json
curl http://127.0.0.1:8765/validate
curl http://127.0.0.1:8765/commands
curl http://127.0.0.1:8765/state
curl http://127.0.0.1:8765/presets
curl -X POST http://127.0.0.1:8765/validate \
  -H 'content-type: application/json' \
  -d '{"allow_control": true, "include_object_reads": true}'
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
```

For `/transition`, omit `from` to use the bridge's last requested state for the
same object id.

`GET /state` reports the last requested bridge scene and the command transport
statuses behind it. Its `applied` field is ACK-based: it is `true` only when all
command results for that request were acknowledged by the light. If a command
was sent but not confirmed, `applied` is `false` and `reason` carries values
such as `sent_no_response` or `echoed_write`.

`GET /status` returns read-only identity/status fields plus the raw
`CommandResult` evidence for global device info, firmware, voltage/status,
device id, and updater chip sync when that transport exposes it.

`GET /validate` returns the same hardware-evidence report as `zlight validate`
without transmitting control writes. `POST /validate` accepts
`allow_control`, `include_object_reads`, `include_color`, `obj`, and the same
test values as the CLI. Write checks only run when the bridge was started with
`--allow-control`.

OSC bridge:

```sh
uv run zlight osc-serve --host 127.0.0.1 --port 9000 --allow-control
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
```

The `/light/...` prefix is accepted as an alias.

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

```python
from zhiyun_light_control import Scene, ZhiyunLight

with ZhiyunLight.usb() as light:
    print(light.probe())
    light.register(device_id=0)
    light.set_brightness(obj=1, value=35)
    light.set_cct(obj=1, kelvin=5600)
    results = light.apply_scene(Scene(obj=1, sleep=0, brightness=35, kelvin=5600))
    print([result.to_dict() for result in results])
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
from zhiyun_light_control import DmxMapping, LightConnectionConfig, make_light_factory, scene_from_dmx

scene = scene_from_dmx(bytes([128, 255]), DmxMapping(obj=1))

with make_light_factory(LightConnectionConfig(transport="usb", persistent=True))() as light:
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
from zhiyun_light_control import ZhiyunLight, validate_sync_light

with ZhiyunLight.usb() as light:
    report = validate_sync_light(
        light,
        allow_control=True,
        include_object_reads=True,
    )

print(report.to_dict()["unconfirmed"])
```

BLE is async. Use `isolated_ble()` for the same worker-protected behavior as
the CLI, or `ble()` when you explicitly want direct bleak execution:

```python
import asyncio
from zhiyun_light_control import AsyncZhiyunLight, Scene

async def main():
    async with AsyncZhiyunLight.isolated_ble(
        name_contains="MOLUS",
        profile="legacy",
    ) as light:
        print(await light.probe())
        await light.transition_scene(
            Scene(obj=1, brightness=10),
            Scene(obj=1, brightness=35, kelvin=5600),
            steps=6,
            duration=1.5,
        )

asyncio.run(main())
```

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
One-shot BLE probe/control commands use the same worker isolation by default;
`--unsafe-in-process` is available for direct bleak runs on stable runtimes.

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
still needs validation on a Python/macOS/Bluetooth stack where bleak scanning is
stable.

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
```

The latest local hardware pass, run against `/dev/cu.usbmodem21301`, confirmed
probe, global firmware/voltage/device-id reads, register-default-group, and
updater chip sync. It did not confirm USB brightness, CCT, sleep, RGB, HSI, or
object reads; normal runtime writes returned `sent_no_response`. A bounded
`discover-usb --control-first-words 0x0301` run produced exact echoed write
frames for sleep, brightness, CCT, and brightness-plus-mode probes, but those
are not ACKs and are not treated as applied control. Live HTTP bridge checks
confirmed `/probe` and `/register`; `/sleep` and `/brightness` transmitted and
returned `sent_no_response`.
A later bounded run with `--register-device-ids 0,1 --control-kinds sleep`
confirmed both registration ids ACK over USB, but `set_sleep_obj1` still
returned `sent_no_response`. Registering device id `1` changed the next probe's
reported `device_id` to `1`; re-registering device id `0` restored the original
probe state.

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
