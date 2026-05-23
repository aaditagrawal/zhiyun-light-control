"""Transport discovery helpers for local bridge integrations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .macos_ble_app import macos_ble_app_info
from .models import CommandResult
from .protocol import (
    RuntimeCommand,
    build_runtime_frame,
    first_response_frame,
    iter_frames,
)
from .transports.ble import (
    BLE_PROFILES,
    BleEndpointCandidate,
    BleExchangeResult,
    BleInspectResult,
    BleScanResult,
    BleTransport,
    exchange_zhiyun_ble_macos_app,
    exchange_zhiyun_ble_safe,
    filter_ble_devices_by_name,
    inspect_zhiyun_ble_macos_app,
    inspect_zhiyun_ble_safe,
    inspect_zhiyun_device,
    scan_zhiyun_devices,
    scan_zhiyun_devices_macos_app,
    scan_zhiyun_devices_safe,
    suggest_ble_endpoint_candidates,
)
from .transports.usb import list_usb_port_metadata, list_usb_ports

BLE_BACKENDS = ("worker", "macos-app", "direct")


@dataclass(frozen=True)
class UsbPortInfo:
    path: str
    selected: bool
    metadata: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {"path": self.path, "selected": self.selected}
        if self.metadata:
            data["metadata"] = self.metadata
        return data


@dataclass(frozen=True)
class BleEndpointCandidateTest:
    candidate: BleEndpointCandidate
    exchange: BleExchangeResult
    command_result: CommandResult | None = None

    @property
    def acknowledged(self) -> bool:
        return bool(self.command_result and self.command_result.acknowledged)

    @property
    def transport_status(self) -> str:
        if self.command_result is None:
            return "transport_error"
        return self.command_result.transport_status

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate": self.candidate.to_dict(),
            "exchange": self.exchange.to_dict(),
            "command_result": self.command_result.to_dict()
            if self.command_result
            else None,
            "acknowledged": self.acknowledged,
            "transport_status": self.transport_status,
        }


@dataclass(frozen=True)
class BleEndpointCandidateReport:
    backend: str
    timeout: float
    address: str | None
    name_contains: str | None
    max_candidates: int
    inspect: BleInspectResult
    tests: tuple[BleEndpointCandidateTest, ...] = ()

    @property
    def ok(self) -> bool:
        return any(test.acknowledged for test in self.tests)

    @property
    def confirmed_candidates(self) -> tuple[BleEndpointCandidate, ...]:
        return tuple(test.candidate for test in self.tests if test.acknowledged)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "backend": self.backend,
            "timeout": self.timeout,
            "address": self.address,
            "name_contains": self.name_contains,
            "max_candidates": self.max_candidates,
            "inspect": self.inspect.to_dict(),
            "tests": [test.to_dict() for test in self.tests],
            "confirmed_candidates": [
                candidate.to_dict() for candidate in self.confirmed_candidates
            ],
        }


def discover_transport_devices(
    *,
    configured_transport: str = "usb",
    configured_usb_port: str | None = None,
    include_ble: bool = False,
    ble_backend: str = "worker",
    ble_timeout: float = 5.0,
    ble_name_contains: str | None = None,
    ble_python: str | None = None,
) -> dict[str, object]:
    ports = list_usb_ports()
    metadata = list_usb_port_metadata(ports)
    selected_port = _selected_usb_port(ports, configured_usb_port)
    response: dict[str, object] = {
        "api": "zhiyun-light-control",
        "configured_transport": configured_transport,
        "usb": {
            "available": bool(ports),
            "selected_port": selected_port,
            "ports": [
                UsbPortInfo(
                    path=port,
                    selected=port == selected_port,
                    metadata=metadata.get(port),
                ).to_dict()
                for port in ports
            ],
        },
        "ble": {
            "included": include_ble,
            "backend": ble_backend,
            "timeout": ble_timeout,
            "name_contains": ble_name_contains,
            "profiles": [profile.to_dict() for profile in BLE_PROFILES.values()],
            "macos_helper": macos_ble_app_info(),
            "scan": None,
        },
    }
    if include_ble:
        response["ble"]["scan"] = scan_ble_devices(
            backend=ble_backend,
            timeout=ble_timeout,
            name_contains=ble_name_contains,
            python=ble_python,
        ).to_dict()
    return response


def scan_ble_devices(
    *,
    backend: str = "worker",
    timeout: float = 5.0,
    name_contains: str | None = None,
    python: str | None = None,
) -> BleScanResult:
    if backend not in BLE_BACKENDS:
        supported = ", ".join(BLE_BACKENDS)
        raise ValueError(f"unsupported BLE backend {backend!r}; expected {supported}")
    if backend == "macos-app":
        return scan_zhiyun_devices_macos_app(
            timeout=timeout,
            name_contains=name_contains,
        )
    if backend == "direct":
        devices = filter_ble_devices_by_name(
            asyncio.run(scan_zhiyun_devices(timeout=timeout)),
            name_contains,
        )
        return BleScanResult(
            ok=True,
            devices=devices,
            returncode=0,
            worker_python="direct",
        )
    return scan_zhiyun_devices_safe(
        timeout=timeout,
        name_contains=name_contains,
        python=python,
    )


def inspect_ble_device(
    *,
    backend: str = "worker",
    timeout: float = 5.0,
    address: str | None = None,
    name_contains: str | None = None,
    python: str | None = None,
) -> BleInspectResult:
    if backend not in BLE_BACKENDS:
        supported = ", ".join(BLE_BACKENDS)
        raise ValueError(f"unsupported BLE backend {backend!r}; expected {supported}")
    if backend == "macos-app":
        return inspect_zhiyun_ble_macos_app(
            address=address,
            name_contains=name_contains,
            timeout=timeout,
        )
    if backend == "direct":
        return asyncio.run(
            inspect_zhiyun_device(
                address=address,
                name_contains=name_contains,
                timeout=timeout,
            )
        )
    return inspect_zhiyun_ble_safe(
        address=address,
        name_contains=name_contains,
        timeout=timeout,
        python=python,
    )


def test_ble_endpoint_candidates(
    *,
    backend: str = "worker",
    timeout: float = 1.5,
    address: str | None = None,
    name_contains: str | None = None,
    python: str | None = None,
    max_candidates: int = 4,
) -> BleEndpointCandidateReport:
    if backend not in BLE_BACKENDS:
        supported = ", ".join(BLE_BACKENDS)
        raise ValueError(f"unsupported BLE backend {backend!r}; expected {supported}")
    inspect = inspect_ble_device(
        backend=backend,
        timeout=timeout,
        address=address,
        name_contains=name_contains,
        python=python,
    )
    selected_address = inspect.address or address
    bounded_max = max(0, max_candidates)
    if not inspect.ok:
        return BleEndpointCandidateReport(
            backend=backend,
            timeout=timeout,
            address=selected_address,
            name_contains=name_contains,
            max_candidates=bounded_max,
            inspect=inspect,
        )
    candidates = suggest_ble_endpoint_candidates(inspect.services)[:bounded_max]
    tests = tuple(
        _test_ble_endpoint_candidate(
            candidate,
            backend=backend,
            timeout=timeout,
            address=selected_address,
            name_contains=None if selected_address else name_contains,
            python=python,
            sequence=index,
        )
        for index, candidate in enumerate(candidates, start=1)
    )
    return BleEndpointCandidateReport(
        backend=backend,
        timeout=timeout,
        address=selected_address,
        name_contains=name_contains,
        max_candidates=bounded_max,
        inspect=inspect,
        tests=tests,
    )


def _test_ble_endpoint_candidate(
    candidate: BleEndpointCandidate,
    *,
    backend: str,
    timeout: float,
    address: str | None,
    name_contains: str | None,
    python: str | None,
    sequence: int,
) -> BleEndpointCandidateTest:
    tx = build_runtime_frame(sequence, RuntimeCommand.DEVICE_INFO)
    exchange = _exchange_ble_endpoint_candidate(
        tx,
        candidate,
        backend=backend,
        timeout=timeout,
        address=address,
        name_contains=name_contains,
        python=python,
    )
    return BleEndpointCandidateTest(
        candidate=candidate,
        exchange=exchange,
        command_result=_command_result_from_exchange(exchange),
    )


def _exchange_ble_endpoint_candidate(
    tx: bytes,
    candidate: BleEndpointCandidate,
    *,
    backend: str,
    timeout: float,
    address: str | None,
    name_contains: str | None,
    python: str | None,
) -> BleExchangeResult:
    if backend == "macos-app":
        return exchange_zhiyun_ble_macos_app(
            tx,
            address=address,
            name_contains=name_contains,
            profile=candidate.profile,
            service_uuid=candidate.service_uuid,
            write_uuid=candidate.write_uuid,
            notify_uuid=candidate.notify_uuid,
            timeout=timeout,
        )
    if backend == "direct":
        return asyncio.run(
            _exchange_ble_endpoint_candidate_direct(
                tx,
                candidate,
                address=address,
                name_contains=name_contains,
                timeout=timeout,
            )
        )
    return exchange_zhiyun_ble_safe(
        tx,
        address=address,
        name_contains=name_contains,
        profile=candidate.profile,
        service_uuid=candidate.service_uuid,
        write_uuid=candidate.write_uuid,
        notify_uuid=candidate.notify_uuid,
        timeout=timeout,
        python=python,
    )


async def _exchange_ble_endpoint_candidate_direct(
    tx: bytes,
    candidate: BleEndpointCandidate,
    *,
    address: str | None,
    name_contains: str | None,
    timeout: float,
) -> BleExchangeResult:
    transport = BleTransport(
        address=address,
        name_contains=name_contains,
        profile=candidate.profile,
        service_uuid=candidate.service_uuid,
        write_uuid=candidate.write_uuid,
        notify_uuid=candidate.notify_uuid,
        timeout=timeout,
    )
    try:
        async with transport:
            rx = await transport.exchange(tx, timeout=timeout)
            return BleExchangeResult(
                ok=True,
                tx=tx,
                rx=rx,
                address=transport.address,
                worker_python="direct",
            )
    except Exception as exc:
        return BleExchangeResult(
            ok=False,
            tx=tx,
            address=transport.address or address,
            error=str(exc),
            worker_python="direct",
        )


def _command_result_from_exchange(exchange: BleExchangeResult) -> CommandResult | None:
    if not exchange.ok and not exchange.rx:
        return None
    return CommandResult(
        command=RuntimeCommand.DEVICE_INFO,
        tx=exchange.tx,
        rx=exchange.rx,
        frames=tuple(iter_frames(exchange.rx)),
        ack=first_response_frame(
            exchange.rx,
            tx=exchange.tx,
            cmd=RuntimeCommand.DEVICE_INFO,
        ),
    )


def _selected_usb_port(
    ports: tuple[str, ...],
    configured_usb_port: str | None,
) -> str | None:
    if configured_usb_port is not None:
        return configured_usb_port
    return ports[0] if ports else None
