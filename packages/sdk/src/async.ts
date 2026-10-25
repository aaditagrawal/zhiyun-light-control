import { CancelledError, DisconnectedError, TimeoutError } from './errors';
import type { OperationOptions } from './types';

/** Deadline covers queueing and native promises; late native completion cannot report success. */
export function validateTimeout(ms: number): void {
  if (!Number.isFinite(ms) || ms <= 0 || ms > 300_000) throw new RangeError('timeoutMs must be 1–300000');
}
export function deadline(options: OperationOptions = {}, fallback = 5000, lifecycle?: AbortSignal) {
  const ms = options.timeoutMs ?? fallback;
  validateTimeout(ms);
  const controller = new AbortController();
  let reason: Error = new CancelledError();
  const stop = (error: Error) => { if (!controller.signal.aborted) { reason = error; controller.abort(); } };
  const abort = () => stop(new CancelledError());
  const disconnected = () => stop(new DisconnectedError());
  const timer = setTimeout(() => stop(new TimeoutError()), ms);
  options.signal?.addEventListener('abort', abort, { once: true });
  if (options.signal?.aborted) abort();
  lifecycle?.addEventListener('abort', disconnected, { once: true });
  if (lifecycle?.aborted) disconnected();
  return {
    signal: controller.signal,
    check() { if (controller.signal.aborted) throw reason; },
    async run<T>(work: Promise<T>): Promise<T> {
      if (controller.signal.aborted) { void work.catch(() => {}); throw reason; }
      return new Promise<T>((resolve, reject) => {
        const onAbort = () => { controller.signal.removeEventListener('abort', onAbort); reject(reason); };
        controller.signal.addEventListener('abort', onAbort, { once: true });
        work.then(resolve, reject).finally(() => controller.signal.removeEventListener('abort', onAbort));
      });
    },
    close() {
      clearTimeout(timer); options.signal?.removeEventListener('abort', abort);
      lifecycle?.removeEventListener('abort', disconnected); stop(new CancelledError());
    },
  };
}
export class SerialQueue {
  private tail: Promise<unknown> = Promise.resolve();
  run<T>(task: () => Promise<T>): Promise<T> {
    const next = this.tail.then(task);
    this.tail = next.catch(() => {});
    return next;
  }
  /** Include tasks submitted while an earlier task is still settling. */
  async drain(): Promise<void> {
    while (true) {
      const tail = this.tail;
      await tail;
      if (tail === this.tail) return;
    }
  }
}
/** Track actual work, never the deadline race returned to its caller. */
export class OwnedWork {
  private readonly pending = new Set<Promise<unknown>>();
  track<T>(work: Promise<T>): Promise<T> {
    this.pending.add(work);
    void work.then(() => this.pending.delete(work), () => this.pending.delete(work));
    return work;
  }
  async drain(): Promise<void> {
    while (this.pending.size) await Promise.allSettled([...this.pending]);
  }
}
/** A bounded wait does not release ownership of an unfinished cleanup promise. */
export async function drainWithin(work: Promise<void>, timeoutMs: number, subject: string): Promise<void> {
  const op = deadline({}, timeoutMs);
  try { await op.run(work); }
  catch (error) {
    if (error instanceof TimeoutError) throw new TimeoutError(`${subject} timed out; unresolved work remains owned. Retry drain() or disconnect() before replacing the session.`);
    throw error;
  } finally { op.close(); }
}
export function throwIfAborted(signal: AbortSignal): void {
  if (signal.aborted) throw new CancelledError();
}
export function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new CancelledError()); return; }
    const abort = () => { clearTimeout(timer); reject(new CancelledError()); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
    signal.addEventListener('abort', abort, { once: true });
  });
}
