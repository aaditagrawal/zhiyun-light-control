"""Command line interface for Zhiyun light control."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .async_client import AsyncZhiyunLight
from .client import ZhiyunLight
from .protocol import (
    RuntimeCommand,
    build_runtime_frame,
    brightness_payload,
    cct_payload,
    first_frame,
    object_id_payload,
    rgb_payload,
    sleep_payload,
)
from .server import serve
from .transports.ble import scan_zhiyun_devices


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="Probe a USB or BLE light.")
    add_transport_args(probe)
    probe.add_argument("--json", action="store_true", help="Print compact JSON.")
    probe.set_defaults(func=cmd_probe)

    scan = sub.add_parser("scan-ble", help="Scan for likely Zhiyun BLE devices.")
    scan.add_argument("--timeout", type=float, default=5.0)
    scan.set_defaults(func=cmd_scan_ble)

    register = sub.add_parser("register", help="Register to the default group.")
    add_transport_args(register)
    register.add_argument("--device-id", type=parse_int, default=0)
    register.add_argument("--yes", action="store_true")
    register.set_defaults(func=cmd_register)

    read = sub.add_parser("read", help="Read an object-scoped command.")
    add_transport_args(read)
    read.add_argument(
        "kind",
        choices=["brightness", "cct", "sleep", "firmware-by-id", "voltage-by-id", "mode"],
    )
    read.add_argument("--obj", type=parse_int, default=1)
    read.set_defaults(func=cmd_read)

    set_cmd = sub.add_parser("set", help="Send an experimental control command.")
    add_transport_args(set_cmd)
    set_cmd.add_argument("kind", choices=["brightness", "cct", "sleep", "rgb"])
    set_cmd.add_argument("--obj", type=parse_int, default=1)
    set_cmd.add_argument("--value", type=float)
    set_cmd.add_argument("--kelvin", type=int)
    set_cmd.add_argument("--red", type=int)
    set_cmd.add_argument("--green", type=int)
    set_cmd.add_argument("--blue", type=int)
    set_cmd.add_argument("--yes", action="store_true")
    set_cmd.set_defaults(func=cmd_set)

    server = sub.add_parser("serve", help="Run a local JSON HTTP bridge.")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument("--light-port")
    server.add_argument("--allow-control", action="store_true")
    server.set_defaults(func=cmd_serve)

    return parser


def add_transport_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--transport", choices=["usb", "ble"], default="usb")
    parser.add_argument("--port", help="USB serial port. Defaults to first /dev/cu.usbmodem*.")
    parser.add_argument("--address", help="BLE address/identifier.")
    parser.add_argument("--name-contains", help="BLE name substring used for discovery.")
    parser.add_argument("--timeout", type=float, default=1.5)


def parse_int(text: str) -> int:
    return int(text, 0)


def cmd_probe(args: argparse.Namespace) -> int:
    if args.transport == "ble":
        result = asyncio.run(_probe_ble(args)).to_dict()
    else:
        with ZhiyunLight.usb(port=args.port, timeout=args.timeout) as light:
            result = light.probe().to_dict()
            chip = light.chip_sync()
            if chip:
                result["chip_sync"] = {
                    "core_id": chip.core_id,
                    "hardware": f"0x{chip.hardware:04x}",
                    "product": f"0x{chip.product:04x}",
                    "firmware_raw": chip.firmware_raw,
                    "updater_firmware": chip.updater_firmware,
                }
    print_json(result, compact=args.json)
    return 0


async def _probe_ble(args: argparse.Namespace):
    async with AsyncZhiyunLight.ble(
        address=args.address,
        name_contains=args.name_contains,
        timeout=args.timeout,
    ) as light:
        return await light.probe()


def cmd_scan_ble(args: argparse.Namespace) -> int:
    devices = asyncio.run(scan_zhiyun_devices(timeout=args.timeout))
    print_json([device.__dict__ for device in devices])
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    require_yes(args, "register changes the light/group runtime state")
    if args.transport == "ble":
        frame = asyncio.run(_register_ble(args))
    else:
        with ZhiyunLight.usb(port=args.port, timeout=args.timeout) as light:
            frame = light.register(device_id=args.device_id)
    print_json({"ack": frame.to_dict() if frame else None})
    return 0


async def _register_ble(args: argparse.Namespace):
    async with AsyncZhiyunLight.ble(
        address=args.address,
        name_contains=args.name_contains,
        timeout=args.timeout,
    ) as light:
        return await light.register(device_id=args.device_id)


def cmd_read(args: argparse.Namespace) -> int:
    if args.transport == "ble":
        raise SystemExit("BLE object reads are not wired in the CLI yet.")
    with ZhiyunLight.usb(port=args.port, timeout=args.timeout) as light:
        if args.kind == "brightness":
            frame = light.read_brightness(obj=args.obj)
        elif args.kind == "cct":
            frame = light.read_cct(obj=args.obj)
        elif args.kind == "sleep":
            frame = light.read_sleep(obj=args.obj)
        elif args.kind == "firmware-by-id":
            frame = light.get_object_firmware(obj=args.obj)
        elif args.kind == "voltage-by-id":
            frame = light.get_object_voltage(obj=args.obj)
        elif args.kind == "mode":
            frame = light.get_object_mode(obj=args.obj)
        else:
            raise AssertionError(args.kind)
    print_json({"frame": frame.to_dict() if frame else None})
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    require_yes(args, "set sends a control command to the light")
    if args.transport == "ble":
        frame = asyncio.run(_set_ble(args))
    else:
        with ZhiyunLight.usb(port=args.port, timeout=args.timeout) as light:
            frame = _set_usb(light, args)
    print_json({"ack": frame.to_dict() if frame else None})
    return 0


def _set_usb(light: ZhiyunLight, args: argparse.Namespace):
    if args.kind == "brightness":
        value = require_value(args.value, "--value is required for brightness")
        return light.set_brightness(obj=args.obj, value=value)
    if args.kind == "cct":
        kelvin = args.kelvin if args.kelvin is not None else int(require_value(args.value, "--kelvin or --value is required for cct"))
        return light.set_cct(obj=args.obj, kelvin=kelvin)
    if args.kind == "sleep":
        return light.set_sleep(obj=args.obj, value=int(require_value(args.value, "--value is required for sleep")))
    if args.kind == "rgb":
        if args.red is None or args.green is None or args.blue is None:
            raise SystemExit("--red, --green, and --blue are required for rgb")
        return light.set_rgb(obj=args.obj, red=args.red, green=args.green, blue=args.blue)
    raise AssertionError(args.kind)


async def _set_ble(args: argparse.Namespace):
    async with AsyncZhiyunLight.ble(
        address=args.address,
        name_contains=args.name_contains,
        timeout=args.timeout,
    ) as light:
        if args.kind == "brightness":
            return await light.set_brightness(args.obj, require_value(args.value, "--value is required for brightness"))
        if args.kind == "cct":
            kelvin = args.kelvin if args.kelvin is not None else int(require_value(args.value, "--kelvin or --value is required for cct"))
            return await light.set_cct(args.obj, kelvin)
        if args.kind == "sleep":
            return await light.set_sleep(args.obj, int(require_value(args.value, "--value is required for sleep")))
        if args.kind == "rgb":
            if args.red is None or args.green is None or args.blue is None:
                raise SystemExit("--red, --green, and --blue are required for rgb")
            return await light.set_rgb(args.obj, args.red, args.green, args.blue)
    raise AssertionError(args.kind)


def cmd_serve(args: argparse.Namespace) -> int:
    serve(
        host=args.host,
        port=args.port,
        light_port=args.light_port,
        allow_control=args.allow_control,
    )
    return 0


def require_yes(args: argparse.Namespace, reason: str) -> None:
    if not args.yes:
        raise SystemExit(f"Refusing: {reason}. Re-run with --yes.")


def require_value(value: float | None, message: str) -> float:
    if value is None:
        raise SystemExit(message)
    return value


def print_json(payload, *, compact: bool = False) -> None:
    kwargs = {"sort_keys": True} if compact else {"indent": 2, "sort_keys": True}
    print(json.dumps(payload, **kwargs))


def build_object_read_frame(kind: str, obj: int, seq: int) -> bytes:
    if kind == "brightness":
        return build_runtime_frame(seq, RuntimeCommand.BRIGHTNESS, brightness_payload(obj, read=True))
    if kind == "cct":
        return build_runtime_frame(seq, RuntimeCommand.CCT, cct_payload(obj, read=True))
    if kind == "sleep":
        return build_runtime_frame(seq, RuntimeCommand.SLEEP, sleep_payload(obj, read=True))
    if kind == "firmware-by-id":
        return build_runtime_frame(seq, RuntimeCommand.FIRMWARE_BY_OBJECT, object_id_payload(obj))
    if kind == "voltage-by-id":
        return build_runtime_frame(seq, RuntimeCommand.VOLTAGE_BY_OBJECT, object_id_payload(obj))
    if kind == "mode":
        return build_runtime_frame(seq, RuntimeCommand.DEVICE_MODE, object_id_payload(obj))
    raise AssertionError(kind)


def parse_object_read_response(kind: str, rx: bytes):
    cmd = {
        "brightness": RuntimeCommand.BRIGHTNESS,
        "cct": RuntimeCommand.CCT,
        "sleep": RuntimeCommand.SLEEP,
        "firmware-by-id": RuntimeCommand.FIRMWARE_BY_OBJECT,
        "voltage-by-id": RuntimeCommand.VOLTAGE_BY_OBJECT,
        "mode": RuntimeCommand.DEVICE_MODE,
    }[kind]
    return first_frame(rx, cmd=cmd)


__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
