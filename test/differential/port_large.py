#!/usr/bin/env python3
"""Archives too large to commit: the TS port against the oracle, with the peak memory of each.

Three properties the small corpora cannot show are held here.

  * Size is not damage. A well-formed archive whose decompressed stream is
    4 GiB or more must be read as the oracle reads it. (`zlib.crc32` of Node
    22.23.3 and 24.14.0 takes the length of a buffer modulo 2**32, so a reader
    that checks the CRC of the whole stream in one call refuses every such
    archive; that is not divergence D1, which is about damaged containers.)
  * The reader does not hold the decompressed stream. Its peak resident memory
    on these archives stays under PORT_RSS_LIMIT_MIB.
  * The metadata limit (divergence D3) is a constant with two sides. A
    long-name or pax header that states exactly METADATA_LIMIT bytes is read
    as the oracle reads it; one that states a single byte more is refused as
    unreadable, where the oracle accepts the archive. The refusal is made
    before the block is read: the port stays under PORT_RSS_LIMIT_MIB on it.

Cases `large.*`, all on one archive of a manifest and one member of zeros:

    valid        the archive as written. CPython's gzip module reads it to its
                 end; both engines must give the same outcome.
    crc-trailer  one bit of its CRC-32 trailer flipped. CPython's gzip module
                 fails on a full read, so this is a D1 input: the oracle never
                 reaches the trailer and accepts, the port must refuse it as
                 unreadable. It shows that the CRC of a stream this long is
                 really computed, not skipped.

Cases `metadata.*`, each on an archive of a manifest, one header with its
blocks, and the member the header describes:

    longname.at-limit     a GNU long-name header (type L) that states exactly
                          METADATA_LIMIT bytes. Both engines read it.
    longname.above-limit  the same with one byte more. D3: the oracle accepts,
                          the port refuses.
    pax.at-limit          a pax header (type x) of exactly METADATA_LIMIT
                          bytes: one `path` record. Both engines read it.
    pax.above-limit       the same with one byte more. D3.
    longname.above-limit.cut
                          the header of longname.above-limit and nothing after
                          it. The oracle reads a short block and refuses the
                          archive as unreadable; so does the port. D3 applies
                          and changes nothing here.

For every case the oracle is also asked, with a TarInfo that looks on, whether
its listing comes to read a metadata block above the limit; a case is D3 only
if it does.

Outcomes are taken from both engines here and compared with
test/vectors/port-large.golden. The archives are built on the spot (about 4 MiB
of deflate data for 4.1 GiB of zeros, about 0.3 MiB for a metadata block) and
deleted afterwards.

    python3 test/differential/port_large.py [--check] [--node node] [--mib 4200]
"""
import argparse, hashlib, json, os, shutil, struct, subprocess, sys, tarfile, zlib
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / 'test/vectors/port-large.golden'
SCRATCH = ROOT / '.cache/port-large' / str(os.getpid())
PORT_RSS_LIMIT_MIB = 256
# Divergence D3 (METADATA_LIMIT in src/pylike/tarfile.ts): the bytes a long-name, long-link or pax header may state.
METADATA_LIMIT = 2 ** 28
# Reading a block of that size costs a few times its size in either engine; the bound catches a reader that costs much more.
PORT_RSS_LIMIT_AT_METADATA_LIMIT_MIB = 2048
UNREADABLE = 'unreadable archive or manifest'
MANIFEST = json.dumps({'name': 'example-package', 'version': '1.0.0'}).encode()


