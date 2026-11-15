import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {Deployment} from '../../dist/deploy.js';
import {realHost} from '../../dist/io/host.js';
import {dumps} from '../../dist/pylike/pydump.js';
import {loads} from '../../dist/pylike/pyjson.js';
// The oracle's own tests of a switch of activation on one build, on the TS engine's class:
//   oracle/tests/test_deploy.py:122  test_install_only_then_start_reuses_release_and_lock
//   oracle/tests/test_deploy.py:140  test_install_only_then_start_without_install_section
// and the way back, which the oracle has no test of. The fixtures are the oracle's (its example configuration, its cook
// stub, its fake service manager and its verifier double), and so are the assertions, line for line where there is one.
// What decides here is `same_activation` in `Deployment.activate` (src/deploy.ts; deploy.py:1251): with a unit that has
// an `[Install]` section the enable links differ between the two activations and force the change by themselves; with
// a unit that has none, the marker `state/activation` is all that does.
const ROOT=path.resolve('.');
const ORACLE=path.join(ROOT,'oracle');
const scratch=path.join(ROOT,'.cache','unit-activation-'+process.pid);
test.before(()=>{fs.mkdirSync(scratch,{recursive:true});fs.chmodSync(scratch,0o755);});
test.after(()=>fs.rmSync(scratch,{recursive:true,force:true}));

// `example()` of test_deploy.py:26-32, by the oracle's own merge, and the interpreter its fixtures run on.
const asked=spawnSync('python3',['-c',`import json,sys
sys.dont_write_bytecode=True
from pathlib import Path
import yaml
root=Path(sys.argv[1])
sys.path.insert(0,str(root/'module_utils'))
from connectome_config import merge_layers,public_catalog
read=lambda name:yaml.safe_load((root/name).read_text())
catalog={}
for file in (root/'connectors').glob('*.yml'):catalog.update(yaml.safe_load(file.read_text()))
config=merge_layers([public_catalog(read('defaults/agent.yml'),catalog),read('examples/private-skeleton/overrides/agent.yml'),{},read('examples/private-skeleton/agents/example/agent.yml')])
print(json.dumps({'config':config,'versions':read('versions.yml'),'python':sys.executable}))`,ORACLE],{encoding:'utf8'});
assert.equal(asked.status,0,asked.stderr);
const example=JSON.parse(asked.stdout);

/** `setUp`, `deployment`, `apply`, `track_units`, `lifecycle_calls` of `DeployTests` (test_deploy.py:45-94). */
function fixture(label){
 const tmp=path.join(scratch,label);
 fs.mkdirSync(tmp);fs.chmodSync(tmp,0o755);
 const root=path.join(tmp,'agent'),units=path.join(tmp,'units');
 const settings={...example.config,state_dir:path.join(tmp,'runtime-state'),secrets_file:path.join(tmp,'connectome-secrets','example.json')};
 fs.mkdirSync(path.dirname(settings.secrets_file),{mode:0o700});fs.chmodSync(path.dirname(settings.secrets_file),0o700);
 fs.writeFileSync(settings.secrets_file,'{}',{mode:0o600});fs.chmodSync(settings.secrets_file,0o600);
 const cook=[example.python,path.join(ORACLE,'tests/fixtures/cook.py'),'{recipe}','{build_dir}','{state_dir}'];
 const systemctl=[example.python,path.join(ORACLE,'tests/fixtures/systemctl.py'),root];
 const analyze=[example.python,path.join(ORACLE,'tests/fixtures/analyze.py')];
 const provider={EXAMPLE_SECRET:'synthetic-value',UNUSED_SECRET:'never-requested'};
 // `safe_ancestors` of oracle/tests/deploy_fixtures.py: only the directories above the fixture are shown as root's, 0755.
 const above=new Set();for(let current=path.dirname(tmp);;current=path.dirname(current)){above.add(current);if(current==='/')break;}
 const host={...realHost,lstat:target=>above.has(target)?{mode:fs.constants.S_IFDIR|0o755,uid:0,gid:0}:realHost.lstat(target)};
 const deployment=(activation,check=false)=>new Deployment({root,unit_dir:units,config:loads(JSON.stringify({...settings,activation})),versions:loads(JSON.stringify(example.versions)),
  cook_command:cook,systemctl,check,analyze_command:analyze,host});
 const apply=dep=>{
  const plan=dep.prepare();
  return dep.activate(new Map(plan.get('secret_names').map(name=>[name,provider[name]])));
 };
 const lifecycle=()=>[path.join(root,'systemctl-calls'),path.join(tmp,'preflight-systemctl-calls')].filter(file=>fs.existsSync(file))
  .flatMap(file=>fs.readFileSync(file,'utf8').split('\n').filter(Boolean).map(line=>JSON.parse(line)));
 const verbs=()=>lifecycle().map(call=>call.find(word=>!word.startsWith('-')));
 // `track_units`: the fake answers `inactive` until it is told to start a unit.
 fs.writeFileSync(path.join(tmp,'track-unit-state'),'');fs.writeFileSync(path.join(tmp,'unit-states.json'),'{}');
 return {tmp,root,units,deployment,apply,lifecycle,verbs};
}
const isLink=file=>fs.lstatSync(file,{throwIfNoEntry:false})?.isSymbolicLink()===true;
const has=(calls,wanted)=>calls.some(call=>JSON.stringify(call)===JSON.stringify(wanted));
/** A unit with no `[Install]` section put in the lock's place of the build's own, as test_deploy.py:145-150 does after `prepare`. */
function otherUnit(dep){
 const source=path.join(dep.build,'other.service');
 fs.writeFileSync(source,'[Service]\nExecStart=/bin/true\n');
 const lock=path.join(dep.build,'connectome.lock'),data=loads(fs.readFileSync(lock));
 data.set('units',[new Map([['path','other.service'],['sha256',createHash('sha256').update(fs.readFileSync(source)).digest('hex')]])]);
 fs.writeFileSync(lock,dumps(data));
}

