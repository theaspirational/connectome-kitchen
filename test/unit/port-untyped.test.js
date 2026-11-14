import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import * as errors from '../../dist/errors.js';
import * as inputs from '../../dist/inputs.js';
import * as lock from '../../dist/lock.js';
import { loads } from '../../dist/pylike/pyjson.js';
// A caller that is not type-checked (plain JavaScript, a decoded JSON value) must get the oracle's answer for a
// wrong argument type, a modelled TypeError, never a JavaScript runtime error and never a judgement of coerced data.
const outcome = call => { try { call(); return 'returned'; } catch (error) { return (error?.pyName ?? 'JS ' + error?.constructor?.name) + ': ' + error?.message; } };
const document = loads('{"version":2,"backend":"host"}');
const recipe = '{"a":1}';
const config = loads('{"recipe_file":{"path":"/fixture/r.json","sha256":"' + 'a'.repeat(64) + '"}}');
test('a path parameter that is not a string', () => {
 for (const build of [null, undefined, 0, true, [], new Map(), Buffer.from('x')]) {
  assert.equal(outcome(() => lock.map_lock(document, build)), 'TypeError: expected str, bytes or os.PathLike object');
  assert.equal(outcome(() => inputs.read_file(build, new Map())), 'TypeError: expected str, bytes or os.PathLike object');
  assert.equal(outcome(() => inputs.installed(build, config)), 'TypeError: expected str, bytes or os.PathLike object');
  // read_lock names TypeError in its handler, so there it is the generic refusal, as in the oracle.
  assert.equal(outcome(() => lock.read_lock(build)), 'LockError: malformed or unreadable cook lock v2');
 }
 assert.equal(outcome(() => inputs.installed(null, new Map())), 'returned'); // no recipe file: the path is never looked at
});
test('recipe bytes given as text are refused, not hashed as UTF-8', () => {
 const record = new Map([['sha256', createHash('sha256').update(recipe).digest('hex')]]);
 assert.equal(outcome(() => inputs.verify_bytes(Buffer.from(recipe), record)), 'returned');
 for (const content of [recipe, null, 5, [123, 125], new Map()]) assert.equal(outcome(() => inputs.verify_bytes(content, record)), 'TypeError: Strings must be encoded before hashing');
});
test('cook output and streams of the wrong type', () => {
 for (const output of ['E_HOST_UNIT', null, 5]) assert.equal(outcome(() => errors.cook_code(output)), 'TypeError: cannot use a bytes pattern on a string-like object');
 for (const stream of [null, 'E_HOST_UNIT', Buffer.from('E_HOST_UNIT'), {}]) assert.equal(outcome(() => errors.cook_code_from_stream(stream)), "AttributeError: object has no attribute 'seek'");
});
test('required names that cannot be iterated are a TypeError where the oracle reaches them', () => {
 assert.equal(outcome(() => lock.map_lock(document, '/fixture/build', { required_names: 5 })), 'LockError: lock missing required v2 fields'); // an earlier refusal wins
});
