"""Project-level SDK helpers for media-control show directories."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .bridge import LightFactory
from .controller import AsyncLightFactory
from .cues import CueLibrary
from .presets import ScenePresetLibrary
from .rig import (
    AsyncLightRig,
    LightRig,
    async_rig_from_mapping,
    load_async_rig,
    load_rig,
    rig_from_mapping,
    save_rig,
)

LIGHT_PROJECT_KIND = "light-project"
LIGHT_PROJECT_SCHEMA_VERSION = 1
DEFAULT_PROJECT_FILE = "project.json"
DEFAULT_RIG_FILE = "rig.json"
DEFAULT_PRESETS_FILE = "scenes.json"
DEFAULT_CUES_FILE = "cues.json"


class LightProjectError(ValueError):
    pass


@dataclass(frozen=True)
class LightProject:
    """A reusable show/project bundle containing rig, presets, and cues."""

    rig_mapping: dict[str, object]
    preset_library: ScenePresetLibrary | None = None
    cue_library: CueLibrary | None = None
    source_path: str | None = None
    root_path: str | None = None
    rig_path: str | None = None
    presets_path: str | None = None
    cues_path: str | None = None

    @classmethod
    def load(cls, path: str | Path) -> LightProject:
        return load_light_project(path)

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
        *,
        base_dir: str | Path | None = None,
    ) -> LightProject:
        return light_project_from_mapping(payload, base_dir=base_dir)

    def fixture_names(self, *, tag: str | None = None) -> tuple[str, ...]:
        return self.to_rig().fixture_names(tag=tag)

    def preset_names(self) -> list[str]:
        library = self._preset_library()
        if library is None:
            rig = self.to_rig()
            library = rig.preset_library
        return [] if library is None else library.names()

    def cue_names(self) -> list[str]:
        library = self._cue_library()
        if library is None:
            rig = self.to_rig()
            library = rig.cue_library
        return [] if library is None else library.names()

    def to_rig(
        self,
        *,
        light_factories: Mapping[str, LightFactory] | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile_controls: bool | None = None,
    ) -> LightRig:
        presets = self._preset_library(preset_library)
        cues = self._cue_library(cue_library)
        if self.rig_path is not None:
            return load_rig(
                self.rig_path,
                light_factories=light_factories,
                preset_library=presets,
                cue_library=cues,
                control_mode=control_mode,
                require_acknowledged=require_acknowledged,
                require_setup_profile_controls=require_setup_profile_controls,
            )
        return rig_from_mapping(
            self.rig_mapping,
            light_factories=light_factories,
            preset_library=presets,
            cue_library=cues,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
            require_setup_profile_controls=require_setup_profile_controls,
        )

    def to_async_rig(
        self,
        *,
        light_factories: Mapping[str, AsyncLightFactory] | None = None,
        preset_library: ScenePresetLibrary | None = None,
        cue_library: CueLibrary | None = None,
        control_mode: int | None = None,
        require_acknowledged: bool | None = None,
        require_setup_profile_controls: bool | None = None,
    ) -> AsyncLightRig:
        presets = self._preset_library(preset_library)
        cues = self._cue_library(cue_library)
        if self.rig_path is not None:
            return load_async_rig(
                self.rig_path,
                light_factories=light_factories,
                preset_library=presets,
                cue_library=cues,
                control_mode=control_mode,
                require_acknowledged=require_acknowledged,
                require_setup_profile_controls=require_setup_profile_controls,
            )
        return async_rig_from_mapping(
            self.rig_mapping,
            light_factories=light_factories,
            preset_library=presets,
            cue_library=cues,
            control_mode=control_mode,
            require_acknowledged=require_acknowledged,
            require_setup_profile_controls=require_setup_profile_controls,
        )

    def summary(self) -> dict[str, object]:
        return {
            "api": "zhiyun-light-control",
            "kind": LIGHT_PROJECT_KIND,
            "source_path": self.source_path,
            "root_path": self.root_path,
            "rig_path": self.rig_path,
            "presets_path": self.presets_path,
            "cues_path": self.cues_path,
            "fixtures": list(self.fixture_names()),
            "presets": self.preset_names(),
            "cues": self.cue_names(),
        }

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "api": "zhiyun-light-control",
            "kind": LIGHT_PROJECT_KIND,
            "schema_version": LIGHT_PROJECT_SCHEMA_VERSION,
        }
        root = Path(self.root_path) if self.root_path is not None else None
        if self.rig_path is None:
            data["rig"] = dict(self.rig_mapping)
        else:
            data["rig_path"] = _project_path(self.rig_path, root=root)
        if self.presets_path is not None:
            data["presets_path"] = _project_path(self.presets_path, root=root)
        elif self.preset_library is not None:
            data["presets"] = self.preset_library.to_dict()
        if self.cues_path is not None:
            data["cues_path"] = _project_path(self.cues_path, root=root)
        elif self.cue_library is not None:
            data["cues"] = self.cue_library.to_dict()
        return data

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def save(
        self,
        path: str | Path,
        *,
        rig_name: str = DEFAULT_RIG_FILE,
        presets_name: str = DEFAULT_PRESETS_FILE,
        cues_name: str = DEFAULT_CUES_FILE,
        indent: int | None = 2,
    ) -> dict[str, object]:
        return save_light_project(
            self,
            path,
            rig_name=rig_name,
            presets_name=presets_name,
            cues_name=cues_name,
            indent=indent,
        )

    def _preset_library(
        self,
        override: ScenePresetLibrary | None = None,
    ) -> ScenePresetLibrary | None:
        return self.preset_library if override is None else override

    def _cue_library(
        self,
        override: CueLibrary | None = None,
    ) -> CueLibrary | None:
        return self.cue_library if override is None else override


def load_light_project(path: str | Path) -> LightProject:
    requested = Path(path)
    if requested.is_dir():
        root = requested
        project_path = root / DEFAULT_PROJECT_FILE
        if project_path.exists():
            payload = _load_json_mapping(project_path)
            return light_project_from_mapping(
                payload,
                base_dir=root,
                source_path=project_path,
            )
        return light_project_from_mapping({}, base_dir=root, source_path=root)
    payload = _load_json_mapping(requested)
    return light_project_from_mapping(
        payload,
        base_dir=requested.parent,
        source_path=requested,
    )


def light_project_from_mapping(
    payload: Mapping[str, object],
    *,
    base_dir: str | Path | None = None,
    source_path: str | Path | None = None,
) -> LightProject:
    kind = str(payload.get("kind", LIGHT_PROJECT_KIND))
    if kind != LIGHT_PROJECT_KIND:
        raise LightProjectError(f"unsupported light project kind: {kind}")
    base = None if base_dir is None else Path(base_dir)
    rig_mapping, rig_path = _project_rig(payload, base)
    preset_library, presets_path = _project_presets(payload, base)
    cue_library, cues_path = _project_cues(payload, base)
    root = base if base is not None else _common_root(rig_path, presets_path, cues_path)
    return LightProject(
        rig_mapping=rig_mapping,
        preset_library=preset_library,
        cue_library=cue_library,
        source_path=None if source_path is None else str(Path(source_path)),
        root_path=None if root is None else str(root),
        rig_path=None if rig_path is None else str(rig_path),
        presets_path=None if presets_path is None else str(presets_path),
        cues_path=None if cues_path is None else str(cues_path),
    )


def light_project_to_json(
    project: LightProject | Mapping[str, object],
    *,
    indent: int | None = 2,
) -> str:
    payload = project.to_dict() if isinstance(project, LightProject) else dict(project)
    return json.dumps(payload, indent=indent, sort_keys=True)


def save_light_project(
    project: LightProject | Mapping[str, object],
    path: str | Path,
    *,
    rig_name: str = DEFAULT_RIG_FILE,
    presets_name: str = DEFAULT_PRESETS_FILE,
    cues_name: str = DEFAULT_CUES_FILE,
    indent: int | None = 2,
) -> dict[str, object]:
    resolved = (
        project
        if isinstance(project, LightProject)
        else light_project_from_mapping(project)
    )
    project_path = _project_file_path(path)
    root = project_path.parent
    root.mkdir(parents=True, exist_ok=True)
    rig_path = root / rig_name
    save_rig(resolved.rig_mapping, rig_path, indent=indent)

    data: dict[str, object] = {
        "api": "zhiyun-light-control",
        "kind": LIGHT_PROJECT_KIND,
        "schema_version": LIGHT_PROJECT_SCHEMA_VERSION,
        "rig_path": rig_name,
    }
    written: dict[str, str] = {
        "project_path": str(project_path),
        "rig_path": str(rig_path),
    }
    if resolved.preset_library is not None:
        presets_path = root / presets_name
        _write_json_mapping(resolved.preset_library.to_dict(), presets_path, indent)
        data["presets_path"] = presets_name
        written["presets_path"] = str(presets_path)
    if resolved.cue_library is not None:
        cues_path = root / cues_name
        _write_json_mapping(resolved.cue_library.to_dict(), cues_path, indent)
        data["cues_path"] = cues_name
        written["cues_path"] = str(cues_path)
    _write_json_mapping(data, project_path, indent)
    written["mapping"] = data
    return written


def _project_rig(
    payload: Mapping[str, object],
    base_dir: Path | None,
) -> tuple[dict[str, object], Path | None]:
    raw_rig = payload.get("rig")
    raw_path = payload.get("rig_path", payload.get("rig_file"))
    if isinstance(raw_rig, Mapping):
        return _string_key_dict(raw_rig), None
    if isinstance(raw_rig, str):
        raw_path = raw_rig
    path = _project_existing_path(raw_path, base_dir, DEFAULT_RIG_FILE)
    if path is None:
        raise LightProjectError("light project is missing rig or rig_path")
    return _load_json_mapping(path), path


def _project_presets(
    payload: Mapping[str, object],
    base_dir: Path | None,
) -> tuple[ScenePresetLibrary | None, Path | None]:
    raw_presets = payload.get("presets")
    if isinstance(raw_presets, Mapping):
        return ScenePresetLibrary.from_mapping(raw_presets), None
    raw_scenes = payload.get("scenes")
    if isinstance(raw_scenes, Mapping):
        return ScenePresetLibrary.from_mapping(raw_scenes), None
    raw_path = payload.get(
        "presets_path",
        payload.get("scenes_path", raw_presets),
    )
    path = _project_existing_path(raw_path, base_dir, DEFAULT_PRESETS_FILE)
    if path is None:
        return None, None
    return ScenePresetLibrary.load(path), path


def _project_cues(
    payload: Mapping[str, object],
    base_dir: Path | None,
) -> tuple[CueLibrary | None, Path | None]:
    raw_cues = payload.get("cues")
    if isinstance(raw_cues, Mapping):
        return CueLibrary.from_mapping(raw_cues), None
    raw_path = payload.get("cues_path", raw_cues)
    path = _project_existing_path(raw_path, base_dir, DEFAULT_CUES_FILE)
    if path is None:
        return None, None
    return CueLibrary.load(path), path


def _project_existing_path(
    raw: object,
    base_dir: Path | None,
    default_name: str,
) -> Path | None:
    if isinstance(raw, str) and raw.strip():
        return _resolve_project_path(raw, base_dir)
    if base_dir is None:
        return None
    default_path = base_dir / default_name
    return default_path if default_path.exists() else None


def _resolve_project_path(value: str, base_dir: Path | None) -> Path:
    path = Path(value)
    if path.is_absolute() or base_dir is None:
        return path
    return base_dir / path


def _project_file_path(path: str | Path) -> Path:
    target = Path(path)
    if target.suffix:
        return target
    return target / DEFAULT_PROJECT_FILE


def _load_json_mapping(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise LightProjectError(f"{path} must contain a JSON object")
    return _string_key_dict(payload)


def _write_json_mapping(
    payload: Mapping[str, object],
    path: Path,
    indent: int | None,
) -> None:
    path.write_text(
        f"{json.dumps(dict(payload), indent=indent, sort_keys=True)}\n",
        encoding="utf-8",
    )


def _string_key_dict(payload: Mapping[object, object]) -> dict[str, object]:
    return {str(key): value for key, value in payload.items()}


def _project_path(path: str, *, root: Path | None) -> str:
    resolved = Path(path)
    if root is None:
        return str(resolved)
    try:
        return str(resolved.relative_to(root))
    except ValueError:
        return str(resolved)


def _common_root(*paths: Path | None) -> Path | None:
    present = [path.parent for path in paths if path is not None]
    if not present:
        return None
    first = present[0]
    return first if all(path == first for path in present) else None
