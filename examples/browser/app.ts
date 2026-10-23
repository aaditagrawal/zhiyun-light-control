import { BridgeClient, EFFECT_PRESETS, LightController, WebBluetoothTransport, disconnectedState, parseEffectConfig } from '../../packages/sdk/src';
import type { Device, EffectConfig, EffectPattern, EffectStatus, State, StatePatch, Unsubscribe } from '../../packages/sdk/src';
const el = <T extends HTMLElement>(id: string) => {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing UI element ${id}`);
  return node as T;
};
const brightness = el<HTMLInputElement>('brightness'); const cct = el<HTMLInputElement>('cct');
const apply = el<HTMLButtonElement>('apply'); const power = el<HTMLButtonElement>('power');
const refresh = el<HTMLButtonElement>('refresh'); const disconnect = el<HTMLButtonElement>('disconnect');
const auto = el<HTMLInputElement>('live'); const token = el<HTMLInputElement>('token');
const url = el<HTMLInputElement>('bridge-url'); const remember = el<HTMLInputElement>('remember');
const direct = el<HTMLButtonElement>('direct');
const effectSettings = el<HTMLFieldSetElement>('effect-settings');
const effectPattern = el<HTMLSelectElement>('effect-pattern');
const effectDuration = el<HTMLInputElement>('effect-duration');
const effectPeriod = el<HTMLInputElement>('effect-period');
const effectUntil = el<HTMLInputElement>('effect-until-stopped');
const effectRestore = el<HTMLInputElement>('effect-restore');
const effectEnd = el<HTMLInputElement>('effect-cct-end');
const effectStart = el<HTMLButtonElement>('effect-start');
const effectStop = el<HTMLButtonElement>('effect-stop');
const reconnect = el<HTMLButtonElement>('reconnect-light');
const connectionSettings = el('connection-settings');
const reconnectActions = el('reconnect-actions');
const connectionButtons = ['attach', 'scan', 'connect-device', 'direct'].map(id => el<HTMLButtonElement>(id));
const storageKey = 'lighttime.browser.session';
let client: BridgeClient | LightController | null = null;
let bridge: BridgeClient | null = null;
let state = disconnectedState(); let epoch = 0;
let acceptedAt: number | null = null;
let busy = false; let writing = false; let dirty = false;
let revision = 0; let pending: { patch: StatePatch; revision: number } | null = null;
let timer: ReturnType<typeof setTimeout> | undefined;
let operations = new AbortController(); let stream: AbortController | null = null;
let unsubscribe: Unsubscribe | null = null; let devices: Device[] = [];
let effectPoll: AbortController | null = null;
let effectTimer: ReturnType<typeof setTimeout> | undefined;
let effectRevision = 0;
let automatic = false;
let defaultAddress: string | null = null;
let pageActive = true;
let healthProbe: AbortController | null = null;
let discoveryNeeded = false;
let eventsLost = false; let eventsRecovering = false;
let lightDisconnected = false;
let deviceWatch: { abort: AbortController; probe: BridgeClient; timer: ReturnType<typeof setTimeout> | undefined } | null = null;
const recoveryDelays = [500, 1500, 3000];
const servedByBridge = /^\/ui(?:\/|$)/.test(location.pathname) && ['http:', 'https:'].includes(location.protocol);
const patternNames: Record<string, string> = { blink: 'Blink', breathe: 'Breathe', 'fade-in': 'Fade in', 'fade-out': 'Fade out', 'cct-sweep': 'CCT sweep', pulse: 'Heartbeat', flicker: 'Flicker', sunrise: 'Sunrise / sunset', lightning: 'Lightning', emergency: 'Alternating white', paparazzi: 'Camera flashes', tv: 'Television', morse: 'Morse', keyframes: 'Keyframes' };
type Parameter = { key: string; label: string; value: string | number | boolean; min?: number; max?: number; step?: string; options?: string[] };
function parameters(pattern: string, values: Record<string, unknown> = {}): Parameter[] {
  const fire = values.style === 'fire';
  const schema: Record<string, Parameter[]> = {
    blink: [{ key: 'duty', label: 'On fraction (0.5 = half)', value: .5, min: .05, max: .95, step: '.05' }],
    pulse: [{ key: 'style', label: 'Pulse shape', value: 'heartbeat', options: ['heartbeat', 'beat'] }, { key: 'bpm', label: 'Tempo (BPM)', value: 60, min: 20, max: 180 }, { key: 'accentEvery', label: 'Accent every N beats (0 = none)', value: 0, min: 0, max: 16 }, { key: 'accentRatio', label: 'Unaccented strength', value: .6, min: .2, max: 1, step: '.05' }, { key: 'decay', label: 'Beat decay fraction', value: .3, min: .05, max: 1, step: '.05' }],
    flicker: [{ key: 'style', label: 'Flicker character', value: 'candle', options: ['candle', 'fire'] }, { key: 'depth', label: 'Flicker depth', value: fire ? .55 : .25, min: 0, max: .6, step: '.05' }, { key: 'speed', label: 'Flicker speed', value: 1, min: .25, max: 2, step: '.05' }, { key: 'gusts', label: 'Gust strength', value: fire ? .5 : .15, min: 0, max: 1, step: '.05' }, { key: 'cctCoupling', label: 'Temperature variation', value: fire ? .5 : 0, min: 0, max: 1, step: '.05' }],
    sunrise: [{ key: 'direction', label: 'Direction', value: 'rise', options: ['rise', 'set'] }],
    lightning: [{ key: 'interval', label: 'Average strike gap (seconds)', value: 6, min: 2, max: 60, step: '.1' }, { key: 'flashesMax', label: 'Maximum flashes per strike', value: 3, min: 1, max: 4 }],
    emergency: [{ key: 'flashes', label: 'Flashes per side', value: 2, min: 1, max: 4 }, { key: 'flashOn', label: 'Flash on (seconds)', value: .15, min: .1, max: 10, step: '.05' }, { key: 'flashOff', label: 'Flash off (seconds)', value: .15, min: .1, max: 10, step: '.05' }, { key: 'gap', label: 'Gap between sides (seconds)', value: .3, min: .2, max: 30, step: '.1' }],
    paparazzi: [{ key: 'interval', label: 'Average flash gap (seconds)', value: .8, min: .33, max: 5, step: '.01' }, { key: 'burst', label: 'Double-flash probability', value: .25, min: 0, max: 1, step: '.05' }],
    tv: [{ key: 'interval', label: 'Average scene length (seconds)', value: 2.5, min: .5, max: 10, step: '.1' }, { key: 'motion', label: 'Screen movement', value: .3, min: 0, max: 1, step: '.05' }],
    morse: [{ key: 'text', label: 'Morse message', value: 'SOS' }, { key: 'unit', label: 'Dot length (seconds)', value: .2, min: .1, max: 2, step: '.05' }, { key: 'loop', label: 'Repeat message', value: true }],
    keyframes: [{ key: 'loop', label: 'Repeat program', value: true }],
  };
  return (schema[pattern] ?? []).map(item => ({ ...item, value: values[item.key] as Parameter['value'] ?? item.value }));
}
function renderParameters(values: Record<string, unknown> = {}) {
  const container = el('effect-parameters'); container.replaceChildren();
  for (const parameter of parameters(effectPattern.value, values)) {
    const label = document.createElement('label'); label.textContent = parameter.label;
    label.htmlFor = 'effect-param-' + parameter.key;
    const input = parameter.options ? document.createElement('select') : document.createElement('input');
    input.id = label.htmlFor;
    if (input instanceof HTMLSelectElement) {
      for (const option of parameter.options!) input.add(new Option(option, option));
      input.value = String(parameter.value);
    } else {
      input.type = typeof parameter.value === 'boolean' ? 'checkbox' : typeof parameter.value === 'number' ? 'number' : 'text';
      if (input.type === 'checkbox') { input.checked = Boolean(parameter.value); label.className = 'checkbox effect-wide'; }
      else { input.value = String(parameter.value); input.required = true; }
      if (parameter.min !== undefined) input.min = String(parameter.min);
      if (parameter.max !== undefined) input.max = String(parameter.max);
      input.step = parameter.step ?? '1';
      if (parameter.key === 'text') input.maxLength = 64;
    }
    input.addEventListener('change', patternFields); label.append(input); container.append(label);
  }
  el('effect-keyframes-field').hidden = effectPattern.value !== 'keyframes';
}
function readParameters(): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const parameter of parameters(effectPattern.value)) {
    const input = el<HTMLInputElement | HTMLSelectElement>('effect-param-' + parameter.key);
    result[parameter.key] = input instanceof HTMLInputElement && input.type === 'checkbox' ? input.checked
      : input instanceof HTMLInputElement && input.type === 'number' ? input.valueAsNumber : input.value;
  }
  if (effectPattern.value === 'keyframes') result.keyframes = JSON.parse(el<HTMLTextAreaElement>('effect-keyframes').value);
  const seed = el<HTMLInputElement>('effect-seed').valueAsNumber;
  if (seed !== 1) result.seed = seed;
  const curve = el<HTMLSelectElement>('effect-curve').value;
  if (curve !== (effectPattern.value === 'sunrise' ? 'perceptual' : 'linear')) result.curve = curve;
  return result;
}
function patternFields() {
  const kind = effectPattern.value;
  const fade = kind === 'fade-in' || kind === 'fade-out' || kind === 'sunrise'
    || (['morse', 'keyframes'].includes(kind) && !el<HTMLInputElement>('effect-param-loop').checked);
  if (fade) effectUntil.checked = false;
  effectUntil.disabled = fade;
  effectDuration.disabled = effectUntil.checked;
  effectPeriod.disabled = fade || !['blink', 'breathe', 'cct-sweep'].includes(kind);
  effectPeriod.min = kind === 'blink' ? '.2' : '1';
  effectEnd.disabled = !['cct-sweep', 'flicker', 'sunrise', 'emergency', 'tv'].includes(kind);
}
function renderEffect(actual: EffectStatus) {
  const name = actual.pattern ? patternNames[actual.pattern] : 'Pattern';
  el('effect-status').textContent = actual.running ? `${name} running${actual.endsAt ? '' : ' · until stopped'}`
    : actual.lastError ? 'Pattern stopped after an error.' : actual.pattern ? `${name} stopped.` : 'No pattern running.';
  const progress = el<HTMLProgressElement>('effect-progress');
  progress.hidden = actual.progress === null;
  progress.value = actual.progress ?? 0;
  if (actual.running && actual.progress !== null) el('effect-status').textContent += ` · ${Math.round(actual.progress * 100)}%`;
  el('effect-observed').textContent = `Runner checked ${new Date().toLocaleTimeString()}${actual.endsAt ? ` · ends ${new Date(actual.endsAt).toLocaleTimeString()}` : ''}`;
  el('effect-error').textContent = actual.lastError ? `Runner failed: ${actual.lastError}` : '';
}
function stopEffectPolling() {
  clearTimeout(effectTimer); effectTimer = undefined;
  effectPoll?.abort(); effectPoll = null; effectRevision++;
}
function startEffectPolling(active: BridgeClient) {
  stopEffectPolling();
  const abort = new AbortController(); effectPoll = abort;
  const currentEpoch = epoch;
  const current = () => !abort.signal.aborted && epoch === currentEpoch && bridge === active;
  const poll = async () => {
    if (!current()) return;
    const version = effectRevision;
    try {
      if (!busy && !writing) {
        const actual = await active.effect({ signal: abort.signal });
        if (current() && version === effectRevision) renderEffect(actual);
      }
    } catch (error) {
      if (current() && version === effectRevision) {
        el('effect-error').textContent = `Runner status unavailable: ${errorText(error)}. Last observed status may be stale.`;
      }
    } finally { if (current()) effectTimer = setTimeout(() => void poll(), 1000); }
  };
  // The attach request owns the busy state until its finalizer runs.
  effectTimer = setTimeout(() => void poll(), 0);
}
function status(message: string, kind: 'idle' | 'connected' | 'busy' | 'error' = 'idle') {
  el('status').textContent = message; el('indicator').dataset.state = kind;
}
function errorText(error: unknown): string { return error instanceof Error ? error.message : 'Unknown connection error'; }
function showError(error: unknown) { status(errorText(error), 'error'); }
function draft() {
  el('brightness-value').replaceChildren(document.createTextNode(Number(brightness.value).toLocaleString(undefined, { maximumFractionDigits: 1 })), Object.assign(document.createElement('span'), { textContent: '%' }));
  el('cct-value').replaceChildren(document.createTextNode(cct.value), Object.assign(document.createElement('span'), { textContent: 'K' }));
}
function controls() {
  const connected = Boolean(client && state.connected && !busy);
  brightness.disabled = !connected; cct.disabled = !connected;
  apply.disabled = !connected || !dirty; power.disabled = !connected || writing;
  refresh.disabled = !connected || writing; disconnect.disabled = !client || busy;
  const retryHttp = discoveryNeeded || eventsLost;
  reconnect.disabled = busy || healthProbe !== null || (eventsLost && eventsRecovering);
  reconnectActions.hidden = !retryHttp && (!automatic || state.connected);
  reconnect.textContent = !retryHttp && lightDisconnected && defaultAddress ? 'Reconnect light' : 'Retry connection';
  const patternsAvailable = connected && bridge !== null;
  effectSettings.disabled = !patternsAvailable;
  effectStart.disabled = !patternsAvailable || writing;
  effectStop.disabled = !patternsAvailable || writing;
  el('patterns-connection').textContent = bridge && state.connected
    ? 'Patterns change the light over time. Current output appears in readback above.'
    : automatic || discoveryNeeded ? `Waiting for the light’s bridge connection. Use ${reconnect.textContent} if it is unavailable.`
    : 'Attach through the bridge to run patterns. Direct Bluetooth supports manual controls.';
  patternFields();
  power.textContent = state.power ? 'Turn output off' : 'Restore output';
  for (const button of connectionButtons) button.disabled = busy || (button === direct && !WebBluetoothTransport.available());
}
function render(actual: State): boolean {
  // JSON and SSE have no shared delivery order. Only complete, dated readback
  // can establish a connection; equal timestamps may only repeat the same state.
  if (actual.connected) {
    const at = actual.updatedAt ? Date.parse(actual.updatedAt) : NaN;
    if (!Number.isFinite(at) || actual.brightness === null || actual.cct === null || actual.power === null) return false;
    if (acceptedAt !== null && (at < acceptedAt || (at === acceptedAt &&
        (actual.brightness !== state.brightness || actual.cct !== state.cct || actual.power !== state.power ||
         actual.address !== state.address || actual.transport !== state.transport ||
         actual.device?.id !== state.device?.id || actual.device?.serial !== state.device?.serial ||
         actual.device?.model !== state.device?.model)))) return false;
    acceptedAt = at;
    lightDisconnected = false;
  }
  state = actual; paint(); return true;
}
function paint() {
  const actual = state;
  el('actual-brightness').textContent = actual.brightness === null ? '—' : `${actual.brightness.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
  el('actual-cct').textContent = actual.cct === null ? '—' : `${actual.cct} K`;
  el('actual-power').textContent = actual.power === null ? '—' : actual.power ? 'On' : 'Off';
  el('last-read').textContent = actual.updatedAt ? `Read ${new Date(actual.updatedAt).toLocaleTimeString()}` : 'No verified state yet.';
  if (actual.connected && !dirty && !writing) {
    if (actual.brightness !== null) brightness.value = String(actual.brightness);
    if (actual.cct !== null) cct.value = String(actual.cct);
    draft();
    el('write-status').textContent = 'Actual settings read back from the light.';
  }
  controls();
}
function persistToken() {
  try {
    if (remember.checked) sessionStorage.setItem(storageKey, JSON.stringify({ token: token.value, url: url.value }));
    else sessionStorage.removeItem(storageKey);
  } catch { el('events-status').textContent = 'Session storage is unavailable. Token remains in memory only.'; remember.checked = false; }
}
// A bridge-served /ui/ follows its origin, including Tailscale/MagicDNS and any
// port. A separately hosted page defaults to that host's bridge, never a remote
// visitor's loopback. Offline/file previews can enter a bridge URL explicitly.
if (location.protocol === 'http:' || location.protocol === 'https:') {
  const endpoint = new URL(location.origin);
  if (!servedByBridge && endpoint.port !== '8877') endpoint.port = '8877';
  url.value = endpoint.origin;
}
try {
  const saved = sessionStorage.getItem(storageKey);
  if (saved) {
    const value: unknown = JSON.parse(saved);
    if (value && typeof value === 'object' && 'token' in value && 'url' in value && typeof value.token === 'string' && typeof value.url === 'string') {
      token.value = value.token; url.value = value.url; remember.checked = true;
    }
  }
} catch { /* private browsing may disallow session storage */ }
remember.addEventListener('change', persistToken); token.addEventListener('input', persistToken); url.addEventListener('input', persistToken);
async function release(disconnectBridge = false) {
  stopDeviceWatch();
  epoch++; clearTimeout(timer); pending = null; dirty = false; revision++;
  healthProbe?.abort(); healthProbe = null;
  eventsLost = false; eventsRecovering = false; lightDisconnected = false;
  stopEffectPolling();
  timer = undefined; acceptedAt = null;
  devices = []; el('device-picker').hidden = true;
  stream?.abort(); stream = null; operations.abort(); operations = new AbortController();
  unsubscribe?.(); unsubscribe = null;
  const previous = client; client = null; bridge = null; writing = false; busy = false;
  // Everything visible and every session reference is invalidated before any
  // native cleanup can yield. Cleanup must never touch a later session's DOM.
  render(disconnectedState()); el('events-status').textContent = ''; el('write-status').textContent = 'Changes are successful only after the light reads them back.';
  el('effect-status').textContent = 'Runner status unavailable. Attach through the bridge.';
  el('effect-observed').textContent = 'No runner status received.';
  el<HTMLProgressElement>('effect-progress').hidden = true;
  el('effect-error').textContent = '';
  el('effect-action').textContent = automatic ? 'Patterns keep running when you close this page. Reopen it to check their status.'
    : 'Patterns continue on the bridge when this page closes. Attach again to check the runner.';
  if (previous instanceof BridgeClient) {
    try { if (disconnectBridge) await previous.disconnect(); }
    finally { previous.dispose(); }
  }
  if (previous instanceof LightController) await previous.disconnect();
}
type Current = () => boolean;
async function configuring(action: (current: Current, signal: AbortSignal) => Promise<void>) {
  if (busy) return;
  const currentEpoch = epoch; const current = () => epoch === currentEpoch;
  const signal = operations.signal;
  busy = true; controls();
  try { await action(current, signal); }
  catch (error) { if (current()) showError(error); }
  finally { if (current()) { busy = false; controls(); } }
}
function prepareBridge(action: (next: BridgeClient, current: Current, signal: AbortSignal) => Promise<void>) {
  if (busy) return;
  // Validate the new endpoint before releasing an existing session.
  try {
    const next = new BridgeClient({
      baseUrl: url.value.trim(), token: token.value.trim(), timeoutMs: 60_000,
    });
    void release().catch(() => {}); discoveryNeeded = false; automatic = false;
    persistToken(); client = next; bridge = next;
    void configuring((current, signal) => action(next, current, signal));
  } catch (error) { showError(error); }
}
function startEvents(active: BridgeClient) {
  stream?.abort(); const abort = new AbortController(); stream = abort;
  const currentEpoch = epoch;
  const current = () => pageActive && !abort.signal.aborted && epoch === currentEpoch && bridge === active && stream === abort;
  eventsLost = false; eventsRecovering = true;
  el('events-status').textContent = 'Listening for bridge state events…';
  void (async () => {
    let failures = 0;
    try {
      while (current()) {
        try {
          for await (const event of active.events({ signal: abort.signal })) {
            if (!current()) return;
            // An authoritative device disconnect ends the session. HTTP stream
            // repair below never scans, reconnects BLE, or replays draft writes.
            if (!event.connected) {
              void release().catch(() => {}); lightDisconnected = true; controls();
              status('Bridge reports the light disconnected. Attach again to read the light.', 'error');
              if (automatic) watchBridgeDevice();
              return;
            }
            if (!render(event)) continue;
            failures = 0; eventsLost = false; controls();
            el('events-status').textContent = 'Bridge state events active.';
            if (!writing && !busy) status('Bridge connected · actual readback received.', 'connected');
          }
        } catch (error) {
          if (!current()) return;
          eventsLost = true;
          const delay = recoveryDelays[failures++];
          el('events-status').textContent = delay === undefined
            ? `Live events stopped: ${errorText(error)}. Use Retry connection or Read back.`
            : `Live events interrupted: ${errorText(error)}. Retrying (${failures}/${recoveryDelays.length})…`;
          if (!writing && !busy) status('Bridge events lost. Displayed readback may be stale.', 'error');
          controls();
          if (delay === undefined) return;
          await waitForRetry(abort.signal, delay);
        }
      }
    } finally {
      if (current()) { eventsRecovering = false; controls(); }
    }
  })();
}
function waitForRetry(signal: AbortSignal, delay = 1000): Promise<void> {
  return new Promise(resolve => {
    const finish = () => { clearTimeout(wait); signal.removeEventListener('abort', finish); resolve(); };
    const wait = setTimeout(finish, delay);
    signal.addEventListener('abort', finish, { once: true });
    if (signal.aborted) finish();
  });
}
function stopDeviceWatch() {
  if (!deviceWatch) return;
  deviceWatch.abort.abort(); clearTimeout(deviceWatch.timer); deviceWatch.probe.dispose(); deviceWatch = null;
}
function watchBridgeDevice() {
  stopDeviceWatch();
  if (!automatic || !pageActive) return;
  const currentEpoch = epoch;
  const watcher = { abort: new AbortController(), probe: new BridgeClient({ baseUrl: location.origin, timeoutMs: 3000 }), timer: undefined as ReturnType<typeof setTimeout> | undefined };
  deviceWatch = watcher;
  const until = Date.now() + 60_000;
  const current = () => pageActive && automatic && epoch === currentEpoch && deviceWatch === watcher && !watcher.abort.signal.aborted;
  const poll = async () => {
    if (!current()) return;
    try {
      const actual = await watcher.probe.refresh({ signal: watcher.abort.signal });
      if (!current()) return;
      if (actual.connected) { stopDeviceWatch(); attachAutomatically(); return; }
    } catch { /* Device absence is already displayed; no settings are replayed. */ }
    if (!current()) return;
    if (Date.now() >= until) { stopDeviceWatch(); return; }
    watcher.timer = setTimeout(() => void poll(), 2000);
  };
  // Observe a bridge that may still be autoconnecting after restart. These
  // HTTP reads do not submit a connect mutation or replay a requested setting.
  watcher.timer = setTimeout(() => void poll(), 2000);
}
function attachAutomatically(connectLight = false) {
  if (busy || !automatic) return;
  const next = new BridgeClient({ baseUrl: location.origin, timeoutMs: 60_000 });
  void release().catch(() => {}); client = next; bridge = next;
  lightDisconnected = connectLight;
  void configuring(async (current, signal) => {
    status(connectLight ? 'Reconnecting the light…' : 'Reading the light…', 'busy');
    try {
      if (connectLight && defaultAddress) {
        const actual = await next.connect({ transport: 'ble', address: defaultAddress }, { signal });
        if (!current()) return;
        if (actual.connected && render(actual)) {
          status('Light connected · actual readback received.', 'connected');
          startEvents(next); startEffectPolling(next); return;
        }
      }
      // Let a just-started bridge finish attaching without asking for setup.
      // Each read is bounded; the retry window is brief and never scans BLE.
      const until = Date.now() + 8000;
      let lastError: unknown;
      do {
        try {
          const actual = await next.refresh({ signal, timeoutMs: 3000 });
          if (!current()) return;
          if (actual.connected && render(actual)) {
            lightDisconnected = false;
            status('Light connected · actual readback received.', 'connected');
            startEvents(next); startEffectPolling(next); return;
          }
          if (!actual.connected) lightDisconnected = true;
        } catch (error) { if (!current()) return; lastError = error; }
        if (Date.now() >= until) break;
        await waitForRetry(signal);
      } while (current());
      if (!current()) return;
      status(lightDisconnected && defaultAddress ? 'Light is unavailable. Use Reconnect light to try again.'
        : `Light is unavailable. Retry when it is connected to the bridge.${lastError ? ` ${errorText(lastError)}` : ''}`, 'error');
      watchBridgeDevice();
    } catch (error) { if (current()) status(`Reconnect failed: ${errorText(error)}`, 'error'); }
  });
}
async function inspectLocalBridge(retry = false) {
  if (!servedByBridge || !discoveryNeeded || !pageActive || healthProbe) return;
  const currentEpoch = epoch;
  const abort = new AbortController(); healthProbe = abort;
  const current = () => pageActive && !abort.signal.aborted && epoch === currentEpoch && healthProbe === abort;
  const probe = new BridgeClient({ baseUrl: location.origin, timeoutMs: 3000 });
  connectionSettings.hidden = true;
  status('Connecting to the light…', 'busy');
  controls();
  // Hide setup while detecting the personal controller. Secured bridges return
  // to the ordinary editable connection UI, with the remembered session intact.
  try {
    for (let attempt = 0; current(); attempt++) {
      try {
        const health = await probe.health({ signal: abort.signal });
        if (!current()) return;
        discoveryNeeded = false;
        if (health.authRequired === false) {
          automatic = true; defaultAddress = health.defaultAddress ?? null;
          auto.checked = true;
          connectionSettings.hidden = true;
          attachAutomatically();
        } else {
          connectionSettings.hidden = false;
          status('Disconnected. Choose a connection below.');
        }
        return;
      } catch (error) {
        if (!current()) return;
        status(`Bridge startup check failed: ${errorText(error)}. Use Retry connection.`, 'error');
        const delay = retry ? recoveryDelays[attempt] : undefined;
        if (delay === undefined) return;
        await waitForRetry(abort.signal, delay);
      }
    }
  } finally {
    probe.dispose();
    if (healthProbe === abort) { healthProbe = null; controls(); }
  }
}
function recoverConnection() {
  if (!pageActive || busy) return;
  if (discoveryNeeded) void inspectLocalBridge(true);
  else if (bridge && state.connected && eventsLost && !eventsRecovering) startEvents(bridge);
}
reconnect.addEventListener('click', () => {
  if (discoveryNeeded) void inspectLocalBridge();
  else if (bridge && state.connected && eventsLost) startEvents(bridge);
  else attachAutomatically(lightDisconnected);
});
window.addEventListener('online', recoverConnection);
document.addEventListener('visibilitychange', () => { if (!document.hidden) recoverConnection(); });
el('attach').addEventListener('click', () => prepareBridge(async (next, current, signal) => {
  status('Reading the bridge connection…', 'busy');
  const result = await next.refresh({ signal });
  if (!current()) return;
  if (!render(result)) throw new Error('No newer verified readback received. Use Read back to retry.');
  status(result.connected ? 'Bridge connected · actual readback received.' : 'Bridge is available; connect a light below.', result.connected ? 'connected' : 'idle');
  startEvents(next);
  startEffectPolling(next);
}));
el('scan').addEventListener('click', () => prepareBridge(async (next, current, signal) => {
  status('Finding lights via the bridge…', 'busy');
  const found = await next.devices(5, { signal });
  if (!current()) return;
  devices = found.filter(device => device.transport === 'ble');
  const identityOnly = found.length - devices.length;
  const select = el<HTMLSelectElement>('devices'); select.replaceChildren();
  for (const [index, device] of devices.entries()) select.add(new Option(`${device.name || 'Unnamed'} · ${device.transport} · ${device.address}`, String(index)));
  el('device-picker').hidden = devices.length === 0;
  status(devices.length ? `Select a BLE light and connect.${identityOnly ? ' Serial identity-only devices are hidden.' : ''}`
    : identityOnly ? 'Only serial identity devices found. G60 output control requires BLE.' : 'No lights found. Check bridge permissions and try again.');
}));
el('connect-device').addEventListener('click', () => {
  if (busy) return;
  const device = devices[Number(el<HTMLSelectElement>('devices').value)];
  if (!device || !bridge) { showError(new Error('Find lights first.')); return; }
  // Connecting a selected light starts a fresh readback epoch too.
  prepareBridge(async (next, current, signal) => {
    status('Connecting through the bridge…', 'busy');
    const result = await next.connect({ transport: device.transport, address: device.address }, { signal });
    if (!current()) return;
    if (!render(result)) throw new Error('No newer verified readback received. Use Read back to retry.');
    status(result.connected ? 'Bridge connected · actual readback received.' : 'Bridge did not connect the light.', result.connected ? 'connected' : 'error');
    startEvents(next);
    startEffectPolling(next);
  });
});
direct.addEventListener('click', () => {
  if (busy) return;
  // requestDevice must execute synchronously in this user gesture, before release/other awaits.
  const selection = WebBluetoothTransport.request();
  void release().catch(() => {}); discoveryNeeded = false; automatic = false;
  void configuring(async (current, signal) => {
    status('Choose a PL103 / G60 in the Bluetooth dialog…', 'busy');
    const transport = await selection;
    if (!current()) { await transport.disconnect().catch(() => {}); return; }
    const next = new LightController(transport); client = next;
    unsubscribe = next.subscribe(actual => {
      if (!current()) return;
      if (!actual.connected) {
        void release().catch(() => {});
        status('Bluetooth disconnected. Choose the light again to reconnect.', 'error');
      } else render(actual);
    });
    const actual = await next.connect({ signal });
    if (!current()) return;
    if (!render(actual)) throw new Error('No verified Bluetooth readback received. Choose the light again.');
    status('Bluetooth connected · actual readback received.', 'connected');
    el('events-status').textContent = 'Direct BLE · use Read back for changes made on the light.';
  });
});
function schedule() {
  clearTimeout(timer);
  // Dispatch the first value immediately. pump() keeps one verified write in
  // flight and replaces its single pending draft with the newest slider value.
  // A trailing debounce otherwise leaves a continuously moving slider silent.
  if (auto.checked && dirty) queueDraft();
}
for (const input of [brightness, cct]) input.addEventListener('input', () => {
  dirty = true; revision++; draft(); controls();
  el('write-status').textContent = 'Requested settings changed. Awaiting verified apply.'; schedule();
});
auto.addEventListener('change', schedule);
function queueDraft() {
  clearTimeout(timer);
  if (busy || !client || !state.connected || !dirty) return;
  pending = { patch: { brightness: Number(brightness.value), cct: Number(cct.value) }, revision };
  void pump();
}
apply.addEventListener('click', queueDraft);
async function pump() {
  if (writing || !client) return;
  const currentEpoch = epoch; const active = client; const signal = operations.signal;
  effectRevision++;
  writing = true; controls();
  try {
    while (pending && epoch === currentEpoch) {
      const target = pending; pending = null;
      // Keep the full desired target while queued. Compare only at dispatch,
      // after an earlier write/SSE may have changed the accepted state. In
      // particular, a pending reversal must undo the just-completed write.
      const patch: StatePatch = target.patch.power !== undefined ? target.patch : {
        ...(target.patch.brightness !== undefined && target.patch.brightness !== state.brightness ? { brightness: target.patch.brightness } : {}),
        ...(target.patch.cct !== undefined && target.patch.cct !== state.cct ? { cct: target.patch.cct } : {}),
      };
      const unchanged = Object.keys(patch).length === 0;
      if (!unchanged) {
        if (bridge) el('effect-action').textContent = 'Manual output cancels any bridge pattern before applying. Waiting for verified readback and the next runner check.';
        el('write-status').textContent = 'Applying settings and checking actual readback…';
        const actual = await active.setState(patch, { signal });
        if (epoch !== currentEpoch) return;
        if (!actual.connected) {
          void release().catch(() => {}); lightDisconnected = true; controls();
          status('The light disconnected before verification. Attach again to read the light.', 'error');
          return;
        }
        render(actual);
      }
      const matches = state.connected &&
        (target.patch.brightness === undefined || (state.brightness !== null && Math.abs(state.brightness - target.patch.brightness) <= 0.01)) &&
        (target.patch.cct === undefined || state.cct === target.patch.cct) &&
        (target.patch.power === undefined || state.power === target.patch.power);
      if (target.revision === revision) dirty = !matches;
      status(unchanged ? 'Requested settings match the last verified readback.'
        : matches ? 'Settings verified by device readback.' : 'Readback differs from the requested settings.', matches ? 'connected' : 'error');
      el('write-status').textContent = target.revision !== revision ? 'Newer requested settings are pending.'
        : unchanged ? 'Requested settings match the last verified readback. No write needed.'
        : matches ? 'Settings match actual device readback.' : 'Actual readback differs. Apply again to retry the requested settings.';
    }
  } catch (error) {
    if (epoch === currentEpoch) { pending = null; showError(error); el('write-status').textContent = 'Apply failed. Read back the light before retrying.'; }
  } finally {
    if (epoch === currentEpoch) {
      writing = false;
      // Update sliders without replacing the outcome message from this write.
      const message = el('write-status').textContent; paint(); el('write-status').textContent = message;
    }
  }
}
power.addEventListener('click', () => {
  if (writing || !client || !state.connected) return;
  clearTimeout(timer); pending = null; dirty = false; revision++;
  pending = { patch: { power: !state.power }, revision }; void pump();
});
effectPattern.addEventListener('change', () => {
  el<HTMLSelectElement>('effect-preset').value = '';
  renderParameters();
  effectRestore.checked = !['fade-in', 'fade-out', 'sunrise'].includes(effectPattern.value);
  el<HTMLSelectElement>('effect-curve').value = effectPattern.value === 'sunrise' ? 'perceptual' : 'linear';
  if (effectPattern.value === 'sunrise') {
    effectDuration.value = '900'; el<HTMLInputElement>('effect-cct').value = '2700'; effectEnd.value = '6500';
  }
  patternFields();
});
const presetLabels: Record<string, string> = { candle: 'Candle', fireplace: 'Fireplace', storm: 'Thunderstorm', heartbeat: 'Heartbeat', sunrise15: 'Sunrise · 15 minutes', sunset: 'Sunset', emergency: 'Alternating white bursts', paparazzi: 'Camera flashes', television: 'Television glow', sos: 'Morse · SOS', keyframes: 'Keyframe demonstration' };
const presetPicker = el<HTMLSelectElement>('effect-preset');
for (const [id, config] of Object.entries(EFFECT_PRESETS)) {
  presetPicker.add(new Option(presetLabels[id] ?? patternNames[config.pattern] ?? id, id));
}
presetPicker.addEventListener('change', () => {
  const config = EFFECT_PRESETS[presetPicker.value];
  if (!config) return;
  const values = config as unknown as Record<string, unknown>;
  const defaults = parseEffectConfig(config);
  effectPattern.value = config.pattern;
  effectUntil.checked = config.duration === null;
  effectDuration.value = String(config.duration ?? (config.pattern === 'sunrise' ? 900 : 10));
  effectPeriod.value = String(config.period ?? 4);
  el<HTMLInputElement>('effect-min').value = String(config.minBrightness ?? 0);
  el<HTMLInputElement>('effect-max').value = String(config.maxBrightness ?? state.brightness ?? 5);
  el<HTMLInputElement>('effect-cct').value = String(defaults.cct ?? state.cct ?? 5000);
  effectEnd.value = String(defaults.cctEnd);
  el<HTMLInputElement>('effect-seed').value = String(values.seed ?? 1);
  el<HTMLSelectElement>('effect-curve').value = String(values.curve ?? (config.pattern === 'sunrise' ? 'perceptual' : 'linear'));
  effectRestore.checked = config.restoreOnComplete ?? !(['fade-in', 'fade-out', 'sunrise'].includes(config.pattern) || values.loop === false);
  renderParameters(values);
  if (values.keyframes) el<HTMLTextAreaElement>('effect-keyframes').value = JSON.stringify(values.keyframes, null, 2);
  patternFields();
  el('effect-action').textContent = `${presetLabels[presetPicker.value] ?? 'Preset'} selected. Tune its settings, then Start pattern.`;
});
effectUntil.addEventListener('change', patternFields);
el<HTMLFormElement>('effect-form').addEventListener('submit', event => {
  event.preventDefault();
  if (!bridge || !state.connected || busy || writing) return;
  const active = bridge;
  let additional: Record<string, unknown>;
  try { additional = readParameters(); }
  catch (error) { el('effect-error').textContent = `Invalid program: ${errorText(error)}`; return; }
  const config = {
    pattern: effectPattern.value as EffectPattern,
    duration: effectUntil.checked ? null : effectDuration.valueAsNumber,
    minBrightness: el<HTMLInputElement>('effect-min').valueAsNumber,
    maxBrightness: el<HTMLInputElement>('effect-max').valueAsNumber,
    cct: el<HTMLInputElement>('effect-cct').valueAsNumber,
    restoreOnComplete: effectRestore.checked,
    ...(effectPeriod.disabled ? {} : { period: effectPeriod.valueAsNumber }),
    ...(effectEnd.disabled ? {} : { cctEnd: effectEnd.valueAsNumber }),
    ...additional,
  } as EffectConfig;
  clearTimeout(timer); pending = null; effectRevision++;
  void configuring(async (current, signal) => {
    el('effect-action').textContent = 'Starting pattern and capturing verified baseline…';
    try {
      const actual = await active.startEffect(config, { signal });
      if (!current()) return;
      renderEffect(actual);
      el('effect-action').textContent = 'Pattern accepted by the bridge. It continues if this page closes. Stop restores its starting brightness and CCT.';
    } catch (error) {
      if (current()) el('effect-action').textContent = `Pattern start failed: ${errorText(error)}. Check the actual runner status below.`;
    }
  });
});
effectStop.addEventListener('click', () => {
  if (!bridge || !state.connected || busy || writing) return;
  const active = bridge;
  clearTimeout(timer); pending = null; effectRevision++;
  void configuring(async (current, signal) => {
    el('effect-action').textContent = 'Stopping pattern and verifying baseline restoration…';
    try {
      const actual = await active.stopEffect({ restore: true }, { signal });
      if (!current()) return;
      renderEffect(actual);
      el('effect-action').textContent = actual.lastError ? 'Pattern stopped; restoration reported an error. Check readback and runner failure.'
        : 'Pattern stopped. Any active pattern’s starting brightness and CCT have been restored and verified by the bridge.';
    } catch (error) {
      if (current()) el('effect-action').textContent = `Stop failed: ${errorText(error)}. Check the actual runner status and readback.`;
    }
  });
});
refresh.addEventListener('click', () => void configuring(async (current, signal) => {
  if (!client) return;
  status('Reading actual device settings…', 'busy');
  const actual = await client.refresh({ signal });
  if (!current()) return;
  if (!actual.connected) {
    void release().catch(() => {}); lightDisconnected = true; controls();
    status('Light is disconnected. Attach again to read the light.', 'error'); return;
  }
  if (render(actual)) status('Actual device readback received.', 'connected');
  else status('No newer verified readback received. Displaying the last verified state.', 'error');
}));
disconnect.addEventListener('click', () => {
  if (busy) return;
  const cleanup = release(true);
  void configuring(async current => {
    status('Disconnecting the light…', 'busy');
    await cleanup;
    if (current()) { lightDisconnected = true; status('Light disconnected.'); }
  });
});
window.addEventListener('pagehide', () => {
  // release() invalidates the visible connection synchronously before awaiting
  // native teardown, so a bfcache restoration cannot reuse a disposed client.
  pageActive = false;
  void release().catch(() => {});
  busy = false;
  status('Disconnected. Attach again to read the light.');
  controls();
});
window.addEventListener('pageshow', event => {
  pageActive = true;
  if (event.persisted) {
    void release().catch(() => {});
    if (automatic) attachAutomatically();
    else { status('Page restored. Attach again to read the light.'); void inspectLocalBridge(); }
    controls();
  }
});
el('bluetooth-support').textContent = WebBluetoothTransport.available()
  ? 'Web Bluetooth is available. Select a G60 from a user-initiated chooser.'
  : 'Direct Bluetooth is unavailable here. Use the local bridge, or open a secure page in a supported Chromium browser.';
discoveryNeeded = servedByBridge;
renderParameters();
draft(); render(state);
void inspectLocalBridge();
