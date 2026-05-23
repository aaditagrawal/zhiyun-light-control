"""Public data models for integration code."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .protocol import ParsedFrame, has_echo_frame, is_echo_frame


@dataclass(frozen=True)
class CommandResult:
    """Raw command exchange result.

    ``ack`` is the frame matching the command id, when the device sends one.
    For some currently experimental object-control commands the light may not
    reply even when the frame was transmitted. Some low-level probes can also
    produce an exact write echo; that is surfaced separately and is not counted
    as an acknowledgement.
    """

    command: int
    tx: bytes
    rx: bytes
    frames: tuple[ParsedFrame, ...]
    ack: ParsedFrame | None

    @property
    def acknowledged(self) -> bool:
        return (
            self.ack is not None
            and self.ack.crc_ok
            and not is_echo_frame(self.rx, self.ack, self.tx)
        )

    @property
    def sent(self) -> bool:
        return bool(self.tx)

    @property
    def timed_out(self) -> bool:
        return not self.rx

    @property
    def echoed(self) -> bool:
        return has_echo_frame(self.rx, self.tx)

    @property
    def transport_status(self) -> str:
        if self.acknowledged:
            return "acknowledged"
        if self.echoed:
            return "echoed_write"
        if self.timed_out:
            return "sent_no_response"
        return "response_without_matching_ack"

    def to_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "tx_hex": self.tx.hex(),
            "rx_hex": self.rx.hex() if self.rx else None,
            "sent": self.sent,
            "acknowledged": self.acknowledged,
            "echoed": self.echoed,
            "timed_out": self.timed_out,
            "transport_status": self.transport_status,
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

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
