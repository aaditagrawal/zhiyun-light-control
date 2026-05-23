"""Minimal OSC bridge for media-production tools.

This is intentionally small and dependency-free. It implements the OSC message
types needed for light control: int, float, string, booleans, and nil.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Any, Callable

from .bridge import close_light_factory
from .client import ZhiyunLight
from .models import Scene
from .presets import ScenePresetLibrary
from .protocol import (
    RuntimeCommand,
    brightness_payload,
    cct_payload,
    hsi_payload,
    register_payload,
    rgb_payload,
    sleep_payload,
)
from .state import SceneStateTracker


OscArg = int | float | str | bool | None


@dataclass(frozen=True)
class OscMessage:
    address: str
    args: tuple[OscArg, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"address": self.address, "args": list(self.args)}


@dataclass(frozen=True)
class OscDispatchResult:
    message: OscMessage
    action: str
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message.to_dict(),
            "action": self.action,
            "result": self.result,
            "error": self.error,
        }


class OscDecodeError(ValueError):
    pass


def encode_message(address: str, *args: OscArg) -> bytes:
    tags = ","
    payload = bytearray()
    for arg in args:
        if isinstance(arg, bool):
            tags += "T" if arg else "F"
        elif arg is None:
            tags += "N"
        elif isinstance(arg, int):
            tags += "i"
            payload.extend(struct.pack(">i", arg))
        elif isinstance(arg, float):
            tags += "f"
            payload.extend(struct.pack(">f", arg))
        elif isinstance(arg, str):
            tags += "s"
            payload.extend(_pack_string(arg))
        else:
            raise TypeError(f"unsupported OSC argument: {arg!r}")
    return _pack_string(address) + _pack_string(tags) + bytes(payload)


def decode_message(data: bytes) -> OscMessage:
    address, offset = _read_string(data, 0)
    if not address.startswith("/"):
        raise OscDecodeError("OSC address must start with '/'")
    tags, offset = _read_string(data, offset)
    if not tags.startswith(","):
        raise OscDecodeError("OSC type tag string must start with ','")
    args: list[OscArg] = []
    for tag in tags[1:]:
        if tag == "i":
            _require(data, offset, 4)
            args.append(struct.unpack_from(">i", data, offset)[0])
            offset += 4
        elif tag == "f":
            _require(data, offset, 4)
            args.append(struct.unpack_from(">f", data, offset)[0])
            offset += 4
        elif tag == "s":
            value, offset = _read_string(data, offset)
            args.append(value)
        elif tag == "T":
            args.append(True)
        elif tag == "F":
            args.append(False)
        elif tag == "N":
            args.append(None)
        else:
            raise OscDecodeError(f"unsupported OSC type tag: {tag!r}")
    return OscMessage(address=address, args=tuple(args))


class OscLightDispatcher:
    """Map OSC messages to light API calls."""

    def __init__(
        self,
        light_factory: Callable[[], Any],
        *,
        allow_control: bool,
        preset_library: ScenePresetLibrary | None = None,
        state_tracker: SceneStateTracker | None = None,
    ):
        self.light_factory = light_factory
        self.allow_control = allow_control
        self.preset_library = preset_library
        self.state_tracker = state_tracker or SceneStateTracker()

    def dispatch(self, message: OscMessage) -> OscDispatchResult:
        try:
            if message.address in {"/zhiyun/probe", "/light/probe"}:
                with self.light_factory() as light:
                    return OscDispatchResult(
                        message=message,
                        action="probe",
                        result=light.probe().to_dict(),
                    )
            if not self.allow_control:
                return OscDispatchResult(
                    message=message,
                    action="blocked",
                    error="control requires allow_control",
                )
            with self.light_factory() as light:
                return self._dispatch_control(light, message)
        except Exception as exc:
            return OscDispatchResult(
                message=message,
                action="error",
                error=str(exc),
            )

    def _dispatch_control(
        self,
        light: ZhiyunLight,
        message: OscMessage,
    ) -> OscDispatchResult:
        address = message.address
        args = message.args
        obj = _obj_arg(args, default=1)
        if address in {"/zhiyun/register", "/light/register"}:
            device_id = int(args[0]) if args else 0
            result = light.exchange_runtime(
                RuntimeCommand.REGISTER_DEFAULT_GROUP,
                register_payload(device_id),
            )
            return _command_result(message, "register", result.to_dict())
        if address in {"/zhiyun/brightness", "/light/brightness"}:
            value = _number_arg(args, 0)
            result = light.exchange_runtime(
                RuntimeCommand.BRIGHTNESS,
                brightness_payload(obj, value),
            )
            self._record_scene(Scene(obj=obj, brightness=value), "brightness", [result])
            return _command_result(message, "brightness", result.to_dict())
        if address in {"/zhiyun/cct", "/light/cct"}:
            kelvin = int(_number_arg(args, 0))
            result = light.exchange_runtime(RuntimeCommand.CCT, cct_payload(obj, kelvin))
            self._record_scene(Scene(obj=obj, kelvin=kelvin), "cct", [result])
            return _command_result(message, "cct", result.to_dict())
        if address in {"/zhiyun/sleep", "/light/sleep", "/zhiyun/power", "/light/power"}:
            value = int(_number_arg(args, 0))
            result = light.exchange_runtime(RuntimeCommand.SLEEP, sleep_payload(obj, value))
            self._record_scene(Scene(obj=obj, sleep=value), "sleep", [result])
            return _command_result(message, "sleep", result.to_dict())
        if address in {"/zhiyun/rgb", "/light/rgb"}:
            red = int(_number_arg(args, 0))
            green = int(_number_arg(args, 1))
            blue = int(_number_arg(args, 2))
            result = light.exchange_runtime(
                RuntimeCommand.RGB,
                rgb_payload(obj, red, green, blue),
            )
            self._record_scene(
                Scene(obj=obj, red=red, green=green, blue=blue),
                "rgb",
                [result],
            )
            return _command_result(message, "rgb", result.to_dict())
        if address in {"/zhiyun/hsi", "/light/hsi"}:
            hue = _number_arg(args, 0)
            saturation = _number_arg(args, 1)
            intensity = int(_number_arg(args, 2))
            result = light.exchange_runtime(
                RuntimeCommand.HSI,
                hsi_payload(obj, hue, saturation, intensity),
            )
            self._record_scene(
                Scene(obj=obj, hue=hue, saturation=saturation, intensity=intensity),
                "hsi",
                [result],
            )
            return _command_result(message, "hsi", result.to_dict())
        if address in {"/zhiyun/scene", "/light/scene"}:
            scene = _scene_from_args(args, obj=obj)
            command_results = light.apply_scene(scene)
            self._record_scene(scene, "scene", command_results)
            results = [result.to_dict() for result in command_results]
            return OscDispatchResult(
                message=message,
                action="scene",
                result={"scene": scene.to_dict(), "results": results},
            )
        if address in {"/zhiyun/preset", "/light/preset"}:
            if self.preset_library is None:
                raise ValueError("no preset file loaded")
            name = _string_arg(args, 0)
            preset_obj = int(args[1]) if len(args) >= 2 and isinstance(args[1], int) else None
            scene = self.preset_library.get(name)
            if preset_obj is not None:
                scene = Scene(**{**scene.to_dict(), "obj": preset_obj})
            command_results = light.apply_scene(scene)
            self._record_scene(scene, "preset", command_results)
            results = [result.to_dict() for result in command_results]
            return OscDispatchResult(
                message=message,
                action="preset",
                result={"preset": name, "scene": scene.to_dict(), "results": results},
            )
        return OscDispatchResult(
            message=message,
            action="unknown",
            error=f"unknown OSC address: {address}",
        )

    def _record_scene(self, scene: Scene, action: str, results) -> None:
        self.state_tracker.record(
            scene,
            source="osc",
            action=action,
            applied=True,
            results=results,
        )


def serve_osc(
    *,
    host: str = "127.0.0.1",
    port: int = 9000,
    light_port: str | None = None,
    allow_control: bool = False,
    once: bool = False,
    light_factory: Callable[[], Any] | None = None,
    preset_library: ScenePresetLibrary | None = None,
    state_tracker: SceneStateTracker | None = None,
) -> None:
    dispatcher = OscLightDispatcher(
        light_factory=light_factory or (lambda: ZhiyunLight.usb(port=light_port)),
        allow_control=allow_control,
        preset_library=preset_library,
        state_tracker=state_tracker,
    )
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind((host, port))
            while True:
                data, addr = sock.recvfrom(65535)
                try:
                    result = dispatcher.dispatch(decode_message(data))
                except OscDecodeError as exc:
                    result = OscDispatchResult(
                        message=OscMessage("/decode-error"),
                        action="error",
                        error=str(exc),
                    )
                response = encode_message(
                    "/zhiyun/result",
                    1 if result.error is None else 0,
                    result.action,
                    result.error or "",
                )
                sock.sendto(response, addr)
                if once:
                    return
    finally:
        close_light_factory(dispatcher.light_factory)


def _pack_string(value: str) -> bytes:
    raw = value.encode("utf-8") + b"\x00"
    pad = (-len(raw)) % 4
    return raw + (b"\x00" * pad)


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\x00", offset)
    if end < 0:
        raise OscDecodeError("unterminated OSC string")
    raw = data[offset:end]
    next_offset = end + 1
    next_offset += (-next_offset) % 4
    if next_offset > len(data):
        raise OscDecodeError("OSC string padding exceeds packet length")
    return raw.decode("utf-8"), next_offset


def _require(data: bytes, offset: int, length: int) -> None:
    if offset + length > len(data):
        raise OscDecodeError("OSC packet ended unexpectedly")


def _number_arg(args: tuple[OscArg, ...], index: int) -> float:
    try:
        value = args[index]
    except IndexError as exc:
        raise ValueError(f"missing numeric OSC argument {index}") from exc
    if not isinstance(value, (int, float)):
        raise ValueError(f"OSC argument {index} must be numeric")
    return float(value)


def _string_arg(args: tuple[OscArg, ...], index: int) -> str:
    try:
        value = args[index]
    except IndexError as exc:
        raise ValueError(f"missing string OSC argument {index}") from exc
    if not isinstance(value, str):
        raise ValueError(f"OSC argument {index} must be a string")
    return value


def _obj_arg(args: tuple[OscArg, ...], *, default: int) -> int:
    if len(args) >= 2 and isinstance(args[-1], int):
        return int(args[-1])
    return default


def _scene_from_args(args: tuple[OscArg, ...], *, obj: int) -> Scene:
    brightness = _optional_number(args, 0)
    kelvin = _optional_int(args, 1)
    sleep = _optional_int(args, 2)
    return Scene(obj=obj, brightness=brightness, kelvin=kelvin, sleep=sleep)


def _optional_number(args: tuple[OscArg, ...], index: int) -> float | None:
    if len(args) <= index or args[index] is None:
        return None
    return _number_arg(args, index)


def _optional_int(args: tuple[OscArg, ...], index: int) -> int | None:
    value = _optional_number(args, index)
    return int(value) if value is not None else None


def _command_result(
    message: OscMessage,
    action: str,
    result: dict[str, Any],
) -> OscDispatchResult:
    return OscDispatchResult(message=message, action=action, result=result)
