from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from zhiyun_light_control.transports.usb import (
    _acquire_usb_lock,
    _default_lock_path,
    _release_usb_lock,
)


class UsbLockTests(unittest.TestCase):
    def test_default_lock_path_is_stable_and_outside_device_tree(self) -> None:
        first = _default_lock_path("/dev/cu.usbmodem21301")
        second = _default_lock_path("/dev/cu.usbmodem21301")

        self.assertEqual(first, second)
        self.assertEqual(Path(first).parent, Path(tempfile.gettempdir()))
        self.assertTrue(Path(first).name.startswith("zhiyun-light-control-"))

    def test_usb_lock_serializes_same_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = os.path.join(tmp, "usb.lock")
            first = _acquire_usb_lock(
                "/dev/cu.test",
                timeout=0,
                lock_path=lock_path,
            )
            try:
                with self.assertRaisesRegex(TimeoutError, "USB port lock"):
                    _acquire_usb_lock(
                        "/dev/cu.test",
                        timeout=0,
                        lock_path=lock_path,
                    )
            finally:
                _release_usb_lock(first)

            second = _acquire_usb_lock(
                "/dev/cu.test",
                timeout=0,
                lock_path=lock_path,
            )
            _release_usb_lock(second)


if __name__ == "__main__":
    unittest.main()
