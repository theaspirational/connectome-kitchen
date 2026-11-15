#!/usr/bin/env python3
"""Module differential for the ported engine modules: live Python oracle against the TS port.

Every vector is an input only. The oracle is called here, now, on that input;
the TS port is called on the same input, at the same paths, on the same files;
the two outcomes are compared as canonical bytes. A recorded outcome is never
the reference (for committed vectors it is checked as well, as a third party).

Outcome classes of the oracle:
  result     normal return, tagged encoding of test/differential/record.py
  exception  a ValueError (the engine's declared refusals are all ValueErrors):
             class name, message and, for SafeError, the public diagnostic
  crash      any other exception: class name only. CPython's own message text
             is not part of the engine. Counted and compared separately.

Nothing is fetched; nothing outside .cache is written.
"""
import argparse, base64, copy, hashlib, importlib, json, os, shutil, stat, subprocess, sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from codec import decode
from record import encode, canonical

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = ROOT / '.cache/ts-port'
FIXTURE = SCRATCH / 'lock-files-v1'
TREES = SCRATCH / 'trees'
MODULES = ('connectome_errors', 'connectome_lock', 'connectome_inputs', 'connectome_archive', 'connectome_config')
PORT = ROOT / 'test/vectors/port.golden'
PORT_TREES = ROOT / 'test/vectors/port.trees.golden'
# Inputs on which the port deliberately answers differently: both outcomes are recorded and both are checked.
DIVERGENCES = ROOT / 'test/vectors/port-divergences.golden'


def initialize(modules=ROOT / 'oracle/module_utils'):
    sys.path.insert(0, str(modules.resolve()))
    for name in MODULES:
        importlib.import_module(name)


def materialize(root, tree):
    """Same contract as `materialize` in ts_invoke.mjs."""
    remove(root)
    root.mkdir(parents=True, mode=0o755)
    later = []
    for relative, spec in tree.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        if 'link' in spec:
            os.symlink(spec['link'].replace('${ROOT}', str(root)), target)
        elif 'dir' in spec:
            target.mkdir(parents=True, exist_ok=True)
            later.append((target, spec['dir']))
        else:
            binary = spec.get('file', spec.get('$bytes'))
            target.write_bytes(base64.b64decode(binary) if binary is not None else spec['text'].replace('${ROOT}', str(root)).encode())
            later.append((target, spec.get('mode', 0o644)))
    for target, mode in sorted(later, key=lambda item: -len(str(item[0]))):
        os.chmod(target, mode)


def remove(root):
    if root.is_symlink() or root.is_file():
        root.unlink()
        return
    if root.is_dir():
        os.chmod(root, 0o700)
        for directory, names, _files in os.walk(root):
            for name in names:  # before the walk descends: a 0000 directory cannot be listed
                child = os.path.join(directory, name)
                if not os.path.islink(child):
                    os.chmod(child, 0o700)
        shutil.rmtree(root)


def fixture():
    """The explicit lock-file fixture of guard_vectors.py (`lock-files-v1`), at this harness's own root."""
    materialize(FIXTURE, {'run.sh': {'text': '#!/bin/sh\nexit 0\n'},
                          'example.service': {'text': '[Service]\nExecStart=/bin/true\n'},
                          'cook-recipe.json': {'text': '{}'}})
    return FIXTURE


def bind(value, old, new):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [bind(item, old, new) for item in value]
    if isinstance(value, dict):
        return {bind(key, old, new): bind(item, old, new) for key, item in value.items()}
    return value


def invoke(name, args, kwargs):
    """Call the oracle. Returns its outcome."""
    module, _, method = name.partition('.')
    loaded = importlib.import_module(module)
    if name == 'connectome_errors.SafeError.public':
        target = lambda *a: loaded.SafeError(*a).public()
    else:
        target = loaded
        for part in method.split('.'):
            target = getattr(target, part)
    try:
        result = target(*copy.deepcopy(args), **copy.deepcopy(kwargs))
    except ValueError as error:
        outcome = {'exception': type(error).__name__, 'message': str(error)}
        if hasattr(error, 'public'):
            outcome['public'] = encode(error.public())
    except Exception as error:  # a runtime error of the interpreter, not a declared refusal
        outcome = {'crash': type(error).__name__}
    else:
        outcome = {'result': encode(result)}
    return outcome


