/** Active-time loading limits: real progress renews the stall window, never the total budget. */
export class ShowcaseLoadBudget {
  private elapsed = 0;
  private lastProgress = 0;
  private started: number | null = null;

  constructor(private readonly now: () => number = () => performance.now()) {}

  private activeTime() {
    return this.elapsed + (this.started === null ? 0 : this.now() - this.started);
  }

  resume() { if (this.started === null) this.started = this.now(); }
  pause() { this.elapsed = this.activeTime(); this.started = null; }
  progress() { this.lastProgress = this.activeTime(); }
  reset() { this.elapsed = 0; this.lastProgress = 0; this.started = null; }

  remaining() {
    const active = this.activeTime();
    return Math.max(0, Math.min(60_000 - active, 20_000 - (active - this.lastProgress)));
  }
}
