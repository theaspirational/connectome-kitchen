import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
// The public cases of the deploy corpus (test/corpus/README.md): synthetic deployments through the Python oracle. The
// third is the first again with cook's part replayed from the files of a build in the shape a real cook leaves: its
// request and its name from the case, and the lock after the adapter's post-processing, are the bytes of that build.
// What the oracle answers now is the committed golden, and every step kitchen has a port of answers as the oracle does
// on the same files. Then the TS engine (deploy, cook) runs each whole case at the root the oracle ran at, and its
// record is the oracle's: the role's view, plan, apply, the tree apply left, the second apply, a later release beside
// the current one, the first release taken back, the deployment installed and not started, the switch between that
// and a start in both directions through the contract with a stand-in unit verifier (also for a unit without an Install
// section, where only the engine's marker tells the two apart), a verifier that refuses, and the refusals.
// The engine is run twice: with the oracle's name for the build, where the record is the oracle's as text, and as a kitchen
// names it, by its adapter contract (ruling D5), where it is the oracle's but for the adapter words and the digests that
// follow the name. Last, the role's build is taken over by a kitchen (one build, one cook run, one restart) and the
// engine is run from another Node path and install location (nothing).
// The cases of real deployments are kept outside this repository and are run by hand.
test('the synthetic deploy corpus: the oracle reproduces its goldens, and the ported steps and the TS engine agree with it',()=>{
 const done=spawnSync('python3',['test/corpus/corpus.py','compare','--corpus','synthetic','--node',process.execPath],{encoding:'utf8',timeout:600000});
 assert.equal(done.status,0,done.stdout+done.stderr);
 const lines=done.stdout.trim().split('\n');
 assert.deepEqual(lines.map(line=>line.split(':')[0]),['PASS example','PASS example-replay','PASS example-trial'],done.stdout);
 for(const line of lines){
  const [,calls,equal,mismatch]=/: (\d+) ported calls, (\d+) equal, (\d+) mismatch; golden reproduced; engine equal: role view, plan, apply, tree, re-apply, rebuild, release back, install only, activation switch, unit verifier, refusals; build named by kitchen-cook-adapter\/\d+: the oracle's record but for the adapter words and [1-9]\d* digests that follow the name; taking over: one new build, cook run and restart, then none from another Node path and install location \(kitchen-cook-adapter\/\d+\)$/u.exec(line)??[];
  assert.ok(Number(calls)>=17&&calls===equal&&mismatch==='0',line);
 }
});
