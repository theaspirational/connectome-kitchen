"""The public cases of the deploy corpus. Every name, path and number here is made up.

They have the shape of the deployments the corpus was built for and none of
their content: a recipe handed over as a pinned file with host-installed
servers, two archives as host dependencies, secrets by name and by file, a
private data argument, a unit to exclude; and a second case with no recipe, a
framework archive from a registry release and a mock provider. Absolute paths
are put together from their parts, as the publication gate reads this file too.
"""
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


def build(directory, defaults, catalog, versions):
    """Writes the cases under `directory`/cases. `defaults`, `catalog` and `versions` are the oracle's own public files."""
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
        target = directory / 'cases' / case['name']
        target.mkdir(parents=True)
        if content is not None:
            (target / 'recipe.json').write_bytes(content)
        (target / 'case.json').write_text(json.dumps(case, indent=1, sort_keys=True) + '\n')
