"""USB/BLE routing and serial ownership without real hardware."""

import asyncio
import os
import struct
import threading

import pytest

from lighttime import LightClient, UsbBleTransport
from lighttime.protocol import Command, Frame, FrameParser, ProtocolError, control_payload
from lighttime.transport import Transport, TransportError, _CleanupError
from lighttime.usb_hybrid import _UsbSerial, validate_usb_setter


class SimulatedBle(Transport):
    kind = "ble"

    def __init__(self, values, serial="SIMULATED-G60"):
        super().__init__("simulated-device")
        self.values = values
        self.serial = serial
        self.requests = []

    async def connect(self):
        self.connected = True

    async def request(self, command, payload=b""):
        self.requests.append(command)
        # Let the independent pseudo-terminal firmware consume a USB write.
        await asyncio.sleep(0.002)
        if command == Command.INFO:
            value = self.serial.encode() + b"\0pl103\0"
        elif command == Command.DEVICE_ID:
            value = b"\0\0"
        else:
            key = "brightness" if command == Command.BRIGHTNESS else "cct"
            value = bytearray(control_payload(command, 0, self.values[key]))
            value[2] = 0
        return Frame(command, 1, bytes(value), message_type=1)

    async def send(self, command, payload=b""):
        raise AssertionError("An output setter was routed to Bluetooth")


class PseudoFirmware:
    def __init__(self, values):
        import pty

        self.master, self.slave = pty.openpty()
        self.path = os.ttyname(self.slave)
        os.set_blocking(self.master, False)
        self.parser = FrameParser()
        self.values = values
        self.frames = []
        asyncio.get_running_loop().add_reader(self.master, self.read)

    def read(self):
        try:
            raw = os.read(self.master, 4096)
        except BlockingIOError:
            return
        for frame in self.parser.feed(raw):
            self.frames.append(frame)
            if frame.command == Command.INFO:
                payload = b"SIMULATED-G60\0pl103\0"
            elif frame.command == Command.DEVICE_ID:
                payload = b"\0\0"
            else:
                validate_usb_setter(frame.command, frame.payload, 0)
                key = "brightness" if frame.command == Command.BRIGHTNESS else "cct"
                self.values[key] = struct.unpack_from(
                    "<f" if key == "brightness" else "<H", frame.payload, 3
                )[0]
                continue  # USB setters have no reply.
            os.write(
                self.master,
                Frame(frame.command, frame.sequence, payload, message_type=1).encode(),
            )

    def close(self):
        asyncio.get_running_loop().remove_reader(self.master)
        os.close(self.master)
        os.close(self.slave)


def test_real_serial_framing_and_independent_ble_readback():
    async def run():
        values = {"brightness": 7, "cct": 5000}
        device = PseudoFirmware(values)
        ble = SimulatedBle(values)
        hybrid = UsbBleTransport(ble.address, device.path, ble=ble, timeout=0.3)
        try:
            async with LightClient(transport=hybrid, retries=0) as light:
                assert light.state.transport == "usb+ble"
                result = await light.set(brightness=12, cct=3200)
                assert (result.brightness, result.cct) == (12, 3200)
                assert light.state.device.serial == "SIMULATED-G60"
                await light.set(power=False)
                assert values["brightness"] == 0
                await light.set(power=True)
                assert values["brightness"] == 12
                assert all(frame.message_type == 0x100 for frame in device.frames)
                commands = [frame.command for frame in device.frames]
                assert commands[:2] == [Command.INFO, Command.DEVICE_ID]
                assert commands[2:] == [
                    Command.CCT,
                    Command.BRIGHTNESS,
                    Command.BRIGHTNESS,
                    Command.BRIGHTNESS,
                ]
                assert Command.BRIGHTNESS in ble.requests and Command.CCT in ble.requests
            assert not hybrid.connected and not hybrid._usb.owned
        finally:
            await hybrid.disconnect()
            device.close()

    asyncio.run(run())


