"""Command line interface for Zhiyun light control."""

from __future__ import annotations

import argparse
import asyncio
import json

from .artnet import DmxMapping, serve_artnet
from .async_client import AsyncZhiyunLight
from .bridge import LightConnectionConfig, make_light_factory
from .client import ZhiyunLight
from .discovery import (
    DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS,
    DEFAULT_DISCOVERY_FIRST_WORDS,
    DEFAULT_DISCOVERY_OBJECT_IDS,
    discover_usb_primitives,
)
from .models import CommandResult, Scene
from .osc import serve_osc
from .presets import ScenePresetLibrary, merge_scene
from .protocol import (
    RuntimeCommand,
    brightness_payload,
    build_runtime_frame,
    cct_payload,
    first_frame,
    object_id_payload,
    register_payload,
    rgb_payload,
    sleep_payload,
)
from .sacn import serve_sacn
from .server import serve
from .transports.ble import (
    BLE_PROFILE_NAMES,
    DEFAULT_BLE_PROFILE,
    BleWorkerError,
    scan_zhiyun_devices,
    scan_zhiyun_devices_safe,
)
from .transports.usb import DEFAULT_LOCK_TIMEOUT
from .validation import validate_async_light, validate_sync_light


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
    add_ble_execution_args(probe)
    probe.add_argument("--json", action="store_true", help="Print compact JSON.")
    probe.set_defaults(func=cmd_probe)

    validate = sub.add_parser(
        "validate",
        help="Run a structured hardware validation report.",
    )
    add_transport_args(validate)
    add_ble_profile_args(validate)
    validate.add_argument("--allow-control", action="store_true")
    validate.add_argument("--include-object-reads", action="store_true")
    validate.add_argument("--include-color", action="store_true")
    validate.add_argument(
        "--unsafe-in-process",
        action="store_true",
        help="Allow direct BLE validation in this process.",
    )
    validate.add_argument("--device-id", type=parse_int, default=0)
    validate.add_argument("--obj", type=parse_int, default=1)
    validate.add_argument("--brightness", type=float, default=35.0)
    validate.add_argument("--kelvin", type=int, default=5600)
    validate.add_argument("--sleep", type=int, default=0)
    validate.add_argument("--red", type=int, default=255)
    validate.add_argument("--green", type=int, default=255)
    validate.add_argument("--blue", type=int, default=255)
    validate.add_argument("--hue", type=float, default=0.0)
    validate.add_argument("--saturation", type=float, default=0.0)
    validate.add_argument("--intensity", type=int, default=35)
    validate.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero unless every attempted command is confirmed by ACK.",
    )
    validate.add_argument("--json", action="store_true", help="Print compact JSON.")
    validate.set_defaults(func=cmd_validate)

    discover = sub.add_parser(
        "discover-usb",
        help="Run a bounded USB protocol discovery matrix.",
    )
    discover.add_argument(
        "--port", help="USB serial port. Defaults to first /dev/cu.usbmodem*."
    )
    discover.add_argument("--timeout", type=float, default=0.5)
    discover.add_argument(
        "--usb-lock-timeout",
        type=parse_optional_float,
        default=DEFAULT_LOCK_TIMEOUT,
        help="Seconds to wait for the USB port lock. Use 'none' to wait forever.",
    )
    discover.add_argument(
        "--object-ids",
        type=parse_int_list,
        default=DEFAULT_DISCOVERY_OBJECT_IDS,
        help="Comma-separated object ids to probe. Default: 0,1.",
    )
    discover.add_argument(
        "--first-words",
        type=parse_int_list,
        default=DEFAULT_DISCOVERY_FIRST_WORDS,
        help="Comma-separated frame first-word values to probe.",
    )
    discover.add_argument(
        "--control-object-ids",
        type=parse_int_list,
        help=(
            "Comma-separated object ids for control probes. Defaults to "
            "--object-ids when --allow-control is set."
        ),
    )
    discover.add_argument(
        "--control-first-words",
        type=parse_int_list,
        default=DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS,
        help="Comma-separated first-word values for gated control probes.",
    )
    discover.add_argument("--allow-control", action="store_true")
    discover.add_argument("--brightness", type=float, default=35.0)
    discover.add_argument("--kelvin", type=int, default=5600)
    discover.add_argument("--sleep", type=int, default=0)
    discover.add_argument("--json", action="store_true", help="Print compact JSON.")
    discover.set_defaults(func=cmd_discover_usb)

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

    frame = sub.add_parser("frame", help="Exchange one raw frame.")
    add_transport_args(frame)
    add_ble_execution_args(frame)
    frame.add_argument("--first-word", type=parse_int, required=True)
    frame.add_argument("--command", dest="raw_command", type=parse_int, required=True)
    frame.add_argument("--payload-hex", type=parse_hex_bytes, default=b"")
    frame.add_argument("--yes", action="store_true")
    frame.set_defaults(func=cmd_frame)

    register = sub.add_parser("register", help="Register to the default group.")
    add_transport_args(register)
    add_ble_execution_args(register)
    register.add_argument("--device-id", type=parse_int, default=0)
    register.add_argument("--yes", action="store_true")
    register.set_defaults(func=cmd_register)

    read = sub.add_parser("read", help="Read an object-scoped command.")
    add_transport_args(read)
    add_ble_execution_args(read)
    read.add_argument(
        "kind",
        choices=[
            "brightness",
            "cct",
            "sleep",
            "firmware-by-id",
            "voltage-by-id",
            "mode",
        ],
    )
    read.add_argument("--obj", type=parse_int, default=1)
    read.set_defaults(func=cmd_read)

    set_cmd = sub.add_parser("set", help="Send an experimental control command.")
    add_transport_args(set_cmd)
    add_ble_execution_args(set_cmd)
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
    add_ble_execution_args(apply)
    apply.add_argument("--obj", type=parse_int)
    apply.add_argument("--brightness", type=float)
    apply.add_argument("--kelvin", type=int)
    apply.add_argument("--sleep", type=int)
    apply.add_argument("--red", type=int)
    apply.add_argument("--green", type=int)
    apply.add_argument("--blue", type=int)
    apply.add_argument("--hue", type=float)
    apply.add_argument("--saturation", type=float)
    apply.add_argument("--intensity", type=int)
    apply.add_argument(
        "--preset-file", help="JSON file containing named scene presets."
    )
    apply.add_argument("--preset", help="Named preset to apply before CLI overrides.")
    apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the scene without sending commands.",
    )
    apply.add_argument("--yes", action="store_true")
    apply.set_defaults(func=cmd_apply)

    server = sub.add_parser("serve", help="Run a local JSON HTTP bridge.")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    add_bridge_transport_args(server)
    server.add_argument(
        "--cors-origin",
        type=parse_optional_text,
        default="*",
        help="Access-Control-Allow-Origin value. Use 'none' to disable CORS.",
    )
    server.add_argument(
        "--preset-file", help="JSON file containing named scene presets."
    )
    server.add_argument("--allow-control", action="store_true")
    server.set_defaults(func=cmd_serve)

    osc = sub.add_parser("osc-serve", help="Run a local OSC UDP bridge.")
    osc.add_argument("--host", default="127.0.0.1")
    osc.add_argument("--port", type=int, default=9000)
    add_bridge_transport_args(osc)
    osc.add_argument("--preset-file", help="JSON file containing named scene presets.")
    osc.add_argument("--allow-control", action="store_true")
    osc.set_defaults(func=cmd_osc_serve)

    artnet = sub.add_parser("artnet-serve", help="Run an Art-Net / DMX bridge.")
    artnet.add_argument("--host", default="0.0.0.0")
    artnet.add_argument("--port", type=int, default=6454)
    artnet.add_argument("--universe", type=parse_int, default=0)
    add_bridge_transport_args(artnet)
    artnet.add_argument("--obj", type=parse_int, default=1)
    artnet.add_argument("--brightness-channel", type=parse_optional_int, default=1)
    artnet.add_argument("--cct-channel", type=parse_optional_int, default=2)
    artnet.add_argument("--sleep-channel", type=parse_optional_int)
    artnet.add_argument("--cct-min", type=int, default=2700)
    artnet.add_argument("--cct-max", type=int, default=6500)
    artnet.add_argument("--allow-control", action="store_true")
    artnet.set_defaults(func=cmd_artnet_serve)

    sacn = sub.add_parser("sacn-serve", help="Run an sACN / E1.31 DMX bridge.")
    sacn.add_argument("--host", default="0.0.0.0")
    sacn.add_argument("--port", type=int, default=5568)
    sacn.add_argument("--universe", type=parse_int, default=1)
    add_bridge_transport_args(sacn)
    sacn.add_argument("--obj", type=parse_int, default=1)
    sacn.add_argument("--brightness-channel", type=parse_optional_int, default=1)
    sacn.add_argument("--cct-channel", type=parse_optional_int, default=2)
    sacn.add_argument("--sleep-channel", type=parse_optional_int)
    sacn.add_argument("--cct-min", type=int, default=2700)
    sacn.add_argument("--cct-max", type=int, default=6500)
    sacn.add_argument("--multicast", action="store_true")
    sacn.add_argument("--allow-control", action="store_true")
    sacn.set_defaults(func=cmd_sacn_serve)

    return parser


