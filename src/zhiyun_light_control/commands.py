"""Transport-neutral runtime command planning helpers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from inspect import isawaitable

from .models import (
    CommandResult,
    Scene,
    flatten_command_batches,
    require_command_results,
)
from .protocol import (
    DEFAULT_CONTROL_MODE,
    RUNTIME_TYPE,
    RuntimeCommand,
    brightness_payload,
    build_frame,
    cct_payload,
    first_response_frame,
    hsi_payload,
    iter_frames,
    rgb_payload,
    sleep_payload,
)
from .transitions import EasingName, scene_transition, transition_interval


@dataclass(frozen=True)
class RuntimeCommandSpec:
    """A single runtime command that can be sent over USB or BLE."""

    name: str
    command: int
    payload: bytes
    object_id: int | None = None
    fields: tuple[str, ...] = ()
    requires_control: bool = True

    @property
    def command_hex(self) -> str:
        return f"0x{self.command:04x}"

    @property
    def payload_hex(self) -> str:
        return self.payload.hex()

    def frame(
        self,
        *,
        seq: int,
        first_word: int = RUNTIME_TYPE,
    ) -> bytes:
        return build_frame(first_word, seq, self.command, self.payload)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "command": self.command,
            "command_hex": self.command_hex,
            "payload_hex": self.payload_hex,
            "object_id": self.object_id,
            "fields": list(self.fields),
            "requires_control": self.requires_control,
        }


@dataclass(frozen=True)
class RuntimeFrameSpec:
    """A planned serialized frame for one runtime command."""

    command: RuntimeCommandSpec
    seq: int
    first_word: int
    frame: bytes

    @property
    def frame_hex(self) -> str:
        return self.frame.hex()

    def to_dict(self) -> dict[str, object]:
        return {
            **self.command.to_dict(),
            "seq": self.seq,
            "first_word": self.first_word,
            "first_word_hex": f"0x{self.first_word:04x}",
            "frame_hex": self.frame_hex,
        }


@dataclass(frozen=True)
class SceneCommandPlan:
    """A scene plus ordered runtime commands and optional serialized frames."""

    scene: Scene
    commands: tuple[RuntimeCommandSpec, ...]
    frames: tuple[RuntimeFrameSpec, ...]
    start_seq: int

    @property
    def next_seq(self) -> int:
        return self.start_seq + len(self.frames)

    def to_dict(self) -> dict[str, object]:
        return {
            "scene": self.scene.to_dict(),
            "start_seq": self.start_seq,
            "next_seq": self.next_seq,
            "commands": [command.to_dict() for command in self.commands],
            "frames": [frame.to_dict() for frame in self.frames],
        }


def scene_command_specs(
    scene: Scene,
    *,
    control_mode: int = DEFAULT_CONTROL_MODE,
) -> tuple[RuntimeCommandSpec, ...]:
    """Return ordered runtime commands needed to apply a scene."""

    specs: list[RuntimeCommandSpec] = []
    if scene.sleep is not None:
        specs.append(
            RuntimeCommandSpec(
                name="sleep",
                command=RuntimeCommand.SLEEP,
                payload=sleep_payload(
                    scene.obj,
                    scene.sleep,
                    read=False,
                    control_mode=control_mode,
                ),
                object_id=scene.obj,
                fields=("sleep",),
            )
        )
    if scene.brightness is not None:
        specs.append(
            RuntimeCommandSpec(
                name="brightness",
                command=RuntimeCommand.BRIGHTNESS,
                payload=brightness_payload(
                    scene.obj,
                    scene.brightness,
                    read=False,
                    control_mode=control_mode,
                ),
                object_id=scene.obj,
                fields=("brightness",),
            )
        )
    if scene.kelvin is not None:
        specs.append(
            RuntimeCommandSpec(
                name="cct",
                command=RuntimeCommand.CCT,
                payload=cct_payload(
                    scene.obj,
                    scene.kelvin,
                    read=False,
                    control_mode=control_mode,
                ),
                object_id=scene.obj,
                fields=("kelvin",),
            )
        )
    if scene.red is not None or scene.green is not None or scene.blue is not None:
        if scene.red is None or scene.green is None or scene.blue is None:
            raise ValueError("scene RGB requires red, green, and blue")
        specs.append(
            RuntimeCommandSpec(
                name="rgb",
                command=RuntimeCommand.RGB,
                payload=rgb_payload(
                    scene.obj,
                    scene.red,
                    scene.green,
                    scene.blue,
                    control_mode=control_mode,
                ),
                object_id=scene.obj,
                fields=("red", "green", "blue"),
            )
        )
    if (
        scene.hue is not None
        or scene.saturation is not None
        or scene.intensity is not None
    ):
        if scene.hue is None or scene.saturation is None or scene.intensity is None:
            raise ValueError("scene HSI requires hue, saturation, and intensity")
        specs.append(
            RuntimeCommandSpec(
                name="hsi",
                command=RuntimeCommand.HSI,
                payload=hsi_payload(
                    scene.obj,
                    scene.hue,
                    scene.saturation,
                    scene.intensity,
                    control_mode=control_mode,
                ),
                object_id=scene.obj,
                fields=("hue", "saturation", "intensity"),
            )
        )
    return tuple(specs)


def scene_command_plan(
    scene: Scene,
    *,
    control_mode: int = DEFAULT_CONTROL_MODE,
    first_word: int = RUNTIME_TYPE,
    start_seq: int = 1,
) -> SceneCommandPlan:
    commands = scene_command_specs(scene, control_mode=control_mode)
    frames = tuple(
        RuntimeFrameSpec(
            command=command,
            seq=seq,
            first_word=first_word,
            frame=command.frame(seq=seq, first_word=first_word),
        )
        for seq, command in enumerate(commands, start=start_seq)
    )
    return SceneCommandPlan(
        scene=scene,
        commands=commands,
        frames=frames,
        start_seq=start_seq,
    )


def scene_frame_specs(
    scene: Scene,
    *,
    control_mode: int = DEFAULT_CONTROL_MODE,
    first_word: int = RUNTIME_TYPE,
    start_seq: int = 1,
) -> tuple[RuntimeFrameSpec, ...]:
    """Return ordered serialized runtime frames for a scene."""

    return scene_command_plan(
        scene,
        control_mode=control_mode,
        first_word=first_word,
        start_seq=start_seq,
    ).frames


def transition_command_plans(
    start: Scene,
    end: Scene,
    *,
    steps: int = 10,
    easing: EasingName = "linear",
    control_mode: int = DEFAULT_CONTROL_MODE,
    first_word: int = RUNTIME_TYPE,
    start_seq: int = 1,
) -> tuple[SceneCommandPlan, ...]:
    """Return per-scene command plans for a transition."""

    plans: list[SceneCommandPlan] = []
    next_seq = start_seq
    for scene in scene_transition(start, end, steps=steps, easing=easing):
        plan = scene_command_plan(
            scene,
            control_mode=control_mode,
            first_word=first_word,
            start_seq=next_seq,
        )
        plans.append(plan)
        next_seq = plan.next_seq
    return tuple(plans)


def execute_command_plan(
    light: object,
    plan: SceneCommandPlan,
    *,
    timeout: float | None = None,
) -> list[CommandResult]:
    """Execute a planned scene through a sync USB or sync BLE light object."""

    return [
        _exchange_runtime(light, command, timeout=timeout)
        for command in plan.commands
    ]


def execute_command_plan_confirmed(
    light: object,
    plan: SceneCommandPlan,
    *,
    timeout: float | None = None,
    action: str = "scene",
) -> list[CommandResult]:
    """Execute a planned scene and require matching ACK evidence."""

    return require_command_results(
        execute_command_plan(light, plan, timeout=timeout),
        action=action,
    )


def execute_transition_plans(
    light: object,
    plans: Iterable[SceneCommandPlan],
    *,
    duration: float = 0.0,
    timeout: float | None = None,
) -> list[list[CommandResult]]:
    """Execute per-scene transition plans through a sync light object."""

    plan_items = tuple(plans)
    delay = transition_interval(duration, len(plan_items))
    batches: list[list[CommandResult]] = []
    for index, plan in enumerate(plan_items):
        batches.append(execute_command_plan(light, plan, timeout=timeout))
        if delay > 0 and index < len(plan_items) - 1:
            time.sleep(delay)
    return batches


def execute_transition_plans_confirmed(
    light: object,
    plans: Iterable[SceneCommandPlan],
    *,
    duration: float = 0.0,
    timeout: float | None = None,
    action: str = "transition",
) -> list[list[CommandResult]]:
    """Execute transition plans and require matching ACK evidence."""

    batches = execute_transition_plans(
        light,
        plans,
        duration=duration,
        timeout=timeout,
    )
    require_command_results(flatten_command_batches(batches), action=action)
    return batches


def execute_frame_plan(
    light: object,
    plan: SceneCommandPlan,
    *,
    timeout: float | None = None,
) -> list[CommandResult]:
    """Execute a planned scene using its exact serialized frames."""

    return [
        _exchange_prebuilt_frame(light, frame, timeout=timeout)
        for frame in plan.frames
    ]


def execute_serialized_frame_plan(
    light: object,
    plan: Mapping[str, object],
    *,
    timeout: float | None = None,
) -> list[CommandResult]:
    """Execute exact frame bytes from a serialized SDK plan dictionary."""

    results: list[CommandResult] = []
    _execute_serialized_plan(
        light,
        plan,
        results=results,
        timeout=timeout,
    )
    return results


def _execute_serialized_plan(
    light: object,
    plan: Mapping[str, object],
    *,
    results: list[CommandResult],
    timeout: float | None,
) -> None:
    nested = plan.get("command_plan")
    if isinstance(nested, Mapping):
        _execute_serialized_plan(
            light,
            nested,
            results=results,
            timeout=timeout,
        )
        return

    raw_frames = plan.get("frames")
    if isinstance(raw_frames, list | tuple):
        results.extend(
            _exchange_prebuilt_frame_bytes(
                light,
                frame,
                command,
                timeout=timeout,
            )
            for frame, command in _serialized_frame_entries(raw_frames)
        )
        return

    raw_batches = plan.get("command_batches")
    if isinstance(raw_batches, list | tuple):
        delay = _serialized_transition_interval(plan, len(raw_batches))
        for index, batch in enumerate(_mapping_items(raw_batches, "command batch")):
            _execute_serialized_plan(
                light,
                batch,
                results=results,
                timeout=timeout,
            )
            if delay > 0 and index < len(raw_batches) - 1:
                time.sleep(delay)
        return

    raw_steps = plan.get("steps")
    if isinstance(raw_steps, list | tuple):
        for step in _mapping_items(raw_steps, "step"):
            _execute_serialized_plan(
                light,
                step,
                results=results,
                timeout=timeout,
            )
        return

    raise ValueError(
        "serialized plan must contain command_plan, frames, command_batches, or steps"
    )


def execute_frame_plan_confirmed(
    light: object,
    plan: SceneCommandPlan,
    *,
    timeout: float | None = None,
    action: str = "scene frame plan",
) -> list[CommandResult]:
    """Execute exact serialized scene frames and require ACK evidence."""

    return require_command_results(
        execute_frame_plan(light, plan, timeout=timeout),
        action=action,
    )


def execute_serialized_frame_plan_confirmed(
    light: object,
    plan: Mapping[str, object],
    *,
    timeout: float | None = None,
    action: str = "serialized frame plan",
) -> list[CommandResult]:
    """Execute serialized frame bytes and require ACK evidence."""

    return require_command_results(
        execute_serialized_frame_plan(light, plan, timeout=timeout),
        action=action,
    )


def execute_transition_frame_plans(
    light: object,
    plans: Iterable[SceneCommandPlan],
    *,
    duration: float = 0.0,
    timeout: float | None = None,
) -> list[list[CommandResult]]:
    """Execute transition plans using exact serialized frames."""

    plan_items = tuple(plans)
    delay = transition_interval(duration, len(plan_items))
    batches: list[list[CommandResult]] = []
    for index, plan in enumerate(plan_items):
        batches.append(execute_frame_plan(light, plan, timeout=timeout))
        if delay > 0 and index < len(plan_items) - 1:
            time.sleep(delay)
    return batches


def execute_transition_frame_plans_confirmed(
    light: object,
    plans: Iterable[SceneCommandPlan],
    *,
    duration: float = 0.0,
    timeout: float | None = None,
    action: str = "transition frame plan",
) -> list[list[CommandResult]]:
    """Execute exact serialized transition frames and require ACK evidence."""

    batches = execute_transition_frame_plans(
        light,
        plans,
        duration=duration,
        timeout=timeout,
    )
    require_command_results(flatten_command_batches(batches), action=action)
    return batches


async def execute_async_command_plan(
    light: object,
    plan: SceneCommandPlan,
    *,
    timeout: float | None = None,
) -> list[CommandResult]:
    """Execute a planned scene through an async BLE light object."""

    results: list[CommandResult] = []
    for command in plan.commands:
        results.append(
            await _exchange_runtime_async(light, command, timeout=timeout)
        )
    return results


async def execute_async_command_plan_confirmed(
    light: object,
    plan: SceneCommandPlan,
    *,
    timeout: float | None = None,
    action: str = "scene",
) -> list[CommandResult]:
    """Execute an async planned scene and require matching ACK evidence."""

    return require_command_results(
        await execute_async_command_plan(light, plan, timeout=timeout),
        action=action,
    )


async def execute_async_transition_plans(
    light: object,
    plans: Iterable[SceneCommandPlan],
    *,
    duration: float = 0.0,
    timeout: float | None = None,
) -> list[list[CommandResult]]:
    """Execute per-scene transition plans through an async light object."""

    plan_items = tuple(plans)
    delay = transition_interval(duration, len(plan_items))
    batches: list[list[CommandResult]] = []
    for index, plan in enumerate(plan_items):
        batches.append(await execute_async_command_plan(light, plan, timeout=timeout))
        if delay > 0 and index < len(plan_items) - 1:
            await asyncio.sleep(delay)
    return batches


async def execute_async_transition_plans_confirmed(
    light: object,
    plans: Iterable[SceneCommandPlan],
    *,
    duration: float = 0.0,
    timeout: float | None = None,
    action: str = "transition",
) -> list[list[CommandResult]]:
    """Execute async transition plans and require matching ACK evidence."""

    batches = await execute_async_transition_plans(
        light,
        plans,
        duration=duration,
        timeout=timeout,
    )
    require_command_results(flatten_command_batches(batches), action=action)
    return batches


async def execute_async_frame_plan(
    light: object,
    plan: SceneCommandPlan,
    *,
    timeout: float | None = None,
) -> list[CommandResult]:
    """Execute a planned scene using exact serialized frames asynchronously."""

    results: list[CommandResult] = []
    for frame in plan.frames:
        results.append(
            await _exchange_prebuilt_frame_async(light, frame, timeout=timeout)
        )
    return results


async def execute_async_serialized_frame_plan(
    light: object,
    plan: Mapping[str, object],
    *,
    timeout: float | None = None,
) -> list[CommandResult]:
    """Execute exact frame bytes from a serialized SDK plan asynchronously."""

    results: list[CommandResult] = []
    await _execute_serialized_plan_async(
        light,
        plan,
        results=results,
        timeout=timeout,
    )
    return results


async def _execute_serialized_plan_async(
    light: object,
    plan: Mapping[str, object],
    *,
    results: list[CommandResult],
    timeout: float | None,
) -> None:
    nested = plan.get("command_plan")
    if isinstance(nested, Mapping):
        await _execute_serialized_plan_async(
            light,
            nested,
            results=results,
            timeout=timeout,
        )
        return

    raw_frames = plan.get("frames")
    if isinstance(raw_frames, list | tuple):
        for frame, command in _serialized_frame_entries(raw_frames):
            results.append(
                await _exchange_prebuilt_frame_bytes_async(
                    light,
                    frame,
                    command,
                    timeout=timeout,
                )
            )
        return

    raw_batches = plan.get("command_batches")
    if isinstance(raw_batches, list | tuple):
        delay = _serialized_transition_interval(plan, len(raw_batches))
        for index, batch in enumerate(_mapping_items(raw_batches, "command batch")):
            await _execute_serialized_plan_async(
                light,
                batch,
                results=results,
                timeout=timeout,
            )
            if delay > 0 and index < len(raw_batches) - 1:
                await asyncio.sleep(delay)
        return

    raw_steps = plan.get("steps")
    if isinstance(raw_steps, list | tuple):
        for step in _mapping_items(raw_steps, "step"):
            await _execute_serialized_plan_async(
                light,
                step,
                results=results,
                timeout=timeout,
            )
        return

    raise ValueError(
        "serialized plan must contain command_plan, frames, command_batches, or steps"
    )


async def execute_async_frame_plan_confirmed(
    light: object,
    plan: SceneCommandPlan,
    *,
    timeout: float | None = None,
    action: str = "scene frame plan",
) -> list[CommandResult]:
    """Execute exact serialized async scene frames and require ACK evidence."""

    return require_command_results(
        await execute_async_frame_plan(light, plan, timeout=timeout),
        action=action,
    )


async def execute_async_serialized_frame_plan_confirmed(
    light: object,
    plan: Mapping[str, object],
    *,
    timeout: float | None = None,
    action: str = "serialized frame plan",
) -> list[CommandResult]:
    """Execute serialized frame bytes asynchronously and require ACK evidence."""

    return require_command_results(
        await execute_async_serialized_frame_plan(light, plan, timeout=timeout),
        action=action,
    )


async def execute_async_transition_frame_plans(
    light: object,
    plans: Iterable[SceneCommandPlan],
    *,
    duration: float = 0.0,
    timeout: float | None = None,
) -> list[list[CommandResult]]:
    """Execute async transition plans using exact serialized frames."""

    plan_items = tuple(plans)
    delay = transition_interval(duration, len(plan_items))
    batches: list[list[CommandResult]] = []
    for index, plan in enumerate(plan_items):
        batches.append(await execute_async_frame_plan(light, plan, timeout=timeout))
        if delay > 0 and index < len(plan_items) - 1:
            await asyncio.sleep(delay)
    return batches


async def execute_async_transition_frame_plans_confirmed(
    light: object,
    plans: Iterable[SceneCommandPlan],
    *,
    duration: float = 0.0,
    timeout: float | None = None,
    action: str = "transition frame plan",
) -> list[list[CommandResult]]:
    """Execute exact serialized async transition frames and require ACK evidence."""

    batches = await execute_async_transition_frame_plans(
        light,
        plans,
        duration=duration,
        timeout=timeout,
    )
    require_command_results(flatten_command_batches(batches), action=action)
    return batches


def _exchange_runtime(
    light: object,
    command: RuntimeCommandSpec,
    *,
    timeout: float | None,
) -> CommandResult:
    exchange = getattr(light, "exchange_runtime", None)
    if not callable(exchange):
        raise TypeError("light must expose exchange_runtime(command, payload)")
    if timeout is None:
        return exchange(command.command, command.payload)
    return exchange(command.command, command.payload, timeout=timeout)


async def _exchange_runtime_async(
    light: object,
    command: RuntimeCommandSpec,
    *,
    timeout: float | None,
) -> CommandResult:
    exchange = getattr(light, "exchange_runtime", None)
    if not callable(exchange):
        raise TypeError("light must expose exchange_runtime(command, payload)")
    if timeout is None:
        result = exchange(command.command, command.payload)
    else:
        result = exchange(command.command, command.payload, timeout=timeout)
    if isawaitable(result):
        return await result
    return result


def _exchange_prebuilt_frame_bytes(
    light: object,
    frame: bytes,
    command: int,
    *,
    timeout: float | None,
) -> CommandResult:
    exchange_frame = getattr(light, "exchange_prebuilt_frame", None)
    if callable(exchange_frame):
        if timeout is None:
            return exchange_frame(frame, command)
        return exchange_frame(frame, command, timeout=timeout)
    exchange = _transport_exchange(light)
    rx = exchange(frame) if timeout is None else exchange(frame, timeout=timeout)
    return _command_result_from_frame(frame, rx, command)


def _exchange_prebuilt_frame(
    light: object,
    frame: RuntimeFrameSpec,
    *,
    timeout: float | None,
) -> CommandResult:
    return _exchange_prebuilt_frame_bytes(
        light,
        frame.frame,
        frame.command.command,
        timeout=timeout,
    )


async def _exchange_prebuilt_frame_async(
    light: object,
    frame: RuntimeFrameSpec,
    *,
    timeout: float | None,
) -> CommandResult:
    return await _exchange_prebuilt_frame_bytes_async(
        light,
        frame.frame,
        frame.command.command,
        timeout=timeout,
    )


async def _exchange_prebuilt_frame_bytes_async(
    light: object,
    frame: bytes,
    command: int,
    *,
    timeout: float | None,
) -> CommandResult:
    exchange_frame = getattr(light, "exchange_prebuilt_frame", None)
    if callable(exchange_frame):
        if timeout is None:
            result = exchange_frame(frame, command)
        else:
            result = exchange_frame(frame, command, timeout=timeout)
        if isawaitable(result):
            return await result
        return result
    exchange = _transport_exchange(light)
    result = exchange(frame) if timeout is None else exchange(frame, timeout=timeout)
    rx = await result if isawaitable(result) else result
    return _command_result_from_frame(frame, rx, command)


def serialized_frame_commands(
    plan: Mapping[str, object],
) -> tuple[tuple[bytes, int], ...]:
    """Extract ``(frame, command)`` entries from a serialized SDK command plan."""

    return tuple(_iter_serialized_frame_commands(plan))


def _iter_serialized_frame_commands(
    plan: Mapping[str, object],
) -> Iterable[tuple[bytes, int]]:
    nested = plan.get("command_plan")
    if isinstance(nested, Mapping):
        yield from _iter_serialized_frame_commands(nested)
        return

    raw_frames = plan.get("frames")
    if isinstance(raw_frames, list | tuple):
        yield from _serialized_frame_entries(raw_frames)
        return

    raw_batches = plan.get("command_batches")
    if isinstance(raw_batches, list | tuple):
        for batch in _mapping_items(raw_batches, "command batch"):
            yield from _iter_serialized_frame_commands(batch)
        return

    raw_steps = plan.get("steps")
    if isinstance(raw_steps, list | tuple):
        for step in _mapping_items(raw_steps, "step"):
            yield from _iter_serialized_frame_commands(step)
        return

    raise ValueError(
        "serialized plan must contain command_plan, frames, command_batches, or steps"
    )


def _serialized_frame_entries(
    raw_frames: Iterable[object],
) -> tuple[tuple[bytes, int], ...]:
    frames: list[tuple[bytes, int]] = []
    for index, raw_frame in enumerate(raw_frames):
        if not isinstance(raw_frame, Mapping):
            raise ValueError(f"frame {index} must be an object")
        frame_hex = raw_frame.get("frame_hex")
        command = raw_frame.get("command")
        if not isinstance(frame_hex, str) or not frame_hex:
            raise ValueError(f"frame {index} must contain frame_hex")
        if not isinstance(command, int):
            raise ValueError(f"frame {index} must contain integer command")
        try:
            frame = bytes.fromhex(frame_hex)
        except ValueError as exc:
            raise ValueError(f"frame {index} has invalid frame_hex") from exc
        frames.append((frame, command))
    return tuple(frames)


def _mapping_items(
    items: Iterable[object],
    label: str,
) -> tuple[Mapping[str, object], ...]:
    mappings: list[Mapping[str, object]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} {index} must be an object")
        mappings.append(item)
    return tuple(mappings)


def _serialized_transition_interval(
    plan: Mapping[str, object],
    batches: int,
) -> float:
    raw_duration = plan.get("duration", 0.0)
    duration = raw_duration if isinstance(raw_duration, int | float) else 0.0
    return transition_interval(float(duration), batches)


def _transport_exchange(light: object):
    transport = getattr(light, "transport", None)
    exchange = getattr(transport, "exchange", None)
    if not callable(exchange):
        raise TypeError(
            "light must expose exchange_prebuilt_frame or transport.exchange"
        )
    return exchange


def _command_result_from_frame(
    tx: bytes,
    rx: bytes,
    command: int,
) -> CommandResult:
    frames = tuple(iter_frames(rx))
    return CommandResult(
        command=command & 0xFFFF,
        tx=tx,
        rx=rx,
        frames=frames,
        ack=first_response_frame(rx, tx=tx, cmd=command),
    )
