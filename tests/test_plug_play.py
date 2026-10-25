"""Plug-and-play contracts over offline transports and ephemeral HTTP only."""

import asyncio
import sys
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

import lighttime.bridge as bridge_module
import lighttime.cli as cli_module
import lighttime.transport as transport_module
from lighttime import BridgeClient, TransportError
from lighttime.bridge import Bridge, MockTransport
from lighttime.protocol import Command, Frame


def ble_device(address="OFFLINE"):
    return {"transport": "ble", "address": address, "name": "PL103_OFFLINE"}


@pytest.fixture
def lamp(monkeypatch):
    rig = SimpleNamespace(devices=[ble_device()], scans=0, transports=[], frames=[])

    async def discover(timeout=5):
        rig.scans += 1
        return rig.devices

    class Lamp(MockTransport):
        kind = "ble"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            rig.transports.append(self)

        async def _write(self, data):
            rig.frames.append(Frame.decode(data))
            await super()._write(data)

    monkeypatch.setattr(bridge_module, "discover", discover)
    monkeypatch.setattr(bridge_module, "BleTransport", Lamp)
    return rig


@pytest.mark.parametrize("arguments", [[], ["serve"], ["serve", "--no-auth"]])
def test_cli_default_loopback_tokenless_autoconnect(monkeypatch, capsys, tmp_path, arguments):
    token_file = tmp_path / "config" / "lighttime" / "bridge.token"
    monkeypatch.setattr(cli_module, "default_token_path", lambda: token_file)
    monkeypatch.setattr(sys, "argv", ["lighttime", *arguments])
    applications = []

    def no_token(path):
        pytest.fail("Default startup attempted to create a token")

    def run_app(app, **options):
        applications.append(app.on_startup[-1].__self__)
        assert options == {
            "host": "127.0.0.1",
            "port": 8877,
            "access_log": None,
            "shutdown_timeout": 5,
        }

    monkeypatch.setattr(cli_module, "ensure_token", no_token)
    monkeypatch.setattr(cli_module.web, "run_app", run_app)
    cli_module.main()
    bridge = applications[0]
    assert not bridge.auth_required and bridge.token == "" and bridge.auto_connect is True
    assert bridge.initial_address is None and not token_file.parent.exists()
    assert capsys.readouterr().out == ""


def test_cli_auth_uses_default_file_and_auto_connect_optout(monkeypatch, capsys, tmp_path):
    token_file = tmp_path / "bridge.token"
    monkeypatch.setattr(cli_module, "default_token_path", lambda: token_file)
    monkeypatch.setattr(
        sys, "argv", ["lighttime", "serve", "--auth", "--no-auto-connect", "--address", "OFFLINE"]
    )
    applications = []
    monkeypatch.setattr(
        cli_module.web, "run_app", lambda app, **_: applications.append(app.on_startup[-1].__self__)
    )
    cli_module.main()
    bridge = applications[0]
    assert bridge.auth_required and bridge.token == token_file.read_text().strip()
    assert bridge.auto_connect is False and bridge.initial_address == "OFFLINE"
    assert str(token_file) in capsys.readouterr().out


def test_auto_connect_optout_and_configured_address(lamp):
    async def scenario():
        bridge = Bridge(auto_connect=False)
        bridge.initial_address = "CONFIGURED"
        async with TestClient(TestServer(bridge.app)) as http:
            assert bridge.connection_task is None and lamp.scans == 0
            assert not lamp.transports
            response = await http.post("/v1/connect", json={})
            state = await response.json()
            assert response.status == 200 and state["address"] == "CONFIGURED"
            assert state["connected"] and lamp.scans == 0

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "devices,message",
    [
        ([], "No MOLUS G60"),
        ([{"transport": "serial", "address": "/dev/offline", "name": "PL103"}], "No MOLUS G60"),
        ([{"transport": "ble", "address": "OTHER", "name": "unrelated"}], "No MOLUS G60"),
        ([ble_device("FIRST"), ble_device("SECOND")], "Multiple MOLUS G60"),
    ],
)
def test_auto_connect_requires_exactly_one_ble_pl103(lamp, devices, message):
    lamp.devices = devices

    async def scenario():
        bridge = Bridge()
        async with TestClient(TestServer(bridge.app)) as http:
            response = await http.post("/v1/connect", json={})
            body = await response.json()
            assert response.status == 503 and body["error"]["code"] == "device_unavailable"
            assert message in body["error"]["message"]
            assert "explicit" in body["error"]["message"]
            if len(devices) == 2:
                assert all(device["address"] in body["error"]["message"] for device in devices)
            assert lamp.scans == 1 and not lamp.transports and bridge.initial_address is None

    asyncio.run(scenario())


