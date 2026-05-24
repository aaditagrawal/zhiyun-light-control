from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zhiyun_light_control.cli import main
from zhiyun_light_control.models import CommandResult
from zhiyun_light_control.protocol import (
    RuntimeCommand,
    UpdaterCommand,
    build_frame,
    build_runtime_frame,
    first_frame,
    first_response_frame,
    iter_frames,
)
from zhiyun_light_control.transports.ble import BleExchangeResult, BleWorkerError


class CliTests(unittest.TestCase):
    def write_scene_plan_bundle(self, path: Path) -> dict[str, object]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "plan",
                    "--brightness",
                    "25",
                    "--kelvin",
                    "5600",
                    "--first-word",
                    "0x0301",
                    "--start-seq",
                    "7",
                    "--output",
                    str(path),
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), payload)
        return payload

    def test_apply_preset_dry_run_resolves_scene_without_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenes.json"
            path.write_text(
                json.dumps({"scenes": {"key": {"brightness": 35, "kelvin": 5600}}}),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "apply",
                        "--preset-file",
                        str(path),
                        "--preset",
                        "key",
                        "--brightness",
                        "42",
                        "--dry-run",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["scene"]["brightness"], 42.0)
        self.assertEqual(payload["scene"]["kelvin"], 5600)

    def test_cue_dry_run_resolves_named_cue_without_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cues.json"
            path.write_text(
                json.dumps(
                    {
                        "cues": {
                            "intro": {
                                "steps": [{"scene": {"brightness": 10}}],
                                "stop_on_unconfirmed": True,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "cue",
                        "--cue-file",
                        str(path),
                        "--cue",
                        "intro",
                        "--dry-run",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["cue"], "intro")
        self.assertEqual(payload["request"]["steps"][0]["scene"]["brightness"], 10)
        self.assertEqual(payload["request"]["control_mode"], 0x33)

    def test_cue_command_posts_named_cue_to_bridge_client(self) -> None:
        class FakeBridgeClient:
            instances: list[FakeBridgeClient] = []

            def __init__(self, base_url: str, *, timeout: float = 3.0):
                self.base_url = base_url
                self.timeout = timeout
                self.calls: list[tuple[dict[str, object], int | None]] = []
                self.instances.append(self)

            def run_cue(self, cue, *, control_mode=None):
                self.calls.append((cue, control_mode))
                return {"applied": True, "stopped": False, "steps": []}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cues.json"
            path.write_text(
                json.dumps({"cues": {"intro": {"steps": [{"scene": {}}]}}}),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with (
                patch("zhiyun_light_control.cli.LightBridgeClient", FakeBridgeClient),
                contextlib.redirect_stdout(stdout),
            ):
                code = main(
                    [
                        "cue",
                        "--cue-file",
                        str(path),
                        "--cue",
                        "intro",
                        "--base-url",
                        "http://bridge.test",
                        "--timeout",
                        "5",
                        "--control-mode",
                        "0x01",
                        "--yes",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["applied"])
        client = FakeBridgeClient.instances[0]
        self.assertEqual(client.base_url, "http://bridge.test")
        self.assertEqual(client.timeout, 5.0)
        self.assertEqual(client.calls[0][0]["steps"], [{"scene": {}}])
        self.assertEqual(client.calls[0][1], 0x01)

    def test_serve_loads_cue_file_for_bridge(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_serve(**kwargs: object) -> None:
            calls.append(dict(kwargs))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cues.json"
            path.write_text(
                json.dumps({"cues": {"intro": {"steps": [{"scene": {}}]}}}),
                encoding="utf-8",
            )
            with (
                patch("zhiyun_light_control.cli.serve", side_effect=fake_serve),
                patch(
                    "zhiyun_light_control.cli.bridge_light_factory",
                    return_value=lambda: None,
                ),
            ):
                code = main(["serve", "--cue-file", str(path)])

        self.assertEqual(code, 0)
        cue_library = calls[0]["cue_library"]
        self.assertEqual(cue_library.names(), ["intro"])

    def test_osc_serve_loads_cue_file_for_bridge(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_serve_osc(**kwargs: object) -> None:
            calls.append(dict(kwargs))

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cues.json"
            path.write_text(
                json.dumps({"cues": {"intro": {"steps": [{"scene": {}}]}}}),
                encoding="utf-8",
            )
            with (
                patch("zhiyun_light_control.cli.serve_osc", side_effect=fake_serve_osc),
                patch(
                    "zhiyun_light_control.cli.bridge_light_factory",
                    return_value=lambda: None,
                ),
            ):
                code = main(["osc-serve", "--cue-file", str(path)])

        self.assertEqual(code, 0)
        cue_library = calls[0]["cue_library"]
        self.assertEqual(cue_library.names(), ["intro"])

    def test_validate_strict_fails_when_control_is_unconfirmed(self) -> None:
        class FakeLight:
            def __init__(self) -> None:
                self.payloads: list[tuple[int, bytes]] = []

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def probe(self):
                class Probe:
                    def to_dict(self):
                        return {
                            "firmware": "test",
                            "device_identifier": "id",
                            "generation": "pl103",
                            "voltage_status": 101,
                            "device_id": 1,
                            "port": "/dev/cu.test",
                        }

                return Probe()

            def exchange_updater(self, cmd, payload=b"", *, timeout=0.8):
                return self.exchange_runtime(cmd, payload, timeout=timeout)

            def exchange_runtime(self, cmd, payload=b"", *, timeout=0.8):
                del payload, timeout
                from zhiyun_light_control.models import CommandResult

                tx = build_runtime_frame(1, cmd)
                rx = b""
                return CommandResult(cmd, tx, rx, (), first_frame(rx, cmd=cmd))

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=FakeLight(),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(["validate", "--allow-control", "--strict", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(payload["all_attempted_confirmed"])
        self.assertIn("set_brightness", payload["unconfirmed"])

    def test_status_cli_reports_ack_backed_usb_status(self) -> None:
        class FakeLight:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_runtime(self, cmd, payload=b"", *, timeout=0.8):
                del payload, timeout
                payload_by_cmd = {
                    RuntimeCommand.DEVICE_INFO: b"device-test\x00pl103\x00",
                    RuntimeCommand.FIRMWARE: b"1.6.4\x00",
                    RuntimeCommand.VOLTAGE: b"\x65",
                    RuntimeCommand.DEVICE_ID: b"\x00\x00",
                }
                tx = build_runtime_frame(1, cmd)
                rx = build_runtime_frame(1, cmd, payload_by_cmd[cmd])
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    cmd,
                    tx,
                    rx,
                    frames,
                    first_response_frame(rx, tx=tx, cmd=cmd),
                )

            def exchange_updater(self, cmd, payload=b"", *, timeout=0.8):
                del payload, timeout
                payload_by_cmd = {
                    UpdaterCommand.CHIP_SYNC: bytes.fromhex(
                        "0048444c0000010010030041054008a40065a36075"
                    ),
                    UpdaterCommand.READ_SN: bytes.fromhex("004105130110c1e009a408"),
                }
                tx = build_frame(0x0103, 1, cmd)
                rx = build_frame(
                    0x0103,
                    1,
                    cmd,
                    payload_by_cmd[cmd],
                )
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    cmd,
                    tx,
                    rx,
                    frames,
                    first_response_frame(rx, tx=tx, cmd=cmd),
                )

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=FakeLight(),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(["status", "--transport", "usb", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["connection_confirmed"])
        self.assertEqual(payload["device_identifier"], "device-test")
        self.assertEqual(payload["generation"], "pl103")
        self.assertEqual(payload["firmware"], "1.6.4")
        self.assertEqual(payload["voltage_status"], 101)
        self.assertEqual(payload["device_id"], 0)
        self.assertEqual(payload["chip_sync"]["updater_firmware"], "1.64")
        self.assertEqual(payload["read_sn"]["product"], "0x0541")
        self.assertEqual(
            payload["read_sn"]["device_identifier"],
            "08a409e0c1100113",
        )
        self.assertTrue(payload["commands"]["device_info"]["acknowledged"])

    def test_ready_cli_reports_usb_preflight(self) -> None:
        class FakeLight:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_runtime(self, cmd, payload=b"", *, timeout=1.5):
                del payload, timeout
                payload_by_cmd = {
                    RuntimeCommand.DEVICE_INFO: b"device-test\x00pl103\x00",
                    RuntimeCommand.FIRMWARE: b"1.6.4\x00",
                    RuntimeCommand.VOLTAGE: b"\x65",
                    RuntimeCommand.DEVICE_ID: b"\x00\x00",
                }
                tx = build_runtime_frame(1, cmd)
                rx = build_runtime_frame(1, cmd, payload_by_cmd[cmd])
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    cmd,
                    tx,
                    rx,
                    frames,
                    first_response_frame(rx, tx=tx, cmd=cmd),
                )

        devices = {
            "api": "zhiyun-light-control",
            "configured_transport": "usb",
            "usb": {
                "available": True,
                "selected_port": "/dev/cu.usbmodem-test",
                "ports": [
                    {
                        "path": "/dev/cu.usbmodem-test",
                        "selected": True,
                    }
                ],
            },
            "ble": {"macos_status": None, "scan": None},
        }

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.bridge.ZhiyunLight.usb",
                return_value=FakeLight(),
            ),
            patch(
                "zhiyun_light_control.integration.discover_transport_devices",
                return_value=devices,
            ) as discover,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "ready",
                    "--transport",
                    "usb",
                    "--port",
                    "/dev/cu.usbmodem-test",
                    "--allow-control",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["ready_for"]["read_status"])
        self.assertTrue(payload["ready_for"]["control_requests"])
        self.assertFalse(payload["ready_for"]["confirmed_control"])
        self.assertEqual(payload["status"]["firmware"], "1.6.4")
        self.assertEqual(
            payload["devices"]["usb"]["selected_port"],
            "/dev/cu.usbmodem-test",
        )
        self.assertIn(
            "confirm-control",
            payload["requirements"]["confirmed_control"]["pending_actions"],
        )
        discover.assert_called_once_with(
            configured_transport="usb",
            configured_usb_port="/dev/cu.usbmodem-test",
            include_ble=False,
            include_ble_status=False,
            ble_backend="worker",
            ble_timeout=1.5,
            ble_name_contains=None,
            ble_python=None,
        )

    def test_integration_cli_reports_local_controller_snapshot(self) -> None:
        class FakeLight:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_runtime(self, cmd, payload=b"", *, timeout=1.5):
                del payload, timeout
                payload_by_cmd = {
                    RuntimeCommand.DEVICE_INFO: b"device-test\x00pl103\x00",
                    RuntimeCommand.FIRMWARE: b"1.6.4\x00",
                    RuntimeCommand.VOLTAGE: b"\x65",
                    RuntimeCommand.DEVICE_ID: b"\x00\x00",
                }
                tx = build_runtime_frame(1, cmd)
                rx = build_runtime_frame(1, cmd, payload_by_cmd[cmd])
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    cmd,
                    tx,
                    rx,
                    frames,
                    first_response_frame(rx, tx=tx, cmd=cmd),
                )

        devices = {
            "api": "zhiyun-light-control",
            "configured_transport": "usb",
            "usb": {
                "available": True,
                "selected_port": "/dev/cu.usbmodem-test",
                "ports": [
                    {
                        "path": "/dev/cu.usbmodem-test",
                        "selected": True,
                    }
                ],
            },
            "ble": {
                "backend": "macos-app",
                "macos_status": {
                    "ok": False,
                    "authorization": "not_determined",
                    "state": "unauthorized",
                    "error": "Bluetooth state unauthorized: 3",
                },
                "scan": None,
            },
        }

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.bridge.ZhiyunLight.usb",
                return_value=FakeLight(),
            ),
            patch(
                "zhiyun_light_control.integration.discover_transport_devices",
                return_value=devices,
            ) as discover,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "integration",
                    "--transport",
                    "usb",
                    "--port",
                    "/dev/cu.usbmodem-test",
                    "--ble-backend",
                    "macos-app",
                    "--include-ble-status",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["api"], "zhiyun-light-control")
        self.assertEqual(payload["version"], "0.1.0")
        self.assertEqual(payload["summary"]["transport"], "usb")
        self.assertTrue(payload["summary"]["connection_confirmed"])
        self.assertFalse(payload["summary"]["control_enabled"])
        self.assertTrue(payload["summary"]["ready_for"]["read_status"])
        self.assertEqual(
            payload["summary"]["pending_action_ids"],
            ["enable-control", "confirm-control"],
        )
        self.assertEqual(
            payload["summary"]["selected_usb_port"],
            "/dev/cu.usbmodem-test",
        )
        self.assertEqual(payload["summary"]["firmware"], "1.6.4")
        self.assertEqual(payload["summary"]["generation"], "pl103")
        self.assertEqual(payload["summary"]["device_identifier"], "device-test")
        self.assertEqual(payload["summary"]["ble_authorization"], "not_determined")
        self.assertEqual(
            payload["summary"]["ble_blocker"],
            "Bluetooth state unauthorized: 3",
        )
        self.assertEqual(
            payload["payloads"]["manifest"]["setup"]["integration"]["path"],
            "/integration",
        )
        self.assertEqual(
            payload["payloads"]["manifest"]["setup"]["local_preflight"][
                "integration_command"
            ],
            "zlight integration --transport usb --json",
        )
        self.assertIn("integration-cli", {
            primitive["name"]
            for primitive in payload["payloads"]["capabilities"]["primitives"]
        })
        discover.assert_called_once_with(
            configured_transport="usb",
            configured_usb_port="/dev/cu.usbmodem-test",
            include_ble=False,
            include_ble_status=True,
            ble_backend="macos-app",
            ble_timeout=1.5,
            ble_name_contains=None,
            ble_python=None,
        )

    def test_ready_cli_reports_ble_authorization_error(self) -> None:
        devices = {
            "api": "zhiyun-light-control",
            "configured_transport": "ble",
            "usb": {"available": False, "selected_port": None, "ports": []},
            "ble": {
                "backend": "macos-app",
                "macos_status": {
                    "ok": False,
                    "authorization": "not_determined",
                    "state": "unauthorized",
                    "error": "Bluetooth state unauthorized: 3",
                },
                "scan": None,
            },
        }

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.integration.local_status_snapshot",
                return_value=(
                    {
                        "ok": False,
                        "error": "Bluetooth state unauthorized: 3",
                    },
                    False,
                    "Bluetooth state unauthorized: 3",
                ),
            ),
            patch(
                "zhiyun_light_control.integration.discover_transport_devices",
                return_value=devices,
            ) as discover,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "ready",
                    "--transport",
                    "ble",
                    "--ble-backend",
                    "macos-app",
                    "--name-contains",
                    "PL103",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["ready_for"]["read_status"])
        self.assertEqual(payload["bridge"]["ble_backend"], "macos-app")
        self.assertEqual(payload["status"]["error"], "Bluetooth state unauthorized: 3")
        self.assertEqual(
            payload["devices"]["ble"]["macos_status"]["authorization"],
            "not_determined",
        )
        self.assertEqual(
            payload["requirements"]["read_status"]["pending_actions"],
            ["authorize-bluetooth", "read-status"],
        )
        discover.assert_called_once_with(
            configured_transport="ble",
            configured_usb_port=None,
            include_ble=False,
            include_ble_status=True,
            ble_backend="macos-app",
            ble_timeout=1.5,
            ble_name_contains="PL103",
            ble_python=None,
        )

    def test_integration_cli_reports_ble_authorization_error(self) -> None:
        devices = {
            "api": "zhiyun-light-control",
            "configured_transport": "ble",
            "usb": {"available": False, "selected_port": None, "ports": []},
            "ble": {
                "backend": "macos-app",
                "macos_status": {
                    "ok": False,
                    "authorization": "not_determined",
                    "state": "unauthorized",
                    "error": "Bluetooth state unauthorized: 3",
                },
                "scan": None,
            },
        }

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.integration.local_status_snapshot",
                return_value=(
                    {
                        "ok": False,
                        "error": "Bluetooth state unauthorized: 3",
                    },
                    False,
                    "Bluetooth state unauthorized: 3",
                ),
            ),
            patch(
                "zhiyun_light_control.integration.discover_transport_devices",
                return_value=devices,
            ) as discover,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "integration",
                    "--transport",
                    "ble",
                    "--ble-backend",
                    "macos-app",
                    "--name-contains",
                    "PL103",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(payload["summary"]["connection_confirmed"])
        self.assertFalse(payload["summary"]["ready_for"]["read_status"])
        self.assertEqual(payload["summary"]["transport"], "ble")
        self.assertEqual(payload["summary"]["ble_backend"], "macos-app")
        self.assertEqual(payload["summary"]["ble_authorization"], "not_determined")
        self.assertEqual(payload["summary"]["ble_state"], "unauthorized")
        self.assertEqual(
            payload["summary"]["ble_blocker"],
            "Bluetooth state unauthorized: 3",
        )
        self.assertEqual(
            payload["summary"]["pending_action_ids"],
            [
                "authorize-bluetooth",
                "read-status",
                "enable-control",
                "confirm-control",
            ],
        )
        discover.assert_called_once_with(
            configured_transport="ble",
            configured_usb_port=None,
            include_ble=False,
            include_ble_status=True,
            ble_backend="macos-app",
            ble_timeout=1.5,
            ble_name_contains="PL103",
            ble_python=None,
        )

    def test_metadata_cli_exports_api_payloads_without_opening_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            preset_path = Path(tmp) / "scenes.json"
            cue_path = Path(tmp) / "cues.json"
            preset_path.write_text(
                json.dumps({"scenes": {"key": {"brightness": 35}}}),
                encoding="utf-8",
            )
            cue_path.write_text(
                json.dumps({"cues": {"intro": {"steps": [{"preset": "key"}]}}}),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with (
                patch("zhiyun_light_control.cli.ZhiyunLight.usb") as usb,
                patch("zhiyun_light_control.cli.AsyncZhiyunLight.isolated_ble") as ble,
                contextlib.redirect_stdout(stdout),
            ):
                code = main(
                    [
                        "metadata",
                        "--transport",
                        "ble",
                        "--ble-backend",
                        "macos-app",
                        "--name-contains",
                        "PL103",
                        "--preset-file",
                        str(preset_path),
                        "--cue-file",
                        str(cue_path),
                        "--allow-control",
                        "--json",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transport"]["transport"], "ble")
        self.assertEqual(payload["payloads"]["manifest"]["transport"]["active"], "ble")
        self.assertEqual(
            payload["payloads"]["manifest"]["transport"]["ble_backend"],
            "macos-app",
        )
        self.assertEqual(payload["payloads"]["manifest"]["presets"], ["key"])
        self.assertEqual(payload["payloads"]["capabilities"]["cues"], ["intro"])
        self.assertIn("/execute-plan", payload["payloads"]["openapi"]["paths"])
        self.assertTrue(payload["payloads"]["capabilities"]["control_enabled"])
        usb.assert_not_called()
        ble.assert_not_called()

    def test_metadata_cli_can_print_single_payload_kind(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(["metadata", "--kind", "openapi", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["openapi"], "3.1.0")
        self.assertIn("/manifest", payload["paths"])

    def test_set_returns_nonzero_when_command_is_unacknowledged(self) -> None:
        class FakeLight:
            def __init__(self) -> None:
                self.payloads: list[tuple[int, bytes]] = []

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_runtime(self, cmd, payload=b"", *, timeout=0.8):
                del timeout
                self.payloads.append((cmd, payload))
                tx = build_runtime_frame(1, cmd, payload)
                return CommandResult(cmd, tx, b"", (), None)

        fake = FakeLight()
        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=fake,
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(["set", "brightness", "--value", "35", "--yes"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(payload["acknowledged"])
        self.assertEqual(payload["transport_status"], "sent_no_response")
        self.assertEqual(fake.payloads[0][1][2], 0x33)

    def test_set_cli_accepts_legacy_control_mode_override(self) -> None:
        class FakeLight:
            def __init__(self) -> None:
                self.payloads: list[tuple[int, bytes]] = []

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_runtime(self, cmd, payload=b"", *, timeout=0.8):
                del timeout
                self.payloads.append((cmd, payload))
                tx = build_runtime_frame(1, cmd, payload)
                rx = build_runtime_frame(1, cmd, b"\x00")
                return CommandResult(
                    cmd,
                    tx,
                    rx,
                    tuple(iter_frames(rx)),
                    first_response_frame(rx, tx=tx, cmd=cmd),
                )

        fake = FakeLight()
        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=fake,
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "set",
                    "sleep",
                    "--value",
                    "0",
                    "--control-mode",
                    "0x01",
                    "--yes",
                ]
            )

        self.assertEqual(code, 0)
        self.assertEqual(fake.payloads[0][0], RuntimeCommand.SLEEP)
        self.assertEqual(fake.payloads[0][1].hex(), "01000100")

    def test_apply_returns_nonzero_when_any_command_is_unacknowledged(self) -> None:
        class FakeLight:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def apply_scene(self, _scene, *, control_mode=0x33):
                del control_mode
                tx = build_runtime_frame(1, RuntimeCommand.BRIGHTNESS)
                return [CommandResult(RuntimeCommand.BRIGHTNESS, tx, b"", (), None)]

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=FakeLight(),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(["apply", "--brightness", "35", "--yes"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload["results"][0]["transport_status"], "sent_no_response")

    def test_apply_can_use_custom_first_word_frame_route(self) -> None:
        class FakeLight:
            def __init__(self) -> None:
                self.calls: list[tuple[bytes, int, float]] = []

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_prebuilt_frame(self, frame, command, *, timeout=0.8):
                self.calls.append((frame, command, timeout))
                parsed = first_frame(frame)
                assert parsed is not None
                rx = build_frame(parsed.first_word, parsed.seq, command, b"\x00")
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    command,
                    frame,
                    rx,
                    frames,
                    first_response_frame(rx, tx=frame, cmd=command),
                )

        fake = FakeLight()
        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=fake,
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "apply",
                    "--first-word",
                    "0x0301",
                    "--timeout",
                    "0.25",
                    "--sleep",
                    "0",
                    "--brightness",
                    "50",
                    "--kelvin",
                    "3200",
                    "--yes",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["first_word_hex"], "0x0301")
        self.assertEqual(
            [first_frame(call[0]).first_word for call in fake.calls],
            [0x0301, 0x0301, 0x0301],
        )
        self.assertEqual(
            [call[1] for call in fake.calls],
            [
                RuntimeCommand.SLEEP,
                RuntimeCommand.BRIGHTNESS,
                RuntimeCommand.CCT,
            ],
        )
        self.assertEqual([call[2] for call in fake.calls], [0.25, 0.25, 0.25])

    def test_apply_accept_echo_allows_echo_only_route_exit_zero(self) -> None:
        class FakeLight:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_prebuilt_frame(self, frame, command, *, timeout=0.8):
                del timeout
                frames = tuple(iter_frames(frame))
                return CommandResult(
                    command,
                    frame,
                    frame,
                    frames,
                    first_response_frame(frame, tx=frame, cmd=command),
                )

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=FakeLight(),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "apply",
                    "--first-word",
                    "0x0301",
                    "--accept-echo",
                    "--brightness",
                    "50",
                    "--yes",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["accepted_echo"])
        self.assertFalse(payload["results"][0]["acknowledged"])
        self.assertEqual(payload["results"][0]["transport_status"], "echoed_write")

    def test_plan_cli_writes_serialized_scene_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            payload = self.write_scene_plan_bundle(path)

        self.assertEqual(payload["kind"], "serialized-plan-bundle")
        self.assertEqual(payload["plan"]["action"], "scene")
        self.assertEqual(payload["plan"]["scene"]["brightness"], 25.0)
        self.assertEqual(payload["plan"]["command_plan"]["start_seq"], 7)
        self.assertEqual(payload["plan"]["command_plan"]["next_seq"], 9)
        self.assertEqual(payload["plan"]["command_plan"]["frames"][0]["seq"], 7)
        self.assertEqual(
            payload["plan"]["command_plan"]["frames"][0]["first_word_hex"],
            "0x0301",
        )
        self.assertEqual(payload["summary"]["frame_count"], 2)

    def test_execute_plan_cli_runs_exact_usb_frames(self) -> None:
        class FakeLight:
            def __init__(self) -> None:
                self.calls: list[tuple[bytes, int, float]] = []

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_prebuilt_frame(self, frame, command, *, timeout=0.8):
                self.calls.append((frame, command, timeout))
                parsed = first_frame(frame)
                assert parsed is not None
                rx = build_frame(parsed.first_word, parsed.seq, command, b"\x00")
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    command,
                    frame,
                    rx,
                    frames,
                    first_response_frame(rx, tx=frame, cmd=command),
                )

        fake = FakeLight()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            payload = self.write_scene_plan_bundle(path)
            stdout = io.StringIO()
            with (
                patch(
                    "zhiyun_light_control.cli.ZhiyunLight.usb",
                    return_value=fake,
                ),
                contextlib.redirect_stdout(stdout),
            ):
                code = main(
                    [
                        "execute-plan",
                        "--timeout",
                        "0.25",
                        str(path),
                        "--yes",
                        "--json",
                    ]
                )

        result = json.loads(stdout.getvalue())
        expected = [
            (bytes.fromhex(frame["frame_hex"]), frame["command"], 0.25)
            for frame in payload["plan"]["command_plan"]["frames"]
        ]
        self.assertEqual(code, 0)
        self.assertTrue(result["applied"])
        self.assertEqual(result["planned_action"], "scene")
        self.assertEqual(fake.calls, expected)

    def test_execute_plan_cli_uses_ble_backend_for_serialized_frames(self) -> None:
        class FakeAsyncLight:
            def __init__(self) -> None:
                self.calls: list[tuple[bytes, int, float]] = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                return

            async def exchange_prebuilt_frame(self, frame, command, *, timeout=0.8):
                self.calls.append((frame, command, timeout))
                parsed = first_frame(frame)
                assert parsed is not None
                rx = build_frame(parsed.first_word, parsed.seq, command, b"\x00")
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    command,
                    frame,
                    rx,
                    frames,
                    first_response_frame(rx, tx=frame, cmd=command),
                )

        fake = FakeAsyncLight()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            payload = self.write_scene_plan_bundle(path)
            stdout = io.StringIO()
            with (
                patch(
                    "zhiyun_light_control.cli.AsyncZhiyunLight.isolated_ble",
                    return_value=fake,
                ) as isolated,
                contextlib.redirect_stdout(stdout),
            ):
                code = main(
                    [
                        "execute-plan",
                        "--transport",
                        "ble",
                        "--name-contains",
                        "MOLUS",
                        "--python",
                        "python-test",
                        "--timeout",
                        "0.25",
                        str(path),
                        "--yes",
                        "--json",
                    ]
                )

        result = json.loads(stdout.getvalue())
        expected = [
            (bytes.fromhex(frame["frame_hex"]), frame["command"], 0.25)
            for frame in payload["plan"]["command_plan"]["frames"]
        ]
        self.assertEqual(code, 0)
        self.assertTrue(result["applied"])
        self.assertEqual(fake.calls, expected)
        isolated.assert_called_once_with(
            address=None,
            name_contains="MOLUS",
            profile="direct",
            service_uuid=None,
            write_uuid=None,
            notify_uuid=None,
            timeout=0.25,
            python="python-test",
        )

    def test_execute_plan_cli_can_post_bundle_to_bridge(self) -> None:
        class FakeBridgeClient:
            instances: list[FakeBridgeClient] = []

            def __init__(self, base_url: str, *, timeout: float = 3.0):
                self.base_url = base_url
                self.timeout = timeout
                self.calls = []
                self.instances.append(self)

            def execute_plan(self, plan, *, timeout=None):
                self.calls.append((plan, timeout))
                return {"action": "execute_plan", "applied": True}

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            self.write_scene_plan_bundle(path)
            stdout = io.StringIO()
            with (
                patch("zhiyun_light_control.cli.LightBridgeClient", FakeBridgeClient),
                contextlib.redirect_stdout(stdout),
            ):
                code = main(
                    [
                        "execute-plan",
                        "--base-url",
                        "http://bridge.test",
                        "--bridge-timeout",
                        "5",
                        "--timeout",
                        "0.25",
                        str(path),
                        "--yes",
                        "--json",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["applied"])
        client = FakeBridgeClient.instances[0]
        self.assertEqual(client.base_url, "http://bridge.test")
        self.assertEqual(client.timeout, 5.0)
        self.assertEqual(client.calls[0][1], 0.25)
        self.assertEqual(client.calls[0][0].summary()["planned_action"], "scene")

    def test_discover_usb_cli_runs_matrix(self) -> None:
        class FakeLight:
            def __init__(self) -> None:
                self.seq = 0

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_runtime(self, cmd, payload=b"", *, timeout=0.5):
                del timeout
                self.seq += 1
                tx = build_runtime_frame(self.seq, cmd, payload)
                rx = (
                    build_runtime_frame(self.seq, cmd, b"\x00")
                    if cmd
                    in {
                        RuntimeCommand.DEVICE_INFO,
                        RuntimeCommand.FIRMWARE,
                    }
                    else b""
                )
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    cmd,
                    tx,
                    rx,
                    frames,
                    first_response_frame(rx, tx=tx, cmd=cmd),
                )

            def exchange_frame(self, first_word, cmd, payload=b"", *, timeout=0.5):
                del timeout
                self.seq += 1
                tx = build_frame(first_word, self.seq, cmd, payload)
                rx = tx
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    cmd,
                    tx,
                    rx,
                    frames,
                    first_response_frame(rx, tx=tx, cmd=cmd),
                )

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=FakeLight(),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "discover-usb",
                    "--object-ids",
                    "1",
                    "--first-words",
                    "0x0301",
                    "--control-object-ids",
                    "0,1",
                    "--control-first-words",
                    "0x0100,0x0301",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["object_ids"], [1])
        self.assertEqual(payload["first_words"], [0x0301])
        self.assertEqual(payload["control_object_ids"], [])
        self.assertIn(
            "first_word_0x0301_read_brightness_obj0",
            {attempt["name"] for attempt in payload["attempts"]},
        )

    def test_discover_usb_cli_passes_control_discovery_options(self) -> None:
        class FakeLight:
            def __init__(self) -> None:
                self.seq = 0

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_runtime(self, cmd, payload=b"", *, timeout=0.5):
                del timeout
                self.seq += 1
                tx = build_runtime_frame(self.seq, cmd, payload)
                rx = (
                    build_runtime_frame(self.seq, cmd, b"\x00")
                    if cmd
                    in {
                        RuntimeCommand.DEVICE_INFO,
                        RuntimeCommand.FIRMWARE,
                        RuntimeCommand.REGISTER_DEFAULT_GROUP,
                    }
                    else b""
                )
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    cmd,
                    tx,
                    rx,
                    frames,
                    first_response_frame(rx, tx=tx, cmd=cmd),
                )

            def exchange_frame(self, first_word, cmd, payload=b"", *, timeout=0.5):
                del timeout
                self.seq += 1
                tx = build_frame(first_word, self.seq, cmd, payload)
                return CommandResult(cmd, tx, b"", (), None)

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=FakeLight(),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "discover-usb",
                    "--object-ids",
                    "1",
                    "--first-words",
                    "0x0100",
                    "--allow-control",
                    "--control-object-ids",
                    "1",
                    "--register-device-ids",
                    "0,1",
                    "--register-group-ids",
                    "0,2",
                    "--control-kinds",
                    "sleep",
                    "--control-modes",
                    "0x01",
                    "--post-register-reads",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        attempts = {attempt["name"] for attempt in payload["attempts"]}
        self.assertEqual(code, 0)
        self.assertEqual(payload["register_device_ids"], [0, 1])
        self.assertEqual(payload["register_group_ids"], [0, 2])
        self.assertEqual(payload["control_kinds"], ["sleep"])
        self.assertEqual(payload["control_modes"], [1])
        self.assertTrue(payload["post_register_reads"])
        self.assertIn("register_default_group_dev1_group2", attempts)
        self.assertIn("after_register_dev1_group2_read_brightness_obj1", attempts)
        self.assertIn("set_sleep_obj1_mode0x01", attempts)
        self.assertNotIn("set_brightness_obj1_mode0x01", attempts)

    def test_discover_usb_cli_g60_matrix_is_read_only_without_allow_control(
        self,
    ) -> None:
        class FakeLight:
            def __init__(self) -> None:
                self.seq = 0

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_runtime(self, cmd, payload=b"", *, timeout=0.5):
                del timeout
                self.seq += 1
                tx = build_runtime_frame(self.seq, cmd, payload)
                rx = (
                    build_runtime_frame(self.seq, cmd, b"\x00")
                    if cmd
                    in {
                        RuntimeCommand.DEVICE_INFO,
                        RuntimeCommand.FIRMWARE,
                    }
                    else b""
                )
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    cmd,
                    tx,
                    rx,
                    frames,
                    first_response_frame(rx, tx=tx, cmd=cmd),
                )

            def exchange_frame(self, first_word, cmd, payload=b"", *, timeout=0.5):
                del timeout
                self.seq += 1
                tx = build_frame(first_word, self.seq, cmd, payload)
                return CommandResult(cmd, tx, b"", (), None)

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=FakeLight(),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(["discover-usb", "--g60-matrix", "--json"])

        payload = json.loads(stdout.getvalue())
        categories = {attempt["category"] for attempt in payload["attempts"]}
        self.assertEqual(code, 0)
        self.assertEqual(payload["profile"], "g60")
        self.assertEqual(payload["object_ids"], [0, 1])
        self.assertEqual(payload["first_words"], [0x0100, 0x0101, 0x0103, 0x0301])
        self.assertFalse(payload["control_enabled"])
        self.assertEqual(payload["control_object_ids"], [])
        self.assertNotIn("control", categories)
        self.assertEqual(
            payload["workflow"]["read_only_command"],
            "zlight discover-usb --g60-matrix --json",
        )
        self.assertEqual(
            payload["workflow"]["control_command"],
            "zlight discover-usb --g60-matrix --allow-control --json",
        )

    def test_discover_usb_cli_g60_matrix_expands_control_candidates(self) -> None:
        class FakeLight:
            def __init__(self) -> None:
                self.seq = 0

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_runtime(self, cmd, payload=b"", *, timeout=0.5):
                del timeout
                self.seq += 1
                tx = build_runtime_frame(self.seq, cmd, payload)
                rx = (
                    build_runtime_frame(self.seq, cmd, b"\x00")
                    if cmd
                    in {
                        RuntimeCommand.DEVICE_INFO,
                        RuntimeCommand.FIRMWARE,
                        RuntimeCommand.REGISTER_DEFAULT_GROUP,
                    }
                    else b""
                )
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    cmd,
                    tx,
                    rx,
                    frames,
                    first_response_frame(rx, tx=tx, cmd=cmd),
                )

            def exchange_frame(self, first_word, cmd, payload=b"", *, timeout=0.5):
                del timeout
                self.seq += 1
                tx = build_frame(first_word, self.seq, cmd, payload)
                return CommandResult(cmd, tx, b"", (), None)

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=FakeLight(),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "discover-usb",
                    "--g60-matrix",
                    "--allow-control",
                    "--control-kinds",
                    "sleep",
                    "--control-modes",
                    "0x01",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        attempts = {attempt["name"] for attempt in payload["attempts"]}
        self.assertEqual(code, 0)
        self.assertEqual(payload["profile"], "g60")
        self.assertEqual(payload["control_object_ids"], [0, 1])
        self.assertEqual(
            payload["control_first_words"],
            [0x0100, 0x0101, 0x0103, 0x0301],
        )
        self.assertEqual(payload["register_device_ids"], [0, 1])
        self.assertEqual(payload["register_group_ids"], [0])
        self.assertIn("register_default_group_dev1_group0", attempts)
        self.assertIn("set_sleep_obj0_mode0x01", attempts)
        self.assertIn("set_sleep_obj0_mode0x01_fw0x0101", attempts)

    def test_frame_cli_exchanges_raw_usb_frame(self) -> None:
        class FakeLight:
            def __init__(self) -> None:
                self.call: tuple[int, int, bytes, float] | None = None

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_frame(self, first_word, cmd, payload=b"", *, timeout=0.5):
                self.call = (first_word, cmd, payload, timeout)
                tx = build_frame(first_word, 1, cmd, payload)
                rx = build_frame(first_word, 1, cmd, b"\x65")
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    cmd,
                    tx,
                    rx,
                    frames,
                    first_response_frame(rx, tx=tx, cmd=cmd),
                )

        fake = FakeLight()
        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=fake,
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "frame",
                    "--first-word",
                    "0x0100",
                    "--command",
                    "0x2001",
                    "--payload-hex",
                    "00 01",
                    "--timeout",
                    "0.35",
                    "--yes",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(fake.call, (0x0100, 0x2001, b"\x00\x01", 0.35))
        self.assertTrue(payload["acknowledged"])
        self.assertEqual(payload["transport_status"], "acknowledged")

    def test_frame_cli_requires_yes(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main(["frame", "--first-word", "0x0100", "--command", "0x2001"])

        self.assertIn("frame sends a raw frame", str(raised.exception))

    def test_ble_probe_uses_crash_isolated_client_by_default(self) -> None:
        class FakeProbe:
            def to_dict(self):
                return {"address": "AA", "firmware": "test"}

        class FakeAsyncLight:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                return

            async def probe(self):
                return FakeProbe()

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.AsyncZhiyunLight.isolated_ble",
                return_value=FakeAsyncLight(),
            ) as isolated,
            patch("zhiyun_light_control.cli.AsyncZhiyunLight.ble") as direct,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "probe",
                    "--transport",
                    "ble",
                    "--name-contains",
                    "MOLUS",
                    "--python",
                    "python-test",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["firmware"], "test")
        direct.assert_not_called()
        isolated.assert_called_once_with(
            address=None,
            name_contains="MOLUS",
            profile="direct",
            service_uuid=None,
            write_uuid=None,
            notify_uuid=None,
            timeout=1.5,
            python="python-test",
        )

    def test_ble_status_uses_crash_isolated_client_by_default(self) -> None:
        class FakeAsyncLight:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                return

            async def exchange_runtime(self, cmd, payload=b"", *, timeout=1.5):
                del payload, timeout
                payload_by_cmd = {
                    RuntimeCommand.DEVICE_INFO: b"device-test\x00pl103\x00",
                    RuntimeCommand.FIRMWARE: b"1.6.4\x00",
                    RuntimeCommand.VOLTAGE: b"\x65",
                    RuntimeCommand.DEVICE_ID: b"\x00\x00",
                }
                tx = build_runtime_frame(1, cmd)
                rx = build_runtime_frame(1, cmd, payload_by_cmd[cmd])
                frames = tuple(iter_frames(rx))
                return CommandResult(
                    cmd,
                    tx,
                    rx,
                    frames,
                    first_response_frame(rx, tx=tx, cmd=cmd),
                )

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.AsyncZhiyunLight.isolated_ble",
                return_value=FakeAsyncLight(),
            ) as isolated,
            patch("zhiyun_light_control.cli.AsyncZhiyunLight.ble") as direct,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "status",
                    "--transport",
                    "ble",
                    "--name-contains",
                    "MOLUS",
                    "--python",
                    "python-test",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transport"], "ble")
        self.assertEqual(payload["device_identifier"], "device-test")
        self.assertEqual(payload["firmware"], "1.6.4")
        self.assertIsNone(payload["chip_sync"])
        direct.assert_not_called()
        isolated.assert_called_once_with(
            address=None,
            name_contains="MOLUS",
            profile="direct",
            service_uuid=None,
            write_uuid=None,
            notify_uuid=None,
            timeout=1.5,
            python="python-test",
        )

    def test_ble_probe_can_opt_into_direct_client(self) -> None:
        class FakeProbe:
            def to_dict(self):
                return {"address": "AA", "firmware": "test"}

        class FakeAsyncLight:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                return

            async def probe(self):
                return FakeProbe()

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.AsyncZhiyunLight.isolated_ble"
            ) as isolated,
            patch(
                "zhiyun_light_control.cli.AsyncZhiyunLight.ble",
                return_value=FakeAsyncLight(),
            ) as direct,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "probe",
                    "--transport",
                    "ble",
                    "--unsafe-in-process",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["firmware"], "test")
        isolated.assert_not_called()
        direct.assert_called_once_with(
            address=None,
            name_contains=None,
            profile="direct",
            service_uuid=None,
            write_uuid=None,
            notify_uuid=None,
            timeout=1.5,
        )

    def test_ble_probe_passes_profile_and_custom_characteristics(self) -> None:
        class FakeProbe:
            def to_dict(self):
                return {"address": "AA", "firmware": "test"}

        class FakeAsyncLight:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                return

            async def probe(self):
                return FakeProbe()

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.AsyncZhiyunLight.isolated_ble",
                return_value=FakeAsyncLight(),
            ) as isolated,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "probe",
                    "--transport",
                    "ble",
                    "--ble-profile",
                    "legacy",
                    "--ble-service-uuid",
                    "service-test",
                    "--ble-write-uuid",
                    "write-test",
                    "--ble-notify-uuid",
                    "notify-test",
                    "--json",
                ]
            )

        self.assertEqual(code, 0)
        isolated.assert_called_once_with(
            address=None,
            name_contains=None,
            profile="legacy",
            service_uuid="service-test",
            write_uuid="write-test",
            notify_uuid="notify-test",
            timeout=1.5,
            python=None,
        )

    def test_ble_probe_can_use_macos_app_backend(self) -> None:
        class FakeProbe:
            def to_dict(self):
                return {"address": "UUID-1", "firmware": "test"}

        class FakeAsyncLight:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                return

            async def probe(self):
                return FakeProbe()

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.AsyncZhiyunLight.isolated_ble"
            ) as isolated,
            patch(
                "zhiyun_light_control.cli.AsyncZhiyunLight.macos_ble_app",
                return_value=FakeAsyncLight(),
            ) as macos_ble,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "probe",
                    "--transport",
                    "ble",
                    "--ble-backend",
                    "macos-app",
                    "--address",
                    "UUID-1",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["firmware"], "test")
        isolated.assert_not_called()
        macos_ble.assert_called_once_with(
            address="UUID-1",
            name_contains=None,
            profile="direct",
            service_uuid=None,
            write_uuid=None,
            notify_uuid=None,
            timeout=1.5,
        )

    def test_ble_validate_can_use_macos_app_backend(self) -> None:
        class FakeReport:
            connection_confirmed = True
            all_attempted_confirmed = True

            def to_dict(self):
                return {"connection_confirmed": True}

        class FakeAsyncLight:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                return

        async def fake_validate(_light, **kwargs):
            calls["kwargs"] = kwargs
            return FakeReport()

        calls = {}
        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.AsyncZhiyunLight.macos_ble_app",
                return_value=FakeAsyncLight(),
            ) as macos_ble,
            patch(
                "zhiyun_light_control.cli.validate_async_light",
                new=fake_validate,
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "validate",
                    "--transport",
                    "ble",
                    "--ble-backend",
                    "macos-app",
                    "--name-contains",
                    "PL103",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["connection_confirmed"])
        self.assertFalse(calls["kwargs"]["allow_control"])
        macos_ble.assert_called_once_with(
            address=None,
            name_contains="PL103",
            profile="direct",
            service_uuid=None,
            write_uuid=None,
            notify_uuid=None,
            timeout=1.5,
        )

    def test_ble_validate_worker_failure_returns_json_error(self) -> None:
        class FakeAsyncLight:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                return

        async def fake_validate(_light, **_kwargs):
            tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
            raise BleWorkerError(
                BleExchangeResult(
                    ok=False,
                    tx=tx,
                    error="Bluetooth state unauthorized: 3",
                    worker_python="macos-app",
                )
            )

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.AsyncZhiyunLight.macos_ble_app",
                return_value=FakeAsyncLight(),
            ),
            patch(
                "zhiyun_light_control.cli.validate_async_light",
                new=fake_validate,
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "validate",
                    "--transport",
                    "ble",
                    "--ble-backend",
                    "macos-app",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["exchange"]["worker_python"], "macos-app")

    def test_scan_ble_can_use_macos_app_backend(self) -> None:
        class FakeScanResult:
            ok = True

            def to_dict(self):
                return {
                    "ok": True,
                    "devices": [{"address": "UUID-1", "name": "PL103_EDFE"}],
                }

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.scan_zhiyun_devices_macos_app",
                return_value=FakeScanResult(),
            ) as scan,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "scan-ble",
                    "--backend",
                    "macos-app",
                    "--timeout",
                    "1",
                    "--name-contains",
                    "PL103",
                    "--include-all",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["devices"][0]["address"], "UUID-1")
        scan.assert_called_once_with(
            timeout=1.0,
            name_contains="PL103",
            include_all=True,
        )

    def test_devices_cli_reports_transport_discovery_state(self) -> None:
        response = {
            "api": "zhiyun-light-control",
            "configured_transport": "usb",
            "usb": {
                "available": True,
                "selected_port": "/dev/cu.usbmodem-test",
                "ports": [
                    {
                        "path": "/dev/cu.usbmodem-test",
                        "selected": True,
                    }
                ],
            },
            "ble": {
                "included": True,
                "backend": "macos-app",
                "macos_status": {
                    "ok": False,
                    "authorization": "not_determined",
                    "state": "unauthorized",
                },
                "scan": {
                    "ok": False,
                    "error": "Bluetooth state unauthorized: 3",
                },
            },
        }

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.discover_transport_devices",
                return_value=response,
            ) as discover,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "devices",
                    "--port",
                    "/dev/cu.usbmodem-test",
                    "--include-ble-status",
                    "--include-ble",
                    "--ble-backend",
                    "macos-app",
                    "--ble-timeout",
                    "1",
                    "--name-contains",
                    "PL103",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["usb"]["selected_port"], "/dev/cu.usbmodem-test")
        self.assertEqual(payload["ble"]["backend"], "macos-app")
        self.assertEqual(payload["ble"]["macos_status"]["state"], "unauthorized")
        discover.assert_called_once_with(
            configured_transport="usb",
            configured_usb_port="/dev/cu.usbmodem-test",
            include_ble=True,
            include_ble_status=True,
            ble_backend="macos-app",
            ble_timeout=1.0,
            ble_name_contains="PL103",
            ble_python=None,
        )

    def test_inspect_ble_can_use_macos_app_backend(self) -> None:
        class FakeInspectResult:
            ok = True

            def to_dict(self):
                return {
                    "ok": True,
                    "address": "UUID-1",
                    "services": [
                        {
                            "uuid": "service",
                            "characteristics": [
                                {
                                    "uuid": "write",
                                    "properties": ["write"],
                                }
                            ],
                        }
                    ],
                }

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.inspect_ble_device",
                return_value=FakeInspectResult(),
            ) as inspect_ble,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "inspect-ble",
                    "--backend",
                    "macos-app",
                    "--timeout",
                    "1",
                    "--name-contains",
                    "PL103",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["address"], "UUID-1")
        inspect_ble.assert_called_once_with(
            backend="macos-app",
            timeout=1.0,
            address=None,
            name_contains="PL103",
            python=None,
        )

    def test_test_ble_endpoints_can_use_macos_app_backend(self) -> None:
        class FakeEndpointReport:
            ok = True

            def to_dict(self):
                return {
                    "ok": True,
                    "backend": "macos-app",
                    "tests": [{"acknowledged": True}],
                }

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.test_ble_endpoint_candidates",
                return_value=FakeEndpointReport(),
            ) as test_ble,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "test-ble-endpoints",
                    "--backend",
                    "macos-app",
                    "--timeout",
                    "1",
                    "--name-contains",
                    "PL103",
                    "--max-candidates",
                    "2",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["backend"], "macos-app")
        self.assertTrue(payload["tests"][0]["acknowledged"])
        test_ble.assert_called_once_with(
            backend="macos-app",
            timeout=1.0,
            address=None,
            name_contains="PL103",
            python=None,
            max_candidates=2,
        )

    def test_mesh_session_runs_dynamic_ipc_flow(self) -> None:
        class FakePublicKey:
            xy = b"p" * 64

            def to_dict(self):
                return {"xy_hex": self.xy.hex()}

        class FakeKeypair:
            private_key = object()
            public_key = FakePublicKey()

        class FakeSecrets:
            def to_dict(self):
                return {"device_key_hex": "dd"}

        class FakeSessionResult:
            def to_dict(self):
                return {"ok": True, "tx_hexes": [], "rx_hexes": []}

        class FakeSession:
            def __init__(self):
                self.rx = [
                    bytes.fromhex("03010100010001000000000000"),
                    b"",
                    b"\x03\x03" + (b"k" * 64),
                    b"\x03\x05" + (b"c" * 16),
                    b"\x03\x06" + (b"r" * 16),
                ]
                self.tx: list[bytes] = []

            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange(self, tx: bytes, *, timeout: float):
                self.tx.append(tx)
                return self.rx.pop(0)

            def close(self):
                return FakeSessionResult()

        fake_session = FakeSession()
        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.open_zhiyun_ble_ipc_macos_app",
                return_value=fake_session,
            ) as open_session,
            patch(
                "zhiyun_light_control.cli.generate_provisioner_keypair",
                return_value=FakeKeypair(),
            ),
            patch(
                "zhiyun_light_control.cli.derive_shared_ecdh_secret",
                return_value=b"s" * 32,
            ),
            patch(
                "zhiyun_light_control.cli.generate_provisioning_random",
                return_value=b"q" * 16,
            ),
            patch(
                "zhiyun_light_control.cli.build_provisioner_confirmation",
                return_value=b"\x03\x05" + (b"p" * 16),
            ),
            patch(
                "zhiyun_light_control.cli.verify_provisionee_confirmation",
                return_value=True,
            ),
            patch(
                "zhiyun_light_control.cli.provisioning_session_secrets",
                return_value=FakeSecrets(),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "mesh-session",
                    "--name-contains",
                    "PL103",
                    "--timeout",
                    "2",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["confirmation_verified"])
        self.assertEqual(len(fake_session.tx), 5)
        self.assertEqual(payload["provisionee_random_hex"], (b"r" * 16).hex())
        self.assertEqual(len(payload["confirmation_inputs_hex"]), 290)
        open_session.assert_called_once()

    def test_mesh_provision_plan_builds_offline_pdu_from_session_json(self) -> None:
        session = {
            "shared_ecdh_secret_hex": (b"s" * 32).hex(),
            "confirmation_inputs_hex": bytes(range(145)).hex(),
            "provisioner_random_hex": (b"q" * 16).hex(),
            "provisionee_random_hex": (b"r" * 16).hex(),
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            path.write_text(json.dumps(session), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "mesh-provision-plan",
                        "--session-json",
                        str(path),
                        "--network-key-hex",
                        (b"n" * 16).hex(),
                        "--key-index",
                        "1",
                        "--flags",
                        "2",
                        "--iv-index",
                        "3",
                        "--unicast-address",
                        "4",
                        "--json",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["offline"])
        self.assertEqual(payload["plan"]["network_key_hex"], (b"n" * 16).hex())
        self.assertEqual(payload["plan"]["key_index"], 1)
        self.assertEqual(payload["plan"]["flags"], 2)
        self.assertEqual(payload["plan"]["iv_index"], 3)
        self.assertEqual(payload["plan"]["unicast_address"], 4)
        self.assertTrue(payload["plan"]["provisioning_data_pdu_hex"].startswith("0307"))

    def test_mesh_provision_plan_requires_new_session_transcript_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            path.write_text(
                json.dumps(
                    {
                        "shared_ecdh_secret_hex": (b"s" * 32).hex(),
                        "provisioner_random_hex": (b"q" * 16).hex(),
                        "provisionee_random_hex": (b"r" * 16).hex(),
                    }
                ),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = main(
                    [
                        "mesh-provision-plan",
                        "--session-json",
                        str(path),
                        "--network-key-hex",
                        (b"n" * 16).hex(),
                        "--json",
                    ]
                )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertIn("confirmation_inputs_hex", payload["error"])

    def test_mesh_setup_plan_builds_official_config_sequence(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(
                [
                    "mesh-setup-plan",
                    "--mesh-uuid-hex",
                    bytes(range(16)).hex(),
                    "--network-key-hex",
                    (b"n" * 16).hex(),
                    "--app-key-hex",
                    (b"a" * 16).hex(),
                    "--device-key-hex",
                    (b"d" * 16).hex(),
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["network"]["mesh_name"], "ZY Mesh Network")
        self.assertEqual(payload["cdb"]["meshUUID"], bytes(range(16)).hex().upper())
        self.assertEqual(
            [step["access_payload_hex"] for step in payload["config_sequence"]],
            [
                "8008ff",
                "800c",
                "80240a",
                "00000000" + (b"a" * 16).hex(),
            ],
        )
        self.assertEqual(
            [step["proxy_pdu_count"] for step in payload["proxy_pdu_sequence"]],
            [1, 1, 1, 2],
        )

    def test_ble_helper_reports_helper_and_opens_settings(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.macos_ble_app_info",
                return_value={
                    "ok": True,
                    "bundle_id": "local.zhiyun-light-control.ble-scan",
                    "app_path": "/tmp/ZhiyunBleScan.app",
                },
            ) as info,
            patch(
                "zhiyun_light_control.cli.open_macos_bluetooth_settings",
                return_value={"ok": True, "returncode": 0},
            ) as open_settings,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(["ble-helper", "--ensure", "--open-settings", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(
            payload["helper"]["bundle_id"],
            "local.zhiyun-light-control.ble-scan",
        )
        self.assertTrue(payload["open_settings"]["ok"])
        info.assert_called_once_with(
            ensure=True,
            bundle_name=None,
            bundle_id=None,
        )
        open_settings.assert_called_once_with()

    def test_ble_helper_status_reports_authorization_state(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.macos_ble_app_info",
                return_value={
                    "ok": True,
                    "bundle_id": "local.zhiyun-light-control.ble-scan",
                    "app_path": "/tmp/ZhiyunBleScan.app",
                },
            ),
            patch(
                "zhiyun_light_control.cli.macos_ble_app_status",
                return_value={
                    "ok": False,
                    "state": "unauthorized",
                    "authorization": "denied",
                    "error": "Bluetooth state unauthorized: 3",
                },
            ) as status,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(["ble-helper", "--status", "--timeout", "1.25", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(payload["status"]["state"], "unauthorized")
        self.assertEqual(payload["status"]["authorization"], "denied")
        status.assert_called_once_with(
            timeout=1.25,
            bundle_name=None,
            bundle_id=None,
        )

    def test_ble_helper_authorize_runs_long_lived_permission_request(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.macos_ble_app_info",
                return_value={
                    "ok": True,
                    "bundle_id": "local.zhiyun-light-control.ble-scan",
                    "app_path": "/tmp/ZhiyunBleScan.app",
                },
            ),
            patch(
                "zhiyun_light_control.cli.macos_ble_app_authorize",
                return_value={
                    "ok": True,
                    "state": "powered on",
                    "authorization": "allowed",
                },
            ) as authorize,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(["ble-helper", "--authorize", "--timeout", "30", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["authorization"]["authorization"], "allowed")
        authorize.assert_called_once_with(
            timeout=30.0,
            bundle_name=None,
            bundle_id=None,
        )

    def test_ble_helper_accepts_fresh_bundle_identity(self) -> None:
        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.macos_ble_app_info",
                return_value={
                    "ok": True,
                    "bundle_id": "local.zhiyun-light-control.ble-scan2",
                    "app_path": "/tmp/ZhiyunBleScan2.app",
                },
            ) as info,
            patch(
                "zhiyun_light_control.cli.macos_ble_app_authorize",
                return_value={
                    "ok": False,
                    "state": "unknown",
                    "authorization": "not_determined",
                },
            ) as authorize,
            contextlib.redirect_stdout(stdout),
        ):
            code = main(
                [
                    "ble-helper",
                    "--ensure",
                    "--authorize",
                    "--bundle-name",
                    "ZhiyunBleScan2",
                    "--bundle-id",
                    "local.zhiyun-light-control.ble-scan2",
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(
            payload["helper"]["bundle_id"],
            "local.zhiyun-light-control.ble-scan2",
        )
        info.assert_called_once_with(
            ensure=True,
            bundle_name="ZhiyunBleScan2",
            bundle_id="local.zhiyun-light-control.ble-scan2",
        )
        authorize.assert_called_once_with(
            timeout=3.0,
            bundle_name="ZhiyunBleScan2",
            bundle_id="local.zhiyun-light-control.ble-scan2",
        )

    def test_ble_probe_worker_failure_returns_json_error(self) -> None:
        class FakeAsyncLight:
            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb) -> None:
                return

            async def probe(self):
                tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
                raise BleWorkerError(
                    BleExchangeResult(
                        ok=False,
                        tx=tx,
                        error="worker terminated by signal 6 (SIGABRT)",
                        returncode=-6,
                        signal_name="SIGABRT",
                    )
                )

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.AsyncZhiyunLight.isolated_ble",
                return_value=FakeAsyncLight(),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(["probe", "--transport", "ble", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["transport"], "ble")
        self.assertEqual(payload["exchange"]["signal"], "SIGABRT")

    def test_bridge_cli_passes_ble_worker_options(self) -> None:
        with (
            patch("zhiyun_light_control.cli.make_light_factory") as make_factory,
            patch("zhiyun_light_control.cli.serve") as serve,
        ):
            make_factory.return_value = object()
            code = main(
                [
                    "serve",
                    "--transport",
                    "ble",
                    "--address",
                    "AA:BB",
                    "--ble-python",
                    "python-test",
                    "--unsafe-in-process",
                    "--usb-lock-timeout",
                    "0.25",
                    "--cors-origin",
                    "http://studio.local",
                ]
            )

        self.assertEqual(code, 0)
        config = make_factory.call_args.args[0]
        self.assertEqual(config.transport, "ble")
        self.assertEqual(config.address, "AA:BB")
        self.assertEqual(config.usb_lock_timeout, 0.25)
        self.assertEqual(config.ble_profile, "direct")
        self.assertEqual(config.ble_backend, "direct")
        self.assertIsNone(config.ble_service_uuid)
        self.assertIsNone(config.ble_write_uuid)
        self.assertIsNone(config.ble_notify_uuid)
        self.assertEqual(config.ble_python, "python-test")
        self.assertTrue(config.ble_in_process)
        self.assertEqual(serve.call_args.kwargs["cors_origin"], "http://studio.local")
        self.assertEqual(serve.call_args.kwargs["transport"], "ble")
        self.assertEqual(serve.call_args.kwargs["ble_backend"], "direct")
        self.assertEqual(serve.call_args.kwargs["ble_profile"], "direct")
        self.assertEqual(serve.call_args.kwargs["ble_address"], "AA:BB")
        serve.assert_called_once()

    def test_bridge_cli_passes_ble_profile_options(self) -> None:
        with (
            patch("zhiyun_light_control.cli.make_light_factory") as make_factory,
            patch("zhiyun_light_control.cli.serve"),
        ):
            make_factory.return_value = object()
            code = main(
                [
                    "serve",
                    "--transport",
                    "ble",
                    "--ble-profile",
                    "yc",
                    "--ble-backend",
                    "macos-app",
                    "--ble-service-uuid",
                    "service-test",
                    "--ble-write-uuid",
                    "write-test",
                    "--ble-notify-uuid",
                    "notify-test",
                ]
            )

        self.assertEqual(code, 0)
        config = make_factory.call_args.args[0]
        self.assertEqual(config.ble_profile, "yc")
        self.assertEqual(config.ble_backend, "macos-app")
        self.assertEqual(config.ble_service_uuid, "service-test")
        self.assertEqual(config.ble_write_uuid, "write-test")
        self.assertEqual(config.ble_notify_uuid, "notify-test")

    def test_bridge_cli_can_disable_cors(self) -> None:
        with (
            patch("zhiyun_light_control.cli.make_light_factory") as make_factory,
            patch("zhiyun_light_control.cli.serve") as serve,
        ):
            make_factory.return_value = object()
            code = main(["serve", "--cors-origin", "none"])

        self.assertEqual(code, 0)
        self.assertIsNone(serve.call_args.kwargs["cors_origin"])


if __name__ == "__main__":
    unittest.main()
