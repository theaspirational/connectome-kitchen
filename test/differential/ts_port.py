#!/usr/bin/env python3
"""Module differential for errors, lock and inputs: live Python oracle against the TS port.

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

The archive seam (plan 3.1: `archive` is the next module) is not ported. The
oracle's three lazily imported archive functions are wrapped here; their calls
and answers are logged and the TS side replays that log, refusing any call
that is not the next logged one with identical arguments.

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
MODULES = ('connectome_errors', 'connectome_lock', 'connectome_inputs')
PORT = ROOT / 'test/vectors/port-m1-3.golden'
SEAM = ('npm_name', 'validate_dependency_record', 'check_dependencies')


def initialize(modules=ROOT / 'oracle/module_utils'):
    sys.path.insert(0, str(modules.resolve()))
    for name in MODULES + ('connectome_archive',):
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
            target.write_bytes(base64.b64decode(spec['file']) if 'file' in spec else spec['text'].encode())
            later.append((target, spec.get('mode', 0o644)))
    for target, mode in sorted(later, key=lambda item: -len(str(item[0]))):
        os.chmod(target, mode)


def remove(root):
    if root.is_symlink() or root.is_file():
        root.unlink()
        return
    if root.is_dir():
        for directory, _names, _files in os.walk(root):
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
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
    """Call the oracle. Returns (outcome, seam log)."""
    module, _, method = name.partition('.')
    loaded = importlib.import_module(module)
    archive = importlib.import_module('connectome_archive')
    originals = {key: getattr(archive, key) for key in SEAM}
    log = []

    inside = []

    def wrap(key):
        def call(*a):
            if inside:  # the archive module calling itself; only lock.py's own calls cross the seam
                return originals[key](*a)
            entry = {'function': key, 'args': encode(list(a))}
            log.append(entry)
            inside.append(key)
            try:
                result = originals[key](*a)
            except ValueError as error:
                entry['outcome'] = {'exception': type(error).__name__, 'message': str(error)}
                raise
            except Exception as error:
                entry['outcome'] = {'crash': type(error).__name__}
                raise
            finally:
                inside.pop()
            entry['outcome'] = {'result': encode(result)}
            return result
        return call
    try:
        for key in SEAM:
            setattr(archive, key, wrap(key))
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
    finally:
        for key in SEAM:
            setattr(archive, key, originals[key])
    return outcome, log


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
except ValueError as error: outcome = {'exception': type(error).__name__, 'message': str(error)}
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
    return json.loads(done.stdout), []


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
    raise ValueError('unknown generator')


def expand(value, root):
    if isinstance(value, list):
        return [expand(item, root) for item in value]
    if isinstance(value, dict):
        if '$generate' in value:
            return generated(value, root)
        return {key: expand(item, root) for key, item in value.items()}
    return value


def sha256_arg(value):
    """`{"$sha256-of": <generated bytes spec>}` -> hex digest, so a record can name the digest of generated content."""
    if isinstance(value, list):
        return [sha256_arg(item) for item in value]
    if isinstance(value, dict):
        if '$sha256-of' in value:
            return hashlib.sha256(base64.b64decode(sha256_arg(value['$sha256-of'])['$bytes'])).hexdigest()
        return {key: sha256_arg(item) for key, item in value.items()}
    return value


class Vector:
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
            self.root = TREES / hashlib.sha256(self.id.encode()).hexdigest()[:16]
            tree = {key: expand(spec, self.root) if '$generate' in spec else spec for key, spec in self.row['tree'].items()}
            materialize(self.root, tree)
        root = str(self.root) if self.root else str(TREES / 'none')
        return bind(sha256_arg(expand(args, self.root)), '${ROOT}', root), bind(sha256_arg(expand(kwargs, self.root)), '${ROOT}', root)

    def unbind(self, line):
        if self.root is None:
            return line
        return line.replace(json.dumps(str(self.root))[1:-1], '@BUILD@' if self.root == FIXTURE else '${ROOT}')

    def cleanup(self):
        if self.root is not None and self.root != FIXTURE:
            remove(self.root)


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
    if PORT.exists():
        for line in PORT.read_text().splitlines():
            row = json.loads(line)
            vectors.append(Vector('port', row['id'], row))
    return vectors


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
            outcome, log = (invoke_bare if vector.row.get('bare') else invoke)(vector.function, call_args, call_kwargs)
            record['oracle'] = vector.unbind(canonical(outcome))
            record['seam'] = log
            records.append(record)
            job = {'id': vector.id, 'function': vector.function, 'args': args, 'kwargs': kwargs}
            if log:
                job['seam'] = log
            jobs.append(job)
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


def classify(record):
    if 'skipped' in record:
        return 'skipped'
    oracle, ts = json.loads(record['oracle']), json.loads(record['ts'])
    if 'unsupported' in ts:
        return 'outside-signature-oracle-crash' if 'crash' in oracle else 'outside-signature-oracle-declared'
    if 'crash' in oracle:
        return 'crash-equal' if oracle == ts else 'crash-mismatch'
    return 'equal' if record['oracle'] == record['ts'] else 'mismatch'


def summarize(records):
    report = {}
    for record in records:
        source = record['vector'].source
        entry = report.setdefault(source, {'vectors': 0, 'equal': 0, 'mismatch': 0, 'crash-equal': 0, 'crash-mismatch': 0,
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
        if recorded is not None and 'oracle' in record and source != 'recording':
            if canonical(recorded) != record['oracle']:
                entry['recorded-outcome-differs-from-live-oracle'] += 1
    return report


def failed(report):
    return any(entry['mismatch'] or entry['crash-mismatch'] or entry['outside-signature-oracle-declared'] or
               entry['recorded-outcome-differs-from-live-oracle'] for entry in report.values())


def build_port(seeds, node):
    """Write the committed corpus: each seed row gets the oracle's outcome and seam log. TS is not consulted."""
    initialize()
    lock = importlib.import_module('connectome_lock')
    fixture()
    rows = []
    seen = set()
    for line in seeds.read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        row = json.loads(line)
        if row['id'] in seen:
            raise ValueError('duplicate vector id ' + row['id'])
        seen.add(row['id'])
        vector = Vector('port', row['id'], row)
        try:
            args, kwargs = vector.prepare()
            outcome, log = (invoke_bare if row.get('bare') else invoke)(row['function'], decode(args), decode(kwargs))
            row = dict(row, outcome=json.loads(vector.unbind(canonical(outcome))))
            if log:
                row['seam'] = json.loads(vector.unbind(canonical(log)))
            else:
                row.pop('seam', None)
        finally:
            vector.cleanup()
        rows.append(canonical(row))
    PORT.write_text('\n'.join(rows) + '\n')
    print(canonical({'port_vectors': len(rows)}))


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--recording', type=Path)
    run.add_argument('--exploratory', type=Path)
    run.add_argument('--node', default='node')
    run.add_argument('--report', type=Path)
    build = sub.add_parser('build')
    build.add_argument('--seeds', type=Path, default=ROOT / 'test/vectors/port-m1-3.seeds')
    build.add_argument('--node', default='node')
    args = parser.parse_args()
    if args.command == 'build':
        build_port(args.seeds, args.node)
        return 0
    vectors = committed()
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
