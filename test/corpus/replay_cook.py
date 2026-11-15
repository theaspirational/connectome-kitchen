#!/usr/bin/env python3
"""Stand-in for the cook binary that replays a build a real cook made. Test only: it builds and fetches nothing.

Where stand_in_cook.py writes a lock in the shape of the oracle's fixtures, this
script writes the lock of a real build, from a copy of that build's files kept
with the case (`cook_build` in case.json). It is started in the place of the
runtime, with the adapter's own argv, and it refuses a call the copied build
could not have come from: another unit name, host repository, host commit, Bun,
Node, layout, recipe or set of archives.

The copy cannot be used where it lies. A lock names its build directory and the
reader holds it to that, and a case runs under a sandbox root with a request id
of its own. So every file is carried over, the same way for each:

  - every absolute path under a moved top-level directory goes under the
    sandbox root, as corpus.py moves the layers and the recipe;
  - the request id of the real build becomes the name of the build it is
    replayed into;
  - the digest of a file whose bytes this changes (the recipe, the unit, the
    launcher) becomes the digest of the changed bytes.

Nothing else is changed, with one exception: the provenance the adapter adds to
each host dependency after cook has run is taken out again, because cook did not
write it and the adapter under test has to. What the copy does not hold is not
made up: the application, the engines, the per-request configuration directory.
The snapshots of the archives are copied from the archives the adapter names,
after their size and digest matched the lock. The modes are the ones a build
of the pinned cook has: the lock and the layout marker 0600, a snapshot 0400,
the launcher and the unit as the lock says.

A copy of a build holds the lock as the adapter rewrote it, sorted, so by
itself it does not say in which order cook wrote the keys; the lock is then
written sorted. A case may also keep the lock as cook wrote it, taken before the
adapter read it (`cooked` in `cook_build`; scripts/rehearse/cook-keep takes
it). Then those bytes are written instead, carried as text, so that the key
order, the indentation and the final newline are cook's. They must say what the
build's lock says without the provenance, or the replay refuses.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

PROVENANCE = 'provenance'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Carry:
    """How the text of a real build is carried over to one sandbox build."""

    def __init__(self, absolute, sandbox, real_request, request):
        self.absolute, self.sandbox, self.real_request, self.request = re.compile(absolute), sandbox, real_request, request
        self.digests = {}

    def text(self, value):
        value = self.absolute.sub(self.sandbox + '/', value).replace(self.real_request, self.request)
        for real, here in self.digests.items():
            value = value.replace(real, here)
        return value

    def file(self, path):
        """The carried bytes of a file whose digest a lock may name; the digest is carried from then on."""
        data = Path(path).read_bytes()
        carried = self.text(data.decode('utf-8')).encode('utf-8')
        self.digests[sha256(data)] = sha256(carried)
        return carried

    def value(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {self.text(key): self.value(item) for key, item in value.items()}
        return value


def carry_for(replay, request):
    """The carrier of one build, with the digests of the three files whose bytes change."""
    directory = Path(replay['directory'])
    carry = Carry(replay['absolute'], replay['sandbox'], replay['request_id'], request)
    lock = json.loads((directory / 'connectome.lock').read_text())
    files = {'cook-recipe.json': carry.file(directory / 'cook-recipe.json')}
    for name in (Path(lock['launch']['script']).name, Path(lock['launch']['userUnit']).name):
        files[name] = carry.file(directory / name)
    return carry, lock, files


def cook_lock(lock):
    """The lock without what the adapter added to it after cook had run."""
    lock = json.loads(json.dumps(lock))
    for dependency in lock.get('hostDependencies', []):
        dependency.pop(PROVENANCE, None)
    return lock


def refuse(message):
    sys.stderr.write('replay_cook: ' + message + '\n')
    return 3


def main(argv, replay):
    entrypoint, verb, recipe_path = argv[1:4]
    options, index = {}, 4
    while index < len(argv):
        name = argv[index]
        if name in ('--no-prompts', '--yes', '--strict'):
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
    directory = Path(replay['directory'])
    carry, real, files = carry_for(replay, build.name)
    lock = carry.value(cook_lock(real))
    # The call must be one the real build could have come from.
    script, unit = Path(lock['launch']['script']), Path(lock['launch']['userUnit'])
    asked = {
        'recipe path': (recipe_path, lock['recipePath']),
        'recipe bytes': (sha256(Path(recipe_path).read_bytes()), lock['recipe']['sha256']),
        'build directory': (str(build), lock['build']['dir']),
        'host repository': (options['--ch-repo'], [lock['connectomeHost']['url']]),
        'host commit': (options['--ch-ref'], [lock['connectomeHost']['commit']]),
        'Bun': (options['--bun-path'], [lock['engines']['bun']['path']]),
        'Node': (options['--node-path'], [lock['engines']['node']['path']]),
        'unit name': (options['--user-unit'], [unit.name]),
        'layout': (json.loads(Path(options['--layout'][0]).read_text()), lock['layout']),
        'layout file': (Path(options['--layout'][0]).read_bytes(), carry.text((directory / 'cook-layout.json').read_text()).encode()),
        'launcher and unit directory': ([str(script.parent), str(unit.parent)], [str(build)] * 2),
    }
    for what, (given, recorded) in asked.items():
        if given != recorded:
            return refuse('the adapter\'s call differs from the real build in: ' + what)
    archives = {}
    for value in options.get('--host-dependency', []):
        name, _, rest = value.partition('=')
        archive, _, digest = rest.rpartition('#sha256=')
        archives[name] = (archive, digest)
    dependencies = lock.get('hostDependencies', [])
    if [entry['name'] for entry in dependencies] != list(archives):
        return refuse('the adapter\'s call differs from the real build in: host dependencies and their order')
    for entry in dependencies:
        archive, digest = archives[entry['name']]
        data = Path(archive).read_bytes()
        if digest != entry['sha256'] or sha256(data) != digest or len(data) != entry['size']:
            return refuse('the adapter\'s call differs from the real build in: an archive')
        snapshot = Path(entry['snapshot'])
        if build not in snapshot.parents:
            return refuse('a snapshot of the real build lies outside its build')
        snapshot.parent.mkdir(mode=0o700, exist_ok=True)
        shutil.copyfile(archive, snapshot)
        snapshot.chmod(0o400)
    modes = {entry['path']: entry['mode'] for entry in lock['outputs']}
    for name in (script.name, unit.name):
        (build / name).write_bytes(files[name])
        (build / name).chmod(modes[name])
    hidden = directory / '.cook-layout.json'
    if hidden.is_file():
        (build / hidden.name).write_text(carry.text(hidden.read_text()))
        (build / hidden.name).chmod(0o600)
    text = json.dumps(lock, sort_keys=True, indent=2) + '\n'
    if replay.get('cooked'):
        text = carry.text(Path(replay['cooked']).read_text())
        if json.loads(text) != lock:
            return refuse('the lock as cook wrote it is not the lock of the build without the provenance')
    (build / 'connectome.lock').write_text(text)
    (build / 'connectome.lock').chmod(0o600)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv, globals()['REPLAY']))
