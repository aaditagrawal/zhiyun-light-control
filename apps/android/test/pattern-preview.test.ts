import { expect, test } from 'bun:test';
import { EFFECT_PRESETS, parseEffectConfig, resolveEffect } from '@lighttime/sdk';
import type { EffectConfig } from '@lighttime/sdk';
import { createEffectForm, EFFECT_CHOICES, effectConfigFromForm } from '../src/effectForm';
import { PATTERN_GROUPS } from '../src/pattern-ui/catalog';
import { draftsFromFrames, keyframesFromDrafts, MAX_KEYFRAMES, nextKeyframeAt } from '../src/pattern-ui/keyframes';
import { PREVIEW_BASELINE, previewEffect } from '../src/pattern-ui/preview';

function preview(config: EffectConfig) {
  const result = previewEffect(config);
  if (!result.preview) throw new Error(result.error);
  return result.preview;
}

test('every scene remains reachable exactly once and has 64 truthful SDK samples', () => {
  const ids = PATTERN_GROUPS.flatMap(group => [...group.ids]);
  expect(ids).toHaveLength(16);
  expect(new Set(ids).size).toBe(16);
  expect([...ids].sort()).toEqual(EFFECT_CHOICES.map(choice => choice.id).sort());
  for (const choice of EFFECT_CHOICES) {
    const config = effectConfigFromForm(createEffectForm(choice.id));
    const sampled = preview(config);
    const resolved = resolveEffect(config, PREVIEW_BASELINE);
    expect(sampled.samples).toHaveLength(64);
    expect(sampled.samples[0]!.at).toBe(0);
    expect(sampled.samples[63]!.at).toBe(sampled.seconds);
    for (const sample of sampled.samples) {
      expect({ brightness: sample.brightness, cct: sample.cct }).toEqual(resolved.target(sample.at));
    }
  }
});

test('temperature defaults stay warm or cool as specified, while legacy previews explain their assumed baseline', () => {
  const candle = preview(effectConfigFromForm(createEffectForm('candle')));
  expect(candle.samples.every(sample => sample.cct === 2700)).toBe(true);
  expect(candle.baselineNote).toBeNull();
  expect(preview(effectConfigFromForm(createEffectForm('storm'))).samples.every(sample => sample.cct === 6500)).toBe(true);
  const breathe = preview(effectConfigFromForm(createEffectForm('breathe')));
  expect(breathe.samples.every(sample => sample.cct === 4300)).toBe(true);
  expect(breathe.baselineNote).toContain('Preview baseline: 4300 K');
  const explicit = preview(effectConfigFromForm({ ...createEffectForm('breathe'), cct: '5600' }));
  expect(explicit.samples.every(sample => sample.cct === 5600)).toBe(true);
  expect(explicit.baselineNote).toBeNull();
  expect(preview(effectConfigFromForm(createEffectForm('keyframes'))).baselineNote).toBeNull();
});

test('brightness, period, seed and temperature edits change the preview without mutating presets', () => {
  const original = JSON.stringify(EFFECT_PRESETS);
  const form = { ...createEffectForm('breathe'), minBrightness: '10', maxBrightness: '80', period: '8', cct: '3200' };
  const tuned = preview(effectConfigFromForm(form));
  expect(tuned.seconds).toBe(8);
  expect(tuned.brightnessLabel).toBe('10–80%');
  expect(tuned.ceiling).toBe(80);
  expect(tuned.samples[0]!.brightness).toBe(10);
  expect(Math.max(...tuned.samples.map(sample => sample.brightness))).toBe(80);
  expect(tuned.samples.every(sample => sample.cct === 3200)).toBe(true);
  const fire = createEffectForm('fireplace');
  expect(preview(effectConfigFromForm({ ...fire, seed: '24' })).samples)
    .not.toEqual(preview(effectConfigFromForm(fire)).samples);
  expect(JSON.stringify(EFFECT_PRESETS)).toBe(original);
});

test('finite fades and sunrise show the full natural duration and final target', () => {
  for (const id of ['fade-in', 'fade-out', 'sunrise15', 'sunset'] as const) {
    const config = effectConfigFromForm({ ...createEffectForm(id), duration: '3600' });
    const sampled = preview(config);
    expect(sampled.seconds).toBe(3600);
    expect(sampled.windowLabel).toBe('Full transition · 60 min');
    expect(sampled.runLabel).toBe('60 min');
    const rising = id === 'fade-in' || id === 'sunrise15';
    expect(sampled.samples[0]!.brightness).toBe(rising ? 0 : config.maxBrightness!);
    expect(sampled.samples[63]!.brightness).toBe(rising ? config.maxBrightness! : 0);
  }
  expect(preview(effectConfigFromForm(createEffectForm('sunrise15'))).seconds).toBe(900);
});

