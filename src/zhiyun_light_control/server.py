"""Small stdlib HTTP bridge for local media-production integrations."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import fields
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .bridge import close_light_factory
from .client import ZhiyunLight
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
        state_tracker: SceneStateTracker | None = None,
        cors_origin: str | None = "*",
    ):
        super().__init__(server_address, LightRequestHandler)
        self.light_port = port
        self.allow_control = allow_control
        self.light_factory = light_factory or (lambda: ZhiyunLight.usb(port=port))
        self.preset_library = preset_library
        self.state_tracker = state_tracker or SceneStateTracker()
        self.cors_origin = cors_origin


class LightRequestHandler(BaseHTTPRequestHandler):
    server: LightHttpServer

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._json({"ok": True})
            return
        if path == "/commands":
            self._json(
                {
                    "get": [
                        "/health",
                        "/openapi.json",
                        "/probe",
                        "/status",
                        "/validate",
                        "/commands",
                        "/presets",
                        "/state",
                    ],
                    "post": [
                        "/validate",
                        "/register",
                        "/brightness",
                        "/cct",
                        "/sleep",
                        "/rgb",
                        "/hsi",
                        "/frame",
                        "/scene",
                        "/transition",
                        "/preset",
                    ],
                    "control_enabled": self.server.allow_control,
                    "presets": self.server.preset_library.names()
                    if self.server.preset_library
                    else [],
                }
            )
            return
        if path == "/openapi.json":
            self._json(openapi_schema())
            return
        if path == "/presets":
            library = self.server.preset_library
            self._json(library.to_dict() if library else {"scenes": {}})
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
            if path == "/validate":
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
        raise ValueError("unknown endpoint")

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
            "/openapi.json": {
                "get": _operation("Machine-readable bridge schema", "OpenApiSchema")
            },
            "/commands": {
                "get": _operation("List supported bridge endpoints", "Commands")
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
            "/presets": {"get": _operation("List loaded scene presets", "Presets")},
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
        "Commands": {
            "type": "object",
            "properties": {
                "get": {"type": "array", "items": {"type": "string"}},
                "post": {"type": "array", "items": {"type": "string"}},
                "control_enabled": {"type": "boolean"},
                "presets": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["get", "post", "control_enabled", "presets"],
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
        "Presets": {"type": "object", "additionalProperties": True},
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
    }


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    light_port: str | None = None,
    allow_control: bool = False,
    light_factory: Callable[[], object] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    state_tracker: SceneStateTracker | None = None,
    cors_origin: str | None = "*",
) -> None:
    httpd = LightHttpServer(
        (host, port),
        port=light_port,
        allow_control=allow_control,
        light_factory=light_factory,
        preset_library=preset_library,
        state_tracker=state_tracker,
        cors_origin=cors_origin,
    )
    try:
        httpd.serve_forever()
    finally:
        close_light_factory(httpd.light_factory)


def _optional_int(body: dict[str, object], key: str) -> int | None:
    return int(body[key]) if key in body and body[key] is not None else None


def _optional_float(body: dict[str, object], key: str) -> float | None:
    return float(body[key]) if key in body and body[key] is not None else None


def _body_bool(body: dict[str, object], key: str, default: bool = False) -> bool:
    value = body.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


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


_SCENE_FIELDS = {field.name for field in fields(Scene)}


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
