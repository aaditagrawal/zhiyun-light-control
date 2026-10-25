import type { ReactNode } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { StyleProp, ViewStyle } from 'react-native';
import { colors as c, type as t } from './palette';

/** `#RRGGBB` to `rgba()`. Gradients fade to the same hue at zero alpha, never to black. */
export function rgba(hex: string, alpha: number): string {
  const value = Number.parseInt(hex.slice(1, 7), 16);
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
}

/** Eased Gaussian-like alpha ramp, so light tails off to zero without a visible knee. */
export const FALLOFF = [[1, 0], [0.7, 20], [0.4, 40], [0.18, 60], [0.06, 80], [0, 100]] as const;

/** Colour stops for `FALLOFF`, scaled to a peak alpha. */
export function falloff(hex: string, alpha: number): string {
  return FALLOFF.map(([level, at]) => `${rgba(hex, alpha * level)} ${at}%`).join(', ');
}

/** Soft radial light. Layers stay translucent so their stacking order never matters. */
export function glow(hex: string, alpha: number, at = '50% 50%', reach = 'closest-side'): string {
  return `radial-gradient(circle ${reach} at ${at}, ${falloff(hex, alpha)})`;
}

/**
 * Two-layer soft drop shadow: a tight contact shadow plus a wide ambient one.
 * No negative spread, so the tail stays gradual; parents leave a gutter of about
 * `offset + blur` below and `blur - offset` above before anything clips it.
 */
export function softShadow(rgb = '10, 0, 0', tight = 0.3, wide = 0.2, blur = 40, offset = 14): string {
  return `0 ${Math.round(offset * 0.4)}px 10px rgba(${rgb}, ${tight}), 0 ${offset}px ${blur}px rgba(${rgb}, ${wide})`;
}

export type JellyTone = 'ruby' | 'magenta' | 'copper' | 'night';

const faces: Record<JellyTone, { deep: string; mid: string; edge: string; rim: string }> = {
  ruby: { deep: '#170405', mid: '#7D0E12', edge: '#EC6878', rim: '255, 190, 198' },
  magenta: { deep: '#1E030E', mid: '#8C104A', edge: '#F078AA', rim: '255, 196, 222' },
  copper: { deep: '#1C0400', mid: '#96280C', edge: '#E6826A', rim: '255, 206, 188' },
  night: { deep: '#120303', mid: '#3A0707', edge: '#962A30', rim: '255, 170, 176' },
};

/** Jelly glass: near-black centre falling outward to a lit, frosted rim. */
export function jelly(tone: JellyTone = 'ruby', strength = 1): ViewStyle {
  const face = faces[tone];
  return {
    backgroundColor: face.deep,
    backgroundImage: `radial-gradient(ellipse 74% 70% at 50% 44%, ${face.deep} 0%, ${face.deep} 26%, ${face.mid} 70%, ${face.edge} 100%)`,
    boxShadow: [rimShadow(strength, face.rim), softShadow('10, 0, 0', 0.34, 0.26, 44, 16)].join(', '),
  };
}

/** Thick pale inner rim, brightest along the lower edge, with a fine highlight on top. */
export function rimShadow(strength = 1, rim = '255, 190, 198'): string {
  return [
    `inset 0 1.5px 0 rgba(255, 238, 240, ${0.8 * strength})`,
    `inset 0 -16px 20px -11px rgba(${rim}, ${0.85 * strength})`,
    `inset 0 0 24px 1px rgba(${rim}, ${0.45 * strength})`,
    `inset 0 0 0 1px rgba(255, 222, 226, ${0.2 * strength})`,
  ].join(', ');
}

/** Luminous edge painted above full-bleed content such as scene art. */
export function Rim({ radius, strength = 1, rim }: { radius: number; strength?: number; rim?: string }) {
  return <View pointerEvents="none" style={[StyleSheet.absoluteFill, { borderRadius: radius, boxShadow: rimShadow(strength, rim) }]} />;
}

export const glass: ViewStyle = {
  backgroundColor: 'rgba(42, 8, 8, 0.52)',
  backgroundImage: 'radial-gradient(ellipse 90% 70% at 50% 0%, rgba(255, 228, 228, 0.2) 0%, rgba(255, 228, 228, 0) 100%)',
  boxShadow: 'inset 0 1px 0 rgba(255, 232, 232, 0.45), inset 0 0 0 1px rgba(255, 200, 200, 0.14), '
    + `inset 0 -8px 14px -7px rgba(255, 170, 170, 0.3), ${softShadow('10, 0, 0', 0.28, 0.18, 36, 10)}`,
};

