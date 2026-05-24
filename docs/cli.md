# CLI

The `zlight` CLI provides read-only probes, direct primitive control, dry-run
planning, metadata export, transport discovery, and bridge servers.

## Discovery And Readiness

List local USB ports and optional BLE status:

```sh
uv run zlight devices --include-ble-status --json
```

Read ACK-backed status:

```sh
uv run zlight status --transport usb --json
```

Run a controller preflight without starting the HTTP bridge:

```sh
uv run zlight ready --transport usb --json
```

`zlight ready` performs the same read-only transport preflight as HTTP
`/ready`. It returns `ready_for`, `requirements`, `warnings`, and `actions`, and
includes macOS helper authorization state when run with
`--transport ble --ble-backend macos-app`. It does not start the HTTP bridge,
run a BLE scan, or send control writes.

Run a structured validation report:

```sh
uv run zlight validate --transport usb --include-object-reads --json
```

Control probes are opt-in:

```sh
uv run zlight validate --transport usb --allow-control --include-object-reads --json
```

Use `--strict` in automation when unconfirmed attempted primitives should fail
the run.

## Direct Primitives

Low-level primitives:

```sh
uv run zlight register --transport usb --yes
uv run zlight read brightness --transport usb --obj 1
uv run zlight set brightness --transport usb --obj 1 --value 35 --yes
uv run zlight apply --transport usb --brightness 35 --kelvin 5600 --yes
```

The direct commands `register`, `read`, `set`, and `apply` use the same ACK
definition as the SDK. They print the full `CommandResult` payload either way,
but unacknowledged transmissions return exit code `1`.

`apply` can also send exact scene frames over an experimental first-word route:

```sh
uv run zlight apply --transport usb --first-word 0x0301 --accept-echo --sleep 0 --brightness 50 --kelvin 3200 --yes
```

On the locally tested G60 firmware `1.6.4`, this route can return
`echoed_write` rather than an ACK. Treat that as transport evidence only and
ask for physical confirmation before reporting user-visible success.
`--accept-echo` only changes the process exit code for exact echoes; the JSON
still reports `acknowledged: false`.

`zlight frame` exposes the lower-level `exchange_frame()` primitive for direct
bench checks:

```sh
uv run zlight frame --first-word 0x0100 --command 0x2001 --payload-hex "" --yes
```

It requires `--yes` and exits non-zero unless the light returns a matching ACK.

## Plans And Execution

Build a plan without opening hardware:

```sh
uv run zlight plan --brightness 35 --kelvin 5600 --output scene-plan.json
```

Execute the plan later over USB/BLE or through the HTTP bridge:

```sh
uv run zlight execute-plan scene-plan.json --transport usb --yes
```

Plans preserve the exact frame bytes to send later, including sequence number,
first word, command, and payload bytes.

## Presets And Cues

Named presets are loaded from JSON with `--preset-file`. Files can either be a
top-level mapping of names to scene objects or an object with a `scenes` mapping:

```json
{
  "scenes": {
    "key": {"brightness": 35, "kelvin": 5600},
    "blackout": {"sleep": 1}
  }
}
```

CLI scene fields override preset values when supplied. Use
`zlight apply --dry-run` to resolve a preset and override set without opening a
USB/BLE connection.

Cue files use the same sequence shape as HTTP `/sequence`:

```sh
uv run zlight cue --cue-file examples/cues.json --cue warm-key --yes
```

## Metadata

Shell-driven controllers can get bootstrap metadata without opening a transport
or starting the HTTP bridge:

```sh
uv run zlight metadata --kind all --json
```

Use `--kind openapi`, `manifest`, or `capabilities` when a single payload is
enough.

## USB Locking

USB transports take an advisory file lock before opening the serial device and
hold it until close. This serializes independent CLI or bridge processes that
target the same `/dev/cu.usbmodem*` path and avoids interleaved
request/response frames. `--usb-lock-timeout` controls the wait: `0` fails fast
and `none` waits indefinitely.

## BLE CLI Entry Points

BLE discovery and endpoint checks:

```sh
uv run zlight scan-ble --name-contains MOLUS
uv run zlight inspect-ble --name-contains MOLUS --json
uv run zlight test-ble-endpoints --name-contains MOLUS --json
```

See [ble.md](ble.md) for profiles, macOS helper setup, and worker isolation.
