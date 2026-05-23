from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zhiyun_light_control import Scene
from zhiyun_light_control.presets import (
    PresetError,
    ScenePresetLibrary,
    merge_scene,
    scene_from_mapping,
)


class PresetTests(unittest.TestCase):
    def test_loads_named_scenes_from_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenes.json"
            path.write_text(
                json.dumps(
                    {
                        "scenes": {
                            "key": {"brightness": 35, "kelvin": 5600},
                            "blackout": {"sleep": 1},
                        }
                    }
                ),
                encoding="utf-8",
            )

            library = ScenePresetLibrary.load(path)

        self.assertEqual(library.names(), ["blackout", "key"])
        self.assertEqual(library.get("key"), Scene(obj=1, brightness=35, kelvin=5600))

    def test_rejects_unknown_scene_fields(self) -> None:
        with self.assertRaisesRegex(PresetError, "unknown scene fields"):
            scene_from_mapping({"brightness": 25, "not_a_scene_field": 1})

    def test_merge_scene_keeps_base_obj_unless_explicitly_overridden(self) -> None:
        base = Scene(obj=4, brightness=20, kelvin=5600)
        overrides = Scene(obj=1, brightness=45)

        merged = merge_scene(base, overrides)
        explicit_obj = merge_scene(base, overrides, override_obj=True)

        self.assertEqual(merged, Scene(obj=4, brightness=45, kelvin=5600))
        self.assertEqual(explicit_obj, Scene(obj=1, brightness=45, kelvin=5600))


if __name__ == "__main__":
    unittest.main()
