"""scripts/takeover.mjs: a running deployment the role made, taken over by a kitchen in two steps, on a sandbox of the deploy corpus.

The case is the public `example` (`activation: start`). The role's own apply through the Python engine makes the build
that runs; then the runner: `check`, `prepare`, `verify`, `start`, `verify`. Cook is the corpus stand-in, and the service
manager is the oracle's fake behind scripts/takeover/systemctl-guard. What is shown is the runner: that `prepare` builds
beside the running deployment and touches nothing of it, that `start` is one restart, what each mode refuses, and that
no secret value reaches the log.
"""
import copy, hashlib, json, os, shutil, subprocess, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT / 'test/corpus'), str(ROOT / 'test/differential')]
import corpus
import ts_port

SCRATCH = ROOT / '.cache/takeover-test'
GUARD = ROOT / 'scripts/takeover/systemctl-guard'
NODE = os.environ.get('KITCHEN_NODE') or shutil.which('node')


class Takeover(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ts_port.initialize()
        for name in ('connectome_deploy', 'connectome_cook', 'kitchen_v1'):
            __import__(name)
        ts_port.remove(SCRATCH)
        SCRATCH.mkdir(parents=True)
        cls.case = next(case for case in corpus.cases(corpus.located('synthetic', SCRATCH)) if case.name == 'example')

    @classmethod
    def tearDownClass(cls):
        ts_port.remove(SCRATCH)

    def world(self, label, activation='start'):
        """A sandbox where the role has deployed and started the agent, and the takeover input for it."""
        case = copy.copy(self.case)
        case.data = copy.deepcopy(self.case.data)
        case.data['layers']['agent']['activation'] = activation
        world = corpus.World(case, SCRATCH / label)
        world.build()
        self.role = world.role_apply(world.operator_files())
        sandbox, root = world.sandbox, Path(world.root)
        self.unit = 'connectome-%s.service' % world.effective['name']
        fake = sandbox / 'fake-systemctl'
        corpus.write(fake, '#!/bin/sh\nexec %s %s %s "$@"\n' % tuple(world.systemctl), 0o755)
        request = dict(copy.deepcopy(world.request))
        del request['systemctl']
        recipe = world.raw_layers['agent']['recipe_file']['path']
        # The recipe's bytes where the running build has them: the configured path need not be readable by the account.
        self.live = root / 'builds' / self.role['request_id']
        world.node, world.own_name = NODE, True
        self.name = world.request_id()
        self.input = sandbox / 'takeover.json'
        # The configured recipe path is the controller's and is not there for the account, as on a host where it lies in
        # another account's home: `plan` and `apply` must both live on the bytes the runner hands over.
        pinned = next(str(path) for path in sorted(Path(world.sandbox / 'srv').rglob('*')) if path.is_file())
        Path(recipe).unlink()
        self.document = {'schema': 'connectome.kitchen-takeover/v1', 'request': request, 'systemctl': [str(fake), '--user'],
                         'files': {pinned: hashlib.sha256(Path(pinned).read_bytes()).hexdigest()}, 'recipe': {'source': str(self.live / 'cook-recipe.json')},
                         'expect': {'home': str(world.home), 'account': os.environ.get('USER') or __import__('pwd').getpwuid(os.geteuid()).pw_name,
                                    'request_id': self.name, 'live_build': self.role['request_id']}}
        self.input.write_text(json.dumps(self.document))
        self.keep = world.home / 'kitchen-takeover' / world.effective['name']
        self.calls = root / 'systemctl-calls'
        return world

    def run_mode(self, world, mode):
        env = {'HOME': str(world.home), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'XDG_RUNTIME_DIR': str(world.sandbox)}
        seen = len(corpus.lines(self.calls))
        done = subprocess.run([NODE, str(ROOT / 'test/corpus/takeover_driver.mjs'), mode, str(self.input), str(world.sandbox), world.account],
                              env=env, capture_output=True, text=True, timeout=300)
        text = (self.keep / 'takeover.log').read_text() if (self.keep / 'takeover.log').is_file() else ''
        log = list(map(json.loads, text.splitlines()))
        log = log[max((index for index, entry in enumerate(log) if entry['step'] == 'start'), default=0):]  # this run's lines
        verbs = [next(word for word in call if not word.startswith('-')) for call in corpus.lines(self.calls)[seen:]]
        return done, log, verbs

    def pointers(self, world):
        root, unit_dir = Path(world.root), Path(world.unit_dir)
        return (os.readlink(root / 'current'), os.readlink(unit_dir / self.unit), (unit_dir / (self.unit + '.d') / '90-connectome-env.conf').read_text(),
                os.readlink(unit_dir / 'default.target.wants' / self.unit), (root / 'state/ownership.json').read_text())

    def test_check_prepare_verify_start(self):
        world = self.world('run')
        root = Path(world.root)
        build, cook_calls = root / 'builds' / self.name, lambda: len(corpus.lines(root / 'cook-calls.jsonl'))
        before, tree = self.pointers(world), corpus.snapshot(world.sandbox)
        self.assertEqual((before[0], cook_calls()), (str(self.live), 1))

        done, log, verbs = self.run_mode(world, 'check')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(set(verbs), {'is-active', 'show'})
        changed = {path for path, row in corpus.snapshot(world.sandbox).items() if tree.get(path) != row}
        self.assertEqual({path for path in changed if not path.startswith(str(self.keep.parent)) and Path(path).name not in corpus.VOLATILE}, set())
        plan = next(entry for entry in log if entry['step'] == 'plan answered')['core']
        self.assertEqual((plan['prepare']['changed'], plan['prepare']['build_dir'], plan['activate']), (True, str(build), {'changed': True}))
        self.assertNotIn('activation', plan['prepare']['plan'])

        done, log, verbs = self.run_mode(world, 'start')  # not before prepare
        self.assertEqual((done.returncode, self.pointers(world), cook_calls()), (3, before, 1), done.stderr)
        self.assertIn('not prepared', log[-1]['why'])
        self.assertEqual(set(verbs), {'is-active', 'show'})

        done, log, verbs = self.run_mode(world, 'prepare')
        self.assertEqual(done.returncode, 0, done.stderr)
        after = self.pointers(world)
        self.assertEqual(after[:4], before[:4])  # the pointer, the unit link, the drop-in and the enable link
        added = [entry for entry in json.loads(after[4])['entries'] if entry not in json.loads(before[4])['entries']]
        self.assertEqual(added, [{'kind': 'directory', 'path': str(build)}])
        self.assertEqual(json.loads(before[4])['entries'], [entry for entry in json.loads(after[4])['entries'] if entry not in added])
        self.assertEqual(set(verbs), {'is-active', 'show'})
        self.assertEqual((cook_calls(), (build / '.prepared').read_text(), (build / 'secrets.env').exists()), (2, self.name, False))
        self.assertEqual([entry['kind'] for entry in log if entry['step'] == 'tool started'], ['adapter'])
        self.assertTrue(next(entry for entry in log if entry['step'] == 'running deployment')['untouched_by_this_run'])
        receipt = next(entry for entry in log if entry['step'] == 'apply answered')['core']
        self.assertEqual((receipt['changed'], sorted(receipt)), (True, ['changed', 'prepare']))

        done, log, verbs = self.run_mode(world, 'prepare')  # again: nothing to do, no cook run
        self.assertEqual((done.returncode, cook_calls(), self.pointers(world)), (0, 2, after), done.stderr)

        done, log, verbs = self.run_mode(world, 'verify')
        self.assertEqual((done.returncode, self.pointers(world)), (0, after), done.stderr)
        plan = next(entry for entry in log if entry['step'] == 'plan answered')['core']
        self.assertEqual((plan['prepare']['changed'], plan['activate']), (False, {'changed': True}))
        view = next(entry for entry in log if entry['step'] == 'secrets file')
        self.assertEqual((view['missing'], view['environment_file_of_current_build_is_what_start_would_write'], view['secret_files_are_what_start_would_write']),
                         ([], True, {'CHAT_RC_PATH': True}))
        self.assertEqual(set(verbs), {'is-active', 'show'})

        done, log, verbs = self.run_mode(world, 'start')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([entry['kind'] for entry in log if entry['step'] == 'tool started'], ['daemon-reload', 'restart', 'is-active'])
        self.assertEqual((verbs.count('restart'), verbs.count('daemon-reload'), set(verbs)), (1, 1, {'is-active', 'show', 'daemon-reload', 'restart'}))
        taken = self.pointers(world)
        self.assertEqual(taken[:2], (str(build), str(build / 'managed-units' / self.unit)))
        self.assertEqual(taken[3], str(build / 'managed-units' / self.unit))
        self.assertEqual(taken[2], before[2].replace(self.role['request_id'], self.name))
        self.assertEqual(((build / 'secrets.env').read_text(), cook_calls()), ((self.live / 'secrets.env').read_text(), 2))
        self.assertTrue((self.live / '.prepared').is_file())  # the role's build stays, for the way back
        self.assertFalse(next(entry for entry in log if entry['step'] == 'running deployment')['untouched_by_this_run'])

        done, log, verbs = self.run_mode(world, 'verify')
        self.assertEqual((done.returncode, self.pointers(world)), (0, taken), done.stderr)
        self.assertFalse(next(entry for entry in log if entry['step'] == 'plan answered')['core']['changed'])
        done, log, verbs = self.run_mode(world, 'start')  # again: nothing to do, no restart
        self.assertEqual((done.returncode, self.pointers(world), verbs.count('restart'), verbs.count('daemon-reload')), (0, taken, 0, 0), done.stderr)
        # No secret value in anything the runner kept.
        kept = ''.join(path.read_text() for path in self.keep.iterdir())
        for value in world.secrets.values():
            self.assertNotIn(value, kept)
        self.assertNotIn(hashlib.sha256(Path(world.effective['secrets_file']).read_bytes()).hexdigest(), kept)

    def test_refuses_install_only_another_live_build_a_wrong_recipe_and_missing_secret(self):
        world = self.world('refusals')
        before = self.pointers(world)
        document = copy.deepcopy(self.document)

        def refused(mode, change, word):
            changed = copy.deepcopy(document)
            change(changed)
            self.input.write_text(json.dumps(changed))
            done, log, verbs = self.run_mode(world, mode)
            self.assertEqual((done.returncode, self.pointers(world)), (3, before), done.stderr)
            self.assertIn(word, done.stderr)
            self.assertLessEqual(set(verbs), {'is-active', 'show'})
        refused('prepare', lambda d: d['request']['layers'][-1].update(activation='install_only'), 'activation must be start')
        refused('prepare', lambda d: d['expect'].update(live_build='0' * 64), 'current does not name')
        refused('prepare', lambda d: d['expect'].update(request_id='1' * 64), 'names the build otherwise')
        refused('prepare', lambda d: d['recipe'].update(source=str(Path(world.root) / 'state/ownership.json')), 'recipe source')
        refused('check', lambda d: d['expect'].update(account='another'), 'another account')
        secrets = Path(world.effective['secrets_file'])
        values = json.loads(secrets.read_text())
        secrets.write_text(json.dumps({key: value for key, value in values.items() if key != 'CHAT_RC'}))
        refused('verify', lambda d: None, 'secrets file does not hold')
        self.assertEqual(json.loads((self.keep / 'takeover.log').read_text().splitlines()[-2])['missing'], ['CHAT_RC'])
        secrets.write_text(json.dumps(values))
        # A refusal of the engine: the receipt has the closed code, and the account's log the engine's own fixed words.
        self.input.write_text(json.dumps(document))
        state = Path(world.effective['state_dir'])
        state.chmod(0o755)
        done, log, verbs = self.run_mode(world, 'check')
        state.chmod(0o700)
        self.assertEqual((done.returncode, self.pointers(world)), (1, before), done.stderr)
        answer = next(entry for entry in log if entry['step'] == 'plan answered')['core']
        self.assertEqual((answer['error_code'], 'state_dir' in answer['msg']), ('E_DEPLOY_FAILED', False))
        self.assertIn('state_dir must be a real directory, mode 0700', next(entry for entry in log if entry['step'] == 'engine refused')['reason'])

    def test_a_start_that_fails_is_taken_back_to_the_running_build(self):
        world = self.world('back')
        root, before = Path(world.root), self.pointers(world)
        build = root / 'builds' / self.name
        self.assertEqual(self.run_mode(world, 'prepare')[0].returncode, 0)
        # The service manager fails the restart while the pointer names the new build, as a unit that does not come up would.
        fake = Path(self.document['systemctl'][0])
        passing = fake.read_text().splitlines()[1]
        corpus.write(fake, '#!/bin/sh\nfor a in "$@"; do if [ "$a" = restart ] && [ "$(readlink %s)" = %s ]; then echo "$@" >>%s; exit 1; fi; done\n%s\n' % (
            root / 'current', build, world.sandbox / 'failed-restarts', passing), 0o755)
        done, log, verbs = self.run_mode(world, 'start')
        self.assertEqual(done.returncode, 1, done.stderr)
        answer = next(entry for entry in log if entry['step'] == 'apply answered')['core']
        self.assertEqual((answer['phase'], answer['error_code']), ('activate', 'E_DEPLOY_FAILED'))
        self.assertIn('previous deployment restored', answer['msg'])
        after = self.pointers(world)
        self.assertEqual(after[:4], before[:4])  # the pointer, the unit link, the drop-in and the enable link are the running build's again
        self.assertEqual(len((world.sandbox / 'failed-restarts').read_text().splitlines()), 1)
        self.assertEqual((verbs.count('restart'), verbs.count('daemon-reload')), (1, 2))  # the previous build restarted once, by the take-back
        self.assertTrue((build / '.prepared').is_file() and (self.live / 'secrets.env').is_file())
        states = json.loads((root.parent / 'unit-states.json').read_text())
        self.assertEqual(states['user:' + self.unit], 'active')

    def test_the_guard(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        log, called = SCRATCH / 'guard.log', SCRATCH / 'guard.called'
        real = SCRATCH / 'guard-real'
        corpus.write(real, '#!/bin/sh\necho "$*" >>%s\n' % called, 0o755)
        for path in (log, called):
            path.unlink(missing_ok=True)
        status = lambda mode, *args: subprocess.run([str(GUARD), mode, str(log), 'a.service', str(real), *args]).returncode
        questions = (['--user', 'is-active', 'a.service'], ['--user', 'is-active', '--quiet', 'b.service'], ['--user', 'show', '-p', 'ActiveState', '--value', 'a.service'])
        for mode in ('readonly', 'start'):
            for args in questions:
                self.assertEqual(status(mode, *args), 0)
        self.assertEqual(status('start', '--user', 'daemon-reload'), 0)
        self.assertEqual(status('start', '--user', 'restart', 'a.service'), 0)
        refusals = [('readonly', 'daemon-reload'), ('readonly', 'restart', 'a.service'), ('start', 'restart', 'b.service'), ('start', 'restart', 'a.service', 'b.service'),
                    ('start', 'restart'), ('start', 'daemon-reload', 'a.service'), ('other', 'show', 'a.service'), ('start',)]
        refusals += [(mode, verb, 'a.service') for mode in ('readonly', 'start') for verb in ('start', 'stop', 'enable', 'disable', 'reload-or-restart', 'try-restart', 'mask', 'kill', 'isolate', 'edit', 'link', 'set-property')]
        for mode, *args in refusals:
            self.assertEqual(status(mode, '--user', *args), 97, (mode, args))
        self.assertEqual(len(called.read_text().splitlines()), 8)
        self.assertEqual(len(log.read_text().splitlines()), 8 + len(refusals))


if __name__ == '__main__':
    unittest.main()
