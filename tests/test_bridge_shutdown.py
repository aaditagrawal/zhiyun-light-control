"""Real ephemeral HTTP runners with in-memory BLE only; no adapter or live bridge."""

import asyncio
import struct
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from aiohttp import ClientSession, web

import lighttime.bridge as bridge_module
import lighttime.transport as transport_module
from lighttime import TransportError
from lighttime.bridge import Bridge
from lighttime.protocol import Command, Frame, control_value

HEADERS = {"Authorization": "Bearer offline-shutdown-test"}


async def bounded(awaitable, timeout=0.5):
    task = asyncio.ensure_future(awaitable)
    done, _ = await asyncio.wait((task,), timeout=timeout)
    assert done, "mock operation exceeded its shutdown budget"
    return task.result()


class OfflineBle:
    """Exercise real LightClient/BleTransport locks, replies and cleanup ownership."""

    def __init__(self):
        self.scanners = []
        self.clients = []
        self.writes = []
        self.phase = None
        self.ignore_cancel = False
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.scanned = asyncio.Event()
        self.disconnect_error = False
        self.device = SimpleNamespace(address="OFFLINE", name="PL103_OFFLINE")

    async def gate(self, phase):
        if self.phase != phase:
            return
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            if not self.ignore_cancel:
                raise
            await self.release.wait()

    def scanner(self, **kwargs):
        env = self

        class Scanner:
            def __init__(self):
                self.active = False
                self.discovered_devices = [env.device]
                self.discovered_devices_and_advertisement_data = {
                    env.device.address: (
                        env.device,
                        SimpleNamespace(local_name=env.device.name, rssi=-50),
                    )
                }
                env.scanners.append(self)

            async def start(self):
                self.active = True
                env.scanned.set()

            async def stop(self):
                await env.gate("stop")
                self.active = False

        return Scanner()

    def client(self, device, *, timeout, disconnected_callback):
        env = self

        class Client:
            def __init__(self):
                self.is_connected = False
                self.notify = None
                self.brightness = 5.0
                self.cct = 5000
                self.services = SimpleNamespace(get_characteristic=lambda _: object())
                env.clients.append(self)

            async def connect(self):
                assert any(scanner.active for scanner in env.scanners)
                await env.gate("connect")
                # Can become connected late if the mock backend ignores cancellation.
                self.is_connected = True

            async def disconnect(self):
                if env.disconnect_error:
                    raise TransportError("injected disconnect failure")
                self.is_connected = False
                disconnected_callback(self)

            async def start_notify(self, uuid, callback):
                self.notify = callback

            async def write_gatt_char(self, uuid, data, *, response):
                frame = Frame.decode(data)
                env.writes.append(frame)
                if frame.command == Command.INFO:
                    await env.gate("identify")
                    payload = b"OFFLINE-SERIAL\0pl103\0"
                elif frame.command == Command.DEVICE_ID:
                    payload = b"\0\0"
                else:
                    name = "brightness" if frame.command == Command.BRIGHTNESS else "cct"
                    if frame.payload[2]:
                        await env.gate("set")
                        setattr(self, name, control_value(frame, 0))
                        return
                    await env.gate("read")
                    payload = b"\0\0\0" + struct.pack(
                        "<f" if name == "brightness" else "<H", getattr(self, name)
                    )
                assert self.notify is not None
                self.notify(
                    None, bytearray(Frame(frame.command, frame.sequence, payload, 1).encode())
                )

        return Client()

    def assert_idle(self):
        assert all(not client.is_connected for client in self.clients)
        assert all(not scanner.active for scanner in self.scanners)
        assert not transport_module._discovery_scanners


@pytest.fixture
def offline_ble(monkeypatch):
    env = OfflineBle()
    monkeypatch.setattr(transport_module, "BleakClient", env.client)
    monkeypatch.setattr(transport_module, "BleakScanner", env.scanner)
    monkeypatch.setattr(transport_module, "comports", lambda: [])
    monkeypatch.setattr(transport_module, "_discovery_scanners", set())
    # Ordinary operations would take much longer than the asserted cleanup bound.
    monkeypatch.setattr(transport_module, "_CONNECT_TIMEOUT", 2)
    monkeypatch.setattr(transport_module, "_SCANNER_TIMEOUT", 1)
    monkeypatch.setattr(transport_module, "_BLE_CONNECT_TIMEOUT", 1)
    monkeypatch.setattr(transport_module, "_CLEANUP_TIMEOUT", 0.03)
    monkeypatch.setattr(bridge_module, "_OPERATION_DRAIN_TIMEOUT", 0.12)
    monkeypatch.setattr(bridge_module, "_FINAL_CLEANUP_TIMEOUT", 0.12)
    return env


@asynccontextmanager
async def server(env):
    bridge = Bridge("offline-shutdown-test")
    runner = web.AppRunner(bridge.app, shutdown_timeout=0.03)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    try:
        async with ClientSession(headers=HEADERS) as http:
            yield bridge, runner, http, url
    finally:
        env.release.set()
        env.disconnect_error = False
        await bounded(runner.cleanup())


