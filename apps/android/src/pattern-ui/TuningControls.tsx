import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import Slider from '@react-native-community/slider';
import { glass, rgba, softShadow } from './materials';
import { colors as c, type as t } from './palette';
import { PREVIEW_BASELINE, temperatureColor } from './preview';

export function Chip({ label, selected, disabled = false, onPress }: {
  label: string; selected?: boolean; disabled?: boolean; onPress: () => void;
}) {
  return <Pressable accessibilityRole="button" accessibilityState={{ selected, disabled }} disabled={disabled} onPress={onPress}
    style={({ pressed }) => [s.chip, selected ? s.chipSelected : glass, disabled && s.disabled, pressed && s.pressed]}>
    <Text style={[s.chipText, selected && s.chipTextSelected]}>{label}</Text>
  </Pressable>;
}

export function Field({ label, value, onChange, hint, text = false, placeholder, editable = true, accessibilityLabel }: {
  label: string; value: string; onChange: (value: string) => void; hint?: string; text?: boolean;
  placeholder?: string; editable?: boolean; accessibilityLabel?: string;
}) {
  return <View style={s.field}>
    <Text style={s.fieldLabel}>{label}</Text>
    <TextInput accessibilityLabel={accessibilityLabel ?? label} value={value} onChangeText={onChange} editable={editable}
      keyboardType={text ? 'default' : 'decimal-pad'} autoCorrect={false} autoCapitalize={text ? 'characters' : 'none'}
      maxLength={text ? 64 : 12} selectTextOnFocus style={[s.input, !editable && s.readOnly]} selectionColor={c.rim}
      cursorColor={c.white} placeholder={placeholder} placeholderTextColor={c.soft} />
    {hint && <Text style={s.hint}>{hint}</Text>}
  </View>;
}

const TICKS = 23;

/**
 * A lit trough of tick pills. The white active pill is the visible thumb; the native
 * slider on top keeps a transparent thumb and remains the accessible, draggable input.
 */
export function Range({ label, value, min, max, step, unit, onChange }: {
  label: string; value: string; min: number; max: number; step: number; unit: string; onChange: (value: string) => void;
}) {
  const numeric = Number(value);
  const temperature = unit === 'K';
  const fallback = temperature ? PREVIEW_BASELINE.cct : min;
  const current = Math.max(min, Math.min(max, Number.isFinite(numeric) && value !== '' ? numeric : fallback));
  const fraction = (current - min) / (max - min);
  const nearest = Math.round(fraction * (TICKS - 1));
  const light = temperature ? temperatureColor(current) : '#FCC01C';
  return <View style={s.range}>
    <View style={s.between}>
      <Text style={s.fieldLabel}>{label}</Text>
      <Text style={s.rangeValue}>{value ? `${value} ${unit}` : 'At Start'}</Text>
    </View>
    <View style={s.trough}>
      {temperature && <View pointerEvents="none" style={[s.band, {
        backgroundImage: `linear-gradient(90deg, ${rgba('#F39A3E', 0.62)} 0%, ${rgba('#F3B46E', 0.22)} 30%, ${rgba('#FFF1DD', 0.06)} 50%, ${rgba('#9CC8F0', 0.2)} 72%, ${rgba('#9CC8F0', 0.55)} 100%)` }]} />}
      <View pointerEvents="none" style={[s.band, { backgroundImage:
        `radial-gradient(ellipse 34% 130% at ${Math.round(8 + fraction * 84)}% 50%, ${rgba(light, value ? 0.95 : 0.4)} 0%, ${rgba(light, 0)} 100%)` }]} />
      <View pointerEvents="none" style={s.ticks}>
        {Array.from({ length: TICKS }, (_, index) => <View key={index} style={[s.tick,
          index === nearest ? s.tickActive : index < nearest && !temperature ? s.tickLit : s.tickDim,
          index !== nearest && { opacity: 0.4 + 0.6 * (1 - Math.min(1, Math.abs(index - nearest) / 9)) }]} />)}
      </View>
      <Slider accessibilityLabel={`Requested ${label.toLowerCase()}`} minimumValue={min} maximumValue={max} step={step}
        accessibilityValue={{ min, max, now: current, text: value ? `${value} ${unit}` : 'Temperature captured at Start' }}
        value={current} onValueChange={next => onChange(String(Math.round(next)))}
        minimumTrackTintColor="transparent" maximumTrackTintColor="transparent"
        thumbTintColor="transparent" style={s.slider} />
    </View>
    {temperature && <View style={s.between}>
      <Text style={s.hint}>Warm · {min} K</Text>
      <Text style={s.hint}>Cool · {max} K</Text>
    </View>}
  </View>;
}

