from __future__ import annotations

import unittest
from unittest.mock import patch

from zhiyun_light_control import (
    AsyncLightIntegration,
    LightConnectionConfig,
    LightIntegration,
    local_async_integration_snapshot,
    local_async_readiness,
    local_async_status_snapshot,
    local_async_validation,
    local_capabilities,
    local_error_status,
    local_integration_snapshot,
    local_manifest,
    local_readiness,
    local_status_snapshot,
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
