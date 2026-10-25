# Final integration review

Reviewed the settled shared pattern engine, direct runner, controller/transport
ownership, Web Bluetooth cleanup, Android hook/retirement and native pattern UI.
Three supported lifecycle findings were reproduced offline and sent to the
parent promptly. No implementation files, locks, global configuration, hardware,
live services, ADB or native builds were changed or used by this review.

## P1 — Ordinary runner handoffs disconnect an active BLE transaction

**Location:** `packages/sdk/src/direct-effects.ts`, `begin()` abort of the previous
command (reviewed line 113), interacting with `transport.ts:182–186`.

Starting a replacement, making a manual adjustment or pressing Stop & restore
aborts the previous command's signal immediately. If its native setter write is
still pending, the real protocol transport deliberately drops the session to
prevent further output from an uncertain transaction. That aborts the controller
lifecycle and cancels the replacement command too. Draining retains the native
promise correctly, but the new control action never applies or restores its
requested settings; the user must reconnect.

The focused runner unit tests use a fake controller that retains canceled writes
without the real transport's drop behavior, so they do not cover this integration.

**Exact offline reproduction**, from the repository root:

```sh
bun run - <<'TS'
import { DirectEffectRunner } from './packages/sdk/src/direct-effects.ts';
import { LightController } from './packages/sdk/src/controller.ts';
import { BleProtocolTransport } from './packages/sdk/src/transport.ts';
import { FakeAdapter } from './packages/sdk/test/fixtures.ts';
for (const action of ['manual', 'stop', 'replace']) {
  let enter!: () => void, release!: () => void;
  const entered = new Promise<void>(r => enter = r);
  const held = new Promise<void>(r => release = r);
  const adapter = new FakeAdapter();
  const controller = new LightController(new BleProtocolTransport(adapter));
  await controller.connect();
  const write = adapter.write.bind(adapter);
  let hold = true;
  adapter.write = async bytes => {
    // Every G60 setter frame here fits in one native chunk. Byte 12 is write mode.
    if (hold && bytes[12] === 1) { hold = false; enter(); await held; }
    await write(bytes);
  };
  const runner = new DirectEffectRunner(controller);
  await runner.start({ pattern: 'blink', period: 0.2, duration: null,
    minBrightness: 0, maxBrightness: 16, cct: 2700 });
  await entered;
  const result = (action === 'manual' ? runner.setState({ brightness: 11 })
    : action === 'stop' ? runner.stop()
    : runner.start({ pattern: 'breathe', duration: null, maxBrightness: 12 }))
    .catch(error => error);
  release();
  const actual = await result;
  console.log({ action, result: actual.name ?? 'success',
    connected: controller.state.connected,
    hardware: { brightness: adapter.brightness, cct: adapter.cct },
    disconnects: adapter.disconnects });
  await runner.dispose();
  await controller.disconnect();
}
TS
```

**Observed before correction:** all three actions return `CancelledError`, with
`connected: false`, hardware `{ brightness: 16, cct: 5000 }` and one native close.
Only the first brightness setter reaches hardware. Manual 11%, baseline
restoration (5% / 5000 K), and replacement output never occur.

**Correction boundary:** ordinary handoff needs to invalidate future scheduling
synchronously while allowing an already admitted controller transaction to settle
before replacement. Lifecycle `cancel()` / `dispose()` must still invalidate and
retain every admitted transaction, including transactions from superseded
commands. The parent accepted this finding and assigned the runner correction;
the parent owns final verification of corrected real-transport handoffs.

## P2 — Background/disconnect during Start or Stop permanently leaves Android busy

**Location:** `apps/android/src/useLight.ts:289`, together with `close():77–80`;
disabled reconnection controls are in `apps/android/App.tsx:113–125`.

`effectOperation()` sets `working` true, then waits for Start or Stop. Background,
explicit disconnect, or link loss increments the epoch and clears the session.
When that old operation settles, its `finally` clears `working` only if the old
epoch still matches. `close()` does not clear it either. Even successful complete
cleanup therefore leaves `working: true`; Find my light / Search again and nearby
Connect controls stay disabled. Returning to the foreground cannot reconnect.

**Exact executed hook reproduction:**
[artifacts/local/final-review-use-light-busy.ts](../../artifacts/local/final-review-use-light-busy.ts).
Run:

```sh
bun artifacts/local/final-review-use-light-busy.ts
```

