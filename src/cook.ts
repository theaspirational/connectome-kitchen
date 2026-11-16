/**
 * Production adapter for the reviewed cook source tree. No service activation and no secret lookup.
 * Port of `oracle/module_utils/connectome_cook.py` (a49b5dc), the branches a deployment without a catalog takes.
 *
 * STATUS: success-path port, held against the oracle by the deploy corpus (`test/corpus`). It has no mutants and no
 * vectors for hostile input, and it is not listed as ported in PORTING.md. What is not here:
 *   - catalog mode (refused by `validate_config` before anything else happens);
 *   - argparse's prefix abbreviations and `-h`: an option must be written out, anything else is `E_ADAPTER_INPUT`;
 *   - `verify_cook` has run against no real cook checkout yet (the corpus records the call and does not perform it).
 * Line references are to `connectome_cook.py`.
 */
import * as inputs from './inputs.js';
import { environment_names, resolve_pins, validate_config } from './deploy.js';
import { LockError, credential_shaped, read_lock, repository, text } from './lock.js';
import { SafeError, cook_code_from_stream, diagnostic } from './errors.js';
import type { Diagnostic } from './errors.js';
import { ArchiveError, FRAMEWORK, dependency_provenance, framework_provenance, records_metadata } from './archive.js';
import * as pathio from './pylike/pathio.js';
import * as pathlib from './pylike/pathlib.js';
import {
  BytesIO, PyAttributeError, PyKeyError, PyOSError, PyTypeError, PyValueError,
  deepcopy, dictGet, eq, isDict, isList, isStr, item, iterate, keysEqual, keysInclude, keysWithin, strIn, subscript, truthy, typeName,
} from './pylike/py.js';
import type { PyDict, PyValue } from './pylike/py.js';
import { canonical, dict, dumps, indented } from './pylike/pydump.js';
import { loads } from './pylike/pyjson.js';
import { join as shlex_join } from './pylike/shlex.js';
import * as subprocess from './pylike/subprocess.js';
import { strip } from './pylike/text.js';
import * as isolation from './isolation.js';

export const COOK_COMMIT = '1b874d9dd55b529486b8029dd9814a6c0e2147e0';
/**
 * The name the adapter has in a build's request identity (ruling D5, plan question Q1). The engine hashes this word where
 * the oracle hashes the interpreter and the script path (deploy.py:482), so a build is named by what the adapter does
 * and not by where Node or kitchen are installed.
 *
 * The number moves when, for some request, the adapter puts something else into a build: the recipe it writes or stages,
 * the layout, `framework.lock.json`, the lock after its post-processing, a mode of one of those files, or the arguments
 * and the environment names it hands cook. It moves for nothing else. A change that leaves all of that as it is (a
 * refactoring, a new refusal, another Node, another install location) keeps the number, and no agent is rebuilt for it.
 * `test/corpus/adapter_contract.py` holds that output against the record kept under this name
 * (`test/corpus/adapter-contract/`), and fails on any difference until the number here is raised and a new record made.
 *
 * `/2` (ruling D6) is `/1` and how the adapter has cook acquire what goes into a build (src/isolation.ts):
 *   - `BUN_INSTALL_CACHE_DIR` with `COOK_PASS_ENV`: Bun installs from an empty cache of the build's own, never from the
 *     account's. That is part of the contract and it can change the bytes of a build: with nothing cached, Bun asks the
 *     registry for every package and resolves a range that no lockfile pins to the newest version that fits, where a
 *     warm account cache could have answered with an earlier one. Cook's install is not pinned beyond the two archives
 *     (backlog B26), and `/2` does not pin it; it says which acquisition a build of this name had.
 *   - `GIT_CONFIG_COUNT` and its `GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>`, only where the recipe names a source
 *     repository by a local path: Git takes that repository over its transport and does not hard-link its objects, so
 *     the clone's object files are its own (a pack, where a linked clone has the repository's loose files).
 * These names were first added under `/1` with the number kept, on the ground that they could not change what comes into
 * a build. A review showed the ground false for the first of them, the owner had the number raised, and the exception
 * is gone: every name in cook's environment is under the number, and the record of `/2` holds them all.
 */
