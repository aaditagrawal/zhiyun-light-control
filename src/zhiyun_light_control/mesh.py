"""Bluetooth Mesh provisioning helpers for Zhiyun lights."""

from __future__ import annotations

import os
from dataclasses import dataclass

MESH_SAR_COMPLETE = 0
MESH_MESSAGE_TYPE_PROVISIONING = 0x03

PROVISIONING_INVITE = 0x00
PROVISIONING_CAPABILITIES = 0x01
PROVISIONING_START = 0x02
PROVISIONING_PUBLIC_KEY = 0x03
PROVISIONING_CONFIRMATION = 0x05
PROVISIONING_RANDOM = 0x06
PROVISIONING_DATA = 0x07
PROVISIONING_FAILED = 0x09

FIPS_P256_ECDH_ALGORITHM = 0
NO_OOB_AUTHENTICATION = 0
NO_OOB_AUTH_VALUE = b"\x00" * 16
ZERO_AES_KEY = b"\x00" * 16
PRCK = b"prck"
PRSK = b"prsk"
PRSN = b"prsn"
PRDK = b"prdk"


@dataclass(frozen=True)
class MeshProxyPdu:
    sar: int
    message_type: int
    payload: bytes

    def to_dict(self) -> dict[str, object]:
        return {
            "sar": self.sar,
            "message_type": self.message_type,
            "message_type_hex": f"0x{self.message_type:02x}",
            "payload_hex": self.payload.hex(),
        }


@dataclass(frozen=True)
class ProvisioningCapabilities:
    number_of_elements: int
    algorithms: int
    public_key_type: int
    static_oob_type: int
    output_oob_size: int
    output_oob_action: int
    input_oob_size: int
    input_oob_action: int

    @property
    def supports_fips_p256_ecdh(self) -> bool:
        return bool(self.algorithms & 0x0001)

    def to_dict(self) -> dict[str, object]:
        return {
            "number_of_elements": self.number_of_elements,
            "algorithms": self.algorithms,
            "algorithms_hex": f"0x{self.algorithms:04x}",
            "supports_fips_p256_ecdh": self.supports_fips_p256_ecdh,
            "public_key_type": self.public_key_type,
            "static_oob_type": self.static_oob_type,
            "output_oob_size": self.output_oob_size,
            "output_oob_action": self.output_oob_action,
            "output_oob_action_hex": f"0x{self.output_oob_action:04x}",
            "input_oob_size": self.input_oob_size,
            "input_oob_action": self.input_oob_action,
            "input_oob_action_hex": f"0x{self.input_oob_action:04x}",
        }


@dataclass(frozen=True)
class ProvisioningPublicKey:
    x: bytes
    y: bytes

    @property
    def xy(self) -> bytes:
        return self.x + self.y

    def to_dict(self) -> dict[str, object]:
        return {
            "x_hex": self.x.hex(),
            "y_hex": self.y.hex(),
            "xy_hex": self.xy.hex(),
        }


@dataclass(frozen=True)
class ProvisionerKeypair:
    private_key: object
    public_key: ProvisioningPublicKey

    def to_dict(self) -> dict[str, object]:
        return {"public_key": self.public_key.to_dict()}


@dataclass(frozen=True)
class ProvisioningSessionSecrets:
    confirmation_salt: bytes
    confirmation_key: bytes
    provisioning_salt: bytes
    session_key: bytes
    session_nonce: bytes
    device_key: bytes

    def to_dict(self) -> dict[str, object]:
        return {
            "confirmation_salt_hex": self.confirmation_salt.hex(),
            "confirmation_key_hex": self.confirmation_key.hex(),
            "provisioning_salt_hex": self.provisioning_salt.hex(),
            "session_key_hex": self.session_key.hex(),
            "session_nonce_hex": self.session_nonce.hex(),
            "device_key_hex": self.device_key.hex(),
        }


