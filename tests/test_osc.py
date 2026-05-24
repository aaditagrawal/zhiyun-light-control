from __future__ import annotations

import unittest

from zhiyun_light_control.cues import CueLibrary
from zhiyun_light_control.models import CommandResult
from zhiyun_light_control.osc import (
    OscLightDispatcher,
    decode_message,
    encode_message,
)
from zhiyun_light_control.presets import ScenePresetLibrary
from zhiyun_light_control.protocol import build_runtime_frame, first_frame
from zhiyun_light_control.state import SceneStateTracker


class FakeProbe:
    def to_dict(self):
        return {"firmware": "test", "device_id": 1}


class FakeLight:
    def __init__(self) -> None:
        self.commands: list[int] = []
        self.payloads: list[tuple[int, bytes]] = []
        self.transition_calls = []

    def __enter__(self) -> FakeLight:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def probe(self) -> FakeProbe:
        return FakeProbe()

    def exchange_runtime(self, cmd: int, payload: bytes = b"", *, timeout: float = 0.8):
        del timeout
        self.commands.append(cmd)
        self.payloads.append((cmd, payload))
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

    def transition_scene(
        self,
        start,
        end,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
    ):
        self.transition_calls.append((start, end, steps, duration, easing))
        return [self.apply_scene(end) for _ in range(steps)]


