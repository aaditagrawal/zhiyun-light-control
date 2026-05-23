"""Async BLE transport for Zhiyun light control.

This module imports bleak lazily so USB-only users do not need BLE
dependencies installed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from ..protocol import iter_frames


DIRECT_ZY_SERVICE_UUID = "0000fee9-0000-1000-8000-00805f9b34fb"
DIRECT_ZY_WRITE_UUID = "d44bc439-abfd-45a2-b575-925416129600"
DIRECT_ZY_NOTIFY_UUID = "d44bc439-abfd-45a2-b575-925416129601"
MESH_PROVISIONING_SERVICE_UUID = "00001827-0000-1000-8000-00805f9b34fb"
MESH_PROXY_SERVICE_UUID = "00001828-0000-1000-8000-00805f9b34fb"


@dataclass(frozen=True)
class BleDevice:
    address: str
    name: str | None
    rssi: int | None = None


class BleTransport:
    """Async BLE transport backed by bleak."""

    def __init__(
        self,
        *,
        address: str | None = None,
        name_contains: str | None = None,
        service_uuid: str = DIRECT_ZY_SERVICE_UUID,
        write_uuid: str = DIRECT_ZY_WRITE_UUID,
        notify_uuid: str = DIRECT_ZY_NOTIFY_UUID,
        timeout: float = 1.5,
    ):
        self.address = address
        self.name_contains = name_contains
        self.service_uuid = service_uuid
        self.write_uuid = write_uuid
        self.notify_uuid = notify_uuid
        self.timeout = timeout
        self._client: Any | None = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def __aenter__(self) -> "BleTransport":
        await self.open()
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.close()

    async def open(self) -> None:
        if self._client is not None:
            return
        BleakClient, _ = _load_bleak()
        address = self.address
        if address is None:
            matches = await scan_zhiyun_devices(timeout=self.timeout)
            if self.name_contains:
                lowered = self.name_contains.lower()
                matches = [
                    dev for dev in matches if dev.name and lowered in dev.name.lower()
                ]
            if not matches:
                raise RuntimeError("no matching Zhiyun BLE device found")
            address = matches[0].address
        client = BleakClient(address)
        await client.connect()
        await client.start_notify(self.notify_uuid, self._on_notify)
        self.address = address
        self._client = client

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.stop_notify(self.notify_uuid)
        finally:
            await self._client.disconnect()
            self._client = None

    async def exchange(self, tx: bytes, timeout: float | None = None) -> bytes:
        if self._client is None:
            await self.open()
        if self._client is None:
            raise RuntimeError("BLE transport is not open")
        self._drain_queue()
        await self._client.write_gatt_char(self.write_uuid, tx, response=False)
        deadline = asyncio.get_running_loop().time() + (
            self.timeout if timeout is None else timeout
        )
        buf = bytearray()
        while asyncio.get_running_loop().time() < deadline:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            try:
                chunk = await asyncio.wait_for(self._queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            buf.extend(chunk)
            if any(True for _ in iter_frames(bytes(buf))):
                break
        return bytes(buf)

    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        self._queue.put_nowait(bytes(data))

    def _drain_queue(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()


async def scan_zhiyun_devices(timeout: float = 5.0) -> list[BleDevice]:
    _, BleakScanner = _load_bleak()
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    found: list[BleDevice] = []
    for device, adv in devices.values():
        service_uuids = {uuid.lower() for uuid in (adv.service_uuids or [])}
        name = device.name or adv.local_name
        name_hit = bool(name and any(part in name.lower() for part in ("zhiyun", "molus", "g60", "zy")))
        service_hit = bool(
            {
                DIRECT_ZY_SERVICE_UUID,
                MESH_PROVISIONING_SERVICE_UUID,
                MESH_PROXY_SERVICE_UUID,
            }
            & service_uuids
        )
        if name_hit or service_hit:
            found.append(BleDevice(address=device.address, name=name, rssi=adv.rssi))
    return found


def _load_bleak() -> tuple[Any, Any]:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise RuntimeError("BLE support requires installing the 'ble' extra") from exc
    return BleakClient, BleakScanner

