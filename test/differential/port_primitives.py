#!/usr/bin/env python3
"""Per-helper differential: the Python-semantics helpers of the TS port against live CPython.

Each case is an input only. CPython (the oracle interpreter, with the frozen
engine for the few function-level kinds) computes the answer here; the TS
helpers get the same input through test/differential/port_primitives.mjs; the
two answers are compared exactly. No expected value is stored anywhere.

    python3 test/differential/port_primitives.py run [--node node] [--scale 1.0] [--kind NAME ...]

Prints `{kind: {cases, mismatches}}`, a few witnesses per failing kind, exits 1
on any mismatch. Everything is seeded; nothing depends on time or hash order.

Limits of the comparison. Each is a property of the two runtimes, stated here
rather than hidden in a generator:
  * A Python str can hold a high surrogate directly followed by a low one as
    two code points; a JavaScript string cannot tell that from one astral
    character. Inputs never contain that adjacency, and answers are compared
    after the same joining JSON applies to both sides.
  * `json.loads` depth bounds are stated for a call from a script's top level,
    so those cases run in a fresh interpreter (see BARE).
  * `str.lower()` is swept over the code points this interpreter's Unicode
    version assigns. The JavaScript runtime's tables are newer and lowercase
    letters added since; nothing in the engine can observe that.
  * `urlsplit` is whatever the oracle interpreter ships. Distributions backport
    fixes into it (bracketed hosts, here), so "3.12.3" does not pin it.
  * `tar` compares `tarfile.open(..., 'r:gz')`, `getmembers()` and
    `extractfile().read()` on valid gzip containers. A member is read on both
    sides when tarfile reads it as a file (a regular or an unknown type) and
    its size is at most 16 MiB; archive.py reads at most 1 MiB. Link members
    are listed and not read. A read request above 2**36 bytes is MemoryError
    in the model; on the oracle host the real bound lies between 2**36 and
    2**38 and follows the machine's memory, so no case asks for a size in
    that band.
  * `tar` is not an equality in one place, divergence D3: where tarfile comes
    to read a long-name, long-link or pax header that states more than 2**28
    bytes, the TS reader must refuse with its OSError, whatever tarfile
    answers. Those cases are counted by what the oracle does with them
    (`metadata_limit`). No generated archive holds that many bytes: the
    oracle reads a short block and then refuses the archive, lets a
    MemoryError or OverflowError escape, or, where the short block is no
    pax record and the header is not the first member, ends the listing
    silently and so lists the archive. test/differential/port_large.py has
    archives that really hold such a block.
  * `tarbudget` is `tar` read under admission budgets small enough for a few
    blocks to meet (divergence D4). The oracle has no budgets. Each archive
    is walked a second time with the oracle's own tarfile, which counts what
    the TS reader counts at the same points (test/differential/tar_budget.py).
    Where that walk passes a budget, the TS reader must refuse with the
    OSError of that very budget; where it passes none, the two listings must
    be equal, so a budget that is not reached changes nothing. The cases are
    counted by the budget that refused (`budgets`).
  * `tardepth` runs in a fresh interpreter, in a function the script calls
    directly: chains of extension headers recurse in tarfile, and the bound
    (328 as first member, 329 later) moves with the caller's depth.
  * `targz` is a classification, not an equality: the TS reader checks the
    whole gzip container first, the oracle decompresses only as far as it
    reads. A container gzip reads to its end must give the same outcome of
    the archive.py:131-139 flow on both sides; a damaged one must be an
    OSError in TS, and is counted by what the oracle does with it.
"""
import argparse, base64, binascii, datetime, hashlib, json, multiprocessing, os, posixpath, random, stat, struct, subprocess, sys, urllib.parse
from pathlib import Path, PurePosixPath
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'oracle/module_utils'))
import connectome_errors as engine_errors
import connectome_lock as engine_lock

SCRATCH = ROOT / '.cache/port-primitives'
FS = ROOT / '.cache/prim-fs' / str(os.getpid())  # one tree per run, so that two runs never share links
AT = chr(64)
COLONS = ':' * 2


# ---------------------------------------------------------------- transport

def tag(value):
    """JSON-model value -> JSON that keeps number kind and key order."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return {'i': str(value)}
    if isinstance(value, float):
        return {'f': repr(value)}
    if isinstance(value, list):
        return {'l': [tag(item) for item in value]}
    if isinstance(value, dict):
        return {'d': [[key, tag(item)] for key, item in value.items()]}
    raise TypeError('not a JSON-model value')


def b64(data):
    return base64.b64encode(data).decode()


def joined(value):
    """What both sides look like after JSON transport: adjacent surrogate halves are one character."""
    return json.loads(json.dumps(value))


def apart(text):
    """Drop a low surrogate that directly follows a high one (see the module docstring)."""
    out = []
    for char in text:
        if out and '\ud800' <= out[-1] <= '\udbff' and '\udc00' <= char <= '\udfff':
            continue
        out.append(char)
    return ''.join(out)


def failure(error, message=False):
    return {'error': [type(error).__name__, str(error)] if message else type(error).__name__}


# ---------------------------------------------------------------- generators

SPICE = ['\x00', '\x01', '\t', '\n', '\r', '\x0b', '\x0c', '\x1c', '\x1f', ' ', '\x7f', '\x80', '\x85', '\x9f', '\xa0', '\xad', '\xe9', '\u0130', '\u0131',
         '\u017f', '\u212a', '\u0660', '\u2028', '\u2029', '\u3000', '\ufeff', '\uff0f', '\uff1a', '\uff20', '\uff1f', '\uff03', '\u2100', '\u2488',
         '\ud800', '\udbff', '\udc00', '\udc80', '\udcff', '\udfff', '\U00010000', '\U0001f600', '\U00020000', '\U000e0001', '\U0010ffff', '\u0378', '\ue000']


def wild(rng, low, high, ascii_pool='abcXYZ019 _-./\\\'"%:@?#[]{}=&'):
    out = []
    for _ in range(rng.randint(low, high)):
        roll = rng.random()
        if roll < 0.55:
            out.append(rng.choice(ascii_pool))
        elif roll < 0.80:
            out.append(rng.choice(SPICE))
        elif roll < 0.90:
            out.append(chr(rng.randrange(0x80, 0x3000)))
        else:
            out.append(chr(rng.randrange(0x110000)))
    return apart(''.join(out))


def gen_unquote(rng, count):
    hexes = '0123456789abcdefABCDEF'
    fixed = ['%EF%BB%BF', '%ef%bb%bfx', '%C0%80', '%E0%80%80', '%ED%A0%80', '%ED%B0%80', '%ED%A0%BD%ED%B8%80', '%F0%9F%98%80', '%F4%90%80%80', '%F8%88%80%80%80',
             '%E2%82', '%E2%82%AC', '%E2a%82%AC', '%E2\xe9%82%AC', '%', '%%', '%4', '%4g', '%g4', '%41', '%2541', '%00', '%7F', '%80', '%C2%80', '%c2%9f', '%FF%FE', '']
    jobs = [{'s': item} for item in fixed]
    for _ in range(count):
        out = []
        for _ in range(rng.randint(0, 14)):
            roll = rng.random()
            if roll < 0.45:
                out.append('%' + rng.choice(hexes) + rng.choice(hexes))
            elif roll < 0.55:
                out.append(rng.choice(['%', '%' + rng.choice(hexes), '%' + rng.choice('gGzZ ') + rng.choice(hexes), '%%']))
            elif roll < 0.70:
                out.append(''.join('%%%02X' % byte for byte in chr(rng.choice([0xe9, 0x20ac, 0x1f600, 0xfeff, 0x80, 0x7ff, 0x800, 0xffff, 0x10000])).encode()))
            elif roll < 0.80:
                out.append(''.join('%%%02x' % byte for byte in rng.choice([b'\xc0\xaf', b'\xe0\x9f\xbf', b'\xed\xa0\x80', b'\xf0\x8f\xbf\xbf', b'\xf4\x90\x80\x80', b'\xe2\x82', b'\xf0\x9f\x98', b'\xff', b'\x80\x80'])))
            else:
                out.append(wild(rng, 1, 3))
        jobs.append({'s': apart(''.join(out))})
    return jobs


def py_unquote(job):
    return urllib.parse.unquote(job['s'])


def ipv6_text(rng):
    clean = rng.random() < 0.6  # only well-formed fields; the structure still decides
    good = ['0', '1', 'a', 'ff', 'FFFF', 'abcd', '0000', '00a', 'fe80']
    hextet = lambda: rng.choice(good if clean else good + ['12345', 'g', '', '00000', 'x1', chr(0x661)])
    octet = lambda: rng.choice(['0', '1', '10', '255'] if clean else ['0', '1', '10', '255', '256', '01', '1a', '', '999', chr(0x661), '00'])
    dotted = lambda: '.'.join(octet() for _ in range(4 if clean else rng.choice([4, 4, 3, 5])))
    roll = rng.random()
    if roll < 0.25:
        parts = [hextet() for _ in range(rng.choice([8, 8, 8, 7, 9]))]
    elif roll < 0.70:
        parts = [hextet() for _ in range(rng.randint(0, 8))]
        parts.insert(rng.randint(0, len(parts)), '')
        if rng.random() < 0.15:
            parts.insert(rng.randint(0, len(parts)), '')
    elif roll < 0.88:
        parts = [hextet() for _ in range(rng.choice([6, 6, 5, 7]) if rng.random() < 0.4 else rng.randint(0, 6))]
        if len(parts) != 6 or rng.random() < 0.3:
            parts.insert(rng.randint(0, len(parts)), '')
        parts.append(dotted())
    else:
        return rng.choice(['', ':', COLONS, COLONS + ':', ':' * 4, 'v1.x', 'vF.a:b', 'v.x', 'v1.', 'vg.x', 'V1.x', 'v1.x' + chr(0xe9), 'v1', 'v12ab.~', dotted(), 'x', 'example.invalid', '1'])
    text = ':'.join(parts)
    edge = rng.random()
    if edge < 0.2:
        text = ':' + text
    elif edge < 0.4:
        text = text + ':'
    elif edge < 0.5:
        text = ':' + text + ':'
    if rng.random() < 0.25:
        text += rng.choice(['%eth0', '%', '%a%b', '%25en', '%' + chr(0xe9), '%1', '% ', '%]', '%['] if not clean else ['%eth0', '%25en', '%1', '%E'])
    return text


def gen_url_text(rng):
    if rng.random() < 0.45:  # mostly well-formed, so that the accepting branches are reached as often as the refusing ones
        scheme = rng.choice(['https', 'https', 'ssh', 'ssh', 'HTTPS', 'sSh', 'http', 'git+ssh', 'file'])
        user = rng.choice(['', '', '', 'git', 'a.b_c-d', '_', '-x', 'u%2Ev', 'u%20v', 'u:pw', 'u:', 'a b', '%2Dx'])
        host = rng.choice(['example.invalid', 'EXAMPLE.Invalid', 'h', 'xn--a', '-host', '%2Dhost', 'h%25x', '[' + ipv6_text(rng) + ']', '[' + ipv6_text(rng) + ']', ''])
        port = rng.choice(['', '', '', ':22', ':0', ':65535', ':65536', ':', ':x', ':022'])
        path = rng.choice(['/repo.git', '/a/b', '/', '', '/a%20b', '/a b', '/a%3Fb', '/a\\b', '/a?b', '/a#b', '/a%0Ab', '/%C2%85'])
        text = scheme + '://' + (user + AT if user else '') + host + port + path
        if rng.random() < 0.25:
            at = rng.randint(0, len(text))
            text = text[:at] + rng.choice(['[', ']', AT, ':', '/', '%', '\t', ' ', '-', 'v', '.']) + text[at:]
        return text
    lead = rng.choice(['', '', '', '', ' ', '\t', '\x00', '\x1f \n', '\xa0', '\ufeff'])
    scheme = rng.choice(['https', 'https', 'ssh', 'ssh', 'HTTPS', 'Ssh', 'http', 'git', 'file', 'ftp', 'ext', 'git+ssh', 'h2', '1x', 'a.b-c+d', 'hTTps', '', 'ht\ntps', '\u017fsh', 'ssh\u0130', 'http\u00e9'])
    colon = rng.choice([':', ':', ':', ':', '', COLONS])
    slashes = rng.choice(['//', '//', '//', '//', '//', '/', '', '///', '/\t/', '\\\\'])
    user = rng.choice(['git', 'user', 'a.b_c-d', '-x', '', 'a b', 'u%40v', '%2Dx', '\u01f5it', 'a[b', 'a]b', '[x]', 'a' + AT + 'b', '_'])
    secret = rng.choice(['pw', '', 'p:w', 'p' + AT + 'w', '%3A'])
    userinfo = rng.choice(['', '', '', user + AT, user + ':' + secret + AT, user + ':' + AT, ':' + secret + AT, AT, user + AT + user + AT])
    host = rng.choice(['example.invalid', 'example.invalid', 'EXAMPLE.Invalid', 'xn--a', '-host', '%2Dhost', '\u0130stanbul.invalid', '\u03a3\u0391\u03a3.invalid', 'a..b', '', 'localhost',
                       'h%25x', 'h%zone', 'H%Zone', 'a\uff0eb', 'a\u2100b', 'a\uff1ab', 'a\uff0fb', 'a\uff20b', 'a\uff1fb', 'a\uff03b', 'a\u2488b', '\ufe13', '\u2048', 'h\u00e9'])
    roll = rng.random()
    if roll < 0.45:
        inner = ipv6_text(rng)
        host = rng.choice(['[%s]', '[%s]', '[%s]', '[%s]', 'a[%s]', '[%s]x', '[%s', '%s]', '[[%s]]', '][%s', '[%s][', '[%s]' + AT]) % inner
    port = rng.choice(['', '', '', ':', ':22', ':0', ':65535', ':65536', ':00022', ':99999999999', ':2a', ':-1', ':\u0662\u0662', ':\uff12\uff12', ':22' + ':33', ': 22', ':22 ', ':\u00b2', ':' + '7' * 30, ':0000000000000000000022'])
    path = rng.choice(['', '/', '/repo.git', '/a/b', '/a b', '/a%20b', '/..', 'rel', '/a\\b', '/\u00e9', '/a;b', '//x'])
    query = rng.choice(['', '', '', '?', '?q=1', '?token=x', '?a#b'])
    fragment = rng.choice(['', '', '', '#', '#frag', '#a?b'])
    text = lead + scheme + colon + slashes + userinfo + host + port + path + query + fragment
    for _ in range(rng.choice([0, 0, 0, 1, 1, 2])):
        at = rng.randint(0, len(text))
        edit = rng.random()
        spice = rng.choice(['[', ']', AT, ':', '/', '?', '#', '%', '\t', '\n', '\r', ' ', '\uff1a', '\uff0f', '\u2100', '\ud800', '\x00', 'v', '.', '-'])
        text = text[:at] + spice + text[at:] if edit < 0.6 else text[:at] + text[at + 1:]
    return apart(text)


def gen_url(rng, count):
    return [{'s': gen_url_text(rng)} for _ in range(count)]


def py_url(job):
    try:
        parts = urllib.parse.urlsplit(job['s'])
    except ValueError:
        return {'split': 'ValueError'}
    out = {'split': [parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment],
           'hostname': parts.hostname, 'username': parts.username, 'password': parts.password}
    try:
        out['port'] = parts.port
    except ValueError:
        out['port'] = 'ValueError'
    return out


def gen_repository(rng, count):
    jobs = [{'s': gen_url_text(rng)} for _ in range(count)]
    users = ['git', 'a.b', '-x', '', 'a b', 'gi\tt', '\u01f5it', '_u', 'u-']
    hosts = ['example.invalid', '-h', 'h_', '1h', 'h.', '.h', 'H-1', 'h\u00e9', '']
    paths = ['repo.git', '/abs/repo', '', ' x', 'a#b', 'a?b', 'a\\b', 'a\nb', '%0Ab', '~/r', ':x', 'a%3Fb', 'a%23b', 'a%5Cb', 'a%20b', 'x//y', 'a\u2028b', 'token=abc', 'x/secret' + '/ab/c']
    prefixes = ['', '', '', '', 'https:', 'HTTP:', 'git:', 'file:', 'ftp:', 'ext:', 'ext' + COLONS, '\u017fsh:', 'f\u0131le:', 'f\u0130le:', 'g\u0130t:', '\u017f\u017fH:', 'http\u017f:', 'gopher:', 'x', 'ssh:', 'Ssh:']
    for _ in range(count):
        user = rng.choice(users)
        if rng.random() < 0.4:  # mostly well-formed scp syntax
            text = rng.choice(['', 'git' + AT, 'a.b' + AT, '_u' + AT, 'u-' + AT]) + rng.choice(['example.invalid', 'H-1', '1h', 'h.', 'h']) + ':' + rng.choice(['repo.git', '/abs/repo', '~/r', 'a%20b', 'x//y', 'a/b', '.', 'a:b', ':x', 'a b', 'a%0Ab', 'a%3Fb'])
            jobs.append({'s': text})
            continue
        text = rng.choice(prefixes) + (user + rng.choice([AT, AT, AT + AT]) if rng.random() < 0.6 else '') + rng.choice(hosts) + rng.choice([':', ':', ':', COLONS, '', '://']) + rng.choice(paths)
        jobs.append({'s': apart(text)})
    locals_ = ['/x/y', '/x?y', '/x%3Fy', '/x%23', '/a\\b', '/a%5Cb', '/ctrl\x01', '/%01', '/%7f', '/x%c2%80', '/x%C2%A0', '/ ', '/', '//', '/\u00e9', '/x%ED%A0%80', '', ' /x', '/x\n', '/x%0A',
               '/%EF%BB%BFx', 'https://example.invalid/%3Ftoken%3Dx', 'https://u%3Apw%40example.invalid/r', 'ssh://%2Dox' + AT + 'example.invalid/r', 'ssh://-ox/r', 'ssh://%2dox/r',
               'https://example.invalid', 'https://example.invalid/', 'ssh://git' + AT + 'example.invalid:22/r', 'https://git' + AT + 'example.invalid/r', 'ssh://example.invalid:0/r',
               'ssh://example.invalid:/r', 'ssh://u%20v' + AT + 'example.invalid/r', 'ssh://u%2Ev' + AT + 'example.invalid/r']
    jobs += [{'s': item} for item in locals_]
    return jobs


def py_repository(job):
    try:
        return engine_lock.repository(job['s'])
    except Exception as error:
        return failure(error)


def gen_b64(rng, count):
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    jobs = [{'s': item} for item in ['', '=', '==', 'A', 'AA', 'AAA', 'AAAA', 'AA==', 'AAA=', 'AA=', 'A===', 'AAAA=', 'AAAA==', '=AAA', 'AA==AA==', 'AA= =', 'AAA=\n', ' AAAA', 'AAAA ', 'AA\nAA', 'AA-_', 'AA=A', 'A=A=', 'AAAAA', 'AAAAAA', 'AAAAAAA', 'AB==', 'AAB=', '\u00e9AAA', 'AAA\u00e9', 'AAAA\x00', '====', 'AA======']]
    for _ in range(count):
        roll = rng.random()
        if roll < 0.35:
            text = b64(rng.randbytes(rng.randint(0, 12)))
        else:
            text = ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 12))) + '=' * rng.choice([0, 0, 1, 2, 3])
        for _ in range(rng.choice([0, 0, 1, 1, 2])):
            at = rng.randint(0, len(text))
            edit = rng.random()
            if edit < 0.5:
                text = text[:at] + rng.choice(['=', '=', ' ', '\n', '-', '_', '.', '\x00', '\u00e9', 'A', '\r']) + text[at:]
            else:
                text = text[:at] + text[at + 1:]
        jobs.append({'s': text})
    return jobs


def py_b64(job):
    try:
        return {'b': b64(base64.b64decode(job['s'], validate=True))}
    except ValueError as error:
        return failure(error)


def stamp(date, hour, minute, second, fraction=''):
    """The lock's timestamp shape after its `Z` was replaced, as `map_lock` hands it to `fromisoformat`."""
    return date + 'T' + ':'.join('%02d' % field for field in (hour, minute, second)) + fraction + '+00' + ':00'


