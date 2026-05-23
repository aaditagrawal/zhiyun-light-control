"""Minimal Art-Net / DMX bridge for lighting-control integrations."""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Any, Callable

from .client import ZhiyunLight
from .models import Scene


ARTNET_ID = b"Art-Net\x00"
OP_DMX = 0x5000


class ArtNetError(ValueError):
    pass


@dataclass(frozen=True)
class ArtDmxPacket:
    sequence: int
    physical: int
    universe: int
    data: bytes

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "physical": self.physical,
            "universe": self.universe,
            "length": len(self.data),
            "data_hex": self.data.hex(),
        }


@dataclass(frozen=True)
class DmxMapping:
    """DMX channel mapping.

    Channels are one-based absolute DMX channel numbers within the ArtDmx data.
    ``sleep_channel`` is disabled by default because the exact power/sleep
    semantics still need more live validation.
    """

    obj: int = 1
    brightness_channel: int | None = 1
    cct_channel: int | None = 2
    sleep_channel: int | None = None
    cct_min: int = 2700
    cct_max: int = 6500


@dataclass(frozen=True)
class ArtNetDispatchResult:
    packet: ArtDmxPacket
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


def encode_artdmx(
    data: bytes,
    *,
    universe: int = 0,
    sequence: int = 0,
    physical: int = 0,
    protocol_version: int = 14,
) -> bytes:
    if len(data) > 512:
        raise ValueError("DMX payload may not exceed 512 bytes")
    if len(data) % 2:
        data += b"\x00"
    sub_uni = universe & 0xFF
    net = (universe >> 8) & 0x7F
    return (
        ARTNET_ID
        + struct.pack("<H", OP_DMX)
        + struct.pack(">HBBBBH", protocol_version, sequence & 0xFF, physical & 0xFF, sub_uni, net, len(data))
        + data
    )


def decode_artdmx(packet: bytes) -> ArtDmxPacket:
    if len(packet) < 18:
        raise ArtNetError("Art-Net packet too short")
    if packet[:8] != ARTNET_ID:
        raise ArtNetError("invalid Art-Net id")
    opcode = struct.unpack_from("<H", packet, 8)[0]
    if opcode != OP_DMX:
        raise ArtNetError(f"unsupported Art-Net opcode 0x{opcode:04x}")
    sequence = packet[12]
    physical = packet[13]
    sub_uni = packet[14]
    net = packet[15]
    length = struct.unpack_from(">H", packet, 16)[0]
    if length > 512:
        raise ArtNetError("ArtDmx length exceeds 512")
    if 18 + length > len(packet):
        raise ArtNetError("ArtDmx payload truncated")
    universe = (net << 8) | sub_uni
    return ArtDmxPacket(
        sequence=sequence,
        physical=physical,
        universe=universe,
        data=packet[18 : 18 + length],
    )


class ArtNetLightDispatcher:
    def __init__(
        self,
        light_factory: Callable[[], Any],
        *,
        universe: int = 0,
        mapping: DmxMapping = DmxMapping(),
        allow_control: bool = False,
    ):
        self.light_factory = light_factory
        self.universe = universe
        self.mapping = mapping
        self.allow_control = allow_control
        self._last_scene: Scene | None = None

    def dispatch(self, packet: ArtDmxPacket) -> ArtNetDispatchResult:
        if packet.universe != self.universe:
            return ArtNetDispatchResult(packet=packet, scene=None, applied=False, reason="universe_ignored")
        scene = scene_from_dmx(packet.data, self.mapping)
        if scene == self._last_scene:
            return ArtNetDispatchResult(packet=packet, scene=scene, applied=False, reason="unchanged")
        if not self.allow_control:
            return ArtNetDispatchResult(packet=packet, scene=scene, applied=False, reason="control_disabled")
        with self.light_factory() as light:
            light.apply_scene(scene)
        self._last_scene = scene
        return ArtNetDispatchResult(packet=packet, scene=scene, applied=True)


def scene_from_dmx(data: bytes, mapping: DmxMapping = DmxMapping()) -> Scene:
    brightness = None
    kelvin = None
    sleep = None
    if mapping.brightness_channel is not None:
        brightness = _channel(data, mapping.brightness_channel) / 255.0 * 100.0
    if mapping.cct_channel is not None:
        cct_value = _channel(data, mapping.cct_channel)
        kelvin = round(mapping.cct_min + (cct_value / 255.0 * (mapping.cct_max - mapping.cct_min)))
    if mapping.sleep_channel is not None:
        sleep = 0 if _channel(data, mapping.sleep_channel) >= 128 else 1
    return Scene(obj=mapping.obj, brightness=brightness, kelvin=kelvin, sleep=sleep)


def serve_artnet(
    *,
    host: str = "0.0.0.0",
    port: int = 6454,
    universe: int = 0,
    light_port: str | None = None,
    mapping: DmxMapping = DmxMapping(),
    allow_control: bool = False,
    once: bool = False,
    light_factory: Callable[[], Any] | None = None,
) -> None:
    dispatcher = ArtNetLightDispatcher(
        light_factory=light_factory or (lambda: ZhiyunLight.usb(port=light_port)),
        universe=universe,
        mapping=mapping,
        allow_control=allow_control,
    )
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((host, port))
        while True:
            data, _addr = sock.recvfrom(1024)
            try:
                dispatcher.dispatch(decode_artdmx(data))
            except ArtNetError:
                pass
            if once:
                return


def _channel(data: bytes, channel: int) -> int:
    if channel < 1:
        raise ValueError("DMX channels are one-based")
    index = channel - 1
    return data[index] if index < len(data) else 0
