export type ShowcaseCommand = 'front' | 'back' | 'reset' | 'spin' | 'pause' | 'resume' | 'dispose';
export const SHOWCASE_ASSET_URL = 'file:///android_asset/lighttime-showcase.html';
export const SHOWCASE_LOAD_PHASES = ['bootstrap', 'renderer', 'model', 'materials', 'rig', 'compiled'] as const;
export type ShowcaseEvent = { type: 'ready' | 'interaction' | 'error' } | { type: 'state'; spinning: boolean }
  | { type: 'progress'; phase: typeof SHOWCASE_LOAD_PHASES[number] };

/** The viewer is bundled locally. Accept only its small, known status messages. */
export function parseShowcaseEvent(raw: string): ShowcaseEvent | null {
  if (raw.length > 1024) return null;
  try {
    const event: unknown = JSON.parse(raw);
    if (!event || typeof event !== 'object' || !('type' in event)) return null;
    if (event.type === 'ready' || event.type === 'interaction' || event.type === 'error') return { type: event.type };
    if (event.type === 'progress' && 'phase' in event && typeof event.phase === 'string'
      && SHOWCASE_LOAD_PHASES.includes(event.phase as typeof SHOWCASE_LOAD_PHASES[number])) {
      return { type: 'progress', phase: event.phase as typeof SHOWCASE_LOAD_PHASES[number] };
    }
    if (event.type === 'state' && 'spinning' in event && typeof event.spinning === 'boolean') {
      return { type: 'state', spinning: event.spinning };
    }
  } catch { /* A malformed page message never becomes a native action. */ }
  return null;
}

export function showcaseCommandScript(action: ShowcaseCommand): string {
  return `window.G60Showcase?.command(${JSON.stringify(action)});true;`;
}

export function isShowcaseNavigation(url: string): boolean {
  return url === 'about:blank' || url === SHOWCASE_ASSET_URL || url === SHOWCASE_ASSET_URL + '#reduce-motion';
}
