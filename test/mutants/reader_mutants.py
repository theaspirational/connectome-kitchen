#!/usr/bin/env python3
"""Mutants of the archive reader: src/pylike/tarfile.ts and the manifest-name test of src/archive.ts.

The mutants of test/mutants/ts_mutants.py follow the guards of the engine
modules and are replayed on the module vectors; none of them is in the reader.
This set is the reader's own. Each mutant removes or weakens one check of it:
the metadata limit (D3), the admission budgets and the heap they are sized for
(D4), the runtime the stepwise gzip reader is tied to and the step record
(B9, L1), the one-pass number and name handling (F2), the pax store (F3), and
the manifest-name test.

A mutant is applied to a scratch copy of src/, compiled with the pinned
compiler under the strict configuration, and then held against the
repository's own checks of the reader:

    unit           test/unit/port-tar-budgets, port-tar-limits, port-runtime
                   and port-manifest-name
    differential   the helper differential against the oracle, kinds `tar`,
                   `tarbudget` and `pyint`, at a fraction of its size

Status:
    killed     a unit file fails, or the differential reports a mismatch or
               cannot run
    survived   neither notices it
    invalid    the mutant does not compile; it counts as nothing and fails
               the run

`run` writes test/mutants/reader-results.golden; `run --check` compares with
it. scripts/check-port-ready reads that file: a mutant that is not killed
blocks, and so do results taken from other sources than the ones in the tree.

The file holds the status of each mutant and nothing that depends on the
runtime. Which of the two checks killed a mutant does: without the pinned
Node in the list of verified runtimes the differential cannot run on Node
24.14.0 and runs as before on 22.23.3. That detail is printed by each run
(`killed_by`) and written to .cache/reader-mutants/details.json.

    python3 test/mutants/reader_mutants.py run [--check] [--workers 8] [--node node] [--only prefix]
"""
import argparse, concurrent.futures, hashlib, json, os, shutil, subprocess, sys, threading
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / 'test/mutants/reader-results.golden'
READER, ARCHIVE = 'src/pylike/tarfile.ts', 'src/archive.ts'
UNIT = ['port-tar-budgets.test.js', 'port-tar-limits.test.js', 'port-runtime.test.js', 'port-manifest-name.test.js']
DIFFERENTIAL = ['--kind', 'tar', '--kind', 'tarbudget', '--kind', 'pyint', '--scale', '0.34', '--processes', '2']
COPIED = ['package.json', 'tsconfig.json', 'test/differential/port_primitives.py', 'test/differential/port_primitives.mjs', 'test/differential/tar_budget.py'] + ['test/unit/' + name for name in UNIT]

