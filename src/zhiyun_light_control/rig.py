"""Named multi-light SDK helpers for media-control rigs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field, fields

from .bridge import LightConnectionConfig, LightFactory
from .controller import AsyncLightController, AsyncLightFactory, LightController
from .cues import CueLibrary
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

    def __enter__(self) -> LightRig:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def close(self) -> None:
        for controller in self.controllers.values():
            controller.close()

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

    async def __aenter__(self) -> AsyncLightRig:
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.close()

    async def close(self) -> None:
        for controller in self.controllers.values():
            await controller.close()

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
        return LightConnectionConfig(**config_values)
    if isinstance(raw_config, LightConnectionConfig):
        return raw_config
    if not isinstance(raw_config, Mapping):
        raise ValueError("fixture config must be a mapping or LightConnectionConfig")
    config_values = {
        str(key): value
        for key, value in raw_config.items()
        if str(key) in _config_field_names()
    }
    return LightConnectionConfig(**config_values)


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
