const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
const digits = new Int16Array(128).fill(-1);
for (let index = 0; index < alphabet.length; index++) digits[alphabet.charCodeAt(index)] = index;
digits[45] = 62; // Also accept the URL-safe alphabet.
digits[95] = 63;

/** Encode bytes without depending on browser globals or Node's Buffer. */
export function encodeBase64(bytes: Uint8Array): string {
  let result = '';
  let index = 0;
  for (; index + 2 < bytes.length; index += 3) {
    const word = bytes[index]! << 16 | bytes[index + 1]! << 8 | bytes[index + 2]!;
    result += alphabet[word >>> 18]! + alphabet[word >>> 12 & 63]!
      + alphabet[word >>> 6 & 63]! + alphabet[word & 63]!;
  }
  if (index < bytes.length) {
    const first = bytes[index]!;
    const second = bytes[index + 1];
    result += alphabet[first >>> 2]! + alphabet[(first & 3) << 4 | (second ?? 0) >>> 4]!
      + (second === undefined ? '=' : alphabet[(second & 15) << 2]!) + '=';
  }
  return result;
}

/** Decode a padded native BLE value into a fresh, independently owned array. */
export function decodeBase64(value: string): Uint8Array {
  if (value.length % 4 !== 0) throw new Error('Invalid base64 length.');
  const padding = value.endsWith('==') ? 2 : value.endsWith('=') ? 1 : 0;
  const bytes = new Uint8Array(value.length / 4 * 3 - padding);
  let output = 0;
  const fullEnd = padding ? value.length - 4 : value.length;
  for (let index = 0; index < fullEnd; index += 4) {
    const ca = value.charCodeAt(index);
    const cb = value.charCodeAt(index + 1);
    const cc = value.charCodeAt(index + 2);
    const cd = value.charCodeAt(index + 3);
    const a = digits[ca]!;
    const b = digits[cb]!;
    const c = digits[cc]!;
    const d = digits[cd]!;
    if ((ca | cb | cc | cd) > 127 || (a | b | c | d) < 0) throw new Error('Invalid base64 value.');
    const word = a << 18 | b << 12 | c << 6 | d;
    bytes[output++] = word >>> 16;
    bytes[output++] = word >>> 8;
    bytes[output++] = word;
  }
  if (padding) {
    const ca = value.charCodeAt(fullEnd);
    const cb = value.charCodeAt(fullEnd + 1);
    const cc = padding === 1 ? value.charCodeAt(fullEnd + 2) : 65;
    const a = digits[ca]!;
    const b = digits[cb]!;
    const c = digits[cc]!;
    if ((ca | cb | cc) > 127 || (a | b | c) < 0) throw new Error('Invalid base64 value.');
    bytes[output++] = a << 2 | b >>> 4;
    if (padding === 1) bytes[output] = b << 4 | c >>> 2;
  }
  return bytes;
}
