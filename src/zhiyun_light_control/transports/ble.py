"""Async BLE transport for Zhiyun light control.

This module imports bleak lazily so USB-only users do not need BLE
dependencies installed.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from ..protocol import iter_frames

DIRECT_ZY_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
DIRECT_ZY_WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
DIRECT_ZY_NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
LEGACY_ZY_SERVICE_UUID = "0000fee9-0000-1000-8000-00805f9b34fb"
LEGACY_ZY_WRITE_UUID = "d44bc439-abfd-45a2-b575-925416129600"
LEGACY_ZY_NOTIFY_UUID = "d44bc439-abfd-45a2-b575-925416129601"
MESH_PROVISIONING_SERVICE_UUID = "00001827-0000-1000-8000-00805f9b34fb"
MESH_PROXY_SERVICE_UUID = "00001828-0000-1000-8000-00805f9b34fb"
KNOWN_ZHIYUN_SERVICE_UUIDS = {
    DIRECT_ZY_SERVICE_UUID,
    LEGACY_ZY_SERVICE_UUID,
    MESH_PROVISIONING_SERVICE_UUID,
    MESH_PROXY_SERVICE_UUID,
}


@dataclass(frozen=True)
class BleDevice:
    address: str
    name: str | None
    rssi: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {"address": self.address, "name": self.name, "rssi": self.rssi}


@dataclass(frozen=True)
class BleScanResult:
    ok: bool
    devices: tuple[BleDevice, ...]
    error: str | None = None
    returncode: int | None = None
    worker_python: str | None = None
    signal_name: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "devices": [device.to_dict() for device in self.devices],
            "error": self.error,
            "returncode": self.returncode,
            "signal": self.signal_name,
            "worker_python": self.worker_python,
        }


@dataclass(frozen=True)
class BleExchangeResult:
    ok: bool
    tx: bytes
    rx: bytes = b""
    address: str | None = None
    error: str | None = None
    returncode: int | None = None
    worker_python: str | None = None
    signal_name: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "address": self.address,
            "tx_hex": self.tx.hex(),
            "rx_hex": self.rx.hex() if self.rx else None,
            "sent": bool(self.tx),
            "error": self.error,
            "returncode": self.returncode,
            "signal": self.signal_name,
            "worker_python": self.worker_python,
        }


class BleWorkerError(RuntimeError):
    def __init__(self, result: BleExchangeResult):
        super().__init__(result.error or "BLE worker failed")
        self.result = result


@dataclass(frozen=True)
class _BleWorkerRun:
    ok: bool
    stdout: str
    stderr: str
    returncode: int | None
    executable: str
    error: str | None = None


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
        self._client: object | None = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def __aenter__(self) -> BleTransport:
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

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        self._queue.put_nowait(bytes(data))

    def _drain_queue(self) -> None:
        while not self._queue.empty():
            self._queue.get_nowait()


class CrashIsolatedBleTransport:
    """BLE transport that runs each exchange in a worker process."""

    def __init__(
        self,
        *,
        address: str | None = None,
        name_contains: str | None = None,
        service_uuid: str = DIRECT_ZY_SERVICE_UUID,
        write_uuid: str = DIRECT_ZY_WRITE_UUID,
        notify_uuid: str = DIRECT_ZY_NOTIFY_UUID,
        timeout: float = 1.5,
        python: str | None = None,
    ):
        self.address = address
        self.name_contains = name_contains
        self.service_uuid = service_uuid
        self.write_uuid = write_uuid
        self.notify_uuid = notify_uuid
        self.timeout = timeout
        self.python = python

    async def __aenter__(self) -> CrashIsolatedBleTransport:
        await self.open()
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.close()

    async def open(self) -> None:
        return

    async def close(self) -> None:
        return

    async def exchange(self, tx: bytes, timeout: float | None = None) -> bytes:
        effective_timeout = self.timeout if timeout is None else timeout
        result = await asyncio.to_thread(
            exchange_zhiyun_ble_safe,
            tx,
            address=self.address,
            name_contains=self.name_contains,
            service_uuid=self.service_uuid,
            write_uuid=self.write_uuid,
            notify_uuid=self.notify_uuid,
            timeout=effective_timeout,
            python=self.python,
        )
        if result.address:
            self.address = result.address
        if not result.ok:
            raise BleWorkerError(result)
        return result.rx


async def scan_zhiyun_devices(timeout: float = 5.0) -> list[BleDevice]:
    _, BleakScanner = _load_bleak()
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    found: list[BleDevice] = []
    for device, adv in devices.values():
        service_uuids = {uuid.lower() for uuid in (adv.service_uuids or [])}
        name = device.name or adv.local_name
        name_hit = bool(
            name
            and any(part in name.lower() for part in ("zhiyun", "molus", "g60", "zy"))
        )
        service_hit = bool(KNOWN_ZHIYUN_SERVICE_UUIDS & service_uuids)
        if name_hit or service_hit:
            found.append(BleDevice(address=device.address, name=name, rssi=adv.rssi))
    return found


def scan_zhiyun_devices_safe(
    timeout: float = 5.0,
    *,
    python: str | None = None,
) -> BleScanResult:
    """Run BLE scanning in a worker process.

    CoreBluetooth/pyobjc failures can abort the interpreter instead of raising
    Python exceptions. Running the scan in a child process keeps CLI tools and
    long-lived media-control services alive when that happens.
    """

    run = _run_ble_worker(
        ["scan", "--timeout", str(timeout)],
        timeout=timeout,
        python=python,
    )
    if not run.ok:
        return BleScanResult(
            ok=False,
            devices=(),
            error=run.error,
            returncode=run.returncode,
            worker_python=run.executable,
            signal_name=_signal_name(run.returncode),
        )
    try:
        payload = json.loads(run.stdout or "{}")
    except json.JSONDecodeError as exc:
        return BleScanResult(
            ok=False,
            devices=(),
            error=f"could not parse BLE worker output: {exc}",
            returncode=run.returncode,
            worker_python=run.executable,
        )
    devices = tuple(
        BleDevice(
            address=str(item["address"]),
            name=item.get("name"),
            rssi=item.get("rssi"),
        )
        for item in payload.get("devices", [])
    )
    return BleScanResult(
        ok=True,
        devices=devices,
        returncode=run.returncode,
        worker_python=run.executable,
    )


def exchange_zhiyun_ble_safe(
    tx: bytes,
    *,
    address: str | None = None,
    name_contains: str | None = None,
    service_uuid: str = DIRECT_ZY_SERVICE_UUID,
    write_uuid: str = DIRECT_ZY_WRITE_UUID,
    notify_uuid: str = DIRECT_ZY_NOTIFY_UUID,
    timeout: float = 1.5,
    python: str | None = None,
) -> BleExchangeResult:
    args = [
        "exchange-raw",
        "--tx-hex",
        tx.hex(),
        "--timeout",
        str(timeout),
        "--service-uuid",
        service_uuid,
        "--write-uuid",
        write_uuid,
        "--notify-uuid",
        notify_uuid,
    ]
    if address:
        args.extend(["--address", address])
    if name_contains:
        args.extend(["--name-contains", name_contains])
    run = _run_ble_worker(args, timeout=timeout, python=python)
    if not run.ok:
        return BleExchangeResult(
            ok=False,
            tx=tx,
            error=run.error,
            returncode=run.returncode,
            worker_python=run.executable,
            signal_name=_signal_name(run.returncode),
        )
    try:
        payload = json.loads(run.stdout or "{}")
    except json.JSONDecodeError as exc:
        return BleExchangeResult(
            ok=False,
            tx=tx,
            error=f"could not parse BLE worker output: {exc}",
            returncode=run.returncode,
            worker_python=run.executable,
        )
    if not isinstance(payload, dict):
        return BleExchangeResult(
            ok=False,
            tx=tx,
            error="BLE worker output was not a JSON object",
            returncode=run.returncode,
            worker_python=run.executable,
        )
    error = _payload_string(payload, "error")
    rx_hex = _payload_string(payload, "rx_hex")
    if rx_hex is None:
        rx = b""
    else:
        try:
            rx = bytes.fromhex(rx_hex)
        except ValueError as exc:
            return BleExchangeResult(
                ok=False,
                tx=tx,
                error=f"could not parse BLE worker rx_hex: {exc}",
                returncode=run.returncode,
                worker_python=run.executable,
            )
    return BleExchangeResult(
        ok=error is None,
        tx=tx,
        rx=rx,
        address=_payload_string(payload, "address"),
        error=error,
        returncode=run.returncode,
        worker_python=run.executable,
    )


def _run_ble_worker(
    args: list[str],
    *,
    timeout: float,
    python: str | None,
) -> _BleWorkerRun:
    executable = python or sys.executable
    env = os.environ.copy()
    package_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = (
        package_root
        if not env.get("PYTHONPATH")
        else f"{package_root}{os.pathsep}{env['PYTHONPATH']}"
    )
    try:
        proc = subprocess.run(
            [
                executable,
                "-m",
                "zhiyun_light_control.ble_worker",
                *args,
            ],
            capture_output=True,
            text=True,
            timeout=max(timeout + 5.0, 10.0),
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return _BleWorkerRun(
            ok=False,
            stdout=_process_text(exc.stdout),
            stderr=_process_text(exc.stderr),
            returncode=None,
            executable=executable,
            error=f"BLE worker timed out after {max(timeout + 5.0, 10.0):g}s",
        )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    if proc.returncode != 0:
        detail = (
            _worker_error(stdout)
            or stderr
            or stdout
            or _format_worker_returncode(proc.returncode)
        )
        return _BleWorkerRun(
            ok=False,
            stdout=stdout,
            stderr=stderr,
            returncode=proc.returncode,
            executable=executable,
            error=detail,
        )
    return _BleWorkerRun(
        ok=True,
        stdout=stdout,
        stderr=stderr,
        returncode=proc.returncode,
        executable=executable,
    )


def _process_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace").strip()
    return value.strip()


def _worker_error(stdout: str) -> str | None:
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return None
    error = payload.get("error")
    return str(error) if error else None


def _payload_string(payload: dict[object, object], key: str) -> str | None:
    value = payload.get(key)
    return str(value) if value is not None else None


def _format_worker_returncode(returncode: int) -> str:
    name = _signal_name(returncode)
    if name:
        return f"worker terminated by signal {-returncode} ({name})"
    return f"worker exited {returncode}"


def _signal_name(returncode: int | None) -> str | None:
    if returncode is None:
        return None
    if returncode >= 0:
        return None
    try:
        return signal.Signals(-returncode).name
    except ValueError:
        return f"SIG{-returncode}"


def _load_bleak() -> tuple[object, object]:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise RuntimeError("BLE support requires installing the 'ble' extra") from exc
    return BleakClient, BleakScanner
