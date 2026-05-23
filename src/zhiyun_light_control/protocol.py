"""Protocol primitives for Zhiyun MOLUS light control."""

from __future__ import annotations

import binascii
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

SOF = b"\x24\x3c"
RUNTIME_TYPE = 0x0100
UPDATER_DEVICE = 0x0103
DEFAULT_CONTROL_MODE = 0x33
LEGACY_CONTROL_MODE = 0x01


class RuntimeCommand(IntEnum):
    REGISTER_DEFAULT_GROUP = 0x0006
    BRIGHTNESS = 0x1001
    CCT = 0x1002
    RGB = 0x1003
    HUE = 0x1004
    SATURATION = 0x1005
    CMY_FILTER = 0x1006
    CHROMATICITY = 0x1007
    SLEEP = 0x1008
    MAX_MODE = 0x1009
    HSI = 0x100A
    BRIGHTNESS_WITH_MODE = 0x100B
    IDENTIFY = 0x1101
    VOLTAGE_BY_OBJECT = 0x1201
    FIRMWARE_BY_OBJECT = 0x1202
    DEVICE_MODE = 0x1203
    VOLTAGE = 0x2001
    MTU = 0x2002
    DEVICE_INFO = 0x2003
    EXTRA_INFO = 0x2004
    DEVICE_ID = 0x2005
    FIRMWARE = 0x8001


class UpdaterCommand(IntEnum):
    CHIP_SYNC = 0x1300
    WRITE_SN = 0x1301
    READ_SN = 0x1302
    PARAMETER_SAVE = 0x1303
    CHIP_WRITE = 0x7003
    CHIP_CHECK = 0x7004
    DFU_IS_OK = 0x8002
    DFU_START = 0x8003
    CHIP_RUN = 0x8004


@dataclass(frozen=True)
class ParsedFrame:
    offset: int
    length: int
    first_word: int
    seq: int
    cmd: int
    payload: bytes
    crc_expected: int
    crc_actual: int

    @property
    def crc_ok(self) -> bool:
        return self.crc_expected == self.crc_actual

    @property
    def body(self) -> bytes:
        return struct.pack("<HHH", self.first_word, self.seq, self.cmd) + self.payload

    def to_dict(self) -> dict[str, object]:
        return {
            "offset": self.offset,
            "length": self.length,
            "first_word": self.first_word,
            "seq": self.seq,
            "cmd": self.cmd,
            "payload_hex": self.payload.hex(),
            "payload_ascii": ascii_payload(self.payload),
            "crc_ok": self.crc_ok,
        }


@dataclass(frozen=True)
class DeviceInfo:
    identifier: str
    generation: str
    raw: bytes


@dataclass(frozen=True)
class ChipSyncInfo:
    core_id: str
    page_size: int
    page_count: int
    bootloader: int
    product: int
    hardware: int
    firmware_raw: int
    serial32: int

    @property
    def flash_size(self) -> int:
        return self.page_size * self.page_count

    @property
    def updater_firmware(self) -> str:
        return f"{self.firmware_raw // 100}.{self.firmware_raw % 100:02d}"


def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0)


def ascii_payload(data: bytes) -> str:
    return "".join(chr(byte) if 32 <= byte < 127 else "." for byte in data)


def build_frame(first_word: int, seq: int, cmd: int, payload: bytes = b"") -> bytes:
    body = (
        struct.pack("<HHH", first_word & 0xFFFF, seq & 0xFFFF, cmd & 0xFFFF) + payload
    )
    return SOF + struct.pack("<H", len(body)) + body + struct.pack("<H", crc16(body))


def build_runtime_frame(seq: int, cmd: int, payload: bytes = b"") -> bytes:
    return build_frame(RUNTIME_TYPE, seq, cmd, payload)


def build_updater_frame(
    seq: int,
    cmd: int,
    payload: bytes = b"",
    *,
    device: int = UPDATER_DEVICE,
) -> bytes:
    return build_frame(device, seq, cmd, payload)


def iter_frames(buf: bytes) -> Iterable[ParsedFrame]:
    index = 0
    while True:
        start = buf.find(SOF, index)
        if start < 0 or start + 4 > len(buf):
            return
        length = struct.unpack_from("<H", buf, start + 2)[0]
        end = start + 4 + length + 2
        if end > len(buf):
            return
        body = buf[start + 4 : start + 4 + length]
        rx_crc = struct.unpack_from("<H", buf, end - 2)[0]
        if len(body) >= 6:
            first, seq, cmd = struct.unpack_from("<HHH", body, 0)
            yield ParsedFrame(
                offset=start,
                length=length,
                first_word=first,
                seq=seq,
                cmd=cmd,
                payload=body[6:],
                crc_expected=crc16(body),
                crc_actual=rx_crc,
            )
        index = end


def first_frame(buf: bytes, *, cmd: int | None = None) -> ParsedFrame | None:
    for frame in iter_frames(buf):
        if cmd is None or frame.cmd == (cmd & 0xFFFF):
            return frame
    return None


def frame_bytes(buf: bytes, frame: ParsedFrame) -> bytes:
    """Return the exact serialized frame bytes for a parsed frame."""

    end = frame.offset + 4 + frame.length + 2
    return buf[frame.offset : end]


def is_echo_frame(buf: bytes, frame: ParsedFrame, tx: bytes) -> bool:
    """Return true when a parsed response frame is just the transmitted frame."""

    return bool(tx) and frame_bytes(buf, frame) == tx


def has_echo_frame(buf: bytes, tx: bytes) -> bool:
    return any(is_echo_frame(buf, frame, tx) for frame in iter_frames(buf))


