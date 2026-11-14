/**
 * The Python value model the ported engine modules work on, and the handful of
 * Python operators whose failure mode is part of the oracle's behaviour.
 *
 * Plan section 3.2 row 17: many oracle guards are "any exception here is a
 * refusal". A port that lets the JavaScript runtime decide what throws would
 * refuse differently. So every operator below that can raise in Python raises
 * the same *class* here, on purpose, and the callers catch by class exactly
 * where the oracle has an `except` clause.
 */
import { Float, Int } from './json.js';

export { Float, Int };

/**
 * A float Python reads from JSON that is not finite: the literal `1E400`
 * (overflow) or, where the caller's `parse_constant` lets them through, `NaN`,
 * `Infinity` and `-Infinity`. `Float` refuses these by design (waiver W1 at the
 * envelope); documents the engine merely *reads* still have to see them, since
 * the oracle refuses them later and with a different message.
 */
export class NonFinite {
  declare readonly value: number;
  constructor(value: number) {
    if (Number.isFinite(value)) throw new Error('finite number');
    this.value = value; Object.freeze(this);
  }
}

/** A Python `dict` with `str` keys. A `Map` keeps insertion order for every key; a plain object would move integer-like keys to the front. */
export type PyDict = Map<string, PyValue>;
/** What `json.loads` can produce. `bool` is not an `Int`, as `type(x) is int` demands. */
export type PyValue = null | boolean | Int | Float | NonFinite | string | PyValue[] | PyDict;

/** Base of every modelled Python exception. `pyName` is the Python class name. */
export class PyError extends Error {
  get pyName(): string { return 'Exception'; }
  constructor(message = '') { super(message); this.name = this.pyName; }
}
export class PyValueError extends PyError { override get pyName(): string { return 'ValueError'; } }
export class PyUnicodeError extends PyValueError { override get pyName(): string { return 'UnicodeDecodeError'; } }
export class PyTypeError extends PyError { override get pyName(): string { return 'TypeError'; } }
export class PyKeyError extends PyError { override get pyName(): string { return 'KeyError'; } }
export class PyAttributeError extends PyError { override get pyName(): string { return 'AttributeError'; } }
export class PyRuntimeError extends PyError { override get pyName(): string { return 'RuntimeError'; } }
export class PyRecursionError extends PyRuntimeError { override get pyName(): string { return 'RecursionError'; } }
export class PyImportError extends PyError { override get pyName(): string { return 'ImportError'; } }
/** `OSError` and its subclasses; `errno` is the symbolic name (`ENOENT`, ...). */
export class PyOSError extends PyError {
  declare readonly errno: string;
  constructor(errno: string, message = errno) { super(message); this.errno = errno; }
  override get pyName(): string {
    switch (this.errno) {
      case 'ENOENT': return 'FileNotFoundError';
      case 'EACCES': case 'EPERM': return 'PermissionError';
      case 'ENOTDIR': return 'NotADirectoryError';
      case 'EISDIR': return 'IsADirectoryError';
      case 'EEXIST': return 'FileExistsError';
      default: return 'OSError';
    }
  }
}

export const isDict = (value: unknown): value is PyDict => value instanceof Map;
export const isList = (value: unknown): value is PyValue[] => Array.isArray(value);
export const isStr = (value: unknown): value is string => typeof value === 'string';
/** `type(value) is int`: an integer that is not a `bool`. */
export const isInt = (value: unknown): value is Int => value instanceof Int;

/** The name `type(value).__name__` would give; used only in messages of modelled crashes. */
export function typeName(value: PyValue): string {
  if (value === null) return 'NoneType';
  if (typeof value === 'boolean') return 'bool';
  if (typeof value === 'string') return 'str';
  if (value instanceof Int) return 'int';
  if (value instanceof Float || value instanceof NonFinite) return 'float';
  return Array.isArray(value) ? 'list' : 'dict';
}

function numeric(value: PyValue): bigint | number | undefined {
  if (typeof value === 'boolean') return value ? 1n : 0n;
  if (value instanceof Int) return value.value;
  if (value instanceof Float || value instanceof NonFinite) return value.value;
  return undefined;
}

