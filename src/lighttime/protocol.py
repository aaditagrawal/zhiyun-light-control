"""ZYBL protocol, implemented from captured G60 messages and app analysis."""

import binascii
import math
import struct
from dataclasses import dataclass
from enum import IntEnum

SERVICE_UUID = "0000fee9-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "d44bc439-abfd-45a2-b575-925416129600"
NOTIFY_UUID = "d44bc439-abfd-45a2-b575-925416129601"
MAGIC = b"$<"
MAX_BODY = 1024


class Command(IntEnum):
    BRIGHTNESS = 0x1001
    CCT = 0x1002
    INFO = 0x2003
    DEVICE_ID = 0x2005


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Frame:
    command: int
    sequence: int
    payload: bytes = b""
    message_type: int = 0x0100

    def encode(self) -> bytes:
        body = struct.pack("<HHH", self.message_type, self.sequence, self.command) + self.payload
        if len(body) > MAX_BODY:
            raise ProtocolError("Frame body too large")
        return (
            MAGIC
            + struct.pack("<H", len(body))
            + body
            + struct.pack("<H", binascii.crc_hqx(body, 0))
        )

    @classmethod
    def decode(cls, raw: bytes) -> "Frame":
        if len(raw) < 12 or raw[:2] != MAGIC:
            raise ProtocolError("Invalid frame header")
        length = struct.unpack_from("<H", raw, 2)[0]
        if not 6 <= length <= MAX_BODY or len(raw) != length + 6:
            raise ProtocolError("Invalid frame length")
        body = raw[4:-2]
        if binascii.crc_hqx(body, 0) != struct.unpack_from("<H", raw, len(raw) - 2)[0]:
            raise ProtocolError("CRC mismatch")
        message_type, sequence, command = struct.unpack_from("<HHH", body)
        return cls(command, sequence, body[6:], message_type)


class FrameParser:
    """Incremental parser for both serial reads and fragmented BLE notifications."""

    def __init__(self):
        self.buffer = bytearray()
        self.invalid_frames = 0

    def feed(self, data: bytes | bytearray) -> list[Frame]:
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 2:
            index = self.buffer.find(MAGIC)
            if index < 0:
                self.buffer[:] = b"$" if self.buffer[-1:] == b"$" else b""
                break
            if index:
                del self.buffer[:index]
            if len(self.buffer) < 4:
                break
            length = struct.unpack_from("<H", self.buffer, 2)[0]
            if not 6 <= length <= MAX_BODY:
                self.invalid_frames += 1
                del self.buffer[0]
                continue
            total = length + 6
            if len(self.buffer) < total:
                break
            try:
                frames.append(Frame.decode(bytes(self.buffer[:total])))
            except ProtocolError:
                self.invalid_frames += 1
                del self.buffer[0]
            else:
                del self.buffer[:total]
        return frames


def validate_brightness(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Brightness must be a number from 0 to 100")
    if not math.isfinite(value) or not 0 <= value <= 100 or not float(value).is_integer():
        raise ValueError("G60 brightness must be a whole percent from 0 to 100")
    return float(value)


def validate_cct(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 2700 <= value <= 6500
        or value % 100
    ):
        raise ValueError("G60 CCT must be 2700 to 6500 K in steps of 100 K")
    return value


def control_payload(command: Command, device_id: int, value: float | None = None) -> bytes:
    if command == Command.BRIGHTNESS:
        encoded = struct.pack("<f", 0 if value is None else validate_brightness(value))
    elif command == Command.CCT:
        encoded = struct.pack("<H", 0 if value is None else validate_cct(value))
    else:
        raise ValueError("Unsupported control command")
    return struct.pack("<HB", device_id, value is not None) + encoded


def control_value(frame: Frame, device_id: int) -> float | int:
    size = 7 if frame.command == Command.BRIGHTNESS else 5
    if frame.command not in (Command.BRIGHTNESS, Command.CCT) or len(frame.payload) != size:
        raise ProtocolError("Invalid control response size")
    if struct.unpack_from("<H", frame.payload)[0] != device_id:
        raise ProtocolError("Response belongs to another device")
    if frame.payload[2] not in (0, 1):
        raise ProtocolError("Invalid read/write flag")
    if frame.command == Command.BRIGHTNESS:
        return validate_brightness(struct.unpack_from("<f", frame.payload, 3)[0])
    return validate_cct(struct.unpack_from("<H", frame.payload, 3)[0])
