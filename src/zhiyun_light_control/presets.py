"""Named scene presets for media-control integrations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import fields, replace
from pathlib import Path

from .models import Scene

SCENE_FIELDS = {field.name for field in fields(Scene)}


class PresetError(ValueError):
    pass


class ScenePresetLibrary:
    def __init__(self, scenes: Mapping[str, Scene]):
        self.scenes = dict(scenes)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> ScenePresetLibrary:
        raw_scenes = data.get("scenes", data)
        if not isinstance(raw_scenes, Mapping):
            raise PresetError("preset file must contain a mapping of scene names")
        scenes: dict[str, Scene] = {}
        for name, value in raw_scenes.items():
            if not isinstance(name, str):
                raise PresetError("preset names must be strings")
            if not isinstance(value, Mapping):
                raise PresetError(f"preset {name!r} must be an object")
            scenes[name] = scene_from_mapping(value)
        return cls(scenes)

    @classmethod
    def load(cls, path: str | Path) -> ScenePresetLibrary:
        with Path(path).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, Mapping):
            raise PresetError("preset file must contain a JSON object")
        return cls.from_mapping(data)

    def names(self) -> list[str]:
        return sorted(self.scenes)

    def get(self, name: str) -> Scene:
        try:
            return self.scenes[name]
        except KeyError as exc:
            raise PresetError(f"unknown preset: {name}") from exc

    def to_dict(self) -> dict[str, object]:
        return {
            "scenes": {name: scene.to_dict() for name, scene in self.scenes.items()}
        }


def scene_from_mapping(data: Mapping[str, object]) -> Scene:
    unknown = set(data) - SCENE_FIELDS
    if unknown:
        raise PresetError(f"unknown scene fields: {', '.join(sorted(unknown))}")
    return Scene(
        obj=_optional_int(data, "obj", default=1),
        brightness=_optional_float(data, "brightness"),
        kelvin=_optional_int(data, "kelvin"),
        sleep=_optional_int(data, "sleep"),
        red=_optional_int(data, "red"),
        green=_optional_int(data, "green"),
        blue=_optional_int(data, "blue"),
        hue=_optional_float(data, "hue"),
        saturation=_optional_float(data, "saturation"),
        intensity=_optional_int(data, "intensity"),
    )


def merge_scene(base: Scene, overrides: Scene, *, override_obj: bool = False) -> Scene:
    changes = {
        name: value
        for name, value in overrides.to_dict().items()
        if value is not None and not (name == "obj" and value == 1 and not override_obj)
    }
    return replace(base, **changes)


def scene_from_optional_mapping(data: Mapping[str, object], *, obj: int = 1) -> Scene:
    scene = scene_from_mapping(data)
    if "obj" not in data:
        scene = replace(scene, obj=obj)
    return scene


def _optional_int(
    data: Mapping[str, object],
    key: str,
    *,
    default: int | None = None,
) -> int | None:
    value = data.get(key, default)
    return int(value) if value is not None else None


def _optional_float(data: Mapping[str, object], key: str) -> float | None:
    value = data.get(key)
    return float(value) if value is not None else None
