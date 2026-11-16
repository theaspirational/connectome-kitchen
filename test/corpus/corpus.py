#!/usr/bin/env python3
"""Deploy corpus: whole deployments through the Python oracle, kept as goldens.

A case is the input of one deployment: the four layers, the pinned versions,
the paths, the cook command, the recipe and the archives. `record` runs each
case through the oracle's deploy path and writes what came out; `check` runs
it again and compares; `compare` puts the steps kitchen has already ported
(config, inputs, archive, lock) beside the oracle on the same files, and then
runs the TS engine (deploy, cook) on the whole case at the same root and holds
its record against the oracle's, once with the oracle's name for the build and once as a kitchen names it (ruling D5),
and shows a role's build taken over by a kitchen. That record includes the deployment installed without a start and then started, and
the way back, through the contract with a stand-in for the unit verifier (stand_in_verifier.py).

    corpus.py record  --corpus DIR [--case NAME]
    corpus.py check   --corpus DIR [--case NAME]
    corpus.py compare --corpus DIR [--case NAME] [--node NODE]
    corpus.py import-layer --private DIR --public DIR --out DIR --case NAME=PLAYBOOK:INVENTORY:HOST ...
    corpus.py import-build --corpus DIR --base CASE --name NAME --build DIR [--cooked FILE]

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
import replay_cook
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


def request_of(data):
    """The request of a case as its layer has it: nothing moved, the committed recipe pin. Returns the merged configuration,
    the spec the engine writes as deployment.json (deploy.py:478-480) and the name the role's cook command gives the build."""
    import connectome_config, connectome_deploy
    layers = copy.deepcopy(data['layers'])
    if 'recipe_file' in layers['agent']:
        layers['agent']['recipe_file']['sha256'] = data['recipe']['committed_sha256']
    effective = connectome_config.merge_layers([connectome_config.public_catalog(layers['defaults'], layers['catalog']), layers['private'], layers['group'], layers['agent']])
    spec = {'config': {key: value for key, value in effective.items() if key not in ('activation', 'exclusive_with')},
            'pins': connectome_deploy.resolve_pins(effective, data['versions'])}
    return effective, spec, sha256(connectome_deploy.canonical({'spec': spec, 'cook': data['cook_command']}).encode())


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


def snapshot(root, port=False):
    """Every entry under `root`. With `port`, the log of cook's calls is read as `cook_calls` gives it: the TS adapter's two
    names of the build's own Bun cache are held to be there and left out, so the row is comparable with the oracle's."""
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
                if port and name == 'cook-calls.jsonl':  # in the words of the stand-ins that write it (stand_in_cook.py, replay_cook.py)
                    data = ''.join(json.dumps(call, sort_keys=True) + '\n' for call in cook_calls(path, True)).encode()
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


# The names the TS adapter adds to cook's environment, and the oracle's adapter does not (src/isolation.ts): the Bun cache
# of the build's own, in every call, so that a new build shares no file with a build that is there; and Git's counted
# configuration names, where the recipe names a source repository by a local path. A known difference of the port, and
# under the number of the adapter's contract since `/2` (ruling D6): adapter_contract.py records these names as they are.
OWN_CACHE = ('BUN_INSTALL_CACHE_DIR', 'COOK_PASS_ENV')
GIT_NAMES = re.compile(r'GIT_CONFIG_(?:COUNT|KEY_[0-9]+|VALUE_[0-9]+)')


def cook_calls(path, port, whole=False):
    """The cook calls a log holds. Of the TS adapter (`port`): each must name the build's own Bun cache in cook's
    environment, and is then given as the oracle's adapter would have made it, without the names the port adds. So the
    difference is held, in every call, and everything else of a call stays comparable with the oracle's as text.
    `whole`: the port's calls with every name, as the record of the adapter's contract keeps them."""
    calls = lines(path)
    if not port:
        return calls
    for call in calls:
        names = call.get('environment_names', [])
        if not set(OWN_CACHE) <= set(names):
            raise CorpusError('the TS adapter called cook without a Bun cache of the build\'s own: %s lacks %s' % (path, ', '.join(sorted(set(OWN_CACHE) - set(names)))))
        if not whole:
            call['environment_names'] = [name for name in names if name not in OWN_CACHE and not GIT_NAMES.fullmatch(name)]
    return calls


def fixture_environment():
    """The environment of every engine and adapter the corpus starts, whoever calls it. The adapter hands cook the caller's
    PATH, HOME, LANG, LC_ALL and TMPDIR where they are set (cook.py:60-63), and a record names them. So they are set here
    and not taken from the caller: PATH and HOME as they are (a default where one is missing), LANG fixed, LC_ALL and
    TMPDIR not set. A golden then says HOME, LANG and PATH on every machine."""
    os.environ.pop('LC_ALL', None)
    os.environ.pop('TMPDIR', None)
    os.environ['LANG'] = 'C.UTF-8'
    os.environ.setdefault('PATH', os.defpath)
    os.environ.setdefault('HOME', pwd.getpwuid(os.geteuid()).pw_dir)


def found_node(node):
    """A Node named by a bare word is the one on the search path, as a shell would find it; a path stays a path."""
    if os.sep in node:
        return node
    found = shutil.which(node)
    if found is None:
        raise CorpusError('no %s on the search path: --node <path of a Node>' % node)
    return found


