import { expect, test } from 'bun:test';
import { OperationGate } from '../src/OperationGate';

test('rapid UI events never queue overlapping slider or power writes', async () => {
  const gate = new OperationGate();
  const events: string[] = [];
  let release!: () => void;
  const pending = gate.run(async () => {
    events.push('brightness');
    await new Promise<void>((resolve) => { release = resolve; });
    events.push('readback');
  });
  expect(await gate.run(async () => { events.push('cct'); })).toBe(false);
  expect(await gate.run(async () => { events.push('power'); })).toBe(false);
  release();
  await pending;
  expect(events).toEqual(['brightness', 'readback']);
  expect(gate.busy).toBe(false);
});

test('a failed operation releases the UI gate for readback recovery', async () => {
  const gate = new OperationGate();
  await expect(gate.run(async () => { throw new Error('offline'); })).rejects.toThrow('offline');
  expect(await gate.run(async () => {})).toBe(true);
});
