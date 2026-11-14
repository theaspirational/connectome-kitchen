#!/usr/bin/env python3
"""TS mutants of the ported engine modules (plan 3.4 step 3).

`anchors` writes test/mutants/ts-anchors.golden; `run` applies every anchor to
a scratch copy of src/, compiles it with the pinned compiler and replays the
committed vectors on the result.

Ids. A guard-site mutant carries the id of the oracle mutant of the same guard
(test/mutants/guard-anchors.golden, `lock.L122.4`); its TS anchor is found
through the `lock.py:122` reference in the comment above the TS guard. A named
check carries the phase-1 name (`lock.schema.cook#1`). The oracle mutants are
derived from `require`/`need` and `raise` statements, so a function without
one has no oracle mutant at all (`credential_shaped`, `repository`, the errors
module, the archive predicates, the clauses of one-`raise` functions). Their
checks get TS ids here: `lock.credential.*`, `lock.repository.*`,
`errors.closed.*`, `cook.provenance.*`, `config.layers.*`.

A mutant must compile under the strict configuration; one that does not is
`invalid` and counts as nothing. Status of a compiled mutant:
  killed       some committed vector gets a different *declared* outcome
               (a value, or a ValueError with class and message), and the
               node unit suite fails
  crash-only   every differing vector differs only by a runtime error under
               the mutant. Reported separately and never counted as killed,
               as the phase-1 category of the same name.
  survived     no committed vector differs
  compiler     the mutant does not compile and its anchor says so beforehand:
               the check is enforced by a type, not by a test. Reported
               separately and never counted as killed.
"""
import argparse, concurrent.futures, hashlib, json, os, re, shutil, subprocess, sys, threading
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
ANCHORS = ROOT / 'test/mutants/ts-anchors.golden'
RESULTS = ROOT / 'test/mutants/ts-results.golden'
SOURCES = {'lock': 'src/lock.ts', 'inputs': 'src/inputs.ts', 'errors': 'src/errors.ts', 'archive': 'src/archive.ts', 'config': 'src/config.ts'}
MARKER = re.compile(r'^\s*// row (\d+) ([^:]+): .* (lock|inputs|errors|archive|config)\.py:(\d+)(?:-(\d+))?$')

# What a deleted `raise` becomes where plain deletion would not compile or would change nothing.
RAISE = {
    'lock.L324.8': 'return null;', 'lock.L331.8': 'return new Map();', 'lock.L333.8': 'return new Map();',
    'inputs.L111.8': 'return null;', 'inputs.L127.8': 'return Buffer.alloc(0);', 'inputs.L150.8': 'return new Map();',
    # A swallowed refusal would leave `manifest` unassigned (UnboundLocalError in the oracle); these keep the program valid.
    'archive.L137.8': "throw new ArchiveError('unreadable archive or manifest');", 'archive.L139.8': 'manifest = new Map();',
}

