"""Read-only status reports backed by raw command evidence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

from .models import CommandResult
from .protocol import (
    RuntimeCommand,
    UpdaterCommand,
    parse_chip_sync,
    parse_device_id,
    parse_device_info,
    parse_read_sn,
    parse_version,
    parse_voltage_status,
)


@dataclass(frozen=True)
class LightStatusReport:
    """Current read-only identity/status data plus command evidence."""

    transport: str
    device_identifier: str | None
    generation: str | None
    firmware: str | None
    voltage_status: int | None
    device_id: int | None
    chip_sync: dict[str, object] | None
    read_sn: dict[str, object] | None
    commands: dict[str, CommandResult]

    @property
    def connection_confirmed(self) -> bool:
        return any(result.acknowledged for result in self.commands.values())

    def to_dict(self) -> dict[str, object]:
        return {
            "transport": self.transport,
            "connection_confirmed": self.connection_confirmed,
            "device_identifier": self.device_identifier,
            "generation": self.generation,
            "firmware": self.firmware,
            "voltage_status": self.voltage_status,
            "device_id": self.device_id,
            "chip_sync": self.chip_sync,
            "read_sn": self.read_sn,
            "commands": {
                name: result.to_dict() for name, result in self.commands.items()
            },
        }


def read_sync_status(
    light: object,
    *,
    transport: str = "usb",
    timeout: float = 0.8,
) -> LightStatusReport:
    """Read confirmed global status primitives from an opened sync light."""

    exchange_runtime = _required_exchange(light, "exchange_runtime")
    commands: dict[str, CommandResult] = {
        "device_info": exchange_runtime(RuntimeCommand.DEVICE_INFO, timeout=timeout),
        "firmware": exchange_runtime(RuntimeCommand.FIRMWARE, timeout=timeout),
        "voltage": exchange_runtime(RuntimeCommand.VOLTAGE, timeout=timeout),
        "device_id": exchange_runtime(RuntimeCommand.DEVICE_ID, timeout=timeout),
    }

    chip_sync: dict[str, object] | None = None
    read_sn: dict[str, object] | None = None
    exchange_updater = _optional_exchange(light, "exchange_updater")
    if exchange_updater is not None:
        commands["updater_chip_sync"] = exchange_updater(
            UpdaterCommand.CHIP_SYNC,
            timeout=timeout,
        )
        commands["updater_read_sn"] = exchange_updater(
            UpdaterCommand.READ_SN,
            timeout=timeout,
        )
        chip_sync = _parse_chip_sync(commands["updater_chip_sync"])
        read_sn = _parse_read_sn(commands["updater_read_sn"])

    device_info = _parse_device_info(commands["device_info"])
    return LightStatusReport(
        transport=transport,
        device_identifier=device_info[0],
        generation=device_info[1],
        firmware=_parse_version(commands["firmware"]),
        voltage_status=_parse_voltage(commands["voltage"]),
        device_id=_parse_device_id(commands["device_id"]),
        chip_sync=chip_sync,
        read_sn=read_sn,
        commands=commands,
    )


async def read_async_status(
    light: object,
    *,
    transport: str = "ble",
    timeout: float = 1.5,
) -> LightStatusReport:
    """Read confirmed global status primitives from an opened async light."""

    exchange_runtime = _required_async_exchange(light, "exchange_runtime")
    commands: dict[str, CommandResult] = {
        "device_info": await exchange_runtime(
            RuntimeCommand.DEVICE_INFO, timeout=timeout
        ),
        "firmware": await exchange_runtime(RuntimeCommand.FIRMWARE, timeout=timeout),
        "voltage": await exchange_runtime(RuntimeCommand.VOLTAGE, timeout=timeout),
        "device_id": await exchange_runtime(RuntimeCommand.DEVICE_ID, timeout=timeout),
    }

    chip_sync: dict[str, object] | None = None
    read_sn: dict[str, object] | None = None
    exchange_updater = _optional_async_exchange(light, "exchange_updater")
    if exchange_updater is not None:
        commands["updater_chip_sync"] = await exchange_updater(
            UpdaterCommand.CHIP_SYNC,
            timeout=timeout,
        )
        commands["updater_read_sn"] = await exchange_updater(
            UpdaterCommand.READ_SN,
            timeout=timeout,
        )
        chip_sync = _parse_chip_sync(commands["updater_chip_sync"])
        read_sn = _parse_read_sn(commands["updater_read_sn"])

    device_info = _parse_device_info(commands["device_info"])
    return LightStatusReport(
        transport=transport,
        device_identifier=device_info[0],
        generation=device_info[1],
        firmware=_parse_version(commands["firmware"]),
        voltage_status=_parse_voltage(commands["voltage"]),
        device_id=_parse_device_id(commands["device_id"]),
        chip_sync=chip_sync,
        read_sn=read_sn,
        commands=commands,
    )


def _required_exchange(light: object, name: str) -> Callable[..., CommandResult]:
    exchange = _optional_exchange(light, name)
    if exchange is None:
        raise TypeError(f"light does not expose {name}()")
    return exchange


def _optional_exchange(
    light: object,
    name: str,
) -> Callable[..., CommandResult] | None:
    exchange = getattr(light, name, None)
    if exchange is None:
        return None
    if not callable(exchange):
        raise TypeError(f"light attribute {name} is not callable")
    return cast(Callable[..., CommandResult], exchange)


def _required_async_exchange(
    light: object,
    name: str,
) -> Callable[..., Awaitable[CommandResult]]:
    exchange = _optional_async_exchange(light, name)
    if exchange is None:
        raise TypeError(f"light does not expose {name}()")
    return exchange


def _optional_async_exchange(
    light: object,
    name: str,
) -> Callable[..., Awaitable[CommandResult]] | None:
    exchange = getattr(light, name, None)
    if exchange is None:
        return None
    if not callable(exchange):
        raise TypeError(f"light attribute {name} is not callable")
    return cast(Callable[..., Awaitable[CommandResult]], exchange)


def _parse_device_info(result: CommandResult) -> tuple[str | None, str | None]:
    if not result.acknowledged or result.ack is None:
        return None, None
    info = parse_device_info(result.ack)
    return info.identifier or None, info.generation or None


def _parse_version(result: CommandResult) -> str | None:
    if not result.acknowledged or result.ack is None:
        return None
    version = parse_version(result.ack)
    return version or None


def _parse_voltage(result: CommandResult) -> int | None:
    if not result.acknowledged or result.ack is None:
        return None
    return parse_voltage_status(result.ack)


def _parse_device_id(result: CommandResult) -> int | None:
    if not result.acknowledged or result.ack is None:
        return None
    return parse_device_id(result.ack)


def _parse_chip_sync(result: CommandResult) -> dict[str, object] | None:
    if not result.acknowledged or result.ack is None:
        return None
    try:
        chip = parse_chip_sync(result.ack)
    except ValueError:
        return None
    return {
        "core_id": chip.core_id,
        "page_size": chip.page_size,
        "page_count": chip.page_count,
        "flash_size": chip.flash_size,
        "bootloader": chip.bootloader,
        "product": f"0x{chip.product:04x}",
        "hardware": f"0x{chip.hardware:04x}",
        "firmware_raw": chip.firmware_raw,
        "updater_firmware": chip.updater_firmware,
        "serial32": chip.serial32,
    }


def _parse_read_sn(result: CommandResult) -> dict[str, object] | None:
    if not result.acknowledged or result.ack is None:
        return None
    try:
        read_sn = parse_read_sn(result.ack)
    except ValueError:
        return None
    return {
        "prefix": read_sn.prefix,
        "product": f"0x{read_sn.product:04x}",
        "identifier_little_endian_hex": read_sn.identifier_little_endian_hex,
        "device_identifier": read_sn.device_identifier,
        "raw_hex": read_sn.raw.hex(),
    }
