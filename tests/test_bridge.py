from __future__ import annotations

import unittest
from unittest.mock import patch

from zhiyun_light_control import (
    LightConnectionConfig,
    PersistentLightFactory,
    Scene,
    make_light_factory,
    open_light,
)
from zhiyun_light_control.client import ZhiyunLight
from zhiyun_light_control.models import CommandResult
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    build_frame,
    build_runtime_frame,
    first_frame,
)


class FakeProbe:
    def to_dict(self):
        return {"firmware": "ble-test"}


class FakeAsyncLight:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False
        self.commands: list[tuple[int, bytes, float]] = []
        self.frames: list[tuple[int, int, bytes, float]] = []
        self.updater_commands: list[tuple[int, bytes, float]] = []
        self.scenes: list[Scene] = []
        self.control_modes: list[int] = []

    async def __aenter__(self) -> FakeAsyncLight:
        self.opened = True
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        self.closed = True

    async def probe(self) -> FakeProbe:
        return FakeProbe()

    async def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        self.commands.append((cmd, payload, timeout))
        tx = build_runtime_frame(1, cmd, payload)
        rx = build_runtime_frame(1, cmd, b"\x00")
        ack = first_frame(rx, cmd=cmd)
        return CommandResult(cmd, tx, rx, (ack,), ack)

    async def exchange_frame(
        self,
        first_word: int,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        self.frames.append((first_word, cmd, payload, timeout))
        tx = build_frame(first_word, 1, cmd, payload)
        rx = build_frame(first_word, 1, cmd, b"\x00")
        ack = first_frame(rx, cmd=cmd)
        return CommandResult(cmd, tx, rx, (ack,), ack)

    async def exchange_updater(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        self.updater_commands.append((cmd, payload, timeout))
        tx = build_frame(0x0103, 1, cmd, payload)
        rx = build_frame(0x0103, 1, cmd, b"\x00")
        ack = first_frame(rx, cmd=cmd)
        return CommandResult(cmd, tx, rx, (ack,), ack)

    async def register_confirmed(self, device_id: int = 0, group_id: int = 0):
        payload = bytes((device_id, group_id, 0, 0))
        return await self.exchange_runtime(
            RuntimeCommand.REGISTER_DEFAULT_GROUP,
            payload,
        )

    async def set_brightness_confirmed(
        self,
        obj: int,
        value: float,
        *,
        control_mode: int = 0x33,
    ):
        payload = bytes((obj, 0, control_mode)) + float(value).hex().encode()
        return await self.exchange_runtime(RuntimeCommand.BRIGHTNESS, payload)

    async def apply_scene(self, scene: Scene, *, control_mode: int = 0x33):
        self.scenes.append(scene)
        self.control_modes.append(control_mode)
        return []

    async def apply_scene_confirmed(self, scene: Scene, *, control_mode: int = 0x33):
        return await self.apply_scene(scene, control_mode=control_mode)

    async def transition_scene_confirmed(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int = 0x33,
    ):
        del start, duration, easing
        return [
            await self.apply_scene(end, control_mode=control_mode)
            for _index in range(steps)
        ]


class FakeSyncContext:
    def __init__(self, name: str) -> None:
        self.name = name
        self.enter_count = 0
        self.exit_count = 0

    def __enter__(self) -> FakeSyncContext:
        self.enter_count += 1
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.exit_count += 1


class BridgeFactoryTests(unittest.TestCase):
    def test_usb_factory_builds_sync_usb_client_without_opening_port(self) -> None:
        factory = make_light_factory(
            LightConnectionConfig(
                transport="usb",
                port="/dev/cu.test",
                timeout=2.0,
                usb_lock_timeout=0.25,
            )
        )

        light = factory()

        self.assertIsInstance(light, ZhiyunLight)
        self.assertEqual(light.transport.port, "/dev/cu.test")
        self.assertEqual(light.transport.timeout, 2.0)
        self.assertEqual(light.transport.lock_timeout, 0.25)

    def test_open_light_returns_configured_context_manager(self) -> None:
        context = open_light(
            LightConnectionConfig(
                transport="usb",
                port="/dev/cu.test",
                timeout=2.0,
                usb_lock_timeout=0.25,
            )
        )

        self.assertIsInstance(context, ZhiyunLight)
        self.assertEqual(context.transport.port, "/dev/cu.test")
        self.assertEqual(context.transport.timeout, 2.0)

    def test_ble_factory_adapts_async_client_to_sync_bridge_interface(self) -> None:
        fake = FakeAsyncLight()
        scene = Scene(obj=1, brightness=20)

        with patch(
            "zhiyun_light_control.bridge.AsyncZhiyunLight.isolated_ble",
            return_value=fake,
        ) as make_ble:
            factory = make_light_factory(
                LightConnectionConfig(
                    transport="ble",
                    address="AA:BB",
                    name_contains="MOLUS",
                    timeout=2.5,
                    ble_python="python-test",
                )
            )
            with factory() as light:
                probe = light.probe()
                result = light.exchange_runtime(0x1001, b"\x01", timeout=0.25)
                frame_result = light.exchange_frame(
                    0x0100,
                    0x2001,
                    timeout=0.35,
                )
                updater_result = light.exchange_updater(0x1300, timeout=0.45)
                scene_results = light.apply_scene(scene, control_mode=0x01)
                register_result = light.register_confirmed()
                brightness_result = light.set_brightness_confirmed(
                    1,
                    35,
                    control_mode=0x01,
                )
                confirmed_scene = light.apply_scene_confirmed(
                    scene,
                    control_mode=0x01,
                )
                transition_batches = light.transition_scene_confirmed(
                    Scene(obj=1, brightness=10),
                    Scene(obj=1, brightness=20),
                    steps=2,
                    duration=0,
                    control_mode=0x01,
                )

        make_ble.assert_called_once_with(
            address="AA:BB",
            name_contains="MOLUS",
            profile="direct",
            service_uuid=None,
            write_uuid=None,
            notify_uuid=None,
            timeout=2.5,
            python="python-test",
        )
        self.assertTrue(fake.opened)
        self.assertTrue(fake.closed)
        self.assertEqual(probe.to_dict()["firmware"], "ble-test")
        self.assertEqual(result.command, 0x1001)
        self.assertEqual(frame_result.command, 0x2001)
        self.assertEqual(updater_result.command, 0x1300)
        self.assertEqual(register_result.command, RuntimeCommand.REGISTER_DEFAULT_GROUP)
        self.assertEqual(brightness_result.command, RuntimeCommand.BRIGHTNESS)
        self.assertEqual(
            [cmd for cmd, _payload, _timeout in fake.commands],
            [
                0x1001,
                RuntimeCommand.REGISTER_DEFAULT_GROUP,
                RuntimeCommand.BRIGHTNESS,
            ],
        )
        self.assertEqual(fake.commands[0], (0x1001, b"\x01", 0.25))
        self.assertEqual(fake.frames, [(0x0100, 0x2001, b"", 0.35)])
        self.assertEqual(fake.updater_commands, [(0x1300, b"", 0.45)])
        self.assertEqual(fake.scenes, [scene, scene, scene, scene])
        self.assertEqual(fake.control_modes, [0x01, 0x01, 0x01, 0x01])
        self.assertEqual(scene_results, [])
        self.assertEqual(confirmed_scene, [])
        self.assertEqual(transition_batches, [[], []])

    def test_ble_factory_can_opt_into_direct_client(self) -> None:
        fake = FakeAsyncLight()

        with (
            patch(
                "zhiyun_light_control.bridge.AsyncZhiyunLight.isolated_ble"
            ) as isolated_ble,
            patch(
                "zhiyun_light_control.bridge.AsyncZhiyunLight.ble",
                return_value=fake,
            ) as direct_ble,
        ):
            factory = make_light_factory(
                LightConnectionConfig(
                    transport="ble",
                    address="AA:BB",
                    name_contains="MOLUS",
                    timeout=2.5,
                    ble_in_process=True,
                )
            )
            with factory() as light:
                probe = light.probe()

        isolated_ble.assert_not_called()
        direct_ble.assert_called_once_with(
            address="AA:BB",
            name_contains="MOLUS",
            profile="direct",
            service_uuid=None,
            write_uuid=None,
            notify_uuid=None,
            timeout=2.5,
        )
        self.assertEqual(probe.to_dict()["firmware"], "ble-test")

    def test_ble_factory_passes_profile_and_custom_characteristics(self) -> None:
        fake = FakeAsyncLight()

        with patch(
            "zhiyun_light_control.bridge.AsyncZhiyunLight.isolated_ble",
            return_value=fake,
        ) as make_ble:
            factory = make_light_factory(
                LightConnectionConfig(
                    transport="ble",
                    ble_profile="legacy",
                    ble_service_uuid="service-test",
                    ble_write_uuid="write-test",
                    ble_notify_uuid="notify-test",
                )
            )
            with factory() as light:
                light.probe()

        make_ble.assert_called_once_with(
            address=None,
            name_contains=None,
            profile="legacy",
            service_uuid="service-test",
            write_uuid="write-test",
            notify_uuid="notify-test",
            timeout=1.5,
            python=None,
        )

    def test_ble_factory_can_use_macos_app_backend(self) -> None:
        fake = FakeAsyncLight()

        with (
            patch(
                "zhiyun_light_control.bridge.AsyncZhiyunLight.isolated_ble"
            ) as isolated_ble,
            patch(
                "zhiyun_light_control.bridge.AsyncZhiyunLight.macos_ble_app",
                return_value=fake,
            ) as macos_ble,
        ):
            factory = make_light_factory(
                LightConnectionConfig(
                    transport="ble",
                    address="UUID-1",
                    ble_backend="macos-app",
                    ble_profile="legacy",
                )
            )
            with factory() as light:
                light.probe()

        isolated_ble.assert_not_called()
        macos_ble.assert_called_once_with(
            address="UUID-1",
            name_contains=None,
            profile="legacy",
            service_uuid=None,
            write_uuid=None,
            notify_uuid=None,
            timeout=1.5,
        )

    def test_persistent_factory_reuses_open_light_until_closed(self) -> None:
        contexts: list[FakeSyncContext] = []

        def factory() -> FakeSyncContext:
            context = FakeSyncContext(f"light-{len(contexts)}")
            contexts.append(context)
            return context

        persistent = PersistentLightFactory(factory)
        with persistent() as first:
            self.assertEqual(first.name, "light-0")
        with persistent() as second:
            self.assertIs(second, first)

        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0].enter_count, 1)
        self.assertEqual(contexts[0].exit_count, 0)

        persistent.close()

        self.assertEqual(contexts[0].exit_count, 1)

    def test_persistent_factory_resets_connection_after_exception(self) -> None:
        contexts: list[FakeSyncContext] = []

        def factory() -> FakeSyncContext:
            context = FakeSyncContext(f"light-{len(contexts)}")
            contexts.append(context)
            return context

        persistent = PersistentLightFactory(factory)
        with self.assertRaisesRegex(RuntimeError, "boom"), persistent():
            raise RuntimeError("boom")
        with persistent() as light:
            self.assertEqual(light.name, "light-1")

        self.assertEqual(len(contexts), 2)
        self.assertEqual(contexts[0].exit_count, 1)
        persistent.close()

    def test_config_can_request_persistent_factory(self) -> None:
        factory = make_light_factory(
            LightConnectionConfig(
                transport="usb",
                port="/dev/cu.test",
                persistent=True,
            )
        )

        self.assertIsInstance(factory, PersistentLightFactory)


if __name__ == "__main__":
    unittest.main()
