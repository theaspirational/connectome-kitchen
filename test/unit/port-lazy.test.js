import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import * as archive from '../../dist/archive.js';
import * as inputs from '../../dist/inputs.js';
import * as lock from '../../dist/lock.js';
// The lazy imports of lock.py:149-152, :256-258 and :282-284, as the compiled port keeps them.
const imports = file => [...fs.readFileSync(file, 'utf8').matchAll(/^import .* from '([^']+)';$/gmu)].map(match => match[1]);
test('lock has no static edge to inputs or archive; archive imports lock statically, as in the oracle', () => {
 const edges = imports('dist/lock.js');
 assert.ok(edges.length > 4);
 assert.ok(!edges.some(name => /inputs|archive/u.test(name)));
 assert.ok(imports('dist/archive.js').includes('./lock.js'));
 const source = fs.readFileSync('dist/lock.js', 'utf8');
 assert.equal([...source.matchAll(/lazy\('\.\/inputs\.js'\)/gu)].length, 1);
 assert.equal([...source.matchAll(/lazy\('\.\/archive\.js'\)/gu)].length, 2);
});
test('the lazy load yields the module instance an import gets, so error classes compare', () => {
 const lazy = createRequire(new URL('../../dist/lock.js', import.meta.url));
 assert.equal(lazy('./inputs.js'), inputs);
 assert.equal(lazy('./archive.js'), archive);
 assert.equal(lazy('./inputs.js').ExternalDataArgError, inputs.ExternalDataArgError);
 assert.equal(lazy('./archive.js').ArchiveError, archive.ArchiveError);
 assert.ok(new archive.ArchiveError('x') instanceof lock.LockError === false);
});
