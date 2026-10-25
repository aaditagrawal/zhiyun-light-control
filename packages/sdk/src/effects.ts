import { ProtocolError } from './errors';
import { EFFECT_PATTERNS } from './types';
import type { EffectConfig, EffectCurve, EffectEase, EffectPattern, EffectStatus } from './types';

/** Shortest blink phase and keyframe spacing in seconds. */
export const EFFECT_MIN_STEP = 0.1;
/** Accepts decimal inputs whose binary product lands just below EFFECT_MIN_STEP. */
const TOLERANCE = 1e-9;
const SEED_MAX = 0xffffffff;
const COMMON_FIELDS: readonly string[] = ['pattern', 'duration', 'period', 'minBrightness', 'maxBrightness', 'cct', 'cctEnd',
  'restoreOnComplete', 'seed', 'curve'];
const PATTERN_FIELDS: Readonly<Record<EffectPattern, readonly string[]>> = {
  blink: ['duty'], breathe: [], 'fade-in': [], 'fade-out': [], 'cct-sweep': [],
  pulse: ['bpm', 'style', 'accentEvery', 'accentRatio', 'decay'],
  flicker: ['style', 'depth', 'speed', 'gusts', 'cctCoupling'],
  sunrise: ['direction'], lightning: ['interval', 'flashesMax'], emergency: ['flashes', 'flashOn', 'flashOff', 'gap'],
  paparazzi: ['interval', 'burst'], tv: ['interval', 'motion'], morse: ['text', 'unit', 'loop'], keyframes: ['keyframes', 'loop'],
};
/** Pattern defaults for omitted cct/cctEnd; null means the verified baseline CCT. */
const CCT_DEFAULTS: Partial<Record<EffectPattern, readonly [number | null, number]>> = {
  flicker: [2700, 3200], sunrise: [2700, 6500], lightning: [6500, 6500], emergency: [2700, 6500], paparazzi: [5600, 6500], tv: [5000, 6500],
};
/** ITU Morse for the supported characters; input is case-insensitive. */
export const MORSE_CODE: Readonly<Record<string, string>> = Object.freeze({
  A: '.-', B: '-...', C: '-.-.', D: '-..', E: '.', F: '..-.', G: '--.', H: '....', I: '..', J: '.---', K: '-.-', L: '.-..', M: '--',
  N: '-.', O: '---', P: '.--.', Q: '--.-', R: '.-.', S: '...', T: '-', U: '..-', V: '...-', W: '.--', X: '-..-', Y: '-.--', Z: '--..',
  0: '-----', 1: '.----', 2: '..---', 3: '...--', 4: '....-', 5: '.....', 6: '-....', 7: '--...', 8: '---..', 9: '----.',
  '.': '.-.-.-', ',': '--..--', '?': '..--..', '/': '-..-.', '=': '-...-', '+': '.-.-.', '-': '-....-', '@': '.--.-.',
});

interface ParsedCommon {
  /** Effective seconds after natural-completion capping; null until stopped. */
  duration: number | null;
  period: number; minBrightness: number;
  /** null resolves to the verified nonzero baseline brightness, or 5. */
  maxBrightness: number | null;
  /** null resolves to the verified baseline CCT. */
  cct: number | null;
  cctEnd: number; restoreOnComplete: boolean; seed: number; curve: EffectCurve;
}
export interface ParsedKeyframe { at: number; brightness: number | null; cct: number | null; ease: EffectEase }
export type ParsedEffectOptions =
  | { pattern: 'blink'; duty: number }
  | { pattern: 'breathe' | 'fade-in' | 'fade-out' | 'cct-sweep' }
  | { pattern: 'pulse'; bpm: number; style: 'heartbeat' | 'beat'; accentEvery: number; accentRatio: number; decay: number }
  | { pattern: 'flicker'; style: 'candle' | 'fire'; depth: number; speed: number; gusts: number; cctCoupling: number }
  | { pattern: 'sunrise'; direction: 'rise' | 'set' }
  | { pattern: 'lightning'; interval: number; flashesMax: number }
  | { pattern: 'emergency'; flashes: number; flashOn: number; flashOff: number; gap: number }
  | { pattern: 'paparazzi'; interval: number; burst: number }
  | { pattern: 'tv'; interval: number; motion: number }
  | { pattern: 'morse'; text: string; unit: number; loop: boolean }
  | { pattern: 'keyframes'; keyframes: readonly ParsedKeyframe[]; loop: boolean };
