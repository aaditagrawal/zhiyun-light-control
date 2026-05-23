"""Command line interface for Zhiyun light control."""

from __future__ import annotations

import argparse
import asyncio
import json

from .async_client import AsyncZhiyunLight
from .client import ZhiyunLight
from .models import CommandResult, Scene
from .protocol import (
    RuntimeCommand,
    build_runtime_frame,
    brightness_payload,
    cct_payload,
    first_frame,
    object_id_payload,
    register_payload,
    rgb_payload,
    sleep_payload,
)
from .server import serve
from .transports.ble import scan_zhiyun_devices, scan_zhiyun_devices_safe


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
    scan.add_argument(
        "--python",
        help="Python executable for the crash-isolated BLE worker.",
    )
    scan.add_argument(
        "--unsafe-in-process",
        action="store_true",
        help="Run bleak scan in this process instead of the crash-isolated worker.",
    )
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

    apply = sub.add_parser("apply", help="Apply a small lighting scene.")
    add_transport_args(apply)
    apply.add_argument("--obj", type=parse_int, default=1)
    apply.add_argument("--brightness", type=float)
    apply.add_argument("--kelvin", type=int)
    apply.add_argument("--sleep", type=int)
    apply.add_argument("--red", type=int)
    apply.add_argument("--green", type=int)
    apply.add_argument("--blue", type=int)
    apply.add_argument("--hue", type=float)
    apply.add_argument("--saturation", type=float)
    apply.add_argument("--intensity", type=int)
    apply.add_argument("--yes", action="store_true")
    apply.set_defaults(func=cmd_apply)

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
    if args.unsafe_in_process:
        devices = asyncio.run(scan_zhiyun_devices(timeout=args.timeout))
        print_json({"ok": True, "devices": [device.to_dict() for device in devices]})
        return 0
    result = scan_zhiyun_devices_safe(timeout=args.timeout, python=args.python)
    print_json(result.to_dict())
    return 0 if result.ok else 2


def cmd_register(args: argparse.Namespace) -> int:
    require_yes(args, "register changes the light/group runtime state")
    if args.transport == "ble":
        result = asyncio.run(_register_ble(args))
    else:
        with ZhiyunLight.usb(port=args.port, timeout=args.timeout) as light:
            result = light.exchange_runtime(
                RuntimeCommand.REGISTER_DEFAULT_GROUP,
                register_payload(args.device_id),
            )
    print_json(result.to_dict())
    return 0


async def _register_ble(args: argparse.Namespace):
    async with AsyncZhiyunLight.ble(
        address=args.address,
        name_contains=args.name_contains,
        timeout=args.timeout,
    ) as light:
        return await light.exchange_runtime(
            RuntimeCommand.REGISTER_DEFAULT_GROUP,
            register_payload(args.device_id),
        )


def cmd_read(args: argparse.Namespace) -> int:
    if args.transport == "ble":
        result = asyncio.run(_read_ble(args))
    else:
        with ZhiyunLight.usb(port=args.port, timeout=args.timeout) as light:
            result = _read_usb(light, args)
    print_json(result.to_dict())
    return 0


def _read_usb(light: ZhiyunLight, args: argparse.Namespace) -> CommandResult:
    cmd, payload = object_read_request(args.kind, args.obj)
    return light.exchange_runtime(cmd, payload)


async def _read_ble(args: argparse.Namespace) -> CommandResult:
    cmd, payload = object_read_request(args.kind, args.obj)
    async with AsyncZhiyunLight.ble(
        address=args.address,
        name_contains=args.name_contains,
        timeout=args.timeout,
    ) as light:
        return await light.exchange_runtime(cmd, payload)


def object_read_request(kind: str, obj: int) -> tuple[RuntimeCommand, bytes]:
    if kind == "brightness":
        return RuntimeCommand.BRIGHTNESS, brightness_payload(obj, read=True)
    if kind == "cct":
        return RuntimeCommand.CCT, cct_payload(obj, read=True)
    if kind == "sleep":
        return RuntimeCommand.SLEEP, sleep_payload(obj, read=True)
    if kind == "firmware-by-id":
        return RuntimeCommand.FIRMWARE_BY_OBJECT, object_id_payload(obj)
    if kind == "voltage-by-id":
        return RuntimeCommand.VOLTAGE_BY_OBJECT, object_id_payload(obj)
    if kind == "mode":
        return RuntimeCommand.DEVICE_MODE, object_id_payload(obj)
    raise AssertionError(kind)