@pytest.mark.parametrize("startup", [False, True])
def test_single_device_identity_readback_health_and_sdk_retry(lamp, startup):
    lamp.devices.insert(0, {"transport": "serial", "address": "/dev/offline", "name": "PL103"})

    async def scenario():
        bridge = Bridge(auto_connect=startup)
        async with TestClient(TestServer(bridge.app)) as http:
            if startup:
                assert bridge.connection_task is not None
                await bridge.connection_task
            async with BridgeClient(str(http.make_url("/"))) as sdk:
                state = await sdk.connect()
                assert state["connected"] and state["address"] == "OFFLINE"
                assert state["device"] == {"model": "PL103", "serial": "MOCK-NOT-HARDWARE", "id": 0}
                assert state["brightness"] == 5 and state["cct"] == 5000 and state["updatedAt"]
                assert lamp.scans == 1 and len(lamp.transports) == 1
                assert {Command.INFO, Command.DEVICE_ID, Command.BRIGHTNESS, Command.CCT} <= {
                    frame.command for frame in lamp.frames
                }
                health = await (await http.get("/v1/health")).json()
                assert health["defaultAddress"] == "OFFLINE" and not health["authRequired"]
                # Reuse must query fresh hardware state and keep the running effect.
                effect = await sdk.start_effect("blink", duration=None)
                run = bridge.effects._run
                assert run is not None
                runner = run.task
                lamp.transports[0].cct = 4300
                before = len(lamp.frames)
                state = await sdk.connect(transport="ble")
                assert state["cct"] == 4300
                assert len(lamp.frames) >= before + 2
                status = await sdk.effect()
                assert status["running"] and status["id"] == effect["id"]
                assert bridge.effects._run is run and runner is not None and not runner.cancelling()
                assert lamp.scans == 1 and len(lamp.transports) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("succeeds", [False, True])
def test_startup_and_http_share_one_discovery(lamp, monkeypatch, succeeds):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def discover(timeout=5):
            lamp.scans += 1
            entered.set()
            await release.wait()
            return lamp.devices if succeeds else []

        monkeypatch.setattr(bridge_module, "discover", discover)
        bridge = Bridge(auto_connect=True)
        async with TestClient(TestServer(bridge.app)) as http:
            await asyncio.wait_for(entered.wait(), 1)
            assert (await http.get("/v1/health")).status == 200
            pending = asyncio.create_task(http.post("/v1/connect", json={}))
            try:
                # Wait for the HTTP handler to enter the shared automatic attempt.
                async with asyncio.timeout(1):
                    while len(bridge._device_tasks) < 2:
                        await asyncio.sleep(0)
                release.set()
                response = await pending
                assert response.status == (200 if succeeds else 503)
                assert bridge.connection_task is not None
                await bridge.connection_task
                assert lamp.scans == 1 and len(lamp.transports) == int(succeeds)
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())


def test_cancelled_waiter_keeps_auto_attempt_owned(lamp, monkeypatch):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def discover(timeout=5):
            lamp.scans += 1
            entered.set()
            await release.wait()
            return lamp.devices

        monkeypatch.setattr(bridge_module, "discover", discover)
        bridge = Bridge()
        waiter = asyncio.create_task(bridge._connect_automatically())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            owner = bridge._auto_connection_task
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert owner in bridge._device_tasks and owner is not None and not owner.cancelling()
            release.set()
            assert (await bridge._connect_automatically())["connected"]
            assert lamp.scans == 1 and len(lamp.transports) == 1
        finally:
            release.set()
            await bridge.shutdown(bridge.app)

    asyncio.run(scenario())


