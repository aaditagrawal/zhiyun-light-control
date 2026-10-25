export type ShowcaseCommand = 'front' | 'back' | 'reset' | 'spin' | 'pause' | 'resume' | 'dispose';
export const SHOWCASE_ASSET_URL = 'file:///android_asset/lighttime-showcase.html';
/** The only document variants: fullscreen or transparent inline, each with an optional reduced-motion start. */
export const SHOWCASE_VARIANTS = ['', '#reduce-motion', '#inline', '#inline-reduce-motion'] as const;
export const SHOWCASE_LOAD_PHASES = ['bootstrap', 'renderer', 'model', 'materials', 'rig', 'compiled'] as const;
export type ShowcaseEvent = { type: 'ready' | 'interaction' | 'error' } | { type: 'state'; spinning: boolean }
  | { type: 'progress'; phase: typeof SHOWCASE_LOAD_PHASES[number] };

export function showcaseUri({ inline = false, reducedMotion = false }: { inline?: boolean; reducedMotion?: boolean }): string {
  return SHOWCASE_ASSET_URL + (inline ? reducedMotion ? '#inline-reduce-motion' : '#inline' : reducedMotion ? '#reduce-motion' : '');
}

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
  return url === 'about:blank' || SHOWCASE_VARIANTS.some(variant => url === SHOWCASE_ASSET_URL + variant);
}

type Touch = { identifier: string | number };

/**
 * Holds a parent scroll view while any finger rests on an inline model. A lone
 * new finger discards stale touches, so a missed end event cannot keep the
 * page locked past the next gesture.
 */
export class TouchLock {
  private readonly touches = new Set<string>();
  private locked = false;

  constructor(private readonly notify: (locked: boolean) => void) {}

  get active() { return this.locked; }

  start(changed: readonly Touch[], total: number) {
    if (total <= 1) this.touches.clear();
    for (const touch of changed) this.touches.add(String(touch.identifier));
    this.set(true);
  }

  end(changed: readonly Touch[], remaining: number) {
    for (const touch of changed) this.touches.delete(String(touch.identifier));
    if (this.touches.size === 0 || remaining === 0) this.release();
  }

  release() {
    this.touches.clear();
    this.set(false);
  }

  private set(next: boolean) {
    if (this.locked === next) return;
    this.locked = next;
    this.notify(next);
  }
}
