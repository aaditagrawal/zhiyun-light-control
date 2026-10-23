"""Offline regressions: no Bluetooth adapter, camera, or existing bridge is used."""

import asyncio
import struct
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestClient, TestServer
from bleak.exc import BleakError

import lighttime.transport as transport_module
from lighttime import LightClient, TransportError
from lighttime.bridge import Bridge, MockTransport
from lighttime.protocol import Command, Frame, ProtocolError
from lighttime.transport import BleTransport, discover


class Stall:
    """A backend call whose cancellation handler can also hang."""

    def __init__(self):
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()


class FakeBleak:
    def __init__(self):
        self.scanners = []
        self.clients = []
        self.commands = []
        self.start_stall = None
        self.stop_stall = None
        self.connect_stall = None
        self.notify_stall = None
        self.disconnect_stall = None
        self.stop_error = False
        self.connect_error = False
        self.connect_failures_remaining = 0
        self.disconnect_error = False
        self.disconnect_keeps_connected = False
        self.fail_control = False
        self.identity_failure = None
        self.identity_entered = asyncio.Event()
        self.control_read_entered = asyncio.Event()
        self.identity_queries = []
        self.advertising = True
        self.device = SimpleNamespace(address="AA:BB", name="PL103_FAKE")

    def scanner(self, **kwargs):
        env = self

        class Scanner:
            def __init__(self):
                self.active = False
                self.stop_calls = 0
                self.discovered_devices = [env.device] if env.advertising else []
                self.discovered_devices_and_advertisement_data = {
                    env.device.address: (
                        env.device,
                        SimpleNamespace(local_name="PL103_FAKE", rssi=-50),
                    )
                }
                env.scanners.append(self)

            async def start(self):
                self.active = True  # Startup may fail after allocating backend resources.
                if env.start_stall:
                    await env.start_stall()

            async def stop(self):
                self.stop_calls += 1
                if env.stop_error:
                    raise BleakError("injected scanner stop failure")
                if env.stop_stall:
                    await env.stop_stall()
                self.active = False

        return Scanner()

    def client(self, device, *, timeout, disconnected_callback):
        env = self

        class Client:
            def __init__(self):
                self.is_connected = False
                self.callback = disconnected_callback
                self.notify = None
                self.number = len(env.clients) + 1
                self.disconnect_calls = 0
                self.services = SimpleNamespace(get_characteristic=lambda _: object())
                env.clients.append(self)

            async def connect(self):
                # Regression assertion for G60 advertisements disappearing on scan stop.
                assert any(scanner.active for scanner in env.scanners)
                self.is_connected = True
                if env.connect_failures_remaining:
                    env.connect_failures_remaining -= 1
                    raise BleakError("injected transient connect failure")
                if env.connect_error:
                    raise BleakError("injected connect failure after backend allocation")
                if env.connect_stall:
                    await env.connect_stall()

            async def disconnect(self):
                self.disconnect_calls += 1
                if env.disconnect_error:
                    raise BleakError("injected disconnect failure")
                if env.disconnect_stall:
                    await env.disconnect_stall()
                if env.disconnect_keeps_connected:
                    return
                self.is_connected = False
                self.callback(self)

            async def start_notify(self, uuid, callback):
                self.notify = callback
                if env.notify_stall:
                    await env.notify_stall()

            async def write_gatt_char(self, uuid, data, *, response):
                frame = Frame.decode(data)
                env.commands.append((self.number, frame))
                if frame.command in (Command.INFO, Command.DEVICE_ID):
                    env.identity_queries.append((self.number, frame.command))
                    failure = env.identity_failure if self.number > 1 else None
                    prefix = "info" if frame.command == Command.INFO else "id"
                    if failure == prefix + "_timeout":
                        return
                    if failure == prefix + "_cancel":
                        env.identity_entered.set()
                        await asyncio.Event().wait()
                    if frame.command == Command.INFO:
                        payload = b"FAKE-SERIAL\0pl103\0"
                        if failure == "info_bad":
                            payload = b"WRONG\0other\0"
                    else:
                        payload = b"\0" if failure == "id_bad" else b"\0\0"
                else:
                    if env.fail_control:
                        env.fail_control = False
                        raise BleakError("injected link failure")
                    if frame.payload[2]:
                        return
                    env.control_read_entered.set()
                    value = 5 if frame.command == Command.BRIGHTNESS else 5000
                    payload = b"\0\0\0" + struct.pack(
                        "<f" if frame.command == Command.BRIGHTNESS else "<H", value
                    )
                assert self.notify is not None
                self.notify(
                    None, bytearray(Frame(frame.command, frame.sequence, payload, 1).encode())
                )

        return Client()


@pytest.fixture
def fake_bleak(monkeypatch):
    env = FakeBleak()
    monkeypatch.setattr(transport_module, "BleakScanner", env.scanner)
    monkeypatch.setattr(transport_module, "BleakClient", env.client)
    monkeypatch.setattr(transport_module, "comports", lambda: [])
    monkeypatch.setattr(transport_module, "_CONNECT_TIMEOUT", 0.12)
    monkeypatch.setattr(transport_module, "_CLEANUP_TIMEOUT", 0.03)
    monkeypatch.setattr(transport_module, "_SCANNER_TIMEOUT", 0.02)
    monkeypatch.setattr(transport_module, "_BLE_CONNECT_TIMEOUT", 0.02)
    monkeypatch.setattr(transport_module, "_NOTIFY_TIMEOUT", 0.02)
    return env


