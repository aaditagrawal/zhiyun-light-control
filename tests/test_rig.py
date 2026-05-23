from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zhiyun_light_control import (
    AsyncLightRig,
    CommandResult,
    LightConnectionConfig,
    LightFixture,
    LightRig,
    RigConfigError,
    Scene,
    async_rig_from_mapping,
    fixture_from_mapping,
    load_rig,
    rig_from_mapping,
)
from zhiyun_light_control.protocol import (
    RUNTIME_TYPE,
    RuntimeCommand,
    build_frame,
    first_frame,
)


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
        self.control_modes: list[int] = []
        self.payloads: list[tuple[int, bytes]] = []

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
        self.scenes.append(scene)
        self.control_modes.append(control_mode)
        return [_result(RuntimeCommand.BRIGHTNESS, acknowledged=self.acknowledged)]

    def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        del timeout
        self.payloads.append((cmd, payload))
        payload_by_cmd = {
            RuntimeCommand.DEVICE_INFO: f"{self.name}\x00pl103\x00".encode(),
            RuntimeCommand.FIRMWARE: b"1.6.4\x00",
            RuntimeCommand.VOLTAGE: b"\x65",
            RuntimeCommand.DEVICE_ID: b"\x01\x00",
        }
        return _result(
            cmd,
            payload=payload_by_cmd.get(cmd, b"\x00"),
            acknowledged=self.acknowledged,
        )


