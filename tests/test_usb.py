from __future__ import annotations

import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from zhiyun_light_control.transports.usb import (
    _acquire_usb_lock,
    _darwin_usb_port_metadata,
    _darwin_usbmodem_location_id,
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

    def test_macos_usbmodem_location_is_derived_from_port_name(self) -> None:
        self.assertEqual(
            _darwin_usbmodem_location_id("/dev/cu.usbmodem21301"),
            0x02130000,
        )

    def test_macos_usb_metadata_maps_descriptor_by_location(self) -> None:
        tree = [
            {
                "IORegistryEntryChildren": [
                    {
                        "locationID": 0x02130000,
                        "idVendor": 0xFFF8,
                        "idProduct": 0x0180,
                        "bcdDevice": 0x0200,
                        "USB Vendor Name": "Zhiyun Tech",
                        "USB Product Name": "Zhiyun Virtual ComPort",
                        "USBSpeed": 1,
                        "UsbLinkSpeed": 12000000,
                    }
                ]
            }
        ]
        completed = subprocess.CompletedProcess(
            args=("ioreg",),
            returncode=0,
            stdout=plistlib.dumps(tree),
            stderr=b"",
        )

        with patch(
            "zhiyun_light_control.transports.usb.subprocess.run",
            return_value=completed,
        ):
            metadata = _darwin_usb_port_metadata(("/dev/cu.usbmodem21301",))

        port = metadata["/dev/cu.usbmodem21301"]
        self.assertEqual(port["location_id_hex"], "0x02130000")
        self.assertEqual(port["vendor_id"], 0xFFF8)
        self.assertEqual(port["vendor_id_hex"], "0xfff8")
        self.assertEqual(port["product_id"], 0x0180)
        self.assertEqual(port["product_name"], "Zhiyun Virtual ComPort")
        self.assertEqual(port["source"], "macos-ioreg")


if __name__ == "__main__":
    unittest.main()
