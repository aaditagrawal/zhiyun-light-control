import { describe, expect, test } from 'bun:test';
import { LightController } from '../src/controller';
import { BleProtocolTransport } from '../src/transport';
import type { CommandTransport } from '../src/transport';
import { CancelledError, DisconnectedError, ProtocolError, TimeoutError } from '../src/errors';
import { COMMAND, RESPONSE_TYPE } from '../src/protocol';
import type { Frame } from '../src/protocol';
import { G60_WRITE, WebBluetoothTransport } from '../src/web-bluetooth';
import { FakeAdapter } from './fixtures';

function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
async function expectPending(work: Promise<unknown>): Promise<void> {
  let settled = false;
  void work.then(() => { settled = true; }, () => { settled = true; });
  await Bun.sleep(1);
  expect(settled).toBe(false);
}
function holdWrite(adapter: FakeAdapter) {
  const entered = deferred(); const release = deferred();
  const write = adapter.write.bind(adapter);
  let starts = 0;
  adapter.write = async bytes => {
    starts++; entered.resolve();
    await release.promise;
    await write(bytes);
  };
  return { entered: entered.promise, release: () => release.resolve(), starts: () => starts };
}
async function webCleanupFixture(stopWork: () => Promise<void>, timeoutMs = 100) {
  let stops = 0; let closes = 0;
  class Characteristic extends EventTarget {
    async writeValueWithResponse(_bytes: Uint8Array<ArrayBuffer>): Promise<void> {}
    async startNotifications(): Promise<this> { return this; }
    async stopNotifications(): Promise<this> { stops++; await stopWork(); return this; }
  }
  const characteristic = new Characteristic();
  const device = Object.assign(new EventTarget(), {
    id: 'cleanup-web-device',
    gatt: {
      connected: true,
      async connect() { return this; },
      async getPrimaryService(_uuid: string) { return { getCharacteristic: async (_characteristic: string) => characteristic }; },
      disconnect() { closes++; this.connected = false; },
    },
  });
  const transport = await WebBluetoothTransport.request({ timeoutMs }, { requestDevice: async () => device });
  await transport.connect();
  return { transport, device, stops: () => stops, closes: () => closes };
}

/** A third-party transport with no drain hook and intentionally ignored signals. */
class UncooperativeTransport implements CommandTransport {
  readonly address = 'uncooperative';
  connected = false;
  brightness = 5; cct = 5000; closes = 0;
  readonly sends: number[] = [];
  readonly requests: number[] = [];
  beforeConnect: (() => Promise<void>) | undefined;
  beforeSend: (() => Promise<void>) | undefined;
  beforeRequest: (() => Promise<void>) | undefined;
  wrongModel = false;
  async connect(): Promise<void> { await this.beforeConnect?.(); this.connected = true; }
  async disconnect(): Promise<void> { this.closes++; this.connected = false; }
  onDisconnect() { return () => {}; }
  async send(command: number, payload: Uint8Array): Promise<void> {
    this.sends.push(command);
    await this.beforeSend?.();
    const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
    if (command === COMMAND.brightness) this.brightness = view.getFloat32(3, true);
    if (command === COMMAND.cct) this.cct = view.getUint16(3, true);
  }
  async request(command: number, payload = new Uint8Array()): Promise<Frame> {
    this.requests.push(command);
    await this.beforeRequest?.();
    let result: Uint8Array;
    if (command === COMMAND.info) result = new TextEncoder().encode(`serial\0${this.wrongModel ? 'wrong' : 'pl103'}\0`);
    else if (command === COMMAND.deviceId) result = new Uint8Array(2);
    else {
      result = payload.slice();
      const view = new DataView(result.buffer);
      if (command === COMMAND.brightness) view.setFloat32(3, this.brightness, true);
      else view.setUint16(3, this.cct, true);
    }
    return { command, payload: result, sequence: 1, messageType: RESPONSE_TYPE };
  }
}

