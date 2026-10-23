import { COMMAND, FrameParser, encodeFrame, RESPONSE_TYPE } from '../../../packages/sdk/src/protocol.ts';

// Run the real browser bundle in a fresh same-origin frame per case. Every fetch
// is replaced before evaluating the app, and navigator.bluetooth is a GATT fake.
// Unexpected endpoints throw; no code here calls native fetch or real Bluetooth.
globalThis.lighttimeBrowserRegression = async ({ html, bundle }) => {
  const results = [];
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const deferred = () => {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
  };
  const snapshot = (brightness = 5, second = 0, extra = {}) => ({
    connected: true, transport: 'ble', address: 'FAKE-G60',
    device: { model: 'pl103', serial: 'OFFLINE-TEST', id: 0 },
    brightness, cct: 5000, power: brightness > 0,
    updatedAt: `2026-10-23T12:00:${String(second).padStart(2, '0')}.000Z`, ...extra,
  });
  const disconnected = () => ({ connected: false, transport: null, address: null,
    device: null, brightness: null, cct: null, power: null, updatedAt: null });
  const idleEffect = () => ({ running: false, id: null, pattern: null, startedAt: null, endsAt: null, progress: null, lastError: null });
  const activeEffect = (extra = {}) => ({ running: true, id: 'offline-effect', pattern: 'blink', startedAt: '2026-10-23T12:00:00Z',
    endsAt: '2026-10-23T12:00:10Z', progress: 0.3, lastError: null, ...extra });
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const wait = async (check, message) => {
    const until = performance.now() + 1800;
    while (!check()) {
      if (performance.now() > until) throw new Error(`Timed out: ${message}`);
      await sleep(5);
    }
  };
  async function fixture({ pageLocation, savedSession, health, startupState, holdHealth = false, healthFailures = 0 } = {}) {
    const iframe = document.createElement('iframe');
    // Remove all resource loading, retaining the real application's DOM.
    iframe.srcdoc = html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
    const loaded = new Promise(resolve => iframe.addEventListener('load', resolve, { once: true }));
    document.body.append(iframe); await loaded;
    const w = iframe.contentWindow; const doc = w.document;
    const $ = id => doc.getElementById(id);
    const api = { state: startupState ?? snapshot(), health: health ?? { name: 'lighttime', version: 'offline', authRequired: true }, effect: idleEffect(), requests: [], streams: [], handlers: [], devices: [],
      choosers: [], gatt: [], errors: [], healthFailures, eventFailures: 0, iframe, w, $ };
    api.healthReply = holdHealth ? deferred() : null;
    let healthHeld = holdHealth;
    const encoder = new w.TextEncoder();
    const json = value => new w.Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
    api.fetch = async (input, init = {}) => {
      const path = new URL(String(input)).pathname; const method = init.method ?? 'GET';
      api.requests.push({ path, method, body: init.body ?? null, url: String(input), authorization: new Headers(init.headers).get('Authorization') });
      const handler = api.handlers.find(item => item.path === path && item.method === method);
      if (handler) { api.handlers.splice(api.handlers.indexOf(handler), 1); return await handler.run(init); }
      if (path === '/v1/health') {
        if (api.healthFailures > 0) { api.healthFailures--; throw new w.TypeError('Offline health fetch'); }
        if (healthHeld) { healthHeld = false; api.healthReply.started = true; return json(await api.healthReply.promise); }
        return json(api.health);
      }
      if (path === '/v1/events') {
        if (api.eventFailures > 0) { api.eventFailures--; throw new w.TypeError('Offline event fetch'); }
        const entry = { cancelled: false };
        const body = new w.ReadableStream({
          start(controller) { entry.controller = controller; },
          cancel() { entry.cancelled = true; },
        });
        api.streams.push(entry);
        return new w.Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
      }
      if (path === '/v1/devices') return json({ devices: api.devices });
      if (path === '/v1/state' && method === 'GET') return json(api.state);
      if (path === '/v1/state' && method === 'PATCH') {
        api.effect = idleEffect();
        const patch = JSON.parse(init.body);
        api.state = snapshot(patch.brightness ?? (patch.power === false ? 0 : patch.power === true ? 5 : api.state.brightness), 1,
          { cct: patch.cct ?? api.state.cct, updatedAt: new Date(Date.parse(api.state.updatedAt) + 1000).toISOString() });
        return json(api.state);
      }
      if (path === '/v1/connect' && method === 'POST') return json(api.state);
      if (path === '/v1/disconnect' && method === 'POST') return json(disconnected());
      if (path === '/v1/effects' && method === 'GET') return json(api.effect);
      if (path === '/v1/effects/start' && method === 'POST') {
        const config = JSON.parse(init.body); api.baseline = api.state;
        api.effect = activeEffect({ pattern: config.pattern, ...(config.duration === null ? { endsAt: null, progress: null } : {}) });
        return json(api.effect);
      }
      if (path === '/v1/effects/stop' && method === 'POST') {
        if (JSON.parse(init.body).restore !== false && api.baseline) api.state = snapshot(api.baseline.brightness, 5, { cct: api.baseline.cct });
        api.effect = idleEffect(); return json(api.effect);
      }
      throw new Error(`Unexpected fake request: ${method} ${path}`);
    };
    w.fetch = api.fetch;
    Object.defineProperty(w, 'isSecureContext', { value: true, configurable: true });
    Object.defineProperty(w.navigator, 'bluetooth', { configurable: true, value: {
      requestDevice() { const next = api.choosers.shift(); if (!next) throw new Error('No fake chooser queued'); return next; },
    } });
    w.addEventListener('unhandledrejection', event => { api.errors.push(String(event.reason)); event.preventDefault(); });
    api.hold = (path = '/v1/state', method = 'GET') => {
      const held = deferred(); held.started = false;
      api.handlers.push({ path, method, run(init) { held.started = true; held.signal = init.signal; return held.promise.then(json); } });
      return held;
    };
    api.emit = value => {
      const stream = api.streams.at(-1);
      assert(stream && !stream.cancelled, 'Expected active fake SSE stream');
      stream.controller.enqueue(encoder.encode(`event: state\ndata: ${JSON.stringify(value)}\n\n`));
    };
    api.failStream = () => {
      const stream = api.streams.at(-1); stream.failed = true;
      stream.controller.error(new w.TypeError('Offline event stream'));
    };
    api.online = () => w.dispatchEvent(new w.Event('online'));
    api.foreground = () => {
      Object.defineProperty(doc, 'hidden', { configurable: true, value: false });
      doc.dispatchEvent(new w.Event('visibilitychange'));
    };
    api.view = () => Object.fromEntries(['status', 'actual-brightness', 'actual-cct', 'write-status', 'events-status', 'last-read', 'effect-status', 'effect-error', 'effect-action']
      .map(id => [id, $(id).textContent]));
    api.input = (id, value) => { $(id).value = String(value); $(id).dispatchEvent(new w.Event('input', { bubbles: true })); };
    api.click = id => $(id).click();
    api.hide = () => w.dispatchEvent(new w.PageTransitionEvent('pagehide', { persisted: true }));
    api.show = () => w.dispatchEvent(new w.PageTransitionEvent('pageshow', { persisted: true }));
    api.attach = async (value = snapshot()) => {
      api.state = value; api.click('attach');
      await wait(() => !api.$('attach').disabled && !api.$('brightness').disabled && api.streams.length > 0, 'bridge attach');
    };
    api.isDisconnected = () => {
      assert($('brightness').disabled && $('cct').disabled && $('apply').disabled && $('refresh').disabled && $('power').disabled && $('disconnect').disabled,
        'All light controls must be disabled');
      assert($('actual-brightness').textContent === '—', 'Actual readback must clear synchronously');
      assert($('indicator').dataset.state !== 'connected', 'Connected indicator must clear');
      assert(!$('events-status').textContent.includes('active'), 'Live events must clear');
      assert(!$('write-status').textContent.includes('match actual'), 'Write verification must clear');
      assert($('effect-start').disabled && $('effect-stop').disabled && $('effect-settings').disabled, 'Pattern controls must clear');
      assert($('effect-progress').hidden && !$('effect-status').textContent.includes('running'), 'Runner display must clear');
    };
    api.makeGatt = ({ holdConnect = false, holdService = false } = {}) => {
      const device = new w.EventTarget(); device.id = 'OFFLINE-GATT'; device.name = 'PL103 fake';
      const gate = deferred(); const serviceGate = deferred();
      const notify = new w.EventTarget(); const write = new w.EventTarget(); const parser = new FrameParser();
      const fake = { device, gate, serviceGate, connects: 0, disconnects: 0, subscriptions: 0, writes: 0,
        brightness: 5, cct: 5000, frames: [], holdReads: false, replies: [] };
      notify.startNotifications = async () => { fake.subscriptions++; return notify; };
      notify.stopNotifications = async () => { fake.subscriptions--; return notify; };
      const reply = (frame, payload) => {
        const bytes = encodeFrame({ ...frame, messageType: RESPONSE_TYPE, payload });
        notify.value = new DataView(bytes.buffer);
        notify.dispatchEvent(new w.Event('characteristicvaluechanged'));
      };
      write.writeValueWithResponse = async bytes => {
        fake.writes++;
        for (const frame of parser.push(bytes)) {
          fake.frames.push(frame);
          if (frame.command === COMMAND.info) reply(frame, new TextEncoder().encode('OFFLINE-TEST\0pl103\0'));
          else if (frame.command === COMMAND.deviceId) reply(frame, new Uint8Array(2));
          else {
            const payload = frame.payload.slice(); const view = new DataView(payload.buffer);
            if (payload[2] === 1) {
              if (frame.command === COMMAND.brightness) fake.brightness = view.getFloat32(3, true);
              else fake.cct = view.getUint16(3, true);
            } else {
              if (frame.command === COMMAND.brightness) view.setFloat32(3, fake.brightness, true);
              else view.setUint16(3, fake.cct, true);
              if (fake.holdReads) fake.replies.push(() => reply(frame, payload));
              else reply(frame, payload);
            }
          }
        }
      };
      const service = { getCharacteristic: async uuid => uuid.endsWith('9600') ? write : notify };
      const server = { connected: false,
        async connect() { fake.connects++; if (holdConnect) await gate.promise; server.connected = true; return server; },
        disconnect() { fake.disconnects++; server.connected = false; device.dispatchEvent(new w.Event('gattserverdisconnected')); },
        async getPrimaryService() { if (holdService) await serviceGate.promise; return service; },
      };
      device.gatt = server; api.gatt.push(fake); return fake;
    };
    api.direct = async fake => {
      api.choosers.push(Promise.resolve(fake.device)); api.click('direct');
      await wait(() => !$('brightness').disabled && $('status').textContent.startsWith('Bluetooth connected'), 'direct GATT connect');
    };
    api.close = async () => { api.hide(); await sleep(10); iframe.remove(); };
    $('token').value = 'offline-dummy-token'; $('bridge-url').value = 'http://offline.invalid';
    // Never read a parent tab's saved token/session, including when running from
    // a profile with unrelated saved state. This fake is local to the frame.
    const session = new Map(savedSession ? [['lighttime.browser.session', JSON.stringify(savedSession)]] : []);
    Object.defineProperty(w, 'sessionStorage', { configurable: true, value: {
      getItem: key => session.get(key) ?? null, setItem: (key, value) => session.set(key, value), removeItem: key => session.delete(key),
    } });
    if (pageLocation) w.eval(`((location) => {${bundle}\n})(${JSON.stringify(pageLocation)})`);
    else w.eval(bundle);
    return api;
  }
  async function test(name, run, options) {
    globalThis.lighttimeBrowserRegressionProgress = { name, completed: results.length, passed: results.filter(r => r.passed).length };
    let api;
    try {
      api = await fixture(options); await run(api);
      assert(api.errors.length === 0, `Unhandled rejections: ${api.errors.join(', ')}`);
      results.push({ name, passed: true, view: api.view(), requests: api.requests,
        gatt: api.gatt.map(g => ({ connects: g.connects, disconnects: g.disconnects, writes: g.writes })) });
    } catch (error) { results.push({ name, passed: false, error: String(error), view: api?.view() }); }
    finally { if (api) await api.close(); }
  }

  await test('SSE older than verified PATCH cannot roll back actual values or draft', async a => {
    await a.attach(); a.input('brightness', 8); a.click('apply');
    await wait(() => a.$('actual-brightness').textContent === '8%' && !a.$('refresh').disabled, 'PATCH 8 verified');
    const before = a.view(); a.emit(snapshot(5)); await sleep(20);
    assert(a.$('actual-brightness').textContent === '8%' && a.$('brightness').value === '8', 'Old SSE replaced verified 8%');
    assert(a.$('last-read').textContent === before['last-read'], 'Old SSE moved readback time backwards');
    assert(a.$('write-status').textContent === before['write-status'], 'Old SSE changed write outcome');
  });
  await test('Equal-conflicting, undated and invalid-date connected events cannot establish newer readback', async a => {
    await a.attach();
    for (const event of [snapshot(7), snapshot(8, 1, { updatedAt: null }), snapshot(9, 1, { updatedAt: 'invalid' }), snapshot(10, 1, { brightness: null })]) {
      a.emit(event); await sleep(10); assert(a.$('actual-brightness').textContent === '5%', 'Unordered/unverified state replaced readback');
    }
    a.emit(snapshot(12, 2)); await wait(() => a.$('actual-brightness').textContent === '12%', 'newer event accepted');
    assert(a.$('brightness').value === '12', 'Clean draft did not track accepted newer readback');
  });
  await test('Newer SSE before old PATCH retains draft and cannot falsely verify settings', async a => {
    await a.attach(); const patch = a.hold('/v1/state', 'PATCH');
    a.input('brightness', 8); a.click('apply'); await wait(() => patch.started, 'held PATCH');
    a.emit(snapshot(12, 2)); await wait(() => a.$('actual-brightness').textContent === '12%', 'newer SSE');
    patch.resolve(snapshot(8, 1)); await wait(() => !a.$('refresh').disabled, 'PATCH finished');
    assert(a.$('actual-brightness').textContent === '12%' && a.$('brightness').value === '8', 'Actual or requested draft overwritten');
    assert(!a.$('apply').disabled && a.$('write-status').textContent.includes('differs'), 'Old HTTP result falsely cleared/verified draft');
    assert(!a.$('status').textContent.includes('Settings verified'), 'Old HTTP result falsely verified displayed state');
  });
  await test('Newer SSE while refreshing outranks late old JSON and preserves unsent draft', async a => {
    await a.attach(); a.input('brightness', 11); const read = a.hold(); a.click('refresh');
    await wait(() => read.started, 'held refresh'); a.emit(snapshot(9, 2));
    await wait(() => a.$('actual-brightness').textContent === '9%', 'new SSE');
    read.resolve(snapshot(5, 1)); await wait(() => !a.$('refresh').disabled, 'refresh finished');
    assert(a.$('actual-brightness').textContent === '9%' && a.$('brightness').value === '11' && !a.$('apply').disabled, 'Late read lost accepted state or draft');
  });
  await test('Undated SSE disconnect defeats pending PATCH and clears queued controls', async a => {
    await a.attach(); const patch = a.hold('/v1/state', 'PATCH');
    a.input('brightness', 8); a.click('apply'); await wait(() => patch.started, 'PATCH started');
    a.$('live').checked = true; a.input('brightness', 10); a.emit(disconnected());
    await wait(() => a.$('actual-brightness').textContent === '—', 'undated disconnect'); a.isDisconnected();
    patch.resolve(snapshot(8, 1)); await sleep(250); a.isDisconnected();
    assert(a.requests.filter(r => r.method === 'PATCH').length === 1, 'Queued controls leaked after disconnect');
  });
  await test('Bridge BFcache invalidates synchronously, cancels debounce and requires fresh attach with a reset clock', async a => {
    await a.attach(snapshot(8, 50)); a.$('live').checked = true; a.input('brightness', 15);
    a.hide(); a.isDisconnected(); a.show(); a.isDisconnected();
    const count = a.requests.length; a.click('refresh'); await sleep(250);
    assert(a.requests.length === count, 'Restored page fetched or wrote before fresh attach');
    await a.attach(snapshot(5, 0)); assert(a.$('actual-brightness').textContent === '5%', 'New session inherited old timestamp watermark');
    a.click('refresh'); await wait(() => !a.$('refresh').disabled, 'fresh refresh');
    assert(a.requests.filter(r => r.path === '/v1/state' && r.method === 'GET').length === 3, 'Fresh client did not issue refresh');
  });
  await test('Late attach cancellation cannot clear a newer pending configuration or show its old response', async a => {
    const old = a.hold(); a.click('attach'); await wait(() => old.started, 'old attach started');
    a.hide(); a.show(); const fresh = a.hold(); a.click('attach'); await wait(() => fresh.started, 'new attach started');
    old.resolve(snapshot(88, 50)); await sleep(20);
    assert(a.$('attach').disabled && a.$('status').textContent.includes('Reading the bridge'), 'Old configuring catch/finally overwrote pending new session');
    fresh.resolve(snapshot(6)); await wait(() => !a.$('brightness').disabled, 'new attach finished');
    assert(a.$('actual-brightness').textContent === '6%', 'Late old attach won');
  });
  await test('Late device scan cannot restore a stale picker or replace a fresh session status', async a => {
    const scan = a.hold('/v1/devices'); a.click('scan'); await wait(() => scan.started, 'scan started');
    a.hide(); a.show(); await a.attach(snapshot(6));
    scan.resolve({ devices: [{ transport: 'ble', address: 'STALE', name: 'PL103 old' }] }); await sleep(20);
    assert(a.$('device-picker').hidden && a.$('actual-brightness').textContent === '6%' && a.$('status').textContent.includes('Bridge connected'), 'Late scan altered fresh session');
  });
  await test('Late selected bridge connection cannot overwrite a fresh attachment', async a => {
    a.devices = [{ transport: 'ble', address: 'FAKE-G60', name: 'PL103 offline' }];
    a.click('scan'); await wait(() => !a.$('device-picker').hidden && !a.$('scan').disabled, 'device scan');
    const connection = a.hold('/v1/connect', 'POST'); a.click('connect-device');
    await wait(() => connection.started, 'bridge connect');
    a.hide(); a.show(); await a.attach(snapshot(6));
    connection.resolve(snapshot(88, 50)); await sleep(20);
    assert(a.$('actual-brightness').textContent === '6%' && !a.$('brightness').disabled && a.$('status').textContent.includes('Bridge connected'), 'Late bridge connect altered fresh session');
  });
  await test('Selecting a bridge device after a prior session resets snapshot ordering', async a => {
    await a.attach(snapshot(8, 50));
    a.devices = [{ transport: 'ble', address: 'FAKE-G60', name: 'PL103 offline' }];
    a.click('scan'); await wait(() => !a.$('device-picker').hidden && !a.$('scan').disabled, 'device scan');
    a.state = snapshot(3); a.click('connect-device');
    await wait(() => !a.$('brightness').disabled, 'selected device connected');
    assert(a.$('actual-brightness').textContent === '3%' && a.$('brightness').value === '3', 'New connection inherited stale snapshot ordering');
  });
  await test('Late explicit bridge disconnect cleanup cannot release a freshly attached session', async a => {
    await a.attach(); const cleanup = a.hold('/v1/disconnect', 'POST'); a.click('disconnect');
    await wait(() => cleanup.started, 'disconnect request'); a.hide(); a.show(); await a.attach(snapshot(6));
    cleanup.resolve(disconnected()); await sleep(20);
    assert(a.$('actual-brightness').textContent === '6%' && !a.$('brightness').disabled && a.$('status').textContent.includes('Bridge connected'), 'Old disconnect cleanup destroyed fresh session');
  });
  await test('Direct GATT BFcache clears visible state before teardown yields; old cleanup cannot clear fresh bridge', async a => {
    const gatt = a.makeGatt(); await a.direct(gatt);
    a.$('live').checked = true; a.input('brightness', 15); a.hide(); a.isDisconnected(); a.show();
    await a.attach(snapshot(6)); await sleep(250);
    assert(a.$('actual-brightness').textContent === '6%' && !a.$('brightness').disabled, 'Old native cleanup cleared fresh bridge');
    assert(gatt.disconnects > 0 && gatt.subscriptions === 0 && !gatt.frames.some(f => f.payload[2] === 1), 'Direct BLE cleanup leaked subscription or pending setter');
  });
  await test('Late Bluetooth chooser cannot replace restored fresh bridge or open GATT', async a => {
    const choice = deferred(); const gatt = a.makeGatt(); a.choosers.push(choice.promise); a.click('direct');
    a.hide(); a.show(); await a.attach(snapshot(6)); choice.resolve(gatt.device); await sleep(30);
    assert(gatt.connects === 0 && gatt.disconnects > 0, 'Late chooser opened GATT or leaked selected transport');
    assert(a.$('actual-brightness').textContent === '6%' && a.$('status').textContent.includes('Bridge connected'), 'Late chooser overwrote fresh bridge');
  });
  await test('Late direct GATT setup and cancellation cannot overwrite restored fresh bridge', async a => {
    const gatt = a.makeGatt({ holdService: true }); a.choosers.push(Promise.resolve(gatt.device)); a.click('direct');
    await wait(() => gatt.connects === 1, 'GATT connect'); a.hide(); a.show(); await a.attach(snapshot(6));
    gatt.serviceGate.resolve(); await sleep(30);
    assert(a.$('actual-brightness').textContent === '6%' && !a.$('brightness').disabled && a.$('status').textContent.includes('Bridge connected'), 'Late native setup changed fresh session');
    assert(gatt.subscriptions === 0, 'Late native setup leaked observer');
  });
  await test('Direct pending readback cannot reanimate restored page; fresh direct connection works', async a => {
    const old = a.makeGatt(); await a.direct(old); old.holdReads = true; a.click('refresh');
    await wait(() => old.replies.length > 0, 'held direct readback');
    a.hide(); a.isDisconnected(); a.show(); for (const reply of old.replies) reply(); await sleep(20); a.isDisconnected();
    const fresh = a.makeGatt(); fresh.brightness = 7; await a.direct(fresh);
    assert(a.$('actual-brightness').textContent === '7%', 'Fresh direct readback failed');
  });
  for (const origin of ['http://100.64.0.99:8877', 'https://bridge.example.test:10000', 'https://device.example.test:9443']) {
    await test('Bridge /ui/ uses its exact origin: ' + origin, async a => {
      await wait(() => !a.$('connection-settings').hidden, 'secured health check');
      assert(a.requests.every(r => r.path === '/v1/health'), 'Secured page requested more than health before explicit attach');
      assert(a.$('bridge-url').value === origin, 'Same-origin URL changed its scheme/host/port');
      await a.attach(); assert(a.requests.every(r => r.url.startsWith(origin + '/v1/')), 'Request did not use the serving bridge');
    }, { pageLocation: { protocol: origin.startsWith('https') ? 'https:' : 'http:', origin, pathname: '/ui/' } });
  }
  await test('An opted-in session URL is preserved and can be edited on a remote /ui/', async a => {
    await wait(() => !a.$('connection-settings').hidden, 'secured health check');
    assert(a.$('bridge-url').value === 'http://old-bridge.example.test:8877' && a.$('remember').checked, 'Saved endpoint was replaced');
    a.input('bridge-url', 'https://bridge.example.test:10000'); await a.attach();
    assert(a.requests.every(r => r.url.startsWith('https://bridge.example.test:10000/')), 'Edited URL was ignored');
  }, { pageLocation: { protocol: 'https:', origin: 'https://bridge.example.test:10000', pathname: '/ui/' },
    savedSession: { token: 'offline-dummy-token', url: 'http://old-bridge.example.test:8877' } });
  await test('Patterns require bridge attach; direct BLE remains manual and never polls effects', async a => {
    const gatt = a.makeGatt(); await a.direct(gatt);
    assert(a.$('effect-start').disabled && a.$('effect-stop').disabled && a.$('effect-settings').disabled, 'Direct BLE enabled server patterns');
    assert(a.$('patterns-connection').textContent.includes('Attach through the bridge'), 'Bridge attachment instruction missing');
    await sleep(1100); assert(!a.requests.some(r => r.path.startsWith('/v1/effects')), 'Direct BLE polled effects');
  });
  await test('Pattern configuration and actual progress remain separate from verified output', async a => {
    await a.attach(); a.input('effect-min', 2); a.input('effect-max', 25); a.input('effect-cct', 3000); a.input('effect-cct-end', 6000);
    a.$('effect-pattern').value = 'cct-sweep'; a.$('effect-pattern').dispatchEvent(new a.w.Event('change'));
    a.$('effect-until-stopped').checked = true; a.$('effect-until-stopped').dispatchEvent(new a.w.Event('change'));
    a.click('effect-start'); await wait(() => a.$('effect-status').textContent.includes('CCT sweep running'), 'sweep accepted');
    const config = JSON.parse(a.requests.find(r => r.path === '/v1/effects/start').body);
    assert(config.duration === null && config.period === 4 && config.minBrightness === 2 && config.maxBrightness === 25 && config.cct === 3000 && config.cctEnd === 6000 && config.restoreOnComplete, 'Config contract differs');
    assert(a.$('actual-brightness').textContent === '5%' && a.$('actual-cct').textContent === '5000 K', 'Requested pattern was displayed as actual output');
    assert(a.$('effect-progress').hidden, 'Unbounded pattern showed finite progress');
    a.effect = activeEffect({ pattern: 'cct-sweep', progress: 0.6 });
    await wait(() => a.$('effect-status').textContent.includes('60%'), 'polled actual progress');
    assert(a.$('effect-max').value === '25' && !a.$('effect-progress').hidden, 'Polling replaced requested settings');
    a.effect = { ...idleEffect(), lastError: 'Offline verification failure' };
    await wait(() => a.$('effect-error').textContent.includes('Offline verification failure'), 'runner failure visible');
    assert(a.$('actual-brightness').textContent === '5%', 'Runner failure changed actual readback');
  });
  await test('Fade form disallows unbounded runs and defaults to holding its endpoint', async a => {
    await a.attach();
    a.$('effect-until-stopped').checked = true;
    a.$('effect-pattern').value = 'fade-out'; a.$('effect-pattern').dispatchEvent(new a.w.Event('change'));
    assert(a.$('effect-until-stopped').disabled && !a.$('effect-until-stopped').checked && a.$('effect-period').disabled && !a.$('effect-restore').checked, 'Fade controls have incorrect defaults');
    a.click('effect-start'); await wait(() => a.requests.some(r => r.path === '/v1/effects/start'), 'fade start');
    const config = JSON.parse(a.requests.find(r => r.path === '/v1/effects/start').body);
    assert(config.pattern === 'fade-out' && config.duration === 10 && !config.restoreOnComplete && !('period' in config), 'Fade body invalid');
  });
  await test('Invalid min/max never dispatches a start, and Stop explicitly requests baseline restoration', async a => {
    await a.attach(); a.input('effect-min', 20); a.input('effect-max', 5); a.click('effect-start');
    await wait(() => a.$('effect-action').textContent.includes('start failed'), 'invalid range feedback');
    assert(!a.requests.some(r => r.path === '/v1/effects/start'), 'Invalid config sent to bridge');
    a.input('effect-min', 0); a.click('effect-start');
    await wait(() => a.$('effect-status').textContent.includes('Blink running') && !a.$('effect-stop').disabled, 'blink accepted');
    a.click('effect-stop'); await wait(() => a.$('effect-status').textContent.includes('No pattern running') && !a.$('effect-stop').disabled, 'stop finished');
    assert(JSON.parse(a.requests.find(r => r.path === '/v1/effects/stop').body).restore === true, 'Stop did not request restoration');
  });
  await test('Manual Apply relies on backend cancellation and keeps verified output', async a => {
    await a.attach(); a.click('effect-start'); await wait(() => a.$('effect-status').textContent.includes('running') && !a.$('effect-stop').disabled, 'pattern started');
    a.input('brightness', 8); a.click('apply'); await wait(() => a.$('actual-brightness').textContent === '8%', 'manual output verified');
    await wait(() => a.$('effect-status').textContent === 'No pattern running.', 'manual cancellation status');
    assert(!a.requests.some(r => r.path === '/v1/effects/stop'), 'Manual Apply issued a restoring stop');
  });
  await test('Polling is limited to once per second while attached, stops on pagehide, and never stops the server pattern', async a => {
    await a.attach(); a.effect = activeEffect();
    await wait(() => a.requests.some(r => r.path === '/v1/effects'), 'initial runner poll');
    const count = a.requests.filter(r => r.path === '/v1/effects').length; await sleep(1150);
    assert(a.requests.filter(r => r.path === '/v1/effects').length === count + 1, 'Status polling cadence differs from 1 Hz');
    a.hide(); a.isDisconnected(); const hiddenCount = a.requests.length; await sleep(1100);
    assert(a.requests.length === hiddenCount && a.effect.running, 'Pagehide polled or stopped server pattern');
    assert(!a.requests.some(r => r.path === '/v1/effects/stop' || r.path === '/v1/disconnect'), 'Pagehide sent a server stop/disconnect');
    a.show(); await a.attach(); await wait(() => a.$('effect-status').textContent.includes('Blink running'), 'reattach sees persistent runner');
  });
  await test('A status poll started before a mutation cannot replace the mutation result', async a => {
    const old = a.hold('/v1/effects'); await a.attach(); await wait(() => old.started, 'held runner poll');
    a.click('effect-start'); await wait(() => a.$('effect-status').textContent.includes('Blink running'), 'start result');
    old.resolve(idleEffect()); await sleep(30);
    assert(a.$('effect-status').textContent.includes('Blink running'), 'Older poll overwrote start result');
  });
  await test('Late effect read/start/stop completions cannot alter a fresh session', async a => {
    const poll = a.hold('/v1/effects'); await a.attach(); await wait(() => poll.started, 'held effect read');
    const start = a.hold('/v1/effects/start', 'POST'); a.click('effect-start'); await wait(() => start.started, 'held effect start');
    a.hide(); a.show(); await a.attach(snapshot(6)); const fresh = a.view();
    poll.resolve(activeEffect()); start.resolve(activeEffect()); await sleep(30);
    assert(a.$('actual-brightness').textContent === '6%' && !a.$('effect-status').textContent.includes('Blink running') && a.$('effect-action').textContent === fresh['effect-action'], 'Late effect read/start replaced fresh session');
    const stop = a.hold('/v1/effects/stop', 'POST'); a.click('effect-stop'); await wait(() => stop.started, 'held stop');
    a.hide(); a.show(); await a.attach(snapshot(7)); const current = a.view(); stop.resolve(idleEffect()); await sleep(30);
    assert(a.$('actual-brightness').textContent === '7%' && a.$('effect-action').textContent === current['effect-action'], 'Late stop altered fresh session');
  });
  const personalPage = { protocol: 'https:', origin: 'https://personal.example.test:10000', pathname: '/ui/' };
  const noAuthHealth = { name: 'lighttime', version: 'offline', authRequired: false, defaultAddress: 'FAKE-G60' };
  await test('Personal sliders apply immediately and coalesce dragging into one latest pending target', async a => {
    await wait(() => !a.$('brightness').disabled, 'personal attach');
    assert(a.$('live').checked, 'Personal sliders require an extra apply opt-in');
    assert(!a.requests.some(r => r.method === 'PATCH'), 'Startup applied an unsent draft');
    const first = a.hold('/v1/state', 'PATCH');
    const last = a.hold('/v1/state', 'PATCH');
    const before = performance.now();
    a.input('brightness', 7);
    await wait(() => first.started, 'first live write');
    assert(performance.now() - before < 150, 'First slider write waited for a trailing debounce');
    a.input('brightness', 8); a.input('brightness', 9);
    await sleep(30);
    assert(a.requests.filter(r => r.method === 'PATCH').length === 1, 'Slider writes overlapped');
    first.resolve(snapshot(7, 1));
    await wait(() => last.started, 'latest pending write');
    const patches = a.requests.filter(r => r.method === 'PATCH');
    assert(patches.length === 2 && JSON.parse(patches[1].body).brightness === 9, 'Intermediate drag target was replayed');
    assert(patches.every(r => Object.keys(JSON.parse(r.body)).join() === 'brightness'), 'Brightness-only dragging resent unchanged CCT');
    last.resolve(snapshot(9, 2));
    await wait(() => a.$('actual-brightness').textContent === '9%' && a.$('apply').disabled, 'latest target verified');
  }, { pageLocation: personalPage, health: noAuthHealth });
  await test('Personal /ui/ attaches automatically with no setup, auth header, or connect click', async a => {
    await wait(() => !a.$('brightness').disabled && !a.$('effect-start').disabled, 'automatic controller ready');
    await wait(() => a.requests.some(r => r.path === '/v1/effects'), 'automatic runner polling');
    assert(a.$('connection-settings').hidden && a.$('reconnect-actions').hidden, 'Personal page displayed setup');
    assert(a.requests.every(r => r.authorization === null && r.url.startsWith(personalPage.origin + '/')), 'Personal flow leaked saved auth or used another origin');
    assert(!a.requests.some(r => r.path === '/v1/connect' || r.path === '/v1/devices'), 'Startup connected/scanned an already connected light');
    a.input('brightness', 7); a.click('apply'); await wait(() => a.$('actual-brightness').textContent === '7%', 'immediate manual control');
    a.click('effect-start'); await wait(() => a.$('effect-status').textContent.includes('Blink running'), 'immediate pattern control');
  }, { pageLocation: personalPage, health: noAuthHealth,
    savedSession: { token: 'offline-old-dummy-token', url: 'http://old-bridge.example.test:8877' } });
  await test('Personal startup briefly retries disconnected readback and enables controls without setup', async a => {
    assert(a.$('connection-settings').hidden, 'Setup flashed during startup');
    await wait(() => a.requests.some(r => r.path === '/v1/state'), 'first startup read');
    a.state = snapshot(9); await wait(() => !a.$('brightness').disabled, 'startup retry connected');
    assert(a.$('actual-brightness').textContent === '9%' && a.$('connection-settings').hidden, 'Retry did not establish actual output');
  }, { pageLocation: personalPage, health: noAuthHealth, startupState: disconnected() });
  await test('Personal reconnect uses only the bridge default address and restores direct controls', async a => {
    await wait(() => !a.$('brightness').disabled && a.streams.length > 0, 'automatic attached');
    a.emit(disconnected()); await wait(() => !a.$('reconnect-actions').hidden && !a.$('reconnect-light').disabled, 'simple reconnect offered');
    a.state = snapshot(6); a.click('reconnect-light'); await wait(() => !a.$('brightness').disabled, 'simple reconnect finished');
    const request = a.requests.find(r => r.path === '/v1/connect');
    assert(request && JSON.parse(request.body).address === 'FAKE-G60' && JSON.parse(request.body).transport === 'ble', 'Reconnect did not use server default address');
    assert(a.$('connection-settings').hidden && !a.requests.some(r => r.path === '/v1/devices'), 'Reconnect exposed setup or scanned');
  }, { pageLocation: personalPage, health: noAuthHealth });
  await test('Personal startup lifecycle cancels late readback and automatically attaches a fresh session', async a => {
    await wait(() => !a.$('brightness').disabled, 'automatic attached');
    a.hide(); a.isDisconnected(); const old = a.hold(); a.show(); await wait(() => old.started, 'restored automatic read');
    a.hide(); a.show(); await wait(() => !a.$('brightness').disabled, 'fresh automatic session');
    old.resolve(snapshot(88, 50)); await sleep(30);
    assert(a.$('actual-brightness').textContent === '5%' && a.$('connection-settings').hidden, 'Late automatic read changed fresh session');
  }, { pageLocation: personalPage, health: noAuthHealth });
  await test('Explicit personal disconnect still offers an explicit hardware reconnect', async a => {
    await wait(() => !a.$('brightness').disabled, 'personal attach');
    a.click('disconnect');
    await wait(() => !a.$('reconnect-light').disabled && a.$('status').textContent === 'Light disconnected.', 'explicit disconnect');
    assert(a.$('reconnect-light').textContent === 'Reconnect light', 'Explicit disconnect lost hardware reconnect action');
    assert(a.requests.filter(r => r.path === '/v1/connect').length === 0, 'Disconnect reconnected automatically');
    a.click('reconnect-light'); await wait(() => !a.$('brightness').disabled, 'explicit reconnect');
    assert(a.requests.filter(r => r.path === '/v1/connect' && r.method === 'POST').length === 1, 'Explicit reconnect was not sent once');
  }, { pageLocation: personalPage, health: noAuthHealth });
  await test('Late health discovery cannot replace a newer personal session or reveal setup', async a => {
    await wait(() => a.healthReply.started, 'held initial health');
    a.hide(); a.show(); await wait(() => !a.$('brightness').disabled, 'fresh health and automatic attach');
    a.healthReply.resolve({ name: 'lighttime', version: 'offline', authRequired: true }); await sleep(30);
    assert(a.$('connection-settings').hidden && a.$('actual-brightness').textContent === '5%' && !a.$('brightness').disabled, 'Late health changed active personal session');
  }, { pageLocation: personalPage, health: noAuthHealth, holdHealth: true });
  await test('Personal bridge without a default address offers readback retry without setup or MAC input', async a => {
    await wait(() => !a.$('brightness').disabled && a.streams.length > 0, 'automatic attach');
    a.emit(disconnected()); await wait(() => !a.$('reconnect-actions').hidden, 'simple retry offered');
    assert(a.$('reconnect-light').textContent === 'Retry connection', 'Missing retry label');
    a.state = snapshot(6); a.click('reconnect-light'); await wait(() => !a.$('brightness').disabled, 'readback retry attached');
    assert(!a.requests.some(r => r.path === '/v1/connect' || r.path === '/v1/devices') && a.$('connection-settings').hidden, 'Retry requested address setup or guessed a device');
  }, { pageLocation: personalPage, health: { ...noAuthHealth, defaultAddress: null } });
  await test('Personal startup retry delay is cancelled on pagehide before a fresh automatic session', async a => {
    await wait(() => a.requests.some(r => r.path === '/v1/state'), 'initial disconnected read');
    a.hide(); a.isDisconnected(); a.state = snapshot(7); a.show();
    await wait(() => !a.$('brightness').disabled, 'fresh automatic attachment');
    await sleep(1100);
    assert(a.$('actual-brightness').textContent === '7%' && a.requests.filter(r => r.path === '/v1/state').length === 2, 'Old startup delay fetched or changed fresh session');
  }, { pageLocation: personalPage, health: noAuthHealth, startupState: disconnected() });
  await test('Invalid startup health offers same-origin retry without setup prompts or automatic writes', async a => {
    await wait(() => !a.$('reconnect-actions').hidden && !a.$('reconnect-light').disabled, 'invalid health feedback');
    assert(a.$('connection-settings').hidden, 'Invalid health exposed token/setup prompts');
    assert(a.$('status').textContent.includes('startup check failed') && a.requests.every(r => r.path === '/v1/health'), 'Invalid health caused automatic control');
  }, { pageLocation: personalPage, health: { name: 'lighttime', version: 'offline', authRequired: 'false' } });

  await test('Transient health failure recovers once on online/foreground without saved endpoint or draft replay', async a => {
    await wait(() => !a.$('reconnect-light').disabled && a.$('status').textContent.includes('startup check failed'), 'health failure');
    assert(a.$('connection-settings').hidden && !a.$('reconnect-actions').hidden, 'Failure required token/setup');
    a.$('brightness').value = '37';
    a.online(); a.foreground(); a.online();
    await wait(() => !a.$('brightness').disabled, 'network recovery');
    assert(a.requests.filter(r => r.path === '/v1/health').length === 2, 'Recovery events created duplicate probes');
    assert(a.requests.every(r => r.method === 'GET' && r.authorization === null && r.url.startsWith(personalPage.origin + '/')), 'Recovery replayed settings, auth, or another endpoint');
    assert(a.$('brightness').value === '5' && a.$('live').checked && a.$('connection-settings').hidden, 'Recovery did not show verified personal output');
  }, { pageLocation: personalPage, health: noAuthHealth, healthFailures: 1,
    savedSession: { token: 'offline-old-token', url: 'http://old-bridge.example.test:8877' } });

  await test('Failed health Retry connection uses only same-origin discovery without BLE connect', async a => {
    await wait(() => !a.$('reconnect-light').disabled && a.$('status').textContent.includes('startup check failed'), 'retry available');
    assert(a.$('reconnect-light').textContent === 'Retry connection', 'Health failure offered hardware reconnect');
    a.click('reconnect-light'); await wait(() => !a.$('brightness').disabled, 'explicit HTTP retry');
    assert(a.requests.every(r => r.method === 'GET' && r.url.startsWith(personalPage.origin + '/')), 'HTTP retry mutated hardware or used saved endpoint');
  }, { pageLocation: personalPage, health: noAuthHealth, healthFailures: 1 });

  await test('Foreground health retry burst is bounded and remains retryable after exhaustion', async a => {
    await wait(() => a.$('status').textContent.includes('startup check failed'), 'initial failure');
    a.foreground(); a.online();
    await sleep(5400);
    assert(a.requests.length === 5 && a.requests.every(r => r.path === '/v1/health'), 'Health retries exceeded one probe plus three recovery retries');
    assert(!a.$('reconnect-light').disabled && a.$('connection-settings').hidden, 'Exhaustion stranded retry or exposed setup');
    a.healthFailures = 0; a.click('reconnect-light');
    await wait(() => !a.$('brightness').disabled, 'retry after exhausted burst');
  }, { pageLocation: personalPage, health: noAuthHealth, healthFailures: Infinity });

  await test('Pagehide cancels a delayed health retry and ignores connectivity events until restoration', async a => {
    await wait(() => a.$('status').textContent.includes('startup check failed'), 'initial failure');
    a.online(); await wait(() => a.requests.length === 2, 'recovery probe');
    a.hide(); a.online(); a.foreground(); await sleep(650);
    assert(a.requests.length === 2, 'Hidden page retried discovery');
    a.healthFailures = 0; a.show();
    await wait(() => !a.$('brightness').disabled, 'fresh discovery after restoration');
    await sleep(600);
    assert(a.requests.filter(r => r.path === '/v1/health').length === 3, 'Old health timer survived restoration');
  }, { pageLocation: personalPage, health: noAuthHealth, healthFailures: Infinity });

  await test('Explicit manual session supersedes health retries and preserves its remote credentials', async a => {
    await wait(() => a.$('status').textContent.includes('startup check failed'), 'initial failure');
    a.online(); await wait(() => a.requests.length === 2, 'retry probe');
    await a.attach(); a.online(); a.foreground(); await sleep(650);
    assert(a.requests.filter(r => r.path === '/v1/health').length === 2, 'Old health retry replaced manual session');
    const requests = a.requests.filter(r => r.path !== '/v1/health');
    assert(requests.every(r => r.url.startsWith('https://remote.example.test:9443/') && r.authorization === 'Bearer offline-manual-token'), 'Manual endpoint/token changed');
    assert(requests.every(r => r.method === 'GET'), 'Recovery replayed manual writes');
  }, { pageLocation: personalPage, healthFailures: Infinity,
    savedSession: { token: 'offline-manual-token', url: 'https://remote.example.test:9443' } });

  await test('Authenticated health after transient failure reveals saved setup without automatic attach', async a => {
    await wait(() => a.$('status').textContent.includes('startup check failed'), 'initial failure');
    a.online(); await wait(() => !a.$('connection-settings').hidden, 'secured setup');
    a.foreground(); a.online(); await sleep(50);
    assert(a.$('bridge-url').value === 'https://remote.example.test:9443' && a.$('token').value === 'offline-manual-token', 'Secured settings were lost');
    assert(a.requests.length === 2 && a.requests.every(r => r.path === '/v1/health'), 'Secured deployment attached or retried without explicit action');
  }, { pageLocation: personalPage, healthFailures: 1,
    savedSession: { token: 'offline-manual-token', url: 'https://remote.example.test:9443' } });

  await test('SSE loss makes one replacement subscription despite repeated connectivity events', async a => {
    await wait(() => !a.$('brightness').disabled && a.streams.length === 1, 'personal session');
    a.failStream(); await wait(() => a.$('events-status').textContent.includes('Retrying'), 'SSE backoff');
    a.online(); a.foreground(); a.online();
    await wait(() => a.streams.length === 2, 'replacement stream');
    a.emit(snapshot(6, 1)); await wait(() => a.$('events-status').textContent.includes('active'), 'replacement readback');
    a.online(); a.foreground(); await sleep(600);
    assert(a.streams.length === 2 && a.streams.filter(s => !s.cancelled && !s.failed).length === 1, 'Duplicate live SSE subscription');
    assert(a.requests.every(r => r.method === 'GET') && a.$('actual-brightness').textContent === '6%', 'Stream recovery mutated hardware or lost readback');
  }, { pageLocation: personalPage, health: noAuthHealth });

  await test('SSE retry exhaustion is bounded; foreground restarts only the attached stream', async a => {
    await a.attach(); a.eventFailures = Infinity; a.failStream();
    await sleep(5400);
    assert(a.requests.filter(r => r.path === '/v1/events').length === 4, 'SSE retry count exceeded three replacements');
    assert(a.$('events-status').textContent.includes('Live events stopped') && !a.$('reconnect-light').disabled, 'SSE exhaustion did not expose retry');
    a.eventFailures = 0; a.foreground();
    await wait(() => a.streams.length === 2, 'foreground subscription');
    a.emit(snapshot(7, 1)); await wait(() => a.$('actual-brightness').textContent === '7%', 'foreground stream readback');
    assert(a.requests.every(r => r.method === 'GET'), 'SSE foreground repair reattached hardware');
  });

  await test('Pagehide cancels SSE retry timers and late reads before a fresh manual session', async a => {
    await a.attach(); a.failStream();
    await wait(() => a.$('events-status').textContent.includes('Retrying'), 'SSE backoff');
    a.hide(); a.show(); a.online(); a.foreground(); await sleep(650);
    assert(a.requests.filter(r => r.path === '/v1/events').length === 1, 'Old stream retried after pagehide');
    await a.attach(snapshot(6)); await sleep(600);
    assert(a.streams.length === 2 && a.$('actual-brightness').textContent === '6%', 'Old recovery altered fresh session');
  });

  await test('Replacing the session cancels pending SSE recovery without altering the new session', async a => {
    await a.attach(); a.failStream();
    await wait(() => a.$('events-status').textContent.includes('Retrying'), 'old backoff');
    await a.attach(snapshot(6)); await sleep(650);
    assert(a.streams.length === 2 && a.requests.filter(r => r.path === '/v1/events').length === 2, 'Old retry survived a new session');
    assert(a.$('actual-brightness').textContent === '6%' && !a.$('brightness').disabled, 'New session was cleared');
  });

  await test('Late replacement-stream response is aborted and cannot affect a newer session', async a => {
    await a.attach();
    const old = a.hold('/v1/events'); a.failStream();
    await wait(() => old.started, 'held replacement fetch');
    await a.attach(snapshot(6));
    assert(old.signal.aborted, 'Replacing session did not abort its recovery fetch');
    const fresh = a.view(); old.resolve(snapshot(88, 50)); await sleep(600);
    assert(a.streams.length === 2 && a.$('actual-brightness').textContent === '6%', 'Late replacement created a stream or replaced readback');
    assert(a.$('status').textContent === fresh.status && a.$('events-status').textContent === fresh['events-status'], 'Late recovery overwrote new session status');
  });

  await test('SSE recovery keeps attached credentials and unsent drafts without replaying manual settings', async a => {
    await a.attach(); a.input('brightness', 9);
    a.input('bridge-url', 'https://edited.example.test:9443'); a.input('token', 'offline-edited-token');
    a.failStream(); await wait(() => a.streams.length === 2, 'manual replacement stream');
    a.emit(snapshot(6, 1)); await wait(() => a.$('actual-brightness').textContent === '6%', 'replacement output');
    assert(a.$('brightness').value === '9' && !a.$('apply').disabled, 'Recovery discarded unsent draft');
    assert(a.requests.every(r => r.method === 'GET' && r.url.startsWith('https://remote.example.test:9443/') && r.authorization === 'Bearer offline-manual-token'), 'Recovery replayed a mutation or reread edited settings');
  }, { savedSession: { token: 'offline-manual-token', url: 'https://remote.example.test:9443' } });

  await test('Slider PATCHes contain only changed fields and retain full verified readback', async a => {
    await a.attach(); a.input('brightness', 7); a.click('apply');
    await wait(() => a.$('actual-brightness').textContent === '7%' && !a.$('refresh').disabled, 'brightness verified');
    a.input('cct', 5100); a.click('apply');
    await wait(() => a.$('actual-cct').textContent === '5100 K' && !a.$('refresh').disabled, 'CCT verified');
    const patches = a.requests.filter(r => r.method === 'PATCH').map(r => JSON.parse(r.body));
    assert(JSON.stringify(patches) === JSON.stringify([{ brightness: 7 }, { cct: 5100 }]), 'One-slider target resent unchanged fields');
    assert(a.$('actual-brightness').textContent === '7%', 'CCT-only write lost brightness readback');
    a.click('power'); await wait(() => a.$('actual-power').textContent === 'Off' && !a.$('refresh').disabled, 'absolute power off');
    assert(a.requests.filter(r => r.method === 'PATCH').at(-1).body === '{"power":false}', 'Power stopped being an absolute command');
  });

  await test('A queued reversal to the old baseline is compared after the in-flight write', async a => {
    await wait(() => !a.$('brightness').disabled, 'personal session');
    const first = a.hold('/v1/state', 'PATCH'); const reversal = a.hold('/v1/state', 'PATCH');
    a.input('brightness', 7); await wait(() => first.started, 'outgoing target');
    a.input('brightness', 5); first.resolve(snapshot(7, 1));
    await wait(() => reversal.started, 'baseline reversal');
    const patches = a.requests.filter(r => r.method === 'PATCH');
    assert(patches.length === 2 && patches[1].body === '{"brightness":5}', 'Baseline reversal was dropped or resent unchanged CCT');
    reversal.resolve(snapshot(5, 2)); await wait(() => a.$('apply').disabled && a.$('actual-brightness').textContent === '5%', 'reversal verified');
  }, { pageLocation: personalPage, health: noAuthHealth });

  await test('Coalescing retains both latest desired fields until dispatch after the in-flight write', async a => {
    await wait(() => !a.$('brightness').disabled, 'personal session');
    const first = a.hold('/v1/state', 'PATCH'); const latest = a.hold('/v1/state', 'PATCH');
    a.input('brightness', 7); await wait(() => first.started, 'first write');
    a.input('brightness', 8); a.input('cct', 5100); a.input('brightness', 9);
    assert(a.requests.filter(r => r.method === 'PATCH').length === 1, 'Combined targets overlapped first write');
    first.resolve(snapshot(7, 1)); await wait(() => latest.started, 'combined latest target');
    const patches = a.requests.filter(r => r.method === 'PATCH');
    assert(patches.length === 2 && patches[1].body === '{"brightness":9,"cct":5100}', 'Combined pending target lost a field or replayed an intermediate value');
    latest.resolve(snapshot(9, 2, { cct: 5100 }));
    await wait(() => a.$('apply').disabled && a.$('actual-cct').textContent === '5100 K', 'combined target verified');
  }, { pageLocation: personalPage, health: noAuthHealth });

  await test('Coalesced target already confirmed by the in-flight write sends no empty PATCH', async a => {
    await wait(() => !a.$('brightness').disabled, 'personal session');
    const first = a.hold('/v1/state', 'PATCH');
    a.input('brightness', 7); await wait(() => first.started, 'first target');
    a.input('brightness', 9); a.input('brightness', 7); first.resolve(snapshot(7, 1));
    await wait(() => a.$('apply').disabled && !a.$('refresh').disabled, 'matching pending target');
    assert(a.requests.filter(r => r.method === 'PATCH').length === 1, 'Matching target sent an empty or redundant PATCH');
    assert(a.$('write-status').textContent.includes('match the last verified readback'), 'No-op claimed fresh device verification');
  }, { pageLocation: personalPage, health: noAuthHealth });

  await test('Already confirmed slider target preserves read time and does not cancel a pattern', async a => {
    await wait(() => !a.$('brightness').disabled, 'personal session');
    const readTime = a.$('last-read').textContent; const action = a.$('effect-action').textContent;
    a.input('brightness', 5); await sleep(30);
    assert(!a.requests.some(r => r.method === 'PATCH'), 'Unchanged target sent a PATCH');
    assert(a.$('last-read').textContent === readTime && a.$('write-status').textContent.includes('last verified readback'), 'No-op invented a new readback');
    assert(a.$('effect-action').textContent === action && a.$('apply').disabled, 'No-op claimed pattern cancellation or remained dirty');
  }, { pageLocation: personalPage, health: noAuthHealth });

  await test('Pending target is compared with newer accepted SSE instead of an old write response', async a => {
    await a.attach(); a.$('live').checked = true;
    const first = a.hold('/v1/state', 'PATCH');
    a.input('brightness', 7); await wait(() => first.started, 'first write');
    a.input('cct', 5100); a.emit(snapshot(7, 2, { cct: 5100 }));
    await wait(() => a.$('actual-cct').textContent === '5100 K', 'newer SSE');
    first.resolve(snapshot(7, 1)); await wait(() => !a.$('refresh').disabled && a.$('apply').disabled, 'latest target confirmed');
    assert(a.requests.filter(r => r.method === 'PATCH').length === 1, 'Dispatch ignored the newer accepted state');
    assert(a.$('actual-cct').textContent === '5100 K', 'Old response rolled back readback');
  });
  await test('All presets only edit configuration until Start and leave verified output unchanged', async a => {
    await a.attach();
    const expected = ['candle', 'fireplace', 'storm', 'heartbeat', 'sunrise15', 'sunset', 'emergency', 'paparazzi', 'television', 'sos', 'keyframes'];
    const picker = a.$('effect-preset');
    assert(expected.every(id => [...picker.options].some(option => option.value === id)), 'Missing preset');
    for (const id of expected) {
      picker.value = id; picker.dispatchEvent(new a.w.Event('change'));
      assert(a.$('effect-action').textContent.includes('selected'), 'Preset selection feedback missing');
      assert(a.$('actual-brightness').textContent === '5%' && a.$('actual-cct').textContent === '5000 K', 'Preset changed actual output display');
    }
    assert(!a.requests.some(r => r.method === 'POST' || r.method === 'PATCH'), 'Preset selection wrote to the light');
  });
  await test('Every pattern can submit its own validated fields without stale parameters', async a => {
    await a.attach();
    const patterns = ['blink', 'breathe', 'fade-in', 'fade-out', 'cct-sweep', 'pulse', 'flicker', 'sunrise', 'lightning', 'emergency', 'paparazzi', 'tv', 'morse', 'keyframes'];
    for (const pattern of patterns) {
      a.$('effect-pattern').value = pattern; a.$('effect-pattern').dispatchEvent(new a.w.Event('change'));
      const before = a.requests.filter(r => r.path === '/v1/effects/start').length;
      a.click('effect-start');
      await wait(() => a.requests.filter(r => r.path === '/v1/effects/start').length === before + 1 && !a.$('effect-start').disabled, `${pattern} accepted`);
      const config = JSON.parse(a.requests.filter(r => r.path === '/v1/effects/start').at(-1).body);
      assert(config.pattern === pattern, 'Pattern choice was lost');
      if (!['blink', 'breathe', 'cct-sweep'].includes(pattern)) assert(!('period' in config), 'Irrelevant cycle period submitted');
      if (pattern !== 'pulse') assert(!('bpm' in config), 'Pulse fields leaked into another pattern');
      if (pattern !== 'morse') assert(!('text' in config), 'Morse fields leaked into another pattern');
    }
  });
  await test('Fast blink sends a full-cycle period and duty, and rejects impossible phases before HTTP', async a => {
    await a.attach(); a.input('effect-period', .2); a.click('effect-start');
    await wait(() => a.requests.some(r => r.path === '/v1/effects/start') && !a.$('effect-start').disabled, 'fast blink');
    const config = JSON.parse(a.requests.find(r => r.path === '/v1/effects/start').body);
    assert(config.period === .2 && config.duty === .5, 'Fast blink timing differs');
    a.input('effect-param-duty', .25); a.click('effect-start');
    await wait(() => a.$('effect-action').textContent.includes('start failed'), 'invalid phase rejected');
    assert(a.requests.filter(r => r.path === '/v1/effects/start').length === 1, 'Impossible blink phase reached bridge');
  });
  await test('Keyframe JSON errors do not dispatch and valid programs retain easing and loop', async a => {
    await a.attach(); a.$('effect-pattern').value = 'keyframes'; a.$('effect-pattern').dispatchEvent(new a.w.Event('change'));
    a.$('effect-keyframes').value = '{broken'; a.click('effect-start');
    assert(a.$('effect-error').textContent.includes('Invalid program'), 'Malformed JSON error missing');
    assert(!a.requests.some(r => r.path === '/v1/effects/start'), 'Malformed JSON was submitted');
    a.$('effect-keyframes').value = '[{"at":0,"brightness":2},{"at":2,"brightness":12,"ease":"smooth"}]';
    a.$('effect-param-loop').checked = false; a.$('effect-param-loop').dispatchEvent(new a.w.Event('change'));
    a.click('effect-start'); await wait(() => a.requests.some(r => r.path === '/v1/effects/start'), 'keyframes accepted');
    const config = JSON.parse(a.requests.find(r => r.path === '/v1/effects/start').body);
    assert(config.loop === false && config.keyframes[1].ease === 'smooth', 'Keyframe program changed in submission');
    assert(a.$('effect-until-stopped').disabled, 'Non-looping program allowed unbounded duration');
  });
  await test('Sunrise preset holds its endpoint and exposes perceptual curve without writing', async a => {
    await a.attach(); a.$('effect-preset').value = 'sunrise15'; a.$('effect-preset').dispatchEvent(new a.w.Event('change'));
    assert(a.$('effect-duration').value === '900' && a.$('effect-curve').value === 'perceptual', 'Sunrise defaults differ');
    assert(a.$('effect-cct').value === '2700' && a.$('effect-cct-end').value === '6500', 'Sunrise temperature endpoints differ');
    assert(!a.$('effect-restore').checked && a.$('effect-until-stopped').disabled, 'Sunrise endpoint holding differs');
    assert(!a.requests.some(r => r.path === '/v1/effects/start'), 'Sunrise preset started immediately');
  });
  await test('Personal controller accepts verified hybrid USB/BLE readback with no setup', async a => {
    await wait(() => !a.$('brightness').disabled, 'hybrid personal attach');
    assert(a.$('actual-brightness').textContent === '5%' && a.$('connection-settings').hidden, 'Hybrid readback failed to attach');
    a.input('brightness', 9); await wait(() => a.$('actual-brightness').textContent === '9%', 'hybrid manual write');
  }, { pageLocation: personalPage, health: noAuthHealth, startupState: snapshot(5, 0, { transport: 'usb+ble' }) });
  await test('Unexpected personal device disconnect observes a later bridge autoconnection without a connect mutation', async a => {
    await wait(() => !a.$('brightness').disabled && a.streams.length > 0, 'personal attach');
    a.state = disconnected(); a.emit(disconnected());
    await wait(() => a.$('brightness').disabled && a.$('actual-brightness').textContent === '—', 'authoritative disconnect');
    a.state = snapshot(8, 3, { transport: 'usb+ble' });
    await sleep(2050);
    await wait(() => !a.$('brightness').disabled && a.$('actual-brightness').textContent === '8%', 'bridge autoconnection observed');
    assert(!a.requests.some(r => r.path === '/v1/connect' || r.path === '/v1/devices' || r.method === 'PATCH'), 'Observation submitted a device mutation');
    assert(a.$('connection-settings').hidden, 'Recovery exposed setup');
  }, { pageLocation: personalPage, health: noAuthHealth });
  await test('Explicit personal disconnect does not start a device observation loop', async a => {
    await wait(() => !a.$('brightness').disabled, 'personal attach');
    a.click('disconnect'); await wait(() => a.$('status').textContent === 'Light disconnected.', 'disconnect finished');
    const count = a.requests.length; await sleep(2100);
    assert(a.requests.length === count && a.$('brightness').disabled, 'Explicit disconnect restarted observation');
  }, { pageLocation: personalPage, health: noAuthHealth });
  await test('Late device-observation read cannot replace a new session after page restoration', async a => {
    await wait(() => !a.$('brightness').disabled && a.streams.length > 0, 'personal attach');
    a.state = disconnected(); a.emit(disconnected());
    const old = a.hold(); await sleep(2050); await wait(() => old.started, 'held observation read');
    a.hide(); a.state = snapshot(6, 1); a.show();
    await wait(() => !a.$('brightness').disabled, 'fresh restored session');
    old.resolve(snapshot(99, 50)); await sleep(30);
    assert(a.$('actual-brightness').textContent === '6%', 'Late observation replaced fresh readback');
  }, { pageLocation: personalPage, health: noAuthHealth });
  return { browser: navigator.userAgent, lifecycle: 'Synthetic PageTransitionEvent persisted=true, real JS heap and browser APIs; no claim of actual BFcache eligibility',
    network: 'Every fetch and Bluetooth chooser replaced before evaluating app; dummy token only',
    passed: results.filter(r => r.passed).length, failed: results.filter(r => !r.passed).length, results };
};
