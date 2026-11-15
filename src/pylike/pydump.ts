/**
 * `json.dumps` of CPython 3.12 on the value model of `py.ts` (dicts are `Map`s).
 *
 * The engine writes four shapes and all of them are bytes somebody compares:
 *   - `canonical`: `sort_keys=True, separators=(',', ':')`: the request id, `deployment.json`, `ownership.json`;
 *   - `indented`: `sort_keys=True, indent=2`: the layout, the provenance record, the recipe, the rewritten lock;
 *   - `plain`: the defaults: the adapter's diagnostic line.
 * `ensure_ascii` is always on, as it is in the oracle.
 */
import { dumps as scalar } from './json.js';
import { compare } from './json.js';
import { Float, Int, NonFinite, PyTypeError, isDict } from './py.js';
import type { PyValue } from './py.js';

export interface DumpOptions { sort_keys?: boolean; indent?: number | null; separators?: readonly [string, string]; }

function quote(text: string): string {
  let out = '"';
  for (let index = 0; index < text.length; index++) {
    const unit = text.charCodeAt(index), char = text[index]!;
    if (char === '"' || char === '\\') out += '\\' + char;
    else if (unit === 8) out += '\\b'; else if (unit === 9) out += '\\t'; else if (unit === 10) out += '\\n';
    else if (unit === 12) out += '\\f'; else if (unit === 13) out += '\\r';
    else if (unit < 32 || unit >= 127) out += '\\u' + unit.toString(16).padStart(4, '0');
    else out += char;
  }
  return out + '"';
}

export function dumps(value: PyValue, { sort_keys = false, indent = null, separators }: DumpOptions = {}): string {
  const [comma, colon] = separators ?? (indent === null ? [', ', ': '] : [',', ': ']);
  const seen = new Set<object>();
  function emit(item: PyValue, level: number): string {
    if (item === null) return 'null';
    if (typeof item === 'boolean') return item ? 'true' : 'false';
    if (typeof item === 'string') return quote(item);
    if (item instanceof Int) return item.value.toString();
    if (item instanceof Float) return scalar(item);
    if (item instanceof NonFinite) return Number.isNaN(item.value) ? 'NaN' : item.value > 0 ? 'Infinity' : '-Infinity';
    if (!Array.isArray(item) && !isDict(item)) throw new PyTypeError('Object is not JSON serializable');
    if (seen.has(item)) throw new PyTypeError('Circular reference detected');
    const open = indent === null ? '' : '\n' + ' '.repeat(indent * (level + 1));
    const close = indent === null ? '' : '\n' + ' '.repeat(indent * level);
    const between = indent === null ? comma : comma + open;
    seen.add(item);
    let out: string;
    if (Array.isArray(item)) {
      out = item.length ? '[' + open + item.map(entry => emit(entry, level + 1)).join(between) + close + ']' : '[]';
    } else {
      const keys = [...item.keys()];
      if (sort_keys) keys.sort(compare);
      out = keys.length ? '{' + open + keys.map(key => quote(key) + colon + emit(item.get(key)!, level + 1)).join(between) + close + '}' : '{}';
    }
    seen.delete(item);
    return out;
  }
  return emit(value, 0);
}

/** `json.dumps(value, sort_keys=True, separators=(',', ':'))`. */
export const canonical = (value: PyValue): string => dumps(value, { sort_keys: true, separators: [',', ':'] });
/** `json.dumps(value, sort_keys=True, indent=2)`. */
export const indented = (value: PyValue): string => dumps(value, { sort_keys: true, indent: 2 });

/** A dict literal: `dict({a: 1})` keeps the order the keys are written in. */
export const dict = (entries: Record<string, PyValue>): Map<string, PyValue> => new Map(Object.entries(entries));
