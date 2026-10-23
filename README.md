# Zhiyun Light Control

[![CI](https://github.com/aaditagrawal/zhiyun-light-control/actions/workflows/ci.yml/badge.svg)](https://github.com/aaditagrawal/zhiyun-light-control/actions/workflows/ci.yml)

**Lighttime** is the current implementation in this repository: a ZHIYUN MOLUS
G60 controller, Python and TypeScript SDKs, and a local bridge for browser
controls, OBS Studio and production scripts. Authentication is enabled by
default. An Expo Android app provides a separate direct Bluetooth client.

The GitHub repository remains **Zhiyun Light Control**. Python's package,
import and CLI are now `lighttime`; the TypeScript package is `@lighttime/sdk`.
This is a breaking replacement of the previous `zhiyun_light_control` /
`zlight` implementation. See [the overhaul and migration notes](docs/overhaul.md).

## Why

The original project explored unsupported-device reverse engineering with
GPT‑5.5 xHigh in Codex. This overhaul continues that experiment with
**GPT‑6.1 Sol through Codex**, using parallel research, implementation and
review to turn protocol findings into reusable control tools. This time the
agent had access to Linux instead of macOS, along with the laptop webcam to
verify changes to the physical light. The user provided the G60 and requested
the SDK, browser, OBS and Android stack. Vendor-app analysis, captured replies,
automated regressions and real-device readback inform the implementation.

Subsequent effect exploration, USB analysis and Android enhancements also use
parallel **Claude Opus 5.5** agents, coordinated and physically tested by the
GPT‑6.1 Sol parent.

The result is an early-stage control toolkit validated on one G60:
**Linux BLE control and USB brightness/CCT writes are verified.** USB state
reads remain unanswered; reliable USB control uses Bluetooth readback. Other
MOLUS models have not been validated.

## Start on Linux

Install [uv](https://docs.astral.sh/uv/), then clone this repository. Python
3.13+ is required; `.python-version` pins the checkout's validation environment.
BlueZ must be running with a powered Bluetooth adapter. Release any other
controller's connection to the light before connecting. The tested G60 needed
no Bluetooth pairing or mesh reset.

```sh
git clone https://github.com/aaditagrawal/zhiyun-light-control.git
cd zhiyun-light-control
uv sync --frozen
uv run lighttime scan
uv run lighttime state --address YOUR_BLE_ADDRESS
uv run lighttime set --address YOUR_BLE_ADDRESS --brightness 10 --cct 4500
uv run lighttime serve --host 127.0.0.1 --address YOUR_BLE_ADDRESS --no-auth
```

This personal-testing mode connects to the light at startup. Open
`http://127.0.0.1:8877/` to go straight to its controls; the page attaches to
the bridge automatically. The browser, OBS and Python/TypeScript bridge clients
share one bridge-owned BLE connection. Stop the bridge before using a direct
Python, Web Bluetooth or Android connection.

Brightness is **0–100% in whole percentages**; CCT is **2700–6500 K in 100 K
steps**. Setters verify actual device readback after BLE writes. Off means
brightness zero; on restores the controller session's last nonzero brightness,
or 5% if none has been observed. It does not switch mains power.

For a personal page on another device, use the optional
[private Tailscale HTTPS setup](docs/python.md#private-https-access). For SDK
deployments, omit `--no-auth` to use the default bearer-token authentication;
`uv run lighttime token-path` prints the token file's location. See
[Python deployment](docs/python.md) and [browser deployment](docs/typescript.md)
for authenticated access and exact origin configuration.

## Python SDK

```python
import asyncio
from lighttime import LightClient

async def main():
    async with LightClient("YOUR_BLE_ADDRESS") as light:
        state = await light.set(brightness=10, cct=4500)
        print(state.brightness, state.cct)  # actual device readback

asyncio.run(main())
```

Use asynchronous `BridgeClient` when another process owns the BLE connection.
[Python setup and deployment](docs/python.md) covers wheel installation,
authentication and a Linux user-service template.

## Clients and effects

- [TypeScript SDK and browser controller](docs/typescript.md): `@lighttime/sdk`
  includes bridge and direct BLE transports; compatible secure-context browsers
  can use Web Bluetooth. The bridge-served browser path has been tested on G60.
- [OBS integration](docs/obs.md): Python script, hotkeys, program-scene presets
  and a browser dock. The HTTP worker was tested against the real bridge;
  an installed OBS UI session remains untested.
- [Expo Android app](docs/android.md): direct BLE using `react-native-ble-plx`.
  An arm64 standalone preview APK was built and inspected with **debug/test
  signing**. Physical installation, permission flow and BLE testing are deferred
  until the user connects a phone. Expo Go cannot load the native BLE module.
- [Shared effect contract](docs/effects-contract.md): fourteen patterns include
  blink, breathe, fades, temperature sweeps, heartbeat, candle/fire, lightning,
  sunrise/sunset, white bursts, camera flashes, TV glow, Morse and keyframes.
  The bridge owns timing and cancellation across clients. All fourteen,
  stop/restore and manual override have been tested on the G60; see the
  [effect guide](docs/effects.md) and validation record.

## Validation and USB limits

On the tested Linux G60 (`PL103`, manufacturer `0x0905`), BLE queries confirmed
2%, 12%, off, 2700 K warm and 6500 K cool. Camera observations supported the
relative brightness/color changes; they are not calibrated output or Kelvin
measurements. Python, TypeScript, browser and OBS-worker bridge paths also
received real-device readback. The initial 5% / 5000 K was restored. See the
[validation record](docs/validation.md) for evidence and remaining targets.

The USB device enumerates as `fff8:0180`, `Zhiyun Virtual ComPort`, and Linux
binds `cdc_acm` automatically:

```sh
uv run lighttime usb-info --port /dev/ttyACM0
```

Identity queries work. Single, isolated `00 01` USB writes changed brightness
and CCT, and all 60 repeated write cases matched independent Bluetooth
readback. USB brightness/CCT reads still did not answer; a serial echo does
not establish success. Opt into the verified hybrid using
`--usb-writes /dev/ttyACM0` on `serve` or `set`; see the
[USB SDK guide](docs/usb.md), [USB research](docs/research/usb-deep.md) and the
[ramp and timing experiments](docs/experiments.md). If access is denied, review
[`scripts/install-usb-access.sh`](scripts/install-usb-access.sh) and its
device-specific `uaccess` rule before installing it with sudo. No firmware
flasher is included. Windows/macOS hardware and direct Web Bluetooth remain
unverified.

## Build and check

No PyPI, npm or app-store publication has been performed. Build Python
distributions with `uv build --out-dir artifacts/distributions`. For the SDK
and browser, install the manifest-pinned Bun and run:

```sh
cd packages/sdk
bun install --frozen-lockfile
bun test
bun run typecheck
bun run build
bun run build:browser
```

From the repository root:

```sh
uv run --frozen pytest -q
uv run --frozen ruff check src tests scripts integrations/obs
uv run --frozen ty check
uv audit --frozen
```

[CI](.github/workflows/ci.yml) checks frozen Python 3.13/3.14 environments,
the SDK tests/build/types, and Android tests/types. It performs no physical
hardware or native Android build. Android has a documented unresolved high
Expo CLI dependency advisory; see [Android validation](docs/android.md).
[`scripts/hardware_verify.py`](scripts/hardware_verify.py) is an explicit
physical test that changes the light and can capture camera images. Normal
tests use mocks and local test servers.

## Protocol research

The official Android AAB and macOS updater were analyzed for UUIDs, framing,
CRC and command formats, then compared with captured G60 replies. The SDK is
independently implemented from those protocol facts:

- [Device and HTTP API contract](docs/api-contract.md)
- [Official app analysis and download hashes](docs/research/official-apps.md)
- [Existing implementations and license notes](docs/research/existing-protocols.md)
- [USB/updater research](docs/research/rea-usb.md)

Vendor binaries, decompiled trees, camera photos, build artifacts and private
tokens are excluded from the source distribution. Licensed under [MIT](LICENSE).