def first_response_frame(
    buf: bytes,
    *,
    tx: bytes = b"",
    cmd: int | None = None,
) -> ParsedFrame | None:
    """Return the first matching frame that is not an exact write echo."""

    for frame in iter_frames(buf):
        if cmd is not None and frame.cmd != (cmd & 0xFFFF):
            continue
        if is_echo_frame(buf, frame, tx):
            continue
        return frame
    return None


def require_frame(buf: bytes, *, cmd: int | None = None) -> ParsedFrame:
    frame = first_frame(buf, cmd=cmd)
    if frame is None:
        label = f" for command 0x{cmd:04x}" if cmd is not None else ""
        raise ValueError(f"no complete Zhiyun frame{label}")
    if not frame.crc_ok:
        raise ValueError(f"CRC mismatch for command 0x{frame.cmd:04x}")
    return frame


def functional_payload(obj: int, op: int, payload: bytes) -> bytes:
    return struct.pack("<HB", obj & 0xFFFF, op & 0xFF) + payload


def register_payload(device_id: int, group_id: int = 0) -> bytes:
    return struct.pack("<HH", device_id & 0xFFFF, group_id & 0xFFFF)


def control_operation(read: bool, control_mode: int = DEFAULT_CONTROL_MODE) -> int:
    return 0 if read else control_mode & 0xFF


def brightness_payload(
    obj: int,
    value: float = 0.0,
    *,
    read: bool = False,
    control_mode: int = DEFAULT_CONTROL_MODE,
) -> bytes:
    return functional_payload(
        obj,
        control_operation(read, control_mode),
        struct.pack("<f", float(value)),
    )


def cct_payload(
    obj: int,
    kelvin: int = 0,
    *,
    read: bool = False,
    control_mode: int = DEFAULT_CONTROL_MODE,
) -> bytes:
    return functional_payload(
        obj,
        control_operation(read, control_mode),
        struct.pack("<H", kelvin & 0xFFFF),
    )


def rgb_payload(
    obj: int,
    red: int = 0,
    green: int = 0,
    blue: int = 0,
    *,
    read: bool = False,
    control_mode: int = DEFAULT_CONTROL_MODE,
) -> bytes:
    return functional_payload(
        obj,
        control_operation(read, control_mode),
        struct.pack("<HHH", red & 0xFFFF, green & 0xFFFF, blue & 0xFFFF),
    )


def hsi_payload(
    obj: int,
    hue: float = 0.0,
    saturation: float = 0.0,
    intensity: int = 0,
    *,
    read: bool = False,
    control_mode: int = DEFAULT_CONTROL_MODE,
) -> bytes:
    return functional_payload(
        obj,
        control_operation(read, control_mode),
        struct.pack("<ffH", float(hue), float(saturation), intensity & 0xFFFF),
    )


def brightness_with_mode_payload(
    obj: int,
    value: float = 0.0,
    mode: int = 0,
    *,
    read: bool = False,
    control_mode: int = DEFAULT_CONTROL_MODE,
) -> bytes:
    payload = b"" if read else struct.pack("<fb", float(value), mode & 0xFF)
    return functional_payload(obj, control_operation(read, control_mode), payload)


def sleep_payload(
    obj: int,
    value: int = 0,
    *,
    read: bool = False,
    control_mode: int = DEFAULT_CONTROL_MODE,
) -> bytes:
    return functional_payload(
        obj,
        control_operation(read, control_mode),
        struct.pack("<B", value & 0xFF),
    )


def object_id_payload(obj: int) -> bytes:
    return struct.pack("<H", obj & 0xFFFF)


def parse_c_string_payload(payload: bytes) -> list[str]:
    return [
        part.decode("ascii", errors="replace")
        for part in payload.rstrip(b"\x00").split(b"\x00")
        if part
    ]


def parse_device_info(frame: ParsedFrame) -> DeviceInfo:
    parts = parse_c_string_payload(frame.payload)
    identifier = parts[0] if parts else ""
    generation = parts[1] if len(parts) > 1 else ""
    return DeviceInfo(identifier=identifier, generation=generation, raw=frame.payload)


def parse_version(frame: ParsedFrame) -> str:
    return frame.payload.rstrip(b"\x00").decode("ascii", errors="replace")


def parse_voltage_status(frame: ParsedFrame) -> int | None:
    return frame.payload[0] if frame.payload else None


def parse_device_id(frame: ParsedFrame) -> int | None:
    if len(frame.payload) < 2:
        return None
    return struct.unpack_from("<H", frame.payload, 0)[0]


def parse_chip_sync(frame: ParsedFrame) -> ChipSyncInfo:
    payload = frame.payload
    if len(payload) < 21:
        raise ValueError(f"chipSync payload too short: {len(payload)} bytes")
    core_id = payload[1:5].rstrip(b"\x00").decode("ascii", errors="replace")
    page_size = struct.unpack_from("<H", payload, 5)[0]
    page_count = struct.unpack_from("<H", payload, 7)[0]
    bootloader = struct.unpack_from("<H", payload, 9)[0]
    product = struct.unpack_from("<H", payload, 11)[0]
    hardware = struct.unpack_from("<H", payload, 13)[0]
    firmware_raw = struct.unpack_from("<H", payload, 15)[0]
    serial32 = struct.unpack_from("<I", payload, 17)[0]
    return ChipSyncInfo(
        core_id=core_id,
        page_size=page_size,
        page_count=page_count,
        bootloader=bootloader,
        product=product,
        hardware=hardware,
        firmware_raw=firmware_raw,
        serial32=serial32,
    )
