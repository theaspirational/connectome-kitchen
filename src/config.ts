/**
 * Layer semantics shared by the controller filter and offline tests.
 * Port of `oracle/module_utils/connectome_config.py` (a49b5dc), one to one.
 *
 * The oracle merges with Ansible's `combine` when ansible-core is importable
 * and with its own stand-in otherwise (config.py:5-20). This port is the
 * stand-in: it is what the frozen oracle runs on a host without Ansible, which
 * includes the machine every vector was produced on. Whether real `combine`
 * agrees with the stand-in is plan risk R6 and is not settled here.
 *
 * Comments of the form `row N <mutant id>: ... config.py:L` mark where a row
 * of the fail-closed inventory (plan section 4.1) lives.
 */
import { compare } from './pylike/json.js';
import {
  Float, Int, NonFinite, PyAttributeError, PyTypeError, PyValueError,
  deepcopy, dictGet, eq, isDict, isList, iterate, len, strIn, subscript, truthy, typeName,
} from './pylike/py.js';
import type { PyDict, PyValue } from './pylike/py.js';

/** `value.values()` or `value.items()`: `AttributeError` on anything that is not a dict. */
function mapping(value: PyValue, method: string): PyDict {
  if (!isDict(value)) throw new PyAttributeError("'" + typeName(value) + "' object has no attribute '" + method + "'");
  return value;
}

/**
 * The module's `combine` name: here the oracle's stand-in, `_python_combine(*layers, recursive=False)`.
 * Lists are replaced, and with `recursive` mappings are merged. config.py:5-20
 */
export function combine(layers: readonly PyValue[], recursive = false): PyDict {
  const result: PyDict = new Map();
  for (const layer of layers) {
    for (const [key, value] of mapping(layer, 'items')) {
      const present = result.get(key);
      if (recursive && isDict(value) && isDict(present)) result.set(key, combine([present, value], true));
      else result.set(key, deepcopy(value));
    }
  }
  return result;
}

/** config.py:23-28 */
function prune(value: PyValue): PyValue {
  if (isDict(value)) return new Map([...value].filter(([, entry]) => entry !== null).map(([key, entry]) => [key, prune(entry)]));
  if (isList(value)) return value.map(prune);
  return value;
}

/** config.py:31-54 */
export function merge_layers(layers: PyValue): PyDict {
  if (len(layers) !== 4 || iterate(layers).some(layer => !isDict(layer))) {
    // row 69 config.layers.count: exactly four mapping layers. config.py:32-33
    throw new PyValueError('exactly four mapping layers required');
  }
  // The ONE production merge site. Keep tombstones until all layers are merged.
  let merged: PyDict = new Map();
  // A left fold is essential: Ansible's multi-argument combine reverses the
  // fold, which can resurrect lower keys across an intermediate null.
  // row 69 config.layers.fold: left fold, lowest layer first. config.py:38-39
  for (const layer of layers as PyDict[]) merged = combine([merged, layer], true);
  const result = prune(merged) as PyDict;
  for (const section of ['connectors', 'modules', 'catalog']) {
    if (section === 'catalog' && !result.has(section)) continue;
    const entries = dictGet(result, section, new Map());
    if (!isDict(entries)) {
      // row 69 config.layers.enabled-type: sections are mappings. config.py:45-46
      throw new PyValueError('connectors and modules must be mappings');
    }
    const records = entries as PyDict;
    for (const entry of records.values()) {
      if (!isDict(entry) || typeof dictGet(entry, 'enabled', true) !== 'boolean') {
        // row 69 config.layers.enabled-type: entries are mappings with a boolean `enabled`. config.py:48-49
        throw new PyValueError('entries must be mappings with boolean enabled');
      }
    }
    // row 69 config.layers.prune: disabled entries are removed before the engine sees them. config.py:50-51
    result.set(section, new Map([...records].filter(([, entry]) => dictGet(entry, 'enabled', true) === true)));
  }
  if (!truthy(dictGet(result, 'catalog', null))) result.delete('catalog');
  return result;
}

