# Python SDK and bridge deployment

Install from a checkout with `uv sync --frozen`, or build a wheel using
`uv build`. Python 3.13+ is supported; the tested environment is pinned in
`.python-version`. Bleak selects BlueZ on Linux, CoreBluetooth on macOS and
WinRT on Windows. Linux hardware has been tested; macOS/Windows hardware have
not. macOS uses a device UUID rather than a Linux Bluetooth MAC address.

## Install a built distribution

The package has not been published to PyPI. Build the wheel and source archive
from this checkout:

```sh
uv build --out-dir artifacts/distributions
```

To use the SDK in another Python 3.13+ project, run there:

```sh
uv add /absolute/path/to/lighttime-0.1.0-py3-none-any.whl
```

For an isolated command-line installation, use
`uv tool install --python 3.14 /absolute/path/to/lighttime-0.1.0-py3-none-any.whl`.
The wheel includes the built browser controller, so serving it does not need
Bun or the source checkout. The frozen lockfile reproduces the tested checkout
environment; a wheel install resolves compatible dependencies for its platform.

## Direct connection

```python
import asyncio
from lighttime import LightClient, discover

async def main():
    devices = await discover(timeout=5)
    address = next(d["address"] for d in devices if d["transport"] == "ble")
    async with LightClient(address) as light:
        print(light.state.to_dict())
        await light.set(brightness=15, cct=4000)
        await light.set(power=False)
        await light.set(power=True)
        await light.refresh()

asyncio.run(main())
```

`DeviceInfo`, `LightState`, `BleTransport`, `SerialTransport`, `TransportError`
and `RequestTimeout` are exported. `LightClient` serializes operations, checks
device model and ID, retries absolute operations once after reconnecting, and
verifies setters with actual readback. Call `close()` or use the context manager.
One G60 supports one active controller connection; applications should share the
bridge rather than opening competing Bluetooth connections.

## Shared bridge

```python
import asyncio
from pathlib import Path
from lighttime import BridgeClient

async def main():
    token = Path.home().joinpath(".config/lighttime/bridge.token").read_text().strip()
    async with BridgeClient(token=token) as bridge:
        print(await bridge.refresh())
        await bridge.set(brightness=10, cct=4500)

asyncio.run(main())
```

On macOS the default token path is `~/Library/Application Support/lighttime/bridge.token`;
on Windows it is `%APPDATA%/lighttime/bridge.token`; on Linux it honors
`$XDG_CONFIG_HOME`. `lighttime token-path` is authoritative for the current OS.
The service creates the file with owner-only permissions on POSIX. It does not
print the bearer token into logs. `--token-file PATH` selects another file.

Authentication is enabled by default. GET health is public; in that default
mode all other `/v1` routes require a bearer token. Cross-origin web access
requires exact `--allow-origin` values. SSE uses authenticated fetch
streaming; do not put tokens in query strings. Static UI assets served by the
bridge contain no token. Mock transport requires explicit `--allow-mock` and
identifies itself as `MOCK-NOT-HARDWARE`.

For a persistent Linux bridge, adapt `integrations/linux/lighttime.service`, copy
it to `~/.config/systemd/user/lighttime.service`, then run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now lighttime
systemctl --user status lighttime
```

Run with your normal desktop user; do not run Bluetooth control as root. The
service template binds only loopback. To access from outside the machine, choose
an appropriate host, exact origin allowlist, and secure transport. When direct
BLE disconnects unexpectedly, the next absolute command/read attempts one
reconnection. A failure is returned to the caller rather than silently falling
back to a mock or reporting cached data as fresh.

### Personal testing

To open straight to the light's controls, start the bridge with its address
and explicitly select personal mode:

```sh
uv run lighttime serve --host 127.0.0.1 --address YOUR_BLE_ADDRESS --no-auth
```

Open `http://127.0.0.1:8877/ui/`. The bridge connects at startup, and the page
automatically opens the light's controls. `--no-auth` disables bearer-token
authentication for this instance; omitting the flag keeps the default
authenticated SDK setup.

### Private HTTPS access

With Tailscale and its HTTPS prerequisites configured, a private HTTPS proxy
can keep the bridge on loopback. Use your own fully qualified Tailscale
hostname in place of `YOUR_HOST`, including the explicit port in the origin:

```sh
uv run lighttime serve --address YOUR_BLE_ADDRESS --host 127.0.0.1 --no-auth \
  --allow-origin https://YOUR_HOST:10000
tailscale serve --bg --https=10000 http://127.0.0.1:8877
```

Open `https://YOUR_HOST:10000/ui/` from an authorized tailnet device to go
straight to controls. The page uses its own origin for the bridge automatically.
This uses private Tailscale Serve. Choose an unused HTTPS port; this example
uses 10000 so an existing 443 or 8443 listener can remain in place. See
[Tailscale Serve](https://tailscale.com/kb/1242/tailscale-serve) for HTTPS and
access prerequisites. This serves the UI and API from one origin; direct
browser Bluetooth still depends on the browser and the device running it.

For a service installation, put the same bridge flags into your adapted user
unit. Stop that unit before using another direct BLE controller, including the
Android app or ZY Vega, then restart it to return ownership to the shared
bridge. Use the generic unit template rather than a validation machine's unit.
