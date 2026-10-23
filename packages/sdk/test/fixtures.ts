import { COMMAND, encodeFrame, FrameParser, RESPONSE_TYPE } from '../src';
import type { BleAdapter, Frame, State, Unsubscribe } from '../src';
export const hex = (bytes: Uint8Array) => Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
export const bytes = (value: string) => Uint8Array.from(value.match(/../g)!.map(pair => Number.parseInt(pair, 16)));
export const capture = {
  infoTx: '243c0600000101000320d4ad',
  infoRx: '243c22000100010003203038613430396530633131303031313300706c3130330000000000004f58',
  idTx: '243c0600000102000520ae9c', idRx: '243c0800010002000520000010f6',
  dimTx: '243c0d000001030001100000000000000018ad', dimRx: '243c0d000100030001100000000000a040b4be',
  cctTx: '243c0b000001040002100000000000cd89', cctRx: '243c0b000100040002100000008813a28e',
};
export const state: State = { connected: true, transport: 'ble', address: 'test-light',
  device: { model: 'pl103', serial: '08a409e0c1100113', id: 0 }, brightness: 5, cct: 5000, power: true, updatedAt: '2026-10-23T12:00:00Z' };
export class FakeAdapter implements BleAdapter {
  address = 'test-light'; notification: ((bytes: Uint8Array) => void) | undefined;
  disconnected: (() => void) | undefined; parser = new FrameParser(); frames: Frame[] = [];
  brightness = 5; cct = 5000; ignoreWrites = false; automatic = true;
  writes = 0; subscriptions = 0; disconnects = 0;
  async connect() {}
  async subscribe(listener: (bytes: Uint8Array) => void): Promise<Unsubscribe> {
    this.notification = listener; this.subscriptions++;
    return () => { this.notification = undefined; this.subscriptions--; };
  }
  onDisconnect(listener: () => void): Unsubscribe { this.disconnected = listener; return () => { this.disconnected = undefined; }; }
  async disconnect() { this.disconnects++; this.disconnected?.(); }
  async write(data: Uint8Array) {
    this.writes++;
    for (const frame of this.parser.push(data)) {
      this.frames.push(frame);
      if (!this.automatic) continue;
      if (frame.command === COMMAND.info) this.reply(frame, new FrameParser().push(bytes(capture.infoRx))[0]!.payload);
      else if (frame.command === COMMAND.deviceId) this.reply(frame, new Uint8Array(2));
      else if (frame.command === COMMAND.brightness || frame.command === COMMAND.cct) {
        const payload = frame.payload.slice(); const view = new DataView(payload.buffer);
        if (payload[2] === 1) {
          if (!this.ignoreWrites) {
            if (frame.command === COMMAND.brightness) this.brightness = view.getFloat32(3, true);
            else this.cct = view.getUint16(3, true);
          }
          // The real G60 may emit no command reply for writes.
        } else {
          if (frame.command === COMMAND.brightness) view.setFloat32(3, this.brightness, true);
          else view.setUint16(3, this.cct, true);
          this.reply(frame, payload);
        }
      }
    }
  }
  reply(frame: Frame, payload = frame.payload) {
    this.notification?.(encodeFrame({ ...frame, messageType: RESPONSE_TYPE, payload }));
  }
  drop() { this.disconnected?.(); }
}
