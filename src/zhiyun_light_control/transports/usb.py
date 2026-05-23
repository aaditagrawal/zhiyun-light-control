"""USB CDC transport for Zhiyun Virtual ComPort devices on POSIX systems."""

from __future__ import annotations

import glob
import os
import select
import termios
import time


DEFAULT_TIMEOUT = 0.8


class UsbTransport:
    """Synchronous USB CDC transport using the POSIX serial device."""

    def __init__(self, port: str | None = None, *, timeout: float = DEFAULT_TIMEOUT):
        self.port = port
        self.timeout = timeout
        self.fd: int | None = None

    def __enter__(self) -> "UsbTransport":
        self.open()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def open(self) -> None:
        if self.fd is not None:
            return
        path = find_usb_port(self.port)
        fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(fd)
        iflag, oflag, cflag, lflag, _ispeed, _ospeed, cc = attrs
        iflag &= ~(termios.IXON | termios.IXOFF | termios.IXANY)
        iflag &= ~(termios.ICRNL | termios.INLCR | termios.IGNCR)
        oflag &= ~termios.OPOST
        lflag &= ~(termios.ICANON | termios.ECHO | termios.ECHOE | termios.ISIG)
        cflag |= termios.CLOCAL | termios.CREAD
        cflag &= ~termios.PARENB
        cflag &= ~termios.CSTOPB
        cflag &= ~termios.CSIZE
        cflag |= termios.CS8
        termios.tcsetattr(
            fd,
            termios.TCSANOW,
            [iflag, oflag, cflag, lflag, termios.B9600, termios.B9600, cc],
        )
        termios.tcflush(fd, termios.TCIOFLUSH)
        self.port = path
        self.fd = fd

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def exchange(self, tx: bytes, timeout: float | None = None) -> bytes:
        if self.fd is None:
            self.open()
        if self.fd is None:
            raise RuntimeError("USB transport is not open")
        os.write(self.fd, tx)
        deadline = time.time() + (self.timeout if timeout is None else timeout)
        buf = bytearray()
        while time.time() < deadline:
            rlist, _, _ = select.select([self.fd], [], [], 0.05)
            if not rlist:
                continue
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                continue
            if chunk:
                buf.extend(chunk)
        return bytes(buf)


def find_usb_port(port: str | None = None) -> str:
    if port:
        return port
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        raise RuntimeError("no /dev/cu.usbmodem* ports found")
    return ports[0]

