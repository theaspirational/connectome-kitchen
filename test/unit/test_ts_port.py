"""The TS port of errors, lock and inputs: committed evidence is current and complete."""
import json, re, subprocess, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / 'test/differential'), str(ROOT / 'test/mutants')]
import ts_mutants
import ts_port


class Port(unittest.TestCase):
    def test_committed_corpus_is_what_the_oracle_answers_now(self):
        rows, trees = ts_port.port_text()
        self.assertEqual(ts_port.PORT.read_text(), rows)
        self.assertEqual(ts_port.PORT_TREES.read_text(), trees)
        outcomes = [json.loads(line)['outcome'] for line in rows.splitlines()]
        self.assertGreater(len(outcomes), 1300)
        # Acceptances and refusals are both present in bulk; a corpus of refusals alone proves little.
        self.assertGreater(sum('result' in o for o in outcomes), 600)
        self.assertGreater(sum('exception' in o for o in outcomes), 500)

    def test_every_oracle_mutant_of_lock_and_inputs_has_a_ts_mutant_with_the_same_id(self):
        anchors = json.loads(ts_mutants.ANCHORS.read_text())
        self.assertEqual(anchors, ts_mutants.build_anchors())
        ids = {anchor['id'] for anchor in anchors}
        guards = {a['id'] for a in json.loads((ROOT / 'test/mutants/guard-anchors.golden').read_text()) if a['id'].split('.')[0] in ('lock', 'inputs')}
        self.assertEqual(len(guards), 84)
        self.assertLessEqual(guards, ids)
        named = set()
        for name in ('named-guard-anchors.golden', 'named-site-anchors.golden'):
            named |= {a['id'] for a in json.loads((ROOT / 'test/mutants' / name).read_text()) if re.search(r'connectome_(lock|inputs)\.py$', a['file'])}
        self.assertEqual(len(named), 59)
        self.assertLessEqual(named, ids)
        # The names of the phase-1 list that are anchored in these modules: all of them, none invented.
        names = {entry['id'] for entry in json.loads((ROOT / 'test/mutants/named-checks.golden').read_text())
                 if any(re.search(r'connectome_(lock|inputs|errors)\.py$', anchor['file']) for anchor in entry['anchors'])}
        self.assertLessEqual({n for n in names if n.startswith(('lock.', 'inputs.'))}, {i.split('#')[0] for i in ids})
        # Rows 1, 3 and 28 have no oracle mutant; they must not be left without a TS one.
        for prefix in ('lock.credential.', 'lock.repository.', 'errors.closed.'):
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
                self.assertTrue(result['declared'] or result['named_fake_test'] == 'fails', result['id'])
                self.assertNotIn(result['id'], unkilled)
            else:
                # Not waived here: listed with a written analysis for the reviewer.
                self.assertIn(result['status'], ('survived', 'compiler'), result['id'])
                self.assertEqual(unkilled[result['id']]['status'], result['status'], result['id'])
                self.assertGreater(len(unkilled[result['id']]['analysis']), 200)
        self.assertEqual(set(unkilled), {r['id'] for r in results['results'] if r['status'] != 'killed'})
        self.assertEqual(len({entry['analysis'] for entry in unkilled.values()}), len(unkilled))

    def test_porting_map_is_current_and_marks_every_row_of_the_three_modules(self):
        self.assertEqual(subprocess.run([sys.executable, str(ROOT / 'scripts/porting-map.py'), '--check'], capture_output=True).returncode, 0)
        text = (ROOT / 'PORTING.md').read_text()
        self.assertIn('Rows with at least one ported site: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 23, 28, 49, 59, 63.', text)
        for line in text.splitlines():
            site = re.match(r'\| (\d+) \| `[^`]+` \| ported[^|]*\| `(src/\w+\.ts):(\d+)` \| `(lock|inputs|errors)\.py:(\d+)', line)
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

    def test_archive_is_a_documented_stub_and_nothing_else_is_ported(self):
        stub = (ROOT / 'src/archive.ts').read_text()
        self.assertTrue(stub.startswith('/**\n * STUB of one seam.'))
        self.assertEqual(sorted(re.findall(r'^export function (\w+)', stub, re.M)), ['bind_seam', 'check_dependencies', 'npm_name', 'validate_dependency_record'])
        ported = sorted(path.name for path in (ROOT / 'src').glob('*.ts'))
        self.assertEqual(ported, ['archive.ts', 'cli.ts', 'contract.ts', 'errors.ts', 'index.ts', 'inputs.ts', 'lock.ts'])
        lock = (ROOT / 'src/lock.ts').read_text()
        self.assertNotRegex(lock, r"(?m)^import (?!type )[^;]*from '\./(inputs|archive)\.js'")
        self.assertEqual(len(re.findall(r"lazy\('\./inputs\.js'\) as typeof Inputs; // lock\.py:149-152", lock)), 1)
        self.assertEqual(len(re.findall(r"lazy\('\./archive\.js'\) as typeof Archive; // lock\.py:(255-258|281-284)", lock)), 2)


if __name__ == '__main__':
    unittest.main()
