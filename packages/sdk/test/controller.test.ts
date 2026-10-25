import { describe, expect, test } from 'bun:test';
import { BleProtocolTransport, COMMAND, LightController, VerificationError } from '../src';
import { FakeAdapter } from './fixtures';
const setup = async () => {
  const adapter = new FakeAdapter(); const controller = new LightController(new BleProtocolTransport(adapter), { verificationDelayMs: 1 });
  await controller.connect(); return { adapter, controller };
};
describe('actual G60 readback controller', () => {
  test('connect queries info and ID before reading the actual state', async () => {
    const { adapter, controller } = await setup();
    expect(controller.state.brightness).toBe(5); expect(controller.state.cct).toBe(5000);
    expect(controller.state.device?.model).toBe('pl103');
    expect(adapter.frames.map(frame => frame.command)).toEqual([COMMAND.info, COMMAND.deviceId, COMMAND.brightness, COMMAND.cct]);
    await controller.disconnect();
  });
  test('write without reply is verified by actual readback; power restores last nonzero value', async () => {
    const { controller } = await setup();
    expect((await controller.setState({ brightness: 23, cct: 3500 })).brightness).toBe(23);
    expect((await controller.setState({ power: false })).brightness).toBe(0);
    expect((await controller.setState({ power: true })).brightness).toBe(23);
    await controller.disconnect();
  });
  test('does not claim successful control when hardware ignores a write', async () => {
    const { adapter, controller } = await setup(); adapter.ignoreWrites = true;
    await expect(controller.setState({ brightness: 50 })).rejects.toBeInstanceOf(VerificationError);
    expect(controller.state.brightness).toBe(5);
    expect(adapter.frames.filter(frame => frame.command === COMMAND.brightness && frame.payload[2] === 0)).toHaveLength(4);
    await controller.disconnect();
  });
  test('mutations serialize through the complete write/readback transaction', async () => {
    const { adapter, controller } = await setup();
    const [first, second] = await Promise.all([controller.setState({ brightness: 20 }), controller.setState({ brightness: 30 })]);
    expect(first.brightness).toBe(20); expect(second.brightness).toBe(30);
    expect(adapter.frames.slice(4).map(frame => [frame.command, frame.payload[2]])).toEqual([
      [COMMAND.brightness, 1], [COMMAND.brightness, 0], [COMMAND.cct, 0],
      [COMMAND.brightness, 1], [COMMAND.brightness, 0], [COMMAND.cct, 0],
    ]);
    await controller.disconnect();
  });
  test('validates inputs and contradictory power before any write', async () => {
    const { adapter, controller } = await setup(); const count = adapter.frames.length;
    expect(() => controller.setState({ brightness: NaN })).toThrow();
    expect(() => controller.setState({ cct: 2700.5 })).toThrow();
    expect(() => controller.setState({ brightness: 5.5 })).toThrow('whole percent');
    expect(() => controller.setState({ brightness: 6.7 })).toThrow('whole percent');
    expect(() => controller.setState({ cct: 4501 })).toThrow('multiple of 100');
    expect(() => controller.setState({ cct: 4550 })).toThrow('multiple of 100');
    expect(() => controller.setState({ power: false, brightness: 5 })).toThrow();
    expect(adapter.frames).toHaveLength(count); await controller.disconnect();
  });
  test('subscriber exceptions cannot turn verified writes into failures; snapshots are isolated', async () => {
    const { adapter, controller } = await setup(); controller.subscribe(() => { throw Error('UI bug'); });
    const result = await controller.setState({ cct: 3200 }); result.device!.id = 77;
    expect(controller.state.device!.id).toBe(0); adapter.drop(); expect(controller.state.connected).toBe(false);
  });
  test('initially off G60 restores the safe 5% fallback', async () => {
    const adapter = new FakeAdapter(); adapter.brightness = 0;
    const controller = new LightController(new BleProtocolTransport(adapter)); await controller.connect();
    expect((await controller.setState({ power: true })).brightness).toBe(5);
    await controller.disconnect();
  });
});
