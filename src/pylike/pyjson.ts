/**
 * `json.loads` as CPython 3.12 runs it, for documents the engine reads and
 * then judges: cook's lock, an operator recipe, the adapter's diagnostic line.
 *
 * It differs from `json.ts` `loads` (the envelope parser) where the oracle does:
 *   - `object_pairs_hook` and `parse_constant` are callbacks, called in the
 *     order CPython calls them: a constant where it stands, the pairs hook when
 *     its object closes. Which refusal a document gets depends on that order.
 *   - without a pairs hook a repeated key keeps its first position and its
 *     last value; without a constant hook `NaN` and the infinities are floats.
 *   - a float literal that overflows (`1E400`) is a float, not an error.
 *   - an integer literal of more than 4300 digits is a `ValueError`
 *     (`sys.int_info.default_max_str_digits`).
 *   - nesting is bounded where CPython's C recursion guard raises
 *     `RecursionError`, see MAX_DEPTH below.
 * Objects are `Map`s, so key order is the document's.
 */
import { Float, Int, NonFinite, PyError, PyRecursionError, PyValueError } from './py.js';
import type { PyDict, PyValue } from './py.js';
import { decodeJsonBytes } from './codec.js';

export class JSONDecodeError extends PyValueError { override get pyName(): string { return 'JSONDecodeError'; } }

export type Pair = [key: string, value: PyValue];
export interface LoadOptions {
  /** Receives the pairs of each object when it closes; its result replaces the object. */
  object_pairs_hook?: (pairs: Pair[]) => PyValue;
  /** Receives `NaN`, `Infinity` or `-Infinity`; its result replaces the constant. */
  parse_constant?: (name: string) => PyValue;
}

/**
 * CPython 3.12.3 guards the scanner's recursion with its C recursion counter
 * (`Py_C_RECURSION_LIMIT`, 10000). Everything the scanner does at depth costs
 * some of that budget, so each of the following has its own bound. "Depth" is
 * the number of containers open at that point (an object counts while its
 * pairs hook runs). All numbers were measured on the oracle interpreter with
 * `json.loads` called from a script's top level:
 *
 *   9997  a container may be opened (`[`, `{`)
 *   9996  a C-level failure that needs no Python frame still gets its own
 *         class: "Expecting value", the integer digit limit; and the default
 *         handling of `NaN`/`Infinity` (a builtin call) still succeeds
 *   9995  a Python-level hook can be called and can return
 *   9994  a Python-level hook can raise a new exception instance, as both
 *         engine hooks do (`raise LockError(...)`, `need(...)`)
 *   9993  the scanner can raise `JSONDecodeError` (a Python class with its own
 *         `__init__`) for a malformed string, delimiter or key
 *
 * Beyond its bound each of them is `RecursionError` instead. The counter is
 * per C call chain, so a caller that itself sits inside C frames (for example
 * under `exec`) lowers all five numbers by the same few levels; that band is
 * oracle-dependent and is reported, not papered over. Only the hook bounds are
 * visible through the engine: `read_lock` keeps a hook's own message but turns
 * `RecursionError` into its generic refusal. Every consumer treats
 * `JSONDecodeError` and `RecursionError` alike.
 */
export const MAX_DEPTH = 9997;
export const MAX_C_ERROR_DEPTH = 9996;
export const MAX_HOOK_DEPTH = 9995;
export const MAX_HOOK_RAISE_DEPTH = 9994;
export const MAX_DECODE_ERROR_DEPTH = 9993;
/** `sys.int_info.default_max_str_digits`. */
export const MAX_INT_DIGITS = 4300;

