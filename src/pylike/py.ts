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
