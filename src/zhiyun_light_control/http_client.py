"""Small stdlib client for the local HTTP bridge."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Iterator, Mapping
from os import PathLike
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .bridge import LightConnectionConfig
from .commands import (
    SerializedPlanBundle,
    serialized_plan_payload,
)
from .models import Scene
from .profiles import (
    LightSetupProfile,
    SetupProfileMissing,
    save_light_setup_profile,
    setup_profile_capabilities,
    setup_profile_primitive_readiness,
    setup_profile_primitive_readiness_map,
    setup_profile_primitive_ready,
    setup_profile_primitive_ready_for,
    setup_profile_summary,
    setup_profile_unready_primitive_capabilities,
)


class LightBridgeError(RuntimeError):
    def __init__(self, status: int, payload: object):
        super().__init__(f"bridge request failed with HTTP {status}: {payload}")
        self.status = status
        self.payload = payload


class LightBridgeNotReady(RuntimeError):
    def __init__(
        self,
        capabilities: Iterable[str],
        payload: Mapping[str, object],
    ):
        self.capabilities = _readiness_capabilities(capabilities)
        self.payload = {
            str(key): value
            for key, value in payload.items()
            if isinstance(key, str)
        }
        self.ready_for = readiness_ready_for(payload)
        self.pending_action_ids = {
            capability: readiness_pending_action_ids(payload, capability=capability)
            for capability in self.capabilities
            if not readiness_ready(payload, capability)
        }
        self.warnings = readiness_warnings(payload)
        details = ", ".join(
            f"{capability}: {', '.join(action_ids) or 'not ready'}"
            for capability, action_ids in self.pending_action_ids.items()
        )
        if not details:
            details = "not ready"
        super().__init__(
            f"bridge not ready for {', '.join(self.capabilities)} ({details})"
        )


class LightBridgeUnconfirmed(RuntimeError):
    def __init__(self, payload: Mapping[str, object]):
        self.payload = _string_key_dict(payload)
        self.statuses = bridge_response_statuses(payload)
        self.reason = bridge_response_reason(payload)
        details = self.reason or ", ".join(self.statuses) or "not acknowledged"
        super().__init__(f"bridge control response was not confirmed ({details})")


class LightBridgeClient:
    """Convenience wrapper for the local JSON bridge API."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8765",
        *,
        timeout: float = 3.0,
        require_ready_for_controls: bool = False,
        control_readiness: Iterable[str] | None = None,
        require_acknowledged_controls: bool = False,
        setup_profile: LightSetupProfile | None = None,
        require_setup_profile_controls: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.require_ready_for_controls = require_ready_for_controls
        self.control_readiness = _control_readiness_capabilities(control_readiness)
        self.require_acknowledged_controls = require_acknowledged_controls
        self.setup_profile_evidence = setup_profile
        self.require_setup_profile_controls = require_setup_profile_controls

    def with_setup_profile(
        self,
        profile: LightSetupProfile,
        *,
        require: str | Iterable[str] = (),
        require_controls: bool | None = None,
    ) -> LightBridgeClient:
        profile.require_ready(*_setup_profile_requirements(require))
        return LightBridgeClient(
            self.base_url,
            timeout=self.timeout,
            require_ready_for_controls=self.require_ready_for_controls,
            control_readiness=self.control_readiness,
            require_acknowledged_controls=self.require_acknowledged_controls,
            setup_profile=profile,
            require_setup_profile_controls=(
                self.require_setup_profile_controls
                if require_controls is None
                else require_controls
            ),
        )

    def require_setup_profile(self, *capabilities: str) -> LightSetupProfile:
        if self.setup_profile_evidence is None:
            raise SetupProfileMissing()
        return self.setup_profile_evidence.require_ready(*capabilities)

    def setup_profile_ready(self, capability: str) -> bool:
        return (
            self.setup_profile_evidence is not None
            and self.setup_profile_evidence.ready(capability)
        )

    def setup_profile_primitive_ready(self, primitive: str) -> bool:
        return (
            self.setup_profile_evidence is not None
            and self.setup_profile_evidence.primitive_ready(primitive)
        )

    def require_setup_profile_primitive(
        self,
        primitive: str,
    ) -> LightSetupProfile:
        if self.setup_profile_evidence is None:
            raise SetupProfileMissing()
        return self.setup_profile_evidence.require_primitive(primitive)

    def health(self) -> dict[str, object]:
        return self._get("/health")

    def commands(self) -> dict[str, object]:
        return self._get("/commands")

    def manifest(self) -> dict[str, object]:
        return self._get("/manifest")

    def capabilities(self) -> dict[str, object]:
        return self._get("/capabilities")

    def control_guard(self) -> dict[str, object]:
        return control_guard(self.capabilities())

    def request_templates(self) -> dict[str, dict[str, object]]:
        return request_templates(self.capabilities())

    def request_template(self, category: str, name: str) -> dict[str, object]:
        return request_template(self.capabilities(), category, name)

    def request_template_body(self, category: str, name: str) -> dict[str, object]:
        return request_template_body(self.capabilities(), category, name)

    def request_template_query(self, category: str, name: str) -> dict[str, object]:
        return request_template_query(self.capabilities(), category, name)

    def request_template_required_readiness(
        self,
        category: str,
        name: str,
    ) -> list[str]:
        return request_template_required_readiness(
            self.capabilities(),
            category,
            name,
        )

    def primitive_requirements_map(self) -> dict[str, list[str]]:
        return bridge_primitive_requirements_map(self.capabilities())

    def primitive_requirements(self, primitive: str) -> list[str]:
        return bridge_primitive_requirements(self.capabilities(), primitive)

    def diagnostics(self) -> dict[str, object]:
        return self._get("/diagnostics")

    def integration(
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
        snapshot = self._get(f"/integration{suffix}")
        snapshot["client"] = {
            "require_ready_for_controls": self.require_ready_for_controls,
            "control_readiness": list(self.control_readiness),
            "require_acknowledged_controls": self.require_acknowledged_controls,
            "require_setup_profile_controls": (
                self.require_setup_profile_controls
            ),
            "setup_profile": setup_profile_summary(self.setup_profile_evidence),
        }
        return snapshot

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

    def require_readiness(self, *capabilities: str) -> dict[str, object]:
        payload = self.ready()
        readiness_require(payload, capabilities)
        return payload

    def wait_until_ready(
        self,
        *capabilities: str,
        timeout: float = 10.0,
        interval: float = 0.25,
    ) -> dict[str, object]:
        selected = _readiness_capabilities(capabilities)
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            payload = self.ready()
            if readiness_all_ready(payload, selected):
                return payload
            if time.monotonic() >= deadline:
                raise LightBridgeNotReady(selected, payload)
            time.sleep(max(0.0, interval))

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

    def devices_selected_usb_port(self) -> str | None:
        return devices_selected_usb_port(self.devices())

    def devices_ble_authorization(self) -> str | None:
        return devices_ble_authorization(self.devices(include_ble_status=True))

    def devices_ble_blocker(self) -> str | None:
        return devices_ble_blocker(self.devices(include_ble_status=True))

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

    def setup_report(
        self,
        *,
        include_ble: bool = False,
        include_ble_status: bool = False,
        ble_backend: str | None = None,
        timeout: float | None = None,
        name_contains: str | None = None,
        persistent: bool = False,
        config_timeout: float | None = None,
        allow_control: bool = False,
        include_object_reads: bool = False,
        include_color: bool = False,
        values: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        snapshot = self.integration(
            include_ble=include_ble,
            include_ble_status=include_ble_status,
            ble_backend=ble_backend,
            timeout=timeout,
            name_contains=name_contains,
        )
        validation = self.validate(
            allow_control=allow_control,
            include_object_reads=include_object_reads,
            include_color=include_color,
            values=values,
        )
        return bridge_setup_report(
            snapshot,
            validation=validation,
            persistent=persistent,
            timeout=self.timeout if config_timeout is None else config_timeout,
            include_object_reads=include_object_reads,
        )

    def setup_profile(
        self,
        **options: object,
    ) -> LightSetupProfile:
        return LightSetupProfile.from_setup_report(self.setup_report(**options))

    def setup_capabilities(self, **options: object) -> dict[str, bool]:
        return setup_profile_capabilities(self.setup_report(**options))

    def setup_primitive_ready(
        self,
        primitive: str,
        **options: object,
    ) -> bool:
        return setup_profile_primitive_ready(
            self.setup_report(**options),
            primitive,
        )

    def setup_primitive_readiness(
        self,
        primitive: str,
        **options: object,
    ) -> dict[str, object]:
        return setup_profile_primitive_readiness(
            self.setup_report(**options),
            primitive,
        )

    def setup_primitive_readiness_map(
        self,
        **options: object,
    ) -> dict[str, dict[str, object]]:
        return setup_profile_primitive_readiness_map(self.setup_report(**options))

    def save_setup_profile(
        self,
        path: str | PathLike[str],
        *,
        indent: int | None = 2,
        **options: object,
    ) -> LightSetupProfile:
        profile = self.setup_profile(**options)
        save_light_setup_profile(profile, path, indent=indent)
        return profile

    def plan(self, payload: Mapping[str, object]) -> dict[str, object]:
        return self._post("/plan", dict(payload))

    def plan_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        obj: int | None = None,
        control_mode: int | None = None,
        first_word: int | str | None = None,
        start_seq: int | str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "action": "scene",
            "scene": _scene_payload(scene),
        }
        _set_plan_options(
            payload,
            obj=obj,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return self.plan(payload)

    def plan_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        obj: int | None = None,
        control_mode: int | None = None,
        first_word: int | str | None = None,
        start_seq: int | str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {"action": "preset", "preset": name}
        if overrides is not None:
            payload["overrides"] = dict(overrides)
        _set_plan_options(
            payload,
            obj=obj,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return self.plan(payload)

    def plan_transition(
        self,
        to_scene: Scene | Mapping[str, object],
        *,
        from_scene: Scene | Mapping[str, object] | None = None,
        obj: int | None = None,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int | None = None,
        first_word: int | str | None = None,
        start_seq: int | str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "action": "transition",
            "to": _scene_payload(to_scene),
            "steps": steps,
            "duration": duration,
            "easing": easing,
        }
        if from_scene is not None:
            payload["from"] = _scene_payload(from_scene)
        _set_plan_options(
            payload,
            obj=obj,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return self.plan(payload)

    def plan_sequence(
        self,
        steps: Iterable[Mapping[str, object]],
        *,
        obj: int | None = None,
        stop_on_unconfirmed: bool = False,
        control_mode: int | None = None,
        first_word: int | str | None = None,
        start_seq: int | str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "action": "sequence",
            "steps": [dict(step) for step in steps],
            "stop_on_unconfirmed": stop_on_unconfirmed,
        }
        _set_plan_options(
            payload,
            obj=obj,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=start_seq,
        )
        return self.plan(payload)

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
        post_register_reads: bool = False,
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
        if post_register_reads:
            payload["post_register_reads"] = True
        if timeout is not None:
            payload["timeout"] = timeout
        if brightness is not None:
            payload["brightness"] = brightness
        if kelvin is not None:
            payload["kelvin"] = kelvin
        if sleep is not None:
            payload["sleep"] = sleep
        return self._post("/discover-usb", payload)

    def register(
        self,
        *,
        device_id: int = 0,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "register",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        return self._post_control("/register", {"device_id": device_id})

    def set_brightness(
        self,
        value: float,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_brightness",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        return self._post_control(
            "/brightness",
            _with_control_mode({"obj": obj, "value": value}, control_mode),
        )

    def set_cct(
        self,
        kelvin: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_cct",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        return self._post_control(
            "/cct",
            _with_control_mode({"obj": obj, "kelvin": kelvin}, control_mode),
        )

    def set_sleep(
        self,
        value: int,
        *,
        obj: int = 1,
        control_mode: int | None = None,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_sleep",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        return self._post_control(
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
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_rgb",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        return self._post_control(
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
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "set_hsi",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        return self._post_control(
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
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "frame",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        payload: dict[str, object] = {
            "first_word": first_word,
            "command": command,
            "payload_hex": payload_hex,
        }
        if timeout is not None:
            payload["timeout"] = timeout
        return self._post_control("/frame", payload)

    def execute_plan(
        self,
        plan: SerializedPlanBundle | Mapping[str, object],
        *,
        timeout: float | None = None,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        plan_payload = serialized_plan_payload(plan)
        self._require_setup_profile_primitive_if_requested(
            _plan_primitive_name(plan_payload),
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        body = plan.to_dict() if isinstance(plan, SerializedPlanBundle) else dict(plan)
        if timeout is not None:
            body["timeout"] = timeout
        return self._post_control("/execute-plan", body)

    def apply_scene(
        self,
        scene: Scene | Mapping[str, object],
        *,
        control_mode: int | None = None,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "scene",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        payload = _scene_payload(scene)
        return self._post_control("/scene", _with_control_mode(payload, control_mode))

    def transition(
        self,
        to_scene: Scene | Mapping[str, object],
        *,
        from_scene: Scene | Mapping[str, object] | None = None,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int | None = None,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "transition",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        payload: dict[str, object] = {
            "to": _scene_payload(to_scene),
            "steps": steps,
            "duration": duration,
            "easing": easing,
        }
        if from_scene is not None:
            payload["from"] = _scene_payload(from_scene)
        return self._post_control(
            "/transition",
            _with_control_mode(payload, control_mode),
        )

    def apply_preset(
        self,
        name: str,
        *,
        overrides: Mapping[str, object] | None = None,
        control_mode: int | None = None,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "preset",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        payload = {"name": name}
        if overrides is not None:
            payload.update(overrides)
        return self._post_control("/preset", _with_control_mode(payload, control_mode))

    def run_sequence(
        self,
        steps: Iterable[Mapping[str, object]],
        *,
        control_mode: int | None = None,
        stop_on_unconfirmed: bool = False,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "sequence",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        payload: dict[str, object] = {
            "steps": [dict(step) for step in steps],
            "stop_on_unconfirmed": stop_on_unconfirmed,
        }
        return self._post_control(
            "/sequence",
            _with_control_mode(payload, control_mode),
        )

    def run_cue(
        self,
        cue: Mapping[str, object],
        *,
        control_mode: int | None = None,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "cue",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        return self._post_control(
            "/sequence",
            _with_control_mode(dict(cue), control_mode),
        )

    def run_named_cue(
        self,
        name: str,
        *,
        control_mode: int | None = None,
        obj: int | None = None,
        stop_on_unconfirmed: bool | None = None,
        require_ready: bool = False,
        required_readiness: Iterable[str] | None = None,
        require_setup_profile: bool = False,
    ) -> dict[str, object]:
        self._require_setup_profile_primitive_if_requested(
            "run_named_cue",
            require_setup_profile,
        )
        self._require_control_readiness(require_ready, required_readiness)
        payload: dict[str, object] = {"name": name}
        if obj is not None:
            payload["obj"] = obj
        if stop_on_unconfirmed is not None:
            payload["stop_on_unconfirmed"] = stop_on_unconfirmed
        return self._post_control("/cue", _with_control_mode(payload, control_mode))

    def _get(self, path: str) -> dict[str, object]:
        return self._request("GET", path)

    def _post(self, path: str, payload: Mapping[str, object]) -> dict[str, object]:
        return self._request("POST", path, payload)

    def _post_control(
        self,
        path: str,
        payload: Mapping[str, object],
    ) -> dict[str, object]:
        response = self._post(path, payload)
        if self.require_acknowledged_controls:
            require_acknowledged_response(response)
        return response

    def _require_control_readiness(
        self,
        require_ready: bool,
        required_readiness: Iterable[str] | None,
    ) -> None:
        if not (
            require_ready
            or required_readiness is not None
            or self.require_ready_for_controls
        ):
            return
        if required_readiness is not None:
            capabilities = _control_readiness_capabilities(required_readiness)
        elif self.require_ready_for_controls:
            capabilities = self.control_readiness
        else:
            capabilities = _control_readiness_capabilities(None)
        self.require_readiness(*capabilities)

    def _require_setup_profile_primitive_if_requested(
        self,
        primitive: str,
        require_setup_profile: bool,
    ) -> None:
        if require_setup_profile or self.require_setup_profile_controls:
            self.require_setup_profile_primitive(primitive)

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


def _plan_primitive_name(plan: Mapping[str, object]) -> str:
    action = str(plan.get("action", "scene"))
    if action in {"preset", "sequence", "cue", "run_named_cue", "transition"}:
        return action
    return "scene"


def _setup_profile_requirements(require: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(require, str):
        return (require,) if require else ()
    return tuple(str(item) for item in require)


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


def require_acknowledged_response(
    payload: Mapping[str, object],
) -> dict[str, object]:
    response = _string_key_dict(payload)
    if not bridge_response_applied(response):
        raise LightBridgeUnconfirmed(response)
    return response


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


def bridge_connection_config(
    payload: Mapping[str, object],
    *,
    persistent: bool = False,
    timeout: float = 1.5,
) -> LightConnectionConfig:
    summary = _bridge_setup_summary(payload)
    transport = _optional_text(summary.get("transport")) or "usb"
    if transport == "ble":
        return LightConnectionConfig.ble(
            address=_optional_text(summary.get("ble_address")),
            name_contains=_optional_text(summary.get("ble_name_contains")),
            timeout=timeout,
            backend=_optional_text(summary.get("ble_backend")) or "worker",
            profile=_optional_text(summary.get("ble_profile")) or "direct",
            persistent=persistent,
        )
    if transport == "usb":
        return LightConnectionConfig.usb(
            port=_bridge_setup_selected_usb_port(payload),
            timeout=timeout,
            persistent=persistent,
        )
    return LightConnectionConfig(
        transport=transport,
        timeout=timeout,
        persistent=persistent,
    )


def bridge_setup_report(
    payload: Mapping[str, object],
    *,
    validation: Mapping[str, object] | None = None,
    persistent: bool = False,
    timeout: float = 1.5,
    include_object_reads: bool = False,
) -> dict[str, object]:
    summary = _bridge_setup_summary(payload)
    ready = _bridge_setup_ready(payload)
    devices = _bridge_setup_devices(payload)
    status = _bridge_setup_status(payload)
    validation_payload = _string_key_dict(validation) if validation is not None else {}
    ready_for = _bridge_setup_ready_for(summary, ready)
    validation_ready = validation_ready_for(validation_payload)
    validation_unconfirmed = validation_unconfirmed_names(validation_payload)
    connection_confirmed = _bridge_setup_connection_confirmed(summary, ready, status)
    control_enabled = summary.get("control_enabled") is True
    errors = _bridge_setup_errors(ready, validation_payload)
    report_summary = {
        "ok": connection_confirmed,
        "connection_confirmed": connection_confirmed,
        "route_confirmed": connection_confirmed,
        "ready_for": ready_for,
        "validation_ready_for": validation_ready,
        "validation_unconfirmed": validation_unconfirmed,
        "pending_action_ids": _bridge_setup_pending_action_ids(summary, ready),
        "warnings": _bridge_setup_warnings(summary, ready),
        "errors": errors,
    }
    report = {
        "api": "zhiyun-light-control",
        "ok": connection_confirmed,
        "source": "http_bridge",
        "config": bridge_connection_config(
            payload,
            persistent=persistent,
            timeout=timeout,
        ).to_dict(),
        "selected_route": None,
        "routes": [],
        "route_confirmed": connection_confirmed,
        "require_confirmed_route": True,
        "control_enabled": control_enabled,
        "include_object_reads": include_object_reads,
        "status_ok": connection_confirmed,
        "status_error": errors[0] if errors and not connection_confirmed else None,
        "status": status,
        "ready_for": ready_for,
        "validation_ready_for": validation_ready,
        "validation_unconfirmed": validation_unconfirmed,
        "validation": validation_payload,
        "ready": ready,
        "devices": devices,
        "integration": _string_key_dict(payload),
        "summary": report_summary,
    }
    report["capabilities"] = setup_profile_capabilities(report)
    report["primitive_ready_for"] = setup_profile_primitive_ready_for(report)
    report["primitive_readiness"] = setup_profile_primitive_readiness_map(report)
    return report


def control_guard(payload: Mapping[str, object]) -> dict[str, object]:
    guard = _metadata_payload(payload).get("control_guard")
    if not isinstance(guard, Mapping):
        return {}
    return _string_key_dict(guard)


def request_templates(payload: Mapping[str, object]) -> dict[str, dict[str, object]]:
    raw_templates = _metadata_payload(payload).get("request_templates")
    if not isinstance(raw_templates, Mapping):
        return {}
    templates: dict[str, dict[str, object]] = {}
    for raw_category, raw_category_templates in raw_templates.items():
        if not isinstance(raw_category, str):
            continue
        if not isinstance(raw_category_templates, Mapping):
            continue
        templates[raw_category] = _string_key_dict(raw_category_templates)
    return templates


def request_template(
    payload: Mapping[str, object],
    category: str,
    name: str,
) -> dict[str, object]:
    category_templates = request_templates(payload).get(category)
    if not isinstance(category_templates, Mapping):
        return {}
    template = category_templates.get(name)
    if not isinstance(template, Mapping):
        return {}
    return _string_key_dict(template)


def request_template_body(
    payload: Mapping[str, object],
    category: str,
    name: str,
) -> dict[str, object]:
    body = request_template(payload, category, name).get("body")
    if not isinstance(body, Mapping):
        return {}
    return _string_key_dict(body)


def request_template_query(
    payload: Mapping[str, object],
    category: str,
    name: str,
) -> dict[str, object]:
    query = request_template(payload, category, name).get("query")
    if not isinstance(query, Mapping):
        return {}
    return _string_key_dict(query)


def request_template_required_readiness(
    payload: Mapping[str, object],
    category: str,
    name: str,
) -> list[str]:
    required = request_template(payload, category, name).get("required_readiness")
    if not isinstance(required, list):
        return []
    return [str(item) for item in required if item is not None]


def bridge_primitive_requirements_map(
    payload: Mapping[str, object],
) -> dict[str, list[str]]:
    raw_requirements = _metadata_payload(payload).get("primitive_requirements")
    if not isinstance(raw_requirements, Mapping):
        return {}
    requirements: dict[str, list[str]] = {}
    for raw_primitive, raw_capabilities in raw_requirements.items():
        if not isinstance(raw_primitive, str):
            continue
        if not isinstance(raw_capabilities, list):
            continue
        requirements[raw_primitive] = [
            str(capability)
            for capability in raw_capabilities
            if capability is not None
        ]
    return requirements


def bridge_primitive_requirements(
    payload: Mapping[str, object],
    primitive: str,
) -> list[str]:
    normalized = primitive.strip().lower().replace("-", "_")
    return bridge_primitive_requirements_map(payload).get(normalized, [])


def bridge_setup_capabilities(
    payload: Mapping[str, object],
) -> dict[str, bool]:
    return setup_profile_capabilities(payload)


def bridge_setup_primitive_ready(
    payload: Mapping[str, object],
    primitive: str,
) -> bool:
    return setup_profile_primitive_ready(payload, primitive)


def bridge_setup_unready_primitive_capabilities(
    payload: Mapping[str, object],
    primitive: str,
) -> list[str]:
    return setup_profile_unready_primitive_capabilities(payload, primitive)


def bridge_setup_primitive_readiness(
    payload: Mapping[str, object],
    primitive: str,
) -> dict[str, object]:
    return setup_profile_primitive_readiness(payload, primitive)


def bridge_setup_primitive_readiness_map(
    payload: Mapping[str, object],
) -> dict[str, dict[str, object]]:
    return setup_profile_primitive_readiness_map(payload)


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


def readiness_all_ready(
    payload: Mapping[str, object],
    capabilities: Iterable[str],
) -> bool:
    return not readiness_unready_capabilities(payload, capabilities)


def readiness_unready_capabilities(
    payload: Mapping[str, object],
    capabilities: Iterable[str],
) -> list[str]:
    return [
        capability
        for capability in _readiness_capabilities(capabilities)
        if not readiness_ready(payload, capability)
    ]


def readiness_require(
    payload: Mapping[str, object],
    capabilities: Iterable[str],
) -> None:
    selected = _readiness_capabilities(capabilities)
    if readiness_unready_capabilities(payload, selected):
        raise LightBridgeNotReady(selected, payload)


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


def _readiness_capabilities(capabilities: Iterable[str]) -> tuple[str, ...]:
    selected = tuple(str(capability) for capability in capabilities)
    return selected or ("read_status",)


def _control_readiness_capabilities(
    capabilities: Iterable[str] | None,
) -> tuple[str, ...]:
    if capabilities is None:
        return ("control_requests",)
    return tuple(str(capability) for capability in capabilities)


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


def devices_usb(payload: Mapping[str, object]) -> dict[str, object]:
    devices = _devices_payload(payload)
    usb = devices.get("usb")
    if not isinstance(usb, Mapping):
        return {}
    return _string_key_dict(usb)


def devices_usb_available(payload: Mapping[str, object]) -> bool:
    return devices_usb(payload).get("available") is True


def devices_selected_usb_port(payload: Mapping[str, object]) -> str | None:
    port = devices_usb(payload).get("selected_port")
    return str(port) if port is not None else None


def devices_usb_ports(payload: Mapping[str, object]) -> list[dict[str, object]]:
    ports = devices_usb(payload).get("ports")
    if not isinstance(ports, list):
        return []
    return [_string_key_dict(port) for port in ports if isinstance(port, Mapping)]


def devices_ble(payload: Mapping[str, object]) -> dict[str, object]:
    devices = _devices_payload(payload)
    ble = devices.get("ble")
    if not isinstance(ble, Mapping):
        return {}
    return _string_key_dict(ble)


def devices_ble_status(payload: Mapping[str, object]) -> dict[str, object]:
    status = devices_ble(payload).get("macos_status")
    if not isinstance(status, Mapping):
        return {}
    return _string_key_dict(status)


def devices_ble_authorization(payload: Mapping[str, object]) -> str | None:
    authorization = devices_ble_status(payload).get("authorization")
    return str(authorization) if authorization is not None else None


def devices_ble_state(payload: Mapping[str, object]) -> str | None:
    state = devices_ble_status(payload).get("state")
    return str(state) if state is not None else None


def devices_ble_scan(payload: Mapping[str, object]) -> dict[str, object]:
    scan = devices_ble(payload).get("scan")
    if not isinstance(scan, Mapping):
        return {}
    return _string_key_dict(scan)


def devices_ble_scan_ok(payload: Mapping[str, object]) -> bool:
    return devices_ble_scan(payload).get("ok") is True


def devices_ble_scan_devices(payload: Mapping[str, object]) -> list[dict[str, object]]:
    devices = devices_ble_scan(payload).get("devices")
    if not isinstance(devices, list):
        return []
    return [
        _string_key_dict(device)
        for device in devices
        if isinstance(device, Mapping)
    ]


def devices_ble_blocker(payload: Mapping[str, object]) -> str | None:
    status_error = devices_ble_status(payload).get("error")
    if status_error is not None:
        return str(status_error)
    scan_error = devices_ble_scan(payload).get("error")
    if scan_error is not None:
        return str(scan_error)
    return None


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


def _devices_payload(payload: Mapping[str, object]) -> dict[str, object]:
    if "usb" in payload or "ble" in payload:
        return _string_key_dict(payload)
    devices = payload.get("devices")
    if not isinstance(devices, Mapping):
        return {}
    return _string_key_dict(devices)


def _bridge_setup_summary(payload: Mapping[str, object]) -> dict[str, object]:
    summary = payload.get("summary")
    if isinstance(summary, Mapping):
        return _string_key_dict(summary)
    return {}


def _bridge_setup_payloads(payload: Mapping[str, object]) -> dict[str, object]:
    payloads = payload.get("payloads")
    if isinstance(payloads, Mapping):
        return _string_key_dict(payloads)
    return {}


def _bridge_setup_ready(payload: Mapping[str, object]) -> dict[str, object]:
    ready = _bridge_setup_payloads(payload).get("ready")
    if isinstance(ready, Mapping):
        return _string_key_dict(ready)
    if "ready_for" in payload:
        return _string_key_dict(payload)
    return {}


def _bridge_setup_devices(payload: Mapping[str, object]) -> dict[str, object]:
    devices = _bridge_setup_payloads(payload).get("devices")
    if isinstance(devices, Mapping):
        return _string_key_dict(devices)
    return _devices_payload(payload)


def _bridge_setup_status(payload: Mapping[str, object]) -> dict[str, object]:
    ready_status = _bridge_setup_ready(payload).get("status")
    if isinstance(ready_status, Mapping):
        return _string_key_dict(ready_status)
    status = payload.get("status")
    if isinstance(status, Mapping):
        return _string_key_dict(status)
    return {}


def _bridge_setup_selected_usb_port(payload: Mapping[str, object]) -> str | None:
    summary_port = _bridge_setup_summary(payload).get("selected_usb_port")
    if summary_port is not None:
        return str(summary_port)
    return devices_selected_usb_port(_bridge_setup_devices(payload))


def _bridge_setup_ready_for(
    summary: Mapping[str, object],
    ready: Mapping[str, object],
) -> dict[str, bool]:
    ready_for = summary.get("ready_for")
    if isinstance(ready_for, Mapping):
        return {
            str(key): value is True
            for key, value in ready_for.items()
        }
    return readiness_ready_for(ready)


def _bridge_setup_connection_confirmed(
    summary: Mapping[str, object],
    ready: Mapping[str, object],
    status: Mapping[str, object],
) -> bool:
    if summary.get("connection_confirmed") is True:
        return True
    if ready.get("connection_confirmed") is True:
        return True
    return status.get("connection_confirmed") is True


def _bridge_setup_pending_action_ids(
    summary: Mapping[str, object],
    ready: Mapping[str, object],
) -> list[str]:
    pending = summary.get("pending_action_ids")
    if isinstance(pending, list):
        return [str(item) for item in pending if item is not None]
    return readiness_pending_action_ids(ready)


def _bridge_setup_warnings(
    summary: Mapping[str, object],
    ready: Mapping[str, object],
) -> list[str]:
    warnings = summary.get("warnings")
    if isinstance(warnings, list):
        return [str(item) for item in warnings if item is not None]
    return readiness_warnings(ready)


def _bridge_setup_errors(
    ready: Mapping[str, object],
    validation: Mapping[str, object],
) -> list[str]:
    errors: list[str] = []
    ready_status = ready.get("status")
    if isinstance(ready_status, Mapping):
        error = ready_status.get("error")
        if error is not None:
            errors.append(str(error))
    validation_error = validation.get("error")
    if validation_error is not None:
        errors.append(str(validation_error))
    return errors


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _metadata_payload(payload: Mapping[str, object]) -> dict[str, object]:
    if "control_guard" in payload or "request_templates" in payload:
        return _string_key_dict(payload)
    raw_payloads = payload.get("payloads")
    if isinstance(raw_payloads, Mapping):
        capabilities = raw_payloads.get("capabilities")
        if isinstance(capabilities, Mapping):
            return _string_key_dict(capabilities)
        manifest = raw_payloads.get("manifest")
        if isinstance(manifest, Mapping):
            return _string_key_dict(manifest)
    return {}


def _string_key_dict(payload: Mapping[object, object]) -> dict[str, object]:
    return {
        str(key): value
        for key, value in payload.items()
        if isinstance(key, str)
    }


def _with_control_mode(
    payload: dict[str, object],
    control_mode: int | None,
) -> dict[str, object]:
    if control_mode is not None:
        payload["control_mode"] = control_mode
    return payload


def _set_plan_options(
    payload: dict[str, object],
    *,
    obj: int | None,
    control_mode: int | None,
    first_word: int | str | None,
    start_seq: int | str | None,
) -> None:
    if obj is not None:
        payload["obj"] = obj
    if control_mode is not None:
        payload["control_mode"] = control_mode
    if first_word is not None:
        payload["first_word"] = first_word
    if start_seq is not None:
        payload["start_seq"] = start_seq


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
