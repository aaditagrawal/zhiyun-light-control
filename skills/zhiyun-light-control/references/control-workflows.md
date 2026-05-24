# Control Workflows

## Status Before Control

Run device discovery and ACK-backed status before sending control commands:

```sh
uv run zlight devices --include-ble-status --json
uv run zlight status --transport usb --json
```

For the local G60, a healthy USB status report includes firmware `1.6.4`,
generation `pl103`, and ACKs for device info, firmware, voltage, device id, and
updater identity reads.

For BLE on macOS, first verify helper authorization and endpoint evidence:

```sh
uv run zlight ble-helper --status --json
uv run zlight scan-ble --backend macos-app --timeout 10
uv run zlight test-ble-endpoints --backend macos-app --address <BLE-UUID> --timeout 12 --json
```

The local G60 advertises as `PL103_EDFE`. The confirmed read-only endpoint is
`fee9` with write `d44bc439-abfd-45a2-b575-925416129600` and notify
`d44bc439-abfd-45a2-b575-925416129601`. It ACKs global identity/status reads,
but object reads and control writes still time out.

## Interpreting Results

- `acknowledged`: confirmed command response matched the sent command.
- `sent_no_response`: bytes were sent, but no matching frame came back.
- `echoed_write`: the device echoed the exact write frame. This is unconfirmed
  at the protocol layer, but it can still be physically effectful on the local
  G60 `0x0301` route.

Do not collapse these states into a single success/failure boolean in agent
output. Report both the transport evidence and physical observation.

## Local G60 Stable Light

The physically observed stable look is `2700K` at `20%`. The exact minimal
route is still being narrowed down, so use a small fanout across responsive
`0x0301` candidates:

```sh
for obj in 0 1 2; do
  for mode in 0x33 0x01; do
    uv run zlight apply --transport usb --first-word 0x0301 --control-mode "$mode" --obj "$obj" --accept-echo --brightness 20 --kelvin 2700 --yes
  done
done
```

If a USB lock timeout occurs, another `zlight` process is still holding the
serial lock. Wait for it to finish, then retry. Do not kill unrelated processes
unless the user asks.

`--accept-echo` is only an exit-code policy. It does not convert echo responses
into ACKs, and integrations should still surface `acknowledged: false`.

## Discovery Matrix

Read-only matrix:

```sh
uv run zlight discover-usb --g60-matrix --json
```

State-changing sleep probe:

```sh
uv run zlight discover-usb --g60-matrix --allow-control --control-kinds sleep --control-first-words 0x0301 --control-object-ids 1 --sleep 1 --json
```

The sleep probe has physically blinked the local G60 while reporting
`echoed_write`. A broader brightness/CCT pass reached `2700K` at `20%`, also
while reporting `echoed_write`.

## Dynamic Mesh Session

Use this when continuing BLE Mesh provisioning past public-key exchange:

```sh
uv run --extra mesh zlight mesh-session --name-contains PL103 --json
```

This keeps one CoreBluetooth helper connection open and lets Python calculate
the confirmation/random frames from the session-specific provisionee public key.
It stops before provisioning data, so it should not persistently attach the G60
to a generated mesh network.

## SDK Use

Prefer the high-level SDK for integrations when the standard ACK-backed route is
appropriate:

```python
from zhiyun_light_control import LightConnectionConfig, LightController, Scene

config = LightConnectionConfig.usb()
with LightController(config) as controller:
    result = controller.apply_scene(Scene(obj=1, brightness=35, kelvin=5600))
```

For the current G60 `0x0301` route, use frame planning/execution so the first
word is explicit. Repeat across candidate object ids and operation bytes until
the minimal route is isolated:

```python
from zhiyun_light_control import (
    LightConnectionConfig,
    Scene,
    execute_frame_plan,
    open_light,
    scene_command_plan,
)

config = LightConnectionConfig.usb()
with open_light(config) as light:
    for obj in (0, 1, 2):
        for control_mode in (0x33, 0x01):
            plan = scene_command_plan(
                Scene(obj=obj, brightness=20, kelvin=2700),
                control_mode=control_mode,
                first_word=0x0301,
            )
            results = execute_frame_plan(light, plan)
```

Report `result.transport_status` values to the caller and ask for physical
confirmation when results are `echoed_write`.
