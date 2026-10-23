import { describe, expect, test } from 'bun:test';
import { LightController } from '../src/controller';
import { BleProtocolTransport } from '../src/transport';
import { COMMAND } from '../src/protocol';
import { FakeAdapter } from './fixtures';
import { DirectEffectRunner } from '../src/direct-effects';
import { CancelledError, TimeoutError, VerificationError } from '../src/errors';
import { resolveEffect } from '../src/pattern-engine';
import { parseEffectStatus } from '../src/effects';
import type { EffectConfig, OperationOptions, State, StatePatch } from '../src/types';

const deferred = () => {
  let resolve!: () => void;
  const promise = new Promise<void>(finish => { resolve = finish; });
  return { promise, resolve };
};
async function flush() { for (let i = 0; i < 60; i++) await Promise.resolve(); }

class Clock {
  time = 0;
  readonly wall = Date.UTC(2026, 9, 23, 12);
  private waits = new Set<{ at: number; finish: () => void }>();
  now = () => this.time;
  wallNow = () => this.wall + this.time;
  sleep = (milliseconds: number, signal: AbortSignal): Promise<void> => new Promise((resolve, reject) => {
    const wait = { at: this.time + milliseconds, finish: () => { cleanup(); resolve(); } };
    const aborted = () => { cleanup(); reject(new CancelledError()); };
    const cleanup = () => { this.waits.delete(wait); signal.removeEventListener('abort', aborted); };
    if (signal.aborted) { reject(new CancelledError()); return; }
    this.waits.add(wait);
    signal.addEventListener('abort', aborted, { once: true });
  });
  async advance(milliseconds: number) {
    this.time += milliseconds;
    for (const wait of [...this.waits]) if (wait.at <= this.time) wait.finish();
    await flush();
  }
  get sleeping() { return this.waits.size; }
}

/** Models a deadline-raced public API while retaining cancellation-resistant native work. */
class Controller {
  private hardware = { brightness: 7, cct: 5000 };
  private cache: State = {
    connected: true, transport: 'ble', address: 'fake-g60', device: { id: 88, model: 'pl103', serial: 'fake' },
    brightness: 7, cct: 5000, power: true, updatedAt: new Date(0).toISOString(),
  };
  private listeners = new Set<(state: State) => void>();
  private owned = new Set<Promise<unknown>>();
  readonly writes: { patch: StatePatch; at: number; signal: AbortSignal | undefined }[] = [];
  readonly events: string[] = [];
  reads = 0;
  active = 0;
  maxActive = 0;
  drainFailures = 0;
  failWrite: Error | null = null;
  nextWrite: ReturnType<typeof deferred> | null = null;
  nextRead: ReturnType<typeof deferred> | null = null;
  nextDrain: ReturnType<typeof deferred> | null = null;
  afterRead: (() => void) | null = null;
  disconnected = false;
  constructor(private readonly clock: Clock) {}
  get state(): State { return { ...this.cache }; }
  get output() { return { ...this.hardware }; }
  get outstanding() { return this.owned.size; }
  get controller(): LightController { return this as unknown as LightController; }
  subscribe(listener: (state: State) => void) { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; }
  external(brightness: number, cct: number) { this.hardware = { brightness, cct }; }
  private publish() {
    this.cache = { ...this.cache, ...this.hardware, power: this.hardware.brightness > 0, updatedAt: new Date(this.clock.wallNow()).toISOString() };
    for (const listener of this.listeners) listener(this.state);
  }
  private call<T>(work: Promise<T>, options: OperationOptions): Promise<T> {
    this.owned.add(work);
    void work.then(() => this.owned.delete(work), () => this.owned.delete(work));
    return new Promise<T>((resolve, reject) => {
      const aborted = () => { cleanup(); reject(new CancelledError()); };
      const cleanup = () => options.signal?.removeEventListener('abort', aborted);
      if (options.signal?.aborted) { reject(new CancelledError()); return; }
      options.signal?.addEventListener('abort', aborted, { once: true });
      void work.then(value => { cleanup(); resolve(value); }, error => { cleanup(); reject(error); });
    });
  }
  refresh(options: OperationOptions = {}): Promise<State> {
    ++this.reads;
    this.events.push('read');
    const wait = this.nextRead; this.nextRead = null;
    return this.call((async () => {
      if (wait) await wait.promise;
      if (!options.signal?.aborted) this.publish();
      this.afterRead?.(); this.afterRead = null;
      return this.state;
    })(), options);
  }
  setState(patch: StatePatch, options: OperationOptions = {}): Promise<State> {
    if (options.signal?.aborted) return Promise.reject(new CancelledError());
    const target = { ...patch };
    const wait = this.nextWrite; this.nextWrite = null;
    const failure = this.failWrite; this.failWrite = null;
    this.writes.push({ patch: target, at: this.clock.time, signal: options.signal });
    this.events.push('write');
    ++this.active; this.maxActive = Math.max(this.maxActive, this.active);
    return this.call((async () => {
      try {
        if (wait) await wait.promise;
        if (failure) throw failure;
        const brightness = target.brightness ?? (target.power === false ? 0 : target.power === true ? 5 : undefined);
        if (brightness !== undefined) this.hardware.brightness = brightness;
        if (target.cct !== undefined) this.hardware.cct = target.cct;
        this.events.push('native settled');
        // A cancelled setter can reach hardware without updating its cached readback.
        if (!options.signal?.aborted) this.publish();
        return this.state;
      } finally { --this.active; }
    })(), options);
  }
  async drain() {
    this.events.push('drain');
    if (this.drainFailures > 0) { --this.drainFailures; throw new TimeoutError('Native drain unresolved'); }
    const wait = this.nextDrain; this.nextDrain = null;
    if (wait) await wait.promise;
    while (this.owned.size) await Promise.allSettled([...this.owned]);
  }
  async disconnect() { await this.drain(); this.disconnected = true; this.events.push('disconnect'); }
}
function setup() {
  const clock = new Clock();
  const fake = new Controller(clock);
  const runner = new DirectEffectRunner(fake.controller, clock);
  return { clock, fake, runner };
}
const blink: EffectConfig = { pattern: 'blink', duration: null, period: 0.2, minBrightness: 0, maxBrightness: 16, cct: 2700 };

