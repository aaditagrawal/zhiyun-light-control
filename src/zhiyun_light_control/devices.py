"""Transport discovery helpers for local bridge integrations."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .bridge import LightConnectionConfig
from .macos_ble_app import macos_ble_app_info, macos_ble_app_status
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
class LightConnectionCandidate:
    """A ranked connection route discovered from local USB or BLE evidence."""

    config: LightConnectionConfig
    source: str
    confidence: str
    confidence_score: int
    reason: str
    evidence: dict[str, object] | None = None

    @property
    def transport(self) -> str:
        return self.config.transport

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "transport": self.transport,
            "source": self.source,
            "confidence": self.confidence,
            "confidence_score": self.confidence_score,
            "reason": self.reason,
            "config": self.config.to_dict(),
        }
        if self.evidence is not None:
            data["evidence"] = self.evidence
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
    include_ble_status: bool = False,
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
            "macos_status": None,
            "scan": None,
        },
    }
    if include_ble_status:
        response["ble"]["macos_status"] = macos_ble_app_status(timeout=ble_timeout)
    if include_ble:
        response["ble"]["scan"] = scan_ble_devices(
            backend=ble_backend,
            timeout=ble_timeout,
            name_contains=ble_name_contains,
            python=ble_python,
        ).to_dict()
    return response


def usb_config_from_devices(
    payload: Mapping[str, object],
    *,
    port: str | None = None,
    timeout: float | None = None,
    usb_lock_timeout: float | None = None,
    persistent: bool | None = None,
) -> LightConnectionConfig:
    """Build a USB connection config from a ``devices`` discovery payload."""

    usb = _nested_mapping(payload, "usb")
    selected = port or _string_value(usb.get("selected_port"))
    if selected is None:
        selected = _first_usb_port_path(usb)
    if selected is None:
        raise ValueError("devices payload has no USB serial port")
    config = LightConnectionConfig.usb(port=selected)
    updates: dict[str, object] = {}
    if timeout is not None:
        updates["timeout"] = timeout
    if usb_lock_timeout is not None:
        updates["usb_lock_timeout"] = usb_lock_timeout
    if persistent is not None:
        updates["persistent"] = persistent
    return config.with_updates(**updates)


def ble_config_from_scan(
    payload: Mapping[str, object],
    *,
    backend: str | None = None,
    timeout: float | None = None,
    name_contains: str | None = None,
    python: str | None = None,
    persistent: bool = False,
) -> LightConnectionConfig:
    """Build a BLE config from a devices payload or raw BLE scan payload."""

    scan, ble = _scan_payload(payload)
    if scan.get("ok") is not True:
        raise ValueError("BLE scan payload is not confirmed ok")
    device = _first_ble_device(scan)
    address = _required_string(device, "address")
    profile = _string_value(device.get("suggested_profile"))
    return LightConnectionConfig.ble(
        address=address,
        name_contains=name_contains,
        timeout=timeout if timeout is not None else _float_value(ble.get("timeout")),
        backend=backend or _string_value(ble.get("backend")) or "worker",
        profile=profile or "direct",
        python=python,
        persistent=persistent,
    )


def ble_config_from_candidate(
    candidate: Mapping[str, object],
    *,
    address: str | None = None,
    name_contains: str | None = None,
    backend: str = "worker",
    timeout: float = 1.5,
    python: str | None = None,
    persistent: bool = False,
) -> LightConnectionConfig:
    """Build a BLE config from one endpoint candidate mapping."""

    return LightConnectionConfig.ble(
        address=address,
        name_contains=name_contains,
        timeout=timeout,
        backend=backend,
        profile=_string_value(candidate.get("profile")) or "direct",
        service_uuid=_required_string(candidate, "service_uuid"),
        write_uuid=_required_string(candidate, "write_uuid"),
        notify_uuid=_required_string(candidate, "notify_uuid"),
        python=python,
        persistent=persistent,
    )


def ble_config_from_endpoint_report(
    payload: Mapping[str, object],
    *,
    backend: str | None = None,
    timeout: float | None = None,
    python: str | None = None,
    persistent: bool = False,
    require_confirmed: bool = True,
) -> LightConnectionConfig:
    """Build a BLE config from a BLE endpoint-test report."""

    candidate = _endpoint_report_candidate(
        payload,
        require_confirmed=require_confirmed,
    )
    inspect = _optional_nested_mapping(payload, "inspect")
    address = _string_value(payload.get("address"))
    if address is None and inspect is not None:
        address = _string_value(inspect.get("address"))
    return ble_config_from_candidate(
        candidate,
        address=address,
        name_contains=_string_value(payload.get("name_contains")),
        backend=backend or _string_value(payload.get("backend")) or "worker",
        timeout=(
            timeout
            if timeout is not None
            else _float_value(payload.get("timeout"))
        ),
        python=python,
        persistent=persistent,
    )


def connection_candidates_from_devices(
    payload: Mapping[str, object],
    *,
    include_usb: bool = True,
    include_ble: bool = True,
    persistent: bool = False,
) -> tuple[LightConnectionCandidate, ...]:
    """Return ranked connection configs from a ``devices`` discovery payload."""

    candidates: list[LightConnectionCandidate] = []
    if include_usb:
        usb = _optional_nested_mapping(payload, "usb")
        if usb is not None:
            usb_candidate = _usb_connection_candidate(
                payload,
                usb,
                persistent=persistent,
            )
            if usb_candidate is not None:
                candidates.append(usb_candidate)
    if include_ble:
        candidates.extend(
            _ble_scan_connection_candidates(payload, persistent=persistent)
        )
    return _rank_connection_candidates(candidates)


def connection_candidates_from_endpoint_report(
    payload: Mapping[str, object],
    *,
    backend: str | None = None,
    timeout: float | None = None,
    python: str | None = None,
    persistent: bool = False,
    require_confirmed: bool = True,
) -> tuple[LightConnectionCandidate, ...]:
    """Return ranked BLE configs from an endpoint-test report."""

    candidates: list[LightConnectionCandidate] = []
    for candidate in _endpoint_report_candidates(
        payload,
        require_confirmed=require_confirmed,
    ):
        config = _ble_config_from_endpoint_candidate(
            payload,
            candidate,
            backend=backend,
            timeout=timeout,
            python=python,
            persistent=persistent,
        )
        confirmed = _candidate_is_confirmed(payload, candidate)
        candidates.append(
            LightConnectionCandidate(
                config=config,
                source="ble.endpoint_report",
                confidence="confirmed-endpoint"
                if confirmed
                else "unconfirmed-endpoint",
                confidence_score=100 if confirmed else 65,
                reason=(
                    "endpoint candidate returned ACK-backed DEVICE_INFO"
                    if confirmed
                    else "endpoint candidate was suggested but not ACK-confirmed"
                ),
                evidence={
                    "candidate": dict(candidate),
                    "acknowledged": confirmed,
                },
            )
        )
    return _rank_connection_candidates(candidates)


def discover_connection_candidates(
    *,
    configured_transport: str = "usb",
    configured_usb_port: str | None = None,
    include_ble: bool = False,
    include_ble_status: bool = False,
    ble_backend: str = "worker",
    ble_timeout: float = 5.0,
    ble_name_contains: str | None = None,
    ble_python: str | None = None,
    persistent: bool = False,
) -> tuple[LightConnectionCandidate, ...]:
    """Discover local USB/BLE routes and return ranked SDK configs."""

    return connection_candidates_from_devices(
        discover_transport_devices(
            configured_transport=configured_transport,
            configured_usb_port=configured_usb_port,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            ble_backend=ble_backend,
            ble_timeout=ble_timeout,
            ble_name_contains=ble_name_contains,
            ble_python=ble_python,
        ),
        include_ble=include_ble,
        persistent=persistent,
    )


def best_connection_candidate(
    candidates: Iterable[LightConnectionCandidate],
) -> LightConnectionCandidate:
    """Return the highest-ranked candidate from a candidate iterable."""

    ranked = _rank_connection_candidates(list(candidates))
    if not ranked:
        raise ValueError("no connection candidates available")
    return ranked[0]


def best_connection_config(
    candidates: Iterable[LightConnectionCandidate],
) -> LightConnectionConfig:
    return best_connection_candidate(candidates).config


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


def _nested_mapping(
    payload: Mapping[str, object],
    key: str,
) -> Mapping[str, object]:
    value = payload.get(key)
    if isinstance(value, Mapping):
        return value
    raise ValueError(f"payload missing {key!r} mapping")


def _optional_nested_mapping(
    payload: Mapping[str, object],
    key: str,
) -> Mapping[str, object] | None:
    value = payload.get(key)
    return value if isinstance(value, Mapping) else None


def _string_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = _string_value(payload.get(key))
    if value is None:
        raise ValueError(f"payload missing {key!r}")
    return value


def _float_value(value: object, default: float = 1.5) -> float:
    if value is None:
        return default
    return float(value)


def _first_usb_port_path(usb: Mapping[str, object]) -> str | None:
    ports = usb.get("ports")
    if not isinstance(ports, list):
        return None
    for item in ports:
        if not isinstance(item, Mapping):
            continue
        path = _string_value(item.get("path"))
        if path is not None:
            return path
    return None


def _scan_payload(
    payload: Mapping[str, object],
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    ble = _optional_nested_mapping(payload, "ble")
    if ble is not None:
        scan = _optional_nested_mapping(ble, "scan")
        if scan is None:
            raise ValueError("devices payload missing BLE scan mapping")
        return scan, ble
    return payload, {}


def _first_ble_device(scan: Mapping[str, object]) -> Mapping[str, object]:
    devices = scan.get("devices")
    if not isinstance(devices, list):
        raise ValueError("BLE scan payload has no devices list")
    first: Mapping[str, object] | None = None
    for device in devices:
        if not isinstance(device, Mapping):
            continue
        if first is None:
            first = device
        if _string_value(device.get("suggested_profile")) is not None:
            return device
    if first is None:
        raise ValueError("BLE scan payload has no devices")
    return first


def _endpoint_report_candidate(
    payload: Mapping[str, object],
    *,
    require_confirmed: bool,
) -> Mapping[str, object]:
    if require_confirmed:
        candidate = _first_candidate_from_list(payload.get("confirmed_candidates"))
        if candidate is not None:
            return candidate
        raise ValueError("endpoint report has no confirmed BLE candidates")
    candidate = _first_candidate_from_list(payload.get("confirmed_candidates"))
    if candidate is not None:
        return candidate
    tests = payload.get("tests")
    if isinstance(tests, list):
        for test in tests:
            if not isinstance(test, Mapping):
                continue
            candidate = _optional_nested_mapping(test, "candidate")
            if candidate is not None:
                return candidate
    inspect = _optional_nested_mapping(payload, "inspect")
    if inspect is not None:
        candidate = _first_candidate_from_list(inspect.get("endpoint_candidates"))
        if candidate is not None:
            return candidate
    raise ValueError("endpoint report has no BLE endpoint candidates")


def _first_candidate_from_list(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, Mapping):
            return item
    return None


def _usb_connection_candidate(
    payload: Mapping[str, object],
    usb: Mapping[str, object],
    *,
    persistent: bool,
) -> LightConnectionCandidate | None:
    try:
        config = usb_config_from_devices(payload, persistent=persistent)
    except ValueError:
        return None
    selected = _selected_usb_port_evidence(usb, config.port)
    confidence, score, reason = _usb_confidence(selected)
    return LightConnectionCandidate(
        config=config,
        source="devices.usb",
        confidence=confidence,
        confidence_score=score,
        reason=reason,
        evidence=dict(selected) if selected is not None else None,
    )


def _selected_usb_port_evidence(
    usb: Mapping[str, object],
    port: str | None,
) -> Mapping[str, object] | None:
    ports = usb.get("ports")
    if not isinstance(ports, list):
        return None
    first: Mapping[str, object] | None = None
    for item in ports:
        if not isinstance(item, Mapping):
            continue
        if first is None:
            first = item
        if port is not None and item.get("path") == port:
            return item
    return first


def _usb_confidence(
    selected: Mapping[str, object] | None,
) -> tuple[str, int, str]:
    metadata = _optional_nested_mapping(selected or {}, "metadata")
    if metadata is not None:
        vendor_id = metadata.get("vendor_id")
        product_id = metadata.get("product_id")
        product_name = _string_value(metadata.get("product_name")) or ""
        description = _string_value(metadata.get("description")) or ""
        haystack = f"{product_name} {description}".lower()
        if vendor_id == 0xFFF8 and product_id == 0x0180:
            return (
                "known-usb-descriptor",
                95,
                "USB descriptor matches Zhiyun Virtual ComPort",
            )
        if "zhiyun" in haystack:
            return (
                "vendor-name",
                85,
                "USB descriptor text references Zhiyun",
            )
    return ("serial-port", 70, "USB serial port is available")


def _ble_scan_connection_candidates(
    payload: Mapping[str, object],
    *,
    persistent: bool,
) -> tuple[LightConnectionCandidate, ...]:
    ble = _optional_nested_mapping(payload, "ble")
    if ble is None:
        return ()
    scan = _optional_nested_mapping(ble, "scan")
    if scan is None or scan.get("ok") is not True:
        return ()
    devices = scan.get("devices")
    if not isinstance(devices, list):
        return ()
    candidates: list[LightConnectionCandidate] = []
    for device in devices:
        if not isinstance(device, Mapping):
            continue
        try:
            config = _ble_config_from_scan_device(
                device,
                ble,
                persistent=persistent,
            )
        except ValueError:
            continue
        confidence, score, reason = _ble_scan_confidence(device)
        candidates.append(
            LightConnectionCandidate(
                config=config,
                source="devices.ble.scan",
                confidence=confidence,
                confidence_score=score,
                reason=reason,
                evidence=dict(device),
            )
        )
    return tuple(candidates)


def _ble_config_from_scan_device(
    device: Mapping[str, object],
    ble: Mapping[str, object],
    *,
    persistent: bool,
) -> LightConnectionConfig:
    return LightConnectionConfig.ble(
        address=_required_string(device, "address"),
        timeout=_float_value(ble.get("timeout")),
        backend=_string_value(ble.get("backend")) or "worker",
        profile=_string_value(device.get("suggested_profile")) or "direct",
        persistent=persistent,
    )


def _ble_scan_confidence(
    device: Mapping[str, object],
) -> tuple[str, int, str]:
    profile = _string_value(device.get("suggested_profile"))
    if profile is not None:
        return (
            "advertised-profile",
            85,
            f"BLE advertisement matched built-in {profile} profile",
        )
    name = (_string_value(device.get("name")) or "").lower()
    if any(part in name for part in ("zhiyun", "molus", "pl103", "g60")):
        return ("name-match", 65, "BLE device name looks like a Zhiyun light")
    return ("scan-hit", 45, "BLE scan returned a candidate device")


def _endpoint_report_candidates(
    payload: Mapping[str, object],
    *,
    require_confirmed: bool,
) -> tuple[Mapping[str, object], ...]:
    candidates: list[Mapping[str, object]] = []
    if require_confirmed:
        _append_candidate_list(candidates, payload.get("confirmed_candidates"))
        if not candidates:
            raise ValueError("endpoint report has no confirmed BLE candidates")
        return tuple(candidates)
    _append_candidate_list(candidates, payload.get("confirmed_candidates"))
    tests = payload.get("tests")
    if isinstance(tests, list):
        for test in tests:
            if not isinstance(test, Mapping):
                continue
            candidate = _optional_nested_mapping(test, "candidate")
            if candidate is not None:
                _append_unique_candidate(candidates, candidate)
    inspect = _optional_nested_mapping(payload, "inspect")
    if inspect is not None:
        _append_candidate_list(candidates, inspect.get("endpoint_candidates"))
    if not candidates:
        raise ValueError("endpoint report has no BLE endpoint candidates")
    return tuple(candidates)


def _append_candidate_list(
    candidates: list[Mapping[str, object]],
    value: object,
) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, Mapping):
            _append_unique_candidate(candidates, item)


def _append_unique_candidate(
    candidates: list[Mapping[str, object]],
    candidate: Mapping[str, object],
) -> None:
    key = _candidate_key(candidate)
    if any(_candidate_key(item) == key for item in candidates):
        return
    candidates.append(candidate)


def _candidate_key(candidate: Mapping[str, object]) -> tuple[str, str, str]:
    return (
        _string_value(candidate.get("service_uuid")) or "",
        _string_value(candidate.get("write_uuid")) or "",
        _string_value(candidate.get("notify_uuid")) or "",
    )


def _candidate_is_confirmed(
    payload: Mapping[str, object],
    candidate: Mapping[str, object],
) -> bool:
    confirmed = payload.get("confirmed_candidates")
    if not isinstance(confirmed, list):
        return False
    key = _candidate_key(candidate)
    return any(
        isinstance(item, Mapping) and _candidate_key(item) == key
        for item in confirmed
    )


def _ble_config_from_endpoint_candidate(
    payload: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    backend: str | None,
    timeout: float | None,
    python: str | None,
    persistent: bool,
) -> LightConnectionConfig:
    inspect = _optional_nested_mapping(payload, "inspect")
    address = _string_value(payload.get("address"))
    if address is None and inspect is not None:
        address = _string_value(inspect.get("address"))
    return ble_config_from_candidate(
        candidate,
        address=address,
        name_contains=_string_value(payload.get("name_contains")),
        backend=backend or _string_value(payload.get("backend")) or "worker",
        timeout=(
            timeout
            if timeout is not None
            else _float_value(payload.get("timeout"))
        ),
        python=python,
        persistent=persistent,
    )


def _rank_connection_candidates(
    candidates: Iterable[LightConnectionCandidate],
) -> tuple[LightConnectionCandidate, ...]:
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                -candidate.confidence_score,
                candidate.transport,
                candidate.source,
            ),
        )
    )
