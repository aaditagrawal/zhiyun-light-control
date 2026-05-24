"""Bluetooth Mesh provisioning helpers for Zhiyun lights."""

from __future__ import annotations

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

FIPS_P256_ECDH_ALGORITHM = 0
NO_OOB_AUTHENTICATION = 0


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