/** Python `==` on the value model: numbers compare across `bool`/`int`/`float` exactly, containers structurally, dicts regardless of order. */
export function eq(a: PyValue, b: PyValue): boolean {
  const x = numeric(a), y = numeric(b);
  if (x !== undefined || y !== undefined) {
    if (x === undefined || y === undefined) return false;
    if (typeof x === 'bigint' && typeof y === 'bigint') return x === y;
    if (typeof x === 'number' && typeof y === 'number') return x === y;
    const [whole, real] = typeof x === 'bigint' ? [x, y as number] : [y as bigint, x];
    // An int equals a float only when the float is integral and exactly that integer.
    return Number.isInteger(real) && BigInt(real) === whole;
  }
  if (a === null || b === null) return a === b;
  if (typeof a === 'string' || typeof b === 'string') return a === b;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((item, index) => eq(item, b[index]!));
  }
  if (!isDict(a) || !isDict(b) || a.size !== b.size) return false;
  for (const [key, item] of a) { if (!b.has(key) || !eq(item, b.get(key)!)) return false; }
  return true;
}

/** `dict.get(key)` with Python's `None` default. */
export const get = (mapping: PyDict, key: string): PyValue => { const value = mapping.get(key); return value === undefined ? null : value; };

/** `mapping[key]` on a dict: `KeyError` when the key is absent. */
export function item(mapping: PyDict, key: string): PyValue {
  const value = mapping.get(key);
  if (value === undefined) throw new PyKeyError(key);
  return value;
}

/** `value[key]` for a string key on any value: `KeyError` on a dict without it, `TypeError` on everything that is not a dict. */
export function subscript(value: PyValue, key: string): PyValue {
  if (isDict(value)) return item(value, key);
  throw new PyTypeError(Array.isArray(value) || typeof value === 'string' ? typeName(value) + ' indices must be integers' : "'" + typeName(value) + "' object is not subscriptable");
}

/** `bool(value)`. */
export function truthy(value: PyValue): boolean {
  if (value === null) return false;
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') return value.length > 0;
  if (value instanceof Int) return value.value !== 0n;
  if (value instanceof Float) return value.value !== 0;
  if (value instanceof NonFinite) return true;
  return Array.isArray(value) ? value.length > 0 : value.size > 0;
}

/** `set(mapping) == {names}`. */
export function keysEqual(mapping: PyDict, names: ReadonlySet<string>): boolean {
  if (mapping.size !== names.size) return false;
  for (const key of mapping.keys()) if (!names.has(key)) return false;
  return true;
}
/** `set(mapping) <= {names}`. */
export function keysWithin(mapping: PyDict, names: ReadonlySet<string>): boolean {
  for (const key of mapping.keys()) if (!names.has(key)) return false;
  return true;
}
/** `{names} <= set(mapping)`. */
export function keysInclude(mapping: PyDict, names: ReadonlySet<string>): boolean {
  for (const name of names) if (!mapping.has(name)) return false;
  return true;
}

/**
 * `value in {literal strings}`. Hashing comes first in Python: a list or a
 * dict raises `TypeError` here instead of answering False.
 */
export function inStrSet(value: PyValue, names: ReadonlySet<string>): boolean {
  if (Array.isArray(value) || isDict(value)) throw new PyTypeError("unhashable type: '" + typeName(value) + "'");
  return typeof value === 'string' && names.has(value);
}

/**
 * `needle in container` for a literal string needle: key test on a dict,
 * element test on a list, substring test on a str, `TypeError` on anything
 * else ("argument of type 'int' is not iterable").
 */
export function strIn(needle: string, container: PyValue): boolean {
  if (isDict(container)) return container.has(needle);
  if (Array.isArray(container)) return container.some(element => element === needle);
  if (typeof container === 'string') return container.includes(needle);
  throw new PyTypeError("argument of type '" + typeName(container) + "' is not iterable");
}

/** A C0 or C1 control character, or DEL: `ord(c) < 32 or 127 <= ord(c) <= 159`. */
export function hasControl(value: string): boolean {
  for (let index = 0; index < value.length; index++) {
    const unit = value.charCodeAt(index);
    if (unit < 32 || (unit >= 127 && unit <= 159)) return true;
  }
  return false;
}

/*
 * Code points for which `str.isprintable()` is False in the oracle interpreter
 * (CPython 3.12, Unicode 15.0.0): categories Cc, Cf, Cs, Co, Cn, Zl, Zp and
 * Zs except the space. A table and not a `\\p{...}` class, because the
 * JavaScript runtime's Unicode version is newer and differs between Node
 * releases. Ranges are `gap-from-previous-end,length-1` pairs in hex, made with
 *   flat=[];prev=0;start=None
 *   for n in range(0x110001):
 *     bad=n<0x110000 and not chr(n).isprintable()
 *     if bad and start is None:start=n
 *     if not bad and start is not None:flat+=[start-prev,n-1-start];prev=n-1;start=None
 *   print(','.join(format(x,'x') for x in flat))
 */
