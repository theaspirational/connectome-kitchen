#!/usr/bin/env python3
"""Write test/vectors/port-divergences.golden: inputs on which the TS port deliberately answers differently.

Each entry has the input, the oracle's outcome, the port's outcome, the reason
and the divergence it belongs to (D1, D2, D3). The oracle outcome is taken from the
oracle here; the port outcome is taken from the compiled port here. Afterwards
both are pinned: the unit suites fail if either engine stops answering what its
entry says (test_ts_port.py for the oracle, port.test.js for the port).

`ruling` names the entry of test/port-rulings.golden that accepted the
divergence; an entry without one blocks scripts/check-port-ready. The table
RULED below is the only place a ruling is attached, and it is edited by hand
when a ruling arrives, never derived.

    python3 test/differential/port_divergences.py [--check]
"""
import base64, hashlib, json, subprocess, sys, tarfile
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
CODE = ('On a damaged container the port answers before it looks at the tar stream, so a refusal the oracle would have given for the content is replaced: ')
NAN = ('D2: `required_names` sorts a set. With a NaN among two or more numbers Python does not refuse; every comparison with NaN is false and the order that '
       'comes out follows the address of the float object, so it is not pinned here (`unordered`). The port raises TypeError instead of modelling it. ')
LIMIT = ('D3: the port refuses a long-name, long-link or pax header whose size field states more than 2**28 bytes (268,435,456), before it reads the blocks, as '
         '"unreadable archive or manifest". CPython has no such limit: it reads what the header states and decodes it into a str of any length. The archives on '
         'both sides of the limit, which hold a block of that size, are built by test/differential/port_large.py; the entries here are small. ')
HUGE = ('Here: a header of type %s states 2**62 bytes and nothing follows it. The oracle asks for a read of that size and ends in MemoryError, a runtime error '
        'no handler of the engine names; the port refuses with the closed archive refusal.')
# Which ruling accepted which divergence (test/port-rulings.golden). Edited by hand when a ruling arrives.
RULED = {'D1': 'D1', 'D2': 'D2', 'D3': 'D3'}
MANIFEST = '{"name": "example-package", "version": "1.0.0"}'


def archive(text=MANIFEST, padding=0, manifest=True):
    members = ([['package/package.json', text]] if manifest else []) + [['package/index.js', 'x' * padding]]
    return bytearray(ts_port.tgz(members))


def crc_flipped(**how):
    data = archive(**how)
    data[-8] ^= 1
    return data


def cases():
    crc = crc_flipped()
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
        ('gzip.crc-trailer.identity-mismatch', crc_flipped(text='{"name": "other-package", "version": "1.0.0"}'),
         CODE + 'here the manifest names another package and the CRC-32 trailer is wrong. The oracle refuses with E_ARCHIVE_IDENTITY; the port refuses with E_ADAPTER_INPUT. The public code changes.'),
        ('gzip.crc-trailer.no-manifest', crc_flipped(manifest=False),
         CODE + 'here the archive has no manifest and the CRC-32 trailer is wrong. Both refuse with E_ADAPTER_INPUT; the oracle says "archive manifest required", the port "unreadable archive or manifest".'),
        ('gzip.crc-trailer.manifest-not-mapping', crc_flipped(text='[]'),
         CODE + 'here the manifest is a list and the CRC-32 trailer is wrong. Both refuse with E_ADAPTER_INPUT; the oracle says "archive manifest must be a mapping", the port "unreadable archive or manifest".'),
    ]


def metadata_cases():
    """A manifest, then one header that states 2**62 bytes, for each of the five header types the limit applies to."""
    def member(name, size, kind):
        info = tarfile.TarInfo(name)
        info.size, info.type = size, kind
        return info.tobuf(format=tarfile.GNU_FORMAT)
    manifest = member('package/package.json', len(MANIFEST), tarfile.REGTYPE) + MANIFEST.encode() + b'\0' * (-len(MANIFEST) % 512)
    kinds = (('longname', tarfile.GNUTYPE_LONGNAME, 'L (GNU long name)'), ('longlink', tarfile.GNUTYPE_LONGLINK, 'K (GNU long link)'), ('pax', tarfile.XHDTYPE, 'x (pax extended header)'),
             ('pax-global', tarfile.XGLTYPE, 'g (pax global header)'), ('pax-solaris', tarfile.SOLARIS_XHDTYPE, 'X (Solaris extended header)'))
    return [('tar-metadata.%s.states-2-to-the-62' % ident, ts_port.stored_gzip(manifest + member('metadata', 2 ** 62, kind)), HUGE % what) for ident, kind, what in kinds]


def number(text):
    return {'$float': text}


def nan_cases():
    nan = number('nan')
    return [
        ('required-names.nan.direct', {'required_secrets': [number('1.5'), nan]}, 'Here: a number and a NaN among the direct names.'),
        ('required-names.nan.three', {'required_secrets': [2, nan, 1]}, 'Here: two integers and a NaN.'),
        ('required-names.nan.secret-file', {'required_secrets': [1], 'secret_files': [{'secret': nan}]}, 'Here: the NaN is the `secret` of a secret file.'),
        ('required-names.nan.connector', {'required_secrets': [1], 'connectors': {'a': {'required_secrets': [nan]}}}, 'Here: the NaN is a name a connector requires.'),
    ]


def build():
    ts_port.initialize()
    rows, jobs = [], []
    for ident, data, why in cases():
        data = bytes(data)
        rows.append({'id': ident, 'divergence': 'D1', 'reason': GZIP + why, 'function': 'connectome_archive.archive_manifest', 'args': [entry_for(data, R)], 'kwargs': {},
                     'tree': {'archives/case.tgz': {'file': base64.b64encode(data).decode()}}})
    for ident, data, why in metadata_cases():
        rows.append({'id': ident, 'divergence': 'D3', 'reason': LIMIT + why, 'function': 'connectome_archive.archive_manifest', 'args': [entry_for(data, R)], 'kwargs': {},
                     'tree': {'archives/case.tgz': {'file': base64.b64encode(data).decode()}}})
    for ident, config, why in nan_cases():
        rows.append({'id': ident, 'divergence': 'D2', 'reason': NAN + why, 'function': 'connectome_config.required_names', 'args': [config], 'kwargs': {}, 'unordered': True})
    for row in rows:
        row['ruling'] = RULED.get(row['divergence'])
        vector = ts_port.Vector('divergence', row['id'], row)
        args, kwargs = vector.prepare()
        oracle = json.loads(vector.unbind(canonical(ts_port.invoke(row['function'], ts_port.decode(args), ts_port.decode(kwargs)))))
        row['oracle'] = ts_port.unordered(oracle) if row.get('unordered') else oracle
        jobs.append((vector, {'id': row['id'], 'function': row['function'], 'args': args, 'kwargs': kwargs}))
    lines = ts_port.node_outcomes([job for _vector, job in jobs], 'node')
    for row, (vector, _job), line in zip(rows, jobs, lines):
        row['port'] = json.loads(vector.unbind(line))
        vector.cleanup()
    same = [row['id'] for row in rows if (ts_port.unordered(row['port']) if row.get('unordered') else row['port']) == row['oracle']]
    if same:
        raise SystemExit('not a divergence: ' + ', '.join(same))
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
