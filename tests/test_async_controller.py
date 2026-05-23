from __future__ import annotations

import unittest
from unittest.mock import patch

from zhiyun_light_control import (
    AsyncLightController,
    CueLibrary,
    LightConnectionConfig,
    Scene,
    ScenePresetLibrary,
    UnconfirmedCommandError,
    open_async_light,
)
from zhiyun_light_control.models import CommandResult
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    build_runtime_frame,
    first_frame,
)


class FakeAsyncLight:
    def __init__(self, *, acknowledged: bool = True) -> None:
        self.acknowledged = acknowledged
        self.opened = False
        self.closed = False
        self.scenes: list[Scene] = []
        self.control_modes: list[int] = []
        self.transitions: list[tuple[Scene, Scene, int, float, str, int]] = []

    async def __aenter__(self) -> FakeAsyncLight:
        self.opened = True
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        self.closed = True

    async def probe(self) -> dict[str, str]:
        return {"firmware": "async-test"}

    async def apply_scene(
        self,
        scene: Scene,
        *,
        control_mode: int = 0x33,
    ) -> list[CommandResult]:
        self.scenes.append(scene)
        self.control_modes.append(control_mode)
        return [_result(RuntimeCommand.BRIGHTNESS, acknowledged=self.acknowledged)]

    async def transition_scene(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int = 0x33,
    ) -> list[list[CommandResult]]:
        self.transitions.append((start, end, steps, duration, easing, control_mode))
        return [
            [_result(RuntimeCommand.BRIGHTNESS, acknowledged=self.acknowledged)]
            for _index in range(steps)
        ]


class FakeAsyncFactory:
    def __init__(self, light: FakeAsyncLight) -> None:
        self.light = light
        self.closed = False

    def __call__(self) -> FakeAsyncLight:
        return self.light

    async def close(self) -> None:
        self.closed = True


class AsyncControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_controller_applies_scenes_and_presets(self) -> None:
        light = FakeAsyncLight()
        presets = ScenePresetLibrary.from_mapping(
            {"scenes": {"key": {"brightness": 25, "kelvin": 5600}}}
        )
        controller = AsyncLightController(
            light_factory=FakeAsyncFactory(light),
            preset_library=presets,
        )

        scene = await controller.apply_scene(Scene(obj=1, brightness=10))
        preset = await controller.apply_preset("key", overrides={"brightness": 35})

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
        history = controller.state_history(limit=1)
        self.assertEqual(history["events"][0]["version"], 2)
        self.assertEqual(history["events"][0]["state"]["action"], "preset")

    async def test_async_controller_runs_sequences_and_stops_on_unconfirmed(
        self,
    ) -> None:
        light = FakeAsyncLight(acknowledged=False)
        presets = ScenePresetLibrary.from_mapping({"key": {"brightness": 40}})
        controller = AsyncLightController(
            light_factory=FakeAsyncFactory(light),
            preset_library=presets,
        )

        sequence = await controller.run_sequence(
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

    async def test_async_controller_plans_sdk_primitives_without_opening_light(
        self,
    ) -> None:
        light = FakeAsyncLight()
        presets = ScenePresetLibrary.from_mapping(
            {"key": {"brightness": 25, "kelvin": 5600}}
        )
        cues = CueLibrary.from_mapping(
            {"intro": {"steps": [{"preset": "key"}], "stop_on_unconfirmed": True}}
        )
        controller = AsyncLightController(
            light_factory=FakeAsyncFactory(light),
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
        self.assertFalse(light.opened)
        self.assertFalse(light.closed)

    async def test_async_controller_runs_named_cues_and_transitions(self) -> None:
        light = FakeAsyncLight()
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
        controller = AsyncLightController(
            light_factory=FakeAsyncFactory(light),
            cue_library=cues,
        )

        response = await controller.run_named_cue("intro")
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

    async def test_async_controller_strict_mode_raises_for_unconfirmed(self) -> None:
        light = FakeAsyncLight(acknowledged=False)
        controller = AsyncLightController(
            light_factory=FakeAsyncFactory(light),
            require_acknowledged=True,
        )

        with self.assertRaises(UnconfirmedCommandError) as error:
            await controller.apply_scene(Scene(obj=1, brightness=10))

        self.assertEqual(error.exception.action, "scene")
        self.assertEqual(error.exception.statuses, ["sent_no_response"])
        self.assertFalse(controller.state()["applied"])
        self.assertEqual(controller.state()["reason"], "sent_no_response")

    async def test_async_controller_waits_for_state_updates(self) -> None:
        controller = AsyncLightController(
            light_factory=FakeAsyncFactory(FakeAsyncLight())
        )

        empty = controller.state_snapshot()
        self.assertEqual(empty, {"version": 0, "state": {"scene": None}})

        await controller.apply_scene(Scene(obj=1, brightness=10))
        update = controller.wait_for_state_update(0, timeout=0.1)

        self.assertEqual(update["version"], 1)
        self.assertEqual(update["state"]["scene"]["brightness"], 10)

    async def test_async_controller_rejects_malformed_cue_steps(self) -> None:
        controller = AsyncLightController(
            light_factory=FakeAsyncFactory(FakeAsyncLight())
        )

        with self.assertRaisesRegex(ValueError, "cue steps must be objects"):
            await controller.run_cue({"steps": ["not-a-step"]})

    async def test_async_controller_closes_factory(self) -> None:
        factory = FakeAsyncFactory(FakeAsyncLight())
        controller = AsyncLightController(light_factory=factory)

        await controller.close()

        self.assertTrue(factory.closed)

    async def test_async_context_manager_closes_factory(self) -> None:
        factory = FakeAsyncFactory(FakeAsyncLight())

        async with AsyncLightController(light_factory=factory):
            pass

        self.assertTrue(factory.closed)

    def test_open_async_light_rejects_usb_config(self) -> None:
        with self.assertRaisesRegex(ValueError, "BLE"):
            open_async_light(LightConnectionConfig(transport="usb"))

    def test_open_async_light_selects_worker_backend(self) -> None:
        fake = FakeAsyncLight()

        with patch(
            "zhiyun_light_control.controller.AsyncZhiyunLight.isolated_ble",
            return_value=fake,
        ) as make_ble:
            light = open_async_light(
                LightConnectionConfig(
                    transport="ble",
                    address="AA:BB",
                    name_contains="MOLUS",
                    timeout=2.5,
                    ble_python="python-test",
                )
            )

        self.assertIs(light, fake)
        make_ble.assert_called_once()
        self.assertEqual(make_ble.call_args.kwargs["address"], "AA:BB")
        self.assertEqual(make_ble.call_args.kwargs["name_contains"], "MOLUS")
        self.assertEqual(make_ble.call_args.kwargs["timeout"], 2.5)
        self.assertEqual(make_ble.call_args.kwargs["python"], "python-test")

    def test_open_async_light_selects_direct_backend(self) -> None:
        fake = FakeAsyncLight()

        with patch(
            "zhiyun_light_control.controller.AsyncZhiyunLight.ble",
            return_value=fake,
        ) as make_ble:
            light = open_async_light(
                LightConnectionConfig(transport="ble", ble_in_process=True)
            )

        self.assertIs(light, fake)
        make_ble.assert_called_once()

    def test_open_async_light_selects_macos_app_backend(self) -> None:
        fake = FakeAsyncLight()

        with patch(
            "zhiyun_light_control.controller.AsyncZhiyunLight.macos_ble_app",
            return_value=fake,
        ) as make_ble:
            light = open_async_light(
                LightConnectionConfig(transport="ble", ble_backend="macos-app")
            )

        self.assertIs(light, fake)
        make_ble.assert_called_once()


def _result(command: int, *, acknowledged: bool) -> CommandResult:
    tx = build_runtime_frame(1, command, b"")
    if not acknowledged:
        return CommandResult(command, tx, b"", (), None)
    rx = build_runtime_frame(1, command, b"\x00")
    ack = first_frame(rx, cmd=command)
    return CommandResult(command, tx, rx, (ack,), ack)


if __name__ == "__main__":
    unittest.main()
