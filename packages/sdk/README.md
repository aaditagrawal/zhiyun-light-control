# @lighttime/sdk

Independent TypeScript control for the ZHIYUN MOLUS G60: verified direct BLE
brightness/CCT and a shared local HTTP bridge. MIT licensed; no runtime
dependencies or copied vendor implementations.

Turn on the G60 and run `uv run --frozen lighttime` from the repository root.
The bridge connects one nearby PL103 automatically; no address or token setup
is needed. Open `http://localhost:8877/` for the controller.

The SDK is not published to npm. Build it here, then install it in your app
with `bun add /absolute/path/to/zhiyun-light-control/packages/sdk`, or install
a locally packed archive. Use the manifest-pinned Bun:

```sh
bun install --frozen-lockfile
bun run typecheck
bun test
bun run build
bun run smoke
bun audit
bun run build:browser
```

For SDK or browser changes that ship with the Python bridge, run
`bun scripts/build-web.ts` from the repository root. This canonical build also
copies the controller and `/lighttime.js` module into Python's static assets.

```ts
import { BridgeClient } from '@lighttime/sdk';
const light = new BridgeClient();
try {
  const actual = await light.connect();
  const verified = await light.setState({ brightness: 5, cct: 5000 });
  console.log({ actual, verified });
} catch (error) {
  console.error(error);
} finally {
  light.dispose();
}
```

The default endpoint is `http://127.0.0.1:8877`. For a site served by the
bridge, use `new BridgeClient({ baseUrl: location.origin })`. A no-build HTML
page can import `{ BridgeClient, EFFECT_PRESETS }` from `/lighttime.js` instead
of installing the package. Run the example with
`uv run --frozen lighttime serve --web-root examples/site-controller`.
Remote apps need a configured reachable private HTTPS bridge URL or a
same-origin proxy; the browser's localhost is its own device.

`connect()` without arguments reuses the existing connection with fresh
readback, or lets the bridge discover one G60. Several candidates require
explicit selection. Authentication is optional via `serve --auth`; only those
deployments need the SDK's `token` option.

Brightness targets must be whole percentages (0–100); CCT targets must be multiples of 100 K (2700–6500). Power means brightness zero / restore, with a safe 5% fallback. A successful mutation requires actual device readback; cancellation cannot undo an accepted write.

Exports include `LightController`, `BleProtocolTransport`, `BleAdapter`, `WebBluetoothTransport`, `BridgeClient`, protocol helpers, contract types and structured errors. React Native resolves TypeScript source through the `react-native` export condition; ESM/types resolve built `dist`.

Start with [website integration](../../docs/integrating-sites.md) or see
[the TypeScript guide](../../docs/typescript.md) for adapters, lifecycle,
streaming events and browser/OBS setup. `examples/browser` is the full
controller; `examples/site-controller` is the no-build integration example.
No universal direct Web Bluetooth/browser compatibility is promised.
