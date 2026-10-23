import { morseSpans, parseEffectConfig, resolveEffect } from '@lighttime/sdk';
import type { EffectConfig, EffectTarget, ParsedEffectConfig } from '@lighttime/sdk';

export const PREVIEW_SAMPLE_COUNT = 64;
// A display assumption, never hardware readback or a replacement for runner baseline capture.
export const PREVIEW_BASELINE = Object.freeze({ brightness: 16, cct: 4300 });

export interface PreviewSample extends EffectTarget { at: number }
export interface PatternPreview {
  samples: readonly PreviewSample[];
  seconds: number;
  windowLabel: string;
  ceiling: number;
  brightnessLabel: string;
  temperatureLabel: string;
  runLabel: string;
  baselineNote: string | null;
}
export type PreviewResult = { preview: PatternPreview; error: null } | { preview: null; error: string };

export function formatSeconds(seconds: number): string {
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60} min`;
  return `${Number(seconds.toFixed(2))} s`;
}

function windowFor(config: ParsedEffectConfig): { seconds: number; label: string } {
  if (config.pattern === 'fade-in' || config.pattern === 'fade-out' || config.pattern === 'sunrise') {
    return { seconds: config.duration!, label: `Full transition · ${formatSeconds(config.duration!)}` };
  }
  let seconds: number;
  let cycle = true;
  switch (config.pattern) {
    case 'blink': case 'breathe': case 'cct-sweep': seconds = config.period; break;
    case 'pulse': seconds = 120 / config.bpm; break;
    case 'keyframes': seconds = config.keyframes[config.keyframes.length - 1]!.at; break;
    case 'morse': {
      const full = (morseSpans(config.text).units + (config.loop ? 7 : 0)) * config.unit;
      seconds = Math.min(full, 12);
      cycle = full <= 12;
      break;
    }
    case 'emergency': seconds = 2 * (config.flashes * (config.flashOn + config.flashOff) + config.gap); break;
    // Short windows also bound the SDK's renewal cursors, even for hour-long runs.
    case 'lightning': seconds = 12; cycle = false; break;
    case 'tv': seconds = 8; cycle = false; break;
    default: seconds = 6; cycle = false;
  }
  if (config.duration !== null && config.duration <= seconds) {
    return { seconds: config.duration, label: `Full run · ${formatSeconds(config.duration)}` };
  }
  const label = config.pattern === 'pulse' ? 'Two beats' : config.pattern === 'morse' ? 'Message' : 'One cycle';
  return { seconds, label: `${cycle ? `${label} · ` : 'First '}${formatSeconds(seconds)}` };
}

function baselineNote(config: ParsedEffectConfig): string | null {
  const first = config.pattern === 'keyframes' ? config.keyframes[0]! : null;
  const brightness = first ? first.brightness === null : config.maxBrightness === null;
  const cct = first ? first.cct === null : config.cct === null;
  if (!brightness && !cct) return null;
  const values = [brightness ? '16%' : '', cct ? '4300 K' : ''].filter(Boolean).join(' · ');
  return `Preview baseline: ${values}. Start uses the settings read from your light.`;
}

/** Exactly 64 forward samples; no timer, transport, event replay loop, or hardware access. */
export function previewEffect(config: EffectConfig): PreviewResult {
  try {
    const unresolved = parseEffectConfig(config);
    const effect = resolveEffect(config, PREVIEW_BASELINE);
    const parsed = effect.config;
    const window = windowFor(parsed);
    const samples = Array.from({ length: PREVIEW_SAMPLE_COUNT }, (_, index) => {
      const at = window.seconds * index / (PREVIEW_SAMPLE_COUNT - 1);
      return { at, ...effect.target(at) };
    });
    const brightnesses = samples.map(sample => sample.brightness);
    const ccts = samples.map(sample => sample.cct);
    const high = Math.max(...brightnesses);
    const warm = Math.min(...ccts), cool = Math.max(...ccts);
    let carriedBrightness: number = PREVIEW_BASELINE.brightness;
    const frameLevels = parsed.pattern === 'keyframes' ? parsed.keyframes.map(frame => {
      carriedBrightness = frame.brightness ?? carriedBrightness;
      return carriedBrightness;
    }) : [];
    const ceiling = Math.max(1, parsed.pattern === 'keyframes' ? Math.max(...frameLevels) : parsed.maxBrightness!, high);
    const min = parsed.pattern === 'keyframes' ? Math.min(...frameLevels)
      : parsed.pattern === 'cct-sweep' ? parsed.maxBrightness! : parsed.minBrightness;
    const max = parsed.pattern === 'keyframes' ? Math.max(...frameLevels) : parsed.maxBrightness!;
    return { error: null, preview: {
      samples, seconds: window.seconds, windowLabel: window.label, ceiling,
      brightnessLabel: min === max ? `${max}%` : `${min}–${max}%`,
      temperatureLabel: warm === cool ? `${warm} K` : `${warm}–${cool} K`,
      runLabel: effect.duration === null ? 'Until stopped' : formatSeconds(effect.duration),
      baselineNote: baselineNote(unresolved),
    } };
  } catch (error) {
    return { preview: null, error: error instanceof Error ? error.message : 'Check the requested settings.' };
  }
}

/** Indicative screen colors: warmer to cooler whites, not a colorimetric light simulation. */
export function temperatureColor(cct: number): string {
  const t = Math.max(0, Math.min(1, (cct - 2700) / 3800));
  const warm = [232, 192, 133], cool = [207, 222, 239];
  return `rgb(${warm.map((value, index) => Math.round(value + (cool[index]! - value) * t)).join(', ')})`;
}
