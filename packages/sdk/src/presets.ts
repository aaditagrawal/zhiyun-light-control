import type { EffectConfig } from './types';

function freeze<T>(value: T): T {
  if (typeof value === 'object' && value !== null && !Object.isFrozen(value)) {
    for (const item of Object.values(value)) freeze(item);
    Object.freeze(value);
  }
  return value;
}

/**
 * Starting points with restrained 12–20% peaks; users may tune any level 0–100%.
 * Deeply frozen: copy a preset before editing it. Selecting one changes no output.
 */
export const EFFECT_PRESETS: Readonly<Record<string, EffectConfig>> = freeze({
  candle: { pattern: 'flicker', style: 'candle', depth: 0.4, minBrightness: 2, maxBrightness: 16, duration: null },
  fireplace: { pattern: 'flicker', style: 'fire', minBrightness: 2, maxBrightness: 18, duration: null },
  storm: { pattern: 'lightning', interval: 6, minBrightness: 0, maxBrightness: 20, duration: null },
  heartbeat: { pattern: 'pulse', style: 'heartbeat', bpm: 60, minBrightness: 2, maxBrightness: 16, duration: null },
  sunrise15: { pattern: 'sunrise', direction: 'rise', duration: 900, minBrightness: 0, maxBrightness: 20 },
  sunset: { pattern: 'sunrise', direction: 'set', duration: 900, minBrightness: 0, maxBrightness: 20 },
  emergency: { pattern: 'emergency', minBrightness: 0, maxBrightness: 20, duration: null },
  paparazzi: { pattern: 'paparazzi', interval: 0.8, burst: 0.25, minBrightness: 0, maxBrightness: 20, duration: null },
  television: { pattern: 'tv', interval: 2.5, motion: 0.3, minBrightness: 2, maxBrightness: 16, duration: null },
  sos: { pattern: 'morse', text: 'SOS', unit: 0.2, minBrightness: 0, maxBrightness: 16, duration: null },
  keyframes: { pattern: 'keyframes', duration: null, keyframes: [
    { at: 0, brightness: 4, cct: 2700 },
    { at: 2, brightness: 16, cct: 4300, ease: 'smooth' },
    { at: 3.5, cct: 6500, ease: 'linear' },
    { at: 4, brightness: 8, ease: 'hold' },
    { at: 6, brightness: 4, cct: 2700, ease: 'perceptual' },
  ] },
} satisfies Record<string, EffectConfig>);