@pytest.fixture
def offline_bluez(fake_bleak, monkeypatch):
    if transport_module.sys.platform != "linux":
        pytest.skip("The real BlueZ scanner regression runs on Linux")

    from bleak import BleakScanner
    from bleak.args.bluez import BlueZScannerArgs
    from bleak.backends.bluezdbus.manager import BlueZManager
    from bleak.backends.bluezdbus.scanner import BleakScannerBlueZDBus
    from dbus_fast.constants import MessageType

    class FakeBus:
        def __init__(self):
            self.calls = []
            self.active = False
            self.delay_start = False
            self.start_entered = asyncio.Event()
            self.start_release = asyncio.Event()
            self.start_failure = None
            self.stop_error = False

        async def call(self, message):
            self.calls.append(message)
            if message.member == "StartDiscovery":
                # The daemon has acted, but Bleak has not received its stop closure.
                self.active = True
                self.start_entered.set()
                if self.delay_start:
                    await self.start_release.wait()
                if self.start_failure == "error":
                    raise BleakError("injected StartDiscovery failure")
                if self.start_failure == "cancelled":
                    raise asyncio.CancelledError
            elif message.member == "StopDiscovery":
                if self.stop_error:
                    raise BleakError("injected StopDiscovery failure")
                self.active = False
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN)

    # Construct the real backend/manager without initializing a system D-Bus bus.
    manager = BlueZManager()
    manager._properties["/org/bluez/offline"] = {}
    bus = FakeBus()
    monkeypatch.setattr(manager, "_bus", bus)
    monkeypatch.setattr(
        "bleak.backends.bluezdbus.scanner.get_global_bluez_manager",
        AsyncMock(return_value=manager),
    )
    scanners, starts, stops = [], [], []
    stop_entered = asyncio.Event()

    def factory(*, bluez: BlueZScannerArgs):
        options = bluez.copy()
        options["adapter"] = "offline"
        scanner = BleakScanner(backend=BleakScannerBlueZDBus, bluez=options)
        start = AsyncMock(wraps=scanner.start)
        original_stop = scanner.stop

        async def stopping():
            stop_entered.set()
            await original_stop()

        stop = AsyncMock(wraps=stopping)
        monkeypatch.setattr(scanner, "start", start)
        monkeypatch.setattr(scanner, "stop", stop)
        scanners.append(scanner)
        starts.append(start)
        stops.append(stop)
        return scanner

    monkeypatch.setattr(transport_module, "BleakScanner", factory)
    monkeypatch.setattr(transport_module, "_discovery_scanners", set())
    return SimpleNamespace(
        bus=bus,
        manager=manager,
        scanners=scanners,
        starts=starts,
        stops=stops,
        stop_entered=stop_entered,
    )


async def bounded(awaitable, timeout=0.6):
    task = asyncio.create_task(awaitable)
    done, _ = await asyncio.wait((task,), timeout=timeout)
    assert done, "operation exceeded its complete lifecycle budget"
    return task.result()


@pytest.mark.parametrize("owner", ["discovery", "transport"])
@pytest.mark.parametrize("caller_cancelled", [False, True])
def test_bluez_start_reply_timeout_preserves_startup_until_one_stop_and_replacement(
    offline_bluez, fake_bleak, monkeypatch, owner, caller_cancelled
):
    async def scenario():
        monkeypatch.setattr(transport_module, "_RETRY_DELAY", 0)
        bus = offline_bluez.bus
        bus.delay_start = True
        transport = BleTransport("AA:BB")
        operation = discover(0.001) if owner == "discovery" else transport.connect()
        task = asyncio.create_task(operation)

        async def cleanup():
            if owner == "discovery":
                await transport_module._retry_discovery_cleanup(transport_module._deadline(0.1))
            else:
                await transport.disconnect()

        try:
            await bounded(bus.start_entered.wait())
            if caller_cancelled:
                task.cancel()
            done, _ = await asyncio.wait((task,), timeout=0.3)
            assert done, "startup did not return within its lifecycle/cleanup bounds"
            expected = asyncio.CancelledError if caller_cancelled else TransportError
            with pytest.raises(expected):
                task.result()
            if owner == "discovery":
                assert len(transport_module._discovery_scanners) == 1
                session = next(iter(transport_module._discovery_scanners))
            else:
                session = transport._scanner
                assert session is not None
            original_start = session.start_task
            assert original_start is not None and not original_start.done()
            assert not original_start.cancelling()
            assert not session.closed and session.stop_task is None
            assert offline_bluez.scanners[0]._backend._stop is None
            assert bus.active and offline_bluez.stops[0].await_count == 0
            assert bus.calls[0].body[0]["DuplicateData"].value is True
            with pytest.raises(TransportError, match="scanner start.*cleanup"):
                await bounded(transport.connect())
            if owner == "discovery":
                with pytest.raises(TransportError, match="scanner start.*cleanup"):
                    await bounded(discover(0.001))
            assert len(offline_bluez.scanners) == 1 and not fake_bleak.clients
            assert session.start_task is original_start and not original_start.cancelling()
            assert [call.member for call in bus.calls] == [
                "SetDiscoveryFilter",
                "StartDiscovery",
            ]
        finally:
            bus.start_release.set()
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, TransportError):
                await task
            await bounded(cleanup())

        assert session.closed and not bus.active
        assert session.start_task is original_start and original_start.result() is None
        assert offline_bluez.starts[0].await_count == 1
        assert offline_bluez.stops[0].await_count == 1
        assert [call.member for call in bus.calls] == [
            "SetDiscoveryFilter",
            "StartDiscovery",
            "StopDiscovery",
            "SetDiscoveryFilter",
        ]
        assert bus.calls[-1].body == [{}]
        assert not transport_module._discovery_scanners and transport._scanner is None
        monkeypatch.setattr(transport_module, "BleakScanner", fake_bleak.scanner)
        if owner == "discovery":
            assert await bounded(discover(0.001))
        await bounded(transport.connect())
        assert transport.connected
        await bounded(transport.disconnect())

    asyncio.run(scenario())