class AsyncFakeLight:
    def __init__(self, name: str, *, acknowledged: bool = True) -> None:
        self.name = name
        self.acknowledged = acknowledged
        self.scenes: list[Scene] = []
        self.control_modes: list[int] = []
        self.payloads: list[tuple[int, bytes]] = []

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
        self.scenes.append(scene)
        self.control_modes.append(control_mode)
        return [_result(RuntimeCommand.BRIGHTNESS, acknowledged=self.acknowledged)]

    async def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        del timeout
        self.payloads.append((cmd, payload))
        payload_by_cmd = {
            RuntimeCommand.DEVICE_INFO: f"{self.name}\x00pl103\x00".encode(),
            RuntimeCommand.FIRMWARE: b"1.6.4\x00",
            RuntimeCommand.VOLTAGE: b"\x65",
            RuntimeCommand.DEVICE_ID: b"\x01\x00",
        }
        return _result(
            cmd,
            payload=payload_by_cmd.get(cmd, b"\x00"),
            acknowledged=self.acknowledged,
        )


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

    def test_rig_from_mapping_loads_fixtures_presets_and_cues(self) -> None:
        key = FakeLight("key")
        rig = rig_from_mapping(
            {
                "fixtures": {
                    "key": {
                        "transport": "usb",
                        "port": "/dev/cu.key",
                        "obj": 2,
                        "tags": ["set"],
                    }
                },
                "presets": {"scenes": {"look": {"brightness": 22}}},
                "cues": {"cues": {"intro": {"steps": [{"preset": "look"}]}}},
                "control_mode": "0x01",
                "require_acknowledged": True,
            },
            light_factories={"key": FakeFactory(key)},
        )

        response = rig.apply_preset("key", "look")

        self.assertTrue(rig.require_acknowledged)
        self.assertEqual(rig.control_mode, 0x01)
        self.assertEqual(rig.fixture("key").config.port, "/dev/cu.key")
        self.assertEqual(rig.fixture("key").tags, ("set",))
        self.assertEqual(response["scene"]["brightness"], 22.0)
        self.assertEqual(key.scenes[0].obj, 2)
        self.assertEqual(key.control_modes, [0x01])
        self.assertEqual(rig.to_dict()["presets"]["scenes"]["look"]["brightness"], 22.0)

    def test_load_rig_reads_json_config(self) -> None:
        key = FakeLight("key")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rig.json"
            path.write_text(
                json.dumps(
                    {
                        "fixtures": [
                            {
                                "name": "key",
                                "transport": "usb",
                                "port": "/dev/cu.key",
                            }
                        ],
                        "presets": {"key": {"brightness": 30}},
                    }
                ),
                encoding="utf-8",
            )

            rig = load_rig(path, light_factories={"key": FakeFactory(key)})

        self.assertEqual(rig.fixture_names(), ("key",))
        self.assertEqual(rig.fixture("key").config.port, "/dev/cu.key")
        response = rig.controller("key").apply_preset("key")
        self.assertEqual(response["scene"]["brightness"], 30.0)

    def test_rig_from_mapping_requires_fixtures(self) -> None:
        with self.assertRaisesRegex(RigConfigError, "fixtures"):
            rig_from_mapping({"presets": {}})

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

    def test_status_all_returns_per_fixture_readiness_evidence(self) -> None:
        key = FakeLight("key")
        fill = FakeLight("fill", acknowledged=False)
        rig = LightRig(
            [
                LightFixture("key"),
                LightFixture("fill"),
            ],
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )

        response = rig.status_all(stop_on_error=True)

        self.assertFalse(response["applied"])
        self.assertTrue(response["stopped"])
        self.assertEqual(tuple(response["fixtures"]), ("key", "fill"))
        self.assertTrue(response["fixtures"]["key"]["connection_confirmed"])
        self.assertFalse(response["fixtures"]["fill"]["connection_confirmed"])
        self.assertEqual(response["fixtures"]["key"]["status"]["firmware"], "1.6.4")

    def test_validate_all_uses_fixture_object_ids(self) -> None:
        key = FakeLight("key")
        rig = LightRig(
            [LightFixture("key", obj=7)],
            light_factories={"key": FakeFactory(key)},
        )

        response = rig.validate_all(include_object_reads=True)

        self.assertTrue(response["applied"])
        self.assertEqual(response["reason"], "acknowledged")
        self.assertTrue(response["fixtures"]["key"]["ok"])
        payloads = dict(key.payloads)
        self.assertEqual(payloads[RuntimeCommand.BRIGHTNESS][:2], b"\x07\x00")

    def test_readiness_all_includes_fixture_state(self) -> None:
        key = FakeLight("key")
        rig = LightRig(
            [LightFixture("key")],
            light_factories={"key": FakeFactory(key)},
        )
        rig.apply_all({"brightness": 35})

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": True, "selected_port": None, "ports": []},
                "ble": {"macos_status": None, "scan": None},
            },
        ):
            response = rig.readiness_all(allow_control=True)

        readiness = response["fixtures"]["key"]["readiness"]
        self.assertTrue(response["applied"])
        self.assertTrue(readiness["ready_for"]["read_status"])
        self.assertTrue(readiness["ready_for"]["control_requests"])
        self.assertEqual(readiness["state"]["version"], 1)

    def test_snapshot_all_includes_capabilities_readiness_and_fixture_metadata(
        self,
    ) -> None:
        key = FakeLight("key")
        rig = rig_from_mapping(
            {
                "fixtures": {
                    "key": {
                        "transport": "usb",
                        "port": "/dev/cu.key",
                        "tags": ["set"],
                    }
                },
                "presets": {"scenes": {"look": {"brightness": 30}}},
                "cues": {"cues": {"intro": {"steps": [{"preset": "look"}]}}},
            },
            light_factories={"key": FakeFactory(key)},
        )
        rig.apply_preset("key", "look")

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {
                    "available": True,
                    "selected_port": "/dev/cu.key",
                    "ports": [{"path": "/dev/cu.key", "selected": True}],
                },
                "ble": {"macos_status": None, "scan": None},
            },
        ):
            response = rig.snapshot_all(allow_control=True, include_ble_status=True)

        snapshot = response["fixtures"]["key"]["snapshot"]
        summary = snapshot["summary"]
        manifest = snapshot["payloads"]["manifest"]
        capabilities = snapshot["payloads"]["capabilities"]
        ready = snapshot["payloads"]["ready"]
        self.assertTrue(response["applied"])
        self.assertEqual(response["reason"], "acknowledged")
        self.assertEqual(summary["selected_usb_port"], "/dev/cu.key")
        self.assertTrue(summary["ready_for"]["read_status"])
        self.assertTrue(summary["ready_for"]["confirmed_control"])
        self.assertEqual(manifest["presets"], ["look"])
        self.assertEqual(manifest["cues"], ["intro"])
        self.assertIn("sleep", [item["name"] for item in capabilities["primitives"]])
        self.assertEqual(ready["state"]["version"], 1)
        self.assertEqual(rig.capabilities("key")["reason"], "available")

    def test_duplicate_fixture_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate"):
            LightRig([{"name": "key"}, {"name": "key"}])


class AsyncLightRigTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_rig_from_mapping_loads_presets(self) -> None:
        key = AsyncFakeLight("key")
        rig = async_rig_from_mapping(
            {
                "fixtures": {
                    "key": {
                        "transport": "ble",
                        "name_contains": "KEY",
                        "obj": 4,
                    }
                },
                "presets": {"scenes": {"look": {"brightness": 55}}},
                "control_mode": "0x01",
                "require_acknowledged": True,
            },
            light_factories={"key": FakeFactory(key)},
        )

        response = await rig.apply_preset("key", "look")

        self.assertTrue(rig.require_acknowledged)
        self.assertEqual(rig.control_mode, 0x01)
        self.assertEqual(rig.fixture("key").config.transport, "ble")
        self.assertEqual(response["scene"]["brightness"], 55.0)
        self.assertEqual(key.scenes[0].obj, 4)
        self.assertEqual(key.control_modes, [0x01])

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

    async def test_async_status_all_reports_each_fixture(self) -> None:
        key = AsyncFakeLight("key")
        rig = AsyncLightRig(
            [LightFixture("key")],
            light_factories={"key": FakeFactory(key)},
        )

        response = await rig.status_all()

        self.assertTrue(response["applied"])
        self.assertTrue(response["fixtures"]["key"]["connection_confirmed"])
        self.assertEqual(response["fixtures"]["key"]["status"]["firmware"], "1.6.4")

    async def test_async_validate_all_uses_fixture_object_ids(self) -> None:
        key = AsyncFakeLight("key")
        rig = AsyncLightRig(
            [LightFixture("key", obj=9)],
            light_factories={"key": FakeFactory(key)},
        )

        response = await rig.validate_all(include_object_reads=True)

        self.assertTrue(response["applied"])
        payloads = dict(key.payloads)
        self.assertEqual(payloads[RuntimeCommand.BRIGHTNESS][:2], b"\x09\x00")

    async def test_async_readiness_all_uses_fixture_state(self) -> None:
        key = AsyncFakeLight("key")
        rig = AsyncLightRig(
            [LightFixture("key")],
            light_factories={"key": FakeFactory(key)},
        )
        await rig.apply_all({"brightness": 40})

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {"macos_status": None, "scan": None},
            },
        ):
            response = await rig.readiness_all(allow_control=True)

        readiness = response["fixtures"]["key"]["readiness"]
        self.assertTrue(response["applied"])
        self.assertTrue(readiness["ready_for"]["read_status"])
        self.assertEqual(readiness["state"]["version"], 1)

    async def test_async_snapshot_all_returns_per_fixture_integration_payload(
        self,
    ) -> None:
        key = AsyncFakeLight("key")
        rig = async_rig_from_mapping(
            {
                "fixtures": {
                    "key": {
                        "transport": "ble",
                        "name_contains": "KEY",
                    }
                },
                "presets": {"scenes": {"look": {"brightness": 40}}},
            },
            light_factories={"key": FakeFactory(key)},
        )
        await rig.apply_preset("key", "look")

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {"macos_status": None, "scan": {"ok": True, "devices": []}},
            },
        ):
            response = await rig.snapshot_all(allow_control=True, include_ble=True)

        snapshot = response["fixtures"]["key"]["snapshot"]
        summary = snapshot["summary"]
        manifest = snapshot["payloads"]["manifest"]
        self.assertTrue(response["applied"])
        self.assertEqual(summary["transport"], "ble")
        self.assertTrue(summary["ready_for"]["read_status"])
        self.assertTrue(summary["ready_for"]["confirmed_control"])
        self.assertEqual(manifest["presets"], ["look"])
        self.assertEqual(rig.capabilities("key")["reason"], "available")


def _result(
    cmd: int,
    *,
    payload: bytes = b"\x00",
    acknowledged: bool = True,
) -> CommandResult:
    tx = build_frame(RUNTIME_TYPE, 1, cmd)
    if not acknowledged:
        return CommandResult(cmd, tx, b"", (), None)
    rx = build_frame(RUNTIME_TYPE, 1, cmd, payload)
    ack = first_frame(rx, cmd=cmd)
    return CommandResult(cmd, tx, rx, (ack,), ack)


if __name__ == "__main__":
    unittest.main()