def gen_iso(rng, count):
    jobs = []
    years = ['0000', '0001', '0004', '0100', '0400', '1900', '2000', '2023', '2024', '9999']
    for year in years:
        for month in range(0, 14):
            for day in range(0, 33):
                jobs.append({'s': stamp('%s-%02d-%02d' % (year, month, day), 0, 0, 0)})
    for hour in range(0, 26):
        jobs.append({'s': stamp('2024-02-29', hour, 0, 0)})
    for value in range(0, 62):
        jobs.append({'s': stamp('2024-02-29', 23, value, 0)})
        jobs.append({'s': stamp('2024-02-29', 23, 59, value)})
    for digits in range(1, 10):
        jobs.append({'s': stamp('2023-12-31', 23, 59, 59, '.' + '9' * digits)})
        jobs.append({'s': stamp('2023-02-29', 24, 60, 60, '.' + '0' * digits)})
    for _ in range(count):
        fraction = '.' + ''.join(rng.choice('0123456789') for _ in range(rng.randint(1, 12))) if rng.random() < 0.5 else ''
        date = '%04d-%02d-%02d' % (rng.choice([0, 1, 4, 1999, 2000, 2100, 2400, 9999]), rng.randint(0, 13), rng.randint(0, 32))
        jobs.append({'s': stamp(date, rng.randint(0, 25), rng.randint(0, 61), rng.randint(0, 61), fraction)})
    return jobs


def py_iso(job):
    try:
        datetime.datetime.fromisoformat(job['s'])
        return 'ok'
    except ValueError:
        return 'ValueError'


KEYS = ['a', 'b', 'a', '1', '2', '10', '01', '-1', '__proto__', 'constructor', '\u00e9', '\\u0061', '', 'a b', '\\ud83d\\ude00', '\U0001f600']


def json_string(rng):
    out = []
    for _ in range(rng.randint(0, 6)):
        roll = rng.random()
        if roll < 0.4:
            out.append(rng.choice('abc xyz019_-/{}[]:,'))
        elif roll < 0.6:
            out.append(rng.choice(['\\n', '\\t', '\\"', '\\\\', '\\/', '\\b', '\\f', '\\r', '\\u0000', '\\u00e9', '\\u2028', '\\ud83d\\ude00', '\\ud800', '\\udc00', '\\ude00\\ud83d', '\\uD83D', '\\uFFFF', '\\u00E9']))
        elif roll < 0.9:
            out.append(rng.choice(['\xe9', '\u4e2d', '\U0001f600', '\x7f', '\u2028', '\ufeff', '\ud800', '\udc80', '\x80']))
        else:
            out.append(chr(rng.randrange(0x20, 0x3000)).replace('"', 'q').replace('\\', 'b'))
    return '"' + ''.join(out) + '"'


def json_doc(rng, depth=0):
    gap = lambda: rng.choice(['', '', '', ' ', '\n', '\t', '\r', '  '])
    roll = rng.random()
    if depth > 3 or roll < 0.45:
        pick = rng.random()
        if pick < 0.12:
            return rng.choice(['null', 'true', 'false'])
        if pick < 0.35:
            return str(rng.choice([0, 1, -1, 7, 10 ** rng.randrange(40), -(10 ** rng.randrange(40)), rng.randrange(-2 ** 70, 2 ** 70), 2 ** 53 + 1])) if rng.random() < 0.9 else rng.choice(['-0', '0'])
        if pick < 0.37:
            return rng.choice(['1' * 4299, '9' * 4300, '1' * 4301, '-' + '1' * 4300, '-' + '1' * 4301, '0.' + '1' * 4400, '1' * 4400 + 'e0'])
        if pick < 0.62:
            return rng.choice(['1.0', '-0.0', '1e5', '1E+5', '1e-5', '0.1e-6', '1E400', '-1E400', '1e-400', '-1e-400', '123456789012345678901234567890.0', '1.5E3', '0e0', '-0e-0', '0.0000001',
                               '1e16', '1e22', '1e21', '5e-324', '2.2250738585072014e-308', '1.7976931348623157e308', '1.7976931348623159e308', '0.1', '2.5', '9007199254740993.0', '1e15', '123456789012345680.0',
                               repr(struct.unpack('>d', rng.randbytes(8))[0]).replace('nan', '0.5').replace('inf', '1e9')])
        if pick < 0.67:
            return rng.choice(['NaN', 'Infinity', '-Infinity'])
        return json_string(rng)
    if roll < 0.72:
        return '[' + gap() + (',' + gap()).join(json_doc(rng, depth + 1) + gap() for _ in range(rng.randint(0, 4))) + ']'
    items = []
    for _ in range(rng.randint(0, 4)):
        key = rng.choice(KEYS)
        items.append((json_string(rng) if rng.random() < 0.15 else '"' + key + '"') + gap() + ':' + gap() + json_doc(rng, depth + 1) + gap())
    return '{' + gap() + (',' + gap()).join(items) + '}'


def corrupt(rng, text):
    at = rng.randint(0, len(text))
    edit = rng.random()
    spice = rng.choice(['{', '}', '[', ']', '"', ',', ':', '\\', ' ', '\t', '\n', '\x00', '\x01', '0', '1', '-', '+', '.', 'e', 'E', 'N', 'I', 'n', 't', 'f', 'u', 'a', '\ufeff', '\u2028', "'", '/',
                        'NaN', 'Infinity', '-Infinity', 'null', '\\u', '\\ud800', '\xa0', '\x0c', '\x0b', '//x', '1e', '0x1', '01', '-', 'nul', 'True'])
    if edit < 0.35:
        return text[:at] + spice + text[at:]
    if edit < 0.6:
        return text[:at] + text[at + 1:]
    if edit < 0.75:
        return text[:at] + spice + text[at + 1:]
    if edit < 0.85:
        return text[:at]
    if edit < 0.92:
        return '\ufeff' + text
    return text + spice


def gen_json(rng, count):
    jobs = []
    for index in range(count):
        text = json_doc(rng)
        if rng.random() < 0.6:
            text = corrupt(rng, text)
            if rng.random() < 0.2:
                text = corrupt(rng, text)
        text = apart(text)
        for mode in ('plain', 'hooks'):
            jobs.append({'mode': mode, 's': text})
        if index % 2 == 0:
            encoding = rng.choice(['utf-8', 'utf-8', 'utf-8-sig', 'utf-16-le', 'utf-16-be', 'utf-16', 'utf-32-le', 'utf-32-be', 'utf-32'])
            data = text.encode(encoding, 'surrogatepass')
            if encoding in ('utf-16', 'utf-32') and rng.random() < 0.5:  # the other byte order, with its mark
                swapped = encoding + '-be'
                data = ('\ufeff' + text).encode(swapped, 'surrogatepass')
            roll = rng.random()
            if roll < 0.15 and data:
                data = data[:len(data) - rng.randint(1, min(3, len(data)))]
            elif roll < 0.30:
                at = rng.randint(0, len(data))
                data = data[:at] + rng.choice([b'\xff', b'\x00', b'\xc0', b'\xed\xa0\x80', b'\xed\xa0\xbd\xed\xb8\x80', b'\x80', b'\xf4\x90\x80\x80', b'\x00\x00', b'\xef\xbb\xbf', b'\xfe\xff', b'\x00\xd8', b'\x00\x00\x11\x00']) + data[at:]
            elif roll < 0.36 and data:
                at = rng.randrange(len(data))
                data = data[:at] + bytes([data[at] ^ (1 << rng.randrange(8))]) + data[at + 1:]
            jobs.append({'mode': rng.choice(['plain', 'plain', 'hooks']), 'b': b64(data)})
    for item in [b'', b' ', b'0', b'00', b'\x00', b'\x000', b'0\x00', b'\x00\x00\x000', b'0\x00\x00\x00', b'[]', b'\xef\xbb\xbf[]', b'\xef\xbb\xbf\xef\xbb\xbf[]', b'\xff\xfe[\x00]\x00', b'\xfe\xff\x00[\x00]',
                 b'[\x00]\x00', b'\x00[\x00]', b'\xff\xfe\x00\x00[\x00\x00\x00]\x00\x00\x00', b'\x00\x00\xfe\xff\x00\x00\x00[\x00\x00\x00]', b'"\xed\xa0\x80"', b'"\xed\xa0\xbd\xed\xb8\x80"', b'"\xf0\x9f\x98\x80"',
                 b'"\xc0\xaf"', b'"\xed\xa0"', b'"\x00\xd8"', b'"\x00=\xd8\x00\xde"\x00', b'1\x00', b'\x001', b'1\x00\x00', b'12', b'1\x002\x00']:
        jobs.append({'mode': 'plain', 'b': b64(item)})
    for item in ['', ' ', '\ufeff', '\ufeff1', '1 \ufeff', '\xa01', '1\x0c', '[1,]', '{"a":1,}', '[,1]', '{,}', '{"a"}', '{"a":}', '{1:2}', "{'a':1}", '-', '-a', '1.', '.5', '1.e3', '1e', '1e+', '01', '-01', '0x10', '+1',
                 'nul', 'NaNx', 'Infinit', '-Infinit', '-NaN', '+Infinity', '[NaN]', '{"a":NaN,"a":1}', '{"a":1,"a":NaN}', '{"a":{"b":1,"b":2},"a":NaN}', '[{"a":1,"a":2},NaN]', '[NaN,{"a":1,"a":2}]',
                 '{"a":1,"a":2}x', '{"a":1,"a":2', '"\\ud83d\\ude00"', '"\\ud83d\\u0041"', '"\\ud83d\\', '"\\u12"', '"\\u12G4"', '"a\x1fb"', '"a\x7fb"', '"\\x41"', '"\\U00000041"', '"', '"\\"', '[] []', '[]]', '{}{}',
                 '\u0661', '1\u0662', '[\u00a0]', ' \t\n\r1\r\n\t ', '\x0b1', 'true', 'TRUE', 'tru', '{"\\u0061":1,"a":2}', '{"a":1,"\\u0061":2}', '{"__proto__":1,"__proto__":2}', '{"":1,"":2}']:
        for mode in ('plain', 'hooks'):
            jobs.append({'mode': mode, 's': item})
    return jobs


