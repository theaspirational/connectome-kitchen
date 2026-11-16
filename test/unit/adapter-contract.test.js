import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import {ADAPTER_CONTRACT} from '../../dist/cook.js';
// The name a kitchen gives a build holds the name of its adapter contract and no path (ruling D5, src/cook.ts). So the
// number in that name has to move when the adapter puts something else into a build, and nothing else may move it.
// test/corpus/adapter_contract.py holds the adapter's output on the public cases of the deploy corpus against the record
// kept under the present name: the recipe written or staged, the layout, the framework record, the lock after the
// adapter's post-processing, the modes, and how cook was called. Here: that record is the adapter's output, and the
// check is not one that passes whatever the adapter does.
const ROOT=path.resolve('.');
const scratch=path.join(ROOT,'.cache','unit-adapter-contract-'+process.pid);
test.before(()=>fs.mkdirSync(scratch,{recursive:true}));
test.after(()=>fs.rmSync(scratch,{recursive:true,force:true}));
const tool=(...args)=>spawnSync('python3',['test/corpus/adapter_contract.py',...args,'--node',process.execPath],{encoding:'utf8',timeout:300000});
const record=()=>JSON.parse(fs.readFileSync(path.join(ROOT,'test/corpus/adapter-contract',ADAPTER_CONTRACT.replace('/','-')+'.golden'),'utf8'));
// The adapter with one thing about what it leaves changed afterwards: adapter_seam.mjs, then the change.
const changed=(name,change)=>{
 const file=path.join(scratch,name+'.mjs');
 fs.writeFileSync(file,`import fs from 'node:fs';\nimport path from 'node:path';\nawait import(${JSON.stringify(path.join(ROOT,'test/corpus/adapter_seam.mjs'))});\nconst build=process.argv[process.argv.indexOf('--out')+1];\n${change}\n`);
 return file;
};
const CHANGES={
 'the mode of the layout':["fs.chmodSync(path.join(build,'cook-layout.json'),0o600);",'/cook-layout.json/mode'],
 'the bytes of the lock, with what it says left as it is':["const lock=path.join(build,'connectome.lock');fs.writeFileSync(lock,JSON.stringify(JSON.parse(fs.readFileSync(lock,'utf8'))));",'/connectome.lock/text'],
 'one more file in the build':["fs.writeFileSync(path.join(build,'more.json'),'{}');",'/more.json (only in the port)'],
};

test('the name of the adapter contract is one word with a number, and the record under it is of this adapter',()=>{
 assert.match(ADAPTER_CONTRACT,/^kitchen-cook-adapter\/[1-9][0-9]*$/u);
 const kept=record();
 assert.equal(kept.contract,ADAPTER_CONTRACT);
 assert.deepEqual(Object.keys(kept.cases).sort(),['example','example-replay','example-trial']);
 // What the record has to hold to be worth its name: a staged recipe kept, a recipe written, the framework record, the lock with the provenance added.
 const left=name=>Object.entries(kept.cases[name].left).map(([file,row])=>[path.basename(file),row]);
 for(const name of Object.keys(kept.cases)){
  assert.deepEqual([kept.cases[name].status,kept.cases[name].stderr,kept.cases[name].cook_calls.length,kept.cases[name].verify_cook_calls.length],[0,'',1,1],name);
  // Since /2 (ruling D6) the names the adapter adds to cook's environment are under the number: the record holds them, in every call.
  const names=kept.cases[name].cook_calls[0].environment_names;
  assert.deepEqual([names.includes('BUN_INSTALL_CACHE_DIR'),names.includes('COOK_PASS_ENV'),names.some(word=>word.startsWith('GIT_'))],[true,true,false],name);
  assert.ok(left(name).some(([leaf,row])=>leaf==='connectome.lock'&&typeof row.text==='string'&&row.mode),name);
  assert.ok(left(name).some(([leaf,row])=>leaf==='cook-layout.json'&&row.mode==='0644'),name);
 }
 assert.ok(left('example-trial').some(([leaf])=>leaf==='framework.lock.json')&&left('example-trial').some(([leaf])=>leaf==='cook-recipe.json'));
 assert.ok(Object.entries(kept.cases.example.given.build).some(([file,row])=>path.basename(file)==='cook-recipe.json'&&row.mode==='0600'));
 assert.ok(left('example').some(([leaf,row])=>leaf==='connectome.lock'&&row.text.includes('"provenance"')));
});

test('the adapter\'s output is the one recorded under the name of its contract',()=>{
 const done=tool('check');
 assert.equal(done.status,0,done.stdout+done.stderr);
 assert.deepEqual(done.stdout.trim().split('\n'),['example','example-replay','example-trial'].map(name=>`PASS ${name}: the adapter's output is the one recorded under ${ADAPTER_CONTRACT}`));
});

test('a change of what the adapter leaves fails the check until the number is raised, and no record is rewritten for it',()=>{
 for(const [what,[change,where]] of Object.entries(CHANGES)){
  const seam=changed('changed-'+Object.keys(CHANGES).indexOf(what),change);
  const done=tool('check','--case','example-replay','--seam',seam);
  assert.equal(done.status,1,what+done.stdout+done.stderr);
  const lines=done.stdout.trimEnd().split('\n');
  assert.ok(lines.some(line=>line.startsWith('  ADAPTER example-replay differs at /left/')&&line.endsWith(where)),what+done.stdout);
  assert.ok(lines.includes(`FAIL example-replay: the adapter's output is NOT the one recorded under ${ADAPTER_CONTRACT}`),what+done.stdout);
  assert.match(lines.at(-1),/raise the number of ADAPTER_CONTRACT in src\/cook\.ts and record under the new name/u);
 }
 const before=fs.readFileSync(path.join(ROOT,'test/corpus/adapter-contract',ADAPTER_CONTRACT.replace('/','-')+'.golden'),'utf8');
 for(const flags of [[],['--replace']]){
  const done=tool('record',...flags,'--seam',changed('changed-record',CHANGES['the mode of the layout'][0]));
  assert.equal(done.status,1,done.stdout+done.stderr);
  assert.match(done.stdout,/^REFUSED: the adapter's output is not the one recorded under \S+, with the cases and stand-ins as they were\. Raise the number/u);
 }
 assert.equal(fs.readFileSync(path.join(ROOT,'test/corpus/adapter-contract',ADAPTER_CONTRACT.replace('/','-')+'.golden'),'utf8'),before);
});
