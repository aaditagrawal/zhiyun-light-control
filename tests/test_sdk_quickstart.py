from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

from zhiyun_light_control import LightIntegration, SetupProfileNotReady


def load_quickstart() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "examples" / "sdk_quickstart.py"
    spec = importlib.util.spec_from_file_location("sdk_quickstart", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load sdk_quickstart example")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SdkQuickstartTests(unittest.TestCase):
    def test_builds_config_from_setup_report(self) -> None:
        quickstart = load_quickstart()

        config = quickstart.config_from_setup(setup_report())

        self.assertEqual(config.transport, "usb")
        self.assertEqual(config.port, "/dev/cu.usbmodem21301")
        self.assertTrue(config.persistent)

    def test_builds_config_from_connection_report(self) -> None:
        quickstart = load_quickstart()

        config = quickstart.config_from_connection_report(connection_report())

        self.assertEqual(config.transport, "usb")
        self.assertEqual(config.port, "/dev/cu.usbmodem21301")
        self.assertTrue(config.persistent)

    def test_builds_profile_from_setup_report(self) -> None:
        quickstart = load_quickstart()

        profile = quickstart.profile_from_setup(setup_report())

        self.assertEqual(profile.config.transport, "usb")
        self.assertEqual(profile.config.port, "/dev/cu.usbmodem21301")
        self.assertTrue(profile.ready("read_status"))
        self.assertFalse(profile.ready("control_writes"))

    def test_saves_profile_when_requested(self) -> None:
        quickstart = load_quickstart()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            quickstart.save_profile_if_requested(setup_report(), path)
            profile = quickstart.load_light_setup_profile(path)

        self.assertEqual(profile.config.port, "/dev/cu.usbmodem21301")
        self.assertTrue(profile.ready("read_status"))

    def test_skips_profile_save_without_path(self) -> None:
        quickstart = load_quickstart()

        quickstart.save_profile_if_requested(setup_report(), None)

    def test_rejects_setup_report_without_config(self) -> None:
        quickstart = load_quickstart()

        with self.assertRaisesRegex(ValueError, "config"):
            quickstart.config_from_setup({"ok": False})

    def test_rejects_connection_report_without_selected_config(self) -> None:
        quickstart = load_quickstart()

        with self.assertRaisesRegex(ValueError, "selected_config"):
            quickstart.config_from_connection_report({"ok": False})

    def test_connection_summary_keeps_selected_route_and_summary(self) -> None:
        quickstart = load_quickstart()

        summary = quickstart.connection_summary(connection_report())

        self.assertEqual(summary["summary"]["selected_transport"], "usb")
        self.assertEqual(summary["selected"]["confidence"], "status-confirmed")

    def test_setup_with_connection_evidence_preserves_confirmed_route(self) -> None:
        quickstart = load_quickstart()

        setup = quickstart.setup_with_connection_evidence(
            setup_report(),
            connection_report(),
        )
        profile = quickstart.profile_from_setup(setup)

        self.assertTrue(setup["route_confirmed"])
        self.assertTrue(setup["summary"]["route_confirmed"])
        self.assertEqual(setup["selected_route"]["confidence"], "status-confirmed")
        self.assertEqual(setup["routes"][0]["transport"], "usb")
        self.assertEqual(setup["connection"]["summary"]["selected_transport"], "usb")
        self.assertTrue(profile.route_confirmed)

    def test_control_integration_from_setup_requires_primitive(self) -> None:
        quickstart = load_quickstart()
        integration = LightIntegration()

        with self.assertRaises(SetupProfileNotReady):
            quickstart.control_integration_from_setup(
                integration,
                setup_report(),
                "set_brightness",
            )

        configured = quickstart.control_integration_from_setup(
            integration,
            setup_report(control_writes=True),
            "set_brightness",
        )

        self.assertEqual(configured.config.port, "/dev/cu.usbmodem21301")
        self.assertTrue(configured.require_setup_profile_controls)


def connection_report() -> dict[str, object]:
    return {
        "ok": True,
        "selected_config": {
            "transport": "usb",
            "port": "/dev/cu.usbmodem21301",
            "persistent": True,
        },
        "selected": {
            "transport": "usb",
            "confidence": "status-confirmed",
            "config": {
                "transport": "usb",
                "port": "/dev/cu.usbmodem21301",
                "persistent": True,
            },
        },
        "summary": {
            "candidate_count": 1,
            "confirmed_count": 1,
            "selected_transport": "usb",
            "usb_available": True,
            "ble_blocked": True,
            "ble_blocker": "Bluetooth state unauthorized: 3",
        },
        "candidates": [
            {
                "transport": "usb",
                "confidence": "status-confirmed",
                "config": {
                    "transport": "usb",
                    "port": "/dev/cu.usbmodem21301",
                    "persistent": True,
                },
            }
        ],
        "best_confirmed": {
            "transport": "usb",
            "confidence": "status-confirmed",
            "config": {
                "transport": "usb",
                "port": "/dev/cu.usbmodem21301",
                "persistent": True,
            },
        },
    }


def setup_report(*, control_writes: bool = False) -> dict[str, object]:
    return {
        "config": {
            "transport": "usb",
            "port": "/dev/cu.usbmodem21301",
            "persistent": True,
        },
        "ok": True,
        "route_confirmed": True,
        "status_ok": True,
        "ready_for": {"read_status": True},
        "validation_ready_for": {"control_writes": control_writes},
        "validation_unconfirmed": [] if control_writes else ["set_brightness"],
    }


if __name__ == "__main__":
    unittest.main()
