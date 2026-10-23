"""Bridge-owned effects made only from verified brightness/CCT setters."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from . import programs
from .client import LightClient, LightState
from .protocol import validate_cct
from .transport import TransportError

if TYPE_CHECKING:
    from .bridge import Bridge

_PATTERNS = programs.PATTERNS
_WRITE_SPACING = 0.1  # Minimum seconds between write STARTS (at most 10 per second).
_IDLE_RESAMPLE = 1.0  # Longest wait between target checks, even for constant output.
_WAKE_LATENESS = 0.001  # Wake just after a scheduled change rather than just before it.


def _seconds(name: str, value: object, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number from {minimum:g} to {maximum:g} seconds")
    # Check bounds before conversion, including arbitrarily large JSON integers.
    if not minimum <= value <= maximum or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and from {minimum:g} to {maximum:g} seconds")
    return float(value)


def _brightness(value: object) -> float:
    # Avoid float conversion overflow on arbitrarily large JSON integers in
    # the protocol validator; invalid configuration must remain HTTP 400.
    return programs.brightness_value("Brightness", value)


@dataclass(frozen=True)
class EffectConfig:
    pattern: str
    duration: float | None = 10
    period: float = 4
    minBrightness: float = 0
    maxBrightness: float | None = None
    cct: int | None = None
    cctEnd: int = 6500
    restoreOnComplete: bool = True
    seed: int = 1
    curve: str = "linear"
    options: programs.Options = field(default_factory=programs.NoOptions)
    program: programs.Program | None = field(default=None, compare=False, repr=False)

    @classmethod
    def parse(cls, data: dict) -> EffectConfig:
        pattern = data.get("pattern")
        if not isinstance(pattern, str) or pattern not in _PATTERNS:
            raise ValueError(f"pattern must be one of {', '.join(_PATTERNS)}")
        programs.check_fields(pattern, data)
        low, high = programs.period_range(pattern)
        period = _seconds("period", data.get("period", 4), low, high)
        options = programs.parse_options(pattern, data, period)
        cyclic = programs.loops(pattern, options)
        natural = programs.natural_duration(options)
        if "duration" not in data:
            duration = natural if natural is not None else programs.default_duration(pattern)
        elif data["duration"] is None:
            if not cyclic:
                raise ValueError(f"{pattern} requires a finite duration unless it loops")
            duration = None
        else:
            duration = _seconds("duration", data["duration"], 1, 3600)
            if natural is not None:
                # A longer request still stops at the program's natural completion.
                duration = min(duration, natural)
        restore = data.get("restoreOnComplete", cyclic)
        if not isinstance(restore, bool):
            raise ValueError("restoreOnComplete must be a boolean")
        minimum = _brightness(data.get("minBrightness", 0))
        maximum = _brightness(data["maxBrightness"]) if "maxBrightness" in data else None
        if maximum is not None and minimum > maximum:
            raise ValueError("maxBrightness must be >= minBrightness")
        default_cct, default_end = programs.cct_defaults(pattern)
        seed = programs.integer("seed", data.get("seed", 1), 0, programs.SEED_MAX)
        curve = programs.choice(
            "curve", data.get("curve", programs.default_curve(pattern)), tuple(programs.CURVES)
        )
        return cls(
            pattern=pattern,
            duration=duration,
            period=period,
            minBrightness=minimum,
            maxBrightness=maximum,
            cct=validate_cct(data["cct"]) if "cct" in data else default_cct,
            cctEnd=validate_cct(data["cctEnd"]) if "cctEnd" in data else default_end,
            restoreOnComplete=restore,
            seed=seed,
            curve=curve,
            options=options,
        )

    def resolve(self, state: LightState) -> EffectConfig:
        if not state.connected or state.brightness is None or state.cct is None:
            raise TransportError("A verified connected light is required for effects")
        maximum = self.maxBrightness
        if maximum is None:
            maximum = state.brightness or 5
        if self.minBrightness > maximum:
            raise ValueError(
                "maxBrightness must be >= minBrightness (including its baseline default)"
            )
        cct = self.cct if self.cct is not None else state.cct
        levels = programs.Levels(
            self.minBrightness,
            maximum,
            cct,
            self.cctEnd,
            self.seed,
            programs.CURVES[self.curve],
        )
        program = programs.build(
            self.pattern,
            self.options,
            levels,
            period=self.period,
            duration=self.duration,
            baseline=(state.brightness, state.cct),
        )
        return replace(self, maxBrightness=maximum, cct=cct, program=program)

    def target(self, elapsed: float) -> tuple[int, int]:
        assert self.program is not None, "resolve() before sampling"
        final = self.duration is not None and elapsed >= self.duration
        if self.duration is not None and final:
            elapsed = self.duration
        return self.program.sample(elapsed, final)

    def next_change(self, elapsed: float) -> float | None:
        assert self.program is not None, "resolve() before sampling"
        return self.program.next_change(elapsed)


@dataclass
class _Run:
    config: EffectConfig
    client: LightClient
    baseline: tuple[float, int]
    id: str
    started: float
    started_at: datetime
    task: asyncio.Task | None = None
    running: bool = True
    cancelled: bool = False
    elapsed: float = 0


class EffectEngine:
    """One runner per bridge, with cancellation drained before device mutation.

    Control operations take control_lock, drain the runner WITHOUT Bridge.lock,
    then take Bridge.lock for device I/O. The runner takes only Bridge.lock.
    Its task is registered as owned device work, including across cancellation.
    """

    def __init__(self, bridge: Bridge):
        self.bridge = bridge
        self.control_lock = asyncio.Lock()
        self._run: _Run | None = None
        self._last_error: str | None = None
        self._next_output = 0.0

    def status(self) -> dict:
        run = self._run
        progress = None
        if run is not None and run.config.duration is not None:
            elapsed = (
                asyncio.get_running_loop().time() - run.started if run.running else run.elapsed
            )
            progress = min(1, max(0, elapsed / run.config.duration))
        return {
            "running": run.running if run else False,
            "id": run.id if run else None,
            "pattern": run.config.pattern if run else None,
            "startedAt": run.started_at.isoformat() if run else None,
            "endsAt": (
                (run.started_at + timedelta(seconds=run.config.duration)).isoformat()
                if run and run.config.duration is not None
                else None
            ),
            "progress": progress,
            "lastError": self._last_error,
        }

    def cancel(self):
        """Synchronous invalidation also guards backends that swallow cancellation."""
        run = self._run
        if run is not None:
            run.cancelled = True
            if run.task is not None and not run.task.done() and not run.task.cancelling():
                run.task.cancel()

    async def start(self, data: dict) -> dict:
        config = EffectConfig.parse(data)
        async with self.control_lock:
            # Resolve defaults/validate against actual readback before stopping an
            # existing effect. Invalid configuration never causes restoration.
            async with self.bridge.lock:
                self.bridge.require_running()
                client = self.bridge.client
                if client is None:
                    raise TransportError("Connect to a light first")
                baseline = await client.refresh()
                resolved = config.resolve(baseline)
            replacing = self._run is not None and self._run.running
            await self.stop_locked(restore=False)
            async with self.bridge.lock:
                self.bridge.require_running()
                if replacing:
                    # A cancelled in-flight setter may have changed output since
                    # preflight; capture the replacement's own verified baseline.
                    baseline = await client.refresh()
                    resolved = config.resolve(baseline)
                assert baseline.brightness is not None and baseline.cct is not None
                run = _Run(
                    resolved,
                    client,
                    (baseline.brightness, baseline.cct),
                    uuid4().hex,
                    asyncio.get_running_loop().time(),
                    datetime.now(UTC),
                )
                self._run = run
                self._last_error = None
                run.task = self.bridge.own_device_task(self._runner(run), "Bridge effect")

                def finished(task):
                    # Cancellation before the coroutine's first turn bypasses
                    # its finally block, but must still produce a stopped status.
                    run.elapsed = max(0, asyncio.get_running_loop().time() - run.started)
                    run.running = False

                run.task.add_done_callback(finished)
                self.bridge.publish()
                return self.status()

    async def stop(self, restore: bool = True) -> dict:
        async with self.control_lock:
            return await self.stop_locked(restore=restore)

    async def stop_locked(self, *, restore: bool) -> dict:
        """Caller owns control_lock; never call with Bridge.lock held."""
        self.bridge.require_running()
        run = self._run
        if run is None or run.task is None or run.task.done():
            return self.status()
        self.cancel()
        # wait() does not transfer caller cancellation to the owned runner. A
        # cancelled HTTP caller cannot discard a cancellation-resistant setter.
        await asyncio.wait((run.task,))
        if restore:
            try:
                await self._output(run, restore=True, allow_cancelled=True)
            except Exception as exc:
                self._last_error = str(exc) or type(exc).__name__
                self.bridge.publish()
                raise
        return self.status()

    def _check_output(self, run: _Run, *, allow_cancelled=False):
        self.bridge.require_running()
        if (
            self._run is not run
            or self.bridge.client is not run.client
            or (run.cancelled and not allow_cancelled)
        ):
            raise asyncio.CancelledError

    async def _output(self, run: _Run, *, restore=False, allow_cancelled=False) -> float:
        while True:
            async with self.bridge.lock:
                self._check_output(run, allow_cancelled=allow_cancelled)
                now = asyncio.get_running_loop().time()
                elapsed = max(0, now - run.started)
                target = run.baseline if restore else run.config.target(elapsed)
                # A cancelled setter can reach hardware before its ACK/readback,
                # leaving the cached snapshot equal to baseline. Restoration is
                # always an absolute set of BOTH fields followed by verification.
                brightness = (
                    target[0] if restore or run.client.state.brightness != target[0] else None
                )
                cct = target[1] if restore or run.client.state.cct != target[1] else None
                if brightness is None and cct is None:
                    return elapsed
                delay = self._next_output - now
                if delay <= 0:
                    # Spacing is measured between write starts. No cached target
                    # survives a lock wait or rate-limit sleep: the target above was
                    # sampled now, so missed targets are skipped rather than replayed.
                    # LightClient.set serializes its writes and verifies readback.
                    self._next_output = now + _WRITE_SPACING
                    await run.client.set(
                        brightness=brightness,
                        cct=cct,
                        _guard=lambda: self._check_output(run, allow_cancelled=allow_cancelled),
                    )
                    self.bridge.publish()
                    return elapsed
            await asyncio.sleep(delay)

    async def _runner(self, run: _Run):
        loop = asyncio.get_running_loop()
        try:
            while not run.cancelled:
                elapsed = await self._output(run)
                duration = run.config.duration
                if duration is not None and elapsed >= duration:
                    if run.config.restoreOnComplete:
                        await self._output(run, restore=True)
                    return
                # Sleep until the program's next possible change. Constant output
                # is still rechecked periodically, without writing identical targets.
                wake = run.config.next_change(elapsed)
                limit = elapsed + _IDLE_RESAMPLE
                wake = limit if wake is None else min(max(wake, elapsed), limit)
                if duration is not None:
                    wake = min(wake, duration)
                await asyncio.sleep(max(0.0, run.started + wake + _WAKE_LATENESS - loop.time()))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = str(exc) or type(exc).__name__
            self.bridge.publish()
        finally:
            run.elapsed = max(0, asyncio.get_running_loop().time() - run.started)
            run.running = False
