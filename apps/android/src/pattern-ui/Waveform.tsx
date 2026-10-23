import { memo } from 'react';
import { StyleSheet, View } from 'react-native';
import { colors as c } from './palette';
import { temperatureColor } from './preview';
import type { PatternPreview } from './preview';

export const Waveform = memo(function Waveform({ preview, compact = false }: {
  preview: PatternPreview; compact?: boolean;
}) {
  return <View accessible={false} accessibilityElementsHidden importantForAccessibility="no-hide-descendants"
    style={[s.plot, compact && s.compact]}>
    {preview.samples.map((sample, index) => <View key={index} style={[s.column, !compact && s.spaced]}>
      <View style={[s.bar, {
        height: `${100 * sample.brightness / preview.ceiling}%`,
        backgroundColor: temperatureColor(sample.cct),
      }]} />
    </View>)}
    <View style={s.baseline} />
  </View>;
});

const s = StyleSheet.create({
  plot: { height: 100, flexDirection: 'row', alignItems: 'flex-end', position: 'relative' },
  compact: { height: 34 }, column: { flex: 1, height: '100%', justifyContent: 'flex-end', paddingHorizontal: 0.2 },
  spaced: { paddingHorizontal: 0.7 }, bar: { width: '100%', borderTopLeftRadius: 2, borderTopRightRadius: 2 },
  baseline: { position: 'absolute', bottom: 0, left: 0, right: 0, height: 1, backgroundColor: c.line },
});