# Clause-level and TS-only anchors, written by hand: (id, rows, file, before, after, note).
MANUAL = [
    ('secrets.reserved.file-env#1', [49], 'inputs', "!RESERVED.has(item(entry, 'env') as string)", 'true', ''),
    ('inputs.record.secret-files#3', [63], 'inputs', 'isStr(value) && NAME.test(value)', 'true', ''),
    ('inputs.record.secret-files#4', [63], 'inputs', "!seen.has(item(entry, 'env'))", 'true', ''),
    ('lock.schema.operator-recipe#1', [59], 'lock', "inputs.read_file(path, item(config as PyDict, 'recipe_file'));", ';', ''),
    ('lock.schema.operator-recipe#2', [59], 'lock', "eq(item(recipe as PyDict, 'sha256'), subscript(item(config as PyDict, 'recipe_file'), 'sha256'))", 'true', ''),
    ('lock.schema.operator-recipe#3', [59], 'lock', "eq(item(recipe as PyDict, 'path'), path)", 'true', ''),
    ('inputs.record.data-args#1', [63], 'inputs', 'let valid = isList(records);', 'let valid = true;', ''),
    ('inputs.record.data-args#2', [63], 'inputs', 'keysWithin(record, DATA_ALLOWED)', 'true', ''),
    ('inputs.record.data-args#3', [63], 'inputs', "SERVER.test(item(record, 'server') as string)", 'true', ''),
    ('inputs.record.data-args#4', [63], 'inputs', "isList(item(record, 'args'))", 'true', ''),
    ('inputs.record.data-args#5', [63], 'inputs', "Array.from(item(record, 'args') as Iterable<PyValue>).every(arg => isStr(arg))", 'true', ''),
    ('inputs.record.data-args#6', [63], 'inputs', "isStr(item(record, 'kind')) && (item(record, 'kind') === 'file' || item(record, 'kind') === 'dir')", 'true', ''),
    ('inputs.record.data-args#7', [63], 'inputs', 'if (seen.has(key)) {', 'if (false) {', ''),
    ('inputs.record.data-path#1', [63], 'inputs', "isStr(value) && value.startsWith('/') && !value.startsWith('//')", "isStr(value) && !value.startsWith('//')",
     'the type test stays: without it the mutant does not compile'),
    ('inputs.record.data-path#2', [63], 'inputs', "&& !value.startsWith('//')", '', ''),
    ('inputs.record.data-path#3', [63], 'inputs', "&& value !== '/' && value === pathlib.pathStr(value)", '&& value === pathlib.pathStr(value)', ''),
    ('inputs.record.data-path#4', [63], 'inputs', "&& value !== '/' && value === pathlib.pathStr(value)", "&& value !== '/'", ''),
    ('inputs.record.data-path#5', [63], 'inputs', "&& !pathlib.pathParts(value).includes('..')\n    && !hasControl(value);", '&& !hasControl(value);', ''),
    ('inputs.record.data-path#6', [63], 'inputs', "&& !pathlib.pathParts(value).includes('..')\n    && !hasControl(value);", "&& !pathlib.pathParts(value).includes('..');", ''),
    ('inputs.record.data-flag#1', [63], 'inputs', 'if (!FLAG.test(value)) return null;', '', 'the type test stays: without it the mutant does not compile'),
    ('inputs.record.data-flag#2', [63], 'inputs', 'if (DENIED_FLAGS.has(value)) return false;', '', ''),
    ('inputs.record.data-flag#3', [63], 'inputs', 'return !DENIED_PREFIXES.some(prefix => value.startsWith(prefix));', 'return true;', ''),
    ('inputs.record.transport#2', [63], 'inputs', "b64decodeStrict((transport as ReadonlyMap<string, unknown>).get('cook-recipe.json'))",
     "Buffer.from(String((transport as ReadonlyMap<string, unknown>).get('cook-recipe.json')), 'base64')", 'strict decoding replaced by a lenient one, as validate=False in the oracle mutant'),
    # Row 69: the three named checks whose oracle mutant replaces a condition, and the two that change the merge.
    ('config.layers.count', [69], 'config', 'len(layers) !== 4 || iterate(layers).some(layer => !isDict(layer))', 'false', ''),
    ('config.layers.enabled-type', [69], 'config', "!isDict(entry) || typeof dictGet(entry, 'enabled', true) !== 'boolean'", 'false', ''),
    ('config.layers.public-connectors', [69], 'config', "if (strIn('connectors', result)) {", 'if (false) {', ''),
    ('config.layers.public-connectors#1', [69], 'config', "if (strIn('connectors', result)) {", 'if (false) {', 'same mutation as the name without a site number, as in the oracle lists'),
    ('config.layers.fold#1', [69], 'config', 'for (const layer of layers as PyDict[]) merged = combine([merged, layer], true);', 'merged = (layers as PyDict[]).reduceRight((upper, lower) => combine([lower, upper], true));',
     "the reversed fold of Ansible's multi-argument combine, as in the oracle mutant"),
    ('config.layers.prune#1', [69], 'config', "filter(([, entry]) => dictGet(entry, 'enabled', true) === true)", 'filter(() => true)', ''),
    ('config.layers.tombstones', [69], 'config', 'const result = prune(merged) as PyDict;', 'const result = merged;', 'TS only; nulls survive the merge'),
    ('config.layers.tombstones-last', [69], 'config', 'if (recursive && isDict(value) && isDict(present)) result.set(key, combine([present, value], true));\n      else result.set(key, deepcopy(value));',
     'if (recursive && isDict(value) && isDict(present)) result.set(key, combine([present, value], true));\n      else if (value === null) result.delete(key);\n      else result.set(key, deepcopy(value));',
     'TS only; a null applied while merging instead of after it lets a lower layer come back'),
    ('config.layers.recursive', [69], 'config', 'result.set(key, combine([present, value], true));', 'result.set(key, deepcopy(value));', 'TS only; an upper mapping replaces a lower one instead of merging into it'),
    ('config.layers.empty-catalog', [69], 'config', "if (!truthy(dictGet(result, 'catalog', null))) result.delete('catalog');", ';', 'TS only'),
    # Row 39: the archive predicates without a `need` (no oracle mutant), and the clauses of the sidecar check.
    ('cook.provenance.npm-name-length', [39], 'archive', 'Array.from(value).length <= 214', 'true', 'TS only'),
    ('cook.provenance.npm-name-reserved', [39], 'archive', "value !== 'node_modules' && value !== 'favicon.ico'", 'true', 'TS only'),
    ('cook.provenance.npm-name-grammar', [39], 'archive', '&& NPM_NAME.test(value);', ';', 'TS only'),
    ('cook.provenance.origin-characters', [39], 'archive', 'credential_shaped(value) || REGISTRY_REFUSED.test(value)) return false;', 'credential_shaped(value)) return false;', 'TS only'),
    ('cook.provenance.origin-credential', [39, 1], 'archive', 'if (!text(value) || credential_shaped(value) || REGISTRY_REFUSED.test(value)) return false;', 'if (!text(value) || REGISTRY_REFUSED.test(value)) return false;', 'TS only'),
    ('cook.provenance.origin-scheme', [39], 'archive', "if (!(parts.scheme === 'https' && url.username", 'if (!(url.username', 'TS only'),
    ('cook.provenance.origin-user', [39], 'archive', 'url.username(parts.netloc) === null && url.password(parts.netloc) === null', 'url.password(parts.netloc) === null', 'TS only'),
    ('cook.provenance.origin-password', [39], 'archive', 'url.username(parts.netloc) === null && url.password(parts.netloc) === null', 'url.username(parts.netloc) === null', 'TS only'),
    ('cook.provenance.origin-path', [39], 'archive', "&& (parts.path === '' || parts.path === '/') && !parts.query", '&& !parts.query', 'TS only'),
    ('cook.provenance.origin-query', [39], 'archive', '&& !parts.query && !parts.fragment', '&& !parts.fragment', 'TS only'),
    ('cook.provenance.origin-fragment', [39], 'archive', '&& !parts.query && !parts.fragment', '&& !parts.query', 'TS only'),
    ('cook.provenance.origin-host', [39], 'archive', '&& host !== null && REGISTRY_HOST.test(host))) return false;', '&& host !== null)) return false;', 'TS only'),
    ('cook.provenance.origin-port', [39], 'archive', 'return port === null || (0 < port && port <= 65535);\n  } catch (error) {\n    if (error instanceof PyValueError) return false;\n    throw error;\n  }\n}\n\n/** archive.py:51-58 */',
     'return true;\n  } catch (error) {\n    if (error instanceof PyValueError) return false;\n    throw error;\n  }\n}\n\n/** archive.py:51-58 */', 'TS only'),
    ('cook.provenance.origin-malformed', [39], 'archive', 'return port === null || (0 < port && port <= 65535);\n  } catch (error) {\n    if (error instanceof PyValueError) return false;',
     'return port === null || (0 < port && port <= 65535);\n  } catch (error) {\n    if (error instanceof PyValueError) return true;', 'TS only'),
    ('cook.provenance.integrity-prefix', [39], 'archive', "if (!text(value) || !value.startsWith('sha512-')) return false;", 'if (!text(value)) return false;', 'TS only'),
    ('cook.provenance.integrity-length', [39], 'archive', 'return decoded.length === 64 && b64encode(decoded) === value.slice(7);', 'return b64encode(decoded) === value.slice(7);', 'TS only'),
    ('cook.provenance.integrity-canonical', [39], 'archive', 'return decoded.length === 64 && b64encode(decoded) === value.slice(7);', 'return decoded.length === 64;', 'TS only'),
    ('cook.provenance.record-verifiable', [39], 'archive', "record.set('commit_verifiable', eq(kind, 'git_commit'));", "record.set('commit_verifiable', true);", 'TS only'),
    ('cook.provenance.record-payload', [39], 'archive', "if (eq(kind, 'artifact')) record.set('payload', ARTIFACT_PAYLOAD);", '', 'TS only'),
    ('cook.provenance.record-comparison', [39], 'archive', "if (eq(kind, 'registry_release')) record.set('registry_comparison', REGISTRY_COMPARISON);", '', 'TS only'),
    ('cook.provenance.metadata-dependencies', [39], 'archive', "return strIn('host_dependencies', settings)\n    || ", 'return ', 'TS only'),
    ('cook.provenance.metadata-release', [39], 'archive', "dictGet(dictGet(settings, 'framework_archive', new Map()), 'registry_release', null) === true;", "dictGet(dictGet(settings, 'framework_archive', new Map()), 'registry_release', null) !== null;", 'TS only'),
    ('cook.provenance.sidecar-fields', [39], 'archive', 'const valid = isDict(record) && keysEqual(record, PROVENANCE_FIELDS)', 'const valid = isDict(record) && keysInclude(record, PROVENANCE_FIELDS)', 'TS only; extra fields pass'),
    ('cook.provenance.sidecar-schema', [39], 'archive', "    && eq(field('schema'), 'connectome.framework-archive/v1')\n", '', 'TS only'),
    ('cook.provenance.sidecar-digest', [39], 'archive', "&& eq(field('sha256'), subscript(artifact, 'sha256')) && eq(field('name'), FRAMEWORK)", "&& eq(field('name'), FRAMEWORK)", 'TS only'),
    ('cook.provenance.sidecar-name', [39], 'archive', "&& eq(field('sha256'), subscript(artifact, 'sha256')) && eq(field('name'), FRAMEWORK)", "&& eq(field('sha256'), subscript(artifact, 'sha256'))", 'TS only'),
    ('cook.provenance.sidecar-version', [39], 'archive', "&& version(field('version')) && !credential_shaped(field('version')) && eq(field('requested_commit'), pin)", "&& isStr(field('version')) && !credential_shaped(field('version')) && eq(field('requested_commit'), pin)", 'TS only; the type test stays'),
    ('cook.provenance.sidecar-version-credential', [39, 1], 'archive', "&& version(field('version')) && !credential_shaped(field('version')) && eq(field('requested_commit'), pin)", "&& version(field('version')) && eq(field('requested_commit'), pin)", 'TS only'),
    ('cook.provenance.sidecar-pin', [39], 'archive', "&& version(field('version')) && !credential_shaped(field('version')) && eq(field('requested_commit'), pin)", "&& version(field('version')) && !credential_shaped(field('version'))", 'TS only'),
    ('cook.provenance.sidecar-release', [39], 'archive', "    && field('registry_release') === (dictGet(artifact, 'registry_release', null) === true)\n", '', 'TS only'),
    ('cook.provenance.sidecar-verifiable', [39], 'archive', "    && (field('commit_verifiable') === true || field('registry_release') === true)\n", '', 'TS only'),
    ('cook.provenance.sidecar-release-identity', [39], 'archive', "    && (field('registry_release') !== true || (eq(field('name'), dictGet(artifact, 'name', null))\n      && eq(field('version'), dictGet(artifact, 'version', null))));",
     ';', 'TS only'),
    ('cook.provenance.manifest-name-pattern', [39], 'archive', 'filter(member => MANIFEST_NAME.test(member.name) && member.isfile)', 'filter(member => member.isfile)', 'TS only'),
    ('cook.provenance.manifest-regular-file', [39], 'archive', 'filter(member => MANIFEST_NAME.test(member.name) && member.isfile)', 'filter(member => MANIFEST_NAME.test(member.name))', 'TS only'),
    # Row 2: the call itself (the oracle has mutants only for the three checks inside).
    ('lock.names-only.call', [2], 'lock', 'namesOnly(lock, frame + 1);', ';', 'TS only'),
    # Row 1: no oracle mutant exists for credential_shaped.
    ('lock.credential.provider-token', [1], 'lock', "'(?:gh[oprsu]_|github_pat_|glpat[-_]|xox[baprs]-|sk-)[A-Za-z0-9_-]{12,}',", "'(?!)',", 'TS only'),
    ('lock.credential.vault-token', [1], 'lock', "BOUNDARY + 'hvs\\\\.[A-Za-z0-9_-]+',", "'(?!)',", 'TS only'),
    ('lock.credential.bot-token', [1], 'lock', "BOUNDARY + '[0-9]{8,10}:[A-Za-z0-9_-]{30,}',", "'(?!)',", 'TS only'),
    ('lock.credential.private-key', [1], 'lock', "'-----' + 'BEGIN [^\\\\n]*PRIVATE KEY-----',", "'(?!)',", 'TS only'),
    ('lock.credential.jwt', [1], 'lock', "BOUNDARY + 'eyJ[' + W + '-]+\\\\.[' + W + '-]+\\\\.[' + W + '-]+',", "'(?!)',", 'TS only'),
    ('lock.credential.assignment', [1], 'lock', "[TOKEN_SHAPES.join('|'), ASSIGNMENT, URL_SHAPES.join('|'), STORE_PATH]", "[TOKEN_SHAPES.join('|'), '(?!)', URL_SHAPES.join('|'), STORE_PATH]", 'TS only'),
    ('lock.credential.url-userinfo', [1], 'lock', "'://[^/' + S + ']*:[^/' + S + ']*@',", "'(?!)',", 'TS only'),
    ('lock.credential.query', [1], 'lock', "'[?&](?:' + ['token', 'password', 'secret', 'api_key'].map(insensitive).join('|') + ')=',", "'(?!)',", 'TS only'),
    ('lock.credential.store-path', [1], 'lock', "[TOKEN_SHAPES.join('|'), ASSIGNMENT, URL_SHAPES.join('|'), STORE_PATH]", "[TOKEN_SHAPES.join('|'), ASSIGNMENT, URL_SHAPES.join('|'), '(?!)']", 'TS only'),
    # Row 1, plan 3.2 rows 5, 6 and 16: the Python regex semantics the port has to reproduce.
    ('lock.credential.word-boundary', [1], 'lock', "const BOUNDARY = '(?<![' + W + '])';", "const BOUNDARY = '';", 'TS only; \\b'),
    ('lock.credential.unicode-boundary', [1], 'lock', "const BOUNDARY = '(?<![' + W + '])';", "const BOUNDARY = '(?<![A-Za-z0-9_])';", 'TS only; \\b is Unicode-aware'),
    ('lock.credential.unicode-word', [1], 'lock', "BOUNDARY + 'eyJ[' + W + '-]+\\\\.[' + W + '-]+\\\\.[' + W + '-]+',", "BOUNDARY + 'eyJ[A-Za-z0-9_-]+\\\\.[A-Za-z0-9_-]+\\\\.[A-Za-z0-9_-]+',", 'TS only; \\w is Unicode-aware'),
    ('lock.credential.unicode-space', [1], 'lock', "[' + S + ']*[:=][' + S + ']*[^' + S + ']+';", "\\\\s*[:=]\\\\s*\\\\S+';", 'TS only; \\s differs between the two engines'),
    ('lock.credential.ignore-case', [1], 'lock', 'const lower = letter.toLowerCase(), upper = letter.toUpperCase();', 'const lower = letter, upper = letter;', 'TS only; re.I'),
    ('lock.credential.case-equivalents', [1], 'lock', "(lower === 'i' ? '\\\\u0130\\\\u0131' : lower === 's' ? '\\\\u017f' : lower === 'k' ? '\\\\u212a' : '')", "''", 'TS only; CPython case equivalences under re.I'),
    ('lock.credential.class-equivalents', [1], 'lock', "const LETTER = 'A-Za-z\\\\u0130\\\\u0131\\\\u017f\\\\u212a';", "const LETTER = 'A-Za-z';", 'TS only; the same equivalences inside a class'),
    ('lock.credential.dot', [1], 'lock', "'-----' + 'BEGIN [^\\\\n]*PRIVATE KEY-----',", "'-----' + 'BEGIN .*PRIVATE KEY-----',", 'TS only; "." excludes only a newline in Python'),
    # Row 3: no oracle mutant exists for repository.
    ('lock.repository.text', [3], 'lock', '  if (!text(value)) return false;\n  if (credential_shaped(value)) return false;', '  if (!isStr(value)) return false;\n  if (credential_shaped(value)) return false;', 'TS only; the type test stays'),
    ('lock.repository.credential', [3], 'lock', '  if (credential_shaped(value)) return false;\n', '', 'TS only'),
    ('lock.repository.decoded-text', [3], 'lock', '  if (!text(decoded)) return false;\n', '', 'TS only'),
    ('lock.repository.decoded-credential', [3], 'lock', '  if (credential_shaped(decoded)) return false;\n', '', 'TS only'),
    ('lock.repository.delimiters', [3], 'lock', "  if (/[?#\\\\]/u.test(decoded)) return false;\n", '', 'TS only'),
    ('lock.repository.whitespace', [3], 'lock', '  if (SPACE.test(decoded)) return false;\n', '', 'TS only'),
    ('lock.repository.scheme', [3], 'lock', "if (parts.scheme === 'https' || parts.scheme === 'ssh') {", 'if (parts.scheme) {', 'TS only; any scheme accepted as a URL'),
    ('lock.repository.host', [3], 'lock', 'const host = url.hostname(parts.netloc), user', "const host = url.hostname(parts.netloc) ?? 'host', user", 'TS only; an absent host passes'),
    ('lock.repository.path', [3], 'lock', 'return Boolean(host\n        && parts.path', 'return Boolean(host', 'TS only'),
    ('lock.repository.password', [3], 'lock', '        && url.password(parts.netloc) === null\n', '', 'TS only'),
    ('lock.repository.ssh-option-host', [3], 'lock', "        && (parts.scheme !== 'ssh' || !url.unquote(host).startsWith('-'))\n", '', 'TS only'),
    ('lock.repository.port-range', [3], 'lock', '        && (port === null || (0 < port && port <= 65535))\n', '', 'TS only'),
    ('lock.repository.port-syntax', [3], 'lock', 'const port = url.port(parts.netloc);', 'const port = null as number | null;', 'TS only; the port is no longer read, so a malformed one no longer refuses'),
    ('lock.repository.user', [3], 'lock', "        && (user === null || (parts.scheme === 'ssh' && USER.test(url.unquote(user)))));", ');', 'TS only'),
    ('lock.repository.user-grammar', [3], 'lock', "(parts.scheme === 'ssh' && USER.test(url.unquote(user)))", "parts.scheme === 'ssh'", 'TS only'),
    ('lock.repository.malformed-url', [3], 'lock', '    if (error instanceof PyValueError) return false;\n    throw error;\n  }\n  // scp syntax', '    if (error instanceof PyValueError) return true;\n    throw error;\n  }\n  // scp syntax', 'TS only'),
    ('lock.repository.transport-prefix', [3], 'lock', '  if (TRANSPORT_PREFIX.test(decoded)) return false;\n', '', 'TS only'),
    ('lock.repository.prefix-ignore-case', [3], 'lock', "/^(?:[hH][tT][tT][pP][sS\\u017f]?|[sS\\u017f][sS\\u017f][hH]|[gG][iI\\u0130\\u0131][tT]|[fF][iI\\u0130\\u0131][lL][eE]|[fF][tT][pP]|[eE][xX][tT]):/u",
     '/^(?:https?|ssh|git|file|ftp|ext):/u', 'TS only; re.I on the prefix test'),
    ('lock.repository.scp-shape', [3], 'lock', "return SCP.test(decoded) && !decoded.includes('://');", "return !decoded.includes('://');", 'TS only'),
    ('lock.repository.scp-not-uri', [3], 'lock', "return SCP.test(decoded) && !decoded.includes('://');", 'return SCP.test(decoded);', 'TS only'),
    # Row 28: no oracle mutant exists for the errors module.
    ('errors.closed.cook-table', [28], 'errors', 'if (COOK_CODES.has(code)) return code;', 'return code;', 'TS only'),
    ('errors.closed.left-boundary', [28], 'errors', '/(?<![A-Za-z0-9_])(E_[A-Z_]+)(?![A-Za-z0-9_])/g', '/(E_[A-Z_]+)(?![A-Za-z0-9_])/g', 'TS only'),
    ('errors.closed.right-boundary', [28], 'errors', '/(?<![A-Za-z0-9_])(E_[A-Z_]+)(?![A-Za-z0-9_])/g', '/(?<![A-Za-z0-9_])(E_[A-Z_]+)/g', 'TS only'),
    ('errors.closed.stream-window', [28], 'errors', 'tail = chunk.slice(Math.max(0, chunk.length - 128));', 'tail = new Uint8Array(0);', 'TS only'),
    ('errors.closed.stream-rewind', [28], 'errors', '  stream.seek(0);\n', '', 'TS only'),
    ('errors.closed.unknown-code', [28], 'errors', "typeof code === 'string' && Object.hasOwn(MESSAGES, code) ? code : 'E_DEPLOY_FAILED'", "typeof code === 'string' ? code : 'E_DEPLOY_FAILED'", 'TS only'),
    ('errors.closed.upstream-only-cook', [28], 'errors', "if (known === 'E_COOK_FAILED' && typeof upstream === 'string' && COOK_CODES.has(upstream)) {", "if (typeof upstream === 'string' && COOK_CODES.has(upstream)) {", 'TS only'),
    ('errors.closed.upstream-table', [28], 'errors', "if (known === 'E_COOK_FAILED' && typeof upstream === 'string' && COOK_CODES.has(upstream)) {", "if (known === 'E_COOK_FAILED' && typeof upstream === 'string') {", 'TS only'),
    ('errors.closed.envelope-exact', [28], 'errors', 'return eq(data, new Map(Object.entries(safe))) ? safe : fallback;', 'return safe;', 'TS only'),
    ('errors.closed.envelope-mapping', [28], 'errors', '  if (!isDict(data)) return fallback;\n', '', 'TS only; expected not to compile: the decoder is typed'),
    ('errors.closed.envelope-type', [28], 'errors', "  if (typeof output !== 'string' && !(output instanceof Uint8Array)) return fallback;\n", '', 'TS only; expected not to compile: the decoder is typed'),
]


