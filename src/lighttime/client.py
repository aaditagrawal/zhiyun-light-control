"""Public Python SDK for one ZHIYUN MOLUS G60."""

import asyncio
import json
import struct
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from .protocol import (
    Command,
    ProtocolError,
    control_payload,
    control_value,
    validate_brightness,
    validate_cct,
)
from .transport import BleTransport, Transport, TransportError, _CleanupError


def _validate_set(*, brightness=None, cct=None, power=None):
    """Shared, side-effect-free validation for direct and HTTP setters."""
    if brightness is not None:
        try:
            brightness = validate_brightness(brightness)
        except OverflowError:
            raise ValueError("G60 brightness must be a whole percent from 0 to 100") from None
    if cct is not None:
        cct = validate_cct(cct)
    if power is not None and not isinstance(power, bool):
        raise ValueError("Power must be a boolean")
    if power is False and brightness is not None and brightness > 0:
        raise ValueError("Power off conflicts with positive brightness")
    if power is True and brightness == 0:
        raise ValueError("Power on conflicts with zero brightness")
    return brightness, cct, power


def _check_guard(guard: Callable[[], None] | None):
    if guard is not None:
        # Backend ACK/read calls can swallow cancellation. Their completion does
        # not authorize the next field/query or a reconnect in the same setter.
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        guard()


@dataclass(frozen=True)
class DeviceInfo:
    model: str
    serial: str
    id: int


@dataclass(frozen=True)
class LightState:
    connected: bool = False
    transport: str | None = None
    address: str | None = None
    device: DeviceInfo | None = None
    brightness: float | None = None
    cct: int | None = None
    power: bool | None = None
    updatedAt: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class LightClient:
    """One serialized connection; writes are read back before success is returned.

    Use ``async with LightClient(address) as light`` or explicit connect/close.
    Absolute set operations and reads retry once after reconnecting. No firmware
    commands, mesh provisioning, or Bluetooth reset operations are implemented.
    """

    def __init__(self, address: str = "", *, transport: Transport | None = None, retries: int = 1):
        self.transport = transport or BleTransport(address)
        self.retries = retries
        self._lock = asyncio.Lock()
        self._info: DeviceInfo | None = None
        self._state = LightState()
        self._last_brightness = 5.0

    @property
    def state(self) -> LightState:
        if not self.transport.connected or self._info is None:
            return LightState(address=self.transport.address, transport=self.transport.kind)
        return self._state

    def _invalidate(self) -> None:
        self._info = None
        self._state = LightState()

    async def _disconnect(self) -> None:
        self._invalidate()
        await self.transport.disconnect()

    async def _connect_identified(self, *, _guard=None) -> None:
        _check_guard(_guard)
        self._invalidate()
        try:
            await self.transport.connect()
            _check_guard(_guard)
            await self._identify(_guard=_guard)
        except _CleanupError:
            # The transport has already spent its cleanup budget. A later explicit
            # close/connect retries cleanup without extending this failed attempt.
            raise
        except BaseException as exc:
            try:
                await self._disconnect()
            except TransportError as cleanup_error:
                if not isinstance(exc, asyncio.CancelledError):
                    raise cleanup_error from exc
                exc.add_note(str(cleanup_error))
            raise

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def _identify(self, *, _guard=None):
        _check_guard(_guard)
        frame = await self.transport.request(Command.INFO)
        _check_guard(_guard)
        fields = [
            part.decode("ascii", errors="replace") for part in frame.payload.split(b"\0") if part
        ]
        if len(fields) < 2 or fields[1].lower() != "pl103":
            raise ProtocolError("The connected device is not a MOLUS G60 (PL103)")
        id_frame = await self.transport.request(Command.DEVICE_ID)
        _check_guard(_guard)
        if len(id_frame.payload) != 2:
            raise ProtocolError("Invalid device ID response")
        self._info = DeviceInfo(
            fields[1].upper(), fields[0], struct.unpack("<H", id_frame.payload)[0]
        )

    async def connect(self, *, _guard: Callable[[], None] | None = None) -> LightState:
        async with self._lock:
            await self._connect_identified(_guard=_guard)
            try:
                return await self._refresh(_guard=_guard)
            except _CleanupError:
                self._invalidate()
                raise
            except BaseException as exc:
                try:
                    await self._disconnect()
                except TransportError as cleanup_error:
                    if not isinstance(exc, asyncio.CancelledError):
                        raise cleanup_error from exc
                    exc.add_note(str(cleanup_error))
                raise

    async def close(self):
        async with self._lock:
            await self._disconnect()

    async def _retry_connection(self, attempt: int, *, _guard=None) -> None:
        await self._disconnect()
        _check_guard(_guard)
        if attempt < self.retries:
            await asyncio.sleep(0.3)
            _check_guard(_guard)
            await self._connect_identified(_guard=_guard)

    async def _request(self, command: Command, value: float | None = None, *, _guard=None):
        if self._info is None:
            raise TransportError("Connect to a light first")
        for attempt in range(self.retries + 1):
            _check_guard(_guard)
            try:
                frame = await self.transport.request(
                    command, control_payload(command, self._info.id, value)
                )
            except (TransportError, OSError):
                await self._retry_connection(attempt, _guard=_guard)
                if attempt >= self.retries:
                    raise
            else:
                _check_guard(_guard)
                return control_value(frame, self._info.id)
        raise TransportError("Request failed")

    async def _refresh(self, *, _guard=None) -> LightState:
        brightness = float(await self._request(Command.BRIGHTNESS, _guard=_guard))
        cct = int(await self._request(Command.CCT, _guard=_guard))
        _check_guard(_guard)
        if brightness > 0:
            self._last_brightness = brightness
        self._state = LightState(
            True,
            self.transport.kind,
            self.transport.address,
            self._info,
            brightness,
            cct,
            brightness > 0,
            datetime.now(UTC).isoformat(),
        )
        return self._state

    async def _send(self, command: Command, value: float, *, _guard=None):
        if self._info is None:
            raise TransportError("Connect to a light first")
        for attempt in range(self.retries + 1):
            _check_guard(_guard)
            try:
                await self.transport.send(command, control_payload(command, self._info.id, value))
            except (TransportError, OSError):
                await self._retry_connection(attempt, _guard=_guard)
                if attempt >= self.retries:
                    raise
            else:
                _check_guard(_guard)
                return

    async def refresh(self, *, _guard: Callable[[], None] | None = None) -> LightState:
        async with self._lock:
            return await self._refresh(_guard=_guard)

    async def set(
        self,
        *,
        brightness: float | None = None,
        cct: int | None = None,
        power: bool | None = None,
        _guard: Callable[[], None] | None = None,
    ) -> LightState:
        brightness, cct, power = _validate_set(brightness=brightness, cct=cct, power=power)
        async with self._lock:
            _check_guard(_guard)
            if power is False:
                if self._state.brightness:
                    self._last_brightness = self._state.brightness
                brightness = 0.0
            elif power is True and brightness is None:
                brightness = self._last_brightness
            if cct is not None:
                await self._send(Command.CCT, cct, _guard=_guard)
            if brightness is not None:
                await self._send(Command.BRIGHTNESS, brightness, _guard=_guard)
            state = await self._refresh(_guard=_guard)
            if brightness is not None and (
                state.brightness is None or abs(state.brightness - brightness) > 0.11
            ):
                raise ProtocolError(
                    f"Brightness readback differs: requested {brightness}, got {state.brightness}"
                )
            if cct is not None and state.cct != cct:
                raise ProtocolError(f"CCT readback differs: requested {cct}, got {state.cct}")
            return state


