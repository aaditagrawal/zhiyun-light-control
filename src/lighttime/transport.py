"""Asynchronous BLE and USB serial transports with bounded requests."""

import asyncio
import re
import sys
from collections.abc import Callable, Coroutine
from contextlib import suppress
from functools import partial
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
from serial import Serial
from serial.tools.list_ports import comports

from .protocol import NOTIFY_UUID, WRITE_UUID, Frame, FrameParser


class TransportError(ConnectionError):
    pass


class RequestTimeout(TransportError):
    pass


class _CleanupError(TransportError):
    """Bounded cleanup already failed; retain ownership and retry on a later call."""


_CONNECT_TIMEOUT = 40.0
_CLEANUP_TIMEOUT = 5.0
_SCANNER_TIMEOUT = 5.0
_BLE_CONNECT_TIMEOUT = 15.0
_NOTIFY_TIMEOUT = 5.0
_RETRY_DELAY = 0.5


def _deadline(seconds: float) -> float:
    return asyncio.get_running_loop().time() + seconds


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - asyncio.get_running_loop().time())


def _observe(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


class _BleOperations:
    """Own backend calls even when a backend ignores cancellation.

    wait_for waits for cancellation handlers, so it cannot enforce these deadlines.
    A timed-out call stays owned here until it finishes; cleanup must drain it
    before invoking another operation on the same backend.
    """

    def __init__(self):
        self.tasks: set[asyncio.Task[None]] = set()

    async def run(
        self, label: str, operation: Callable[[], Coroutine[Any, Any, None]], deadline: float
    ) -> None:
        if not _remaining(deadline):
            raise TransportError(f"{label} deadline exceeded")
        task = asyncio.create_task(operation(), name=label)
        self.tasks.add(task)
        task.add_done_callback(_observe)
        try:
            done, _ = await asyncio.wait((task,), timeout=_remaining(deadline))
            if not done:
                task.cancel()
                raise TransportError(f"{label} deadline exceeded; cleanup required")
            task.result()
        except asyncio.CancelledError:
            if not task.done() and not task.cancelling():
                task.cancel()
            raise
        except TransportError:
            raise
        except Exception as exc:
            raise TransportError(f"{label} failed: {str(exc) or type(exc).__name__}") from exc
        finally:
            if task.done():
                self.tasks.discard(task)

    async def drain(self, deadline: float) -> None:
        for task in self.tasks:
            if not task.done() and not task.cancelling():
                task.cancel()
        if self.tasks:
            done, pending = await asyncio.wait(self.tasks, timeout=_remaining(deadline))
            self.tasks.difference_update(done)
            if pending:
                raise TransportError(
                    "Bluetooth backend cancellation still pending; cleanup required"
                )


class _ScannerSession:
    def __init__(self):
        # BlueZ suppresses unchanged advertisements with Bleak's default filter.
        # A stationary, previously seen G60 can then disappear from a fresh scan.
        self.scanner = (
            BleakScanner(bluez={"filters": {"DuplicateData": True}})
            if sys.platform == "linux"
            else BleakScanner()
        )
        self.start_task: asyncio.Task[None] | None = None
        self.stop_task: asyncio.Task[None] | None = None
        self.cleanup_task: asyncio.Task[None] | None = None
        self.closed = False

    async def start(self, deadline: float) -> None:
        if self.start_task is None:
            if not _remaining(deadline):
                raise TransportError("Bluetooth scanner start deadline exceeded")
            # BlueZ may accept StartDiscovery before its reply delivers the stop handle.
            # Keep the original startup alive so cleanup can receive that handle.
            self.start_task = asyncio.create_task(
                self.scanner.start(), name="Bluetooth scanner start"
            )
            self.start_task.add_done_callback(_observe)
        await self._wait_operation(self.start_task, "start", deadline)

    @staticmethod
    async def _wait_operation(task: asyncio.Task[None], phase: str, deadline: float) -> None:
        # Bound only this wait. Cancelling startup or stop can lose BlueZ's one-shot
        # stop handle, and a subsequent no-op stop cannot establish disposal.
        label = f"Bluetooth scanner {phase}"
        done, _ = await asyncio.wait((task,), timeout=_remaining(deadline))
        if not done:
            raise TransportError(f"{label} deadline exceeded; cleanup required")
        if task.cancelled():
            raise TransportError(f"{label} was cancelled; cleanup unresolved")
        try:
            task.result()
        except Exception as exc:
            # Retain the failed task and owner; the backend may already have acted.
            raise TransportError(
                f"{label} failed: {str(exc) or type(exc).__name__}; cleanup unresolved"
            ) from exc

    async def _stop(self, deadline: float) -> None:
        if self.start_task is None:
            # No startup was attempted (for example, its deadline was already spent).
            self.closed = True
            return
        await self._wait_operation(self.start_task, "start", deadline)
        if self.stop_task is None:
            if not _remaining(deadline):
                raise TransportError("Bluetooth scanner stop deadline exceeded; cleanup required")
            # BlueZ consumes its one-shot stop handle before awaiting the manager lock.
            # Keep that original call alive across caller deadlines/cancellation: another
            # stop() may be a no-op even though discovery and filters are still active.
            self.stop_task = asyncio.create_task(self.scanner.stop(), name="Bluetooth scanner stop")
            self.stop_task.add_done_callback(_observe)
        await self._wait_operation(self.stop_task, "stop", deadline)
        self.closed = True

    async def close(self, deadline: float) -> None:
        if self.closed:
            return
        if self.cleanup_task is None or self.cleanup_task.done():
            self.cleanup_task = asyncio.create_task(self._stop(deadline))
            self.cleanup_task.add_done_callback(_observe)
        # Caller cancellation must not orphan an unfinished scanner cleanup.
        await asyncio.shield(self.cleanup_task)


class Transport:
    kind = "base"

    def __init__(self, address: str, timeout: float = 3):
        self.address = address
        self.timeout = timeout
        self.connected = False
        self._sequence = 0
        self._lock = asyncio.Lock()
        self._parser = FrameParser()
        self._pending: dict[tuple[int, int], asyncio.Future[Frame]] = {}

    async def connect(self):
        raise NotImplementedError

    async def disconnect(self):
        self.connected = False
        for pending in self._pending.values():
            if not pending.done():
                pending.set_exception(TransportError("Disconnected"))
        self._pending.clear()
        self._parser = FrameParser()

    async def _write(self, data: bytes):
        raise NotImplementedError

    def _receive(self, data: bytes | bytearray):
        for frame in self._parser.feed(data):
            future = self._pending.get((frame.sequence, frame.command))
            if future and not future.done() and frame.message_type == 1:
                future.set_result(frame)

    async def request(self, command: int, payload: bytes = b"") -> Frame:
        async with self._lock:
            if not self.connected:
                raise TransportError("Transport is disconnected")
            self._sequence = (self._sequence + 1) & 0xFFFF
            key = (self._sequence, command)
            future = asyncio.get_running_loop().create_future()
            self._pending[key] = future
            try:
                await asyncio.wait_for(
                    self._write(Frame(command, self._sequence, payload).encode()), self.timeout
                )
                return await asyncio.wait_for(future, self.timeout)
            except TimeoutError as exc:
                raise RequestTimeout(
                    f"No response to command 0x{command:04x} on {self.kind}"
                ) from exc
            finally:
                self._pending.pop(key, None)
                if not future.done():
                    future.cancel()

    async def send(self, command: int, payload: bytes = b""):
        """Send an absolute setter. G60 sends no protocol reply for setters."""
        async with self._lock:
            if not self.connected:
                raise TransportError("Transport is disconnected")
            self._sequence = (self._sequence + 1) & 0xFFFF
            try:
                await asyncio.wait_for(
                    self._write(Frame(command, self._sequence, payload).encode()), self.timeout
                )
            except TimeoutError as exc:
                raise RequestTimeout("Write acknowledgement timed out") from exc


class BleTransport(Transport):
    kind = "ble"

    def __init__(self, address: str, timeout: float = 3):
        super().__init__(address, timeout)
        self._client: BleakClient | None = None
        self._scanner: _ScannerSession | None = None
        self._operations = _BleOperations()
        self._cleanup_task: asyncio.Task[None] | None = None
        self._generation = 0

    async def connect(self):
        deadline = _deadline(_CONNECT_TIMEOUT)
        # Never replace a backend while this process still owns its resources.
        if self._client is not None or self._scanner is not None:
            await self.disconnect()
        await _retry_discovery_cleanup(min(deadline, _deadline(_CLEANUP_TIMEOUT)))
        self._parser = FrameParser()
        for attempt in range(3):
            try:
                # Keep discovery active until BlueZ attaches the connection watcher.
                # Some G60 advertisements disappear immediately when scanning stops.
                self._scanner = _ScannerSession()
                scanner = self._scanner.scanner
                await self._scanner.start(min(deadline, _deadline(_SCANNER_TIMEOUT)))
                scan_deadline = min(deadline, _deadline(_SCANNER_TIMEOUT))
                device = None
                while _remaining(scan_deadline):
                    device = next(
                        (
                            d
                            for d in scanner.discovered_devices
                            if d.address.lower() == self.address.lower()
                        ),
                        None,
                    )
                    if device is not None:
                        break
                    await asyncio.sleep(min(0.25, _remaining(scan_deadline)))
                if device is None:
                    message = (
                        f"G60 at {self.address} is not advertising. "
                        "Close ZY Vega or another controller and retry."
                    )
                    if sys.platform == "linux" and re.fullmatch(
                        r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", self.address
                    ):
                        message += (
                            f" If `bluetoothctl info {self.address}` still reports Connected: yes "
                            "after the previous controller has exited, confirm no other app is "
                            f"using this G60, run `bluetoothctl disconnect {self.address}` to "
                            "release that stale link, then retry. "
                            "Lighttime does not automatically disconnect another controller's link."
                        )
                    raise TransportError(message)
                self._generation += 1
                generation = self._generation
                client = BleakClient(
                    device,
                    timeout=_BLE_CONNECT_TIMEOUT,
                    disconnected_callback=partial(self._lost, generation),
                )
                self._client = client
                await self._operations.run(
                    "Bluetooth connect",
                    client.connect,
                    min(deadline, _deadline(_BLE_CONNECT_TIMEOUT)),
                )
                await self._close_scanner(min(deadline, _deadline(_SCANNER_TIMEOUT)))
                if client.services.get_characteristic(WRITE_UUID) is None:
                    raise TransportError("Device does not expose the ZHIYUN control service")
                await self._operations.run(
                    "Bluetooth notifications",
                    partial(
                        client.start_notify,
                        NOTIFY_UUID,
                        partial(self._receive_generation, generation, client),
                    ),
                    min(deadline, _deadline(_NOTIFY_TIMEOUT)),
                )
                if not client.is_connected:
                    raise TransportError("Bluetooth link lost during connection")
                self.connected = True
                return
            except (BleakError, OSError, TransportError) as exc:
                await self.disconnect()  # A failed cleanup prevents any replacement/retry.
                if attempt == 2 or not _remaining(deadline):
                    raise TransportError(str(exc)) from exc
                await asyncio.sleep(min(_RETRY_DELAY, _remaining(deadline)))
            except BaseException as exc:
                try:
                    await self.disconnect()
                except TransportError as cleanup_error:
                    exc.add_note(str(cleanup_error))
                raise

    def _receive_generation(
        self, generation: int, client: BleakClient, _: object, data: bytearray
    ) -> None:
        if generation == self._generation and client is self._client and self.connected:
            self._receive(data)

    def _lost(self, generation: int, client: BleakClient):
        if generation != self._generation or client is not self._client:
            return
        self.connected = False
        for future in self._pending.values():
            if not future.done():
                future.set_exception(TransportError("Bluetooth link lost"))

    async def _write(self, data: bytes):
        if self._client is None or not self._client.is_connected:
            raise TransportError("Bluetooth link lost")
        try:
            await self._client.write_gatt_char(WRITE_UUID, data, response=True)
        except BleakError as exc:
            raise TransportError(str(exc)) from exc

    async def disconnect(self):
        await super().disconnect()
        self._generation += 1
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup(_deadline(_CLEANUP_TIMEOUT)))
            self._cleanup_task.add_done_callback(_observe)
        await asyncio.shield(self._cleanup_task)

    async def _close_scanner(self, deadline: float) -> None:
        if self._scanner is not None:
            await self._scanner.close(deadline)
            self._scanner = None

    async def _close_client(self, deadline: float) -> None:
        await self._operations.drain(deadline)
        if self._client is not None:
            await self._operations.run("Bluetooth disconnect", self._client.disconnect, deadline)
            if self._client.is_connected:
                raise TransportError("Bluetooth backend remains connected; cleanup required")
            self._client = None

    async def _cleanup(self, deadline: float) -> None:
        results = await asyncio.gather(
            self._close_client(deadline), self._close_scanner(deadline), return_exceptions=True
        )
        errors = [str(result) for result in results if isinstance(result, BaseException)]
        if errors:
            raise _CleanupError("Bluetooth cleanup failed: " + "; ".join(errors))


