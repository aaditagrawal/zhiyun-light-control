from __future__ import annotations

import unittest

from zhiyun_light_control.discovery import discover_usb_primitives
from zhiyun_light_control.models import CommandResult
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    build_frame,
    build_runtime_frame,
    first_response_frame,
    iter_frames,
)


class FakeDiscoveryLight:
    def __init__(self) -> None:
        self.seq = 0
        self.runtime_commands: list[int] = []
        self.frame_first_words: list[int] = []

    def exchange_runtime(self, cmd: int, payload: bytes = b"", *, timeout: float = 0.5):
        del timeout
        self.seq += 1
        self.runtime_commands.append(cmd)
        tx = build_runtime_frame(self.seq, cmd, payload)
        rx = b""
        if cmd in {
            RuntimeCommand.DEVICE_INFO,
            RuntimeCommand.FIRMWARE,
            RuntimeCommand.REGISTER_DEFAULT_GROUP,
        }:
            rx = build_runtime_frame(self.seq, cmd, b"\x00")
        return _result(cmd, tx, rx)

    def exchange_frame(
        self,
        first_word: int,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.5,
    ):
        del timeout
        self.seq += 1
        self.frame_first_words.append(first_word)
        tx = build_frame(first_word, self.seq, cmd, payload)
        rx = tx if first_word == 0x0301 else b""
        return _result(cmd, tx, rx)


def _result(cmd: int, tx: bytes, rx: bytes) -> CommandResult:
    frames = tuple(iter_frames(rx))
    return CommandResult(
        command=cmd & 0xFFFF,
        tx=tx,
        rx=rx,
        frames=frames,
        ack=first_response_frame(rx, tx=tx, cmd=cmd),
    )


class DiscoveryTests(unittest.TestCase):
    def test_usb_discovery_reports_confirmed_timeout_and_echo_attempts(self) -> None:
        light = FakeDiscoveryLight()

        report = discover_usb_primitives(
            light,
            object_ids=(1,),
            first_words=(0x0301,),
            allow_control=True,
        )
        payload = report.to_dict()
        attempts = {attempt["name"]: attempt for attempt in payload["attempts"]}

        self.assertTrue(attempts["global_device_info"]["confirmed"])
        self.assertEqual(attempts["read_brightness_obj1"]["status"], "sent_no_response")
        self.assertEqual(
            attempts["first_word_0x0301_read_brightness_obj0"]["status"],
            "echoed_write",
        )
        self.assertTrue(attempts["register_default_group_dev0_group0"]["confirmed"])
        self.assertIn("set_brightness_with_mode_obj1_mode1", attempts)
        self.assertEqual(payload["summary"]["confirmed"], 3)
        self.assertEqual(payload["control_object_ids"], [1])
        self.assertEqual(payload["control_first_words"], [0x0100])

    def test_usb_discovery_can_probe_control_object_ids_and_first_words(self) -> None:
        light = FakeDiscoveryLight()

        report = discover_usb_primitives(
            light,
            object_ids=(1,),
            first_words=(),
            control_object_ids=(0, 1),
            control_first_words=(0x0100, 0x0301),
            allow_control=True,
        )
        payload = report.to_dict()
        attempts = {attempt["name"]: attempt for attempt in payload["attempts"]}

        self.assertEqual(payload["control_object_ids"], [0, 1])
        self.assertEqual(payload["control_first_words"], [0x0100, 0x0301])
        self.assertIn("set_brightness_obj0", attempts)
        self.assertIn("set_brightness_obj0_fw0x0301", attempts)
        self.assertEqual(
            attempts["set_brightness_obj0_fw0x0301"]["category"],
            "control_first_word_probe",
        )
        self.assertEqual(
            attempts["set_brightness_obj0_fw0x0301"]["status"],
            "echoed_write",
        )
        self.assertIn(0x0301, light.frame_first_words)


if __name__ == "__main__":
    unittest.main()