@pytest.mark.parametrize("owner", ["discovery", "transport"])
@pytest.mark.parametrize("failure", ["error", "cancelled"])
@pytest.mark.parametrize("after_timeout", [False, True])
def test_bluez_start_failure_after_possible_effects_keeps_unresolved_owner(
    offline_bluez, fake_bleak, monkeypatch, owner, failure, after_timeout
):
    async def scenario():
        monkeypatch.setattr(transport_module, "_RETRY_DELAY", 0)
        bus = offline_bluez.bus
        bus.delay_start = after_timeout
        bus.start_failure = failure
        transport = BleTransport("AA:BB")
        operation = discover(0.001) if owner == "discovery" else transport.connect()
        expected = (
            "start failed: injected StartDiscovery failure"
            if failure == "error"
            else ("start was cancelled")
        )
        try:
            with pytest.raises(TransportError, match="deadline" if after_timeout else expected):
                await bounded(operation)
            if owner == "discovery":
                assert len(transport_module._discovery_scanners) == 1
                session = next(iter(transport_module._discovery_scanners))

                async def cleanup():
                    await transport_module._retry_discovery_cleanup(transport_module._deadline(0.1))

            else:
                session = transport._scanner
                assert session is not None
                cleanup = transport.disconnect
            original_start = session.start_task
            assert original_start is not None
            if after_timeout:
                assert not original_start.done() and not original_start.cancelling()
                bus.start_release.set()
            with pytest.raises(TransportError, match=expected + ".*unresolved"):
                await bounded(cleanup())
            assert original_start.done()
            assert original_start.cancelled() is (failure == "cancelled")
            assert offline_bluez.scanners[0]._backend._stop is None
            bus.start_failure = None
            for _ in range(2):
                with pytest.raises(TransportError, match=expected + ".*unresolved"):
                    await bounded(cleanup())
                with pytest.raises(TransportError, match="unresolved"):
                    await bounded(transport.connect())
                if owner == "discovery":
                    with pytest.raises(TransportError, match="unresolved"):
                        await bounded(discover(0.001))
                    assert session in transport_module._discovery_scanners
                else:
                    assert transport._scanner is session
                assert bus.active and not session.closed
                assert session.start_task is original_start and session.stop_task is None
                assert offline_bluez.starts[0].await_count == 1
                assert offline_bluez.stops[0].await_count == 0
                assert len(offline_bluez.scanners) == 1 and not fake_bleak.clients
            assert [call.member for call in bus.calls] == [
                "SetDiscoveryFilter",
                "StartDiscovery",
            ]
        finally:
            bus.start_release.set()

    asyncio.run(scenario())


@pytest.mark.parametrize("owner", ["discovery", "transport"])
@pytest.mark.parametrize("caller_cancelled", [False, True])
def test_bluez_scanner_stop_timeout_preserves_one_shot_cleanup_until_success(
    offline_bluez, fake_bleak, monkeypatch, owner, caller_cancelled
):
    async def scenario():
        session = transport_module._ScannerSession()
        transport = BleTransport("AA:BB")
        await session.start(transport_module._deadline(0.1))
        if owner == "discovery":
            transport_module._discovery_scanners.add(session)

            async def cleanup():
                await transport_module._retry_discovery_cleanup(transport_module._deadline(0.02))

        else:
            transport._scanner = session
            cleanup = transport.disconnect

        bus = offline_bluez.bus
        assert bus.calls[0].body[0]["DuplicateData"].value is True
        await offline_bluez.manager._bus_lock.acquire()
        try:
            if caller_cancelled:
                task = asyncio.create_task(cleanup())
                await bounded(offline_bluez.stop_entered.wait())
                task.cancel()
                done, _ = await asyncio.wait((task,), timeout=0.1)
                assert done
                with pytest.raises(asyncio.CancelledError):
                    task.result()
            else:
                with pytest.raises(TransportError, match="deadline"):
                    await bounded(cleanup())
            # The real backend has consumed its handle while awaiting the manager lock.
            assert offline_bluez.scanners[0]._backend._stop is None
            with pytest.raises(TransportError, match="cleanup"):
                await bounded(transport.connect())
            if owner == "discovery":
                with pytest.raises(TransportError, match="cleanup"):
                    await bounded(discover(0.001))
            assert bus.active and not session.closed
            assert session.stop_task is not None and not session.stop_task.done()
            assert not session.stop_task.cancelling()
            assert len(offline_bluez.scanners) == 1 and not fake_bleak.clients
            assert "StopDiscovery" not in [call.member for call in bus.calls]
        finally:
            offline_bluez.manager._bus_lock.release()
            await bounded(cleanup())

        assert session.closed and not bus.active
        assert offline_bluez.stops[0].await_count == 1
        assert [call.member for call in bus.calls] == [
            "SetDiscoveryFilter",
            "StartDiscovery",
            "StopDiscovery",
            "SetDiscoveryFilter",
        ]
        assert bus.calls[-1].body == [{}]
        assert not transport_module._discovery_scanners and transport._scanner is None
        # An actual successful stop releases ownership and permits a fresh connection.
        monkeypatch.setattr(transport_module, "BleakScanner", fake_bleak.scanner)
        await bounded(transport.connect())
        assert transport.connected
        await bounded(transport.disconnect())

    asyncio.run(scenario())


