"""USB CDC transport for Zhiyun Virtual ComPort devices."""

from __future__ import annotations

import glob
import hashlib
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator

import serial
from serial.tools import list_ports

if os.name == "posix":
    import errno
    import fcntl
    import select
    import termios

DEFAULT_TIMEOUT = 0.8
DEFAULT_LOCK_TIMEOUT = 10.0
ZHIYUN_VENDOR_ID = 0xFFF8
ZHIYUN_PRODUCT_ID = 0x0180
_USBMODEM_LOCATION_RE = re.compile(r"usbmodem([0-9a-fA-F]+)")
_USB_GLOB_PATTERNS = (
    "/dev/cu.usbmodem*",
    "/dev/tty.usbmodem*",
    "/dev/ttyACM*",
    "/dev/ttyUSB*",
)


class UsbTransport:
    """Synchronous USB CDC transport.

    POSIX systems use direct file-descriptor I/O so the local macOS bench path
    keeps its lock and termios behavior. Windows and other non-POSIX runtimes
    use pyserial with the same ``exchange(tx, timeout)`` SDK contract.
    """

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
        self._serial: object | None = None

    def __enter__(self) -> UsbTransport:
        self.open()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def open(self) -> None:
        if self.fd is not None or self._serial is not None:
            return
        path = find_usb_port(self.port)
        if _should_use_pyserial(path):
            self._open_pyserial(path)
            return
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

    def _open_pyserial(self, path: str) -> None:
        self._serial = serial.Serial(
            port=path,
            baudrate=9600,
            timeout=0.05,
            write_timeout=self.timeout,
        )
        self.port = path

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self._lock_fd is not None:
            _release_usb_lock(self._lock_fd)
            self._lock_fd = None
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def exchange(self, tx: bytes, timeout: float | None = None) -> bytes:
        if self.fd is None and self._serial is None:
            self.open()
        if self._serial is not None:
            return _exchange_pyserial(
                self._serial,
                tx,
                self.timeout if timeout is None else timeout,
            )
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


def _should_use_pyserial(path: str) -> bool:
    return os.name != "posix" or not path.startswith("/dev/")


def _exchange_pyserial(serial_port: object, tx: bytes, timeout: float) -> bytes:
    serial_port.write(tx)
    serial_port.flush()
    deadline = time.time() + timeout
    buf = bytearray()
    while time.time() < deadline:
        waiting = getattr(serial_port, "in_waiting", 0)
        size = waiting if isinstance(waiting, int) and waiting > 0 else 1
        chunk = serial_port.read(size)
        if chunk:
            buf.extend(chunk)
            continue
        time.sleep(0.01)
    return bytes(buf)


def list_usb_ports() -> tuple[str, ...]:
    ports = set(_pyserial_usb_ports())
    for pattern in _USB_GLOB_PATTERNS:
        ports.update(glob.glob(pattern))
    return tuple(sorted(ports))


def list_usb_port_metadata(
    ports: tuple[str, ...] | None = None,
) -> dict[str, dict[str, object]]:
    """Return best-effort USB descriptor metadata keyed by serial device path."""

    ports = list_usb_ports() if ports is None else ports
    if sys.platform != "darwin":
        return _pyserial_usb_port_metadata(ports)
    serial_metadata = _pyserial_usb_port_metadata(ports)
    darwin_metadata = _darwin_usb_port_metadata(ports)
    return {
        port: serial_metadata.get(port, {}) | darwin_metadata.get(port, {})
        for port in ports
    }


def _pyserial_usb_ports() -> tuple[str, ...]:
    matches: list[str] = []
    for info in list_ports.comports():
        device = getattr(info, "device", None)
        if isinstance(device, str) and device and _serial_port_matches_zhiyun(info):
            matches.append(device)
    return tuple(matches)


def _pyserial_usb_port_metadata(
    ports: tuple[str, ...],
) -> dict[str, dict[str, object]]:
    by_device = {
        str(device): info
        for info in list_ports.comports()
        if isinstance((device := getattr(info, "device", None)), str)
    }
    return {
        port: _pyserial_port_metadata(by_device[port]) if port in by_device else {}
        for port in ports
    }


def _pyserial_port_metadata(info: object) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if (vendor_id := _serial_port_int(info, "vid")) is not None:
        metadata["vendor_id"] = vendor_id
        metadata["vendor_id_hex"] = f"0x{vendor_id:04x}"
    if (product_id := _serial_port_int(info, "pid")) is not None:
        metadata["product_id"] = product_id
        metadata["product_id_hex"] = f"0x{product_id:04x}"
    for target, source in (
        ("serial_number", "serial_number"),
        ("manufacturer", "manufacturer"),
        ("product_name", "product"),
        ("description", "description"),
        ("hardware_id", "hwid"),
    ):
        value = _serial_port_text(info, source)
        if value:
            metadata[target] = value
    if metadata:
        metadata["source"] = "pyserial"
    return metadata


def _serial_port_matches_zhiyun(info: object) -> bool:
    vendor_id = _serial_port_int(info, "vid")
    product_id = _serial_port_int(info, "pid")
    if vendor_id == ZHIYUN_VENDOR_ID and product_id == ZHIYUN_PRODUCT_ID:
        return True
    haystack = " ".join(
        text
        for text in (
            _serial_port_text(info, "manufacturer"),
            _serial_port_text(info, "product"),
            _serial_port_text(info, "description"),
            _serial_port_text(info, "hwid"),
        )
        if text
    ).lower()
    return "zhiyun" in haystack


def _serial_port_int(info: object, name: str) -> int | None:
    value = getattr(info, name, None)
    return value if isinstance(value, int) else None


def _serial_port_text(info: object, name: str) -> str:
    value = getattr(info, name, None)
    return value if isinstance(value, str) else ""


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
    if os.name != "posix":
        raise RuntimeError("USB port locks require a POSIX serial device")
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
    if os.name != "posix":
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _default_lock_path(port: str) -> str:
    digest = hashlib.sha1(port.encode("utf-8")).hexdigest()[:16]
    name = f"zhiyun-light-control-{digest}.lock"
    return os.path.join(tempfile.gettempdir(), name)
