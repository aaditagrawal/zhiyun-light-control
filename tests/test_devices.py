from __future__ import annotations

import unittest
from unittest.mock import patch

from zhiyun_light_control.devices import (
    BLE_BACKENDS,
    discover_transport_devices,
    scan_ble_devices,
)
from zhiyun_light_control.devices import (
    test_ble_endpoint_candidates as run_ble_endpoint_candidate_test,
)
from zhiyun_light_control.protocol import RuntimeCommand, build_runtime_frame
from zhiyun_light_control.transports.ble import (
    DIRECT_ZY_NOTIFY_UUID,
    DIRECT_ZY_SERVICE_UUID,
    DIRECT_ZY_WRITE_UUID,
    BleCharacteristic,
    BleDevice,
    BleExchangeResult,
    BleInspectResult,
    BleScanResult,
    BleService,
)


class DeviceDiscoveryTests(unittest.TestCase):
    def test_discovers_usb_ports_without_ble_scan(self) -> None:
        with patch(
            "zhiyun_light_control.devices.list_usb_ports",
            return_value=("/dev/cu.usbmodem21301", "/dev/cu.usbmodem31301"),
        ), patch(
            "zhiyun_light_control.devices.list_usb_port_metadata",
            return_value={
                "/dev/cu.usbmodem31301": {
                    "vendor_id": 0xFFF8,
                    "vendor_id_hex": "0xfff8",
                    "product_name": "Zhiyun Virtual ComPort",
                }
            },
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
                {
                    "path": "/dev/cu.usbmodem31301",
                    "selected": True,
                    "metadata": {
                        "vendor_id": 0xFFF8,
                        "vendor_id_hex": "0xfff8",
                        "product_name": "Zhiyun Virtual ComPort",
                    },
                },
            ],
        )
        self.assertFalse(payload["ble"]["included"])
        self.assertEqual(
            payload["ble"]["macos_helper"]["bundle_id"],
            "local.zhiyun-light-control.ble-scan",
        )
        self.assertIsNone(payload["ble"]["scan"])

    def test_discovers_ble_with_selected_backend(self) -> None:
        scan = BleScanResult(
            ok=True,
            devices=(
                BleDevice(
                    address="UUID-1",
                    name="PL103_EDFE",
                    rssi=-47,
                    services=("0000fee9-0000-1000-8000-00805f9b34fb",),
                ),
            ),
            returncode=0,
            worker_python="macos-app",
        )

        with (
            patch(
                "zhiyun_light_control.devices.list_usb_ports",
                return_value=("/dev/cu.usbmodem21301",),
            ),
            patch(
                "zhiyun_light_control.devices.list_usb_port_metadata",
                return_value={},
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
        self.assertEqual(
            payload["ble"]["scan"]["devices"][0]["services"],
            ["0000fee9-0000-1000-8000-00805f9b34fb"],
        )
        self.assertEqual(
            payload["ble"]["scan"]["devices"][0]["suggested_profile"],
            "legacy",
        )
        scan_macos.assert_called_once_with(timeout=1.25, name_contains="PL103")

    def test_discovers_optional_macos_ble_status(self) -> None:
        with (
            patch(
                "zhiyun_light_control.devices.list_usb_ports",
                return_value=("/dev/cu.usbmodem21301",),
            ),
            patch(
                "zhiyun_light_control.devices.list_usb_port_metadata",
                return_value={},
            ),
            patch(
                "zhiyun_light_control.devices.macos_ble_app_status",
                return_value={
                    "ok": False,
                    "state": "unauthorized",
                    "authorization": "denied",
                },
            ) as status,
        ):
            payload = discover_transport_devices(
                configured_transport="ble",
                include_ble_status=True,
                ble_timeout=1.25,
            )

        self.assertEqual(payload["ble"]["macos_status"]["state"], "unauthorized")
        self.assertEqual(payload["ble"]["macos_status"]["authorization"], "denied")
        status.assert_called_once_with(timeout=1.25)

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

    def test_test_ble_endpoint_candidates_confirms_read_only_ack(self) -> None:
        inspect = BleInspectResult(
            ok=True,
            address="UUID-1",
            services=(
                BleService(
                    uuid=DIRECT_ZY_SERVICE_UUID,
                    characteristics=(
                        BleCharacteristic(
                            uuid=DIRECT_ZY_WRITE_UUID,
                            properties=("write-without-response",),
                        ),
                        BleCharacteristic(
                            uuid=DIRECT_ZY_NOTIFY_UUID,
                            properties=("notify",),
                        ),
                    ),
                ),
            ),
            worker_python="macos-app",
        )

        def fake_exchange(tx: bytes, **kwargs: object) -> BleExchangeResult:
            self.assertEqual(kwargs["address"], "UUID-1")
            self.assertIsNone(kwargs["name_contains"])
            self.assertEqual(kwargs["profile"], "direct")
            self.assertEqual(kwargs["service_uuid"], DIRECT_ZY_SERVICE_UUID)
            self.assertEqual(kwargs["write_uuid"], DIRECT_ZY_WRITE_UUID)
            self.assertEqual(kwargs["notify_uuid"], DIRECT_ZY_NOTIFY_UUID)
            rx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO, b"g60\x00pl103\x00")
            return BleExchangeResult(
                ok=True,
                tx=tx,
                rx=rx,
                address="UUID-1",
                worker_python="macos-app",
            )

        with (
            patch(
                "zhiyun_light_control.devices.inspect_ble_device",
                return_value=inspect,
            ) as inspect_ble,
            patch(
                "zhiyun_light_control.devices.exchange_zhiyun_ble_macos_app",
                side_effect=fake_exchange,
            ) as exchange,
        ):
            report = run_ble_endpoint_candidate_test(
                backend="macos-app",
                timeout=1.25,
                name_contains="PL103",
                max_candidates=1,
            )

        payload = report.to_dict()
        self.assertTrue(report.ok)
        self.assertTrue(payload["tests"][0]["acknowledged"])
        self.assertEqual(payload["tests"][0]["transport_status"], "acknowledged")
        self.assertEqual(payload["confirmed_candidates"][0]["profile"], "direct")
        self.assertEqual(
            payload["tests"][0]["command_result"]["command"],
            RuntimeCommand.DEVICE_INFO,
        )
        inspect_ble.assert_called_once_with(
            backend="macos-app",
            timeout=1.25,
            address=None,
            name_contains="PL103",
            python=None,
        )
        exchange.assert_called_once()

    def test_test_ble_endpoint_candidates_returns_inspect_failure(self) -> None:
        inspect = BleInspectResult(
            ok=False,
            address=None,
            error="Bluetooth state unauthorized: 3",
            worker_python="macos-app",
        )

        with patch(
            "zhiyun_light_control.devices.inspect_ble_device",
            return_value=inspect,
        ) as inspect_ble:
            report = run_ble_endpoint_candidate_test(
                backend="macos-app",
                timeout=2.0,
                name_contains="PL103",
            )

        payload = report.to_dict()
        self.assertFalse(report.ok)
        self.assertEqual(payload["inspect"]["error"], "Bluetooth state unauthorized: 3")
        self.assertEqual(payload["tests"], [])
        inspect_ble.assert_called_once_with(
            backend="macos-app",
            timeout=2.0,
            address=None,
            name_contains="PL103",
            python=None,
        )


if __name__ == "__main__":
    unittest.main()
