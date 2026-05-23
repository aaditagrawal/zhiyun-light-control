"""In-process SDK controller for scenes, presets, and cues."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .bridge import (
    LightConnectionConfig,
    LightFactory,
    close_light_factory,
    make_light_factory,
)
from .cues import CueLibrary
from .models import (
    CommandResult,
    Scene,
    flatten_command_batches,
    require_command_results,
)
from .presets import (
    SCENE_FIELDS,
    ScenePresetLibrary,
    merge_scene,
    scene_from_mapping,
    scene_from_optional_mapping,
)
from .protocol import DEFAULT_CONTROL_MODE
from .state import results_confirmed, unconfirmed_results_reason


class LightController:
    """Synchronous SDK facade for programmatic media-control workflows."""

    def __init__(
        self,
        config: LightConnectionConfig | None = None,
        *,
        light_factory: LightFactory | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
    ) -> None:
        if light_factory is None:
            light_factory = make_light_factory(config or LightConnectionConfig())
        self.light_factory = light_factory
        self.preset_library = preset_library
        self.cue_library = cue_library
        self.control_mode = control_mode
        self.require_acknowledged = require_acknowledged

    def __enter__(self) -> LightController:
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def close(self) -> None:
        close_light_factory(self.light_factory)

    def probe(self):
        with self.light_factory() as light:
            return light.probe()

    def apply_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        resolved = _scene_payload(scene)
        with self.light_factory() as light:
            results = light.apply_scene(
                resolved,
                control_mode=self._control_mode(control_mode),
            )
        self._require_acknowledged(results, require_acknowledged, action="scene")
        return _scene_response("scene", resolved, results)

    def scene_from_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        obj: int = 1,
    ) -> Scene:
        library = self._preset_library()
        return _preset_scene(library, name, overrides=overrides, obj=obj)

    def apply_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        scene = self.scene_from_preset(name, overrides=overrides, obj=obj)
        with self.light_factory() as light:
            results = light.apply_scene(
                scene,
                control_mode=self._control_mode(control_mode),
            )
        self._require_acknowledged(results, require_acknowledged, action="preset")
        return {
            "preset": name,
            **_scene_response("preset", scene, results),
        }

    def run_sequence(
        self,
        steps: Iterable[Mapping[str, object]],
        *,
        obj: int = 1,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        step_items = [dict(step) for step in steps]
        if not step_items:
            raise ValueError("sequence steps must be a non-empty iterable")
        with self.light_factory() as light:
            response, results = self._run_sequence_on_light(
                light,
                step_items,
                obj=obj,
                stop_on_unconfirmed=stop_on_unconfirmed,
                control_mode=self._control_mode(control_mode),
            )
        self._require_acknowledged(results, require_acknowledged, action="sequence")
        return response

    def run_cue(
        self,
        cue: Mapping[str, object],
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        steps = cue.get("steps")
        if not isinstance(steps, list):
            raise ValueError("cue steps must be a list")
        stop_on_unconfirmed = bool(cue.get("stop_on_unconfirmed"))
        return self.run_sequence(
            [dict(step) for step in steps if isinstance(step, Mapping)],
            obj=obj,
            stop_on_unconfirmed=stop_on_unconfirmed,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        )

    def run_named_cue(
        self,
        name: str,
        *,
        obj: int = 1,
        stop_on_unconfirmed: bool | None = None,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        cue = self._cue_library().get(name)
        if stop_on_unconfirmed is not None:
            cue["stop_on_unconfirmed"] = stop_on_unconfirmed
        response = self.run_cue(
            cue,
            obj=obj,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        )
        return {"cue": name, **response}

    def plan_sequence(
        self,
        steps: Iterable[Mapping[str, object]],
        *,
        obj: int = 1,
        stop_on_unconfirmed: bool = False,
    ) -> dict[str, object]:
        step_items = [dict(step) for step in steps]
        if not step_items:
            raise ValueError("sequence steps must be a non-empty iterable")
        current_scene: Scene | None = None
        planned_steps: list[dict[str, object]] = []
        for index, step in enumerate(step_items):
            response, current_scene = self._plan_step(
                step,
                index=index,
                obj=obj,
                current_scene=current_scene,
            )
            planned_steps.append(response)
        return {
            "action": "sequence",
            "steps": planned_steps,
            "scene": None if current_scene is None else current_scene.to_dict(),
            "stop_on_unconfirmed": stop_on_unconfirmed,
        }

    def _run_sequence_on_light(
        self,
        light: object,
        steps: list[dict[str, object]],
        *,
        obj: int,
        stop_on_unconfirmed: bool,
        control_mode: int,
    ) -> tuple[dict[str, object], list[CommandResult]]:
        current_scene: Scene | None = None
        all_results: list[CommandResult] = []
        step_responses: list[dict[str, object]] = []
        stopped = False
        for index, step in enumerate(steps):
            response, current_scene, results = self._run_step(
                light,
                step,
                index=index,
                obj=obj,
                current_scene=current_scene,
                control_mode=control_mode,
            )
            step_responses.append(response)
            all_results.extend(results)
            if stop_on_unconfirmed and not response["applied"]:
                stopped = True
                break
        return (
            {
                "action": "sequence",
                "steps": step_responses,
                "scene": None if current_scene is None else current_scene.to_dict(),
                "stopped": stopped,
                "applied": results_confirmed(tuple(all_results)),
                "reason": _results_reason(all_results),
            },
            all_results,
        )

    def _run_step(
        self,
        light: object,
        step: dict[str, object],
        *,
        index: int,
        obj: int,
        current_scene: Scene | None,
        control_mode: int,
    ) -> tuple[dict[str, object], Scene, list[CommandResult]]:
        if "to" in step:
            response, scene, results = self._run_transition_step(
                light,
                step,
                obj=obj,
                current_scene=current_scene,
                control_mode=control_mode,
            )
        elif "preset" in step:
            response, scene, results = self._run_preset_step(
                light,
                step,
                obj=obj,
                control_mode=control_mode,
            )
        else:
            scene = _step_scene(step, obj=obj)
            results = light.apply_scene(scene, control_mode=control_mode)
            response = _scene_response("scene", scene, results)
        response["index"] = index
        return response, scene, results

    def _run_preset_step(
        self,
        light: object,
        step: dict[str, object],
        *,
        obj: int,
        control_mode: int,
    ) -> tuple[dict[str, object], Scene, list[CommandResult]]:
        name = str(step["preset"])
        scene = _preset_scene(
            self._preset_library(),
            name,
            overrides=_step_preset_overrides(step),
            obj=obj,
        )
        results = light.apply_scene(scene, control_mode=control_mode)
        return (
            {"preset": name, **_scene_response("preset", scene, results)},
            scene,
            results,
        )

    def _run_transition_step(
        self,
        light: object,
        step: dict[str, object],
        *,
        obj: int,
        current_scene: Scene | None,
        control_mode: int,
    ) -> tuple[dict[str, object], Scene, list[CommandResult]]:
        target = step.get("to")
        if not isinstance(target, Mapping):
            raise ValueError("transition step requires a 'to' object")
        end = scene_from_optional_mapping(target, obj=obj)
        start = _transition_start(step, current_scene, obj=end.obj)
        steps = int(step.get("steps", 10))
        duration = float(step.get("duration", 1.0))
        easing = str(step.get("easing", "linear"))
        batches = light.transition_scene(
            start,
            end,
            steps=steps,
            duration=duration,
            easing=easing,
            control_mode=control_mode,
        )
        results = flatten_command_batches(batches)
        response = {
            "action": "transition",
            "from": start.to_dict(),
            "scene": end.to_dict(),
            "steps": steps,
            "duration": duration,
            "easing": easing,
            "batches": [[result.to_dict() for result in batch] for batch in batches],
            "applied": results_confirmed(tuple(results)),
            "reason": _results_reason(results),
        }
        return response, end, results

    def _plan_step(
        self,
        step: dict[str, object],
        *,
        index: int,
        obj: int,
        current_scene: Scene | None,
    ) -> tuple[dict[str, object], Scene]:
        if "to" in step:
            target = step.get("to")
            if not isinstance(target, Mapping):
                raise ValueError("transition step requires a 'to' object")
            scene = scene_from_optional_mapping(target, obj=obj)
            start = _transition_start(step, current_scene, obj=scene.obj)
            response = {
                "action": "transition",
                "from": start.to_dict(),
                "scene": scene.to_dict(),
                "steps": int(step.get("steps", 10)),
                "duration": float(step.get("duration", 1.0)),
                "easing": str(step.get("easing", "linear")),
            }
        elif "preset" in step:
            name = str(step["preset"])
            scene = _preset_scene(
                self._preset_library(),
                name,
                overrides=_step_preset_overrides(step),
                obj=obj,
            )
            response = {
                "action": "preset",
                "preset": name,
                "scene": scene.to_dict(),
            }
        else:
            scene = _step_scene(step, obj=obj)
            response = {"action": "scene", "scene": scene.to_dict()}
        response["index"] = index
        return response, scene

    def _control_mode(self, value: int | None) -> int:
        return self.control_mode if value is None else value

    def _require_acknowledged(
        self,
        results: Iterable[CommandResult],
        explicit: bool | None,
        *,
        action: str,
    ) -> None:
        if self.require_acknowledged if explicit is None else explicit:
            require_command_results(results, action=action)

    def _preset_library(self) -> ScenePresetLibrary:
        if self.preset_library is None:
            raise ValueError("no preset library configured")
        return self.preset_library

    def _cue_library(self) -> CueLibrary:
        if self.cue_library is None:
            raise ValueError("no cue library configured")
        return self.cue_library


def _scene_payload(scene: Scene | Mapping[str, object]) -> Scene:
    if isinstance(scene, Scene):
        return scene
    return scene_from_mapping(scene)


def _scene_response(
    action: str,
    scene: Scene,
    results: Iterable[CommandResult],
) -> dict[str, object]:
    result_items = list(results)
    return {
        "action": action,
        "scene": scene.to_dict(),
        "results": [result.to_dict() for result in result_items],
        "applied": results_confirmed(tuple(result_items)),
        "reason": _results_reason(result_items),
    }


def _results_reason(results: Iterable[CommandResult]) -> str | None:
    result_items = list(results)
    if results_confirmed(tuple(result_items)):
        return None
    return unconfirmed_results_reason(tuple(result_items))


def _preset_scene(
    library: ScenePresetLibrary,
    name: str,
    *,
    overrides: Mapping[str, object] | None,
    obj: int,
) -> Scene:
    override_data = {} if overrides is None else dict(overrides)
    return merge_scene(
        library.get(name),
        scene_from_optional_mapping(override_data, obj=obj),
        override_obj="obj" in override_data,
    )


def _step_scene(step: Mapping[str, object], *, obj: int) -> Scene:
    raw_scene = step.get("scene", _scene_fields_from_mapping(step))
    if not isinstance(raw_scene, Mapping):
        raise ValueError("scene step must be an object")
    return scene_from_optional_mapping(raw_scene, obj=obj)


def _step_preset_overrides(step: Mapping[str, object]) -> dict[str, object]:
    raw_overrides = step.get("overrides")
    if raw_overrides is not None:
        if not isinstance(raw_overrides, Mapping):
            raise ValueError("preset overrides must be an object")
        return dict(raw_overrides)
    return _scene_fields_from_mapping(step)


def _scene_fields_from_mapping(data: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in data.items()
        if key in SCENE_FIELDS and value is not None
    }


def _transition_start(
    step: Mapping[str, object],
    current_scene: Scene | None,
    *,
    obj: int,
) -> Scene:
    raw_start = step.get("from")
    if raw_start is not None:
        if not isinstance(raw_start, Mapping):
            raise ValueError("transition 'from' must be an object")
        return scene_from_optional_mapping(raw_start, obj=obj)
    if current_scene is not None and current_scene.obj == obj:
        return current_scene
    return Scene(obj=obj)
