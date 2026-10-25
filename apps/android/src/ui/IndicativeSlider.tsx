import { useEffect, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import Slider from '@react-native-community/slider';
import { color, font, minimumTouchTarget, temperatureSwatch } from './theme';

type Kind = 'brightness' | 'temperature';
type Request = { value: number; pending: boolean; rejected: boolean };

const dialTicks = 41;
const rulerTicks = 20;

function format(value: number, kind: Kind): string {
  return kind === 'brightness' ? `${Number(value.toFixed(1))}%` : `${value} K`;
}

/**
 * The instrument always shows hardware readback. The native slider edits a separate request,
 * drawn only as a ghost marker until the light reads back.
 */
export function IndicativeSlider({ kind, value, verified, working, disabled, onCommit }: {
  kind: Kind;
  value: number;
  verified: boolean;
  working: boolean;
  disabled: boolean;
  onCommit: (value: number) => Promise<void>;
}) {
  const temperature = kind === 'temperature';
  const title = temperature ? 'White balance' : 'Brightness';
  const min = temperature ? 2700 : 0;
  const max = temperature ? 6500 : 100;
  const step = temperature ? 100 : 1;
  const nudge = temperature ? 100 : 5;
  const [draft, setDraft] = useState(value);
  const [dragging, setDragging] = useState(false);
  const [request, setRequest] = useState<Request | null>(null);
  const draggingNow = useRef(false);
  const committing = useRef(false);
  const mounted = useRef(true);
  const { width, height } = useWindowDimensions();
  const locked = disabled || request?.pending === true;
  const thumb = dragging ? draft : request?.pending ? request.value : value;
  const matches = request !== null && Math.abs(request.value - value) <= (temperature ? 0 : 0.01);
  const failed = request !== null && !request.pending && !working && (request.rejected || !verified);
  const settled = request !== null && !request.pending && !working && !failed && matches;
  const fraction = (n: number) => Math.max(0, Math.min(1, (n - min) / (max - min)));
  const ghost = dragging ? draft : request?.pending ? request.value : null;

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  // A concurrent power/read/pattern operation invalidates a gesture before it can write.
  useEffect(() => {
    if (disabled) { draggingNow.current = false; setDragging(false); }
  }, [disabled]);

  // A confirmed request needs no lasting caption; the instrument already shows the readback.
  useEffect(() => {
    if (!settled) return;
    const timer = setTimeout(() => { if (mounted.current) setRequest(null); }, 2400);
    return () => clearTimeout(timer);
  }, [settled, request]);

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

  const requestLabel = dragging ? `Draft ${format(draft, kind)} · release to apply`
    : request ? `Requested ${format(request.value, kind)} · ${request.pending || working ? 'checking…'
      : failed ? 'not confirmed' : matches ? 'readback verified' : 'actual has changed'}`
      : null;
  const actualLabel = `Actual ${title.toLowerCase()}: ${format(value, kind)}. ${verified ? 'Readback verified' : 'Unverified'}`;

  // The instrument fits the available width; the page provides room to scroll.
  const dial = Math.round(Math.max(196, Math.min(width - 96, (height || 852) * 0.33, 288)));
  const disc = Math.round(dial * 0.46);
  const radius = dial / 2 - 17;
  const lit = 0.35 + fraction(value) * 0.65;
  const ringRgb = verified ? '255, 59, 59' : '255, 181, 158';

  const instrument = temperature ? (
    <View accessible accessibilityLabel={actualLabel} style={s.rulerHead}>
      <View accessible={false} style={[s.swatch, { backgroundColor: temperatureSwatch(value) }]} />
      <Text accessible={false} importantForAccessibility="no" style={s.rulerValue} maxFontSizeMultiplier={1.6} numberOfLines={1}>{format(value, kind)}</Text>
    </View>
  ) : (
    <View accessible accessibilityLabel={actualLabel} pointerEvents="none"
      style={[s.well, { width: dial, height: dial, borderRadius: dial / 2 }, locked && s.dimmed]}>
      <View style={[s.cone, { width: dial * 0.9, height: dial * 0.9, borderRadius: dial * 0.45, opacity: lit }]} />
      {[0.97, 0.86, 0.66].map((scale, index) => <View key={scale} style={{
        position: 'absolute', width: dial * scale, height: dial * scale, borderRadius: dial * scale / 2,
        borderWidth: index === 0 ? 2 : 1.25, borderColor: `rgba(${ringRgb}, ${lit * (1 - index * 0.22)})`,
        boxShadow: `0 0 ${Math.round(8 + lit * 14)}px rgba(${ringRgb}, ${lit * 0.65}), inset 0 0 ${Math.round(3 + lit * 6)}px rgba(${ringRgb}, ${lit * 0.6})`,
      }} />)}
      {Array.from({ length: dialTicks }, (_, i) => {
        const at = i / (dialTicks - 1);
        const on = value > 0 && at <= fraction(value) + 0.0001;
        const major = i % 10 === 0;
        const h = major ? 14 : 9;
        return <View key={i} style={[s.tick, {
          left: dial / 2 - 1.5, top: dial / 2 - h / 2, height: h,
          transform: [{ rotate: `${-135 + 270 * at}deg` }, { translateY: -radius }],
        }, on ? [s.tickOn, !verified && s.tickUnverified] : s.tickOff]} />;
      })}
      {ghost !== null && <View style={[s.ghostTick, {
        left: dial / 2 - 4, top: dial / 2 - 12,
        transform: [{ rotate: `${-135 + 270 * fraction(ghost)}deg` }, { translateY: -radius }],
      }]} />}
      <View style={[s.disc, { width: disc, height: disc, borderRadius: disc / 2 }]}>
        <Text style={[s.discValue, { fontSize: Math.round(disc * 0.3), lineHeight: Math.round(disc * 0.38) }]}
          maxFontSizeMultiplier={1.15} numberOfLines={1} adjustsFontSizeToFit>{format(value, kind)}</Text>
      </View>
    </View>
  );

  return (
    <View style={[s.control, temperature && s.ruler]}>
      {instrument}
      <View style={s.row}>
        <Pressable accessibilityRole="button" accessibilityLabel={`Decrease ${title.toLowerCase()}`}
          accessibilityState={{ disabled: locked || value <= min }} disabled={locked || value <= min}
          onPress={() => { void commit(value - nudge); }}
          style={({ pressed }) => [s.nudge, (locked || value <= min) && s.dimmed, pressed && s.pressed]}>
          <View style={s.minus} />
        </Pressable>
        <View style={[s.groove, locked && s.dimmed]}>
          <View pointerEvents="none" accessible={false} importantForAccessibility="no-hide-descendants" style={s.trackInset}>
            {temperature ? <View style={s.trough}>
              {Array.from({ length: rulerTicks }, (_, i) => {
                const at = i / (rulerTicks - 1);
                const nearest = Math.round(fraction(value) * (rulerTicks - 1)) === i;
                return <View key={i} style={[s.rulerTick, at <= fraction(value) + 0.0001 ? { backgroundColor: temperatureSwatch(min + at * (max - min)) } : s.rulerTickOff,
                  nearest && s.rulerTickActual]} />;
              })}
            </View> : <View style={s.channel}>
              <View style={[s.channelFill, { width: `${fraction(value) * 100}%` }]} />
            </View>}
            {ghost !== null && <View style={[s.ghostPin, { left: `${fraction(ghost) * 100}%` }]} />}
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
            thumbTintColor={temperature ? 'transparent' : locked ? color.muted : color.text} style={s.slider} />
        </View>
        <Pressable accessibilityRole="button" accessibilityLabel={`Increase ${title.toLowerCase()}`}
          accessibilityState={{ disabled: locked || value >= max }} disabled={locked || value >= max}
          onPress={() => { void commit(value + nudge); }}
          style={({ pressed }) => [s.nudge, (locked || value >= max) && s.dimmed, pressed && s.pressed]}>
          <View style={s.minus} /><View style={[s.minus, s.plus]} />
        </Pressable>
      </View>
      {requestLabel && <View style={s.status}>
        {temperature && (dragging || request) && <View accessible={false} style={[s.swatch, s.swatchSmall, {
          backgroundColor: temperatureSwatch(dragging ? draft : request!.value),
        }]} />}
        {requestLabel && <Text accessibilityLiveRegion={dragging ? 'none' : 'polite'} style={[s.statusText, failed && s.warning]}>{requestLabel}</Text>}
      </View>}
    </View>
  );
}

const glassFace = {
  backgroundColor: 'rgba(42, 8, 8, 0.55)',
  backgroundImage: 'radial-gradient(ellipse 90% 70% at 50% 0%, rgba(255, 228, 228, 0.22) 0%, rgba(255, 228, 228, 0) 100%)',
  boxShadow: 'inset 0 1px 0 rgba(255, 232, 232, 0.5), inset 0 0 0 1px rgba(255, 200, 200, 0.14), '
    + 'inset 0 -8px 14px -7px rgba(255, 170, 170, 0.32), 0 8px 18px -8px rgba(0, 0, 0, 0.6)',
};

const s = StyleSheet.create({
  control: { alignItems: 'center', gap: 22, width: '100%' },
  ruler: { gap: 16 },
  well: { alignItems: 'center', justifyContent: 'center', backgroundColor: '#050101',
    backgroundImage: 'radial-gradient(circle closest-side, rgba(40, 4, 4, 1) 0%, rgba(8, 1, 1, 1) 55%, rgba(120, 10, 10, 0.75) 100%)',
    boxShadow: 'inset 0 8px 18px rgba(0, 0, 0, 0.95), inset 0 -2px 0 rgba(255, 150, 150, 0.12), 0 1.5px 0 rgba(255, 200, 200, 0.35), '
      + '0 0 0 10px rgba(30, 4, 4, 0.55), 0 0 0 11px rgba(255, 170, 170, 0.16), 0 26px 40px -14px rgba(0, 0, 0, 0.9)' },
  cone: { position: 'absolute', backgroundImage: 'radial-gradient(circle closest-side, rgba(255, 40, 40, 0.4) 0%, rgba(255, 40, 40, 0.35) 20%, rgba(255, 40, 40, 0.24) 40%, rgba(255, 40, 40, 0.11) 60%, rgba(255, 40, 40, 0.025) 80%, rgba(255, 40, 40, 0) 100%)' },
  tick: { position: 'absolute', width: 3, borderRadius: 1.5 },
  tickOn: { backgroundColor: '#FF4A4A', boxShadow: '0 0 6px rgba(255, 40, 40, 0.95)' },
  tickUnverified: { backgroundColor: '#FFB59E', boxShadow: '0 0 12px rgba(255, 181, 158, 0.65)' },
  tickOff: { backgroundColor: 'rgba(255, 140, 140, 0.3)' },
  ghostTick: { position: 'absolute', width: 8, height: 24, borderRadius: 4, borderWidth: 2, borderColor: '#FFFFFF',
    backgroundColor: 'rgba(255, 255, 255, 0.15)', boxShadow: '0 0 10px rgba(255, 255, 255, 0.8)' },
  disc: { alignItems: 'center', justifyContent: 'center', paddingHorizontal: 8, backgroundColor: '#F2EFEF',
    backgroundImage: 'radial-gradient(circle at 38% 30%, rgba(255, 255, 255, 1) 0%, rgba(255, 255, 255, 1) 28%, rgba(222, 214, 214, 1) 74%, rgba(176, 164, 164, 1) 100%)',
    boxShadow: '0 0 0 3px rgba(18, 2, 2, 0.95), 0 0 22px 4px rgba(255, 52, 52, 0.6), inset 0 -4px 8px rgba(0, 0, 0, 0.2)' },
  discValue: { color: color.ink, fontFamily: font.semibold, fontVariant: ['tabular-nums'], letterSpacing: -1, textAlign: 'center' },
  rulerHead: { flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 32 },
  rulerValue: { color: color.text, fontFamily: font.semibold, fontSize: 22, lineHeight: 30, fontVariant: ['tabular-nums'] },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8, width: '100%' },
  nudge: { width: minimumTouchTarget, height: minimumTouchTarget, borderRadius: minimumTouchTarget / 2, alignItems: 'center', justifyContent: 'center', ...glassFace },
  minus: { width: 14, height: 2.5, borderRadius: 1.25, backgroundColor: color.text },
  plus: { position: 'absolute', transform: [{ rotate: '90deg' }] },
  groove: { flex: 1, height: 56, borderRadius: 28, justifyContent: 'center', backgroundColor: '#0B0202',
    boxShadow: 'inset 0 4px 10px rgba(0, 0, 0, 0.95), inset 0 -1.5px 0 rgba(255, 170, 170, 0.22), 0 1px 0 rgba(255, 200, 200, 0.18)' },
  trackInset: { position: 'absolute', left: 16, right: 16, top: 0, bottom: 0, justifyContent: 'center' },
  channel: { height: 8, borderRadius: 4, backgroundColor: 'rgba(255, 90, 90, 0.14)' },
  channelFill: { height: 8, borderRadius: 4, backgroundColor: '#FF3B3B', boxShadow: '0 0 10px rgba(255, 40, 40, 0.9)',
    backgroundImage: 'linear-gradient(90deg, rgba(120, 10, 10, 1) 0%, rgba(255, 59, 59, 1) 100%)' },
  trough: { height: 30, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  rulerTick: { width: 4, height: 16, borderRadius: 2 },
  rulerTickOff: { backgroundColor: 'rgba(255, 190, 190, 0.22)' },
  rulerTickActual: { height: 30, width: 5, borderRadius: 2.5, boxShadow: '0 0 10px rgba(255, 255, 255, 0.9)' },
  ghostPin: { position: 'absolute', width: 4, height: 38, marginLeft: -2, borderRadius: 2, borderWidth: 1.5, borderColor: '#FFFFFF', backgroundColor: 'rgba(255, 255, 255, 0.2)' },
  slider: { width: '100%', height: 56 },
  swatch: { width: 14, height: 14, borderRadius: 7, borderWidth: 1.5, borderColor: 'rgba(255, 255, 255, 0.7)' },
  swatchSmall: { width: 10, height: 10, borderRadius: 5 },
  status: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'center', gap: 8, minHeight: 20 },
  statusText: { color: color.muted, fontFamily: font.regular, fontSize: 13, lineHeight: 19, textAlign: 'center', flexShrink: 1 },
  warning: { color: color.error },
  dimmed: { opacity: 0.5 },
  pressed: { opacity: 0.7, transform: [{ scale: 0.96 }] },
});
