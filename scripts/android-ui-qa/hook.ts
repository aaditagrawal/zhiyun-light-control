// Development-only replacement for useLight, selected by build.ts. Never imported
// by the Android application. All values and discoveries here are fixtures.
import { useState } from 'react';
import type { EffectStatus, State, StatePatch } from '@lighttime/sdk';

export function useLight() {
  const mode = new URLSearchParams(location.search).get('mode') ?? 'welcome';
  const [connected, setConnected] = useState(['connected', 'error', 'working', 'running'].includes(mode));
  const [scanning, setScanning] = useState(mode === 'scanning');
  const [connecting, setConnecting] = useState(mode === 'connecting');
  const [hasScanned, setHasScanned] = useState(['empty', 'nearby', 'multiple'].includes(mode));
  const [found, setFound] = useState(['nearby', 'multiple'].includes(mode));
  const [verified, setVerified] = useState(mode !== 'error');
  const [state, setState] = useState<State>({ connected: true, address: 'fixture-only', transport: 'ble',
    device: { model: 'PL103', serial: 'UI fixture', id: 0, firmware: '1.6.4' },
    brightness: 24, cct: 4300, power: true, updatedAt: new Date().toISOString(),
  } as State);
  const [effect, setEffect] = useState<EffectStatus>({ running: mode === 'running',
    id: mode === 'running' ? 'fixture-initial' : null, pattern: mode === 'running' ? 'flicker' : null,
    startedAt: mode === 'running' ? new Date().toISOString() : null, endsAt: null, progress: null, lastError: null });
  const first = { id: 'fixture-only', name: 'PL103_EDFE', rssi: -48 };
  const devices = found ? mode === 'multiple' ? [first, { id: 'fixture-second', name: 'PL103_A7C2', rssi: -71 }] : [first] : [];
  const working = mode === 'working' || connecting;
  const status = ['permission', 'denied'].includes(mode) ? 'Bluetooth permission needed'
    : mode === 'offline' ? 'Bluetooth is off' : connected ? 'Connected · readback verified'
    : scanning ? 'Looking for your G60' : 'Ready when you are';
  const message = mode === 'permission' ? 'Allow Bluetooth access in app settings, then search again.'
    : mode === 'denied' ? 'Nearby devices permission is needed to search for and connect to the G60.'
    : mode === 'offline' ? 'Turn on Bluetooth in Android settings, then search again.'
    : mode === 'error' && !verified ? 'Readback failed. Read the light to try again.'
    : connected ? 'Current settings read from your G60.'
    : hasScanned && !devices.length ? 'No PL103 lights found. Keep the G60 nearby and release any other active Bluetooth connection.'
    : 'Connect directly over Bluetooth. Turn on your light to begin.';
  return {
    state: connected ? state : null, verified, status, message, devices,
    selected: connected || connecting ? first : null, working, scanning, hasScanned,
    permissionBlocked: mode === 'permission', connecting, unsupported: mode === 'unsupported',
    error: (mode === 'error' && !verified) || ['permission', 'denied', 'offline'].includes(mode), effect,
    scan: async () => {
      if (['permission', 'denied', 'offline', 'unsupported'].includes(mode)) return;
      setHasScanned(true); setScanning(true); setFound(false);
      setTimeout(() => { setScanning(false); setFound(mode !== 'empty'); }, 800);
    },
    stopScan: async () => setScanning(false),
    connect: async () => { setConnecting(true); setTimeout(() => { setConnecting(false); setConnected(true); setVerified(true); }, 500); },
    disconnect: async () => { setConnected(false); setConnecting(false); setEffect(s => ({ ...s, running: false })); },
    refresh: async () => setVerified(true),
    update: async (patch: StatePatch) => setState(s => ({ ...s, ...patch,
      power: patch.power ?? ((patch.brightness ?? s.brightness!) > 0), updatedAt: new Date().toISOString() })),
    startEffect: async (config: any) => setEffect({ running: true, id: crypto.randomUUID(), pattern: config.pattern,
      startedAt: new Date().toISOString(), endsAt: config.duration ? new Date(Date.now() + config.duration * 1000).toISOString() : null,
      progress: config.duration ? 0.25 : null, lastError: null }),
    stopEffect: async () => setEffect(s => ({ ...s, running: false })),
  };
}
