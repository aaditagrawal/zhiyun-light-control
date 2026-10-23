/** One outstanding UI operation: no queued slider writes after the user's next action. */
export class OperationGate {
  private held = false;

  get busy(): boolean { return this.held; }

  async run(task: () => Promise<void>): Promise<boolean> {
    if (this.held) return false;
    this.held = true;
    try { await task(); return true; }
    finally { this.held = false; }
  }
}
