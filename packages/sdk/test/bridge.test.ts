import { describe, expect, test } from 'bun:test';
import { BridgeClient, BridgeError, CancelledError, ProtocolError, TimeoutError } from '../src';
import { state } from './fixtures';
const fakeFetch = (handler: (url: string, init: RequestInit) => Response | Promise<Response>): typeof fetch =>
  ((input: string | URL | Request, init?: RequestInit) => Promise.resolve(handler(String(input), init ?? {}))) as typeof fetch;
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
describe('authenticated bridge', () => {
  test('optional/empty tokens omit Authorization for no-auth JSON and SSE; health validates discovery metadata', async () => {
    const calls: RequestInit[] = [];
    for (const token of [undefined, '', '   ']) {
      const client = new BridgeClient({ ...(token === undefined ? {} : { token }), fetch: fakeFetch((url, init) => {
        calls.push(init);
        if (url.endsWith('/v1/health')) return json({ name: 'lighttime', version: 'test', authRequired: false, defaultAddress: 'FAKE-G60' });
        if (url.endsWith('/v1/events')) return new Response(`event: state\ndata: ${JSON.stringify(state)}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } });
        return json(state);
      }) });
      expect(await client.health()).toEqual({ name: 'lighttime', version: 'test', authRequired: false, defaultAddress: 'FAKE-G60' });
      expect(await client.refresh()).toEqual(state);
      const events = client.events(); expect((await events.next()).value).toEqual(state); await events.return(); client.dispose();
    }
    for (const init of calls) expect(new Headers(init.headers).has('Authorization')).toBe(false);
    for (const invalid of [{ authRequired: 'false' }, { defaultAddress: 12 }, { defaultAddress: '' }]) {
      const client = new BridgeClient({ fetch: fakeFetch(() => json({ name: 'lighttime', version: 'test', ...invalid })) });
      await expect(client.health()).rejects.toBeInstanceOf(ProtocolError); client.dispose();
    }
    expect(() => new BridgeClient({ token: 'bad\r\ntoken' })).toThrow(TypeError);
  });
  test('default native fetch keeps its required global receiver; injected fetch is a plain call', async () => {
    const original = globalThis.fetch;
    let calls = 0;
    try {
      globalThis.fetch = function (this: unknown) {
        if (this !== globalThis) throw new TypeError("Failed to execute 'fetch' on 'Window': Illegal invocation");
        calls++; return Promise.resolve(json(state));
      } as unknown as typeof fetch;
      const client = new BridgeClient({ token: 't' });
      expect(await client.refresh()).toEqual(state); client.dispose();
      const injected = function (this: unknown) {
        expect(this).toBeUndefined(); calls++; return Promise.resolve(json(state));
      };
      const custom = new BridgeClient({ token: 't', fetch: injected });
      expect(await custom.refresh()).toEqual(state); custom.dispose();
      expect(calls).toBe(2);
    } finally { globalThis.fetch = original; }
  });
  test('auth stays in headers for JSON, scans, and SSE; camelCase State is preserved', async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    const client = new BridgeClient({ token: 'test-only-token', fetch: fakeFetch((url, init) => {
      calls.push({ url, init });
      if (url.endsWith('/v1/events')) return new Response(`event: state\ndata: ${JSON.stringify(state)}\n\n`, { headers: { 'Content-Type': 'text/event-stream' } });
      if (url.includes('/v1/devices?')) return json({ devices: [{ transport: 'ble', name: 'PL103_TEST', address: 'test-light' }] });
      return json(state);
    }) });
    expect(await client.refresh()).toEqual(state);
    expect(await client.setState({ brightness: 20 })).toEqual(state);
    expect(await client.devices(2)).toHaveLength(1);
    const events = client.events(); expect((await events.next()).value).toEqual(state); await events.return();
    for (const call of calls) {
      expect(new Headers(call.init.headers).get('Authorization')).toBe('Bearer test-only-token');
      expect(call.url).not.toContain('test-only-token'); expect(call.init.credentials).toBe('omit'); expect(call.init.redirect).toBe('error');
    }
    expect(calls[1]!.init.method).toBe('PATCH'); expect(JSON.parse(calls[1]!.init.body as string)).toEqual({ brightness: 20 });
    client.dispose();
  });
  test('surfaces authenticated HTTP errors and safe non-JSON fallback', async () => {
    const client = new BridgeClient({ token: 'test', fetch: fakeFetch(() => json({ error: { code: 'unauthorized', message: 'Token required' } }, 401)) });
    try { await client.refresh(); throw new Error('expected failure'); }
    catch (error) { expect(error).toBeInstanceOf(BridgeError); expect((error as BridgeError).status).toBe(401); expect((error as BridgeError).code).toBe('unauthorized'); }
    const proxy = new BridgeClient({ token: 'test', fetch: fakeFetch(() => new Response('upstream HTML', { status: 502 })) });
    await expect(proxy.refresh()).rejects.toMatchObject({ code: 'http_502', status: 502 });
  });
  test('rejects malformed JSON and State shapes', async () => {
    await expect(new BridgeClient({ token: 't', fetch: fakeFetch(() => new Response('no json')) }).refresh()).rejects.toBeInstanceOf(ProtocolError);
    await expect(new BridgeClient({ token: 't', fetch: fakeFetch(() => json({ connected: true, updated_at: 'wrong' })) }).refresh()).rejects.toBeInstanceOf(ProtocolError);
    expect(() => new BridgeClient({ token: 't', baseUrl: 'http://user:pass@localhost/' })).toThrow();
    expect(() => new BridgeClient({ token: 't', baseUrl: 'http://localhost/?token=bad' })).toThrow();
  });
  test('G60 resolution and scan bounds reject before contacting the bridge', async () => {
    let calls = 0;
    const client = new BridgeClient({ token: 't', fetch: fakeFetch(() => { calls++; return json(state); }) });
    for (const patch of [{ brightness: 5.5 }, { brightness: 6.7 }, { cct: 4501 }, { cct: 4550 }, { cct: 4599 }]) {
      await expect(client.setState(patch)).rejects.toBeInstanceOf(RangeError);
    }
    await expect(client.devices(0.09)).rejects.toBeInstanceOf(RangeError);
    await expect(client.devices(30.1)).rejects.toBeInstanceOf(RangeError);
    expect(calls).toBe(0);
  });
  test('bridge mutations serialize and a queued timeout never dispatches a write', async () => {
    const brightness: number[] = []; let finish!: () => void;
    const gate = new Promise<void>(resolve => { finish = resolve; });
    const client = new BridgeClient({ token: 't', fetch: fakeFetch(async (_url, init) => {
      const value = JSON.parse(init.body as string).brightness as number;
      brightness.push(value); if (value === 10) await gate;
      return json({ ...state, brightness: value });
    }) });
    const first = client.setState({ brightness: 10 });
    const queued = client.setState({ brightness: 20 }, { timeoutMs: 5 });
    await expect(queued).rejects.toBeInstanceOf(TimeoutError);
    expect(brightness).toEqual([10]); finish(); expect((await first).brightness).toBe(10);
    expect((await client.setState({ brightness: 30 })).brightness).toBe(30);
    expect(brightness).toEqual([10, 30]); client.dispose();
  });
  test('bounds hung fetches and body parsing; explicit abort and dispose cancel requests', async () => {
    const hung = fakeFetch(() => new Promise(() => {}));
    const client = new BridgeClient({ token: 'test', fetch: hung });
    await expect(client.refresh({ timeoutMs: 5 })).rejects.toBeInstanceOf(TimeoutError);
    const cancel = new AbortController(); const request = client.refresh({ signal: cancel.signal }); cancel.abort();
    await expect(request).rejects.toBeInstanceOf(CancelledError);
    const pending = client.refresh(); client.dispose(); await expect(pending).rejects.toBeInstanceOf(CancelledError);
    await expect(client.refresh()).rejects.toBeInstanceOf(CancelledError);
    const body = new BridgeClient({ token: 't', fetch: fakeFetch(() => new Response(new ReadableStream({ start() {} }))) });
    await expect(body.refresh({ timeoutMs: 5 })).rejects.toBeInstanceOf(TimeoutError);
  });
  test('SSE handles UTF-8 fragments, CRLF split boundaries, comments, and multiline data', async () => {
    const withUnicode = { ...state, device: { ...state.device!, serial: '灯-test' } };
    const data = `: heartbeat\r\nevent: ignore\r\ndata: {}\r\n\r\nevent: state\r\ndata: ${JSON.stringify(withUnicode)}\r\n\r\nevent: state\ndata: {\ndata: ${JSON.stringify(state).slice(1)}\n\n`;
    const bytes = new TextEncoder().encode(data);
    const client = new BridgeClient({ token: 'test', fetch: fakeFetch(() => new Response(new ReadableStream({
      start(controller) { for (const byte of bytes) controller.enqueue(Uint8Array.of(byte)); controller.close(); },
    }), { headers: { 'Content-Type': 'text/event-stream' } })) });
    const events = client.events();
    expect((await events.next()).value).toEqual(withUnicode); expect((await events.next()).value).toEqual(state);
    await expect(events.next()).rejects.toMatchObject({ code: 'stream_closed' });
  });
  test('SSE auth errors, idle timeout, cancellation, malformed/oversized events are visible', async () => {
    const denied = new BridgeClient({ token: 't', fetch: fakeFetch(() => json({ error: { code: 'unauthorized', message: 'No access' } }, 403)) });
    await expect(denied.events().next()).rejects.toMatchObject({ status: 403 });
    const idle = new BridgeClient({ token: 't', fetch: fakeFetch(() => new Response(new ReadableStream({ start() {} }), { headers: { 'Content-Type': 'text/event-stream' } })) });
    await expect(idle.events({ idleTimeoutMs: 5 }).next()).rejects.toBeInstanceOf(TimeoutError);
    const pending = idle.events().next(); idle.dispose(); await expect(pending).rejects.toBeInstanceOf(CancelledError);
    for (const data of ['event: state\ndata: nope\n\n', `event: state\ndata: ${'x'.repeat(65537)}`]) {
      const invalid = new BridgeClient({ token: 't', fetch: fakeFetch(() => new Response(data, { headers: { 'Content-Type': 'text/event-stream' } })) });
      await expect(invalid.events().next()).rejects.toBeInstanceOf(ProtocolError);
    }
  });
});