def add_transport_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--transport", choices=["usb", "ble"], default="usb")
    parser.add_argument(
        "--port", help="USB serial port. Defaults to first /dev/cu.usbmodem*."
    )
    parser.add_argument("--address", help="BLE address/identifier.")
    parser.add_argument(
        "--name-contains", help="BLE name substring used for discovery."
    )
    parser.add_argument("--timeout", type=float, default=1.5)
    parser.add_argument(
        "--usb-lock-timeout",
        type=parse_optional_float,
        default=DEFAULT_LOCK_TIMEOUT,
        help="Seconds to wait for the USB port lock. Use 'none' to wait forever.",
    )


def add_ble_execution_args(parser: argparse.ArgumentParser) -> None:
    add_ble_profile_args(parser)
    parser.add_argument(
        "--python",
        help="Python executable for the crash-isolated BLE worker.",
    )
    parser.add_argument(
        "--unsafe-in-process",
        action="store_true",
        help="Run BLE commands in this process instead of the crash-isolated worker.",
    )


def add_ble_profile_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ble-profile",
        choices=BLE_PROFILE_NAMES,
        default=DEFAULT_BLE_PROFILE.name,
        help="BLE characteristic profile to use for command exchange.",
    )
    parser.add_argument(
        "--ble-service-uuid",
        help="Override the BLE service UUID for command exchange.",
    )
    parser.add_argument(
        "--ble-write-uuid",
        help="Override the BLE write characteristic UUID.",
    )
    parser.add_argument(
        "--ble-notify-uuid",
        help="Override the BLE notify/read characteristic UUID.",
    )


