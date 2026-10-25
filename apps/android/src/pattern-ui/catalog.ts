import type { EffectChoiceId } from '../effectForm';

export const PATTERN_GROUPS = [
  { title: 'Atmosphere', ids: ['candle', 'fireplace', 'storm', 'television'] },
  { title: 'Rhythm', ids: ['breathe', 'heartbeat', 'blink'] },
  { title: 'Transitions', ids: ['sunrise15', 'sunset', 'fade-in', 'fade-out', 'cct-sweep'] },
  { title: 'Signals', ids: ['emergency', 'paparazzi', 'sos'] },
  { title: 'Sequence', ids: ['keyframes'] },
] as const satisfies readonly { title: string; ids: readonly EffectChoiceId[] }[];

/** Every scene in browsing order; previous and next follow it. */
export const SCENE_ORDER: readonly EffectChoiceId[] = PATTERN_GROUPS.flatMap(group => [...group.ids]);

export function groupIndexOf(id: EffectChoiceId): number {
  return PATTERN_GROUPS.findIndex(group => (group.ids as readonly EffectChoiceId[]).includes(id));
}

/** Positions and sizes are percentages of a square art box. */
export type ArtLayer =
  | { kind: 'glow'; x: number; y: number; r: number; color: string; alpha?: number; squash?: number }
  | { kind: 'orb'; x: number; y: number; r: number; core: string; mid: string; edge: string }
  | { kind: 'disc'; x: number; y: number; r: number; ring: string; label: string; core: string }
  | { kind: 'pill'; x: number; y: number; w: number; h: number; color: string; rotate?: number; alpha?: number }
  | { kind: 'ring'; x: number; y: number; r: number; color: string; alpha: number }
  | { kind: 'sun'; x: number; y: number; r: number; core: string; edge: string; horizon: number }
  | { kind: 'split'; x: number; y: number; r: number; from: string; to: string }
  | { kind: 'slab'; x: number; y: number; w: number; h: number; from: string; to: string; rotate?: number };

export type CardTone = readonly [light: string, vivid: string, deep: string];

export interface SceneLook {
  mood: string;
  /** Album-style card colours, top to bottom. */
  tone: CardTone;
  /** Lit backdrop of the large artwork: core, middle and outer darkness. */
  sky: readonly [string, string, string];
  art: readonly ArtLayer[];
}

const orange: CardTone = ['#FFB347', '#F2741A', '#B8400C'];
const copper: CardTone = ['#F2804E', '#D2451E', '#7E1A0C'];
const violet: CardTone = ['#EE92C6', '#C24C98', '#6E1A55'];
const magenta: CardTone = ['#F46CA4', '#CC2A72', '#860E48'];
const pink: CardTone = ['#FF93AE', '#E64870', '#A01C44'];
const coral: CardTone = ['#FF7287', '#EE3C50', '#B41A2C'];
const ruby: CardTone = ['#F25A5A', '#CE1E2A', '#7A0A12'];
const gold: CardTone = ['#FFCB52', '#F79A22', '#C2540E'];

