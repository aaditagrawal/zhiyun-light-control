"""Shared light factories for synchronous media bridges."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass

from .async_client import AsyncZhiyunLight
from .client import ZhiyunLight
from .models import Scene


@dataclass(frozen=True)
class LightConnectionConfig:
    transport: str = "usb"
    port: str | None = None
    address: str | None = None
    name_contains: str | None = None
    timeout: float = 1.5
    ble_python: str | None = None
    ble_in_process: bool = False
    persistent: bool = False


LightFactory = Callable[[], object]


def make_light_factory(config: LightConnectionConfig) -> LightFactory:
    factory = make_one_shot_light_factory(config)
    if config.persistent:
        return PersistentLightFactory(factory)
    return factory


def make_one_shot_light_factory(config: LightConnectionConfig) -> LightFactory:
    if config.transport == "usb":
        return lambda: ZhiyunLight.usb(port=config.port, timeout=config.timeout)
    if config.transport == "ble":
        return lambda: SyncBleLight(config)
    raise ValueError(f"unsupported light transport: {config.transport}")


class PersistentLightFactory:
    """Keep a light connection open between bridge dispatches."""

    def __init__(self, factory: LightFactory):
        self.factory = factory
        self._lock = threading.RLock()
        self._context: object | None = None
        self._light: object | None = None

    def __call__(self) -> _PersistentLightBorrow:
        return _PersistentLightBorrow(self)

    def close(self) -> None:
        with self._lock:
            self._close_locked(None, None, None)

    def _borrow(self):
        self._lock.acquire()
        try:
            if self._light is None:
                self._context = self.factory()
                self._light = self._context.__enter__()
            return self._light
        except Exception:
            self._lock.release()
            raise

    def _release(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is not None:
                self._close_locked(exc_type, exc, tb)
        finally:
            self._lock.release()

    def _close_locked(self, exc_type, exc, tb) -> None:
        context = self._context
        self._context = None
        self._light = None
        if context is not None:
            context.__exit__(exc_type, exc, tb)


class _PersistentLightBorrow:
    def __init__(self, owner: PersistentLightFactory):
        self.owner = owner

    def __enter__(self):
        return self.owner._borrow()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.owner._release(exc_type, exc, tb)


def close_light_factory(factory: LightFactory) -> None:
    close = getattr(factory, "close", None)
    if close is not None:
        close()


class SyncBleLight:
    """Synchronous adapter around the async BLE client.

    The stdlib HTTP, OSC, and Art-Net bridges are synchronous. Keeping one event
    loop per context manager gives them a uniform sync interface while the async
    BLE implementation handles either direct bleak or crash-isolated workers.
    """

    def __init__(self, config: LightConnectionConfig):
        self.config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._light: AsyncZhiyunLight | None = None

    def __enter__(self) -> SyncBleLight:
        self._loop = asyncio.new_event_loop()
        self._light = self._make_async_light()
        try:
            self._run(self._light.__aenter__())
        except Exception:
            self._light = None
            self._loop.close()
            self._loop = None
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._light is not None:
                self._run(self._light.__aexit__(exc_type, exc, tb))
        finally:
            self._light = None
            if self._loop is not None:
                self._loop.close()
                self._loop = None

    def probe(self):
        return self._run_light("probe")

    def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ):
        return self._run_light("exchange_runtime", cmd, payload, timeout=timeout)

    def apply_scene(self, scene: Scene):
        return self._run_light("apply_scene", scene)

    def transition_scene(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
    ):
        return self._run_light(
            "transition_scene",
            start,
            end,
            steps=steps,
            duration=duration,
            easing=easing,
        )

    def _run_light(self, method: str, *args: object, **kwargs: object):
        if self._light is None:
            raise RuntimeError("BLE light is not open")
        return self._run(getattr(self._light, method)(*args, **kwargs))

    def _make_async_light(self) -> AsyncZhiyunLight:
        if self.config.ble_in_process:
            return AsyncZhiyunLight.ble(
                address=self.config.address,
                name_contains=self.config.name_contains,
                timeout=self.config.timeout,
            )
        return AsyncZhiyunLight.isolated_ble(
            address=self.config.address,
            name_contains=self.config.name_contains,
            timeout=self.config.timeout,
            python=self.config.ble_python,
        )

    def _run(self, awaitable):
        if self._loop is None:
            raise RuntimeError("BLE event loop is not open")
        previous_loop = _get_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            return self._loop.run_until_complete(awaitable)
        finally:
            asyncio.set_event_loop(previous_loop)


def _get_event_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        return None
