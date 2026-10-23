"""Local HTTP/SSE bridge shared by websites and OBS, with optional authentication."""

import asyncio
import importlib
import json
import math
import os
import secrets
import struct
import sys
from contextlib import suppress
from pathlib import Path

from aiohttp import web

from . import __version__
from .client import LightClient, LightState, _validate_set
from .effects import EffectEngine
from .protocol import Command, Frame, ProtocolError, control_value
from .transport import (
    BleTransport,
    SerialTransport,
    Transport,
    TransportError,
    _deadline,
    _retry_discovery_cleanup,
    discover,
)

_OPERATION_DRAIN_TIMEOUT = 11.0  # Cancellation can traverse two five-second BLE cleanups.
_FINAL_CLEANUP_TIMEOUT = 6.0  # Transport cleanup has its own five-second deadline.
_POLL_INTERVAL = 2.0  # SSE subscribers otherwise receive a fresh two-query read this often.
_READBACK_FRESHNESS = 2.0  # A newer verified readback makes the ordinary poll redundant.
_AUTO_CONNECT_TIMEOUT = 55.0  # Includes discovery, identity and verified initial readback.
_DEVICE_PATHS = frozenset(
    {
        "/v1/devices",
        "/v1/connect",
        "/v1/disconnect",
        "/v1/state",
        "/v1/effects",
        "/v1/effects/start",
        "/v1/effects/stop",
    }
)


def default_token_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home()))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "lighttime" / "bridge.token"


