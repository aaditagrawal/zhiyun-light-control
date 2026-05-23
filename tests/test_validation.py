from __future__ import annotations

import unittest
from dataclasses import dataclass

from zhiyun_light_control.protocol import build_runtime_frame, first_frame
from zhiyun_light_control.validation import validate_async_light, validate_sync_light


@dataclass(frozen=True)
class FakeProbe:
    firmware: str | None = "1.6.4"
    device_identifier: str | None = "id"
    generation: str | None = "pl103"
    voltage_status: int | None = 101
    device_id: int | None = 1
    port: str | None = "/dev/cu.test"

    def to_dict(self):
        return {
            "firmware": self.firmware,
            "device_identifier": self.device_identifier,
            "generation": self.generation,
            "voltage_status": self.voltage_status,
            "device_id": self.device_id,
            "port": self.port,
        }


class FakeValidationLight:
    def __init__(self, *, ack: bool = True) -> None:
        self.ack = ack
        self.commands: list[int] = []
        self.payloads: list[tuple[int, bytes]] = []

    def probe(self) -> FakeProbe:
        return FakeProbe()

    def exchange_runtime(self, cmd: int, payload: bytes = b"", *, timeout: float = 0.8):
        del timeout
        self.commands.append(cmd)
        self.payloads.append((cmd, payload))
        tx = build_runtime_frame(len(self.commands), cmd, payload)
        rx = build_runtime_frame(len(self.commands), cmd, b"\x00") if self.ack else b""
        ack = first_frame(rx, cmd=cmd)
        from zhiyun_light_control.models import CommandResult

        frames = (ack,) if ack is not None else ()
        return CommandResult(cmd, tx, rx, frames, ack)

    def exchange_updater(self, cmd: int, payload: bytes = b"", *, timeout: float = 0.8):
        return self.exchange_runtime(cmd, payload, timeout=timeout)


class AsyncFakeValidationLight(FakeValidationLight):
    async def probe(self) -> FakeProbe:
        return FakeProbe()

    async def exchange_runtime(
        self, cmd: int, payload: bytes = b"", *, timeout: float = 1.5
    ):
        return super().exchange_runtime(cmd, payload, timeout=timeout)


class ValidationTests(unittest.TestCase):
    def test_validation_report_separates_confirmed_from_sent(self) -> None:
        report = validate_sync_light(
            FakeValidationLight(ack=False),
            allow_control=True,
            include_object_reads=True,
        )

        payload = report.to_dict()
        self.assertTrue(payload["connection_confirmed"])
        self.assertFalse(payload["all_attempted_confirmed"])
        self.assertIn("set_brightness", payload["unconfirmed"])
        self.assertEqual(
            next(
                check
                for check in payload["checks"]
                if check["name"] == "set_brightness"
            )["status"],
            "sent_no_response",
        )

    def test_validation_can_confirm_all_attempted_primitives(self) -> None:
        report = validate_sync_light(
            FakeValidationLight(ack=True),
            allow_control=True,
            include_object_reads=True,
            include_color=True,
        )

        payload = report.to_dict()
        self.assertTrue(payload["connection_confirmed"])
        self.assertTrue(payload["all_attempted_confirmed"])
        self.assertEqual(payload["unconfirmed"], [])
        self.assertIn("set_rgb", [check["name"] for check in payload["checks"]])

    def test_validation_control_mode_is_sent_to_write_checks(self) -> None:
        light = FakeValidationLight(ack=True)

        validate_sync_light(light, allow_control=True, control_mode=0x01)

        payloads = dict(light.payloads)
        self.assertEqual(payloads[0x1008][2], 0x01)
        self.assertEqual(payloads[0x1001][2], 0x01)
        self.assertEqual(payloads[0x1002][2], 0x01)


class AsyncValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_validation_matches_report_shape(self) -> None:
        report = await validate_async_light(
            AsyncFakeValidationLight(ack=True),
            allow_control=True,
            include_object_reads=True,
        )

        payload = report.to_dict()
        self.assertEqual(payload["transport"], "ble")
        self.assertTrue(payload["connection_confirmed"])
        self.assertTrue(payload["all_attempted_confirmed"])


if __name__ == "__main__":
    unittest.main()