const NUMBER = /-?(?:0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?/y;
const ESCAPED = '"\\/bfnrt';

type Frame = { array: PyValue[] } | { pairs: Pair[]; key: string };

export function loads(input: string | Uint8Array, options: LoadOptions = {}): PyValue {
  let source: string;
  if (typeof input === 'string') {
    if (input.startsWith('\ufeff')) throw new JSONDecodeError('Unexpected UTF-8 BOM (decode using utf-8-sig)');
    source = input;
  } else source = decodeJsonBytes(input);
  const { object_pairs_hook: pairsHook, parse_constant: constantHook } = options;
  let at = 0;
  const stack: Frame[] = [];
  const deep = (): never => { throw new PyRecursionError('maximum recursion depth exceeded'); };
  const bad = (message: string, limit = MAX_DECODE_ERROR_DEPTH): never => { if (stack.length > limit) deep(); throw new JSONDecodeError(message); };
  const space = (): void => { for (;;) { const c = source.charCodeAt(at); if (c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d) at++; else return; } };
  /** Calls a hook at `depth`; a Python exception it raises needs one more level than the call itself. */
  function hooked<Result>(depth: number, call: () => Result): Result {
    if (depth > MAX_HOOK_DEPTH) deep();
    try { return call(); } catch (error) { if (depth > MAX_HOOK_RAISE_DEPTH && error instanceof PyError) deep(); throw error; }
  }

  function string(): string {
    const start = at++;
    for (;;) {
      if (at >= source.length) bad('Unterminated string starting at');
      const unit = source.charCodeAt(at++);
      if (unit === 0x22) return JSON.parse(source.slice(start, at)) as string;
      if (unit < 0x20) bad('Invalid control character at');
      if (unit !== 0x5c) continue;
      const escape = source[at++];
      if (escape === 'u') {
        for (let n = 0; n < 4; n++, at++) {
          const h = source.charCodeAt(at);
          if (!((h >= 0x30 && h <= 0x39) || (h >= 0x41 && h <= 0x46) || (h >= 0x61 && h <= 0x66))) bad('Invalid \\uXXXX escape');
        }
      } else if (escape === undefined || !ESCAPED.includes(escape)) bad('Invalid \\escape');
    }
  }
  function close(pairs: Pair[], depth: number): PyValue {
    if (pairsHook) return hooked(depth, () => pairsHook(pairs));
    const result: PyDict = new Map();
    for (const [key, value] of pairs) result.set(key, value);
    return result;
  }
  function constant(name: string, value: number): PyValue {
    at += name.length;
    if (constantHook) return hooked(stack.length, () => constantHook(name));
    if (stack.length > MAX_C_ERROR_DEPTH) deep();
    return new NonFinite(value);
  }
  function scalar(): PyValue {
    const head = source[at];
    if (head === '"') return string();
    if (head === 'n' && source.startsWith('null', at)) { at += 4; return null; }
    if (head === 't' && source.startsWith('true', at)) { at += 4; return true; }
    if (head === 'f' && source.startsWith('false', at)) { at += 5; return false; }
    if (head === 'N' && source.startsWith('NaN', at)) return constant('NaN', NaN);
    if (head === 'I' && source.startsWith('Infinity', at)) return constant('Infinity', Infinity);
    if (head === '-' && source.startsWith('-Infinity', at)) return constant('-Infinity', -Infinity);
    NUMBER.lastIndex = at;
    const match = NUMBER.exec(source);
    if (!match) return bad('Expecting value', MAX_C_ERROR_DEPTH);
    at += match[0].length;
    if (match[1] !== undefined || match[2] !== undefined) {
      const value = Number(match[0]);
      return Number.isFinite(value) ? new Float(value) : new NonFinite(value);
    }
    if (match[0].length - (match[0].startsWith('-') ? 1 : 0) > MAX_INT_DIGITS) {
      if (stack.length > MAX_C_ERROR_DEPTH) deep();
      throw new PyValueError('Exceeds the limit (4300 digits) for integer string conversion');
    }
    return new Int(match[0]);
  }
  /** Reads `"key" :` and leaves `at` on the value. */
  function key(): string {
    if (source[at] !== '"') bad('Expecting property name enclosed in double quotes');
    const name = string(); space();
    if (source[at] !== ':') bad("Expecting ':' delimiter");
    at++; space(); return name;
  }

  let value: PyValue;
  space();
  for (;;) {
    const head = source[at];
    if (head === '[' || head === '{') {
      if (stack.length + 1 > MAX_DEPTH) throw new PyRecursionError('maximum recursion depth exceeded while decoding a JSON document');
      at++; space();
      if (head === '[') {
        if (source[at] !== ']') { stack.push({ array: [] }); continue; }
        at++; value = [];
      } else {
        // The frame goes on the stack before its first key is read: a bad key is an error inside the object.
        if (source[at] !== '}') { const frame: Frame = { pairs: [], key: '' }; stack.push(frame); frame.key = key(); continue; }
        at++; value = close([], stack.length + 1);
      }
    } else value = scalar();
    // A value is complete: hand it to the enclosing container, closing as many as end here.
    for (;;) {
      const top = stack.at(-1);
      if (!top) { space(); if (at !== source.length) bad('Extra data'); return value; }
      space();
      const next = source[at];
      if ('array' in top) {
        top.array.push(value);
        if (next === ']') { at++; stack.pop(); value = top.array; continue; }
        if (next !== ',') bad("Expecting ',' delimiter");
        at++; space(); break;
      }
      top.pairs.push([top.key, value]);
      if (next === '}') { at++; value = close(top.pairs, stack.length); stack.pop(); continue; }
      if (next !== ',') bad("Expecting ',' delimiter");
      at++; space(); top.key = key(); break;
    }
  }
}
