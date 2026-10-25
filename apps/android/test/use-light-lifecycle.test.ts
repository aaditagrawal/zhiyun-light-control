import { expect, test } from 'bun:test';
import { BleProtocolTransport, DirectEffectRunner, LightController } from '../../../packages/sdk/src';
import { FakeAdapter } from '../../../packages/sdk/test/fixtures';
import { EffectOwnership, EffectRetirement } from '../src/EffectOwnership';
import { OperationGate } from '../src/OperationGate';
import type { PermissionResult } from '../src/ble/runtime';
import type { useLight } from '../src/useLight';

// Execute the actual hook body with local hook cells. This models state and
// AppState delivery without importing React Native or changing global modules.
const source = (await Bun.file(new URL('../src/useLight.ts', import.meta.url)).text())
  .replace(/^import .*;\n/gm, '');
const js = new Bun.Transpiler({ loader: 'ts' }).transformSync(source)
  .replace('export function useLight', 'function useLight');

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(accept => { resolve = accept; });
  return { promise, resolve };
}

function fixture(options: {
  permission?: () => Promise<PermissionResult>;
  radioState?: () => Promise<string>;
  appState?: string | null;
} = {}) {
  const cells: any[] = [];
  let cursor = 0;
  const effects: (() => (() => void))[] = [];
  let background: (value: string) => void = () => {};
  let mounted = true;
  const calls = { permissions: 0, managers: 0, radioReads: 0, scans: 0, scanStops: 0, connections: 0, updatesAfterUnmount: 0 };
  const permissionRequested = deferred<void>();
  const radioRequested = deferred<void>();
  let now = 0, timerId = 0;
  const timers = new Map<number, { at: number; callback: () => void }>();
  const appState = {
    currentState: options.appState === undefined ? 'active' : options.appState,
    addEventListener: (_event: string, callback: (value: string) => void) => {
      background = callback; return { remove() { background = () => {}; } };
    },
  };
  const changeAppState = (value: string) => { appState.currentState = value; background(value); };
  const useState = (initial: any) => {
    const index = cursor++;
    if (!(index in cells)) cells[index] = typeof initial === 'function' ? initial() : initial;
    return [cells[index], (value: any) => {
      if (!mounted) ++calls.updatesAfterUnmount;
      cells[index] = typeof value === 'function' ? value(cells[index]) : value;
    }];
  };
  const useRef = (initial: any) => cells[cursor++] ??= { current: initial };
  const useEffect = (callback: () => (() => void)) => {
    const index = cursor++;
    if (!(index in cells)) { cells[index] = true; effects.push(callback); }
  };
  const native = {
    state: async () => {
      ++calls.radioReads; radioRequested.resolve();
      return options.radioState ? options.radioState() : 'PoweredOn';
    },
    onStateChange: () => ({ remove() {} }),
    startDeviceScan: async () => { ++calls.scans; },
    stopDeviceScan: async () => { ++calls.scanStops; },
    destroy: async () => {},
  };
  const adapter = new FakeAdapter();
  adapter.connect = async () => { ++calls.connections; };
  class NativeBleAdapter { constructor() { return adapter; } }
  const dependencies = {
    useState, useRef, useEffect,
    AppState: appState,
    Platform: { Version: 35 },
    BleProtocolTransport, LightController, DirectEffectRunner,
    EffectOwnership, EffectRetirement, OperationGate, NativeBleAdapter,
    advertisedLight: () => null,
    createBleManager: () => { ++calls.managers; return native; },
    requestBlePermissions: async () => {
      ++calls.permissions; permissionRequested.resolve();
      return options.permission ? options.permission() : 'granted';
    },
    runtimeIssue: () => null,
    // Local hook timers let foreground ordering and expiry run deterministically;
    // SDK native-I/O and drain timers retain their real implementations.
    setTimeout: (callback: () => void, delay: number) => {
      const id = ++timerId; timers.set(id, { at: now + delay, callback }); return id;
    },
    clearTimeout: (id: number) => { timers.delete(id); },
    Date: { now: () => now },
  };
  const hook = new Function(...Object.keys(dependencies), `${js}\nreturn useLight;`)
    (...Object.values(dependencies)) as typeof useLight;
  const render = () => { cursor = 0; return hook(); };
  const initial = render();
  const cleanups = effects.map(mount => mount());
  const holdNextWrite = () => {
    let enter!: () => void, release!: () => void;
    const entered = new Promise<void>(resolve => { enter = resolve; });
    const held = new Promise<void>(resolve => { release = resolve; });
    const write = adapter.write.bind(adapter);
    let hold = true;
    adapter.write = async bytes => {
      if (hold) { hold = false; enter(); await held; }
      await write(bytes);
    };
    return { entered, release: () => release() };
  };
  return { initial, render, holdNextWrite, calls, adapter,
    permissionRequested: permissionRequested.promise, radioRequested: radioRequested.promise,
    background: () => changeAppState('background'), foreground: () => changeAppState('active'),
    get pendingTimers() { return timers.size; },
    suspend: (ms: number) => { now += ms; },
    elapse: (ms: number) => {
      const target = now + ms;
      for (;;) {
        const next = [...timers.entries()].sort((a, b) => a[1].at - b[1].at)[0];
        if (!next || next[1].at > target) break;
        now = next[1].at; timers.delete(next[0]); next[1].callback();
      }
      now = target;
    },
    unmount: () => { mounted = false; cleanups.forEach(cleanup => cleanup()); } };
}

