# Protocol Reference

This page is the narrow wire-level reference for Zhiyun MOLUS runtime and
updater frames. See [hardware-notes.md](hardware-notes.md) for live G60 field
observations, [ble.md](ble.md) for BLE endpoint routing, and
[bridge-http.md](bridge-http.md) for HTTP/media integration behavior.

## Runtime Frame

```text
24 3c
len_lo len_hi
type_lo type_hi    # observed 0x0100, serialized as 00 01
seq_lo seq_hi
cmd_lo cmd_hi
payload...
crc_lo crc_hi      # CRC-16/CCITT, initial 0, over body only
```

Known global runtime commands:

| Command | Name | Status |
| --- | --- | --- |
| `0x2003` | Device info | Verified USB |
| `0x8001` | Firmware version | Verified USB |
| `0x2001` | Voltage/status | Verified USB |
| `0x2005` | Device id | Verified USB after firmware `1.6.4`; returned `0` in the current session |
| `0x0006` | Register to default group | Verified USB ACK |

Known object commands:

| Command | Name | Payload shape | Current USB status |
| --- | --- | --- | --- |
| `0x1001` | Brightness | `u16 obj, u8 op, float value` | `sent_no_response` on G60 `1.6.4` |
| `0x1002` | CCT | `u16 obj, u8 op, u16 kelvin` | `sent_no_response` on G60 `1.6.4` |
| `0x1003` | RGB | `u16 obj, u8 op, u16 r, u16 g, u16 b` | `sent_no_response` on G60 `1.6.4` |
| `0x1008` | Sleep/power | `u16 obj, u8 op, u8 value` | `sent_no_response` on G60 `1.6.4` |
| `0x100a` | HSI | `u16 obj, u8 op, float h, float s, u16 intensity` | `sent_no_response` on G60 `1.6.4` |
| `0x100b` | Brightness plus mode | `u16 obj, u8 op, float brightness, i8 mode` | `sent_no_response` on G60 `1.6.4` |
| `0x1101` | Identify | `u16 obj` | `sent_no_response` on G60 `1.6.4` |
| `0x1201` | Voltage by object | `u16 obj` | `sent_no_response` on G60 `1.6.4` |
| `0x1202` | Firmware by object | `u16 obj` | `sent_no_response` on G60 `1.6.4` |
| `0x1203` | Device mode | `u16 obj` | `sent_no_response` on G60 `1.6.4` |

The official Vega Android package includes `base/assets/pl103/1.6.4.config`.
For PL103 it lists optional control commands `0x1001`, `0x1002`, `0x1008`,
`0x1101`, `0x1201`, and `0x1202` with `controlMode: "0x33"`, plus CCT range
`2700..6500`. Disassembly of Vega's `libzylink.so` shows that `controlMode` is
the `u8 op` field inside the functional payload, not the frame first word.
Accordingly, library writes default to op `0x33`; object reads use op `0x00`,
and `control_mode=0x01` is available for reproducing legacy probes.

## ACK And Evidence Semantics

The library exposes object-control commands through `CommandResult` objects so
integrations can inspect `tx_hex`, `rx_hex`, parsed frames, echo detection, and
timeout/ACK status. `transport_status` is one of:

- `acknowledged`
- `sent_no_response`
- `echoed_write`
- `response_without_matching_ack`

A primitive is confirmed only when the device returns a matching ACK frame with
a valid CRC. A transmitted object-control frame that receives no response stays
`sent_no_response`, not confirmed. An exact write echo is reported as
`echoed_write` and is also not confirmed.

`zlight validate` and `validate_sync_light()` build on `CommandResult` to
produce a hardware evidence report. Use `zlight validate --strict` in
automation when unconfirmed attempted primitives should fail the run. Validation
responses include `summary.status_counts`, `summary.categories`, and
`summary.ready_for` so controllers can decide whether identity reads, object
reads, control setup, and control writes are confirmed without parsing every raw
command result.

The direct CLI commands `register`, `read`, `set`, and `apply` use the same ACK
definition for process exit status. They print the full `CommandResult` payload
either way, but unacknowledged transmissions return exit code `1`.

## Planning Without I/O

For dry-run routing, `scene_command_specs()` exposes the ordered runtime
commands for a `Scene`, `scene_frame_specs()` serializes those commands into
frames for a chosen first word and sequence range, and `scene_command_plan()`
groups both views with a serializable scene payload. `transition_command_plans()`
does the same for each generated transition scene while carrying sequence
numbers forward. None of these helpers opens USB or BLE.

When a host is ready to send, `execute_command_plan()` /
`execute_async_command_plan()` and the matching transition helpers execute those
same plans against any USB, sync BLE, async BLE, or custom light object exposing
`exchange_runtime()`. For lower-level routing, `execute_frame_plan()` /
`execute_async_frame_plan()` send the exact planned frame bytes and preserve
first-word and sequence-number choices.

Low-level protocol callers should import `RuntimeCommand`, `UpdaterCommand`,
`build_runtime_frame`, `first_frame`, payload builders, and functional payload
parsers from `zhiyun_light_control.protocol` when building custom transports or
external SDK adapters. Legacy root-package imports remain available for
compatibility but are not part of the curated root `__all__` surface.

## USB Locking

USB transports take an advisory file lock before opening the serial device and
hold it until close. This serializes independent CLI or bridge processes that
target the same `/dev/cu.usbmodem*` path and avoids interleaved
request/response frames. `--usb-lock-timeout` controls the wait: `0` fails fast
and `none` waits indefinitely.

## Updater Identity Frame

Updater identity uses the same envelope with a different first word:

```text
24 3c
len_lo len_hi
dev_lo dev_hi      # request uses 0x0103
seq_lo seq_hi
cmd_lo cmd_hi
payload...
crc_lo crc_hi
```

Read-only updater commands implemented:

| Command | Name | Status |
| --- | --- | --- |
| `0x1300` | chipSync | Verified USB |
| `0x1302` | readSn | Verified USB; payload decodes to product `0x0541` and runtime identifier `08a409e0c1100113` |

Firmware write commands are intentionally not exposed by this package.
