/** Shared native palette. Keep PatternPanel and the connection screen in one tonal family. */
export const color = {
  background: '#101214',
  surface: '#1a1d1e',
  raised: '#262a2c',
  line: '#414648',
  text: '#f4f1e8',
  muted: '#b7babb',
  accent: '#e8d6b4',
  ink: '#211e18',
  success: '#c2d8bf',
  error: '#ffb4a9',
  errorSurface: '#302321',
  warm: '#e6ac6b',
  neutral: '#f4eddc',
  cool: '#cedff0',
} as const;

export const minimumTouchTarget = 48;

/** Indicative white tones, rather than a calibrated preview of the fixture. */
export const temperatureTrack = [
  '#e6ac6b', '#e8b578', '#ebbd87', '#edc695', '#efcfa4',
  '#f1d8b4', '#f3e1c5', '#f4e9d5', '#f4eddc', '#f3efdf',
  '#f0efe3', '#edeee7', '#e8ecea', '#e1e9ec', '#dbe6ee',
  '#d5e3ef', '#cedff0',
] as const;

export function temperatureSwatch(kelvin: number): string {
  const fraction = Math.max(0, Math.min(1, (kelvin - 2700) / 3800));
  return temperatureTrack[Math.round(fraction * (temperatureTrack.length - 1))]!;
}