/** A validated configuration with every baseline-independent default applied. */
export type ParsedEffectConfig = ParsedCommon & ParsedEffectOptions;

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function bounded(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;
}
function pattern(value: unknown): value is EffectPattern { return EFFECT_PATTERNS.includes(value as EffectPattern); }
function number(name: string, value: unknown, min: number, max: number): number {
  if (!bounded(value, min, max)) throw new RangeError(`${name} must be a finite number from ${min} to ${max}`);
  return value;
}
function integer(name: string, value: unknown, min: number, max: number): number {
  if (!bounded(value, min, max) || !Number.isInteger(value)) throw new RangeError(`${name} must be a whole number from ${min} to ${max}`);
  return value;
}
function choice<T extends string>(name: string, value: unknown, options: readonly T[]): T {
  if (typeof value !== 'string' || !options.includes(value as T)) throw new RangeError(`${name} must be one of ${options.join(', ')}`);
  return value as T;
}
function flag(name: string, value: unknown): boolean {
  if (typeof value !== 'boolean') throw new TypeError(`${name} must be boolean`);
  return value;
}
function brightness(name: string, value: unknown): number {
  if (!bounded(value, 0, 100) || !Number.isInteger(value)) throw new RangeError(`${name} must be a whole percent between 0 and 100`);
  return value;
}
function kelvin(name: string, value: unknown): number {
  if (!bounded(value, 2700, 6500) || value % 100 !== 0) throw new RangeError(`${name} must be a multiple of 100 between 2700 and 6500 K`);
  return value;
}

/** On-spans in whole units, and units to the end of the last symbol: dot 1, dash 3, gaps 1/3/7. */
export function morseSpans(text: string): { spans: readonly (readonly [number, number])[]; units: number } {
  const spans: [number, number][] = [];
  let cursor = 0;
  text.toUpperCase().split(' ').filter(word => word).forEach((word, wordIndex) => {
    if (wordIndex) cursor += 7;
    [...word].forEach((char, charIndex) => {
      if (charIndex) cursor += 3;
      [...MORSE_CODE[char]!].forEach((symbol, symbolIndex) => {
        if (symbolIndex) cursor += 1;
        const length = symbol === '.' ? 1 : 3;
        spans.push([cursor, cursor + length]);
        cursor += length;
      });
    });
  });
  return { spans, units: cursor };
}

function keyframes(value: unknown): ParsedKeyframe[] {
  if (!Array.isArray(value) || value.length < 2 || value.length > 256) throw new RangeError('keyframes must be a list of 2 to 256 frames');
  const frames: ParsedKeyframe[] = [];
  value.forEach((item: unknown, index) => {
    const name = `keyframes[${index}]`;
    if (!record(item)) throw new TypeError(`${name} must be an object`);
    if (Object.keys(item).some(key => !['at', 'brightness', 'cct', 'ease'].includes(key))) throw new TypeError(`${name} accepts only at, brightness, cct and ease`);
    if (item.at === undefined) throw new TypeError(`${name}.at is required`);
    const at = number(`${name}.at`, item.at, 0, 3600);
    if (index === 0 && at !== 0) throw new RangeError('The first keyframe must be at 0 seconds');
    if (index && at - frames[index - 1]!.at < EFFECT_MIN_STEP - TOLERANCE) throw new RangeError(`${name}.at must be at least 0.1 s after the previous frame`);
    frames.push({ at, brightness: item.brightness === undefined ? null : brightness(`${name}.brightness`, item.brightness),
      cct: item.cct === undefined ? null : kelvin(`${name}.cct`, item.cct),
      ease: item.ease === undefined ? 'linear' : choice(`${name}.ease`, item.ease, ['hold', 'linear', 'smooth', 'perceptual'] as const) });
  });
  return frames;
}

