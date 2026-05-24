"""Portable setup profiles for host integrations."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from os import PathLike
from pathlib import Path

from .bridge import LightConnectionConfig

SETUP_PROFILE_KIND = "setup-profile"
SETUP_PROFILE_SCHEMA_VERSION = 1
SETUP_PROFILE_PRIMITIVE_REQUIREMENTS = {
    "status": ("read_status",),
    "probe": ("read_status",),
    "readiness": ("read_status",),
    "setup_report": ("read_status",),
    "setup_profile": ("read_status",),
    "devices": ("device_discovery",),
    "state_events": ("state_events",),
    "events": ("state_events",),
    "history": ("state_events",),
    "read_brightness": ("object_reads",),
    "read_cct": ("object_reads",),
    "read_sleep": ("object_reads",),
    "read_object_voltage": ("object_reads",),
    "read_object_mode": ("object_reads",),
    "read_object_firmware": ("object_reads",),
    "identify": ("object_reads",),
    "register": ("control_setup",),
    "register_default_group": ("control_setup",),
    "brightness": ("control_writes",),
    "cct": ("control_writes",),
    "sleep": ("control_writes",),
    "rgb": ("control_writes",),
    "hsi": ("control_writes",),
    "set_brightness": ("control_writes",),
    "set_cct": ("control_writes",),
    "set_sleep": ("control_writes",),
    "set_rgb": ("control_writes",),
    "set_hsi": ("control_writes",),
    "scene": ("control_writes",),
    "preset": ("control_writes",),
    "sequence": ("control_writes",),
    "cue": ("control_writes",),
    "frame": ("control_writes",),
    "execute_plan": ("control_writes",),
    "apply_scene": ("control_writes",),
    "apply_preset": ("control_writes",),
    "run_sequence": ("control_writes",),
    "run_cue": ("control_writes",),
    "run_named_cue": ("control_writes",),
    "transition": ("control_writes",),
}


@dataclass(frozen=True)
class LightSetupProfile:
    """Persisted connection config plus the evidence that made it usable."""

    config: LightConnectionConfig
    setup_report: dict[str, object]
    created_at: float
    schema_version: int = SETUP_PROFILE_SCHEMA_VERSION

    @classmethod
    def from_setup_report(
        cls,
        payload: Mapping[str, object],
        *,
        created_at: float | None = None,
    ) -> LightSetupProfile:
        config = LightConnectionConfig.from_mapping(_mapping(payload, "config"))
        return cls(
            config=config,
            setup_report=_dict_from_mapping(payload),
            created_at=time.time() if created_at is None else created_at,
        )

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
    ) -> LightSetupProfile:
        kind = str(payload.get("kind", SETUP_PROFILE_KIND))
        if kind != SETUP_PROFILE_KIND:
            raise ValueError(f"unsupported setup profile kind: {kind}")
        report = _mapping(payload, "setup_report")
        config_payload = payload.get("config")
        if isinstance(config_payload, Mapping):
            config = LightConnectionConfig.from_mapping(
                _dict_from_mapping(config_payload)
            )
        else:
            config = LightConnectionConfig.from_mapping(_mapping(report, "config"))
        return cls(
            config=config,
            setup_report=_dict_from_mapping(report),
            created_at=float(payload.get("created_at", 0.0)),
            schema_version=int(
                payload.get("schema_version", SETUP_PROFILE_SCHEMA_VERSION)
            ),
        )

    @property
    def ok(self) -> bool:
        return self.setup_report.get("ok") is True

    @property
    def route_confirmed(self) -> bool:
        return self.setup_report.get("route_confirmed") is True

    @property
    def connection_confirmed(self) -> bool:
        summary = _optional_mapping(self.setup_report.get("summary"))
        if summary is not None and "connection_confirmed" in summary:
            return summary.get("connection_confirmed") is True
        return self.setup_report.get("status_ok") is True

    @property
    def capabilities(self) -> dict[str, bool]:
        return setup_profile_capabilities(self.setup_report)

    @property
    def primitive_ready_for(self) -> dict[str, bool]:
        return setup_profile_primitive_ready_for(self)

    @property
    def primitive_readiness(self) -> dict[str, dict[str, object]]:
        return setup_profile_primitive_readiness_map(self)

    @property
    def ready_for(self) -> dict[str, bool]:
        return _bool_mapping(self.setup_report.get("ready_for"))

    @property
    def validation_ready_for(self) -> dict[str, bool]:
        return _bool_mapping(self.setup_report.get("validation_ready_for"))

    @property
    def validation_unconfirmed(self) -> list[str]:
        value = self.setup_report.get("validation_unconfirmed")
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    @property
    def summary(self) -> dict[str, object]:
        summary = _optional_mapping(self.setup_report.get("summary"))
        if summary is None:
            return {}
        return _dict_from_mapping(summary)

    def ready(self, capability: str) -> bool:
        return setup_profile_ready(self, capability)

    def unready_capabilities(self, *capabilities: str) -> list[str]:
        return setup_profile_unready_capabilities(self, capabilities)

    def require_ready(self, *capabilities: str) -> LightSetupProfile:
        missing = self.unready_capabilities(*capabilities)
        if missing:
            raise SetupProfileNotReady(self, missing)
        return self

    def primitive_requirements(self, primitive: str) -> tuple[str, ...]:
        return setup_profile_primitive_requirements(primitive)

    def primitive_ready(self, primitive: str) -> bool:
        return setup_profile_primitive_ready(self, primitive)

    def unready_primitive_capabilities(self, primitive: str) -> list[str]:
        return setup_profile_unready_primitive_capabilities(self, primitive)

    def primitive_readiness_for(self, primitive: str) -> dict[str, object]:
        return setup_profile_primitive_readiness(self, primitive)

    def require_primitive(self, primitive: str) -> LightSetupProfile:
        return self.require_ready(*self.primitive_requirements(primitive))

    def to_dict(self) -> dict[str, object]:
        return {
            "api": "zhiyun-light-control",
            "kind": SETUP_PROFILE_KIND,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "config": self.config.to_dict(),
            "summary": self.summary,
            "capabilities": self.capabilities,
            "ready_for": self.ready_for,
            "validation_ready_for": self.validation_ready_for,
            "validation_unconfirmed": self.validation_unconfirmed,
            "primitive_ready_for": self.primitive_ready_for,
            "primitive_readiness": self.primitive_readiness,
            "setup_report": _dict_from_mapping(self.setup_report),
        }


class SetupProfileNotReady(RuntimeError):
    def __init__(
        self,
        profile: LightSetupProfile,
        capabilities: list[str],
    ) -> None:
        self.profile = profile
        self.capabilities = tuple(capabilities)
        self.ready_for = profile.ready_for
        self.validation_ready_for = profile.validation_ready_for
        self.validation_unconfirmed = profile.validation_unconfirmed
        missing = ", ".join(self.capabilities)
        super().__init__(f"setup profile not ready for {missing}")


class SetupProfileMissing(RuntimeError):
    def __init__(self) -> None:
        super().__init__("integration has no setup profile evidence")


def setup_profile_primitive_requirements(primitive: str) -> tuple[str, ...]:
    normalized = _normalize_primitive(primitive)
    requirements = SETUP_PROFILE_PRIMITIVE_REQUIREMENTS.get(normalized)
    if requirements is None:
        raise ValueError(f"unknown setup profile primitive: {primitive}")
    return requirements


def setup_profile_primitive_requirements_map() -> dict[str, tuple[str, ...]]:
    return {
        primitive: tuple(requirements)
        for primitive, requirements in sorted(
            SETUP_PROFILE_PRIMITIVE_REQUIREMENTS.items()
        )
    }


def setup_profile_capabilities(
    payload: LightSetupProfile | Mapping[str, object],
) -> dict[str, bool]:
    evidence = _setup_evidence_mapping(payload)
    capabilities = _bool_mapping(evidence.get("ready_for"))
    for name, ready in _setup_validation_ready_for(evidence).items():
        if name in capabilities:
            capabilities[name] = capabilities[name] and ready
        else:
            capabilities[name] = ready
    return capabilities


def setup_profile_ready(
    payload: LightSetupProfile | Mapping[str, object],
    capability: str,
) -> bool:
    return setup_profile_capabilities(payload).get(capability, False)


def setup_profile_unready_capabilities(
    payload: LightSetupProfile | Mapping[str, object],
    capabilities: Iterable[str],
) -> list[str]:
    readiness = setup_profile_capabilities(payload)
    return [
        capability
        for capability in capabilities
        if not readiness.get(capability, False)
    ]


def setup_profile_primitive_ready(
    payload: LightSetupProfile | Mapping[str, object],
    primitive: str,
) -> bool:
    return not setup_profile_unready_primitive_capabilities(payload, primitive)


def setup_profile_unready_primitive_capabilities(
    payload: LightSetupProfile | Mapping[str, object],
    primitive: str,
) -> list[str]:
    return setup_profile_unready_capabilities(
        payload,
        setup_profile_primitive_requirements(primitive),
    )


def setup_profile_primitive_readiness(
    payload: LightSetupProfile | Mapping[str, object],
    primitive: str,
) -> dict[str, object]:
    normalized = _normalize_primitive(primitive)
    requirements = setup_profile_primitive_requirements(normalized)
    capabilities = setup_profile_capabilities(payload)
    unready = [
        capability
        for capability in requirements
        if not capabilities.get(capability, False)
    ]
    return {
        "primitive": normalized,
        "ready": not unready,
        "requirements": list(requirements),
        "capabilities": {
            capability: capabilities.get(capability, False)
            for capability in requirements
        },
        "unready_capabilities": unready,
    }


def setup_profile_primitive_ready_for(
    payload: LightSetupProfile | Mapping[str, object],
) -> dict[str, bool]:
    return {
        primitive: not readiness["unready_capabilities"]
        for primitive, readiness in setup_profile_primitive_readiness_map(
            payload
        ).items()
    }


def setup_profile_primitive_readiness_map(
    payload: LightSetupProfile | Mapping[str, object],
) -> dict[str, dict[str, object]]:
    return {
        primitive: setup_profile_primitive_readiness(payload, primitive)
        for primitive in setup_profile_primitive_requirements_map()
    }


def setup_profile_summary(
    profile: LightSetupProfile | None,
) -> dict[str, object]:
    if profile is None:
        return {"present": False}
    return {
        "present": True,
        "ok": profile.ok,
        "config": profile.config.to_dict(),
        "capabilities": profile.capabilities,
        "primitive_ready_for": profile.primitive_ready_for,
    }


def light_setup_profile_from_mapping(
    payload: Mapping[str, object],
) -> LightSetupProfile:
    return LightSetupProfile.from_mapping(payload)


def light_setup_profile_from_json(text: str) -> LightSetupProfile:
    payload = json.loads(text)
    if not isinstance(payload, Mapping):
        raise ValueError("setup profile JSON must contain an object")
    return LightSetupProfile.from_mapping(payload)


def light_setup_profile_to_json(
    profile: LightSetupProfile,
    *,
    indent: int | None = 2,
) -> str:
    return json.dumps(profile.to_dict(), indent=indent, sort_keys=True)


def save_light_setup_profile(
    profile: LightSetupProfile,
    path: str | PathLike[str],
    *,
    indent: int | None = 2,
) -> None:
    text = light_setup_profile_to_json(profile, indent=indent)
    Path(path).write_text(f"{text}\n", encoding="utf-8")


def load_light_setup_profile(path: str | PathLike[str]) -> LightSetupProfile:
    return light_setup_profile_from_json(Path(path).read_text(encoding="utf-8"))


def _mapping(
    payload: Mapping[str, object],
    key: str,
) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"setup profile is missing a {key} object")
    return _dict_from_mapping(value)


def _optional_mapping(value: object) -> Mapping[object, object] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _setup_evidence_mapping(
    payload: LightSetupProfile | Mapping[str, object],
) -> dict[str, object]:
    if isinstance(payload, LightSetupProfile):
        return _dict_from_mapping(payload.setup_report)
    if payload.get("kind") == SETUP_PROFILE_KIND:
        return _mapping(payload, "setup_report")
    return _dict_from_mapping(payload)


def _setup_validation_ready_for(payload: Mapping[str, object]) -> dict[str, bool]:
    validation_ready = _bool_mapping(payload.get("validation_ready_for"))
    if validation_ready:
        return validation_ready
    summary = _optional_mapping(payload.get("summary"))
    if summary is None:
        return {}
    return _bool_mapping(summary.get("validation_ready_for"))


def _normalize_primitive(primitive: str) -> str:
    return primitive.strip().lower().replace("-", "_")


def _dict_from_mapping(payload: Mapping[object, object]) -> dict[str, object]:
    return {str(key): value for key, value in payload.items()}


def _bool_mapping(value: object) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item is True for key, item in value.items()}
