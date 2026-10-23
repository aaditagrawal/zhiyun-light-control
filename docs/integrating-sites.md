# Add Lighttime to a website

Run Lighttime on the computer beside the G60. Your site talks to its HTTP
bridge, which owns the Bluetooth connection and returns verified device state.
With one nearby light, there is no address or token to configure.

```sh
git clone https://github.com/aaditagrawal/zhiyun-light-control.git
cd zhiyun-light-control
uv run --frozen lighttime
```

Turn on the light and open `http://localhost:8877/`. `uv run` creates/syncs the
environment automatically from the pinned checkout. The default bridge binds
loopback port 8877, discovers a single PL103/G60 once at startup and connects
it. `POST /v1/connect` with `{}` also reuses an existing connection with fresh
readback, or discovers a single light if disconnected. If more than one light
is found, return the choice to the user; never choose arbitrarily. The built-in
controller offers **Choose a light**, followed by a nearby-device selector.

## Copy-paste brief for your coding agent

```text
Integrate the ZHIYUN MOLUS G60 using this Lighttime repository. Read AGENTS.md,
docs/integrating-sites.md and docs/api-contract.md. Use the shared HTTP bridge;
start it with uv run --frozen lighttime. One nearby PL103 connects automatically.
Use BridgeClient.connect() without an address, or POST /v1/connect with {}.
No token field or address setup is needed in the primary flow.

For a static site served by the bridge, import BridgeClient and EFFECT_PRESETS
from /lighttime.js and use { baseUrl: location.origin }. Start with
examples/site-controller/index.html and serve it with
uv run --frozen lighttime serve --web-root examples/site-controller.
For a bundled app, build packages/sdk and bun add its absolute local path or
packed archive; @lighttime/sdk is not published to npm. A default BridgeClient
targets http://127.0.0.1:8877. Use a configured private HTTPS bridge URL or a
same-origin proxy when the app runs remotely.

Implement whole-percent brightness 0–100, CCT 2700–6500 in 100 K steps,
soft off/on and optional shared effects. Keep requested settings, actual
readback and errors separate. Show success only from returned verified state.
Reuse the bridge's connection; do not open a second BLE controller. Dispose
client requests/streams when the page detaches, preserving shared hardware.
Surface connection errors and multiple-light selection clearly. Check the
existing contracts and tests; do not claim hardware validation from mocks.
```

## Static HTML: no SDK install or build

The bridge serves a self-contained ES module at `/lighttime.js`. Start the
working [site-controller example](../examples/site-controller/index.html):

```sh
uv run --frozen lighttime serve --web-root examples/site-controller
```

To make your own page, save this as `my-site/index.html` in the checkout:

```html
<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>My G60 controller</title>
<button id="apply" disabled>Set 10% / 4500 K</button>
<button id="candle" disabled>Candle</button>
<button id="stop" disabled>Stop and restore</button>
<pre id="actual">Connecting…</pre>
<p id="error" role="alert"></p>
<script type="module">
  import { BridgeClient, EFFECT_PRESETS } from '/lighttime.js';
  const light = new BridgeClient({ baseUrl: location.origin });
  const actual = document.querySelector('#actual');
  const error = document.querySelector('#error');
  const buttons = [...document.querySelectorAll('button')];
  const show = state => { actual.textContent = JSON.stringify(state, null, 2); };
  async function run(action) {
    error.textContent = '';
    try { await action(); }
    catch (cause) { error.textContent = String(cause); }
  }
  document.querySelector('#apply').onclick = () => run(async () => {
    show(await light.setState({ brightness: 10, cct: 4500 }));
  });
  document.querySelector('#candle').onclick = () => run(async () => {
    await light.startEffect(EFFECT_PRESETS.candle);
    show(await light.refresh());
  });
  document.querySelector('#stop').onclick = () => run(async () => {
    await light.stopEffect({ restore: true });
    show(await light.refresh());
  });
  await run(async () => {
    show(await light.connect());
    buttons.forEach(button => { button.disabled = false; });
  });
  addEventListener('pagehide', () => light.dispose(), { once: true });
</script>
</html>
```

Serve it from the repository root:

```sh
uv run --frozen lighttime serve --web-root my-site
```

Open `http://localhost:8877/`. The `<pre>` holds actual readback, while errors
have their own element. For continuous output updates, consume `light.events()`;
show stream loss as stale state and stop the subscription on page detach. A
running effect belongs to the bridge and continues after the page closes.

## Bundled TypeScript apps

Build the SDK in this checkout:

```sh
cd packages/sdk
bun install --frozen-lockfile
bun run build
```

Then run in the consuming app:

```sh
bun add /absolute/path/to/zhiyun-light-control/packages/sdk
```

For a portable archive, use `bun pm pack --destination ../../artifacts/distributions`
in `packages/sdk` and `bun add /absolute/path/to/lighttime-sdk-0.1.0.tgz` in
the app. There is no published npm package to download.