class SerialTransport(Transport):
    """USB supports identity queries; runtime control is not yet verified."""

    kind = "serial"

    def __init__(self, address: str, timeout: float = 3):
        super().__init__(address, timeout)
        self._port: Serial | None = None
        self._reader: asyncio.Task | None = None

    async def connect(self):
        self._parser = FrameParser()
        port = Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=self.timeout)
        port.dtr = False
        port.rts = False
        port.port = self.address
        try:
            await asyncio.to_thread(port.open)
        except BaseException:
            port.close()
            raise
        self._port = port
        self.connected = True
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        try:
            while self.connected and self._port:
                data = await asyncio.to_thread(self._port.read, 4096)
                self._receive(data)
        except Exception as exc:
            self.connected = False
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(TransportError(str(exc)))

    async def _write(self, data: bytes):
        if self._port is None:
            raise TransportError("Serial port closed")
        await asyncio.to_thread(self._port.write, data)

    async def disconnect(self):
        await super().disconnect()
        if self._reader:
            self._reader.cancel()
            with suppress(asyncio.CancelledError):
                await self._reader
            self._reader = None
        if self._port:
            await asyncio.to_thread(self._port.close)
            self._port = None


# Discovery has no transport instance to retain failed cleanup. Keep an explicit
# owner and retry it before opening another scanner, including after cancellation.
_discovery_scanners: set[_ScannerSession] = set()