class FakeUnconfirmedLight(FakeLight):
    def exchange_runtime(self, cmd: int, payload: bytes = b"", *, timeout: float = 0.8):
        del payload, timeout
        self.commands.append(cmd)
        tx = build_runtime_frame(1, cmd)
        return CommandResult(cmd, tx, b"", (), None)


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

        result = dispatcher.dispatch(
            decode_message(encode_message("/zhiyun/brightness", 25.0))
        )

        self.assertEqual(result.action, "blocked")
        self.assertIn("allow_control", result.error)

    def test_dispatch_register_accepts_group_id(self) -> None:
        light = FakeLight()
        dispatcher = OscLightDispatcher(lambda: light, allow_control=True)

        result = dispatcher.dispatch(
            decode_message(encode_message("/zhiyun/register", 2, 3))
        )

        self.assertEqual(result.action, "register")
        self.assertEqual(
            light.payloads[0],
            (0x0006, b"\x02\x00\x03\x00"),
        )

    def test_dispatch_scene_maps_to_light_scene(self) -> None:
        light = FakeLight()
        tracker = SceneStateTracker()
        dispatcher = OscLightDispatcher(lambda: light, allow_control=True)
        dispatcher.state_tracker = tracker

        result = dispatcher.dispatch(
            decode_message(encode_message("/zhiyun/scene", 25.0, 5600, 0))
        )

        self.assertEqual(result.action, "scene")
        self.assertEqual(
            [item["command"] for item in result.result["results"]],
            [0x1001, 0x1002, 0x1008],
        )
        self.assertEqual(light.commands, [0x1001, 0x1002, 0x1008])
        state = tracker.to_dict()
        self.assertEqual(state["source"], "osc")
        self.assertEqual(state["scene"]["kelvin"], 5600)
        self.assertTrue(state["applied"])

    def test_dispatch_scene_marks_unacknowledged_state_unapplied(self) -> None:
        light = FakeUnconfirmedLight()
        tracker = SceneStateTracker()
        dispatcher = OscLightDispatcher(lambda: light, allow_control=True)
        dispatcher.state_tracker = tracker

        result = dispatcher.dispatch(
            decode_message(encode_message("/zhiyun/brightness", 25.0))
        )

        self.assertEqual(result.action, "brightness")
        state = tracker.to_dict()
        self.assertFalse(state["applied"])
        self.assertEqual(state["reason"], "sent_no_response")
        self.assertEqual(state["result_statuses"], ["sent_no_response"])

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

        result = dispatcher.dispatch(
            decode_message(encode_message("/zhiyun/preset", "key", 2))
        )

        self.assertEqual(result.action, "preset")
        self.assertEqual(result.result["preset"], "key")
        self.assertEqual(result.result["scene"]["obj"], 2)
        self.assertEqual(
            [item["command"] for item in result.result["results"]], [0x1001, 0x1002]
        )

    def test_dispatch_cue_runs_named_steps(self) -> None:
        light = FakeLight()
        tracker = SceneStateTracker()
        presets = ScenePresetLibrary.from_mapping(
            {"scenes": {"key": {"brightness": 30, "kelvin": 5000}}}
        )
        cues = CueLibrary.from_mapping(
            {
                "intro": {
                    "steps": [
                        {"scene": {"brightness": 10}},
                        {"preset": "key", "overrides": {"kelvin": 5600}},
                    ],
                    "stop_on_unconfirmed": True,
                }
            }
        )
        dispatcher = OscLightDispatcher(
            lambda: light,
            allow_control=True,
            preset_library=presets,
            cue_library=cues,
            state_tracker=tracker,
        )

        result = dispatcher.dispatch(
            decode_message(encode_message("/zhiyun/cue", "intro", 2))
        )

        self.assertEqual(result.action, "cue")
        self.assertEqual(result.result["cue"], "intro")
        self.assertTrue(result.result["applied"])
        self.assertFalse(result.result["stopped"])
        self.assertEqual(
            [step["action"] for step in result.result["steps"]],
            ["scene", "preset"],
        )
        self.assertEqual(light.commands, [0x1001, 0x1001, 0x1002])
        state = tracker.to_dict()
        self.assertEqual(state["action"], "cue")
        self.assertEqual(state["scene"]["obj"], 2)
        self.assertEqual(state["scene"]["kelvin"], 5600)
        self.assertTrue(state["applied"])

    def test_dispatch_cue_stops_on_unconfirmed_step(self) -> None:
        light = FakeUnconfirmedLight()
        tracker = SceneStateTracker()
        cues = CueLibrary.from_mapping(
            {
                "intro": {
                    "steps": [
                        {"scene": {"brightness": 10}},
                        {"scene": {"kelvin": 5600}},
                    ],
                    "stop_on_unconfirmed": True,
                }
            }
        )
        dispatcher = OscLightDispatcher(
            lambda: light,
            allow_control=True,
            cue_library=cues,
            state_tracker=tracker,
        )

        result = dispatcher.dispatch(
            decode_message(encode_message("/zhiyun/cue", "intro"))
        )

        self.assertEqual(result.action, "cue")
        self.assertFalse(result.result["applied"])
        self.assertTrue(result.result["stopped"])
        self.assertEqual(result.result["reason"], "sent_no_response")
        self.assertEqual(len(result.result["steps"]), 1)
        self.assertEqual(light.commands, [0x1001])
        state = tracker.to_dict()
        self.assertEqual(state["action"], "cue")
        self.assertFalse(state["applied"])
        self.assertEqual(state["reason"], "sent_no_response")

    def test_dispatch_transition_uses_last_requested_scene_as_start(self) -> None:
        light = FakeLight()
        tracker = SceneStateTracker()
        dispatcher = OscLightDispatcher(
            lambda: light,
            allow_control=True,
            state_tracker=tracker,
        )
        dispatcher.dispatch(decode_message(encode_message("/zhiyun/scene", 10.0)))

        result = dispatcher.dispatch(
            decode_message(
                encode_message(
                    "/zhiyun/transition",
                    40.0,
                    5600,
                    None,
                    0.2,
                    3,
                    "ease-in-out",
                )
            )
        )

        self.assertEqual(result.action, "transition")
        self.assertTrue(result.result["applied"])
        self.assertEqual(result.result["from"]["brightness"], 10.0)
        self.assertEqual(result.result["scene"]["kelvin"], 5600)
        self.assertEqual(result.result["steps"], 3)
        self.assertAlmostEqual(result.result["duration"], 0.2)
        self.assertEqual(result.result["easing"], "ease-in-out")
        start, end, steps, duration, easing = light.transition_calls[0]
        self.assertEqual(start.brightness, 10.0)
        self.assertEqual(end.brightness, 40.0)
        self.assertEqual(steps, 3)
        self.assertAlmostEqual(duration, 0.2)
        self.assertEqual(easing, "ease-in-out")
        state = tracker.to_dict()
        self.assertEqual(state["action"], "transition")
        self.assertEqual(state["scene"]["brightness"], 40.0)
        self.assertTrue(state["applied"])


if __name__ == "__main__":
    unittest.main()
