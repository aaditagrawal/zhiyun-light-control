from __future__ import annotations

import json
import unittest
from collections import defaultdict, deque
from collections.abc import Mapping
from pathlib import Path

from zhiyun_light_control import ZhiyunLight
from zhiyun_light_control.protocol import (
    RUNTIME_TYPE,
    UPDATER_DEVICE,
    RuntimeCommand,
    UpdaterCommand,
    first_frame,
)
from zhiyun_light_control.status import read_sync_status

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "hardware"
FIXTURE_KIND = "zhiyun-light-control.hardware-observation.v1"


class HardwareFixtureReplayTransport:
    def __init__(self, exchanges: list[Mapping[str, object]]) -> None:
        self.sent: list[bytes] = []
        self._responses: dict[tuple[int, int], deque[bytes]] = defaultdict(deque)
        for exchange in exchanges:
            first_word, command = _exchange_key(exchange)
            self._responses[(first_word, command)].append(
                bytes.fromhex(str(exchange["rx_hex"]))
            )

    def exchange(self, tx: bytes, timeout: float = 0.8) -> bytes:
        del timeout
        self.sent.append(tx)
        frame = first_frame(tx)
        if frame is None:
            raise AssertionError(f"fixture replay received an invalid tx: {tx.hex()}")

        key = (frame.first_word, frame.cmd)
        responses = self._responses.get(key)
        if not responses:
            raise AssertionError(f"no hardware fixture response for {_key_label(key)}")
        return responses.popleft()

    def assert_complete(self) -> None:
        unused = {
            _key_label(key): len(responses)
            for key, responses in self._responses.items()
            if responses
        }
        if unused:
            raise AssertionError(f"unused hardware fixture responses: {unused}")

    def close(self) -> None:
        return


class HardwareFixtureTests(unittest.TestCase):
    def test_captured_status_observations_replay_without_hardware(self) -> None:
        fixture_paths = sorted(FIXTURE_DIR.glob("*.json"))
        self.assertTrue(fixture_paths, "expected at least one hardware fixture")

        for path in fixture_paths:
            with self.subTest(fixture=path.name):
                fixture = _load_fixture(path)
                transport = HardwareFixtureReplayTransport(fixture["exchanges"])
                report = read_sync_status(
                    ZhiyunLight(transport),
                    transport=str(fixture["transport"]),
                )

                transport.assert_complete()
                self.assertGreaterEqual(len(transport.sent), 1)
                self.assertEqual(
                    [first_frame(tx).cmd for tx in transport.sent],
                    [_command_value(exchange) for exchange in fixture["exchanges"]],
                )
                self.assert_status_matches_fixture(
                    report.to_dict(),
                    fixture["expected_status"],
                )

    def assert_status_matches_fixture(
        self,
        actual: Mapping[str, object],
        expected: Mapping[str, object],
    ) -> None:
        for field in (
            "transport",
            "connection_confirmed",
            "device_identifier",
            "generation",
            "firmware",
            "voltage_status",
            "device_id",
            "chip_sync",
            "read_sn",
        ):
            self.assertEqual(actual[field], expected[field], field)

        actual_statuses = {
            name: command["transport_status"]
            for name, command in actual["commands"].items()
        }
        self.assertEqual(actual_statuses, expected["command_transport_statuses"])


def _load_fixture(path: Path) -> dict[str, object]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if fixture.get("kind") != FIXTURE_KIND:
        raise AssertionError(f"{path.name} is not a {FIXTURE_KIND} fixture")
    if not isinstance(fixture.get("exchanges"), list) or not fixture["exchanges"]:
        raise AssertionError(f"{path.name} must include at least one exchange")
    if not isinstance(fixture.get("expected_status"), dict):
        raise AssertionError(f"{path.name} must include expected_status")
    return fixture


def _exchange_key(exchange: Mapping[str, object]) -> tuple[int, int]:
    space = str(exchange["space"])
    if space == "runtime":
        return RUNTIME_TYPE, _command_value(exchange)
    if space == "updater":
        return UPDATER_DEVICE, _command_value(exchange)
    raise AssertionError(f"unsupported hardware fixture command space: {space}")


def _command_value(exchange: Mapping[str, object]) -> int:
    command = exchange["command"]
    if isinstance(command, int):
        return command
    command_text = str(command)
    if command_text.startswith("0x"):
        return int(command_text, 16)

    space = str(exchange["space"])
    if space == "runtime":
        return int(RuntimeCommand[command_text])
    if space == "updater":
        return int(UpdaterCommand[command_text])
    raise AssertionError(f"unsupported hardware fixture command space: {space}")


def _key_label(key: tuple[int, int]) -> str:
    first_word, command = key
    return f"first_word=0x{first_word:04x} command=0x{command:04x}"


if __name__ == "__main__":
    unittest.main()
