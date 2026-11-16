"""test/corpus/corpus.py: what the corpus sets for itself and does not take from its caller, and the one known difference
it holds in a call of cook."""
import json, os, shutil, subprocess, sys, unittest
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / 'test/corpus'), str(ROOT / 'test/differential')]
import corpus

SCRATCH = ROOT / '.cache/corpus-environment-test'


class CorpusEnvironment(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)
        SCRATCH.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(SCRATCH, ignore_errors=True)

    def test_the_names_cook_is_handed_do_not_follow_the_caller(self):
        for caller in ({'LC_ALL': 'C', 'TMPDIR': '/tmp', 'LANG': 'en_US.UTF-8', 'PATH': '/usr/bin', 'HOME': '/somewhere'}, {}):
            with patch.dict(os.environ, caller, clear=True):
                corpus.fixture_environment()
                # The five names the adapter passes on (cook.py:23), as a record would name them.
                self.assertEqual(sorted(set(os.environ) & {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR'}), ['HOME', 'LANG', 'PATH'], caller)
                self.assertEqual(os.environ['LANG'], 'C.UTF-8')
                self.assertEqual((os.environ['PATH'], os.environ['HOME']) == ('/usr/bin', '/somewhere'), bool(caller))

    def test_a_bare_node_is_the_one_on_the_search_path_and_a_path_stays(self):
        self.assertEqual(corpus.found_node('node'), shutil.which('node'))
        self.assertEqual(corpus.found_node('/opt/elsewhere/node'), '/opt/elsewhere/node')
        self.assertEqual(corpus.found_node('./node'), './node')
        with self.assertRaises(corpus.CorpusError):
            corpus.found_node('no-such-node-on-the-search-path')
        # The documented call without --node gets as far as the cases (it stopped before them: <repo>/node).
        done = subprocess.run([sys.executable, str(ROOT / 'test/corpus/corpus.py'), 'compare', '--corpus', 'synthetic', '--steps-only', '--case', 'example', '--scratch', str(SCRATCH / 'compare')],
                              cwd=ROOT, capture_output=True, text=True, timeout=600, env={**os.environ, 'LC_ALL': 'C.UTF-8', 'TMPDIR': '/tmp'})
        self.assertEqual((done.returncode, done.stdout.startswith('PASS example: ')), (0, True), done.stdout + done.stderr)
        self.assertIn('golden reproduced', done.stdout)

    def test_a_cook_call_of_the_port_must_name_the_cache_of_the_builds_own(self):
        (SCRATCH / 'port').mkdir()
        log = SCRATCH / 'port' / 'cook-calls.jsonl'
        call = {'argv': ['runtime'], 'cwd': '/', 'environment_names': ['BUN_INSTALL_CACHE_DIR', 'COOK_PASS_ENV', 'HOME', 'LANG', 'PATH']}
        log.write_text(json.dumps(call, sort_keys=True) + '\n')
        self.assertEqual(corpus.cook_calls(log, False), [call])  # the oracle's: as written
        self.assertEqual(corpus.cook_calls(log, True), [dict(call, environment_names=['HOME', 'LANG', 'PATH'])])
        oracle = SCRATCH / 'oracle' / 'cook-calls.jsonl'
        oracle.parent.mkdir()
        oracle.write_text(json.dumps(dict(call, environment_names=['HOME', 'LANG', 'PATH']), sort_keys=True) + '\n')
        self.assertEqual(corpus.snapshot(log.parent, port=True)[str(log)], corpus.snapshot(oracle.parent)[str(oracle)])
        # Git's configuration names, where a recipe names a local source repository: held apart likewise, and whole for the record of the contract.
        git = dict(call, environment_names=['BUN_INSTALL_CACHE_DIR', 'COOK_PASS_ENV', 'GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0', 'GIT_DIR', 'HOME'])
        log.write_text(json.dumps(git, sort_keys=True) + '\n')
        self.assertEqual(corpus.cook_calls(log, True), [dict(call, environment_names=['GIT_DIR', 'HOME'])])  # no other name of Git's is the port's to add
        self.assertEqual(corpus.cook_calls(log, True, whole=True), [git])
        for names in (['COOK_PASS_ENV', 'HOME'], ['BUN_INSTALL_CACHE_DIR', 'HOME'], ['HOME', 'LANG', 'PATH']):
            log.write_text(json.dumps(dict(call, environment_names=names)) + '\n')
            with self.assertRaises(corpus.CorpusError):
                corpus.cook_calls(log, True)
            with self.assertRaises(corpus.CorpusError):
                corpus.snapshot(log.parent, port=True)


if __name__ == '__main__':
    unittest.main()