export const obsidian: ViewStyle = {
  backgroundColor: '#050404',
  backgroundImage: [
    'radial-gradient(ellipse 30% 95% at 15% 20%, rgba(255, 255, 255, 0.34) 0%, rgba(255, 255, 255, 0) 100%)',
    'radial-gradient(ellipse 22% 80% at 89% 58%, rgba(255, 255, 255, 0.17) 0%, rgba(255, 255, 255, 0) 100%)',
    'linear-gradient(180deg, rgba(255, 255, 255, 0.2) 0%, rgba(255, 255, 255, 0) 52%, rgba(255, 255, 255, 0.05) 100%)',
  ].join(', '),
  boxShadow: 'inset 0 1px 0 rgba(255, 255, 255, 0.26), inset 0 -1.5px 0 rgba(255, 255, 255, 0.1), '
    + softShadow('6, 0, 0', 0.36, 0.26, 40, 14),
};

/** Glossy black capsule for primary actions; red stays reserved for light. */
export function ObsidianButton({ label, glyph, disabled = false, hint, accessibilityLabel, onPress }: {
  label: string; glyph?: ReactNode; disabled?: boolean; hint?: string; accessibilityLabel?: string; onPress: () => void;
}) {
  return <Pressable accessibilityRole="button" accessibilityLabel={accessibilityLabel ?? label} accessibilityHint={hint}
    accessibilityState={{ disabled }} disabled={disabled} onPress={onPress}
    style={({ pressed }) => [s.obsidian, obsidian, disabled && s.disabled, pressed && s.pressed]}>
    {glyph}
    <Text style={s.obsidianLabel}>{label}</Text>
  </Pressable>;
}

export function GlassCircle({ label, hint, selected, size = 44, onPress, children }: {
  label: string; hint?: string; selected?: boolean; size?: number; onPress: () => void; children: ReactNode;
}) {
  // The pressable is always at least 48 dp; a smaller glass disc sits centred inside it.
  const target = Math.max(48, size);
  return <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityHint={hint}
    accessibilityState={selected === undefined ? undefined : { expanded: selected }} onPress={onPress}
    style={({ pressed }) => [{ width: target, height: target, borderRadius: target / 2 }, s.center, pressed && s.pressed]}>
    <View style={[glass, { width: size, height: size, borderRadius: size / 2 }, s.center]}>{children}</View>
  </Pressable>;
}

export function GlassChip({ label, dot, children }: { label?: string; dot?: string; children?: ReactNode }) {
  return <View style={[s.chip, glass]}>
    {dot && <View style={[s.dot, { backgroundColor: dot, boxShadow: `0 0 8px ${rgba(dot, 0.9)}` }]} />}
    {children}
    {label && <Text numberOfLines={1} style={s.chipLabel}>{label}</Text>}
  </View>;
}

/** Two linked rings: a run that lasts until it is stopped. */
export function LoopGlyph({ color = c.white }: { color?: string }) {
  return <View style={s.loop}>
    <View style={[s.loopRing, { borderColor: color }]} />
    <View style={[s.loopRing, s.loopRight, { borderColor: color }]} />
  </View>;
}

export function InfoGlyph({ color = c.white }: { color?: string }) {
  return <View style={s.info}>
    <View style={[s.infoDot, { backgroundColor: color }]} />
    <View style={[s.infoStem, { backgroundColor: color }]} />
  </View>;
}

/** A black recessed well with concentric neon rings around its contents. */
export function NeonWell({ size, lit, children, halo }: { size: number; lit: boolean; children: ReactNode; halo?: ReactNode }) {
  const level = lit ? 1 : 0.42;
  return <View style={[s.well, { width: size, height: size, borderRadius: size / 2 }]}>
    {[0.92, 0.8, 0.7].map((scale, index) => <View key={scale} pointerEvents="none" style={{
      position: 'absolute', width: size * scale, height: size * scale, borderRadius: size * scale / 2,
      borderWidth: index === 0 ? 2 : 1.25, borderColor: rgba('#FF3B3B', level * (1 - index * 0.18)),
      boxShadow: `0 0 ${lit ? 9 : 4}px ${rgba('#FF2020', level * 0.85)}, inset 0 0 6px ${rgba('#FF2020', level * 0.6)}`,
    }} />)}
    {halo}
    {children}
  </View>;
}

export const whiteDisc: ViewStyle = {
  backgroundColor: '#F2EFEF',
  backgroundImage: 'radial-gradient(circle at 38% 30%, rgba(255, 255, 255, 1) 0%, rgba(255, 255, 255, 1) 28%, rgba(222, 214, 214, 1) 74%, rgba(176, 164, 164, 1) 100%)',
  boxShadow: '0 0 0 2px rgba(18, 2, 2, 0.95), 0 0 16px 2px rgba(255, 52, 52, 0.6), inset 0 -3px 6px rgba(0, 0, 0, 0.2)',
};

// Linework glyphs, independent of icon fonts.
export function PlayGlyph({ size = 18, color = c.night }: { size?: number; color?: string }) {
  return <View style={{ width: 0, height: 0, marginLeft: size * 0.22, borderTopWidth: size * 0.58, borderBottomWidth: size * 0.58,
    borderLeftWidth: size, borderTopColor: 'transparent', borderBottomColor: 'transparent', borderLeftColor: color }} />;
}

