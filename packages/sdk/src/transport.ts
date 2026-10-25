import { deadline, drainWithin, OwnedWork, SerialQueue, validateTimeout } from './async';
import { CancelledError, DisconnectedError, ProtocolError } from './errors';
import { encodeFrame, FrameParser, REQUEST_TYPE, RESPONSE_TYPE } from './protocol';
import type { Frame } from './protocol';
import type { OperationOptions, Unsubscribe } from './types';

/** Native promises must represent actual settlement. Await async subscription cleanup in disconnect. */
export interface BleAdapter {
  readonly address: string;
  connect(): Promise<void>;
  write(bytes: Uint8Array): Promise<void>;
  subscribe(listener: (bytes: Uint8Array) => void): Promise<Unsubscribe>;
  onDisconnect(listener: () => void): Unsubscribe;
  disconnect(): Promise<void>;
}
export interface CommandTransport {
  readonly connected: boolean;
  readonly address: string;
  connect(options?: OperationOptions): Promise<void>;
  /** Await actual work hidden by caller cancellation/deadline races, when supported. */
  drain?(): Promise<void>;
  disconnect(): Promise<void>;
  send(command: number, payload: Uint8Array, options?: OperationOptions): Promise<void>;
  request(command: number, payload?: Uint8Array, options?: OperationOptions): Promise<Frame>;
  onDisconnect(listener: () => void): Unsubscribe;
}
interface Pending { command: number; resolve: (frame: Frame) => void; reject: (error: Error) => void }
export interface BleTransportOptions { timeoutMs?: number; maxBodyLength?: number; maxWriteSize?: number }