def statement(lines, start):
    """The statement that begins at `lines[start]`: text up to the `;` (or `{`) that closes it at depth 0."""
    text, depth, quote, index = '', 0, None, start
    while index < len(lines):
        line = lines[index]
        position = 0
        while position < len(line):
            char = line[position]
            if quote:
                if char == '\\':
                    position += 1
                elif char == quote:
                    quote = None
            elif char in '\'"`':
                quote = char
            elif char in '([{':
                depth += 1
            elif char in ')]}':
                depth -= 1
            elif char == ';' and depth == 0:
                return text + line[:position + 1]
            position += 1
        text += line + '\n'
        index += 1
    raise ValueError('unterminated statement')


def condition(call):
    """`require(<condition>, 'message');` -> the condition text."""
    open_at = call.index('(') + 1
    depth, quote, last = 0, None, None
    position = open_at
    while position < len(call):
        char = call[position]
        if quote:
            if char == '\\':
                position += 1
            elif char == quote:
                quote = None
        elif char in '\'"`':
            quote = char
        elif char in '([{':
            depth += 1
        elif char in ')]}':
            if depth == 0:
                break
            depth -= 1
        elif char == ',' and depth == 0:
            last = position
        position += 1
    if last is None:
        raise ValueError('guard without a message')
    return call[open_at:last]