```ts
import { BridgeClient } from '@lighttime/sdk';

const light = new BridgeClient(); // local bridge on port 8877
try {
  const actual = await light.connect();
  const verified = await light.setState({ brightness: 10, cct: 4500 });
  console.log({ actual, verified });
} catch (error) {
  console.error(error);
} finally {
  light.dispose();
}
```

Use `new BridgeClient({ baseUrl: location.origin })` when the bridge serves
your app. For a remote app, provide its configured private bridge URL instead;
the constructor's default remains `http://127.0.0.1:8877`.

## Python clients

From this checkout, run scripts with `uv run --frozen python script.py`. For
another project, install the local package with `uv add /absolute/path/to/zhiyun-light-control`
or use a built wheel as described in [the Python guide](python.md).

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

These calls reuse the bridge connection; they do not open a competing BLE
session. A remote script can use `BridgeClient("https://YOUR_PRIVATE_BRIDGE")`.

## Raw fetch

This example runs on a page served by the bridge or by an app proxying its
`/v1` routes to the bridge:

```js
async function request(path, method, body) {
  const response = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error?.message ?? `HTTP ${response.status}`);
  return result;
}

try {
  const actual = await request('/v1/connect', 'POST', {});
  const verified = await request('/v1/state', 'PATCH', { brightness: 10, cct: 4500 });
  console.log({ actual, verified });
} catch (error) {
  console.error(error);
}
```

`GET /v1/state` refreshes readback. Soft off/on are
`PATCH /v1/state` with `{ "power": false }` / `{ "power": true }`.
Effects use `POST /v1/effects/start` with
`{ "pattern": "breathe", "period": 4, "maxBrightness": 20 }`, and
`POST /v1/effects/stop` with `{ "restore": true }`. See
[the HTTP contract](api-contract.md) and [effect contract](effects-contract.md).

## Choose where the site runs

**Bridge serves the site:** use `serve --web-root PATH`, `/lighttime.js` and
`{ baseUrl: location.origin }`. The UI and API share one origin, so there is
no CORS setup. This is the shortest path for static sites, including private
HTTPS access through a proxy to the bridge.

**Existing app on the same computer:** point the SDK at
`http://127.0.0.1:8877`. For a separate browser origin, start the bridge with
its exact origin, for example
`uv run --frozen lighttime serve --allow-origin http://localhost:5173`.
Alternatively proxy `/v1` through your app server and use the app's origin.
An origin includes scheme, host and port; `localhost` and `127.0.0.1` are
different origins.

**Existing remote or cross-device app:** run the bridge beside the light and
provide a reachable HTTPS bridge URL in the same private environment, with
`--allow-origin https://YOUR_APP_HOST` for a separate browser origin. Or route
the app's `/v1` requests through a same-origin proxy that can reach the bridge.
[Private Tailscale HTTPS](python.md#private-https-access) is one option.
`localhost` on a phone is the phone; use the bridge computer's private URL.
A public HTTPS deployment cannot automatically reach arbitrary laptop hardware.
An allowlist alone does not make loopback reachable or override browser
mixed-content/local-network policies.

## Troubleshooting

- **Bridge offline:** start `uv run --frozen lighttime` and check
  `http://localhost:8877/v1/health`. The page may load before startup discovery
  finishes; retry Connect and keep errors visible.
- **Wrong host or port:** the SDK default is the browser/script device's local
  port 8877. Use `location.origin` for a bridge-served page, or configure the
  bridge computer's private HTTPS URL for a remote page. For a separate origin,
  match `--allow-origin` to the page's exact origin.
- **BLE occupied or no light found:** power on the G60, check the adapter/BlueZ,
  and close ZY Vega, the Android app or another direct controller. Keep one
  bridge process. If BlueZ retains a prior link, follow the bridge's targeted
  `bluetoothctl info ADDRESS` / `bluetoothctl disconnect ADDRESS` recovery
  message after identifying its owner; an adapter reset is unnecessary.
- **Several G60s:** restart with `serve --address SELECTED_IDENTIFIER` using an
  identifier from the error, or list `GET /v1/devices?timeout=5` and send
  `{ "transport": "ble", "address": "SELECTED_IDENTIFIER" }` to
  `/v1/connect`. On macOS the identifier is a UUID rather than a Linux MAC address.
- **Readback/verification failure:** refresh actual state before retrying. A
  request may have changed hardware before its readback failed; a draft value
  or an accepted effect start is not proof of current output.

## Optional authentication and startup controls

`serve --auth` opts into bearer authentication; `--token-file PATH` optionally
selects its file. Pass the token in an Authorization header or the SDK's
`token` option for that deployment. See [Python deployment](python.md#optional-authentication)
for details. The default local flow has no token file or token field.
`--no-auth` remains a compatibility flag. Use `--no-auto-connect` to start
without discovery, or `--address ADDRESS` for explicit selection.
