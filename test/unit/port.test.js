import test from 'node:test';
import assert from 'node:assert/strict';
import { cleanup, evaluate, vectors } from '../differential/ts_corpus.mjs';
// errors, lock and inputs against the oracle's committed outcomes: one test per vector.
const all = vectors();
test.after(cleanup);
test('the committed corpus of errors, lock and inputs is present', () => {
 const count = source => all.filter(vector => vector.source === source).length;
 assert.equal(count('guard-negative'), 35); assert.equal(count('crash-only'), 1); assert.ok(count('port') > 1000);
 assert.equal(new Set(all.map(vector => vector.id)).size, all.length);
});
for (const vector of all) test(vector.id, () => { const { expected, actual } = evaluate(vector); assert.equal(actual, expected); });
