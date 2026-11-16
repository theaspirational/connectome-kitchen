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
        # deploy, cook, their adapter entry and the contract dispatch are a success-path port (docs/port-notes.md): present, and not in the porting map.
        # isolation is no port: the oracle has no counterpart (a build's own Bun cache; "Where the port knowingly differs" in the notes).
        self.assertEqual(ported, ['archive.ts', 'cli.ts', 'config.ts', 'contract.ts', 'cook-adapter.ts', 'cook.ts', 'deploy.ts', 'engine.ts', 'errors.ts', 'index.ts', 'inputs.ts', 'isolation.ts', 'lock.ts'])
        self.assertNotIn('isolation', porting_text := (ROOT / 'PORTING.md').read_text())
        porting = porting_text
        for module in ('connectome_deploy', 'connectome_cook'):
            self.assertNotIn('| ported | `%s.' % module, porting)
            self.assertIn('| oracle-only | `%s.' % module, porting)
        self.assertNotIn('bind_seam', (ROOT / 'src/archive.ts').read_text())
        lock = (ROOT / 'src/lock.ts').read_text()
        self.assertNotRegex(lock, r"(?m)^import (?!type )[^;]*from '\./(inputs|archive)\.js'")
        self.assertEqual(len(re.findall(r"lazy\('\./inputs\.js'\) as typeof Inputs; // lock\.py:149-152", lock)), 1)
        self.assertEqual(len(re.findall(r"lazy\('\./archive\.js'\) as typeof Archive; // lock\.py:(255-258|281-284)", lock)), 2)

    def test_deliberate_divergences_are_named_and_still_hold_on_the_oracle(self):
        entries = json.loads((ROOT / 'test/vectors/port-divergences.golden').read_text())
        self.assertEqual(len({entry['id'] for entry in entries}), len(entries))
        self.assertEqual({entry['divergence'] for entry in entries}, {'D1', 'D2', 'D3', 'D4'})
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
            if entry['divergence'] in ('D1', 'D3', 'D4'):
                self.assertEqual((entry['port']['message'], dict(entry['port']['public']['$mapping'])['error_code']), ('unreadable archive or manifest', 'E_ADAPTER_INPUT'), entry['id'])
                if entry['divergence'] == 'D4':
                    # A budget refuses archives the oracle reads; the ones that meet every budget from three sides are in port-budget.golden.
                    self.assertEqual(entry['oracle'], {'result': {'$mapping': [['name', 'example-package'], ['version', '1.0.0']]}}, entry['id'])
                if entry['divergence'] == 'D3':
                    # The small D3 entries state a size no machine allocates; the archives that hold a block at the limit are in port-large.golden.
                    self.assertEqual(entry['oracle'], {'crash': 'MemoryError'}, entry['id'])
            else:
                self.assertEqual(entry['port'], {'crash': 'TypeError'}, entry['id'])
                self.assertIn({'$float': 'nan'}, entry['oracle']['result'], entry['id'])

    def test_rulings_are_recorded_as_received_and_nothing_unkilled_counts_as_killed(self):
        record = json.loads((ROOT / 'test/port-rulings.golden').read_text())
        rulings = {entry['id']: entry for entry in record['rulings']}
        self.assertEqual(sorted(rulings), ['B9', 'C1', 'D1', 'D2', 'D3', 'D4', 'D5', 'D6', 'L1', 'M1', 'O1', 'R6'])
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
        for ident in ('D3', 'D4', 'D5', 'D6', 'B9', 'L1'):
            self.assertGreater(len(rulings[ident]['left_to_the_implementer']), 20, ident)
        # D5, and D6 which raised the number: the contract name the later ruling records is the one the adapter states, and a record of
        # the adapter's output is kept under it. The earlier name stays in D5 as received, with its record.
        name = re.search(r"^export const ADAPTER_CONTRACT = '([^']+)';$", (ROOT / 'src/cook.ts').read_text(), re.M)[1]
        self.assertIn('`%s`' % name, rulings['D6']['left_to_the_implementer'])
        self.assertIn('`%s`' % name, rulings['D6']['as_received'])
        self.assertIn('`kitchen-cook-adapter/1`', rulings['D5']['left_to_the_implementer'])
        self.assertTrue((ROOT / 'test/corpus/adapter-contract/kitchen-cook-adapter-1.golden').is_file())
        self.assertEqual(json.loads((ROOT / 'test/corpus/adapter-contract' / (name.replace('/', '-') + '.golden')).read_text())['contract'], name)
        # The witness a ruling moved to a later module is an open obligation with a trigger, not a note.
        owed = {entry['id']: entry for entry in record['obligations']}
        self.assertEqual(owed['archive.L140.4']['due_when_any_exists'], ['src/cook.ts', 'src/deploy.ts'])
        # It came due with the deploy and cook port. A discharged obligation names its witness, and the witness is there under that name.
        if owed['archive.L140.4']['status'] == 'discharged':
            witness = owed['archive.L140.4']['discharged_with']
            self.assertIn("test('" + witness['name'] + "'", (ROOT / witness['test']).read_text())
            self.assertGreater(len(owed['archive.L140.4']['discharged_by']), 40)
        else:
            self.assertFalse(any((ROOT / name).exists() for name in owed['archive.L140.4']['due_when_any_exists']))
        # The trusted, digest-checked input is a precondition on the caller, not a property that was proven: it comes due with the catalog.
        self.assertEqual((owed['archive.trusted-input']['ruling'], owed['archive.trusted-input']['status'], owed['archive.trusted-input']['due_when_any_exists']), ('C1', 'open', ['src/catalog.ts']))
        self.assertIn('not a proven property', rulings['C1']['as_received'])
        backlog = {entry['id'] for entry in record['backlog']}
        for item in rulings['O1']['items']:
            self.assertGreater(len(item['bound']), 60, item['id'])
            self.assertIn(item['backlog'], backlog, item['id'])
        r6 = json.loads((ROOT / 'test/vectors/r6-combine.golden').read_text())
        self.assertEqual(sorted(entry['id'] for entry in r6['known_mismatches_of_the_combine_helper']), sorted(rulings['R6']['known_mismatches_of_the_combine_helper']))
        self.assertEqual(r6['vectors']['connectome_config.merge_layers'].get('mismatch', 0), 0)

    def test_the_readiness_check_is_red_while_a_review_a_ruling_or_an_approval_is_owed(self):
        record = json.loads((ROOT / 'test/port-rulings.golden').read_text())
        done = subprocess.run([sys.executable, str(ROOT / 'scripts/check-port-ready')], capture_output=True, text=True)
        lines = done.stdout.splitlines()
        self.assertEqual(lines[0], '262 of 270 TS mutants killed')
        reader = json.loads((ROOT / 'test/mutants/reader-results.golden').read_text())
        self.assertEqual(lines[1], '%d of %d reader mutants killed' % (reader['mutants'], reader['mutants']))
        self.assertGreaterEqual(reader['mutants'], 64)
        self.assertIn('RULED: 8 TS mutants are not kills and are accepted as such by ruling M1: 5 redundant survivors, 2 compiler, 1 crash-only', lines)
        review = record['delta_review']
        blocked = [line for line in lines if line.startswith('BLOCKED: ')]
        # What the record itself owes, line for line.
        owed = ['BLOCKED: open finding %s is not closed: %s (test/port-rulings.golden)' % (entry['id'], entry['status']) for entry in record['open_findings'] if entry['status'] != 'ruled']
        defaults = next(entry for entry in record['rulings'] if entry['id'] == 'D4')['defaults']
        if defaults['status'] != 'approved':
            owed.append('BLOCKED: the budgets of D4 (metadataBytes 2**24, paxRecords 2**21, sparseNumbers 2**16, members 2**17) and the heap they are sized for (2048 MiB) are %s (test/port-rulings.golden, ruling D4)' % defaults['status'])
        for line in owed:
            self.assertIn(line, blocked, done.stdout)
        # Anything else that blocks is work no recorded review has read: commits after the reviewed one, or an edited tree.
        for line in blocked:
            if line not in owed:
                self.assertTrue(line.startswith('BLOCKED: no recorded review covers what was changed after %s outside docs/ and test/port-rulings.golden: ' % review['commit'][:7]), done.stdout)
        self.assertEqual(done.returncode, int(bool(blocked)), done.stdout)
        self.assertEqual(any(line.startswith('PASS port evidence') for line in lines), not blocked)
        # A passed review names the commit it read and its report, and says who recorded it on whose word.
        self.assertEqual(review['status'], 'passed')
        self.assertRegex(review['commit'], r'^[0-9a-f]{40}$')
        self.assertRegex(review['review_report_sha256'], r'^[0-9a-f]{64}$')
        self.assertIn('on this instruction of the manager of the dispatch', review['set_by'])
        self.assertIn('on this instruction of the technical owner', record['earlier_delta_reviews'][0]['set_by'])
        self.assertIn('RULED: the delta review of %s passed (%s; report sha256 %s)' % (review['commit'][:7], review['covers'], review['review_report_sha256']), lines)
        # F4 is ruled as D4 since its numbers were approved and its round reviewed. Ruled is not fixed: what D4 leaves is
        # recorded as residual risk of the trusted-input version, accepted by ruling C1, and the check prints it as that.
        f4 = next(entry for entry in record['open_findings'] if entry['id'] == 'F4')
        self.assertEqual((f4['status'], f4['ruling'], f4['residual_risk_accepted_by'], defaults['status']), ('ruled', 'D4', 'C1', 'approved'))
        self.assertGreaterEqual(len(f4['residual_risk']), 3)
        self.assertIn('not fixed', f4['ruled_not_fixed'])
        self.assertIn('RULED: finding F4 is ruled as D4 and not fixed: %d items of residual risk are accepted for trusted input by ruling C1 (test/port-rulings.golden)' % len(f4['residual_risk']), lines)
        self.assertNotIn('RULED: finding F4 is closed by ruling D4', lines)
        # The approval names its source, and each number says where it comes from.
        self.assertIn('The technical owner', defaults['approved_by'])
        self.assertEqual(len(defaults['where_each_number_comes_from']), 5)
        self.assertEqual((defaults['budgets'], defaults['heap_limit_required_mib']), ({'metadataBytes': 2 ** 24, 'paxRecords': 2 ** 21, 'sparseNumbers': 2 ** 16, 'members': 2 ** 17}, 2048))
        # The two LOW findings of the review that was recorded are backlog entries that say they are open.
        backlog = {entry['id']: entry['entry'] for entry in record['backlog']}
        for ident in ('B12', 'B14', 'B16', 'B17'):
            self.assertTrue(backlog[ident].startswith('Open'), ident)

    def test_the_readiness_check_blocks_on_each_thing_it_claims_to_hold(self):
        # The check, run on copies of the evidence with one thing wrong each: every one must turn it red, with its own line.
        import shutil, tempfile
        names = ['scripts/check-port-ready', 'scripts/porting-map.py', 'PORTING.md', 'test/port-rulings.golden', 'test/vectors/port-divergences.golden', 'test/vectors/port-large.golden',
                 'test/vectors/port-budget.golden', 'test/mutants/reader-results.golden', 'test/vectors/r6-combine.golden', 'test/mutants/ts-results.golden', 'test/mutants/ts-unkilled.golden', 'test/mutants/ts-reviewed.golden', 'oracle/versions.yml']

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
        def budgets(edit):
            def change(root):
                record = json.loads((root / 'test/vectors/port-budget.golden').read_text())
                dropped = edit({case['id']: case for case in record['cases']})
                record['cases'] = [case for case in record['cases'] if case['id'] != dropped]
                (root / 'test/vectors/port-budget.golden').write_text(json.dumps(record))
            return change

        def mutants(edit):
            def change(root):
                record = json.loads((root / 'test/mutants/reader-results.golden').read_text())
                edit(record)
                (root / 'test/mutants/reader-results.golden').write_text(json.dumps(record))
            return change
        d4 = lambda record: next(entry for entry in record['rulings'] if entry['id'] == 'D4')
        # Unchanged, the copy blocks on exactly what the record owes, as the tree does: the open finding and the unapproved numbers.
        original = json.loads((ROOT / 'test/port-rulings.golden').read_text())
        unruled = ['open finding %s is not closed: ' % entry['id'] for entry in original['open_findings'] if entry['status'] != 'ruled']
        unapproved = ['the budgets of D4 ('] if d4(original)['defaults']['status'] != 'approved' else []
        code, out = run(lambda root: None)
        blocked = [line[len('BLOCKED: '):] for line in out.splitlines() if line.startswith('BLOCKED: ')]
        self.assertEqual(len(blocked), len(unapproved + unruled), out)
        for line, start in zip(blocked, unapproved + unruled):
            self.assertTrue(line.startswith(start), out)
        self.assertEqual(code, int(bool(blocked)), out)
        # With every finding ruled and the numbers approved it passes: each line asserted below is then the only thing in the way.
        settle = lambda record: ([entry.update(status='ruled', ruling='D3') for entry in record['open_findings']], d4(record)['defaults'].update(status='approved'))
        code, out = run(rulings(settle))
        self.assertEqual((code, [line for line in out.splitlines() if line.startswith('BLOCKED: ')]), (0, []), out)
        self.assertTrue(any(line.startswith('PASS port evidence') for line in out.splitlines()), out)
        # Outside a repository git has no answer; the check says so and does not pass in silence.
        self.assertIn('NOTE: could not ask git which commits are later than the reviewed one', out)
        d3 = lambda record: next(entry for entry in record['rulings'] if entry['id'] == 'D3')
        wrong = [
            ('the delta review is pending', rulings(lambda record: record['delta_review'].update(status='pending'))),
            ('invalid/stale port evidence', rulings(lambda record: record['delta_review'].update(commit='a198042'))),
            ('open finding F1 is not closed: not ruled', rulings(lambda record: record['open_findings'][0].update(status='not ruled'))),
            # F1 is closed by D3 and the D3 entries cite it: without the ruling the record contradicts itself.
            ('invalid/stale port evidence', rulings(lambda record: record['rulings'].remove(d3(record)))),
            ('deliberate divergences from the oracle without a ruling: tar-metadata.longname.states-2-to-the-62', rulings(lambda record: d3(record).update(subject='note'))),
            ('the runtime the stepwise gzip reader is tied to has no ruling', rulings(lambda record: record['rulings'].remove(next(entry for entry in record['rulings'] if entry['id'] == 'B9')))),
            ('the pinned Node (24.15.0, oracle/versions.yml) is not what the stepwise gzip reader is verified on', replace('oracle/versions.yml', '    version: 24.14.0\n', '    version: 24.15.0\n')),
            ('the pinned Node (24.14.0, oracle/versions.yml) is not what the stepwise gzip reader is verified on (22.23.3)', replace('src/pylike/tarfile.ts', "['22.23.3', '24.14.0']", "['22.23.3']")),
            ('the vectors at the metadata limit do not show both of its sides in both engines', large(lambda cases: cases['metadata.pax.above-limit'].update(classification='equal'))),
            # At the limit the port refuses under the byte budget of D4, and lists only under a byte budget raised to the limit; above it the limit refuses either way.
            ('the vectors at the metadata limit do not show both of its sides in both engines', large(lambda cases: cases['metadata.longname.at-limit'].update(port=cases['metadata.longname.at-limit']['oracle']))),
            ('the vectors at the metadata limit do not show both of its sides in both engines', large(lambda cases: cases['metadata.pax.at-limit']['reader'].pop('budget'))),
            ('the vectors at the metadata limit do not show both of its sides in both engines', large(lambda cases: cases['metadata.pax.above-limit']['reader'].update(budget='metadataBytes'))),
            ('the vectors at the metadata limit do not show both of its sides in both engines', large(lambda cases: cases['metadata.longname.at-limit'].update(reader_under_a_byte_budget_of_the_limit=cases['metadata.longname.above-limit']['reader_under_a_byte_budget_of_the_limit']))),
            ('the vectors at the metadata limit do not show both of its sides in both engines', large(lambda cases: cases['metadata.pax.above-limit'].update(reader_under_a_byte_budget_of_the_limit={'members': 2}))),
            ('the vectors at the metadata limit do not show both of its sides in both engines', replace('src/pylike/tarfile.ts', 'export const METADATA_LIMIT = 2n ** 28n;', 'export const METADATA_LIMIT = 2n ** 29n;')),
            ('the 4 GiB archive vectors do not show a valid archive', large(lambda cases: cases['large.valid'].update(classification='D1'))),
            # D4: the ruling, the approval of its numbers, the numbers themselves, and the vectors on the sides of each budget.
            ('the admission budgets of the tar reader have no ruling', lambda root: (rulings(lambda record: record['rulings'].remove(d4(record)))(root),
                                                                              (root / 'test/vectors/port-divergences.golden').write_text(json.dumps([entry for entry in json.loads((root / 'test/vectors/port-divergences.golden').read_text()) if entry['divergence'] != 'D4'])))),
            ('deliberate divergences from the oracle without a ruling: tar-budget.sparse-numbers.map-of-one-number-more', rulings(lambda record: d4(record).update(subject='note'))),
            ('the budgets of D4 (metadataBytes 2**24, paxRecords 2**21, sparseNumbers 2**16, members 2**17) and the heap they are sized for (2048 MiB) are proposed, not approved', rulings(lambda record: d4(record)['defaults'].update(status='proposed, not approved'))),
            ('the budgets or the heap requirement of the reader are not the ones ruling D4 records', rulings(lambda record: d4(record)['defaults']['budgets'].update(members=2 ** 18))),
            ('the budgets or the heap requirement of the reader are not the ones ruling D4 records', rulings(lambda record: d4(record)['defaults'].update(heap_limit_required_mib=3072))),
            # A finding that is ruled with residual risk names the ruling that accepts the risk.
            ('finding F4 is ruled with residual risk that no recorded ruling accepts', rulings(lambda record: next(entry for entry in record['open_findings'] if entry['id'] == 'F4').update(residual_risk_accepted_by='C9'))),
            ('the vectors at the admission budgets do not show both sides of each of them in both engines', budgets(lambda cases: cases['members.above'].update(classification='equal'))),
            ('the vectors at the admission budgets do not show both sides of each of them in both engines', budgets(lambda cases: cases['sparse-global.above']['oracle_walk'].update(crossed=None))),
            ('the vectors at the admission budgets do not show both sides of each of them in both engines', budgets(lambda cases: cases['names.at']['oracle_walk']['used'].update(metadataBytes=2 ** 24 - 1))),
            ('the vectors at the admission budgets do not show both sides of each of them in both engines', budgets(lambda cases: cases['sparse-1.0.above']['reader'].update(budget='metadataBytes'))),
            ('the vectors at the admission budgets do not show both sides of each of them in both engines', budgets(lambda cases: cases['combined.all-below']['oracle_walk']['used'].update(members=2 ** 17 - 2))),
            ('the vectors at the admission budgets do not show both sides of each of them in both engines', budgets(lambda cases: 'records.below')),
            # The reader's mutants: each is killed, and the results are of the sources in the tree.
            ('reader mutants not killed: reader.d4.members.uncounted', mutants(lambda record: next(result for result in record['results'] if result['id'] == 'reader.d4.members.uncounted').update(status='survived'))),
            ('invalid/stale port evidence', mutants(lambda record: record['results'].pop())),
            ('the reader mutant results were taken from other sources than the ones in the tree', replace('src/archive.ts', 'const MANIFEST_NAME = {', 'const MANIFEST_NAME  = {')),
        ]
        # A change of the reader's source also makes the mutant results stale; that line is expected beside the one a case is about.
        stale = 'BLOCKED: the reader mutant results were taken from other sources than the ones in the tree'
        for message, change in wrong:
            # On a copy where every finding is ruled, so that the defect is the only thing that can block.
            code, out = run(settled(change))
            self.assertEqual(code, 1, message)
            blocked = [line for line in out.splitlines() if line.startswith('BLOCKED: ')]
            if not message.startswith(stale[len('BLOCKED: '):]):
                blocked = [line for line in blocked if not line.startswith(stale)]
            self.assertEqual(len(blocked), 1, out)
            self.assertTrue(blocked[0].startswith('BLOCKED: ' + message), out)
            self.assertNotIn('PASS port evidence', out, message)

    def test_the_readiness_check_blocks_on_a_later_commit_that_no_recorded_review_has_read(self):
        # In a repository of its own: a commit after the reviewed one may record a review and edit the notes, and nothing else.
        import shutil, tempfile
        names = ['scripts/check-port-ready', 'scripts/porting-map.py', 'PORTING.md', 'test/port-rulings.golden', 'test/vectors/port-divergences.golden', 'test/vectors/port-large.golden',
                 'test/vectors/port-budget.golden', 'test/mutants/reader-results.golden', 'test/vectors/r6-combine.golden', 'test/mutants/ts-results.golden', 'test/mutants/ts-unkilled.golden',
                 'test/mutants/ts-reviewed.golden', 'oracle/versions.yml']
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch).resolve()
            git = lambda *args: subprocess.run(['git', '-C', str(root), '-c', 'user.name=check', '-c', 'user.email=check@example.invalid', '-c', 'commit.gpgsign=false', *args], capture_output=True, text=True, check=True).stdout.strip()
            for name in names:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / name, root / name)
            for folder in ('src', 'oracle/module_utils', 'docs'):
                shutil.copytree(ROOT / folder, root / folder)
            git('init', '--quiet')
            git('add', '--all')
            git('commit', '--quiet', '-m', 'the reviewed commit')
            reviewed = git('rev-parse', 'HEAD')

            def check():
                done = subprocess.run([sys.executable, str(root / 'scripts/check-port-ready')], capture_output=True, text=True)
                return done.returncode, done.stdout, [line for line in done.stdout.splitlines() if line.startswith('BLOCKED: ')]

            def commit(message, edits):
                for name, text in edits.items():
                    (root / name).parent.mkdir(parents=True, exist_ok=True)
                    (root / name).write_text(text)
                git('add', '--all')
                git('commit', '--quiet', '-m', message)
            # The record of the review itself, with every finding settled: a commit that touches only the record. It passes.
            record = json.loads((root / 'test/port-rulings.golden').read_text())
            for entry in record['open_findings']:
                entry.update(status='ruled', ruling='D3')
            next(entry for entry in record['rulings'] if entry['id'] == 'D4')['defaults']['status'] = 'approved'
            record['delta_review']['commit'] = reviewed
            commit('record the review', {'test/port-rulings.golden': json.dumps(record)})
            code, out, blocked = check()
            self.assertEqual((code, blocked), (0, []), out)
            self.assertIn('NOTE: not covered by the recorded delta review: %s record the review' % git('rev-parse', '--short', 'HEAD'), out)
            self.assertIn('PASS port evidence', out)
            # So does a commit that edits the notes.
            commit('notes', {'docs/port-notes.md': (root / 'docs/port-notes.md').read_text() + '\nA note.\n'})
            self.assertEqual(check()[0], 0)
            # An edit that is not committed blocks, in the tree or new beside it; and no longer once it is undone.
            start = 'BLOCKED: no recorded review covers what was changed after %s outside docs/ and test/port-rulings.golden: ' % reviewed[:7]
            (root / 'test/unit').mkdir(parents=True)
            (root / 'test/unit/new.test.js').write_text('// new\n')
            code, out, blocked = check()
            self.assertEqual((code, blocked), (1, [start + 'uncommitted changes in the working tree']), out)
            (root / 'test/unit/new.test.js').unlink()
            (root / 'docs/more.md').write_text('more\n')
            self.assertEqual(check()[0], 0)
            # A commit that changes a source file blocks, and is named; one that changes a vector file as well.
            source = (root / 'src/errors.ts').read_text()
            commit('touch a source file', {'src/errors.ts': source + '\n// a later change\n', 'docs/more.md': 'more\n'})
            first = git('rev-parse', '--short', 'HEAD')
            code, out, blocked = check()
            self.assertEqual((code, blocked), (1, [start + first + ' touch a source file']), out)
            self.assertNotIn('PASS port evidence', out)
            commit('touch a vector file', {'test/vectors/port-large.golden': (root / 'test/vectors/port-large.golden').read_text() + '\n'})
            code, out, blocked = check()
            self.assertEqual((code, blocked), (1, [start + git('rev-parse', '--short', 'HEAD') + ' touch a vector file; ' + first + ' touch a source file']), out)

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
        self.assertEqual((large['metadata_limit_bytes'], large['metadata_budget_bytes']), (2 ** 28, 2 ** 24))
        self.assertIn('export const METADATA_LIMIT = 2n ** 28n;', (ROOT / 'src/pylike/tarfile.ts').read_text())
        self.assertIn('  metadataBytes: 2 ** 24,\n', (ROOT / 'src/pylike/tarfile.ts').read_text())
        # The helper differential judges its `tar` kind against the same production budgets the reader states.
        import port_primitives
        stated = re.search(r'^export const BUDGETS: Budgets = Object\.freeze\(\{\n((?:  .*\n)+)\}\);$', (ROOT / 'src/pylike/tarfile.ts').read_text(), re.M)[1]
        self.assertEqual(port_primitives.PRODUCTION_BUDGETS, {name: 2 ** int(power) for name, power in re.findall(r'^  (\w+): 2 \*\* (\d+),$', stated, re.M)})
        cases = {case['id']: case for case in large['cases'] if case['id'].startswith('metadata.')}
        self.assertEqual(sorted(cases), ['metadata.longname.above-limit', 'metadata.longname.above-limit.cut', 'metadata.longname.at-limit', 'metadata.pax.above-limit', 'metadata.pax.at-limit'])
        accepted = {'result': {'$mapping': [['name', 'example-package'], ['version', '1.0.0']]}}
        for kind in ('longname', 'pax'):
            at, above = cases['metadata.%s.at-limit' % kind], cases['metadata.%s.above-limit' % kind]
            # Both are valid containers the oracle accepts, and only the one above the limit reads a block above it. The port refuses both:
            # the limit lies above the byte budget of D4, so at the limit the reader's refusal is the budget's, and above it the limit's.
            self.assertEqual((at['full_gzip_read'], at['reads_metadata_above_limit'], at['classification'], at['oracle']), ('valid', False, 'D4', accepted), kind)
            self.assertEqual((above['full_gzip_read'], above['reads_metadata_above_limit'], above['classification'], above['oracle']), ('valid', True, 'D3', accepted), kind)
            for case in (at, above):
                self.assertEqual((case['port']['message'], dict(case['port']['public']['$mapping'])['error_code']), ('unreadable archive or manifest', 'E_ADAPTER_INPUT'), kind)
            self.assertEqual(at['reader'], {'error': 'OSError', 'message': 'tar metadata budget exceeded: metadata bytes', 'budget': 'metadataBytes'}, kind)
            self.assertEqual(above['reader'], {'error': 'OSError', 'message': 'tar metadata block above 268435456 bytes'}, kind)
            # D3's own two sides, with the byte budget raised to the limit: at it the reader lists what the oracle lists, above it the limit refuses.
            self.assertEqual(at['reader_under_a_byte_budget_of_the_limit'], {'members': at['oracle_lists_members']}, kind)
            self.assertEqual(above['reader_under_a_byte_budget_of_the_limit'], above['reader'], kind)
        cut = cases['metadata.longname.above-limit.cut']
        self.assertEqual((cut['reads_metadata_above_limit'], cut['classification'], cut['oracle']['message']), (True, 'equal', 'unreadable archive or manifest'))


if __name__ == '__main__':
    unittest.main()