/** An open ring with a clockwise arrowhead: start the same scene again. */
export function RestartGlyph({ color = c.night }: { color?: string }) {
  return <View style={s.restart}>
    <View style={[s.restartRing, { borderColor: color, borderTopColor: 'transparent' }]} />
    <View style={[s.restartHead, { borderLeftColor: color }]} />
  </View>;
}

export function StopGlyph({ size = 14, color = c.white }: { size?: number; color?: string }) {
  return <View style={{ width: size, height: size, borderRadius: size * 0.22, backgroundColor: color }} />;
}

export function SkipGlyph({ direction, color = c.white }: { direction: 'previous' | 'next'; color?: string }) {
  const bar = <View style={{ width: 3, height: 16, borderRadius: 1.5, backgroundColor: color }} />;
  const arrow = <View style={{ width: 0, height: 0, borderTopWidth: 8, borderBottomWidth: 8, borderTopColor: 'transparent',
    borderBottomColor: 'transparent', ...(direction === 'next'
      ? { borderLeftWidth: 13, borderLeftColor: color } : { borderRightWidth: 13, borderRightColor: color }) }} />;
  return <View style={s.skip}>{direction === 'next' ? <>{arrow}{bar}</> : <>{bar}{arrow}</>}</View>;
}

export function CheckGlyph({ color = c.night }: { color?: string }) {
  return <View style={{ width: 7, height: 12, marginTop: -3, borderRightWidth: 2.5, borderBottomWidth: 2.5,
    borderColor: color, transform: [{ rotate: '45deg' }] }} />;
}

export function PlusGlyph({ open = false, color = c.white }: { open?: boolean; color?: string }) {
  return <View style={s.plus}>
    <View style={[s.plusBar, { backgroundColor: color }]} />
    {!open && <View style={[s.plusBar, s.plusVertical, { backgroundColor: color }]} />}
  </View>;
}

const s = StyleSheet.create({
  center: { alignItems: 'center', justifyContent: 'center' },
  obsidian: { minHeight: 56, borderRadius: 28, paddingHorizontal: 24, paddingVertical: 14, flexDirection: 'row',
    alignItems: 'center', justifyContent: 'center', gap: 12 },
  obsidianLabel: { color: c.white, fontFamily: t.regular, fontSize: 16, lineHeight: 22, textAlign: 'center', flexShrink: 1 },
  chip: { minHeight: 30, borderRadius: 15, paddingHorizontal: 12, paddingVertical: 5, flexDirection: 'row', alignItems: 'center',
    gap: 7, alignSelf: 'flex-start', maxWidth: '100%' },
  chipLabel: { color: c.white, fontFamily: t.medium, fontSize: 13, lineHeight: 18, flexShrink: 1 },
  dot: { width: 7, height: 7, borderRadius: 4 },
  well: { backgroundColor: '#050101', alignItems: 'center', justifyContent: 'center',
    backgroundImage: 'radial-gradient(circle closest-side, rgba(120, 10, 10, 0) 60%, rgba(120, 10, 10, 0.6) 100%)',
    boxShadow: `inset 0 5px 12px rgba(0, 0, 0, 0.95), 0 1px 0 rgba(255, 190, 190, 0.28), ${softShadow('6, 0, 0', 0.4, 0.26, 40, 12)}` },
  skip: { flexDirection: 'row', alignItems: 'center', gap: 1 },
  restart: { width: 24, height: 24 },
  restartRing: { position: 'absolute', left: 1, top: 1, width: 22, height: 22, borderRadius: 11, borderWidth: 3,
    transform: [{ rotate: '45deg' }] },
  restartHead: { position: 'absolute', left: 11, top: -3.5, width: 0, height: 0, borderLeftWidth: 8, borderTopWidth: 5.5,
    borderBottomWidth: 5.5, borderTopColor: 'transparent', borderBottomColor: 'transparent' },
  loop: { width: 20, height: 12, flexDirection: 'row', alignItems: 'center' },
  loopRing: { width: 11, height: 11, borderRadius: 6, borderWidth: 2 },
  loopRight: { marginLeft: -2 },
  info: { width: 16, height: 18, alignItems: 'center', justifyContent: 'space-between', paddingVertical: 1 },
  infoDot: { width: 4, height: 4, borderRadius: 2 },
  infoStem: { width: 3.5, height: 10, borderRadius: 1.75 },
  plus: { width: 16, height: 16, alignItems: 'center', justifyContent: 'center' },
  plusBar: { position: 'absolute', width: 16, height: 2.5, borderRadius: 1.25 },
  plusVertical: { transform: [{ rotate: '90deg' }] },
  pressed: { opacity: 0.78, transform: [{ scale: 0.985 }] }, disabled: { opacity: 0.42 },
});