@pytest.mark.parametrize("owner", ["discovery", "transport"])
@pytest.mark.parametrize("after_timeout", [False, True])
def test_bluez_scanner_stop_failure_remains_unresolved_even_when_retry_would_be_noop(
    offline_bluez, fake_bleak, owner, after_timeout
):
    async def scenario():
        session = transport_module._ScannerSession()
        transport = BleTransport("AA:BB")
        await session.start(transport_module._deadline(0.1))
        if owner == "discovery":
            transport_module._discovery_scanners.add(session)

            async def cleanup():
                await transport_module._retry_discovery_cleanup(transport_module._deadline(0.02))

        else:
            transport._scanner = session
            cleanup = transport.disconnect

        bus = offline_bluez.bus
        bus.stop_error = True
        if after_timeout:
            await offline_bluez.manager._bus_lock.acquire()
            try:
                with pytest.raises(TransportError, match="deadline"):
                    await bounded(cleanup())
            finally:
                offline_bluez.manager._bus_lock.release()
        with pytest.raises(TransportError, match="StopDiscovery failure.*unresolved"):
            await bounded(cleanup())
        assert offline_bluez.scanners[0]._backend._stop is None
        bus.stop_error = False
        # A new stop would return normally, without reaching the fake bus at all.
        for _ in range(2):
            with pytest.raises(TransportError, match="StopDiscovery failure.*unresolved"):
                await bounded(cleanup())
            with pytest.raises(TransportError, match="unresolved"):
                await bounded(transport.connect())
            if owner == "discovery":
                with pytest.raises(TransportError, match="unresolved"):
                    await bounded(discover(0.001))
            assert bus.active and not session.closed
            assert offline_bluez.stops[0].await_count == 1
            assert len(offline_bluez.scanners) == 1 and not fake_bleak.clients
            if owner == "discovery":
                assert session in transport_module._discovery_scanners
            else:
                assert transport._scanner is session
        assert [call.member for call in bus.calls].count("StopDiscovery") == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["start", "stop", "connect", "notify"])
def test_ble_lifecycle_deadlines_retain_owned_operations_until_cleanup_retry(fake_bleak, phase):
    async def scenario():
        stall = Stall()
        setattr(fake_bleak, phase + "_stall", stall)
        transport = BleTransport("AA:BB")
        try:
            with pytest.raises(TransportError, match="cleanup"):
                await bounded(transport.connect())
            assert not transport.connected
            assert stall.cancelled.is_set() is (phase not in ("start", "stop"))
            if phase in ("start", "stop"):
                assert transport._scanner is not None
                if phase == "start":
                    assert transport._scanner.start_task is not None
                    assert not transport._scanner.start_task.done()
                    assert not transport._scanner.start_task.cancelling()
                else:
                    assert transport._scanner.stop_task is not None
                    assert not transport._scanner.stop_task.done()
            else:
                assert transport._client is fake_bleak.clients[0]
                assert transport._operations.tasks
            # Retrying while cleanup is stuck must not allocate replacement resources.
            counts = (len(fake_bleak.clients), len(fake_bleak.scanners))
            with pytest.raises(TransportError, match="cleanup"):
                await bounded(transport.connect())
            assert counts == (len(fake_bleak.clients), len(fake_bleak.scanners))
        finally:
            stall.release.set()
            await bounded(transport.disconnect())
        assert transport._client is None
        assert transport._scanner is None
        assert not transport._operations.tasks
        assert all(not client.is_connected for client in fake_bleak.clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["start", "stop"])
