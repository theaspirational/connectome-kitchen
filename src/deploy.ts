/**
 * Transactional, unprivileged deployment engine.
 * Port of `oracle/module_utils/connectome_deploy.py` (a49b5dc), the branches a deployment without a catalog takes.
 *
 * STATUS: success-path port. It is held against the oracle by the deploy corpus (`test/corpus`), which runs both
 * engines on whole deployments. It has no mutants and no vectors for hostile input, and it is not listed as ported
 * in PORTING.md. What is not here:
 *   - catalog mode. A configuration with an active catalog is refused in `validate_config` (the oracle goes on);
 *   - `~user` expansion of `root`, `base` and `unit_dir`;
 *   - the exact exception class of a wrongly typed value where the oracle answers with its generic failure anyway.
 * `docs/port-notes.md` has the list.
 *
 * The oracle reads owners and the account's home through `Path.lstat` and `pwd.getpwuid`; here every such read goes
 * through a `Host` (`src/io/host.ts`). Line references are to `connectome_deploy.py`.
 */
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import { isDirectory, isLink, isRegular, permissions, realHost } from './io/host.js';
import type { Account, Host, PathInfo } from './io/host.js';
import * as inputs from './inputs.js';
import { LockError, read_lock } from './lock.js';
import { SafeError, adapter_diagnostic, diagnostic } from './errors.js';
import type { Diagnostic } from './errors.js';
import {
  dependency_inputs, dependency_provenance, framework_provenance, provenance_record, records_metadata, validate_provenance,
} from './archive.js';
import { compare } from './pylike/json.js';
import { comparePaths } from './pylike/path.js';
import * as pathio from './pylike/pathio.js';
import * as pathlib from './pylike/pathlib.js';
import {
  PyAttributeError, PyKeyError, PyOSError, PyTypeError, PyValueError,
  dictGet, eq, isDict, isList, isStr, item, iterate, strIn, subscript, truthy, typeName,
} from './pylike/py.js';
import type { PyDict, PyValue } from './pylike/py.js';
import { canonical, dict } from './pylike/pydump.js';
import { loads } from './pylike/pyjson.js';
import * as subprocess from './pylike/subprocess.js';
import { splitlines, strip } from './pylike/text.js';
import { contains, space } from './pylike/unicode.js';

export { canonical };

const sha256 = (content: Uint8Array | string): string => createHash('sha256').update(content).digest('hex');
const EMPTY: PyDict = new Map();
/** `value.items()` / `value.values()` / `value.keys()`: `AttributeError` on anything that is not a dict. */
function mapping(value: PyValue): PyDict {
  if (!isDict(value)) throw new PyAttributeError("'" + typeName(value) + "' object has no attribute 'items'");
  return value;
}
/** `list == list` / `x in list` on values the model compares structurally. */
const within = (needle: PyValue, list: PyValue): boolean => iterate(list).some(entry => eq(entry, needle));
/** `[dict(entry) for entry in records]`. */
const copies = (records: PyValue): PyValue[] => iterate(records).map(entry => new Map(mapping(entry)));

/** The error `core` a refusal makes public (contract 2.4): the closed diagnostic and the optional closed keys. */
export interface PublicError extends Diagnostic { unit?: string; scope?: string; ancestor_path?: string; external_server?: string; }

/** deploy.py:31-32 */
export class DeployError extends SafeError {
  override get pyName(): string { return 'DeployError'; }
  override public(): PublicError { return super.public(); }
}

/** Closed tool identity and numeric status only; never command output. deploy.py:35-45 */
export class UnitVerificationFailed extends DeployError {
  declare returncode: number | null;
  constructor(returncode: number | null = null) {
    super('systemd-analyze ' + (returncode === null ? 'unavailable' : 'exited ' + returncode), 'E_UNIT_VERIFY');
    this.returncode = returncode;
  }
  override get pyName(): string { return 'UnitVerificationFailed'; }
  override public(): PublicError {
    const result = super.public();
    result.msg += ' (' + this.message + ')';
    return result;
  }
}

/** Activation context built only from fixed steps and sanitized causes. deploy.py:48-58 */
export class ActivationFailed extends DeployError {
  constructor(step: string, cause: SafeError, restored: boolean) {
    super('activation failed at ' + step + ': ' + cause.message + '; ' + (restored ? 'previous deployment restored' : 'rollback recovery also failed'),
      restored ? cause.code : 'E_DEPLOY_FAILED');
  }
  override get pyName(): string { return 'ActivationFailed'; }
  override public(): PublicError {
    const result = super.public();
    result.msg += ' (' + this.message + ')';
    return result;
  }
}

/** Unit names and scopes have passed the closed input/unit-name schema. deploy.py:61-70 */
export class UnitActive extends DeployError {
  declare unit: string; declare scope: string;
  constructor(unit: string, scope = 'user', code = 'E_UNIT_ACTIVE') {
    super('unit is active: ' + unit, code);
    this.unit = unit; this.scope = scope;
  }
  override get pyName(): string { return 'UnitActive'; }
  override public(): PublicError {
    const result: PublicError = { ...super.public(), unit: this.unit, scope: this.scope };
    result.msg += ' (' + this.scope + ': ' + this.unit + ')';
    return result;
  }
}

/** Only a locally inspected directory path, never tool output, is public. deploy.py:73-80 */
export class AncestorWritable extends DeployError {
  declare path: string;
  constructor(path: string) {
    super('group/other-writable ancestor: ' + path, 'E_ANCESTOR_WRITABLE');
    this.path = path;
  }
  override get pyName(): string { return 'AncestorWritable'; }
  override public(): PublicError { return { ...super.public(), ancestor_path: this.path }; }
}

/** deploy.py:83-90 */
export function check_ancestors(path: string, host: Host = realHost): void {
  for (const parent of pathio.parents(path).reverse()) {
    let info: PathInfo;
    try { info = host.lstat(parent); } catch (error) {
      if (error instanceof PyOSError && error.errno === 'ENOENT') continue;
      throw error;
    }
    if (isDirectory(info) && (info.mode & 0o022)) throw new AncestorWritable(parent);
  }
}

/** Only the lock-validated server name is public, never the path or reason. deploy.py:93-100 */
export class ExternalCommandUnsafe extends DeployError {
  declare server: PyValue;
  constructor(name: PyValue, reason: string, code = 'E_EXTERNAL_COMMAND') {
    super('unsafe external server path: ' + reason, code);
    this.server = name;
  }
  override get pyName(): string { return 'ExternalCommandUnsafe'; }
  override public(): PublicError { return { ...super.public(), external_server: this.server as string }; }
}

