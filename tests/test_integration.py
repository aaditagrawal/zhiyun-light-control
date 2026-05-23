from __future__ import annotations

import unittest
from unittest.mock import patch

from zhiyun_light_control import (
    LightConnectionConfig,
    LightIntegration,
    local_capabilities,
    local_error_status,
    local_integration_snapshot,
    local_manifest,
    local_readiness,
    local_status_snapshot,
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


class FailingLight:
    def __enter__(self) -> FailingLight:
        raise RuntimeError("port busy")

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return


class FakeFactory:
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


def _result(first_word: int, cmd: int, payload: bytes) -> CommandResult:
    tx = build_frame(first_word, 1, cmd)
    rx = build_frame(first_word, 1, cmd, payload)
    ack = first_frame(rx, cmd=cmd)
    return CommandResult(cmd, tx, rx, (ack,), ack)


if __name__ == "__main__":
    unittest.main()
