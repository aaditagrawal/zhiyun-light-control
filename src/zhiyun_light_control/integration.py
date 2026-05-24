"""Programmatic integration preflight helpers for media-control hosts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from os import PathLike

from .bridge import (
    LightConnectionConfig,
    LightFactory,
    close_light_factory,
    make_light_factory,
)
from .controller import (
    AsyncLightController,
    AsyncLightFactory,
    LightController,
    open_async_light,
)
from .cues import CueLibrary
from .devices import (
    LightConnectionCandidate,
    connection_candidates_from_devices,
    connection_candidates_from_endpoint_report,
    discover_transport_devices,
    inspect_ble_device,
    test_ble_endpoint_candidates,
)
from .devices import (
    best_connection_candidate as _best_connection_candidate,
)
from .devices import (
    best_connection_config as _best_connection_config,
)
from .discovery import (
    DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS,
    DEFAULT_DISCOVERY_CONTROL_KINDS,
    DEFAULT_DISCOVERY_CONTROL_MODES,
    DEFAULT_DISCOVERY_FIRST_WORDS,
    DEFAULT_DISCOVERY_OBJECT_IDS,
    DEFAULT_DISCOVERY_REGISTER_DEVICE_IDS,
    DEFAULT_DISCOVERY_REGISTER_GROUP_IDS,
    discover_usb_primitives,
)
from .models import Scene
from .presets import ScenePresetLibrary
from .profiles import (
    LightSetupProfile,
    SetupProfileMissing,
    load_light_setup_profile,
    setup_profile_capabilities,
    setup_profile_primitive_readiness_map,
    setup_profile_primitive_ready_for,
)
from .protocol import DEFAULT_CONTROL_MODE, RUNTIME_TYPE
from .server import (
    capabilities_response,
    integration_manifest_response,
    integration_snapshot_response,
    readiness_response,
)
from .state import SceneState, SceneStateTracker
from .status import read_async_status, read_sync_status
from .transports.ble import BleWorkerError
from .validation import validate_async_light, validate_sync_light

StatusSnapshot = tuple[dict[str, object], bool, str | None]


class IntegrationNotReady(RuntimeError):
    def __init__(
        self,
        payload: Mapping[str, object],
        capabilities: Iterable[str],
    ) -> None:
        self.payload = _readiness_payload(payload)
        self.capabilities = _capabilities_or_default(capabilities)
        self.ready_for = integration_ready_for(self.payload)
        self.pending_action_ids = {
            capability: integration_pending_action_ids(
                self.payload,
                capability=capability,
            )
            for capability in self.capabilities
            if not self.ready_for.get(capability, False)
        }
        self.warnings = integration_warnings(self.payload)
        missing = ", ".join(self.pending_action_ids) or ", ".join(self.capabilities)
        super().__init__(f"integration not ready for {missing}")


@dataclass(frozen=True)
class LightIntegration:
    """Reusable setup/preflight facade for embedding in host applications."""

    config: LightConnectionConfig = field(default_factory=LightConnectionConfig)
    allow_control: bool = False
    preset_names: tuple[str, ...] = ()
    cue_names: tuple[str, ...] = ()
    light_factory: LightFactory | None = None
    preset_library: ScenePresetLibrary | None = None
    cue_library: CueLibrary | None = None
    obj: int = 1
    state_tracker: SceneStateTracker = field(default_factory=SceneStateTracker)
    setup_profile_evidence: LightSetupProfile | None = None
    require_setup_profile_controls: bool = False

    def status(self) -> StatusSnapshot:
        return local_status_snapshot(
            self.config,
            light_factory=self.light_factory,
        )

    @classmethod
    def from_setup_profile(
        cls,
        profile: LightSetupProfile,
        *,
        require: str | Iterable[str] = (),
        require_controls: bool = False,
        **options: object,
    ) -> LightIntegration:
        profile.require_ready(*_profile_requirements(require))
        return cls(
            config=profile.config,
            setup_profile_evidence=profile,
            require_setup_profile_controls=require_controls,
            **options,
        )

    @classmethod
    def from_setup_profile_file(
        cls,
        path: str | PathLike[str],
        *,
        require: str | Iterable[str] = (),
        **options: object,
    ) -> LightIntegration:
        return cls.from_setup_profile(
            load_light_setup_profile(path),
            require=require,
            **options,
        )

    def readiness(
        self,
        *,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        resolved_state_version, resolved_state = self._state_inputs(
            state_version=state_version,
            state=state,
        )
        return local_readiness(
            self.config,
            allow_control=self.allow_control,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            state_version=resolved_state_version,
            state=resolved_state,
            light_factory=self.light_factory,
        )

    def ready_for(
        self,
        *,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> dict[str, bool]:
        return integration_ready_for(
            self.readiness(
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                state_version=state_version,
                state=state,
            )
        )

    def ready(
        self,
        capability: str,
        *,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> bool:
        return self.ready_for(
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            state_version=state_version,
            state=state,
        ).get(capability, False)

    def pending_action_ids(
        self,
        *,
        capability: str | None = None,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> list[str]:
        return integration_pending_action_ids(
            self.readiness(
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                state_version=state_version,
                state=state,
            ),
            capability=capability,
        )

    def require_readiness(
        self,
        *capabilities: str,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        payload = self.readiness(
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            state_version=state_version,
            state=state,
        )
        return integration_require(payload, capabilities)

    def require_control_ready(
        self,
        *,
        strict: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        capability = "confirmed_control" if strict else "control_requests"
        return self.require_readiness(
            capability,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            state_version=state_version,
            state=state,
        )

    def manifest(self) -> dict[str, object]:
        return local_manifest(
            self.config,
            allow_control=self.allow_control,
            presets=self._preset_names(),
            cues=self._cue_names(),
        )

    def capabilities(self) -> dict[str, object]:
        return local_capabilities(
            allow_control=self.allow_control,
            presets=self._preset_names(),
            cues=self._cue_names(),
        )

    def state(self) -> dict[str, object]:
        return self.state_tracker.to_dict()

    def state_snapshot(self) -> dict[str, object]:
        return _state_payload(*self.state_tracker.versioned_snapshot())

    def state_history(
        self,
        *,
        after_version: int = 0,
        limit: int | None = None,
    ) -> dict[str, object]:
        return _history_payload(
            self.state_tracker.history(after_version=after_version, limit=limit)
        )

    def wait_for_state_update(
        self,
        after_version: int,
        *,
        timeout: float | None = None,
    ) -> dict[str, object]:
        return _state_payload(
            *self.state_tracker.wait_for_update(after_version, timeout=timeout)
        )

    def devices(
        self,
        *,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
    ) -> dict[str, object]:
        return local_devices(
            self.config,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
        )

    def discover_usb(
        self,
        *,
        object_ids: Iterable[int] = DEFAULT_DISCOVERY_OBJECT_IDS,
        first_words: Iterable[int] = DEFAULT_DISCOVERY_FIRST_WORDS,
        control_object_ids: Iterable[int] | None = None,
        control_first_words: Iterable[int] = DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS,
        register_device_ids: Iterable[int] = DEFAULT_DISCOVERY_REGISTER_DEVICE_IDS,
        register_group_ids: Iterable[int] = DEFAULT_DISCOVERY_REGISTER_GROUP_IDS,
        control_kinds: Iterable[str] = DEFAULT_DISCOVERY_CONTROL_KINDS,
        control_modes: Iterable[int] | None = DEFAULT_DISCOVERY_CONTROL_MODES,
        post_register_reads: bool = False,
        timeout: float | None = None,
        allow_control: bool | None = None,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
    ) -> dict[str, object]:
        return local_usb_discovery(
            self.config,
            light_factory=self.light_factory,
            object_ids=object_ids,
            first_words=first_words,
            control_object_ids=control_object_ids,
            control_first_words=control_first_words,
            register_device_ids=register_device_ids,
            register_group_ids=register_group_ids,
            control_kinds=control_kinds,
            control_modes=control_modes,
            post_register_reads=post_register_reads,
            timeout=timeout,
            allow_control=(
                self.allow_control if allow_control is None else allow_control
            ),
            brightness=brightness,
            kelvin=kelvin,
            sleep=sleep,
        )

    def inspect_ble(
        self,
        *,
        backend: str | None = None,
        timeout: float | None = None,
        address: str | None = None,
        name_contains: str | None = None,
        python: str | None = None,
    ) -> dict[str, object]:
        return local_ble_inspect(
            self.config,
            backend=backend,
            timeout=timeout,
            address=address,
            name_contains=name_contains,
            python=python,
        )

    def test_ble_endpoints(
        self,
        *,
        backend: str | None = None,
        timeout: float | None = None,
        address: str | None = None,
        name_contains: str | None = None,
        python: str | None = None,
        max_candidates: int = 4,
    ) -> dict[str, object]:
        return local_ble_endpoint_test(
            self.config,
            backend=backend,
            timeout=timeout,
            address=address,
            name_contains=name_contains,
            python=python,
            max_candidates=max_candidates,
        )

    def connection_candidates(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> tuple[LightConnectionCandidate, ...]:
        return local_connection_candidates(
            self.config,
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
        )

    def best_connection(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> LightConnectionCandidate:
        return _best_connection_candidate(
            self.connection_candidates(
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
            )
        )

    def best_connection_config(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> LightConnectionConfig:
        return _best_connection_config(
            self.connection_candidates(
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
            )
        )

    def with_config(self, config: LightConnectionConfig) -> LightIntegration:
        return replace(self, config=config, setup_profile_evidence=None)

    def with_setup_profile(
        self,
        profile: LightSetupProfile,
        *,
        require: str | Iterable[str] = (),
        require_controls: bool | None = None,
    ) -> LightIntegration:
        profile.require_ready(*_profile_requirements(require))
        return replace(
            self,
            config=profile.config,
            setup_profile_evidence=profile,
            require_setup_profile_controls=(
                self.require_setup_profile_controls
                if require_controls is None
                else require_controls
            ),
        )

    def require_setup_profile(self, *capabilities: str) -> LightSetupProfile:
        if self.setup_profile_evidence is None:
            raise SetupProfileMissing()
        return self.setup_profile_evidence.require_ready(*capabilities)

    def setup_profile_ready(self, capability: str) -> bool:
        return (
            self.setup_profile_evidence is not None
            and self.setup_profile_evidence.ready(capability)
        )

    def setup_profile_primitive_ready(self, primitive: str) -> bool:
        return (
            self.setup_profile_evidence is not None
            and self.setup_profile_evidence.primitive_ready(primitive)
        )

    def require_setup_profile_primitive(self, primitive: str) -> LightSetupProfile:
        if self.setup_profile_evidence is None:
            raise SetupProfileMissing()
        return self.setup_profile_evidence.require_primitive(primitive)

    def with_best_connection(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> LightIntegration:
        return self.with_config(
            self.best_connection_config(
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
            )
        )

    def probe_connection_candidates(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        confirmed_only: bool = False,
    ) -> tuple[LightConnectionCandidate, ...]:
        return local_probe_connection_candidates(
            self.config,
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            confirmed_only=confirmed_only,
        )

    def confirmed_connection_candidates(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> tuple[LightConnectionCandidate, ...]:
        return self.probe_connection_candidates(
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            confirmed_only=True,
        )

    def best_confirmed_connection(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> LightConnectionCandidate:
        return _best_connection_candidate(
            self.confirmed_connection_candidates(
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
            )
        )

    def best_confirmed_connection_config(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> LightConnectionConfig:
        return self.best_confirmed_connection(
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
        ).config

    def with_confirmed_connection(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> LightIntegration:
        return self.with_config(
            self.best_confirmed_connection_config(
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
            )
        )

    def setup_report(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        require_confirmed_route: bool = True,
        allow_control: bool | None = None,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        obj: int | None = None,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> dict[str, object]:
        return local_setup_report(
            self.config,
            allow_control=(
                self.allow_control if allow_control is None else allow_control
            ),
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            require_confirmed_route=require_confirmed_route,
            include_object_reads=include_object_reads,
            include_color=include_color,
            device_id=device_id,
            obj=self._obj(obj),
            brightness=brightness,
            kelvin=kelvin,
            sleep=sleep,
            red=red,
            green=green,
            blue=blue,
            hue=hue,
            saturation=saturation,
            intensity=intensity,
            control_mode=control_mode,
        )

    def setup_profile(self, **options: object) -> LightSetupProfile:
        return LightSetupProfile.from_setup_report(self.setup_report(**options))

    def ble_endpoint_connection_candidates(
        self,
        *,
        backend: str | None = None,
        timeout: float | None = None,
        address: str | None = None,
        name_contains: str | None = None,
        python: str | None = None,
        max_candidates: int = 4,
        persistent: bool = False,
        require_confirmed: bool = True,
    ) -> tuple[LightConnectionCandidate, ...]:
        return local_ble_endpoint_connection_candidates(
            self.config,
            backend=backend,
            timeout=timeout,
            address=address,
            name_contains=name_contains,
            python=python,
            max_candidates=max_candidates,
            persistent=persistent,
            require_confirmed=require_confirmed,
        )

    def best_ble_endpoint_config(
        self,
        *,
        backend: str | None = None,
        timeout: float | None = None,
        address: str | None = None,
        name_contains: str | None = None,
        python: str | None = None,
        max_candidates: int = 4,
        persistent: bool = False,
        require_confirmed: bool = True,
    ) -> LightConnectionConfig:
        return _best_connection_config(
            self.ble_endpoint_connection_candidates(
                backend=backend,
                timeout=timeout,
                address=address,
                name_contains=name_contains,
                python=python,
                max_candidates=max_candidates,
                persistent=persistent,
                require_confirmed=require_confirmed,
            )
        )

    def with_ble_endpoint_connection(
        self,
        *,
        backend: str | None = None,
        timeout: float | None = None,
        address: str | None = None,
        name_contains: str | None = None,
        python: str | None = None,
        max_candidates: int = 4,
        persistent: bool = False,
        require_confirmed: bool = True,
    ) -> LightIntegration:
        return self.with_config(
            self.best_ble_endpoint_config(
                backend=backend,
                timeout=timeout,
                address=address,
                name_contains=name_contains,
                python=python,
                max_candidates=max_candidates,
                persistent=persistent,
                require_confirmed=require_confirmed,
            )
        )

    def controller(
        self,
        *,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        state_tracker: SceneStateTracker | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
    ) -> LightController:
        return LightController(
            self.config,
            light_factory=self.light_factory,
            preset_library=self._preset_library(preset_library),
            cue_library=self._cue_library(cue_library),
            state_tracker=self._state_tracker(state_tracker),
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        )

    def _call_controller(
        self,
        method: str,
        *args: object,
        controller_preset_library: ScenePresetLibrary | None = None,
        controller_cue_library: CueLibrary | None = None,
        controller_state_tracker: SceneStateTracker | None = None,
        controller_control_mode: int = DEFAULT_CONTROL_MODE,
        controller_require_acknowledged: bool = False,
        **kwargs: object,
    ) -> object:
        controller = self.controller(
            preset_library=controller_preset_library,
            cue_library=controller_cue_library,
            state_tracker=controller_state_tracker,
            control_mode=controller_control_mode,
            require_acknowledged=controller_require_acknowledged,
        )
        try:
            return getattr(controller, method)(*args, **kwargs)
        finally:
            if self.light_factory is None:
                controller.close()

    def register(
        self,
        device_id: int = 0,
        group_id: int = 0,
        *,
        require_acknowledged: bool = False,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "register",
            require_setup_profile,
        )
        return self._call_controller(
            "register",
            controller_require_acknowledged=require_acknowledged,
            device_id=device_id,
            group_id=group_id,
            require_acknowledged=require_acknowledged,
        )

    def read_brightness(
        self,
        *,
        obj: int | None = None,
        require_acknowledged: bool = False,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "read_brightness",
            require_setup_profile,
        )
        return self._call_controller(
            "read_brightness",
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    def read_cct(
        self,
        *,
        obj: int | None = None,
        require_acknowledged: bool = False,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "read_cct",
            require_setup_profile,
        )
        return self._call_controller(
            "read_cct",
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    def read_sleep(
        self,
        *,
        obj: int | None = None,
        require_acknowledged: bool = False,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "read_sleep",
            require_setup_profile,
        )
        return self._call_controller(
            "read_sleep",
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    def set_brightness(
        self,
        value: float,
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_brightness",
            require_setup_profile,
        )
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self._call_controller(
            "set_brightness",
            value,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    def set_cct(
        self,
        kelvin: int,
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_cct",
            require_setup_profile,
        )
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self._call_controller(
            "set_cct",
            kelvin,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    def set_sleep(
        self,
        value: int,
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_sleep",
            require_setup_profile,
        )
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self._call_controller(
            "set_sleep",
            value,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    def set_rgb(
        self,
        red: int,
        green: int,
        blue: int,
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_rgb",
            require_setup_profile,
        )
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self._call_controller(
            "set_rgb",
            red,
            green,
            blue,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    def set_hsi(
        self,
        hue: float,
        saturation: float,
        intensity: int,
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_hsi",
            require_setup_profile,
        )
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self._call_controller(
            "set_hsi",
            hue,
            saturation,
            intensity,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    def apply_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "scene",
            require_setup_profile,
        )
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self._call_controller(
            "apply_scene",
            _integration_scene_payload(scene, obj=self._obj(obj)),
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            require_acknowledged=require_acknowledged,
        )

    def apply_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        obj: int | None = None,
        preset_library: ScenePresetLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "preset",
            require_setup_profile,
        )
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self._call_controller(
            "apply_preset",
            name,
            controller_preset_library=preset_library,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            overrides=overrides,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    def run_sequence(
        self,
        steps: Iterable[Mapping[str, object]],
        *,
        obj: int | None = None,
        stop_on_unconfirmed: bool = False,
        preset_library: ScenePresetLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "sequence",
            require_setup_profile,
        )
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self._call_controller(
            "run_sequence",
            steps,
            controller_preset_library=preset_library,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            stop_on_unconfirmed=stop_on_unconfirmed,
            require_acknowledged=require_acknowledged,
        )

    def run_cue(
        self,
        cue: Mapping[str, object],
        *,
        obj: int | None = None,
        preset_library: ScenePresetLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "cue",
            require_setup_profile,
        )
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self._call_controller(
            "run_cue",
            cue,
            controller_preset_library=preset_library,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    def run_named_cue(
        self,
        name: str,
        *,
        obj: int | None = None,
        stop_on_unconfirmed: bool | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "run_named_cue",
            require_setup_profile,
        )
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self._call_controller(
            "run_named_cue",
            name,
            controller_preset_library=preset_library,
            controller_cue_library=cue_library,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            stop_on_unconfirmed=stop_on_unconfirmed,
            require_acknowledged=require_acknowledged,
        )

    def plan_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return self._call_controller(
            "plan_scene",
            scene,
            controller_control_mode=control_mode,
            obj=self._obj(obj),
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        obj: int | None = None,
        preset_library: ScenePresetLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return self._call_controller(
            "plan_preset",
            name,
            controller_preset_library=preset_library,
            controller_control_mode=control_mode,
            overrides=overrides,
            obj=self._obj(obj),
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_transition(
        self,
        to_scene: Scene | Mapping[str, object],
        *,
        from_scene: Scene | Mapping[str, object] | None = None,
        obj: int | None = None,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int = DEFAULT_CONTROL_MODE,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return self._call_controller(
            "plan_transition",
            to_scene,
            controller_control_mode=control_mode,
            from_scene=from_scene,
            obj=self._obj(obj),
            steps=steps,
            duration=duration,
            easing=easing,
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_sequence(
        self,
        steps: Iterable[Mapping[str, object]],
        *,
        obj: int | None = None,
        stop_on_unconfirmed: bool = False,
        preset_library: ScenePresetLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return self._call_controller(
            "plan_sequence",
            steps,
            controller_preset_library=preset_library,
            controller_control_mode=control_mode,
            obj=self._obj(obj),
            stop_on_unconfirmed=stop_on_unconfirmed,
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_cue(
        self,
        cue: Mapping[str, object],
        *,
        obj: int | None = None,
        stop_on_unconfirmed: bool | None = None,
        preset_library: ScenePresetLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return self._call_controller(
            "plan_cue",
            cue,
            controller_preset_library=preset_library,
            controller_control_mode=control_mode,
            obj=self._obj(obj),
            stop_on_unconfirmed=stop_on_unconfirmed,
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_named_cue(
        self,
        name: str,
        *,
        obj: int | None = None,
        stop_on_unconfirmed: bool | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return self._call_controller(
            "plan_named_cue",
            name,
            controller_preset_library=preset_library,
            controller_cue_library=cue_library,
            controller_control_mode=control_mode,
            obj=self._obj(obj),
            stop_on_unconfirmed=stop_on_unconfirmed,
            first_word=first_word,
            start_seq=start_seq,
        )

    def snapshot(
        self,
        *,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        resolved_state_version, resolved_state = self._state_inputs(
            state_version=state_version,
            state=state,
        )
        return local_integration_snapshot(
            self.config,
            allow_control=self.allow_control,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            presets=self._preset_names(),
            cues=self._cue_names(),
            state_version=resolved_state_version,
            state=resolved_state,
            light_factory=self.light_factory,
        )

    def validate(
        self,
        *,
        allow_control: bool | None = None,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        obj: int = 1,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> dict[str, object]:
        control_enabled = (
            self.allow_control if allow_control is None else allow_control
        )
        return local_validation(
            self.config,
            allow_control=control_enabled,
            include_object_reads=include_object_reads,
            include_color=include_color,
            device_id=device_id,
            obj=obj,
            brightness=brightness,
            kelvin=kelvin,
            sleep=sleep,
            red=red,
            green=green,
            blue=blue,
            hue=hue,
            saturation=saturation,
            intensity=intensity,
            control_mode=control_mode,
            light_factory=self.light_factory,
        )

    def _preset_library(
        self,
        explicit: ScenePresetLibrary | None,
    ) -> ScenePresetLibrary | None:
        return self.preset_library if explicit is None else explicit

    def _cue_library(self, explicit: CueLibrary | None) -> CueLibrary | None:
        return self.cue_library if explicit is None else explicit

    def _state_tracker(
        self,
        explicit: SceneStateTracker | None,
    ) -> SceneStateTracker:
        return self.state_tracker if explicit is None else explicit

    def _preset_names(self) -> tuple[str, ...]:
        return _integration_preset_names(self.preset_names, self.preset_library)

    def _cue_names(self) -> tuple[str, ...]:
        return _integration_cue_names(self.cue_names, self.cue_library)

    def _obj(self, explicit: int | None) -> int:
        return self.obj if explicit is None else explicit

    def _state_inputs(
        self,
        *,
        state_version: int,
        state: Mapping[str, object] | None,
    ) -> tuple[int, Mapping[str, object] | None]:
        if state is not None or state_version != 0:
            return state_version, state
        snapshot = self.state_snapshot()
        return _state_version(snapshot), _state_from_snapshot(snapshot)

    def _require_control_readiness(
        self,
        require_ready: bool,
        required_readiness: Iterable[str] | None,
        *,
        require_acknowledged: bool,
    ) -> None:
        if not require_ready and required_readiness is None:
            return
        self.require_readiness(
            *_control_readiness_capabilities(
                required_readiness,
                require_acknowledged=require_acknowledged,
            )
        )

    def _require_setup_profile_primitive_if_requested(
        self,
        primitive: str,
        require_setup_profile: bool,
    ) -> None:
        if require_setup_profile or self.require_setup_profile_controls:
            self.require_setup_profile_primitive(primitive)


@dataclass(frozen=True)
class AsyncLightIntegration:
    """Async setup/preflight facade for BLE-native host applications."""

    config: LightConnectionConfig = field(
        default_factory=lambda: LightConnectionConfig(transport="ble")
    )
    allow_control: bool = False
    preset_names: tuple[str, ...] = ()
    cue_names: tuple[str, ...] = ()
    light_factory: AsyncLightFactory | None = None
    preset_library: ScenePresetLibrary | None = None
    cue_library: CueLibrary | None = None
    obj: int = 1
    state_tracker: SceneStateTracker = field(default_factory=SceneStateTracker)
    setup_profile_evidence: LightSetupProfile | None = None
    require_setup_profile_controls: bool = False

    async def status(self) -> StatusSnapshot:
        return await local_async_status_snapshot(
            self.config,
            light_factory=self.light_factory,
        )

    @classmethod
    def from_setup_profile(
        cls,
        profile: LightSetupProfile,
        *,
        require: str | Iterable[str] = (),
        require_controls: bool = False,
        **options: object,
    ) -> AsyncLightIntegration:
        profile.require_ready(*_profile_requirements(require))
        return cls(
            config=profile.config,
            setup_profile_evidence=profile,
            require_setup_profile_controls=require_controls,
            **options,
        )

    @classmethod
    def from_setup_profile_file(
        cls,
        path: str | PathLike[str],
        *,
        require: str | Iterable[str] = (),
        **options: object,
    ) -> AsyncLightIntegration:
        return cls.from_setup_profile(
            load_light_setup_profile(path),
            require=require,
            **options,
        )

    async def readiness(
        self,
        *,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        resolved_state_version, resolved_state = self._state_inputs(
            state_version=state_version,
            state=state,
        )
        return await local_async_readiness(
            self.config,
            allow_control=self.allow_control,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            state_version=resolved_state_version,
            state=resolved_state,
            light_factory=self.light_factory,
        )

    async def ready_for(
        self,
        *,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> dict[str, bool]:
        return integration_ready_for(
            await self.readiness(
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                state_version=state_version,
                state=state,
            )
        )

    async def ready(
        self,
        capability: str,
        *,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> bool:
        return (
            await self.ready_for(
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                state_version=state_version,
                state=state,
            )
        ).get(capability, False)

    async def pending_action_ids(
        self,
        *,
        capability: str | None = None,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> list[str]:
        return integration_pending_action_ids(
            await self.readiness(
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                state_version=state_version,
                state=state,
            ),
            capability=capability,
        )

    async def require_readiness(
        self,
        *capabilities: str,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        payload = await self.readiness(
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            state_version=state_version,
            state=state,
        )
        return integration_require(payload, capabilities)

    async def require_control_ready(
        self,
        *,
        strict: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        capability = "confirmed_control" if strict else "control_requests"
        return await self.require_readiness(
            capability,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            state_version=state_version,
            state=state,
        )

    def manifest(self) -> dict[str, object]:
        return local_manifest(
            self.config,
            allow_control=self.allow_control,
            presets=self._preset_names(),
            cues=self._cue_names(),
        )

    def capabilities(self) -> dict[str, object]:
        return local_capabilities(
            allow_control=self.allow_control,
            presets=self._preset_names(),
            cues=self._cue_names(),
        )

    def state(self) -> dict[str, object]:
        return self.state_tracker.to_dict()

    def state_snapshot(self) -> dict[str, object]:
        return _state_payload(*self.state_tracker.versioned_snapshot())

    def state_history(
        self,
        *,
        after_version: int = 0,
        limit: int | None = None,
    ) -> dict[str, object]:
        return _history_payload(
            self.state_tracker.history(after_version=after_version, limit=limit)
        )

    async def wait_for_state_update(
        self,
        after_version: int,
        *,
        timeout: float | None = None,
    ) -> dict[str, object]:
        version, state = await asyncio.to_thread(
            self.state_tracker.wait_for_update,
            after_version,
            timeout=timeout,
        )
        return _state_payload(version, state)

    async def devices(
        self,
        *,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
    ) -> dict[str, object]:
        return await local_async_devices(
            self.config,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
        )

    async def discover_usb(
        self,
        *,
        object_ids: Iterable[int] = DEFAULT_DISCOVERY_OBJECT_IDS,
        first_words: Iterable[int] = DEFAULT_DISCOVERY_FIRST_WORDS,
        control_object_ids: Iterable[int] | None = None,
        control_first_words: Iterable[int] = DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS,
        register_device_ids: Iterable[int] = DEFAULT_DISCOVERY_REGISTER_DEVICE_IDS,
        register_group_ids: Iterable[int] = DEFAULT_DISCOVERY_REGISTER_GROUP_IDS,
        control_kinds: Iterable[str] = DEFAULT_DISCOVERY_CONTROL_KINDS,
        control_modes: Iterable[int] | None = DEFAULT_DISCOVERY_CONTROL_MODES,
        post_register_reads: bool = False,
        timeout: float | None = None,
        allow_control: bool | None = None,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
    ) -> dict[str, object]:
        return await local_async_usb_discovery(
            self.config,
            light_factory=None,
            object_ids=object_ids,
            first_words=first_words,
            control_object_ids=control_object_ids,
            control_first_words=control_first_words,
            register_device_ids=register_device_ids,
            register_group_ids=register_group_ids,
            control_kinds=control_kinds,
            control_modes=control_modes,
            post_register_reads=post_register_reads,
            timeout=timeout,
            allow_control=(
                self.allow_control if allow_control is None else allow_control
            ),
            brightness=brightness,
            kelvin=kelvin,
            sleep=sleep,
        )

    async def inspect_ble(
        self,
        *,
        backend: str | None = None,
        timeout: float | None = None,
        address: str | None = None,
        name_contains: str | None = None,
        python: str | None = None,
    ) -> dict[str, object]:
        return await local_async_ble_inspect(
            self.config,
            backend=backend,
            timeout=timeout,
            address=address,
            name_contains=name_contains,
            python=python,
        )

    async def test_ble_endpoints(
        self,
        *,
        backend: str | None = None,
        timeout: float | None = None,
        address: str | None = None,
        name_contains: str | None = None,
        python: str | None = None,
        max_candidates: int = 4,
    ) -> dict[str, object]:
        return await local_async_ble_endpoint_test(
            self.config,
            backend=backend,
            timeout=timeout,
            address=address,
            name_contains=name_contains,
            python=python,
            max_candidates=max_candidates,
        )

    async def connection_candidates(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> tuple[LightConnectionCandidate, ...]:
        return await local_async_connection_candidates(
            self.config,
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
        )

    async def best_connection(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> LightConnectionCandidate:
        return _best_connection_candidate(
            await self.connection_candidates(
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
            )
        )

    async def best_connection_config(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> LightConnectionConfig:
        return _best_connection_config(
            await self.connection_candidates(
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
            )
        )

    def with_config(self, config: LightConnectionConfig) -> AsyncLightIntegration:
        return replace(self, config=config, setup_profile_evidence=None)

    def with_setup_profile(
        self,
        profile: LightSetupProfile,
        *,
        require: str | Iterable[str] = (),
        require_controls: bool | None = None,
    ) -> AsyncLightIntegration:
        profile.require_ready(*_profile_requirements(require))
        return replace(
            self,
            config=profile.config,
            setup_profile_evidence=profile,
            require_setup_profile_controls=(
                self.require_setup_profile_controls
                if require_controls is None
                else require_controls
            ),
        )

    def require_setup_profile(self, *capabilities: str) -> LightSetupProfile:
        if self.setup_profile_evidence is None:
            raise SetupProfileMissing()
        return self.setup_profile_evidence.require_ready(*capabilities)

    def setup_profile_ready(self, capability: str) -> bool:
        return (
            self.setup_profile_evidence is not None
            and self.setup_profile_evidence.ready(capability)
        )

    def setup_profile_primitive_ready(self, primitive: str) -> bool:
        return (
            self.setup_profile_evidence is not None
            and self.setup_profile_evidence.primitive_ready(primitive)
        )

    def require_setup_profile_primitive(self, primitive: str) -> LightSetupProfile:
        if self.setup_profile_evidence is None:
            raise SetupProfileMissing()
        return self.setup_profile_evidence.require_primitive(primitive)

    async def with_best_connection(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> AsyncLightIntegration:
        return self.with_config(
            await self.best_connection_config(
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
            )
        )

    async def probe_connection_candidates(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        confirmed_only: bool = False,
    ) -> tuple[LightConnectionCandidate, ...]:
        return await local_async_probe_connection_candidates(
            self.config,
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            confirmed_only=confirmed_only,
        )

    async def confirmed_connection_candidates(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> tuple[LightConnectionCandidate, ...]:
        return await self.probe_connection_candidates(
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            confirmed_only=True,
        )

    async def best_confirmed_connection(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> LightConnectionCandidate:
        return _best_connection_candidate(
            await self.confirmed_connection_candidates(
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
            )
        )

    async def best_confirmed_connection_config(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> LightConnectionConfig:
        return (
            await self.best_confirmed_connection(
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
            )
        ).config

    async def with_confirmed_connection(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
    ) -> AsyncLightIntegration:
        return self.with_config(
            await self.best_confirmed_connection_config(
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
            )
        )

    async def setup_report(
        self,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        require_confirmed_route: bool = True,
        allow_control: bool | None = None,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        obj: int | None = None,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> dict[str, object]:
        return await local_async_setup_report(
            self.config,
            allow_control=(
                self.allow_control if allow_control is None else allow_control
            ),
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            require_confirmed_route=require_confirmed_route,
            include_object_reads=include_object_reads,
            include_color=include_color,
            device_id=device_id,
            obj=self._obj(obj),
            brightness=brightness,
            kelvin=kelvin,
            sleep=sleep,
            red=red,
            green=green,
            blue=blue,
            hue=hue,
            saturation=saturation,
            intensity=intensity,
            control_mode=control_mode,
        )

    async def setup_profile(self, **options: object) -> LightSetupProfile:
        return LightSetupProfile.from_setup_report(
            await self.setup_report(**options)
        )

    async def ble_endpoint_connection_candidates(
        self,
        *,
        backend: str | None = None,
        timeout: float | None = None,
        address: str | None = None,
        name_contains: str | None = None,
        python: str | None = None,
        max_candidates: int = 4,
        persistent: bool = False,
        require_confirmed: bool = True,
    ) -> tuple[LightConnectionCandidate, ...]:
        return await local_async_ble_endpoint_connection_candidates(
            self.config,
            backend=backend,
            timeout=timeout,
            address=address,
            name_contains=name_contains,
            python=python,
            max_candidates=max_candidates,
            persistent=persistent,
            require_confirmed=require_confirmed,
        )

    async def best_ble_endpoint_config(
        self,
        *,
        backend: str | None = None,
        timeout: float | None = None,
        address: str | None = None,
        name_contains: str | None = None,
        python: str | None = None,
        max_candidates: int = 4,
        persistent: bool = False,
        require_confirmed: bool = True,
    ) -> LightConnectionConfig:
        return _best_connection_config(
            await self.ble_endpoint_connection_candidates(
                backend=backend,
                timeout=timeout,
                address=address,
                name_contains=name_contains,
                python=python,
                max_candidates=max_candidates,
                persistent=persistent,
                require_confirmed=require_confirmed,
            )
        )

    async def with_ble_endpoint_connection(
        self,
        *,
        backend: str | None = None,
        timeout: float | None = None,
        address: str | None = None,
        name_contains: str | None = None,
        python: str | None = None,
        max_candidates: int = 4,
        persistent: bool = False,
        require_confirmed: bool = True,
    ) -> AsyncLightIntegration:
        return self.with_config(
            await self.best_ble_endpoint_config(
                backend=backend,
                timeout=timeout,
                address=address,
                name_contains=name_contains,
                python=python,
                max_candidates=max_candidates,
                persistent=persistent,
                require_confirmed=require_confirmed,
            )
        )

    def controller(
        self,
        *,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        state_tracker: SceneStateTracker | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
    ) -> AsyncLightController:
        return AsyncLightController(
            self.config,
            light_factory=self.light_factory,
            preset_library=self._preset_library(preset_library),
            cue_library=self._cue_library(cue_library),
            state_tracker=self._state_tracker(state_tracker),
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        )

    async def _call_controller(
        self,
        method: str,
        *args: object,
        controller_preset_library: ScenePresetLibrary | None = None,
        controller_cue_library: CueLibrary | None = None,
        controller_state_tracker: SceneStateTracker | None = None,
        controller_control_mode: int = DEFAULT_CONTROL_MODE,
        controller_require_acknowledged: bool = False,
        **kwargs: object,
    ) -> object:
        controller = self.controller(
            preset_library=controller_preset_library,
            cue_library=controller_cue_library,
            state_tracker=controller_state_tracker,
            control_mode=controller_control_mode,
            require_acknowledged=controller_require_acknowledged,
        )
        try:
            return await getattr(controller, method)(*args, **kwargs)
        finally:
            if self.light_factory is None:
                await controller.close()

    async def register(
        self,
        device_id: int = 0,
        group_id: int = 0,
        *,
        require_acknowledged: bool = False,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "register",
            require_setup_profile,
        )
        return await self._call_controller(
            "register",
            controller_require_acknowledged=require_acknowledged,
            device_id=device_id,
            group_id=group_id,
            require_acknowledged=require_acknowledged,
        )

    async def read_brightness(
        self,
        *,
        obj: int | None = None,
        require_acknowledged: bool = False,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "read_brightness",
            require_setup_profile,
        )
        return await self._call_controller(
            "read_brightness",
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    async def read_cct(
        self,
        *,
        obj: int | None = None,
        require_acknowledged: bool = False,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "read_cct",
            require_setup_profile,
        )
        return await self._call_controller(
            "read_cct",
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    async def read_sleep(
        self,
        *,
        obj: int | None = None,
        require_acknowledged: bool = False,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "read_sleep",
            require_setup_profile,
        )
        return await self._call_controller(
            "read_sleep",
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    async def set_brightness(
        self,
        value: float,
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_brightness",
            require_setup_profile,
        )
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self._call_controller(
            "set_brightness",
            value,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    async def set_cct(
        self,
        kelvin: int,
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_cct",
            require_setup_profile,
        )
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self._call_controller(
            "set_cct",
            kelvin,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    async def set_sleep(
        self,
        value: int,
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_sleep",
            require_setup_profile,
        )
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self._call_controller(
            "set_sleep",
            value,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    async def set_rgb(
        self,
        red: int,
        green: int,
        blue: int,
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_rgb",
            require_setup_profile,
        )
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self._call_controller(
            "set_rgb",
            red,
            green,
            blue,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    async def set_hsi(
        self,
        hue: float,
        saturation: float,
        intensity: int,
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_hsi",
            require_setup_profile,
        )
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self._call_controller(
            "set_hsi",
            hue,
            saturation,
            intensity,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    async def apply_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "scene",
            require_setup_profile,
        )
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self._call_controller(
            "apply_scene",
            _integration_scene_payload(scene, obj=self._obj(obj)),
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            require_acknowledged=require_acknowledged,
        )

    async def apply_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        obj: int | None = None,
        preset_library: ScenePresetLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "preset",
            require_setup_profile,
        )
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self._call_controller(
            "apply_preset",
            name,
            controller_preset_library=preset_library,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            overrides=overrides,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    async def run_sequence(
        self,
        steps: Iterable[Mapping[str, object]],
        *,
        obj: int | None = None,
        stop_on_unconfirmed: bool = False,
        preset_library: ScenePresetLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "sequence",
            require_setup_profile,
        )
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self._call_controller(
            "run_sequence",
            steps,
            controller_preset_library=preset_library,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            stop_on_unconfirmed=stop_on_unconfirmed,
            require_acknowledged=require_acknowledged,
        )

    async def run_cue(
        self,
        cue: Mapping[str, object],
        *,
        obj: int | None = None,
        preset_library: ScenePresetLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "cue",
            require_setup_profile,
        )
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self._call_controller(
            "run_cue",
            cue,
            controller_preset_library=preset_library,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            require_acknowledged=require_acknowledged,
        )

    async def run_named_cue(
        self,
        name: str,
        *,
        obj: int | None = None,
        stop_on_unconfirmed: bool | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "run_named_cue",
            require_setup_profile,
        )
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self._call_controller(
            "run_named_cue",
            name,
            controller_preset_library=preset_library,
            controller_cue_library=cue_library,
            controller_control_mode=control_mode,
            controller_require_acknowledged=require_acknowledged,
            obj=self._obj(obj),
            stop_on_unconfirmed=stop_on_unconfirmed,
            require_acknowledged=require_acknowledged,
        )

    def plan_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return self.controller(control_mode=control_mode).plan_scene(
            scene,
            obj=self._obj(obj),
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        obj: int | None = None,
        preset_library: ScenePresetLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return self.controller(
            preset_library=preset_library,
            control_mode=control_mode,
        ).plan_preset(
            name,
            overrides=overrides,
            obj=self._obj(obj),
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_transition(
        self,
        to_scene: Scene | Mapping[str, object],
        *,
        from_scene: Scene | Mapping[str, object] | None = None,
        obj: int | None = None,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int = DEFAULT_CONTROL_MODE,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return self.controller(control_mode=control_mode).plan_transition(
            to_scene,
            from_scene=from_scene,
            obj=self._obj(obj),
            steps=steps,
            duration=duration,
            easing=easing,
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_sequence(
        self,
        steps: Iterable[Mapping[str, object]],
        *,
        obj: int | None = None,
        stop_on_unconfirmed: bool = False,
        preset_library: ScenePresetLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return self.controller(
            preset_library=preset_library,
            control_mode=control_mode,
        ).plan_sequence(
            steps,
            obj=self._obj(obj),
            stop_on_unconfirmed=stop_on_unconfirmed,
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_cue(
        self,
        cue: Mapping[str, object],
        *,
        obj: int | None = None,
        stop_on_unconfirmed: bool | None = None,
        preset_library: ScenePresetLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return self.controller(
            preset_library=preset_library,
            control_mode=control_mode,
        ).plan_cue(
            cue,
            obj=self._obj(obj),
            stop_on_unconfirmed=stop_on_unconfirmed,
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_named_cue(
        self,
        name: str,
        *,
        obj: int | None = None,
        stop_on_unconfirmed: bool | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return self.controller(
            preset_library=preset_library,
            cue_library=cue_library,
            control_mode=control_mode,
        ).plan_named_cue(
            name,
            obj=self._obj(obj),
            stop_on_unconfirmed=stop_on_unconfirmed,
            first_word=first_word,
            start_seq=start_seq,
        )

    async def snapshot(
        self,
        *,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        state_version: int = 0,
        state: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        resolved_state_version, resolved_state = self._state_inputs(
            state_version=state_version,
            state=state,
        )
        return await local_async_integration_snapshot(
            self.config,
            allow_control=self.allow_control,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            presets=self._preset_names(),
            cues=self._cue_names(),
            state_version=resolved_state_version,
            state=resolved_state,
            light_factory=self.light_factory,
        )

    async def validate(
        self,
        *,
        allow_control: bool | None = None,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        obj: int = 1,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> dict[str, object]:
        control_enabled = (
            self.allow_control if allow_control is None else allow_control
        )
        return await local_async_validation(
            self.config,
            allow_control=control_enabled,
            include_object_reads=include_object_reads,
            include_color=include_color,
            device_id=device_id,
            obj=obj,
            brightness=brightness,
            kelvin=kelvin,
            sleep=sleep,
            red=red,
            green=green,
            blue=blue,
            hue=hue,
            saturation=saturation,
            intensity=intensity,
            control_mode=control_mode,
            light_factory=self.light_factory,
        )

    def _preset_library(
        self,
        explicit: ScenePresetLibrary | None,
    ) -> ScenePresetLibrary | None:
        return self.preset_library if explicit is None else explicit

    def _cue_library(self, explicit: CueLibrary | None) -> CueLibrary | None:
        return self.cue_library if explicit is None else explicit

    def _state_tracker(
        self,
        explicit: SceneStateTracker | None,
    ) -> SceneStateTracker:
        return self.state_tracker if explicit is None else explicit

    def _preset_names(self) -> tuple[str, ...]:
        return _integration_preset_names(self.preset_names, self.preset_library)

    def _cue_names(self) -> tuple[str, ...]:
        return _integration_cue_names(self.cue_names, self.cue_library)

    def _obj(self, explicit: int | None) -> int:
        return self.obj if explicit is None else explicit

    def _state_inputs(
        self,
        *,
        state_version: int,
        state: Mapping[str, object] | None,
    ) -> tuple[int, Mapping[str, object] | None]:
        if state is not None or state_version != 0:
            return state_version, state
        snapshot = self.state_snapshot()
        return _state_version(snapshot), _state_from_snapshot(snapshot)

    async def _require_control_readiness(
        self,
        require_ready: bool,
        required_readiness: Iterable[str] | None,
        *,
        require_acknowledged: bool,
    ) -> None:
        if not require_ready and required_readiness is None:
            return
        await self.require_readiness(
            *_control_readiness_capabilities(
                required_readiness,
                require_acknowledged=require_acknowledged,
            )
        )

    def _require_setup_profile_primitive_if_requested(
        self,
        primitive: str,
        require_setup_profile: bool,
    ) -> None:
        if require_setup_profile or self.require_setup_profile_controls:
            self.require_setup_profile_primitive(primitive)


def _integration_preset_names(
    names: Iterable[str],
    library: ScenePresetLibrary | None,
) -> tuple[str, ...]:
    explicit = tuple(names)
    return explicit if explicit else () if library is None else tuple(library.names())


def _integration_cue_names(
    names: Iterable[str],
    library: CueLibrary | None,
) -> tuple[str, ...]:
    explicit = tuple(names)
    return explicit if explicit else () if library is None else tuple(library.names())


def _state_payload(version: int, state: SceneState | None) -> dict[str, object]:
    return {
        "version": version,
        "state": {"scene": None} if state is None else state.to_dict(),
    }


def _history_payload(history: tuple[tuple[int, SceneState], ...]) -> dict[str, object]:
    return {
        "events": [
            {
                "version": version,
                "state": state.to_dict(),
            }
            for version, state in history
        ]
    }


def _state_version(snapshot: Mapping[str, object]) -> int:
    raw_version = snapshot.get("version")
    return raw_version if isinstance(raw_version, int) else 0


def _state_from_snapshot(snapshot: Mapping[str, object]) -> Mapping[str, object] | None:
    raw_state = snapshot.get("state")
    return raw_state if isinstance(raw_state, Mapping) else None


def _integration_scene_payload(
    scene: Scene | Mapping[str, object],
    *,
    obj: int,
) -> Scene | dict[str, object]:
    if isinstance(scene, Scene):
        return scene
    payload = dict(scene)
    payload.setdefault("obj", obj)
    return payload


def _control_readiness_capabilities(
    required_readiness: Iterable[str] | None,
    *,
    require_acknowledged: bool,
) -> tuple[str, ...]:
    if required_readiness is not None:
        return tuple(required_readiness)
    if require_acknowledged:
        return ("confirmed_control",)
    return ("control_requests",)


def local_status_snapshot(
    config: LightConnectionConfig | None = None,
    *,
    light_factory: LightFactory | None = None,
    status_reader: Callable[[object], object] | None = None,
) -> StatusSnapshot:
    resolved = config or LightConnectionConfig()
    factory: LightFactory | None = None
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
    finally:
        if light_factory is None and factory is not None:
            close_light_factory(factory)
    payload = _report_to_dict(report)
    return payload, payload.get("connection_confirmed") is True, None


def local_devices(
    config: LightConnectionConfig | None = None,
    *,
    include_ble: bool = False,
    include_ble_status: bool | None = None,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig()
    return discover_transport_devices(
        configured_transport=resolved.transport,
        configured_usb_port=resolved.port,
        include_ble=include_ble,
        include_ble_status=_include_ble_status(resolved, include_ble_status),
        ble_backend=_ble_backend(resolved),
        ble_timeout=resolved.timeout,
        ble_name_contains=resolved.name_contains,
        ble_python=resolved.ble_python,
    )


async def local_async_devices(
    config: LightConnectionConfig | None = None,
    *,
    include_ble: bool = False,
    include_ble_status: bool | None = None,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig(transport="ble")
    return await asyncio.to_thread(
        local_devices,
        resolved,
        include_ble=include_ble,
        include_ble_status=include_ble_status,
    )


def local_connection_candidates(
    config: LightConnectionConfig | None = None,
    *,
    include_usb: bool = True,
    include_ble: bool = False,
    include_ble_status: bool | None = None,
    persistent: bool = False,
) -> tuple[LightConnectionCandidate, ...]:
    resolved = config or LightConnectionConfig()
    return connection_candidates_from_devices(
        local_devices(
            resolved,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
        ),
        include_usb=include_usb,
        include_ble=include_ble,
        persistent=persistent,
    )


def local_probe_connection_candidates(
    config: LightConnectionConfig | None = None,
    *,
    include_usb: bool = True,
    include_ble: bool = False,
    include_ble_status: bool | None = None,
    persistent: bool = False,
    confirmed_only: bool = False,
) -> tuple[LightConnectionCandidate, ...]:
    candidates = local_connection_candidates(
        config,
        include_usb=include_usb,
        include_ble=include_ble,
        include_ble_status=include_ble_status,
        persistent=persistent,
    )
    probed = tuple(_probe_connection_candidate(candidate) for candidate in candidates)
    if confirmed_only:
        probed = tuple(
            candidate
            for candidate in probed
            if _candidate_status_confirmed(candidate)
        )
    return _rank_connection_candidates(probed)


async def local_async_connection_candidates(
    config: LightConnectionConfig | None = None,
    *,
    include_usb: bool = True,
    include_ble: bool = False,
    include_ble_status: bool | None = None,
    persistent: bool = False,
) -> tuple[LightConnectionCandidate, ...]:
    return await asyncio.to_thread(
        local_connection_candidates,
        config,
        include_usb=include_usb,
        include_ble=include_ble,
        include_ble_status=include_ble_status,
        persistent=persistent,
    )


async def local_async_probe_connection_candidates(
    config: LightConnectionConfig | None = None,
    *,
    include_usb: bool = True,
    include_ble: bool = False,
    include_ble_status: bool | None = None,
    persistent: bool = False,
    confirmed_only: bool = False,
) -> tuple[LightConnectionCandidate, ...]:
    return await asyncio.to_thread(
        local_probe_connection_candidates,
        config,
        include_usb=include_usb,
        include_ble=include_ble,
        include_ble_status=include_ble_status,
        persistent=persistent,
        confirmed_only=confirmed_only,
    )


def local_setup_report(
    config: LightConnectionConfig | None = None,
    *,
    allow_control: bool = False,
    include_usb: bool = True,
    include_ble: bool = False,
    include_ble_status: bool | None = None,
    persistent: bool = False,
    require_confirmed_route: bool = True,
    include_object_reads: bool = False,
    include_color: bool = False,
    device_id: int = 0,
    obj: int = 1,
    brightness: float = 35.0,
    kelvin: int = 5600,
    sleep: int = 0,
    red: int = 255,
    green: int = 255,
    blue: int = 255,
    hue: float = 0.0,
    saturation: float = 0.0,
    intensity: int = 35,
    control_mode: int = DEFAULT_CONTROL_MODE,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig()
    route_error: str | None = None
    routes = local_probe_connection_candidates(
        resolved,
        include_usb=include_usb,
        include_ble=include_ble,
        include_ble_status=include_ble_status,
        persistent=persistent,
    )
    confirmed_routes = tuple(
        route for route in routes if _candidate_status_confirmed(route)
    )
    selected_route = _setup_selected_route(
        routes,
        confirmed_routes,
        require_confirmed_route=require_confirmed_route,
    )
    if require_confirmed_route and selected_route is None:
        route_error = "no status-confirmed connection candidates available"
    selected_config = selected_route.config if selected_route is not None else resolved
    status, status_ok, status_error = local_status_snapshot(selected_config)
    readiness = local_readiness(
        selected_config,
        allow_control=allow_control,
        include_ble=include_ble,
        include_ble_status=include_ble_status,
    )
    validation = local_validation(
        selected_config,
        allow_control=allow_control,
        include_object_reads=include_object_reads,
        include_color=include_color,
        device_id=device_id,
        obj=obj,
        brightness=brightness,
        kelvin=kelvin,
        sleep=sleep,
        red=red,
        green=green,
        blue=blue,
        hue=hue,
        saturation=saturation,
        intensity=intensity,
        control_mode=control_mode,
    )
    return _setup_report_payload(
        config=selected_config,
        routes=routes,
        selected_route=selected_route,
        route_error=route_error,
        require_confirmed_route=require_confirmed_route,
        allow_control=allow_control,
        include_object_reads=include_object_reads,
        status=status,
        status_ok=status_ok,
        status_error=status_error,
        readiness=readiness,
        validation=validation,
    )


async def local_async_setup_report(
    config: LightConnectionConfig | None = None,
    *,
    allow_control: bool = False,
    include_usb: bool = True,
    include_ble: bool = False,
    include_ble_status: bool | None = None,
    persistent: bool = False,
    require_confirmed_route: bool = True,
    include_object_reads: bool = False,
    include_color: bool = False,
    device_id: int = 0,
    obj: int = 1,
    brightness: float = 35.0,
    kelvin: int = 5600,
    sleep: int = 0,
    red: int = 255,
    green: int = 255,
    blue: int = 255,
    hue: float = 0.0,
    saturation: float = 0.0,
    intensity: int = 35,
    control_mode: int = DEFAULT_CONTROL_MODE,
) -> dict[str, object]:
    return await asyncio.to_thread(
        local_setup_report,
        config,
        allow_control=allow_control,
        include_usb=include_usb,
        include_ble=include_ble,
        include_ble_status=include_ble_status,
        persistent=persistent,
        require_confirmed_route=require_confirmed_route,
        include_object_reads=include_object_reads,
        include_color=include_color,
        device_id=device_id,
        obj=obj,
        brightness=brightness,
        kelvin=kelvin,
        sleep=sleep,
        red=red,
        green=green,
        blue=blue,
        hue=hue,
        saturation=saturation,
        intensity=intensity,
        control_mode=control_mode,
    )


def local_usb_discovery(
    config: LightConnectionConfig | None = None,
    *,
    light_factory: LightFactory | None = None,
    object_ids: Iterable[int] = DEFAULT_DISCOVERY_OBJECT_IDS,
    first_words: Iterable[int] = DEFAULT_DISCOVERY_FIRST_WORDS,
    control_object_ids: Iterable[int] | None = None,
    control_first_words: Iterable[int] = DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS,
    register_device_ids: Iterable[int] = DEFAULT_DISCOVERY_REGISTER_DEVICE_IDS,
    register_group_ids: Iterable[int] = DEFAULT_DISCOVERY_REGISTER_GROUP_IDS,
    control_kinds: Iterable[str] = DEFAULT_DISCOVERY_CONTROL_KINDS,
    control_modes: Iterable[int] | None = DEFAULT_DISCOVERY_CONTROL_MODES,
    post_register_reads: bool = False,
    timeout: float | None = None,
    allow_control: bool = False,
    brightness: float = 35.0,
    kelvin: int = 5600,
    sleep: int = 0,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig()
    if resolved.transport != "usb":
        raise ValueError("USB discovery requires transport='usb'")
    factory = light_factory or make_light_factory(resolved)
    try:
        with factory() as light:
            report = discover_usb_primitives(
                light,
                object_ids=object_ids,
                first_words=first_words,
                control_object_ids=control_object_ids,
                control_first_words=control_first_words,
                register_device_ids=register_device_ids,
                register_group_ids=register_group_ids,
                control_kinds=control_kinds,
                control_modes=control_modes,
                post_register_reads=post_register_reads,
                timeout=resolved.timeout if timeout is None else timeout,
                allow_control=allow_control,
                brightness=brightness,
                kelvin=kelvin,
                sleep=sleep,
            )
    finally:
        if light_factory is None:
            close_light_factory(factory)
    return report.to_dict()


def integration_ready_for(payload: Mapping[str, object]) -> dict[str, bool]:
    ready_for = _readiness_payload(payload).get("ready_for")
    if not isinstance(ready_for, Mapping):
        return {}
    return {
        str(capability): ready is True
        for capability, ready in ready_for.items()
    }


def integration_ready(payload: Mapping[str, object], capability: str) -> bool:
    return integration_ready_for(payload).get(capability, False)


def integration_pending_action_ids(
    payload: Mapping[str, object],
    *,
    capability: str | None = None,
) -> list[str]:
    readiness = _readiness_payload(payload)
    if capability is not None:
        return _pending_ids_from_requirement(readiness, capability)
    requirements = readiness.get("requirements")
    pending: list[str] = []
    seen: set[str] = set()
    if isinstance(requirements, Mapping):
        for requirement in requirements.values():
            if not isinstance(requirement, Mapping):
                continue
            _append_pending_ids(requirement.get("pending_actions"), pending, seen)
    summary = readiness.get("summary")
    if isinstance(summary, Mapping):
        _append_pending_ids(summary.get("pending_action_ids"), pending, seen)
    return pending


def integration_warnings(payload: Mapping[str, object]) -> list[str]:
    warnings = _readiness_payload(payload).get("warnings")
    if not isinstance(warnings, list):
        return []
    return [str(warning) for warning in warnings]


def integration_require(
    payload: Mapping[str, object],
    capabilities: Iterable[str],
) -> dict[str, object]:
    readiness = _readiness_payload(payload)
    required = _capabilities_or_default(capabilities)
    ready_for = integration_ready_for(readiness)
    if all(ready_for.get(capability, False) for capability in required):
        return readiness
    raise IntegrationNotReady(readiness, required)


async def local_async_usb_discovery(
    config: LightConnectionConfig | None = None,
    *,
    light_factory: LightFactory | None = None,
    object_ids: Iterable[int] = DEFAULT_DISCOVERY_OBJECT_IDS,
    first_words: Iterable[int] = DEFAULT_DISCOVERY_FIRST_WORDS,
    control_object_ids: Iterable[int] | None = None,
    control_first_words: Iterable[int] = DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS,
    register_device_ids: Iterable[int] = DEFAULT_DISCOVERY_REGISTER_DEVICE_IDS,
    register_group_ids: Iterable[int] = DEFAULT_DISCOVERY_REGISTER_GROUP_IDS,
    control_kinds: Iterable[str] = DEFAULT_DISCOVERY_CONTROL_KINDS,
    control_modes: Iterable[int] | None = DEFAULT_DISCOVERY_CONTROL_MODES,
    post_register_reads: bool = False,
    timeout: float | None = None,
    allow_control: bool = False,
    brightness: float = 35.0,
    kelvin: int = 5600,
    sleep: int = 0,
) -> dict[str, object]:
    return await asyncio.to_thread(
        local_usb_discovery,
        config,
        light_factory=light_factory,
        object_ids=object_ids,
        first_words=first_words,
        control_object_ids=control_object_ids,
        control_first_words=control_first_words,
        register_device_ids=register_device_ids,
        register_group_ids=register_group_ids,
        control_kinds=control_kinds,
        control_modes=control_modes,
        post_register_reads=post_register_reads,
        timeout=timeout,
        allow_control=allow_control,
        brightness=brightness,
        kelvin=kelvin,
        sleep=sleep,
    )


def local_ble_inspect(
    config: LightConnectionConfig | None = None,
    *,
    backend: str | None = None,
    timeout: float | None = None,
    address: str | None = None,
    name_contains: str | None = None,
    python: str | None = None,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig(transport="ble")
    selected_backend = backend or _ble_backend(resolved)
    selected_timeout = resolved.timeout if timeout is None else timeout
    selected_address = address or resolved.address
    selected_name = name_contains or resolved.name_contains
    selected_python = python or resolved.ble_python
    result = inspect_ble_device(
        backend=selected_backend,
        timeout=selected_timeout,
        address=selected_address,
        name_contains=selected_name,
        python=selected_python,
    ).to_dict()
    result.update(
        {
            "backend": selected_backend,
            "timeout": selected_timeout,
            "name_contains": selected_name,
        }
    )
    return result


async def local_async_ble_inspect(
    config: LightConnectionConfig | None = None,
    *,
    backend: str | None = None,
    timeout: float | None = None,
    address: str | None = None,
    name_contains: str | None = None,
    python: str | None = None,
) -> dict[str, object]:
    return await asyncio.to_thread(
        local_ble_inspect,
        config,
        backend=backend,
        timeout=timeout,
        address=address,
        name_contains=name_contains,
        python=python,
    )


def local_ble_endpoint_test(
    config: LightConnectionConfig | None = None,
    *,
    backend: str | None = None,
    timeout: float | None = None,
    address: str | None = None,
    name_contains: str | None = None,
    python: str | None = None,
    max_candidates: int = 4,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig(transport="ble")
    selected_backend = backend or _ble_backend(resolved)
    selected_timeout = resolved.timeout if timeout is None else timeout
    return test_ble_endpoint_candidates(
        backend=selected_backend,
        timeout=selected_timeout,
        address=address or resolved.address,
        name_contains=name_contains or resolved.name_contains,
        python=python or resolved.ble_python,
        max_candidates=max_candidates,
    ).to_dict()


async def local_async_ble_endpoint_test(
    config: LightConnectionConfig | None = None,
    *,
    backend: str | None = None,
    timeout: float | None = None,
    address: str | None = None,
    name_contains: str | None = None,
    python: str | None = None,
    max_candidates: int = 4,
) -> dict[str, object]:
    return await asyncio.to_thread(
        local_ble_endpoint_test,
        config,
        backend=backend,
        timeout=timeout,
        address=address,
        name_contains=name_contains,
        python=python,
        max_candidates=max_candidates,
    )


def local_ble_endpoint_connection_candidates(
    config: LightConnectionConfig | None = None,
    *,
    backend: str | None = None,
    timeout: float | None = None,
    address: str | None = None,
    name_contains: str | None = None,
    python: str | None = None,
    max_candidates: int = 4,
    persistent: bool = False,
    require_confirmed: bool = True,
) -> tuple[LightConnectionCandidate, ...]:
    resolved = config or LightConnectionConfig(transport="ble")
    selected_backend = backend or _ble_backend(resolved)
    selected_timeout = resolved.timeout if timeout is None else timeout
    report = local_ble_endpoint_test(
        resolved,
        backend=selected_backend,
        timeout=selected_timeout,
        address=address,
        name_contains=name_contains,
        python=python,
        max_candidates=max_candidates,
    )
    return connection_candidates_from_endpoint_report(
        report,
        backend=selected_backend,
        timeout=selected_timeout,
        python=python or resolved.ble_python,
        persistent=persistent,
        require_confirmed=require_confirmed,
    )


async def local_async_ble_endpoint_connection_candidates(
    config: LightConnectionConfig | None = None,
    *,
    backend: str | None = None,
    timeout: float | None = None,
    address: str | None = None,
    name_contains: str | None = None,
    python: str | None = None,
    max_candidates: int = 4,
    persistent: bool = False,
    require_confirmed: bool = True,
) -> tuple[LightConnectionCandidate, ...]:
    return await asyncio.to_thread(
        local_ble_endpoint_connection_candidates,
        config,
        backend=backend,
        timeout=timeout,
        address=address,
        name_contains=name_contains,
        python=python,
        max_candidates=max_candidates,
        persistent=persistent,
        require_confirmed=require_confirmed,
    )


async def local_async_status_snapshot(
    config: LightConnectionConfig | None = None,
    *,
    light_factory: AsyncLightFactory | None = None,
    status_reader: Callable[[object], object] | None = None,
) -> StatusSnapshot:
    resolved = config or LightConnectionConfig(transport="ble")
    try:
        factory = light_factory or _async_light_factory(resolved)
        async with factory() as light:
            if status_reader is None:
                report = await read_async_status(
                    light,
                    transport=resolved.transport,
                    timeout=resolved.timeout,
                )
            else:
                report = await _await_report(status_reader(light))
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
    devices = local_devices(
        resolved,
        include_ble=include_ble,
        include_ble_status=include_ble_status,
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


async def local_async_readiness(
    config: LightConnectionConfig | None = None,
    *,
    allow_control: bool = False,
    include_ble: bool = False,
    include_ble_status: bool | None = None,
    state_version: int = 0,
    state: Mapping[str, object] | None = None,
    light_factory: AsyncLightFactory | None = None,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig(transport="ble")
    status, connection_confirmed, error = await local_async_status_snapshot(
        resolved,
        light_factory=light_factory,
    )
    backend = _ble_backend(resolved)
    devices = await local_async_devices(
        resolved,
        include_ble=include_ble,
        include_ble_status=include_ble_status,
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


async def local_async_integration_snapshot(
    config: LightConnectionConfig | None = None,
    *,
    allow_control: bool = False,
    include_ble: bool = False,
    include_ble_status: bool | None = None,
    presets: Iterable[str] = (),
    cues: Iterable[str] = (),
    state_version: int = 0,
    state: Mapping[str, object] | None = None,
    light_factory: AsyncLightFactory | None = None,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig(transport="ble")
    preset_names = tuple(presets)
    cue_names = tuple(cues)
    ready = await local_async_readiness(
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


def local_validation(
    config: LightConnectionConfig | None = None,
    *,
    allow_control: bool = False,
    include_object_reads: bool = False,
    include_color: bool = False,
    device_id: int = 0,
    obj: int = 1,
    brightness: float = 35.0,
    kelvin: int = 5600,
    sleep: int = 0,
    red: int = 255,
    green: int = 255,
    blue: int = 255,
    hue: float = 0.0,
    saturation: float = 0.0,
    intensity: int = 35,
    control_mode: int = DEFAULT_CONTROL_MODE,
    light_factory: LightFactory | None = None,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig()
    factory: LightFactory | None = None
    try:
        factory = light_factory or make_light_factory(resolved)
        with factory() as light:
            report = validate_sync_light(
                light,
                transport=resolved.transport,
                allow_control=allow_control,
                include_object_reads=include_object_reads,
                include_color=include_color,
                device_id=device_id,
                obj=obj,
                brightness=brightness,
                kelvin=kelvin,
                sleep=sleep,
                red=red,
                green=green,
                blue=blue,
                hue=hue,
                saturation=saturation,
                intensity=intensity,
                control_mode=control_mode,
            )
    except Exception as exc:
        return _validation_error_payload(resolved, exc, allow_control=allow_control)
    finally:
        if light_factory is None and factory is not None:
            close_light_factory(factory)
    return report.to_dict()


async def local_async_validation(
    config: LightConnectionConfig | None = None,
    *,
    allow_control: bool = False,
    include_object_reads: bool = False,
    include_color: bool = False,
    device_id: int = 0,
    obj: int = 1,
    brightness: float = 35.0,
    kelvin: int = 5600,
    sleep: int = 0,
    red: int = 255,
    green: int = 255,
    blue: int = 255,
    hue: float = 0.0,
    saturation: float = 0.0,
    intensity: int = 35,
    control_mode: int = DEFAULT_CONTROL_MODE,
    light_factory: AsyncLightFactory | None = None,
) -> dict[str, object]:
    resolved = config or LightConnectionConfig(transport="ble")
    try:
        factory = light_factory or _async_light_factory(resolved)
        async with factory() as light:
            report = await validate_async_light(
                light,
                transport=resolved.transport,
                allow_control=allow_control,
                include_object_reads=include_object_reads,
                include_color=include_color,
                device_id=device_id,
                obj=obj,
                brightness=brightness,
                kelvin=kelvin,
                sleep=sleep,
                red=red,
                green=green,
                blue=blue,
                hue=hue,
                saturation=saturation,
                intensity=intensity,
                control_mode=control_mode,
            )
    except Exception as exc:
        return _validation_error_payload(resolved, exc, allow_control=allow_control)
    return report.to_dict()


def local_error_status(exc: Exception) -> dict[str, object]:
    status: dict[str, object] = {"ok": False, "error": str(exc)}
    if isinstance(exc, BleWorkerError):
        status["transport"] = "ble"
        status["exchange"] = exc.result.to_dict()
    return status


def _ble_backend(config: LightConnectionConfig) -> str:
    return "direct" if config.ble_in_process else config.ble_backend


def _include_ble_status(
    config: LightConnectionConfig,
    requested: bool | None,
) -> bool:
    if requested is not None:
        return requested
    return config.transport == "ble" and _ble_backend(config) == "macos-app"


def _probe_connection_candidate(
    candidate: LightConnectionCandidate,
) -> LightConnectionCandidate:
    status, confirmed, error = local_status_snapshot(candidate.config)
    evidence = dict(candidate.evidence or {})
    evidence["status_probe"] = _status_probe_evidence(status, confirmed, error)
    if confirmed:
        return LightConnectionCandidate(
            config=candidate.config,
            source=f"{candidate.source}.status",
            confidence="status-confirmed",
            confidence_score=max(100, candidate.confidence_score + 20),
            reason=(
                f"{candidate.reason}; status probe confirmed "
                f"{_status_probe_label(status)}"
            ),
            evidence=evidence,
        )
    return LightConnectionCandidate(
        config=candidate.config,
        source=f"{candidate.source}.status",
        confidence="status-unconfirmed",
        confidence_score=min(40, candidate.confidence_score),
        reason=(
            f"{candidate.reason}; status probe failed: "
            f"{error or 'connection not confirmed'}"
        ),
        evidence=evidence,
    )


def _status_probe_evidence(
    status: Mapping[str, object],
    confirmed: bool,
    error: str | None,
) -> dict[str, object]:
    evidence = {
        key: status[key]
        for key in (
            "transport",
            "device_identifier",
            "generation",
            "firmware",
            "device_id",
            "voltage_status",
            "port",
        )
        if key in status
    }
    evidence["connection_confirmed"] = confirmed
    if error is not None:
        evidence["error"] = error
    elif "error" in status:
        evidence["error"] = status["error"]
    return evidence


def _status_probe_label(status: Mapping[str, object]) -> str:
    parts = [
        f"{key}={status[key]}"
        for key in ("firmware", "generation", "device_identifier")
        if status.get(key) is not None
    ]
    return ", ".join(parts) or "read-status"


def _candidate_status_confirmed(candidate: LightConnectionCandidate) -> bool:
    evidence = candidate.evidence or {}
    status_probe = evidence.get("status_probe")
    if not isinstance(status_probe, Mapping):
        return False
    return status_probe.get("connection_confirmed") is True


def _profile_requirements(require: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(require, str):
        return (require,)
    return tuple(require)


def _setup_selected_route(
    routes: tuple[LightConnectionCandidate, ...],
    confirmed_routes: tuple[LightConnectionCandidate, ...],
    *,
    require_confirmed_route: bool,
) -> LightConnectionCandidate | None:
    if confirmed_routes:
        return _best_connection_candidate(confirmed_routes)
    if require_confirmed_route:
        return None
    if routes:
        return _best_connection_candidate(routes)
    return None


def _setup_report_payload(
    *,
    config: LightConnectionConfig,
    routes: tuple[LightConnectionCandidate, ...],
    selected_route: LightConnectionCandidate | None,
    route_error: str | None,
    require_confirmed_route: bool,
    allow_control: bool,
    include_object_reads: bool,
    status: Mapping[str, object],
    status_ok: bool,
    status_error: str | None,
    readiness: Mapping[str, object],
    validation: Mapping[str, object],
) -> dict[str, object]:
    ready_for = integration_ready_for(readiness)
    validation_ready = _validation_ready_for(validation)
    route_confirmed = (
        selected_route is not None and _candidate_status_confirmed(selected_route)
    )
    ok = status_ok and (route_confirmed or not require_confirmed_route)
    errors = [error for error in (route_error, status_error) if error is not None]
    report = {
        "api": "zhiyun-light-control",
        "ok": ok,
        "config": config.to_dict(),
        "selected_route": None
        if selected_route is None
        else selected_route.to_dict(),
        "routes": [route.to_dict() for route in routes],
        "route_confirmed": route_confirmed,
        "require_confirmed_route": require_confirmed_route,
        "control_enabled": allow_control,
        "include_object_reads": include_object_reads,
        "status_ok": status_ok,
        "status_error": status_error,
        "status": dict(status),
        "ready_for": ready_for,
        "validation_ready_for": validation_ready,
        "validation_unconfirmed": _validation_unconfirmed(validation),
        "validation": dict(validation),
        "summary": {
            "ok": ok,
            "connection_confirmed": status_ok,
            "route_confirmed": route_confirmed,
            "ready_for": ready_for,
            "validation_ready_for": validation_ready,
            "validation_unconfirmed": _validation_unconfirmed(validation),
            "pending_action_ids": integration_pending_action_ids(readiness),
            "warnings": integration_warnings(readiness),
            "errors": errors,
        },
    }
    report["capabilities"] = setup_profile_capabilities(report)
    report["primitive_ready_for"] = setup_profile_primitive_ready_for(report)
    report["primitive_readiness"] = setup_profile_primitive_readiness_map(report)
    return report


def _validation_ready_for(payload: Mapping[str, object]) -> dict[str, bool]:
    summary = payload.get("summary")
    if not isinstance(summary, Mapping):
        return {}
    ready_for = summary.get("ready_for")
    if not isinstance(ready_for, Mapping):
        return {}
    return {str(key): value is True for key, value in ready_for.items()}


def _validation_unconfirmed(payload: Mapping[str, object]) -> list[str]:
    unconfirmed = payload.get("unconfirmed")
    if not isinstance(unconfirmed, list):
        return []
    return [str(name) for name in unconfirmed]


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


def _report_to_dict(report: object) -> dict[str, object]:
    to_dict = getattr(report, "to_dict", None)
    if not callable(to_dict):
        raise TypeError("status reader must return an object with to_dict()")
    payload = to_dict()
    if not isinstance(payload, dict):
        raise TypeError("status reader to_dict() must return a dict")
    return {str(key): value for key, value in payload.items()}


def _readiness_payload(payload: Mapping[str, object]) -> dict[str, object]:
    if isinstance(payload.get("ready_for"), Mapping):
        return {str(key): value for key, value in payload.items()}
    nested_payloads = payload.get("payloads")
    if isinstance(nested_payloads, Mapping):
        ready = nested_payloads.get("ready")
        if isinstance(ready, Mapping):
            return {str(key): value for key, value in ready.items()}
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, Mapping):
        snapshot_payloads = snapshot.get("payloads")
        if isinstance(snapshot_payloads, Mapping):
            ready = snapshot_payloads.get("ready")
            if isinstance(ready, Mapping):
                return {str(key): value for key, value in ready.items()}
    return {str(key): value for key, value in payload.items()}


def _capabilities_or_default(capabilities: Iterable[str]) -> tuple[str, ...]:
    items = tuple(str(capability) for capability in capabilities)
    return items or ("read_status",)


def _pending_ids_from_requirement(
    payload: Mapping[str, object],
    capability: str,
) -> list[str]:
    requirements = payload.get("requirements")
    if not isinstance(requirements, Mapping):
        return []
    requirement = requirements.get(capability)
    if not isinstance(requirement, Mapping):
        return []
    pending: list[str] = []
    _append_pending_ids(requirement.get("pending_actions"), pending, set())
    return pending


def _append_pending_ids(
    values: object,
    pending: list[str],
    seen: set[str],
) -> None:
    if not isinstance(values, list):
        return
    for value in values:
        action_id = str(value)
        if action_id in seen:
            continue
        seen.add(action_id)
        pending.append(action_id)


async def _await_report(report: object) -> object:
    if hasattr(report, "__await__"):
        return await report
    return report


def _async_light_factory(config: LightConnectionConfig) -> AsyncLightFactory:
    return lambda: open_async_light(config)


def _validation_error_payload(
    config: LightConnectionConfig,
    exc: Exception,
    *,
    allow_control: bool,
) -> dict[str, object]:
    return {
        "transport": config.transport,
        "ok": False,
        "error": str(exc),
        "control_enabled": allow_control,
        "connection_confirmed": False,
        "all_attempted_confirmed": False,
        "unconfirmed": [],
        "summary": {
            "attempted": 0,
            "confirmed": 0,
            "unconfirmed": 0,
            "status_counts": {},
            "categories": {},
            "ready_for": {
                "read_status": False,
                "object_reads": False,
                "control_setup": False,
                "control_writes": False,
            },
        },
        "checks": [],
        "notes": ["validation did not run because the transport could not open"],
    }
