#!/usr/bin/env python3
"""Inputs for the errors/lock/inputs differential. Inputs only: no expected value is written here.

`hand()` is a curated list with stable ids: one or more inputs per guard,
per clause and per branch of the three modules, plus boundary cases.
`generated()` perturbs valid seeds systematically (the idea of
test/differential/guard_vectors.py, with file trees) and is not stored.

Strings a publication gate would read as a credential, an address or a private
path are never written literally: `J(...)` joins pieces when a vector is run,
`${ROOT}` is the vector's scratch tree.
"""
import base64, copy, hashlib, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'oracle/module_utils'))

R = '${ROOT}'
BUILD = R + '/build'
RECIPE = b'{"name": "example"}\n'
RECIPE_SHA = hashlib.sha256(RECIPE).hexdigest()
UNIT = b'[Service]\nExecStart=/bin/true\n'
UNIT_SHA = hashlib.sha256(UNIT).hexdigest()


def B(data):
    return {'$bytes': base64.b64encode(data).decode()}


def P(path):
    return {'$path': path}


def J(*parts):
    return {'$join': list(parts)}


def F(text):
    return {'$float': text}


def b64(data):
    return base64.b64encode(data).decode()


def tree(**extra):
    """The build tree every lock vector runs against, plus an operator area outside it."""
    files = {
        'build/run.sh': {'text': '#!/bin/sh\nexit 0\n', 'mode': 0o700},
        'build/example.service': {'file': b64(UNIT)},
        'build/units/nested.service': {'file': b64(UNIT)},
        'build/.service': {'file': b64(UNIT)},
        'build/plain.txt': {'text': 'plain\n'},
        'build/sub': {'dir': 0o755},
        'build/cook-recipe.json': {'file': b64(RECIPE), 'mode': 0o600},
        'build/link.service': {'link': 'example.service'},
        'build/escape.service': {'link': R + '/outside/evil.service'},
        'build/inside': {'link': R + '/build/units'},
        'build/out': {'link': R + '/outside'},
        'build/loop-a': {'link': 'loop-b'},
        'build/loop-b': {'link': 'loop-a'},
        'build/dangling': {'link': 'missing-target'},
        'outside/evil.service': {'file': b64(UNIT)},
        'outside/run.sh': {'text': '#!/bin/sh\nexit 0\n'},
        'alias': {'link': R + '/build'},
        'operator/recipe.json': {'file': b64(RECIPE), 'mode': 0o600},
        'operator/open.json': {'file': b64(RECIPE), 'mode': 0o644},
        'operator/link.json': {'link': 'recipe.json'},
        'operator/dir.json': {'dir': 0o755},
        'operator/array.json': {'text': '[]'},
        'operator/via': {'link': R + '/operator'},
        'closed': {'dir': 0o000},
    }
    files.update(extra)
    return files


def lock(**changes):
    data = {
        'version': 2, 'backend': 'host',
        'cook': {'version': '0.0.1', 'commit': 'e' * 40},
        'recipe': {'path': '/fixture/recipe.json', 'sha256': 'a' * 64},
        'recipePath': '/fixture/recipe.json',
        'createdAt': '2026-11-09T00:00:00.000Z',
        'build': {'id': 'b' * 64, 'dir': BUILD},
        'connectomeHost': {'url': 'https://example.invalid/host.git', 'ref': 'main', 'commit': 'c' * 40, 'path': BUILD + '/app'},
        'components': [
            {'kind': 'git', 'key': 'example', 'role': 'mcp', 'url': '/fixture/repo', 'ref': 'main', 'commit': 'a' * 40, 'path': '/app', 'install': ''},
            {'kind': 'npm', 'key': 'package', 'role': 'extension', 'name': 'example', 'spec': '1.0.0', 'version': '1.0.0', 'integrity': 'sha512-YWJj'},
        ],
        'localExtensions': [{'name': 'example', 'hostDir': '/fixture/extension', 'path': '/app/extension'}],
        'requirements': [{'name': 'EXAMPLE_NAME', 'envName': 'EXAMPLE_NAME', 'origin': 'unresolved'}],
        'credentials': ['EXAMPLE_NAME', 'store/example#field'],
        'outputs': [{'path': 'run.sh', 'mode': 448, 'sha256': 'd' * 64}, {'path': 'env', 'mode': 384, 'sha256Omitted': 'contains-requirement-value'}],
        'launch': {'kind': 'script', 'script': 'run.sh'},
        'state': {'path': '/fixture/state'},
        'engines': {name: {'path': '/fixture/bin/' + name, 'realPath': '/fixture/bin/' + name, 'version': '1.0.0'} for name in ('bun', 'node')},
        'layout': {'schema': 'connectome.host-layout/v1', 'runtime': '/fixture/runtime', 'config': '/fixture/config', 'state': '/fixture/state',
                   'workspace': '/fixture/workspace', 'credentials': '/fixture/credentials', 'connectorState': {'example': {'DATA_DIR': '/fixture/state/example'}}},
        'health': {'command': ['/fixture/bin/health', '--once']},
        'external': [{'name': 'alpha', 'command': '/fixture/bin/alpha'}, {'name': 'beta', 'command': '/fixture/bin/beta'}],
    }
    data.update(changes)
    return data


def minimal():
    data = lock()
    for key in ('state', 'engines', 'layout', 'health', 'external'):
        del data[key]
    data['cook'] = {'version': '0.0.1'}
    data['components'] = []
    data['localExtensions'] = []
    return data


def edit(value, path, new):
    """Copy of `value` with the node at `path` replaced; `DROP` removes it."""
    result = copy.deepcopy(value)
    target = result
    for key in path[:-1]:
        target = target[key]
    if new is DROP:
        del target[path[-1]]
    else:
        target[path[-1]] = copy.deepcopy(new)
    return result


DROP = object()


def dependency(name='example-package'):
    return {'name': name, 'originalRange': '^1.0.0', 'version': '1.0.0', 'sha256': 'e' * 64, 'size': 1, 'snapshot': 'snapshots/example.tgz',
            'packageJsonDelta': {'direct': {'section': 'dependencies', 'name': name, 'before': '^1.0.0', 'after': 'file:snapshots/example.tgz'},
                                 'override': {'section': 'overrides', 'name': name, 'before': None, 'after': 'file:snapshots/example.tgz'}}}


def declared(name='example-package'):
    return {'name': name, 'archive': '/fixture/archives/' + name + '.tgz', 'sha256': 'e' * 64, 'kind': 'artifact', 'version': '1.0.0'}


def provenance(name='example-package'):
    import connectome_archive
    return connectome_archive.provenance_record(declared(name))


def dependency_lock():
    record = dependency()
    record['provenance'] = provenance()
    return lock(hostDependencies=[record])


def dependency_config():
    return {'cook': {'host_dependencies': [declared()]}}


def recipe_config(path=R + '/operator/recipe.json'):
    return {'recipe_file': {'path': path, 'sha256': RECIPE_SHA}}


def recipe_lock():
    installed = BUILD + '/cook-recipe.json'
    return lock(recipe={'path': installed, 'sha256': RECIPE_SHA}, recipePath=installed)


def data_arg(**changes):
    record = {'server': 'example', 'command': '/fixture/bin/tool', 'args': ['--data', '/fixture/state/data'], 'path': '/fixture/state/data', 'kind': 'file', 'flag': '--data'}
    record.update(changes)
    return record


def config(**changes):
    data = {'external_data_args': [data_arg(), data_arg(server='second', kind='dir')],
            'activation': 'install_only',
            'exclusive_with': [{'scope': 'user', 'unit': 'other.service'}, {'scope': 'system', 'unit': 'other.service'}],
            'recipe_file': {'path': '/fixture/operator/recipe.json', 'sha256': 'a' * 64},
            'secret_files': [{'secret': 'EXAMPLE_NAME', 'env': 'EXAMPLE_FILE'}, {'secret': 'OTHER_NAME', 'env': 'OTHER_FILE'}]}
    data['external_data_args'][1].pop('flag')
    data.update(changes)
    return data


class Rows:
    def __init__(self):
        self.rows, self.ids = [], set()

    def add(self, ident, function, args, kwargs=None, files=None, **flags):
        if ident in self.ids:
            raise ValueError('duplicate vector id ' + ident)
        self.ids.add(ident)
        row = {'id': ident, 'function': function, 'args': args, 'kwargs': kwargs or {}}
        if files is not None:
            row['tree'] = files
        row.update(flags)
        self.rows.append(row)


