"""Async public API, currently used for BLE."""

from __future__ import annotations

import itertools
from dataclasses import asdict, dataclass

from .commands import (
    execute_async_command_plan,
    execute_async_transition_plans,
    scene_command_plan,
    transition_command_plans,
)
from .models import (
    CommandResult,
    Scene,
    flatten_command_batches,
    require_command_result,
    require_command_results,
)
from .protocol import (
    DEFAULT_CONTROL_MODE,
    RUNTIME_TYPE,
    UPDATER_DEVICE,
    RuntimeCommand,
    UpdaterCommand,
    brightness_payload,
    build_frame,
    cct_payload,
    first_response_frame,
    hsi_payload,
    iter_frames,
    object_id_payload,
    parse_chip_sync,
    parse_device_id,
    parse_device_info,
    parse_read_sn,
    parse_version,
    parse_voltage_status,
    register_payload,
    rgb_payload,
    sleep_payload,
)
from .transitions import EasingName
from .transports.ble import (
    DEFAULT_BLE_PROFILE,
    BleProfile,
    BleTransport,
    CrashIsolatedBleTransport,
    MacosBleAppTransport,
)


@dataclass(frozen=True)
class AsyncProbeResult:
    device_identifier: str | None
    generation: str | None
    firmware: str | None
    voltage_status: int | None
    device_id: int | None
    address: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class AsyncZhiyunLight:
    """Async light client for BLE transports."""

    def __init__(self, transport: object):
        self.transport = transport
        self._seq = itertools.count(1)

    async def __aenter__(self) -> AsyncZhiyunLight:
        if hasattr(self.transport, "open"):
            await self.transport.open()
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.close()

    @classmethod
    def ble(
        cls,
        *,
        address: str | None = None,
        name_contains: str | None = None,
        profile: str | BleProfile = DEFAULT_BLE_PROFILE.name,
        service_uuid: str | None = None,
        write_uuid: str | None = None,
        notify_uuid: str | None = None,
        timeout: float = 1.5,
    ) -> AsyncZhiyunLight:
        return cls(
            BleTransport(
                address=address,
                name_contains=name_contains,
                profile=profile,
                service_uuid=service_uuid,
                write_uuid=write_uuid,
                notify_uuid=notify_uuid,
                timeout=timeout,
            )
        )

    @classmethod
    def isolated_ble(
        cls,
        *,
        address: str | None = None,
        name_contains: str | None = None,
        profile: str | BleProfile = DEFAULT_BLE_PROFILE.name,
        service_uuid: str | None = None,
        write_uuid: str | None = None,
        notify_uuid: str | None = None,
        timeout: float = 1.5,
        python: str | None = None,
    ) -> AsyncZhiyunLight:
        return cls(
            CrashIsolatedBleTransport(
                address=address,
                name_contains=name_contains,
                profile=profile,
                service_uuid=service_uuid,
                write_uuid=write_uuid,
                notify_uuid=notify_uuid,
                timeout=timeout,
                python=python,
            )
        )

    @classmethod
    def macos_ble_app(
        cls,
        *,
        address: str | None = None,
        name_contains: str | None = None,
        profile: str | BleProfile = DEFAULT_BLE_PROFILE.name,
        service_uuid: str | None = None,
        write_uuid: str | None = None,
        notify_uuid: str | None = None,
        timeout: float = 1.5,
    ) -> AsyncZhiyunLight:
        return cls(
            MacosBleAppTransport(
                address=address,
                name_contains=name_contains,
                profile=profile,
                service_uuid=service_uuid,
                write_uuid=write_uuid,
                notify_uuid=notify_uuid,
                timeout=timeout,
            )
        )

    async def close(self) -> None:
        if hasattr(self.transport, "close"):
            await self.transport.close()

    async def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        return await self.exchange_frame(RUNTIME_TYPE, cmd, payload, timeout=timeout)

    async def exchange_runtime_confirmed(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
        action: str = "runtime command",
    ) -> CommandResult:
        return require_command_result(
            await self.exchange_runtime(cmd, payload, timeout=timeout),
            action=action,
        )

    async def exchange_frame(
        self,
        first_word: int,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        tx = build_frame(first_word, next(self._seq), cmd, payload)
        rx = await self.transport.exchange(tx, timeout=timeout)
        frames = tuple(iter_frames(rx))
        return CommandResult(
            command=cmd & 0xFFFF,
            tx=tx,
            rx=rx,
            frames=frames,
            ack=first_response_frame(rx, tx=tx, cmd=cmd),
        )

    async def exchange_frame_confirmed(
        self,
        first_word: int,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
        action: str = "frame command",
    ) -> CommandResult:
        return require_command_result(
            await self.exchange_frame(first_word, cmd, payload, timeout=timeout),
            action=action,
        )

    async def command(self, cmd: int, payload: bytes = b"", *, timeout: float = 1.5):
        return (await self.exchange_runtime(cmd, payload, timeout=timeout)).ack

    async def exchange_updater(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ) -> CommandResult:
        return await self.exchange_frame(UPDATER_DEVICE, cmd, payload, timeout=timeout)

    async def updater_command(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 1.5,
    ):
        return (await self.exchange_updater(cmd, payload, timeout=timeout)).ack

    async def get_device_info(self):
        frame = await self.command(RuntimeCommand.DEVICE_INFO)
        return parse_device_info(frame) if frame else None

    async def get_firmware_version(self) -> str | None:
        frame = await self.command(RuntimeCommand.FIRMWARE)
        return parse_version(frame) if frame else None

    async def get_voltage_status(self) -> int | None:
        frame = await self.command(RuntimeCommand.VOLTAGE)
        return parse_voltage_status(frame) if frame else None

    async def get_device_id(self) -> int | None:
        frame = await self.command(RuntimeCommand.DEVICE_ID)
        return parse_device_id(frame) if frame else None

    async def probe(self) -> AsyncProbeResult:
        info = await self.get_device_info()
        firmware = await self.get_firmware_version()
        voltage = await self.get_voltage_status()
        device_id = await self.get_device_id()
        return AsyncProbeResult(
            device_identifier=info.identifier if info else None,
            generation=info.generation if info else None,
            firmware=firmware,
            voltage_status=voltage,
            device_id=device_id,
            address=getattr(self.transport, "address", None),
        )

    async def register(self, device_id: int = 0, group_id: int = 0):
        return await self.command(
            RuntimeCommand.REGISTER_DEFAULT_GROUP,
            register_payload(device_id, group_id),
        )

    async def register_confirmed(self, device_id: int = 0, group_id: int = 0):
        return require_command_result(
            await self.exchange_runtime(
                RuntimeCommand.REGISTER_DEFAULT_GROUP,
                register_payload(device_id, group_id),
            ),
            action="register",
        )

    async def read_brightness(self, obj: int = 0):
        return await self.command(
            RuntimeCommand.BRIGHTNESS,
            brightness_payload(obj, read=True),
        )

    async def set_brightness(
        self,
        obj: int,
        value: float,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return await self.exchange_runtime(
            RuntimeCommand.BRIGHTNESS,
            brightness_payload(obj, value, read=False, control_mode=control_mode),
        )

    async def set_brightness_confirmed(
        self,
        obj: int,
        value: float,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> CommandResult:
        return require_command_result(
            await self.set_brightness(obj, value, control_mode=control_mode),
            action="brightness",
        )

    async def read_cct(self, obj: int = 0):
        return await self.command(RuntimeCommand.CCT, cct_payload(obj, read=True))

    async def set_cct(
        self,
        obj: int,
        kelvin: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return await self.exchange_runtime(
            RuntimeCommand.CCT,
            cct_payload(obj, kelvin, read=False, control_mode=control_mode),
        )

    async def set_cct_confirmed(
        self,
        obj: int,
        kelvin: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> CommandResult:
        return require_command_result(
            await self.set_cct(obj, kelvin, control_mode=control_mode),
            action="cct",
        )

    async def set_rgb(
        self,
        obj: int,
        red: int,
        green: int,
        blue: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return await self.exchange_runtime(
            RuntimeCommand.RGB,
            rgb_payload(obj, red, green, blue, control_mode=control_mode),
        )

    async def set_rgb_confirmed(
        self,
        obj: int,
        red: int,
        green: int,
        blue: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> CommandResult:
        return require_command_result(
            await self.set_rgb(obj, red, green, blue, control_mode=control_mode),
            action="rgb",
        )

    async def set_hsi(
        self,
        obj: int,
        hue: float,
        saturation: float,
        intensity: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return await self.exchange_runtime(
            RuntimeCommand.HSI,
            hsi_payload(obj, hue, saturation, intensity, control_mode=control_mode),
        )

    async def set_hsi_confirmed(
        self,
        obj: int,
        hue: float,
        saturation: float,
        intensity: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> CommandResult:
        return require_command_result(
            await self.set_hsi(
                obj,
                hue,
                saturation,
                intensity,
                control_mode=control_mode,
            ),
            action="hsi",
        )

    async def read_sleep(self, obj: int = 0):
        return await self.command(RuntimeCommand.SLEEP, sleep_payload(obj, read=True))

    async def set_sleep(
        self,
        obj: int,
        value: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return await self.exchange_runtime(
            RuntimeCommand.SLEEP,
            sleep_payload(obj, value, control_mode=control_mode),
        )

    async def set_sleep_confirmed(
        self,
        obj: int,
        value: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> CommandResult:
        return require_command_result(
            await self.set_sleep(obj, value, control_mode=control_mode),
            action="sleep",
        )

    async def get_object_firmware(self, obj: int = 0):
        return await self.command(
            RuntimeCommand.FIRMWARE_BY_OBJECT, object_id_payload(obj)
        )

    async def get_object_voltage(self, obj: int = 0):
        return await self.command(
            RuntimeCommand.VOLTAGE_BY_OBJECT, object_id_payload(obj)
        )

    async def get_object_mode(self, obj: int = 0):
        return await self.command(RuntimeCommand.DEVICE_MODE, object_id_payload(obj))

    async def identify(self, obj: int = 0):
        return await self.command(RuntimeCommand.IDENTIFY, object_id_payload(obj))

    async def chip_sync(self):
        frame = await self.updater_command(UpdaterCommand.CHIP_SYNC)
        return parse_chip_sync(frame) if frame else None

    async def read_sn(self):
        frame = await self.updater_command(UpdaterCommand.READ_SN)
        return parse_read_sn(frame) if frame else None

    async def apply_scene(
        self,
        scene: Scene,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> list[CommandResult]:
        return await execute_async_command_plan(
            self,
            scene_command_plan(scene, control_mode=control_mode),
        )

    async def apply_scene_confirmed(
        self,
        scene: Scene,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> list[CommandResult]:
        return require_command_results(
            await self.apply_scene(scene, control_mode=control_mode),
            action="scene",
        )

    async def transition_scene(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: EasingName = "linear",
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> list[list[CommandResult]]:
        return await execute_async_transition_plans(
            self,
            transition_command_plans(
                start,
                end,
                steps=steps,
                easing=easing,
                control_mode=control_mode,
            ),
            duration=duration,
        )

    async def transition_scene_confirmed(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: EasingName = "linear",
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> list[list[CommandResult]]:
        batches = await self.transition_scene(
            start,
            end,
            steps=steps,
            duration=duration,
            easing=easing,
            control_mode=control_mode,
        )
        require_command_results(flatten_command_batches(batches), action="transition")
        return batches
