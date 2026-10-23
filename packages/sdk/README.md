# @lighttime/sdk

Independent TypeScript control for the actual ZHIYUN MOLUS G60: verified direct BLE brightness/CCT and authenticated local HTTP bridge support. MIT licensed; no runtime dependencies or copied vendor implementations.

```sh
bun install --frozen-lockfile
bun run typecheck
bun test
bun run build
bun run smoke
bun audit
bun run build:browser
```

```ts
import { BridgeClient } from '@lighttime/sdk';
const light = new BridgeClient({ token: tokenFromUserInput });
const actual = await light.refresh();
const verified = await light.setState({ brightness: 5, cct: 5000 });
light.dispose();
```

Brightness targets must be whole percentages (0–100); CCT targets must be multiples of 100 K (2700–6500). Power means brightness zero / restore, with a safe 5% fallback. A successful mutation requires actual device readback; cancellation cannot undo an accepted write.

Exports include `LightController`, `BleProtocolTransport`, `BleAdapter`, `WebBluetoothTransport`, `BridgeClient`, protocol helpers, contract types and structured errors. React Native resolves TypeScript source through the `react-native` export condition; ESM/types resolve built `dist`.

See the repository's `docs/typescript.md` for adapter examples, timeout/lifecycle behavior, streaming events, browser/OBS setup and deployment restrictions. `examples/browser` is the static browser webroot. No universal Web Bluetooth/browser compatibility is promised.
