"""Purpose-built killing tests for the catalog guards in connectome_catalog.py, lines 421-855.

Every test calls real oracle code with one crafted input and asserts the guard's
own refusal (class, message and closed code) or the file state it protects. Most
tests first show that the neighbouring valid input is accepted, so the refusal is
owed to the crafted difference alone.

The fixture is a small agent tree with pinned stand-in `node` and `bun` archives.
Pins come from the oracle's own resolver and installed trees from its own
`install`; nothing is downloaded and the stand-in runtimes are never executed.
Build commands that really run use the pinned platform shell, as the frozen
catalog tests do.
"""
import io, json, os, socket, sys, tarfile, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'oracle/module_utils'),str(ROOT/'oracle/tests')]
import connectome_catalog as catalog
from connectome_archive import ArchiveError

SHELL = '/bin/sh'
OUTSIDE = 'command dependency outside build and pinned platform closure'
FOREIGN = 'absolute dependency outside agent tree and pinned platform closure'
CODE_ENV = 'code-loading/interpreter-control environment refused: NODE_OPTIONS'


def thaw(top):
    """Give the owner access back to a sealed or search-only scratch tree."""
    top = str(top)
    if os.path.isdir(top) and not os.path.islink(top):
        os.chmod(top, 0o700)
    for folder, dirs, files in os.walk(top):
        for name in dirs:
            path = os.path.join(folder, name)
            if not os.path.islink(path):
                os.chmod(path, 0o700)
        for name in files:
            path = os.path.join(folder, name)
            if os.path.isfile(path) and not os.path.islink(path):
                os.chmod(path, os.stat(path).st_mode | 0o200)


def pack(path, files):
    with tarfile.open(path, 'w:gz') as stream:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o755
            stream.addfile(member, io.BytesIO(data))
    return catalog.sha256(path)


class Agent:
    """WORK/agent/builds/request, WORK/agent/inputs/*.tar.gz and a separate WORK/state path."""

    def __init__(self, case, shell=False):
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name).resolve()
        case.addCleanup(thaw, self.work)
        self.root = self.work / 'agent'
        self.build = self.root / 'builds/request'
        self.state = self.work / 'state'
        self.build.mkdir(parents=True)
        (self.root / 'inputs').mkdir()
        self.node = str(self.build / 'catalog/node/bin/node')
        self.config = {'state_dir': str(self.state), 'catalog': {}}
        self.versions = {'artifacts': {}, 'platform': {SHELL: catalog.sha256(SHELL)} if shell else {}}
        self.recipe = {}
        for name, version in (('node', '24.1.0'), ('bun', '1.0.0')):
            self.add(name, {'kind': 'runtime', 'requires': [], 'exports': {name: 'bin/' + name}},
                     {'bin/' + name: b'stand-in runtime, never executed\n'}, version)

    def add(self, name, descriptor, files, version='1.0.0'):
        descriptor['pin_ref'] = 'artifacts.' + name
        self.config['catalog'][name] = descriptor
        path = self.root / 'inputs' / (name + '.tar.gz')
        self.versions['artifacts'][name] = {
            'archive': 'inputs/' + path.name, 'sha256': pack(path, files), 'version': version,
            'descriptor_sha256': catalog.descriptor_digest(descriptor)}

    def pins(self):
        """Re-pin the current descriptors and resolve them with the oracle's resolver."""
        for name, entry in self.config['catalog'].items():
            self.versions['artifacts'][name]['descriptor_sha256'] = catalog.descriptor_digest(entry)
        return catalog.resolve(self.config, self.versions)

    def prepare(self):
        (self.build / 'cook-recipe.json').write_text(json.dumps(self.recipe))
        return self.pins()

    def install(self):
        pins = self.prepare()
        return pins, catalog.install(self.config, pins, self.build)

    def relock(self):
        """A digest-valid catalog lock for the current descriptors and recipe, on the installed files."""
        pins = self.prepare()
        path = self.build / 'catalog.lock.json'
        record = json.loads(path.read_text())
        record['pins'] = {key: pins[key] for key in ('artifacts', 'platform')}
        record['descriptors'] = {name: catalog.descriptor_digest(entry)
                                 for name, entry in self.config['catalog'].items()}
        record['recipe_sha256'] = catalog.sha256(self.build / 'cook-recipe.json')
        path.write_bytes(catalog.canonical(record) + b'\n')
        return pins

    def lock(self, pins):
        """The cook lock fields `verify` reads, for a launcher at BUILD/run.sh."""
        runtime = {name: str(self.build / 'catalog' / name / 'bin' / name) for name in ('node', 'bun')}
        return {'launch': {'script': 'run.sh'},
                'recipe': {'sha256': catalog.sha256(self.build / 'cook-recipe.json')},
                'components': [], 'localExtensions': [], 'external': [],
                'engines': {name: {'path': path, 'realPath': path, 'version': pins['artifacts'][name]['version']}
                            for name, path in runtime.items()}}

    def verify(self, pins, lock=None):
        """Seal, run the default immutable verification, then thaw the disposable tree."""
        catalog.seal(self.build)
        try:
            return catalog.verify(self.config, pins, self.build, lock)
        finally:
            thaw(self.build)


