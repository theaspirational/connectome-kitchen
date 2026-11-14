#!/usr/bin/env python3
"""Archives of 4 GiB and more: the TS port against the oracle, with the peak memory of each.

Two properties the small corpora cannot show are held here.

  * Size is not damage. A well-formed archive whose decompressed stream is
    4 GiB or more must be read as the oracle reads it. (`zlib.crc32` of Node
    22.23.3 and 24.14.0 takes the length of a buffer modulo 2**32, so a reader
    that checks the CRC of the whole stream in one call refuses every such
    archive; that is not divergence D1, which is about damaged containers.)
  * The reader does not hold the decompressed stream. Its peak resident memory
    on these archives stays under PORT_RSS_LIMIT_MIB.

Cases, all on one archive of a manifest and one member of zeros:

    valid        the archive as written. CPython's gzip module reads it to its
                 end; both engines must give the same outcome.
    crc-trailer  one bit of its CRC-32 trailer flipped. CPython's gzip module
                 fails on a full read, so this is a D1 input: the oracle never
                 reaches the trailer and accepts, the port must refuse it as
                 unreadable. It shows that the CRC of a stream this long is
                 really computed, not skipped.

Outcomes are taken from both engines here and compared with
test/vectors/port-large.golden. The archive is built on the spot (about 4 MiB
of deflate data for 4.1 GiB of zeros) and deleted afterwards.

    python3 test/differential/port_large.py [--check] [--node node] [--mib 4200]
"""
import argparse, hashlib, json, os, shutil, struct, subprocess, sys, tarfile, zlib
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / 'test/vectors/port-large.golden'
SCRATCH = ROOT / '.cache/port-large' / str(os.getpid())
PORT_RSS_LIMIT_MIB = 256
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


def build(node, mib):
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)
    me = [sys.executable, '-B', str(Path(__file__).resolve()), '--oracle-child']
    try:
        path = SCRATCH / 'large.tgz'
        length = write_archive(path, mib)
        flipped = SCRATCH / 'large-crc.tgz'
        data = bytearray(path.read_bytes())
        data[-8] ^= 1
        flipped.write_bytes(data)
        del data
        cases, measured = [], {}
        for name, file in (('valid', path), ('crc-trailer', flipped)):
            record = entry(file)
            container, _ = child(me + [json.dumps({'container': str(file)})])
            oracle, oracle_rss = child(me + [json.dumps({'entry': record})])
            port, port_rss = port_outcome(node, record, SCRATCH)
            unbind = lambda text: json.loads(text.replace(json.dumps(str(SCRATCH))[1:-1], '${SCRATCH}'))
            oracle, port = unbind(oracle.strip()), unbind(port)
            if oracle == port:
                kind = 'equal'
            elif container.strip() != 'valid' and port.get('message') == 'unreadable archive or manifest':
                kind = 'D1'
            else:
                kind = 'MISMATCH'
            cases.append({'id': 'large.' + name, 'full_gzip_read': container.strip(), 'oracle': oracle, 'port': port, 'classification': kind})
            measured[name] = {'oracle_peak_rss_mib': oracle_rss, 'port_peak_rss_mib': port_rss}
        return {'decompressed_bytes': length, 'at_least_4_gib': length >= 1 << 32, 'cases': cases}, measured, path.stat().st_size
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)


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
    wanted = {'large.valid': 'equal', 'large.crc-trailer': 'D1'}
    for case in report['cases']:
        if case['classification'] != wanted[case['id']]:
            problems.append(case['id'] + ' is ' + case['classification'] + ', not ' + wanted[case['id']])
    if not report['at_least_4_gib']:
        problems.append('the archive is smaller than 4 GiB')
    for name, entry_ in measured.items():
        if entry_['port_peak_rss_mib'] > PORT_RSS_LIMIT_MIB:
            problems.append('%s: the port peaked at %d MiB, over the bound of %d' % (name, entry_['port_peak_rss_mib'], PORT_RSS_LIMIT_MIB))
    if args.check and args.mib == 4200 and GOLDEN.read_text() != text:
        problems.append('outcomes differ from test/vectors/port-large.golden')
    elif not args.check and args.mib == 4200:
        GOLDEN.write_text(text)
    print(json.dumps({'decompressed_bytes': report['decompressed_bytes'], 'compressed_bytes': compressed, 'port_rss_limit_mib': PORT_RSS_LIMIT_MIB,
                      'cases': {case['id']: case['classification'] for case in report['cases']}, 'measured': measured, 'problems': problems}))
    return int(bool(problems))


if __name__ == '__main__':
    sys.exit(main())
