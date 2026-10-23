import { useEffect, useRef, useState } from 'react';
import { AppState, Platform } from 'react-native';
import { BleProtocolTransport, LightController } from '@lighttime/sdk';
import type { State, StatePatch, Unsubscribe } from '@lighttime/sdk';
import type { BleManager, Subscription } from 'react-native-ble-plx';
import { OperationGate } from './OperationGate';
import { NativeBleAdapter } from './ble/NativeBleAdapter';
import { advertisedLight } from './ble/constants';
import type { DiscoveredLight } from './ble/constants';
import { createBleManager, requestBlePermissions, runtimeIssue } from './ble/runtime';

type Phase = 'idle' | 'permission' | 'scanning' | 'connecting' | 'connected' | 'busy' | 'offline' | 'unsupported' | 'error';
const labels: Record<Phase, string> = {
  idle: 'Ready when you are', permission: 'Bluetooth permission needed', scanning: 'Looking for your G60',
  connecting: 'Connecting to your light', connected: 'Connected · readback verified', busy: 'Reading your light',
  offline: 'Bluetooth is off', unsupported: 'Bluetooth unavailable', error: 'Connection needs attention',
};
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Bluetooth operation failed. Try searching again.';
}

export function useLight() {
  const initialIssue = useRef(runtimeIssue()).current;
  const [phase, setPhase] = useState<Phase>(initialIssue ? 'unsupported' : 'idle');
  const [message, setMessage] = useState(initialIssue ?? 'Connect directly over Bluetooth. Turn on your light to begin.');
  const [devices, setDevices] = useState<DiscoveredLight[]>([]);
  const [selected, setSelected] = useState<DiscoveredLight | null>(null);
  const [state, setState] = useState<State | null>(null);
  const [verified, setVerified] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [working, setWorking] = useState(false);
  const [hasScanned, setHasScanned] = useState(false);
  const [permissionBlocked, setPermissionBlocked] = useState(false);
  const manager = useRef<BleManager | null>(null);
  const controller = useRef<LightController | null>(null);
  const abort = useRef<AbortController | null>(null);
  const unstate = useRef<Unsubscribe | null>(null);
  const radio = useRef<Subscription | null>(null);
  const scanTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scanActive = useRef(false);
  const seen = useRef(new Map<string, DiscoveredLight>());
  const gate = useRef(new OperationGate()).current;
  const epoch = useRef(0);
  const mounted = useRef(true);
  const closing = useRef<Promise<void> | null>(null);

  const stopNativeScan = async () => {
    if (scanTimer.current) clearTimeout(scanTimer.current);
    scanTimer.current = null;
    const wasScanning = scanActive.current;
    scanActive.current = false;
    if (mounted.current) setScanning(false);
    if (wasScanning && manager.current) await manager.current.stopDeviceScan();
  };

  const close = (nextPhase: Phase = 'idle', nextMessage = 'Disconnected. The light keeps its current settings.') => {
    ++epoch.current;
    abort.current?.abort();
    abort.current = null;
    const previous = controller.current;
    controller.current = null;
    unstate.current?.();
    unstate.current = null;
    if (mounted.current) {
      setVerified(false); setState(null); setSelected(null);
      setPhase(nextPhase); setMessage(nextMessage);
    }
    // Keep reconnection behind native teardown, including background/disconnect races.
    const pending = (async () => {
      await stopNativeScan().catch(() => {});
      if (previous) await previous.disconnect().catch(() => {});
    })();
    closing.current = Promise.all([closing.current, pending]).then(() => {});
    return closing.current;
  };

  const ensureManager = async (): Promise<BleManager | null> => {
    if (!manager.current) {
      try { manager.current = createBleManager(); }
      catch {
        setPhase('unsupported');
        setMessage('The native Bluetooth module is unavailable. Rebuild and install the Android development app.');
        return null;
      }
      radio.current = manager.current.onStateChange((next) => {
        if (!mounted.current) return;
        if (next === 'PoweredOff') void close('offline', 'Turn on Bluetooth in Android settings, then search again.');
        else if (next === 'Unsupported') void close('unsupported', 'This Android device does not support Bluetooth Low Energy.');
        else if (next === 'Unauthorized') {
          setPermissionBlocked(true);
          void close('permission', 'Allow Bluetooth access in app settings, then search again.');
        }
      });
    }
    const status = await manager.current.state();
    if (status !== 'PoweredOn') {
      const next: Phase = status === 'Unsupported' ? 'unsupported' : status === 'Unauthorized' ? 'permission' : status === 'PoweredOff' ? 'offline' : 'error';
      setPhase(next);
      setMessage(status === 'PoweredOff' ? 'Turn on Bluetooth in Android settings, then search again.'
        : status === 'Unauthorized' ? 'Allow Bluetooth access in app settings, then search again.'
          : status === 'Unsupported' ? 'This Android device does not support Bluetooth Low Energy.'
            : 'Bluetooth is starting or restarting. Wait a moment, then search again.');
      return null;
    }
    return manager.current;
  };

  const scan = async () => {
    if (initialIssue || controller.current || scanActive.current) return;
    await gate.run(async () => {
      setWorking(true);
      const run = ++epoch.current;
      try {
        await closing.current;
        if (!mounted.current || epoch.current !== run) return;
        setPhase('permission'); setMessage('Allow Bluetooth access to find and control your light.');
        const permission = await requestBlePermissions();
        if (!mounted.current || epoch.current !== run) return;
        setPermissionBlocked(permission === 'blocked');
        if (permission !== 'granted') {
          setPhase('permission');
          setMessage(Number(Platform.Version) >= 31
            ? 'Nearby devices permission is needed to search for and connect to the G60.'
            : 'This Android version requires location permission and Location switched on for Bluetooth scanning. Lighttime does not collect location.');
          return;
        }
        const native = await ensureManager();
        if (!native || !mounted.current || epoch.current !== run) return;
        seen.current.clear(); setDevices([]); setHasScanned(true);
        scanActive.current = true; setScanning(true); setPhase('scanning');
        setMessage('Searching for PL103 lights for 10 seconds. Keep your G60 switched on and nearby.');
        await native.startDeviceScan(null, { allowDuplicates: false }, (error, device) => {
          if (!mounted.current || epoch.current !== run || !scanActive.current) return;
          if (error) {
            void stopNativeScan().catch(() => {});
            setPhase('error');
            setMessage(`${errorMessage(error)}${Number(Platform.Version) < 31 ? ' On Android 11 and earlier, check that Location is switched on.' : ''}`);
            return;
          }
          const light = device && advertisedLight(device);
          if (light) {
            seen.current.set(light.id, light);
            setDevices([...seen.current.values()].sort((a, b) => (b.rssi ?? -200) - (a.rssi ?? -200)));
          }
        });
        if (epoch.current !== run || !scanActive.current) return;
        scanTimer.current = setTimeout(() => {
          void stopNativeScan().catch(() => {});
          if (mounted.current && epoch.current === run) {
            setPhase('idle');
            setMessage(seen.current.size ? 'Choose your light below to connect.'
              : `No PL103 lights found. Keep the G60 nearby and release any other active Bluetooth connection.${Number(Platform.Version) < 31 ? ' Check that Android Location is switched on.' : ''}`);
          }
        }, 10_000);
      } catch (error) {
        await stopNativeScan().catch(() => {});
        if (mounted.current && epoch.current === run) { setPhase('error'); setMessage(errorMessage(error)); }
      } finally { if (mounted.current) setWorking(false); }
    });
  };

  const stopScan = async () => {
    ++epoch.current;
    await stopNativeScan().catch(() => {});
    if (mounted.current) { setPhase('idle'); setMessage('Search stopped. Choose a light or search again.'); }
  };

  const connect = async (device: DiscoveredLight) => {
    await gate.run(async () => {
      setWorking(true);
      const run = ++epoch.current;
      const signal = new AbortController(); abort.current = signal;
      let session: LightController | null = null;
      try {
        await closing.current;
        await stopNativeScan();
        if (!mounted.current || epoch.current !== run) return;
        const native = await ensureManager();
        if (!native || !mounted.current || epoch.current !== run) return;
        const transport = new BleProtocolTransport(new NativeBleAdapter(native, device.id), { maxWriteSize: 20, timeoutMs: 5000 });
        session = new LightController(transport, { timeoutMs: 15_000, verificationAttempts: 3, verificationDelayMs: 150 });
        controller.current = session;
        unstate.current = session.subscribe((next) => {
          if (!mounted.current || epoch.current !== run) return;
          setState(next);
          if (!next.connected) {
            setVerified(false); setPhase('error');
            setMessage('The light disconnected. Search again to reconnect.');
            controller.current = null;
            unstate.current?.(); unstate.current = null;
          }
        });
        setSelected(device); setPhase('connecting');
        setMessage(`Connecting to ${device.name} and reading its current settings…`);
        const actual = await session.connect({ signal: signal.signal, timeoutMs: 15_000 });
        if (!mounted.current || epoch.current !== run) { await session.disconnect().catch(() => {}); return; }
        setState(actual); setVerified(true); setPhase('connected');
        setMessage('Brightness and color temperature were read from your G60.');
      } catch (error) {
        if (session) await session.disconnect().catch(() => {});
        if (mounted.current && epoch.current === run) {
          controller.current = null;
          unstate.current?.(); unstate.current = null;
          setState(null); setSelected(null); setVerified(false); setPhase('error'); setMessage(errorMessage(error));
        }
      } finally { if (abort.current === signal) abort.current = null; if (mounted.current) setWorking(false); }
    });
  };

  const operate = async (patch?: StatePatch) => {
    await gate.run(async () => {
      const session = controller.current;
      if (!session) return;
      const run = epoch.current;
      const signal = new AbortController(); abort.current = signal;
      setWorking(true); setVerified(false); setPhase('busy');
      setMessage(patch ? 'Applying your setting, then checking the light’s actual values…' : 'Reading brightness and color temperature…');
      try {
        const actual = patch
          ? await session.setState(patch, { signal: signal.signal, timeoutMs: 12_000 })
          : await session.refresh({ signal: signal.signal, timeoutMs: 12_000 });
        if (!mounted.current || epoch.current !== run) return;
        setState(actual); setVerified(true); setPhase('connected');
        setMessage(patch ? 'Setting confirmed by readback from your G60.' : 'Current settings read from your G60.');
      } catch (error) {
        if (!mounted.current || epoch.current !== run) return;
        setState(session.state); setVerified(false); setPhase('error');
        setMessage(`${errorMessage(error)} No setting was confirmed. ${session.state.connected ? 'Tap Read light to check the actual settings before adjusting again.' : 'Search again to reconnect.'}`);
      } finally { if (abort.current === signal) abort.current = null; if (mounted.current) setWorking(false); }
    });
  };

  useEffect(() => {
    mounted.current = true;
    const subscription = AppState.addEventListener('change', (next) => {
      if (next === 'background') void close('idle', 'Connection closed while the app was in the background. Search to reconnect.');
    });
    return () => {
      mounted.current = false;
      subscription.remove(); radio.current?.remove(); radio.current = null;
      const native = manager.current;
      void close().finally(() => {
        if (manager.current === native) manager.current = null;
        if (native) void native.destroy().catch(() => {});
      });
    };
    // Native resources are owned by this mount; callbacks use refs rather than render state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    status: labels[phase], connecting: phase === 'connecting', message, devices, selected, state, verified, scanning, working, hasScanned,
    permissionBlocked, unsupported: phase === 'unsupported', error: phase === 'error' || phase === 'permission' || phase === 'offline',
    scan, stopScan, connect, disconnect: () => close(), refresh: () => operate(), update: (patch: StatePatch) => operate(patch),
  };
}