describe('owned native work drain', () => {
  test('controller timeout retains uncooperative work; queued expiration never starts another field or read', async () => {
    const transport = new UncooperativeTransport();
    const controller = new LightController(transport, { timeoutMs: 100 });
    await controller.connect();
    const entered = deferred(); const release = deferred();
    transport.beforeSend = async () => { entered.resolve(); await release.promise; };
    const first = controller.setState({ brightness: 20, cct: 3200 }, { timeoutMs: 5 }).catch(error => error);
    await entered.promise;
    const second = controller.setState({ cct: 6000 }, { timeoutMs: 10 }).catch(error => error);
    expect(await first).toBeInstanceOf(TimeoutError);
    expect(await second).toBeInstanceOf(TimeoutError);
    const draining = controller.drain();
    await expectPending(draining);
    expect(transport.sends).toEqual([COMMAND.brightness]);
    release.resolve(); await draining;
    expect(transport.sends).toEqual([COMMAND.brightness]);
    expect(transport.requests).toEqual([COMMAND.info, COMMAND.deviceId, COMMAND.brightness, COMMAND.cct]);
    expect(controller.state.brightness).toBe(5);
    await controller.disconnect();
  });

  test('replacement mutation waits for cancelled underlying work, with no late old CCT or verification', async () => {
    const transport = new UncooperativeTransport(); const controller = new LightController(transport);
    await controller.connect();
    const entered = deferred(); const release = deferred(); const cancel = new AbortController();
    transport.beforeSend = async () => { entered.resolve(); await release.promise; };
    const first = controller.setState({ brightness: 20, cct: 3200 }, { signal: cancel.signal }).catch(error => error);
    await entered.promise; cancel.abort();
    expect(await first).toBeInstanceOf(CancelledError);
    const replacement = controller.setState({ cct: 6000 });
    await expectPending(replacement); expect(transport.sends).toEqual([COMMAND.brightness]);
    transport.beforeSend = undefined; release.resolve();
    expect((await replacement).cct).toBe(6000);
    expect(transport.sends).toEqual([COMMAND.brightness, COMMAND.cct]);
    expect(transport.requests.slice(4)).toEqual([COMMAND.brightness, COMMAND.cct]);
    await controller.disconnect();
  });

  test('cancelled refresh cannot issue its second read or publish a stale snapshot', async () => {
    const transport = new UncooperativeTransport(); const controller = new LightController(transport);
    await controller.connect();
    const baseline = controller.state.updatedAt;
    const entered = deferred(); const release = deferred(); const cancel = new AbortController();
    transport.beforeRequest = async () => { entered.resolve(); await release.promise; };
    const refresh = controller.refresh({ signal: cancel.signal }).catch(error => error);
    await entered.promise; cancel.abort(); expect(await refresh).toBeInstanceOf(CancelledError);
    release.resolve(); await controller.drain();
    expect(transport.requests.slice(4)).toEqual([COMMAND.brightness]);
    expect(controller.state.updatedAt).toBe(baseline);
    await controller.disconnect();
  });

  test('abort during the final readback field prevents stale publication and verification retries', async () => {
    const transport = new UncooperativeTransport(); const controller = new LightController(transport);
    await controller.connect(); const baseline = controller.state.updatedAt;
    const entered = deferred(); const release = deferred(); const cancel = new AbortController();
    transport.beforeRequest = async () => {
      if (transport.requests.at(-1) === COMMAND.brightness) transport.brightness = 5;
      else { entered.resolve(); await release.promise; }
    };
    const writing = controller.setState({ brightness: 20 }, { signal: cancel.signal }).catch(error => error);
    await entered.promise; cancel.abort(); expect(await writing).toBeInstanceOf(CancelledError);
    release.resolve(); await controller.drain();
    expect(transport.requests.slice(4)).toEqual([COMMAND.brightness, COMMAND.cct]);
    expect(controller.state.updatedAt).toBe(baseline); expect(controller.state.brightness).toBe(5);
    await controller.disconnect();
  });

  test('disconnect invalidates synchronously and waits for controller work before native close', async () => {
    const transport = new UncooperativeTransport(); const controller = new LightController(transport);
    await controller.connect();
    const entered = deferred(); const release = deferred();
    transport.beforeSend = async () => { entered.resolve(); await release.promise; };
    const writing = controller.setState({ brightness: 30, cct: 3200 }).catch(error => error);
    await entered.promise;
    const closing = controller.disconnect(); const duplicate = controller.disconnect();
    expect(controller.state.connected).toBe(false);
    expect(await writing).toBeInstanceOf(DisconnectedError);
    await expectPending(closing); expect(transport.closes).toBe(0);
    release.resolve(); await Promise.all([closing, duplicate, controller.drain()]);
    expect(transport.sends).toEqual([COMMAND.brightness]); expect(transport.closes).toBe(1);
  });

  test('bounded controller cleanup retains a timed-out queue until later settlement', async () => {
    const transport = new UncooperativeTransport(); const controller = new LightController(transport, { timeoutMs: 15 });
    await controller.connect();
    const entered = deferred(); const release = deferred();
    transport.beforeSend = async () => { entered.resolve(); await release.promise; };
    const writing = controller.setState({ brightness: 20 }, { timeoutMs: 5 }).catch(error => error);
    await entered.promise; expect(await writing).toBeInstanceOf(TimeoutError);
    await expect(controller.drain()).rejects.toThrow('unresolved work remains owned');
    await expect(controller.disconnect()).rejects.toBeInstanceOf(TimeoutError);
    expect(transport.closes).toBe(0);
    release.resolve(); await controller.drain(); expect(transport.closes).toBe(1);
  });

  test('connect failure cleanup drains outside its own queue instead of deadlocking', async () => {
    const transport = new UncooperativeTransport(); transport.wrongModel = true;
    const controller = new LightController(transport, { timeoutMs: 100 });
    await expect(controller.connect()).rejects.toBeInstanceOf(ProtocolError);
    await controller.drain(); expect(transport.closes).toBe(1);
    expect(controller.state.connected).toBe(false);
  });

  test('failure during initial readback blocks an already queued setter before cleanup runs', async () => {
    const transport = new UncooperativeTransport(); let requests = 0;
    transport.beforeRequest = async () => { if (++requests === 4) throw new ProtocolError('initial readback failed'); };
    const controller = new LightController(transport);
    const connecting = controller.connect().catch(error => error);
    const queued = controller.setState({ brightness: 20 }).catch(error => error);
    expect(await connecting).toBeInstanceOf(ProtocolError);
    expect(await queued).toBeInstanceOf(DisconnectedError);
    await controller.drain(); expect(transport.sends).toEqual([]); expect(transport.closes).toBe(1);
  });

  test('controller retains failed third-party transport cleanup for a later drain retry', async () => {
    const transport = new UncooperativeTransport(); const controller = new LightController(transport);
    await controller.connect();
    transport.disconnect = async () => { if (++transport.closes === 1) throw new Error('transport close failed'); };
    await expect(controller.disconnect()).rejects.toThrow('transport close failed');
    expect(controller.state.connected).toBe(false);
    await controller.drain(); expect(transport.closes).toBe(2);
    await controller.disconnect(); expect(transport.closes).toBe(2);
  });

  test('cancelled uncooperative connect cannot start device queries and closes after settlement', async () => {
    const transport = new UncooperativeTransport(); const entered = deferred(); const release = deferred();
    transport.beforeConnect = async () => { entered.resolve(); await release.promise; };
    const controller = new LightController(transport); const cancel = new AbortController();
    const connecting = controller.connect({ signal: cancel.signal }).catch(error => error);
    await entered.promise; cancel.abort(); expect(await connecting).toBeInstanceOf(CancelledError);
    const draining = controller.drain(); await expectPending(draining); expect(transport.closes).toBe(0);
    release.resolve(); await draining;
    expect(transport.requests).toEqual([]); expect(transport.closes).toBe(1);
  });

  test('write timeout owns the native chunk, blocks queued writes and delays native close', async () => {
    const adapter = new FakeAdapter(); const transport = new BleProtocolTransport(adapter, { maxWriteSize: 3 });
    await transport.connect(); const gate = holdWrite(adapter);
    const first = transport.send(COMMAND.info, new Uint8Array(20), { timeoutMs: 5 }).catch(error => error);
    await gate.entered;
    const queued = transport.send(COMMAND.deviceId, new Uint8Array()).catch(error => error);
    expect(await first).toBeInstanceOf(TimeoutError); expect(await queued).toBeInstanceOf(DisconnectedError);
    expect(transport.connected).toBe(false); expect(adapter.disconnects).toBe(0);
    const draining = transport.drain(); await expectPending(draining);
    gate.release(); await draining;
    expect(gate.starts()).toBe(1); expect(adapter.disconnects).toBe(1);
  });

  test('controller drain sees native work after nested transport timeout has already rejected', async () => {
    const adapter = new FakeAdapter(); const transport = new BleProtocolTransport(adapter, { timeoutMs: 10 });
    const controller = new LightController(transport); await controller.connect();
    const gate = holdWrite(adapter);
    const writing = controller.setState({ brightness: 20, cct: 3200 }).catch(error => error);
    await gate.entered; await writing;
    expect(controller.state.connected).toBe(false); expect(adapter.disconnects).toBe(0);
    const draining = controller.drain(); await expectPending(draining);
    gate.release(); await draining; expect(adapter.disconnects).toBe(1); expect(gate.starts()).toBe(1);
  });

  test('runner disposal owns a cancelled manual native write through drain before controller disconnect', async () => {
    const { DirectEffectRunner } = await import('../src/direct-effects');
    const adapter = new FakeAdapter(); const controller = new LightController(new BleProtocolTransport(adapter));
    await controller.connect(); const runner = new DirectEffectRunner(controller); const gate = holdWrite(adapter);
    const manual = runner.setState({ brightness: 20, cct: 3200 }).catch(error => error);
    await gate.entered;
    const disposing = runner.dispose();
    expect(await manual).toBeInstanceOf(CancelledError);
    await expectPending(disposing); expect(adapter.disconnects).toBe(0);
    const closing = controller.disconnect(); await expectPending(closing);
    gate.release(); await Promise.all([disposing, closing]);
    expect(gate.starts()).toBe(1); expect(adapter.disconnects).toBe(1);
    expect(adapter.frames.slice(4).map(frame => frame.command)).toEqual([COMMAND.brightness]);
  });

  test('runner disposal reports bounded drain failure and retry still owns the raw manual write', async () => {
    const { DirectEffectRunner } = await import('../src/direct-effects');
    const transport = new UncooperativeTransport(); const controller = new LightController(transport, { timeoutMs: 15 });
    await controller.connect(); const runner = new DirectEffectRunner(controller);
    const entered = deferred(); const release = deferred();
    transport.beforeSend = async () => { entered.resolve(); await release.promise; };
    const manual = runner.setState({ brightness: 20, cct: 3200 }).catch(error => error);
    await entered.promise;
    await expect(runner.dispose()).rejects.toThrow('unresolved work remains owned');
    expect(await manual).toBeInstanceOf(CancelledError);
    await expect(runner.setState({ cct: 6000 })).rejects.toBeInstanceOf(CancelledError);
    const retry = runner.dispose(); await expectPending(retry);
    release.resolve(); await retry;
    expect(transport.sends).toEqual([COMMAND.brightness]);
    await controller.disconnect(); expect(transport.closes).toBe(1);
  });

  test('transport disconnect drains an aborted request and deduplicates concurrent close', async () => {
    const adapter = new FakeAdapter(); const transport = new BleProtocolTransport(adapter); await transport.connect();
    const gate = holdWrite(adapter);
    const request = transport.request(COMMAND.info).catch(error => error); await gate.entered;
    const closing = transport.disconnect(); const duplicate = transport.disconnect();
    expect(transport.connected).toBe(false); expect(await request).toBeInstanceOf(DisconnectedError);
    await expectPending(closing); expect(adapter.disconnects).toBe(0);
    gate.release(); await Promise.all([closing, duplicate, transport.drain()]);
    expect(adapter.disconnects).toBe(1);
  });

  test('late notification setup is retained, unsubscribed once and settled before close', async () => {
    const adapter = new FakeAdapter(); const subscribe = adapter.subscribe.bind(adapter);
    const entered = deferred(); const release = deferred();
    adapter.subscribe = async listener => {
      const off = await subscribe(listener); entered.resolve(); await release.promise; return off;
    };
    const transport = new BleProtocolTransport(adapter);
    const connecting = transport.connect({ timeoutMs: 5 }).catch(error => error);
    await entered.promise; expect(await connecting).toBeInstanceOf(TimeoutError);
    expect(adapter.disconnects).toBe(0);
    const draining = transport.drain(); await expectPending(draining);
    release.resolve(); await draining;
    expect(adapter.subscriptions).toBe(0); expect(adapter.disconnects).toBe(1);
  });

  test('native close timeout retains its actual promise; retry never submits a duplicate close', async () => {
    const adapter = new FakeAdapter(); const transport = new BleProtocolTransport(adapter, { timeoutMs: 10 });
    await transport.connect(); const release = deferred(); let closes = 0;
    adapter.disconnect = async () => { closes++; await release.promise; };
    await expect(transport.disconnect()).rejects.toThrow('unresolved work remains owned');
    await expect(transport.drain()).rejects.toBeInstanceOf(TimeoutError); expect(closes).toBe(1);
    release.resolve(); await transport.drain(); await transport.disconnect(); expect(closes).toBe(1);
  });

  test('failed native close keeps the session closed and retries cleanup on a later drain', async () => {
    const adapter = new FakeAdapter(); const transport = new BleProtocolTransport(adapter); await transport.connect();
    let closes = 0;
    adapter.disconnect = async () => { if (++closes === 1) throw new Error('native close failed'); };
    await expect(transport.disconnect()).rejects.toThrow('native close failed');
    expect(transport.connected).toBe(false);
    await expect(transport.send(COMMAND.info, new Uint8Array())).rejects.toBeInstanceOf(DisconnectedError);
    await transport.drain(); expect(closes).toBe(2);
    await transport.disconnect(); expect(closes).toBe(2);
  });

  test('failed subscription cleanup remains owned and retryable instead of losing the handle', async () => {
    const adapter = new FakeAdapter(); const subscribe = adapter.subscribe.bind(adapter); let attempts = 0;
    adapter.subscribe = async listener => {
      const off = await subscribe(listener);
      return () => { if (++attempts <= 2) throw new Error('unsubscribe failed'); off(); };
    };
    const transport = new BleProtocolTransport(adapter); await transport.connect();
    await expect(transport.disconnect()).rejects.toThrow('unsubscribe failed');
    expect(adapter.disconnects).toBe(0); expect(adapter.subscriptions).toBe(1);
    await transport.drain(); expect(adapter.subscriptions).toBe(0); expect(adapter.disconnects).toBe(1);
  });

  test('Web Bluetooth waits for native write and stopNotifications before disconnecting GATT', async () => {
    const writing = deferred(); const finishWrite = deferred(); const stopping = deferred(); const finishStop = deferred();
    const order: string[] = [];
    class Characteristic extends EventTarget {
      constructor(private readonly writeWork = async () => {}, private readonly stopWork = async () => {}) { super(); }
      async writeValueWithResponse(_bytes: Uint8Array<ArrayBuffer>): Promise<void> { await this.writeWork(); }
      async startNotifications(): Promise<this> { return this; }
      async stopNotifications(): Promise<this> { await this.stopWork(); return this; }
    }
    const writer = new Characteristic(async () => {
      order.push('write start'); writing.resolve(); await finishWrite.promise; order.push('write settled');
    });
    const notify = new Characteristic(undefined, async () => {
      order.push('stop start'); stopping.resolve(); await finishStop.promise; order.push('stop settled');
    });
    const device = Object.assign(new EventTarget(), {
      id: 'drain-web-device',
      gatt: {
        connected: true,
        async connect() { return this; },
        async getPrimaryService(_uuid: string) {
          return { getCharacteristic: async (uuid: string) => uuid === G60_WRITE ? writer : notify };
        },
        disconnect() { order.push('GATT close'); },
      },
    });
    const transport = await WebBluetoothTransport.request({}, { requestDevice: async () => device });
    await transport.connect();
    const send = transport.send(COMMAND.info, new Uint8Array(), { timeoutMs: 5 }).catch(error => error);
    await writing.promise; expect(await send).toBeInstanceOf(TimeoutError);
    const draining = transport.drain();
    expect(order).toEqual(['write start']);
    finishWrite.resolve(); await stopping.promise;
    await expectPending(draining); expect(order).toEqual(['write start', 'write settled', 'stop start']);
    finishStop.resolve(); await draining;
    expect(order).toEqual(['write start', 'write settled', 'stop start', 'stop settled', 'GATT close']);
  });

  test('Web Bluetooth remote disconnect completes cleanup when the settled notification stop rejects', async () => {
    const fixture = await webCleanupFixture(async () => { throw new DOMException('GATT Server is disconnected.', 'NetworkError'); });
    fixture.device.gatt.connected = false;
    fixture.device.dispatchEvent(new Event('gattserverdisconnected'));
    expect(fixture.transport.connected).toBe(false);
    await fixture.transport.disconnect(); await fixture.transport.drain(); await fixture.transport.disconnect();
    expect(fixture.stops()).toBe(1); expect(fixture.closes()).toBe(1);
  });

  test('Web Bluetooth live notification stop failure retains cleanup for a later successful retry', async () => {
    const failure = new DOMException('Notification shutdown failed.', 'NetworkError'); let failing = true;
    const fixture = await webCleanupFixture(async () => { if (failing) throw failure; });
    await expect(fixture.transport.disconnect()).rejects.toBe(failure);
    expect(fixture.device.gatt.connected).toBe(true); expect(fixture.closes()).toBe(0); expect(fixture.stops()).toBe(1);
    failing = false; await fixture.transport.drain(); await fixture.transport.disconnect();
    expect(fixture.stops()).toBe(2); expect(fixture.closes()).toBe(1);
  });

  test('Web Bluetooth remote loss cannot release an unsettled notification stop or submit duplicate cleanup', async () => {
    const stopping = deferred(); const release = deferred();
    const fixture = await webCleanupFixture(async () => {
      stopping.resolve(); await release.promise;
      throw new DOMException('GATT Server is disconnected.', 'NetworkError');
    }, 10);
    const closing = fixture.transport.disconnect().catch(error => error);
    await stopping.promise;
    fixture.device.gatt.connected = false;
    fixture.device.dispatchEvent(new Event('gattserverdisconnected'));
    expect(await closing).toBeInstanceOf(TimeoutError);
    await expect(fixture.transport.drain()).rejects.toBeInstanceOf(TimeoutError);
    expect(fixture.closes()).toBe(0); expect(fixture.stops()).toBe(1);
    release.resolve(); await fixture.transport.drain(); await fixture.transport.disconnect();
    expect(fixture.stops()).toBe(1); expect(fixture.closes()).toBe(1);
  });
});
