import { deadline, drainWithin, pause, SerialQueue, throwIfAborted, validateTimeout } from './async';
import { DisconnectedError, ProtocolError, VerificationError } from './errors';
import { COMMAND, decodeValue, valuePayload } from './protocol';
import type { CommandTransport } from './transport';
import { disconnectedState, validatePatch } from './types';
import type { OperationOptions, State, StatePatch, Unsubscribe } from './types';
export interface ControllerOptions { timeoutMs?: number; verificationAttempts?: number; verificationDelayMs?: number }
/** Direct, single G60 controller. All reads and mutations are serialized. */
export class LightController {
  private readonly queue = new SerialQueue();
  private readonly listeners = new Set<(state: State) => void>();
  private readonly lifecycle = new AbortController();
  private current = disconnectedState();
  private restoreBrightness = 5;
  private readonly timeoutMs: number;
  private readonly attempts: number;
  private readonly delayMs: number;
  private readonly undrop: Unsubscribe;
  private disconnecting: Promise<void> | undefined;
  private needsDisconnect = false;
  constructor(private readonly transport: CommandTransport, options: ControllerOptions = {}) {
    this.timeoutMs = options.timeoutMs ?? 10_000;
    validateTimeout(this.timeoutMs);
    this.attempts = options.verificationAttempts ?? 3;
    this.delayMs = options.verificationDelayMs ?? 100;
    if (!Number.isInteger(this.attempts) || this.attempts < 1 || this.attempts > 10 || !Number.isFinite(this.delayMs) || this.delayMs < 0 || this.delayMs > 2000) throw new RangeError('Invalid verification policy');
    this.undrop = transport.onDisconnect(() => { this.lifecycle.abort(); this.publish(disconnectedState()); });
  }
  /** Snapshot copies keep consumers from mutating the controller cache. */
  get state(): State { return { ...this.current, device: this.current.device ? { ...this.current.device } : null }; }
  subscribe(listener: (state: State) => void): Unsubscribe {
    this.listeners.add(listener); return () => { this.listeners.delete(listener); };
  }
  private publish(state: State): State {
    this.current = state;
    for (const listener of this.listeners) { try { listener(this.state); } catch { /* observer failures do not alter hardware outcomes */ } }
    return this.state;
  }
  private operation<T>(options: OperationOptions, work: (signal: AbortSignal) => Promise<T>): Promise<T> {
    const op = deadline(options, this.timeoutMs, this.lifecycle.signal);
    const queued = this.queue.run(async () => {
      op.check();
      // A transport may race its own native promises. Do not start replacement work
      // until those promises have settled, even if the prior caller already failed.
      await this.transport.drain?.();
      op.check();
      return work(op.signal);
    });
    return op.run(queued).finally(() => op.close());
  }
  connect(options: OperationOptions = {}): Promise<State> {
    return this.operation(options, async signal => {
      try {
        await this.transport.connect({ signal, timeoutMs: this.timeoutMs });
        throwIfAborted(signal);
        const info = await this.transport.request(COMMAND.info, undefined, { signal });
        throwIfAborted(signal);
        const fields = asciiFields(info.payload);
        if (fields.length < 2 || fields[1]?.toLowerCase() !== 'pl103') throw new ProtocolError('Device is not a confirmed PL103 / MOLUS G60');
        const id = await this.transport.request(COMMAND.deviceId, undefined, { signal });
        throwIfAborted(signal);
        if (id.payload.length !== 2) throw new ProtocolError('Invalid device ID response');
        const deviceId = new DataView(id.payload.buffer, id.payload.byteOffset, 2).getUint16(0, true);
        this.current = { ...disconnectedState(), connected: true, transport: 'ble', address: this.transport.address,
          device: { model: fields[1]!, serial: fields[0]!, id: deviceId } };
        return await this.read(signal);
      } catch (error) {
        // Block queued mutations before releasing this slot, while preserving the
        // original setup error for the caller. Full lifecycle cleanup runs below.
        this.current = disconnectedState();
        throw error;
      }
    }).catch(error => {
      // Cleanup must run outside the queued task: disconnect drains that queue.
      void this.disconnect().catch(() => {});
      throw error;
    });
  }
  private invalidate(): void {
    this.lifecycle.abort();
    this.undrop();
    this.publish(disconnectedState());
  }
  private closeTransport(): Promise<void> {
    if (this.disconnecting) return this.disconnecting;
    const cleanup = (async () => {
      await this.queue.drain();
      await this.transport.drain?.();
      await this.transport.disconnect();
      this.needsDisconnect = false;
    })();
    this.disconnecting = cleanup;
    void cleanup.catch(() => { if (this.disconnecting === cleanup) this.disconnecting = undefined; });
    return cleanup;
  }
  /** Wait for actual submitted work, including native work hidden by timeout races. */
  drain(): Promise<void> {
    return drainWithin((async () => {
      await this.queue.drain();
      await this.transport.drain?.();
      if (this.needsDisconnect) await this.closeTransport();
      else await this.disconnecting;
    })(), this.timeoutMs, 'Controller drain');
  }
  disconnect(): Promise<State> {
    if (!this.disconnecting) this.needsDisconnect = true;
    this.invalidate();
    return drainWithin(this.closeTransport(), this.timeoutMs, 'Controller disconnect').then(() => this.state);
  }
  refresh(options: OperationOptions = {}): Promise<State> { return this.operation(options, signal => this.read(signal)); }
  setState(patch: StatePatch, options: OperationOptions = {}): Promise<State> {
    validatePatch(patch);
    // Snapshot caller input so queued work cannot be changed by mutation of the original object.
    const target = { ...patch };
    return this.operation(options, async signal => {
      const id = this.requireDevice();
      const brightness = target.brightness ?? (target.power === false ? 0 : target.power === true ? this.restoreBrightness : undefined);
      if (brightness !== undefined) await this.transport.send(COMMAND.brightness, valuePayload(COMMAND.brightness, id, brightness), { signal });
      throwIfAborted(signal);
      if (target.cct !== undefined) await this.transport.send(COMMAND.cct, valuePayload(COMMAND.cct, id, target.cct), { signal });
      throwIfAborted(signal);
      for (let attempt = 0; attempt < this.attempts; attempt++) {
        throwIfAborted(signal);
        if (attempt > 0) await pause(this.delayMs, signal);
        const actual = await this.read(signal);
        throwIfAborted(signal);
        // float32 round trips tolerate representation noise only, not a visibly different percent.
        if ((brightness === undefined || Math.abs(actual.brightness! - brightness) <= 0.01) &&
            (target.cct === undefined || actual.cct === target.cct)) return actual;
      }
      throw new VerificationError(`Readback did not match requested settings (actual ${this.current.brightness}%, ${this.current.cct} K)`);
    });
  }
  private requireDevice(): number {
    if (!this.transport.connected || !this.current.connected || !this.current.device) throw new DisconnectedError();
    return this.current.device.id;
  }
  private async read(signal: AbortSignal): Promise<State> {
    throwIfAborted(signal);
    const id = this.requireDevice();
    const dim = await this.transport.request(COMMAND.brightness, valuePayload(COMMAND.brightness, id), { signal });
    throwIfAborted(signal);
    const cct = await this.transport.request(COMMAND.cct, valuePayload(COMMAND.cct, id), { signal });
    throwIfAborted(signal);
    this.requireDevice();
    const brightness = decodeValue(dim, id);
    const kelvin = decodeValue(cct, id);
    if (brightness > 0) this.restoreBrightness = brightness;
    return this.publish({ ...this.current, brightness, cct: kelvin, power: brightness > 0, updatedAt: new Date().toISOString() });
  }
}
function asciiFields(payload: Uint8Array): string[] {
  const fields: string[] = []; let current = '';
  for (const byte of payload) {
    if (byte === 0) { if (current) fields.push(current); current = ''; }
    else { if (byte < 32 || byte > 126) throw new ProtocolError('Non-ASCII device information'); current += String.fromCharCode(byte); }
  }
  if (current) fields.push(current);
  return fields;
}
