"""Bridge-owned effects made only from verified brightness/CCT setters."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from .client import LightClient, LightState
from .protocol import validate_brightness, validate_cct
from .transport import TransportError

if TYPE_CHECKING:
    from .bridge import Bridge

_PATTERNS = ("blink", "breathe", "fade-in", "fade-out", "cct-sweep")
_FRAME_INTERVAL = 0.2


def _seconds(name: str, value: object, maximum: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number from 1 to {maximum} seconds")
    # Check bounds before conversion, including arbitrarily large JSON integers.
    if not 1 <= value <= maximum or not math.isfinite(value):
        raise ValueError(f"{name} must be finite and from 1 to {maximum} seconds")
    return float(value)


def _brightness(value: object) -> float:
    # Avoid float conversion overflow on arbitrarily large JSON integers in
    # the protocol validator; invalid configuration must remain HTTP 400.
    if isinstance(value, int) and not 0 <= value <= 100:
        raise ValueError("Brightness must be a whole percent from 0 to 100")
    return validate_brightness(value)


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

    @classmethod
    def parse(cls, data: dict) -> EffectConfig:
        if set(data) - set(cls.__dataclass_fields__):
            raise ValueError("Unknown effect property")
        pattern = data.get("pattern")
        if not isinstance(pattern, str) or pattern not in _PATTERNS:
            raise ValueError(f"pattern must be one of {', '.join(_PATTERNS)}")
        duration = data.get("duration", 10)
        fade = pattern in ("fade-in", "fade-out")
        if duration is None:
            if fade:
                raise ValueError("Fades require a finite duration")
        else:
            duration = _seconds("duration", duration, 3600)
        restore = data.get("restoreOnComplete", not fade)
        if not isinstance(restore, bool):
            raise ValueError("restoreOnComplete must be a boolean")
        minimum = _brightness(data.get("minBrightness", 0))
        maximum = _brightness(data["maxBrightness"]) if "maxBrightness" in data else None
        if maximum is not None and minimum > maximum:
            raise ValueError("maxBrightness must be >= minBrightness")
        return cls(
            pattern=pattern,
            duration=duration,
            period=_seconds("period", data.get("period", 4), 60),
            minBrightness=minimum,
            maxBrightness=maximum,
            cct=validate_cct(data["cct"]) if "cct" in data else None,
            cctEnd=validate_cct(data.get("cctEnd", 6500)),
            restoreOnComplete=restore,
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
        return replace(
            self, maxBrightness=maximum, cct=self.cct if self.cct is not None else state.cct
        )

    def target(self, elapsed: float) -> tuple[int, int]:
        assert self.maxBrightness is not None and self.cct is not None
        if self.duration is not None:
            elapsed = min(elapsed, self.duration)
        maximum, minimum = self.maxBrightness, self.minBrightness
        phase = (elapsed / self.period) % 1
        cct = self.cct
        if self.pattern == "blink":
            brightness = maximum if phase < 0.5 else minimum
        elif self.pattern == "breathe":
            brightness = minimum + (maximum - minimum) * (1 - math.cos(2 * math.pi * phase)) / 2
        elif self.pattern == "cct-sweep":
            brightness = maximum
            cct = (
                round((self.cct + (self.cctEnd - self.cct) * (1 - abs(2 * phase - 1))) / 100) * 100
            )
        else:
            assert self.duration is not None
            progress = min(1, max(0, elapsed / self.duration))
            if self.pattern == "fade-out":
                progress = 1 - progress
            brightness = minimum + (maximum - minimum) * progress
        return round(brightness), cct


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
                    # No cached target survives a lock wait or rate-limit sleep.
                    # LightClient.set serializes its writes and verifies readback.
                    try:
                        await run.client.set(
                            brightness=brightness,
                            cct=cct,
                            _guard=lambda: self._check_output(run, allow_cancelled=allow_cancelled),
                        )
                    finally:
                        self._next_output = asyncio.get_running_loop().time() + _FRAME_INTERVAL
                    self.bridge.publish()
                    return elapsed
            await asyncio.sleep(delay)

    async def _runner(self, run: _Run):
        try:
            while not run.cancelled:
                elapsed = await self._output(run)
                duration = run.config.duration
                if duration is not None and elapsed >= duration:
                    if run.config.restoreOnComplete:
                        await self._output(run, restore=True)
                    return
                delay = _FRAME_INTERVAL
                if duration is not None:
                    delay = min(delay, duration - elapsed)
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._last_error = str(exc) or type(exc).__name__
            self.bridge.publish()
        finally:
            run.elapsed = max(0, asyncio.get_running_loop().time() - run.started)
            run.running = False