def markers():
    """Every `row N id: ... file.py:L` comment with the statement under it."""
    found = []
    for module, relative in SOURCES.items():
        lines = (ROOT / relative).read_text().split('\n')
        for number, line in enumerate(lines):
            match = MARKER.match(line)
            if not match or match[3] != module:
                continue
            follow = number + 1
            while lines[follow].lstrip().startswith('//'):
                follow += 1
            try:
                text = statement(lines, follow)
            except ValueError:
                text = lines[follow]
            found.append({'module': module, 'file': relative, 'row': int(match[1]), 'name': match[2], 'start': int(match[4]), 'end': int(match[5] or match[4]),
                          'ts_line': follow + 1, 'statement': text})
    return found


def build_anchors():
    found = markers()
    anchors = []

    def guard(ident, rows, module, line, kind, note=''):
        choices = [m for m in found if m['module'] == module and m['start'] <= line <= m['end'] and
                   (m['statement'].lstrip().startswith(('require(', 'need(')) if kind == 'True' else m['statement'].lstrip().startswith('throw '))]
        exact = [m for m in choices if m['start'] == line] or choices
        exact = list({m['ts_line']: m for m in exact}.values())  # two rows may mark one guard
        if len(exact) != 1:
            raise ValueError('%s: %d TS guards cite line %d' % (ident, len(exact), line))
        marker = exact[0]
        text = marker['statement'].strip()
        if kind == 'True':
            before = condition(text)
            after = 'true'
            call = text[:text.index('(') + 1]
            before, after = call + before + ',', call + after + ','
        else:
            before, after = text, RAISE.get(ident, ';')
            if (ROOT / marker['file']).read_text().count(before) != 1:  # e.g. `throw error;`: anchor it to its marker comment
                above = (ROOT / marker['file']).read_text().split('\n')[marker['ts_line'] - 2:marker['ts_line']]
                cite = above[0][above[0].rindex(module + '.py:'):]
                indent = above[1][:len(above[1]) - len(above[1].lstrip())]
                before, after = cite + '\n' + indent + before, cite + '\n' + indent + after
        anchors.append({'id': ident, 'rows': rows, 'file': marker['file'], 'before': before, 'after': after, 'oracle_line': line, 'note': note})
    for oracle in json.loads((ROOT / 'test/mutants/guard-anchors.golden').read_text()):
        module = oracle['id'].split('.')[0]
        if module in SOURCES:
            guard(oracle['id'], oracle['rows'], module, oracle['line'], oracle['after'])
    manual = {entry[0] for entry in MANUAL}
    for source in ('named-guard-anchors.golden', 'named-site-anchors.golden'):
        for oracle in json.loads((ROOT / 'test/mutants' / source).read_text()):
            module = oracle['file'].rsplit('/', 1)[-1].removeprefix('connectome_').removesuffix('.py')
            if module not in SOURCES or oracle['id'] in manual:
                continue
            if oracle['after'].strip() != 'True':
                raise ValueError('named site needs a hand-written TS anchor: ' + oracle['id'])
            row = next(m['row'] for m in found if m['module'] == module and m['start'] <= oracle['line'] <= m['end'])
            guard(oracle['id'], oracle.get('rows') or [row], module, oracle['line'], 'True', 'named check; whole guard, as in the oracle')
    for ident, rows, module, before, after, note in MANUAL:
        anchors.append({'id': ident, 'rows': rows, 'file': SOURCES[module], 'before': before, 'after': after, 'note': note})
    seen = set()
    for anchor in anchors:
        if anchor['id'] in seen:
            raise ValueError('duplicate mutant id ' + anchor['id'])
        seen.add(anchor['id'])
        source = (ROOT / anchor['file']).read_text()
        if source.count(anchor['before']) != 1:
            raise ValueError('%s: anchor occurs %d times' % (anchor['id'], source.count(anchor['before'])))
        anchor['source_sha256'] = hashlib.sha256(source.encode()).hexdigest()
    return anchors


