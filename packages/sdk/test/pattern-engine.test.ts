import { describe, expect, test } from 'bun:test';
import vectors from '../../../tests/fixtures/effect-vectors.json';
import { effectHash32, effectRandom, quantizeBrightness, quantizeCct, resolveEffect } from '../src/pattern-engine';
import type { EffectConfig } from '../src/types';

const baseline = vectors.baseline;
const resolve = (config: EffectConfig) => resolveEffect(config, baseline);

describe('shared Python/TypeScript effect vectors', () => {
  test('the unchanged fixture covers 30 configurations and 691 samples', () => {
    expect(vectors.cases).toHaveLength(30);
    expect(vectors.cases.reduce((count, item) => count + item.samples.length, 0)).toBe(691);
  });
  vectors.cases.forEach((item, index) => {
    test(`${index + 1}: ${JSON.stringify(item.config)} agrees forwards, backwards, and after skipped samples`, () => {
      const effect = resolve(item.config as EffectConfig), samples = item.samples;
      const sequences = [samples, [...samples].reverse(),
        [...samples.filter((_, i) => i % 3 === 0), ...samples.filter((_, i) => i % 3 === 1)]];
      for (const sequence of sequences) for (const sample of sequence) {
        const [elapsed, brightness, cct] = sample as [number, number, number];
        expect({ elapsed, target: effect.target(elapsed) }).toEqual({ elapsed, target: { brightness, cct } });
      }
    });
  });
});

describe('shared arithmetic and baseline resolution', () => {
  test('uint32 hash and counter randomness match Python pinned values', () => {
    expect([0, 1, 2, 0x7fffffff, 0xffffffff, 123456789].map(effectHash32)).toEqual([
      0, 1753845952, 3507691905, 2368339896, 1734902346, 2834422664,
    ]);
    expect(effectHash32(2 ** 32 + 1)).toBe(effectHash32(1));
    expect(effectRandom(1, 0, 0)).toBe(0.40834908187389374);
    expect(effectRandom(1, 1, 0)).toBe(0.6268993967678398);
    expect(effectRandom(1, 1, 1)).toBe(0.4974512110929936);
    expect(effectRandom(0, 0, 0)).toBe(0);
    expect(effectRandom(0xffffffff, 5, 123456)).toBe(0.3603505159262568);
  });
  test('brightness and CCT round half up and clamp to valid G60 values', () => {
    expect([2.5, 2.4999, 0.5, -0.3, 99.5, 100.6].map(quantizeBrightness)).toEqual([3, 2, 1, 0, 100, 100]);
    expect([4550, 4549.99, 3650, 2600, 6551, 2649.9].map(quantizeCct)).toEqual([4600, 4500, 3700, 2700, 6500, 2700]);
  });
  test('resolves baseline defaults, including the zero-brightness fallback', () => {
    const input = { pattern: 'blink' } as const, effect = resolve(input);
    expect(input).toEqual({ pattern: 'blink' });
    expect(effect.config.pattern).toBe('blink');
    expect(effect.config.maxBrightness).toBe(7);
    expect(effect.config.cct).toBe(5000);
    expect(effect.target(0)).toEqual({ brightness: 7, cct: 5000 });
    expect(resolveEffect(input, { brightness: 0, cct: 3200 }).target(0)).toEqual({ brightness: 5, cct: 3200 });
    expect(resolve({ pattern: 'flicker' }).config.cct).toBe(2700);
    expect(() => resolve({ pattern: 'blink', minBrightness: 8 })).toThrow(RangeError);
    expect(resolve({ pattern: 'blink', minBrightness: 8, maxBrightness: 20 }).target(0).brightness).toBe(20);
  });
  test('rejects unverified or invalid baseline numbers even when the config overrides them', () => {
    for (const brightness of [null, -1, 101, 0.5, NaN, Infinity]) {
      expect(() => resolveEffect({ pattern: 'blink', maxBrightness: 20 }, { brightness, cct: 5000 })).toThrow(RangeError);
    }
    for (const cct of [null, 2600, 6600, 4550, NaN, Infinity]) {
      expect(() => resolveEffect({ pattern: 'blink', cct: 3000 }, { brightness: 7, cct })).toThrow(RangeError);
    }
    expect(() => resolve({ pattern: 'blink', duty: 0 })).toThrow();
  });
});

