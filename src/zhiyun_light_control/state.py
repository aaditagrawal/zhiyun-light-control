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
    result_summaries: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["scene"] = self.scene.to_dict()
        data["result_statuses"] = list(self.result_statuses)
        data["result_summaries"] = [dict(item) for item in self.result_summaries]
        return data


class SceneStateTracker:
    def __init__(self, *, history_limit: int = 256) -> None:
        self._condition = threading.Condition(threading.Lock())
        self._state: SceneState | None = None
        self._version = 0
        self._history_limit = max(1, history_limit)
        self._history: list[tuple[int, SceneState]] = []

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
        summaries = tuple(_result_summary(result) for result in result_items)
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
            result_summaries=summaries,
        )
        with self._condition:
            self._state = state
            self._version += 1
            self._history.append((self._version, state))
            if len(self._history) > self._history_limit:
                del self._history[: len(self._history) - self._history_limit]
            self._condition.notify_all()
        return state

    def snapshot(self) -> SceneState | None:
        with self._condition:
            return self._state

    def versioned_snapshot(self) -> tuple[int, SceneState | None]:
        with self._condition:
            return self._version, self._state

    def wait_for_update(
        self,
        after_version: int,
        *,
        timeout: float | None = None,
    ) -> tuple[int, SceneState | None]:
        with self._condition:
            if self._version <= after_version:
                self._condition.wait_for(
                    lambda: self._version > after_version,
                    timeout=timeout,
                )
            return self._version, self._state

    def history(
        self,
        *,
        after_version: int = 0,
        limit: int | None = None,
    ) -> tuple[tuple[int, SceneState], ...]:
        with self._condition:
            items = tuple(
                item for item in self._history if item[0] > after_version
            )
        if limit is None:
            return items
        if limit <= 0:
            return ()
        return items[-limit:]

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


def _result_summary(result: object) -> dict[str, object]:
    converter = getattr(result, "to_dict", None)
    if callable(converter):
        payload = converter()
        if isinstance(payload, dict):
            return _normalized_result_summary(payload, fallback=result)
    if isinstance(result, dict):
        return _normalized_result_summary(result, fallback=result)
    summary: dict[str, object] = {
        "transport_status": _result_status(result),
        "acknowledged": _result_acknowledged(result),
    }
    command = getattr(result, "command", None)
    if isinstance(command, int):
        summary["command"] = command
    return summary


def _normalized_result_summary(
    payload: dict[object, object],
    *,
    fallback: object,
) -> dict[str, object]:
    summary = {str(key): value for key, value in payload.items()}
    if "transport_status" not in summary:
        summary["transport_status"] = _result_status(fallback)
    if "acknowledged" not in summary:
        summary["acknowledged"] = _result_acknowledged(fallback)
    return summary


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