test('long random runs use short bounded windows and short runs never preview past completion', () => {
  for (const config of [
    { pattern: 'lightning', duration: 3600, interval: 2 },
    { pattern: 'paparazzi', duration: null, interval: 0.33 },
    { pattern: 'tv', duration: 3600, interval: 0.5 },
    { pattern: 'flicker', duration: null },
  ] satisfies EffectConfig[]) {
    const sampled = preview(config);
    expect(sampled.seconds).toBeLessThanOrEqual(12);
    expect(sampled.samples).toHaveLength(64);
  }
  const short = preview({ pattern: 'breathe', period: 60, duration: 1, maxBrightness: 50 });
  expect(short.seconds).toBe(1);
  expect(short.windowLabel).toBe('Full run · 1 s');
});

test('invalid and incomplete configurations return no stale preview and never throw from rendering', () => {
  const invalid = [
    { pattern: 'blink', period: 0.2, duty: 0.4 },
    { pattern: 'flicker', cct: 2750 },
    { pattern: 'breathe', minBrightness: 20, maxBrightness: 10 },
    { pattern: 'fade-in', duration: null },
    { pattern: 'keyframes', keyframes: [{ at: 0 }, { at: Number.NaN }] },
  ] satisfies EffectConfig[];
  for (const config of invalid) {
    const result = previewEffect(config);
    expect(result.preview).toBeNull();
    expect(result.error).toBeTypeOf('string');
  }
});

test('frame editing preserves carry-forward semantics, custom easing and immutable SDK presets', () => {
  const form = createEffectForm('keyframes');
  if (form.base.pattern !== 'keyframes') throw new Error('Expected keyframes');
  const original = JSON.stringify(EFFECT_PRESETS.keyframes);
  const drafts = draftsFromFrames(form.base.keyframes);
  drafts[1] = { ...drafts[1]!, at: '2.5', brightness: '42', cct: '', ease: 'perceptual' };
  const frames = keyframesFromDrafts(drafts);
  const config = effectConfigFromForm({ ...form, base: { ...form.base, keyframes: frames } });
  const sampled = preview(config);
  expect(frames[1]).toEqual({ at: 2.5, brightness: 42, ease: 'perceptual' });
  expect(frames[1]).not.toHaveProperty('cct');
  expect(sampled.ceiling).toBe(42);
  expect(sampled.brightnessLabel).toBe('4–42%');
  expect(JSON.stringify(EFFECT_PRESETS.keyframes)).toBe(original);
  expect(frames[0]).not.toBe(form.base.keyframes[0]);
});

test('incomplete or out-of-resolution keyframe edits stay invalid instead of silently becoming zero', () => {
  const drafts = draftsFromFrames([{ at: 0, brightness: 4, cct: 2700 }, { at: 2, brightness: 16 }]);
  for (const patch of [{ at: '' }, { at: '0.05' }, { brightness: '4.5' }, { cct: '2750' }]) {
    const frames = keyframesFromDrafts([drafts[0]!, { ...drafts[1]!, ...patch }]);
    const result = previewEffect({ pattern: 'keyframes', keyframes: frames });
    expect(result.preview).toBeNull();
    expect(result.error).toBeTypeOf('string');
  }
  const carried = keyframesFromDrafts([{ ...drafts[0]!, brightness: '', cct: '' }, drafts[1]!]);
  expect(preview({ pattern: 'keyframes', keyframes: carried }).baselineNote).toContain('16% · 4300 K');
});

test('frame add/remove operations respect shared count and time limits', () => {
  const drafts = draftsFromFrames([{ at: 0 }, { at: 2 }]);
  expect(nextKeyframeAt(drafts)).toBe(3);
  expect(nextKeyframeAt([drafts[0]!, { ...drafts[1]!, at: '3599.9' }])).toBe(3600);
  expect(nextKeyframeAt([drafts[0]!, { ...drafts[1]!, at: '3600' }])).toBeNull();
  expect(nextKeyframeAt([drafts[0]!, { ...drafts[1]!, at: '' }])).toBeNull();
  const full = Array.from({ length: MAX_KEYFRAMES }, (_, id) => ({ ...drafts[0]!, id, at: String(id) }));
  expect(nextKeyframeAt(full)).toBeNull();
  expect(() => parseEffectConfig({ pattern: 'keyframes', keyframes: keyframesFromDrafts(full) })).not.toThrow();
  expect(previewEffect({ pattern: 'keyframes', keyframes: keyframesFromDrafts([drafts[0]!]) }).preview).toBeNull();
});

test('curve and blink on-time edits reach shared validation and change sampled output', () => {
  const fade = { ...createEffectForm('fade-in'), minBrightness: '0', maxBrightness: '100' };
  const linear = preview(effectConfigFromForm({ ...fade, base: { ...fade.base, curve: 'linear' } }));
  const perceptual = preview(effectConfigFromForm({ ...fade, base: { ...fade.base, curve: 'perceptual' } }));
  expect(perceptual.samples[32]!.brightness).toBeLessThan(linear.samples[32]!.brightness);
  const blink = createEffectForm('blink');
  const narrow = preview(effectConfigFromForm({ ...blink, base: { pattern: 'blink', duty: 0.25 } }));
  expect(narrow.samples.filter(sample => sample.brightness > 0).length).toBeLessThan(20);
  expect(() => effectConfigFromForm({ ...blink, base: { pattern: 'blink', duty: 0 } })).toThrow();
});
