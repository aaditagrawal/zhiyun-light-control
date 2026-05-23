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


if __name__ == "__main__":
    unittest.main()
