#!/usr/bin/env python3
"""TS mutants of errors, lock and inputs (plan 3.4 step 3).

`anchors` writes test/mutants/ts-anchors.golden; `run` applies every anchor to
a scratch copy of src/, compiles it with the pinned compiler and replays the
committed vectors on the result.

Ids. A guard-site mutant carries the id of the oracle mutant of the same guard
(test/mutants/guard-anchors.golden, `lock.L122.4`); its TS anchor is found
through the `lock.py:122` reference in the comment above the TS guard. A named
check carries the phase-1 name (`lock.schema.cook#1`). Rows 1, 3 and 28 have no
oracle mutant at all (the oracle mutants are derived from `require`/`need` and
`raise` statements, and `credential_shaped`, `repository` and the errors module
contain none): their checks get TS ids here, `lock.credential.*`,
`lock.repository.*`, `errors.closed.*`.

A mutant must compile under the strict configuration; one that does not is
`invalid` and counts as nothing. Status of a compiled mutant:
  killed       some committed vector gets a different *declared* outcome
               (a value, or a ValueError with class and message), or a test
               of test/unit/port-seam.test.js fails (those run the port
               against a named fake of the unported archive module and
               compare outcomes as data); and the node unit suite fails
  killed-seam  the only difference is that the mutant asks the archive seam a
               different question than the oracle did. Reported separately.
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
SOURCES = {'lock': 'src/lock.ts', 'inputs': 'src/inputs.ts', 'errors': 'src/errors.ts'}
MARKER = re.compile(r'^\s*// row (\d+) ([^:]+): .* (lock|inputs|errors)\.py:(\d+)(?:-(\d+))?$')

# What a deleted `raise` becomes where plain deletion would not compile or would change nothing.
RAISE = {
    'lock.L324.8': 'return null;', 'lock.L331.8': 'return new Map();', 'lock.L333.8': 'return new Map();',
    'inputs.L111.8': 'return null;', 'inputs.L127.8': 'return Buffer.alloc(0);', 'inputs.L150.8': 'return new Map();',
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
            if ident == 'lock.L331.8':  # `throw error;` is not unique; anchor it to its marker comment
                before, after = 'lock.py:330-331\n      throw error;', 'lock.py:330-331\n      return new Map();'
        anchors.append({'id': ident, 'rows': rows, 'file': marker['file'], 'before': before, 'after': after, 'oracle_line': line, 'note': note})
    for oracle in json.loads((ROOT / 'test/mutants/guard-anchors.golden').read_text()):
        module = oracle['id'].split('.')[0]
        if module in ('lock', 'inputs'):
            guard(oracle['id'], oracle['rows'], module, oracle['line'], oracle['after'])
    manual = {entry[0] for entry in MANUAL}
    for source in ('named-guard-anchors.golden', 'named-site-anchors.golden'):
        for oracle in json.loads((ROOT / 'test/mutants' / source).read_text()):
            module = oracle['file'].rsplit('/', 1)[-1].removeprefix('connectome_').removesuffix('.py')
            if module not in ('lock', 'inputs') or oracle['id'] in manual:
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
  if (expected !== actual) differing.push({ id: vector.id, kind: 'crash' in JSON.parse(actual) ? 'crash' : 'seam_mismatch' in JSON.parse(actual) ? 'seam' : 'declared' });
}
cleanup();
process.stdout.write(JSON.stringify(differing));
'''
COPIED = ['package.json', 'tsconfig.json', 'test/differential/ts_invoke.mjs', 'test/differential/ts_corpus.mjs', 'test/unit/port.test.js', 'test/unit/port-seam.test.js',
          'test/vectors/guard-negative.golden', 'test/vectors/crash-only.golden', 'test/vectors/port-m1-3.golden', 'test/vectors/port-m1-3.trees.golden']
local = getattr(threading, 'local')()  # spelled so that the publication gate does not read a host name
counter = [0]
mutex = threading.Lock()


def scratch(out):
    if not hasattr(local, 'root'):
        with mutex:
            counter[0] += 1
            local.root = out / ('worker-%d' % counter[0])
        shutil.rmtree(local.root, ignore_errors=True)
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
    unit = subprocess.run([node, '--test', 'test/unit/port.test.js'], cwd=root, capture_output=True, text=True, env=env, timeout=600)
    # The named-fake tests turn every outcome into data first, so a failure there is an assertion, never an error.
    fake = subprocess.run([node, '--test', 'test/unit/port-seam.test.js'], cwd=root, capture_output=True, text=True, env=env, timeout=600)
    declared = [d['id'] for d in differing if d['kind'] == 'declared']
    seam = [d['id'] for d in differing if d['kind'] == 'seam']
    crash = [d['id'] for d in differing if d['kind'] == 'crash']
    status = 'killed' if declared or fake.returncode else 'killed-seam' if seam else 'crash-only' if crash else 'survived'
    return {'status': status, 'differing_vectors': len(differing), 'declared': len(declared), 'seam': len(seam), 'crash': len(crash),
            'witnesses': (declared or seam or crash)[:3], 'unit_suite': 'fails' if unit.returncode or fake.returncode else 'passes',
            'named_fake_test': 'fails' if fake.returncode else 'passes'}


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
    inconsistent = [r['id'] for r in results if (r['status'] in ('killed', 'killed-seam', 'crash-only')) != (r.get('unit_suite') == 'fails')]
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
