from __future__ import annotations

import unittest

from zhiyun_light_control import (
    RuntimeCommandSpec,
    RuntimeFrameSpec,
    Scene,
    scene_command_specs,
    scene_frame_specs,
)
from zhiyun_light_control.protocol import RuntimeCommand, first_frame


class CommandPlanningTests(unittest.TestCase):
    def test_scene_command_specs_expose_ordered_runtime_primitives(self) -> None:
        specs = scene_command_specs(
            Scene(
                obj=7,
                sleep=0,
                brightness=25,
                kelvin=5600,
                red=1,
                green=2,
                blue=3,
                hue=120,
                saturation=0.5,
                intensity=35,
            ),
            control_mode=0x01,
        )

        self.assertEqual(
            [spec.name for spec in specs],
            ["sleep", "brightness", "cct", "rgb", "hsi"],
        )
        self.assertEqual(
            [spec.command for spec in specs],
            [
                RuntimeCommand.SLEEP,
                RuntimeCommand.BRIGHTNESS,
                RuntimeCommand.CCT,
                RuntimeCommand.RGB,
                RuntimeCommand.HSI,
            ],
        )
        self.assertEqual([spec.object_id for spec in specs], [7, 7, 7, 7, 7])
        self.assertEqual([spec.payload[:3] for spec in specs], [b"\x07\x00\x01"] * 5)
        self.assertEqual(specs[1].fields, ("brightness",))
        self.assertEqual(specs[1].to_dict()["command_hex"], "0x1001")
        self.assertTrue(specs[1].to_dict()["requires_control"])

    def test_scene_frame_specs_serialize_with_supplied_sequence_range(self) -> None:
        frames = scene_frame_specs(
            Scene(obj=1, brightness=25, kelvin=5600),
            first_word=0x0301,
            start_seq=9,
        )

        self.assertIsInstance(frames[0], RuntimeFrameSpec)
        self.assertIsInstance(frames[0].command, RuntimeCommandSpec)
        self.assertEqual([frame.seq for frame in frames], [9, 10])
        self.assertEqual([frame.first_word for frame in frames], [0x0301, 0x0301])
        self.assertEqual([first_frame(frame.frame).seq for frame in frames], [9, 10])
        self.assertEqual(
            [first_frame(frame.frame).cmd for frame in frames],
            [RuntimeCommand.BRIGHTNESS, RuntimeCommand.CCT],
        )
        self.assertEqual(frames[0].to_dict()["first_word_hex"], "0x0301")
        self.assertEqual(frames[0].to_dict()["frame_hex"], frames[0].frame.hex())

    def test_scene_command_specs_reject_partial_color_tuples(self) -> None:
        with self.assertRaisesRegex(ValueError, "RGB"):
            scene_command_specs(Scene(obj=1, red=255))

        with self.assertRaisesRegex(ValueError, "HSI"):
            scene_command_specs(Scene(obj=1, hue=120, saturation=0.5))


if __name__ == "__main__":
    unittest.main()
