"""Hybrid G60 transport: Bluetooth identity/readback with USB-serial setters.

The G60's USB CDC port applies absolute brightness/CCT setters sent with the same
``00 01`` header the BLE app uses, but it never replies to brightness/CCT reads.
This transport therefore keeps every read on Bluetooth (so ``LightClient`` still
verifies each write by readback) and sends only allowlisted setters over USB.
See docs/usb.md and docs/research/usb-deep.md for the evidence and limits.
"""

import asyncio
import os
import struct
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor

from serial import Serial

from .protocol import (
    Command,
    Frame,
    FrameParser,
    ProtocolError,
    validate_brightness,
    validate_cct,
)
from .transport import BleTransport, RequestTimeout, Transport, TransportError, _CleanupError

_OPEN_TIMEOUT = 5.0
_CLEANUP_TIMEOUT = 5.0
_SETTER_SIZES = {Command.BRIGHTNESS: 7, Command.CCT: 5}
# Port open/close can block in the kernel (USB control transfers). They run here so
# the owning future survives caller cancellation; reads and writes never use threads.
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lighttime-usb")


def _observe(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


async def _settled(work: Future, deadline: float) -> bool:
    """Wait for owned thread work without ever cancelling it."""
    if not work.done():
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        wrapped = asyncio.wrap_future(work)
        # wait() never cancels the worker on caller cancellation or timeout.
        # Observe the wrapper too: the concurrent future remains the owner, but
        # a later worker exception must not become an unobserved asyncio error.
        wrapped.add_done_callback(lambda result: None if result.cancelled() else result.exception())
        await asyncio.wait((wrapped,), timeout=remaining)
    return work.done()


def validate_usb_setter(command: int, payload: bytes, device_id: int) -> None:
    """Reject anything except an absolute brightness/CCT setter for this light."""
    if _SETTER_SIZES.get(command) != len(payload):
        raise ProtocolError(
            f"USB writes allow only absolute brightness/CCT setters, not 0x{command:04x}"
        )
    target, operation = struct.unpack_from("<HB", payload)
    if target != device_id or operation != 1:
        raise ProtocolError("USB setter must be an absolute write for the verified device")
    if command == Command.BRIGHTNESS:
        validate_brightness(struct.unpack_from("<f", payload, 3)[0])
    else:
        validate_cct(struct.unpack_from("<H", payload, 3)[0])


async def _read_identity(request: Callable[[int], Awaitable[Frame]]) -> tuple[str, str, int]:
    info = await request(Command.INFO)
    fields = [part.decode("ascii", errors="replace") for part in info.payload.split(b"\0") if part]
    if len(fields) < 2 or fields[1].lower() != "pl103":
        raise ProtocolError("The connected device is not a MOLUS G60 (PL103)")
    device = await request(Command.DEVICE_ID)
    if len(device.payload) != 2:
        raise ProtocolError("Invalid device ID response")
    return fields[0], fields[1].upper(), struct.unpack("<H", device.payload)[0]


class _UsbSerial:
    """Own one CDC-ACM port; non-blocking I/O on the event loop thread only."""

    def __init__(self, path: str, timeout: float):
        self.path = path
        self.timeout = timeout
        self._port: Serial | None = None
        self._fd: int | None = None
        self._opening: Future | None = None
        self._closing: Future | None = None
        self._cleanup_task: asyncio.Task[None] | None = None
        self._parser = FrameParser()
        self._pending: dict[tuple[int, int], asyncio.Future[Frame]] = {}
        self._sequence = 0
        self._error: str | None = None

    @property
    def owned(self) -> bool:
        return self._port is not None or self._opening is not None or self._closing is not None

    @property
    def ready(self) -> bool:
        return self._fd is not None and self._error is None

    async def open(self) -> None:
        if self.owned:
            await self.close()  # Never replace a port this process still owns.
        loop = asyncio.get_running_loop()
        port = Serial(port=None, baudrate=115200, timeout=0, write_timeout=0, exclusive=True)
        port.dtr = False
        port.rts = False
        port.port = self.path
        self._port, self._error, self._parser = port, None, FrameParser()
        self._opening = _EXECUTOR.submit(port.open)
        if not await _settled(self._opening, loop.time() + _OPEN_TIMEOUT):
            raise TransportError(f"USB serial open on {self.path} timed out; cleanup required")
        opening, self._opening = self._opening, None
        error = TransportError("open cancelled") if opening.cancelled() else opening.exception()
        if error is not None:
            self._port = None  # pyserial releases its descriptor when open fails.
            raise TransportError(f"Cannot open USB serial port {self.path}: {error}") from error
        try:
            self._fd = port.fileno()
            loop.add_reader(self._fd, self._readable)
        except (AttributeError, NotImplementedError, OSError, ValueError) as exc:
            self._fd = None
            raise TransportError("USB writes need a POSIX serial port and event loop") from exc

    def _readable(self) -> None:
        if (fd := self._fd) is None:
            return
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            return
        except OSError as exc:
            self._lost(f"USB serial port lost: {exc}")
            return
        if not data:
            self._lost("USB serial port closed by the device")
            return
        for frame in self._parser.feed(data):
            future = self._pending.get((frame.sequence, frame.command))
            if future and not future.done() and frame.message_type == 1:
                future.set_result(frame)

    def _lost(self, reason: str) -> None:
        self._error = reason
        if self._fd is not None:
            asyncio.get_running_loop().remove_reader(self._fd)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(TransportError(reason))

    def _ready_fd(self) -> int:
        if self._fd is None:
            raise TransportError(f"USB serial port {self.path} is not open")
        if self._error is not None:
            raise TransportError(self._error)
        return self._fd

    @staticmethod
    async def _writable(fd: int, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        ready = loop.create_future()
        loop.add_writer(fd, lambda: ready.done() or ready.set_result(None))
        try:
            await asyncio.wait_for(ready, timeout)
            return True
        except TimeoutError:
            return False
        finally:
            loop.remove_writer(fd)

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) & 0xFFFF
        return self._sequence

    async def write(self, command: int, payload: bytes = b"") -> None:
        """Write one frame with the BLE-identical ``00 01`` header within the timeout."""
        await self._write_frame(Frame(command, self._next_sequence(), payload).encode())

    async def _write_frame(self, frame: bytes) -> None:
        fd = self._ready_fd()
        data = memoryview(frame)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        while data:
            try:
                data = data[os.write(fd, data) :]
            except BlockingIOError:
                pass
            except OSError as exc:
                self._lost(f"USB serial write failed: {exc}")
                raise TransportError(f"USB serial write failed: {exc}") from exc
            try:
                writable = not data or await self._writable(fd, max(0.0, deadline - loop.time()))
            except asyncio.CancelledError:
                # A partial packet cannot be followed by another setter on this
                # session. Invalidate it and require owned cleanup/reconnection.
                self._lost("USB serial write cancelled; reconnect before reuse")
                raise
            if not writable:
                # A partial frame may have reached the device; reopen before reuse.
                self._lost("USB serial write timed out")
                raise TransportError("USB serial write timed out")
            fd = self._ready_fd()

    async def request(self, command: int) -> Frame:
        """USB identity reads only; brightness/CCT reads have no USB reply."""
        if command not in (Command.INFO, Command.DEVICE_ID):
            raise ProtocolError(f"USB requests are limited to identity, not 0x{command:04x}")
        self._ready_fd()
        future = asyncio.get_running_loop().create_future()
        sequence = self._next_sequence()
        key = (sequence, command)
        self._pending[key] = future
        try:
            await self._write_frame(Frame(command, sequence).encode())
            return await asyncio.wait_for(future, self.timeout)
        except TimeoutError as exc:
            raise RequestTimeout(f"No USB response to command 0x{command:04x}") from exc
        finally:
            self._pending.pop(key, None)
            if not future.done():
                future.cancel()

    async def close(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            loop = asyncio.get_running_loop()
            self._cleanup_task = asyncio.create_task(self._close(loop.time() + _CLEANUP_TIMEOUT))
            self._cleanup_task.add_done_callback(_observe)
        # Caller cancellation must not orphan an unfinished port close.
        await asyncio.shield(self._cleanup_task)

    async def _close(self, deadline: float) -> None:
        loop = asyncio.get_running_loop()
        if self._fd is not None:
            loop.remove_reader(self._fd)
            loop.remove_writer(self._fd)
            self._fd = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(TransportError("USB serial port closed"))
        self._pending.clear()
        if self._opening is not None:
            if not await _settled(self._opening, deadline):
                raise _CleanupError(
                    f"USB serial open on {self.path} still pending; cleanup required"
                )
            opening, self._opening = self._opening, None
            if opening.cancelled() or opening.exception() is not None:
                self._port = None
        if self._port is not None and self._closing is None:
            self._closing = _EXECUTOR.submit(self._port.close)
        if self._closing is not None:
            if not await _settled(self._closing, deadline):
                raise _CleanupError(
                    f"USB serial close on {self.path} still pending; cleanup required"
                )
            closing, self._closing = self._closing, None
            if closing.cancelled():  # Never ran (executor teardown): the port is still open.
                raise _CleanupError(f"USB serial close on {self.path} did not run; retry cleanup")
            if (error := closing.exception()) is not None:
                # Keep ownership: the descriptor may still be open. A later cleanup retries
                # the close, and no replacement port opens until it succeeds.
                raise _CleanupError(
                    f"USB serial close on {self.path} failed: {error}; cleanup required"
                ) from error
            self._port = None


class UsbBleTransport(Transport):
    """One G60 with Bluetooth identity/readback and USB-serial absolute setters.

    ``address`` stays the BLE address. ``connect`` matches the USB and BLE identity
    (serial, model, device ID) before any setter can be sent. ``request`` always
    uses Bluetooth; ``send`` accepts only absolute brightness/CCT setters and writes
    them to USB with the BLE-identical ``00 01`` header. Pair it with ``LightClient``,
    which verifies every write by Bluetooth readback.
    """

    kind = "usb+ble"

    def __init__(
        self, address: str, port: str, *, timeout: float = 3, ble: Transport | None = None
    ):
        super().__init__(address, timeout)
        self.port = port
        self._ble = ble or BleTransport(address, timeout)
        self._usb = _UsbSerial(port, timeout)
        self._identity: tuple[str, str, int] | None = None
        self._cleanup_task: asyncio.Task[None] | None = None

    @property
    def connected(self) -> bool:
        return self._identity is not None and self._ble.connected and self._usb.ready

    @connected.setter
    def connected(self, value: bool) -> None:
        # Base-class compatibility: state is derived from both links.
        if not value:
            self._identity = None

    async def connect(self):
        self._identity = None
        try:
            await self._usb.open()
            await self._ble.connect()
            usb = await _read_identity(self._usb.request)
            ble = await _read_identity(self._ble.request)
            if usb != ble:
                raise TransportError(
                    f"USB port {self.port} is not the BLE light {self.address}: "
                    f"USB {usb[1]} {usb[0]} id {usb[2]}, BLE {ble[1]} {ble[0]} id {ble[2]}"
                )
            self._identity = ble
        except BaseException as exc:
            try:
                await self.disconnect()
            except TransportError as cleanup_error:
                if not isinstance(exc, asyncio.CancelledError):
                    raise cleanup_error from exc
                exc.add_note(str(cleanup_error))
            raise

    async def request(self, command: int, payload: bytes = b"") -> Frame:
        if not self.connected:
            raise TransportError("USB+BLE transport is disconnected")
        return await self._ble.request(command, payload)

    async def send(self, command: int, payload: bytes = b""):
        """Write an absolute brightness/CCT setter over USB. No protocol reply follows."""
        async with self._lock:
            identity = self._identity
            if identity is None or not self.connected:
                raise TransportError("USB+BLE transport is disconnected")
            validate_usb_setter(command, payload, identity[2])
            await self._usb.write(command, payload)

    async def _write(self, data: bytes):
        raise TransportError("UsbBleTransport routes frames through request/send only")

    async def disconnect(self):
        self._identity = None
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup())
            self._cleanup_task.add_done_callback(_observe)
        await asyncio.shield(self._cleanup_task)

    async def _cleanup(self) -> None:
        results = await asyncio.gather(
            self._usb.close(), self._ble.disconnect(), return_exceptions=True
        )
        errors = [str(result) for result in results if isinstance(result, BaseException)]
        if errors:
            raise _CleanupError("USB+BLE cleanup failed: " + "; ".join(errors))
