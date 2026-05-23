from __future__ import annotations

import unittest
from unittest.mock import patch

from zhiyun_light_control import LightConnectionConfig, Scene, make_light_factory
from zhiyun_light_control.client import ZhiyunLight
from zhiyun_light_control.models import CommandResult
from zhiyun_light_control.protocol import build_runtime_frame, first_frame


class FakeProbe:
    def to_dict(self):
        return {"firmware": "ble-test"}


class FakeAsyncLight:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False
        self.commands: list[tuple[int, bytes, float]] = []
        self.scenes: list[Scene] = []

    async def __aenter__(self) -> "FakeAsyncLight":
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

    async def apply_scene(self, scene: Scene):
        self.scenes.append(scene)
        return []


class BridgeFactoryTests(unittest.TestCase):
    def test_usb_factory_builds_sync_usb_client_without_opening_port(self) -> None:
        factory = make_light_factory(
            LightConnectionConfig(
                transport="usb",
                port="/dev/cu.test",
                timeout=2.0,
            )
        )

        light = factory()

        self.assertIsInstance(light, ZhiyunLight)
        self.assertEqual(light.transport.port, "/dev/cu.test")
        self.assertEqual(light.transport.timeout, 2.0)

    def test_ble_factory_adapts_async_client_to_sync_bridge_interface(self) -> None:
        fake = FakeAsyncLight()
        scene = Scene(obj=1, brightness=20)

        with patch(
            "zhiyun_light_control.bridge.AsyncZhiyunLight.ble",
            return_value=fake,
        ) as make_ble:
            factory = make_light_factory(
                LightConnectionConfig(
                    transport="ble",
                    address="AA:BB",
                    name_contains="MOLUS",
                    timeout=2.5,
                )
            )
            with factory() as light:
                probe = light.probe()
                result = light.exchange_runtime(0x1001, b"\x01", timeout=0.25)
                scene_results = light.apply_scene(scene)

        make_ble.assert_called_once_with(
            address="AA:BB",
            name_contains="MOLUS",
            timeout=2.5,
        )
        self.assertTrue(fake.opened)
        self.assertTrue(fake.closed)
        self.assertEqual(probe.to_dict()["firmware"], "ble-test")
        self.assertEqual(result.command, 0x1001)
        self.assertEqual(fake.commands, [(0x1001, b"\x01", 0.25)])
        self.assertEqual(fake.scenes, [scene])
        self.assertEqual(scene_results, [])


if __name__ == "__main__":
    unittest.main()
