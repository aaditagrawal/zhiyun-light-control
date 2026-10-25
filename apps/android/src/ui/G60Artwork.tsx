import { Component, lazy, memo, Suspense, useCallback, useRef } from 'react';
import type { ReactNode } from 'react';
import { Image, Pressable, StyleSheet, View } from 'react-native';
import type { ImageSourcePropType } from 'react-native';

const hero = require('../../assets/g60/hero-ruby.png');
const compactSource = require('../../assets/g60/compact-ruby.png');
// Loaded only for interactive artwork, so a missing WebView module stays inside InlineBoundary.
const G60Inline = lazy(() => import('../showcase/G60Inline'));

/**
 * Illustrative product artwork under scarlet studio light. Live connection and output are shown separately.
 * `interactive` replaces the still with a slowly turning model that can be dragged and pinched in place;
 * `active` pauses it while offscreen or covered, and `onInteractionChange` reports when a parent scroll
 * view should yield to a gesture. Without `interactive` the artwork is purely static.
 */
export const G60Artwork = memo(function G60Artwork({ compact = false, control = false, source, onExplore,
  interactive = false, active = true, onInteractionChange }: {
  compact?: boolean;
  control?: boolean;
  source?: ImageSourcePropType;
  onExplore?: () => void;
  interactive?: boolean;
  active?: boolean;
  onInteractionChange?: (active: boolean) => void;
}) {
  const report = useRef(onInteractionChange);
  report.current = onInteractionChange;
  const release = useCallback(() => report.current?.(false), []);
  const touching = useCallback((next: boolean) => report.current?.(next), []);
  const still = <View pointerEvents="none" style={s.fill}>
    <View style={s.floor} />
    <Image source={source ?? (compact || control ? compactSource : hero)} resizeMode="contain"
      style={[s.product, control && s.controlProduct]} fadeDuration={0} />
  </View>;
  return <View style={[s.stage, compact && s.compact, control && s.control]}>
    <View pointerEvents="none" accessible={false} importantForAccessibility="no-hide-descendants" style={s.fill}>
      <View style={s.glow} />
    </View>
    {interactive ? <InlineBoundary fallback={still} onFail={release}>
      <Suspense fallback={still}><G60Inline active={active} still={still} onInteractionChange={touching} /></Suspense>
    </InlineBoundary> : <View accessible={false} importantForAccessibility="no-hide-descendants" style={s.fill}>{still}</View>}
    {onExplore && <Pressable accessibilityRole="button" accessibilityLabel="Open 3D showcase"
      onPress={onExplore} hitSlop={4} style={({ pressed }) => [s.badge, s.expand, pressed && s.pressed]}>
      <View accessible={false} style={s.expandGlyph}>
        <View style={[s.corner, s.topLeft]} /><View style={[s.corner, s.topRight]} />
        <View style={[s.corner, s.bottomLeft]} /><View style={[s.corner, s.bottomRight]} />
      </View>
    </Pressable>}
  </View>;
});

/** Any inline renderer or native-module failure leaves the still artwork and releases the parent scroll. */
class InlineBoundary extends Component<{ fallback: ReactNode; onFail: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown) {
    console.warn('G60 inline', 'unavailable', error instanceof Error ? error.message : String(error));
    this.props.onFail();
  }
  render() { return this.state.failed ? this.props.fallback : this.props.children; }
}

const s = StyleSheet.create({
  stage: { width: '100%', aspectRatio: 1.42, position: 'relative' },
  fill: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center' },
  badge: { position: 'absolute', width: 48, height: 48, borderRadius: 24,
    alignItems: 'center', justifyContent: 'center', backgroundColor: '#300909',
    backgroundImage: 'linear-gradient(150deg, rgba(255,220,218,0.22) 0%, rgba(255,220,218,0) 65%)',
    borderWidth: 1, borderColor: 'rgba(255,220,218,0.45)', boxShadow: '0 5px 18px rgba(25,0,0,0.2)' },
  expand: { right: '18%', top: 8 },
  pressed: { opacity: 0.7 },
  expandGlyph: { width: 18, height: 18 },
  corner: { position: 'absolute', width: 7, height: 7, borderColor: '#FFFFFF' },
  topLeft: { top: 0, left: 0, borderTopWidth: 2, borderLeftWidth: 2, borderTopLeftRadius: 2 },
  topRight: { top: 0, right: 0, borderTopWidth: 2, borderRightWidth: 2, borderTopRightRadius: 2 },
  bottomLeft: { bottom: 0, left: 0, borderBottomWidth: 2, borderLeftWidth: 2, borderBottomLeftRadius: 2 },
  bottomRight: { bottom: 0, right: 0, borderBottomWidth: 2, borderRightWidth: 2, borderBottomRightRadius: 2 },
  compact: { aspectRatio: 2.15 },
  control: { height: '100%', aspectRatio: undefined },
  controlProduct: { height: '128%' },
  glow: { position: 'absolute', width: '92%', height: '110%',
    backgroundImage: 'radial-gradient(circle closest-side, rgba(255, 70, 60, 0.5) 0%, rgba(252, 66, 57, 0.44) 20%, rgba(241, 53, 46, 0.3) 40%, rgba(230, 41, 38, 0.14) 60%, rgba(223, 33, 33, 0.035) 80%, rgba(223, 33, 33, 0) 100%)' },
  floor: { position: 'absolute', bottom: '4%', width: '56%', height: '14%', borderRadius: 999,
    backgroundImage: 'radial-gradient(ellipse closest-side, rgba(10, 0, 0, 0.5) 0%, rgba(10, 0, 0, 0.4) 25%, rgba(10, 0, 0, 0.18) 50%, rgba(10, 0, 0, 0.04) 75%, rgba(10, 0, 0, 0) 100%)' },
  product: { width: '100%', height: '100%' },
});
