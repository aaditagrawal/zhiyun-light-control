import { deadline, SerialQueue, validateTimeout } from './async';
import { BridgeError, CancelledError, LighttimeError, ProtocolError, TimeoutError } from './errors';
import { parseEffectStatus, validateEffectConfig } from './effects';
import type { Device, EffectConfig, EffectStatus, OperationOptions, State, StatePatch, Unsubscribe } from './types';
import { validatePatch } from './types';
export type FetchFunction = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
export interface BridgeOptions { baseUrl?: string; token?: string; timeoutMs?: number; fetch?: FetchFunction }
export interface BridgeHealth { name: string; version: string; authRequired?: boolean; defaultAddress?: string | null }
export interface ConnectOptions { transport: 'ble' | 'serial' | 'mock'; address: string; deviceId?: number }
export interface EventOptions { signal?: AbortSignal; idleTimeoutMs?: number }
/** fetch streaming supports Authorization on SSE; credentials never enter a URL. */
export class BridgeClient {
  private readonly baseUrl: string;
  private readonly token: string;
  private readonly timeoutMs: number;
  private readonly fetcher: FetchFunction;
  private readonly active = new Set<AbortController>();
  private readonly mutations = new SerialQueue();
  private disposed = false;
  constructor(options: BridgeOptions) {
    const url = new URL(options.baseUrl ?? 'http://127.0.0.1:8877');
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new TypeError('Bridge URL must be HTTP(S), without credentials, query, or fragment');
    const token = options.token ?? '';
    if (typeof token !== 'string' || /[\r\n]/.test(token)) throw new TypeError('Bridge token must be a string without line breaks');
    this.baseUrl = url.href.replace(/\/$/, ''); this.token = token.trim() ? token : '';
    this.timeoutMs = options.timeoutMs ?? 15_000;
    validateTimeout(this.timeoutMs);
    const injected = options.fetch;
    const native = globalThis.fetch;
    if (injected) this.fetcher = (input, init) => injected(input, init);
    else if (typeof native === 'function') this.fetcher = native.bind(globalThis);
    else throw new LighttimeError('unsupported', 'A fetch implementation is required');
  }
  /** Cancels requests/streams; shared bridge hardware and server patterns keep running. */
  dispose(): void { this.disposed = true; for (const controller of this.active) controller.abort(); this.active.clear(); }
  async health(options: OperationOptions = {}): Promise<BridgeHealth> {
    const value = await this.json('/v1/health', 'GET', undefined, options);
    if (!record(value) || typeof value.name !== 'string' || typeof value.version !== 'string' ||
        (value.authRequired !== undefined && typeof value.authRequired !== 'boolean') ||
        (value.defaultAddress !== undefined && !(value.defaultAddress === null || typeof value.defaultAddress === 'string' && value.defaultAddress.trim()))) throw new ProtocolError('Invalid health response');
    return { name: value.name, version: value.version,
      ...(value.authRequired === undefined ? {} : { authRequired: value.authRequired }),
      ...(value.defaultAddress === undefined ? {} : { defaultAddress: value.defaultAddress as string | null }) };
  }
  async devices(timeout = 5, options: OperationOptions = {}): Promise<Device[]> {
    if (!Number.isFinite(timeout) || timeout < 0.1 || timeout > 30) throw new RangeError('Scan timeout must be 0.1–30 seconds');
    const value = await this.json(`/v1/devices?timeout=${encodeURIComponent(timeout)}`, 'GET', undefined,
      { ...options, timeoutMs: options.timeoutMs ?? Math.max(this.timeoutMs, timeout * 1000 + 5000) });
    if (!record(value) || !Array.isArray(value.devices) || !value.devices.every(validDevice)) throw new ProtocolError('Invalid devices response');
    return value.devices;
  }
  async connect(connection: ConnectOptions, options: OperationOptions = {}): Promise<State> {
    return this.mutation('/v1/connect', 'POST', { ...connection }, { ...options, timeoutMs: options.timeoutMs ?? 60_000 }, parseState);
  }
  async disconnect(options: OperationOptions = {}): Promise<State> { return this.mutation('/v1/disconnect', 'POST', {}, options, parseState); }
  async refresh(options: OperationOptions = {}): Promise<State> { return parseState(await this.json('/v1/state', 'GET', undefined, options)); }
  async setState(patch: StatePatch, options: OperationOptions = {}): Promise<State> {
    validatePatch(patch); return this.mutation('/v1/state', 'PATCH', { ...patch }, options, parseState);
  }
  async effect(options: OperationOptions = {}): Promise<EffectStatus> {
    return parseEffectStatus(await this.json('/v1/effects', 'GET', undefined, { ...options, timeoutMs: options.timeoutMs ?? 60_000 }));
  }
  async startEffect(config: EffectConfig, options: OperationOptions = {}): Promise<EffectStatus> {
    validateEffectConfig(config);
    return this.mutation('/v1/effects/start', 'POST', { ...config }, { ...options, timeoutMs: options.timeoutMs ?? 60_000 }, parseEffectStatus);
  }
  async stopEffect(stop: { restore?: boolean } = {}, options: OperationOptions = {}): Promise<EffectStatus> {
    if (typeof stop !== 'object' || stop === null || Array.isArray(stop) || Object.keys(stop).some(key => key !== 'restore') ||
        (stop.restore !== undefined && typeof stop.restore !== 'boolean')) throw new TypeError('Effect stop accepts only a boolean restore option');
    return this.mutation('/v1/effects/stop', 'POST', { ...stop }, { ...options, timeoutMs: options.timeoutMs ?? 60_000 }, parseEffectStatus);
  }
  private async mutation<T>(path: string, method: string, body: unknown, options: OperationOptions,
    parse: (value: unknown) => T): Promise<T> {
    const timeoutMs = options.timeoutMs ?? this.timeoutMs;
    validateTimeout(timeoutMs);
    const scope = this.scope(options.signal);
    const op = deadline({ signal: scope.controller.signal, timeoutMs });
    try {
      const queued = this.mutations.run(async () => {
        op.check();
        return parse(await this.json(path, method, body, { signal: op.signal, timeoutMs }));
      });
      return await op.run(queued);
    } finally { op.close(); scope.close(); }
  }
  private scope(signal?: AbortSignal): { controller: AbortController; close: Unsubscribe } {
    if (this.disposed) throw new CancelledError();
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort(); this.active.add(controller);
    return { controller, close: () => { signal?.removeEventListener('abort', abort); this.active.delete(controller); } };
  }
  private async json(path: string, method: string, body: unknown, options: OperationOptions): Promise<unknown> {
    validateTimeout(options.timeoutMs ?? this.timeoutMs);
    const scope = this.scope(options.signal);
    const op = deadline({ signal: scope.controller.signal, timeoutMs: options.timeoutMs ?? this.timeoutMs });
    try {
      op.check();
      const response = await op.run(this.fetcher(this.baseUrl + path, {
        method, headers: { ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}), Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: op.signal, credentials: 'omit', cache: 'no-store', redirect: 'error',
      }));
      if (!response.ok) throw await op.run(httpError(response));
      try { return await op.run(response.json()); }
      catch (error) {
        if (error instanceof SyntaxError) throw new ProtocolError('Bridge returned invalid JSON');
        throw error;
      }
    } finally { op.close(); scope.close(); }
  }
  /** No silent reconnect: EOF/network/idle errors reach the caller so it can show stale status. */
  async *events(options: EventOptions = {}): AsyncGenerator<State, void, void> {
    const scope = this.scope(options.signal);
    const idleMs = options.idleTimeoutMs ?? 45_000;
    const validIdle = Number.isFinite(idleMs) && idleMs > 0 && idleMs <= 300_000;
    if (!validIdle) { scope.close(); throw new RangeError('idleTimeoutMs must be 1–300000'); }
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    try {
      const response = await this.streamStep(scope.controller, idleMs, signal => this.fetcher(this.baseUrl + '/v1/events', {
        headers: { ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}), Accept: 'text/event-stream' },
        signal, credentials: 'omit', cache: 'no-store', redirect: 'error',
      }));
      if (!response.ok) throw await this.streamStep(scope.controller, idleMs, () => httpError(response));
      if (!response.headers.get('content-type')?.toLowerCase().includes('text/event-stream') || !response.body) throw new ProtocolError('Bridge did not return an SSE stream');
      reader = response.body.getReader();
      const decoder = new TextDecoder(); const parser = new SseParser();
      while (true) {
        const chunk = await this.streamStep(scope.controller, idleMs, () => reader!.read());
        if (chunk.done) throw new LighttimeError('stream_closed', 'Bridge event stream closed');
        for (const data of parser.push(decoder.decode(chunk.value, { stream: true }))) {
          if (scope.controller.signal.aborted) throw new CancelledError();
          let value: unknown;
          try { value = JSON.parse(data); } catch { throw new ProtocolError('Invalid state event JSON'); }
          yield parseState(value);
        }
      }
    } finally {
      scope.controller.abort();
      if (reader) { void reader.cancel().catch(() => {}); reader.releaseLock(); }
      scope.close();
    }
  }
  private async streamStep<T>(controller: AbortController, ms: number, work: (signal: AbortSignal) => Promise<T>): Promise<T> {
    const op = deadline({ signal: controller.signal, timeoutMs: ms });
    try { op.check(); return await op.run(work(controller.signal)); }
    catch (error) { if (error instanceof TimeoutError) controller.abort(); throw error; }
    finally { op.close(); }
  }
}
function record(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null; }
function validDevice(value: unknown): value is Device {
  return record(value) && ['ble', 'serial'].includes(String(value.transport)) && typeof value.address === 'string' && typeof value.name === 'string' &&
    (value.rssi === undefined || typeof value.rssi === 'number') && (value.accessible === undefined || typeof value.accessible === 'boolean');
}
export function parseState(value: unknown): State {
  if (!record(value) || typeof value.connected !== 'boolean' || !['ble', 'serial', 'mock', 'usb+ble', null].includes(value.transport as State['transport']) ||
      !(value.address === null || typeof value.address === 'string') || !(value.updatedAt === null || typeof value.updatedAt === 'string') ||
      !(value.power === null || typeof value.power === 'boolean') ||
      !(value.brightness === null || typeof value.brightness === 'number' && Number.isFinite(value.brightness) && value.brightness >= 0 && value.brightness <= 100) ||
      !(value.cct === null || typeof value.cct === 'number' && Number.isInteger(value.cct) && value.cct >= 2700 && value.cct <= 6500) ||
      !(value.device === null || record(value.device) && typeof value.device.model === 'string' && typeof value.device.serial === 'string' &&
        typeof value.device.id === 'number' && Number.isInteger(value.device.id) && value.device.id >= 0 && value.device.id <= 65535)) throw new ProtocolError('Invalid bridge State');
  return value as unknown as State;
}
async function httpError(response: Response): Promise<BridgeError> {
  let code = `http_${response.status}`; let message = `Bridge request failed (HTTP ${response.status})`;
  try {
    const body: unknown = await response.json();
    if (record(body) && record(body.error)) {
      if (typeof body.error.code === 'string') code = body.error.code;
      if (typeof body.error.message === 'string') message = body.error.message;
    }
  } catch { /* retain safe HTTP fallback, never include token or request */ }
  return new BridgeError(code, message, response.status);
}
/** Incremental SSE lines including split CRLF; comments and unknown event names are ignored. */
class SseParser {
  private buffer = ''; private event = ''; private data: string[] = []; private bytes = 0;
  push(chunk: string): string[] {
    this.buffer += chunk;
    const output: string[] = [];
    while (true) {
      const at = this.buffer.search(/[\r\n]/);
      if (at < 0) break;
      if (this.buffer[at] === '\r' && at === this.buffer.length - 1) break;
      const line = this.buffer.slice(0, at);
      const consumed = this.buffer.slice(at, at + 2) === '\r\n' ? 2 : 1;
      this.buffer = this.buffer.slice(at + consumed);
      this.bytes += line.length;
      if (this.bytes > 65536) throw new ProtocolError('SSE event exceeds maximum size');
      if (line === '') {
        if (this.event === 'state' && this.data.length) output.push(this.data.join('\n'));
        this.event = ''; this.data = []; this.bytes = 0;
      } else if (!line.startsWith(':')) {
        const colon = line.indexOf(':'); const key = colon < 0 ? line : line.slice(0, colon);
        let value = colon < 0 ? '' : line.slice(colon + 1); if (value.startsWith(' ')) value = value.slice(1);
        if (key === 'event') this.event = value; else if (key === 'data') this.data.push(value);
      }
    }
    if (this.buffer.length + this.bytes > 65536) throw new ProtocolError('SSE event exceeds maximum size');
    return output;
  }
}