MATCHES = 'return index < this.count && this.cells[2 * index] === length && this.cells[2 * index + 1] === crc >>> 0;'
# (id, file, before, after, what the mutant does)
MUTANTS = [
    # D3: the limit on one metadata block.
    ('reader.d3.at-limit-refused', READER, 'if (info.size > METADATA_LIMIT) throw new MetadataLimit();', 'if (info.size >= METADATA_LIMIT) throw new MetadataLimit();', 'a block of exactly the limit is refused'),
    ('reader.d3.removed', READER, 'if (info.size > METADATA_LIMIT) throw new MetadataLimit();', '', 'no limit on one block'),
    ('reader.d3.limit-doubled', READER, 'export const METADATA_LIMIT = 2n ** 28n;', 'export const METADATA_LIMIT = 2n ** 29n;', 'the limit is 2**29'),
    ('reader.d3.not-for-pax', READER, '  const buf = metadataBlocks(info, tar, frame);\n  // A pax header stores', '  const buf = tar.file.read(info.block(info.size, frame), frame);\n  // A pax header stores', 'a pax block is read without limit or budget'),
    # D4: the four budgets.
    ('reader.d4.charge.at-budget-refused', READER, 'if (amount > tar.budgets[budget] - tar.used[budget]) throw new MetadataBudget(budget);', 'if (amount >= tar.budgets[budget] - tar.used[budget]) throw new MetadataBudget(budget);', 'a count that ends exactly at its budget is refused'),
    ('reader.d4.charge.not-cumulative', READER, '  tar.used[budget] += amount;\n', '', 'each amount is held against the whole budget: nothing adds up'),
    ('reader.d4.charge.never', READER, 'if (amount > tar.budgets[budget] - tar.used[budget]) throw new MetadataBudget(budget);', '', 'no budget refuses'),
    ('reader.d4.bytes.block-uncounted', READER, "  charge(tar, 'metadataBytes', Number(info.size));\n", '', 'the bytes of long-name and pax blocks are not counted'),
    ('reader.d4.bytes.old-gnu-blocks-uncounted', READER, "    charge(tar, 'metadataBytes', BLOCK);\n    const buf = tar.file.read(BLOCK, frame);", '    const buf = tar.file.read(BLOCK, frame);', 'old GNU extension blocks are not counted as bytes'),
    ('reader.d4.bytes.sparse-1.0-blocks-uncounted', READER, "const block = (): Buffer => { charge(tar, 'metadataBytes', BLOCK); return", 'const block = (): Buffer => { return', 'the blocks of a sparse map of form 1.0 are not counted as bytes'),
    ('reader.d4.records.uncounted', READER, "    charge(tar, 'paxRecords', 1);\n", '', 'pax records are not counted'),
    ('reader.d4.sparse.old-header-uncounted', READER, "  charge(tar, 'sparseNumbers', 2 * structs.length);\n", '', 'the four structs of an old GNU header are not counted'),
    ('reader.d4.sparse.old-header-as-regions', READER, "charge(tar, 'sparseNumbers', 2 * structs.length);", "charge(tar, 'sparseNumbers', structs.length);", 'an old GNU header is counted in regions, half of its numbers'),
    ('reader.d4.sparse.old-extension-uncounted', READER, "if (offset && numbytes) { charge(tar, 'sparseNumbers', 2); structs.push([offset, numbytes]); }", 'if (offset && numbytes) { structs.push([offset, numbytes]); }', 'the structs of old GNU extension blocks are not counted'),
    ('reader.d4.sparse.old-extension-as-regions', READER, "if (offset && numbytes) { charge(tar, 'sparseNumbers', 2);", "if (offset && numbytes) { charge(tar, 'sparseNumbers', 1);", 'an old GNU extension struct is counted as one number'),
    ('reader.d4.sparse.map-uncounted', READER, "      charge(tar, 'sparseNumbers', 1);\n      numbers.push(value);", '      numbers.push(value);', 'the numbers of a GNU.sparse.map are not counted'),
    ('reader.d4.sparse.inherited-map-uncounted', READER, "      charge(tar, 'sparseNumbers', 1);\n      numbers.push(value);", "      if (values.has('GNU.sparse.map')) charge(tar, 'sparseNumbers', 1);\n      numbers.push(value);",
     'a map inherited from a global header is expanded without being counted: the amplification the delta review found'),
    ('reader.d4.sparse.map-counted-before-it-is-a-number', READER, "      if (value === null) throw new PyValueError('invalid literal for int() with base 10');\n      charge(tar, 'sparseNumbers', 1);\n      numbers.push(value);",
     "      charge(tar, 'sparseNumbers', 1);\n      if (value === null) throw new PyValueError('invalid literal for int() with base 10');\n      numbers.push(value);", 'the budget answers before tarfile would have refused the number'),
    ('reader.d4.sparse.records-0.0-uncounted', READER, "        charge(tar, 'sparseNumbers', 1);\n        (keyword === SPARSE_OFFSET ? offsets : numbytes).push(number);", '        (keyword === SPARSE_OFFSET ? offsets : numbytes).push(number);', 'the records of form 0.0 are not counted'),
    ('reader.d4.sparse.lines-1.0-uncounted', READER, "      const number = line();\n      charge(tar, 'sparseNumbers', 1);\n      numbers.push(number);", '      const number = line();\n      numbers.push(number);', 'the numbers of form 1.0 are not counted'),
    ('reader.d4.sparse.lines-1.0-as-regions', READER, "      const number = line();\n      charge(tar, 'sparseNumbers', 1);\n      numbers.push(number);", "      const number = line();\n      if (numbers.length % 2 === 0) charge(tar, 'sparseNumbers', 1);\n      numbers.push(number);",
     'form 1.0 is counted in regions: a hidden factor of two against the other forms'),
    ('reader.d4.members.uncounted', READER, "if (info !== null) { charge(tar, 'members', 1); tar.members.push(info); }", 'if (info !== null) { tar.members.push(info); }', 'members are not counted'),
    ('reader.d4.default.metadata-bytes', READER, '  metadataBytes: 2 ** 28,\n  paxRecords', '  metadataBytes: 2 ** 29,\n  paxRecords', 'another production budget'),
    ('reader.d4.default.pax-records', READER, '  paxRecords: 2 ** 24,\n', '  paxRecords: 2 ** 25,\n', 'another production budget'),
    ('reader.d4.default.sparse-numbers', READER, '  sparseNumbers: 2 ** 20,\n  members', '  sparseNumbers: 2 ** 21,\n  members', 'another production budget'),
    ('reader.d4.default.members', READER, '  members: 2 ** 17,\n});', '  members: 2 ** 18,\n});', 'another production budget'),
    ('reader.d4.options.ignored', READER, 'const budgets: Budgets = { ...BUDGETS, ...options.budgets };', 'const budgets: Budgets = { ...options.budgets, ...BUDGETS };', 'the budgets a test passes are not the ones applied'),
    ('reader.d4.options.unchecked', READER, "      if (!Number.isSafeInteger(budgets[name]) || budgets[name] < 0) throw new Error('a budget is a non-negative safe integer: ' + name);", '', 'a budget may be any value'),
    ('reader.d4.refusal.echoes-the-count', READER, "super('ETARBUDGET', 'tar metadata budget exceeded: ' + BUDGET_NAMES[budget]);", "super('ETARBUDGET', 'tar metadata budget exceeded: ' + BUDGET_NAMES[budget] + ' ' + String(BUDGETS[budget]));", 'the message holds a number'),
    # The heap the budgets are sized for.
    ('reader.heap.unchecked', READER, 'if (v8.getHeapStatistics().heap_size_limit < HEAP_LIMIT_REQUIRED_MIB * 1024 * 1024) {', 'if (false) {', 'any heap limit is accepted'),
    ('reader.heap.requirement-halved', READER, 'export const HEAP_LIMIT_REQUIRED_MIB = 3072;', 'export const HEAP_LIMIT_REQUIRED_MIB = 1536;', 'half the heap is required'),
    ('reader.heap.gzip-unchecked', READER, 'export function gzipMembers(data: Uint8Array, options: OpenOptions = {}): TarMember[] {\n  sizedHeap();\n', 'export function gzipMembers(data: Uint8Array, options: OpenOptions = {}): TarMember[] {\n', 'a gzip archive is listed under any heap limit'),
    ('reader.heap.tar-unchecked', READER, 'export function tarMembers(stream: Uint8Array, options: OpenOptions = {}): TarMember[] {\n  sizedHeap();\n', 'export function tarMembers(stream: Uint8Array, options: OpenOptions = {}): TarMember[] {\n', 'a tar stream is listed under any heap limit'),
    # A size in a global header.
    ('reader.pax.global-size-not-shared', READER, '    if (last !== undefined && last.text === text) return last.value;\n', '', 'a global size is converted anew for every member: memory no budget counts'),
    ('reader.pax.global-size-stale', READER, 'if (last !== undefined && last.text === text) return last.value;', 'if (last !== undefined) return last.value;', 'the first size converted answers for every later one'),
    # B9 and L1: the runtime and the step record.
    ('reader.runtime.check-removed', READER, '  verifiedRuntime();\n  const members: GzipMember[] = [];', '  const members: GzipMember[] = [];', 'any runtime is trusted'),
    ('reader.runtime.list-wider', READER, "['22.23.3', '24.14.0']", "['22.23.3', '24.14.0', '24.14.1']", 'one more Node version is trusted'),
    ('reader.runtime.pin-dropped', READER, "['22.23.3', '24.14.0']", "['22.23.3']", 'the pinned Node is refused'),
    ('reader.runtime.bun-trusted', READER, " || versions['bun'] !== undefined", '', 'Bun is trusted'),
    ('reader.record.not-compared', READER, MATCHES, 'return true;', 'a later pass is not held to the step record'),
    ('reader.record.length-only', READER, MATCHES, 'return index < this.count && this.cells[2 * index] === length;', 'only the length of a piece is compared'),
    ('reader.record.crc-only', READER, MATCHES, 'return index < this.count && this.cells[2 * index + 1] === crc >>> 0;', 'only the running CRC-32 of a piece is compared'),
    ('reader.record.member-end-not-compared', READER, 'if (this.step !== member.steps || this.inflater!.at !== member.tail) throw this.differs();', '', 'a member may end elsewhere than the checking pass saw'),
    ('reader.record.later-damage-as-archive-damage', READER, 'if (error instanceof GzipDamage) throw this.differs();', '', 'an engine fault in a later pass is reported as a damaged archive'),
    ('reader.record.step-unbounded', READER, ' || inputStep > INPUT_STEP_MAX)', ')', 'a step may be any size'),
    # F2: numbers and names in one pass.
    ('reader.f2.slashes-not-stripped', READER, 'while (end > 0 && name.charCodeAt(end - 1) === 0x2f) end--;', '', 'trailing slashes stay'),
    ('reader.f2.one-slash-stripped', READER, 'while (end > 0 && name.charCodeAt(end - 1) === 0x2f) end--;', 'if (end > 0 && name.charCodeAt(end - 1) === 0x2f) end--;', 'one trailing slash is stripped, not all'),
    ('reader.f2.double-underscore', READER, 'if (code === 0x5f && this.state === 2) { this.state = 3; return true; }', 'if (code === 0x5f && (this.state === 2 || this.state === 3)) { this.state = 3; return true; }', 'two underscores in a row are taken'),
    ('reader.f2.trailing-underscore', READER, 'if (this.state !== 2 && this.state !== 4) return null;', 'if (this.state !== 2 && this.state !== 3 && this.state !== 4) return null;', 'a number may end in an underscore'),
    ('reader.f2.digit-limit', READER, 'return this.digits.length <= MAX_INT_DIGITS;', 'return this.digits.length <= MAX_INT_DIGITS + 1;', '4,301 digits are taken'),
    ('reader.f2.blanks', READER, 'const blank = code === 0x20 || (code >= 0x09 && code <= 0x0d);', 'const blank = code === 0x20 || (code >= 0x09 && code <= 0x0a);', 'vertical tab, form feed and carriage return are no blanks'),
    ('reader.f2.blank-after-sign', READER, 'if (blank && (this.state === 0 || this.state === 4)) return true;', 'if (blank && (this.state === 0 || this.state === 1 || this.state === 4)) return true;', 'a blank may follow the sign'),
    # F3: the pax store.
    ('reader.f3.first-value-kept', READER, 'if (PAX_OBSERVED.has(keyword)) values.set(keyword, [equals + 1, end]);', 'if (PAX_OBSERVED.has(keyword) && !values.has(keyword)) values.set(keyword, [equals + 1, end]);', 'of two records of one keyword the first value counts'),
    ('reader.f3.repeat-moves-to-the-end', READER, 'if (PAX_OBSERVED.has(keyword)) values.set(keyword, [equals + 1, end]);', 'if (PAX_OBSERVED.has(keyword)) { values.delete(keyword); values.set(keyword, [equals + 1, end]); }', 'a repeated keyword takes the place of its last record'),
    ('reader.f3.keyword-length', READER, 'const PAX_KEYWORD_MAX = 19;', 'const PAX_KEYWORD_MAX = 18;', 'the longest observed keywords are not seen'),
    ('reader.f3.empty-keyword', READER, 'if (found <= 0 || buf[last] !== 0x0a)', 'if (found < 0 || buf[last] !== 0x0a)', 'a record without a keyword is taken'),
    ('reader.f3.realsize-not-kept', READER, "'GNU.sparse.name', 'GNU.sparse.realsize', ", "'GNU.sparse.name', ", 'GNU.sparse.realsize is dropped'),
    ('reader.f3.sparse-offset-not-read', READER, "const SPARSE_OFFSET = 'GNU.sparse.offset',", "const SPARSE_OFFSET = 'GNU.sparse.offsets',", 'the offsets of form 0.0 are not read'),
    # The manifest-name test of archive.ts.
    ('reader.manifest.empty-first-component', ARCHIVE, "if (slash < 1 || !name.startsWith('package', slash + 1)", "if (slash < 0 || !name.startsWith('package', slash + 1)", 'a name that begins with the slash is taken'),
    ('reader.manifest.any-prefix', ARCHIVE, " || !name.startsWith('package', slash + 1)", '', 'the word before the dot is not looked at'),
    ('reader.manifest.any-suffix', ARCHIVE, " || !name.endsWith('json')", '', 'the word after the dot is not looked at'),
    ('reader.manifest.newline-taken', ARCHIVE, 'return any !== 0x0a && name.length', 'return name.length', 'a newline is taken for the dot'),
    ('reader.manifest.longer-tail', ARCHIVE, 'name.length - slash - 1 === (any > 0xffff ? 13 : 12)', 'name.length - slash - 1 >= 12', 'more than one character may stand for the dot'),
    ('reader.manifest.astral-is-two', ARCHIVE, '(any > 0xffff ? 13 : 12)', '12', 'a character outside the BMP is not taken for the dot'),
]

