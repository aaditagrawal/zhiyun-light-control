---
name: zhiyun-light-control
description: Use this skill when working with the zhiyun-light-control Python SDK or CLI for Zhiyun MOLUS lights, including hardware validation, local control, agent integrations, USB/BLE debugging, rigs, bridges, and unacknowledged command investigation.
---

# Zhiyun Light Control

This repository provides a `uv`-managed Python SDK and CLI for local control of
Zhiyun MOLUS lights. Treat live hardware control as evidence-driven: a sent frame
is not success unless the command is ACK-confirmed or the user physically
observes the expected light change.

## Core Rules

- Use `uv` for all Python commands.
- Do not claim a command succeeded solely because bytes were transmitted.
- Preserve `transport_status` in user-facing results. `acknowledged` is confirmed;
  `sent_no_response` and `echoed_write` are unconfirmed.
- On the current local MOLUS G60 firmware `1.6.4`, global USB status/register
  commands ACK, but default `0x0100` object-control writes time out.
- The physically observed G60 USB control evidence is first word `0x0301`:
  sleep control blinked the light, and a broad brightness/CCT candidate pass
  reached `2700K` at `20%`, while returning `echoed_write`, not an ACK.
- Document new hardware observations in `docs/hardware-notes.md` and keep tests
  updated when CLI or SDK behavior changes.

## Quick Hardware Check

```sh
uv run zlight devices --include-ble-status --json
uv run zlight status --transport usb --json
```

If macOS BLE reports unauthorized, use:

```sh
uv run zlight ble-helper --ensure --open-settings
uv run zlight ble-helper --status --json
```

## Safe Validation

```sh
uv run zlight validate --transport usb --include-object-reads --json
uv run zlight discover-usb --g60-matrix --json
```

Use `--allow-control` only when the user explicitly wants state-changing
hardware probes.

## Current G60 Control Evidence

For the locally observed G60, use `0x0301` when probing control:

```sh
uv run zlight apply --transport usb --first-word 0x0301 --accept-echo --sleep 0 --brightness 50 --kelvin 3200 --yes
```

Expect `transport_status: "echoed_write"` on this route. That is not
ACK-confirmed. `--accept-echo` makes shell automation return exit code `0` for
exact echoes while preserving `acknowledged: false` in JSON.

For the currently confirmed stable look, repeat brightness/CCT writes across
responsive `0x0301` candidates:

```sh
for obj in 0 1 2; do
  for mode in 0x33 0x01; do
    uv run zlight apply --transport usb --first-word 0x0301 --control-mode "$mode" --obj "$obj" --accept-echo --brightness 20 --kelvin 2700 --yes
  done
done
```

Ask the user to visually confirm brightness and CCT changes.

For reusable agent workflows and interpretation details, read
`references/control-workflows.md`.
