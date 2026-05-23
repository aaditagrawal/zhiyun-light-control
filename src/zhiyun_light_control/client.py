"""Synchronous public API for Zhiyun MOLUS lights."""

from __future__ import annotations

import itertools
import time
from dataclasses import asdict, dataclass
from typing import Any

from .models import CommandResult, Scene
from .protocol import (
    RuntimeCommand,
    UpdaterCommand,
    build_runtime_frame,
    build_updater_frame,
    brightness_payload,
    cct_payload,
    first_response_frame,
    iter_frames,
    hsi_payload,
    object_id_payload,
    parse_chip_sync,
    parse_device_id,
    parse_device_info,
    parse_version,
    parse_voltage_status,
    register_payload,
    rgb_payload,
    sleep_payload,
)
from .transitions import EasingName, scene_transition, transition_interval
from .transports.usb import UsbTransport


@dataclass(frozen=True)
class ProbeResult:
    device_identifier: str | None
    generation: str | None
    firmware: str | None
    voltage_status: int | None
    device_id: int | None
    port: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ZhiyunLight:
    """Synchronous light client.

    This class works with any transport exposing ``exchange(tx, timeout)`` and
    ``close()``. The bundled implementation is USB CDC.
    """

    def __init__(self, transport: Any):
        self.transport = transport
        self._seq = itertools.count(1)

    def __enter__(self) -> "ZhiyunLight":
        if hasattr(self.transport, "open"):
            self.transport.open()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    @classmethod
    def usb(cls, port: str | None = None, *, timeout: float = 0.8) -> "ZhiyunLight":
        return cls(UsbTransport(port=port, timeout=timeout))

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
        tx = build_runtime_frame(next(self._seq), cmd, payload)
        rx = self.transport.exchange(tx, timeout=timeout)
        frames = tuple(iter_frames(rx))
        return CommandResult(
            command=cmd & 0xFFFF,
            tx=tx,
            rx=rx,
            frames=frames,
            ack=first_response_frame(rx, tx=tx, cmd=cmd),
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
        tx = build_updater_frame(next(self._seq), cmd, payload)
        rx = self.transport.exchange(tx, timeout=timeout)
        frames = tuple(iter_frames(rx))
        return CommandResult(
            command=cmd & 0xFFFF,
            tx=tx,
            rx=rx,
            frames=frames,
            ack=first_response_frame(rx, tx=tx, cmd=cmd),
        )

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

    def read_brightness(self, obj: int = 0):
        return self.command(RuntimeCommand.BRIGHTNESS, brightness_payload(obj, read=True))

    def set_brightness(self, obj: int, value: float):
        return self.command(
            RuntimeCommand.BRIGHTNESS,
            brightness_payload(obj, value, read=False),
        )

    def read_cct(self, obj: int = 0):
        return self.command(RuntimeCommand.CCT, cct_payload(obj, read=True))

    def set_cct(self, obj: int, kelvin: int):
        return self.command(RuntimeCommand.CCT, cct_payload(obj, kelvin, read=False))

    def set_rgb(self, obj: int, red: int, green: int, blue: int):
        return self.command(RuntimeCommand.RGB, rgb_payload(obj, red, green, blue))

    def set_hsi(self, obj: int, hue: float, saturation: float, intensity: int):
        return self.command(RuntimeCommand.HSI, hsi_payload(obj, hue, saturation, intensity))

    def read_sleep(self, obj: int = 0):
        return self.command(RuntimeCommand.SLEEP, sleep_payload(obj, read=True))

    def set_sleep(self, obj: int, value: int):
        return self.command(RuntimeCommand.SLEEP, sleep_payload(obj, value, read=False))

    def apply_scene(self, scene: Scene) -> list[CommandResult]:
        results: list[CommandResult] = []
        if scene.sleep is not None:
            results.append(
                self.exchange_runtime(
                    RuntimeCommand.SLEEP,
                    sleep_payload(scene.obj, scene.sleep, read=False),
                )
            )
        if scene.brightness is not None:
            results.append(
                self.exchange_runtime(
                    RuntimeCommand.BRIGHTNESS,
                    brightness_payload(scene.obj, scene.brightness, read=False),
                )
            )
        if scene.kelvin is not None:
            results.append(
                self.exchange_runtime(
                    RuntimeCommand.CCT,
                    cct_payload(scene.obj, scene.kelvin, read=False),
                )
            )
        if scene.red is not None or scene.green is not None or scene.blue is not None:
            if scene.red is None or scene.green is None or scene.blue is None:
                raise ValueError("scene RGB requires red, green, and blue")
            results.append(
                self.exchange_runtime(
                    RuntimeCommand.RGB,
                    rgb_payload(scene.obj, scene.red, scene.green, scene.blue),
                )
            )
        if (
            scene.hue is not None
            or scene.saturation is not None
            or scene.intensity is not None
        ):
            if scene.hue is None or scene.saturation is None or scene.intensity is None:
                raise ValueError("scene HSI requires hue, saturation, and intensity")
            results.append(
                self.exchange_runtime(
                    RuntimeCommand.HSI,
                    hsi_payload(
                        scene.obj,
                        scene.hue,
                        scene.saturation,
                        scene.intensity,
                    ),
                )
            )
        return results

    def transition_scene(
        self,
        start: Scene,
        end: Scene,
        *,
        steps: int = 10,
        duration: float = 1.0,
        easing: EasingName = "linear",
    ) -> list[list[CommandResult]]:
        scenes = scene_transition(start, end, steps=steps, easing=easing)
        delay = transition_interval(duration, len(scenes))
        batches: list[list[CommandResult]] = []
        for index, scene in enumerate(scenes):
            batches.append(self.apply_scene(scene))
            if delay > 0 and index < len(scenes) - 1:
                time.sleep(delay)
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
        return self.updater_command(UpdaterCommand.READ_SN)
