from __future__ import annotations

import unittest

from zhiyun_light_control.mesh import (
    MESH_MESSAGE_TYPE_PROVISIONING,
    PROVISIONING_INVITE,
    build_mesh_proxy_pdu,
    build_provisioning_invite,
    build_provisioning_public_key,
    build_provisioning_start_no_oob,
    parse_mesh_proxy_pdu,
    parse_provisioning_capabilities,
    parse_provisioning_public_key,
)


class MeshProvisioningTests(unittest.TestCase):
    def test_build_provisioning_invite_wraps_proxy_header(self) -> None:
        self.assertEqual(build_provisioning_invite(5), bytes.fromhex("030005"))

    def test_build_mesh_proxy_pdu_validates_header_fields(self) -> None:
        self.assertEqual(
            build_mesh_proxy_pdu(MESH_MESSAGE_TYPE_PROVISIONING, b"\x00"),
            bytes([MESH_MESSAGE_TYPE_PROVISIONING, PROVISIONING_INVITE]),
        )
        with self.assertRaises(ValueError):
            build_mesh_proxy_pdu(0x40, b"")
        with self.assertRaises(ValueError):
            build_mesh_proxy_pdu(0, b"", sar=4)

    def test_parse_mesh_proxy_pdu(self) -> None:
        pdu = parse_mesh_proxy_pdu(bytes.fromhex("03010100010001000000000000"))
        self.assertEqual(pdu.sar, 0)
        self.assertEqual(pdu.message_type, MESH_MESSAGE_TYPE_PROVISIONING)
        self.assertEqual(pdu.payload.hex(), "010100010001000000000000")

    def test_parse_provisioning_capabilities_from_g60_probe(self) -> None:
        capabilities = parse_provisioning_capabilities(
            bytes.fromhex("03010100010001000000000000")
        )
        self.assertEqual(capabilities.number_of_elements, 1)
        self.assertEqual(capabilities.algorithms, 1)
        self.assertTrue(capabilities.supports_fips_p256_ecdh)
        self.assertEqual(capabilities.public_key_type, 0)
        self.assertEqual(capabilities.static_oob_type, 1)
        self.assertEqual(capabilities.output_oob_size, 0)
        self.assertEqual(capabilities.output_oob_action, 0)
        self.assertEqual(capabilities.input_oob_size, 0)
        self.assertEqual(capabilities.input_oob_action, 0)

    def test_build_provisioning_start_no_oob_matches_nordic_shape(self) -> None:
        self.assertEqual(
            build_provisioning_start_no_oob(),
            bytes.fromhex("03020000000000"),
        )

    def test_build_and_parse_provisioning_public_key(self) -> None:
        xy = bytes(range(64))
        pdu = build_provisioning_public_key(xy)
        self.assertEqual(pdu, bytes.fromhex("0303") + xy)
        parsed = parse_provisioning_public_key(pdu)
        self.assertEqual(parsed.x, bytes(range(32)))
        self.assertEqual(parsed.y, bytes(range(32, 64)))
        self.assertEqual(parse_provisioning_public_key(xy).xy, xy)

    def test_build_provisioning_public_key_validates_size(self) -> None:
        with self.assertRaises(ValueError):
            build_provisioning_public_key(b"\x00" * 63)

    def test_parse_provisioning_capabilities_rejects_wrong_pdu(self) -> None:
        with self.assertRaises(ValueError):
            parse_provisioning_capabilities(b"")
        with self.assertRaises(ValueError):
            parse_provisioning_capabilities(bytes.fromhex("000101"))


if __name__ == "__main__":
    unittest.main()
