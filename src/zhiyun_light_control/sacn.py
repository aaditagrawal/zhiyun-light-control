"""Minimal sACN / E1.31 DMX bridge for lighting-control integrations."""

from __future__ import annotations

import contextlib
import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass

from .artnet import DEFAULT_DMX_MAPPING, DmxMapping, scene_from_dmx
from .bridge import close_light_factory
from .client import ZhiyunLight
from .models import Scene
from .state import SceneStateTracker, results_confirmed, unconfirmed_results_reason

ACN_PACKET_IDENTIFIER = b"ASC-E1.17\x00\x00\x00"
VECTOR_ROOT_E131_DATA = 0x00000004
VECTOR_E131_DATA_PACKET = 0x00000002
VECTOR_DMP_SET_PROPERTY = 0x02
DMP_ADDRESS_TYPE_DATA_TYPE = 0xA1
DMX_START_CODE = 0x00
DEFAULT_SACN_PORT = 5568


class SacnError(ValueError):
    pass


@dataclass(frozen=True)
class SacnPacket:
    sequence: int
    universe: int
    priority: int
    source_name: str
    data: bytes

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "universe": self.universe,
            "priority": self.priority,
            "source_name": self.source_name,
            "length": len(self.data),
            "data_hex": self.data.hex(),
        }


@dataclass(frozen=True)
class SacnDispatchResult:
    packet: SacnPacket
    scene: Scene | None
    applied: bool
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "packet": self.packet.to_dict(),
            "scene": self.scene.to_dict() if self.scene else None,
            "applied": self.applied,
            "reason": self.reason,
        }


def encode_sacn(
    data: bytes,
    *,
    universe: int = 1,
    sequence: int = 0,
    priority: int = 100,
    source_name: str = "zhiyun-light-control",
    cid: bytes | None = None,
) -> bytes:
    _validate_universe(universe)
    if len(data) > 512:
        raise ValueError("DMX payload may not exceed 512 bytes")
    cid = bytes(16) if cid is None else cid
    if len(cid) != 16:
        raise ValueError("sACN CID must be 16 bytes")

    source = source_name.encode("utf-8")[:63] + b"\x00"
    source = source.ljust(64, b"\x00")
    dmp_values = bytes([DMX_START_CODE]) + data
    dmp = (
        _flags_and_length(10 + len(dmp_values))
        + struct.pack(
            ">BBHHH",
            VECTOR_DMP_SET_PROPERTY,
            DMP_ADDRESS_TYPE_DATA_TYPE,
            0,
            1,
            len(dmp_values),
        )
        + dmp_values
    )
    framing = (
        _flags_and_length(77 + len(dmp))
        + struct.pack(">I", VECTOR_E131_DATA_PACKET)
        + source
        + struct.pack(">BHBBH", priority & 0xFF, 0, sequence & 0xFF, 0, universe)
        + dmp
    )
    root = (
        struct.pack(">HH", 0x0010, 0x0000)
        + ACN_PACKET_IDENTIFIER
        + _flags_and_length(22 + len(framing))
        + struct.pack(">I", VECTOR_ROOT_E131_DATA)
        + cid
        + framing
    )
    return root


def decode_sacn(packet: bytes) -> SacnPacket:
    if len(packet) < 126:
        raise SacnError("sACN packet too short")
    preamble_size, postamble_size = struct.unpack_from(">HH", packet, 0)
    if preamble_size != 0x0010 or postamble_size != 0x0000:
        raise SacnError("invalid sACN preamble")
    if packet[4:16] != ACN_PACKET_IDENTIFIER:
        raise SacnError("invalid ACN packet identifier")
    _read_flags_length(packet, 16)
    root_vector = struct.unpack_from(">I", packet, 18)[0]
    if root_vector != VECTOR_ROOT_E131_DATA:
        raise SacnError(f"unsupported sACN root vector 0x{root_vector:08x}")
    framing_offset = 38
    _read_flags_length(packet, framing_offset)
    framing_vector = struct.unpack_from(">I", packet, framing_offset + 2)[0]
    if framing_vector != VECTOR_E131_DATA_PACKET:
        raise SacnError(f"unsupported sACN framing vector 0x{framing_vector:08x}")
    source_name = packet[framing_offset + 6 : framing_offset + 70].split(b"\x00", 1)[0]
    priority = packet[framing_offset + 70]
    sequence = packet[framing_offset + 73]
    universe = struct.unpack_from(">H", packet, framing_offset + 75)[0]
    if not 1 <= universe <= 63999:
        raise SacnError("sACN universe must be in range 1..63999")

    dmp_offset = 115
    dmp_length = _read_flags_length(packet, dmp_offset)
    if dmp_offset + dmp_length > len(packet):
        raise SacnError("sACN DMP layer truncated")
    dmp_vector = packet[dmp_offset + 2]
    address_type = packet[dmp_offset + 3]
    first_address = struct.unpack_from(">H", packet, dmp_offset + 4)[0]
    address_increment = struct.unpack_from(">H", packet, dmp_offset + 6)[0]
    value_count = struct.unpack_from(">H", packet, dmp_offset + 8)[0]
    values_offset = dmp_offset + 10
    if dmp_vector != VECTOR_DMP_SET_PROPERTY:
        raise SacnError(f"unsupported sACN DMP vector 0x{dmp_vector:02x}")
    if address_type != DMP_ADDRESS_TYPE_DATA_TYPE:
        raise SacnError(f"unsupported sACN DMP address type 0x{address_type:02x}")
    if first_address != 0 or address_increment != 1:
        raise SacnError("unsupported sACN DMP address range")
    if value_count < 1 or value_count > 513:
        raise SacnError("invalid sACN DMP property value count")
    if values_offset + value_count > len(packet):
        raise SacnError("sACN DMP property values truncated")
    values = packet[values_offset : values_offset + value_count]
    if values[0] != DMX_START_CODE:
        raise SacnError(f"unsupported sACN DMX start code 0x{values[0]:02x}")

    return SacnPacket(
        sequence=sequence,
        universe=universe,
        priority=priority,
        source_name=source_name.decode("utf-8", errors="replace"),
        data=values[1:],
    )