def hand():
    rows = Rows()
    add = rows.add
    T = tree()

    def ml(ident, data, build=BUILD, files=T, **kwargs):
        add('lock.map.' + ident, 'connectome_lock.map_lock', [data, build], kwargs, files)

    # ---- map_lock: acceptance -------------------------------------------------
    ml('valid.rich', lock())
    ml('valid.minimal', minimal())
    ml('valid.units', lock(units=[{'path': 'example.service', 'sha256': UNIT_SHA}, {'path': 'units/nested.service', 'sha256': UNIT_SHA}]))
    ml('valid.user-unit', lock(launch={'kind': 'script', 'script': 'run.sh', 'userUnit': 'units/nested.service'}))
    ml('valid.user-unit.absolute', lock(launch={'kind': 'script', 'script': BUILD + '/run.sh', 'userUnit': BUILD + '/example.service'}))
    ml('valid.build-path', lock(), P(BUILD))
    ml('valid.build-unnormalized', lock(), R + '/build/./sub/..//')
    ml('valid.build-alias', lock(), R + '/alias')
    ml('valid.dir-alias', lock(build={'id': 'b' * 64, 'dir': R + '/alias'}))
    ml('valid.host-pin', lock(), host_pin='c' * 40)
    ml('valid.required-names', lock(), required_names=['EXAMPLE_NAME', 'store/example#field'])
    ml('valid.integer-like-keys', edit(lock(), ['layout', 'connectorState'], {'10': {'A': '/fixture/a'}, '9': {'B': '/fixture/b'}, 'x': {}}))
    # ---- row 4: top-level schema ---------------------------------------------
    ml('top.not-mapping', [lock()])
    ml('top.version.3', lock(version=3))
    ml('top.version.float', lock(version=F('2.0')))
    ml('top.version.bool', lock(version=True))
    ml('top.version.string', lock(version='2'))
    ml('top.version.missing', edit(lock(), ['version'], DROP))
    ml('top.backend.other', lock(backend='docker'))
    ml('top.backend.missing', edit(lock(), ['backend'], DROP))
    for key in ('cook', 'recipe', 'recipePath', 'createdAt', 'connectomeHost', 'components', 'localExtensions', 'requirements', 'build', 'credentials', 'outputs', 'launch'):
        ml('top.missing.' + key, edit(lock(), [key], DROP))
    ml('top.unsupported', lock(unexpected='extra'))
    # Two refusals in one document: which one is reported shows which guard ran first.
    ml('top.missing-and-incomplete', edit(lock(incomplete=True), ['launch'], DROP))
    ml('top.unsupported-and-incomplete', lock(incomplete=True, unexpected='extra'))
    ml('top.backend-and-incomplete', lock(incomplete=True, backend='docker'))
    # ---- row 2: names only ------------------------------------------------------
    for compact in ('value', 'Values', 'SECRET', 'secrets', 'token', 'pass-word', 'api_key', 'apiToken', 'auth.token', 'secretValue', 'resolved value'):
        ml('names.field.' + compact.replace(' ', '_'), edit(lock(), ['components', 0], dict(lock()['components'][0], **{compact: 'x'})))
    ml('names.field.nested-list', lock(outputs=[[{'value': 1}]]))
    ml('names.field.first-wins', edit(lock(), ['layout', 'connectorState'], {'value': 'a\u0001', 'x': 1}))
    ml('names.control.value', edit(lock(), ['health', 'command', 1], 'a\u0007b'))
    ml('names.control.key', edit(lock(), ['layout', 'connectorState'], {'a\u0085b': {}}))
    ml('names.control.del', edit(lock(), ['recipePath'], 'a\u007fb'))
    ml('names.credential.value', edit(lock(), ['health', 'command', 1], J('s', 'k-', 'a' * 12)))
    ml('names.credential.key', edit(lock(), ['layout', 'connectorState'], {'$mapping': [[J('gh', 'p_', 'a' * 12), {}]]}))
    # Key order decides which of two refusals is reported: an integer-like key must not move to the front.
    ml('names.order.insertion', edit(lock(), ['layout', 'connectorState'], {'x': 'a\u0001', '1': J('s', 'k-', 'a' * 12)}))
    ml('names.order.insertion-reverse', edit(lock(), ['layout', 'connectorState'], {'1': J('s', 'k-', 'a' * 12), 'x': 'a\u0001'}))
    # ---- row 5: incomplete --------------------------------------------------------
    ml('incomplete.true', lock(incomplete=True))
    ml('incomplete.null', lock(incomplete=None))
    # ---- row 6: build identity -------------------------------------------------
    ml('identity.not-mapping', lock(build=BUILD))
    ml('identity.extra', lock(build={'id': 'b' * 64, 'dir': BUILD, 'extra': 1}))
    ml('identity.id.short', lock(build={'id': 'b' * 63, 'dir': BUILD}))
    ml('identity.id.upper', lock(build={'id': 'B' * 64, 'dir': BUILD}))
    ml('identity.id.newline', lock(build={'id': 'b' * 64 + '\n', 'dir': BUILD}))
    ml('identity.dir.empty', lock(build={'id': 'b' * 64, 'dir': ''}))
    ml('identity.dir.relative', lock(build={'id': 'b' * 64, 'dir': 'build'}))
    ml('identity.dir.other', lock(build={'id': 'b' * 64, 'dir': R + '/outside'}))
    ml('identity.dir.child', lock(build={'id': 'b' * 64, 'dir': BUILD + '/sub'}))
    ml('identity.dir.dotdot', lock(build={'id': 'b' * 64, 'dir': BUILD + '/sub/..'}))
    ml('identity.dir.loop', lock(build={'id': 'b' * 64, 'dir': BUILD + '/loop-a'}))
    ml('identity.dir.missing', lock(build={'id': 'b' * 64, 'dir': R + '/missing'}), R + '/missing')
    ml('identity.dir.closed', lock(build={'id': 'b' * 64, 'dir': R + '/closed/x'}), R + '/closed/x')
    # ---- row 59: cook, recipe, timestamp ----------------------------------------
    ml('cook.not-mapping', lock(cook='0.0.1'))
    ml('cook.extra', lock(cook={'version': '0.0.1', 'extra': 1}))
    ml('cook.version.missing', lock(cook={}))
    for bad in ('1.0', '01.0.0', '1.0.0-', '^1.0.0', '1.0.0\n', 'v1.0.0-rc.1+build.5 '):
        ml('cook.version.bad.' + hashlib.sha256(bad.encode()).hexdigest()[:8], lock(cook={'version': bad}))
    ml('cook.version.prefixed', lock(cook={'version': 'v1.2.3-rc.1+build.5'}))
    ml('cook.commit.short', lock(cook={'version': '0.0.1', 'commit': 'e' * 39}))
    ml('cook.commit.sha256', lock(cook={'version': '0.0.1', 'commit': 'e' * 64}))
    ml('cook.commit.null', lock(cook={'version': '0.0.1', 'commit': None}))
    ml('recipe.not-mapping', lock(recipe='/fixture/recipe.json'))
    ml('recipe.extra', lock(recipe={'path': '/fixture/recipe.json', 'sha256': 'a' * 64, 'extra': 1}))
    ml('recipe.missing-sha', lock(recipe={'path': '/fixture/recipe.json'}))
    ml('recipe.path.empty', lock(recipe={'path': '', 'sha256': 'a' * 64}, recipePath=''))
    ml('recipe.sha.bad', lock(recipe={'path': '/fixture/recipe.json', 'sha256': 'g' * 64}))
    ml('recipe.path.differs', lock(recipePath='/fixture/other.json'))
    ml('recipe.recipe-path.number', lock(recipePath=1))
    ml('recipe.created.number', lock(createdAt=1))
    ml('recipe.created.empty', lock(createdAt=''))
    for stamp in ('2026-11-09T00:00:00Z', '2026-11-09T00:00:00.123456789Z', '2024-02-29T23:59:59.9Z', '0001-01-01T00:00:00Z', '9999-12-31T23:59:59Z'):
        ml('timestamp.ok.' + stamp.replace(':', '').replace('.', '_'), lock(createdAt=stamp))
    for stamp in ('2026-11-09T00:00:00', '2026-11-09 00:00:00Z', '2026-11-09T00:00:00+00:00', '2026-11-09T00:00:00.Z', '2026-13-09T00:00:00Z', '2026-02-29T00:00:00Z',
                  '2026-11-31T00:00:00Z', '2026-11-09T24:00:00Z', '2026-11-09T00:60:00Z', '2026-11-09T00:00:60Z', '0000-01-01T00:00:00Z', '2026-00-09T00:00:00Z',
                  '2026-11-00T00:00:00Z', '2026-11-09T00:00:00Z\n', '2026-11-09T00:00:00z', 'Z2026-11-09T00:00:00Z', '1900-02-29T00:00:00Z', '2000-02-29T00:00:00Z',
                  '\u0662026-11-09T00:00:00Z'):
        ml('timestamp.' + hashlib.sha256(stamp.encode()).hexdigest()[:8], lock(createdAt=stamp))
    # ---- row 59: operator recipe (lazy inputs import) ---------------------------
    ml('operator.valid', recipe_lock(), config=recipe_config())
    ml('operator.digest-differs', lock(recipe={'path': BUILD + '/cook-recipe.json', 'sha256': 'a' * 64}, recipePath=BUILD + '/cook-recipe.json'), config=recipe_config())
    ml('operator.path-differs', lock(recipe={'path': '/fixture/recipe.json', 'sha256': RECIPE_SHA}), config=recipe_config())
    ml('operator.installed-missing', recipe_lock(), files=tree(**{'build/cook-recipe.json': {'dir': 0o755}}), config=recipe_config())
    ml('operator.installed-differs', recipe_lock(), files=tree(**{'build/cook-recipe.json': {'text': '{}'}}), config=recipe_config())
    ml('operator.installed-link', recipe_lock(), files=tree(**{'build/cook-recipe.json': {'link': R + '/operator/recipe.json'}}), config=recipe_config())
    ml('operator.config-invalid', recipe_lock(), config={'recipe_file': {'path': 'relative.json', 'sha256': RECIPE_SHA}})
    # The same refusals followed by a second defect, so that skipping the first check is visible as the second refusal.
    late = {'createdAt': 'not a timestamp'}
    ml('operator.digest-differs.then-timestamp', dict(lock(recipe={'path': BUILD + '/cook-recipe.json', 'sha256': 'a' * 64}, recipePath=BUILD + '/cook-recipe.json'), **late), config=recipe_config())
    ml('operator.path-differs.then-timestamp', dict(lock(recipe={'path': '/fixture/recipe.json', 'sha256': RECIPE_SHA}), **late), config=recipe_config())
    ml('operator.installed-missing.then-timestamp', dict(recipe_lock(), **late), files=tree(**{'build/cook-recipe.json': {'dir': 0o755}}), config=recipe_config())
    ml('operator.installed-differs.then-timestamp', dict(recipe_lock(), **late), files=tree(**{'build/cook-recipe.json': {'text': '{}'}}), config=recipe_config())
    ml('operator.config-invalid.then-timestamp', dict(recipe_lock(), **late), config={'recipe_file': {'path': 'relative.json', 'sha256': RECIPE_SHA}})
    ml('operator.valid.then-timestamp', dict(recipe_lock(), **late), config=recipe_config())
    ml('operator.config-data-args', recipe_lock(), config=dict(recipe_config(), external_data_args=[{}]))
    ml('operator.config-activation', recipe_lock(), config=dict(recipe_config(), activation='stop'))
    ml('operator.config-list', recipe_lock(), config=['recipe_file'])
    ml('operator.config-string', recipe_lock(), config='has recipe_file inside')
    ml('operator.config-number', recipe_lock(), config=7)
    ml('operator.config-other-string', recipe_lock(), config='no such key')
    # ---- row 8: host pin, names, external ---------------------------------------
    ml('host.not-mapping', lock(connectomeHost='x'))
    ml('host.extra', edit(lock(), ['connectomeHost', 'extra'], 1))
    ml('host.url.missing', edit(lock(), ['connectomeHost', 'url'], DROP))
    ml('host.url.http', edit(lock(), ['connectomeHost', 'url'], 'http://example.invalid/host.git'))
    ml('host.ref.empty', edit(lock(), ['connectomeHost', 'ref'], ''))
    ml('host.commit.short', edit(lock(), ['connectomeHost', 'commit'], 'c' * 39))
    ml('host.path.empty', edit(lock(), ['connectomeHost', 'path'], ''))
    ml('host.path.absent', edit(lock(), ['connectomeHost', 'path'], DROP))
    ml('host.pin.differs', lock(), host_pin='d' * 40)
    ml('host.pin.number', lock(), host_pin=0)
    ml('names.required.missing', lock(), required_names=['EXAMPLE_NAME', 'OTHER_NAME'])
    ml('names.required.string-chars', lock(credentials=['A', 'B']), required_names='AB')
    ml('names.required.number', lock(), required_names=[1])
    ml('names.required.unhashable', lock(), required_names=[['EXAMPLE_NAME']])
    ml('external.not-list', lock(external={'name': 'alpha'}))
    ml('external.empty', lock(external=[]))
    ml('external.extra', lock(external=[{'name': 'alpha', 'command': '/fixture/bin/alpha', 'args': []}]))
    ml('external.name.bad', lock(external=[{'name': '-alpha', 'command': '/fixture/bin/alpha'}]))
    ml('external.name.newline', lock(external=[{'name': 'alpha\n', 'command': '/fixture/bin/alpha'}]))
    for command in ('bin/alpha', '/', '/fixture//alpha', '/fixture/./alpha', '/fixture/../alpha', '/fixture/alpha/', '/fixture/${HOME}/alpha', '//fixture/alpha', '/fixture/..alpha'):
        ml('external.command.' + hashlib.sha256(command.encode()).hexdigest()[:8], lock(external=[{'name': 'alpha', 'command': command}]))
    ml('external.duplicate', lock(external=[{'name': 'alpha', 'command': '/fixture/bin/alpha'}, {'name': 'alpha', 'command': '/fixture/bin/beta'}]))
    ml('external.unsorted', lock(external=[{'name': 'beta', 'command': '/fixture/bin/beta'}, {'name': 'alpha', 'command': '/fixture/bin/alpha'}]))
    ml('external.sorted.case', lock(external=[{'name': 'Z', 'command': '/fixture/bin/z'}, {'name': '_', 'command': '/fixture/bin/u'}, {'name': 'a', 'command': '/fixture/bin/a'}]))
    ml('external.connector-state', edit(lock(), ['layout', 'connectorState'], {'beta': {}}))
    ml('external.no-layout', edit(lock(), ['layout'], DROP))
    # ---- row 59: requirements, credentials --------------------------------------
    ml('requirements.not-list', lock(requirements={}))
    ml('requirements.item.not-mapping', lock(requirements=['EXAMPLE_NAME']))
    ml('requirements.item.extra', lock(requirements=[{'name': 'A', 'envName': 'A', 'origin': 'probe', 'extra': 1}]))
    ml('requirements.name.empty', lock(requirements=[{'name': '', 'envName': 'A', 'origin': 'probe'}]))
    ml('requirements.env.lower', lock(requirements=[{'name': 'A', 'envName': 'a', 'origin': 'probe'}]))
    ml('requirements.env.digit', lock(requirements=[{'name': 'A', 'envName': '1A', 'origin': 'probe'}]))
    ml('requirements.origin.unknown', lock(requirements=[{'name': 'A', 'envName': 'A', 'origin': 'file'}]))
    ml('requirements.origin.empty', lock(requirements=[{'name': 'A', 'envName': 'A', 'origin': ''}]))
    for origin in ('env-file', 'process-env', 'probe', 'prompt'):
        ml('requirements.origin.' + origin, lock(requirements=[{'name': 'A', 'envName': 'A_1', 'origin': origin}]))
    ml('credentials.not-list', lock(credentials='EXAMPLE_NAME'))
    ml('credentials.number', lock(credentials=[1]))
    ml('credentials.lower', lock(credentials=['lower']))
    ml('credentials.store-form', lock(credentials=['a/b.c-d_e#f-1']))
    ml('credentials.store-form.bad', lock(credentials=['a/b#c#d']))
    ml('credentials.trailing-newline', lock(credentials=['EXAMPLE_NAME\n']))
    # ---- row 59: components, outputs, extensions ---------------------------------
    for key in ('components', 'localExtensions', 'outputs'):
        ml('inventory.' + key, lock(**{key: {}}))
    git, npm = lock()['components']
    ml('component.not-mapping', lock(components=['git']))
    ml('component.kind.other', lock(components=[dict(git, kind='svn')]))
    ml('component.kind.missing', lock(components=[edit(git, ['kind'], DROP)]))
    ml('component.kind.unhashable', lock(components=[dict(git, kind=[])]))
    ml('component.git.extra', lock(components=[dict(git, name='x')]))
    for key in ('key', 'ref', 'path'):
        ml('component.git.' + key + '.missing', lock(components=[edit(git, [key], DROP)]))
        ml('component.git.' + key + '.empty', lock(components=[dict(git, **{key: ''})]))
    ml('component.git.url.bad', lock(components=[dict(git, url='ftp://example.invalid/x')]))
    ml('component.git.install.missing', lock(components=[edit(git, ['install'], DROP)]))
    ml('component.git.install.null', lock(components=[dict(git, install=None)]))
    ml('component.git.commit.bad', lock(components=[dict(git, commit='a' * 41)]))
    ml('component.npm.extra', lock(components=[dict(npm, url='/fixture/x')]))
    for key in ('key', 'name', 'spec'):
        ml('component.npm.' + key + '.missing', lock(components=[edit(npm, [key], DROP)]))
    ml('component.npm.version.bad', lock(components=[dict(npm, version='1.x')]))
    ml('component.npm.version.absent', lock(components=[edit(npm, ['version'], DROP)]))
    ml('component.npm.integrity.bad', lock(components=[dict(npm, integrity='md5-YWJj')]))
    ml('component.npm.integrity.empty', lock(components=[dict(npm, integrity='')]))
    ml('component.npm.integrity.many', lock(components=[dict(npm, integrity='sha1-YQ== sha256-YWI= sha384-YWJj sha512-YWJj')]))
    ml('component.npm.integrity.double-space', lock(components=[dict(npm, integrity='sha1-YQ==  sha256-YWI=')]))
    ml('component.npm.integrity.absent', lock(components=[edit(npm, ['integrity'], DROP)]))
    ml('component.role.other', lock(components=[dict(git, role='module')]))
    ml('component.role.missing', lock(components=[edit(npm, ['role'], DROP)]))
    ml('component.role.unhashable', lock(components=[dict(npm, role={})]))
    out = lock()['outputs'][0]
    ml('output.not-mapping', lock(outputs=['run.sh']))
    ml('output.extra', lock(outputs=[dict(out, size=1)]))
    ml('output.path.missing', lock(outputs=[edit(out, ['path'], DROP)]))
    for mode in (-1, 512, F('448.0'), True, '448', None):
        ml('output.mode.' + str(mode if not isinstance(mode, dict) else 'float'), lock(outputs=[dict(out, mode=mode)]))
    ml('output.mode.zero', lock(outputs=[dict(out, mode=0)]))
    ml('output.mode.max', lock(outputs=[dict(out, mode=511)]))
    ml('output.sha.bad', lock(outputs=[dict(out, sha256='d' * 63)]))
    ml('output.sha.absent', lock(outputs=[edit(out, ['sha256'], DROP)]))
    ml('output.omitted.other', lock(outputs=[{'path': 'env', 'mode': 384, 'sha256Omitted': 'because'}]))
    ml('output.omitted.true', lock(outputs=[{'path': 'env', 'mode': 384, 'sha256Omitted': True}]))
    ext = lock()['localExtensions'][0]
    ml('extension.not-mapping', lock(localExtensions=['example']))
    ml('extension.extra', lock(localExtensions=[dict(ext, extra='x')]))
    ml('extension.missing', lock(localExtensions=[edit(ext, ['hostDir'], DROP)]))
    ml('extension.empty', lock(localExtensions=[dict(ext, path='')]))
    ml('extension.number', lock(localExtensions=[dict(ext, name=1)]))
    # ---- row 59: state, engines, layout, health -----------------------------------
    ml('state.not-mapping', lock(state='/fixture/state'))
    ml('state.extra', lock(state={'path': '/fixture/state', 'mode': 448}))
    ml('state.relative', lock(state={'path': 'state'}))
    ml('state.empty', lock(state={'path': ''}))
    ml('state.null', lock(state=None))
    engine = lock()['engines']['bun']
    ml('engines.not-mapping', lock(engines=[]))
    ml('engines.missing', lock(engines={'bun': engine}))
    ml('engines.extra', lock(engines={'bun': engine, 'node': engine, 'deno': engine}))
    ml('engine.not-mapping', lock(engines={'bun': engine, 'node': '/fixture/bin/node'}))
    ml('engine.extra', lock(engines={'bun': engine, 'node': dict(engine, extra=1)}))
    ml('engine.path.empty', lock(engines={'bun': dict(engine, path=''), 'node': engine}))
    ml('engine.real.empty', lock(engines={'bun': engine, 'node': dict(engine, realPath='')}))
    ml('engine.version.range', lock(engines={'bun': engine, 'node': dict(engine, version='>=1.0.0')}))
    layout = lock()['layout']
    ml('layout.not-mapping', lock(layout='x'))
    ml('layout.extra', lock(layout=dict(layout, extra=1)))
    ml('layout.missing', lock(layout=edit(layout, ['workspace'], DROP)))
    ml('layout.schema', lock(layout=dict(layout, schema='connectome.host-layout/v2')))
    for root in ('runtime', 'config', 'state', 'workspace', 'credentials'):
        ml('layout.root.relative.' + root, lock(layout=dict(layout, **{root: 'relative'})))
    ml('layout.root.empty', lock(layout=dict(layout, config='')))
    ml('layout.connector-state.list', lock(layout=dict(layout, connectorState=[])))
    ml('layout.bindings.list', lock(layout=dict(layout, connectorState={'example': []})))
    ml('layout.bindings.name', lock(layout=dict(layout, connectorState={'example': {'data_dir': '/fixture/x'}})))
    ml('layout.bindings.path-empty', lock(layout=dict(layout, connectorState={'example': {'DATA_DIR': ''}})))
    ml('layout.bindings.path-number', lock(layout=dict(layout, connectorState={'example': {'DATA_DIR': 1}})))
    ml('health.not-mapping', lock(health=['/fixture/bin/health']))
    ml('health.extra', lock(health={'command': ['/fixture/bin/health'], 'timeout': 1}))
    ml('health.command.string', lock(health={'command': '/fixture/bin/health'}))
    ml('health.command.empty', lock(health={'command': []}))
    ml('health.command.blank-arg', lock(health={'command': ['/fixture/bin/health', '']}))
    ml('health.command.number', lock(health={'command': ['/fixture/bin/health', 1]}))
    # ---- rows 10 and 59: launch and units -------------------------------------------
    ml('launch.not-mapping', lock(launch='run.sh'))
    ml('launch.extra', lock(launch={'kind': 'script', 'script': 'run.sh', 'args': []}))
    ml('launch.kind.other', lock(launch={'kind': 'binary', 'script': 'run.sh'}))
    ml('launch.kind.missing', lock(launch={'script': 'run.sh'}))
    ml('launch.script.missing', lock(launch={'kind': 'script'}))
    ml('launch.script.empty', lock(launch={'kind': 'script', 'script': ''}))
    ml('launch.script.number', lock(launch={'kind': 'script', 'script': 1}))
    for script in ('missing.sh', 'sub', 'link.service', '../outside/run.sh', R + '/outside/run.sh', 'out/run.sh', 'escape.service', 'dangling', 'loop-a', 'inside/nested.service',
                   'sub/../run.sh', './run.sh', 'run.sh/', 'run.sh/x', 'x' * 300, '../build/run.sh'):
        ml('launch.script.' + hashlib.sha256(script.encode()).hexdigest()[:8], lock(launch={'kind': 'script', 'script': script}))
    shut = tree(**{'build/shut': {'dir': 0o000}, 'build/shut/run.sh': {'text': '#!/bin/sh\n'}})
    ml('launch.script.unreadable-parent', lock(launch={'kind': 'script', 'script': 'shut/run.sh'}), files=shut)
    ml('launch.script.alias-build', lock(launch={'kind': 'script', 'script': R + '/alias/run.sh'}))
    ml('units.ambiguous', lock(launch={'kind': 'script', 'script': 'run.sh', 'userUnit': 'example.service'}, units=[{'path': 'example.service', 'sha256': UNIT_SHA}]))
    for unit in ('plain.txt', '.service', 'link.service', 'missing.service', 'escape.service', 'sub', 'example.service/', 'units/../example.service', 'inside/nested.service'):
        ml('user-unit.' + hashlib.sha256(unit.encode()).hexdigest()[:8], lock(launch={'kind': 'script', 'script': 'run.sh', 'userUnit': unit}))
    ml('user-unit.alias-absolute', lock(launch={'kind': 'script', 'script': 'run.sh', 'userUnit': R + '/alias/example.service'}))
    ml('user-unit.bad-name', lock(launch={'kind': 'script', 'script': 'run.sh', 'userUnit': 'bad name.service'}), files=tree(**{'build/bad name.service': {'file': b64(UNIT)}}))
    ml('user-unit.number', lock(launch={'kind': 'script', 'script': 'run.sh', 'userUnit': 1}))
    ml('units.not-list', lock(units={'path': 'example.service', 'sha256': UNIT_SHA}))
    ml('units.empty', lock(units=[]))
    ml('units.item.not-mapping', lock(units=['example.service']))
    ml('units.item.extra', lock(units=[{'path': 'example.service', 'sha256': UNIT_SHA, 'mode': 420}]))
    ml('units.item.sha.bad', lock(units=[{'path': 'example.service', 'sha256': 'x'}]))
    ml('units.item.sha.differs', lock(units=[{'path': 'example.service', 'sha256': '0' * 64}]))
    ml('units.item.suffix', lock(units=[{'path': 'plain.txt', 'sha256': hashlib.sha256(b'plain\n').hexdigest()}]))
    ml('units.item.dot-service', lock(units=[{'path': '.service', 'sha256': UNIT_SHA}]))
    ml('units.item.missing', lock(units=[{'path': 'missing.service', 'sha256': UNIT_SHA}]))
    ml('units.item.link', lock(units=[{'path': 'link.service', 'sha256': UNIT_SHA}]))
    ml('units.item.path.number', lock(units=[{'path': 1, 'sha256': UNIT_SHA}]))
    ml('units.item.second-bad', lock(units=[{'path': 'example.service', 'sha256': UNIT_SHA}, {'path': 'units/nested.service', 'sha256': '0' * 64}]))
    # ---- row 9: host dependencies (archive seam) -------------------------------------
    dep = dependency_lock()['hostDependencies'][0]

    def deps(ident, *records, **kwargs):
        ml('deps.' + ident, lock(hostDependencies=list(records)), **kwargs)
    deps('valid', dep)
    deps('valid.config', dep, config=dependency_config(), framework_pin='f' * 40)
    deps('valid.no-provenance', edit(dep, ['provenance'], DROP))
    ml('deps.not-list', lock(hostDependencies={}))
    ml('deps.empty', lock(hostDependencies=[]))
    ml('deps.empty.config', lock(hostDependencies=[]), config={})
    ml('deps.absent.config', lock(), config={})
    ml('deps.absent.config-declares', lock(), config=dependency_config())
    deps('item.not-mapping', 'example-package')
    deps('item.extra', dict(dep, extra=1))
    for key in ('name', 'originalRange', 'version', 'sha256', 'size', 'snapshot', 'packageJsonDelta'):
        deps('item.missing.' + key, edit(dep, [key], DROP))
    for key in ('name', 'originalRange', 'snapshot'):
        deps('item.empty.' + key, dict(dep, **{key: ''}))
    deps('item.version', dict(dep, version='1'))
    deps('item.sha', dict(dep, sha256='e' * 63))
    for size in (0, -1, F('1.0'), True, '1'):
        deps('item.size.' + str(size if not isinstance(size, dict) else 'float'), dict(dep, size=size))
    deps('name.not-npm', edit(edit(dep, ['name'], 'Example'), ['provenance'], DROP))
    deps('name.duplicate', edit(dep, ['provenance'], DROP), edit(dep, ['provenance'], DROP))
    deps('provenance.invalid', dict(dep, provenance={}))
    deps('provenance.not-mapping', dict(dep, provenance='x'))
    for key in ('name', 'sha256', 'version'):
        other = {'name': 'other-package', 'sha256': 'f' * 64, 'version': '2.0.0'}[key]
        deps('provenance.identity.' + key, dict(dep, provenance=dict(dep['provenance'], **{key: other})))
    delta = dep['packageJsonDelta']
    deps('delta.not-mapping', dict(dep, packageJsonDelta=[]))
    deps('delta.extra', dict(dep, packageJsonDelta=dict(delta, extra={})))
    deps('delta.missing', dict(dep, packageJsonDelta={'direct': delta['direct']}))
    for kind in ('direct', 'override'):
        e = delta[kind]
        deps('edit.' + kind + '.not-mapping', dict(dep, packageJsonDelta=dict(delta, **{kind: 'x'})))
        deps('edit.' + kind + '.extra', dict(dep, packageJsonDelta=dict(delta, **{kind: dict(e, extra=1)})))
        deps('edit.' + kind + '.section', dict(dep, packageJsonDelta=dict(delta, **{kind: dict(e, section='peerDependencies')})))
        deps('edit.' + kind + '.section-swapped', dict(dep, packageJsonDelta=dict(delta, **{kind: dict(e, section='overrides' if kind == 'direct' else 'dependencies')})))
        deps('edit.' + kind + '.section-unhashable', dict(dep, packageJsonDelta=dict(delta, **{kind: dict(e, section=[])})))
        deps('edit.' + kind + '.name', dict(dep, packageJsonDelta=dict(delta, **{kind: dict(e, name='other-package')})))
        deps('edit.' + kind + '.after', dict(dep, packageJsonDelta=dict(delta, **{kind: dict(e, after='')})))
        deps('edit.' + kind + '.before', dict(dep, packageJsonDelta=dict(delta, **{kind: dict(e, before=None if kind == 'direct' else '^1.0.0')})))
    deps('config.differs', dep, config={'cook': {'host_dependencies': [dict(declared(), sha256='f' * 64)]}})
    deps('config.missing', dep, config={})
    deps('config.not-mapping', dep, config=[])
    deps('config.framework-record', dep, config=dependency_config(), framework_pin='f' * 40, framework_record={'version': '1.0.0'})
    # ---- read_lock ---------------------------------------------------------------------
    def rl(ident, text, **kwargs):
        # A text spec has ${ROOT} bound when the tree is created; raw bytes are written as they are.
        files = tree(**{'build/connectome.lock': {'file': b64(text)} if isinstance(text, bytes) else {'text': text}})
        add('lock.read.' + ident, 'connectome_lock.read_lock', [BUILD + '/connectome.lock'], kwargs, files)
    good = json.dumps(lock())
    rl('valid', good)
    rl('valid.path-object', good)
    rows.rows[-1]['args'] = [P(BUILD + '/connectome.lock')]
    rl('valid.spaced', json.dumps(lock(), indent=2) + '\r\n')
    rl('valid.kwargs', good, host_pin='c' * 40, required_names=['EXAMPLE_NAME'])
    rl('host-pin-differs', good, host_pin='d' * 40)
    rl('duplicate.top', good[:-1] + ',"version":2}')
    rl('duplicate.nested', good.replace('"kind": "script"', '"kind": "script", "kind": "script"'))
    rl('duplicate.same-value', '{"a":1,"a":1}')
    rl('nonfinite.nan', good.replace('"version": 2', '"version": NaN'))
    rl('nonfinite.infinity', good.replace('448', 'Infinity'))
    rl('nonfinite.negative', good.replace('448', '-Infinity'))
    rl('nonfinite.before-duplicate', '{"a":1,"a":NaN}')
    rl('duplicate.before-nonfinite', '{"x":{"a":1,"a":2},"y":NaN}')
    rl('duplicate.before-syntax', '{"x":{"a":1,"a":2},"y":}')
    rl('syntax.before-duplicate', '{"a":1,"a":2,"y":}')
    rl('overflow-float', good.replace('"version": 2', '"version": 1E400'))
    rl('overflow-float.mode', good.replace('448', '1e999'))
    rl('integer.4300', good.replace('448', '1' * 4300))
    rl('integer.4301', good.replace('448', '1' * 4301))
    rl('integer.negative-4301', good.replace('448', '-' + '1' * 4301))
    rl('float.long-digits', good.replace('448', '1' * 4301 + '.0'))
    rl('empty', '')
    rl('whitespace', ' \n')
    rl('array', '[]')
    rl('string', '"lock"')
    rl('null', 'null')
    rl('trailing', good + ' x')
    rl('trailing-comma', good[:-1] + ',}')
    rl('bom', '\ufeff' + good)
    rl('invalid-utf8', good.encode() + b'\xff')
    rl('utf16', good.encode('utf-16'))
    rl('control-in-string', good.replace('main', 'ma\nin'))
    rl('escaped-control', good.replace('main', 'ma\\u0001in'))
    rl('escaped-surrogate', good.replace('"main"', '"\\ud800"'))
    rl('surrogate-pair', good.replace('"main"', '"\\ud83d\\ude00"'))
    rl('escape.bad', good.replace('main', 'ma\\x41in'))
    rl('escape.short-u', good.replace('main', 'ma\\u12'))
    rl('number.leading-zero', good.replace('448', '0448'))
    rl('number.plus', good.replace('448', '+448'))
    rl('number.exponent-int', good.replace('448', '448e0'))
    rl('number.negative-zero', good.replace('448', '-0'))
    rl('literal.case', good.replace('448', 'True'))
    rl('nan.case', good.replace('448', 'nan'))
    rl('unhashable-kind', json.dumps(lock(components=[dict(lock()['components'][0], kind=[])])))
    rl('unhashable-role', json.dumps(lock(components=[dict(lock()['components'][0], role={})])))
    rl('not-object-root.number', '2')
    rl('crlf-only', good.replace(', ', ',\r'))
    add('lock.read.missing', 'connectome_lock.read_lock', [BUILD + '/missing.lock'], {}, T)
    add('lock.read.directory', 'connectome_lock.read_lock', [BUILD + '/sub'], {}, T)
    add('lock.read.closed', 'connectome_lock.read_lock', [R + '/closed/connectome.lock'], {}, T)
    add('lock.read.loop', 'connectome_lock.read_lock', [BUILD + '/loop-a'], {}, T)
    add('lock.read.via-alias', 'connectome_lock.read_lock', [R + '/alias/connectome.lock'], {},
        tree(**{'build/connectome.lock': {'text': json.dumps(lock())}}))
    add('lock.read.relative-parent', 'connectome_lock.read_lock', [BUILD + '/sub/../connectome.lock'], {},
        tree(**{'build/connectome.lock': {'text': json.dumps(lock())}}))
    add('lock.read.operator', 'connectome_lock.read_lock', [BUILD + '/connectome.lock'], {'config': recipe_config()},
        tree(**{'build/connectome.lock': {'text': json.dumps(recipe_lock())}}))
    add('lock.read.operator.config-list', 'connectome_lock.read_lock', [BUILD + '/connectome.lock'], {'config': ['recipe_file']},
        tree(**{'build/connectome.lock': {'text': json.dumps(recipe_lock())}}))
    add('lock.read.config-number', 'connectome_lock.read_lock', [BUILD + '/connectome.lock'], {'config': 7},
        tree(**{'build/connectome.lock': {'text': json.dumps(lock())}}))
    add('lock.read.required-unhashable', 'connectome_lock.read_lock', [BUILD + '/connectome.lock'], {'required_names': [[1]]},
        tree(**{'build/connectome.lock': {'text': json.dumps(lock())}}))
    add('lock.read.deps.config', 'connectome_lock.read_lock', [BUILD + '/connectome.lock'], {'config': dependency_config(), 'framework_pin': 'f' * 40},
        tree(**{'build/connectome.lock': {'text': json.dumps(dependency_lock())}}))
    for name, value in {'string': 'x', 'list': [], 'number': 1, 'null': None, 'empty': {}}.items():
        record = dict(dependency())
        record['provenance'] = value
        add('lock.read.deps.provenance.' + name, 'connectome_lock.read_lock', [BUILD + '/connectome.lock'], {},
            tree(**{'build/connectome.lock': {'text': json.dumps(lock(hostDependencies=[record]))}}))
    add('lock.read.deps.config-differs', 'connectome_lock.read_lock', [BUILD + '/connectome.lock'], {'config': {}},
        tree(**{'build/connectome.lock': {'text': json.dumps(dependency_lock())}}))
    add('lock.read.unreadable-parent', 'connectome_lock.read_lock', [BUILD + '/connectome.lock'], {},
        tree(**{'build/shut': {'dir': 0o000}, 'build/shut/run.sh': {'text': '#!/bin/sh\n'},
                'build/connectome.lock': {'text': json.dumps(lock(launch={'kind': 'script', 'script': 'shut/run.sh'}))}}))
    # Nesting bounds, stated for a script-level caller (see lock.ts and pyjson.ts).
    before, after = json.dumps(lock(components='@@')).split('"@@"')
    for depth, leaf in ((900, ''), (985, '"x"'), (990, '"x"'), (991, '"x"'), (992, '"x"'), (995, ''), (996, ''), (997, ''), (998, ''), (2000, ''), (9990, ''), (9996, ''), (9997, ''), (9998, ''), (9999, '')):
        add('lock.read.depth.%d.%s' % (depth, 'str' if leaf else 'empty'), 'connectome_lock.read_lock', [BUILD + '/connectome.lock'], {},
            tree(**{'build/connectome.lock': {'$generate': 'nested-lock', 'prefix': before, 'suffix': after, 'depth': depth, 'leaf': leaf}}), bare=True)
    # ---- contained_file ---------------------------------------------------------------
    for value in ('run.sh', 'sub', 'link.service', 'missing', '../outside/run.sh', 'out/run.sh', 'inside/nested.service', '', 1, None, ['run.sh'], 'loop-a', 'run.sh/', R + '/build/run.sh', R + '/alias/run.sh'):
        add('lock.contained.' + hashlib.sha256(json.dumps(value).encode()).hexdigest()[:8], 'connectome_lock.contained_file', [P(BUILD), value], {}, T)
    add('lock.contained.alias-build', 'connectome_lock.contained_file', [P(R + '/alias'), 'run.sh'], {}, T)
    # ---- lock predicates ---------------------------------------------------------------
    for index, value in enumerate(('x', '', 'a\tb', 'a\u007fb', 'a\u009fb', 'a\u00a0b', 'caf\u00e9', '\ud800', 0, None, True, [], {}, F('1.5'), ' ')):
        add('lock.text.%02d' % index, 'connectome_lock.text', [value])
        add('lock.external-name.%02d' % index, 'connectome_lock.external_name', [value])
    for index, value in enumerate(('a' * 64, 'a' * 63, 'A' * 64, 'a' * 64 + '\n', 'g' * 64, '\uff10' * 64, 0, None, [])):
        add('lock.digest.%02d' % index, 'connectome_lock.digest', [value])
        add('lock.digest40.%02d' % index, 'connectome_lock.digest', [value, 40])
        add('lock.digest40kw.%02d' % index, 'connectome_lock.digest', [value], {'size': 40})
    add('lock.digest40.exact', 'connectome_lock.digest', ['0' * 40, 40])
    for index, value in enumerate(('1.2.3', 'v1.2.3', '0.0.0', '01.2.3', '1.02.3', '1.2', J('1.2', '.3.4'), '1.2.3-rc.1', '1.2.3-', '1.2.3+build', '1.2.3+', '1.2.3-a+b.c', 'V1.2.3', '1.2.3\n', '\u0661.2.3', ' 1.2.3', 1, None, '', '1.2.3-rc..1', '1.2.3-\u00e9')):
        add('lock.version.%02d' % index, 'connectome_lock.version', [value])
    for index, value in enumerate(('alpha', '_a', 'a.b-c_d', '-a', '.a', 'a b', 'a/b', 'A1', '\u00e9', 'a\n')):
        add('lock.external-name.x%02d' % index, 'connectome_lock.external_name', [value])
    for index, value in enumerate(('/fixture/bin/tool', '/', '//fixture/tool', '///fixture/tool', '/fixture//tool', '/fixture/./tool', '/fixture/../tool', '/fixture/tool/', 'fixture/tool',
                                   '/fixture/${X}/tool', '/fixture/$X/tool', '/fixture/..tool', '/fixture/tool..', '/fixture/a/..', '/.', '/..', '', 1, None, '/fixture/t\u0001ool', '/fixture/caf\u00e9')):
        add('lock.external-command.%02d' % index, 'connectome_lock.external_command', [value])
    for index, (condition, message) in enumerate(((True, 'm'), (False, 'refused'), (0, 'zero'), (1, 'one'), ('', 'empty'), ('x', 'text'), ([], 'list'), ([0], 'list'), ({}, 'dict'), ({'a': 1}, 'dict'), (None, 'none'), (F('0.0'), 'float'), (F('-0.0'), 'float'), (F('0.5'), 'float'))):
        add('lock.require.%02d' % index, 'connectome_lock.require', [condition, message])
        add('inputs.need.%02d' % index, 'connectome_inputs.need', [condition, message])
    add('inputs.need.default.false', 'connectome_inputs.need', [False])
    add('inputs.need.default.true', 'connectome_inputs.need', [True])
    # ---- row 1: credential shapes --------------------------------------------------------
    A12, A30 = 'a' * 12, 'A1_-' * 8
    shapes = {
        'gho': J('gh', 'o_', A12), 'ghp': J('gh', 'p_', A12), 'ghr': J('gh', 'r_', A12), 'ghs': J('gh', 's_', A12), 'ghu': J('gh', 'u_', A12), 'gha': J('gh', 'a_', A12),
        'ghp.11': J('gh', 'p_', 'a' * 11), 'pat': J('github', '_pat_', A12), 'glpat.dash': J('gl', 'pat-', A12), 'glpat.under': J('gl', 'pat_', A12), 'glpat.none': J('gl', 'pat', A12),
        'xoxb': J('xo', 'xb-', A12), 'xoxa': J('xo', 'xa-', A12), 'xoxp': J('xo', 'xp-', A12), 'xoxr': J('xo', 'xr-', A12), 'xoxs': J('xo', 'xs-', A12), 'xoxc': J('xo', 'xc-', A12),
        'sk': J('s', 'k-', A12), 'sk.11': J('s', 'k-', 'a' * 11), 'sk.embedded': J('ta', 'sk-', A12), 'sk.upper': J('S', 'K-', A12),
        'vt': J('hv', 's.', 'a'), 'vt.bare': J('hv', 's.'), 'vt.word-before': J('xhv', 's.', 'a'), 'vt.punct-before': J('-hv', 's.', 'a'), 'vt.unicode-before': J('\u00e9hv', 's.', 'a'),
        'vt.digit-before': J('\u0661hv', 's.', 'a'),
        'bot.8': J('12345678', ':', A30), 'bot.10': J('1234567890', ':', A30), 'bot.7': J('1234567', ':', A30), 'bot.11': J('12345678901', ':', A30), 'bot.29': J('12345678', ':', 'a' * 29),
        'bot.word-before': J('x12345678', ':', A30), 'bot.fullwidth': J('\uff11' * 8, ':', A30),
        'key': J('-----', 'BEGIN ', 'PRIVATE KEY', '-----'), 'key.rsa': J('-----', 'BEGIN RSA ', 'PRIVATE KEY', '-----'), 'key.newline': J('-----', 'BEGIN \n', 'PRIVATE KEY', '-----'),
        'key.cr': J('-----', 'BEGIN \r', 'PRIVATE KEY', '-----'), 'key.ls': J('-----', 'BEGIN \u2028', 'PRIVATE KEY', '-----'), 'key.public': J('-----', 'BEGIN PUBLIC KEY', '-----'),
        'jwt': J('ey', 'J', 'a.b.c'), 'jwt.two': J('ey', 'J', 'a.b'), 'jwt.unicode': J('ey', 'J', '\u00e9.\u0661.\u4e2d'), 'jwt.dash': J('ey', 'J', '-.-.-'), 'jwt.word-before': J('xey', 'J', 'a.b.c'),
        'jwt.empty-part': J('ey', 'J', '.b.c'), 'jwt.space-part': J('ey', 'J', 'a. .c'),
        'assign.token': J('TOK', 'EN', '=x'), 'assign.lower': J('tok', 'en', ': x'), 'assign.prefixed': J('MY_API_', 'KEY', ' = x'), 'assign.password': J('pass', 'word', ':x'),
        'assign.secret': J('SEC', 'RET', '=x'), 'assign.no-value': J('TOK', 'EN', '='), 'assign.space-value': J('TOK', 'EN', '= '), 'assign.word-before': J('1TOK', 'EN', '=x'),
        'assign.hyphen-before': J('a-TOK', 'EN', '=x'), 'assign.long-s': J('\u017fec', 'ret', '=x'), 'assign.kelvin': J('TO', '\u212aEN', '=x'), 'assign.dotted-i': J('AP\u0130_', 'KEY', '=x'),
        'assign.dotless-i': J('ap\u0131_', 'key', '=x'), 'assign.class-kelvin': J('\u212a_TOK', 'EN', '=x'), 'assign.nbsp': J('TOK', 'EN', '\u00a0=\u00a0x'), 'assign.fs': J('TOK', 'EN', '\u001c=x'),
        'assign.bom': J('TOK', 'EN', '\ufeff=x'), 'assign.value-nel': J('TOK', 'EN', '=\u0085'), 'assign.tokens': J('TOK', 'ENS', '=x'), 'assign.other-letter': J('\u00e9TOK', 'EN', '=x'),
        'assign.underscore-before': J('_TOK', 'EN', '=x'), 'assign.sharp-s': J('PA\u00df', 'WORD', '=x'),
        'url.userinfo': J('https://user', ':pw', '@example.invalid/x'), 'url.empty-userinfo': J('x://', ':', '@'), 'url.slash-first': J('x://a/b', ':c', '@d'), 'url.space': J('x://a b', ':c', '@d'),
        'url.nel': J('x://a\u0085b', ':c', '@d'), 'url.no-scheme-slashes': J('a', ':b', '@c'), 'query.token': J('?to', 'ken', '='), 'query.amp': J('&pass', 'word', '=1'), 'query.secret': J('?SEC', 'RET', '='),
        'query.api-key': J('&api_', 'key', '='), 'query.apikey': J('?api', 'key', '='), 'query.kelvin': J('?to', '\u212aen', '='), 'query.no-equals': J('?to', 'ken'),
        'store.vault': J('va', 'ult/', 'app/x'), 'store.secret': J('sec', 'ret/', 'app/x/y-z'), 'store.upper': J('VA', 'ULT/', 'App/x'), 'store.digit-first': J('va', 'ult/', '1pp/x'),
        'store.short': J('va', 'ult/', 'a/x'), 'store.unicode': J('va', 'ult/', 'a\u00e9/\u4e2d'), 'store.long-s': J('\u017fec', 'ret/', 'app/x'), 'store.dotless-first': J('va', 'ult/', '\u0131pp/x'),
        'store.no-tail': J('va', 'ult/', 'app/'), 'clean': 'nothing to see', 'clean.empty': '', 'clean.words': 'token password secret api_key vault',
    }
    for name, value in shapes.items():
        add('lock.credential.' + name, 'connectome_lock.credential_shaped', [value])
    for index, value in enumerate((0, None, [], {})):
        add('lock.credential.type.%d' % index, 'connectome_lock.credential_shaped', [value])
    # ---- row 3: repository ----------------------------------------------------------------
    repos = {
        'path': '/fixture/repo', 'path.root': '/', 'path.double': '//fixture/repo', 'path.space': '/fixture/my repo', 'path.query': '/fixture/repo?x', 'path.fragment': '/fixture/repo#x',
        'path.backslash': '/fixture\\repo', 'path.encoded-query': '/fixture/repo%3Fx', 'path.encoded-control': '/fixture/repo%0a', 'path.encoded-nul': '/fixture/repo%00',
        'path.encoded-credential': J('/fixture/', 's%6b-', 'a' * 12), 'path.credential': J('/fixture/', 'sk', '-', 'a' * 12), 'path.encoded-bom': '/fixture/%EF%BB%BFrepo',
        'path.bad-escape': '/fixture/%zz/%4', 'path.credential-raw-only': J('/fixture/TOK', 'EN', '=%20'), 'path.encoded-invalid-utf8': '/fixture/%ff%fe',
        'https': 'https://example.invalid/org/repo.git', 'https.upper': 'HTTPS://EXAMPLE.invalid/org/repo', 'https.port': 'https://example.invalid:8443/repo', 'https.port.zero': 'https://example.invalid:0/repo',
        'https.port.max': 'https://example.invalid:65535/repo', 'https.port.over': 'https://example.invalid:65536/repo', 'https.port.text': 'https://example.invalid:https/repo',
        'https.port.empty': 'https://example.invalid:/repo', 'https.port.leading-zero': 'https://example.invalid:00443/repo', 'https.port.fullwidth': 'https://example.invalid:\uff14\uff14\uff13/repo',
        'https.port.superscript': 'https://example.invalid:\u00b2/repo', 'https.port.huge': 'https://example.invalid:' + '9' * 30 + '/repo',
        'https.no-path': 'https://example.invalid', 'https.root-path': 'https://example.invalid/', 'https.no-host': 'https:///repo', 'https.user': 'https://user@example.invalid/repo',
        'https.empty-user': 'https://@example.invalid/repo', 'https.password': J('https://user', ':pw', '@example.invalid/repo'), 'https.query': 'https://example.invalid/repo?ref=main',
        'https.fragment': 'https://example.invalid/repo#main', 'https.space': 'https://example.invalid/my repo', 'https.encoded-space': 'https://example.invalid/my%20repo',
        'https.nbsp': 'https://example.invalid/my\u00a0repo', 'https.unicode-host': 'https://\u00e9xample.invalid/repo', 'https.fullwidth-slash-host': 'https://example\uff0finvalid/repo',
        'https.fullwidth-colon-host': 'https://example.invalid\uff1a22/repo', 'https.bracket-open': 'https://[example.invalid/repo', 'https.bracket-word': 'https://[example]/repo',
        'https.bracket-v6': J('https://[', ':', ':1]/repo'), 'https.bracket-v6-port': J('https://[', ':', ':1]:443/repo'), 'https.bracket-v6-bad-port': J('https://[', ':', ':1]:x/repo'),
        'https.bracket-v4': J('https://[127', '.0.0', '.1]/repo'), 'https.bracket-future': 'https://[v1.x]/repo', 'https.bracket-future-bad': 'https://[vz.x]/repo',
        'https.bracket-zone': J('https://[fe80', ':', ':1%25eth0]/repo'), 'https.bracket-inner': J('https://x[', ':', ':1]y/repo'), 'https.two-colons': 'https://example.invalid:1:2/repo',
        'https.no-slashes': 'https:example.invalid/repo', 'https.single-slash': 'https:/example.invalid/repo', 'https.tab': 'https://example.invalid/re\tpo', 'https.encoded-tab': 'https://example.invalid/re%09po',
        'https.leading-space': ' https://example.invalid/repo', 'https.encoded-hash': 'https://example.invalid/repo%23x', 'https.encoded-backslash': 'https://example.invalid/repo%5Cx',
        'ssh': 'ssh://git@example.invalid/org/repo.git', 'ssh.port': 'ssh://git@example.invalid:22/org/repo', 'ssh.no-user': 'ssh://example.invalid/org/repo', 'ssh.user.dash': 'ssh://-oProxy@example.invalid/repo',
        'ssh.user.encoded-dash': 'ssh://%2DoProxy@example.invalid/repo', 'ssh.user.dotted': 'ssh://a.b_c-d@example.invalid/repo', 'ssh.user.empty': 'ssh://@example.invalid/repo',
        'ssh.user.space': 'ssh://a%20b@example.invalid/repo', 'ssh.user.unicode': 'ssh://\u00e9@example.invalid/repo', 'ssh.host.dash': 'ssh://git@-oProxyCommand/repo',
        'ssh.host.encoded-dash': 'ssh://git@%2DoProxyCommand/repo', 'ssh.password': J('ssh://git', ':pw', '@example.invalid/repo'), 'ssh.two-at': J('ssh://a@b', '@example.invalid/repo'),
        'ssh.upper': 'SSH://git@example.invalid/repo',
        'scp': 'git@example.invalid:org/repo.git', 'scp.no-user': 'example.invalid:org/repo', 'scp.user.dash': '-o@example.invalid:repo', 'scp.host.dash': '-example.invalid:repo',
        'scp.host.underscore': 'exa_mple.invalid:repo', 'scp.double-colon': 'example.invalid::repo', 'scp.empty-path': 'example.invalid:', 'scp.newline-path': 'example.invalid:a%0ab',
        'scp.uri-inside': 'example.invalid:a://b', 'scp.encoded-uri': 'example.invalid:a%3A//b', 'scp.space': 'example.invalid:my repo', 'scp.unicode-path': 'example.invalid:caf\u00e9',
        'scp.encoded-bom-path': 'example.invalid:%EF%BB%BF', 'scp.ls-path': 'example.invalid:a\u2028b', 'scp.nel-path': 'example.invalid:a\u0085b',
        'prefix.http': 'http://example.invalid/repo', 'prefix.git': 'git://example.invalid/repo', 'prefix.file': 'file:///fixture/repo', 'prefix.ftp': 'ftp://example.invalid/repo',
        'prefix.ext': 'ext::sh -c x', 'prefix.ext-plain': 'ext:helper', 'prefix.upper': 'GIT:example', 'prefix.long-s': '\u017f\u017fh:example', 'prefix.dotless': 'g\u0131t:example',
        'prefix.encoded': '%67it:example', 'prefix.https-s': 'http\u017f:example', 'prefix.other': 'svn:example', 'prefix.other-uri': 'svn://example.invalid/repo',
        'relative': 'org/repo', 'relative.dot': './repo', 'word': 'repo', 'empty': '', 'control': 'example.invalid:re\u0001po', 'number': 1, 'none': None, 'list': ['/fixture/repo'],
    }
    for name, value in repos.items():
        add('lock.repository.' + name, 'connectome_lock.repository', [value])
    # ---- names_only -------------------------------------------------------------------------
    names = {
        'empty': {}, 'scalar': 1, 'none': None, 'list': ['a', ['b', {'c': 'd'}]], 'value-key': {'value': 1}, 'nested-value-key': {'a': [{'b': {'Secret': 1}}]},
        'mixed-case': {'Api-Key': 1}, 'api.token': {'api token': 1}, 'auth': {'AUTH_TOKEN': 1}, 'resolved': {'resolvedValue': 1}, 'not-exact': {'values2x': 1, 'tokens': 1, 'passwords': 1, 'mysecret': 1},
        'digits-stripped': {'v4a1l2u3e': 1}, 'unicode-stripped': {'v\u00e4alue': 1, 'val\u00fce': 1}, 'kelvin': {'to\u212aen': 1}, 'dotted-i': {'ap\u0130key': 1}, 'long-s': {'\u017fecret': 1},
        'control': ['a\u0000b'], 'control.key': {'a\u001fb': 1}, 'control.c1': ['a\u0080b'], 'allowed.nbsp': ['a\u00a0b'], 'credential': [J('s', 'k-', 'a' * 12)],
        'credential.key': {'$mapping': [[J('TOK', 'EN', '=x'), 1]]}, 'key-before-value': {'value': 'a\u0001'}, 'control-before-credential': [J('s', 'k-', 'a' * 12, '\u0001')], 'first-item': ['a\u0001', {'value': 1}],
        'float': [F('1.5'), F('inf')], 'bool': [True, False],
    }
    for name, value in names.items():
        add('lock.names.' + name, 'connectome_lock.names_only', [value])
    # ---- errors ---------------------------------------------------------------------------------
    import connectome_errors
    for code in sorted(connectome_errors.MESSAGES):
        add('errors.diagnostic.' + code, 'connectome_errors.diagnostic', [code])
    for code in sorted(connectome_errors.COOK_CODES):
        add('errors.diagnostic.cook.' + code, 'connectome_errors.diagnostic', ['E_COOK_FAILED', code])
    for name, args in {'unknown': ['E_UNKNOWN'], 'none': [None], 'number': [1], 'list': [['E_COOK_FAILED']], 'proto': ['toString'], 'constructor': ['constructor'], 'lower': ['e_cook_failed'],
                       'upstream.unknown': ['E_COOK_FAILED', 'E_SOMETHING'], 'upstream.engine-code': ['E_COOK_FAILED', 'E_LOCK_INVALID'], 'upstream.number': ['E_COOK_FAILED', 1],
                       'upstream.other-code': ['E_LOCK_INVALID', 'E_HOST_UNIT'], 'upstream.deploy': ['E_DEPLOY_FAILED', 'E_HOST_UNIT'], 'upstream.none': ['E_COOK_FAILED', None],
                       'upstream.newline': ['E_COOK_FAILED', 'E_HOST_UNIT\n']}.items():
        add('errors.diagnostic.' + name, 'connectome_errors.diagnostic', args)
    add('errors.diagnostic.keyword', 'connectome_errors.diagnostic', ['E_COOK_FAILED'], {'upstream': 'E_HOST_LAYOUT'})
    for name, args in {'default': ['internal detail'], 'code': ['internal', 'E_LOCK_INVALID'], 'cook': ['internal', 'E_COOK_FAILED', 'E_HOST_UNIT'], 'unknown': ['internal', 'E_NOPE'],
                       'cook.unknown': ['internal', 'E_COOK_FAILED', 'E_NOPE'], 'none-code': ['internal', None], 'upstream-ignored': ['internal', 'E_LOCK_INVALID', 'E_HOST_UNIT']}.items():
        add('errors.safe-error.' + name, 'connectome_errors.SafeError.public', args)
    cooks = {
        'plain': b'E_HOST_UNIT', 'line': b'error: E_HOST_LAYOUT: details\n', 'unknown': b'E_SOMETHING_ELSE', 'engine': b'E_LOCK_INVALID', 'first-known': b'E_NOPE E_PRIVATE_MODE E_HOST_UNIT',
        'prefix-letter': b'XE_HOST_UNIT', 'prefix-digit': b'1E_HOST_UNIT', 'prefix-underscore': b'_E_HOST_UNIT', 'suffix-letter': b'E_HOST_UNITx', 'suffix-digit': b'E_HOST_UNIT1',
        'suffix-underscore': b'E_HOST_UNIT_', 'suffix-lower': b'E_HOST_UNITs', 'punct': b'(E_HOST_UNIT)', 'colon': b':E_HOST_UNIT:', 'high-bytes': b'\xffE_HOST_UNIT\xfe', 'nul': b'\x00E_HOST_UNIT\x00',
        'newline': b'\nE_HOST_UNIT\n', 'empty': b'', 'only-prefix': b'E_', 'lower': b'e_host_unit', 'longer-known': b'E_HOST_DEPENDENCY_POLICY_FIELDS', 'longer-unknown': b'E_HOST_DEPENDENCY_POLICY',
        'two-adjacent': b'E_HOST_UNIT E_HOST_LAYOUT', 'utf8': 'caf\u00e9 E_HOST_UNIT'.encode(), 'crlf': b'E_HOST_UNIT\r\n', 'tab': b'\tE_HOST_UNIT\t', 'dash': b'-E_HOST_UNIT-',
        'double': b'E_E_HOST_UNIT', 'inner': b'E_HOST_E_HOST_UNIT',
    }
    for name, value in cooks.items():
        add('errors.cook-code.' + name, 'connectome_errors.cook_code', [B(value)])
    code = b'E_HOST_ENGINE_CHECKOUT'
    for shift in (0, 1, 21, 22, 23, 100, 127, 128, 129, 130, 150, 151):  # the code's first byte sits `shift` bytes before the first chunk boundary
        add('errors.stream.boundary.%03d' % shift, 'connectome_errors.cook_code_from_stream', [{'$stream': {'$generate': 'repeat', 'unit': '.', 'count': 65536 - shift, 'suffix': code.decode() + '.' * 300}, 'position': 0}])
        add('errors.stream.second.%03d' % shift, 'connectome_errors.cook_code_from_stream', [{'$stream': {'$generate': 'repeat', 'unit': '.', 'count': 131072 - shift, 'suffix': code.decode() + '.' * 300}, 'position': 0}])
        # an identifier character right before the code, inside or outside the 128-byte window
        add('errors.stream.glued.%03d' % shift, 'connectome_errors.cook_code_from_stream', [{'$stream': {'$generate': 'repeat', 'unit': '.', 'count': 65535 - shift, 'suffix': 'X' + code.decode() + '.' * 300}, 'position': 0}])
    add('errors.stream.empty', 'connectome_errors.cook_code_from_stream', [{'$stream': B(b''), 'position': 0}])
    add('errors.stream.position', 'connectome_errors.cook_code_from_stream', [{'$stream': B(b'E_HOST_UNIT then more'), 'position': 15}])
    add('errors.stream.unknown', 'connectome_errors.cook_code_from_stream', [{'$stream': B(b'E_NOTHING ' * 100), 'position': 0}])
    add('errors.stream.exact-chunk', 'connectome_errors.cook_code_from_stream', [{'$stream': {'$generate': 'repeat', 'unit': '.', 'count': 65536 - 11, 'suffix': 'E_HOST_UNIT'}, 'position': 0}])
    cooked = connectome_errors.diagnostic('E_COOK_FAILED', 'E_HOST_UNIT')
    plain = connectome_errors.diagnostic('E_LOCK_INVALID')
    envelopes = {
        'exact': json.dumps(plain), 'exact.cook': json.dumps(cooked), 'exact.bytes': B(json.dumps(plain).encode()), 'exact.spaced': '  ' + json.dumps(plain, indent=1) + '\n',
        'exact.reordered': json.dumps({'msg': plain['msg'], 'error_code': plain['error_code']}), 'exact.unicode-escaped': json.dumps(plain).replace('C', '\\u0043', 1),
        'exact.utf16': B(json.dumps(plain).encode('utf-16')), 'exact.utf8-bom': B(b'\xef\xbb\xbf' + json.dumps(plain).encode()), 'exact.bom-text': '\ufeff' + json.dumps(plain),
        'extra-key': json.dumps(dict(plain, extra=1)), 'wrong-msg': json.dumps(dict(plain, msg='changed')), 'missing-msg': json.dumps({'error_code': plain['error_code']}),
        'unknown-code': json.dumps({'error_code': 'E_NOPE', 'msg': 'x'}), 'deploy-failed': json.dumps(connectome_errors.diagnostic('E_DEPLOY_FAILED')),
        'cook.unknown-upstream': json.dumps(dict(cooked, cook_error_code='E_NOPE')), 'cook.upstream-without-suffix': json.dumps(dict(connectome_errors.diagnostic('E_COOK_FAILED'), cook_error_code='E_HOST_UNIT')),
        'cook.null-upstream': json.dumps(dict(connectome_errors.diagnostic('E_COOK_FAILED'), cook_error_code=None)), 'other.upstream': json.dumps(dict(plain, cook_error_code='E_HOST_UNIT')),
        'duplicate.last-wins': json.dumps(plain)[:-1] + ', "error_code": "E_LOCK_INVALID"}', 'duplicate.last-differs': json.dumps(plain)[:-1] + ', "error_code": "E_COOK_FAILED"}',
        'duplicate.first-differs': '{"error_code": "E_COOK_FAILED", ' + json.dumps(plain)[1:], 'nan': '{"error_code": NaN, "msg": "x"}', 'code.number': '{"error_code": 1, "msg": "x"}',
        'code.list': '{"error_code": [], "msg": "x"}', 'array': '[]', 'string': '"E_LOCK_INVALID"', 'null': 'null', 'empty': '', 'garbage': 'Traceback (most recent call last):', 'invalid-utf8': B(b'\xff\xfe\xfd'),
        'truncated': json.dumps(plain)[:-2], 'trailing': json.dumps(plain) + 'x', 'none': None, 'number': 5, 'list': [json.dumps(plain)], 'mapping': plain,
        'big-int': '{"error_code": ' + '1' * 4301 + '}', 'float-code': '{"error_code": 1.0}', 'true-msg': '{"error_code": "E_LOCK_INVALID", "msg": true}',
    }
    for name, value in envelopes.items():
        add('errors.adapter.' + name, 'connectome_errors.adapter_diagnostic', [value])
    add('errors.adapter.deep', 'connectome_errors.adapter_diagnostic', [{'$generate': 'nested', 'open': '[', 'close': ']', 'leaf': '', 'depth': 20000}], bare=True)
    add('errors.adapter.depth.9997', 'connectome_errors.adapter_diagnostic', [{'$generate': 'nested', 'open': '[', 'close': ']', 'leaf': '', 'depth': 9997}], bare=True)
    # ---- inputs: data paths and flags ------------------------------------------------------------
    for index, value in enumerate(('/fixture/state/data', '/', '//fixture/data', '///fixture/data', '/fixture//data', '/fixture/./data', '/fixture/../data', '/fixture/data/', 'fixture/data', '',
                                   '/fixture/da\u0000ta', '/fixture/da\u0085ta', '/fixture/da\u007fta', '/fixture/..data', '/fixture/data..', '/fixture/.', '/..', '/fixture/caf\u00e9', '/fixture/da ta', 1, None, ['/x'])):
        add('inputs.data-path.%02d' % index, 'connectome_inputs.external_data_path', [value])
    for index, value in enumerate(('--data', '-d', '--data-dir', '--data_dir', '-D1', '--', '-', '---data', '--1data', '-1', 'data', '', '--data=', '--data dir', '--data\n', '--d\u00e9', '-e', '--eval', '-p',
                                   '--print', '-c', '-r', '--require', '--import', '--loader', '--experimental-loader', '-x', '-m', '--requirex', '--import-map', '--loader2', '--experimental-loader-x',
                                   '--Require', '-E', '-M', '--evaluate', '--experimental', 1, None, ['--data'], True)):
        add('inputs.data-flag.%02d' % index, 'connectome_inputs.external_data_flag', [value])
    arg = data_arg()
    eda = {
        'valid': [arg], 'valid.empty': [], 'empty-mapping': {}, 'empty-string': '', 'valid.no-flag': [edit(arg, ['flag'], DROP)], 'valid.two': [arg, dict(arg, server='other')], 'not-list': {'server': 'example'}, 'none': None, 'string': 'x',
        'item.not-mapping': ['example'], 'item.extra': [dict(arg, extra=1)], 'server.number': [dict(arg, server=1)], 'server.dash': [dict(arg, server='-x')], 'server.newline': [dict(arg, server='example\n')],
        'server.empty': [dict(arg, server='')], 'command.relative': [dict(arg, command='bin/tool')], 'command.root': [dict(arg, command='/')], 'command.dotdot': [dict(arg, command='/fixture/../tool')],
        'args.string': [dict(arg, args='--data')], 'args.number-item': [dict(arg, args=['--data', 1])], 'args.empty': [dict(arg, args=[])], 'path.relative': [dict(arg, path='state/data')],
        'path.double': [dict(arg, path='//fixture/state/data')], 'path.control': [dict(arg, path='/fixture/state/da\u0001ta')], 'kind.other': [dict(arg, kind='socket')], 'kind.number': [dict(arg, kind=1)],
        'kind.list': [dict(arg, kind=['file'])], 'kind.dir': [dict(arg, kind='dir')], 'flag.denied': [dict(arg, flag='--require')], 'flag.prefix': [dict(arg, flag='--import-x')],
        'flag.grammar': [dict(arg, flag='data')], 'flag.number': [dict(arg, flag=1)], 'flag.null': [dict(arg, flag=None)], 'duplicate': [arg, arg], 'duplicate.second-kind': [arg, dict(arg, kind='dir')],
        'second-bad': [arg, dict(arg, server='other', kind='x')], 'first-bad-second-dup': [dict(arg, kind='x'), arg],
    }
    for key in ('server', 'command', 'args', 'path', 'kind'):
        eda['missing.' + key] = [edit(arg, [key], DROP)]
    for name, value in eda.items():
        add('inputs.data-args.' + name, 'connectome_inputs.validate_external_data_args', [value])
    # ---- inputs.validate --------------------------------------------------------------------------
    def iv(ident, value):
        add('inputs.validate.' + ident, 'connectome_inputs.validate', [value])
    iv('valid', config())
    iv('valid.empty', {})
    iv('valid.start', {'activation': 'start'})
    for name, value in {'list': [], 'none': None, 'string': 'config', 'number': 1}.items():
        iv('config.' + name, value)
    iv('data-args.invalid', config(external_data_args=[{}]))
    iv('data-args.null', config(external_data_args=None))
    iv('data-args.before-activation', {'external_data_args': 'x', 'activation': 'stop'})
    for name, value in {'other': 'stop', 'number': 1, 'null': None, 'list': ['start'], 'upper': 'START', 'newline': 'start\n'}.items():
        iv('activation.' + name, config(activation=value))
    ex = {'scope': 'user', 'unit': 'other.service'}
    for name, value in {'not-list': {}, 'null': None, 'string': 'other.service', 'item.not-mapping': ['other.service'], 'item.extra': [dict(ex, extra=1)], 'item.missing-unit': [{'scope': 'user'}],
                        'scope.other': [dict(ex, scope='global')], 'scope.number': [dict(ex, scope=1)], 'unit.number': [dict(ex, unit=1)], 'unit.no-suffix': [dict(ex, unit='other')],
                        'unit.slash': [dict(ex, unit='a/b.service')], 'unit.dot-first': [dict(ex, unit='.hidden.service')], 'unit.dash-first': [dict(ex, unit='-x.service')],
                        'unit.template': [dict(ex, unit=J('name@', 'instance.service'))], 'unit.only-suffix': [dict(ex, unit='.service')], 'unit.newline': [dict(ex, unit='other.service\n')],
                        'unit.space': [dict(ex, unit='my unit.service')], 'duplicate': [ex, dict(ex)], 'same-unit-other-scope': [ex, dict(ex, scope='system')], 'empty': []}.items():
        iv('exclusive.' + name, config(exclusive_with=value))
    rf = {'path': '/fixture/operator/recipe.json', 'sha256': 'a' * 64}
    for name, value in {'not-mapping': '/fixture/recipe.json', 'null': None, 'extra': dict(rf, mode=384), 'missing-sha': {'path': rf['path']}, 'path.number': dict(rf, path=1), 'path.relative': dict(rf, path='recipe.json'),
                        'path.brace-open': dict(rf, path='/fixture/{x/recipe.json'), 'path.brace-close': dict(rf, path='/fixture/x}/recipe.json'), 'path.template': dict(rf, path='/fixture/{{ x }}/recipe.json'),
                        'path.control': dict(rf, path='/fixture/re\u0001cipe.json'), 'path.c1': dict(rf, path='/fixture/re\u009bcipe.json'), 'path.double-slash': dict(rf, path='/fixture//recipe.json'),
                        'path.dot': dict(rf, path='/fixture/./recipe.json'), 'path.dotdot': dict(rf, path='/fixture/x/../recipe.json'), 'path.trailing-slash': dict(rf, path='/fixture/recipe.json/'),
                        'path.leading-double': dict(rf, path='//fixture/recipe.json'), 'path.leading-triple': dict(rf, path='///fixture/recipe.json'), 'path.root': dict(rf, path='/'),
                        'path.unicode': dict(rf, path='/fixture/caf\u00e9.json'), 'sha.short': dict(rf, sha256='a' * 63), 'sha.upper': dict(rf, sha256='A' * 64), 'sha.number': dict(rf, sha256=1),
                        'sha.newline': dict(rf, sha256='a' * 64 + '\n')}.items():
        iv('recipe-file.' + name, config(recipe_file=value))
    sf = {'secret': 'EXAMPLE_NAME', 'env': 'EXAMPLE_FILE'}
    for name, value in {'not-list': {}, 'null': None, 'item.not-mapping': ['EXAMPLE_NAME'], 'item.extra': [dict(sf, mode=384)], 'item.missing-env': [{'secret': 'EXAMPLE_NAME'}],
                        'secret.lower': [dict(sf, secret='example')], 'secret.number': [dict(sf, secret=1)], 'env.lower': [dict(sf, env='example_file')], 'env.digit-first': [dict(sf, env='1FILE')],
                        'env.newline': [dict(sf, env='EXAMPLE_FILE\n')], 'env.number': [dict(sf, env=1)], 'duplicate-env': [sf, dict(sf, secret='OTHER_NAME')], 'same-secret-other-env': [sf, dict(sf, env='OTHER_FILE')],
                        'empty': []}.items():
        iv('secret-files.' + name, config(secret_files=value))
    for reserved in ('DATA_DIR', 'WORKSPACE_DIR', 'PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR'):
        iv('secret-files.reserved.' + reserved, config(secret_files=[dict(sf, env=reserved)]))
    iv('secret-files.reserved-as-secret', config(secret_files=[dict(sf, secret='PATH')]))
    iv('secret-files.not-reserved', config(secret_files=[dict(sf, env='LC_CTYPE')]))
    # ---- inputs: verify_bytes, read_file, collect, payloads, installed ------------------------------
    def vb(ident, content, record=None):
        content = content if isinstance(content, bytes) else content.encode()
        add('inputs.verify.' + ident, 'connectome_inputs.verify_bytes', [B(content), {'sha256': hashlib.sha256(content).hexdigest()} if record is None else record])
    vb('valid', RECIPE)
    vb('valid.empty-object', '{}')
    vb('valid.nested', '{"a": [1, 2.5, true, null, {"b": "c"}], "d": -0.0, "e": 1e5}')
    vb('digest-differs', RECIPE, {'sha256': 'a' * 64})
    vb('digest-number', RECIPE, {'sha256': 1})
    vb('record-missing-key', RECIPE, {})
    vb('record-list', RECIPE, [])
    vb('record-none', RECIPE, None)
    vb('record-extra', RECIPE, {'sha256': RECIPE_SHA, 'path': 1})
    for name, text in {'array': '[]', 'string': '"x"', 'number': '1', 'null': 'null', 'empty': '', 'duplicate': '{"a":1,"a":1}', 'duplicate.nested': '{"a":{"b":1,"b":2}}', 'nan': '{"a":NaN}',
                       'infinity': '{"a":[Infinity]}', 'negative-infinity': '{"a":-Infinity}', 'overflow-float': '{"a":1E400}', 'trailing': '{} x', 'trailing-comma': '{"a":1,}', 'single-quotes': "{'a':1}",
                       'comment': '{} // x', 'control': '{"a":"\n"}', 'big-int.4300': '{"a":' + '9' * 4300 + '}', 'big-int.4301': '{"a":' + '9' * 4301 + '}', 'lone-surrogate-escape': '{"a":"\\udc00"}',
                       'integer-like-keys': '{"2":1,"1":2,"10":3}', 'proto-key': '{"__proto__":1,"constructor":2}', 'empty-key': '{"":1}', 'unicode-key': '{"\u00e9":1,"\\u00e9x":2}',
                       'same-key-escaped': '{"a":1,"\\u0061":2}', 'whitespace-nbsp': '{}\u00a0', 'leading-zero': '{"a":01}', 'hex': '{"a":0x1}', 'plus': '{"a":+1}', 'dot': '{"a":1.}'}.items():
        vb(name, text)
    for name, content in {'utf8-bom': b'\xef\xbb\xbf{}', 'utf16-bom': '{"a":1}'.encode('utf-16'), 'utf16-le': '{"a":1}'.encode('utf-16-le'), 'utf16-be': '{"a":1}'.encode('utf-16-be'),
                          'utf32-bom': '{"a":1}'.encode('utf-32'), 'utf32-le': '{"a":1}'.encode('utf-32-le'), 'utf32-be': '{"a":1}'.encode('utf-32-be'), 'utf16-odd': '{"a":1}'.encode('utf-16-le') + b'\x00',
                          'utf16-short': '{}'.encode('utf-16-le'), 'invalid-utf8': b'{"a":"\xff"}', 'overlong': b'{"a":"\xc0\x80"}', 'encoded-surrogate': b'{"a":"\xed\xa0\x80"}',
                          'truncated-utf8': b'{"a":"\xe2\x82"}', 'latin1': '{"a":"caf\u00e9"}'.encode('latin-1'), 'nul-prefix': b'\x00{}', 'two-bytes': b'{}', 'utf32-bad': b'\x00\x11\x00\x00' * 2,
                          'utf8-4byte': '{"a":"\U0001f600"}'.encode()}.items():
        vb('bytes.' + name, content)
    for depth in (9994, 9995, 9996, 9997):
        add('inputs.verify.depth.object.%d' % depth, 'connectome_inputs.verify_bytes',
            [{'$generate': 'nested', 'open': '{"a":', 'close': '}', 'leaf': '1', 'depth': depth}, {'sha256': {'$sha256-of': {'$generate': 'nested', 'open': '{"a":', 'close': '}', 'leaf': '1', 'depth': depth}}}], bare=True)
    for depth in (9995, 9996, 9997, 9998):
        wrapped = {'$generate': 'repeat', 'prefix': '{"a":' + '[' * (depth - 1), 'unit': ']', 'count': depth - 1, 'suffix': '}'}
        add('inputs.verify.depth.array.%d' % depth, 'connectome_inputs.verify_bytes', [wrapped, {'sha256': {'$sha256-of': wrapped}}], bare=True)
    record = {'path': R + '/operator/recipe.json', 'sha256': RECIPE_SHA}

    def rf_(ident, path, rec=None, files=T):
        add('inputs.read.' + ident, 'connectome_inputs.read_file', [path, record if rec is None else rec], {}, files)
    rf_('valid', R + '/operator/recipe.json')
    rf_('valid.path-object', P(R + '/operator/recipe.json'))
    rf_('valid.unnormalized', R + '/operator/./recipe.json')
    rf_('dotdot', R + '/operator/../operator/recipe.json')
    rf_('link', R + '/operator/link.json')
    rf_('via-dir-link', R + '/operator/via/recipe.json')
    rf_('directory', R + '/operator/dir.json')
    rf_('missing', R + '/operator/missing.json')
    rf_('missing-parent', R + '/nowhere/recipe.json')
    rf_('through-file', R + '/operator/recipe.json/x')
    rf_('closed', R + '/closed/recipe.json')
    rf_('loop', BUILD + '/loop-a')
    rf_('dangling', BUILD + '/dangling')
    rf_('relative', 'operator/recipe.json')
    rf_('digest-differs', R + '/operator/recipe.json', {'path': 'x', 'sha256': 'a' * 64})
    rf_('array', R + '/operator/array.json', {'sha256': hashlib.sha256(b'[]').hexdigest()})
    rf_('record-list', R + '/operator/recipe.json', [])
    rf_('unreadable', R + '/operator/recipe.json', files=tree(**{'operator/recipe.json': {'file': b64(RECIPE), 'mode': 0o000}}))
    rf_('trailing-slash', R + '/operator/recipe.json/')

    def co(ident, value, files=T):
        add('inputs.collect.' + ident, 'connectome_inputs.collect', [value], {}, files)
        add('inputs.payloads.standalone.' + ident, 'connectome_inputs.payloads', [value], {}, files)
    co('none', {})
    co('valid', recipe_config())
    co('link', recipe_config(R + '/operator/link.json'))
    co('missing', recipe_config(R + '/operator/missing.json'))
    co('digest-differs', {'recipe_file': {'path': R + '/operator/recipe.json', 'sha256': 'a' * 64}})
    co('invalid-config', {'recipe_file': {'path': 'relative', 'sha256': RECIPE_SHA}})
    co('not-mapping', [])
    co('array', {'recipe_file': {'path': R + '/operator/array.json', 'sha256': hashlib.sha256(b'[]').hexdigest()}})
    cfg = {'recipe_file': {'path': '/fixture/operator/recipe.json', 'sha256': RECIPE_SHA}}
    supplied = {'cook-recipe.json': b64(RECIPE)}
    for name, (conf, transport) in {
            'valid': (cfg, supplied), 'valid.none-expected': ({}, {}), 'unexpected': ({}, supplied), 'missing': (cfg, {}), 'extra': (cfg, dict(supplied, other='')), 'renamed': (cfg, {'recipe.json': b64(RECIPE)}),
            'list': (cfg, [b64(RECIPE)]), 'string': (cfg, b64(RECIPE)), 'number': (cfg, 0), 'false': (cfg, False), 'digest-differs': (dict(recipe_file=dict(cfg['recipe_file'], sha256='a' * 64)), supplied),
            'not-json': (dict(recipe_file={'path': '/fixture/r.json', 'sha256': hashlib.sha256(b'x').hexdigest()}), {'cook-recipe.json': b64(b'x')}),
            'base64.space': (cfg, {'cook-recipe.json': b64(RECIPE) + ' '}), 'base64.newline': (cfg, {'cook-recipe.json': b64(RECIPE)[:8] + '\n' + b64(RECIPE)[8:]}),
            'base64.junk': (cfg, {'cook-recipe.json': 'not strict base64!'}), 'base64.urlsafe': (cfg, {'cook-recipe.json': b64(RECIPE).replace('+', '-').replace('/', '_') + '-_'}),
            'base64.no-padding': (cfg, {'cook-recipe.json': b64(RECIPE).rstrip('=')}), 'base64.extra-padding': (cfg, {'cook-recipe.json': b64(RECIPE) + '='}),
            'base64.leading-padding': (cfg, {'cook-recipe.json': '=' + b64(RECIPE)}), 'base64.inner-padding': (cfg, {'cook-recipe.json': b64(RECIPE)[:4] + '=' + b64(RECIPE)[4:]}),
            'base64.one-char': (cfg, {'cook-recipe.json': 'e'}), 'base64.non-ascii': (cfg, {'cook-recipe.json': 'e\u00e9=='}), 'base64.number': (cfg, {'cook-recipe.json': 1}),
            'base64.null': (cfg, {'cook-recipe.json': None}), 'base64.list': (cfg, {'cook-recipe.json': []}), 'base64.empty': (cfg, {'cook-recipe.json': ''}),
            'base64.data-after-padding': (cfg, {'cook-recipe.json': 'e30=e30='}), 'invalid-config': ({'activation': 'x'}, {}), 'config-list': ([], {}),
    }.items():
        add('inputs.payloads.' + name, 'connectome_inputs.payloads', [conf, transport])
    add('inputs.payloads.keyword', 'connectome_inputs.payloads', [cfg], {'supplied': supplied})
    add('inputs.payloads.bytes-transport', 'connectome_inputs.payloads', [cfg, {'cook-recipe.json': B(b64(RECIPE).encode())}])

    def ins(ident, files, conf=None, build=BUILD):
        add('inputs.installed.' + ident, 'connectome_inputs.installed', [build, {'recipe_file': {'path': '/fixture/operator/recipe.json', 'sha256': RECIPE_SHA}} if conf is None else conf], {}, files)
    ins('valid', T)
    ins('valid.path-object', T, build=P(BUILD))
    ins('none', T, {})
    ins('mode.0644', tree(**{'build/cook-recipe.json': {'file': b64(RECIPE), 'mode': 0o644}}))
    ins('mode.0400', tree(**{'build/cook-recipe.json': {'file': b64(RECIPE), 'mode': 0o400}}))
    ins('mode.setuid', tree(**{'build/cook-recipe.json': {'file': b64(RECIPE), 'mode': 0o4600}}))
    ins('missing', tree(**{'build/cook-recipe.json': {'dir': 0o700}}))
    ins('link', tree(**{'build/cook-recipe.json': {'link': R + '/operator/recipe.json'}}))
    ins('digest-differs', tree(**{'build/cook-recipe.json': {'text': '{}', 'mode': 0o600}}))
    ins('invalid-config', T, {'activation': 1})
    ins('alias-build', T, build=R + '/alias')
    ins('missing-build', T, build=R + '/missing')
    hand_archive(add)
    hand_config(add)
    return rows.rows


