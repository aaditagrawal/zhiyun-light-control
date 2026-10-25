import { morseSpans, parseEffectConfig } from './effects';
import type { ParsedEffectConfig } from './effects';
import type { EffectConfig } from './types';

export interface EffectTarget { brightness: number; cct: number }
export interface ResolvedEffect {
  config: ParsedEffectConfig;
  duration: number | null;
  restoreOnComplete: boolean;
  target(elapsedSeconds: number): EffectTarget;
  sample(tSeconds: number, final?: boolean): EffectTarget;
  nextChange(elapsedSeconds: number): number | null;
}

const SAMPLE_INTERVAL = 0.1;

/** Whole-percent output, with the same half-up rounding as Python. */
export function quantizeBrightness(value: number): number {
  return Math.min(100, Math.max(0, Math.floor(value + 0.5)));
}
/** Output in whole 100 K units, clamped to the G60 range. */
export function quantizeCct(value: number): number {
  return Math.min(6500, Math.max(2700, 100 * Math.floor(value / 100 + 0.5)));
}
/** lowbias32: all integer arithmetic is modulo 2**32. */
export function effectHash32(value: number): number {
  let x = value >>> 0;
  x ^= x >>> 16;
  x = Math.imul(x, 0x7feb352d);
  x ^= x >>> 15;
  x = Math.imul(x, 0x846ca68b);
  x ^= x >>> 16;
  return x >>> 0;
}
/** Counter-based randomness; sampling order never advances an RNG. */
export function effectRandom(seed: number, stream: number, index: number): number {
  return effectHash32(seed ^ effectHash32((Math.imul(stream, 0x9e3779b9) + index) >>> 0)) / 4294967296;
}

const smooth = (u: number): number => u * u * (3 - 2 * u);
const perceptual = (u: number): number => u ** 2.2;
const cosine = (u: number): number => (1 - Math.cos(Math.PI * u)) / 2;
const expGap = (mean: number, u: number, low: number, high: number): number =>
  Math.min(high, Math.max(low, -mean * Math.log(1 - u)));

function noise(seed: number, stream: number, t: number, period: number): number {
  const x = t / period, n = Math.floor(x), a = effectRandom(seed, stream, n);
  return a + (effectRandom(seed, stream, n + 1) - a) * cosine(x - n);
}
function firstAfter(t: number, candidates: readonly number[]): number | null {
  let best: number | null = null;
  for (const candidate of candidates) if (candidate > t && (best === null || candidate < best)) best = candidate;
  return best;
}
function bisectRight(values: readonly number[], value: number): number {
  let low = 0, high = values.length;
  while (low < high) {
    const mid = (low + high) >>> 1;
    if (value < values[mid]!) high = mid;
    else low = mid + 1;
  }
  return low;
}

interface Located { index: number; previous: number | null; start: number | null; following: number }

/** Constant-memory event cursor, replayed from the start on backward seeks. */
class Renewal {
  private index = 0;
  private previous: number | null = null;
  private start = 0;
  private following = 0;

  constructor(private readonly gap: (index: number) => number, private readonly anchored = false) { this.rewind(); }
  private rewind(): void {
    this.index = 0;
    this.previous = null;
    this.start = this.anchored ? 0 : this.gap(0);
    this.following = this.start + this.gap(1);
  }
  locate(t: number): Located {
    if (t < this.start) {
      this.rewind();
      if (t < this.start) return { index: -1, previous: null, start: null, following: this.start };
    }
    while (this.following <= t) {
      ++this.index;
      this.previous = this.start;
      this.start = this.following;
      this.following = this.start + this.gap(this.index + 1);
    }
    return { index: this.index, previous: this.previous, start: this.start, following: this.following };
  }
}

class Levels {
  constructor(readonly low: number, readonly high: number, readonly cct: number, readonly cctEnd: number,
    readonly seed: number, readonly curve: (u: number) => number) {}
  at(u: number): number { return this.low + (this.high - this.low) * u; }
  target(brightness: number, cct = this.cct): EffectTarget {
    return { brightness: quantizeBrightness(brightness), cct: quantizeCct(cct) };
  }
}

abstract class Program {
  abstract sample(t: number, final?: boolean): EffectTarget;
  nextChange(t: number): number | null { return t + SAMPLE_INTERVAL; }
}

