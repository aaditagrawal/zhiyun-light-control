from __future__ import annotations

import unittest

from zhiyun_light_control import (
    Scene,
    SceneTransition,
    interpolate_scene,
    scene_transition,
)
from zhiyun_light_control.transitions import transition_interval


class TransitionTests(unittest.TestCase):
    def test_scene_transition_interpolates_known_numeric_fields(self) -> None:
        scenes = scene_transition(
            Scene(obj=1, brightness=0, kelvin=3000),
            Scene(obj=1, brightness=100, kelvin=5000),
            steps=4,
        )

        self.assertEqual([scene.brightness for scene in scenes], [25, 50, 75, 100])
        self.assertEqual([scene.kelvin for scene in scenes], [3500, 4000, 4500, 5000])

    def test_unknown_start_values_are_only_sent_at_final_scene(self) -> None:
        scenes = scene_transition(
            Scene(obj=1),
            Scene(obj=1, brightness=70, red=10, green=20, blue=30),
            steps=2,
        )

        self.assertEqual(len(scenes), 1)
        self.assertEqual(scenes[0].brightness, 70)
        self.assertEqual((scenes[0].red, scenes[0].green, scenes[0].blue), (10, 20, 30))

    def test_sleep_is_final_only(self) -> None:
        scenes = scene_transition(Scene(obj=1, sleep=0), Scene(obj=1, sleep=1), steps=3)

        self.assertEqual([scene.sleep for scene in scenes], [1])

    def test_easing_changes_intermediate_position(self) -> None:
        scene = interpolate_scene(
            Scene(obj=1, brightness=0),
            Scene(obj=1, brightness=100),
            0.5,
            easing="ease-in",
        )

        self.assertEqual(scene.brightness, 25)

    def test_transition_dataclass_returns_scenes(self) -> None:
        transition = SceneTransition(
            Scene(obj=1, brightness=0),
            Scene(obj=1, brightness=10),
            steps=2,
            include_start=True,
        )

        self.assertEqual(
            [scene.brightness for scene in transition.scenes()], [0, 5, 10]
        )

    def test_validation_rejects_ambiguous_transitions(self) -> None:
        with self.assertRaisesRegex(ValueError, "steps"):
            scene_transition(Scene(), Scene(), steps=0)
        with self.assertRaisesRegex(ValueError, "object"):
            scene_transition(Scene(obj=1), Scene(obj=2))
        with self.assertRaisesRegex(ValueError, "RGB"):
            scene_transition(Scene(obj=1, red=1), Scene(obj=1, red=2, green=3, blue=4))

    def test_transition_interval_spans_duration_between_updates(self) -> None:
        self.assertEqual(transition_interval(2.0, 5), 0.5)
        self.assertEqual(transition_interval(2.0, 1), 0.0)
        with self.assertRaisesRegex(ValueError, "duration"):
            transition_interval(-1.0, 5)


if __name__ == "__main__":
    unittest.main()
