import test from 'node:test';
import assert from 'node:assert/strict';
import { cleanup, divergences, evaluate, vectors } from '../differential/ts_corpus.mjs';
// The ported engine modules against the oracle's committed outcomes: one test per vector.
const all = vectors();
test.after(cleanup);
test('the committed corpus of the ported modules is present', () => {
 const count = source => all.filter(vector => vector.source === source).length;
 assert.equal(count('guard-negative'), 52); assert.equal(count('crash-only'), 1); assert.ok(count('port') > 1800);
 assert.equal(new Set(all.map(vector => vector.id)).size, all.length);
});
for (const vector of all) test(vector.id, () => { const { expected, actual } = evaluate(vector); assert.equal(actual, expected); });
// Where the port deliberately differs, it must answer exactly what the reviewed record says it answers.
for (const vector of divergences()) test(vector.id, () => { const { expected, actual } = evaluate(vector); assert.equal(actual, expected); });
