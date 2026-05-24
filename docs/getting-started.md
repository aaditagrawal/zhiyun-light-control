# Getting Started

This project is alpha hardware-control software for Zhiyun MOLUS lights. It has
been verified primarily with a MOLUS G60 on firmware `1.6.4`.

## Requirements

- Python `3.10` or newer.
- [`uv`](https://docs.astral.sh/uv/) for environment and package management.
- USB control uses `pyserial`.
- BLE support is optional and uses `bleak`.

On macOS, the light should usually stay on its PD power supply while the Mac is
connected with USB-C for data. The G60 presents as a USB CDC serial device.

## Install From A Checkout

For development and BLE support:

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

## Control Status

The verified USB path includes probe, firmware/status/device-id reads,
registration, and updater identity reads. Object-scoped control writes and BLE
command routing are exposed for experimentation, but callers should validate
them on their own light before using them in a live rig.

The API reports command evidence instead of pretending every write succeeded.
Unacknowledged writes return `applied: false` or raise when the caller requests
confirmed control.

Firmware flashing is intentionally not implemented. Use Zhiyun's official
updater for firmware writes.

## Next Steps

- Use [CLI](cli.md) for one-shot setup, validation, and bench commands.
- Use [Python SDK](python-sdk.md) for embedded applications.
- Use [HTTP bridge](bridge-http.md) when another local process should own the
  hardware connection.
- Use [BLE](ble.md) before attempting BLE command routing on macOS.
- Read [hardware-notes.md](hardware-notes.md) before relying on object control
  behavior observed on a single G60.