describe('direct verified effect ownership', () => {
  test('captures a fresh baseline, exposes seven copied status fields, and ignores observer errors', async () => {
    const { fake, runner } = setup();
    fake.external(13, 4100);
    runner.subscribe(() => { throw new Error('observer'); });
    const status = await runner.start(blink);
    await flush();
    expect(fake.reads).toBe(1);
    expect(Object.keys(status).sort()).toEqual(['running', 'id', 'pattern', 'startedAt', 'endsAt', 'progress', 'lastError'].sort());
    expect(parseEffectStatus(status)).toEqual(status);
    expect(status.running).toBe(true);
    status.running = false;
    expect(runner.status.running).toBe(true);
    await runner.dispose();
    expect(fake.disconnected).toBe(false);
  });

  test('manual validation preserves an active effect and valid override drains without restoring', async () => {
    const { clock, fake, runner } = setup();
    await runner.start(blink); await flush();
    const id = runner.status.id;
    for (const patch of [{ brightness: 2.5 }, { cct: 2750 }, { brightness: 8, power: false }]) {
      await expect(runner.setState(patch)).rejects.toBeInstanceOf(RangeError);
      expect(runner.status.running).toBe(true);
      expect(runner.status.id).toBe(id);
    }
    const manual = runner.setState({ brightness: 9 });
    await flush(); await clock.advance(100); await manual;
    expect(runner.status.running).toBe(false);
    expect(fake.writes.at(-1)!.patch).toEqual({ brightness: 9 });
    expect(fake.output).toEqual({ brightness: 9, cct: 2700 });
    await runner.dispose();
  });

  test('invalid start/stop requests preserve an active run and accepted configs are snapshotted', async () => {
    const { clock, fake, runner } = setup();
    await runner.start(blink); await flush();
    const id = runner.status.id;
    await expect(runner.start({ ...blink, period: 0.19 })).rejects.toBeInstanceOf(RangeError);
    await expect(runner.stop({ restore: 'yes' } as unknown as { restore: boolean })).rejects.toBeInstanceOf(TypeError);
    expect(runner.status.id).toBe(id);
    expect(runner.status.running).toBe(true);
    runner.cancel();
    const read = deferred(); fake.nextRead = read;
    const config: EffectConfig = { pattern: 'keyframes', duration: null, keyframes: [
      { at: 0, brightness: 4, cct: 2700 }, { at: 1, brightness: 12, cct: 6500 },
    ] };
    const expected = resolveEffect(config, fake.state);
    const start = runner.start(config);
    await flush();
    (config.keyframes[0] as { brightness: number }).brightness = 99;
    read.resolve(); await start;
    // The replacement's first output waits for the prior write's rate slot.
    await flush(); await clock.advance(100);
    expect(fake.writes.at(-1)!.patch).toEqual(expected.target(0.1));
    await runner.dispose();
  });

  test('cancel invalidates synchronously, aborts the owned call, and prevents all later setters', async () => {
    const { clock, fake, runner } = setup();
    const native = deferred(); fake.nextWrite = native;
    await runner.start(blink); await flush();
    expect(fake.writes).toHaveLength(1);
    runner.cancel();
    expect(runner.status.running).toBe(false);
    expect(fake.writes[0]!.signal!.aborted).toBe(true);
    await clock.advance(2000);
    expect(fake.writes).toHaveLength(1);
    const cleanup = runner.dispose();
    let complete = false; void cleanup.then(() => { complete = true; });
    await flush(); expect(complete).toBe(false);
    native.resolve(); await cleanup;
    await clock.advance(2000);
    expect(fake.writes).toHaveLength(1);
    expect(clock.sleeping).toBe(0);
  });

  test('manual override lets the admitted transaction settle and never overlaps it', async () => {
    const { clock, fake, runner } = setup();
    const native = deferred(); fake.nextWrite = native;
    await runner.start(blink); await flush();
    const manual = runner.setState({ brightness: 11 });
    await flush(); await clock.advance(500);
    expect(fake.writes).toHaveLength(1);
    expect(fake.writes[0]!.signal!.aborted).toBe(false);
    native.resolve(); await manual;
    expect(fake.maxActive).toBe(1);
    expect(fake.writes.map(write => write.patch)).toEqual([{ brightness: 16, cct: 2700 }, { brightness: 11 }]);
    expect(fake.output).toEqual({ brightness: 11, cct: 2700 });
    await runner.dispose();
  });

  test('explicit stop drains first and restores both absolute fields even when cached values match baseline', async () => {
    const { clock, fake, runner } = setup();
    const native = deferred(); fake.nextWrite = native;
    await runner.start(blink); await flush();
    const stopping = runner.stop({ restore: true });
    await flush(); await clock.advance(500);
    expect(fake.state.brightness).toBe(7);
    expect(fake.writes).toHaveLength(1);
    native.resolve(); await stopping;
    expect(fake.writes.at(-1)!.patch).toEqual({ brightness: 7, cct: 5000 });
    expect(fake.output).toEqual({ brightness: 7, cct: 5000 });
    const count = fake.writes.length;
    await runner.stop();
    expect(fake.writes).toHaveLength(count);
    await runner.dispose();
  });

  test('finite completion writes its exact endpoint then restores with 100 ms write-start spacing', async () => {
    const { clock, fake, runner } = setup();
    await runner.start({ pattern: 'fade-in', duration: 1, minBrightness: 0, maxBrightness: 20, cct: 3200, restoreOnComplete: true });
    await flush();
    await clock.advance(1001);
    expect(fake.writes.at(-1)!.patch.brightness).toBe(20);
    expect(runner.status.running).toBe(true);
    await clock.advance(100);
    expect(fake.writes.at(-1)!.patch).toEqual({ brightness: 7, cct: 5000 });
    expect(runner.status.running).toBe(false);
    expect(runner.status.progress).toBe(1);
    for (let i = 1; i < fake.writes.length; i++) expect(fake.writes[i]!.at - fake.writes[i - 1]!.at).toBeGreaterThanOrEqual(100);
    await runner.dispose();
  });

  test('finite default holds the endpoint and idle stop never restores a completed run', async () => {
    const { clock, fake, runner } = setup();
    await runner.start({ pattern: 'fade-out', duration: 1, minBrightness: 0, maxBrightness: 20 }); await flush();
    await clock.advance(1001);
    expect(runner.status.running).toBe(false);
    expect(fake.output.brightness).toBe(0);
    const count = fake.writes.length;
    await runner.stop();
    expect(fake.writes).toHaveLength(count);
    expect(fake.output.brightness).toBe(0);
    await runner.dispose();
  });

  test('samples current time after a blocked drain and skips identical targets without busy polling', async () => {
    const { clock, fake, runner } = setup();
    const blocked = deferred();
    fake.afterRead = () => { fake.nextDrain = blocked; };
    const config: EffectConfig = { pattern: 'fade-in', duration: 1, minBrightness: 0, maxBrightness: 20, cct: 3200 };
    await runner.start(config); await flush();
    await clock.advance(750);
    expect(fake.writes).toHaveLength(0);
    blocked.resolve(); await flush();
    expect(fake.writes[0]!.patch).toEqual(resolveEffect(config, { brightness: 7, cct: 5000 }).target(0.75));
    await runner.dispose();
    const idle = setup();
    await idle.runner.start({ pattern: 'breathe', duration: null, minBrightness: 7, maxBrightness: 7, cct: 5000 }); await flush();
    expect(idle.fake.writes).toHaveLength(0);
    expect(idle.clock.sleeping).toBe(1);
    await idle.clock.advance(500);
    expect(idle.fake.writes).toHaveLength(0);
    expect(idle.clock.sleeping).toBe(1);
    await idle.runner.dispose();
  });

  test('a queued start superseded by stop or dispose cannot later capture/output a new run', async () => {
    const { fake, runner } = setup();
    const read = deferred(); fake.nextRead = read;
    const first = runner.start(blink); const firstResult = first.catch(error => error);
    await flush();
    const second = runner.start({ pattern: 'breathe', duration: null }); const secondResult = second.catch(error => error);
    const stop = runner.stop({ restore: true });
    await flush();
    read.resolve(); await stop;
    expect(await firstResult).toBeInstanceOf(CancelledError);
    expect(await secondResult).toBeInstanceOf(CancelledError);
    expect(fake.reads).toBe(1);
    expect(fake.writes).toHaveLength(0);
    const queued = runner.start(blink); const queuedResult = queued.catch(error => error);
    const disposed = runner.dispose();
    await disposed;
    expect(await queuedResult).toBeInstanceOf(CancelledError);
    expect(fake.writes).toHaveLength(0);
  });

  test('replacement captures its own fresh baseline after old native output settles', async () => {
    const { clock, fake, runner } = setup();
    const native = deferred(); fake.nextWrite = native;
    await runner.start(blink); await flush();
    const replacement = runner.start({ pattern: 'breathe', duration: null, minBrightness: 0, maxBrightness: 12 });
    await flush(); await clock.advance(500);
    expect(fake.reads).toBe(1);
    native.resolve(); await replacement; await flush();
    expect(fake.reads).toBe(2);
    const stop = runner.stop(); await flush(); await clock.advance(100); await stop;
    expect(fake.writes.at(-1)!.patch).toEqual({ brightness: 16, cct: 2700 });
    expect(fake.maxActive).toBe(1);
    await runner.dispose();
  });

  test('output and restoration failures stop output, persist errors, and never trigger automatic restore', async () => {
    const { clock, fake, runner } = setup();
    fake.failWrite = new VerificationError('Readback mismatch');
    await runner.start(blink); await flush();
    expect(runner.status.running).toBe(false);
    expect(runner.status.lastError).toBe('Readback mismatch');
    expect(fake.writes).toHaveLength(1);
    await clock.advance(2000);
    expect(fake.writes).toHaveLength(1);
    await runner.start(blink); await flush();
    expect(runner.status.lastError).toBeNull();
    fake.failWrite = new VerificationError('Restore mismatch');
    const stop = runner.stop(); const result = stop.catch(error => error);
    await flush(); await clock.advance(100);
    expect(await result).toBeInstanceOf(VerificationError);
    expect(runner.status.lastError).toBe('Restore mismatch');
    expect(runner.status.running).toBe(false);
    const count = fake.writes.length;
    await clock.advance(1000);
    expect(fake.writes).toHaveLength(count);
    await runner.dispose();
  });

  test('failed disposal retains pending native ownership, blocks new work, and can retry after settlement', async () => {
    const { clock, fake, runner } = setup();
    const native = deferred(); fake.nextWrite = native;
    const manual = runner.setState({ brightness: 17 }); const manualResult = manual.catch(error => error);
    await flush();
    expect(fake.outstanding).toBe(1);
    fake.drainFailures = 1;
    await expect(runner.dispose()).rejects.toBeInstanceOf(TimeoutError);
    expect(await manualResult).toBeInstanceOf(CancelledError);
    expect(fake.outstanding).toBe(1);
    expect(fake.disconnected).toBe(false);
    await expect(runner.start(blink)).rejects.toBeInstanceOf(CancelledError);
    await expect(runner.setState({ brightness: 9 })).rejects.toBeInstanceOf(CancelledError);
    const retry = runner.dispose();
    let complete = false; void retry.then(() => { complete = true; });
    await flush(); expect(complete).toBe(false);
    await clock.advance(500); native.resolve(); await retry;
    expect(fake.outstanding).toBe(0);
    expect(fake.writes).toHaveLength(1);
    expect(fake.disconnected).toBe(false);
    await runner.dispose();
  });

  test('disposal owns a cancelled baseline read and prevents late start acceptance', async () => {
    const { fake, runner } = setup();
    const read = deferred(); fake.nextRead = read;
    const start = runner.start(blink); const started = start.catch(error => error);
    await flush();
    const cleanup = runner.dispose();
    let drained = false; void cleanup.then(() => { drained = true; });
    await flush();
    expect(await started).toBeInstanceOf(CancelledError);
    expect(drained).toBe(false);
    expect(runner.status.id).toBeNull();
    read.resolve(); await cleanup;
    expect(fake.writes).toHaveLength(0);
    expect(runner.status.running).toBe(false);
  });

  test('disposal drains a submitted Stop restoration without starting another write', async () => {
    const { clock, fake, runner } = setup();
    await runner.start(blink); await flush();
    const native = deferred(); fake.nextWrite = native;
    const stop = runner.stop(); const stopped = stop.catch(error => error);
    await flush(); await clock.advance(100);
    expect(fake.writes.at(-1)!.patch).toEqual({ brightness: 7, cct: 5000 });
    const cleanup = runner.dispose();
    let drained = false; void cleanup.then(() => { drained = true; });
    await flush();
    expect(await stopped).toBeInstanceOf(CancelledError);
    expect(fake.writes.at(-1)!.signal!.aborted).toBe(true);
    expect(drained).toBe(false);
    native.resolve(); await cleanup;
    expect(fake.writes).toHaveLength(2);
    expect(fake.maxActive).toBe(1);
  });
});

