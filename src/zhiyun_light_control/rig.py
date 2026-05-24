"""Named multi-light SDK helpers for media-control rigs."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .bridge import LightConnectionConfig, LightFactory
from .controller import AsyncLightController, AsyncLightFactory, LightController
from .cues import CueLibrary
from .integration import (
    AsyncLightIntegration,
    IntegrationNotReady,
    LightIntegration,
    StatusSnapshot,
    integration_require,
)
from .models import Scene
from .presets import ScenePresetLibrary, scene_from_mapping
from .profiles import (
    LightSetupProfile,
    load_light_setup_profile,
    save_light_setup_profile,
    setup_profile_primitive_readiness,
)
from .profiles import (
    setup_profile_summary as _setup_profile_summary,
)
from .protocol import DEFAULT_CONTROL_MODE, RUNTIME_TYPE
from .state import SceneStateTracker


@dataclass(frozen=True)
class LightFixture:
    """A named controllable light in a local rig."""

    name: str
    config: LightConnectionConfig = field(default_factory=LightConnectionConfig)
    obj: int = 1
    tags: tuple[str, ...] = ()
    setup_profile: LightSetupProfile | None = None

    @classmethod
    def from_setup_profile(
        cls,
        name: str,
        profile: LightSetupProfile,
        *,
        obj: int = 1,
        tags: Iterable[str] = (),
    ) -> LightFixture:
        return cls(
            name=name,
            config=profile.config,
            obj=obj,
            tags=tuple(str(tag) for tag in tags),
            setup_profile=profile,
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "name": self.name,
            "obj": self.obj,
            "tags": list(self.tags),
        }
        if self.setup_profile is not None:
            payload["setup_profile"] = self.setup_profile.to_dict()
        else:
            payload["config"] = asdict(self.config)
        return payload


class RigConfigError(ValueError):
    pass


class RigNotReady(RuntimeError):
    def __init__(
        self,
        response: Mapping[str, object],
        capabilities: Iterable[str],
        fixture_errors: Mapping[str, IntegrationNotReady],
    ) -> None:
        self.response = dict(response)
        self.capabilities = tuple(capabilities) or ("read_status",)
        self.fixture_errors = dict(fixture_errors)
        self.pending_action_ids = {
            name: error.pending_action_ids
            for name, error in self.fixture_errors.items()
        }
        names = ", ".join(self.fixture_errors) or "no fixtures"
        super().__init__(f"rig not ready for {', '.join(self.capabilities)}: {names}")


FixtureInput = LightFixture | Mapping[str, object]
SceneInput = Scene | Mapping[str, object]


class LightRig:
    """Synchronous named-fixture controller for USB/BLE light groups."""

    def __init__(
        self,
        fixtures: Iterable[FixtureInput],
        *,
        light_factories: Mapping[str, LightFactory] | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_setup_profile_controls: bool = False,
    ) -> None:
        self.fixtures = _fixture_map(fixtures)
        self.preset_library = preset_library
        self.cue_library = cue_library
        self.control_mode = control_mode
        self.require_acknowledged = require_acknowledged
        self.require_setup_profile_controls = require_setup_profile_controls
        self.light_factories = dict(light_factories or {})
        self.controllers = {
            name: LightController(
                fixture.config,
                light_factory=self.light_factories.get(name),
                preset_library=preset_library,
                cue_library=cue_library,
                state_tracker=SceneStateTracker(),
                control_mode=control_mode,
                require_acknowledged=require_acknowledged,
            )
            for name, fixture in self.fixtures.items()
        }

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
        *,
        light_factories: Mapping[str, LightFactory] | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile_controls: bool | None = None,
    ) -> LightRig:
        definition = _rig_definition_from_mapping(payload)
        return cls(
            definition.fixtures,
            light_factories=light_factories,
            preset_library=preset_library or definition.preset_library,
            cue_library=cue_library or definition.cue_library,
            control_mode=definition.control_mode
            if control_mode is None
            else control_mode,
            require_acknowledged=(
                definition.require_acknowledged
                if require_acknowledged is None
                else require_acknowledged
            ),
            require_setup_profile_controls=(
                definition.require_setup_profile_controls
                if require_setup_profile_controls is None
                else require_setup_profile_controls
            ),
        )

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        light_factories: Mapping[str, LightFactory] | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile_controls: bool | None = None,
    ) -> LightRig:
        return cls.from_mapping(
            load_rig_mapping(path),
            light_factories=light_factories,
            preset_library=preset_library,
            cue_library=cue_library,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
            require_setup_profile_controls=require_setup_profile_controls,
        )

    def __enter__(self) -> LightRig:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def close(self) -> None:
        for controller in self.controllers.values():
            controller.close()

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "fixtures": [fixture.to_dict() for fixture in self.fixtures.values()],
            "control_mode": self.control_mode,
            "require_acknowledged": self.require_acknowledged,
            "require_setup_profile_controls": self.require_setup_profile_controls,
        }
        if self.preset_library is not None:
            data["presets"] = self.preset_library.to_dict()
        if self.cue_library is not None:
            data["cues"] = self.cue_library.to_dict()
        return data

    def to_json(self, *, indent: int | None = 2) -> str:
        return rig_to_json(self, indent=indent)

    def save(self, path: str | Path, *, indent: int | None = 2) -> None:
        save_rig(self, path, indent=indent)

    def fixture_names(self, *, tag: str | None = None) -> tuple[str, ...]:
        return tuple(
            name
            for name, fixture in self.fixtures.items()
            if tag is None or tag in fixture.tags
        )

    def fixture(self, name: str) -> LightFixture:
        try:
            return self.fixtures[name]
        except KeyError as exc:
            raise ValueError(f"unknown fixture {name!r}") from exc

    def setup_profile(self, name: str) -> LightSetupProfile | None:
        return self.fixture(name).setup_profile

    def require_setup_profile(
        self,
        name: str,
        *capabilities: str,
    ) -> LightSetupProfile:
        profile = self.setup_profile(name)
        if profile is None:
            raise RigConfigError(f"fixture {name!r} has no setup profile")
        return profile.require_ready(*capabilities)

    def require_setup_profile_primitive(
        self,
        name: str,
        primitive: str,
    ) -> LightSetupProfile:
        profile = self.setup_profile(name)
        if profile is None:
            raise RigConfigError(f"fixture {name!r} has no setup profile")
        return profile.require_primitive(primitive)

    def setup_profile_summary(
        self,
        name: str,
        *,
        primitives: Iterable[str] | None = None,
    ) -> dict[str, object]:
        return _fixture_setup_profile_summary(
            self.fixture(name),
            primitives=primitives,
        )

    def setup_profile_summary_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        primitives: Iterable[str] | None = None,
    ) -> dict[str, object]:
        responses = {
            name: self.setup_profile_summary(name, primitives=primitives)
            for name in self._selected_fixture_names(fixture_names, tag=tag)
        }
        return _rig_setup_profile_summary_response(
            responses,
            require_setup_profile_controls=self.require_setup_profile_controls,
            primitives=primitives,
        )

    def with_setup_profiles(
        self,
        profiles: Mapping[str, object],
        *,
        require_setup_profile_controls: bool | None = None,
    ) -> LightRig:
        profile_map = _setup_profile_map(profiles)
        _require_known_profile_fixtures(profile_map, self.fixtures)
        fixtures = [
            _fixture_with_setup_profile(fixture, profile_map.get(name))
            for name, fixture in self.fixtures.items()
        ]
        return LightRig(
            fixtures,
            light_factories=self.light_factories,
            preset_library=self.preset_library,
            cue_library=self.cue_library,
            control_mode=self.control_mode,
            require_acknowledged=self.require_acknowledged,
            require_setup_profile_controls=(
                self.require_setup_profile_controls
                if require_setup_profile_controls is None
                else require_setup_profile_controls
            ),
        )

    def controller(self, name: str) -> LightController:
        self.fixture(name)
        return self.controllers[name]

    def integration(
        self,
        name: str,
        *,
        allow_control: bool = False,
        require_setup_profile_controls: bool | None = None,
    ) -> LightIntegration:
        fixture = self.fixture(name)
        guard = self._setup_profile_controls_guard(require_setup_profile_controls)
        integration = LightIntegration(
            config=fixture.config,
            allow_control=allow_control,
            preset_names=_preset_names(self.preset_library),
            cue_names=_cue_names(self.cue_library),
            light_factory=self.controller(name).light_factory,
            preset_library=self.preset_library,
            cue_library=self.cue_library,
            obj=fixture.obj,
            require_setup_profile_controls=guard,
        )
        if fixture.setup_profile is None:
            return integration
        return integration.with_setup_profile(
            fixture.setup_profile,
            require_controls=guard,
        )

    def capabilities(self, name: str) -> dict[str, object]:
        payload = self.integration(name).capabilities()
        return _capabilities_response(name, payload)

    def capabilities_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
    ) -> dict[str, object]:
        responses = {
            name: self.capabilities(name)
            for name in self._selected_fixture_names(fixture_names, tag=tag)
        }
        return _rig_response("rig_capabilities", responses, stopped=False)

    def plan_scene(
        self,
        name: str,
        scene: SceneInput,
        *,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        payload = self.controller(name).plan_scene(
            scene,
            obj=fixture.obj,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return _fixture_plan_response(fixture, payload)

    def plan_preset(
        self,
        name: str,
        preset: str,
        *,
        overrides: Mapping[str, object] | None = None,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        payload = self.controller(name).plan_preset(
            preset,
            overrides=overrides,
            obj=fixture.obj,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return _fixture_plan_response(fixture, payload)

    def plan_sequence(
        self,
        name: str,
        steps: Iterable[Mapping[str, object]],
        *,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        payload = self.controller(name).plan_sequence(
            steps,
            obj=fixture.obj,
            stop_on_unconfirmed=stop_on_unconfirmed,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return _fixture_plan_response(fixture, payload)

    def plan_named_cue(
        self,
        name: str,
        cue: str,
        *,
        stop_on_unconfirmed: bool | None = None,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        payload = self.controller(name).plan_named_cue(
            cue,
            obj=fixture.obj,
            stop_on_unconfirmed=stop_on_unconfirmed,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return _fixture_plan_response(fixture, payload)

    def plan_all(
        self,
        scene: SceneInput,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        responses = {
            name: self.plan_scene(
                name,
                scene,
                control_mode=control_mode,
                first_word=first_word,
                start_seq=start_seq,
            )
            for name in self._selected_fixture_names(fixture_names, tag=tag)
        }
        return _rig_plan_response("rig_plan_all", responses)

    def plan_scene_map(
        self,
        scenes: Mapping[str, SceneInput],
        *,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        responses = {
            str(name): self.plan_scene(
                str(name),
                scene,
                control_mode=control_mode,
                first_word=first_word,
                start_seq=start_seq,
            )
            for name, scene in scenes.items()
        }
        return _rig_plan_response("rig_plan_scene_map", responses)

    def plan_named_cue_all(
        self,
        cue: str,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        stop_on_unconfirmed: bool | None = None,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        responses = {
            name: self.plan_named_cue(
                name,
                cue,
                stop_on_unconfirmed=stop_on_unconfirmed,
                control_mode=control_mode,
                first_word=first_word,
                start_seq=start_seq,
            )
            for name in self._selected_fixture_names(fixture_names, tag=tag)
        }
        return _rig_plan_response("rig_plan_named_cue_all", responses)

    def snapshot(
        self,
        name: str,
        *,
        allow_control: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
    ) -> dict[str, object]:
        state_snapshot = self.controller(name).state_snapshot()
        payload = self.integration(name, allow_control=allow_control).snapshot(
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            state_version=_state_version(state_snapshot),
            state=_state_payload(state_snapshot),
        )
        return _snapshot_response(name, payload)

    def snapshot_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        allow_control: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        stop_on_unready: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = self.snapshot(
                name,
                allow_control=allow_control,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
            )
            responses[name] = response
            if stop_on_unready and response.get("ok") is not True:
                stopped = True
                break
        return _rig_response("rig_snapshot", responses, stopped=stopped)

    def connection_report(
        self,
        name: str,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        probe_status: bool = True,
        prefer_confirmed: bool = True,
    ) -> dict[str, object]:
        payload = self.integration(name).connection_report(
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            probe_status=probe_status,
            prefer_confirmed=prefer_confirmed,
        )
        return _connection_report_response(name, payload)

    def connection_report_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        probe_status: bool = True,
        prefer_confirmed: bool = True,
        stop_on_unready: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = self.connection_report(
                name,
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
                probe_status=probe_status,
                prefer_confirmed=prefer_confirmed,
            )
            responses[name] = response
            if stop_on_unready and response.get("ok") is not True:
                stopped = True
                break
        return _rig_response("rig_connection_report", responses, stopped=stopped)

    def setup_report(
        self,
        name: str,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        require_confirmed_route: bool = True,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int | None = None,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        payload = self.integration(name, allow_control=allow_control).setup_report(
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            require_confirmed_route=require_confirmed_route,
            allow_control=allow_control,
            include_object_reads=include_object_reads,
            include_color=include_color,
            device_id=device_id,
            obj=fixture.obj,
            brightness=brightness,
            kelvin=kelvin,
            sleep=sleep,
            red=red,
            green=green,
            blue=blue,
            hue=hue,
            saturation=saturation,
            intensity=intensity,
            control_mode=self.control_mode if control_mode is None else control_mode,
        )
        return _setup_report_response(name, payload)

    def setup_report_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        require_confirmed_route: bool = True,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int | None = None,
        stop_on_unready: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = self.setup_report(
                name,
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
                require_confirmed_route=require_confirmed_route,
                allow_control=allow_control,
                include_object_reads=include_object_reads,
                include_color=include_color,
                device_id=device_id,
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
            responses[name] = response
            if stop_on_unready and response.get("ok") is not True:
                stopped = True
                break
        return _rig_response("rig_setup_report", responses, stopped=stopped)

    def setup_profiles_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        require_confirmed_route: bool = True,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int | None = None,
        stop_on_unready: bool = False,
    ) -> dict[str, LightSetupProfile]:
        return rig_setup_profiles_from_report(
            self.setup_report_all(
                fixture_names=fixture_names,
                tag=tag,
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
                require_confirmed_route=require_confirmed_route,
                allow_control=allow_control,
                include_object_reads=include_object_reads,
                include_color=include_color,
                device_id=device_id,
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
                stop_on_unready=stop_on_unready,
            )
        )

    def setup_profile_bundle(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        require_confirmed_route: bool = True,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int | None = None,
        stop_on_unready: bool = False,
        profile_dir: str = "profiles",
        require_setup_profile_controls: bool | None = None,
    ) -> dict[str, object]:
        profiles = self.setup_profiles_all(
            fixture_names=fixture_names,
            tag=tag,
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            require_confirmed_route=require_confirmed_route,
            allow_control=allow_control,
            include_object_reads=include_object_reads,
            include_color=include_color,
            device_id=device_id,
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
            stop_on_unready=stop_on_unready,
        )
        profiled = self.with_setup_profiles(
            profiles,
            require_setup_profile_controls=require_setup_profile_controls,
        )
        return rig_profile_bundle_mapping(
            profiled,
            profiles,
            profile_dir=profile_dir,
        )

    def save_setup_profile_bundle(
        self,
        path: str | Path,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        require_confirmed_route: bool = True,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int | None = None,
        stop_on_unready: bool = False,
        profile_dir: str = "profiles",
        require_setup_profile_controls: bool | None = None,
        indent: int | None = 2,
        profile_indent: int | None = 2,
    ) -> dict[str, object]:
        profiles = self.setup_profiles_all(
            fixture_names=fixture_names,
            tag=tag,
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            require_confirmed_route=require_confirmed_route,
            allow_control=allow_control,
            include_object_reads=include_object_reads,
            include_color=include_color,
            device_id=device_id,
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
            stop_on_unready=stop_on_unready,
        )
        profiled = self.with_setup_profiles(
            profiles,
            require_setup_profile_controls=require_setup_profile_controls,
        )
        return save_rig_profile_bundle(
            profiled,
            path,
            profiles,
            profile_dir=profile_dir,
            indent=indent,
            profile_indent=profile_indent,
        )

    def probe(self, name: str) -> dict[str, object]:
        result = self.controller(name).probe()
        return {"fixture": name, "probe": result.to_dict()}

    def probe_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        stop_on_error: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            try:
                responses[name] = self.probe(name)
            except Exception as exc:
                responses[name] = {"fixture": name, "ok": False, "error": str(exc)}
                if stop_on_error:
                    stopped = True
                    break
        return _rig_response("rig_probe", responses, stopped=stopped)

    def status(self, name: str) -> dict[str, object]:
        status, confirmed, error = self.integration(name).status()
        return _status_response(name, (status, confirmed, error))

    def status_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        stop_on_error: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = self.status(name)
            responses[name] = response
            if stop_on_error and response.get("ok") is not True:
                stopped = True
                break
        return _rig_response("rig_status", responses, stopped=stopped)

    def readiness(
        self,
        name: str,
        *,
        allow_control: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
    ) -> dict[str, object]:
        state_snapshot = self.controller(name).state_snapshot()
        payload = self.integration(name, allow_control=allow_control).readiness(
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            state_version=_state_version(state_snapshot),
            state=_state_payload(state_snapshot),
        )
        return _readiness_response(name, payload)

    def readiness_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        allow_control: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        stop_on_unready: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = self.readiness(
                name,
                allow_control=allow_control,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
            )
            responses[name] = response
            if stop_on_unready and response.get("ok") is not True:
                stopped = True
                break
        return _rig_response("rig_readiness", responses, stopped=stopped)

    def require_readiness(
        self,
        name: str,
        *capabilities: str,
        allow_control: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
    ) -> dict[str, object]:
        response = self.readiness(
            name,
            allow_control=allow_control,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
        )
        integration_require(_fixture_readiness_payload(response), capabilities)
        return response

    def require_readiness_all(
        self,
        *capabilities: str,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        allow_control: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
    ) -> dict[str, object]:
        required = tuple(capabilities) or ("read_status",)
        response = self.readiness_all(
            fixture_names=fixture_names,
            tag=tag,
            allow_control=allow_control,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
        )
        failures = _rig_readiness_failures(response, required)
        if failures:
            raise RigNotReady(response, required, failures)
        return response

    def validate(
        self,
        name: str,
        *,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
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
        fixture = self.fixture(name)
        payload = self.integration(name, allow_control=allow_control).validate(
            allow_control=allow_control,
            include_object_reads=include_object_reads,
            include_color=include_color,
            device_id=device_id,
            obj=fixture.obj,
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
        return _validation_response(name, payload)

    def validate_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
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
        stop_on_unready: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = self.validate(
                name,
                allow_control=allow_control,
                include_object_reads=include_object_reads,
                include_color=include_color,
                device_id=device_id,
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
            responses[name] = response
            if stop_on_unready and response.get("ok") is not True:
                stopped = True
                break
        return _rig_response("rig_validation", responses, stopped=stopped)

    def apply_scene(
        self,
        name: str,
        scene: SceneInput,
        *,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        self._require_setup_profile_primitive_if_requested(
            name,
            "scene",
            require_setup_profile,
        )
        response = self.controller(name).apply_scene(
            _fixture_scene(fixture, scene),
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        )
        return {"fixture": name, **response}

    def apply_preset(
        self,
        name: str,
        preset: str,
        *,
        overrides: Mapping[str, object] | None = None,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        self._require_setup_profile_primitive_if_requested(
            name,
            "preset",
            require_setup_profile,
        )
        response = self.controller(name).apply_preset(
            preset,
            overrides=overrides,
            obj=fixture.obj,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        )
        return {"fixture": name, **response}

    def execute_plan(
        self,
        name: str,
        plan: Mapping[str, object],
        *,
        timeout: float | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        self.fixture(name)
        self._require_setup_profile_primitive_if_requested(
            name,
            _plan_primitive_name(plan),
            require_setup_profile,
        )
        response = self.controller(name).execute_plan(
            plan,
            timeout=timeout,
            require_acknowledged=require_acknowledged,
        )
        return {"fixture": name, **response}

    def execute_plan_map(
        self,
        plans: Mapping[str, object],
        *,
        stop_on_unconfirmed: bool = False,
        timeout: float | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name, plan in _fixture_plan_map(plans).items():
            response = self.execute_plan(
                name,
                plan,
                timeout=timeout,
                require_acknowledged=require_acknowledged,
                require_setup_profile=require_setup_profile,
            )
            responses[name] = response
            if stop_on_unconfirmed and response.get("applied") is not True:
                stopped = True
                break
        return _rig_response("rig_execute_plan_map", responses, stopped=stopped)

    def apply_all(
        self,
        scene: SceneInput,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = self.apply_scene(
                name,
                scene,
                control_mode=control_mode,
                require_acknowledged=require_acknowledged,
                require_setup_profile=require_setup_profile,
            )
            responses[name] = response
            if stop_on_unconfirmed and response.get("applied") is not True:
                stopped = True
                break
        return _rig_response("rig_apply_all", responses, stopped=stopped)

    def apply_scene_map(
        self,
        scenes: Mapping[str, SceneInput],
        *,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name, scene in scenes.items():
            response = self.apply_scene(
                name,
                scene,
                control_mode=control_mode,
                require_acknowledged=require_acknowledged,
                require_setup_profile=require_setup_profile,
            )
            responses[name] = response
            if stop_on_unconfirmed and response.get("applied") is not True:
                stopped = True
                break
        return _rig_response("rig_apply_scene_map", responses, stopped=stopped)

    def blackout(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        return self.apply_all(
            {"sleep": 1},
            fixture_names=fixture_names,
            tag=tag,
            stop_on_unconfirmed=stop_on_unconfirmed,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
            require_setup_profile=require_setup_profile,
        )

    def state_snapshot(self) -> dict[str, object]:
        return {
            "fixtures": {
                name: controller.state_snapshot()
                for name, controller in self.controllers.items()
            }
        }

    def state_history(
        self,
        *,
        after_version: int = 0,
        limit: int | None = None,
    ) -> dict[str, object]:
        return {
            "fixtures": {
                name: controller.state_history(
                    after_version=after_version,
                    limit=limit,
                )
                for name, controller in self.controllers.items()
            }
        }

    def _selected_fixture_names(
        self,
        names: Iterable[str] | None,
        *,
        tag: str | None,
    ) -> tuple[str, ...]:
        selected = tuple(names) if names is not None else self.fixture_names(tag=tag)
        if not selected:
            raise ValueError("no fixtures selected")
        for name in selected:
            self.fixture(name)
        return selected

    def _setup_profile_controls_guard(self, explicit: bool | None) -> bool:
        return self.require_setup_profile_controls if explicit is None else explicit

    def _require_setup_profile_primitive_if_requested(
        self,
        name: str,
        primitive: str,
        require_setup_profile: bool | None,
    ) -> None:
        if self._setup_profile_controls_guard(require_setup_profile):
            self.require_setup_profile_primitive(name, primitive)


class AsyncLightRig:
    """Async named-fixture controller for BLE-native host applications."""

    def __init__(
        self,
        fixtures: Iterable[FixtureInput],
        *,
        light_factories: Mapping[str, AsyncLightFactory] | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
        require_setup_profile_controls: bool = False,
    ) -> None:
        self.fixtures = _fixture_map(fixtures)
        self.preset_library = preset_library
        self.cue_library = cue_library
        self.control_mode = control_mode
        self.require_acknowledged = require_acknowledged
        self.require_setup_profile_controls = require_setup_profile_controls
        self.light_factories = dict(light_factories or {})
        self.controllers = {
            name: AsyncLightController(
                fixture.config,
                light_factory=self.light_factories.get(name),
                preset_library=preset_library,
                cue_library=cue_library,
                state_tracker=SceneStateTracker(),
                control_mode=control_mode,
                require_acknowledged=require_acknowledged,
            )
            for name, fixture in self.fixtures.items()
        }

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
        *,
        light_factories: Mapping[str, AsyncLightFactory] | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile_controls: bool | None = None,
    ) -> AsyncLightRig:
        definition = _rig_definition_from_mapping(payload)
        return cls(
            definition.fixtures,
            light_factories=light_factories,
            preset_library=preset_library or definition.preset_library,
            cue_library=cue_library or definition.cue_library,
            control_mode=definition.control_mode
            if control_mode is None
            else control_mode,
            require_acknowledged=(
                definition.require_acknowledged
                if require_acknowledged is None
                else require_acknowledged
            ),
            require_setup_profile_controls=(
                definition.require_setup_profile_controls
                if require_setup_profile_controls is None
                else require_setup_profile_controls
            ),
        )

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        light_factories: Mapping[str, AsyncLightFactory] | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile_controls: bool | None = None,
    ) -> AsyncLightRig:
        return cls.from_mapping(
            load_rig_mapping(path),
            light_factories=light_factories,
            preset_library=preset_library,
            cue_library=cue_library,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
            require_setup_profile_controls=require_setup_profile_controls,
        )

    async def __aenter__(self) -> AsyncLightRig:
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.close()

    async def close(self) -> None:
        for controller in self.controllers.values():
            await controller.close()

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "fixtures": [fixture.to_dict() for fixture in self.fixtures.values()],
            "control_mode": self.control_mode,
            "require_acknowledged": self.require_acknowledged,
            "require_setup_profile_controls": self.require_setup_profile_controls,
        }
        if self.preset_library is not None:
            data["presets"] = self.preset_library.to_dict()
        if self.cue_library is not None:
            data["cues"] = self.cue_library.to_dict()
        return data

    def to_json(self, *, indent: int | None = 2) -> str:
        return rig_to_json(self, indent=indent)

    def save(self, path: str | Path, *, indent: int | None = 2) -> None:
        save_rig(self, path, indent=indent)

    def fixture_names(self, *, tag: str | None = None) -> tuple[str, ...]:
        return tuple(
            name
            for name, fixture in self.fixtures.items()
            if tag is None or tag in fixture.tags
        )

    def fixture(self, name: str) -> LightFixture:
        try:
            return self.fixtures[name]
        except KeyError as exc:
            raise ValueError(f"unknown fixture {name!r}") from exc

    def setup_profile(self, name: str) -> LightSetupProfile | None:
        return self.fixture(name).setup_profile

    def require_setup_profile(
        self,
        name: str,
        *capabilities: str,
    ) -> LightSetupProfile:
        profile = self.setup_profile(name)
        if profile is None:
            raise RigConfigError(f"fixture {name!r} has no setup profile")
        return profile.require_ready(*capabilities)

    def require_setup_profile_primitive(
        self,
        name: str,
        primitive: str,
    ) -> LightSetupProfile:
        profile = self.setup_profile(name)
        if profile is None:
            raise RigConfigError(f"fixture {name!r} has no setup profile")
        return profile.require_primitive(primitive)

    def setup_profile_summary(
        self,
        name: str,
        *,
        primitives: Iterable[str] | None = None,
    ) -> dict[str, object]:
        return _fixture_setup_profile_summary(
            self.fixture(name),
            primitives=primitives,
        )

    def setup_profile_summary_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        primitives: Iterable[str] | None = None,
    ) -> dict[str, object]:
        responses = {
            name: self.setup_profile_summary(name, primitives=primitives)
            for name in self._selected_fixture_names(fixture_names, tag=tag)
        }
        return _rig_setup_profile_summary_response(
            responses,
            require_setup_profile_controls=self.require_setup_profile_controls,
            primitives=primitives,
        )

    def with_setup_profiles(
        self,
        profiles: Mapping[str, object],
        *,
        require_setup_profile_controls: bool | None = None,
    ) -> AsyncLightRig:
        profile_map = _setup_profile_map(profiles)
        _require_known_profile_fixtures(profile_map, self.fixtures)
        fixtures = [
            _fixture_with_setup_profile(fixture, profile_map.get(name))
            for name, fixture in self.fixtures.items()
        ]
        return AsyncLightRig(
            fixtures,
            light_factories=self.light_factories,
            preset_library=self.preset_library,
            cue_library=self.cue_library,
            control_mode=self.control_mode,
            require_acknowledged=self.require_acknowledged,
            require_setup_profile_controls=(
                self.require_setup_profile_controls
                if require_setup_profile_controls is None
                else require_setup_profile_controls
            ),
        )

    def controller(self, name: str) -> AsyncLightController:
        self.fixture(name)
        return self.controllers[name]

    def integration(
        self,
        name: str,
        *,
        allow_control: bool = False,
        require_setup_profile_controls: bool | None = None,
    ) -> AsyncLightIntegration:
        fixture = self.fixture(name)
        guard = self._setup_profile_controls_guard(require_setup_profile_controls)
        integration = AsyncLightIntegration(
            config=fixture.config,
            allow_control=allow_control,
            preset_names=_preset_names(self.preset_library),
            cue_names=_cue_names(self.cue_library),
            light_factory=self.controller(name).light_factory,
            preset_library=self.preset_library,
            cue_library=self.cue_library,
            obj=fixture.obj,
            require_setup_profile_controls=guard,
        )
        if fixture.setup_profile is None:
            return integration
        return integration.with_setup_profile(
            fixture.setup_profile,
            require_controls=guard,
        )

    def capabilities(self, name: str) -> dict[str, object]:
        payload = self.integration(name).capabilities()
        return _capabilities_response(name, payload)

    def capabilities_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
    ) -> dict[str, object]:
        responses = {
            name: self.capabilities(name)
            for name in self._selected_fixture_names(fixture_names, tag=tag)
        }
        return _rig_response("rig_capabilities", responses, stopped=False)

    def plan_scene(
        self,
        name: str,
        scene: SceneInput,
        *,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        payload = self.controller(name).plan_scene(
            scene,
            obj=fixture.obj,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return _fixture_plan_response(fixture, payload)

    def plan_preset(
        self,
        name: str,
        preset: str,
        *,
        overrides: Mapping[str, object] | None = None,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        payload = self.controller(name).plan_preset(
            preset,
            overrides=overrides,
            obj=fixture.obj,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return _fixture_plan_response(fixture, payload)

    def plan_sequence(
        self,
        name: str,
        steps: Iterable[Mapping[str, object]],
        *,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        payload = self.controller(name).plan_sequence(
            steps,
            obj=fixture.obj,
            stop_on_unconfirmed=stop_on_unconfirmed,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return _fixture_plan_response(fixture, payload)

    def plan_named_cue(
        self,
        name: str,
        cue: str,
        *,
        stop_on_unconfirmed: bool | None = None,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        payload = self.controller(name).plan_named_cue(
            cue,
            obj=fixture.obj,
            stop_on_unconfirmed=stop_on_unconfirmed,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return _fixture_plan_response(fixture, payload)

    def plan_all(
        self,
        scene: SceneInput,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        responses = {
            name: self.plan_scene(
                name,
                scene,
                control_mode=control_mode,
                first_word=first_word,
                start_seq=start_seq,
            )
            for name in self._selected_fixture_names(fixture_names, tag=tag)
        }
        return _rig_plan_response("rig_plan_all", responses)

    def plan_scene_map(
        self,
        scenes: Mapping[str, SceneInput],
        *,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        responses = {
            str(name): self.plan_scene(
                str(name),
                scene,
                control_mode=control_mode,
                first_word=first_word,
                start_seq=start_seq,
            )
            for name, scene in scenes.items()
        }
        return _rig_plan_response("rig_plan_scene_map", responses)

    def plan_named_cue_all(
        self,
        cue: str,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        stop_on_unconfirmed: bool | None = None,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        responses = {
            name: self.plan_named_cue(
                name,
                cue,
                stop_on_unconfirmed=stop_on_unconfirmed,
                control_mode=control_mode,
                first_word=first_word,
                start_seq=start_seq,
            )
            for name in self._selected_fixture_names(fixture_names, tag=tag)
        }
        return _rig_plan_response("rig_plan_named_cue_all", responses)

    async def snapshot(
        self,
        name: str,
        *,
        allow_control: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
    ) -> dict[str, object]:
        state_snapshot = self.controller(name).state_snapshot()
        payload = await self.integration(name, allow_control=allow_control).snapshot(
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            state_version=_state_version(state_snapshot),
            state=_state_payload(state_snapshot),
        )
        return _snapshot_response(name, payload)

    async def snapshot_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        allow_control: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        stop_on_unready: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = await self.snapshot(
                name,
                allow_control=allow_control,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
            )
            responses[name] = response
            if stop_on_unready and response.get("ok") is not True:
                stopped = True
                break
        return _rig_response("rig_snapshot", responses, stopped=stopped)

    async def connection_report(
        self,
        name: str,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        probe_status: bool = True,
        prefer_confirmed: bool = True,
    ) -> dict[str, object]:
        payload = await self.integration(name).connection_report(
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            probe_status=probe_status,
            prefer_confirmed=prefer_confirmed,
        )
        return _connection_report_response(name, payload)

    async def connection_report_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        probe_status: bool = True,
        prefer_confirmed: bool = True,
        stop_on_unready: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = await self.connection_report(
                name,
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
                probe_status=probe_status,
                prefer_confirmed=prefer_confirmed,
            )
            responses[name] = response
            if stop_on_unready and response.get("ok") is not True:
                stopped = True
                break
        return _rig_response("rig_connection_report", responses, stopped=stopped)

    async def setup_report(
        self,
        name: str,
        *,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        require_confirmed_route: bool = True,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int | None = None,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        payload = await self.integration(
            name,
            allow_control=allow_control,
        ).setup_report(
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            require_confirmed_route=require_confirmed_route,
            allow_control=allow_control,
            include_object_reads=include_object_reads,
            include_color=include_color,
            device_id=device_id,
            obj=fixture.obj,
            brightness=brightness,
            kelvin=kelvin,
            sleep=sleep,
            red=red,
            green=green,
            blue=blue,
            hue=hue,
            saturation=saturation,
            intensity=intensity,
            control_mode=self.control_mode if control_mode is None else control_mode,
        )
        return _setup_report_response(name, payload)

    async def setup_report_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        require_confirmed_route: bool = True,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int | None = None,
        stop_on_unready: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = await self.setup_report(
                name,
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
                require_confirmed_route=require_confirmed_route,
                allow_control=allow_control,
                include_object_reads=include_object_reads,
                include_color=include_color,
                device_id=device_id,
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
            responses[name] = response
            if stop_on_unready and response.get("ok") is not True:
                stopped = True
                break
        return _rig_response("rig_setup_report", responses, stopped=stopped)

    async def setup_profiles_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        require_confirmed_route: bool = True,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int | None = None,
        stop_on_unready: bool = False,
    ) -> dict[str, LightSetupProfile]:
        return rig_setup_profiles_from_report(
            await self.setup_report_all(
                fixture_names=fixture_names,
                tag=tag,
                include_usb=include_usb,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
                persistent=persistent,
                require_confirmed_route=require_confirmed_route,
                allow_control=allow_control,
                include_object_reads=include_object_reads,
                include_color=include_color,
                device_id=device_id,
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
                stop_on_unready=stop_on_unready,
            )
        )

    async def setup_profile_bundle(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        require_confirmed_route: bool = True,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int | None = None,
        stop_on_unready: bool = False,
        profile_dir: str = "profiles",
        require_setup_profile_controls: bool | None = None,
    ) -> dict[str, object]:
        profiles = await self.setup_profiles_all(
            fixture_names=fixture_names,
            tag=tag,
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            require_confirmed_route=require_confirmed_route,
            allow_control=allow_control,
            include_object_reads=include_object_reads,
            include_color=include_color,
            device_id=device_id,
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
            stop_on_unready=stop_on_unready,
        )
        profiled = self.with_setup_profiles(
            profiles,
            require_setup_profile_controls=require_setup_profile_controls,
        )
        return rig_profile_bundle_mapping(
            profiled,
            profiles,
            profile_dir=profile_dir,
        )

    async def save_setup_profile_bundle(
        self,
        path: str | Path,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        include_usb: bool = True,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        persistent: bool = False,
        require_confirmed_route: bool = True,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
        brightness: float = 35.0,
        kelvin: int = 5600,
        sleep: int = 0,
        red: int = 255,
        green: int = 255,
        blue: int = 255,
        hue: float = 0.0,
        saturation: float = 0.0,
        intensity: int = 35,
        control_mode: int | None = None,
        stop_on_unready: bool = False,
        profile_dir: str = "profiles",
        require_setup_profile_controls: bool | None = None,
        indent: int | None = 2,
        profile_indent: int | None = 2,
    ) -> dict[str, object]:
        profiles = await self.setup_profiles_all(
            fixture_names=fixture_names,
            tag=tag,
            include_usb=include_usb,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            persistent=persistent,
            require_confirmed_route=require_confirmed_route,
            allow_control=allow_control,
            include_object_reads=include_object_reads,
            include_color=include_color,
            device_id=device_id,
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
            stop_on_unready=stop_on_unready,
        )
        profiled = self.with_setup_profiles(
            profiles,
            require_setup_profile_controls=require_setup_profile_controls,
        )
        return save_rig_profile_bundle(
            profiled,
            path,
            profiles,
            profile_dir=profile_dir,
            indent=indent,
            profile_indent=profile_indent,
        )

    async def probe(self, name: str) -> dict[str, object]:
        result = await self.controller(name).probe()
        return {"fixture": name, "probe": result.to_dict()}

    async def probe_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        stop_on_error: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            try:
                responses[name] = await self.probe(name)
            except Exception as exc:
                responses[name] = {"fixture": name, "ok": False, "error": str(exc)}
                if stop_on_error:
                    stopped = True
                    break
        return _rig_response("rig_probe", responses, stopped=stopped)

    async def status(self, name: str) -> dict[str, object]:
        status = await self.integration(name).status()
        return _status_response(name, status)

    async def status_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        stop_on_error: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = await self.status(name)
            responses[name] = response
            if stop_on_error and response.get("ok") is not True:
                stopped = True
                break
        return _rig_response("rig_status", responses, stopped=stopped)

    async def readiness(
        self,
        name: str,
        *,
        allow_control: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
    ) -> dict[str, object]:
        state_snapshot = self.controller(name).state_snapshot()
        payload = await self.integration(name, allow_control=allow_control).readiness(
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            state_version=_state_version(state_snapshot),
            state=_state_payload(state_snapshot),
        )
        return _readiness_response(name, payload)

    async def readiness_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        allow_control: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
        stop_on_unready: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = await self.readiness(
                name,
                allow_control=allow_control,
                include_ble=include_ble,
                include_ble_status=include_ble_status,
            )
            responses[name] = response
            if stop_on_unready and response.get("ok") is not True:
                stopped = True
                break
        return _rig_response("rig_readiness", responses, stopped=stopped)

    async def require_readiness(
        self,
        name: str,
        *capabilities: str,
        allow_control: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
    ) -> dict[str, object]:
        response = await self.readiness(
            name,
            allow_control=allow_control,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
        )
        integration_require(_fixture_readiness_payload(response), capabilities)
        return response

    async def require_readiness_all(
        self,
        *capabilities: str,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        allow_control: bool = False,
        include_ble: bool = False,
        include_ble_status: bool | None = None,
    ) -> dict[str, object]:
        required = tuple(capabilities) or ("read_status",)
        response = await self.readiness_all(
            fixture_names=fixture_names,
            tag=tag,
            allow_control=allow_control,
            include_ble=include_ble,
            include_ble_status=include_ble_status,
        )
        failures = _rig_readiness_failures(response, required)
        if failures:
            raise RigNotReady(response, required, failures)
        return response

    async def validate(
        self,
        name: str,
        *,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
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
        fixture = self.fixture(name)
        payload = await self.integration(name, allow_control=allow_control).validate(
            allow_control=allow_control,
            include_object_reads=include_object_reads,
            include_color=include_color,
            device_id=device_id,
            obj=fixture.obj,
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
        return _validation_response(name, payload)

    async def validate_all(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        device_id: int = 0,
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
        stop_on_unready: bool = False,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = await self.validate(
                name,
                allow_control=allow_control,
                include_object_reads=include_object_reads,
                include_color=include_color,
                device_id=device_id,
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
            responses[name] = response
            if stop_on_unready and response.get("ok") is not True:
                stopped = True
                break
        return _rig_response("rig_validation", responses, stopped=stopped)

    async def apply_scene(
        self,
        name: str,
        scene: SceneInput,
        *,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        self._require_setup_profile_primitive_if_requested(
            name,
            "scene",
            require_setup_profile,
        )
        response = await self.controller(name).apply_scene(
            _fixture_scene(fixture, scene),
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        )
        return {"fixture": name, **response}

    async def apply_preset(
        self,
        name: str,
        preset: str,
        *,
        overrides: Mapping[str, object] | None = None,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        self._require_setup_profile_primitive_if_requested(
            name,
            "preset",
            require_setup_profile,
        )
        response = await self.controller(name).apply_preset(
            preset,
            overrides=overrides,
            obj=fixture.obj,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        )
        return {"fixture": name, **response}

    async def execute_plan(
        self,
        name: str,
        plan: Mapping[str, object],
        *,
        timeout: float | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        self.fixture(name)
        self._require_setup_profile_primitive_if_requested(
            name,
            _plan_primitive_name(plan),
            require_setup_profile,
        )
        response = await self.controller(name).execute_plan(
            plan,
            timeout=timeout,
            require_acknowledged=require_acknowledged,
        )
        return {"fixture": name, **response}

    async def execute_plan_map(
        self,
        plans: Mapping[str, object],
        *,
        stop_on_unconfirmed: bool = False,
        timeout: float | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name, plan in _fixture_plan_map(plans).items():
            response = await self.execute_plan(
                name,
                plan,
                timeout=timeout,
                require_acknowledged=require_acknowledged,
                require_setup_profile=require_setup_profile,
            )
            responses[name] = response
            if stop_on_unconfirmed and response.get("applied") is not True:
                stopped = True
                break
        return _rig_response("rig_execute_plan_map", responses, stopped=stopped)

    async def apply_all(
        self,
        scene: SceneInput,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = await self.apply_scene(
                name,
                scene,
                control_mode=control_mode,
                require_acknowledged=require_acknowledged,
                require_setup_profile=require_setup_profile,
            )
            responses[name] = response
            if stop_on_unconfirmed and response.get("applied") is not True:
                stopped = True
                break
        return _rig_response("rig_apply_all", responses, stopped=stopped)

    async def apply_scene_map(
        self,
        scenes: Mapping[str, SceneInput],
        *,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name, scene in scenes.items():
            response = await self.apply_scene(
                name,
                scene,
                control_mode=control_mode,
                require_acknowledged=require_acknowledged,
                require_setup_profile=require_setup_profile,
            )
            responses[name] = response
            if stop_on_unconfirmed and response.get("applied") is not True:
                stopped = True
                break
        return _rig_response("rig_apply_scene_map", responses, stopped=stopped)

    async def blackout(
        self,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile: bool | None = None,
    ) -> dict[str, object]:
        return await self.apply_all(
            {"sleep": 1},
            fixture_names=fixture_names,
            tag=tag,
            stop_on_unconfirmed=stop_on_unconfirmed,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
            require_setup_profile=require_setup_profile,
        )

    def state_snapshot(self) -> dict[str, object]:
        return {
            "fixtures": {
                name: controller.state_snapshot()
                for name, controller in self.controllers.items()
            }
        }

    def state_history(
        self,
        *,
        after_version: int = 0,
        limit: int | None = None,
    ) -> dict[str, object]:
        return {
            "fixtures": {
                name: controller.state_history(
                    after_version=after_version,
                    limit=limit,
                )
                for name, controller in self.controllers.items()
            }
        }

    def _selected_fixture_names(
        self,
        names: Iterable[str] | None,
        *,
        tag: str | None,
    ) -> tuple[str, ...]:
        selected = tuple(names) if names is not None else self.fixture_names(tag=tag)
        if not selected:
            raise ValueError("no fixtures selected")
        for name in selected:
            self.fixture(name)
        return selected

    def _setup_profile_controls_guard(self, explicit: bool | None) -> bool:
        return self.require_setup_profile_controls if explicit is None else explicit

    def _require_setup_profile_primitive_if_requested(
        self,
        name: str,
        primitive: str,
        require_setup_profile: bool | None,
    ) -> None:
        if self._setup_profile_controls_guard(require_setup_profile):
            self.require_setup_profile_primitive(name, primitive)


def fixture_from_mapping(payload: Mapping[str, object]) -> LightFixture:
    name = payload.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("fixture requires a non-empty name")
    raw_obj = payload.get("obj", 1)
    if not isinstance(raw_obj, int):
        raise ValueError("fixture obj must be an integer")
    raw_tags = payload.get("tags", ())
    if not isinstance(raw_tags, Iterable) or isinstance(raw_tags, str | bytes):
        raise ValueError("fixture tags must be an iterable of strings")
    tags = tuple(str(tag) for tag in raw_tags)
    profile = _setup_profile_from_fixture_mapping(payload)
    return LightFixture(
        name=name,
        config=_config_from_fixture_mapping(payload, setup_profile=profile),
        obj=raw_obj,
        tags=tags,
        setup_profile=profile,
    )


def rig_from_mapping(
    payload: Mapping[str, object],
    *,
    light_factories: Mapping[str, LightFactory] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    cue_library: CueLibrary | None = None,
    control_mode: int | None = None,
    require_acknowledged: bool | None = None,
    require_setup_profile_controls: bool | None = None,
) -> LightRig:
    return LightRig.from_mapping(
        payload,
        light_factories=light_factories,
        preset_library=preset_library,
        cue_library=cue_library,
        control_mode=control_mode,
        require_acknowledged=require_acknowledged,
        require_setup_profile_controls=require_setup_profile_controls,
    )


def async_rig_from_mapping(
    payload: Mapping[str, object],
    *,
    light_factories: Mapping[str, AsyncLightFactory] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    cue_library: CueLibrary | None = None,
    control_mode: int | None = None,
    require_acknowledged: bool | None = None,
    require_setup_profile_controls: bool | None = None,
) -> AsyncLightRig:
    return AsyncLightRig.from_mapping(
        payload,
        light_factories=light_factories,
        preset_library=preset_library,
        cue_library=cue_library,
        control_mode=control_mode,
        require_acknowledged=require_acknowledged,
        require_setup_profile_controls=require_setup_profile_controls,
    )


def load_rig(
    path: str | Path,
    *,
    light_factories: Mapping[str, LightFactory] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    cue_library: CueLibrary | None = None,
    control_mode: int | None = None,
    require_acknowledged: bool | None = None,
    require_setup_profile_controls: bool | None = None,
) -> LightRig:
    return LightRig.load(
        path,
        light_factories=light_factories,
        preset_library=preset_library,
        cue_library=cue_library,
        control_mode=control_mode,
        require_acknowledged=require_acknowledged,
        require_setup_profile_controls=require_setup_profile_controls,
    )


def load_async_rig(
    path: str | Path,
    *,
    light_factories: Mapping[str, AsyncLightFactory] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    cue_library: CueLibrary | None = None,
    control_mode: int | None = None,
    require_acknowledged: bool | None = None,
    require_setup_profile_controls: bool | None = None,
) -> AsyncLightRig:
    return AsyncLightRig.load(
        path,
        light_factories=light_factories,
        preset_library=preset_library,
        cue_library=cue_library,
        control_mode=control_mode,
        require_acknowledged=require_acknowledged,
        require_setup_profile_controls=require_setup_profile_controls,
    )


def rig_from_json(
    text: str,
    *,
    light_factories: Mapping[str, LightFactory] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    cue_library: CueLibrary | None = None,
    control_mode: int | None = None,
    require_acknowledged: bool | None = None,
    require_setup_profile_controls: bool | None = None,
) -> LightRig:
    payload = json.loads(text)
    if not isinstance(payload, Mapping):
        raise RigConfigError("rig JSON must contain an object")
    return rig_from_mapping(
        {str(key): value for key, value in payload.items()},
        light_factories=light_factories,
        preset_library=preset_library,
        cue_library=cue_library,
        control_mode=control_mode,
        require_acknowledged=require_acknowledged,
        require_setup_profile_controls=require_setup_profile_controls,
    )


def async_rig_from_json(
    text: str,
    *,
    light_factories: Mapping[str, AsyncLightFactory] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    cue_library: CueLibrary | None = None,
    control_mode: int | None = None,
    require_acknowledged: bool | None = None,
    require_setup_profile_controls: bool | None = None,
) -> AsyncLightRig:
    payload = json.loads(text)
    if not isinstance(payload, Mapping):
        raise RigConfigError("rig JSON must contain an object")
    return async_rig_from_mapping(
        {str(key): value for key, value in payload.items()},
        light_factories=light_factories,
        preset_library=preset_library,
        cue_library=cue_library,
        control_mode=control_mode,
        require_acknowledged=require_acknowledged,
        require_setup_profile_controls=require_setup_profile_controls,
    )


def rig_setup_profiles_from_report(
    payload: Mapping[str, object],
) -> dict[str, LightSetupProfile]:
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, Mapping):
        raise RigConfigError("rig setup report is missing fixtures")
    profiles: dict[str, LightSetupProfile] = {}
    for name, response in fixtures.items():
        profiles[str(name)] = _setup_profile_from_value(response)
    return profiles


def rig_profile_bundle_mapping(
    rig: LightRig | AsyncLightRig,
    profiles: Mapping[str, object] | None = None,
    *,
    profile_dir: str = "profiles",
) -> dict[str, object]:
    profile_map = _rig_profile_map(rig, profiles)
    _require_known_profile_fixtures(profile_map, rig.fixtures)
    profile_paths = _profile_relative_paths(profile_map, profile_dir)
    data: dict[str, object] = {
        "fixtures": [
            _fixture_bundle_mapping(fixture, profile_paths.get(name))
            for name, fixture in rig.fixtures.items()
        ],
        "control_mode": rig.control_mode,
        "require_acknowledged": rig.require_acknowledged,
        "require_setup_profile_controls": rig.require_setup_profile_controls,
    }
    if rig.preset_library is not None:
        data["presets"] = rig.preset_library.to_dict()
    if rig.cue_library is not None:
        data["cues"] = rig.cue_library.to_dict()
    return data


def save_rig_profile_bundle(
    rig: LightRig | AsyncLightRig,
    path: str | Path,
    profiles: Mapping[str, object] | None = None,
    *,
    profile_dir: str = "profiles",
    indent: int | None = 2,
    profile_indent: int | None = 2,
) -> dict[str, object]:
    profile_map = _rig_profile_map(rig, profiles)
    _require_known_profile_fixtures(profile_map, rig.fixtures)
    bundle = rig_profile_bundle_mapping(
        rig,
        profile_map,
        profile_dir=profile_dir,
    )
    rig_path = Path(path)
    rig_path.parent.mkdir(parents=True, exist_ok=True)
    written_profiles: dict[str, str] = {}
    for name, relative_path in _profile_relative_paths(
        profile_map,
        profile_dir,
    ).items():
        target = rig_path.parent / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        save_light_setup_profile(
            profile_map[name],
            target,
            indent=profile_indent,
        )
        written_profiles[name] = str(target)
    save_rig(bundle, rig_path, indent=indent)
    return {
        "rig_path": str(rig_path),
        "profile_paths": written_profiles,
        "mapping": bundle,
    }


def rig_to_json(
    rig: LightRig | AsyncLightRig | Mapping[str, object],
    *,
    indent: int | None = 2,
) -> str:
    payload = dict(rig) if isinstance(rig, Mapping) else rig.to_dict()
    return json.dumps(payload, indent=indent, sort_keys=True)


def save_rig(
    rig: LightRig | AsyncLightRig | Mapping[str, object],
    path: str | Path,
    *,
    indent: int | None = 2,
) -> None:
    Path(path).write_text(f"{rig_to_json(rig, indent=indent)}\n", encoding="utf-8")


def load_rig_mapping(path: str | Path) -> dict[str, object]:
    rig_path = Path(path)
    with rig_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise RigConfigError("rig file must contain a JSON object")
    return _resolve_rig_profile_paths(
        {str(key): value for key, value in payload.items()},
        base_dir=rig_path.parent,
    )


@dataclass(frozen=True)
class _RigDefinition:
    fixtures: tuple[LightFixture, ...]
    preset_library: ScenePresetLibrary | None
    cue_library: CueLibrary | None
    control_mode: int
    require_acknowledged: bool
    require_setup_profile_controls: bool


def _rig_definition_from_mapping(payload: Mapping[str, object]) -> _RigDefinition:
    return _RigDefinition(
        fixtures=_fixtures_from_rig_mapping(payload),
        preset_library=_preset_library_from_mapping(payload),
        cue_library=_cue_library_from_mapping(payload),
        control_mode=_mapping_int(payload, "control_mode", DEFAULT_CONTROL_MODE),
        require_acknowledged=bool(payload.get("require_acknowledged", False)),
        require_setup_profile_controls=bool(
            payload.get("require_setup_profile_controls", False)
        ),
    )


def _fixtures_from_rig_mapping(
    payload: Mapping[str, object],
) -> tuple[LightFixture, ...]:
    raw_fixtures = payload.get("fixtures")
    if raw_fixtures is None:
        raise RigConfigError("rig config requires fixtures")
    if isinstance(raw_fixtures, Mapping):
        return tuple(
            _named_fixture_from_mapping(str(name), value)
            for name, value in raw_fixtures.items()
        )
    if isinstance(raw_fixtures, str | bytes) or not isinstance(
        raw_fixtures,
        Iterable,
    ):
        raise RigConfigError("rig fixtures must be an array or object")
    return tuple(_fixture_payload(fixture) for fixture in raw_fixtures)


def _named_fixture_from_mapping(name: str, value: object) -> LightFixture:
    if isinstance(value, LightFixture):
        return value
    if not isinstance(value, Mapping):
        raise RigConfigError(f"fixture {name!r} must be an object")
    payload = dict(value)
    payload.setdefault("name", name)
    return fixture_from_mapping(payload)


def _preset_library_from_mapping(
    payload: Mapping[str, object],
) -> ScenePresetLibrary | None:
    raw_presets = payload.get("presets")
    if raw_presets is None:
        return None
    if not isinstance(raw_presets, Mapping):
        raise RigConfigError("rig presets must be an object")
    return ScenePresetLibrary.from_mapping(raw_presets)


def _cue_library_from_mapping(payload: Mapping[str, object]) -> CueLibrary | None:
    raw_cues = payload.get("cues")
    if raw_cues is None:
        return None
    if not isinstance(raw_cues, Mapping):
        raise RigConfigError("rig cues must be an object")
    return CueLibrary.from_mapping(raw_cues)


def _mapping_int(
    payload: Mapping[str, object],
    key: str,
    default: int,
) -> int:
    value = payload.get(key, default)
    if isinstance(value, str):
        return int(value, 0)
    return int(value)


def _fixture_map(fixtures: Iterable[FixtureInput]) -> dict[str, LightFixture]:
    fixture_items = [_fixture_payload(fixture) for fixture in fixtures]
    if not fixture_items:
        raise ValueError("rig requires at least one fixture")
    names = [fixture.name for fixture in fixture_items]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate fixture names: {', '.join(duplicates)}")
    return {fixture.name: fixture for fixture in fixture_items}


def _fixture_payload(fixture: FixtureInput) -> LightFixture:
    if isinstance(fixture, LightFixture):
        return fixture
    return fixture_from_mapping(fixture)


def _fixture_scene(fixture: LightFixture, scene: SceneInput) -> Scene:
    if isinstance(scene, Scene):
        return scene
    data = dict(scene)
    data.setdefault("obj", fixture.obj)
    return scene_from_mapping(data)


def _fixture_setup_profile_summary(
    fixture: LightFixture,
    *,
    primitives: Iterable[str] | None,
) -> dict[str, object]:
    config = (
        fixture.setup_profile.config
        if fixture.setup_profile is not None
        else fixture.config
    )
    data: dict[str, object] = {
        "fixture": fixture.name,
        "obj": fixture.obj,
        "tags": list(fixture.tags),
        "transport": config.transport,
        "config": config.to_dict(),
        "setup_profile": _setup_profile_summary(fixture.setup_profile),
    }
    if primitives is not None:
        readiness = _setup_profile_primitive_readiness_for(
            fixture.setup_profile,
            primitives,
        )
        data["primitive_ready_for"] = {
            name: item["ready"] is True for name, item in readiness.items()
        }
        data["primitive_readiness"] = readiness
        data["ready"] = all(item["ready"] is True for item in readiness.values())
    return data


def _rig_setup_profile_summary_response(
    responses: Mapping[str, object],
    *,
    require_setup_profile_controls: bool,
    primitives: Iterable[str] | None,
) -> dict[str, object]:
    fixture_responses = _mapping_values(responses)
    missing_profiles = [
        name
        for name, response in fixture_responses.items()
        if _fixture_profile_present(response) is not True
    ]
    unready = _fixture_unready_primitives(fixture_responses)
    data: dict[str, object] = {
        "action": "rig_setup_profiles",
        "fixtures": fixture_responses,
        "require_setup_profile_controls": require_setup_profile_controls,
        "complete": not missing_profiles,
        "missing_profiles": missing_profiles,
    }
    if primitives is not None:
        data["primitives"] = _primitive_names_from_fixture_responses(
            fixture_responses,
        )
        data["ready"] = not missing_profiles and not unready
        data["unready"] = unready
    return data


def _setup_profile_primitive_readiness_for(
    profile: LightSetupProfile | None,
    primitives: Iterable[str],
) -> dict[str, dict[str, object]]:
    payload: LightSetupProfile | Mapping[str, object]
    payload = profile if profile is not None else {}
    readiness: dict[str, dict[str, object]] = {}
    for primitive in primitives:
        item = setup_profile_primitive_readiness(payload, str(primitive))
        name = str(item["primitive"])
        readiness[name] = item
    return readiness


def _mapping_values(payload: Mapping[str, object]) -> dict[str, dict[str, object]]:
    values: dict[str, dict[str, object]] = {}
    for name, value in payload.items():
        if isinstance(value, Mapping):
            values[name] = {str(key): item for key, item in value.items()}
    return values


def _fixture_profile_present(payload: Mapping[str, object]) -> bool:
    setup_profile = payload.get("setup_profile")
    return isinstance(setup_profile, Mapping) and setup_profile.get("present") is True


def _fixture_unready_primitives(
    responses: Mapping[str, Mapping[str, object]],
) -> dict[str, list[str]]:
    unready: dict[str, list[str]] = {}
    for name, response in responses.items():
        primitive_ready = response.get("primitive_ready_for")
        if not isinstance(primitive_ready, Mapping):
            continue
        names = [
            str(primitive)
            for primitive, ready in primitive_ready.items()
            if ready is not True
        ]
        if names:
            unready[name] = names
    return unready


def _primitive_names_from_fixture_responses(
    responses: Mapping[str, Mapping[str, object]],
) -> list[str]:
    names: list[str] = []
    for response in responses.values():
        primitive_ready = response.get("primitive_ready_for")
        if not isinstance(primitive_ready, Mapping):
            continue
        for primitive in primitive_ready:
            name = str(primitive)
            if name not in names:
                names.append(name)
    return names


def _config_from_fixture_mapping(
    payload: Mapping[str, object],
    *,
    setup_profile: LightSetupProfile | None,
) -> LightConnectionConfig:
    raw_config = payload.get("config")
    if raw_config is None:
        if setup_profile is not None:
            return setup_profile.config
        config_values = {
            key: payload[key] for key in _config_field_names() if key in payload
        }
        return LightConnectionConfig.from_mapping(config_values)
    if setup_profile is not None:
        raise ValueError("fixture cannot define both config and setup profile")
    if isinstance(raw_config, LightConnectionConfig):
        return raw_config
    if not isinstance(raw_config, Mapping):
        raise ValueError("fixture config must be a mapping or LightConnectionConfig")
    config_values = {
        str(key): value
        for key, value in raw_config.items()
        if str(key) in _config_field_names()
    }
    return LightConnectionConfig.from_mapping(config_values)


def _setup_profile_from_fixture_mapping(
    payload: Mapping[str, object],
) -> LightSetupProfile | None:
    raw_profile = payload.get("setup_profile", payload.get("profile"))
    raw_profile_path = payload.get("profile_path")
    if raw_profile is not None and raw_profile_path is not None:
        raise ValueError("fixture cannot define both profile and profile_path")
    if raw_profile_path is not None:
        if not isinstance(raw_profile_path, str):
            raise ValueError("fixture profile_path must be a string")
        return load_light_setup_profile(raw_profile_path)
    if raw_profile is None:
        return None
    if isinstance(raw_profile, LightSetupProfile):
        return raw_profile
    if isinstance(raw_profile, str):
        return load_light_setup_profile(raw_profile)
    if isinstance(raw_profile, Mapping):
        return LightSetupProfile.from_mapping(
            {str(key): value for key, value in raw_profile.items()}
        )
    raise ValueError("fixture profile must be a path, mapping, or LightSetupProfile")


def _setup_profile_map(
    payload: Mapping[str, object],
) -> dict[str, LightSetupProfile]:
    if isinstance(payload.get("fixtures"), Mapping):
        return rig_setup_profiles_from_report(payload)
    return {
        str(name): _setup_profile_from_value(profile)
        for name, profile in payload.items()
    }


def _setup_profile_from_value(value: object) -> LightSetupProfile:
    if isinstance(value, LightSetupProfile):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("setup profile value must be a mapping or LightSetupProfile")
    payload = _string_key_dict(value)
    if payload.get("kind") == "setup-profile":
        return LightSetupProfile.from_mapping(payload)
    raw_profile = payload.get("setup_profile", payload.get("profile"))
    if raw_profile is not None:
        return _setup_profile_from_value(raw_profile)
    raw_setup = payload.get("setup_report")
    if isinstance(raw_setup, Mapping):
        return LightSetupProfile.from_setup_report(_string_key_dict(raw_setup))
    if "config" in payload:
        return LightSetupProfile.from_setup_report(payload)
    raise ValueError("setup profile value is missing setup evidence")


def _fixture_with_setup_profile(
    fixture: LightFixture,
    profile: LightSetupProfile | None,
) -> LightFixture:
    if profile is None:
        return fixture
    return LightFixture.from_setup_profile(
        fixture.name,
        profile,
        obj=fixture.obj,
        tags=fixture.tags,
    )


def _require_known_profile_fixtures(
    profiles: Mapping[str, LightSetupProfile],
    fixtures: Mapping[str, LightFixture],
) -> None:
    unknown = sorted(name for name in profiles if name not in fixtures)
    if unknown:
        raise ValueError("unknown fixture profile(s): " + ", ".join(unknown))


def _string_key_dict(payload: Mapping[object, object]) -> dict[str, object]:
    return {str(key): value for key, value in payload.items()}


def _rig_profile_map(
    rig: LightRig | AsyncLightRig,
    profiles: Mapping[str, object] | None,
) -> dict[str, LightSetupProfile]:
    if profiles is not None:
        return _setup_profile_map(profiles)
    return {
        name: fixture.setup_profile
        for name, fixture in rig.fixtures.items()
        if fixture.setup_profile is not None
    }


def _fixture_bundle_mapping(
    fixture: LightFixture,
    profile_path: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": fixture.name,
        "obj": fixture.obj,
        "tags": list(fixture.tags),
    }
    if profile_path is None:
        payload["config"] = fixture.config.to_dict()
    else:
        payload["profile_path"] = profile_path
    return payload


def _profile_relative_paths(
    profiles: Mapping[str, LightSetupProfile],
    profile_dir: str,
) -> dict[str, str]:
    directory = Path(profile_dir)
    if directory.is_absolute():
        raise ValueError("profile_dir must be a relative path")
    used: set[str] = set()
    paths: dict[str, str] = {}
    for name in profiles:
        filename = _profile_filename(name)
        base = filename.removesuffix(".json")
        candidate = filename
        index = 2
        while candidate in used:
            candidate = f"{base}-{index}.json"
            index += 1
        used.add(candidate)
        paths[name] = str(directory / candidate)
    return paths


def _profile_filename(name: str) -> str:
    stem = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in name.strip()
    ).strip("_")
    return f"{stem or 'fixture'}.json"


def _resolve_rig_profile_paths(
    payload: dict[str, object],
    *,
    base_dir: Path,
) -> dict[str, object]:
    raw_fixtures = payload.get("fixtures")
    if isinstance(raw_fixtures, Mapping):
        payload["fixtures"] = {
            str(name): _resolve_fixture_profile_path(value, base_dir=base_dir)
            for name, value in raw_fixtures.items()
        }
    elif not isinstance(raw_fixtures, str | bytes) and isinstance(
        raw_fixtures,
        Iterable,
    ):
        payload["fixtures"] = [
            _resolve_fixture_profile_path(value, base_dir=base_dir)
            for value in raw_fixtures
        ]
    return payload


def _resolve_fixture_profile_path(value: object, *, base_dir: Path) -> object:
    if not isinstance(value, Mapping):
        return value
    fixture = {str(key): item for key, item in value.items()}
    raw_profile = fixture.get("profile")
    raw_profile_path = fixture.get("profile_path")
    if isinstance(raw_profile_path, str):
        fixture["profile_path"] = str(_resolve_path(raw_profile_path, base_dir))
    if isinstance(raw_profile, str):
        fixture["profile"] = str(_resolve_path(raw_profile, base_dir))
    return fixture


def _resolve_path(path: str, base_dir: Path) -> Path:
    resolved = Path(path)
    if resolved.is_absolute():
        return resolved
    return base_dir / resolved


def _config_field_names() -> tuple[str, ...]:
    return tuple(field.name for field in fields(LightConnectionConfig))


def _rig_response(
    action: str,
    fixture_responses: Mapping[str, object],
    *,
    stopped: bool,
) -> dict[str, object]:
    return {
        "action": action,
        "fixtures": dict(fixture_responses),
        "applied": _all_fixtures_applied(fixture_responses),
        "reason": _rig_reason(fixture_responses),
        "stopped": stopped,
    }


def _fixture_plan_response(
    fixture: LightFixture,
    payload: Mapping[str, object],
) -> dict[str, object]:
    config = (
        fixture.setup_profile.config
        if fixture.setup_profile is not None
        else fixture.config
    )
    return {
        "fixture": fixture.name,
        "transport": config.transport,
        "config": config.to_dict(),
        "obj": fixture.obj,
        "tags": list(fixture.tags),
        **dict(payload),
    }


def _rig_plan_response(
    action: str,
    fixture_responses: Mapping[str, object],
) -> dict[str, object]:
    responses = dict(fixture_responses)
    return {
        "action": action,
        "dry_run": True,
        "fixtures": responses,
        "planned": bool(responses),
        "reason": "planned" if responses else "no_fixtures",
        "fixture_order": list(responses),
        "start_seq": _rig_plan_start_seq(responses),
        "next_seq": _rig_plan_next_seq(responses),
    }


def _rig_plan_start_seq(fixture_responses: Mapping[str, object]) -> int | None:
    starts = [
        value
        for response in fixture_responses.values()
        if isinstance(response, Mapping)
        and isinstance((value := response.get("start_seq")), int)
    ]
    return min(starts) if starts else None


def _rig_plan_next_seq(fixture_responses: Mapping[str, object]) -> int | None:
    next_values = [
        value
        for response in fixture_responses.values()
        if isinstance(response, Mapping)
        and isinstance((value := response.get("next_seq")), int)
    ]
    return max(next_values) if next_values else None


def _fixture_plan_map(plans: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    raw_plans = plans.get("fixtures")
    source = raw_plans if isinstance(raw_plans, Mapping) else plans
    fixture_plans: dict[str, Mapping[str, object]] = {}
    for name, plan in source.items():
        if not isinstance(plan, Mapping):
            raise ValueError(f"plan for fixture {name!r} must be an object")
        fixture_plans[str(name)] = plan
    return fixture_plans


def _plan_primitive_name(plan: Mapping[str, object]) -> str:
    action = str(plan.get("action", "scene"))
    if action in {"preset", "sequence", "cue", "run_named_cue"}:
        return action
    return "scene"


def _status_response(name: str, status: StatusSnapshot) -> dict[str, object]:
    payload, confirmed, error = status
    return {
        "fixture": name,
        "status": payload,
        "connection_confirmed": confirmed,
        "ok": confirmed and error is None,
        "error": error,
        "reason": "acknowledged" if confirmed and error is None else error,
    }


def _readiness_response(
    name: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    ready_for = payload.get("ready_for")
    read_status = (
        ready_for.get("read_status")
        if isinstance(ready_for, Mapping)
        else False
    )
    return {
        "fixture": name,
        "readiness": dict(payload),
        "ok": read_status is True,
        "reason": _readiness_reason(payload),
    }


def _validation_response(
    name: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    return {
        "fixture": name,
        "validation": dict(payload),
        "ok": payload.get("connection_confirmed") is True,
        "reason": _validation_reason(payload),
    }


def _fixture_readiness_payload(response: Mapping[str, object]) -> dict[str, object]:
    readiness = response.get("readiness")
    if isinstance(readiness, Mapping):
        return {str(key): value for key, value in readiness.items()}
    return {}


def _rig_readiness_failures(
    response: Mapping[str, object],
    capabilities: Iterable[str],
) -> dict[str, IntegrationNotReady]:
    fixtures = response.get("fixtures")
    if not isinstance(fixtures, Mapping):
        return {}
    failures: dict[str, IntegrationNotReady] = {}
    for name, fixture_response in fixtures.items():
        if not isinstance(fixture_response, Mapping):
            failures[str(name)] = IntegrationNotReady({}, capabilities)
            continue
        try:
            integration_require(
                _fixture_readiness_payload(fixture_response),
                capabilities,
            )
        except IntegrationNotReady as exc:
            failures[str(name)] = exc
    return failures


def _capabilities_response(
    name: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    return {
        "fixture": name,
        "capabilities": dict(payload),
        "ok": True,
        "reason": "available",
    }


def _snapshot_response(
    name: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    summary = payload.get("summary")
    connection_confirmed = (
        summary.get("connection_confirmed")
        if isinstance(summary, Mapping)
        else False
    )
    return {
        "fixture": name,
        "snapshot": dict(payload),
        "ok": connection_confirmed is True,
        "reason": _snapshot_reason(payload),
    }


def _connection_report_response(
    name: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    summary = payload.get("summary")
    selected_transport = (
        summary.get("selected_transport")
        if isinstance(summary, Mapping)
        else None
    )
    return {
        "fixture": name,
        "connection_report": dict(payload),
        "ok": payload.get("ok") is True,
        "transport": selected_transport,
        "config": payload.get("selected_config"),
        "reason": _connection_report_reason(payload),
    }


def _connection_report_reason(payload: Mapping[str, object]) -> str:
    if payload.get("ok") is True:
        return "route_selected"
    summary = payload.get("summary")
    if isinstance(summary, Mapping):
        blocker = summary.get("ble_blocker")
        if blocker:
            return str(blocker)
    return "no_route"


def _setup_report_response(
    name: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    return {
        "fixture": name,
        "setup_report": dict(payload),
        "ok": payload.get("ok") is True,
        "route_confirmed": payload.get("route_confirmed") is True,
        "config": payload.get("config"),
        "ready_for": _bool_map(payload.get("ready_for")),
        "validation_ready_for": _bool_map(payload.get("validation_ready_for")),
        "reason": _setup_report_reason(payload),
    }


def _setup_report_reason(payload: Mapping[str, object]) -> str:
    if payload.get("ok") is True:
        return "setup_ready"
    summary = payload.get("summary")
    if isinstance(summary, Mapping):
        errors = summary.get("errors")
        if isinstance(errors, list) and errors:
            return "; ".join(str(item) for item in errors)
        blocker = summary.get("ble_blocker")
        if blocker:
            return str(blocker)
    status_error = payload.get("status_error")
    if status_error:
        return str(status_error)
    if (
        payload.get("require_confirmed_route") is True
        and payload.get("route_confirmed") is not True
    ):
        return "no_route"
    return "not_ready"


def _bool_map(value: object) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item is True for key, item in value.items()}


def _readiness_reason(payload: Mapping[str, object]) -> str:
    error = payload.get("error")
    if error:
        return str(error)
    warnings = payload.get("warnings")
    if isinstance(warnings, list) and warnings:
        return "; ".join(str(item) for item in warnings)
    ready_for = payload.get("ready_for")
    if isinstance(ready_for, Mapping) and ready_for.get("read_status") is True:
        return "ready"
    return "not_ready"


def _validation_reason(payload: Mapping[str, object]) -> str:
    error = payload.get("error")
    if error:
        return str(error)
    unconfirmed = payload.get("unconfirmed")
    if isinstance(unconfirmed, list) and unconfirmed:
        return ",".join(str(item) for item in unconfirmed)
    if payload.get("connection_confirmed") is True:
        return "acknowledged"
    return "not_confirmed"


def _snapshot_reason(payload: Mapping[str, object]) -> str:
    summary = payload.get("summary")
    if not isinstance(summary, Mapping):
        return "not_ready"
    warnings = summary.get("warnings")
    if isinstance(warnings, list) and warnings:
        return "; ".join(str(item) for item in warnings)
    pending = summary.get("pending_action_ids")
    if isinstance(pending, list) and pending:
        return "pending:" + ",".join(str(item) for item in pending)
    if summary.get("connection_confirmed") is True:
        return "ready"
    blocker = summary.get("ble_blocker")
    if blocker:
        return str(blocker)
    return "not_ready"


def _preset_names(library: ScenePresetLibrary | None) -> tuple[str, ...]:
    return tuple(library.names()) if library is not None else ()


def _cue_names(library: CueLibrary | None) -> tuple[str, ...]:
    return tuple(library.names()) if library is not None else ()


def _state_version(snapshot: Mapping[str, object]) -> int:
    raw_version = snapshot.get("version")
    return raw_version if isinstance(raw_version, int) else 0


def _state_payload(snapshot: Mapping[str, object]) -> Mapping[str, object] | None:
    raw_state = snapshot.get("state")
    return raw_state if isinstance(raw_state, Mapping) else None


def _all_fixtures_applied(fixture_responses: Mapping[str, object]) -> bool:
    return bool(fixture_responses) and all(
        _fixture_response_applied(response)
        for response in fixture_responses.values()
    )


def _fixture_response_applied(response: object) -> bool:
    if not isinstance(response, Mapping):
        return False
    if "applied" in response:
        return response.get("applied") is True
    if "probe" in response:
        return True
    return response.get("ok") is not False


def _rig_reason(fixture_responses: Mapping[str, object]) -> str:
    if not fixture_responses:
        return "no_fixtures"
    failed: list[str] = []
    for name, response in fixture_responses.items():
        if _fixture_response_applied(response):
            continue
        reason = "unconfirmed"
        if isinstance(response, Mapping):
            raw_reason = response.get("reason") or response.get("error")
            if raw_reason is not None:
                reason = str(raw_reason)
        failed.append(f"{name}:{reason}")
    return "acknowledged" if not failed else ",".join(failed)
