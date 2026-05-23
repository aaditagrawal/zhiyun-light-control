from __future__ import annotations

import unittest

from zhiyun_light_control import (
    AsyncLightRig,
    CommandResult,
    LightConnectionConfig,
    LightFixture,
    LightRig,
    Scene,
    fixture_from_mapping,
)
from zhiyun_light_control.protocol import RuntimeCommand, build_frame, first_frame


class FakeProbe:
    def __init__(self, name: str) -> None:
        self.name = name

    def to_dict(self) -> dict[str, object]:
        return {"device_identifier": self.name, "firmware": "1.6.4"}


class FakeLight:
    def __init__(self, name: str, *, acknowledged: bool = True) -> None:
        self.name = name
        self.acknowledged = acknowledged
        self.scenes: list[Scene] = []

    def __enter__(self) -> FakeLight:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def probe(self) -> FakeProbe:
        return FakeProbe(self.name)

    def apply_scene(
        self,
        scene: Scene,
        *,
        control_mode: int = 0x33,
    ) -> list[CommandResult]:
        del control_mode
        self.scenes.append(scene)
        return [_result(RuntimeCommand.BRIGHTNESS, acknowledged=self.acknowledged)]


class AsyncFakeLight:
    def __init__(self, name: str, *, acknowledged: bool = True) -> None:
        self.name = name
        self.acknowledged = acknowledged
        self.scenes: list[Scene] = []

    async def __aenter__(self) -> AsyncFakeLight:
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return

    async def probe(self) -> FakeProbe:
        return FakeProbe(self.name)

    async def apply_scene(
        self,
        scene: Scene,
        *,
        control_mode: int = 0x33,
    ) -> list[CommandResult]:
        del control_mode
        self.scenes.append(scene)
        return [_result(RuntimeCommand.BRIGHTNESS, acknowledged=self.acknowledged)]


class FakeFactory:
    def __init__(self, light: object) -> None:
        self.light = light

    def __call__(self) -> object:
        return self.light


class LightRigTests(unittest.TestCase):
    def test_fixture_from_mapping_accepts_inline_config(self) -> None:
        fixture = fixture_from_mapping(
            {
                "name": "key",
                "transport": "usb",
                "port": "/dev/cu.test",
                "obj": 2,
                "tags": ["stage-left", "warm"],
            }
        )

        self.assertEqual(fixture.name, "key")
        self.assertEqual(fixture.config.transport, "usb")
        self.assertEqual(fixture.config.port, "/dev/cu.test")
        self.assertEqual(fixture.obj, 2)
        self.assertEqual(fixture.tags, ("stage-left", "warm"))

    def test_apply_all_uses_fixture_object_defaults_and_tracks_state(self) -> None:
        key = FakeLight("key")
        fill = FakeLight("fill")
        rig = LightRig(
            [
                LightFixture("key", obj=1),
                LightFixture("fill", obj=2),
            ],
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )

        response = rig.apply_all({"brightness": 35}, tag=None)

        self.assertTrue(response["applied"])
        self.assertEqual(key.scenes[0].obj, 1)
        self.assertEqual(fill.scenes[0].obj, 2)
        self.assertEqual(fill.scenes[0].brightness, 35)
        state = rig.state_snapshot()["fixtures"]
        self.assertEqual(state["key"]["state"]["scene"]["brightness"], 35)
        self.assertEqual(state["fill"]["state"]["scene"]["obj"], 2)

    def test_apply_scene_map_can_stop_on_first_unconfirmed_fixture(self) -> None:
        key = FakeLight("key", acknowledged=False)
        fill = FakeLight("fill")
        rig = LightRig(
            [
                {"name": "key", "obj": 1},
                {"name": "fill", "obj": 2},
            ],
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )

        response = rig.apply_scene_map(
            {
                "key": {"brightness": 10},
                "fill": {"brightness": 20},
            },
            stop_on_unconfirmed=True,
        )

        self.assertFalse(response["applied"])
        self.assertTrue(response["stopped"])
        self.assertEqual(response["reason"], "key:sent_no_response")
        self.assertEqual(len(key.scenes), 1)
        self.assertEqual(fill.scenes, [])

    def test_tag_selection_and_probe_all(self) -> None:
        key = FakeLight("key")
        fill = FakeLight("fill")
        rig = LightRig(
            [
                LightFixture("key", tags=("stage",)),
                LightFixture("fill", tags=("desk",)),
            ],
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )

        response = rig.probe_all(tag="stage")

        self.assertTrue(response["applied"])
        self.assertEqual(tuple(response["fixtures"]), ("key",))
        self.assertEqual(response["fixtures"]["key"]["probe"]["firmware"], "1.6.4")

    def test_duplicate_fixture_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            LightRig([{"name": "key"}, {"name": "key"}])


class AsyncLightRigTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_apply_all_uses_fixture_object_defaults(self) -> None:
        key = AsyncFakeLight("key")
        fill = AsyncFakeLight("fill")
        rig = AsyncLightRig(
            [
                LightFixture(
                    "key",
                    LightConnectionConfig(transport="ble", name_contains="KEY"),
                    obj=1,
                ),
                LightFixture(
                    "fill",
                    LightConnectionConfig(transport="ble", name_contains="FILL"),
                    obj=2,
                ),
            ],
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )

        response = await rig.apply_all({"brightness": 45})

        self.assertTrue(response["applied"])
        self.assertEqual(key.scenes[0].obj, 1)
        self.assertEqual(fill.scenes[0].obj, 2)
        self.assertEqual(fill.scenes[0].brightness, 45)

    async def test_async_blackout_targets_tagged_fixtures(self) -> None:
        key = AsyncFakeLight("key")
        fill = AsyncFakeLight("fill")
        rig = AsyncLightRig(
            [
                LightFixture("key", tags=("stage",)),
                LightFixture("fill", tags=("desk",)),
            ],
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )

        response = await rig.blackout(tag="stage")

        self.assertTrue(response["applied"])
        self.assertEqual(key.scenes[0].sleep, 1)
        self.assertEqual(fill.scenes, [])


def _result(cmd: int, *, acknowledged: bool = True) -> CommandResult:
    tx = build_frame(0x0001, 1, cmd)
    if not acknowledged:
        return CommandResult(cmd, tx, b"", (), None)
    rx = build_frame(0x0001, 1, cmd, b"\x00")
    ack = first_frame(rx, cmd=cmd)
    return CommandResult(cmd, tx, rx, (ack,), ack)


if __name__ == "__main__":
    unittest.main()