FRAMEWORK = '@' + 'animalabs/agent-framework'
PIN = 'f' * 40


def G(*members):
    """A generated archive: `[name, text or None for a directory]` members (see `tgz` in ts_port.py)."""
    return {'$generate': 'tgz', 'members': [list(member) for member in members]}


def manifest(name='example-package', version='1.0.0', **more):
    return json.dumps(dict({'name': name, 'version': version}, **more))


def packed(name='example-package', version='1.0.0', **more):
    return G(['package', None], ['package/package.json', manifest(name, version, **more)], ['package/index.js', 'module.exports = 1;\n'])


def entry(spec, name='example-package', kind='artifact', file='example.tgz', **more):
    """A dependency declaration for the archive `spec`, stored in the tree as archives/<file>."""
    record = {'name': name, 'archive': R + '/archives/' + file, 'sha256': {'$sha256-of': spec}, 'kind': kind, 'version': '1.0.0'}
    record.update(more)
    return record


def hand_archive(add):
    base = packed()
    A = {'archives/example.tgz': base, 'archives/dir.tgz': {'dir': 0o755}, 'archives/link.tgz': {'link': 'example.tgz'}}

    def fn(ident, function, args, files=None):
        add('archive.' + ident, 'connectome_archive.' + function, args, {}, files)
    # ---- small predicates -------------------------------------------------------------------
    for index, value in enumerate(('example', 'example-package', '@scope/name', '@s/n', 'a.b_c-d', '0', 'Example', '@Scope/name', '@scope/Name', '_name', '.name', '-name', '@scope/.name',
                                   '@scope', '@scope/', 'scope/name', '@a/b/c', 'node_modules', 'favicon.ico', 'node_modules2', 'a' * 214, 'a' * 215, '@' + 'a' * 100 + '/' + 'b' * 112,
                                   '@' + 'a' * 100 + '/' + 'b' * 113, 'name\n', 'na me', 'caf\u00e9', '', 1, None, ['example'], 'a~b', 'a%20b', 'name!', '@/name')):
        fn('npm-name.%02d' % index, 'npm_name', [value])
    origins = {
        'plain': 'https://registry.example.invalid', 'slash': 'https://registry.example.invalid/', 'path': 'https://registry.example.invalid/npm', 'http': 'http://registry.example.invalid',
        'port': 'https://registry.example.invalid:8443', 'port.zero': 'https://registry.example.invalid:0', 'port.over': 'https://registry.example.invalid:65536', 'port.text': 'https://registry.example.invalid:x',
        'port.empty': 'https://registry.example.invalid:', 'user': 'https://user@registry.example.invalid', 'password': J('https://user', ':pw', '@registry.example.invalid'),
        'query': 'https://registry.example.invalid?x', 'empty-query': 'https://registry.example.invalid?', 'fragment': 'https://registry.example.invalid#x', 'percent': 'https://registry.example.invalid/%2F',
        'backslash': 'https://registry.example.invalid\\x', 'space': 'https://registry.example.invalid ', 'nbsp': 'https://registry.example.invalid\u00a0', 'upper': 'HTTPS://REGISTRY.EXAMPLE.invalid',
        'no-host': 'https://', 'no-slashes': 'https:registry.example.invalid', 'underscore-host': 'https://regis_try.example.invalid', 'dash-first-host': 'https://-registry.example.invalid',
        'unicode-host': 'https://caf\u00e9.example.invalid', 'kelvin-host': 'https://\u212aregistry.example.invalid', 'bracket-host': J('https://[', ':', ':1]'), 'bracket-word': 'https://[registry]',
        'credential': J('https://registry.example.invalid/', 'to', 'ken=x'), 'credential-host': J('https://hv', 's.example.invalid'), 'empty': '', 'number': 1, 'none': None, 'double-slash-path': 'https://registry.example.invalid//', 'ssh': 'ssh://registry.example.invalid',
        'two-ports': 'https://registry.example.invalid:1:2', 'trailing-dot': 'https://registry.example.invalid.', 'control': 'https://registry.example.invalid\u0001',
    }
    for name, value in origins.items():
        fn('registry-origin.' + name, 'registry_origin', [value])
    digest512 = base64.b64encode(hashlib.sha512(b'example').digest()).decode()
    for name, value in {'valid': 'sha512-' + digest512, 'sha256': 'sha256-' + digest512, 'upper': 'SHA512-' + digest512, 'short': 'sha512-' + base64.b64encode(b'x' * 63).decode(),
                        'long': 'sha512-' + base64.b64encode(b'x' * 65).decode(), 'no-padding': 'sha512-' + digest512.rstrip('='), 'extra-padding': 'sha512-' + digest512 + '=',
                        'noncanonical-bits': 'sha512-' + digest512[:-3] + chr(ord(digest512[-3]) + 1) + '==', 'urlsafe': 'sha512-' + digest512.replace('+', '-').replace('/', '_'),
                        'space': 'sha512-' + digest512 + ' ', 'newline': 'sha512-' + digest512 + '\n', 'empty-digest': 'sha512-', 'bare': digest512, 'non-ascii': 'sha512-' + digest512[:-4] + '\u00e9\u00e9==',
                        'empty': '', 'number': 1, 'none': None, 'two': 'sha512-' + digest512 + ' sha512-' + digest512}.items():
        fn('sha512-integrity.' + name, 'sha512_integrity', [value])
    for index, args in enumerate(([True, 'm'], [False, 'refused'], [0, 'zero'], ['x', 'text'], [[], 'list'], [False, 'coded', 'E_ARCHIVE_HASH'], [False, 'unknown code', 'E_NOPE'], [None, 'none', None],
                                  [False, 'lock', 'E_LOCK_INVALID'], [False, 'cook', 'E_COOK_FAILED'])):
        fn('need.%02d' % index, 'need', args)
    # ---- validate_dependency ---------------------------------------------------------------
    dep = {'name': 'example-package', 'archive': '/fixture/archives/example.tgz', 'sha256': 'e' * 64, 'kind': 'artifact', 'version': '1.0.0'}
    git = dict(dep, kind='git_commit', commit='a' * 40)
    registry = dict(dep, kind='registry_release', registry_url='https://registry.example.invalid', registry_integrity='sha512-' + digest512)
    deps = {
        'valid.artifact': dep, 'valid.git': git, 'valid.registry': registry, 'valid.scoped': dict(dep, name='@scope/example'), 'not-mapping': 'example-package', 'none': None, 'list': [dep],
        'extra': dict(dep, extra=1), 'name.upper': dict(dep, name='Example'), 'name.number': dict(dep, name=1), 'name.credential': dict(dep, name=J('s', 'k-', 'a' * 12)),
        'version.range': dict(dep, version='^1.0.0'), 'version.number': dict(dep, version=1), 'version.none': dict(dep, version=None), 'version.credential': dict(dep, version=J('1.0.0-', 's', 'k-', 'a' * 12)),
        'kind.other': dict(dep, kind='tarball'), 'kind.number': dict(dep, kind=1), 'kind.list': dict(dep, kind=['artifact']), 'kind.none': dict(dep, kind=None),
        'git.no-commit': dict(dep, kind='git_commit'), 'git.short': dict(git, commit='a' * 39), 'git.zero': dict(git, commit='0' * 40), 'git.upper': dict(git, commit='A' * 40), 'git.number': dict(git, commit=1),
        'artifact.commit': dict(dep, commit='a' * 40), 'artifact.commit-null': dict(dep, commit=None), 'registry.commit': dict(registry, commit='a' * 40),
        'registry.no-url': edit(registry, ['registry_url'], DROP), 'registry.no-integrity': edit(registry, ['registry_integrity'], DROP), 'registry.bad-url': dict(registry, registry_url='http://registry.example.invalid'),
        'registry.bad-integrity': dict(registry, registry_integrity='sha512-x'), 'artifact.registry-url': dict(dep, registry_url='https://registry.example.invalid'),
        'artifact.registry-integrity': dict(dep, registry_integrity='sha512-' + digest512), 'git.registry-url': dict(git, registry_url=None),
        'archive.relative': dict(dep, archive='archives/example.tgz'), 'archive.empty': dict(dep, archive=''), 'archive.number': dict(dep, archive=1), 'archive.query': dict(dep, archive='/fixture/a.tgz?x'),
        'archive.fragment': dict(dep, archive='/fixture/a.tgz#x'), 'archive.control': dict(dep, archive='/fixture/a\u0001.tgz'), 'archive.credential': dict(dep, archive=J('/fixture/', 's', 'k-', 'a' * 12)),
        'archive.dotdot': dict(dep, archive='/fixture/../a.tgz'), 'archive.double-slash': dict(dep, archive='//fixture/a.tgz'), 'sha.short': dict(dep, sha256='e' * 63), 'sha.upper': dict(dep, sha256='E' * 64),
        'sha.number': dict(dep, sha256=1), 'two-defects.identity-first': dict(dep, name='Example', kind='tarball'), 'two-defects.kind-first': dict(dep, kind='tarball', sha256='x'),
        'two-defects.archive-first': dict(dep, archive='relative', sha256='x'),
    }
    for key in ('name', 'archive', 'sha256', 'kind', 'version'):
        deps['missing.' + key] = edit(dep, [key], DROP)
    for name, value in deps.items():
        fn('validate-dependency.' + name, 'validate_dependency', [value])
    # ---- legacy_dependency, dependency_inputs ---------------------------------------------------
    plain = {'path': '/fixture/archives/framework.tgz', 'sha256': 'e' * 64}
    marked = dict(plain, registry_release=True, name=FRAMEWORK, version='1.2.3')
    legacy = {
        'valid.unmarked': plain, 'valid.marked': marked, 'not-mapping': '/fixture/a.tgz', 'none': None, 'missing-sha': {'path': plain['path']}, 'extra': dict(plain, extra=1),
        'marked.false': dict(marked, registry_release=False), 'marked.one': dict(marked, registry_release=1), 'marked.string': dict(marked, registry_release='true'), 'marked.null': dict(marked, registry_release=None),
        'marked.other-name': dict(marked, name='other'), 'marked.no-name': edit(marked, ['name'], DROP), 'marked.no-version': edit(marked, ['version'], DROP), 'marked.range': dict(marked, version='^1.2.3'),
        'marked.credential-version': dict(marked, version=J('1.2.3-', 's', 'k-', 'a' * 12)), 'unmarked.name': dict(plain, name=FRAMEWORK), 'unmarked.version': dict(plain, version='1.2.3'),
        'unmarked.empty-version': dict(plain, version=''), 'path.relative': dict(plain, path='framework.tgz'), 'path.number': dict(plain, path=1), 'sha.bad': dict(plain, sha256='x'),
        'marked.path.relative': dict(marked, path='framework.tgz'),
    }
    for name, value in legacy.items():
        fn('legacy.' + name, 'legacy_dependency', [{'cook': {'framework_archive': value}}, PIN])
    fn('legacy.pin.short', 'legacy_dependency', [{'cook': {'framework_archive': plain}}, 'f' * 39])
    fn('legacy.pin.zero', 'legacy_dependency', [{'cook': {'framework_archive': plain}}, '0' * 40])
    fn('legacy.pin.none', 'legacy_dependency', [{'cook': {'framework_archive': plain}}, None])
    fn('legacy.pin.none.marked', 'legacy_dependency', [{'cook': {'framework_archive': marked}}, None])
    for name, value in {'no-cook': {}, 'cook-list': {'cook': []}, 'cook-string': {'cook': 'x'}, 'cook-none': {'cook': None}, 'no-framework': {'cook': {}}, 'config-list': [], 'config-none': None}.items():
        fn('legacy.config.' + name, 'legacy_dependency', [value, PIN])
        fn('inputs.config.' + name, 'dependency_inputs', [value, PIN])
        fn('records-metadata.config.' + name, 'records_metadata', [value])
    other = dict(dep, name='other-package', archive='/fixture/archives/other.tgz')
    inputs_ = {
        'none': {'cook': {}}, 'empty': {'cook': {'host_dependencies': []}}, 'one': {'cook': {'host_dependencies': [dep]}}, 'two': {'cook': {'host_dependencies': [dep, other]}},
        'framework': {'cook': {'framework_archive': plain}}, 'framework-and-one': {'cook': {'framework_archive': plain, 'host_dependencies': [dep]}}, 'framework.marked': {'cook': {'framework_archive': marked}},
        'duplicate': {'cook': {'host_dependencies': [dep, dict(dep)]}}, 'duplicate.framework': {'cook': {'framework_archive': plain, 'host_dependencies': [dict(dep, name=FRAMEWORK)]}},
        'not-list': {'cook': {'host_dependencies': {}}}, 'null': {'cook': {'host_dependencies': None}}, 'string': {'cook': {'host_dependencies': 'x'}}, 'bad-item': {'cook': {'host_dependencies': [dep, dict(other, kind='x')]}},
        'bad-framework-first': {'cook': {'framework_archive': {}, 'host_dependencies': [{}]}}, 'not-list-before-framework': {'cook': {'framework_archive': {}, 'host_dependencies': 1}},
        'order-kept': {'cook': {'host_dependencies': [other, dep]}},
    }
    for name, value in inputs_.items():
        fn('inputs.' + name, 'dependency_inputs', [value, PIN])
        fn('records-metadata.' + name, 'records_metadata', [value])
    for name, value in {'marked': {'cook': {'framework_archive': marked}}, 'marked.one': {'cook': {'framework_archive': dict(marked, registry_release=1)}}, 'framework-list': {'cook': {'framework_archive': []}},
                        'framework-none': {'cook': {'framework_archive': None}}, 'cook-contains-name': {'cook': ['host_dependencies']}, 'cook-number': {'cook': 1},
                        'cook-string-contains': {'cook': 'no host_dependencies here'}}.items():
        fn('records-metadata.x.' + name, 'records_metadata', [value])
    # ---- provenance_record, validate_dependency_record ---------------------------------------------
    for name, value in {'artifact': dep, 'git': git, 'registry': registry, 'unknown-kind': dict(dep, kind='x'), 'no-kind': edit(dep, ['kind'], DROP), 'list': [], 'none': None, 'string': 'kind', 'kind-list': dict(dep, kind=[])}.items():
        fn('provenance-record.' + name, 'provenance_record', [value])
    import connectome_archive
    good = connectome_archive.provenance_record(dep)
    good_git = connectome_archive.provenance_record(git)
    good_registry = connectome_archive.provenance_record(registry)
    records = {
        'valid.artifact': good, 'valid.git': good_git, 'valid.registry': good_registry, 'not-mapping': 'x', 'none': None, 'list': [good], 'no-flag': edit(good, ['commit_verifiable'], DROP),
        'flag.one': dict(good, commit_verifiable=0), 'flag.wrong': dict(good, commit_verifiable=True), 'flag.git-false': dict(good_git, commit_verifiable=False), 'flag.null': dict(good, commit_verifiable=None),
        'no-payload': edit(good, ['payload'], DROP), 'payload.other': dict(good, payload='other'), 'payload.on-git': dict(good_git, payload=good['payload']),
        'comparison.missing': edit(good_registry, ['registry_comparison'], DROP), 'comparison.other': dict(good_registry, registry_comparison='compared'),
        'comparison.on-artifact': dict(good, registry_comparison=good_registry['registry_comparison']), 'payload.on-registry': dict(good_registry, payload=good['payload']),
        'extra': dict(good, extra=1), 'bad-dependency': dict(good, sha256='x'), 'kind.unknown': dict(good, kind='x'), 'kind.missing': edit(good, ['kind'], DROP), 'empty': {},
        'kind.list': dict(good, kind=[]),
    }
    for name, value in records.items():
        fn('dependency-record.' + name, 'validate_dependency_record', [value])
    # ---- check_dependencies -------------------------------------------------------------------------
    locked = {'name': dep['name'], 'version': '1.0.0', 'sha256': dep['sha256'], 'provenance': good}
    conf = {'cook': {'host_dependencies': [dep]}}

    def cd(ident, data, config=conf, pin=PIN, *more):
        fn('check.' + ident, 'check_dependencies', [data, config, pin, *more])
    cd('valid', {'hostDependencies': [locked]})
    cd('valid.none', {}, {})
    cd('valid.none.cook', {'hostDependencies': []}, {'cook': {}})
    cd('valid.extra-keys-ignored', {'hostDependencies': [dict(locked, size=1, anything=[1])], 'other': 1})
    cd('missing-in-lock', {})
    cd('missing-in-config', {'hostDependencies': [locked]}, {})
    cd('other-name', {'hostDependencies': [dict(locked, name='other-package')]})
    cd('order', {'hostDependencies': [dict(locked, name='other-package', sha256=other['sha256'], provenance=connectome_archive.provenance_record(other)), locked]}, {'cook': {'host_dependencies': [dep, other]}})
    cd('two.valid', {'hostDependencies': [locked, dict(locked, name='other-package', provenance=connectome_archive.provenance_record(other))]}, {'cook': {'host_dependencies': [dep, other]}})
    cd('sha-differs', {'hostDependencies': [dict(locked, sha256='f' * 64)]})
    cd('version-differs', {'hostDependencies': [dict(locked, version='1.0.1')]})
    cd('no-provenance.generic', {'hostDependencies': [edit(locked, ['provenance'], DROP)]})
    cd('provenance.invalid', {'hostDependencies': [dict(locked, provenance={})]})
    cd('provenance.other-archive', {'hostDependencies': [dict(locked, provenance=dict(good, archive='/fixture/archives/moved.tgz'))]})
    cd('provenance.reordered', {'hostDependencies': [dict(locked, provenance=dict(reversed(list(good.items()))))]})
    cd('no-sha', {'hostDependencies': [edit(locked, ['sha256'], DROP)]})
    cd('no-version', {'hostDependencies': [edit(locked, ['version'], DROP)]})
    cd('no-name', {'hostDependencies': [edit(locked, ['name'], DROP)]})
    cd('item.string', {'hostDependencies': ['example-package']})
    cd('inventory.mapping', {'hostDependencies': {'example-package': locked}})
    cd('inventory.string', {'hostDependencies': 'x'})
    cd('inventory.number', {'hostDependencies': 1})
    cd('inventory.none', {'hostDependencies': None})
    cd('data.list', [])
    cd('data.none', None)
    cd('config.invalid', {'hostDependencies': [locked]}, {'cook': {'host_dependencies': [{}]}})
    cd('config.list', {'hostDependencies': [locked]}, [])
    legacy_conf = {'cook': {'framework_archive': plain}}
    framework = {'name': FRAMEWORK, 'version': '1.2.3', 'sha256': plain['sha256']}
    cd('legacy.valid', {'hostDependencies': [framework]}, legacy_conf)
    cd('legacy.any-version', {'hostDependencies': [dict(framework, version='9.9.9')]}, legacy_conf)
    cd('legacy.record-version', {'hostDependencies': [framework]}, legacy_conf, PIN, {'version': '1.2.3'})
    cd('legacy.record-version-differs', {'hostDependencies': [framework]}, legacy_conf, PIN, {'version': '1.2.4'})
    cd('legacy.record-without-version', {'hostDependencies': [framework]}, legacy_conf, PIN, {})
    cd('legacy.record-list', {'hostDependencies': [framework]}, legacy_conf, PIN, [])
    cd('legacy.sha-differs', {'hostDependencies': [dict(framework, sha256='0' * 64)]}, legacy_conf)
    legacy_record = connectome_archive.provenance_record(dict(connectome_archive.legacy_dependency(legacy_conf, PIN), version='1.2.3'))
    cd('legacy.provenance.valid', {'hostDependencies': [dict(framework, provenance=legacy_record)]}, legacy_conf)
    cd('legacy.provenance.other-pin', {'hostDependencies': [dict(framework, provenance=legacy_record)]}, legacy_conf, 'e' * 40)
    cd('legacy.provenance.version-differs', {'hostDependencies': [dict(framework, version='1.2.4', provenance=legacy_record)]}, legacy_conf)
    cd('legacy.marked.valid', {'hostDependencies': [framework]}, {'cook': {'framework_archive': marked}})
    cd('legacy.marked.version-differs', {'hostDependencies': [dict(framework, version='1.2.4')]}, {'cook': {'framework_archive': marked}})
    add('archive.check.keyword', 'connectome_archive.check_dependencies', [{'hostDependencies': [framework]}, legacy_conf, PIN], {'framework_record': {'version': '1.2.4'}})
    # ---- validate_provenance -------------------------------------------------------------------------
    sidecar = {'schema': 'connectome.framework-archive/v1', 'sha256': plain['sha256'], 'name': FRAMEWORK, 'version': '1.2.3', 'requested_commit': PIN, 'registry_release': False, 'commit_verifiable': True}
    released = dict(sidecar, registry_release=True, commit_verifiable=False)

    def vp(ident, record, artifact=plain, pin=PIN):
        fn('validate-provenance.' + ident, 'validate_provenance', [record, {'cook': {'framework_archive': artifact}}, pin])
    vp('valid', sidecar)
    vp('valid.marked', released, marked)
    vp('valid.marked.verifiable', dict(released, commit_verifiable=True), marked)
    vp('not-mapping', 'x')
    vp('none', None)
    vp('extra', dict(sidecar, extra=1))
    for key in sidecar:
        vp('missing.' + key, edit(sidecar, [key], DROP))
    vp('schema', dict(sidecar, schema='connectome.framework-archive/v2'))
    vp('sha', dict(sidecar, sha256='0' * 64))
    vp('name', dict(sidecar, name='other'))
    vp('version.range', dict(sidecar, version='^1.2.3'))
    vp('version.credential', dict(sidecar, version=J('1.2.3-', 's', 'k-', 'a' * 12)))
    vp('pin', dict(sidecar, requested_commit='e' * 40))
    vp('release.one', dict(sidecar, registry_release=0))
    vp('verifiable.one', dict(sidecar, commit_verifiable=1))
    vp('release.claimed', released)
    vp('release.denied', sidecar, marked)
    vp('neither', dict(sidecar, commit_verifiable=False))
    vp('marked.other-version', dict(released, version='1.2.4'), marked)
    vp('marked.artifact-one', released, dict(marked, registry_release=1))
    vp('artifact.list', sidecar, [])
    vp('artifact.none', sidecar, None)
    vp('artifact.no-sha', sidecar, {'path': plain['path']})
    fn('validate-provenance.no-cook', 'validate_provenance', [sidecar, {}, PIN])
    fn('validate-provenance.config-list', 'validate_provenance', [sidecar, [], PIN])
    # ---- archive_manifest, dependency_provenance, framework_provenance (files) ----------------------------
    def am(ident, spec, record=None, files=None, **more):
        tree_ = dict(A, **{'archives/case.tgz': spec}) if files is None else files
        fn('manifest.' + ident, 'archive_manifest', [entry(spec, file='case.tgz', **more) if record is None else record], tree_)
    am('valid', base)
    am('valid.version-none', base, version=None)
    am('valid.git-head', packed(gitHead='a' * 40))
    am('valid.extra-members', G(['package/README.md', 'x'], ['package/package.json', manifest()], ['package/lib/package.json', '{}'], ['other.json', '{}']))
    am('valid.other-root', G(['pkg-root/package.json', manifest()]))
    am('valid.dot-is-wildcard', G(['package/packageXjson', manifest()]))
    am('valid.directory-entry-ignored', G(['other/package.json', None], ['package/package.json', manifest()]))
    am('hash-differs', base, entry(base, file='case.tgz', sha256='0' * 64))
    am('registry.valid', base, kind='registry_release', registry_url='https://registry.example.invalid', registry_integrity={'$sha512-integrity-of': base})
    am('registry.integrity-differs', base, kind='registry_release', registry_url='https://registry.example.invalid', registry_integrity='sha512-' + digest512)
    am('missing', base, entry(base, file='absent.tgz'))
    am('directory', base, entry(base, file='dir.tgz'))
    am('link', base, entry(base, file='link.tgz'))
    am('no-manifest', G(['package/index.js', 'x']))
    am('empty-archive', G())
    am('two-manifests', G(['package/package.json', manifest()], ['other/package.json', manifest()]))
    am('same-manifest-twice', G(['package/package.json', manifest()], ['package/package.json', manifest()]))
    am('too-deep', G(['a/b/package.json', manifest()]))
    am('top-level', G(['package.json', manifest()]))
    am('manifest.one-mebibyte', G(['package/package.json', {'prefix': '{"name":"example-package","version":"1.0.0","pad":"', 'unit': 'x', 'count': 1048576 - 51, 'suffix': '"}'}]))
    am('manifest.too-large', G(['package/package.json', {'prefix': '{"name":"example-package","version":"1.0.0","pad":"', 'unit': 'x', 'count': 1048576 - 50, 'suffix': '"}'}]))
    am('manifest.not-json', G(['package/package.json', 'not json']))
    am('manifest.empty', G(['package/package.json', '']))
    am('manifest.list', G(['package/package.json', '[]']))
    am('manifest.duplicate-keys', G(['package/package.json', '{"name":"other","name":"example-package","version":"1.0.0"}']))
    am('manifest.nan', G(['package/package.json', '{"name":"example-package","version":"1.0.0","x":NaN}']))
    am('manifest.utf16', G(['package/package.json', manifest()]), files=dict(A, **{'archives/case.tgz': {'file': b64(_tgz_bytes([('package/package.json', manifest().encode('utf-16'))]))}}),
       record={'name': 'example-package', 'archive': R + '/archives/case.tgz', 'sha256': hashlib.sha256(_tgz_bytes([('package/package.json', manifest().encode('utf-16'))])).hexdigest(), 'kind': 'artifact', 'version': '1.0.0'})
    am('name-differs', packed('other-package'))
    am('name-missing', G(['package/package.json', '{"version":"1.0.0"}']))
    am('version-differs', packed(version='1.0.1'))
    am('version-range', packed(version='^1.0.0'), version=None)
    am('version-missing', G(['package/package.json', '{"name":"example-package"}']), version=None)
    am('version-credential', packed(version=J('1.0.0-', 's', 'k-', 'a' * 12)), version=None)
    am('version-number', G(['package/package.json', '{"name":"example-package","version":1}']), version=None)
    am('not-gzip', {'text': 'plain text, not an archive'}, record={'name': 'example-package', 'archive': R + '/archives/case.tgz', 'sha256': hashlib.sha256(b'plain text, not an archive').hexdigest(), 'kind': 'artifact', 'version': '1.0.0'})
    am('empty-file', {'text': ''}, record={'name': 'example-package', 'archive': R + '/archives/case.tgz', 'sha256': hashlib.sha256(b'').hexdigest(), 'kind': 'artifact', 'version': '1.0.0'})
    am('gzip-of-text', {'file': b64(_gz(b'not a tar archive at all'))}, record={'name': 'example-package', 'archive': R + '/archives/case.tgz', 'sha256': hashlib.sha256(_gz(b'not a tar archive at all')).hexdigest(), 'kind': 'artifact', 'version': '1.0.0'})
    am('entry.no-archive', base, {'name': 'example-package'})
    am('entry.archive-number', base, {'archive': 1})
    am('entry.no-sha', base, {'name': 'example-package', 'archive': R + '/archives/case.tgz', 'kind': 'artifact', 'version': '1.0.0'})
    am('entry.list', base, [])
    am('unreadable-parent', base, {'name': 'example-package', 'archive': R + '/closed/case.tgz', 'sha256': 'e' * 64, 'kind': 'artifact', 'version': '1.0.0'}, files=dict(A, closed={'dir': 0o000}))
    am('unreadable-file', base, files=dict(A, **{'archives/case.tgz': dict(base, mode=0o000)}))

    def dp(ident, config, files, pin=PIN, function='dependency_provenance'):
        fn(('provenance.' if function == 'dependency_provenance' else 'framework.') + ident, function, [config, pin], files)
    one = entry(base)
    dp('one', {'cook': {'host_dependencies': [one]}}, A)
    dp('none', {'cook': {}}, A)
    dp('git.valid', {'cook': {'host_dependencies': [entry(packed(gitHead='a' * 40), kind='git_commit', commit='a' * 40, file='git.tgz')]}}, dict(A, **{'archives/git.tgz': packed(gitHead='a' * 40)}))
    dp('git.other-head', {'cook': {'host_dependencies': [entry(packed(gitHead='b' * 40), kind='git_commit', commit='a' * 40, file='git.tgz')]}}, dict(A, **{'archives/git.tgz': packed(gitHead='b' * 40)}))
    dp('git.no-head', {'cook': {'host_dependencies': [entry(base, kind='git_commit', commit='a' * 40)]}}, A)
    dp('artifact.head', {'cook': {'host_dependencies': [entry(packed(gitHead='a' * 40), file='git.tgz')]}}, dict(A, **{'archives/git.tgz': packed(gitHead='a' * 40)}))
    dp('artifact.bad-head', {'cook': {'host_dependencies': [entry(packed(gitHead='main'), file='git.tgz')]}}, dict(A, **{'archives/git.tgz': packed(gitHead='main')}))
    dp('artifact.null-head', {'cook': {'host_dependencies': [entry(packed(gitHead=None), file='git.tgz')]}}, dict(A, **{'archives/git.tgz': packed(gitHead=None)}))
    dp('second-bad', {'cook': {'host_dependencies': [one, entry(packed('other-package'), name='wrong-name', file='other.tgz')]}}, dict(A, **{'archives/other.tgz': packed('other-package')}))
    dp('two', {'cook': {'host_dependencies': [one, entry(packed('other-package'), name='other-package', file='other.tgz')]}}, dict(A, **{'archives/other.tgz': packed('other-package')}))
    fw = packed(FRAMEWORK, '1.2.3', gitHead=PIN)
    fw_no_head = packed(FRAMEWORK, '1.2.3')
    F = dict(A, **{'archives/framework.tgz': fw, 'archives/nohead.tgz': fw_no_head})
    unmarked = {'path': R + '/archives/framework.tgz', 'sha256': {'$sha256-of': fw}}
    unmarked_no_head = {'path': R + '/archives/nohead.tgz', 'sha256': {'$sha256-of': fw_no_head}}
    for function in ('dependency_provenance', 'framework_provenance'):
        dp('legacy.valid', {'cook': {'framework_archive': unmarked}}, F, function=function)
        dp('legacy.other-pin', {'cook': {'framework_archive': unmarked}}, F, 'e' * 40, function=function)
        dp('legacy.no-head', {'cook': {'framework_archive': unmarked_no_head}}, F, function=function)
        dp('legacy.marked', {'cook': {'framework_archive': dict(unmarked, registry_release=True, name=FRAMEWORK, version='1.2.3')}}, F, function=function)
        dp('legacy.marked.no-head', {'cook': {'framework_archive': dict(unmarked_no_head, registry_release=True, name=FRAMEWORK, version='1.2.3')}}, F, function=function)
        dp('legacy.marked.other-version', {'cook': {'framework_archive': dict(unmarked, registry_release=True, name=FRAMEWORK, version='1.2.4')}}, F, function=function)
        dp('legacy.marked.other-pin', {'cook': {'framework_archive': dict(unmarked, registry_release=True, name=FRAMEWORK, version='1.2.3')}}, F, 'e' * 40, function=function)
        dp('legacy.with-dependency', {'cook': {'framework_archive': unmarked, 'host_dependencies': [one]}}, F, function=function)
        dp('legacy.hash-differs', {'cook': {'framework_archive': dict(unmarked, sha256='0' * 64)}}, F, function=function)
        dp('legacy.invalid', {'cook': {'framework_archive': {}}}, F, function=function)
    dp('no-framework', {'cook': {}}, F, function='framework_provenance')
    dp('no-cook', {}, F, function='framework_provenance')


