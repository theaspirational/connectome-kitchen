import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import * as archive from '../../dist/archive.js';
import * as inputs from '../../dist/inputs.js';
import * as lock from '../../dist/lock.js';
import { loads } from '../../dist/pylike/pyjson.js';
// The lock -> archive seam while the archive module is not ported, and the lazy imports of lock.ts.
const build = path.resolve('.cache/ts-port-seam/build');
fs.rmSync(path.dirname(build), { recursive: true, force: true });
fs.mkdirSync(build, { recursive: true });
fs.writeFileSync(build + '/run.sh', '#!/bin/sh\nexit 0\n');
test.after(() => { archive.bind_seam(null); fs.rmSync(path.dirname(build), { recursive: true, force: true }); });

const dependency = extra => ({ name: 'example-package', originalRange: '^1.0.0', version: '1.0.0', sha256: 'e'.repeat(64), size: 1, snapshot: 'snapshots/example.tgz',
 packageJsonDelta: { direct: { section: 'dependencies', name: 'example-package', before: '^1.0.0', after: 'file:snapshots/example.tgz' },
  override: { section: 'overrides', name: 'example-package', before: null, after: 'file:snapshots/example.tgz' } }, ...extra });
const text = changes => JSON.stringify({ version: 2, backend: 'host', cook: { version: '0.0.1' }, recipe: { path: '/fixture/recipe.json', sha256: 'a'.repeat(64) },
 recipePath: '/fixture/recipe.json', createdAt: '2026-11-09T00:00:00.000Z', build: { id: 'b'.repeat(64), dir: build },
 connectomeHost: { url: 'https://example.invalid/host.git', ref: 'main', commit: 'c'.repeat(40) }, components: [], localExtensions: [], requirements: [], credentials: [], outputs: [],
 launch: { kind: 'script', script: 'run.sh' }, ...changes });
const document = changes => loads(text(changes));
/** What a call did, as data: a crash of the port must fail an assertion, never error a test. */
const outcome = call => { try { return { returned: call() instanceof Map }; } catch (error) { return { raised: error?.pyName ?? 'JS ' + error?.constructor?.name, message: error?.message }; } };
/** A named fake: an archive module that accepts everything and records what it was asked. */
function permissive() {
 const calls = [];
 archive.bind_seam({ npm_name: value => { calls.push(['npm_name', value]); return true; }, validate_dependency_record: () => { calls.push(['validate_dependency_record']); },
  check_dependencies: () => { calls.push(['check_dependencies']); } });
 return calls;
}

test('unbound, the stub raises an ImportError-class error and nothing turns it into a verdict', () => {
 archive.bind_seam(null);
 const unported = { raised: 'ImportError', message: 'connectome_archive is not ported yet; only the lock seam is declared' };
 assert.deepEqual(outcome(() => lock.map_lock(document({ hostDependencies: [dependency()] }), build)), unported);
 assert.deepEqual(outcome(() => lock.map_lock(document({}), build, { config: new Map() })), unported);
 fs.writeFileSync(build + '/connectome.lock', text({ hostDependencies: [dependency()] }));
 assert.deepEqual(outcome(() => lock.read_lock(build + '/connectome.lock')), unported);
 // Without host dependencies and without a config the seam is never reached.
 assert.deepEqual(outcome(() => lock.map_lock(document({}), build)), { returned: true });
 assert.deepEqual(outcome(() => lock.map_lock(document({ hostDependencies: [] }), build)), { returned: true });
});
test('a dependency record outside the closed schema is refused before the archive module is asked anything', () => {
 const refusal = { raised: 'LockError', message: 'invalid host dependency record' };
 for (const record of [dependency({ extra: 1 }), dependency({ size: 0 }), dependency({ version: '1' }), dependency({ snapshot: '' }), 'example-package']) {
  const calls = permissive();
  assert.deepEqual(outcome(() => lock.map_lock(document({ hostDependencies: [record] }), build)), refusal);
  assert.deepEqual(calls, []);
 }
});
test('with a permissive archive module the lock still applies its own dependency rules, in the oracle order', () => {
 let calls = permissive();
 assert.deepEqual(outcome(() => lock.map_lock(document({ hostDependencies: [dependency({ provenance: { name: 'example-package', sha256: 'e'.repeat(64), version: '1.0.0' } })] }), build)), { returned: true });
 assert.deepEqual(calls.map(call => call[0]), ['npm_name', 'validate_dependency_record']);
 calls = permissive();
 assert.deepEqual(outcome(() => lock.map_lock(document({ hostDependencies: [dependency(), dependency()] }), build)), { raised: 'LockError', message: 'invalid or duplicate host dependency name' });
 assert.deepEqual(calls.map(call => call[0]), ['npm_name', 'npm_name']);
 calls = permissive();
 assert.deepEqual(outcome(() => lock.map_lock(document({ hostDependencies: [dependency({ provenance: { name: 'other-package', sha256: 'e'.repeat(64), version: '1.0.0' } })] }), build)),
  { raised: 'LockError', message: 'invalid host dependency provenance' });
 calls = permissive();
 assert.deepEqual(outcome(() => lock.map_lock(document({}), build, { config: new Map() })), { returned: true });
 assert.deepEqual(calls.map(call => call[0]), ['check_dependencies']);
});
test('lock.ts has no static edge to inputs or archive, and the lazy load yields the imported module instance', () => {
 const source = fs.readFileSync('dist/lock.js', 'utf8');
 const imported = [...source.matchAll(/^import .* from '([^']+)';$/gmu)].map(match => match[1]);
 assert.ok(imported.length > 4);
 assert.ok(!imported.some(name => /inputs|archive/u.test(name)));
 assert.equal([...source.matchAll(/lazy\('\.\/inputs\.js'\)/gu)].length, 1);
 assert.equal([...source.matchAll(/lazy\('\.\/archive\.js'\)/gu)].length, 2);
 const lazy = createRequire(new URL('../../dist/lock.js', import.meta.url));
 assert.equal(lazy('./inputs.js'), inputs);
 assert.equal(lazy('./archive.js'), archive);
 assert.equal(lazy('./inputs.js').ExternalDataArgError, inputs.ExternalDataArgError);
});
