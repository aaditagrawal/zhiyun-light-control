from __future__ import annotations

import unittest
from unittest.mock import patch

from zhiyun_light_control import (
    AsyncLightIntegration,
    CueLibrary,
    IntegrationNotReady,
    LightConnectionCandidate,
    LightConnectionConfig,
    LightIntegration,
    PersistentLightFactory,
    Scene,
    ScenePresetLibrary,
    integration_pending_action_ids,
    integration_ready,
    integration_ready_for,
    integration_require,
    integration_warnings,
    local_async_devices,
    local_async_integration_snapshot,
    local_async_readiness,
    local_async_status_snapshot,
    local_async_usb_discovery,
    local_async_validation,
    local_ble_endpoint_connection_candidates,
    local_capabilities,
    local_connection_candidates,
    local_devices,
    local_error_status,
    local_integration_snapshot,
    local_manifest,
    local_probe_connection_candidates,
    local_readiness,
    local_status_snapshot,
    local_usb_discovery,
    local_validation,
)
from zhiyun_light_control.models import CommandResult
from zhiyun_light_control.protocol import (
    RUNTIME_TYPE,
    UPDATER_DEVICE,
    RuntimeCommand,
    UpdaterCommand,
    build_frame,
    first_frame,
)


class FakeStatusLight:
    def __enter__(self) -> FakeStatusLight:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        del payload, timeout
        payload_by_cmd = {
            RuntimeCommand.DEVICE_INFO: b"device-test\x00pl103\x00",
            RuntimeCommand.FIRMWARE: b"1.6.4\x00",
            RuntimeCommand.VOLTAGE: b"\x65",
            RuntimeCommand.DEVICE_ID: b"\x01\x00",
        }
        return _result(RUNTIME_TYPE, cmd, payload_by_cmd[cmd])

    def exchange_updater(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        del payload, timeout
        payload_by_cmd = {
            UpdaterCommand.CHIP_SYNC: bytes.fromhex(
                "0048444c0000010010030041054008a40065a36075"
            ),
            UpdaterCommand.READ_SN: bytes.fromhex("004105130110c1e009a408"),
        }
        return _result(UPDATER_DEVICE, cmd, payload_by_cmd[cmd])


class AsyncFakeStatusLight:
    async def __aenter__(self) -> AsyncFakeStatusLight:
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return

    async def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        del payload, timeout
        payload_by_cmd = {
            RuntimeCommand.DEVICE_INFO: b"device-test\x00pl103\x00",
            RuntimeCommand.FIRMWARE: b"1.6.4\x00",
            RuntimeCommand.VOLTAGE: b"\x65",
            RuntimeCommand.DEVICE_ID: b"\x01\x00",
        }
        return _result(RUNTIME_TYPE, cmd, payload_by_cmd[cmd])

    async def exchange_updater(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        del payload, timeout
        payload_by_cmd = {
            UpdaterCommand.CHIP_SYNC: bytes.fromhex(
                "0048444c0000010010030041054008a40065a36075"
            ),
            UpdaterCommand.READ_SN: bytes.fromhex("004105130110c1e009a408"),
        }
        return _result(UPDATER_DEVICE, cmd, payload_by_cmd[cmd])


class FakeProbe:
    def to_dict(self) -> dict[str, object]:
        return {
            "firmware": "1.6.4",
            "device_identifier": "id",
            "generation": "pl103",
            "voltage_status": 101,
            "device_id": 1,
            "port": "/dev/cu.test",
        }


class FakeValidationLight:
    def __init__(self, *, acknowledged: bool = True) -> None:
        self.acknowledged = acknowledged
        self.payloads: list[tuple[int, bytes]] = []

    def __enter__(self) -> FakeValidationLight:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def probe(self) -> FakeProbe:
        return FakeProbe()

    def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        del timeout
        self.payloads.append((cmd, payload))
        if not self.acknowledged:
            tx = build_frame(RUNTIME_TYPE, len(self.payloads), cmd, payload)
            return CommandResult(cmd, tx, b"", (), None)
        return _result(RUNTIME_TYPE, cmd, b"\x00")

    def exchange_updater(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        del payload, timeout
        return _result(UPDATER_DEVICE, cmd, b"\x00")


class FakeSceneLight:
    def __init__(self) -> None:
        self.scenes: list[Scene] = []
        self.control_modes: list[int] = []
        self.primitive_calls: list[tuple[str, int, object, int]] = []

    def __enter__(self) -> FakeSceneLight:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return

    def apply_scene(
        self,
        scene: Scene,
        *,
        control_mode: int = 0x33,
    ) -> list[CommandResult]:
        self.scenes.append(scene)
        self.control_modes.append(control_mode)
        return [_result(RUNTIME_TYPE, RuntimeCommand.BRIGHTNESS, b"\x00")]

    def set_brightness(
        self,
        obj: int,
        value: float,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("brightness", obj, value, control_mode))
        return _result(RUNTIME_TYPE, RuntimeCommand.BRIGHTNESS, b"\x00")

    def set_cct(
        self,
        obj: int,
        kelvin: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("cct", obj, kelvin, control_mode))
        return _result(RUNTIME_TYPE, RuntimeCommand.CCT, b"\x00")

    def set_sleep(
        self,
        obj: int,
        value: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("sleep", obj, value, control_mode))
        return _result(RUNTIME_TYPE, RuntimeCommand.SLEEP, b"\x00")

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
        return _result(RUNTIME_TYPE, RuntimeCommand.RGB, b"\x00")

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
        return _result(RUNTIME_TYPE, RuntimeCommand.HSI, b"\x00")


class FakeControlStatusLight(FakeSceneLight, FakeStatusLight):
    def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        if cmd == RuntimeCommand.BRIGHTNESS:
            return _result(RUNTIME_TYPE, cmd, bytes.fromhex("02000000000c42"))
        if cmd == RuntimeCommand.CCT:
            return _result(RUNTIME_TYPE, cmd, bytes.fromhex("020000e015"))
        if cmd == RuntimeCommand.SLEEP:
            return _result(RUNTIME_TYPE, cmd, bytes.fromhex("02000000"))
        if cmd == RuntimeCommand.REGISTER_DEFAULT_GROUP:
            del payload
            return _result(RUNTIME_TYPE, cmd, b"\x00")
        return FakeStatusLight.exchange_runtime(self, cmd, payload, timeout=timeout)


class CountingControlStatusLight(FakeControlStatusLight):
    def __init__(self) -> None:
        super().__init__()
        self.enter_count = 0
        self.exit_count = 0

    def __enter__(self) -> CountingControlStatusLight:
        self.enter_count += 1
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.exit_count += 1


class AsyncFakeValidationLight:
    def __init__(self, *, acknowledged: bool = True) -> None:
        self.acknowledged = acknowledged
        self.payloads: list[tuple[int, bytes]] = []

    async def __aenter__(self) -> AsyncFakeValidationLight:
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return

    async def probe(self) -> FakeProbe:
        return FakeProbe()

    async def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        del timeout
        self.payloads.append((cmd, payload))
        if not self.acknowledged:
            tx = build_frame(RUNTIME_TYPE, len(self.payloads), cmd, payload)
            return CommandResult(cmd, tx, b"", (), None)
        return _result(RUNTIME_TYPE, cmd, b"\x00")


class AsyncFakeSceneLight:
    def __init__(self) -> None:
        self.scenes: list[Scene] = []
        self.control_modes: list[int] = []
        self.primitive_calls: list[tuple[str, int, object, int]] = []

    async def __aenter__(self) -> AsyncFakeSceneLight:
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return

    async def apply_scene(
        self,
        scene: Scene,
        *,
        control_mode: int = 0x33,
    ) -> list[CommandResult]:
        self.scenes.append(scene)
        self.control_modes.append(control_mode)
        return [_result(RUNTIME_TYPE, RuntimeCommand.BRIGHTNESS, b"\x00")]

    async def set_brightness(
        self,
        obj: int,
        value: float,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("brightness", obj, value, control_mode))
        return _result(RUNTIME_TYPE, RuntimeCommand.BRIGHTNESS, b"\x00")

    async def set_cct(
        self,
        obj: int,
        kelvin: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("cct", obj, kelvin, control_mode))
        return _result(RUNTIME_TYPE, RuntimeCommand.CCT, b"\x00")

    async def set_sleep(
        self,
        obj: int,
        value: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("sleep", obj, value, control_mode))
        return _result(RUNTIME_TYPE, RuntimeCommand.SLEEP, b"\x00")

    async def set_rgb(
        self,
        obj: int,
        red: int,
        green: int,
        blue: int,
        *,
        control_mode: int = 0x33,
    ) -> CommandResult:
        self.primitive_calls.append(("rgb", obj, (red, green, blue), control_mode))
        return _result(RUNTIME_TYPE, RuntimeCommand.RGB, b"\x00")

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
        return _result(RUNTIME_TYPE, RuntimeCommand.HSI, b"\x00")


class AsyncFakeControlStatusLight(AsyncFakeSceneLight, AsyncFakeStatusLight):
    async def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        if cmd == RuntimeCommand.BRIGHTNESS:
            return _result(RUNTIME_TYPE, cmd, bytes.fromhex("02000000000c42"))
        if cmd == RuntimeCommand.CCT:
            return _result(RUNTIME_TYPE, cmd, bytes.fromhex("020000e015"))
        if cmd == RuntimeCommand.SLEEP:
            return _result(RUNTIME_TYPE, cmd, bytes.fromhex("02000000"))
        if cmd == RuntimeCommand.REGISTER_DEFAULT_GROUP:
            del payload
            return _result(RUNTIME_TYPE, cmd, b"\x00")
        return await AsyncFakeStatusLight.exchange_runtime(
            self,
            cmd,
            payload,
            timeout=timeout,
        )


class FailingLight:
    def __enter__(self) -> FailingLight:
        raise RuntimeError("port busy")

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return


class AsyncFailingLight:
    async def __aenter__(self) -> AsyncFailingLight:
        raise RuntimeError("adapter busy")

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return


class FakeFactory:
    def __init__(self, light: object) -> None:
        self.light = light

    def __call__(self) -> object:
        return self.light


class FakeSyncContext:
    def __init__(self) -> None:
        self.enter_count = 0
        self.exit_count = 0

    def __enter__(self) -> FakeSyncContext:
        self.enter_count += 1
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.exit_count += 1


class FakePayload:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, object]:
        return dict(self.payload)


class AsyncFakeFactory:
    def __init__(self, light: object) -> None:
        self.light = light

    def __call__(self) -> object:
        return self.light


class IntegrationTests(unittest.TestCase):
    def test_local_status_snapshot_reads_configured_light(self) -> None:
        status, confirmed, error = local_status_snapshot(
            LightConnectionConfig(transport="usb", port="/dev/cu.test"),
            light_factory=FakeFactory(FakeStatusLight()),
        )

        self.assertTrue(confirmed)
        self.assertIsNone(error)
        self.assertEqual(status["transport"], "usb")
        self.assertEqual(status["firmware"], "1.6.4")
        self.assertEqual(status["generation"], "pl103")
        self.assertEqual(status["read_sn"]["device_identifier"], "08a409e0c1100113")

    def test_local_status_snapshot_closes_owned_persistent_factory(self) -> None:
        context = FakeSyncContext()
        factory = PersistentLightFactory(lambda: context)

        with patch(
            "zhiyun_light_control.integration.make_light_factory",
            return_value=factory,
        ):
            status, confirmed, error = local_status_snapshot(
                LightConnectionConfig(transport="usb", persistent=True),
                status_reader=lambda _light: FakePayload(
                    {"connection_confirmed": True}
                ),
            )

        self.assertTrue(confirmed)
        self.assertIsNone(error)
        self.assertEqual(status["connection_confirmed"], True)
        self.assertEqual(context.enter_count, 1)
        self.assertEqual(context.exit_count, 1)

    def test_local_readiness_builds_programmatic_preflight_payload(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_devices(**kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {
                "usb": {
                    "available": True,
                    "selected_port": kwargs["configured_usb_port"],
                    "ports": [],
                },
                "ble": {"macos_status": None, "scan": None},
            }

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            side_effect=fake_devices,
        ):
            payload = local_readiness(
                LightConnectionConfig(transport="usb", port="/dev/cu.test"),
                allow_control=True,
                light_factory=FakeFactory(FakeStatusLight()),
            )

        self.assertTrue(payload["connection_confirmed"])
        self.assertTrue(payload["ready_for"]["read_status"])
        self.assertTrue(payload["ready_for"]["control_requests"])
        self.assertEqual(payload["bridge"]["transport"], "usb")
        self.assertEqual(payload["devices"]["usb"]["selected_port"], "/dev/cu.test")
        self.assertEqual(calls[0]["configured_transport"], "usb")
        self.assertEqual(calls[0]["configured_usb_port"], "/dev/cu.test")
        self.assertFalse(calls[0]["include_ble"])

    def test_local_snapshot_includes_manifest_capabilities_and_summary(self) -> None:
        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {
                    "available": True,
                    "selected_port": "/dev/cu.test",
                    "ports": [],
                },
                "ble": {"macos_status": None, "scan": None},
            },
        ):
            snapshot = local_integration_snapshot(
                LightConnectionConfig(transport="usb", port="/dev/cu.test"),
                allow_control=True,
                presets=["key"],
                cues=["intro"],
                light_factory=FakeFactory(FakeStatusLight()),
            )

        self.assertEqual(snapshot["summary"]["transport"], "usb")
        self.assertTrue(snapshot["summary"]["connection_confirmed"])
        self.assertEqual(snapshot["summary"]["selected_usb_port"], "/dev/cu.test")
        manifest = snapshot["payloads"]["manifest"]
        capabilities = snapshot["payloads"]["capabilities"]
        self.assertEqual(manifest["presets"], ["key"])
        self.assertEqual(manifest["cues"], ["intro"])
        self.assertEqual(capabilities["presets"], ["key"])
        self.assertEqual(capabilities["cues"], ["intro"])

    def test_light_integration_facade_wraps_common_payloads(self) -> None:
        integration = LightIntegration(
            config=LightConnectionConfig(transport="ble", ble_backend="macos-app"),
            allow_control=False,
            preset_names=("key",),
            cue_names=("intro",),
            light_factory=FakeFactory(FakeStatusLight()),
        )

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {
                    "macos_status": {"ok": True, "authorization": "allowed"},
                    "scan": None,
                },
            },
        ) as devices:
            readiness = integration.readiness()
            snapshot = integration.snapshot(include_ble=True)

        self.assertEqual(
            integration.manifest()["transport"]["ble_backend"],
            "macos-app",
        )
        self.assertEqual(integration.capabilities()["cues"], ["intro"])
        self.assertTrue(readiness["connection_confirmed"])
        self.assertEqual(snapshot["payloads"]["manifest"]["presets"], ["key"])
        self.assertTrue(devices.call_args_list[0].kwargs["include_ble_status"])
        self.assertTrue(devices.call_args_list[1].kwargs["include_ble"])

    def test_light_integration_readiness_guards_fail_closed(self) -> None:
        integration = LightIntegration(
            config=LightConnectionConfig(transport="usb", port="/dev/cu.test"),
            allow_control=False,
            light_factory=FakeFactory(FakeStatusLight()),
        )

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {
                    "available": True,
                    "selected_port": "/dev/cu.test",
                    "ports": [],
                },
                "ble": {"macos_status": None, "scan": None},
            },
        ):
            self.assertTrue(integration.ready("read_status"))
            self.assertEqual(
                integration.pending_action_ids(capability="control_requests"),
                ["enable-control"],
            )
            with self.assertRaises(IntegrationNotReady) as error:
                integration.require_control_ready()

        self.assertEqual(error.exception.capabilities, ("control_requests",))
        self.assertEqual(
            error.exception.pending_action_ids,
            {"control_requests": ["enable-control"]},
        )
        self.assertIn("Write endpoints are disabled", error.exception.warnings[0])

    def test_integration_readiness_helpers_accept_snapshots(self) -> None:
        snapshot = {
            "payloads": {
                "ready": {
                    "ready_for": {
                        "read_status": True,
                        "confirmed_control": False,
                    },
                    "requirements": {
                        "confirmed_control": {
                            "pending_actions": ["confirm-control"]
                        }
                    },
                    "warnings": ["No ACK-confirmed control request is recorded yet."],
                }
            }
        }

        self.assertTrue(integration_ready(snapshot, "read_status"))
        self.assertFalse(integration_ready_for(snapshot)["confirmed_control"])
        self.assertEqual(
            integration_pending_action_ids(snapshot),
            ["confirm-control"],
        )
        self.assertEqual(
            integration_warnings(snapshot),
            ["No ACK-confirmed control request is recorded yet."],
        )
        with self.assertRaises(IntegrationNotReady):
            integration_require(snapshot, ("confirmed_control",))

    def test_light_integration_devices_use_configured_backend(self) -> None:
        integration = LightIntegration(
            config=LightConnectionConfig(
                transport="ble",
                port="/dev/cu.test",
                name_contains="PL103",
                ble_backend="macos-app",
                timeout=2.0,
            )
        )

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {
                    "available": True,
                    "selected_port": "/dev/cu.test",
                    "ports": [],
                },
                "ble": {"macos_status": {"state": "powered_on"}, "scan": {}},
            },
        ) as devices:
            payload = integration.devices(include_ble=True)

        self.assertEqual(payload["usb"]["selected_port"], "/dev/cu.test")
        kwargs = devices.call_args.kwargs
        self.assertEqual(kwargs["configured_transport"], "ble")
        self.assertEqual(kwargs["configured_usb_port"], "/dev/cu.test")
        self.assertEqual(kwargs["ble_backend"], "macos-app")
        self.assertEqual(kwargs["ble_timeout"], 2.0)
        self.assertEqual(kwargs["ble_name_contains"], "PL103")
        self.assertTrue(kwargs["include_ble"])
        self.assertTrue(kwargs["include_ble_status"])

    def test_light_integration_exposes_usb_discovery_pipeline(self) -> None:
        light = FakeStatusLight()
        integration = LightIntegration(
            config=LightConnectionConfig(
                transport="usb",
                port="/dev/cu.test",
                timeout=1.25,
            ),
            allow_control=True,
            light_factory=FakeFactory(light),
        )

        with patch(
            "zhiyun_light_control.integration.discover_usb_primitives",
            return_value=FakePayload(
                {"transport": "usb", "summary": {"confirmed": 1}},
            ),
        ) as discover:
            payload = integration.discover_usb(
                object_ids=(1,),
                first_words=(0x0100,),
                timeout=0.25,
                allow_control=False,
            )

        self.assertEqual(payload["summary"]["confirmed"], 1)
        self.assertIs(discover.call_args.args[0], light)
        kwargs = discover.call_args.kwargs
        self.assertEqual(kwargs["object_ids"], (1,))
        self.assertEqual(kwargs["first_words"], (0x0100,))
        self.assertEqual(kwargs["timeout"], 0.25)
        self.assertFalse(kwargs["allow_control"])

    def test_local_usb_discovery_requires_usb_transport(self) -> None:
        with self.assertRaisesRegex(ValueError, "transport='usb'"):
            local_usb_discovery(LightConnectionConfig(transport="ble"))

    def test_light_integration_exposes_ble_endpoint_pipeline(self) -> None:
        integration = LightIntegration(
            config=LightConnectionConfig(
                transport="ble",
                ble_backend="macos-app",
                name_contains="PL103",
                timeout=2.0,
            )
        )

        with patch(
            "zhiyun_light_control.integration.inspect_ble_device",
            return_value=FakePayload({"ok": False, "address": None}),
        ) as inspect:
            inspect_payload = integration.inspect_ble()

        self.assertFalse(inspect_payload["ok"])
        self.assertEqual(inspect_payload["backend"], "macos-app")
        self.assertEqual(inspect_payload["timeout"], 2.0)
        self.assertEqual(inspect_payload["name_contains"], "PL103")
        inspect_kwargs = inspect.call_args.kwargs
        self.assertEqual(inspect_kwargs["backend"], "macos-app")
        self.assertEqual(inspect_kwargs["timeout"], 2.0)
        self.assertEqual(inspect_kwargs["name_contains"], "PL103")

        with patch(
            "zhiyun_light_control.integration.test_ble_endpoint_candidates",
            return_value=FakePayload(
                {"ok": True, "confirmed_candidates": [{"profile": "legacy"}]},
            ),
        ) as test_ble:
            test_payload = integration.test_ble_endpoints(max_candidates=2)

        self.assertTrue(test_payload["ok"])
        self.assertEqual(test_payload["confirmed_candidates"][0]["profile"], "legacy")
        test_kwargs = test_ble.call_args.kwargs
        self.assertEqual(test_kwargs["backend"], "macos-app")
        self.assertEqual(test_kwargs["name_contains"], "PL103")
        self.assertEqual(test_kwargs["max_candidates"], 2)

    def test_light_integration_exposes_ranked_connection_routes(self) -> None:
        integration = LightIntegration(
            config=LightConnectionConfig(
                transport="ble",
                port="/dev/cu.configured",
                ble_backend="macos-app",
                name_contains="PL103",
                timeout=2.0,
            ),
            allow_control=True,
            preset_names=("key",),
        )

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {
                    "available": True,
                    "selected_port": "/dev/cu.usbmodem21301",
                    "ports": [
                        {
                            "path": "/dev/cu.usbmodem21301",
                            "selected": True,
                            "metadata": {
                                "vendor_id": 0xFFF8,
                                "product_id": 0x0180,
                                "product_name": "Zhiyun Virtual ComPort",
                            },
                        }
                    ],
                },
                "ble": {
                    "backend": "macos-app",
                    "timeout": 2.0,
                    "scan": {
                        "ok": True,
                        "devices": [
                            {
                                "address": "UUID-1",
                                "name": "PL103_EDFE",
                                "suggested_profile": "legacy",
                            }
                        ],
                    },
                    "macos_status": {"authorization": "allowed"},
                },
            },
        ) as devices:
            candidates = integration.connection_candidates(
                include_ble=True,
                persistent=True,
            )
            best_config = integration.best_connection_config(
                include_ble=True,
                persistent=True,
            )
            configured = integration.with_best_connection(
                include_ble=True,
                persistent=True,
            )

        self.assertEqual(
            [candidate.transport for candidate in candidates],
            ["usb", "ble"],
        )
        self.assertEqual(candidates[0].confidence, "known-usb-descriptor")
        self.assertEqual(candidates[1].confidence, "advertised-profile")
        self.assertEqual(best_config.transport, "usb")
        self.assertEqual(best_config.port, "/dev/cu.usbmodem21301")
        self.assertTrue(best_config.persistent)
        self.assertEqual(configured.config.port, "/dev/cu.usbmodem21301")
        self.assertTrue(configured.allow_control)
        self.assertEqual(configured.preset_names, ("key",))
        self.assertIs(configured.state_tracker, integration.state_tracker)
        self.assertTrue(devices.call_args.kwargs["include_ble"])
        self.assertTrue(devices.call_args.kwargs["include_ble_status"])

    def test_light_integration_probes_connection_routes_with_status_evidence(
        self,
    ) -> None:
        usb_candidate = LightConnectionCandidate(
            config=LightConnectionConfig.usb(port="/dev/cu.usbmodem21301"),
            source="devices.usb",
            confidence="known-usb-descriptor",
            confidence_score=95,
            reason="USB descriptor matches Zhiyun Virtual ComPort",
        )
        ble_candidate = LightConnectionCandidate(
            config=LightConnectionConfig.ble(
                address="UUID-1",
                backend="macos-app",
            ),
            source="devices.ble.scan",
            confidence="advertised-profile",
            confidence_score=85,
            reason="BLE advertisement matched built-in direct profile",
        )
        seen_configs: list[LightConnectionConfig] = []

        def fake_status(config: LightConnectionConfig):
            seen_configs.append(config)
            if config.transport == "usb":
                return (
                    {
                        "transport": "usb",
                        "connection_confirmed": True,
                        "firmware": "1.6.4",
                        "port": config.port,
                    },
                    True,
                    None,
                )
            return (
                {"ok": False, "error": "Bluetooth state unauthorized: 3"},
                False,
                "Bluetooth state unauthorized: 3",
            )

        integration = LightIntegration(allow_control=True, preset_names=("key",))
        with (
            patch(
                "zhiyun_light_control.integration.local_connection_candidates",
                return_value=(ble_candidate, usb_candidate),
            ) as candidates,
            patch(
                "zhiyun_light_control.integration.local_status_snapshot",
                side_effect=fake_status,
            ),
        ):
            routes = integration.probe_connection_candidates(
                include_ble=True,
                persistent=True,
            )
            confirmed = integration.confirmed_connection_candidates(
                include_ble=True,
                persistent=True,
            )
            best_config = integration.best_confirmed_connection_config(
                include_ble=True,
                persistent=True,
            )
            configured = integration.with_confirmed_connection(
                include_ble=True,
                persistent=True,
            )

        self.assertEqual(
            [route.confidence for route in routes],
            ["status-confirmed", "status-unconfirmed"],
        )
        self.assertEqual(routes[0].config.port, "/dev/cu.usbmodem21301")
        self.assertEqual(routes[0].evidence["status_probe"]["firmware"], "1.6.4")
        self.assertTrue(routes[0].evidence["status_probe"]["connection_confirmed"])
        self.assertEqual(
            routes[1].evidence["status_probe"]["error"],
            "Bluetooth state unauthorized: 3",
        )
        self.assertEqual(confirmed, (routes[0],))
        self.assertEqual(best_config.port, "/dev/cu.usbmodem21301")
        self.assertEqual(configured.config.port, "/dev/cu.usbmodem21301")
        self.assertTrue(configured.allow_control)
        self.assertEqual(configured.preset_names, ("key",))
        self.assertIs(configured.state_tracker, integration.state_tracker)
        self.assertEqual(
            [config.transport for config in seen_configs],
            ["ble", "usb", "ble", "usb", "ble", "usb", "ble", "usb"],
        )
        self.assertEqual(candidates.call_args.kwargs["include_ble"], True)
        self.assertEqual(candidates.call_args.kwargs["persistent"], True)

    def test_light_integration_exposes_ble_endpoint_connection_routes(self) -> None:
        integration = LightIntegration(
            config=LightConnectionConfig(
                transport="ble",
                ble_backend="macos-app",
                name_contains="PL103",
                timeout=2.0,
            ),
            cue_names=("intro",),
        )
        endpoint_report = {
            "ok": True,
            "backend": "macos-app",
            "timeout": 2.0,
            "address": "UUID-1",
            "name_contains": "PL103",
            "confirmed_candidates": [
                {
                    "profile": "direct",
                    "service_uuid": "service-uuid",
                    "write_uuid": "write-uuid",
                    "notify_uuid": "notify-uuid",
                }
            ],
            "tests": [],
        }

        with patch(
            "zhiyun_light_control.integration.test_ble_endpoint_candidates",
            return_value=FakePayload(endpoint_report),
        ) as test_ble:
            candidates = integration.ble_endpoint_connection_candidates(
                persistent=True,
            )
            best_config = integration.best_ble_endpoint_config()
            configured = integration.with_ble_endpoint_connection()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].confidence, "confirmed-endpoint")
        self.assertEqual(candidates[0].transport, "ble")
        self.assertEqual(candidates[0].config.address, "UUID-1")
        self.assertEqual(candidates[0].config.ble_backend, "macos-app")
        self.assertEqual(candidates[0].config.ble_service_uuid, "service-uuid")
        self.assertEqual(candidates[0].config.ble_write_uuid, "write-uuid")
        self.assertEqual(candidates[0].config.ble_notify_uuid, "notify-uuid")
        self.assertTrue(candidates[0].config.persistent)
        self.assertEqual(best_config.transport, "ble")
        self.assertEqual(configured.config.ble_notify_uuid, "notify-uuid")
        self.assertEqual(configured.cue_names, ("intro",))
        self.assertIs(configured.state_tracker, integration.state_tracker)
        self.assertEqual(test_ble.call_args.kwargs["backend"], "macos-app")
        self.assertEqual(test_ble.call_args.kwargs["timeout"], 2.0)
        self.assertEqual(test_ble.call_args.kwargs["name_contains"], "PL103")

    def test_local_connection_candidate_helpers_use_integration_defaults(self) -> None:
        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {
                    "available": True,
                    "selected_port": "/dev/cu.usbmodem21301",
                    "ports": [{"path": "/dev/cu.usbmodem21301", "selected": True}],
                },
                "ble": {"scan": None},
            },
        ):
            candidates = local_connection_candidates(
                LightConnectionConfig(transport="usb"),
            )

        self.assertEqual(candidates[0].config.transport, "usb")

        with patch(
            "zhiyun_light_control.integration.test_ble_endpoint_candidates",
            return_value=FakePayload(
                {
                    "backend": "worker",
                    "address": "UUID-1",
                    "confirmed_candidates": [
                        {
                            "profile": "direct",
                            "service_uuid": "service-uuid",
                            "write_uuid": "write-uuid",
                            "notify_uuid": "notify-uuid",
                        }
                    ],
                }
            ),
        ):
            ble_candidates = local_ble_endpoint_connection_candidates(
                LightConnectionConfig(transport="ble", address="UUID-1"),
            )

        self.assertEqual(ble_candidates[0].config.ble_write_uuid, "write-uuid")

        with (
            patch(
                "zhiyun_light_control.integration.local_connection_candidates",
                return_value=(
                    LightConnectionCandidate(
                        config=LightConnectionConfig.usb(port="/dev/cu.test"),
                        source="devices.usb",
                        confidence="serial-port",
                        confidence_score=70,
                        reason="USB serial port is available",
                    ),
                ),
            ),
            patch(
                "zhiyun_light_control.integration.local_status_snapshot",
                return_value=(
                    {
                        "transport": "usb",
                        "connection_confirmed": True,
                        "firmware": "1.6.4",
                    },
                    True,
                    None,
                ),
            ),
        ):
            probed = local_probe_connection_candidates(
                LightConnectionConfig(transport="usb"),
            )

        self.assertEqual(probed[0].confidence, "status-confirmed")
        self.assertEqual(probed[0].evidence["status_probe"]["firmware"], "1.6.4")

    def test_local_devices_can_override_ble_status_probe(self) -> None:
        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {"macos_status": None, "scan": None},
            },
        ) as devices:
            payload = local_devices(
                LightConnectionConfig(transport="ble", ble_backend="macos-app"),
                include_ble_status=False,
            )

        self.assertFalse(devices.call_args.kwargs["include_ble_status"])
        self.assertFalse(payload["usb"]["available"])

    def test_local_validation_runs_configured_transport_report(self) -> None:
        payload = local_validation(
            LightConnectionConfig(transport="usb", port="/dev/cu.test"),
            include_object_reads=True,
            light_factory=FakeFactory(FakeValidationLight()),
        )

        self.assertEqual(payload["transport"], "usb")
        self.assertTrue(payload["connection_confirmed"])
        self.assertTrue(payload["all_attempted_confirmed"])
        self.assertTrue(payload["summary"]["ready_for"]["object_reads"])
        self.assertIn("read_brightness", [check["name"] for check in payload["checks"]])

    def test_light_integration_validate_uses_control_policy(self) -> None:
        light = FakeValidationLight(acknowledged=False)
        integration = LightIntegration(
            config=LightConnectionConfig(transport="usb", port="/dev/cu.test"),
            allow_control=True,
            light_factory=FakeFactory(light),
        )

        payload = integration.validate(control_mode=0x01)

        self.assertTrue(payload["control_enabled"])
        self.assertIn("set_sleep", payload["unconfirmed"])
        self.assertFalse(payload["summary"]["ready_for"]["control_writes"])
        payloads = dict(light.payloads)
        self.assertEqual(payloads[RuntimeCommand.SLEEP][2], 0x01)
        self.assertEqual(payloads[RuntimeCommand.BRIGHTNESS][2], 0x01)

    def test_light_integration_controller_reuses_configured_transport(self) -> None:
        light = FakeSceneLight()
        integration = LightIntegration(
            config=LightConnectionConfig(transport="usb", port="/dev/cu.test"),
            light_factory=FakeFactory(light),
        )

        controller = integration.controller(
            control_mode=0x01,
            require_acknowledged=True,
        )
        payload = controller.apply_scene({"obj": 1, "brightness": 45})

        self.assertTrue(controller.require_acknowledged)
        self.assertEqual(payload["action"], "scene")
        self.assertEqual(light.control_modes, [0x01])
        self.assertEqual(light.scenes[0].brightness, 45)
        self.assertEqual(integration.state_snapshot()["version"], 1)
        self.assertEqual(integration.state()["scene"]["brightness"], 45)

    def test_light_integration_runs_primitive_controls_directly(self) -> None:
        light = FakeControlStatusLight()
        integration = LightIntegration(
            config=LightConnectionConfig(transport="usb", port="/dev/cu.test"),
            obj=2,
            light_factory=FakeFactory(light),
        )

        register = integration.register(device_id=1, group_id=2)
        read = integration.read_brightness()
        brightness = integration.set_brightness(35, control_mode=0x01)
        cct = integration.set_cct(5600, control_mode=0x01)
        sleep = integration.set_sleep(0, control_mode=0x01)
        rgb = integration.set_rgb(255, 180, 120, control_mode=0x01)
        hsi = integration.set_hsi(30.0, 0.5, 40, control_mode=0x01)

        self.assertTrue(register["acknowledged"])
        self.assertEqual(register["action"], "register")
        self.assertTrue(read["acknowledged"])
        self.assertEqual(read["action"], "read_brightness")
        self.assertEqual(read["value"], 35.0)
        self.assertEqual(read["obj"], 2)
        self.assertTrue(brightness["applied"])
        self.assertEqual(cct["scene"]["kelvin"], 5600)
        self.assertEqual(sleep["scene"]["sleep"], 0)
        self.assertEqual(rgb["scene"]["red"], 255)
        self.assertEqual(hsi["scene"]["hue"], 30.0)
        self.assertEqual(
            light.primitive_calls,
            [
                ("brightness", 2, 35, 0x01),
                ("cct", 2, 5600, 0x01),
                ("sleep", 2, 0, 0x01),
                ("rgb", 2, (255, 180, 120), 0x01),
                ("hsi", 2, (30.0, 0.5, 40), 0x01),
            ],
        )
        self.assertEqual(integration.state_snapshot()["version"], 5)
        self.assertEqual(integration.state()["action"], "set_hsi")

    def test_light_integration_closes_owned_persistent_controller_factory(self) -> None:
        light = CountingControlStatusLight()
        factory = PersistentLightFactory(lambda: light)

        with patch(
            "zhiyun_light_control.controller.make_light_factory",
            return_value=factory,
        ):
            read = LightIntegration(
                config=LightConnectionConfig(transport="usb", persistent=True),
                obj=2,
            ).read_brightness()

        self.assertTrue(read["acknowledged"])
        self.assertEqual(light.enter_count, 1)
        self.assertEqual(light.exit_count, 1)

    def test_light_integration_does_not_close_injected_factory(self) -> None:
        light = CountingControlStatusLight()
        factory = PersistentLightFactory(lambda: light)
        integration = LightIntegration(
            config=LightConnectionConfig(transport="usb", persistent=True),
            obj=2,
            light_factory=factory,
        )

        read = integration.read_brightness()

        self.assertTrue(read["acknowledged"])
        self.assertEqual(light.enter_count, 1)
        self.assertEqual(light.exit_count, 0)
        factory.close()
        self.assertEqual(light.exit_count, 1)

    def test_light_integration_runs_controls_directly(self) -> None:
        light = FakeControlStatusLight()
        presets = ScenePresetLibrary.from_mapping({"key": {"brightness": 25}})
        cues = CueLibrary.from_mapping({"intro": {"steps": [{"preset": "key"}]}})
        integration = LightIntegration(
            config=LightConnectionConfig(transport="usb", port="/dev/cu.test"),
            obj=2,
            light_factory=FakeFactory(light),
            preset_library=presets,
            cue_library=cues,
        )

        scene = integration.apply_scene({"brightness": 12}, control_mode=0x01)
        preset = integration.apply_preset(
            "key",
            overrides={"brightness": 30},
            control_mode=0x01,
        )
        sequence = integration.run_sequence(
            [{"scene": {"brightness": 14}}, {"preset": "key"}],
            control_mode=0x01,
        )
        cue = integration.run_named_cue("intro", control_mode=0x01)

        self.assertTrue(scene["applied"])
        self.assertTrue(preset["applied"])
        self.assertTrue(sequence["applied"])
        self.assertTrue(cue["applied"])
        self.assertEqual(
            [sent.obj for sent in light.scenes],
            [2, 2, 2, 2, 2],
        )
        self.assertEqual(
            [sent.brightness for sent in light.scenes],
            [12.0, 30.0, 14.0, 25.0, 25.0],
        )
        self.assertEqual(light.control_modes, [0x01, 0x01, 0x01, 0x01, 0x01])
        self.assertEqual(integration.state_snapshot()["version"], 4)
        self.assertEqual(integration.state()["action"], "cue")
        self.assertEqual(integration.state()["scene"]["brightness"], 25.0)
        history = integration.state_history(limit=2)
        self.assertEqual(
            [event["version"] for event in history["events"]],
            [3, 4],
        )
        self.assertEqual(
            integration.wait_for_state_update(3, timeout=0)["state"]["action"],
            "cue",
        )
        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {
                    "available": True,
                    "selected_port": "/dev/cu.test",
                    "ports": [],
                },
                "ble": {"macos_status": None, "scan": None},
            },
        ):
            readiness = integration.readiness()
            snapshot = integration.snapshot()

        self.assertEqual(readiness["state"]["version"], 4)
        self.assertEqual(readiness["state"]["snapshot"]["action"], "cue")
        self.assertTrue(readiness["ready_for"]["confirmed_control"])
        self.assertEqual(snapshot["payloads"]["ready"]["state"]["version"], 4)

    def test_light_integration_control_readiness_guard_fails_closed(self) -> None:
        integration = LightIntegration(
            config=LightConnectionConfig(transport="usb", port="/dev/cu.test"),
            allow_control=False,
            light_factory=FakeFactory(FakeStatusLight()),
        )

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {
                    "available": True,
                    "selected_port": "/dev/cu.test",
                    "ports": [],
                },
                "ble": {"macos_status": None, "scan": None},
            },
        ), self.assertRaises(IntegrationNotReady) as error:
            integration.apply_scene({"brightness": 10}, require_ready=True)

        self.assertEqual(error.exception.capabilities, ("control_requests",))
        self.assertEqual(
            error.exception.pending_action_ids,
            {"control_requests": ["enable-control"]},
        )

    def test_light_integration_plans_sdk_primitives_without_opening_light(
        self,
    ) -> None:
        presets = ScenePresetLibrary.from_mapping(
            {"key": {"brightness": 25, "kelvin": 5600}}
        )
        cues = CueLibrary.from_mapping(
            {"intro": {"steps": [{"preset": "key"}], "stop_on_unconfirmed": True}}
        )
        integration = LightIntegration(
            config=LightConnectionConfig(transport="usb", port="/dev/cu.test"),
            light_factory=FakeFactory(FailingLight()),
            preset_library=presets,
            cue_library=cues,
        )

        scene = integration.plan_scene(
            {"brightness": 12},
            obj=2,
            control_mode=0x01,
            first_word=0x0301,
            start_seq=3,
        )
        preset = integration.plan_preset(
            "key",
            overrides={"brightness": 30},
            start_seq=scene["next_seq"],
        )
        transition = integration.plan_transition(
            {"brightness": 40},
            from_scene={"brightness": 20},
            steps=2,
            start_seq=7,
        )
        sequence = integration.plan_sequence(
            [{"preset": "key"}, {"to": {"brightness": 35}, "steps": 1}],
            stop_on_unconfirmed=True,
            start_seq=11,
        )
        cue = integration.plan_named_cue(
            "intro",
            stop_on_unconfirmed=False,
            start_seq=20,
        )

        self.assertEqual(integration.manifest()["presets"], ["key"])
        self.assertEqual(integration.capabilities()["cues"], ["intro"])
        self.assertTrue(scene["dry_run"])
        self.assertEqual(scene["scene"]["obj"], 2)
        self.assertEqual(scene["control_mode"], 0x01)
        self.assertEqual(scene["first_word_hex"], "0x0301")
        self.assertEqual(preset["scene"]["brightness"], 30.0)
        self.assertEqual(preset["command_plan"]["start_seq"], scene["next_seq"])
        self.assertEqual(
            [
                batch["scene"]["brightness"]
                for batch in transition["command_batches"]
            ],
            [30.0, 40.0],
        )
        self.assertTrue(sequence["stop_on_unconfirmed"])
        self.assertEqual(
            [step["action"] for step in sequence["steps"]],
            ["preset", "transition"],
        )
        self.assertEqual(cue["cue"], "intro")
        self.assertFalse(cue["stop_on_unconfirmed"])

    def test_local_validation_reports_open_errors_without_raising(self) -> None:
        payload = local_validation(light_factory=FakeFactory(FailingLight()))

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "port busy")
        self.assertFalse(payload["summary"]["ready_for"]["read_status"])

    def test_local_status_snapshot_reports_errors_without_raising(self) -> None:
        status, confirmed, error = local_status_snapshot(
            light_factory=FakeFactory(FailingLight())
        )

        self.assertFalse(confirmed)
        self.assertEqual(error, "port busy")
        self.assertEqual(status["error"], "port busy")

    def test_local_manifest_and_capabilities_can_be_used_standalone(self) -> None:
        config = LightConnectionConfig(
            transport="ble",
            name_contains="MOLUS",
            ble_in_process=True,
        )

        manifest = local_manifest(config, presets=["key"], cues=["intro"])
        capabilities = local_capabilities(presets=["key"], cues=["intro"])

        self.assertEqual(manifest["transport"]["active"], "ble")
        self.assertEqual(manifest["transport"]["ble_backend"], "direct")
        self.assertEqual(manifest["transport"]["ble_name_contains"], "MOLUS")
        self.assertEqual(capabilities["presets"], ["key"])
        self.assertEqual(capabilities["cues"], ["intro"])

    def test_local_error_status_returns_plain_error_payload(self) -> None:
        payload = local_error_status(RuntimeError("boom"))

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "boom")


class AsyncIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_async_status_snapshot_reads_configured_ble_light(self) -> None:
        status, confirmed, error = await local_async_status_snapshot(
            LightConnectionConfig(
                transport="ble",
                name_contains="MOLUS",
                ble_in_process=True,
            ),
            light_factory=AsyncFakeFactory(AsyncFakeStatusLight()),
        )

        self.assertTrue(confirmed)
        self.assertIsNone(error)
        self.assertEqual(status["transport"], "ble")
        self.assertEqual(status["firmware"], "1.6.4")
        self.assertEqual(status["generation"], "pl103")
        self.assertEqual(status["read_sn"]["device_identifier"], "08a409e0c1100113")

    async def test_local_async_readiness_uses_ble_backend_config(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_devices(**kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {"macos_status": None, "scan": None},
            }

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            side_effect=fake_devices,
        ):
            payload = await local_async_readiness(
                LightConnectionConfig(
                    transport="ble",
                    name_contains="PL103",
                    ble_backend="direct",
                ),
                allow_control=True,
                include_ble=True,
                light_factory=AsyncFakeFactory(AsyncFakeStatusLight()),
            )

        self.assertTrue(payload["connection_confirmed"])
        self.assertTrue(payload["ready_for"]["control_requests"])
        self.assertEqual(payload["bridge"]["transport"], "ble")
        self.assertEqual(calls[0]["configured_transport"], "ble")
        self.assertEqual(calls[0]["ble_name_contains"], "PL103")
        self.assertTrue(calls[0]["include_ble"])

    async def test_async_light_integration_facade_wraps_common_payloads(self) -> None:
        integration = AsyncLightIntegration(
            config=LightConnectionConfig(
                transport="ble",
                ble_backend="macos-app",
                name_contains="PL103",
            ),
            allow_control=False,
            preset_names=("key",),
            cue_names=("intro",),
            light_factory=AsyncFakeFactory(AsyncFakeStatusLight()),
        )

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {
                    "macos_status": {"ok": True, "authorization": "allowed"},
                    "scan": None,
                },
            },
        ) as devices:
            readiness = await integration.readiness()
            snapshot = await integration.snapshot(include_ble=True)

        self.assertEqual(
            integration.manifest()["transport"]["ble_backend"],
            "macos-app",
        )
        self.assertEqual(integration.capabilities()["cues"], ["intro"])
        self.assertTrue(readiness["connection_confirmed"])
        self.assertEqual(snapshot["payloads"]["manifest"]["presets"], ["key"])
        self.assertTrue(devices.call_args_list[0].kwargs["include_ble_status"])
        self.assertTrue(devices.call_args_list[1].kwargs["include_ble"])

    async def test_async_light_integration_readiness_guard(self) -> None:
        integration = AsyncLightIntegration(
            config=LightConnectionConfig(transport="ble", name_contains="MOLUS"),
            allow_control=True,
            light_factory=AsyncFakeFactory(AsyncFakeStatusLight()),
        )

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {"macos_status": None, "scan": None},
            },
        ):
            self.assertTrue(await integration.ready("control_requests"))
            with self.assertRaises(IntegrationNotReady) as error:
                await integration.require_control_ready(strict=True)

        self.assertEqual(error.exception.capabilities, ("confirmed_control",))
        self.assertEqual(
            error.exception.pending_action_ids,
            {"confirmed_control": ["confirm-control"]},
        )

    async def test_async_light_integration_devices_use_threaded_local_path(
        self,
    ) -> None:
        integration = AsyncLightIntegration(
            config=LightConnectionConfig(transport="ble", name_contains="PL103")
        )

        with patch(
            "zhiyun_light_control.integration.local_devices",
            return_value={
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {"included": True, "name_contains": "PL103"},
            },
        ) as devices:
            payload = await integration.devices(include_ble=True)

        self.assertTrue(payload["ble"]["included"])
        self.assertEqual(devices.call_args.args[0].name_contains, "PL103")
        self.assertTrue(devices.call_args.kwargs["include_ble"])

    async def test_local_async_devices_uses_ble_defaults(self) -> None:
        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {"macos_status": {"state": "unauthorized"}, "scan": None},
            },
        ) as devices:
            payload = await local_async_devices(
                LightConnectionConfig(
                    transport="ble",
                    ble_backend="macos-app",
                    name_contains="PL103",
                )
            )

        self.assertEqual(payload["ble"]["macos_status"]["state"], "unauthorized")
        self.assertTrue(devices.call_args.kwargs["include_ble_status"])
        self.assertEqual(devices.call_args.kwargs["ble_name_contains"], "PL103")

    async def test_async_discovery_helpers_use_threaded_local_paths(self) -> None:
        config = LightConnectionConfig(transport="usb", port="/dev/cu.test")
        with patch(
            "zhiyun_light_control.integration.local_usb_discovery",
            return_value={"transport": "usb", "summary": {"confirmed": 1}},
        ) as discover:
            payload = await local_async_usb_discovery(
                config,
                object_ids=(1,),
                first_words=(0x0100,),
            )

        self.assertEqual(payload["summary"]["confirmed"], 1)
        self.assertEqual(discover.call_args.args[0].port, "/dev/cu.test")
        self.assertEqual(discover.call_args.kwargs["object_ids"], (1,))

        with patch(
            "zhiyun_light_control.integration.local_ble_endpoint_test",
            return_value={"ok": False, "confirmed_candidates": []},
        ) as test_ble:
            payload = await AsyncLightIntegration(
                config=LightConnectionConfig(
                    transport="ble",
                    ble_backend="macos-app",
                    name_contains="PL103",
                )
            ).test_ble_endpoints(max_candidates=1)

        self.assertFalse(payload["ok"])
        self.assertEqual(test_ble.call_args.kwargs["max_candidates"], 1)

    async def test_async_integration_exposes_connection_routes(self) -> None:
        usb_candidate = LightConnectionCandidate(
            config=LightConnectionConfig.usb(
                port="/dev/cu.usbmodem21301",
                persistent=True,
            ),
            source="devices.usb",
            confidence="known-usb-descriptor",
            confidence_score=95,
            reason="USB descriptor matches Zhiyun Virtual ComPort",
        )
        integration = AsyncLightIntegration(
            config=LightConnectionConfig(transport="ble", name_contains="PL103"),
            allow_control=True,
        )

        with patch(
            "zhiyun_light_control.integration.local_connection_candidates",
            return_value=(usb_candidate,),
        ) as candidates:
            routes = await integration.connection_candidates(
                include_ble=True,
                persistent=True,
            )
            best_config = await integration.best_connection_config(
                include_ble=True,
                persistent=True,
            )
            configured = await integration.with_best_connection(
                include_ble=True,
                persistent=True,
            )

        self.assertEqual(routes, (usb_candidate,))
        self.assertEqual(best_config.port, "/dev/cu.usbmodem21301")
        self.assertEqual(configured.config.port, "/dev/cu.usbmodem21301")
        self.assertTrue(configured.allow_control)
        self.assertIs(configured.state_tracker, integration.state_tracker)
        self.assertEqual(candidates.call_args.kwargs["include_ble"], True)
        self.assertEqual(candidates.call_args.kwargs["persistent"], True)

    async def test_async_integration_exposes_status_probed_routes(self) -> None:
        usb_candidate = LightConnectionCandidate(
            config=LightConnectionConfig.usb(port="/dev/cu.usbmodem21301"),
            source="devices.usb.status",
            confidence="status-confirmed",
            confidence_score=115,
            reason="USB descriptor matches Zhiyun Virtual ComPort; status confirmed",
            evidence={"status_probe": {"connection_confirmed": True}},
        )
        integration = AsyncLightIntegration(
            config=LightConnectionConfig(transport="ble", name_contains="PL103"),
            allow_control=True,
        )

        with patch(
            "zhiyun_light_control.integration.local_probe_connection_candidates",
            return_value=(usb_candidate,),
        ) as candidates:
            routes = await integration.probe_connection_candidates(
                include_ble=True,
                persistent=True,
            )
            confirmed = await integration.confirmed_connection_candidates(
                include_ble=True,
                persistent=True,
            )
            best_config = await integration.best_confirmed_connection_config(
                include_ble=True,
                persistent=True,
            )
            configured = await integration.with_confirmed_connection(
                include_ble=True,
                persistent=True,
            )

        self.assertEqual(routes, (usb_candidate,))
        self.assertEqual(confirmed, (usb_candidate,))
        self.assertEqual(best_config.port, "/dev/cu.usbmodem21301")
        self.assertEqual(configured.config.port, "/dev/cu.usbmodem21301")
        self.assertTrue(configured.allow_control)
        self.assertIs(configured.state_tracker, integration.state_tracker)
        self.assertEqual(candidates.call_args.kwargs["include_ble"], True)
        self.assertEqual(candidates.call_args.kwargs["persistent"], True)
        self.assertTrue(candidates.call_args.kwargs["confirmed_only"])

    async def test_async_integration_exposes_ble_endpoint_routes(self) -> None:
        ble_candidate = LightConnectionCandidate(
            config=LightConnectionConfig.ble(
                address="UUID-1",
                backend="macos-app",
                service_uuid="service-uuid",
                write_uuid="write-uuid",
                notify_uuid="notify-uuid",
                persistent=True,
            ),
            source="ble.endpoint_report",
            confidence="confirmed-endpoint",
            confidence_score=100,
            reason="endpoint candidate returned ACK-backed DEVICE_INFO",
        )
        integration = AsyncLightIntegration(
            config=LightConnectionConfig(transport="ble", name_contains="PL103"),
            cue_names=("intro",),
        )

        with patch(
            "zhiyun_light_control.integration.local_ble_endpoint_connection_candidates",
            return_value=(ble_candidate,),
        ) as candidates:
            routes = await integration.ble_endpoint_connection_candidates(
                backend="macos-app",
                persistent=True,
            )
            best_config = await integration.best_ble_endpoint_config(
                backend="macos-app",
                persistent=True,
            )
            configured = await integration.with_ble_endpoint_connection(
                backend="macos-app",
                persistent=True,
            )

        self.assertEqual(routes, (ble_candidate,))
        self.assertEqual(best_config.ble_write_uuid, "write-uuid")
        self.assertEqual(configured.config.ble_notify_uuid, "notify-uuid")
        self.assertEqual(configured.cue_names, ("intro",))
        self.assertIs(configured.state_tracker, integration.state_tracker)
        self.assertEqual(candidates.call_args.kwargs["backend"], "macos-app")
        self.assertEqual(candidates.call_args.kwargs["persistent"], True)

    async def test_local_async_snapshot_includes_manifest_and_summary(self) -> None:
        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {"macos_status": None, "scan": None},
            },
        ):
            snapshot = await local_async_integration_snapshot(
                LightConnectionConfig(transport="ble", name_contains="MOLUS"),
                allow_control=True,
                presets=["key"],
                cues=["intro"],
                light_factory=AsyncFakeFactory(AsyncFakeStatusLight()),
            )

        self.assertEqual(snapshot["summary"]["transport"], "ble")
        self.assertTrue(snapshot["summary"]["connection_confirmed"])
        self.assertEqual(snapshot["payloads"]["manifest"]["presets"], ["key"])
        self.assertEqual(snapshot["payloads"]["capabilities"]["cues"], ["intro"])

    async def test_local_async_validation_uses_control_policy(self) -> None:
        light = AsyncFakeValidationLight(acknowledged=False)
        integration = AsyncLightIntegration(
            config=LightConnectionConfig(transport="ble", name_contains="MOLUS"),
            allow_control=True,
            light_factory=AsyncFakeFactory(light),
        )

        payload = await integration.validate(control_mode=0x01)

        self.assertTrue(payload["control_enabled"])
        self.assertIn("set_sleep", payload["unconfirmed"])
        self.assertFalse(payload["summary"]["ready_for"]["control_writes"])
        payloads = dict(light.payloads)
        self.assertEqual(payloads[RuntimeCommand.SLEEP][2], 0x01)
        self.assertEqual(payloads[RuntimeCommand.BRIGHTNESS][2], 0x01)

    async def test_async_light_integration_controller_reuses_config(self) -> None:
        light = AsyncFakeSceneLight()
        integration = AsyncLightIntegration(
            config=LightConnectionConfig(transport="ble", name_contains="MOLUS"),
            light_factory=AsyncFakeFactory(light),
        )

        controller = integration.controller(
            control_mode=0x01,
            require_acknowledged=True,
        )
        payload = await controller.apply_scene({"obj": 1, "brightness": 45})

        self.assertTrue(controller.require_acknowledged)
        self.assertEqual(payload["action"], "scene")
        self.assertEqual(light.control_modes, [0x01])
        self.assertEqual(light.scenes[0].brightness, 45)
        self.assertEqual(integration.state_snapshot()["version"], 1)
        self.assertEqual(integration.state()["scene"]["brightness"], 45)

    async def test_async_light_integration_runs_primitive_controls(self) -> None:
        light = AsyncFakeControlStatusLight()
        integration = AsyncLightIntegration(
            config=LightConnectionConfig(transport="ble", name_contains="MOLUS"),
            obj=2,
            light_factory=AsyncFakeFactory(light),
        )

        register = await integration.register(device_id=1, group_id=2)
        read = await integration.read_brightness()
        brightness = await integration.set_brightness(35, control_mode=0x01)
        cct = await integration.set_cct(5600, control_mode=0x01)
        sleep = await integration.set_sleep(0, control_mode=0x01)
        rgb = await integration.set_rgb(255, 180, 120, control_mode=0x01)
        hsi = await integration.set_hsi(30.0, 0.5, 40, control_mode=0x01)

        self.assertTrue(register["acknowledged"])
        self.assertEqual(register["action"], "register")
        self.assertTrue(read["acknowledged"])
        self.assertEqual(read["action"], "read_brightness")
        self.assertEqual(read["value"], 35.0)
        self.assertEqual(read["obj"], 2)
        self.assertTrue(brightness["applied"])
        self.assertEqual(cct["scene"]["kelvin"], 5600)
        self.assertEqual(sleep["scene"]["sleep"], 0)
        self.assertEqual(rgb["scene"]["red"], 255)
        self.assertEqual(hsi["scene"]["hue"], 30.0)
        self.assertEqual(
            light.primitive_calls,
            [
                ("brightness", 2, 35, 0x01),
                ("cct", 2, 5600, 0x01),
                ("sleep", 2, 0, 0x01),
                ("rgb", 2, (255, 180, 120), 0x01),
                ("hsi", 2, (30.0, 0.5, 40), 0x01),
            ],
        )
        self.assertEqual(integration.state_snapshot()["version"], 5)
        self.assertEqual(integration.state()["action"], "set_hsi")

    async def test_async_light_integration_runs_controls_directly(self) -> None:
        light = AsyncFakeControlStatusLight()
        presets = ScenePresetLibrary.from_mapping({"key": {"brightness": 25}})
        cues = CueLibrary.from_mapping({"intro": {"steps": [{"preset": "key"}]}})
        integration = AsyncLightIntegration(
            config=LightConnectionConfig(transport="ble", name_contains="MOLUS"),
            obj=2,
            light_factory=AsyncFakeFactory(light),
            preset_library=presets,
            cue_library=cues,
        )

        scene = await integration.apply_scene({"brightness": 12}, control_mode=0x01)
        preset = await integration.apply_preset(
            "key",
            overrides={"brightness": 30},
            control_mode=0x01,
        )
        sequence = await integration.run_sequence(
            [{"scene": {"brightness": 14}}, {"preset": "key"}],
            control_mode=0x01,
        )
        cue = await integration.run_named_cue("intro", control_mode=0x01)

        self.assertTrue(scene["applied"])
        self.assertTrue(preset["applied"])
        self.assertTrue(sequence["applied"])
        self.assertTrue(cue["applied"])
        self.assertEqual(
            [sent.obj for sent in light.scenes],
            [2, 2, 2, 2, 2],
        )
        self.assertEqual(
            [sent.brightness for sent in light.scenes],
            [12.0, 30.0, 14.0, 25.0, 25.0],
        )
        self.assertEqual(light.control_modes, [0x01, 0x01, 0x01, 0x01, 0x01])
        self.assertEqual(integration.state_snapshot()["version"], 4)
        self.assertEqual(integration.state()["action"], "cue")
        self.assertEqual(integration.state()["scene"]["brightness"], 25.0)
        history = integration.state_history(limit=2)
        self.assertEqual(
            [event["version"] for event in history["events"]],
            [3, 4],
        )
        update = await integration.wait_for_state_update(3, timeout=0)
        self.assertEqual(update["state"]["action"], "cue")
        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {"macos_status": None, "scan": None},
            },
        ):
            readiness = await integration.readiness()
            snapshot = await integration.snapshot()

        self.assertEqual(readiness["state"]["version"], 4)
        self.assertEqual(readiness["state"]["snapshot"]["action"], "cue")
        self.assertTrue(readiness["ready_for"]["confirmed_control"])
        self.assertEqual(snapshot["payloads"]["ready"]["state"]["version"], 4)

    async def test_async_light_integration_control_readiness_guard_fails_closed(
        self,
    ) -> None:
        integration = AsyncLightIntegration(
            config=LightConnectionConfig(transport="ble", name_contains="MOLUS"),
            allow_control=False,
            light_factory=AsyncFakeFactory(AsyncFakeStatusLight()),
        )

        with patch(
            "zhiyun_light_control.integration.discover_transport_devices",
            return_value={
                "usb": {"available": False, "selected_port": None, "ports": []},
                "ble": {"macos_status": None, "scan": None},
            },
        ), self.assertRaises(IntegrationNotReady) as error:
            await integration.apply_scene({"brightness": 10}, require_ready=True)

        self.assertEqual(error.exception.capabilities, ("control_requests",))
        self.assertEqual(
            error.exception.pending_action_ids,
            {"control_requests": ["enable-control"]},
        )

    async def test_async_light_integration_plans_sdk_primitives_without_opening_light(
        self,
    ) -> None:
        presets = ScenePresetLibrary.from_mapping(
            {"key": {"brightness": 25, "kelvin": 5600}}
        )
        cues = CueLibrary.from_mapping(
            {"intro": {"steps": [{"preset": "key"}], "stop_on_unconfirmed": True}}
        )
        integration = AsyncLightIntegration(
            config=LightConnectionConfig(transport="ble", name_contains="MOLUS"),
            light_factory=AsyncFakeFactory(AsyncFailingLight()),
            preset_library=presets,
            cue_library=cues,
        )

        scene = integration.plan_scene(
            {"brightness": 12},
            obj=2,
            control_mode=0x01,
            first_word=0x0301,
            start_seq=3,
        )
        preset = integration.plan_preset(
            "key",
            overrides={"brightness": 30},
            start_seq=scene["next_seq"],
        )
        transition = integration.plan_transition(
            {"brightness": 40},
            from_scene={"brightness": 20},
            steps=2,
            start_seq=7,
        )
        sequence = integration.plan_sequence(
            [{"preset": "key"}, {"to": {"brightness": 35}, "steps": 1}],
            stop_on_unconfirmed=True,
            start_seq=11,
        )
        cue = integration.plan_named_cue(
            "intro",
            stop_on_unconfirmed=False,
            start_seq=20,
        )

        self.assertEqual(integration.manifest()["presets"], ["key"])
        self.assertEqual(integration.capabilities()["cues"], ["intro"])
        self.assertTrue(scene["dry_run"])
        self.assertEqual(scene["scene"]["obj"], 2)
        self.assertEqual(scene["control_mode"], 0x01)
        self.assertEqual(scene["first_word_hex"], "0x0301")
        self.assertEqual(preset["scene"]["brightness"], 30.0)
        self.assertEqual(preset["command_plan"]["start_seq"], scene["next_seq"])
        self.assertEqual(
            [
                batch["scene"]["brightness"]
                for batch in transition["command_batches"]
            ],
            [30.0, 40.0],
        )
        self.assertTrue(sequence["stop_on_unconfirmed"])
        self.assertEqual(
            [step["action"] for step in sequence["steps"]],
            ["preset", "transition"],
        )
        self.assertEqual(cue["cue"], "intro")
        self.assertFalse(cue["stop_on_unconfirmed"])

    async def test_local_async_validation_reports_open_errors(self) -> None:
        payload = await local_async_validation(
            light_factory=AsyncFakeFactory(AsyncFailingLight())
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "adapter busy")
        self.assertFalse(payload["summary"]["ready_for"]["read_status"])


def _result(first_word: int, cmd: int, payload: bytes) -> CommandResult:
    tx = build_frame(first_word, 1, cmd)
    rx = build_frame(first_word, 1, cmd, payload)
    ack = first_frame(rx, cmd=cmd)
    return CommandResult(cmd, tx, rx, (ack,), ack)


if __name__ == "__main__":
    unittest.main()
