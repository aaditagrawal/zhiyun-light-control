import { useEffect, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import Slider from '@react-native-community/slider';
import { color, minimumTouchTarget, temperatureSwatch, temperatureTrack } from './theme';

type Kind = 'brightness' | 'temperature';
type Request = { value: number; pending: boolean; rejected: boolean };

const brightnessPresets = [5, 25, 50, 100];
const temperaturePresets = [2700, 3200, 5600, 6500];

function format(value: number, kind: Kind): string {
  return kind === 'brightness' ? `${Number(value.toFixed(1))}%` : `${value} K`;
}

/** The large value is always hardware readback. The native thumb edits a separate request. */
export function IndicativeSlider({ kind, value, verified, working, disabled, onCommit }: {
  kind: Kind;
  value: number;
  verified: boolean;
  working: boolean;
  disabled: boolean;
  onCommit: (value: number) => Promise<void>;
}) {
  const temperature = kind === 'temperature';
  const title = temperature ? 'Color temperature' : 'Brightness';
  const min = temperature ? 2700 : 0;
  const max = temperature ? 6500 : 100;
  const step = temperature ? 100 : 1;
  const [draft, setDraft] = useState(value);
  const [dragging, setDragging] = useState(false);
  const [request, setRequest] = useState<Request | null>(null);
  const draggingNow = useRef(false);
  const committing = useRef(false);
  const mounted = useRef(true);
  const { fontScale, width } = useWindowDimensions();
  const stacked = fontScale > 1.25 || width < 350;
  const locked = disabled || request?.pending === true;
  const thumb = dragging ? draft : request?.pending ? request.value : value;
  const matches = request !== null && Math.abs(request.value - value) <= (temperature ? 0 : 0.01);
  const failed = request !== null && !request.pending && !working && (request.rejected || !verified);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // A concurrent power/read/pattern operation invalidates a gesture before it can write.
  useEffect(() => {
    if (disabled) { draggingNow.current = false; setDragging(false); }
  }, [disabled]);

  const normalize = (next: number) => Math.max(min, Math.min(max, Math.round(next / step) * step));
  const commit = async (next: number) => {
    if (locked || committing.current) return;
    const target = normalize(next);
    committing.current = true;
    draggingNow.current = false;
    setDragging(false);
    setRequest({ value: target, pending: true, rejected: false });
    let rejected = false;
    try { await onCommit(target); }
    catch { rejected = true; }
    finally {
      committing.current = false;
      if (mounted.current) setRequest({ value: target, pending: false, rejected });
    }
  };

  const readbackLabel = working ? 'Actual · checking readback…'
    : verified ? 'Actual · readback verified' : 'Last read · unverified';
  const requestLabel = dragging ? `Draft ${format(draft, kind)} · release to apply`
    : request ? `Requested ${format(request.value, kind)} · ${request.pending || working ? 'checking…'
      : failed ? 'not confirmed' : matches ? 'readback verified' : 'actual has changed'}`
      : `Release to apply · ${temperature ? '100 K' : '1%'} steps`;

  return (
    <View style={s.card}>
      <View style={[s.heading, stacked && s.headingStacked]}>
        <View style={s.titleGroup}>
          <Text accessibilityRole="header" style={s.title}>{title}</Text>
          <Text style={[s.readbackLabel, !verified && !working && s.warning]}>{readbackLabel}</Text>
        </View>
        <View style={s.valueGroup}>
          {temperature && <View accessible={false} style={[s.swatch, { backgroundColor: temperatureSwatch(value) }]} />}
          <Text style={s.value} accessibilityLabel={`Actual ${title.toLowerCase()}: ${format(value, kind)}. ${verified ? 'Readback verified' : 'Unverified'}`}>
            {format(value, kind)}
          </Text>
        </View>
      </View>

      <View style={[s.sliderArea, locked && s.trackDisabled]}>
        <View pointerEvents="none" accessible={false} importantForAccessibility="no-hide-descendants" style={s.trackInset}>
          <View style={s.track}>
            {temperature ? temperatureTrack.map((tone, i) => (
              <View key={i} style={[s.trackSegment, { backgroundColor: tone }]} />
            )) : Array.from({ length: 20 }, (_, i) => (
              <View key={i} style={[s.trackSegment, { backgroundColor: color.accent, opacity: 0.1 + i / 19 * 0.9 }]} />
            ))}
          </View>
        </View>
        <Slider accessibilityLabel={`Requested ${title.toLowerCase()}`} accessibilityRole="adjustable"
          hitSlop={8}
          accessibilityHint={`Adjust in ${temperature ? '100 kelvin' : 'one percent'} steps. Release to apply. The actual value is shown separately.`}
          accessibilityState={{ disabled: locked, busy: request?.pending === true }}
          accessibilityValue={{ min, max, now: thumb, text: `${format(thumb, kind)} requested. Actual ${format(value, kind)}${verified ? ', verified' : ', unverified'}` }}
          minimumValue={min} maximumValue={max} step={step} value={thumb} disabled={locked}
          onSlidingStart={(next) => {
            if (!locked) { draggingNow.current = true; setDraft(normalize(next)); setDragging(true); }
          }}
          onValueChange={(next) => {
            if (locked) return;
            setDraft(normalize(next));
            // Android accessibility/keyboard adjustments have no touch-release event.
            // A touch gesture only writes in onSlidingComplete.
            if (!draggingNow.current) void commit(next);
          }}
          onSlidingComplete={(next) => { void commit(next); }}
          minimumTrackTintColor="transparent" maximumTrackTintColor="transparent"
          thumbTintColor={locked ? color.muted : color.text} style={s.slider} />
      </View>

      <View style={s.scale} accessible={false} importantForAccessibility="no-hide-descendants">
        <View style={s.scaleEdge}>
          <Text style={s.scaleLabel}>{temperature ? 'Warm' : 'Off'}</Text>
          <Text style={s.scaleValue}>{temperature ? '2700 K' : '0%'}</Text>
        </View>
        {temperature && <Text style={s.scaleMiddle}>Neutral</Text>}
        <View style={[s.scaleEdge, s.alignEnd]}>
          <Text style={s.scaleLabel}>{temperature ? 'Daylight / cool' : 'Full'}</Text>
          <Text style={s.scaleValue}>{temperature ? '6500 K' : '100%'}</Text>
        </View>
      </View>

      <View style={s.requestRow}>
        {temperature && (dragging || request) && <View accessible={false} style={[s.swatch, {
          backgroundColor: temperatureSwatch(dragging ? draft : request!.value),
        }]} />}
        <Text accessibilityLiveRegion={dragging ? 'none' : 'polite'} style={[s.request, failed && s.warning]}>{requestLabel}</Text>
      </View>
      <View style={s.presets}>
        {(temperature ? temperaturePresets : brightnessPresets).map(target => {
          const selected = verified && Math.abs(value - target) <= (temperature ? 0 : 0.01);
          return (
            <Pressable key={target} accessibilityRole="button"
              accessibilityLabel={`Set ${title.toLowerCase()} to ${format(target, kind)}`}
              accessibilityState={{ disabled: locked, selected }} disabled={locked}
              onPress={() => { void commit(target); }}
              style={({ pressed }) => [s.preset, stacked && s.widePreset, selected && s.presetSelected, locked && s.disabled, pressed && s.pressed]}>
              {temperature && <View accessible={false} style={[s.presetSwatch, { backgroundColor: temperatureSwatch(target) }]} />}
              <Text style={[s.presetLabel, selected && s.presetLabelSelected]}>{format(target, kind)}</Text>
            </Pressable>
          );
        })}
      </View>
      {temperature && <Text style={s.note}>White tones are indicative.</Text>}
    </View>
  );
}

const s = StyleSheet.create({
  card: { backgroundColor: color.surface, borderRadius: 18, padding: 18, gap: 8 },
  heading: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' },
  headingStacked: { flexDirection: 'column', alignItems: 'flex-start' },
  titleGroup: { flexShrink: 1, gap: 5 },
  title: { fontSize: 16, fontWeight: '600', color: color.text },
  readbackLabel: { fontSize: 12, color: color.muted },
  valueGroup: { flexDirection: 'row', alignItems: 'center', gap: 9, flexShrink: 1 },
  value: { fontSize: 28, fontWeight: '500', color: color.text, fontVariant: ['tabular-nums'], flexShrink: 1 },
  swatch: { width: 15, height: 15, borderRadius: 8, borderWidth: 1, borderColor: color.muted },
  sliderArea: { height: 56, justifyContent: 'center', marginHorizontal: -2 },
  slider: { width: '100%', height: 56 },
  trackInset: { position: 'absolute', left: 16, right: 16 },
  track: { flexDirection: 'row', height: 8, borderRadius: 4, overflow: 'hidden', gap: 1 },
  trackSegment: { flex: 1 },
  trackDisabled: { opacity: 0.55 },
  scale: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, flexWrap: 'wrap' },
  scaleEdge: { gap: 3, flexShrink: 1 },
  alignEnd: { alignItems: 'flex-end', marginLeft: 'auto' },
  scaleLabel: { fontSize: 12, color: color.text },
  scaleValue: { fontSize: 12, color: color.muted, fontVariant: ['tabular-nums'] },
  scaleMiddle: { fontSize: 12, color: color.muted, flexShrink: 1 },
  requestRow: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 5, minHeight: 20 },
  request: { fontSize: 13, color: color.muted, flexShrink: 1 },
  presets: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 5 },
  preset: {
    minHeight: minimumTouchTarget, minWidth: 58, flexGrow: 1, flexBasis: '20%', borderRadius: 10,
    backgroundColor: color.raised, borderWidth: 1, borderColor: color.line,
    paddingHorizontal: 10, paddingVertical: 12, alignItems: 'center', justifyContent: 'center', gap: 6,
  },
  presetSelected: { borderColor: color.accent, backgroundColor: '#333027' },
  widePreset: { flexBasis: '45%' },
  presetLabel: { color: color.text, fontSize: 14, fontVariant: ['tabular-nums'], textAlign: 'center' },
  presetLabelSelected: { color: color.accent, fontWeight: '600' },
  presetSwatch: { width: 16, height: 3, borderRadius: 2 },
  note: { fontSize: 12, color: color.muted, marginTop: 2 },
  warning: { color: color.error },
  disabled: { opacity: 0.5 },
  pressed: { opacity: 0.65 },
});