describe('finite endpoints and frame/message semantics', () => {
  test('target clamps elapsed to duration while sample exposes the underlying program', () => {
    const blink = resolve({ pattern: 'blink', duration: 4, period: 4, maxBrightness: 20 });
    expect(blink.target(4)).toEqual({ brightness: 20, cct: 5000 });
    expect(blink.target(6)).toEqual(blink.target(4));
    expect(blink.sample(6)).toEqual({ brightness: 0, cct: 5000 });
    const fade = resolve({ pattern: 'fade-in', duration: 2, maxBrightness: 20 });
    expect(fade.restoreOnComplete).toBe(false);
    expect(fade.target(1001)).toEqual({ brightness: 20, cct: 5000 });
    expect(fade.nextChange(2)).toBeNull();
  });
  test('Morse uses ASCII case-insensitive spans and caps nonlooping natural duration', () => {
    const once = resolve({ pattern: 'morse', text: '  e  E ', unit: 0.2, loop: false, duration: 10, maxBrightness: 9 });
    expect(once.duration).toBe(1.8);
    expect(once.restoreOnComplete).toBe(false);
    expect([0, 0.2, 1.59, 1.6, 1.8].map(t => once.target(t).brightness)).toEqual([9, 0, 0, 9, 0]);
    expect(once.nextChange(1.8)).toBeNull();
    const loop = resolve({ pattern: 'morse', text: 'E', unit: 0.25, duration: 2, maxBrightness: 9 });
    expect(loop.sample(2)).toEqual({ brightness: 9, cct: 5000 });
    expect(loop.sample(2, true)).toEqual({ brightness: 0, cct: 5000 });
    expect(loop.target(2)).toEqual({ brightness: 0, cct: 5000 });
    const partial = resolve({ pattern: 'morse', text: 'T', unit: 0.25, duration: 2.6, maxBrightness: 9 });
    expect(partial.target(2.6)).toEqual(partial.sample(2.6));
    expect(partial.target(2.6).brightness).toBe(9);
  });
  test('keyframes carry baseline values and use the arriving frame ease', () => {
    const effect = resolve({ pattern: 'keyframes', loop: false, keyframes: [
      { at: 0 }, { at: 1, brightness: 27 }, { at: 2, cct: 3000, ease: 'hold' },
      { at: 3, brightness: 0, cct: 6500, ease: 'smooth' },
    ] });
    expect(effect.duration).toBe(3);
    expect(effect.restoreOnComplete).toBe(false);
    expect(effect.target(0)).toEqual(baseline);
    expect(effect.target(0.5)).toEqual({ brightness: 17, cct: 5000 });
    expect(effect.target(1.9)).toEqual({ brightness: 27, cct: 5000 });
    expect(effect.target(2)).toEqual({ brightness: 27, cct: 3000 });
    expect(effect.target(2.5)).toEqual({ brightness: 14, cct: 4800 });
    expect(effect.target(1001)).toEqual({ brightness: 0, cct: 6500 });
    expect(effect.nextChange(1.2)).toBe(2);
    expect(effect.nextChange(3)).toBeNull();
    const zero = resolveEffect({ pattern: 'keyframes', keyframes: [{ at: 0 }, { at: 1 }] }, { brightness: 0, cct: 3200 });
    expect(zero.target(0)).toEqual({ brightness: 0, cct: 3200 });
  });
  test('looping keyframes avoid wrapping only at an exact final boundary', () => {
    const config = { pattern: 'keyframes', duration: 2, keyframes: [{ at: 0, brightness: 0 }, { at: 1, brightness: 100 }] } as const;
    const effect = resolve(config);
    expect(effect.sample(2).brightness).toBe(0);
    expect(effect.sample(2, true).brightness).toBe(100);
    expect(effect.target(1001).brightness).toBe(100);
    const partial = resolve({ ...config, duration: 2.5 });
    expect(partial.target(2.5).brightness).toBe(50);
    expect(partial.target(1001)).toEqual(partial.sample(2.5));
  });
  test('emergency switches CCT 0.1 seconds after the last flash turns dark', () => {
    const effect = resolve({ pattern: 'emergency', maxBrightness: 50, duration: null });
    expect(effect.target(0.45)).toEqual({ brightness: 0, cct: 2700 });
    expect(effect.target(0.54)).toEqual({ brightness: 0, cct: 2700 });
    expect(effect.target(0.55)).toEqual({ brightness: 0, cct: 6500 });
    expect(effect.target(0.9)).toEqual({ brightness: 50, cct: 6500 });
    expect(effect.nextChange(0.46)).toBe(0.5499999999999999);
  });
});

