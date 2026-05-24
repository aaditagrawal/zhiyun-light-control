from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zhiyun_light_control import (
    LightConnectionConfig,
    LightSetupProfile,
    SetupProfileNotReady,
    light_setup_profile_from_json,
    light_setup_profile_to_json,
    load_light_setup_profile,
    save_light_setup_profile,
    setup_profile_capabilities,
    setup_profile_primitive_readiness,
    setup_profile_primitive_readiness_map,
    setup_profile_primitive_ready,
    setup_profile_primitive_ready_for,
    setup_profile_primitive_requirements,
    setup_profile_primitive_requirements_map,
    setup_profile_ready,
    setup_profile_summary,
    setup_profile_unready_capabilities,
    setup_profile_unready_primitive_capabilities,
)


class SetupProfileTests(unittest.TestCase):
    def test_profile_wraps_setup_report_capabilities(self) -> None:
        profile = LightSetupProfile.from_setup_report(
            setup_report(),
            created_at=42.0,
        )

        self.assertTrue(profile.ok)
        self.assertTrue(profile.route_confirmed)
        self.assertTrue(profile.connection_confirmed)
        self.assertEqual(profile.config.transport, "usb")
        self.assertEqual(profile.config.port, "/dev/cu.usbmodem21301")
        self.assertTrue(profile.ready("read_status"))
        self.assertFalse(profile.ready("control_writes"))
        self.assertEqual(profile.validation_unconfirmed, ["set_brightness"])
        self.assertEqual(
            profile.capabilities,
            {
                "read_status": True,
                "control_writes": False,
            },
        )
        self.assertEqual(
            setup_profile_capabilities(profile.to_dict()),
            {
                "read_status": True,
                "control_writes": False,
            },
        )
        self.assertTrue(setup_profile_ready(profile.to_dict(), "read_status"))
        self.assertFalse(
            setup_profile_ready(profile.to_dict(), "control_writes")
        )
        self.assertEqual(
            profile.unready_capabilities("read_status", "control_writes"),
            ["control_writes"],
        )
        self.assertEqual(
            setup_profile_unready_capabilities(
                setup_report(),
                ["read_status", "control_writes"],
            ),
            ["control_writes"],
        )
        self.assertEqual(
            profile.primitive_requirements("set_brightness"),
            ("control_writes",),
        )
        self.assertEqual(
            profile.primitive_requirements("read_brightness"),
            ("object_reads",),
        )
        self.assertTrue(profile.primitive_ready("status"))
        self.assertFalse(profile.primitive_ready("set_brightness"))
        self.assertTrue(setup_profile_primitive_ready(setup_report(), "status"))
        self.assertFalse(
            setup_profile_primitive_ready(
                profile.to_dict(),
                "set-brightness",
            )
        )
        self.assertEqual(
            profile.unready_primitive_capabilities("set_brightness"),
            ["control_writes"],
        )
        self.assertEqual(
            setup_profile_unready_primitive_capabilities(
                profile,
                "set_brightness",
            ),
            ["control_writes"],
        )
        self.assertEqual(
            setup_profile_primitive_readiness(profile, "set-brightness"),
            {
                "primitive": "set_brightness",
                "ready": False,
                "requirements": ["control_writes"],
                "capabilities": {"control_writes": False},
                "unready_capabilities": ["control_writes"],
            },
        )
        self.assertEqual(
            profile.primitive_readiness_for("status"),
            {
                "primitive": "status",
                "ready": True,
                "requirements": ["read_status"],
                "capabilities": {"read_status": True},
                "unready_capabilities": [],
            },
        )
        self.assertTrue(profile.primitive_ready_for["status"])
        self.assertFalse(profile.primitive_ready_for["brightness"])
        self.assertTrue(
            setup_profile_primitive_ready_for(profile.to_dict())["status"]
        )
        self.assertFalse(
            setup_profile_primitive_readiness_map(profile)["brightness"]["ready"]
        )
        self.assertEqual(
            setup_profile_primitive_requirements("set-brightness"),
            ("control_writes",),
        )
        self.assertEqual(
            setup_profile_primitive_requirements("brightness"),
            ("control_writes",),
        )
        self.assertEqual(
            setup_profile_primitive_requirements_map()["brightness"],
            ("control_writes",),
        )
        self.assertEqual(
            setup_profile_summary(None),
            {"present": False},
        )
        self.assertEqual(
            setup_profile_summary(profile)["primitive_ready_for"]["brightness"],
            False,
        )
        self.assertEqual(
            setup_profile_summary(profile)["config"]["port"],
            "/dev/cu.usbmodem21301",
        )

        payload = profile.to_dict()
        self.assertEqual(payload["kind"], "setup-profile")
        self.assertEqual(payload["created_at"], 42.0)
        self.assertEqual(payload["config"], profile.config.to_dict())
        self.assertEqual(payload["validation_unconfirmed"], ["set_brightness"])
        self.assertFalse(payload["primitive_ready_for"]["set_brightness"])
        self.assertEqual(
            payload["primitive_readiness"]["read_brightness"][
                "unready_capabilities"
            ],
            ["object_reads"],
        )

    def test_profile_json_round_trips(self) -> None:
        profile = LightSetupProfile.from_setup_report(
            setup_report(),
            created_at=123.0,
        )

        restored = light_setup_profile_from_json(
            light_setup_profile_to_json(profile)
        )

        self.assertEqual(restored.created_at, 123.0)
        self.assertEqual(restored.config.port, "/dev/cu.usbmodem21301")
        self.assertEqual(restored.ready_for, {"read_status": True})
        self.assertEqual(restored.validation_ready_for["control_writes"], False)

    def test_profile_file_helpers_round_trip(self) -> None:
        profile = LightSetupProfile.from_setup_report(
            setup_report(
                LightConnectionConfig.ble(
                    address="UUID-1",
                    backend="macos-app",
                    profile="legacy",
                )
            ),
            created_at=9.0,
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "light-profile.json"
            save_light_setup_profile(profile, path)
            restored = load_light_setup_profile(path)

        self.assertEqual(restored.config.transport, "ble")
        self.assertEqual(restored.config.address, "UUID-1")
        self.assertEqual(restored.config.ble_backend, "macos-app")
        self.assertEqual(restored.config.ble_profile, "legacy")

    def test_require_ready_reports_profile_evidence(self) -> None:
        profile = LightSetupProfile.from_setup_report(setup_report())

        with self.assertRaisesRegex(SetupProfileNotReady, "control_writes") as error:
            profile.require_ready("read_status", "control_writes")

        self.assertEqual(error.exception.capabilities, ("control_writes",))
        self.assertEqual(error.exception.ready_for, {"read_status": True})
        self.assertEqual(
            error.exception.validation_ready_for,
            {
                "read_status": True,
                "control_writes": False,
            },
        )
        self.assertEqual(error.exception.validation_unconfirmed, ["set_brightness"])

        with self.assertRaisesRegex(SetupProfileNotReady, "control_writes"):
            profile.require_primitive("set_brightness")

        with self.assertRaisesRegex(ValueError, "unknown setup profile primitive"):
            profile.primitive_ready("fan_speed")


def setup_report(
    config: LightConnectionConfig | None = None,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig.usb(
        port="/dev/cu.usbmodem21301",
        persistent=True,
    )
    return {
        "api": "zhiyun-light-control",
        "ok": True,
        "config": resolved.to_dict(),
        "route_confirmed": True,
        "status_ok": True,
        "ready_for": {"read_status": True},
        "validation_ready_for": {
            "read_status": True,
            "control_writes": False,
        },
        "validation_unconfirmed": ["set_brightness"],
        "summary": {
            "ok": True,
            "connection_confirmed": True,
            "route_confirmed": True,
            "ready_for": {"read_status": True},
            "validation_ready_for": {
                "read_status": True,
                "control_writes": False,
            },
            "validation_unconfirmed": ["set_brightness"],
            "pending_action_ids": ["confirm-control"],
            "warnings": [],
            "errors": [],
        },
    }


if __name__ == "__main__":
    unittest.main()
