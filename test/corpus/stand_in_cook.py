#!/usr/bin/env python3
"""Stand-in for the cook binary. Test only: it builds nothing and fetches nothing.

The adapter of the oracle calls its runtime as

    <runtime> <entrypoint> install <recipe> --out <build> ... --layout <file>
        --host-dependency <name>=<archive>#sha256=<digest> ... --user-unit <name>

and then reads the lock cook leaves in the build. This script takes the place
of <runtime>: it records how it was called and writes a launcher, a user unit
and a version 2 host lock. The lock has the shape of the oracle's own fixtures
(oracle/tests/fixtures/locks/v2.json, oracle/tests/fixtures/cook.py and the
`materialize` fake of oracle/tests/test_cook.py). It is not what the pinned
cook writes for the same recipe: the unit text, the build id, the snapshot
names and the cook version are made up here, and say so. With the marker
`unit-without-install-section` beside the root, the unit has no `[Install]`
section.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile


def manifest_version(archive):
    with tarfile.open(archive, 'r:*') as tar:
        return json.loads(tar.extractfile('package/package.json').read())['version']


def main(argv):
    entrypoint, verb, recipe_path = argv[1:4]
    options, flags, index = {}, [], 4
    while index < len(argv):
        name = argv[index]
        if name in ('--no-prompts', '--yes', '--strict'):
            flags.append(name)
            index += 1
        else:
            options.setdefault(name, []).append(argv[index + 1])
            index += 2
    build = Path(options['--out'][0])
    root = build.parent.parent
    with (root / 'cook-calls.jsonl').open('a') as stream:
        stream.write(json.dumps({'argv': argv, 'cwd': os.getcwd(), 'environment_names': sorted(os.environ)}, sort_keys=True) + '\n')
    if verb != 'install':
        return 2
    recipe_bytes = Path(recipe_path).read_bytes()
    recipe = json.loads(recipe_bytes)
    layout = json.loads(Path(options['--layout'][0]).read_text())
    spec = json.loads((build / 'deployment.json').read_text())
    (build / 'run.sh').write_text('#!/bin/sh\n# stand-in launcher: the corpus starts nothing\nexit 0\n')
    (build / 'run.sh').chmod(0o755)
    unit = options['--user-unit'][0]
    (build / unit).write_text(
        '[Unit]\nDescription=Connectome agent (stand-in cook)\n[Service]\nType=simple\nWorkingDirectory=' + layout['state'] +
        '\nExecStart=' + str(build / 'run.sh') + '\nRestart=on-failure\n' +
        # corpus.py leaves this marker beside the root for the one scenario that needs a unit nothing links to.
        ('' if (root.parent / 'unit-without-install-section').exists() else '[Install]\nWantedBy=default.target\n'))
    names = set(spec['config'].get('required_secrets', []))
    names.update(record['env'] for record in spec['config'].get('secret_files', []))
    for name, entry in (recipe.get('requirements') or {}).items():
        names.add(entry.get('exposeAs', name) if isinstance(entry, dict) else name)
    dependencies = []
    for value in options.get('--host-dependency', []):
        name, _, rest = value.partition('=')
        archive, _, digest = rest.rpartition('#sha256=')
        version = manifest_version(archive)
        snapshot = str(build / 'host-dependencies' / Path(archive).name)
        dependencies.append({
            'name': name, 'version': version, 'sha256': digest, 'size': Path(archive).stat().st_size,
            'originalRange': '^' + version, 'snapshot': snapshot,
            'packageJsonDelta': {
                'direct': {'section': 'dependencies', 'name': name, 'before': '^' + version, 'after': 'file:' + snapshot},
                'override': {'section': 'overrides', 'name': name, 'before': None, 'after': 'file:' + snapshot}}})
    lock = {
        'version': 2, 'backend': 'host',
        'cook': {'version': '0.0.0-stand-in'},
        'recipe': {'path': recipe_path, 'sha256': hashlib.sha256(recipe_bytes).hexdigest()},
        'recipePath': recipe_path, 'createdAt': '2026-11-15T00:00:00.000Z',
        'build': {'id': hashlib.sha256(recipe_bytes).hexdigest(), 'dir': str(build)},
        'connectomeHost': {'url': options['--ch-repo'][0], 'ref': options['--ch-ref'][0], 'commit': options['--ch-ref'][0]},
        'components': [], 'localExtensions': [],
        'requirements': [{'name': name, 'envName': name, 'origin': 'process-env'} for name in sorted(names)],
        'credentials': sorted(names),
        'outputs': [{'path': 'run.sh', 'mode': 0o755, 'sha256': hashlib.sha256((build / 'run.sh').read_bytes()).hexdigest()}],
        'launch': {'kind': 'script', 'script': 'run.sh', 'userUnit': unit},
        'layout': layout, 'state': {'path': layout['state']},
    }
    servers = recipe.get('mcpServers') or {}
    external = [{'name': name, 'command': server['command']} for name, server in sorted(servers.items())
                if isinstance(server, dict) and isinstance(server.get('source'), dict) and 'external' in server['source']]
    if external:
        lock['external'] = external
    if dependencies:
        lock['hostDependencies'] = dependencies
    (build / 'connectome.lock').write_text(json.dumps(lock, sort_keys=True, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
