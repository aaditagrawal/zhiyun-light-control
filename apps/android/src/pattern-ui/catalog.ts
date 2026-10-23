import type { EffectChoiceId } from '../effectForm';

export const PATTERN_GROUPS = [
  { title: 'Atmosphere', ids: ['candle', 'fireplace', 'storm', 'television'] },
  { title: 'Rhythm', ids: ['breathe', 'heartbeat', 'blink'] },
  { title: 'Transitions', ids: ['sunrise15', 'sunset', 'fade-in', 'fade-out', 'cct-sweep'] },
  { title: 'Signals', ids: ['emergency', 'paparazzi', 'sos'] },
  { title: 'Sequence', ids: ['keyframes'] },
] as const satisfies readonly { title: string; ids: readonly EffectChoiceId[] }[];