def test_disconnect_drains_startup_discovery_and_prevents_late_connection(lamp, monkeypatch):
    async def scenario():
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def discover(timeout=5):
            lamp.scans += 1
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()  # Simulate a backend that ignores cancellation.
            return lamp.devices

        monkeypatch.setattr(bridge_module, "discover", discover)
        bridge = Bridge(auto_connect=True)
        async with TestClient(TestServer(bridge.app)) as http:
            await asyncio.wait_for(entered.wait(), 1)
            pending = asyncio.create_task(http.post("/v1/disconnect", json={}))
            try:
                await asyncio.wait_for(cancelled.wait(), 1)
                assert not pending.done() and bridge.effects.control_lock.locked()
                release.set()
                response = await asyncio.wait_for(pending, 1)
                assert response.status == 200 and not (await response.json())["connected"]
                assert bridge.connection_task is not None
                await bridge.connection_task
                assert (
                    bridge._auto_connection_task is not None and bridge._auto_connection_task.done()
                )
                assert bridge.client is None and bridge._connecting_client is None
                assert lamp.scans == 1 and not lamp.transports
                assert not (await (await http.get("/v1/state")).json())["connected"]
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("address", [" ", "\t", "\r\n", "\u2003"])
def test_blank_address_preserves_active_connection_and_effect(lamp, address):
    async def scenario():
        bridge = Bridge()
        async with TestClient(TestServer(bridge.app)) as http:
            async with BridgeClient(str(http.make_url("/"))) as sdk:
                await sdk.connect("EXPLICIT")
                effect = await sdk.start_effect(
                    "blink", duration=None, minBrightness=5, maxBrightness=5
                )
                client, run = bridge.client, bridge.effects._run
                before = len(lamp.frames)
                response = await http.post("/v1/connect", json={"address": address})
                assert response.status == 400
                assert (await response.json())["error"]["code"] == "invalid_request"
                assert bridge.client is client and client is not None and client.state.connected
                assert client.state.address == "EXPLICIT" and lamp.scans == 0
                assert len(lamp.transports) == 1 and len(lamp.frames) == before
                assert bridge.effects._run is run and run is not None and run.task is not None
                assert not run.task.cancelling() and (await sdk.effect())["id"] == effect["id"]
                assert (await sdk.effect())["running"]

    asyncio.run(scenario())


@pytest.mark.parametrize("locked", ["lock", "control_lock"])
def test_disconnect_invalidates_older_auto_queued_behind_locks(lamp, locked):
    async def scenario():
        bridge = Bridge(auto_connect=False)
        async with TestClient(TestServer(bridge.app)) as http:
            lock = bridge.lock if locked == "lock" else bridge.effects.control_lock
            await lock.acquire()
            # HTTP admits the automatic request first, but its separately owned
            # task can reach control_lock after the disconnect handler does.
            pending = asyncio.gather(
                http.post("/v1/connect", json={}), http.post("/v1/disconnect", json={})
            )
            try:
                async with asyncio.timeout(1):
                    while bridge._connection_generation == 0:
                        await asyncio.sleep(0)
                assert bridge._auto_connection_task is not None
            finally:
                lock.release()
            connected, disconnected = await asyncio.wait_for(pending, 1)
            assert disconnected.status == 200 and not (await disconnected.json())["connected"]
            assert connected.status == 503
            error = (await connected.json())["error"]
            assert error["code"] == "device_unavailable" and "superseded" in error["message"]
            assert not (await (await http.get("/v1/state")).json())["connected"]
            assert not lamp.transports and lamp.scans == 0
            # A new request after disconnect is a new, allowed generation.
            response = await http.post("/v1/connect", json={})
            assert response.status == 200 and (await response.json())["connected"]
            assert len(lamp.transports) == 1 and lamp.scans == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("address", [None, "CONFIGURED"])
