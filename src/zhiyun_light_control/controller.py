"""In-process SDK controller for scenes, presets, and cues."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from inspect import isawaitable

from .async_client import AsyncZhiyunLight
from .bridge import (
    LightConnectionConfig,
    LightFactory,
    close_light_factory,
    make_light_factory,
)
from .commands import (
    execute_async_serialized_frame_plan,
    execute_serialized_frame_plan,
    scene_command_plan,
    transition_command_plans,
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
from .protocol import (
    DEFAULT_CONTROL_MODE,
    RUNTIME_TYPE,
    FunctionalValue,
    ParsedFrame,
    RuntimeCommand,
    brightness_payload,
    cct_payload,
    parse_brightness_payload,
    parse_cct_payload,
    parse_hsi_payload,
    parse_rgb_payload,
    parse_sleep_payload,
    register_payload,
    sleep_payload,
)
from .state import (
    SceneState,
    SceneStateTracker,
    results_confirmed,
    unconfirmed_results_reason,
)

AsyncLightFactory = Callable[[], object]


class LightController:
    """Synchronous SDK facade for programmatic media-control workflows."""

    def __init__(
        self,
        config: LightConnectionConfig | None = None,
        *,
        light_factory: LightFactory | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        state_tracker: SceneStateTracker | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
    ) -> None:
        if light_factory is None:
            light_factory = make_light_factory(config or LightConnectionConfig())
        self.light_factory = light_factory
        self.preset_library = preset_library
        self.cue_library = cue_library
        self.state_tracker = state_tracker or SceneStateTracker()
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

    def register(
        self,
        device_id: int = 0,
        group_id: int = 0,
        *,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        with self.light_factory() as light:
            result = light.exchange_runtime(
                RuntimeCommand.REGISTER_DEFAULT_GROUP,
                register_payload(device_id, group_id),
            )
        self._require_acknowledged([result], require_acknowledged, action="register")
        return _command_response("register", result)

    def read_brightness(
        self,
        obj: int = 0,
        *,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        return self._read_runtime_command(
            "read_brightness",
            RuntimeCommand.BRIGHTNESS,
            brightness_payload(obj, read=True),
            require_acknowledged=require_acknowledged,
        )

    def read_cct(
        self,
        obj: int = 0,
        *,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        return self._read_runtime_command(
            "read_cct",
            RuntimeCommand.CCT,
            cct_payload(obj, read=True),
            require_acknowledged=require_acknowledged,
        )

    def read_sleep(
        self,
        obj: int = 0,
        *,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        return self._read_runtime_command(
            "read_sleep",
            RuntimeCommand.SLEEP,
            sleep_payload(obj, read=True),
            require_acknowledged=require_acknowledged,
        )

    def set_brightness(
        self,
        value: float,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        scene = Scene(obj=obj, brightness=value)
        with self.light_factory() as light:
            result = light.set_brightness(
                obj,
                value,
                control_mode=self._control_mode(control_mode),
            )
        return self._record_primitive(
            "set_brightness",
            scene,
            result,
            require_acknowledged=require_acknowledged,
        )

    def set_cct(
        self,
        kelvin: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        scene = Scene(obj=obj, kelvin=kelvin)
        with self.light_factory() as light:
            result = light.set_cct(
                obj,
                kelvin,
                control_mode=self._control_mode(control_mode),
            )
        return self._record_primitive(
            "set_cct",
            scene,
            result,
            require_acknowledged=require_acknowledged,
        )

    def set_sleep(
        self,
        value: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        scene = Scene(obj=obj, sleep=value)
        with self.light_factory() as light:
            result = light.set_sleep(
                obj,
                value,
                control_mode=self._control_mode(control_mode),
            )
        return self._record_primitive(
            "set_sleep",
            scene,
            result,
            require_acknowledged=require_acknowledged,
        )

    def set_rgb(
        self,
        red: int,
        green: int,
        blue: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        scene = Scene(obj=obj, red=red, green=green, blue=blue)
        with self.light_factory() as light:
            result = light.set_rgb(
                obj,
                red,
                green,
                blue,
                control_mode=self._control_mode(control_mode),
            )
        return self._record_primitive(
            "set_rgb",
            scene,
            result,
            require_acknowledged=require_acknowledged,
        )

    def set_hsi(
        self,
        hue: float,
        saturation: float,
        intensity: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        scene = Scene(
            obj=obj,
            hue=hue,
            saturation=saturation,
            intensity=intensity,
        )
        with self.light_factory() as light:
            result = light.set_hsi(
                obj,
                hue,
                saturation,
                intensity,
                control_mode=self._control_mode(control_mode),
            )
        return self._record_primitive(
            "set_hsi",
            scene,
            result,
            require_acknowledged=require_acknowledged,
        )

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
        self._record_scene(resolved, "scene", results)
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
        self._record_scene(scene, "preset", results)
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
                action="sequence",
            )
        self._record_response_scene(response, "sequence", results)
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
        stop_on_unconfirmed = bool(cue.get("stop_on_unconfirmed"))
        step_items = _cue_steps(cue.get("steps"))
        with self.light_factory() as light:
            response, results = self._run_sequence_on_light(
                light,
                step_items,
                obj=obj,
                stop_on_unconfirmed=stop_on_unconfirmed,
                control_mode=self._control_mode(control_mode),
                action="cue",
            )
        self._record_response_scene(response, "cue", results)
        self._require_acknowledged(results, require_acknowledged, action="cue")
        return response

    def state(self) -> dict[str, object]:
        return self.state_tracker.to_dict()

    def state_snapshot(self) -> dict[str, object]:
        version, state = self.state_tracker.versioned_snapshot()
        return _state_payload(version, state)

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

    def plan_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        obj: int = 1,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return _plan_scene(
            scene,
            obj=obj,
            control_mode=self._control_mode(control_mode),
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        obj: int = 1,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return _plan_preset(
            self._preset_library(),
            name,
            overrides=overrides,
            obj=obj,
            control_mode=self._control_mode(control_mode),
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_transition(
        self,
        to_scene: Scene | Mapping[str, object],
        *,
        from_scene: Scene | Mapping[str, object] | None = None,
        obj: int = 1,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return _plan_transition(
            to_scene,
            from_scene=from_scene,
            obj=obj,
            steps=steps,
            duration=duration,
            easing=easing,
            control_mode=self._control_mode(control_mode),
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_cue(
        self,
        cue: Mapping[str, object],
        *,
        obj: int = 1,
        stop_on_unconfirmed: bool | None = None,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return _plan_cue(
            cue,
            obj=obj,
            stop_on_unconfirmed=stop_on_unconfirmed,
            preset_library=self.preset_library,
            control_mode=self._control_mode(control_mode),
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_named_cue(
        self,
        name: str,
        *,
        obj: int = 1,
        stop_on_unconfirmed: bool | None = None,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        response = self.plan_cue(
            self._cue_library().get(name),
            obj=obj,
            stop_on_unconfirmed=stop_on_unconfirmed,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return {"cue": name, **response}

    def plan_sequence(
        self,
        steps: Iterable[Mapping[str, object]],
        *,
        obj: int = 1,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return _plan_sequence(
            steps,
            obj=obj,
            stop_on_unconfirmed=stop_on_unconfirmed,
            preset_library=self.preset_library,
            control_mode=self._control_mode(control_mode),
            first_word=first_word,
            start_seq=start_seq,
        )

    def execute_plan(
        self,
        plan: Mapping[str, object],
        *,
        timeout: float | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        with self.light_factory() as light:
            results = execute_serialized_frame_plan(
                light,
                plan,
                timeout=timeout,
            )
        response = _plan_execution_response(plan, results)
        self._record_response_scene(response, "execute_plan", results)
        self._require_acknowledged(results, require_acknowledged, action="execute_plan")
        return response

    def _run_sequence_on_light(
        self,
        light: object,
        steps: list[dict[str, object]],
        *,
        obj: int,
        stop_on_unconfirmed: bool,
        control_mode: int,
        action: str,
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
                "action": action,
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

    def _record_scene(
        self,
        scene: Scene,
        action: str,
        results: Iterable[CommandResult],
    ) -> None:
        result_items = list(results)
        self.state_tracker.record(
            scene,
            source="sdk",
            action=action,
            applied=results_confirmed(tuple(result_items)),
            reason=_results_reason(result_items),
            results=result_items,
        )

    def _record_response_scene(
        self,
        response: Mapping[str, object],
        action: str,
        results: Iterable[CommandResult],
    ) -> None:
        scene = _response_scene(response)
        if scene is not None:
            self._record_scene(scene, action, results)

    def _record_primitive(
        self,
        action: str,
        scene: Scene,
        result: CommandResult,
        *,
        require_acknowledged: bool | None,
    ) -> dict[str, object]:
        self._record_scene(scene, action, [result])
        self._require_acknowledged([result], require_acknowledged, action=action)
        return _primitive_response(action, scene, result)

    def _read_runtime_command(
        self,
        action: str,
        command: RuntimeCommand,
        payload: bytes,
        *,
        require_acknowledged: bool | None,
    ) -> dict[str, object]:
        with self.light_factory() as light:
            result = light.exchange_runtime(command, payload)
        self._require_acknowledged([result], require_acknowledged, action=action)
        return _command_response(action, result)

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

    def _control_mode(self, value: int | None) -> int:
        return self.control_mode if value is None else value

    def _require_acknowledged(
        self,
        results: Iterable[CommandResult],
        explicit: bool | None,
        *,
        action: str,
    ) -> None:
        should_require = self.require_acknowledged if explicit is None else explicit
        if should_require:
            require_command_results(results, action=action)

    def _preset_library(self) -> ScenePresetLibrary:
        if self.preset_library is None:
            raise ValueError("no preset library configured")
        return self.preset_library

    def _cue_library(self) -> CueLibrary:
        if self.cue_library is None:
            raise ValueError("no cue library configured")
        return self.cue_library


class AsyncLightController:
    """Async SDK facade for BLE-native media-control workflows."""

    def __init__(
        self,
        config: LightConnectionConfig | None = None,
        *,
        light_factory: AsyncLightFactory | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        state_tracker: SceneStateTracker | None = None,
        control_mode: int = DEFAULT_CONTROL_MODE,
        require_acknowledged: bool = False,
    ) -> None:
        if light_factory is None:
            config = config or LightConnectionConfig(transport="ble")

            def default_light_factory() -> AsyncZhiyunLight:
                return open_async_light(config)

            light_factory = default_light_factory
        self.light_factory = light_factory
        self.preset_library = preset_library
        self.cue_library = cue_library
        self.state_tracker = state_tracker or SceneStateTracker()
        self.control_mode = control_mode
        self.require_acknowledged = require_acknowledged

    async def __aenter__(self) -> AsyncLightController:
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.close()

    async def close(self) -> None:
        await close_async_light_factory(self.light_factory)

    async def probe(self):
        async with self.light_factory() as light:
            return await light.probe()

    async def register(
        self,
        device_id: int = 0,
        group_id: int = 0,
        *,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        async with self.light_factory() as light:
            result = await light.exchange_runtime(
                RuntimeCommand.REGISTER_DEFAULT_GROUP,
                register_payload(device_id, group_id),
            )
        self._require_acknowledged([result], require_acknowledged, action="register")
        return _command_response("register", result)

    async def read_brightness(
        self,
        obj: int = 0,
        *,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        return await self._read_runtime_command(
            "read_brightness",
            RuntimeCommand.BRIGHTNESS,
            brightness_payload(obj, read=True),
            require_acknowledged=require_acknowledged,
        )

    async def read_cct(
        self,
        obj: int = 0,
        *,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        return await self._read_runtime_command(
            "read_cct",
            RuntimeCommand.CCT,
            cct_payload(obj, read=True),
            require_acknowledged=require_acknowledged,
        )

    async def read_sleep(
        self,
        obj: int = 0,
        *,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        return await self._read_runtime_command(
            "read_sleep",
            RuntimeCommand.SLEEP,
            sleep_payload(obj, read=True),
            require_acknowledged=require_acknowledged,
        )

    async def set_brightness(
        self,
        value: float,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        scene = Scene(obj=obj, brightness=value)
        async with self.light_factory() as light:
            result = await light.set_brightness(
                obj,
                value,
                control_mode=self._control_mode(control_mode),
            )
        return self._record_primitive(
            "set_brightness",
            scene,
            result,
            require_acknowledged=require_acknowledged,
        )

    async def set_cct(
        self,
        kelvin: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        scene = Scene(obj=obj, kelvin=kelvin)
        async with self.light_factory() as light:
            result = await light.set_cct(
                obj,
                kelvin,
                control_mode=self._control_mode(control_mode),
            )
        return self._record_primitive(
            "set_cct",
            scene,
            result,
            require_acknowledged=require_acknowledged,
        )

    async def set_sleep(
        self,
        value: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        scene = Scene(obj=obj, sleep=value)
        async with self.light_factory() as light:
            result = await light.set_sleep(
                obj,
                value,
                control_mode=self._control_mode(control_mode),
            )
        return self._record_primitive(
            "set_sleep",
            scene,
            result,
            require_acknowledged=require_acknowledged,
        )

    async def set_rgb(
        self,
        red: int,
        green: int,
        blue: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        scene = Scene(obj=obj, red=red, green=green, blue=blue)
        async with self.light_factory() as light:
            result = await light.set_rgb(
                obj,
                red,
                green,
                blue,
                control_mode=self._control_mode(control_mode),
            )
        return self._record_primitive(
            "set_rgb",
            scene,
            result,
            require_acknowledged=require_acknowledged,
        )

    async def set_hsi(
        self,
        hue: float,
        saturation: float,
        intensity: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        scene = Scene(
            obj=obj,
            hue=hue,
            saturation=saturation,
            intensity=intensity,
        )
        async with self.light_factory() as light:
            result = await light.set_hsi(
                obj,
                hue,
                saturation,
                intensity,
                control_mode=self._control_mode(control_mode),
            )
        return self._record_primitive(
            "set_hsi",
            scene,
            result,
            require_acknowledged=require_acknowledged,
        )

    async def apply_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        resolved = _scene_payload(scene)
        async with self.light_factory() as light:
            results = await light.apply_scene(
                resolved,
                control_mode=self._control_mode(control_mode),
            )
        self._record_scene(resolved, "scene", results)
        self._require_acknowledged(results, require_acknowledged, action="scene")
        return _scene_response("scene", resolved, results)

    def scene_from_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        obj: int = 1,
    ) -> Scene:
        return _preset_scene(
            self._preset_library(),
            name,
            overrides=overrides,
            obj=obj,
        )

    async def apply_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        scene = self.scene_from_preset(name, overrides=overrides, obj=obj)
        async with self.light_factory() as light:
            results = await light.apply_scene(
                scene,
                control_mode=self._control_mode(control_mode),
            )
        self._record_scene(scene, "preset", results)
        self._require_acknowledged(results, require_acknowledged, action="preset")
        return {"preset": name, **_scene_response("preset", scene, results)}

    async def run_sequence(
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
        async with self.light_factory() as light:
            response, results = await self._run_sequence_on_light(
                light,
                step_items,
                obj=obj,
                stop_on_unconfirmed=stop_on_unconfirmed,
                control_mode=self._control_mode(control_mode),
                action="sequence",
            )
        self._record_response_scene(response, "sequence", results)
        self._require_acknowledged(results, require_acknowledged, action="sequence")
        return response

    async def run_cue(
        self,
        cue: Mapping[str, object],
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        step_items = _cue_steps(cue.get("steps"))
        async with self.light_factory() as light:
            response, results = await self._run_sequence_on_light(
                light,
                step_items,
                obj=obj,
                stop_on_unconfirmed=bool(cue.get("stop_on_unconfirmed")),
                control_mode=self._control_mode(control_mode),
                action="cue",
            )
        self._record_response_scene(response, "cue", results)
        self._require_acknowledged(results, require_acknowledged, action="cue")
        return response

    def state(self) -> dict[str, object]:
        return self.state_tracker.to_dict()

    def state_snapshot(self) -> dict[str, object]:
        version, state = self.state_tracker.versioned_snapshot()
        return _state_payload(version, state)

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

    async def run_named_cue(
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
        response = await self.run_cue(
            cue,
            obj=obj,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
        )
        return {"cue": name, **response}

    def plan_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        obj: int = 1,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return _plan_scene(
            scene,
            obj=obj,
            control_mode=self._control_mode(control_mode),
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        obj: int = 1,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return _plan_preset(
            self._preset_library(),
            name,
            overrides=overrides,
            obj=obj,
            control_mode=self._control_mode(control_mode),
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_transition(
        self,
        to_scene: Scene | Mapping[str, object],
        *,
        from_scene: Scene | Mapping[str, object] | None = None,
        obj: int = 1,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return _plan_transition(
            to_scene,
            from_scene=from_scene,
            obj=obj,
            steps=steps,
            duration=duration,
            easing=easing,
            control_mode=self._control_mode(control_mode),
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_cue(
        self,
        cue: Mapping[str, object],
        *,
        obj: int = 1,
        stop_on_unconfirmed: bool | None = None,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return _plan_cue(
            cue,
            obj=obj,
            stop_on_unconfirmed=stop_on_unconfirmed,
            preset_library=self._preset_library_or_none(),
            control_mode=self._control_mode(control_mode),
            first_word=first_word,
            start_seq=start_seq,
        )

    def plan_named_cue(
        self,
        name: str,
        *,
        obj: int = 1,
        stop_on_unconfirmed: bool | None = None,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        response = self.plan_cue(
            self._cue_library().get(name),
            obj=obj,
            stop_on_unconfirmed=stop_on_unconfirmed,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return {"cue": name, **response}

    def plan_sequence(
        self,
        steps: Iterable[Mapping[str, object]],
        *,
        obj: int = 1,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        first_word: int = RUNTIME_TYPE,
        start_seq: int = 1,
    ) -> dict[str, object]:
        return _plan_sequence(
            steps,
            obj=obj,
            stop_on_unconfirmed=stop_on_unconfirmed,
            preset_library=self._preset_library_or_none(),
            control_mode=self._control_mode(control_mode),
            first_word=first_word,
            start_seq=start_seq,
        )

    async def execute_plan(
        self,
        plan: Mapping[str, object],
        *,
        timeout: float | None = None,
        require_acknowledged: bool | None = None,
    ) -> dict[str, object]:
        async with self.light_factory() as light:
            results = await execute_async_serialized_frame_plan(
                light,
                plan,
                timeout=timeout,
            )
        response = _plan_execution_response(plan, results)
        self._record_response_scene(response, "execute_plan", results)
        self._require_acknowledged(results, require_acknowledged, action="execute_plan")
        return response

    async def _run_sequence_on_light(
        self,
        light: object,
        steps: list[dict[str, object]],
        *,
        obj: int,
        stop_on_unconfirmed: bool,
        control_mode: int,
        action: str,
    ) -> tuple[dict[str, object], list[CommandResult]]:
        current_scene: Scene | None = None
        all_results: list[CommandResult] = []
        step_responses: list[dict[str, object]] = []
        stopped = False
        for index, step in enumerate(steps):
            response, current_scene, results = await self._run_step(
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
                "action": action,
                "steps": step_responses,
                "scene": None if current_scene is None else current_scene.to_dict(),
                "stopped": stopped,
                "applied": results_confirmed(tuple(all_results)),
                "reason": _results_reason(all_results),
            },
            all_results,
        )

    async def _run_step(
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
            response, scene, results = await self._run_transition_step(
                light,
                step,
                obj=obj,
                current_scene=current_scene,
                control_mode=control_mode,
            )
        elif "preset" in step:
            response, scene, results = await self._run_preset_step(
                light,
                step,
                obj=obj,
                control_mode=control_mode,
            )
        else:
            scene = _step_scene(step, obj=obj)
            results = await light.apply_scene(scene, control_mode=control_mode)
            response = _scene_response("scene", scene, results)
        response["index"] = index
        return response, scene, results

    def _record_scene(
        self,
        scene: Scene,
        action: str,
        results: Iterable[CommandResult],
    ) -> None:
        result_items = list(results)
        self.state_tracker.record(
            scene,
            source="sdk",
            action=action,
            applied=results_confirmed(tuple(result_items)),
            reason=_results_reason(result_items),
            results=result_items,
        )

    def _record_response_scene(
        self,
        response: Mapping[str, object],
        action: str,
        results: Iterable[CommandResult],
    ) -> None:
        scene = _response_scene(response)
        if scene is not None:
            self._record_scene(scene, action, results)

    def _record_primitive(
        self,
        action: str,
        scene: Scene,
        result: CommandResult,
        *,
        require_acknowledged: bool | None,
    ) -> dict[str, object]:
        self._record_scene(scene, action, [result])
        self._require_acknowledged([result], require_acknowledged, action=action)
        return _primitive_response(action, scene, result)

    async def _read_runtime_command(
        self,
        action: str,
        command: RuntimeCommand,
        payload: bytes,
        *,
        require_acknowledged: bool | None,
    ) -> dict[str, object]:
        async with self.light_factory() as light:
            result = await light.exchange_runtime(command, payload)
        self._require_acknowledged([result], require_acknowledged, action=action)
        return _command_response(action, result)

    async def _run_preset_step(
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
        results = await light.apply_scene(scene, control_mode=control_mode)
        return (
            {"preset": name, **_scene_response("preset", scene, results)},
            scene,
            results,
        )

    async def _run_transition_step(
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
        batches = await light.transition_scene(
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

    def _control_mode(self, value: int | None) -> int:
        return self.control_mode if value is None else value

    def _require_acknowledged(
        self,
        results: Iterable[CommandResult],
        explicit: bool | None,
        *,
        action: str,
    ) -> None:
        should_require = self.require_acknowledged if explicit is None else explicit
        if should_require:
            require_command_results(results, action=action)

    def _preset_library(self) -> ScenePresetLibrary:
        if self.preset_library is None:
            raise ValueError("no preset library configured")
        return self.preset_library

    def _preset_library_or_none(self) -> ScenePresetLibrary | None:
        return self.preset_library

    def _cue_library(self) -> CueLibrary:
        if self.cue_library is None:
            raise ValueError("no cue library configured")
        return self.cue_library


def open_async_light(config: LightConnectionConfig | None = None) -> AsyncZhiyunLight:
    config = config or LightConnectionConfig(transport="ble")
    if config.transport != "ble":
        raise ValueError("async light control currently supports BLE transport")
    backend = "direct" if config.ble_in_process else config.ble_backend
    if backend == "direct":
        return AsyncZhiyunLight.ble(
            address=config.address,
            name_contains=config.name_contains,
            profile=config.ble_profile,
            service_uuid=config.ble_service_uuid,
            write_uuid=config.ble_write_uuid,
            notify_uuid=config.ble_notify_uuid,
            timeout=config.timeout,
        )
    if backend == "macos-app":
        return AsyncZhiyunLight.macos_ble_app(
            address=config.address,
            name_contains=config.name_contains,
            profile=config.ble_profile,
            service_uuid=config.ble_service_uuid,
            write_uuid=config.ble_write_uuid,
            notify_uuid=config.ble_notify_uuid,
            timeout=config.timeout,
        )
    return AsyncZhiyunLight.isolated_ble(
        address=config.address,
        name_contains=config.name_contains,
        profile=config.ble_profile,
        service_uuid=config.ble_service_uuid,
        write_uuid=config.ble_write_uuid,
        notify_uuid=config.ble_notify_uuid,
        timeout=config.timeout,
        python=config.ble_python,
    )


async def close_async_light_factory(factory: AsyncLightFactory) -> None:
    close = getattr(factory, "close", None)
    if close is None:
        return
    result = close()
    if isawaitable(result):
        await result


def _cue_steps(raw_steps: object) -> list[dict[str, object]]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("cue steps must be a non-empty list")
    steps: list[dict[str, object]] = []
    for step in raw_steps:
        if not isinstance(step, Mapping):
            raise ValueError("cue steps must be objects")
        steps.append(dict(step))
    return steps


def _plan_scene(
    scene: Scene | Mapping[str, object],
    *,
    obj: int,
    control_mode: int,
    first_word: int,
    start_seq: int,
) -> dict[str, object]:
    resolved = _plan_scene_payload(scene, obj=obj)
    command_plan = scene_command_plan(
        resolved,
        control_mode=control_mode,
        first_word=first_word,
        start_seq=start_seq,
    )
    return {
        "dry_run": True,
        "action": "scene",
        "scene": resolved.to_dict(),
        "command_plan": command_plan.to_dict(),
        "control_mode": control_mode,
        "first_word": first_word,
        "first_word_hex": f"0x{first_word:04x}",
        "start_seq": start_seq,
        "next_seq": command_plan.next_seq,
    }


def _plan_preset(
    library: ScenePresetLibrary,
    name: str,
    *,
    overrides: Mapping[str, object] | None,
    obj: int,
    control_mode: int,
    first_word: int,
    start_seq: int,
) -> dict[str, object]:
    scene = _preset_scene(library, name, overrides=overrides, obj=obj)
    command_plan = scene_command_plan(
        scene,
        control_mode=control_mode,
        first_word=first_word,
        start_seq=start_seq,
    )
    return {
        "dry_run": True,
        "action": "preset",
        "preset": name,
        "scene": scene.to_dict(),
        "command_plan": command_plan.to_dict(),
        "control_mode": control_mode,
        "first_word": first_word,
        "first_word_hex": f"0x{first_word:04x}",
        "start_seq": start_seq,
        "next_seq": command_plan.next_seq,
    }


def _plan_transition(
    to_scene: Scene | Mapping[str, object],
    *,
    from_scene: Scene | Mapping[str, object] | None,
    obj: int,
    steps: int,
    duration: float,
    easing: str,
    control_mode: int,
    first_word: int,
    start_seq: int,
) -> dict[str, object]:
    scene = _plan_scene_payload(to_scene, obj=obj)
    start = (
        Scene(obj=scene.obj)
        if from_scene is None
        else _plan_scene_payload(from_scene, obj=scene.obj)
    )
    command_plans = transition_command_plans(
        start,
        scene,
        steps=steps,
        easing=easing,
        control_mode=control_mode,
        first_word=first_word,
        start_seq=start_seq,
    )
    next_seq = command_plans[-1].next_seq if command_plans else start_seq
    return {
        "dry_run": True,
        "action": "transition",
        "from": start.to_dict(),
        "scene": scene.to_dict(),
        "steps": steps,
        "duration": duration,
        "easing": easing,
        "command_batches": [plan.to_dict() for plan in command_plans],
        "control_mode": control_mode,
        "first_word": first_word,
        "first_word_hex": f"0x{first_word:04x}",
        "start_seq": start_seq,
        "next_seq": next_seq,
    }


def _plan_cue(
    cue: Mapping[str, object],
    *,
    obj: int,
    stop_on_unconfirmed: bool | None,
    preset_library: ScenePresetLibrary | None,
    control_mode: int,
    first_word: int,
    start_seq: int,
) -> dict[str, object]:
    sequence_stop = (
        bool(cue.get("stop_on_unconfirmed"))
        if stop_on_unconfirmed is None
        else stop_on_unconfirmed
    )
    return _plan_sequence(
        _cue_steps(cue.get("steps")),
        obj=obj,
        stop_on_unconfirmed=sequence_stop,
        preset_library=preset_library,
        control_mode=control_mode,
        first_word=first_word,
        start_seq=start_seq,
    )


def _plan_scene_payload(scene: Scene | Mapping[str, object], *, obj: int) -> Scene:
    if isinstance(scene, Scene):
        return scene
    return scene_from_optional_mapping(scene, obj=obj)


def _plan_sequence(
    steps: Iterable[Mapping[str, object]],
    *,
    obj: int,
    stop_on_unconfirmed: bool,
    preset_library: ScenePresetLibrary | None,
    control_mode: int,
    first_word: int,
    start_seq: int,
) -> dict[str, object]:
    step_items = [dict(step) for step in steps]
    if not step_items:
        raise ValueError("sequence steps must be a non-empty iterable")
    current_scene: Scene | None = None
    planned_steps: list[dict[str, object]] = []
    next_seq = start_seq
    for index, step in enumerate(step_items):
        response, current_scene, next_seq = _plan_step(
            step,
            index=index,
            obj=obj,
            current_scene=current_scene,
            preset_library=preset_library,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=next_seq,
        )
        planned_steps.append(response)
    return {
        "dry_run": True,
        "action": "sequence",
        "steps": planned_steps,
        "scene": None if current_scene is None else current_scene.to_dict(),
        "stop_on_unconfirmed": stop_on_unconfirmed,
        "control_mode": control_mode,
        "first_word": first_word,
        "first_word_hex": f"0x{first_word:04x}",
        "start_seq": start_seq,
        "next_seq": next_seq,
    }


def _plan_step(
    step: Mapping[str, object],
    *,
    index: int,
    obj: int,
    current_scene: Scene | None,
    preset_library: ScenePresetLibrary | None,
    control_mode: int,
    first_word: int,
    start_seq: int,
) -> tuple[dict[str, object], Scene, int]:
    if "to" in step:
        target = step.get("to")
        if not isinstance(target, Mapping):
            raise ValueError("transition step requires a 'to' object")
        scene = scene_from_optional_mapping(target, obj=obj)
        start = _transition_start(step, current_scene, obj=scene.obj)
        steps = int(step.get("steps", 10))
        easing = str(step.get("easing", "linear"))
        command_plans = transition_command_plans(
            start,
            scene,
            steps=steps,
            easing=easing,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        next_seq = command_plans[-1].next_seq if command_plans else start_seq
        response = {
            "action": "transition",
            "from": start.to_dict(),
            "scene": scene.to_dict(),
            "steps": steps,
            "duration": float(step.get("duration", 1.0)),
            "easing": easing,
            "command_batches": [plan.to_dict() for plan in command_plans],
            "start_seq": start_seq,
            "next_seq": next_seq,
        }
    elif "preset" in step:
        if preset_library is None:
            raise ValueError("no preset library configured")
        name = str(step["preset"])
        scene = _preset_scene(
            preset_library,
            name,
            overrides=_step_preset_overrides(step),
            obj=obj,
        )
        command_plan = scene_command_plan(
            scene,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        next_seq = command_plan.next_seq
        response = {
            "action": "preset",
            "preset": name,
            "scene": scene.to_dict(),
            "command_plan": command_plan.to_dict(),
            "start_seq": start_seq,
            "next_seq": next_seq,
        }
    else:
        scene = _step_scene(step, obj=obj)
        command_plan = scene_command_plan(
            scene,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        next_seq = command_plan.next_seq
        response = {
            "action": "scene",
            "scene": scene.to_dict(),
            "command_plan": command_plan.to_dict(),
            "start_seq": start_seq,
            "next_seq": next_seq,
        }
    response["index"] = index
    return response, scene, next_seq


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


def _response_scene(response: Mapping[str, object]) -> Scene | None:
    raw_scene = response.get("scene")
    if not isinstance(raw_scene, Mapping):
        return None
    return scene_from_mapping(raw_scene)


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


def _plan_execution_response(
    plan: Mapping[str, object],
    results: Iterable[CommandResult],
) -> dict[str, object]:
    result_items = list(results)
    response: dict[str, object] = {
        "action": "execute_plan",
        "planned_action": str(plan.get("action", "unknown")),
        "plan": dict(plan),
        "results": [result.to_dict() for result in result_items],
        "applied": results_confirmed(tuple(result_items)),
        "reason": _results_reason(result_items),
    }
    raw_scene = plan.get("scene")
    if isinstance(raw_scene, Mapping):
        response["scene"] = dict(raw_scene)
    for key in ("preset", "cue"):
        if key in plan:
            response[key] = plan[key]
    return response


def _primitive_response(
    action: str,
    scene: Scene,
    result: CommandResult,
) -> dict[str, object]:
    response = _scene_response(action, scene, [result])
    response["result"] = result.to_dict()
    response["acknowledged"] = result.acknowledged
    response["transport_status"] = result.transport_status
    decoded = _decoded_command_value(result)
    if decoded is not None:
        response["decoded"] = decoded
        response["value"] = decoded["value"]
        response["obj"] = decoded["obj"]
        response["operation"] = decoded["operation"]
    return response


def _command_response(
    action: str,
    result: CommandResult,
) -> dict[str, object]:
    response = {
        "action": action,
        "result": result.to_dict(),
        "acknowledged": result.acknowledged,
        "transport_status": result.transport_status,
    }
    decoded = _decoded_command_value(result)
    if decoded is not None:
        response["decoded"] = decoded
        response["value"] = decoded["value"]
        response["obj"] = decoded["obj"]
        response["operation"] = decoded["operation"]
    return response


def _decoded_command_value(result: CommandResult) -> dict[str, object] | None:
    if not result.acknowledged or result.ack is None:
        return None
    parser = _primitive_parser(result.command)
    if parser is None:
        return None
    try:
        return parser(result.ack).to_dict()
    except ValueError:
        return None


def _primitive_parser(
    command: int,
) -> Callable[[ParsedFrame], FunctionalValue] | None:
    if command == RuntimeCommand.BRIGHTNESS:
        return parse_brightness_payload
    if command == RuntimeCommand.CCT:
        return parse_cct_payload
    if command == RuntimeCommand.SLEEP:
        return parse_sleep_payload
    if command == RuntimeCommand.RGB:
        return parse_rgb_payload
    if command == RuntimeCommand.HSI:
        return parse_hsi_payload
    return None


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
