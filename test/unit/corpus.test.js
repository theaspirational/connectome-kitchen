import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
// The public cases of the deploy corpus (test/corpus/README.md): two synthetic deployments through the Python oracle.
// What the oracle answers now is the committed golden, and every step kitchen has a port of answers as the oracle does
// on the same files. The cases of real deployments are kept outside this repository and are run by hand.
test('the synthetic deploy corpus: the oracle reproduces its goldens and the ported steps agree with it',()=>{
 const done=spawnSync('python3',['test/corpus/corpus.py','compare','--corpus','synthetic','--node',process.execPath],{encoding:'utf8',timeout:300000});
 assert.equal(done.status,0,done.stdout+done.stderr);
 const lines=done.stdout.trim().split('\n');
 assert.deepEqual(lines.map(line=>line.split(':')[0]),['PASS example','PASS example-trial'],done.stdout);
 for(const line of lines){
  const [,calls,equal,mismatch]=/: (\d+) ported calls, (\d+) equal, (\d+) mismatch; golden reproduced$/u.exec(line)??[];
  assert.ok(Number(calls)>=17&&calls===equal&&mismatch==='0',line);
 }
});