async function heldBleEffect() {
  const clock = new Clock();
  const adapter = new FakeAdapter();
  const transport = new BleProtocolTransport(adapter, { maxWriteSize: 20, timeoutMs: 1000 });
  const controller = new LightController(transport, { timeoutMs: 1000, verificationDelayMs: 0 });
  await controller.connect();
  const initialFrames = adapter.frames.length;
  const entered = deferred(); const release = deferred();
  const original = adapter.write.bind(adapter);
  let held = false;
  let active = 0; let maxActive = 0;
  const setterStarts: { command: number; at: number }[] = [];
  adapter.write = async data => {
    ++active; maxActive = Math.max(maxActive, active);
    try {
      if (data[12] === 1) {
        setterStarts.push({ command: new DataView(data.buffer, data.byteOffset, data.byteLength).getUint16(8, true), at: clock.time });
        if (!held) { held = true; entered.resolve(); await release.promise; }
      }
      await original(data);
    } finally { --active; }
  };
  const runner = new DirectEffectRunner(controller, clock);
  await runner.start(blink);
  await entered.promise;
  return {
    clock, adapter, controller, transport, runner, release: release.resolve,
    maxActive: () => maxActive,
    brightnessStarts: () => setterStarts.filter(item => item.command === COMMAND.brightness).map(item => item.at),
    mutations: () => adapter.frames.slice(initialFrames).filter(frame => frame.payload[2] === 1),
  };
}

