import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {spawnSync} from 'node:child_process';
const manifest=JSON.parse(fs.readFileSync('package.json','utf8'));
test('bin and exports name compiled files only',async()=>{
 const targets=[manifest.bin.kitchen,...Object.values(manifest.exports).flatMap(entry=>Object.values(entry))];
 assert.equal(targets.length,5);
 for(const target of targets){assert.match(target,/^(?:\.\/)?dist\//u);assert.ok(fs.statSync(target).isFile(),target);}
 // Self-reference resolves through the exports map, as a consumer would.
 const root=await import('connectome-kitchen'),contract=await import('connectome-kitchen/contract');
 assert.deepEqual(Object.keys(root).sort(),['CONTRACT','VERSION','json']);
 assert.deepEqual(Object.keys(root.json).sort(),['Float','Int','canonical','compare','dumps','loads','spaced','unicode']);
 assert.deepEqual(Object.keys(contract).sort(),['CONTRACT','VERSION']);
 assert.equal(contract.CONTRACT,'connectome.kitchen/v1');assert.equal(contract.VERSION,manifest.version);
});
test('compiled CLI scaffold refuses with one receipt line and exit 2',()=>{
 const result=spawnSync(process.execPath,[manifest.bin.kitchen,'plan','--input','/nonexistent.json'],{encoding:'utf8',env:{}});
 assert.equal(result.status,2);assert.equal(result.stderr,'');
 assert.equal(result.stdout,'{"contract":"connectome.kitchen/v1","command":null,"core":{"error_code":"E_CONTRACT","msg":"incubation scaffold; engine unavailable"},"meta":{"kitchen":"0.0.0-incubation","artifact_sha256":null}}\n');
 assert.equal(fs.readFileSync(manifest.bin.kitchen,'utf8').split('\n')[0],'#!/usr/bin/env node');
});
test('npm test is the fast suite; the qualification run is explicit, and no install runs it',()=>{
 const scripts=manifest.scripts;
 // `npm test` builds and runs test/unit only. The heavy vectors live in test/qualify and run through `npm run qualify`.
 assert.equal(scripts.pretest,'npm run build');
 assert.equal(scripts.test,'node --test test/unit/*.test.js');
 assert.equal(scripts.qualify,'python3 scripts/qualify');
 assert.deepEqual(fs.readdirSync('test/qualify').filter(name=>name.endsWith('.test.js')).sort(),['port-budget.test.js','port-large.test.js']);
 // No unit file starts the two heavy differentials or the qualification run: none holds their path as an argument.
 for(const name of fs.readdirSync('test/unit').filter(name=>name.endsWith('.test.js')&&name!=='package.test.js')){
  assert.doesNotMatch(fs.readFileSync('test/unit/'+name,'utf8'),/['"`](?:test\/differential\/port_(?:budget|large)\.py|scripts\/qualify)['"`]|test\/qualify\//u,name);
 }
 // npm runs these names by itself on install, pack or publish: none is defined, so no install can start a test.
 for(const hook of ['preinstall','install','postinstall','prepare','prepublish','prepublishOnly','prepack','postpack','dependencies']){
  assert.equal(scripts[hook],undefined,hook);
 }
 for(const [name,line] of Object.entries(scripts))if(name!=='qualify')assert.doesNotMatch(line,/qualify/u,name);
 // The offline installer runs no script of any package, and neither it nor its check names a test.
 const installer=fs.readFileSync('scripts/install-offline.sh','utf8');
 assert.match(installer,/npm ci --offline --ignore-scripts /u);
 for(const name of ['scripts/install-offline.sh','scripts/check-offline-install.sh','scripts/build'])assert.doesNotMatch(fs.readFileSync(name,'utf8'),/qualify|npm (?:run )?test|node --test/u,name);
});
