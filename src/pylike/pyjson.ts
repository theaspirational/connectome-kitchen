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
import { Float, Int, NonFinite, PyRecursionError, PyValueError } from './py.js';
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
 * (`Py_C_RECURSION_LIMIT`, 10000). Measured on the oracle interpreter, called
 * from module level: a container may be opened at nesting depth 9997, and a
 * Python-level hook can still be called from depth 9995. One level deeper is
 * `RecursionError` in both cases. The counter is per C call chain, so a caller
 * that itself sits inside C frames lowers both numbers by a few levels; that
 * band is oracle-dependent and is reported, not papered over.
 */
export const MAX_DEPTH = 9997;
export const MAX_HOOK_DEPTH = 9995;
/** `sys.int_info.default_max_str_digits`. */
export const MAX_INT_DIGITS = 4300;

const NUMBER = /-?(?:0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?/y;
const ESCAPED = '"\\/bfnrt';

type Frame = { array: PyValue[] } | { pairs: Pair[]; key: string };

export function loads(input: string | Uint8Array, options: LoadOptions = {}): PyValue {
  let source: string;
  if (typeof input === 'string') {
    if (input.startsWith('﻿')) throw new JSONDecodeError('Unexpected UTF-8 BOM (decode using utf-8-sig)');
    source = input;
  } else source = decodeJsonBytes(input);
  const { object_pairs_hook: pairsHook, parse_constant: constantHook } = options;
  let at = 0;
  const bad = (message: string): never => { throw new JSONDecodeError(message); };
  const space = (): void => { for (;;) { const c = source.charCodeAt(at); if (c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d) at++; else return; } };
  const hook = (depth: number): void => { if (depth > MAX_HOOK_DEPTH) throw new PyRecursionError('maximum recursion depth exceeded'); };

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
    if (pairsHook) { hook(depth); return pairsHook(pairs); }
    const result: PyDict = new Map();
    for (const [key, value] of pairs) result.set(key, value);
    return result;
  }
  function constant(name: string, value: number, depth: number): PyValue {
    at += name.length;
    if (constantHook) { hook(depth); return constantHook(name); }
    return new NonFinite(value);
  }
  function scalar(depth: number): PyValue {
    const head = source[at];
    if (head === '"') return string();
    if (head === 'n' && source.startsWith('null', at)) { at += 4; return null; }
    if (head === 't' && source.startsWith('true', at)) { at += 4; return true; }
    if (head === 'f' && source.startsWith('false', at)) { at += 5; return false; }
    if (head === 'N' && source.startsWith('NaN', at)) return constant('NaN', NaN, depth);
    if (head === 'I' && source.startsWith('Infinity', at)) return constant('Infinity', Infinity, depth);
    if (head === '-' && source.startsWith('-Infinity', at)) return constant('-Infinity', -Infinity, depth);
    NUMBER.lastIndex = at;
    const match = NUMBER.exec(source);
    if (!match) return bad('Expecting value');
    at += match[0].length;
    if (match[1] !== undefined || match[2] !== undefined) {
      const value = Number(match[0]);
      return Number.isFinite(value) ? new Float(value) : new NonFinite(value);
    }
    if (match[0].length - (match[0].startsWith('-') ? 1 : 0) > MAX_INT_DIGITS) throw new PyValueError('Exceeds the limit (4300 digits) for integer string conversion');
    return new Int(match[0]);
  }
  /** Reads `"key" :` and leaves `at` on the value. */
  function key(): string {
    if (source[at] !== '"') bad('Expecting property name enclosed in double quotes');
    const name = string(); space();
    if (source[at] !== ':') bad("Expecting ':' delimiter");
    at++; space(); return name;
  }

  const stack: Frame[] = [];
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
        if (source[at] !== '}') { stack.push({ pairs: [], key: key() }); continue; }
        at++; value = close([], stack.length + 1);
      }
    } else value = scalar(stack.length);
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
