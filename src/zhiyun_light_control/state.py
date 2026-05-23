"""In-memory state tracking for bridge integrations."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass

from .models import Scene


@dataclass(frozen=True)
class SceneState:
    scene: Scene
    source: str
    action: str
    updated_at: float
    applied: bool | None = None
    reason: str | None = None
    result_statuses: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["scene"] = self.scene.to_dict()
        data["result_statuses"] = list(self.result_statuses)
        return data


class SceneStateTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: SceneState | None = None

    def record(
        self,
        scene: Scene,
        *,
        source: str,
        action: str,
        applied: bool | None = None,
        reason: str | None = None,
        results: list[object] | tuple[object, ...] = (),
    ) -> SceneState:
        statuses = tuple(_result_status(result) for result in results)
        state = SceneState(
            scene=scene,
            source=source,
            action=action,
            updated_at=time.time(),
            applied=applied,
            reason=reason,
            result_statuses=statuses,
        )
        with self._lock:
            self._state = state
        return state

    def snapshot(self) -> SceneState | None:
        with self._lock:
            return self._state

    def to_dict(self) -> dict[str, object]:
        state = self.snapshot()
        return {"scene": None} if state is None else state.to_dict()


def _result_status(result: object) -> str:
    status = getattr(result, "transport_status", None)
    if status is not None:
        return str(status)
    if isinstance(result, dict) and "transport_status" in result:
        return str(result["transport_status"])
    return "unknown"
