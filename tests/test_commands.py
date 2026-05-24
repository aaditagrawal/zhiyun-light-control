from __future__ import annotations

import unittest

from zhiyun_light_control import (
    CommandResult,
    RuntimeCommandSpec,
    RuntimeFrameSpec,
    Scene,
    SceneCommandPlan,
    UnconfirmedCommandError,
    execute_async_command_plan,
    execute_async_command_plan_confirmed,
    execute_async_frame_plan,
    execute_async_serialized_frame_plan,
    execute_async_serialized_frame_plan_confirmed,
    execute_async_transition_frame_plans,
    execute_async_transition_plans,
    execute_command_plan,
    execute_command_plan_confirmed,
    execute_frame_plan,
    execute_serialized_frame_plan,
    execute_serialized_frame_plan_confirmed,
    execute_transition_frame_plans,
    execute_transition_plans,
    scene_command_plan,
    scene_command_specs,
    scene_frame_specs,
    serialized_frame_commands,
    transition_command_plans,
)
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    build_frame,
    build_runtime_frame,
    first_frame,
)


class FakeRuntimeLight:
    def __init__(self, *, acknowledged: bool = True) -> None:
        self.acknowledged = acknowledged
        self.exchanges: list[tuple[int, bytes, float | None]] = []

    def exchange_runtime(
        self,
        command: int,
        payload: bytes = b"",
        *,
        timeout: float | None = None,
    ) -> CommandResult:
        self.exchanges.append((command, payload, timeout))
        tx = build_runtime_frame(len(self.exchanges), command, payload)
        if not self.acknowledged:
            return CommandResult(command, tx, b"", (), None)
        rx = build_runtime_frame(len(self.exchanges), command, b"\x00")
        ack = first_frame(rx, cmd=command)
        return CommandResult(command, tx, rx, (ack,), ack)


class FakeFrameTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, float | None]] = []

    def exchange(self, tx: bytes, timeout: float | None = None) -> bytes:
        self.sent.append((tx, timeout))
        frame = first_frame(tx)
        assert frame is not None
        return build_frame(frame.first_word, frame.seq, frame.cmd, b"\x00")


class FakeFrameLight:
    def __init__(self) -> None:
        self.transport = FakeFrameTransport()


class AsyncFakeRuntimeLight:
    def __init__(self, *, acknowledged: bool = True) -> None:
        self.sync = FakeRuntimeLight(acknowledged=acknowledged)

    @property
    def exchanges(self) -> list[tuple[int, bytes, float | None]]:
        return self.sync.exchanges

    async def exchange_runtime(
        self,
        command: int,
        payload: bytes = b"",
        *,
        timeout: float | None = None,
    ) -> CommandResult:
        return self.sync.exchange_runtime(command, payload, timeout=timeout)


class AsyncFakeFrameTransport:
    def __init__(self) -> None:
        self.sync = FakeFrameTransport()

    @property
    def sent(self) -> list[tuple[bytes, float | None]]:
        return self.sync.sent

    async def exchange(self, tx: bytes, timeout: float | None = None) -> bytes:
        return self.sync.exchange(tx, timeout=timeout)


class AsyncFakeFrameLight:
    def __init__(self) -> None:
        self.transport = AsyncFakeFrameTransport()