// Decorative screen artwork, never an RGB command or a physical G60 measurement.
export const SCENE_LOOKS = {
  candle: { mood: 'Quiet warmth', tone: orange, sky: ['#F0521E', '#8E1410', '#200504'], art: [
    { kind: 'glow', x: 50, y: 42, r: 48, color: '#FF7A2E', alpha: 0.75 },
    { kind: 'glow', x: 50, y: 46, r: 26, color: '#FFD27A', alpha: 0.9, squash: 1.5 },
    { kind: 'orb', x: 50, y: 40, r: 19, core: '#FFF1C8', mid: '#FF9A3C', edge: '#B3200F' },
  ] },
  fireplace: { mood: 'Slow evenings', tone: copper, sky: ['#E2481C', '#7E160C', '#1E0503'], art: [
    { kind: 'glow', x: 50, y: 44, r: 44, color: '#E2301F', alpha: 0.7 },
    { kind: 'glow', x: 32, y: 58, r: 26, color: '#FF6A1F', alpha: 0.85 },
    { kind: 'glow', x: 66, y: 54, r: 30, color: '#FF8E3A', alpha: 0.8 },
    { kind: 'orb', x: 40, y: 46, r: 11, core: '#FFE2A0', mid: '#FF7A2E', edge: '#9E1A0C' },
    { kind: 'orb', x: 60, y: 38, r: 14, core: '#FFEDBC', mid: '#FF8A3D', edge: '#A61C0E' },
    { kind: 'glow', x: 46, y: 22, r: 3, color: '#FFE2A0', alpha: 1 },
    { kind: 'glow', x: 64, y: 16, r: 2.4, color: '#FFE2A0', alpha: 1 },
    { kind: 'glow', x: 36, y: 28, r: 2, color: '#FFD08A', alpha: 1 },
  ] },
  storm: { mood: 'Electric skies', tone: violet, sky: ['#7C2448', '#3A0C1E', '#100307'], art: [
    { kind: 'glow', x: 62, y: 30, r: 44, color: '#C9D8FF', alpha: 0.42 },
    { kind: 'glow', x: 40, y: 58, r: 30, color: '#E2406A', alpha: 0.5 },
    { kind: 'pill', x: 57, y: 32, w: 4.5, h: 46, color: '#F4F8FF', rotate: 24 },
    { kind: 'pill', x: 47, y: 54, w: 3.4, h: 26, color: '#E4EDFF', rotate: -20 },
    { kind: 'pill', x: 41, y: 66, w: 2.6, h: 14, color: '#E4EDFF', rotate: 18, alpha: 0.8 },
  ] },
  television: { mood: 'After hours', tone: magenta, sky: ['#B03466', '#560F30', '#16040C'], art: [
    { kind: 'glow', x: 50, y: 40, r: 46, color: '#F06AA6', alpha: 0.55 },
    { kind: 'slab', x: 50, y: 40, w: 60, h: 42, from: '#FFC2DA', to: '#9E2462' },
    { kind: 'pill', x: 50, y: 66, w: 22, h: 2.6, color: '#FFD8E8', alpha: 0.7 },
  ] },
  breathe: { mood: 'Room to pause', tone: pink, sky: ['#C8406C', '#661230', '#1A040B'], art: [
    { kind: 'glow', x: 50, y: 40, r: 46, color: '#FF7FA4', alpha: 0.55 },
    { kind: 'ring', x: 50, y: 40, r: 36, color: '#FFD6E2', alpha: 0.22 },
    { kind: 'ring', x: 50, y: 40, r: 28, color: '#FFD6E2', alpha: 0.36 },
    { kind: 'ring', x: 50, y: 40, r: 21, color: '#FFD6E2', alpha: 0.55 },
    { kind: 'orb', x: 50, y: 40, r: 14, core: '#FFF0F4', mid: '#FF9DBA', edge: '#B8305A' },
  ] },
  heartbeat: { mood: 'A double beat', tone: coral, sky: ['#E22626', '#7A0E10', '#1C0404'], art: [
    { kind: 'glow', x: 50, y: 44, r: 48, color: '#FF3434', alpha: 0.5 },
    { kind: 'disc', x: 38, y: 33, r: 24, ring: '#E43A3A', label: '#FCC01C', core: '#FF8A2A' },
    { kind: 'disc', x: 63, y: 55, r: 22, ring: '#E43A3A', label: '#FCC01C', core: '#FF8A2A' },
  ] },
  blink: { mood: 'On the beat', tone: ruby, sky: ['#CC2838', '#680C16', '#180405'], art: [
    { kind: 'glow', x: 34, y: 42, r: 34, color: '#FFE2B8', alpha: 0.55 },
    { kind: 'orb', x: 34, y: 42, r: 17, core: '#FFFFFF', mid: '#FFE2B8', edge: '#C2402A' },
    { kind: 'orb', x: 67, y: 42, r: 17, core: '#5A0C10', mid: '#3A0608', edge: '#7E1A1E' },
  ] },
  sunrise15: { mood: 'First light', tone: gold, sky: ['#FF8A3A', '#C2341C', '#360806'], art: [
    { kind: 'glow', x: 50, y: 50, r: 52, color: '#FFB347', alpha: 0.7 },
    { kind: 'sun', x: 50, y: 50, r: 24, core: '#FFF3C4', edge: '#FF9A2E', horizon: 60 },
  ] },
  sunset: { mood: 'Day winds down', tone: coral, sky: ['#E8442A', '#861414', '#200404'], art: [
    { kind: 'glow', x: 50, y: 58, r: 50, color: '#FF5A2A', alpha: 0.7 },
    { kind: 'sun', x: 50, y: 58, r: 26, core: '#FFC27A', edge: '#E2301F', horizon: 64 },
  ] },
  'fade-in': { mood: 'A gentle arrival', tone: pink, sky: ['#D63A44', '#741214', '#1A0404'], art: [
    { kind: 'glow', x: 50, y: 70, r: 50, color: '#FFB0A0', alpha: 0.5, squash: 0.7 },
    { kind: 'pill', x: 30, y: 54, w: 7, h: 12, color: '#FFE6DC', alpha: 0.35 },
    { kind: 'pill', x: 43, y: 48, w: 7, h: 24, color: '#FFE6DC', alpha: 0.55 },
    { kind: 'pill', x: 56, y: 42, w: 7, h: 36, color: '#FFE6DC', alpha: 0.8 },
    { kind: 'pill', x: 69, y: 36, w: 7, h: 48, color: '#FFFFFF', alpha: 1 },
  ] },
  'fade-out': { mood: 'A soft goodbye', tone: violet, sky: ['#A42A44', '#4E0E1C', '#120406'], art: [
    { kind: 'glow', x: 30, y: 40, r: 40, color: '#FFB0C4', alpha: 0.45 },
    { kind: 'pill', x: 31, y: 36, w: 7, h: 48, color: '#FFFFFF', alpha: 1 },
    { kind: 'pill', x: 44, y: 42, w: 7, h: 36, color: '#FFE0EA', alpha: 0.75 },
    { kind: 'pill', x: 57, y: 48, w: 7, h: 24, color: '#FFE0EA', alpha: 0.5 },
    { kind: 'pill', x: 70, y: 54, w: 7, h: 12, color: '#FFE0EA', alpha: 0.3 },
  ] },
  'cct-sweep': { mood: 'Warm into cool', tone: orange, sky: ['#C84A5A', '#621626', '#160406'], art: [
    { kind: 'glow', x: 36, y: 42, r: 34, color: '#FFC46E', alpha: 0.55 },
    { kind: 'glow', x: 64, y: 42, r: 34, color: '#CFE2FF', alpha: 0.45 },
    { kind: 'split', x: 50, y: 42, r: 22, from: '#FFC46E', to: '#D8E8FF' },
  ] },
  emergency: { mood: 'White flashes', tone: ruby, sky: ['#E02424', '#7E0E10', '#1C0404'], art: [
    { kind: 'glow', x: 36, y: 40, r: 32, color: '#FFD9A8', alpha: 0.6 },
    { kind: 'glow', x: 64, y: 40, r: 32, color: '#D6E6FF', alpha: 0.55 },
    { kind: 'pill', x: 37, y: 40, w: 10, h: 38, color: '#FFE6C4' },
    { kind: 'pill', x: 63, y: 40, w: 10, h: 38, color: '#E8F1FF' },
  ] },
  paparazzi: { mood: 'In the spotlight', tone: magenta, sky: ['#B83058', '#5C0E28', '#16040A'], art: [
    { kind: 'glow', x: 32, y: 30, r: 18, color: '#FFFFFF', alpha: 0.8 },
    { kind: 'glow', x: 66, y: 24, r: 24, color: '#FFF4F0', alpha: 0.85 },
    { kind: 'glow', x: 58, y: 54, r: 14, color: '#FFFFFF', alpha: 0.75 },
    { kind: 'glow', x: 38, y: 58, r: 10, color: '#FFE4EC', alpha: 0.7 },
    { kind: 'pill', x: 66, y: 24, w: 1.6, h: 30, color: '#FFFFFF', alpha: 0.85 },
    { kind: 'pill', x: 66, y: 24, w: 1.6, h: 30, color: '#FFFFFF', rotate: 90, alpha: 0.85 },
    { kind: 'pill', x: 32, y: 30, w: 1.4, h: 20, color: '#FFFFFF', rotate: 45, alpha: 0.7 },
    { kind: 'pill', x: 32, y: 30, w: 1.4, h: 20, color: '#FFFFFF', rotate: -45, alpha: 0.7 },
  ] },
  sos: { mood: 'A message in light', tone: copper, sky: ['#CC3A2C', '#6A1010', '#180404'], art: [
    { kind: 'glow', x: 50, y: 42, r: 46, color: '#FF6A3A', alpha: 0.5, squash: 0.6 },
    { kind: 'glow', x: 20, y: 42, r: 4, color: '#FFF0DC', alpha: 1 },
    { kind: 'glow', x: 29, y: 42, r: 4, color: '#FFF0DC', alpha: 1 },
    { kind: 'glow', x: 38, y: 42, r: 4, color: '#FFF0DC', alpha: 1 },
    { kind: 'pill', x: 50, y: 42, w: 4.6, h: 10, color: '#FFF0DC', rotate: 90 },
    { kind: 'pill', x: 62, y: 42, w: 4.6, h: 10, color: '#FFF0DC', rotate: 90 },
    { kind: 'glow', x: 73, y: 42, r: 4, color: '#FFF0DC', alpha: 1 },
    { kind: 'glow', x: 82, y: 42, r: 4, color: '#FFF0DC', alpha: 1 },
  ] },
  keyframes: { mood: 'Make your own', tone: violet, sky: ['#CC3E78', '#661236', '#18040E'], art: [
    { kind: 'glow', x: 50, y: 42, r: 46, color: '#F07AB0', alpha: 0.5 },
    { kind: 'slab', x: 44, y: 46, w: 44, h: 30, from: '#8E2A5A', to: '#3E0A22', rotate: -14 },
    { kind: 'slab', x: 50, y: 42, w: 46, h: 30, from: '#EB86A8', to: '#9E2E62', rotate: -4 },
    { kind: 'slab', x: 56, y: 38, w: 46, h: 30, from: '#FFD0DC', to: '#E2607E', rotate: 7 },
  ] },
} as const satisfies Record<EffectChoiceId, SceneLook>;
