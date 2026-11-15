"""The public cases of the deploy corpus. Every name, path and number here is made up.

They have the shape of the deployments the corpus was built for and none of
their content: a recipe handed over as a pinned file with host-installed
servers, two archives as host dependencies, secrets by name and by file, a
private data argument, a unit to exclude; and a second case with no recipe, a
framework archive from a registry release and a mock provider. Absolute paths
are put together from their parts, as the publication gate reads this file too.

A third case is the first one again with the files of a build beside it, for
replay_cook.py: a lock in the shape the pinned cook writes on a host (absolute
launcher and unit paths, engine records, a checkout path, snapshots named by
digest, an output outside the build, modes 0700 and 0600, a credential name
nobody declared), as the adapter leaves it. The values are made up like the rest.
"""
import hashlib
import json
from pathlib import Path

SCOPE = '@' + 'animalabs'


def at(*parts):
    return '/' + '/'.join(parts)


def recipe(home, connectors, runtime):
    state = home + '/.local/state/connectome/example'
    return {
        'name': 'Example', 'description': 'Synthetic agent of the deploy corpus.',
        'agent': {'name': 'Example', 'provider': 'mock', 'model': 'mock-example', 'systemPrompt': 'Synthetic prompt. Do nothing.', 'maxTokens': 1024},
        'modules': {'history': True, 'workspace': {'path': state + '/workspace'}},
        'requirements': {'EXAMPLE_API_KEY': {'exposeAs': 'EXAMPLE_API_KEY', 'required': False},
                         'EXAMPLE_BASE_URL': {'exposeAs': 'EXAMPLE_BASE_URL', 'required': False}},
        'mcpServers': {
            'chat': {'command': runtime + '/bin/node', 'args': [connectors + '/chat-0a1b2c3d/build/index.js', '--stdio'],
                     'env': {'CHAT_RC_PATH': '${CHAT_RC_PATH}', 'CHAT_STATE_DIR': state + '/chat', 'CHAT_REALM': 'https://chat.example.invalid'},
                     'source': {'external': True}, 'toolPrefix': 'chat'},
            'relay': {'command': at('usr', 'bin', 'python3'),
                      'args': [connectors + '/example/relay.py', '--node', runtime + '/bin/node', '--connector', connectors + '/relay-4e5f6a7b/dist/src/index.js',
                               '--state', state + '/relay-channel', '--config', connectors + '/example/relay-config.json'],
                      'env': {'RELAY_TOKENS_FILE': state + '/credentials/relay'}, 'source': {'external': True}, 'reconnect': False},
            'shell': {'command': runtime + '/bin/node', 'args': [connectors + '/terminal-9c8d7e6f/dist/src/mcp/mcp-stdio-server.js'],
                      'env': {'HOME': home, 'PATH': at('usr', 'bin') + ':/bin'}, 'source': {'external': True}},
            'inline': {'url': 'https://tools.example.invalid/mcp', 'transport': 'http'},
        },
    }


COOK_ORDER = ('version', 'backend', 'engines', 'layout', 'state', 'hostDependencies', 'external', 'recipePath', 'createdAt', 'connectomeHost', 'components',
              'cook', 'recipe', 'build', 'credentials', 'requirements', 'localExtensions', 'outputs', 'launch')


