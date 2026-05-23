"""Scene transition planning for media-control cues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .models import Scene


EasingName = Literal["linear", "ease-in", "ease-out", "ease-in-out"]


@dataclass(frozen=True)
class SceneTransition:
    """A deterministic sequence of scene updates from one requested state to another."""

    start: Scene
    end: Scene
    steps: int = 10
    easing: EasingName = "linear"
    include_start: bool = False

    def scenes(self) -> tuple[Scene, ...]:
        return tuple(
            scene_transition(
                self.start,
                self.end,
                steps=self.steps,
                easing=self.easing,
                include_start=self.include_start,
            )
        )


def scene_transition(
    start: Scene,
    end: Scene,
    *,
    steps: int = 10,
    easing: EasingName = "linear",
    include_start: bool = False,
) -> tuple[Scene, ...]:
    """Return scene updates that end exactly at ``end``.

    ``steps`` is the number of generated control updates after the starting
    state. Unknown starting values are omitted until the final update so callers
    do not imply a physical value the light has not reported.
    """

    _validate_transition(start, end, steps, easing)
    scenes: list[Scene] = []
    if include_start:
        scenes.append(interpolate_scene(start, end, 0.0, easing=easing))
    for index in range(1, steps + 1):
        scenes.append(interpolate_scene(start, end, index / steps, easing=easing))
    return tuple(scene for scene in scenes if _has_control_fields(scene))


def interpolate_scene(
    start: Scene,
    end: Scene,
    fraction: float,
    *,
    easing: EasingName = "linear",
) -> Scene:
    """Interpolate the fields present in ``end`` at ``fraction``."""

    _validate_transition(start, end, 1, easing)
    position = _ease(_clamp(fraction), easing)
    return Scene(
        obj=end.obj,
        sleep=_final_only(end.sleep, position),
        brightness=_interpolate_float(start.brightness, end.brightness, position),
        kelvin=_interpolate_int(start.kelvin, end.kelvin, position),
        red=_interpolate_int(start.red, end.red, position),
        green=_interpolate_int(start.green, end.green, position),
        blue=_interpolate_int(start.blue, end.blue, position),
        hue=_interpolate_float(start.hue, end.hue, position),
        saturation=_interpolate_float(start.saturation, end.saturation, position),
        intensity=_interpolate_int(start.intensity, end.intensity, position),
    )


def transition_interval(duration: float, scene_count: int) -> float:
    """Return the delay between scene updates for a total transition duration."""

    if duration < 0:
        raise ValueError("duration must be non-negative")
    if scene_count <= 1:
        return 0.0
    return duration / (scene_count - 1)


def _validate_transition(
    start: Scene,
    end: Scene,
    steps: int,
    easing: str,
) -> None:
    if steps < 1:
        raise ValueError("steps must be at least 1")
    if start.obj != end.obj:
        raise ValueError("scene transitions must target one object id")
    if easing not in {"linear", "ease-in", "ease-out", "ease-in-out"}:
        raise ValueError(f"unknown easing: {easing}")
    _validate_complete_tuple(
        "RGB",
        (start.red, start.green, start.blue),
        (end.red, end.green, end.blue),
    )
    _validate_complete_tuple(
        "HSI",
        (start.hue, start.saturation, start.intensity),
        (end.hue, end.saturation, end.intensity),
    )


def _validate_complete_tuple(
    name: str,
    start_values: tuple[object | None, ...],
    end_values: tuple[object | None, ...],
) -> None:
    for values in (start_values, end_values):
        present = [value is not None for value in values]
        if any(present) and not all(present):
            raise ValueError(f"scene transition {name} requires a complete tuple")


def _interpolate_float(start: float | None, end: float | None, fraction: float) -> float | None:
    if end is None:
        return None
    if start is None:
        return end if fraction >= 1.0 else None
    return start + (end - start) * fraction


def _interpolate_int(start: int | None, end: int | None, fraction: float) -> int | None:
    value = _interpolate_float(
        float(start) if start is not None else None,
        float(end) if end is not None else None,
        fraction,
    )
    return int(round(value)) if value is not None else None


def _final_only(end: int | None, fraction: float) -> int | None:
    if end is None:
        return None
    return end if fraction >= 1.0 else None


def _ease(fraction: float, easing: str) -> float:
    if easing == "linear":
        return fraction
    if easing == "ease-in":
        return fraction * fraction
    if easing == "ease-out":
        inverse = 1.0 - fraction
        return 1.0 - inverse * inverse
    if fraction < 0.5:
        return 2.0 * fraction * fraction
    inverse = -2.0 * fraction + 2.0
    return 1.0 - inverse * inverse / 2.0


def _clamp(fraction: float) -> float:
    return max(0.0, min(1.0, float(fraction)))


def _has_control_fields(scene: Scene) -> bool:
    return any(value is not None for key, value in scene.to_dict().items() if key != "obj")
