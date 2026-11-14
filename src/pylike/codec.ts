/**
 * CPython text codecs as far as the engine depends on them: the UTF-8 decoder
 * with the three error handlers the oracle reaches (`strict`, `surrogatepass`,
 * `surrogateescape`), UTF-16/32 for `json.loads(bytes)`, the filesystem
 * encoding, and strict base64.
 */
import { PyTypeError, PyUnicodeError, PyValueError } from './py.js';

export type Utf8Errors = 'strict' | 'surrogatepass' | 'surrogateescape';

class UnicodeEncodeError extends PyValueError { override get pyName(): string { return 'UnicodeEncodeError'; } }
/** `binascii.Error`, a `ValueError`. */
export class Base64Error extends PyValueError { override get pyName(): string { return 'Error'; } }

const continuation = (byte: number | undefined): byte is number => byte !== undefined && (byte & 0xc0) === 0x80;

/**
 * `bytes.decode('utf-8', errors)`. Well-formed sequences are exactly Unicode
 * table 3-7. Never strips a byte order mark (unlike `TextDecoder` by default).
 */
export function decodeUtf8(bytes: Uint8Array, errors: Utf8Errors = 'strict'): string {
  const units: number[] = []; let out = '';
  const push = (codePoint: number): void => {
    if (codePoint > 0xffff) { codePoint -= 0x10000; units.push(0xd800 + (codePoint >> 10), 0xdc00 + (codePoint & 0x3ff)); }
    else units.push(codePoint);
    if (units.length >= 8192) { out += String.fromCharCode(...units); units.length = 0; }
  };
  let index = 0;
  while (index < bytes.length) {
    const lead = bytes[index]!;
    if (lead < 0x80) { push(lead); index++; continue; }
    const b1 = bytes[index + 1], b2 = bytes[index + 2], b3 = bytes[index + 3];
    if (lead >= 0xc2 && lead <= 0xdf && continuation(b1)) { push(((lead & 0x1f) << 6) | (b1 & 0x3f)); index += 2; continue; }
    if (lead >= 0xe0 && lead <= 0xef && continuation(b1) && continuation(b2)) {
      const low = lead === 0xe0 ? 0xa0 : 0x80, high = lead === 0xed ? 0x9f : 0xbf;
      if (b1 >= low && b1 <= high) { push(((lead & 0x0f) << 12) | ((b1 & 0x3f) << 6) | (b2 & 0x3f)); index += 3; continue; }
      // `surrogatepass` admits exactly an encoded surrogate: ED A0..BF 80..BF.
      if (errors === 'surrogatepass' && lead === 0xed) { push(((lead & 0x0f) << 12) | ((b1 & 0x3f) << 6) | (b2 & 0x3f)); index += 3; continue; }
    }
    if (lead >= 0xf0 && lead <= 0xf4 && continuation(b1) && continuation(b2) && continuation(b3)) {
      const low = lead === 0xf0 ? 0x90 : 0x80, high = lead === 0xf4 ? 0x8f : 0xbf;
      if (b1 >= low && b1 <= high) { push(((lead & 0x07) << 18) | ((b1 & 0x3f) << 12) | ((b2 & 0x3f) << 6) | (b3 & 0x3f)); index += 4; continue; }
    }
    // Every byte of an ill-formed sequence is >= 0x80, so escaping byte by byte equals CPython's range-wise handler.
    if (errors === 'surrogateescape') { push(0xdc00 + lead); index++; continue; }
    throw new PyUnicodeError("'utf-8' codec can't decode byte");
  }
  return out + String.fromCharCode(...units);
}

/** `bytes.decode('utf-16-le' | 'utf-16-be', 'surrogatepass')`: unpaired surrogates pass, an odd length does not. */
function decodeUtf16(bytes: Uint8Array, little: boolean): string {
  if (bytes.length % 2) throw new PyUnicodeError("'utf-16' codec can't decode byte: truncated data");
  let out = ''; const units: number[] = [];
  for (let index = 0; index < bytes.length; index += 2) {
    units.push(little ? bytes[index]! | (bytes[index + 1]! << 8) : (bytes[index]! << 8) | bytes[index + 1]!);
    if (units.length >= 8192) { out += String.fromCharCode(...units); units.length = 0; }
  }
  return out + String.fromCharCode(...units);
}

/** `bytes.decode('utf-32-le' | 'utf-32-be', 'surrogatepass')`. */
function decodeUtf32(bytes: Uint8Array, little: boolean): string {
  if (bytes.length % 4) throw new PyUnicodeError("'utf-32' codec can't decode bytes: truncated data");
  let out = '';
  for (let index = 0; index < bytes.length; index += 4) {
    const [a, b, c, d] = [bytes[index]!, bytes[index + 1]!, bytes[index + 2]!, bytes[index + 3]!];
    const codePoint = little ? (a | (b << 8) | (c << 16)) + d * 0x1000000 : (d | (c << 8) | (b << 16)) + a * 0x1000000;
    if (codePoint > 0x10ffff) throw new PyUnicodeError("'utf-32' codec can't decode bytes: code point not in range(0x110000)");
    out += codePoint > 0xffff ? String.fromCodePoint(codePoint) : String.fromCharCode(codePoint);
  }
  return out;
}

const starts = (bytes: Uint8Array, ...prefix: number[]): boolean => prefix.every((byte, index) => bytes[index] === byte);

/**
 * `bytes.decode(json.detect_encoding(bytes), 'surrogatepass')`, which is what
 * `json.loads` does with a bytes argument: UTF-8 with or without a mark, and
 * UTF-16 or UTF-32 recognised by a mark or by the position of zero bytes.
 */