class World:
    """One case laid out under one sandbox root, and the oracle run on it."""

    def __init__(self, case, sandbox, node=None, own_name=False):
        self.case, self.sandbox = case, Path(sandbox)
        self.node = node  # None: the oracle, in this process. Else the Node that runs the TS engine (ts_driver.mjs).
        # How the TS engine names the build. False: it is handed the two words the oracle's shim hashes, and the build has the
        # oracle's name. True: it is handed nothing and names the build as a kitchen does, by its adapter contract (ruling D5).
        self.own_name = own_name
        self.install = HERE  # where ts_driver.mjs and adapter_seam.mjs are, with the `dist` they import two levels up
        self.tell = False  # whether an outcome says which adapter command the engine asked for
        self.contract = None
        if not SYSTEMD_PATH.fullmatch(str(self.sandbox)) or self.sandbox.resolve() != self.sandbox:
            raise CorpusError('the scratch path must be a real path of letters, digits and _./:@+- only: the engine refuses any other')
        self.account = case.data['account']
        self.home = self.sandbox / 'home' / self.account
        self.mutation = None

    def moved(self, value):
        return move(value, str(self.sandbox))

    def build(self, mutation=None, unit_without_install=False):
        """Lays the files out and returns nothing; `self.request` is what the engine is then asked.

        With `unit_without_install`, cook is the stand-in in every case, and the unit it writes has no `[Install]` section."""
        import connectome_config, connectome_deploy
        data, self.mutation = self.case.data, mutation
        self.verifier = None
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
        # A case with the files of a real build: the runtime replays that build's lock instead (replay_cook.py).
        self.replay = None
        if 'cook_build' in data and not unit_without_install:
            self.replay = {'directory': str((self.case.directory / data['cook_build']['directory']).resolve()), 'request_id': data['cook_build']['request_id'],
                           'sandbox': str(self.sandbox), 'absolute': ABSOLUTE.pattern,
                           'cooked': str((self.case.directory / data['cook_build']['cooked']).resolve()) if data['cook_build'].get('cooked') else None}
            stand_in = '#!%s -I\nimport runpy, sys\nsys.dont_write_bytecode = True\nrunpy.run_path(%r, init_globals={"REPLAY": %r}, run_name="__main__")\n' % (
                sys.executable, str(HERE / 'replay_cook.py'), self.replay)
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
        if unit_without_install:
            (Path(self.root).parent / 'unit-without-install-section').write_text('')  # read by stand_in_cook.py
        self.systemctl = [sys.executable, str(ORACLE / 'tests/fixtures/systemctl.py'), self.root]
        self.request = {'base': self.base, 'root': self.root, 'unit_dir': self.unit_dir, 'layers': copy.deepcopy(self.layers),
                        'versions': self.versions, 'engines': {'cook': tools}, 'systemctl': self.systemctl}

    def port(self, job):
        """One call of the TS engine, in the same sandbox and behind the same two seams as the oracle's (ts_driver.mjs)."""
        job.update(sandbox=str(self.sandbox), account=self.account, root_owned=list(ROOT_OWNED), seam=str(self.install / 'adapter_seam.mjs'))
        if not self.own_name:
            job['identity'] = [sys.executable, str(ORACLE / 'scripts/cook-adapter.py')]  # the words the oracle's shim hashes: the oracle's build name
        if self.tell:
            job['tell_adapter'] = True
        job_file, out_file = (self.sandbox.parent / ('%s.%s-%d.json' % (self.sandbox.name, kind, os.getpid())) for kind in ('job', 'outcome'))
        try:
            job_file.touch(mode=0o600)
            job_file.write_text(json.dumps(job))
            done = subprocess.run([self.node, str(self.install / 'ts_driver.mjs'), str(job_file), str(out_file)], cwd=ROOT, capture_output=True, text=True, timeout=600)
            if done.returncode:
                raise CorpusError('the TS engine stopped on case %s: %s' % (self.case.name, done.stderr[-2000:]))
            if done.stderr:
                sys.stderr.write(done.stderr)
            return json.loads(out_file.read_text())
        finally:
            job_file.unlink(missing_ok=True)
            out_file.unlink(missing_ok=True)

    def stand_in_verifier(self, verdict=None):
        """Puts a `systemd-analyze` where both engines find it first: stand_in_verifier.py, which writes its calls beside the
        other logs of the root. With `verdict` it gives that exit status whatever the units are. Until the next `build`."""
        self.verifier = self.sandbox / 'verifier'
        calls, verdict_file = Path(self.root) / VERIFIER_LOG, self.verifier / 'verdict'
        write(self.verifier / 'systemd-analyze', '#!%s -I\nimport runpy, sys\nsys.dont_write_bytecode = True\nrunpy.run_path(%r, init_globals={"CALLS": %r, "VERDICT": %r}, run_name="__main__")\n' % (
            sys.executable, str(HERE / 'stand_in_verifier.py'), str(calls), str(verdict_file)), 0o755)
        if verdict is not None:
            write(verdict_file, '%d\n' % verdict)

    @contextlib.contextmanager
    def search_path(self):
        """The search path of an engine call: with a stand-in verifier, its directory first. Both engines look the verifier up
        by name on the path of their own process (`shutil.which`; `which` of src/deploy.ts), and the TS driver inherits this one."""
        if self.verifier is None:
            yield
            return
        with patch.dict(os.environ, {'PATH': str(self.verifier) + os.pathsep + os.environ.get('PATH', os.defpath)}):
            yield

    def dispatch(self, command, activation=None, **extra):
        """Contract v1 `plan` or `apply`. With `activation`, the agent's layer asks for that activation; the rest is the same request."""
        import connectome_deploy, kitchen_v1
        request = dict(copy.deepcopy(self.request), **extra)
        if activation is not None:
            request['layers'][-1]['activation'] = activation
        with self.search_path():
            if self.node:
                return self.port({'op': 'dispatch', 'command': command, 'request': request, 'secrets': self.secrets if command == 'apply' else None})
            with host_model(self.sandbox, self.account), patch.object(connectome_deploy, 'run', adapter_through_seam(connectome_deploy.run)):
                envelope, status = kitchen_v1.dispatch({'contract': kitchen_v1.CONTRACT, 'command': command, 'request': request},
                                                       self.secrets if command == 'apply' else None)
        return {'status': status, 'core': envelope['core']}

    def units_stopped(self):
        """The units the fake service manager has as active, stopped behind the engine's back, as an operator stops an agent."""
        states = Path(self.root).parent / 'unit-states.json'
        known = json.loads(states.read_text()) if states.is_file() else {}  # no file: nothing was ever started, which the record then shows
        states.write_text(json.dumps({key: 'inactive' for key in known}))
        return sorted(key for key, value in known.items() if value == 'active')

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

    def where(self):
        """The TS engine about itself: the name of its adapter contract, and the two words it starts its adapter with."""
        return self.port({'op': 'where'})

    def adapter_words(self):
        """The words that stand for the adapter in the name of the build. The oracle's shim hashes its interpreter and the
        path of its script (deploy.py:482, kitchen_v1.py). A kitchen hashes one word, the name of its adapter contract."""
        if not self.own_name:
            return self.shim_command()[:2]
        if self.contract is None:
            self.contract = self.where()['contract']
        return [self.contract]

    def naming(self):
        """What stands for cook in the name of the build: the adapter words, then the words of the command, which are the same in both."""
        return self.adapter_words() + self.shim_command()[2:]

    def role_apply(self, files):
        """The role's own apply, as its module makes it around the Python engine (oracle/library/connectome_release.py, without
        its lock): the role's cook command, so the build has the name a deployed agent's build has today. The adapter is
        started through the seam with this interpreter, since the role's own is a path of the deployed host."""
        import connectome_deploy
        original = connectome_deploy.run

        def run(argv, cwd=None, adapter=False, unit_verify=False, env=None):
            if adapter:
                argv = [sys.executable, str(HERE / 'adapter_seam.py')] + list(argv[2:])
            return original(argv, cwd=cwd, adapter=adapter, unit_verify=unit_verify, env=env)
        with self.search_path(), host_model(self.sandbox, self.account), patch.object(connectome_deploy, 'run', run):
            deployment = connectome_deploy.Deployment(self.root, self.unit_dir, copy.deepcopy(self.effective), self.versions, self.role_command, self.systemctl,
                                                      base=self.base, operator_files=files)
            deployment.validate_paths()
            deployment.preflight_activation()
            connectome_deploy.make_directory(deployment.root)
            return {'request_id': deployment.request_id, 'prepare': deployment.prepare(), 'activate': deployment.activate(copy.deepcopy(self.secrets))}

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
                'cook_command': self.shim_command(), 'cook_identity': self.naming(), 'systemctl': self.systemctl}})
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

    def identity(self):
        """The name and the request of the replayed build, from the case as the layer has it: nothing moved, the committed recipe pin,
        the role's cook command. No file is read and none is written.

        This is not the engine's answer. On both sides the harness puts the request together itself, with the engine's
        parts (`resolve_pins`, `canonical`) in the engine's order (deploy.py:478-482): `request_of` here, op `identity` in
        ts_driver.mjs. Neither constructs a `Deployment`. So `is_the_replayed_build` shows that the case is the input of the
        kept build, and that the two `canonical` and `resolve_pins` agree on it; that an engine itself gives the build this
        name is shown elsewhere: `role` holds `Deployment.request_id` of each engine on the moved case, and `import-build`
        refuses a case whose request the oracle names otherwise."""
        import connectome_deploy
        data = self.case.data
        effective, spec, request = request_of(data)
        if self.node:
            return self.port({'op': 'identity', 'effective': effective, 'versions': data['versions'], 'cook_command': data['cook_command']})
        return {'request_id': request, 'deployment_json_sha256': sha256(connectome_deploy.canonical(spec).encode())}

    def replayed_build(self):
        """The build `apply` left, held against the build it replays, file by file.

        A file of the kept build is carried to this root as replay_cook.py carries the lock (paths, the request id, the digests
        of the three files this changes) and must then be the bytes the engine and its adapter left. The launcher and the unit
        are equal by construction; the request, the recipe, the layout, the marker and above all the lock after the adapter's
        post-processing are not: those the engine wrote.
        """
        directory, build = Path(self.replay['directory']), Path(self.root) / 'builds' / self.request_id()
        carry, _lock, _files = replay_cook.carry_for(self.replay, build.name)
        files = {}
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            here = build / path.name
            carried = carry.text(path.read_text())
            if not here.is_file():
                files[path.name] = 'not in the build the engine left'
            elif here.read_text() == carried:
                files[path.name] = 'the bytes of the replayed build, carried to this root'
            else:
                try:
                    where = differences(json.loads(carried), json.loads(here.read_text()))
                except ValueError:
                    where = []
                files[path.name] = {'differs': where or 'in its text'}
        identity = self.identity()
        real = {'request_id': self.replay['request_id'], 'deployment_json_sha256': sha256((directory / 'deployment.json').read_bytes())}
        return {'identity': dict(identity, is_the_replayed_build=identity == real), 'files': files,
                'only_in_the_build_the_engine_left': sorted(path.name for path in build.iterdir() if not (directory / path.name).is_file())}

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
        """The name the contract path gives the build. For the oracle it is the engine's own, asked in check mode. For a TS
        engine that names the build itself it is the oracle's formula over the oracle's request, with the one word of the
        adapter contract where the shim's two words are: the name a kitchen must give the build if the two engines differ in
        those words and in nothing else. The harness looks for the build under this name, so an engine that hashed anything
        else leaves a tree that is not the oracle's."""
        import connectome_deploy, kitchen_v1
        with host_model(self.sandbox, self.account):
            return connectome_deploy.Deployment(self.root, self.unit_dir, copy.deepcopy(self.effective), self.versions, self.naming(), check=True, base=self.base).request_id


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


