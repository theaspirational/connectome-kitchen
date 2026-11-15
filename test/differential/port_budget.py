#!/usr/bin/env python3
"""The admission budgets of the TS tar reader (divergence D4) at their production values, against the oracle.

The reader counts four things over one listing and refuses, with a constant
`OSError`, an archive that would pass one of them (src/pylike/tarfile.ts,
`BUDGETS`). The oracle has no budgets and reads these archives. Here every
budget is met from three sides with archives that really hold that much, in
both engines, through `archive_manifest` with no option:

    below   the count ends one under the budget    both engines accept, same manifest
    at      the count ends exactly at the budget   both engines accept, same manifest
    above   the count would pass it by one         D4: the oracle accepts, the port refuses

    members.*          plain members
    records.*          one pax header of empty records
    names.*            256 GNU long names of about 1 MiB each ("many large names")
    sparse-global.*    a GNU.sparse.map in a global header, expanded again for every
                       later extended header ("sparse-map amplification")
    sparse-{0.1,0.0,1.0,old}.{at,above}
                       one sparse map of each form with the same number of regions:
                       the four forms cost the same, a region is two numbers in each

and two archives that come close to all four budgets at once:

    combined.all-below    every count ends one under its budget
    combined.heap-worst   members, sparse numbers and metadata bytes one under their
                          budgets, with the shapes that cost the reader most heap:
                          member names and a pax path that are no UTF-8

For every case tar_budget.py walks the archive with the oracle's tarfile and
counts what the reader counts; `used` is where the walk ended, `crossed` the
budget it would have passed. A case is D4 only if that walk crosses a budget,
the oracle accepts, the port refuses as unreadable, and the reader itself
(test/differential/tar_budget_list.mjs) names the same budget.

The port runs every case under a V8 heap limit of exactly H, H being the
reader's own HEAP_LIMIT_REQUIRED_MIB: the budgets are a contract only together
with the heap they were sized for. The reader checks `heap_size_limit`, which
is the old space `--max-old-space-size` sets plus a young generation whose
size depends on the runtime and on the host, so `sized_heap` asks the runtime
which flag gives a limit of exactly H and fails if none does. A refusal must cost little: the port stays
under REFUSAL_RSS_LIMIT_MIB on every `above` case but `names.above`, which
holds 255 names before it refuses the last. Outcomes are compared with
test/vectors/port-budget.golden; peak memory is printed and not pinned.

    python3 test/differential/port_budget.py [--check] [--node node] [--only prefix]
"""
import argparse, hashlib, json, os, shutil, struct, subprocess, sys, tarfile, zlib
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / 'test/vectors/port-budget.golden'
SCRATCH = ROOT / '.cache/port-budget' / str(os.getpid())
UNREADABLE = 'unreadable archive or manifest'
MANIFEST = json.dumps({'name': 'example-package', 'version': '1.0.0'}).encode()
# The reader's production budgets. `main` fails unless the compiled reader states the same.
BUDGETS = {'metadataBytes': 2 ** 28, 'paxRecords': 2 ** 24, 'sparseNumbers': 2 ** 20, 'members': 2 ** 17}
REFUSAL_RSS_LIMIT_MIB = 512
PORT_RSS_LIMIT_MIB = 3072


def header(name=b'package/file.js', size=0, kind=b'0', link=b'', prefix=b'', patch=()):
    block = bytearray(512)
    block[0:len(name)] = name
    block[100:108], block[108:116], block[116:124] = b'0000644\0', b'0000000\0', b'0000000\0'
    block[124:136] = b'%011o\0' % size
    block[136:148] = b'00000000000\0'
    block[156:157] = kind
    block[157:157 + len(link)] = link
    block[257:265] = b'ustar\x0000'
    block[345:345 + len(prefix)] = prefix
    for start, value in patch:
        block[start:start + len(value)] = value
    block[148:156] = b' ' * 8
    block[148:156] = b'%06o\0 ' % sum(block)
    return bytes(block)


def pad(length):
    return b'\0' * (-length % 512)


def record(key, value):
    body = b' ' + key + b'=' + value + b'\n'
    length = len(body) + 1
    while len(str(length)) + len(body) != length:
        length = len(str(length)) + len(body)
    return str(length).encode() + body


