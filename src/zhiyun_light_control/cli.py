"""Command line interface for Zhiyun light control."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .artnet import DmxMapping, serve_artnet
from .async_client import AsyncZhiyunLight
from .bridge import LightConnectionConfig, make_light_factory
from .client import ZhiyunLight
from .commands import (
    execute_async_frame_plan,
    execute_async_serialized_frame_plan,
    execute_frame_plan,
    execute_serialized_frame_plan,
    load_serialized_plan_bundle,
    scene_command_plan,
    serialized_plan_bundle,
    serialized_plan_bundle_from_json,
    serialized_plan_payload,
)
from .cues import CueLibrary
from .devices import (
    discover_transport_devices,
    inspect_ble_device,
    test_ble_endpoint_candidates,
)
from .discovery import (
    DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS,
    DEFAULT_DISCOVERY_CONTROL_KINDS,
    DEFAULT_DISCOVERY_FIRST_WORDS,
    DEFAULT_DISCOVERY_OBJECT_IDS,
    DEFAULT_DISCOVERY_REGISTER_DEVICE_IDS,
    DEFAULT_DISCOVERY_REGISTER_GROUP_IDS,
    DISCOVERY_CONTROL_KIND_NAMES,
    G60_USB_DISCOVERY_PROFILE,
    discover_usb_primitives,
)
from .http_client import LightBridgeClient, LightBridgeError
from .integration import (
    local_capabilities,
    local_integration_snapshot,
    local_manifest,
    local_readiness,
)
from .macos_ble_app import (
    macos_ble_app_authorize,
    macos_ble_app_info,
    macos_ble_app_status,
    open_macos_bluetooth_settings,
)
from .mesh import (
    build_mesh_config_proxy_pdu_sequence,
    build_mesh_config_sequence_plan,
    build_provisioner_confirmation,
    build_provisioner_random,
    build_provisioning_data_plan,
    build_provisioning_invite,
    build_provisioning_public_key,
    build_provisioning_start_no_oob,
    build_zy_mesh_network_plan,
    confirmation_inputs,
    derive_shared_ecdh_secret,
    generate_network_key,
    generate_provisioner_keypair,
    generate_provisioning_random,
    parse_provisioning_capabilities,
    parse_provisioning_confirmation,
    parse_provisioning_failure,
    parse_provisioning_public_key,
    parse_provisioning_random,
    provisioning_session_secrets,
    verify_provisionee_confirmation,
)
from .models import CommandResult, Scene
from .osc import serve_osc
from .presets import ScenePresetLibrary, merge_scene
from .protocol import (
    DEFAULT_CONTROL_MODE,
    RUNTIME_TYPE,
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
from .server import openapi_schema, serve
from .status import read_async_status, read_sync_status
from .transports.ble import (
    BLE_PROFILE_NAMES,
    DEFAULT_BLE_PROFILE,
    MESH_PROVISIONING_NOTIFY_UUID,
    MESH_PROVISIONING_SERVICE_UUID,
    MESH_PROVISIONING_WRITE_UUID,
    MESH_PROXY_NOTIFY_UUID,
    MESH_PROXY_SERVICE_UUID,
    MESH_PROXY_WRITE_UUID,
    BleWorkerError,
    exchange_zhiyun_ble_macos_app,
    exchange_zhiyun_ble_safe,
    exchange_zhiyun_ble_sequence_macos_app,
    exchange_zhiyun_ble_sequence_safe,
    filter_ble_devices_by_name,
    open_zhiyun_ble_ipc_macos_app,
    scan_zhiyun_devices,
    scan_zhiyun_devices_macos_app,
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

    status = sub.add_parser(
        "status",
        help="Read ACK-backed global status and command evidence.",
    )
    add_transport_args(status)
    add_ble_execution_args(status)
    status.add_argument("--json", action="store_true", help="Print compact JSON.")
    status.set_defaults(func=cmd_status)

    ready = sub.add_parser(
        "ready",
        help="Run a local read-only setup preflight for controllers.",
    )
    add_transport_args(ready)
    add_ble_execution_args(ready)
    ready.add_argument(
        "--allow-control",
        action="store_true",
        help="Report control-request readiness as enabled for this preflight.",
    )
    ready.add_argument("--json", action="store_true", help="Print compact JSON.")
    ready.set_defaults(func=cmd_ready)

    integration = sub.add_parser(
        "integration",
        help="Print a local controller integration snapshot.",
    )
    add_transport_args(integration)
    add_ble_execution_args(integration)
    integration.add_argument(
        "--allow-control",
        action="store_true",
        help="Report control-request readiness as enabled for this snapshot.",
    )
    integration.add_argument(
        "--include-ble",
        action="store_true",
        help="Run a bounded BLE scan and include its diagnostic result.",
    )
    integration.add_argument(
        "--include-ble-status",
        action="store_true",
        help="Run the macOS helper Bluetooth authorization/status check.",
    )
    integration.add_argument("--json", action="store_true", help="Print compact JSON.")
    integration.set_defaults(func=cmd_integration)

    metadata = sub.add_parser(
        "metadata",
        help="Print API metadata without opening USB/BLE or starting a bridge.",
    )
    add_bridge_transport_args(metadata)
    metadata.add_argument(
        "--kind",
        choices=["all", "openapi", "manifest", "capabilities"],
        default="all",
        help="Metadata payload to print.",
    )
    metadata.add_argument(
        "--allow-control",
        action="store_true",
        help="Report control-request endpoints as enabled in metadata.",
    )
    metadata.add_argument(
        "--preset-file", help="JSON file containing named scene presets."
    )
    metadata.add_argument("--cue-file", help="JSON file containing named cues.")
    metadata.add_argument("--json", action="store_true", help="Print compact JSON.")
    metadata.set_defaults(func=cmd_metadata)

    validate = sub.add_parser(
        "validate",
        help="Run a structured hardware validation report.",
    )
    add_transport_args(validate)
    add_ble_profile_args(validate)
    validate.add_argument(
        "--ble-backend",
        choices=["worker", "macos-app", "direct"],
        default="worker",
        help="BLE validation backend. macos-app uses a CoreBluetooth app bundle.",
    )
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
    add_control_mode_arg(validate)
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
        "--g60-matrix",
        action="store_true",
        help=(
            "Use the G60 truth-pass matrix preset. Read probes are always safe; "
            "control writes are still skipped unless --allow-control is also set."
        ),
    )
    discover.add_argument(
        "--port",
        help="USB serial port. Defaults to the first detected USB CDC candidate.",
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
        default=None,
        help="Comma-separated object ids to probe. Default: 0,1.",
    )
    discover.add_argument(
        "--first-words",
        type=parse_int_list,
        default=None,
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
        default=None,
        help="Comma-separated first-word values for gated control probes.",
    )
    discover.add_argument(
        "--register-device-ids",
        type=parse_int_list,
        default=None,
        help="Comma-separated device ids to register before control probes.",
    )
    discover.add_argument(
        "--register-group-ids",
        type=parse_int_list,
        default=None,
        help="Comma-separated group ids to register before control probes.",
    )
    discover.add_argument(
        "--control-kinds",
        type=parse_control_kind_list,
        default=None,
        help=(
            "Comma-separated control candidates to send under --allow-control. "
            "Use 'none' to skip write probes."
        ),
    )
    discover.add_argument(
        "--control-modes",
        type=parse_int_list,
        help=(
            "Comma-separated operation bytes for gated control probes. "
            "Defaults to the Vega control mode and legacy op=1."
        ),
    )
    discover.add_argument(
        "--post-register-reads",
        action="store_true",
        help=(
            "After each gated register attempt, rerun object read probes for "
            "the selected control object ids."
        ),
    )
    discover.add_argument("--allow-control", action="store_true")
    discover.add_argument("--brightness", type=float, default=35.0)
    discover.add_argument("--kelvin", type=int, default=5600)
    discover.add_argument("--sleep", type=int, default=0)
    discover.add_argument("--json", action="store_true", help="Print compact JSON.")
    discover.set_defaults(func=cmd_discover_usb)

    devices = sub.add_parser(
        "devices",
        help="List local USB/BLE transport discovery state.",
    )
    devices.add_argument(
        "--transport",
        choices=["usb", "ble"],
        default="usb",
        help="Configured transport to report for bridge setup.",
    )
    devices.add_argument(
        "--port",
        help="Configured USB serial port to mark as selected.",
    )
    devices.add_argument(
        "--include-ble",
        action="store_true",
        help="Run a bounded BLE scan and include its diagnostic result.",
    )
    devices.add_argument(
        "--include-ble-status",
        action="store_true",
        help="Run the macOS helper Bluetooth authorization/status check.",
    )
    devices.add_argument(
        "--ble-backend",
        choices=["worker", "macos-app", "direct"],
        default="worker",
        help="BLE scan backend. macos-app uses a CoreBluetooth app bundle.",
    )
    devices.add_argument(
        "--ble-timeout",
        type=float,
        default=5.0,
        help="Seconds to wait for requested BLE status/scan checks.",
    )
    devices.add_argument("--name-contains", help="Filter discovered BLE names.")
    devices.add_argument(
        "--python",
        help="Python executable for the crash-isolated BLE worker.",
    )
    devices.add_argument("--json", action="store_true", help="Print compact JSON.")
    devices.set_defaults(func=cmd_devices)

    scan = sub.add_parser("scan-ble", help="Scan for likely Zhiyun BLE devices.")
    scan.add_argument("--timeout", type=float, default=5.0)
    scan.add_argument("--name-contains", help="Filter discovered BLE names.")
    scan.add_argument(
        "--include-all",
        action="store_true",
        help="With --backend macos-app, include non-Zhiyun BLE advertisements.",
    )
    scan.add_argument(
        "--python",
        help="Python executable for the crash-isolated BLE worker.",
    )
    scan.add_argument(
        "--unsafe-in-process",
        action="store_true",
        help="Run bleak scan in this process instead of the crash-isolated worker.",
    )
    scan.add_argument(
        "--backend",
        choices=["worker", "macos-app", "direct"],
        default="worker",
        help="BLE scan backend. macos-app uses a CoreBluetooth app bundle.",
    )
    scan.set_defaults(func=cmd_scan_ble)

    inspect_ble = sub.add_parser(
        "inspect-ble",
        help="Inspect BLE GATT services and characteristics.",
    )
    inspect_ble.add_argument("--address", help="BLE address/identifier.")
    inspect_ble.add_argument("--name-contains", help="Filter discovered BLE names.")
    inspect_ble.add_argument("--timeout", type=float, default=5.0)
    inspect_ble.add_argument(
        "--python",
        help="Python executable for the crash-isolated BLE worker.",
    )
    inspect_ble.add_argument(
        "--backend",
        choices=["worker", "macos-app", "direct"],
        default="worker",
        help="BLE inspection backend. macos-app uses a CoreBluetooth app bundle.",
    )
    inspect_ble.add_argument(
        "--unsafe-in-process",
        action="store_true",
        help="Run bleak inspection in this process instead of the worker.",
    )
    inspect_ble.add_argument("--json", action="store_true", help="Print compact JSON.")
    inspect_ble.set_defaults(func=cmd_inspect_ble)

    test_ble = sub.add_parser(
        "test-ble-endpoints",
        help="Probe suggested BLE endpoints with a read-only identity command.",
    )
    test_ble.add_argument("--address", help="BLE address/identifier.")
    test_ble.add_argument("--name-contains", help="Filter discovered BLE names.")
    test_ble.add_argument("--timeout", type=float, default=5.0)
    test_ble.add_argument(
        "--max-candidates",
        type=int,
        default=4,
        help="Maximum suggested endpoint candidates to test.",
    )
    test_ble.add_argument(
        "--python",
        help="Python executable for the crash-isolated BLE worker.",
    )
    test_ble.add_argument(
        "--backend",
        choices=["worker", "macos-app", "direct"],
        default="worker",
        help="BLE test backend. macos-app uses a CoreBluetooth app bundle.",
    )
    test_ble.add_argument(
        "--unsafe-in-process",
        action="store_true",
        help="Run bleak inspection/exchange in this process instead of the worker.",
    )
    test_ble.add_argument("--json", action="store_true", help="Print compact JSON.")
    test_ble.set_defaults(func=cmd_test_ble_endpoints)

    mesh_probe = sub.add_parser(
        "mesh-probe",
        help="Probe the BLE Mesh provisioning endpoint with an invite.",
    )
    mesh_probe.add_argument("--address", help="BLE address/identifier.")
    mesh_probe.add_argument("--name-contains", help="Filter discovered BLE names.")
    mesh_probe.add_argument("--timeout", type=float, default=8.0)
    mesh_probe.add_argument("--attention", type=parse_int, default=5)
    mesh_probe.add_argument(
        "--python",
        help="Python executable for the crash-isolated BLE worker.",
    )
    mesh_probe.add_argument(
        "--backend",
        choices=["worker", "macos-app"],
        default="macos-app",
        help="BLE exchange backend. macos-app uses a CoreBluetooth app bundle.",
    )
    mesh_probe.add_argument("--json", action="store_true", help="Print compact JSON.")
    mesh_probe.set_defaults(func=cmd_mesh_probe)

    mesh_handshake = sub.add_parser(
        "mesh-handshake",
        help="Run the first BLE Mesh provisioning handshake frames.",
    )
    mesh_handshake.add_argument("--address", help="BLE address/identifier.")
    mesh_handshake.add_argument(
        "--name-contains",
        default="PL103",
        help="Filter discovered BLE names.",
    )
    mesh_handshake.add_argument("--timeout", type=float, default=12.0)
    mesh_handshake.add_argument("--attention", type=parse_int, default=5)
    mesh_handshake.add_argument(
        "--json",
        action="store_true",
        help="Print compact JSON.",
    )
    mesh_handshake.set_defaults(func=cmd_mesh_handshake)

    mesh_session = sub.add_parser(
        "mesh-session",
        help="Run a dynamic BLE Mesh provisioning confirmation session.",
    )
    mesh_session.add_argument("--address", help="BLE address/identifier.")
    mesh_session.add_argument(
        "--name-contains",
        default="PL103",
        help="Filter discovered BLE names.",
    )
    mesh_session.add_argument("--timeout", type=float, default=12.0)
    mesh_session.add_argument("--attention", type=parse_int, default=5)
    mesh_session.add_argument(
        "--json",
        action="store_true",
        help="Print compact JSON.",
    )
    mesh_session.set_defaults(func=cmd_mesh_session)

    mesh_provision_plan = sub.add_parser(
        "mesh-provision-plan",
        help="Build the next BLE Mesh provisioning-data PDU without sending it.",
    )
    mesh_provision_plan.add_argument(
        "--session-json",
        required=True,
        help="mesh-session JSON path, or '-' to read from stdin.",
    )
    mesh_provision_plan.add_argument(
        "--network-key-hex",
        type=parse_hex_bytes,
        help="16-byte Bluetooth Mesh network key. Generated if omitted.",
    )
    mesh_provision_plan.add_argument("--key-index", type=parse_int, default=0)
    mesh_provision_plan.add_argument("--flags", type=parse_int, default=0)
    mesh_provision_plan.add_argument("--iv-index", type=parse_int, default=0)
    mesh_provision_plan.add_argument(
        "--unicast-address",
        type=parse_int,
        default=0x0005,
    )
    mesh_provision_plan.add_argument(
        "--json",
        action="store_true",
        help="Print compact JSON.",
    )
    mesh_provision_plan.set_defaults(func=cmd_mesh_provision_plan)

    mesh_setup_plan = sub.add_parser(
        "mesh-setup-plan",
        help="Build the official Zhiyun BLE Mesh setup/config plan offline.",
    )
    mesh_setup_plan.add_argument(
        "--mesh-uuid-hex",
        type=parse_hex_bytes,
        help="16-byte mesh UUID. Generated if omitted.",
    )
    mesh_setup_plan.add_argument(
        "--network-key-hex",
        type=parse_hex_bytes,
        help="16-byte Bluetooth Mesh network key. Generated if omitted.",
    )
    mesh_setup_plan.add_argument(
        "--app-key-hex",
        type=parse_hex_bytes,
        help="16-byte Bluetooth Mesh application key. Generated if omitted.",
    )
    mesh_setup_plan.add_argument("--key-index", type=parse_int, default=0)
    mesh_setup_plan.add_argument("--app-key-index", type=parse_int, default=0)
    mesh_setup_plan.add_argument("--flags", type=parse_int, default=0)
    mesh_setup_plan.add_argument("--iv-index", type=parse_int, default=0)
    mesh_setup_plan.add_argument(
        "--device-key-hex",
        type=parse_hex_bytes,
        help=(
            "16-byte provisioned light device key. When supplied, encrypted "
            "Mesh Proxy network PDUs are included."
        ),
    )
    mesh_setup_plan.add_argument(
        "--sequence-number",
        type=parse_int,
        default=1,
        help="Initial 24-bit mesh sequence number for generated proxy PDUs.",
    )
    mesh_setup_plan.add_argument(
        "--ttl",
        type=parse_int,
        default=5,
        help="Network TTL for generated proxy PDUs.",
    )
    mesh_setup_plan.add_argument(
        "--unicast-address",
        type=parse_int,
        default=0x0005,
        help="Light unicast address assigned during provisioning.",
    )
    mesh_setup_plan.add_argument(
        "--json",
        action="store_true",
        help="Print compact JSON.",
    )
    mesh_setup_plan.set_defaults(func=cmd_mesh_setup_plan)

    mesh_config_send = sub.add_parser(
        "mesh-config-send",
        help="Send generated Zhiyun BLE Mesh config PDUs over Mesh Proxy.",
    )
    mesh_config_send.add_argument("--address", help="BLE address/identifier.")
    mesh_config_send.add_argument(
        "--name-contains",
        default="PL103",
        help="Filter discovered BLE names.",
    )
    mesh_config_send.add_argument("--timeout", type=float, default=8.0)
    mesh_config_send.add_argument(
        "--ble-backend",
        choices=["worker", "macos-app"],
        default="worker",
        help="BLE sequence backend. macos-app uses a CoreBluetooth app bundle.",
    )
    mesh_config_send.add_argument("--python", help="Python executable for worker.")
    mesh_config_send.add_argument(
        "--network-key-hex",
        required=True,
        type=parse_hex_bytes,
        help="16-byte Bluetooth Mesh network key used during provisioning.",
    )
    mesh_config_send.add_argument(
        "--app-key-hex",
        required=True,
        type=parse_hex_bytes,
        help="16-byte Bluetooth Mesh application key to add.",
    )
    mesh_config_send.add_argument(
        "--device-key-hex",
        required=True,
        type=parse_hex_bytes,
        help="16-byte device key derived from the provisioning session.",
    )
    mesh_config_send.add_argument("--mesh-uuid-hex", type=parse_hex_bytes)
    mesh_config_send.add_argument("--key-index", type=parse_int, default=0)
    mesh_config_send.add_argument("--app-key-index", type=parse_int, default=0)
    mesh_config_send.add_argument("--iv-index", type=parse_int, default=0)
    mesh_config_send.add_argument("--sequence-number", type=parse_int, default=1)
    mesh_config_send.add_argument("--ttl", type=parse_int, default=5)
    mesh_config_send.add_argument(
        "--unicast-address",
        type=parse_int,
        default=0x0005,
        help="Provisioned light unicast address.",
    )
    mesh_config_send.add_argument("--yes", action="store_true")
    mesh_config_send.add_argument(
        "--json",
        action="store_true",
        help="Print compact JSON.",
    )
    mesh_config_send.set_defaults(func=cmd_mesh_config_send)

    helper = sub.add_parser(
        "ble-helper",
        help="Inspect or prepare the macOS CoreBluetooth helper app.",
    )
    helper.add_argument(
        "--ensure",
        action="store_true",
        help="Build the cached helper app if needed.",
    )
    helper.add_argument(
        "--open-settings",
        action="store_true",
        help="Open macOS Privacy & Security Bluetooth settings.",
    )
    helper.add_argument(
        "--status",
        action="store_true",
        help="Run the helper and report macOS Bluetooth authorization/state.",
    )
    helper.add_argument(
        "--authorize",
        action="store_true",
        help="Bring the helper forward and wait for the Bluetooth permission prompt.",
    )
    helper.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Seconds to wait for --status or --authorize.",
    )
    helper.add_argument(
        "--bundle-name",
        help="Override the macOS helper app name for Bluetooth authorization tests.",
    )
    helper.add_argument(
        "--bundle-id",
        help="Override the macOS helper bundle id for a fresh Bluetooth TCC prompt.",
    )
    helper.add_argument("--json", action="store_true", help="Print compact JSON.")
    helper.set_defaults(func=cmd_ble_helper)

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
    add_control_mode_arg(set_cmd)
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
    add_control_mode_arg(apply)
    apply.add_argument(
        "--first-word",
        type=parse_int,
        default=RUNTIME_TYPE,
        help=(
            "Frame first word for experimental routes. Default is 0x0100; "
            "some G60 USB control writes are physically effectful on 0x0301."
        ),
    )
    apply.add_argument(
        "--preset-file", help="JSON file containing named scene presets."
    )
    apply.add_argument("--preset", help="Named preset to apply before CLI overrides.")
    apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the scene without sending commands.",
    )
    apply.add_argument(
        "--accept-echo",
        action="store_true",
        help=(
            "Return exit code 0 when every unacknowledged command is an exact "
            "write echo. JSON output still reports acknowledged=false."
        ),
    )
    apply.add_argument("--yes", action="store_true")
    apply.set_defaults(func=cmd_apply)

    plan = sub.add_parser(
        "plan",
        help="Build a serialized runtime plan bundle without opening a transport.",
    )
    plan.add_argument("--obj", type=parse_int)
    plan.add_argument("--brightness", type=float)
    plan.add_argument("--kelvin", type=int)
    plan.add_argument("--sleep", type=int)
    plan.add_argument("--red", type=int)
    plan.add_argument("--green", type=int)
    plan.add_argument("--blue", type=int)
    plan.add_argument("--hue", type=float)
    plan.add_argument("--saturation", type=float)
    plan.add_argument("--intensity", type=int)
    add_control_mode_arg(plan)
    plan.add_argument("--preset-file", help="JSON file containing named scene presets.")
    plan.add_argument("--preset", help="Named preset to apply before CLI overrides.")
    plan.add_argument("--first-word", type=parse_int, default=RUNTIME_TYPE)
    plan.add_argument("--start-seq", type=parse_int, default=1)
    plan.add_argument(
        "--output",
        help="Write the serialized plan bundle JSON to this path.",
    )
    plan.add_argument("--json", action="store_true", help="Print compact JSON.")
    plan.set_defaults(func=cmd_plan)

    execute_plan = sub.add_parser(
        "execute-plan",
        help="Execute a serialized plan bundle over USB/BLE or a running bridge.",
    )
    add_transport_args(execute_plan)
    add_ble_execution_args(execute_plan)
    execute_plan.add_argument(
        "plan_file",
        help="Serialized plan bundle JSON path, or '-' to read from stdin.",
    )
    execute_plan.add_argument(
        "--base-url",
        help="POST to a running zlight serve bridge instead of opening USB/BLE.",
    )
    execute_plan.add_argument(
        "--bridge-timeout",
        type=float,
        default=12.0,
        help="HTTP timeout when --base-url is used.",
    )
    execute_plan.add_argument("--yes", action="store_true")
    execute_plan.add_argument("--json", action="store_true", help="Print compact JSON.")
    execute_plan.set_defaults(func=cmd_execute_plan)

    cue = sub.add_parser("cue", help="Run a named cue against the HTTP bridge.")
    cue.add_argument(
        "--base-url",
        default="http://127.0.0.1:8765",
        help="Base URL for a running zlight serve process.",
    )
    cue.add_argument("--timeout", type=float, default=12.0)
    cue.add_argument("--cue-file", required=True, help="JSON file containing cues.")
    cue.add_argument("--cue", help="Named cue to run.")
    cue.add_argument("--list", action="store_true", help="List cue names and exit.")
    add_control_mode_arg(cue)
    cue.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the cue request without sending it to the bridge.",
    )
    cue.add_argument("--yes", action="store_true")
    cue.set_defaults(func=cmd_cue)

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
    server.add_argument("--cue-file", help="JSON file containing named cues.")
    server.add_argument("--allow-control", action="store_true")
    server.set_defaults(func=cmd_serve)

    osc = sub.add_parser("osc-serve", help="Run a local OSC UDP bridge.")
    osc.add_argument("--host", default="127.0.0.1")
    osc.add_argument("--port", type=int, default=9000)
    add_bridge_transport_args(osc)
    osc.add_argument("--preset-file", help="JSON file containing named scene presets.")
    osc.add_argument("--cue-file", help="JSON file containing named cues.")
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
        "--port",
        help="USB serial port. Defaults to the first detected USB CDC candidate.",
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
        "--ble-backend",
        choices=["worker", "macos-app", "direct"],
        default="worker",
        help="BLE command backend. macos-app uses a CoreBluetooth app bundle.",
    )
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


def add_control_mode_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--control-mode",
        type=parse_int,
        default=DEFAULT_CONTROL_MODE,
        help=(
            "Functional write operation byte. Defaults to Vega controlMode 0x33; "
            "use 0x01 for legacy probes."
        ),
    )


def add_bridge_transport_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--transport", choices=["usb", "ble"], default="usb")
    parser.add_argument(
        "--light-port",
        help="USB serial port. Defaults to the first detected USB CDC candidate.",
    )
    parser.add_argument("--address", help="BLE address/identifier.")
    parser.add_argument(
        "--name-contains", help="BLE name substring used for discovery."
    )
    add_ble_profile_args(parser)
    parser.add_argument(
        "--ble-backend",
        choices=["worker", "macos-app", "direct"],
        default="worker",
        help="BLE bridge backend. macos-app uses a CoreBluetooth app bundle.",
    )
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


def parse_control_kind_list(text: str) -> tuple[str, ...]:
    if text.strip().lower() == "none":
        return ()
    values = tuple(part.strip() for part in text.split(",") if part.strip())
    if not values:
        return ()
    unsupported = tuple(
        value for value in values if value not in DISCOVERY_CONTROL_KIND_NAMES
    )
    if unsupported:
        supported = ", ".join(DISCOVERY_CONTROL_KIND_NAMES)
        raise argparse.ArgumentTypeError(
            f"unsupported control kind: {', '.join(unsupported)}; expected {supported}"
        )
    return values


def parse_hex_bytes(text: str) -> bytes:
    normalized = text.strip()
    if normalized.lower().startswith("0x"):
        normalized = normalized[2:]
    try:
        return bytes.fromhex(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected hex bytes") from exc


def usb_discovery_options_from_args(args: argparse.Namespace) -> dict[str, object]:
    profile = G60_USB_DISCOVERY_PROFILE if args.g60_matrix else None
    return {
        "profile": profile.name if profile is not None else "default",
        "object_ids": args.object_ids
        if args.object_ids is not None
        else (
            profile.object_ids if profile is not None else DEFAULT_DISCOVERY_OBJECT_IDS
        ),
        "first_words": args.first_words
        if args.first_words is not None
        else (
            profile.first_words
            if profile is not None
            else DEFAULT_DISCOVERY_FIRST_WORDS
        ),
        "control_object_ids": args.control_object_ids
        if args.control_object_ids is not None
        else (profile.control_object_ids if profile is not None else None),
        "control_first_words": args.control_first_words
        if args.control_first_words is not None
        else (
            profile.control_first_words
            if profile is not None
            else DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS
        ),
        "register_device_ids": args.register_device_ids
        if args.register_device_ids is not None
        else (
            profile.register_device_ids
            if profile is not None
            else DEFAULT_DISCOVERY_REGISTER_DEVICE_IDS
        ),
        "register_group_ids": args.register_group_ids
        if args.register_group_ids is not None
        else (
            profile.register_group_ids
            if profile is not None
            else DEFAULT_DISCOVERY_REGISTER_GROUP_IDS
        ),
        "control_kinds": args.control_kinds
        if args.control_kinds is not None
        else (
            profile.control_kinds
            if profile is not None
            else DEFAULT_DISCOVERY_CONTROL_KINDS
        ),
        "control_modes": args.control_modes
        if args.control_modes is not None
        else (profile.control_modes if profile is not None else None),
    }


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
            read_sn = light.read_sn()
            if read_sn:
                result["read_sn"] = {
                    "prefix": read_sn.prefix,
                    "product": f"0x{read_sn.product:04x}",
                    "identifier_little_endian_hex": (
                        read_sn.identifier_little_endian_hex
                    ),
                    "device_identifier": read_sn.device_identifier,
                    "raw_hex": read_sn.raw.hex(),
                }
    print_json(result, compact=args.json)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    if args.transport == "ble":
        try:
            report = asyncio.run(_status_ble(args))
        except RuntimeError as exc:
            return print_ble_runtime_error(exc, compact=args.json)
    else:
        with sync_usb_light_from_args(args) as light:
            report = read_sync_status(
                light,
                transport=args.transport,
                timeout=args.timeout,
            )
    print_json(report.to_dict(), compact=args.json)
    return 0 if report.connection_confirmed else 1


def cmd_ready(args: argparse.Namespace) -> int:
    payload = local_readiness(
        light_connection_config_from_args(args),
        allow_control=args.allow_control,
        include_ble=False,
        include_ble_status=None,
    )
    print_json(payload, compact=args.json)
    return 0 if payload.get("connection_confirmed") is True else 2


def cmd_integration(args: argparse.Namespace) -> int:
    snapshot = local_integration_snapshot(
        light_connection_config_from_args(args),
        allow_control=args.allow_control,
        include_ble=args.include_ble,
        include_ble_status=True if args.include_ble_status else None,
    )
    print_json(snapshot, compact=args.json)
    payloads = snapshot.get("payloads")
    raw_ready = payloads.get("ready") if isinstance(payloads, dict) else {}
    ready = raw_ready if isinstance(raw_ready, dict) else {}
    return 0 if ready.get("connection_confirmed") is True else 2


def cmd_metadata(args: argparse.Namespace) -> int:
    preset_names = library_names(load_preset_library(args))
    cue_names = library_names(load_cue_library(args))
    config = light_connection_config_from_args(args)
    manifest = local_manifest(
        config,
        allow_control=args.allow_control,
        presets=preset_names,
        cues=cue_names,
    )
    capabilities = local_capabilities(
        allow_control=args.allow_control,
        presets=preset_names,
        cues=cue_names,
    )
    payloads = {
        "openapi": openapi_schema(),
        "manifest": manifest,
        "capabilities": capabilities,
    }
    payload = (
        {
            "api": "zhiyun-light-control",
            "version": "0.1.0",
            "transport": config.to_dict(),
            "payloads": payloads,
        }
        if args.kind == "all"
        else payloads[args.kind]
    )
    print_json(payload, compact=args.json)
    return 0


async def _status_ble(args: argparse.Namespace):
    async with async_ble_light_from_args(args) as light:
        return await read_async_status(
            light,
            transport=args.transport,
            timeout=args.timeout,
        )


def cmd_validate(args: argparse.Namespace) -> int:
    if args.transport == "ble":
        backend = "direct" if args.unsafe_in_process else args.ble_backend
        if backend == "worker":
            raise SystemExit(
                "BLE validation can run many exchanges; run scan-ble first, then "
                "re-run validate with --ble-backend macos-app on macOS or "
                "--unsafe-in-process on a stable direct bleak runtime"
            )
        try:
            report = asyncio.run(_validate_ble(args))
        except RuntimeError as exc:
            return print_ble_runtime_error(exc, compact=args.json)
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
                control_mode=args.control_mode,
            )
    print_json(report.to_dict(), compact=args.json)
    if args.strict and not report.all_attempted_confirmed:
        return 1
    return 0 if report.connection_confirmed else 1


def cmd_discover_usb(args: argparse.Namespace) -> int:
    options = usb_discovery_options_from_args(args)
    with sync_usb_light_from_args(args) as light:
        report = discover_usb_primitives(
            light,
            profile=options["profile"],
            object_ids=options["object_ids"],
            first_words=options["first_words"],
            control_object_ids=options["control_object_ids"],
            control_first_words=options["control_first_words"],
            register_device_ids=options["register_device_ids"],
            register_group_ids=options["register_group_ids"],
            control_kinds=options["control_kinds"],
            control_modes=options["control_modes"],
            post_register_reads=args.post_register_reads,
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
            control_mode=args.control_mode,
        )


async def _probe_ble(args: argparse.Namespace):
    async with async_ble_light_from_args(args) as light:
        return await light.probe()


def cmd_scan_ble(args: argparse.Namespace) -> int:
    backend = "direct" if args.unsafe_in_process else args.backend
    if backend == "direct":
        devices = filter_ble_devices_by_name(
            asyncio.run(scan_zhiyun_devices(timeout=args.timeout)),
            args.name_contains,
        )
        print_json({"ok": True, "devices": [device.to_dict() for device in devices]})
        return 0
    if backend == "macos-app":
        result = scan_zhiyun_devices_macos_app(
            timeout=args.timeout,
            name_contains=args.name_contains,
            include_all=args.include_all,
        )
        print_json(result.to_dict())
        return 0 if result.ok else 2
    result = scan_zhiyun_devices_safe(
        timeout=args.timeout,
        name_contains=args.name_contains,
        python=args.python,
    )
    print_json(result.to_dict())
    return 0 if result.ok else 2


def cmd_inspect_ble(args: argparse.Namespace) -> int:
    backend = "direct" if args.unsafe_in_process else args.backend
    result = inspect_ble_device(
        backend=backend,
        timeout=args.timeout,
        address=args.address,
        name_contains=args.name_contains,
        python=args.python,
    )
    print_json(result.to_dict(), compact=args.json)
    return 0 if result.ok else 2


def cmd_test_ble_endpoints(args: argparse.Namespace) -> int:
    backend = "direct" if args.unsafe_in_process else args.backend
    result = test_ble_endpoint_candidates(
        backend=backend,
        timeout=args.timeout,
        address=args.address,
        name_contains=args.name_contains,
        python=args.python,
        max_candidates=args.max_candidates,
    )
    print_json(result.to_dict(), compact=args.json)
    return 0 if result.ok else 2


def cmd_mesh_probe(args: argparse.Namespace) -> int:
    tx = build_provisioning_invite(args.attention)
    exchange_args = {
        "address": args.address,
        "name_contains": args.name_contains,
        "timeout": args.timeout,
        "service_uuid": MESH_PROVISIONING_SERVICE_UUID,
        "write_uuid": MESH_PROVISIONING_WRITE_UUID,
        "notify_uuid": MESH_PROVISIONING_NOTIFY_UUID,
    }
    if args.backend == "macos-app":
        result = exchange_zhiyun_ble_macos_app(tx, **exchange_args)
    else:
        result = exchange_zhiyun_ble_safe(tx, python=args.python, **exchange_args)
    payload = {
        "ok": result.ok,
        "probe": "mesh_provisioning_invite",
        "attention": args.attention,
        "exchange": result.to_dict(),
        "capabilities": None,
        "parse_error": None,
    }
    if result.rx:
        try:
            payload["capabilities"] = parse_provisioning_capabilities(
                result.rx
            ).to_dict()
        except ValueError as exc:
            payload["parse_error"] = str(exc)
    print_json(payload, compact=args.json)
    return 0 if result.ok and result.rx and payload["capabilities"] else 2


def cmd_mesh_handshake(args: argparse.Namespace) -> int:
    keypair = generate_provisioner_keypair()
    start = build_provisioning_start_no_oob()
    provisioner_random = generate_provisioning_random()
    frames = (
        build_provisioning_invite(args.attention),
        start,
        build_provisioning_public_key(keypair.public_key.xy),
    )
    result = exchange_zhiyun_ble_sequence_macos_app(
        frames,
        address=args.address,
        name_contains=args.name_contains,
        timeout=args.timeout,
        service_uuid=MESH_PROVISIONING_SERVICE_UUID,
        write_uuid=MESH_PROVISIONING_WRITE_UUID,
        notify_uuid=MESH_PROVISIONING_NOTIFY_UUID,
    )
    capabilities: dict[str, object] | None = None
    capabilities_pdu: bytes | None = None
    provisionee_public_key: dict[str, object] | None = None
    provisionee_public_key_xy: bytes | None = None
    shared_secret_hex: str | None = None
    provisioner_confirmation_hex: str | None = None
    provisioner_random_hex: str | None = None
    parse_errors: list[str] = []
    for index, rx in enumerate(result.rx):
        if not rx:
            continue
        if capabilities is None:
            try:
                capabilities = parse_provisioning_capabilities(rx).to_dict()
                capabilities_pdu = rx
                continue
            except ValueError as exc:
                parse_errors.append(f"rx[{index}] capabilities: {exc}")
        if provisionee_public_key is None:
            try:
                public_key = parse_provisioning_public_key(rx)
                provisionee_public_key = public_key.to_dict()
                provisionee_public_key_xy = public_key.xy
                shared_secret = derive_shared_ecdh_secret(
                    keypair.private_key,
                    public_key.xy,
                )
                shared_secret_hex = shared_secret.hex()
            except ValueError as exc:
                parse_errors.append(f"rx[{index}] public_key: {exc}")
    if capabilities_pdu is not None and provisionee_public_key_xy is not None:
        inputs = confirmation_inputs(
            invite_pdu=frames[0],
            capabilities_pdu=capabilities_pdu,
            start_pdu=start,
            provisioner_public_key_xy=keypair.public_key.xy,
            provisionee_public_key_xy=provisionee_public_key_xy,
        )
        shared_secret = bytes.fromhex(shared_secret_hex or "")
        provisioner_confirmation_hex = build_provisioner_confirmation(
            shared_secret=shared_secret,
            confirmation_inputs=inputs,
            provisioner_random=provisioner_random,
        ).hex()
        provisioner_random_hex = build_provisioner_random(provisioner_random).hex()
    payload = {
        "ok": result.ok,
        "probe": "mesh_provisioning_handshake",
        "exchange": result.to_dict(),
        "provisioner_public_key": keypair.public_key.to_dict(),
        "capabilities": capabilities,
        "provisionee_public_key": provisionee_public_key,
        "shared_ecdh_secret_hex": shared_secret_hex,
        "next_frames": {
            "provisioner_confirmation_hex": provisioner_confirmation_hex,
            "provisioner_random_hex": provisioner_random_hex,
        },
        "parse_errors": parse_errors,
        "complete": bool(capabilities and provisionee_public_key and shared_secret_hex),
    }
    print_json(payload, compact=args.json)
    return 0 if payload["complete"] else 2


def cmd_mesh_session(args: argparse.Namespace) -> int:
    keypair = generate_provisioner_keypair()
    invite = build_provisioning_invite(args.attention)
    start = build_provisioning_start_no_oob()
    public_key_frame = build_provisioning_public_key(keypair.public_key.xy)
    provisioner_random = generate_provisioning_random()
    frames: list[bytes] = [invite, start, public_key_frame]
    rx_values: list[bytes] = []
    parse_errors: list[str] = []
    error: str | None = None
    session_result = None
    capabilities: dict[str, object] | None = None
    provisionee_public_key: dict[str, object] | None = None
    shared_secret: bytes | None = None
    inputs: bytes | None = None
    provisionee_confirmation: bytes | None = None
    provisionee_random: bytes | None = None
    confirmation_verified = False
    failure: dict[str, object] | None = None
    secrets: dict[str, object] | None = None

    session = open_zhiyun_ble_ipc_macos_app(
        address=args.address,
        name_contains=args.name_contains,
        timeout=args.timeout,
        service_uuid=MESH_PROVISIONING_SERVICE_UUID,
        write_uuid=MESH_PROVISIONING_WRITE_UUID,
        notify_uuid=MESH_PROVISIONING_NOTIFY_UUID,
    )
    try:
        with session:
            capabilities_rx = session.exchange(invite, timeout=args.timeout)
            rx_values.append(capabilities_rx)
            parsed_capabilities = parse_provisioning_capabilities(capabilities_rx)
            capabilities = parsed_capabilities.to_dict()

            start_rx = session.exchange(start, timeout=args.timeout)
            rx_values.append(start_rx)

            public_key_rx = session.exchange(public_key_frame, timeout=args.timeout)
            rx_values.append(public_key_rx)
            parsed_public_key = parse_provisioning_public_key(public_key_rx)
            provisionee_public_key = parsed_public_key.to_dict()
            shared_secret = derive_shared_ecdh_secret(
                keypair.private_key,
                parsed_public_key.xy,
            )
            inputs = confirmation_inputs(
                invite_pdu=invite,
                capabilities_pdu=capabilities_rx,
                start_pdu=start,
                provisioner_public_key_xy=keypair.public_key.xy,
                provisionee_public_key_xy=parsed_public_key.xy,
            )

            confirmation_frame = build_provisioner_confirmation(
                shared_secret=shared_secret,
                confirmation_inputs=inputs,
                provisioner_random=provisioner_random,
            )
            frames.append(confirmation_frame)
            confirmation_rx = session.exchange(confirmation_frame, timeout=args.timeout)
            rx_values.append(confirmation_rx)
            provisionee_confirmation = parse_provisioning_confirmation(
                confirmation_rx
            )

            random_frame = build_provisioner_random(provisioner_random)
            frames.append(random_frame)
            random_rx = session.exchange(random_frame, timeout=args.timeout)
            rx_values.append(random_rx)
            try:
                provisionee_random = parse_provisioning_random(random_rx)
            except ValueError:
                failure = parse_provisioning_failure(random_rx).to_dict()
                raise
            confirmation_verified = verify_provisionee_confirmation(
                shared_secret=shared_secret,
                confirmation_inputs=inputs,
                provisionee_confirmation=provisionee_confirmation,
                provisionee_random=provisionee_random,
            )
            secrets = provisioning_session_secrets(
                shared_secret=shared_secret,
                confirmation_inputs=inputs,
                provisioner_random=provisioner_random,
                provisionee_random=provisionee_random,
            ).to_dict()
            session_result = session.close()
    except Exception as exc:
        error = str(exc)
        try:
            session_result = session.close()
            if session_result.error is not None:
                error = session_result.error
        except Exception as close_exc:
            parse_errors.append(f"close: {close_exc}")

    payload = {
        "ok": bool(confirmation_verified and error is None),
        "probe": "mesh_provisioning_dynamic_session",
        "attention": args.attention,
        "exchange": session_result.to_dict() if session_result else None,
        "tx_hexes": [frame.hex() for frame in frames],
        "rx_hexes": [value.hex() if value else None for value in rx_values],
        "provisioner_public_key": keypair.public_key.to_dict(),
        "capabilities": capabilities,
        "provisionee_public_key": provisionee_public_key,
        "shared_ecdh_secret_hex": shared_secret.hex() if shared_secret else None,
        "confirmation_inputs_hex": inputs.hex() if inputs else None,
        "provisionee_confirmation_hex": (
            provisionee_confirmation.hex() if provisionee_confirmation else None
        ),
        "provisioner_random_hex": provisioner_random.hex(),
        "provisionee_random_hex": (
            provisionee_random.hex() if provisionee_random else None
        ),
        "confirmation_verified": confirmation_verified,
        "session_secrets": secrets,
        "failure": failure,
        "error": error,
        "parse_errors": parse_errors,
        "complete": bool(confirmation_verified),
    }
    print_json(payload, compact=args.json)
    return 0 if payload["ok"] else 2


def cmd_mesh_provision_plan(args: argparse.Namespace) -> int:
    try:
        session = load_mesh_session_payload(args.session_json)
        shared_secret = required_hex_field(session, "shared_ecdh_secret_hex")
        inputs = required_hex_field(session, "confirmation_inputs_hex")
        provisioner_random = required_hex_field(session, "provisioner_random_hex")
        provisionee_random = required_hex_field(session, "provisionee_random_hex")
        network_key = args.network_key_hex or generate_network_key()
        plan = build_provisioning_data_plan(
            shared_secret=shared_secret,
            confirmation_inputs=inputs,
            provisioner_random=provisioner_random,
            provisionee_random=provisionee_random,
            network_key=network_key,
            key_index=args.key_index,
            flags=args.flags,
            iv_index=args.iv_index,
            unicast_address=args.unicast_address,
        )
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "hint": (
                "Run mesh-session with this SDK version and pass its complete "
                "JSON output to --session-json."
            ),
        }
        print_json(payload, compact=args.json)
        return 2

    payload = {
        "ok": True,
        "action": "mesh_provision_plan",
        "offline": True,
        "send_warning": (
            "This PDU is persistent only if it is sent to the provisioning "
            "characteristic; this command does not send it."
        ),
        "plan": plan.to_dict(),
    }
    print_json(payload, compact=args.json)
    return 0


def cmd_mesh_setup_plan(args: argparse.Namespace) -> int:
    try:
        plan = build_zy_mesh_network_plan(
            mesh_uuid=args.mesh_uuid_hex,
            network_key=args.network_key_hex,
            app_key=args.app_key_hex,
            key_index=args.key_index,
            app_key_index=args.app_key_index,
            flags=args.flags,
            iv_index=args.iv_index,
            light_unicast_address=args.unicast_address,
        )
        config_sequence = build_mesh_config_sequence_plan(
            plan.app_key,
            net_key_index=plan.key_index,
            app_key_index=plan.app_key_index,
        )
        proxy_pdu_sequence = ()
        if args.device_key_hex is not None:
            proxy_pdu_sequence = build_mesh_config_proxy_pdu_sequence(
                config_sequence,
                network_key=plan.network_key,
                device_key=args.device_key_hex,
                src=plan.provisioner_unicast_address,
                dst=plan.light_unicast_address,
                iv_index=plan.iv_index,
                sequence_number=args.sequence_number,
                ttl=args.ttl,
            )
    except ValueError as exc:
        payload = {
            "ok": False,
            "error": str(exc),
        }
        print_json(payload, compact=args.json)
        return 2

    payload = {
        "ok": True,
        "action": "mesh_setup_plan",
        "offline": True,
        "send_warning": (
            "This builds the official setup data and post-provisioning config "
            "access messages only; it does not provision or control the light."
        ),
        "pipeline": [
            "pb_gatt_provisioning_1827_2adb_2adc",
            "mesh_proxy_configuration_1828_2add_2ade",
            "fee9_identity_status_and_native_runtime_control",
        ],
        "network": plan.to_dict(),
        "cdb": plan.to_cdb_dict(),
        "config_sequence": [step.to_dict() for step in config_sequence],
        "proxy_pdu_sequence": [step.to_dict() for step in proxy_pdu_sequence],
    }
    print_json(payload, compact=args.json)
    return 0


def cmd_mesh_config_send(args: argparse.Namespace) -> int:
    require_yes(
        args,
        "mesh-config-send writes persistent configuration to the BLE Mesh Proxy",
    )
    try:
        plan = build_zy_mesh_network_plan(
            mesh_uuid=args.mesh_uuid_hex,
            network_key=args.network_key_hex,
            app_key=args.app_key_hex,
            key_index=args.key_index,
            app_key_index=args.app_key_index,
            iv_index=args.iv_index,
            light_unicast_address=args.unicast_address,
        )
        config_sequence = build_mesh_config_sequence_plan(
            plan.app_key,
            net_key_index=plan.key_index,
            app_key_index=plan.app_key_index,
        )
        proxy_pdu_sequence = build_mesh_config_proxy_pdu_sequence(
            config_sequence,
            network_key=plan.network_key,
            device_key=args.device_key_hex,
            src=plan.provisioner_unicast_address,
            dst=plan.light_unicast_address,
            iv_index=plan.iv_index,
            sequence_number=args.sequence_number,
            ttl=args.ttl,
        )
    except ValueError as exc:
        payload = {
            "ok": False,
            "error": str(exc),
        }
        print_json(payload, compact=args.json)
        return 2

    tx = tuple(pdu for step in proxy_pdu_sequence for pdu in step.proxy_pdus)
    exchange_args = {
        "address": args.address,
        "name_contains": args.name_contains,
        "timeout": args.timeout,
        "profile": "mesh-proxy",
        "service_uuid": MESH_PROXY_SERVICE_UUID,
        "write_uuid": MESH_PROXY_WRITE_UUID,
        "notify_uuid": MESH_PROXY_NOTIFY_UUID,
    }
    if args.ble_backend == "macos-app":
        result = exchange_zhiyun_ble_sequence_macos_app(tx, **exchange_args)
    else:
        result = exchange_zhiyun_ble_sequence_safe(
            tx,
            python=args.python,
            **exchange_args,
        )

    payload = {
        "ok": result.ok,
        "action": "mesh_config_send",
        "backend": args.ble_backend,
        "network": plan.to_dict(),
        "config_sequence": [step.to_dict() for step in config_sequence],
        "proxy_pdu_sequence": [step.to_dict() for step in proxy_pdu_sequence],
        "exchange": result.to_dict(),
    }
    print_json(payload, compact=args.json)
    return 0 if result.ok else 2


def load_mesh_session_payload(path: str) -> dict[str, object]:
    if path == "-":
        text = sys.stdin.read()
    else:
        with open(path, encoding="utf-8") as stream:
            text = stream.read()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("mesh-session JSON must be an object")
    return payload


def required_hex_field(payload: dict[str, object], name: str) -> bytes:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"mesh-session JSON is missing {name}")
    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"mesh-session field {name} is not valid hex") from exc


def cmd_devices(args: argparse.Namespace) -> int:
    payload = discover_transport_devices(
        configured_transport=args.transport,
        configured_usb_port=args.port,
        include_ble=args.include_ble,
        include_ble_status=args.include_ble_status,
        ble_backend=args.ble_backend,
        ble_timeout=args.ble_timeout,
        ble_name_contains=args.name_contains,
        ble_python=args.python,
    )
    print_json(payload, compact=args.json)
    ble = payload.get("ble")
    if not isinstance(ble, dict):
        return 0
    status = ble.get("macos_status")
    scan = ble.get("scan")
    status_failed = (
        args.include_ble_status and isinstance(status, dict) and not status.get("ok")
    )
    scan_failed = args.include_ble and isinstance(scan, dict) and not scan.get("ok")
    return 2 if status_failed or scan_failed else 0


def cmd_ble_helper(args: argparse.Namespace) -> int:
    helper_kwargs = {
        "bundle_name": args.bundle_name,
        "bundle_id": args.bundle_id,
    }
    payload = {"helper": macos_ble_app_info(ensure=args.ensure, **helper_kwargs)}
    code = 0
    if args.ensure and not payload["helper"]["ok"]:
        code = 2
    if args.status:
        status = macos_ble_app_status(timeout=args.timeout, **helper_kwargs)
        payload["status"] = status
        if not status["ok"]:
            code = 2
    if args.authorize:
        authorization = macos_ble_app_authorize(timeout=args.timeout, **helper_kwargs)
        payload["authorization"] = authorization
        if not authorization["ok"]:
            code = 2
    if args.open_settings:
        settings = open_macos_bluetooth_settings()
        payload["open_settings"] = settings
        if not settings["ok"]:
            code = 2
    print_json(payload, compact=args.json)
    return code


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
            brightness_payload(
                args.obj,
                value,
                read=False,
                control_mode=args.control_mode,
            ),
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
            cct_payload(
                args.obj,
                kelvin,
                read=False,
                control_mode=args.control_mode,
            ),
        )
    if args.kind == "sleep":
        value = int(require_value(args.value, "--value is required for sleep"))
        return light.exchange_runtime(
            RuntimeCommand.SLEEP,
            sleep_payload(
                args.obj,
                value,
                read=False,
                control_mode=args.control_mode,
            ),
        )
    if args.kind == "rgb":
        if args.red is None or args.green is None or args.blue is None:
            raise SystemExit("--red, --green, and --blue are required for rgb")
        return light.exchange_runtime(
            RuntimeCommand.RGB,
            rgb_payload(
                args.obj,
                args.red,
                args.green,
                args.blue,
                control_mode=args.control_mode,
            ),
        )
    raise AssertionError(args.kind)


async def _set_ble(args: argparse.Namespace):
    async with async_ble_light_from_args(args) as light:
        if args.kind == "brightness":
            value = require_value(args.value, "--value is required for brightness")
            return await light.exchange_runtime(
                RuntimeCommand.BRIGHTNESS,
                brightness_payload(args.obj, value, control_mode=args.control_mode),
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
                cct_payload(args.obj, kelvin, control_mode=args.control_mode),
            )
        if args.kind == "sleep":
            value = int(require_value(args.value, "--value is required for sleep"))
            return await light.exchange_runtime(
                RuntimeCommand.SLEEP,
                sleep_payload(args.obj, value, control_mode=args.control_mode),
            )
        if args.kind == "rgb":
            if args.red is None or args.green is None or args.blue is None:
                raise SystemExit("--red, --green, and --blue are required for rgb")
            return await light.exchange_runtime(
                RuntimeCommand.RGB,
                rgb_payload(
                    args.obj,
                    args.red,
                    args.green,
                    args.blue,
                    control_mode=args.control_mode,
                ),
            )
    raise AssertionError(args.kind)


def cmd_apply(args: argparse.Namespace) -> int:
    scene = scene_from_args(args)
    if args.dry_run:
        print_json(
            {
                "dry_run": True,
                "scene": scene.to_dict(),
                "first_word": args.first_word,
                "first_word_hex": f"0x{args.first_word:04x}",
                "results": [],
            }
        )
        return 0
    require_yes(args, "apply sends one or more control commands to the light")
    if args.transport == "ble":
        try:
            results = asyncio.run(_apply_ble(args, scene))
        except RuntimeError as exc:
            return print_ble_runtime_error(exc)
    else:
        with sync_usb_light_from_args(args) as light:
            if args.first_word == RUNTIME_TYPE:
                results = light.apply_scene(scene, control_mode=args.control_mode)
            else:
                plan = scene_command_plan(
                    scene,
                    control_mode=args.control_mode,
                    first_word=args.first_word,
                )
                results = execute_frame_plan(light, plan, timeout=args.timeout)
    print_json(
        {
            "scene": scene.to_dict(),
            "first_word": args.first_word,
            "first_word_hex": f"0x{args.first_word:04x}",
            "accepted_echo": args.accept_echo,
            "results": [result.to_dict() for result in results],
        }
    )
    return command_results_exit_code(results, accept_echo=args.accept_echo)


def cmd_plan(args: argparse.Namespace) -> int:
    scene = scene_from_args(args)
    command_plan = scene_command_plan(
        scene,
        control_mode=args.control_mode,
        first_word=args.first_word,
        start_seq=args.start_seq,
    )
    plan = {
        "action": "scene",
        "scene": scene.to_dict(),
        "control_mode": args.control_mode,
        "first_word": args.first_word,
        "first_word_hex": f"0x{args.first_word:04x}",
        "command_plan": command_plan.to_dict(),
    }
    bundle = serialized_plan_bundle(plan)
    if args.output:
        bundle.save(args.output)
    print_json(bundle.to_dict(), compact=args.json)
    return 0


def cmd_execute_plan(args: argparse.Namespace) -> int:
    require_yes(args, "execute-plan sends one or more planned frames to the light")
    bundle = plan_bundle_from_arg(args.plan_file)
    if args.base_url:
        client = LightBridgeClient(args.base_url, timeout=args.bridge_timeout)
        try:
            result = client.execute_plan(bundle, timeout=args.timeout)
        except LightBridgeError as exc:
            print_json(
                {
                    "ok": False,
                    "status": exc.status,
                    "payload": exc.payload,
                },
                compact=args.json,
            )
            return 1
        print_json(result, compact=args.json)
        return 0 if result.get("applied") is True else 1
    if args.transport == "ble":
        try:
            results = asyncio.run(_execute_plan_ble(args, bundle))
        except RuntimeError as exc:
            return print_ble_runtime_error(exc, compact=args.json)
    else:
        with sync_usb_light_from_args(args) as light:
            results = execute_serialized_frame_plan(
                light,
                bundle,
                timeout=args.timeout,
            )
    response = serialized_plan_execution_response(bundle, results)
    print_json(response, compact=args.json)
    return command_results_exit_code(results)


async def _execute_plan_ble(args: argparse.Namespace, bundle) -> list[CommandResult]:
    async with async_ble_light_from_args(args) as light:
        return await execute_async_serialized_frame_plan(
            light,
            bundle,
            timeout=args.timeout,
        )


def plan_bundle_from_arg(path: str):
    if path == "-":
        return load_serialized_plan_bundle_from_text(sys.stdin.read())
    return load_serialized_plan_bundle(path)


def load_serialized_plan_bundle_from_text(text: str):
    return serialized_plan_bundle_from_json(text)


def serialized_plan_execution_response(bundle, results: list[CommandResult]):
    plan = serialized_plan_payload(bundle)
    result_items = list(results)
    applied = all(result.acknowledged for result in result_items)
    response: dict[str, object] = {
        "action": "execute_plan",
        "planned_action": str(plan.get("action", "unknown")),
        "plan": dict(plan),
        "results": [result.to_dict() for result in result_items],
        "applied": applied,
        "reason": None if applied else unconfirmed_results_reason(result_items),
    }
    raw_scene = plan.get("scene")
    if isinstance(raw_scene, dict):
        response["scene"] = dict(raw_scene)
    for key in ("preset", "cue"):
        if key in plan:
            response[key] = plan[key]
    return response


def unconfirmed_results_reason(results: list[CommandResult]) -> str | None:
    unconfirmed = [
        f"0x{result.command:04x}:{result.transport_status}"
        for result in results
        if not result.acknowledged
    ]
    if not unconfirmed:
        return None
    return "unconfirmed command results: " + ", ".join(unconfirmed)


def cmd_cue(args: argparse.Namespace) -> int:
    library = CueLibrary.load(args.cue_file)
    if args.list:
        print_json({"cues": library.names()})
        return 0
    if not args.cue:
        raise SystemExit("--cue is required unless --list is used")
    cue = library.get(args.cue)
    if args.dry_run:
        request = dict(cue)
        request["control_mode"] = args.control_mode
        print_json({"dry_run": True, "cue": args.cue, "request": request})
        return 0
    require_yes(args, "cue sends one or more control commands through the bridge")
    client = LightBridgeClient(args.base_url, timeout=args.timeout)
    try:
        result = client.run_cue(cue, control_mode=args.control_mode)
    except LightBridgeError as exc:
        print_json(
            {
                "ok": False,
                "cue": args.cue,
                "status": exc.status,
                "payload": exc.payload,
            }
        )
        return 1
    output = {"cue": args.cue, **result}
    print_json(output)
    return 0 if output.get("applied") is True else 1


def command_result_exit_code(result: CommandResult) -> int:
    return 0 if result.acknowledged else 1


def command_results_exit_code(
    results: list[CommandResult],
    *,
    accept_echo: bool = False,
) -> int:
    accepted = all(
        result.acknowledged or (accept_echo and result.echoed) for result in results
    )
    return 0 if accepted else 1


def sync_usb_light_from_args(args: argparse.Namespace) -> ZhiyunLight:
    return ZhiyunLight.usb(
        port=args.port,
        timeout=args.timeout,
        lock_timeout=args.usb_lock_timeout,
    )


async def _apply_ble(args: argparse.Namespace, scene: Scene) -> list[CommandResult]:
    async with async_ble_light_from_args(args) as light:
        if args.first_word != RUNTIME_TYPE:
            plan = scene_command_plan(
                scene,
                control_mode=args.control_mode,
                first_word=args.first_word,
            )
            return await execute_async_frame_plan(light, plan, timeout=args.timeout)
        return await light.apply_scene(scene, control_mode=args.control_mode)


def async_ble_light_from_args(args: argparse.Namespace) -> AsyncZhiyunLight:
    backend = (
        "direct"
        if getattr(args, "unsafe_in_process", False)
        else getattr(args, "ble_backend", "worker")
    )
    if backend == "direct":
        return AsyncZhiyunLight.ble(
            address=args.address,
            name_contains=args.name_contains,
            profile=args.ble_profile,
            service_uuid=args.ble_service_uuid,
            write_uuid=args.ble_write_uuid,
            notify_uuid=args.ble_notify_uuid,
            timeout=args.timeout,
        )
    if backend == "macos-app":
        return AsyncZhiyunLight.macos_ble_app(
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


def load_cue_library(args: argparse.Namespace) -> CueLibrary | None:
    path = getattr(args, "cue_file", None)
    return CueLibrary.load(path) if path else None


def library_names(library) -> list[str]:
    return [] if library is None else library.names()


def cmd_serve(args: argparse.Namespace) -> int:
    serve(
        host=args.host,
        port=args.port,
        light_port=args.light_port,
        allow_control=args.allow_control,
        light_factory=bridge_light_factory(args),
        preset_library=load_preset_library(args),
        cue_library=load_cue_library(args),
        cors_origin=args.cors_origin,
        transport=args.transport,
        ble_backend="direct" if args.unsafe_in_process else args.ble_backend,
        ble_profile=args.ble_profile,
        ble_address=args.address,
        ble_name_contains=args.name_contains,
        ble_python=args.ble_python,
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
        cue_library=load_cue_library(args),
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
        light_connection_config_from_args(
            args,
            persistent=not args.no_persistent_light,
        )
    )


def light_connection_config_from_args(
    args: argparse.Namespace,
    *,
    persistent: bool = False,
) -> LightConnectionConfig:
    timeout = getattr(args, "light_timeout", getattr(args, "timeout", 1.5))
    ble_python = getattr(args, "ble_python", getattr(args, "python", None))
    unsafe_in_process = getattr(args, "unsafe_in_process", False)
    ble_backend = (
        "direct" if unsafe_in_process else getattr(args, "ble_backend", "worker")
    )
    return LightConnectionConfig(
        transport=getattr(args, "transport", "usb"),
        port=getattr(args, "light_port", getattr(args, "port", None)),
        address=getattr(args, "address", None),
        name_contains=getattr(args, "name_contains", None),
        timeout=timeout,
        usb_lock_timeout=getattr(args, "usb_lock_timeout", DEFAULT_LOCK_TIMEOUT),
        ble_profile=getattr(args, "ble_profile", DEFAULT_BLE_PROFILE.name),
        ble_service_uuid=getattr(args, "ble_service_uuid", None),
        ble_write_uuid=getattr(args, "ble_write_uuid", None),
        ble_notify_uuid=getattr(args, "ble_notify_uuid", None),
        ble_backend=ble_backend,
        ble_python=ble_python,
        ble_in_process=unsafe_in_process,
        persistent=persistent,
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
