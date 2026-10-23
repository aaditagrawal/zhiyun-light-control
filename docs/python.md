# Python SDK and bridge deployment

From a checkout, turn on the G60 and run:

```sh
uv run --frozen lighttime
```

Open `http://localhost:8877/`. No arguments means `serve`: loopback port 8877,
no bearer token, and automatic discovery/connection of one nearby PL103/G60.
`uv run` prepares the environment; a separate `uv sync` is unnecessary. Python
3.13+ is supported and `.python-version` pins the checkout environment.
Bleak selects BlueZ on Linux, CoreBluetooth on macOS and WinRT on Windows.
Linux hardware has been tested; macOS/Windows hardware have not. macOS uses a
device UUID rather than a Linux Bluetooth MAC address.

For website integration, start with [the website guide](integrating-sites.md).

## Shared bridge

Use the asynchronous bridge client when the bridge owns Bluetooth:

```python
import asyncio
from lighttime import BridgeClient

async def main():
    async with BridgeClient() as light:
        actual = await light.connect()
        verified = await light.set(brightness=10, cct=4500)
        print(actual, verified)

asyncio.run(main())
```

The default URL is `http://127.0.0.1:8877`. `connect()` without an address
reuses an existing bridge connection and reads fresh state. If disconnected,
it discovers a single G60; several candidates produce an actionable selection
error. To choose one, use `await light.connect("SELECTED_DEVICE_IDENTIFIER")`.
`refresh()` reads actual state without initiating discovery. A remote client
can supply its reachable private bridge URL as `BridgeClient("https://YOUR_HOST:10000")`.

The browser, OBS and bridge SDKs share one BLE owner. Stop the bridge before
using a direct Python, Web Bluetooth, Android or vendor-app connection. A
transport/readback failure is returned to the caller, never replaced with mock
output or cached state labeled fresh. Mock transport requires explicit
`--allow-mock` and identifies itself as `MOCK-NOT-HARDWARE`.

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

Alternatively use `uv add /absolute/path/to/zhiyun-light-control` for a local
checkout dependency. For an isolated command-line installation, use
`uv tool install --python 3.14 /absolute/path/to/lighttime-0.1.0-py3-none-any.whl`.
The wheel includes the browser controller and `/lighttime.js` module, so
serving a site does not need Bun or the source checkout. The frozen lockfile
reproduces the tested checkout environment; a wheel install resolves compatible
dependencies for its platform.

An optional no-clone command is:

```sh
uvx --python 3.14 --from git+https://github.com/aaditagrawal/zhiyun-light-control lighttime
```

This installs from Git and resolves compatible dependencies rather than using
the checkout's frozen lockfile.

## Direct connection

Direct clients take an explicit platform identifier. Find it with
`uv run --frozen lighttime scan`, then select the intended light:

```python
import asyncio
from lighttime import LightClient

async def main():
    async with LightClient("SELECTED_DEVICE_IDENTIFIER") as light:
        print(light.state.to_dict())
        await light.set(brightness=15, cct=4000)
        await light.set(power=False)
        await light.set(power=True)
        await light.refresh()

asyncio.run(main())
```

`discover`, `DeviceInfo`, `LightState`, `BleTransport`, `SerialTransport`,
`TransportError` and `RequestTimeout` are exported. `LightClient` serializes
operations, checks device model and ID, retries absolute operations once after
reconnecting, and verifies setters with actual readback. Call `close()` or use
the context manager. One G60 supports one active controller connection.

Brightness is whole-percent 0–100 and CCT is 2700–6500 K in 100 K steps. Off
writes zero brightness; on restores the controller's last nonzero brightness
or 5% if none has been observed. Reconnecting starts a new controller lifetime.

## Startup and site hosting

```sh
uv run --frozen lighttime serve --web-root examples/site-controller
```

This serves the no-build HTML example and API from one origin. Import
`BridgeClient` and `EFFECT_PRESETS` from `/lighttime.js`; web clients use
`{ baseUrl: location.origin }`. A custom `--web-root PATH` selects your site's
index/assets; packaged builds include the normal controller by default.

Startup discovery runs once in the background, so HTTP is ready while the
bridge finds the light. Use `serve --address SELECTED_DEVICE_IDENTIFIER` for
explicit selection, or `serve --no-auto-connect` to defer startup connection.
`POST /v1/connect` with `{}` remains available for a later connect/reconnect.
A multiple-device error calls for explicit selection, not a random first match.

### Personal testing

The default command already provides the local, tokenless controller:

```sh
uv run --frozen lighttime
```

Open `http://localhost:8877/`. Existing `serve --no-auth` commands remain
compatible; the flag is no longer necessary for the default flow.

## Persistent Linux bridge

Adapt [integrations/linux/lighttime.service](../integrations/linux/lighttime.service),
replacing `PROJECT_PATH`, and copy it to
`~/.config/systemd/user/lighttime.service`. `uv run --frozen lighttime --help`
prepares the checkout environment without starting the bridge. Then run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now lighttime
systemctl --user status lighttime
```

The template uses the checkout's `.venv/bin/lighttime`, binds loopback and
connects a single nearby G60 automatically. Run it as your normal desktop user
with BlueZ and a powered adapter. Stop that unit before another direct BLE
controller, then restart it to return ownership to the bridge.

### Private HTTPS access

With Tailscale and its HTTPS prerequisites configured, a private HTTPS proxy
can keep the bridge on loopback. Use your own fully qualified Tailscale
hostname in place of `YOUR_HOST`, including the port in the allowed origin:

```sh
uv run --frozen lighttime serve --allow-origin https://YOUR_HOST:10000
tailscale serve --bg --https=10000 http://127.0.0.1:8877
```

Open `https://YOUR_HOST:10000/` from an authorized tailnet device. The UI uses
its own origin for the bridge automatically. This is private Tailscale Serve;
choose an unused HTTPS port. See
[Tailscale Serve](https://tailscale.com/kb/1242/tailscale-serve) for prerequisites.
Put the same bridge flags in an adapted user unit for a persistent deployment.

A separately hosted browser app needs a reachable private HTTPS bridge URL and
its exact `--allow-origin https://YOUR_APP_HOST`, or a same-origin app proxy
that can reach the bridge. `localhost` on another device does not reach this
computer. A public deployed site cannot discover arbitrary laptop hardware;
see [website architecture](integrating-sites.md#choose-where-the-site-runs).

## Optional authentication

Opt into bearer authentication when your deployment calls for it:

```sh
uv run --frozen lighttime serve --auth
# Optional custom credential location:
uv run --frozen lighttime serve --auth --token-file /absolute/path/bridge.token
```

The service creates its token file with owner-only permissions on POSIX and
prints its path, not the token. `uv run --frozen lighttime token-path` reports
the default location. On Linux this honors `$XDG_CONFIG_HOME`, otherwise
`~/.config/lighttime/bridge.token`; macOS uses
`~/Library/Application Support/lighttime/bridge.token`, and Windows uses
`%APPDATA%/lighttime/bridge.token`. Use the running bridge's custom path if set.

```python
from pathlib import Path
from lighttime import BridgeClient

# Only for a bridge explicitly started with --auth.
token = Path("/absolute/path/bridge.token").read_text().strip()
async with BridgeClient(token=token) as light:
    actual = await light.connect()
```

With `--auth`, health remains public and other `/v1` routes require
`Authorization: Bearer …`. SDK fetch streaming carries that header for SSE;
tokens never belong in query strings or a deployed public bundle. With default
no-auth operation, the CLI creates no token file and SDKs omit Authorization.
Origin checks apply in both modes. `--no-auth` remains supported for existing
commands.
