import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {spawnSync} from 'node:child_process';
import * as cook from '../../dist/cook.js';
import * as deploy from '../../dist/deploy.js';
import {ContractError,dispatch} from '../../dist/engine.js';
import {realHost} from '../../dist/io/host.js';
import {dict,dumps,canonical,indented} from '../../dist/pylike/pydump.js';
import {loads} from '../../dist/pylike/pyjson.js';
// What the deploy corpus (corpus.test.js) cannot reach of the deploy and cook port: the corpus puts a seam where the
// adapter is started and where the cook checkout is verified, and it never meets a held lock. Where the oracle has an
// answer, it is asked and the two answers are compared; nothing expected is written here except for what is TS only.
const ROOT=path.resolve('.');
const scratch=path.join(ROOT,'.cache','unit-engine-'+process.pid);
test.before(()=>fs.mkdirSync(scratch,{recursive:true}));
test.after(()=>fs.rmSync(scratch,{recursive:true,force:true}));
const python=(source,...args)=>spawnSync('python3',['-c','import sys\nsys.dont_write_bytecode=True\nsys.path.insert(0,'+JSON.stringify(path.join(ROOT,'oracle/module_utils'))+')\n'+source,...args],{encoding:'utf8'});

test('the adapter entry refuses as the oracle\'s does: status and the one line on stderr',()=>{
 const missing=path.join(scratch,'nowhere');
 for(const args of [[],['--spec',missing],['--spec',missing,'--out',missing,'--state',missing,'--entrypoint',missing,'--extra','x'],['--spec'],
  ['--spec',missing+'/builds/x/deployment.json','--out',missing+'/builds/x','--state',missing+'/state','--entrypoint',missing+'/bin/cook'],
  ['--spec=relative','--out=relative','--state=relative','--entrypoint=relative']]){
  const oracle=spawnSync('python3',[path.join(ROOT,'oracle/scripts/cook-adapter.py'),...args],{encoding:'utf8',env:{PATH:process.env.PATH,PYTHONDONTWRITEBYTECODE:'1'}});
  const port=spawnSync(process.execPath,[path.join(ROOT,'dist/cook-adapter.js'),...args],{encoding:'utf8',env:{PATH:process.env.PATH}});
  assert.ok(oracle.status===1||oracle.status===2,oracle.stderr);
  assert.deepEqual([port.status,port.stderr,port.stdout],[oracle.status,oracle.stderr,oracle.stdout],JSON.stringify(args));
 }
});

test('verify_cook asks git as the oracle does: a checkout at another commit, and one without the reviewed tree',()=>{
 const checkout=path.join(scratch,'cook');fs.mkdirSync(path.join(checkout,'bin'),{recursive:true});
 const entrypoint=path.join(checkout,'bin','cook');fs.writeFileSync(entrypoint,'#!/bin/sh\nexit 64\n',{mode:0o755});
 const env={PATH:process.env.PATH,HOME:scratch};
 const git=(...args)=>{const done=spawnSync('git',['-C',checkout,'-c','user.name=unit','-c','user.email=unit@example.invalid','-c','init.defaultBranch=main',...args],{encoding:'utf8',env});assert.equal(done.status,0,done.stderr);return done.stdout.trim();};
 git('init','-q');git('add','.');git('commit','-q','-m','unit');
 const head=git('rev-parse','HEAD');
 const ask=`import json,connectome_cook\ntry:\n connectome_cook.verify_cook(sys.argv[1],sys.argv[2],json.loads(sys.argv[3]));print(json.dumps(None))\nexcept connectome_cook.AdapterError as error:print(json.dumps([str(error),error.public()]))`;
 for(const pin of ['0'.repeat(39)+'1',head]){
  const oracle=python(ask,entrypoint,pin,JSON.stringify(env));assert.equal(oracle.status,0,oracle.stderr);
  let port=null;
  try{cook.verify_cook(entrypoint,pin,env);}catch(error){if(!(error instanceof cook.AdapterError))throw error;port=[error.message,error.public()];}
  assert.notEqual(port,null);
  assert.deepEqual(port,JSON.parse(oracle.stdout),pin);
 }
});

test('the host lock is the lock another process meets, and it ends when the descriptor is closed',()=>{
 const file=path.join(scratch,'.deploy-lock');
 const held=()=>spawnSync('python3',['-c','import fcntl,sys\nwith open(sys.argv[1],"a") as stream:\n try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)\n except BlockingIOError:sys.exit(7)',file]).status;
 const fd=fs.openSync(file,'a',0o666);
 try{assert.equal(held(),0);realHost.lock(fd);assert.equal(held(),7);}finally{fs.closeSync(fd);}
 assert.equal(held(),0);
});

