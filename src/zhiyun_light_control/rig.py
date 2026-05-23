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
from .protocol import DEFAULT_CONTROL_MODE
from .state import SceneStateTracker


@dataclass(frozen=True)
class LightFixture:
    """A named controllable light in a local rig."""

    name: str
    config: LightConnectionConfig = field(default_factory=LightConnectionConfig)
    obj: int = 1
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "obj": self.obj,
            "tags": list(self.tags),
            "config": asdict(self.config),
        }


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
    ) -> None:
        self.fixtures = _fixture_map(fixtures)
        self.preset_library = preset_library
        self.cue_library = cue_library
        self.control_mode = control_mode
        self.require_acknowledged = require_acknowledged
        factories = dict(light_factories or {})
        self.controllers = {
            name: LightController(
                fixture.config,
                light_factory=factories.get(name),
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
    ) -> LightRig:
        return cls.from_mapping(
            load_rig_mapping(path),
            light_factories=light_factories,
            preset_library=preset_library,
            cue_library=cue_library,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
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
        }
        if self.preset_library is not None:
            data["presets"] = self.preset_library.to_dict()
        if self.cue_library is not None:
            data["cues"] = self.cue_library.to_dict()
        return data

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

    def controller(self, name: str) -> LightController:
        self.fixture(name)
        return self.controllers[name]

    def integration(
        self,
        name: str,
        *,
        allow_control: bool = False,
    ) -> LightIntegration:
        fixture = self.fixture(name)
        return LightIntegration(
            config=fixture.config,
            allow_control=allow_control,
            preset_names=_preset_names(self.preset_library),
            cue_names=_cue_names(self.cue_library),
            light_factory=self.controller(name).light_factory,
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
    ) -> dict[str, object]:
        fixture = self.fixture(name)
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
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        response = self.controller(name).apply_preset(
            preset,
            overrides=overrides,
            obj=fixture.obj,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        )
        return {"fixture": name, **response}

    def apply_all(
        self,
        scene: SceneInput,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = self.apply_scene(
                name,
                scene,
                control_mode=control_mode,
                require_acknowledged=require_acknowledged,
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
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name, scene in scenes.items():
            response = self.apply_scene(
                name,
                scene,
                control_mode=control_mode,
                require_acknowledged=require_acknowledged,
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
    ) -> dict[str, object]:
        return self.apply_all(
            {"sleep": 1},
            fixture_names=fixture_names,
            tag=tag,
            stop_on_unconfirmed=stop_on_unconfirmed,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
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
    ) -> None:
        self.fixtures = _fixture_map(fixtures)
        self.preset_library = preset_library
        self.cue_library = cue_library
        self.control_mode = control_mode
        self.require_acknowledged = require_acknowledged
        factories = dict(light_factories or {})
        self.controllers = {
            name: AsyncLightController(
                fixture.config,
                light_factory=factories.get(name),
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
    ) -> AsyncLightRig:
        return cls.from_mapping(
            load_rig_mapping(path),
            light_factories=light_factories,
            preset_library=preset_library,
            cue_library=cue_library,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
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
        }
        if self.preset_library is not None:
            data["presets"] = self.preset_library.to_dict()
        if self.cue_library is not None:
            data["cues"] = self.cue_library.to_dict()
        return data

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

    def controller(self, name: str) -> AsyncLightController:
        self.fixture(name)
        return self.controllers[name]

    def integration(
        self,
        name: str,
        *,
        allow_control: bool = False,
    ) -> AsyncLightIntegration:
        fixture = self.fixture(name)
        return AsyncLightIntegration(
            config=fixture.config,
            allow_control=allow_control,
            preset_names=_preset_names(self.preset_library),
            cue_names=_cue_names(self.cue_library),
            light_factory=self.controller(name).light_factory,
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
    ) -> dict[str, object]:
        fixture = self.fixture(name)
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
    ) -> dict[str, object]:
        fixture = self.fixture(name)
        response = await self.controller(name).apply_preset(
            preset,
            overrides=overrides,
            obj=fixture.obj,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        )
        return {"fixture": name, **response}

    async def apply_all(
        self,
        scene: SceneInput,
        *,
        fixture_names: Iterable[str] | None = None,
        tag: str | None = None,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name in self._selected_fixture_names(fixture_names, tag=tag):
            response = await self.apply_scene(
                name,
                scene,
                control_mode=control_mode,
                require_acknowledged=require_acknowledged,
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
    ) -> dict[str, object]:
        responses: dict[str, object] = {}
        stopped = False
        for name, scene in scenes.items():
            response = await self.apply_scene(
                name,
                scene,
                control_mode=control_mode,
                require_acknowledged=require_acknowledged,
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
    ) -> dict[str, object]:
        return await self.apply_all(
            {"sleep": 1},
            fixture_names=fixture_names,
            tag=tag,
            stop_on_unconfirmed=stop_on_unconfirmed,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
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
    return LightFixture(
        name=name,
        config=_config_from_fixture_mapping(payload),
        obj=raw_obj,
        tags=tags,
    )


def rig_from_mapping(
    payload: Mapping[str, object],
    *,
    light_factories: Mapping[str, LightFactory] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    cue_library: CueLibrary | None = None,
    control_mode: int | None = None,
    require_acknowledged: bool | None = None,
) -> LightRig:
    return LightRig.from_mapping(
        payload,
        light_factories=light_factories,
        preset_library=preset_library,
        cue_library=cue_library,
        control_mode=control_mode,
        require_acknowledged=require_acknowledged,
    )


def async_rig_from_mapping(
    payload: Mapping[str, object],
    *,
    light_factories: Mapping[str, AsyncLightFactory] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    cue_library: CueLibrary | None = None,
    control_mode: int | None = None,
    require_acknowledged: bool | None = None,
) -> AsyncLightRig:
    return AsyncLightRig.from_mapping(
        payload,
        light_factories=light_factories,
        preset_library=preset_library,
        cue_library=cue_library,
        control_mode=control_mode,
        require_acknowledged=require_acknowledged,
    )


def load_rig(
    path: str | Path,
    *,
    light_factories: Mapping[str, LightFactory] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    cue_library: CueLibrary | None = None,
    control_mode: int | None = None,
    require_acknowledged: bool | None = None,
) -> LightRig:
    return LightRig.load(
        path,
        light_factories=light_factories,
        preset_library=preset_library,
        cue_library=cue_library,
        control_mode=control_mode,
        require_acknowledged=require_acknowledged,
    )


def load_async_rig(
    path: str | Path,
    *,
    light_factories: Mapping[str, AsyncLightFactory] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    cue_library: CueLibrary | None = None,
    control_mode: int | None = None,
    require_acknowledged: bool | None = None,
) -> AsyncLightRig:
    return AsyncLightRig.load(
        path,
        light_factories=light_factories,
        preset_library=preset_library,
        cue_library=cue_library,
        control_mode=control_mode,
        require_acknowledged=require_acknowledged,
    )


def load_rig_mapping(path: str | Path) -> dict[str, object]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise RigConfigError("rig file must contain a JSON object")
    return {str(key): value for key, value in payload.items()}


@dataclass(frozen=True)
class _RigDefinition:
    fixtures: tuple[LightFixture, ...]
    preset_library: ScenePresetLibrary | None
    cue_library: CueLibrary | None
    control_mode: int
    require_acknowledged: bool


def _rig_definition_from_mapping(payload: Mapping[str, object]) -> _RigDefinition:
    return _RigDefinition(
        fixtures=_fixtures_from_rig_mapping(payload),
        preset_library=_preset_library_from_mapping(payload),
        cue_library=_cue_library_from_mapping(payload),
        control_mode=_mapping_int(payload, "control_mode", DEFAULT_CONTROL_MODE),
        require_acknowledged=bool(payload.get("require_acknowledged", False)),
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


def _config_from_fixture_mapping(
    payload: Mapping[str, object],
) -> LightConnectionConfig:
    raw_config = payload.get("config")
    if raw_config is None:
        config_values = {
            key: payload[key] for key in _config_field_names() if key in payload
        }
        return LightConnectionConfig.from_mapping(config_values)
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
