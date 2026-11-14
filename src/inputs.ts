/**
 * Closed operator-input schemas and byte verification (controller and target).
 * Port of `oracle/module_utils/connectome_inputs.py` (a49b5dc), one to one.
 *
 * Comments of the form `row N <mutant id>: ... inputs.py:L` mark where a row
 * of the fail-closed inventory (plan section 4.1) lives. A guard is always
 * `need(<condition>, <message>)`.
 */
import { createHash } from 'node:crypto';
import { b64decodeStrict, b64encode } from './pylike/codec.js';
import * as pathlib from './pylike/pathlib.js';
import {
  PyAttributeError, PyOSError, PyRecursionError, PyTypeError, PyValueError,
  eq, hasControl, isDict, isList, isStr, item, keysEqual, keysInclude, keysWithin, subscript, truthy, typeName,
} from './pylike/py.js';
import type { PyDict, PyValue } from './pylike/py.js';
import { loads } from './pylike/pyjson.js';
import type { Pair } from './pylike/pyjson.js';

/** inputs.py:11-13 */
export function need(condition: PyValue, message = 'invalid operator input'): void {
  if (!truthy(condition)) throw new PyValueError(message);
}

/** Closed input failure, distinguishable at the deployment boundary. inputs.py:16-17 */
export class ExternalDataArgError extends PyValueError { override get pyName(): string { return 'ExternalDataArgError'; } }

/** inputs.py:20-23 */
export function external_data_path(value: PyValue): boolean {
  // row 63 inputs.record.data-path: absolute, one leading slash, not the root, lexically normal, no "..", no control characters. inputs.py:21-23
  return isStr(value) && value.startsWith('/') && !value.startsWith('//')
    && value !== '/' && value === pathlib.pathStr(value) && !pathlib.pathParts(value).includes('..')
    && !hasControl(value);
}

const DENIED_FLAGS = new Set(['-e', '--eval', '-p', '--print', '-c', '-r', '--require', '--import', '--loader', '--experimental-loader', '-x', '-m']);
const DENIED_PREFIXES = ['--require', '--import', '--loader', '--experimental-loader'];
const FLAG = /^--?[A-Za-z][A-Za-z0-9_-]*$/u;

/**
 * Literal data options only; code-loading flags can never be exempted.
 * Returns what the oracle's `and` chain returns: `false` for a non-string or a
 * denied flag, `null` when the grammar does not match, else a boolean.
 * inputs.py:26-32
 */
export function external_data_flag(value: PyValue): boolean | null {
  // row 63 inputs.record.data-flag: flag grammar. inputs.py:30
  if (!isStr(value)) return false;
  if (!FLAG.test(value)) return null;
  // row 63 inputs.record.data-flag: never a code-loading flag. inputs.py:31
  if (DENIED_FLAGS.has(value)) return false;
  // row 63 inputs.record.data-flag: nor anything that starts like one. inputs.py:32
  return !DENIED_PREFIXES.some(prefix => value.startsWith(prefix));
}

const DATA_REQUIRED = new Set(['server', 'command', 'args', 'path', 'kind']);
const DATA_ALLOWED = new Set(['server', 'command', 'args', 'path', 'kind', 'flag']);
const SERVER = /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/u;

/** inputs.py:35-58 */
export function validate_external_data_args(records: PyValue): void {
  // row 63 inputs.record.data-args: a list of records. inputs.py:36
  let valid = isList(records);
  const seen = new Set<string>();
  for (const record of valid ? records as PyValue[] : []) {
    // row 63 inputs.record.data-args: closed record, literal server name, argv of strings, file or dir. inputs.py:39-49
    if (!(isDict(record)
      && keysInclude(record, DATA_REQUIRED) && keysWithin(record, DATA_ALLOWED)
      && isStr(item(record, 'server'))
      && SERVER.test(item(record, 'server') as string)
      && external_data_path(item(record, 'command'))
      && isList(item(record, 'args'))
      && (item(record, 'args') as PyValue[]).every(arg => isStr(arg))
      && external_data_path(item(record, 'path'))
      && isStr(item(record, 'kind')) && (item(record, 'kind') === 'file' || item(record, 'kind') === 'dir')
      && (!record.has('flag') || external_data_flag(item(record, 'flag')) === true))) {
      valid = false;
      break;
    }
    const key = item(record, 'server') as string;
    // row 63 inputs.record.data-args: one declaration per server. inputs.py:53
    if (seen.has(key)) {
      valid = false;
      break;
    }
    seen.add(key);
  }
  if (!valid) {
    // row 63 inputs.record.data-args: one closed refusal. inputs.py:58
    throw new ExternalDataArgError('invalid external_data_args');
  }
}