def test_identity_mismatch_closes_both_links_before_any_setter():
    async def run():
        values = {"brightness": 7, "cct": 5000}
        device = PseudoFirmware(values)
        ble = SimulatedBle(values, serial="ANOTHER-G60")
        hybrid = UsbBleTransport(ble.address, device.path, ble=ble, timeout=0.3)
        try:
            with pytest.raises(TransportError, match="not the BLE light"):
                await hybrid.connect()
            assert not hybrid.connected and not ble.connected and not hybrid._usb.owned
            assert all(
                frame.command in (Command.INFO, Command.DEVICE_ID) for frame in device.frames
            )
        finally:
            await hybrid.disconnect()
            device.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        (Command.INFO, b""),
        (0x1301, b"\0\0"),
        (Command.BRIGHTNESS, control_payload(Command.BRIGHTNESS, 0)),
        (Command.CCT, control_payload(Command.CCT, 1, 3200)),
        (Command.BRIGHTNESS, struct.pack("<HBf", 0, 1, float("nan"))),
        (Command.BRIGHTNESS, struct.pack("<HBf", 0, 1, 5.5)),
        (Command.CCT, struct.pack("<HBH", 0, 1, 4550)),
    ],
)
def test_usb_setter_allowlist_rejects_unverified_operations(command, payload):
    with pytest.raises((ValueError, ProtocolError)):
        validate_usb_setter(command, payload, 0)


def test_failed_port_close_retains_ownership_and_retries(monkeypatch):
    class Port:
        calls = 0

        def close(self):
            self.calls += 1
            if self.calls == 1:
                raise OSError("temporary close failure")

    async def run():
        port = Port()
        usb = _UsbSerial("simulated-port", 0.1)
        monkeypatch.setattr(usb, "_port", port)
        with pytest.raises(_CleanupError, match="cleanup required"):
            await usb.close()
        assert usb.owned and usb._port is port
        await usb.close()
        assert not usb.owned and port.calls == 2

    asyncio.run(run())


def test_cancelled_port_close_keeps_original_worker_owned(monkeypatch):
    started, release = threading.Event(), threading.Event()

    class Port:
        calls = 0

        def close(self):
            self.calls += 1
            started.set()
            release.wait(2)

    async def run():
        port = Port()
        usb = _UsbSerial("simulated-port", 0.1)
        monkeypatch.setattr(usb, "_port", port)
        task = asyncio.create_task(usb.close())
        try:
            while not started.is_set():
                await asyncio.sleep(0.001)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert usb.owned and usb._closing is not None
            release.set()
            await usb.close()
            assert not usb.owned and port.calls == 1
        finally:
            release.set()
            await usb.close()

    asyncio.run(run())


def test_cancelled_partial_write_invalidates_the_session(monkeypatch):
    async def run():
        usb = _UsbSerial("simulated-port", 0.1)
        usb._fd = 55
        entered = asyncio.Event()

        async def blocked_writable(fd, timeout):
            entered.set()
            await asyncio.Event().wait()
            return True

        monkeypatch.setattr(os, "write", lambda fd, data: 1)
        monkeypatch.setattr(usb, "_writable", blocked_writable)
        task = asyncio.create_task(
            usb.write(Command.BRIGHTNESS, control_payload(Command.BRIGHTNESS, 0, 12))
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not usb.ready
        with pytest.raises(TransportError, match="reconnect"):
            await usb.write(Command.BRIGHTNESS, control_payload(Command.BRIGHTNESS, 0, 7))
        usb._fd = None

    asyncio.run(run())


def test_timed_out_open_stays_owned_until_original_worker_settles(monkeypatch):
    started, release = threading.Event(), threading.Event()
    ports = []

    class Port:
        def __init__(self, **_):
            self.closes = 0
            ports.append(self)

        def open(self):
            started.set()
            release.wait(2)

        def close(self):
            self.closes += 1

    async def run():
        monkeypatch.setattr("lighttime.usb_hybrid.Serial", Port)
        monkeypatch.setattr("lighttime.usb_hybrid._OPEN_TIMEOUT", 0.02)
        monkeypatch.setattr("lighttime.usb_hybrid._CLEANUP_TIMEOUT", 0.02)
        usb = _UsbSerial("simulated-port", 0.1)
        try:
            with pytest.raises(TransportError, match="timed out"):
                await usb.open()
            assert started.is_set() and usb.owned
            with pytest.raises(_CleanupError, match="still pending"):
                await usb.open()
            assert len(ports) == 1
            release.set()
            await usb.close()
            assert not usb.owned and ports[0].closes == 1
        finally:
            release.set()
            await usb.close()

    asyncio.run(run())