The script reads and transpiles the unchanged `useLight.ts` body into memory,
injects local hook cells and an AppState event callback, and uses the actual
`DirectEffectRunner`, `LightController`, `BleProtocolTransport`, `EffectOwnership`
and `EffectRetirement`, with the existing offline `FakeAdapter`. It connects,
holds the first native baseline-query write of `startEffect`, dispatches
`background`, releases the write and waits for both the operation and cleanup.
It changes no globals and imports no native runtime. The script is saved only in
the ignored local artifact directory at the parent's explicit request.

**Observed before correction:**

```text
connected: working=false, connected=true
during start: working=true
after background and settled cleanup:
  working=true, connected=false, status="Ready when you are"
```

**Correction boundary:** clear the held UI operation's busy state once that
operation actually settles, while mounted. The operation gate already prevents a
replacement operation from acquiring it earlier; the existing manual/connect
paths use that behavior. Clearing busy in `close()` before the gate finishes
would expose apparently available controls while the old operation still owns
the gate. The parent accepted this finding and owns its correction and regression.

**Resolved during review:** rerunning the saved, unchanged reproduction after the
parent's scoped correction produced `working: false`, `connected: false` after
the operation and cleanup settled. The before-correction result above came from
the same harness executed in memory before saving it.

## P2 — Web Bluetooth cleanup cannot finish after a normal remote disconnect

**Location:** `packages/sdk/src/web-bluetooth.ts:73–79` in the reviewed version.

The adapter awaited `stopNotifications()` before clearing `notificationsNeedStop`
and calling `gatt.disconnect()`. After remote disconnection, stopping notifications
rejects because GATT is disconnected. Every cleanup retry called it again and
failed before native close. No native promise was still pending, but controller /
runner disposal remained unable to complete.

**Exact offline reproduction**, from the repository root:

```sh
bun run - <<'TS'
import { WebBluetoothTransport, G60_WRITE } from './packages/sdk/src/web-bluetooth.ts';
class Characteristic extends EventTarget {
  async startNotifications() { return this; }
  async stopNotifications() {
    if (!device.gatt.connected) throw new DOMException(
      'GATT Server is disconnected. Cannot stop notifications.', 'NetworkError');
    return this;
  }
  async writeValueWithResponse() {}
}
const writer = new Characteristic(), notify = new Characteristic();
const device = Object.assign(new EventTarget(), {
  id: 'offline-device',
  gatt: {
    connected: true, closes: 0,
    async connect() { this.connected = true; return this; },
    async getPrimaryService() {
      return { getCharacteristic: async (id: string) => id === G60_WRITE ? writer : notify };
    },
    disconnect() { this.closes++; this.connected = false; },
  },
});
const transport = await WebBluetoothTransport.request(
  { timeoutMs: 100 }, { requestDevice: async () => device });
await transport.connect();
device.gatt.connected = false;
device.dispatchEvent(new Event('gattserverdisconnected'));
for (const action of ['disconnect', 'drain', 'disconnect'] as const) {
  try { await transport[action](); console.log(action, 'fulfilled'); }
  catch (error) { console.log(action, error.name, error.message); }
}
console.log({ connected: transport.connected,
  nativeConnected: device.gatt.connected, closes: device.gatt.closes });
TS
```

**Observed before correction:** all three calls reject `NetworkError`, and
`closes: 0`.

**Resolved during review:** the assigned implementation agent kept the actual
notification-stop promise awaited, then accepted a settled rejection only if
`gatt.connected === false`. I reran the exact reproduction against that scoped
correction: all three calls fulfilled and `closes: 1`. The implementation agent
also reported passing regressions for live-stop rejection/retry and a pending
stop that times out, loses the link and eventually rejects; those additional
tests remain the parent's final validation responsibility.

## Bounded verification

Ran only the focused offline suites:

```sh
bun test packages/sdk/test/direct-effects.test.ts \
  packages/sdk/test/native-drain.test.ts \
  packages/sdk/test/pattern-engine.test.ts \
  apps/android/test/effect-ownership.test.ts \
  apps/android/test/effect-form.test.ts
```

Result before the scoped corrections: **86 passed, 0 failed, 2405 assertions**.
This includes the unchanged shared golden fixture's 30 configurations and 691
targets, exercised forwards, backwards and with skipped sampling. Also inspected
the controller queue/raw native work drains, finite endpoint/restoration spacing,
cancel/disposal admission guards, retained-owner retries and native form/parser
integration. No further supported parity or ownership finding was established in
that bounded surface. The parent's reported full suites and APK build were not
rerun by this reviewer.
