import { describe, expect, test } from 'bun:test';
import type { BleManager, Device } from 'react-native-ble-plx';
import { fromByteArray, toByteArray } from 'base64-js';
import { BleProtocolTransport, crc16Xmodem, LightController } from '@lighttime/sdk';
import { NativeBleAdapter } from '../src/ble/NativeBleAdapter';
import { advertisedLight, G60_NOTIFY, G60_SERVICE, G60_WRITE } from '../src/ble/constants';
import captures from './fixtures/g60-reads.json';

const bytes = (hex: string) => Uint8Array.from(hex.match(/../g)!.map((pair) => Number.parseInt(pair, 16)));
const hex = (data: Uint8Array) => Array.from(data, (byte) => byte.toString(16).padStart(2, '0')).join('');
type Notify = (error: Error | null, characteristic: { value: string } | null) => void;

function nativeHarness({ unsupported = false, ignoreWrites = false } = {}) {
  let notify: Notify | undefined;
  let dropped: (() => void) | undefined;
  let connected = false;
  let brightness = 5;
  let cct = 5000;
  let buffered: number[] = [];
  let monitorRemovals = 0;
  let disconnectRemovals = 0;
  let cancelled = 0;
  const frames: string[] = [];
  const chunks: number[] = [];
  const device = {
    id: 'test-g60',
    discoverAllServicesAndCharacteristics: async () => device,
    services: async () => [{ uuid: G60_SERVICE }],
    characteristicsForService: async (service: string) => {
      expect(service).toBe(G60_SERVICE);
      return unsupported ? [] : [
        { uuid: G60_WRITE, isWritableWithResponse: true },
        { uuid: G60_NOTIFY, isNotifiable: true },
      ];
    },
    monitorCharacteristicForService: (service: string, characteristic: string, listener: Notify) => {
      expect(service).toBe(G60_SERVICE); expect(characteristic).toBe(G60_NOTIFY);
      notify = listener;
      return { remove: () => { ++monitorRemovals; notify = undefined; } };
    },
    writeCharacteristicWithResponseForService: async (service: string, characteristic: string, value: string) => {
      expect(service).toBe(G60_SERVICE); expect(characteristic).toBe(G60_WRITE);
      const chunk = toByteArray(value); chunks.push(chunk.length); buffered.push(...chunk);
      while (buffered.length >= 4) {
        const length = buffered[2]! | buffered[3]! << 8;
        if (buffered.length < length + 6) break;
        const frame = Uint8Array.from(buffered.splice(0, length + 6)); frames.push(hex(frame));
        const view = new DataView(frame.buffer);
        const command = view.getUint16(8, true);
        if ((command === 0x1001 || command === 0x1002) && frame[12] === 1) {
          if (!ignoreWrites) {
            if (command === 0x1001) brightness = view.getFloat32(13, true);
            else cct = view.getUint16(13, true);
          }
          // Actual G60 never emits a protocol response to these writes.
          continue;
        }
        const capture = command === 0x2003 ? captures.info : command === 0x2005 ? captures.id
          : command === 0x1001 ? captures.brightness : captures.cct;
        const response = bytes(capture.rx);
        const responseView = new DataView(response.buffer);
        responseView.setUint16(6, view.getUint16(6, true), true);
        if (command === 0x1001) responseView.setFloat32(13, brightness, true);
        if (command === 0x1002) responseView.setUint16(13, cct, true);
        responseView.setUint16(response.length - 2, crc16Xmodem(response.subarray(4, -2)), true);
        // Real native callbacks carry Base64, with notification boundaries unrelated to frames.
        for (const part of [response.slice(0, 7), response.slice(7)]) {
          notify?.(null, { value: fromByteArray(part) });
        }
      }
      return {};
    },
  };
  const native = {
    connectToDevice: async () => { connected = true; return device; },
    onDeviceDisconnected: (_id: string, listener: () => void) => {
      dropped = listener;
      return { remove: () => { ++disconnectRemovals; dropped = undefined; } };
    },
    cancelDeviceConnection: async () => { ++cancelled; connected = false; dropped?.(); return device; },
    isDeviceConnected: async () => connected,
  };
  return {
    manager: native as unknown as BleManager, device: device as unknown as Device, frames, chunks,
    monitorError: () => notify?.(new Error('GATT failure'), null),
    get monitorRemovals() { return monitorRemovals; }, get disconnectRemovals() { return disconnectRemovals; },
    get cancelled() { return cancelled; },
  };
}

