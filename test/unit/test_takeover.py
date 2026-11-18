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
MODES = ('check', 'prepare', 'verify', 'start')


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

    def world(self, label, activation='start', recipe=None):
        """A sandbox where the role has deployed and started the agent, and the takeover input for it. `recipe` changes the
        case's recipe before anything is laid out: it is handed the recipe and the world."""
        case = copy.copy(self.case)
        case.data = copy.deepcopy(self.case.data)
        case.data['layers']['agent']['activation'] = activation
        world = corpus.World(case, SCRATCH / label)
        if recipe is not None:
            case.directory = SCRATCH / (label + '-case')
            shutil.copytree(self.case.directory, case.directory)
            file = case.directory / case.data['recipe']['file']
            document = json.loads(file.read_text())
            recipe(document, world)
            file.write_text(json.dumps(document, indent=2) + '\n')
        world.build()
        self.role = world.role_apply(world.operator_files())
        sandbox, root = world.sandbox, Path(world.root)
        self.unit = 'connectome-%s.service' % world.effective['name']
        # The oracle's fake has no process: it answers `show` of every property but ActiveState with one line that is not
        # a number. The two properties that are numbers get one here, behind the fake (which still logs the call): a main
        # process, and no restart: 0 is a value. A test puts its own answers in front of `exec` in `fake-systemctl`.
        process = sandbox / 'fake-systemctl-process'
        corpus.write(process, '#!/bin/sh\ncase "$*" in *MainPID*|*NRestarts*) %s %s %s "$@" >/dev/null || exit\n'
                     '  case "$*" in *MainPID*) echo 4242;; *) echo 0;; esac; exit 0;; esac\nexec %s %s %s "$@"\n' % (tuple(world.systemctl) * 2), 0o755)
        fake = sandbox / 'fake-systemctl'
        corpus.write(fake, '#!/bin/sh\nexec %s "$@"\n' % process, 0o755)
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

    def first_world(self, label):
        """A sandbox where nothing is deployed: the account, its secrets file and the pinned inputs, and no deployment root,
        no unit and no build. The takeover input says so with `live_build: null`."""
        case = copy.copy(self.case)
        case.data = copy.deepcopy(self.case.data)
        case.data['layers']['agent']['activation'] = 'start'
        world = corpus.World(case, SCRATCH / label)
        world.build()
        sandbox, root = world.sandbox, Path(world.root)
        self.assertFalse(root.exists() or Path(world.unit_dir).exists())  # the premise
        self.unit = 'connectome-%s.service' % world.effective['name']
        process = sandbox / 'fake-systemctl-process'
        corpus.write(process, '#!/bin/sh\ncase "$*" in *MainPID*|*NRestarts*) %s %s %s "$@" >/dev/null || exit\n'
                     '  case "$*" in *MainPID*) echo 0;; *) echo 0;; esac; exit 0;; esac\nexec %s %s %s "$@"\n' % (tuple(world.systemctl) * 2), 0o755)
        fake = sandbox / 'fake-systemctl'
        # The oracle's fake writes down what it was asked under the deployment root, which is not there yet: the calls are
        # written down here, beside it.
        self.calls = sandbox / 'first-systemctl-calls'
        corpus.write(fake, '#!/bin/sh\n/usr/bin/python3 -I -c \'import json, sys; print(json.dumps(sys.argv[1:]))\' "$@" >>%s\nexec %s "$@"\n' % (self.calls, process), 0o755)
        request = dict(copy.deepcopy(world.request))
        del request['systemctl']
        recipe = world.raw_layers['agent']['recipe_file']['path']
        world.node, world.own_name = NODE, True
        self.name = world.request_id()
        self.input = sandbox / 'takeover.json'
        pinned = next(str(path) for path in sorted(Path(world.sandbox / 'srv').rglob('*')) if path.is_file())
        # The recipe's bytes as the operator hands them to the account; the configured path is not there for it.
        source = sandbox / 'handed-recipe.json'
        shutil.copyfile(recipe, source)
        Path(recipe).unlink()
        self.document = {'schema': 'connectome.kitchen-takeover/v1', 'request': request, 'systemctl': [str(fake), '--user'],
                         'files': {pinned: hashlib.sha256(Path(pinned).read_bytes()).hexdigest()}, 'recipe': {'source': str(source)},
                         'expect': {'home': str(world.home), 'account': os.environ.get('USER') or __import__('pwd').getpwuid(os.geteuid()).pw_name,
                                    'request_id': self.name, 'live_build': None}}
        self.input.write_text(json.dumps(self.document))
        self.keep = world.home / 'kitchen-takeover' / world.effective['name']
        return world

    def installed(self, world):
        """What of a deployment is there: the pointer, the unit link, the enable link, the drop-in directory."""
        root, unit_dir = Path(world.root), Path(world.unit_dir)
        return [os.path.lexists(path) for path in (root / 'current', unit_dir / self.unit, unit_dir / 'default.target.wants' / self.unit, unit_dir / (self.unit + '.d'))]

    def test_first_install_check_prepare_verify_start(self):
        world = self.first_world('first')
        root, unit_dir = Path(world.root), Path(world.unit_dir)
        build = root / 'builds' / self.name
        tools = lambda log: [entry['kind'] for entry in log if entry['step'] == 'tool started']
        guard = lambda log: [(entry['level'], entry['asked'], entry['refused']) for entry in log if entry['step'] == 'guard self-test']

        done, log, verbs = self.run_mode(world, 'check')
        self.assertEqual((done.returncode, root.exists(), unit_dir.exists()), (0, False, False), done.stderr)
        self.assertEqual(set(verbs), {'is-active', 'show'})
        plan = next(entry for entry in log if entry['step'] == 'plan answered')['core']
        self.assertEqual((plan['prepare']['changed'], plan['prepare']['build_dir'], plan['activate']), (True, str(build), {'changed': True}))
        self.assertIsNone(next(entry for entry in log if entry['step'] == 'start')['live_build'])
        # The guard is asked six things it must refuse, two of them of the mode of a first start.
        self.assertEqual(guard(log)[4:], [('first-start', 'stop another-' + self.unit, True), ('first-start', 'disable ' + self.unit, True)])
        self.assertEqual(len(guard(log)), 6)

        done, log, verbs = self.run_mode(world, 'start')  # not before prepare
        self.assertEqual((done.returncode, self.installed(world), root.exists()), (3, [False] * 4, False), done.stderr)
        self.assertIn('not prepared', log[-1]['why'])
        self.assertEqual(set(verbs), {'is-active', 'show'})

        done, log, verbs = self.run_mode(world, 'prepare')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual((tools(log), set(verbs), self.installed(world)), (['adapter'], {'is-active', 'show'}, [False] * 4))
        self.assertEqual(((build / '.prepared').read_text(), (build / 'secrets.env').exists(), (root / 'state/secret-files').exists()), (self.name, False, False))
        self.assertEqual([entry for entry in json.loads((root / 'state/ownership.json').read_text())['entries'] if entry['path'] == str(build)], [{'kind': 'directory', 'path': str(build)}])
        witness = next(entry for entry in log if entry['step'] == 'running deployment')
        # No build was there to measure, and `builds` not being there is an empty list that was read: the witness is complete.
        self.assertEqual((witness['untouched_by_this_run'], witness['builds_measured'], witness['measurement_complete']), (True, [], True))
        done, log, verbs = self.run_mode(world, 'prepare')  # again: nothing to do, no cook run
        self.assertEqual((done.returncode, tools(log), self.installed(world)), (0, [], [False] * 4), done.stderr)

        done, log, verbs = self.run_mode(world, 'verify')
        self.assertEqual((done.returncode, self.installed(world)), (0, [False] * 4), done.stderr)
        plan = next(entry for entry in log if entry['step'] == 'plan answered')['core']
        self.assertEqual((plan['prepare']['changed'], plan['activate']), (False, {'changed': True}))
        view = next(entry for entry in log if entry['step'] == 'secrets file')
        # No build runs: there is no environment file to hold the secrets file to, and no secret file is there yet.
        self.assertEqual((view['missing'], view['environment_file_of_current_build_is_what_start_would_write'], view['secret_files_are_what_start_would_write']), ([], None, {'CHAT_RC_PATH': None}))
        self.assertEqual(set(verbs), {'is-active', 'show'})

        done, log, verbs = self.run_mode(world, 'start')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(tools(log), ['daemon-reload', 'restart', 'is-active'])
        self.assertEqual((verbs.count('restart'), verbs.count('daemon-reload'), set(verbs)), (1, 1, {'is-active', 'show', 'daemon-reload', 'restart'}))
        self.assertEqual((os.readlink(root / 'current'), os.readlink(unit_dir / self.unit), os.readlink(unit_dir / 'default.target.wants' / self.unit)),
                         (str(build), str(build / 'managed-units' / self.unit), str(build / 'managed-units' / self.unit)))
        self.assertIn(self.name, (unit_dir / (self.unit + '.d') / '90-connectome-env.conf').read_text())
        self.assertTrue((build / 'secrets.env').is_file() and (root / 'state/secret-files/CHAT_RC_PATH').is_file())
        self.assertEqual(json.loads((root.parent / 'unit-states.json').read_text())['user:' + self.unit], 'active')

        done, log, verbs = self.run_mode(world, 'verify')  # after the first start: the pointer names the new build
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(next(entry for entry in log if entry['step'] == 'plan answered')['core']['changed'])
        view = next(entry for entry in log if entry['step'] == 'secrets file')
        self.assertEqual((view['environment_file_of_current_build_is_what_start_would_write'], view['secret_files_are_what_start_would_write']), (True, {'CHAT_RC_PATH': True}))
        # A repeated null-live start must not apply at all, even with otherwise-valid rotated inputs.
        secrets = Path(world.effective['secrets_file'])
        rotated = json.loads(secrets.read_text())
        rotated[next(iter(rotated))] = 'synthetic-rotation-must-not-appear'
        secrets.write_text(json.dumps(rotated))
        before = (build / 'secrets.env').read_bytes()
        done, _, verbs = self.run_mode(world, 'start')
        self.assertEqual((done.returncode, verbs, (build / 'secrets.env').read_bytes()), (3, [], before), done.stderr)
        self.assertIn('a first install is of a root whose current is not there', done.stderr)
        self.assertNotIn('synthetic-rotation-must-not-appear', done.stderr)
        # `check` and `prepare` are of a root where nothing runs: once the agent is installed they refuse this request.
        for mode in ('check', 'prepare'):
            done, log, verbs = self.run_mode(world, mode)
            self.assertEqual(done.returncode, 3, done.stderr)
            self.assertIn('a first install is of a root whose current is not there', done.stderr)
            self.assertEqual(set(verbs), set())
        # No secret value in anything the runner kept.
        kept = ''.join(path.read_text() for path in self.keep.iterdir())
        for value in world.secrets.values():
            self.assertNotIn(value, kept)

    def test_first_start_checks_effective_units_before_apply(self):
        for form in ('export', 'explicit'):
            for shape in ('single', 'no-install', 'extra-target', 'alias', 'wrong-unit', 'extra-unit'):
                if shape == 'extra-unit' and form == 'export':
                    continue  # the export names at most one unit
                with self.subTest(form=form, shape=shape):
                    world = self.first_world(form + '-' + shape)
                    root, unit_dir = Path(world.root), Path(world.unit_dir)
                    if shape == 'no-install':
                        (root.parent / 'unit-without-install-section').touch()
                    done, _, _ = self.run_mode(world, 'prepare')
                    self.assertEqual(done.returncode, 0, done.stderr)
                    build = root / 'builds' / self.name
                    lock_path = build / 'connectome.lock'
                    lock = json.loads(lock_path.read_bytes())
                    self.assertNotIn('units', lock)
                    self.assertEqual(lock['launch']['userUnit'], self.unit)
                    unit_file = build / self.unit
                    if shape in ('extra-target', 'alias'):
                        with unit_file.open('a') as stream:
                            stream.write('WantedBy=other.target\n' if shape == 'extra-target' else 'Alias=other.service\n')
                    if shape == 'wrong-unit':
                        unit_file = unit_file.rename(build / 'wrong.service')
                        lock['launch']['userUnit'] = unit_file.name

                    def record(file):
                        return {'path': file.name, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}

                    if form == 'explicit':
                        del lock['launch']['userUnit']
                        lock['units'] = [record(unit_file)]
                        if shape == 'extra-unit':
                            extra = build / 'other.service'
                            shutil.copyfile(unit_file, extra)
                            lock['units'].append(record(extra))
                    lock_path.write_text(json.dumps(lock))

                    manifest = root / 'state/ownership.json'
                    before = manifest.read_bytes()
                    done, log, verbs = self.run_mode(world, 'start')
                    if shape == 'single':
                        self.assertEqual((done.returncode, verbs.count('restart'), self.installed(world)), (0, 1, [True] * 4), done.stderr)
                    else:
                        self.assertEqual((done.returncode, self.installed(world)), (3, [False] * 4), done.stderr)
                        self.assertIn('first start requires', log[-1]['why'])
                        self.assertTrue(set(verbs) <= {'is-active', 'show'}, verbs)
                        self.assertFalse(any(row['step'] == 'apply asked' for row in log))
                        self.assertFalse((build / 'secrets.env').exists())
                        self.assertFalse(unit_dir.exists())
                        self.assertEqual(manifest.read_bytes(), before)

    def test_a_first_start_that_fails_leaves_nothing_installed(self):
        world = self.first_world('first-back')
        root = Path(world.root)
        build = root / 'builds' / self.name
        self.assertEqual(self.run_mode(world, 'prepare')[0].returncode, 0)
        # The unit does not come up: the service manager fails the restart.
        fake = Path(self.document['systemctl'][0])
        passing = fake.read_text()
        corpus.write(fake, passing.replace('\nexec ', '\nfor a in "$@"; do if [ "$a" = restart ]; then exit 1; fi; done\nexec '), 0o755)
        done, log, verbs = self.run_mode(world, 'start')
        self.assertEqual(done.returncode, 1, done.stderr)
        answer = next(entry for entry in log if entry['step'] == 'apply answered')['core']
        self.assertEqual((answer['phase'], answer['error_code']), ('activate', 'E_DEPLOY_FAILED'))
        self.assertIn('previous deployment restored', answer['msg'])
        # The engine takes its start back with `stop` of this one unit, which the guard lets through for a first start only.
        self.assertEqual([entry['kind'] for entry in log if entry['step'] == 'tool started'], ['daemon-reload', 'restart', 'stop', 'daemon-reload'])
        self.assertEqual([entry for entry in log if entry['step'] == 'tool refused'], [])
        self.assertEqual((verbs.count('restart'), verbs.count('stop'), verbs.count('daemon-reload')), (1, 1, 2))
        # Nothing is installed: no pointer, no unit link, no enable link, no drop-in, no secret file. The build stays.
        self.assertEqual((self.installed(world), (root / 'state/secret-files').exists(), (build / '.prepared').is_file()), ([False] * 4, False, True))
        self.assertNotEqual(json.loads((root.parent / 'unit-states.json').read_text()).get('user:' + self.unit), 'active')
        # And the next start, with the cause gone, starts it.
        corpus.write(fake, passing, 0o755)
        done, log, verbs = self.run_mode(world, 'verify')
        self.assertEqual(done.returncode, 0, done.stderr)
        view = next(entry for entry in log if entry['step'] == 'secrets file')
        self.assertIs(view['environment_file_of_current_build_is_what_start_would_write'], True)
        self.assertEqual(view['secret_files_are_what_start_would_write'], {'CHAT_RC_PATH': None})
        for secret in world.secrets.values():
            self.assertNotIn(secret, done.stderr)
        # An entry that cannot be read as a regular environment file is not missing.
        env_path = build / 'secrets.env'
        content = env_path.read_bytes()
        env_path.unlink()
        env_path.mkdir()
        _, log, _ = self.run_mode(world, 'verify')
        self.assertIs(next(entry for entry in log if entry['step'] == 'secrets file')['environment_file_of_current_build_is_what_start_would_write'], False)
        env_path.rmdir()
        env_path.write_bytes(content)
        env_path.chmod(0o600)
        self.assertEqual(done.returncode, 0, done.stderr)
        done, log, verbs = self.run_mode(world, 'start')
        self.assertEqual((done.returncode, self.installed(world), verbs.count('restart'), verbs.count('stop')), (0, [True] * 4, 1, 0), done.stderr)

    def test_a_first_install_is_refused_where_something_is_deployed(self):
        # `live_build: null` says nothing is deployed. Where a build, a pointer or a unit of this deployment is there, it is
        # not a first install, and nothing is asked of the engine.
        def refused(world, mode, word, change=lambda d: None):
            changed = copy.deepcopy(self.document)
            change(changed)
            self.input.write_text(json.dumps(changed))
            done, log, verbs = self.run_mode(world, mode)
            self.assertEqual(done.returncode, 3, done.stderr)
            self.assertIn(word, done.stderr)
            self.assertEqual([entry for entry in log if entry['step'].endswith(' asked')], [])
            self.assertLessEqual(set(verbs), {'is-active', 'show'})
        world = self.world('first-on-running')  # a deployment that runs
        before = self.pointers(world)
        for mode in MODES:
            refused(world, mode, 'a first install is of a root with no build but its own', lambda d: d['expect'].update(live_build=None))
        self.assertEqual(self.pointers(world), before)
        world = self.first_world('first-refusals')
        root, unit_dir = Path(world.root), Path(world.unit_dir)
        for value in ('', 0, False, 'null'):
            refused(world, 'check', 'expect', lambda d: d['expect'].update(live_build=value))
        # A takeover (two build names) of a root where nothing is deployed is refused as before.
        refused(world, 'check', 'the deployment root is not one an engine made', lambda d: d['expect'].update(live_build='0' * 64))
        root.mkdir(parents=True)
        (root / 'current').symlink_to(root / 'builds' / ('0' * 64))
        for mode in MODES:
            refused(world, mode, 'a first install is of a root whose current is not there')
        (root / 'current').unlink()
        for leaf, directory in ((self.unit, False), ('default.target.wants/' + self.unit, False), (self.unit + '.d', True)):
            path = unit_dir / leaf
            path.parent.mkdir(parents=True, exist_ok=True)
            path.mkdir() if directory else path.symlink_to('/nowhere/' + self.unit)  # also a link that names nothing
            for mode in ('check', 'prepare'):
                refused(world, mode, 'a first install is of an account that has no unit of this deployment')
            path.rmdir() if directory else path.unlink()
        # And with all of that gone it is one. (The directories made here are this test's, with its umask: the engine holds
        # the ancestors of what it writes to modes of its own, so they go too.)
        shutil.rmtree(root); shutil.rmtree(unit_dir.parent)
        self.input.write_text(json.dumps(self.document))
        done, log, verbs = self.run_mode(world, 'check')
        self.assertEqual(done.returncode, 0, done.stderr)

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
        witness = next(entry for entry in log if entry['step'] == 'running deployment')
        self.assertIs(witness['untouched_by_this_run'], True)
        # What the line says it covers: the build that runs, and not the requested one, which this step wrote.
        self.assertEqual((witness['builds_measured'], witness['requested_build_measured'], witness['measurement_complete']), ([self.role['request_id']], False, True))
        self.assertIn('the requested build %s: this step writes it' % self.name, witness['not_measured'])
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
        # `verify` writes no build, so it measures the requested one too.
        witness = next(entry for entry in log if entry['step'] == 'running deployment')
        self.assertEqual((witness['untouched_by_this_run'], witness['builds_measured'], witness['requested_build_measured']), (True, sorted([self.role['request_id'], self.name]), True))
        self.assertFalse([line for line in witness['not_measured'] if 'requested build' in line])

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
        self.assertIs(next(entry for entry in log if entry['step'] == 'running deployment')['untouched_by_this_run'], False)

        done, log, verbs = self.run_mode(world, 'verify')
        self.assertEqual((done.returncode, self.pointers(world)), (0, taken), done.stderr)
        witness = next(entry for entry in log if entry['step'] == 'running deployment')  # the requested build is now the one that runs, and is measured
        self.assertEqual((witness['untouched_by_this_run'], witness['requested_build_measured'], self.name in witness['builds']), (True, True, True))
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

    # What Bun does with its cache, in the place of the stand-in's first line: it takes the cache cook would hand it (the
    # name reaches Bun only where COOK_PASS_ENV selects it, cook src/process-env.ts), else the account's; "downloads" the
    # package file where the cache lacks it; hard-links it into the build; and sets the mode of the `bin` file from the
    # umask of this install, on that inode.
    BUN = '''
import os, sys
from pathlib import Path
_out = Path(sys.argv[sys.argv.index("--out") + 1])
_passed = os.environ.get("COOK_PASS_ENV", "").replace(",", " ").split()
_cache = Path(os.environ["BUN_INSTALL_CACHE_DIR"]) if "BUN_INSTALL_CACHE_DIR" in _passed and "BUN_INSTALL_CACHE_DIR" in os.environ else Path(os.environ["HOME"]) / ".bun/install/cache"
_src = _cache / "pkg@1/bin.js"
if not _src.exists():
    _src.parent.mkdir(parents=True)
    _src.write_text("#!/usr/bin/env node\\n")
_dst = _out / "app/node_modules/pkg/bin.js"
_dst.parent.mkdir(parents=True)
os.link(_src, _dst)
os.chmod(_dst, 0o755)
'''

    def before_cook(self, world, lines):
        """The stand-in for cook does this first."""
        runtime = Path(world.tools_moved['runtime'])
        first, rest = runtime.read_text().split('\n', 1)
        runtime.write_text(first + '\n' + lines + rest)

    def test_prepare_shares_no_file_with_the_running_build(self):
        world = self.world('own-cache')
        root = Path(world.root)
        # The running build as an earlier install left it: a `bin` file that is also a name of the account's Bun cache, mode 0775.
        cached = world.home / '.bun/install/cache/pkg@1/bin.js'
        cached.parent.mkdir(parents=True)
        cached.write_text('#!/usr/bin/env node\n')
        cached.chmod(0o775)
        live = self.live / 'app/node_modules/pkg/bin.js'
        live.parent.mkdir(parents=True)
        os.link(cached, live)
        orphan = root / ('.bun-cache-' + 'f' * 64) / 'pkg@1/bin.js'  # what a run that was killed leaves: the cache of another request
        orphan.parent.mkdir(parents=True)
        orphan.write_text('#!/usr/bin/env node\n')
        self.before_cook(world, self.BUN)
        seen = lambda path: (path.stat().st_mode & 0o7777, path.stat().st_nlink, path.stat().st_ino, path.stat().st_ctime_ns)
        before = seen(live)
        done, log, verbs = self.run_mode(world, 'prepare')
        self.assertEqual(done.returncode, 0, done.stderr)
        new = root / 'builds' / self.name / 'app/node_modules/pkg/bin.js'
        self.assertEqual((seen(live), before[:2]), (before, (0o775, 2)))  # not its mode, not its link count, not its change time
        self.assertEqual((new.stat().st_mode & 0o7777, new.stat().st_nlink, new.stat().st_ino == live.stat().st_ino), (0o755, 1, False))
        self.assertEqual([path.name for path in root.iterdir() if 'cache' in path.name and path.is_dir()], [])  # the cache of its own is gone, and the one a killed run left
        call = corpus.lines(root / 'cook-calls.jsonl')[-1]
        self.assertLessEqual(set(corpus.OWN_CACHE), set(call['environment_names']))
        witness = next(entry for entry in log if entry['step'] == 'running deployment')
        self.assertEqual((witness['untouched_by_this_run'], witness['builds_that_were_there_as_before'], sorted(witness['builds'])), (True, True, [self.role['request_id']]))
        self.assertEqual((witness['builds'][self.role['request_id']]['unchanged'], witness['builds'][self.role['request_id']]['entries'] > 5), (True, True))
        shared = next(entry for entry in log if entry['step'] == 'new build')['files_with_a_name_outside_the_build']
        self.assertEqual((shared['outside'], shared['with_other_builds']), (0, 0))

    def test_a_write_to_the_running_build_is_seen_and_the_build_refused(self):
        # A runtime that links a file of the running build into the new one and sets its mode: what an install through a shared
        # cache does. No cache of its own keeps a runtime from that; the adapter refuses the build and the runner says what changed.
        world = self.world('shared')
        live = self.live / 'shared-fixture.txt'
        live.write_text('public synthetic fixture\n')
        live.chmod(0o444)
        self.before_cook(world, '\nimport os, sys\nfrom pathlib import Path\n_shared = Path(sys.argv[sys.argv.index("--out") + 1]) / "shared-fixture.txt"\nos.link(%r, _shared)\nos.chmod(_shared, 0o644)\n' % str(live))
        before = self.pointers(world)
        done, log, verbs = self.run_mode(world, 'prepare')
        self.assertEqual((done.returncode, self.pointers(world)[:4]), (3, before[:4]), done.stderr)
        self.assertEqual(next(entry for entry in log if entry['step'] == 'apply answered')['core']['error_code'], 'E_COOK_FAILED')
        self.assertFalse((Path(world.root) / 'builds' / self.name / '.prepared').exists())
        witness = next(entry for entry in log if entry['step'] == 'running deployment')
        self.assertEqual((witness['untouched_by_this_run'], witness['pointer_unit_and_process_as_before'], witness['builds_that_were_there_as_before']), (False, True, False))
        self.assertEqual(witness['builds'][self.role['request_id']]['first_changed'], [{'path': 'shared-fixture.txt', 'what': ['mode', 'links', 'ctime_ns']}])
        self.assertIn('a build that was there changed', log[-1]['why'])
        self.assertEqual(set(verbs), {'is-active', 'show'})
        done, log, verbs = self.run_mode(world, 'start')  # and it is not started
        self.assertEqual((done.returncode, self.pointers(world)[:4]), (3, before[:4]), done.stderr)

    def test_a_change_of_the_build_directory_itself_is_seen(self):
        world = self.world('build-directory')
        mode = self.live.stat().st_mode & 0o7777
        self.before_cook(world, '\nimport os\nos.chmod(%r, %d)\n' % (str(self.live), mode ^ 0o050))
        done, log, verbs = self.run_mode(world, 'prepare')
        witness = next(entry for entry in log if entry['step'] == 'running deployment')
        self.assertEqual((done.returncode, witness['untouched_by_this_run'], witness['builds_that_were_there_as_before']), (3, False, False), done.stderr)
        self.assertEqual(witness['builds'][self.role['request_id']]['first_changed'], [{'path': '.', 'what': ['mode', 'ctime_ns']}])
        self.assertIn('a build that was there changed', log[-1]['why'])

    def test_verify_sees_a_write_to_the_requested_build_when_it_runs(self):
        world = self.world('verify-running')
        for mode in ('prepare', 'start'):
            self.assertEqual(self.run_mode(world, mode)[0].returncode, 0)
        file = Path(world.root) / 'builds' / self.name / 'fixture.txt'
        file.write_text('before\n')
        # Somebody writes to the build that runs while `verify` looks: the service manager's stand-in does it, the second time it
        # is asked when the unit started. The first time is before the runner takes the builds, the second after `plan`.
        fake, flag = Path(self.document['systemctl'][0]), world.sandbox / 'asked-once'
        passing = fake.read_text().splitlines()[1]
        corpus.write(fake, '#!/bin/sh\ncase "$*" in *ExecMainStartTimestamp*) if [ -e %s ]; then echo after >%s; else : >%s; fi;; esac\n%s\n' % (flag, file, flag, passing), 0o755)
        done, log, verbs = self.run_mode(world, 'verify')
        witness = next(entry for entry in log if entry['step'] == 'running deployment')
        self.assertEqual((done.returncode, file.read_text(), witness['untouched_by_this_run'], witness['requested_build_measured']), (3, 'after\n', False, True), done.stderr)
        changed = witness['builds'][self.name]['first_changed']
        self.assertEqual(([row['path'] for row in changed], {'mtime_ns', 'ctime_ns'} <= set(changed[0]['what'])), (['fixture.txt'], True))
        self.assertTrue(witness['builds'][self.role['request_id']]['unchanged'])
        self.assertEqual(set(verbs), {'is-active', 'show'})

    def test_what_could_not_be_read_is_not_certified(self):
        world = self.world('unread')
        closed = self.live / 'closed'
        closed.mkdir()
        (closed / 'file').write_text('public synthetic fixture\n')
        closed.chmod(0)
        try:
            done, log, verbs = self.run_mode(world, 'prepare')
        finally:
            closed.chmod(0o700)
        witness = next(entry for entry in log if entry['step'] == 'running deployment')
        row = witness['builds'][self.role['request_id']]
        # Nothing changed and the two inventories are equal, marker for marker: that is not an unchanged build.
        self.assertEqual((row['changed'], row['unchanged'], row['complete'], row['first_not_read']), (0, None, False, ['closed/']))
        self.assertEqual((done.returncode, witness['untouched_by_this_run'], witness['builds_that_were_there_as_before'], witness['measurement_complete']), (3, None, None, False), done.stderr)
        self.assertTrue([line for line in witness['not_measured'] if line.startswith('what could not be read')])
        self.assertIn('could not be measured completely', log[-1]['why'])

    def connector(self, world):
        """A connector's repository at a local path, and an earlier clone of it in the build that runs, as Git makes one."""
        source = world.sandbox / 'srv/local-connector'
        env = {'HOME': str(world.home), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'GIT_CONFIG_NOSYSTEM': '1'}
        git = lambda *args: subprocess.run(['git', *args], env=env, check=True, capture_output=True)
        git('init', str(source))
        (source / 'fixture.txt').write_text('public fixture\n')
        git('-C', str(source), 'add', '.')
        git('-C', str(source), '-c', 'user.name=Public Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
        git('clone', str(source), str(self.live / 'connector'))
        linked = [path for path in (self.live / 'connector/.git/objects').rglob('*') if path.is_file() and path.stat().st_nlink > 1]
        self.assertTrue(linked)  # Git linked the objects: the premise
        return source

    CLONE = '\nimport subprocess, sys\nfrom pathlib import Path\nsubprocess.run(["git", "clone", %r, str(Path(sys.argv[sys.argv.index("--out") + 1]) / "connector")], check=True, capture_output=True)\n'

    @unittest.skipUnless(shutil.which('git'), 'no git')
    def test_a_connector_from_a_local_repository_the_recipe_names_is_built(self):
        # Cook clones a connector with a plain `git clone`, which hard-links the objects of a repository at a local path: two
        # builds then share them. The adapter tells Git, through cook's environment, to take a repository the recipe names over
        # its transport. The stand-in clones with the environment it was handed, as cook's Git does.
        # The recipe of a case names a path as the layer has it; the corpus moves it under the sandbox, as every other path.
        world = self.world('local-git', recipe=lambda document, world: document['mcpServers'].update(
            fixture={'command': 'node', 'args': ['index.js'], 'source': {'url': '/' + 'srv/local-connector', 'ref': 'main'}}))
        source = self.connector(world)
        self.assertEqual(json.loads((self.live / 'cook-recipe.json').read_text())['mcpServers']['fixture']['source']['url'], str(source))
        self.before_cook(world, self.CLONE % str(source))
        done, log, verbs = self.run_mode(world, 'prepare')
        self.assertEqual(done.returncode, 0, done.stderr)
        witness = next(entry for entry in log if entry['step'] == 'running deployment')
        shared = next(entry for entry in log if entry['step'] == 'new build')['files_with_a_name_outside_the_build']
        self.assertEqual((witness['untouched_by_this_run'], shared['outside'], shared['with_other_builds']), (True, 0, 0))
        new = Path(world.root) / 'builds' / self.name / 'connector'
        self.assertEqual((new / 'fixture.txt').read_text(), 'public fixture\n')
        origin = subprocess.run(['git', '-C', str(new), 'config', 'remote.origin.url'], capture_output=True, text=True, env={'HOME': str(world.home), 'PATH': '/usr/bin:/bin', 'GIT_CONFIG_NOSYSTEM': '1'})
        self.assertEqual(origin.stdout.strip(), str(source))  # the origin is the path, as before
        names = corpus.lines(Path(world.root) / 'cook-calls.jsonl')[-1]['environment_names']
        self.assertEqual([name for name in names if name.startswith('GIT_')], ['GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_VALUE_0'])

    @unittest.skipUnless(shutil.which('git'), 'no git')
    def test_a_local_repository_the_recipe_does_not_name_is_still_refused(self):
        # The known limit: a clone of a local repository that the recipe handed to cook does not name gets no such word, Git
        # links its objects, and the build is refused, since a file of it is a file of the build that runs.
        world = self.world('local-git-unnamed')
        source = self.connector(world)
        self.before_cook(world, self.CLONE % str(source))
        done, log, verbs = self.run_mode(world, 'prepare')
        self.assertEqual((done.returncode, next(entry for entry in log if entry['step'] == 'apply answered')['core']['error_code']), (3, 'E_COOK_FAILED'), done.stderr)
        self.assertIs(next(entry for entry in log if entry['step'] == 'running deployment')['untouched_by_this_run'], False)
        self.assertEqual([name for name in corpus.lines(Path(world.root) / 'cook-calls.jsonl')[-1]['environment_names'] if name.startswith('GIT_')], [])

    @unittest.skipUnless(shutil.which('git'), 'no git')
    def test_a_named_repository_whose_path_begins_with_another_is_cloned_from_itself(self):
        # Git holds `insteadOf` against the beginning of a URL: a rewrite for `repo` alone sends `repo%20other` to the
        # repository `repo other` (finding of the review of d454870). Each named path has an entry of its own, a `%` as `%25`.
        def recipe(document, world):
            for name, url in (('prefix', '/' + 'srv/repo'), ('literal', '/' + 'srv/repo%20other')):
                document['mcpServers'][name] = {'command': 'node', 'args': ['index.js'], 'source': {'url': url, 'ref': 'main'}}
        world = self.world('local-git-prefix', recipe=recipe)
        env = {'HOME': str(world.home), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'GIT_CONFIG_NOSYSTEM': '1'}
        git = lambda *args: subprocess.run(['git', *args], env=env, check=True, capture_output=True, text=True).stdout.strip()
        for name, word in (('repo', 'prefix'), ('repo%20other', 'literal'), ('repo other', 'decoded')):
            source = world.sandbox / 'srv' / name
            git('init', '-q', '-b', 'main', str(source))
            (source / 'fixture.txt').write_text(word + '\n')
            git('-C', str(source), 'add', '.')
            git('-C', str(source), '-c', 'user.name=Public Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', word)
        literal = world.sandbox / 'srv/repo%20other'
        self.assertEqual(json.loads((self.live / 'cook-recipe.json').read_text())['mcpServers']['literal']['source']['url'], str(literal))
        self.before_cook(world, self.CLONE % str(literal))
        done, log, verbs = self.run_mode(world, 'prepare')
        self.assertEqual(done.returncode, 0, done.stderr)
        new = Path(world.root) / 'builds' / self.name / 'connector'
        shared = next(entry for entry in log if entry['step'] == 'new build')['files_with_a_name_outside_the_build']
        self.assertEqual(((new / 'fixture.txt').read_text(), git('-C', str(new), 'config', 'remote.origin.url'), git('-C', str(new), 'rev-parse', 'HEAD'), shared['outside']),
                         ('literal\n', str(literal), git('-C', str(literal), 'rev-parse', 'HEAD'), 0))
        names = corpus.lines(Path(world.root) / 'cook-calls.jsonl')[-1]['environment_names']
        self.assertEqual([name for name in names if name.startswith('GIT_')], ['GIT_CONFIG_COUNT', 'GIT_CONFIG_KEY_0', 'GIT_CONFIG_KEY_1', 'GIT_CONFIG_VALUE_0', 'GIT_CONFIG_VALUE_1'])

    def test_a_recipe_that_refers_to_further_recipes_gets_no_rewrite(self):
        # The adapter does not know the repositories a child recipe names, and a rewrite applies to every path that begins
        # with its own: so Git gets none, and a local repository is cloned as before.
        def recipe(document, world):
            document['mcpServers']['fixture'] = {'command': 'node', 'args': ['index.js'], 'source': {'url': '/' + 'srv/local-connector', 'ref': 'main'}}
            document.setdefault('modules', {})['fleet'] = {'children': [{'name': 'child', 'recipe': 'child.json'}]}
        world = self.world('local-git-fleet', recipe=recipe)
        done, log, verbs = self.run_mode(world, 'prepare')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([name for name in corpus.lines(Path(world.root) / 'cook-calls.jsonl')[-1]['environment_names'] if name.startswith('GIT_')], [])

    def test_a_repository_a_rewrite_would_send_elsewhere_is_refused_before_cook(self):
        # A named path with a line break has no entry of its own. Where it begins with a path that has one and a `%` follows,
        # Git would decode it and look elsewhere: the adapter refuses, and cook is not started. Without the `%` it is the same path.
        calls = lambda world: len(corpus.lines(Path(world.root) / 'cook-calls.jsonl'))
        for label, tail, expected in (('refused', '\n%41', (1, 'E_ADAPTER_INPUT', 0)), ('same', '\nA', (0, None, 1))):
            def recipe(document, world):
                for name, url in (('prefix', '/' + 'srv/repo'), ('broken', '/' + 'srv/repo' + tail)):
                    document['mcpServers'][name] = {'command': 'node', 'args': ['index.js'], 'source': {'url': url, 'ref': 'main'}}
            world = self.world('local-git-misdirected-' + label, recipe=recipe)
            before = calls(world)
            done, log, verbs = self.run_mode(world, 'prepare')
            answered = next(entry for entry in log if entry['step'] == 'apply answered')['core']
            self.assertEqual((done.returncode, answered.get('error_code'), calls(world) - before), expected, done.stderr)

    @unittest.skipIf(os.geteuid() == 0, 'root reads every file')
    def test_a_dropin_that_cannot_be_read_is_not_certified(self):
        # A drop-in beside the engine's own, closed to the account: its two descriptions are equal marks, also where it was
        # written in between (finding W1-R). Nothing is seen to change, and nothing of it is known: null, and status 3.
        for label, mode, rewrite in (('prepare', 'prepare', False), ('written', 'prepare', True), ('verify', 'verify', False)):
            world = self.world('closed-dropin-' + label)
            closed = Path(world.unit_dir) / (self.unit + '.d') / '10-public-fixture.conf'
            try:
                if mode == 'verify':
                    done, log, verbs = self.run_mode(world, 'prepare')
                    self.assertEqual((done.returncode, next(entry for entry in log if entry['step'] == 'running deployment')['untouched_by_this_run']), (0, True), done.stderr)
                closed.write_text('[Service]\nNice=1\n')
                closed.chmod(0)
                if rewrite:
                    self.before_cook(world, '\nfrom pathlib import Path\np = Path(%r); p.chmod(0o600); p.write_text("[Service]\\nNice=2\\n"); p.chmod(0)\n' % str(closed))
                done, log, verbs = self.run_mode(world, mode)
                witness = next(entry for entry in log if entry['step'] == 'running deployment')
                self.assertEqual((done.returncode, witness['untouched_by_this_run'], witness['pointer_unit_and_process_as_before'], witness['measurement_complete'], witness['could_not_be_read']),
                                 (3, None, None, False, ['dropin/10-public-fixture.conf']), done.stderr)
                self.assertEqual(witness['builds_that_were_there_as_before'], True)
                self.assertIn('could not be measured completely', log[-1]['why'])
                self.assertTrue([line for line in witness['not_measured'] if line.startswith('what could not be read')])
            finally:
                if closed.exists():
                    closed.chmod(0o600)

    def test_a_process_the_service_manager_does_not_answer_for_is_not_certified(self):
        # `show` fails for the properties of the process: two empty answers are equal, and are not a process that stayed.
        for label, asked in (('all', ('MainPID', 'NRestarts', 'ExecMainStartTimestamp')), ('one', ('NRestarts',))):
            world = self.world('show-fails-' + label)
            fake = Path(self.document['systemctl'][0])
            text = fake.read_text()
            self.assertIn('\nexec ', text)
            fake.write_text(text.replace('\nexec ', '\ncase "$*" in %s) exit 1;; esac\nexec ' % '|'.join('*%s*' % name for name in asked)))
            done, log, verbs = self.run_mode(world, 'prepare')
            witness = next(entry for entry in log if entry['step'] == 'running deployment')
            self.assertEqual((done.returncode, witness['untouched_by_this_run'], witness['pointer_unit_and_process_as_before'], witness['measurement_complete'], witness['could_not_be_read']),
                             (3, None, None, False, sorted('unit property ' + name for name in asked)), done.stderr)
            self.assertIn('could not be measured completely', log[-1]['why'])

    def test_a_show_that_ends_well_without_the_number_is_not_certified(self):
        # `show` ends with status 0 and no word for a property the service manager does not have (a real systemctl does,
        # finding W1-R), or with a word that is no number. The main process id and the restart count are always numbers:
        # without one nothing is known of the process, and two equal answers of that are not a process that stayed.
        for name in ('MainPID', 'NRestarts'):
            for mode in ('prepare', 'verify'):
                for label, answer in (('empty', ''), ('word', 'echo n/a; ')):
                    world = self.world('show-%s-%s-%s' % (label, name, mode))
                    if mode == 'verify':
                        done, log, verbs = self.run_mode(world, 'prepare')
                        self.assertEqual((done.returncode, next(entry for entry in log if entry['step'] == 'running deployment')['untouched_by_this_run']), (0, True), done.stderr)
                    fake = Path(self.document['systemctl'][0])
                    text = fake.read_text()
                    self.assertIn('\nexec ', text)
                    fake.write_text(text.replace('\nexec ', '\ncase "$*" in *%s*) %sexit 0;; esac\nexec ' % (name, answer)))
                    done, log, verbs = self.run_mode(world, mode)
                    witness = next(entry for entry in log if entry['step'] == 'running deployment')
                    moments = [next(entry for entry in log if entry['step'] == step) for step in ('before', 'after')]
                    self.assertEqual([(moment['unit_state'][name], moment['unit_state_not_answered']) for moment in moments], [(answer and 'n/a', [name])] * 2)
                    self.assertEqual((done.returncode, witness['untouched_by_this_run'], witness['pointer_unit_and_process_as_before'], witness['measurement_complete'], witness['could_not_be_read']),
                                     (3, None, None, False, ['unit property ' + name]), done.stderr)
                    self.assertEqual(witness['builds_that_were_there_as_before'], True)
                    self.assertIn('could not be measured completely', log[-1]['why'])

    def test_a_number_is_an_answer_and_an_empty_time_stamp_is_one(self):
        # 0 is a value of both numbers (no main process, never restarted), and a time stamp that is not set is empty and
        # answered: the witness is complete with them, in `prepare` and in `verify`.
        world = self.world('show-zero')
        fake = Path(self.document['systemctl'][0])
        fake.write_text(fake.read_text().replace('\nexec ', '\ncase "$*" in *MainPID*|*NRestarts*) echo 0; exit 0;; *ExecMainStartTimestamp*) exit 0;; esac\nexec '))
        for mode in ('prepare', 'verify'):
            done, log, verbs = self.run_mode(world, mode)
            witness = next(entry for entry in log if entry['step'] == 'running deployment')
            before = next(entry for entry in log if entry['step'] == 'before')
            self.assertEqual(([before['unit_state'][name] for name in ('MainPID', 'NRestarts', 'ExecMainStartTimestamp')], before['unit_state_not_answered']), (['0', '0', ''], []))
            self.assertEqual((done.returncode, witness['untouched_by_this_run'], witness['pointer_unit_and_process_as_before'], witness['measurement_complete'], witness['could_not_be_read']),
                             (0, True, True, True, []), done.stderr)

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
        # The mode of a first start: what `start` passes, and `stop` of this one unit, with which the engine takes a failed
        # first start back. Nothing else: no stop of another unit, and none of the verbs `start` refuses.
        passed = questions + (['--user', 'daemon-reload'], ['--user', 'restart', 'a.service'], ['--user', 'stop', 'a.service'])
        for args in passed:
            self.assertEqual(status('first-start', *args), 0, args)
        first = [('stop', 'b.service'), ('stop', 'a.service', 'b.service'), ('stop',), ('restart', 'b.service'), ('daemon-reload', 'a.service')]
        first += [(verb, 'a.service') for verb in ('start', 'enable', 'disable', 'reload-or-restart', 'try-restart', 'mask', 'kill', 'isolate', 'edit', 'link', 'set-property')]
        for args in first:
            self.assertEqual(status('first-start', '--user', *args), 97, args)
        self.assertEqual(len(called.read_text().splitlines()), 8 + len(passed))
        self.assertEqual(len(log.read_text().splitlines()), 8 + len(refusals) + len(passed) + len(first))


if __name__ == '__main__':
    unittest.main()
