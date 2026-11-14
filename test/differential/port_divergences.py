#!/usr/bin/env python3
"""Write test/vectors/port-divergences.golden: inputs on which the TS port deliberately answers differently.

Each entry has the input, the oracle's outcome, the port's outcome and the
reason. The oracle outcome is taken from the oracle here; the port outcome is
taken from the compiled port here. Afterwards both are pinned: the unit suites
fail if either engine stops answering what its entry says (test_ts_port.py for
the oracle, port.test.js for the port). `reviewed` is false until a review
accepts the entry; scripts/check-port-ready blocks on it.

    python3 test/differential/port_divergences.py [--check]
"""
import base64, hashlib, json, subprocess, sys
from pathlib import Path
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ts_port
from record import canonical
ROOT = ts_port.ROOT
R = '${ROOT}'
GZIP = ('D1: the port reads the gzip container of an archive strictly. CPython reads it lazily, in 8 KiB steps and only as far as the tar listing and the one '
        'manifest need, never checks the CRC-32 and length trailer it does not reach, and lets a truncated stream escape as EOFError or zlib.error, which '
        'archive.py:138 does not catch. The port decompresses and checks the whole container first and refuses any damage as "unreadable archive or manifest". ')


def archive(text='{"name": "example-package", "version": "1.0.0"}', padding=0):
    members = [['package/package.json', text], ['package/index.js', 'x' * padding]]
    return bytearray(ts_port.tgz(members))


def cases():
    whole = archive()
    crc = archive(); crc[-8] ^= 1
    length = archive(); length[-1] ^= 0x40
    junk = archive() + b'not gzip'
    no_trailer = archive()[:-8]
    cut_in_manifest = archive()[:10 + 5 + 512 + 20]
    cut_in_later_member = archive(padding=20000)[:10 + 5 + 512 + 512 + 512 + 9000]
    magic = bytearray(b'\x1f\x8b')
    return [
        ('gzip.crc-trailer', crc, 'Here: one bit of the CRC-32 trailer is flipped. The oracle accepts the archive; the port refuses it.'),
        ('gzip.length-trailer', length, 'Here: the stored length in the trailer is wrong. The oracle accepts the archive; the port refuses it.'),
        ('gzip.trailing-junk', junk, 'Here: bytes that are not a gzip member follow the stream. The oracle never reads them and accepts; the port refuses.'),
        ('gzip.missing-trailer', no_trailer, 'Here: the eight trailer bytes are missing. The oracle has all the data it asks for and accepts; the port refuses.'),
        ('gzip.truncated-after-manifest', cut_in_later_member, 'Here: the stream ends inside a later member. The oracle raises EOFError, a runtime error no handler of the engine names; the port refuses with the closed archive refusal.'),
        ('gzip.truncated-in-manifest', cut_in_manifest, 'Here: the stream ends inside the manifest. The oracle raises EOFError; the port refuses with the closed archive refusal.'),
        ('gzip.magic-only', magic, 'Here: the file is the two magic bytes. The oracle raises EOFError; the port refuses with the closed archive refusal.'),
    ], whole


def build():
    ts_port.initialize()
    rows, jobs = [], []
    listed, whole = cases()
    for ident, data, why in listed:
        data = bytes(data)
        row = {'id': ident, 'reason': GZIP + why, 'function': 'connectome_archive.archive_manifest', 'args': [entry_for(data, R)], 'kwargs': {},
               'tree': {'archives/case.tgz': {'file': base64.b64encode(data).decode()}}, 'reviewed': False}
        vector = ts_port.Vector('divergence', ident, row)
        args, kwargs = vector.prepare()
        row['oracle'] = json.loads(vector.unbind(canonical(ts_port.invoke(row['function'], ts_port.decode(args), ts_port.decode(kwargs)))))
        jobs.append((vector, {'id': ident, 'function': row['function'], 'args': args, 'kwargs': kwargs}))
        rows.append(row)
    lines = ts_port.node_outcomes([job for _vector, job in jobs], 'node')
    for row, (vector, _job), line in zip(rows, jobs, lines):
        row['port'] = json.loads(vector.unbind(line))
        vector.cleanup()
    rows = [row for row in rows if row['oracle'] != row['port']]
    return json.dumps(rows, indent=1) + '\n'


def entry_for(data, root):
    return {'name': 'example-package', 'archive': root + '/archives/case.tgz', 'sha256': hashlib.sha256(data).hexdigest(), 'kind': 'artifact', 'version': '1.0.0'}


if __name__ == '__main__':
    text = build()
    if '--check' in sys.argv[1:]:
        sys.exit(int(ts_port.DIVERGENCES.read_text() != text))
    ts_port.DIVERGENCES.write_text(text)
    for row in json.loads(text):
        print(row['id'], '| oracle', json.dumps(row['oracle'])[:70], '| port', json.dumps(row['port'])[:60])