VERIFIER_LOG = 'unit-verifier-calls.jsonl'
VOLATILE = ('systemctl-calls', 'unit-states.json')
LOGS = ('cook-calls.jsonl', 'verify-cook-calls.jsonl', VERIFIER_LOG)
# The deployment through the contract with a unit verifier found: installed and not started, then started, and the way back.
# (step, command, activation). Before `stopped`, the units are stopped behind the engine's back.
# It is run twice. With the unit cook writes, a start adds the links of its `[Install]` section and an install without a start
# takes them away, so the installed files alone already say that something changes. With a unit that has no such section the
# files are the same under both activations, and the engine's own marker (`state/activation`) is all that tells them apart.
SWITCH = (
    ('installed', 'apply', 'install_only'),
    ('installed_again', 'apply', 'install_only'),
    ('start_planned', 'plan', 'start'),
    ('started', 'apply', 'start'),
    ('started_again', 'apply', 'start'),
    ('install_only_planned_while_active', 'plan', 'install_only'),
    ('install_only_refused_while_active', 'apply', 'install_only'),
    ('stopped', None, None),
    ('install_only_planned', 'plan', 'install_only'),
    ('installed_after_stop', 'apply', 'install_only'),
    ('installed_after_stop_again', 'apply', 'install_only'),
)