def replayed_build(case, content, tools):
    """The files of a build of `case` in the shape of a real one, by name. `tools` are corpus.py's own steps (see `build`)."""
    digest = lambda data: hashlib.sha256(data).hexdigest()
    effective, spec, request = tools['request_of'](case)
    root, state, name = case['root'], effective['state_dir'], effective['name']
    here = root + '/builds/' + request
    argv = case['cook_command']
    bun, node = (argv[argv.index(flag) + 1] for flag in ('--bun-path', '--node-path'))
    layout = {'schema': 'connectome.host-layout/v1', 'runtime': here, 'config': root + '/cook-config/' + request,
              'credentials': root + '/cook-credentials/' + request, 'state': state, 'workspace': root + '/workspace', 'connectorState': {}}
    unit = ('[Unit]\nDescription=Synthetic host (connectome-%s.service)\n\n[Service]\nType=simple\nWorkingDirectory=%s\n'
            'ExecStart=/bin/sh "%s/run.sh" --headless\nRestart=on-failure\nUMask=0077\n\n[Install]\nWantedBy=default.target\n' % (name, state, here)).encode()
    launcher = ('#!/bin/sh\n# synthetic launcher: the corpus starts nothing\ncd \'%s\'\nexit 0\n' % state).encode()
    dependencies = []
    for entry in effective['cook']['host_dependencies']:
        data = tools['archive'](entry['name'], entry['version'])
        snapshot = here + '/host-inputs/' + digest(data) + '.tgz'
        before = '^' + entry['version'].rpartition('.')[0] + '.0'
        dependencies.append({
            'name': entry['name'], 'version': entry['version'], 'sha256': digest(data), 'size': len(data), 'originalRange': before, 'snapshot': snapshot,
            'packageJsonDelta': {'direct': {'section': 'dependencies', 'name': entry['name'], 'before': before, 'after': 'file:' + snapshot},
                                 'override': {'section': 'overrides', 'name': entry['name'], 'before': None, 'after': 'file:' + snapshot}},
            # What the adapter adds once cook has run (connectome_archive.provenance_record).
            'provenance': {'name': entry['name'], 'version': entry['version'], 'sha256': digest(data), 'archive': entry['archive'], 'kind': 'artifact',
                           'commit_verifiable': False, 'payload': 'operator-supplied prebuilt payload, verified out of band'}})
    servers = json.loads(content)['mcpServers']
    declared = sorted(json.loads(content)['requirements'])
    lock = {
        'version': 2, 'backend': 'host', 'cook': {'version': '0.0.1'}, 'createdAt': '2026-01-02T03:04:05.678Z',
        'recipe': {'path': here + '/cook-recipe.json', 'sha256': digest(content)}, 'recipePath': here + '/cook-recipe.json',
        'build': {'id': digest(b'synthetic build identity'), 'dir': here},
        'connectomeHost': {'url': effective['cook']['host_repo'], 'ref': spec['pins']['runtime']['connectome-host'],
                           'commit': spec['pins']['runtime']['connectome-host'], 'path': here + '/app'},
        'engines': {'bun': {'path': bun, 'realPath': bun, 'version': '1.0.0'}, 'node': {'path': node, 'realPath': node, 'version': '1.0.0'}},
        'components': [], 'localExtensions': [],
        'requirements': [{'name': entry, 'envName': entry, 'origin': 'unresolved'} for entry in declared],
        'credentials': sorted(declared + [record['env'] for record in effective['secret_files']] + ['EXAMPLE_AUTH_NAME']),
        'external': [{'name': key, 'command': servers[key]['command']} for key in sorted(servers) if 'external' in servers[key].get('source', {})],
        'hostDependencies': dependencies,
        'outputs': [{'path': '../../cook-config/' + request + '/recipes/cook-recipe.json', 'mode': 0o600, 'sha256': digest(b'synthetic host recipe')},
                    {'path': 'connectome-%s.service' % name, 'mode': 0o600, 'sha256': digest(unit)}, {'path': 'run.sh', 'mode': 0o700, 'sha256': digest(launcher)}],
        'launch': {'kind': 'script', 'script': here + '/run.sh', 'userUnit': here + '/connectome-%s.service' % name},
        'layout': layout, 'state': {'path': state},
    }
    text = lambda value: (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    # The same lock as cook leaves it, before the adapter adds provenance and sorts: the key order the pinned cook writes.
    cooked = {key: lock[key] for key in COOK_ORDER if key in lock}
    assert set(cooked) == set(lock)
    cooked['hostDependencies'] = [{key: entry[key] for key in ('name', 'originalRange', 'version', 'sha256', 'size', 'snapshot', 'packageJsonDelta')} for entry in dependencies]
    cooked['connectomeHost'] = {key: lock['connectomeHost'][key] for key in ('url', 'ref', 'path', 'commit')}
    cooked['build'] = {key: lock['build'][key] for key in ('dir', 'id')}
    return request, (json.dumps(cooked, indent=2) + '\n').encode(), {'connectome.lock': text(lock), 'deployment.json': tools['canonical'](spec).encode(), 'cook-recipe.json': content, '.prepared': request.encode(),
                     'cook-layout.json': text(layout), '.cook-layout.json': (json.dumps({'phase': 'installed', 'layout': layout}, indent=2) + '\n').encode(),
                     'run.sh': launcher, 'connectome-%s.service' % name: unit}


def build(directory, defaults, catalog, versions, replay=None):
    """Writes the cases under `directory`/cases. `defaults`, `catalog` and `versions` are the oracle's own public files.

    `replay` holds three steps of corpus.py (`archive`, `request_of`, `canonical`); with it the third case is written too."""
    directory = Path(directory)
    runtime = dict(versions['runtime'])
    disabled = {'connectors': {name: {'enabled': False} for name, entry in catalog.items() if entry.get('kind', 'connector') == 'connector'},
                'modules': {name: {'enabled': False} for name in defaults.get('modules', {})}, 'required_secrets': []}

    def command(inputs, state, node):
        return [at('usr', 'bin', 'python3'), inputs + '/connectome-agents/scripts/cook-adapter.py', '--runtime', inputs + '/bun-1.0.0/bun',
                '--entrypoint', inputs + '/connectome-cook/bin/cook', '--bun-path', inputs + '/bun-1.0.0/bun', '--node-path', node,
                '--spec', '{recipe}', '--out', '{build_dir}', '--state', state]

    # A deployment from a pinned recipe file.
    home, inputs, connectors = at('home', 'example'), at('srv', 'example-inputs'), at('opt', 'example-connectors')
    runtime_tree = at('opt', 'example-runtime', 'r1')
    state = home + '/.local/state/connectome/example'
    content = (json.dumps(recipe(home, connectors, runtime_tree), indent=2) + '\n').encode()
    packages = {inputs + '/context-manager-1.2.3.tgz': {'name': SCOPE + '/context-manager', 'version': '1.2.3'},
                inputs + '/agent-framework-4.5.6.tgz': {'name': SCOPE + '/agent-framework', 'version': '4.5.6'}}
    agent = {
        'name': 'example', 'activation': 'start', 'exclusive_with': [{'scope': 'user', 'unit': 'connectome.service'}],
        'state_dir': state, 'secrets_file': home + '/.config/connectome-secrets/example.json', 'budgets': None,
        'external_data_args': [{'server': 'relay', 'path': state + '/relay-channel', 'kind': 'dir', 'flag': '--state',
                                'command': at('usr', 'bin', 'python3'), 'args': recipe(home, connectors, runtime_tree)['mcpServers']['relay']['args']}],
        'cook': {'host_repo': inputs + '/host-00000001.bundle', 'connector_state': {},
                 'host_dependencies': [{'name': package['name'], 'archive': path, 'kind': 'artifact', 'version': package['version'], 'sha256': None}
                                       for path, package in packages.items()]},
        'recipe_file': {'path': at('root', 'example-deploy', 'recipe.json'), 'sha256': None},
        'required_secrets': ['EXAMPLE_API_KEY', 'EXAMPLE_BASE_URL'], 'secret_files': [{'secret': 'CHAT_RC', 'env': 'CHAT_RC_PATH'}],
    }
    cases = [({'name': 'example', 'account': 'example', 'base': home + '/.local/share/connectome', 'root': home + '/.local/share/connectome/example',
               'unit_dir': home + '/.config/systemd/user', 'cook_command': command(inputs, state, at('opt', 'example-node', '1.0.0', 'bin', 'node')),
               'systemctl': ['systemctl', '--user'], 'recipe': {'file': 'recipe.json'},
               'archives': {path: {'package': package} for path, package in packages.items()},
               'layers': {'defaults': defaults, 'catalog': catalog, 'private': disabled, 'group': {'budgets': None}, 'agent': agent}}, content)]
    # A deployment the adapter writes the recipe for: a framework archive, a mock provider, one secret.
    home = at('home', 'example-trial')
    state, archive = home + '/.local/state/connectome/example-trial', home + '/inputs/agent-framework-4.5.6.tgz'
    package = {'name': SCOPE + '/agent-framework', 'version': '4.5.6'}
    agent = {'name': 'example-trial', 'agent': {'provider': 'mock', 'model': 'mock-example', 'systemPrompt': 'Synthetic prompt. Do nothing.'},
             'cook': {'host_repo': home + '/src/connectome-host', 'connector_state': {},
                      'framework_archive': dict(package, path=archive, sha256=None, registry_release=True)},
             'state_dir': state, 'secrets_file': home + '/.config/connectome-secrets/trial.json', 'budgets': None}
    cases.append(({'name': 'example-trial', 'account': 'example-trial', 'base': home + '/.local/share/connectome',
                   'root': home + '/.local/share/connectome/example-trial', 'unit_dir': home + '/.config/systemd/user',
                   'cook_command': command(home + '/src', state, home + '/opt/node-1.0.0/bin/node'), 'systemctl': ['systemctl', '--user'],
                   'archives': {archive: {'package': package}},
                   'layers': {'defaults': defaults, 'catalog': catalog, 'private': dict(disabled, required_secrets=['EXAMPLE_CANARY']),
                              'group': {'model': 'mock-example', 'budgets': None}, 'agent': agent}}, None))
    for case, content in cases:
        case['versions'] = {'runtime': runtime}
        case['substitutions'] = ['Synthetic case: nothing in it is a value of a deployed host.']
    files = {}
    if replay:
        # The first case again, pinned as a layer would commit it, with the files of a build of it in the shape of a real one.
        case, content = json.loads(json.dumps(cases[0][0])), cases[0][1]
        case['name'] = 'example-replay'
        case['recipe'] = {'file': 'build/cook-recipe.json', 'committed_sha256': hashlib.sha256(content).hexdigest(), 'matches_committed_pin': True}
        case['layers']['agent']['recipe_file']['sha256'] = case['recipe']['committed_sha256']
        for entry in case['layers']['agent']['cook']['host_dependencies']:
            entry['sha256'] = hashlib.sha256(replay['archive'](entry['name'], entry['version'])).hexdigest()
        request, cooked, files = replayed_build(case, content, replay)
        case['cook_build'] = {'directory': 'build', 'request_id': request, 'cooked': 'cooked.lock'}
        case['substitutions'].append('Cook is not run: test/corpus/replay_cook.py replays a made-up build in the shape of a real one, and writes its lock in the key order cook writes.')
        cases.append((case, None))
    for case, content in cases:
        target = directory / 'cases' / case['name']
        target.mkdir(parents=True)
        if content is not None:
            (target / 'recipe.json').write_bytes(content)
        if 'cook_build' in case:
            (target / 'build').mkdir()
            for name, data in files.items():
                (target / 'build' / name).write_bytes(data)
            (target / 'cooked.lock').write_bytes(cooked)
        (target / 'case.json').write_text(json.dumps(case, indent=1, sort_keys=True) + '\n')
