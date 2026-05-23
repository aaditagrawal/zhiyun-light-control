from __future__ import annotations

import unittest

from zhiyun_light_control import AsyncProbeResult, AsyncZhiyunLight, Scene
from zhiyun_light_control.protocol import RuntimeCommand, build_runtime_frame, first_frame


class AsyncEchoAckTransport:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def exchange(self, tx: bytes, timeout: float = 1.5) -> bytes:
        del timeout
        self.sent.append(tx)
        frame = first_frame(tx)
        assert frame is not None
        return build_runtime_frame(frame.seq, frame.cmd, b"\x00")

    async def close(self) -> None:
        return


class AsyncClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_client_exports_probe_result_type(self) -> None:
        result = AsyncProbeResult(
            device_identifier="id",
            generation="pl103",
            firmware="1.6.4",
            voltage_status=101,
            device_id=1,
            address="AA",
        )

        self.assertEqual(result.to_dict()["address"], "AA")

    async def test_async_object_helpers_match_sync_command_surface(self) -> None:
        transport = AsyncEchoAckTransport()
        light = AsyncZhiyunLight(transport)

        await light.read_brightness(obj=7)
        await light.read_cct(obj=7)
        await light.read_sleep(obj=7)
        await light.get_object_voltage(obj=7)
        await light.get_object_mode(obj=7)
        await light.identify(obj=7)

        frames = [first_frame(tx) for tx in transport.sent]
        self.assertEqual(
            [frame.cmd for frame in frames],
            [
                RuntimeCommand.BRIGHTNESS,
                RuntimeCommand.CCT,
                RuntimeCommand.SLEEP,
                RuntimeCommand.VOLTAGE_BY_OBJECT,
                RuntimeCommand.DEVICE_MODE,
                RuntimeCommand.IDENTIFY,
            ],
        )
        self.assertEqual([frame.payload[2] for frame in frames[:3]], [0, 0, 0])
        self.assertEqual([frame.payload[:2] for frame in frames], [b"\x07\x00"] * 6)

    async def test_async_exchange_frame_allows_custom_first_word(self) -> None:
        transport = AsyncEchoAckTransport()
        light = AsyncZhiyunLight(transport)

        result = await light.exchange_frame(0x0301, RuntimeCommand.DEVICE_INFO)

        self.assertTrue(result.acknowledged)
        self.assertEqual(first_frame(transport.sent[0]).first_word, 0x0301)

    async def test_async_apply_scene_orders_media_workflow_commands(self) -> None:
        transport = AsyncEchoAckTransport()
        light = AsyncZhiyunLight(transport)

        results = await light.apply_scene(Scene(obj=1, sleep=0, brightness=25, kelvin=5600))

        self.assertEqual([result.command for result in results], [0x1008, 0x1001, 0x1002])
        sent_cmds = [first_frame(tx).cmd for tx in transport.sent]
        self.assertEqual(sent_cmds, [0x1008, 0x1001, 0x1002])

    async def test_async_transition_scene_matches_sync_surface(self) -> None:
        transport = AsyncEchoAckTransport()
        light = AsyncZhiyunLight(transport)

        batches = await light.transition_scene(
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