class Steps:
    """Engine calls on one world, one after another, each with what it changed and whom it called."""

    def __init__(self, world, files):
        self.world, self.files = world, files
        self.state = snapshot(world.sandbox)
        self.kept = (world.unit_dir, str(Path(world.root) / 'state'))  # the text of what the engine itself writes
        self.seen = self.counts()

    def logs(self):
        root = Path(self.world.root)
        return {'systemctl_calls_before_the_root_existed': root.parent / 'preflight-systemctl-calls', 'systemctl_calls': root / 'systemctl-calls',
                'cook_calls': root / 'cook-calls.jsonl', 'verify_cook_calls': root / 'verify-cook-calls.jsonl', 'unit_verifier_calls': root / VERIFIER_LOG}

    def counts(self):
        return {name: len(lines(path)) for name, path in self.logs().items()}

    def call(self, command, activation, **extra):
        outcome = self.world.dispatch(command, activation=activation, **extra, **({'operator_files': self.files} if command == 'apply' else {}))
        now = snapshot(self.world.sandbox)
        outcome['command'], outcome['activation'] = command, activation
        outcome['tree'] = {path: (row if path.startswith(self.kept) else {key: value for key, value in row.items() if key != 'text'})
                           for path, row in now.items() if self.state.get(path) != row and Path(path).name not in VOLATILE + LOGS + ('preflight-systemctl-calls',)}
        outcome['removed'] = sorted(path for path in self.state if path not in now)
        for name, path in self.logs().items():
            calls = lines(path)[self.seen[name]:]
            outcome[name] = len(calls) if name in ('cook_calls', 'verify_cook_calls') else calls
        self.state, self.seen = now, self.counts()
        return outcome


def activation_switch(world, unit_without_install=False):
    """SWITCH on a sandbox of its own. Returns the steps in order."""
    world.build(unit_without_install=unit_without_install)
    world.stand_in_verifier()
    steps, done = Steps(world, world.operator_files()), []
    for name, command, activation in SWITCH:
        if command is None:
            done.append({'step': name, 'units': world.units_stopped()})
            steps.state = snapshot(world.sandbox)
        else:
            done.append(dict(steps.call(command, activation), step=name))
    return done


def verifier_refuses(world):
    """The first install without a start, with a unit verifier that refuses the unit: what the engine answers and what it leaves."""
    world.build()
    world.stand_in_verifier(verdict=7)
    return Steps(world, world.operator_files()).call('apply', 'install_only')


MUTATIONS = (('recipe-pin', 'plan'), ('archive-pin', 'plan'), ('secrets-file-missing', 'plan'), ('external-command-missing', 'apply'))


