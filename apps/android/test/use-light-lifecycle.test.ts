import { expect, test } from 'bun:test';
import { BleProtocolTransport, DirectEffectRunner, LightController } from '../../../packages/sdk/src';
import { FakeAdapter } from '../../../packages/sdk/test/fixtures';
import { EffectOwnership, EffectRetirement } from '../src/EffectOwnership';
import { OperationGate } from '../src/OperationGate';
import type { useLight } from '../src/useLight';

// Execute the actual hook body with local hook cells. This models state and
// AppState delivery without importing React Native or changing global modules.
const source = (await Bun.file(new URL('../src/useLight.ts', import.meta.url)).text())
  .replace(/^import .*;\n/gm, '');
const js = new Bun.Transpiler({ loader: 'ts' }).transformSync(source)
  .replace('export function useLight', 'function useLight');

function fixture() {
  const cells: any[] = [];
  let cursor = 0;
  const effects: (() => (() => void))[] = [];
  let background: (value: string) => void = () => {};
  const useState = (initial: any) => {
    const index = cursor++;
    if (!(index in cells)) cells[index] = typeof initial === 'function' ? initial() : initial;
    return [cells[index], (value: any) => {
      cells[index] = typeof value === 'function' ? value(cells[index]) : value;
    }];
  };
  const useRef = (initial: any) => cells[cursor++] ??= { current: initial };
  const useEffect = (callback: () => (() => void)) => {
    const index = cursor++;
    if (!(index in cells)) { cells[index] = true; effects.push(callback); }
  };
  const native = {
    state: async () => 'PoweredOn',
    onStateChange: () => ({ remove() {} }),
    stopDeviceScan: async () => {},
    destroy: async () => {},
  };
  const adapter = new FakeAdapter();
  class NativeBleAdapter { constructor() { return adapter; } }
  const dependencies = {
    useState, useRef, useEffect,
    AppState: { addEventListener: (_event: string, callback: (value: string) => void) => {
      background = callback; return { remove() {} };
    } },
    Platform: { Version: 35 },
    BleProtocolTransport, LightController, DirectEffectRunner,
    EffectOwnership, EffectRetirement, OperationGate, NativeBleAdapter,
    advertisedLight: () => null,
    createBleManager: () => native,
    requestBlePermissions: async () => 'granted',
    runtimeIssue: () => null,
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
  return { initial, render, holdNextWrite, background: () => background('background'),
    unmount: () => cleanups.forEach(cleanup => cleanup()) };
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
