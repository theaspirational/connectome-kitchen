#!/usr/bin/env python3
"""Deploy corpus: whole deployments through the Python oracle, kept as goldens.

A case is the input of one deployment: the four layers, the pinned versions,
the paths, the cook command, the recipe and the archives. `record` runs each
case through the oracle's deploy path and writes what came out; `check` runs
it again and compares; `compare` puts the steps kitchen has already ported
(config, inputs, archive, lock) beside the oracle on the same files, and then
runs the TS engine (deploy, cook) on the whole case at the same root and holds
its record against the oracle's.

    corpus.py record  --corpus DIR [--case NAME]
    corpus.py check   --corpus DIR [--case NAME]
    corpus.py compare --corpus DIR [--case NAME] [--node NODE]
    corpus.py import-layer --private DIR --public DIR --out DIR --case NAME=PLAYBOOK:INVENTORY:HOST ...

Nothing is fetched, no unit of the host is asked or touched, and nothing is
written outside the scratch directory and, for `record`, the corpus. See
README.md beside this file for what a golden holds and what it does not show.
"""
import argparse, base64, contextlib, copy, hashlib, importlib, json, os, pwd, re, shutil, stat, subprocess, sys
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ORACLE = ROOT / 'oracle'
sys.path[:0] = [str(HERE), str(ROOT / 'test/differential'), str(ORACLE / 'module_utils'), str(ORACLE / 'shim')]
import ts_port
from record import encode, canonical

# Top-level directories of the deployed hosts. A path under one of them is moved under the sandbox root.
MOVED = ('home', 'opt', 'srv', 'root', 'usr')
ABSOLUTE = re.compile(r'(?<![A-Za-z0-9_.~/-])/(?=(?:%s)/)' % '|'.join(MOVED))
# Of those, the ones the deployed hosts keep root-owned. Under the sandbox they are the user's own files and are shown as root's.
ROOT_OWNED = ('opt', 'srv', 'usr')
HEX64 = re.compile(r'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])')
SYSTEMD_PATH = re.compile(r'/[A-Za-z0-9_./:@+-]+')
PLACEHOLDER = 'corpus-placeholder-for-'
TEXT_LIMIT = 1 << 18