const ACTIVATIONS = new Set(['start', 'install_only']);
const SCOPE_UNIT = new Set(['scope', 'unit']);
const UNIT = /^[A-Za-z0-9_@.-]+\.service$/u;
const PATH_SHA256 = new Set(['path', 'sha256']);
const SHA256 = /^[a-f0-9]{64}$/u;
const SECRET_ENV = new Set(['secret', 'env']);
const NAME = /^[A-Z_][A-Z0-9_]*$/u;
const RESERVED = new Set(['DATA_DIR', 'WORKSPACE_DIR', 'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR']);

/** `config.get`: an `AttributeError` on anything that is not a mapping, as in the oracle. */
function mapping(config: PyValue): PyDict {
  if (!isDict(config)) throw new PyAttributeError("'" + typeName(config) + "' object has no attribute 'get'");
  return config;
}
const getOr = (config: PyDict, key: string, fallback: PyValue): PyValue => { const value = config.get(key); return value === undefined ? fallback : value; };

/** inputs.py:61-99 */
export function validate(config: PyValue): void {
  const settings = mapping(config);
  validate_external_data_args(getOr(settings, 'external_data_args', []));
  const activation = getOr(settings, 'activation', 'start');
  // row 63 inputs.record.activation: one of two values. inputs.py:63-65
  need(isStr(activation)
    && ACTIVATIONS.has(activation),
    'activation must be start or install_only');
  const exclusive = getOr(settings, 'exclusive_with', []);
  // row 63 inputs.record.exclusive: a list. inputs.py:67
  need(isList(exclusive), 'exclusive_with must be a list');
  const units = new Set<string>();
  for (const record of exclusive as PyValue[]) {
    // row 63 inputs.record.exclusive: closed record. inputs.py:70-71
    need(isDict(record) && keysEqual(record, SCOPE_UNIT),
      'closed exclusive_with record required');
    const entry = record as PyDict;
    const scope = item(entry, 'scope'), unit = item(entry, 'unit');
    // row 63 inputs.record.exclusive: known scope, literal unit name. inputs.py:72-75
    need(isStr(scope) && (scope === 'system' || scope === 'user')
      && isStr(unit)
      && UNIT.test(unit)
      && !(unit.startsWith('.') || unit.startsWith('-')), 'invalid exclusive unit');
    const key = (scope as string) + '\u0000' + (unit as string);
    // row 63 inputs.record.exclusive: no duplicates. inputs.py:77
    need(!units.has(key), 'duplicate exclusive unit');
    units.add(key);
  }
  if (settings.has('recipe_file')) {
    const record = item(settings, 'recipe_file');
    // row 63 inputs.record.recipe-file: closed record. inputs.py:81
    need(isDict(record) && keysEqual(record, PATH_SHA256), 'closed recipe_file record required');
    const value = item(record as PyDict, 'path');
    // row 63 inputs.record.recipe-path: absolute, literal, brace-free, no control characters. inputs.py:83-85
    need(isStr(value) && value.startsWith('/') && !value.includes('{') && !value.includes('}')
      && !hasControl(value),
      'controller recipe requires an absolute literal path');
    // row 63 inputs.record.recipe-path: canonical. inputs.py:86
    need(value === pathlib.abspath(value as string), 'controller recipe path must be canonical');
    const sha256 = item(record as PyDict, 'sha256');
    // row 63 inputs.record.recipe-file: a full digest. inputs.py:87-88
    need(isStr(sha256) && SHA256.test(sha256),
      'invalid recipe sha256');
  }
  if (settings.has('secret_files')) {
    const records = item(settings, 'secret_files');
    // row 63 inputs.record.secret-files: a list. inputs.py:91
    need(isList(records), 'secret_files must be a list');
    const seen = new Set<PyValue>();
    for (const record of records as PyValue[]) {
      // row 63 inputs.record.secret-files: closed record. inputs.py:94
      need(isDict(record) && keysEqual(record, SECRET_ENV), 'closed secret_files record required');
      const entry = record as PyDict;
      // row 63 inputs.record.secret-files: both names are literal environment names. inputs.py:95-96
      need([...entry.values()].every(value => isStr(value) && NAME.test(value)),
        'invalid secret file name');
      // row 49 secrets.reserved.file-env: not twice, and never an engine-set variable. inputs.py:97-98
      need(!seen.has(item(entry, 'env')) && !RESERVED.has(item(entry, 'env') as string),
        'duplicate or reserved secret file environment name');
      seen.add(item(entry, 'env'));
    }
  }
}