export function decodeJsonBytes(bytes: Uint8Array): string {
  if (starts(bytes, 0x00, 0x00, 0xfe, 0xff)) return decodeUtf32(bytes.subarray(4), false);
  if (starts(bytes, 0xff, 0xfe, 0x00, 0x00)) return decodeUtf32(bytes.subarray(4), true);
  if (starts(bytes, 0xfe, 0xff)) return decodeUtf16(bytes.subarray(2), false);
  if (starts(bytes, 0xff, 0xfe)) return decodeUtf16(bytes.subarray(2), true);
  if (starts(bytes, 0xef, 0xbb, 0xbf)) return decodeUtf8(bytes.subarray(3), 'surrogatepass');
  if (bytes.length >= 4) {
    if (!bytes[0]) return bytes[1] ? decodeUtf16(bytes, false) : decodeUtf32(bytes, false);
    if (!bytes[1]) return bytes[2] || bytes[3] ? decodeUtf16(bytes, true) : decodeUtf32(bytes, true);
  } else if (bytes.length === 2) {
    if (!bytes[0]) return decodeUtf16(bytes, false);
    if (!bytes[1]) return decodeUtf16(bytes, true);
  }
  return decodeUtf8(bytes, 'surrogatepass');
}

/**
 * `os.fsencode` followed by the embedded-null check every `os` call makes: UTF-8
 * with `surrogateescape`. A lone surrogate outside U+DC80..U+DCFF cannot be
 * encoded (`UnicodeEncodeError`, a `ValueError`); a NUL is `ValueError`.
 */
export function fsencode(path: string): Buffer {
  const bytes: number[] = [];
  for (let index = 0; index < path.length; index++) {
    const unit = path.charCodeAt(index);
    if (unit < 0x80) bytes.push(unit);
    else if (unit < 0x800) bytes.push(0xc0 | (unit >> 6), 0x80 | (unit & 0x3f));
    else if (unit >= 0xd800 && unit <= 0xdbff && index + 1 < path.length && path.charCodeAt(index + 1) >= 0xdc00 && path.charCodeAt(index + 1) <= 0xdfff) {
      const codePoint = 0x10000 + ((unit - 0xd800) << 10) + (path.charCodeAt(++index) - 0xdc00);
      bytes.push(0xf0 | (codePoint >> 18), 0x80 | ((codePoint >> 12) & 0x3f), 0x80 | ((codePoint >> 6) & 0x3f), 0x80 | (codePoint & 0x3f));
    } else if (unit >= 0xd800 && unit <= 0xdfff) {
      if (unit < 0xdc80 || unit > 0xdcff) throw new UnicodeEncodeError("'utf-8' codec can't encode character: surrogates not allowed");
      bytes.push(unit - 0xdc00);
    } else bytes.push(0xe0 | (unit >> 12), 0x80 | ((unit >> 6) & 0x3f), 0x80 | (unit & 0x3f));
  }
  if (bytes.includes(0)) throw new PyValueError('embedded null byte');
  return Buffer.from(bytes);
}
/** `os.fsdecode`: UTF-8 with `surrogateescape`. */
export const fsdecode = (bytes: Uint8Array): string => decodeUtf8(bytes, 'surrogateescape');

const ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

/**
 * `base64.b64decode(data, validate=True)` of CPython 3.12: `binascii.a2b_base64`
 * in strict mode. A `str` must be ASCII (`ValueError`); anything that is not
 * text or bytes is a `TypeError`. Both are what `inputs.payloads` catches.
 */
export function b64decodeStrict(data: unknown): Buffer {
  let text: string;
  if (typeof data === 'string') {
    for (let index = 0; index < data.length; index++) if (data.charCodeAt(index) > 127) throw new PyValueError('string argument should contain only ASCII characters');
    text = data;
  } else if (data instanceof Uint8Array) text = Buffer.from(data).toString('latin1');
  else throw new PyTypeError('argument should be a bytes-like object or ASCII string');
  if (text.startsWith('=')) throw new Base64Error('Leading padding not allowed');
  const out: number[] = []; let quad = 0, left = 0, pads = 0, padding = false;
  for (let index = 0; index < text.length; index++) {
    const char = text[index]!;
    if (char === '=') {
      padding = true;
      if (quad >= 2 && quad + ++pads >= 4) {
        if (index + 1 < text.length) throw new Base64Error('Excess data after padding');
        return Buffer.from(out);
      }
      continue;
    }
    const value = ALPHABET.indexOf(char);
    if (value < 0) throw new Base64Error('Only base64 data is allowed');
    if (padding) throw new Base64Error('Discontinuous padding not allowed');
    pads = 0;
    if (quad === 0) { quad = 1; left = value; }
    else if (quad === 1) { quad = 2; out.push((left << 2) | (value >> 4)); left = value & 0x0f; }
    else if (quad === 2) { quad = 3; out.push((left << 4) | (value >> 2)); left = value & 0x03; }
    else { quad = 0; out.push((left << 6) | value); left = 0; }
  }
  if (quad !== 0) throw new Base64Error(quad === 1 ? 'Invalid base64-encoded string: number of data characters cannot be 1 more than a multiple of 4' : 'Incorrect padding');
  return Buffer.from(out);
}
/** `base64.b64encode(data).decode('ascii')`. */
export const b64encode = (data: Uint8Array): string => Buffer.from(data.buffer, data.byteOffset, data.byteLength).toString('base64');