async def connect(http, url):
    response = await bounded(http.post(url + "/v1/connect", json={"address": "OFFLINE"}))
    assert response.status == 200
    assert (await response.json())["connected"]


@pytest.mark.parametrize("operation", ["connect", "identify", "read", "set", "discover"])
def test_runner_cleanup_cancels_active_device_operations_before_client_close(
    offline_ble, operation
):
    async def scenario():
        env = offline_ble
        async with server(env) as (bridge, runner, http, url):
            if operation in {"read", "set"}:
                await connect(http, url)
            env.phase = operation
            if operation in {"connect", "identify"}:
                request = http.post(url + "/v1/connect", json={"address": "OFFLINE"})
            elif operation == "set":
                request = http.patch(url + "/v1/state", json={"brightness": 7})
            elif operation == "discover":
                request = http.get(url + "/v1/devices?timeout=30")
            else:
                request = http.get(url + "/v1/state")
            pending = asyncio.create_task(request)
            try:
                await bounded(env.scanned.wait() if operation == "discover" else env.entered.wait())
                assert bridge.lock.locked()
                owner = bridge.client or bridge._connecting_client
                if operation != "discover":
                    assert owner is not None and owner._lock.locked()
                started = asyncio.get_running_loop().time()
                await bounded(runner.cleanup())
                assert asyncio.get_running_loop().time() - started < 0.4
                response = await bounded(pending)
                assert response.status == 503
                assert (await response.json())["error"]["code"] == "bridge_stopping"
                assert bridge.client is None and bridge._connecting_client is None
                assert not bridge.lock.locked() and not bridge._device_tasks
                env.assert_idle()
                counts = len(env.clients), len(env.scanners), len(env.writes)
                env.release.set()
                await asyncio.sleep(0.04)
                assert counts == (len(env.clients), len(env.scanners), len(env.writes))
                env.assert_idle()
            finally:
                env.release.set()
                await bounded(pending)

    asyncio.run(scenario())


def test_stopping_rejects_new_and_queued_device_requests_without_allocating(offline_ble):
    async def scenario():
        env = offline_ble
        async with server(env) as (bridge, runner, http, url):
            await connect(http, url)
            requests = [
                ("POST", "/v1/connect", {"address": "REPLACEMENT"}),
                ("GET", "/v1/devices", None),
                ("POST", "/v1/disconnect", None),
                ("GET", "/v1/state", None),
                ("PATCH", "/v1/state", {"brightness": 7}),
            ]
            await bridge.lock.acquire()
            pending = [
                asyncio.create_task(http.request(method, url + path, json=body))
                for method, path, body in requests
            ]
            try:

                async def queued():
                    while len(bridge._device_tasks) != len(requests):
                        await asyncio.sleep(0)

                await bounded(queued())
                counts = len(env.clients), len(env.scanners), len(env.writes)
                await bridge.begin_shutdown(bridge.app)
                for method, path, body in requests + [("GET", "/v1/events", None)]:
                    response = await bounded(http.request(method, url + path, json=body))
                    assert response.status == 503
                    assert (await response.json())["error"]["code"] == "bridge_stopping"
                for task in pending:
                    assert (await bounded(task)).status == 503
                assert counts == (len(env.clients), len(env.scanners), len(env.writes))
            finally:
                bridge.lock.release()
            await bounded(runner.cleanup())
            env.assert_idle()

    asyncio.run(scenario())


def test_cancel_resistant_read_is_owned_and_reported_unresolved_until_retried(offline_ble):
    async def scenario():
        env = offline_ble
        async with server(env) as (bridge, runner, http, url):
            await connect(http, url)
            client = bridge.client
            env.phase, env.ignore_cancel = "read", True
            pending = asyncio.create_task(http.get(url + "/v1/state"))
            try:
                await bounded(env.entered.wait())
                cleanup = asyncio.create_task(runner.cleanup())
                # HTTP callers finish even when a backend cancellation handler hangs.
                assert (await bounded(pending, 0.1)).status == 503
                with pytest.raises(
                    TransportError, match="operations still pending; cleanup unresolved"
                ):
                    await bounded(cleanup)
                assert bridge.client is client and client is not None
                assert client._lock.locked() and bridge._device_tasks
                assert bridge._cleanup_task is None
                assert env.clients[0].is_connected
                owned = tuple(bridge._device_tasks)
            finally:
                env.release.set()
            await bounded(asyncio.gather(*owned, return_exceptions=True))
            await bounded(runner.cleanup())
            assert bridge.client is None and not bridge._device_tasks
            env.assert_idle()

    asyncio.run(scenario())