def test_discovery_bounds_full_scanner_lifecycle_and_retries_cleanup(fake_bleak, phase):
    async def scenario():
        stall = Stall()
        setattr(fake_bleak, phase + "_stall", stall)
        try:
            with pytest.raises(TransportError):
                await bounded(discover(0.01))
            assert transport_module._discovery_scanners
            count = len(fake_bleak.scanners)
            with pytest.raises(TransportError):
                await bounded(discover(0.01))
            assert len(fake_bleak.scanners) == count
        finally:
            stall.release.set()
            devices = await bounded(discover(0.01))
        assert devices == [
            {"transport": "ble", "address": "AA:BB", "name": "PL103_FAKE", "rssi": -50}
        ]
        assert not transport_module._discovery_scanners
        assert all(not scanner.active for scanner in fake_bleak.scanners)

    asyncio.run(scenario())


def test_connect_retries_failed_discovery_cleanup_before_allocating_backend(fake_bleak):
    async def scenario():
        stall = fake_bleak.stop_stall = Stall()
        transport = BleTransport("AA:BB")
        try:
            with pytest.raises(TransportError):
                await bounded(discover(0.01))
            assert transport_module._discovery_scanners
            with pytest.raises(TransportError, match="cleanup"):
                await bounded(transport.connect())
            assert len(fake_bleak.scanners) == 1
            assert not fake_bleak.clients
        finally:
            stall.release.set()
            await bounded(transport.connect())
            await transport.disconnect()
        assert not transport_module._discovery_scanners
        assert all(not scanner.active for scanner in fake_bleak.scanners)

    asyncio.run(scenario())


def test_failed_disconnect_remains_owned_and_old_callbacks_cannot_change_new_generation(fake_bleak):
    async def scenario():
        transport = BleTransport("AA:BB")
        await transport.connect()
        old_client = fake_bleak.clients[0]
        fake_bleak.disconnect_error = True
        with pytest.raises(TransportError, match="disconnect failure"):
            await bounded(transport.disconnect())
        assert old_client.is_connected
        assert transport._client is old_client
        assert not transport.connected
        with pytest.raises(TransportError):
            await bounded(transport.connect())
        assert fake_bleak.clients == [old_client]
        fake_bleak.disconnect_error = False
        await transport.disconnect()
        await transport.connect()
        current = fake_bleak.clients[-1]
        # A callback scheduled by the old backend may run after a successful reconnect.
        old_client.callback(old_client)
        assert transport.connected
        pending = asyncio.get_running_loop().create_future()
        transport._pending[(42, Command.INFO)] = pending
        reply = bytearray(Frame(Command.INFO, 42, b"old", 1).encode())
        old_client.notify(None, reply)
        assert not pending.done()
        current.notify(None, reply)
        assert pending.result().payload == b"old"
        transport._pending.clear()
        await transport.disconnect()

    asyncio.run(scenario())


def test_disconnect_timeout_retains_backend_and_cancellation_handler_for_retry(fake_bleak):
    async def scenario():
        transport = BleTransport("AA:BB")
        await transport.connect()
        stall = fake_bleak.disconnect_stall = Stall()
        old_client = fake_bleak.clients[0]
        try:
            with pytest.raises(TransportError, match="deadline"):
                await bounded(transport.disconnect())
            assert old_client.is_connected
            assert transport._client is old_client
            assert transport._operations.tasks
            with pytest.raises(TransportError, match="cleanup"):
                await bounded(transport.connect())
            assert fake_bleak.clients == [old_client]
        finally:
            stall.release.set()
            await bounded(transport.disconnect())
        assert transport._client is None
        await transport.connect()
        await transport.disconnect()

    asyncio.run(scenario())


def test_disconnect_returning_while_backend_still_connected_is_an_explicit_failure(fake_bleak):
    async def scenario():
        transport = BleTransport("AA:BB")
        await transport.connect()
        backend = fake_bleak.clients[0]
        fake_bleak.disconnect_keeps_connected = True
        try:
            with pytest.raises(TransportError, match="remains connected"):
                await transport.disconnect()
            assert transport._client is backend and backend.is_connected
            with pytest.raises(TransportError, match="remains connected"):
                await transport.connect()
            assert fake_bleak.clients == [backend]
        finally:
            fake_bleak.disconnect_keeps_connected = False
            await transport.disconnect()

    asyncio.run(scenario())


def test_scanner_stop_error_preserves_scanner_and_prevents_new_backend(fake_bleak):
    async def scenario():
        transport = BleTransport("AA:BB")
        fake_bleak.stop_error = True
        with pytest.raises(TransportError, match="scanner stop failure.*unresolved"):
            await transport.connect()
        scanner = transport._scanner
        assert scanner is not None and fake_bleak.scanners[0].active
        assert transport._client is None
        assert not fake_bleak.clients[0].is_connected
        fake_bleak.stop_error = False
        with pytest.raises(TransportError, match="scanner stop failure.*unresolved"):
            await transport.connect()
        with pytest.raises(TransportError, match="scanner stop failure.*unresolved"):
            await transport.disconnect()
        assert len(fake_bleak.scanners) == 1 and len(fake_bleak.clients) == 1
        assert transport._scanner is scanner and not scanner.closed
        assert fake_bleak.scanners[0].stop_calls == 1

    asyncio.run(scenario())