for (const action of ['start-background', 'stop-background', 'start-disconnect'] as const) {
  test(`actual hook releases its busy state after ${action} settles`, async () => {
    const app = fixture();
    await app.initial.connect({ id: 'offline-light', name: 'PL103_test', rssi: -40 });
    let light = app.render();
    expect(light.state?.connected).toBe(true);
    if (action === 'stop-background') {
      await light.startEffect({ pattern: 'blink', duration: null, minBrightness: 5, maxBrightness: 5, cct: 5000 });
      light = app.render();
      expect(light.effect.running).toBe(true);
    }
    const held = app.holdNextWrite();
    const operation = action === 'stop-background' ? light.stopEffect()
      : light.startEffect({ pattern: 'blink', duration: null, maxBrightness: 16 });
    await held.entered;
    expect(app.render().working).toBe(true);
    const closing = action === 'start-disconnect' ? light.disconnect() : undefined;
    if (action !== 'start-disconnect') app.background();
    held.release();
    await operation;
    await closing;
    await light.disconnect();
    const settled = app.render();
    expect(settled.working).toBe(false);
    expect(settled.state?.connected ?? false).toBe(false);
    expect(settled.effect.running).toBe(false);
    app.unmount();
  });
}

const nearbyLight = { id: 'offline-light', name: 'PL103_test', rssi: -40 };

for (const activeFirst of [true, false]) {
  test(`permission activity grant continues the same search (${activeFirst ? 'active' : 'result'} first)`, async () => {
    const permission = deferred<PermissionResult>();
    const app = fixture({ permission: () => permission.promise });
    const search = app.initial.scan();
    await app.permissionRequested;
    app.background();
    expect(app.render().working).toBe(true);
    expect(app.render().message).not.toContain('Connection closed');
    if (activeFirst) app.foreground();
    permission.resolve('granted');
    if (!activeFirst) {
      await Bun.sleep(0);
      expect(app.render().working).toBe(true);
      expect(app.calls.managers).toBe(0);
      expect(app.calls.scans).toBe(0);
      await app.initial.connect(nearbyLight);
      expect(app.calls.connections).toBe(0);
      app.foreground();
    }
    await search;
    expect(app.calls.permissions).toBe(1);
    expect(app.calls.scans).toBe(1);
    expect(app.render().scanning).toBe(true);
    expect(app.render().hasScanned).toBe(true);
    expect(app.render().working).toBe(false);

    // The exception ends before real BLE work; a later background still closes it.
    app.background();
    await Bun.sleep(0);
    expect(app.calls.scanStops).toBe(1);
    expect(app.render().scanning).toBe(false);
    expect(app.render().message).toContain('Connection closed while the app was in the background');
    app.foreground();
    await Bun.sleep(0);
    expect(app.calls.scans).toBe(1);
    app.unmount();
  });
}

