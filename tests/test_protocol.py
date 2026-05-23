from __future__ import annotations

import unittest

from zhiyun_light_control.protocol import (
    LEGACY_CONTROL_MODE,
    RuntimeCommand,
    UpdaterCommand,
    brightness_with_mode_payload,
    build_frame,
    build_runtime_frame,
    build_updater_frame,
    first_frame,
    first_response_frame,
    has_echo_frame,
    parse_chip_sync,
    parse_device_id,
    parse_device_info,
    parse_read_sn,
    parse_version,
    register_payload,
    sleep_payload,
)


class ProtocolTests(unittest.TestCase):
    def test_runtime_frame_matches_live_probe(self) -> None:
        frame = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
        self.assertEqual(frame.hex(), "243c0600000101000320d4ad")

    def test_custom_frame_builder_sets_first_word(self) -> None:
        frame = build_frame(0x0301, 1, RuntimeCommand.DEVICE_INFO)
        parsed = first_frame(frame)
        self.assertEqual(parsed.first_word, 0x0301)

    def test_updater_frame_matches_live_probe(self) -> None:
        frame = build_updater_frame(7, UpdaterCommand.CHIP_SYNC)
        self.assertEqual(frame.hex(), "243c0600030107000013ce17")

    def test_register_frame_matches_live_probe(self) -> None:
        frame = build_runtime_frame(
            1,
            RuntimeCommand.REGISTER_DEFAULT_GROUP,
            register_payload(0),
        )
        self.assertEqual(frame.hex(), "243c0a00000101000600000000001121")

    def test_response_frame_skips_exact_write_echo(self) -> None:
        tx = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO)
        ack = build_runtime_frame(1, RuntimeCommand.DEVICE_INFO, b"\x00")
        rx = tx + ack

        self.assertTrue(has_echo_frame(rx, tx))
        frame = first_response_frame(rx, tx=tx, cmd=RuntimeCommand.DEVICE_INFO)

        self.assertIsNotNone(frame)
        self.assertEqual(frame.payload, b"\x00")

    def test_response_frame_returns_none_for_only_echo(self) -> None:
        tx = build_runtime_frame(1, RuntimeCommand.BRIGHTNESS, b"\x00\x00\x00")

        self.assertIsNone(
            first_response_frame(tx, tx=tx, cmd=RuntimeCommand.BRIGHTNESS)
        )

    def test_brightness_with_mode_payload_shape(self) -> None:
        payload = brightness_with_mode_payload(1, 35.0, 1)
        self.assertEqual(payload.hex(), "01003300000c4201")

    def test_functional_write_payload_control_mode_is_overridable(self) -> None:
        self.assertEqual(sleep_payload(1, 1).hex(), "01003301")
        self.assertEqual(
            sleep_payload(1, 1, control_mode=LEGACY_CONTROL_MODE).hex(),
            "01000101",
        )
        self.assertEqual(sleep_payload(1, read=True).hex(), "01000000")

    def test_parse_device_info_after_upgrade(self) -> None:
        rx = bytes.fromhex(
            "243c220001000100032030386134303965306331313030313133"
            "00706c3130330000000000004f58"
        )
        frame = first_frame(rx, cmd=RuntimeCommand.DEVICE_INFO)
        self.assertIsNotNone(frame)
        info = parse_device_info(frame)
        self.assertEqual(info.identifier, "08a409e0c1100113")
        self.assertEqual(info.generation, "pl103")

    def test_parse_firmware_after_upgrade(self) -> None:
        rx = bytes.fromhex("243c0c00010002000180312e362e340003c3")
        frame = first_frame(rx, cmd=RuntimeCommand.FIRMWARE)
        self.assertEqual(parse_version(frame), "1.6.4")

    def test_parse_device_id_after_upgrade(self) -> None:
        rx = bytes.fromhex("243c08000100060005200000b1f0")
        frame = first_frame(rx, cmd=RuntimeCommand.DEVICE_ID)
        self.assertEqual(parse_device_id(frame), 0)

    def test_parse_chip_sync_after_upgrade(self) -> None:
        rx = bytes.fromhex(
            "243c1b000103070000130048444c0000010010030041054008a40065a36075fc30"
        )
        frame = first_frame(rx, cmd=UpdaterCommand.CHIP_SYNC)
        chip = parse_chip_sync(frame)
        self.assertEqual(chip.core_id, "HDL")
        self.assertEqual(chip.product, 0x0541)
        self.assertEqual(chip.hardware, 0x0840)
        self.assertEqual(chip.firmware_raw, 164)
        self.assertEqual(chip.flash_size, 1048576)

    def test_parse_read_sn_after_upgrade(self) -> None:
        rx = bytes.fromhex("243c1100010301000213004105130110c1e009a408cd00")
        frame = first_frame(rx, cmd=UpdaterCommand.READ_SN)
        read_sn = parse_read_sn(frame)

        self.assertEqual(read_sn.prefix, 0)
        self.assertEqual(read_sn.product, 0x0541)
        self.assertEqual(read_sn.identifier_little_endian_hex, "130110c1e009a408")
        self.assertEqual(read_sn.device_identifier, "08a409e0c1100113")


if __name__ == "__main__":
    unittest.main()
