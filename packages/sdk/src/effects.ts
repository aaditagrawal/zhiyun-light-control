import { ProtocolError } from './errors';
import type { EffectConfig, EffectPattern, EffectStatus } from './types';

const patterns: readonly EffectPattern[] = ['blink', 'breathe', 'fade-in', 'fade-out', 'cct-sweep'];
const configKeys = ['pattern', 'duration', 'period', 'minBrightness', 'maxBrightness', 'cct', 'cctEnd', 'restoreOnComplete'];
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function bounded(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max;
}
function pattern(value: unknown): value is EffectPattern { return patterns.includes(value as EffectPattern); }
export function validateEffectConfig(config: EffectConfig): void {
  if (!record(config) || Object.keys(config).some(key => !configKeys.includes(key))) throw new TypeError('Only documented effect fields are supported');
  if (!pattern(config.pattern)) throw new TypeError('Unsupported effect pattern');
  const fade = config.pattern === 'fade-in' || config.pattern === 'fade-out';
  if (config.duration !== undefined && !(config.duration === null && !fade) && !bounded(config.duration, 1, 3600)) {
    throw new RangeError('Effect duration must be 1–3600 seconds, or null for cyclic patterns');
  }
  if (config.period !== undefined && !bounded(config.period, 1, 60)) throw new RangeError('Effect period must be 1–60 seconds');
  for (const key of ['minBrightness', 'maxBrightness'] as const) {
    const value = config[key];
    if (value !== undefined && (!bounded(value, 0, 100) || !Number.isInteger(value))) throw new RangeError('Effect brightness must be a whole percent between 0 and 100');
  }
  if (config.maxBrightness !== undefined && config.maxBrightness < (config.minBrightness ?? 0)) throw new RangeError('Effect maximum brightness must be at least the minimum');
  for (const key of ['cct', 'cctEnd'] as const) {
    const value = config[key];
    if (value !== undefined && (!bounded(value, 2700, 6500) || value % 100 !== 0)) throw new RangeError('Effect CCT must be a multiple of 100 between 2700 and 6500 K');
  }
  if (config.restoreOnComplete !== undefined && typeof config.restoreOnComplete !== 'boolean') throw new TypeError('restoreOnComplete must be boolean');
}
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
