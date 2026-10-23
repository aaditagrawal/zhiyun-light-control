// Explicit opt-in live smoke. Never connects/disconnects BLE or changes the visual baseline.
import { BridgeClient } from '@lighttime/sdk';
const tokenPath = Bun.env.LIGHTTIME_TOKEN_FILE ?? `${Bun.env.HOME}/.config/lighttime/bridge.token`;
const token = (await Bun.file(tokenPath).text()).trim();
const client = new BridgeClient({ baseUrl: Bun.env.LIGHTTIME_BRIDGE_URL ?? 'http://127.0.0.1:8877', token });
const cancel = new AbortController();
const assertBaseline = (state: { connected: boolean; brightness: number | null; cct: number | null }) => {
  if (!state.connected || state.brightness === null || Math.abs(state.brightness - 5) > 0.01 || state.cct !== 5000) throw new Error('Bridge readback differs from the expected 5% / 5000 K baseline; stopping.');
};
try {
  const before = await client.refresh(); assertBaseline(before);
  const events = client.events({ signal: cancel.signal, idleTimeoutMs: 10_000 });
  const first = events.next();
  // Attach a rejection handler before the write to avoid an unhandled stream error.
  const eventResult = first.then(value => ({ ok: true as const, value }), () => ({ ok: false as const }));
  const verified = await client.setState({ brightness: 5, cct: 5000 }); assertBaseline(verified);
  const event = await eventResult;
  if (!event.ok || event.value.done) throw new Error('Authenticated bridge SSE did not deliver State');
  assertBaseline(event.value.value);
  cancel.abort(); await events.return();
  assertBaseline(await client.refresh());
  console.log('Live bridge smoke passed: refresh, verified PATCH 5% / 5000 K, authenticated SSE, final baseline readback.');
} catch (error) {
  // Print only the error category; never dump requests, headers, token, or remote error bodies.
  console.error(`Live bridge smoke failed (${error instanceof Error ? error.name : 'unknown error'}).`);
  process.exitCode = 1;
} finally { cancel.abort(); client.dispose(); }
