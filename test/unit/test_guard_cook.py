"""Purpose-built killing tests: cook adapter, config layers and archive guards."""
import contextlib, hashlib, importlib.util, io, json, sys, tarfile, unittest
from collections import UserDict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'oracle/module_utils'),str(ROOT/'oracle/tests')]
import connectome_archive as archive
import connectome_config as layers
import connectome_cook as cook
import connectome_lock as locks
from connectome_errors import SafeError, diagnostic
import test_cook

ARGV = 'command arguments contain invalid or credential-shaped values'
OVERLAP = 'secret names overlap adapter-owned environment'
OUTSIDE = 'command dependency outside build and pinned platform closure'


class Guards(unittest.TestCase):
    # ---- helpers -------------------------------------------------------
    def refused(self, cls, message, code, call, *args, **kwargs):
        """One declared refusal: exact class, the guard's own message and closed code."""
        with self.assertRaises((SafeError, locks.LockError)) as caught:
            call(*args, **kwargs)
        error = caught.exception
        self.assertEqual((type(error), str(error), getattr(error, 'code', None)), (cls, message, code))

    def adapter(self):
        fixture = test_cook.AdapterTests('test_framework_hash_and_commit_verified')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def adapt(self, fixture, spec, build=None, name='deployment.json', **overrides):
        build = build or fixture.build
        path = build / name
        path.write_text(json.dumps(spec))
        arguments = dict(spec_path=str(path), build_dir=str(build), state_dir=spec['config']['state_dir'],
                         runtime=sys.executable, entrypoint='/fixture/source/bin/cook',
                         bun_path=sys.executable, node_path=sys.executable)
        arguments.update(overrides)
        return cook.adapt(**arguments)

    @contextlib.contextmanager
    def cooked(self, fixture, spec, build=None, change=None):
        """Unit double for the two child-process collaborators only (git verification
        and the cook CLI): it writes the lock cook writes for exactly this request."""
        build = build or fixture.build
        calls = []
        def child(argv, cwd, env, flag_indices):
            cook.validate_arguments(argv, flag_indices)
            calls.append(argv)
            layout = json.loads((build / 'cook-layout.json').read_text())
            record = test_cook.fixture()
            record['build']['dir'] = str(build)
            record['launch']['script'] = str(build / 'run.sh')
            (build / 'run.sh').write_text('#!/bin/sh\nexit 0\n')
            record['layout'], record['state'] = layout, {'path': layout['state']}
            snapshot = str(build / 'framework.tgz')
            record['hostDependencies'] = [{
                'name': cook.FRAMEWORK, 'sha256': spec['config']['cook']['framework_archive']['sha256'],
                'originalRange': '^1.0.0', 'version': '1.0.0', 'size': 123, 'snapshot': snapshot,
                'packageJsonDelta': {
                    'direct': {'section': 'dependencies', 'name': cook.FRAMEWORK,
                               'before': '^1.0.0', 'after': 'file:' + snapshot},
                    'override': {'section': 'overrides', 'name': cook.FRAMEWORK,
                                 'before': None, 'after': 'file:' + snapshot}}}]
            record['components'] = [{
                'kind': 'git', 'key': name, 'role': 'mcp', 'url': entry['repo'],
                'ref': spec['pins']['connectors'][name], 'commit': spec['pins']['connectors'][name],
                'path': str(build / 'connectors' / name), 'install': 'make build'}
                for name, entry in spec['config']['connectors'].items()]
            if change:
                change(record, layout)
            (build / 'connectome.lock').write_text(json.dumps(record))
        with patch.object(cook, 'verify_cook'), patch.object(cook, 'command', side_effect=child):
            yield calls

    def operator(self, fixture, content=b'{"name": "example"}'):
        """A spec whose recipe is the operator's own file (recipe_file mode)."""
        spec = fixture.spec()
        path = fixture.work / 'operator.json'
        path.write_bytes(content)
        spec['config']['recipe_file'] = {'path': str(path), 'sha256': hashlib.sha256(content).hexdigest()}
        spec['config'].pop('agent')
        spec['pins']['connectors'] = {}
        return spec, path

    def checkout(self, directory):
        entry = directory / 'bin/cook'
        entry.parent.mkdir(parents=True)
        entry.write_text('fixture\n')
        return entry

    def main(self, **options):
        """The adapter CLI boundary: exit status and the closed stderr envelope."""
        argv = ['cook-adapter']
        for key, value in options.items():
            argv += ['--' + key.replace('_', '-'), value]
        stream = io.StringIO()
        with patch.object(sys, 'argv', argv), contextlib.redirect_stderr(stream):
            try:
                cook.main()
                status = 0
            except SystemExit as stop:
                status = stop.code
        return status, stream.getvalue()

    def catalog(self):
        import test_catalog
        fixture = test_catalog.CatalogTests('test_literal_local_argv_and_unit_cwd_are_accepted')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def catalog_inputs(self, fixture, directory=None):
        entry = (directory or fixture.root / 'inputs/cook') / 'bin/cook'
        entry.parent.mkdir(parents=True)
        entry.write_bytes((ROOT / 'oracle/tests/fixtures/catalog-cook.py').read_bytes())
        Path(fixture.config['cook']['host_repo']).mkdir()
        return entry

    def catalog_options(self, fixture, entry):
        path = fixture.build / 'deployment.json'
        path.write_text(json.dumps(fixture.spec()))
        return dict(spec=str(path), out=str(fixture.build), state=fixture.config['state_dir'],
                    runtime=sys.executable, entrypoint=str(entry), bun_path=sys.executable,
                    node_path=sys.executable)

    def catalog_adapt(self, fixture, entry):
        options = self.catalog_options(fixture, entry)
        return cook.adapt(options['spec'], options['out'], options['state'], options['runtime'],
                          options['entrypoint'], options['bun_path'], options['node_path'])

    def plan_receipt(self, write):
        """Contract shim boundary: SafeError keeps its closed code, anything else is E_DEPLOY_FAILED."""
        import test_deploy
        fixture = test_deploy.DeployTests('test_install_only_static_health_and_status')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        spec = importlib.util.spec_from_file_location('shim', ROOT / 'oracle/shim/kitchen_v1.py')
        shim = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shim)
        path = Path(fixture.tmp.name) / 'framework.tgz'
        write(path)
        config = dict(fixture.config, cook={
            'host_repo': 'https://example.invalid/host.git', 'connector_state': {},
            'framework_archive': {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}})
        request = {'root': str(fixture.root), 'unit_dir': str(fixture.units), 'config': config,
                   'versions': fixture.versions, 'systemctl': fixture.systemctl,
                   'engines': {'cook': {key: '/bin/true' for key in ('entrypoint', 'runtime', 'bun', 'node')}}}
        return shim.dispatch({'contract': shim.CONTRACT, 'command': 'plan', 'request': request})

    def package(self, path, manifests):
        with tarfile.open(path, 'w:gz', format=tarfile.USTAR_FORMAT) as stream:
            for name, content in manifests.items():
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(content), 0o644
                stream.addfile(member, io.BytesIO(content))
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def dependency(self, **changes):
        return dict({'name': 'example-dependency', 'archive': '/fixture/dependency.tgz', 'sha256': 'a' * 64,
                     'kind': 'artifact', 'version': '1.0.0'}, **changes)

    # ---- archive -------------------------------------------------------
    def test_archive_L74_8(self):
        self.refused(archive.ArchiveError, 'commit required only for git_commit', 'E_ADAPTER_INPUT',
                     archive.validate_dependency, self.dependency(commit='d' * 40))

    def test_archive_L79_8(self):
        self.refused(archive.ArchiveError, 'registry identity is allowed only for registry_release',
                     'E_ADAPTER_INPUT', archive.validate_dependency,
                     self.dependency(registry_url='https://registry.example.invalid'))

    def test_archive_L95_8(self):
        artifact = {'path': '/fixture/framework.tgz', 'sha256': 'a' * 64, 'registry_release': True,
                    'name': 'different', 'version': '1.0.0'}
        self.refused(archive.ArchiveError, 'registry release requires explicit name and version',
                     'E_ARCHIVE_IDENTITY', archive.legacy_dependency,
                     {'cook': {'framework_archive': artifact}}, 'd' * 40)

    def test_archive_L99_8(self):
        artifact = {'path': '/fixture/framework.tgz', 'sha256': 'a' * 64, 'version': '1.0.0'}
        self.refused(archive.ArchiveError, 'release identity requires registry_release: true',
                     'E_ADAPTER_INPUT', archive.legacy_dependency,
                     {'cook': {'framework_archive': artifact}}, 'd' * 40)

    def test_archive_L134_12(self):
        fixture = self.adapter()
        manifest = json.dumps({'name': 'example-dependency', 'version': '1.0.0'}).encode()
        path = fixture.work / 'two-manifests.tgz'
        digest = self.package(path, {'package/package.json': manifest, 'other/package.json': manifest})
        self.refused(archive.ArchiveError, 'archive manifest required', 'E_ADAPTER_INPUT',
                     archive.archive_manifest, self.dependency(archive=str(path), sha256=digest))

    def test_archive_L139_8(self):
        # Module level the mutant only crashes (unbound manifest), so assert the closed code
        # at the contract boundary, where a refusal and a crash differ.
        receipt, status = self.plan_receipt(lambda path: path.write_bytes(b'not an archive'))
        self.assertEqual((status, receipt['core']), (1, dict(diagnostic('E_ADAPTER_INPUT'), phase='prepare')))

    def test_archive_L140_4(self):
        # A JSON manifest that is not an object has no .get; assert the closed code at the
        # contract boundary instead (refusal E_ADAPTER_INPUT, crash E_DEPLOY_FAILED).
        receipt, status = self.plan_receipt(
            lambda path: self.package(path, {'package/package.json': b'["not", "a", "mapping"]'}))
        self.assertEqual((status, receipt['core']), (1, dict(diagnostic('E_ADAPTER_INPUT'), phase='prepare')))

    def test_archive_L193_4(self):
        # No JSON non-mapping has .get/.items, so assert at the lock reader, which maps the
        # guard's refusal and a crash to two different closed messages.
        fixture = test_cook.LockTests('test_recorded_v2_accepted_and_mapped')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        record = test_cook.fixture()
        record['hostDependencies'] = [{
            'name': 'example-dependency', 'originalRange': '^1.0.0', 'version': '1.0.0', 'sha256': 'd' * 64,
            'size': 123, 'snapshot': '/fixture/example.tgz', 'provenance': ['artifact'],
            'packageJsonDelta': {
                'direct': {'section': 'dependencies', 'name': 'example-dependency',
                           'before': '^1.0.0', 'after': 'file:/fixture/example.tgz'},
                'override': {'section': 'overrides', 'name': 'example-dependency',
                             'before': None, 'after': 'file:/fixture/example.tgz'}}}]
        self.refused(locks.LockError, 'invalid host dependency provenance', None,
                     locks.read_lock, fixture.record(record))

    # ---- config layers -------------------------------------------------
    def test_config_L60_8(self):
        with self.assertRaisesRegex(ValueError, '^public connectors belong in the catalog$'):
            layers.public_catalog({'connectors': {'extra': {'repo': 'https://example.invalid/extra.git'}}}, {})

    def test_config_layers_public_connectors_1(self):
        # Even an empty connectors mapping in the public defaults is refused, not replaced.
        with self.assertRaisesRegex(ValueError, '^public connectors belong in the catalog$'):
            layers.public_catalog({'name': 'example', 'connectors': {}},
                                  {'extra': {'repo': 'https://example.invalid/extra.git'}})

    def test_config_L46_12(self):  # non-JSON input
        # Every JSON non-mapping lacks .values(), so the mutant would only crash; a
        # mapping that is not a dict is the smallest input it tolerates.
        entries = UserDict({'extra': {'enabled': True}})
        with self.assertRaisesRegex(ValueError, '^connectors and modules must be mappings$'):
            layers.merge_layers([{'connectors': entries}, {}, {}, {}])

    def test_config_layers_fold_1(self):
        # The tombstone in layer 2 must also bury layer 1's keys under layer 4's mapping.
        result = layers.merge_layers([{'agent': {'model': 'lower', 'systemPrompt': 'lower'}},
                                      {'agent': None}, {}, {'agent': {'temperature': 1}}])
        self.assertEqual(result['agent'], {'temperature': 1})

    def test_config_layers_prune_1(self):
        result = layers.merge_layers([
            {'connectors': {'extra': {'enabled': True, 'repo': 'https://example.invalid/extra.git'},
                            'kept': {'repo': 'https://example.invalid/kept.git'}},
             'modules': {'lessons': {'enabled': True}}}, {}, {},
            {'connectors': {'extra': {'enabled': False}}, 'modules': {'lessons': {'enabled': False}}}])
        self.assertEqual((sorted(result['connectors']), result['modules']), (['kept'], {}))

    # ---- cook: argv ----------------------------------------------------
    def test_cook_argv_dash_1(self):
        self.refused(cook.AdapterError, ARGV, 'E_ADAPTER_INPUT', cook.validate_arguments, ['tool', '-value'])

    def test_cook_argv_flag_position_1(self):
        # Index 1 is the recorded flag; the equal value at index 2 is an operand.
        self.assertIsNone(cook.validate_arguments(['tool', '--flag', 'value'], (1,)))
        self.refused(cook.AdapterError, ARGV, 'E_ADAPTER_INPUT',
                     cook.validate_arguments, ['tool', '--flag', '--flag'], (1,))

    def test_cook_argv_validate_first_1(self):
        # Each field is clean on its own; only the assembled NAME=ARCHIVE operand is
        # credential-shaped, so the whole-argv validation is the refusing check. It must
        # refuse before the first child process (the Git verification of the checkout).
        fixture = self.adapter()
        spec = fixture.spec()
        name = 'example-token'
        artifact = test_cook.framework_archive(fixture.work / 'dependency.tgz', None, name=name)
        spec['config']['cook']['host_dependencies'] = [{
            'name': name, 'archive': artifact['path'], 'sha256': artifact['sha256'],
            'kind': 'artifact', 'version': '1.0.0'}]
        entry = self.checkout(fixture.work / 'source')
        started = []
        def child(argv, **options):
            started.append(argv[0])
            return SimpleNamespace(returncode=0, stdout=b'' if 'status' in argv else cook.COOK_COMMIT.encode())
        with patch.object(cook.subprocess, 'run', side_effect=child):
            self.refused(cook.AdapterError, ARGV, 'E_ADAPTER_INPUT',
                         self.adapt, fixture, spec, entrypoint=str(entry))
        self.assertEqual(started, [])

    def test_cook_L52_12(self):
        # The mutant leaves the result unbound, so assert the closed code at the adapter
        # CLI boundary: unavailable command E_COOK_FAILED, crash E_ADAPTER_INPUT.
        fixture = self.adapter()
        spec = fixture.spec()
        path = fixture.build / 'deployment.json'
        path.write_text(json.dumps(spec))
        entry = self.checkout(fixture.work / 'source')
        with patch.object(cook.subprocess, 'run', side_effect=OSError('unavailable')) as run:
            status, output = self.main(spec=str(path), out=str(fixture.build), state=spec['config']['state_dir'],
                                       runtime=sys.executable, entrypoint=str(entry),
                                       bun_path=sys.executable, node_path=sys.executable)
        self.assertEqual(run.call_count, 1)
        self.assertEqual((status, json.loads(output)), (1, diagnostic('E_COOK_FAILED')))

    # ---- cook: paths and checkout --------------------------------------
    def test_cook_L67_4(self):
        self.refused(cook.AdapterError, 'invalid or credential-shaped adapter path', 'E_ADAPTER_INPUT',
                     cook.absolute_path, '/fixture/' + 'gh' + 's_' + 'A' * 32)

    def test_cook_input_paths_1(self):
        for value in ('/fixture/path?query', '/fixture/path#fragment'):
            self.refused(cook.AdapterError, 'invalid or credential-shaped adapter path', 'E_ADAPTER_INPUT',
                         cook.absolute_path, value)

    def test_cook_L70_4(self):
        self.refused(cook.AdapterError, 'absolute adapter path required', 'E_ADAPTER_INPUT',
                     cook.absolute_path, 'relative/path')

    def test_cook_L76_4(self):
        fixture = self.adapter()
        entry = fixture.work / 'source/bin/cook'
        entry.mkdir(parents=True)  # the right place, but not a file
        git = lambda argv, **options: b'' if 'status' in argv else cook.COOK_COMMIT.encode()
        with patch.object(cook, 'command', side_effect=git):
            self.refused(cook.AdapterError, 'absolute cook entrypoint required', 'E_ADAPTER_INPUT',
                         cook.verify_cook, str(entry), cook.COOK_COMMIT, {})

    def test_cook_L78_4(self):
        fixture = self.adapter()
        entry = fixture.work / 'source/tools/cook'
        entry.parent.mkdir(parents=True)
        entry.write_text('fixture\n')
        git = lambda argv, **options: b'' if 'status' in argv else cook.COOK_COMMIT.encode()
        with patch.object(cook, 'command', side_effect=git):
            self.refused(cook.AdapterError, 'use the reviewed source bin/cook entrypoint', 'E_ADAPTER_INPUT',
                         cook.verify_cook, str(entry), cook.COOK_COMMIT, {})

    def test_cook_L223_4(self):
        fixture = self.adapter()
        spec = fixture.spec()
        alias = fixture.work / 'state-alias'
        alias.symlink_to(fixture.work / 'state')
        spec['config']['state_dir'] = str(alias)
        with self.cooked(fixture, spec) as calls:
            self.refused(cook.AdapterError, 'adapter paths must be absolute without symlink aliases',
                         'E_ADAPTER_INPUT', self.adapt, fixture, spec)
        self.assertEqual(calls, [])

    def test_cook_L225_4(self):
        fixture = self.adapter()
        spec = fixture.spec()
        with self.cooked(fixture, spec) as calls:
            self.refused(cook.AdapterError, 'fresh deployment.json required', 'E_ADAPTER_INPUT',
                         self.adapt, fixture, spec, name='request.json')
        self.assertEqual(calls, [])

    def test_cook_L226_4(self):
        fixture = self.adapter()
        spec = fixture.spec()
        build = fixture.work / 'deployment/releases/request'
        build.mkdir(parents=True)
        with self.cooked(fixture, spec, build=build) as calls:
            self.refused(cook.AdapterError, 'adapter output must be ROOT/builds/REQUEST', 'E_ADAPTER_INPUT',
                         self.adapt, fixture, spec, build=build)
        self.assertEqual(calls, [])

    def test_cook_L228_4(self):
        fixture = self.adapter()
        spec = fixture.spec()
        spec['config']['state_dir'] = str(fixture.work / 'deployment/state')
        with self.cooked(fixture, spec) as calls:
            self.refused(cook.AdapterError, 'state must be separate from deployment root/builds',
                         'E_ADAPTER_INPUT', self.adapt, fixture, spec)
        self.assertEqual(calls, [])

    def test_cook_L235_8(self):
        fixture = self.adapter()
        spec = fixture.spec()
        plain = fixture.work / 'runtime'
        plain.write_text('not executable\n')
        plain.chmod(0o600)
        with self.cooked(fixture, spec) as calls:
            self.refused(cook.AdapterError, 'absolute executable runtime, Bun and Node paths required',
                         'E_ADAPTER_INPUT', self.adapt, fixture, spec, runtime=str(plain))
        self.assertEqual(calls, [])

    # ---- cook: recipe translation --------------------------------------
    def test_cook_L117_4(self):
        spec = self.adapter().spec()
        spec['extra'] = {}
        self.refused(cook.AdapterError, 'normalized deployment spec required', 'E_ADAPTER_INPUT',
                     cook.recipe_from_spec, spec)

    def test_cook_L123_8(self):
        # The reference resolves to a full commit and the pins are a fresh resolution of
        # it; only the rule that a connector names its own pin refuses.
        spec = self.adapter().spec()
        spec['config']['connectors']['extra']['pin_ref'] = 'runtime.cook'
        spec['pins']['connectors']['extra'] = cook.COOK_COMMIT
        self.refused(cook.AdapterError, 'connector pin_ref must name its resolved connector', 'E_ADAPTER_INPUT',
                     cook.recipe_from_spec, spec)

    def test_cook_L134_12(self):
        # After the mutant swallows the read failure, the next declared refusal (a reserved
        # secret name) answers instead; unmutated, the read failure is reported first.
        fixture = self.adapter()
        spec, _ = self.operator(fixture)
        spec['config']['recipe_file']['sha256'] = 'a' * 64
        spec['config']['required_secrets'] = ['PATH']
        self.refused(cook.AdapterError, 'recipe digest mismatch', 'E_ADAPTER_INPUT',
                     cook.recipe_from_spec, spec)

    def test_cook_L136_8(self):
        # PATH is a valid direct secret name for the role; the adapter owns it.
        fixture = self.adapter()
        spec, _ = self.operator(fixture)
        spec['config']['required_secrets'] = ['PATH']
        self.refused(cook.AdapterError, OVERLAP, 'E_ADAPTER_INPUT', cook.recipe_from_spec, spec)

    def test_cook_L188_4(self):
        spec = self.adapter().spec()
        spec['config']['required_secrets'] = ['HOME']
        self.refused(cook.AdapterError, OVERLAP, 'E_ADAPTER_INPUT', cook.recipe_from_spec, spec)

    def test_cook_L140_4(self):
        spec = self.adapter().spec()
        spec['config']['agent'] = {'systemPrompt': ['not', 'text']}
        self.refused(cook.AdapterError, 'agent.systemPrompt must be a string when present', 'E_ADAPTER_INPUT',
                     cook.recipe_from_spec, spec)

    def test_cook_L150_12(self):
        spec = self.adapter().spec()
        spec['config']['modules']['lessons']['settings'] = ['native']
        self.refused(cook.AdapterError, 'descriptor settings must be a mapping', 'E_ADAPTER_INPUT',
                     cook.recipe_from_spec, spec)

    def test_cook_L157_16(self):
        spec = self.adapter().spec()
        spec['config']['connectors']['extra']['settings']['token'] = 'literal-value'
        self.refused(cook.AdapterError, 'connector token must be a secret-name reference', 'E_ADAPTER_INPUT',
                     cook.recipe_from_spec, spec)

    def test_cook_L208_4(self):  # non-JSON input
        # No JSON non-mapping has .items(); a mapping that is not a dict is the smallest
        # input the mutated code tolerates.
        servers = UserDict({'extra': {'command': '/fixture/server', 'source': {'external': True}}})
        self.refused(cook.AdapterError, 'recipe mcpServers must be a mapping', 'E_ADAPTER_INPUT',
                     cook.external_servers, {'mcpServers': servers})

    # ---- cook: operator recipe -----------------------------------------
    def test_cook_L251_16(self):
        fixture = self.adapter()
        spec, path = self.operator(fixture)
        (fixture.build / 'cook-recipe.json').write_bytes(b'{"name": "other"}')
        path.unlink()
        self.refused(cook.AdapterError, 'invalid staged recipe', 'E_ADAPTER_INPUT', self.adapt, fixture, spec)

    def test_cook_L257_16(self):
        fixture = self.adapter()
        spec, path = self.operator(fixture)
        path.unlink()
        self.refused(cook.AdapterError, 'invalid recipe input', 'E_ADAPTER_INPUT', self.adapt, fixture, spec)

    def test_cook_input_staged_recipe_1(self):
        # The controller file is intact; only the staged copy in the build differs.
        fixture = self.adapter()
        spec, _ = self.operator(fixture)
        (fixture.build / 'cook-recipe.json').write_bytes(b'{"name": "other"}')
        self.refused(cook.AdapterError, 'invalid staged recipe', 'E_ADAPTER_INPUT', self.adapt, fixture, spec)

    # ---- cook: settings and lock binding -------------------------------
    def test_cook_L261_4(self):
        fixture = self.adapter()
        spec = fixture.spec()
        with self.cooked(fixture, spec) as calls:
            self.refused(cook.AdapterError, 'adapter state must match configured state_dir', 'E_ADAPTER_INPUT',
                         self.adapt, fixture, spec, state_dir=str(fixture.work / 'other-state'))
        self.assertEqual(calls, [])

    def test_cook_L264_4(self):
        fixture = self.adapter()
        spec = fixture.spec()
        spec['config']['cook']['allow_scripts'] = True
        with self.cooked(fixture, spec) as calls:
            self.refused(cook.AdapterError, 'cook requires host_repo, local dependencies and explicit connector_state',
                         'E_ADAPTER_INPUT', self.adapt, fixture, spec)
        self.assertEqual(calls, [])

    def test_cook_L268_4(self):
        fixture = self.adapter()
        spec = fixture.spec()
        spec['config']['cook']['host_repo'] = 'relative/host'
        with self.cooked(fixture, spec) as calls:
            self.refused(cook.AdapterError,
                         'cook.host_repo requires a safe HTTPS, SSH/scp or absolute local repository',
                         'E_ADAPTER_INPUT', self.adapt, fixture, spec)
        self.assertEqual(calls, [])

    def test_cook_L336_4(self):
        fixture = self.adapter()
        spec = fixture.spec()
        def change(record, layout):
            record['state'] = {'path': '/fixture/other-state'}
        with self.cooked(fixture, spec, change=change) as calls:
            self.refused(cook.AdapterError, 'cook lock did not preserve the requested persistent layout',
                         'E_LOCK_STATE_PATH', self.adapt, fixture, spec)
        self.assertEqual(len(calls), 1)

    def test_cook_L355_4(self):
        # The later config-bound lock reader refuses the same inventory with its own
        # LockError; the adapter's refusal is the earlier, differently typed one.
        fixture = self.adapter()
        spec = fixture.spec()
        def change(record, layout):
            record['hostDependencies'] = []
        with self.cooked(fixture, spec, change=change):
            self.refused(cook.AdapterError, 'cook dependency inventory differs from inputs', 'E_LOCK_INVALID',
                         self.adapt, fixture, spec)

    def test_cook_L360_8(self):
        fixture = self.adapter()
        spec = fixture.spec()
        def change(record, layout):
            record['hostDependencies'][0]['version'] = '2.0.0'
        with self.cooked(fixture, spec, change=change):
            self.refused(cook.AdapterError, 'cook dependency identity differs from inputs', 'E_LOCK_INVALID',
                         self.adapt, fixture, spec)

    def test_cook_L374_4(self):
        fixture = self.adapter()
        spec = fixture.spec()
        def change(record, layout):
            Path(layout['config']).mkdir(parents=True)
            (Path(layout['config']) / '.env').write_text('EXAMPLE_SETTING=value\n')
        with self.cooked(fixture, spec, change=change):
            self.refused(cook.AdapterError, 'cook unexpectedly wrote runtime env values', 'E_ADAPTER_INPUT',
                         self.adapt, fixture, spec)

    # ---- cook: catalog mode --------------------------------------------
    def relaunch(self, fixture, **settings):
        """Replace the catalog connector's launch settings and re-pin its descriptor."""
        fixture.config['connectors']['extra']['settings'] = settings
        fixture.repin()
        return fixture.spec()

    def test_cook_L168_8(self):
        # No artifact reference needs the build path here, so without the guard the
        # connector's own launch rule is the next refusal.
        fixture = self.catalog()
        spec = self.relaunch(fixture, command='node')
        self.refused(cook.AdapterError, 'catalog compilation requires the agent build path', 'E_ADAPTER_INPUT',
                     cook.recipe_from_spec, spec)

    def test_cook_L172_16(self):
        fixture = self.catalog()
        spec = self.relaunch(fixture, command='node')
        self.refused(cook.AdapterError, 'catalog connector requires launch settings without source',
                     'E_ADAPTER_INPUT', cook.recipe_from_spec, spec, build_dir=fixture.build)

    def test_cook_L174_16(self):
        fixture = self.catalog()
        spec = self.relaunch(fixture, command='{artifact:node}/bin/node', args=['{artifact:extra}/server.py'],
                             token='literal-value')
        self.refused(cook.AdapterError, 'catalog connector token must be a secret-name reference',
                     'E_ADAPTER_INPUT', cook.recipe_from_spec, spec, build_dir=fixture.build)

    def test_cook_L349_8(self):
        fixture = self.catalog()
        entry = self.catalog_inputs(fixture)
        run = cook.command
        def child(argv, **options):
            run(argv, **options)
            path = fixture.build / 'connectome.lock'
            record = json.loads(path.read_text())
            record['components'] = [{
                'kind': 'git', 'key': 'extra', 'role': 'mcp', 'url': 'https://example.invalid/extra.git',
                'ref': 'e' * 40, 'commit': 'e' * 40, 'path': str(fixture.build / 'connectors/extra'),
                'install': ''}]
            path.write_text(json.dumps(record))
        with patch.object(cook, 'verify_cook'), patch.object(cook, 'command', side_effect=child):
            self.refused(cook.AdapterError, 'catalog components must come from the digest-pinned closure',
                         'E_ADAPTER_INPUT', self.catalog_adapt, fixture, entry)

    def test_catalog_contained_checkout_1(self):
        fixture = self.catalog()
        entry = self.catalog_inputs(fixture, fixture.work / 'elsewhere/cook')
        with patch.object(cook, 'verify_cook') as verify:
            self.refused(cook.AdapterError, 'cook checkout must belong to agent without aliases',
                         'E_CATALOG_INVALID', self.catalog_adapt, fixture, entry)
        verify.assert_not_called()

    def staged_run(self):
        fixture = self.catalog()
        entry = self.catalog_inputs(fixture)
        calls, run = [], cook.command
        def child(argv, **options):
            calls.append(argv)
            return run(argv, **options)
        with patch.object(cook, 'verify_cook') as verify, patch.object(cook, 'command', side_effect=child):
            result = self.main(**self.catalog_options(fixture, entry))
        return fixture, result, calls, verify

    def test_catalog_stage_staged_exec_1(self):
        # Asserted at the CLI boundary: with the input checkout left in the code position
        # the adapter's own audit answers with a closed diagnostic instead of a run.
        fixture, result, calls, verify = self.staged_run()
        staged = str(fixture.build / 'cook-source/bin/cook')
        self.assertEqual(result, (0, ''))
        self.assertEqual([argv[1] for argv in calls], [staged])
        self.assertEqual(verify.call_args.args[0], staged)
        self.assertTrue(Path(staged).is_file())

    def test_catalog_stage_staged_exec_2(self):
        fixture, result, calls, _ = self.staged_run()
        staged = str(fixture.build / 'host-source')
        self.assertEqual(result, (0, ''))
        self.assertEqual([argv[argv.index('--ch-repo') + 1] for argv in calls], [staged])
        self.assertTrue(Path(staged).is_dir())

    def test_catalog_audit_site_cook_argv_1(self):
        # A comma is a legal archive file name for every earlier check; only the audit
        # of the assembled argv refuses it as an ambiguous path operand.
        fixture = self.catalog()
        entry = self.catalog_inputs(fixture)
        name = 'example-dependency'
        artifact = test_cook.framework_archive(fixture.root / 'inputs/dependency,one.tgz', None, name=name)
        fixture.config['cook']['host_dependencies'] = [{
            'name': name, 'archive': artifact['path'], 'sha256': artifact['sha256'],
            'kind': 'artifact', 'version': '1.0.0'}]
        with patch.object(cook, 'verify_cook') as verify:
            self.refused(cook.AdapterError, 'ambiguous command path', 'E_CATALOG_INVALID',
                         self.catalog_adapt, fixture, entry)
        verify.assert_not_called()

    def test_catalog_audit_site_cook_argv_2(self):
        # Staging always yields build paths, so this audit is only observable when the
        # staging collaborator hands back the input checkout (inside the agent tree,
        # which the argv audit accepts, but outside the staged build).
        fixture = self.catalog()
        entry = self.catalog_inputs(fixture)
        with patch.object(cook, 'verify_cook') as verify, \
                patch.object(cook.catalog, 'stage_checkout', side_effect=lambda source, target: source):
            self.refused(cook.AdapterError, OUTSIDE, 'E_CATALOG_INVALID', self.catalog_adapt, fixture, entry)
        verify.assert_not_called()

    def test_catalog_audit_site_cook_argv_3(self):
        # The staged host checkout carries a script whose interpreter is outside the
        # closure. Post-build verification would refuse it too, but only after cook ran.
        fixture = self.catalog()
        entry = self.catalog_inputs(fixture)
        (Path(fixture.config['cook']['host_repo']) / 'tool').write_text('#!/fixture/interpreter\n')
        calls, run = [], cook.command
        def child(argv, **options):
            calls.append(argv)
            return run(argv, **options)
        with patch.object(cook, 'verify_cook') as verify, patch.object(cook, 'command', side_effect=child):
            self.refused(cook.AdapterError, OUTSIDE, 'E_CATALOG_INVALID', self.catalog_adapt, fixture, entry)
        verify.assert_not_called()
        self.assertEqual(calls, [])
        self.assertFalse((fixture.build / 'connectome.lock').exists())


if __name__ == '__main__':
    unittest.main()
