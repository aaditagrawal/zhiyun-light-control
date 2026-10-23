import { expect, test } from 'bun:test';
import { EffectOwnership, EffectRetirement } from '../src/EffectOwnership';

test('background/disconnect cancels synchronously and drains before disconnecting', async () => {
  const events: string[] = [];
  let release!: () => void;
  const owner = new EffectOwnership({
    cancel: () => { events.push('cancel'); },
    dispose: async () => { events.push('drain'); await new Promise<void>(resolve => { release = resolve; }); events.push('settled'); },
  }, async () => { events.push('disconnect'); });
  const pending = owner.close();
  expect(events).toEqual(['cancel', 'drain']);
  expect(owner.close()).toBe(pending);
  expect(events).toEqual(['cancel', 'drain', 'cancel']);
  release();
  await pending;
  expect(events.at(-1)).toBe('disconnect');
  expect(events.indexOf('settled')).toBeLessThan(events.indexOf('disconnect'));
});

test('unresolved drain keeps native resources owned; cleanup can retry', async () => {
  let attempts = 0;
  let disconnected = false;
  const owner = new EffectOwnership({
    cancel: () => {}, dispose: async () => { if (++attempts === 1) throw new Error('still owned'); },
  }, async () => { disconnected = true; });
  await expect(owner.close()).rejects.toThrow('still owned');
  expect(disconnected).toBe(false);
  await owner.close();
  expect(disconnected).toBe(true);
});

test('a retired failed connection remains owned and blocks replacement until retry drains', async () => {
  const retired = new EffectRetirement();
  const events: string[] = [];
  let attempts = 0;
  const failedConnection = new EffectOwnership({
    cancel: () => { events.push('cancel'); },
    dispose: async () => { events.push('drain'); if (++attempts === 1) throw new Error('owned write pending'); },
  }, async () => { events.push('disconnect'); });
  retired.retain(failedConnection);
  retired.retain(failedConnection);
  const reconnect = async () => { await retired.drain(); events.push('new connection / manager cleanup allowed'); };
  await expect(reconnect()).rejects.toThrow('owned write pending');
  expect(retired.ownedCount).toBe(1);
  expect(events).toEqual(['cancel', 'drain']);
  await reconnect();
  expect(retired.ownedCount).toBe(0);
  expect(events).toEqual(['cancel', 'drain', 'cancel', 'drain', 'disconnect', 'new connection / manager cleanup allowed']);
});
