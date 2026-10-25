import { useEffect, useRef } from 'react';
import { AccessibilityInfo, Animated, Easing, Pressable, StyleSheet, Text, View } from 'react-native';
import type { EffectChoiceId } from '../effectForm';
import { SCENE_LOOKS } from './catalog';
import { falloff, GlassChip, GlassCircle, InfoGlyph, NeonWell, PlayGlyph, RestartGlyph, Rim, SkipGlyph, softShadow, whiteDisc } from './materials';
import { colors as c, type as t } from './palette';
import { formatSeconds } from './preview';
import type { PatternPreview } from './preview';
import { SceneArtwork } from './SceneArtwork';
import { Waveform } from './Waveform';

const CARD_TOP = 40;
const CARD_HEIGHT = 262;
const OVERLAP = 34;

/** A slow neon pulse while the selected scene is the one running; still when motion is reduced. */
function usePulse(active: boolean) {
  const value = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!active) { value.setValue(0); return; }
    let cancelled = false;
    let loop: Animated.CompositeAnimation | null = null;
    void AccessibilityInfo.isReduceMotionEnabled().then(reduce => {
      if (cancelled) return;
      if (reduce) { value.setValue(1); return; }
      loop = Animated.loop(Animated.sequence([
        Animated.timing(value, { toValue: 1, duration: 1200, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(value, { toValue: 0.3, duration: 1200, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
      ]));
      loop.start();
    }, () => { if (!cancelled) value.setValue(1); });
    return () => { cancelled = true; loop?.stop(); };
  }, [active, value]);
  return value;
}

interface Neighbour { title: string; onPress: () => void }

export function ScenePreview({ id, title, preview, subtitle, summary, live, startDisabled, startLabel, startHint, onStart,
  previous, next, notesOpen, onNotes }: {
  id: EffectChoiceId; title: string; preview: PatternPreview | null; subtitle: string; summary: string | null; live: boolean;
  startDisabled: boolean; startLabel: string; startHint: string; onStart: () => void;
  previous: Neighbour; next: Neighbour; notesOpen: boolean; onNotes: () => void;
}) {
  const look = SCENE_LOOKS[id];
  const pulse = usePulse(live);
  return <View style={s.stage}>
    <View pointerEvents="none" style={[s.peek, s.peekFar]}><Rim radius={30} strength={0.7} /></View>
    <View pointerEvents="none" style={[s.peek, s.peekNear]}><Rim radius={32} strength={0.75} /></View>
    <View style={s.front}>
      <SceneArtwork look={look} />
      {preview ? <View accessible accessibilityRole="image"
        accessibilityLabel={`${title} preview. ${preview.windowLabel}. Bar height is brightness on a zero to ${preview.ceiling} percent scale. Bar color is illustrative white temperature. Brightness range ${preview.brightnessLabel}. Temperature ${preview.temperatureLabel}. This is a preview, not actual light output.`}
        style={s.wave}>
        <View pointerEvents="none" style={s.waveGlow} />
        <Waveform preview={preview} height={58} />
      </View> : <View style={s.wave}><Text style={s.unavailable}>Finish tuning to see the preview.</Text></View>}
      <View style={s.frontTop}>
        <GlassChip label={preview ? `Preview · ${formatSeconds(preview.seconds)}` : 'Preview'} />
        <GlassCircle label="Scene notes" hint="How scenes run, and what the preview shows." selected={notesOpen} size={40} onPress={onNotes}>
          <InfoGlyph />
        </GlassCircle>
      </View>
      <Rim radius={34} />
    </View>

    <View pointerEvents="none" style={s.dome} />
    <View pointerEvents="none" style={s.domeLight} />
    <View style={s.transport}>
      <Text accessibilityRole="header" style={s.title}>{title}</Text>
      <Text accessibilityLiveRegion="polite" style={[s.subtitle, live && s.subtitleLive]}>{subtitle}</Text>
      <View style={s.controls}>
        <View pointerEvents="none" style={s.wings} />
        <Pressable accessibilityRole="button" accessibilityLabel={`Previous scene, ${previous.title}`}
          accessibilityHint="Selects the scene. The light stays unchanged." onPress={previous.onPress}
          style={({ pressed }) => [s.skip, pressed && s.pressed]}>
          <SkipGlyph direction="previous" />
        </Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel={startLabel} accessibilityHint={startHint}
          accessibilityState={{ disabled: startDisabled }} disabled={startDisabled} onPress={onStart}
          style={({ pressed }) => [s.start, pressed && s.pressed]}>
          <NeonWell size={100} lit={!startDisabled || live}
            halo={<Animated.View pointerEvents="none" style={[s.halo, { opacity: pulse }]} />}>
            <View style={[s.disc, whiteDisc, startDisabled && s.discDisabled]}>{live ? <RestartGlyph /> : <PlayGlyph size={19} />}</View>
          </NeonWell>
        </Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel={`Next scene, ${next.title}`}
          accessibilityHint="Selects the scene. The light stays unchanged." onPress={next.onPress}
          style={({ pressed }) => [s.skip, pressed && s.pressed]}>
          <SkipGlyph direction="next" />
        </Pressable>
      </View>
      {summary && <Text style={s.summary}>{summary}</Text>}
    </View>
  </View>;
}

const s = StyleSheet.create({
  stage: { borderRadius: 40, overflow: 'hidden', backgroundColor: '#3A0808', paddingTop: CARD_TOP + CARD_HEIGHT - OVERLAP,
    backgroundImage: 'linear-gradient(180deg, #A82C2C 0%, #6E1414 34%, #2A0606 100%)',
    boxShadow: 'inset 0 1.5px 0 rgba(255, 196, 196, 0.5), inset 0 0 40px rgba(255, 110, 110, 0.16), 0 28px 50px -20px rgba(0, 0, 0, 0.85)' },
  peek: { position: 'absolute', height: 120 },
  peekFar: { top: 12, left: '15%', right: '15%', borderRadius: 30, backgroundColor: '#B9447F',
    backgroundImage: 'linear-gradient(180deg, #F29AC0 0%, #CE6D9B 45%, #7E2452 100%)' },
  peekNear: { top: 25, left: '9%', right: '9%', borderRadius: 32, backgroundColor: '#D45A72',
    backgroundImage: 'linear-gradient(180deg, #FFA8B4 0%, #EB86A8 40%, #9E2E50 100%)' },
  front: { position: 'absolute', top: CARD_TOP, left: '4.5%', right: '4.5%', height: CARD_HEIGHT, borderRadius: 34,
    overflow: 'hidden', backgroundColor: '#2A0606', boxShadow: softShadow('10, 0, 0', 0.34, 0.26, 36, 14) },
  // A 48 dp row holds the 48 dp notes target; its 40 dp disc keeps the earlier 14 dp inset.
  frontTop: { position: 'absolute', top: 10, left: 14, right: 8, minHeight: 48, flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between', gap: 8 },
  wave: { position: 'absolute', left: 20, right: 20, bottom: OVERLAP + 24, height: 58, justifyContent: 'flex-end' },
  waveGlow: { position: 'absolute', left: -24, right: -24, top: -26, bottom: -20,
    backgroundImage: `radial-gradient(ellipse closest-side, ${falloff('#FFC896', 0.24)})` },
  unavailable: { color: c.pink, fontFamily: t.regular, fontSize: 14, lineHeight: 20 },
  dome: { position: 'absolute', top: CARD_TOP + CARD_HEIGHT - OVERLAP - 6, left: '-18%', width: '136%', aspectRatio: 1,
    borderRadius: 9999, backgroundColor: '#5A0A0A',
    backgroundImage: 'linear-gradient(180deg, #D62C28 0%, #9A1616 12%, #4A0808 32%, #2A0505 60%)',
    boxShadow: 'inset 0 2px 0 rgba(255, 204, 198, 0.7), inset 0 16px 26px -10px rgba(255, 150, 140, 0.5), 0 -12px 30px rgba(20, 0, 0, 0.6)' },
  domeLight: { position: 'absolute', top: CARD_TOP + CARD_HEIGHT - OVERLAP - 6, left: '12%', right: '12%', height: 170,
    backgroundImage: `radial-gradient(ellipse closest-side, ${falloff('#FF8C80', 0.42)})` },
  transport: { alignItems: 'center', paddingHorizontal: 16, paddingTop: 14, paddingBottom: 22 },
  title: { color: c.white, fontFamily: t.medium, fontSize: 22, lineHeight: 30, textAlign: 'center' },
  subtitle: { color: c.pink, fontFamily: t.regular, fontSize: 14, lineHeight: 20, textAlign: 'center' },
  subtitleLive: { color: '#FFDCD6' },
  controls: { marginTop: 12, alignSelf: 'stretch', flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    minHeight: 112 },
  wings: { position: 'absolute', left: 4, right: 4, top: '50%', height: 66, marginTop: -33, borderRadius: 33,
    backgroundColor: 'rgba(22, 3, 3, 0.55)',
    boxShadow: 'inset 0 3px 8px rgba(0, 0, 0, 0.6), inset 0 -1px 0 rgba(255, 180, 176, 0.24), 0 1px 0 rgba(255, 180, 176, 0.16)' },
  skip: { width: 56, height: 56, borderRadius: 28, alignItems: 'center', justifyContent: 'center', marginHorizontal: 8 },
  start: { borderRadius: 56 },
  disc: { width: 58, height: 58, borderRadius: 29, alignItems: 'center', justifyContent: 'center' },
  discDisabled: { opacity: 0.5 },
  halo: { position: 'absolute', width: 114, height: 114, borderRadius: 57, borderWidth: 2, borderColor: 'rgba(255, 74, 74, 0.95)',
    boxShadow: '0 0 8px 1px rgba(255, 40, 40, 0.6), 0 0 26px 2px rgba(255, 40, 40, 0.4)' },
  summary: { marginTop: 10, color: c.soft, fontFamily: t.regular, fontSize: 14, lineHeight: 20, textAlign: 'center' },
  pressed: { opacity: 0.8, transform: [{ scale: 0.97 }] },
});
