"""Programmatic integration preflight helpers for media-control hosts."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from .bridge import (
    LightConnectionConfig,
    LightFactory,
    make_light_factory,
)
from .devices import discover_transport_devices
from .server import (
    capabilities_response,
    integration_manifest_response,
    integration_snapshot_response,
    readiness_response,
)
from .status import read_sync_status
from .transports.ble import BleWorkerError

StatusSnapshot = tuple[dict[str, object], bool, str | None]


@dataclass(frozen=True)
class LightIntegration:
    """Reusable setup/preflight facade for embedding in host applications."""

    config: LightConnectionConfig = field(default_factory=LightConnectionConfig)
    allow_control: bool = False
    preset_names: tuple[str, ...] = ()
    cue_names: tuple[str, ...] = ()
    light_factory: LightFactory | None = None

    def status(self) -> StatusSnapshot:
        return local_status_snapshot(
            self.config,
            light_factory=self.light_factory,
        )

    def readiness(
        self,
        *,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return local_readiness(
            self.config,
            allow_control=self.allow_control,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            state_version=state_version,
            state=state,
            light_factory=self.light_factory,
        )

    def manifest(self) -> dict[str, object]:
        return local_manifest(
            self.config,
            allow_control=self.allow_control,
            presets=self.preset_names,
            cues=self.cue_names,
        )

    def capabilities(self) -> dict[str, object]:
        return local_capabilities(
            allow_control=self.allow_control,
            presets=self.preset_names,
            cues=self.cue_names,
        )

    def snapshot(
        self,
        *,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        return local_integration_snapshot(
            self.config,
            allow_control=self.allow_control,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            presets=self.preset_names,
            cues=self.cue_names,
            state_version=state_version,
            state=state,
            light_factory=self.light_factory,
        )


def local_status_snapshot(
    config: LightConnectionConfig | None = None,
    *,
    light_factory: LightFactory | None = None,
    status_reader: Callable[[object], object] | None = None,
) -> StatusSnapshot:
    resolved = config or LightConnectionConfig()
    try:
        factory = light_factory or make_light_factory(resolved)
        with factory() as light:
            if status_reader is None:
                report = read_sync_status(
                    light,
                    transport=resolved.transport,
                    timeout=resolved.timeout,
                )
            else:
                report = status_reader(light)
    except Exception as exc:
        return local_error_status(exc), False, str(exc)
    payload = _report_to_dict(report)
    return payload, payload.get("connection_confirmed") is True, None


def local_readiness(
    config: LightConnectionConfig | None = None,
    *,
    allow_control: bool = False,
    include_ble: bool = False,
    include_ble_status: bool | None = None,
    state_version: int = 0,
    state: Mapping[str, object] | None = None,
    light_factory: LightFactory | None = None,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig()
    status, connection_confirmed, error = local_status_snapshot(
        resolved,
        light_factory=light_factory,
    )
    backend = _ble_backend(resolved)
    status_requested = (
        resolved.transport == "ble" and backend == "macos-app"
        if include_ble_status is None
        else include_ble_status
    )
    devices = discover_transport_devices(
        configured_transport=resolved.transport,
        configured_usb_port=resolved.port,
        include_ble=include_ble,
        include_ble_status=status_requested,
        ble_backend=backend,
        ble_timeout=resolved.timeout,
        ble_name_contains=resolved.name_contains,
        ble_python=resolved.ble_python,
    )
    return readiness_response(
        allow_control=allow_control,
        transport=resolved.transport,
        ble_backend=backend,
        ble_profile=resolved.ble_profile,
        ble_address=resolved.address,
        ble_name_contains=resolved.name_contains,
        connection_confirmed=connection_confirmed,
        status=status,
        error=error,
        devices=devices,
        state_version=state_version,
        state=None if state is None else dict(state),
    )


def local_manifest(
    config: LightConnectionConfig | None = None,
    *,
    allow_control: bool = False,
    presets: Iterable[str] = (),
    cues: Iterable[str] = (),
) -> dict[str, object]:
    resolved = config or LightConnectionConfig()
    return integration_manifest_response(
        allow_control=allow_control,
        presets=list(presets),
        cues=list(cues),
        transport=resolved.transport,
        ble_backend=_ble_backend(resolved),
        ble_profile=resolved.ble_profile,
        ble_address=resolved.address,
        ble_name_contains=resolved.name_contains,
    )


def local_capabilities(
    *,
    allow_control: bool = False,
    presets: Iterable[str] = (),
    cues: Iterable[str] = (),
) -> dict[str, object]:
    return capabilities_response(
        allow_control=allow_control,
        presets=list(presets),
        cues=list(cues),
    )


def local_integration_snapshot(
    config: LightConnectionConfig | None = None,
    *,
    allow_control: bool = False,
    include_ble: bool = False,
    include_ble_status: bool | None = None,
    presets: Iterable[str] = (),
    cues: Iterable[str] = (),
    state_version: int = 0,
    state: Mapping[str, object] | None = None,
    light_factory: LightFactory | None = None,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig()
    preset_names = tuple(presets)
    cue_names = tuple(cues)
    ready = local_readiness(
        resolved,
        allow_control=allow_control,
        include_ble=include_ble,
        include_ble_status=include_ble_status,
        state_version=state_version,
        state=state,
        light_factory=light_factory,
    )
    devices = ready.get("devices")
    if not isinstance(devices, Mapping):
        devices = {}
    return integration_snapshot_response(
        manifest=local_manifest(
            resolved,
            allow_control=allow_control,
            presets=preset_names,
            cues=cue_names,
        ),
        capabilities=local_capabilities(
            allow_control=allow_control,
            presets=preset_names,
            cues=cue_names,
        ),
        ready=ready,
        devices=devices,
    )


def local_error_status(exc: Exception) -> dict[str, object]:
    status: dict[str, object] = {"ok": False, "error": str(exc)}
    if isinstance(exc, BleWorkerError):
        status["transport"] = "ble"
        status["exchange"] = exc.result.to_dict()
    return status


def _ble_backend(config: LightConnectionConfig) -> str:
    return "direct" if config.ble_in_process else config.ble_backend


def _report_to_dict(report: object) -> dict[str, object]:
    to_dict = getattr(report, "to_dict", None)
    if not callable(to_dict):
        raise TypeError("status reader must return an object with to_dict()")
    payload = to_dict()
    if not isinstance(payload, dict):
        raise TypeError("status reader to_dict() must return a dict")
    return {str(key): value for key, value in payload.items()}
