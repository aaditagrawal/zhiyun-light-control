"""Named cue sequences for bridge integrations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path


class CueError(ValueError):
    pass


class CueLibrary:
    def __init__(self, cues: Mapping[str, dict[str, object]]):
        self.cues = {name: dict(cue) for name, cue in cues.items()}

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> CueLibrary:
        raw_cues = data.get("cues", data)
        if not isinstance(raw_cues, Mapping):
            raise CueError("cue file must contain a mapping of cue names")
        cues: dict[str, dict[str, object]] = {}
        for name, value in raw_cues.items():
            if not isinstance(name, str):
                raise CueError("cue names must be strings")
            if not isinstance(value, Mapping):
                raise CueError(f"cue {name!r} must be an object")
            cues[name] = cue_from_mapping(value)
        return cls(cues)

    @classmethod
    def load(cls, path: str | Path) -> CueLibrary:
        with Path(path).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, Mapping):
            raise CueError("cue file must contain a JSON object")
        return cls.from_mapping(data)

    def names(self) -> list[str]:
        return sorted(self.cues)

    def get(self, name: str) -> dict[str, object]:
        try:
            return dict(self.cues[name])
        except KeyError as exc:
            raise CueError(f"unknown cue: {name}") from exc

    def to_dict(self) -> dict[str, object]:
        return {"cues": {name: dict(cue) for name, cue in self.cues.items()}}


def cue_from_mapping(data: Mapping[str, object]) -> dict[str, object]:
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise CueError("cue steps must be a non-empty array")
    normalized_steps: list[dict[str, object]] = []
    for step in steps:
        if not isinstance(step, Mapping):
            raise CueError("cue steps must be objects")
        normalized_steps.append(dict(step))
    cue: dict[str, object] = {"steps": normalized_steps}
    if "stop_on_unconfirmed" in data:
        cue["stop_on_unconfirmed"] = bool(data["stop_on_unconfirmed"])
    return cue
