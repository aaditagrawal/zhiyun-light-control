from __future__ import annotations

import unittest
from unittest.mock import patch

from zhiyun_light_control.devices import (
    BLE_BACKENDS,
    discover_transport_devices,
    scan_ble_devices,
)
from zhiyun_light_control.transports.ble import BleDevice, BleScanResult


class DeviceDiscoveryTests(unittest.TestCase):
    def test_discovers_usb_ports_without_ble_scan(self) -> None:
        with patch(
            "zhiyun_light_control.devices.list_usb_ports",
            return_value=("/dev/cu.usbmodem21301", "/dev/cu.usbmodem31301"),
        ):
            payload = discover_transport_devices(
                configured_transport="usb",
                configured_usb_port="/dev/cu.usbmodem31301",
            )

        self.assertEqual(payload["configured_transport"], "usb")
        self.assertTrue(payload["usb"]["available"])
        self.assertEqual(payload["usb"]["selected_port"], "/dev/cu.usbmodem31301")
        self.assertEqual(
            payload["usb"]["ports"],
            [
                {"path": "/dev/cu.usbmodem21301", "selected": False},
                {"path": "/dev/cu.usbmodem31301", "selected": True},
            ],
        )
        self.assertFalse(payload["ble"]["included"])
        self.assertIsNone(payload["ble"]["scan"])

    def test_discovers_ble_with_selected_backend(self) -> None:
        scan = BleScanResult(
            ok=True,
            devices=(BleDevice(address="UUID-1", name="PL103_EDFE", rssi=-47),),
            returncode=0,
            worker_python="macos-app",
        )

        with (
            patch(
                "zhiyun_light_control.devices.list_usb_ports",
                return_value=("/dev/cu.usbmodem21301",),
            ),
            patch(
                "zhiyun_light_control.devices.scan_zhiyun_devices_macos_app",
                return_value=scan,
            ) as scan_macos,
        ):
            payload = discover_transport_devices(
                configured_transport="ble",
                include_ble=True,
                ble_backend="macos-app",
                ble_timeout=1.25,
                ble_name_contains="PL103",
            )

        self.assertEqual(payload["configured_transport"], "ble")
        self.assertTrue(payload["ble"]["included"])
        self.assertEqual(payload["ble"]["backend"], "macos-app")
        self.assertEqual(payload["ble"]["scan"]["devices"][0]["address"], "UUID-1")
        scan_macos.assert_called_once_with(timeout=1.25, name_contains="PL103")

    def test_worker_scan_passes_python_override(self) -> None:
        scan = BleScanResult(
            ok=False,
            devices=(),
            error="worker terminated by signal 6 (SIGABRT)",
            returncode=-6,
            worker_python="python-test",
            signal_name="SIGABRT",
        )

        with patch(
            "zhiyun_light_control.devices.scan_zhiyun_devices_safe",
            return_value=scan,
        ) as scan_safe:
            result = scan_ble_devices(
                backend="worker",
                timeout=2.0,
                name_contains="MOLUS",
                python="python-test",
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.signal_name, "SIGABRT")
        scan_safe.assert_called_once_with(
            timeout=2.0,
            name_contains="MOLUS",
            python="python-test",
        )

    def test_rejects_unknown_ble_backend(self) -> None:
        self.assertEqual(BLE_BACKENDS, ("worker", "macos-app", "direct"))
        with self.assertRaisesRegex(ValueError, "unsupported BLE backend"):
            scan_ble_devices(backend="other")


if __name__ == "__main__":
    unittest.main()
