import { describe, expect, test } from 'bun:test';
import { decodeBase64, encodeBase64 } from '../src/ble/base64';
import captures from './fixtures/g60-reads.json';

describe('native BLE base64', () => {
  test.each([
    ['', ''], ['f', 'Zg=='], ['fo', 'Zm8='], ['foo', 'Zm9v'],
    ['foob', 'Zm9vYg=='], ['fooba', 'Zm9vYmE='], ['foobar', 'Zm9vYmFy'],
  ])('matches the RFC 4648 vector %j', (plain, encoded) => {
    const bytes = new TextEncoder().encode(plain);
    expect(encodeBase64(bytes)).toBe(encoded);
    expect(decodeBase64(encoded)).toEqual(bytes);
  });

  test('accepts standard and URL-safe alphabets and ignores unused pad bits', () => {
    expect(decodeBase64('+/8=')).toEqual(new Uint8Array([251, 255]));
    expect(decodeBase64('-_8=')).toEqual(new Uint8Array([251, 255]));
    expect(decodeBase64('Zh==')).toEqual(new Uint8Array([102]));
    expect(decodeBase64('Zm9=')).toEqual(new Uint8Array([102, 111]));
  });

  test('matches an independent platform oracle for every byte, tail length and large input', () => {
    for (const length of [0, 1, 2, 3, 7, 20, 40, 256, 511, 512, 16_383, 16_384, 65_537]) {
      const bytes = Uint8Array.from({ length }, (_, index) => (index * 73 + length) & 255);
      const expected = Buffer.from(bytes).toString('base64');
      expect(encodeBase64(bytes)).toBe(expected);
      expect(decodeBase64(expected)).toEqual(bytes);
    }
    for (let byte = 0; byte < 256; byte++) {
      const bytes = new Uint8Array([byte]);
      expect(encodeBase64(bytes)).toBe(Buffer.from(bytes).toString('base64'));
    }
  });

  test('round trips captured queries and notifications at every fragmentation boundary', () => {
    for (const capture of [captures.info, captures.id, captures.brightness, captures.cct]) {
      for (const hex of [capture.tx, capture.rx]) {
        const bytes = Uint8Array.from(Buffer.from(hex, 'hex'));
        for (let split = 0; split <= bytes.length; split++) {
          for (const part of [bytes.subarray(0, split), bytes.subarray(split)]) {
            const encoded = Buffer.from(part).toString('base64');
            expect(encodeBase64(part)).toBe(encoded);
            expect(decodeBase64(encoded)).toEqual(part);
          }
        }
      }
    }
  });

  test('honors byte views and returns independent notification storage', () => {
    const backing = new Uint8Array([99, 102, 111, 111, 99]);
    expect(encodeBase64(backing.subarray(1, 4))).toBe('Zm9v');
    const first = decodeBase64('Zm9v');
    first.fill(0);
    expect(decodeBase64('Zm9v')).toEqual(new Uint8Array([102, 111, 111]));
  });

  test.each(['A', 'Zg', 'Zg=', 'Zg===', '=m9v', 'Zm=v', 'Zm9v====', '====', 'A===', 'Zg==AAAA',
    'Zg A', 'Zg\n=', 'Zm9!', 'Zm9é', 'Zm9\u0141', 'Zm9😀'])('rejects corrupt native data %j', (value) => {
    expect(() => decodeBase64(value)).toThrow('Invalid base64');
  });
});
