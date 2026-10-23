import { useRef } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { EffectEase } from '@lighttime/sdk';
import { colors as c } from './palette';
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
      <Text style={s.title}>Scene sequence</Text>
      <Text style={s.hint}>Each frame sets a moment in the loop. Blank brightness or temperature carries the previous value. Easing shapes the arrival at a frame.</Text>
    </View>
    {drafts.map((frame, index) => <View key={frame.id} style={s.frame}>
      <View style={s.heading}>
        <Text style={s.title}>Frame {index + 1}</Text>
        {index > 0 && <Pressable accessibilityRole="button" accessibilityLabel={`Remove frame ${index + 1}`}
          accessibilityState={{ disabled: drafts.length <= 2 }} disabled={drafts.length <= 2}
          onPress={() => update(drafts.filter(item => item.id !== frame.id))}
          style={({ pressed }) => [s.remove, drafts.length <= 2 && s.disabled, pressed && s.pressed]}>
          <Text style={s.removeText}>Remove</Text>
        </Pressable>}
      </View>
      <Field label="Time · seconds" accessibilityLabel={`Frame ${index + 1} time in seconds`} value={frame.at}
        editable={index !== 0} onChange={at => edit(frame.id, { at })} hint={index === 0 ? 'The sequence starts at 0 seconds.' : 'At least 0.1 s after the previous frame; up to 3600 s.'} />
      <View style={s.outputs}>
        <View style={s.output}><Field label="Brightness · %" accessibilityLabel={`Frame ${index + 1} brightness percent`}
          value={frame.brightness} placeholder="Carry" onChange={brightness => edit(frame.id, { brightness })} hint="0–100, whole percent" /></View>
        <View style={s.output}><Field label="Temperature · K" accessibilityLabel={`Frame ${index + 1} temperature kelvin`}
          value={frame.cct} placeholder="Carry" onChange={cct => edit(frame.id, { cct })} hint="2700–6500, 100 K steps" /></View>
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
    <Text style={s.hint}>{drafts.length} / {MAX_KEYFRAMES} frames · minimum 2 · first frame stays at 0 s</Text>
  </View>;
}

const s = StyleSheet.create({
  editor: { gap: 14 }, intro: { gap: 8 }, title: { color: c.text, fontSize: 15, lineHeight: 22, fontWeight: '500' },
  hint: { color: c.muted, fontSize: 12, lineHeight: 19 }, frame: { borderWidth: 1, borderColor: c.line, borderRadius: 16, padding: 14, gap: 16 },
  heading: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  remove: { minHeight: 48, paddingHorizontal: 12, paddingVertical: 12, justifyContent: 'center', backgroundColor: c.raised, borderRadius: 12 },
  removeText: { color: c.text, fontSize: 13 }, outputs: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  output: { flexGrow: 1, flexBasis: 130 }, easing: { gap: 8 }, chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  disabled: { opacity: 0.4 }, pressed: { opacity: 0.65 },
});