class BridgeClient:
    """Python HTTP SDK for applications sharing the bridge's BLE connection."""

    def __init__(self, base_url: str = "http://127.0.0.1:8877", *, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._session = None

    async def __aenter__(self):
        import aiohttp

        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self.token}"} if self.token else {},
            timeout=aiohttp.ClientTimeout(total=60),
        )
        return self

    async def __aexit__(self, *_):
        if self._session:
            await self._session.close()
            self._session = None

    async def request(self, method: str, path: str, data: dict | None = None):
        if self._session is None:
            raise RuntimeError("Use BridgeClient as an async context manager")
        async with self._session.request(method, self.base_url + path, json=data) as response:
            body = await response.json()
            if response.status >= 400:
                raise TransportError(
                    body.get("error", {}).get("message", f"HTTP {response.status}")
                )
            return body

    async def devices(self, timeout: float = 5):
        return (await self.request("GET", f"/v1/devices?timeout={timeout}"))["devices"]

    async def connect(self, address: str | None = None, transport: str = "ble"):
        data = {"transport": transport}
        if address is not None:
            data["address"] = address
        return await self.request("POST", "/v1/connect", data)

    async def disconnect(self):
        return await self.request("POST", "/v1/disconnect", {})

    async def refresh(self):
        return await self.request("GET", "/v1/state")

    async def set(self, **changes):
        return await self.request("PATCH", "/v1/state", changes)

    async def effect(self):
        return await self.request("GET", "/v1/effects")

    async def start_effect(self, pattern: str, **options):
        return await self.request("POST", "/v1/effects/start", {"pattern": pattern, **options})

    async def stop_effect(self, restore: bool = True):
        return await self.request("POST", "/v1/effects/stop", {"restore": restore})

    async def events(self):
        """Yield authenticated server state events until cancelled or disconnected.

        Use contextlib.aclosing when breaking out of iteration early. Stream
        failure reaches the caller, which can then mark displayed state stale.
        """
        import aiohttp

        if self._session is None:
            raise RuntimeError("Use BridgeClient as an async context manager")
        timeout = aiohttp.ClientTimeout(total=None, sock_read=30)
        async with self._session.get(self.base_url + "/v1/events", timeout=timeout) as response:
            if response.status != 200:
                raise TransportError(f"Event stream returned HTTP {response.status}")
            if not response.headers.get("Content-Type", "").startswith("text/event-stream"):
                raise TransportError("Bridge did not return an event stream")
            event = ""
            data = []
            size = 0
            while True:
                line = await response.content.readline()
                if not line:
                    raise TransportError("Bridge event stream closed")
                size += len(line)
                if size > 65536:
                    raise ProtocolError("Event exceeds maximum size")
                text = line.decode("utf-8").rstrip("\r\n")
                if not text:
                    if event == "state" and data:
                        yield json.loads("\n".join(data))
                    event, data, size = "", [], 0
                elif text.startswith("event:"):
                    event = text[6:].lstrip(" ")
                elif text.startswith("data:"):
                    data.append(text[5:].lstrip(" "))