describe('next-change scheduling and renewal rewinds', () => {
  test('blink and pulse expose exact edges between the continuous sampling grid', () => {
    const blink = resolve({ pattern: 'blink', period: 0.2 });
    expect(blink.nextChange(0)).toBe(0.1);
    expect(blink.nextChange(0.1)).toBe(0.2);
    expect(blink.nextChange(0.15)).toBe(0.2);
    const heart = resolve({ pattern: 'pulse' });
    expect(heart.nextChange(0.3)).toBe(0.33);
    expect(heart.nextChange(0.05)).toBeCloseTo(0.15);
    expect(resolve({ pattern: 'breathe' }).nextChange(0.3)).toBe(0.4);
    expect(resolve({ pattern: 'sunrise' }).nextChange(900)).toBeNull();
  });
  test('stochastic event cursors agree with fresh instances after long skips and rewinds', () => {
    const configs: EffectConfig[] = [
      { pattern: 'flicker', style: 'fire', gusts: 1, duration: null },
      { pattern: 'lightning', flashesMax: 4, interval: 2, duration: null },
      { pattern: 'paparazzi', burst: 1, interval: 0.33, duration: null },
      { pattern: 'tv', motion: 0, interval: 0.5, duration: null },
      { pattern: 'tv', motion: 1, interval: 0.5, duration: null },
    ];
    for (const config of configs) {
      const effect = resolve(config);
      for (const t of [10001, 0, 1001, 0.1, 500, 4, 5000, 0, 10]) {
        const fresh = resolve(config), expected = fresh.target(t), next = fresh.nextChange(t);
        expect(effect.target(t)).toEqual(expected);
        expect(effect.nextChange(t)).toBe(next);
        expect(next).not.toBeNull();
        expect(next!).toBeGreaterThan(t);
        if (config.pattern === 'tv') {
          expect(expected.brightness).toBeGreaterThanOrEqual(effect.config.minBrightness);
          expect(expected.brightness).toBeLessThanOrEqual(effect.config.maxBrightness!);
        }
      }
    }
  });
  test('event-driven next changes reach seeded flashes and scene cuts', () => {
    const firstStrike = 5.915443091672376;
    const lightning = resolve({ pattern: 'lightning', duration: null, maxBrightness: 80 });
    expect(lightning.nextChange(0)).toBe(firstStrike);
    expect(lightning.target(firstStrike - 0.01).brightness).toBe(0);
    expect(lightning.target(firstStrike + 0.05).brightness).toBeGreaterThan(0);
    expect(lightning.nextChange(firstStrike)).toBe(firstStrike + 0.1);
    const tv = resolve({ pattern: 'tv', motion: 0, duration: null, maxBrightness: 40 });
    expect(tv.target(0)).toEqual({ brightness: 17, cct: 6300 });
    const cut = tv.nextChange(0)!;
    expect(tv.target(cut - 0.01)).toEqual(tv.target(0));
    expect(tv.nextChange(cut)!).toBeGreaterThan(cut);
  });
});