def write_archive(path, mib):
    """A gzip member holding `package/package.json` and `package/blob.bin` of `mib` MiB of zeros. Returns the tar length."""
    crc, length = 0, 0
    packer = zlib.compressobj(9, zlib.DEFLATED, -15)
    with open(path, 'wb') as out:
        out.write(b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff')

        def put(data):
            nonlocal crc, length
            crc = zlib.crc32(data, crc)
            length += len(data)
            out.write(packer.compress(data))
        info = tarfile.TarInfo('package/package.json')
        info.size = len(MANIFEST)
        put(info.tobuf(format=tarfile.USTAR_FORMAT))
        put(MANIFEST + b'\0' * (-len(MANIFEST) % 512))
        info = tarfile.TarInfo('package/blob.bin')
        info.size = mib << 20
        put(info.tobuf(format=tarfile.GNU_FORMAT))
        block = b'\0' * (1 << 20)
        for _ in range(mib):
            put(block)
        put(b'\0' * 1024)
        out.write(packer.flush())
        out.write(struct.pack('<II', crc, length & 0xffffffff))
    return length


def gzip_writer(path):
    """(put, close) for one gzip member written piece by piece."""
    state = {'crc': 0, 'length': 0}
    packer = zlib.compressobj(6, zlib.DEFLATED, -15)
    out = open(path, 'wb')
    out.write(b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff')

    def put(data):
        state['crc'] = zlib.crc32(data, state['crc'])
        state['length'] += len(data)
        out.write(packer.compress(data))

    def close():
        out.write(packer.flush())
        out.write(struct.pack('<II', state['crc'], state['length'] & 0xffffffff))
        out.close()
    return put, close


def header(name, size, kind=tarfile.REGTYPE):
    info = tarfile.TarInfo(name)
    info.size, info.type = size, kind
    return info.tobuf(format=tarfile.GNU_FORMAT)


def write_metadata_archive(path, kind, stated, cut=False):
    """A manifest, then a header of `kind` that states `stated` bytes with blocks that hold them, then the member it describes."""
    put, close = gzip_writer(path)
    put(header('package/package.json', len(MANIFEST)))
    put(MANIFEST + b'\0' * (-len(MANIFEST) % 512))
    if kind == 'longname':
        put(header('././@LongLink', stated, tarfile.GNUTYPE_LONGNAME))
        fill, tail = stated - 1, b'\0'  # the name and its terminator
        lead = b''
    else:
        put(header('PaxHeader', stated, tarfile.XHDTYPE))
        lead = b'%d path=' % stated  # one record whose length is the whole block
        fill, tail = stated - len(lead) - 1, b'\n'
    if not cut:
        put(lead)
        block = b'a' * (1 << 20)
        for _ in range(fill >> 20):
            put(block)
        put(b'a' * (fill & 0xfffff))
        put(tail + b'\0' * (-stated % 512))
        put(header('package/index.js', 0))
        put(b'\0' * 1024)
    close()


def entry(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return {'name': 'example-package', 'archive': str(path), 'sha256': digest.hexdigest(), 'kind': 'artifact', 'version': '1.0.0'}


def child(argv):
    """Runs a child to its end. Returns (stdout, peak resident memory in MiB)."""
    process = subprocess.Popen(argv, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = process.stdout.read(), process.stderr.read()
    _pid, status, usage = os.wait4(process.pid, 0)
    process.returncode = os.waitstatus_to_exitcode(status)
    if process.returncode:
        raise RuntimeError('child failed (%d): %s' % (process.returncode, err.decode(errors='replace')[-2000:]))
    return out.decode(), round(usage.ru_maxrss / 1024)


def oracle_child(argument):
    """In a child: the oracle's outcome for one `archive_manifest` call, or what a full gzip read of the file says."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    request = json.loads(argument)
    if 'container' in request:
        import gzip
        try:
            with gzip.open(request['container'], 'rb') as stream:
                while stream.read(1 << 22):
                    pass
            print('valid')
        except (OSError, EOFError, zlib.error) as error:
            print(type(error).__name__)
        return 0
    if 'listing' in request:
        # Whether tarfile, listing the archive, comes to read a long-name, long-link or pax block above the limit.
        seen = []

        class Probe(tarfile.TarInfo):
            def _proc_gnulong(self, archive):
                seen.append(self.size > METADATA_LIMIT)
                return super()._proc_gnulong(archive)

            def _proc_pax(self, archive):
                seen.append(self.size > METADATA_LIMIT)
                return super()._proc_pax(archive)
        try:
            with tarfile.open(request['listing'], 'r:gz', tarinfo=Probe) as archive:
                archive.getmembers()
        except Exception:
            pass
        print(json.dumps(any(seen)))
        return 0
    import ts_port
    from record import canonical
    ts_port.initialize()
    print(canonical(ts_port.invoke('connectome_archive.archive_manifest', [request['entry']], {})))
    return 0


def port_outcome(node, record, scratch):
    jobs, out = scratch / 'jobs.jsonl', scratch / 'outcomes.jsonl'
    jobs.write_text(json.dumps({'id': 'large', 'function': 'connectome_archive.archive_manifest', 'args': [record], 'kwargs': {}}) + '\n')
    _stdout, rss = child([node, str(ROOT / 'test/differential/ts_runner.mjs'), str(jobs), str(out)])
    return out.read_text().strip(), rss


def cases(mib):
    """(id, writer(path), a file to damage or None)."""
    def large(path):
        return write_archive(path, mib)
    plan = [('large.valid', large, None), ('large.crc-trailer', None, 'large.valid')]
    for kind in ('longname', 'pax'):
        plan.append(('metadata.%s.at-limit' % kind, lambda path, kind=kind: write_metadata_archive(path, kind, METADATA_LIMIT), None))
        plan.append(('metadata.%s.above-limit' % kind, lambda path, kind=kind: write_metadata_archive(path, kind, METADATA_LIMIT + 1), None))
    plan.append(('metadata.longname.above-limit.cut', lambda path: write_metadata_archive(path, 'longname', METADATA_LIMIT + 1, cut=True), None))
    return plan


def build(node, mib):
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    me = [sys.executable, '-B', str(Path(__file__).resolve()), '--oracle-child']
    unbind = lambda text: json.loads(text.replace(json.dumps(str(SCRATCH))[1:-1], '${SCRATCH}'))
    try:
        results, measured, length, compressed = [], {}, 0, 0
        files = {}
        for ident, writer, damaged in cases(mib):
            file = files[ident] = SCRATCH / (ident + '.tgz')
            if writer is not None:
                made = writer(file)
                if ident == 'large.valid':
                    length, compressed = made, file.stat().st_size
            else:
                data = bytearray(files[damaged].read_bytes())
                data[-8] ^= 1
                file.write_bytes(data)
                del data
            record = entry(file)
            container, _ = child(me + [json.dumps({'container': str(file)})])
            above, _ = child(me + [json.dumps({'listing': str(file)})])
            oracle, oracle_rss = child(me + [json.dumps({'entry': record})])
            port, port_rss = port_outcome(node, record, SCRATCH)
            container, above, oracle, port = container.strip(), json.loads(above), unbind(oracle.strip()), unbind(port)
            if oracle == port:
                kind = 'equal'
            elif container != 'valid' and port.get('message') == UNREADABLE:
                kind = 'D1'
            elif container == 'valid' and above and port.get('message') == UNREADABLE:
                kind = 'D3'
            else:
                kind = 'MISMATCH'
            results.append({'id': ident, 'full_gzip_read': container, 'reads_metadata_above_limit': above, 'oracle': oracle, 'port': port, 'classification': kind})
            measured[ident] = {'oracle_peak_rss_mib': oracle_rss, 'port_peak_rss_mib': port_rss}
            if damaged is None and ident != 'large.valid':
                file.unlink()
        return {'decompressed_bytes': length, 'at_least_4_gib': length >= 1 << 32, 'metadata_limit_bytes': METADATA_LIMIT, 'cases': results}, measured, compressed
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)


WANTED = {'large.valid': 'equal', 'large.crc-trailer': 'D1',
          'metadata.longname.at-limit': 'equal', 'metadata.longname.above-limit': 'D3',
          'metadata.pax.at-limit': 'equal', 'metadata.pax.above-limit': 'D3',
          'metadata.longname.above-limit.cut': 'equal'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--oracle-child')
    parser.add_argument('--node', default='node')
    parser.add_argument('--mib', type=int, default=4200)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.oracle_child:
        return oracle_child(args.oracle_child)
    report, measured, compressed = build(args.node, args.mib)
    text = json.dumps(report, indent=1) + '\n'
    problems = []
    by_id = {case['id']: case for case in report['cases']}
    if sorted(by_id) != sorted(WANTED):
        problems.append('the cases are not the ones expected')
    for case in report['cases']:
        if case['classification'] != WANTED.get(case['id']):
            problems.append(case['id'] + ' is ' + case['classification'] + ', not ' + str(WANTED.get(case['id'])))
        # The limit has two sides: at it nothing is above, and the oracle accepts on both sides of it.
        if case['id'].endswith(('.at-limit', '.above-limit')):
            if case['reads_metadata_above_limit'] != case['id'].endswith('.above-limit'):
                problems.append(case['id'] + ': the oracle ' + ('reads' if case['reads_metadata_above_limit'] else 'does not read') + ' a metadata block above the limit')
            if 'result' not in case['oracle']:
                problems.append(case['id'] + ': the oracle does not accept the archive')
    if not report['at_least_4_gib']:
        problems.append('the archive is smaller than 4 GiB')
    for name, entry_ in measured.items():
        bound = PORT_RSS_LIMIT_AT_METADATA_LIMIT_MIB if name.endswith('.at-limit') else PORT_RSS_LIMIT_MIB
        if entry_['port_peak_rss_mib'] > bound:
            problems.append('%s: the port peaked at %d MiB, over the bound of %d' % (name, entry_['port_peak_rss_mib'], bound))
    if args.check and args.mib == 4200 and GOLDEN.read_text() != text:
        problems.append('outcomes differ from test/vectors/port-large.golden')
    elif not args.check and args.mib == 4200:
        GOLDEN.write_text(text)
    print(json.dumps({'decompressed_bytes': report['decompressed_bytes'], 'compressed_bytes': compressed, 'port_rss_limit_mib': PORT_RSS_LIMIT_MIB,
                      'port_rss_limit_at_metadata_limit_mib': PORT_RSS_LIMIT_AT_METADATA_LIMIT_MIB, 'metadata_limit_bytes': METADATA_LIMIT,
                      'cases': {case['id']: case['classification'] for case in report['cases']}, 'measured': measured, 'problems': problems}))
    return int(bool(problems))


if __name__ == '__main__':
    sys.exit(main())
