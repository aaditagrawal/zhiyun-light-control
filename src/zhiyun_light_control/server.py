"""Small stdlib HTTP bridge for local media-production integrations."""

from __future__ import annotations

import json
from dataclasses import fields
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from typing import Any

from .bridge import close_light_factory
from .client import ZhiyunLight
from .models import Scene
from .presets import ScenePresetLibrary, merge_scene, scene_from_optional_mapping
from .protocol import (
    RuntimeCommand,
    brightness_payload,
    cct_payload,
    hsi_payload,
    register_payload,
    rgb_payload,
    sleep_payload,
)
from .state import SceneStateTracker
from .validation import validate_sync_light


class LightHttpServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        port: str | None = None,
        allow_control: bool = False,
        light_factory: Any | None = None,
        preset_library: ScenePresetLibrary | None = None,
        state_tracker: SceneStateTracker | None = None,
    ):
        super().__init__(server_address, LightRequestHandler)
        self.light_port = port
        self.allow_control = allow_control
        self.light_factory = light_factory or (lambda: ZhiyunLight.usb(port=port))
        self.preset_library = preset_library
        self.state_tracker = state_tracker or SceneStateTracker()


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
                        "/probe",
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

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _handle_control(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        obj = int(body.get("obj", 1))
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
                    brightness_payload(obj, float(body["value"])),
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
                    cct_payload(obj, int(body["kelvin"])),
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
                    sleep_payload(obj, int(body["value"])),
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
            if path == "/scene":
                scene = _scene_from_body(body, obj=obj)
                results = light.apply_scene(scene)
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
                    if key != "name" and value is not None
                }
                scene = merge_scene(
                    library.get(name),
                    scene_from_optional_mapping(overrides, obj=obj),
                    override_obj="obj" in body,
                )
                results = light.apply_scene(scene)
                self._record_scene(scene, action="preset", results=results)
                return {
                    "preset": name,
                    "scene": scene.to_dict(),
                    "results": [result.to_dict() for result in results],
                }
        raise ValueError("unknown endpoint")

    def _handle_validate(self, body: dict[str, Any]) -> dict[str, Any]:
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
            )
        return report.to_dict()

    def _transition_start(self, body: dict[str, Any], *, obj: int) -> Scene:
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
        self.server.state_tracker.record(
            scene,
            source="http",
            action=action,
            applied=True,
            results=results,
        )

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self._send_common_headers(len(data))
        self.end_headers()
        self.wfile.write(data)

    def _send_common_headers(self, content_length: int) -> None:
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(content_length))
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
        self.send_header("access-control-allow-headers", "content-type")


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    light_port: str | None = None,
    allow_control: bool = False,
    light_factory: Any | None = None,
    preset_library: ScenePresetLibrary | None = None,
    state_tracker: SceneStateTracker | None = None,
) -> None:
    httpd = LightHttpServer(
        (host, port),
        port=light_port,
        allow_control=allow_control,
        light_factory=light_factory,
        preset_library=preset_library,
        state_tracker=state_tracker,
    )
    try:
        httpd.serve_forever()
    finally:
        close_light_factory(httpd.light_factory)


def _optional_int(body: dict[str, Any], key: str) -> int | None:
    return int(body[key]) if key in body and body[key] is not None else None


def _optional_float(body: dict[str, Any], key: str) -> float | None:
    return float(body[key]) if key in body and body[key] is not None else None


def _body_bool(body: dict[str, Any], key: str, default: bool = False) -> bool:
    value = body.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


_SCENE_FIELDS = {field.name for field in fields(Scene)}


def _scene_fields_from_body(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key in _SCENE_FIELDS}


def _scene_from_body(body: dict[str, Any], *, obj: int = 1) -> Scene:
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
