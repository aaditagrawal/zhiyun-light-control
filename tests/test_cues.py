from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zhiyun_light_control.cues import CueError, CueLibrary, cue_from_mapping


class CueTests(unittest.TestCase):
    def test_loads_named_cues_from_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cues.json"
            path.write_text(
                json.dumps(
                    {
                        "cues": {
                            "intro": {
                                "stop_on_unconfirmed": True,
                                "steps": [
                                    {"scene": {"brightness": 10}},
                                    {"to": {"brightness": 30}, "duration": 1.0},
                                ],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            library = CueLibrary.load(path)

        self.assertEqual(library.names(), ["intro"])
        cue = library.get("intro")
        self.assertTrue(cue["stop_on_unconfirmed"])
        self.assertEqual(len(cue["steps"]), 2)
        self.assertEqual(library.to_dict()["cues"]["intro"]["steps"], cue["steps"])

    def test_top_level_mapping_is_supported(self) -> None:
        library = CueLibrary.from_mapping({"intro": {"steps": [{"scene": {}}]}})

        self.assertEqual(library.names(), ["intro"])

    def test_rejects_invalid_cues(self) -> None:
        with self.assertRaisesRegex(CueError, "non-empty"):
            cue_from_mapping({"steps": []})
        with self.assertRaisesRegex(CueError, "objects"):
            cue_from_mapping({"steps": ["bad"]})
        with self.assertRaisesRegex(CueError, "unknown"):
            CueLibrary.from_mapping({"cues": {}}).get("missing")


if __name__ == "__main__":
    unittest.main()
