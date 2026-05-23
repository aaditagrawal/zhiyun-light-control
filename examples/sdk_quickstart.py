from __future__ import annotations

import argparse
import json
from pathlib import Path

from zhiyun_light_control import (
    LightConnectionCandidate,
    LightConnectionConfig,
    LightIntegration,
    best_connection_config,
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
        "--persistent",
        action="store_true",
        help="Persist the selected config as a long-lived connection preference.",
    )
    parser.add_argument(
        "--include-object-reads",
        action="store_true",
        help="Include object-scoped read probes in the validation summary.",
    )
    parser.add_argument(
        "--allow-control",
        action="store_true",
        help="Allow the optional brightness command and control validation probes.",
    )
    parser.add_argument(
        "--brightness",
        type=float,
        help="Optional brightness command to send after status probing.",
    )
    args = parser.parse_args()
    if args.brightness is not None and not args.allow_control:
        parser.error("--brightness requires --allow-control")

    config_path = Path(args.config)

    integration = LightIntegration()
    has_saved_config = config_path.exists() and config_path.stat().st_size > 0
    routes: list[dict[str, object]] = []
    if has_saved_config and not args.rediscover:
        config = load_light_connection_config(config_path)
        setup_source = "saved"
    else:
        config, routes = discover_confirmed_config(
            integration,
            include_ble=args.include_ble,
            include_ble_status=True,
            persistent=args.persistent,
        )
        save_light_connection_config(config, config_path)
        setup_source = "discovered"

    integration = integration.with_config(config)
    status, ok, error = integration.status()
    readiness = integration.readiness(include_ble_status=True)
    validation = integration.validate(
        allow_control=args.allow_control,
        include_object_reads=args.include_object_reads,
    )
    payload: dict[str, object] = {
        "config": config.to_dict(),
        "setup_source": setup_source,
        "routes": routes,
        "status_ok": ok,
        "status_error": error,
        "status": status,
        "ready_for": readiness["ready_for"],
        "validation_ready_for": validation["summary"]["ready_for"],
        "validation_unconfirmed": validation["unconfirmed"],
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


def discover_confirmed_config(
    integration: LightIntegration,
    *,
    include_ble: bool,
    include_ble_status: bool,
    persistent: bool,
) -> tuple[LightConnectionConfig, list[dict[str, object]]]:
    routes = integration.probe_connection_candidates(
        include_ble=include_ble,
        include_ble_status=include_ble_status,
        persistent=persistent,
    )
    confirmed = tuple(route for route in routes if status_probe_confirmed(route))
    return best_connection_config(confirmed), [route.to_dict() for route in routes]


def status_probe_confirmed(candidate: LightConnectionCandidate) -> bool:
    evidence = candidate.evidence or {}
    status_probe = evidence.get("status_probe")
    if not isinstance(status_probe, dict):
        return False
    return status_probe.get("connection_confirmed") is True


if __name__ == "__main__":
    main()
