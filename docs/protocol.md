# Protocol Notes

This package implements the USB runtime frame and a BLE transport for the same command layer.

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
| `0x2005` | Device id | Verified USB after firmware `1.6.4`; returned `0` before registration and `1` after registration in this session |
| `0x0006` | Register to default group | Verified USB ACK |

Known object commands:

| Command | Name | Payload shape |
| --- | --- | --- |
| `0x1001` | Brightness | `u16 obj, u8 op, float value` |
| `0x1002` | CCT | `u16 obj, u8 op, u16 kelvin` |
| `0x1003` | RGB | `u16 obj, u8 op, u16 r, u16 g, u16 b` |
| `0x1008` | Sleep/power | `u16 obj, u8 op, u8 value` |
| `0x100a` | HSI | `u16 obj, u8 op, float h, float s, u16 intensity` |
| `0x1101` | Identify | `u16 obj` |
| `0x1201` | Voltage by object | `u16 obj` |
| `0x1202` | Firmware by object | `u16 obj` |
| `0x1203` | Device mode | `u16 obj` |

Object reads still need more live validation. On the upgraded G60, registration ACKs over USB, but object reads tested for object ids `0` and `1` did not respond.

The library exposes object-control commands through `CommandResult` objects so integrations can inspect `tx_hex`, `rx_hex`, parsed frames, and timeout/ACK status. This is useful while the exact object-control behavior is still being validated across USB and BLE. `transport_status` is `acknowledged`, `sent_no_response`, or `response_without_matching_ack`.

## Media Integration Surface

The local HTTP bridge is intentionally small and JSON-only:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Process health |
| `GET` | `/probe` | Global light probe |
| `GET` | `/commands` | List bridge commands |
| `POST` | `/register` | Register default group |
| `POST` | `/brightness` | Set brightness |
| `POST` | `/cct` | Set color temperature |
| `POST` | `/sleep` | Set sleep/power value |
| `POST` | `/rgb` | Set RGB values |
| `POST` | `/hsi` | Set HSI values |
| `POST` | `/scene` | Apply several properties in order |

Control endpoints require `zlight serve --allow-control`. Responses include command result details instead of hiding timeouts, because some endpoints are still experimental on the current G60.

The local bridges default to USB, but `serve`, `osc-serve`, `artnet-serve`, and
`sacn-serve` also accept `--transport ble` with `--address` or
`--name-contains`. BLE bridge mode uses the same command payload builders as USB
and adapts the async BLE client behind the synchronous stdlib bridge servers.
The bridge CLI keeps one USB/BLE light connection open by default so repeated
media-control messages do not reconnect for every request. Use
`--no-persistent-light` to restore per-request connections.

The OSC bridge is UDP-based and dependency-free:

| Address | Arguments |
| --- | --- |
| `/zhiyun/probe` | none |
| `/zhiyun/register` | `i device_id` |
| `/zhiyun/brightness` | `f value`, optional trailing `i obj` |
| `/zhiyun/cct` | `i kelvin`, optional trailing `i obj` |
| `/zhiyun/sleep` | `i value`, optional trailing `i obj` |
| `/zhiyun/rgb` | `i red, i green, i blue`, optional trailing `i obj` |
| `/zhiyun/hsi` | `f hue, f saturation, i intensity`, optional trailing `i obj` |
| `/zhiyun/scene` | `f brightness, i kelvin, i sleep`, optional trailing `i obj` |

The `/light/...` prefix is an alias. Control endpoints require `zlight osc-serve --allow-control`. The server replies to each datagram with `/zhiyun/result` containing success flag, action, and error text.

The Art-Net bridge listens for ArtDmx packets, defaults to universe `0`, and maps DMX channels to a `Scene`:

| DMX Channel | Default | Meaning |
| --- | --- | --- |
| 1 | enabled | Brightness, 0-255 mapped to 0-100% |
| 2 | enabled | CCT, 0-255 mapped to 2700-6500K |
| 3 | disabled | Sleep/power, values below 128 map to `1`, values 128+ map to `0` |

Power/sleep is opt-in because that command's exact device semantics still need more live validation. Use `zlight artnet-serve --sleep-channel 3 --allow-control` to enable it. Repeated identical scenes are dropped so a steady DMX stream does not spam the USB/BLE transport.

The sACN/E1.31 bridge listens for DMX data packets on UDP port `5568`, defaults
to universe `1`, and uses the same `DmxMapping` channel map as Art-Net. Use
`zlight sacn-serve --multicast --allow-control` to join the universe multicast
group, or omit `--multicast` for unicast/local test traffic. Repeated identical
scenes are dropped the same way as Art-Net.

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
| `0x1302` | readSn | Verified USB |

Firmware write commands are intentionally not exposed by this package.

## BLE Surfaces

Direct ZY light service:

- Service: `0000fee9-0000-1000-8000-00805f9b34fb`
- Write characteristic: `d44bc439-abfd-45a2-b575-925416129600`
- Notify/read characteristic: `d44bc439-abfd-45a2-b575-925416129601`

Mesh-related services observed in ZY Vega:

- Provisioning: `00001827-0000-1000-8000-00805f9b34fb`
- Proxy: `00001828-0000-1000-8000-00805f9b34fb`

Older/alternate YC light service:

- Service: `0000ffe0-0000-1000-8000-00805f9b34fb`
- Write characteristic: `0000ffe1-0000-1000-8000-00805f9b34fb`
- Read characteristic: `0000ffe2-0000-1000-8000-00805f9b34fb`

`zlight scan-ble` runs BLE discovery in a worker process by default. This is deliberate: on the local macOS setup, bleak/CoreBluetooth aborts the interpreter during scanning. Isolating the scan keeps API users and long-running bridge processes alive and returns a JSON diagnostic instead.

Current local BLE scan validation:

| Runtime | BLE stack | Result |
| --- | --- | --- |
| Python 3.13 | `bleak 3.0.2`, PyObjC `12.1` | Worker terminates with `SIGABRT` before returning devices |
| Python 3.12 | `bleak 3.0.2`, PyObjC `12.1` | Worker terminates with `SIGABRT` before returning devices |

The failure appears to be below the package's Python transport layer. The
worker wrapper reports `worker_python`, `returncode`, and `signal` so callers can
surface this separately from ordinary zero-device scans.