@dataclass(frozen=True)
class ProvisioningFailure:
    code: int

    @property
    def reason(self) -> str:
        return {
            0x01: "invalid_pdu",
            0x02: "invalid_format",
            0x03: "unexpected_pdu",
            0x04: "confirmation_failed",
            0x05: "out_of_resources",
            0x06: "decryption_failed",
            0x07: "unexpected_error",
            0x08: "cannot_assign_addresses",
        }.get(self.code, "unknown")

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "code_hex": f"0x{self.code:02x}",
            "reason": self.reason,
        }


def build_mesh_proxy_pdu(
    message_type: int,
    payload: bytes,
    *,
    sar: int = MESH_SAR_COMPLETE,
) -> bytes:
    if not 0 <= sar <= 3:
        raise ValueError("sar must fit in two bits")
    if not 0 <= message_type <= 0x3F:
        raise ValueError("message_type must fit in six bits")
    return bytes([((sar & 0x03) << 6) | (message_type & 0x3F)]) + payload


def build_provisioning_invite(attention_duration: int = 5) -> bytes:
    if not 0 <= attention_duration <= 0xFF:
        raise ValueError("attention_duration must fit in one byte")
    return build_mesh_proxy_pdu(
        MESH_MESSAGE_TYPE_PROVISIONING,
        bytes([PROVISIONING_INVITE, attention_duration]),
    )


def build_provisioning_start_no_oob(
    *,
    algorithm: int = FIPS_P256_ECDH_ALGORITHM,
    public_key_type: int = 0,
) -> bytes:
    return build_provisioning_start(
        algorithm=algorithm,
        public_key_type=public_key_type,
        auth_method=NO_OOB_AUTHENTICATION,
        auth_action=0,
        auth_size=0,
    )


def build_provisioning_start(
    *,
    algorithm: int,
    public_key_type: int,
    auth_method: int,
    auth_action: int,
    auth_size: int,
) -> bytes:
    for name, value in (
        ("algorithm", algorithm),
        ("public_key_type", public_key_type),
        ("auth_method", auth_method),
        ("auth_action", auth_action),
        ("auth_size", auth_size),
    ):
        if not 0 <= value <= 0xFF:
            raise ValueError(f"{name} must fit in one byte")
    return build_mesh_proxy_pdu(
        MESH_MESSAGE_TYPE_PROVISIONING,
        bytes(
            [
                PROVISIONING_START,
                algorithm,
                public_key_type,
                auth_method,
                auth_action,
                auth_size,
            ]
        ),
    )


def build_provisioning_public_key(public_key_xy: bytes) -> bytes:
    if len(public_key_xy) != 64:
        raise ValueError("provisioning public key must be 64 bytes of x||y")
    return build_mesh_proxy_pdu(
        MESH_MESSAGE_TYPE_PROVISIONING,
        bytes([PROVISIONING_PUBLIC_KEY]) + public_key_xy,
    )


def build_provisioner_confirmation(
    *,
    shared_secret: bytes,
    confirmation_inputs: bytes,
    provisioner_random: bytes,
    auth_value: bytes = NO_OOB_AUTH_VALUE,
) -> bytes:
    if len(provisioner_random) != 16:
        raise ValueError("provisioner_random must be 16 bytes")
    if len(auth_value) != 16:
        raise ValueError("auth_value must be 16 bytes")
    confirmation_salt = mesh_salt(confirmation_inputs)
    confirmation_key = mesh_k1(shared_secret, confirmation_salt, PRCK)
    confirmation = aes_cmac(provisioner_random + auth_value, confirmation_key)
    return build_mesh_proxy_pdu(
        MESH_MESSAGE_TYPE_PROVISIONING,
        bytes([PROVISIONING_CONFIRMATION]) + confirmation,
    )