def ensure_token(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.is_symlink():
            raise ValueError("Token file must not be a symbolic link") from None
        token = path.read_text().strip()
        if len(token) < 32:
            raise ValueError("Bridge token must contain at least 32 characters") from None
        if os.name != "nt":
            path.chmod(0o600)
        return token
    with os.fdopen(fd, "w") as stream:
        token = secrets.token_urlsafe(32)
        stream.write(token + "\n")
    return token


class MockTransport(Transport):
    """Explicit opt-in test backend. Never selected as a hardware fallback."""

    kind = "mock"

    def __init__(self, address="mock", **kwargs):
        super().__init__(address, **kwargs)
        self.brightness = 5.0
        self.cct = 5000

    async def connect(self):
        self.connected = True

    async def _write(self, data: bytes):
        frame = Frame.decode(data)
        if frame.command == Command.INFO:
            payload = b"MOCK-NOT-HARDWARE\0pl103\0"
        elif frame.command == Command.DEVICE_ID:
            payload = b"\0\0"
        elif frame.command in (Command.BRIGHTNESS, Command.CCT):
            key = "brightness" if frame.command == Command.BRIGHTNESS else "cct"
            if frame.payload[2] == 1:
                setattr(self, key, control_value(frame, 0))
                return
            value = getattr(self, key)
            payload = b"\0\0\0" + struct.pack("<f" if key == "brightness" else "<H", value)
        else:
            return
        self._receive(Frame(frame.command, frame.sequence, payload, 1).encode())


def api_error(status: int, code: str, message: str):
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


class Bridge:
    def __init__(
        self,
        token: str = "",
        *,
        auth_required: bool | None = None,
        auto_connect: bool | None = None,
        allow_origins=(),
        allow_mock=False,
        web_root: Path | None = None,
        usb_writes: str | None = None,
    ):
        if auth_required is None:
            auth_required = bool(token)
        if auth_required and not token:
            raise ValueError("An authenticated bridge requires a token")
        self.token = token
        self.auth_required = auth_required
        # None preserves programmatic startup with an explicitly configured address.
        # The CLI opts into one automatic attempt, including discovery if needed.
        self.auto_connect = auto_connect
        self.allow_origins = set(allow_origins)
        self.allow_mock = allow_mock
        # Optional serial port for hybrid output: BLE stays the identity/readback
        # path while setters use USB. None keeps the ordinary BLE transport.
        self.usb_writes = usb_writes
        self._readback: tuple[LightClient, str] | None = None
        self._readback_at = 0.0
        self.client: LightClient | None = None
        self.lock = asyncio.Lock()
        self.effects = EffectEngine(self)
        self.subscribers: set[asyncio.Queue] = set()
        self.poll_task: asyncio.Task | None = None
        self.connection_task: asyncio.Task | None = None
        self._auto_connection_task: asyncio.Task | None = None
        self._connection_generation = 0
        self._auto_connection_generation: int | None = None
        self._stopping = False
        self._device_tasks: set[asyncio.Task] = set()
        self._stop_waiter: asyncio.Future[None] | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._connecting_client: LightClient | None = None
        self.initial_address: str | None = None
        self.web_root = web_root
        self.app = web.Application(middlewares=[self.middleware], client_max_size=32768)
        self.app.add_routes(
            [
                web.get("/v1/health", self.health),
                web.get("/v1/devices", self.devices),
                web.post("/v1/connect", self.connect),
                web.post("/v1/disconnect", self.disconnect),
                web.get("/v1/state", self.get_state),
                web.patch("/v1/state", self.set_state),
                web.get("/v1/effects", self.get_effect),
                web.post("/v1/effects/start", self.start_effect),
                web.post("/v1/effects/stop", self.stop_effect),
                web.get("/v1/events", self.events),
                web.get("/lighttime.js", self.sdk_module),
                web.options("/{tail:.*}", self.options),
                web.get("/", self.index),
                web.get("/ui", self.index),
                web.get("/ui/", self.index),
            ]
        )
        if web_root and web_root.is_dir():
            self.app.router.add_static("/ui/", web_root, show_index=False)
        self.app.on_startup.append(self.startup)
        self.app.on_shutdown.append(self.begin_shutdown)
        # AppRunner drains HTTP handlers between on_shutdown and on_cleanup.
        self.app.on_cleanup.append(self.shutdown)

    def state(self):
        return self.client.state.to_dict() if self.client else LightState().to_dict()

    def _observe_readback(self):
        # Every verified operation publishes. A changed updatedAt is a new
        # two-field hardware readback; record when it was first observed.
        client = self.client
        stamp = client.state.updatedAt if client is not None else None
        if client is not None and stamp is not None and self._readback != (client, stamp):
            self._readback = (client, stamp)
            self._readback_at = asyncio.get_running_loop().time()

    def readback_fresh(self) -> bool:
        """Whether the current client's cached state is a readback under two seconds old."""
        client = self.client
        if client is None or self._readback != (client, client.state.updatedAt):
            return False
        return asyncio.get_running_loop().time() - self._readback_at < _READBACK_FRESHNESS

    def publish(self):
        if self._stopping:
            return
        self._observe_readback()
        state = self.state()
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(state)

    @web.middleware
    async def middleware(self, request, handler):
        origin = request.headers.get("Origin")
        own_origin = f"{request.scheme}://{request.host}"
        permitted = origin is None or origin == own_origin or origin in self.allow_origins
        if not permitted:
            return api_error(403, "origin_denied", "Origin is not allowed by this bridge")
        if (
            self.auth_required
            and request.method != "OPTIONS"
            and request.path.startswith("/v1/")
            and request.path != "/v1/health"
        ):
            auth = request.headers.get("Authorization", "")
            if not secrets.compare_digest(auth.encode(), f"Bearer {self.token}".encode()):
                response = api_error(401, "unauthorized", "A valid bridge bearer token is required")
                if origin:
                    self.cors(response, origin)
                return response
        try:
            if request.path in _DEVICE_PATHS and request.method != "OPTIONS":
                response = await self.device_request(request, handler)
            else:
                response = await handler(request)
        except ProtocolError as exc:
            self.publish()
            response = api_error(502, "verification_failed", str(exc))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            response = api_error(400, "invalid_request", str(exc))
        except (TransportError, OSError) as exc:
            self.publish()
            response = api_error(503, "device_unavailable", str(exc))
        except web.HTTPException:
            raise
        if (
            request.path.startswith("/ui/") or request.path == "/lighttime.js"
        ) and not response.prepared:
            # Revalidate the personal controller after deployments; API fetch
            # cache settings do not affect a browser's HTML/module cache.
            response.headers["Cache-Control"] = "no-store"
        if origin and permitted and not response.prepared:
            self.cors(response, origin)
        return response

    def require_running(self):
        if self._stopping:
            raise TransportError("The bridge is shutting down")

    def own_device_task(self, operation, name):
        task = asyncio.create_task(operation, name=name)
        self._device_tasks.add(task)

        def finished(task):
            self._device_tasks.discard(task)
            if not task.cancelled():
                task.exception()

        task.add_done_callback(finished)
        return task

    async def device_request(self, request, handler):
        if self._stopping:
            return api_error(503, "bridge_stopping", "The bridge is shutting down")
        if self._stop_waiter is None:
            self._stop_waiter = asyncio.get_running_loop().create_future()
        task = self.own_device_task(handler(request), f"Bridge {request.method} {request.path}")
        try:
            # Do not attach backend cancellation handlers to aiohttp's handler drain.
            # An uncooperative operation stays explicitly owned in _device_tasks.
            await asyncio.wait((task, self._stop_waiter), return_when=asyncio.FIRST_COMPLETED)
            if self._stopping:
                return api_error(503, "bridge_stopping", "The bridge is shutting down")
            return task.result()
        finally:
            if not task.done() and not task.cancelling():
                task.cancel()

    def cors(self, response, origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        response.headers["Access-Control-Allow-Private-Network"] = "true"

    async def body(self, request):
        def invalid_constant(value):
            raise ValueError(f"Non-finite JSON value: {value}")

        result = json.loads(await request.text() or "{}", parse_constant=invalid_constant)
        if not isinstance(result, dict):
            raise ValueError("Expected a JSON object")
        return result

    async def health(self, request):
        data = {"name": "lighttime", "version": __version__, "authRequired": self.auth_required}
        if not self.auth_required:
            data["defaultAddress"] = self.initial_address
        return web.json_response(data)

    async def options(self, request):
        return web.Response(status=204)

    async def devices(self, request):
        timeout = float(request.query.get("timeout", "5"))
        if not math.isfinite(timeout) or not 0.1 <= timeout <= 30:
            raise ValueError("Discovery timeout must be between 0.1 and 30 seconds")
        async with self.lock:
            self.require_running()
            try:
                devices = await discover(timeout)
            except Exception as exc:
                raise TransportError(f"Device discovery failed: {exc}") from exc
        return web.json_response({"devices": devices})

    async def _replace_device(self, address: str, kind: str):
        async with self.effects.control_lock:
            await self.effects.stop_locked(restore=False)
            async with self.lock:
                return await self.connect_device(address, kind)

    def _require_auto_connection(self, generation: int):
        self.require_running()
        if generation != self._connection_generation:
            raise TransportError("Automatic connection superseded by disconnect. Retry Connect.")

    async def _connect_automatically(self, generation: int | None = None):
        if generation is None:
            generation = self._connection_generation
        self._require_auto_connection(generation)
        task = self._auto_connection_task
        if task is None or task.done() or self._auto_connection_generation != generation:
            task = self.own_device_task(
                self._auto_connect(generation), "Bridge automatic connection"
            )
            self._auto_connection_task = task
            self._auto_connection_generation = generation
        # A dropped HTTP request or cancelled startup waiter must not abandon the
        # shared scanner/connection. Shutdown cancels and drains its explicit owner.
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Disconnect may cancel the owner before its coroutine starts. Report
            # that supersession through the API; ordinary caller cancellation
            # still propagates without cancelling the accepted shared work.
            if task.cancelled():
                self._require_auto_connection(generation)
            raise

    async def _auto_connect(self, generation: int):
        def guard():
            self._require_auto_connection(generation)

        try:
            async with asyncio.timeout(_AUTO_CONNECT_TIMEOUT):
                async with self.effects.control_lock:
                    guard()
                    async with self.lock:
                        guard()
                        if self.client is not None and self.client.state.connected:
                            await self.client.refresh(_guard=guard)
                            guard()
                            self.publish()
                            return self.state()
                        address = self.initial_address
                        if address is None:
                            try:
                                devices = await discover()
                            except Exception as exc:
                                raise TransportError(f"Device discovery failed: {exc}") from exc
                            guard()
                            candidates = [
                                device
                                for device in devices
                                if device.get("transport") == "ble"
                                and device.get("name", "").upper().startswith("PL103")
                            ]
                            if not candidates:
                                raise TransportError(
                                    "No MOLUS G60 (PL103) found over Bluetooth. Turn on the light, "
                                    "close other controllers, and retry Connect; or select a BLE "
                                    "address explicitly. USB is not used for automatic control."
                                )
                            if len(candidates) > 1:
                                addresses = ", ".join(device["address"] for device in candidates)
                                raise TransportError(
                                    f"Multiple MOLUS G60 (PL103) lights found ({addresses}). "
                                    "Select an explicit BLE address via /v1/connect or --address."
                                )
                            address = candidates[0]["address"]
                            self.initial_address = address
                    # Stopping an effect can wait for Bridge.lock. Release it
                    # first, while control_lock still serializes all replacements.
                    await self.effects.stop_locked(restore=False)
                    guard()
                    async with self.lock:
                        return await self.connect_device(address, "ble", _guard=guard)
        except TimeoutError as exc:
            raise TransportError(
                "Automatic Bluetooth connection timed out. Retry Connect or select an explicit BLE address."
            ) from exc

    async def connect_device(self, address: str, kind: str, *, _guard=None):
        # Existing callers may already hold Bridge.lock. Invalidate synchronously
        # here; HTTP/autoconnect drain the runner first in _replace_device.
        guard = _guard or self.require_running
        guard()
        if kind == "serial":
            raise ValueError(
                "USB identity works, but runtime light control is unverified. Use BLE; CLI usb-info supports USB identification."
            )
        if kind != "ble" and not (kind == "mock" and self.allow_mock):
            raise ValueError("Unsupported transport (mock requires --allow-mock)")
        self.effects.cancel()
        if self._connecting_client:
            await self._connecting_client.close()
            self._connecting_client = None
            guard()
        if self.client:
            await self.client.close()
            self.client = None
        # Cleanup above may have completed after shutdown began. Never allocate a
        # replacement backend after that await, even if cancellation was swallowed.
        guard()
        transport: Transport
        if kind == "mock":
            transport = MockTransport(address)
        elif self.usb_writes is not None:
            # The hybrid module is imported only when configured. Its transport
            # matches BLE and USB identity before reporting kind "usb+ble".
            hybrid = importlib.import_module(".usb_hybrid", __package__)
            transport = hybrid.UsbBleTransport(address, self.usb_writes)
        else:
            transport = BleTransport(address)
        client = LightClient(transport=transport)
        self._connecting_client = client
        try:
            await client.connect(_guard=guard)
        except Exception as exc:
            # Keep ownership of failed cleanup; the next connect/disconnect retries it.
            raise TransportError(f"Connection failed: {exc}") from exc
        guard()  # A late connect remains owned as the cleanup candidate.
        self.client = client
        self._connecting_client = None
        self.publish()
        return self.state()

    async def connect(self, request):
        generation = self._connection_generation
        data = await self.body(request)
        if set(data) - {"transport", "address", "deviceId"}:
            raise ValueError("Unknown connect property")
        address = data.get("address")
        kind = data.get("transport", "ble")
        if "address" not in data and kind == "ble":
            return web.json_response(await self._connect_automatically(generation))
        if not isinstance(address, str) or not 1 <= len(address) <= 512 or not address.strip():
            raise ValueError("A device address is required")
        # Always query the hardware ID; a guessed ID must never override it.
        state = await self._replace_device(address, kind)
        return web.json_response(state)

    async def disconnect(self, request):
        # Invalidate before waiting for either lock: an older owner can still be
        # queued behind this disconnect, or ignore cancellation during cleanup.
        self._connection_generation += 1
        task = self._auto_connection_task
        if task is not None and not task.done() and not task.cancelling():
            task.cancel()
        async with self.effects.control_lock:
            await self.effects.stop_locked(restore=False)
            async with self.lock:
                self.require_running()
                if self._connecting_client:
                    await self._connecting_client.close()
                    self._connecting_client = None
                if self.client:
                    await self.client.close()
                    self.client = None
                self.publish()
        return web.json_response(self.state())

    async def get_state(self, request):
        if self.client is None:
            return web.json_response(self.state())
        async with self.lock:
            self.require_running()
            if self.client:
                await self.client.refresh()
            self.publish()
            return web.json_response(self.state())

    async def set_state(self, request):
        data = await self.body(request)
        if (
            not data
            or set(data) - {"brightness", "cct", "power"}
            or any(v is None for v in data.values())
        ):
            raise ValueError(
                "Supply brightness, cct, or power; null and unknown properties are not accepted"
            )
        _validate_set(**data)
        async with self.effects.control_lock:
            await self.effects.stop_locked(restore=False)
            async with self.lock:
                self.require_running()
                if self.client is None:
                    raise TransportError("Connect to a light first")
                await self.client.set(**data, _guard=self.require_running)
                self.publish()
                return web.json_response(self.state())

    async def get_effect(self, request):
        self.require_running()
        return web.json_response(self.effects.status())

    async def start_effect(self, request):
        return web.json_response(await self.effects.start(await self.body(request)))

    async def stop_effect(self, request):
        data = await self.body(request)
        if set(data) - {"restore"} or not isinstance(data.get("restore", True), bool):
            raise ValueError("Supply only an optional boolean restore property")
        return web.json_response(await self.effects.stop(data.get("restore", True)))

    async def events(self, request):
        if self._stopping:
            return api_error(503, "bridge_stopping", "The bridge is shutting down")
        queue = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        queue.put_nowait(self.state())
        response = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}
        )
        origin = request.headers.get("Origin")
        if origin:
            self.cors(response, origin)
        try:
            await response.prepare(request)
            while not self._stopping:
                try:
                    state = await asyncio.wait_for(queue.get(), 10)
                    if state is None:
                        break
                    await response.write(f"event: state\ndata: {json.dumps(state)}\n\n".encode())
                except TimeoutError:
                    await response.write(b": keepalive\n\n")
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            self.subscribers.discard(queue)
        return response

    async def index(self, request):
        if self.web_root and (self.web_root / "index.html").is_file():
            raise web.HTTPFound("/ui/index.html")
        return web.Response(
            text="Lighttime bridge is running. Build examples/browser for the controller UI."
        )

    async def sdk_module(self, request):
        path = Path(__file__).with_name("static") / "sdk.js"
        try:
            source = path.read_bytes()
        except FileNotFoundError:
            return web.Response(status=404, text="Lighttime SDK module is not installed")
        return web.Response(body=source, content_type="application/javascript")

    async def startup(self, app):
        self.poll_task = asyncio.create_task(self.poll())
        if self.auto_connect is True or (self.auto_connect is None and self.initial_address):
            self.connection_task = asyncio.create_task(
                self.initial_connect(self._connection_generation)
            )

    async def initial_connect(self, generation: int | None = None):
        try:
            await self._connect_automatically(generation)
        except (TransportError, OSError, TimeoutError) as exc:
            print(
                f"Initial Bluetooth connection failed: {exc}. Connect from the controller to retry.",
                flush=True,
            )
            self.publish()

    async def poll(self):
        while not self._stopping:
            await asyncio.sleep(_POLL_INTERVAL)
            # Effect frames and manual writes already publish verified readback;
            # skipping a redundant poll avoids ~60 ms of lock contention.
            if self.subscribers and not self.readback_fresh():
                async with self.lock:
                    if self._stopping:
                        return
                    client = self.client
                    if client is None or self.readback_fresh():
                        continue
                    with suppress(TransportError, OSError, ValueError):
                        await client.refresh()
                    self.publish()

    async def begin_shutdown(self, app):
        self._stopping = True
        self.effects.cancel()
        if self._stop_waiter is not None and not self._stop_waiter.done():
            self._stop_waiter.set_result(None)
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(None)
        tasks = self._device_tasks | {
            task for task in (self.connection_task, self.poll_task) if task is not None
        }
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel()

    async def _close_owned_client(self, attribute):
        client = getattr(self, attribute)
        if client is not None:
            await client.close()
            setattr(self, attribute, None)

    async def _cleanup_devices(self):
        results = await asyncio.gather(
            self._close_owned_client("_connecting_client"),
            self._close_owned_client("client"),
            _retry_discovery_cleanup(_deadline(5)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def shutdown(self, app):
        await self.begin_shutdown(app)
        tasks = self._device_tasks | {
            task for task in (self.connection_task, self.poll_task) if task is not None
        }
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=_OPERATION_DRAIN_TIMEOUT)
            if pending:
                # Closing a client now could race a late connect/reconnect. Keep
                # both the operations and clients owned; a later shutdown can retry.
                raise TransportError("Bridge device operations still pending; cleanup unresolved")
        try:
            for task in (self.connection_task, self.poll_task):
                if task is not None and not task.cancelled():
                    task.result()
        finally:
            if self._cleanup_task is None or self._cleanup_task.done():
                self._cleanup_task = asyncio.create_task(
                    self._cleanup_devices(), name="Bridge device cleanup"
                )

                def observe(task):
                    if not task.cancelled():
                        task.exception()

                self._cleanup_task.add_done_callback(observe)
            done, _ = await asyncio.wait((self._cleanup_task,), timeout=_FINAL_CLEANUP_TIMEOUT)
            if not done:
                raise TransportError("Bridge device cleanup still pending; cleanup unresolved")
            self._cleanup_task.result()


async def usb_info(address: str):
    transport = SerialTransport(address)
    try:
        await transport.connect()
        info = await transport.request(Command.INFO)
        device_id = await transport.request(Command.DEVICE_ID)
        fields = [
            part.decode("ascii", errors="replace") for part in info.payload.split(b"\0") if part
        ]
        return {
            "transport": "serial",
            "address": address,
            "identity": fields,
            "deviceId": struct.unpack("<H", device_id.payload)[0],
            "runtimeControlVerified": False,
        }
    finally:
        await transport.disconnect()