class Blink extends Program {
  constructor(private readonly levels: Levels, private readonly period: number, private readonly duty: number) { super(); }
  sample(t: number): EffectTarget {
    const phase = (t / this.period) % 1;
    return this.levels.target(phase < this.duty ? this.levels.high : this.levels.low);
  }
  override nextChange(t: number): number | null {
    const start = Math.floor(t / this.period) * this.period;
    return firstAfter(t, [start + this.duty * this.period, start + this.period,
      start + this.period + this.duty * this.period, start + 2 * this.period]);
  }
}

class Breathe extends Program {
  constructor(private readonly levels: Levels, private readonly period: number) { super(); }
  sample(t: number): EffectTarget {
    const phase = (t / this.period) % 1, u = (1 - Math.cos(2 * Math.PI * phase)) / 2;
    return this.levels.target(this.levels.at(this.levels.curve(u)));
  }
}

class Fade extends Program {
  constructor(private readonly levels: Levels, private readonly duration: number, private readonly rising: boolean) { super(); }
  sample(t: number): EffectTarget {
    let progress = Math.min(1, Math.max(0, t / this.duration));
    if (!this.rising) progress = 1 - progress;
    return this.levels.target(this.levels.at(this.levels.curve(progress)));
  }
  override nextChange(t: number): number | null { return t < this.duration ? t + SAMPLE_INTERVAL : null; }
}

class CctSweep extends Program {
  constructor(private readonly levels: Levels, private readonly period: number) { super(); }
  sample(t: number): EffectTarget {
    const phase = (t / this.period) % 1, span = this.levels.cctEnd - this.levels.cct;
    return this.levels.target(this.levels.high, this.levels.cct + span * (1 - Math.abs(2 * phase - 1)));
  }
}

type Options<P extends ParsedEffectConfig['pattern']> = Extract<ParsedEffectConfig, { pattern: P }>;

class Pulse extends Program {
  private readonly beat: number;
  private readonly second: number;
  constructor(private readonly levels: Levels, private readonly options: Options<'pulse'>) {
    super();
    this.beat = 60 / options.bpm;
    this.second = Math.min(0.35, Math.max(0.2, 0.33 * this.beat));
  }
  sample(t: number): EffectTarget {
    const options = this.options, k = Math.floor(t / this.beat), x = Math.max(0, t - k * this.beat);
    const accent = options.accentEvery === 0 || k % options.accentEvery === 0;
    const a = accent ? 1 : options.accentRatio;
    let envelope: number;
    if (options.style === 'beat') envelope = a * Math.exp(-x / (options.decay * this.beat));
    else {
      const first = a * Math.exp(-x / 0.12);
      envelope = x < this.second ? first : Math.max(first, 0.65 * a * Math.exp(-(x - this.second) / 0.12));
    }
    return this.levels.target(this.levels.at(envelope));
  }
  override nextChange(t: number): number | null {
    const start = Math.floor(t / this.beat) * this.beat;
    const onsets = [start, start + this.beat, start + 2 * this.beat];
    if (this.options.style === 'heartbeat') onsets.push(start + this.second, start + this.beat + this.second);
    const onset = firstAfter(t, onsets), grid = t + SAMPLE_INTERVAL;
    return onset === null ? grid : Math.min(grid, onset);
  }
}

class Flicker extends Program {
  private readonly h1: number;
  private readonly gusts: Renewal;
  constructor(private readonly levels: Levels, private readonly options: Options<'flicker'>) {
    super();
    const candle = options.style === 'candle', mean = candle ? 8 : 3;
    this.h1 = Math.max(0.2, (candle ? 0.45 : 0.30) / options.speed);
    this.gusts = new Renewal(i => expGap(mean, effectRandom(levels.seed, 3, i), 0.5, 30));
  }
  private gust(t: number): number {
    const located = this.gusts.locate(t);
    let strength = 0;
    const events: [number, number | null][] = [[located.index, located.start], [located.index - 1, located.previous]];
    for (const [index, start] of events) {
      if (index < 0 || start === null) continue;
      const length = 0.3 + 0.5 * effectRandom(this.levels.seed, 4, index);
      if (start <= t && t < start + length) strength = Math.max(strength, Math.sin(Math.PI * (t - start) / length) ** 2);
    }
    return strength;
  }
  sample(t: number): EffectTarget {
    const levels = this.levels, options = this.options;
    const n = 0.6 * noise(levels.seed, 1, t, this.h1) + 0.4 * noise(levels.seed, 2, t, 0.2);
    let dip = options.depth * n;
    if (options.gusts) dip += 0.35 * options.gusts * this.gust(t);
    dip = Math.min(1, Math.max(0, dip));
    const warmth = options.cctCoupling * (1 - noise(levels.seed, 5, t, 1.5));
    return levels.target(levels.high - (levels.high - levels.low) * dip, levels.cct + (levels.cctEnd - levels.cct) * warmth);
  }
}