const s = StyleSheet.create({
  chip: { minHeight: 48, minWidth: 48, maxWidth: '100%', justifyContent: 'center', paddingHorizontal: 18, paddingVertical: 11,
    borderRadius: 24, alignSelf: 'flex-start' },
  chipSelected: { backgroundColor: '#C81E2A',
    backgroundImage: 'linear-gradient(180deg, #F2565E 0%, #CC1E2A 55%, #8E0E16 100%)',
    boxShadow: `inset 0 1.5px 0 rgba(255, 228, 228, 0.7), inset 0 -6px 12px -4px rgba(255, 190, 190, 0.5), ${softShadow('223, 33, 33', 0.34, 0.24, 36, 10)}` },
  chipText: { color: c.pink, fontFamily: t.regular, fontSize: 15, lineHeight: 22, flexShrink: 1 },
  chipTextSelected: { color: c.white, fontFamily: t.medium },
  field: { gap: 8 },
  fieldLabel: { color: c.white, fontFamily: t.medium, fontSize: 15, lineHeight: 22, flexShrink: 1 },
  input: { color: c.white, fontFamily: t.medium, fontSize: 18, minHeight: 54, borderRadius: 27, paddingHorizontal: 20,
    paddingVertical: 12, backgroundColor: '#140303', fontVariant: ['tabular-nums'],
    boxShadow: 'inset 0 4px 10px rgba(0, 0, 0, 0.75), inset 0 -1px 0 rgba(255, 170, 170, 0.3), inset 0 0 0 1px rgba(255, 150, 150, 0.12)' },
  readOnly: { color: c.soft },
  hint: { color: c.soft, fontFamily: t.regular, fontSize: 13, lineHeight: 19 },
  between: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  range: { gap: 10 },
  rangeValue: { color: c.white, fontFamily: t.semibold, fontSize: 22, lineHeight: 30, fontVariant: ['tabular-nums'] },
  trough: { height: 64, borderRadius: 32, backgroundColor: '#120303', overflow: 'hidden', justifyContent: 'center',
    boxShadow: 'inset 0 5px 12px rgba(0, 0, 0, 0.85), inset 0 -1.5px 0 rgba(255, 170, 170, 0.32), 0 1px 0 rgba(255, 190, 190, 0.12)' },
  band: { position: 'absolute', top: 0, bottom: 0, left: 0, right: 0 },
  ticks: { position: 'absolute', left: 18, right: 18, top: 0, bottom: 0, flexDirection: 'row', alignItems: 'center',
    justifyContent: 'space-between' },
  tick: { width: 4, borderRadius: 2 },
  tickLit: { height: 22, backgroundColor: c.white },
  tickDim: { height: 18, backgroundColor: 'rgba(255, 220, 220, 0.4)' },
  tickActive: { width: 10, height: 38, borderRadius: 5, backgroundColor: c.white, boxShadow: '0 0 4px rgba(255, 236, 200, 0.7), 0 0 12px rgba(255, 236, 200, 0.4)' },
  slider: { position: 'absolute', left: 2, right: 2, height: 64, opacity: 0.9 },
  pressed: { opacity: 0.8, transform: [{ scale: 0.98 }] }, disabled: { opacity: 0.4 },
});
