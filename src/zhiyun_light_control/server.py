"""Small stdlib HTTP bridge for local media-production integrations."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .client import ZhiyunLight


class LightHttpServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        port: str | None = None,
        allow_control: bool = False,
    ):
        super().__init__(server_address, LightRequestHandler)
        self.light_port = port
        self.allow_control = allow_control


class LightRequestHandler(BaseHTTPRequestHandler):
    server: LightHttpServer

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json({"ok": True})
            return
        if self.path == "/probe":
            with ZhiyunLight.usb(port=self.server.light_port) as light:
                self._json(light.probe().to_dict())
            return
        self._json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self.server.allow_control:
            self._json(
                {"error": "control endpoints require --allow-control"},
                status=HTTPStatus.FORBIDDEN,
            )
            return
        try:
            body = self._read_json()
            result = self._handle_control(body)
        except Exception as exc:  # pragma: no cover - keeps HTTP errors useful.
            self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return
        self._json(result)

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _handle_control(self, body: dict[str, Any]) -> dict[str, Any]:
        obj = int(body.get("obj", 1))
        with ZhiyunLight.usb(port=self.server.light_port) as light:
            if self.path == "/register":
                frame = light.register(device_id=int(body.get("device_id", 0)))
            elif self.path == "/brightness":
                frame = light.set_brightness(obj=obj, value=float(body["value"]))
            elif self.path == "/cct":
                frame = light.set_cct(obj=obj, kelvin=int(body["kelvin"]))
            elif self.path == "/sleep":
                frame = light.set_sleep(obj=obj, value=int(body["value"]))
            elif self.path == "/rgb":
                frame = light.set_rgb(
                    obj=obj,
                    red=int(body["red"]),
                    green=int(body["green"]),
                    blue=int(body["blue"]),
                )
            elif self.path == "/hsi":
                frame = light.set_hsi(
                    obj=obj,
                    hue=float(body["hue"]),
                    saturation=float(body["saturation"]),
                    intensity=int(body["intensity"]),
                )
            else:
                raise ValueError("unknown endpoint")
        return {"ack": frame.to_dict() if frame else None}

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    light_port: str | None = None,
    allow_control: bool = False,
) -> None:
    httpd = LightHttpServer((host, port), port=light_port, allow_control=allow_control)
    httpd.serve_forever()
