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
 assert.deepEqual(Object.keys(report).sort(),['adapter','b64','cook_code','cook_stream','depth','diagnostic','eq','fsencode','iso','json','pathfs','pathpure','pyint','pyops','readtext','repository','sweep','tar','tardepth','targz','unquote','url','utf8']);
 for(const [kind,entry] of Object.entries(report))assert.ok(entry.cases>0,kind);
 console.log('helper differential cases: '+Object.values(report).reduce((sum,entry)=>sum+entry.cases,0));
});
for(const kind of Object.keys(report))test('CPython agrees: '+kind,()=>{
 assert.equal(report[kind].mismatches,0,lines.filter(line=>line.startsWith('WITNESS '+kind+' ')).join('\n'));
});
test('a damaged gzip container is never accepted, and every kind of disagreement with the lazy oracle reader is seen',()=>{
 const buckets=report.targz.buckets;
 assert.equal(buckets['TS ACCEPTS A DAMAGED CONTAINER'],0);
 for(const name of ['valid container','both refuse as unreadable','oracle accepts, TS refuses','oracle lets an uncaught class escape, TS refuses'])assert.ok(buckets[name]>0,name);
});
test('helper differential exit status',()=>{assert.equal(done.status,0);});
