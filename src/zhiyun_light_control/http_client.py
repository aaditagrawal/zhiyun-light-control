"""Small stdlib client for the local HTTP bridge."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .models import Scene


class LightBridgeError(RuntimeError):
    def __init__(self, status: int, payload: object):
        super().__init__(f"bridge request failed with HTTP {status}: {payload}")
        self.status = status
        self.payload = payload


class LightBridgeClient:
    """Convenience wrapper for the local JSON bridge API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8765",
        *,
        timeout: float = 3.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, object]:
        return self._get("/health")

    def commands(self) -> dict[str, object]:
        return self._get("/commands")

    def capabilities(self) -> dict[str, object]:
        return self._get("/capabilities")

    def diagnostics(self) -> dict[str, object]:
        return self._get("/diagnostics")

    def openapi(self) -> dict[str, object]:
        return self._get("/openapi.json")

    def probe(self) -> dict[str, object]:
        return self._get("/probe")

    def status(self) -> dict[str, object]:
        return self._get("/status")

    def state(self) -> dict[str, object]:
        return self._get("/state")

    def presets(self) -> dict[str, object]:
        return self._get("/presets")

    def validate(
        self,
        *,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        values: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        payload = {
            "allow_control": allow_control,
            "include_object_reads": include_object_reads,
            "include_color": include_color,
        }
        if values is not None:
            payload.update(values)
        return self._post("/validate", payload)

    def register(self, *, device_id: int = 0) -> dict[str, object]:
        return self._post("/register", {"device_id": device_id})

    def set_brightness(
        self,
        value: float,
        *,
        obj: int = 1,
        control_mode: int | None = None,
    ) -> dict[str, object]:
        return self._post(
            "/brightness",
            _with_control_mode({"obj": obj, "value": value}, control_mode),
        )

    def set_cct(
        self,
        kelvin: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
    ) -> dict[str, object]:
        return self._post(
            "/cct",
            _with_control_mode({"obj": obj, "kelvin": kelvin}, control_mode),
        )

    def set_sleep(
        self,
        value: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
    ) -> dict[str, object]:
        return self._post(
            "/sleep",
            _with_control_mode({"obj": obj, "value": value}, control_mode),
        )

    def set_rgb(
        self,
        red: int,
        green: int,
        blue: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
    ) -> dict[str, object]:
        return self._post(
            "/rgb",
            _with_control_mode(
                {"obj": obj, "red": red, "green": green, "blue": blue}, control_mode
            ),
        )

    def set_hsi(
        self,
        hue: float,
        saturation: float,
        intensity: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
    ) -> dict[str, object]:
        return self._post(
            "/hsi",
            _with_control_mode(
                {
                    "obj": obj,
                    "hue": hue,
                    "saturation": saturation,
                    "intensity": intensity,
                },
                control_mode,
            ),
        )

    def frame(
        self,
        *,
        first_word: int | str,
        command: int | str,
        payload_hex: str = "",
        timeout: float | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "first_word": first_word,
            "command": command,
            "payload_hex": payload_hex,
        }
        if timeout is not None:
            payload["timeout"] = timeout
        return self._post("/frame", payload)

    def apply_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        control_mode: int | None = None,
    ) -> dict[str, object]:
        payload = _scene_payload(scene)
        return self._post("/scene", _with_control_mode(payload, control_mode))

    def transition(
        self,
        to_scene: Scene | Mapping[str, object],
        *,
        from_scene: Scene | Mapping[str, object] | None = None,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "to": _scene_payload(to_scene),
            "steps": steps,
            "duration": duration,
            "easing": easing,
        }
        if from_scene is not None:
            payload["from"] = _scene_payload(from_scene)
        return self._post("/transition", _with_control_mode(payload, control_mode))

    def apply_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        control_mode: int | None = None,
    ) -> dict[str, object]:
        payload = {"name": name}
        if overrides is not None:
            payload.update(overrides)
        return self._post("/preset", _with_control_mode(payload, control_mode))

    def run_sequence(
        self,
        steps: Iterable[Mapping[str, object]],
        *,
        control_mode: int | None = None,
        stop_on_unconfirmed: bool = False,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "steps": [dict(step) for step in steps],
            "stop_on_unconfirmed": stop_on_unconfirmed,
        }
        return self._post("/sequence", _with_control_mode(payload, control_mode))

    def _get(self, path: str) -> dict[str, object]:
        return self._request("GET", path)

    def _post(self, path: str, payload: Mapping[str, object]) -> dict[str, object]:
        return self._request("POST", path, payload)

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        data = None
        headers = {"accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["content-type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return _json_response(response.read())
        except HTTPError as exc:
            raise LightBridgeError(exc.code, _json_response(exc.read())) from exc


def _scene_payload(scene: Scene | Mapping[str, object]) -> dict[str, object]:
    if isinstance(scene, Scene):
        return scene.to_dict()
    return dict(scene)


def _with_control_mode(
    payload: dict[str, object],
    control_mode: int | None,
) -> dict[str, object]:
    if control_mode is not None:
        payload["control_mode"] = control_mode
    return payload


def _json_response(data: bytes) -> dict[str, object]:
    if not data:
        return {}
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("bridge response was not a JSON object")
    return payload
