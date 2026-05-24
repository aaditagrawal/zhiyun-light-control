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

This builds a cached compiled Swift `.app` helper, ad-hoc signs the full app
bundle, and opens Bluetooth privacy settings for the exact bundle id used by
scans. Older script-based or unsigned helper builds could report
`not_determined` plus `unauthorized` without appearing in Settings; delete the
cached app or rerun `--ensure` with current code to rebuild and sign the helper.
Use:

```sh
uv run zlight ble-helper --ensure --authorize --timeout 60 --json
uv run zlight ble-helper --status --json
```

The `--authorize` mode brings the helper app forward and keeps it alive while
macOS asks for Bluetooth access. `--status` or
`GET /devices?include_ble_status=true` reports the current helper Bluetooth
state and authorization status without starting GATT inspection.
If status reports `pending_action: "allow_bluetooth_prompt"`, macOS has not
completed the Bluetooth authorization decision yet; click Allow on the
`ZhiyunBleScan` Bluetooth prompt or allow it in the Bluetooth privacy pane,
then rerun the scan.

On the local Mac, System Settings can show `ZhiyunBleScan` enabled while a fresh
helper run still reports `authorization=not_determined` and
`Bluetooth state unknown: 0`. Treat that combination as a TCC/helper identity
mismatch; it is not evidence that BLE scanning or mesh provisioning is ready.

If macOS appears to have stale TCC state for the default helper, build a fresh
helper identity for diagnosis:

```sh
uv run zlight ble-helper --ensure --authorize \
  --bundle-name ZhiyunBleScanFresh \
  --bundle-id local.zhiyun-light-control.ble-scan-fresh \
  --timeout 60 --json
```

The same identity can be reused by macOS-app scans or mesh sessions with:

```sh
export ZLIGHT_BLE_HELPER_NAME=ZhiyunBleScanFresh
export ZLIGHT_BLE_HELPER_BUNDLE_ID=local.zhiyun-light-control.ble-scan-fresh
```

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
`1827` on the local G60. After switching the helper to a compiled Swift app,
macOS authorization reached `allowed`, scan found `PL103_EDFE`, and endpoint
testing confirmed `FEE9` (`d44bc439...9600` write, `...9601` notify) ACKs the
read-only `DEVICE_INFO` frame. The `1827/2ADB/2ADC` endpoint timed out for that
same read-only probe.

On the confirmed `FEE9` endpoint, global status reads ACKed for device info,
firmware, voltage, and device id. Updater commands, object reads, register, and
brightness/CCT/sleep writes timed out. That means the current BLE route is a
confirmed identity/status route, not a confirmed control route.

## Mesh Provisioning Breakpoint

Official ZY Vega `1.1.7` uses two BLE layers for PL-series lights:

1. Unprovisioned lights expose Bluetooth Mesh Provisioning service `1827` with
   Data In `2ADB` and Data Out `2ADC`.
2. After provisioning, lights expose Mesh Proxy service `1828` with Data In
   `2ADD` and Data Out `2ADE`.
3. Vega then adds an app key, reconnects through the proxy path, reads device
   info / firmware / voltage, calls the native local-control register, and only
   then sends native `ZYLightClient` control packets for brightness, CCT, sleep,
   RGB, HSI, and effects.

The local G60 is currently at the first stage: it advertises `1827` and not
`1828`. A raw runtime `DEVICE_INFO` frame is not valid on `1827`, which is why
earlier endpoint tests timed out. The verified mesh handshake primitive is:

```sh
uv run zlight mesh-probe --backend macos-app --name-contains PL103 --json
```

On the attached G60, this sends PB-GATT provisioning invite `030005` and
receives capabilities `03010100010001000000000000`: one element, FIPS P-256
ECDH, no public-key OOB, static OOB supported, no input OOB, and no output OOB.
This proves the mesh provisioning bearer is reachable. It does not provision
the device or solve brightness/CCT control by itself.

The next implemented probe is:

```sh
uv run --extra mesh zlight mesh-handshake --name-contains PL103 --json
```

It sends invite `030005`, no-OOB provisioning start `03020000000000`, and a
generated P-256 provisioner public key in one CoreBluetooth connection. The
expected next device response is a provisioning public key PDU. On macOS this
depends on the rebuilt `ZhiyunBleScan` helper being allowed in Privacy &
Security > Bluetooth.

