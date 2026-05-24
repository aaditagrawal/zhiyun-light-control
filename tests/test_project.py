from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zhiyun_light_control import (
    LightConnectionConfig,
    LightProject,
    LightProjectError,
    LightSetupProfile,
    light_project_from_mapping,
    light_project_to_json,
    load_light_project,
    save_light_project,
    save_light_setup_profile,
)


class LightProjectTests(unittest.TestCase):
    def test_loads_show_directory_defaults_into_sync_and_async_rigs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(
                root / "rig.json",
                {
                    "fixtures": {
                        "key": {
                            "transport": "usb",
                            "port": "/dev/cu.key",
                            "obj": 2,
                            "tags": ["stage"],
                        }
                    }
                },
            )
            write_json(
                root / "scenes.json",
                {"scenes": {"look": {"brightness": 25, "kelvin": 5600}}},
            )
            write_json(
                root / "cues.json",
                {
                    "cues": {
                        "intro": {
                            "steps": [{"preset": "look"}],
                            "stop_on_unconfirmed": True,
                        }
                    }
                },
            )

            project = load_light_project(root)
            rig = project.to_rig()
            async_rig = project.to_async_rig()
            plan = rig.plan_named_cue_all("intro")
            async_plan = async_rig.plan_named_cue_all("intro")

            self.assertEqual(project.fixture_names(), ("key",))
            self.assertEqual(project.preset_names(), ["look"])
            self.assertEqual(project.cue_names(), ["intro"])
            self.assertEqual(project.to_dict()["rig_path"], "rig.json")
            self.assertEqual(rig.fixture("key").obj, 2)
            self.assertEqual(plan["fixtures"]["key"]["scene"]["brightness"], 25.0)
            self.assertEqual(async_plan["fixtures"]["key"]["scene"]["kelvin"], 5600)

    def test_project_loader_keeps_rig_relative_profile_paths_usable(self) -> None:
        profile = setup_profile(
            LightConnectionConfig.usb(port="/dev/cu.project", persistent=True)
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profiles = root / "profiles"
            profiles.mkdir()
            save_light_setup_profile(profile, profiles / "key.json")
            write_json(
                root / "project.json",
                {
                    "kind": "light-project",
                    "rig_path": "rig.json",
                    "presets_path": "scenes.json",
                },
            )
            write_json(
                root / "rig.json",
                {
                    "fixtures": {
                        "key": {
                            "profile_path": "profiles/key.json",
                            "obj": 3,
                        }
                    }
                },
            )
            write_json(root / "scenes.json", {"scenes": {"look": {"sleep": 0}}})

            rig = load_light_project(root).to_rig()

        self.assertEqual(rig.fixture("key").config.port, "/dev/cu.project")
        self.assertEqual(rig.fixture("key").obj, 3)
        self.assertTrue(rig.require_setup_profile("key", "read_status").ok)
        self.assertEqual(rig.plan_preset("key", "look")["scene"]["sleep"], 0)

    def test_project_round_trips_split_files(self) -> None:
        project = light_project_from_mapping(
            {
                "rig": {
                    "fixtures": {
                        "key": {
                            "transport": "usb",
                            "port": "/dev/cu.key",
                            "tags": ["stage"],
                        }
                    }
                },
                "presets": {
                    "scenes": {
                        "look": {
                            "brightness": 35,
                        }
                    }
                },
                "cues": {
                    "cues": {
                        "intro": {
                            "steps": [{"preset": "look"}],
                        }
                    }
                },
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            output = save_light_project(project, Path(directory) / "show")
            loaded = LightProject.load(Path(directory) / "show")
            project_json = json.loads(
                Path(output["project_path"]).read_text(encoding="utf-8")
            )

            self.assertEqual(project_json["rig_path"], "rig.json")
            self.assertEqual(project_json["presets_path"], "scenes.json")
            self.assertEqual(project_json["cues_path"], "cues.json")
            self.assertIn('"kind": "light-project"', light_project_to_json(loaded))
            self.assertEqual(loaded.fixture_names(), ("key",))
            self.assertEqual(loaded.preset_names(), ["look"])
            self.assertEqual(loaded.cue_names(), ["intro"])
            self.assertTrue(loaded.summary()["fixtures"])

    def test_project_mapping_requires_a_rig(self) -> None:
        with self.assertRaisesRegex(LightProjectError, "missing rig"):
            light_project_from_mapping({"kind": "light-project"})


def setup_profile(config: LightConnectionConfig) -> LightSetupProfile:
    return LightSetupProfile.from_setup_report(
        {
            "api": "zhiyun-light-control",
            "ok": True,
            "config": config.to_dict(),
            "route_confirmed": True,
            "status_ok": True,
            "ready_for": {"read_status": True},
            "validation_ready_for": {"control_writes": False},
            "validation_unconfirmed": ["set_brightness"],
        }
    )


def write_json(path: Path, payload: object) -> None:
    path.write_text(
        f"{json.dumps(payload, indent=2, sort_keys=True)}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
