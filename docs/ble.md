# BLE

BLE support is experimental and shares the same runtime command layer as USB.
Use read-only discovery and endpoint confirmation before attempting control
primitives.

## Service Profiles

Direct ZY light service used by some Zhiyun devices:

- Service: `6e400001-b5a3-f393-e0a9-e50e24dcca9e`
- Write characteristic: `6e400002-b5a3-f393-e0a9-e50e24dcca9e`
- Notify/read characteristic: `6e400003-b5a3-f393-e0a9-e50e24dcca9e`

Mesh-related services observed in ZY Vega and on the local `PL103_EDFE` G60:

- Provisioning: `00001827-0000-1000-8000-00805f9b34fb`
- Proxy: `00001828-0000-1000-8000-00805f9b34fb`
- Local G60 write characteristic under `1827`: `00002adb-0000-1000-8000-00805f9b34fb`
- Local G60 notify characteristic under `1827`: `00002adc-0000-1000-8000-00805f9b34fb`

Older/alternate YC light service:

- Service: `0000ffe0-0000-1000-8000-00805f9b34fb`
- Write characteristic: `0000ffe1-0000-1000-8000-00805f9b34fb`
- Read characteristic: `0000ffe2-0000-1000-8000-00805f9b34fb`

Older direct ZY service verified on the local `PL103_EDFE` G60:

- Service: `0000fee9-0000-1000-8000-00805f9b34fb`
- Write characteristic: `d44bc439-abfd-45a2-b575-925416129600`
- Notify/read characteristic: `d44bc439-abfd-45a2-b575-925416129601`

The BLE API names these characteristic sets as profiles: `direct`, `legacy`,
and `yc`. One-shot BLE CLI commands and bridge commands accept
`--ble-profile`, plus `--ble-service-uuid`, `--ble-write-uuid`, and
`--ble-notify-uuid` for custom bench routing. `AsyncZhiyunLight.ble()` and
`AsyncZhiyunLight.isolated_ble()` expose the same profile and UUID override
arguments.

## Discovery And Endpoint Confirmation

BLE discovery and endpoint checks:

```sh
uv run zlight scan-ble --name-contains MOLUS
uv run zlight inspect-ble --name-contains MOLUS --json
uv run zlight test-ble-endpoints --name-contains MOLUS --json
```

`zlight inspect-ble` and HTTP `POST /inspect-ble` enumerate the live GATT table
through the selected backend. The HTTP endpoint connects through `worker`,
`macos-app`, or `direct`, resolves by `address` or `name_contains`, and does not
send Zhiyun runtime frames or require `--allow-control`. Use this after
Bluetooth authorization to collect the exact services, write characteristics,
notify characteristics, and properties exposed by a specific firmware before
choosing a custom command profile. The `endpoint_candidates` array ranks
matching built-in profiles ahead of generic write/notify pairs and includes
`cli_args` ready to pass to one-shot BLE commands or bridge startup.

`zlight test-ble-endpoints` and HTTP `POST /test-ble-endpoints` use those
candidates directly and report ACK evidence for each read-only `DEVICE_INFO`
probe. This is the preferred way to promote a guessed write/notify pair into a
confirmed BLE command route before testing control primitives.

## macOS Helper Flow

Direct Swift/Python processes without an app bundle were killed by macOS TCC
before scan results were returned on the local setup. Use:

```sh
uv run zlight ble-helper --ensure --open-settings
```

This builds the cached helper and opens Bluetooth privacy settings for the exact
bundle id used by scans. Use:

```sh
uv run zlight ble-helper --status --json
```

or `GET /devices?include_ble_status=true` to report the current helper
Bluetooth state and authorization status without starting GATT inspection.

When the HTTP bridge is configured as `transport=ble` with
`ble_backend=macos-app`, the embedded `devices.ble.macos_status` field includes
the helper authorization/state report without scanning.

## Worker Isolation

`zlight scan-ble` runs BLE discovery in a worker process by default. This is
deliberate: on the local macOS setup, bleak/CoreBluetooth aborted the
interpreter during scanning. Isolating the scan keeps API users and long-running
bridge processes alive and returns a JSON diagnostic instead.

One-shot BLE command primitives (`probe`, `status`, `register`, `read`, `set`,
and `apply`) also use worker-isolated raw exchanges by default. The worker
connects, writes one frame to the selected profile's write characteristic, waits
for notification data, and returns `{address, rx_hex}` to the parent process.
The parent then parses that response through the same `CommandResult` path used
by USB, so ACKs, timeouts, and echo detection keep the same semantics. Use
`--unsafe-in-process` only when you want the direct bleak transport in the
parent process.

`zlight validate --transport ble` is intentionally guarded by
`--unsafe-in-process` because direct bleak validation runs in the main process.
Use crash-isolated `scan-ble` first, then direct BLE validation only on a
runtime where scanning/connecting is stable.

## Bridge BLE Mode

The local bridges default to USB, but `serve`, `osc-serve`, `artnet-serve`, and
`sacn-serve` also accept `--transport ble` with `--address` or
`--name-contains`. BLE bridge mode uses the same command payload builders as USB
and adapts the async BLE client behind the synchronous stdlib bridge servers.

Like one-shot BLE CLI commands, bridge BLE uses worker-isolated exchanges by
default; pass `--unsafe-in-process` only when direct bleak is stable enough for a
long-running media-control process. The bridge CLI keeps one light context open
by default. USB and direct BLE use that to avoid reconnecting for every request;
worker-isolated BLE still performs each raw BLE exchange in a child process. Use
`--no-persistent-light` to restore per-request contexts.

## Local BLE Field Notes

Native bundled CoreBluetooth inspection with an
`NSBluetoothAlwaysUsageDescription` plist found service `FEE9` plus mesh service
`1827` on the local G60. On `FEE9`, device-info, firmware, and register frames
ACKed; legacy op `0x01` brightness/sleep controls timed out. Writing raw
runtime frames to `1827/2ADB` disconnected immediately.

Current local BLE scan validation:

| Runtime | BLE stack | Result |
| --- | --- | --- |
| Python 3.13 | `bleak 3.0.2`, PyObjC `12.1` | Worker terminates with `SIGABRT` before returning devices |
| Python 3.12 | `bleak 3.0.2`, PyObjC `12.1` | Worker terminates with `SIGABRT` before returning devices |

The failure appears to be below the package's Python transport layer. The
worker wrapper reports `worker_python`, `returncode`, and `signal` so callers can
surface this separately from ordinary zero-device scans.
