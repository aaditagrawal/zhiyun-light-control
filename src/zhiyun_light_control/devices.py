"""Transport discovery helpers for local bridge integrations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .macos_ble_app import macos_ble_app_info
from .transports.ble import (
    BLE_PROFILES,
    BleScanResult,
    filter_ble_devices_by_name,
    scan_zhiyun_devices,
    scan_zhiyun_devices_macos_app,
    scan_zhiyun_devices_safe,
)
from .transports.usb import list_usb_ports

BLE_BACKENDS = ("worker", "macos-app", "direct")


@dataclass(frozen=True)
class UsbPortInfo:
    path: str
    selected: bool

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "selected": self.selected}


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
    selected_port = _selected_usb_port(ports, configured_usb_port)
    response: dict[str, object] = {
        "api": "zhiyun-light-control",
        "configured_transport": configured_transport,
        "usb": {
            "available": bool(ports),
            "selected_port": selected_port,
            "ports": [
                UsbPortInfo(path=port, selected=port == selected_port).to_dict()
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


def _selected_usb_port(
    ports: tuple[str, ...],
    configured_usb_port: str | None,
) -> str | None:
    if configured_usb_port is not None:
        return configured_usb_port
    return ports[0] if ports else None
