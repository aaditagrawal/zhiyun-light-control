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
    LightSetupProfile,
    RigConfigError,
    RigNotReady,
    Scene,
    ScenePresetLibrary,
    SetupProfileNotReady,
    async_rig_from_json,
    async_rig_from_mapping,
    fixture_from_mapping,
    load_rig,
    rig_from_json,
    rig_from_mapping,
    rig_profile_bundle_mapping,
    rig_setup_profiles_from_report,
    rig_to_json,
    save_light_setup_profile,
    save_rig,
    save_rig_profile_bundle,
    serialized_plan_bundle,
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
        self.primitive_calls: list[tuple[str, int, object, int]] = []
        self.prebuilt_frames: list[bytes] = []
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

    def set_brightness(
        self,
        obj: int,
        value: float,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("brightness", obj, value, control_mode))
        return _result(RuntimeCommand.BRIGHTNESS, acknowledged=self.acknowledged)

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
        self.primitive_calls.append(
            ("rgb", obj, (red, green, blue), control_mode)
        )
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


class AsyncFakeLight:
    def __init__(self, name: str, *, acknowledged: bool = True) -> None:
        self.name = name
        self.acknowledged = acknowledged
        self.scenes: list[Scene] = []
        self.control_modes: list[int] = []
        self.primitive_calls: list[tuple[str, int, object, int]] = []
        self.prebuilt_frames: list[bytes] = []
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

    async def set_brightness(
        self,
        obj: int,
        value: float,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("brightness", obj, value, control_mode))
        return _result(RuntimeCommand.BRIGHTNESS, acknowledged=self.acknowledged)

    async def set_cct(
        self,
        obj: int,
        kelvin: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("cct", obj, kelvin, control_mode))
        return _result(RuntimeCommand.CCT, acknowledged=self.acknowledged)

    async def set_sleep(
        self,
        obj: int,
        value: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("sleep", obj, value, control_mode))
        return _result(RuntimeCommand.SLEEP, acknowledged=self.acknowledged)

    async def set_rgb(
        self,
        obj: int,
        red: int,
        green: int,
        blue: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(
            ("rgb", obj, (red, green, blue), control_mode)
        )
        return _result(RuntimeCommand.RGB, acknowledged=self.acknowledged)

    async def set_hsi(
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

    async def exchange_prebuilt_frame(
        self,
        frame: bytes,
        command: int,
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        del timeout
        self.prebuilt_frames.append(frame)
        return _prebuilt_result(
            frame,
            command,
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

    def test_fixture_from_mapping_accepts_inline_setup_profile(self) -> None:
        fixture = fixture_from_mapping(
            {
                "name": "key",
                "profile": setup_profile().to_dict(),
                "obj": 3,
                "tags": ["profiled"],
            }
        )

        self.assertEqual(fixture.config.port, "/dev/cu.usbmodem21301")
        self.assertIsNotNone(fixture.setup_profile)
        self.assertTrue(fixture.setup_profile.ready("read_status"))
        self.assertFalse(fixture.setup_profile.ready("control_writes"))
        self.assertEqual(fixture.obj, 3)
        self.assertEqual(fixture.tags, ("profiled",))

    def test_fixture_can_be_created_from_setup_profile(self) -> None:
        profile = setup_profile(
            LightConnectionConfig.ble(
                address="UUID-1",
                backend="worker",
                profile="legacy",
            )
        )

        fixture = LightFixture.from_setup_profile(
            "rim",
            profile,
            obj=5,
            tags=("stage-right",),
        )

        self.assertEqual(fixture.name, "rim")
        self.assertEqual(fixture.config.transport, "ble")
        self.assertEqual(fixture.config.address, "UUID-1")
        self.assertEqual(fixture.config.ble_profile, "legacy")
        self.assertEqual(fixture.obj, 5)
        self.assertEqual(fixture.tags, ("stage-right",))
        self.assertIs(fixture.setup_profile, profile)

    def test_fixture_rejects_mixed_config_and_profile(self) -> None:
        with self.assertRaisesRegex(ValueError, "config and setup profile"):
            fixture_from_mapping(
                {
                    "name": "key",
                    "profile": setup_profile().to_dict(),
                    "config": {"transport": "usb", "port": "/dev/cu.other"},
                }
            )

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

    def test_rig_json_helpers_round_trip_profiled_fixture_groups(self) -> None:
        key = FakeLight("key")
        rig = LightRig(
            [
                LightFixture.from_setup_profile(
                    "key",
                    setup_profile(control_writes=True),
                    obj=2,
                    tags=("set",),
                )
            ],
            light_factories={"key": FakeFactory(key)},
            preset_library=ScenePresetLibrary.from_mapping(
                {"scenes": {"look": {"brightness": 25}}}
            ),
            require_acknowledged=True,
            require_setup_profile_controls=True,
        )

        text = rig.to_json()
        restored = rig_from_json(text, light_factories={"key": FakeFactory(key)})
        compact = rig_to_json(restored, indent=None)
        mapped = rig_to_json({"fixtures": [{"name": "fill"}]}, indent=None)

        self.assertEqual(restored.fixture_names(), ("key",))
        self.assertTrue(restored.require_acknowledged)
        self.assertTrue(restored.require_setup_profile_controls)
        self.assertEqual(restored.fixture("key").obj, 2)
        self.assertEqual(restored.fixture("key").tags, ("set",))
        self.assertTrue(restored.require_setup_profile("key", "control_writes").ok)
        self.assertEqual(
            restored.to_dict()["presets"]["scenes"]["look"]["brightness"],
            25.0,
        )
        self.assertIn('"require_setup_profile_controls": true', compact)
        self.assertEqual(mapped, '{"fixtures": [{"name": "fill"}]}')

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rig.json"
            restored.save(path)
            saved = load_rig(path, light_factories={"key": FakeFactory(key)})
            alt_path = Path(directory) / "rig-alt.json"
            save_rig(saved, alt_path)
            saved_again = load_rig(alt_path)

        self.assertTrue(saved_again.require_setup_profile_controls)
        self.assertEqual(
            saved_again.fixture("key").config.port,
            "/dev/cu.usbmodem21301",
        )
        self.assertIsNotNone(saved_again.fixture("key").setup_profile)

        with self.assertRaisesRegex(RigConfigError, "rig JSON"):
            rig_from_json("[]")

    def test_load_rig_resolves_relative_fixture_profile_path(self) -> None:
        key = FakeLight("key")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile_path = root / "key-profile.json"
            save_light_setup_profile(setup_profile(), profile_path)
            rig_path = root / "rig.json"
            rig_path.write_text(
                json.dumps(
                    {
                        "fixtures": {
                            "key": {
                                "profile_path": "key-profile.json",
                                "obj": 4,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            rig = load_rig(rig_path, light_factories={"key": FakeFactory(key)})

        fixture = rig.fixture("key")
        self.assertEqual(fixture.config.port, "/dev/cu.usbmodem21301")
        self.assertEqual(fixture.obj, 4)
        self.assertIsNotNone(fixture.setup_profile)
        self.assertTrue(rig.require_setup_profile("key", "read_status").ok)
        with self.assertRaises(SetupProfileNotReady):
            rig.require_setup_profile("key", "control_writes")
        self.assertIn("setup_profile", fixture.to_dict())

    def test_rig_integration_carries_libraries_for_planning(self) -> None:
        key = FakeLight("key")
        rig = rig_from_mapping(
            {
                "fixtures": [
                    {
                        "name": "key",
                        "obj": 2,
                        "profile": setup_profile().to_dict(),
                    }
                ],
                "presets": {"scenes": {"look": {"brightness": 30}}},
                "cues": {"intro": {"steps": [{"preset": "look"}]}},
            },
            light_factories={"key": FakeFactory(key)},
        )

        integration = rig.integration("key", require_setup_profile_controls=True)
        plan = integration.plan_named_cue("intro", start_seq=5)
        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": True, "selected_port": "/dev/cu.usbmodem21301"},
                "ble": {"macos_status": None, "scan": None},
            },
        ):
            snapshot = rig.snapshot("key")

        self.assertEqual(integration.manifest()["presets"], ["look"])
        self.assertEqual(integration.capabilities()["cues"], ["intro"])
        self.assertTrue(integration.require_setup_profile_controls)
        self.assertTrue(integration.setup_profile_primitive_ready("status"))
        self.assertFalse(integration.setup_profile_primitive_ready("brightness"))
        self.assertTrue(snapshot["snapshot"]["client"]["setup_profile"]["present"])
        self.assertEqual(plan["cue"], "intro")
        self.assertEqual(plan["steps"][0]["scene"]["obj"], 2)
        self.assertEqual(plan["steps"][0]["command_plan"]["start_seq"], 5)
        self.assertEqual(key.scenes, [])

    def test_rig_plans_fixture_groups_without_opening_transports(self) -> None:
        key = FakeLight("key")
        fill = FakeLight("fill")
        rig = rig_from_mapping(
            {
                "fixtures": {
                    "key": {"transport": "usb", "obj": 1, "tags": ["set"]},
                    "fill": {
                        "transport": "ble",
                        "name_contains": "FILL",
                        "obj": 2,
                        "tags": ["set"],
                    },
                },
                "presets": {"scenes": {"look": {"brightness": 30}}},
                "cues": {"cues": {"intro": {"steps": [{"preset": "look"}]}}},
                "control_mode": "0x01",
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )

        scene = rig.plan_scene(
            "key",
            {"brightness": 10},
            first_word=0x0301,
            start_seq=7,
        )
        preset = rig.plan_preset(
            "fill",
            "look",
            overrides={"brightness": 35},
            start_seq=11,
        )
        all_plan = rig.plan_all({"brightness": 12}, tag="set", start_seq=4)
        scene_map = rig.plan_scene_map(
            {
                "key": {"brightness": 20},
                "fill": {"kelvin": 5600},
            },
            start_seq=9,
        )
        cue_plan = rig.plan_named_cue_all("intro", tag="set", start_seq=20)

        self.assertTrue(scene["dry_run"])
        self.assertEqual(scene["fixture"], "key")
        self.assertEqual(scene["transport"], "usb")
        self.assertEqual(scene["config"]["transport"], "usb")
        self.assertEqual(scene["scene"]["obj"], 1)
        self.assertEqual(scene["first_word_hex"], "0x0301")
        self.assertEqual(scene["control_mode"], 0x01)
        self.assertEqual(scene["command_plan"]["start_seq"], 7)
        self.assertEqual(preset["fixture"], "fill")
        self.assertEqual(preset["transport"], "ble")
        self.assertEqual(preset["scene"]["obj"], 2)
        self.assertEqual(preset["scene"]["brightness"], 35.0)
        self.assertEqual(all_plan["action"], "rig_plan_all")
        self.assertTrue(all_plan["dry_run"])
        self.assertTrue(all_plan["planned"])
        self.assertEqual(all_plan["fixture_order"], ["key", "fill"])
        self.assertEqual(all_plan["start_seq"], 4)
        self.assertEqual(all_plan["next_seq"], 5)
        self.assertEqual(all_plan["fixtures"]["fill"]["scene"]["obj"], 2)
        self.assertEqual(scene_map["action"], "rig_plan_scene_map")
        self.assertEqual(scene_map["fixtures"]["fill"]["scene"]["kelvin"], 5600)
        self.assertEqual(cue_plan["action"], "rig_plan_named_cue_all")
        self.assertEqual(cue_plan["fixtures"]["key"]["cue"], "intro")
        self.assertEqual(cue_plan["fixtures"]["fill"]["steps"][0]["scene"]["obj"], 2)
        self.assertEqual(key.scenes, [])
        self.assertEqual(fill.scenes, [])

    def test_rig_executes_serialized_plan_maps(self) -> None:
        key = FakeLight("key")
        fill = FakeLight("fill")
        rig = rig_from_mapping(
            {
                "fixtures": {
                    "key": {"transport": "usb", "obj": 1, "tags": ["set"]},
                    "fill": {"transport": "usb", "obj": 2, "tags": ["set"]},
                }
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )
        plan = rig.plan_all({"brightness": 12}, tag="set", first_word=0x0301)

        response = rig.execute_plan_map(
            serialized_plan_bundle(plan, created_at=123.0),
            timeout=0.25,
        )

        self.assertEqual(response["action"], "rig_execute_plan_map")
        self.assertTrue(response["applied"])
        self.assertEqual(response["fixtures"]["key"]["planned_action"], "scene")
        self.assertEqual(response["fixtures"]["fill"]["scene"]["obj"], 2)
        self.assertEqual(
            [first_frame(frame).first_word for frame in key.prebuilt_frames],
            [0x0301],
        )
        self.assertEqual(
            [first_frame(frame).first_word for frame in fill.prebuilt_frames],
            [0x0301],
        )

    def test_rig_executes_serialized_cue_plan_maps(self) -> None:
        key = FakeLight("key")
        fill = FakeLight("fill")
        rig = rig_from_mapping(
            {
                "fixtures": {
                    "key": {"transport": "usb", "obj": 1, "tags": ["set"]},
                    "fill": {"transport": "usb", "obj": 2, "tags": ["set"]},
                },
                "cues": {
                    "cues": {
                        "intro": {
                            "steps": [
                                {"scene": {"brightness": 12}},
                                {
                                    "to": {"brightness": 16},
                                    "steps": 1,
                                    "duration": 0,
                                },
                            ]
                        }
                    }
                },
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )
        plan = rig.plan_named_cue_all("intro", tag="set", first_word=0x0301)

        response = rig.execute_plan_map(plan, timeout=0.25)

        self.assertEqual(response["action"], "rig_execute_plan_map")
        self.assertTrue(response["applied"])
        self.assertEqual(response["fixtures"]["key"]["planned_action"], "sequence")
        self.assertEqual(response["fixtures"]["fill"]["planned_action"], "sequence")
        self.assertEqual(response["fixtures"]["key"]["cue"], "intro")
        self.assertEqual(response["fixtures"]["fill"]["cue"], "intro")
        self.assertEqual(
            [first_frame(frame).seq for frame in key.prebuilt_frames],
            [1, 2],
        )
        self.assertEqual(
            [first_frame(frame).seq for frame in fill.prebuilt_frames],
            [1, 2],
        )
        self.assertEqual(response["fixtures"]["fill"]["scene"]["obj"], 2)

    def test_rig_can_guard_fixture_controls_with_setup_profiles(self) -> None:
        key = FakeLight("key")
        rig = LightRig(
            [
                LightFixture(
                    "key",
                    obj=2,
                    setup_profile=setup_profile(),
                )
            ],
            light_factories={"key": FakeFactory(key)},
            require_setup_profile_controls=True,
        )
        mapped = rig_from_mapping(
            {
                "require_setup_profile_controls": True,
                "fixtures": {"key": {"profile": setup_profile().to_dict()}},
            },
            light_factories={"key": FakeFactory(FakeLight("mapped"))},
        )

        with self.assertRaisesRegex(SetupProfileNotReady, "control_writes"):
            rig.apply_scene("key", {"brightness": 35})
        with self.assertRaisesRegex(SetupProfileNotReady, "control_writes"):
            rig.set_brightness("key", 35)
        with self.assertRaisesRegex(SetupProfileNotReady, "control_writes"):
            rig.apply_preset("key", "look")
        with self.assertRaisesRegex(RigConfigError, "no setup profile"):
            LightRig(
                [LightFixture("missing")],
                light_factories={"missing": FakeFactory(FakeLight("missing"))},
                require_setup_profile_controls=True,
            ).apply_scene("missing", {"brightness": 10})

        self.assertTrue(rig.require_setup_profile_primitive("key", "status").ok)
        self.assertTrue(mapped.require_setup_profile_controls)
        self.assertTrue(mapped.to_dict()["require_setup_profile_controls"])
        self.assertEqual(key.scenes, [])

    def test_rig_direct_primitives_use_fixture_object_ids(self) -> None:
        key = FakeLight("key")
        rig = LightRig(
            [LightFixture("key", obj=7)],
            light_factories={"key": FakeFactory(key)},
        )

        register = rig.register("key", device_id=2, group_id=3)
        brightness = rig.set_brightness("key", 35, control_mode=0x01)
        cct = rig.set_cct("key", 5600, control_mode=0x01)
        sleep = rig.set_sleep("key", 1, control_mode=0x01)
        rgb = rig.set_rgb("key", 255, 180, 120, control_mode=0x01)
        hsi = rig.set_hsi("key", 30.0, 0.5, 40, control_mode=0x01)
        read = rig.read_brightness("key")

        self.assertTrue(register["ok"])
        self.assertEqual(brightness["fixture"], "key")
        self.assertEqual(brightness["scene"]["obj"], 7)
        self.assertTrue(cct["applied"])
        self.assertEqual(sleep["scene"]["sleep"], 1)
        self.assertEqual(rgb["action"], "set_rgb")
        self.assertEqual(hsi["scene"]["hue"], 30.0)
        self.assertTrue(read["ok"])
        self.assertEqual(read["action"], "read_brightness")
        self.assertEqual(
            key.primitive_calls,
            [
                ("brightness", 7, 35, 0x01),
                ("cct", 7, 5600, 0x01),
                ("sleep", 7, 1, 0x01),
                ("rgb", 7, (255, 180, 120), 0x01),
                ("hsi", 7, (30.0, 0.5, 40), 0x01),
            ],
        )
        self.assertEqual(
            dict(key.payloads)[RuntimeCommand.BRIGHTNESS][:2],
            b"\x07\x00",
        )

    def test_rig_direct_primitive_all_targets_tags_and_stops(self) -> None:
        key = FakeLight("key", acknowledged=False)
        fill = FakeLight("fill")
        desk = FakeLight("desk")
        rig = LightRig(
            [
                LightFixture("key", obj=1, tags=("stage",)),
                LightFixture("fill", obj=2, tags=("stage",)),
                LightFixture("desk", obj=3, tags=("desk",)),
            ],
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
                "desk": FakeFactory(desk),
            },
        )

        response = rig.set_sleep_all(1, tag="stage", stop_on_unconfirmed=True)

        self.assertFalse(response["applied"])
        self.assertTrue(response["stopped"])
        self.assertEqual(response["reason"], "key:sent_no_response")
        self.assertEqual(key.primitive_calls, [("sleep", 1, 1, 0x33)])
        self.assertEqual(fill.primitive_calls, [])
        self.assertEqual(desk.primitive_calls, [])

    def test_rig_apply_all_can_require_setup_profile_per_call(self) -> None:
        key = FakeLight("key")
        fill = FakeLight("fill")
        rig = LightRig(
            [
                LightFixture("key", obj=1, setup_profile=setup_profile()),
                LightFixture(
                    "fill",
                    obj=2,
                    setup_profile=setup_profile(control_writes=True),
                ),
            ],
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )

        with self.assertRaisesRegex(SetupProfileNotReady, "control_writes"):
            rig.apply_all({"brightness": 35}, require_setup_profile=True)

        self.assertEqual(key.scenes, [])
        self.assertEqual(fill.scenes, [])

    def test_rig_setup_profile_summary_is_no_io_fixture_preflight(self) -> None:
        key = FakeLight("key")
        rig = LightRig(
            [
                LightFixture(
                    "key",
                    obj=1,
                    tags=("set",),
                    setup_profile=setup_profile(control_writes=True),
                ),
                LightFixture(
                    "fill",
                    obj=2,
                    tags=("set",),
                    setup_profile=setup_profile(),
                ),
                LightFixture("practical", obj=3, tags=("ambient",)),
            ],
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(FakeLight("fill")),
                "practical": FakeFactory(FakeLight("practical")),
            },
            require_setup_profile_controls=True,
        )

        key_summary = rig.setup_profile_summary(
            "key",
            primitives=("status", "set_brightness", "read_brightness"),
        )
        set_summary = rig.setup_profile_summary_all(
            tag="set",
            primitives=("status", "set_brightness"),
        )
        all_summary = rig.setup_profile_summary_all(
            primitives=("status", "set_brightness"),
        )

        self.assertEqual(key_summary["transport"], "usb")
        self.assertEqual(key_summary["config"]["port"], "/dev/cu.usbmodem21301")
        self.assertTrue(key_summary["setup_profile"]["present"])
        self.assertTrue(key_summary["primitive_ready_for"]["status"])
        self.assertTrue(key_summary["primitive_ready_for"]["set_brightness"])
        self.assertFalse(key_summary["primitive_ready_for"]["read_brightness"])
        self.assertFalse(key_summary["ready"])
        self.assertEqual(key.scenes, [])

        self.assertEqual(set_summary["missing_profiles"], [])
        self.assertFalse(set_summary["ready"])
        self.assertEqual(set_summary["unready"], {"fill": ["set_brightness"]})
        self.assertEqual(set_summary["primitives"], ["status", "set_brightness"])
        self.assertTrue(set_summary["require_setup_profile_controls"])
        self.assertFalse(all_summary["complete"])
        self.assertEqual(all_summary["missing_profiles"], ["practical"])
        self.assertEqual(
            all_summary["unready"]["practical"],
            ["status", "set_brightness"],
        )

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

    def test_rig_iterates_fixture_state_events(self) -> None:
        key = FakeLight("key")
        rig = LightRig(
            [LightFixture("key", obj=1)],
            light_factories={"key": FakeFactory(key)},
        )

        initial = next(rig.state_events("key", limit=1, timeout=0.1))
        self.assertEqual(
            initial,
            {
                "fixture": "key",
                "version": 0,
                "state": {"scene": None},
            },
        )

        rig.apply_scene("key", {"brightness": 10})
        rig.apply_scene("key", {"brightness": 20})
        events = list(
            rig.state_events(
                "key",
                after_version=1,
                limit=1,
                timeout=0.1,
                initial=False,
            )
        )

        self.assertEqual(events[0]["fixture"], "key")
        self.assertEqual(events[0]["version"], 2)
        self.assertEqual(events[0]["state"]["scene"]["brightness"], 20)

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

    def test_require_readiness_all_raises_with_fixture_pending_actions(self) -> None:
        key = FakeLight("key")
        rig = LightRig(
            [LightFixture("key")],
            light_factories={"key": FakeFactory(key)},
        )

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": True, "selected_port": None, "ports": []},
                "ble": {"macos_status": None, "scan": None},
            },
        ):
            response = rig.require_readiness_all("read_status")
            with self.assertRaises(RigNotReady) as error:
                rig.require_readiness_all("control_requests")

        self.assertTrue(response["applied"])
        self.assertEqual(error.exception.capabilities, ("control_requests",))
        self.assertEqual(
            error.exception.pending_action_ids,
            {"key": {"control_requests": ["enable-control"]}},
        )

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
        self.assertEqual(snapshot["client"]["setup_profile"], {"present": False})

    def test_connection_report_all_returns_per_fixture_route_reports(self) -> None:
        key = FakeLight("key")
        fill = FakeLight("fill")
        rig = rig_from_mapping(
            {
                "fixtures": {
                    "key": {
                        "transport": "usb",
                        "port": "/dev/cu.key",
                        "tags": ["set"],
                    },
                    "fill": {
                        "transport": "ble",
                        "name_contains": "FILL",
                        "tags": ["set"],
                    },
                }
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )
        calls: list[tuple[LightConnectionConfig, dict[str, object]]] = []

        def fake_report(
            config: LightConnectionConfig,
            **kwargs: object,
        ) -> dict[str, object]:
            calls.append((config, dict(kwargs)))
            selected_config = config.to_dict() if config.transport == "usb" else None
            return {
                "ok": config.transport == "usb",
                "selected_config": selected_config,
                "summary": {
                    "selected_transport": config.transport
                    if config.transport == "usb"
                    else None,
                    "candidate_count": 1,
                    "confirmed_count": 1 if config.transport == "usb" else 0,
                    "ble_blocker": None
                    if config.transport == "usb"
                    else "Bluetooth state unauthorized: 3",
                },
                "selected": {"transport": config.transport}
                if config.transport == "usb"
                else None,
                "best_confirmed": {"transport": config.transport}
                if config.transport == "usb"
                else None,
            }

        with patch(
            "zhiyun_light_control.integration.local_connection_report",
            side_effect=fake_report,
        ):
            response = rig.connection_report_all(
                tag="set",
                include_ble=True,
                include_ble_status=True,
                persistent=True,
            )

        self.assertEqual(response["action"], "rig_connection_report")
        self.assertFalse(response["applied"])
        self.assertEqual(response["fixtures"]["key"]["transport"], "usb")
        self.assertEqual(
            response["fixtures"]["key"]["config"]["port"],
            "/dev/cu.key",
        )
        self.assertFalse(response["fixtures"]["fill"]["ok"])
        self.assertEqual(
            response["fixtures"]["fill"]["reason"],
            "Bluetooth state unauthorized: 3",
        )
        self.assertEqual(
            [config.transport for config, _kwargs in calls],
            ["usb", "ble"],
        )
        self.assertTrue(all(kwargs["include_ble"] for _config, kwargs in calls))
        self.assertTrue(
            all(kwargs["include_ble_status"] for _config, kwargs in calls)
        )
        self.assertTrue(all(kwargs["persistent"] for _config, kwargs in calls))

    def test_setup_report_all_returns_per_fixture_setup_evidence(self) -> None:
        key = FakeLight("key")
        fill = FakeLight("fill")
        rig = rig_from_mapping(
            {
                "fixtures": {
                    "key": {
                        "transport": "usb",
                        "port": "/dev/cu.key",
                        "obj": 2,
                        "tags": ["set"],
                    },
                    "fill": {
                        "transport": "ble",
                        "name_contains": "FILL",
                        "obj": 3,
                        "tags": ["set"],
                    },
                },
                "control_mode": 0x33,
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )
        calls: list[tuple[LightConnectionConfig, dict[str, object]]] = []

        def fake_report(
            config: LightConnectionConfig,
            **kwargs: object,
        ) -> dict[str, object]:
            calls.append((config, dict(kwargs)))
            ready = config.transport == "usb"
            return {
                "ok": ready,
                "config": config.to_dict(),
                "route_confirmed": ready,
                "require_confirmed_route": True,
                "status_ok": ready,
                "status_error": None if ready else "no status ACK",
                "ready_for": {
                    "read_status": ready,
                    "control_writes": False,
                },
                "validation_ready_for": {
                    "read_status": ready,
                    "control_writes": False,
                },
                "validation_unconfirmed": ["set_brightness"],
                "summary": {
                    "ok": ready,
                    "errors": [] if ready else ["no status ACK"],
                    "ble_blocker": None
                    if ready
                    else "Bluetooth state unauthorized: 3",
                },
            }

        with patch(
            "zhiyun_light_control.integration.local_setup_report",
            side_effect=fake_report,
        ):
            response = rig.setup_report_all(
                tag="set",
                include_ble=True,
                include_ble_status=True,
                persistent=True,
                allow_control=True,
                include_object_reads=True,
                include_color=True,
            )

        self.assertEqual(response["action"], "rig_setup_report")
        self.assertFalse(response["applied"])
        self.assertEqual(response["fixtures"]["key"]["config"]["port"], "/dev/cu.key")
        self.assertTrue(response["fixtures"]["key"]["route_confirmed"])
        self.assertEqual(response["fixtures"]["key"]["reason"], "setup_ready")
        self.assertEqual(
            response["fixtures"]["fill"]["reason"],
            "no status ACK",
        )
        self.assertEqual(
            [config.transport for config, _kwargs in calls],
            ["usb", "ble"],
        )
        self.assertEqual([kwargs["obj"] for _config, kwargs in calls], [2, 3])
        self.assertTrue(all(kwargs["include_ble"] for _config, kwargs in calls))
        self.assertTrue(
            all(kwargs["include_ble_status"] for _config, kwargs in calls)
        )
        self.assertTrue(all(kwargs["persistent"] for _config, kwargs in calls))
        self.assertTrue(all(kwargs["allow_control"] for _config, kwargs in calls))
        self.assertTrue(
            all(kwargs["include_object_reads"] for _config, kwargs in calls)
        )
        self.assertTrue(all(kwargs["include_color"] for _config, kwargs in calls))
        self.assertEqual(
            [kwargs["control_mode"] for _config, kwargs in calls],
            [0x33, 0x33],
        )

    def test_setup_profiles_all_materializes_reusable_profiled_rig(self) -> None:
        key = FakeLight("key")
        fill = FakeLight("fill")
        rig = rig_from_mapping(
            {
                "fixtures": {
                    "key": {
                        "transport": "usb",
                        "port": "/dev/cu.key",
                        "obj": 2,
                        "tags": ["set"],
                    },
                    "fill": {
                        "transport": "ble",
                        "name_contains": "FILL",
                        "obj": 3,
                        "tags": ["set"],
                    },
                }
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )

        def fake_report(
            config: LightConnectionConfig,
            **kwargs: object,
        ) -> dict[str, object]:
            del kwargs
            ready = config.transport == "usb"
            return {
                "ok": ready,
                "config": config.to_dict(),
                "route_confirmed": ready,
                "require_confirmed_route": True,
                "status_ok": ready,
                "status_error": None if ready else "no status ACK",
                "ready_for": {
                    "read_status": ready,
                    "control_writes": False,
                },
                "validation_ready_for": {
                    "read_status": ready,
                    "control_writes": False,
                },
                "validation_unconfirmed": ["set_brightness"],
                "summary": {
                    "ok": ready,
                    "errors": [] if ready else ["no status ACK"],
                },
            }

        with patch(
            "zhiyun_light_control.integration.local_setup_report",
            side_effect=fake_report,
        ):
            report = rig.setup_report_all(tag="set")

        profiles = rig_setup_profiles_from_report(report)
        self.assertEqual(set(profiles), {"key", "fill"})
        self.assertTrue(profiles["key"].ready("read_status"))
        self.assertFalse(profiles["fill"].ready("read_status"))

        profiled = rig.with_setup_profiles(
            report,
            require_setup_profile_controls=True,
        )
        self.assertTrue(profiled.require_setup_profile_controls)
        self.assertEqual(profiled.fixture("key").config.port, "/dev/cu.key")
        self.assertIsNotNone(profiled.fixture("fill").setup_profile)
        self.assertEqual(profiled.fixture("fill").obj, 3)
        self.assertEqual(profiled.fixture("fill").tags, ("set",))

        rematerialized = rig.with_setup_profiles(profiles)
        self.assertTrue(rematerialized.require_setup_profile("key", "read_status").ok)
        with self.assertRaisesRegex(ValueError, "unknown fixture profile"):
            rig.with_setup_profiles({"missing": profiles["key"]})

        with patch(
            "zhiyun_light_control.integration.local_setup_report",
            side_effect=fake_report,
        ):
            generated = rig.setup_profiles_all(tag="set")
        self.assertEqual(set(generated), {"key", "fill"})

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rig.json"
            profiled.save(path)
            restored = load_rig(path)

        self.assertTrue(restored.require_setup_profile("key", "read_status").ok)
        self.assertIsNotNone(restored.fixture("fill").setup_profile)

    def test_save_rig_profile_bundle_writes_relative_profile_paths(self) -> None:
        rig = LightRig(
            [
                LightFixture.from_setup_profile(
                    "key",
                    setup_profile(control_writes=True),
                    obj=2,
                    tags=("set",),
                ),
                LightFixture(
                    "practical",
                    LightConnectionConfig.usb(port="/dev/cu.practical"),
                    obj=3,
                    tags=("set",),
                ),
            ],
            require_setup_profile_controls=True,
        )
        bundle = rig_profile_bundle_mapping(rig, profile_dir="setup")
        fixtures = bundle["fixtures"]
        self.assertIsInstance(fixtures, list)
        key = fixtures[0]
        practical = fixtures[1]
        self.assertEqual(key["profile_path"], "setup/key.json")
        self.assertNotIn("setup_profile", key)
        self.assertEqual(practical["config"]["port"], "/dev/cu.practical")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = save_rig_profile_bundle(
                rig,
                root / "show" / "rig.json",
                profile_dir="setup",
            )
            rig_path = Path(output["rig_path"])
            restored = load_rig(rig_path)

        self.assertEqual(rig_path.name, "rig.json")
        self.assertTrue(restored.require_setup_profile("key", "read_status").ok)
        self.assertEqual(restored.fixture("key").config.port, "/dev/cu.usbmodem21301")
        self.assertEqual(restored.fixture("practical").config.port, "/dev/cu.practical")
        self.assertEqual(
            output["mapping"]["fixtures"][0]["profile_path"],
            "setup/key.json",
        )
        self.assertEqual(Path(output["profile_paths"]["key"]).name, "key.json")

        with self.assertRaisesRegex(ValueError, "profile_dir must be a relative path"):
            rig_profile_bundle_mapping(rig, profile_dir="/tmp/profiles")

    def test_save_setup_profile_bundle_runs_full_setup_flow(self) -> None:
        key = FakeLight("key")
        fill = FakeLight("fill")
        rig = rig_from_mapping(
            {
                "fixtures": {
                    "key": {
                        "transport": "usb",
                        "port": "/dev/cu.key",
                        "obj": 2,
                        "tags": ["set"],
                    },
                    "fill": {
                        "transport": "ble",
                        "name_contains": "FILL",
                        "obj": 3,
                        "tags": ["set"],
                    },
                }
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )
        calls: list[tuple[LightConnectionConfig, dict[str, object]]] = []

        def fake_report(
            config: LightConnectionConfig,
            **kwargs: object,
        ) -> dict[str, object]:
            calls.append((config, dict(kwargs)))
            return {
                "ok": True,
                "config": config.to_dict(),
                "route_confirmed": True,
                "require_confirmed_route": True,
                "status_ok": True,
                "status_error": None,
                "ready_for": {
                    "read_status": True,
                    "control_writes": False,
                },
                "validation_ready_for": {
                    "read_status": True,
                    "control_writes": False,
                },
                "validation_unconfirmed": ["set_brightness"],
                "summary": {"ok": True, "errors": []},
            }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "zhiyun_light_control.integration.local_setup_report",
                side_effect=fake_report,
            ):
                output = rig.save_setup_profile_bundle(
                    root / "show" / "rig.json",
                    tag="set",
                    include_ble=True,
                    persistent=True,
                    profile_dir="setup",
                    require_setup_profile_controls=True,
                )
            restored = load_rig(output["rig_path"])

        self.assertEqual(
            [config.transport for config, _kwargs in calls],
            ["usb", "ble"],
        )
        self.assertTrue(all(kwargs["include_ble"] for _config, kwargs in calls))
        self.assertTrue(all(kwargs["persistent"] for _config, kwargs in calls))
        self.assertTrue(restored.require_setup_profile_controls)
        self.assertTrue(restored.require_setup_profile("key", "read_status").ok)
        self.assertTrue(restored.require_setup_profile("fill", "read_status").ok)
        self.assertEqual(
            output["mapping"]["fixtures"][0]["profile_path"],
            "setup/key.json",
        )
        self.assertEqual(
            output["mapping"]["fixtures"][1]["profile_path"],
            "setup/fill.json",
        )

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

    async def test_async_rig_accepts_fixture_profile_mapping(self) -> None:
        key = AsyncFakeLight("key")
        rig = async_rig_from_mapping(
            {
                "require_setup_profile_controls": True,
                "fixtures": {
                    "key": {
                        "profile": setup_profile(
                            LightConnectionConfig.ble(
                                address="UUID-1",
                                backend="macos-app",
                            )
                        ).to_dict(),
                        "obj": 6,
                    }
                }
            },
            light_factories={"key": FakeFactory(key)},
        )

        fixture = rig.fixture("key")
        self.assertEqual(fixture.config.transport, "ble")
        self.assertEqual(fixture.config.address, "UUID-1")
        self.assertEqual(fixture.obj, 6)
        self.assertTrue(rig.require_setup_profile("key", "read_status").ok)
        self.assertTrue(rig.require_setup_profile_controls)
        integration = rig.integration("key")
        self.assertTrue(integration.require_setup_profile_controls)
        self.assertFalse(integration.setup_profile_primitive_ready("brightness"))

    async def test_async_rig_json_helpers_load_saved_sdk_profiles(self) -> None:
        rig = AsyncLightRig(
            [
                LightFixture.from_setup_profile(
                    "rim",
                    setup_profile(
                        LightConnectionConfig.ble(
                            address="UUID-2",
                            backend="worker",
                        )
                    ),
                    obj=7,
                )
            ],
            require_setup_profile_controls=True,
        )

        restored = async_rig_from_json(rig.to_json())

        self.assertEqual(restored.fixture_names(), ("rim",))
        self.assertTrue(restored.require_setup_profile_controls)
        self.assertEqual(restored.fixture("rim").config.transport, "ble")
        self.assertEqual(restored.fixture("rim").config.address, "UUID-2")
        self.assertEqual(restored.fixture("rim").obj, 7)
        self.assertTrue(restored.require_setup_profile("rim", "read_status").ok)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "async-rig.json"
            restored.save(path)
            saved = AsyncLightRig.load(
                path,
                light_factories={"rim": FakeFactory(AsyncFakeLight("rim"))},
            )

            with self.assertRaisesRegex(SetupProfileNotReady, "control_writes"):
                await saved.apply_scene(
                    "rim",
                    {"brightness": 10},
                    require_setup_profile=True,
                )

    async def test_async_rig_can_guard_fixture_controls_with_setup_profiles(
        self,
    ) -> None:
        key = AsyncFakeLight("key")
        fill = AsyncFakeLight("fill")
        rig = AsyncLightRig(
            [
                LightFixture("key", obj=1, setup_profile=setup_profile()),
                LightFixture(
                    "fill",
                    obj=2,
                    setup_profile=setup_profile(control_writes=True),
                ),
            ],
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
            require_setup_profile_controls=True,
        )

        with self.assertRaisesRegex(SetupProfileNotReady, "control_writes"):
            await rig.apply_scene("key", {"brightness": 35})
        with self.assertRaisesRegex(SetupProfileNotReady, "control_writes"):
            await rig.apply_all({"brightness": 35})
        with self.assertRaisesRegex(RigConfigError, "no setup profile"):
            await AsyncLightRig(
                [LightFixture("missing")],
                light_factories={"missing": FakeFactory(AsyncFakeLight("missing"))},
                require_setup_profile_controls=True,
            ).apply_scene("missing", {"brightness": 10})

        self.assertEqual(key.scenes, [])
        self.assertEqual(fill.scenes, [])

    async def test_async_rig_setup_profile_summary_is_no_io_preflight(self) -> None:
        rig = AsyncLightRig(
            [
                LightFixture.from_setup_profile(
                    "key",
                    setup_profile(control_writes=True),
                    tags=("set",),
                ),
                LightFixture("fill", setup_profile=setup_profile()),
            ],
            light_factories={
                "key": FakeFactory(AsyncFakeLight("key")),
                "fill": FakeFactory(AsyncFakeLight("fill")),
            },
            require_setup_profile_controls=True,
        )

        summary = rig.setup_profile_summary_all(
            primitives=("status", "set_brightness"),
        )

        self.assertTrue(summary["fixtures"]["key"]["primitive_ready_for"]["status"])
        self.assertTrue(
            summary["fixtures"]["key"]["primitive_ready_for"]["set_brightness"]
        )
        self.assertFalse(
            summary["fixtures"]["fill"]["primitive_ready_for"]["set_brightness"]
        )
        self.assertEqual(summary["unready"], {"fill": ["set_brightness"]})
        self.assertTrue(summary["complete"])
        self.assertFalse(summary["ready"])

    async def test_async_rig_plans_fixture_groups_without_io(self) -> None:
        key = AsyncFakeLight("key")
        fill = AsyncFakeLight("fill")
        rig = async_rig_from_mapping(
            {
                "fixtures": {
                    "key": {"transport": "ble", "name_contains": "KEY", "obj": 3},
                    "fill": {"transport": "usb", "obj": 4},
                },
                "presets": {"scenes": {"look": {"brightness": 45}}},
                "cues": {"cues": {"intro": {"steps": [{"preset": "look"}]}}},
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )

        scene = rig.plan_scene("key", {"brightness": 10}, start_seq=3)
        all_plan = rig.plan_all({"brightness": 20}, start_seq=8)
        cue_plan = rig.plan_named_cue_all("intro", start_seq=12)

        self.assertTrue(scene["dry_run"])
        self.assertEqual(scene["fixture"], "key")
        self.assertEqual(scene["transport"], "ble")
        self.assertEqual(scene["scene"]["obj"], 3)
        self.assertEqual(scene["command_plan"]["start_seq"], 3)
        self.assertEqual(all_plan["fixture_order"], ["key", "fill"])
        self.assertEqual(all_plan["fixtures"]["fill"]["scene"]["obj"], 4)
        self.assertEqual(cue_plan["fixtures"]["fill"]["cue"], "intro")
        self.assertEqual(cue_plan["fixtures"]["fill"]["steps"][0]["scene"]["obj"], 4)
        self.assertEqual(key.scenes, [])
        self.assertEqual(fill.scenes, [])

    async def test_async_rig_executes_serialized_plan_maps(self) -> None:
        key = AsyncFakeLight("key")
        fill = AsyncFakeLight("fill")
        rig = async_rig_from_mapping(
            {
                "fixtures": {
                    "key": {"transport": "ble", "name_contains": "KEY", "obj": 3},
                    "fill": {"transport": "ble", "name_contains": "FILL", "obj": 4},
                }
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )
        plan = rig.plan_all({"brightness": 20}, first_word=0x0301)

        response = await rig.execute_plan_map(
            serialized_plan_bundle(plan, created_at=123.0),
            timeout=0.25,
        )

        self.assertEqual(response["action"], "rig_execute_plan_map")
        self.assertTrue(response["applied"])
        self.assertEqual(response["fixtures"]["key"]["planned_action"], "scene")
        self.assertEqual(response["fixtures"]["fill"]["scene"]["obj"], 4)
        self.assertEqual(
            [first_frame(frame).first_word for frame in key.prebuilt_frames],
            [0x0301],
        )
        self.assertEqual(
            [first_frame(frame).first_word for frame in fill.prebuilt_frames],
            [0x0301],
        )

    async def test_async_rig_executes_serialized_cue_plan_maps(self) -> None:
        key = AsyncFakeLight("key")
        fill = AsyncFakeLight("fill")
        rig = async_rig_from_mapping(
            {
                "fixtures": {
                    "key": {"transport": "ble", "name_contains": "KEY", "obj": 3},
                    "fill": {"transport": "ble", "name_contains": "FILL", "obj": 4},
                },
                "cues": {
                    "cues": {
                        "intro": {
                            "steps": [
                                {"scene": {"brightness": 12}},
                                {
                                    "to": {"brightness": 16},
                                    "steps": 1,
                                    "duration": 0,
                                },
                            ]
                        }
                    }
                },
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )
        plan = rig.plan_named_cue_all("intro", first_word=0x0301)

        response = await rig.execute_plan_map(plan, timeout=0.25)

        self.assertEqual(response["action"], "rig_execute_plan_map")
        self.assertTrue(response["applied"])
        self.assertEqual(response["fixtures"]["key"]["planned_action"], "sequence")
        self.assertEqual(response["fixtures"]["fill"]["planned_action"], "sequence")
        self.assertEqual(response["fixtures"]["key"]["cue"], "intro")
        self.assertEqual(response["fixtures"]["fill"]["cue"], "intro")
        self.assertEqual(
            [first_frame(frame).seq for frame in key.prebuilt_frames],
            [1, 2],
        )
        self.assertEqual(
            [first_frame(frame).seq for frame in fill.prebuilt_frames],
            [1, 2],
        )
        self.assertEqual(response["fixtures"]["fill"]["scene"]["obj"], 4)

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

    async def test_async_rig_iterates_fixture_state_events(self) -> None:
        key = AsyncFakeLight("key")
        rig = AsyncLightRig(
            [
                LightFixture(
                    "key",
                    LightConnectionConfig(transport="ble", name_contains="KEY"),
                    obj=1,
                )
            ],
            light_factories={"key": FakeFactory(key)},
        )

        initial_events: list[dict[str, object]] = []
        async for event in rig.state_events("key", limit=1, timeout=0.1):
            initial_events.append(event)
        self.assertEqual(
            initial_events,
            [
                {
                    "fixture": "key",
                    "version": 0,
                    "state": {"scene": None},
                }
            ],
        )

        await rig.apply_scene("key", {"brightness": 10})
        await rig.apply_scene("key", {"brightness": 20})
        events: list[dict[str, object]] = []
        async for event in rig.state_events(
            "key",
            after_version=1,
            limit=1,
            timeout=0.1,
            initial=False,
        ):
            events.append(event)

        self.assertEqual(events[0]["fixture"], "key")
        self.assertEqual(events[0]["version"], 2)
        self.assertEqual(events[0]["state"]["scene"]["brightness"], 20)

    async def test_async_rig_direct_primitives_use_fixture_object_ids(self) -> None:
        key = AsyncFakeLight("key")
        rig = AsyncLightRig(
            [LightFixture("key", obj=6)],
            light_factories={"key": FakeFactory(key)},
        )

        register = await rig.register("key", device_id=2, group_id=3)
        brightness = await rig.set_brightness("key", 35, control_mode=0x01)
        cct = await rig.set_cct("key", 5600, control_mode=0x01)
        sleep = await rig.set_sleep("key", 1, control_mode=0x01)
        rgb = await rig.set_rgb("key", 255, 180, 120, control_mode=0x01)
        hsi = await rig.set_hsi("key", 30.0, 0.5, 40, control_mode=0x01)
        read = await rig.read_brightness("key")

        self.assertTrue(register["ok"])
        self.assertEqual(brightness["fixture"], "key")
        self.assertEqual(brightness["scene"]["obj"], 6)
        self.assertTrue(cct["applied"])
        self.assertEqual(sleep["scene"]["sleep"], 1)
        self.assertEqual(rgb["action"], "set_rgb")
        self.assertEqual(hsi["scene"]["hue"], 30.0)
        self.assertTrue(read["ok"])
        self.assertEqual(read["action"], "read_brightness")
        self.assertEqual(
            key.primitive_calls,
            [
                ("brightness", 6, 35, 0x01),
                ("cct", 6, 5600, 0x01),
                ("sleep", 6, 1, 0x01),
                ("rgb", 6, (255, 180, 120), 0x01),
                ("hsi", 6, (30.0, 0.5, 40), 0x01),
            ],
        )
        self.assertEqual(
            dict(key.payloads)[RuntimeCommand.BRIGHTNESS][:2],
            b"\x06\x00",
        )

    async def test_async_direct_primitive_all_targets_tags_and_stops(self) -> None:
        key = AsyncFakeLight("key", acknowledged=False)
        fill = AsyncFakeLight("fill")
        desk = AsyncFakeLight("desk")
        rig = AsyncLightRig(
            [
                LightFixture("key", obj=1, tags=("stage",)),
                LightFixture("fill", obj=2, tags=("stage",)),
                LightFixture("desk", obj=3, tags=("desk",)),
            ],
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
                "desk": FakeFactory(desk),
            },
        )

        response = await rig.set_sleep_all(
            1,
            tag="stage",
            stop_on_unconfirmed=True,
        )

        self.assertFalse(response["applied"])
        self.assertTrue(response["stopped"])
        self.assertEqual(response["reason"], "key:sent_no_response")
        self.assertEqual(key.primitive_calls, [("sleep", 1, 1, 0x33)])
        self.assertEqual(fill.primitive_calls, [])
        self.assertEqual(desk.primitive_calls, [])

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

    async def test_async_require_readiness_all_raises_for_unready_fixture(
        self,
    ) -> None:
        key = AsyncFakeLight("key")
        rig = AsyncLightRig(
            [LightFixture("key")],
            light_factories={"key": FakeFactory(key)},
        )

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {"macos_status": None, "scan": None},
            },
        ):
            response = await rig.require_readiness_all(
                "control_requests",
                allow_control=True,
            )
            with self.assertRaises(RigNotReady) as error:
                await rig.require_readiness_all(
                    "confirmed_control",
                    allow_control=True,
                )

        self.assertTrue(response["applied"])
        self.assertEqual(error.exception.capabilities, ("confirmed_control",))
        self.assertEqual(
            error.exception.pending_action_ids,
            {"key": {"confirmed_control": ["confirm-control"]}},
        )

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
                        "obj": 3,
                    }
                },
                "presets": {"scenes": {"look": {"brightness": 40}}},
            },
            light_factories={"key": FakeFactory(key)},
        )
        plan = rig.integration("key").plan_preset("look", start_seq=4)
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
        self.assertEqual(plan["scene"]["obj"], 3)
        self.assertEqual(plan["command_plan"]["start_seq"], 4)
        self.assertEqual(snapshot["client"]["setup_profile"], {"present": False})

    async def test_async_connection_report_all_returns_fixture_reports(
        self,
    ) -> None:
        key = AsyncFakeLight("key")
        fill = AsyncFakeLight("fill")
        rig = async_rig_from_mapping(
            {
                "fixtures": {
                    "key": {
                        "transport": "ble",
                        "name_contains": "KEY",
                        "tags": ["set"],
                    },
                    "fill": {
                        "transport": "ble",
                        "name_contains": "FILL",
                        "tags": ["set"],
                    },
                }
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )
        calls: list[tuple[LightConnectionConfig, dict[str, object]]] = []

        async def fake_report(
            config: LightConnectionConfig,
            **kwargs: object,
        ) -> dict[str, object]:
            calls.append((config, dict(kwargs)))
            return {
                "ok": True,
                "selected_config": config.to_dict(),
                "summary": {
                    "selected_transport": "ble",
                    "candidate_count": 1,
                    "confirmed_count": 1,
                    "ble_blocker": None,
                },
                "selected": {"transport": "ble"},
                "best_confirmed": {"transport": "ble"},
            }

        with patch(
            "zhiyun_light_control.integration.local_async_connection_report",
            side_effect=fake_report,
        ):
            response = await rig.connection_report_all(
                tag="set",
                include_ble=True,
                persistent=True,
            )

        self.assertEqual(response["action"], "rig_connection_report")
        self.assertTrue(response["applied"])
        self.assertEqual(response["fixtures"]["key"]["transport"], "ble")
        self.assertEqual(
            response["fixtures"]["fill"]["config"]["name_contains"],
            "FILL",
        )
        self.assertEqual(
            [config.name_contains for config, _kwargs in calls],
            ["KEY", "FILL"],
        )
        self.assertTrue(all(kwargs["include_ble"] for _config, kwargs in calls))
        self.assertTrue(all(kwargs["persistent"] for _config, kwargs in calls))

    async def test_async_setup_report_all_returns_fixture_setup_evidence(
        self,
    ) -> None:
        key = AsyncFakeLight("key")
        fill = AsyncFakeLight("fill")
        rig = async_rig_from_mapping(
            {
                "fixtures": {
                    "key": {
                        "transport": "ble",
                        "name_contains": "KEY",
                        "obj": 4,
                        "tags": ["set"],
                    },
                    "fill": {
                        "transport": "ble",
                        "name_contains": "FILL",
                        "obj": 5,
                        "tags": ["set"],
                    },
                },
                "control_mode": 0x33,
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )
        calls: list[tuple[LightConnectionConfig, dict[str, object]]] = []

        async def fake_report(
            config: LightConnectionConfig,
            **kwargs: object,
        ) -> dict[str, object]:
            calls.append((config, dict(kwargs)))
            return {
                "ok": True,
                "config": config.to_dict(),
                "route_confirmed": True,
                "require_confirmed_route": True,
                "status_ok": True,
                "status_error": None,
                "ready_for": {
                    "read_status": True,
                    "control_writes": False,
                },
                "validation_ready_for": {
                    "read_status": True,
                    "control_writes": False,
                },
                "validation_unconfirmed": ["set_brightness"],
                "summary": {"ok": True, "errors": []},
            }

        with patch(
            "zhiyun_light_control.integration.local_async_setup_report",
            side_effect=fake_report,
        ):
            response = await rig.setup_report_all(
                tag="set",
                include_ble=True,
                persistent=True,
                include_object_reads=True,
            )

        self.assertEqual(response["action"], "rig_setup_report")
        self.assertTrue(response["applied"])
        self.assertEqual(
            response["fixtures"]["key"]["config"]["name_contains"],
            "KEY",
        )
        self.assertTrue(
            response["fixtures"]["fill"]["ready_for"]["read_status"],
        )
        self.assertEqual(
            [config.name_contains for config, _kwargs in calls],
            ["KEY", "FILL"],
        )
        self.assertEqual([kwargs["obj"] for _config, kwargs in calls], [4, 5])
        self.assertTrue(all(kwargs["include_ble"] for _config, kwargs in calls))
        self.assertTrue(all(kwargs["persistent"] for _config, kwargs in calls))
        self.assertTrue(
            all(kwargs["include_object_reads"] for _config, kwargs in calls)
        )

    async def test_async_setup_profiles_all_materializes_profiled_rig(
        self,
    ) -> None:
        key = AsyncFakeLight("key")
        fill = AsyncFakeLight("fill")
        rig = async_rig_from_mapping(
            {
                "fixtures": {
                    "key": {
                        "transport": "ble",
                        "name_contains": "KEY",
                        "obj": 4,
                        "tags": ["set"],
                    },
                    "fill": {
                        "transport": "ble",
                        "name_contains": "FILL",
                        "obj": 5,
                        "tags": ["set"],
                    },
                }
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )

        async def fake_report(
            config: LightConnectionConfig,
            **kwargs: object,
        ) -> dict[str, object]:
            del kwargs
            return {
                "ok": True,
                "config": config.to_dict(),
                "route_confirmed": True,
                "require_confirmed_route": True,
                "status_ok": True,
                "status_error": None,
                "ready_for": {
                    "read_status": True,
                    "control_writes": False,
                },
                "validation_ready_for": {
                    "read_status": True,
                    "control_writes": False,
                },
                "validation_unconfirmed": ["set_brightness"],
                "summary": {"ok": True, "errors": []},
            }

        with patch(
            "zhiyun_light_control.integration.local_async_setup_report",
            side_effect=fake_report,
        ):
            profiles = await rig.setup_profiles_all(tag="set", include_ble=True)

        self.assertEqual(set(profiles), {"key", "fill"})
        self.assertEqual(profiles["key"].config.name_contains, "KEY")
        self.assertTrue(profiles["fill"].ready("read_status"))

        profiled = rig.with_setup_profiles(
            profiles,
            require_setup_profile_controls=True,
        )
        self.assertTrue(profiled.require_setup_profile_controls)
        self.assertEqual(profiled.fixture("fill").config.name_contains, "FILL")
        self.assertEqual(profiled.fixture("key").obj, 4)
        self.assertTrue(profiled.require_setup_profile("key", "read_status").ok)

    async def test_async_setup_profile_bundle_runs_full_setup_flow(
        self,
    ) -> None:
        key = AsyncFakeLight("key")
        fill = AsyncFakeLight("fill")
        rig = async_rig_from_mapping(
            {
                "fixtures": {
                    "key": {
                        "transport": "ble",
                        "name_contains": "KEY",
                        "obj": 4,
                        "tags": ["set"],
                    },
                    "fill": {
                        "transport": "ble",
                        "name_contains": "FILL",
                        "obj": 5,
                        "tags": ["set"],
                    },
                }
            },
            light_factories={
                "key": FakeFactory(key),
                "fill": FakeFactory(fill),
            },
        )
        calls: list[tuple[LightConnectionConfig, dict[str, object]]] = []

        async def fake_report(
            config: LightConnectionConfig,
            **kwargs: object,
        ) -> dict[str, object]:
            calls.append((config, dict(kwargs)))
            return {
                "ok": True,
                "config": config.to_dict(),
                "route_confirmed": True,
                "require_confirmed_route": True,
                "status_ok": True,
                "status_error": None,
                "ready_for": {
                    "read_status": True,
                    "control_writes": False,
                },
                "validation_ready_for": {
                    "read_status": True,
                    "control_writes": False,
                },
                "validation_unconfirmed": ["set_brightness"],
                "summary": {"ok": True, "errors": []},
            }

        with patch(
            "zhiyun_light_control.integration.local_async_setup_report",
            side_effect=fake_report,
        ):
            bundle = await rig.setup_profile_bundle(
                tag="set",
                include_ble=True,
                persistent=True,
                profile_dir="setup",
                require_setup_profile_controls=True,
            )

        self.assertEqual(
            [config.name_contains for config, _kwargs in calls],
            ["KEY", "FILL"],
        )
        self.assertTrue(all(kwargs["include_ble"] for _config, kwargs in calls))
        self.assertTrue(all(kwargs["persistent"] for _config, kwargs in calls))
        self.assertTrue(bundle["require_setup_profile_controls"])
        self.assertEqual(bundle["fixtures"][0]["profile_path"], "setup/key.json")
        self.assertEqual(bundle["fixtures"][1]["profile_path"], "setup/fill.json")


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


def setup_profile(
    config: LightConnectionConfig | None = None,
    *,
    control_writes: bool = False,
) -> LightSetupProfile:
    resolved = config or LightConnectionConfig.usb(
        port="/dev/cu.usbmodem21301",
        persistent=True,
    )
    return LightSetupProfile.from_setup_report(
        {
            "api": "zhiyun-light-control",
            "ok": True,
            "config": resolved.to_dict(),
            "route_confirmed": True,
            "status_ok": True,
            "ready_for": {"read_status": True},
            "validation_ready_for": {"control_writes": control_writes},
            "validation_unconfirmed": []
            if control_writes
            else ["set_brightness"],
        }
    )


if __name__ == "__main__":
    unittest.main()
