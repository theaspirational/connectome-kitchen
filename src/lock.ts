/**
 * Fail-closed reader for cook's host lock v2. Never include input in errors.
 * Port of `oracle/module_utils/connectome_lock.py` (a49b5dc), one to one.
 *
 * Comments of the form `row N <mutant id>: ... lock.py:L` mark where a row of
 * the fail-closed inventory (plan section 4.1) lives; `scripts/porting-map.py`
 * reads them. A guard is always `require(<condition>, <message>)`, so that its
 * mutant is "replace the condition by true".
 */
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { compare } from './pylike/json.js';
import { fromisoformat } from './pylike/datetime.js';
import * as pathlib from './pylike/pathlib.js';
import {
  Int, PyAttributeError, PyKeyError, PyOSError, PyRecursionError, PyTypeError, PyValueError,
  eq, get, hasControl, inStrSet, isDict, isInt, isList, isStr, item, keysEqual, keysInclude, keysWithin, strIn, subscript, truthy, typeName,
} from './pylike/py.js';
import type { PyDict, PyValue } from './pylike/py.js';
import { loads } from './pylike/pyjson.js';
import type { Pair } from './pylike/pyjson.js';
import { S, W } from './pylike/regex.js';
import * as url from './pylike/url.js';
import type * as Archive from './archive.js';
import type * as Inputs from './inputs.js';

/**
 * The oracle imports `connectome_inputs` and `connectome_archive` inside
 * `map_lock`, not at module level (lock.py:149-152, :256-258, :282-284), and
 * `archive` imports this module. The port keeps both properties: no static
 * edge from here to either module, and the load happens at the same three
 * places. Node loads an ES module synchronously through `require`, and hands
 * back the same module instance an `import` gets, so error classes compare.
 */
const lazy = createRequire(import.meta.url);

export class LockError extends PyValueError { override get pyName(): string { return 'LockError'; } }

/** lock.py:14-16 */
export function require(condition: PyValue, message: string): void {
  if (!truthy(condition)) throw new LockError(message);
}

/** lock.py:19-20 */
export function text(value: PyValue): value is string {
  return isStr(value) && value.length > 0 && !hasControl(value);
}

/** lock.py:23-24 */
export function digest(value: PyValue, size = 64): boolean {
  return isStr(value) && new RegExp('^[a-f0-9]{' + size + '}$', 'u').test(value);
}