class Writer:
    """One gzip member written piece by piece."""

    def __init__(self, path):
        self.out = open(path, 'wb')
        self.out.write(b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff')
        self.packer = zlib.compressobj(6, zlib.DEFLATED, -15)
        self.crc, self.length = 0, 0

    def put(self, data):
        self.crc = zlib.crc32(data, self.crc)
        self.length += len(data)
        self.out.write(self.packer.compress(data))

    def repeat(self, data, count):
        chunk = data * max(1, (1 << 20) // len(data))
        each = len(chunk) // len(data)
        while count >= each:
            self.put(chunk)
            count -= each
        self.put(data * count)

    def fill(self, pattern, size):
        chunk = pattern * ((1 << 20) // len(pattern))
        while size >= len(chunk):
            self.put(chunk)
            size -= len(chunk)
        self.put(chunk[:size])

    def close(self):
        self.put(b'\0' * 1024)
        self.out.write(self.packer.flush())
        self.out.write(struct.pack('<II', self.crc, self.length & 0xffffffff))
        self.out.close()

    # Parts of an archive. Each returns what it adds to the four counts: (metadata bytes, pax records, sparse numbers, members).
    def manifest(self):
        self.put(header(b'package/package.json', len(MANIFEST)) + MANIFEST + pad(len(MANIFEST)))
        return 0, 0, 0, 1

    def members(self, count, block=None):
        self.repeat(block or header(), count)
        return 0, 0, 0, count

    def pax(self, records, kind=b'x'):
        data = b''.join(records)
        self.put(header(b'PaxHeader', len(data), kind) + data + pad(len(data)))
        return len(data), len(records), 0, 0

    def empty_records(self, count):
        """An extended header of `count` records `x=`, and the member it describes."""
        one = record(b'x', b'')
        self.put(header(b'PaxHeader', len(one) * count, b'x'))
        self.repeat(one, count)
        self.put(pad(len(one) * count) + header())
        return len(one) * count, count, 0, 1

    def long_name(self, size, fill=b'a'):
        """A GNU long name that states `size` bytes, and the member it names."""
        self.put(header(b'././@LongLink', size, b'L'))
        self.fill(fill, size - 1)
        self.put(b'\0' + pad(size) + header())
        return size, 0, 0, 1

    def path(self, size, fill=b'a'):
        """An extended header that is one `path` record of `size` bytes in all, and the member it names."""
        lead = b'%d path=' % size
        self.put(header(b'PaxHeader', size, b'x') + lead)
        self.fill(fill, size - len(lead) - 1)
        self.put(b'\n' + pad(size) + header())
        return size, 1, 0, 1

    def map_01(self, numbers, kind=b'x'):
        """A pax header that holds a GNU.sparse.map of `numbers` numbers. tarfile expands the map for the member after the header, a global one included."""
        size, records, _, _ = self.pax([record(b'GNU.sparse.map', b','.join([b'7'] * numbers))], kind)
        return size, records, numbers, 0

    def expansions(self, count, numbers):
        """`count` members, each behind an extended header of one record: each expands the map of `numbers` numbers a global header holds."""
        data = record(b'comment', b'x')
        self.repeat(header(b'PaxHeader', len(data), b'x') + data + pad(len(data)) + header(), count)
        return len(data) * count, count, numbers * count, count

    def sparse_00(self, numbers):
        data = record(b'GNU.sparse.size', b'0') + (record(b'GNU.sparse.offset', b'7') + record(b'GNU.sparse.numbytes', b'7')) * (numbers // 2) + record(b'GNU.sparse.offset', b'7') * (numbers % 2)
        self.put(header(b'PaxHeader', len(data), b'x') + data + pad(len(data)) + header())
        return len(data), 1 + numbers, numbers, 1

    def sparse_10(self, regions, extra=0):
        """The map of form 1.0 states `regions` regions; `extra` more numbers follow it in the data and are not read."""
        head = record(b'GNU.sparse.major', b'1') + record(b'GNU.sparse.minor', b'0')
        data = b'%d\n' % regions + b'7\n' * (2 * regions + extra)
        self.put(header(b'PaxHeader', len(head), b'x') + head + pad(len(head)) + header(b'package/sparse.bin', len(data) + len(pad(len(data)))) + data + pad(len(data)))
        # The reader takes the map block by block and stops when it has the numbers it was told to expect.
        consumed = len(b'%d\n' % regions) + 4 * regions
        return len(head) + 512 * -(-consumed // 512), 2, 2 * regions, 1

    def sparse_old(self, structs):
        """An old GNU sparse header (four structs, zero here) with extension blocks that hold `structs` structs."""
        pair = b'%011o\0' % 7 * 2
        blocks = -(-structs // 21)
        self.put(header(b'package/old.bin', 0, b'S', patch=[(482, b'\1' if blocks else b'\0'), (483, b'%011o\0' % 0)]))
        for index in range(blocks):
            count = min(21, structs - 21 * index)
            self.put(pair * count + b'\0' * (504 - 24 * count) + (b'\1' if index + 1 < blocks else b'\0') + b'\0' * 7)
        return 512 * blocks, 0, 8 + 2 * structs, 1


def plan():
    """(id, builder(writer) -> expected counts). The counts a builder returns are what tar_budget.py must find."""
    B = BUDGETS
    cases = []

    def case(ident, build):
        def run(path):
            writer, total = Writer(path), [0, 0, 0, 0]

            def add(counts):
                for index, value in enumerate(counts):
                    total[index] += value
            build(writer, add)
            writer.close()
            return dict(zip(('metadataBytes', 'paxRecords', 'sparseNumbers', 'members'), total))
        cases.append((ident, run))

    for side, delta in (('below', -1), ('at', 0), ('above', 1)):
        case('members.' + side, lambda w, add, delta=delta: (add(w.manifest()), add(w.members(B['members'] + delta - 1))))
        case('records.' + side, lambda w, add, delta=delta: (add(w.manifest()), add(w.empty_records(B['paxRecords'] + delta))))
        # 256 long names: 255 of 1 MiB and one that brings the sum to the budget, give or take one byte.
        case('names.' + side, lambda w, add, delta=delta: (add(w.manifest()), [add(w.long_name(1 << 20)) for _ in range(255)], add(w.long_name(B['metadataBytes'] - 255 * (1 << 20) + delta))))
        # A global map of 1,024 numbers: expanded for the member after it and for 1,022 extended headers, 1,023 times in all.
        # The last extended header holds a map of its own, which brings the sum to the budget, give or take one number.
        case('sparse-global.' + side, lambda w, add, delta=delta: (add(w.manifest()), add(w.map_01(1024, b'g')), add(w.members(1)), add(w.expansions(1022, 1024)), add(w.map_01(1024 + delta)), add(w.members(1))))
    regions = B['sparseNumbers'] // 2
    for side, delta in (('at', 0), ('above', 1)):
        case('sparse-0.1.' + side, lambda w, add, delta=delta: (add(w.manifest()), add(w.map_01(2 * regions + delta)), add(w.members(1))))
        case('sparse-0.0.' + side, lambda w, add, delta=delta: (add(w.manifest()), add(w.sparse_00(2 * regions + delta))))
        case('sparse-1.0.' + side, lambda w, add, delta=delta: (add(w.manifest()), add(w.sparse_10(regions + delta))))
        case('sparse-old.' + side, lambda w, add, delta=delta: (add(w.manifest()), add(w.sparse_old(regions + delta - 4))))

    worst = header(b'\xff' * 100, link=b'\xff' * 100, prefix=b'\xff' * 155, kind=b'2')

    def counted(add):
        total = [0, 0, 0, 0]

        def both(counts):
            add(counts)
            for index, value in enumerate(counts):
                total[index] += value
        return total, both

    def all_below(w, add):
        total, both = counted(add)
        both(w.manifest())
        # A global map of 1,023 numbers is expanded 1,025 times: for the member after it, for 1,022 extended headers here,
        # and for the two extended headers at the end, the one of the records and the one of the path.
        both(w.map_01(1023, b'g'))
        both(w.members(1))
        both(w.expansions(1022, 1023))
        both((0, 0, 2 * 1023, 0))
        both(w.members(B['members'] - 1 - total[3] - 2))
        both(w.empty_records(B['paxRecords'] - 1 - total[1] - 1))
        both(w.path(B['metadataBytes'] - 1 - total[0], b'\xff'))

    def heap_worst(w, add):
        total, both = counted(add)
        both(w.manifest())
        both(w.map_01(1023, b'g'))
        both(w.members(1))
        both(w.expansions(1023, 1023))
        both((0, 0, 1023, 0))
        both(w.members(B['members'] - 1 - total[3] - 1, worst))
        both(w.path(B['metadataBytes'] - 1 - total[0], b'\xff'))
    case('combined.all-below', all_below)
    case('combined.heap-worst', heap_worst)
    return cases


def entry(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return {'name': 'example-package', 'archive': str(path), 'sha256': digest.hexdigest(), 'kind': 'artifact', 'version': '1.0.0'}


def child(argv):
    """Runs a child to its end. Returns (stdout, peak resident memory in MiB, seconds of processor time)."""
    process = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = process.stdout.read(), process.stderr.read()
    _pid, status, usage = os.wait4(process.pid, 0)
    process.returncode = os.waitstatus_to_exitcode(status)
    if process.returncode:
        raise RuntimeError('child failed (%d): %s' % (process.returncode, err.decode(errors='replace')[-2000:]))
    return out.decode(), round(usage.ru_maxrss / 1024), round(usage.ru_utime + usage.ru_stime, 1)


def oracle_child(argument):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    request = json.loads(argument)
    if 'walk' in request:
        import tar_budget
        print(json.dumps(tar_budget.crossing(request['walk'], BUDGETS)))
        return 0
    import ts_port
    from record import canonical
    ts_port.initialize()
    print(canonical(ts_port.invoke('connectome_archive.archive_manifest', [request['entry']], {})))
    return 0


def reader_constants(node, flags=()):
    out, _rss, _seconds = child([node, *flags, str(ROOT / 'test/differential/tar_budget_list.mjs')])
    return json.loads(out)


def sized_heap(node, required):
    """The `--max-old-space-size` under which this runtime, here, has a heap limit of exactly `required` MiB."""
    young = reader_constants(node, ['--max-old-space-size=%d' % required])['heap_size_limit_mib'] - required
    flag = required - young
    limit = reader_constants(node, ['--max-old-space-size=%d' % flag])['heap_size_limit_mib']
    if limit != required:
        raise SystemExit('no --max-old-space-size gives a heap limit of exactly %d MiB here: %d gives %d' % (required, flag, limit))
    return flag


def build(node, heap, only, keep=None):
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    me = [sys.executable, '-B', str(Path(__file__).resolve()), '--oracle-child']
    sized = [node, '--max-old-space-size=%d' % sized_heap(node, heap)]
    unbind = lambda text: json.loads(text.replace(json.dumps(str(SCRATCH))[1:-1], '${SCRATCH}'))
    results, measured = [], {}
    try:
        for ident, writer in plan():
            if only and not ident.startswith(only):
                continue
            file = SCRATCH / (ident + '.tgz')
            expected = writer(file)
            record_ = entry(file)
            walk, _, _ = child(me + [json.dumps({'walk': str(file)})])
            oracle, oracle_rss, oracle_seconds = child(me + [json.dumps({'entry': record_})])
            jobs, out = SCRATCH / 'jobs.jsonl', SCRATCH / 'outcomes.jsonl'
            jobs.write_text(json.dumps({'id': ident, 'function': 'connectome_archive.archive_manifest', 'args': [record_], 'kwargs': {}}) + '\n')
            _stdout, port_rss, port_seconds = child(sized + [str(ROOT / 'test/differential/ts_runner.mjs'), str(jobs), str(out)])
            reader, reader_rss, _ = child(sized + [str(ROOT / 'test/differential/tar_budget_list.mjs'), str(file)])
            walk, oracle, port, reader = json.loads(walk), unbind(oracle.strip()), unbind(out.read_text().strip()), json.loads(reader)
            reader = {key: reader[key] for key in ('members', 'error', 'message', 'budget') if key in reader}
            if oracle == port and walk['crossed'] is None and 'members' in reader:
                kind = 'equal'
            elif walk['crossed'] in BUDGETS and 'result' in oracle and port.get('message') == UNREADABLE and reader.get('budget') == walk['crossed']:
                kind = 'D4'
            else:
                kind = 'MISMATCH'
            results.append({'id': ident, 'built_to_count': expected, 'oracle_walk': walk, 'oracle': oracle, 'port': port, 'reader': reader, 'classification': kind})
            measured[ident] = {'compressed_bytes': file.stat().st_size, 'oracle_peak_rss_mib': oracle_rss, 'oracle_cpu_s': oracle_seconds, 'port_peak_rss_mib': port_rss, 'port_cpu_s': port_seconds, 'reader_peak_rss_mib': reader_rss}
            if keep:
                shutil.copy(file, Path(keep) / file.name)
            file.unlink()
        return {'budgets': BUDGETS, 'cases': results}, measured
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)


def wanted(ident):
    return 'D4' if ident.endswith('.above') else 'equal'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--oracle-child')
    parser.add_argument('--node', default='node')
    parser.add_argument('--only')
    parser.add_argument('--keep', help='a directory to leave the archives in, for measurements')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.oracle_child:
        return oracle_child(args.oracle_child)
    constants = reader_constants(args.node)
    problems = []
    if constants['defaults'] != BUDGETS:
        problems.append('the compiled reader states other budgets than this file: ' + json.dumps(constants['defaults']))
    heap = constants['heap_limit_required_mib']
    report, measured = build(args.node, heap, args.only, args.keep)
    text = json.dumps(report, indent=1) + '\n'
    for case in report['cases']:
        ident, walk = case['id'], case['oracle_walk']
        if case['classification'] != wanted(ident):
            problems.append(ident + ' is ' + case['classification'] + ', not ' + wanted(ident))
        if ident.endswith('.above'):
            crossed = {'members': 'members', 'records': 'paxRecords', 'names': 'metadataBytes'}.get(ident.split('.')[0], 'sparseNumbers')
            if walk['crossed'] != crossed:
                problems.append(ident + ': the walk of the oracle crosses ' + str(walk['crossed']) + ', not ' + crossed)
            if ident != 'names.above' and measured[ident]['port_peak_rss_mib'] > REFUSAL_RSS_LIMIT_MIB:
                problems.append('%s: the port peaked at %d MiB on a refusal, over the bound of %d' % (ident, measured[ident]['port_peak_rss_mib'], REFUSAL_RSS_LIMIT_MIB))
        elif walk['used'] != case['built_to_count']:
            problems.append(ident + ': the walk of the oracle counts ' + json.dumps(walk['used']) + ', the archive was built to ' + json.dumps(case['built_to_count']))
        if measured[ident]['port_peak_rss_mib'] > PORT_RSS_LIMIT_MIB:
            problems.append('%s: the port peaked at %d MiB, over the bound of %d' % (ident, measured[ident]['port_peak_rss_mib'], PORT_RSS_LIMIT_MIB))
    by_id = {case['id']: case for case in report['cases']}
    if not args.only:
        for name in BUDGETS:
            key = {'members': 'members', 'paxRecords': 'records', 'metadataBytes': 'names', 'sparseNumbers': 'sparse-global'}[name]
            sides = [by_id[key + '.' + side]['oracle_walk']['used'][name] for side in ('below', 'at')]
            if sides != [BUDGETS[name] - 1, BUDGETS[name]]:
                problems.append(key + ': below and at do not end one under the budget and at it: ' + json.dumps(sides))
        if by_id['combined.all-below']['oracle_walk']['used'] != {name: value - 1 for name, value in BUDGETS.items()}:
            problems.append('combined.all-below does not end one under every budget')
        if args.check and GOLDEN.read_text() != text:
            problems.append('outcomes differ from test/vectors/port-budget.golden')
        elif not args.check:
            GOLDEN.write_text(text)
    print(json.dumps({'budgets': BUDGETS, 'heap_limit_required_mib': heap, 'port_heap_size_limit_mib': heap, 'port_max_old_space_size_mib': sized_heap(args.node, heap), 'refusal_rss_limit_mib': REFUSAL_RSS_LIMIT_MIB, 'port_rss_limit_mib': PORT_RSS_LIMIT_MIB,
                      'cases': {case['id']: case['classification'] for case in report['cases']}, 'measured': measured, 'problems': problems}))
    return int(bool(problems))


if __name__ == '__main__':
    sys.exit(main())