class Sunrise extends Program {
  constructor(private readonly levels: Levels, private readonly duration: number, private readonly rising: boolean) { super(); }
  sample(t: number): EffectTarget {
    const u = Math.min(1, Math.max(0, t / this.duration)), w = this.rising ? u : 1 - u;
    return this.levels.target(this.levels.at(this.levels.curve(w)),
      this.levels.cct + (this.levels.cctEnd - this.levels.cct) * smooth(w));
  }
  override nextChange(t: number): number | null { return t < this.duration ? t + SAMPLE_INTERVAL : null; }
}

type Segment = readonly [begin: number, end: number, level: number];

class Lightning extends Program {
  private readonly strikes: Renewal;
  constructor(private readonly levels: Levels, private readonly options: Options<'lightning'>) {
    super();
    this.strikes = new Renewal(i => expGap(options.interval, effectRandom(levels.seed, 1, i), 1.5, 4 * options.interval));
  }
  private segments(index: number, start: number): Segment[] {
    const levels = this.levels, count = 1 + Math.floor(this.options.flashesMax * effectRandom(levels.seed, 2, index));
    const segments: Segment[] = [];
    let begin = start;
    for (let flash = 0; flash < count; ++flash) {
      const key = 16 * index + flash, end = begin + 0.1;
      segments.push([begin, end, levels.at(0.6 + 0.4 * effectRandom(levels.seed, 3, key))]);
      begin = end;
      if (flash < count - 1) begin = end + (0.1 + 0.15 * effectRandom(levels.seed, 4, key));
    }
    segments.push([begin, begin + 0.1, levels.at(0.25)]);
    return segments;
  }
  sample(t: number): EffectTarget {
    const located = this.strikes.locate(t);
    let level = this.levels.low;
    if (located.start !== null) for (const [begin, end, value] of this.segments(located.index, located.start)) {
      if (begin <= t && t < end) { level = value; break; }
    }
    return this.levels.target(level);
  }
  override nextChange(t: number): number | null {
    const located = this.strikes.locate(t), edges = [located.following];
    if (located.start !== null) for (const [begin, end] of this.segments(located.index, located.start)) edges.push(begin, end);
    return firstAfter(t, edges);
  }
}

class Emergency extends Program {
  private readonly cycle: number;
  private readonly half: number;
  private readonly period: number;
  private readonly switchAt: number;
  private readonly edges: number[];
  constructor(private readonly levels: Levels, private readonly options: Options<'emergency'>) {
    super();
    this.cycle = options.flashOn + options.flashOff;
    this.half = options.flashes * this.cycle + options.gap;
    this.period = 2 * this.half;
    this.switchAt = (options.flashes - 1) * this.cycle + options.flashOn + 0.1;
    const edges = new Set([this.switchAt]);
    for (let flash = 0; flash < options.flashes; ++flash) {
      edges.add(flash * this.cycle);
      edges.add(flash * this.cycle + options.flashOn);
    }
    this.edges = [...new Set([...edges, ...[...edges].map(edge => this.half + edge), this.period])].sort((a, b) => a - b);
  }
  sample(t: number): EffectTarget {
    const u = t % this.period, half = u >= this.half ? 1 : 0, v = u - half * this.half;
    const flash = Math.floor(v / this.cycle), lit = flash < this.options.flashes && v - flash * this.cycle < this.options.flashOn;
    const side = (half + (v >= this.switchAt ? 1 : 0)) % 2;
    return this.levels.target(lit ? this.levels.high : this.levels.low, side === 0 ? this.levels.cct : this.levels.cctEnd);
  }
  override nextChange(t: number): number | null {
    const base = t - t % this.period;
    return firstAfter(t, [...this.edges.map(edge => base + edge), base + this.period + this.edges[0]!]);
  }
}

