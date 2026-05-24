"""Worker process used for crash-isolated BLE operations."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .transports.ble import (
    BLE_PROFILE_NAMES,
    DEFAULT_BLE_PROFILE,
    BleTransport,
    filter_ble_devices_by_name,
    inspect_zhiyun_device,
    scan_zhiyun_devices,
)


def main(argv: list[str] | None = None) -> int:
    args_list = sys.argv[1:] if argv is None else argv
    if not args_list or args_list[0].startswith("-"):
        return _legacy_scan_main(args_list)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Scan for likely Zhiyun BLE devices.")
    scan.add_argument("--timeout", type=float, default=5.0)
    scan.add_argument("--name-contains")

    inspect = sub.add_parser("inspect", help="Inspect BLE GATT services.")
    inspect.add_argument("--address")
    inspect.add_argument("--name-contains")
    inspect.add_argument("--timeout", type=float, default=5.0)

    exchange = sub.add_parser("exchange-raw", help="Exchange one raw frame.")
    exchange.add_argument("--tx-hex", required=True)
    exchange.add_argument("--address")
    exchange.add_argument("--name-contains")
    exchange.add_argument("--timeout", type=float, default=1.5)
    exchange.add_argument(
        "--profile",
        choices=BLE_PROFILE_NAMES,
        default=DEFAULT_BLE_PROFILE.name,
    )
    exchange.add_argument("--service-uuid")
    exchange.add_argument("--write-uuid")
    exchange.add_argument("--notify-uuid")

    sequence = sub.add_parser("exchange-sequence", help="Exchange raw frames.")
    sequence.add_argument("--tx-hexes", required=True)
    sequence.add_argument("--address")
    sequence.add_argument("--name-contains")
    sequence.add_argument("--timeout", type=float, default=1.5)
    sequence.add_argument(
        "--profile",
        choices=BLE_PROFILE_NAMES,
        default=DEFAULT_BLE_PROFILE.name,
    )
    sequence.add_argument("--service-uuid")
    sequence.add_argument("--write-uuid")
    sequence.add_argument("--notify-uuid")

    args = parser.parse_args(args_list)
    if args.command == "scan":
        return _scan_main(args.timeout, args.name_contains)
    if args.command == "inspect":
        return _inspect_main(args)
    if args.command == "exchange-raw":
        return _exchange_main(args)
    if args.command == "exchange-sequence":
        return _exchange_sequence_main(args)
    raise AssertionError(args.command)


def _legacy_scan_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--name-contains")
    args = parser.parse_args(argv)
    return _scan_main(args.timeout, args.name_contains)


def _scan_main(timeout: float, name_contains: str | None = None) -> int:
    try:
        devices = filter_ble_devices_by_name(
            asyncio.run(
                scan_zhiyun_devices(
                    timeout=timeout,
                    name_contains=name_contains,
                )
            ),
            name_contains,
        )
    except Exception as exc:
        print(json.dumps({"devices": [], "error": str(exc)}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {"devices": [device.to_dict() for device in devices]},
            sort_keys=True,
        )
    )
    return 0


def _inspect_main(args: argparse.Namespace) -> int:
    result = asyncio.run(
        inspect_zhiyun_device(
            address=args.address,
            name_contains=args.name_contains,
            timeout=args.timeout,
        )
    )
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0 if result.ok else 1


def _exchange_main(args: argparse.Namespace) -> int:
    try:
        payload = bytes.fromhex(args.tx_hex)
        result = asyncio.run(_exchange_raw(payload, args))
    except Exception as exc:
        print(json.dumps({"address": args.address, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


def _exchange_sequence_main(args: argparse.Namespace) -> int:
    try:
        payloads = tuple(bytes.fromhex(item) for item in args.tx_hexes.split(","))
        result = asyncio.run(_exchange_sequence(payloads, args))
    except Exception as exc:
        print(json.dumps({"address": args.address, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


async def _exchange_raw(
    payload: bytes,
    args: argparse.Namespace,
) -> dict[str, object]:
    async with BleTransport(
        address=args.address,
        name_contains=args.name_contains,
        profile=args.profile,
        service_uuid=args.service_uuid,
        write_uuid=args.write_uuid,
        notify_uuid=args.notify_uuid,
        timeout=args.timeout,
    ) as transport:
        rx = await transport.exchange(payload, timeout=args.timeout)
        return {
            "address": transport.address,
            "rx_hex": rx.hex() if rx else None,
        }


async def _exchange_sequence(
    payloads: tuple[bytes, ...],
    args: argparse.Namespace,
) -> dict[str, object]:
    async with BleTransport(
        address=args.address,
        name_contains=args.name_contains,
        profile=args.profile,
        service_uuid=args.service_uuid,
        write_uuid=args.write_uuid,
        notify_uuid=args.notify_uuid,
        timeout=args.timeout,
    ) as transport:
        rx_items = []
        for payload in payloads:
            rx = await transport.exchange(payload, timeout=args.timeout)
            rx_items.append(rx.hex() if rx else None)
        return {
            "address": transport.address,
            "rx_hex": "".join(item or "" for item in rx_items) or None,
            "rx_hexes": rx_items,
        }


if __name__ == "__main__":
    raise SystemExit(main())
