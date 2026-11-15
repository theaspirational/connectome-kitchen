"""The TS port of errors, lock, inputs, archive and config: committed evidence is current and complete."""
import json, re, subprocess, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / 'test/differential'), str(ROOT / 'test/mutants')]
import ts_mutants
import ts_port

PORTED = ('errors', 'lock', 'inputs', 'archive', 'config')
MODULE_FILE = r'connectome_(' + '|'.join(PORTED) + r')\.py$'


class Port(unittest.TestCase):
    def test_committed_corpus_is_what_the_oracle_answers_now(self):
        rows, trees = ts_port.port_text()
        self.assertEqual(ts_port.PORT.read_text(), rows)
        self.assertEqual(ts_port.PORT_TREES.read_text(), trees)
        outcomes = [json.loads(line)['outcome'] for line in rows.splitlines()]
        self.assertGreater(len(outcomes), 1800)
        # Acceptances and refusals are both present in bulk; a corpus of refusals alone proves little.
        self.assertGreater(sum('result' in o for o in outcomes), 800)
        self.assertGreater(sum('exception' in o for o in outcomes), 700)
        functions = {json.loads(line)['function'].split('.')[0] for line in rows.splitlines()}
        self.assertEqual(functions, {'connectome_' + name for name in PORTED})

    def test_every_oracle_mutant_of_the_ported_modules_has_a_ts_mutant_with_the_same_id(self):
        anchors = json.loads(ts_mutants.ANCHORS.read_text())
        self.assertEqual(anchors, ts_mutants.build_anchors())
        ids = {anchor['id'] for anchor in anchors}
        guards = {a['id'] for a in json.loads((ROOT / 'test/mutants/guard-anchors.golden').read_text()) if a['id'].split('.')[0] in PORTED}
        self.assertEqual(len(guards), 60 + 24 + 30 + 4)
        self.assertLessEqual(guards, ids)
        named = set()
        for name in ('named-guard-anchors.golden', 'named-site-anchors.golden'):
            named |= {a['id'] for a in json.loads((ROOT / 'test/mutants' / name).read_text()) if re.search(MODULE_FILE, a['file'])}
        self.assertEqual(len(named), 59 + 6)
        self.assertLessEqual(named, ids)
        # The names of the phase-1 list that are anchored in these modules: all of them, none invented.
        names = {entry['id'] for entry in json.loads((ROOT / 'test/mutants/named-checks.golden').read_text())
                 if any(re.search(MODULE_FILE, anchor['file']) for anchor in entry['anchors'])}
        self.assertLessEqual({n for n in names if n.startswith(('lock.', 'inputs.', 'config.'))}, {i.split('#')[0] for i in ids})
        # Functions without a `need` or `raise` have no oracle mutant; they must not be left without a TS one.
        for prefix in ('lock.credential.', 'lock.repository.', 'errors.closed.', 'cook.provenance.'):
            self.assertGreaterEqual(sum(i.startswith(prefix) for i in ids), 9, prefix)
        for anchor in anchors:
            self.assertEqual((ROOT / anchor['file']).read_text().count(anchor['before']), 1, anchor['id'])
            self.assertNotEqual(anchor['before'], anchor['after'], anchor['id'])

    def test_recorded_mutant_results_leave_nothing_unexplained(self):
        results = json.loads(ts_mutants.RESULTS.read_text())
        anchors = json.loads(ts_mutants.ANCHORS.read_text())
        self.assertEqual([r['id'] for r in results['results']], [a['id'] for a in anchors])
        self.assertEqual(results['unit_suite_disagrees'], [])
        unkilled = {entry['id']: entry for entry in json.loads((ROOT / 'test/mutants/ts-unkilled.golden').read_text())}
        for result in results['results']:
            if result['status'] == 'killed':
                self.assertEqual(result['unit_suite'], 'fails', result['id'])
                self.assertTrue(result['declared'], result['id'])
                self.assertNotIn(result['id'], unkilled)
            else:
                # Not waived here: listed with a written analysis for the reviewer.
                self.assertIn(result['status'], ('survived', 'compiler', 'crash-only'), result['id'])
                self.assertEqual(unkilled[result['id']]['status'], result['status'], result['id'])
                self.assertEqual({'survived': 'redundant survivor', 'compiler': 'compiler', 'crash-only': 'crash-only'}[result['status']], unkilled[result['id']]['classification'], result['id'])
                self.assertGreater(len(unkilled[result['id']]['analysis']), 200)
        self.assertEqual(set(unkilled), {r['id'] for r in results['results'] if r['status'] != 'killed'})
        self.assertEqual(len({entry['analysis'] for entry in unkilled.values()}), len(unkilled))

    def test_porting_map_is_current_and_marks_every_row_of_the_ported_modules(self):
        self.assertEqual(subprocess.run([sys.executable, str(ROOT / 'scripts/porting-map.py'), '--check'], capture_output=True).returncode, 0)
        text = (ROOT / 'PORTING.md').read_text()
        self.assertIn('Rows with at least one ported site: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 23, 28, 39, 49, 59, 63, 69.', text)
        for line in text.splitlines():
            site = re.match(r'\| (\d+) \| `[^`]+` \| ported[^|]*\| `(src/\w+\.ts):(\d+)` \| `(lock|inputs|errors|archive|config)\.py:(\d+)', line)
            if site:
                source = (ROOT / site[2]).read_text().split('\n')
                self.assertIn('// row ' + site[1] + ' ', source[int(site[3]) - 1], line)
        # Every row id cited by a TS guard comment is an id the plan gives that row.
        import importlib.machinery
        inventory = importlib.machinery.SourceFileLoader('inventory', str(ROOT / 'scripts/inventory-sites')).load_module()
        ids = {row['row']: row['ids'] for row in inventory.inventory(ROOT / 'docs/approved-plan.md')}
        cited = set(re.findall(r'^\| (\d+) \| `([^`]+)` \| ported', text, re.M))
        self.assertGreater(len(cited), 30)
        for row, ident in cited:
            self.assertIn(ident, ids[int(row)], row)

    def test_ported_modules_and_lazy_imports(self):
        ported = sorted(path.name for path in (ROOT / 'src').glob('*.ts'))
        self.assertEqual(ported, ['archive.ts', 'cli.ts', 'config.ts', 'contract.ts', 'errors.ts', 'index.ts', 'inputs.ts', 'lock.ts'])
        self.assertNotIn('bind_seam', (ROOT / 'src/archive.ts').read_text())
        lock = (ROOT / 'src/lock.ts').read_text()
        self.assertNotRegex(lock, r"(?m)^import (?!type )[^;]*from '\./(inputs|archive)\.js'")
        self.assertEqual(len(re.findall(r"lazy\('\./inputs\.js'\) as typeof Inputs; // lock\.py:149-152", lock)), 1)
        self.assertEqual(len(re.findall(r"lazy\('\./archive\.js'\) as typeof Archive; // lock\.py:(255-258|281-284)", lock)), 2)

    def test_deliberate_divergences_are_named_and_still_hold_on_the_oracle(self):
        entries = json.loads((ROOT / 'test/vectors/port-divergences.golden').read_text())
        self.assertEqual(len({entry['id'] for entry in entries}), len(entries))
        self.assertEqual({entry['divergence'] for entry in entries}, {'D1', 'D2', 'D3'})
        ts_port.initialize()
        for entry in entries:
            self.assertGreater(len(entry['reason']), 100, entry['id'])
            self.assertTrue(entry['reason'].startswith(entry['divergence'] + ': '), entry['id'])
            self.assertNotEqual(entry['oracle'], entry['port'], entry['id'])
            vector = ts_port.Vector('divergence', entry['id'], entry)
            try:
                args, kwargs = vector.prepare()
                outcome = json.loads(vector.unbind(ts_port.canonical(ts_port.invoke(entry['function'], ts_port.decode(args), ts_port.decode(kwargs)))))
                self.assertEqual(ts_port.unordered(outcome) if entry.get('unordered') else outcome, entry['oracle'], entry['id'])
            finally:
                vector.cleanup()
        # D3 has one entry for each header type the limit applies to.
        self.assertEqual(sorted(entry['id'].split('.')[1] for entry in entries if entry['divergence'] == 'D3'), ['longlink', 'longname', 'pax', 'pax-global', 'pax-solaris'])
        # D1 changes the public code where the oracle would have refused the content: that is recorded, not implied.
        by_id = {entry['id']: entry for entry in entries}
        changed = by_id['gzip.crc-trailer.identity-mismatch']
        self.assertEqual(dict(changed['oracle']['public']['$mapping'])['error_code'], 'E_ARCHIVE_IDENTITY')
        self.assertEqual(dict(changed['port']['public']['$mapping'])['error_code'], 'E_ADAPTER_INPUT')
        for entry in entries:
            if entry['divergence'] in ('D1', 'D3'):
                self.assertEqual((entry['port']['message'], dict(entry['port']['public']['$mapping'])['error_code']), ('unreadable archive or manifest', 'E_ADAPTER_INPUT'), entry['id'])
                if entry['divergence'] == 'D3':
                    # The small D3 entries state a size no machine allocates; the archives that hold a block at the limit are in port-large.golden.
                    self.assertEqual(entry['oracle'], {'crash': 'MemoryError'}, entry['id'])
            else:
                self.assertEqual(entry['port'], {'crash': 'TypeError'}, entry['id'])
                self.assertIn({'$float': 'nan'}, entry['oracle']['result'], entry['id'])

    def test_rulings_are_recorded_as_received_and_nothing_unkilled_counts_as_killed(self):
        record = json.loads((ROOT / 'test/port-rulings.golden').read_text())
        rulings = {entry['id']: entry for entry in record['rulings']}
        self.assertEqual(sorted(rulings), ['B9', 'D1', 'D2', 'D3', 'L1', 'M1', 'O1', 'R6'])
        for entry in rulings.values():
            self.assertGreater(len(entry['as_received']), 40, entry['id'])
        results = {r['id']: r['status'] for r in json.loads(ts_mutants.RESULTS.read_text())['results']}
        reviewed = json.loads((ROOT / 'test/mutants/ts-reviewed.golden').read_text())
        classes = rulings['M1']['classification']
        self.assertEqual({name: len(ids) for name, ids in classes.items()}, {'redundant survivor': 5, 'compiler': 2, 'crash-only': 1})
        self.assertEqual(sorted(entry['id'] for entry in reviewed), sorted(ident for ids in classes.values() for ident in ids))
        for entry in reviewed:
            self.assertNotEqual(results[entry['id']], 'killed', entry['id'])
            self.assertIn(entry['id'], classes[entry['classification']])
        self.assertEqual(sum(status == 'killed' for status in results.values()), 262)
        self.assertEqual(len(results), 270)
        # A choice a ruling left open is recorded as the implementer's, apart from the ruling's own words.
        for ident in ('D3', 'B9', 'L1'):
            self.assertGreater(len(rulings[ident]['left_to_the_implementer']), 20, ident)
        # The witness a ruling moved to a later module is an open obligation with a trigger, not a note.
        owed = {entry['id']: entry for entry in record['obligations']}
        self.assertEqual(owed['archive.L140.4']['due_when_any_exists'], ['src/cook.ts', 'src/deploy.ts'])
        backlog = {entry['id'] for entry in record['backlog']}
        for item in rulings['O1']['items']:
            self.assertGreater(len(item['bound']), 60, item['id'])
            self.assertIn(item['backlog'], backlog, item['id'])
        r6 = json.loads((ROOT / 'test/vectors/r6-combine.golden').read_text())
        self.assertEqual(sorted(entry['id'] for entry in r6['known_mismatches_of_the_combine_helper']), sorted(rulings['R6']['known_mismatches_of_the_combine_helper']))
        self.assertEqual(r6['vectors']['connectome_config.merge_layers'].get('mismatch', 0), 0)

    def test_the_readiness_check_is_red_while_a_review_or_a_ruling_is_owed(self):
        record = json.loads((ROOT / 'test/port-rulings.golden').read_text())
        done = subprocess.run([sys.executable, str(ROOT / 'scripts/check-port-ready')], capture_output=True, text=True)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[0], '262 of 270 TS mutants killed')
        self.assertIn('RULED: 8 TS mutants are not kills and are accepted as such by ruling M1: 5 redundant survivors, 2 compiler, 1 crash-only', lines)
        review = record['delta_review']
        owed = review['status'] != 'passed' or any(entry['status'] != 'ruled' for entry in record['open_findings'])
        self.assertEqual(done.returncode, int(owed), done.stdout)
        self.assertEqual(any(line.startswith('BLOCKED: ') for line in lines), owed, done.stdout)
        self.assertEqual(any(line.startswith('PASS port evidence') for line in lines), not owed)
        if review['status'] != 'passed':
            self.assertTrue(any(line.startswith('BLOCKED: the delta review is ') for line in lines), done.stdout)
        else:
            # A passed review names the commit it read and its report, and says who recorded it on whose word.
            self.assertRegex(review['commit'], r'^[0-9a-f]{40}$')
            self.assertRegex(review['review_report_sha256'], r'^[0-9a-f]{64}$')
            self.assertIn('on this instruction of the technical owner', review['set_by'])
            self.assertIn('RULED: the delta review of %s passed (%s; report sha256 %s)' % (review['commit'][:7], review['covers'], review['review_report_sha256']), lines)
        for entry in record['open_findings']:
            self.assertEqual(('BLOCKED: open finding %s has no ruling (test/port-rulings.golden)' % entry['id']) in lines, entry['status'] != 'ruled', entry['id'])

    def test_the_readiness_check_blocks_on_each_thing_it_claims_to_hold(self):
        # The check, run on copies of the evidence with one thing wrong each: every one must turn it red, with its own line.
        import shutil, tempfile
        names = ['scripts/check-port-ready', 'scripts/porting-map.py', 'PORTING.md', 'test/port-rulings.golden', 'test/vectors/port-divergences.golden', 'test/vectors/port-large.golden',
                 'test/vectors/r6-combine.golden', 'test/mutants/ts-results.golden', 'test/mutants/ts-unkilled.golden', 'test/mutants/ts-reviewed.golden', 'oracle/versions.yml']

        def run(change):
            with tempfile.TemporaryDirectory(dir=ROOT / '.cache' if (ROOT / '.cache').is_dir() else None) as scratch:
                root = Path(scratch)
                for name in names:
                    (root / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy(ROOT / name, root / name)
                for folder in ('src', 'oracle/module_utils', 'docs'):
                    shutil.copytree(ROOT / folder, root / folder)
                change(root)
                done = subprocess.run([sys.executable, str(root / 'scripts/check-port-ready')], capture_output=True, text=True)
                return done.returncode, done.stdout

        def rulings(edit, settled=False):
            def change(root):
                record = json.loads((root / 'test/port-rulings.golden').read_text())
                if settled:
                    settle(record)
                edit(record)
                (root / 'test/port-rulings.golden').write_text(json.dumps(record))
            return change

        def settled(change):
            def both(root):
                rulings(lambda record: None, settled=True)(root)
                change(root)
            return both

        def replace(name, old, new):
            def change(root):
                text = (root / name).read_text()
                self.assertEqual(text.count(old), 1, old)
                (root / name).write_text(text.replace(old, new))
            return change

        def large(edit):
            def change(root):
                record = json.loads((root / 'test/vectors/port-large.golden').read_text())
                edit({case['id']: case for case in record['cases']})
                (root / 'test/vectors/port-large.golden').write_text(json.dumps(record))
            return change
        # Unchanged, the copy blocks on exactly the findings that have no ruling, as the tree does.
        unruled = ['BLOCKED: open finding %s has no ruling (test/port-rulings.golden)' % entry['id']
                   for entry in json.loads((ROOT / 'test/port-rulings.golden').read_text())['open_findings'] if entry['status'] != 'ruled']
        code, out = run(lambda root: None)
        self.assertEqual([line for line in out.splitlines() if line.startswith('BLOCKED: ')], unruled, out)
        self.assertEqual(code, int(bool(unruled)), out)
        # With every finding ruled it passes: each line asserted below is then the only thing in the way.
        settle = lambda record: [entry.update(status='ruled', ruling='D3') for entry in record['open_findings']]
        code, out = run(rulings(settle))
        self.assertEqual((code, [line for line in out.splitlines() if line.startswith('BLOCKED: ')]), (0, []), out)
        self.assertTrue(any(line.startswith('PASS port evidence') for line in out.splitlines()), out)
        # Outside a repository git has no answer; the check says so and does not pass in silence.
        self.assertIn('NOTE: could not ask git which commits are later than the reviewed one', out)
        d3 = lambda record: next(entry for entry in record['rulings'] if entry['id'] == 'D3')
        wrong = [
            ('the delta review is pending', rulings(lambda record: record['delta_review'].update(status='pending'))),
            ('invalid/stale port evidence', rulings(lambda record: record['delta_review'].update(commit='a198042'))),
            ('open finding F1 has no ruling', rulings(lambda record: record['open_findings'][0].update(status='not ruled'))),
            # F1 is closed by D3 and the D3 entries cite it: without the ruling the record contradicts itself.
            ('invalid/stale port evidence', rulings(lambda record: record['rulings'].remove(d3(record)))),
            ('deliberate divergences from the oracle without a ruling: tar-metadata.longname.states-2-to-the-62', rulings(lambda record: d3(record).update(subject='note'))),
            ('the runtime the stepwise gzip reader is tied to has no ruling', rulings(lambda record: record['rulings'].remove(next(entry for entry in record['rulings'] if entry['id'] == 'B9')))),
            ('the pinned Node (24.15.0, oracle/versions.yml) is not what the stepwise gzip reader is verified on', replace('oracle/versions.yml', '    version: 24.14.0\n', '    version: 24.15.0\n')),
            ('the pinned Node (24.14.0, oracle/versions.yml) is not what the stepwise gzip reader is verified on (22.23.3)', replace('src/pylike/tarfile.ts', "['22.23.3', '24.14.0']", "['22.23.3']")),
            ('the vectors at the metadata limit do not show both of its sides in both engines', large(lambda cases: cases['metadata.pax.above-limit'].update(classification='equal'))),
            ('the vectors at the metadata limit do not show both of its sides in both engines', large(lambda cases: cases['metadata.longname.at-limit'].update(port=cases['metadata.longname.above-limit']['port']))),
            ('the vectors at the metadata limit do not show both of its sides in both engines', replace('src/pylike/tarfile.ts', 'export const METADATA_LIMIT = 2n ** 28n;', 'export const METADATA_LIMIT = 2n ** 29n;')),
            ('the 4 GiB archive vectors do not show a valid archive', large(lambda cases: cases['large.valid'].update(classification='D1'))),
        ]
        for message, change in wrong:
            # On a copy where every finding is ruled, so that the defect is the only thing that can block.
            code, out = run(settled(change))
            self.assertEqual(code, 1, message)
            blocked = [line for line in out.splitlines() if line.startswith('BLOCKED: ')]
            self.assertEqual(len(blocked), 1, out)
            self.assertTrue(blocked[0].startswith('BLOCKED: ' + message), out)
            self.assertNotIn('PASS port evidence', out, message)

    def test_archives_of_4_gib_are_recorded_as_read_not_as_divergent(self):
        large = json.loads((ROOT / 'test/vectors/port-large.golden').read_text())
        self.assertGreaterEqual(large['decompressed_bytes'], 1 << 32)
        cases = {case['id']: case for case in large['cases']}
        self.assertEqual(cases['large.valid']['classification'], 'equal')
        self.assertEqual(cases['large.valid']['oracle'], cases['large.valid']['port'])
        self.assertIn('result', cases['large.valid']['port'])
        self.assertEqual((cases['large.crc-trailer']['full_gzip_read'], cases['large.crc-trailer']['classification']), ('BadGzipFile', 'D1'))

    def test_the_metadata_limit_is_recorded_with_both_of_its_sides_in_both_engines(self):
        large = json.loads((ROOT / 'test/vectors/port-large.golden').read_text())
        self.assertEqual(large['metadata_limit_bytes'], 2 ** 28)
        self.assertIn('export const METADATA_LIMIT = 2n ** 28n;', (ROOT / 'src/pylike/tarfile.ts').read_text())
        cases = {case['id']: case for case in large['cases'] if case['id'].startswith('metadata.')}
        self.assertEqual(sorted(cases), ['metadata.longname.above-limit', 'metadata.longname.above-limit.cut', 'metadata.longname.at-limit', 'metadata.pax.above-limit', 'metadata.pax.at-limit'])
        accepted = {'result': {'$mapping': [['name', 'example-package'], ['version', '1.0.0']]}}
        for kind in ('longname', 'pax'):
            at, above = cases['metadata.%s.at-limit' % kind], cases['metadata.%s.above-limit' % kind]
            # Both are valid containers the oracle accepts. Only the one above the limit reads a block above it, and only that one the port refuses.
            self.assertEqual((at['full_gzip_read'], at['reads_metadata_above_limit'], at['classification'], at['oracle'], at['port']), ('valid', False, 'equal', accepted, accepted), kind)
            self.assertEqual((above['full_gzip_read'], above['reads_metadata_above_limit'], above['classification'], above['oracle']), ('valid', True, 'D3', accepted), kind)
            self.assertEqual((above['port']['message'], dict(above['port']['public']['$mapping'])['error_code']), ('unreadable archive or manifest', 'E_ADAPTER_INPUT'), kind)
        cut = cases['metadata.longname.above-limit.cut']
        self.assertEqual((cut['reads_metadata_above_limit'], cut['classification'], cut['oracle']['message']), (True, 'equal', 'unreadable archive or manifest'))


if __name__ == '__main__':
    unittest.main()
