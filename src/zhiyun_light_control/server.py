"""Small stdlib HTTP bridge for local media-production integrations."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from typing import Any

from .client import ZhiyunLight
from .models import Scene
from .protocol import (
    RuntimeCommand,
    brightness_payload,
    cct_payload,
    hsi_payload,
    register_payload,
    rgb_payload,
    sleep_payload,
)


class LightHttpServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        port: str | None = None,
        allow_control: bool = False,
        light_factory: Any | None = None,
    ):
        super().__init__(server_address, LightRequestHandler)
        self.light_port = port
        self.allow_control = allow_control
        self.light_factory = light_factory or (lambda: ZhiyunLight.usb(port=port))


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
                    "get": ["/health", "/probe", "/commands"],
                    "post": ["/register", "/brightness", "/cct", "/sleep", "/rgb", "/hsi", "/scene"],
                    "control_enabled": self.server.allow_control,
                }
            )
            return
        if path == "/probe":
            with self.server.light_factory() as light:
                self._json(light.probe().to_dict())
            return
        self._json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_common_headers(0)
        self.end_headers()

    def do_POST(self) -> None:
        if not self.server.allow_control:
            self._json(
                {"error": "control endpoints require --allow-control"},
                status=HTTPStatus.FORBIDDEN,
            )
            return
        try:
            body = self._read_json()
            path = urlparse(self.path).path
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
                return result.to_dict()
            if path == "/cct":
                result = light.exchange_runtime(
                    RuntimeCommand.CCT,
                    cct_payload(obj, int(body["kelvin"])),
                )
                return result.to_dict()
            if path == "/sleep":
                result = light.exchange_runtime(
                    RuntimeCommand.SLEEP,
                    sleep_payload(obj, int(body["value"])),
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
                return result.to_dict()
            if path == "/scene":
                scene = Scene(
                    obj=obj,
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
                return {
                    "scene": scene.to_dict(),
                    "results": [result.to_dict() for result in light.apply_scene(scene)],
                }
        raise ValueError("unknown endpoint")

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
) -> None:
    httpd = LightHttpServer((host, port), port=light_port, allow_control=allow_control)
    httpd.serve_forever()


def _optional_int(body: dict[str, Any], key: str) -> int | None:
    return int(body[key]) if key in body and body[key] is not None else None


def _optional_float(body: dict[str, Any], key: str) -> float | None:
    return float(body[key]) if key in body and body[key] is not None else None