class CommandPlanningTests(unittest.TestCase):
    def test_scene_command_specs_expose_ordered_runtime_primitives(self) -> None:
        specs = scene_command_specs(
            Scene(
                obj=7,
                sleep=0,
                brightness=25,
                kelvin=5600,
                red=1,
                green=2,
                blue=3,
                hue=120,
                saturation=0.5,
                intensity=35,
            ),
            control_mode=0x01,
        )

        self.assertEqual(
            [spec.name for spec in specs],
            ["sleep", "brightness", "cct", "rgb", "hsi"],
        )
        self.assertEqual(
            [spec.command for spec in specs],
            [
                RuntimeCommand.SLEEP,
                RuntimeCommand.BRIGHTNESS,
                RuntimeCommand.CCT,
                RuntimeCommand.RGB,
                RuntimeCommand.HSI,
            ],
        )
        self.assertEqual([spec.object_id for spec in specs], [7, 7, 7, 7, 7])
        self.assertEqual([spec.payload[:3] for spec in specs], [b"\x07\x00\x01"] * 5)
        self.assertEqual(specs[1].fields, ("brightness",))
        self.assertEqual(specs[1].to_dict()["command_hex"], "0x1001")
        self.assertTrue(specs[1].to_dict()["requires_control"])

    def test_scene_frame_specs_serialize_with_supplied_sequence_range(self) -> None:
        frames = scene_frame_specs(
            Scene(obj=1, brightness=25, kelvin=5600),
            first_word=0x0301,
            start_seq=9,
        )

        self.assertIsInstance(frames[0], RuntimeFrameSpec)
        self.assertIsInstance(frames[0].command, RuntimeCommandSpec)
        self.assertEqual([frame.seq for frame in frames], [9, 10])
        self.assertEqual([frame.first_word for frame in frames], [0x0301, 0x0301])
        self.assertEqual([first_frame(frame.frame).seq for frame in frames], [9, 10])
        self.assertEqual(
            [first_frame(frame.frame).cmd for frame in frames],
            [RuntimeCommand.BRIGHTNESS, RuntimeCommand.CCT],
        )
        self.assertEqual(frames[0].to_dict()["first_word_hex"], "0x0301")
        self.assertEqual(frames[0].to_dict()["frame_hex"], frames[0].frame.hex())

    def test_scene_command_plan_groups_commands_and_frames(self) -> None:
        plan = scene_command_plan(
            Scene(obj=1, brightness=25, kelvin=5600),
            first_word=0x0301,
            start_seq=12,
        )

        self.assertIsInstance(plan, SceneCommandPlan)
        self.assertEqual(plan.start_seq, 12)
        self.assertEqual(plan.next_seq, 14)
        self.assertEqual(
            [command.name for command in plan.commands],
            ["brightness", "cct"],
        )
        self.assertEqual([frame.seq for frame in plan.frames], [12, 13])

        payload = plan.to_dict()
        self.assertEqual(payload["scene"]["brightness"], 25.0)
        self.assertEqual(payload["start_seq"], 12)
        self.assertEqual(payload["next_seq"], 14)
        self.assertEqual(
            [command["command_hex"] for command in payload["commands"]],
            ["0x1001", "0x1002"],
        )
        self.assertEqual(
            [frame["command_hex"] for frame in payload["frames"]],
            ["0x1001", "0x1002"],
        )

    def test_transition_command_plans_carry_sequence_numbers(self) -> None:
        plans = transition_command_plans(
            Scene(obj=1, brightness=10),
            Scene(obj=1, brightness=30, kelvin=5600),
            steps=2,
            first_word=0x0301,
            start_seq=21,
        )

        self.assertEqual(len(plans), 2)
        self.assertEqual([plan.start_seq for plan in plans], [21, 22])
        self.assertEqual([plan.next_seq for plan in plans], [22, 24])
        self.assertEqual(plans[-1].scene.brightness, 30.0)
        self.assertEqual(plans[-1].scene.kelvin, 5600)
        self.assertEqual(
            [frame.command.command_hex for plan in plans for frame in plan.frames],
            ["0x1001", "0x1001", "0x1002"],
        )

    def test_execute_command_plan_sends_ordered_runtime_exchanges(self) -> None:
        light = FakeRuntimeLight()
        plan = scene_command_plan(
            Scene(obj=1, brightness=25, kelvin=5600),
            control_mode=0x01,
        )

        results = execute_command_plan(light, plan, timeout=0.2)

        self.assertEqual(
            [command for command, _payload, _timeout in light.exchanges],
            [RuntimeCommand.BRIGHTNESS, RuntimeCommand.CCT],
        )
        self.assertEqual(
            [timeout for _command, _payload, timeout in light.exchanges],
            [0.2, 0.2],
        )
        self.assertEqual(light.exchanges[0][1][:3], b"\x01\x00\x01")
        self.assertTrue(all(result.acknowledged for result in results))

    def test_execute_command_plan_confirmed_fails_on_unacknowledged_result(
        self,
    ) -> None:
        light = FakeRuntimeLight(acknowledged=False)
        plan = scene_command_plan(Scene(obj=1, brightness=25))

        with self.assertRaises(UnconfirmedCommandError):
            execute_command_plan_confirmed(light, plan)

    def test_execute_transition_plans_sends_each_planned_batch(self) -> None:
        light = FakeRuntimeLight()
        plans = transition_command_plans(
            Scene(obj=1, brightness=10),
            Scene(obj=1, brightness=30, kelvin=5600),
            steps=2,
        )

        batches = execute_transition_plans(light, plans)

        self.assertEqual([len(batch) for batch in batches], [1, 2])
        self.assertEqual(
            [command for command, _payload, _timeout in light.exchanges],
            [RuntimeCommand.BRIGHTNESS, RuntimeCommand.BRIGHTNESS, RuntimeCommand.CCT],
        )

    def test_execute_frame_plan_preserves_planned_frame_bytes(self) -> None:
        light = FakeFrameLight()
        plan = scene_command_plan(
            Scene(obj=1, brightness=25, kelvin=5600),
            first_word=0x0301,
            start_seq=41,
        )

        results = execute_frame_plan(light, plan, timeout=0.2)

        self.assertEqual(
            [tx for tx, _timeout in light.transport.sent],
            [frame.frame for frame in plan.frames],
        )
        self.assertEqual(
            [timeout for _tx, timeout in light.transport.sent],
            [0.2, 0.2],
        )
        self.assertEqual([first_frame(result.tx).seq for result in results], [41, 42])
        self.assertEqual(
            [first_frame(result.tx).first_word for result in results],
            [0x0301, 0x0301],
        )
        self.assertTrue(all(result.acknowledged for result in results))

    def test_execute_serialized_frame_plan_uses_planned_frame_hex(self) -> None:
        light = FakeFrameLight()
        plan = scene_command_plan(
            Scene(obj=1, brightness=25, kelvin=5600),
            first_word=0x0301,
            start_seq=51,
        ).to_dict()

        frames = serialized_frame_commands({"command_plan": plan})
        results = execute_serialized_frame_plan(
            light,
            {"command_plan": plan},
            timeout=0.25,
        )

        self.assertEqual([command for _frame, command in frames], [0x1001, 0x1002])
        self.assertEqual(
            [tx for tx, _timeout in light.transport.sent],
            [frame for frame, _command in frames],
        )
        self.assertEqual(
            [timeout for _tx, timeout in light.transport.sent],
            [0.25, 0.25],
        )
        self.assertEqual([first_frame(result.tx).seq for result in results], [51, 52])
        self.assertTrue(all(result.acknowledged for result in results))

    def test_execute_serialized_frame_plan_confirmed_fails_closed(self) -> None:
        class TimeoutFrameTransport:
            def exchange(self, tx: bytes, timeout: float | None = None) -> bytes:
                del tx, timeout
                return b""

        class TimeoutFrameLight:
            transport = TimeoutFrameTransport()

        plan = scene_command_plan(Scene(obj=1, brightness=25)).to_dict()

        with self.assertRaises(UnconfirmedCommandError):
            execute_serialized_frame_plan_confirmed(
                TimeoutFrameLight(),
                {"command_plan": plan},
            )

    def test_execute_transition_frame_plans_preserves_batch_frames(self) -> None:
        light = FakeFrameLight()
        plans = transition_command_plans(
            Scene(obj=1, brightness=10),
            Scene(obj=1, brightness=30, kelvin=5600),
            steps=2,
            first_word=0x0301,
            start_seq=21,
        )

        batches = execute_transition_frame_plans(light, plans)

        self.assertEqual([len(batch) for batch in batches], [1, 2])
        self.assertEqual(
            [first_frame(tx).seq for tx, _timeout in light.transport.sent],
            [21, 22, 23],
        )

    def test_scene_command_specs_reject_partial_color_tuples(self) -> None:
        with self.assertRaisesRegex(ValueError, "RGB"):
            scene_command_specs(Scene(obj=1, red=255))

        with self.assertRaisesRegex(ValueError, "HSI"):
            scene_command_specs(Scene(obj=1, hue=120, saturation=0.5))


class AsyncCommandExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_async_command_plan_sends_ordered_runtime_exchanges(
        self,
    ) -> None:
        light = AsyncFakeRuntimeLight()
        plan = scene_command_plan(Scene(obj=1, brightness=25, kelvin=5600))

        results = await execute_async_command_plan(light, plan, timeout=0.2)

        self.assertEqual(
            [command for command, _payload, _timeout in light.exchanges],
            [RuntimeCommand.BRIGHTNESS, RuntimeCommand.CCT],
        )
        self.assertEqual(
            [timeout for _command, _payload, timeout in light.exchanges],
            [0.2, 0.2],
        )
        self.assertTrue(all(result.acknowledged for result in results))

    async def test_execute_async_command_plan_confirmed_fails_closed(
        self,
    ) -> None:
        light = AsyncFakeRuntimeLight(acknowledged=False)
        plan = scene_command_plan(Scene(obj=1, brightness=25))

        with self.assertRaises(UnconfirmedCommandError):
            await execute_async_command_plan_confirmed(light, plan)

    async def test_execute_async_transition_plans_sends_each_batch(self) -> None:
        light = AsyncFakeRuntimeLight()
        plans = transition_command_plans(
            Scene(obj=1, brightness=10),
            Scene(obj=1, brightness=30, kelvin=5600),
            steps=2,
        )

        batches = await execute_async_transition_plans(light, plans)

        self.assertEqual([len(batch) for batch in batches], [1, 2])
        self.assertEqual(
            [command for command, _payload, _timeout in light.exchanges],
            [RuntimeCommand.BRIGHTNESS, RuntimeCommand.BRIGHTNESS, RuntimeCommand.CCT],
        )

    async def test_execute_async_frame_plan_preserves_planned_frame_bytes(
        self,
    ) -> None:
        light = AsyncFakeFrameLight()
        plan = scene_command_plan(
            Scene(obj=1, brightness=25, kelvin=5600),
            first_word=0x0301,
            start_seq=41,
        )

        results = await execute_async_frame_plan(light, plan, timeout=0.2)

        self.assertEqual(
            [tx for tx, _timeout in light.transport.sent],
            [frame.frame for frame in plan.frames],
        )
        self.assertEqual([first_frame(result.tx).seq for result in results], [41, 42])
        self.assertTrue(all(result.acknowledged for result in results))

    async def test_execute_async_serialized_frame_plan_uses_frame_hex(self) -> None:
        light = AsyncFakeFrameLight()
        plan = scene_command_plan(
            Scene(obj=1, brightness=25, kelvin=5600),
            first_word=0x0301,
            start_seq=61,
        ).to_dict()

        results = await execute_async_serialized_frame_plan(
            light,
            {"command_plan": plan},
            timeout=0.25,
        )

        self.assertEqual(
            [first_frame(tx).seq for tx, _timeout in light.transport.sent],
            [61, 62],
        )
        self.assertEqual(
            [timeout for _tx, timeout in light.transport.sent],
            [0.25, 0.25],
        )
        self.assertTrue(all(result.acknowledged for result in results))

    async def test_execute_async_serialized_frame_plan_confirmed_fails(
        self,
    ) -> None:
        class TimeoutFrameTransport:
            async def exchange(
                self,
                tx: bytes,
                timeout: float | None = None,
            ) -> bytes:
                del tx, timeout
                return b""

        class TimeoutFrameLight:
            transport = TimeoutFrameTransport()

        plan = scene_command_plan(Scene(obj=1, brightness=25)).to_dict()

        with self.assertRaises(UnconfirmedCommandError):
            await execute_async_serialized_frame_plan_confirmed(
                TimeoutFrameLight(),
                {"command_plan": plan},
            )

    async def test_execute_async_transition_frame_plans_preserves_batches(
        self,
    ) -> None:
        light = AsyncFakeFrameLight()
        plans = transition_command_plans(
            Scene(obj=1, brightness=10),
            Scene(obj=1, brightness=30, kelvin=5600),
            steps=2,
            first_word=0x0301,
            start_seq=21,
        )

        batches = await execute_async_transition_frame_plans(light, plans)

        self.assertEqual([len(batch) for batch in batches], [1, 2])
        self.assertEqual(
            [first_frame(tx).seq for tx, _timeout in light.transport.sent],
            [21, 22, 23],
        )


if __name__ == "__main__":
    unittest.main()
