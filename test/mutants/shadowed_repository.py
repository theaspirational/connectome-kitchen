#!/usr/bin/env python3
"""Evidence for the `repository` and `registry_origin` checks no vector can isolate (test/mutants/ts-unkilled.golden).

Each check is implied by another check of the same function. The implications
are tested here on the oracle, on seeded random strings biased towards the
cases that matter. A counterexample would be an input that kills the mutant.
"""
import json, random, sys
from pathlib import Path
from urllib.parse import unquote, urlsplit
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'oracle/module_utils'))
import connectome_lock as lock

rng = random.Random(20261114)
PIECES = ['', 'a', 'Z', '0', '/', ':', '@', '%', '%41', '%00', '%0a', '%20', '%2F', '%3A', '%40', '%ff', '%EF%BB%BF', ' ', '\t', '\n', '\x00', '\x1f', '\x7f', '\x85', '\x9f', '\xa0',
          ' ', 'é', '\U0001f600', '[', ']', '-', '.', '_', '?', '#', '\\', 'https://', 'ssh://', 'HTTPS://', 'git', 'example.invalid', 'user', 'pw', '//', '=']


def text():
    return ''.join(rng.choice(PIECES) for _ in range(rng.randrange(0, 12)))


def url():
    scheme = rng.choice(['https', 'ssh', 'HTTPS', 'Ssh', 'https', 'ssh'])
    user = ''.join(rng.choice(PIECES) for _ in range(rng.randrange(0, 3)))
    secret = ''.join(rng.choice(PIECES) for _ in range(rng.randrange(0, 3)))
    host = ''.join(rng.choice(PIECES) for _ in range(rng.randrange(0, 3)))
    return scheme + '://' + user + ':' + secret + '@' + host + rng.choice(['', '/x', '/'])


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 200000
    text_cases = text_refused = url_cases = with_password = 0
    for _ in range(count):
        value = text()
        text_cases += 1
        if not lock.text(value):
            text_refused += 1
            # lock.repository.text: a string `text` refuses is refused again after unquoting.
            if lock.text(unquote(value)):
                print(json.dumps({'counterexample': 'text', 'value': value}))
                return 1
        value = url() if rng.random() < 0.8 else text()
        url_cases += 1
        if any(c.isspace() for c in unquote(value)):
            continue  # refused before the URL is parsed
        try:
            parts = urlsplit(value)
            secret = parts.password
        except ValueError:
            continue
        if parts.scheme in {'https', 'ssh'} and secret is not None:
            with_password += 1
            # lock.repository.password: a URL with a password already has the credential shape.
            if not lock.credential_shaped(value):
                print(json.dumps({'counterexample': 'password', 'value': value}))
                return 1
    # cook.provenance.origin-password, -query, -fragment (archive.py:39-46): the same three implications for a registry origin.
    origin_password = origin_query = 0
    for _ in range(count):
        value = url() if rng.random() < 0.5 else 'https://' + text() + rng.choice(['', '?', '#', '?x', '#x', '/'])
        if not lock.text(value) or any(c.isspace() or c in '%\\?#' for c in value):
            continue  # refused before the URL is parsed
        try:
            parts = urlsplit(value)
        except ValueError:
            continue
        if parts.password is not None:
            origin_password += 1
            if not lock.credential_shaped(value):
                print(json.dumps({'counterexample': 'origin-password', 'value': value}))
                return 1
        if parts.query or parts.fragment:
            origin_query += 1
            print(json.dumps({'counterexample': 'origin-query-or-fragment', 'value': value}))
            return 1
    print(json.dumps({'strings': text_cases, 'refused_by_text': text_refused, 'urls': url_cases, 'urls_with_password_reaching_the_check': with_password,
                      'origins_with_password_reaching_the_check': origin_password, 'origins_with_query_or_fragment_reaching_the_check': origin_query, 'counterexamples': 0}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