class HookDuplicate(ValueError):
    pass


class HookConstant(ValueError):
    pass


def hook_require(condition, message):
    if not condition:
        raise HookDuplicate(message)


def hook_pairs(pairs):
    result = {}
    for key, value in pairs:
        hook_require(key not in result, 'duplicate')
        result[key] = value
    return result


def hook_constant(_value):
    raise HookConstant('constant')


def py_json(job):
    source = job['s'] if 's' in job else base64.b64decode(job['b'])
    options = {'object_pairs_hook': hook_pairs, 'parse_constant': hook_constant} if job['mode'] == 'hooks' else {}
    try:
        return {'v': tag(json.loads(source, **options))}
    except (ValueError, RecursionError) as error:
        return failure(error)


# Runs at the top level of a fresh interpreter: the frame depth the bounds in pyjson.ts are stated for.
BARE = r'''
import json, sys
class HookDuplicate(ValueError): pass
class HookConstant(ValueError): pass
def hook_require(condition, message):
    if not condition: raise HookDuplicate(message)
def hook_pairs(pairs):
    result = {}
    for key, value in pairs:
        hook_require(key not in result, 'duplicate')
        result[key] = value
    return result
def hook_constant(_value): raise HookConstant('constant')
def hook_dict(pairs): return dict(pairs)
OPTIONS = {'plain': {}, 'hooks': {'object_pairs_hook': hook_pairs, 'parse_constant': hook_constant}, 'pairs-dict': {'object_pairs_hook': hook_dict}}
for line in sys.stdin:
    job = json.loads(line)
    source = job['open'] * job['depth'] + job['leaf'] + job['close'] * job['depth']
    try: json.loads(source.encode() if job.get('bytes') else source, **OPTIONS[job['mode']]); outcome = 'ok'
    except (ValueError, RecursionError) as error: outcome = type(error).__name__
    print(outcome)
'''


def gen_depth(rng, count):
    jobs = []
    shapes = [('[', '', ']'), ('[', '1', ']'), ('[', '"\\ud83d\\ude00"', ']'), ('[', '1E400', ']'), ('{"a":', '1', '}'), ('{"a":', '{}', '}'), ('[', '{}', ']'), ('[', '{"a":1}', ']'),
              ('[', '{"a":1,"a":2}', ']'), ('{"k":', '{"a":1,"a":2}', '}'), ('[', 'NaN', ']'), ('{"a":', 'NaN', '}'), ('[', '-Infinity', ']'), ('[', 'x', ']'), ('{"a":', 'x', '}'), ('[', '"abc', ''),
              ('[', '"a\x01b"', ']'), ('[', '"a\\qb"', ']'), ('[', '1', ''), ('[', '1 2', ']'), ('[', '{a:1}', ']'), ('[', '{"a" 1}', ']'), ('[', '{"a":1,}', ']'), ('[', '[,]', ']'), ('[', '1' * 4301, ']'),
              ('[', '{"a":NaN,"a":1}', ']'), ('[', '{"a":1,"a":NaN}', ']'), ('{"a":', '', '}'), ('[', '{', ']'), ('[', '"\\u12"', ']')]
    for opening, leaf, closing in shapes:
        for depth in range(9990, 10000):
            for mode in ('plain', 'hooks', 'pairs-dict'):
                jobs.append({'mode': mode, 'open': opening, 'depth': depth, 'leaf': leaf, 'close': closing})
        jobs.append({'mode': 'hooks', 'open': opening, 'depth': 9994, 'leaf': leaf, 'close': closing, 'bytes': True})
        jobs.append({'mode': 'hooks', 'open': opening, 'depth': 9995, 'leaf': leaf, 'close': closing, 'bytes': True})
    return jobs


def bare_outcomes(jobs):
    done = subprocess.run([sys.executable, '-I', '-c', BARE], input=''.join(json.dumps(job) + '\n' for job in jobs), capture_output=True, text=True, timeout=600)
    if done.returncode:
        raise RuntimeError('bare interpreter failed: ' + done.stderr[-500:])
    lines = done.stdout.split('\n')[:-1]
    if len(lines) != len(jobs):
        raise RuntimeError('bare interpreter answered %d of %d' % (len(lines), len(jobs)))
    return lines


def gen_utf8(rng, count):
    pieces = [b'a', b'\x00', b'\x7f', b'\x80', b'\xbf', b'\xc0', b'\xc1', b'\xc2', b'\xc2\x80', b'\xdf\xbf', b'\xe0', b'\xe0\x80', b'\xe0\x9f\xbf', b'\xe0\xa0\x80', b'\xed\x9f\xbf', b'\xed\xa0\x80', b'\xed\xbf\xbf',
              b'\xed\xa0', b'\xee\x80\x80', b'\xef\xbb\xbf', b'\xef\xbf\xbf', b'\xf0', b'\xf0\x80', b'\xf0\x8f\xbf\xbf', b'\xf0\x90\x80\x80', b'\xf0\x9f\x98', b'\xf0\x9f\x98\x80', b'\xf4\x8f\xbf\xbf', b'\xf4\x90\x80\x80',
              b'\xf5', b'\xf8\x88\x80\x80\x80', b'\xfe', b'\xff', b'\xe2\x82\xac', b'\xe2\x82', b'\xe2', b'\xed\xa0\xbd\xed\xb8\x80', b'\xed\xb0\x80']
    jobs = []
    for _ in range(count):
        data = b''.join(rng.choice(pieces) if rng.random() < 0.8 else rng.randbytes(rng.randint(1, 4)) for _ in range(rng.randint(0, 8)))
        for mode in ('strict', 'surrogatepass', 'surrogateescape'):
            jobs.append({'mode': mode, 'b': b64(data)})
    return jobs


def py_utf8(job):
    try:
        return {'s': base64.b64decode(job['b']).decode('utf-8', job['mode'])}
    except UnicodeDecodeError as error:
        return failure(error)


def gen_fsencode(rng, count):
    jobs = [{'s': apart(item)} for item in ['', 'a', 'a\x00b', '\x00', 'a\ud800b', 'a\udc80\ud800\udc80x', '\udc80\udc81', 'ab\udfff', '\ud800\ud801', '\udc00\ud800', 'a\udc7f', 'a\udd00', '\U0001f600\ud800', '\U0001f600\U0001f600\udbff\udbffz', '\udcff\x00', '\x00\ud800', '\ud800\x00']]
    for _ in range(count):
        text = ''.join(rng.choice(['a', '/', '.', '\x00', '\xe9', '\u4e2d', '\U0001f600', '\ud800', '\udbff', '\udc00', '\udc7f', '\udc80', '\udcff', '\udd00', '\udfff']) if rng.random() < 0.97 else chr(rng.randrange(0x110000)) for _ in range(rng.randint(0, 9)))
        jobs.append({'s': apart(text)})
    return jobs


def py_fsencode(job):
    text = job['s']
    try:
        os.lstat(text)
    except OSError:
        pass
    except ValueError as error:
        return failure(error, message=True)
    return {'b': b64(os.fsencode(text))}


def gen_pathpure(rng, count):
    pool = ['a', 'b', 'file.txt', '.hidden', '..', '.', '', 'x.tar.gz', 'name.', '...', 'a b', "it's", '"q"', 'back\\slash', 'ctl\x01', 'nl\n', 'tab\t', '\xe9', '\U0001f600', '\x7f', '\x80', '\u2028', '\ufeff', '\ud800', '\U000e0001', '\u0378', '\xad', '\x00']
    def one():
        if rng.random() < 0.15:
            return wild(rng, 0, 10, ascii_pool='ab./ \'"\\')
        text = rng.choice(['', '', '/', '/', '//', '///', './', '../']) + rng.choice(['/', '/', '/', '//', '/./']).join(rng.choice(pool) for _ in range(rng.randint(0, 5))) + rng.choice(['', '', '/', '//', '/.', '/..'])
        return apart(text)
    jobs = []
    for _ in range(count):
        first = one()
        second = one() if rng.random() < 0.6 else first[:rng.randint(0, len(first))]
        jobs.append({'a': first, 'b': apart(second), 'c': one()})
    return jobs


def py_pathpure(job):
    first, second, third = job['a'], job['b'], job['c']
    path = PurePosixPath(first)
    out = {'pathStr': str(path), 'pathParts': list(path.parts), 'joinPath': str(path / second), 'name': path.name, 'suffix': path.suffix, 'parent': str(path.parent),
           'isRelativeTo': path.is_relative_to(second), 'isAbsolute': path.is_absolute(), 'posixJoin': posixpath.join(first, second, third), 'repr': repr(first)}
    try:
        out['relativeTo'] = str(path.relative_to(second))
    except ValueError as error:
        out['relativeTo'] = failure(error, message=True)
    try:
        out['normpath'] = posixpath.normpath(first)
    except ValueError as error:
        out['normpath'] = failure(error)
    return out


def build_tree():
    """A scratch tree with every kind of link the engine's path checks have to judge."""
    remove_tree()
    tree = FS / 't'
    (tree / 'dir/sub').mkdir(parents=True)
    (tree / 'empty-dir').mkdir()
    (tree / 'file.txt').write_bytes(b'file\n')
    (tree / 'dir/inner.txt').write_bytes(b'inner\n')
    (tree / 'dir/sub/deep.txt').write_bytes(b'deep\n')
    (tree / ('n' * 255)).write_bytes(b'long\n')
    (tree / 'locked').mkdir()
    (tree / 'locked/secret.txt').write_bytes(b'secret\n')
    os.symlink('../file.txt', tree / 'locked/out')
    links = {'rel-file': 'file.txt', 'abs-file': str(tree / 'file.txt'), 'dangling': 'missing-target', 'dir-link': 'dir', 'abs-dir-link': str(tree / 'dir'),
             'chain1': 'chain2', 'chain2': 'chain3', 'chain3': 'dir/inner.txt', 'loop-a': 'loop-b', 'loop-b': 'loop-a', 'self': 'self', 'up-link': '..',
             'dir/up': '../file.txt', 'dotdot-link': 'dir/sub/../inner.txt', 'into-locked': 'locked/secret.txt', 'long-link': 'n' * 255, 'dir/to-loop': '../loop-a',
             'slash-link': '/', 'trail-link': 'dir/', 'dot-link': '.', 'dir/sub/back': '../..', 'empty-target-dir': 'empty-dir', 'through-loop': 'loop-a/x',
             'deep-dangling': 'dir/missing/x', 'via-file': 'file.txt/x', 'dir/abs-up': str(tree), 'too-long-link': 'm' * 300}
    for name, target in links.items():
        os.symlink(target, tree / name)
    os.chmod(tree / 'locked', 0)
    return tree


def remove_tree():
    if not FS.exists():
        return
    for directory, names, _files in os.walk(FS):
        for name in names:
            path = os.path.join(directory, name)
            if not os.path.islink(path):
                os.chmod(path, 0o700)
    for directory, names, files in os.walk(FS, topdown=False):
        for name in files + names:
            path = os.path.join(directory, name)
            if os.path.islink(path) or not os.path.isdir(path):
                os.unlink(path)
            else:
                os.rmdir(path)
    os.rmdir(FS)
    try:
        os.rmdir(FS.parent)
    except OSError:
        pass


def gen_pathfs(rng, count):
    tree = FS / 't'
    names = ['file.txt', 'dir', 'sub', 'inner.txt', 'deep.txt', 'empty-dir', 'locked', 'secret.txt', 'out', 'rel-file', 'abs-file', 'dangling', 'dir-link', 'abs-dir-link', 'chain1', 'chain2', 'chain3',
             'loop-a', 'loop-b', 'self', 'up-link', 'up', 'dotdot-link', 'into-locked', 'long-link', 'to-loop', 'slash-link', 'trail-link', 'dot-link', 'back', 'empty-target-dir', 'through-loop',
             'deep-dangling', 'via-file', 'abs-up', 'too-long-link', 'n' * 255, 'm' * 300, 'missing', 'x', '..', '..', '.', '', 't']
    odd = ['nul\x00x', 'esc\udc80', 'sur\ud800', '\xe9', 'a b']
    jobs = []
    for index in range(count):
        parts = [rng.choice(names) if rng.random() < 0.96 else rng.choice(odd) for _ in range(rng.randint(1, 5))]
        relative = rng.choice(['/', '/', '/', '//']).join(parts) + rng.choice(['', '', '', '/', '/.'])
        roll = rng.random()
        if roll < 0.55:
            jobs.append({'p': str(tree) + '/' + relative, 'cwd': str(tree)})
        elif roll < 0.60:
            jobs.append({'p': '/' + str(tree) + '/' + relative, 'cwd': str(tree)})
        else:
            jobs.append({'p': relative or '.', 'cwd': str(rng.choice([tree, tree / 'dir', tree / 'dir/sub', tree / 'empty-dir']))})
    return jobs