export const ADAPTER_CONTRACT = 'kitchen-cook-adapter/2';
export const BASE_ENV: ReadonlySet<string> = new Set(['PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR']);

export const AdapterError = ArchiveError;
const EMPTY: PyDict = new Map();
type Environment = Readonly<Record<string, string>>;
function mapping(value: PyValue): PyDict {
  if (!isDict(value)) throw new PyAttributeError("'" + typeName(value) + "' object has no attribute 'items'");
  return value;
}

/** cook.py:29-31 */
export function need(condition: unknown, message: string, code = 'E_ADAPTER_INPUT'): asserts condition {
  if (!condition) throw new AdapterError(message, code);
}

/** cook.py:34-39 */
export function validate_arguments(argv: PyValue, flag_indices: readonly number[] = []): void {
  // Only trusted flag positions are exempt; a value equal to a flag is not.
  need(isList(argv) && argv.length && argv.every((arg, index) => text(arg) && !credential_shaped(arg) && (!arg.startsWith('-') || flag_indices.includes(index))),
    'command arguments contain invalid or credential-shaped values');
}

export interface CommandOptions { cwd?: string | null; env?: Environment | null; capture?: boolean; flag_indices?: readonly number[]; }

/** cook.py:42-57 */
export function command(argv: readonly string[], { cwd = null, env = null, capture = false, flag_indices = [] }: CommandOptions = {}): Buffer | null {
  validate_arguments([...argv], flag_indices);
  // Capture privately. No raw output or exception containing argv ever crosses the adapter boundary.
  let result: subprocess.Completed;
  try {
    result = subprocess.run(argv, { cwd, env, stdin: 'ignore', stdout: capture ? 'pipe' : 'ignore', stderr: 'pipe' });
  } catch (error) {
    if (!(error instanceof PyOSError)) throw error;
    throw new AdapterError('local command unavailable', 'E_COOK_FAILED');
  }
  if (result.returncode) {
    const upstream = capture ? null : cook_code_from_stream(new BytesIO(result.stderr));
    throw new AdapterError('cook adapter command failed (output redacted)', 'E_COOK_FAILED', upstream);
  }
  return capture ? result.stdout : null;
}

/** cook.py:60-63 */
export function clean_environment(): Record<string, string> {
  // No arbitrary inherited values, COOK_PASS_ENV, Bun preload options, Git overrides, provider credentials, or cook env-file input reach the child.
  return Object.fromEntries(Object.entries(process.env).filter((entry): entry is [string, string] => BASE_ENV.has(entry[0]) && entry[1] !== undefined));
}

/** cook.py:66-71 */
export function absolute_path(value: PyValue): string {
  need(text(value) && !credential_shaped(value) && !value.includes('?') && !value.includes('#'), 'invalid or credential-shaped adapter path');
  const path = pathlib.pathStr(value);
  need(pathlib.isAbsolute(path), 'absolute adapter path required');
  return path;
}

export type VerifyCook = (entrypoint: string, pin: PyValue, env: Environment) => void;