test('a configuration with an active catalog is refused before anything is read or written',()=>{
 const config=loads('{"name":"example","state_dir":"/nonexistent/state","catalog":{"node":{"kind":"runtime"}}}');
 assert.equal(deploy.catalog_active(config),true);
 assert.throws(()=>deploy.validate_config(config),error=>error instanceof deploy.DeployError&&error.public().error_code==='E_DEPLOY_FAILED');
 // With a recipe file the catalog is not active, in the oracle as here.
 assert.equal(deploy.catalog_active(loads('{"recipe_file":{},"catalog":{"node":{}}}')),false);
});

test('an envelope that is not contract v1 plan or apply is a contract error, and spawn is not served here',()=>{
 const engines=dict({cook:dict({entrypoint:'/e',runtime:'/r',bun:'/b',node:'/n'})});
 const envelope=(command,request)=>dict({contract:'connectome.kitchen/v1',command,request});
 for(const data of [null,dict({}),envelope('spawn',dict({action:'validate'})),envelope('plan',dict({engines,unknown:true})),
  envelope('plan',dict({engines})),envelope('plan',dict({engines,layers:[],config:dict({})})),envelope('apply',dict({engines,config:dict({}),phase:'later'})),
  envelope('plan',dict({config:dict({}),engines:dict({cook:dict({entrypoint:'e',runtime:'/r',bun:'/b',node:'/n'})})}))]){
  assert.throws(()=>dispatch(data),ContractError);
 }
 // A request that is well formed and names nothing usable is the engine's refusal, with the phase it stopped in.
 const [receipt,status]=dispatch(envelope('plan',dict({engines,config:dict({name:'Not An Identifier'}),root:'/nonexistent/root',unit_dir:'/nonexistent/units',versions:dict({})})));
 assert.equal(status,1);
 assert.equal(canonical(receipt.get('core')),'{"error_code":"E_DEPLOY_FAILED","msg":"E_DEPLOY_FAILED: Deployment failed; previous build retained/restored where possible (output redacted).","phase":"prepare"}');
});

test('the JSON writer writes what json.dumps writes, in the three shapes the engine uses',()=>{
 const source='{"b":[1,1.0,1e16,1e-05,true,null,"\\u00e9\\ud83d\\ude00\\u007f\\n\\"\\\\"],"a":{},"c":[],"d":{"z":-0.0,"y":[{"x":12345678901234567890}]}}';
 const oracle=python('import json\nvalue=json.loads(sys.argv[1])\nprint(json.dumps([json.dumps(value),json.dumps(value,sort_keys=True,separators=(",",":")),json.dumps(value,sort_keys=True,indent=2)]))',source);
 assert.equal(oracle.status,0,oracle.stderr);
 const value=loads(source);
 assert.deepEqual([dumps(value),canonical(value),indented(value)],JSON.parse(oracle.stdout));
});