class Guards(unittest.TestCase):
    # ---- helpers --------------------------------------------------------
    def refuses(self, message, call, *args, **kwargs):
        with self.assertRaises(ArchiveError) as caught:
            call(*args, **kwargs)
        self.assertEqual((str(caught.exception), caught.exception.code), (message, 'E_CATALOG_INVALID'))

    def installed(self, shell=False, prepare=None):
        """An installed tree with a launcher, its pins and a matching cook lock."""
        agent = Agent(self, shell)
        if prepare:
            prepare(agent)
        pins, _ = agent.install()
        (agent.build / 'run.sh').write_text('exit 0\n')
        return agent, pins, agent.lock(pins)

    def paths(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name).resolve()
        build = work / 'agent/builds/request'
        build.mkdir(parents=True)
        return work, build, work / 'state'

    @staticmethod
    def tool(agent, files, **descriptor):
        agent.add('tool', dict({'kind': 'adapter', 'requires': []}, **descriptor), files)

    # ---- state/build separation (row 62, line 460) ------------------------
    def test_catalog_L460_4(self):
        agent = Agent(self)
        pins = agent.pins()
        self.assertIsNone(catalog.audit_descriptors(agent.config, pins, agent.build))
        # State inside the build, then the build inside the state directory.
        for state in (agent.build / 'state', agent.root):
            agent.config['state_dir'] = str(state)
            self.refuses('state and build must be separate',
                         catalog.audit_descriptors, agent.config, pins, agent.build)

    # ---- launch syntax, units, launchers, interpreter lines (rows 15, 20) --
    def test_catalog_L483_8(self):
        _, build, state = self.paths()
        self.assertEqual(catalog.audit_launcher("exit '0'\n", build, build, state, {}), [build])
        self.refuses('invalid launch quoting', catalog.audit_launcher, "exit '0\n", build, build, state, {})
        self.refuses('invalid launch quoting', catalog.literal_words, 'exec "unterminated')

    def test_catalog_L501_8(self):
        _, build, state = self.paths()
        body = '[Service]\nWorkingDirectory=' + str(build) + '\n'
        self.assertEqual(catalog.audit_unit(body + 'StandardOutput=journal\n', build, state, {}), build)
        for key in ('RootDirectory', 'RootImage', 'ExecSearchPath'):
            self.refuses('unit execution remapping refused', catalog.audit_unit,
                         body + key + '=' + str(build / 'alternate') + '\n', build, state, {})

    def test_catalog_L507_16(self):
        _, build, state = self.paths()
        body = '[Service]\nWorkingDirectory=' + str(build) + '\n'
        self.assertEqual(catalog.audit_unit(body + 'Environment=MODE=plain\n', build, state, {}), build)
        self.refuses('unit environment requires literal assignments', catalog.audit_unit,
                     body + 'Environment=MODE\n', build, state, {})

    def test_catalog_L546_12(self):
        _, build, state = self.paths()
        self.assertEqual(catalog.audit_launcher('exit 0\n', build, build, state, {}), [build])
        for line in ('exit', 'exit later', 'exit 0 1'):
            self.refuses('ambiguous launcher exit', catalog.audit_launcher, line + '\n', build, build, state, {})

    def test_catalog_L556_8(self):
        _, build, state = self.paths()
        (build / 'plain.txt').write_text('no interpreter line\n')
        self.assertIsNone(catalog.audit_tree_code(build, state, {}))
        (build / 'alias.txt').symlink_to(build / 'plain.txt')
        self.refuses('build symlinks are refused', catalog.audit_tree_code, build, state, {})

    def test_catalog_L564_20(self):
        _, build, state = self.paths()
        # The readable interpreter line sits at the top of the tree, which the
        # walk lists before the contents of any subdirectory.
        (build / 'first.sh').write_text('#!' + str(build / 'bin/interpreter') + '\n')
        (build / 'nested').mkdir()
        self.assertIsNone(catalog.audit_tree_code(build, state, {}))
        (build / 'nested/second.sh').write_bytes(b'#!\xff\xfe\n')
        self.refuses('invalid interpreter directive', catalog.audit_tree_code, build, state, {})

    def test_catalog_L565_16(self):
        _, build, state = self.paths()
        script = build / 'script.sh'
        script.write_text('#!' + str(build / 'bin/interpreter') + '\n')
        self.assertIsNone(catalog.audit_tree_code(build, state, {}))
        script.write_text('#!bin/interpreter\n')
        self.refuses('interpreter must be absolute', catalog.audit_tree_code, build, state, {})

    # ---- staging inventory (row 54, lines 570-576) -------------------------
    def checkout(self):
        work, _, _ = self.paths()
        source = work / 'checkout'
        (source / 'bin').mkdir(parents=True)
        (source / 'bin/cook').write_text('staged entrypoint\n')
        self.assertEqual(catalog.stage_checkout(source, work / 'control'), work / 'control')
        self.assertEqual((work / 'control/bin/cook').read_text(), 'staged entrypoint\n')
        return work, source

    def test_catalog_L570_4(self):
        work, source = self.checkout()
        (work / 'alias').symlink_to(source, target_is_directory=True)
        self.refuses('artifact directory alias refused', catalog.stage_checkout, work / 'alias', work / 'staged')
        self.assertFalse((work / 'staged').exists())

    def test_catalog_L573_8(self):
        work, source = self.checkout()
        (source / 'bin/alias').symlink_to(source / 'bin/cook')
        self.refuses('artifact symlinks are refused', catalog.stage_checkout, source, work / 'staged')
        self.assertFalse((work / 'staged').exists())

    def test_catalog_L576_8(self):
        # A socket cannot be opened for hashing, so without the guard the
        # inventory stops with OSError. `verify(immutable=False)` is the cook
        # adapter's own post-build call; it turns that into its generic refusal.
        agent, pins, _ = self.installed()
        self.assertIsNone(catalog.verify(agent.config, pins, agent.build, immutable=False))
        listener = socket.socket(socket.AF_UNIX)
        self.addCleanup(listener.close)
        listener.bind(str(agent.build / 'catalog/node/probe.sock'))
        self.refuses('artifact special file refused',
                     catalog.verify, agent.config, pins, agent.build, immutable=False)
        self.refuses('artifact special file refused',
                     catalog.stage_checkout, agent.build / 'catalog', agent.work / 'staged')
        self.assertFalse((agent.work / 'staged').exists())

    # ---- archive extraction (row 16, lines 582-595) -------------------------
    def tarball(self, members=None):
        work, _, _ = self.paths()
        path = work / 'payload.tar.gz'
        if members is None:
            return work, path, pack(path, {'bin/tool': b'payload\n'})
        with tarfile.open(path, 'w:gz') as stream:
            for member, data in members:
                stream.addfile(member, io.BytesIO(data) if data is not None else None)
        return work, path, catalog.sha256(path)

    def test_catalog_L582_4(self):
        work, path, digest = self.tarball()
        self.assertIsNone(catalog.extract(path, work / 'control', digest))
        self.assertEqual((work / 'control/bin/tool').read_bytes(), b'payload\n')
        (work / 'alias.tar.gz').symlink_to(path)
        self.refuses('archive must be an agent-local regular file',
                     catalog.extract, work / 'alias.tar.gz', work / 'target', digest)
        self.assertFalse((work / 'target').exists())

    def test_catalog_L586_8(self):
        work, path, digest = self.tarball()
        self.assertIsNone(catalog.extract(path, work / 'control', digest))
        other = 'f' * 64
        self.assertNotEqual(digest, other)
        self.refuses('artifact archive digest mismatch', catalog.extract, path, work / 'target', other)
        self.assertFalse((work / 'target').exists())

    def test_catalog_L592_16(self):
        def regular(name):
            member = tarfile.TarInfo(name)
            member.size = 5
            return member, b'data\n'
        link = tarfile.TarInfo('alias')
        link.type, link.linkname = tarfile.SYMTYPE, 'file'
        # A name outside the member grammar, a traversal, and a link to a sibling member.
        for members in ([regular('two words')], [regular('../outside')], [regular('file'), (link, None)]):
            work, path, digest = self.tarball(members)
            (work / 'extract').mkdir()
            self.refuses('unsafe archive member', catalog.extract, path, work / 'extract/target', digest)
            self.assertEqual(list((work / 'extract').iterdir()), [])
        work, path, digest = self.tarball([regular('file'), regular('bin/tool')])
        self.assertIsNone(catalog.extract(path, work / 'target', digest))

    def test_catalog_L595_16(self):
        # The limit counts declared sizes. A directory header carries a size
        # field but no data blocks, so the archive stays tiny.
        def declared(size):
            member = tarfile.TarInfo('payload')
            member.type, member.size, member.mode = tarfile.DIRTYPE, size, 0o755
            return [(member, None)]
        work, path, digest = self.tarball(declared(1024 ** 3))
        self.assertIsNone(catalog.extract(path, work / 'control', digest))
        self.assertTrue((work / 'control/payload').is_dir())
        work, path, digest = self.tarball(declared(1024 ** 3 + 1))
        self.refuses('artifact extraction limit exceeded', catalog.extract, path, work / 'target', digest)
        self.assertFalse((work / 'target').exists())

    # ---- install: containment and build sandbox (rows 53, 54) ---------------
    def test_catalog_L628_8(self):
        def agent_with_tool():
            agent = Agent(self)
            self.tool(agent, {'tool.js': b'// payload\n'}, exports={'tool': 'tool.js'})
            pinned = agent.root / 'inputs/tool.tar.gz'
            outside = agent.work / 'outside.tar.gz'
            return agent, agent.prepare(), pinned, outside
        agent, pins, pinned, outside = agent_with_tool()
        self.assertEqual(catalog.install(agent.config, pins, agent.build)['schema'], 'connectome.catalog-lock/v1')
        # The pinned name is a link to the same bytes outside the agent tree.
        agent, pins, pinned, outside = agent_with_tool()
        pinned.rename(outside)
        pinned.symlink_to(outside)
        self.refuses('archive escapes agent tree', catalog.install, agent.config, pins, agent.build)
        self.assertFalse((agent.build / 'catalog/tool').exists())
        # The pin itself names the outside file.
        agent, pins, pinned, outside = agent_with_tool()
        pinned.rename(outside)
        pins['artifacts']['tool']['archive'] = str(outside)
        self.refuses('archive escapes agent tree', catalog.install, agent.config, pins, agent.build)
        self.assertFalse((agent.build / 'catalog/tool').exists())

    def test_catalog_L639_12(self):
        # subprocess.run is replaced so that a missing executable is a decision
        # of the engine, not a spawn failure of the operating system.
        def attempt(executable):
            agent = Agent(self)
            self.tool(agent, {'bin/present': b'stand-in\n', 'input.txt': b'data\n'},
                      build=[['{artifact:tool}/bin/' + executable, 'input.txt']])
            pins = agent.prepare()
            with patch.object(catalog.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as run:
                try:
                    return catalog.install(agent.config, pins, agent.build)
                finally:
                    self.commands = [call.args[0] for call in run.call_args_list]
                    self.directory = agent.build / 'catalog/tool'
        self.assertEqual(attempt('present')['schema'], 'connectome.catalog-lock/v1')
        self.assertEqual(self.commands, [[str(self.directory / 'bin/present'), 'input.txt']])
        self.refuses('build executable must be installed', attempt, 'missing')
        self.assertEqual(self.commands, [])

    def build_result(self, script):
        agent = Agent(self, shell=True)
        self.tool(agent, {'make.sh': script.encode()}, build=[[SHELL, 'make.sh']])
        pins = agent.prepare()
        self.agent = agent
        return catalog.install(agent.config, pins, agent.build)

    def test_catalog_L643_12(self):
        self.assertIn('tool/built', self.build_result(': > built\nexit 0\n')['files'])
        self.refuses('artifact build failed', self.build_result, ': > built\nexit 3\n')
        self.assertTrue((self.agent.build / 'catalog/tool/built').is_file())
        self.assertFalse((self.agent.build / 'catalog.lock.json').exists())

    def test_catalog_L646_12(self):
        def attempt(files):
            agent = Agent(self)
            self.tool(agent, files, exports={'tool': 'dist/tool.js'})
            pins = agent.prepare()
            self.agent = agent
            return catalog.install(agent.config, pins, agent.build)
        self.assertIn('tool/dist/tool.js', attempt({'dist/tool.js': b'// payload\n'})['files'])
        self.refuses('artifact export missing after build', attempt, {'dist/other.js': b'// payload\n'})
        self.assertFalse((self.agent.build / 'catalog.lock.json').exists())

    def test_catalog_build_audit(self):
        # Row 53, line 637. The first build command plants a link to outside
        # code in a search-only directory, which the tree walk cannot list. The
        # descriptor audit before the build saw a path that did not exist yet,
        # so only the audit in front of the second command can refuse it.
        agent = Agent(self, shell=True)
        outside = agent.work / 'outside'
        outside.mkdir()
        marker = outside / 'ran'
        (outside / 'payload.sh').write_text(': > ' + str(marker) + '\n')
        script = ('/bin/mkdir hidden\n/bin/ln -s ' + str(outside / 'payload.sh') + ' hidden/link\n'
                  '/bin/chmod 300 hidden\n')
        self.tool(agent, {'make.sh': script.encode()}, build=[[SHELL, 'make.sh'], [SHELL, 'hidden/link']])
        pins = agent.prepare()
        self.refuses(OUTSIDE, catalog.install, agent.config, pins, agent.build)
        self.assertTrue((agent.build / 'catalog/tool/hidden/link').is_symlink())
        self.assertFalse(marker.exists(), 'the second build command ran code outside the build')

    # ---- install: audits before and after the build (row 62) ----------------
    def test_catalog_audit_site_pre_build_descriptors(self):
        # Line 615. A health command that escapes the build is refused before
        # anything is extracted or run, not only by the audit after the build.
        def attempt(argument):
            agent = Agent(self, shell=True)
            self.tool(agent, {'make.sh': b': > built\n'}, build=[[SHELL, 'make.sh']])
            agent.config['health'] = {'command': [SHELL, argument]}
            pins = agent.prepare()
            self.agent = agent
            return catalog.install(agent.config, pins, agent.build)
        self.assertIn('tool/built', attempt('probe.sh')['files'])
        self.refuses(OUTSIDE, attempt, '../outside.sh')
        self.assertEqual([path.name for path in self.agent.build.iterdir()], ['cook-recipe.json'])

    def test_catalog_audit_site_pre_build_recipe(self):
        # Line 616. The same for a recipe-only surface.
        def attempt(recipe):
            agent = Agent(self, shell=True)
            self.tool(agent, {'make.sh': b': > built\n'}, build=[[SHELL, 'make.sh']])
            agent.recipe = recipe
            pins = agent.prepare()
            self.agent = agent
            return catalog.install(agent.config, pins, agent.build)
        self.assertIn('tool/built', attempt({'agent': {'provider': {'env': {'MODE': 'plain'}}}})['files'])
        self.refuses(CODE_ENV, attempt, {'agent': {'provider': {'env': {'NODE_OPTIONS': '--require=./hook.cjs'}}}})
        self.assertEqual([path.name for path in self.agent.build.iterdir()], ['cook-recipe.json'])

    def linked_build(self, link, health=None, recipe=None):
        """Install a tree whose last build command links BUILD/alias to a directory outside it."""
        agent = Agent(self, shell=True)
        outside = agent.work / 'outside'
        outside.mkdir()
        script = '/bin/ln -s ' + str(outside) + ' ../../alias\n' if link else ': > built\n'
        self.tool(agent, {'make.sh': script.encode()}, build=[[SHELL, 'make.sh']])
        if health:
            agent.config['health'] = {'command': [SHELL, str(agent.build / health)]}
        if recipe:
            agent.recipe = {'mcpServers': {'tool': {'command': agent.node, 'args': [str(agent.build / recipe)]}}}
        pins = agent.prepare()
        self.agent = agent
        return catalog.install(agent.config, pins, agent.build)

    def test_catalog_audit_site_post_build_descriptors(self):
        # Line 647. The path is inside the build until the build itself turns
        # its parent into a link; no tree walk follows the last command.
        self.assertEqual(self.linked_build(False, health='alias/probe.sh')['schema'], 'connectome.catalog-lock/v1')
        self.refuses(OUTSIDE, self.linked_build, True, health='alias/probe.sh')
        self.assertTrue((self.agent.build / 'alias').is_symlink())
        self.assertFalse((self.agent.build / 'catalog.lock.json').exists())

    def test_catalog_audit_site_post_build_recipe(self):
        # Line 648. The same through a launch argument that only the recipe names.
        self.assertEqual(self.linked_build(False, recipe='alias/server.js')['schema'], 'connectome.catalog-lock/v1')
        self.refuses(FOREIGN, self.linked_build, True, recipe='alias/server.js')
        self.assertTrue((self.agent.build / 'alias').is_symlink())
        self.assertFalse((self.agent.build / 'catalog.lock.json').exists())

    # ---- preflight unit (row 55) ---------------------------------------------
    def test_catalog_preflight_audit(self):
        # Line 676. An absolute executable with a relative operand that leaves the build.
        agent = Agent(self)
        agent.add('check', {'kind': 'preflight', 'requires': [], 'run': ['{artifact:check}/run', 'catalog/check/ready']},
                  {'run': b'stand-in\n'})
        run = str(agent.build / 'catalog/check/run')
        self.assertEqual(catalog.preflight_unit(agent.config, agent.build),
                         'ExecStartPre="' + run + '" "catalog/check/ready"\n')
        agent.config['catalog']['check']['run'][1] = '../../../outside/ready'
        self.refuses(OUTSIDE, catalog.preflight_unit, agent.config, agent.build)

    # ---- verify: tree, descriptors, recipe, health, preflights (row 62) ------
    def test_catalog_audit_site_verify_tree_code(self):
        # Line 698. An interpreter line that names code in the state directory.
        agent, pins, _ = self.installed()
        hook = agent.build / 'hook.sh'
        hook.write_text('#!' + str(agent.build / 'catalog/node/bin/node') + '\n')
        self.assertIsNone(agent.verify(pins))
        hook.write_text('#!' + str(agent.state / 'interpreter') + '\n')
        self.refuses(OUTSIDE, agent.verify, pins)

    def test_catalog_audit_site_verify_descriptors(self):
        # Line 700. A digest-valid lock for a descriptor that sets a code-loading variable.
        agent, _, _ = self.installed()
        settings = agent.config['catalog']['node'].setdefault('settings', {})
        settings['env'] = {'MODE': 'plain'}
        self.assertIsNone(agent.verify(agent.relock()))
        settings['env'] = {'NODE_OPTIONS': '--require=./hook.cjs'}
        self.refuses(CODE_ENV, agent.verify, agent.relock())

    def test_catalog_audit_site_verify_recipe(self):
        # Line 701. The same for a recipe-only surface, without any cook lock.
        agent, _, _ = self.installed()
        agent.recipe = {'agent': {'provider': {'env': {'MODE': 'plain'}}}}
        self.assertIsNone(agent.verify(agent.relock()))
        agent.recipe = {'agent': {'provider': {'env': {'NODE_OPTIONS': '--require=./hook.cjs'}}}}
        self.refuses(CODE_ENV, agent.verify, agent.relock())

    def test_catalog_audit_site_verify_health(self):
        # Line 702. A health setting that is not a command and names another agent's file.
        agent, pins, _ = self.installed()
        agent.config['health'] = {'socket': str(agent.state / 'health.sock')}
        self.assertIsNone(agent.verify(pins))
        agent.config['health'] = {'socket': '/fixture/other-agent/health.sock'}
        self.refuses(FOREIGN, agent.verify, pins)

    def test_catalog_audit_site_verify_preflights(self):
        # Line 703. The descriptor audit accepts a relative executable with a
        # slash; only rendering the preflight unit requires an absolute one.
        def prepare(agent):
            agent.add('check', {'kind': 'preflight', 'requires': [], 'run': ['{artifact:check}/run', 'ready']},
                      {'run': b'stand-in\n'})
        agent, pins, _ = self.installed(prepare=prepare)
        self.assertIsNone(agent.verify(pins))
        agent.config['catalog']['check']['run'][0] = 'catalog/check/run'
        self.refuses('preflight executable must be absolute', agent.verify, agent.relock())

    # ---- verify: cook lock (rows 22, 62) --------------------------------------
    def test_catalog_audit_site_verify_lock_health(self):
        # Line 706. The generic lock audit admits state paths; a command may not load one.
        agent, pins, lock = self.installed()
        lock['health'] = {'command': [agent.node, 'catalog/node/probe.js']}
        self.assertIsNone(agent.verify(pins, lock))
        lock['health'] = {'command': [agent.node, str(agent.state / 'probe.js')]}
        self.refuses(OUTSIDE, agent.verify, pins, lock)

    def test_catalog_audit_site_verify_lock_paths(self):
        # Line 707. Any absolute path in the lock must stay in the agent tree or state.
        agent, pins, lock = self.installed()
        lock['layout'] = {'workspace': str(agent.root / 'workspace')}
        self.assertIsNone(agent.verify(pins, lock))
        lock['layout'] = {'workspace': '/fixture/other-agent/workspace'}
        self.refuses(FOREIGN, agent.verify, pins, lock)

    def test_catalog_L708_12(self):
        agent, pins, lock = self.installed()
        self.assertIsNone(agent.verify(pins, lock))
        lock['recipe']['sha256'] = 'f' * 64
        self.refuses('cook recipe digest mismatch', agent.verify, pins, lock)

    def test_catalog_L710_12(self):
        agent, pins, lock = self.installed()
        self.assertIsNone(agent.verify(pins, lock))
        for key, value in (('external', [{'name': 'ghost', 'command': agent.node}]),
                           ('components', [{'name': 'ghost'}]), ('localExtensions', [{'name': 'ghost'}])):
            self.refuses('cook closure differs from catalog', agent.verify, pins, dict(lock, **{key: value}))

    def test_catalog_L712_12(self):
        agent, pins, lock = self.installed()
        self.assertIsNone(agent.verify(pins, lock))
        lock['engines'] = {'node': lock['engines']['node']}
        self.refuses('catalog requires engine provenance', agent.verify, pins, lock)

    def test_catalog_L714_16(self):
        agent, pins, lock = self.installed()
        self.assertIsNone(agent.verify(pins, lock))
        other = str(agent.build / 'catalog/node/bin/other')
        for change in ({'path': other, 'realPath': other}, {'realPath': other}):
            engines = dict(lock['engines'], node=dict(lock['engines']['node'], **change))
            self.refuses('engine outside pinned catalog', agent.verify, pins, dict(lock, engines=engines))

    def test_catalog_L715_16(self):
        agent, pins, lock = self.installed()
        lock['engines']['node']['version'] = 'v24.1.0'
        self.assertIsNone(agent.verify(pins, lock))
        lock['engines']['node']['version'] = 'v24.0.9'
        self.refuses('engine version mismatch', agent.verify, pins, lock)

    def test_catalog_L719_16(self):
        agent, pins, lock = self.installed()
        body = '[Service]\nWorkingDirectory=' + str(agent.build) + '\n'
        (agent.build / 'agent.service').write_text(body)
        (agent.root / 'workspace').mkdir()
        (agent.root / 'workspace/agent.service').write_text(body)
        lock['units'] = [{'path': 'agent.service'}]
        self.assertIsNone(agent.verify(pins, lock))
        lock['units'] = [{'path': '../../workspace/agent.service'}]
        self.refuses('unit path escapes build', agent.verify, pins, lock)

    def test_catalog_L725_12(self):
        agent, pins, lock = self.installed()
        (agent.root / 'workspace').mkdir()
        (agent.root / 'workspace/run.sh').write_text('exit 0\n')
        self.assertIsNone(agent.verify(pins, lock))
        lock['launch'] = {'script': '../../workspace/run.sh'}
        self.refuses('launch path escapes build', agent.verify, pins, lock)

    def test_catalog_L735_8(self):
        agent, pins, lock = self.installed()
        self.assertIsNone(agent.verify(pins, lock))
        broken = dict(lock)
        del broken['recipe']
        self.refuses('catalog lock or launch surface invalid', agent.verify, pins, broken)
        (agent.build / 'catalog.lock.json').write_text('{"schema": ')
        self.refuses('catalog lock or launch surface invalid', agent.verify, pins, lock)

    # ---- verify: units and launcher (row 62) ----------------------------------
    def unit(self, agent, lock, body):
        (agent.build / 'agent.service').write_text(body)
        lock['units'] = [{'path': 'agent.service'}]

    def test_catalog_audit_site_verify_unit_text(self):
        # Line 720. A [Unit] directive is outside what the Service audit classifies.
        agent, pins, lock = self.installed()
        body = '[Unit]\nConditionPathExists=%s\n[Service]\nWorkingDirectory=' + str(agent.build) + '\n'
        self.unit(agent, lock, body % (agent.build / 'ready.flag'))
        self.assertIsNone(agent.verify(pins, lock))
        self.unit(agent, lock, body % '/fixture/other-agent/ready.flag')
        self.refuses(FOREIGN, agent.verify, pins, lock)

    def test_catalog_audit_site_verify_unit_directory(self):
        # Line 721. A pinned platform tool is admitted as a unit working
        # directory, and it is that directory, not the build, from which the
        # launcher's relative operand is then resolved and refused.
        agent, pins, lock = self.installed(shell=True)
        (agent.build / 'run.sh').write_text('exec ' + agent.node + ' catalog/node/run.js\n')
        self.unit(agent, lock, '[Service]\nWorkingDirectory=' + str(agent.build) + '\n')
        self.assertIsNone(agent.verify(pins, lock))
        self.unit(agent, lock, '[Service]\nWorkingDirectory=' + SHELL + '\n')
        self.refuses(OUTSIDE, agent.verify, pins, lock)

    def test_catalog_audit_site_verify_unit_preflight(self):
        # Line 722. The preflight operand is safe from the build, which is all
        # the earlier audits check; it is not safe from this unit's directory.
        def prepare(agent):
            agent.add('check', {'kind': 'preflight', 'requires': [],
                                'run': ['{artifact:check}/run', 'catalog/check/ready']}, {'run': b'stand-in\n'})
        agent, pins, lock = self.installed(shell=True, prepare=prepare)
        self.unit(agent, lock, '[Service]\nWorkingDirectory=' + str(agent.build) + '\n')
        self.assertIsNone(agent.verify(pins, lock))
        self.unit(agent, lock, '[Service]\nWorkingDirectory=' + SHELL + '\n')
        self.refuses(OUTSIDE, agent.verify, pins, lock)

    def test_catalog_audit_site_verify_launcher_text(self):
        # Line 726. The launcher classifier skips comment lines; the text audit does not.
        agent, pins, lock = self.installed()
        (agent.build / 'run.sh').write_text('# helper: ' + str(agent.state / 'helper') + '\nexit 0\n')
        self.assertIsNone(agent.verify(pins, lock))
        (agent.build / 'run.sh').write_text('# helper: /fixture/other-agent/helper\nexit 0\n')
        self.refuses(FOREIGN, agent.verify, pins, lock)

    def test_catalog_audit_site_verify_launcher_commands(self):
        # Line 728. A relative operand is invisible to the text audit.
        agent, pins, lock = self.installed()
        (agent.build / 'run.sh').write_text('exec ' + agent.node + ' catalog/node/run.js\n')
        self.assertIsNone(agent.verify(pins, lock))
        (agent.build / 'run.sh').write_text('exec ' + agent.node + ' ../outside.js\n')
        self.refuses(OUTSIDE, agent.verify, pins, lock)

    def test_catalog_audit_site_launch_cwd_recipe(self):
        # Line 729. `cd` admits a pinned platform tool as a directory; the
        # recipe's relative environment path is then resolved from it.
        def prepare(agent):
            agent.recipe = {'agent': {'provider': {'env': {'CONFIG': 'config.json'}}}}
        agent, pins, lock = self.installed(shell=True, prepare=prepare)
        (agent.build / 'run.sh').write_text('cd ' + str(agent.build / 'catalog') + '\nexit 0\n')
        self.assertIsNone(agent.verify(pins, lock))
        (agent.build / 'run.sh').write_text('cd ' + SHELL + '\nexit 0\n')
        self.refuses(OUTSIDE, agent.verify, pins, lock)

    def test_catalog_audit_site_launch_cwd_unit(self):
        # Line 731. A unit environment path that stays inside the build from the
        # unit's own directory leaves it after the launcher changes directory.
        agent, pins, lock = self.installed()
        self.unit(agent, lock, '[Service]\nWorkingDirectory=' + str(agent.build / 'catalog/node') + '\n'
                               'Environment=CONFIG=../../config.json\n')
        self.assertIsNone(agent.verify(pins, lock))
        (agent.build / 'run.sh').write_text('cd ' + str(agent.build) + '\nexit 0\n')
        self.refuses(OUTSIDE, agent.verify, pins, lock)

    # ---- cook inputs, constructed environment, unit isolation (rows 48, 54) ---
    def test_catalog_L753_8(self):
        work, _, _ = self.paths()
        root = work / 'agent'
        def config(repo='inputs/host', framework='inputs/framework.tgz', dependency='inputs/dependency.tgz'):
            return {'cook': {'host_repo': repo, 'framework_archive': {'path': framework},
                             'host_dependencies': [{'archive': dependency}]}}
        inside = {key: str(root / value) for key, value in
                  (('repo', 'inputs/host'), ('framework', 'inputs/framework.tgz'), ('dependency', 'inputs/dependency.tgz'))}
        self.assertIsNone(catalog.input_paths(config(**inside), root))
        for key in inside:
            relative = dict(inside)
            del relative[key]
            self.refuses('catalog requires local cook inputs', catalog.input_paths, config(**relative), root)

    def test_catalog_L768_8(self):
        # shutil.copyfile is replaced because the first copy leaves a 0555
        # target that an unprivileged copy cannot reopen: without the guard a
        # real copy would stop with PermissionError here, and overwrite the
        # first tool for an account that may ignore file modes.
        def attempt(tools):
            agent = Agent(self)
            agent.versions['platform'] = {tool: 'a' * 64 for tool in tools}
            pins = agent.pins()
            self.copied = []
            def copy(source, target):
                self.copied.append((str(source), str(target)))
                if not os.path.lexists(target):
                    Path(target).write_bytes(b'stand-in tool\n')
            self.tools = agent.build / 'platform-bin'
            with patch.object(catalog.shutil, 'copyfile', side_effect=copy):
                return catalog.environment(agent.config, pins, agent.build)
        environment = attempt(['/bin/fixture-tool', '/usr/bin/other-tool'])
        self.assertEqual(environment['PATH'].split(os.pathsep)[-1], str(self.tools))
        self.assertEqual(sorted(path.name for path in self.tools.iterdir()), ['fixture-tool', 'other-tool'])
        self.refuses('platform tool basename collision', attempt, ['/bin/fixture-tool', '/usr/bin/fixture-tool'])
        self.assertEqual(self.copied, [('/bin/fixture-tool', str(self.tools / 'fixture-tool'))])

    def test_catalog_L842_4(self):
        agent = Agent(self)
        pins = agent.pins()
        body = ('[Service]\nWorkingDirectory=' + str(agent.build) + '\nEnvironment=%s\n'
                'ExecStart=' + agent.node + ' catalog/node/run.js\n')
        text = catalog.isolated_unit(agent.config, pins, agent.build, body % 'MODE=plain')
        self.assertTrue(text.startswith('[Service]\nEnvironment=\nPassEnvironment=\nEnvironmentFile=\n'))
        for name in ('LANG', 'HOME', 'TMPDIR', 'DATA_DIR', 'npm_config_offline'):
            self.refuses('unit environment overlaps constructed environment',
                         catalog.isolated_unit, agent.config, pins, agent.build, body % (name + '=plain'))


if __name__ == '__main__':
    unittest.main()