async def _retry_discovery_cleanup(deadline: float) -> None:
    for previous in tuple(_discovery_scanners):
        await previous.close(deadline)
        _discovery_scanners.discard(previous)


async def discover(timeout: float = 5) -> list[dict]:
    import os

    devices = [
        {
            "transport": "serial",
            "address": p.device,
            "name": p.description,
            "accessible": os.access(p.device, os.R_OK | os.W_OK),
        }
        for p in comports()
        if p.vid == 0xFFF8 and p.pid == 0x0180
    ]
    deadline = _deadline(timeout + _SCANNER_TIMEOUT)
    await _retry_discovery_cleanup(min(deadline, _deadline(_CLEANUP_TIMEOUT)))
    session = _ScannerSession()
    try:
        try:
            await session.start(min(deadline, _deadline(_SCANNER_TIMEOUT)))
            await asyncio.sleep(min(timeout, _remaining(deadline)))
            seen = session.scanner.discovered_devices_and_advertisement_data
        except BaseException as exc:
            try:
                await session.close(_deadline(_CLEANUP_TIMEOUT))
            except TransportError as cleanup_error:
                exc.add_note(str(cleanup_error))
            raise
        else:
            await session.close(_deadline(_CLEANUP_TIMEOUT))
    finally:
        if not session.closed:
            _discovery_scanners.add(session)
    for device, advertisement in seen.values():
        name = advertisement.local_name or device.name or ""
        if name.upper().startswith("PL103"):
            devices.append(
                {
                    "transport": "ble",
                    "address": device.address,
                    "name": name,
                    "rssi": advertisement.rssi,
                }
            )
    return devices
