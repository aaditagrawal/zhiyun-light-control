from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path

from zhiyun_light_control import (
    LightConnectionConfig,
    LightIntegration,
    LightSetupProfile,
    load_light_connection_config,
    load_light_setup_profile,
    save_light_connection_config,
    save_light_setup_profile,
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
        "--profile",
        help=(
            "Path to a reusable LightSetupProfile JSON file. When present, "
            "the profile is preferred over --config."
        ),
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
    profile_path = Path(args.profile) if args.profile else None

    integration = LightIntegration()
    has_saved_profile = (
        profile_path is not None
        and profile_path.exists()
        and profile_path.stat().st_size > 0
    )
    has_saved_config = config_path.exists() and config_path.stat().st_size > 0
    if has_saved_profile and not args.rediscover:
        profile = load_light_setup_profile(profile_path)
        profile.require_ready("read_status")
        integration = integration.with_setup_profile(profile, require="read_status")
        config = profile.config
        setup = dict(profile.setup_report)
        setup_source = "profile"
    elif has_saved_config and not args.rediscover:
        config = load_light_connection_config(config_path)
        integration = integration.with_config(config)
        setup = integration.setup_report(
            include_usb=False,
            include_ble=False,
            include_ble_status=True,
            require_confirmed_route=False,
            allow_control=args.allow_control,
            include_object_reads=args.include_object_reads,
        )
        save_profile_if_requested(setup, profile_path)
        setup_source = "saved"
    else:
        setup = integration.setup_report(
            include_ble=args.include_ble,
            include_ble_status=True,
            persistent=args.persistent,
            allow_control=args.allow_control,
            include_object_reads=args.include_object_reads,
        )
        if setup["ok"] is not True:
            raise SystemExit(json.dumps(setup, indent=2, sort_keys=True))
        config = config_from_setup(setup)
        save_light_connection_config(config, config_path)
        save_profile_if_requested(setup, profile_path)
        integration = integration.with_config(config)
        setup_source = "discovered"

    payload = dict(setup)
    payload["setup_source"] = setup_source
    if profile_path is not None:
        payload["profile_path"] = str(profile_path)

    if args.brightness is not None:
        integration = control_integration_from_setup(
            integration,
            setup,
            "set_brightness",
        )
        register = integration.register(
            device_id=0,
            group_id=0,
            require_setup_profile=True,
        )
        brightness = integration.set_brightness(
            args.brightness,
            require_ready=True,
            require_setup_profile=True,
        )
        payload["register"] = {
            "acknowledged": register["acknowledged"],
            "transport_status": register["transport_status"],
        }
        payload["brightness"] = {
            "acknowledged": brightness["acknowledged"],
            "transport_status": brightness["transport_status"],
        }

    print(json.dumps(payload, indent=2, sort_keys=True))


def config_from_setup(payload: Mapping[str, object]) -> LightConnectionConfig:
    config = payload.get("config")
    if not isinstance(config, Mapping):
        raise ValueError("setup report is missing a config object")
    return LightConnectionConfig.from_mapping(config)


def profile_from_setup(payload: Mapping[str, object]) -> LightSetupProfile:
    return LightSetupProfile.from_setup_report(payload)


def control_integration_from_setup(
    integration: LightIntegration,
    payload: Mapping[str, object],
    primitive: str,
) -> LightIntegration:
    configured = integration.with_setup_profile(profile_from_setup(payload))
    configured.require_setup_profile_primitive(primitive)
    return configured


def save_profile_if_requested(
    payload: Mapping[str, object],
    path: Path | None,
) -> None:
    if path is not None:
        save_light_setup_profile(profile_from_setup(payload), path)


if __name__ == "__main__":
    main()
