from __future__ import annotations

import argparse
import json
from pathlib import Path

from zhiyun_light_control import (
    LightIntegration,
    load_light_connection_config,
    save_light_connection_config,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover, persist, and use a Zhiyun light SDK connection."
    )
    parser.add_argument(
        "--config",
        default="zhiyun-light.json",
        help="Path to a reusable LightConnectionConfig JSON file.",
    )
    parser.add_argument(
        "--rediscover",
        action="store_true",
        help="Ignore an existing config file and rerun local discovery.",
    )
    parser.add_argument(
        "--include-ble",
        action="store_true",
        help="Include BLE scan candidates during discovery.",
    )
    parser.add_argument(
        "--brightness",
        type=float,
        help="Optional brightness command to send after status probing.",
    )
    args = parser.parse_args()
    config_path = Path(args.config)

    integration = LightIntegration()
    has_saved_config = config_path.exists() and config_path.stat().st_size > 0
    if has_saved_config and not args.rediscover:
        config = load_light_connection_config(config_path)
    else:
        config = integration.best_connection_config(
            include_ble=args.include_ble,
            include_ble_status=True,
        )
        save_light_connection_config(config, config_path)

    integration = integration.with_config(config)
    status, ok, error = integration.status()
    payload: dict[str, object] = {
        "config": config.to_dict(),
        "status_ok": ok,
        "status_error": error,
        "status": status,
    }

    if args.brightness is not None:
        register = integration.register(device_id=0, group_id=0)
        brightness = integration.set_brightness(args.brightness)
        payload["register"] = {
            "acknowledged": register["acknowledged"],
            "transport_status": register["transport_status"],
        }
        payload["brightness"] = {
            "acknowledged": brightness["acknowledged"],
            "transport_status": brightness["transport_status"],
        }

    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
