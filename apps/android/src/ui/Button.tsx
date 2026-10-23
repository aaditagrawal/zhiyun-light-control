import { Pressable, StyleSheet, Text } from 'react-native';
import { color, minimumTouchTarget } from './theme';

export function Button({ label, onPress, disabled = false, secondary = false, hint }: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  secondary?: boolean;
  hint?: string;
}) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityHint={hint}
      accessibilityState={{ disabled }} disabled={disabled} onPress={onPress}
      style={({ pressed }) => [s.button, secondary && s.secondary, disabled && s.disabled, pressed && s.pressed]}>
      <Text style={[s.label, secondary && s.secondaryLabel]}>{label}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  button: {
    minHeight: minimumTouchTarget, minWidth: minimumTouchTarget, borderRadius: 12,
    paddingVertical: 13, paddingHorizontal: 18, alignItems: 'center', justifyContent: 'center',
    backgroundColor: color.accent, flexShrink: 1,
  },
  secondary: { backgroundColor: color.raised },
  label: { color: color.ink, fontSize: 15, fontWeight: '600', textAlign: 'center' },
  secondaryLabel: { color: color.text },
  disabled: { opacity: 0.45 },
  pressed: { opacity: 0.7 },
});
