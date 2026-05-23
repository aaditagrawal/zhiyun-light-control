"""Transport-neutral runtime command planning helpers."""

from __future__ import annotations

from dataclasses import dataclass

from .models import Scene
from .protocol import (
    DEFAULT_CONTROL_MODE,
    RUNTIME_TYPE,
    RuntimeCommand,
    brightness_payload,
    build_frame,
    cct_payload,
    hsi_payload,
    rgb_payload,
    sleep_payload,
)


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


def scene_frame_specs(
    scene: Scene,
    *,
    control_mode: int = DEFAULT_CONTROL_MODE,
    first_word: int = RUNTIME_TYPE,
    start_seq: int = 1,
) -> tuple[RuntimeFrameSpec, ...]:
    """Return ordered serialized runtime frames for a scene."""

    return tuple(
        RuntimeFrameSpec(
            command=command,
            seq=seq,
            first_word=first_word,
            frame=command.frame(seq=seq, first_word=first_word),
        )
        for seq, command in enumerate(
            scene_command_specs(scene, control_mode=control_mode),
            start=start_seq,
        )
    )
