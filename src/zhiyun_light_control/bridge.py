"""Shared light factories for synchronous media bridges."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields

from .async_client import AsyncZhiyunLight
from .client import ZhiyunLight
from .models import Scene
from .protocol import DEFAULT_CONTROL_MODE
from .transports.ble import DEFAULT_BLE_PROFILE
from .transports.usb import DEFAULT_LOCK_TIMEOUT


@dataclass(frozen=True)
class LightConnectionConfig:
    transport: str = "usb"
    port: str | None = None
    address: str | None = None
    name_contains: str | None = None
    timeout: float = 1.5
    usb_lock_timeout: float | None = DEFAULT_LOCK_TIMEOUT
    ble_profile: str = DEFAULT_BLE_PROFILE.name
    ble_service_uuid: str | None = None
    ble_write_uuid: str | None = None
    ble_notify_uuid: str | None = None
    ble_python: str | None = None
    ble_backend: str = "worker"
    ble_in_process: bool = False
    persistent: bool = False

    @classmethod
    def usb(
        cls,
        *,
        port: str | None = None,
        timeout: float = 1.5,
        usb_lock_timeout: float | None = DEFAULT_LOCK_TIMEOUT,
        persistent: bool = False,
    ) -> LightConnectionConfig:
        return cls(
            transport="usb",
            port=port,
            timeout=timeout,
            usb_lock_timeout=usb_lock_timeout,
            persistent=persistent,
        )

    @classmethod
    def ble(
        cls,
        *,
        address: str | None = None,
        name_contains: str | None = None,
        timeout: float = 1.5,
        backend: str = "worker",
        profile: str = DEFAULT_BLE_PROFILE.name,
        service_uuid: str | None = None,
        write_uuid: str | None = None,
        notify_uuid: str | None = None,
        python: str | None = None,
        in_process: bool = False,
        persistent: bool = False,
    ) -> LightConnectionConfig:
        return cls(
            transport="ble",
            address=address,
            name_contains=name_contains,
            timeout=timeout,
            ble_backend=backend,
            ble_profile=profile,
            ble_service_uuid=service_uuid,
            ble_write_uuid=write_uuid,
            ble_notify_uuid=notify_uuid,
            ble_python=python,
            ble_in_process=in_process,
            persistent=persistent,
        )

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
    ) -> LightConnectionConfig:
        values = {
            field.name: _config_value(field.name, payload[field.name])
            for field in fields(cls)
            if field.name in payload
        }
        return cls(**values)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def with_updates(self, **updates: object) -> LightConnectionConfig:
        return self.from_mapping(self.to_dict() | updates)

    def with_ble_candidate(
        self,
        candidate: Mapping[str, object],
        *,
        address: str | None = None,
        name_contains: str | None = None,
        backend: str | None = None,
        timeout: float | None = None,
        python: str | None = None,
        persistent: bool | None = None,
    ) -> LightConnectionConfig:
        return self.with_updates(
            transport="ble",
            address=self.address if address is None else address,
            name_contains=(
                self.name_contains if name_contains is None else name_contains
            ),
            timeout=self.timeout if timeout is None else timeout,
            ble_backend=self.ble_backend if backend is None else backend,
            ble_profile=_optional_string(candidate.get("profile")) or self.ble_profile,
            ble_service_uuid=_optional_string(
                candidate.get("service_uuid", self.ble_service_uuid)
            ),
            ble_write_uuid=_optional_string(
                candidate.get("write_uuid", self.ble_write_uuid)
            ),
            ble_notify_uuid=_optional_string(
                candidate.get("notify_uuid", self.ble_notify_uuid)
            ),
            ble_python=self.ble_python if python is None else python,
            persistent=self.persistent if persistent is None else persistent,
        )


def light_connection_config_from_mapping(
    payload: Mapping[str, object],
) -> LightConnectionConfig:
    return LightConnectionConfig.from_mapping(payload)


def _config_value(name: str, value: object) -> object:
    if name == "timeout":
        return float(value)
    if name == "usb_lock_timeout":
        return _optional_float(value)
    if name in {"ble_in_process", "persistent"}:
        return _config_bool(value)
    return value


def _config_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"none", "null", ""}:
        return None
    return float(value)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


LightFactory = Callable[[], object]


def make_light_factory(config: LightConnectionConfig) -> LightFactory:
    factory = make_one_shot_light_factory(config)
    if config.persistent:
        return PersistentLightFactory(factory)
    return factory


def open_light(config: LightConnectionConfig | None = None):
    return make_light_factory(config or LightConnectionConfig())()


def make_one_shot_light_factory(config: LightConnectionConfig) -> LightFactory:
    if config.transport == "usb":
        return lambda: ZhiyunLight.usb(
            port=config.port,
            timeout=config.timeout,
            lock_timeout=config.usb_lock_timeout,
        )
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

    def command(self, cmd: int, payload: bytes = b"", *, timeout: float = 1.5):
        return self._run_light("command", cmd, payload, timeout=timeout)

    def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ):
        return self._run_light("exchange_runtime", cmd, payload, timeout=timeout)

    def exchange_runtime_confirmed(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
        action: str = "runtime command",
    ):
        return self._run_light(
            "exchange_runtime_confirmed",
            cmd,
            payload,
            timeout=timeout,
            action=action,
        )

    def exchange_frame(
        self,
        first_word: int,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ):
        return self._run_light(
            "exchange_frame",
            first_word,
            cmd,
            payload,
            timeout=timeout,
        )

    def exchange_prebuilt_frame(
        self,
        frame: bytes,
        command: int,
        *,
        timeout: float = 1.5,
    ):
        return self._run_light(
            "exchange_prebuilt_frame",
            frame,
            command,
            timeout=timeout,
        )

    def exchange_frame_confirmed(
        self,
        first_word: int,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
        action: str = "frame command",
    ):
        return self._run_light(
            "exchange_frame_confirmed",
            first_word,
            cmd,
            payload,
            timeout=timeout,
            action=action,
        )

    def exchange_updater(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ):
        return self._run_light("exchange_updater", cmd, payload, timeout=timeout)

    def updater_command(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ):
        return self._run_light("updater_command", cmd, payload, timeout=timeout)

    def get_device_info(self):
        return self._run_light("get_device_info")

    def get_firmware_version(self):
        return self._run_light("get_firmware_version")

    def get_voltage_status(self):
        return self._run_light("get_voltage_status")

    def get_device_id(self):
        return self._run_light("get_device_id")

    def register(self, device_id: int = 0, group_id: int = 0):
        return self._run_light("register", device_id, group_id)

    def register_confirmed(self, device_id: int = 0, group_id: int = 0):
        return self._run_light("register_confirmed", device_id, group_id)

    def read_brightness(self, obj: int = 0):
        return self._run_light("read_brightness", obj)

    def set_brightness(
        self,
        obj: int,
        value: float,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light(
            "set_brightness",
            obj,
            value,
            control_mode=control_mode,
        )

    def set_brightness_confirmed(
        self,
        obj: int,
        value: float,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light(
            "set_brightness_confirmed",
            obj,
            value,
            control_mode=control_mode,
        )

    def read_cct(self, obj: int = 0):
        return self._run_light("read_cct", obj)

    def set_cct(
        self,
        obj: int,
        kelvin: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light("set_cct", obj, kelvin, control_mode=control_mode)

    def set_cct_confirmed(
        self,
        obj: int,
        kelvin: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light(
            "set_cct_confirmed",
            obj,
            kelvin,
            control_mode=control_mode,
        )

    def set_rgb(
        self,
        obj: int,
        red: int,
        green: int,
        blue: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light(
            "set_rgb",
            obj,
            red,
            green,
            blue,
            control_mode=control_mode,
        )

    def set_rgb_confirmed(
        self,
        obj: int,
        red: int,
        green: int,
        blue: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light(
            "set_rgb_confirmed",
            obj,
            red,
            green,
            blue,
            control_mode=control_mode,
        )

    def set_hsi(
        self,
        obj: int,
        hue: float,
        saturation: float,
        intensity: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light(
            "set_hsi",
            obj,
            hue,
            saturation,
            intensity,
            control_mode=control_mode,
        )

    def set_hsi_confirmed(
        self,
        obj: int,
        hue: float,
        saturation: float,
        intensity: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light(
            "set_hsi_confirmed",
            obj,
            hue,
            saturation,
            intensity,
            control_mode=control_mode,
        )

    def read_sleep(self, obj: int = 0):
        return self._run_light("read_sleep", obj)

    def set_sleep(
        self,
        obj: int,
        value: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light("set_sleep", obj, value, control_mode=control_mode)

    def set_sleep_confirmed(
        self,
        obj: int,
        value: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light(
            "set_sleep_confirmed",
            obj,
            value,
            control_mode=control_mode,
        )

    def apply_scene(
        self,
        scene: Scene,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light("apply_scene", scene, control_mode=control_mode)

    def apply_scene_confirmed(
        self,
        scene: Scene,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light(
            "apply_scene_confirmed",
            scene,
            control_mode=control_mode,
        )

    def transition_scene(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light(
            "transition_scene",
            start,
            end,
            steps=steps,
            duration=duration,
            easing=easing,
            control_mode=control_mode,
        )

    def transition_scene_confirmed(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: str = "linear",
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self._run_light(
            "transition_scene_confirmed",
            start,
            end,
            steps=steps,
            duration=duration,
            easing=easing,
            control_mode=control_mode,
        )

    def get_object_firmware(self, obj: int = 0):
        return self._run_light("get_object_firmware", obj)

    def get_object_voltage(self, obj: int = 0):
        return self._run_light("get_object_voltage", obj)

    def get_object_mode(self, obj: int = 0):
        return self._run_light("get_object_mode", obj)

    def identify(self, obj: int = 0):
        return self._run_light("identify", obj)

    def chip_sync(self):
        return self._run_light("chip_sync")

    def read_sn(self):
        return self._run_light("read_sn")

    def _run_light(self, method: str, *args: object, **kwargs: object):
        if self._light is None:
            raise RuntimeError("BLE light is not open")
        return self._run(getattr(self._light, method)(*args, **kwargs))

    def _make_async_light(self) -> AsyncZhiyunLight:
        backend = "direct" if self.config.ble_in_process else self.config.ble_backend
        if backend == "direct":
            return AsyncZhiyunLight.ble(
                address=self.config.address,
                name_contains=self.config.name_contains,
                profile=self.config.ble_profile,
                service_uuid=self.config.ble_service_uuid,
                write_uuid=self.config.ble_write_uuid,
                notify_uuid=self.config.ble_notify_uuid,
                timeout=self.config.timeout,
            )
        if backend == "macos-app":
            return AsyncZhiyunLight.macos_ble_app(
                address=self.config.address,
                name_contains=self.config.name_contains,
                profile=self.config.ble_profile,
                service_uuid=self.config.ble_service_uuid,
                write_uuid=self.config.ble_write_uuid,
                notify_uuid=self.config.ble_notify_uuid,
                timeout=self.config.timeout,
            )
        return AsyncZhiyunLight.isolated_ble(
            address=self.config.address,
            name_contains=self.config.name_contains,
            profile=self.config.ble_profile,
            service_uuid=self.config.ble_service_uuid,
            write_uuid=self.config.ble_write_uuid,
            notify_uuid=self.config.ble_notify_uuid,
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
        return asyncio.get_running_loop()
    except RuntimeError:
        return None
