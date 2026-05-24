from __future__ import annotations

import unittest

from zhiyun_light_control import (
    CueLibrary,
    LightController,
    Scene,
    ScenePresetLibrary,
    UnconfirmedCommandError,
    serialized_plan_bundle,
)
from zhiyun_light_control.models import CommandResult
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    build_frame,
    build_runtime_frame,
    first_frame,
)


class FakeLight:
    def __init__(self, *, acknowledged: bool = True) -> None:
        self.acknowledged = acknowledged
        self.scenes: list[Scene] = []
        self.runtime_requests: list[tuple[int, bytes]] = []
        self.prebuilt_frames: list[bytes] = []
        self.primitive_calls: list[tuple[str, int, object, int]] = []
        self.transitions: list[tuple[Scene, Scene, int, float, str]] = []

    def __enter__(self) -> FakeLight:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def probe(self):
        return {"firmware": "test"}

    def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        del timeout
        self.runtime_requests.append((cmd, payload))
        if cmd == RuntimeCommand.BRIGHTNESS:
            return _result(
                cmd,
                acknowledged=self.acknowledged,
                payload=bytes.fromhex("01000000000c42"),
            )
        if cmd == RuntimeCommand.CCT:
            return _result(
                cmd,
                acknowledged=self.acknowledged,
                payload=bytes.fromhex("010000e015"),
            )
        if cmd == RuntimeCommand.SLEEP:
            return _result(
                cmd,
                acknowledged=self.acknowledged,
                payload=bytes.fromhex("01000000"),
            )
        return _result(cmd, acknowledged=self.acknowledged)

    def exchange_prebuilt_frame(
        self,
        frame: bytes,
        command: int,
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        del timeout
        self.prebuilt_frames.append(frame)
        return _prebuilt_result(
            frame,
            command,
            acknowledged=self.acknowledged,
        )

    def set_brightness(
        self,
        obj: int,
        value: float,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("brightness", obj, value, control_mode))
        return _result(RuntimeCommand.BRIGHTNESS, acknowledged=self.acknowledged)

    def set_brightness_with_mode(
        self,
        obj: int,
        value: float,
        mode: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(
            ("brightness_with_mode", obj, (value, mode), control_mode)
        )
        return _result(
            RuntimeCommand.BRIGHTNESS_WITH_MODE,
            acknowledged=self.acknowledged,
        )

    def set_cct(
        self,
        obj: int,
        kelvin: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("cct", obj, kelvin, control_mode))
        return _result(RuntimeCommand.CCT, acknowledged=self.acknowledged)

    def set_sleep(
        self,
        obj: int,
        value: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("sleep", obj, value, control_mode))
        return _result(RuntimeCommand.SLEEP, acknowledged=self.acknowledged)

    def set_rgb(
        self,
        obj: int,
        red: int,
        green: int,
        blue: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("rgb", obj, (red, green, blue), control_mode))
        return _result(RuntimeCommand.RGB, acknowledged=self.acknowledged)

    def set_hsi(
        self,
        obj: int,
        hue: float,
        saturation: float,
        intensity: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(
            ("hsi", obj, (hue, saturation, intensity), control_mode)
        )
        return _result(RuntimeCommand.HSI, acknowledged=self.acknowledged)

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
        self.assertEqual(controller.state()["action"], "preset")
        self.assertEqual(controller.state()["scene"]["brightness"], 35.0)
        self.assertEqual(controller.state_snapshot()["version"], 2)
        history = controller.state_history()
        self.assertEqual([event["version"] for event in history["events"]], [1, 2])
        self.assertEqual(history["events"][0]["state"]["action"], "scene")
        self.assertEqual(history["events"][1]["state"]["action"], "preset")

    def test_controller_runs_primitive_commands_with_state_evidence(self) -> None:
        light = FakeLight()
        controller = LightController(light_factory=FakeFactory(light))

        register = controller.register(device_id=1, group_id=2)
        read = controller.read_brightness(obj=1)
        brightness = controller.set_brightness(35, obj=2, control_mode=0x01)
        brightness_mode = controller.set_brightness_with_mode(
            36,
            2,
            obj=2,
            control_mode=0x01,
        )
        cct = controller.set_cct(5600, obj=2, control_mode=0x01)
        sleep = controller.set_sleep(0, obj=2, control_mode=0x01)
        rgb = controller.set_rgb(255, 180, 120, obj=2, control_mode=0x01)
        hsi = controller.set_hsi(30.0, 0.5, 40, obj=2, control_mode=0x01)

        self.assertTrue(register["acknowledged"])
        self.assertEqual(register["action"], "register")
        self.assertTrue(read["acknowledged"])
        self.assertEqual(read["action"], "read_brightness")
        self.assertEqual(read["value"], 35.0)
        self.assertEqual(read["obj"], 1)
        self.assertEqual(read["operation"], 0)
        self.assertEqual(read["decoded"]["value"], 35.0)
        self.assertTrue(brightness["applied"])
        self.assertEqual(brightness_mode["mode"], 2)
        self.assertEqual(cct["scene"]["kelvin"], 5600)
        self.assertEqual(sleep["scene"]["sleep"], 0)
        self.assertEqual(rgb["scene"]["red"], 255)
        self.assertEqual(hsi["scene"]["hue"], 30.0)
        self.assertEqual(
            light.primitive_calls,
            [
                ("brightness", 2, 35, 0x01),
                ("brightness_with_mode", 2, (36, 2), 0x01),
                ("cct", 2, 5600, 0x01),
                ("sleep", 2, 0, 0x01),
                ("rgb", 2, (255, 180, 120), 0x01),
                ("hsi", 2, (30.0, 0.5, 40), 0x01),
            ],
        )
        self.assertEqual(controller.state()["action"], "set_hsi")
        self.assertEqual(controller.state_snapshot()["version"], 6)

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

    def test_controller_plans_sdk_primitives_without_opening_light(self) -> None:
        light = FakeLight()
        presets = ScenePresetLibrary.from_mapping(
            {"key": {"brightness": 25, "kelvin": 5600}}
        )
        cues = CueLibrary.from_mapping(
            {"intro": {"steps": [{"preset": "key"}], "stop_on_unconfirmed": True}}
        )
        controller = LightController(
            light_factory=FakeFactory(light),
            preset_library=presets,
            cue_library=cues,
        )

        scene = controller.plan_scene(
            {"brightness": 12},
            obj=2,
            control_mode=0x01,
            first_word=0x0301,
            start_seq=3,
        )
        preset = controller.plan_preset(
            "key",
            overrides={"brightness": 30},
            start_seq=scene["next_seq"],
        )
        transition = controller.plan_transition(
            {"brightness": 40},
            from_scene={"brightness": 20},
            steps=2,
            duration=0.5,
            start_seq=7,
        )
        cue = controller.plan_named_cue(
            "intro",
            stop_on_unconfirmed=False,
            start_seq=11,
        )

        self.assertTrue(scene["dry_run"])
        self.assertEqual(scene["action"], "scene")
        self.assertEqual(scene["scene"]["obj"], 2)
        self.assertEqual(scene["control_mode"], 0x01)
        self.assertEqual(scene["first_word_hex"], "0x0301")
        self.assertEqual(scene["next_seq"], 4)
        self.assertEqual(preset["action"], "preset")
        self.assertEqual(preset["scene"]["brightness"], 30.0)
        self.assertEqual(preset["command_plan"]["start_seq"], 4)
        self.assertEqual(transition["action"], "transition")
        self.assertEqual(
            [
                batch["scene"]["brightness"]
                for batch in transition["command_batches"]
            ],
            [30.0, 40.0],
        )
        self.assertEqual(cue["cue"], "intro")
        self.assertFalse(cue["stop_on_unconfirmed"])
        self.assertEqual(cue["steps"][0]["action"], "preset")
        self.assertEqual(light.scenes, [])
        self.assertEqual(light.transitions, [])

    def test_controller_executes_serialized_scene_plan_frames(self) -> None:
        light = FakeLight()
        controller = LightController(light_factory=FakeFactory(light))
        plan = controller.plan_scene(
            {"brightness": 12},
            first_word=0x0301,
            start_seq=7,
        )

        response = controller.execute_plan(
            serialized_plan_bundle(plan, created_at=123.0),
            timeout=0.25,
        )

        self.assertEqual(response["action"], "execute_plan")
        self.assertEqual(response["planned_action"], "scene")
        self.assertTrue(response["applied"])
        self.assertEqual(response["scene"]["brightness"], 12.0)
        self.assertEqual(len(response["results"]), 1)
        self.assertEqual(
            [first_frame(frame).first_word for frame in light.prebuilt_frames],
            [0x0301],
        )
        self.assertEqual(
            [first_frame(frame).seq for frame in light.prebuilt_frames],
            [7],
        )
        self.assertEqual(controller.state()["action"], "execute_plan")
        self.assertEqual(controller.state()["scene"]["brightness"], 12.0)

    def test_controller_executes_serialized_sequence_plan_frames(self) -> None:
        light = FakeLight()
        presets = ScenePresetLibrary.from_mapping(
            {"scenes": {"key": {"brightness": 15, "kelvin": 5600}}}
        )
        controller = LightController(
            light_factory=FakeFactory(light),
            preset_library=presets,
        )
        plan = controller.plan_sequence(
            [
                {"scene": {"brightness": 10}},
                {"preset": "key"},
                {"to": {"brightness": 20}, "steps": 1, "duration": 0},
            ],
            first_word=0x0301,
            start_seq=21,
        )

        response = controller.execute_plan(plan, timeout=0.25)

        self.assertEqual(response["planned_action"], "sequence")
        self.assertTrue(response["applied"])
        self.assertEqual(response["scene"]["brightness"], 20.0)
        self.assertEqual(
            [first_frame(frame).seq for frame in light.prebuilt_frames],
            [21, 22, 23, 24],
        )
        self.assertEqual(
            [first_frame(frame).cmd for frame in light.prebuilt_frames],
            [
                RuntimeCommand.BRIGHTNESS,
                RuntimeCommand.BRIGHTNESS,
                RuntimeCommand.CCT,
                RuntimeCommand.BRIGHTNESS,
            ],
        )
        self.assertEqual(controller.state()["action"], "execute_plan")
        self.assertEqual(controller.state()["scene"]["brightness"], 20.0)

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
        plan = controller.plan_sequence(
            cues.get("intro")["steps"],
            control_mode=0x01,
            first_word=0x0301,
            start_seq=9,
        )

        self.assertEqual(response["cue"], "intro")
        self.assertEqual(response["action"], "cue")
        self.assertTrue(response["applied"])
        self.assertEqual(response["steps"][1]["action"], "transition")
        self.assertEqual(light.transitions[0][2], 2)
        self.assertEqual(plan["start_seq"], 9)
        self.assertEqual(plan["next_seq"], 12)
        self.assertEqual(plan["steps"][0]["command_plan"]["start_seq"], 9)
        self.assertEqual(
            plan["steps"][0]["command_plan"]["frames"][0]["first_word_hex"],
            "0x0301",
        )
        self.assertEqual(plan["steps"][1]["from"]["brightness"], 10.0)
        self.assertEqual(
            [
                batch["scene"]["brightness"]
                for batch in plan["steps"][1]["command_batches"]
            ],
            [20.0, 30.0],
        )
        self.assertEqual(plan["scene"]["brightness"], 30.0)
        self.assertEqual(controller.state()["action"], "cue")
        self.assertEqual(controller.state()["scene"]["brightness"], 30.0)

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
        self.assertFalse(controller.state()["applied"])
        self.assertEqual(controller.state()["reason"], "sent_no_response")

    def test_controller_waits_for_state_updates(self) -> None:
        controller = LightController(light_factory=FakeFactory(FakeLight()))

        empty = controller.state_snapshot()
        self.assertEqual(empty, {"version": 0, "state": {"scene": None}})

        controller.apply_scene(Scene(obj=1, brightness=10))
        update = controller.wait_for_state_update(0, timeout=0.1)

        self.assertEqual(update["version"], 1)
        self.assertEqual(update["state"]["scene"]["brightness"], 10)

    def test_controller_iterates_state_events(self) -> None:
        controller = LightController(light_factory=FakeFactory(FakeLight()))

        initial = next(controller.state_events(limit=1, timeout=0.1))
        self.assertEqual(initial, {"version": 0, "state": {"scene": None}})

        controller.apply_scene(Scene(obj=1, brightness=10))
        controller.apply_scene(Scene(obj=1, brightness=20))
        events = list(
            controller.state_events(
                after_version=1,
                limit=1,
                timeout=0.1,
                initial=False,
            )
        )

        self.assertEqual(events[0]["version"], 2)
        self.assertEqual(events[0]["state"]["scene"]["brightness"], 20)

    def test_controller_rejects_malformed_cue_steps(self) -> None:
        controller = LightController(light_factory=FakeFactory(FakeLight()))

        with self.assertRaisesRegex(ValueError, "cue steps must be objects"):
            controller.run_cue({"steps": ["not-a-step"]})

    def test_controller_closes_persistent_factory(self) -> None:
        factory = FakeFactory(FakeLight())
        controller = LightController(light_factory=factory)

        controller.close()

        self.assertTrue(factory.closed)


def _result(
    command: int,
    *,
    acknowledged: bool,
    payload: bytes = b"\x00",
) -> CommandResult:
    tx = build_runtime_frame(1, command, b"")
    if not acknowledged:
        return CommandResult(command, tx, b"", (), None)
    rx = build_runtime_frame(1, command, payload)
    ack = first_frame(rx, cmd=command)
    return CommandResult(command, tx, rx, (ack,), ack)


def _prebuilt_result(
    tx: bytes,
    command: int,
    *,
    acknowledged: bool,
) -> CommandResult:
    if not acknowledged:
        return CommandResult(command, tx, b"", (), None)
    frame = first_frame(tx)
    assert frame is not None
    rx = build_frame(frame.first_word, frame.seq, command, b"\x00")
    ack = first_frame(rx, cmd=command)
    return CommandResult(command, tx, rx, (ack,), ack)


if __name__ == "__main__":
    unittest.main()
