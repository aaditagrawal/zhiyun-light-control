# Python SDK

The SDK exposes synchronous and asynchronous clients, transport-neutral
connection configs, setup profiles, state tracking, and planning helpers.

## Direct Controller

Read-only direct USB probe:

```python
from zhiyun_light_control import LightConnectionConfig, LightController

config = LightConnectionConfig.usb()

with LightController(config) as light:
    probe = light.probe()
    print(probe.to_dict())
```

Apply a scene only after you have validated that control writes work for your
transport and firmware:

```python
from zhiyun_light_control import LightConnectionConfig, LightController, Scene

config = LightConnectionConfig.usb(port="/dev/cu.usbmodem1101")

with LightController(config, require_acknowledged=True) as light:
    light.register(device_id=0, group_id=0)
    result = light.apply_scene(Scene(obj=1, brightness=35, kelvin=5600))
    print(result["applied"])
```

See [../examples/sdk_quickstart.py](../examples/sdk_quickstart.py) for a fuller
discover, persist, validate, and control flow.

## Integration Setup

Use the higher-level integration layer when an application needs setup evidence
before exposing controls:

```python
from zhiyun_light_control import LightConnectionConfig, LightIntegration

integration = LightIntegration(LightConnectionConfig.usb())
setup = integration.setup_report(include_ble_status=True)

if setup["ready"]:
    print(setup["summary"])
```

For transport setup, embedded hosts can call `connection_candidates` and
`with_best_connection` to derive ranked USB/BLE `LightConnectionConfig` objects
from local discovery, or `ble_endpoint_connection_candidates` and
`with_ble_endpoint_connection` to derive BLE configs from endpoint-test evidence.
Hosts that need a route proven by a read-only status ACK can call
`probe_connection_candidates` for `status_probe` evidence on each route, or
`with_confirmed_connection` to select the best route whose identity/status probe
succeeds.

That status confirmation is separate from control-write confirmation; use
validation with `allow_control` before enabling production writes.

`setup_report` combines the common host setup flow into one SDK payload:
status-probed routes, the selected config, readiness, validation readiness, and
unconfirmed primitive names. Reports include `capabilities`,
`primitive_ready_for`, and `primitive_readiness`, which are plain JSON
projections of the SDK primitive gates. That lets a controller arm or block
`status`, `read_brightness`, `set_brightness`, cues, scenes, and transitions
without duplicating the primitive-to-capability map.

`LightIntegration.connection_report(include_ble=true)` and the async equivalent
provide discovered USB/BLE devices, ranked candidates, status-probe evidence,
the selected SDK config, and explicit BLE blockers such as macOS Bluetooth
authorization failures.

## Setup Profiles

`setup_profile` wraps setup evidence in a portable `LightSetupProfile` JSON
object with the selected `LightConnectionConfig`, summary booleans, validation
capabilities, and unconfirmed primitive names. Use
`save_light_setup_profile`/`load_light_setup_profile` when a host needs to carry
the same setup evidence between processes or operating systems.

Profiles expose `ready`, `unready_capabilities`, and `require_ready`, and the
integration facades expose `from_setup_profile`, `from_setup_profile_file`, and
`with_setup_profile` so host applications can rebuild SDK clients from saved
evidence while failing fast on missing capabilities.

Profiles also expose primitive-level checks. `primitive_ready("set_brightness")`
and `require_primitive("read_brightness")` map public SDK operations to the
evidence capabilities they require: `control_writes`, `object_reads`,
`control_setup`, or `read_status`. Standalone helpers
`setup_profile_primitive_readiness` and
`setup_profile_primitive_readiness_map` consume either a `LightSetupProfile`,
profile JSON, or a raw setup report.

`save_light_connection_config` and `load_light_connection_config` serialize the
same config shape to JSON so setup tools can persist a confirmed USB port or BLE
endpoint profile for later SDK sessions.

`LightIntegration` and `AsyncLightIntegration` instances created from a profile
retain that evidence as `setup_profile_evidence` and expose matching
`setup_profile_primitive_ready`/`require_setup_profile_primitive` helpers. Their
direct primitive helpers also accept `require_setup_profile=true`, and
`with_setup_profile(..., require_controls=true)` or
`from_setup_profile(..., require_controls=true)` makes that saved-profile gate
the default before opening USB/BLE.

Embedded integration snapshots add a top-level `client` object containing the
active guard mode and the same compact profile summary used by bridge clients.
That lets a setup dashboard consume one snapshot and confirm whether the host is
currently using saved setup evidence.

## Direct Integration Helpers

Embedded hosts that already use `LightIntegration` or `AsyncLightIntegration`
can attach preset/cue libraries there and call `plan_*` helpers before opening
USB or BLE. The same integration objects also expose direct primitive helpers
for `register`, `read_brightness`, `read_cct`, `read_sleep`, `set_brightness`,
`set_cct`, `set_sleep`, `set_rgb`, and `set_hsi`, plus `apply_scene`,
`apply_preset`, `run_sequence`, `run_cue`, and `run_named_cue` control helpers
with opt-in readiness checks.

Primitive responses preserve raw `CommandResult` evidence and add decoded
functional payload fields when an ACK carries brightness, CCT, sleep, RGB, or
HSI values. Direct integration control records into the integration state
tracker so subsequent `state_snapshot`, `state_history`, and readiness/snapshot
payloads include the latest control evidence by default.

One-shot integration helpers close internally owned light factories after each
call; long-lived media hosts should keep an explicit controller or injected
factory when they want connection persistence across commands.

## Planning And Execution

`LightController.plan_scene()`, `plan_preset()`, `plan_transition()`, and
`plan_sequence()` resolve plans without opening USB/BLE. The matching
`LightBridgeClient` helpers do the same when the HTTP bridge is the process
boundary.

`LightIntegration.execute_plan(...)` and
`AsyncLightIntegration.execute_plan(...)` accept loaded serialized plan bundles
as well as raw plan dictionaries, so a planner process can persist JSON and a
runtime integration process can execute it without manually unwrapping the
envelope.

Low-level protocol callers can import `RuntimeCommand`, `UpdaterCommand`,
`build_runtime_frame`, `first_frame`, payload builders, and functional payload
parsers directly from `zhiyun_light_control` when building custom transports or
external SDK adapters.
