"""USB CDC transport for Zhiyun Virtual ComPort devices on POSIX systems."""

from __future__ import annotations

import errno
import fcntl
import glob
import hashlib
import os
import plistlib
import re
import select
import subprocess
import sys
import tempfile
import termios
import time
from collections.abc import Iterator

DEFAULT_TIMEOUT = 0.8
DEFAULT_LOCK_TIMEOUT = 10.0
_USBMODEM_LOCATION_RE = re.compile(r"usbmodem([0-9a-fA-F]+)")


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
    ports = list_usb_ports()
    if not ports:
        raise RuntimeError("no /dev/cu.usbmodem* ports found")
    return ports[0]


def list_usb_ports() -> tuple[str, ...]:
    return tuple(sorted(glob.glob("/dev/cu.usbmodem*")))


def list_usb_port_metadata(
    ports: tuple[str, ...] | None = None,
) -> dict[str, dict[str, object]]:
    """Return best-effort USB descriptor metadata keyed by serial device path."""

    ports = list_usb_ports() if ports is None else ports
    if sys.platform != "darwin":
        return {port: {} for port in ports}
    return _darwin_usb_port_metadata(ports)


def _darwin_usb_port_metadata(
    ports: tuple[str, ...],
    *,
    timeout: float = 2.0,
) -> dict[str, dict[str, object]]:
    locations = {
        port: location
        for port in ports
        if (location := _darwin_usbmodem_location_id(port)) is not None
    }
    if not locations:
        return {port: {} for port in ports}
    try:
        result = subprocess.run(
            ["ioreg", "-p", "IOUSB", "-l", "-w", "0", "-a"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return {
            port: _location_only_metadata(location)
            for port, location in locations.items()
        } | {port: {} for port in ports if port not in locations}
    if result.returncode != 0 or not result.stdout:
        return {
            port: _location_only_metadata(location)
            for port, location in locations.items()
        } | {port: {} for port in ports if port not in locations}
    try:
        tree = plistlib.loads(result.stdout)
    except plistlib.InvalidFileException:
        return {
            port: _location_only_metadata(location)
            for port, location in locations.items()
        } | {port: {} for port in ports if port not in locations}
    usb_entries = {
        location: _usb_entry_metadata(entry)
        for entry in _iter_ioreg_entries(tree)
        if (location := _int_property(entry, "locationID")) is not None
    }
    metadata: dict[str, dict[str, object]] = {}
    for port in ports:
        location = locations.get(port)
        if location is None:
            metadata[port] = {}
            continue
        descriptor = dict(usb_entries.get(location, {}))
        if not descriptor:
            descriptor = _location_only_metadata(location)
        metadata[port] = descriptor
    return metadata


def _darwin_usbmodem_location_id(port: str) -> int | None:
    match = _USBMODEM_LOCATION_RE.search(os.path.basename(port))
    if not match:
        return None
    suffix = match.group(1)
    if len(suffix) <= 2:
        return None
    try:
        return int(suffix[:-2], 16) << 16
    except ValueError:
        return None


def _iter_ioreg_entries(item: object) -> Iterator[dict[str, object]]:
    if isinstance(item, dict):
        yield item
        children = item.get("IORegistryEntryChildren")
        if isinstance(children, list):
            for child in children:
                yield from _iter_ioreg_entries(child)
    elif isinstance(item, list):
        for child in item:
            yield from _iter_ioreg_entries(child)


def _usb_entry_metadata(entry: dict[str, object]) -> dict[str, object]:
    location = _int_property(entry, "locationID")
    metadata = _location_only_metadata(location) if location is not None else {}
    _set_int_metadata(metadata, "vendor_id", entry, "idVendor", width=4)
    _set_int_metadata(metadata, "product_id", entry, "idProduct", width=4)
    _set_int_metadata(metadata, "bcd_device", entry, "bcdDevice", width=4)
    _set_int_metadata(metadata, "usb_speed", entry, "USBSpeed")
    _set_int_metadata(metadata, "usb_link_speed", entry, "UsbLinkSpeed")
    _set_int_metadata(metadata, "device_speed", entry, "Device Speed")
    _set_text_metadata(metadata, "vendor_name", entry, "USB Vendor Name")
    _set_text_metadata(metadata, "product_name", entry, "USB Product Name")
    _set_text_metadata(metadata, "serial_number", entry, "USB Serial Number")
    _set_text_metadata(metadata, "serial_number", entry, "kUSBSerialNumberString")
    metadata["source"] = "macos-ioreg"
    return metadata


def _location_only_metadata(location: int) -> dict[str, object]:
    return {
        "location_id": location,
        "location_id_hex": f"0x{location:08x}",
        "source": "macos-port-name",
    }


def _set_int_metadata(
    metadata: dict[str, object],
    name: str,
    entry: dict[str, object],
    key: str,
    *,
    width: int | None = None,
) -> None:
    value = _int_property(entry, key)
    if value is None:
        return
    metadata[name] = value
    if width is not None:
        metadata[f"{name}_hex"] = f"0x{value:0{width}x}"


def _set_text_metadata(
    metadata: dict[str, object],
    name: str,
    entry: dict[str, object],
    key: str,
) -> None:
    value = entry.get(key)
    if isinstance(value, str) and value:
        metadata[name] = value


def _int_property(entry: dict[str, object], key: str) -> int | None:
    value = entry.get(key)
    return value if isinstance(value, int) else None


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
