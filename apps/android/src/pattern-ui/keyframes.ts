import type { EffectEase, EffectKeyframe } from '@lighttime/sdk';

export const MAX_KEYFRAMES = 256;
export interface FrameDraft { id: number; at: string; brightness: string; cct: string; ease: EffectEase }

export function draftsFromFrames(frames: readonly EffectKeyframe[]): FrameDraft[] {
  return frames.map((frame, id) => ({ id, at: String(frame.at),
    brightness: frame.brightness === undefined ? '' : String(frame.brightness),
    cct: frame.cct === undefined ? '' : String(frame.cct), ease: frame.ease ?? 'linear' }));
}

/** Keep incomplete edits invalid; blank optional outputs deliberately carry forward. */
export function keyframesFromDrafts(drafts: readonly FrameDraft[]): EffectKeyframe[] {
  return drafts.map(frame => ({ at: frame.at.trim() ? Number(frame.at) : Number.NaN,
    ...(frame.brightness.trim() ? { brightness: Number(frame.brightness) } : {}),
    ...(frame.cct.trim() ? { cct: Number(frame.cct) } : {}), ease: frame.ease }));
}

export function nextKeyframeAt(drafts: readonly FrameDraft[]): number | null {
  if (drafts.length >= MAX_KEYFRAMES) return null;
  const text = drafts[drafts.length - 1]?.at;
  const last = text?.trim() ? Number(text) : Number.NaN;
  if (!Number.isFinite(last) || last < 0 || last > 3599.9) return null;
  return Math.min(3600, Math.round((last + 1) * 10) / 10);
}
