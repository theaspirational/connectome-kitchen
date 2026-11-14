// The committed vectors of errors, lock and inputs, replayed on the TS port
// without Python: every outcome in these files was written by the oracle
// (ts_port.py build, guard_vectors.py, crash_only.py), never by the port.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import * as json from '../../dist/pylike/json.js';
import { loads } from '../../dist/pylike/pyjson.js';
import { materialize, remove, run } from './ts_invoke.mjs';

const MODULES = ['connectome_errors', 'connectome_lock', 'connectome_inputs'];
const SCRATCH = path.resolve('.cache/ts-port-unit');
const LOCK_FILES = { 'run.sh': { text: '#!/bin/sh\nexit 0\n' }, 'example.service': { text: '[Service]\nExecStart=/bin/true\n' }, 'cook-recipe.json': { text: '{}' } };

const plain = value => Array.isArray(value) ? value.map(plain) : value instanceof Map ? Object.fromEntries([...value].map(([key, item]) => [key, plain(item)])) : value;
const number = value => Number(typeof value === 'number' ? value : value.value);
const bytes = text => new Map([['$bytes', Buffer.from(text, 'latin1').toString('base64')]]);

/** `$generate`, `$join` and `$sha256-of`, as `expand` and `sha256_arg` in ts_port.py. */
function expand(value, root) {
  if (Array.isArray(value)) return value.map(item => expand(item, root));
  if (!(value instanceof Map)) return value;
  if (value.has('$join')) return value.get('$join').join('');
  if (value.has('$sha256-of')) return createHash('sha256').update(Buffer.from(expand(value.get('$sha256-of'), root).get('$bytes'), 'base64')).digest('hex');
  if (value.has('$generate')) {
    const kind = value.get('$generate'), get = key => value.get(key) ?? '';
    if (kind === 'nested-lock') return { text: (get('prefix') + '['.repeat(number(get('depth'))) + get('leaf') + ']'.repeat(number(get('depth'))) + get('suffix')).replaceAll('${ROOT}', root) };
    if (kind === 'nested') return bytes(get('open').repeat(number(get('depth'))) + get('leaf') + get('close').repeat(number(get('depth'))));
    if (kind === 'repeat') return bytes(get('prefix') + get('unit').repeat(number(get('count'))) + get('suffix'));
    throw new Error('unknown generator');
  }
  return new Map([...value].map(([key, item]) => [key, expand(item, root)]));
}
const toMap = value => Array.isArray(value) ? value.map(toMap) : value !== null && typeof value === 'object' && !(value instanceof Map) && value.constructor === Object ? new Map(Object.entries(value).map(([key, item]) => [key, toMap(item)])) : value instanceof Map ? new Map([...value].map(([key, item]) => [key, toMap(item)])) : value;
function bind(value, from, to) {
  if (typeof value === 'string') return value.replaceAll(from, to);
  if (Array.isArray(value)) return value.map(item => bind(item, from, to));
  if (value instanceof Map) return new Map([...value].map(([key, item]) => [bind(key, from, to), bind(item, from, to)]));
  return value;
}

/** Every committed vector of the three modules: `{id, source, row}` with `row` parsed by the engine's own decoder. */
export function vectors() {
  const result = [];
  const lines = file => fs.readFileSync(file, 'utf8').split('\n').filter(Boolean);
  for (const line of lines('test/vectors/guard-negative.golden')) {
    const row = loads(line);
    if (MODULES.includes(row.get('function').split('.')[0])) result.push({ id: 'guard-negative.' + createHash('sha256').update(line).digest('hex').slice(0, 16), source: 'guard-negative', row });
  }
  for (const line of lines('test/vectors/crash-only.golden')) {
    const row = loads(line);
    if (!MODULES.includes(row.get('function').split('.')[0])) continue;
    row.set('outcome', new Map([['exception', row.get('outcome').get('exception')], ['message', row.get('outcome').get('message')]]));
    result.push({ id: 'crash-only.' + row.get('id'), source: 'crash-only', row });
  }
  const trees = JSON.parse(fs.readFileSync('test/vectors/port-m1-3.trees.golden', 'utf8'));
  for (const line of lines('test/vectors/port-m1-3.golden')) {
    const row = loads(line);
    result.push({ id: row.get('id'), source: 'port', row, tree: row.has('tree') ? trees[row.get('tree')] : undefined, digest: row.get('tree') });
  }
  return result;
}

const made = new Set();
/** Runs one vector on the port. Returns `{expected, actual}`, two canonical JSON lines with the scratch root unbound. */
export function evaluate(vector) {
  const { row } = vector;
  let root = null, mark = '${ROOT}';
  if (row.get('profile') === 'lock-files-v1') { root = path.join(SCRATCH, 'lock-files-v1'); mark = '@BUILD@'; if (!made.has(root)) { materialize(root, LOCK_FILES); made.add(root); } }
  else if (vector.tree) {
    root = path.join(SCRATCH, vector.digest);
    if (!made.has(root)) {
      const tree = Object.fromEntries(Object.entries(vector.tree).map(([name, spec]) => [name, '$generate' in spec ? expand(toMap(spec), root) : spec]));
      materialize(root, tree); made.add(root);
    }
  }
  const at = root ?? path.join(SCRATCH, 'none');
  const job = new Map([['function', row.get('function')], ['args', bind(expand(row.get('args'), at), mark, at)], ['kwargs', bind(expand(row.get('kwargs'), at), mark, at)]]);
  if (row.has('seam')) job.set('seam', bind(row.get('seam'), mark, at));
  const actual = run(job).replaceAll(JSON.stringify(at).slice(1, -1), mark);
  return { expected: json.canonical(plain(row.get('outcome'))), actual };
}
export function cleanup() { for (const root of made) remove(root); made.clear(); remove(SCRATCH); }
