import test from 'node:test';
import fs from 'node:fs';
import assert from 'node:assert/strict';
import {loads,canonical,unicode} from '../../dist/pylike/json.js';
test('extracted Python canonical vectors',()=>{
 const lines=fs.readFileSync('test/vectors/canonical.jsonl','utf8').trim().split('\n').filter(Boolean);
 assert.ok(lines.length>0);
 for(const line of lines){const row=JSON.parse(line);assert.equal((row.encoder==='unicode'?unicode:canonical)(loads(row.source)),row.expected);}
 console.log('extracted canonical vectors: '+lines.length);
});
