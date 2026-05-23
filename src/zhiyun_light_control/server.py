"""Small stdlib HTTP bridge for local media-production integrations."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import fields
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .bridge import close_light_factory
from .client import ZhiyunLight
from .cues import CueLibrary
from .devices import (
    BLE_BACKENDS,
    discover_transport_devices,
    inspect_ble_device,
    test_ble_endpoint_candidates,
)
from .discovery import (
    DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS,
    DEFAULT_DISCOVERY_CONTROL_KINDS,
    DEFAULT_DISCOVERY_FIRST_WORDS,
    DEFAULT_DISCOVERY_OBJECT_IDS,
    DEFAULT_DISCOVERY_REGISTER_DEVICE_IDS,
    DEFAULT_DISCOVERY_REGISTER_GROUP_IDS,
    discover_usb_primitives,
)
from .models import Scene
from .presets import ScenePresetLibrary, merge_scene, scene_from_optional_mapping
from .protocol import (
    DEFAULT_CONTROL_MODE,
    RUNTIME_TYPE,
    RuntimeCommand,
    brightness_payload,
    cct_payload,
    hsi_payload,
    register_payload,
    rgb_payload,
    sleep_payload,
)
from .state import SceneStateTracker, results_confirmed, unconfirmed_results_reason
from .status import read_sync_status
from .validation import validate_sync_light


class LightHttpServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        port: str | None = None,
        allow_control: bool = False,
        light_factory: Callable[[], object] | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        state_tracker: SceneStateTracker | None = None,
        cors_origin: str | None = "*",
        transport: str = "usb",
        ble_backend: str | None = None,
        ble_profile: str | None = None,
        ble_address: str | None = None,
        ble_name_contains: str | None = None,
        ble_python: str | None = None,
    ):
        super().__init__(server_address, LightRequestHandler)
        self.light_port = port
        self.allow_control = allow_control
        self.light_factory = light_factory or (lambda: ZhiyunLight.usb(port=port))
        self.preset_library = preset_library
        self.cue_library = cue_library
        self.state_tracker = state_tracker or SceneStateTracker()
        self.cors_origin = cors_origin
        self.transport = transport
        self.ble_backend = ble_backend
        self.ble_profile = ble_profile
        self.ble_address = ble_address
        self.ble_name_contains = ble_name_contains
        self.ble_python = ble_python


class LightRequestHandler(BaseHTTPRequestHandler):
    server: LightHttpServer

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._json({"ok": True})
            return
        if path == "/commands":
            self._json(
                {
                    "get": [
                        "/health",
                        "/manifest",
                        "/openapi.json",
                        "/probe",
                        "/status",
                        "/validate",
                        "/commands",
                        "/capabilities",
                        "/diagnostics",
                        "/integration",
                        "/ready",
                        "/devices",
                        "/events",
                        "/history",
                        "/presets",
                        "/cues",
                        "/state",
                    ],
                    "post": [
                        "/validate",
                        "/plan",
                        "/inspect-ble",
                        "/test-ble-endpoints",
                        "/register",
                        "/discover-usb",
                        "/brightness",
                        "/cct",
                        "/sleep",
                        "/rgb",
                        "/hsi",
                        "/frame",
                        "/scene",
                        "/transition",
                        "/preset",
                        "/sequence",
                        "/cue",
                    ],
                    "control_enabled": self.server.allow_control,
                    "presets": self.server.preset_library.names()
                    if self.server.preset_library
                    else [],
                    "cues": self.server.cue_library.names()
                    if self.server.cue_library
                    else [],
                }
            )
            return
        if path == "/manifest":
            self._json(
                integration_manifest_response(
                    allow_control=self.server.allow_control,
                    presets=self.server.preset_library.names()
                    if self.server.preset_library
                    else [],
                    cues=self.server.cue_library.names()
                    if self.server.cue_library
                    else [],
                    transport=self.server.transport,
                    ble_backend=self.server.ble_backend,
                    ble_profile=self.server.ble_profile,
                    ble_address=self.server.ble_address,
                    ble_name_contains=self.server.ble_name_contains,
                )
            )
            return
        if path == "/capabilities":
            self._json(
                capabilities_response(
                    allow_control=self.server.allow_control,
                    presets=self.server.preset_library.names()
                    if self.server.preset_library
                    else [],
                    cues=self.server.cue_library.names()
                    if self.server.cue_library
                    else [],
                )
            )
            return
        if path == "/diagnostics":
            self._json(self._handle_diagnostics())
            return
        if path == "/integration":
            try:
                self._json(self._handle_integration(parsed.query))
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/ready":
            self._json(self._handle_ready())
            return
        if path == "/devices":
            try:
                self._json(self._handle_devices(parsed.query))
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/events":
            self._handle_events(parsed.query)
            return
        if path == "/history":
            self._json(self._handle_history(parsed.query))
            return
        if path == "/openapi.json":
            self._json(openapi_schema())
            return
        if path == "/presets":
            library = self.server.preset_library
            self._json(library.to_dict() if library else {"scenes": {}})
            return
        if path == "/cues":
            library = self.server.cue_library
            self._json(library.to_dict() if library else {"cues": {}})
            return
        if path == "/state":
            self._json(self.server.state_tracker.to_dict())
            return
        if path == "/probe":
            with self.server.light_factory() as light:
                self._json(light.probe().to_dict())
            return
        if path == "/status":
            with self.server.light_factory() as light:
                self._json(read_sync_status(light, transport="http").to_dict())
            return
        if path == "/validate":
            self._json(self._handle_validate({}))
            return
        self._json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_common_headers(0)
        self.end_headers()

    def do_POST(self) -> None:
        try:
            body = self._read_json()
            path = urlparse(self.path).path
            if path == "/plan":
                result = self._handle_plan(body)
            elif path == "/inspect-ble":
                result = self._handle_inspect_ble(body)
            elif path == "/test-ble-endpoints":
                result = self._handle_test_ble_endpoints(body)
            elif path == "/validate":
                if _body_bool(body, "allow_control") and not self.server.allow_control:
                    self._json(
                        {
                            "error": (
                                "validation control checks require bridge "
                                "--allow-control"
                            )
                        },
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return
                result = self._handle_validate(body)
            elif path == "/discover-usb":
                if _body_bool(body, "allow_control") and not self.server.allow_control:
                    self._json(
                        {
                            "error": (
                                "USB discovery control probes require bridge "
                                "--allow-control"
                            )
                        },
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return
                result = self._handle_discover_usb(body)
            else:
                if not self.server.allow_control:
                    self._json(
                        {"error": "control endpoints require --allow-control"},
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return
                result = self._handle_control(path, body)
        except Exception as exc:  # pragma: no cover - keeps HTTP errors useful.
            self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._json(result)

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def _handle_control(self, path: str, body: dict[str, object]) -> dict[str, object]:
        obj = int(body.get("obj", 1))
        control_mode = _control_mode(body)
        with self.server.light_factory() as light:
            if path == "/register":
                result = light.exchange_runtime(
                    RuntimeCommand.REGISTER_DEFAULT_GROUP,
                    register_payload(int(body.get("device_id", 0))),
                )
                return result.to_dict()
            if path == "/brightness":
                result = light.exchange_runtime(
                    RuntimeCommand.BRIGHTNESS,
                    brightness_payload(
                        obj,
                        float(body["value"]),
                        control_mode=control_mode,
                    ),
                )
                self._record_scene(
                    Scene(obj=obj, brightness=float(body["value"])),
                    action="brightness",
                    results=[result],
                )
                return result.to_dict()
            if path == "/cct":
                result = light.exchange_runtime(
                    RuntimeCommand.CCT,
                    cct_payload(obj, int(body["kelvin"]), control_mode=control_mode),
                )
                self._record_scene(
                    Scene(obj=obj, kelvin=int(body["kelvin"])),
                    action="cct",
                    results=[result],
                )
                return result.to_dict()
            if path == "/sleep":
                result = light.exchange_runtime(
                    RuntimeCommand.SLEEP,
                    sleep_payload(obj, int(body["value"]), control_mode=control_mode),
                )
                self._record_scene(
                    Scene(obj=obj, sleep=int(body["value"])),
                    action="sleep",
                    results=[result],
                )
                return result.to_dict()
            if path == "/rgb":
                result = light.exchange_runtime(
                    RuntimeCommand.RGB,
                    rgb_payload(
                        obj,
                        int(body["red"]),
                        int(body["green"]),
                        int(body["blue"]),
                        control_mode=control_mode,
                    ),
                )
                self._record_scene(
                    Scene(
                        obj=obj,
                        red=int(body["red"]),
                        green=int(body["green"]),
                        blue=int(body["blue"]),
                    ),
                    action="rgb",
                    results=[result],
                )
                return result.to_dict()
            if path == "/hsi":
                result = light.exchange_runtime(
                    RuntimeCommand.HSI,
                    hsi_payload(
                        obj,
                        float(body["hue"]),
                        float(body["saturation"]),
                        int(body["intensity"]),
                        control_mode=control_mode,
                    ),
                )
                self._record_scene(
                    Scene(
                        obj=obj,
                        hue=float(body["hue"]),
                        saturation=float(body["saturation"]),
                        intensity=int(body["intensity"]),
                    ),
                    action="hsi",
                    results=[result],
                )
                return result.to_dict()
            if path == "/frame":
                result = light.exchange_frame(
                    _body_int(body, "first_word", RUNTIME_TYPE),
                    _body_int(body, "command"),
                    _payload_hex(body),
                    timeout=float(body.get("timeout", 0.8)),
                )
                return result.to_dict()
            if path == "/scene":
                scene = _scene_from_body(body, obj=obj)
                results = light.apply_scene(scene, control_mode=control_mode)
                self._record_scene(scene, action="scene", results=results)
                return {
                    "scene": scene.to_dict(),
                    "results": [result.to_dict() for result in results],
                }
            if path == "/transition":
                target_data = body.get("to")
                if target_data is None:
                    target_data = _scene_fields_from_body(body)
                if not isinstance(target_data, dict):
                    raise ValueError("transition 'to' must be an object")
                end = _scene_from_body(target_data, obj=obj)
                start = self._transition_start(body, obj=end.obj)
                steps = int(body.get("steps", 10))
                duration = float(body.get("duration", 1.0))
                easing = str(body.get("easing", "linear"))
                batches = light.transition_scene(
                    start,
                    end,
                    steps=steps,
                    duration=duration,
                    easing=easing,
                    control_mode=control_mode,
                )
                flat_results = [result for batch in batches for result in batch]
                self._record_scene(end, action="transition", results=flat_results)
                return {
                    "from": start.to_dict(),
                    "scene": end.to_dict(),
                    "steps": steps,
                    "duration": duration,
                    "easing": easing,
                    "batches": [
                        [result.to_dict() for result in batch] for batch in batches
                    ],
                }
            if path == "/preset":
                library = self.server.preset_library
                if library is None:
                    raise ValueError("no preset file loaded")
                name = str(body["name"])
                overrides = {
                    key: value
                    for key, value in body.items()
                    if key not in {"name", "control_mode"} and value is not None
                }
                scene = merge_scene(
                    library.get(name),
                    scene_from_optional_mapping(overrides, obj=obj),
                    override_obj="obj" in body,
                )
                results = light.apply_scene(scene, control_mode=control_mode)
                self._record_scene(scene, action="preset", results=results)
                return {
                    "preset": name,
                    "scene": scene.to_dict(),
                    "results": [result.to_dict() for result in results],
                }
            if path == "/sequence":
                return self._handle_sequence(
                    light,
                    body,
                    obj=obj,
                    control_mode=control_mode,
                )
            if path == "/cue":
                return self._handle_named_cue(
                    light,
                    body,
                    obj=obj,
                    control_mode=control_mode,
                )
        raise ValueError("unknown endpoint")

    def _handle_plan(self, body: dict[str, object]) -> dict[str, object]:
        action = _plan_action(body)
        obj = int(body.get("obj", 1))
        control_mode = _control_mode(body)
        if action == "sequence":
            response = self._plan_sequence(body, obj=obj)
        elif action == "preset":
            response = self._plan_preset(body, obj=obj)
        elif action == "transition":
            response = self._plan_transition(body, obj=obj)
        else:
            response = self._plan_scene(body, obj=obj)
        return {
            "dry_run": True,
            "control_mode": control_mode,
            **response,
        }

    def _handle_inspect_ble(self, body: dict[str, object]) -> dict[str, object]:
        backend = str(body.get("backend", self.server.ble_backend or "worker"))
        timeout = float(body.get("timeout", 5.0))
        address = _optional_text(body, "address") or self.server.ble_address
        name_contains = (
            _optional_text(body, "name_contains") or self.server.ble_name_contains
        )
        python = _optional_text(body, "python") or self.server.ble_python
        result = inspect_ble_device(
            backend=backend,
            timeout=timeout,
            address=address,
            name_contains=name_contains,
            python=python,
        ).to_dict()
        result.update(
            {
                "backend": backend,
                "timeout": timeout,
                "name_contains": name_contains,
            }
        )
        return result

    def _handle_test_ble_endpoints(self, body: dict[str, object]) -> dict[str, object]:
        backend = str(body.get("backend", self.server.ble_backend or "worker"))
        timeout = float(body.get("timeout", 5.0))
        address = _optional_text(body, "address") or self.server.ble_address
        name_contains = (
            _optional_text(body, "name_contains") or self.server.ble_name_contains
        )
        python = _optional_text(body, "python") or self.server.ble_python
        max_candidates = int(body.get("max_candidates", 4))
        return test_ble_endpoint_candidates(
            backend=backend,
            timeout=timeout,
            address=address,
            name_contains=name_contains,
            python=python,
            max_candidates=max_candidates,
        ).to_dict()

    def _plan_scene(self, body: dict[str, object], *, obj: int) -> dict[str, object]:
        scene_data = body.get("scene", _scene_fields_from_body(body))
        if not isinstance(scene_data, dict):
            raise ValueError("plan scene must be an object")
        scene = _scene_from_body(scene_data, obj=obj)
        return {"action": "scene", "scene": scene.to_dict()}

    def _plan_preset(self, body: dict[str, object], *, obj: int) -> dict[str, object]:
        library = self.server.preset_library
        if library is None:
            raise ValueError("no preset file loaded")
        name_value = body.get("preset", body.get("name"))
        if name_value is None:
            raise ValueError("plan preset requires name or preset")
        name = str(name_value)
        overrides = _sequence_preset_overrides(body)
        scene = merge_scene(
            library.get(name),
            scene_from_optional_mapping(overrides, obj=obj),
            override_obj="obj" in overrides,
        )
        return {"action": "preset", "preset": name, "scene": scene.to_dict()}

    def _plan_transition(
        self,
        body: dict[str, object],
        *,
        obj: int,
    ) -> dict[str, object]:
        target_data = body.get("to")
        if target_data is None:
            target_data = _scene_fields_from_body(body)
        if not isinstance(target_data, dict):
            raise ValueError("plan transition 'to' must be an object")
        end = _scene_from_body(target_data, obj=obj)
        start = self._transition_start(body, obj=end.obj)
        return {
            "action": "transition",
            "from": start.to_dict(),
            "scene": end.to_dict(),
            "steps": int(body.get("steps", 10)),
            "duration": float(body.get("duration", 1.0)),
            "easing": str(body.get("easing", "linear")),
        }

    def _plan_sequence(self, body: dict[str, object], *, obj: int) -> dict[str, object]:
        raw_steps = body.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("plan sequence steps must be a non-empty array")
        current_scene: Scene | None = None
        planned_steps: list[dict[str, object]] = []
        for index, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, dict):
                raise ValueError("plan sequence steps must be objects")
            step_response, current_scene = self._plan_sequence_step(
                raw_step,
                index=index,
                obj=obj,
                current_scene=current_scene,
            )
            planned_steps.append(step_response)
        return {
            "action": "sequence",
            "steps": planned_steps,
            "scene": None if current_scene is None else current_scene.to_dict(),
            "stop_on_unconfirmed": _body_bool(body, "stop_on_unconfirmed"),
        }

    def _plan_sequence_step(
        self,
        step: dict[str, object],
        *,
        index: int,
        obj: int,
        current_scene: Scene | None,
    ) -> tuple[dict[str, object], Scene]:
        if "to" in step:
            response = self._plan_sequence_transition(
                step,
                obj=obj,
                current_scene=current_scene,
            )
        elif "preset" in step:
            response = self._plan_preset(step, obj=obj)
        else:
            response = self._plan_scene(step, obj=obj)
        scene_payload = response.get("scene")
        if not isinstance(scene_payload, dict):
            raise ValueError("planned step did not produce a scene")
        scene = _scene_from_body(scene_payload, obj=obj)
        response["index"] = index
        return response, scene

    def _plan_sequence_transition(
        self,
        step: dict[str, object],
        *,
        obj: int,
        current_scene: Scene | None,
    ) -> dict[str, object]:
        target_data = step["to"]
        if not isinstance(target_data, dict):
            raise ValueError("plan sequence transition 'to' must be an object")
        end = _scene_from_body(target_data, obj=obj)
        start = _sequence_transition_start(self, step, current_scene, obj=end.obj)
        return {
            "action": "transition",
            "from": start.to_dict(),
            "scene": end.to_dict(),
            "steps": int(step.get("steps", 10)),
            "duration": float(step.get("duration", 1.0)),
            "easing": str(step.get("easing", "linear")),
        }

    def _handle_sequence(
        self,
        light,
        body: dict[str, object],
        *,
        obj: int,
        control_mode: int,
        state_action: str = "sequence",
    ) -> dict[str, object]:
        raw_steps = body.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("sequence steps must be a non-empty array")
        stop_on_unconfirmed = _body_bool(body, "stop_on_unconfirmed")
        current_scene: Scene | None = None
        sequence_results = []
        step_responses: list[dict[str, object]] = []
        stopped = False
        for index, raw_step in enumerate(raw_steps):
            if not isinstance(raw_step, dict):
                raise ValueError("sequence steps must be objects")
            step_response, step_scene, step_results = self._handle_sequence_step(
                light,
                raw_step,
                index=index,
                obj=obj,
                current_scene=current_scene,
                control_mode=control_mode,
            )
            step_responses.append(step_response)
            sequence_results.extend(step_results)
            current_scene = step_scene
            if stop_on_unconfirmed and not step_response["applied"]:
                stopped = True
                break
        applied = results_confirmed(sequence_results)
        reason = None if applied else unconfirmed_results_reason(sequence_results)
        if current_scene is not None:
            self._record_scene(
                current_scene,
                action=state_action,
                results=sequence_results,
            )
        return {
            "steps": step_responses,
            "stopped": stopped,
            "applied": applied,
            "reason": reason,
        }

    def _handle_named_cue(
        self,
        light,
        body: dict[str, object],
        *,
        obj: int,
        control_mode: int,
    ) -> dict[str, object]:
        library = self.server.cue_library
        if library is None:
            raise ValueError("no cue file loaded")
        name_value = body.get("cue", body.get("name"))
        if name_value is None:
            raise ValueError("cue request requires name or cue")
        name = str(name_value)
        cue = library.get(name)
        if "stop_on_unconfirmed" in body:
            cue["stop_on_unconfirmed"] = _body_bool(body, "stop_on_unconfirmed")
        result = self._handle_sequence(
            light,
            cue,
            obj=obj,
            control_mode=control_mode,
            state_action="cue",
        )
        return {"cue": name, **result}

    def _handle_sequence_step(
        self,
        light,
        step: dict[str, object],
        *,
        index: int,
        obj: int,
        current_scene: Scene | None,
        control_mode: int,
    ):
        if "to" in step:
            response, scene, results = self._handle_sequence_transition(
                light,
                step,
                obj=obj,
                current_scene=current_scene,
                control_mode=control_mode,
            )
        elif "preset" in step:
            response, scene, results = self._handle_sequence_preset(
                light,
                step,
                obj=obj,
                control_mode=control_mode,
            )
        else:
            response, scene, results = self._handle_sequence_scene(
                light,
                step,
                obj=obj,
                control_mode=control_mode,
            )
        applied = results_confirmed(results)
        response.update(
            {
                "index": index,
                "applied": applied,
                "reason": None if applied else unconfirmed_results_reason(results),
            }
        )
        return response, scene, results

    def _handle_sequence_scene(
        self,
        light,
        step: dict[str, object],
        *,
        obj: int,
        control_mode: int,
    ):
        scene_data = step.get("scene", _scene_fields_from_body(step))
        if not isinstance(scene_data, dict):
            raise ValueError("sequence scene step must be an object")
        scene = _scene_from_body(scene_data, obj=obj)
        results = light.apply_scene(scene, control_mode=control_mode)
        return (
            {
                "action": "scene",
                "scene": scene.to_dict(),
                "results": [result.to_dict() for result in results],
            },
            scene,
            results,
        )

    def _handle_sequence_preset(
        self,
        light,
        step: dict[str, object],
        *,
        obj: int,
        control_mode: int,
    ):
        library = self.server.preset_library
        if library is None:
            raise ValueError("no preset file loaded")
        name = str(step["preset"])
        overrides = _sequence_preset_overrides(step)
        scene = merge_scene(
            library.get(name),
            scene_from_optional_mapping(overrides, obj=obj),
            override_obj="obj" in overrides,
        )
        results = light.apply_scene(scene, control_mode=control_mode)
        return (
            {
                "action": "preset",
                "preset": name,
                "scene": scene.to_dict(),
                "results": [result.to_dict() for result in results],
            },
            scene,
            results,
        )

    def _handle_sequence_transition(
        self,
        light,
        step: dict[str, object],
        *,
        obj: int,
        current_scene: Scene | None,
        control_mode: int,
    ):
        target_data = step["to"]
        if not isinstance(target_data, dict):
            raise ValueError("sequence transition 'to' must be an object")
        end = _scene_from_body(target_data, obj=obj)
        start = _sequence_transition_start(self, step, current_scene, obj=end.obj)
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
        results = [result for batch in batches for result in batch]
        return (
            {
                "action": "transition",
                "from": start.to_dict(),
                "scene": end.to_dict(),
                "steps": steps,
                "duration": duration,
                "easing": easing,
                "batches": [
                    [result.to_dict() for result in batch] for batch in batches
                ],
            },
            end,
            results,
        )

    def _handle_validate(self, body: dict[str, object]) -> dict[str, object]:
        allow_control = _body_bool(body, "allow_control")
        include_object_reads = _body_bool(body, "include_object_reads")
        include_color = _body_bool(body, "include_color")
        with self.server.light_factory() as light:
            report = validate_sync_light(
                light,
                transport="http",
                allow_control=allow_control,
                include_object_reads=include_object_reads,
                include_color=include_color,
                device_id=int(body.get("device_id", 0)),
                obj=int(body.get("obj", 1)),
                brightness=float(body.get("brightness", 35.0)),
                kelvin=int(body.get("kelvin", 5600)),
                sleep=int(body.get("sleep", 0)),
                red=int(body.get("red", 255)),
                green=int(body.get("green", 255)),
                blue=int(body.get("blue", 255)),
                hue=float(body.get("hue", 0.0)),
                saturation=float(body.get("saturation", 0.0)),
                intensity=int(body.get("intensity", 35)),
                control_mode=_control_mode(body),
            )
        return report.to_dict()

    def _handle_diagnostics(self) -> dict[str, object]:
        status, connection_confirmed, error = self._status_snapshot()
        return diagnostics_response(
            allow_control=self.server.allow_control,
            transport=self.server.transport,
            ble_backend=self.server.ble_backend,
            ble_profile=self.server.ble_profile,
            ble_address=self.server.ble_address,
            ble_name_contains=self.server.ble_name_contains,
            connection_confirmed=connection_confirmed,
            status=status,
            error=error,
        )

    def _handle_integration(self, query: str) -> dict[str, object]:
        presets = (
            self.server.preset_library.names() if self.server.preset_library else []
        )
        cues = self.server.cue_library.names() if self.server.cue_library else []
        manifest = integration_manifest_response(
            allow_control=self.server.allow_control,
            presets=presets,
            cues=cues,
            transport=self.server.transport,
            ble_backend=self.server.ble_backend,
            ble_profile=self.server.ble_profile,
            ble_address=self.server.ble_address,
            ble_name_contains=self.server.ble_name_contains,
        )
        capabilities = capabilities_response(
            allow_control=self.server.allow_control,
            presets=presets,
            cues=cues,
        )
        ready = self._handle_ready()
        devices = self._handle_devices(query)
        return integration_snapshot_response(
            manifest=manifest,
            capabilities=capabilities,
            ready=ready,
            devices=devices,
        )

    def _handle_ready(self) -> dict[str, object]:
        status, connection_confirmed, error = self._status_snapshot()
        version, state = self.server.state_tracker.versioned_snapshot()
        devices = discover_transport_devices(
            configured_transport=self.server.transport,
            configured_usb_port=self.server.light_port,
            include_ble=False,
            include_ble_status=(
                self.server.transport == "ble"
                and self.server.ble_backend == "macos-app"
            ),
            ble_backend=self.server.ble_backend or "worker",
            ble_name_contains=self.server.ble_name_contains,
            ble_python=self.server.ble_python,
        )
        return readiness_response(
            allow_control=self.server.allow_control,
            transport=self.server.transport,
            ble_backend=self.server.ble_backend,
            ble_profile=self.server.ble_profile,
            ble_address=self.server.ble_address,
            ble_name_contains=self.server.ble_name_contains,
            connection_confirmed=connection_confirmed,
            status=status,
            error=error,
            devices=devices,
            state_version=version,
            state=None if state is None else state.to_dict(),
        )

    def _status_snapshot(self) -> tuple[dict[str, object], bool, str | None]:
        status: dict[str, object]
        connection_confirmed = False
        error: str | None = None
        try:
            with self.server.light_factory() as light:
                report = read_sync_status(light, transport=self.server.transport)
            status = report.to_dict()
            connection_confirmed = report.connection_confirmed
        except Exception as exc:  # pragma: no cover - endpoint keeps errors useful.
            error = str(exc)
            status = {"ok": False, "error": error}
        return status, connection_confirmed, error

    def _handle_discover_usb(self, body: dict[str, object]) -> dict[str, object]:
        if self.server.transport != "usb":
            raise ValueError("USB discovery requires a bridge running transport=usb")
        with self.server.light_factory() as light:
            report = discover_usb_primitives(
                light,
                object_ids=_body_int_tuple(
                    body,
                    "object_ids",
                    default=DEFAULT_DISCOVERY_OBJECT_IDS,
                ),
                first_words=_body_int_tuple(
                    body,
                    "first_words",
                    default=DEFAULT_DISCOVERY_FIRST_WORDS,
                ),
                control_object_ids=_body_optional_int_tuple(
                    body,
                    "control_object_ids",
                ),
                control_first_words=_body_int_tuple(
                    body,
                    "control_first_words",
                    default=DEFAULT_DISCOVERY_CONTROL_FIRST_WORDS,
                ),
                register_device_ids=_body_int_tuple(
                    body,
                    "register_device_ids",
                    default=DEFAULT_DISCOVERY_REGISTER_DEVICE_IDS,
                ),
                register_group_ids=_body_int_tuple(
                    body,
                    "register_group_ids",
                    default=DEFAULT_DISCOVERY_REGISTER_GROUP_IDS,
                ),
                control_kinds=_body_text_tuple(
                    body,
                    "control_kinds",
                    default=DEFAULT_DISCOVERY_CONTROL_KINDS,
                ),
                control_modes=_body_optional_int_tuple(body, "control_modes"),
                post_register_reads=_body_bool(
                    body,
                    "post_register_reads",
                    default=False,
                ),
                timeout=float(body.get("timeout", 0.5)),
                allow_control=_body_bool(body, "allow_control"),
                brightness=float(body.get("brightness", 35.0)),
                kelvin=int(body.get("kelvin", 5600)),
                sleep=int(body.get("sleep", 0)),
            )
        return report.to_dict()

    def _handle_devices(self, query: str) -> dict[str, object]:
        params = parse_qs(query)
        backend = _query_text(
            params,
            "ble_backend",
            default=self.server.ble_backend or "worker",
        )
        return discover_transport_devices(
            configured_transport=self.server.transport,
            configured_usb_port=self.server.light_port,
            include_ble=_query_bool(params, "include_ble", default=False),
            include_ble_status=_query_bool(
                params,
                "include_ble_status",
                default=False,
            ),
            ble_backend=backend,
            ble_timeout=_query_float(params, "timeout", default=5.0),
            ble_name_contains=_query_text(
                params,
                "name_contains",
                default=self.server.ble_name_contains,
            ),
            ble_python=self.server.ble_python,
        )

    def _handle_events(self, query: str) -> None:
        params = parse_qs(query)
        limit = _query_int(params, "limit", default=0)
        timeout = _query_float(params, "timeout", default=30.0)
        include_initial = _query_bool(params, "initial", default=True)
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        if limit > 0:
            self.send_header("connection", "close")
            self.close_connection = True
        else:
            self.send_header("connection", "keep-alive")
        if self.server.cors_origin:
            self.send_header("access-control-allow-origin", self.server.cors_origin)
        self.end_headers()
        sent = 0
        version, state = self.server.state_tracker.versioned_snapshot()
        try:
            if include_initial:
                self._write_state_event(version, state)
                sent += 1
            while limit <= 0 or sent < limit:
                next_version, next_state = self.server.state_tracker.wait_for_update(
                    version,
                    timeout=timeout,
                )
                if next_version == version:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    if limit > 0:
                        break
                    continue
                version = next_version
                self._write_state_event(version, next_state)
                sent += 1
        except OSError:
            return

    def _handle_history(self, query: str) -> dict[str, object]:
        params = parse_qs(query)
        after = _query_int(params, "after", default=0)
        limit = _query_int(params, "limit", default=50)
        version, _state = self.server.state_tracker.versioned_snapshot()
        events = self.server.state_tracker.history(
            after_version=after,
            limit=limit,
        )
        return {
            "version": version,
            "after": after,
            "limit": limit,
            "events": [
                {"version": event_version, "state": state.to_dict()}
                for event_version, state in events
            ],
        }

    def _write_state_event(self, version: int, state) -> None:
        payload = {
            "version": version,
            "state": {"scene": None} if state is None else state.to_dict(),
        }
        data = json.dumps(payload, sort_keys=True)
        self.wfile.write(f"id: {version}\n".encode())
        self.wfile.write(b"event: state\n")
        self.wfile.write(f"data: {data}\n\n".encode())
        self.wfile.flush()

    def _transition_start(self, body: dict[str, object], *, obj: int) -> Scene:
        start_data = body.get("from")
        if start_data is not None:
            if not isinstance(start_data, dict):
                raise ValueError("transition 'from' must be an object")
            return _scene_from_body(start_data, obj=obj)
        snapshot = self.server.state_tracker.snapshot()
        if snapshot is not None and snapshot.scene.obj == obj:
            return snapshot.scene
        return Scene(obj=obj)

    def _record_scene(
        self,
        scene: Scene,
        *,
        action: str,
        results,
    ) -> None:
        applied = results_confirmed(results)
        self.server.state_tracker.record(
            scene,
            source="http",
            action=action,
            applied=applied,
            reason=None if applied else unconfirmed_results_reason(results),
            results=results,
        )

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("content-length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(
        self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self._send_common_headers(len(data))
        self.end_headers()
        self.wfile.write(data)

    def _send_common_headers(self, content_length: int) -> None:
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(content_length))
        if self.server.cors_origin:
            self.send_header("access-control-allow-origin", self.server.cors_origin)
            self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
            self.send_header("access-control-allow-headers", "content-type")
            self.send_header("access-control-max-age", "600")


def openapi_schema() -> dict[str, object]:
    """Return a compact OpenAPI schema for local bridge integrations."""

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Zhiyun Light Control Bridge",
            "version": "0.1.0",
            "description": (
                "Local JSON API for probing and controlling Zhiyun MOLUS lights."
            ),
        },
        "paths": {
            "/health": {"get": _operation("Process health", "Health")},
            "/manifest": {
                "get": _operation(
                    "Describe integration surfaces for media controllers",
                    "Manifest",
                )
            },
            "/openapi.json": {
                "get": _operation("Machine-readable bridge schema", "OpenApiSchema")
            },
            "/commands": {
                "get": _operation("List supported bridge endpoints", "Commands")
            },
            "/capabilities": {
                "get": _operation(
                    "Describe control primitives and confirmation semantics",
                    "Capabilities",
                )
            },
            "/diagnostics": {
                "get": _operation(
                    "Check bridge transport readiness",
                    "Diagnostics",
                )
            },
            "/integration": {
                "get": _operation(
                    "Return a controller integration snapshot",
                    "Integration",
                )
            },
            "/ready": {
                "get": _operation(
                    "Summarize controller readiness",
                    "Readiness",
                )
            },
            "/devices": {
                "get": _operation(
                    "Discover local USB ports and optional BLE devices",
                    "Devices",
                )
            },
            "/events": {
                "get": {
                    "summary": "Stream bridge state events",
                    "responses": {
                        "200": {
                            "description": "Server-Sent Events stream",
                            "content": {
                                "text/event-stream": {
                                    "schema": {"type": "string"}
                                }
                            },
                        }
                    },
                }
            },
            "/history": {
                "get": _operation(
                    "Read recent bridge state events",
                    "History",
                )
            },
            "/probe": {"get": _operation("Probe the connected light", "Probe")},
            "/status": {
                "get": _operation(
                    "Read ACK-backed light status",
                    "Status",
                )
            },
            "/validate": {
                "get": _operation("Run read-only hardware validation", "Validation"),
                "post": _operation(
                    "Run hardware validation with optional gated controls",
                    "Validation",
                    request_schema="ValidationRequest",
                ),
            },
            "/plan": {
                "post": _operation(
                    "Resolve a scene, preset, transition, or sequence without writes",
                    "PlanResponse",
                    request_schema="PlanRequest",
                )
            },
            "/inspect-ble": {
                "post": _operation(
                    "Inspect BLE GATT services and characteristics",
                    "BleInspect",
                    request_schema="BleInspectRequest",
                )
            },
            "/test-ble-endpoints": {
                "post": _operation(
                    "Probe suggested BLE endpoints with read-only identity command",
                    "BleEndpointTest",
                    request_schema="BleEndpointTestRequest",
                )
            },
            "/discover-usb": {
                "post": _operation(
                    "Run a bounded USB protocol discovery matrix",
                    "UsbDiscovery",
                    request_schema="UsbDiscoveryRequest",
                )
            },
            "/presets": {"get": _operation("List loaded scene presets", "Presets")},
            "/cues": {"get": _operation("List loaded named cues", "Cues")},
            "/state": {"get": _operation("Read requested bridge state", "State")},
            "/register": {
                "post": _operation(
                    "Register to the default group",
                    "CommandResult",
                    request_schema="RegisterRequest",
                )
            },
            "/brightness": {
                "post": _operation(
                    "Set brightness",
                    "CommandResult",
                    request_schema="BrightnessRequest",
                )
            },
            "/cct": {
                "post": _operation(
                    "Set color temperature",
                    "CommandResult",
                    request_schema="CctRequest",
                )
            },
            "/sleep": {
                "post": _operation(
                    "Set sleep/power value",
                    "CommandResult",
                    request_schema="SleepRequest",
                )
            },
            "/rgb": {
                "post": _operation(
                    "Set RGB values",
                    "CommandResult",
                    request_schema="RgbRequest",
                )
            },
            "/hsi": {
                "post": _operation(
                    "Set HSI values",
                    "CommandResult",
                    request_schema="HsiRequest",
                )
            },
            "/frame": {
                "post": _operation(
                    "Exchange one raw frame",
                    "CommandResult",
                    request_schema="FrameRequest",
                )
            },
            "/scene": {
                "post": _operation(
                    "Apply a scene",
                    "SceneApplyResponse",
                    request_schema="Scene",
                )
            },
            "/transition": {
                "post": _operation(
                    "Apply a timed transition",
                    "TransitionResponse",
                    request_schema="TransitionRequest",
                )
            },
            "/preset": {
                "post": _operation(
                    "Apply a loaded preset with optional overrides",
                    "PresetResponse",
                    request_schema="PresetRequest",
                )
            },
            "/sequence": {
                "post": _operation(
                    "Run an ordered cue sequence",
                    "SequenceResponse",
                    request_schema="SequenceRequest",
                )
            },
            "/cue": {
                "post": _operation(
                    "Run a loaded named cue",
                    "CueResponse",
                    request_schema="CueRequest",
                )
            },
        },
        "components": {"schemas": _openapi_schemas()},
    }


def _operation(
    summary: str,
    response_schema: str,
    *,
    request_schema: str | None = None,
) -> dict[str, object]:
    operation: dict[str, object] = {
        "summary": summary,
        "responses": {
            "200": {
                "description": "JSON response",
                "content": _json_schema_ref(response_schema),
            }
        },
    }
    if request_schema is not None:
        operation["requestBody"] = {
            "required": False,
            "content": _json_schema_ref(request_schema),
        }
    return operation


def _json_schema_ref(name: str) -> dict[str, object]:
    return {
        "application/json": {
            "schema": {"$ref": f"#/components/schemas/{name}"}
        }
    }


def _openapi_schemas() -> dict[str, object]:
    number_or_null = {"oneOf": [{"type": "number"}, {"type": "null"}]}
    integer_or_null = {"oneOf": [{"type": "integer"}, {"type": "null"}]}
    control_mode = {"oneOf": [{"type": "integer"}, {"type": "string"}]}
    return {
        "Health": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
        "OpenApiSchema": {"type": "object"},
        "Manifest": {"type": "object", "additionalProperties": True},
        "Commands": {
            "type": "object",
            "properties": {
                "get": {"type": "array", "items": {"type": "string"}},
                "post": {"type": "array", "items": {"type": "string"}},
                "control_enabled": {"type": "boolean"},
                "presets": {"type": "array", "items": {"type": "string"}},
                "cues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["get", "post", "control_enabled", "presets", "cues"],
        },
        "Capabilities": {"type": "object", "additionalProperties": True},
        "Diagnostics": {"type": "object", "additionalProperties": True},
        "Integration": {"type": "object", "additionalProperties": True},
        "ReadinessAction": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "id": {"type": "string"},
                "label": {"type": "string"},
                "ready": {"type": "boolean"},
                "category": {"type": "string"},
                "method": {"type": "string"},
                "path": {"type": "string"},
                "command": {"type": "string"},
                "required_for": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["id", "label", "ready", "category", "required_for"],
        },
        "Readiness": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "ready_for": {"type": "object", "additionalProperties": True},
                "requirements": {
                    "type": "object",
                    "additionalProperties": True,
                },
                "actions": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/ReadinessAction"},
                },
                "warnings": {"type": "array", "items": {"type": "string"}},
                "next_steps": {"type": "array", "items": {"type": "string"}},
            },
        },
        "Devices": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "configured_transport": {"type": "string"},
                "usb": {"type": "object", "additionalProperties": True},
                "ble": {"type": "object", "additionalProperties": True},
            },
        },
        "History": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "version": {"type": "integer"},
                "after": {"type": "integer"},
                "limit": {"type": "integer"},
                "events": {"type": "array", "items": {"type": "object"}},
            },
        },
        "Probe": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "device_identifier": {"type": ["string", "null"]},
                "generation": {"type": ["string", "null"]},
                "firmware": {"type": ["string", "null"]},
                "device_id": {"type": ["integer", "null"]},
                "voltage_status": {"type": ["integer", "null"]},
            },
        },
        "Status": {"type": "object", "additionalProperties": True},
        "CommandResult": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "command": {"type": "integer"},
                "tx_hex": {"type": "string"},
                "rx_hex": {"type": ["string", "null"]},
                "sent": {"type": "boolean"},
                "acknowledged": {"type": "boolean"},
                "transport_status": {"type": "string"},
            },
            "required": [
                "command",
                "tx_hex",
                "sent",
                "acknowledged",
                "transport_status",
            ],
        },
        "Validation": {"type": "object", "additionalProperties": True},
        "PlanRequest": {"type": "object", "additionalProperties": True},
        "PlanResponse": {"type": "object", "additionalProperties": True},
        "BleInspectRequest": {"type": "object", "additionalProperties": True},
        "BleInspect": {"type": "object", "additionalProperties": True},
        "BleEndpointTestRequest": {"type": "object", "additionalProperties": True},
        "BleEndpointTest": {"type": "object", "additionalProperties": True},
        "UsbDiscovery": {"type": "object", "additionalProperties": True},
        "Presets": {"type": "object", "additionalProperties": True},
        "Cues": {"type": "object", "additionalProperties": True},
        "State": {"type": "object", "additionalProperties": True},
        "ValidationRequest": {
            "type": "object",
            "properties": {
                "allow_control": {"type": "boolean"},
                "include_object_reads": {"type": "boolean"},
                "include_color": {"type": "boolean"},
                "obj": {"type": "integer"},
                "brightness": {"type": "number"},
                "kelvin": {"type": "integer"},
                "control_mode": control_mode,
            },
        },
        "RegisterRequest": {
            "type": "object",
            "properties": {"device_id": {"type": "integer"}},
        },
        "UsbDiscoveryRequest": {
            "type": "object",
            "properties": {
                "allow_control": {"type": "boolean"},
                "object_ids": {"type": "array", "items": control_mode},
                "first_words": {"type": "array", "items": control_mode},
                "control_object_ids": {"type": "array", "items": control_mode},
                "control_first_words": {"type": "array", "items": control_mode},
                "register_device_ids": {"type": "array", "items": control_mode},
                "register_group_ids": {"type": "array", "items": control_mode},
                "control_kinds": {"type": "array", "items": {"type": "string"}},
                "control_modes": {"type": "array", "items": control_mode},
                "timeout": {"type": "number"},
                "brightness": {"type": "number"},
                "kelvin": {"type": "integer"},
                "sleep": {"type": "integer"},
            },
        },
        "BrightnessRequest": {
            "type": "object",
            "properties": {
                "obj": {"type": "integer"},
                "value": {"type": "number"},
                "control_mode": control_mode,
            },
            "required": ["value"],
        },
        "CctRequest": {
            "type": "object",
            "properties": {
                "obj": {"type": "integer"},
                "kelvin": {"type": "integer"},
                "control_mode": control_mode,
            },
            "required": ["kelvin"],
        },
        "SleepRequest": {
            "type": "object",
            "properties": {
                "obj": {"type": "integer"},
                "value": {"type": "integer"},
                "control_mode": control_mode,
            },
            "required": ["value"],
        },
        "RgbRequest": {
            "type": "object",
            "properties": {
                "obj": {"type": "integer"},
                "red": {"type": "integer"},
                "green": {"type": "integer"},
                "blue": {"type": "integer"},
                "control_mode": control_mode,
            },
            "required": ["red", "green", "blue"],
        },
        "HsiRequest": {
            "type": "object",
            "properties": {
                "obj": {"type": "integer"},
                "hue": {"type": "number"},
                "saturation": {"type": "number"},
                "intensity": {"type": "integer"},
                "control_mode": control_mode,
            },
            "required": ["hue", "saturation", "intensity"],
        },
        "FrameRequest": {
            "type": "object",
            "properties": {
                "first_word": {"oneOf": [{"type": "integer"}, {"type": "string"}]},
                "command": {"oneOf": [{"type": "integer"}, {"type": "string"}]},
                "payload_hex": {"type": "string"},
                "timeout": {"type": "number"},
            },
            "required": ["command"],
        },
        "Scene": {
            "type": "object",
            "properties": {
                "obj": {"type": "integer"},
                "brightness": number_or_null,
                "kelvin": integer_or_null,
                "sleep": integer_or_null,
                "red": integer_or_null,
                "green": integer_or_null,
                "blue": integer_or_null,
                "hue": number_or_null,
                "saturation": number_or_null,
                "intensity": integer_or_null,
                "control_mode": control_mode,
            },
        },
        "SceneApplyResponse": {
            "type": "object",
            "properties": {
                "scene": {"$ref": "#/components/schemas/Scene"},
                "results": {
                    "type": "array",
                    "items": {"$ref": "#/components/schemas/CommandResult"},
                },
            },
        },
        "TransitionRequest": {
            "type": "object",
            "properties": {
                "from": {"$ref": "#/components/schemas/Scene"},
                "to": {"$ref": "#/components/schemas/Scene"},
                "steps": {"type": "integer"},
                "duration": {"type": "number"},
                "easing": {"type": "string"},
                "control_mode": control_mode,
            },
        },
        "TransitionResponse": {"type": "object", "additionalProperties": True},
        "PresetRequest": {
            "allOf": [
                {"$ref": "#/components/schemas/Scene"},
                {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ]
        },
        "PresetResponse": {"type": "object", "additionalProperties": True},
        "SequenceRequest": {"type": "object", "additionalProperties": True},
        "SequenceResponse": {"type": "object", "additionalProperties": True},
        "CueRequest": {"type": "object", "additionalProperties": True},
        "CueResponse": {"type": "object", "additionalProperties": True},
    }


def integration_manifest_response(
    *,
    allow_control: bool,
    presets: list[str],
    cues: list[str],
    transport: str,
    ble_backend: str | None,
    ble_profile: str | None,
    ble_address: str | None,
    ble_name_contains: str | None,
) -> dict[str, object]:
    """Return a stable integration map for local media-control tools."""

    return {
        "api": "zhiyun-light-control",
        "version": "0.1.0",
        "control_enabled": allow_control,
        "transport": {
            "active": transport,
            "ble_backend": ble_backend,
            "ble_profile": ble_profile,
            "ble_address": ble_address,
            "ble_name_contains": ble_name_contains,
        },
        "setup": {
            "preflight": {"method": "GET", "path": "/ready"},
            "integration": {"method": "GET", "path": "/integration"},
            "local_preflight": {
                "command": "zlight ready --transport usb --json",
                "integration_command": "zlight integration --transport usb --json",
                "ble_macos_command": (
                    "zlight ready --transport ble --ble-backend macos-app "
                    "--name-contains <name> --json"
                ),
                "requires_bridge": False,
            },
            "capabilities": {"method": "GET", "path": "/capabilities"},
            "diagnostics": {"method": "GET", "path": "/diagnostics"},
            "devices": {
                "method": "GET",
                "path": "/devices",
                "query": [
                    "include_ble",
                    "include_ble_status",
                    "ble_backend",
                    "timeout",
                    "name_contains",
                ],
            },
            "openapi": {"method": "GET", "path": "/openapi.json"},
            "ble_authorization": {
                "command": "zlight ble-helper --ensure --open-settings",
                "status_command": "zlight ble-helper --status --json",
                "devices_query": "/devices?include_ble_status=true",
            },
        },
        "http": {
            "read": [
                {"name": "status", "method": "GET", "path": "/status"},
                {"name": "state", "method": "GET", "path": "/state"},
                {"name": "history", "method": "GET", "path": "/history"},
            ],
            "control": [
                {"name": "brightness", "method": "POST", "path": "/brightness"},
                {"name": "cct", "method": "POST", "path": "/cct"},
                {"name": "sleep", "method": "POST", "path": "/sleep"},
                {"name": "rgb", "method": "POST", "path": "/rgb"},
                {"name": "hsi", "method": "POST", "path": "/hsi"},
                {"name": "scene", "method": "POST", "path": "/scene"},
                {"name": "transition", "method": "POST", "path": "/transition"},
                {"name": "preset", "method": "POST", "path": "/preset"},
                {"name": "sequence", "method": "POST", "path": "/sequence"},
                {"name": "cue", "method": "POST", "path": "/cue"},
            ],
            "bench": [
                {"name": "validate", "method": "POST", "path": "/validate"},
                {"name": "discover-usb", "method": "POST", "path": "/discover-usb"},
                {"name": "inspect-ble", "method": "POST", "path": "/inspect-ble"},
                {
                    "name": "test-ble-endpoints",
                    "method": "POST",
                    "path": "/test-ble-endpoints",
                },
                {"name": "frame", "method": "POST", "path": "/frame"},
            ],
        },
        "state": {
            "snapshot": {"method": "GET", "path": "/state"},
            "events": {
                "method": "GET",
                "path": "/events",
                "query": ["limit", "timeout", "initial"],
            },
            "history": {
                "method": "GET",
                "path": "/history",
                "query": ["after", "limit"],
            },
        },
        "osc": {
            "server_command": "zlight osc-serve --allow-control",
            "addresses": [
                "/zhiyun/probe",
                "/zhiyun/register",
                "/zhiyun/brightness",
                "/zhiyun/cct",
                "/zhiyun/sleep",
                "/zhiyun/rgb",
                "/zhiyun/hsi",
                "/zhiyun/scene",
                "/zhiyun/preset",
                "/zhiyun/cue",
            ],
            "alias_prefix": "/light",
            "result_address": "/zhiyun/result",
        },
        "dmx": {
            "artnet": {
                "server_command": "zlight artnet-serve --allow-control",
                "default_universe": 0,
            },
            "sacn": {
                "server_command": "zlight sacn-serve --allow-control",
                "default_universe": 1,
            },
            "default_channels": {
                "brightness": 1,
                "cct": 2,
                "sleep": None,
            },
        },
        "scene_fields": list(_SCENE_FIELD_ORDER),
        "presets": presets,
        "cues": cues,
        "control_guard": control_guard_response(),
        "request_templates": request_templates_response(),
        "evidence": {
            "statuses": [
                "acknowledged",
                "sent_no_response",
                "echoed_write",
                "response_without_matching_ack",
            ],
            "write_applied_rule": (
                "A control request is applied only when every CommandResult is "
                "acknowledged."
            ),
            "state_model": (
                "State endpoints report requested state plus ACK evidence, not a "
                "physical light measurement."
            ),
        },
    }


def integration_snapshot_response(
    *,
    manifest: Mapping[str, object],
    capabilities: Mapping[str, object],
    ready: Mapping[str, object],
    devices: Mapping[str, object],
) -> dict[str, object]:
    """Return a compact controller snapshot plus raw discovery payloads."""

    version = manifest.get("version")
    return {
        "api": _integration_text(
            manifest.get("api") or ready.get("api"),
            default="zhiyun-light-control",
        ),
        "version": str(version) if version is not None else None,
        "summary": _integration_summary(ready=ready, devices=devices),
        "payloads": {
            "manifest": dict(manifest),
            "capabilities": dict(capabilities),
            "ready": dict(ready),
            "devices": dict(devices),
        },
    }


def _integration_summary(
    *,
    ready: Mapping[str, object],
    devices: Mapping[str, object],
) -> dict[str, object]:
    bridge = _integration_mapping(ready, "bridge")
    if not bridge:
        bridge = _integration_mapping(ready, "transport")
    status = _integration_mapping(ready, "status")
    transport = bridge.get("transport") or bridge.get("active")
    return {
        "transport": str(transport) if transport is not None else None,
        "ble_backend": _integration_optional_text(bridge.get("ble_backend")),
        "ble_profile": _integration_optional_text(bridge.get("ble_profile")),
        "ble_address": _integration_optional_text(bridge.get("ble_address")),
        "ble_name_contains": _integration_optional_text(
            bridge.get("ble_name_contains")
        ),
        "control_enabled": ready.get("control_enabled") is True,
        "connection_confirmed": ready.get("connection_confirmed") is True,
        "ready_for": _integration_bool_map(ready, "ready_for"),
        "pending_action_ids": _integration_pending_action_ids(ready),
        "warnings": _integration_text_list(ready.get("warnings")),
        "selected_usb_port": _integration_selected_usb_port(devices),
        "usb_available": _integration_usb(devices).get("available") is True,
        "ble_authorization": _integration_ble_authorization(devices),
        "ble_state": _integration_ble_state(devices),
        "ble_blocker": _integration_ble_blocker(devices),
        "ble_scan_ok": _integration_ble_scan(devices).get("ok") is True,
        "ble_devices": _integration_ble_scan_devices(devices),
        "firmware": _integration_optional_text(status.get("firmware")),
        "generation": _integration_optional_text(status.get("generation")),
        "device_identifier": _integration_optional_text(
            status.get("device_identifier")
        ),
    }


def _integration_pending_action_ids(payload: Mapping[str, object]) -> list[str]:
    pending: list[str] = []
    seen: set[str] = set()

    def append(action_id: object) -> None:
        if action_id is None:
            return
        value = str(action_id)
        if value in seen:
            return
        seen.add(value)
        pending.append(value)

    requirements = payload.get("requirements")
    if isinstance(requirements, Mapping):
        for requirement in requirements.values():
            if not isinstance(requirement, Mapping):
                continue
            action_ids = requirement.get("pending_actions")
            if isinstance(action_ids, list):
                for action_id in action_ids:
                    append(action_id)
    if pending:
        return pending

    actions = payload.get("actions")
    if isinstance(actions, list):
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            if action.get("ready") is True:
                continue
            append(action.get("id"))
    return pending


def _integration_selected_usb_port(payload: Mapping[str, object]) -> str | None:
    port = _integration_usb(payload).get("selected_port")
    return str(port) if port is not None else None


def _integration_ble_authorization(payload: Mapping[str, object]) -> str | None:
    authorization = _integration_ble_status(payload).get("authorization")
    return str(authorization) if authorization is not None else None


def _integration_ble_state(payload: Mapping[str, object]) -> str | None:
    state = _integration_ble_status(payload).get("state")
    return str(state) if state is not None else None


def _integration_ble_blocker(payload: Mapping[str, object]) -> str | None:
    status_error = _integration_ble_status(payload).get("error")
    if status_error is not None:
        return str(status_error)
    scan_error = _integration_ble_scan(payload).get("error")
    if scan_error is not None:
        return str(scan_error)
    return None


def _integration_ble_scan_devices(
    payload: Mapping[str, object],
) -> list[dict[str, object]]:
    devices = _integration_ble_scan(payload).get("devices")
    if not isinstance(devices, list):
        return []
    return [
        _integration_string_key_dict(device)
        for device in devices
        if isinstance(device, Mapping)
    ]


def _integration_usb(payload: Mapping[str, object]) -> dict[str, object]:
    usb = _integration_devices(payload).get("usb")
    if not isinstance(usb, Mapping):
        return {}
    return _integration_string_key_dict(usb)


def _integration_ble(payload: Mapping[str, object]) -> dict[str, object]:
    ble = _integration_devices(payload).get("ble")
    if not isinstance(ble, Mapping):
        return {}
    return _integration_string_key_dict(ble)


def _integration_ble_status(payload: Mapping[str, object]) -> dict[str, object]:
    status = _integration_ble(payload).get("macos_status")
    if not isinstance(status, Mapping):
        return {}
    return _integration_string_key_dict(status)


def _integration_ble_scan(payload: Mapping[str, object]) -> dict[str, object]:
    scan = _integration_ble(payload).get("scan")
    if not isinstance(scan, Mapping):
        return {}
    return _integration_string_key_dict(scan)


def _integration_devices(payload: Mapping[str, object]) -> dict[str, object]:
    if "usb" in payload or "ble" in payload:
        return _integration_string_key_dict(payload)
    devices = payload.get("devices")
    if not isinstance(devices, Mapping):
        return {}
    return _integration_string_key_dict(devices)


def _integration_mapping(
    payload: Mapping[str, object],
    key: str,
) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        return {}
    return _integration_string_key_dict(value)


def _integration_bool_map(
    payload: Mapping[str, object],
    key: str,
) -> dict[str, bool]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        return {}
    return {
        str(item_key): item
        for item_key, item in value.items()
        if isinstance(item_key, str) and isinstance(item, bool)
    }


def _integration_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _integration_optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _integration_text(value: object, *, default: str) -> str:
    return str(value) if value is not None else default


def _integration_string_key_dict(payload: Mapping[object, object]) -> dict[str, object]:
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(key, str)
    }


def control_guard_response() -> dict[str, object]:
    return {
        "write_endpoints_require": [
            "start bridge with --allow-control",
            "check ready_for.control_requests before posting control requests",
            "treat responses as applied only when acknowledged evidence is true",
        ],
        "default_required_readiness": ["control_requests"],
        "strict_required_readiness": ["confirmed_control"],
        "enable_command": "zlight serve --allow-control",
        "validation_request": {
            "method": "POST",
            "path": "/validate",
            "body": {"allow_control": True},
            "ready_capability": "confirmed_control",
        },
        "python_client": {
            "default_guard": (
                "LightBridgeClient(base_url, require_ready_for_controls=True)"
            ),
            "strict_guard": (
                "LightBridgeClient(base_url, require_ready_for_controls=True, "
                "control_readiness=['confirmed_control'])"
            ),
        },
    }


def request_templates_response() -> dict[str, object]:
    control_mode = f"0x{DEFAULT_CONTROL_MODE:02x}"
    return {
        "read": {
            "status": {"method": "GET", "path": "/status"},
            "state": {"method": "GET", "path": "/state"},
            "history": {
                "method": "GET",
                "path": "/history",
                "query": {"limit": 10},
            },
            "events": {
                "method": "GET",
                "path": "/events",
                "query": {"limit": 1, "timeout": 30, "initial": True},
            },
        },
        "setup": {
            "ready": {"method": "GET", "path": "/ready"},
            "integration": {
                "method": "GET",
                "path": "/integration",
                "query": {"include_ble_status": True},
            },
            "devices_ble_status": {
                "method": "GET",
                "path": "/devices",
                "query": {"include_ble_status": True},
            },
            "validate_control": {
                "method": "POST",
                "path": "/validate",
                "body": {"allow_control": True},
            },
        },
        "control": {
            "brightness": {
                "method": "POST",
                "path": "/brightness",
                "required_readiness": ["control_requests"],
                "body": {"obj": 1, "value": 35, "control_mode": control_mode},
            },
            "cct": {
                "method": "POST",
                "path": "/cct",
                "required_readiness": ["control_requests"],
                "body": {"obj": 1, "kelvin": 5600, "control_mode": control_mode},
            },
            "sleep": {
                "method": "POST",
                "path": "/sleep",
                "required_readiness": ["control_requests"],
                "body": {"obj": 1, "value": 0, "control_mode": control_mode},
            },
            "rgb": {
                "method": "POST",
                "path": "/rgb",
                "required_readiness": ["control_requests"],
                "body": {
                    "obj": 1,
                    "red": 255,
                    "green": 255,
                    "blue": 255,
                    "control_mode": control_mode,
                },
            },
            "hsi": {
                "method": "POST",
                "path": "/hsi",
                "required_readiness": ["control_requests"],
                "body": {
                    "obj": 1,
                    "hue": 0,
                    "saturation": 0,
                    "intensity": 35,
                    "control_mode": control_mode,
                },
            },
            "scene": {
                "method": "POST",
                "path": "/scene",
                "required_readiness": ["control_requests"],
                "body": {
                    "obj": 1,
                    "sleep": 0,
                    "brightness": 35,
                    "kelvin": 5600,
                    "control_mode": control_mode,
                },
            },
            "transition": {
                "method": "POST",
                "path": "/transition",
                "required_readiness": ["control_requests"],
                "body": {
                    "from": {"brightness": 10},
                    "to": {"brightness": 60, "kelvin": 5600},
                    "steps": 8,
                    "duration": 2.0,
                    "easing": "ease-in-out",
                    "control_mode": control_mode,
                },
            },
            "preset": {
                "method": "POST",
                "path": "/preset",
                "required_readiness": ["control_requests"],
                "body": {
                    "name": "key",
                    "brightness": 45,
                    "control_mode": control_mode,
                },
            },
            "sequence": {
                "method": "POST",
                "path": "/sequence",
                "required_readiness": ["control_requests"],
                "body": {
                    "steps": [
                        {"scene": {"brightness": 10}},
                        {"preset": "key", "overrides": {"brightness": 45}},
                        {"to": {"brightness": 60}, "steps": 4, "duration": 1.0},
                    ],
                    "stop_on_unconfirmed": True,
                    "control_mode": control_mode,
                },
            },
            "cue": {
                "method": "POST",
                "path": "/cue",
                "required_readiness": ["control_requests"],
                "body": {
                    "name": "warm-key",
                    "stop_on_unconfirmed": True,
                    "control_mode": control_mode,
                },
            },
        },
    }


def capabilities_response(
    *,
    allow_control: bool,
    presets: list[str],
    cues: list[str],
) -> dict[str, object]:
    return {
        "api": "zhiyun-light-control",
        "control_enabled": allow_control,
        "scene_fields": list(_SCENE_FIELD_ORDER),
        "presets": presets,
        "cues": cues,
        "control_guard": control_guard_response(),
        "request_templates": request_templates_response(),
        "evidence_statuses": [
            "acknowledged",
            "sent_no_response",
            "echoed_write",
            "response_without_matching_ack",
        ],
        "confirmation": {
            "read_primitives": (
                "ACK-backed status/probe results confirm command transport."
            ),
            "write_primitives": (
                "Control writes are considered applied only when every returned "
                "CommandResult is acknowledged."
            ),
            "state_applied": (
                "GET /state applied=true means the last scene request had only "
                "acknowledged command results."
            ),
        },
        "primitives": [
            {
                "name": "probe",
                "method": "GET",
                "path": "/probe",
                "requires_control": False,
                "confirmation": "acknowledged identity/status frames",
            },
            {
                "name": "status",
                "method": "GET",
                "path": "/status",
                "requires_control": False,
                "confirmation": "acknowledged read-only status frames",
            },
            {
                "name": "validate",
                "method": "POST",
                "path": "/validate",
                "requires_control": "allow_control request field",
                "confirmation": "per-check validation report",
            },
            {
                "name": "plan",
                "method": "POST",
                "path": "/plan",
                "requires_control": False,
                "fields": [
                    "action",
                    "scene",
                    "preset",
                    "name",
                    "from",
                    "to",
                    "steps",
                    "overrides",
                    "control_mode",
                ],
                "confirmation": "dry-run scene resolution without hardware writes",
            },
            {
                "name": "discover-usb",
                "method": "POST",
                "path": "/discover-usb",
                "requires_control": "allow_control request field",
                "fields": [
                    "allow_control",
                    "object_ids",
                    "first_words",
                    "control_object_ids",
                    "control_first_words",
                    "register_device_ids",
                    "register_group_ids",
                    "control_kinds",
                    "control_modes",
                    "post_register_reads",
                    "timeout",
                    "brightness",
                    "kelvin",
                    "sleep",
                ],
                "confirmation": "per-attempt ACK/timeout/evidence report",
            },
            {
                "name": "inspect-ble",
                "method": "POST",
                "path": "/inspect-ble",
                "requires_control": False,
                "fields": [
                    "backend",
                    "address",
                    "name_contains",
                    "timeout",
                    "python",
                ],
                "confirmation": "GATT service and characteristic enumeration",
                "ble_backends": list(BLE_BACKENDS),
            },
            {
                "name": "test-ble-endpoints",
                "method": "POST",
                "path": "/test-ble-endpoints",
                "requires_control": False,
                "fields": [
                    "backend",
                    "address",
                    "name_contains",
                    "timeout",
                    "python",
                    "max_candidates",
                ],
                "confirmation": (
                    "read-only DEVICE_INFO probe against suggested BLE endpoints"
                ),
                "ble_backends": list(BLE_BACKENDS),
            },
            {
                "name": "ready",
                "method": "GET",
                "path": "/ready",
                "requires_control": False,
                "confirmation": (
                    "single preflight response with readiness booleans and actions"
                ),
            },
            {
                "name": "ready-cli",
                "method": "CLI",
                "command": "zlight ready --transport usb --json",
                "requires_control": False,
                "confirmation": (
                    "local read-only preflight with the same readiness model as "
                    "HTTP /ready"
                ),
            },
            {
                "name": "integration-cli",
                "method": "CLI",
                "command": "zlight integration --transport usb --json",
                "requires_control": False,
                "confirmation": (
                    "local controller snapshot with manifest, capabilities, "
                    "readiness, and device discovery payloads"
                ),
            },
            {
                "name": "devices",
                "method": "GET",
                "path": "/devices",
                "requires_control": False,
                "fields": [
                    "include_ble",
                    "include_ble_status",
                    "ble_backend",
                    "timeout",
                    "name_contains",
                ],
                "confirmation": (
                    "USB port list plus optional BLE scan and authorization "
                    "diagnostics"
                ),
                "ble_backends": list(BLE_BACKENDS),
            },
            {
                "name": "events",
                "method": "GET",
                "path": "/events",
                "requires_control": False,
                "fields": ["limit", "timeout", "initial"],
                "confirmation": "state-event stream mirrors requested bridge state",
            },
            {
                "name": "history",
                "method": "GET",
                "path": "/history",
                "requires_control": False,
                "fields": ["after", "limit"],
                "confirmation": "bounded recent state-event history",
            },
            {
                "name": "cues",
                "method": "GET",
                "path": "/cues",
                "requires_control": False,
                "confirmation": "loaded named cue definitions",
            },
            {
                "name": "register",
                "method": "POST",
                "path": "/register",
                "requires_control": True,
                "fields": ["device_id"],
                "confirmation": "CommandResult.acknowledged",
            },
            {
                "name": "brightness",
                "method": "POST",
                "path": "/brightness",
                "requires_control": True,
                "fields": ["obj", "value", "control_mode"],
                "confirmation": "CommandResult.acknowledged",
            },
            {
                "name": "cct",
                "method": "POST",
                "path": "/cct",
                "requires_control": True,
                "fields": ["obj", "kelvin", "control_mode"],
                "confirmation": "CommandResult.acknowledged",
            },
            {
                "name": "sleep",
                "method": "POST",
                "path": "/sleep",
                "requires_control": True,
                "fields": ["obj", "value", "control_mode"],
                "confirmation": "CommandResult.acknowledged",
            },
            {
                "name": "rgb",
                "method": "POST",
                "path": "/rgb",
                "requires_control": True,
                "fields": ["obj", "red", "green", "blue", "control_mode"],
                "confirmation": "CommandResult.acknowledged",
            },
            {
                "name": "hsi",
                "method": "POST",
                "path": "/hsi",
                "requires_control": True,
                "fields": [
                    "obj",
                    "hue",
                    "saturation",
                    "intensity",
                    "control_mode",
                ],
                "confirmation": "CommandResult.acknowledged",
            },
            {
                "name": "frame",
                "method": "POST",
                "path": "/frame",
                "requires_control": True,
                "fields": ["first_word", "command", "payload_hex", "timeout"],
                "confirmation": "CommandResult.acknowledged",
            },
            {
                "name": "scene",
                "method": "POST",
                "path": "/scene",
                "requires_control": True,
                "fields": list(_SCENE_FIELD_ORDER) + ["control_mode"],
                "confirmation": "all results acknowledged",
            },
            {
                "name": "transition",
                "method": "POST",
                "path": "/transition",
                "requires_control": True,
                "fields": ["from", "to", "steps", "duration", "easing"],
                "confirmation": "all batch results acknowledged",
            },
            {
                "name": "preset",
                "method": "POST",
                "path": "/preset",
                "requires_control": True,
                "fields": ["name", *list(_SCENE_FIELD_ORDER), "control_mode"],
                "confirmation": "all results acknowledged",
            },
            {
                "name": "sequence",
                "method": "POST",
                "path": "/sequence",
                "requires_control": True,
                "fields": ["steps", "stop_on_unconfirmed", "control_mode"],
                "confirmation": "all step results acknowledged",
            },
            {
                "name": "cue",
                "method": "POST",
                "path": "/cue",
                "requires_control": True,
                "fields": ["name", "cue", "obj", "stop_on_unconfirmed", "control_mode"],
                "confirmation": "all named cue step results acknowledged",
            },
        ],
    }


def diagnostics_response(
    *,
    allow_control: bool,
    transport: str,
    ble_backend: str | None,
    ble_profile: str | None,
    ble_address: str | None,
    ble_name_contains: str | None,
    connection_confirmed: bool,
    status: dict[str, object],
    error: str | None,
) -> dict[str, object]:
    next_steps = _diagnostic_next_steps(
        allow_control=allow_control,
        transport=transport,
        connection_confirmed=connection_confirmed,
        error=error,
    )
    return {
        "api": "zhiyun-light-control",
        "ok": connection_confirmed,
        "connection_confirmed": connection_confirmed,
        "control_enabled": allow_control,
        "bridge": {
            "transport": transport,
            "ble_backend": ble_backend,
            "ble_profile": ble_profile,
            "ble_address": ble_address,
            "ble_name_contains": ble_name_contains,
        },
        "status": status,
        "next_steps": next_steps,
    }


def readiness_response(
    *,
    allow_control: bool,
    transport: str,
    ble_backend: str | None,
    ble_profile: str | None,
    ble_address: str | None,
    ble_name_contains: str | None,
    connection_confirmed: bool,
    status: dict[str, object],
    error: str | None,
    devices: dict[str, object],
    state_version: int,
    state: dict[str, object] | None,
) -> dict[str, object]:
    confirmed_control = bool(state and state.get("applied") is True)
    control_requests = connection_confirmed and allow_control
    next_steps = _diagnostic_next_steps(
        allow_control=allow_control,
        transport=transport,
        connection_confirmed=connection_confirmed,
        error=error,
    )
    warnings = _readiness_warnings(
        allow_control=allow_control,
        transport=transport,
        connection_confirmed=connection_confirmed,
        error=error,
        state=state,
    )
    actions = _readiness_actions(
        allow_control=allow_control,
        transport=transport,
        connection_confirmed=connection_confirmed,
        confirmed_control=confirmed_control,
        error=error,
    )
    ready_for = {
        "read_status": connection_confirmed,
        "control_requests": control_requests,
        "confirmed_control": confirmed_control,
        "state_events": True,
        "device_discovery": True,
    }
    return {
        "api": "zhiyun-light-control",
        "ok": connection_confirmed,
        "connection_confirmed": connection_confirmed,
        "control_enabled": allow_control,
        "ready_for": ready_for,
        "requirements": _readiness_requirements(ready_for, actions),
        "bridge": {
            "transport": transport,
            "ble_backend": ble_backend,
            "ble_profile": ble_profile,
            "ble_address": ble_address,
            "ble_name_contains": ble_name_contains,
        },
        "status": status,
        "devices": devices,
        "state": {
            "version": state_version,
            "snapshot": {"scene": None} if state is None else state,
        },
        "write_confirmation": {
            "required": True,
            "last_control_confirmed": confirmed_control,
            "evidence_field": "CommandResult.acknowledged",
        },
        "warnings": warnings,
        "next_steps": next_steps,
        "actions": actions,
    }


def _readiness_warnings(
    *,
    allow_control: bool,
    transport: str,
    connection_confirmed: bool,
    error: str | None,
    state: dict[str, object] | None,
) -> list[str]:
    warnings: list[str] = []
    error_text = error.lower() if error else ""
    if transport == "ble" and "unauthorized" in error_text:
        warnings.append("macOS Bluetooth authorization is blocking BLE access.")
    elif not connection_confirmed:
        warnings.append("The configured transport did not return ACK-backed status.")
    if connection_confirmed and not allow_control:
        warnings.append("Write endpoints are disabled until --allow-control is set.")
    if connection_confirmed and allow_control and not (
        state and state.get("applied") is True
    ):
        warnings.append("No ACK-confirmed control request is recorded yet.")
    if state and state.get("applied") is False:
        reason = state.get("reason")
        if reason:
            warnings.append(f"Last control request was not confirmed: {reason}.")
        else:
            warnings.append("Last control request was not confirmed.")
    return warnings


def _readiness_actions(
    *,
    allow_control: bool,
    transport: str,
    connection_confirmed: bool,
    confirmed_control: bool,
    error: str | None,
) -> list[dict[str, object]]:
    actions = [
        {
            "id": "read-status",
            "label": "Read ACK-backed transport status",
            "ready": connection_confirmed,
            "category": "transport",
            "method": "GET",
            "path": "/status",
            "required_for": ["read_status"],
        },
        {
            "id": "discover-devices",
            "label": "Discover local USB/BLE transport candidates",
            "ready": True,
            "category": "discovery",
            "method": "GET",
            "path": "/devices",
            "required_for": ["device_discovery"],
        },
        {
            "id": "state-events",
            "label": "Subscribe to requested-state events",
            "ready": True,
            "category": "state",
            "method": "GET",
            "path": "/events",
            "required_for": ["state_events"],
        },
        {
            "id": "enable-control",
            "label": "Start the bridge with write endpoints enabled",
            "ready": allow_control,
            "category": "control",
            "command": "zlight serve --allow-control",
            "required_for": ["control_requests"],
        },
        {
            "id": "confirm-control",
            "label": "Prove write primitives with ACK-backed validation",
            "ready": confirmed_control,
            "category": "control",
            "method": "POST",
            "path": "/validate",
            "body": {"allow_control": True},
            "requires_control": True,
            "blocked_by": [] if allow_control else ["enable-control"],
            "required_for": ["confirmed_control"],
        },
    ]
    error_text = error.lower() if error else ""
    if transport == "ble" and "unauthorized" in error_text:
        actions.insert(
            0,
            {
                "id": "authorize-bluetooth",
                "label": "Allow the macOS BLE helper to use Bluetooth",
                "ready": False,
                "category": "transport",
                "command": "zlight ble-helper --ensure --open-settings",
                "required_for": ["read_status", "control_requests"],
            },
        )
    elif not connection_confirmed:
        actions.insert(
            0,
            {
                "id": "check-transport",
                "label": "Check cable, power, selected transport, and device id",
                "ready": False,
                "category": "transport",
                "required_for": ["read_status", "control_requests"],
            },
        )
    return actions


def _readiness_requirements(
    ready_for: dict[str, bool],
    actions: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    requirements: dict[str, dict[str, object]] = {}
    action_by_id = _readiness_actions_by_id(actions)
    for capability, ready in ready_for.items():
        matching_actions = _readiness_actions_for(actions, capability)
        requirements[capability] = {
            "ready": ready,
            "actions": [
                action["id"]
                for action in matching_actions
                if isinstance(action.get("id"), str)
            ],
            "pending_actions": _readiness_pending_action_ids(
                matching_actions,
                action_by_id,
            ),
        }
    return requirements


def _readiness_actions_by_id(
    actions: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    action_by_id: dict[str, dict[str, object]] = {}
    for action in actions:
        action_id = action.get("id")
        if isinstance(action_id, str):
            action_by_id[action_id] = action
    return action_by_id


def _readiness_actions_for(
    actions: list[dict[str, object]],
    capability: str,
) -> list[dict[str, object]]:
    matching: list[dict[str, object]] = []
    for action in actions:
        required_for = action.get("required_for")
        if not isinstance(required_for, list):
            continue
        if capability in required_for:
            matching.append(action)
    return matching


def _readiness_pending_action_ids(
    actions: list[dict[str, object]],
    action_by_id: dict[str, dict[str, object]],
) -> list[str]:
    pending: list[str] = []
    seen: set[str] = set()

    def append_pending(action_id: str) -> None:
        if action_id in seen:
            return
        action = action_by_id.get(action_id)
        if action is not None:
            blocked_by = action.get("blocked_by")
            if isinstance(blocked_by, list):
                for blocker in blocked_by:
                    if isinstance(blocker, str):
                        append_pending(blocker)
            if action.get("ready") is True:
                return
        seen.add(action_id)
        pending.append(action_id)

    for action in actions:
        action_id = action.get("id")
        if isinstance(action_id, str):
            append_pending(action_id)
    return pending


def _diagnostic_next_steps(
    *,
    allow_control: bool,
    transport: str,
    connection_confirmed: bool,
    error: str | None,
) -> list[str]:
    steps: list[str] = []
    error_text = error.lower() if error else ""
    if transport == "ble" and "unauthorized" in error_text:
        steps.append(
            "Allow ZhiyunBleScan in macOS Privacy & Security > Bluetooth, then retry."
        )
    elif not connection_confirmed:
        steps.append(
            "Check the selected transport, cable/power state, and device identifier."
        )
    if connection_confirmed and not allow_control:
        steps.append("Start the bridge with --allow-control before write endpoints.")
    if connection_confirmed and allow_control:
        steps.append(
            "Use POST /validate with allow_control=true to prove write primitives."
        )
    return steps


def _sequence_preset_overrides(step: dict[str, object]) -> dict[str, object]:
    overrides: dict[str, object] = {}
    explicit = step.get("overrides")
    if explicit is not None:
        if not isinstance(explicit, dict):
            raise ValueError("sequence preset overrides must be an object")
        overrides.update({key: value for key, value in explicit.items()})
    overrides.update(
        {
            key: value
            for key, value in step.items()
            if key in _SCENE_FIELDS and value is not None
        }
    )
    return overrides


def _plan_action(body: dict[str, object]) -> str:
    value = body.get("action")
    if value is not None:
        action = str(value).strip().lower()
        if action not in {"scene", "preset", "transition", "sequence"}:
            raise ValueError(
                "plan action must be scene, preset, transition, or sequence"
            )
        return action
    if "to" in body:
        return "transition"
    if isinstance(body.get("steps"), list):
        return "sequence"
    if "preset" in body or "name" in body:
        return "preset"
    return "scene"


def _sequence_transition_start(
    handler: LightRequestHandler,
    step: dict[str, object],
    current_scene: Scene | None,
    *,
    obj: int,
) -> Scene:
    if "from" in step:
        start_data = step["from"]
        if not isinstance(start_data, dict):
            raise ValueError("sequence transition 'from' must be an object")
        return _scene_from_body(start_data, obj=obj)
    if current_scene is not None and current_scene.obj == obj:
        return current_scene
    return handler._transition_start(step, obj=obj)


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    light_port: str | None = None,
    allow_control: bool = False,
    light_factory: Callable[[], object] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    cue_library: CueLibrary | None = None,
    state_tracker: SceneStateTracker | None = None,
    cors_origin: str | None = "*",
    transport: str = "usb",
    ble_backend: str | None = None,
    ble_profile: str | None = None,
    ble_address: str | None = None,
    ble_name_contains: str | None = None,
    ble_python: str | None = None,
) -> None:
    httpd = LightHttpServer(
        (host, port),
        port=light_port,
        allow_control=allow_control,
        light_factory=light_factory,
        preset_library=preset_library,
        cue_library=cue_library,
        state_tracker=state_tracker,
        cors_origin=cors_origin,
        transport=transport,
        ble_backend=ble_backend,
        ble_profile=ble_profile,
        ble_address=ble_address,
        ble_name_contains=ble_name_contains,
        ble_python=ble_python,
    )
    try:
        httpd.serve_forever()
    finally:
        close_light_factory(httpd.light_factory)


def _optional_int(body: dict[str, object], key: str) -> int | None:
    return int(body[key]) if key in body and body[key] is not None else None


def _optional_float(body: dict[str, object], key: str) -> float | None:
    return float(body[key]) if key in body and body[key] is not None else None


def _optional_text(body: dict[str, object], key: str) -> str | None:
    return str(body[key]) if key in body and body[key] is not None else None


def _body_bool(body: dict[str, object], key: str, default: bool = False) -> bool:
    value = body.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _body_int_tuple(
    body: dict[str, object],
    key: str,
    *,
    default: tuple[int, ...],
) -> tuple[int, ...]:
    if key not in body or body[key] is None:
        return default
    return _int_tuple_value(body[key], key=key)


def _body_optional_int_tuple(
    body: dict[str, object],
    key: str,
) -> tuple[int, ...] | None:
    if key not in body or body[key] is None:
        return None
    return _int_tuple_value(body[key], key=key)


def _body_text_tuple(
    body: dict[str, object],
    key: str,
    *,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    if key not in body or body[key] is None:
        return default
    value = body[key]
    if isinstance(value, str):
        if value.strip().lower() == "none":
            return ()
        items = tuple(part.strip() for part in value.split(",") if part.strip())
    elif isinstance(value, list | tuple):
        items = tuple(str(item).strip() for item in value if str(item).strip())
    else:
        raise ValueError(f"{key} must be a list or comma-separated string")
    return items


def _int_tuple_value(value: object, *, key: str) -> tuple[int, ...]:
    if isinstance(value, int):
        return (value,)
    if isinstance(value, str):
        items = tuple(part.strip() for part in value.split(",") if part.strip())
        if not items:
            raise ValueError(f"{key} must contain at least one value")
        return tuple(int(item, 0) for item in items)
    if isinstance(value, list | tuple):
        if not value:
            raise ValueError(f"{key} must contain at least one value")
        return tuple(_int_item(item, key=key) for item in value)
    raise ValueError(f"{key} must be an integer, list, or comma-separated string")


def _int_item(value: object, *, key: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError(f"{key} values must be integers or integer strings")


def _query_bool(
    params: dict[str, list[str]],
    key: str,
    *,
    default: bool = False,
) -> bool:
    values = params.get(key)
    if not values:
        return default
    return values[-1].strip().lower() in {"1", "true", "yes", "on"}


def _query_int(
    params: dict[str, list[str]],
    key: str,
    *,
    default: int,
) -> int:
    values = params.get(key)
    return int(values[-1], 0) if values else default


def _query_float(
    params: dict[str, list[str]],
    key: str,
    *,
    default: float,
) -> float:
    values = params.get(key)
    return float(values[-1]) if values else default


def _query_text(
    params: dict[str, list[str]],
    key: str,
    *,
    default: str | None = None,
) -> str | None:
    values = params.get(key)
    if not values:
        return default
    value = values[-1].strip()
    return value if value else default


def _body_int(
    body: dict[str, object],
    key: str,
    default: int | None = None,
) -> int:
    value = body.get(key, default)
    if value is None:
        raise ValueError(f"{key} is required")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError(f"{key} must be an integer or integer string")


def _control_mode(body: dict[str, object]) -> int:
    return _body_int(body, "control_mode", DEFAULT_CONTROL_MODE)


def _payload_hex(body: dict[str, object]) -> bytes:
    payload = body.get("payload_hex", "")
    if not isinstance(payload, str):
        raise ValueError("payload_hex must be a string")
    return bytes.fromhex(payload)


_SCENE_FIELD_ORDER = tuple(field.name for field in fields(Scene))
_SCENE_FIELDS = set(_SCENE_FIELD_ORDER)


def _scene_fields_from_body(body: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in body.items() if key in _SCENE_FIELDS}


def _scene_from_body(body: dict[str, object], *, obj: int = 1) -> Scene:
    return Scene(
        obj=int(body.get("obj", obj)),
        brightness=_optional_float(body, "brightness"),
        kelvin=_optional_int(body, "kelvin"),
        sleep=_optional_int(body, "sleep"),
        red=_optional_int(body, "red"),
        green=_optional_int(body, "green"),
        blue=_optional_int(body, "blue"),
        hue=_optional_float(body, "hue"),
        saturation=_optional_float(body, "saturation"),
        intensity=_optional_int(body, "intensity"),
    )
