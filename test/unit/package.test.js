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
