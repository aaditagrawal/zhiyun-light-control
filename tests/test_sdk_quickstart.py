from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType


def load_quickstart() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "examples" / "sdk_quickstart.py"
    spec = importlib.util.spec_from_file_location("sdk_quickstart", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load sdk_quickstart example")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SdkQuickstartTests(unittest.TestCase):
    def test_builds_config_from_setup_report(self) -> None:
        quickstart = load_quickstart()

        config = quickstart.config_from_setup(
            {
                "config": {
                    "transport": "usb",
                    "port": "/dev/cu.usbmodem21301",
                    "persistent": True,
                }
            }
        )

        self.assertEqual(config.transport, "usb")
        self.assertEqual(config.port, "/dev/cu.usbmodem21301")
        self.assertTrue(config.persistent)

    def test_rejects_setup_report_without_config(self) -> None:
        quickstart = load_quickstart()

        with self.assertRaisesRegex(ValueError, "config"):
            quickstart.config_from_setup({"ok": False})


if __name__ == "__main__":
    unittest.main()