function morseText(char: string): boolean {
  return char === ' ' || char >= 'a' && char <= 'z' || Object.prototype.hasOwnProperty.call(MORSE_CODE, char);
}
function options(kind: EffectPattern, data: Record<string, unknown>, period: number): ParsedEffectOptions {
  const get = (key: string, fallback: unknown) => data[key] === undefined ? fallback : data[key];
  switch (kind) {
    case 'blink': {
      const duty = number('duty', get('duty', 0.5), 0, 1);
      if (duty * period < EFFECT_MIN_STEP - TOLERANCE || (1 - duty) * period < EFFECT_MIN_STEP - TOLERANCE) {
        throw new RangeError('Both blink phases (duty × period) must last at least 0.1 s');
      }
      return { pattern: kind, duty };
    }
    case 'pulse': {
      const style = choice('style', get('style', 'heartbeat'), ['heartbeat', 'beat'] as const);
      const bpm = number('bpm', get('bpm', 60), 20, 180);
      if (style === 'heartbeat' && bpm > 120) throw new RangeError('Heartbeat pulses require bpm of 120 or less');
      return { pattern: kind, bpm, style, accentEvery: integer('accentEvery', get('accentEvery', 0), 0, 16),
        accentRatio: number('accentRatio', get('accentRatio', 0.6), 0.2, 1), decay: number('decay', get('decay', 0.3), 0.05, 1) };
    }
    case 'flicker': {
      const style = choice('style', get('style', 'candle'), ['candle', 'fire'] as const);
      const fire = style === 'fire';
      return { pattern: kind, style, depth: number('depth', get('depth', fire ? 0.55 : 0.25), 0, 0.6),
        speed: number('speed', get('speed', 1), 0.25, 2), gusts: number('gusts', get('gusts', fire ? 0.5 : 0.15), 0, 1),
        cctCoupling: number('cctCoupling', get('cctCoupling', fire ? 0.5 : 0), 0, 1) };
    }
    case 'sunrise': return { pattern: kind, direction: choice('direction', get('direction', 'rise'), ['rise', 'set'] as const) };
    case 'lightning':
      return { pattern: kind, interval: number('interval', get('interval', 6), 2, 60), flashesMax: integer('flashesMax', get('flashesMax', 3), 1, 4) };
    case 'emergency':
      return { pattern: kind, flashes: integer('flashes', get('flashes', 2), 1, 4), flashOn: number('flashOn', get('flashOn', 0.15), 0.1, 10),
        flashOff: number('flashOff', get('flashOff', 0.15), 0.1, 10), gap: number('gap', get('gap', 0.3), 0.2, 30) };
    case 'paparazzi':
      return { pattern: kind, interval: number('interval', get('interval', 0.8), 0.33, 5), burst: number('burst', get('burst', 0.25), 0, 1) };
    case 'tv':
      return { pattern: kind, interval: number('interval', get('interval', 2.5), 0.5, 10), motion: number('motion', get('motion', 0.3), 0, 1) };
    case 'morse': {
      const text = get('text', 'SOS');
      if (typeof text !== 'string' || text.length < 1 || text.length > 64 || ![...text].every(morseText) || !text.trim()) {
        throw new RangeError('text must be 1 to 64 characters of A-Z, 0-9, space and . , ? / = + - @');
      }
      return { pattern: kind, text, unit: number('unit', get('unit', 0.2), 0.1, 2), loop: flag('loop', get('loop', true)) };
    }
    case 'keyframes':
      if (data.keyframes === undefined) throw new TypeError('keyframes is required');
      return { pattern: kind, keyframes: keyframes(data.keyframes), loop: flag('loop', get('loop', true)) };
    default: return { pattern: kind };
  }
}

