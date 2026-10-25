import { color, font } from '../ui/theme';

// Share the app's base palette. The scene stage adds a lit-scarlet range
// (hue 355–8°), white type with pink secondary text, and jelly-glass rims.
export const colors = {
  ...color,
  white: '#FFFFFF',
  pink: '#F2D2D1',
  blush: '#FFBBBC',
  soft: 'rgba(242, 210, 209, 0.74)',
  night: '#0E0404',
  ember: '#150505',
  coal: '#290605',
  oxblood: '#610B0A',
  garnet: '#871F1C',
  crimson: '#C22422',
  scarlet: '#DF2121',
  neon: '#FF3B3B',
  rim: '#F4939D',
  gold: '#FCC01C',
  copper: '#A06E43',
  peach: '#FFB59E',
  live: '#FF6A5E',
} as const;

/** The app's bundled Poppins and Playfair families. */
export const type = font;