def test_late_backend_connect_retains_failed_candidate_until_cleanup_retry(offline_ble):
    async def scenario():
        env = offline_ble
        async with server(env) as (bridge, runner, http, url):
            env.phase, env.ignore_cancel = "connect", True
            pending = asyncio.create_task(
                http.post(url + "/v1/connect", json={"address": "OFFLINE"})
            )
            try:
                await bounded(env.entered.wait())
                with pytest.raises(TransportError, match="cleanup"):
                    await bounded(runner.cleanup())
                assert (await bounded(pending)).status == 503
                candidate = bridge._connecting_client
                assert candidate is not None and bridge.client is None
                assert candidate.transport._client is env.clients[0]
                assert candidate.transport._operations.tasks
                assert len(env.clients) == 1
            finally:
                env.release.set()
            await bounded(
                asyncio.gather(*candidate.transport._operations.tasks, return_exceptions=True)
            )
            assert env.clients[0].is_connected  # The late connection is still explicitly owned.
            await bounded(runner.cleanup())
            assert bridge._connecting_client is None and len(env.clients) == 1
            env.assert_idle()

    asyncio.run(scenario())


def test_disconnect_failure_keeps_client_after_http_drain_then_retry_cleans_it(offline_ble):
    async def scenario():
        env = offline_ble
        async with server(env) as (bridge, runner, http, url):
            await connect(http, url)
            client = bridge.client
            env.disconnect_error = True
            try:
                with pytest.raises(TransportError, match="injected disconnect failure"):
                    await bounded(runner.cleanup())
                assert bridge.client is client and client is not None
                assert client.transport._client is env.clients[0]
                assert env.clients[0].is_connected and not bridge._device_tasks
            finally:
                env.disconnect_error = False
            await bounded(runner.cleanup())
            assert bridge.client is None
            env.assert_idle()

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["body", "replacement_close"])
def test_late_handler_cannot_allocate_replacement_after_stopping(
    offline_ble, monkeypatch, boundary
):
    async def scenario():
        env = offline_ble
        async with server(env) as (bridge, runner, http, url):
            env.phase, env.ignore_cancel = "late", True
            if boundary == "body":
                original_body = bridge.body

                async def late_body(request):
                    data = await original_body(request)
                    await env.gate("late")
                    return data

                monkeypatch.setattr(bridge, "body", late_body)
            else:
                await connect(http, url)
                client = bridge.client
                assert client is not None
                original_close = client.close

                async def late_close():
                    await env.gate("late")
                    await original_close()

                monkeypatch.setattr(client, "close", late_close)
            pending = asyncio.create_task(
                http.post(url + "/v1/connect", json={"address": "REPLACEMENT"})
            )
            try:
                await bounded(env.entered.wait())
                counts = len(env.clients), len(env.scanners)
                with pytest.raises(TransportError, match="cleanup unresolved"):
                    await bounded(runner.cleanup())
                assert (await bounded(pending)).status == 503
                owned = tuple(bridge._device_tasks)
                assert owned
            finally:
                env.release.set()
            await bounded(asyncio.gather(*owned, return_exceptions=True))
            assert counts == (len(env.clients), len(env.scanners))
            await bounded(runner.cleanup())
            assert bridge.client is None and bridge._connecting_client is None
            env.assert_idle()

    asyncio.run(scenario())


def test_final_close_timeout_keeps_cleanup_task_and_client_until_completion(
    offline_ble, monkeypatch
):
    async def scenario():
        env = offline_ble
        async with server(env) as (bridge, runner, http, url):
            await connect(http, url)
            client = bridge.client
            assert client is not None
            original_close = client.close
            calls = 0
            env.phase = "close"

            async def slow_close():
                nonlocal calls
                calls += 1
                await env.gate("close")
                await original_close()

            monkeypatch.setattr(client, "close", slow_close)
            try:
                with pytest.raises(
                    TransportError, match="cleanup still pending; cleanup unresolved"
                ):
                    await bounded(runner.cleanup())
                cleanup = bridge._cleanup_task
                assert cleanup is not None and not cleanup.done() and not cleanup.cancelling()
                assert bridge.client is client and env.clients[0].is_connected
                assert calls == 1
            finally:
                env.release.set()
            await bounded(cleanup)
            await bounded(runner.cleanup())
            assert calls == 1 and bridge.client is None
            env.assert_idle()

    asyncio.run(scenario())


def test_discovery_stop_pending_is_retained_and_eventually_cleaned(offline_ble):
    async def scenario():
        env = offline_ble
        async with server(env) as (bridge, runner, http, url):
            env.phase = "stop"
            pending = asyncio.create_task(http.get(url + "/v1/devices?timeout=30"))
            try:
                await bounded(env.scanned.wait())
                with pytest.raises(TransportError, match="cleanup unresolved"):
                    await bounded(runner.cleanup())
                assert (await bounded(pending)).status == 503
                assert not bridge._device_tasks
                assert len(transport_module._discovery_scanners) == 1
                scanner = next(iter(transport_module._discovery_scanners))
                assert scanner.stop_task is not None and not scanner.stop_task.done()
                assert env.scanners[0].active
            finally:
                env.release.set()
            cleanup = bridge._cleanup_task
            assert cleanup is not None
            await bounded(cleanup)
            await bounded(runner.cleanup())
            assert len(env.scanners) == 1 and not env.clients
            env.assert_idle()

    asyncio.run(scenario())
