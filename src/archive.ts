/**
 * Verify local hash-pinned npm archives; no acquisition, extraction or scripts.
 * Port of `oracle/module_utils/connectome_archive.py` (a49b5dc), one to one.
 *
 * Comments of the form `row N <mutant id>: ... archive.py:L` mark where a row
 * of the fail-closed inventory (plan section 4.1) lives. A guard is always
 * `need(<condition>, <message>[, <code>])`.
 *
 * `lock.ts` loads this module lazily, as `lock.py` does (lock.py:256-258,
 * :282-284); this module imports `lock` statically, as `archive.py` does.
 */
import { createHash } from 'node:crypto';
import { SafeError } from './errors.js';
import { credential_shaped, digest, text, version } from './lock.js';
import { b64decodeStrict, b64encode } from './pylike/codec.js';
import * as pathlib from './pylike/pathlib.js';
import {
  PyOSError, PyTypeError, PyValueError,
  dictGet, eq, get, isDict, isList, isStr, item, iterate, keysEqual, keysInclude, keysWithin, strIn, subscript, truthy, typeName,
} from './pylike/py.js';
import type { PyDict, PyValue } from './pylike/py.js';
import { loads } from './pylike/pyjson.js';
import { S } from './pylike/regex.js';
import * as tarfile from './pylike/tarfile.js';
import * as url from './pylike/url.js';

export const FRAMEWORK = '@' + 'animalabs/agent-framework';
export const ARTIFACT_PAYLOAD = 'operator-supplied prebuilt payload, verified out of band';
export const REGISTRY_COMPARISON = 'bytes were not compared against the registry';

/** archive.py:22-24 */
export class ArchiveError extends SafeError {
  constructor(message: string, code: PyValue = 'E_ADAPTER_INPUT', cook_error_code: PyValue = null) {
    super(message, code, cook_error_code);
  }
  override get pyName(): string { return 'ArchiveError'; }
}

/** archive.py:27-29 */
export function need(condition: PyValue, message: string, code: PyValue = 'E_ADAPTER_INPUT'): void {
  if (!truthy(condition)) throw new ArchiveError(message, code);
}

const sha256 = (bytes: Uint8Array): string => createHash('sha256').update(bytes).digest('hex');

const NPM_NAME = /^(?:@[a-z0-9][a-z0-9._-]*\/)?[a-z0-9][a-z0-9._-]*$/u;
/** archive.py:32-35 */
export function npm_name(value: PyValue): boolean {
  // npm's URL-safe, lowercase new-package names (including optional scope).
  // row 39 cook.provenance: npm name grammar, length and the two reserved names. archive.py:34-35
  return text(value) && Array.from(value).length <= 214 && value !== 'node_modules' && value !== 'favicon.ico'
    && NPM_NAME.test(value);
}

const REGISTRY_REFUSED = new RegExp('[' + S + '%\\\\?#]', 'u');
const REGISTRY_HOST = /^[A-Za-z0-9][A-Za-z0-9.-]*$/u;
/** archive.py:38-48 */
export function registry_origin(value: PyValue): boolean {
  // row 39 cook.provenance: a registry origin is a bare, credential-free HTTPS origin. archive.py:39-46
  if (!text(value) || credential_shaped(value) || REGISTRY_REFUSED.test(value)) return false;
  try {
    const parts = url.split(value);
    const host = url.hostname(parts.netloc);
    if (!(parts.scheme === 'https' && url.username(parts.netloc) === null && url.password(parts.netloc) === null
      && (parts.path === '' || parts.path === '/') && !parts.query && !parts.fragment
      && host !== null && REGISTRY_HOST.test(host))) return false;
    const port = url.port(parts.netloc);
    return port === null || (0 < port && port <= 65535);
  } catch (error) {
    if (error instanceof PyValueError) return false;
    throw error;
  }
}

/** archive.py:51-58 */
export function sha512_integrity(value: PyValue): boolean {
  // row 39 cook.provenance: a canonical sha512 integrity string. archive.py:52-56
  if (!text(value) || !value.startsWith('sha512-')) return false;
  try {
    const decoded = b64decodeStrict(value.slice(7));
    return decoded.length === 64 && b64encode(decoded) === value.slice(7);
  } catch (error) {
    if (error instanceof PyValueError) return false;
    throw error;
  }
}