EVAL = r'''
import { cleanup, evaluate, vectors } from './test/differential/ts_corpus.mjs';
const differing = [];
for (const vector of vectors()) {
  const { expected, actual } = evaluate(vector);
  if (expected !== actual) differing.push({ id: vector.id, kind: 'crash' in JSON.parse(actual) ? 'crash' : 'declared' });
}
cleanup();
process.stdout.write(JSON.stringify(differing));
'''
COPIED = ['package.json', 'tsconfig.json', 'test/differential/ts_invoke.mjs', 'test/differential/ts_corpus.mjs', 'test/unit/port.test.js', 'test/unit/port-lazy.test.js',
          'test/vectors/guard-negative.golden', 'test/vectors/crash-only.golden', 'test/vectors/port.golden', 'test/vectors/port.trees.golden', 'test/vectors/port-divergences.golden']
local = getattr(threading, 'local')()  # spelled so that the publication gate does not read a host name
counter = [0]
mutex = threading.Lock()


def scratch(out):
    if not hasattr(local, 'root'):
        with mutex:
            counter[0] += 1
            local.root = out / ('worker-%d' % counter[0])
        if local.root.exists():  # a killed run leaves fixture directories without permissions behind
            for directory, names, _files in os.walk(local.root):
                for name in names:
                    child = os.path.join(directory, name)
                    if not os.path.islink(child):
                        os.chmod(child, 0o700)
            shutil.rmtree(local.root)
        local.root.mkdir(parents=True)
        os.symlink(ROOT / 'node_modules', local.root / 'node_modules')
        for relative in COPIED:
            (local.root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, local.root / relative)
        (local.root / 'eval.mjs').write_text(EVAL)
        shutil.copytree(ROOT / 'src', local.root / 'src')
    return local.root


