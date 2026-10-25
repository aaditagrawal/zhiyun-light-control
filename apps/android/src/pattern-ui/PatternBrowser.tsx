import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { createEffectForm, EFFECT_CHOICES, effectConfigFromForm } from '../effectForm';
import type { EffectChoiceId } from '../effectForm';
import { PATTERN_GROUPS, SCENE_LOOKS, SCENE_ORDER } from './catalog';
import { CheckGlyph, GlassChip, LoopGlyph, Rim, glass, softShadow } from './materials';
import { colors as c, type as t } from './palette';
import { previewEffect } from './preview';
import type { PatternPreview } from './preview';
import { Waveform } from './Waveform';

const thumbnails = Object.fromEntries(EFFECT_CHOICES.map(choice => [choice.id,
  previewEffect(effectConfigFromForm(createEffectForm(choice.id))).preview,
])) as Record<EffectChoiceId, PatternPreview | null>;

export function PatternBrowser({ groupIndex, onGroup, selected, running, preview, onSelect }: {
  groupIndex: number; onGroup: (index: number) => void; selected: EffectChoiceId; running: EffectChoiceId | null;
  preview: PatternPreview | null; onSelect: (id: EffectChoiceId) => void;
}) {
  const group = PATTERN_GROUPS[groupIndex]!;
  return <View style={s.browser}>
    <ScrollView horizontal showsHorizontalScrollIndicator={false} removeClippedSubviews={false} style={s.bleed}
      contentContainerStyle={[s.gutter, s.tabs]}>
      {PATTERN_GROUPS.map((item, index) => {
        const active = groupIndex === index;
        return <Pressable key={item.title} accessibilityRole="tab" accessibilityState={{ selected: active }}
          accessibilityLabel={`${item.title}, ${item.ids.length} ${item.ids.length === 1 ? 'scene' : 'scenes'}`}
          onPress={() => onGroup(index)} style={({ pressed }) => [s.tab, active ? s.tabActive : glass, pressed && s.pressed]}>
          <Text style={[s.tabLabel, active && s.tabLabelActive]}>{item.title}</Text>
        </Pressable>;
      })}
    </ScrollView>
    <ScrollView key={group.title} horizontal showsHorizontalScrollIndicator={false} removeClippedSubviews={false} style={s.bleed}
      contentContainerStyle={[s.gutter, s.cards]}>
      {group.ids.map(id => {
        const choice = EFFECT_CHOICES.find(item => item.id === id)!;
        const active = id === selected;
        const waveform = active ? preview : thumbnails[id];
        const look = SCENE_LOOKS[id];
        const [light, vivid, deep] = look.tone;
        const runLabel = thumbnails[id]?.runLabel ?? 'Preset';
        return <Pressable key={id} accessibilityRole="button" accessibilityState={{ selected: active }}
          accessibilityLabel={`${choice.title}. ${choice.note} ${runLabel}.${running === id ? ' Running.' : ''}`}
          accessibilityHint="Select to preview and tune. The light stays unchanged."
          onPress={() => onSelect(id)} style={({ pressed }) => [s.card, {
            backgroundColor: vivid, backgroundImage: `linear-gradient(168deg, ${light} 0%, ${vivid} 55%, ${deep} 100%)`,
          }, active && s.cardActive, pressed && s.pressed]}>
          <View pointerEvents="none" style={s.scrim} />
          {waveform && <View pointerEvents="none" style={s.silhouette}><Waveform preview={waveform} variant="silhouette" height={46} /></View>}
          <GlassChip label={runLabel === 'Until stopped' ? undefined : runLabel} dot={running === id ? c.white : undefined}>
            {runLabel === 'Until stopped' && <LoopGlyph />}
          </GlassChip>
          <Text style={s.cardTitle}>{choice.title}</Text>
          <Text accessible={false} numberOfLines={1} maxFontSizeMultiplier={1.2} style={s.number}>
            {String(SCENE_ORDER.indexOf(id) + 1).padStart(2, '0')}
          </Text>
          {active && <View style={s.check}><CheckGlyph /></View>}
          <Rim radius={30} strength={active ? 1.15 : 0.5} rim={active ? '255, 236, 238' : undefined} />
        </Pressable>;
      })}
    </ScrollView>
  </View>;
}

// Rows bleed to the screen edges, then pad their content back in by the same amount. The
// padding is a transparent gutter sized to the soft shadow reach (`softShadow` below:
// offset + blur under, blur − offset above), so nothing inside is cut into a slab.
const PAGE = 20;
const ABOVE = 18;
const BELOW = 40;

const s = StyleSheet.create({
  // Keep vertical gutters in normal flow: shadows have room and rows never overlap touch targets.
  browser: { gap: 0 },
  bleed: { marginHorizontal: -PAGE, flexGrow: 0 },
  gutter: { paddingHorizontal: PAGE, paddingTop: ABOVE, paddingBottom: BELOW },
  tabs: { gap: 8 },
  tab: { minHeight: 48, paddingHorizontal: 18, paddingVertical: 11, borderRadius: 24, justifyContent: 'center' },
  tabActive: { backgroundColor: '#C81E2A',
    backgroundImage: 'linear-gradient(180deg, #F2565E 0%, #CC1E2A 55%, #8E0E16 100%)',
    boxShadow: `inset 0 1.5px 0 rgba(255, 228, 228, 0.7), inset 0 -6px 12px -4px rgba(255, 190, 190, 0.5), ${softShadow('223, 33, 33', 0.34, 0.24, 36, 10)}` },
  tabLabel: { color: c.pink, fontFamily: t.regular, fontSize: 15, lineHeight: 22 },
  tabLabelActive: { color: c.white, fontFamily: t.medium },
  cards: { gap: 12 },
  card: { minWidth: 146, minHeight: 172, borderRadius: 30, padding: 14, overflow: 'hidden', gap: 8,
    boxShadow: softShadow('10, 0, 0', 0.32, 0.22, 36, 12) },
  cardActive: { boxShadow: softShadow('255, 60, 60', 0.34, 0.24, 36, 12) },
  scrim: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0,
    backgroundImage: 'radial-gradient(ellipse 95% 62% at 18% 24%, rgba(46, 4, 6, 0.4) 0%, rgba(46, 4, 6, 0) 100%)' },
  silhouette: { position: 'absolute', left: 0, right: 0, bottom: 0, opacity: 0.9 },
  cardTitle: { color: c.white, fontFamily: t.medium, fontSize: 16, lineHeight: 22, marginTop: 4,
    textShadowColor: 'rgba(60, 0, 0, 0.45)', textShadowOffset: { width: 0, height: 1 }, textShadowRadius: 6 },
  number: { color: c.white, fontFamily: t.semibold, fontSize: 40, lineHeight: 52, includeFontPadding: false,
    textShadowColor: 'rgba(60, 0, 0, 0.35)', textShadowOffset: { width: 0, height: 2 }, textShadowRadius: 10 },
  check: { position: 'absolute', right: 12, bottom: 12, width: 28, height: 28, borderRadius: 14, backgroundColor: c.white,
    alignItems: 'center', justifyContent: 'center', boxShadow: '0 4px 10px rgba(60, 0, 0, 0.4)' },
  pressed: { opacity: 0.82, transform: [{ scale: 0.98 }] },
});