/** cook.py:74-94 */
export function verify_cook(entrypoint: string, pin: PyValue, env: Environment): void {
  entrypoint = absolute_path(entrypoint);
  need(pathlib.isAbsolute(entrypoint) && pathlib.isFile(entrypoint), 'absolute cook entrypoint required');
  const root = pathlib.parent(pathlib.parent(entrypoint));
  need(pathlib.resolve(entrypoint) === pathlib.joinPath(pathlib.resolve(root), 'bin/cook'), 'use the reviewed source bin/cook entrypoint');
  // The reviewed checkout may be owned by another account (root) and read-only. Trust exactly this resolved path for
  // these calls only; never '*', never a gitconfig write. No fsmonitor, hooks or optional index-lock refreshes run.
  const checkout = pathlib.resolve(root);
  const prefix = ['git', '-c', 'safe.directory=' + checkout, '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '--no-optional-locks', '-C', checkout];
  const prefix_flags = [1, 3, 5, 7, 8];
  const git = (args: readonly string[], flag_indices: readonly number[] = []): string =>
    strip(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(
      command([...prefix, ...args], { env, capture: true, flag_indices: [...prefix_flags, ...flag_indices.map(index => index + prefix.length)] })!));
  need(git(['rev-parse', 'HEAD']) === pin, 'cook checkout differs from requested pin');
  need(git(['rev-parse', 'HEAD^{tree}']) === git(['rev-parse', COOK_COMMIT + '^{tree}']), 'unsupported cook source tree');
  need(!git(['status', '--porcelain', '--untracked-files=normal'], [1, 2]), 'cook source checkout must be clean');
}

/** cook.py:97-102 */
export function build_commands(entry: PyValue): string {
  const builds = dictGet(entry, 'build', []);
  need(isList(builds) && builds.every(argv => isList(argv) && argv.length && argv.every(arg => text(arg) && !credential_shaped(arg))),
    'descriptor build must contain nonempty argv lists');
  return (builds as string[][]).map(argv => shlex_join(argv)).join(' && ');
}

/** cook.py:105-108 */
export function source(entry: PyValue, pin: PyValue, target: string): PyDict {
  need(repository(dictGet(entry, 'repo')), 'source requires a safe HTTPS, SSH/scp or absolute local repository');
  return dict({ url: subscript(entry, 'repo'), ref: pin, inContainer: dict({ path: target }), install: dict({ run: build_commands(entry), runtime: 'custom' }) });
}

const SPEC = new Set(['config', 'pins']);
const CONNECTOR_SETTINGS = new Set(['command', 'args', 'env', 'url', 'transport', 'token', 'toolPrefix', 'enabledFeatureSets', 'disabledFeatureSets',
  'enabledTools', 'disabledTools', 'reconnect', 'reconnectIntervalMs', 'channelSubscription']);
const REFERENCE = /^\$\{[A-Z_][A-Z0-9_]*\}$/u;
// The two names of the build's own Bun cache are the adapter's too, and Git's configuration names (src/isolation.ts); the oracle has neither.
const overlapsOwned = (names: readonly string[]): boolean => names.some(name => BASE_ENV.has(name) || name === 'DATA_DIR' || name === 'WORKSPACE_DIR'
  || name === isolation.CACHE_ENV || name === isolation.PASS_ENV || name.startsWith('GIT_CONFIG_'));

/**
 * Only the effective entries are translated. No discovery/catalog reads. cook.py:111-202
 *
 * settings are native cook/host settings, not a guessed connector launch API. Missing launch metadata refuses rather
 * than inventing an upstream command.
 */
export function recipe_from_spec(spec: PyValue, recipe_bytes: Uint8Array | null = null, _build_dir: string | null = null): [recipe: PyValue, names: string[]] {
  need(isDict(spec) && keysEqual(spec, SPEC), 'normalized deployment spec required');
  const config = item(spec, 'config'), pins = item(spec, 'pins');
  validate_config(config);
  const settings_of = config as PyDict;
  // Revalidate full pins even when used without the Ansible role.
  const versions = new Map(mapping(pins));
  const connectors = settings_of.has('recipe_file') ? EMPTY : mapping(dictGet(config, 'connectors', EMPTY));
  for (const [name, entry] of connectors) need(eq(dictGet(entry, 'pin_ref'), 'connectors.' + name), 'connector pin_ref must name its resolved connector');
  need(eq(resolve_pins(config, versions), pins), 'normalized pins required');
  if (truthy(dictGet(config, 'budgets'))) {
    throw new AdapterError('step/cost budgets not supported by host recipe schema at ' + String(subscript(subscript(pins, 'runtime'), 'connectome-host')) + '; remove budgets explicitly', 'E_BUDGETS_UNSUPPORTED');
  }
  if (settings_of.has('recipe_file')) {
    const record = item(settings_of, 'recipe_file');
    let content: Uint8Array;
    try {
      content = recipe_bytes === null ? inputs.read_file(subscript(record, 'path') as string, record) : inputs.verify_bytes(recipe_bytes, record);
    } catch (error) {
      if (error instanceof PyValueError) throw new AdapterError(error.message);
      throw error;
    }
    const names = environment_names(config);
    need(!overlapsOwned(names), 'secret names overlap adapter-owned environment');
    return [loads(content), names];
  }
  const agent = dictGet(config, 'agent');
  need(isDict(agent) && (dictGet(agent, 'systemPrompt') === null || isStr(dictGet(agent, 'systemPrompt'))), 'agent.systemPrompt must be a string when present');
  const modules: PyDict = new Map(), servers: PyDict = new Map();
  const recipe: PyDict = dict({ name: item(settings_of, 'name'), agent: deepcopy(agent), modules, mcpServers: servers, extensions: new Map() });
  if (settings_of.has('description')) recipe.set('description', item(settings_of, 'description'));
  for (const section of ['connectors', 'modules']) {
    for (const [name, entry] of mapping(dictGet(config, section, EMPTY))) {
      need(dictGet(entry, 'enabled', true) === true, 'adapter input must already exclude disabled entries');
      const settings = deepcopy(dictGet(entry, 'settings', new Map()));
      need(isDict(settings), 'descriptor settings must be a mapping');
      if (section === 'connectors') {
        need(keysWithin(settings, CONNECTOR_SETTINGS) && isStr(dictGet(settings, 'command')), 'connector settings require native stdio command metadata');
        need(!(settings.has('token') && !(isStr(settings.get('token')) && REFERENCE.test(settings.get('token') as string))), 'connector token must be a secret-name reference');
        settings.set('source', source(entry, subscript(subscript(pins, 'connectors'), name), '/connectors/' + name));
        servers.set(name, settings);
      } else {
        need(!strIn('repo', entry) && !strIn('build', entry), 'source-backed modules require a separate reviewed extension adapter');
        // Empty settings enable a native module. Disabled entries never reach this loop; the role already pruned them.
        modules.set(name, settings.size ? settings : true);
      }
    }
  }
  const names = environment_names(config);
  need(!overlapsOwned(names), 'secret names overlap adapter-owned environment');
  // Cook needs nonempty declarations to record runtime-only names in its lock. Never emit an empty cook-only block to the host configuration.
  if (names.length) recipe.set('requirements', new Map(names.map(name => [name, dict({ exposeAs: name, required: false })])));
  // A secret-looking env field must be a reference, never a literal/default.
  for (const server of servers.values()) {
    for (const [key, value] of mapping(dictGet(server, 'env', EMPTY))) {
      if (names.includes(key) || /TOKEN|SECRET|PASSWORD|API_KEY/u.test(key)) {
        need(names.includes(key) && value === '${' + key + '}', 'connector secret env must reference an explicitly declared name');
      }
    }
  }
  return [recipe, names];
}

/** Top-level recipe servers marked source.external, by name. cook.py:205-211 */
export function external_servers(recipe: PyValue): PyDict {
  const servers = isDict(recipe) ? dictGet(recipe, 'mcpServers', new Map()) : new Map();
  need(isDict(servers), 'recipe mcpServers must be a mapping');
  return new Map([...servers].filter(([, server]) => isDict(server) && isDict(dictGet(server, 'source')) && strIn('external', dictGet(server, 'source')))
    .map(([name, server]) => [name, dictGet(server, 'command')]));
}

/**
 * The source repositories a recipe names by a local path: `url` of every `source` mapping in it that is not `external`.
 * Not in the oracle. Only the recipe handed to cook is read: a repository named in a file the recipe refers to is not found.
 */
export function local_sources(recipe: PyValue): string[] {
  const found = new Set<string>();
  const walk = (value: PyValue): void => {
    if (isList(value)) for (const entry of value) walk(entry);
    if (!isDict(value)) return;
    for (const [key, entry] of value) {
      if (key === 'source' && isDict(entry) && !entry.has('external')) {
        const url = entry.get('url');
        if (isStr(url) && url.startsWith('/')) found.add(url);
      }
      walk(entry);
    }
  };
  walk(recipe);
  return [...found].sort();
}

/** Compatibility helper; provenance is recorded separately from cook's lock. cook.py:214-218 */
export function framework_input(config: PyValue, pin: PyValue): string {
  framework_provenance(config, pin);
  const artifact = subscript(subscript(config, 'cook'), 'framework_archive');
  return FRAMEWORK + '=' + (subscript(artifact, 'path') as string) + '#sha256=' + (subscript(artifact, 'sha256') as string);
}

export interface Seams {
  /** `connectome_cook.verify_cook`, replaceable as the corpus replaces the oracle's (test/corpus/adapter_seam.py). */
  verify_cook?: VerifyCook;
}

const COOK_REQUIRED = new Set(['host_repo', 'connector_state']);
const COOK_ALLOWED = new Set(['host_repo', 'framework_archive', 'host_dependencies', 'connector_state']);
/** `{(a, b), ...}` of two JSON values, as comparable keys. */
const pair = (a: PyValue, b: PyValue): string => canonical([a, b]);

/** cook.py:221-377 */
export function adapt(spec_file: PyValue, build_dir: PyValue, state_dir: PyValue, runtime: PyValue, entrypoint: PyValue, bun_path: PyValue, node_path: PyValue, seams: Seams = {}): PyDict {
  const build = absolute_path(build_dir), state = absolute_path(state_dir), spec_path = absolute_path(spec_file);
  need([build, state, spec_path].every(path => pathlib.isAbsolute(path) && pathlib.resolve(path) === path), 'adapter paths must be absolute without symlink aliases');
  need(pathio.isDir(build) && pathlib.name(spec_path) === 'deployment.json', 'fresh deployment.json required');
  need(pathlib.name(pathlib.parent(build)) === 'builds', 'adapter output must be ROOT/builds/REQUEST');
  const root = pathlib.parent(pathlib.parent(build));
  need(!pathlib.isRelativeTo(state, root) && !pathlib.isRelativeTo(root, state), 'state must be separate from deployment root/builds');
  const spec = loads(pathlib.readText(spec_path));
  for (const value of [runtime, entrypoint, bun_path, node_path]) absolute_path(value);
  need([runtime, bun_path, node_path].every(path => pathlib.isAbsolute(path as string) && pathio.executable(path as string)), 'absolute executable runtime, Bun and Node paths required');
  const staged = pathlib.joinPath(build, 'cook-recipe.json');
  const supplied_recipe = strIn('recipe_file', dictGet(spec, 'config', new Map()));
  const allowed = new Set([spec_path]);
  let content: Uint8Array | null = null;
  if (supplied_recipe) {
    const record = (): PyValue => subscript(subscript(spec, 'config'), 'recipe_file');
    if (pathio.exists(staged) || pathlib.isSymlink(staged)) {
      allowed.add(staged);
      try {
        content = inputs.read_file(staged, record());
      } catch (error) {
        if (error instanceof PyValueError || error instanceof PyTypeError) throw new AdapterError('invalid staged recipe');
        throw error;
      }
    } else {
      try {
        content = inputs.read_file(subscript(record(), 'path') as string, record());
      } catch (error) {
        if (error instanceof PyValueError || error instanceof PyTypeError || error instanceof PyKeyError) throw new AdapterError('invalid recipe input');
        throw error;
      }
    }
  }
  const present = pathio.listdir(build).map(name => pathlib.joinPath(build, name));
  need(present.length === allowed.size && present.every(path => allowed.has(path)), 'cook requires a fresh build containing only deployment.json and optional recipe');
  const [recipe, names] = recipe_from_spec(spec, content, build);
  const config = subscript(spec, 'config') as PyDict, pins = subscript(subscript(spec, 'pins'), 'runtime');
  need(dictGet(config, 'state_dir') === state, 'adapter state must match configured state_dir');
  const settings = dictGet(config, 'cook', new Map());
  need(isDict(settings), 'cook settings must be a mapping');
  need(keysInclude(settings, COOK_REQUIRED) && keysWithin(settings, COOK_ALLOWED) && (settings.has('framework_archive') || settings.has('host_dependencies')),
    'cook requires host_repo, local dependencies and explicit connector_state');
  need(repository(item(settings, 'host_repo')), 'cook.host_repo requires a safe HTTPS, SSH/scp or absolute local repository');
  const external = external_servers(recipe);
  const connector_state = item(settings, 'connector_state');
  need(isDict(connector_state) && ![...connector_state.keys()].some(name => external.has(name)), 'connector_state must not name an external server');
  const framework_pin = subscript(pins, 'agent-framework');
  const dependencies = dependency_provenance(config, framework_pin);
  let provenance: PyDict | null = null;
  if (settings.has('framework_archive')) {
    provenance = framework_provenance(config, framework_pin);
    pathio.writeBytes(pathlib.joinPath(build, 'framework.lock.json'), indented(provenance) + '\n');
  }
  const recipe_path = pathlib.joinPath(build, 'cook-recipe.json');
  if (supplied_recipe) {
    pathio.writeBytes(recipe_path, content!);
    pathio.chmod(recipe_path, 0o600);
  } else pathio.writeBytes(recipe_path, indented(recipe) + '\n');
  // Config/credentials are per-request, state/workspace persist. All roots are non-overlapping as required by cook's
  // split layout. Do not assume the parent of an arbitrary external state_dir is writable by this uid.
  const request = pathlib.name(build);
  const layout: PyDict = dict({
    schema: 'connectome.host-layout/v1', runtime: build,
    config: pathlib.joinPath(pathlib.joinPath(root, 'cook-config'), request),
    credentials: pathlib.joinPath(pathlib.joinPath(root, 'cook-credentials'), request),
    state, workspace: pathlib.joinPath(root, 'workspace'), connectorState: connector_state,
  });
  const layout_path = pathlib.joinPath(build, 'cook-layout.json');
  pathio.writeBytes(layout_path, indented(layout) + '\n');
  const argv: PyValue[] = [runtime, entrypoint, 'install', recipe_path];
  const flag_indices: number[] = [];
  const name = item(config, 'name');
  if (!isStr(name)) throw new PyTypeError('can only concatenate str');
  const operands: [string, PyValue][] = [
    ['--out', build], ['--no-prompts', null], ['--yes', null], ['--strict', null],
    ['--ch-repo', item(settings, 'host_repo')], ['--ch-ref', subscript(pins, 'connectome-host')],
    ['--bun-path', bun_path], ['--node-path', node_path], ['--layout', layout_path],
    ...dependencies.map((d): [string, PyValue] => ['--host-dependency', (item(d, 'name') as string) + '=' + (item(d, 'archive') as string) + '#sha256=' + (item(d, 'sha256') as string)]),
    ['--user-unit', 'connectome-' + name + '.service'],
  ];
  for (const [flag, value] of operands) {
    flag_indices.push(argv.length);
    argv.push(flag);
    if (value !== null) argv.push(value);
  }
  // Validate all input before even the verification Git subprocesses spawn.
  validate_arguments(argv, flag_indices);
  const env = clean_environment();
  (seams.verify_cook ?? verify_cook)(entrypoint as string, subscript(pins, 'cook'), env);
  // Not in the oracle. Bun links package files from its cache into the build, and sets modes on the linked inodes. With
  // the account's cache those are files of every earlier build, also of the one that runs. So this build gets a cache of
  // its own for this one run (cook hands Bun the name only where COOK_PASS_ENV selects it), and nothing of it stays: not
  // this one, and not one a killed run left under this root. Git likewise links the objects of a repository named by a
  // local path; for those the recipe names it is told to take them over its transport.
  const cache = isolation.cache_dir(root, request);
  isolation.sweep(root, request);
  try {
    command(argv as string[], { cwd: build, env: { ...env, [isolation.CACHE_ENV]: cache, [isolation.PASS_ENV]: isolation.CACHE_ENV, ...isolation.git_environment(local_sources(recipe)) }, flag_indices });
  } finally {
    isolation.remove(cache);
  }
  // What the cache of its own is for, held on the build itself: no file of it is a file of another build.
  need(isolation.shared(pathlib.parent(build), request).with_other_builds === 0, 'cook left files in the build that another build shares', 'E_COOK_FAILED');
  const lock_path = pathlib.joinPath(build, 'connectome.lock');
  let lock = read_lock(lock_path, { host_pin: subscript(pins, 'connectome-host'), required_names: names });
  need(eq(dictGet(lock, 'layout'), layout) && eq(dictGet(lock, 'state'), dict({ path: state })),
    'cook lock did not preserve the requested persistent layout', 'E_LOCK_STATE_PATH');
  const components = iterate(item(lock, 'components'));
  if (!supplied_recipe) {
    const expected = new Set([...mapping(dictGet(config, 'connectors', EMPTY))].map(([connector, entry]) => pair(subscript(entry, 'repo'), subscript(subscript(subscript(spec, 'pins'), 'connectors'), connector))));
    const actual = new Set(components.map(entry => pair(dictGet(entry, 'url'), dictGet(entry, 'commit'))));
    need(actual.size === expected.size && [...actual].every(key => expected.has(key)) && !lock.has('external')
      && components.every(component => subscript(component, 'kind') === 'git' && subscript(component, 'role') === 'mcp'),
    'cook component provenance differs from enabled inputs');
  }
  // External servers are not components: no commit is claimed. Bind the recorded commands exactly to the reviewed
  // top-level recipe, whose args activation also checks; fleet-child external servers are refused.
  const recorded: PyDict = new Map(iterate(dictGet(lock, 'external', [])).map(server => [subscript(server, 'name') as string, subscript(server, 'command')]));
  need(eq(recorded, external), 'cook external servers differ from the recipe');
  // Validate cook's inventory before adding config-only provenance. Legacy configs never rewrite cook's lock, argv or build identity.
  const expected = new Map(dependencies.map(d => [item(d, 'name') as string, d]));
  const inventory = iterate(dictGet(lock, 'hostDependencies', []));
  const listed = new Set(inventory.map(dep => subscript(dep, 'name') as string));
  need(inventory.length === expected.size && listed.size === expected.size && [...listed].every(dep => expected.has(dep)),
    'cook dependency inventory differs from inputs', 'E_LOCK_INVALID');
  for (const dep of inventory) {
    const record = expected.get(subscript(dep, 'name') as string)!;
    need(eq(subscript(dep, 'version'), item(record, 'version')) && eq(subscript(dep, 'sha256'), item(record, 'sha256')),
      'cook dependency identity differs from inputs', 'E_LOCK_INVALID');
  }
  if (records_metadata(config)) {
    // Keep cook's raw schema (not the reader's derived units) on disk.
    const raw = loads(pathlib.readText(lock_path));
    for (const dep of iterate(subscript(raw, 'hostDependencies'))) {
      const record = expected.get(subscript(dep, 'name') as string);
      if (record === undefined || !isDict(dep)) throw new PyKeyError('hostDependencies');
      dep.set('provenance', record);
    }
    pathio.writeBytes(lock_path, indented(raw) + '\n');
  }
  lock = read_lock(lock_path, { host_pin: subscript(pins, 'connectome-host'), required_names: names, config, framework_pin, framework_record: provenance });
  // No values were supplied. Refuse unexpected value-bearing generated env, so run.sh cannot override the role's runtime EnvironmentFile secrets.
  const env_file = pathlib.joinPath(item(layout, 'config') as string, '.env');
  need(!pathio.exists(env_file) || !strip(pathlib.readText(env_file)), 'cook unexpectedly wrote runtime env values');
  return lock;
}

const OPTIONS = ['spec', 'out', 'state', 'runtime', 'entrypoint', 'bun-path', 'node-path'] as const;
const REQUIRED = ['spec', 'out', 'state', 'entrypoint'];
const line = (value: Diagnostic): string => dumps(new Map(Object.entries(value))) + '\n';

/** cook.py:380-397. Returns the exit status and what goes to stderr; the caller writes it and exits. */
export function main(argv: readonly string[], seams: Seams = {}): [status: number, stderr: string] {
  const args = new Map<string, string>();
  for (let index = 0; index < argv.length; index++) {
    const word = argv[index]!, cut = word.indexOf('=');
    const option = OPTIONS.find(candidate => word === '--' + candidate || word.startsWith('--' + candidate + '='));
    const value = option === undefined ? undefined : cut < 0 ? argv[++index] : word.slice(cut + 1);
    if (option === undefined || value === undefined) return [2, line(diagnostic('E_ADAPTER_INPUT'))];
    args.set(option, value);
  }
  if (REQUIRED.some(option => !args.has(option))) return [2, line(diagnostic('E_ADAPTER_INPUT'))];
  const get = (option: string): string | null => args.get(option) ?? null;
  try {
    adapt(get('spec'), get('out'), get('state'), get('runtime'), get('entrypoint'), get('bun-path'), get('node-path'), seams);
  } catch (error) {
    if (error instanceof SafeError) return [1, line(error.public())];
    if (error instanceof LockError) return [1, line(diagnostic('E_LOCK_INVALID'))];
    return [1, line(diagnostic('E_ADAPTER_INPUT'))];
  }
  return [0, ''];
}
