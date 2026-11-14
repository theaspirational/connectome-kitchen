#!/usr/bin/env python3
"""Plan risk R6: the oracle bound to Ansible's real `combine`, against the TS port of the stand-in.

`connectome_config` merges with `ansible.plugins.filter.core.combine` when
ansible-core is importable and with its own stand-in otherwise (config.py:5-20).
Every committed vector was recorded without ansible-core, so `src/config.ts` is
a port of the stand-in. This script puts an ansible-core source tree on the
path, checks that the oracle really runs Ansible's `combine`, and compares:

  * every committed and generated vector of `connectome_config`, by function;
  * a seeded fuzz of four-layer `merge_layers` calls built to reach the
    shortcuts of Ansible's `merge_hash` (equal subtrees under another key
    order, `1`/`True`/`1.0`, nulls, empty layers).

The result is test/vectors/r6-combine.golden. What it closes and what it does
not is stated in docs/port-notes.md: `merge_layers`, the one production merge
site, yes; the `combine` helper called on its own, no.

    python3 test/differential/r6_combine.py --ansible /path/to/ansible-core/lib [--check]
"""
import argparse, collections, json, random, sys
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / 'test/vectors/r6-combine.golden'
SEED, COUNT = 1, 4000
KEYS = ['connectors', 'modules', 'catalog', 'a', 'b', 'enabled', 'x']
LEAVES = [None, True, False, 0, 1, 1.0, 'connector', 'v', [], [1], {}]


def layers(rng):
    def value(depth):
        roll = rng.random()
        if depth > 3 or roll < 0.4:
            return rng.choice(LEAVES)
        if roll < 0.5:
            return [value(depth + 1) for _ in range(rng.randrange(3))]
        return {key: value(depth + 1) for key in rng.sample(KEYS, rng.randrange(4))}

    def permuted(item):
        """The same value under another key order, or with an equal scalar of another type."""
        if isinstance(item, dict):
            keys = list(item)
            rng.shuffle(keys)
            return {key: permuted(item[key]) for key in keys}
        if isinstance(item, list):
            return [permuted(entry) for entry in item]
        if item is True and rng.random() < .3:
            return 1
        if item == 1 and type(item) is int and rng.random() < .3:
            return rng.choice([True, 1.0])
        return item

    def entry():
        record = {key: value(2) for key in rng.sample(['enabled', 'kind', 'required_secrets', 'opt'], rng.randrange(4))}
        if 'enabled' in record and rng.random() < .7:
            record['enabled'] = rng.choice([True, False, None])
        return record

    def layer():
        made = {}
        for section in rng.sample(['connectors', 'modules', 'catalog', 'other'], rng.randrange(1, 4)):
            made[section] = (rng.choice([None, {name: rng.choice([entry(), None]) for name in rng.sample(['p', 'q', 'r'], rng.randrange(4))}])
                             if section != 'other' else value(1))
        return made

    four = [layer() for _ in range(4)]
    for index in range(1, 4):
        if rng.random() < .35:
            four[index] = permuted(four[rng.randrange(index)])
        elif rng.random() < .2:
            four[index] = {}
    return four


def unordered(value):
    if isinstance(value, dict) and '$mapping' in value:
        return sorted((key, json.dumps(unordered(item), sort_keys=True)) for key, item in value['$mapping'])
    if isinstance(value, dict):
        return {key: unordered(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unordered(item) for item in value]
    return value


def build(ansible, node):
    sys.path.insert(0, str(ansible))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ts_port
    from record import canonical, encode
    ts_port.initialize()
    import connectome_config
    from ansible.release import __version__ as version
    bound = connectome_config.combine.__module__ + '.' + connectome_config.combine.__qualname__
    if not connectome_config.combine.__module__.startswith('ansible.'):
        raise SystemExit('the oracle did not bind Ansible\'s combine: ' + bound)
    vectors = [vector for vector in ts_port.committed() + ts_port.generated_vectors() if vector.function.startswith('connectome_config.')]
    if any(vector.row.get('bare') for vector in vectors):
        raise SystemExit('a config vector runs in a bare interpreter and would bypass Ansible')
    tally, known = {}, []
    for record in ts_port.evaluate(vectors, node=node):
        kind = ts_port.classify(record)
        function = tally.setdefault(record['vector'].function, collections.Counter())
        function[kind] += 1
        if kind in ('mismatch', 'crash-mismatch'):
            known.append({'id': record['vector'].id, 'function': record['vector'].function, 'args': record['vector'].row['args'], 'kwargs': record['vector'].row['kwargs'],
                          'ansible': json.loads(record['oracle']), 'port': json.loads(record['ts'])})
    rng = random.Random(SEED)
    cases = [layers(rng) for _ in range(COUNT)]
    oracle = [canonical(ts_port.invoke('connectome_config.merge_layers', [case], {})) for case in cases]
    port = ts_port.node_outcomes([{'id': str(index), 'function': 'connectome_config.merge_layers', 'args': [encode(case)], 'kwargs': {}} for index, case in enumerate(cases)], node)
    fuzz, example = collections.Counter(), None
    for case, want, got in zip(cases, oracle, port):
        want_value, got_value = json.loads(want), json.loads(got)
        if want == got:
            kind = 'crash-equal' if 'crash' in want_value else 'accepted-equal' if 'result' in want_value else 'refused-equal'
        elif ('result' in want_value) != ('result' in got_value) or 'result' not in want_value:
            kind = 'ACCEPT-REFUSE-DIFFERENCE'
        elif unordered(want_value) == unordered(got_value):
            kind = 'accepted-key-order-differs'
            example = example or {'layers': case, 'ansible': want_value, 'port': got_value}
        else:
            kind = 'VALUE-DIFFERENCE'
        fuzz[kind] += 1
    return {
        'ansible_core': version, 'combine': bound,
        'vectors': {function: dict(sorted(counts.items())) for function, counts in sorted(tally.items())},
        'known_mismatches_of_the_combine_helper': known,
        'merge_layers_fuzz': {'seed': SEED, 'cases': COUNT, 'outcomes': dict(sorted(fuzz.items())), 'key_order_example': example},
    }


def sound(report):
    """What the R6 ruling rests on: no mismatch on merge_layers, none on the two functions that do not merge, no value or accept/refuse difference in the fuzz."""
    for function, counts in report['vectors'].items():
        if function != 'connectome_config.combine' and (counts.get('mismatch') or counts.get('crash-mismatch')):
            return False
    outcomes = report['merge_layers_fuzz']['outcomes']
    return not outcomes.get('VALUE-DIFFERENCE') and not outcomes.get('ACCEPT-REFUSE-DIFFERENCE')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ansible', type=Path, required=True, help='the lib directory of an ansible-core source tree')
    parser.add_argument('--node', default='node')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    report = build(args.ansible.resolve(), args.node)
    text = json.dumps(report, indent=1) + '\n'
    print(json.dumps({'ansible_core': report['ansible_core'], 'vectors': report['vectors'], 'known_mismatches_of_the_combine_helper': [entry['id'] for entry in report['known_mismatches_of_the_combine_helper']],
                      'merge_layers_fuzz': report['merge_layers_fuzz']['outcomes'], 'sound': sound(report)}))
    if args.check:
        return int(not sound(report) or GOLDEN.read_text() != text)
    GOLDEN.write_text(text)
    return int(not sound(report))


if __name__ == '__main__':
    sys.exit(main())