def run_case(case, sandbox, node=None, own_name=False):
    """One case at one sandbox root. Returns the record and the world, left as `apply` made it.

    With `node`, every engine call (the role's view, plan, apply, the second apply, the refusals) goes to the TS engine
    instead. The files are laid out by the same code, and the record is put together the same way. With `own_name` the TS
    engine names the build as a kitchen does, and `{REQUEST}` in the record stands for that name.
    """
    fixture_environment()
    ts_port.initialize()
    for name in ('connectome_deploy', 'connectome_cook', 'kitchen_v1'):
        importlib.import_module(name)
    import connectome_config
    world = World(case, sandbox, node, own_name)
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
    before = snapshot(world.sandbox, node is not None)
    installed = world.install_only(world.operator_files())
    after = snapshot(world.sandbox, node is not None)
    kept = (world.unit_dir, str(Path(world.root) / 'state'))  # the text of what the engine itself writes; the build is the one `apply` shows
    installed['tree'] = {path: ({key: value for key, value in row.items() if key != 'text'} if not path.startswith(kept) else row)
                         for path, row in after.items() if before.get(path) != row}
    installed['systemctl_calls'] = lines(Path(world.root) / 'systemctl-calls')
    installed['systemctl_calls_before_the_root_existed'] = lines(Path(world.root).parent / 'preflight-systemctl-calls')
    installed['cook_calls'] = len(lines(Path(world.root) / 'cook-calls.jsonl'))
    switched = activation_switch(world)
    switched_without_install = activation_switch(world, unit_without_install=True)
    refused_by_verifier = verifier_refuses(world)
    world.build()
    before = snapshot(world.sandbox, node is not None)
    files = world.operator_files()
    record = {
        'case': case.name,
        'engine': {'oracle': 'a49b5dc', 'combine': 'ansible-core' if connectome_config.combine is not connectome_config._python_combine else 'stand-in of the oracle (no ansible-core here)',
                   'cook': 'stand-in (test/corpus/stand_in_cook.py)' if not world.replay else 'not run: the lock and outputs of a build kept with the case, replayed (test/corpus/replay_cook.py)' + (
                       '; the lock is the bytes cook wrote, in cook\'s key order' if world.replay.get('cooked') else ''), 'systemctl': 'fake of the oracle (oracle/tests/fixtures/systemctl.py)',
                   'verify_cook': 'recorded, not performed (test/corpus/adapter_seam.py)',
                   'unit_verifier': 'stand-in found first on the search path, in `activation_switch` and `unit_verifier_refuses` only (test/corpus/stand_in_verifier.py)', 'ownership': 'modelled (host_model in test/corpus/corpus.py)'},
        'substitutions': case.data.get('substitutions', []),
        'config': {'layers': world.layers, 'effective': world.effective, 'secret_names': world.names},
        'inputs': {'operator_files': {name: {'sha256': sha256(base64.b64decode(value))} for name, value in files.items()}},
        'role': world.role_view(),
        # What the engine behind the contract hashes for cook in the name of the build (deploy.py:482). The first two words are
        # the oracle's own and a kitchen does not have them (ruling D5); `compare` holds this section apart, on purpose.
        'build_name': {'adapter_words': world.adapter_words(), 'command_words': world.shim_command()[2:], 'request_id': world.request_id()},
    }
    record['plan'] = world.dispatch('plan')
    untouched = snapshot(world.sandbox, node is not None)
    record['plan']['wrote'] = sorted(path for path in untouched if path not in before or untouched[path] != before[path])
    record['apply'] = world.dispatch('apply', operator_files=files)
    after = snapshot(world.sandbox, node is not None)
    record['apply']['tree'] = {path: row for path, row in after.items() if before.get(path) != row}
    record['apply']['removed'] = sorted(path for path in before if path not in after)
    record['apply']['systemctl_calls'] = lines(Path(world.root) / 'systemctl-calls')
    record['apply']['systemctl_calls_before_the_root_existed'] = lines(Path(world.root).parent / 'preflight-systemctl-calls')
    record['apply']['cook_calls'] = cook_calls(Path(world.root) / 'cook-calls.jsonl', node is not None)
    record['apply']['verify_cook_calls'] = lines(Path(world.root) / 'verify-cook-calls.jsonl')
    if world.replay:
        record['replayed_build'] = world.replayed_build()
    record['reapply'] = world.dispatch('apply', operator_files=files)
    again = snapshot(world.sandbox, node is not None)
    volatile = VOLATILE
    record['reapply']['tree_changes'] = sorted(path for path in set(after) | set(again) if after.get(path) != again.get(path) and Path(path).name not in volatile)
    record['plan_after_apply'] = world.dispatch('plan')
    # A later release of the same deployment: another cook pin, so another build beside the one that is current. Then the
    # first request again, which is how a release is taken back: its build is still there and only the pointer moves.
    later = copy.deepcopy(world.versions)
    pin = later['runtime']['cook']
    later['runtime']['cook'] = pin[:-1] + ('0' if pin[-1] != '0' else '1')
    logs = LOGS[:2]
    state = again
    for name, extra in (('rebuild', {'versions': later}), ('release_back', {})):
        calls = {log: len(lines(Path(world.root) / log)) for log in ('systemctl-calls',) + logs}
        record[name] = world.dispatch('apply', operator_files=files, **extra)
        now = snapshot(world.sandbox, node is not None)
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
        record[name]['cook_calls'] = cook_calls(Path(world.root) / 'cook-calls.jsonl', node is not None)[calls['cook-calls.jsonl']:]
        record[name]['verify_cook_calls'] = lines(Path(world.root) / 'verify-cook-calls.jsonl')[calls['verify-cook-calls.jsonl']:]
        state = now
    jobs = world.ported()
    record['ported'] = brief(oracle_outcomes(world, jobs))
    record['refusals'] = refusals
    record['install_only'] = installed
    record['activation_switch'] = switched
    record['activation_switch_unit_without_install_section'] = switched_without_install
    record['unit_verifier_refuses'] = refused_by_verifier
    return located_nowhere(record, world), world, jobs


