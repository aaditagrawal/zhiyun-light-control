/** A session stays owned until effect writes settle; native cleanup must wait for close(). */
export class EffectOwnership {
  private pending: Promise<void> | null = null;

  constructor(
    private readonly runner: { cancel(): void; dispose(): Promise<void> },
    private readonly disconnect: () => Promise<unknown>,
  ) {}

  close(): Promise<void> {
    // Invalidate before the first await, including when close is called again.
    this.runner.cancel();
    if (this.pending) return this.pending;
    this.pending = (async () => {
      await this.runner.dispose();
      await this.disconnect();
    })();
    // A failed drain retains ownership. A later close may retry cleanup.
    void this.pending.catch(() => { this.pending = null; });
    return this.pending;
  }
}

/** Failed teardown keeps its owner reachable, so a later explicit action can retry it. */
export class EffectRetirement {
  private readonly owners = new Set<EffectOwnership>();
  private draining: Promise<void> | null = null;

  get ownedCount(): number { return this.owners.size; }
  retain(owner: EffectOwnership): void { this.owners.add(owner); }

  drain(): Promise<void> {
    if (this.draining) return this.draining;
    const pending = (async () => {
      while (this.owners.size) {
        const failures: unknown[] = [];
        await Promise.all([...this.owners].map(async owner => {
          try { await owner.close(); this.owners.delete(owner); }
          catch (error) { failures.push(error); }
        }));
        if (failures.length) throw failures[0];
      }
    })();
    this.draining = pending;
    const settled = () => { if (this.draining === pending) this.draining = null; };
    void pending.then(settled, settled);
    return pending;
  }
}