def test_disconnect_invalidates_startup_before_first_task_turn(lamp, address):
    async def scenario():
        bridge = Bridge(auto_connect=True)
        bridge.initial_address = address
        try:
            await bridge.startup(bridge.app)
            response = await bridge.disconnect(None)
            assert response.status == 200
            assert bridge.connection_task is not None
            await bridge.connection_task
            assert bridge.client is None and not lamp.transports and lamp.scans == 0
            assert (await bridge._connect_automatically())["connected"]
            assert len(lamp.transports) == 1 and lamp.scans == int(address is None)
        finally:
            await bridge.shutdown(bridge.app)

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["replacement_close", "connect", "identify", "read", "reuse"])
def test_superseded_auto_guard_survives_backend_ignoring_cancellation(lamp, monkeypatch, boundary):
    async def scenario():
        entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        bridge = Bridge(auto_connect=False)
        bridge.initial_address = "CONFIGURED"
        connects = 0
        original_connect = bridge_module.BleTransport.connect
        original_request = bridge_module.BleTransport.request

        async def gate():
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()

        async def connect(transport):
            nonlocal connects
            connects += 1
            if boundary == "connect":
                await gate()
            await original_connect(transport)

        async def request(transport, command, payload=b""):
            if (boundary == "identify" and command == Command.INFO) or (
                boundary in {"read", "reuse"} and command == Command.BRIGHTNESS
            ):
                await gate()
                if boundary in {"read", "reuse"}:
                    raise TransportError("injected late read failure")
            return await original_request(transport, command, payload)

        async with TestClient(TestServer(bridge.app)) as http:
            if boundary in {"replacement_close", "reuse"}:
                await bridge.connect_device("OLD", "ble")
                client = bridge.client
                assert client is not None
                if boundary == "replacement_close":
                    await client.close()
                    original_close = client.close

                    async def close():
                        await gate()
                        await original_close()

                    monkeypatch.setattr(client, "close", close)
            monkeypatch.setattr(bridge_module.BleTransport, "connect", connect)
            monkeypatch.setattr(bridge_module.BleTransport, "request", request)
            pending = asyncio.create_task(http.post("/v1/connect", json={}))
            disconnect = None
            try:
                await asyncio.wait_for(entered.wait(), 1)
                owner = bridge._auto_connection_task
                disconnect = asyncio.create_task(http.post("/v1/disconnect", json={}))
                await asyncio.wait_for(cancelled.wait(), 1)
                assert owner is not None and owner in bridge._device_tasks and not owner.done()
                assert not disconnect.done()
                counts = connects, len(lamp.transports)
                release.set()
                response = await asyncio.wait_for(pending, 1)
                assert response.status == 503
                assert "superseded" in (await response.json())["error"]["message"]
                assert (await asyncio.wait_for(disconnect, 1)).status == 200
                assert counts == (connects, len(lamp.transports))
                assert bridge.client is None and bridge._connecting_client is None
                assert all(not transport.connected for transport in lamp.transports)
                assert not (await (await http.get("/v1/state")).json())["connected"]
                # Identification/readback must not continue into another command
                # or reconnect after the late backend call returns.
                if boundary in {"connect", "identify"}:
                    assert not any(frame.command == Command.DEVICE_ID for frame in lamp.frames)
            finally:
                release.set()
                await asyncio.gather(
                    *[task for task in (pending, disconnect) if task is not None],
                    return_exceptions=True,
                )

    asyncio.run(scenario())


def test_auto_connect_budget_and_manual_retry(lamp, monkeypatch, capsys):
    async def scenario():
        entered = asyncio.Event()

        async def discover(timeout=5):
            lamp.scans += 1
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(bridge_module, "discover", discover)
        monkeypatch.setattr(bridge_module, "_AUTO_CONNECT_TIMEOUT", 0.03)
        bridge = Bridge(auto_connect=True)
        async with TestClient(TestServer(bridge.app)) as http:
            await asyncio.wait_for(entered.wait(), 1)
            assert bridge.connection_task is not None
            await asyncio.wait_for(bridge.connection_task, 1)
            assert not lamp.transports and lamp.scans == 1
            assert "timed out" in capsys.readouterr().out
            await asyncio.sleep(0.04)
            assert lamp.scans == 1  # Startup does not retry or scan perpetually.

            async def available(timeout=5):
                lamp.scans += 1
                return lamp.devices

            monkeypatch.setattr(bridge_module, "discover", available)
            assert (await http.post("/v1/connect", json={})).status == 200
            assert lamp.scans == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("startup", [False, True])
