export interface OperationOptions { signal?: AbortSignal; timeoutMs?: number }
export interface Device {
  transport: 'ble' | 'serial'; address: string; name: string;
  rssi?: number; accessible?: boolean;
}
export interface State {
  connected: boolean;
  transport: 'ble' | 'serial' | 'mock' | null;
  address: string | null;
  device: { model: string; serial: string; id: number } | null;
  brightness: number | null; cct: number | null; power: boolean | null;
  updatedAt: string | null;
}
export interface StatePatch { brightness?: number; cct?: number; power?: boolean }
export type EffectPattern = 'blink' | 'breathe' | 'fade-in' | 'fade-out' | 'cct-sweep';
/** Bridge-generated patterns; only cyclic patterns accept an unbounded duration. */
export interface EffectConfig {
  pattern: EffectPattern;
  duration?: number | null;
  period?: number;
  minBrightness?: number;
  maxBrightness?: number;
  cct?: number;
  cctEnd?: number;
  restoreOnComplete?: boolean;
}
export interface EffectStatus {
  running: boolean;
  id: string | null;
  pattern: EffectPattern | null;
  startedAt: string | null;
  endsAt: string | null;
  progress: number | null;
  lastError: string | null;
}
export type Unsubscribe = () => void;
export function disconnectedState(): State {
  return { connected: false, transport: null, address: null, device: null,
    brightness: null, cct: null, power: null, updatedAt: null };
}
export function validatePatch(patch: StatePatch): void {
  if (Object.keys(patch).some(key => !['brightness', 'cct', 'power'].includes(key))) {
    throw new TypeError('Only brightness, cct, and power are supported');
  }
  if (patch.brightness !== undefined && (!Number.isInteger(patch.brightness) || patch.brightness < 0 || patch.brightness > 100)) {
    throw new RangeError('G60 brightness must be a whole percent between 0 and 100');
  }
  if (patch.cct !== undefined && (!Number.isInteger(patch.cct) || patch.cct % 100 !== 0 || patch.cct < 2700 || patch.cct > 6500)) {
    throw new RangeError('G60 CCT must be a multiple of 100 between 2700 and 6500 K');
  }
  if (patch.power !== undefined && typeof patch.power !== 'boolean') throw new TypeError('Power must be boolean');
  if (patch.power !== undefined && patch.brightness !== undefined && patch.power !== (patch.brightness > 0)) {
    throw new RangeError('Brightness and power must agree');
  }
}
