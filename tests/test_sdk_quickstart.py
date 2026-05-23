from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

from zhiyun_light_control import LightConnectionCandidate, LightConnectionConfig


def load_quickstart() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "examples" / "sdk_quickstart.py"
    spec = importlib.util.spec_from_file_location("sdk_quickstart", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load sdk_quickstart example")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeIntegration:
    def __init__(self, routes: tuple[LightConnectionCandidate, ...]) -> None:
        self.routes = routes
        self.calls: list[dict[str, object]] = []

    def probe_connection_candidates(
        self,
        *,
        include_ble: bool,
        include_ble_status: bool,
        persistent: bool,
    ) -> tuple[LightConnectionCandidate, ...]:
        self.calls.append(
            {
                "include_ble": include_ble,
                "include_ble_status": include_ble_status,
                "persistent": persistent,
            }
        )
        return self.routes


class SdkQuickstartTests(unittest.TestCase):
    def test_discovers_only_status_confirmed_config(self) -> None:
        quickstart = load_quickstart()
        unconfirmed = LightConnectionCandidate(
            config=LightConnectionConfig.ble(address="UUID-1"),
            source="devices.ble.status",
            confidence="status-unconfirmed",
            confidence_score=40,
            reason="BLE status probe failed",
            evidence={"status_probe": {"connection_confirmed": False}},
        )
        confirmed = LightConnectionCandidate(
            config=LightConnectionConfig.usb(port="/dev/cu.usbmodem21301"),
            source="devices.usb.status",
            confidence="status-confirmed",
            confidence_score=115,
            reason="USB status probe confirmed",
            evidence={
                "status_probe": {
                    "connection_confirmed": True,
                    "firmware": "1.6.4",
                }
            },
        )
        integration = FakeIntegration((unconfirmed, confirmed))

        config, routes = quickstart.discover_confirmed_config(
            integration,
            include_ble=True,
            include_ble_status=True,
            persistent=True,
        )

        self.assertEqual(config.transport, "usb")
        self.assertEqual(config.port, "/dev/cu.usbmodem21301")
        self.assertFalse(quickstart.status_probe_confirmed(unconfirmed))
        self.assertTrue(quickstart.status_probe_confirmed(confirmed))
        self.assertEqual(routes[0]["confidence"], "status-unconfirmed")
        self.assertEqual(routes[1]["confidence"], "status-confirmed")
        self.assertEqual(
            integration.calls,
            [
                {
                    "include_ble": True,
                    "include_ble_status": True,
                    "persistent": True,
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
