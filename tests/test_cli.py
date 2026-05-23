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

    def test_validate_strict_fails_when_control_is_unconfirmed(self) -> None:
        class FakeLight:
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

    def test_set_returns_nonzero_when_command_is_unacknowledged(self) -> None:
        class FakeLight:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def exchange_runtime(self, cmd, payload=b"", *, timeout=0.8):
                del payload, timeout
                tx = build_runtime_frame(1, cmd)
                return CommandResult(cmd, tx, b"", (), None)

        stdout = io.StringIO()
        with (
            patch(
                "zhiyun_light_control.cli.ZhiyunLight.usb",
                return_value=FakeLight(),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(["set", "brightness", "--value", "35", "--yes"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(payload["acknowledged"])
        self.assertEqual(payload["transport_status"], "sent_no_response")

    def test_apply_returns_nonzero_when_any_command_is_unacknowledged(self) -> None:
        class FakeLight:
            def __enter__(self):
                return self

            def __exit__(self, _exc_type, _exc, _tb):
                return None

            def apply_scene(self, _scene):
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
                    "--json",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["object_ids"], [1])
        self.assertEqual(payload["first_words"], [0x0301])
        self.assertIn(
            "first_word_0x0301_read_brightness_obj0",
            {attempt["name"] for attempt in payload["attempts"]},
        )

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
            timeout=1.5,
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


if __name__ == "__main__":
    unittest.main()