describe('Android BLE integration with captured G60 frames', () => {
  test('discovers a PL103 from its name without requiring service advertisements', () => {
    expect(advertisedLight({ id: 'a', localName: 'PL103_EDFE', name: null, rssi: -61 }))
      .toEqual({ id: 'a', name: 'PL103_EDFE', rssi: -61 });
    expect(advertisedLight({ id: 'b', localName: null, name: 'pl103_demo', rssi: null })?.id).toBe('b');
    expect(advertisedLight({ id: 'c', localName: 'Unrelated', name: null, rssi: -40 })).toBeNull();
  });

  test('connects, decodes fragmented native notifications, and uses exactly the captured queries', async () => {
    const native = nativeHarness();
    const light = new LightController(new BleProtocolTransport(new NativeBleAdapter(native.manager, 'test-g60')));
    const state = await light.connect();
    expect(native.frames).toEqual([captures.info.tx, captures.id.tx, captures.brightness.tx, captures.cct.tx]);
    expect(state).toMatchObject({ connected: true, brightness: 5, cct: 5000, power: true, device: { id: 0, model: 'pl103', serial: '08a409e0c1100113' } });
    await light.disconnect();
    expect(native.monitorRemovals).toBe(1);
    expect(native.disconnectRemovals).toBe(1);
    expect(native.cancelled).toBeGreaterThan(0);
  });

  test('writes without waiting for a protocol reply, then queries and verifies; power restores brightness', async () => {
    const native = nativeHarness();
    const light = new LightController(new BleProtocolTransport(new NativeBleAdapter(native.manager, 'test-g60')), { verificationDelayMs: 0 });
    await light.connect();
    expect(await light.setState({ brightness: 12, cct: 3200 })).toMatchObject({ brightness: 12, cct: 3200 });
    expect(await light.setState({ power: false })).toMatchObject({ brightness: 0, power: false });
    expect(await light.setState({ power: true })).toMatchObject({ brightness: 12, power: true });
    expect(native.chunks.every((length) => length <= 20)).toBe(true);
    await light.disconnect();
  });

  test('rejects unsupported G60 resolution before reaching native BLE writes', async () => {
    const native = nativeHarness();
    const light = new LightController(new BleProtocolTransport(new NativeBleAdapter(native.manager, 'test-g60')));
    await light.connect();
    const initialFrames = native.frames.length;
    for (const patch of [{ brightness: 5.5 }, { brightness: 6.7 }, { cct: 4501 }, { cct: 4550 }, { cct: 4599 }]) {
      // Both synchronous validation and asynchronous rejection must leave the wire untouched.
      let failure: unknown;
      try { await light.setState(patch); } catch (error) { failure = error; }
      expect(failure).toBeInstanceOf(RangeError);
      expect(native.frames.length).toBe(initialFrames);
    }
    await light.disconnect();
  });

  test('does not confirm an ignored hardware write', async () => {
    const native = nativeHarness({ ignoreWrites: true });
    const light = new LightController(new BleProtocolTransport(new NativeBleAdapter(native.manager, 'test-g60')), { verificationDelayMs: 0 });
    await light.connect();
    await expect(light.setState({ brightness: 80 })).rejects.toThrow('Readback did not match');
    expect(light.state.brightness).toBe(5);
    await light.disconnect();
  });

  test('rejects unsupported GATT characteristics and closes the native connection', async () => {
    const native = nativeHarness({ unsupported: true });
    const adapter = new NativeBleAdapter(native.manager, 'test-g60');
    await expect(adapter.connect()).rejects.toThrow('Unsupported light');
    expect(native.frames).toEqual([]);
    expect(native.cancelled).toBeGreaterThan(0);
  });

  test('notification errors invalidate readback and close native subscriptions', async () => {
    const native = nativeHarness();
    const light = new LightController(new BleProtocolTransport(new NativeBleAdapter(native.manager, 'test-g60')));
    await light.connect();
    native.monitorError();
    expect(light.state.connected).toBe(false);
    expect(native.monitorRemovals).toBe(1);
    expect(native.disconnectRemovals).toBe(1);
    await expect(light.refresh()).rejects.toThrow();
    await light.disconnect();
  });

  test('cancels a connection which completes after cleanup', async () => {
    const native = nativeHarness();
    let release!: (device: Device) => void;
    const pending = new Promise<Device>((resolve) => { release = resolve; });
    native.manager.connectToDevice = () => pending;
    const adapter = new NativeBleAdapter(native.manager, 'test-g60');
    const connecting = adapter.connect().catch((error: unknown) => error);
    await adapter.disconnect();
    release(native.device);
    expect(await connecting).toBeInstanceOf(Error);
    expect(native.cancelled).toBeGreaterThanOrEqual(2);
    expect(native.frames).toEqual([]);
  });
});