def cmd_set(args: argparse.Namespace) -> int:
    require_yes(args, "set sends a control command to the light")
    if args.transport == "ble":
        result = asyncio.run(_set_ble(args))
    else:
        with ZhiyunLight.usb(port=args.port, timeout=args.timeout) as light:
            result = _set_usb(light, args)
    print_json(result.to_dict())
    return 0


def _set_usb(light: ZhiyunLight, args: argparse.Namespace):
    if args.kind == "brightness":
        value = require_value(args.value, "--value is required for brightness")
        return light.exchange_runtime(
            RuntimeCommand.BRIGHTNESS,
            brightness_payload(args.obj, value, read=False),
        )
    if args.kind == "cct":
        kelvin = (
            args.kelvin
            if args.kelvin is not None
            else int(require_value(args.value, "--kelvin or --value is required for cct"))
        )
        return light.exchange_runtime(
            RuntimeCommand.CCT,
            cct_payload(args.obj, kelvin, read=False),
        )
    if args.kind == "sleep":
        value = int(require_value(args.value, "--value is required for sleep"))
        return light.exchange_runtime(
            RuntimeCommand.SLEEP,
            sleep_payload(args.obj, value, read=False),
        )
    if args.kind == "rgb":
        if args.red is None or args.green is None or args.blue is None:
            raise SystemExit("--red, --green, and --blue are required for rgb")
        return light.exchange_runtime(
            RuntimeCommand.RGB,
            rgb_payload(args.obj, args.red, args.green, args.blue),
        )
    raise AssertionError(args.kind)


async def _set_ble(args: argparse.Namespace):
    async with AsyncZhiyunLight.ble(
        address=args.address,
        name_contains=args.name_contains,
        timeout=args.timeout,
    ) as light:
        if args.kind == "brightness":
            value = require_value(args.value, "--value is required for brightness")
            return await light.exchange_runtime(
                RuntimeCommand.BRIGHTNESS,
                brightness_payload(args.obj, value),
            )
        if args.kind == "cct":
            kelvin = (
                args.kelvin
                if args.kelvin is not None
                else int(require_value(args.value, "--kelvin or --value is required for cct"))
            )
            return await light.exchange_runtime(
                RuntimeCommand.CCT,
                cct_payload(args.obj, kelvin),
            )
        if args.kind == "sleep":
            value = int(require_value(args.value, "--value is required for sleep"))
            return await light.exchange_runtime(
                RuntimeCommand.SLEEP,
                sleep_payload(args.obj, value),
            )
        if args.kind == "rgb":
            if args.red is None or args.green is None or args.blue is None:
                raise SystemExit("--red, --green, and --blue are required for rgb")
            return await light.exchange_runtime(
                RuntimeCommand.RGB,
                rgb_payload(args.obj, args.red, args.green, args.blue),
            )
    raise AssertionError(args.kind)


def cmd_apply(args: argparse.Namespace) -> int:
    require_yes(args, "apply sends one or more control commands to the light")
    scene = scene_from_args(args)
    if args.transport == "ble":
        results = asyncio.run(_apply_ble(args, scene))
    else:
        with ZhiyunLight.usb(port=args.port, timeout=args.timeout) as light:
            results = light.apply_scene(scene)
    print_json({"scene": scene.to_dict(), "results": [result.to_dict() for result in results]})
    return 0


async def _apply_ble(args: argparse.Namespace, scene: Scene) -> list[CommandResult]:
    async with AsyncZhiyunLight.ble(
        address=args.address,
        name_contains=args.name_contains,
        timeout=args.timeout,
    ) as light:
        return await light.apply_scene(scene)


def scene_from_args(args: argparse.Namespace) -> Scene:
    return Scene(
        obj=args.obj,
        brightness=args.brightness,
        kelvin=args.kelvin,
        sleep=args.sleep,
        red=args.red,
        green=args.green,
        blue=args.blue,
        hue=args.hue,
        saturation=args.saturation,
        intensity=args.intensity,
    )


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
    cmd, payload = object_read_request(kind, obj)
    return build_runtime_frame(seq, cmd, payload)


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
