# Verified USB writes with Bluetooth readback

The tested MOLUS G60 / PL103, firmware 1.6.4, accepts brightness and CCT writes
over its USB-C CDC-ACM port. Brightness/CCT queries still receive no USB reply.
Lighttime therefore offers an explicit **USB/BLE hybrid**: output writes use
USB, while Bluetooth identifies the same lamp and verifies its actual settings.
Both connections are required. Ordinary Bluetooth remains the default.

## Start a hybrid bridge on Linux

Find the BLE address with `lighttime scan` and the USB port with
`lighttime usb-info`. Linux normally exposes this unit as `/dev/ttyACM0`, USB
VID/PID `fff8:0180`; port names can change after reconnection.

```sh
uv run lighttime serve --host 127.0.0.1 --address YOUR_BLE_ADDRESS \
  --usb-writes /dev/ttyACM0 --no-auth
```

Open `http://127.0.0.1:8877/`. The same Python/TypeScript HTTP clients, website,
OBS dock and effects API work with the hybrid bridge. State reports
`transport: "usb+ble"`; the connection request still uses `transport: "ble"`.
Authentication is optional; add `--auth` if your deployment needs a token.

If access is denied, [the USB access helper](../scripts/install-usb-access.sh)
documents a device-specific Linux rule. A temporary ACL can disappear when
the cable is reattached. The SDK never installs drivers or changes permissions.
If USB is removed, stop the hybrid bridge and restart without `--usb-writes`
to use Bluetooth alone. There is no silent transport fallback.

## Direct Python SDK

```python
import asyncio
from lighttime import LightClient, UsbBleTransport

async def main():
    transport = UsbBleTransport("YOUR_BLE_ADDRESS", "/dev/ttyACM0")
    async with LightClient(transport=transport) as light:
        actual = await light.set(brightness=12, cct=4500)
        print(actual.transport, actual.brightness, actual.cct)

asyncio.run(main())
```

The same `--usb-writes PORT` option is available on `state` and `set`. Release
an existing bridge connection before using a direct client. macOS/Windows BLE
support remains provided by Bleak, but this hybrid implementation uses POSIX
serial descriptors and an event loop with descriptor readers. Only Linux
hybrid operation has been physically exercised; Windows hybrid support is not
implemented.

## Verification and lifecycle

Before permitting an output write, the transport reads USB and BLE identity
and matches serial number, model PL103 and protocol device ID. Only absolute
brightness `0x1001` and CCT `0x1002` setters for that identity are accepted on
USB, with whole-percent brightness and 100 K CCT resolution. It sends the
BLE-identical `00 01` address pair at 115200 baud, 8N1, DTR/RTS low. USB
setters have no protocol reply; `LightClient.set` succeeds only after matching
fresh Bluetooth brightness/CCT readback.

USB open/close workers remain owned across caller cancellation and deadlines.
Failed cleanup prevents port replacement until a later cleanup succeeds.
Nonblocking descriptor I/O avoids background writes after a cancelled setter;
a cancelled partial frame invalidates that session before another write.
A permanently blocked kernel open/close cannot be forcibly terminated by
Python; cleanup reports unresolved ownership rather than claiming success.

## Evidence and limits

Isolated frames established that `00 01` changes output. Frames addressed
`01 03` were exact serial echoes with no corresponding change. The updater's
`03 01` route produced an unexpected zero brightness for a float setter, so it
is excluded from runtime control. Sixty subsequent `00 01` write cases all
matched independent Bluetooth readback, including combined changes. The
original 7% / 6100 K was restored.

That preliminary direct USB-write/HTTP-read experiment had approximately
65–67 ms median verified round trips. See [latency measurements](latency.md)
for integrated bridge comparisons and [the USB research](research/usb-deep.md)
for official-app analysis, framing and captures. USB-only output state and
fully USB-only verified control remain unestablished. No firmware, provisioning,
registration or sleep commands are part of this transport.