def located_nowhere(record, world):
    """A record as text, with what follows from where it was made given a name: the sandbox, this checkout, the name of the build, this interpreter."""
    # Tools and fixtures of this checkout appear in argv; they are not part of what the engine decided.
    text = json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False)
    for value, token in ((str(world.sandbox), '{ROOT}'), (str(ROOT), '{KITCHEN}'), (world.request_id(), '{REQUEST}')):
        text = text.replace(value, token)
    text = text.replace(json.dumps(sys.executable), json.dumps('{PYTHON}'))  # as a whole argument only, never inside a moved path
    return re.sub(r'\{ROOT\}/(%s)(?=[/"\\])' % '|'.join(MOVED), r'{ROOT:\1}', text)


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
    import connectome_deploy
    synthetic.build(generated, yaml.safe_load((ORACLE / 'defaults/agent.yml').read_text()), catalog, yaml.safe_load((ORACLE / 'versions.yml').read_text()),
                    replay={'archive': package_archive, 'request_of': request_of, 'canonical': connectome_deploy.canonical})
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
            print('%s: plan %d, apply %d, reapply %d, rebuild %d, release back %d, install only %s, activation switch %s, unit verifier refusal %d, %d ported calls, %d refusals' % (
                case.name, record['plan']['status'], record['apply']['status'], record['reapply']['status'], record['rebuild']['status'],
                record['release_back']['status'], 'refused' if 'refused' in record['install_only'] else '%d applies' % len(record['install_only']['applies']),
                ' '.join(''.join(str(step.get('status', '-')) for step in record[name]) for name in ('activation_switch', 'activation_switch_unit_without_install_section')),
                record['unit_verifier_refuses']['status'],
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


SHIM_WORDS = ['{PYTHON}', '{KITCHEN}/oracle/scripts/cook-adapter.py']


def own_name_differences(case, world, raw, node):
    """The TS engine as a kitchen runs it, at the same root: nobody tells it how to name the build (ruling D5).

    The oracle keeps its name and the engine has its own, and each record says `{REQUEST}` for its own. The section
    `build_name` is held apart: the oracle hashes two words for its adapter, the engine one, the name of its adapter contract,
    and the words after them are the same. Everything else must be the oracle's record but for digests, and a digest may
    differ only as one that follows from the name: the same oracle digest against the same engine digest wherever it stands
    (`normalize`, the rule that holds two roots together). Their bytes are held by the other run, where the engine has the
    oracle's name and every digest is the oracle's. Returns where the records part, the contract name and how many digests follow the name.
    """
    text, port_world, _jobs = run_case(case, world.sandbox, node=node, own_name=True)
    oracle, port = json.loads(raw), json.loads(text)
    theirs, ours = oracle.pop('build_name'), port.pop('build_name')
    words, found = port_world.adapter_words(), []
    if theirs['adapter_words'] != SHIM_WORDS or ours['adapter_words'] != words or len(words) != 1 or not re.fullmatch(r'[a-z][a-z-]*/[1-9][0-9]*', str(words[0])):
        found.append('/build_name/adapter_words')
    if theirs['command_words'] != ours['command_words']:
        found.append('/build_name/command_words')
    if theirs['request_id'] != '{REQUEST}' or ours['request_id'] != '{REQUEST}' or port_world.request_id() == world.request_id():
        found.append('/build_name/request_id')
    dump = lambda value: json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False)
    names = {}
    try:
        _text, names = normalize(dump(oracle), dump(port))
    except CorpusError:
        found += differences(json.loads(HEX64.sub('#', dump(oracle))), json.loads(HEX64.sub('#', dump(port)))) or ['(a digest follows the name of the build in one place and not in another)']
    return found, words[0], len(names)


def engine_elsewhere(scratch, node):
    """The same Node at another path and the same kitchen at another install location. Returns the Node and the directory of
    the driver there. The Node is a second name of the same file where the file system allows one, else a copy; `dist`, the
    package file that makes it a module tree and the two test drivers are copies."""
    there = scratch / 'elsewhere'
    ts_port.remove(there)
    (there / 'node/bin').mkdir(parents=True)
    moved = there / 'node/bin/node'
    try:
        os.link(Path(node).resolve(), moved)
    except OSError:
        shutil.copy2(Path(node).resolve(), moved)
    shutil.copytree(ROOT / 'dist', there / 'kitchen/dist')
    shutil.copy2(ROOT / 'package.json', there / 'kitchen/package.json')
    (there / 'kitchen/test/corpus').mkdir(parents=True)
    for name in ('ts_driver.mjs', 'adapter_seam.mjs'):
        shutil.copy2(HERE / name, there / 'kitchen/test/corpus' / name)
    return str(moved), there / 'kitchen/test/corpus'


def taking_over(case, sandbox, node, elsewhere):
    """What it means for an agent the role deployed that a kitchen takes it over, and what a move of Node or of kitchen means after that.

    On a sandbox of its own, with a stand-in unit verifier: the role's own apply through the Python engine, with the role's
    cook command, which is the build a deployed agent runs today. Then the TS engine as a kitchen runs it: `plan`, `apply`
    and `apply` again. Then the same engine from another Node path and another install location: `plan` and `apply`, a later
    release (another cook pin), and that release once more from where the engine was first. Returns what does not hold, and
    one line that says what was seen.
    """
    world = World(case, sandbox)
    world.build()
    world.stand_in_verifier()
    files = world.operator_files()
    steps = Steps(world, files)
    role = world.role_apply(files)
    steps.state, steps.seen = snapshot(world.sandbox), steps.counts()
    world.node, world.own_name, world.tell = node, True, True
    here, name = world.where(), world.request_id()
    build = str(Path(world.root) / 'builds' / name)
    unit = 'connectome-%s.service' % world.effective['name']
    restarts = lambda step: sum(call[:2] == ['restart', unit] for call in step['systemctl_calls'])
    # A deployment that is installed and not started (`activation: install_only`) is built and never restarted: the new
    # build costs it one cook run and no restart, and nothing of it may be started, restarted or enabled at all.
    started = world.effective.get('activation', 'start') != 'install_only'
    due = 1 if started else 0
    untouched = lambda step: started or not [call for call in step['systemctl_calls'] if set(call) & {'start', 'restart', 'enable', 'reenable'}]
    quiet = lambda step: (step['status'], step['core'].get('changed'), step['cook_calls'], restarts(step), step.get('adapter_started', []), step['removed']) == (0, False, 0, 0, [], []) \
        and step['core']['prepare']['build_dir'] == build and not [path for path in step['tree'] if '/builds/' in path or path == str(Path(world.root) / 'current')]
    built = lambda step, where: (step['status'], step['core'].get('changed'), step['core']['prepare']['changed'], step['cook_calls'], restarts(step), step['adapter_started']) == (0, True, True, 1, due, [where['adapter']]) and untouched(step)
    planned, taken = steps.call('plan', None), steps.call('apply', None)
    current = os.readlink(Path(world.root) / 'current')
    again = steps.call('apply', None)
    world.node, world.install = elsewhere
    there = world.where()
    moved_plan, moved_apply = steps.call('plan', None), steps.call('apply', None)
    later = copy.deepcopy(world.versions)
    pin = later['runtime']['cook']
    later['runtime']['cook'] = pin[:-1] + ('0' if pin[-1] != '0' else '1')
    released = steps.call('apply', None, versions=later)
    world.node, world.install = node, HERE
    released_again = steps.call('apply', None, versions=later)
    holds = {
        'the role\'s apply builds and starts the agent under the role\'s name for the build': role['prepare']['changed'] is True and role['activate']['changed'] is True and (Path(world.root) / 'builds' / role['request_id'] / '.prepared').is_file(),
        'a kitchen gives the build another name than the role': name != role['request_id'],
        'plan says so: a build to make, under the kitchen\'s name, and nothing written': (planned['status'], planned['core']['prepare']['changed'], planned['core']['prepare']['build_dir'], planned['tree'], planned['cook_calls']) == (0, True, build, {}, 0),
        'the first apply is one cook run with the adapter of this Node and this install, and one restart (none where the deployment is installed and not started)': built(taken, here) and taken['core']['prepare']['build_dir'] == build,
        'it leaves the kitchen\'s build current and the role\'s build in place': current == build and (Path(world.root) / 'builds' / role['request_id'] / '.prepared').is_file(),
        'the second apply changes nothing: no cook run, no restart': quiet(again),
        'the other Node and the other install are other paths, with the same contract name': there['contract'] == here['contract'] and all(a != b for a, b in zip(here['adapter'], there['adapter'])) and there['adapter'] == [elsewhere[0], str(elsewhere[1].parents[1] / 'dist/cook-adapter.js')],
        'from there plan and apply change nothing: the same name, no cook run, no restart': quiet(moved_plan) and quiet(moved_apply),
        'a later release from there is one cook run, started with the adapter of that Node and that install': built(released, there) and released['core']['prepare']['build_dir'] != build,
        'and from here again that release changes nothing': (released_again['status'], released_again['core'].get('changed'), released_again['cook_calls'], restarts(released_again), released_again['core']['prepare']['build_dir']) == (0, False, 0, 0, released['core']['prepare']['build_dir']),
    }
    return [what for what, held in holds.items() if not held], 'one new build, cook run and %s, then none from another Node path and install location (%s)' % (
        'restart' if started else 'no restart (installed, not started)', here['contract'])


def command_compare(args):
    """The ported steps, live: oracle and TS port on the same files, outcome against outcome as canonical bytes.

    Then the engine: plan, apply, what apply left, the second apply, the releases, the installs without a start, the switch
    of activation and the refusals of the TS engine against the oracle's. Twice: once with the oracle's name for the build,
    where every byte and digest is held, and once as a kitchen names it (`own_name_differences`). Last, the role's build taken
    over by a kitchen and the engine moved (`taking_over`).
    """
    scratch = scratch_of(args.corpus, args.scratch)
    failed = False
    elsewhere = None if args.steps_only else engine_elsewhere(scratch, args.node)
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
            named, owed, said = [], [], 'not run'
            if not args.steps_only:
                named, contract, derived = own_name_differences(case, world, raw, args.node)
                for where in named:
                    print('  ENGINE %s under its own name for the build differs at %s' % (case.name, where))
                owed, seen = taking_over(case, scratch / 'taking-over' / case.name, args.node, elsewhere)
                for what in owed:
                    print('  TAKING OVER %s: does not hold: %s' % (case.name, what))
                said = '%s; build named by %s: %s; taking over: %s' % (
                    'equal: role view, plan, apply, tree, re-apply, rebuild, release back, install only, activation switch, unit verifier, refusals' if not parted else 'DIFFERS in %d places' % len(parted),
                    contract, 'the oracle\'s record but for the adapter words and %d digests that follow the name' % derived if not named else 'DIFFERS in %d places' % len(named),
                    seen if not owed else '%d things do NOT hold' % len(owed))
            print('%s %s: %d ported calls, %d equal, %d mismatch; golden %s; engine %s' % (
                'PASS' if counts.get('mismatch', 0) == 0 and recorded and not parted and not named and not owed else 'FAIL', case.name, len(jobs), counts.get('equal', 0), counts.get('mismatch', 0),
                'reproduced' if recorded else 'NOT reproduced', said))
            failed = failed or not recorded or bool(parted) or bool(named) or bool(owed)
        finally:
            ts_port.remove(world.sandbox)
            ts_port.remove(scratch / 'taking-over' / case.name)
    if elsewhere:
        ts_port.remove(scratch / 'elsewhere')
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


BUILD_FILES = ('connectome.lock', 'deployment.json', 'cook-recipe.json', 'cook-layout.json', '.cook-layout.json', '.prepared')


def command_import_build(args):
    """A case that replays a real build: the deploy inputs of an existing case, the real recipe, and a copy of the build's files.

    Refused unless the copy is one build and the existing case is its input: the engine's own request for that case, with the
    committed recipe pin and the role's cook command, must be the copy's deployment.json and must give the copy's request id.
    """
    import connectome_deploy
    corpus, source = Path(args.corpus).resolve(), Path(args.build).resolve()
    if corpus.is_relative_to(ROOT):
        raise CorpusError('a case made from a real build stays outside this repository')
    base = json.loads((corpus / 'cases' / args.base / 'case.json').read_text())
    sums = source / 'SHA256SUMS'
    if sums.is_file():
        for line in sums.read_text().splitlines():
            digest, name = line.split(None, 1)
            if sha256((source / name.lstrip('*')).read_bytes()) != digest:
                raise CorpusError('a file of the build copy is not the bytes its checksum list names')
    lock = json.loads((source / 'connectome.lock').read_text())
    request = (source / '.prepared').read_text()
    names = BUILD_FILES + (Path(lock['launch']['script']).name, Path(lock['launch']['userUnit']).name)
    recipe = (source / 'cook-recipe.json').read_bytes()
    committed = base['recipe']['committed_sha256']
    layers = copy.deepcopy(base['layers'])
    layers['agent']['recipe_file']['sha256'] = committed
    _effective, spec, named = request_of(base)
    outputs = {entry['path']: entry.get('sha256') for entry in lock['outputs']}
    checks = {
        'the lock names one build, the one of the marker': Path(lock['build']['dir']).name == request and HEX64.fullmatch(request) is not None,
        'the recipe is the one the lock names': sha256(recipe) == lock['recipe']['sha256'] and Path(lock['recipePath']).name == 'cook-recipe.json',
        'the recipe is the one the layer pins': sha256(recipe) == committed,
        'the launcher and the unit are the ones the lock names': all(outputs.get(name) == sha256((source / name).read_bytes()) for name in names[-2:]),
        'the layout file is the layout of the lock': json.loads((source / 'cook-layout.json').read_text()) == lock['layout'],
        'the request of the base case is the deployment.json of the build': connectome_deploy.canonical(spec) == (source / 'deployment.json').read_text(),
        'the request of the base case and its cook command give the name of the build': named == request,
    }
    cooked = Path(args.cooked).read_bytes() if args.cooked else None
    if cooked is not None:
        checks['the lock as cook wrote it is the lock of the build without the provenance the adapter adds'] = json.loads(cooked) == replay_cook.cook_lock(lock)
    for what, holds in checks.items():
        print('%s %s' % ('ok  ' if holds else 'FAIL', what))
    if not all(checks.values()):
        raise CorpusError('the build copy and case %s do not belong together' % args.base)
    directory = corpus / 'cases' / args.name
    ts_port.remove(directory)
    directory.mkdir(parents=True, mode=0o700)
    (directory / 'build').mkdir(mode=0o700)
    for name in names:
        if (source / name).is_file():
            shutil.copyfile(source / name, directory / 'build' / name)
            os.chmod(directory / 'build' / name, 0o600)
    case = copy.deepcopy(base)
    case['name'] = args.name
    case['layers'] = layers
    case['recipe'] = {'file': 'build/cook-recipe.json', 'committed_sha256': committed, 'matches_committed_pin': True}
    case['cook_build'] = {'directory': 'build', 'request_id': request, 'base_case': args.base}
    order = 'the lock is written with sorted keys: the order cook wrote is not in the copy'
    if cooked is not None:
        (directory / 'cooked.lock').write_bytes(cooked)
        os.chmod(directory / 'cooked.lock', 0o600)
        case['cook_build']['cooked'] = 'cooked.lock'
        order = 'the lock is written as cook wrote it, byte for byte once carried: the case keeps those bytes, taken before the adapter read them'
    case['substitutions'] = [text for text in base.get('substitutions', []) if 'placeholder' not in text] + [
        'The recipe is the one the real build was made from; it is the bytes the layer pins.',
        'Cook is not run. Its runtime is replaced by test/corpus/replay_cook.py, which writes the lock, the launcher and the unit of the real build %s, carried to the sandbox root and to the request id of the run. The provenance the adapter adds is taken out first, and %s.' % (request[:8], order),
        'The copy of the real build holds no application, engines, snapshots or per-request configuration. The snapshots are copied from the archives of the case; the rest is absent.']
    (directory / 'case.json').write_text(json.dumps(case, indent=1, sort_keys=True, ensure_ascii=False) + '\n')
    os.chmod(directory / 'case.json', 0o600)
    print('%s: replays build %s on the inputs of case %s' % (args.name, request[:8], args.base))
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
    sub = commands.add_parser('import-build')
    sub.add_argument('--corpus', required=True, help='corpus directory, outside this repository')
    sub.add_argument('--base', required=True, help='the case whose deploy inputs the build was made from')
    sub.add_argument('--name', required=True, help='name of the new case')
    sub.add_argument('--build', required=True, help='a copy of the files of one real build (lock, request, recipe, layout, launcher, unit, marker)')
    sub.add_argument('--cooked', help='the lock of that build as cook wrote it, before the adapter rewrote it (scripts/rehearse/cook-keep keeps it)')
    args = parser.parse_args(argv)
    os.umask(0o022)  # the modes of what the adapter and the stand-in write are recorded; they must not depend on the caller's umask
    fixture_environment()  # nor the names of cook's environment on the caller's
    try:
        if args.command == 'compare':
            args.node = found_node(args.node)
        if args.command == 'import-layer':
            return command_import(args)
        if args.command == 'import-build':
            return command_import_build(args)
        if args.command == 'compare':
            return command_compare(args)
        return command_record(args, check=args.command == 'check')
    except CorpusError as error:
        print('corpus: ' + str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
