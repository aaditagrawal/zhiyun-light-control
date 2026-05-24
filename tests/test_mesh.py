from __future__ import annotations

import unittest

from zhiyun_light_control.mesh import (
    MESH_MESSAGE_TYPE_PROVISIONING,
    PROVISIONING_INVITE,
    aes_cmac,
    build_config_app_key_add_params,
    build_mesh_config_sequence_plan,
    build_mesh_proxy_pdu,
    build_provisioner_confirmation,
    build_provisioner_random,
    build_provisioning_data,
    build_provisioning_data_plan,
    build_provisioning_invite,
    build_provisioning_public_key,
    build_provisioning_start_no_oob,
    build_zy_mesh_network_plan,
    confirmation_inputs,
    generate_application_key,
    generate_network_key,
    mesh_k1,
    mesh_salt,
    pack_mesh_key_indexes,
    parse_mesh_proxy_pdu,
    parse_provisioning_capabilities,
    parse_provisioning_confirmation,
    parse_provisioning_failure,
    parse_provisioning_public_key,
    parse_provisioning_random,
    provisioning_data_plaintext,
    provisioning_session_secrets,
    verify_provisionee_confirmation,
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

    def test_confirmation_inputs_follow_nordic_payload_slices(self) -> None:
        invite = build_provisioning_invite(5)
        capabilities = bytes.fromhex("03010100010001000000000000")
        start = build_provisioning_start_no_oob()
        provisioner_key = bytes(range(64))
        provisionee_key = bytes(range(64, 128))

        inputs = confirmation_inputs(
            invite_pdu=invite,
            capabilities_pdu=capabilities,
            start_pdu=start,
            provisioner_public_key_xy=provisioner_key,
            provisionee_public_key_xy=provisionee_key,
        )

        self.assertEqual(
            inputs,
            bytes.fromhex("05")
            + bytes.fromhex("0100010001000000000000")
            + bytes.fromhex("0000000000")
            + provisioner_key
            + provisionee_key,
        )

    def test_confirmation_and_random_pdus_round_trip(self) -> None:
        shared_secret = bytes(range(32))
        inputs = bytes(range(145))
        random = bytes(range(16))

        confirmation = build_provisioner_confirmation(
            shared_secret=shared_secret,
            confirmation_inputs=inputs,
            provisioner_random=random,
        )
        parsed_confirmation = parse_provisioning_confirmation(confirmation)
        self.assertEqual(len(parsed_confirmation), 16)
        self.assertTrue(
            verify_provisionee_confirmation(
                shared_secret=shared_secret,
                confirmation_inputs=inputs,
                provisionee_confirmation=parsed_confirmation,
                provisionee_random=random,
            )
        )

        random_pdu = build_provisioner_random(random)
        self.assertEqual(random_pdu, bytes.fromhex("0306") + random)
        self.assertEqual(parse_provisioning_random(random_pdu), random)

    def test_parse_provisioning_failure(self) -> None:
        failure = parse_provisioning_failure(bytes.fromhex("030904"))

        self.assertEqual(failure.code, 4)
        self.assertEqual(failure.reason, "confirmation_failed")
        self.assertEqual(failure.to_dict()["code_hex"], "0x04")

    def test_provisioning_data_encryption_derives_session_secrets(self) -> None:
        shared_secret = bytes(range(32))
        inputs = bytes(range(145))
        provisioner_random = bytes(range(16))
        provisionee_random = bytes(range(16, 32))
        network_key = bytes(range(32, 48))

        plaintext = provisioning_data_plaintext(
            network_key=network_key,
            key_index=0,
            flags=0,
            iv_index=0x01020304,
            unicast_address=0x0005,
        )
        pdu, secrets = build_provisioning_data(
            shared_secret=shared_secret,
            confirmation_inputs=inputs,
            provisioner_random=provisioner_random,
            provisionee_random=provisionee_random,
            network_key=network_key,
            key_index=0,
            flags=0,
            iv_index=0x01020304,
            unicast_address=0x0005,
        )

        self.assertEqual(len(plaintext), 25)
        self.assertEqual(len(secrets.session_nonce), 13)
        self.assertEqual(len(secrets.session_key), 16)
        self.assertEqual(len(secrets.device_key), 16)
        self.assertEqual(len(pdu), 35)
        self.assertEqual(pdu[:2], bytes.fromhex("0307"))
        self.assertEqual(
            provisioning_session_secrets(
                shared_secret=shared_secret,
                confirmation_inputs=inputs,
                provisioner_random=provisioner_random,
                provisionee_random=provisionee_random,
            ),
            secrets,
        )

    def test_provisioning_data_plan_serializes_offline_send_artifact(self) -> None:
        plan = build_provisioning_data_plan(
            shared_secret=bytes(range(32)),
            confirmation_inputs=bytes(range(145)),
            provisioner_random=bytes(range(16)),
            provisionee_random=bytes(range(16, 32)),
            network_key=bytes(range(32, 48)),
            key_index=1,
            flags=2,
            iv_index=3,
            unicast_address=4,
        )

        payload = plan.to_dict()
        self.assertEqual(plan.pdu[:2], bytes.fromhex("0307"))
        self.assertEqual(payload["network_key_hex"], bytes(range(32, 48)).hex())
        self.assertEqual(payload["key_index_hex"], "0x001")
        self.assertEqual(payload["flags_hex"], "0x02")
        self.assertEqual(payload["iv_index_hex"], "0x00000003")
        self.assertEqual(payload["unicast_address_hex"], "0x0004")
        self.assertIn("session_key_hex", payload["session_secrets"])

    def test_generate_network_key_returns_mesh_key_size(self) -> None:
        self.assertEqual(len(generate_network_key()), 16)
        self.assertEqual(len(generate_application_key()), 16)

    def test_zy_mesh_network_plan_matches_official_defaults(self) -> None:
        plan = build_zy_mesh_network_plan(
            mesh_uuid=bytes(range(16)),
            network_key=bytes(range(16, 32)),
            app_key=bytes(range(32, 48)),
            iv_index=1,
            light_unicast_address=0x0005,
        )

        payload = plan.to_dict()
        cdb = plan.to_cdb_dict()

        self.assertEqual(payload["mesh_name"], "ZY Mesh Network")
        self.assertEqual(
            payload["provisioner_uuid_hex"],
            "9ee44bef29fc41e89e53ee567a2118df",
        )
        self.assertEqual(
            payload["provisioner_device_key_hex"],
            "cabf7e4ac8b9e254372bbd6146d318bb",
        )
        self.assertEqual(payload["group_address_hex"], "0xc000")
        self.assertEqual(cdb["meshUUID"], bytes(range(16)).hex().upper())
        self.assertEqual(cdb["netKeys"][0]["key"], bytes(range(16, 32)).hex().upper())
        self.assertEqual(cdb["appKeys"][0]["key"], bytes(range(32, 48)).hex().upper())
        self.assertEqual(
            cdb["provisioners"][0]["UUID"],
            "9EE44BEF29FC41E89E53EE567A2118DF",
        )

    def test_mesh_config_sequence_matches_vega_register_order(self) -> None:
        app_key = bytes(range(16))
        sequence = build_mesh_config_sequence_plan(app_key)

        self.assertEqual(
            [step.name for step in sequence],
            [
                "config_composition_data_get",
                "config_default_ttl_get",
                "config_network_transmit_set",
                "config_app_key_add",
            ],
        )
        self.assertEqual(sequence[0].access_payload.hex(), "8008ff")
        self.assertEqual(sequence[0].delay_after_status_ms, 500)
        self.assertEqual(sequence[1].access_payload.hex(), "800c")
        self.assertEqual(sequence[2].access_payload.hex(), "80240a")
        self.assertEqual(sequence[3].access_payload.hex(), "00000000" + app_key.hex())
        self.assertEqual(sequence[3].expected_status_opcode, 0x8003)

    def test_mesh_key_index_packing_matches_app_key_add_shape(self) -> None:
        self.assertEqual(pack_mesh_key_indexes(0, 0), bytes.fromhex("000000"))
        self.assertEqual(pack_mesh_key_indexes(0x123, 0x456), bytes.fromhex("236145"))
        self.assertEqual(
            build_config_app_key_add_params(bytes(range(16))),
            bytes.fromhex("000000") + bytes(range(16)),
        )

    def test_mesh_crypto_helpers_match_known_shapes(self) -> None:
        self.assertEqual(len(aes_cmac(b"abc", b"\x00" * 16)), 16)
        salt = mesh_salt(b"abc")
        self.assertEqual(len(salt), 16)
        self.assertEqual(len(mesh_k1(b"data", salt, b"text")), 16)

    def test_parse_provisioning_capabilities_rejects_wrong_pdu(self) -> None:
        with self.assertRaises(ValueError):
            parse_provisioning_capabilities(b"")
        with self.assertRaises(ValueError):
            parse_provisioning_capabilities(bytes.fromhex("000101"))


if __name__ == "__main__":
    unittest.main()