/** One connection per instance. Construct a new transport after disconnect or failed setup. */
export class BleProtocolTransport implements CommandTransport {
  private status: 'new' | 'connecting' | 'connected' | 'closed' = 'new';
  private readonly parser: FrameParser;
  private readonly writes = new SerialQueue();
  private readonly work = new OwnedWork();
  private readonly pending = new Map<number, Pending>();
  private readonly listeners = new Set<() => void>();
  private readonly lifecycle = new AbortController();
  private unnotify: Unsubscribe | undefined;
  private undrop: Unsubscribe | undefined;
  private sequence = 0;
  private readonly timeoutMs: number;
  private readonly maxWriteSize: number;
  private closing: Promise<void> | undefined;
  private needsClose = false;
  constructor(private readonly adapter: BleAdapter, options: BleTransportOptions = {}) {
    this.parser = new FrameParser(options.maxBodyLength);
    this.timeoutMs = options.timeoutMs ?? 5000;
    validateTimeout(this.timeoutMs);
    this.maxWriteSize = options.maxWriteSize ?? 20;
    if (!Number.isInteger(this.maxWriteSize) || this.maxWriteSize < 1 || this.maxWriteSize > 512) throw new RangeError('Invalid BLE write size');
  }
  get connected(): boolean { return this.status === 'connected'; }
  get address(): string { return this.adapter.address; }
  onDisconnect(listener: () => void): Unsubscribe { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; }
  async connect(options: OperationOptions = {}): Promise<void> {
    if (this.status !== 'new') throw new DisconnectedError('Use a new transport for each connection');
    const op = deadline(options, this.timeoutMs, this.lifecycle.signal);
    this.status = 'connecting';
    try {
      op.check();
      this.undrop = this.adapter.onDisconnect(() => this.drop());
      const setup = this.work.track((async () => {
        await this.adapter.connect();
        op.check();
        if (this.status !== 'connecting') throw new DisconnectedError();
        const unsubscribe = await this.adapter.subscribe(bytes => this.receive(bytes));
        this.unnotify = unsubscribe;
        if (this.status !== 'connecting') { this.releaseSubscriptions(); throw new DisconnectedError(); }
        op.check();
      })());
      await op.run(setup);
      op.check();
      if (this.status !== 'connecting') throw new DisconnectedError();
      this.status = 'connected';
    } catch (error) { this.drop(); void this.closeAdapter().catch(() => {}); throw error; }
    finally { op.close(); }
  }
  disconnect(): Promise<void> {
    this.drop();
    return drainWithin(this.closeAdapter(), this.timeoutMs, 'BLE disconnect');
  }
  /** Keep unresolved native work owned even when this bounded wait rejects. */
  drain(): Promise<void> {
    return drainWithin((async () => {
      await this.work.drain();
      if (this.needsClose) await this.closeAdapter();
      else await this.closing;
    })(), this.timeoutMs, 'BLE drain');
  }
  private closeAdapter(): Promise<void> {
    if (this.closing) return this.closing;
    // Do not track this coordinator in `work`: it waits for that set itself.
    const closing = (async () => {
      await this.work.drain();
      this.releaseSubscriptions();
      await this.adapter.disconnect();
      this.needsClose = false;
    })();
    this.closing = closing;
    void closing.catch(() => { if (this.closing === closing) this.closing = undefined; });
    return closing;
  }
  private drop(): void {
    if (this.status === 'closed') return;
    this.status = 'closed'; this.needsClose = true; this.lifecycle.abort(); this.parser.reset();
    try { this.releaseSubscriptions(); } catch { /* retain failed cleanup for drain/disconnect to retry */ }
    for (const entry of this.pending.values()) entry.reject(new DisconnectedError());
    this.pending.clear();
    for (const listener of this.listeners) { try { listener(); } catch { /* isolate observers */ } }
    this.listeners.clear();
  }
  private releaseSubscriptions(): void {
    let failure: unknown;
    try { this.unnotify?.(); this.unnotify = undefined; } catch (error) { failure = error; }
    try { this.undrop?.(); this.undrop = undefined; } catch (error) { failure ??= error; }
    if (failure !== undefined) throw failure;
  }
  private receive(bytes: Uint8Array): void {
    if (this.status !== 'connected') return;
    for (const frame of this.parser.push(bytes)) {
      const entry = this.pending.get(frame.sequence);
      if (frame.messageType === RESPONSE_TYPE && entry?.command === frame.command) {
        this.pending.delete(frame.sequence); entry.resolve(frame);
      }
    }
  }
  private nextSequence(): number {
    if (!this.connected) throw new DisconnectedError();
    // Never reuse a sequence on a connection: delayed replies cannot match a later request.
    if (this.sequence === 65535) throw new ProtocolError('Sequence space exhausted; reconnect with a new transport');
    return ++this.sequence;
  }
  async send(command: number, payload: Uint8Array, options: OperationOptions = {}): Promise<void> {
    await this.exchange(command, payload, false, options);
  }
  async request(command: number, payload = new Uint8Array(), options: OperationOptions = {}): Promise<Frame> {
    return await this.exchange(command, payload, true, options) as Frame;
  }
  private async exchange(command: number, payload: Uint8Array, reply: boolean, options: OperationOptions): Promise<Frame | void> {
    const op = deadline(options, this.timeoutMs, this.lifecycle.signal);
    let sequence: number | undefined;
    let nativeWritePending = false;
    try {
      op.check(); sequence = this.nextSequence();
      if (this.pending.size >= 64) throw new ProtocolError('Too many pending requests');
      const frame = encodeFrame({ messageType: REQUEST_TYPE, command, sequence, payload });
      if (frame.length > this.parser.maxBodyLength + 6) throw new ProtocolError('Frame exceeds transport maximum');
      let response: Promise<Frame> | undefined;
      if (reply) {
        response = new Promise((resolve, reject) => this.pending.set(sequence!, { command, resolve, reject }));
        // Disconnect may reject while the GATT write itself is still pending.
        void response.catch(() => {});
      }
      const writing = this.work.track(this.writes.run(async () => {
        op.check(); if (!this.connected) throw new DisconnectedError();
        for (let offset = 0; offset < frame.length; offset += this.maxWriteSize) {
          op.check(); if (!this.connected) throw new DisconnectedError();
          try {
            nativeWritePending = true;
            // Retain the queue slot until the native promise actually settles.
            await this.adapter.write(frame.slice(offset, offset + this.maxWriteSize));
            nativeWritePending = false;
            op.check();
            if (!this.connected) throw new DisconnectedError();
          }
          catch (error) {
            nativeWritePending = false;
            this.drop(); void this.closeAdapter().catch(() => {}); throw error;
          }
        }
      }));
      if (response) {
        const [, result] = await op.run(this.work.track(Promise.all([writing, response])));
        op.check();
        if (!this.connected) throw new DisconnectedError();
        return result;
      }
      await op.run(writing);
      op.check();
      if (!this.connected) throw new DisconnectedError();
    } catch (error) {
      if (nativeWritePending) {
        // Invalidate immediately, but never close native GATT underneath the write.
        this.drop(); void this.closeAdapter().catch(() => {});
      }
      throw error;
    } finally {
      if (sequence !== undefined) {
        this.pending.get(sequence)?.reject(new CancelledError());
        this.pending.delete(sequence);
      }
      op.close();
    }
  }
}