class CorpusError(RuntimeError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def move(value, prefix):
    """Every absolute path under a moved directory, anywhere inside a value, under `prefix`."""
    if isinstance(value, str):
        return ABSOLUTE.sub(prefix + '/', value)
    if isinstance(value, list):
        return [move(item, prefix) for item in value]
    if isinstance(value, dict):
        return {move(key, prefix): move(item, prefix) for key, item in value.items()}
    return value


def make_directory(path, mode=0o755, private_under=None):
    """Creates missing directories with exact modes: 0700 under `private_under`, `mode` elsewhere."""
    missing = []
    current = Path(path)
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir()
        directory.chmod(0o700 if private_under and directory.is_relative_to(private_under) and directory != private_under else mode)


def write(path, content, mode=0o644, private_under=None):
    make_directory(Path(path).parent, private_under=private_under)
    Path(path).write_bytes(content if isinstance(content, bytes) else content.encode())
    Path(path).chmod(mode)


def package_archive(name, version):
    """A registry-shaped archive with nothing but its manifest. The bytes are the same on any host."""
    manifest = json.dumps({'name': name, 'version': version}, sort_keys=True)
    return ts_port.tgz([['package/package.json', manifest]])


class Case:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.data = json.loads((self.directory / 'case.json').read_text())
        self.name = self.data['name']

    def tools(self):
        """The four engine paths of the role's cook command, by flag."""
        argv = self.data['cook_command']
        try:
            return {key: argv[argv.index(flag) + 1] for key, flag in
                    (('runtime', '--runtime'), ('entrypoint', '--entrypoint'), ('bun', '--bun-path'), ('node', '--node-path'))}
        except (ValueError, IndexError):
            raise CorpusError('case %s: the cook command names no runtime, entrypoint, Bun or Node' % self.name) from None


def cases(corpus, only=None):
    found = [Case(path.parent) for path in sorted(Path(corpus).glob('cases/*/case.json'))]
    if only:
        found = [case for case in found if case.name in only]
    if not found:
        raise CorpusError('no case found under ' + str(corpus))
    return found


@contextlib.contextmanager
def host_model(sandbox, account):
    """What the deployed host is taken to look like from inside the engine.

    The same seam as `safe_ancestors` of oracle/tests/deploy_fixtures.py, one step further. Every lstat still reaches a real
    file. What is changed is who owns it: the directories above the sandbox, the sandbox, its `home`, and everything under
    the root-owned directories of the deployed host are shown as root's, and the directories above the sandbox as 0755.
    The account's own tree keeps its real owner and modes, and the account's home is the one under the sandbox.
    """
    sandbox = Path(sandbox)
    above = set(sandbox.parents)
    as_root = [sandbox / name for name in ROOT_OWNED]
    original = Path.lstat
    record = pwd.getpwuid(os.geteuid())

    def metadata(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path in above:
            fields = list(info)
            fields[0] = stat.S_IFDIR | 0o755
            fields[4] = fields[5] = 0
            return os.stat_result(fields)
        if path == sandbox or path == sandbox / 'home' or any(path == top or path.is_relative_to(top) for top in as_root):
            fields = list(info)
            fields[4] = fields[5] = 0
            return os.stat_result(fields)
        return info

    def account_record(uid):
        if uid != os.geteuid():
            return pwd.getpwuid.__wrapped__(uid)
        return pwd.struct_passwd((account, 'x', record.pw_uid, record.pw_gid, '', str(sandbox / 'home' / account), '/usr/sbin/nologin'))
    account_record.__wrapped__ = pwd.getpwuid
    with patch.object(Path, 'lstat', metadata), patch.object(pwd, 'getpwuid', account_record):
        yield


def adapter_through_seam(original):
    """`connectome_deploy.run`, with the adapter script replaced by test/corpus/adapter_seam.py. Nothing else is changed."""
    def run(argv, cwd=None, adapter=False, unit_verify=False, env=None):
        if adapter:
            argv = [argv[0], str(HERE / 'adapter_seam.py')] + list(argv[2:])
        return original(argv, cwd=cwd, adapter=adapter, unit_verify=unit_verify, env=env)
    return run


def snapshot(root):
    rows = {}
    for directory, names, files in os.walk(root):
        for name in sorted(names + files):
            path = Path(directory) / name
            info = path.lstat()
            row = {'mode': '%04o' % stat.S_IMODE(info.st_mode)}
            if path.is_symlink():
                row = {'link': os.readlink(path)}
            elif path.is_dir():
                row['kind'] = 'directory'
            else:
                data = path.read_bytes()
                row['sha256'] = sha256(data)
                try:
                    if len(data) <= TEXT_LIMIT:
                        row['text'] = data.decode('utf-8')
                except UnicodeDecodeError:
                    pass
            rows[str(path)] = row
    return rows


def lines(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()] if Path(path).is_file() else []


class World:
    """One case laid out under one sandbox root, and the oracle run on it."""

    def __init__(self, case, sandbox, node=None):
        self.case, self.sandbox = case, Path(sandbox)
        self.node = node  # None: the oracle, in this process. Else the Node that runs the TS engine (ts_driver.mjs).
        if not SYSTEMD_PATH.fullmatch(str(self.sandbox)) or self.sandbox.resolve() != self.sandbox:
            raise CorpusError('the scratch path must be a real path of letters, digits and _./:@+- only: the engine refuses any other')
        self.account = case.data['account']
        self.home = self.sandbox / 'home' / self.account
        self.mutation = None

    def moved(self, value):
        return move(value, str(self.sandbox))

    def build(self, mutation=None):
        """Lays the files out and returns nothing; `self.request` is what the engine is then asked."""
        import connectome_config, connectome_deploy
        data, self.mutation = self.case.data, mutation
        ts_port.remove(self.sandbox)
        make_directory(self.sandbox)
        make_directory(self.sandbox / 'home')
        self.home.mkdir()
        self.home.chmod(0o755)
        layers = self.moved(copy.deepcopy(data['layers']))
        agent = layers['agent']
        self.pins = {}
        if 'recipe_file' in agent:
            text = self.moved((self.case.directory / data['recipe']['file']).read_bytes().decode('utf-8')).encode('utf-8')
            write(agent['recipe_file']['path'], text, 0o600, self.home)
            agent['recipe_file']['sha256'] = self.pins['recipe'] = sha256(text)
            if mutation == 'recipe-pin':
                # The pin as the layer commits it, where the corpus recipe is not those bytes; else a pin one digit off.
                committed = data['recipe'].get('committed_sha256')
                agent['recipe_file']['sha256'] = committed if committed and committed != sha256(
                    (self.case.directory / data['recipe']['file']).read_bytes()) else self.pins['recipe'][:-1] + ('0' if self.pins['recipe'][-1] != '0' else '1')
        cook = agent.get('cook', {})
        records = [(entry, 'archive') for entry in cook.get('host_dependencies', [])]
        if 'framework_archive' in cook:
            records.append((cook['framework_archive'], 'path'))
        originals = [entry[key] for layer in (data['layers']['agent'].get('cook', {}),)
                     for entry, key in [(e, 'archive') for e in layer.get('host_dependencies', [])] +
                     ([(layer['framework_archive'], 'path')] if 'framework_archive' in layer else [])]
        for index, ((entry, key), original) in enumerate(zip(records, originals)):
            source = data['archives'][original]
            content = (self.case.directory / source['file']).read_bytes() if 'file' in source else package_archive(**source['package'])
            if entry['sha256'] is None and 'package' in source:
                entry['sha256'] = sha256(content)  # a made-up package: its pin is the digest of the bytes generated here
            if sha256(content) != entry['sha256']:
                raise CorpusError('case %s: archive %d is not the bytes its pin names' % (self.case.name, index))
            write(entry[key], content)
            if mutation == 'archive-pin' and index == 0:
                entry['sha256'] = entry['sha256'][:-1] + ('0' if entry['sha256'][-1] != '0' else '1')
        self.layers = [connectome_config.public_catalog(layers['defaults'], layers['catalog']), layers['private'], layers['group'], agent]
        self.raw_layers = layers
        self.effective = connectome_config.merge_layers(copy.deepcopy(self.layers))
        self.versions = copy.deepcopy(data['versions'])
        self.root, self.base, self.unit_dir = (self.moved(data[key]) for key in ('root', 'base', 'unit_dir'))
        self.role_command = self.moved(data['cook_command'])
        # The secrets file: placeholders under the declared names, never a value of the host.
        self.names = connectome_deploy.secret_names(self.effective)
        self.secrets = {name: PLACEHOLDER + name for name in self.names}
        secrets_file = Path(self.effective.get('secrets_file', str(self.home / '.config/connectome-secrets' / (self.effective['name'] + '.json'))))
        if mutation != 'secrets-file-missing':
            write(secrets_file, json.dumps(self.secrets, sort_keys=True), 0o600, self.home)
        # The engines the cook command names. The runtime is the stand-in; nothing here is Bun, Node or cook.
        tools = {key: self.moved(value) for key, value in self.case.tools().items()}
        stand_in = '#!%s -I\nimport runpy, sys\nsys.dont_write_bytecode = True\nrunpy.run_path(%r, run_name="__main__")\n' % (sys.executable, str(HERE / 'stand_in_cook.py'))
        write(tools['entrypoint'], '#!/bin/sh\n# stand-in entrypoint: never executed\nexit 64\n', 0o755)
        for key in ('bun', 'node', 'runtime'):
            write(tools[key], stand_in if tools[key] == tools['runtime'] else '#!/bin/sh\n# stand-in engine: never executed\nexit 64\n', 0o755)
        self.tools_moved = tools
        # What the recipe's host-installed servers point at.
        self.external = []
        if 'recipe_file' in agent:
            recipe = json.loads(Path(agent['recipe_file']['path']).read_text())
            private = {(entry['server'], entry['path']): entry['kind'] for entry in self.effective.get('external_data_args', [])}
            for name, server in sorted((recipe.get('mcpServers') or {}).items()):
                if not (isinstance(server, dict) and isinstance(server.get('source'), dict) and 'external' in server['source']):
                    continue
                if not Path(server['command']).exists():
                    write(server['command'], '#!/bin/sh\n# stand-in server command: never executed\nexit 64\n', 0o755)
                self.external.append(server['command'])
                for raw in server.get('args', []):
                    value = connectome_deploy.path_like_arg(raw)
                    if value is None or not value.startswith('/') or Path(value).exists():
                        continue
                    kind = private.get((name, value))
                    if kind == 'dir' or (kind is None and not Path(value).suffix):
                        make_directory(value, private_under=self.home)
                        if kind:
                            Path(value).chmod(0o700)
                    else:
                        write(value, '', 0o600 if kind else 0o644, self.home)
            # A server command that is not also one of the engines: without an engine the adapter refuses first, for another reason.
            self.external = [command for command in self.external if command not in tools.values()]
            if mutation == 'external-command-missing' and self.external:
                Path(self.external[0]).unlink()
        # The oracle's own fake service manager; with this marker it answers `inactive` until it is told to start a unit.
        make_directory(Path(self.root).parent, private_under=self.home)
        (Path(self.root).parent / 'track-unit-state').write_text('')
        self.systemctl = [sys.executable, str(ORACLE / 'tests/fixtures/systemctl.py'), self.root]
        self.request = {'base': self.base, 'root': self.root, 'unit_dir': self.unit_dir, 'layers': copy.deepcopy(self.layers),
                        'versions': self.versions, 'engines': {'cook': tools}, 'systemctl': self.systemctl}

    def port(self, job):
        """One call of the TS engine, in the same sandbox and behind the same two seams as the oracle's (ts_driver.mjs)."""
        job.update(sandbox=str(self.sandbox), account=self.account, root_owned=list(ROOT_OWNED), seam=str(HERE / 'adapter_seam.mjs'),
                   adapter=[sys.executable, str(ORACLE / 'scripts/cook-adapter.py')])  # the words the oracle's shim puts first: one build name for both engines
        job_file, out_file = (self.sandbox.parent / ('%s.%s-%d.json' % (self.sandbox.name, kind, os.getpid())) for kind in ('job', 'outcome'))
        try:
            job_file.touch(mode=0o600)
            job_file.write_text(json.dumps(job))
            done = subprocess.run([self.node, str(HERE / 'ts_driver.mjs'), str(job_file), str(out_file)], cwd=ROOT, capture_output=True, text=True, timeout=600)
            if done.returncode:
                raise CorpusError('the TS engine stopped on case %s: %s' % (self.case.name, done.stderr[-2000:]))
            if done.stderr:
                sys.stderr.write(done.stderr)
            return json.loads(out_file.read_text())
        finally:
            job_file.unlink(missing_ok=True)
            out_file.unlink(missing_ok=True)

    def dispatch(self, command, **extra):
        import connectome_deploy, kitchen_v1
        request = dict(copy.deepcopy(self.request), **extra)
        if self.node:
            return self.port({'op': 'dispatch', 'command': command, 'request': request, 'secrets': self.secrets if command == 'apply' else None})
        with host_model(self.sandbox, self.account), patch.object(connectome_deploy, 'run', adapter_through_seam(connectome_deploy.run)):
            envelope, status = kitchen_v1.dispatch({'contract': kitchen_v1.CONTRACT, 'command': command, 'request': request},
                                                   self.secrets if command == 'apply' else None)
        return {'status': status, 'core': envelope['core']}

    def role_view(self):
        """The role's own call: its cook command, check mode. Nothing is run and nothing is written."""
        import connectome_deploy, connectome_inputs
        if self.node:
            return self.port({'op': 'role', 'role': {'root': self.root, 'unit_dir': self.unit_dir, 'base': self.base, 'effective': copy.deepcopy(self.effective),
                                                     'versions': self.versions, 'cook_command': self.role_command}})
        with host_model(self.sandbox, self.account), patch.object(connectome_deploy, 'run', side_effect=AssertionError('check mode ran a tool')):
            try:
                files = connectome_inputs.collect(self.effective)
                deployment = connectome_deploy.Deployment(self.root, self.unit_dir, copy.deepcopy(self.effective), self.versions, self.role_command,
                                                          ['systemctl', '--user'], check=True, base=self.base, operator_files=files)
                return {'request_id': deployment.request_id, 'build_dir': str(deployment.build), 'pins': deployment.pins,
                        'spec_sha256': sha256(connectome_deploy.canonical(deployment.spec).encode()), 'cook_command': self.role_command,
                        'prepare': deployment.prepare(), 'activate': deployment.activate()}
            except ValueError as error:
                return {'refused': error.public() if hasattr(error, 'public') else {'exception': type(error).__name__, 'message': str(error)}}

    def shim_command(self):
        """The cook command as the oracle's shim words it (oracle/shim/kitchen_v1.py)."""
        cook = self.tools_moved
        return [sys.executable, str(ORACLE / 'scripts/cook-adapter.py'), '--spec', '{recipe}', '--out', '{build_dir}', '--state', '{state_dir}',
                '--runtime', cook['runtime'], '--entrypoint', cook['entrypoint'], '--bun-path', cook['bun'], '--node-path', cook['node']]

    def install_only(self, files):
        """The same deployment installed and not started (`activation: install_only`), twice, through the engine's own class.

        Not through the contract: there the engine looks for the host's `systemd-analyze` and runs it on the installed unit,
        and the corpus asks nothing of the host. `analyze_command=[]` is the engine's own way to say that there is no verifier.
        The steps are the ones the module boundary takes around each call (oracle/library/connectome_release.py), without its lock.
        """
        import connectome_deploy
        from connectome_errors import SafeError
        config = dict(copy.deepcopy(self.effective), activation='install_only')
        if self.node:
            return self.port({'op': 'install_only', 'secrets': self.secrets, 'files': files, 'role': {
                'root': self.root, 'unit_dir': self.unit_dir, 'base': self.base, 'effective': config, 'versions': self.versions,
                'cook_command': self.shim_command(), 'systemctl': self.systemctl}})
        applies = []
        with host_model(self.sandbox, self.account), patch.object(connectome_deploy, 'run', adapter_through_seam(connectome_deploy.run)):
            try:
                for _ in range(2):
                    deployment = connectome_deploy.Deployment(self.root, self.unit_dir, copy.deepcopy(config), self.versions, self.shim_command(), self.systemctl,
                                                              base=self.base, operator_files=files, analyze_command=[])
                    deployment.validate_paths()
                    deployment.preflight_activation()
                    connectome_deploy.make_directory(deployment.root)
                    applies.append({'prepare': deployment.prepare()})
                    applies[-1]['activate'] = deployment.activate(copy.deepcopy(self.secrets))
            except SafeError as error:
                return {'applies': applies, 'refused': error.public()}
        return {'applies': applies}

    def operator_files(self):
        import connectome_inputs
        return connectome_inputs.collect(self.effective)

    def operator_files_or_none(self):
        try:
            return self.operator_files()
        except ValueError:
            return None

    def ported(self):
        """The calls of this deployment that kitchen has a port of: [id, function, args, kwargs], on the files of this world."""
        import connectome_deploy
        effective, agent = self.effective, self.raw_layers['agent']
        pins = connectome_deploy.resolve_pins(effective, self.versions)
        framework = pins['runtime']['agent-framework']
        build_config = {key: value for key, value in effective.items() if key not in ('activation', 'exclusive_with')}
        jobs = [
            ['config.public_catalog', 'connectome_config.public_catalog', [self.raw_layers['defaults'], self.raw_layers['catalog']], {}],
            ['config.merge_layers', 'connectome_config.merge_layers', [self.layers], {}],
            ['config.required_names', 'connectome_config.required_names', [effective], {}],
            ['inputs.validate', 'connectome_inputs.validate', [effective], {}],
            ['inputs.collect', 'connectome_inputs.collect', [effective], {}],
            ['inputs.payloads', 'connectome_inputs.payloads', [effective, self.operator_files()], {}],
            ['archive.records_metadata', 'connectome_archive.records_metadata', [effective], {}],
            ['archive.dependency_inputs', 'connectome_archive.dependency_inputs', [effective, framework], {}],
            ['archive.dependency_provenance', 'connectome_archive.dependency_provenance', [effective, framework], {}],
        ]
        import connectome_archive
        for index, entry in enumerate(connectome_archive.dependency_inputs(effective, framework)):
            jobs.append(['archive.archive_manifest.%d' % index, 'connectome_archive.archive_manifest', [entry], {}])
        if 'framework_archive' in effective.get('cook', {}):
            jobs.append(['archive.framework_provenance', 'connectome_archive.framework_provenance', [effective, framework], {}])
        # The refusals met in practice, as the same calls see them: a recipe and an archive that are not the bytes their pins name.
        if 'recipe_file' in effective:
            wrong = copy.deepcopy(effective)
            wrong['recipe_file']['sha256'] = self.case.data['recipe'].get('committed_sha256') or '0' * 64
            if wrong['recipe_file']['sha256'] == effective['recipe_file']['sha256']:
                wrong['recipe_file']['sha256'] = '0' * 64
            jobs.append(['refusal.inputs.collect.recipe-pin', 'connectome_inputs.collect', [wrong], {}])
        wrong = copy.deepcopy(effective)
        cook = wrong.get('cook', {})
        first = (cook.get('host_dependencies') or [cook.get('framework_archive')])[0]
        if first:
            first['sha256'] = first['sha256'][:-1] + ('0' if first['sha256'][-1] != '0' else '1')
            jobs.append(['refusal.archive.dependency_provenance.archive-pin', 'connectome_archive.dependency_provenance', [wrong, framework], {}])
        # After `apply`: the build as the engine left it.
        build = Path(self.root) / 'builds' / self.request_id()
        if (build / 'connectome.lock').is_file():
            kwargs = {'config': build_config, 'framework_pin': framework, 'host_pin': pins['runtime']['connectome-host'],
                      'required_names': connectome_deploy.environment_names(effective)}
            if (build / 'framework.lock.json').is_file():
                kwargs['framework_record'] = json.loads((build / 'framework.lock.json').read_text())
                jobs.append(['archive.validate_provenance', 'connectome_archive.validate_provenance', [kwargs['framework_record'], build_config, framework], {}])
            jobs.append(['lock.read_lock', 'connectome_lock.read_lock', [str(build / 'connectome.lock')], kwargs])
            jobs.append(['lock.read_lock.bare', 'connectome_lock.read_lock', [str(build / 'connectome.lock')], {}])
            raw = json.loads((build / 'connectome.lock').read_text())
            jobs.append(['archive.check_dependencies', 'connectome_archive.check_dependencies', [raw, build_config, framework], {}])
            jobs.append(['inputs.installed', 'connectome_inputs.installed', [str(build), build_config], {}])
        return [[ident, function, encode(args), encode(kwargs)] for ident, function, args, kwargs in jobs]

    def request_id(self):
        """The name the contract path gives the build: the engine's own, asked in check mode."""
        import connectome_deploy, kitchen_v1
        with host_model(self.sandbox, self.account):
            return connectome_deploy.Deployment(self.root, self.unit_dir, copy.deepcopy(self.effective), self.versions, self.shim_command(), check=True, base=self.base).request_id


def oracle_outcomes(world, jobs):
    from codec import decode
    with host_model(world.sandbox, world.account):
        return {ident: ts_port.invoke(function, decode(args), decode(kwargs)) for ident, function, args, kwargs in jobs}


def brief(value):
    """An outcome for a golden: bytes and long texts (a recipe in transport) as their digests."""
    if isinstance(value, dict):
        if set(value) == {'$bytes'}:
            return {'$sha256-of-bytes': sha256(base64.b64decode(value['$bytes']))}
        return {key: brief(item) for key, item in value.items()}
    if isinstance(value, list):
        return [brief(item) for item in value]
    if isinstance(value, str) and len(value) > 1024:
        return {'$sha256-of-text': sha256(value.encode('utf-8'))}
    return value


MUTATIONS = (('recipe-pin', 'plan'), ('archive-pin', 'plan'), ('secrets-file-missing', 'plan'), ('external-command-missing', 'apply'))


def run_case(case, sandbox, node=None):
    """One case at one sandbox root. Returns the record and the world, left as `apply` made it.

    With `node`, every engine call (the role's view, plan, apply, the second apply, the refusals) goes to the TS engine
    instead. The files are laid out by the same code, and the record is put together the same way.
    """
    ts_port.initialize()
    for name in ('connectome_deploy', 'connectome_cook', 'kitchen_v1'):
        importlib.import_module(name)
    import connectome_config
    world = World(case, sandbox, node)
    refusals = {}
    for mutation, command in MUTATIONS:
        world.build(mutation)
        if mutation == 'recipe-pin' and 'recipe_file' not in world.raw_layers['agent'] or mutation == 'external-command-missing' and not world.external:
            continue
        refusals[mutation] = dict(world.dispatch(command, **({'operator_files': world.operator_files_or_none()} if command == 'apply' else {})), command=command)
        if mutation == 'external-command-missing':
            refusals[mutation]['left_behind'] = sorted(str(path) for path in Path(world.root).rglob('*') if path.name in ('.prepared', 'current'))
    # Installed and not started, on a sandbox of its own: with the units of a started deployment active the engine refuses this.
    world.build()
    before = snapshot(world.sandbox)
    installed = world.install_only(world.operator_files())
    after = snapshot(world.sandbox)
    kept = (world.unit_dir, str(Path(world.root) / 'state'))  # the text of what the engine itself writes; the build is the one `apply` shows
    installed['tree'] = {path: ({key: value for key, value in row.items() if key != 'text'} if not path.startswith(kept) else row)
                         for path, row in after.items() if before.get(path) != row}
    installed['systemctl_calls'] = lines(Path(world.root) / 'systemctl-calls')
    installed['systemctl_calls_before_the_root_existed'] = lines(Path(world.root).parent / 'preflight-systemctl-calls')
    installed['cook_calls'] = len(lines(Path(world.root) / 'cook-calls.jsonl'))
    world.build()
    before = snapshot(world.sandbox)
    files = world.operator_files()
    record = {
        'case': case.name,
        'engine': {'oracle': 'a49b5dc', 'combine': 'ansible-core' if connectome_config.combine is not connectome_config._python_combine else 'stand-in of the oracle (no ansible-core here)',
                   'cook': 'stand-in (test/corpus/stand_in_cook.py)', 'systemctl': 'fake of the oracle (oracle/tests/fixtures/systemctl.py)',
                   'verify_cook': 'recorded, not performed (test/corpus/adapter_seam.py)', 'ownership': 'modelled (host_model in test/corpus/corpus.py)'},
        'substitutions': case.data.get('substitutions', []),
        'config': {'layers': world.layers, 'effective': world.effective, 'secret_names': world.names},
        'inputs': {'operator_files': {name: {'sha256': sha256(base64.b64decode(value))} for name, value in files.items()}},
        'role': world.role_view(),
    }
    record['plan'] = world.dispatch('plan')
    untouched = snapshot(world.sandbox)
    record['plan']['wrote'] = sorted(path for path in untouched if path not in before or untouched[path] != before[path])
    record['apply'] = world.dispatch('apply', operator_files=files)
    after = snapshot(world.sandbox)
    record['apply']['tree'] = {path: row for path, row in after.items() if before.get(path) != row}
    record['apply']['removed'] = sorted(path for path in before if path not in after)
    record['apply']['systemctl_calls'] = lines(Path(world.root) / 'systemctl-calls')
    record['apply']['systemctl_calls_before_the_root_existed'] = lines(Path(world.root).parent / 'preflight-systemctl-calls')
    record['apply']['cook_calls'] = lines(Path(world.root) / 'cook-calls.jsonl')
    record['apply']['verify_cook_calls'] = lines(Path(world.root) / 'verify-cook-calls.jsonl')
    record['reapply'] = world.dispatch('apply', operator_files=files)
    again = snapshot(world.sandbox)
    volatile = ('systemctl-calls', 'unit-states.json')
    record['reapply']['tree_changes'] = sorted(path for path in set(after) | set(again) if after.get(path) != again.get(path) and Path(path).name not in volatile)
    record['plan_after_apply'] = world.dispatch('plan')
    # A later release of the same deployment: another cook pin, so another build beside the one that is current. Then the
    # first request again, which is how a release is taken back: its build is still there and only the pointer moves.
    later = copy.deepcopy(world.versions)
    pin = later['runtime']['cook']
    later['runtime']['cook'] = pin[:-1] + ('0' if pin[-1] != '0' else '1')
    logs = ('cook-calls.jsonl', 'verify-cook-calls.jsonl')
    state = again
    for name, extra in (('rebuild', {'versions': later}), ('release_back', {})):
        calls = {log: len(lines(Path(world.root) / log)) for log in ('systemctl-calls',) + logs}
        record[name] = world.dispatch('apply', operator_files=files, **extra)
        now = snapshot(world.sandbox)
        record[name]['tree'] = {path: row for path, row in now.items() if state.get(path) != row and Path(path).name not in volatile + logs}
        for path, row in record[name]['tree'].items():
            if Path(path).name == 'ownership.json':
                # The engine sorts the entries by path, and with two builds that order follows their names, which follow the root.
                # The digest of the file stays; the entries are listed in an order that does not: by shape, the first build first.
                first = world.request_id()
                entries = json.loads(row.pop('text'))['entries']
                row['entries_by_shape'] = sorted(entries, key=lambda entry: (HEX64.sub('#', entry['path']), first not in entry['path'], HEX64.sub('#', json.dumps(entry, sort_keys=True))))
        record[name]['removed'] = sorted(path for path in state if path not in now)
        record[name]['systemctl_calls'] = lines(Path(world.root) / 'systemctl-calls')[calls['systemctl-calls']:]
        record[name]['cook_calls'] = lines(Path(world.root) / 'cook-calls.jsonl')[calls['cook-calls.jsonl']:]
        record[name]['verify_cook_calls'] = lines(Path(world.root) / 'verify-cook-calls.jsonl')[calls['verify-cook-calls.jsonl']:]
        state = now
    jobs = world.ported()
    record['ported'] = brief(oracle_outcomes(world, jobs))
    record['refusals'] = refusals
    record['install_only'] = installed
    # Tools and fixtures of this checkout appear in argv; they are not part of what the engine decided.
    text = json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False)
    for value, token in ((str(world.sandbox), '{ROOT}'), (str(ROOT), '{KITCHEN}'), (world.request_id(), '{REQUEST}')):
        text = text.replace(value, token)
    text = text.replace(json.dumps(sys.executable), json.dumps('{PYTHON}'))  # as a whole argument only, never inside a moved path
    text = re.sub(r'\{ROOT\}/(%s)(?=[/"\\])' % '|'.join(MOVED), r'{ROOT:\1}', text)
    return text, world, jobs


def normalize(first, second):
    """Two runs at two sandbox roots -> one text. A digest that differs between them is derived from a path and gets a name."""
    one, two = HEX64.findall(first), HEX64.findall(second)
    if len(one) != len(two) or HEX64.sub('#', first) != HEX64.sub('#', second):
        raise CorpusError('the two runs differ in more than digests: the oracle run is not reproducible at another root')
    names = {}
    for left, right in zip(one, two):
        if left != right:
            names.setdefault(left, '{sha256:path-derived:%d}' % (len(names) + 1))
    for left, right in zip(one, two):
        if (left in names) != (left != right):
            raise CorpusError('a digest is path-derived in one place and not in another')
    return HEX64.sub(lambda match: names.get(match[0], match[0]), first), names


SYNTHETIC = 'synthetic'


def scratch_of(corpus, given):
    if given:
        return Path(given).resolve()
    if corpus == SYNTHETIC or Path(corpus).resolve().is_relative_to(ROOT):
        return (ROOT / '.cache/corpus').resolve()
    return (Path(corpus) / 'scratch').resolve()


def golden_of(corpus, case):
    return (HERE / 'synthetic' if corpus == SYNTHETIC else Path(corpus) / 'goldens') / (case.name + '.golden')


def located(corpus, scratch):
    """The directory that holds `cases/`. The synthetic corpus is generated, from test/corpus/synthetic.py, each time."""
    if corpus != SYNTHETIC:
        return Path(corpus)
    import yaml, synthetic
    generated = scratch / 'synthetic-cases'
    ts_port.remove(generated)
    catalog = {}
    for path in sorted((ORACLE / 'connectors').glob('*.yml')):
        catalog.update(yaml.safe_load(path.read_text()))
    synthetic.build(generated, yaml.safe_load((ORACLE / 'defaults/agent.yml').read_text()), catalog, yaml.safe_load((ORACLE / 'versions.yml').read_text()))
    return generated


def produce(corpus, case, scratch, keep=False):
    worlds = []
    try:
        texts = []
        for label in ('a', 'bb'):  # of different length, so that a length that leaks into a golden shows
            text, world, jobs = run_case(case, scratch / label / case.name)
            worlds.append(world)
            texts.append(text)
        try:
            text, names = normalize(*texts)
        except CorpusError:
            for label, content in zip(('a', 'bb'), texts):
                (scratch / (case.name + '.' + label + '.txt')).write_text(content)
            raise
        return text + '\n', worlds[-1], jobs, names, texts[-1]  # the jobs name the files of the last world; the last text is its record as run
    finally:
        for world in worlds[:-1] if keep and len(worlds) == 2 else worlds:
            ts_port.remove(world.sandbox)


def command_record(args, check=False):
    scratch = scratch_of(args.corpus, args.scratch)
    failed = False
    for case in cases(located(args.corpus, scratch), args.case):
        text, _world, _jobs, _names, _raw = produce(args.corpus, case, scratch)
        path = golden_of(args.corpus, case)
        if check:
            same = path.is_file() and path.read_text() == text
            print('%s %s' % ('PASS' if same else 'FAIL', case.name))
            failed = failed or not same
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            os.chmod(path, 0o644 if args.corpus == SYNTHETIC else 0o600)
            record = json.loads(text)
            print('%s: plan %d, apply %d, reapply %d, rebuild %d, release back %d, install only %s, %d ported calls, %d refusals' % (
                case.name, record['plan']['status'], record['apply']['status'], record['reapply']['status'], record['rebuild']['status'],
                record['release_back']['status'], 'refused' if 'refused' in record['install_only'] else '%d applies' % len(record['install_only']['applies']),
                len(record['ported']), len(record['refusals'])))
    return int(failed)


def differences(oracle, port, path='', limit=12):
    """Where two records part: the paths of the first values that differ, for a message."""
    if type(oracle) is not type(port):
        return [path or '.']
    if isinstance(oracle, dict):
        found = []
        for key in sorted(set(oracle) | set(port)):
            if key not in oracle or key not in port:
                found.append('%s/%s (only in the %s)' % (path, key, 'oracle' if key in oracle else 'port'))
            else:
                found += differences(oracle[key], port[key], path + '/' + key, limit)
            if len(found) >= limit:
                break
        return found[:limit]
    if isinstance(oracle, list):
        if len(oracle) != len(port):
            return ['%s (length %d, port %d)' % (path, len(oracle), len(port))]
        return [entry for index, (left, right) in enumerate(zip(oracle, port)) for entry in differences(left, right, '%s/%d' % (path, index), limit)][:limit]
    return [] if oracle == port else [path]


def engine_differences(case, world, raw, node):
    """The TS engine on the whole case, at the root the oracle has just run at. Returns where its record differs."""
    text, port_world, _jobs = run_case(case, world.sandbox, node=node)
    if text == raw:
        return []
    return differences(json.loads(raw), json.loads(text)) or ['(the records differ in their text only)']


def command_compare(args):
    """The ported steps, live: oracle and TS port on the same files, outcome against outcome as canonical bytes.

    Then the engine: plan, apply, what apply left, the second apply and the refusals of the TS engine against the oracle's.
    """
    scratch = scratch_of(args.corpus, args.scratch)
    failed = False
    for case in cases(located(args.corpus, scratch), args.case):
        text, world, jobs, names, raw = produce(args.corpus, case, scratch, keep=True)
        try:
            oracle = oracle_outcomes(world, jobs)
            outcomes = ts_port.node_outcomes([{'id': ident, 'function': function, 'args': call_args, 'kwargs': kwargs} for ident, function, call_args, kwargs in jobs], args.node)
            counts, rows = {}, {}
            for (ident, function, _a, _k), line in zip(jobs, outcomes):
                kind = 'equal' if canonical(oracle[ident]) == line else 'mismatch'
                counts[kind] = counts.get(kind, 0) + 1
                rows[ident] = kind
                if kind != 'equal':
                    failed = True
                    print('  MISMATCH %s %s (%s)\n    oracle %s\n    port   %s' % (case.name, ident, function, canonical(oracle[ident])[:400], line[:400]))
            # And against the golden: what the oracle answers now is what was recorded.
            golden = golden_of(args.corpus, case)
            recorded = golden.is_file() and golden.read_text() == text
            # And the engine itself. It lays the case out again at this root, so it comes after everything that reads the oracle's files.
            parted = [] if args.steps_only else engine_differences(case, world, raw, args.node)
            for where in parted:
                print('  ENGINE %s differs at %s' % (case.name, where))
            print('%s %s: %d ported calls, %d equal, %d mismatch; golden %s; engine %s' % (
                'PASS' if counts.get('mismatch', 0) == 0 and recorded and not parted else 'FAIL', case.name, len(jobs), counts.get('equal', 0), counts.get('mismatch', 0),
                'reproduced' if recorded else 'NOT reproduced',
                'not run' if args.steps_only else 'equal: role view, plan, apply, tree, re-apply, rebuild, release back, install only, refusals' if not parted else 'DIFFERS in %d places' % len(parted)))
            failed = failed or not recorded or bool(parted)
        finally:
            ts_port.remove(world.sandbox)
    return int(failed)


# ---------------------------------------------------------------------------------------------------------------------------
# import-layer: a case from a private deployment layer laid out like oracle/examples/private-skeleton, with one playbook
# and one inventory directory per agent. Nothing of a layer is named here; the caller names the playbook, inventory and host.

def resolver(sources, files_root, nominal_root):
    """Evaluates the variables of a play the way the role would see them: lazily, each expression with the names it uses."""
    import jinja2, yaml
    from jinja2 import meta
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined)
    environment.filters.update(from_yaml=yaml.safe_load, combine=lambda left, right: dict(left, **right), to_json=json.dumps,
                               bool=lambda value: value in (True, 1, 'true', 'True', 'yes', 'on'))
    done, active = {}, []

    def lookup(kind, argument):
        if kind != 'file':
            raise CorpusError('only file lookups are evaluated')
        path = Path(argument)
        return (files_root / path.relative_to(nominal_root) if path.is_relative_to(nominal_root) else path).read_text().rstrip('\n')

    def render(value):
        if isinstance(value, dict):
            return {key: render(item) for key, item in value.items()}
        if isinstance(value, list):
            return [render(item) for item in value]
        if not isinstance(value, str) or '{{' not in value:
            return value
        source = value.strip()
        context = {name: get(name) for name in meta.find_undeclared_variables(environment.parse(source)) if name != 'lookup'}
        context['lookup'] = lookup
        if source.startswith('{{') and source.endswith('}}') and source.count('{{') == 1:
            return environment.compile_expression(source[2:-2].strip(), undefined_to_none=False)(**context)
        return environment.from_string(value).render(**context)

    def get(name):
        if name not in done:
            if name not in sources:
                raise CorpusError('the play uses a variable the layer does not set: ' + name)
            if name in active:
                raise CorpusError('variable defined by itself: ' + name)
            active.append(name)
            done[name] = render(sources[name])
            active.pop()
        return done[name]
    return get


