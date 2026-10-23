# Measuring bridge latency

`scripts/measure_latency.py` measures the existing Python `BridgeClient` against
an already-connected BLE or USB/BLE hybrid G60. The report records the selected
transport. It requires `--live`; help and argument checks do
not contact a bridge. It never scans, connects/disconnects devices, touches
BlueZ, changes a service, or opens a camera. Keep the persistent bridge as the
one controller and leave the browser/other applications idle during a baseline
run. A running effect causes refusal before any benchmark write.

From this checkout, for a personal no-auth bridge:

```sh
uv run --frozen python scripts/measure_latency.py --live
```

For an authenticated bridge, supply the token **file**, never the token itself:

```sh
uv run --frozen python scripts/measure_latency.py --live \
  --token-file ~/.config/lighttime/bridge.token
```

Use `--base-url https://YOUR_BRIDGE` to compare a private HTTPS route. The script
does not read any token file by default. Credentials, URLs, device addresses,
serial numbers and server exception messages are omitted from its output/report.
Do not enable HTTP/BLE debug logging when sharing a report.

The default run performs one warmup pair and ten measured write/read pairs for
each of `brightness`, `cct` and `combined`. Brightness alternates between 1% and
10%; CCT alternates between 3200 K and 5600 K. Single-field modes send only that
field. `--modes brightness --samples 3 --warmup 0` gives a short first run.
Every PATCH is followed by a distinct GET state query. Both responses must
contain the same connected G60 and match the requested fields. A failed or
cancelled operation stops the measured sequence. There are no benchmark retries
or concurrent requests; bridge/SDK recovery remains part of any affected timing.

The script captures current brightness/CCT/power before its first write, and
attempts absolute restoration of both fields in `finally`, even when a PATCH
fails or the first Ctrl-C interrupts the run. Restoration requires a fresh
identity check, a successful setter readback and a separate matching GET.
A replacement/disconnected device causes refusal rather than restoration to
another light. Restoration failure produces a nonzero exit and remains visible
in the JSON. Forced termination or an unavailable bridge can prevent restoration.
The default 10% cap applies to test writes and restoration: a baseline above the
cap is refused before writing. An explicit `--max-brightness` changes that cap.
Do not adjust the light or change controllers during a run.

JSON goes to a new timestamped file under `artifacts/latency/`; `--output PATH`
selects a new filename and refuses an existing file. Each completed/failed HTTP
operation records its latency and requested/confirmed settings. Reports are
checkpointed after each operation, outside the timed interval. Warmup, baseline
and restoration rows are retained but excluded from summary statistics. Each
mode has separate write/read counts, failures, min/mean/max, p50 and p95 in ms.
Percentiles use nearest rank; with fewer than 20 samples p95 is the maximum.
The default untimed pause is 100 ms after each pair; change it with
`--interval-ms`. No result establishes optical response time or a sustained
write rate, since each measured write is followed by an additional verification
read and pause.

## Completed loopback measurement

On 2026-10-23, the parent ran the benchmark against the existing persistent
BLE G60 connection through local loopback HTTP. The browser UI and its SSE
subscription stayed open throughout, as reported by the operator. This includes
possible contention with the bridge's two-second state polling. The run used
Python 3.14.8, 20 measured pairs plus one warmup pair per mode, a 20 ms untimed
pause after each pair, brightness targets 4%/6%, and CCT targets 4500/5500 K.
The saved run spans 16:15:19.255251–16:15:31.144739 UTC.

Source: local, ignored report `artifacts/latency/loopback-baseline.json`.
All values below are HTTP SDK round-trip milliseconds, including bridge queueing
and completed BLE readback. PATCH already verifies its setter; GET is the
additional independent two-field read after that PATCH.

| Operation | Samples | p50 (ms) | p95 (ms) | Failures |
| --- | ---: | ---: | ---: | ---: |
| Brightness-only PATCH | 20 | 84.452 | 144.425 | 0 |
| GET after brightness PATCH | 20 | 59.752 | 60.686 | 0 |
| CCT-only PATCH | 20 | 84.396 | 129.239 | 0 |
| GET after CCT PATCH | 20 | 59.474 | 74.245 | 0 |
| Combined brightness/CCT PATCH | 20 | 114.274 | 129.241 | 0 |
| GET after combined PATCH | 20 | 59.375 | 60.179 | 0 |

All 130 recorded operations, including warmups, baseline and restoration,
completed successfully. The report captured **5% / 5500 K** and verified
restoration to **5% / 5500 K** through both the restoration setter and final
fresh GET.

The combined PATCH median exceeds the single-field medians by 29.8–29.9 ms in
this run. That supports testing omission of an unchanged setter at UI dispatch.
It does not establish the eventual UI improvement, and these p95 values do not
show a consistent tail-latency reduction. Full fresh GET medians were
59.4–59.8 ms. No sub-10 ms verified setter result was observed or is claimed.

