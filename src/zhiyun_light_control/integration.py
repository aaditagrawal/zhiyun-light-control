"""Programmatic integration preflight helpers for media-control hosts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace

from .bridge import (
    LightConnectionConfig,
    LightFactory,
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
        return replace(self, config=config)

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

    def apply_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
    ) -> dict[str, object]:
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self.controller(
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        ).apply_scene(
            _integration_scene_payload(scene, obj=self._obj(obj)),
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
    ) -> dict[str, object]:
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self.controller(
            preset_library=preset_library,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        ).apply_preset(
            name,
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
    ) -> dict[str, object]:
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self.controller(
            preset_library=preset_library,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        ).run_sequence(
            steps,
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
    ) -> dict[str, object]:
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self.controller(
            preset_library=preset_library,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        ).run_cue(
            cue,
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
    ) -> dict[str, object]:
        self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return self.controller(
            preset_library=preset_library,
            cue_library=cue_library,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        ).run_named_cue(
            name,
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

    async def status(self) -> StatusSnapshot:
        return await local_async_status_snapshot(
            self.config,
            light_factory=self.light_factory,
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
        return replace(self, config=config)

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

    async def apply_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        obj: int | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
    ) -> dict[str, object]:
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self.controller(
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        ).apply_scene(
            _integration_scene_payload(scene, obj=self._obj(obj)),
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
    ) -> dict[str, object]:
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self.controller(
            preset_library=preset_library,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        ).apply_preset(
            name,
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
    ) -> dict[str, object]:
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self.controller(
            preset_library=preset_library,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        ).run_sequence(
            steps,
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
    ) -> dict[str, object]:
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self.controller(
            preset_library=preset_library,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        ).run_cue(
            cue,
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
    ) -> dict[str, object]:
        await self._require_control_readiness(
            require_ready,
            required_readiness,
            require_acknowledged=require_acknowledged,
        )
        return await self.controller(
            preset_library=preset_library,
            cue_library=cue_library,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        ).run_named_cue(
            name,
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
