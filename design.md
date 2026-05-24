# Design

This project is designed as an SDK first and a set of bridge applications
second. The goal is to let a Python program, a local show-control service, or a
media bridge control Zhiyun MOLUS lights without depending on one specific UI or
operating system.

## Goals

- Provide a programmatic API that can be embedded in automation, video tools,
  and live-control systems.
- Keep USB and BLE behind the same command model so callers can change
  transports without rewriting scene logic.
- Make hardware evidence visible. ACKs, echoes, timeouts, and parsed frames are
  returned to callers.
- Treat setup and runtime as separate phases. Applications should discover and
  validate hardware before exposing control buttons.
- Keep bridge surfaces JSON-friendly so other languages and tools can use the
  local service.
- Avoid firmware flashing. The repository documents updater identity reads, but
  it does not replace Zhiyun's firmware updater.

## Non-Goals

- A polished desktop app.
- Hiding unconfirmed writes behind a success-shaped response.
- Reverse-engineering or distributing firmware packages.
- macOS-only control paths. macOS helpers exist only where the platform makes
  Bluetooth authorization or scan stability harder from a plain Python process.

## Architecture

The package is split into layers:

- `protocol.py` builds and parses frames, command payloads, CRCs, runtime
  commands, and updater identity commands.
- `mesh.py` builds and parses the small Bluetooth Mesh provisioning PDUs used
  to prove whether a PL-series light is still unprovisioned.
- `models.py` defines public command and scene result objects.
- `transports/usb.py` implements USB CDC serial exchange with port discovery and
  locking.
- `transports/ble.py` implements BLE exchange with direct, worker, and macOS
  helper-backed paths.
- `client.py` and `async_client.py` provide low-level direct light clients.
- `bridge.py` defines `LightConnectionConfig`, factory helpers, and persistent
  connection support.
- `controller.py` provides `LightController` and `AsyncLightController` for
  scenes, presets, cues, transitions, plans, and state tracking.
- `integration.py` adds discovery, readiness, validation, setup profiles, and
  application-oriented snapshots around the controllers.
- `rig.py` and `project.py` model named fixtures, fixture groups, presets, cues,
  and multi-fixture command dispatch.
- `server.py`, `http_client.py`, `osc.py`, `artnet.py`, and `sacn.py` expose
  local bridge APIs for external tools.
- `cli.py` is a thin command-line surface over the same SDK and bridge layers.

## Command Evidence

Every command produces evidence about what happened on the transport:

- `acknowledged`: a matching ACK frame with a valid CRC was received.
- `sent_no_response`: a frame was transmitted and no response was seen.
- `echoed_write`: the device or route echoed the write frame.
- `response_without_matching_ack`: bytes arrived, but not as a matching ACK.

Control APIs can run in permissive mode for discovery or in confirmed mode for
production. Confirmed mode raises `UnconfirmedCommandError` or bridge-specific
exceptions instead of silently accepting a timeout.

This is important because the verified G60 firmware path ACKs global reads and
registration, while object-scoped writes still need validation across transports
and firmware versions.

## Setup Profiles and Readiness

The integration layer can produce a setup report that records discovered
devices, selected connection config, validation categories, ready capabilities,
warnings, and unconfirmed primitive names. A `LightSetupProfile` turns that
report into portable JSON.

Applications can then require capabilities such as:

- `read_status`
- `object_reads`
- `control_setup`
- `control_writes`

The design keeps a setup profile separate from a runtime transport config. A
config says how to connect. A profile says what was proven about that connection
at setup time.

## Transport Choices

USB is the most deterministic path and is the default transport. The G60 appears
as a USB CDC serial device while externally powered by PD.

BLE is optional because it adds platform dependencies and host-specific
Bluetooth permission behavior. The package keeps BLE behind the `ble` extra and
supports multiple backends:

- `worker`: runs BLE operations in a separate Python process for crash
  isolation.
- `direct`: runs `bleak` in-process for environments where that is stable.
- `macos-app`: uses a small CoreBluetooth helper for macOS authorization and
  scanning edge cases.

The public SDK is not macOS-specific; the helper is an implementation detail for
one backend.

For the local G60, BLE control is a setup pipeline rather than a single
runtime-frame write. Official Vega code first talks to mesh provisioning service
`1827`, then reconnects through mesh proxy service `1828`, adds an application
key, reads identity/status over the Zhiyun `FEE9` service, runs native
local-control registration, and only then sends brightness/CCT/sleep packets.
The SDK therefore treats `mesh-probe`, `mesh-handshake`, `mesh-session`, and
`mesh.py` as setup/discovery primitives, not as solved output control.
`mesh-handshake` extends the verified invite/capabilities probe with a no-OOB
provisioning start and generated P-256 provisioner public key over a single BLE
connection. `mesh-session` adds a file-IPC mode to the macOS helper so Python
can compute confirmation/random frames from the live provisionee public key
without dropping the BLE bearer. `mesh.py` owns the Bluetooth Mesh provisioning
crypto needed for the next state transition: confirmation inputs, AES-CMAC
based salts/keys, provisioner confirmation/random, session nonce/key, device
key, and encrypted provisioning data.

## Planning and Transitions

Scenes are compiled into command plans before they are sent. Plans can be saved
as JSON bundles and executed later over USB, BLE, or the HTTP bridge.

This split makes preview, audit, and cross-process execution possible. It also
lets bridge clients submit deterministic frame bundles instead of relying on the
server to reinterpret scene intent.

Transitions are represented as sequences of scene plans. They deliberately reuse
the same primitive command executor, so direct SDK, HTTP, OSC, and rig flows
share confirmation behavior.

## State Model

The project tracks requested scene state, not measured light output. A state
update means the SDK attempted a scene command and recorded the resulting
command evidence.

State history and event streams exist for dashboards and control surfaces that
need to recover after reconnecting. Event cursors are monotonically increasing
versions, and rig APIs expose per-fixture cursor maps.

## Bridge API Design

The HTTP bridge uses only the Python standard library so it can run in small
local environments. The companion `LightBridgeClient` is also stdlib-only.

Bridge responses mirror SDK semantics:

- readiness endpoints report whether controls should be exposed;
- control endpoints include `applied`, status, and command evidence;
- event endpoints stream requested-state changes;
- metadata endpoints expose manifest, capabilities, and OpenAPI-style payloads
  without opening hardware.

OSC, Art-Net, and sACN bridges translate media-tool protocols into the same
scene/control layer instead of implementing separate hardware paths.

## Rig and Project Files

Rig and project definitions are plain JSON so they can be generated by other
tools and checked into repositories. Fixture entries carry connection config,
object id, tags, and optional setup profile evidence.

The rig layer owns fixture dispatch and keeps one controller/state tracker per
fixture. This lets multi-light systems route commands independently while still
sharing presets, cues, validation summaries, and state event helpers.

## Safety Defaults

- Control writes require explicit CLI confirmation through flags such as
  `--yes` or bridge startup with `--allow-control`.
- Validation write probes require `--allow-control`.
- The setup/profile APIs let applications refuse controls until required
  capabilities have been proven.
- Firmware flashing is out of scope.
- BLE is optional and crash-isolated by default.

## Tooling

The repository uses `uv` for package management and command execution. CI runs:

- `uv sync --extra ble --extra dev`
- `uv run ruff check .`
- `uv run pytest -q`
- `uv run python -m compileall -q src tests examples/sdk_quickstart.py`
- an explicit scan to reject `typing.Any` in source and tests
- `uv build`

The package ships `py.typed` and keeps examples in the same checks as the SDK so
documentation examples stay close to the actual API.