These results cover local HTTP-to-BLE verified round trips. They do not measure
input-to-visible-light delay, browser dispatch/paint delay, remote end-user
network latency, HTTPS overhead or optical response. Separate physical timing
and a completed comparative network run would be needed for those conclusions.

The subsequent HTTPS attempt correctly refused before writing because the user
had started a pattern. `artifacts/latency/https-baseline.json` is a refused
attempt, not a completed timing distribution. The active pattern was preserved.
The later completed comparisons below were run after that pattern had ended.

## Where the time goes

The recovered connection was also tested through the private HTTPS endpoint on
the laptop, with the controller's SSE stream open: 20 measured pairs plus one
warmup per mode, targets 4%/8% and 3200/5600 K, 20 ms pauses. All operations
passed and the captured 7% / 6100 K was restored. The report is
`artifacts/latency/https-recovered-ble.json`.

| HTTPS operation | p50 (ms) | p95 (ms) |
| --- | ---: | ---: |
| Brightness-only PATCH | 84.334 | 93.046 |
| CCT-only PATCH | 85.291 | 96.174 |
| Combined PATCH | 114.050 | 125.356 |

This is a completed HTTPS API measurement from the laptop, not a phone's
network round trip or an optical measurement. The earlier refused HTTPS
attempt and slow reconnect report remain retained separately.

The integrated `UsbBleTransport` then ran the same 20-pair cases through the
same HTTPS endpoint with SSE open. The USB and BLE serial/model/ID matched,
State reported `usb+ble`, every setter/read passed, and 7% / 6100 K was restored.
Report: `artifacts/latency/https-usb-ble.json`.

| Hybrid HTTPS operation | p50 (ms) | p95 (ms) |
| --- | ---: | ---: |
| Brightness-only PATCH | 55.127 | 67.019 |
| CCT-only PATCH | 54.657 | 70.623 |
| Combined PATCH | 55.789 | 70.531 |

The corresponding BLE-only medians above were 84.334, 85.291 and 114.050 ms.
These successive runs support a reduction of about 29–58 ms for this workload.
Connection conditions can vary substantially, as the retained slow session
shows; this is not a guaranteed bound. Full Bluetooth readback remains part of
every successful hybrid setter. No requested value is substituted for readback.

Source inspection, rather than new physical measurements, establishes these
costs:

- `src/lighttime/transport.py`: BLE writes request GATT acknowledgement
  (`response=True`). A query also waits for its matching protocol notification.
  The transport serializes frames; the SDK serializes complete read/set
  transactions. GATT acknowledgement alone does not confirm output state.
- `src/lighttime/client.py`: a one-field setter sends one acknowledged setter,
  then brightness and CCT queries. A combined setter sends two acknowledged
  setters and the same two queries. Every GET state performs two fresh queries.
  PATCH success already includes hardware readback; the benchmark's extra GET
  measures a separate full read and detects intervening changes.
- `src/lighttime/bridge.py`: the connection persists across HTTP requests.
  Manual sets cancel an effect and hold the bridge lock during the entire
  verified transaction. An SSE subscriber enables fresh two-query polling every
  two seconds, which can contend for that same lock. Effect-status polling does
  not itself read the lamp. Benchmark with the UI closed first, then repeat with
  SSE open if its contention is relevant.
- `examples/browser/app.ts`: at initial inspection, automatic apply was opt-in
  and used a 200 ms trailing debounce reset by every input. Continuous dragging
  could postpone all output until a pause. The controller now uses immediate
  leading dispatch through `queueDraft`/`pump`, enabling live apply by default
  for automatic personal mode. The pump
  awaits one verified transaction and retains one latest pending target. It
  keeps requested controls separate from confirmed readback.
- `src/lighttime/effects.py`: the initial measurement used 200 ms spacing after
  completion. The expanded scheduler now spaces write starts by at least 100 ms,
  waits for full readback, recomputes targets after waits and skips missed or
  unchanged targets. [Physical cadence and reconnect experiments](experiments.md)
  record the results and the slow connection recovered by reconnecting.

## Decisions after measurement

The UI now dispatches only changed fields, with one in-flight transaction and
one merged latest target. Browser regressions cover reversals during an older
write, stale responses and manual override. The bridge also skips its ordinary
two-second SSE hardware poll when verified readback is less than two seconds
old. Explicit GETs still read both fields; successful setters still verify both.

The completed BLE/hybrid HTTPS comparison supports keeping USB writes as an
explicit option when both cables and Bluetooth are available. It does not
justify replacing readback with requested state or promising a universal latency
bound. A previous slow BLE connection remains part of the evidence.

Removing GATT acknowledgements did not produce a useful improvement in the
small repeated-read experiment described in [experiments.md](experiments.md),
so ordinary BLE retains them. No overlapping GATT transactions, negotiated
connection-parameter changes or ACK-only public effects were added. Health
latency measures HTTP access and must not stand in for setter latency. Optical
response remains a separate measurement with camera-processing uncertainty.