The dynamic follow-up probe is:

```sh
uv run --extra mesh zlight mesh-session --name-contains PL103 --json
```

It uses the macOS helper's file-IPC mode to keep one CoreBluetooth connection
open while Python computes the confirmation and random frames from the live
provisionee public key. This is required because the G60 changes its public key
between provisioning sessions. The command stops before provisioning data by
default; sending provisioning data is persistent mesh setup and should be a
separate, explicit operation.

The persistent version keeps the same connection open and sends the encrypted
Provisioning Data PDU after the provisionee random is verified:

```sh
uv run --extra mesh zlight mesh-session --name-contains PL103 \
  --provision --network-key-hex "$NETWORK_KEY_HEX" --yes --json
```

Successful provisioning reports `provisioning_complete=true` and
`provisioning_complete_hex=0308`. The output `provisioning_plan.network_key_hex`
and `session_secrets.device_key_hex` are the inputs for `mesh-config-send`.

Once a session completes, save the JSON output and build the next send artifact
offline:

```sh
uv run --extra mesh zlight mesh-provision-plan --session-json session.json --json
```

The output includes `provisioning_data_pdu_hex`, generated or supplied
`network_key_hex`, the unicast address, and the derived session/device keys.
This command is intentionally non-mutating; it only proves the next instruction
can be constructed from the live session transcript.

The post-provisioning config plan can be built without a live BLE session:

```sh
uv run zlight mesh-setup-plan --json
```

It emits a generated Zhiyun-compatible CDB skeleton plus the exact config access
payloads Vega sends after provisioning: `8008ff`, `800c`, `80240a`, and app key
add `00 + key-index-pack + app-key`. Those payloads still need to be encrypted
and sent over the Mesh Proxy bearer `1828/2ADD/2ADE`; the command does not
mutate the light.

If the provisioning transcript has produced a light device key, pass it back in:

```sh
uv run zlight mesh-setup-plan --device-key-hex "$DEVICE_KEY_HEX" --json
```

The output then includes `proxy_pdu_sequence`: complete Mesh Proxy Network PDUs
for the config messages, including the segmented app-key-add message. These are
ready for the `1828/2ADD` write characteristic once the light advertises Mesh
Proxy and the local BLE backend is authorized.

The guarded sender for those PDUs is:

```sh
uv run zlight mesh-config-send \
  --network-key-hex "$NETWORK_KEY_HEX" \
  --app-key-hex "$APP_KEY_HEX" \
  --device-key-hex "$DEVICE_KEY_HEX" \
  --ble-backend macos-app \
  --yes --json
```

It uses the Mesh Proxy profile (`1828`, Data In `2ADD`, Data Out `2ADE`) and
returns the per-write exchange evidence. Use `--ble-backend worker` on platforms
where the standard BLE worker can hold the sequence connection.

If `mesh-session` cannot find the light, run:

```sh
uv run zlight scan-ble --backend macos-app --include-all --timeout 10
```

This bypasses the Zhiyun advertisement filter and returns every BLE
advertisement the helper receives. Zero devices from this broad scan means the
macOS helper is not receiving advertisements at all or the nearby devices are
not advertising during the scan window.

The SDK also implements the provisioning cryptographic primitives needed after
public-key exchange: confirmation inputs, confirmation key, provisioner
confirmation, provisioner random, provisioning salt, session key, session nonce,
device key, and AES-CCM encrypted provisioning data. These match the Nordic
Mesh code bundled inside Zhiyun Vega. They are exposed as Python helpers so the
next hardware step can continue with confirmation/random and provisioning data
once a single authorized BLE session can receive the provisionee public key.

The provisionee key must be treated as session-specific. A live replay of a
confirmation computed from a previous public key returned `030904`, a
provisioning failure with reason `confirmation_failed`.

Current local BLE scan validation:

| Runtime | BLE stack | Result |
| --- | --- | --- |
| Python 3.13 | `bleak 3.0.2`, PyObjC `12.1` | Worker terminates with `SIGABRT` before returning devices |
| Python 3.12 | `bleak 3.0.2`, PyObjC `12.1` | Worker terminates with `SIGABRT` before returning devices |

The failure appears to be below the package's Python transport layer. The
worker wrapper reports `worker_python`, `returncode`, and `signal` so callers can
surface this separately from ordinary zero-device scans.