BARE = r'''
import sys, json
sys.dont_write_bytecode = True
sys.path[:0] = [sys.argv[1], sys.argv[2]]
from codec import decode
from record import encode
import importlib
job = json.loads(sys.stdin.read())
module, _, method = job['function'].partition('.')
target = getattr(importlib.import_module(module), method)
args = decode(job['args']); kwargs = decode(job['kwargs'])
try: result = target(*args, **kwargs)
except ValueError as error:
    outcome = {'exception': type(error).__name__, 'message': str(error)}
    if hasattr(error, 'public'): outcome['public'] = encode(error.public())
except Exception as error: outcome = {'crash': type(error).__name__}
else: outcome = {'result': encode(result)}
print(json.dumps(outcome))
'''


def invoke_bare(name, args, kwargs):
    """Call the oracle from the top level of a script: the frame depth the recursion bounds are stated for."""
    job = json.dumps({'function': name, 'args': args, 'kwargs': kwargs})
    done = subprocess.run([sys.executable, '-I', '-c', BARE, str(Path(__file__).resolve().parent), str(ROOT / 'oracle/module_utils')],
                          input=job, capture_output=True, text=True, timeout=120, cwd=ROOT)
    if done.returncode:
        raise RuntimeError('bare oracle run failed')
    return json.loads(done.stdout)


def generated(spec, root):
    """Deterministic large inputs that would bloat a vector file: `{"$generate": kind, ...}`."""
    kind = spec['$generate']
    if kind == 'nested-lock':  # a lock file whose `components` is `depth` lists around `leaf`
        text = spec['prefix'] + '[' * spec['depth'] + spec['leaf'] + ']' * spec['depth'] + spec['suffix']
        return {'text': text.replace('${ROOT}', str(root))}
    if kind == 'nested':  # depth x open, leaf, depth x close, as bytes
        return {'$bytes': base64.b64encode((spec['open'] * spec['depth'] + spec['leaf'] + spec['close'] * spec['depth']).encode()).decode()}
    if kind == 'repeat':  # prefix + unit * count + suffix, as bytes
        return {'$bytes': base64.b64encode((spec.get('prefix', '') + spec['unit'] * spec['count'] + spec.get('suffix', '')).encode('latin-1')).decode()}
    if kind == 'tgz':  # a gzip container (stored blocks) around a ustar archive; byte-identical in ts_corpus.mjs
        return {'$bytes': base64.b64encode(tgz(spec['members'])).decode()}
    raise ValueError('unknown generator')


def content(spec):
    if isinstance(spec, str):
        return spec.encode()
    return (spec.get('prefix', '') + spec['unit'] * spec['count'] + spec.get('suffix', '')).encode()


def tgz(members):
    """`[[name, content or None for a directory], ...]` -> bytes. No clock, no compressor: reproducible anywhere."""
    tar = b''
    for name, spec in members:
        data = b'' if spec is None else content(spec)
        head = bytearray(512)
        head[0:len(name)] = name.encode()
        head[100:108], head[108:116], head[116:124] = b'0000644\0', b'0000000\0', b'0000000\0'
        head[124:136] = b'%011o\0' % len(data)
        head[136:148] = b'00000000000\0'
        head[148:156] = b' ' * 8
        head[156:157] = b'5' if spec is None else b'0'
        head[257:265] = b'ustar\x0000'
        head[329:337], head[337:345] = b'0000000\0', b'0000000\0'
        head[148:156] = b'%06o\0 ' % sum(head)
        tar += bytes(head) + data + b'\0' * (-len(data) % 512)
    return stored_gzip(tar + b'\0' * 1024)


