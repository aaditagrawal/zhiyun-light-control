from __future__ import annotations

import json
import types
import unittest
from unittest.mock import patch

from zhiyun_light_control.protocol import RuntimeCommand, build_runtime_frame, first_frame
from zhiyun_light_control.transports.ble import BleTransport, scan_zhiyun_devices, scan_zhiyun_devices_safe


class FakeDevice:
    def __init__(self, address: str, name: str | None):
        self.address = address
        self.name = name


class FakeAdvertisement:
    def __init__(
        self,
        *,
        service_uuids: list[str] | None = None,
        local_name: str | None = None,
        rssi: int | None = None,
    ):
        self.service_uuids = service_uuids or []
        self.local_name = local_name
        self.rssi = rssi


class SafeBleScanTests(unittest.TestCase):
    def test_safe_scan_parses_worker_devices(self) -> None:
        payload = {
            "devices": [
                {"address": "AA:BB", "name": "MOLUS G60", "rssi": -55},
            ]
        }
        proc = types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

        with patch("zhiyun_light_control.transports.ble.subprocess.run", return_value=proc):
            result = scan_zhiyun_devices_safe(timeout=1.0, python="python-test")

        self.assertTrue(result.ok)
        self.assertEqual(result.devices[0].address, "AA:BB")
        self.assertEqual(result.devices[0].name, "MOLUS G60")
        self.assertEqual(result.devices[0].rssi, -55)

    def test_safe_scan_reports_worker_abort(self) -> None:
        proc = types.SimpleNamespace(
            returncode=-6,
            stdout="",
            stderr="Fatal Python error: Aborted",
        )

        with patch("zhiyun_light_control.transports.ble.subprocess.run", return_value=proc):
            result = scan_zhiyun_devices_safe(timeout=1.0)

        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, -6)
        self.assertIn("Aborted", result.error)


class AsyncBleTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_filters_likely_zhiyun_devices(self) -> None:
        class FakeScanner:
            @staticmethod
            async def discover(timeout: float, return_adv: bool):
                self.assertEqual(timeout, 1.0)
                self.assertTrue(return_adv)
                return {
                    "one": (
                        FakeDevice("AA", None),
                        FakeAdvertisement(local_name="MOLUS G60", rssi=-40),
                    ),
                    "two": (
                        FakeDevice("BB", "Keyboard"),
                        FakeAdvertisement(rssi=-50),
                    ),
                }

        with patch(
            "zhiyun_light_control.transports.ble._load_bleak",
            return_value=(object, FakeScanner),
        ):
            devices = await scan_zhiyun_devices(timeout=1.0)

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].address, "AA")
        self.assertEqual(devices[0].name, "MOLUS G60")

    async def test_transport_exchange_uses_write_and_notify_characteristics(self) -> None:
        class FakeClient:
            callback = None
            writes: list[tuple[str, bytes, bool]] = []

            def __init__(self, address: str):
                self.address = address

            async def connect(self) -> None:
                return

            async def disconnect(self) -> None:
                return

            async def start_notify(self, uuid: str, callback) -> None:
                self.__class__.callback = callback
                self.notify_uuid = uuid

            async def stop_notify(self, uuid: str) -> None:
                self.stopped_uuid = uuid

            async def write_gatt_char(self, uuid: str, tx: bytes, response: bool) -> None:
                self.__class__.writes.append((uuid, tx, response))
                frame = first_frame(tx)
                assert frame is not None
                ack = build_runtime_frame(frame.seq, frame.cmd, b"\x00")
                self.__class__.callback(uuid, bytearray(ack))

        with patch(
            "zhiyun_light_control.transports.ble._load_bleak",
            return_value=(FakeClient, object),
        ):
            async with BleTransport(address="AA", timeout=1.0) as transport:
                tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
                rx = await transport.exchange(tx, timeout=1.0)

        self.assertTrue(rx)
        self.assertEqual(FakeClient.writes[0][0], "d44bc439-abfd-45a2-b575-925416129600")
        self.assertFalse(FakeClient.writes[0][2])
        self.assertEqual(first_frame(rx).cmd, RuntimeCommand.DEVICE_INFO)


if __name__ == "__main__":
    unittest.main()