def test_connect_recovers_when_two_transient_backend_attempts_fail(fake_bleak, monkeypatch):
    async def scenario():
        monkeypatch.setattr(transport_module, "_CONNECT_TIMEOUT", 1)
        monkeypatch.setattr(transport_module, "_RETRY_DELAY", 0)
        fake_bleak.connect_failures_remaining = 2
        transport = BleTransport("AA:BB")
        try:
            await bounded(transport.connect())
            assert transport.connected
            assert len(fake_bleak.clients) == 3
            assert not any(client.is_connected for client in fake_bleak.clients[:-1])
            assert all(not scanner.active for scanner in fake_bleak.scanners)
        finally:
            await transport.disconnect()

    asyncio.run(scenario())


def test_connect_retries_share_one_lifecycle_deadline(fake_bleak, monkeypatch):
    async def scenario():
        async def never_notify():
            await asyncio.Event().wait()

        monkeypatch.setattr(transport_module, "_CONNECT_TIMEOUT", 0.06)
        fake_bleak.notify_stall = never_notify
        transport = BleTransport("AA:BB")
        try:
            with pytest.raises(TransportError, match="deadline"):
                await bounded(transport.connect(), 0.25)
        finally:
            await transport.disconnect()
        assert all(not backend.is_connected for backend in fake_bleak.clients)
        assert all(not scanner.active for scanner in fake_bleak.scanners)

    asyncio.run(scenario())


@pytest.mark.parametrize("repeat_cancel", [False, True])
def test_cancellation_during_failed_connect_cleanup_retains_candidate_and_releases_lock(
    fake_bleak, repeat_cancel
):
    async def scenario():
        stall = fake_bleak.disconnect_stall = Stall()
        fake_bleak.connect_error = True
        bridge = Bridge("fake-token")

        async def connect():
            async with bridge.lock:
                await bridge.connect_device("AA:BB", "ble")

        task = asyncio.create_task(connect())
        try:
            await bounded(stall.entered.wait())
            task.cancel()
            if repeat_cancel:
                await asyncio.sleep(0)
                task.cancel()
            done, _ = await asyncio.wait((task,), timeout=0.3)
            assert done
            with pytest.raises(asyncio.CancelledError):
                task.result()
            assert not bridge.lock.locked()
            candidate = bridge._connecting_client
            assert candidate is not None and isinstance(candidate.transport, BleTransport)
            assert candidate.transport._cleanup_task is not None
            assert candidate.transport._client is fake_bleak.clients[0]
            assert not candidate.state.connected
        finally:
            stall.release.set()
            await bridge.shutdown(bridge.app)
            with suppress(asyncio.CancelledError):
                await task
        assert all(not backend.is_connected for backend in fake_bleak.clients)

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["start", "stop"])
def test_discovery_cancellation_retains_scanner_until_cleanup_can_finish(fake_bleak, phase):
    async def scenario():
        stall = Stall()
        setattr(fake_bleak, phase + "_stall", stall)
        task = asyncio.create_task(discover(0.01))
        try:
            await bounded(stall.entered.wait())
            task.cancel()
            done, _ = await asyncio.wait((task,), timeout=0.3)
            assert done
            with pytest.raises(asyncio.CancelledError):
                task.result()
            assert transport_module._discovery_scanners
        finally:
            stall.release.set()
            await bounded(discover(0.01))
            with suppress(asyncio.CancelledError):
                await task
        assert not transport_module._discovery_scanners
        assert all(not scanner.active for scanner in fake_bleak.scanners)

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["start", "connect", "stop"])
def test_cancelled_connect_cleans_up_without_waiting_forever(fake_bleak, phase):
    async def scenario():
        stall = Stall()
        setattr(fake_bleak, phase + "_stall", stall)
        transport = BleTransport("AA:BB")
        task = asyncio.create_task(transport.connect())
        try:
            await bounded(stall.entered.wait())
            task.cancel()
            done, _ = await asyncio.wait((task,), timeout=0.3)
            assert done
            with pytest.raises(asyncio.CancelledError):
                task.result()
            assert not transport.connected
            assert transport._client is not None or transport._scanner is not None
        finally:
            stall.release.set()
            await bounded(transport.disconnect())
            with suppress(asyncio.CancelledError, TransportError):
                await task

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["read", "set"])
@pytest.mark.parametrize("failure", ["info_bad", "id_bad", "info_timeout", "id_timeout"])
def test_failed_reconnect_identity_invalidates_snapshot_and_blocks_later_commands(
    fake_bleak, operation, failure
):
    async def scenario():
        transport = BleTransport("AA:BB", timeout=0.01)
        client = LightClient(transport=transport)
        await client.connect()
        state = client.state
        assert state.connected and state.device is not None and state.device.id == 0
        fake_bleak.fail_control = True
        fake_bleak.identity_failure = failure
        action = client.refresh() if operation == "read" else client.set(brightness=5)
        with pytest.raises((ProtocolError, TransportError)):
            await bounded(action, 1)
        assert client._info is None
        assert not client.state.connected
        assert client.state.device is None
        assert client.state.brightness is None
        assert client.state.updatedAt is None
        assert not transport.connected and transport._client is None
        command_count = len(fake_bleak.commands)
        with pytest.raises(TransportError, match="Connect"):
            await client.refresh()
        with pytest.raises(TransportError, match="Connect"):
            await client.set(brightness=5)
        assert len(fake_bleak.commands) == command_count
        fake_bleak.identity_failure = None
        assert (await client.connect()).connected
        assert (3, Command.INFO) in fake_bleak.identity_queries
        assert (3, Command.DEVICE_ID) in fake_bleak.identity_queries
        await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["read", "set"])