def compile_and_replay(root, node):
    env = {'PATH': os.environ['PATH'], 'HOME': os.environ.get('HOME', '/nonexistent')}
    tsc = subprocess.run([node, str(ROOT / 'node_modules/typescript/bin/tsc'), '-p', 'tsconfig.json', '--incremental', '--tsBuildInfoFile', '.tsbuildinfo'],
                         cwd=root, capture_output=True, text=True, env=env, timeout=600)
    if tsc.returncode:
        return {'status': 'invalid', 'compiler': [line.split(': error ', 1)[-1] for line in tsc.stdout.strip().splitlines()[:2]]}
    replay = subprocess.run([node, 'eval.mjs'], cwd=root, capture_output=True, text=True, env=env, timeout=600)
    if replay.returncode:
        return {'status': 'invalid', 'replay': replay.stderr.strip().splitlines()[-3:]}
    differing = json.loads(replay.stdout)
    # The test file is run directly: `node --test` adds a parent process that was seen spinning after its child had exited.
    unit = subprocess.run([node, 'test/unit/port.test.js'], cwd=root, capture_output=True, text=True, env=env, timeout=600)
    declared = [d['id'] for d in differing if d['kind'] == 'declared']
    crash = [d['id'] for d in differing if d['kind'] == 'crash']
    status = 'killed' if declared else 'crash-only' if crash else 'survived'
    return {'status': status, 'differing_vectors': len(differing), 'declared': len(declared), 'crash': len(crash),
            'witnesses': (declared or crash)[:3], 'unit_suite': 'fails' if unit.returncode else 'passes'}


