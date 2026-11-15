"""scripts/rehearse.mjs: a deployment installed and not started, through contract v1, on a sandbox of the deploy corpus.

The case is the public `example` with `activation: install_only`. Cook is the corpus stand-in, started through
scripts/rehearse/cook-keep; the service manager is the oracle's fake behind scripts/rehearse/systemctl-readonly. What is
shown is the runner: its log, its refusals, what it lets the engine start, and what it keeps of cook's lock.
"""
import copy, hashlib, json, os, shutil, subprocess, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / 'test/corpus'), str(ROOT / 'test/differential')]
import corpus
import ts_port

SCRATCH = ROOT / '.cache/rehearse-test'
KEEP = ROOT / 'scripts/rehearse/cook-keep'
GUARD = ROOT / 'scripts/rehearse/systemctl-readonly'
NODE = os.environ.get('KITCHEN_NODE') or shutil.which('node')


class Rehearsal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ts_port.initialize()
        for name in ('connectome_deploy', 'connectome_cook', 'kitchen_v1'):
            __import__(name)
        ts_port.remove(SCRATCH)
        SCRATCH.mkdir(parents=True)
        case = next(case for case in corpus.cases(corpus.located('synthetic', SCRATCH)) if case.name == 'example')
        cls.case = case

    @classmethod
    def tearDownClass(cls):
        ts_port.remove(SCRATCH)

    environment = {}

    def world(self, label, activation='install_only'):
        case = copy.copy(self.case)
        case.data = copy.deepcopy(self.case.data)
        case.data['layers']['agent']['activation'] = activation
        world = corpus.World(case, SCRATCH / label)
        world.build()
        sandbox = world.sandbox
        fake = sandbox / 'fake-systemctl'
        corpus.write(fake, '#!/bin/sh\nexec %s %s %s "$@"\n' % tuple(world.systemctl), 0o755)
        corpus.write(sandbox / 'bin/systemd-analyze', '#!/bin/sh\necho "$*" >>%s\n' % (sandbox / 'analyze-calls'), 0o755)
        secrets = Path(world.effective['secrets_file'])
        template = sandbox / 'secrets-template.json'
        template.write_bytes(secrets.read_bytes())
        secrets.unlink()  # the runner writes it
        request = dict(copy.deepcopy(world.request))
        del request['systemctl']
        request['engines']['cook']['runtime'] = str(KEEP)
        recipe = world.raw_layers['agent']['recipe_file']['path']
        self.input = sandbox / 'rehearsal.json'
        self.document = {'schema': 'connectome.kitchen-rehearsal/v1', 'request': request, 'systemctl': [str(fake), '--user'], 'secrets_template': str(template),
                         'files': {recipe: hashlib.sha256(Path(recipe).read_bytes()).hexdigest()}, 'expect': {'home': str(world.home)}}
        self.input.write_text(json.dumps(self.document))
        self.keep = world.home / 'kitchen-rehearsal' / world.effective['name']
        return world

    def run_mode(self, world, mode):
        env = {'HOME': str(world.home), 'PATH': '%s:/usr/bin:/bin' % (world.sandbox / 'bin'), 'LANG': 'C.UTF-8', 'XDG_RUNTIME_DIR': str(world.sandbox)}
        env.update(self.environment)
        done = subprocess.run([NODE, str(ROOT / 'test/corpus/rehearse_driver.mjs'), mode, str(self.input), str(world.sandbox), world.account],
                              env=env, capture_output=True, text=True, timeout=300)
        log = [json.loads(line) for line in (self.keep / 'rehearsal.log').read_text().splitlines()] if (self.keep / 'rehearsal.log').is_file() else []
        return done, log

    def receipts(self, kind):
        """The receipts of one mode and command, oldest first: `<NNN>-<mode>-<command>.receipt.json`."""
        return [json.loads(path.read_text()) for path in sorted(self.keep.glob('[0-9][0-9][0-9]-%s.receipt.json' % kind))]

    def test_check_then_apply_twice_then_verify(self):
        world = self.world('run')
        root, unit_dir = Path(world.root), Path(world.unit_dir)
        done, log = self.run_mode(world, 'check')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(root.exists())  # the engine wrote nothing: the fake service manager's track file is beside the root, not in it
        self.assertEqual(Path(world.effective['secrets_file']).stat().st_mode & 0o777, 0o600)
        self.assertEqual([entry['step'] for entry in log if entry['step'].startswith('plan')], ['plan asked', 'plan answered'])
        self.assertEqual(sorted(path.name for path in self.keep.glob('*.receipt.json')), ['001-check-plan.receipt.json'])
        plan = self.receipts('check-plan')[0]
        self.assertEqual(plan['core']['prepare']['plan']['activation'], 'install_only')
        request = Path(plan['core']['prepare']['build_dir']).name

        self.document['expect']['request_id'] = request
        self.input.write_text(json.dumps(self.document))
        done, log = self.run_mode(world, 'apply')
        self.assertEqual(done.returncode, 0, done.stderr + json.dumps([entry for entry in log if entry['run'] == 'apply' and entry['step'] in ('apply answered', 'tool failed', 'tool refused', 'engine failure that is not a refusal', 'end')]))
        receipt = self.receipts('apply-apply')[0]['core']
        self.assertEqual((receipt['changed'], receipt['activate']['activation'], receipt['activate']['unit_state']), (True, 'install_only', 'inactive'))
        self.assertEqual((root / 'state/activation').read_text(), 'install_only\n')
        self.assertTrue((unit_dir / 'connectome-example.service').is_symlink())
        self.assertFalse((unit_dir / 'default.target.wants').exists())
        # What reached the service manager, and what the engine was let start.
        verbs = {next(word for word in json.loads(line) if not word.startswith('-')) for name in ('systemctl-calls', '../preflight-systemctl-calls')
                 for line in (root / name).read_text().splitlines()}
        self.assertEqual(verbs, {'is-active', 'show', 'daemon-reload'})
        kinds = [entry['kind'] for entry in log if entry['step'] == 'tool started' and entry['run'] == 'apply']
        self.assertEqual(kinds, ['adapter', 'daemon-reload', 'unit verifier'])
        self.assertIn('--user verify', (world.sandbox / 'analyze-calls').read_text())
        # Cook's lock as cook wrote it is kept, and it is not the lock the adapter left.
        kept = next(entry for entry in log if entry['step'] == 'cook lock as written' and entry['run'] == 'apply')
        cooked = Path(kept['kept'])
        build = root / 'builds' / request
        self.assertNotIn('provenance', cooked.read_text())
        self.assertIn('provenance', (build / 'connectome.lock').read_text())
        self.assertTrue(kept['same_content_but_for_the_provenance_the_adapter_adds'])
        self.assertEqual(len(list(self.keep.glob('cook-*.stdout'))), 1)
        copied = self.keep / ('build-' + request[:8])
        for line in (copied / 'SHA256SUMS').read_text().splitlines():
            digest, name = line.split('  ')
            self.assertEqual(hashlib.sha256((copied / name).read_bytes()).hexdigest(), digest)
            self.assertEqual((copied / name).read_bytes(), (build / name).read_bytes())
        self.assertLessEqual({'connectome.lock', 'deployment.json', 'cook-recipe.json', '.prepared', 'run.sh', 'connectome-example.service'},
                             {path.name for path in copied.iterdir()})

        done, log = self.run_mode(world, 'apply')
        self.assertEqual(done.returncode, 0, done.stderr)
        # Each call left a receipt of its own: the second apply's says nothing changed, and the first apply's is still there.
        self.assertEqual([entry['core']['changed'] for entry in self.receipts('apply-apply')], [True, False])
        self.assertEqual(self.receipts('apply-apply')[0]['core'], receipt)
        self.assertEqual([path.name for path in sorted(self.keep.glob('*.receipt.json'))], [
            '001-check-plan.receipt.json', '002-apply-plan.receipt.json', '003-apply-apply.receipt.json', '004-apply-plan.receipt.json', '005-apply-apply.receipt.json'])
        self.assertEqual([entry['receipt'] for entry in log if entry['step'] == 'apply answered'], [str(self.keep / name) for name in ('003-apply-apply.receipt.json', '005-apply-apply.receipt.json')])
        self.assertEqual(len(list(self.keep.glob('cook-*.stdout'))), 1)  # cook did not run again
        done, log = self.run_mode(world, 'verify')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(self.receipts('verify-plan')[0]['core']['changed'])
        self.assertEqual(len(list(self.keep.glob('*.receipt.json'))), 6)
        self.assertEqual(log[-1]['step'], 'end')

    def test_refuses_a_deployment_that_would_start(self):
        world = self.world('start', activation='start')
        done, log = self.run_mode(world, 'apply')
        self.assertEqual(done.returncode, 3)
        self.assertFalse(self.keep.exists() or Path(world.root).exists() or Path(world.effective['secrets_file']).exists())

    def test_refuses_another_runtime_no_user_manager_a_foreign_secrets_file_and_a_moved_bundle(self):
        world = self.world('refusals')
        self.document['request']['engines']['cook']['runtime'] = self.document['request']['engines']['cook']['bun']
        self.input.write_text(json.dumps(self.document))
        self.assertEqual(self.run_mode(world, 'check')[0].returncode, 3)
        world = self.world('refusals')
        self.environment = {'XDG_RUNTIME_DIR': str(world.sandbox / 'absent')}
        done, log = self.run_mode(world, 'check')
        self.assertEqual((done.returncode, self.keep.exists()), (3, False))
        self.environment = {}
        world = self.world('refusals')
        secrets = Path(world.effective['secrets_file'])
        corpus.write(secrets, '{"EXAMPLE_API_KEY": "a value of the host"}', 0o600, world.home)
        done, log = self.run_mode(world, 'check')
        self.assertEqual((done.returncode, secrets.read_text()), (3, '{"EXAMPLE_API_KEY": "a value of the host"}'))
        self.assertIn('left alone', log[-1]['why'])
        world = self.world('refusals')
        self.document['expect']['request_id'] = '0' * 64
        self.input.write_text(json.dumps(self.document))
        done, log = self.run_mode(world, 'apply')
        self.assertEqual(done.returncode, 3)
        self.assertFalse(Path(world.root).exists())

    def test_the_guard_passes_questions_and_a_reload_only(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        log, called = SCRATCH / 'guard.log', SCRATCH / 'guard.called'
        real = SCRATCH / 'guard-real'
        corpus.write(real, '#!/bin/sh\necho "$*" >>%s\n' % called, 0o755)
        for path in (log, called):
            path.unlink(missing_ok=True)
        status = lambda *args: subprocess.run([str(GUARD), str(log), str(real), *args]).returncode
        for args in (['--user', 'is-active', 'a.service'], ['--user', 'show', '-p', 'ActiveState', '--value', 'a.service'], ['--user', 'daemon-reload']):
            self.assertEqual(status(*args), 0)
        for verb in ('start', 'stop', 'restart', 'enable', 'disable', 'reload-or-restart', 'try-restart', 'mask', 'kill', 'isolate', 'edit', 'link', 'set-property'):
            self.assertEqual(status('--user', verb, 'a.service'), 97)
        self.assertEqual(status('--user'), 97)
        self.assertEqual(len(called.read_text().splitlines()), 3)
        self.assertEqual(len(log.read_text().splitlines()), 17)


if __name__ == '__main__':
    unittest.main()