const DEPENDENCY_REQUIRED = new Set(['name', 'archive', 'sha256', 'kind', 'version']);
const DEPENDENCY_ALLOWED = new Set(['name', 'archive', 'sha256', 'kind', 'version', 'commit', 'registry_url', 'registry_integrity']);
const KINDS = new Set(['registry_release', 'artifact', 'git_commit']);

/** archive.py:61-85 */
export function validate_dependency(entry: PyValue): PyDict {
  // row 39 cook.provenance: closed dependency declaration. archive.py:62-65
  need(isDict(entry) && keysInclude(entry, DEPENDENCY_REQUIRED)
    && keysWithin(entry, DEPENDENCY_ALLOWED),
    'host dependency requires a closed name/archive/sha256/kind/version declaration');
  const record = entry as PyDict;
  // row 39 cook.provenance: an npm name and a concrete version. archive.py:66-68
  // row 1 lock.credential: no credential shape in a dependency name or version. archive.py:66-68
  need(npm_name(item(record, 'name')) && !credential_shaped(item(record, 'name'))
    && version(item(record, 'version')) && !credential_shaped(item(record, 'version')),
    'invalid host dependency identity', 'E_ARCHIVE_IDENTITY');
  const kind = item(record, 'kind');
  // row 39 cook.provenance: a closed set of archive kinds. archive.py:69
  need(isStr(kind) && KINDS.has(kind), 'invalid archive kind');
  if (kind === 'git_commit') {
    // row 39 cook.provenance: a git commit is a full, nonzero id. archive.py:71-72
    need(digest(get(record, 'commit'), 40) && !eq(item(record, 'commit'), '0'.repeat(40)),
      'git_commit requires a full commit');
  } else {
    // row 39 cook.provenance: a commit only for git_commit. archive.py:74
    need(!record.has('commit'), 'commit required only for git_commit');
  }
  if (kind === 'registry_release') {
    // row 39 cook.provenance: a registry release names its origin and integrity. archive.py:76-77
    need(registry_origin(get(record, 'registry_url')) && sha512_integrity(get(record, 'registry_integrity')),
      'registry_release requires registry_url and registry_integrity (sha512)');
  } else {
    // row 39 cook.provenance: registry identity only for registry_release. archive.py:79-80
    need(!(record.has('registry_url') || record.has('registry_integrity')),
      'registry identity is allowed only for registry_release');
  }
  const value = item(record, 'archive');
  // row 39 cook.provenance: an absolute local archive path without query or fragment. archive.py:82-83
  // row 1 lock.credential: no credential shape in an archive path. archive.py:82-83
  need(text(value) && !credential_shaped(value) && !/[?#]/u.test(value)
    && pathlib.isAbsolute(value), 'absolute local archive path required');
  // row 39 cook.provenance: a full archive digest. archive.py:84
  need(digest(item(record, 'sha256')), 'invalid archive hash', 'E_ARCHIVE_HASH');
  return record;
}

const PATH_SHA256 = new Set(['path', 'sha256']);
const ARTIFACT_FIELDS = new Set(['path', 'sha256', 'registry_release', 'name', 'version']);

/** archive.py:88-106 */
export function legacy_dependency(config: PyValue, pin: PyValue): PyDict {
  const artifact = subscript(subscript(config, 'cook'), 'framework_archive');
  // row 39 cook.provenance: closed framework archive record. archive.py:90-92
  need(isDict(artifact) && keysInclude(artifact, PATH_SHA256)
    && keysWithin(artifact, ARTIFACT_FIELDS),
    'cook.framework_archive requires path and sha256');
  const record = artifact as PyDict;
  const marked = record.has('registry_release');
  if (marked) {
    // row 39 cook.provenance: a marked release states its name and version. archive.py:95-97
    need(item(record, 'registry_release') === true && eq(get(record, 'name'), FRAMEWORK)
      && version(get(record, 'version')) && !credential_shaped(item(record, 'version')),
      'registry release requires explicit name and version', 'E_ARCHIVE_IDENTITY');
  } else {
    // row 39 cook.provenance: name and version only with the mark. archive.py:99
    need(keysEqual(record, PATH_SHA256), 'release identity requires registry_release: true');
  }
  const result: PyDict = new Map<string, PyValue>([
    ['name', FRAMEWORK], ['archive', item(record, 'path')], ['sha256', item(record, 'sha256')],
    ['kind', marked ? 'artifact' : 'git_commit'], ['version', get(record, 'version')],
  ]);
  if (!marked) result.set('commit', pin);
  // Legacy unmarked declarations infer version from the verified manifest.
  const probe: PyDict = new Map(result);
  probe.set('version', truthy(item(result, 'version')) ? item(result, 'version') : '0.0.0');
  validate_dependency(probe);
  return result;
}

/** archive.py:109-118 */
export function dependency_inputs(config: PyValue, pin: PyValue): PyDict[] {
  const settings = dictGet(config, 'cook', new Map());
  const entries = dictGet(settings, 'host_dependencies', []);
  // row 39 cook.provenance: host dependencies are a list. archive.py:112
  need(isList(entries), 'host_dependencies must be a list');
  const result: PyDict[] = (settings as PyDict).has('framework_archive') ? [legacy_dependency(config, pin)] : [];
  for (const entry of entries as PyValue[]) result.push(new Map(validate_dependency(entry)));
  const names = result.map(entry => item(entry, 'name'));
  // row 39 cook.provenance: one declaration per dependency. archive.py:117
  need(names.length === new Set(names).size, 'duplicate host dependencies refused');
  return result;
}

/**
 * `re.fullmatch(r'[^/]+/package.json', name)`: a first component, one slash,
 * then `package`, any one character but a newline, `json`. It is spelled out
 * and not a pattern because of the runtime: on a name that is not Latin-1,
 * V8 answers `/^[^/]+\/package[^\n]json$/u` with "RangeError: Maximum call
 * stack size exceeded" from about 8.4 million UTF-16 units on (Node 22.23.3
 * and 24.14.0), and the tar reader lets a name be 2**28 bytes long.
 */
const MANIFEST_NAME = {
  test(name: string): boolean {
    const slash = name.indexOf('/');
    if (slash < 1 || !name.startsWith('package', slash + 1) || !name.endsWith('json')) return false;
    // Between the two words stands one character and no more. It takes two UTF-16 units if it lies outside the BMP.
    const any = name.codePointAt(slash + 8)!;
    return any !== 0x0a && name.length - slash - 1 === (any > 0xffff ? 13 : 12);
  },
};

/** archive.py:121-145 */
export function archive_manifest(entry: PyValue): PyDict {
  const location = subscript(entry, 'archive');
  if (!isStr(location)) throw new PyTypeError('expected str, bytes or os.PathLike object, not ' + typeName(location));
  const record = entry as PyDict;
  const path = pathlib.pathStr(location);
  // row 39 cook.provenance: the archive is a regular file, not a link to one. archive.py:123
  need(pathlib.isFile(path) && !pathlib.isSymlink(path), 'archive must be an absolute regular file');
  let manifest: PyValue;
  try {
    const data = pathlib.readBytes(path);
    // row 39 cook.provenance: the bytes have the declared digest. archive.py:126-127
    need(eq(sha256(data), item(record, 'sha256')),
      'archive hash mismatch', 'E_ARCHIVE_HASH');
    if (eq(item(record, 'kind'), 'registry_release')) {
      const observed = 'sha512-' + createHash('sha512').update(data).digest('base64');
      // row 39 cook.provenance: and the declared registry integrity. archive.py:130
      need(eq(observed, item(record, 'registry_integrity')), 'registry integrity mismatch', 'E_ARCHIVE_HASH');
    }
    const manifests = tarfile.gzipMembers(data).filter(member => MANIFEST_NAME.test(member.name) && member.isfile);
    // row 39 cook.provenance: exactly one bounded package manifest. archive.py:134
    need(manifests.length === 1 && manifests[0]!.size <= 1024n * 1024n, 'archive manifest required');
    manifest = loads(manifests[0]!.read());
  } catch (error) {
    if (error instanceof ArchiveError) {
      // row 39 cook.provenance: a declared refusal keeps its own code and message. archive.py:136-137
      throw error;
    }
    if (!(error instanceof PyOSError || error instanceof PyValueError || error instanceof tarfile.TarError)) throw error;
    // row 39 cook.provenance: an unreadable archive is one refusal. archive.py:138-139
    throw new ArchiveError('unreadable archive or manifest');
  }
  // row 39 cook.provenance: the manifest is a mapping. archive.py:140
  need(isDict(manifest), 'archive manifest must be a mapping');
  // row 39 cook.provenance: package name and version are the declared identity. archive.py:141-144
  need(eq(dictGet(manifest, 'name'), item(record, 'name')) && version(dictGet(manifest, 'version'))
    && !credential_shaped(subscript(manifest, 'version'))
    && (item(record, 'version') === null || eq(subscript(manifest, 'version'), item(record, 'version'))),
    'archive package identity mismatch', 'E_ARCHIVE_IDENTITY');
  return manifest as PyDict;
}

/** archive.py:148-163 */
export function dependency_provenance(config: PyValue, pin: PyValue): PyDict[] {
  const result: PyDict[] = [];
  const inputs = dependency_inputs(config, pin);
  for (const [index, entry] of inputs.entries()) {
    const manifest = archive_manifest(entry);
    const legacy = index === 0 && strIn('framework_archive', dictGet(config, 'cook', new Map()));
    const head = get(manifest, 'gitHead');
    if (legacy) {
      // row 39 cook.provenance: the framework archive was built from the requested pin. archive.py:155-156
      need(manifest.has('gitHead') ? eq(head, pin) : eq(item(entry, 'kind'), 'artifact'),
        'framework archive gitHead differs from requested pin', 'E_ARCHIVE_GITHEAD');
    } else {
      // row 39 cook.provenance: a git commit archive names that commit. archive.py:158-160
      need(eq(item(entry, 'kind'), 'git_commit') ? eq(head, item(entry, 'commit'))
        : (!manifest.has('gitHead') || digest(head, 40)),
        'archive gitHead missing or invalid', 'E_ARCHIVE_GITHEAD');
    }
    const versioned: PyDict = new Map(entry);
    versioned.set('version', item(manifest, 'version'));
    result.push(provenance_record(versioned));
  }
  return result;
}

/** archive.py:166-172 */
export function provenance_record(entry: PyValue): PyDict {
  // row 39 cook.provenance: what a record claims follows from its kind alone. archive.py:167-171
  const kind = subscript(entry, 'kind');
  const record: PyDict = new Map(entry as PyDict);
  record.set('commit_verifiable', eq(kind, 'git_commit'));
  if (eq(kind, 'artifact')) record.set('payload', ARTIFACT_PAYLOAD);
  if (eq(kind, 'registry_release')) record.set('registry_comparison', REGISTRY_COMPARISON);
  return record;
}

/** archive.py:175-178 */
export function records_metadata(config: PyValue): boolean {
  const settings = dictGet(config, 'cook', new Map());
  // row 39 cook.provenance: records are written for declared dependencies and for a marked release. archive.py:177-178
  return strIn('host_dependencies', settings)
    || dictGet(dictGet(settings, 'framework_archive', new Map()), 'registry_release', null) === true;
}

/** archive.py:181-189 */
export function framework_provenance(config: PyValue, pin: PyValue): PyDict {
  // Preserve the v1 sidecar as a compatibility record of the legacy request
  // flag, NOT a registry-origin claim. Normalized provenance is artifact.
  const artifact = subscript(subscript(config, 'cook'), 'framework_archive');
  const only: PyDict = new Map([['cook', new Map([['framework_archive', artifact]]) as PyValue]]);
  const record = dependency_provenance(only, pin)[0]!; // a framework archive always yields its record first
  const manifest = archive_manifest(legacy_dependency(config, pin));
  return new Map<string, PyValue>([
    ['schema', 'connectome.framework-archive/v1'], ['sha256', item(record, 'sha256')],
    ['name', item(record, 'name')], ['version', item(record, 'version')], ['requested_commit', pin],
    ['registry_release', dictGet(artifact, 'registry_release', null) === true],
    ['commit_verifiable', manifest.has('gitHead')],
  ]);
}

/** archive.py:192-203 */
export function validate_dependency_record(record: PyValue): void {
  // row 39 cook.provenance: a provenance record is a mapping. archive.py:193
  need(isDict(record), 'invalid dependency provenance', 'E_LOCK_INVALID');
  // From here on `record.get` is the oracle's own next step: on anything but a mapping it is an AttributeError.
  const kind = dictGet(record, 'kind');
  const entry = record as PyDict;
  const extra = new Set(['commit_verifiable']);
  if (eq(kind, 'artifact')) extra.add('payload');
  if (eq(kind, 'registry_release')) extra.add('registry_comparison');
  validate_dependency(new Map([...entry].filter(([key]) => !extra.has(key))));
  // row 39 cook.provenance: the derived fields are exactly what the kind implies. archive.py:197-203
  need(typeof get(entry, 'commit_verifiable') === 'boolean'
    && item(entry, 'commit_verifiable') === eq(item(entry, 'kind'), 'git_commit')
    && (eq(kind, 'artifact') ? eq(get(entry, 'payload'), ARTIFACT_PAYLOAD) : !entry.has('payload'))
    && (eq(kind, 'registry_release') ? eq(get(entry, 'registry_comparison'), REGISTRY_COMPARISON) : !entry.has('registry_comparison')),
    'invalid dependency provenance', 'E_LOCK_INVALID');
}

/** Bind the entire lock inventory to config, without reopening input archives. archive.py:206-226 */
export function check_dependencies(data: PyValue, config: PyValue, pin: PyValue, framework_record: PyValue = null): void {
  const expected = dependency_inputs(config, pin);
  const actual = dictGet(data, 'hostDependencies', []);
  const present = iterate(actual);
  // row 9 lock.host-deps: the lock lists exactly the declared dependencies, in order. archive.py:210-211
  need(eq(present.map(dependency => subscript(dependency, 'name')), expected.map(entry => item(entry, 'name'))),
    'host dependency inventory mismatch', 'E_LOCK_INVALID');
  const indexed = new Map<PyValue, PyDict>(present.map(dependency => [subscript(dependency, 'name'), dependency as PyDict]));
  const generic = strIn('host_dependencies', dictGet(config, 'cook', new Map()));
  for (const entry of expected) {
    const dependency = indexed.get(item(entry, 'name'))!;
    let expectedVersion = item(entry, 'version');
    if (expectedVersion === null && framework_record !== null && eq(item(entry, 'name'), FRAMEWORK)) {
      expectedVersion = subscript(framework_record, 'version');
    }
    // row 9 lock.host-deps: digest and version are the declared ones. archive.py:219-221
    need(eq(item(dependency, 'sha256'), item(entry, 'sha256'))
      && (expectedVersion === null || eq(item(dependency, 'version'), expectedVersion)),
      'host dependency identity mismatch', 'E_LOCK_INVALID');
    if (generic || dependency.has('provenance')) {
      const record = get(dependency, 'provenance');
      validate_dependency_record(record);
      const versioned: PyDict = new Map(entry);
      versioned.set('version', item(dependency, 'version'));
      const wanted = provenance_record(versioned);
      // row 9 lock.host-deps: recorded provenance is what the declaration implies. archive.py:226
      need(eq(record, wanted), 'host dependency provenance mismatch', 'E_LOCK_INVALID');
    }
  }
}

const PROVENANCE_FIELDS = new Set(['schema', 'sha256', 'name', 'version', 'requested_commit', 'registry_release', 'commit_verifiable']);

/** Validate a retained role lock without requiring the original input file. archive.py:229-244 */
export function validate_provenance(record: PyValue, config: PyValue, pin: PyValue): PyValue {
  const artifact = subscript(subscript(config, 'cook'), 'framework_archive');
  const field = (key: string): PyValue => item(record as PyDict, key);
  // row 39 cook.provenance: a retained framework record is closed and equals what config and pin imply. archive.py:233-241
  const valid = isDict(record) && keysEqual(record, PROVENANCE_FIELDS)
    && eq(field('schema'), 'connectome.framework-archive/v1')
    && eq(field('sha256'), subscript(artifact, 'sha256')) && eq(field('name'), FRAMEWORK)
    && version(field('version')) && !credential_shaped(field('version')) && eq(field('requested_commit'), pin)
    && typeof field('registry_release') === 'boolean' && typeof field('commit_verifiable') === 'boolean'
    && field('registry_release') === (dictGet(artifact, 'registry_release', null) === true)
    && (field('commit_verifiable') === true || field('registry_release') === true)
    && (field('registry_release') !== true || (eq(field('name'), dictGet(artifact, 'name', null))
      && eq(field('version'), dictGet(artifact, 'version', null))));
  if (!valid) {
    // row 39 cook.provenance: one closed refusal. archive.py:243
    throw new ArchiveError('framework provenance lock mismatch', 'E_LOCK_INVALID');
  }
  return record;
}
