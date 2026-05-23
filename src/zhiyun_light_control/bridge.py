"""Shared light factories for synchronous media bridges."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

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


LightFactory = Callable[[], Any]


def make_light_factory(config: LightConnectionConfig) -> LightFactory:
    if config.transport == "usb":
        return lambda: ZhiyunLight.usb(port=config.port, timeout=config.timeout)
    if config.transport == "ble":
        return lambda: SyncBleLight(config)
    raise ValueError(f"unsupported light transport: {config.transport}")


class SyncBleLight:
    """Synchronous adapter around the async BLE client.

    The stdlib HTTP, OSC, and Art-Net bridges are synchronous. Keeping one event
    loop per context manager avoids moving a bleak connection across event loops.
    """

    def __init__(self, config: LightConnectionConfig):
        self.config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._light: AsyncZhiyunLight | None = None

    def __enter__(self) -> "SyncBleLight":
        self._loop = asyncio.new_event_loop()
        self._light = AsyncZhiyunLight.ble(
            address=self.config.address,
            name_contains=self.config.name_contains,
            timeout=self.config.timeout,
        )
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

    def _run_light(self, method: str, *args: Any, **kwargs: Any):
        if self._light is None:
            raise RuntimeError("BLE light is not open")
        return self._run(getattr(self._light, method)(*args, **kwargs))

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