/** Cook's external-server rule: a flag's =value, or a bare arg naming a file. deploy.py:103-110 */
export function path_like_arg(arg: string): string | null {
  const cut = arg.indexOf('=');
  const value = arg.startsWith('-') ? (cut < 0 ? '' : arg.slice(cut + 1)) : arg;
  if (!value || /^[a-z][a-z0-9+.-]*:\/\//iu.test(value)) return null;
  if (value.includes('/') || value.startsWith('.') || value.startsWith('~') || /\.(?:[cm]?js|ts|py)\n?$/u.test(value)) return value;
  return null;
}

/**
 * Resolve like realpath, but lstat every visited component and link. deploy.py:113-177
 *
 * The path, every link in its chain, every directory traversed and the final target must be root-owned; directories
 * and the target must not be group/other-writable. The agent account must be able to read and traverse every
 * directory and read and execute the command; a path argument may be a readable file or a readable, traversable
 * directory.
 */
export function check_external_command(name: PyValue, command: string, arg = false, account: Account | null = null, host: Host = realHost): string {
  const { uid, groups } = account ?? host.account();
  const allowed = (info: PathInfo, bits: number): boolean => {
    const shift = info.uid === uid ? 6 : groups.has(info.gid) ? 3 : 0;
    return ((info.mode >> shift) & bits) === bits;
  };
  const inspect = (path: string, last: boolean): PathInfo => {
    let info: PathInfo;
    try { info = host.lstat(path); } catch (error) {
      if (error instanceof PyOSError) throw new ExternalCommandUnsafe(name, 'missing or unreadable path');
      throw error;
    }
    if (info.uid !== 0) throw new ExternalCommandUnsafe(name, 'not root-owned');
    if (isLink(info)) return info;
    const directory = isDirectory(info);
    if (!(last ? isRegular(info) || (arg && directory) : directory)) throw new ExternalCommandUnsafe(name, last ? 'not a regular file' : 'not a directory');
    if (info.mode & 0o022) throw new ExternalCommandUnsafe(name, 'group/other-writable');
    if (!allowed(info, directory || !arg ? 0o5 : 0o4)) throw new ExternalCommandUnsafe(name, 'not readable/executable by the agent account', 'E_EXTERNAL_ACCESS');
    return info;
  };
  let current = '/';
  inspect(current, false);
  let pending = pathlib.pathParts(command).slice(1);
  let links = 0;
  while (pending.length) {
    const part = pending.shift()!;
    if (part === '' || part === '.') continue;
    if (part === '..') {
      // current is always a fully inspected real directory.
      current = pathlib.parent(current);
      continue;
    }
    const candidate = pathlib.joinPath(current, part);
    const info = inspect(candidate, !pending.length);
    if (isLink(info)) {
      links += 1;
      if (links > 40) throw new ExternalCommandUnsafe(name, 'too many symbolic links');
      const target = pathlib.pathStr(pathio.readlink(candidate));
      const absolute = pathlib.isAbsolute(target);
      if (absolute) current = '/';
      pending = [...(absolute ? pathlib.pathParts(target).slice(1) : pathlib.pathParts(target)), ...pending];
      if (!pending.length) throw new ExternalCommandUnsafe(name, 'not a regular file');
      continue;
    }
    current = candidate;
  }
  if (current === '/') throw new ExternalCommandUnsafe(name, 'not a regular file');
  return current;
}

/** Private argv data only; never read content, follow links or repair modes. deploy.py:180-214 */
export function check_external_data_arg(name: PyValue, value: PyValue, account: Account | null = null, home: string | null = null, kind: PyValue = 'file', host: Host = realHost): string {
  const unsafe = (): never => { throw new ExternalCommandUnsafe(name, 'invalid private data argument', 'E_EXTERNAL_DATA_ARG'); };
  if (!inputs.external_data_path(value) || !(kind === 'file' || kind === 'dir')) unsafe();
  const path = pathlib.pathStr(value as string);
  const { uid, groups } = account ?? host.account();
  try {
    const info = host.lstat(path);
    if (!(kind === 'file' ? isRegular(info) : isDirectory(info))) unsafe();
    // Root-maintained paths never use the private-data exception.
    if (info.uid === 0) return check_external_command(name, value as string, true, account, host);
    const base = pathlib.pathStr(home !== null ? home : host.home(uid));
    if (!pathlib.isAbsolute(base) || !pathlib.isRelativeTo(path, base) || path === base || info.uid !== uid
      || permissions(info) !== (kind === 'file' ? 0o600 : 0o700)) unsafe();
    for (const parent of pathio.parents(path).reverse()) {
      const ancestor = host.lstat(parent);
      const owners = pathlib.isRelativeTo(parent, base) ? [0, uid] : [0];
      if (!isDirectory(ancestor) || !owners.includes(ancestor.uid) || (ancestor.mode & 0o022)) unsafe();
      const shift = ancestor.uid === uid ? 6 : groups.has(ancestor.gid) ? 3 : 0;
      if (((ancestor.mode >> shift) & 0o5) !== 0o5) throw new ExternalCommandUnsafe(name, 'ancestor denies agent access', 'E_EXTERNAL_ACCESS');
    }
    // Owner read/traverse is guaranteed by the exact 0600/0700 modes above.
    return path;
  } catch (error) {
    if (error instanceof PyOSError || error instanceof PyKeyError) return unsafe();
    throw error;
  }
}

/** `(name, path, is_arg, private_data)` of deploy.py:217. */
export type ExternalPath = [name: PyValue, path: string, arg: boolean, private_data: boolean];

/** (name, path, is_arg, private_data) with exact invocation and positional/flag binding. deploy.py:217-291 */
export function external_paths(lock: PyDict, recipe: PyValue, data_args: PyValue = [], host: Host = realHost): ExternalPath[] {
  try {
    inputs.validate_external_data_args([...iterate(data_args)]);
  } catch (error) {
    if (error instanceof inputs.ExternalDataArgError) throw new DeployError('invalid private data declaration', 'E_EXTERNAL_DATA_ARG');
    throw error;
  }
  const declared = iterate(data_args) as PyDict[];
  const servers = isDict(recipe) ? dictGet(recipe, 'mcpServers') : null;
  const external = iterate(dictGet(lock, 'external', [])).map(record => subscript(record, 'name'));
  const argsOf = (server: PyDict): PyValue => dictGet(server, 'args', []);
  const sourced = (server: PyValue): server is PyDict => isDict(server) && isDict(dictGet(server, 'source')) && strIn('external', dictGet(server, 'source'));
  for (const entry of declared) {
    const server = isDict(servers) ? dictGet(servers, item(entry, 'server') as string) : null;
    if (!(within(item(entry, 'server'), external) && sourced(server)
      && eq(dictGet(server, 'command'), item(entry, 'command'))
      && eq(argsOf(server), item(entry, 'args'))
      && !eq(item(entry, 'path'), dictGet(server, 'command'))
      && (within(item(entry, 'path'), argsOf(server))
        || (entry.has('flag') && within((item(entry, 'flag') as string) + '=' + (item(entry, 'path') as string), argsOf(server)))))) {
      throw new ExternalCommandUnsafe(item(entry, 'server'), 'unbound data argument', 'E_EXTERNAL_DATA_ARG');
    }
  }
  const exemption = (server: PyValue, path: string): PyDict | undefined => {
    let found: PyDict | undefined;
    for (const entry of declared) if (eq(item(entry, 'server'), server) && item(entry, 'path') === path) found = entry;
    return found;
  };
  const result: ExternalPath[] = [];
  for (const record of iterate(dictGet(lock, 'external', []))) {
    const name = subscript(record, 'name'), command = subscript(record, 'command');
    const server = isDict(servers) ? dictGet(servers, name as string) : null;
    if (!(sourced(server) && eq(dictGet(server, 'command'), command) && isList(argsOf(server)) && (argsOf(server) as PyValue[]).every(isStr))) {
      throw new DeployError('external server differs from the installed recipe', 'E_LOCK_INVALID');
    }
    const args = argsOf(server) as string[];
    result.push([name, command as string, false, false]);
    if (declared.some(entry => eq(item(entry, 'server'), name))) {
      // Only these resolved interpreter targets consume args[0] as a script. Check the lstat-walk's real target, not a symlink alias.
      try {
        const interpreter = pathlib.name(check_external_command(name, command as string, false, null, host));
        if (!['node', 'python3', 'bun'].includes(interpreter) && !/^python3\.[0-9]+$/u.test(interpreter)) throw new PyValueError();
      } catch (error) {
        if (error instanceof PyValueError) throw new ExternalCommandUnsafe(name, 'invalid private data interpreter', 'E_EXTERNAL_DATA_ARG');
        throw error;
      }
      // args[0] is immediately after the interpreter: no interpreter options (not even --) or option values can
      // precede this script. It is non-executable data for the pinned interpreter.
      const script = args.length ? args[0]! : '';
      try {
        if (!script.startsWith('/')) throw new PyValueError();
        const checked = check_external_command(name, script, true, null, host);
        const info = host.lstat(checked);
        if (!isRegular(info) || (info.mode & 0o111)) throw new PyValueError();
      } catch (error) {
        if (error instanceof PyValueError || error instanceof PyOSError) throw new ExternalCommandUnsafe(name, 'invalid first-argument script', 'E_EXTERNAL_DATA_ARG');
        throw error;
      }
    }
    args.forEach((raw, index) => {
      const value = path_like_arg(raw);
      if (value === null) return;
      if (!value.startsWith('/')) throw new ExternalCommandUnsafe(name, 'relative path argument');
      const entry = exemption(name, value);
      const flag = entry ? dictGet(entry, 'flag') as string | null : null;
      const private_data = entry !== undefined && (raw === value || (flag !== null && raw === flag + '=' + value));
      if (private_data) {
        const bound = flag
          ? raw === flag + '=' + value || (index > 0 && args[index - 1] === flag)
          : index > 0 && !args[index - 1]!.startsWith('-');
        if (index < 1 || !bound) throw new ExternalCommandUnsafe(name, 'unbound data position or flag', 'E_EXTERNAL_DATA_ARG');
      }
      result.push([name, value, true, private_data]);
    });
  }
  return result;
}

/** Set exact modes only on directories this call creates, never repair. deploy.py:294-306 */
export function make_directory(path: string, mode = 0o700): void {
  path = pathlib.pathStr(path);
  if (!pathio.exists(pathlib.parent(path))) make_directory(pathlib.parent(path), 0o755);
  try {
    pathio.mkdir(path, mode);
  } catch (error) {
    if (!(error instanceof PyOSError && error.errno === 'EEXIST')) throw error;
    if (!pathio.isDir(path) || pathlib.isSymlink(path)) throw new DeployError('refusing non-directory or symlinked directory');
    return;
  }
  // mkdir's mode is masked by umask; chmod only our new directory.
  pathio.chmod(path, mode);
}

/** deploy.py:313-324 */
export function atomic_write(path: string, content: string | Uint8Array, mode = 0o600): void {
  path = pathlib.pathStr(path);
  make_directory(pathlib.parent(path));
  const temporary = pathio.mkstemp(pathlib.parent(path), '.write-');
  let fd: number | null = temporary.fd;
  try {
    fs.fchmodSync(fd, mode);
    fs.writeFileSync(fd, content);
    fs.closeSync(fd); fd = null;
    pathio.replace(temporary.path, path);
  } finally {
    if (fd !== null) fs.closeSync(fd);
    if (pathio.osPathExists(temporary.path)) pathio.unlink(temporary.path);
  }
}

/** deploy.py:327-338 */
export function link(path: string, target: string): void {
  path = pathlib.pathStr(path);
  make_directory(pathlib.parent(path));
  const temporary = pathio.mkstemp(pathlib.parent(path), '.link-');
  fs.closeSync(temporary.fd);
  try {
    pathio.unlink(temporary.path);
    pathio.symlink(temporary.path, target);
    pathio.replace(temporary.path, path);
  } finally {
    pathio.unlinkMissingOk(temporary.path);
  }
}

/** deploy.py:341-344 */
export function identifier(value: PyValue): string {
  if (!isStr(value) || !/^[a-z0-9][a-z0-9-]{0,62}$/u.test(value)) throw new DeployError('invalid agent or connector identifier');
  return value;
}

/** `catalog.active(config)` (catalog.py:30-31). The catalog module is not ported; this is its one predicate. */
export function catalog_active(config: PyValue): boolean {
  return !strIn('recipe_file', config) && truthy(dictGet(config, 'catalog'));
}

/** deploy.py:347-362 */
export function validate_config(config: PyValue): void {
  try {
    inputs.validate(config);
  } catch (error) {
    if (error instanceof inputs.ExternalDataArgError) throw new DeployError(error.message, 'E_EXTERNAL_DATA_ARG');
    if (error instanceof PyValueError) throw new DeployError(error.message);
    throw error;
  }
  // NOT PORTED: catalog mode. The oracle goes on into connectome_catalog here; this engine has no such module yet.
  if (catalog_active(config)) throw new DeployError('catalog deployments are not ported to this engine');
  identifier(dictGet(config, 'name', 'example'));
  for (const name of iterate(dictGet(config, 'connectors', EMPTY))) {
    identifier(name);
    if (!isStr(name) || !/^[a-z0-9][a-z0-9_-]{0,62}$/u.test(name)) throw new DeployError('invalid connector identifier');
  }
  if (strIn('host_dependencies', dictGet(config, 'cook', EMPTY))) {
    // Shape/duplicate preflight only; archive IO remains in the plan/adapter.
    dependency_inputs(config, '1'.repeat(40));
  }
}

/** `Path(value).expanduser()`: `~` and `~/...` only; another account's home is not looked up. */
function expanduser(value: string, host: Host): string {
  const path = pathlib.pathStr(value);
  if (!path.startsWith('~')) return path;
  if (path !== '~' && !path.startsWith('~/')) throw new DeployError('home of another account is not expanded');
  const home = process.env['HOME'] ?? host.home(host.account().uid);
  return pathlib.pathStr(home + path.slice(1));
}

/** deploy.py:365-370 */
export function base_path(value: string, host: Host = realHost): string {
  // Do not silently trust a redirected base (including symlinked ancestors).
  const path = pathlib.pathStr(pathlib.abspath(expanduser(value, host)));
  if (pathlib.resolve(path) !== path) throw new DeployError('symlinked base directory is unsupported');
  return path;
}

/** deploy.py:373-377 */
export function inside(root: string, value: string): string {
  const path = pathlib.joinPath(root, value);
  if (!pathlib.isRelativeTo(pathlib.resolve(path), pathlib.resolve(root))) throw new DeployError('artifact path escapes base');
  return path;
}

export interface RunOptions { cwd?: string | null; adapter?: boolean; unit_verify?: boolean; env?: Readonly<Record<string, string>> | null; }
export type Run = (argv: readonly string[], options?: RunOptions) => void;

/** deploy.py:380-398 */
export function run(argv: readonly string[], { cwd = null, adapter = false, unit_verify = false, env = null }: RunOptions = {}): void {
  // Only prepare may accept the adapter's closed JSON envelope. Systemctl and health output cannot impersonate adapter diagnostics.
  let result: subprocess.Completed;
  try {
    result = subprocess.run(argv, { cwd, env, stdout: 'ignore', stderr: adapter ? 'pipe' : 'ignore' });
  } catch (error) {
    if (!(error instanceof PyOSError)) throw error;
    if (unit_verify) throw new UnitVerificationFailed();
    throw new DeployError('command unavailable', adapter ? 'E_COOK_FAILED' : 'E_DEPLOY_FAILED');
  }
  if (result.returncode) {
    if (unit_verify) throw new UnitVerificationFailed(result.returncode);
    if (adapter) {
      const safe = adapter_diagnostic(result.stderr.subarray(0, 65537));
      throw new DeployError(safe.msg, safe.error_code, safe.cook_error_code ?? null);
    }
    throw new DeployError('command failed (output redacted)');
  }
}

const SECRET_NAME = /^[A-Z_][A-Z0-9_]*$/u;
const sortedNames = (names: Iterable<string>): string[] => [...new Set(names)].sort(compare);

/** deploy.py:401-412 */
export function direct_secret_names(config: PyValue): string[] {
  const entries = [...mapping(dictGet(config, 'connectors', EMPTY)).values(), ...mapping(dictGet(config, 'modules', EMPTY)).values()];
  const names: PyValue[] = [...iterate(dictGet(config, 'required_secrets', []))];
  for (const entry of entries) names.push(...iterate(dictGet(entry, 'required_secrets', [])));
  if (names.some(name => !isStr(name) || !SECRET_NAME.test(name))) throw new DeployError('invalid secret name');
  if (names.includes('DATA_DIR') || names.includes('WORKSPACE_DIR')) throw new DeployError('secret names overlap runtime state environment');
  return sortedNames(names as string[]);
}

/** deploy.py:415-420 */
export function secret_names(config: PyValue): string[] {
  const direct = direct_secret_names(config);
  const records = iterate(dictGet(config, 'secret_files', []));
  if (records.some(record => direct.includes(subscript(record, 'env') as string))) throw new DeployError('secret file environment name conflicts with direct secret delivery');
  return sortedNames([...direct, ...records.map(record => subscript(record, 'secret') as string)]);
}

/** deploy.py:423-429 */
export function environment_names(config: PyValue): string[] {
  if (truthy(dictGet(config, 'secret_files'))) secret_names(config); // Also reject direct/file destination collisions in standalone adapters.
  return sortedNames([...direct_secret_names(config), ...iterate(dictGet(config, 'secret_files', [])).map(record => subscript(record, 'env') as string)]);
}

/** deploy.py:432-450 */
export function resolve_pins(config: PyValue, versions: PyValue): PyDict {
  const runtime = subscript(versions, 'runtime');
  const connectors: PyDict = new Map();
  const result: PyDict = dict({ runtime, connectors });
  for (const [name, entry] of strIn('recipe_file', config) ? EMPTY : mapping(dictGet(config, 'connectors', EMPTY))) {
    let value = versions;
    try {
      const reference = subscript(entry, 'pin_ref');
      if (!isStr(reference)) throw new PyAttributeError("object has no attribute 'split'");
      for (const part of reference.split('.')) value = subscript(value, part);
    } catch (error) {
      if (error instanceof PyKeyError || error instanceof PyTypeError) throw new DeployError('unknown connector pin reference');
      throw error;
    }
    connectors.set(name, value);
  }
  const pins = [...['connectome-host', 'agent-framework', 'cook'].map(key => subscript(runtime, key)), ...connectors.values()];
  if (pins.some(pin => !isStr(pin) || !/^[a-f0-9]{40}$/u.test(pin) || pin === '0'.repeat(40))) throw new DeployError('unresolved or invalid full commit pin');
  return result;
}

/** `str.format_map` for the cook command: `{name}` fields and doubled braces; any other field is refused. */
function format_map(template: string, substitutions: Readonly<Record<string, string>>): string {
  return template.replace(/\{\{|\}\}|\{([^{}]*)\}|[{}]/gu, (match, name: string | undefined) => {
    if (match === '{{') return '{';
    if (match === '}}') return '}';
    if (name === undefined) throw new PyValueError('Single brace encountered in format string');
    if (!Object.hasOwn(substitutions, name)) throw new PyKeyError(name);
    return substitutions[name]!;
  });
}

const canonicalPath = (value: PyValue): value is string =>
  // eslint-disable-next-line no-control-regex
  isStr(value) && pathlib.isAbsolute(value) && value === pathlib.abspath(value) && !/[\u0000-\u001f\u007f]/u.test(value);
const whitespace = (char: string): boolean => contains(space, char);
/** `str.split()`. */
function words(text: string): string[] {
  const result: string[] = [];
  let word = '';
  for (const char of text) {
    if (!whitespace(char)) { word += char; continue; }
    if (word) result.push(word);
    word = '';
  }
  if (word) result.push(word);
  return result;
}
/** `shutil.which(name)` for a bare command name. */
function which(name: string): string | null {
  for (const directory of (process.env['PATH'] ?? '/bin:/usr/bin').split(':')) {
    const candidate = (directory || '.') + '/' + name;
    try { if (fs.statSync(candidate).isFile() && pathio.executable(candidate)) return candidate; } catch { /* next */ }
  }
  return null;
}
const ASCII_SPACE = /^[ \t\n\r\v\f]+|[ \t\n\r\v\f]+$/gu;

/** A planned file or link: its ownership record and, for a file, its text. */
export type Artifact = [record: PyDict, content: string | null];
export type Artifacts = Map<string, Artifact>;
type ActivationPlan = [lock: PyDict, units: Map<string, string>, previous: string | null, previous_lock: PyDict | null, previous_units: Map<string, string>, artifacts: Artifacts];

export interface DeploymentOptions {
  root: string; unit_dir: string; config: PyValue; versions: PyValue;
  /** The adapter argv with `{recipe}`, `{build_dir}` and `{state_dir}` fields. It is part of the request identity. */
  cook_command: PyValue;
  systemctl?: PyValue; check?: boolean; base?: string | null; operator_files?: PyValue; analyze_command?: readonly string[] | null;
  /** Who owns a path and where the account's home is. */
  host?: Host;
  /** `connectome_deploy.run`, replaceable as the oracle's tests replace it. */
  run?: Run;
}

/** deploy.py:453-1301 */
export class Deployment {
  readonly host: Host; readonly runner: Run;
  readonly base: string; readonly root: string; readonly unit_dir: string;
  readonly config: PyDict; readonly activation: PyValue; readonly operator_files: PyValue; readonly pins: PyDict;
  readonly cook_command: PyValue; readonly systemctl: string[]; readonly analyze_command: readonly string[] | null; readonly check: boolean;
  rendered_units = new Map<string, string>();
  readonly names: string[]; readonly spec: PyDict; readonly request_id: string;
  readonly builds: string; readonly build: string; readonly current: string; readonly state: string;
  readonly state_dir: string; readonly secrets_file: string; readonly manifest_path: string;
  owned = new Map<string, PyDict>();

  constructor({ root, unit_dir, config, versions, cook_command, systemctl = null, check = false, base = null, operator_files = null, analyze_command = null, host = realHost, run: runner = run }: DeploymentOptions) {
    // This constructor must stay mutation-free: the caller creates the root/serialization lock only AFTER all identifiers are validated.
    this.host = host; this.runner = runner;
    validate_config(config);
    const settings = config as PyDict;
    this.base = base_path(base !== null ? base : root, host);
    this.root = inside(this.base, pathlib.pathStr(pathlib.abspath(expanduser(root, host))));
    this.unit_dir = base_path(unit_dir, host);
    this.config = settings;
    this.activation = dictGet(settings, 'activation', 'start');
    this.operator_files = operator_files;
    this.pins = resolve_pins(settings, versions);
    this.cook_command = cook_command;
    this.systemctl = truthy(systemctl) ? iterate(systemctl) as string[] : ['systemctl', '--user'];
    // None discovers the host verifier; an explicit argv is injectable like cook/systemctl. An empty argv models an unavailable optional verifier.
    this.analyze_command = analyze_command;
    this.check = check;
    this.names = secret_names(settings);
    // Runtime activation policy is not cook input or release identity. An explicit start/empty list must also match legacy request/lock bytes.
    const build_config: PyDict = new Map([...settings].filter(([key]) => key !== 'activation' && key !== 'exclusive_with'));
    this.spec = dict({ config: build_config, pins: this.pins });
    // Deployment request identity differs from cook's emitted build identity.
    this.request_id = sha256(canonical(dict({ spec: this.spec, cook: cook_command })));
    this.builds = inside(this.root, 'builds');
    this.build = inside(this.builds, this.request_id);
    this.current = inside(this.root, 'current');
    // Deployment bookkeeping is not agent/runtime state.
    this.state = inside(this.root, 'state');
    let value = dictGet(settings, 'state_dir');
    if (!canonicalPath(value)) throw new DeployError('state_dir must be a canonical absolute path outside the deployment root');
    this.state_dir = pathlib.pathStr(pathlib.abspath(value));
    const home = pathlib.pathStr(host.home(process.geteuid!()));
    const name = dictGet(settings, 'name', 'example');
    if (!isStr(name)) throw new PyTypeError('can only concatenate str');
    value = dictGet(settings, 'secrets_file', pathlib.joinPath(pathlib.joinPath(home, '.config/connectome-secrets'), name + '.json'));
    if (!canonicalPath(value)) throw new DeployError('secrets_file must be a canonical absolute path');
    this.secrets_file = pathlib.pathStr(pathlib.abspath(value));
    this._path_preflight();
    this._validate_state();
    this.manifest_path = pathlib.joinPath(this.state, 'ownership.json');
    this.lock_path();
  }

  /** deploy.py:505-511 */
  _path_preflight(): void {
    for (const path of [this.root, this.state_dir, this.secrets_file, this.unit_dir]) {
      Deployment._systemd_path(path);
      check_ancestors(path, this.host);
    }
    // Root and unit_dir become parents of installed files too.
    for (const path of [this.root, this.unit_dir]) check_ancestors(pathlib.joinPath(path, '.path-check'), this.host);
  }

  /** deploy.py:513-528 */
  validate_paths(): void {
    this._path_preflight();
    validate_config(this.config);
    base_path(this.base, this.host);
    base_path(this.unit_dir, this.host);
    base_path(this.state, this.host);
    inside(this.base, this.root);
    for (const path of [this.builds, this.build, this.current, this.state]) inside(this.root, path);
    // Containment alone permits aliases within the tree. Check every build and state parent before mkdir, recursive cleanup or any other write.
    for (const path of [this.root, this.builds, this.build, this.state]) {
      base_path(path, this.host);
      this._safe_parent(pathlib.joinPath(path, '.path-check'));
    }
    this._validate_state();
    this.lock_path();
  }

  /** deploy.py:530-543 */
  _validate_state(): void {
    // Reject both lexical and resolved overlaps, including links into builds.
    for (const path of [this.state_dir, pathlib.resolve(this.state_dir)]) {
      if (pathlib.isRelativeTo(path, this.root) || pathlib.isRelativeTo(this.root, path)) {
        throw new DeployError('state_dir must be outside and separate from the deployment root/builds');
      }
    }
    if (pathlib.isRelativeTo(this.unit_dir, this.state_dir) || pathlib.isRelativeTo(this.state_dir, this.unit_dir)) {
      throw new DeployError('state_dir must be separate from unit_dir');
    }
    base_path(this.state_dir, this.host);
    this._safe_parent(pathlib.joinPath(this.state_dir, '.path-check'));
    if (pathio.exists(this.state_dir)) {
      const info = this.host.lstat(this.state_dir);
      if (!isDirectory(info) || permissions(info) !== 0o700 || info.uid !== process.geteuid!()) {
        throw new DeployError('state_dir must be a real directory, mode 0700, owned by the agent account; not repaired');
      }
    }
  }

  /** deploy.py:545-548 */
  _ensure_state(): void {
    this._validate_state();
    make_directory(this.state_dir);
    this._validate_state();
  }

  /** deploy.py:550-562 */
  _check_secrets_file(): void {
    const path = this.secrets_file;
    let valid: boolean;
    try {
      valid = pathlib.resolve(path) === path && pathlib.resolve(pathlib.parent(path)) === pathlib.parent(path);
      const directory = this.host.lstat(pathlib.parent(path)), info = this.host.lstat(path);
      valid = valid && isDirectory(directory) && permissions(directory) === 0o700 && directory.uid === process.geteuid!()
        && isRegular(info) && permissions(info) === 0o600 && info.uid === process.geteuid!();
    } catch (error) {
      if (!(error instanceof PyOSError)) throw error;
      valid = false;
    }
    if (!valid) {
      throw new DeployError('secrets file check failed: require non-symlink directory 0700 and regular file 0600, both owned by the agent account (see plan secrets_file); not repaired', 'E_SECRETS_MODE');
    }
  }

  /** deploy.py:564-570 */
  _read_secrets(): PyDict {
    this._check_secrets_file();
    try {
      const values = loads(pathlib.readText(this.secrets_file));
      return new Map(this.names.map(name => [name, subscript(values, name)]));
    } catch (error) {
      if (error instanceof PyOSError || error instanceof PyValueError || error instanceof PyKeyError || error instanceof PyTypeError) {
        throw new DeployError('agent secrets file lookup failed (values redacted)');
      }
      throw error;
    }
  }

  /** deploy.py:572-617 */
  plan(): PyDict {
    const result: PyDict = dict({ state_dir: this.state_dir, secrets_file: this.secrets_file, budgets: dictGet(this.config, 'budgets', new Map()) });
    if (this.activation === 'install_only') result.set('activation', this.activation);
    if (truthy(dictGet(this.config, 'exclusive_with'))) result.set('exclusive_with', item(this.config, 'exclusive_with'));
    if (truthy(dictGet(this.config, 'external_data_args'))) result.set('external_data_args', copies(item(this.config, 'external_data_args')));
    if (this.config.has('recipe_file')) result.set('recipe_file', new Map(mapping(item(this.config, 'recipe_file'))));
    if (truthy(dictGet(this.config, 'secret_files'))) {
      result.set('secret_files', iterate(item(this.config, 'secret_files')).map(record => {
        const copy = new Map(mapping(record));
        copy.set('path', this._secret_file_path(item(copy, 'env') as string));
        return copy;
      }));
    }
    const framework = subscript(subscript(this.pins, 'runtime'), 'agent-framework');
    const prepared = pathlib.isFile(pathlib.joinPath(this.build, '.prepared'));
    if (strIn('framework_archive', dictGet(this.config, 'cook', EMPTY))) {
      const lock = pathlib.joinPath(this.build, 'framework.lock.json');
      if (prepared) {
        result.set('framework_archive', pathlib.isFile(lock)
          ? validate_provenance(loads(pathlib.readText(lock)), this.config, framework)
          : dict({ provenance: 'not_recorded_legacy_build' }));
      } else result.set('framework_archive', framework_provenance(this.config, framework));
    }
    if (records_metadata(this.config)) {
      if (prepared) {
        const data = this._lock();
        const entries = dependency_inputs(this.config, framework);
        const recorded = iterate(dictGet(data, 'hostDependencies', []));
        result.set('host_dependencies', entries.slice(0, recorded.length).map((entry, index) => {
          const merged = new Map(entry);
          merged.set('version', subscript(recorded[index]!, 'version'));
          return provenance_record(merged);
        }));
      } else result.set('host_dependencies', dependency_provenance(this.config, framework));
    }
    return result;
  }

  /** deploy.py:619-620 */
  lock_path(): string { return inside(this.root, '.deploy-lock'); }

  /** deploy.py:622-640 */
  _exclusive_status(refuse = false): PyValue[] {
    const result: PyValue[] = [];
    for (const record of iterate(dictGet(this.config, 'exclusive_with', [])) as PyDict[]) {
      const command = this.systemctl.filter(arg => arg !== '--user' && arg !== '--system');
      const scope = item(record, 'scope') as string, unit = item(record, 'unit') as string;
      let state: subprocess.Completed | null;
      try {
        state = subprocess.run([...command, '--' + scope, 'is-active', unit], { stdout: 'pipe', stderr: 'ignore' });
      } catch (error) {
        if (!(error instanceof PyOSError)) throw error;
        state = null;
      }
      let value = state ? strip(Array.from(state.stdout, byte => (byte < 128 ? String.fromCharCode(byte) : '�')).join('')) : 'unavailable';
      if (!state || !['active', 'activating', 'reloading', 'deactivating', 'inactive', 'failed', 'unknown'].includes(value) || ![0, 3, 4].includes(state.returncode)) {
        if (refuse) throw new DeployError('cannot inspect exclusive unit');
        value = 'unavailable';
      }
      if (refuse && !['inactive', 'failed', 'unknown'].includes(value)) throw new UnitActive(unit, scope, 'E_UNIT_EXCLUSIVE');
      const row = new Map(record);
      row.set('state', value);
      result.push(row);
    }
    return result;
  }

  /** deploy.py:642-653 */
  _refuse_active(names: Iterable<string>): void {
    for (const name of sortedNames(names)) {
      this._unit_path(name);
      let result: subprocess.Completed;
      try {
        result = subprocess.run([...this.systemctl, 'show', '-p', 'ActiveState', '--value', name], { stdout: 'pipe', stderr: 'ignore' });
      } catch (error) {
        if (!(error instanceof PyOSError)) throw error;
        throw new DeployError('cannot inspect unit state');
      }
      if (result.returncode) throw new DeployError('cannot inspect unit state');
      const state = result.stdout.toString('latin1').replace(ASCII_SPACE, '');
      if (state !== 'inactive' && state !== 'failed') throw new UnitActive(name);
    }
  }

  /** Read-only guard, including before the caller creates root/lock files. deploy.py:655-668 */
  preflight_activation(): void {
    if (this.check) return;
    if (this.activation === 'start') {
      this._exclusive_status(true);
      return;
    }
    const names = new Set(['connectome-' + (dictGet(this.config, 'name', 'example') as string) + '.service']);
    const builds = new Set<string>(pathlib.isSymlink(this.current) ? [pathlib.resolve(this.current)] : []);
    if (pathlib.isFile(pathlib.joinPath(this.build, '.prepared'))) builds.add(this.build);
    for (const build of builds) for (const name of this._units(build, this._lock(build)).keys()) names.add(name);
    this._refuse_active(names);
  }

  /** deploy.py:670-676 */
  _staged(): boolean {
    const path = pathlib.joinPath(this.state, 'activation');
    this._safe_parent(path);
    if (pathlib.isSymlink(path) || (pathio.exists(path) && (!pathlib.isFile(path) || pathlib.readText(path) !== 'install_only\n'))) {
      throw new DeployError('invalid activation bookkeeping');
    }
    return pathio.exists(path);
  }

  /** deploy.py:678-685 */
  _status(result: PyDict): PyDict {
    if (truthy(dictGet(this.config, 'external_data_args'))) result.set('external_data_args', copies(item(this.config, 'external_data_args')));
    if (this.activation === 'install_only' && !this.check) {
      result.set('activation', 'install_only'); result.set('unit_state', 'inactive');
      if (truthy(dictGet(this.config, 'exclusive_with'))) result.set('exclusive_with', this._exclusive_status());
    }
    return result;
  }

  /** `json.loads((build / 'deployment.json').read_text())`. */
  private recorded(build: string): PyValue { return loads(pathlib.readText(pathlib.joinPath(build, 'deployment.json'))); }

  /** deploy.py:687-747 */
  _lock(build: string | null = null): PyDict {
    build = build ?? this.build;
    inside(this.builds, build);
    const own = build === this.build;
    let config: PyDict = own ? this.config : this._config(build);
    let pin = own ? subscript(subscript(this.pins, 'runtime'), 'agent-framework')
      : subscript(subscript(subscript(this.recorded(build), 'pins'), 'runtime'), 'agent-framework');
    const framework_lock = pathlib.joinPath(build, 'framework.lock.json');
    const archived = (): boolean => strIn('framework_archive', dictGet(config, 'cook', EMPTY));
    let recorded: PyValue = null;
    if (archived() && pathio.exists(framework_lock)) {
      try {
        recorded = validate_provenance(loads(pathlib.readText(framework_lock)), config, pin);
      } catch (error) {
        if (error instanceof PyOSError || error instanceof PyValueError) throw new DeployError('invalid framework provenance', 'E_LOCK_INVALID');
        throw error;
      }
    }
    let data: PyDict;
    try {
      data = read_lock(inside(build, 'connectome.lock'), {
        config, framework_pin: pin, framework_record: recorded,
        host_pin: own ? subscript(subscript(this.pins, 'runtime'), 'connectome-host') : null,
        required_names: own ? environment_names(config) : [],
      });
    } catch (error) {
      if (error instanceof LockError) throw new DeployError(error.message, 'E_LOCK_INVALID');
      throw error;
    }
    const state = dictGet(data, 'state');
    if (!truthy(state)) throw new DeployError('cook lock must declare configured external state_dir', 'E_LOCK_STATE_PATH');
    const recorded_state = subscript(state, 'path');
    if (!isStr(recorded_state)) throw new PyTypeError('expected str, bytes or os.PathLike object');
    const path = pathlib.pathStr(recorded_state);
    const expected = own ? this.state_dir : pathlib.pathStr(item(this._config(build), 'state_dir') as string);
    if (!pathlib.isAbsolute(path) || recorded_state !== expected) throw new DeployError('cook state does not match configured state_dir', 'E_LOCK_STATE_PATH');
    base_path(path, this.host); // Previous/rollback locks must not alias state either.
    if (pathlib.isRelativeTo(pathlib.resolve(path), pathlib.resolve(this.root))) throw new DeployError('state must be outside deployment root/builds', 'E_LOCK_STATE_PATH');
    config = own ? this.config : this._config(build);
    if (archived() && (pathio.exists(framework_lock) || dictGet(subscript(item(config, 'cook'), 'framework_archive'), 'registry_release') === true)) {
      // Legacy gitHead-only builds predate the sidecar; do not adopt the registry exception without a provenance record.
      // Use recorded request pin for retained builds, not today's pins.
      pin = subscript(subscript(subscript(this.recorded(build), 'pins'), 'runtime'), 'agent-framework');
      let sidecar: PyValue;
      try {
        sidecar = loads(pathlib.readText(framework_lock));
      } catch (error) {
        if (error instanceof PyOSError || error instanceof PyValueError) throw new DeployError('framework provenance lock missing or malformed', 'E_LOCK_INVALID');
        throw error;
      }
      const wanted = validate_provenance(sidecar, config, pin);
      if (!iterate(dictGet(data, 'hostDependencies', [])).some(dep => ['name', 'version', 'sha256'].every(key => eq(subscript(dep, key), subscript(wanted, key))))) {
        throw new DeployError('framework provenance lock mismatch', 'E_LOCK_INVALID');
      }
    }
    try {
      inputs.installed(build, config);
    } catch (error) {
      if (error instanceof PyValueError) throw new DeployError(error.message, 'E_LOCK_INVALID');
      throw error;
    }
    // Role lock view; the request's deployment.json retains this opt-in. Do not claim cook emitted it or rewrite cook's build identity.
    if (truthy(dictGet(config, 'external_data_args'))) data.set('external_data_args', copies(item(config, 'external_data_args')));
    return data;
  }

  private prepared(changed: boolean): PyDict {
    return dict({ changed, build_dir: this.build, secret_names: [...this.names], plan: this.plan() });
  }

  /** deploy.py:749-816 */
  prepare(): PyDict {
    this.validate_paths();
    this.preflight_activation();
    this._check_secrets_file();
    this._load_ownership();
    this._check_build_ownership();
    const supplied = this.config.has('recipe_file');
    if (!this.check && !supplied) this._ensure_state();
    const marker = inside(this.build, '.prepared');
    if (pathlib.isFile(marker) && pathlib.readText(marker) === this.request_id) {
      if (!this.check && supplied) this._ensure_state();
      this._activation_plan();
      return this.prepared(false);
    }
    let payloads: Map<string, Uint8Array>;
    try {
      payloads = inputs.payloads(this.config, this.operator_files);
    } catch (error) {
      if (error instanceof PyValueError) throw new DeployError(error.message);
      throw error;
    }
    if (this.check) return this.prepared(true);
    if (!truthy(this.cook_command)) throw new DeployError('configure a pinned cook adapter command');
    if (supplied) this._ensure_state();
    if (pathlib.isSymlink(this.current) && pathlib.resolve(this.current) === this.build) throw new DeployError('refusing to rebuild active directory');
    if ([...this.owned.keys()].some(path => path !== this.build && pathlib.isRelativeTo(path, this.build))) {
      throw new DeployError('refusing to rebuild a directory with manifest-owned unit files');
    }
    if (pathio.exists(this.build)) {
      // _check_build_ownership refused unknown directories, even if they contained a plausible .prepared marker.
      // Only our own incomplete, inactive request directory may be recursively discarded.
      pathio.rmtree(this.build);
    }
    make_directory(this.root);
    make_directory(this.builds);
    make_directory(this.build);
    make_directory(this.state);
    this.owned.set(this.build, dict({ kind: 'directory' }));
    this._save_ownership(); // persist ownership before invoking cook
    const recipe = inside(this.build, 'deployment.json');
    atomic_write(recipe, canonical(this.spec));
    if (supplied) this._write_input(pathlib.joinPath(this.build, 'cook-recipe.json'), payloads.get('cook-recipe.json')!, 0o600);
    const substitutions = { recipe, build_dir: this.build, state_dir: this.state_dir };
    this.runner((iterate(this.cook_command) as string[]).map(arg => {
      if (!isStr(arg)) throw new PyAttributeError("object has no attribute 'format_map'");
      return format_map(arg, substitutions);
    }), { cwd: this.build, adapter: true });
    // Surface safe ownership diagnostics before the secret activation step, and before creating env/current/service state.
    this._activation_plan();
    atomic_write(inside(this.build, '.prepared'), this.request_id);
    return this.prepared(true);
  }

  /** deploy.py:818-821 */
  _config(build: string): PyDict {
    const config = subscript(loads(pathlib.readText(inside(build, 'deployment.json'))), 'config');
    validate_config(config);
    return config as PyDict;
  }

  /** deploy.py:823-855 */
  _units(build: string, lock: PyDict): Map<string, string> {
    const units = dictGet(lock, 'units'); // planned extension: explicit path+digest records
    if (units === null) {
      const launch = dictGet(lock, 'launch', EMPTY);
      if (dictGet(launch, 'kind') !== 'script') throw new DeployError('host script launcher required');
      const script = pathlib.resolve(inside(build, subscript(launch, 'script') as string));
      if (!pathlib.isFile(script)) throw new DeployError('missing launcher');
      const name = identifier(dictGet(this._config(build), 'name', 'example'));
      const unit = inside(build, 'connectome-' + name + '.service');
      const body = '[Unit]\nDescription=Connectome agent\n[Service]\nType=simple\nWorkingDirectory='
        + Deployment._systemd_path(item(this._config(build), 'state_dir') as string) + '\nExecStart='
        + Deployment._systemd_path(script) + '\nRestart=on-failure\n[Install]\nWantedBy=default.target\n';
      this.rendered_units.set(unit, body);
      return new Map([[pathlib.name(unit), unit]]);
    }
    const result = new Map<string, string>();
    for (const entry of iterate(units)) {
      const path = pathlib.resolve(inside(build, subscript(entry, 'path') as string));
      const leaf = pathlib.name(path);
      if (!/^[A-Za-z0-9_.@-]+\.service$/u.test(leaf) || result.has(leaf)) throw new DeployError('invalid or duplicate user unit');
      const content = pathlib.readBytes(path);
      if (sha256(content) !== subscript(entry, 'sha256')) throw new DeployError('unit output digest mismatch');
      // Install our own copy, so every installed unit file has exact manifest ownership; cook's original output is not adopted.
      const installed = inside(build, 'managed-units/' + leaf);
      this.rendered_units.set(installed, new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(content));
      result.set(leaf, installed);
    }
    if (!result.size) throw new DeployError('no units to activate');
    return result;
  }

  /** deploy.py:857-865 */
  static _systemd_path(value: string): string {
    // EnvironmentFile/WorkingDirectory take paths, not shell-quoted words. Refuse specifiers, globs, escapes, whitespace
    // and other ambiguous input instead of attempting directive-dependent escaping.
    if (!isStr(value) || !/^\/[A-Za-z0-9_./:@+-]+$/u.test(value)) {
      throw new DeployError('systemd paths require an absolute literal path without whitespace or special characters', 'E_SYSTEMD_PATH');
    }
    return value;
  }

  /** deploy.py:867-875 */
  _write_input(path: string, content: Uint8Array, mode: number): void {
    this._safe_parent(path);
    if (pathio.exists(path) || pathlib.isSymlink(path)) throw new DeployError('operator input destination already exists');
    make_directory(pathlib.parent(path));
    // Byte mode is essential: no JSON reserialization or newline conversion.
    pathio.writeExclusive(path, content, mode);
  }

  /** deploy.py:877-881 */
  _environment_path(build: string): string { return inside(build, 'secrets.env'); }

  /** deploy.py:883-895 */
  _check_environment_file(name: string, build: string): void {
    const expected = Deployment._systemd_path(this._environment_path(build));
    let result: subprocess.Completed;
    try {
      result = subprocess.run([...this.systemctl, 'show', '-p', 'EnvironmentFiles', '--value', name], { stdout: 'pipe', stderr: 'ignore' });
    } catch (error) {
      if (!(error instanceof PyOSError)) throw error;
      throw new DeployError('cannot inspect unit environment file', 'E_ENVIRONMENT_FILE');
    }
    // systemctl show is the documented machine-readable manager view, not the on-disk text. Require this exact non-optional file in every unit.
    const accepted = new RegExp('(?:^|[ \\t\\n\\r\\v\\f])' + expected.replace(/[^A-Za-z0-9_/]/gu, '\\$&') + ' \\(ignore_errors=no\\)(?:[ \\t\\n\\r\\v\\f]|$)').test(result.stdout.toString('latin1'));
    if (result.returncode || !accepted) throw new DeployError('required unit environment file not accepted', 'E_ENVIRONMENT_FILE');
  }

  /** deploy.py:897-928 */
  _health(units: Map<string, string>, lock: PyDict | null, build: string): void {
    if (this.activation === 'install_only') {
      this._lock(build);
      if (!pathlib.isSymlink(this.current) || pathlib.resolve(this.current) !== build) throw new DeployError('current does not point at installed release');
      for (const name of units.keys()) {
        if (!pathlib.isFile(this._unit_path(name))) throw new DeployError('installed unit file missing');
        this._check_environment_file(name, build);
      }
      this._refuse_active(units.keys());
      let analyze = this.analyze_command;
      if (analyze === null) {
        const executable = which('systemd-analyze');
        analyze = executable ? [executable] : [];
      }
      if (analyze.length && units.size) this.runner([...analyze, '--user', 'verify', ...[...units.keys()].map(name => this._unit_path(name))], { unit_verify: true });
      return;
    }
    for (const name of units.keys()) {
      this.runner([...this.systemctl, 'is-active', '--quiet', name]);
      if (secret_names(this._config(build)).length) this._check_environment_file(name, build);
    }
    const health = dictGet(lock, 'health', dictGet(this._config(build), 'health', new Map()));
    const command = dictGet(health, 'command');
    if (truthy(command)) {
      if (!isList(command)) throw new DeployError('health command must be argv');
      this.runner(command as string[], { cwd: build });
    }
  }

  /** deploy.py:930-931 */
  _secret_file_path(env: string): string { return pathlib.joinPath(pathlib.joinPath(this.state, 'secret-files'), env); }

  /** deploy.py:933-939 */
  _secret_file_snapshot(previous: string | null, config: PyDict): [Map<string, Buffer>, Set<string>] {
    const old = previous ? iterate(dictGet(this._config(previous), 'secret_files', [])) : [];
    const before = old.map(record => subscript(record, 'env') as string);
    const after = iterate(dictGet(config, 'secret_files', [])).map(record => subscript(record, 'env') as string);
    if (!before.length && !after.length) return [new Map(), new Set()];
    return [this._inspect_secret_files(new Set(before)), new Set([...before, ...after])];
  }

  /** deploy.py:941-957 */
  _inspect_secret_files(allowed: ReadonlySet<string>): Map<string, Buffer> {
    const directory = pathlib.joinPath(this.state, 'secret-files');
    this._safe_parent(pathlib.joinPath(directory, '.path-check'));
    if (!pathio.exists(directory)) return new Map();
    let info = this.host.lstat(directory);
    if (!isDirectory(info) || permissions(info) !== 0o700 || info.uid !== process.geteuid!()) {
      throw new DeployError('secret file directory must be agent-owned mode 0700; not repaired');
    }
    const result = new Map<string, Buffer>();
    for (const name of pathio.listdir(directory)) {
      const path = pathlib.joinPath(directory, name);
      info = this.host.lstat(path);
      if (!allowed.has(name) || !isRegular(info) || permissions(info) !== 0o600 || info.uid !== process.geteuid!()) {
        throw new DeployError('secret file must be declared, regular and agent-owned mode 0600; not repaired');
      }
      result.set(name, pathlib.readBytes(path));
    }
    return result;
  }

  /** deploy.py:959-969 */
  _set_secret_files(content: ReadonlyMap<string, Uint8Array>, allowed: ReadonlySet<string>): void {
    const existing = this._inspect_secret_files(allowed);
    const directory = pathlib.joinPath(this.state, 'secret-files');
    if (content.size) make_directory(directory);
    for (const env of existing.keys()) if (!content.has(env)) pathio.unlink(this._secret_file_path(env));
    for (const [env, value] of content) atomic_write(this._secret_file_path(env), value);
    if (pathio.exists(directory) && !pathio.listdir(directory).length) pathio.rmdir(directory);
  }

  /** deploy.py:971-976 */
  _unit_path(name: string): string {
    if (!isStr(name) || !/^[A-Za-z0-9_.@-]+\.service$/u.test(name)) throw new DeployError('invalid user unit');
    // Unit links intentionally point outside unit_dir; contain their parents, and check the leaf's ownership separately before replacing/removing it.
    return pathlib.joinPath(this.unit_dir, name);
  }

  /** deploy.py:978-983 */
  _dropin(name: string): string {
    this._unit_path(name);
    const directory = pathlib.joinPath(this.unit_dir, name + '.d');
    if (pathlib.isSymlink(directory)) throw new DeployError('refusing symlinked drop-in directory (aliased parent or escape)');
    return pathlib.joinPath(directory, '90-connectome-env.conf');
  }

  /** deploy.py:985-991 */
  static _file_record(content: string): PyDict { return dict({ kind: 'file', sha256: sha256(content) }); }
  static _link_record(target: string): PyDict { return dict({ kind: 'link', target }); }

  /** deploy.py:993-998 */
  _safe_parent(path: string): void {
    const parent = pathlib.parent(path);
    if (pathlib.resolve(parent) !== parent) throw new DeployError('refusing symlinked artifact parent directory');
    if (pathio.parents(path).some(ancestor => pathio.exists(ancestor) && !pathio.isDir(ancestor))) throw new DeployError('refusing non-directory artifact parent');
    check_ancestors(path, this.host);
  }

  /** deploy.py:1000-1007 */
  _matches(path: string, record: PyDict): boolean {
    this._safe_parent(path);
    const kind = item(record, 'kind');
    if (kind === 'directory') return pathio.exists(path) && isDirectory(this.host.lstat(path));
    if (kind === 'link') return pathlib.isSymlink(path) && pathio.readlink(path) === item(record, 'target');
    return pathio.exists(path) && isRegular(this.host.lstat(path)) && sha256(pathlib.readBytes(path)) === item(record, 'sha256');
  }

  /** deploy.py:1009-1040 */
  _load_ownership(): void {
    this.owned = new Map();
    if (pathlib.isSymlink(this.manifest_path)) throw new DeployError('refusing symlinked ownership manifest');
    if (!pathio.exists(this.manifest_path)) return;
    if (!isRegular(this.host.lstat(this.manifest_path))) throw new DeployError('invalid ownership manifest; regular file required');
    try {
      const data = loads(pathlib.readText(this.manifest_path));
      if (!isDict(data) || data.size !== 2 || !data.has('version') || !data.has('entries')
        || !eq(item(data, 'version'), loads('1')) || !isList(item(data, 'entries'))) throw new PyValueError();
      for (const entry of item(data, 'entries') as PyValue[]) {
        if (!isDict(entry)) throw new PyValueError();
        const raw = item(entry, 'path');
        if (!isStr(raw)) throw new PyTypeError('expected str, bytes or os.PathLike object');
        const path = pathlib.pathStr(raw);
        const record: PyDict = new Map([...entry].filter(([key]) => key !== 'path'));
        const kind = record.get('kind');
        const hex = (value: PyValue | undefined): boolean => {
          if (!isStr(value)) throw new PyTypeError('expected string or bytes-like object');
          return /^[a-f0-9]{64}$/u.test(value);
        };
        const valid = (record.size === 2 && record.has('target') && kind === 'link' && isStr(record.get('target')))
          || (record.size === 2 && record.has('sha256') && kind === 'file' && hex(record.get('sha256')))
          || (record.size === 1 && kind === 'directory' && pathlib.parent(path) === this.builds && /^[a-f0-9]{64}$/u.test(pathlib.name(path)));
        if (!valid || !pathlib.isAbsolute(path) || path !== pathlib.abspath(path) || this.owned.has(path)
          || !(path === this.current || pathlib.isRelativeTo(path, this.builds) || pathlib.isRelativeTo(path, this.unit_dir))) throw new PyValueError();
        this.owned.set(path, record);
      }
    } catch (error) {
      if (error instanceof DeployError) throw error;
      if (error instanceof PyValueError || error instanceof PyTypeError || error instanceof PyKeyError) {
        throw new DeployError('invalid ownership manifest; operator recovery required');
      }
      throw error;
    }
    this._verify_ownership();
  }

  /** deploy.py:1042-1044 */
  _check_build_ownership(): void {
    const record = this.owned.get(this.build);
    if (pathio.exists(this.build) && !(record !== undefined && eq(record, dict({ kind: 'directory' })))) {
      throw new DeployError('refusing unmanaged build directory; no matching ownership manifest entry');
    }
  }

  /** deploy.py:1046-1050 */
  _missing_unit_link(path: string, record: PyDict): boolean {
    // systemctl disable removes unit/enablement symlinks. Recreate only recorded, absent unit-dir links; never adopt a replacement or a file.
    return item(record, 'kind') === 'link' && pathlib.isRelativeTo(path, this.unit_dir)
      && pathlib.name(path).endsWith('.service') && !pathio.exists(path) && !pathlib.isSymlink(path);
  }

  /** deploy.py:1052-1055 */
  _verify_ownership(): void {
    for (const [path, record] of this.owned) {
      if (!this._matches(path, record) && !this._missing_unit_link(path, record)) {
        throw new DeployError('refusing unmanaged, modified or missing artifact; ownership mismatch');
      }
    }
  }

  /** deploy.py:1057-1059 */
  _save_ownership(): void {
    const entries = [...this.owned].sort(([a], [b]) => comparePaths(a, b)).map(([path, record]) => new Map<string, PyValue>([['path', path], ...record]));
    atomic_write(this.manifest_path, canonical(dict({ version: loads('1'), entries })));
  }

  private owns(path: string, record: PyDict): boolean {
    const held = this.owned.get(path);
    return held !== undefined && eq(held, record) && this._matches(path, record);
  }

  /** deploy.py:1061-1071 */
  _put(path: string, record: PyDict, content: string | null = null): void {
    // Callers preflight the entire transaction before the first mutation.
    this._preflight(new Map([[path, [record, content]]]));
    if (this.owns(path, record)) return;
    if (item(record, 'kind') === 'link') link(path, item(record, 'target') as string);
    else {
      if (content === null) throw new PyTypeError("write() argument must be str, not None");
      atomic_write(path, content, this.rendered_units.has(path) ? 0o644 : 0o600);
    }
    this.owned.set(path, record);
    this._save_ownership();
  }

  /** deploy.py:1073-1083 */
  _remove(path: string): void {
    const record = this.owned.get(path);
    if (record === undefined) throw new DeployError('refusing to remove unmanaged or modified artifact');
    if (this._missing_unit_link(path, record)) { /* already gone */ }
    else if (this._matches(path, record)) pathio.unlink(path);
    else throw new DeployError('refusing to remove unmanaged or modified artifact');
    this.owned.delete(path);
    this._save_ownership();
  }

  /** deploy.py:1085-1121 */
  _enable_links(name: string, source: string): Artifacts {
    // Do not let `systemctl enable` implicitly replace untracked links. Support a small explicit Install subset, with no specifier expansion.
    const body = this.rendered_units.get(source) ?? pathlib.readText(source);
    let section: string | null = null;
    const settings = new Map<string, string[]>();
    const result: Artifacts = new Map();
    for (const raw of splitlines(body)) {
      const line = strip(raw);
      if (!line || line.startsWith('#') || line.startsWith(';')) continue;
      if (line.startsWith('[') && line.endsWith(']')) { section = line; continue; }
      if (section !== '[Install]') continue;
      const cut = line.indexOf('=');
      const key = strip(cut < 0 ? line : line.slice(0, cut)), value = cut < 0 ? '' : line.slice(cut + 1);
      if (cut < 0 || !['WantedBy', 'RequiredBy', 'Alias'].includes(key)) throw new DeployError('unsupported unit Install directive; use explicit target links');
      if (!strip(value)) settings.set(key, []);
      else settings.set(key, [...(settings.get(key) ?? []), ...words(value)]);
    }
    for (const [key, targets] of settings) {
      for (const target of targets) {
        if (!/^[A-Za-z0-9_@-]+\.(?:target|service)$/u.test(target)) throw new DeployError('unsupported unit Install target or alias');
        const path = key === 'Alias' ? this._unit_path(target)
          : pathlib.joinPath(pathlib.joinPath(this.unit_dir, target + (key === 'WantedBy' ? '.wants' : '.requires')), name);
        result.set(path, [Deployment._link_record(source), null]);
      }
    }
    return result;
  }

  /** deploy.py:1123-1148 */
  _artifacts(units: ReadonlyMap<string, string>, build: string): Artifacts {
    const result: Artifacts = new Map();
    const same = (a: Artifact, b: Artifact): boolean => eq(a[0], b[0]) && a[1] === b[1];
    for (const [name, source] of units) {
      const enable_links = this._enable_links(name, source);
      const additions: Artifacts = this.activation === 'start' ? enable_links : new Map();
      const rendered = this.rendered_units.get(source);
      if (rendered !== undefined) additions.set(source, [Deployment._file_record(rendered), rendered]);
      additions.set(this._unit_path(name), [Deployment._link_record(source), null]);
      const body = '[Service]\nEnvironment=DATA_DIR=' + Deployment._systemd_path(item(this._config(build), 'state_dir') as string)
        + '\nEnvironmentFile=' + Deployment._systemd_path(inside(build, 'secrets.env')) + '\n';
      additions.set(this._dropin(name), [Deployment._file_record(body), body]);
      for (const [path, artifact] of additions) {
        const present = result.get(path);
        if (present !== undefined && !same(present, artifact)) throw new DeployError('conflicting unit installation paths');
        result.set(path, artifact);
      }
    }
    return result;
  }

  /** deploy.py:1150-1156 */
  _preflight(artifacts: Artifacts): void {
    base_path(this.unit_dir, this.host);
    this._verify_ownership();
    for (const path of artifacts.keys()) {
      this._safe_parent(path);
      if (!this.owned.has(path) && (pathio.exists(path) || pathlib.isSymlink(path))) {
        throw new DeployError('refusing unmanaged artifact; no matching ownership manifest entry');
      }
    }
  }

  /** deploy.py:1158-1186 */
  _install(units: ReadonlyMap<string, string>, old_units: ReadonlyMap<string, string>, build: string): void {
    if (this.activation === 'start') this._exclusive_status(true);
    const artifacts = this._artifacts(units, build);
    this._preflight(artifacts);
    for (const name of old_units.keys()) {
      if (units.has(name)) continue;
      // Disable by deleting only our recorded enable links below; systemctl disable may delete additional, unowned aliases.
      if (this.owned.has(this._unit_path(name)) && this.activation === 'start') this.runner([...this.systemctl, 'stop', name]);
    }
    // Remove only exact manifest-owned unit-dir artifacts, including enable links. Generated unit files in old builds remain tracked and retained.
    for (const path of [...this.owned.keys()]) {
      if (pathlib.isRelativeTo(path, this.unit_dir) && !artifacts.has(path)) {
        this._remove(path);
        const parent = pathlib.parent(path);
        if (parent !== this.unit_dir && pathio.exists(parent) && !pathio.listdir(parent).length) pathio.rmdir(parent);
      }
    }
    if (artifacts.size) make_directory(this.unit_dir);
    for (const [path, [record, content]] of artifacts) this._put(path, record, content);
    this.runner([...this.systemctl, 'daemon-reload']);
    if (this.activation === 'start') {
      this._exclusive_status(true);
      for (const name of units.keys()) this.runner([...this.systemctl, 'restart', name]);
    }
  }

  /** deploy.py:1188-1215 */
  _activation_plan(): ActivationPlan {
    const lock = this._lock();
    // The legacy bridge trusts host-installed servers as root-maintained files.
    if (truthy(dictGet(lock, 'external')) || truthy(dictGet(this.config, 'external_data_args'))) {
      let recipe: PyValue;
      try {
        recipe = loads(pathlib.readText(inside(this.build, 'cook-recipe.json')));
      } catch (error) {
        if (error instanceof PyOSError || error instanceof PyValueError) throw new DeployError('external servers require the installed recipe', 'E_LOCK_INVALID');
        throw error;
      }
      const data_args = dictGet(this.config, 'external_data_args', []);
      const kind = (name: PyValue, path: string): PyValue => {
        let found: PyValue | undefined;
        for (const entry of iterate(data_args)) if (eq(subscript(entry, 'server'), name) && subscript(entry, 'path') === path) found = subscript(entry, 'kind');
        if (found === undefined) throw new PyKeyError(path);
        return found;
      };
      for (const [name, path, arg, private_data] of external_paths(lock, recipe, data_args, this.host)) {
        if (private_data) check_external_data_arg(name, path, null, null, kind(name, path), this.host);
        else check_external_command(name, path, arg, null, this.host);
      }
    }
    const units = this._units(this.build, lock);
    const previous = pathlib.isSymlink(this.current) ? pathlib.resolve(this.current) : null;
    const previous_lock = previous ? this._lock(previous) : null;
    const previous_units = previous ? this._units(previous, previous_lock!) : new Map<string, string>();
    if (this.activation === 'install_only' && !this.check) this._refuse_active([...units.keys(), ...previous_units.keys()]);
    const artifacts = this._artifacts(units, this.build);
    // Include current and generated units before env/pointer/service changes.
    this._preflight(new Map([...artifacts, [this.current, [Deployment._link_record(this.build), null]]]));
    return [lock, units, previous, previous_lock, previous_units, artifacts];
  }

  /** deploy.py:1217-1301 */
  activate(secrets: PyValue = null): PyDict {
    this.validate_paths();
    this.preflight_activation();
    this._check_secrets_file();
    if (secrets === null) secrets = this.check ? new Map() : this._read_secrets();
    this.rendered_units = new Map();
    this._load_ownership();
    this._check_build_ownership();
    const supplied = new Set(iterate(secrets) as string[]);
    if ((supplied.size !== this.names.length || !this.names.every(name => supplied.has(name))) && !this.check) {
      throw new DeployError('secret provider must return exactly the declared names');
    }
    const values = mapping(secrets) as Map<string, PyValue>;
    if ([...values.values()].some(value => !isStr(value))) throw new DeployError('secret provider must return exactly the declared names');
    const given = values as Map<string, string>;
    const direct = direct_secret_names(this.config);
    if (direct.some(name => given.has(name) && /[\u0000\n\r]/u.test(given.get(name)!))) throw new DeployError('multiline/NUL env secrets are unsupported');
    if (this.check && !pathlib.isFile(pathlib.joinPath(this.build, '.prepared'))) return dict({ changed: true });
    const [lock, units, previous, previous_lock, previous_units, artifacts] = this._activation_plan();
    const [file_snapshot, file_names] = this._secret_file_snapshot(previous, this.config);
    const secret_files = iterate(dictGet(this.config, 'secret_files', [])) as PyDict[];
    const file_values = new Map<string, Buffer>();
    if (!this.check) {
      for (const record of secret_files) {
        const value = given.get(item(record, 'secret') as string);
        if (value === undefined) throw new PyKeyError(item(record, 'secret') as string);
        file_values.set(item(record, 'env') as string, Buffer.from(value, 'utf8'));
      }
    }
    const env_values = new Map<string, string>([...given].filter(([name]) => direct.includes(name)));
    for (const record of secret_files) env_values.set(item(record, 'env') as string, Deployment._systemd_path(this._secret_file_path(item(record, 'env') as string)));
    const env = [...env_values].sort(([a], [b]) => compare(a, b))
      .map(([name, value]) => name + '="' + value.replaceAll('\\', '\\\\').replaceAll('"', '\\"') + '"\n').join('');
    const env_path = this._environment_path(this.build);
    const same_env = pathlib.isFile(env_path) && pathlib.readText(env_path) === env && (pathlib.stat(env_path).mode & 0o777) === 0o600;
    const same_units = [...artifacts].every(([path, [record]]) => this.owns(path, record));
    const retired = [...this.owned.keys()].some(path => pathlib.isRelativeTo(path, this.unit_dir) && !artifacts.has(path));
    const same_activation = this._staged() === (this.activation === 'install_only');
    if (this.check) return dict({ changed: previous !== this.build || !same_units || retired || !same_activation });
    this._ensure_state();
    const same_files = file_values.size === file_snapshot.size && [...file_values].every(([name, value]) => file_snapshot.get(name)?.equals(value) === true);
    if (previous === this.build && same_env && same_units && !retired && same_files && same_activation) {
      if (file_names.size) this._set_secret_files(file_values, file_names);
      this._health(units, lock, this.build);
      return this._status(dict({ changed: false }));
    }
    const old_env = pathio.exists(env_path) ? pathlib.readText(env_path) : null;
    atomic_write(env_path, env);
    let step = 'secret files';
    try {
      if (file_names.size) this._set_secret_files(file_values, file_names);
      step = 'release pointer';
      this._put(this.current, Deployment._link_record(this.build));
      step = 'unit installation';
      this._install(units, previous_units, this.build);
      step = 'health';
      this._health(units, lock, this.build);
      step = 'activation state';
      if (this.activation === 'install_only') atomic_write(pathlib.joinPath(this.state, 'activation'), 'install_only\n');
      else pathio.unlinkMissingOk(pathlib.joinPath(this.state, 'activation'));
    } catch (error) {
      // Use only closed diagnostics, never exception text, command argv, stderr or environment values. Preserve the
      // *first* failure even when recovery fails too; public() still uses the existing code.
      const cause: SafeError = error instanceof UnitVerificationFailed ? error
        : error instanceof SafeError ? new DeployError(diagnostic(error.code, error.cook_error_code).msg, error.code)
          : new DeployError('unexpected activation exception (details redacted)');
      try {
        if (file_names.size) this._set_secret_files(file_snapshot, file_names);
        if (previous) {
          if (previous === this.build && old_env !== null) atomic_write(env_path, old_env);
          this._put(this.current, Deployment._link_record(previous));
          this._install(previous_units, units, previous);
          this._health(previous_units, previous_lock, previous);
        } else {
          this._remove(this.current);
          this._install(new Map(), units, this.build);
        }
      } catch {
        throw new ActivationFailed(step, cause, false);
      }
      throw new ActivationFailed(step, cause, true);
    }
    return this._status(dict({ changed: true, build_dir: this.build, build_id: subscript(subscript(lock, 'build'), 'id') }));
  }
}
