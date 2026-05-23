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
        self.assertIn("register_default_group_dev1_group2", attempts)
        self.assertIn("set_sleep_obj1_mode0x01", attempts)
        self.assertNotIn("set_brightness_obj1_mode0x01", attempts)

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
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["devices"][0]["address"], "UUID-1")
        scan.assert_called_once_with(timeout=1.0, name_contains="PL103")

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
        info.assert_called_once_with(ensure=True)
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
        status.assert_called_once_with(timeout=1.25)

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
