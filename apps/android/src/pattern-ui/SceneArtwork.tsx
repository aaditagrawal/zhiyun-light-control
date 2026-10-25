import { memo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import type { ArtLayer, SceneLook } from './catalog';
import { falloff, rgba } from './materials';

/** Breathing room between the fitted composition and the frame, so glow tails fade before any edge. */
const INSET = 16;

/** Native light sculptures set the mood; only Waveform represents sampled output. */
export const SceneArtwork = memo(function SceneArtwork({ look, vignette = true }: { look: SceneLook; vignette?: boolean }) {
  const [box, setBox] = useState<{ width: number; height: number } | null>(null);
  // Fit, never cover: the 100-unit composition uses the shorter side minus a safe inset.
  // The sky and any horizon stay full-bleed across the whole frame.
  const side = box ? Math.max(0, Math.min(box.width, box.height) - INSET * 2) : 0;
  const [core, middle, deep] = look.sky;
  return <View pointerEvents="none" accessible={false} accessibilityElementsHidden importantForAccessibility="no-hide-descendants"
    onLayout={event => {
      const { width, height } = event.nativeEvent.layout;
      setBox(previous => previous?.width === width && previous.height === height ? previous : { width, height });
    }}
    style={[s.fill, { backgroundColor: deep,
      backgroundImage: `radial-gradient(ellipse 92% 82% at 50% 36%, ${core} 0%, ${middle} 52%, ${deep} 100%)` }]}>
    {box && look.art.map((layer, index) => <Layer key={index} layer={layer} sky={look.sky}
      frame={{ ...box, unit: side / 100, left: (box.width - side) / 2, top: (box.height - side) / 2 }} />)}
    {vignette && <View style={s.vignette} />}
  </View>;
});

interface Frame { width: number; height: number; unit: number; left: number; top: number }

function Layer({ layer, frame, sky }: { layer: ArtLayer; frame: Frame; sky: SceneLook['sky'] }) {
  const { unit } = frame;
  const x = frame.left + layer.x * unit, y = frame.top + layer.y * unit;
  switch (layer.kind) {
    case 'glow': {
      const width = layer.r * 2 * unit, height = width * (layer.squash ?? 1), alpha = layer.alpha ?? 1;
      return <View style={{ position: 'absolute', left: x - width / 2, top: y - height / 2, width, height,
        backgroundImage: `radial-gradient(ellipse closest-side, ${falloff(layer.color, alpha)})` }} />;
    }
    case 'orb': {
      const d = layer.r * 2 * unit;
      return <View style={{ position: 'absolute', left: x - d / 2, top: y - d / 2, width: d, height: d, borderRadius: d / 2,
        overflow: 'hidden', backgroundColor: layer.edge,
        backgroundImage: `radial-gradient(circle ${Math.round(d * 0.58)}px at 50% 58%, ${layer.core} 0%, ${layer.mid} 42%, ${layer.edge} 86%, ${layer.edge} 100%)`,
        boxShadow: `0 0 ${Math.round(d * 0.12)}px ${rgba(layer.mid, 0.45)}, 0 0 ${Math.round(d * 0.42)}px ${rgba(layer.mid, 0.32)}, `
          + `inset 0 ${-Math.round(d * 0.07)}px ${Math.round(d * 0.14)}px rgba(255, 236, 236, 0.32), inset 0 0 ${Math.round(d * 0.1)}px rgba(255, 228, 228, 0.4)` }}>
        <View style={{ position: 'absolute', left: d * 0.17, top: d * 0.07, width: d * 0.52, height: d * 0.3,
          backgroundImage: 'radial-gradient(ellipse closest-side, rgba(255, 255, 255, 0.85) 0%, rgba(255, 255, 255, 0) 100%)' }} />
      </View>;
    }
    case 'disc': {
      const d = layer.r * 2 * unit;
      return <View style={{ position: 'absolute', left: x - d / 2, top: y - d / 2, width: d, height: d, borderRadius: d / 2,
        alignItems: 'center', justifyContent: 'center',
        backgroundImage: `radial-gradient(circle closest-side, ${layer.label} 0%, ${layer.label} 52%, #8A1A10 56%, #B82424 64%, ${layer.ring} 82%, ${rgba(layer.ring, 0.9)} 92%, ${rgba(layer.ring, 0)} 100%)`,
        boxShadow: `0 ${Math.round(d * 0.03)}px ${Math.round(d * 0.08)}px rgba(20, 0, 0, 0.3), 0 ${Math.round(d * 0.06)}px ${Math.round(d * 0.26)}px rgba(20, 0, 0, 0.24)` }}>
        <View style={{ position: 'absolute', width: d * 0.42, height: d * 0.42, borderRadius: d * 0.21,
          borderWidth: 1, borderColor: 'rgba(150, 84, 0, 0.32)' }} />
        <View style={{ width: d * 0.27, height: d * 0.27, borderRadius: d * 0.135, backgroundColor: '#1A0404', overflow: 'hidden',
          backgroundImage: `radial-gradient(circle closest-side at 50% 62%, ${layer.core} 0%, #C2300E 55%, #1A0404 100%)` }}>
          <View style={{ position: 'absolute', left: '34%', top: '22%', width: '34%', height: '44%', borderRadius: d,
            backgroundColor: '#1A0404' }} />
          <View style={{ position: 'absolute', left: '22%', top: '58%', width: '58%', height: '60%', borderRadius: d,
            backgroundColor: '#1A0404' }} />
        </View>
      </View>;
    }
    case 'pill': {
      const width = layer.w * unit, height = layer.h * unit, alpha = layer.alpha ?? 1;
      const reach = Math.max(8, Math.min(width, height) * 2.2);
      return <View style={{ position: 'absolute', left: x - width / 2, top: y - height / 2, width, height,
        borderRadius: Math.min(width, height) / 2, backgroundColor: rgba(layer.color, alpha),
        boxShadow: `0 0 ${Math.round(reach * 0.35)}px ${rgba(layer.color, 0.7 * alpha)}, 0 0 ${Math.round(reach)}px ${rgba(layer.color, 0.4 * alpha)}`,
        transform: [{ rotate: `${layer.rotate ?? 0}deg` }] }} />;
    }
    case 'ring': {
      const d = layer.r * 2 * unit;
      return <View style={{ position: 'absolute', left: x - d / 2, top: y - d / 2, width: d, height: d, borderRadius: d / 2,
        borderWidth: 1.5, borderColor: rgba(layer.color, layer.alpha),
        boxShadow: `0 0 4px ${rgba(layer.color, layer.alpha * 0.6)}, 0 0 14px ${rgba(layer.color, layer.alpha * 0.4)}, inset 0 0 10px ${rgba(layer.color, layer.alpha * 0.5)}` }} />;
    }
    case 'sun': {
      const d = layer.r * 2 * unit, horizon = frame.top + layer.horizon * unit;
      return <>
        <View style={{ position: 'absolute', left: x - d / 2, top: y - d / 2, width: d, height: d, borderRadius: d / 2,
          backgroundColor: layer.edge,
          backgroundImage: `radial-gradient(circle ${Math.round(d * 0.62)}px at 50% 30%, ${layer.core} 0%, ${layer.core} 22%, ${layer.edge} 100%)`,
          boxShadow: `0 0 ${Math.round(d * 0.14)}px ${rgba(layer.edge, 0.55)}, 0 0 ${Math.round(d * 0.48)}px ${rgba(layer.edge, 0.36)}` }} />
        {/* The horizon is landscape, not foreground: it spans the full frame width and runs to its foot. */}
        <View style={{ position: 'absolute', left: 0, right: 0, top: horizon, height: frame.height - horizon,
          backgroundColor: sky[2], backgroundImage: `linear-gradient(180deg, ${sky[1]} 0%, ${sky[2]} 70%)`,
          borderTopWidth: 1.5, borderTopColor: rgba(layer.core, 0.9),
          boxShadow: `0 -2px 10px ${rgba(layer.edge, 0.6)}, 0 -6px 40px ${rgba(layer.edge, 0.4)}` }} />
      </>;
    }
    case 'split': {
      const d = layer.r * 2 * unit;
      return <View style={{ position: 'absolute', left: x - d / 2, top: y - d / 2, width: d, height: d, borderRadius: d / 2,
        overflow: 'hidden', backgroundColor: layer.from,
        backgroundImage: `linear-gradient(90deg, ${layer.from} 0%, #FFF4E6 50%, ${layer.to} 100%)`,
        boxShadow: `0 0 ${Math.round(d * 0.12)}px rgba(255, 228, 200, 0.4), 0 0 ${Math.round(d * 0.42)}px rgba(255, 228, 200, 0.28), inset 0 ${-Math.round(d * 0.08)}px ${Math.round(d * 0.16)}px rgba(120, 40, 20, 0.35)` }}>
        <View style={{ position: 'absolute', left: d * 0.17, top: d * 0.07, width: d * 0.52, height: d * 0.3,
          backgroundImage: 'radial-gradient(ellipse closest-side, rgba(255, 255, 255, 0.8) 0%, rgba(255, 255, 255, 0) 100%)' }} />
      </View>;
    }
    case 'slab': {
      const width = layer.w * unit, height = layer.h * unit;
      return <View style={{ position: 'absolute', left: x - width / 2, top: y - height / 2, width, height,
        borderRadius: Math.min(width, height) * 0.24, backgroundColor: layer.to,
        backgroundImage: `linear-gradient(160deg, ${layer.from} 0%, ${layer.to} 100%)`,
        boxShadow: `0 ${Math.round(height * 0.04)}px ${Math.round(height * 0.12)}px rgba(20, 0, 4, 0.3), 0 ${Math.round(height * 0.1)}px ${Math.round(height * 0.4)}px rgba(20, 0, 4, 0.24), `
          + `inset 0 1.5px 0 rgba(255, 255, 255, 0.65), inset 0 0 ${Math.round(height * 0.14)}px rgba(255, 220, 232, 0.4)`,
        transform: [{ rotate: `${layer.rotate ?? 0}deg` }] }} />;
    }
  }
}

const s = StyleSheet.create({
  fill: { position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, overflow: 'hidden' },
  vignette: { position: 'absolute', left: 0, right: 0, bottom: 0, height: '55%',
    backgroundImage: 'linear-gradient(180deg, rgba(14, 4, 4, 0) 0%, rgba(14, 4, 4, 0.62) 100%)' },
});