// The end-to-end witness ruling M1 moved here (test/port-rulings.golden, obligation archive.L140.4). The mutant takes
// `need(isinstance(manifest, dict), ...)` out of `archive_manifest`. Inside the archive module only a runtime error tells
// it from the original. At the deployment boundary that error is caught, and the public code changes: the original
// refuses a package manifest that is a list with the archive module's own code, the mutant ends in the generic one.
// Both engines are asked, each with and without its mutant; no code is written down here.
test('archive.L140.4 end to end: at the deployment boundary the mutant gives another public code than the original, in both engines',async()=>{
 const world=path.join(scratch,'witness');
 const directory=(name,mode=0o755)=>{const target=path.join(world,name);fs.mkdirSync(target,{recursive:true});fs.chmodSync(target,mode);return target;};
 fs.mkdirSync(world,{recursive:true});fs.chmodSync(scratch,0o755);fs.chmodSync(world,0o755);
 for(const name of ['deploy','units','state','inputs'])directory(name);
 const secrets=path.join(directory('secrets',0o700),'agent.json');fs.writeFileSync(secrets,'{}',{mode:0o600});fs.chmodSync(secrets,0o600);
 const archive=path.join(world,'inputs','package-1.0.0.tgz');
 const made=python('import hashlib,io,tarfile\nbuffer=io.BytesIO()\nwith tarfile.open(fileobj=buffer,mode="w:gz") as tar:\n info=tarfile.TarInfo("package/package.json");data=b"[]";info.size=len(data);tar.addfile(info,io.BytesIO(data))\nopen(sys.argv[1],"wb").write(buffer.getvalue());print(hashlib.sha256(buffer.getvalue()).hexdigest())',archive);
 assert.equal(made.status,0,made.stderr);fs.chmodSync(archive,0o644);
 const pin=index=>String(index).repeat(40);
 const request={base:path.join(world,'deploy'),root:path.join(world,'deploy','agent'),unit_dir:path.join(world,'units'),
  versions:{runtime:{'connectome-host':pin(1),'agent-framework':pin(2),cook:pin(3)}},
  engines:{cook:{entrypoint:'/nonexistent/bin/cook',runtime:'/nonexistent/bun',bun:'/nonexistent/bun',node:'/nonexistent/node'}},
  config:{name:'agent',agent:{},state_dir:path.join(world,'state','agent'),secrets_file:secrets,
   cook:{host_repo:'/nonexistent/host',connector_state:{},host_dependencies:[{name:'example-package',archive,kind:'artifact',version:'1.0.0',sha256:made.stdout.trim()}]}}};
 const data=JSON.stringify({contract:'connectome.kitchen/v1',command:'plan',request});
 // The oracle, from a copy of its sources, so that the mutant is one changed line of a file nothing else reads.
 const oracleCore=mutant=>{
  const copy=path.join(scratch,mutant?'oracle-mutant':'oracle-original');
  for(const part of ['module_utils','shim','scripts','tests'])fs.cpSync(path.join(ROOT,'oracle',part),path.join(copy,part),{recursive:true,filter:source=>!source.includes('__pycache__')});
  if(mutant){
   const file=path.join(copy,'module_utils','connectome_archive.py'),lines=fs.readFileSync(file,'utf8').split('\n');
   assert.equal(lines[139],"    need(isinstance(manifest, dict), 'archive manifest must be a mapping')");
   lines[139]="    need(True, 'archive manifest must be a mapping')";fs.writeFileSync(file,lines.join('\n'));
  }
  const done=spawnSync('python3',['-c','import json,sys\nsys.dont_write_bytecode=True\nsys.path[:0]=[sys.argv[1]+"/shim",sys.argv[1]+"/module_utils",sys.argv[1]+"/tests"]\nimport kitchen_v1\nfrom deploy_fixtures import safe_ancestors\nwith safe_ancestors(sys.argv[2]):\n envelope,status=kitchen_v1.dispatch(json.loads(sys.argv[3]))\nprint(json.dumps([status,envelope["core"]],sort_keys=True))',copy,world,data],{encoding:'utf8'});
  assert.equal(done.status,0,done.stderr);return JSON.parse(done.stdout);
 };
 // The port, from a copy of the build; the mutant is the one test/mutants/ts-anchors.golden names for this id.
 const portCore=async mutant=>{
  const copy=path.join(scratch,mutant?'dist-mutant':'dist-original');
  fs.cpSync(path.join(ROOT,'dist'),copy,{recursive:true});
  if(mutant){
   const file=path.join(copy,'archive.js'),source=fs.readFileSync(file,'utf8');
   assert.equal(source.split('need(isDict(manifest),').length,2);fs.writeFileSync(file,source.replace('need(isDict(manifest),','need(true,'));
  }
  const engine=await import(path.join(copy,'engine.js')),host=await import(path.join(copy,'io','host.js')),json=await import(path.join(copy,'pylike','pyjson.js')),dump=await import(path.join(copy,'pylike','pydump.js'));
  const above=new Set();for(let current=path.dirname(world);;current=path.dirname(current)){above.add(current);if(current==='/')break;}
  const model={...host.realHost,lstat:target=>above.has(target)?{mode:fs.constants.S_IFDIR|0o755,uid:0,gid:0}:host.realHost.lstat(target)};
  const [receipt,status]=engine.dispatch(json.loads(data),null,{host:model});
  return [status,JSON.parse(dump.canonical(receipt.get('core')))];
 };
 const oracle=oracleCore(false),oracleMutant=oracleCore(true),port=await portCore(false),portMutant=await portCore(true);
 assert.deepEqual(port,oracle);assert.deepEqual(portMutant,oracleMutant);
 assert.equal(oracle[0],1);assert.equal(oracleMutant[0],1);
 assert.notEqual(oracle[1].error_code,oracleMutant[1].error_code);
 assert.notEqual(port[1].error_code,portMutant[1].error_code);
 // The original's code is the archive module's refusal, not the generic failure a runtime error ends in.
 assert.notEqual(oracle[1].error_code,'E_DEPLOY_FAILED');assert.equal(oracleMutant[1].error_code,'E_DEPLOY_FAILED');
});
