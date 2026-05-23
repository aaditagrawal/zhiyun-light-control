"""Worker process used for crash-isolated BLE scans."""

from __future__ import annotations

import argparse
import asyncio
import json

from .transports.ble import scan_zhiyun_devices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args(argv)
    try:
        devices = asyncio.run(scan_zhiyun_devices(timeout=args.timeout))
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


if __name__ == "__main__":
    raise SystemExit(main())
