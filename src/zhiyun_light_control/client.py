"""Synchronous public API for Zhiyun MOLUS lights."""

from __future__ import annotations

import itertools
import time
from dataclasses import asdict, dataclass

from .commands import scene_command_specs
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
from .transitions import EasingName, scene_transition, transition_interval
from .transports.usb import DEFAULT_LOCK_TIMEOUT, UsbTransport


@dataclass(frozen=True)
class ProbeResult:
    device_identifier: str | None
    generation: str | None
    firmware: str | None
    voltage_status: int | None
    device_id: int | None
    port: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ZhiyunLight:
    """Synchronous light client.

    This class works with any transport exposing ``exchange(tx, timeout)`` and
    ``close()``. The bundled implementation is USB CDC.
    """

    def __init__(self, transport: object):
        self.transport = transport
        self._seq = itertools.count(1)

    def __enter__(self) -> ZhiyunLight:
        if hasattr(self.transport, "open"):
            self.transport.open()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    @classmethod
    def usb(
        cls,
        port: str | None = None,
        *,
        timeout: float = 0.8,
        lock_timeout: float | None = DEFAULT_LOCK_TIMEOUT,
    ) -> ZhiyunLight:
        return cls(UsbTransport(port=port, timeout=timeout, lock_timeout=lock_timeout))

    def close(self) -> None:
        if hasattr(self.transport, "close"):
            self.transport.close()

    def exchange_runtime(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        return self.exchange_frame(RUNTIME_TYPE, cmd, payload, timeout=timeout)

    def exchange_runtime_confirmed(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
        action: str = "runtime command",
    ) -> CommandResult:
        return require_command_result(
            self.exchange_runtime(cmd, payload, timeout=timeout),
            action=action,
        )

    def exchange_frame(
        self,
        first_word: int,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        tx = build_frame(first_word, next(self._seq), cmd, payload)
        rx = self.transport.exchange(tx, timeout=timeout)
        frames = tuple(iter_frames(rx))
        return CommandResult(
            command=cmd & 0xFFFF,
            tx=tx,
            rx=rx,
            frames=frames,
            ack=first_response_frame(rx, tx=tx, cmd=cmd),
        )

    def exchange_frame_confirmed(
        self,
        first_word: int,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
        action: str = "frame command",
    ) -> CommandResult:
        return require_command_result(
            self.exchange_frame(first_word, cmd, payload, timeout=timeout),
            action=action,
        )

    def command(self, cmd: int, payload: bytes = b"", *, timeout: float = 0.8):
        return self.exchange_runtime(cmd, payload, timeout=timeout).ack

    def exchange_updater(
        self,
        cmd: int,
        payload: bytes = b"",
        *,
        timeout: float = 0.8,
    ) -> CommandResult:
        return self.exchange_frame(UPDATER_DEVICE, cmd, payload, timeout=timeout)

    def updater_command(self, cmd: int, payload: bytes = b"", *, timeout: float = 0.8):
        return self.exchange_updater(cmd, payload, timeout=timeout).ack

    def get_device_info(self):
        frame = self.command(RuntimeCommand.DEVICE_INFO)
        return parse_device_info(frame) if frame else None

    def get_firmware_version(self) -> str | None:
        frame = self.command(RuntimeCommand.FIRMWARE)
        return parse_version(frame) if frame else None

    def get_voltage_status(self) -> int | None:
        frame = self.command(RuntimeCommand.VOLTAGE)
        return parse_voltage_status(frame) if frame else None

    def get_device_id(self) -> int | None:
        frame = self.command(RuntimeCommand.DEVICE_ID)
        return parse_device_id(frame) if frame else None

    def probe(self) -> ProbeResult:
        info = self.get_device_info()
        firmware = self.get_firmware_version()
        voltage = self.get_voltage_status()
        device_id = self.get_device_id()
        return ProbeResult(
            device_identifier=info.identifier if info else None,
            generation=info.generation if info else None,
            firmware=firmware,
            voltage_status=voltage,
            device_id=device_id,
            port=getattr(self.transport, "port", None),
        )

    def register(self, device_id: int = 0, group_id: int = 0):
        return self.command(
            RuntimeCommand.REGISTER_DEFAULT_GROUP,
            register_payload(device_id, group_id),
        )

    def register_confirmed(self, device_id: int = 0, group_id: int = 0):
        return require_command_result(
            self.exchange_runtime(
                RuntimeCommand.REGISTER_DEFAULT_GROUP,
                register_payload(device_id, group_id),
            ),
            action="register",
        )

    def read_brightness(self, obj: int = 0):
        return self.command(
            RuntimeCommand.BRIGHTNESS, brightness_payload(obj, read=True)
        )

    def set_brightness(
        self,
        obj: int,
        value: float,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self.exchange_runtime(
            RuntimeCommand.BRIGHTNESS,
            brightness_payload(obj, value, read=False, control_mode=control_mode),
        )

    def set_brightness_confirmed(
        self,
        obj: int,
        value: float,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> CommandResult:
        return require_command_result(
            self.set_brightness(obj, value, control_mode=control_mode),
            action="brightness",
        )

    def read_cct(self, obj: int = 0):
        return self.command(RuntimeCommand.CCT, cct_payload(obj, read=True))

    def set_cct(
        self,
        obj: int,
        kelvin: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self.exchange_runtime(
            RuntimeCommand.CCT,
            cct_payload(obj, kelvin, read=False, control_mode=control_mode),
        )

    def set_cct_confirmed(
        self,
        obj: int,
        kelvin: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> CommandResult:
        return require_command_result(
            self.set_cct(obj, kelvin, control_mode=control_mode),
            action="cct",
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
        return self.exchange_runtime(
            RuntimeCommand.RGB,
            rgb_payload(obj, red, green, blue, control_mode=control_mode),
        )

    def set_rgb_confirmed(
        self,
        obj: int,
        red: int,
        green: int,
        blue: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> CommandResult:
        return require_command_result(
            self.set_rgb(obj, red, green, blue, control_mode=control_mode),
            action="rgb",
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
        return self.exchange_runtime(
            RuntimeCommand.HSI,
            hsi_payload(obj, hue, saturation, intensity, control_mode=control_mode),
        )

    def set_hsi_confirmed(
        self,
        obj: int,
        hue: float,
        saturation: float,
        intensity: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> CommandResult:
        return require_command_result(
            self.set_hsi(
                obj,
                hue,
                saturation,
                intensity,
                control_mode=control_mode,
            ),
            action="hsi",
        )

    def read_sleep(self, obj: int = 0):
        return self.command(RuntimeCommand.SLEEP, sleep_payload(obj, read=True))

    def set_sleep(
        self,
        obj: int,
        value: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ):
        return self.exchange_runtime(
            RuntimeCommand.SLEEP,
            sleep_payload(obj, value, read=False, control_mode=control_mode),
        )

    def set_sleep_confirmed(
        self,
        obj: int,
        value: int,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> CommandResult:
        return require_command_result(
            self.set_sleep(obj, value, control_mode=control_mode),
            action="sleep",
        )

    def apply_scene(
        self,
        scene: Scene,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> list[CommandResult]:
        return [
            self.exchange_runtime(command.command, command.payload)
            for command in scene_command_specs(scene, control_mode=control_mode)
        ]

    def apply_scene_confirmed(
        self,
        scene: Scene,
        *,
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> list[CommandResult]:
        return require_command_results(
            self.apply_scene(scene, control_mode=control_mode),
            action="scene",
        )

    def transition_scene(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: EasingName = "linear",
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> list[list[CommandResult]]:
        scenes = scene_transition(start, end, steps=steps, easing=easing)
        delay = transition_interval(duration, len(scenes))
        batches: list[list[CommandResult]] = []
        for index, scene in enumerate(scenes):
            batches.append(self.apply_scene(scene, control_mode=control_mode))
            if delay > 0 and index < len(scenes) - 1:
                time.sleep(delay)
        return batches

    def transition_scene_confirmed(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: EasingName = "linear",
        control_mode: int = DEFAULT_CONTROL_MODE,
    ) -> list[list[CommandResult]]:
        batches = self.transition_scene(
            start,
            end,
            steps=steps,
            duration=duration,
            easing=easing,
            control_mode=control_mode,
        )
        require_command_results(flatten_command_batches(batches), action="transition")
        return batches

    def get_object_firmware(self, obj: int = 0):
        return self.command(RuntimeCommand.FIRMWARE_BY_OBJECT, object_id_payload(obj))

    def get_object_voltage(self, obj: int = 0):
        return self.command(RuntimeCommand.VOLTAGE_BY_OBJECT, object_id_payload(obj))

    def get_object_mode(self, obj: int = 0):
        return self.command(RuntimeCommand.DEVICE_MODE, object_id_payload(obj))

    def identify(self, obj: int = 0):
        return self.command(RuntimeCommand.IDENTIFY, object_id_payload(obj))

    def chip_sync(self):
        frame = self.updater_command(UpdaterCommand.CHIP_SYNC)
        return parse_chip_sync(frame) if frame else None

    def read_sn(self):
        frame = self.updater_command(UpdaterCommand.READ_SN)
        return parse_read_sn(frame) if frame else None
