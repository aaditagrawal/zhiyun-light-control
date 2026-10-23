import asyncio
import math
import struct

import pytest
from aiohttp.test_utils import TestClient, TestServer

from lighttime import LightClient, RequestTimeout
from lighttime.bridge import Bridge, MockTransport
from lighttime.protocol import Command, Frame, FrameParser, ProtocolError, control_value
from lighttime.transport import Transport

GOLDEN = [
    ("243c0600000101000320d4ad", Command.INFO, 1, b""),
    ("243c0800010002000520000010f6", Command.DEVICE_ID, 2, b"\0\0"),
    (
        "243c0d000100030001100000000000a040b4be",
        Command.BRIGHTNESS,
        3,
        b"\0\0\0" + struct.pack("<f", 5),
    ),
    ("243c0b000100040002100000008813a28e", Command.CCT, 4, b"\0\0\0" + struct.pack("<H", 5000)),
]


@pytest.mark.parametrize("raw,command,sequence,payload", GOLDEN)
def test_actual_g60_captures(raw, command, sequence, payload):
    frame = Frame.decode(bytes.fromhex(raw))
    assert (frame.command, frame.sequence, frame.payload) == (command, sequence, payload)
    assert frame.encode().hex() == raw


def test_parser_every_boundary_noise_corruption_and_combined_frames():
    first = bytes.fromhex(GOLDEN[2][0])
    second = bytes.fromhex(GOLDEN[3][0])
    corrupt = bytearray(first)
    corrupt[-1] ^= 1
    for split in range(len(first) + 1):
        parser = FrameParser()
        frames = parser.feed(b"noise" + first[:split])
        frames += parser.feed(first[split:] + corrupt + b"$<\xff\xff" + second)
        assert [f.command for f in frames] == [Command.BRIGHTNESS, Command.CCT]
        assert parser.invalid_frames >= 2
        assert not parser.buffer


def test_controls_reject_wrong_device_truncated_and_nonfinite():
    frame = Frame.decode(bytes.fromhex(GOLDEN[2][0]))
    with pytest.raises(ProtocolError, match="another device"):
        control_value(frame, 10)
    with pytest.raises(ProtocolError):
        control_value(Frame(Command.BRIGHTNESS, 1, b"\0"), 0)
    with pytest.raises(ValueError):
        control_value(Frame(Command.BRIGHTNESS, 1, b"\0\0\0" + struct.pack("<f", math.nan)), 0)


def test_client_verified_control_and_soft_power_restore():
    async def scenario():
        async with LightClient(transport=MockTransport()) as client:
            assert client.state.brightness == 5
            state = await client.set(brightness=12, cct=2700)
            assert (state.brightness, state.cct) == (12, 2700)
            assert (await client.set(power=False)).brightness == 0
            assert (await client.set(power=True)).brightness == 12
            with pytest.raises(ValueError):
                await client.set(power=False, brightness=30)
            with pytest.raises(ValueError):
                await client.set(brightness=math.nan)
            with pytest.raises(ValueError):
                await client.set(cct=6501)
            with pytest.raises(ValueError):
                await client.set(cct=4550)
            with pytest.raises(ValueError):
                await client.set(brightness=5.5)
        assert not client.state.connected

    asyncio.run(scenario())


def test_write_readback_mismatch_is_failure():
    class IgnoresWrites(MockTransport):
        async def send(self, command: int, payload: bytes = b""):
            pass

    async def scenario():
        async with LightClient(transport=IgnoresWrites()) as client:
            with pytest.raises(ProtocolError, match="readback differs"):
                await client.set(brightness=15)

    asyncio.run(scenario())


def test_client_reconnects_once_on_a_lost_link():
    class DropsOnce(MockTransport):
        drop_next = False
        connections = 0

        async def connect(self):
            self.connections += 1
            await super().connect()

        async def request(self, command, payload=b""):
            if self.drop_next:
                self.drop_next = False
                raise RequestTimeout("Injected disconnect")
            return await super().request(command, payload)

    async def scenario():
        transport = DropsOnce()
        async with LightClient(transport=transport) as client:
            transport.drop_next = True
            assert (await client.refresh()).brightness == 5
            assert transport.connections == 2

    asyncio.run(scenario())


def test_transport_ignores_unrelated_sequence_and_times_out():
    class WrongReplies(Transport):
        async def connect(self):
            self.connected = True

        async def _write(self, data):
            frame = Frame.decode(data)
            self._receive(Frame(frame.command, frame.sequence + 1, b"wrong", 1).encode())
            self._receive(Frame(frame.command + 1, frame.sequence, b"wrong", 1).encode())

    async def scenario():
        transport = WrongReplies("test", timeout=0.02)
        await transport.connect()
        with pytest.raises(RequestTimeout):
            await transport.request(Command.INFO)
        assert not transport._pending
        await transport.disconnect()

    asyncio.run(scenario())


def test_bridge_auth_validation_cors_and_actual_state():
    async def scenario():
        bridge = Bridge(
            "test-token-for-tests", allow_mock=True, allow_origins=["https://example.test"]
        )
        async with TestClient(TestServer(bridge.app)) as client:
            assert (await client.get("/v1/health")).status == 200
            assert (await client.get("/v1/state")).status == 401
            headers = {"Authorization": "Bearer test-token-for-tests"}
            assert (await client.get("/v1/state", headers=headers)).status == 200
            response = await client.post(
                "/v1/connect", headers=headers, json={"transport": "mock", "address": "test"}
            )
            assert response.status == 200
            assert (await response.json())["device"]["serial"] == "MOCK-NOT-HARDWARE"
            response = await client.patch(
                "/v1/state", headers=headers, json={"brightness": 8, "cct": 3000}
            )
            state = await response.json()
            assert (state["brightness"], state["cct"]) == (8, 3000)
            for data in [{"brightness": None}, {"cct": 1}, {"power": "off"}, {}, {"unknown": 1}]:
                assert (await client.patch("/v1/state", headers=headers, json=data)).status == 400
            assert (
                await client.patch("/v1/state", headers=headers, data='{"brightness": NaN}')
            ).status == 400
            denied = await client.get(
                "/v1/state", headers={**headers, "Origin": "https://evil.test"}
            )
            assert denied.status == 403
            allowed = await client.options("/v1/state", headers={"Origin": "https://example.test"})
            assert allowed.headers["Access-Control-Allow-Origin"] == "https://example.test"
            assert allowed.headers["Access-Control-Allow-Private-Network"] == "true"
            response = await client.get("/v1/events", headers=headers)
            chunk = await asyncio.wait_for(response.content.readuntil(b"\n\n"), 1)
            assert b'"brightness": 8.0' in chunk
            response.close()
            state = await (await client.post("/v1/disconnect", headers=headers)).json()
            assert state["connected"] is False

    asyncio.run(scenario())


def test_bridge_mock_disabled_by_default():
    async def scenario():
        bridge = Bridge("test-token-for-tests")
        async with TestClient(TestServer(bridge.app)) as client:
            response = await client.post(
                "/v1/connect",
                headers={"Authorization": "Bearer test-token-for-tests"},
                json={"transport": "mock", "address": "test"},
            )
            assert response.status == 400

    asyncio.run(scenario())
