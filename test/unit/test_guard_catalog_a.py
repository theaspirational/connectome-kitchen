"""Purpose-built killing tests for the catalog guards in connectome_catalog.py, lines 1-420.

Every test calls real oracle code with one crafted input and asserts the guard's
own refusal (class, message and closed code) or the file state it protects.
Type guards whose mutant can only crash are asserted at the adapter's closed
diagnostic boundary (cook.main), with the JSON request written to disk.
"""
import contextlib, io, json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'oracle/module_utils'),str(ROOT/'oracle/tests')]
import connectome_catalog as catalog
import connectome_cook as cook
from connectome_archive import ArchiveError
from connectome_errors import diagnostic

REPOSITORY = 'https://example.invalid/connector.git'


def mode(path):
    return path.lstat().st_mode & 0o777


class Guards(unittest.TestCase):
    # ---- fixtures -------------------------------------------------------
    def tree(self):
        """Fresh WORK/agent/builds/request and a separate WORK/state path."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name).resolve()
        self.addCleanup(self.thaw, work)
        root = work / 'agent'
        build = root / 'builds/request'
        build.mkdir(parents=True)
        build.chmod(0o755)
        return work, root, build, work / 'state'

    @staticmethod
    def thaw(work):
        for path in [work, *work.rglob('*')]:
            if not path.is_symlink() and path.is_dir():
                path.chmod(0o700)

    @staticmethod
    def add(config, pins, name, entry, version='1.0.0'):
        connector = entry.get('kind', 'connector') == 'connector'
        entry['pin_ref'] = ('connectors.' if connector else 'artifacts.') + name
        config['connectors' if connector else 'catalog'][name] = entry
        pin = {'archive': 'inputs/' + name + '.tar.gz', 'sha256': 'a' * 64, 'version': version,
               'descriptor_sha256': catalog.descriptor_digest(entry)}
        if connector:
            pin['commit'] = 'e' * 40
        # Normalized pins keep every archive pin, connectors included, under artifacts.
        pins['artifacts'][name] = pin

    @staticmethod
    def repin(config, pins):
        for name, entry in catalog.entries_for(config).items():
            pins['artifacts'][name]['descriptor_sha256'] = catalog.descriptor_digest(entry)

    def closure(self, root=Path('/fixture/agent'), state=Path('/fixture/state')):
        """A valid selected closure: pinned node and bun runtimes, nothing else."""
        config = {'name': 'example-agent', 'agent': {'systemPrompt': 'Example'}, 'state_dir': str(state),
                  'modules': {}, 'cook': {'host_repo': str(root / 'inputs/host'), 'host_dependencies': [],
                                          'connector_state': {}}, 'connectors': {}, 'catalog': {}}
        pins = {'runtime': {'cook': cook.COOK_COMMIT, 'connectome-host': 'c' * 40, 'agent-framework': 'd' * 40},
                'connectors': {}, 'artifacts': {}, 'platform': {}}
        for name, version in (('node', '24.1.0'), ('bun', '1.0.0')):
            self.add(config, pins, name, {'kind': 'runtime', 'requires': [], 'exports': {name: 'bin/' + name}}, version)
        return config, pins

    def request(self):
        """A complete adapter request that the recipe compiler accepts as is."""
        work, root, build, state = self.tree()
        config, pins = self.closure(root, state)
        self.add(config, pins, 'extra', {'repo': REPOSITORY, 'build': [['{artifact:node}/bin/node', 'build.py']],
                                         'settings': {'command': '{artifact:node}/bin/node',
                                                      'args': ['{artifact:extra}/server.py'],
                                                      'env': {'MODE': 'development'}},
                                         'exports': {'server': 'server.py'}})
        recipe, names = cook.recipe_from_spec({'config': config, 'pins': pins}, build_dir=build)
        self.assertEqual(recipe['mcpServers']['extra']['command'], str(build / 'catalog/node/bin/node'))
        self.assertEqual(names, [])
        return root, build, state, config, pins

    def adapter(self, root, build, state, config, pins):
        """Closed diagnostic printed by the real adapter entry point for one JSON request."""
        spec = build / 'deployment.json'
        spec.write_text(json.dumps({'config': config, 'pins': pins}))
        argv = ['cook-adapter', '--spec', str(spec), '--out', str(build), '--state', str(state),
                '--entrypoint', str(root / 'inputs/cook/bin/cook')]
        stream = io.StringIO()
        with patch.object(sys, 'argv', argv), contextlib.redirect_stderr(stream), \
                self.assertRaises(SystemExit) as stop:
            cook.main()
        self.assertEqual(stop.exception.code, 1)
        # Refused before any adapter output is written into the request.
        self.assertEqual(sorted(path.name for path in build.iterdir()), ['deployment.json'])
        return json.loads(stream.getvalue())

    def refused(self, message, call, *args, **kwargs):
        with self.assertRaises(ArchiveError) as caught:
            call(*args, **kwargs)
        self.assertEqual(str(caught.exception), message)
        self.assertEqual(caught.exception.code, 'E_CATALOG_INVALID')

    # ---- descriptor resolution (rows 21, 60, 69) ------------------------
    def test_catalog_L55_4(self):
        merged = catalog.entries_for({'catalog': {'node': {'kind': 'runtime'}}, 'connectors': {'extra': {}}})
        self.assertEqual(sorted(merged), ['extra', 'node'])
        self.refused('duplicate artifact name', catalog.entries_for,
                     {'catalog': {'extra': {'kind': 'adapter'}}, 'connectors': {'extra': {}}})

    def test_catalog_L73_8(self):
        for name in ('Helper', 'helper.tool', '-helper', 'h' * 64):
            config, pins = self.closure()
            self.assertEqual(sorted(catalog.resolve(config, pins)['artifacts']), ['bun', 'node'])
            self.add(config, pins, name, {'kind': 'adapter', 'requires': []})
            self.refused('invalid artifact name', catalog.resolve, config, pins)

    def test_named_catalog_descriptor_kind(self):
        config, pins = self.closure()
        self.add(config, pins, 'helper', {'kind': 'adapter', 'requires': []})
        self.assertEqual(sorted(catalog.resolve(config, pins)['artifacts']), ['bun', 'helper', 'node'])
        config['catalog']['helper']['kind'] = 'plugin'
        self.repin(config, pins)
        self.refused('invalid artifact kind', catalog.resolve, config, pins)

    def test_named_catalog_disabled(self):
        for value in (False, 'true', 1, None):
            config, pins = self.closure()
            self.add(config, pins, 'helper', {'kind': 'adapter', 'requires': [], 'enabled': True})
            self.assertEqual(sorted(catalog.resolve(config, pins)['artifacts']), ['bun', 'helper', 'node'])
            # The descriptor digest ignores `enabled`: only the guard refuses a disabled entry.
            config['catalog']['helper']['enabled'] = value
            self.refused('invalid artifact kind', catalog.resolve, config, pins)

    def test_catalog_L84_8(self):
        root, build, state, config, pins = self.request()
        config['connectors']['extra']['build'] = 'make'
        self.repin(config, pins)
        self.assertEqual(self.adapter(root, build, state, config, pins), diagnostic('E_CATALOG_INVALID'))
        self.refused('build must be an argv list', catalog.resolve, config, pins)

    def test_catalog_L109_8(self):
        config, pins = self.closure()
        del config['catalog']['bun']
        self.refused('catalog requires pinned node and bun exports', catalog.resolve, config, pins)
        config, pins = self.closure()
        self.add(config, pins, 'bun', {'kind': 'adapter', 'requires': [], 'exports': {'bun': 'bin/bun'}})
        self.refused('catalog requires pinned node and bun exports', catalog.resolve, config, pins)
        config, pins = self.closure()
        self.add(config, pins, 'bun', {'kind': 'runtime', 'requires': [], 'exports': {'runtime': 'bin/bun'}})
        self.refused('catalog requires pinned node and bun exports', catalog.resolve, config, pins)

    def test_catalog_L113_4(self):
        root, build, state, config, pins = self.request()
        pins['platform'] = ['/bin/sh']
        self.assertEqual(self.adapter(root, build, state, config, pins), diagnostic('E_CATALOG_INVALID'))
        self.refused('platform closure must be a mapping', catalog.resolve, config, pins)

    def test_catalog_L124_8(self):
        root, build, state, config, pins = self.request()
        config['catalog']['node']['requires'] = ['bun']
        config['catalog']['bun']['requires'] = ['node']
        self.repin(config, pins)
        self.assertEqual(self.adapter(root, build, state, config, pins), diagnostic('E_CATALOG_INVALID'))
        self.refused('artifact dependency cycle', catalog.resolve, config, pins)

    def test_catalog_L167_8(self):
        refusal = 'platform digest mismatch, writable or non-platform target'
        self.refused(refusal, catalog.check_platform, {'platform': {'/usr/bin/connectome-fixture-absent': 'f' * 64}})
        self.assertTrue(Path('/bin/sh').is_file())
        self.assertNotEqual(catalog.sha256('/bin/sh'), 'f' * 64)
        self.refused(refusal, catalog.check_platform, {'platform': {'/bin/sh': 'f' * 64}})

    # ---- absolute-path and argv audit (rows 17, 18, 61) -----------------
    def test_catalog_L189_8(self):
        work, root, build, state = self.tree()
        catalog.audit({'nested': ['file://' + str(build / 'data.json')]}, build, state, {})
        self.refused('nonlocal file URI refused', catalog.audit,
                     {'nested': ['file://other-agent/file']}, build, state, {})

    def test_catalog_L191_8(self):
        work, root, build, state = self.tree()
        catalog.audit({'nested': ['file:' + str(build / 'data.json')]}, build, state, {})
        self.refused('relative file URI refused', catalog.audit,
                     {'nested': ['file:relative/data.json']}, build, state, {})

    def test_catalog_L219_8(self):
        work, root, build, state = self.tree()
        self.assertEqual(catalog.audit_location('tool', build, build, state, {}), build / 'tool')
        loop = work / 'loop'
        loop.symlink_to('loop')
        with self.assertRaises((OSError, RuntimeError)):
            loop.resolve()
        # The command itself resolves inside the build; only the pinned platform entry cannot be resolved.
        self.refused('unresolvable command path', catalog.audit_location,
                     'tool', build, build, state, {str(loop): 'a' * 64})

    def test_catalog_L251_12(self):
        work, root, build, state = self.tree()
        tool = str(build / 'bin/tool')
        catalog.audit_argv([tool, '--require=library.js', '-r'], build, build, state, {})
        self.refused('ambiguous attached short option', catalog.audit_argv,
                     [tool, '-r=library.js'], build, build, state, {})

    def test_catalog_L253_16(self):
        work, root, build, state = self.tree()
        tool = str(build / 'bin/tool')
        declared = {'--data-file': str(state / 'records.json')}
        catalog.audit_argv([tool, '--data-file=' + str(state / 'records.json')], build, build, state, {}, declared)
        # Another path inside the state directory is still not the declared one.
        self.refused('data argument differs from declaration', catalog.audit_argv,
                     [tool, '--data-file=' + str(state / 'other.json')], build, build, state, {}, declared)

    # ---- environment audit and secret names (rows 19, 49, 50) -----------
    def test_catalog_L291_4(self):
        root, build, state, config, pins = self.request()
        config['connectors']['extra']['settings']['env'] = ['MODE=development']
        self.repin(config, pins)
        self.assertEqual(self.adapter(root, build, state, config, pins), diagnostic('E_CATALOG_INVALID'))
        self.refused('catalog environment must be a mapping', catalog.audit_environment,
                     ['MODE=development'], build, build, state, {})

    def test_catalog_L311_12(self):
        work, root, build, state = self.tree()
        catalog.audit_environment({'CONFIG': '--config=settings.json'}, build, build, state, {})
        self.refused('ambiguous environment path option', catalog.audit_environment,
                     {'CONFIG': 'config.name=settings.json'}, build, build, state, {})

    def test_named_recipe_secret_ref_audit_declared(self):
        work, root, build, state = self.tree()
        reference = {'EXAMPLE_SECRET': '${EXAMPLE_SECRET}'}
        catalog.audit_environment(reference, build, build, state, {}, ['EXAMPLE_SECRET'])
        # The same self-reference is refused when the name was never declared.
        self.refused('ambiguous catalog environment value', catalog.audit_environment,
                     reference, build, build, state, {}, [])
        self.refused('ambiguous catalog environment value', catalog.audit_environment,
                     reference, build, build, state, {}, ['OTHER_SECRET'])

    def test_named_recipe_secret_ref_audit_own_key(self):
        work, root, build, state = self.tree()
        declared = ['EXAMPLE_SECRET', 'OTHER_SECRET']
        catalog.audit_environment({'EXAMPLE_SECRET': '${EXAMPLE_SECRET}'}, build, build, state, {}, declared)
        # A declared name may carry only its own reference, not another declared name's.
        self.refused('ambiguous catalog environment value', catalog.audit_environment,
                     {'EXAMPLE_SECRET': '${OTHER_SECRET}'}, build, build, state, {}, declared)

    def test_named_recipe_secret_ref_audit_other_dollar(self):
        work, root, build, state = self.tree()
        declared = ['EXAMPLE_SECRET']
        catalog.audit_environment({'CONFIG': 'settings.json'}, build, build, state, {}, declared)
        for value in ('${EXAMPLE_SECRET}', '$EXAMPLE_SECRET', 'prefix-${EXAMPLE_SECRET}'):
            self.refused('ambiguous catalog environment value', catalog.audit_environment,
                         {'CONFIG': value}, build, build, state, {}, declared)

    def test_catalog_L329_4(self):
        self.assertEqual(catalog.secret_environment_names({'required_secrets': ['EXAMPLE_SECRET']}), ['EXAMPLE_SECRET'])
        refusal = 'secret names overlap constructed environment'
        for name in ('HOME', 'TMPDIR', 'DATA_DIR', 'WORKSPACE_DIR', 'LANG', 'LC_ALL'):
            self.refused(refusal, catalog.secret_environment_names, {'required_secrets': [name]})
        self.refused(refusal, catalog.secret_environment_names,
                     {'connectors': {'extra': {'required_secrets': ['HOME']}}})
        self.refused(refusal, catalog.secret_environment_names,
                     {'catalog': {'helper': {'required_secrets': ['LANG']}}})
        self.refused(refusal, catalog.secret_environment_names,
                     {'modules': {'memory': {'required_secrets': ['TMPDIR']}}})
        self.refused(refusal, catalog.secret_environment_names,
                     {'secret_files': [{'secret': 'EXAMPLE_SECRET', 'env': 'DATA_DIR'}]})

    def test_named_secrets_reserved_catalog_code_env(self):
        self.assertEqual(catalog.secret_environment_names({'required_secrets': ['EXAMPLE_SECRET']}), ['EXAMPLE_SECRET'])
        for name in ('NODE_OPTIONS', 'LD_PRELOAD', 'PYTHONPATH', 'BUN_CONFIG_FILE'):
            self.refused('code-loading/interpreter-control environment refused: ' + name,
                         catalog.secret_environment_names, {'required_secrets': [name]})
        self.refused('code-loading/interpreter-control environment refused: GIT_SSH_COMMAND',
                     catalog.secret_environment_names,
                     {'secret_files': [{'secret': 'EXAMPLE_SECRET', 'env': 'GIT_SSH_COMMAND'}]})

    # ---- data arguments (row 61) ----------------------------------------
    def test_catalog_L343_4(self):
        root, build, state, config, pins = self.request()
        config['connectors']['extra']['settings']['data_args'] = ['--data-file=' + str(state / 'records.json')]
        self.repin(config, pins)
        self.assertEqual(self.adapter(root, build, state, config, pins), diagnostic('E_CATALOG_INVALID'))
        self.refused('data_args must be a mapping', catalog.audit_data_args,
                     ['--data-file=' + str(state / 'records.json')], state)

    def test_named_catalog_data_args_state_absolute(self):
        work, root, build, state = self.tree()
        catalog.audit_data_args({'--data-file': str(state / 'records.json')}, state)
        self.refused('data argument must be absolute', catalog.audit_data_args,
                     {'--data-file': 'records.json'}, state)

    def test_named_catalog_data_args_state_containment(self):
        work, root, build, state = self.tree()
        catalog.audit_data_args({'--data-file': str(state / 'records.json')}, state)
        refusal = 'command dependency outside build and pinned platform closure'
        for value in (str(work / 'outside/records.json'), str(build / 'records.json'), str(state) + '/../escape'):
            self.refused(refusal, catalog.audit_data_args, {'--data-file': value}, state)

    def test_catalog_L359_16(self):
        work, root, build, state = self.tree()
        config, pins = self.closure(root, state)
        first = {'--data-file': str(state / 'records.json')}
        for name in ('alpha', 'beta'):
            self.add(config, pins, name, {'repo': REPOSITORY, 'settings': {
                'command': '{artifact:node}/bin/node', 'data_args': dict(first)}})
        command = str(build / 'catalog/node/bin/node')
        self.assertEqual(catalog.data_options_for(config, build), {command: first})
        config['connectors']['beta']['settings']['data_args'] = {'--data-file': str(state / 'other.json')}
        self.refused('conflicting data argument declarations', catalog.data_options_for, config, build)

    # ---- sealed build link rules (row 12) -------------------------------
    def sealed(self):
        work, root, build, state = self.tree()
        leaf = build / 'file'
        leaf.write_text('data')
        leaf.chmod(0o444)
        build.chmod(0o555)
        catalog.check_immutable(build)
        return work, build, leaf

    def writable(self):
        work, root, build, state = self.tree()
        leaf = build / 'file'
        leaf.write_text('data')
        leaf.chmod(0o644)
        return work, build, leaf

    def test_catalog_L390_4(self):
        work, build, leaf = self.sealed()
        alias = work / 'alias'
        alias.symlink_to(build, target_is_directory=True)
        self.refused('build alias refused', catalog.check_immutable, alias)

    def test_catalog_L394_8(self):
        work, build, leaf = self.sealed()
        build.chmod(0o755)
        os.mkfifo(build / 'pipe')
        (build / 'pipe').chmod(0o444)
        build.chmod(0o555)
        self.refused('build special file refused', catalog.check_immutable, build)

    def test_catalog_L395_8(self):
        work, build, leaf = self.sealed()
        # A second name outside the sealed tree keeps the same inode reachable.
        os.link(leaf, work / 'twin')
        self.assertEqual(leaf.lstat().st_nlink, 2)
        self.refused('build hardlink refused', catalog.check_immutable, build)

    def test_catalog_L402_4(self):
        work, build, leaf = self.writable()
        alias = work / 'alias'
        alias.symlink_to(build, target_is_directory=True)
        self.refused('build alias refused', catalog.seal, alias)
        self.assertEqual((mode(build), mode(leaf)), (0o755, 0o644))

    def test_catalog_L406_8(self):
        work, build, leaf = self.writable()
        os.mkfifo(build / 'pipe')
        (build / 'pipe').chmod(0o644)
        self.refused('build links/special files refused', catalog.seal, build)
        self.assertEqual((mode(build), mode(leaf), mode(build / 'pipe')), (0o755, 0o644, 0o644))

    def test_catalog_L407_8(self):
        work, build, leaf = self.writable()
        catalog.seal(build)
        self.assertEqual((mode(build), mode(leaf)), (0o555, 0o444))
        build.chmod(0o755)
        leaf.chmod(0o644)
        os.link(leaf, work / 'twin')
        self.refused('build hardlink refused', catalog.seal, build)
        # seal refuses the hard link before it changes any mode: nothing was sealed.
        self.assertEqual((mode(build), mode(leaf)), (0o755, 0o644))


if __name__ == '__main__':
    unittest.main()
