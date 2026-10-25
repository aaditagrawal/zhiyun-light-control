import { useEffect, useRef, useState } from 'react';
import { AppState, Platform } from 'react-native';
import { BleProtocolTransport, DirectEffectRunner, LightController } from '@lighttime/sdk';
import type { EffectConfig, EffectStatus, State, StatePatch, Unsubscribe } from '@lighttime/sdk';
import type { BleManager, Subscription } from 'react-native-ble-plx';
import { OperationGate } from './OperationGate';
import { EffectOwnership, EffectRetirement } from './EffectOwnership';
import { NativeBleAdapter } from './ble/NativeBleAdapter';
import { advertisedLight } from './ble/constants';
import type { DiscoveredLight } from './ble/constants';
import { createBleManager, requestBlePermissions, runtimeIssue } from './ble/runtime';

type Phase = 'idle' | 'permission' | 'scanning' | 'connecting' | 'connected' | 'busy' | 'offline' | 'unsupported' | 'error';
type PermissionRequest = { run: number; resume: ((active: boolean) => void) | null };
const backgroundMessage = 'Connection closed while the app was in the background. Search to reconnect.';
const labels: Record<Phase, string> = {
  idle: 'Ready when you are', permission: 'Bluetooth permission needed', scanning: 'Looking for your G60',
  connecting: 'Connecting to your light', connected: 'Connected · readback verified', busy: 'Reading your light',
  offline: 'Bluetooth is off', unsupported: 'Bluetooth unavailable', error: 'Connection needs attention',
};
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Bluetooth operation failed. Try searching again.';
}
const idleEffect = (): EffectStatus => ({ running: false, id: null, pattern: null, startedAt: null, endsAt: null, progress: null, lastError: null });

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
  const [effect, setEffect] = useState<EffectStatus>(idleEffect);
  const manager = useRef<BleManager | null>(null);
  const controller = useRef<LightController | null>(null);
  const runner = useRef<DirectEffectRunner | null>(null);
  const ownership = useRef<EffectOwnership | null>(null);
  const retired = useRef(new EffectRetirement()).current;
  const uneffect = useRef<Unsubscribe | null>(null);
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
  const appState = useRef(AppState.currentState);
  const permissionRequest = useRef<PermissionRequest | null>(null);

  const isCurrent = (run: number) => mounted.current && epoch.current === run;
  const canStart = (run = epoch.current) => isCurrent(run) && appState.current === 'active';

  const waitForForeground = (request: PermissionRequest): Promise<boolean> => {
    if (canStart(request.run)) return Promise.resolve(true);
    // Android can settle the permission promise before delivering the active event.
    // A real trip Home must not leave a scan queued for an arbitrary later return.
    return new Promise(resolve => {
      const deadline = Date.now() + 2_000;
      const timer = setTimeout(() => finish(false), 2_000);
      const finish = (active: boolean) => {
        clearTimeout(timer);
        request.resume = null;
        // Background JS timers may be delayed until after the active event.
        resolve(active && Date.now() <= deadline);
      };
      request.resume = finish;
    });
  };

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
    permissionRequest.current?.resume?.(false);
    abort.current?.abort();
    abort.current = null;
    // cancel() invalidates the runner synchronously, before native teardown starts.
    runner.current?.cancel();
    const previous = ownership.current;
    if (previous) retired.retain(previous);
    ownership.current = null;
    runner.current = null;
    controller.current = null;
    uneffect.current?.(); uneffect.current = null;
    unstate.current?.();
    unstate.current = null;
    if (mounted.current) {
      setVerified(false); setState(null); setSelected(null); setEffect(idleEffect());
      setPhase(nextPhase); setMessage(nextMessage);
    }
    // Keep reconnection behind native teardown, including background/disconnect races.
    const prior = closing.current;
    const pending = (async () => {
      // Retry a failed drain through the retained owners, rather than inheriting
      // a permanently rejected barrier or permitting a replacement connection.
      await prior?.catch(() => {});
      await stopNativeScan().catch(() => {});
      await retired.drain();
    })();
    closing.current = pending;
    // A failed drain must block reconnect/manager destruction, not release live I/O.
    void closing.current.catch(error => {
      if (mounted.current) { setPhase('error'); setMessage(`Bluetooth cleanup is still pending: ${errorMessage(error)}`); }
    });
    return closing.current;
  };

  const finishCleanup = async () => {
    await closing.current?.catch(() => {});
    // Scan/connect are explicit retries. Failure still blocks replacement.
    await retired.drain();
  };

  const ensureManager = async (run: number): Promise<BleManager | null> => {
    if (!canStart(run)) return null;
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
    if (!canStart(run)) return null;
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
    if (!canStart() || initialIssue || controller.current || scanActive.current) return;
    await gate.run(async () => {
      setWorking(true);
      const run = ++epoch.current;
      try {
        await finishCleanup();
        if (!canStart(run)) return;
        setPhase('permission'); setMessage('Allow Bluetooth access to find and control your light.');
        const request: PermissionRequest = { run, resume: null };
        permissionRequest.current = request;
        try {
          // The gate owns this non-cancellable native request until it settles,
          // even when stopScan(), disconnect() or unmount invalidate its result.
          const permission = await requestBlePermissions();
          if (!isCurrent(run)) return;
          setPermissionBlocked(permission === 'blocked');
          if (permission !== 'granted') {
            setPhase('permission');
            setMessage(permission === 'blocked'
              ? 'Allow Bluetooth access in app settings, then search again.'
              : Number(Platform.Version) >= 31
                ? 'Nearby devices permission is needed to search for and connect to the G60.'
                : 'This Android version requires location permission and Location switched on for Bluetooth scanning. Lighttime does not collect location.');
            return;
          }
          const active = await waitForForeground(request);
          if (!isCurrent(run)) return;
          if (!active) { await close('idle', backgroundMessage); return; }
        } finally {
          if (permissionRequest.current === request) permissionRequest.current = null;
        }
        const native = await ensureManager(run);
        if (!native || !canStart(run)) return;
        seen.current.clear(); setDevices([]); setHasScanned(true);
        scanActive.current = true; setScanning(true); setPhase('scanning');
        setMessage('Searching for PL103 lights for 10 seconds. Keep your G60 switched on and nearby.');
        await native.startDeviceScan(null, { allowDuplicates: false }, (error, device) => {
          if (!canStart(run) || !scanActive.current) return;
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
    permissionRequest.current?.resume?.(false);
    await stopNativeScan().catch(() => {});
    if (mounted.current) { setPhase('idle'); setMessage('Search stopped. Choose a light or search again.'); }
  };

  const connect = async (device: DiscoveredLight) => {
    if (!canStart()) return;
    await gate.run(async () => {
      setWorking(true);
      const run = ++epoch.current;
      const signal = new AbortController(); abort.current = signal;
      let session: LightController | null = null;
      let sessionOwner: EffectOwnership | null = null;
      try {
        await finishCleanup();
        await stopNativeScan();
        if (!canStart(run)) return;
        const native = await ensureManager(run);
        if (!native || !canStart(run)) return;
        const transport = new BleProtocolTransport(new NativeBleAdapter(native, device.id), { maxWriteSize: 20, timeoutMs: 5000 });
        session = new LightController(transport, { timeoutMs: 15_000, verificationAttempts: 3, verificationDelayMs: 150 });
        const effects = new DirectEffectRunner(session);
        const ownedController = session;
        sessionOwner = new EffectOwnership(effects, () => ownedController.disconnect());
        ownership.current = sessionOwner;
        runner.current = effects;
        controller.current = session;
        setEffect(effects.status);
        uneffect.current = effects.subscribe((next: EffectStatus) => {
          if (!mounted.current || epoch.current !== run || runner.current !== effects) return;
          setEffect(next);
          if (next.lastError) {
            setVerified(false); setPhase('error');
            setMessage(`Pattern stopped: ${next.lastError} Tap Read light to check the actual settings.`);
          }
        });
        unstate.current = session.subscribe((next) => {
          if (!mounted.current || epoch.current !== run) return;
          setState(next);
          if (!next.connected) {
            void close('error', 'The light disconnected. Search again to reconnect.');
          } else if (effects.status.running) setVerified(true);
        });
        setSelected(device); setPhase('connecting');
        setMessage(`Connecting to ${device.name} and reading its current settings…`);
        const actual = await session.connect({ signal: signal.signal, timeoutMs: 15_000 });
        if (!canStart(run)) { await sessionOwner.close(); return; }
        setState(actual); setVerified(true); setPhase('connected');
        setMessage('Brightness and color temperature were read from your G60.');
      } catch (error) {
        if (sessionOwner) retired.retain(sessionOwner);
        if (mounted.current && epoch.current === run) {
          await close('error', errorMessage(error)).catch(() => {});
        } else await finishCleanup().catch(() => {});
      } finally { if (abort.current === signal) abort.current = null; if (mounted.current) setWorking(false); }
    });
  };

  const operate = async (patch?: StatePatch) => {
    if (!canStart()) return;
    await gate.run(async () => {
      const session = controller.current;
      const effects = runner.current;
      if (!session || !effects) return;
      const run = epoch.current;
      const signal = new AbortController(); abort.current = signal;
      setWorking(true); setVerified(false); setPhase('busy');
      setMessage(patch ? 'Applying your setting, then checking the light’s actual values…' : 'Reading brightness and color temperature…');
      try {
        const actual = patch
          ? await effects.setState(patch)
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

  const effectOperation = async (config?: EffectConfig) => {
    if (!canStart()) return;
    await gate.run(async () => {
      const session = controller.current;
      const effects = runner.current;
      if (!session || !effects) return;
      if (!config && !effects.status.running) return;
      const run = epoch.current;
      setWorking(true);
      setMessage(config ? 'Starting your pattern from the light’s verified settings…' : 'Stopping and restoring brightness and temperature captured at Start…');
      try {
        const next = config ? await effects.start(config) : await effects.stop({ restore: true });
        if (!mounted.current || epoch.current !== run || runner.current !== effects) return;
        setEffect(next); setState(session.state); setVerified(true); setPhase('connected');
        setMessage(config ? 'Pattern running on this phone. Readback shows the actual light.' : 'Pattern stopped. Both baseline settings were restored and verified.');
      } catch (error) {
        if (!mounted.current || epoch.current !== run || runner.current !== effects) return;
        setEffect(effects.status); setState(session.state); setVerified(false); setPhase('error');
        setMessage(`${errorMessage(error)} Tap Read light to check the actual settings.`);
      } finally {
        // The gate still owns this operation after close() changes the epoch.
        // Release its busy UI once it settles so the user can reconnect.
        if (mounted.current) setWorking(false);
      }
    });
  };

  useEffect(() => {
    mounted.current = true;
    const subscription = AppState.addEventListener('change', (next) => {
      appState.current = next;
      if (next === 'active') permissionRequest.current?.resume?.(true);
      if (next === 'background') {
        const request = permissionRequest.current;
        // A permission activity backgrounds Android too. Defer invalidation only
        // for our current request, with no live scan or connection to clean up.
        if (request?.run === epoch.current && !scanActive.current && !controller.current) return;
        void close('idle', backgroundMessage);
      }
    });
    return () => {
      mounted.current = false;
      subscription.remove(); radio.current?.remove(); radio.current = null;
      const native = manager.current;
      void close().then(() => {
        if (manager.current === native) manager.current = null;
        if (native) void native.destroy().catch(() => {});
      }).catch(() => { /* retain the manager while cleanup still owns work */ });
    };
    // Native resources are owned by this mount; callbacks use refs rather than render state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    status: labels[phase], connecting: phase === 'connecting', message, devices, selected, state, verified, scanning, working, hasScanned, effect,
    permissionBlocked, unsupported: phase === 'unsupported', error: phase === 'error' || phase === 'permission' || phase === 'offline',
    scan, stopScan, connect, disconnect: () => close(), refresh: () => operate(), update: (patch: StatePatch) => operate(patch),
    startEffect: (config: EffectConfig) => effectOperation(config), stopEffect: () => effectOperation(),
  };
}