local = getattr(threading, 'local')()
counter = [0]
mutex = threading.Lock()


def sources():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (READER, ARCHIVE)}


def scratch(out):
    if not hasattr(local, 'root'):
        with mutex:
            counter[0] += 1
            local.root = out / ('worker-%d' % counter[0])
        shutil.rmtree(local.root, ignore_errors=True)
        local.root.mkdir(parents=True)
        os.symlink(ROOT / 'node_modules', local.root / 'node_modules')
        os.symlink(ROOT / 'oracle', local.root / 'oracle')
        for relative in COPIED:
            (local.root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, local.root / relative)
        shutil.copytree(ROOT / 'src', local.root / 'src')
        (local.root / '.cache').mkdir()  # port-runtime.test.js writes its archive there
    return local.root


def held(root, node):
    """Compile the tree in `root` and hold it against the reader's checks."""
    env = {'PATH': os.path.dirname(os.path.abspath(node)) + os.pathsep + os.environ['PATH'] if os.sep in node else os.environ['PATH'], 'HOME': os.environ.get('HOME', '/nonexistent')}
    tsc = subprocess.run([node, str(ROOT / 'node_modules/typescript/bin/tsc'), '-p', 'tsconfig.json', '--incremental', '--tsBuildInfoFile', '.tsbuildinfo'], cwd=root, capture_output=True, text=True, env=env, timeout=600)
    if tsc.returncode:
        return {'status': 'invalid', 'compiler': [line.split(': error ', 1)[-1] for line in tsc.stdout.strip().splitlines()[:2]]}
    failing = []
    for name in UNIT:
        # The test file is run directly, as ts_mutants.py runs its own: `node --test` adds a parent process.
        done = subprocess.run([node, 'test/unit/' + name], cwd=root, capture_output=True, text=True, env=env, timeout=900)
        if done.returncode:
            failing.append(name)
    done = subprocess.run([sys.executable, '-B', 'test/differential/port_primitives.py', 'run', '--node', node] + DIFFERENTIAL, cwd=root, capture_output=True, text=True, env=env, timeout=900)
    try:
        report = json.loads(done.stdout.strip().split('\n')[0])
        differs = sorted(kind for kind, entry in report.items() if entry['mismatches'])
        differential = ('differs: ' + ', '.join(differs)) if differs else 'agrees'
    except (ValueError, KeyError, IndexError):
        differential = 'cannot run'
    return {'status': 'killed' if failing or differential != 'agrees' else 'survived', 'unit_files_failing': failing, 'differential': differential}


