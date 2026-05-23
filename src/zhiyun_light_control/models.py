"""Public data models for integration code."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .protocol import ParsedFrame


@dataclass(frozen=True)
class CommandResult:
    """Raw command exchange result.

    ``ack`` is the frame matching the command id, when the device sends one.
    For some currently experimental object-control commands the light may not
    reply even when the frame was transmitted.
    """

    command: int
    tx: bytes
    rx: bytes
    frames: tuple[ParsedFrame, ...]
    ack: ParsedFrame | None

    @property
    def acknowledged(self) -> bool:
        return self.ack is not None and self.ack.crc_ok

    @property
    def timed_out(self) -> bool:
        return not self.rx

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "tx_hex": self.tx.hex(),
            "rx_hex": self.rx.hex() if self.rx else None,
            "acknowledged": self.acknowledged,
            "timed_out": self.timed_out,
            "ack": self.ack.to_dict() if self.ack else None,
            "frames": [frame.to_dict() for frame in self.frames],
        }


@dataclass(frozen=True)
class Scene:
    """A small lighting scene that can be applied over USB or BLE."""

    obj: int = 1
    brightness: float | None = None
    kelvin: int | None = None
    sleep: int | None = None
    red: int | None = None
    green: int | None = None
    blue: int | None = None
    hue: float | None = None
    saturation: float | None = None
    intensity: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

