# Rigs And Projects

Rig files let applications name fixtures, mix USB and BLE transports, attach
setup evidence, and run grouped plans.

## Rig File

Rig files are JSON and can mix USB and BLE fixtures:

```json
{
  "fixtures": {
    "key": {"transport": "usb", "obj": 1, "tags": ["set"]},
    "rim": {"transport": "ble", "name_contains": "MOLUS", "obj": 1}
  },
  "presets": {
    "scenes": {
      "interview": {"brightness": 35, "kelvin": 5600},
      "blackout": {"sleep": 1}
    }
  },
  "require_acknowledged": true
}
```

Programmatic use:

```python
from zhiyun_light_control import LightRig

rig = LightRig.load("examples/rig.json")
print(rig.setup_report_all())
```

## Setup Evidence

Rig fixtures can reference setup evidence with `profile_path` or an inline
`profile` object. The rig loader resolves relative `profile_path` values beside
the rig JSON file, uses the profile's selected config for the fixture, and keeps
the profile available through `rig.setup_profile(name)` and
`rig.require_setup_profile(name, ...)`.

`LightRig` and `AsyncLightRig` accept
`require_setup_profile_controls=true`, which makes fixture apply helpers call
`require_setup_profile_primitive` before opening the underlying USB/BLE
transport. Per-call `require_setup_profile=true` is available on rig apply
helpers for hosts that only want this guard on selected cues.

`setup_profile_summary_all(primitives=...)` gives hosts a no-I/O rig capability
matrix from saved setup evidence, including missing profiles and unready
primitive names per fixture.

`connection_report_all(include_ble=true)` provides the same status-probed route
report per fixture, including selected config and BLE blockers, for setup tools
that need to arm or save a full multi-light rig.

`setup_report_all(include_ble=true)` then runs the full setup evidence pipeline
per fixture: route selection, selected config, readiness, validation readiness,
and primitive capability maps keyed by fixture name.

`setup_profiles_all(...)` and `rig_setup_profiles_from_report(...)` convert
those per-fixture reports into `LightSetupProfile` objects. `with_setup_profiles`
attaches them back to a sync or async rig so `save_rig` can persist a reusable
profiled rig definition.

`rig_profile_bundle_mapping(...)` and `save_rig_profile_bundle(...)` produce the
split-file variant: a rig JSON document with relative `profile_path` entries and
separate profile JSON files beside it.

`setup_profile_bundle(...)` and `save_setup_profile_bundle(...)` are the
one-call setup variants on sync and async rigs: collect evidence, materialize
profiles, attach them to the rig, and optionally write the split-file bundle.

## Direct Rig Helpers

Rigs expose direct named-fixture primitives:

- `register`
- `read_brightness`
- `read_cct`
- `read_sleep`
- `set_brightness`
- `set_cct`
- `set_sleep`
- `set_rgb`
- `set_hsi`

They also expose group write helpers such as `set_brightness_all` and
`set_sleep_all`; async rigs expose matching awaitable methods.

## Rig Planning And Execution

Rig planning helpers (`plan_scene`, `plan_preset`, `plan_transition`,
`plan_sequence`, `plan_named_cue`, and their group variants) run without opening
USB/BLE and return exact per-fixture command plans.

`execute_plan` sends serialized frame bytes from scene, preset, transition,
sequence, or cue command plans, and `execute_plan_map` does the same for grouped
`plan_all`, `plan_scene_map`, or `plan_named_cue_all` results. This gives host
applications a plan -> inspect/schedule -> execute pipeline without coupling to
a specific transport backend.

`serialized_plan_bundle(...)` wraps serialized plan dictionaries in a portable
JSON envelope with schema version, frame summary, and original plan.
`SerializedPlanBundle.save(...)`, `save_serialized_plan_bundle(...)`, and
`load_serialized_plan_bundle(...)` are the disk handoff helpers, and
`serialized_frame_commands(...)` can flatten both single-fixture and grouped rig
plans.

`execute_serialized_frame_plan(...)` and
`execute_async_serialized_frame_plan(...)` accept either the bundle object or a
loaded bundle mapping for single-target plans; grouped rig plans keep using
`execute_plan_map(...)` so each fixture is routed to its configured light.
Controller, integration, and rig execution helpers also accept loaded bundle
objects directly.

This keeps the media-control boundary as JSON until the final USB/BLE runtime
process sends the exact planned bytes.

## Projects

`LightProject` and `load_light_project(...)` are the directory-level SDK bundle
helpers for host applications. A `project.json` can reference `rig.json`,
`scenes.json`, and `cues.json`, and the project object can materialize either a
sync `LightRig` or async `AsyncLightRig` with those libraries wired in.

See [../examples/project.json](../examples/project.json) and
[../examples/rig.json](../examples/rig.json) for sample files.