class Paparazzi extends Program {
  private readonly flashes: Renewal;
  constructor(private readonly levels: Levels, private readonly options: Options<'paparazzi'>) {
    super();
    this.flashes = new Renewal(i => expGap(options.interval, effectRandom(levels.seed, 1, i), 0.2, 6 * options.interval));
  }
  private spans(index: number, start: number): Segment[] {
    const level = this.levels.at(0.8 + 0.2 * effectRandom(this.levels.seed, 2, index));
    const spans: Segment[] = [[start, start + 0.1, level]];
    if (effectRandom(this.levels.seed, 3, index) < this.options.burst) spans.push([start + 0.2, start + 0.3, level]);
    return spans;
  }
  private active(t: number): Segment[] {
    const located = this.flashes.locate(t), spans: Segment[] = [];
    const events: [number, number | null][] = [[located.index, located.start], [located.index - 1, located.previous]];
    for (const [index, start] of events) if (index >= 0 && start !== null) spans.push(...this.spans(index, start));
    return spans;
  }
  sample(t: number): EffectTarget {
    let level = this.levels.low;
    for (const [begin, end, value] of this.active(t)) if (begin <= t && t < end) level = Math.max(level, value);
    return this.levels.target(level);
  }
  override nextChange(t: number): number | null {
    const edges = [this.flashes.locate(t).following];
    for (const [begin, end] of this.active(t)) edges.push(begin, end);
    return firstAfter(t, edges);
  }
}

class Television extends Program {
  private readonly cuts: Renewal;
  constructor(private readonly levels: Levels, private readonly options: Options<'tv'>) {
    super();
    this.cuts = new Renewal(i => expGap(options.interval, effectRandom(levels.seed, 1, i), 0.4, 4 * options.interval), true);
  }
  sample(t: number): EffectTarget {
    const levels = this.levels, motion = this.options.motion, scene = this.cuts.locate(t).index;
    let level = levels.at(0.25 + 0.75 * effectRandom(levels.seed, 2, scene));
    if (motion) level *= 1 + 0.15 * motion * (2 * noise(levels.seed, 4, t, 0.25) - 1);
    const cct = levels.cct + (levels.cctEnd - levels.cct) * effectRandom(levels.seed, 3, scene);
    return levels.target(Math.min(levels.high, Math.max(levels.low, level)), cct);
  }
  override nextChange(t: number): number | null {
    const following = this.cuts.locate(t).following;
    return this.options.motion ? Math.min(t + SAMPLE_INTERVAL, following) : following;
  }
}

class Morse extends Program {
  private readonly starts: number[];
  private readonly ends: number[];
  private readonly edges: number[];
  private readonly period: number;
  constructor(private readonly levels: Levels, private readonly options: Options<'morse'>) {
    super();
    const { spans, units } = morseSpans(options.text);
    this.starts = spans.map(([start]) => start * options.unit);
    this.ends = spans.map(([, end]) => end * options.unit);
    this.edges = [...this.starts, ...this.ends].sort((a, b) => a - b);
    this.period = (units + 7) * options.unit;
  }
  sample(t: number, final = false): EffectTarget {
    let u = t;
    if (this.options.loop) {
      u = t % this.period;
      if (final && t > 0 && u === 0) u = this.period;
    }
    const index = bisectRight(this.starts, u) - 1, lit = index >= 0 && u < this.ends[index]!;
    return this.levels.target(lit ? this.levels.high : this.levels.low);
  }
  override nextChange(t: number): number | null {
    if (!this.options.loop) return this.edges[bisectRight(this.edges, t)] ?? null;
    const base = t - t % this.period;
    return firstAfter(t, [...this.edges.map(edge => base + edge), base + this.period, base + 2 * this.period]);
  }
}

