import { describe, expect, test } from 'bun:test';
import { BleProtocolTransport, CancelledError, COMMAND, DisconnectedError, encodeFrame, REQUEST_TYPE, TimeoutError, WebBluetoothTransport, G60_SERVICE } from '../src';
import { FakeAdapter } from './fixtures';
const tick = () => new Promise(resolve => setTimeout(resolve, 2));
describe('BLE lifecycle and correlation', () => {
  test('requires both sequence and command, and only accepts response type', async () => {
    const adapter = new FakeAdapter(); adapter.automatic = false;
    const transport = new BleProtocolTransport(adapter); await transport.connect();
    let resolved = false;
    const pending = transport.request(COMMAND.info).then(frame => { resolved = true; return frame; });
    await tick(); const frame = adapter.frames[0]!;
    adapter.reply({ ...frame, command: COMMAND.deviceId });
    adapter.reply({ ...frame, sequence: frame.sequence + 1 });
    adapter.notification?.(encodeFrame({ ...frame, messageType: REQUEST_TYPE }));
    await tick(); expect(resolved).toBe(false);
    adapter.reply(frame, Uint8Array.of(7)); expect((await pending).payload).toEqual(Uint8Array.of(7));
    await transport.disconnect(); expect(adapter.subscriptions).toBe(0); expect(adapter.disconnected).toBeUndefined();
  });
  test('timeout cleans correlation and late replies cannot match subsequent work', async () => {
    const adapter = new FakeAdapter(); adapter.automatic = false;
    const transport = new BleProtocolTransport(adapter); await transport.connect();
    await expect(transport.request(COMMAND.info, undefined, { timeoutMs: 8 })).rejects.toBeInstanceOf(TimeoutError);
    const first = adapter.frames[0]!;
    const pending = transport.request(COMMAND.info); await tick();
    adapter.reply(first); adapter.reply(adapter.frames[1]!, Uint8Array.of(9));
    expect((await pending).payload[0]).toBe(9); expect(adapter.frames[1]!.sequence).not.toBe(first.sequence);
    await transport.disconnect();
  });
  test('disconnect rejects pending work and removes listeners', async () => {
    const adapter = new FakeAdapter(); adapter.automatic = false;
    const transport = new BleProtocolTransport(adapter); await transport.connect();
    const pending = transport.request(COMMAND.info); await tick(); adapter.drop();
    await expect(pending).rejects.toBeInstanceOf(DisconnectedError);
    expect(adapter.subscriptions).toBe(0); expect(transport.connected).toBe(false);
    await expect(transport.request(COMMAND.info)).rejects.toBeInstanceOf(DisconnectedError);
    await expect(transport.connect()).rejects.toBeInstanceOf(DisconnectedError);
  });
  test('abort before dispatch has no write; in-flight abort removes request', async () => {
    const adapter = new FakeAdapter(); adapter.automatic = false;
    const transport = new BleProtocolTransport(adapter); await transport.connect();
    const cancel = new AbortController(); cancel.abort();
    await expect(transport.request(COMMAND.info, undefined, { signal: cancel.signal })).rejects.toBeInstanceOf(CancelledError);
    expect(adapter.writes).toBe(0);
    const next = new AbortController(); const pending = transport.request(COMMAND.info, undefined, { signal: next.signal });
    await tick(); next.abort(); await expect(pending).rejects.toBeInstanceOf(CancelledError);
    await transport.disconnect();
  });
  test('write with no protocol reply succeeds, chunks never interleave', async () => {
    const adapter = new FakeAdapter(); adapter.automatic = false;
    const transport = new BleProtocolTransport(adapter, { maxWriteSize: 3 }); await transport.connect();
    await Promise.all([transport.send(COMMAND.brightness, new Uint8Array(7)), transport.send(COMMAND.cct, new Uint8Array(5))]);
    expect(adapter.frames.map(frame => frame.command)).toEqual([COMMAND.brightness, COMMAND.cct]);
    await transport.disconnect();
  });
  test('hung native writes time out and close session, queued writes are never sent', async () => {
    const adapter = new FakeAdapter();
    adapter.write = async () => new Promise(() => {});
    const transport = new BleProtocolTransport(adapter); await transport.connect();
    const first = transport.send(COMMAND.info, new Uint8Array(), { timeoutMs: 10 });
    const second = transport.send(COMMAND.info, new Uint8Array(), { timeoutMs: 30 });
    const results = await Promise.allSettled([first, second]);
    expect(results[0]!.status).toBe('rejected'); expect(results[1]!.status).toBe('rejected');
    expect((results[0] as PromiseRejectedResult).reason).toBeInstanceOf(TimeoutError);
    expect((results[1] as PromiseRejectedResult).reason).toBeInstanceOf(DisconnectedError);
    expect(transport.connected).toBe(false); expect(adapter.subscriptions).toBe(0);
  });
  test('failed notification setup and late native connect are cleaned up', async () => {
    const broken = new FakeAdapter(); broken.subscribe = async () => { throw new Error('subscribe failed'); };
    await expect(new BleProtocolTransport(broken).connect()).rejects.toThrow('subscribe failed');
    expect(broken.disconnected).toBeUndefined(); expect(broken.disconnects).toBeGreaterThan(0);
    const late = new FakeAdapter(); let finish!: () => void;
    late.connect = () => new Promise<void>(resolve => { finish = resolve; });
    const transport = new BleProtocolTransport(late);
    await expect(transport.connect({ timeoutMs: 5 })).rejects.toBeInstanceOf(TimeoutError);
    expect(late.disconnects).toBe(0);
    finish(); await transport.drain(); expect(late.disconnects).toBe(1); expect(late.subscriptions).toBe(0);
  });
  test('Web Bluetooth picker uses PL103 namePrefix with optional FEE9', async () => {
    let options: unknown;
    const device = Object.assign(new EventTarget(), { id: 'browser-device' });
    const transport = await WebBluetoothTransport.request({}, { requestDevice: async value => { options = value; return device; } });
    expect(options).toEqual({ filters: [{ namePrefix: 'PL103' }], optionalServices: [G60_SERVICE] });
    expect(transport.address).toBe('browser-device'); await transport.disconnect();
  });
});