const UNPRINTABLE: readonly (readonly [number, number])[] = (() => {
  const numbers = '0,1f,60,21,d,0,2cb,1,7,3,8,0,2,0,15,0,18e,0,27,1,33,1,4,0,38,7,1c,3,7,10,17,0,c1,0,31,1,3c,1,66,d,3c,1,32,1,10,0,1d,1,2,0,c,4,20,8,4b,0,a2,0,9,1,3,1,17,0,8,0,2,2,5,1,a,1,3,1,5,7,2,3,3,0,6,1,1a,1,4,0,7,3,3,1,17,0,8,0,3,0,3,0,3,1,2,0,6,3,3,1,4,2,2,6,5,0,2,6,12,9,4,0,a,0,4,0,17,0,8,0,3,0,6,1,b,0,4,0,4,1,2,e,5,1,d,6,8,0,4,0,9,1,3,1,17,0,8,0,3,0,6,1,a,1,3,1,4,6,4,3,3,0,6,1,13,9,3,0,7,2,4,0,5,2,3,0,2,0,3,2,3,2,4,2,d,3,6,2,4,0,5,1,2,5,2,d,16,4,e,0,4,0,18,0,11,1,a,0,4,0,5,6,3,0,4,1,2,1,5,1,b,6,17,0,4,0,18,0,b,0,6,1,a,0,4,0,5,6,3,5,3,0,5,1,b,0,4,b,e,0,4,0,34,0,4,0,7,3,11,1,1b,0,4,0,13,2,19,0,a,0,2,1,8,2,2,3,7,0,2,0,9,5,b,1,4,b,3b,3,1e,24,3,0,2,0,6,0,19,0,2,0,18,1,6,0,2,0,8,0,b,1,5,1f,49,0,25,3,28,0,25,0,10,0,e,24,c7,0,2,4,2,1,17a,0,5,1,8,0,2,0,5,1,2a,0,5,1,22,0,5,1,8,0,2,0,5,1,10,0,3a,0,5,1,44,1,21,2,1b,5,57,1,7,1,281,0,1d,2,5a,6,17,8,19,8,15,b,e,0,4,0,3,b,5f,1,b,5,b,5,f,0,c,5,5a,6,2c,4,47,9,20,0,d,3,d,3,2,2,2b,1,6,a,2d,3,1b,5,c,2,3f,1,42,0,1e,1,c,5,b,5,f,1,20,30,4e,2,30,0,75,7,3d,2,10,2,3d,6,2c,1,c,7,2c,4,217,1,7,1,27,1,7,1,9,0,2,0,2,0,2,0,20,1,36,0,10,0,f,1,7,0,14,1,4,0,a,10,19,7,30,10,3,1,1c,0,e,2,22,e,22,e,8d,3,298,18,c,14,715,1,21,0,15e,4,2e,0,2,4,2,1,39,6,3,d,19,8,8,0,8,0,8,0,8,0,8,0,8,0,8,0,8,0,7f,21,1b,0,5a,b,d7,19,d,4,40,0,57,1,68,4,2c,0,5f,0,55,b,30,0,726e,2,38,8,15d,13,b9,7,cc,4,3,0,2,0,6,17,3c,2,b,5,39,7,47,7,d,5,75,a,1f,2,4f,0,c,3,22,0,38,8,f,1,b,1,68,17,1d,9,7,1,7,1,7,8,8,0,8,0,3d,3,7f,1,b,5,2ba5,b,18,3,32,2103,16f,1,6b,25,8,b,6,4,1b,0,6,0,2,0,3,0,3,0,7e,f,1be,1,37,6,2,1f,2b,5,34,0,14,0,5,3,6,0,88,3,bf,2,7,1,7,1,7,1,4,2,8,0,8,c,3,1,d,0,1b,0,14,0,3,0,10,1,f,21,7c,4,4,3,2e,2,59,0,e,2,2,2e,2f,81,1e,2,32,e,1d,3,25,8,1f,4,2c,4,1f,0,26,3,f,29,9f,1,b,5,25,3,25,3,29,7,35,a,d,0,10,0,8,0,3,0,c,0,10,0,8,0,3,42,138,8,17,9,9,17,7,0,2b,0,a,44,7,1,2,0,2d,0,3,2,2,1,18,0,49,7,a,2f,14,0,3,4,22,2,1c,4,2,3f,39,3,15,1,33,0,3,4,9,0,4,0,1e,1,4,3,b,6,a,6,41,1f,28,3,d,8,37,2,1e,1,1c,4,1b,6,5,b,8,4f,4a,36,34,c,34,6,2f,7,b,125,20,0,2b,0,4,1,3,4a,2c,7,2b,15,1b,25,1d,13,18,8,4f,3,25,8,3f,0,6,c,1a,6,b,5,36,0,13,7,28,8,61,0,15,a,13,0,30,3d,8,0,2,0,5,0,10,0,c,5,3c,4,b,5,5,0,9,1,3,1,17,0,8,0,3,0,6,0,b,1,3,1,4,1,2,5,2,4,8,1,8,2,6,8a,5d,0,6,1d,49,7,b,a5,37,1,27,21,46,a,b,5,e,12,3b,5,b,35,1c,1,10,3,18,b8,3d,63,54,b,9,1,2,1,9,0,3,0,1f,0,3,1,d,8,b,45,9,1,2f,1,c,1a,49,7,54,c,4a,6,b,f5,a,0,2e,0,f,9,1e,2,21,1,17,0,f,48,8,0,3,0,2d,2,2,0,3,0,a,7,b,5,7,0,3,0,26,0,3,0,7,6,b,135,1a,6,12,0,2a,2,1d,55,2,e,33,c,39c,65,70,0,6,a,c5,a4b,64,c,431,f,17,fa9,248,21b8,23a,6,20,0,b,3,52,0,b,5,1f,1,7,9,47,9,b,0,8,0,16,4,14,2af,5c,64,4c,3,3a,6,12,3f,6,a,3,d,17f9,7,4d7,29,a,22e6,5,0,8,0,3,0,124,e,2,1c,4,1,2,d,5,7,18d,903,6c,4,e,2,a,6,b,1,5,125f,2f,1,18,8,75,3b,f7,9,28,1,4b,7,71,14,47,79,15,b,15,b,58,8,1a,86,56,0,48,0,3,1,2,1,3,1,5,0,d,0,2,0,8,0,42,0,5,1,9,0,8,0,1d,0,5,0,6,0,2,2,8,0,155,1,125,1,2bf,e,6,0,10,44f,20,5,7,d4,8,0,12,1,8,0,3,0,6,4,3f,20,2,6f,2e,2,f,1,b,3,3,13f,20,10,3b,4,2,1cf,2b,2e5,8,0,5,0,3,0,10,0,c6,1,11,28,4d,3,b,3,3,310,45,4b,3e,c1,5,0,1c,0,3,0,2,1,2,0,b,0,5,0,2,0,2,5,2,3,2,0,2,0,2,0,4,0,3,0,2,1,2,0,2,0,2,0,2,0,2,0,3,0,2,1,5,0,8,0,5,0,5,0,2,0,b,0,12,4,4,0,6,0,12,33,3,10d,2d,3,65,b,10,1,10,0,10,0,26,9,af,37,1e,c,2d,3,a,6,3,d,7,99,3d9,3,12,2,e,2,78,3,60,5,d,3,2,e,d,3,39,7,b,5,29,7,1f,1,3,4d,155,b,f,1,e,2,a,6,2f,0,8,7,f,3,a,6,a,6,94,0,38,24,b,405,a6e1,1f,103b,5,df,1,1683,d,1d32,c1e,21f,5e1,134c,4,1061,add4f,f1,2fe0f'.split(',').map(field => parseInt(field, 16));
  const ranges: [number, number][] = [];
  for (let index = 0, previous = 0; index < numbers.length; index += 2) {
    const first = previous + numbers[index]!, last = first + numbers[index + 1]!;
    ranges.push([first, last]); previous = last;
  }
  return ranges;
})();
/** `str.isprintable()` of a single character (one code point; a lone surrogate counts as one). */
export function isprintable(char: string): boolean {
  const code = char.codePointAt(0)!;
  let low = 0, high = UNPRINTABLE.length;
  while (low < high) {
    const middle = (low + high) >>> 1, range = UNPRINTABLE[middle]!;
    if (code < range[0]) high = middle; else if (code > range[1]) low = middle + 1; else return false;
  }
  return true;
}

/** `io.BytesIO` as far as the engine uses it: `seek`, `read(size)`, `tell`. */
export class BytesIO {
  #data: Uint8Array; #position = 0;
  constructor(data: Uint8Array = new Uint8Array(0)) { this.#data = data; }
  seek(position: number): number { this.#position = position; return position; }
  tell(): number { return this.#position; }
  read(size: number): Uint8Array {
    const start = Math.min(this.#position, this.#data.length), end = Math.min(start + size, this.#data.length);
    this.#position = Math.max(this.#position, end);
    return this.#data.subarray(start, end);
  }
}