def work(mutant, out, node):
    ident, relative, before, after, what = mutant
    root = scratch(out)
    target, original = root / relative, (ROOT / relative).read_text()
    try:
        target.write_text(original.replace(before, after))
        result = held(root, node)
    finally:
        target.write_text(original)
    return dict({'id': ident, 'file': relative, 'mutation': what}, **result)


def run(args):
    seen = set()
    for ident, relative, before, after, _what in MUTANTS:
        if ident in seen:
            raise SystemExit('duplicate mutant id ' + ident)
        seen.add(ident)
        if (ROOT / relative).read_text().count(before) != 1 or before == after:
            raise SystemExit('%s: its anchor occurs %d times in %s' % (ident, (ROOT / relative).read_text().count(before), relative))
    mutants = [mutant for mutant in MUTANTS if not args.only or any(mutant[0].startswith(prefix) for prefix in args.only)]
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # The unmutated tree must pass in the same scratch layout, or a kill means nothing.
    clean = held(scratch(out), args.node)
    if clean['status'] != 'survived':
        print('BLOCKED: the unmutated reader does not pass its own checks in the scratch layout: ' + json.dumps(clean))
        return 1
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda mutant: work(mutant, out, args.node), mutants))
    counts = {}
    for result in results:
        counts[result['status']] = counts.get(result['status'], 0) + 1
    only_one = {'unit only': sorted(r['id'] for r in results if r['status'] == 'killed' and r['unit_files_failing'] and r['differential'] == 'agrees'),
                'differential only': sorted(r['id'] for r in results if r['status'] == 'killed' and not r['unit_files_failing'])}
    recorded = [{key: result[key] for key in ('id', 'file', 'mutation', 'status')} for result in results]
    report = {'sources': sources(), 'unit': UNIT, 'differential': ' '.join(DIFFERENTIAL), 'mutants': len(results), 'counts': dict(sorted(counts.items())), 'results': recorded}
    text = json.dumps(report, indent=1) + '\n'
    killed = [r for r in results if r['status'] == 'killed']
    summary = {'mutants': len(results), 'counts': report['counts'], 'not_killed': [r['id'] for r in results if r['status'] != 'killed'],
               'killed_by': {'both checks': len(killed) - sum(len(value) for value in only_one.values()), 'the unit files only': len(only_one['unit only']), 'the differential only': len(only_one['differential only'])},
               'killed_by_one_check_only': only_one}
    (out / 'details.json').write_text(json.dumps({'node': subprocess.run([args.node, '--version'], capture_output=True, text=True).stdout.strip(), 'results': results}, indent=1) + '\n')
    if args.only:
        print(json.dumps(dict(summary, results=[r for r in results]), indent=1))
        return 0
    if args.check:
        if RESULTS.read_text() != text:
            print('BLOCKED: reader mutant results differ from test/mutants/reader-results.golden')
            print(json.dumps(summary))
            return 1
    else:
        RESULTS.write_text(text)
    print(json.dumps(summary))
    return int(bool(counts.get('invalid')))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('run')
    command.add_argument('--node', default='node')
    command.add_argument('--workers', type=int, default=8)
    command.add_argument('--check', action='store_true')
    command.add_argument('--only', action='append')
    command.add_argument('--out', type=Path, default=ROOT / '.cache/reader-mutants')
    return run(parser.parse_args())


if __name__ == '__main__':
    sys.exit(main())