test('install_only then start reuses the release and the lock (test_deploy.py:122)',()=>{
 const {root,units,deployment,apply,lifecycle}=fixture('reuse');
 const staged=deployment('install_only');
 apply(staged);
 const lock=fs.readFileSync(path.join(staged.build,'connectome.lock')),cookCalls=fs.readFileSync(path.join(root,'cook-calls'));
 const start=deployment('start');
 assert.equal(start.build,staged.build);
 assert.equal(start.prepare().get('changed'),false);
 assert.equal(start.activate(new Map()).get('changed'),true);
 assert.deepEqual(fs.readFileSync(path.join(start.build,'connectome.lock')),lock);
 assert.deepEqual(fs.readFileSync(path.join(root,'cook-calls')),cookCalls);
 assert.ok(isLink(path.join(units,'default.target.wants','connectome-example.service')));
 assert.ok(has(lifecycle(),['restart','connectome-example.service']));
 assert.equal(apply(deployment('start')).get('changed'),false);
});

test('install_only then start without an Install section: the marker alone makes it a change (test_deploy.py:140)',()=>{
 const {root,units,deployment,lifecycle}=fixture('no-install');
 const dep=deployment('install_only');
 dep.prepare();
 otherUnit(dep);
 dep.activate(new Map());
 assert.equal(fs.readFileSync(path.join(root,'state','activation'),'utf8'),'install_only\n');
 const started=deployment('start');
 // The plan says so first: nothing but the marker differs, and check mode answers that the request changes something.
 assert.equal(deployment('start',true).activate().get('changed'),true);
 assert.equal(started.prepare().get('changed'),false);
 assert.equal(started.activate(new Map()).get('changed'),true);
 assert.ok(has(lifecycle(),['restart','other.service']));
 assert.equal(fs.readFileSync(path.join(root,'cook-calls'),'utf8').split('\n').filter(Boolean).length,1);
 // Beyond the oracle's test: nothing links to the unit, the marker is gone, and the same request again changes nothing.
 assert.equal(fs.existsSync(path.join(units,'default.target.wants')),false);
 assert.equal(fs.existsSync(path.join(root,'state','activation')),false);
 assert.equal(deployment('start').activate(new Map()).get('changed'),false);
});

test('start then install_only without an Install section: refused while the unit is active, a change once it is stopped',()=>{
 const {tmp,root,deployment,lifecycle,verbs}=fixture('back');
 const dep=deployment('start');
 dep.prepare();
 otherUnit(dep);
 assert.equal(dep.activate(new Map()).get('changed'),true);
 assert.ok(has(lifecycle(),['restart','other.service']));
 const before=lifecycle().length;
 assert.throws(()=>deployment('install_only').activate(new Map()),error=>error.public().error_code==='E_UNIT_ACTIVE'&&error.public().unit==='other.service');
 assert.equal(fs.existsSync(path.join(root,'state','activation')),false);
 // An operator stops the unit; the engine never does under install_only.
 fs.writeFileSync(path.join(tmp,'unit-states.json'),JSON.stringify({'user:other.service':'inactive'}));
 const staged=deployment('install_only');
 assert.equal(deployment('install_only',true).activate().get('changed'),true);
 assert.equal(staged.prepare().get('changed'),false);
 const result=staged.activate(new Map());
 assert.deepEqual([result.get('changed'),result.get('activation'),result.get('unit_state')],[true,'install_only','inactive']);
 assert.equal(fs.readFileSync(path.join(root,'state','activation'),'utf8'),'install_only\n');
 assert.deepEqual(verbs().slice(before).filter(verb=>['enable','start','restart','stop','disable'].includes(verb)),[]);
 assert.equal(deployment('install_only').activate(new Map()).get('changed'),false);
 assert.equal(fs.readFileSync(path.join(root,'cook-calls'),'utf8').split('\n').filter(Boolean).length,1);
});