@pytest.mark.parametrize("failure", ["info_cancel", "id_cancel"])
def test_cancelled_reconnect_identity_disconnects_and_clears_snapshot(
    fake_bleak, operation, failure
):
    async def scenario():
        transport = BleTransport("AA:BB", timeout=1)
        client = LightClient(transport=transport)
        await client.connect()
        fake_bleak.fail_control = True
        fake_bleak.identity_failure = failure
        action = client.refresh() if operation == "read" else client.set(brightness=5)
        task = asyncio.create_task(action)
        await bounded(fake_bleak.identity_entered.wait(), 1)
        task.cancel()
        done, _ = await asyncio.wait((task,), timeout=0.3)
        assert done
        with pytest.raises(asyncio.CancelledError):
            task.result()
        assert client._info is None
        assert not client.state.connected and client.state.device is None
        assert client.state.brightness is None
        assert transport._client is None
        assert all(not backend.is_connected for backend in fake_bleak.clients)
        with pytest.raises(TransportError):
            await client.set(cct=5000)
        fake_bleak.identity_failure = None
        await client.connect()
        await client.close()

    asyncio.run(scenario())


def test_bridge_connect_deadline_releases_lock_and_retains_failed_candidate(fake_bleak):
    async def scenario():
        stall = fake_bleak.stop_stall = Stall()
        bridge = Bridge("fake-token", allow_mock=True)
        headers = {"Authorization": "Bearer fake-token"}
        try:
            async with TestClient(TestServer(bridge.app)) as http:
                response = await bounded(
                    http.post("/v1/connect", json={"address": "AA:BB"}, headers=headers)
                )
                assert response.status == 503
                assert (await response.json())["error"]["code"] == "device_unavailable"
                assert not bridge.lock.locked()
                assert bridge.client is None
                candidate = bridge._connecting_client
                assert candidate is not None
                assert isinstance(candidate.transport, BleTransport)
                assert candidate.transport._scanner is not None
                state = await bounded(http.get("/v1/state", headers=headers))
                assert (await state.json())["connected"] is False
                # No mock success is allowed until the failed real candidate is cleaned up.
                response = await bounded(
                    http.post(
                        "/v1/connect",
                        json={"address": "test", "transport": "mock"},
                        headers=headers,
                    )
                )
                assert response.status == 503
                assert bridge._connecting_client is candidate
                stall.release.set()
                response = await bounded(
                    http.post(
                        "/v1/connect",
                        json={"address": "test", "transport": "mock"},
                        headers=headers,
                    )
                )
                assert response.status == 200
                assert (await response.json())["connected"] is True
        finally:
            stall.release.set()
            await bridge.shutdown(bridge.app)

    asyncio.run(scenario())


def test_poll_queued_behind_disconnect_survives_and_resumes_publishing(monkeypatch):
    # Fake sleeps leave each connect's readback "fresh"; this regression needs
    # every tick to perform the ordinary hardware poll.
    monkeypatch.setattr("lighttime.bridge._READBACK_FRESHNESS", 0)

    async def scenario():
        bridge = Bridge("fake-token", allow_mock=True)
        await bridge.connect_device("first", "mock")
        queue = asyncio.Queue(maxsize=1)
        bridge.subscribers.add(queue)
        sleeping, tick = asyncio.Event(), asyncio.Event()
        real_sleep = asyncio.sleep

        async def poll_sleep(delay):
            if delay == 2:
                sleeping.set()
                await tick.wait()
                tick.clear()
                sleeping.clear()
            else:
                await real_sleep(delay)

        monkeypatch.setattr("lighttime.bridge.asyncio.sleep", poll_sleep)
        await bridge.lock.acquire()
        task = bridge.poll_task = asyncio.create_task(bridge.poll())
        try:
            await sleeping.wait()
            tick.set()
            await real_sleep(0)
            assert bridge.lock._waiters  # The poll is queued behind the disconnect.
            assert bridge.client is not None
            await bridge.client.close()
            bridge.client = None
            bridge.lock.release()
            await bounded(sleeping.wait())
            assert not task.done()
            await bridge.connect_device("second", "mock")
            queue.get_nowait()
            current = bridge.client
            assert current is not None and isinstance(current.transport, MockTransport)
            current.transport.brightness = 12
            tick.set()
            state = await bounded(queue.get())
            assert state["connected"] and state["brightness"] == 12
            assert not task.done()
        finally:
            if bridge.lock.locked():
                bridge.lock.release()
            await bridge.shutdown(bridge.app)

    asyncio.run(scenario())


@pytest.mark.parametrize("failed_task", ["poll_task", "connection_task"])
def test_shutdown_closes_client_even_when_background_task_already_failed(failed_task):
    async def scenario():
        bridge = Bridge("fake-token", allow_mock=True)
        await bridge.connect_device("test", "mock")
        assert bridge.client is not None
        transport = bridge.client.transport

        async def failed():
            raise RuntimeError("injected background failure")

        task = asyncio.create_task(failed())
        await asyncio.sleep(0)
        setattr(bridge, failed_task, task)
        with pytest.raises(RuntimeError, match="background failure"):
            await bridge.shutdown(bridge.app)
        assert not transport.connected
        assert bridge.client is None

    asyncio.run(scenario())