def stored_gzip(tar):
    """These bytes as one gzip member of stored blocks."""
    out = bytearray(b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff')
    for start in range(0, len(tar), 65535):
        chunk = tar[start:start + 65535]
        out += bytes([int(start + 65535 >= len(tar))]) + len(chunk).to_bytes(2, 'little') + (len(chunk) ^ 0xffff).to_bytes(2, 'little') + chunk
    import zlib
    return bytes(out) + zlib.crc32(tar).to_bytes(4, 'little') + (len(tar) & 0xffffffff).to_bytes(4, 'little')


def expand(value, root):
    """`$generate` and `$join` (a string written in pieces so that no committed file holds it whole)."""
    if isinstance(value, list):
        return [expand(item, root) for item in value]
    if isinstance(value, dict):
        if '$generate' in value:
            return generated(value, root)
        if '$join' in value:
            return ''.join(value['$join'])
        return {key: expand(item, root) for key, item in value.items()}
    return value


def sha256_arg(value):
    """`{"$sha256-of": <bytes>}` -> hex digest, `{"$sha512-integrity-of": <bytes>}` -> `sha512-<base64>`, so a record can name generated content."""
    if isinstance(value, list):
        return [sha256_arg(item) for item in value]
    if isinstance(value, dict):
        if '$sha256-of' in value:
            return hashlib.sha256(base64.b64decode(sha256_arg(value['$sha256-of'])['$bytes'])).hexdigest()
        if '$sha512-integrity-of' in value:
            return 'sha512-' + base64.b64encode(hashlib.sha512(base64.b64decode(sha256_arg(value['$sha512-integrity-of'])['$bytes'])).digest()).decode()
        return {key: sha256_arg(item) for key, item in value.items()}
    return value


class Vector:
    made = set()

    def __init__(self, source, ident, row):
        self.source, self.id, self.row = source, ident, row
        self.function = row['function']
        self.recorded = row.get('outcome')
        self.root = None

    def prepare(self):
        """Bind placeholders and create files. Returns tagged (args, kwargs) as both engines get them."""
        args, kwargs = self.row['args'], self.row['kwargs']
        if self.row.get('profile') == 'lock-files-v1':
            self.root = FIXTURE
            return bind(args, '@BUILD@', str(FIXTURE)), bind(kwargs, '@BUILD@', str(FIXTURE))
        if 'tree' in self.row:
            # Nothing under test writes, so vectors with the same tree share one directory.
            self.root = TREES / hashlib.sha256(canonical(self.row['tree']).encode()).hexdigest()[:16]
            if self.root not in Vector.made:
                tree = {key: dict(expand(spec, self.root), **({'mode': spec['mode']} if 'mode' in spec else {})) if '$generate' in spec else spec
                        for key, spec in self.row['tree'].items()}
                materialize(self.root, tree)
                Vector.made.add(self.root)
        root = str(self.root) if self.root else str(TREES / 'none')
        return bind(sha256_arg(expand(args, self.root)), '${ROOT}', root), bind(sha256_arg(expand(kwargs, self.root)), '${ROOT}', root)

    def unbind(self, line):
        if self.root is None:
            return line
        return line.replace(json.dumps(str(self.root))[1:-1], '@BUILD@' if self.root == FIXTURE else '${ROOT}')

    def cleanup(self):
        if self.root is not None and self.root != FIXTURE:
            remove(self.root)
            Vector.made.discard(self.root)


def committed():
    vectors = []
    for line in (ROOT / 'test/vectors/guard-negative.golden').read_text().splitlines():
        row = json.loads(line)
        if row['function'].split('.')[0] in MODULES:
            vectors.append(Vector('guard-negative', hashlib.sha256(line.encode()).hexdigest()[:16], row))
    for line in (ROOT / 'test/vectors/crash-only.golden').read_text().splitlines():
        row = json.loads(line)
        if row['function'].split('.')[0] in MODULES:
            outcome = {'exception': row['outcome']['exception'], 'message': row['outcome']['message']}
            vectors.append(Vector('crash-only', row['id'], dict(row, outcome=outcome)))
    for row in port_rows():
        vectors.append(Vector('port', row['id'], row))
    for row in json.loads(DIVERGENCES.read_text()):
        vectors.append(Vector('divergence', row['id'], row))
    return vectors


def port_rows():
    """The committed corpus. A row names its file tree by digest; the trees are stored once."""
    if not PORT.exists():
        return []
    trees = json.loads(PORT_TREES.read_text())
    rows = []
    for line in PORT.read_text().splitlines():
        row = json.loads(line)
        if 'tree' in row:
            row['tree'] = trees[row['tree']]
        rows.append(row)
    return rows


def generated_vectors():
    import port_seeds
    return [Vector('generated', row['id'], row) for row in port_seeds.generated()]


def recording(path):
    vectors = []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row['function'].split('.')[0] in MODULES:
            vectors.append(Vector('recording', hashlib.sha256(canonical({k: v for k, v in row.items() if k != 'test'}).encode()).hexdigest()[:16], row))
    return vectors


def exploratory(path):
    vectors = []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row['function'].split('.')[0] in MODULES:
            vectors.append(Vector('exploratory', hashlib.sha256(line.encode()).hexdigest()[:16], row))
    return vectors


def node_outcomes(jobs, node, mutant_dist=None):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    job_file = SCRATCH / ('jobs-%d.jsonl' % os.getpid())
    out_file = SCRATCH / ('outcomes-%d.jsonl' % os.getpid())
    job_file.write_text(''.join(json.dumps(job) + '\n' for job in jobs))
    try:
        done = subprocess.run([node, str(ROOT / 'test/differential/ts_runner.mjs'), str(job_file), str(out_file)],
                              cwd=ROOT, capture_output=True, text=True, timeout=3600)
        if done.returncode:
            raise RuntimeError('TS runner failed: ' + done.stderr[-2000:])
        return out_file.read_text().splitlines()
    finally:
        job_file.unlink(missing_ok=True)
        out_file.unlink(missing_ok=True)


def evaluate(vectors, node='node'):
    """Oracle then TS on every vector, files in place for both. Returns one record per vector."""
    initialize()
    lock = importlib.import_module('connectome_lock')
    fixture()
    for root in (FIXTURE, TREES):
        if lock.credential_shaped(str(root)):
            raise RuntimeError('harness root looks like a credential; choose another checkout path')
    remove(TREES)
    records, jobs = [], []
    try:
        for vector in vectors:
            record = {'vector': vector}
            try:
                args, kwargs = vector.prepare()
                call_args, call_kwargs = decode(args), decode(kwargs)
            except (ValueError, TypeError, KeyError):
                record['skipped'] = 'undecodable arguments (opaque object or fake)'
                records.append(record)
                continue
            outcome = invoke_bare(vector.function, args, kwargs) if vector.row.get('bare') else invoke(vector.function, call_args, call_kwargs)
            record['oracle'] = vector.unbind(canonical(outcome))
            records.append(record)
            jobs.append({'id': vector.id, 'function': vector.function, 'args': args, 'kwargs': kwargs})
        lines = node_outcomes(jobs, node)
        if len(lines) != len(jobs):
            raise RuntimeError('TS runner returned %d outcomes for %d jobs' % (len(lines), len(jobs)))
        pending = iter(lines)
        for record in records:
            if 'oracle' in record:
                record['ts'] = record['vector'].unbind(next(pending))
    finally:
        for vector in vectors:
            vector.cleanup()
        remove(TREES)
    return records


def unordered(outcome):
    """An outcome whose result is a list, with the list in a fixed order: for an oracle answer whose order is not a property of the input."""
    if isinstance(outcome.get('result'), list):
        return dict(outcome, result=sorted(outcome['result'], key=canonical))
    return outcome


def classify(record):
    if 'skipped' in record:
        return 'skipped'
    oracle, ts = json.loads(record['oracle']), json.loads(record['ts'])
    if record['vector'].source == 'divergence':
        row = record['vector'].row
        if row.get('unordered'):
            oracle = unordered(oracle)
        return 'named-divergence' if oracle == row['oracle'] and ts == row['port'] else 'mismatch'
    if 'unsupported' in ts:
        return 'outside-signature-oracle-crash' if 'crash' in oracle else 'outside-signature-oracle-declared'
    if 'crash' in oracle:
        return 'crash-equal' if oracle == ts else 'crash-mismatch'
    return 'equal' if record['oracle'] == record['ts'] else 'mismatch'


def summarize(records):
    report = {}
    for record in records:
        source = record['vector'].source
        entry = report.setdefault(source, {'vectors': 0, 'equal': 0, 'mismatch': 0, 'crash-equal': 0, 'crash-mismatch': 0, 'named-divergence': 0,
                                           'outside-signature-oracle-crash': 0, 'outside-signature-oracle-declared': 0, 'skipped': 0,
                                           'recorded-outcome-differs-from-live-oracle': 0, 'witnesses': [], 'by_function': {}})
        kind = classify(record)
        entry['vectors'] += 1
        entry[kind] += 1
        function = entry['by_function'].setdefault(record['vector'].function, {})
        function[kind] = function.get(kind, 0) + 1
        if kind in ('mismatch', 'crash-mismatch', 'outside-signature-oracle-declared') and len(entry['witnesses']) < 40:
            entry['witnesses'].append({'id': record['vector'].id, 'function': record['vector'].function, 'kind': kind,
                                       'oracle': record['oracle'][:300], 'ts': record['ts'][:300]})
        recorded = record['vector'].recorded
        if recorded is not None and 'oracle' in record and source not in ('recording', 'divergence'):
            if canonical(recorded) != record['oracle']:
                entry['recorded-outcome-differs-from-live-oracle'] += 1
    return report


def failed(report):
    return any(entry['mismatch'] or entry['crash-mismatch'] or entry['outside-signature-oracle-declared'] or
               entry['recorded-outcome-differs-from-live-oracle'] for entry in report.values())


def port_text():
    """The committed corpus as text: each hand-written input with the oracle's outcome. TS is not consulted."""
    import port_seeds
    initialize()
    fixture()
    rows, trees = [], {}
    remove(TREES)
    for row in port_seeds.hand():
        vector = Vector('port', row['id'], row)
        args, kwargs = vector.prepare()
        outcome = invoke_bare(row['function'], args, kwargs) if row.get('bare') else invoke(row['function'], decode(args), decode(kwargs))
        row = dict(row, outcome=json.loads(vector.unbind(canonical(outcome))))
        if 'tree' in row:
            digest = hashlib.sha256(canonical(row['tree']).encode()).hexdigest()[:16]
            trees[digest] = row['tree']
            row['tree'] = digest
        # Not `canonical`: the key order of an input document is part of the input.
        rows.append(json.dumps(row, separators=(',', ':')))
    for root in list(Vector.made):
        remove(root)
    Vector.made.clear()
    remove(TREES)
    return '\n'.join(rows) + '\n', json.dumps(trees, indent=1, sort_keys=True) + '\n'


def build_port():
    rows, trees = port_text()
    PORT.write_text(rows)
    PORT_TREES.write_text(trees)
    print(canonical({'port_vectors': rows.count('\n'), 'trees': len(json.loads(trees))}))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--recording', type=Path)
    run.add_argument('--exploratory', type=Path)
    run.add_argument('--node', default='node')
    run.add_argument('--report', type=Path)
    run.add_argument('--no-generated', action='store_true')
    sub.add_parser('build')
    args = parser.parse_args()
    if args.command == 'build':
        build_port()
        return 0
    vectors = committed()
    if not args.no_generated:
        vectors += generated_vectors()
    if args.recording:
        vectors += recording(args.recording)
    if args.exploratory:
        vectors += exploratory(args.exploratory)
    report = summarize(evaluate(vectors, args.node))
    if args.report:
        args.report.write_text(json.dumps(report, indent=1, sort_keys=True) + '\n')
    brief = {source: {key: value for key, value in entry.items() if key not in ('witnesses', 'by_function')} for source, entry in report.items()}
    print(json.dumps(brief, indent=1, sort_keys=True))
    for source, entry in report.items():
        for witness in entry['witnesses'][:10]:
            print('WITNESS', source, json.dumps(witness, sort_keys=True))
    return int(failed(report))


if __name__ == '__main__':
    sys.exit(main())