const sha256hex = (content: Uint8Array): string => createHash('sha256').update(content).digest('hex');

/** inputs.py:102-117 */
export function verify_bytes<Bytes extends Uint8Array>(content: Bytes, record: PyValue): Bytes {
  // row 23 inputs.recipe: the bytes have the declared digest. inputs.py:103
  need(eq(sha256hex(content), subscript(record, 'sha256')), 'recipe digest mismatch');
  const unique = (pairs: Pair[]): PyValue => {
    const result: PyDict = new Map();
    for (const [key, value] of pairs) {
      // row 7 json.strict: a repeated key is refused. inputs.py:107
      need(!result.has(key), 'duplicate recipe JSON key');
      result.set(key, value);
    }
    return result;
  };
  const invalid = (): never => {
    // row 7 json.strict: NaN and the infinities are refused. inputs.py:111
    throw new PyValueError('nonfinite recipe JSON number');
  };
  try {
    const recipe = loads(content, { object_pairs_hook: unique, parse_constant: invalid });
    // row 23 inputs.recipe: the recipe is a JSON object. inputs.py:114
    need(isDict(recipe), 'recipe must be a JSON object');
  } catch (error) {
    // row 23 inputs.recipe: every decoding failure is one refusal. inputs.py:115-116
    if (error instanceof PyValueError || error instanceof PyRecursionError) throw new PyValueError('invalid recipe JSON');
    throw error;
  }
  return content;
}

/** inputs.py:120-127 */
export function read_file(path: string, record: PyValue): Buffer {
  const file = pathlib.pathStr(path);
  try {
    // row 23 inputs.recipe: no symlink alias anywhere in the path, and a regular file. inputs.py:123-124
    need(pathlib.resolve(file) === file && pathlib.lstat(file).isFile(),
      'recipe must be a regular file without symlink aliases');
    return verify_bytes(pathlib.readBytes(file), record);
  } catch (error) {
    // row 23 inputs.recipe: an unreadable input is one refusal. inputs.py:126-127
    if (error instanceof PyOSError) throw new PyValueError('recipe input unavailable');
    throw error;
  }
}

/** Controller-only lookup; payloads are transport, never part of the spec. inputs.py:130-136 */
export function collect(config: PyValue): Map<string, string> {
  validate(config);
  const settings = config as PyDict;
  if (!settings.has('recipe_file')) return new Map();
  const record = item(settings, 'recipe_file') as PyDict;
  return new Map([['cook-recipe.json', b64encode(read_file(item(record, 'path') as string, record))]]);
}

/** inputs.py:139-151 */
export function payloads(config: PyValue, supplied: PyValue | ReadonlyMap<string, unknown> = null): Map<string, Uint8Array> {
  validate(config);
  const settings = config as PyDict;
  const expected = new Set(settings.has('recipe_file') ? ['cook-recipe.json'] : []);
  let transport: PyValue | ReadonlyMap<string, unknown> = supplied;
  if (transport === null) transport = collect(config); // Standalone engine: caller is the controller too.
  // row 63 inputs.record.transport: the transported file set is exactly the expected one. inputs.py:144
  need(transport instanceof Map && keysEqual(transport as PyDict, expected), 'recipe transport inventory mismatch');
  if (!expected.size) return new Map();
  let content: Buffer;
  try {
    // row 63 inputs.record.transport: strict base64. inputs.py:148
    content = b64decodeStrict((transport as ReadonlyMap<string, unknown>).get('cook-recipe.json'));
  } catch (error) {
    // row 63 inputs.record.transport: a bad transport is one refusal. inputs.py:149-150
    if (error instanceof PyValueError || error instanceof PyTypeError) throw new PyValueError('invalid recipe transport');
    throw error;
  }
  return new Map([['cook-recipe.json', verify_bytes(content, item(settings, 'recipe_file'))]]);
}

/** Retained builds need only their installed bytes, never controller files. inputs.py:154-162 */
export function installed(build: string, config: PyValue): void {
  validate(config);
  const settings = config as PyDict;
  if (settings.has('recipe_file')) {
    const path = pathlib.joinPath(pathlib.pathStr(build), 'cook-recipe.json');
    read_file(path, item(settings, 'recipe_file'));
    const info = pathlib.lstat(path);
    // row 23 inputs.recipe: the installed copy is 0600 and owned by this account. inputs.py:161-162
    need((info.mode & 0o7777) === 0o600 && info.uid === process.geteuid!(),
      'installed recipe mode or owner mismatch');
  }
}