def test_initial_autoconnect_does_not_delay_http_readiness(fake_bleak):
    async def scenario():
        stall = fake_bleak.start_stall = Stall()
        bridge = Bridge("fake-token")
        bridge.initial_address = "AA:BB"
        try:
            async with TestClient(TestServer(bridge.app)) as http:
                await bounded(stall.entered.wait())
                assert (await bounded(http.get("/v1/health"))).status == 200
                response = await bounded(
                    http.get("/v1/state", headers={"Authorization": "Bearer fake-token"})
                )
                assert (await response.json())["connected"] is False
                stall.release.set()
        finally:
            stall.release.set()
            await bridge.shutdown(bridge.app)

    asyncio.run(scenario())


def test_bridge_keeps_client_when_disconnect_cleanup_fails(fake_bleak):
    async def scenario():
        bridge = Bridge("fake-token", allow_mock=True)
        headers = {"Authorization": "Bearer fake-token"}
        async with TestClient(TestServer(bridge.app)) as http:
            assert (
                await http.post("/v1/connect", json={"address": "AA:BB"}, headers=headers)
            ).status == 200
            old_client = bridge.client
            fake_bleak.disconnect_error = True
            try:
                response = await http.post("/v1/disconnect", headers=headers)
                assert response.status == 503
                assert bridge.client is old_client
                response = await http.post(
                    "/v1/connect", json={"address": "mock", "transport": "mock"}, headers=headers
                )
                assert response.status == 503
                assert bridge.client is old_client
                assert not bridge.state()["connected"]
            finally:
                fake_bleak.disconnect_error = False
            assert (await http.post("/v1/disconnect", headers=headers)).status == 200
            assert bridge.client is None

    asyncio.run(scenario())


@pytest.mark.parametrize("queued_state", [False, True])
def test_server_shutdown_finishes_open_sse_without_waiting_for_grace_period(queued_state):
    async def scenario():
        bridge = Bridge("fake-token", allow_mock=True)
        await bridge.connect_device("mock", "mock")
        lamp = bridge.client
        runner = web.AppRunner(bridge.app, shutdown_timeout=1)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        address = runner.addresses[0]
        try:
            async with ClientSession() as http:
                async with http.get(
                    f"http://127.0.0.1:{address[1]}/v1/events",
                    headers={"Authorization": "Bearer fake-token"},
                ) as response:
                    assert response.status == 200
                    assert await response.content.readuntil(b"\n\n")
                    if queued_state:
                        bridge.publish()
                    started = asyncio.get_running_loop().time()
                    await runner.cleanup()
                    assert asyncio.get_running_loop().time() - started < 0.5
                    await asyncio.wait_for(response.content.read(), 0.5)
                    assert response.content.at_eof()
                    assert not bridge.subscribers
                    assert lamp is not None and not lamp.state.connected
        finally:
            await runner.cleanup()

    asyncio.run(scenario())


def test_serial_runtime_connect_is_invalid_but_remains_discoverable(fake_bleak, monkeypatch):
    async def scenario():
        monkeypatch.setattr(
            transport_module,
            "comports",
            lambda: [
                SimpleNamespace(
                    device="/not-a-real-port", description="USB G60", vid=0xFFF8, pid=0x0180
                )
            ],
        )
        devices = await discover(0.01)
        assert devices[0] == {
            "transport": "serial",
            "address": "/not-a-real-port",
            "name": "USB G60",
            "accessible": False,
        }
        bridge = Bridge("fake-token")
        async with TestClient(TestServer(bridge.app)) as http:
            response = await http.post(
                "/v1/connect",
                json={"transport": "serial", "address": "/not-a-real-port"},
                headers={"Authorization": "Bearer fake-token"},
            )
            assert response.status == 400
            body = await response.json()
            assert body["error"]["code"] == "invalid_request"
            assert "usb-info" in body["error"]["message"]
        assert not fake_bleak.clients

    asyncio.run(scenario())


def test_nonadvertising_stale_bluez_link_reports_targeted_manual_recovery(fake_bleak, monkeypatch):
    async def scenario():
        address = "F3:58:E9:18:35:EA"  # A string only; this test never contacts BlueZ.
        fake_bleak.device.address = address
        fake_bleak.advertising = False
        monkeypatch.setattr(transport_module.sys, "platform", "linux")
        monkeypatch.setattr(transport_module, "_CONNECT_TIMEOUT", 1.2)
        previous_owner_link = SimpleNamespace(connected=True)
        transport = BleTransport(address)
        with pytest.raises(TransportError, match="not advertising") as error:
            await bounded(transport.connect(), 2)
        message = str(error.value)
        assert f"bluetoothctl info {address}" in message
        assert f"bluetoothctl disconnect {address}" in message
        assert "confirm no other app" in message
        assert previous_owner_link.connected
        assert not fake_bleak.clients
        assert transport._client is None and transport._scanner is None
        assert all(not scanner.active for scanner in fake_bleak.scanners)

    asyncio.run(scenario())
