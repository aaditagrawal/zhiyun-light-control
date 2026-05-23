from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from zhiyun_light_control.cli import main


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


if __name__ == "__main__":
    unittest.main()
