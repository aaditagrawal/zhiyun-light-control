# Zhiyun Light Control

Experimental USB and BLE control library for Zhiyun MOLUS lights, started from live protocol work against a MOLUS G60 on macOS.

The current verified target is a MOLUS G60 updated to firmware `1.6.4`, visible as `Zhiyun Virtual ComPort` (`fff8:0180`) at `/dev/cu.usbmodem21301`.

## Status

Verified over USB:

- Device info command `0x2003`
- Firmware version command `0x8001`
- Voltage/status command `0x2001`
- Device id command `0x2005` (`0` before registration, `1` after registration in the current session)
- Register-to-default-group command `0x0006`
- Firmware sync/read identity commands `0x1300` and `0x1302`

Implemented but still experimental:

- Object-scoped brightness, CCT, sleep/power, RGB, HSI, and related controls.
- BLE transport using the direct ZY service and characteristics found in ZY Vega.
- Local JSON HTTP bridge for wider media-production automation.
- Scene application for media workflows that need to set several light properties together.

Firmware flashing is intentionally not part of this package. Use Zhiyun's official updater for firmware writes.

## Install For Development

```sh
cd /Users/mav/Documents/aurion/zhiyun-light-control
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[ble,dev]'
```

For USB-only work, the package has no runtime dependencies:

```sh
PYTHONPATH=src python3 -m zhiyun_light_control.cli probe
```

## CLI

Probe the attached USB light:

```sh
zlight probe --transport usb
```

Scan for BLE devices:

```sh
zlight scan-ble --timeout 8
```

BLE scans run in a worker process by default, because the local macOS CoreBluetooth/bleak stack can abort the interpreter instead of raising a Python exception. Use `--unsafe-in-process` only when debugging bleak itself.
If bleak is installed in a separate runtime, point the worker at it:

```sh
zlight scan-ble --python /path/to/venv/bin/python --timeout 8
```

Register the light to the default group over USB:

```sh
zlight register --device-id 0 --yes
```

Send an experimental brightness command:

```sh
zlight set brightness --obj 1 --value 35 --yes
```

Apply a simple scene:

```sh
zlight apply --obj 1 --sleep 0 --brightness 35 --kelvin 5600 --yes
```

Start the local HTTP bridge:

```sh
zlight serve --host 127.0.0.1 --port 8765 --allow-control
```

Example HTTP calls:

```sh
curl http://127.0.0.1:8765/probe
curl http://127.0.0.1:8765/commands
curl -X POST http://127.0.0.1:8765/brightness \
  -H 'content-type: application/json' \
  -d '{"obj": 1, "value": 35}'
curl -X POST http://127.0.0.1:8765/scene \
  -H 'content-type: application/json' \
  -d '{"obj": 1, "sleep": 0, "brightness": 35, "kelvin": 5600}'
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

For integration debugging, use `exchange_runtime()` instead of the convenience methods. It returns a `CommandResult` with the transmitted frame, raw response bytes, parsed frames, and matching ACK if one arrived.
`CommandResult.transport_status` is one of `acknowledged`, `sent_no_response`, or `response_without_matching_ack`, which is useful for fire-and-forget control paths where the current G60 does not ACK every object command.

BLE is async:

```python
import asyncio
from zhiyun_light_control import AsyncZhiyunLight, Scene

async def main():
    async with AsyncZhiyunLight.ble(name_contains="MOLUS") as light:
        print(await light.probe())
        await light.apply_scene(Scene(obj=1, brightness=35, kelvin=5600))

asyncio.run(main())
```

On the local Python `3.13` and `3.12` test environments, importing the BLE transport works but CoreBluetooth scanning via bleak aborts before returning a Python exception. USB tests and live USB probing are verified; BLE needs validation from a Python/macOS combination where bleak scanning is stable. The CLI's default `scan-ble` command isolates that crash in a worker process and reports it as JSON.

## Protocol Notes

See [docs/protocol.md](docs/protocol.md).