def py_pathfs(job):
    def attempt(call):
        try:
            return call()
        except ValueError as error:
            return failure(error, message=True)
        except (OSError, RuntimeError) as error:
            return failure(error)
    text = job['p']
    before = os.getcwd()
    os.chdir(job['cwd'])
    try:
        path = Path(text)
        return {'resolve': attempt(lambda: str(path.resolve())), 'realpath': attempt(lambda: os.path.realpath(text)), 'isFile': attempt(path.is_file), 'isSymlink': attempt(path.is_symlink),
                'lstat': attempt(lambda: stat.S_ISREG(path.lstat().st_mode)), 'readBytes': attempt(lambda: hashlib.sha256(path.read_bytes()).hexdigest()), 'abspath': attempt(lambda: os.path.abspath(text))}
    finally:
        os.chdir(before)


def gen_readtext(rng, count):
    directory = FS / 'text'
    directory.mkdir(parents=True, exist_ok=True)
    pieces = [b'a', b'line', b'\n', b'\r', b'\r\n', b'\n\r', b'\r\r\n', b'\xef\xbb\xbf', b'\xc3\xa9', b'\xf0\x9f\x98\x80', b'\x0b', b'\x0c', b'\x1c', b'\xc2\x85', b'\xe2\x80\xa8', b'\x00', b'\xff', b'\xed\xa0\x80', b'\xc0']
    jobs = []
    for index in range(count):
        data = b''.join(rng.choice(pieces[:16] if rng.random() < 0.9 else pieces) for _ in range(rng.randint(0, 10)))
        path = directory / ('f%d' % index)
        path.write_bytes(data)
        jobs.append({'p': str(path)})
    return jobs


def py_readtext(job):
    try:
        return {'s': Path(job['p']).read_text()}
    except UnicodeDecodeError as error:
        return failure(error)


def gen_cook_code(rng, count):
    known = sorted(engine_errors.COOK_CODES)
    unknown = ['E_UNKNOWN', 'E_DEPLOY_FAILED', 'E_', 'E_HOST', 'E_HOST_LAYOUTS', 'E_HOST_UNIT_', 'E__HOST_UNIT', 'e_host_unit', 'E_HOST_layout']
    glue = [b'', b' ', b'\n', b':', b'x', b'X', b'9', b'_', b'-', b'.', b'\xff', b'\x00', b'\xc3\xa9', b'E', b'E_', b'"', b'(']
    jobs = []
    for _ in range(count):
        out = []
        for _ in range(rng.randint(0, 5)):
            roll = rng.random()
            code = rng.choice(known) if roll < 0.5 else rng.choice(unknown) if roll < 0.8 else None
            out.append(rng.choice(glue) + (code.encode() if code else rng.randbytes(rng.randint(0, 6))) + rng.choice(glue))
        jobs.append({'b': b64(b''.join(out))})
    return jobs


def py_cook_code(job):
    return engine_errors.cook_code(base64.b64decode(job['b']))


def stream_bytes(job):
    data = bytearray(job['fill'].encode('latin-1') * job['size'])
    plant = job['plant'].encode('latin-1')
    data[job['at']:job['at'] + len(plant)] = plant
    return bytes(data[:job['size']]) if job.get('clip', True) else bytes(data)


def gen_cook_stream(rng, count):
    jobs = []
    chunk = 65536
    codes = ['E_HOST_UNIT', 'E_HOST_DEPENDENCY_POLICY_FIELDS', 'E_PRIVATE_LINK_OWNER']
    for boundary, size in ((chunk, chunk + 200), (chunk, 2 * chunk + 200), (2 * chunk, 2 * chunk + 200), (2 * chunk, 3 * chunk)):
        for offset in range(-140, 11):
            for code, before, after, fill in ((codes[0], '', '', ' '), (codes[1], '', '', '.'), (codes[0], 'A', '', ' '), (codes[0], '', '_', ' '), (codes[2], '9', ' ', 'E'), (codes[0], ' ', ' ', '_')):
                at = boundary + offset
                if at < 0:
                    continue
                jobs.append({'size': size, 'fill': fill, 'plant': before + code + after, 'at': at})
    for size in (0, 1, 127, 128, 129, chunk - 1, chunk, chunk + 1, 2 * chunk):
        for at in (0, max(0, size - 11), max(0, size - 5)):
            jobs.append({'size': size, 'fill': ' ', 'plant': 'E_HOST_UNIT', 'at': at})
            jobs.append({'size': size, 'fill': ' ', 'plant': 'E_HOST_UNIT', 'at': at, 'clip': False})
    for _ in range(count):
        size = rng.choice([chunk - 3, chunk, chunk + 5, 2 * chunk, 2 * chunk + 77])
        jobs.append({'size': size, 'fill': rng.choice([' ', 'E', '_', 'a']), 'plant': rng.choice(['x', '', ' ']) + rng.choice(codes) + rng.choice(['', 'S', ' ']), 'at': rng.randrange(0, size)})
    return jobs


def py_cook_stream(job):
    import io
    return engine_errors.cook_code_from_stream(io.BytesIO(stream_bytes(job)))


