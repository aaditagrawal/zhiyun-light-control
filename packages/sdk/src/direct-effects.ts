import { pause, SerialQueue } from './async';
import type { LightController } from './controller';
import { parseEffectConfig } from './effects';
import { CancelledError, DisconnectedError } from './errors';
import { resolveEffect } from './pattern-engine';
import type { ResolvedEffect } from './pattern-engine';
import { validatePatch } from './types';
import type { EffectConfig, EffectStatus, State, StatePatch, Unsubscribe } from './types';

const WRITE_SPACING_MS = 100;
const IDLE_RESAMPLE_SECONDS = 1;
let nextId = 0;

export interface DirectEffectRunnerOptions {
  /** Monotonic milliseconds. Defaults to performance.now(). */
  now?: () => number;
  /** Unix milliseconds, used only for status timestamps and IDs. */
  wallNow?: () => number;
  /** An abortable millisecond wait; injectable for deterministic clocks. */
  sleep?: (milliseconds: number, signal: AbortSignal) => Promise<void>;
}

interface Command {
  readonly generation: number;
  /** Cancels scheduled work; ordinary handoff must not abort an admitted BLE transaction. */
  readonly abort: AbortController;
}
interface Run {
  readonly command: Command;
  readonly effect: ResolvedEffect;
  readonly baseline: { brightness: number; cct: number };
  readonly id: string;
  readonly started: number;
  readonly startedAt: number;
  running: boolean;
  elapsed: number;
}

/** One local effect owner. Dispose it before disconnecting/replacing its controller. */
export class DirectEffectRunner {
  private readonly queue = new SerialQueue();
  private readonly listeners = new Set<(status: EffectStatus) => void>();
  private readonly loops = new Set<Promise<void>>();
  private readonly calls = new Set<AbortController>();
  private readonly now: () => number;
  private readonly wallNow: () => number;
  private readonly sleep: (milliseconds: number, signal: AbortSignal) => Promise<void>;
  private readonly unsubscribe: Unsubscribe;
  private generation = 0;
  private command: Command | null = null;
  private run: Run | null = null;
  private lastError: string | null = null;
  private nextWriteAt = -Infinity;
  private closing = false;
  private disposed = false;
  private disposing: Promise<void> | null = null;

  constructor(private readonly controller: LightController, options: DirectEffectRunnerOptions = {}) {
    this.now = options.now ?? (() => performance.now());
    this.wallNow = options.wallNow ?? Date.now;
    this.sleep = options.sleep ?? pause;
    this.unsubscribe = controller.subscribe(state => { if (!state.connected) this.cancel(); });
  }

  get status(): EffectStatus {
    const run = this.run;
    return {
      running: run?.running ?? false,
      id: run?.id ?? null,
      pattern: run?.effect.config.pattern ?? null,
      startedAt: run ? new Date(run.startedAt).toISOString() : null,
      endsAt: run && run.effect.duration !== null ? new Date(run.startedAt + run.effect.duration * 1000).toISOString() : null,
      progress: run && run.effect.duration !== null ? Math.min(1, this.elapsed(run) / run.effect.duration) : null,
      lastError: this.lastError,
    };
  }

  subscribe(listener: (status: EffectStatus) => void): Unsubscribe {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  }

  private publish(): void {
    for (const listener of this.listeners) {
      try { listener(this.status); } catch { /* observers cannot alter ownership or output */ }
    }
  }

  private elapsed(run: Run): number {
    return run.running ? Math.max(0, (this.now() - run.started) / 1000) : run.elapsed;
  }

  /** No awaited boundary: submitted native work stays owned, but no later setter can start. */
  cancel(): void {
    const previous = this.command;
    ++this.generation;
    this.command = null;
    if (this.run?.running) {
      this.run.elapsed = this.elapsed(this.run);
      this.run.running = false;
    }
    previous?.abort.abort();
    // A superseded command can still own I/O while its replacement waits in the queue.
    for (const call of [...this.calls]) call.abort();
    this.publish();
  }

  private begin(): Command {
    if (this.closing) throw new CancelledError();
    const previous = this.command;
    if (this.run?.running) {
      this.run.elapsed = this.elapsed(this.run);
      this.run.running = false;
    }
    const command = { generation: ++this.generation, abort: new AbortController() };
    this.command = command;
    // Wake the old producer, retaining its admitted transaction through full readback.
    // Aborting that transaction mid-chunk would conservatively close the BLE session.
    previous?.abort.abort();
    this.publish();
    return command;
  }

  private valid(command: Command): boolean {
    return !this.closing && command.generation === this.generation && !command.abort.signal.aborted;
  }
  private check(command: Command): void { if (!this.valid(command)) throw new CancelledError(); }

  private fail(command: Command, error: unknown): void {
    if (!this.valid(command)) return;
    if (this.run?.running) {
      this.run.elapsed = this.elapsed(this.run);
      this.run.running = false;
    }
    this.lastError = error instanceof Error ? error.message : String(error);
    command.abort.abort();
    this.publish();
  }

  /** The queue owns only short operations, never the lifetime of a sleeping run. */
  private enqueue<T>(command: Command, work: () => Promise<T>): Promise<T> {
    return this.queue.run(async () => {
      this.check(command);
      // A public controller call can reject before its native work has settled.
      await this.controller.drain();
      this.check(command);
      return work();
    });
  }

  private control<T>(command: Command, work: () => Promise<T>): Promise<T> {
    return this.enqueue(command, work).catch(error => {
      if (!this.valid(command)) throw new CancelledError();
      this.fail(command, error);
      throw error;
    });
  }

