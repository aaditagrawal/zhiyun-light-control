"""USB protocol discovery helpers for live Zhiyun light benches."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .models import CommandResult
from .protocol import (
    RUNTIME_TYPE,
    RuntimeCommand,
    brightness_payload,
    brightness_with_mode_payload,
    cct_payload,
    hsi_payload,
    object_id_payload,
    register_payload,
    rgb_payload,
    sleep_payload,
)

DEFAULT_DISCOVERY_OBJECT_IDS = (0, 1)
DEFAULT_DISCOVERY_FIRST_WORDS = (RUNTIME_TYPE, 0x0101, 0x0103, 0x0301)
DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS = (RUNTIME_TYPE,)


@dataclass(frozen=True)
class DiscoveryAttempt:
    """One attempted frame in a USB protocol discovery matrix."""

    name: str
    category: str
    command: int
    payload: bytes
    result: CommandResult
    object_id: int | None = None
    first_word: int | None = None

    @property
    def confirmed(self) -> bool:
        return self.result.acknowledged

    @property
    def status(self) -> str:
        return self.result.transport_status

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "category": self.category,
            "command": self.command,
            "command_hex": f"0x{self.command:04x}",
            "object_id": self.object_id,
            "first_word": self.first_word,
            "first_word_hex": f"0x{self.first_word:04x}"
            if self.first_word is not None
            else None,
            "payload_hex": self.payload.hex(),
            "confirmed": self.confirmed,
            "status": self.status,
            "result": self.result.to_dict(),
        }


@dataclass(frozen=True)
class UsbDiscoveryReport:
    """Evidence report for candidate USB primitive frames."""

    object_ids: tuple[int, ...]
    first_words: tuple[int, ...]
    control_object_ids: tuple[int, ...]
    control_first_words: tuple[int, ...]
    control_enabled: bool
    attempts: tuple[DiscoveryAttempt, ...]
    notes: tuple[str, ...] = ()

    @property
    def confirmed(self) -> tuple[DiscoveryAttempt, ...]:
        return tuple(attempt for attempt in self.attempts if attempt.confirmed)

    @property
    def responsive(self) -> tuple[DiscoveryAttempt, ...]:
        return tuple(
            attempt for attempt in self.attempts if not attempt.result.timed_out
        )

    @property
    def unconfirmed_responsive(self) -> tuple[DiscoveryAttempt, ...]:
        return tuple(attempt for attempt in self.responsive if not attempt.confirmed)

    def to_dict(self) -> dict[str, object]:
        return {
            "transport": "usb",
            "object_ids": list(self.object_ids),
            "first_words": list(self.first_words),
            "control_object_ids": list(self.control_object_ids),
            "control_first_words": list(self.control_first_words),
            "control_enabled": self.control_enabled,
            "summary": {
                "attempted": len(self.attempts),
                "responsive": len(self.responsive),
                "confirmed": len(self.confirmed),
                "unconfirmed_responsive": [
                    attempt.name for attempt in self.unconfirmed_responsive
                ],
            },
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "notes": list(self.notes),
        }


def discover_usb_primitives(
    light: object,
    *,
    object_ids: Iterable[int] = DEFAULT_DISCOVERY_OBJECT_IDS,
    first_words: Iterable[int] = DEFAULT_DISCOVERY_FIRST_WORDS,
    control_object_ids: Iterable[int] | None = None,
    control_first_words: Iterable[int] = DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS,
    timeout: float = 0.5,
    allow_control: bool = False,
    brightness: float = 35.0,
    kelvin: int = 5600,
    sleep: int = 0,
) -> UsbDiscoveryReport:
    """Run a bounded USB discovery matrix on an opened synchronous light."""

    object_ids_tuple = tuple(object_ids)
    first_words_tuple = tuple(first_words)
    control_object_ids_tuple = (
        object_ids_tuple if control_object_ids is None else tuple(control_object_ids)
    )
    control_first_words_tuple = tuple(control_first_words)
    attempts: list[DiscoveryAttempt] = []

    for name, cmd, payload in _global_read_candidates():
        attempts.append(
            _attempt_runtime(
                light,
                name=name,
                category="global_read",
                cmd=cmd,
                payload=payload,
                timeout=timeout,
            )
        )

    for obj in object_ids_tuple:
        for name, cmd, payload in _object_read_candidates(obj):
            attempts.append(
                _attempt_runtime(
                    light,
                    name=name,
                    category="object_read",
                    cmd=cmd,
                    payload=payload,
                    timeout=timeout,
                    object_id=obj,
                )
            )

    for first_word in first_words_tuple:
        payload = brightness_payload(0, read=True)
        attempts.append(
            _attempt_frame(
                light,
                name=f"first_word_0x{first_word:04x}_read_brightness_obj0",
                category="first_word_probe",
                first_word=first_word,
                cmd=RuntimeCommand.BRIGHTNESS,
                payload=payload,
                timeout=timeout,
                object_id=0,
            )
        )

    if allow_control:
        attempts.append(
            _attempt_runtime(
                light,
                name="register_default_group_dev0_group0",
                category="control",
                cmd=RuntimeCommand.REGISTER_DEFAULT_GROUP,
                payload=register_payload(0, 0),
                timeout=timeout,
            )
        )
        for obj in control_object_ids_tuple:
            for first_word in control_first_words_tuple:
                for name, cmd, payload in _control_candidates(
                    obj=obj,
                    brightness=brightness,
                    kelvin=kelvin,
                    sleep=sleep,
                ):
                    attempts.append(
                        _attempt_control(
                            light,
                            name=name,
                            first_word=first_word,
                            cmd=cmd,
                            payload=payload,
                            timeout=timeout,
                            object_id=obj,
                        )
                    )

    return UsbDiscoveryReport(
        object_ids=object_ids_tuple,
        first_words=first_words_tuple,
        control_object_ids=control_object_ids_tuple if allow_control else (),
        control_first_words=control_first_words_tuple if allow_control else (),
        control_enabled=allow_control,
        attempts=tuple(attempts),
        notes=_notes(allow_control=allow_control),
    )


def _attempt_runtime(
    light: object,
    *,
    name: str,
    category: str,
    cmd: int,
    payload: bytes,
    timeout: float,
    object_id: int | None = None,
) -> DiscoveryAttempt:
    result = light.exchange_runtime(cmd, payload, timeout=timeout)
    return DiscoveryAttempt(
        name=name,
        category=category,
        command=cmd & 0xFFFF,
        payload=payload,
        result=result,
        object_id=object_id,
        first_word=RUNTIME_TYPE,
    )


def _attempt_frame(
    light: object,
    *,
    name: str,
    category: str,
    first_word: int,
    cmd: int,
    payload: bytes,
    timeout: float,
    object_id: int | None = None,
) -> DiscoveryAttempt:
    result = light.exchange_frame(first_word, cmd, payload, timeout=timeout)
    return DiscoveryAttempt(
        name=name,
        category=category,
        command=cmd & 0xFFFF,
        payload=payload,
        result=result,
        object_id=object_id,
        first_word=first_word & 0xFFFF,
    )


def _attempt_control(
    light: object,
    *,
    name: str,
    first_word: int,
    cmd: int,
    payload: bytes,
    timeout: float,
    object_id: int,
) -> DiscoveryAttempt:
    if first_word == RUNTIME_TYPE:
        return _attempt_runtime(
            light,
            name=name,
            category="control",
            cmd=cmd,
            payload=payload,
            timeout=timeout,
            object_id=object_id,
        )
    return _attempt_frame(
        light,
        name=f"{name}_fw0x{first_word:04x}",
        category="control_first_word_probe",
        first_word=first_word,
        cmd=cmd,
        payload=payload,
        timeout=timeout,
        object_id=object_id,
    )


def _global_read_candidates() -> tuple[tuple[str, int, bytes], ...]:
    return (
        ("global_voltage", RuntimeCommand.VOLTAGE, b""),
        ("global_mtu", RuntimeCommand.MTU, b""),
        ("global_device_info", RuntimeCommand.DEVICE_INFO, b""),
        ("global_extra_info", RuntimeCommand.EXTRA_INFO, b""),
        ("global_device_id", RuntimeCommand.DEVICE_ID, b""),
        ("global_firmware", RuntimeCommand.FIRMWARE, b""),
    )


def _object_read_candidates(obj: int) -> tuple[tuple[str, int, bytes], ...]:
    return (
        (
            f"read_brightness_obj{obj}",
            RuntimeCommand.BRIGHTNESS,
            brightness_payload(obj, read=True),
        ),
        (f"read_cct_obj{obj}", RuntimeCommand.CCT, cct_payload(obj, read=True)),
        (
            f"read_sleep_obj{obj}",
            RuntimeCommand.SLEEP,
            sleep_payload(obj, read=True),
        ),
        (f"read_rgb_obj{obj}", RuntimeCommand.RGB, rgb_payload(obj, read=True)),
        (f"read_hsi_obj{obj}", RuntimeCommand.HSI, hsi_payload(obj, read=True)),
        (
            f"read_firmware_obj{obj}",
            RuntimeCommand.FIRMWARE_BY_OBJECT,
            object_id_payload(obj),
        ),
        (
            f"read_voltage_obj{obj}",
            RuntimeCommand.VOLTAGE_BY_OBJECT,
            object_id_payload(obj),
        ),
        (f"read_mode_obj{obj}", RuntimeCommand.DEVICE_MODE, object_id_payload(obj)),
        (f"identify_obj{obj}", RuntimeCommand.IDENTIFY, object_id_payload(obj)),
    )


def _control_candidates(
    *,
    obj: int,
    brightness: float,
    kelvin: int,
    sleep: int,
) -> tuple[tuple[str, int, bytes], ...]:
    return (
        (f"set_sleep_obj{obj}", RuntimeCommand.SLEEP, sleep_payload(obj, sleep)),
        (
            f"set_brightness_obj{obj}",
            RuntimeCommand.BRIGHTNESS,
            brightness_payload(obj, brightness, read=False),
        ),
        (f"set_cct_obj{obj}", RuntimeCommand.CCT, cct_payload(obj, kelvin)),
        (
            f"set_brightness_with_mode_obj{obj}_mode1",
            RuntimeCommand.BRIGHTNESS_WITH_MODE,
            brightness_with_mode_payload(obj, brightness, 1),
        ),
        (
            f"set_brightness_with_mode_obj{obj}_mode0",
            RuntimeCommand.BRIGHTNESS_WITH_MODE,
            brightness_with_mode_payload(obj, brightness, 0),
        ),
    )


def _notes(*, allow_control: bool) -> tuple[str, ...]:
    notes = [
        "confirmed means a non-echo matching ACK frame with valid CRC was received",
        "echoed_write means the transport echoed the exact transmitted frame",
        "timeouts are useful negative evidence for unsupported USB primitive shapes",
    ]
    if not allow_control:
        notes.append("control candidates skipped; pass --allow-control to include them")
    return tuple(notes)
