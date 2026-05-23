"""Public data models for integration code."""

from __future__ import annotations

from collections.abc import Iterable
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


class UnconfirmedCommandError(RuntimeError):
    def __init__(
        self,
        results: Iterable[CommandResult],
        *,
        action: str = "control",
    ):
        self.results = list(results)
        self.action = action
        self.statuses = [result.transport_status for result in self.results]
        self.unconfirmed = [
            result for result in self.results if not result.acknowledged
        ]
        details = ", ".join(self.statuses) or "no command results"
        super().__init__(f"{action} was not ACK-confirmed ({details})")


def command_results_acknowledged(results: Iterable[CommandResult]) -> bool:
    items = list(results)
    return bool(items) and all(result.acknowledged for result in items)


def flatten_command_batches(
    batches: Iterable[Iterable[CommandResult]],
) -> list[CommandResult]:
    return [result for batch in batches for result in batch]


def require_command_result(
    result: CommandResult,
    *,
    action: str = "control",
) -> CommandResult:
    if not result.acknowledged:
        raise UnconfirmedCommandError([result], action=action)
    return result


def require_command_results(
    results: Iterable[CommandResult],
    *,
    action: str = "control",
) -> list[CommandResult]:
    items = list(results)
    if not command_results_acknowledged(items):
        raise UnconfirmedCommandError(items, action=action)
    return items


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
