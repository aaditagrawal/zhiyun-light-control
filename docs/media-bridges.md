# Media Bridges

The media bridges expose the same command payload builders and ACK evidence as
the SDK and HTTP bridge. Start servers with `--allow-control` when they should
send write commands.

```sh
uv run zlight osc-serve --transport usb --port 9000 --allow-control
uv run zlight artnet-serve --transport usb --universe 0 --allow-control
uv run zlight sacn-serve --transport usb --universe 1 --allow-control
```

## OSC

The OSC bridge is UDP-based and dependency-free.

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
| `/zhiyun/transition` | `f brightness, i kelvin, i sleep, f duration, i steps, s easing, i obj`; later fields optional |
| `/zhiyun/preset` | `s name`, optional trailing `i obj` |
| `/zhiyun/cue` | `s name`, optional trailing `i obj` |

The `/light/...` prefix is an alias. Control endpoints require
`zlight osc-serve --allow-control`. Add `--cue-file` to load named cues for
`/zhiyun/cue`.

`/zhiyun/transition` uses the last requested OSC scene for the same object as
the transition start when one exists; send OSC `nil` for target fields that
should stay unspecified. The server replies to each datagram with
`/zhiyun/result` containing success flag, action, and error text.

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

CLI scene fields override preset values when supplied, and HTTP `/preset`
accepts the same scene fields plus `name`. Use `zlight apply --dry-run` to
resolve a preset and override set without opening a USB/BLE connection.

## Art-Net

The Art-Net bridge listens for ArtDmx packets, defaults to universe `0`, and
maps DMX channels to a `Scene`.

| DMX Channel | Default | Meaning |
| --- | --- | --- |
| 1 | enabled | Brightness, 0-255 mapped to 0-100% |
| 2 | enabled | CCT, 0-255 mapped to 2700-6500K |
| 3 | disabled | Sleep/power, values below 128 map to `1`, values 128+ map to `0` |

Power/sleep is opt-in because that command's exact device semantics still need
more live validation. Use
`zlight artnet-serve --sleep-channel 3 --allow-control` to enable it. Repeated
identical scenes are dropped so a steady DMX stream does not spam the USB/BLE
transport.

## sACN/E1.31

The sACN/E1.31 bridge listens for DMX data packets on UDP port `5568`, defaults
to universe `1`, and uses the same `DmxMapping` channel map as Art-Net. Use
`zlight sacn-serve --multicast --allow-control` to join the universe multicast
group, or omit `--multicast` for unicast/local test traffic. Repeated identical
scenes are dropped the same way as Art-Net.

## State Evidence

Bridge state is requested state, not a confirmed physical measurement. A bridge
event can include a requested scene while still reporting `applied: false` when
the underlying command result is `sent_no_response` or `echoed_write`. Keep this
visible in media-controller UIs so operators can distinguish a requested cue from
a light-confirmed cue.
