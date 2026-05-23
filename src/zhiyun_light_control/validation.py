"""Hardware validation reports for real light integrations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable

from .models import CommandResult
from .protocol import (
    RuntimeCommand,
    UpdaterCommand,
    brightness_payload,
    cct_payload,
    hsi_payload,
    object_id_payload,
    register_payload,
    rgb_payload,
    sleep_payload,
)


@dataclass(frozen=True)
class PrimitiveCheck:
    """Evidence for one hardware primitive."""

    name: str
    category: str
    confirmed: bool
    sent: bool
    status: str
    command: int | None = None
    result: dict[str, Any] | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HardwareValidationReport:
    """Structured report that does not conflate transmitted with confirmed."""

    transport: str
    probe: dict[str, Any] | None
    control_enabled: bool
    checks: tuple[PrimitiveCheck, ...]
    notes: tuple[str, ...] = ()

    @property
    def connection_confirmed(self) -> bool:
        if self.probe is None:
            return False
        return _probe_confirmed(self.probe)

    @property
    def all_attempted_confirmed(self) -> bool:
        attempted = [check for check in self.checks if check.sent]
        return bool(attempted) and all(check.confirmed for check in attempted)

    @property
    def unconfirmed(self) -> tuple[PrimitiveCheck, ...]:
        return tuple(check for check in self.checks if check.sent and not check.confirmed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "probe": self.probe,
            "control_enabled": self.control_enabled,
            "connection_confirmed": self.connection_confirmed,
            "all_attempted_confirmed": self.all_attempted_confirmed,
            "unconfirmed": [check.name for check in self.unconfirmed],
            "checks": [check.to_dict() for check in self.checks],
            "notes": list(self.notes),
        }


def validate_sync_light(
    light: Any,
    *,
    transport: str = "usb",
    allow_control: bool = False,
    include_object_reads: bool = False,
    include_color: bool = False,
    device_id: int = 0,
    obj: int = 1,
    brightness: float = 35.0,
    kelvin: int = 5600,
    sleep: int = 0,
    red: int = 255,
    green: int = 255,
    blue: int = 255,
    hue: float = 0.0,
    saturation: float = 0.0,
    intensity: int = 35,
) -> HardwareValidationReport:
    """Run a hardware validation sequence on an opened synchronous light."""

    probe = light.probe().to_dict()
    checks = [_probe_check(probe)]

    exchange_updater = getattr(light, "exchange_updater", None)
    if exchange_updater is not None:
        checks.append(
            _command_check(
                "updater_chip_sync",
                "identity",
                exchange_updater(UpdaterCommand.CHIP_SYNC),
                detail="read updater identity frame",
            )
        )

    if include_object_reads:
        checks.extend(
            _sync_object_read_checks(
                light.exchange_runtime,
                obj=obj,
            )
        )

    if allow_control:
        checks.extend(
            _sync_control_checks(
                light.exchange_runtime,
                device_id=device_id,
                obj=obj,
                brightness=brightness,
                kelvin=kelvin,
                sleep=sleep,
                include_color=include_color,
                red=red,
                green=green,
                blue=blue,
                hue=hue,
                saturation=saturation,
                intensity=intensity,
            )
        )

    return HardwareValidationReport(
        transport=transport,
        probe=probe,
        control_enabled=allow_control,
        checks=tuple(checks),
        notes=_notes(
            allow_control=allow_control,
            include_object_reads=include_object_reads,
            include_color=include_color,
        ),
    )


async def validate_async_light(
    light: Any,
    *,
    transport: str = "ble",
    allow_control: bool = False,
    include_object_reads: bool = False,
    include_color: bool = False,
    device_id: int = 0,
    obj: int = 1,
    brightness: float = 35.0,
    kelvin: int = 5600,
    sleep: int = 0,
    red: int = 255,
    green: int = 255,
    blue: int = 255,
    hue: float = 0.0,
    saturation: float = 0.0,
    intensity: int = 35,
) -> HardwareValidationReport:
    """Run a hardware validation sequence on an opened async light."""

    probe = (await light.probe()).to_dict()
    checks = [_probe_check(probe)]

    if include_object_reads:
        checks.extend(
            await _async_object_read_checks(
                light.exchange_runtime,
                obj=obj,
            )
        )

    if allow_control:
        checks.extend(
            await _async_control_checks(
                light.exchange_runtime,
                device_id=device_id,
                obj=obj,
                brightness=brightness,
                kelvin=kelvin,
                sleep=sleep,
                include_color=include_color,
                red=red,
                green=green,
                blue=blue,
                hue=hue,
                saturation=saturation,
                intensity=intensity,
            )
        )

    return HardwareValidationReport(
        transport=transport,
        probe=probe,
        control_enabled=allow_control,
        checks=tuple(checks),
        notes=_notes(
            allow_control=allow_control,
            include_object_reads=include_object_reads,
            include_color=include_color,
        ),
    )


def _sync_object_read_checks(
    exchange: Callable[..., CommandResult],
    *,
    obj: int,
) -> list[PrimitiveCheck]:
    return [
        _command_check(
            "read_brightness",
            "object_read",
            exchange(RuntimeCommand.BRIGHTNESS, brightness_payload(obj, read=True)),
        ),
        _command_check(
            "read_cct",
            "object_read",
            exchange(RuntimeCommand.CCT, cct_payload(obj, read=True)),
        ),
        _command_check(
            "read_sleep",
            "object_read",
            exchange(RuntimeCommand.SLEEP, sleep_payload(obj, read=True)),
        ),
        _command_check(
            "read_object_voltage",
            "object_read",
            exchange(RuntimeCommand.VOLTAGE_BY_OBJECT, object_id_payload(obj)),
        ),
        _command_check(
            "read_object_mode",
            "object_read",
            exchange(RuntimeCommand.DEVICE_MODE, object_id_payload(obj)),
        ),
    ]


async def _async_object_read_checks(
    exchange: Callable[..., Awaitable[CommandResult]],
    *,
    obj: int,
) -> list[PrimitiveCheck]:
    return [
        _command_check(
            "read_brightness",
            "object_read",
            await exchange(RuntimeCommand.BRIGHTNESS, brightness_payload(obj, read=True)),
        ),
        _command_check(
            "read_cct",
            "object_read",
            await exchange(RuntimeCommand.CCT, cct_payload(obj, read=True)),
        ),
        _command_check(
            "read_sleep",
            "object_read",
            await exchange(RuntimeCommand.SLEEP, sleep_payload(obj, read=True)),
        ),
        _command_check(
            "read_object_voltage",
            "object_read",
            await exchange(RuntimeCommand.VOLTAGE_BY_OBJECT, object_id_payload(obj)),
        ),
        _command_check(
            "read_object_mode",
            "object_read",
            await exchange(RuntimeCommand.DEVICE_MODE, object_id_payload(obj)),
        ),
    ]


def _sync_control_checks(
    exchange: Callable[..., CommandResult],
    *,
    device_id: int,
    obj: int,
    brightness: float,
    kelvin: int,
    sleep: int,
    include_color: bool,
    red: int,
    green: int,
    blue: int,
    hue: float,
    saturation: float,
    intensity: int,
) -> list[PrimitiveCheck]:
    checks = [
        _command_check(
            "register_default_group",
            "control",
            exchange(
                RuntimeCommand.REGISTER_DEFAULT_GROUP,
                register_payload(device_id),
            ),
        ),
        _command_check(
            "set_sleep",
            "control",
            exchange(RuntimeCommand.SLEEP, sleep_payload(obj, sleep, read=False)),
        ),
        _command_check(
            "set_brightness",
            "control",
            exchange(
                RuntimeCommand.BRIGHTNESS,
                brightness_payload(obj, brightness, read=False),
            ),
        ),
        _command_check(
            "set_cct",
            "control",
            exchange(RuntimeCommand.CCT, cct_payload(obj, kelvin, read=False)),
        ),
    ]
    if include_color:
        checks.extend(
            [
                _command_check(
                    "set_rgb",
                    "control",
                    exchange(RuntimeCommand.RGB, rgb_payload(obj, red, green, blue)),
                ),
                _command_check(
                    "set_hsi",
                    "control",
                    exchange(
                        RuntimeCommand.HSI,
                        hsi_payload(obj, hue, saturation, intensity),
                    ),
                ),
            ]
        )
    return checks


async def _async_control_checks(
    exchange: Callable[..., Awaitable[CommandResult]],
    *,
    device_id: int,
    obj: int,
    brightness: float,
    kelvin: int,
    sleep: int,
    include_color: bool,
    red: int,
    green: int,
    blue: int,
    hue: float,
    saturation: float,
    intensity: int,
) -> list[PrimitiveCheck]:
    checks = [
        _command_check(
            "register_default_group",
            "control",
            await exchange(
                RuntimeCommand.REGISTER_DEFAULT_GROUP,
                register_payload(device_id),
            ),
        ),
        _command_check(
            "set_sleep",
            "control",
            await exchange(RuntimeCommand.SLEEP, sleep_payload(obj, sleep, read=False)),
        ),
        _command_check(
            "set_brightness",
            "control",
            await exchange(
                RuntimeCommand.BRIGHTNESS,
                brightness_payload(obj, brightness, read=False),
            ),
        ),
        _command_check(
            "set_cct",
            "control",
            await exchange(RuntimeCommand.CCT, cct_payload(obj, kelvin, read=False)),
        ),
    ]
    if include_color:
        checks.extend(
            [
                _command_check(
                    "set_rgb",
                    "control",
                    await exchange(RuntimeCommand.RGB, rgb_payload(obj, red, green, blue)),
                ),
                _command_check(
                    "set_hsi",
                    "control",
                    await exchange(
                        RuntimeCommand.HSI,
                        hsi_payload(obj, hue, saturation, intensity),
                    ),
                ),
            ]
        )
    return checks


def _command_check(
    name: str,
    category: str,
    result: CommandResult,
    *,
    detail: str | None = None,
) -> PrimitiveCheck:
    return PrimitiveCheck(
        name=name,
        category=category,
        confirmed=result.acknowledged,
        sent=result.sent,
        status=result.transport_status,
        command=result.command,
        result=result.to_dict(),
        detail=detail,
    )


def _probe_check(probe: dict[str, Any]) -> PrimitiveCheck:
    confirmed = _probe_confirmed(probe)
    return PrimitiveCheck(
        name="probe",
        category="identity",
        confirmed=confirmed,
        sent=True,
        status="confirmed" if confirmed else "no_probe_data",
        detail="read global identity/status commands",
    )


def _probe_confirmed(probe: dict[str, Any]) -> bool:
    return any(
        probe.get(key) is not None
        for key in ("device_identifier", "generation", "firmware", "device_id")
    )


def _notes(
    *,
    allow_control: bool,
    include_object_reads: bool,
    include_color: bool,
) -> tuple[str, ...]:
    notes = [
        "confirmed means the device returned a matching ACK frame with valid CRC",
        "sent_no_response means the frame was written but the device did not ACK it",
        "echoed_write means the transport echoed the frame without a device ACK",
    ]
    if not allow_control:
        notes.append("control checks skipped; pass --allow-control to transmit writes")
    if not include_object_reads:
        notes.append("object read checks skipped; pass --include-object-reads to test them")
    if allow_control and not include_color:
        notes.append("RGB/HSI checks skipped; pass --include-color to test them")
    return tuple(notes)