def build_provisioner_random(provisioner_random: bytes) -> bytes:
    if len(provisioner_random) != 16:
        raise ValueError("provisioner_random must be 16 bytes")
    return build_mesh_proxy_pdu(
        MESH_MESSAGE_TYPE_PROVISIONING,
        bytes([PROVISIONING_RANDOM]) + provisioner_random,
    )


def build_provisioning_data(
    *,
    shared_secret: bytes,
    confirmation_inputs: bytes,
    provisioner_random: bytes,
    provisionee_random: bytes,
    network_key: bytes,
    key_index: int,
    flags: int,
    iv_index: int,
    unicast_address: int,
) -> tuple[bytes, ProvisioningSessionSecrets]:
    plaintext = provisioning_data_plaintext(
        network_key=network_key,
        key_index=key_index,
        flags=flags,
        iv_index=iv_index,
        unicast_address=unicast_address,
    )
    secrets = provisioning_session_secrets(
        shared_secret=shared_secret,
        confirmation_inputs=confirmation_inputs,
        provisioner_random=provisioner_random,
        provisionee_random=provisionee_random,
    )
    encrypted = aes_ccm_encrypt(
        plaintext,
        key=secrets.session_key,
        nonce=secrets.session_nonce,
        mic_size=8,
    )
    pdu = build_mesh_proxy_pdu(
        MESH_MESSAGE_TYPE_PROVISIONING,
        bytes([PROVISIONING_DATA]) + encrypted,
    )
    return pdu, secrets


def generate_provisioner_keypair() -> ProvisionerKeypair:
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:
        raise RuntimeError(
            "Bluetooth Mesh public-key generation requires the 'mesh' extra: "
            "uv run --extra mesh ..."
        ) from exc

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_numbers = private_key.public_key().public_numbers()
    public_key = ProvisioningPublicKey(
        x=public_numbers.x.to_bytes(32, "big"),
        y=public_numbers.y.to_bytes(32, "big"),
    )
    return ProvisionerKeypair(private_key=private_key, public_key=public_key)


def derive_shared_ecdh_secret(
    private_key: object,
    provisionee_public_key_xy: bytes,
) -> bytes:
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:
        raise RuntimeError(
            "Bluetooth Mesh ECDH requires the 'mesh' extra: uv run --extra mesh ..."
        ) from exc

    provisionee = parse_provisioning_public_key(provisionee_public_key_xy)
    peer_numbers = ec.EllipticCurvePublicNumbers(
        int.from_bytes(provisionee.x, "big"),
        int.from_bytes(provisionee.y, "big"),
        ec.SECP256R1(),
    )
    peer_public_key = peer_numbers.public_key()
    return private_key.exchange(ec.ECDH(), peer_public_key)


def generate_provisioning_random() -> bytes:
    return os.urandom(16)


def confirmation_inputs(
    *,
    invite_pdu: bytes,
    capabilities_pdu: bytes,
    start_pdu: bytes,
    provisioner_public_key_xy: bytes,
    provisionee_public_key_xy: bytes,
) -> bytes:
    for name, value in (
        ("invite_pdu", invite_pdu),
        ("capabilities_pdu", capabilities_pdu),
        ("start_pdu", start_pdu),
        ("provisioner_public_key_xy", provisioner_public_key_xy),
        ("provisionee_public_key_xy", provisionee_public_key_xy),
    ):
        if not value:
            raise ValueError(f"{name} is required")
    if len(provisioner_public_key_xy) != 64:
        raise ValueError("provisioner public key must be 64 bytes of x||y")
    if len(provisionee_public_key_xy) != 64:
        raise ValueError("provisionee public key must be 64 bytes of x||y")
    return (
        _provisioning_payload_without_type(invite_pdu, PROVISIONING_INVITE)
        + _provisioning_payload_without_type(
            capabilities_pdu,
            PROVISIONING_CAPABILITIES,
        )
        + _provisioning_payload_without_type(start_pdu, PROVISIONING_START)
        + provisioner_public_key_xy
        + provisionee_public_key_xy
    )