/** config.py:57-65 */
export function public_catalog(defaults: PyValue, catalog: PyValue): PyValue {
  const result = deepcopy(defaults);
  if (strIn('connectors', result)) {
    // row 69 config.layers.public-connectors: public defaults carry no connectors. config.py:59-60
    throw new PyValueError('public connectors belong in the catalog');
  }
  const kind = (entry: PyValue): PyValue => dictGet(entry, 'kind', 'connector');
  const connectors: PyDict = new Map([...mapping(catalog, 'items')].filter(([, entry]) => eq(kind(entry), 'connector')).map(([name, entry]) => [name, deepcopy(entry)]));
  assign(result, 'connectors', connectors);
  const artifacts: PyDict = new Map([...mapping(catalog, 'items')].filter(([, entry]) => !eq(kind(entry), 'connector')).map(([name, entry]) => [name, deepcopy(entry)]));
  if (artifacts.size) assign(result, 'catalog', artifacts);
  return result;
}
/** `target[key] = value`: only a dict takes a string key. */
function assign(target: PyValue, key: string, value: PyValue): void {
  if (!isDict(target)) throw new PyTypeError("'" + typeName(target) + "' object does not support item assignment");
  target.set(key, value);
}

const numeric = (value: PyValue): bigint | number | undefined =>
  typeof value === 'boolean' ? (value ? 1n : 0n) : value instanceof Int ? value.value : value instanceof Float || value instanceof NonFinite ? value.value : undefined;
/** Adds to a Python `set`: the value must be hashable; equal values (`1`, `True`, `1.0`) are one member. */
function add(unique: PyValue[], value: PyValue): void {
  if (isList(value) || isDict(value)) throw new PyTypeError("unhashable type: '" + typeName(value) + "'");
  if (!unique.some(seen => eq(seen, value))) unique.push(value);
}
/**
 * `sorted(a_set)`: ascending; `TypeError` where Python refuses to compare two members (a string with a number).
 *
 * Divergence D2: a NaN among two or more numbers. Python does not refuse it. Every comparison with NaN is false, so
 * `sorted` returns the members in an order that follows the set's iteration order, which for a NaN follows the address
 * of the float object: `[1.5, nan]` in one call, `[nan, 1, 2]` in another. The port does not model an order that is
 * not one and raises `TypeError`. Nothing is opened by that: no engine module calls `required_names` at this freeze
 * (the oracle's tests do), and were its result handed to `read_lock`, a NaN is never equal to a declared name, so the
 * oracle refuses there (`lock omits a declared required secret`). test/vectors/port-divergences.golden, `required-names.*`.
 */
function sorted(unique: PyValue[]): PyValue[] {
  if (unique.length < 2) return unique;
  if (unique.every(value => typeof value === 'string')) return (unique as string[]).sort(compare);
  if (unique.every(value => numeric(value) !== undefined && !(value instanceof NonFinite && Number.isNaN(value.value)))) {
    return unique.sort((a, b) => { const x = numeric(a)!, y = numeric(b)!; return x < y ? -1 : x > y ? 1 : 0; });
  }
  throw new PyTypeError("'<' not supported between instances of these types");
}

/** config.py:68-72 */
export function required_names(config: PyValue): PyValue[] {
  const entries = [...mapping(dictGet(config, 'connectors', new Map()), 'values').values(), ...mapping(dictGet(config, 'modules', new Map()), 'values').values()];
  const names: PyValue[] = [];
  for (const name of iterate(dictGet(config, 'required_secrets', []))) add(names, name);
  for (const entry of iterate(dictGet(config, 'secret_files', []))) add(names, subscript(entry, 'secret'));
  for (const entry of entries) for (const name of iterate(dictGet(entry, 'required_secrets', []))) add(names, name);
  return sorted(names);
}
