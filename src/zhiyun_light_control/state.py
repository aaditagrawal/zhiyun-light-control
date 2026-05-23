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
        result_items = tuple(results)
        statuses = tuple(_result_status(result) for result in result_items)
        if applied is None and result_items:
            applied = results_confirmed(result_items)
        if reason is None and applied is False and result_items:
            reason = unconfirmed_results_reason(result_items)
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


def results_confirmed(results: list[object] | tuple[object, ...]) -> bool:
    return bool(results) and all(_result_acknowledged(result) for result in results)


def unconfirmed_results_reason(results: list[object] | tuple[object, ...]) -> str:
    if not results:
        return "no_commands"
    statuses = [_result_status(result) for result in results]
    if any(status == "sent_no_response" for status in statuses):
        return "sent_no_response"
    if any(status == "echoed_write" for status in statuses):
        return "echoed_write"
    return "unconfirmed"


def _result_acknowledged(result: object) -> bool:
    acknowledged = getattr(result, "acknowledged", None)
    if isinstance(acknowledged, bool):
        return acknowledged
    if isinstance(result, dict):
        value = result.get("acknowledged")
        if isinstance(value, bool):
            return value
    return _result_status(result) == "acknowledged"