def add_bridge_transport_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--transport", choices=["usb", "ble"], default="usb")
    parser.add_argument(
        "--light-port",
        help="USB serial port. Defaults to first /dev/cu.usbmodem*.",
    )
    parser.add_argument("--address", help="BLE address/identifier.")
    parser.add_argument(
        "--name-contains", help="BLE name substring used for discovery."
    )
    add_ble_profile_args(parser)
    parser.add_argument(
        "--ble-python",
        help="Python executable for crash-isolated BLE bridge exchanges.",
    )
    parser.add_argument(
        "--unsafe-in-process",
        action="store_true",
        help="Run BLE bridge commands in this process instead of worker isolation.",
    )
    parser.add_argument("--light-timeout", type=float, default=1.5)
    parser.add_argument(
        "--usb-lock-timeout",
        type=parse_optional_float,
        default=DEFAULT_LOCK_TIMEOUT,
        help="Seconds to wait for the USB port lock. Use 'none' to wait forever.",
    )
    parser.add_argument(
        "--no-persistent-light",
        action="store_true",
        help=(
            "Open and close the light for each bridge request instead of reusing "
            "one connection."
        ),
    )


def parse_int(text: str) -> int:
    return int(text, 0)


def parse_optional_int(text: str) -> int | None:
    if text.lower() in {"none", "off", "disabled"}:
        return None
    return int(text, 0)


