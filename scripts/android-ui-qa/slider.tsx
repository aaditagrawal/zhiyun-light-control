import React from 'react';
import { StyleSheet } from 'react-native';

// Match the native control's transparent tracks instead of drawing the browser's
// default grey bar across the app's instrument artwork. The thumb stays visible.
const sheet = document.createElement('style');
sheet.textContent = `
.lighttime-native-slider { appearance: none; -webkit-appearance: none; padding: 0; cursor: pointer; }
.lighttime-native-slider::-webkit-slider-runnable-track { height: 4px; border: 0; background: var(--slider-track); border-radius: 2px; }
.lighttime-native-slider::-webkit-slider-thumb { appearance: none; -webkit-appearance: none; width: 16px; height: 16px; margin-top: -6px; border: 0; border-radius: 50%; background: var(--slider-thumb); box-shadow: var(--slider-thumb-shadow); }
.lighttime-native-slider::-moz-range-track { height: 4px; border: 0; background: var(--slider-track); }
.lighttime-native-slider::-moz-range-thumb { width: 16px; height: 16px; border: 0; border-radius: 50%; background: var(--slider-thumb); }
.lighttime-native-slider:focus-visible { outline: 2px solid #fff; outline-offset: 2px; border-radius: 28px; }
`;
document.head.append(sheet);

export default function Slider(p: any) {
  const layout = StyleSheet.flatten(p.style) ?? {};
  const percent = (p.value - p.minimumValue) / (p.maximumValue - p.minimumValue) * 100;
  const track = p.minimumTrackTintColor === 'transparent' && p.maximumTrackTintColor === 'transparent' ? 'transparent'
    : `linear-gradient(90deg, ${p.minimumTrackTintColor ?? '#fff'} ${percent}%, ${p.maximumTrackTintColor ?? '#ffffff33'} ${percent}%)`;
  return <input className="lighttime-native-slider" aria-label={p.accessibilityLabel} aria-valuetext={p.accessibilityValue?.text}
    aria-busy={p.accessibilityState?.busy} title={p.accessibilityHint} type="range" min={p.minimumValue}
    max={p.maximumValue} step={p.step} value={p.value} disabled={p.disabled}
    onPointerDown={() => p.onSlidingStart?.(p.value)} onChange={e => p.onValueChange?.(+e.target.value)}
    onPointerUp={e => p.onSlidingComplete?.(+(e.target as HTMLInputElement).value)}
    style={{ ...layout, width: layout.width ?? '100%', height: layout.height ?? 48, margin: 0, background: 'transparent',
      '--slider-thumb': p.thumbTintColor ?? '#fff', '--slider-thumb-shadow': p.thumbTintColor === 'transparent' ? 'none' : '0 1px 4px #0006', '--slider-track': track } as React.CSSProperties} />;
}
