"""Effect contracts and lifecycle over real ephemeral HTTP with an in-memory lamp."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

import pytest
from aiohttp import ClientSession, web

import lighttime.bridge as bridge_module
import lighttime.cli as cli_module
from lighttime import BridgeClient, TransportError
from lighttime.bridge import Bridge, MockTransport
from lighttime.client import LightState
from lighttime.effects import EffectConfig
from lighttime.protocol import Command, Frame, control_value

TOKEN = "offline-effects-only"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
STATUS_KEYS = {"running", "id", "pattern", "startedAt", "endsAt", "progress", "lastError"}


async def eventually(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


class Rig:
    def __init__(self):
        self.transports = []
        self.writes = []
        self.stall_next = False
        self.ignore_cancel = False
        self.ignore_writes = False
        self.error = None
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    def transport_type(self):
        rig = self

        class Lamp(MockTransport):
            closed = False

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                rig.transports.append(self)

            async def disconnect(self):
                self.closed = True
                await super().disconnect()

            async def _write(self, data):
                frame = Frame.decode(data)
                if frame.command in (Command.BRIGHTNESS, Command.CCT) and frame.payload[2]:
                    if rig.stall_next:
                        rig.stall_next = False
                        rig.entered.set()
                        try:
                            await rig.release.wait()
                        except asyncio.CancelledError:
                            rig.cancelled.set()
                            if not rig.ignore_cancel:
                                raise
                            await rig.release.wait()
                    assert not self.closed and self.connected, "write after client cleanup"
                    rig.writes.append(
                        (asyncio.get_running_loop().time(), frame.command, control_value(frame, 0))
                    )
                    if rig.error is not None:
                        raise rig.error
                    if rig.ignore_writes:
                        return
                await super()._write(data)

        return Lamp


@asynccontextmanager
async def server(monkeypatch, *, connected=True, auth_required=True):
    rig = Rig()
    monkeypatch.setattr(bridge_module, "MockTransport", rig.transport_type())
    bridge = Bridge(TOKEN if auth_required else "", auth_required=auth_required, allow_mock=True)
    runner = web.AppRunner(bridge.app, shutdown_timeout=0.03)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    try:
        async with BridgeClient(url, token=TOKEN if auth_required else None) as sdk:
            if connected:
                await sdk.connect("offline", "mock")
            async with ClientSession(headers=HEADERS if auth_required else {}) as http:
                yield rig, bridge, runner, url, sdk, http
    finally:
        rig.release.set()
        rig.ignore_writes = False
        rig.error = None
        await runner.cleanup()


def values(rig, command):
    return [value for _, field, value in rig.writes if field == command]


def assert_rate(rig):
    for command in (Command.BRIGHTNESS, Command.CCT):
        stamps = [stamp for stamp, field, _ in rig.writes if field == command]
        assert all(b - a >= 0.195 for a, b in zip(stamps, stamps[1:], strict=False))


def test_idle_sdk_auth_origin_and_disconnected_start(monkeypatch):
    async def scenario():
        async with server(monkeypatch, connected=False) as (rig, bridge, _, url, sdk, http):
            idle = await sdk.effect()
            assert idle == dict.fromkeys(STATUS_KEYS, None) | {"running": False}
            assert await sdk.stop_effect() == idle
            for path in ("/v1/effects", "/v1/effects/start", "/v1/effects/stop"):
                method = "GET" if path == "/v1/effects" else "POST"
                response = await http.request(method, url + path, headers={"Authorization": ""})
                assert response.status == 401
                denied = await http.request(
                    method, url + path, headers={"Origin": "https://denied.test"}
                )
                assert denied.status == 403
            response = await http.post(url + "/v1/effects/start", json={"pattern": "blink"})
            assert response.status == 503
            assert (await response.json())["error"]["code"] == "device_unavailable"
            assert not rig.writes and bridge.effects is not None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"pattern": "unknown"},
        {"pattern": None},
        {"pattern": []},
        {"unknown": 1},
        {"duration": 0},
        {"duration": 3601},
        {"duration": 10**1000},
        {"duration": True},
        {"duration": "1"},
        {"period": 0},
        {"period": 61},
        {"period": None},
        {"period": False},
        {"minBrightness": -1},
        {"minBrightness": 10**1000},
        {"minBrightness": 1.5},
        {"minBrightness": None},
        {"maxBrightness": 101},
        {"maxBrightness": 10**1000},
        {"maxBrightness": False},
        {"maxBrightness": None},
        {"minBrightness": 20, "maxBrightness": 10},
        {"minBrightness": 6},  # Default maximum is verified baseline 5.
        {"cct": 2600},
        {"cct": 6501},
        {"cct": 4550},
        {"cct": None},
        {"cctEnd": 2701},
        {"cctEnd": True},
        {"restoreOnComplete": 1},
        {"restoreOnComplete": None},
        {"pattern": "fade-in", "duration": None},
        {"pattern": "fade-out", "duration": None},
    ],
)
def test_invalid_config_has_no_output_or_runner(monkeypatch, config):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, _, url, _, http):
            # An empty object must still test the missing required pattern.
            body = {"pattern": "blink", **config} if config else config
            response = await http.post(url + "/v1/effects/start", json=body)
            assert response.status == 400
            assert (await response.json())["error"]["code"] == "invalid_request"
            assert not rig.writes and not bridge.effects.status()["running"]

    asyncio.run(scenario())


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999", "true", "null"])
def test_nonfinite_and_invalid_duration_json_rejected(monkeypatch, literal):
    async def scenario():
        async with server(monkeypatch) as (rig, _, _, url, _, http):
            response = await http.post(
                url + "/v1/effects/start",
                data='{"pattern":"fade-in","duration":' + literal + "}",
            )
            assert response.status == 400
            assert not rig.writes

    asyncio.run(scenario())


@pytest.mark.parametrize("data", [{"restore": None}, {"restore": 1}, {"extra": False}, []])
def test_stop_validation_preserves_running_effect(monkeypatch, data):
    async def scenario():
        async with server(monkeypatch) as (rig, _, _, url, sdk, http):
            started = await sdk.start_effect("blink", duration=None, maxBrightness=9)
            await eventually(lambda: bool(rig.writes))
            response = await http.post(url + "/v1/effects/stop", json=data)
            assert response.status == 400
            assert (await sdk.effect())["running"]
            response = await http.post(
                url + "/v1/effects/start", json={"pattern": "blink", "minBrightness": 20}
            )
            assert response.status == 400
            assert (await sdk.effect())["id"] == started["id"]
            assert (await sdk.effect())["running"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "pattern,times,expected",
    [
        ("blink", [0, 0.49, 0.5, 1], [(10, 2700), (10, 2700), (0, 2700), (10, 2700)]),
        (
            "breathe",
            [0, 0.25, 0.5, 0.75, 1],
            [(0, 2700), (5, 2700), (10, 2700), (5, 2700), (0, 2700)],
        ),
        ("fade-in", [0, 0.25, 1, 3], [(0, 2700), (2, 2700), (10, 2700), (10, 2700)]),
        ("fade-out", [0, 0.5, 1, 3], [(10, 2700), (5, 2700), (0, 2700), (0, 2700)]),
        (
            "cct-sweep",
            [0, 0.25, 0.5, 0.75, 1],
            [(10, 2700), (10, 4600), (10, 6500), (10, 4600), (10, 2700)],
        ),
    ],
)
def test_exact_waveform_and_hardware_quantization(pattern, times, expected):
    config = EffectConfig.parse(
        {"pattern": pattern, "duration": 1, "period": 1, "maxBrightness": 10, "cct": 2700}
    ).resolve(LightState(connected=True, brightness=5, cct=5000))
    assert [config.target(t) for t in times] == expected


@pytest.mark.parametrize(
    "pattern,restore", [("fade-in", False), ("fade-out", False), ("fade-in", True)]
)
def test_finite_fades_exact_endpoint_rate_and_restore(monkeypatch, pattern, restore):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, _, _, sdk, _):
            started = await sdk.start_effect(
                pattern,
                duration=1.05,
                minBrightness=1,
                maxBrightness=9,
                cct=3000,
                restoreOnComplete=restore,
            )
            assert set(started) == STATUS_KEYS and started["running"]
            assert (
                abs(
                    (
                        datetime.fromisoformat(started["endsAt"])
                        - datetime.fromisoformat(started["startedAt"])
                    ).total_seconds()
                    - 1.05
                )
                < 0.00001
            )
            await eventually(lambda: not bridge.effects.status()["running"])
            status = await sdk.effect()
            assert status["progress"] == 1 and status["lastError"] is None
            assert status["id"] == started["id"]
            state = await sdk.refresh()
            endpoint = 9 if pattern == "fade-in" else 1
            assert (state["brightness"], state["cct"]) == (
                (5, 5000) if restore else (endpoint, 3000)
            )
            frames = values(rig, Command.BRIGHTNESS)
            assert len(frames) >= 4
            fade_frames = frames[:-1] if restore else frames
            assert fade_frames[0] == (1 if pattern == "fade-in" else 9)
            assert fade_frames[-1] == endpoint
            assert fade_frames == sorted(fade_frames, reverse=pattern == "fade-out")
            assert_rate(rig)
            count = len(rig.writes)
            await sdk.stop_effect()  # Already completed is idempotent, even with restore=True.
            assert len(rig.writes) == count

    asyncio.run(scenario())


@pytest.mark.parametrize("pattern", ["blink", "breathe", "cct-sweep"])
def test_cyclic_frames_quantized_skip_duplicates_and_finite_finish(monkeypatch, pattern):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, _, _, sdk, _):
            await sdk.start_effect(
                pattern, duration=1, period=1, maxBrightness=9, cct=2700, restoreOnComplete=False
            )
            await eventually(lambda: not bridge.effects.status()["running"])
            assert (await sdk.effect())["progress"] == 1
            bright = values(rig, Command.BRIGHTNESS)
            if pattern == "blink":
                assert bright == [9, 0, 9]
            elif pattern == "breathe":
                assert bright[0] == bright[-1] == 0
                assert any(0 < value < 9 for value in bright)
                assert len(bright) < 7
                assert all(a != b for a, b in zip(bright, bright[1:], strict=False))
            else:
                temperatures = values(rig, Command.CCT)
                assert temperatures[0] == temperatures[-1] == 2700
                assert any(2700 < value < 6500 for value in temperatures)
                assert bright == [9]
                assert all(value % 100 == 0 for value in temperatures)
            assert_rate(rig)
            assert all(float(value).is_integer() for value in bright)

    asyncio.run(scenario())


@pytest.mark.parametrize("restore", [True, False])
def test_explicit_stop_and_unbounded_status(monkeypatch, restore):
    async def scenario():
        async with server(monkeypatch) as (rig, _, _, _, sdk, _):
            started = await sdk.start_effect("blink", duration=None, maxBrightness=9, cct=2700)
            assert started["endsAt"] is None and started["progress"] is None
            await eventually(lambda: values(rig, Command.BRIGHTNESS) == [9])
            stopped = await sdk.stop_effect(restore=restore)
            assert not stopped["running"] and stopped["lastError"] is None
            state = await sdk.refresh()
            assert (state["brightness"], state["cct"]) == ((5, 5000) if restore else (9, 2700))
            count = len(rig.writes)
            assert await sdk.stop_effect() == stopped
            await asyncio.sleep(0.25)
            assert len(rig.writes) == count
            assert_rate(rig)

    asyncio.run(scenario())


def test_baseline_freshness_zero_fallback_and_default_restore(monkeypatch):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, _, _, sdk, _):
            lamp = rig.transports[0]
            # Simulate an external device change after the bridge's last read.
            lamp.brightness, lamp.cct = 0, 4300
            await sdk.start_effect("blink", duration=None)
            await eventually(lambda: lamp.brightness == 5)
            assert lamp.cct == 4300
            assert not values(rig, Command.CCT)
            await sdk.stop_effect()
            assert (lamp.brightness, lamp.cct) == (0, 4300)
            await sdk.start_effect("blink", duration=1, period=1, maxBrightness=9)
            await eventually(lambda: not bridge.effects.status()["running"])
            assert (lamp.brightness, lamp.cct) == (0, 4300)
            assert (await sdk.effect())["progress"] == 1

    asyncio.run(scenario())


def test_replacement_uses_own_verified_baseline_and_one_runner(monkeypatch):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, _, _, sdk, _):
            first = await sdk.start_effect("blink", duration=None, maxBrightness=9, cct=2700)
            await eventually(lambda: rig.transports[0].brightness == 9)
            first_run = bridge.effects._run
            assert first_run is not None and first_run.task is not None
            second = await sdk.start_effect("breathe", duration=None, maxBrightness=7, cct=6500)
            assert second["id"] != first["id"] and first_run.task.done()
            await eventually(lambda: rig.transports[0].brightness == 0)
            assert (
                len([task for task in bridge._device_tasks if task.get_name() == "Bridge effect"])
                == 1
            )
            await sdk.stop_effect()
            state = await sdk.refresh()
            assert (state["brightness"], state["cct"]) == (9, 2700)
            assert 5 not in values(rig, Command.BRIGHTNESS)
            assert 5000 not in values(rig, Command.CCT)
            assert_rate(rig)

    asyncio.run(scenario())


def test_manual_write_drains_inflight_frame_without_restoring(monkeypatch):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, _, _, sdk, _):
            rig.stall_next = True
            await sdk.start_effect("blink", duration=None, maxBrightness=9, cct=2700)
            await eventually(rig.entered.is_set)
            state = await sdk.set(brightness=17, cct=3300)
            assert rig.cancelled.is_set()
            assert (state["brightness"], state["cct"]) == (17, 3300)
            assert not (await sdk.effect())["running"]
            assert not bridge.lock.locked() and not bridge.effects.control_lock.locked()
            assert values(rig, Command.BRIGHTNESS) == [17]
            assert values(rig, Command.CCT) == [3300]
            count = len(rig.writes)
            rig.release.set()
            await asyncio.sleep(0.25)
            assert len(rig.writes) == count

    asyncio.run(scenario())


def test_cancelled_frame_queued_on_bridge_lock_never_replays(monkeypatch):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, _, _, sdk, _):
            await sdk.start_effect("breathe", duration=None, maxBrightness=20)
            await eventually(lambda: bool(rig.writes))
            await bridge.lock.acquire()
            pending = None
            try:
                await asyncio.sleep(0.23)
                assert bridge.lock._waiters
                run = bridge.effects._run
                assert run is not None and run.task is not None
                pending = asyncio.create_task(sdk.set(brightness=17))
                await eventually(run.task.done)
                count = len(rig.writes)
            finally:
                bridge.lock.release()
            assert pending is not None
            assert (await pending)["brightness"] == 17
            assert values(rig, Command.BRIGHTNESS)[-1] == 17
            assert len(rig.writes) == count + 1
            await asyncio.sleep(0.25)
            assert len(rig.writes) == count + 1

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["disconnect", "connect"])
def test_device_replacement_or_disconnect_drains_effect_without_restore(monkeypatch, operation):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, _, _, sdk, _):
            rig.stall_next = True
            await sdk.start_effect("blink", duration=None, maxBrightness=9, cct=2700)
            await eventually(rig.entered.is_set)
            old = rig.transports[0]
            if operation == "disconnect":
                assert not (await sdk.disconnect())["connected"]
            else:
                assert (await sdk.connect("replacement", "mock"))["connected"]
                assert bridge.client is not None and bridge.client.transport is rig.transports[1]
            assert rig.cancelled.is_set() and old.closed
            assert not (await sdk.effect())["running"] and not rig.writes
            rig.release.set()
            await asyncio.sleep(0.25)
            assert not rig.writes

    asyncio.run(scenario())


def test_shutdown_cancels_and_drains_runner_before_client_cleanup(monkeypatch):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, runner, _, sdk, _):
            rig.stall_next = True
            await sdk.start_effect("blink", duration=None, maxBrightness=9, cct=2700)
            await eventually(rig.entered.is_set)
            run = bridge.effects._run
            assert run is not None and run.task in bridge._device_tasks
            started = asyncio.get_running_loop().time()
            await runner.cleanup()
            assert asyncio.get_running_loop().time() - started < 0.4
            assert rig.cancelled.is_set() and rig.transports[0].closed
            assert bridge.client is None and not bridge._device_tasks
            assert not bridge.lock.locked() and not bridge.effects.status()["running"]
            count = len(rig.writes)
            rig.release.set()
            await asyncio.sleep(0.25)
            assert len(rig.writes) == count == 0

    asyncio.run(scenario())


def test_shutdown_retains_resistant_effect_and_rejects_all_effect_admission(monkeypatch):
    async def scenario():
        monkeypatch.setattr(bridge_module, "_OPERATION_DRAIN_TIMEOUT", 0.06)
        async with server(monkeypatch) as (rig, bridge, runner, url, sdk, http):
            rig.stall_next = rig.ignore_cancel = True
            await sdk.start_effect("blink", duration=None, maxBrightness=9, cct=2700)
            await eventually(rig.entered.is_set)
            client = bridge.client
            await bridge.begin_shutdown(bridge.app)
            for method, path, body in [
                ("GET", "/v1/effects", None),
                ("POST", "/v1/effects/start", {"pattern": "blink"}),
                ("POST", "/v1/effects/stop", {}),
            ]:
                response = await http.request(method, url + path, json=body)
                assert response.status == 503
                assert (await response.json())["error"]["code"] == "bridge_stopping"
            with pytest.raises(
                TransportError, match="operations still pending; cleanup unresolved"
            ):
                await bridge.shutdown(bridge.app)
            assert bridge.client is client and bridge._cleanup_task is None
            assert bridge.lock.locked() and not rig.transports[0].closed
            owned = tuple(bridge._device_tasks)
            assert owned and rig.cancelled.is_set()
            rig.release.set()
            await asyncio.gather(*owned, return_exceptions=True)
            await runner.cleanup()
            assert bridge.client is None and not bridge._device_tasks
            assert rig.transports[0].closed and not bridge.effects.status()["running"]
            count = len(rig.writes)
            await asyncio.sleep(0.25)
            assert len(rig.writes) == count  # No additional frame after the in-flight write.

    asyncio.run(scenario())


def test_shutdown_interrupts_stop_restoration_without_new_writes(monkeypatch):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, runner, url, sdk, http):
            await sdk.start_effect("blink", duration=None, maxBrightness=9, cct=2700)
            await eventually(lambda: rig.transports[0].brightness == 9)
            pending = asyncio.create_task(http.post(url + "/v1/effects/stop", json={}))
            await eventually(lambda: not bridge.effects.status()["running"])
            count = len(rig.writes)
            await runner.cleanup()
            response = await pending
            assert response.status == 503
            assert (await response.json())["error"]["code"] == "bridge_stopping"
            assert len(rig.writes) == count == 2
            assert rig.transports[0].closed and not bridge._device_tasks

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["readback", "transport"])
def test_runner_error_stops_and_persists_without_claiming_control(monkeypatch, failure):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, _, _, sdk, _):
            assert bridge.client is not None
            bridge.client.retries = 0
            if failure == "readback":
                rig.ignore_writes = True
            else:
                rig.error = TransportError("offline injected write failure")
            started = await sdk.start_effect("blink", duration=None, maxBrightness=9)
            await eventually(lambda: not bridge.effects.status()["running"])
            status = await sdk.effect()
            assert status["id"] == started["id"] and status["lastError"]
            if failure == "readback":
                assert "readback differs" in status["lastError"]
                assert bridge.state()["brightness"] == 5
            else:
                assert "injected write failure" in status["lastError"]
                assert not bridge.state()["connected"] and bridge.state()["brightness"] is None
            count = len(rig.writes)
            assert (await sdk.stop_effect())["lastError"] == status["lastError"]
            await asyncio.sleep(0.25)
            assert len(rig.writes) == count
            if failure == "readback":
                rig.ignore_writes = False
                assert (await sdk.start_effect("blink", duration=None))["lastError"] is None
                await sdk.stop_effect(restore=False)

    asyncio.run(scenario())


def test_restore_failure_is_http_error_and_remains_visible(monkeypatch):
    async def scenario():
        async with server(monkeypatch) as (rig, _, _, url, sdk, http):
            await sdk.start_effect("blink", duration=None, maxBrightness=9, cct=2700)
            await eventually(lambda: rig.transports[0].brightness == 9)
            rig.ignore_writes = True
            response = await http.post(url + "/v1/effects/stop", json={})
            assert response.status == 502
            assert (await response.json())["error"]["code"] == "verification_failed"
            status = await sdk.effect()
            assert not status["running"] and "readback differs" in status["lastError"]
            assert (rig.transports[0].brightness, rig.transports[0].cct) == (9, 2700)
            count = len(rig.writes)
            await asyncio.sleep(0.25)
            assert len(rig.writes) == count

    asyncio.run(scenario())


def test_state_sse_reports_verified_effect_output(monkeypatch):
    async def scenario():
        async with server(monkeypatch) as (_, bridge, _, url, sdk, http):
            response = await http.get(url + "/v1/events")
            try:
                await response.content.readuntil(b"\n\n")
                await sdk.start_effect("blink", duration=None, maxBrightness=9, cct=2700)
                async with asyncio.timeout(1):
                    while True:
                        event = await response.content.readuntil(b"\n\n")
                        if b'"brightness": 9.0' in event:
                            assert b'"cct": 2700' in event and bridge.state()["brightness"] == 9
                            break
            finally:
                response.close()
            await sdk.stop_effect(restore=False)

    asyncio.run(scenario())


@pytest.mark.parametrize("field", [Command.BRIGHTNESS, Command.CCT])
def test_restore_after_setter_applied_before_cancelled_ack(monkeypatch, field):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, _, _, sdk, _):
            lamp = rig.transports[0]
            original_write = lamp._write
            applied, cancelled = asyncio.Event(), asyncio.Event()

            async def apply_before_ack(data):
                frame = Frame.decode(data)
                await original_write(data)
                if frame.command == field and frame.payload[2] and not applied.is_set():
                    # The absolute setter has already reached physical output,
                    # but no ACK/readback has allowed LightClient to cache it.
                    applied.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cancelled.set()
                        raise

            monkeypatch.setattr(lamp, "_write", apply_before_ack)
            config = {"cct": 2700} if field == Command.CCT else {}
            await sdk.start_effect("blink", duration=None, maxBrightness=9, **config)
            await eventually(applied.is_set)
            assert bridge.state()["brightness"] == 5 and bridge.state()["cct"] == 5000
            assert (lamp.brightness, lamp.cct) != (5, 5000)
            await sdk.stop_effect()
            assert cancelled.is_set()
            assert (lamp.brightness, lamp.cct) == (5, 5000)
            assert (bridge.state()["brightness"], bridge.state()["cct"]) == (5, 5000)
            # Both setters run, even the field whose cached value matched.
            assert values(rig, Command.BRIGHTNESS)[-1] == 5
            assert values(rig, Command.CCT)[-1] == 5000
            assert not (await sdk.effect())["running"]
            assert (await sdk.effect())["lastError"] is None
            assert_rate(rig)

    asyncio.run(scenario())


@pytest.mark.parametrize("token", [None, ""])
def test_personal_mode_no_token_controls_effects_health_and_sse(monkeypatch, token):
    async def scenario():
        async with server(monkeypatch, auth_required=False) as (rig, bridge, _, url, _, http):
            seen_authorization = []
            original_body = bridge.body

            async def record_body(request):
                seen_authorization.append(request.headers.get("Authorization"))
                return await original_body(request)

            monkeypatch.setattr(bridge, "body", record_body)
            health = await (await http.get(url + "/v1/health")).json()
            assert health == {
                "name": "lighttime",
                "version": "0.1.0",
                "authRequired": False,
                "defaultAddress": None,
            }
            # Set only metadata, without starting a hardware connection.
            bridge.initial_address = "OFFLINE-DEFAULT"
            assert (await (await http.get(url + "/v1/health")).json())[
                "defaultAddress"
            ] == "OFFLINE-DEFAULT"
            async with BridgeClient(url, token=token) as sdk:
                assert (await sdk.refresh())["connected"]
                assert (await sdk.set(brightness=7, cct=4300))["brightness"] == 7
                await sdk.start_effect("blink", duration=None, maxBrightness=9)
                await eventually(lambda: rig.transports[0].brightness == 9)
                assert (await sdk.effect())["running"]
                assert not (await sdk.stop_effect())["running"]
                assert (await sdk.refresh())["brightness"] == 7
                stream = sdk.events()
                try:
                    assert (await anext(stream))["brightness"] == 7
                finally:
                    await stream.aclose()
            assert seen_authorization and set(seen_authorization) == {None}
            # Origin enforcement still applies even when bearer auth is disabled.
            denied = await http.patch(
                url + "/v1/state",
                json={"brightness": 20},
                headers={"Origin": "https://denied.test"},
            )
            assert denied.status == 403 and rig.transports[0].brightness == 7
            bridge.allow_origins.add("https://personal.tailnet.test")
            allowed = await http.get(
                url + "/v1/effects", headers={"Origin": "https://personal.tailnet.test"}
            )
            assert allowed.status == 200
            assert allowed.headers["Access-Control-Allow-Origin"] == "https://personal.tailnet.test"

    asyncio.run(scenario())


def test_secure_default_health_and_tokenless_sdk_rejection(monkeypatch):
    assert Bridge(TOKEN).auth_required is True
    with pytest.raises(ValueError, match="requires a token"):
        Bridge()

    async def scenario():
        async with server(monkeypatch) as (_, bridge, _, url, _, http):
            bridge.initial_address = "PRIVATE-DEFAULT"
            health = await (await http.get(url + "/v1/health")).json()
            assert health == {"name": "lighttime", "version": "0.1.0", "authRequired": True}
            async with BridgeClient(url) as sdk:
                with pytest.raises(TransportError, match="bearer token"):
                    await sdk.effect()
                with pytest.raises(TransportError, match="bearer token"):
                    await sdk.set(brightness=7)

    asyncio.run(scenario())


@pytest.mark.parametrize("no_auth", [True, False])
def test_cli_no_auth_skips_token_creation_and_print(monkeypatch, capsys, tmp_path, no_auth):
    token_file = tmp_path / "never-create.token"
    arguments = ["lighttime", "serve", "--address", "OFFLINE", "--token-file", str(token_file)]
    if no_auth:
        arguments.append("--no-auth")
    monkeypatch.setattr("sys.argv", arguments)
    calls, applications = [], []

    def token(path):
        calls.append(path)
        return TOKEN

    def run_app(app, **kwargs):
        # Inspect bound lifecycle owner's options without opening any listener.
        applications.append(app.on_startup[-1].__self__)
        assert kwargs["host"] == "127.0.0.1" and kwargs["shutdown_timeout"] == 5

    monkeypatch.setattr(cli_module, "ensure_token", token)
    monkeypatch.setattr(cli_module.web, "run_app", run_app)
    cli_module.main()
    bridge = applications[0]
    assert bridge.auth_required is not no_auth
    assert bridge.initial_address == "OFFLINE"
    assert calls == ([] if no_auth else [token_file])
    assert not token_file.exists()
    output = capsys.readouterr().out
    assert ("Bridge token file:" in output) is not no_auth
    if no_auth:
        assert str(token_file) not in output and bridge.token == ""


def test_next_effect_refreshes_after_unrestored_interrupted_setter(monkeypatch):
    async def scenario():
        async with server(monkeypatch) as (rig, bridge, _, _, sdk, _):
            lamp = rig.transports[0]
            original_write = lamp._write
            applied = asyncio.Event()

            async def apply_before_ack(data):
                frame = Frame.decode(data)
                await original_write(data)
                if (
                    frame.command == Command.BRIGHTNESS
                    and frame.payload[2]
                    and not applied.is_set()
                ):
                    applied.set()
                    await asyncio.Event().wait()

            monkeypatch.setattr(lamp, "_write", apply_before_ack)
            await sdk.start_effect("blink", duration=None, maxBrightness=9)
            await eventually(applied.is_set)
            assert lamp.brightness == 9 and bridge.state()["brightness"] == 5
            await sdk.stop_effect(restore=False)
            assert lamp.brightness == 9 and bridge.state()["brightness"] == 5
            # A target equal to the stale cache must not be skipped on a later
            # run: start refreshes actual baseline before duplicate comparison.
            await sdk.start_effect("blink", duration=None, maxBrightness=5)
            await eventually(lambda: lamp.brightness == 5)
            assert values(rig, Command.BRIGHTNESS) == [9, 5]
            await sdk.stop_effect()
            assert lamp.brightness == 9  # The new baseline came from hardware.
            assert values(rig, Command.BRIGHTNESS) == [9, 5, 9]

    asyncio.run(scenario())


def test_noop_frames_skip_writes_but_stop_still_sets_and_verifies_baseline(monkeypatch):
    async def scenario():
        async with server(monkeypatch) as (rig, _, _, _, sdk, _):
            lamp = rig.transports[0]
            original_write = lamp._write
            reads = []

            async def record_reads(data):
                frame = Frame.decode(data)
                if frame.command in (Command.BRIGHTNESS, Command.CCT) and not frame.payload[2]:
                    reads.append(frame.command)
                await original_write(data)

            monkeypatch.setattr(lamp, "_write", record_reads)
            await sdk.start_effect("blink", duration=None, minBrightness=5, maxBrightness=5)
            await asyncio.sleep(0.25)
            assert not rig.writes  # Multiple identical, confirmed targets.
            reads.clear()
            await sdk.stop_effect()
            assert values(rig, Command.BRIGHTNESS) == [5]
            assert values(rig, Command.CCT) == [5000]
            assert reads == [Command.BRIGHTNESS, Command.CCT]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "boundary,automatic",
    [
        ("cct-set", False),
        ("brightness-set", False),
        ("brightness-read", False),
        ("cct-read", False),
        ("cct-set", True),
    ],
)
def test_shutdown_mid_restoration_never_submits_next_setter_or_read(
    monkeypatch, boundary, automatic
):
    async def scenario():
        monkeypatch.setattr(bridge_module, "_OPERATION_DRAIN_TIMEOUT", 0.06)
        async with server(monkeypatch) as (rig, bridge, runner, url, sdk, http):
            await sdk.start_effect(
                "blink", duration=1 if automatic else None, maxBrightness=9, cct=2700
            )
            await eventually(lambda: rig.transports[0].brightness == 9)
            lamp = rig.transports[0]
            original_write = lamp._write
            entries = []
            entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
            target = {
                "cct-set": (Command.CCT, 1),
                "brightness-set": (Command.BRIGHTNESS, 1),
                "brightness-read": (Command.BRIGHTNESS, 0),
                "cct-read": (Command.CCT, 0),
            }[boundary]

            async def resistant_write(data):
                frame = Frame.decode(data)
                if frame.command in (Command.BRIGHTNESS, Command.CCT):
                    entry = (frame.command, frame.payload[2])
                    entries.append((*entry, bridge._stopping))
                    if entry == target and not entered.is_set():
                        entered.set()
                        try:
                            await release.wait()
                        except asyncio.CancelledError:
                            cancelled.set()
                            await release.wait()
                await original_write(data)

            monkeypatch.setattr(lamp, "_write", resistant_write)
            pending = None
            try:
                if not automatic:
                    pending = asyncio.create_task(http.post(url + "/v1/effects/stop", json={}))
                await eventually(entered.is_set)
                client = bridge.client
                assert client is not None
                cached = client.state.updatedAt
                await bridge.begin_shutdown(bridge.app)
                await eventually(cancelled.is_set)
                if pending is not None:
                    response = await pending
                    assert response.status == 503
                    assert (await response.json())["error"]["code"] == "bridge_stopping"
                owned = tuple(bridge._device_tasks)
                assert owned and bridge.lock.locked() and client._lock.locked()
                with pytest.raises(TransportError, match="cleanup unresolved"):
                    await bridge.shutdown(bridge.app)
                assert bridge.client is client and bridge._cleanup_task is None and not lamp.closed
                release.set()
                await asyncio.gather(*owned, return_exceptions=True)
                assert not any(stopping for _, _, stopping in entries), entries
                assert client.state.updatedAt == cached  # No post-cancel verification commit.
                await runner.cleanup()
                assert bridge.client is None and not bridge._device_tasks and lamp.closed
                assert not bridge.lock.locked() and not bridge.effects.control_lock.locked()
                count = len(entries)
                await asyncio.sleep(0.02)
                assert len(entries) == count
            finally:
                release.set()
                if pending is not None:
                    await pending

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "patch",
    [
        {"brightness": 101},
        {"brightness": -1},
        {"brightness": 5.5},
        {"brightness": True},
        {"brightness": "5"},
        {"brightness": []},
        {"brightness": 10**1000},
        {"cct": 2600},
        {"cct": 6501},
        {"cct": 4550},
        {"cct": 4300.0},
        {"cct": False},
        {"cct": "4300"},
        {"power": "off"},
        {"power": 0},
        {"power": False, "brightness": 7},
        {"power": True, "brightness": 0},
        {"brightness": 7, "cct": 4550},
        {"cct": 3300, "power": "on"},
        {"brightness": None},
        {},
        {"unknown": 5},
    ],
)
def test_rejected_manual_patch_leaves_effect_and_output_intact(monkeypatch, patch):
    async def scenario():
        async with server(monkeypatch) as (rig, _, _, url, sdk, http):
            started = await sdk.start_effect(
                "blink", duration=None, period=60, maxBrightness=9, cct=2700
            )
            await eventually(lambda: rig.transports[0].brightness == 9)
            writes = len(rig.writes)
            response = await http.patch(url + "/v1/state", json=patch)
            assert response.status == 400
            assert (await response.json())["error"]["code"] == "invalid_request"
            status = await sdk.effect()
            assert status["id"] == started["id"] and status["running"]
            assert status["lastError"] is None and len(rig.writes) == writes
            with pytest.raises(TransportError):
                await sdk.set(**patch)
            assert (await sdk.effect())["running"] and len(rig.writes) == writes
            assert (rig.transports[0].brightness, rig.transports[0].cct) == (9, 2700)
            state = await sdk.set(brightness=7, cct=3300)
            assert not (await sdk.effect())["running"]
            assert (state["brightness"], state["cct"]) == (7, 3300)
            assert values(rig, Command.BRIGHTNESS) == [9, 7]
            assert values(rig, Command.CCT) == [2700, 3300]

    asyncio.run(scenario())
