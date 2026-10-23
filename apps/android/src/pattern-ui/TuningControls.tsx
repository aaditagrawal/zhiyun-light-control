import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import Slider from '@react-native-community/slider';
import { colors as c } from './palette';
import { PREVIEW_BASELINE, temperatureColor } from './preview';

export function Chip({ label, selected, disabled = false, onPress }: {
  label: string; selected?: boolean; disabled?: boolean; onPress: () => void;
}) {
  return <Pressable accessibilityRole="button" accessibilityState={{ selected, disabled }} disabled={disabled} onPress={onPress}
    style={({ pressed }) => [s.chip, selected && s.chipSelected, disabled && s.disabled, pressed && s.pressed]}>
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
      maxLength={text ? 64 : 12} selectTextOnFocus style={[s.input, !editable && s.readOnly]} selectionColor={c.accent}
      placeholder={placeholder} placeholderTextColor={c.muted} />
    {hint && <Text style={s.hint}>{hint}</Text>}
  </View>;
}

export function Range({ label, value, min, max, step, unit, onChange }: {
  label: string; value: string; min: number; max: number; step: number; unit: string; onChange: (value: string) => void;
}) {
  const numeric = Number(value);
  const temperature = unit === 'K';
  const fallback = temperature ? PREVIEW_BASELINE.cct : min;
  const current = Math.max(min, Math.min(max, Number.isFinite(numeric) && value !== '' ? numeric : fallback));
  return <View style={s.range}>
    <View style={s.between}>
      <Text style={s.fieldLabel}>{label}</Text>
      <Text style={s.rangeValue}>{value ? `${value} ${unit}` : 'At Start'}</Text>
    </View>
    <View style={s.sliderArea}>
      {temperature && <View pointerEvents="none" style={s.spectrum}>
        {Array.from({ length: 24 }, (_, index) => <View key={index}
          style={[s.colorStep, { backgroundColor: temperatureColor(2700 + 3800 * index / 23) }]} />)}
      </View>}
      <Slider accessibilityLabel={`Requested ${label.toLowerCase()}`} minimumValue={min} maximumValue={max} step={step}
        accessibilityValue={{ min, max, now: current, text: value ? `${value} ${unit}` : 'Temperature captured at Start' }}
        value={current} onValueChange={next => onChange(String(Math.round(next)))}
        minimumTrackTintColor={temperature ? 'transparent' : c.accent} maximumTrackTintColor={temperature ? 'transparent' : c.line}
        thumbTintColor={temperature ? temperatureColor(current) : c.accent} style={s.slider} />
    </View>
    <View style={s.between}>
      <Text style={s.hint}>{temperature ? 'Warm · ' : ''}{min} {unit}</Text>
      <Text style={s.hint}>{temperature ? 'Cool · ' : ''}{max} {unit}</Text>
    </View>
  </View>;
}

const s = StyleSheet.create({
  chip: { minHeight: 48, justifyContent: 'center', paddingHorizontal: 14, paddingVertical: 12, borderRadius: 14,
    backgroundColor: c.raised, borderWidth: 1, borderColor: c.line, alignSelf: 'flex-start' },
  chipSelected: { backgroundColor: c.selected, borderColor: c.accent }, chipText: { color: c.text, fontSize: 14, lineHeight: 20 },
  chipTextSelected: { color: c.accent, fontWeight: '500' }, field: { gap: 8 },
  fieldLabel: { color: c.text, fontSize: 14, lineHeight: 21, fontWeight: '500', flexShrink: 1 },
  input: { color: c.text, backgroundColor: c.raised, borderWidth: 1, borderColor: c.line, borderRadius: 12,
    paddingHorizontal: 13, paddingVertical: 12, minHeight: 50, fontSize: 17, fontVariant: ['tabular-nums'] },
  readOnly: { color: c.muted }, hint: { color: c.muted, fontSize: 12, lineHeight: 19 },
  between: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  range: { gap: 2 }, rangeValue: { color: c.text, fontSize: 17, fontVariant: ['tabular-nums'] },
  sliderArea: { position: 'relative' }, slider: { width: '100%', height: 48 },
  spectrum: { position: 'absolute', left: 15, right: 15, top: 22, height: 4, borderRadius: 2, overflow: 'hidden', flexDirection: 'row' },
  colorStep: { flex: 1 }, pressed: { opacity: 0.65 }, disabled: { opacity: 0.4 },
});