def test_shutdown_during_auto_discovery_preserves_scanner_cleanup(monkeypatch, startup):
    async def scenario():
        entered, stopping, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        scanners = []

        class Scanner:
            def __init__(self):
                self.active = False
                self.discovered_devices_and_advertisement_data = {}
                scanners.append(self)

            async def start(self):
                self.active = True
                entered.set()

            async def stop(self):
                stopping.set()
                await release.wait()
                self.active = False

        monkeypatch.setattr(transport_module, "BleakScanner", lambda **_: Scanner())
        monkeypatch.setattr(transport_module, "comports", lambda: [])
        monkeypatch.setattr(transport_module, "_discovery_scanners", set())
        monkeypatch.setattr(transport_module, "_CLEANUP_TIMEOUT", 0.02)
        monkeypatch.setattr(bridge_module, "_FINAL_CLEANUP_TIMEOUT", 0.02)
        bridge = Bridge(auto_connect=startup)
        async with TestClient(TestServer(bridge.app)) as http:
            pending = None if startup else asyncio.create_task(http.post("/v1/connect", json={}))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                with pytest.raises(TransportError, match="cleanup unresolved"):
                    await asyncio.wait_for(bridge.shutdown(bridge.app), 1)
                await asyncio.wait_for(stopping.wait(), 1)
                assert (
                    bridge._auto_connection_task is not None and bridge._auto_connection_task.done()
                )
                assert scanners[0].active and len(transport_module._discovery_scanners) == 1
                assert bridge._cleanup_task is not None and not bridge._cleanup_task.done()
                assert bridge.client is None and bridge._connecting_client is None
                if pending is not None:
                    assert (await asyncio.wait_for(pending, 1)).status == 503
            finally:
                release.set()
                if bridge._cleanup_task is not None:
                    await asyncio.wait_for(bridge._cleanup_task, 1)
                await bridge.shutdown(bridge.app)
            assert len(scanners) == 1 and not scanners[0].active
            assert not transport_module._discovery_scanners and not bridge._device_tasks

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "body", [{"address": ""}, {"address": None}, {"transport": "serial"}, {"unknown": True}]
)
def test_invalid_connect_still_validates_before_discovery(lamp, body):
    async def scenario():
        bridge = Bridge()
        async with TestClient(TestServer(bridge.app)) as http:
            assert (await http.post("/v1/connect", json=body)).status == 400
            assert not lamp.transports and lamp.scans == 0
            response = await http.post("/v1/connect", json={"address": "EXPLICIT", "deviceId": 999})
            assert response.status == 200 and (await response.json())["device"]["id"] == 0
            assert lamp.scans == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("installed", [False, True])
def test_sdk_module_uses_installed_package_independently_of_web_root(
    monkeypatch, tmp_path, installed
):
    package_file = tmp_path / "site-packages" / "lighttime" / "bridge.py"
    module = package_file.with_name("static") / "sdk.js"
    if installed:
        module.parent.mkdir(parents=True)
        module.write_text("export const installed = true;\n")
    web_root = tmp_path / "controller"
    web_root.mkdir()
    (web_root / "sdk.js").write_text("wrong module")
    monkeypatch.setattr(bridge_module, "__file__", str(package_file))

    async def scenario():
        bridge = Bridge("private-token", web_root=web_root, allow_origins=["https://site.test"])
        async with TestClient(TestServer(bridge.app)) as http:
            response = await http.get("/lighttime.js", headers={"Origin": "https://site.test"})
            assert response.status == (200 if installed else 404)
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Access-Control-Allow-Origin"] == "https://site.test"
            if installed:
                assert response.content_type == "application/javascript"
                assert await response.text() == module.read_text()
            denied = await http.get("/lighttime.js", headers={"Origin": "https://denied.test"})
            assert denied.status == 403

    asyncio.run(scenario())