for (const result of ['denied', 'blocked'] as const) {
  for (const activeFirst of [true, false]) {
    test(`permission activity ${result} keeps its permission guidance (${activeFirst ? 'active' : 'result'} first)`, async () => {
      const permission = deferred<PermissionResult>();
      const app = fixture({ permission: () => permission.promise });
      const search = app.initial.scan();
      await app.permissionRequested;
      app.background();
      if (activeFirst) app.foreground();
      permission.resolve(result);
      await search;
      const message = app.render().message;
      if (!activeFirst) app.foreground();
      expect(app.render().message).toBe(message);
      expect(message).toBe(result === 'blocked'
        ? 'Allow Bluetooth access in app settings, then search again.'
        : 'Nearby devices permission is needed to search for and connect to the G60.');
      expect(app.render().permissionBlocked).toBe(result === 'blocked');
      expect(app.render().error).toBe(true);
      expect(app.render().working).toBe(false);
      expect(app.render().hasScanned).toBe(false);
      expect(app.calls.managers).toBe(0);
      expect(app.calls.scans).toBe(0);
      expect(app.calls.connections).toBe(0);
      app.unmount();
    });
  }
}

test('Home during a permission request keeps ownership until settlement, then expires automatic resume', async () => {
  const permission = deferred<PermissionResult>();
  const app = fixture({ permission: () => permission.promise });
  let settled = false;
  const search = app.initial.scan().then(() => { settled = true; });
  await app.permissionRequested;
  app.background();
  app.elapse(30_000);
  await Bun.sleep(0);
  expect(settled).toBe(false);
  expect(app.render().working).toBe(true);
  permission.resolve('granted');
  await Bun.sleep(0);
  expect(app.calls.managers).toBe(0);
  expect(app.calls.scans).toBe(0);
  expect(app.render().working).toBe(true);
  app.elapse(5_000);
  await search;
  expect(app.render().working).toBe(false);
  expect(app.render().message).toContain('Connection closed while the app was in the background');
  await app.initial.scan();
  await app.initial.connect(nearbyLight);
  expect(app.calls.permissions).toBe(1);
  expect(app.calls.managers).toBe(0);
  app.foreground();
  await Bun.sleep(0);
  expect(app.calls.scans).toBe(0);
  // Only a new user search can retry once the foreground grace period expired.
  await app.initial.scan();
  expect(app.calls.scans).toBe(1);
  app.unmount();
});

test('a late foreground event cannot beat a background-delayed grace timer', async () => {
  const permission = deferred<PermissionResult>();
  const app = fixture({ permission: () => permission.promise });
  const search = app.initial.scan();
  await app.permissionRequested;
  app.background();
  permission.resolve('granted');
  await Bun.sleep(0);
  app.suspend(30_000);
  // Android can deliver active before running JS timers suspended in background.
  app.foreground();
  await search;
  expect(app.render().working).toBe(false);
  expect(app.calls.managers).toBe(0);
  expect(app.calls.scans).toBe(0);
  expect(app.pendingTimers).toBe(0);
  expect(app.render().message).toContain('Connection closed while the app was in the background');
  app.unmount();
});

for (const action of ['stopScan', 'disconnect'] as const) {
  for (const result of ['granted', 'blocked'] as const) {
    test(`${action} invalidates a pending ${result} result without releasing native permission ownership`, async () => {
      const permission = deferred<PermissionResult>();
      let requests = 0;
      const app = fixture({ permission: () => ++requests === 1 ? permission.promise : Promise.resolve('granted') });
      let settled = false;
      const search = app.initial.scan().then(() => { settled = true; });
      await app.permissionRequested;
      app.background();
      await app.initial[action]();
      const cancelledMessage = app.render().message;
      app.foreground();
      await app.initial.scan();
      await app.initial.connect(nearbyLight);
      expect(settled).toBe(false);
      expect(app.render().working).toBe(true);
      expect(app.calls.permissions).toBe(1);
      expect(app.calls.managers).toBe(0);
      permission.resolve(result);
      await search;
      expect(app.render().message).toBe(cancelledMessage);
      expect(app.render().permissionBlocked).toBe(false);
      expect(app.render().working).toBe(false);
      expect(app.calls.scans).toBe(0);
      expect(app.calls.connections).toBe(0);
      await app.initial.scan();
      expect(app.calls.permissions).toBe(2);
      expect(app.calls.scans).toBe(1);
      app.unmount();
    });
  }
}

