/** Native Lighttime palette: lit scarlet canvas, white type, pink secondary text. Hue stays 355–8°. */
export const color = {
  background: '#150505',
  surface: '#290605',
  raised: '#3A0B0A',
  line: '#7A2522',
  text: '#FFFFFF',
  muted: '#F2D2D1',
  accent: '#DF2121',
  ink: '#0E0404',
  success: '#FFE3E0',
  error: '#FFB59E',
  errorSurface: '#3A0907',
  warm: '#F3B46E',
  neutral: '#FFF6F0',
  cool: '#C9E2F4',
  rubyDeep: '#871F1C',
  rubyEdge: '#F4939D',
  blush: '#F2D2D1',
} as const;

/** Family names registered by the app font loader from assets/fonts. */
export const font = {
  regular: 'Poppins-Regular',
  medium: 'Poppins-Medium',
  semibold: 'Poppins-SemiBold',
  display: 'PlayfairDisplay',
} as const;

/** Full-height crimson light behind every screen: hot core, oxblood, then ember at the edges. */
export const canvas = {
  backgroundColor: '#150505',
  backgroundImage: [
    'radial-gradient(ellipse 105% 65% at 50% 30%, rgba(223, 33, 33, 0.95) 0%, rgba(216, 34, 33, 0.84) 20%, rgba(194, 36, 34, 0.6) 40%, rgba(150, 23, 22, 0.29) 60%, rgba(97, 11, 10, 0.075) 80%, rgba(97, 11, 10, 0) 100%)',
    'radial-gradient(ellipse 120% 90% at 50% 40%, #8D1212 0%, #610B0A 45%, #290605 78%, #150505 100%)',
  ].join(', '),
} as const;

export const minimumTouchTarget = 48;

/** Indicative white tones, rather than a calibrated preview of the fixture. */
export const temperatureTrack = [
  '#F3B46E', '#F3BC7F', '#F4C48E', '#F5CC9E', '#F6D4AD',
  '#F7DCBC', '#F8E4CC', '#F9ECDC', '#FFF1DD', '#F5F0E3',
  '#F0F0E8', '#EAEEEC', '#E4EDF0', '#DCEBF2', '#D5E8F3',
  '#CFE5F4', '#C9E2F4',
] as const;

export function temperatureSwatch(kelvin: number): string {
  const fraction = Math.max(0, Math.min(1, (kelvin - 2700) / 3800));
  return temperatureTrack[Math.round(fraction * (temperatureTrack.length - 1))]!;
}
