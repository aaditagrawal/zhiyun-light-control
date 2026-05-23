"""Small stdlib client for the local HTTP bridge."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from urllib.error import HTTPError
from urllib.parse import urlencode
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

    def manifest(self) -> dict[str, object]:
        return self._get("/manifest")

    def capabilities(self) -> dict[str, object]:
        return self._get("/capabilities")

    def diagnostics(self) -> dict[str, object]:
        return self._get("/diagnostics")

    def ready(self) -> dict[str, object]:
        return self._get("/ready")

    def readiness_ready_for(self) -> dict[str, bool]:
        return readiness_ready_for(self.ready())

    def readiness_ready(self, capability: str) -> bool:
        return readiness_ready(self.ready(), capability)

    def readiness_requirements(self) -> dict[str, dict[str, object]]:
        return readiness_requirements(self.ready())

    def readiness_requirement(self, capability: str) -> dict[str, object]:
        return readiness_requirement(self.ready(), capability)

    def readiness_pending_action_ids(
        self,
        *,
        capability: str | None = None,
    ) -> list[str]:
        return readiness_pending_action_ids(self.ready(), capability=capability)

    def readiness_warnings(self) -> list[str]:
        return readiness_warnings(self.ready())

    def readiness_actions(
        self,
        *,
        include_ready: bool = True,
    ) -> dict[str, dict[str, object]]:
        return readiness_actions_by_id(self.ready(), include_ready=include_ready)

    def pending_readiness_actions(self) -> list[dict[str, object]]:
        return list(self.readiness_actions(include_ready=False).values())

    def readiness_action(self, action_id: str) -> dict[str, object] | None:
        return self.readiness_actions().get(action_id)

    def devices(
        self,
        *,
        include_ble: bool = False,
        include_ble_status: bool = False,
        ble_backend: str | None = None,
        timeout: float | None = None,
        name_contains: str | None = None,
    ) -> dict[str, object]:
        query: dict[str, object] = {}
        if include_ble:
            query["include_ble"] = "true"
        if include_ble_status:
            query["include_ble_status"] = "true"
        if ble_backend is not None:
            query["ble_backend"] = ble_backend
        if timeout is not None:
            query["timeout"] = timeout
        if name_contains is not None:
            query["name_contains"] = name_contains
        suffix = f"?{urlencode(query)}" if query else ""
        return self._get(f"/devices{suffix}")

    def openapi(self) -> dict[str, object]:
        return self._get("/openapi.json")

    def probe(self) -> dict[str, object]:
        return self._get("/probe")

    def status(self) -> dict[str, object]:
        return self._get("/status")

    def state(self) -> dict[str, object]:
        return self._get("/state")

    def history(
        self,
        *,
        after: int | None = None,
        limit: int | None = None,
    ) -> dict[str, object]:
        query: dict[str, object] = {}
        if after is not None:
            query["after"] = after
        if limit is not None:
            query["limit"] = limit
        suffix = f"?{urlencode(query)}" if query else ""
        return self._get(f"/history{suffix}")

    def presets(self) -> dict[str, object]:
        return self._get("/presets")

    def cues(self) -> dict[str, object]:
        return self._get("/cues")

    def state_events(
        self,
        *,
        limit: int | None = None,
        timeout: float = 30.0,
        initial: bool = True,
    ) -> Iterator[dict[str, object]]:
        query: dict[str, object] = {
            "timeout": timeout,
            "initial": str(initial).lower(),
        }
        if limit is not None:
            query["limit"] = limit
        request = Request(
            f"{self.base_url}/events?{urlencode(query)}",
            headers={"accept": "text/event-stream"},
            method="GET",
        )
        read_timeout = max(self.timeout, timeout + 2.0)
        with urlopen(request, timeout=read_timeout) as response:
            data_lines: list[str] = []
            for raw_line in response:
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if line == "":
                    if data_lines:
                        payload = json.loads("\n".join(data_lines))
                        if not isinstance(payload, dict):
                            raise ValueError("state event was not a JSON object")
                        yield payload
                    data_lines = []
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())

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

    def plan(self, payload: Mapping[str, object]) -> dict[str, object]:
        return self._post("/plan", dict(payload))

    def inspect_ble(
        self,
        *,
        backend: str | None = None,
        address: str | None = None,
        name_contains: str | None = None,
        timeout: float | None = None,
        python: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        if backend is not None:
            payload["backend"] = backend
        if address is not None:
            payload["address"] = address
        if name_contains is not None:
            payload["name_contains"] = name_contains
        if timeout is not None:
            payload["timeout"] = timeout
        if python is not None:
            payload["python"] = python
        return self._post("/inspect-ble", payload)

    def test_ble_endpoints(
        self,
        *,
        backend: str | None = None,
        address: str | None = None,
        name_contains: str | None = None,
        timeout: float | None = None,
        python: str | None = None,
        max_candidates: int | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        if backend is not None:
            payload["backend"] = backend
        if address is not None:
            payload["address"] = address
        if name_contains is not None:
            payload["name_contains"] = name_contains
        if timeout is not None:
            payload["timeout"] = timeout
        if python is not None:
            payload["python"] = python
        if max_candidates is not None:
            payload["max_candidates"] = max_candidates
        return self._post("/test-ble-endpoints", payload)

    def discover_usb(
        self,
        *,
        allow_control: bool = False,
        object_ids: Iterable[int | str] | None = None,
        first_words: Iterable[int | str] | None = None,
        control_object_ids: Iterable[int | str] | None = None,
        control_first_words: Iterable[int | str] | None = None,
        register_device_ids: Iterable[int | str] | None = None,
        register_group_ids: Iterable[int | str] | None = None,
        control_kinds: Iterable[str] | None = None,
        control_modes: Iterable[int | str] | None = None,
        timeout: float | None = None,
        brightness: float | None = None,
        kelvin: int | None = None,
        sleep: int | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"allow_control": allow_control}
        _set_iterable(payload, "object_ids", object_ids)
        _set_iterable(payload, "first_words", first_words)
        _set_iterable(payload, "control_object_ids", control_object_ids)
        _set_iterable(payload, "control_first_words", control_first_words)
        _set_iterable(payload, "register_device_ids", register_device_ids)
        _set_iterable(payload, "register_group_ids", register_group_ids)
        _set_iterable(payload, "control_kinds", control_kinds)
        _set_iterable(payload, "control_modes", control_modes)
        if timeout is not None:
            payload["timeout"] = timeout
        if brightness is not None:
            payload["brightness"] = brightness
        if kelvin is not None:
            payload["kelvin"] = kelvin
        if sleep is not None:
            payload["sleep"] = sleep
        return self._post("/discover-usb", payload)

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

    def run_cue(
        self,
        cue: Mapping[str, object],
        *,
        control_mode: int | None = None,
    ) -> dict[str, object]:
        return self._post("/sequence", _with_control_mode(dict(cue), control_mode))

    def run_named_cue(
        self,
        name: str,
        *,
        control_mode: int | None = None,
        obj: int | None = None,
        stop_on_unconfirmed: bool | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"name": name}
        if obj is not None:
            payload["obj"] = obj
        if stop_on_unconfirmed is not None:
            payload["stop_on_unconfirmed"] = stop_on_unconfirmed
        return self._post("/cue", _with_control_mode(payload, control_mode))

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


def command_result_status(result: Mapping[str, object]) -> str:
    status = result.get("transport_status")
    if status is not None:
        return str(status)
    if result.get("acknowledged") is True:
        return "acknowledged"
    return "unknown"


def command_result_acknowledged(result: Mapping[str, object]) -> bool:
    acknowledged = result.get("acknowledged")
    if isinstance(acknowledged, bool):
        return acknowledged
    return command_result_status(result) == "acknowledged"


def bridge_response_statuses(payload: Mapping[str, object]) -> list[str]:
    statuses: list[str] = []
    _append_bridge_statuses(dict(payload), statuses)
    return statuses


def bridge_response_applied(payload: Mapping[str, object]) -> bool:
    applied = payload.get("applied")
    if isinstance(applied, bool):
        return applied
    if "acknowledged" in payload or "transport_status" in payload:
        return command_result_acknowledged(payload)
    statuses = bridge_response_statuses(payload)
    return bool(statuses) and all(status == "acknowledged" for status in statuses)


def bridge_response_reason(payload: Mapping[str, object]) -> str | None:
    reason = payload.get("reason")
    if isinstance(reason, str):
        return reason
    statuses = bridge_response_statuses(payload)
    if any(status == "sent_no_response" for status in statuses):
        return "sent_no_response"
    if any(status == "echoed_write" for status in statuses):
        return "echoed_write"
    if statuses and any(status != "acknowledged" for status in statuses):
        return "unconfirmed"
    return None


def validation_summary(payload: Mapping[str, object]) -> dict[str, object]:
    summary = payload.get("summary")
    if not isinstance(summary, Mapping):
        return {}
    return {
        str(key): value
        for key, value in summary.items()
        if isinstance(key, str)
    }


def validation_ready_for(payload: Mapping[str, object]) -> dict[str, bool]:
    ready_for = validation_summary(payload).get("ready_for")
    if not isinstance(ready_for, Mapping):
        return {}
    return {
        str(key): value
        for key, value in ready_for.items()
        if isinstance(key, str) and isinstance(value, bool)
    }


def validation_ready(payload: Mapping[str, object], capability: str) -> bool:
    return validation_ready_for(payload).get(capability, False)


def validation_category(
    payload: Mapping[str, object],
    category: str,
) -> dict[str, object]:
    categories = validation_summary(payload).get("categories")
    if not isinstance(categories, Mapping):
        return {}
    value = categories.get(category)
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(key, str)
    }


def validation_unconfirmed_names(
    payload: Mapping[str, object],
    *,
    category: str | None = None,
) -> list[str]:
    if category is not None:
        raw_names = validation_category(payload, category).get("unconfirmed_names")
    else:
        raw_names = payload.get("unconfirmed")
    if not isinstance(raw_names, list):
        return []
    return [str(item) for item in raw_names if item is not None]


def readiness_ready_for(payload: Mapping[str, object]) -> dict[str, bool]:
    ready_for = payload.get("ready_for")
    if not isinstance(ready_for, Mapping):
        return {}
    return {
        str(key): value
        for key, value in ready_for.items()
        if isinstance(key, str) and isinstance(value, bool)
    }


def readiness_ready(payload: Mapping[str, object], capability: str) -> bool:
    return readiness_ready_for(payload).get(capability, False)


def readiness_requirements(
    payload: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    raw_requirements = payload.get("requirements")
    if not isinstance(raw_requirements, Mapping):
        return {}
    requirements: dict[str, dict[str, object]] = {}
    for raw_capability, raw_requirement in raw_requirements.items():
        if not isinstance(raw_capability, str):
            continue
        if not isinstance(raw_requirement, Mapping):
            continue
        requirements[raw_capability] = {
            str(key): value
            for key, value in raw_requirement.items()
            if isinstance(key, str)
        }
    return requirements


def readiness_requirement(
    payload: Mapping[str, object],
    capability: str,
) -> dict[str, object]:
    return readiness_requirements(payload).get(capability, {})


def readiness_pending_action_ids(
    payload: Mapping[str, object],
    *,
    capability: str | None = None,
) -> list[str]:
    if capability is None:
        return list(readiness_actions_by_id(payload, include_ready=False))
    raw_ids = readiness_requirement(payload, capability).get("pending_actions")
    if isinstance(raw_ids, list):
        return [str(item) for item in raw_ids if item is not None]
    actions = [
        action
        for action in readiness_actions_by_id(payload).values()
        if _action_requires(action, capability)
    ]
    return _pending_action_ids(actions, readiness_actions_by_id(payload))


def readiness_warnings(payload: Mapping[str, object]) -> list[str]:
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        return []
    return [str(item) for item in warnings if item is not None]


def _action_requires(action: Mapping[str, object], capability: str) -> bool:
    required_for = action.get("required_for")
    return isinstance(required_for, list) and capability in required_for


def _pending_action_ids(
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


def readiness_actions_by_id(
    payload: Mapping[str, object],
    *,
    include_ready: bool = True,
) -> dict[str, dict[str, object]]:
    actions: dict[str, dict[str, object]] = {}
    raw_actions = payload.get("actions")
    if not isinstance(raw_actions, list):
        return actions
    for raw_action in raw_actions:
        if not isinstance(raw_action, dict):
            continue
        action = {
            str(key): value
            for key, value in raw_action.items()
            if isinstance(key, str)
        }
        action_id = action.get("id")
        if not isinstance(action_id, str):
            continue
        if not include_ready and action.get("ready") is True:
            continue
        actions[action_id] = action
    return actions


def _append_bridge_statuses(value: object, statuses: list[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _append_bridge_statuses(item, statuses)
        return
    if not isinstance(value, dict):
        return
    status = value.get("transport_status")
    result_statuses = value.get("result_statuses")
    if isinstance(result_statuses, list):
        statuses.extend(str(item) for item in result_statuses if item is not None)
        return
    if status is not None:
        statuses.append(str(status))
        return
    for item in value.values():
        _append_bridge_statuses(item, statuses)


def _with_control_mode(
    payload: dict[str, object],
    control_mode: int | None,
) -> dict[str, object]:
    if control_mode is not None:
        payload["control_mode"] = control_mode
    return payload


def _set_iterable(
    payload: dict[str, object],
    key: str,
    value: Iterable[object] | None,
) -> None:
    if value is not None:
        payload[key] = list(value)


def _json_response(data: bytes) -> dict[str, object]:
    if not data:
        return {}
    payload = json.loads(data.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("bridge response was not a JSON object")
    return payload