class SacnLightDispatcher:
    def __init__(
        self,
        light_factory: Callable[[], object],
        *,
        universe: int = 1,
        mapping: DmxMapping = DEFAULT_DMX_MAPPING,
        allow_control: bool = False,
        state_tracker: SceneStateTracker | None = None,
    ):
        _validate_universe(universe)
        self.light_factory = light_factory
        self.universe = universe
        self.mapping = mapping
        self.allow_control = allow_control
        self._last_scene: Scene | None = None
        self.state_tracker = state_tracker or SceneStateTracker()

    def dispatch(self, packet: SacnPacket) -> SacnDispatchResult:
        if packet.universe != self.universe:
            return SacnDispatchResult(
                packet=packet,
                scene=None,
                applied=False,
                reason="universe_ignored",
            )
        scene = scene_from_dmx(packet.data, self.mapping)
        if scene == self._last_scene:
            return SacnDispatchResult(
                packet=packet,
                scene=scene,
                applied=False,
                reason="unchanged",
            )
        if not self.allow_control:
            self._record_scene(scene, applied=False, reason="control_disabled")
            return SacnDispatchResult(
                packet=packet,
                scene=scene,
                applied=False,
                reason="control_disabled",
            )
        with self.light_factory() as light:
            results = light.apply_scene(scene)
        applied = results_confirmed(results)
        reason = None if applied else unconfirmed_results_reason(results)
        self._record_scene(scene, applied=applied, reason=reason, results=results)
        self._last_scene = scene
        return SacnDispatchResult(
            packet=packet,
            scene=scene,
            applied=applied,
            reason=reason,
        )

    def _record_scene(
        self,
        scene: Scene,
        *,
        applied: bool,
        reason: str | None = None,
        results=(),
    ) -> None:
        self.state_tracker.record(
            scene,
            source="sacn",
            action="scene",
            applied=applied,
            reason=reason,
            results=results,
        )


def serve_sacn(
    *,
    host: str = "0.0.0.0",
    port: int = DEFAULT_SACN_PORT,
    universe: int = 1,
    light_port: str | None = None,
    mapping: DmxMapping = DEFAULT_DMX_MAPPING,
    allow_control: bool = False,
    once: bool = False,
    multicast: bool = False,
    light_factory: Callable[[], object] | None = None,
    state_tracker: SceneStateTracker | None = None,
) -> None:
    dispatcher = SacnLightDispatcher(
        light_factory=light_factory or (lambda: ZhiyunLight.usb(port=light_port)),
        universe=universe,
        mapping=mapping,
        allow_control=allow_control,
        state_tracker=state_tracker,
    )
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            if multicast:
                group = socket.inet_aton(sacn_multicast_address(universe))
                sock.setsockopt(
                    socket.IPPROTO_IP,
                    socket.IP_ADD_MEMBERSHIP,
                    group + socket.inet_aton("0.0.0.0"),
                )
            while True:
                data, _addr = sock.recvfrom(1500)
                with contextlib.suppress(SacnError):
                    dispatcher.dispatch(decode_sacn(data))
                if once:
                    return
    finally:
        close_light_factory(dispatcher.light_factory)


def sacn_multicast_address(universe: int) -> str:
    _validate_universe(universe)
    return f"239.255.{(universe >> 8) & 0xFF}.{universe & 0xFF}"


def _flags_and_length(length: int) -> bytes:
    if length > 0x0FFF:
        raise ValueError("sACN PDU length exceeds 12-bit range")
    return struct.pack(">H", 0x7000 | length)


def _read_flags_length(packet: bytes, offset: int) -> int:
    if offset + 2 > len(packet):
        raise SacnError("sACN flags/length truncated")
    raw = struct.unpack_from(">H", packet, offset)[0]
    if raw >> 12 != 0x7:
        raise SacnError("invalid sACN flags")
    return raw & 0x0FFF


def _validate_universe(universe: int) -> None:
    if not 1 <= universe <= 63999:
        raise ValueError("sACN universe must be in range 1..63999")