// Cook, npm and engine provenance use concrete versions, never ranges.
const VERSION = /^v?(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/u;
/** lock.py:27-31 */
export function version(value: PyValue): boolean {
  return text(value) && VERSION.test(value);
}

/*
 * The four patterns of lock.py:40-46, rewritten for JavaScript (plan section
 * 3.2 rows 5 and 6). What differs between the two regex engines, and how each
 * difference is closed here:
 *   \w, \s, \S   Unicode sets in a Python str pattern; the classes below are
 *                built from the oracle interpreter's own tables.
 *   \b           always stands before a word character in these patterns, so
 *                it is "not preceded by a word character".
 *   .            excludes only "\n" in Python.
 *   re.I         a letter also matches what lowercases to it in CPython plus
 *                its `_equivalences`: U+0130 and U+0131 for i, U+017F for s,
 *                U+212A for k. JavaScript's /i folds differently, so the
 *                alternatives are spelled out and no pattern uses /i.
 */
const insensitive = (word: string): string => Array.from(word, letter => {
  const lower = letter.toLowerCase(), upper = letter.toUpperCase();
  if (lower === upper) return letter;
  return '[' + lower + upper + (lower === 'i' ? '\\u0130\\u0131' : lower === 's' ? '\\u017f' : lower === 'k' ? '\\u212a' : '') + ']';
}).join('');
const LETTER = 'A-Za-z\\u0130\\u0131\\u017f\\u212a';
const BOUNDARY = '(?<![' + W + '])';
// row 1 lock.credential: the credential shapes, one alternative per line. lock.py:40-46
const TOKEN_SHAPES = [
  '(?:gh[oprsu]_|github_pat_|glpat[-_]|xox[baprs]-|sk-)[A-Za-z0-9_-]{12,}',
  BOUNDARY + 'hvs\\.[A-Za-z0-9_-]+',
  BOUNDARY + '[0-9]{8,10}:[A-Za-z0-9_-]{30,}',
  '-----' + 'BEGIN [^\\n]*PRIVATE KEY-----',
  BOUNDARY + 'eyJ[' + W + '-]+\\.[' + W + '-]+\\.[' + W + '-]+',
];
const ASSIGNMENT = BOUNDARY + '[' + LETTER + '_]*(?:' + ['TOKEN', 'PASSWORD', 'API_KEY', 'SECRET'].map(insensitive).join('|') + ')[' + S + ']*[:=][' + S + ']*[^' + S + ']+';
const URL_SHAPES = [
  '://[^/' + S + ']*:[^/' + S + ']*@',
  '[?&](?:' + ['token', 'password', 'secret', 'api_key'].map(insensitive).join('|') + ')=',
];
const STORE_PATH = '(?:' + insensitive('vault') + '|' + insensitive('secret') + ')/[' + LETTER + '][' + W + '-]+/[' + W + '/-]+';
const CREDENTIAL_SHAPES: readonly RegExp[] = [TOKEN_SHAPES.join('|'), ASSIGNMENT, URL_SHAPES.join('|'), STORE_PATH].map(source => new RegExp(source, 'u'));

/*
 * CPython refuses deep recursion with RecursionError, which `read_lock` turns
 * into a refusal (lock.py:332). `names_only` is the only recursive function
 * here. Its bound is modelled, not left to the JavaScript stack: the default
 * limit is 1000 frames, the script's own top level being the first (measured
 * on the oracle interpreter: a frame may sit at depth 1000). A string costs up to
 * five more frames (the generator expression, then `credential_shaped`,
 * `re.search`, `re._compile` and, for the three patterns compiled with a flag,
 * the flag's `value` property). The caller's own depth shifts the bound in the
 * oracle too; the constants below are the script-level case.
 */
const PY_FRAME_LIMIT = 1000;
const SCRIPT_CALL_FRAME = 2; // the frame of a function a script calls directly
const READ_LOCK_FRAME = 3;   // map_lock when read_lock called it
function frameCheck(frame: number): void {
  if (frame > PY_FRAME_LIMIT) throw new PyRecursionError('maximum recursion depth exceeded');
}

function credentialShaped(value: string, frame: number): boolean {
  // The first pattern is compiled without a flag: credential_shaped, re.search, re._compile.
  frameCheck(frame + 2);
  if (CREDENTIAL_SHAPES[0]!.test(value)) return true;
  frameCheck(frame + 4);
  return CREDENTIAL_SHAPES[1]!.test(value) || CREDENTIAL_SHAPES[2]!.test(value) || CREDENTIAL_SHAPES[3]!.test(value);
}

/**
 * Credential shapes covered by the publication gate, plus provider variants.
 *
 * Not a guarantee against arbitrary secrets: schemas and names-only inputs
 * remain mandatory. Call on every string, not just secret-labelled fields.
 * lock.py:34-46
 */
export function credential_shaped(value: PyValue): boolean {
  if (!isStr(value)) throw new PyTypeError("expected string or bytes-like object, got '" + typeName(value) + "'");
  return credentialShaped(value, SCRIPT_CALL_FRAME);
}

const USER = /^[A-Za-z0-9._][A-Za-z0-9._-]*$/u;
const TRANSPORT_PREFIX = /^(?:[hH][tT][tT][pP][sSſ]?|[sSſ][sSſ][hH]|[gG][iIİı][tT]|[fF][iIİı][lL][eE]|[fF][tT][pP]|[eE][xX][tT]):/u;
const SCP = /^(?:[A-Za-z0-9._][A-Za-z0-9._-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*:(?!:)[^\n]+$/u;
const SPACE = new RegExp('[' + S + ']', 'u');

/** Allow only credential-free HTTPS, SSH/scp, or absolute local sources. lock.py:49-76 */
export function repository(value: PyValue): boolean {
  // row 3 lock.repository: text, and no credential shape, before and after unquoting. lock.py:51-55
  if (!text(value)) return false;
  if (credential_shaped(value)) return false;
  const decoded = url.unquote(value);
  if (!text(decoded)) return false;
  if (credential_shaped(decoded)) return false;
  if (/[?#\\]/u.test(decoded)) return false;
  if (value.startsWith('/')) return true;
  if (SPACE.test(decoded)) return false;
  try {
    const parts = url.split(value);
    if (parts.scheme === 'https' || parts.scheme === 'ssh') {
      // Accessing port also rejects malformed/non-numeric port syntax.
      const port = url.port(parts.netloc);
      const host = url.hostname(parts.netloc), user = url.username(parts.netloc);
      // row 3 lock.repository: host and path, no password, no option-like ssh host, a real port, a user only for ssh. lock.py:65-69
      return Boolean(host
        && parts.path
        && url.password(parts.netloc) === null
        && (parts.scheme !== 'ssh' || !url.unquote(host).startsWith('-'))
        && (port === null || (0 < port && port <= 65535))
        && (user === null || (parts.scheme === 'ssh' && USER.test(url.unquote(user)))));
    }
  } catch (error) {
    if (error instanceof PyValueError) return false;
    throw error;
  }
  // scp syntax has no URI scheme. Refuse known transport/helper prefixes
  // rather than reinterpreting a disallowed URI as an scp hostname.
  // row 3 lock.repository: no transport or helper prefix. lock.py:74-75
  if (TRANSPORT_PREFIX.test(decoded)) return false;
  // row 3 lock.repository: scp shape, and never a URI. lock.py:76
  return SCP.test(decoded) && !decoded.includes('://');
}

const VALUE_FIELDS = new Set(['value', 'values', 'secret', 'secrets', 'token', 'password', 'apikey', 'apitoken', 'authtoken', 'secretvalue', 'resolvedvalue']);

function namesOnly(value: PyValue, frame: number): void {
  frameCheck(frame);
  if (isDict(value)) {
    for (const [key, entry] of value) {
      namesOnly(key, frame + 1);
      const compact = key.toLowerCase().replace(/[^a-z]/gu, '');
      // row 2 lock.names-only: value-looking keys refused at any depth. lock.py:85-87
      require(!VALUE_FIELDS.has(compact), 'lock contains a value-looking secret field');
      namesOnly(entry, frame + 1);
    }
  } else if (isList(value)) {
    for (const entry of value) namesOnly(entry, frame + 1);
  } else if (isStr(value)) {
    frameCheck(frame + 1); // the generator expression inside any()
    // row 2 lock.names-only: control characters refused in every string. lock.py:93
    require(!hasControl(value), 'lock contains control characters');
    // row 2 lock.names-only: credential shapes refused in every string. lock.py:94
    require(!credentialShaped(value, frame + 1), 'lock contains a value-looking secret');
  }
}

/** Defense in depth, including all nested strings and mapping keys. lock.py:79-94 */
export function names_only(value: PyValue): void {
  namesOnly(value, SCRIPT_CALL_FRAME);
}

const EXTERNAL_NAME = /^[A-Za-z0-9_][A-Za-z0-9_.-]*$/u;
/** lock.py:97-98 */
export function external_name(value: PyValue): boolean {
  return text(value) && EXTERNAL_NAME.test(value);
}

/** A literal, absolute, lexically normal executable path; ownership is checked at activation. lock.py:101-104 */
export function external_command(value: PyValue): boolean {
  return text(value) && value.startsWith('/') && !value.includes('${')
    && value === pathlib.pathStr(value) && value !== '/' && !pathlib.pathParts(value).includes('..');
}

/** lock.py:107-112 */
export function contained_file(build: string, value: PyValue): string {
  // row 10 lock.launch-units: an artifact path is text. lock.py:108
  require(text(value), 'lock artifact path required');
  const path = pathlib.joinPath(build, value as string);
  // row 10 lock.launch-units: the resolved path stays inside the resolved build. lock.py:110
  require(pathlib.isRelativeTo(pathlib.resolve(path), pathlib.resolve(build)), 'lock artifact path escapes build');
  // row 10 lock.launch-units: a regular file, and not a link to one. lock.py:111
  require(pathlib.isFile(path) && !pathlib.isSymlink(path), 'lock artifact must be a regular file');
  return path;
}

/** Keyword arguments of `map_lock` and `read_lock` (lock.py:115, :315). */
export interface LockOptions {
  host_pin?: PyValue;
  required_names?: Iterable<PyValue>;
  config?: PyValue;
  framework_pin?: PyValue;
  framework_record?: PyValue;
}

const TWO = new Int(2n);
const REQUIRED = new Set(['version', 'backend', 'cook', 'recipe', 'recipePath', 'createdAt', 'connectomeHost', 'components', 'localExtensions', 'requirements', 'build', 'credentials', 'outputs', 'launch']);
const OPTIONAL = new Set(['state', 'layout', 'engines', 'hostDependencies', 'incomplete', 'units', 'health', 'external']);
const SUPPORTED = new Set([...REQUIRED, ...OPTIONAL]);
const ID_DIR = new Set(['id', 'dir']);
const COOK_FIELDS = new Set(['version', 'commit']);
const PATH_SHA256 = new Set(['path', 'sha256']);
const HOST_FIELDS = new Set(['url', 'ref', 'commit', 'path']);
const REQUIREMENT_FIELDS = new Set(['name', 'envName', 'origin']);
const ORIGINS = new Set(['env-file', 'process-env', 'probe', 'prompt', 'unresolved']);
const COMPONENT_KINDS = new Set(['git', 'npm']);
const GIT_FIELDS = new Set(['kind', 'key', 'role', 'url', 'ref', 'commit', 'path', 'install']);
const NPM_FIELDS = new Set(['kind', 'key', 'role', 'name', 'spec', 'version', 'integrity']);
const ROLES = new Set(['mcp', 'extension']);
const OUTPUT_FIELDS = new Set(['path', 'mode', 'sha256', 'sha256Omitted']);
const EXTENSION_FIELDS = new Set(['name', 'hostDir', 'path']);
const STATE_FIELDS = new Set(['path']);
const ENGINE_NAMES = new Set(['bun', 'node']);
const ENGINE_FIELDS = new Set(['path', 'realPath', 'version']);
const LAYOUT_ROOTS = ['runtime', 'config', 'state', 'workspace', 'credentials'] as const;
const LAYOUT_FIELDS = new Set([...LAYOUT_ROOTS, 'schema', 'connectorState']);
const SERVER_FIELDS = new Set(['name', 'command']);
const DEPENDENCY_FIELDS = new Set(['name', 'originalRange', 'version', 'sha256', 'size', 'snapshot', 'packageJsonDelta']);
const DELTA_FIELDS = new Set(['direct', 'override']);
const EDIT_FIELDS = new Set(['section', 'name', 'before', 'after']);
const DELTA_SECTIONS: readonly (readonly [string, ReadonlySet<string>])[] = [
  ['direct', new Set(['dependencies', 'devDependencies', 'optionalDependencies'])],
  ['override', new Set(['overrides', 'resolutions'])],
];
const HEALTH_FIELDS = new Set(['command']);
const LAUNCH_FIELDS = new Set(['kind', 'script', 'userUnit']);

const TIMESTAMP = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z$/u;
const ENV_NAME = /^[A-Z_][A-Z0-9_]*$/u;
const CREDENTIAL_NAME = /^(?:[A-Z_][A-Z0-9_]*|[A-Za-z0-9_./-]+#[A-Za-z0-9_-]+)$/u;
const INTEGRITY = /^(?:sha(?:1|256|384|512)-[A-Za-z0-9+/]+={0,2})(?: (?:sha(?:1|256|384|512)-[A-Za-z0-9+/]+={0,2}))*$/u;
const UNIT_NAME = /^[A-Za-z0-9_.-]+\.service$/u;

const sha256 = (bytes: Uint8Array): string => createHash('sha256').update(bytes).digest('hex');
/** `set(mapping) - {skip} == {names}`. */
function keysEqualExcept(mapping: PyDict, names: ReadonlySet<string>, skip: string): boolean {
  let count = 0;
  for (const key of mapping.keys()) { if (key === skip) continue; if (!names.has(key)) return false; count++; }
  return count === names.size;
}
/** `set(iterable)`: every element must be hashable. */
function hashed(values: Iterable<PyValue>): PyValue[] {
  const result = [...values];
  for (const value of result) if (isList(value) || isDict(value)) throw new PyTypeError("unhashable type: '" + typeName(value) + "'");
  return result;
}

function mapLock(data: PyValue, build: string, options: LockOptions, frame: number): PyDict {
  const { host_pin = null, required_names = [], config = null, framework_pin = null, framework_record = null } = options;
  const root = pathlib.pathStr(build);
  // row 4 lock.schema.top: exact version. lock.py:122-123
  require(isDict(data) && isInt(get(data, 'version')) && eq(item(data, 'version'), TWO),
    'host lock v2 required; legacy and unknown versions refused');
  const lock = data as PyDict;
  // row 4 lock.schema.top: exact backend. lock.py:124
  require(eq(get(lock, 'backend'), 'host'), 'host lock v2 required');
  // row 4 lock.schema.top: every required key. lock.py:129
  require(keysInclude(lock, REQUIRED), 'lock missing required v2 fields');
  // row 4 lock.schema.top: no key outside the required and optional sets. lock.py:130
  require(keysWithin(lock, SUPPORTED), 'lock contains unsupported fields');
  // row 2 lock.names-only: the whole document, keys included. lock.py:131
  namesOnly(lock, frame + 1);
  // row 5 lock.incomplete: an incomplete install is refused. lock.py:132
  require(!lock.has('incomplete'), 'incomplete cook install refused');
  const identity = item(lock, 'build');
  // row 6 lock.build-identity: the lock names this build directory. lock.py:134-136
  require(isDict(identity) && keysEqual(identity, ID_DIR)
    && digest(item(identity, 'id')) && text(item(identity, 'dir')) && pathlib.isAbsolute(item(identity, 'dir') as string)
    && pathlib.resolve(item(identity, 'dir') as string) === pathlib.resolve(root), 'lock build identity/directory mismatch');
  const cook = item(lock, 'cook');
  // row 59 lock.schema.cook: closed cook record with a concrete version. lock.py:137-138
  require(isDict(cook) && keysWithin(cook, COOK_FIELDS)
    && version(get(cook, 'version')), 'lock cook provenance required');
  if ((cook as PyDict).has('commit')) {
    // row 59 lock.schema.cook: a recorded commit is a full id. lock.py:140
    require(digest(item(cook as PyDict, 'commit'), 40), 'invalid cook commit');
  }
  const recipe = item(lock, 'recipe');
  // row 59 lock.schema.recipe: closed recipe provenance, equal to recipePath. lock.py:142-145
  require(isDict(recipe) && keysEqual(recipe, PATH_SHA256)
    && text(item(recipe, 'path')) && digest(item(recipe, 'sha256'))
    && text(item(lock, 'recipePath')) && eq(item(recipe, 'path'), item(lock, 'recipePath')) && text(item(lock, 'createdAt')),
    'invalid lock recipe provenance');
  if (config !== null && strIn('recipe_file', config)) {
    try {
      const inputs = lazy('./inputs.js') as typeof Inputs; // lock.py:149-152
      inputs.validate(config);
      const path = pathlib.joinPath(root, 'cook-recipe.json');
      // row 59 lock.schema.operator-recipe: the installed recipe is read strictly against the operator digest. lock.py:154
      inputs.read_file(path, item(config as PyDict, 'recipe_file'));
      // row 59 lock.schema.operator-recipe: the lock records that digest and that path. lock.py:155-156
      require(eq(item(recipe as PyDict, 'sha256'), subscript(item(config as PyDict, 'recipe_file'), 'sha256'))
        && eq(item(recipe as PyDict, 'path'), path), 'operator recipe provenance mismatch');
    } catch (error) {
      if (!(error instanceof PyValueError)) throw error;
      // row 59 lock.schema.operator-recipe: every input refusal is a lock refusal. lock.py:157-158
      throw new LockError('invalid operator recipe provenance');
    }
  }
  const created = item(lock, 'createdAt') as string;
  try {
    // row 59 lock.schema.timestamp: UTC timestamp grammar and a real date. lock.py:160-161
    require(TIMESTAMP.test(created)
      && fromisoformat(created.replaceAll('Z', '+00:00')), 'invalid lock timestamp');
  } catch (error) {
    if (!(error instanceof PyValueError)) throw error;
    // row 59 lock.schema.timestamp: an impossible date is a lock refusal. lock.py:162-163
    throw new LockError('invalid lock timestamp');
  }
  const host = item(lock, 'connectomeHost');
  // row 8 lock.pin-names-external: closed host record whose commit is the requested pin. lock.py:165-168
  require(isDict(host) && keysWithin(host, HOST_FIELDS)
    && repository(get(host, 'url')) && text(get(host, 'ref')) && digest(get(host, 'commit'), 40)
    && (!host.has('path') || text(item(host, 'path')))
    && (host_pin === null || eq(item(host, 'commit'), host_pin)), 'lock host pin differs from request');
  const requirements = item(lock, 'requirements');
  // row 59 lock.schema.requirements: names-only records with a closed origin set. lock.py:170-175
  require(isList(requirements) && requirements.every(r =>
    isDict(r) && keysEqual(r, REQUIREMENT_FIELDS)
    && text(item(r, 'name')) && text(item(r, 'envName')) && ENV_NAME.test(item(r, 'envName') as string)
    && text(item(r, 'origin'))
    && ORIGINS.has(item(r, 'origin') as string)), 'requirements must be names-only v2 records');
  const credentials = item(lock, 'credentials');
  // row 59 lock.schema.credentials: credential names only. lock.py:177-179
  require(isList(credentials) && credentials.every(n => isStr(n)
    && CREDENTIAL_NAME.test(n)), 'invalid credential names; values forbidden');
  const declared = new Set<PyValue>([...(credentials as PyValue[]), ...(requirements as PyDict[]).map(r => item(r, 'envName'))]);
  const wanted = hashed(required_names);
  // row 8 lock.pin-names-external: every required secret name is declared. lock.py:181
  require(wanted.every(n => declared.has(n)), 'lock omits a declared required secret');
  // row 59 lock.schema.components: the three inventories are lists. lock.py:182-183
  require(isList(item(lock, 'components')) && isList(item(lock, 'localExtensions'))
    && isList(item(lock, 'outputs')), 'invalid lock inventories');
  for (const component of item(lock, 'components') as PyValue[]) {
    // row 59 lock.schema.components: a component is a git or an npm record. lock.py:185-186
    require(isDict(component) && inStrSet(get(component, 'kind'), COMPONENT_KINDS),
      'invalid component provenance');
    const entry = component as PyDict;
    if (eq(item(entry, 'kind'), 'git')) {
      // row 59 lock.schema.components: closed git provenance. lock.py:188-191
      require(keysWithin(entry, GIT_FIELDS)
        && ['key', 'ref', 'path'].every(k => text(get(entry, k)))
        && repository(get(entry, 'url')) && isStr(get(entry, 'install'))
        && digest(get(entry, 'commit'), 40), 'invalid git component provenance');
    } else {
      // row 59 lock.schema.components: closed npm provenance. lock.py:193-198
      require(keysWithin(entry, NPM_FIELDS)
        && ['key', 'name', 'spec'].every(k => text(get(entry, k)))
        && (!entry.has('version') || version(item(entry, 'version')))
        && (!entry.has('integrity') || (text(item(entry, 'integrity')) && INTEGRITY.test(item(entry, 'integrity') as string))),
        'invalid npm provenance');
    }
    // row 59 lock.schema.components: a closed role set. lock.py:199
    require(inStrSet(get(entry, 'role'), ROLES), 'invalid component role');
  }
  for (const output of item(lock, 'outputs') as PyValue[]) {
    // row 59 lock.schema.outputs: closed output record with a permission mode. lock.py:201-203
    require(isDict(output) && keysWithin(output, OUTPUT_FIELDS)
      && text(get(output, 'path')) && isInt(get(output, 'mode')) && (item(output, 'mode') as Int).value >= 0n && (item(output, 'mode') as Int).value <= 0o777n,
      'invalid output record');
    const record = output as PyDict;
    // row 59 lock.schema.outputs: a recorded digest is a digest. lock.py:204
    require(!record.has('sha256') || digest(item(record, 'sha256')), 'invalid output digest');
    // row 59 lock.schema.outputs: the only reason to omit one. lock.py:205-206
    require(!record.has('sha256Omitted') || eq(item(record, 'sha256Omitted'), 'contains-requirement-value'),
      'invalid output digest omission');
  }
  for (const extension of item(lock, 'localExtensions') as PyValue[]) {
    // row 59 lock.schema.extensions: closed local extension record. lock.py:208-209
    require(isDict(extension) && keysEqual(extension, EXTENSION_FIELDS)
      && [...extension.values()].every(v => text(v)), 'invalid local extension record');
  }
  if (lock.has('state')) {
    const state = item(lock, 'state');
    // row 59 lock.schema.state: closed state record with an absolute path. lock.py:211-212
    require(isDict(state) && keysEqual(state, STATE_FIELDS)
      && text(item(state, 'path')) && pathlib.isAbsolute(item(state, 'path') as string), 'invalid state record');
  }
  if (lock.has('engines')) {
    const engines = item(lock, 'engines');
    // row 59 lock.schema.engines: exactly bun and node. lock.py:214
    require(isDict(engines) && keysEqual(engines, ENGINE_NAMES), 'invalid engine records');
    for (const engine of (engines as PyDict).values()) {
      // row 59 lock.schema.engines: closed engine record. lock.py:216-217
      require(isDict(engine) && keysEqual(engine, ENGINE_FIELDS)
        && text(item(engine, 'path')) && text(item(engine, 'realPath')) && version(item(engine, 'version')), 'invalid engine record');
    }
  }
  if (lock.has('layout')) {
    const layout = item(lock, 'layout');
    // row 59 lock.schema.layout: closed layout record with absolute roots. lock.py:221-224
    require(isDict(layout) && keysEqual(layout, LAYOUT_FIELDS)
      && eq(item(layout, 'schema'), 'connectome.host-layout/v1')
      && LAYOUT_ROOTS.every(k => text(item(layout, k)) && pathlib.isAbsolute(item(layout, k) as string))
      && isDict(item(layout, 'connectorState')), 'invalid layout record');
    for (const bindings of (item(layout as PyDict, 'connectorState') as PyDict).values()) {
      // row 59 lock.schema.layout: connector state is names to paths. lock.py:226-228
      require(isDict(bindings) && [...bindings].every(([k, v]) =>
        ENV_NAME.test(k) && text(v)),
        'invalid connector state names/paths');
    }
  }
  if (lock.has('external')) {
    // Host-installed MCP servers (recipe source.external). Cook omits the
    // key when there are none and never records a commit, args or env.
    const external = item(lock, 'external');
    // row 8 lock.pin-names-external: closed external server records, literal names and commands. lock.py:233-236
    require(isList(external) && external.length > 0 && external.every(server =>
      isDict(server) && keysEqual(server, SERVER_FIELDS)
      && external_name(item(server, 'name')) && external_command(item(server, 'command'))), 'invalid external server record');
    const names = (external as PyDict[]).map(server => item(server, 'name') as string);
    // row 8 lock.pin-names-external: unique external server names. lock.py:238
    require(new Set(names).size === names.length, 'duplicate external server name');
    const sorted = [...names].sort(compare);
    // ASCII names: Python order equals cook's JavaScript string order.
    // row 8 lock.pin-names-external: sorted as cook writes them. lock.py:240-241
    require(names.every((name, index) => name === sorted[index]),
      'external servers must be sorted as cook writes them');
    if (lock.has('layout')) {
      const bound = item(item(lock, 'layout') as PyDict, 'connectorState') as PyDict;
      // row 8 lock.pin-names-external: connector state never names an external server. lock.py:243-244
      require(!names.some(name => bound.has(name)),
        'connector state must not name an external server');
    }
  }
  if (lock.has('hostDependencies')) {
    const dependencies = item(lock, 'hostDependencies');
    // row 9 lock.host-deps: the inventory is a list. lock.py:246
    require(isList(dependencies), 'invalid host dependency inventory');
    const seen = new Set<PyValue>();
    for (const dependency of dependencies as PyValue[]) {
      // row 9 lock.host-deps: closed dependency record. lock.py:249-254
      require(isDict(dependency) && keysEqualExcept(dependency, DEPENDENCY_FIELDS, 'provenance')
        && ['name', 'originalRange', 'snapshot'].every(k => text(item(dependency, k)))
        && version(item(dependency, 'version'))
        && digest(item(dependency, 'sha256')) && isInt(item(dependency, 'size')) && (item(dependency, 'size') as Int).value > 0n,
        'invalid host dependency record');
      const record = dependency as PyDict;
      const archive = lazy('./archive.js') as typeof Archive; // lock.py:255-258
      // row 9 lock.host-deps: an npm name, once. lock.py:259-260
      require(archive.npm_name(item(record, 'name')) && !seen.has(item(record, 'name')),
        'invalid or duplicate host dependency name');
      seen.add(item(record, 'name'));
      if (record.has('provenance')) {
        try {
          const provenance = item(record, 'provenance');
          archive.validate_dependency_record(provenance);
          // row 9 lock.host-deps: provenance describes this dependency. lock.py:266-267
          require(['name', 'sha256', 'version'].every(k => eq(subscript(provenance, k), item(record, k))),
            'dependency provenance identity mismatch');
        } catch (error) {
          if (!(error instanceof PyValueError)) throw error;
          // row 9 lock.host-deps: every provenance refusal is a lock refusal. lock.py:268-269
          throw new LockError('invalid host dependency provenance');
        }
      }
      const delta = item(record, 'packageJsonDelta');
      // row 9 lock.host-deps: closed delta record. lock.py:271
      require(isDict(delta) && keysEqual(delta, DELTA_FIELDS), 'invalid dependency delta');
      for (const [kind, sections] of DELTA_SECTIONS) {
        const edit = item(delta as PyDict, kind);
        // row 9 lock.host-deps: closed policy edit for this dependency. lock.py:275-278
        require(isDict(edit) && keysEqual(edit, EDIT_FIELDS)
          && inStrSet(item(edit, 'section'), sections) && eq(item(edit, 'name'), item(record, 'name')) && text(item(edit, 'after'))
          && (kind === 'direct' ? text(item(edit, 'before')) : item(edit, 'before') === null),
          'invalid dependency policy edit');
      }
    }
  }
  if (config !== null) {
    try {
      const archive = lazy('./archive.js') as typeof Archive; // lock.py:281-284
      // row 9 lock.host-deps: the inventory is bound to config. lock.py:285
      archive.check_dependencies(lock, config, framework_pin, framework_record);
    } catch (error) {
      if (!(error instanceof PyValueError)) throw error;
      // row 9 lock.host-deps: every mismatch is a lock refusal. lock.py:286-287
      throw new LockError('host dependencies differ from config');
    }
  }
  if (lock.has('health')) {
    const health = item(lock, 'health');
    // row 59 lock.schema.health: closed health record with a non-empty argv. lock.py:290-292
    require(isDict(health) && keysEqual(health, HEALTH_FIELDS)
      && isList(item(health, 'command')) && (item(health, 'command') as PyValue[]).length > 0
      && (item(health, 'command') as PyValue[]).every(arg => text(arg)), 'invalid planned health command');
  }
  const launch = item(lock, 'launch');
  // row 59 lock.schema.launch: closed launch record, script launcher only. lock.py:294-295
  require(isDict(launch) && keysWithin(launch, LAUNCH_FIELDS)
    && eq(get(launch, 'kind'), 'script'), 'host script launcher required');
  const launcher = launch as PyDict;
  // row 10 lock.launch-units: the launch script is a contained regular file. lock.py:296
  contained_file(root, get(launcher, 'script'));
  const result: PyDict = new Map(lock);
  if (launcher.has('userUnit')) {
    // row 59 lock.schema.units-ambiguous: one unit declaration, not two. lock.py:299
    require(!lock.has('units'), 'ambiguous lock unit declarations');
    const path = contained_file(root, item(launcher, 'userUnit'));
    // row 10 lock.launch-units: an exported unit has a literal unit name. lock.py:301
    require(UNIT_NAME.test(pathlib.name(path)), 'invalid exported user unit');
    result.set('units', [new Map<string, PyValue>([
      ['path', pathlib.relativeTo(path, root)],
      ['sha256', sha256(pathlib.readBytes(path))],
    ])]);
  }
  if (result.has('units')) {
    const units = item(result, 'units');
    // row 10 lock.launch-units: a non-empty unit list. lock.py:305
    require(isList(units) && units.length > 0, 'invalid unit records');
    for (const unit of units as PyValue[]) {
      // row 10 lock.launch-units: closed unit record. lock.py:307-308
      require(isDict(unit) && keysEqual(unit, PATH_SHA256)
        && digest(item(unit, 'sha256')), 'invalid unit record');
      const path = contained_file(root, item(unit as PyDict, 'path'));
      // row 10 lock.launch-units: a service file whose bytes match the recorded digest. lock.py:310-311
      require(pathlib.suffix(path) === '.service' && eq(sha256(pathlib.readBytes(path)), item(unit as PyDict, 'sha256')),
        'unit output digest mismatch');
    }
  }
  return result;
}

/**
 * Return the role's deployment record; do not rewrite cook's identity.
 *
 * launch.userUnit is cook's actual export hint. The role consumes path/hash
 * units, so hash the contained export here, not a guessed upstream units field.
 * lock.py:115-312
 */
export function map_lock(data: PyValue, build: string, options: LockOptions = {}): PyDict {
  return mapLock(data, build, options, SCRIPT_CALL_FRAME);
}

/** lock.py:315-333 */
export function read_lock(path: string, options: LockOptions = {}): PyDict {
  const unique_fields = (pairs: Pair[]): PyValue => {
    const result: PyDict = new Map();
    for (const [key, value] of pairs) {
      // row 7 json.strict: a repeated key is refused, not merged. lock.py:319
      require(!result.has(key), 'duplicate lock fields refused');
      result.set(key, value);
    }
    return result;
  };
  const invalid_number = (_name: string): PyValue => {
    // row 7 json.strict: NaN and the infinities are refused. lock.py:324
    throw new LockError('nonfinite lock number refused');
  };
  try {
    const file = pathlib.pathStr(path);
    const data = loads(pathlib.readText(file), { object_pairs_hook: unique_fields, parse_constant: invalid_number });
    return mapLock(data, pathlib.parent(file), options, READ_LOCK_FRAME);
  } catch (error) {
    if (error instanceof LockError) {
      // row 7 json.strict: a declared refusal keeps its own message. lock.py:330-331
      throw error;
    }
    if (!(error instanceof PyOSError || error instanceof PyValueError || error instanceof PyTypeError || error instanceof PyKeyError
      || error instanceof PyAttributeError || error instanceof PyRecursionError)) throw error;
    // row 7 json.strict: every other failure the oracle names is one refusal. lock.py:332-333
    throw new LockError('malformed or unreadable cook lock v2');
  }
}
