import { StyleSheet, Text, View } from 'react-native';
import { colors as c } from './palette';
import { formatSeconds } from './preview';
import type { PatternPreview } from './preview';
import { Waveform } from './Waveform';

export function ScenePreview({ title, note, preview }: { title: string; note: string; preview: PatternPreview | null }) {
  return <View style={s.card}>
    <View style={s.heading}>
      <Text style={s.eyebrow}>PREVIEW</Text>
      <Text style={s.caption}>Before you start</Text>
    </View>
    <View style={s.intro}><Text style={s.title}>{title}</Text><Text style={s.note}>{note}</Text></View>
    {preview ? <>
      <View accessible accessibilityRole="image"
        accessibilityLabel={`${title} preview. ${preview.windowLabel}. Brightness ${preview.brightnessLabel}. Temperature ${preview.temperatureLabel}. This is a preview, not actual light output.`}
        style={s.chart}>
        <View style={s.heading}><Text style={s.caption}>{preview.windowLabel}</Text><Text style={s.caption}>0–{preview.ceiling}%</Text></View>
        <Waveform preview={preview} />
        <View style={s.heading}><Text style={s.caption}>0 s</Text><Text style={s.caption}>{formatSeconds(preview.seconds)}</Text></View>
      </View>
      <Text style={s.legend}>64 samples · height = brightness · color = warmth</Text>
      <View style={s.metrics}>
        <View style={s.metric}><Text style={s.caption}>Requested range</Text><Text style={s.value}>{preview.brightnessLabel}</Text></View>
        <View style={s.metric}><Text style={s.caption}>Run time</Text><Text style={s.value}>{preview.runLabel}</Text></View>
      </View>
      {preview.baselineNote && <Text style={s.caption}>{preview.baselineNote}</Text>}
    </> : <View style={s.unavailable}><Text style={s.note}>Finish tuning to see the preview.</Text></View>}
  </View>;
}

const s = StyleSheet.create({
  card: { backgroundColor: c.surface, borderRadius: 22, padding: 20, gap: 18 },
  heading: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  eyebrow: { color: c.accent, fontSize: 12, fontWeight: '600', letterSpacing: 1.8 },
  caption: { color: c.muted, fontSize: 12, lineHeight: 19, fontVariant: ['tabular-nums'] },
  intro: { gap: 6 }, title: { color: c.text, fontSize: 28, fontWeight: '500', letterSpacing: -0.6 },
  note: { color: c.muted, fontSize: 14, lineHeight: 21 }, chart: { gap: 10 },
  legend: { color: c.muted, fontSize: 11, lineHeight: 18 }, metrics: { flexDirection: 'row', flexWrap: 'wrap', gap: 22,
    borderTopWidth: 1, borderColor: c.line, paddingTop: 16 }, metric: { flexGrow: 1, gap: 4 },
  value: { color: c.text, fontSize: 20, fontWeight: '500', fontVariant: ['tabular-nums'] }, unavailable: { paddingVertical: 28 },
});
