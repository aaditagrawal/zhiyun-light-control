import { EFFECT_PRESETS, parseEffectConfig } from '@lighttime/sdk';
import type { EffectConfig } from '@lighttime/sdk';

export const EFFECT_CHOICES = [
  { id: 'candle', title: 'Candle', note: 'A quiet, warm flicker.' },
  { id: 'fireplace', title: 'Fireplace', note: 'Deeper dips and warm gusts.' },
  { id: 'storm', title: 'Storm', note: 'Cool lightning over a dark room.' },
  { id: 'heartbeat', title: 'Heartbeat', note: 'A soft double beat.' },
  { id: 'sunrise15', title: 'Sunrise', note: 'Warm to daylight over 15 minutes.' },
  { id: 'sunset', title: 'Sunset', note: 'Daylight to a warm, dark finish.' },
  { id: 'emergency', title: 'Emergency', note: 'Alternating warm and cool white flashes.' },
  { id: 'paparazzi', title: 'Paparazzi', note: 'Irregular camera flashes and bursts.' },
  { id: 'television', title: 'Television', note: 'Changing scenes, as if a screen were nearby.' },
  { id: 'sos', title: 'SOS / Morse', note: 'Send a short message in light.' },
  { id: 'keyframes', title: 'Scene sequence', note: 'A six-second loop through warm and cool light.' },
  { id: 'blink', title: 'Blink', note: 'An even on-and-off rhythm.' },
  { id: 'breathe', title: 'Breathe', note: 'A slow rise and fall.' },
  { id: 'fade-in', title: 'Fade in', note: 'Arrive gently, then hold.' },
  { id: 'fade-out', title: 'Fade out', note: 'Fade down, then hold.' },
  { id: 'cct-sweep', title: 'CCT sweep', note: 'Travel between two white temperatures.' },
] as const;
export type EffectChoiceId = typeof EFFECT_CHOICES[number]['id'];

export interface EffectForm {
  id: EffectChoiceId;
  base: EffectConfig;
  minBrightness: string; maxBrightness: string;
  duration: string; untilStopped: boolean; period: string;
  cct: string; cctEnd: string; seed: string;
  bpm: string; text: string; unit: string;
  restoreOnComplete: boolean;
}

function choiceConfig(id: EffectChoiceId): EffectConfig {
  const preset = EFFECT_PRESETS[id];
  if (preset) return preset.pattern === 'keyframes'
    ? { ...preset, keyframes: preset.keyframes.map(frame => ({ ...frame })) }
    : { ...preset };
  const pattern = id as 'blink' | 'breathe' | 'fade-in' | 'fade-out' | 'cct-sweep';
  return { pattern, minBrightness: 0, maxBrightness: 16, duration: 10 };
}

/** Empty CCT means the parser deliberately defers to verified baseline (legacy patterns). */
export function createEffectForm(id: EffectChoiceId): EffectForm {
  const base = choiceConfig(id);
  const parsed = parseEffectConfig(base);
  return {
    id, base, minBrightness: String(parsed.minBrightness), maxBrightness: String(parsed.maxBrightness ?? 16),
    duration: String(parsed.duration ?? 10), untilStopped: parsed.duration === null,
    period: String(parsed.period), cct: parsed.cct === null ? '' : String(parsed.cct), cctEnd: String(parsed.cctEnd),
    seed: String(parsed.seed), bpm: parsed.pattern === 'pulse' ? String(parsed.bpm) : '60',
    text: parsed.pattern === 'morse' ? parsed.text : 'SOS', unit: parsed.pattern === 'morse' ? String(parsed.unit) : '0.2',
    restoreOnComplete: parsed.restoreOnComplete,
  };
}

export function supportsUntilStopped(form: EffectForm): boolean {
  return !['fade-in', 'fade-out', 'sunrise'].includes(form.base.pattern);
}

function numeric(text: string, label: string): number {
  if (!text.trim()) throw new RangeError(`${label} is required`);
  const value = Number(text);
  if (!Number.isFinite(value)) throw new RangeError(`${label} must be a number`);
  return value;
}

/** Build only documented fields, then let the shared SDK own all range/resolution checks. */
export function effectConfigFromForm(form: EffectForm): EffectConfig {
  const config: EffectConfig = {
    ...form.base,
    duration: form.untilStopped ? null : numeric(form.duration, 'Duration'),
    minBrightness: numeric(form.minBrightness, 'Minimum brightness'),
    maxBrightness: numeric(form.maxBrightness, 'Maximum brightness'),
    period: numeric(form.period, 'Period'), seed: numeric(form.seed, 'Seed'),
    cctEnd: numeric(form.cctEnd, 'End temperature'), restoreOnComplete: form.restoreOnComplete,
  };
  // Never substitute the current hardware temperature for a pattern default.
  if (form.cct.trim()) config.cct = numeric(form.cct, 'Color temperature');
  else delete config.cct;
  if (config.pattern === 'pulse') config.bpm = numeric(form.bpm, 'BPM');
  if (config.pattern === 'morse') { config.text = form.text; config.unit = numeric(form.unit, 'Morse unit'); }
  parseEffectConfig(config);
  return config;
}
