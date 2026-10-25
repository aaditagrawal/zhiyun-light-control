import { Pressable, StyleSheet, Text } from 'react-native';
import { glass, obsidian } from '../pattern-ui/materials';
import { color, font, minimumTouchTarget } from './theme';

/** Primary actions are glossy obsidian capsules; secondary actions are frosted glass. Red is reserved for light. */
export function Button({ label, onPress, disabled = false, secondary = false, hint }: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  secondary?: boolean;
  hint?: string;
}) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} accessibilityHint={hint}
      accessibilityState={{ disabled }} disabled={disabled} onPress={onPress} android_ripple={{ color: 'rgba(255,255,255,0.12)' }}
      style={({ pressed }) => [s.button, secondary ? glass : obsidian, disabled && s.disabled, pressed && s.pressed]}>
      <Text style={[s.label, secondary && s.secondaryLabel]}>{label}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  button: {
    minHeight: Math.max(56, minimumTouchTarget), minWidth: minimumTouchTarget, borderRadius: 28,
    paddingVertical: 14, paddingHorizontal: 24, alignItems: 'center', justifyContent: 'center', flexShrink: 1,
  },
  label: { color: color.text, fontFamily: font.regular, fontSize: 16, lineHeight: 22, textAlign: 'center' },
  secondaryLabel: { fontFamily: font.medium, fontSize: 15 },
  disabled: { opacity: 0.42 },
  pressed: { opacity: 0.8, transform: [{ scale: 0.985 }] },
});
