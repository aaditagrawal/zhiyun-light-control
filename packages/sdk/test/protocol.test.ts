import { describe, expect, test } from 'bun:test';
import { COMMAND, crc16Xmodem, decodeValue, encodeFrame, FrameParser, REQUEST_TYPE, valuePayload } from '../src';
import { bytes, capture, hex } from './fixtures';
describe('independent G60 protocol', () => {
  test('CRC XMODEM canonical check vector', () => expect(crc16Xmodem(new TextEncoder().encode('123456789'))).toBe(0x31c3));
  test('float32 wire values retain whole-percent public resolution', () => {
    const payload = valuePayload(COMMAND.brightness, 0, 12);
    expect(new DataView(payload.buffer).getFloat32(3, true)).toBe(12);
    expect(() => valuePayload(COMMAND.brightness, 0, 5.5)).toThrow('whole percent');
    expect(() => valuePayload(COMMAND.cct, 0, 4550)).toThrow('multiple of 100');
  });
  test('all four hardware-captured request frames', () => {
    const requests = [[COMMAND.info, new Uint8Array(), capture.infoTx], [COMMAND.deviceId, new Uint8Array(), capture.idTx],
      [COMMAND.brightness, valuePayload(COMMAND.brightness, 0), capture.dimTx], [COMMAND.cct, valuePayload(COMMAND.cct, 0), capture.cctTx]] as const;
    requests.forEach(([command, payload, expected], i) => expect(hex(encodeFrame({ messageType: REQUEST_TYPE, sequence: i + 1, command, payload }))).toBe(expected));
  });
  test('captured responses survive every fragmentation point and coalescing', () => {
    const all = bytes(capture.infoRx + capture.idRx + capture.dimRx + capture.cctRx);
    for (let at = 0; at <= all.length; at++) {
      const parser = new FrameParser();
      const frames = [...parser.push(all.slice(0, at)), ...parser.push(all.slice(at))];
      expect(frames).toHaveLength(4);
      expect(frames.map(frame => frame.sequence)).toEqual([1, 2, 3, 4]);
      expect(decodeValue(frames[2]!, 0)).toBe(5); expect(decodeValue(frames[3]!, 0)).toBe(5000);
    }
    const parser = new FrameParser();
    expect(Array.from(all).flatMap(byte => parser.push(Uint8Array.of(byte)))).toHaveLength(4);
  });
  test('rejects corruption/invalid length and resynchronizes through noise', () => {
    const bad = bytes(capture.dimRx); bad[14]! ^= 0xff;
    const parser = new FrameParser(64);
    const frames = parser.push(Uint8Array.from([...bytes('0024243cffff'), ...bad, ...bytes(capture.idRx)]));
    expect(frames).toHaveLength(1); expect(frames[0]!.command).toBe(COMMAND.deviceId); expect(parser.corruptFrames).toBeGreaterThanOrEqual(2);
    expect(parser.push(new Uint8Array(100_000))).toEqual([]);
    expect(parser.push(bytes(capture.cctRx))).toHaveLength(1);
  });
  test('honors DataView offsets and rejects malformed device values', () => {
    const frame = new FrameParser().push(bytes(capture.dimRx))[0]!;
    expect(decodeValue({ ...frame, payload: new Uint8Array([...new Uint8Array(3), ...frame.payload]).subarray(3) }, 0)).toBe(5);
    expect(() => decodeValue(frame, 1)).toThrow('device ID');
    expect(() => decodeValue({ ...frame, payload: new Uint8Array(2) }, 0)).toThrow('length');
    const malformed = frame.payload.slice(); new DataView(malformed.buffer).setFloat32(3, NaN, true);
    expect(() => decodeValue({ ...frame, payload: malformed }, 0)).toThrow('out-of-range');
  });
});
