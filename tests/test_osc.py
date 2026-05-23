from __future__ import annotations

import unittest

from zhiyun_light_control.models import CommandResult
from zhiyun_light_control.osc import (
    OscLightDispatcher,
    decode_message,
    encode_message,
)
from zhiyun_light_control.presets import ScenePresetLibrary
from zhiyun_light_control.protocol import build_runtime_frame, first_frame


class FakeProbe:
    def to_dict(self):
        return {"firmware": "test", "device_id": 1}


class FakeLight:
    def __init__(self) -> None:
        self.commands: list[int] = []

    def __enter__(self) -> "FakeLight":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def probe(self) -> FakeProbe:
        return FakeProbe()

    def exchange_runtime(self, cmd: int, payload: bytes = b"", *, timeout: float = 0.8):
        del payload, timeout
        self.commands.append(cmd)
        tx = build_runtime_frame(1, cmd)
        rx = build_runtime_frame(1, cmd, b"\x00")
        ack = first_frame(rx, cmd=cmd)
        return CommandResult(cmd, tx, rx, (ack,), ack)

    def apply_scene(self, scene):
        results = []
        if scene.brightness is not None:
            results.append(self.exchange_runtime(0x1001))
        if scene.kelvin is not None:
            results.append(self.exchange_runtime(0x1002))
        if scene.sleep is not None:
            results.append(self.exchange_runtime(0x1008))
        return results


class OscTests(unittest.TestCase):
    def test_encode_decode_round_trip(self) -> None:
        packet = encode_message("/zhiyun/scene", 35.5, 5600, 0, True, None, "key")

        message = decode_message(packet)

        self.assertEqual(message.address, "/zhiyun/scene")
        self.assertAlmostEqual(message.args[0], 35.5)
        self.assertEqual(message.args[1:], (5600, 0, True, None, "key"))

    def test_dispatch_probe_without_control_enabled(self) -> None:
        light = FakeLight()
        dispatcher = OscLightDispatcher(lambda: light, allow_control=False)

        result = dispatcher.dispatch(decode_message(encode_message("/zhiyun/probe")))

        self.assertEqual(result.action, "probe")
        self.assertEqual(result.result["firmware"], "test")

    def test_dispatch_blocks_control_without_allow_control(self) -> None:
        dispatcher = OscLightDispatcher(lambda: FakeLight(), allow_control=False)

        result = dispatcher.dispatch(decode_message(encode_message("/zhiyun/brightness", 25.0)))

        self.assertEqual(result.action, "blocked")
        self.assertIn("allow_control", result.error)

    def test_dispatch_scene_maps_to_light_scene(self) -> None:
        light = FakeLight()
        dispatcher = OscLightDispatcher(lambda: light, allow_control=True)

        result = dispatcher.dispatch(decode_message(encode_message("/zhiyun/scene", 25.0, 5600, 0)))

        self.assertEqual(result.action, "scene")
        self.assertEqual([item["command"] for item in result.result["results"]], [0x1001, 0x1002, 0x1008])
        self.assertEqual(light.commands, [0x1001, 0x1002, 0x1008])

    def test_dispatch_preset_maps_named_scene(self) -> None:
        light = FakeLight()
        library = ScenePresetLibrary.from_mapping(
            {"scenes": {"key": {"brightness": 30, "kelvin": 5000}}}
        )
        dispatcher = OscLightDispatcher(
            lambda: light,
            allow_control=True,
            preset_library=library,
        )

        result = dispatcher.dispatch(decode_message(encode_message("/zhiyun/preset", "key", 2)))

        self.assertEqual(result.action, "preset")
        self.assertEqual(result.result["preset"], "key")
        self.assertEqual(result.result["scene"]["obj"], 2)
        self.assertEqual([item["command"] for item in result.result["results"]], [0x1001, 0x1002])


if __name__ == "__main__":
    unittest.main()
