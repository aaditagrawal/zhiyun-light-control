"""Async public API, currently used for BLE."""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass
from typing import Any

from .protocol import (
    RuntimeCommand,
    build_runtime_frame,
    brightness_payload,
    cct_payload,
    first_frame,
    hsi_payload,
    object_id_payload,
    parse_device_id,
    parse_device_info,
    parse_version,
    parse_voltage_status,
    register_payload,
    rgb_payload,
    sleep_payload,
)
from .transports.ble import BleTransport


@dataclass(frozen=True)
class AsyncProbeResult:
    device_identifier: str | None
    generation: str | None
    firmware: str | None
    voltage_status: int | None
    device_id: int | None
    address: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AsyncZhiyunLight:
    """Async light client for BLE transports."""

    def __init__(self, transport: Any):
        self.transport = transport
        self._seq = itertools.count(1)

    async def __aenter__(self) -> "AsyncZhiyunLight":
        if hasattr(self.transport, "open"):
            await self.transport.open()
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.close()

    @classmethod
    def ble(
        cls,
        *,
        address: str | None = None,
        name_contains: str | None = None,
        timeout: float = 1.5,
    ) -> "AsyncZhiyunLight":
        return cls(
            BleTransport(address=address, name_contains=name_contains, timeout=timeout)
        )

    async def close(self) -> None:
        if hasattr(self.transport, "close"):
            await self.transport.close()

    async def command(self, cmd: int, payload: bytes = b"", *, timeout: float = 1.5):
        tx = build_runtime_frame(next(self._seq), cmd, payload)
        rx = await self.transport.exchange(tx, timeout=timeout)
        return first_frame(rx, cmd=cmd)

    async def get_device_info(self):
        frame = await self.command(RuntimeCommand.DEVICE_INFO)
        return parse_device_info(frame) if frame else None

    async def get_firmware_version(self) -> str | None:
        frame = await self.command(RuntimeCommand.FIRMWARE)
        return parse_version(frame) if frame else None

    async def get_voltage_status(self) -> int | None:
        frame = await self.command(RuntimeCommand.VOLTAGE)
        return parse_voltage_status(frame) if frame else None

    async def get_device_id(self) -> int | None:
        frame = await self.command(RuntimeCommand.DEVICE_ID)
        return parse_device_id(frame) if frame else None

    async def probe(self) -> AsyncProbeResult:
        info = await self.get_device_info()
        firmware = await self.get_firmware_version()
        voltage = await self.get_voltage_status()
        device_id = await self.get_device_id()
        return AsyncProbeResult(
            device_identifier=info.identifier if info else None,
            generation=info.generation if info else None,
            firmware=firmware,
            voltage_status=voltage,
            device_id=device_id,
            address=getattr(self.transport, "address", None),
        )

    async def register(self, device_id: int = 0, group_id: int = 0):
        return await self.command(
            RuntimeCommand.REGISTER_DEFAULT_GROUP,
            register_payload(device_id, group_id),
        )

    async def set_brightness(self, obj: int, value: float):
        return await self.command(
            RuntimeCommand.BRIGHTNESS,
            brightness_payload(obj, value, read=False),
        )

    async def set_cct(self, obj: int, kelvin: int):
        return await self.command(RuntimeCommand.CCT, cct_payload(obj, kelvin, read=False))

    async def set_rgb(self, obj: int, red: int, green: int, blue: int):
        return await self.command(RuntimeCommand.RGB, rgb_payload(obj, red, green, blue))

    async def set_hsi(self, obj: int, hue: float, saturation: float, intensity: int):
        return await self.command(
            RuntimeCommand.HSI,
            hsi_payload(obj, hue, saturation, intensity),
        )

    async def set_sleep(self, obj: int, value: int):
        return await self.command(RuntimeCommand.SLEEP, sleep_payload(obj, value))

    async def get_object_firmware(self, obj: int = 0):
        return await self.command(RuntimeCommand.FIRMWARE_BY_OBJECT, object_id_payload(obj))

