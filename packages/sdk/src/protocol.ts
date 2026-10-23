import { ProtocolError } from './errors';
import { validatePatch } from './types';
export const COMMAND = { info: 0x2003, deviceId: 0x2005, brightness: 0x1001, cct: 0x1002 } as const;
export const REQUEST_TYPE = 0x0100;
export const RESPONSE_TYPE = 0x0001;
export interface Frame { messageType: number; sequence: number; command: number; payload: Uint8Array }
export function crc16Xmodem(bytes: Uint8Array): number {
  let crc = 0;
  for (const byte of bytes) {
    crc ^= byte << 8;
    for (let bit = 0; bit < 8; bit++) crc = ((crc << 1) ^ ((crc & 0x8000) ? 0x1021 : 0)) & 0xffff;
  }
  return crc;
}
function uint16(value: number): void {
  if (!Number.isInteger(value) || value < 0 || value > 0xffff) throw new RangeError('Expected uint16');
}
export function encodeFrame(frame: Frame): Uint8Array {
  uint16(frame.messageType); uint16(frame.sequence); uint16(frame.command);
  const length = frame.payload.length + 6;
  uint16(length);
  const bytes = new Uint8Array(length + 6);
  const view = new DataView(bytes.buffer);
  bytes.set([0x24, 0x3c]); view.setUint16(2, length, true);
  view.setUint16(4, frame.messageType, true); view.setUint16(6, frame.sequence, true);
  view.setUint16(8, frame.command, true); bytes.set(frame.payload, 10);
  view.setUint16(length + 4, crc16Xmodem(bytes.subarray(4, length + 4)), true);
  return bytes;
}
/** Bounded incremental parser. Bad lengths/CRCs resynchronize one byte at a time. */
export class FrameParser {
  private buffer: Uint8Array;
  private used = 0;
  public corruptFrames = 0;
  constructor(public readonly maxBodyLength = 1024) {
    if (!Number.isInteger(maxBodyLength) || maxBodyLength < 6 || maxBodyLength > 65535) throw new RangeError('Invalid maximum frame length');
    this.buffer = new Uint8Array(maxBodyLength + 6);
  }
  reset(): void { this.used = 0; }
  push(chunk: Uint8Array): Frame[] {
    const frames: Frame[] = [];
    for (const byte of chunk) {
      this.buffer[this.used++] = byte;
      while (this.used >= 2) {
        if (this.buffer[0] !== 0x24 || this.buffer[1] !== 0x3c) { this.drop(); continue; }
        if (this.used < 4) break;
        const view = new DataView(this.buffer.buffer);
        const length = view.getUint16(2, true);
        if (length < 6 || length > this.maxBodyLength) { this.corruptFrames++; this.drop(); continue; }
        if (this.used < length + 6) break;
        if (crc16Xmodem(this.buffer.subarray(4, length + 4)) !== view.getUint16(length + 4, true)) {
          this.corruptFrames++; this.drop(); continue;
        }
        frames.push({ messageType: view.getUint16(4, true), sequence: view.getUint16(6, true),
          command: view.getUint16(8, true), payload: this.buffer.slice(10, length + 4) });
        this.buffer.copyWithin(0, length + 6, this.used); this.used -= length + 6;
      }
    }
    return frames;
  }
  private drop(): void { this.buffer.copyWithin(0, 1, this.used); this.used--; }
}
export function valuePayload(command: number, deviceId: number, value?: number): Uint8Array {
  uint16(deviceId);
  if (command !== COMMAND.brightness && command !== COMMAND.cct) throw new ProtocolError('Unsupported value command');
  if (value !== undefined) validatePatch(command === COMMAND.brightness ? { brightness: value } : { cct: value });
  const bytes = new Uint8Array(command === COMMAND.brightness ? 7 : 5);
  const view = new DataView(bytes.buffer); view.setUint16(0, deviceId, true);
  bytes[2] = value === undefined ? 0 : 1;
  if (command === COMMAND.brightness) view.setFloat32(3, value ?? 0, true);
  else view.setUint16(3, value ?? 0, true);
  return bytes;
}
export function decodeValue(frame: Frame, deviceId: number): number {
  const expected = frame.command === COMMAND.brightness ? 7 : frame.command === COMMAND.cct ? 5 : 0;
  if (!expected || frame.payload.length !== expected) throw new ProtocolError('Unexpected value payload length');
  const view = new DataView(frame.payload.buffer, frame.payload.byteOffset, frame.payload.byteLength);
  if (view.getUint16(0, true) !== deviceId || view.getUint8(2) !== 0) throw new ProtocolError('Unexpected device ID or read mode');
  const value = frame.command === COMMAND.brightness ? view.getFloat32(3, true) : view.getUint16(3, true);
  if (!Number.isFinite(value) || (frame.command === COMMAND.brightness ? value < 0 || value > 100 : value < 2700 || value > 6500)) {
    throw new ProtocolError('Device returned an out-of-range value');
  }
  return value;
}
