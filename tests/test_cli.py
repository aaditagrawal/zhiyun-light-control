from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zhiyun_light_control.cli import main
from zhiyun_light_control.protocol import build_runtime_frame, first_frame


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
        with patch("zhiyun_light_control.cli.ZhiyunLight.usb", return_value=FakeLight()):
            with contextlib.redirect_stdout(stdout):
                code = main(["validate", "--allow-control", "--strict", "--json"])

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(payload["all_attempted_confirmed"])
        self.assertIn("set_brightness", payload["unconfirmed"])


if __name__ == "__main__":
    unittest.main()