class Keyframes extends Program {
  private readonly times: number[] = [];
  private readonly values: EffectTarget[] = [];
  private readonly period: number;
  constructor(private readonly options: Options<'keyframes'>, baseline: EffectTarget) {
    super();
    let { brightness, cct } = baseline;
    for (const frame of options.keyframes) {
      brightness = frame.brightness ?? brightness;
      cct = frame.cct ?? cct;
      this.times.push(frame.at);
      this.values.push({ brightness, cct });
    }
    this.period = this.times[this.times.length - 1]!;
  }
  sample(t: number, final = false): EffectTarget {
    let u = Math.min(t, this.period);
    if (this.options.loop) {
      u = t % this.period;
      if (final && t > 0 && u === 0) u = this.period;
    }
    const index = bisectRight(this.times, u);
    let brightness: number, cct: number;
    if (index >= this.times.length) ({ brightness, cct } = this.values[this.values.length - 1]!);
    else {
      const previous = this.values[index - 1]!, next = this.values[index]!, ease = this.options.keyframes[index]!.ease;
      ({ brightness, cct } = previous);
      if (ease !== 'hold') {
        const start = this.times[index - 1]!, progress = (u - start) / (this.times[index]! - start);
        const f = ease === 'smooth' ? smooth(progress) : ease === 'perceptual' ? perceptual(progress) : progress;
        brightness += (next.brightness - brightness) * f;
        cct += (next.cct - cct) * f;
      }
    }
    return { brightness: quantizeBrightness(brightness), cct: quantizeCct(cct) };
  }
  override nextChange(t: number): number | null {
    if (!this.options.loop && t >= this.period) return null;
    const u = this.options.loop ? t % this.period : t;
    const index = Math.min(bisectRight(this.times, u), this.times.length - 1);
    const boundary = t - u + this.times[index]!;
    if (boundary <= t) return t + SAMPLE_INTERVAL;
    const previous = this.values[index - 1]!, next = this.values[index]!;
    if (this.options.keyframes[index]!.ease === 'hold' || previous.brightness === next.brightness && previous.cct === next.cct) return boundary;
    return Math.min(t + SAMPLE_INTERVAL, boundary);
  }
}

function build(config: ParsedEffectConfig, levels: Levels, baseline: EffectTarget): Program {
  switch (config.pattern) {
    case 'blink': return new Blink(levels, config.period, config.duty);
    case 'breathe': return new Breathe(levels, config.period);
    case 'fade-in': case 'fade-out': return new Fade(levels, config.duration!, config.pattern === 'fade-in');
    case 'cct-sweep': return new CctSweep(levels, config.period);
    case 'pulse': return new Pulse(levels, config);
    case 'flicker': return new Flicker(levels, config);
    case 'sunrise': return new Sunrise(levels, config.duration!, config.direction === 'rise');
    case 'lightning': return new Lightning(levels, config);
    case 'emergency': return new Emergency(levels, config);
    case 'paparazzi': return new Paparazzi(levels, config);
    case 'tv': return new Television(levels, config);
    case 'morse': return new Morse(levels, config);
    case 'keyframes': return new Keyframes(config, baseline);
  }
}

/** Resolve validated defaults against a verified G60 brightness/CCT snapshot. No I/O. */
export function resolveEffect(config: EffectConfig, baseline: { brightness: number | null; cct: number | null }): ResolvedEffect {
  const parsed = parseEffectConfig(config), { brightness, cct } = baseline;
  if (brightness === null || !Number.isInteger(brightness) || brightness < 0 || brightness > 100) {
    throw new RangeError('Effect baseline brightness must be a whole percent between 0 and 100');
  }
  if (cct === null || !Number.isInteger(cct) || cct % 100 !== 0 || cct < 2700 || cct > 6500) {
    throw new RangeError('Effect baseline CCT must be a multiple of 100 between 2700 and 6500 K');
  }
  const maximum = parsed.maxBrightness ?? (brightness || 5);
  if (parsed.minBrightness > maximum) throw new RangeError('maxBrightness must be >= minBrightness (including its baseline default)');
  const resolved: ParsedEffectConfig = { ...parsed, maxBrightness: maximum, cct: parsed.cct ?? cct };
  const levels = new Levels(resolved.minBrightness, maximum, resolved.cct!, resolved.cctEnd, resolved.seed,
    resolved.curve === 'perceptual' ? perceptual : u => u);
  const program = build(resolved, levels, { brightness, cct });
  const duration = resolved.duration;
  return {
    config: resolved, duration, restoreOnComplete: resolved.restoreOnComplete,
    target(elapsedSeconds) {
      const final = duration !== null && elapsedSeconds >= duration;
      return program.sample(final ? duration! : elapsedSeconds, final);
    },
    sample(tSeconds, final = false) { return program.sample(tSeconds, final); },
    nextChange(elapsedSeconds) { return program.nextChange(elapsedSeconds); },
  };
}