describe('handoff through the real controller and BLE transport', () => {
  for (const action of ['manual', 'stop', 'replacement'] as const) {
    test(`${action} preserves the connection while an admitted native write finishes`, async () => {
      const f = await heldBleEffect();
      try {
        const oldId = f.runner.status.id;
        const handoff = action === 'manual' ? f.runner.setState({ brightness: 11 })
          : action === 'stop' ? f.runner.stop()
            : f.runner.start({ pattern: 'breathe', duration: null, maxBrightness: 12 });
        let settled = false; void handoff.then(() => { settled = true; }, () => { settled = true; });
        expect(f.runner.status.running).toBe(false);
        await f.clock.advance(50);
        expect(settled).toBe(false);
        expect(f.transport.connected).toBe(true);
        expect(f.controller.state.connected).toBe(true);
        expect(f.adapter.disconnects).toBe(0);
        expect(f.mutations()).toHaveLength(0);
        f.release(); await flush();
        // Finish the admitted brightness+CCT+readback transaction without aborting it.
        expect(f.adapter.brightness).toBe(16);
        expect(f.adapter.cct).toBe(2700);
        expect(f.mutations().map(frame => frame.command)).toEqual([COMMAND.brightness, COMMAND.cct]);
        await f.clock.advance(50);
        await handoff; await flush();
        expect(f.controller.state.connected).toBe(true);
        expect(f.adapter.disconnects).toBe(0);
        expect(f.maxActive()).toBe(1);
        if (action === 'manual') {
          expect(f.controller.state.brightness).toBe(11);
          expect(f.controller.state.cct).toBe(2700);
          expect(f.runner.status.running).toBe(false);
        } else if (action === 'stop') {
          expect(f.controller.state.brightness).toBe(5);
          expect(f.controller.state.cct).toBe(5000);
          expect(f.runner.status.running).toBe(false);
          expect(f.mutations().slice(-2).map(frame => frame.command)).toEqual([COMMAND.brightness, COMMAND.cct]);
        } else {
          expect(f.runner.status.running).toBe(true);
          expect(f.runner.status.id).not.toBe(oldId);
          expect(f.runner.status.pattern).toBe('breathe');
          expect(f.controller.state.brightness).toBe(0);
          expect(f.controller.state.cct).toBe(2700);
          const stop = f.runner.stop(); await flush(); await f.clock.advance(100); await stop;
          // The replacement captures actual output AFTER the prior transaction settles.
          expect(f.controller.state.brightness).toBe(16);
          expect(f.controller.state.cct).toBe(2700);
        }
        const starts = f.brightnessStarts();
        for (let i = 1; i < starts.length; i++) expect(starts[i]! - starts[i - 1]!).toBeGreaterThanOrEqual(100);
      } finally {
        f.release();
        await f.runner.dispose();
        await f.controller.disconnect();
      }
    });
  }

  for (const action of ['cancel', 'dispose'] as const) {
    test(`${action} aborts I/O owned by a superseded command and drains it before cleanup`, async () => {
      const f = await heldBleEffect();
      const manual = f.runner.setState({ brightness: 11 }).catch(error => error);
      let cleanup: Promise<void>;
      try {
        if (action === 'cancel') {
          f.runner.cancel(); await flush();
          expect(f.transport.connected).toBe(false);
          cleanup = f.runner.dispose();
        } else cleanup = f.runner.dispose();
        let drained = false; void cleanup.then(() => { drained = true; });
        await flush();
        expect(drained).toBe(false);
        expect(f.adapter.disconnects).toBe(0);
        expect(await manual).toBeInstanceOf(CancelledError);
        f.release(); await cleanup;
        expect(f.mutations().map(frame => frame.command)).toEqual([COMMAND.brightness]);
        expect(f.adapter.brightness).toBe(16);
        expect(f.adapter.cct).toBe(5000);
        expect(f.maxActive()).toBe(1);
        expect(f.adapter.disconnects).toBe(1);
        await f.clock.advance(1000);
        expect(f.mutations()).toHaveLength(1);
      } finally {
        f.release();
        await f.runner.dispose();
        await f.controller.disconnect();
      }
    });
  }
});