def model_value(rng, depth=0):
    roll = rng.random()
    if depth > 2 or roll < 0.6:
        return rng.choice([None, True, False, 0, 1, -1, 2, 2 ** 53, 2 ** 53 + 1, 10 ** 30, -(10 ** 30), 0.0, -0.0, 1.0, 2.0, 0.5, 1e30, 9007199254740992.0, 9007199254740994.0, float('inf'), float('-inf'), float('nan'), '', 'a', '1', 'True',
                           'E_COOK_FAILED', 'E_HOST_UNIT', 'E_LOCK_INVALID', 'E_UNKNOWN', '\xe9', [], {}])
    if roll < 0.8:
        return [model_value(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return {rng.choice(['a', 'b', 'c', '1', 'error_code', 'msg']): model_value(rng, depth + 1) for _ in range(rng.randint(0, 3))}


def rebuilt(value):
    """A structurally equal value that shares no object with the original (two NaNs are never one object)."""
    if isinstance(value, float):
        return float(repr(value))
    if isinstance(value, list):
        return [rebuilt(item) for item in value]
    if isinstance(value, dict):
        return {key: rebuilt(item) for key, item in value.items()}
    return value


def gen_eq(rng, count):
    jobs = []
    for _ in range(count):
        first = model_value(rng)
        roll = rng.random()
        if roll < 0.4:
            second = rebuilt(first)
            if isinstance(second, dict) and rng.random() < 0.5:
                second = dict(reversed(list(second.items())))
        elif roll < 0.6 and isinstance(first, (bool, int, float)) and first == first and abs(first) != float('inf'):
            second = rng.choice([bool, int, float])(first) if abs(first) < 2 ** 60 else int(first)
        else:
            second = model_value(rng)
        jobs.append({'a': tag(first), 'b': tag(second), '_a': first, '_b': second})
    return jobs


def py_eq(job):
    return job['_a'] == job['_b']


def gen_pyops(rng, count):
    jobs = []
    for _ in range(count):
        value = model_value(rng)
        key = rng.choice(['a', 'b', 'error_code', '1', '', 'git', 'E_HOST_UNIT'])
        jobs.append({'v': tag(value), 'key': key, '_v': value})
    return jobs


def py_pyops(job):
    value, key = job['_v'], job['key']
    def attempt(call):
        try:
            return call()
        except (TypeError, KeyError) as error:
            return failure(error)
    return {'truthy': bool(value), 'typeName': type(value).__name__, 'strIn': attempt(lambda: key in value), 'inStrSet': attempt(lambda: value in {'a', '1', 'git', 'E_HOST_UNIT', ''}),
            'subscript': attempt(lambda: tag(value[key])), 'get': tag(value.get(key)) if isinstance(value, dict) else None, 'isInt': type(value) is int}


def gen_diagnostic(rng, count):
    codes = sorted(engine_errors.MESSAGES) + sorted(engine_errors.COOK_CODES)[:6] + ['E_UNKNOWN', '', 'e_cook_failed']
    jobs = []
    for _ in range(count):
        code = rng.choice(codes) if rng.random() < 0.8 else model_value(rng)
        upstream = rng.choice([None, None, rng.choice(codes)]) if rng.random() < 0.8 else model_value(rng)
        jobs.append({'code': tag(code), 'upstream': tag(upstream), '_code': code, '_upstream': upstream})
    return jobs


def pairs(mapping):
    return [[key, value] for key, value in mapping.items()]


def py_diagnostic(job):
    return pairs(engine_errors.diagnostic(job['_code'], job['_upstream']))


def gen_adapter(rng, count):
    codes = sorted(engine_errors.MESSAGES) + sorted(engine_errors.COOK_CODES)[:6] + ['E_UNKNOWN']
    jobs = []
    for _ in range(count):
        safe = engine_errors.diagnostic(rng.choice(codes), rng.choice([None, None, 'E_HOST_UNIT', 'E_HOST_LAYOUT', 'E_UNKNOWN']))
        roll = rng.random()
        if roll < 0.25:
            text = json.dumps(safe)
        elif roll < 0.35:
            items = list(safe.items())
            rng.shuffle(items)
            text = json.dumps(dict(items), separators=rng.choice([(',', ':'), (', ', ': ')]))
        elif roll < 0.45:
            text = json.dumps(dict(safe, extra=rng.choice([1, None, 'x'])))
        elif roll < 0.55:
            text = json.dumps(dict(safe, msg=safe['msg'] + rng.choice([' ', 'x', '\n'])))
        elif roll < 0.62:
            text = json.dumps(safe)[:-1] + ',"error_code":' + json.dumps(rng.choice([safe['error_code'], 'E_UNKNOWN', 1, None])) + '}'
        elif roll < 0.68:
            text = '{"error_code":"E_UNKNOWN",' + json.dumps(safe)[1:]
        elif roll < 0.74:
            text = json.dumps(dict(safe, cook_error_code=rng.choice([None, 'E_HOST_UNIT', 'E_UNKNOWN', 1, ['E_HOST_UNIT'], float('nan')])))
        elif roll < 0.80:
            text = json.dumps(rng.choice([[safe], safe['msg'], 1, None, True, [], {}, {'error_code': ['E_COOK_FAILED']}, {'error_code': {'a': 1}}, {'error_code': 1.0, 'msg': float('inf')}]))
        elif roll < 0.86:
            text = corrupt(rng, json.dumps(safe))
        elif roll < 0.90:
            text = rng.choice(['', ' ', 'E_COOK_FAILED', '{"error_code":"E_COOK_FAILED","msg":NaN}', '\ufeff' + json.dumps(safe), '[' * 9999, '[' * 20000 + ']' * 20000, json.dumps(safe) + '\n', '\n' + json.dumps(safe)])
        else:
            text = json.dumps(dict(safe, msg=safe['msg'].replace('C', '\\u0043', 1)), ensure_ascii=False).replace('\\\\u0043', '\\u0043')
        if rng.random() < 0.4:
            encoding = rng.choice(['utf-8', 'utf-8', 'utf-8-sig', 'utf-16', 'utf-16-le', 'utf-32-be'])
            data = text.encode(encoding)
            if rng.random() < 0.15 and data:
                data = data[:-1] if rng.random() < 0.5 else data + b'\xff'
            jobs.append({'b': b64(data)})
        else:
            jobs.append({'s': text})
    return jobs


def py_adapter(job):
    return pairs(engine_errors.adapter_diagnostic(job['s'] if 's' in job else base64.b64decode(job['b'])))


# ---------------------------------------------------------------- code point sweeps

def sweep_names_only_key(text):
    try:
        engine_lock.names_only({text: 1})
    except engine_lock.LockError as error:
        return str(error)
    return None


def sweep_repr(text):
    out = repr(text)
    code = ord(text)
    for name, shape in (('x', "'\\x%02x'"), ('u', "'\\u%04x'"), ('U', "'\\U%08x'")):
        if out == shape % code:
            return name
    return 'raw' if out == "'" + text + "'" else out


def sweep_hostname(text):
    host = urllib.parse.SplitResult('', text, '', '', '').hostname
    return 'same' if host == text else host


def sweep_netloc(text):
    try:
        parts = urllib.parse.urlsplit(text)
    except ValueError:
        return 'ValueError'
    return 'kept' if parts.netloc == text[2:] else 'cut'


SWEEPS = {'repository': engine_lock.repository, 'lowered_host': sweep_hostname, 'netloc': sweep_netloc, 'credential_shaped': engine_lock.credential_shaped, 'text': engine_lock.text, 'external_name': engine_lock.external_name, 'version': engine_lock.version,
          'names_only_key': sweep_names_only_key, 'repr': sweep_repr, 'isspace': str.isspace}


def run_sweep(job):
    call = SWEEPS[job['fn']]
    prefix, suffix = job['prefix'], job['suffix']
    runs, last = [], None
    for low, high, step in job['domain']:
        for code in range(low, high, step):
            outcome = call(prefix + chr(code) + suffix)
            if outcome != last or not runs:
                runs.append([code, outcome])
                last = outcome
    return runs


def sweep_templates():
    """(function, prefix, suffix): the swept code point stands where one character class or one letter decides."""
    fill = 'a' * 11
    digits = '1234' * 2
    long = 'b' * 30
    begin = '-' * 5 + 'BEGIN '
    private = 'PRIVATE ' + 'KEY' + '-' * 5
    jwt = 'ey' + 'Ja.b.c'
    out = [('credential_shaped', 's' + 'k-' + fill, ''), ('credential_shaped', 's' + 'k-', fill + 'a'), ('credential_shaped', 'gh', '_' + fill + 'a'), ('credential_shaped', 'glpat', fill + 'a'),
           ('credential_shaped', '', 'hv' + 's.x'), ('credential_shaped', 'hv' + 's.', ''), ('credential_shaped', '', digits + ':' + long), ('credential_shaped', digits, ':' + long),
           ('credential_shaped', begin, private), ('credential_shaped', '', jwt), ('credential_shaped', 'ey' + 'J', '.b.c'), ('credential_shaped', 'ey' + 'Ja.', '.c'), ('credential_shaped', 'ey' + 'Ja.b.', ''),
           ('credential_shaped', '', 'TOKEN=x'), ('credential_shaped', '-', 'TOKEN=x'), ('credential_shaped', 'TOKEN', '=x'), ('credential_shaped', 'TOKEN=', 'x'), ('credential_shaped', 'TOKEN=', ''), ('credential_shaped', 'TOKEN', 'x'),
           ('credential_shaped', '://', ':' + AT), ('credential_shaped', '://:', AT), ('credential_shaped', '://a:b', ''), ('credential_shaped', '', 'token=x'), ('credential_shaped', '?token', ''),
           ('credential_shaped', 'vault/', 'x/y'), ('credential_shaped', 'vault/a', '/y'), ('credential_shaped', 'vault/ab/', ''), ('credential_shaped', 'vault', 'ab/c'), ('credential_shaped', '', 'vault' + '/ab/c')]
    # Each letter of the case-insensitive words, replaced by the swept code point.
    words = {'TOKEN=x': 'TOKEN', 'PASSWORD:x': 'PASSWORD', 'API_KEY=x': 'API_KEY', 'SECRET=x': 'SECRET', '?token=': 'token', '&password=': 'password', '?secret=': 'secret', '&api_key=': 'api_key',
             'vault' + '/ab/c': 'vault', 'secret' + '/ab/c': 'secret'}
    seen = set()
    for whole, word in words.items():
        start = whole.index(word)
        for index, letter in enumerate(word):
            key = (letter.lower(), whole[0] in '?&', whole.endswith('/c'))
            if key in seen:
                continue
            seen.add(key)
            out.append(('credential_shaped', whole[:start + index], whole[start + index + 1:]))
    out += [('text', 'a', 'b'), ('external_name', 'a', 'b'), ('external_name', '', 'b'), ('version', '1.2.3-', ''), ('version', '1.2.', ''), ('version', '', '1.2.3'), ('isspace', '', ''), ('repr', '', '')]
    covered = set()
    for word in ('value', 'values', 'secret', 'token', 'password', 'apikey', 'authtoken'):
        for index, letter in enumerate(word):
            if letter in covered:
                continue
            covered.add(letter)
            out.append(('names_only_key', word[:index], word[index + 1:]))
    out += [('names_only_key', 'va', 'lue'), ('names_only_key', '', 'value'), ('names_only_key', 'value', ''), ('names_only_key', 'plain', 'name')]
    out += [('netloc', '//a', 'b'), ('netloc', '//', ''), ('lowered_host', '', 'x'), ('lowered_host', 'x', ''), ('lowered_host', 'A', 'B')]
    remote = 'ssh://example.invalid'
    out += [('repository', 'g', 't:x'), ('repository', '', ':x'), ('repository', 'h:', ''), ('repository', 'u', AT + 'h:x'), ('repository', '/x', ''), ('repository', 'ssh://', AT + 'example.invalid/x'),
            ('repository', 'ssh://example.inval', 'd/x'), ('repository', remote + ':2', '/x'), ('repository', remote + '/', ''), ('repository', '', remote + '/x'), ('repository', 'ssh://[', ']/x')]
    unique = []
    for item in out:
        if item not in unique:
            unique.append(item)
    return unique


def assigned_ranges():
    """Code points the oracle interpreter's Unicode version assigns, as sweep domains."""
    import unicodedata
    ranges = []
    for code in range(0x110000):
        if unicodedata.category(chr(code)) != 'Cn':
            if ranges and ranges[-1][1] == code:
                ranges[-1][1] = code + 1
            else:
                ranges.append([code, code + 1, 1])
    return ranges


def gen_sweep(rng, count, full):
    domain = [[0, 0x110000, 1]] if full else [[0, 0x800, 1], [0x800, 0x110000, 251], [0xd7f0, 0xe010, 1], [0x2000, 0x2100, 1], [0xff00, 0x10010, 1]]
    # `str.lower()` is compared only where CPython knows the character: the JavaScript runtime's case tables are
    # from a newer Unicode version and lowercase letters this interpreter has never heard of (url.ts says so).
    known = assigned_ranges()
    return [{'fn': fn, 'prefix': prefix, 'suffix': suffix, 'domain': known if fn == 'lowered_host' else domain} for fn, prefix, suffix in sweep_templates()]


# ---------------------------------------------------------------- tar and gzip

TAR_READ_LIMIT = 16 * 1024 * 1024  # members up to this size are read on both sides; archive.py reads at most 1 MiB
MANIFEST = b'{"name": "example", "version": "1.0.0"}\n'


def octal(value, width):
    return ('%0*o' % (width - 1, value)).encode() + b'\0'


def base256(value, width):
    if value >= 0:
        return b'\x80' + value.to_bytes(width - 1, 'big')
    return b'\xff' + (256 ** (width - 1) + value).to_bytes(width - 1, 'big')


def checksummed(block, signed=False):
    block = bytearray(block)
    block[148:156] = b' ' * 8
    total = sum(byte - 256 if signed and byte > 127 else byte for byte in block)
    block[148:156] = ('%06o' % (total % (8 ** 6))).encode() + b'\0 '
    return bytes(block)


def tar_header(name=b'file', size=0, kind=b'0', link=b'', prefix=b'', magic=b'ustar\x0000', signed=False, **raw):
    """One 512-byte header. `size` is an int (octal text) or the raw 12 bytes; `raw` replaces any field by name."""
    block = bytearray(512)
    block[0:len(name[:100])] = name[:100]
    fields = {'mode': (100, octal(0o644, 8)), 'uid': (108, octal(0, 8)), 'gid': (116, octal(0, 8)),
              'size': (124, size if isinstance(size, bytes) else octal(size, 12)), 'mtime': (136, octal(0, 12)),
              'devmajor': (329, octal(0, 8)), 'devminor': (337, octal(0, 8))}
    for key, (start, value) in fields.items():
        value = raw.get(key, value)
        block[start:start + len(value)] = value
    block[156:157] = kind
    block[157:157 + len(link[:100])] = link[:100]
    block[257:265] = magic
    block[265:265 + 4] = b'user'
    block[297:297 + 5] = b'group'
    block[345:345 + len(prefix[:155])] = prefix[:155]
    for start, value in raw.get('patch', ()):
        block[start:start + len(value)] = value
    return checksummed(block, signed) if 'chksum' not in raw else bytes(block[:148] + raw['chksum'] + block[156:])


def padded(data):
    return data + b'\0' * (-len(data) % 512)


def pax_record(key, value):
    body = b' ' + key + b'=' + value + b'\n'
    length = len(body) + 1
    while len(str(length)) + len(body) != length:
        length = len(str(length)) + len(body)
    return str(length).encode() + body


def pax_block(records, kind=b'x', name=b'PaxHeader'):
    data = b''.join(records)
    return tar_header(name, len(data), kind) + padded(data)


def sparse_structs(pairs, count):
    out = b''
    for index in range(count):
        offset, numbytes = pairs[index] if index < len(pairs) else (0, 0)
        out += octal(offset, 12) + octal(numbytes, 12)
    return out


NAMES = [b'package/package.json', b'package/index.js', b'pkg/package.json', b'package.json', b'a/b/package.json', b'package/package_json', b'x/packageXjson', b'package/lib/', b'dir/', b'dir///',
         b'caf\xc3\xa9/package.json', b'bad\xe9name/package.json', b'\xed\xa0\x80/package.json', b'a' * 99, b'a' * 100, b'line\nbreak/package.json', b'', b'/', b'./package/package.json', b'p/package.json/']
LONG_NAMES = [b'd/' * 60 + b'package.json', b'x' * 150, b'long/' + b'y' * 300, b'package/' + b'z' * 120 + b'/package.json', b'n' * 255 + b'/', b'deep/' * 70]
PAX_VALUES = [b'0', b'1', b'5', b'512', b'513', b'1048576', b'1048577', b'-1', b'-5', b'+7', b' 7 ', b'1_0', b'7x', b'', b'0x10', b'\xd9\xa1\xd9\xa2', b'1e3', b'1.5', b'9' * 30, b'9' * 4301,
              b'68719476736', b'9223372036854775807', b'9223372036854775808', b'\xff', b'1\x00']


def tar_item(rng):
    """One archive member, well-formed or not, as bytes."""
    name = rng.choice(NAMES)
    data = rng.choice([MANIFEST, b'', b'x' * 511, b'y' * 512, b'z' * 513, b'[1, 2]', b'{"a": NaN}', b'not json', bytes(rng.randrange(256) for _ in range(rng.randrange(0, 1500)))])
    roll = rng.randrange(34)
    if roll < 6:
        return tar_header(name, len(data), rng.choice([b'0', b'0', b'\0', b'7'])) + padded(data)
    if roll == 6:   # a directory, a link, a device, a fifo
        return tar_header(name, rng.choice([0, 0, len(data)]), rng.choice([b'5', b'1', b'2', b'3', b'4', b'6']), link=rng.choice(NAMES)) + rng.choice([b'', padded(data)])
    if roll == 7:   # unknown type bytes are read as regular files
        return tar_header(name, len(data), bytes([rng.choice([0x38, 0x39, 0x41, 0x5a, 0x61, 0x7f, 0x80, 0xff, 0x20, 0x44, 0x4d, 0x4e, 0x56])])) + padded(data)
    if roll == 8:   # ustar prefix
        return tar_header(rng.choice([b'package.json', b'f', b'd/']), len(data), rng.choice([b'0', b'5', b'\0', b'L']), prefix=rng.choice([b'package', b'a/b', b'p' * 155, b'/', b'caf\xe9'])) + padded(data)
    if roll == 9:   # GNU long name, and long link
        long_name = rng.choice(LONG_NAMES + NAMES) + rng.choice([b'', b'\0', b'\0junk'])
        kind = rng.choice([b'L', b'L', b'L', b'K'])
        return tar_header(b'././@LongLink', len(long_name), kind, magic=b'ustar  \0') + padded(long_name) + tar_header(name, len(data), rng.choice([b'0', b'5', b'\0', b'x'])) + padded(data)
    if roll == 10:  # long name whose size disagrees with its data
        long_name = rng.choice(LONG_NAMES)
        return tar_header(b'././@LongLink', rng.choice([0, 1, len(long_name) + 600, 2 ** 20]), b'L') + padded(long_name) + tar_header(name, len(data)) + padded(data)
    if roll in (11, 12, 13, 14):  # pax extended header
        records = []
        for _ in range(rng.randrange(0, 4)):
            key = rng.choice([b'path', b'path', b'size', b'size', b'linkpath', b'mtime', b'atime', b'uid', b'gid', b'uname', b'hdrcharset', b'comment', b'GNU.sparse.name', b'GNU.sparse.realsize',
                              b'GNU.sparse.size', b'GNU.sparse.map', b'GNU.sparse.major', b'GNU.sparse.minor', b'GNU.sparse.offset', b'GNU.sparse.numbytes', b'GNU.sparse.numblocks', b'caf\xe9', b'k\xc3\xa9y'])
            if key in (b'path', b'linkpath', b'GNU.sparse.name', b'uname'):
                value = rng.choice(NAMES + LONG_NAMES)
            elif key == b'hdrcharset':
                value = rng.choice([b'BINARY', b'ISO-IR 10646 2000 UTF-8', b'x'])
            elif key == b'GNU.sparse.map':
                value = rng.choice([b'0,5', b'0,5,10,5', b'1,2,3', b'', b'x', b'0,-1', b'5,5,0,5', b'0,1048576', b'0,5,', b' 0 , 5 '])
            elif key in (b'GNU.sparse.major', b'GNU.sparse.minor'):
                value = rng.choice([b'1', b'0', b'1', b'0', b'2', b''])
            else:
                value = rng.choice(PAX_VALUES)
            records.append(pax_record(key, value))
        if records and rng.random() < 0.25:
            # the keyword of an earlier record once more, behind the others: its place in the dict is the first one, its value the last
            first = records[0].split(b' ', 1)[1].split(b'=', 1)[0]
            records.append(pax_record(first, rng.choice(NAMES + PAX_VALUES)))
        elif rng.random() < 0.15:
            records.append(rng.choice([b'5 x=\n', b'5 =x\n', b'4 =\n', b'6 xy\n\n', b'0 x=y\n', b'99 path=x\n', b'12 path=abc', b'012 path=ab\n', b'x path=y\n', b'9 =path\n', b'00000000000000000013 path=ab\n',
                                       b'000000000000000000013 path=ab\n', b'13path=abcd\n', b'6 a=b\n\0\0', b'18446744073709551616 a=b\n']))
        if rng.random() < 0.8:
            rng.shuffle(records)
        kind = rng.choice([b'x', b'x', b'x', b'g', b'X'])
        follow = rng.choice([b'0', b'0', b'0', b'5', b'\0', b'S', b'2', b'Z'])
        sparse_data = rng.choice([b'', b'1\n0\n5\n', b'2\n0\n5\n10\n5\n', b'1\n0\n', b'x\n', b'-1\n', b'3\n1\n2\n3\n4\n5\n6\n', b'1\n' + b'9' * 600 + b'\n5\n', b'no newline'])
        body = sparse_data + data if rng.random() < 0.3 else data
        declared = rng.choice([len(body), len(body), len(body), 0, len(body) + 1024])
        return pax_block(records, kind) + tar_header(name, declared, follow) + padded(body)
    if roll == 15:  # old GNU sparse member, with and without extension blocks
        pairs = [(rng.choice([0, 0, 5, 512, 2 ** 20]), rng.choice([0, 5, 512, len(data)])) for _ in range(rng.randrange(0, 6))]
        extension = rng.choice([0, 0, 1, 1])
        origsize = rng.choice([len(data), 0, 100, 2000, 2 ** 20, 2 ** 21])
        patch = [(386, sparse_structs(pairs, 4)), (482, bytes([extension])), (483, octal(origsize, 12))]
        blocks = b''
        if extension:
            more = bytearray(sparse_structs(pairs[4:], 21)) + bytearray(8)
            more[504] = rng.choice([0, 0, 0, 1])
            blocks = bytes(more) if rng.random() < 0.8 else bytes(more[:rng.choice([0, 100, 504, 505])])
        # GNU headers keep times where ustar keeps a prefix; tarfile must not read one as the other
        return tar_header(name, len(data), b'S', magic=b'ustar  \0', prefix=rng.choice([b'', b'', b'12345670123\0', b'package']), patch=patch) + blocks + padded(data)
    if roll == 16:  # sizes in base 256, negative sizes, sizes beyond the data
        size = rng.choice([base256(len(data), 12), base256(-1, 12), base256(-512, 12), base256(2 ** 34, 12), base256(2 ** 62, 12), base256(2 ** 63, 12), base256(2 ** 70, 12), b'-0000000001\0',
                           octal(len(data) + 512, 12), octal(len(data) + 5000, 12), octal(2 ** 33 - 1, 12), b'       \0    ', b'0o17\0       ', b'1_0\0        ', b'+7\0         ', b' 7 \0        ',
                           b'\x80' + b'\0' * 11, b'\xff' * 12, b'8\0          ', b'12\xe9\0        '])
        return tar_header(name, size, rng.choice([b'0', b'0', b'L', b'x', b'5', b'S'])) + padded(data)
    if roll == 17:  # other number fields: valid oddities and garbage
        field = rng.choice(['mode', 'uid', 'gid', 'mtime', 'devmajor', 'devminor'])
        width = 12 if field == 'mtime' else 8
        value = rng.choice([base256(7, width), base256(-7, width), b' ' * width, b'\0' * width, b'9' * width, b'-7\0'.ljust(width, b' '), b'0o7\0'.ljust(width, b'\0'), b'7 7'.ljust(width, b'\0'),
                            b'\xe9'.ljust(width, b'\0'), b'\x1c7\x1c\0'.ljust(width, b'\0')])
        return tar_header(name, len(data), **{field: value}) + padded(data)
    if roll == 18:  # checksum variants
        choice = rng.randrange(5)
        high = rng.choice([b'caf\xe9/package.json', b'\xff\xfe/package.json'])
        if choice == 0:
            return tar_header(high, len(data), signed=True) + padded(data)
        if choice == 1:
            return tar_header(high, len(data)) + padded(data)
        block = bytearray(tar_header(name, len(data)))
        if choice == 2:
            block[148:156] = b'0' * 8
        elif choice == 3:
            total = int(bytes(block[148:154]), 8)
            block[148:156] = base256(total, 8)
        else:
            block[148:156] = bytes(block[148:154]).lstrip(b'0').rjust(6, b' ') + b'\0 '
        return bytes(block) + padded(data)
    if roll == 19:  # v7 header without magic; trailing slash on an old-style regular type
        return tar_header(rng.choice([b'dir/', b'package/package.json/', name]), len(data), b'\0', magic=b'\0' * 8) + padded(data)
    if roll == 20:  # pax header chains and a pax header before a long name
        first = pax_block([pax_record(b'path', b'first/package.json')])
        second = pax_block([pax_record(b'path', b'second/package.json'), pax_record(b'size', str(len(data)).encode())], rng.choice([b'x', b'g']))
        long_name = b'third/' + b'q' * 150 + b'/package.json'
        gnu = tar_header(b'././@LongLink', len(long_name) + 1, b'L') + padded(long_name + b'\0')
        parts = rng.sample([first, second, gnu], rng.randrange(1, 4))
        return b''.join(parts) + tar_header(name, len(data)) + padded(data)
    if roll == 21:  # a header-like block that is only data of the previous member is never seen; a member without its data is
        return tar_header(name, len(data))
    if roll == 22:  # GNU sparse 1.0 in full
        table = b'2\n0\n3\n10\n4\n'
        body = padded(table) + data
        records = [pax_record(b'GNU.sparse.major', b'1'), pax_record(b'GNU.sparse.minor', b'0'), pax_record(b'GNU.sparse.name', name), pax_record(b'GNU.sparse.realsize', rng.choice([b'14', b'20', b'0', b'7', b'x']))]
        if rng.random() < 0.5:
            records.append(pax_record(b'size', str(len(body)).encode()))
        return pax_block(records) + tar_header(b'GNUSparseFile.0/' + name[:60], len(body)) + padded(body)
    if roll == 23:  # GNU sparse 0.0 and 0.1
        if rng.random() < 0.5:
            records = [pax_record(b'GNU.sparse.size', rng.choice([b'20', b'0', b'x', b'-3'])), pax_record(b'GNU.sparse.numblocks', b'2'), pax_record(b'GNU.sparse.offset', rng.choice([b'0', b'\xff', b'x', b'1_0'])),
                       pax_record(b'GNU.sparse.numbytes', b'3'), pax_record(b'GNU.sparse.offset', b'10'), pax_record(b'GNU.sparse.numbytes', rng.choice([b'4', b'', b'-1']))]
        else:
            records = [pax_record(b'GNU.sparse.size', b'20'), pax_record(b'GNU.sparse.numblocks', b'2'), pax_record(b'GNU.sparse.map', rng.choice([b'0,3,10,4', b'0,3,10', b'5,3', b'0,30', b'3,0,0,3', b'19,5']))]
        if rng.random() < 0.3:
            records.append(pax_record(b'GNU.sparse.name', b'sparse/package.json'))
        return pax_block(records) + tar_header(name, len(data)) + padded(data)
    if roll == 24:  # global header that later members inherit
        records = [pax_record(rng.choice([b'path', b'size', b'GNU.sparse.realsize', b'GNU.sparse.name', b'comment', b'GNU.sparse.major']), rng.choice([b'global/package.json', b'5', b'1', b'0', b'x']))]
        return pax_block(records, b'g') + tar_header(name, len(data)) + padded(data) + tar_header(b'second', 3) + padded(b'abc')
    if roll == 25:  # several plain members, some with the same name
        return b''.join(tar_header(rng.choice([name, b'package/package.json']), len(MANIFEST)) + padded(MANIFEST) for _ in range(rng.randrange(2, 5)))
    if roll == 26:  # a pax or long-name header as the very last thing
        return rng.choice([pax_block([pax_record(b'path', b'p/package.json')]), tar_header(b'././@LongLink', 10, b'L') + padded(b'long/name\0'), tar_header(b'././@LongLink', 10, b'L')])
    if roll == 27:  # size override against the header size
        declared = rng.choice([0, len(data), len(data) + 512, 2048])
        override = rng.choice([0, len(data), len(data) + 512, 3, 2 ** 20, 2 ** 33])
        return pax_block([pax_record(b'size', str(override).encode())], rng.choice([b'x', b'x', b'g', b'X'])) + tar_header(name, declared, rng.choice([b'0', b'5', b'Y'])) + padded(data)
    if roll == 28:  # realsize far beyond the data, and a sparse map that asks for more than there is
        records = [pax_record(b'GNU.sparse.realsize', rng.choice([b'1048576', b'2097152', b'600', b'-5']))]
        if rng.random() < 0.5:
            records.append(pax_record(b'GNU.sparse.map', rng.choice([b'0,600', b'0,5,1000,5', b'100,5', b'0,5,2,5', b'-5,10'])))
        return pax_block(records) + tar_header(name, len(data)) + padded(data)
    if roll == 29:  # a zero block in the middle, then more
        return b'\0' * 512 + tar_header(name, len(data)) + padded(data)
    if roll == 30:  # number fields cut short by a NUL, or filling the field without one
        return tar_header(name, octal(len(data), 12)[:-1] + b' ', mode=b'0000644 ', mtime=b'1' * 12) + padded(data)
    if roll == 31:  # sparse extension that runs into the end of the archive
        patch = [(386, sparse_structs([(0, 5)], 4)), (482, b'\1'), (483, octal(5, 12))]
        return tar_header(name, 5, b'S', patch=patch) + rng.choice([b'', b'\0' * 100, b'\0' * 505, sparse_structs([], 21) + b'\1' + b'\0' * 7])
    if roll == 32:  # a long link target, and a hard link to an earlier name
        return tar_header(b'target', len(data)) + padded(data) + tar_header(b'alias', 0, rng.choice([b'1', b'2']), link=b'target')
    return tar_header(name, len(data), kind=rng.choice([b'x', b'g', b'X', b'L', b'K'])) + padded(data)


def module_archive(rng):
    """An archive the tarfile module writes itself, in one of its three formats."""
    import io, tarfile
    buffer = io.BytesIO()
    fmt = rng.choice([tarfile.USTAR_FORMAT, tarfile.GNU_FORMAT, tarfile.PAX_FORMAT])
    extra = {'comment': 'global', 'path': 'forced/package.json'} if fmt == tarfile.PAX_FORMAT and rng.random() < 0.1 else None
    names = ['package/package.json', 'package/index.js', 'package/lib/', 'paquete/\u00f1and\u00fa.json', 'bad\udce9name/package.json', 'd/' * 60 + 'f', 'a' * 150, 'x' * 300, 'p/' + 'q' * 101 + '/package.json',
             'package/\U0001f600.txt', 'dir', 'package/package.json']
    with tarfile.open(fileobj=buffer, mode='w', format=fmt, pax_headers=extra, errors='surrogateescape') as archive:
        for _ in range(rng.randrange(0, 6)):
            info = tarfile.TarInfo(rng.choice(names))
            info.type = rng.choice([tarfile.REGTYPE] * 5 + [tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.CONTTYPE, tarfile.AREGTYPE])
            data = rng.choice([MANIFEST, b'', b'data' * 400]) if info.type in (tarfile.REGTYPE, tarfile.CONTTYPE, tarfile.AREGTYPE) else b''
            info.size = len(data)
            info.linkname = rng.choice(['package/package.json', 'l' * 150, '']) if info.type in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ''
            if fmt == tarfile.PAX_FORMAT and rng.random() < 0.3:
                info.pax_headers = {rng.choice(['comment', 'path', 'size', 'mtime', 'uid']): rng.choice(['1', 'other/package.json', '1.5', 'x'])}
            if rng.random() < 0.2:
                info.size, info.uid = (2 ** 33 + 5, 2 ** 22) if fmt != tarfile.USTAR_FORMAT else (info.size, 0)
            try:
                archive.addfile(info, __import__('io').BytesIO(data.ljust(min(info.size, 4096), b'\0')) if info.size else None)
            except (ValueError, OSError):
                continue  # the format cannot express this member; the next one may fit
    return buffer.getvalue()


def tar_mutation(rng, tar):
    roll = rng.randrange(12)
    data = bytearray(tar)
    headers = [index for index in range(0, len(data), 512) if data[index + 257:index + 262] == b'ustar']
    if roll == 0 and headers:   # flip a header byte and leave the checksum stale
        at = rng.choice(headers) + rng.randrange(512)
        data[at] ^= 1 << rng.randrange(8)
    elif roll in (1, 2) and headers:  # change a header byte and repair the checksum
        start = rng.choice(headers)
        at = rng.choice([rng.randrange(100), rng.randrange(100, 157), 156, 156, rng.randrange(124, 136), rng.randrange(345, 500), rng.randrange(157, 345), rng.randrange(386, 512)])
        data[start + at] = rng.choice([data[start + at] ^ (1 << rng.randrange(8)), 0, 0x20, 0x2f, 0x30, 0x37, 0x38, 0x80, 0xff, rng.randrange(256)])
        data[start:start + 512] = checksummed(data[start:start + 512], rng.random() < 0.1)
    elif roll == 3:             # truncate: at a block boundary, inside a block, to almost nothing
        data = data[:rng.choice([0, 1, 511, 512, 513, 1024, max(0, len(data) - 512), max(0, len(data) - 1), rng.randrange(len(data) + 1), (rng.randrange(len(data) + 1) // 512) * 512])]
    elif roll == 4:             # a zero block somewhere, or one removed
        at = rng.randrange(0, len(data) // 512 + 1) * 512
        if rng.random() < 0.5:
            data[at:at] = b'\0' * 512
        else:
            del data[at:at + 512]
    elif roll == 5:             # something after the end marker
        data += rng.choice([b'junk', tar_header(b'after/package.json', len(MANIFEST)) + padded(MANIFEST), b'\0' * 9000, b'\xff' * 512])
    elif roll == 6 and headers:  # a pax record or a data byte damaged in place
        start = rng.choice(headers) + 512
        if start < len(data):
            at = start + rng.randrange(min(64, len(data) - start))
            data[at] = rng.choice([0, 0x0a, 0x20, 0x3d, 0x30, 0x39, data[at] ^ 0x01, 0xff])
    elif roll == 7:             # the same archive twice
        data = data + data
    return bytes(data)


def gzip_valid(rng, tar):
    """A valid gzip container around the stream: one member or several, header flags, zero padding."""
    import gzip, zlib
    def member(chunk):
        roll = rng.randrange(8)
        if roll < 5:
            return gzip.compress(chunk, rng.choice([0, 1, 6, 9]), mtime=0)
        flags = rng.choice([4, 8, 16, 2, 4 | 8 | 16 | 2, 0x20, 0xe0, 8 | 2])
        head = b'\x1f\x8b\x08' + bytes([flags]) + b'\0\0\0\0' + rng.choice([b'\0', b'\2', b'\4']) + rng.choice([b'\3', b'\xff'])
        if flags & 4:
            extra = bytes(rng.randrange(256) for _ in range(rng.choice([0, 1, 20])))
            head += struct.pack('<H', len(extra)) + extra
        if flags & 8:
            head += rng.choice([b'name.tar\0', b'\0', b'caf\xe9\0'])
        if flags & 16:
            head += rng.choice([b'a comment\0', b'\0'])
        if flags & 2:
            head += rng.choice([b'\0\0', b'\x12\x34'])
        raw = zlib.compressobj(rng.choice([0, 6]), zlib.DEFLATED, -15)
        body = raw.compress(chunk) + raw.flush()
        return head + body + struct.pack('<II', zlib.crc32(chunk), len(chunk) & 0xffffffff)
    if rng.random() < 0.85 or not tar:
        return member(tar) + rng.choice([b'', b'', b'', b'\0', b'\0' * 700])
    cut = sorted(rng.randrange(len(tar) + 1) for _ in range(rng.randrange(1, 4)))
    pieces = [tar[a:b] for a, b in zip([0] + cut, cut + [len(tar)])]
    return b''.join(member(piece) + rng.choice([b'', b'', b'\0\0\0']) for piece in pieces)


def gen_tar(rng, count):
    jobs = []
    for _ in range(count):
        if rng.random() < 0.15:
            tar = module_archive(rng)
        else:
            tar = b''.join(tar_item(rng) for _ in range(rng.choice([1, 1, 2, 2, 3, 5])))
            tar += rng.choice([b'\0' * 1024, b'\0' * 1024, b'\0' * 1024, b'\0' * 512, b'', b'\0' * 10240])
        if rng.random() < 0.35:
            tar = tar_mutation(rng, tar)
        jobs.append({'b': b64(gzip_valid(rng, tar))})
    return jobs


def tar_listing(data, limit=TAR_READ_LIMIT):
    """tarfile.open(..., 'r:gz'), getmembers(), and extractfile().read() of what tarfile reads as a file."""
    import io, tarfile
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            members = []
            for member in archive.getmembers():
                entry = [member.name, member.isfile(), str(member.size), member.type[0]]
                if (member.isfile() or member.type not in tarfile.SUPPORTED_TYPES) and member.size <= limit:
                    try:
                        entry.append(hashlib.sha256(archive.extractfile(member).read()).hexdigest())
                    except Exception as error:
                        entry.append({'error': type(error).__name__})
                members.append(entry)
            return {'members': members}
    except Exception as error:
        return {'error': type(error).__name__}


def py_tar(job):
    return tar_listing(base64.b64decode(job['b']))


# Divergence D3: the TS reader refuses a long-name, long-link or pax header that states more than this many bytes.
METADATA_LIMIT = 2 ** 28
TAR_D3_BUCKETS = ('oracle refuses as unreadable, TS refuses', 'oracle lets an uncaught class escape, TS refuses', 'oracle lists the archive, TS refuses')


def reaches_metadata_limit(data):
    """Whether tarfile, listing this archive, comes to read the blocks of such a header.

    A second walk with a TarInfo that looks at the size on entry to the two
    methods that read those blocks. It is a walk of its own because the extra
    frame would move the recursion bounds the first one is compared on.
    """
    import io, tarfile
    seen = []

    class Probe(tarfile.TarInfo):
        def _proc_gnulong(self, archive):
            seen.append(self.size > METADATA_LIMIT)
            return super()._proc_gnulong(archive)

        def _proc_pax(self, archive):
            seen.append(self.size > METADATA_LIMIT)
            return super()._proc_pax(archive)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz', tarinfo=Probe) as archive:
            archive.getmembers()
    except Exception:
        pass
    return any(seen)


def judge_tar(job, want, got):
    """(agrees, D3 bucket or None). Equal answers, except where D3 applies: there TS must refuse with its OSError."""
    if not reaches_metadata_limit(base64.b64decode(job['b'])):
        return want == got, None
    if want == {'error': 'RecursionError'} and got == want:
        return True, None  # the frame bound came first on both sides
    import builtins, tarfile
    kind = want.get('error')
    # archive.py:138 turns these classes into its closed refusal; any other class escapes it.
    caught = kind is not None and issubclass(getattr(tarfile, kind, None) or getattr(builtins, kind), (OSError, ValueError, tarfile.TarError))
    bucket = TAR_D3_BUCKETS[2 if kind is None else 0 if caught else 1]
    return got == {'error': 'OSError', 'limit': 'metadata'}, bucket


# Divergence D4: the same listing under admission budgets. Small ones, so that archives of a few blocks meet them.
BUDGET_CHOICES = {'metadataBytes': [0, 5, 20, 100, 300, 512, 513, 1024, 2048, 10 ** 6, 10 ** 6], 'paxRecords': [0, 1, 2, 3, 5, 8, 10 ** 6, 10 ** 6],
                  'sparseNumbers': [0, 1, 2, 3, 4, 7, 8, 9, 10, 16, 50, 10 ** 6, 10 ** 6], 'members': [0, 1, 2, 3, 5, 10 ** 6, 10 ** 6]}


def budget_item(rng):
    """Members that count: sparse maps of every form, maps in global headers, runs of records, names and members."""
    data = rng.choice([MANIFEST, b'', b'x' * 600])
    roll = rng.randrange(9)
    numbers = lambda count: [rng.choice([b'0', b'5', b'512', b'7', b'7', b'7', b'x', b'', b'-1', b' 3 ']) if rng.random() < 0.04 else rng.choice([b'0', b'5', b'512', b'1048576']) for _ in range(count)]
    if roll == 0:   # a map in a global header and members that expand it again
        out = pax_block([pax_record(b'GNU.sparse.map', b','.join(numbers(rng.randrange(0, 7))))], b'g') + tar_header(b'package/first', len(data)) + padded(data)
        for _ in range(rng.randrange(0, 5)):
            out += (pax_block([pax_record(rng.choice([b'comment', b'path', b'GNU.sparse.map']), rng.choice([b'x', b'0,5', b'p/package.json']))]) if rng.random() < 0.8 else b'') + tar_header(rng.choice(NAMES), 0)
        return out
    if roll == 1:   # form 0.1
        return pax_block([pax_record(b'GNU.sparse.map', b','.join(numbers(rng.randrange(0, 20))))]) + tar_header(rng.choice(NAMES), len(data)) + padded(data)
    if roll == 2:   # form 0.0: offset and numbytes records in any order, with or without the size that selects the form
        records = [pax_record(rng.choice([b'GNU.sparse.offset', b'GNU.sparse.numbytes']), value) for value in numbers(rng.randrange(0, 14))]
        if rng.random() < 0.85:
            records.insert(rng.randrange(len(records) + 1), pax_record(b'GNU.sparse.size', rng.choice([b'0', b'5', b'x'])))
        return pax_block(records, rng.choice([b'x', b'x', b'g'])) + tar_header(rng.choice(NAMES), len(data)) + padded(data)
    if roll == 3:   # form 1.0: a count of regions, then numbers that may be fewer or more than it states, over one block or several
        lines = numbers(rng.randrange(0, 30))
        regions = rng.choice([len(lines) // 2, len(lines) // 2, len(lines), len(lines) // 2 + 1, 0, 10 ** 30, -1])
        body = b'%d\n' % regions + b''.join(line + b'\n' for line in lines) + rng.choice([b'', b' ' * 300, b'7' * 700 + b'\n7\n'])
        body = padded(body) + data
        return pax_block([pax_record(b'GNU.sparse.major', b'1'), pax_record(b'GNU.sparse.minor', b'0')], rng.choice([b'x', b'x', b'x', b'g'])) + tar_header(rng.choice(NAMES), len(body)) + padded(body)
    if roll == 4:   # old GNU: four structs in the header, more in extension blocks, zero ones among them
        pairs = lambda count: [(rng.choice([0, 5, 512]), rng.choice([0, 5, 5, 512])) for _ in range(count)]
        blocks = rng.randrange(0, 4)
        out = tar_header(rng.choice(NAMES), len(data), b'S', patch=[(386, sparse_structs(pairs(4), 4)), (482, bytes([blocks > 0])), (483, octal(len(data), 12))])
        for index in range(blocks):
            out += sparse_structs(pairs(21), 21) + bytes([index + 1 < blocks or rng.random() < 0.1]) + b'\0' * 7
        return out + padded(data)
    if roll == 5:   # a run of records
        return pax_block([pax_record(rng.choice([b'comment', b'x', b'path', b'size']), rng.choice([b'', b'1', b'v' * 40])) for _ in range(rng.randrange(0, 12))], rng.choice([b'x', b'g', b'X'])) + tar_header(rng.choice(NAMES), len(data)) + padded(data)
    if roll == 6:   # long names and long links of several sizes
        out = b''
        for _ in range(rng.randrange(1, 4)):
            name = rng.choice([b'n' * 5, b'n' * 20, b'n' * 100, b'd/' * 150, b'n' * 512, b'n' * 513, b'n' * 1024])
            out += tar_header(b'././@LongLink', len(name), rng.choice([b'L', b'L', b'K'])) + padded(name) + tar_header(b'short', 0)
        return out
    if roll == 7:   # plain members
        return b''.join(tar_header(rng.choice(NAMES), 0) for _ in range(rng.randrange(1, 8)))
    return tar_item(rng)


def gen_tarbudget(rng, count):
    jobs = []
    for _ in range(count):
        tar = b''.join(budget_item(rng) if rng.random() < 0.7 else tar_item(rng) for _ in range(rng.choice([1, 1, 2, 2, 3, 4])))
        tar += rng.choice([b'\0' * 1024, b'\0' * 1024, b'\0' * 512, b''])
        if rng.random() < 0.15:
            tar = tar_mutation(rng, tar)
        # Most often one budget is small and the others are out of reach, so that each is met on its own; sometimes all are small.
        budgets = {name: 10 ** 6 for name in BUDGET_CHOICES}
        for name in (list(BUDGET_CHOICES) if rng.random() < 0.3 else [rng.choice(list(BUDGET_CHOICES))]):
            budgets[name] = rng.choice(BUDGET_CHOICES[name])
        jobs.append({'b': b64(gzip_valid(rng, tar)), 'budgets': budgets})
    return jobs


TAR_BUDGET_BUCKETS = ('no budget is passed: equal listings', 'metadataBytes', 'paxRecords', 'sparseNumbers', 'members', 'D3')


def judge_tarbudget(job, want, got):
    """(agrees, bucket). Equal answers where the oracle's walk passes no budget; where it passes one, the OSError of that budget."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import tar_budget
    crossed = tar_budget.crossing(base64.b64decode(job['b']), job['budgets'])['crossed']
    if crossed is None:
        return want == got, TAR_BUDGET_BUCKETS[0]
    if want == {'error': 'RecursionError'} and got == want:
        return True, crossed  # the frame bound came first on both sides
    return got == ({'error': 'OSError', 'limit': 'metadata'} if crossed == 'D3' else {'error': 'OSError', 'budget': crossed}), crossed


# The archive.py:131-135 flow, by the outcome archive.py:136-139 would give it.
def manifest_flow(data):
    import io, re, tarfile
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            manifests = [member for member in archive.getmembers() if re.fullmatch(r'[^/]+/package.json', member.name) and member.isfile()]
            if not (len(manifests) == 1 and manifests[0].size <= 1024 * 1024):
                return ['refused', 'archive manifest required']
            manifest = json.load(archive.extractfile(manifests[0]))
            return ['accepted', hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()]
    except (OSError, ValueError, tarfile.TarError) as error:
        return ['caught', type(error).__name__]
    except Exception as error:
        return ['uncaught', type(error).__name__]


def container(data):
    """Whether gzip reads the whole file without an error, which is what the TS reader requires."""
    import gzip, io, zlib
    try:
        gzip.GzipFile(fileobj=io.BytesIO(data)).read()
        return 'valid'
    except (OSError, EOFError, zlib.error) as error:
        return type(error).__name__


def package_archive(rng, big=False):
    members = [tar_header(b'package/package.json', len(MANIFEST)) + padded(MANIFEST)]
    for index in range(rng.randrange(0, 4)):
        body = bytes(rng.randrange(256) for _ in range(rng.choice([0, 10, 700, 3000])))
        members.append(tar_header(b'package/file%d.js' % index, len(body)) + padded(body))
    if big:
        noise = random.Random(rng.random()).randbytes(rng.choice([200000, 400000]))
        members.insert(rng.randrange(len(members) + 1), tar_header(b'package/big.bin', len(noise)) + padded(noise))
    if rng.random() < 0.5:
        rng.shuffle(members)
    tar = b''.join(members) + b'\0' * 1024
    return tar + b'\0' * (-len(tar) % 10240) if rng.random() < 0.7 else tar


def gen_targz(rng, count):
    import gzip
    jobs = []
    pool = [(package_archive(rng), 'small package') for _ in range(6)] + [(package_archive(rng, True), 'package with a member of several hundred KiB') for _ in range(3)]
    fixed = [(b'', 'an empty file'), (b'\x1f', 'one byte of the magic'), (b'\x1f\x8b', 'the magic alone'), (b'\0', 'one zero byte'), (b'\0' * 600, 'zeros only'), (b'plain text, not gzip', 'not gzip at all'),
             (padded(tar_header(b'package/package.json', len(MANIFEST)) + padded(MANIFEST)) + b'\0' * 1024, 'an uncompressed tar'), (b'\x1f\x8b\x08\x00', 'four header bytes'),
             (b'\x1f\x8b\x07\x00' + b'\0' * 6, 'an unknown compression method'), (b'\x1f\x8b\x08\x08\0\0\0\0\0\3name', 'a header whose file name never ends')]
    for data, what in fixed:
        jobs.append({'b': b64(data), '_what': what})
    while len(jobs) < count:
        tar, base = rng.choice(pool)
        good = gzip.compress(tar, rng.choice([1, 6, 9]), mtime=0)
        roll = rng.randrange(20)
        data = bytearray(good)
        if roll == 0:
            what = 'undamaged'
        elif roll in (1, 2):
            cut = rng.choice([1, 2, 3, 9, 10, 11, len(data) - 9, len(data) - 8, len(data) - 7, len(data) - 4, len(data) - 1, rng.randrange(len(data)), rng.randrange(len(data)), max(10, len(data) - rng.randrange(1, 200))])
            data = data[:cut]
            what = 'truncated to %d of %d bytes' % (cut, len(good))
        elif roll in (3, 4, 5):
            at = rng.choice([0, 1, 2, 3, rng.randrange(4, 10), 10, 11, rng.randrange(10, len(data) - 8), rng.randrange(10, len(data) - 8), rng.randrange(len(data) // 2, len(data) - 8), max(10, len(data) - 8 - rng.randrange(1, 40))])
            bit = rng.randrange(8)
            data[at] ^= 1 << bit
            what = 'bit %d of byte %d flipped (%s)' % (bit, at, 'header' if at < 10 else 'deflate data, %d bytes before its end' % (len(good) - 8 - at))
        elif roll in (6, 7):
            at = len(data) - 8 + rng.randrange(4)
            data[at] ^= 1 << rng.randrange(8)
            what = 'one bit flipped in the CRC-32 trailer'
        elif roll in (8, 9):
            at = len(data) - 4 + rng.randrange(4)
            data[at] ^= 1 << rng.randrange(8)
            what = 'one bit flipped in the length trailer'
        elif roll == 10:
            tail = rng.choice([b'x', b'junk after the stream', b'\0\0\0x', b'\x1f', b'\x1f\x8b', b'\x1f\x8b\x08\x00', b'\0' * 9 + b'\1'])
            data += tail
            what = 'followed by %d bytes that are neither padding nor a member' % len(tail)
        elif roll == 11:
            data += b'\0' * rng.choice([1, 8, 5000])
            what = 'followed by zero padding'
        elif roll == 12:
            data += gzip.compress(rng.choice([b'', b'\0' * 1024, tar_header(b'second/package.json', len(MANIFEST)) + padded(MANIFEST)]), mtime=0)
            what = 'followed by a second, valid member'
        elif roll == 13:
            second = bytearray(gzip.compress(b'\0' * 2048, mtime=0))
            second[rng.choice([len(second) - 1, len(second) - 6, 12])] ^= 0x10
            data += second
            what = 'followed by a second member that is damaged'
        elif roll == 14:
            half = len(tar) // 2 // 512 * 512
            first = bytearray(gzip.compress(tar[:half], mtime=0))
            first[len(first) - 8 + rng.randrange(8)] ^= 0x04
            data = first + gzip.compress(tar[half:], mtime=0)
            what = 'two members, the trailer of the first one damaged'
        elif roll == 15:
            data[3] = rng.choice([1, 2, 4, 8, 16, 32, 64, 128, 0x1f])
            what = 'header flag byte changed to %#04x without the fields it announces' % data[3]
        elif roll == 16:
            data = data[:10] + rng.choice([b'', b'\x03\x00', b'\x01\x00\x00\xff\xff']) + data[-8:]
            what = 'deflate data replaced by a short stream'
        elif roll == 17:
            at = rng.randrange(10, len(data) - 8)
            del data[at:at + rng.choice([1, 2, 50])]
            what = 'bytes removed from the deflate data'
        elif roll == 18:
            at = rng.randrange(10, len(data) - 8)
            data[at:at] = bytes(rng.randrange(256) for _ in range(rng.choice([1, 2, 50])))
            what = 'bytes inserted into the deflate data'
        else:
            at = len(data) - 8 - rng.choice([1, 2, 3, 4, 5, 8, 12, 20])
            data[at] ^= 1 << rng.randrange(8)
            what = 'one bit flipped in the last bytes of the deflate data'
        jobs.append({'b': b64(bytes(data)), '_what': base + ', ' + what})
    return jobs


def py_targz(job):
    data = base64.b64decode(job['b'])
    return {'container': container(data), 'flow': manifest_flow(data)}


TARGZ_BUCKETS = ('valid container', 'both refuse as unreadable', 'oracle refuses for the missing manifest, TS as unreadable', 'oracle accepts, TS refuses', 'oracle lets an uncaught class escape, TS refuses', 'TS ACCEPTS A DAMAGED CONTAINER')


def judge_targz(want, got):
    """(agrees, bucket). A valid container must give the same flow; a damaged one must be an OSError in TS."""
    if not isinstance(got, dict) or 'flow' not in got:
        return False, TARGZ_BUCKETS[5]
    if want['container'] == 'valid':
        return got['container'] == 'valid' and got['flow'] == want['flow'], TARGZ_BUCKETS[0]
    if got['container'] == 'valid' or got['flow'][0] != 'caught' or got['flow'][1] not in ('BadGzipFile', 'OSError'):
        return False, TARGZ_BUCKETS[5]
    kind = want['flow'][0]
    return True, TARGZ_BUCKETS[{'caught': 1, 'refused': 2, 'accepted': 3, 'uncaught': 4}[kind]]


# Chains of extension headers recurse in tarfile; the bound is stated for a function a script calls directly.
BARE_TAR = r'''
import base64, gzip, io, json, sys, tarfile
def listing(data):
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            return 'members:%d' % len(archive.getmembers())
    except Exception as error:
        return type(error).__name__
for line in sys.stdin:
    print(listing(base64.b64decode(json.loads(line)['b'])))
'''


def chain(kind, length, first, extra=b''):
    link = pax_block([pax_record(b'comment', b'x')] + ([pax_record(b'path', b'p/package.json')] if extra == b'path' else [])) if kind == 'x' else tar_header(b'././@LongLink', 2, b'L') + padded(b'n\0')
    lead = b'' if first else tar_header(b'lead', 0)
    return lead + link * length + tar_header(b'package/package.json', len(MANIFEST)) + padded(MANIFEST) + b'\0' * 1024


def gen_tardepth(rng, count):
    import gzip
    jobs = []
    for kind in ('x', 'L'):
        for first in (True, False):
            for length in list(range(300, 345)) + [1, 50, 400, 1000]:
                jobs.append({'b': b64(gzip.compress(chain(kind, length, first), 1, mtime=0)), '_what': '%s chain of %d, %s' % (kind, length, 'first member' if first else 'second member')})
    return jobs


def bare_tar(jobs):
    done = subprocess.run([sys.executable, '-I', '-c', BARE_TAR], input=''.join(json.dumps({'b': job['b']}) + '\n' for job in jobs), capture_output=True, text=True, timeout=600)
    if done.returncode:
        raise RuntimeError('bare interpreter failed: ' + done.stderr[-500:])
    lines = done.stdout.split('\n')[:-1]
    if len(lines) != len(jobs):
        raise RuntimeError('bare interpreter answered %d of %d' % (len(lines), len(jobs)))
    return lines


def gen_pyint(rng, count):
    pool = list('0123456789') * 3 + ['_', '_', '+', '-', ' ', '\t', '\n', '\x0b', '\x0c', '\r', '\x1c', '\x1f', '\x00', '\x7f', '\x85', '\xa0', '\u2003', '\u3000', '\ufeff', '\u0660', '\u0669', '\u06f5', '\uff10', '\uff19',
                                      '\U0001d7ce', '\U0001d7ff', '\U0001e950', '\u00b2', '\u2460', 'x', 'e', '.', 'o', '0x', '0o', '\u0966']
    jobs = [{'s': ''.join(rng.choice(pool) for _ in range(rng.choice([0, 1, 1, 2, 3, 5, 8])))} for _ in range(count)]
    # underscores: single ones between digits and nowhere else
    jobs += [{'s': text} for text in ('1__0', '1_0', '_1', '1_', '1___0', '1_0_', '+_1', '-_1', '1_ 0', '1 _0', '0__0', '1_0_0', '1__0_0', '__', '_', '1_+0', '\u0661_\u0662', '\u0661__\u0662', ' 1__0 ')]
    jobs += [{'b': b64(text)} for text in (b'1__0', b'1_0', b'_1', b'1_', b'1___0', b'1_0_', b' 1__0\n')]
    jobs += [{'s': text} for text in ('9' * 4300, '9' * 4301, '-' + '9' * 4300, '-' + '9' * 4301, '0' * 4301, '1_' * 2150 + '1', '1_' * 2151 + '1', ' ' + '7' * 4300 + ' ')]
    jobs += [{'b': b64(''.join(rng.choice(pool) for _ in range(rng.choice([0, 1, 2, 3, 5]))).encode('utf-8', 'surrogatepass'))} for _ in range(count // 2)]
    # every code point CPython knows as a decimal digit, alone and after an ASCII digit
    digits = [chr(code) for code in range(0x110000) if chr(code).isdecimal()]
    jobs += [{'s': rng.choice(['', '1', '-']) + digit} for digit in digits]
    return jobs


def py_pyint(job):
    try:
        return str(int(job['s'] if 's' in job else base64.b64decode(job['b'])))
    except ValueError:
        return 'ValueError'


# ---------------------------------------------------------------- driver

def kinds(full):
    return {
        'unquote': (gen_unquote, py_unquote, 20000),
        'url': (gen_url, py_url, 40000),
        'repository': (gen_repository, py_repository, 25000),
        'sweep': (lambda rng, count: gen_sweep(rng, count, full), None, 1),
        'b64': (gen_b64, py_b64, 40000),
        'iso': (gen_iso, py_iso, 4000),
        'json': (gen_json, py_json, 12000),
        'depth': (gen_depth, None, 1),
        'utf8': (gen_utf8, py_utf8, 20000),
        'fsencode': (gen_fsencode, py_fsencode, 20000),
        'pathpure': (gen_pathpure, py_pathpure, 20000),
        'pathfs': (gen_pathfs, py_pathfs, 8000),
        'readtext': (gen_readtext, py_readtext, 400),
        'cook_code': (gen_cook_code, py_cook_code, 15000),
        'cook_stream': (gen_cook_stream, py_cook_stream, 300),
        'diagnostic': (gen_diagnostic, py_diagnostic, 5000),
        'adapter': (gen_adapter, py_adapter, 8000),
        'eq': (gen_eq, py_eq, 30000),
        'pyops': (gen_pyops, py_pyops, 20000),
        'pyint': (gen_pyint, py_pyint, 6000),
        'tar': (gen_tar, py_tar, 12000),
        'tarbudget': (gen_tarbudget, py_tar, 12000),
        'tardepth': (gen_tardepth, None, 1),
        'targz': (gen_targz, py_targz, 4000),
    }


def node_answers(jobs, node, processes):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    stamp = str(os.getpid())
    shards = [[] for _ in range(max(1, min(processes, len(jobs))))]
    for index, job in enumerate(jobs):
        shards[index % len(shards)].append(job)
    running = []
    try:
        for number, shard in enumerate(shards):
            source = SCRATCH / ('jobs-%s-%d.jsonl' % (stamp, number))
            target = SCRATCH / ('answers-%s-%d.jsonl' % (stamp, number))
            source.write_text(''.join(json.dumps({key: value for key, value in job.items() if not key.startswith('_')}) + '\n' for job in shard))
            running.append((subprocess.Popen([node, str(ROOT / 'test/differential/port_primitives.mjs'), str(source), str(target)], cwd=ROOT, stderr=subprocess.PIPE, text=True), source, target))
        answers = [None] * len(jobs)
        for number, (process, source, target) in enumerate(running):
            _, errors = process.communicate(timeout=1800)
            if process.returncode:
                raise RuntimeError('TS runner failed: ' + errors[-2000:])
            lines = target.read_text().split('\n')[:-1]
            if len(lines) != len(shards[number]):
                raise RuntimeError('TS runner answered %d of %d jobs' % (len(lines), len(shards[number])))
            for position, line in enumerate(lines):
                answers[number + position * len(shards)] = json.loads(line)
        return answers
    finally:
        for process, source, target in running:
            if process.poll() is None:
                process.kill()
            source.unlink(missing_ok=True)
            target.unlink(missing_ok=True)


def brief(job):
    shown = {key: value for key, value in job.items() if not key.startswith('_') and key not in ('k', 'domain')}
    return json.dumps(shown)[:400]


def run(node='node', scale=1.0, only=None, full=True, processes=None, seed=20261114):
    processes = processes or min(16, os.cpu_count() or 2)
    table = kinds(full)
    selected = [name for name in table if not only or name in only]
    report = {}
    jobs, expected = [], []
    remove_tree()
    try:
        if 'pathfs' in selected or 'readtext' in selected:
            build_tree()
        sweeps = []
        for name in selected:
            generate, oracle, count = table[name]
            rng = random.Random('%d/%s' % (seed, name))
            made = generate(rng, max(1, int(count * scale)))
            for job in made:
                job['k'] = name
            if name == 'sweep':
                sweeps = made
                answers = [None] * len(made)
            elif name == 'depth':
                answers = bare_outcomes(made)
            elif name == 'tardepth':
                answers = bare_tar(made)
            else:
                answers = [oracle(job) for job in made]
            jobs += made
            expected += answers
        if sweeps:
            with multiprocessing.Pool(min(processes, len(sweeps))) as pool:
                swept = pool.map(run_sweep, sweeps, chunksize=1)
            first = jobs.index(sweeps[0])
            expected[first:first + len(sweeps)] = swept
        actual = node_answers(jobs, node, processes)
    finally:
        remove_tree()
    witnesses, examples = {}, {}
    for job, want, got in zip(jobs, expected, actual):
        entry = report.setdefault(job['k'], {'cases': 0, 'mismatches': 0})
        entry['cases'] += 1
        want = joined(want)
        if job['k'] == 'targz':
            agrees, bucket = judge_targz(want, got)
            entry.setdefault('buckets', {name: 0 for name in TARGZ_BUCKETS})[bucket] += 1
            examples.setdefault(bucket, job['_what'])
        elif job['k'] == 'tar':
            agrees, bucket = judge_tar(job, want, got)
            if bucket is not None:
                entry.setdefault('metadata_limit', {name: 0 for name in TAR_D3_BUCKETS})[bucket] += 1
        elif job['k'] == 'tarbudget':
            agrees, bucket = judge_tarbudget(job, want, got)
            entry.setdefault('budgets', {name: 0 for name in TAR_BUDGET_BUCKETS})[bucket] += 1
            # A refusal must not hide an archive the oracle reads: count those apart from the ones it refuses anyway.
            if bucket != TAR_BUDGET_BUCKETS[0] and 'members' in want:
                entry['oracle_lists_and_ts_refuses'] = entry.get('oracle_lists_and_ts_refuses', 0) + 1
        else:
            agrees = want == got
        if not agrees:
            entry['mismatches'] += 1
            if job['k'] == 'sweep':
                differing = next((pair for pair in zip(want, got) if pair[0] != pair[1]), (want[-1:], got[-1:]))
                detail = {'python_run': differing[0], 'ts_run': differing[1], 'python_runs': len(want), 'ts_runs': len(got)}
            elif isinstance(want, dict) and isinstance(got, dict):
                detail = {key: [want.get(key), got.get(key)] for key in sorted(set(want) | set(got)) if want.get(key) != got.get(key)}
            else:
                detail = {'python': want, 'ts': got}
            if len(witnesses.setdefault(job['k'], [])) < 5:
                witnesses[job['k']].append({'job': job.get('_what') or brief(job), 'differs': json.dumps(detail)[:600]})
    if 'targz' in report:
        report['targz']['examples'] = examples
    return report, witnesses


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('run')
    command.add_argument('--node', default='node')
    command.add_argument('--scale', type=float, default=1.0)
    command.add_argument('--kind', action='append')
    command.add_argument('--sampled-sweep', action='store_true', help='sweep a sample of the code points instead of all of them')
    command.add_argument('--processes', type=int)
    args = parser.parse_args()
    report, witnesses = run(args.node, args.scale, args.kind, not args.sampled_sweep, args.processes)
    print(json.dumps(report, sort_keys=True))
    for bucket, count in sorted(report.get('targz', {}).get('buckets', {}).items()):
        print('BUCKET targz %d %s; first: %s' % (count, bucket, report['targz']['examples'].get(bucket, '-')))
    for kind, items in sorted(witnesses.items()):
        for item in items:
            print('WITNESS', kind, json.dumps(item, sort_keys=True))
    return int(any(entry['mismatches'] for entry in report.values()))


if __name__ == '__main__':
    sys.exit(main())
