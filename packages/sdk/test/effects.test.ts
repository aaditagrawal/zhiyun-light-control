import { describe, expect, test } from 'bun:test';
import { BridgeClient, CancelledError, ProtocolError, TimeoutError } from '../src';
import type { EffectConfig, EffectPattern, EffectStatus } from '../src';
import { state } from './fixtures';

const idle: EffectStatus = { running: false, id: null, pattern: null, startedAt: null, endsAt: null, progress: null, lastError: null };
const running: EffectStatus = { running: true, id: 'effect-1', pattern: 'breathe', startedAt: '2026-10-23T12:00:00+00:00',
  endsAt: '2026-10-23T12:00:10+00:00', progress: 0.25, lastError: null };
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const create = (handler: (url: string, init: RequestInit) => Response | Promise<Response>, timeoutMs?: number) =>
  new BridgeClient({ token: 'offline-dummy-token', ...(timeoutMs === undefined ? {} : { timeoutMs }), fetch: (input, init) => Promise.resolve(handler(String(input), init ?? {})) });

describe('bridge effect contract', () => {
  test('public types, endpoint bodies, headers, defaults, and all patterns match the contract', async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    const client = create((url, init) => { calls.push({ url, init }); return json(url.endsWith('/start') ? running : idle); });
    expect(await client.effect()).toEqual(idle);
    for (const pattern of ['blink', 'breathe', 'fade-in', 'fade-out', 'cct-sweep'] satisfies EffectPattern[]) {
      const config: EffectConfig = { pattern, duration: 1, period: 60, minBrightness: 0, maxBrightness: 100, cct: 2700, cctEnd: 6500, restoreOnComplete: false };
      expect(await client.startEffect(config)).toEqual(running);
      expect(JSON.parse(calls.at(-1)!.init.body as string)).toEqual(config);
    }
    await client.startEffect({ pattern: 'blink', duration: null });
    expect(JSON.parse(calls.at(-1)!.init.body as string)).toEqual({ pattern: 'blink', duration: null });
    await client.stopEffect(); expect(JSON.parse(calls.at(-1)!.init.body as string)).toEqual({});
    await client.stopEffect({ restore: false }); expect(JSON.parse(calls.at(-1)!.init.body as string)).toEqual({ restore: false });
    expect(calls[0]!.url).toBe('http://127.0.0.1:8877/v1/effects');
    expect(calls[0]!.init.method).toBe('GET'); expect(calls[0]!.init.body).toBeUndefined();
    expect(calls[1]!.url.endsWith('/v1/effects/start')).toBe(true);
    expect(calls.at(-1)!.url.endsWith('/v1/effects/stop')).toBe(true);
    for (const { url, init } of calls) {
      expect(new Headers(init.headers).get('Authorization')).toBe('Bearer offline-dummy-token');
      expect(url).not.toContain('offline-dummy-token');
      expect(init.credentials).toBe('omit'); expect(init.redirect).toBe('error');
    }
    client.dispose();
  });
  test('invalid and unknown config/stop values reject before any output request', async () => {
    let calls = 0; const client = create(() => { calls++; return json(idle); });
    const invalid = [null, [], {}, { pattern: 'rainbow' }, { pattern: 'blink', extra: true },
      ...[0, 3601, NaN, Infinity, '10', true].map(duration => ({ pattern: 'blink', duration })),
      { pattern: 'fade-in', duration: null }, { pattern: 'fade-out', duration: null },
      ...[0, 61, NaN, Infinity, null, '4'].map(period => ({ pattern: 'blink', period })),
      ...[-1, 101, 0.1, NaN, Infinity, null, true].flatMap(value => ['minBrightness', 'maxBrightness'].map(key => ({ pattern: 'blink', [key]: value }))),
      { pattern: 'blink', minBrightness: 51, maxBrightness: 50 },
      ...[2600, 6600, 4501, 4550, NaN, null, '5000'].flatMap(value => ['cct', 'cctEnd'].map(key => ({ pattern: 'cct-sweep', [key]: value }))),
      { pattern: 'breathe', restoreOnComplete: null }, { pattern: 'blink', restoreOnComplete: 'true' }];
    for (const config of invalid) await expect(client.startEffect(config as EffectConfig)).rejects.toBeInstanceOf(Error);
    for (const stop of [null, [], { extra: true }, { restore: 1 }, { restore: null }]) {
      await expect(client.stopEffect(stop as { restore?: boolean })).rejects.toBeInstanceOf(TypeError);
    }
    expect(calls).toBe(0); client.dispose();
  });
  test('malformed status rejects for reads and both mutations; finite, unbounded, and failed status are accepted', async () => {
    const invalid = [null, [], {}, { ...idle, running: 1 }, { ...idle, id: '' }, { ...idle, pattern: 'unknown' },
      { ...idle, startedAt: 'today' }, { ...idle, startedAt: '2026-02-30T12:00:00Z' }, { ...idle, startedAt: '2026-10-23T24:00:00Z' },
      { ...idle, endsAt: '2026-10-23' }, { ...idle, progress: -0.1 }, { ...idle, progress: 1.1 },
      { ...idle, progress: '0' }, { ...idle, lastError: false }, { ...idle, running: true },
      { ...running, progress: null }, { ...running, endsAt: null }, { ...running, endsAt: '2026-10-23T11:00:00Z' }];
    for (const response of invalid) {
      const client = create(() => json(response));
      await expect(client.effect()).rejects.toBeInstanceOf(ProtocolError);
      await expect(client.startEffect({ pattern: 'blink' })).rejects.toBeInstanceOf(ProtocolError);
      await expect(client.stopEffect()).rejects.toBeInstanceOf(ProtocolError); client.dispose();
    }
    for (const response of [running, { ...running, endsAt: null, progress: null }, { ...running, running: false, progress: 1 },
      { ...idle, lastError: 'Readback verification failed' }]) {
      const client = create(() => json(response)); expect(await client.effect()).toEqual(response); client.dispose();
    }
  });
  test('effect operations have 60-second defaults independent of the general timeout, with bounded bodies and overrides', async () => {
    const client = create(async () => { await Bun.sleep(15); return json(idle); }, 1);
    await expect(client.effect()).resolves.toEqual(idle);
    await expect(client.startEffect({ pattern: 'blink' })).resolves.toEqual(idle);
    await expect(client.stopEffect()).resolves.toEqual(idle);
    await expect(client.effect({ timeoutMs: 1 })).rejects.toBeInstanceOf(TimeoutError);
    await expect(client.startEffect({ pattern: 'blink' }, { timeoutMs: 1 })).rejects.toBeInstanceOf(TimeoutError);
    await expect(client.stopEffect({}, { timeoutMs: 1 })).rejects.toBeInstanceOf(TimeoutError);
    client.dispose();
    const body = create(() => new Response(new ReadableStream({ start() {} })));
    await expect(body.effect({ timeoutMs: 5 })).rejects.toBeInstanceOf(TimeoutError); body.dispose();
  });
  test('effect mutations serialize with manual writes and disconnect; expired queued effects never dispatch', async () => {
    const paths: string[] = []; let finish!: () => void;
    const gate = new Promise<void>(resolve => { finish = resolve; });
    const client = create(async (url) => {
      const path = new URL(url).pathname; paths.push(path);
      if (paths.length === 1) await gate;
      return json(path.includes('/effects') ? idle : state);
    });
    const first = client.setState({ brightness: 5 });
    const expired = client.startEffect({ pattern: 'blink' }, { timeoutMs: 5 });
    const start = client.startEffect({ pattern: 'breathe' });
    const stop = client.stopEffect({ restore: true }); const disconnect = client.disconnect();
    await expect(expired).rejects.toBeInstanceOf(TimeoutError);
    expect(paths).toEqual(['/v1/state']); finish();
    await Promise.all([first, start, stop, disconnect]);
    expect(paths).toEqual(['/v1/state', '/v1/effects/start', '/v1/effects/stop', '/v1/disconnect']); client.dispose();
  });
  test('abort and dispose cancel active/queued effects without sending a stop or late mutation', async () => {
    const paths: string[] = [];
    const client = create(url => { paths.push(new URL(url).pathname); return new Promise(() => {}); });
    const controller = new AbortController(); const read = client.effect({ signal: controller.signal }); controller.abort();
    await expect(read).rejects.toBeInstanceOf(CancelledError);
    const start = client.startEffect({ pattern: 'blink' }); const stop = client.stopEffect();
    const cancelled = Promise.all([start.catch(error => error), stop.catch(error => error)]);
    await Bun.sleep(1); client.dispose();
    for (const error of await cancelled) expect(error).toBeInstanceOf(CancelledError);
    await expect(client.startEffect({ pattern: 'blink' })).rejects.toBeInstanceOf(CancelledError);
    await Bun.sleep(1); expect(paths).toEqual(['/v1/effects', '/v1/effects/start']);
  });
});
