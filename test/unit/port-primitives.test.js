import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
// Reduced run of the per-helper differential against CPython (test/differential/port_primitives.py).
// The full run sweeps every code point; this one samples them and shrinks the random kinds.
const done=spawnSync('python3',['test/differential/port_primitives.py','run','--sampled-sweep','--scale','0.05','--node',process.execPath],{encoding:'utf8',maxBuffer:64*1024*1024});
const lines=done.stdout.split('\n').filter(Boolean);
let report={};
try{report=JSON.parse(lines[0]??'');}catch{/* reported by the first test */}
test('helper differential ran every kind',()=>{
 assert.equal(done.stderr,'');
 assert.deepEqual(Object.keys(report).sort(),['adapter','b64','cook_code','cook_stream','depth','diagnostic','eq','fsencode','iso','json','pathfs','pathpure','pyops','readtext','repository','sweep','unquote','url','utf8']);
 for(const [kind,entry] of Object.entries(report))assert.ok(entry.cases>0,kind);
 console.log('helper differential cases: '+Object.values(report).reduce((sum,entry)=>sum+entry.cases,0));
});
for(const kind of Object.keys(report))test('CPython agrees: '+kind,()=>{
 assert.equal(report[kind].mismatches,0,lines.filter(line=>line.startsWith('WITNESS '+kind+' ')).join('\n'));
});
test('helper differential exit status',()=>{assert.equal(done.status,0);});
