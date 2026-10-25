import { memo } from 'react';
import { Image, Pressable, StyleSheet, Text, View } from 'react-native';
import type { ImageSourcePropType } from 'react-native';
import { font } from './theme';

const hero = require('../../assets/g60/hero-ruby.png');
const compactSource = require('../../assets/g60/compact-ruby.png');

/** Illustrative product artwork under scarlet studio light. Live connection and output are shown separately. */
export const G60Artwork = memo(function G60Artwork({ compact = false, control = false, source, onExplore }: {
  compact?: boolean;
  control?: boolean;
  source?: ImageSourcePropType;
  onExplore?: () => void;
}) {
  const artwork = (
    <View pointerEvents="none" accessible={false} importantForAccessibility="no-hide-descendants"
      style={[s.stage, compact && s.compact, control && s.control]}>
      <View style={s.glow} />
      <View style={s.floor} />
      <Image source={source ?? (compact || control ? compactSource : hero)} resizeMode="contain"
        style={[s.product, control && s.controlProduct]} fadeDuration={0} />
    </View>
  );
  if (!onExplore) return artwork;
  return <Pressable accessibilityRole="button" accessibilityLabel="Explore G60 in 3D"
    accessibilityHint="Open the interactive model. Drag to rotate and pinch to zoom."
    onPress={onExplore} style={[s.interactive, control && s.control]}>
    {artwork}
    <View pointerEvents="none" accessible={false} style={s.badge}>
      <Text accessible={false} allowFontScaling={false} style={s.badgeLabel}>3D</Text>
    </View>
  </Pressable>;
});

const s = StyleSheet.create({
  stage: { width: '100%', aspectRatio: 1.42, alignItems: 'center', justifyContent: 'center' },
  interactive: { width: '100%', position: 'relative' },
  badge: { position: 'absolute', right: '18%', top: 8, width: 48, height: 48, borderRadius: 24,
    alignItems: 'center', justifyContent: 'center', backgroundColor: '#300909',
    backgroundImage: 'linear-gradient(150deg, rgba(255,220,218,0.22) 0%, rgba(255,220,218,0) 65%)',
    borderWidth: 1, borderColor: 'rgba(255,220,218,0.45)', boxShadow: '0 5px 18px rgba(25,0,0,0.2)' },
  badgeLabel: { color: '#FFFFFF', fontFamily: font.medium, fontSize: 14, lineHeight: 20 },
  compact: { aspectRatio: 2.15 },
  control: { height: '100%', aspectRatio: undefined },
  controlProduct: { height: '128%' },
  glow: { position: 'absolute', width: '92%', height: '110%',
    backgroundImage: 'radial-gradient(circle closest-side, rgba(255, 70, 60, 0.5) 0%, rgba(252, 66, 57, 0.44) 20%, rgba(241, 53, 46, 0.3) 40%, rgba(230, 41, 38, 0.14) 60%, rgba(223, 33, 33, 0.035) 80%, rgba(223, 33, 33, 0) 100%)' },
  floor: { position: 'absolute', bottom: '4%', width: '56%', height: '14%', borderRadius: 999,
    backgroundImage: 'radial-gradient(ellipse closest-side, rgba(10, 0, 0, 0.5) 0%, rgba(10, 0, 0, 0.4) 25%, rgba(10, 0, 0, 0.18) 50%, rgba(10, 0, 0, 0.04) 75%, rgba(10, 0, 0, 0) 100%)' },
  product: { width: '100%', height: '100%' },
});
