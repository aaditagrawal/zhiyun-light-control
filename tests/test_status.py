from __future__ import annotations

import unittest

from zhiyun_light_control.models import CommandResult
from zhiyun_light_control.protocol import (
    RUNTIME_TYPE,
    UPDATER_DEVICE,
    RuntimeCommand,
    UpdaterCommand,
    build_frame,
    first_frame,
)
from zhiyun_light_control.status import read_async_status, read_sync_status


class FakeStatusLight:
    def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        del payload, timeout
        payload_by_cmd = {
            RuntimeCommand.DEVICE_INFO: b"device-test\x00pl103\x00",
            RuntimeCommand.FIRMWARE: b"1.6.4\x00",
            RuntimeCommand.VOLTAGE: b"\x65",
            RuntimeCommand.DEVICE_ID: b"\x01\x00",
        }
        return _result(RUNTIME_TYPE, cmd, payload_by_cmd[cmd])

    def exchange_updater(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        del payload, timeout
        if cmd != UpdaterCommand.CHIP_SYNC:
            raise AssertionError(cmd)
        return _result(
            UPDATER_DEVICE,
            cmd,
            bytes.fromhex("0048444c0000010010030041054008a40065a36075"),
        )


class FakeAsyncStatusLight(FakeStatusLight):
    exchange_updater = None

    async def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        return super().exchange_runtime(cmd, payload, timeout=timeout)


def _result(first_word: int, cmd: int, payload: bytes) -> CommandResult:
    tx = build_frame(first_word, 1, cmd)
    rx = build_frame(first_word, 1, cmd, payload)
    ack = first_frame(rx, cmd=cmd)
    return CommandResult(cmd, tx, rx, (ack,), ack)


class StatusTests(unittest.TestCase):
    def test_read_sync_status_returns_parsed_data_and_command_evidence(self) -> None:
        report = read_sync_status(FakeStatusLight(), transport="test")
        payload = report.to_dict()

        self.assertTrue(payload["connection_confirmed"])
        self.assertEqual(payload["transport"], "test")
        self.assertEqual(payload["device_identifier"], "device-test")
        self.assertEqual(payload["generation"], "pl103")
        self.assertEqual(payload["firmware"], "1.6.4")
        self.assertEqual(payload["voltage_status"], 101)
        self.assertEqual(payload["device_id"], 1)
        self.assertEqual(payload["chip_sync"]["core_id"], "HDL")
        self.assertEqual(payload["chip_sync"]["updater_firmware"], "1.64")
        self.assertTrue(payload["commands"]["device_info"]["acknowledged"])
        self.assertTrue(payload["commands"]["updater_chip_sync"]["acknowledged"])


class AsyncStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_async_status_returns_runtime_command_evidence(self) -> None:
        report = await read_async_status(FakeAsyncStatusLight(), transport="ble-test")
        payload = report.to_dict()

        self.assertTrue(payload["connection_confirmed"])
        self.assertEqual(payload["transport"], "ble-test")
        self.assertEqual(payload["device_identifier"], "device-test")
        self.assertEqual(payload["generation"], "pl103")
        self.assertEqual(payload["firmware"], "1.6.4")
        self.assertEqual(payload["voltage_status"], 101)
        self.assertEqual(payload["device_id"], 1)
        self.assertIsNone(payload["chip_sync"])
        self.assertTrue(payload["commands"]["device_info"]["acknowledged"])
        self.assertNotIn("updater_chip_sync", payload["commands"])


if __name__ == "__main__":
    unittest.main()
