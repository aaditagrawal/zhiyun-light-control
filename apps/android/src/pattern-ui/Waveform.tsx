import { memo } from 'react';
import { StyleSheet, View } from 'react-native';
import { temperatureColor } from './preview';
import type { PatternPreview } from './preview';

const fade = (rgb: string, alpha: number) => rgb.replace('rgb(', 'rgba(').replace(')', `, ${alpha})`);

/**
 * The 64 bounded preview samples: bar height is brightness against the preview
 * ceiling and colour is an indicative white temperature. Never live output.
 */
export const Waveform = memo(function Waveform({ preview, variant = 'light', height }: {
  preview: PatternPreview; variant?: 'light' | 'silhouette'; height: number;
}) {
  const silhouette = variant === 'silhouette';
  return <View accessible={false} accessibilityElementsHidden importantForAccessibility="no-hide-descendants"
    style={[s.plot, { height }]}>
    {preview.samples.map((sample, index) => {
      const tone = temperatureColor(sample.cct);
      // One view per sample: flex bars with side margins, no wrapper columns.
      return <View key={index} style={[s.bar, silhouette ? s.silhouette : {
        backgroundImage: `linear-gradient(180deg, ${tone} 0%, ${tone} 35%, ${fade(tone, 0.28)} 100%)`,
      }, { height: `${100 * sample.brightness / preview.ceiling}%` }]} />;
    })}
    <View style={[s.baseline, silhouette && s.silhouetteBase]} />
  </View>;
});

const s = StyleSheet.create({
  plot: { flexDirection: 'row', alignItems: 'flex-end' },
  bar: { flex: 1, marginHorizontal: 1.1, borderRadius: 2 },
  silhouette: { marginHorizontal: 0.5, backgroundColor: 'rgba(255, 255, 255, 0.3)' },
  baseline: { position: 'absolute', left: 0, right: 0, bottom: 0, height: 1, backgroundColor: 'rgba(255, 236, 236, 0.4)' },
  silhouetteBase: { backgroundColor: 'rgba(255, 255, 255, 0.18)' },
});
