import { useRef } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { EffectEase } from '@lighttime/sdk';
import { glass, jelly } from './materials';
import { colors as c, type as t } from './palette';
import { Chip, Field } from './TuningControls';
import { MAX_KEYFRAMES, nextKeyframeAt } from './keyframes';
import type { FrameDraft } from './keyframes';

const eases: { value: EffectEase; label: string }[] = [
  { value: 'linear', label: 'Linear' }, { value: 'smooth', label: 'Smooth' },
  { value: 'perceptual', label: 'Perceptual' }, { value: 'hold', label: 'Hold' },
];

export function KeyframeEditor({ drafts, onChange }: {
  drafts: readonly FrameDraft[]; onChange: (drafts: FrameDraft[]) => void;
}) {
  const nextId = useRef(Math.max(-1, ...drafts.map(frame => frame.id)) + 1);
  const update = onChange;
  const edit = (id: number, patch: Partial<FrameDraft>) => update(drafts.map(frame => frame.id === id ? { ...frame, ...patch } : frame));
  const nextAt = nextKeyframeAt(drafts);

  return <View style={s.editor}>
    <View style={s.intro}>
      <Text style={s.title}>Frames</Text>
      <Text style={s.hint}>Each frame is a moment in the loop. Leave a value blank to carry the previous one.</Text>
    </View>
    {drafts.map((frame, index) => <View key={frame.id} style={[s.frame, jelly('night', 0.75)]}>
      <View style={s.heading}>
        <View accessible accessibilityRole="header" accessibilityLabel={`Frame ${index + 1}`} style={s.badge}>
          <Text maxFontSizeMultiplier={1.5} style={s.badgeText}>{index + 1}</Text>
        </View>
        {index > 0 && <Pressable accessibilityRole="button" accessibilityLabel={`Remove frame ${index + 1}`}
          accessibilityState={{ disabled: drafts.length <= 2 }} disabled={drafts.length <= 2}
          onPress={() => update(drafts.filter(item => item.id !== frame.id))}
          style={({ pressed }) => [s.remove, glass, drafts.length <= 2 && s.disabled, pressed && s.pressed]}>
          <Text style={s.removeText}>Remove</Text>
        </Pressable>}
      </View>
      <Field label="Time · s" accessibilityLabel={`Frame ${index + 1} time in seconds`} value={frame.at}
        editable={index !== 0} onChange={at => edit(frame.id, { at })} hint={index === 0 ? 'Starts at 0 s' : '0.1 s after the last frame, up to 3600 s'} />
      <View style={s.outputs}>
        <View style={s.output}><Field label="Brightness · %" accessibilityLabel={`Frame ${index + 1} brightness percent`}
          value={frame.brightness} placeholder="Carry" onChange={brightness => edit(frame.id, { brightness })} hint="0–100" /></View>
        <View style={s.output}><Field label="White · K" accessibilityLabel={`Frame ${index + 1} temperature kelvin`}
          value={frame.cct} placeholder="Carry" onChange={cct => edit(frame.id, { cct })} hint="2700–6500, steps of 100" /></View>
      </View>
      {index > 0 && <View style={s.easing}>
        <Text style={s.hint}>Arrive with</Text>
        <View style={s.chips}>{eases.map(ease => <Chip key={ease.value} label={ease.label}
          selected={frame.ease === ease.value} onPress={() => edit(frame.id, { ease: ease.value })} />)}</View>
      </View>}
    </View>)}
    <Chip label="Add frame" disabled={nextAt === null} onPress={() => {
      if (nextAt !== null) update([...drafts, { id: nextId.current++, at: String(nextAt), brightness: '', cct: '', ease: 'linear' }]);
    }} />
    <Text style={s.hint}>{drafts.length} of {MAX_KEYFRAMES} frames · at least 2</Text>
  </View>;
}

const s = StyleSheet.create({
  editor: { gap: 16 }, intro: { gap: 6 },
  title: { color: c.white, fontFamily: t.medium, fontSize: 18, lineHeight: 26 },
  hint: { color: c.soft, fontFamily: t.regular, fontSize: 13, lineHeight: 19 },
  frame: { borderRadius: 30, padding: 16, gap: 16 },
  heading: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  // The badge grows with scaled text instead of cutting the numeral; its header label carries the meaning.
  badge: { minWidth: 40, minHeight: 40, borderRadius: 24, paddingHorizontal: 8, alignItems: 'center', justifyContent: 'center',
    backgroundColor: '#C81E2A', boxShadow: 'inset 0 1.5px 0 rgba(255, 228, 228, 0.7), 0 0 6px rgba(223, 33, 33, 0.5), 0 0 18px rgba(223, 33, 33, 0.3)' },
  badgeText: { color: c.white, fontFamily: t.semibold, fontSize: 16, lineHeight: 22, includeFontPadding: false },
  remove: { minHeight: 48, minWidth: 48, maxWidth: '100%', paddingHorizontal: 18, paddingVertical: 12,
    justifyContent: 'center', borderRadius: 24 },
  removeText: { color: c.pink, fontFamily: t.regular, fontSize: 14, lineHeight: 20 },
  outputs: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  output: { flexGrow: 1, flexBasis: 130 }, easing: { gap: 8 }, chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  disabled: { opacity: 0.4 }, pressed: { opacity: 0.8 },
});