def verify_provisionee_confirmation(
    *,
    shared_secret: bytes,
    confirmation_inputs: bytes,
    provisionee_confirmation: bytes,
    provisionee_random: bytes,
    auth_value: bytes = NO_OOB_AUTH_VALUE,
) -> bool:
    if len(provisionee_confirmation) != 16:
        raise ValueError("provisionee_confirmation must be 16 bytes")
    if len(provisionee_random) != 16:
        raise ValueError("provisionee_random must be 16 bytes")
    if len(auth_value) != 16:
        raise ValueError("auth_value must be 16 bytes")
    confirmation_salt = mesh_salt(confirmation_inputs)
    confirmation_key = mesh_k1(shared_secret, confirmation_salt, PRCK)
    expected = aes_cmac(provisionee_random + auth_value, confirmation_key)
    return expected == provisionee_confirmation


def provisioning_session_secrets(
    *,
    shared_secret: bytes,
    confirmation_inputs: bytes,
    provisioner_random: bytes,
    provisionee_random: bytes,
) -> ProvisioningSessionSecrets:
    if len(provisioner_random) != 16:
        raise ValueError("provisioner_random must be 16 bytes")
    if len(provisionee_random) != 16:
        raise ValueError("provisionee_random must be 16 bytes")
    confirmation_salt = mesh_salt(confirmation_inputs)
    provisioning_salt = mesh_salt(
        confirmation_salt + provisioner_random + provisionee_random
    )
    session_key = mesh_k1(shared_secret, provisioning_salt, PRSK)
    session_nonce = mesh_k1(shared_secret, provisioning_salt, PRSN)[3:]
    device_key = mesh_k1(shared_secret, provisioning_salt, PRDK)
    return ProvisioningSessionSecrets(
        confirmation_salt=confirmation_salt,
        confirmation_key=mesh_k1(shared_secret, confirmation_salt, PRCK),
        provisioning_salt=provisioning_salt,
        session_key=session_key,
        session_nonce=session_nonce,
        device_key=device_key,
    )


def provisioning_data_plaintext(
    *,
    network_key: bytes,
    key_index: int,
    flags: int,
    iv_index: int,
    unicast_address: int,
) -> bytes:
    if len(network_key) != 16:
        raise ValueError("network_key must be 16 bytes")
    if not 0 <= key_index <= 0x0FFF:
        raise ValueError("key_index must fit in 12 bits")
    if not 0 <= flags <= 0xFF:
        raise ValueError("flags must fit in one byte")
    if not 0 <= iv_index <= 0xFFFFFFFF:
        raise ValueError("iv_index must fit in four bytes")
    if not 0x0001 <= unicast_address <= 0x7FFF:
        raise ValueError("unicast_address must be a unicast address")
    return (
        network_key
        + key_index.to_bytes(2, "big")
        + bytes([flags])
        + iv_index.to_bytes(4, "big")
        + unicast_address.to_bytes(2, "big")
    )


def mesh_salt(data: bytes) -> bytes:
    return aes_cmac(data, ZERO_AES_KEY)


def mesh_k1(data: bytes, salt: bytes, text: bytes) -> bytes:
    return aes_cmac(text, aes_cmac(data, salt))


def aes_cmac(data: bytes, key: bytes) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers import algorithms
        from cryptography.hazmat.primitives.cmac import CMAC
    except ImportError as exc:
        raise RuntimeError(
            "Bluetooth Mesh CMAC requires the 'mesh' extra: uv run --extra mesh ..."
        ) from exc

    cmac = CMAC(algorithms.AES(key))
    cmac.update(data)
    return cmac.finalize()