def parse_optional_float(text: str) -> float | None:
    if text.lower() in {"none", "off", "disabled"}:
        return None
    return float(text)


def parse_optional_text(text: str) -> str | None:
    if text.lower() in {"none", "off", "disabled"}:
        return None
    return text


def parse_int_list(text: str) -> tuple[int, ...]:
    values = tuple(parse_int(part.strip()) for part in text.split(",") if part.strip())
    if not values:
        raise argparse.ArgumentTypeError("expected at least one integer")
    return values


def parse_hex_bytes(text: str) -> bytes:
    normalized = text.strip()
    if normalized.lower().startswith("0x"):
        normalized = normalized[2:]
    try:
        return bytes.fromhex(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected hex bytes") from exc


def cmd_probe(args: argparse.Namespace) -> int:
    if args.transport == "ble":
        try:
            result = asyncio.run(_probe_ble(args)).to_dict()
        except RuntimeError as exc:
            return print_ble_runtime_error(exc, compact=args.json)
    else:
        with sync_usb_light_from_args(args) as light:
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


def cmd_validate(args: argparse.Namespace) -> int:
    if args.transport == "ble":
        if not args.unsafe_in_process:
            raise SystemExit(
                "BLE validation uses the direct bleak transport and may crash on "
                "this macOS stack; run scan-ble first, then re-run validate with "
                "--unsafe-in-process on a stable BLE runtime"
            )
        report = asyncio.run(_validate_ble(args))
    else:
        with sync_usb_light_from_args(args) as light:
            report = validate_sync_light(
                light,
                transport=args.transport,
                allow_control=args.allow_control,
                include_object_reads=args.include_object_reads,
                include_color=args.include_color,
                device_id=args.device_id,
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
    print_json(report.to_dict(), compact=args.json)
    if args.strict and not report.all_attempted_confirmed:
        return 1
    return 0 if report.connection_confirmed else 1


def cmd_discover_usb(args: argparse.Namespace) -> int:
    with sync_usb_light_from_args(args) as light:
        report = discover_usb_primitives(
            light,
            object_ids=args.object_ids,
            first_words=args.first_words,
            control_object_ids=args.control_object_ids,
            control_first_words=args.control_first_words,
            timeout=args.timeout,
            allow_control=args.allow_control,
            brightness=args.brightness,
            kelvin=args.kelvin,
            sleep=args.sleep,
        )
    print_json(report.to_dict(), compact=args.json)
    return 0 if report.confirmed else 1


async def _validate_ble(args: argparse.Namespace):
    async with async_ble_light_from_args(args) as light:
        return await validate_async_light(
            light,
            transport=args.transport,
            allow_control=args.allow_control,
            include_object_reads=args.include_object_reads,
            include_color=args.include_color,
            device_id=args.device_id,
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


async def _probe_ble(args: argparse.Namespace):
    async with async_ble_light_from_args(args) as light:
        return await light.probe()


def cmd_scan_ble(args: argparse.Namespace) -> int:
    if args.unsafe_in_process:
        devices = asyncio.run(scan_zhiyun_devices(timeout=args.timeout))
        print_json({"ok": True, "devices": [device.to_dict() for device in devices]})
        return 0
    result = scan_zhiyun_devices_safe(timeout=args.timeout, python=args.python)
    print_json(result.to_dict())
    return 0 if result.ok else 2


def cmd_frame(args: argparse.Namespace) -> int:
    require_yes(args, "frame sends a raw frame to the light")
    if args.transport == "ble":
        try:
            result = asyncio.run(_frame_ble(args))
        except RuntimeError as exc:
            return print_ble_runtime_error(exc)
    else:
        with sync_usb_light_from_args(args) as light:
            result = light.exchange_frame(
                args.first_word,
                args.raw_command,
                args.payload_hex,
                timeout=args.timeout,
            )
    print_json(result.to_dict())
    return command_result_exit_code(result)


async def _frame_ble(args: argparse.Namespace) -> CommandResult:
    async with async_ble_light_from_args(args) as light:
        return await light.exchange_frame(
            args.first_word,
            args.raw_command,
            args.payload_hex,
            timeout=args.timeout,
        )


def cmd_register(args: argparse.Namespace) -> int:
    require_yes(args, "register changes the light/group runtime state")
    if args.transport == "ble":
        try:
            result = asyncio.run(_register_ble(args))
        except RuntimeError as exc:
            return print_ble_runtime_error(exc)
    else:
        with sync_usb_light_from_args(args) as light:
            result = light.exchange_runtime(
                RuntimeCommand.REGISTER_DEFAULT_GROUP,
                register_payload(args.device_id),
            )
    print_json(result.to_dict())
    return command_result_exit_code(result)


async def _register_ble(args: argparse.Namespace):
    async with async_ble_light_from_args(args) as light:
        return await light.exchange_runtime(
            RuntimeCommand.REGISTER_DEFAULT_GROUP,
            register_payload(args.device_id),
        )


def cmd_read(args: argparse.Namespace) -> int:
    if args.transport == "ble":
        try:
            result = asyncio.run(_read_ble(args))
        except RuntimeError as exc:
            return print_ble_runtime_error(exc)
    else:
        with sync_usb_light_from_args(args) as light:
            result = _read_usb(light, args)
    print_json(result.to_dict())
    return command_result_exit_code(result)


def _read_usb(light: ZhiyunLight, args: argparse.Namespace) -> CommandResult:
    cmd, payload = object_read_request(args.kind, args.obj)
    return light.exchange_runtime(cmd, payload)


async def _read_ble(args: argparse.Namespace) -> CommandResult:
    cmd, payload = object_read_request(args.kind, args.obj)
    async with async_ble_light_from_args(args) as light:
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
        try:
            result = asyncio.run(_set_ble(args))
        except RuntimeError as exc:
            return print_ble_runtime_error(exc)
    else:
        with sync_usb_light_from_args(args) as light:
            result = _set_usb(light, args)
    print_json(result.to_dict())
    return command_result_exit_code(result)


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
            else int(
                require_value(args.value, "--kelvin or --value is required for cct")
            )
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
    async with async_ble_light_from_args(args) as light:
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
                else int(
                    require_value(args.value, "--kelvin or --value is required for cct")
                )
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
    scene = scene_from_args(args)
    if args.dry_run:
        print_json({"dry_run": True, "scene": scene.to_dict(), "results": []})
        return 0
    require_yes(args, "apply sends one or more control commands to the light")
    if args.transport == "ble":
        try:
            results = asyncio.run(_apply_ble(args, scene))
        except RuntimeError as exc:
            return print_ble_runtime_error(exc)
    else:
        with sync_usb_light_from_args(args) as light:
            results = light.apply_scene(scene)
    print_json(
        {"scene": scene.to_dict(), "results": [result.to_dict() for result in results]}
    )
    return command_results_exit_code(results)


def command_result_exit_code(result: CommandResult) -> int:
    return 0 if result.acknowledged else 1


def command_results_exit_code(results: list[CommandResult]) -> int:
    return 0 if all(result.acknowledged for result in results) else 1


def sync_usb_light_from_args(args: argparse.Namespace) -> ZhiyunLight:
    return ZhiyunLight.usb(
        port=args.port,
        timeout=args.timeout,
        lock_timeout=args.usb_lock_timeout,
    )


async def _apply_ble(args: argparse.Namespace, scene: Scene) -> list[CommandResult]:
    async with async_ble_light_from_args(args) as light:
        return await light.apply_scene(scene)


def async_ble_light_from_args(args: argparse.Namespace) -> AsyncZhiyunLight:
    if getattr(args, "unsafe_in_process", False):
        return AsyncZhiyunLight.ble(
            address=args.address,
            name_contains=args.name_contains,
            profile=args.ble_profile,
            service_uuid=args.ble_service_uuid,
            write_uuid=args.ble_write_uuid,
            notify_uuid=args.ble_notify_uuid,
            timeout=args.timeout,
        )
    return AsyncZhiyunLight.isolated_ble(
        address=args.address,
        name_contains=args.name_contains,
        profile=args.ble_profile,
        service_uuid=args.ble_service_uuid,
        write_uuid=args.ble_write_uuid,
        notify_uuid=args.ble_notify_uuid,
        timeout=args.timeout,
        python=getattr(args, "python", None),
    )


def scene_from_args(args: argparse.Namespace) -> Scene:
    scene = Scene(
        obj=args.obj if args.obj is not None else 1,
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
    preset_name = getattr(args, "preset", None)
    if preset_name:
        library = load_preset_library(args)
        if library is None:
            raise SystemExit("--preset-file is required with --preset")
        return merge_scene(
            library.get(preset_name), scene, override_obj=args.obj is not None
        )
    return scene


def load_preset_library(args: argparse.Namespace) -> ScenePresetLibrary | None:
    path = getattr(args, "preset_file", None)
    return ScenePresetLibrary.load(path) if path else None


def cmd_serve(args: argparse.Namespace) -> int:
    serve(
        host=args.host,
        port=args.port,
        light_port=args.light_port,
        allow_control=args.allow_control,
        light_factory=bridge_light_factory(args),
        preset_library=load_preset_library(args),
        cors_origin=args.cors_origin,
    )
    return 0


def cmd_osc_serve(args: argparse.Namespace) -> int:
    serve_osc(
        host=args.host,
        port=args.port,
        light_port=args.light_port,
        allow_control=args.allow_control,
        light_factory=bridge_light_factory(args),
        preset_library=load_preset_library(args),
    )
    return 0


def cmd_artnet_serve(args: argparse.Namespace) -> int:
    serve_artnet(
        host=args.host,
        port=args.port,
        universe=args.universe,
        light_port=args.light_port,
        light_factory=bridge_light_factory(args),
        mapping=DmxMapping(
            obj=args.obj,
            brightness_channel=args.brightness_channel,
            cct_channel=args.cct_channel,
            sleep_channel=args.sleep_channel,
            cct_min=args.cct_min,
            cct_max=args.cct_max,
        ),
        allow_control=args.allow_control,
    )
    return 0


def cmd_sacn_serve(args: argparse.Namespace) -> int:
    serve_sacn(
        host=args.host,
        port=args.port,
        universe=args.universe,
        light_port=args.light_port,
        light_factory=bridge_light_factory(args),
        mapping=DmxMapping(
            obj=args.obj,
            brightness_channel=args.brightness_channel,
            cct_channel=args.cct_channel,
            sleep_channel=args.sleep_channel,
            cct_min=args.cct_min,
            cct_max=args.cct_max,
        ),
        multicast=args.multicast,
        allow_control=args.allow_control,
    )
    return 0


def bridge_light_factory(args: argparse.Namespace):
    return make_light_factory(
        LightConnectionConfig(
            transport=args.transport,
            port=args.light_port,
            address=args.address,
            name_contains=args.name_contains,
            timeout=args.light_timeout,
            usb_lock_timeout=args.usb_lock_timeout,
            ble_profile=args.ble_profile,
            ble_service_uuid=args.ble_service_uuid,
            ble_write_uuid=args.ble_write_uuid,
            ble_notify_uuid=args.ble_notify_uuid,
            ble_python=args.ble_python,
            ble_in_process=args.unsafe_in_process,
            persistent=not args.no_persistent_light,
        )
    )


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


def print_ble_runtime_error(exc: RuntimeError, *, compact: bool = False) -> int:
    payload: dict[str, object] = {
        "ok": False,
        "transport": "ble",
        "error": str(exc),
    }
    if isinstance(exc, BleWorkerError):
        payload["exchange"] = exc.result.to_dict()
    print_json(payload, compact=compact)
    return 2


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