/** Validates every field before any output and applies baseline-independent defaults. */
export function parseEffectConfig(config: EffectConfig): ParsedEffectConfig {
  if (!record(config)) throw new TypeError('Only documented effect fields are supported');
  const data = config as unknown as Record<string, unknown>;
  if (!pattern(data.pattern)) throw new TypeError('Unsupported effect pattern');
  const kind = data.pattern;
  const unknown = Object.keys(data).filter(key => !COMMON_FIELDS.includes(key) && !PATTERN_FIELDS[kind].includes(key));
  if (unknown.length) throw new TypeError(`Only documented effect fields are supported; ${kind} does not accept ${unknown.sort().join(', ')}`);
  const period = data.period === undefined ? 4 : data.period;
  const [shortest, longest] = kind === 'blink' ? [0.2, 60] : [1, 60];
  if (!bounded(period, shortest, longest)) throw new RangeError(`Effect period must be ${shortest}–${longest} seconds`);
  const parsed = options(kind, data, period);
  const loops = parsed.pattern === 'morse' || parsed.pattern === 'keyframes' ? parsed.loop : !['fade-in', 'fade-out', 'sunrise'].includes(kind);
  const natural = parsed.pattern === 'morse' && !parsed.loop ? morseSpans(parsed.text).units * parsed.unit
    : parsed.pattern === 'keyframes' && !parsed.loop ? parsed.keyframes[parsed.keyframes.length - 1]!.at : null;
  let duration: number | null;
  if (data.duration === undefined) duration = natural ?? (kind === 'sunrise' ? 900 : 10);
  else if (data.duration === null) {
    if (!loops) throw new RangeError(`Effect duration must be 1–3600 seconds; ${kind} requires a finite duration unless it loops`);
    duration = null;
  } else {
    if (!bounded(data.duration, 1, 3600)) throw new RangeError('Effect duration must be 1–3600 seconds, or null for looping patterns');
    // A longer request still stops at the program's natural completion.
    duration = natural === null ? data.duration : Math.min(data.duration, natural);
  }
  const restoreOnComplete = data.restoreOnComplete === undefined ? loops : flag('restoreOnComplete', data.restoreOnComplete);
  const minBrightness = data.minBrightness === undefined ? 0 : brightness('Effect brightness', data.minBrightness);
  const maxBrightness = data.maxBrightness === undefined ? null : brightness('Effect brightness', data.maxBrightness);
  if (maxBrightness !== null && maxBrightness < minBrightness) throw new RangeError('Effect maximum brightness must be at least the minimum');
  const [cct, cctEnd] = CCT_DEFAULTS[kind] ?? [null, 6500];
  return {
    ...parsed, duration, period, minBrightness, maxBrightness, restoreOnComplete,
    cct: data.cct === undefined ? cct : kelvin('Effect CCT', data.cct),
    cctEnd: data.cctEnd === undefined ? cctEnd : kelvin('Effect CCT', data.cctEnd),
    seed: data.seed === undefined ? 1 : integer('seed', data.seed, 0, SEED_MAX),
    curve: choice('curve', data.curve === undefined ? (kind === 'sunrise' ? 'perceptual' : 'linear') : data.curve, ['linear', 'perceptual'] as const),
  } as ParsedEffectConfig;
}
export function validateEffectConfig(config: EffectConfig): void { parseEffectConfig(config); }

function timestamp(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  const parts = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!parts) return false;
  const [, year, month, day, hour, minute, second, , offsetHour, offsetMinute] = parts;
  const y = Number(year), m = Number(month), d = Number(day);
  const leap = y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1] ?? 0;
  return m >= 1 && m <= 12 && d >= 1 && d <= days && Number(hour) <= 23 && Number(minute) <= 59 && Number(second) <= 59 &&
    (offsetHour === undefined || Number(offsetHour) <= 23 && Number(offsetMinute) <= 59) && Number.isFinite(Date.parse(value));
}
export function parseEffectStatus(value: unknown): EffectStatus {
  if (!record(value) || typeof value.running !== 'boolean' ||
      !(value.id === null || typeof value.id === 'string' && value.id.trim().length > 0) ||
      !(value.pattern === null || pattern(value.pattern)) ||
      !(value.startedAt === null || timestamp(value.startedAt)) ||
      !(value.endsAt === null || timestamp(value.endsAt)) ||
      !(value.progress === null || bounded(value.progress, 0, 1)) ||
      !(value.lastError === null || typeof value.lastError === 'string') ||
      (value.running && (value.id === null || value.pattern === null || value.startedAt === null)) ||
      (value.endsAt !== null && (value.startedAt === null || Date.parse(value.endsAt as string) < Date.parse(value.startedAt as string))) ||
      (value.running && ((value.endsAt === null) !== (value.progress === null)))) throw new ProtocolError('Invalid bridge EffectStatus');
  return { running: value.running, id: value.id, pattern: value.pattern, startedAt: value.startedAt,
    endsAt: value.endsAt, progress: value.progress, lastError: value.lastError };
}
