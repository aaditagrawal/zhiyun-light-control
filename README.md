# Zhiyun Light Control

[![CI](https://github.com/aaditagrawal/zhiyun-light-control/actions/workflows/ci.yml/badge.svg)](https://github.com/aaditagrawal/zhiyun-light-control/actions/workflows/ci.yml)

Python SDK and bridge tooling for controlling Zhiyun MOLUS lights over USB CDC
and BLE. The package is aimed at local media-production automation: scripts,
show controllers, OSC/DMX bridges, and custom control surfaces.

## Status

This project is alpha hardware-control software.

- Verified target: Zhiyun MOLUS G60 on firmware `1.6.4`.
- Verified path: USB probe, firmware/status/device-id reads, registration, and
  updater identity reads.
- Experimental path: object-scoped control writes and BLE command routing. The
  SDK exposes these primitives, but callers should validate them on their own
  light before using them in a live rig.
- Firmware flashing is intentionally not implemented. Use Zhiyun's official
  updater for firmware writes.

The API reports command evidence instead of pretending every write succeeded.
Unacknowledged writes return `applied: false` or raise when the caller requests
confirmed control.

## Features

- Synchronous and asynchronous Python clients.
- Transport-neutral connection configs for USB and BLE.
- Discovery, readiness, validation, setup profiles, and state events.
- Direct SDK controllers for scenes, presets, cues, transitions, plans, and
  named multi-fixture rigs.
- Local HTTP JSON bridge and stdlib HTTP client.
- OSC, Art-Net, and sACN bridge servers for media tools.
- JSON-serializable plans and rig/project files.

## Requirements

- Python `3.10` or newer.
- [`uv`](https://docs.astral.sh/uv/) for environment and package management.
- USB control uses `pyserial`.
- BLE support is optional and uses `bleak`.

On macOS, the light should usually stay on its PD power supply while the Mac is
connected with USB-C for data. The G60 presents as a USB CDC serial device.

## Install

From a published package, after the first PyPI release:

```sh
uv tool install zhiyun-light-control
```

For runtime use in an existing project:

```sh
pip install zhiyun-light-control
uv add zhiyun-light-control
```

BLE support is optional:

```sh
pip install "zhiyun-light-control[ble]"
uv add "zhiyun-light-control[ble]"
```

This package is not published to PyPI yet. Until a release is published, install
from a checkout.

For development from a checkout:

```sh
uv sync --extra ble --extra dev
```

For runtime-only USB use:

```sh
uv sync
```

The package installs a `zlight` command in the uv environment:

```sh
uv run zlight --help
```

## Quick Hardware Check

List local transport state:

```sh
uv run zlight devices --include-ble-status --json
```

Probe the first detected USB CDC light:

```sh
uv run zlight probe --transport usb --json
```

Read ACK-backed status:

```sh
uv run zlight status --transport usb --json
```

If auto-selection picks the wrong port, pass the serial path explicitly:

```sh
uv run zlight status --transport usb --port /dev/cu.usbmodem1101 --json
```

Run a structured validation report:

```sh
uv run zlight validate --transport usb --include-object-reads --json
```

Control probes are opt-in:

```sh
uv run zlight validate --transport usb --allow-control --include-object-reads --json
```

Use `--strict` in automation when every attempted command must be ACK-confirmed.

## Python SDK

Read-only direct USB probe:

```python
from zhiyun_light_control import LightConnectionConfig, LightController

config = LightConnectionConfig.usb()

with LightController(config) as light:
    probe = light.probe()
    print(probe.to_dict())
```

Apply a scene only after you have validated that control writes work for your
transport and firmware:

```python
from zhiyun_light_control import LightConnectionConfig, LightController, Scene

config = LightConnectionConfig.usb(port="/dev/cu.usbmodem1101")

with LightController(config, require_acknowledged=True) as light:
    light.register(device_id=0, group_id=0)
    result = light.apply_scene(Scene(obj=1, brightness=35, kelvin=5600))
    print(result["applied"])
```

Use the higher-level integration layer when an application needs setup evidence
before exposing controls:

```python
from zhiyun_light_control import LightConnectionConfig, LightIntegration

integration = LightIntegration(LightConnectionConfig.usb())
setup = integration.setup_report(include_ble_status=True)

if setup["ready"]:
    print(setup["summary"])
```

See [examples/sdk_quickstart.py](examples/sdk_quickstart.py) for a fuller
discover, persist, validate, and control flow.

## CLI Control

Low-level primitives:

```sh
uv run zlight register --transport usb --yes
uv run zlight read brightness --transport usb --obj 1
uv run zlight set brightness --transport usb --obj 1 --value 35 --yes
uv run zlight apply --transport usb --brightness 35 --kelvin 5600 --yes
```

For the locally observed G60 echo route, make the frame first word explicit:

```sh
uv run zlight apply --transport usb --first-word 0x0301 --accept-echo --sleep 0 --brightness 50 --kelvin 3200 --yes
```

This route may report `echoed_write`, not an ACK. The CLI keeps that distinction
visible even when `--accept-echo` lets shell automation treat the local
echo route as an accepted transport result. Physical testing showed that a
broad `0x0301` candidate pass reached `2700K` at `20%`; exact brightness/CCT
control routing is still being narrowed down.

For macOS BLE authorization diagnostics, the helper can use a fresh bundle
identity:

```sh
uv run zlight ble-helper --ensure --authorize \
  --bundle-name ZhiyunBleScanFresh \
  --bundle-id local.zhiyun-light-control.ble-scan-fresh \
  --timeout 60 --json
```

Build a plan without opening hardware:

```sh
uv run zlight plan --brightness 35 --kelvin 5600 --output scene-plan.json
```

Execute the plan later over USB/BLE or through the HTTP bridge:

```sh
uv run zlight execute-plan scene-plan.json --transport usb --yes
```

BLE discovery and endpoint checks:

```sh
uv run zlight scan-ble --name-contains MOLUS
uv run zlight inspect-ble --name-contains MOLUS --json
uv run zlight test-ble-endpoints --name-contains MOLUS --json
uv run zlight mesh-probe --backend macos-app --name-contains PL103 --json
uv run --extra mesh zlight mesh-handshake --name-contains PL103 --json
uv run --extra mesh zlight mesh-session --name-contains PL103 --json
uv run --extra mesh zlight mesh-provision-plan --session-json session.json --json
```

`mesh-probe` sends a Bluetooth Mesh provisioning invite on `1827/2ADB` and
decodes capabilities from `2ADC`. On the local G60 this confirms the light is
reachable as an unprovisioned mesh node; it is a setup-discovery primitive, not
finished brightness/CCT control.

`mesh-handshake` keeps one CoreBluetooth connection open and sends the invite,
no-OOB provisioning start, and provisioner public key. It requires the `mesh`
extra for P-256 key generation. A complete response should include the
provisionee public key and derived ECDH secret; this is still pre-control setup,
not a finished light-output command.

`mesh-session` extends that flow with per-session confirmation and random
frames, computed after the G60 returns its public key and sent on the same BLE
connection. It intentionally stops before provisioning data, so it does not
persistently add the light to a generated mesh network.

`mesh-provision-plan` is the next offline builder: it consumes a complete
`mesh-session` JSON transcript and derives the encrypted provisioning-data PDU,
network key metadata, session nonce/key, and device key. It does not send
anything to the light; sending that PDU is a later explicit provisioning step.

When BLE discovery unexpectedly returns no Zhiyun devices on macOS, widen the
scan before changing protocol code:

```sh
uv run zlight scan-ble --backend macos-app --include-all --timeout 10
```

For lower-level provisioning work, import from `zhiyun_light_control.mesh`.
The module includes confirmation/random helpers and encrypted provisioning-data
builders that match the Nordic Mesh flow used by Zhiyun Vega.

## Local Bridges

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

Start media-control bridge servers:

```sh
uv run zlight osc-serve --transport usb --port 9000 --allow-control
uv run zlight artnet-serve --transport usb --universe 0 --allow-control
uv run zlight sacn-serve --transport usb --universe 1 --allow-control
```

## Multi-Fixture Rigs

Rig files are JSON and can mix USB and BLE fixtures:

```json
{
  "fixtures": {
    "key": {"transport": "usb", "obj": 1, "tags": ["set"]},
    "rim": {"transport": "ble", "name_contains": "MOLUS", "obj": 1}
  },
  "presets": {
    "scenes": {
      "interview": {"brightness": 35, "kelvin": 5600},
      "blackout": {"sleep": 1}
    }
  },
  "require_acknowledged": true
}
```

Programmatic use:

```python
from zhiyun_light_control import LightRig

rig = LightRig.load("examples/rig.json")
print(rig.setup_report_all())
```

## Documentation

- [docs/index.md](docs/index.md): task-oriented docs for setup, CLI, SDK,
  bridges, BLE, rigs, hardware notes, and protocol reference.
- [design.md](design.md): SDK architecture and design choices.
- [CHANGELOG.md](CHANGELOG.md): release history.
- [examples/](examples): sample scene, cue, rig, project, and SDK files.

## Development

Run the same local checks as CI:

```sh
uv sync --extra ble --extra dev
uv run ruff check .
uv run pytest -q
uv run python -m compileall -q src tests examples/sdk_quickstart.py
rg -n "\bAny\b|typing\.Any|from typing import .*Any" src tests
uv build
```

The `rg` command should exit with no matches.

## Release Readiness

Before publishing a release:

```sh
uv sync --extra ble --extra dev
uv run ruff check .
uv run pytest -q
uv run python -m compileall -q src tests examples/sdk_quickstart.py
uv build
```

Manual release steps still required:

- Confirm `CHANGELOG.md` has the release notes.
- Confirm the version in `pyproject.toml`.
- Check the built files in `dist/`.
- Publish to PyPI.
- Create and push the matching git tag.
- Create the GitHub release.

## License

MIT. See [LICENSE](LICENSE).
