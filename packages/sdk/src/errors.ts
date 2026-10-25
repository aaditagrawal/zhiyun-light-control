export class LighttimeError extends Error {
  constructor(public readonly code: string, message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = new.target.name;
  }
}
export class TimeoutError extends LighttimeError {
  constructor(message = 'Operation timed out') { super('timeout', message); }
}
export class CancelledError extends LighttimeError {
  constructor() { super('cancelled', 'Operation cancelled'); }
}
export class DisconnectedError extends LighttimeError {
  constructor(message = 'Light is disconnected') { super('disconnected', message); }
}
export class ProtocolError extends LighttimeError {
  constructor(message: string) { super('protocol', message); }
}
export class VerificationError extends LighttimeError {
  constructor(message: string) { super('verification_failed', message); }
}
export class BridgeError extends LighttimeError {
  constructor(code: string, message: string, public readonly status: number) { super(code, message); }
}