def command_import(args):
    import yaml
    private, public, out = Path(args.private).resolve(), Path(args.public).resolve(), Path(args.out).resolve()
    if out.is_relative_to(ROOT):
        raise CorpusError('a corpus imported from a private layer stays outside this repository')
    nominal = Path(args.controller_checkout)
    catalog = {}
    for path in sorted((public / 'connectors').glob('*.yml')):
        catalog.update(yaml.safe_load(path.read_text()))
    defaults = yaml.safe_load((public / 'defaults/agent.yml').read_text())
    role_defaults = yaml.safe_load((public / 'roles/connectome_agent/defaults/main.yml').read_text())
    stores = [Path(directory) for directory in args.archives or []]
    for spec in args.case:
        name, _, rest = spec.partition('=')
        playbook, inventory, host = rest.split(':')
        play = yaml.safe_load((private / playbook).read_text())[0]
        sources = dict(role_defaults)
        for path in sorted((private / inventory / 'group_vars').glob('*.yml')):
            sources.update(yaml.safe_load(path.read_text()) or {})
        for entry in play.get('vars_files', []):
            sources.update(yaml.safe_load((private / entry).read_text()) or {})
        sources.update(play.get('vars', {}))
        sources.update(playbook_dir=str(nominal), inventory_hostname=host, connectome_operator=dict(sources.get('connectome_operator', {})))
        get = resolver(sources, private, nominal)
        agent = copy.deepcopy(get('connectome_agent'))
        versions_file = Path(get('connectome_versions_file'))
        versions = yaml.safe_load((private / versions_file.relative_to(nominal)).read_text())
        directory = out / 'cases' / name
        ts_port.remove(directory)
        directory.mkdir(parents=True, mode=0o700)
        substitutions, case = [], {
            'name': name, 'account': get('ansible_user'), 'versions': versions,
            'base': get('connectome_base'), 'root': get('connectome_root'), 'unit_dir': get('connectome_unit_dir'),
            'cook_command': get('connectome_cook_command'), 'systemctl': get('connectome_systemctl'), 'archives': {},
            'source': {'playbook': playbook, 'inventory': inventory, 'host': host, 'controller_checkout': str(nominal)}}
        substitutions.append('The controller checkout is taken to be at %s: the layer does not say where it is, and a recipe the layer keeps in its own tree is named by that path.' % nominal)
        if 'recipe_file' in agent:
            record = agent['recipe_file']
            path = Path(record['path'])
            case['recipe'] = {'file': 'recipe.json', 'committed_sha256': record['sha256']}
            if path.is_relative_to(nominal):
                content = (private / path.relative_to(nominal)).read_bytes()
                case['recipe']['matches_committed_pin'] = sha256(content) == record['sha256']
            else:
                template = json.loads((private / inventory / args.template).read_text())
                template['agent']['systemPrompt'] = 'Placeholder system prompt of the deploy corpus. The real prompt is withheld from the corpus inputs.'
                content = (json.dumps(template, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
                case['recipe']['matches_committed_pin'] = False
                substitutions.append('The recipe is the layer\'s template (%s) with a placeholder system prompt, because the operator\'s recipe and its prompt are not in the corpus inputs. Its pin is the digest of those bytes, not the committed one; the committed pin is kept as `recipe.committed_sha256` and is what the recipe-pin refusal uses.' % args.template)
                record['sha256'] = sha256(content)
            (directory / 'recipe.json').write_bytes(content)
            os.chmod(directory / 'recipe.json', 0o600)
            substitutions.append('Every path under /%s in the layers and in the recipe is moved under the sandbox root, so the recipe the engine reads is not byte for byte the committed one and its pin is the digest of the moved bytes.' % ', /'.join(MOVED))
        cook = agent.get('cook', {})
        entries = [(entry, 'archive') for entry in cook.get('host_dependencies', [])] + ([(cook['framework_archive'], 'path')] if 'framework_archive' in cook else [])
        for entry, key in entries:
            wanted = entry['sha256']
            found = next((store / Path(entry[key]).name for store in stores
                          if (store / Path(entry[key]).name).is_file() and sha256((store / Path(entry[key]).name).read_bytes()) == wanted), None)
            if found:
                (out / 'files').mkdir(exist_ok=True, mode=0o700)
                shutil.copyfile(found, out / 'files' / (wanted + '.tgz'))
                case['archives'][entry[key]] = {'file': '../../files/' + wanted + '.tgz', 'pinned_bytes': True}
            else:
                package = {'name': entry.get('name', '@' + 'animalabs/agent-framework'), 'version': entry['version']}
                case['archives'][entry[key]] = {'package': package, 'pinned_bytes': False, 'committed_sha256': wanted}
                entry['sha256'] = sha256(package_archive(**package))
                substitutions.append('Archive %s is a stand-in holding only a manifest with the declared name and version: no file with the pinned digest was found. Its pin is the digest of the stand-in.' % Path(entry[key]).name)
        case['layers'] = {'defaults': defaults, 'catalog': catalog, 'private': get('connectome_private'), 'group': get('connectome_group'), 'agent': agent}
        case['substitutions'] = substitutions
        (directory / 'case.json').write_text(json.dumps(case, indent=1, sort_keys=True, ensure_ascii=False) + '\n')
        os.chmod(directory / 'case.json', 0o600)
        print('%s: account %s, %d archives (%d with the pinned bytes), recipe %s' % (
            name, case['account'], len(case['archives']), sum(entry['pinned_bytes'] for entry in case['archives'].values()),
            'as committed' if case.get('recipe', {}).get('matches_committed_pin') else 'placeholder prompt' if 'recipe' in case else 'none'))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('record', 'check', 'compare'):
        sub = commands.add_parser(name)
        sub.add_argument('--corpus', required=True, help='a corpus directory, or `synthetic` for the public cases of test/corpus/synthetic.py')
        sub.add_argument('--case', action='append')
        sub.add_argument('--scratch', help='where the sandboxes are laid out (default: .cache/corpus here, or <corpus>/scratch for a corpus outside this repository)')
        if name == 'compare':
            sub.add_argument('--node', default='node')
            sub.add_argument('--steps-only', action='store_true', help='the ported steps only; do not run the TS engine on the case')
    sub = commands.add_parser('import-layer')
    sub.add_argument('--private', required=True, help='checkout of the private layer')
    sub.add_argument('--public', required=True, help='checkout of the public role the layer pins (defaults, connectors, role defaults)')
    sub.add_argument('--out', required=True, help='corpus directory, outside this repository')
    sub.add_argument('--case', action='append', required=True, metavar='NAME=PLAYBOOK:INVENTORY:HOST')
    sub.add_argument('--archives', action='append', help='directory searched for archives with the pinned digests')
    sub.add_argument('--template', default='recipe.template.json', help='name of the recipe template in an inventory directory')
    sub.add_argument('--controller-checkout', default='/root/deploy-layer', help='where the controller is taken to keep the layer')
    args = parser.parse_args(argv)
    os.umask(0o022)  # the modes of what the adapter and the stand-in write are recorded; they must not depend on the caller's umask
    try:
        if args.command == 'import-layer':
            return command_import(args)
        if args.command == 'compare':
            return command_compare(args)
        return command_record(args, check=args.command == 'check')
    except CorpusError as error:
        print('corpus: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
