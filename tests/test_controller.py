from __future__ import annotations

import unittest

from zhiyun_light_control import (
    CueLibrary,
    LightController,
    Scene,
    ScenePresetLibrary,
    UnconfirmedCommandError,
)
from zhiyun_light_control.models import CommandResult
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    build_runtime_frame,
    first_frame,
)


class FakeLight:
    def __init__(self, *, acknowledged: bool = True) -> None:
        self.acknowledged = acknowledged
        self.scenes: list[Scene] = []
        self.transitions: list[tuple[Scene, Scene, int, float, str]] = []

    def __enter__(self) -> FakeLight:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def probe(self):
        return {"firmware": "test"}

    def apply_scene(self, scene: Scene, *, control_mode: int = 0x33):
        del control_mode
        self.scenes.append(scene)
        return [_result(RuntimeCommand.BRIGHTNESS, acknowledged=self.acknowledged)]

    def transition_scene(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int = 0x33,
    ):
        del control_mode
        self.transitions.append((start, end, steps, duration, easing))
        return [
            [_result(RuntimeCommand.BRIGHTNESS, acknowledged=self.acknowledged)]
            for _index in range(steps)
        ]


class FakeFactory:
    def __init__(self, light: FakeLight) -> None:
        self.light = light
        self.closed = False

    def __call__(self) -> FakeLight:
        return self.light

    def close(self) -> None:
        self.closed = True


class ControllerTests(unittest.TestCase):
    def test_controller_applies_scenes_and_presets_with_ack_evidence(self) -> None:
        light = FakeLight()
        presets = ScenePresetLibrary.from_mapping(
            {"scenes": {"key": {"brightness": 25, "kelvin": 5600}}}
        )
        controller = LightController(
            light_factory=FakeFactory(light),
            preset_library=presets,
        )

        scene = controller.apply_scene(Scene(obj=1, brightness=10))
        preset = controller.apply_preset("key", overrides={"brightness": 35})

        self.assertTrue(scene["applied"])
        self.assertIsNone(scene["reason"])
        self.assertEqual(preset["preset"], "key")
        self.assertEqual(preset["scene"]["brightness"], 35.0)
        self.assertEqual(
            [sent.brightness for sent in light.scenes],
            [10, 35.0],
        )

    def test_controller_runs_sequences_and_stops_on_unconfirmed_steps(self) -> None:
        light = FakeLight(acknowledged=False)
        presets = ScenePresetLibrary.from_mapping({"key": {"brightness": 40}})
        controller = LightController(
            light_factory=FakeFactory(light),
            preset_library=presets,
        )

        sequence = controller.run_sequence(
            [
                {"scene": {"brightness": 10}},
                {"preset": "key"},
            ],
            stop_on_unconfirmed=True,
        )

        self.assertFalse(sequence["applied"])
        self.assertEqual(sequence["reason"], "sent_no_response")
        self.assertTrue(sequence["stopped"])
        self.assertEqual(len(sequence["steps"]), 1)
        self.assertEqual(light.scenes[0].brightness, 10)

    def test_controller_runs_named_cues_and_transitions(self) -> None:
        light = FakeLight()
        cues = CueLibrary.from_mapping(
            {
                "intro": {
                    "steps": [
                        {"scene": {"brightness": 10}},
                        {
                            "to": {"brightness": 30},
                            "steps": 2,
                            "duration": 0,
                            "easing": "linear",
                        },
                    ]
                }
            }
        )
        controller = LightController(light_factory=FakeFactory(light), cue_library=cues)

        response = controller.run_named_cue("intro")
        plan = controller.plan_sequence(cues.get("intro")["steps"])

        self.assertEqual(response["cue"], "intro")
        self.assertTrue(response["applied"])
        self.assertEqual(response["steps"][1]["action"], "transition")
        self.assertEqual(light.transitions[0][2], 2)
        self.assertEqual(plan["steps"][1]["from"]["brightness"], 10.0)
        self.assertEqual(plan["scene"]["brightness"], 30.0)

    def test_controller_strict_mode_raises_for_unconfirmed_control(self) -> None:
        light = FakeLight(acknowledged=False)
        controller = LightController(
            light_factory=FakeFactory(light),
            require_acknowledged=True,
        )

        with self.assertRaises(UnconfirmedCommandError) as error:
            controller.apply_scene(Scene(obj=1, brightness=10))

        self.assertEqual(error.exception.action, "scene")
        self.assertEqual(error.exception.statuses, ["sent_no_response"])

    def test_controller_rejects_malformed_cue_steps(self) -> None:
        controller = LightController(light_factory=FakeFactory(FakeLight()))

        with self.assertRaisesRegex(ValueError, "cue steps must be objects"):
            controller.run_cue({"steps": ["not-a-step"]})

    def test_controller_closes_persistent_factory(self) -> None:
        factory = FakeFactory(FakeLight())
        controller = LightController(light_factory=factory)

        controller.close()

        self.assertTrue(factory.closed)


def _result(command: int, *, acknowledged: bool) -> CommandResult:
    tx = build_runtime_frame(1, command, b"")
    if not acknowledged:
        return CommandResult(command, tx, b"", (), None)
    rx = build_runtime_frame(1, command, b"\x00")
    ack = first_frame(rx, cmd=command)
    return CommandResult(command, tx, rx, (ack,), ack)


if __name__ == "__main__":
    unittest.main()