  private async waitForOutput(command: Command): Promise<void> {
    while (true) {
      this.check(command);
      const remaining = this.nextWriteAt - this.now();
      if (remaining <= 0) return;
      await this.sleep(remaining, command.abort.signal);
    }
  }

  private async call<T>(command: Command, work: (signal: AbortSignal) => Promise<T>): Promise<T> {
    this.check(command);
    const call = new AbortController();
    this.calls.add(call);
    try {
      const actual = await work(call.signal);
      this.check(command);
      return actual;
    } finally { this.calls.delete(call); }
  }

  /** Called with the runner queue held, after any waits and immediately before dispatch. */
  private async write(command: Command, patch: StatePatch): Promise<State> {
    this.check(command);
    this.nextWriteAt = this.now() + WRITE_SPACING_MS;
    return this.call(command, signal => this.controller.setState(patch, { signal }));
  }

  async start(config: EffectConfig): Promise<EffectStatus> {
    // Validate and copy BEFORE cancellation. SDK presets may be deeply frozen.
    parseEffectConfig(config);
    const requested: EffectConfig = config.pattern === 'keyframes'
      ? { ...config, keyframes: config.keyframes.map(frame => ({ ...frame })) }
      : { ...config };
    const command = this.begin();
    return this.control(command, async () => {
      const baseline = await this.call(command, signal => this.controller.refresh({ signal }));
      this.check(command);
      const effect = resolveEffect(requested, baseline);
      if (!baseline.connected || baseline.brightness === null || baseline.cct === null) throw new DisconnectedError();
      const run: Run = {
        command, effect, baseline: { brightness: baseline.brightness, cct: baseline.cct },
        id: `effect-${this.wallNow()}-${++nextId}`, started: this.now(), startedAt: this.wallNow(), running: true, elapsed: 0,
      };
      this.run = run;
      this.lastError = null;
      this.publish();
      this.check(command);
      const loop = this.runLoop(run);
      this.loops.add(loop);
      void loop.then(() => { this.loops.delete(loop); });
      return this.status;
    });
  }

  async stop(options: { restore?: boolean } = {}): Promise<EffectStatus> {
    if (options === null || typeof options !== 'object' || Array.isArray(options) ||
        Object.keys(options).some(key => key !== 'restore') ||
        (options.restore !== undefined && typeof options.restore !== 'boolean')) throw new TypeError('Stop accepts only a boolean restore');
    const restore = options.restore ?? true;
    const active = this.run?.running ? this.run : null;
    // An idle stop also supersedes a start that is still capturing its baseline.
    const command = this.begin();
    return this.control(command, async () => {
      if (active && restore) {
        await this.waitForOutput(command);
        // Always send BOTH absolute fields: an aborted write may have reached hardware.
        await this.write(command, active.baseline);
      }
      return this.status;
    });
  }

  async setState(patch: StatePatch): Promise<State> {
    validatePatch(patch);
    const requested = { ...patch };
    const command = this.begin();
    return this.control(command, async () => {
      await this.waitForOutput(command);
      return this.write(command, requested);
    });
  }

  private async step(run: Run): Promise<boolean> {
    return this.enqueue(run.command, async () => {
      await this.waitForOutput(run.command);
      this.check(run.command);
      // Sample AFTER both the queue/drain and rate waits; never replay a missed frame.
      const elapsed = this.elapsed(run);
      const target = run.effect.target(elapsed);
      const state = this.controller.state;
      if (!state.connected || state.brightness === null || state.cct === null) throw new DisconnectedError();
      const patch: StatePatch = {};
      if (Math.abs(state.brightness - target.brightness) > 0.01) patch.brightness = target.brightness;
      if (state.cct !== target.cct) patch.cct = target.cct;
      if (Object.keys(patch).length) await this.write(run.command, patch);
      this.check(run.command);
      const complete = run.effect.duration !== null && elapsed >= run.effect.duration;
      if (complete) {
        if (run.effect.restoreOnComplete) {
          await this.controller.drain();
          await this.waitForOutput(run.command);
          await this.write(run.command, run.baseline);
        }
        run.elapsed = run.effect.duration!;
        run.running = false;
      }
      this.publish();
      return complete;
    });
  }

  private async runLoop(run: Run): Promise<void> {
    try {
      while (this.valid(run.command) && run.running) {
        if (await this.step(run)) return;
        this.check(run.command);
        const elapsed = this.elapsed(run);
        if (run.effect.duration !== null && elapsed >= run.effect.duration) continue;
        const next = run.effect.nextChange(elapsed) ?? elapsed + IDLE_RESAMPLE_SECONDS;
        let wake = Math.min(elapsed + IDLE_RESAMPLE_SECONDS, Math.max(elapsed, next));
        if (run.effect.duration !== null) wake = Math.min(wake, run.effect.duration);
        await this.sleep(Math.max(1, run.started + wake * 1000 + 1 - this.now()), run.command.abort.signal);
      }
    } catch (error) { this.fail(run.command, error); }
  }

  /** Close admission synchronously. A failed drain retains ownership and can be retried. */
  dispose(): Promise<void> {
    this.closing = true;
    this.cancel();
    if (this.disposed) return Promise.resolve();
    if (this.disposing) return this.disposing;
    const pending = (async () => {
      // Do this bounded wait first, before waiting for runner jobs that may depend on it.
      await this.controller.drain();
      await this.queue.drain();
      await Promise.all([...this.loops]);
      await this.controller.drain();
      this.disposed = true;
      this.unsubscribe();
      this.listeners.clear();
    })();
    this.disposing = pending;
    void pending.catch(error => {
      if (this.disposing === pending) this.disposing = null;
      this.lastError = error instanceof Error ? error.message : String(error);
      this.publish();
    });
    return pending;
  }
}
