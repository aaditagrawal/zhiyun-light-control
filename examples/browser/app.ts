import { BridgeClient, LightController, WebBluetoothTransport, disconnectedState } from '../../packages/sdk/src';
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
const servedByBridge = /^\/ui(?:\/|$)/.test(location.pathname) && ['http:', 'https:'].includes(location.protocol);
const patternNames: Record<EffectPattern, string> = { blink: 'Blink', breathe: 'Breathe', 'fade-in': 'Fade in', 'fade-out': 'Fade out', 'cct-sweep': 'CCT sweep' };
function patternFields() {
  const fade = effectPattern.value === 'fade-in' || effectPattern.value === 'fade-out';
  if (fade) effectUntil.checked = false;
  effectUntil.disabled = fade;
  effectDuration.disabled = effectUntil.checked;
  effectPeriod.disabled = fade;
  effectEnd.disabled = effectPattern.value !== 'cct-sweep';
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
  reconnect.disabled = busy;
  reconnectActions.hidden = !automatic || state.connected;
  reconnect.textContent = defaultAddress ? 'Reconnect light' : 'Retry connection';
  const patternsAvailable = connected && bridge !== null;
  effectSettings.disabled = !patternsAvailable;
  effectStart.disabled = !patternsAvailable || writing;
  effectStop.disabled = !patternsAvailable || writing;
  el('patterns-connection').textContent = bridge && state.connected
    ? 'Patterns change the light over time. Current output appears in readback above.'
    : automatic ? 'Waiting for the light’s bridge connection. Use Reconnect light if it is unavailable.'
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
  epoch++; clearTimeout(timer); pending = null; dirty = false; revision++;
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
    void release().catch(() => {}); persistToken(); client = next; bridge = next;
    void configuring((current, signal) => action(next, current, signal));
  } catch (error) { showError(error); }
}
function startEvents(active: BridgeClient) {
  stream?.abort(); const abort = new AbortController(); stream = abort;
  const currentEpoch = epoch;
  el('events-status').textContent = 'Listening for bridge state events…';
  void (async () => {
    try {
      for await (const event of active.events({ signal: abort.signal })) {
        if (epoch !== currentEpoch) break;
        // An undated disconnect is authoritative. End the session so an older
        // in-flight JSON result cannot restore a connected-looking display.
        if (!event.connected) {
          void release().catch(() => {});
          status('Bridge reports the light disconnected. Attach again to read the light.', 'error');
          return;
        }
        if (!render(event)) continue;
        el('events-status').textContent = 'Bridge state events active.';
        if (!writing) status('Bridge connected · actual readback received.', 'connected');
      }
    } catch (error) {
      if (!abort.signal.aborted && epoch === currentEpoch) {
        el('events-status').textContent = `Live events stopped: ${errorText(error)}. Use Read back to refresh.`;
        status('Bridge events lost. Displayed readback may be stale.', 'error');
      }
    }
  })();
}
function waitForStartup(signal: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    const finish = () => { clearTimeout(wait); signal.removeEventListener('abort', finish); resolve(); };
    const wait = setTimeout(finish, 1000);
    signal.addEventListener('abort', finish, { once: true });
    if (signal.aborted) finish();
  });
}
function attachAutomatically(connectLight = false) {
  if (busy || !automatic) return;
  const next = new BridgeClient({ baseUrl: location.origin, timeoutMs: 60_000 });
  void release().catch(() => {}); client = next; bridge = next;
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
            status('Light connected · actual readback received.', 'connected');
            startEvents(next); startEffectPolling(next); return;
          }
        } catch (error) { if (!current()) return; lastError = error; }
        if (Date.now() >= until) break;
        await waitForStartup(signal);
      } while (current());
      if (!current()) return;
      status(defaultAddress ? 'Light is unavailable. Use Reconnect light to try again.'
        : `Light is unavailable. Retry when it is connected to the bridge.${lastError ? ` ${errorText(lastError)}` : ''}`, 'error');
    } catch (error) { if (current()) status(`Reconnect failed: ${errorText(error)}`, 'error'); }
  });
}
async function inspectLocalBridge() {
  if (!servedByBridge) return;
  const currentEpoch = epoch;
  const current = () => epoch === currentEpoch;
  const probe = new BridgeClient({ baseUrl: location.origin, timeoutMs: 3000 });
  connectionSettings.hidden = true;
  status('Connecting to the light…', 'busy');
  // Hide setup while detecting the personal controller. Secured bridges return
  // to the ordinary editable connection UI, with the remembered session intact.
  try {
    const health = await probe.health({ signal: operations.signal });
    if (!current()) return;
    if (health.authRequired === false) {
      automatic = true; defaultAddress = health.defaultAddress ?? null;
      connectionSettings.hidden = true;
      attachAutomatically();
    } else {
      connectionSettings.hidden = false;
      status('Disconnected. Choose a connection below.');
    }
  } catch (error) {
    if (current()) { connectionSettings.hidden = false; status(`Bridge startup check failed: ${errorText(error)}. Choose a connection below.`, 'error'); }
  } finally { probe.dispose(); }
}
reconnect.addEventListener('click', () => attachAutomatically(true));
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
  void release().catch(() => {});
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
  if (auto.checked && dirty) timer = setTimeout(queueDraft, 200);
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
  if (bridge) el('effect-action').textContent = 'Manual output cancels any bridge pattern before applying. Waiting for verified readback and the next runner check.';
  try {
    while (pending && epoch === currentEpoch) {
      const target = pending; pending = null;
      el('write-status').textContent = 'Applying settings and checking actual readback…';
      const actual = await active.setState(target.patch, { signal });
      if (epoch !== currentEpoch) return;
      if (!actual.connected) {
        void release().catch(() => {});
        status('The light disconnected before verification. Attach again to read the light.', 'error');
        return;
      }
      render(actual);
      const matches = state.connected &&
        (target.patch.brightness === undefined || (state.brightness !== null && Math.abs(state.brightness - target.patch.brightness) <= 0.01)) &&
        (target.patch.cct === undefined || state.cct === target.patch.cct) &&
        (target.patch.power === undefined || state.power === target.patch.power);
      if (target.revision === revision) dirty = !matches;
      status(matches ? 'Settings verified by device readback.' : 'Readback differs from the requested settings.', matches ? 'connected' : 'error');
      el('write-status').textContent = target.revision !== revision ? 'Newer requested settings are pending.'
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
  effectRestore.checked = effectPattern.value !== 'fade-in' && effectPattern.value !== 'fade-out';
  patternFields();
});
effectUntil.addEventListener('change', patternFields);
el<HTMLFormElement>('effect-form').addEventListener('submit', event => {
  event.preventDefault();
  if (!bridge || !state.connected || busy || writing) return;
  const active = bridge;
  const config: EffectConfig = {
    pattern: effectPattern.value as EffectPattern,
    duration: effectUntil.checked ? null : effectDuration.valueAsNumber,
    minBrightness: el<HTMLInputElement>('effect-min').valueAsNumber,
    maxBrightness: el<HTMLInputElement>('effect-max').valueAsNumber,
    cct: el<HTMLInputElement>('effect-cct').valueAsNumber,
    restoreOnComplete: effectRestore.checked,
    ...(effectPeriod.disabled ? {} : { period: effectPeriod.valueAsNumber }),
    ...(effectEnd.disabled ? {} : { cctEnd: effectEnd.valueAsNumber }),
  };
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
    void release().catch(() => {}); status('Light is disconnected. Attach again to read the light.', 'error'); return;
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
    if (current()) status('Light disconnected.');
  });
});
window.addEventListener('pagehide', () => {
  // release() invalidates the visible connection synchronously before awaiting
  // native teardown, so a bfcache restoration cannot reuse a disposed client.
  void release().catch(() => {});
  busy = false;
  status('Disconnected. Attach again to read the light.');
  controls();
});
window.addEventListener('pageshow', event => {
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
draft(); render(state);
void inspectLocalBridge();
