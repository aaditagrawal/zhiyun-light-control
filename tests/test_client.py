from __future__ import annotations

import unittest

from zhiyun_light_control import Scene, ZhiyunLight
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    build_runtime_frame,
    first_frame,
)


class EchoAckTransport:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def exchange(self, tx: bytes, timeout: float = 0.8) -> bytes:
        del timeout
        self.sent.append(tx)
        frame = first_frame(tx)
        assert frame is not None
        return build_runtime_frame(frame.seq, frame.cmd, b"\x00")

    def close(self) -> None:
        return


class ExactEchoTransport:
    def exchange(self, tx: bytes, timeout: float = 0.8) -> bytes:
        del timeout
        return tx

    def close(self) -> None:
        return


class EchoThenAckTransport:
    def exchange(self, tx: bytes, timeout: float = 0.8) -> bytes:
        del timeout
        frame = first_frame(tx)
        assert frame is not None
        return tx + build_runtime_frame(frame.seq, frame.cmd, b"\x00")

    def close(self) -> None:
        return


class ClientTests(unittest.TestCase):
    def test_exchange_runtime_exposes_raw_bytes_and_ack(self) -> None:
        transport = EchoAckTransport()
        light = ZhiyunLight(transport)

        result = light.exchange_runtime(RuntimeCommand.REGISTER_DEFAULT_GROUP, b"\x00\x00\x00\x00")

        self.assertTrue(result.acknowledged)
        self.assertFalse(result.timed_out)
        self.assertTrue(result.sent)
        self.assertEqual(result.transport_status, "acknowledged")
        self.assertEqual(result.command, RuntimeCommand.REGISTER_DEFAULT_GROUP)
        self.assertEqual(result.tx, transport.sent[0])
        self.assertEqual(result.ack.cmd, RuntimeCommand.REGISTER_DEFAULT_GROUP)
        self.assertFalse(result.echoed)

    def test_exchange_runtime_does_not_count_exact_echo_as_ack(self) -> None:
        light = ZhiyunLight(ExactEchoTransport())

        result = light.exchange_runtime(RuntimeCommand.BRIGHTNESS, b"\x01")

        self.assertFalse(result.acknowledged)
        self.assertTrue(result.echoed)
        self.assertEqual(result.transport_status, "echoed_write")

    def test_exchange_runtime_skips_echo_before_real_ack(self) -> None:
        light = ZhiyunLight(EchoThenAckTransport())

        result = light.exchange_runtime(RuntimeCommand.DEVICE_INFO)

        self.assertTrue(result.acknowledged)
        self.assertTrue(result.echoed)
        self.assertEqual(result.transport_status, "acknowledged")
        self.assertEqual(result.ack.payload, b"\x00")

    def test_exchange_frame_allows_custom_first_word_for_discovery(self) -> None:
        transport = EchoAckTransport()
        light = ZhiyunLight(transport)

        result = light.exchange_frame(0x0301, RuntimeCommand.DEVICE_INFO)

        self.assertTrue(result.acknowledged)
        self.assertEqual(first_frame(transport.sent[0]).first_word, 0x0301)

    def test_apply_scene_orders_media_workflow_commands(self) -> None:
        transport = EchoAckTransport()
        light = ZhiyunLight(transport)

        results = light.apply_scene(Scene(obj=1, sleep=0, brightness=25, kelvin=5600))

        self.assertEqual([result.command for result in results], [0x1008, 0x1001, 0x1002])
        sent_cmds = [first_frame(tx).cmd for tx in transport.sent]
        self.assertEqual(sent_cmds, [0x1008, 0x1001, 0x1002])

    def test_apply_scene_requires_complete_rgb_tuple(self) -> None:
        light = ZhiyunLight(EchoAckTransport())

        with self.assertRaisesRegex(ValueError, "RGB"):
            light.apply_scene(Scene(obj=1, red=255))

    def test_transition_scene_applies_interpolated_updates(self) -> None:
        transport = EchoAckTransport()
        light = ZhiyunLight(transport)

        batches = light.transition_scene(
            Scene(obj=1, brightness=0),
            Scene(obj=1, brightness=100),
            steps=2,
            duration=0,
        )

        self.assertEqual(
            [result.command for batch in batches for result in batch],
            [0x1001, 0x1001],
        )
        self.assertEqual([first_frame(tx).cmd for tx in transport.sent], [0x1001, 0x1001])


if __name__ == "__main__":
    unittest.main()
