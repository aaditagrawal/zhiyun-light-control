"""USB CDC transport for Zhiyun Virtual ComPort devices on POSIX systems."""

from __future__ import annotations

import errno
import fcntl
import glob
import hashlib
import os
import select
import tempfile
import termios
import time

DEFAULT_TIMEOUT = 0.8
DEFAULT_LOCK_TIMEOUT = 10.0


class UsbTransport:
    """Synchronous USB CDC transport using the POSIX serial device."""

    def __init__(
        self,
        port: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        lock_timeout: float | None = DEFAULT_LOCK_TIMEOUT,
        lock_path: str | None = None,
    ):
        self.port = port
        self.timeout = timeout
        self.lock_timeout = lock_timeout
        self.lock_path = lock_path
        self.fd: int | None = None
        self._lock_fd: int | None = None

    def __enter__(self) -> UsbTransport:
        self.open()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def open(self) -> None:
        if self.fd is not None:
            return
        path = find_usb_port(self.port)
        lock_fd = _acquire_usb_lock(
            path,
            timeout=self.lock_timeout,
            lock_path=self.lock_path,
        )
        fd: int | None = None
        try:
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
        except Exception:
            if fd is not None:
                os.close(fd)
            _release_usb_lock(lock_fd)
            raise
        if fd is None:
            _release_usb_lock(lock_fd)
            raise RuntimeError("USB serial device did not open")
        self.port = path
        self.fd = fd
        self._lock_fd = lock_fd

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self._lock_fd is not None:
            _release_usb_lock(self._lock_fd)
            self._lock_fd = None

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


def _acquire_usb_lock(
    port: str,
    *,
    timeout: float | None,
    lock_path: str | None,
) -> int:
    path = lock_path or _default_lock_path(port)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    deadline = None if timeout is None else time.time() + max(0.0, timeout)
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()} {port}\n".encode())
            return fd
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                os.close(fd)
                raise
            if deadline is not None and time.time() >= deadline:
                os.close(fd)
                raise TimeoutError(
                    f"timed out waiting for USB port lock for {port}"
                ) from exc
            time.sleep(0.05)


def _release_usb_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _default_lock_path(port: str) -> str:
    digest = hashlib.sha1(port.encode("utf-8")).hexdigest()[:16]
    name = f"zhiyun-light-control-{digest}.lock"
    return os.path.join(tempfile.gettempdir(), name)