def _gz(data):
    import zlib
    out = bytearray(b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff')
    out += b'\x01' + len(data).to_bytes(2, 'little') + (len(data) ^ 0xffff).to_bytes(2, 'little') + data
    return bytes(out) + zlib.crc32(data).to_bytes(4, 'little') + len(data).to_bytes(4, 'little')


def _tgz_bytes(members):
    """Like `tgz` in ts_port.py, for members whose content is raw bytes (stored as a blob in the tree)."""
    tar = b''
    for name, data in members:
        head = bytearray(512)
        head[0:len(name)] = name.encode()
        head[100:108], head[108:116], head[116:124] = b'0000644\0', b'0000000\0', b'0000000\0'
        head[124:136] = b'%011o\0' % len(data)
        head[136:148] = b'00000000000\0'
        head[148:156] = b' ' * 8
        head[156:157] = b'0'
        head[257:265] = b'ustar\x0000'
        head[148:156] = b'%06o\0 ' % sum(head)
        tar += bytes(head) + data + b'\0' * (-len(data) % 512)
    return _gz(tar + b'\0' * 1024)


def hand_config(add):
    def ml(ident, layers):
        add('config.merge.' + ident, 'connectome_config.merge_layers', [layers])
    base = {'name': 'example', 'agent': {'systemPrompt': 'x', 'tools': ['a', 'b']}, 'connectors': {'alpha': {'kind': 'x', 'enabled': True}, 'beta': {'enabled': False}}, 'modules': {'m': {}}}
    ml('valid', [base, {}, {}, {}])
    ml('empty', [{}, {}, {}, {}])
    ml('override', [base, {'name': 'other'}, {'agent': {'systemPrompt': 'y'}}, {'connectors': {'alpha': {'kind': 'z'}}}])
    ml('lists-replaced', [base, {'agent': {'tools': ['c']}}, {}, {}])
    ml('mapping-replaces-scalar', [{'a': 1}, {'a': {'b': 1}}, {}, {}])
    ml('scalar-replaces-mapping', [{'a': {'b': 1}}, {'a': 1}, {}, {}])
    ml('tombstone', [{'a': {'b': 1, 'c': 2}}, {'a': {'b': None}}, {}, {}])
    ml('tombstone.whole', [{'a': {'b': 1}}, {'a': None}, {}, {}])
    ml('tombstone.not-resurrected', [{'a': {'b': 1, 'c': 2}}, {'a': None}, {'a': {'d': 3}}, {}])
    ml('tombstone.then-scalar', [{'a': {'b': 1}}, {'a': None}, {'a': 5}, {}])
    ml('tombstone.last-layer', [{'a': 1}, {}, {}, {'a': None}])
    ml('tombstone.in-list-kept', [{'a': [1, None, {'b': None, 'c': 1}]}, {}, {}, {}])
    ml('tombstone.keeps-position', [{'a': 1, 'b': 2}, {'a': None}, {'a': 3}, {}])
    ml('tombstone.keeps-position.nested', [{'x': {'a': 1, 'b': 2}}, {'x': {'a': None}}, {'x': {'a': {'c': 1}}}, {}])
    ml('tombstone.connector', [base, {'connectors': {'alpha': None}}, {}, {}])
    ml('tombstone.section', [base, {'connectors': None}, {}, {}])
    ml('key-order', [{'b': 1, 'a': 2}, {'c': 3, 'a': 4}, {'10': 5, '9': 6}, {'b': 7}])
    ml('enabled.false-then-true', [base, {'connectors': {'beta': {'enabled': True}}}, {}, {}])
    ml('enabled.true-then-false', [base, {'connectors': {'alpha': {'enabled': False}}}, {}, {}])
    ml('enabled.missing', [{'connectors': {'a': {}}, 'modules': {'b': {'x': 1}}}, {}, {}, {}])
    for name, value in {'string': 'yes', 'one': 1, 'zero': 0, 'null': None, 'list': []}.items():
        ml('enabled.' + name, [{'connectors': {'a': {'enabled': value}}}, {}, {}, {}])
        ml('enabled.module.' + name, [{'modules': {'a': {'enabled': value}}}, {}, {}, {}])
    ml('entry.not-mapping', [{'connectors': {'a': 'x'}}, {}, {}, {}])
    ml('entry.list', [{'modules': {'a': []}}, {}, {}, {}])
    ml('section.list', [{'connectors': []}, {}, {}, {}])
    ml('section.string', [{'modules': 'x'}, {}, {}, {}])
    ml('section.number', [{'connectors': 1}, {}, {}, {}])
    ml('catalog.absent', [{'connectors': {}}, {}, {}, {}])
    ml('catalog.entries', [{'catalog': {'node': {'kind': 'runtime'}, 'old': {'enabled': False}}}, {}, {}, {}])
    ml('catalog.all-disabled', [{'catalog': {'old': {'enabled': False}}}, {}, {}, {}])
    ml('catalog.empty', [{'catalog': {}}, {}, {}, {}])
    ml('catalog.list', [{'catalog': []}, {}, {}, {}])
    ml('catalog.bad-entry', [{'catalog': {'a': 1}}, {}, {}, {}])
    ml('catalog.bad-enabled', [{'catalog': {'a': {'enabled': 'no'}}}, {}, {}, {}])
    ml('catalog.null', [{'catalog': {'a': {}}}, {'catalog': None}, {}, {}])
    ml('sections-order', [{'modules': {}, 'x': 1}, {}, {}, {}])
    ml('modules-before-connectors-error', [{'modules': 1, 'connectors': 2}, {}, {}, {}])
    ml('deep', [{'a': {'b': {'c': {'d': 1, 'e': [1, {'f': None}]}}}}, {'a': {'b': {'c': {'d': None, 'g': 2}}}}, {'a': {'b': {'h': 3}}}, {'a': {'i': 4}}])
    for name, value in {'three': [{}, {}, {}], 'five': [{}, {}, {}, {}, {}], 'none': [], 'list-layer': [{}, [], {}, {}], 'null-layer': [{}, {}, None, {}], 'string-layer': [{}, {}, {}, 'x'],
                        'mapping': {'a': {}, 'b': {}, 'c': {}, 'd': {}}, 'string': 'abcd', 'number': 4, 'null': None, 'nested-list': [[{}, {}, {}, {}]]}.items():
        ml('count.' + name, value)

    for name, (layers, kwargs) in {'two': ([{'a': {'b': 1}, 'l': [1]}, {'a': {'c': 2}, 'l': [2]}], {'recursive': True}), 'flat': ([{'a': {'b': 1}}, {'a': {'c': 2}}], {}),
                                   'flat.explicit': ([{'a': {'b': 1}}, {'a': {'c': 2}}], {'recursive': False}), 'three': ([{'a': {'b': {'c': 1}}}, {'a': {'b': None}}, {'a': {'b': {'d': 2}}}], {'recursive': True}),
                                   'none': ([], {}), 'one': ([{'a': None}], {'recursive': True}), 'list-layer': ([{}, []], {'recursive': True}), 'null-layer': ([None], {}),
                                   'string-layer': (['ab'], {})}.items():
        add('config.combine.' + name, 'connectome_config.combine', layers, kwargs)

    def pc(ident, defaults, catalog):
        add('config.public.' + ident, 'connectome_config.public_catalog', [defaults, catalog])
    cat = {'slack': {'kind': 'connector', 'repo': 'x'}, 'plain': {'repo': 'y'}, 'node': {'kind': 'runtime'}, 'host': {'kind': 'host'}}
    pc('valid', {'agent': {'name': 'x'}}, cat)
    pc('valid.no-artifacts', {}, {'plain': {}})
    pc('valid.empty', {}, {})
    pc('valid.only-artifacts', {'x': 1}, {'node': {'kind': 'runtime'}})
    pc('connectors-present', {'connectors': {}}, cat)
    pc('connectors-null', {'connectors': None}, cat)
    pc('catalog-overwritten', {'catalog': {'old': 1}}, cat)
    pc('catalog-kept', {'catalog': {'old': 1}}, {'plain': {}})
    pc('kind.null', {}, {'a': {'kind': None}})
    pc('kind.number', {}, {'a': {'kind': 1}})
    pc('entry.string', {}, {'a': 'connector'})
    pc('entry.null', {}, {'a': None})
    pc('catalog.list', {}, [])
    pc('catalog.null', {}, None)
    pc('defaults.list', [], cat)
    pc('defaults.list-with-name', ['connectors'], cat)
    pc('defaults.string', 'no', cat)
    pc('defaults.string-with-name', 'my connectors', cat)
    pc('defaults.null', None, cat)
    pc('defaults.number', 1, cat)
    pc('defaults.list.catalog-list', [], [])
    pc('copies', {'a': {'b': [1]}}, {'plain': {'deep': {'x': [1]}}})

    def rn(ident, config):
        add('config.names.' + ident, 'connectome_config.required_names', [config])
    rn('empty', {})
    rn('all', {'required_secrets': ['B', 'A'], 'secret_files': [{'secret': 'C', 'env': 'C_FILE'}], 'connectors': {'x': {'required_secrets': ['D', 'A']}}, 'modules': {'y': {'required_secrets': ['E']}, 'z': {}}})
    rn('sorted-by-code-point', {'required_secrets': ['b', 'B', '_', 'a', 'Z', '\u00e9', '\U0001f600', '\uffff']})
    rn('duplicates', {'required_secrets': ['A', 'A'], 'secret_files': [{'secret': 'A'}]})
    rn('numbers', {'required_secrets': [3, 1, 2, 1]})
    rn('number-and-bool', {'required_secrets': [1, True, 0]})
    rn('mixed', {'required_secrets': ['A', 1]})
    rn('null-and-string', {'required_secrets': [None, 'A']})
    rn('single-null', {'required_secrets': [None]})
    rn('unhashable', {'required_secrets': [['A']]})
    rn('string-secrets', {'required_secrets': 'AB'})
    rn('mapping-secrets', {'required_secrets': {'B': 1, 'A': 2}})
    rn('number-secrets', {'required_secrets': 1})
    rn('null-secrets', {'required_secrets': None})
    rn('secret-files.no-secret', {'secret_files': [{'env': 'X'}]})
    rn('secret-files.string-item', {'secret_files': ['x']})
    rn('secret-files.mapping', {'secret_files': {'a': 1}})
    rn('secret-files.number', {'secret_files': 1})
    rn('connectors.list', {'connectors': []})
    rn('connectors.null', {'connectors': None})
    rn('modules.string', {'modules': 'x'})
    rn('entry.string', {'connectors': {'x': 'y'}})
    rn('entry.null', {'modules': {'x': None}})
    rn('entry.names-string', {'connectors': {'x': {'required_secrets': 'AB'}}})
    rn('entry.names-number', {'connectors': {'x': {'required_secrets': 1}}})
    rn('order-of-errors', {'required_secrets': [['A']], 'secret_files': [{}]})
    rn('order-of-errors.connectors-first', {'connectors': [], 'required_secrets': 1})
    rn('config.list', [])
    rn('config.null', None)


VALUES = [None, False, True, 0, -1, 2, 10000001, {'$float': '0.5'}, {'$float': '2.0'}, '', 'invalid', '../escape', '/outside', 'a\n', '${UNDECLARED}', '0' * 40, '0' * 64, [], {}, ['duplicate', 'duplicate'], [[]], {'1': 0, 'a': 1}]


def paths(value, path=()):
    yield path, value
    if isinstance(value, dict) and not any(key.startswith('$') for key in value):
        for key in value:
            yield from paths(value[key], path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from paths(item, path + (index,))


def generated():
    """Systematic single-node perturbations of valid inputs. Deterministic; ids are content hashes."""
    T = tree()
    seeds = [
        ('connectome_lock.map_lock', [lock(), BUILD], {}, T),
        ('connectome_lock.map_lock', [lock(units=[{'path': 'example.service', 'sha256': UNIT_SHA}]), BUILD], {'host_pin': 'c' * 40, 'required_names': ['EXAMPLE_NAME']}, T),
        ('connectome_lock.map_lock', [lock(launch={'kind': 'script', 'script': 'run.sh', 'userUnit': 'example.service'}), BUILD], {}, T),
        ('connectome_lock.map_lock', [dependency_lock(), BUILD], {}, T),
        ('connectome_lock.map_lock', [dependency_lock(), BUILD], {'config': dependency_config(), 'framework_pin': 'f' * 40}, T),
        ('connectome_lock.map_lock', [recipe_lock(), BUILD], {'config': recipe_config()}, T),
        ('connectome_inputs.validate', [config()], {}, None),
        ('connectome_inputs.validate_external_data_args', [[data_arg(), data_arg(server='other')]], {}, None),
        ('connectome_inputs.payloads', [{'recipe_file': {'path': '/fixture/operator/recipe.json', 'sha256': RECIPE_SHA}}, {'cook-recipe.json': b64(RECIPE)}], {}, None),
        ('connectome_inputs.verify_bytes', [B(RECIPE), {'sha256': RECIPE_SHA}], {}, None),
        ('connectome_inputs.installed', [BUILD, {'recipe_file': {'path': '/fixture/operator/recipe.json', 'sha256': RECIPE_SHA}}], {}, T),
        ('connectome_inputs.collect', [recipe_config()], {}, T),
        ('connectome_errors.diagnostic', ['E_COOK_FAILED', 'E_HOST_UNIT'], {}, None),
        ('connectome_lock.names_only', [lock()], {}, None),
    ]
    import connectome_archive
    dep = {'name': 'example-package', 'archive': '/fixture/archives/example.tgz', 'sha256': 'e' * 64, 'kind': 'artifact', 'version': '1.0.0'}
    git = dict(dep, name='git-package', kind='git_commit', commit='a' * 40)
    registry = dict(dep, name='registry-package', kind='registry_release', registry_url='https://registry.example.invalid',
                    registry_integrity='sha512-' + base64.b64encode(hashlib.sha512(b'example').digest()).decode())
    plain = {'path': '/fixture/archives/framework.tgz', 'sha256': 'e' * 64}
    marked = dict(plain, registry_release=True, name=FRAMEWORK, version='1.2.3')
    locked = [{'name': d['name'], 'version': '1.0.0', 'sha256': d['sha256'], 'provenance': connectome_archive.provenance_record(d)} for d in (dep, git, registry)]
    sidecar = {'schema': 'connectome.framework-archive/v1', 'sha256': plain['sha256'], 'name': FRAMEWORK, 'version': '1.2.3', 'requested_commit': PIN, 'registry_release': True, 'commit_verifiable': False}
    base = packed()
    fw = packed(FRAMEWORK, '1.2.3', gitHead=PIN)
    A = {'archives/example.tgz': base, 'archives/framework.tgz': fw}
    layers = [{'name': 'example', 'agent': {'tools': ['a']}, 'connectors': {'alpha': {'enabled': True, 'x': {'y': 1}}, 'beta': {'enabled': False}}, 'modules': {'m': {}}, 'catalog': {'node': {'kind': 'runtime'}}},
              {'connectors': {'alpha': {'x': {'y': None, 'z': 2}}}}, {'agent': None}, {'modules': {'m': {'enabled': True}}}]
    seeds += [
        ('connectome_archive.validate_dependency', [dep], {}, None), ('connectome_archive.validate_dependency', [git], {}, None), ('connectome_archive.validate_dependency', [registry], {}, None),
        ('connectome_archive.dependency_inputs', [{'cook': {'framework_archive': plain, 'host_dependencies': [dep, git, registry]}}, PIN], {}, None),
        ('connectome_archive.legacy_dependency', [{'cook': {'framework_archive': marked}}, PIN], {}, None),
        ('connectome_archive.validate_dependency_record', [connectome_archive.provenance_record(registry)], {}, None),
        ('connectome_archive.check_dependencies', [{'hostDependencies': locked}, {'cook': {'host_dependencies': [dep, git, registry]}}, PIN], {}, None),
        ('connectome_archive.check_dependencies', [{'hostDependencies': [{'name': FRAMEWORK, 'version': '1.2.3', 'sha256': plain['sha256']}]}, {'cook': {'framework_archive': plain}}, PIN, {'version': '1.2.3'}], {}, None),
        ('connectome_archive.validate_provenance', [sidecar, {'cook': {'framework_archive': marked}}, PIN], {}, None),
        ('connectome_archive.records_metadata', [{'cook': {'framework_archive': marked}}], {}, None),
        ('connectome_archive.provenance_record', [registry], {}, None),
        ('connectome_archive.archive_manifest', [entry(base)], {}, A),
        ('connectome_archive.dependency_provenance', [{'cook': {'framework_archive': {'path': R + '/archives/framework.tgz', 'sha256': {'$sha256-of': fw}}, 'host_dependencies': [entry(base)]}}, PIN], {}, A),
        ('connectome_archive.framework_provenance', [{'cook': {'framework_archive': {'path': R + '/archives/framework.tgz', 'sha256': {'$sha256-of': fw}}}}, PIN], {}, A),
        ('connectome_config.merge_layers', [layers], {}, None),
        ('connectome_config.public_catalog', [{'agent': {'name': 'x'}}, {'slack': {'kind': 'connector'}, 'plain': {}, 'node': {'kind': 'runtime'}}], {}, None),
        ('connectome_config.required_names', [{'required_secrets': ['B', 'A'], 'secret_files': [{'secret': 'C', 'env': 'C_FILE'}], 'connectors': {'x': {'required_secrets': ['D']}}, 'modules': {'y': {}}}], {}, None),
    ]
    rows, seen = [], set()

    def emit(function, args, kwargs, files):
        row = {'function': function, 'args': args, 'kwargs': kwargs}
        if files is not None:
            row['tree'] = files
        text = json.dumps(row)  # key order is part of the input
        ident = 'gen.' + hashlib.sha256(text.encode()).hexdigest()[:16]
        if ident not in seen:
            seen.add(ident)
            rows.append(dict(row, id=ident))
    for function, args, kwargs, files in seeds:
        bundle = [args, kwargs]
        emit(function, args, kwargs, files)
        for path, value in paths(bundle):
            if len(path) < 2:
                continue
            choices = list(VALUES)
            if isinstance(value, str):
                choices += [value + '\n', value + '/', value + '/..', value + '\x00', value * 2, value.upper(), 'x' + value, value[:-1], ' ' + value, value + '\u00e9']
            elif isinstance(value, bool):
                choices += [not value]
            elif isinstance(value, int):
                choices += [value + 1, value - 1, {'$float': repr(float(value))}, str(value)]
            elif isinstance(value, dict) and not any(key.startswith('$') for key in value):
                choices += [{k: v for k, v in value.items() if k != drop} for drop in value]
                choices += [dict(value, unexpected='extra'), dict({'0': 'first'}, **value), dict(reversed(list(value.items())))]
            elif isinstance(value, list) and value:
                choices += [value + value[:1], value[1:], value[::-1], value + [None]]
            for choice in choices:
                changed = edit(bundle, list(path), choice)
                emit(function, changed[0], changed[1], files)
    return rows


if __name__ == '__main__':
    h, g = hand(), generated()
    print(json.dumps({'hand': len(h), 'generated': len(g)}))