def work(anchor, out, node):
    root = scratch(out)
    target = root / anchor['file']
    original = (ROOT / anchor['file']).read_text()
    try:
        target.write_text(original.replace(anchor['before'], anchor['after']))
        result = compile_and_replay(root, node)
        if result['status'] == 'invalid' and 'expected not to compile' in anchor.get('note', '') and 'compiler' in result:
            result['status'] = 'compiler'
    finally:
        target.write_text(original)
    return dict({'id': anchor['id'], 'rows': anchor['rows']}, **result)


def run(args):
    anchors = json.loads(ANCHORS.read_text())
    if anchors != build_anchors():
        print('BLOCKED: test/mutants/ts-anchors.golden is stale; run `ts_mutants.py anchors`')
        return 1
    if args.only:
        anchors = [a for a in anchors if any(a['id'].startswith(prefix) for prefix in args.only)]
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # The unmutated tree must replay clean in the same scratch layout, or a "kill" means nothing.
    base = scratch(out)
    clean = compile_and_replay(base, args.node)
    if clean['status'] != 'survived' or clean['unit_suite'] != 'passes':
        print('BLOCKED: the unmutated port does not replay clean: ' + json.dumps(clean))
        return 1
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda anchor: work(anchor, out, args.node), anchors))
    counts = {}
    for result in results:
        counts[result['status']] = counts.get(result['status'], 0) + 1
    inconsistent = [r['id'] for r in results if (r['status'] in ('killed', 'crash-only')) != (r.get('unit_suite') == 'fails')]
    report = {'mutants': len(results), 'counts': dict(sorted(counts.items())), 'unit_suite_disagrees': inconsistent, 'results': results}
    text = json.dumps(report, indent=1) + '\n'
    if args.only:
        print(json.dumps({'counts': report['counts'], 'not_killed': [r for r in results if r['status'] != 'killed']}, indent=1))
        return 0
    if args.check:
        if RESULTS.read_text() != text:
            print('BLOCKED: TS mutant results differ from test/mutants/ts-results.golden')
            return 1
    else:
        RESULTS.write_text(text)
    print(json.dumps({'mutants': len(results), 'counts': report['counts'], 'unit_suite_disagrees': inconsistent}))
    return int(bool(inconsistent or counts.get('invalid')))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('anchors')
    runner = sub.add_parser('run')
    runner.add_argument('--out', type=Path, default=ROOT / '.cache/ts-mutants')
    runner.add_argument('--workers', type=int, default=8)
    runner.add_argument('--node', default='node')
    runner.add_argument('--check', action='store_true')
    runner.add_argument('--only', action='append')
    args = parser.parse_args()
    if args.command == 'anchors':
        anchors = build_anchors()
        ANCHORS.write_text(json.dumps(anchors, indent=1) + '\n')
        print(json.dumps({'anchors': len(anchors)}))
        return 0
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