def aes_ccm_encrypt(
    data: bytes,
    *,
    key: bytes,
    nonce: bytes,
    mic_size: int,
) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESCCM
    except ImportError as exc:
        raise RuntimeError(
            "Bluetooth Mesh AES-CCM requires the 'mesh' extra: uv run --extra mesh ..."
        ) from exc

    return AESCCM(key, tag_length=mic_size).encrypt(nonce, data, b"")


def parse_mesh_proxy_pdu(data: bytes) -> MeshProxyPdu:
    if not data:
        raise ValueError("mesh proxy PDU is empty")
    header = data[0]
    return MeshProxyPdu(
        sar=(header >> 6) & 0x03,
        message_type=header & 0x3F,
        payload=data[1:],
    )


def parse_provisioning_capabilities(data: bytes) -> ProvisioningCapabilities:
    proxy = parse_mesh_proxy_pdu(data)
    if proxy.sar != MESH_SAR_COMPLETE:
        raise ValueError("segmented provisioning capabilities are not supported yet")
    if proxy.message_type != MESH_MESSAGE_TYPE_PROVISIONING:
        raise ValueError("mesh proxy PDU is not a provisioning message")
    payload = proxy.payload
    if len(payload) < 12:
        raise ValueError("provisioning capabilities payload is too short")
    if payload[0] != PROVISIONING_CAPABILITIES:
        raise ValueError("provisioning PDU is not capabilities")
    return ProvisioningCapabilities(
        number_of_elements=payload[1],
        algorithms=int.from_bytes(payload[2:4], "big"),
        public_key_type=payload[4],
        static_oob_type=payload[5],
        output_oob_size=payload[6],
        output_oob_action=int.from_bytes(payload[7:9], "big"),
        input_oob_size=payload[9],
        input_oob_action=int.from_bytes(payload[10:12], "big"),
    )


def parse_provisioning_public_key(data: bytes) -> ProvisioningPublicKey:
    if len(data) == 64:
        return ProvisioningPublicKey(x=data[:32], y=data[32:])
    proxy = parse_mesh_proxy_pdu(data)
    payload = proxy.payload
    if (
        proxy.message_type == MESH_MESSAGE_TYPE_PROVISIONING
        and payload
        and payload[0] == PROVISIONING_PUBLIC_KEY
    ):
        payload = payload[1:]
    if len(payload) != 64:
        raise ValueError("provisioning public key must contain 64 bytes of x||y")
    return ProvisioningPublicKey(x=payload[:32], y=payload[32:])


def parse_provisioning_confirmation(data: bytes) -> bytes:
    payload = _typed_provisioning_payload(data, PROVISIONING_CONFIRMATION)
    if len(payload) != 16:
        raise ValueError("provisioning confirmation must be 16 bytes")
    return payload


def parse_provisioning_random(data: bytes) -> bytes:
    payload = _typed_provisioning_payload(data, PROVISIONING_RANDOM)
    if len(payload) != 16:
        raise ValueError("provisioning random must be 16 bytes")
    return payload


def parse_provisioning_failure(data: bytes) -> ProvisioningFailure:
    payload = _typed_provisioning_payload(data, PROVISIONING_FAILED)
    if len(payload) != 1:
        raise ValueError("provisioning failure must contain one error code")
    return ProvisioningFailure(code=payload[0])


def _typed_provisioning_payload(data: bytes, pdu_type: int) -> bytes:
    proxy = parse_mesh_proxy_pdu(data)
    if proxy.sar != MESH_SAR_COMPLETE:
        raise ValueError("segmented provisioning PDUs are not supported yet")
    if proxy.message_type != MESH_MESSAGE_TYPE_PROVISIONING:
        raise ValueError("mesh proxy PDU is not a provisioning message")
    if not proxy.payload or proxy.payload[0] != pdu_type:
        raise ValueError(f"provisioning PDU is not type 0x{pdu_type:02x}")
    return proxy.payload[1:]


def _provisioning_payload_without_type(data: bytes, pdu_type: int) -> bytes:
    return _typed_provisioning_payload(data, pdu_type)