for (const result of ['granted', 'denied', 'blocked'] as const) {
  test(`unmount while the permission prompt is pending ignores a late ${result} result`, async () => {
    const permission = deferred<PermissionResult>();
    const app = fixture({ permission: () => permission.promise });
    let settled = false;
    const search = app.initial.scan().then(() => { settled = true; });
    await app.permissionRequested;
    app.background();
    app.unmount();
    app.foreground();
    await app.initial.scan();
    await app.initial.connect(nearbyLight);
    await Bun.sleep(0);
    expect(settled).toBe(false);
    expect(app.calls.permissions).toBe(1);
    permission.resolve(result);
    await search;
    expect(app.calls.managers).toBe(0);
    expect(app.calls.scans).toBe(0);
    expect(app.calls.connections).toBe(0);
    expect(app.calls.updatesAfterUnmount).toBe(0);
    expect(app.pendingTimers).toBe(0);
  });
}

for (const action of ['stopScan', 'disconnect', 'unmount'] as const) {
  test(`${action} cancels the bounded foreground wait without BLE work or a leaked timer`, async () => {
    const permission = deferred<PermissionResult>();
    const app = fixture({ permission: () => permission.promise });
    const search = app.initial.scan();
    await app.permissionRequested;
    app.background();
    permission.resolve('granted');
    await Bun.sleep(0);
    expect(app.render().working).toBe(true);
    expect(app.pendingTimers).toBe(1);
    if (action === 'unmount') app.unmount();
    else await app.initial[action]();
    await search;
    expect(app.pendingTimers).toBe(0);
    app.foreground();
    app.elapse(30_000);
    expect(app.calls.managers).toBe(0);
    expect(app.calls.scans).toBe(0);
    expect(app.calls.updatesAfterUnmount).toBe(0);
    if (action !== 'unmount') { expect(app.render().working).toBe(false); app.unmount(); }
  });
}

for (const action of ['scan', 'connect'] as const) {
  test(`real background during ${action} radio preparation blocks native I/O and stale status`, async () => {
    const status = deferred<string>();
    const app = fixture({ radioState: () => status.promise });
    const operation = action === 'scan' ? app.initial.scan() : app.initial.connect(nearbyLight);
    await app.radioRequested;
    app.background();
    const message = app.render().message;
    status.resolve('PoweredOff');
    await operation;
    expect(app.render().message).toBe(message);
    expect(app.render().working).toBe(false);
    expect(app.render().scanning).toBe(false);
    expect(app.calls.scans).toBe(0);
    expect(app.calls.connections).toBe(0);
    expect(app.adapter.writes).toBe(0);
    await app.initial.scan();
    await app.initial.connect(nearbyLight);
    expect(app.calls.radioReads).toBe(1);
    app.foreground();
    await Bun.sleep(0);
    expect(app.calls.scans).toBe(0);
    expect(app.calls.connections).toBe(0);
    app.unmount();
  });
}

for (const appState of ['background', 'inactive', null]) {
  test(`a hook mounted with ${appState ?? 'unknown'} AppState cannot initiate permissions or BLE`, async () => {
    const app = fixture({ appState });
    await app.initial.scan();
    await app.initial.connect(nearbyLight);
    expect(app.calls.permissions).toBe(0);
    expect(app.calls.managers).toBe(0);
    expect(app.calls.scans).toBe(0);
    expect(app.calls.connections).toBe(0);
    expect(app.render().working).toBe(false);
    app.unmount();
  });
}
