import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { createEffectForm, EFFECT_CHOICES, effectConfigFromForm } from '../effectForm';
import type { EffectChoiceId } from '../effectForm';
import { PATTERN_GROUPS } from './catalog';
import { colors as c } from './palette';
import { previewEffect } from './preview';
import type { PatternPreview } from './preview';
import { Waveform } from './Waveform';

const thumbnails = Object.fromEntries(EFFECT_CHOICES.map(choice => [choice.id,
  previewEffect(effectConfigFromForm(createEffectForm(choice.id))).preview,
])) as Record<EffectChoiceId, PatternPreview | null>;

export function PatternBrowser({ selected, preview, onSelect }: {
  selected: EffectChoiceId; preview: PatternPreview | null; onSelect: (id: EffectChoiceId) => void;
}) {
  const [groupIndex, setGroupIndex] = useState(() => PATTERN_GROUPS.findIndex(group => group.ids.some(id => id === selected)));
  const group = PATTERN_GROUPS[groupIndex]!;
  return <View style={s.browser}>
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.categories}>
      {PATTERN_GROUPS.map((item, index) => <Pressable key={item.title} accessibilityRole="tab"
        accessibilityState={{ selected: groupIndex === index }} accessibilityLabel={`${item.title}, ${item.ids.length} presets`}
        onPress={() => setGroupIndex(index)} style={({ pressed }) => [s.category, groupIndex === index && s.categorySelected, pressed && s.pressed]}>
        <Text style={[s.categoryLabel, groupIndex === index && s.selectedLabel]}>{item.title}</Text>
        <Text style={s.count}>{item.ids.length}</Text>
      </Pressable>)}
    </ScrollView>
    <ScrollView key={group.title} horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={s.cards}>
      {group.ids.map(id => {
        const choice = EFFECT_CHOICES.find(item => item.id === id)!;
        const active = id === selected;
        const waveform = active ? preview : thumbnails[id];
        return <Pressable key={id} accessibilityRole="button" accessibilityState={{ selected: active }}
          accessibilityLabel={`${choice.title}. ${choice.note}`} accessibilityHint="Select to preview and tune. The light stays unchanged."
          onPress={() => onSelect(id)} style={({ pressed }) => [s.card, active && s.cardSelected, pressed && s.pressed]}>
          <Text style={s.cardTitle}>{choice.title}</Text>
          {waveform ? <Waveform preview={waveform} compact /> : <View style={s.emptyWave}><Text style={s.count}>Check tuning</Text></View>}
          <Text style={[s.cardCaption, active && s.selectedLabel]}>{active ? 'Selected' : 'Preset preview'}{active ? '  ✓' : ''}</Text>
        </Pressable>;
      })}
    </ScrollView>
  </View>;
}

const s = StyleSheet.create({
  browser: { gap: 14 }, categories: { gap: 6, paddingBottom: 2 },
  category: { minHeight: 48, paddingHorizontal: 12, paddingVertical: 12, borderRadius: 24, flexDirection: 'row', alignItems: 'center', gap: 8 },
  categorySelected: { backgroundColor: c.raised }, categoryLabel: { color: c.muted, fontSize: 14, fontWeight: '500' },
  selectedLabel: { color: c.accent }, count: { color: c.muted, fontSize: 12 },
  cards: { gap: 10, paddingBottom: 2 }, card: { width: 164, minHeight: 142, padding: 15, borderRadius: 18,
    borderWidth: 1, borderColor: c.line, backgroundColor: c.surface, justifyContent: 'space-between', gap: 16 },
  cardSelected: { backgroundColor: c.selected, borderColor: c.accent }, cardTitle: { color: c.text, fontSize: 16, lineHeight: 22, fontWeight: '500' },
  cardCaption: { color: c.muted, fontSize: 12, lineHeight: 18 }, emptyWave: { height: 34, justifyContent: 'center' }, pressed: { opacity: 0.65 },
});
