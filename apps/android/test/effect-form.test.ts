import { expect, test } from 'bun:test';
import { EFFECT_PRESETS, parseEffectConfig } from '@lighttime/sdk';
import { createEffectForm, EFFECT_CHOICES, effectConfigFromForm } from '../src/effectForm';

test('all eleven shared presets and original five patterns make valid requested forms', () => {
  expect(EFFECT_CHOICES).toHaveLength(16);
  for (const choice of EFFECT_CHOICES) expect(() => effectConfigFromForm(createEffectForm(choice.id))).not.toThrow();
});

test('pattern temperatures use shared defaults instead of the current hardware baseline', () => {
  expect(createEffectForm('candle').cct).toBe('2700');
  expect(createEffectForm('candle').cctEnd).toBe('3200');
  expect(createEffectForm('sunrise15').cct).toBe('2700');
  expect(createEffectForm('sunrise15').cctEnd).toBe('6500');
  expect(effectConfigFromForm(createEffectForm('breathe')).cct).toBeUndefined();
});

test('editing a keyframe demonstration cannot mutate the deeply frozen SDK preset', () => {
  const form = createEffectForm('keyframes');
  if (form.base.pattern !== 'keyframes') throw new Error('Expected keyframes');
  expect(form.base).not.toBe(EFFECT_PRESETS.keyframes);
  expect(form.base.keyframes).not.toBe((EFFECT_PRESETS.keyframes as typeof form.base).keyframes);
  expect(form.base.keyframes[0]).not.toBe((EFFECT_PRESETS.keyframes as typeof form.base).keyframes[0]);
});

test('blink accepts 0.2 seconds with even phases and rejects shorter phases', () => {
  const form = { ...createEffectForm('blink'), period: '0.2' };
  expect(parseEffectConfig(effectConfigFromForm(form)).period).toBe(0.2);
  expect(() => effectConfigFromForm({ ...form, period: '0.19' })).toThrow();
  expect(() => effectConfigFromForm({ ...form, base: { pattern: 'blink', duty: 0.4 } })).toThrow();
});

test('shared validation rejects fractional output, blank values, invalid text and unbounded fades', () => {
  const form = createEffectForm('candle');
  expect(() => effectConfigFromForm({ ...form, minBrightness: '2.5' })).toThrow();
  expect(() => effectConfigFromForm({ ...form, cct: '2750' })).toThrow();
  expect(() => effectConfigFromForm({ ...form, seed: '' })).toThrow();
  expect(() => effectConfigFromForm({ ...createEffectForm('sos'), text: '⚡' })).toThrow();
  expect(() => effectConfigFromForm({ ...createEffectForm('fade-in'), untilStopped: true })).toThrow();
});
