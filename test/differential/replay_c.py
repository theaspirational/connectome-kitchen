#!/usr/bin/env python3
"""Replay recorded kind-C adapter CLI entries through the contract shim.

Every recorded C entry is one `connectome_cook.main` call that the frozen test
made under a patched `adapt`. Here nothing in the engine or the adapter is
patched. One `apply` request (phase `prepare`) goes through the shim; the shim
starts the real `scripts/cook-adapter.py`; the adapter verifies a checkout of
the reviewed cook commit and runs the cook command line. The only fake is the
named `runtime` executable (the program the adapter runs cook with), which
fails or writes a lock the way the scenario says.

A fixed projection turns the shim receipt into the adapter's stdout, stderr
and exit status. An entry counts as replayed only if
  - the projected bytes, put back into the record with the entry's own argv,
    test and function fields, hash to the committed `raw_sha256`; and
  - calling the unpatched `adapt` on the same build fails with the same kind of
    exception the frozen test injected (a coded SafeError, a LockError, or
    another exception), so each entry exercises the same branch of `main`.

Entries whose injected failure the unpatched adapter can never produce are
listed as unreachable; they are not replayed and not relabelled.

Prerequisite outside this repository: a local clone that holds the reviewed
cook commit (`--cook-source`). Nothing is fetched. The scenario root is the
fixed `.cache/scenario-root`; the directories above it are modelled as
root-owned 0755 through the frozen suite's own `safe_ancestors` seam, because
every directory of this checkout is group-writable and the engine refuses that.
"""
import argparse, gzip, hashlib, importlib.util, io, json, os, shutil, subprocess, sys, tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SCENARIO=ROOT/'.cache/scenario-root'
SENTINEL='synthetic-private-argument'
ARGV=[item for flag in ('spec','out','state','runtime','entrypoint','bun-path','node-path') for item in ('--'+flag,SENTINEL)]
FRAMEWORK_PIN='a'*40;HOST_PIN='c'*40
# Codes only the deployment engine raises. The unpatched adapter has no path to
# them, so a patched `adapt` is the only way the frozen test could inject them.
DEPLOY_ONLY=('E_UNIT_VERIFY','E_UNIT_ACTIVE','E_UNIT_EXCLUSIVE','E_ANCESTOR_WRITABLE','E_EXTERNAL_COMMAND','E_EXTERNAL_ACCESS','E_SYSTEMD_PATH','E_ENVIRONMENT_FILE','E_SECRETS_MODE')
# `validate_config`, `resolve_pins` and `environment_names` are shared: the adapter
# re-runs them on the spec the engine wrote after passing the same checks.
SAME_VALIDATION=('E_EXTERNAL_DATA_ARG','E_DEPLOY_FAILED')
RUNTIME=r'''#!%s
"""Named fake: stands in for the runtime that executes cook. Mode comes from a file."""
import hashlib, json, sys
from pathlib import Path
here=Path(__file__).resolve().parent;mode=(here/'mode').read_text().strip();kind,_,value=mode.partition(':')
with (here.parent/'fake-tools.jsonl').open('a') as stream:stream.write(json.dumps({'tool':'runtime','argv':sys.argv[1:],'stdin_tty':sys.stdin.isatty()})+'\n')
if kind=='fail':
 print((value+': ' if value else '')+'synthetic-private-argument',file=sys.stderr);print('synthetic-private-argument');sys.exit(1)
if kind=='lock':
 args=sys.argv[1:];recipe=Path(args[2]);out=Path(args[args.index('--out')+1]);layout=json.loads(Path(args[args.index('--layout')+1]).read_text());pin=args[args.index('--ch-ref')+1]
 (out/'run.sh').write_text('#!/bin/sh\nexit 0\n');(out/'run.sh').chmod(0o755);digest=hashlib.sha256(recipe.read_bytes()).hexdigest()
 lock={'version':2,'backend':'host','state':{'path':layout['state']},'cook':{'version':'0.0.0-test-only'},'recipe':{'path':str(recipe),'sha256':digest},'recipePath':str(recipe),'createdAt':'2026-11-09T00:00:00.000Z','build':{'id':digest,'dir':str(out)},'connectomeHost':{'url':'https://example.invalid/runtime.git','ref':pin,'commit':pin},'components':[],'localExtensions':[],'requirements':[],'credentials':[],'outputs':[],'launch':{'kind':'script','script':'run.sh'}}
 if value=='layout-kept':lock['layout']=layout
 (out/'connectome.lock').write_text(json.dumps(lock))
sys.exit(0)
'''

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)

def request_digest(data):
 # Host-specific values (checkout location, the platform shell's digest) are named, not hashed.
 text=canonical(data).replace(str(ROOT),'${REPO}')
 platform=data['request']['versions'].get('platform',{})
 for value in platform.values():text=text.replace(value,'${PLATFORM_DIGEST}')
 return hashlib.sha256(text.encode()).hexdigest()

def scenarios(cook_codes):
 """name -> (fake runtime mode, config edit, archive edit, expected adapter failure)."""
 table={}
 for code in sorted(cook_codes):table['cook:'+code]=('fail:'+code,None,None,'SafeError:E_COOK_FAILED:'+code)
 table['engine:E_COOK_FAILED']=('fail:',None,None,'SafeError:E_COOK_FAILED')
 table['engine:E_ARCHIVE_HASH']=('fail:','wrong-archive-digest',None,'SafeError:E_ARCHIVE_HASH')
 table['engine:E_ARCHIVE_IDENTITY']=('fail:',None,'other-name','SafeError:E_ARCHIVE_IDENTITY')
 table['engine:E_ARCHIVE_GITHEAD']=('fail:',None,'other-head','SafeError:E_ARCHIVE_GITHEAD')
 table['engine:E_BUDGETS_UNSUPPORTED']=('fail:','budgets',None,'SafeError:E_BUDGETS_UNSUPPORTED')
 table['engine:E_ADAPTER_INPUT']=('fail:','no-connector-state',None,'SafeError:E_ADAPTER_INPUT')
 table['engine:E_LOCK_STATE_PATH']=('lock:layout-dropped',None,None,'SafeError:E_LOCK_STATE_PATH')
 table['engine:E_LOCK_INVALID']=('lock:layout-kept',None,None,'SafeError:E_LOCK_INVALID')
 # Catalog mode: the engine accepts the request; only the adapter checks that the
 # cook checkout itself lies inside the agent tree, and refuses with the catalog code.
 table['engine:E_CATALOG_INVALID']=('fail:','catalog-checkout-outside-agent',None,'SafeError:E_CATALOG_INVALID')
 table['fallback:LockError']=('exit0',None,None,'LockError')
 # A connector `env` that is not a mapping passes the engine's own validation
 # and makes the unpatched adapter raise AttributeError: an exception that is
 # neither SafeError nor LockError, as the frozen test's injected RuntimeError.
 table['fallback:Exception']=('fail:','connector-env-not-a-mapping',None,'Exception')
 return table

def archive(path,edit):
 manifest={'name':'@animalabs/agent-framework','version':'1.0.0','main':'index.js','gitHead':FRAMEWORK_PIN}
 if edit=='other-name':manifest['name']='example-other-package'
 if edit=='other-head':manifest['gitHead']='b'*40
 raw=io.BytesIO()
 with tarfile.open(fileobj=raw,mode='w',format=tarfile.USTAR_FORMAT) as tar:
  for name,value in (('package.json',json.dumps(manifest)),('index.js','export const example = true;\n')):
   content=value.encode();member=tarfile.TarInfo('package/'+name);member.size,member.mode=len(content),0o644;tar.addfile(member,io.BytesIO(content))
 packed=gzip.compress(raw.getvalue(),mtime=0);path.write_bytes(packed);return {'path':str(path),'sha256':hashlib.sha256(packed).hexdigest()}

def prepare_inputs(cook_source,commit):
 """Scenario inputs that no request changes: the reviewed cook checkout and the fake runtime."""
 shutil.rmtree(SCENARIO,ignore_errors=True);SCENARIO.mkdir(mode=0o700,parents=True)
 quiet={'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL,'check':True}
 subprocess.run(['git','clone','--quiet','--no-hardlinks','--no-checkout',str(cook_source),str(SCENARIO/'cook')],**quiet)
 subprocess.run(['git','-C',str(SCENARIO/'cook'),'checkout','--detach','-q',commit],**quiet)
 (SCENARIO/'bin').mkdir();runtime=SCENARIO/'bin/runtime';runtime.write_text(RUNTIME%sys.executable);runtime.chmod(0o755)
 (SCENARIO/'host').mkdir();(SCENARIO/'inputs').mkdir()

def reset(mode,archive_edit):
 for name in ('agent','units','runtime-state','connectome-secrets','fake-tools.jsonl','preflight-systemctl-calls'):
  path=SCENARIO/name
  if path.is_dir():shutil.rmtree(path)
  else:path.unlink(missing_ok=True)
 (SCENARIO/'bin/mode').write_text(mode+'\n')
 secrets=SCENARIO/'connectome-secrets';secrets.mkdir(mode=0o700);(secrets/'example.json').write_text('{}');(secrets/'example.json').chmod(0o600)
 return archive(SCENARIO/'inputs/framework.tgz',archive_edit)

def request(artifact,edit,commit):
 runtime=str(SCENARIO/'bin/runtime')
 cook={'host_repo':str(SCENARIO/'host'),'framework_archive':artifact,'connector_state':{}}
 config={'name':'example','state_dir':str(SCENARIO/'runtime-state'),'secrets_file':str(SCENARIO/'connectome-secrets/example.json'),'agent':{'systemPrompt':'Offline example'},'required_secrets':[],'connectors':{},'modules':{},'cook':cook}
 if edit=='wrong-archive-digest':cook['framework_archive']=dict(artifact,sha256='1'*64)
 if edit=='budgets':config['budgets']={'max_steps':1}
 if edit=='no-connector-state':del cook['connector_state']
 versions={'runtime':{'cook':commit,'connectome-host':HOST_PIN,'agent-framework':FRAMEWORK_PIN},'connectors':{}}
 if edit=='connector-env-not-a-mapping':
  config['connectors']={'extra':{'pin_ref':'connectors.extra','repo':'https://example.invalid/extra.git','settings':{'command':'node','env':'not-a-mapping'}}}
  versions['connectors']['extra']='d'*40
 if edit=='catalog-checkout-outside-agent':
  del cook['framework_archive'];cook.update(host_repo=str(SCENARIO/'agent/inputs/host'),host_dependencies=[])
  config['catalog']={};versions.update(artifacts={},platform={'/bin/sh':hashlib.sha256(Path('/bin/sh').read_bytes()).hexdigest()})
  for name,version in (('node','24.1.0'),('bun','1.0.0')):
   descriptor={'kind':'runtime','requires':[],'exports':{name:'bin/'+name},'pin_ref':'artifacts.'+name};config['catalog'][name]=descriptor
   # Pin digests are request inputs; the descriptor digest is computed here, not by the engine.
   versions['artifacts'][name]={'archive':'inputs/'+name+'.tar.gz','sha256':hashlib.sha256(name.encode()).hexdigest(),'version':version,
    'descriptor_sha256':hashlib.sha256(json.dumps(descriptor,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
 return {'contract':'connectome.kitchen/v1','command':'apply','request':{'phase':'prepare','root':str(SCENARIO/'agent'),'unit_dir':str(SCENARIO/'units'),'config':config,
  'versions':versions,
  'engines':{'cook':{'entrypoint':str(SCENARIO/'cook/bin/cook'),'runtime':runtime,'bun':runtime,'node':runtime}},
  'systemctl':[sys.executable,str(ROOT/'oracle/tests/fixtures/systemctl.py'),str(SCENARIO/'agent')]}}

def project(receipt,status):
 """Fixed projection: drop the envelope and `phase`, restore the adapter's key order and stream."""
 if set(receipt)!={'contract','command','core','meta'} or receipt['command']!='apply' or status!=1:raise ValueError('not an adapter refusal receipt')
 core=dict(receipt['core'])
 if core.pop('phase',None)!='prepare' or 'prepare' in core:raise ValueError('refusal did not come from the adapter stage')
 ordered={key:core.pop(key) for key in ('error_code','msg','cook_error_code') if key in core}
 if core:raise ValueError('unprojected core keys')
 return {'stdout':'','stderr':json.dumps(ordered)+'\n','status':1}

def classify(oracle,paths):
 """What the unpatched adapt raises for this build, in the terms the frozen test injected."""
 import connectome_cook, connectome_errors
 from connectome_lock import LockError
 build=Path(paths['build'])
 for entry in build.iterdir():
  if entry.name!='deployment.json':shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
 for name in ('cook-config','cook-credentials','workspace'):shutil.rmtree(build.parent.parent/name,ignore_errors=True)
 try:connectome_cook.adapt(paths['spec'],paths['build'],paths['state'],paths['runtime'],paths['entrypoint'],paths['runtime'],paths['runtime'])
 except connectome_errors.SafeError as error:return ':'.join(x for x in ('SafeError',error.code,error.cook_error_code) if x)
 except LockError:return 'LockError'
 except Exception:return 'Exception'
 return 'returned'

def child(args):
 """One process per oracle tree: runs every scenario and prints one JSON line each."""
 oracle=args.oracle.resolve();sys.path[:0]=[str(oracle/'module_utils'),str(oracle/'tests')];os.umask(0o022)
 spec=importlib.util.spec_from_file_location('kitchen_shim',oracle/'shim/kitchen_v1.py');shim=importlib.util.module_from_spec(spec);spec.loader.exec_module(shim)
 import connectome_cook, connectome_errors
 from deploy_fixtures import safe_ancestors
 commit=connectome_cook.COOK_COMMIT
 if subprocess.run(['git','-C',str(args.cook_source),'cat-file','-e',commit+'^{commit}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
  print(json.dumps({'prerequisite':'reviewed cook commit unavailable locally'}));return 3
 prepare_inputs(args.cook_source,commit)
 with safe_ancestors(SCENARIO):
  for name,(mode,config_edit,archive_edit,expected) in scenarios(connectome_errors.COOK_CODES).items():
   artifact=reset(mode,archive_edit);data=request(artifact,config_edit,commit)
   receipt,status=shim.dispatch(json.loads(json.dumps(data)))
   log=SCENARIO/'fake-tools.jsonl';calls=log.read_text().replace(str(ROOT),'${REPO}') if log.exists() else ''
   row={'scenario':name,'request_sha256':request_digest(data),'fake_runtime_mode':mode,'fake_runtime_calls':len(calls.splitlines()),'fake_tool_log_sha256':hashlib.sha256(calls.encode()).hexdigest(),'status':status,'expected_adapter_failure':expected}
   try:row['projected']=project(receipt,status)
   except ValueError as error:row['projection_error']=str(error);print(json.dumps(row),flush=True);continue
   builds=sorted((SCENARIO/'agent/builds').iterdir()) if (SCENARIO/'agent/builds').is_dir() else []
   if len(builds)==1 and (builds[0]/'deployment.json').is_file():
    runtime=str(SCENARIO/'bin/runtime')
    row['adapter_failure']=classify(oracle,{'spec':str(builds[0]/'deployment.json'),'build':str(builds[0]),'state':str(SCENARIO/'runtime-state'),'runtime':runtime,'entrypoint':str(SCENARIO/'cook/bin/cook')})
   else:row['adapter_failure']='adapter-not-reached'
   print(json.dumps(row),flush=True)
 return 0

def obligations():return json.loads((ROOT/'test/vectors/cli-obligations.golden').read_text())

def wanted(entry,errors):
 """The scenario that reconstructs what the frozen test injected for this entry, or why none can."""
 record=entry['expected'];code=record['error_code'];test=entry['test'].rsplit('.',1)[1]
 if test=='test_every_reviewed_cook_code_is_preserved_without_raw_output':return 'cook:'+record['cook_error_code'],None
 if test=='test_cli_lock_and_unexpected_exception_fallbacks':return ('fallback:LockError' if code=='E_LOCK_INVALID' else 'fallback:Exception'),None
 if code in SAME_VALIDATION:return None,code+': the adapter raises it only from validation the engine has already applied to the same request, so no contract request reaches it at the adapter stage'
 if code in DEPLOY_ONLY:return None,code+': raised only by deployment-engine code that the adapter never calls'
 return 'engine:'+code,None

def run_child(oracle,cook_source):
 done=subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'child','--oracle',str(oracle),'--cook-source',str(cook_source)],capture_output=True,timeout=900)
 rows=[json.loads(line) for line in done.stdout.decode().splitlines() if line.startswith('{')]
 if done.returncode==3:raise SystemExit('PREREQUISITE MISSING: '+rows[0]['prerequisite'])
 if done.returncode:raise ValueError('scenario runner failed: '+done.stderr.decode()[-2000:])
 return {row['scenario']:row for row in rows}

def expected_records():
 """Recorded outputs are closed diagnostics; rebuild each from its committed code fields and bind it by hash."""
 sys.path.insert(0,str(ROOT/'oracle/module_utils'))
 import connectome_errors
 result=[]
 for entry in obligations():
  found=None
  for code in connectome_errors.MESSAGES:
   for upstream in (None,*sorted(connectome_errors.COOK_CODES)) if code=='E_COOK_FAILED' else (None,):
    public=connectome_errors.diagnostic(code,upstream)
    record={'argv':ARGV,'function':entry['function'],'kind':'C','status':1,'stderr':json.dumps(public)+'\n','stdin':None,'stdout':'','test':entry['test']}
    if hashlib.sha256(canonical(record).encode()).hexdigest()==entry['raw_sha256']:found=public
  if found is None:raise ValueError('recorded entry is not a closed diagnostic: '+entry['id'])
  result.append(dict(entry,expected=found))
 return result,connectome_errors

def evaluate(rows,entries,errors):
 result=[]
 for entry in entries:
  scenario,blocked=wanted(entry,errors)
  out={'id':entry['id'],'test':entry['test'],'raw_sha256':entry['raw_sha256'],'scenario':scenario}
  if scenario is None:out.update(status='unreachable-without-patching',reason=blocked);result.append(out);continue
  row=rows.get(scenario,{});projected=row.get('projected')
  record=projected and {'argv':ARGV,'function':entry['function'],'kind':'C','status':projected['status'],'stderr':projected['stderr'],'stdin':None,'stdout':projected['stdout'],'test':entry['test']}
  out.update(request_sha256=row.get('request_sha256'),fake_runtime_mode=row.get('fake_runtime_mode'),fake_runtime_calls=row.get('fake_runtime_calls'),fake_tool_log_sha256=row.get('fake_tool_log_sha256'),adapter_failure=row.get('adapter_failure'),expected_adapter_failure=row.get('expected_adapter_failure'),
   projected_record_sha256=hashlib.sha256(canonical(record).encode()).hexdigest() if record else None)
  same_bytes=out['projected_record_sha256']==entry['raw_sha256'];same_branch=row.get('adapter_failure')==row.get('expected_adapter_failure')
  out['status']='replayed' if same_bytes and same_branch else 'not-reproduced'
  if not same_bytes:out['reason']='projected bytes differ from the recording: '+str(row.get('projection_error') or (projected or {}).get('stderr'))
  elif not same_branch:out['reason']='adapter failed differently from the injected failure'
  result.append(out)
 return result

SHIM_MUTANTS=[
 ('shim.core.message',"except SafeError as error:core=error.public()","except SafeError as error:core=dict(error.public(),msg='edited')"),
 ('shim.core.code',"except SafeError as error:core=error.public()","except SafeError as error:core=dict(error.public(),error_code='E_DEPLOY_FAILED')"),
 ('shim.core.cook-code',"except SafeError as error:core=error.public()","except SafeError as error:core={k:v for k,v in error.public().items() if k!='cook_error_code'}"),
 ('shim.core.phase'," core['phase']=stage"," core['phase']='activate'"),
 ('shim.core.extra'," core['phase']=stage"," core['phase']=stage;core['changed']=False"),
]

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 for name in ('run','shim-mutants','child'):
  s=sub.add_parser(name);s.add_argument('--cook-source',type=Path,required=True)
  if name=='child':s.add_argument('--oracle',type=Path,required=True)
  if name=='run':s.add_argument('--write',action='store_true')
 a=p.parse_args()
 if a.command=='child':return child(a)
 entries,errors=expected_records()
 if a.command=='run':
  result=evaluate(run_child(ROOT/'oracle',a.cook_source),entries,errors);target=ROOT/'test/vectors/cli-replay.golden';data=json.dumps(result,indent=2)+'\n'
  listed=[]
  for entry,row in zip(obligations(),result):
   entry=dict(entry,status='replayed' if row['status']=='replayed' else 'pending-shim-projection')
   entry['issue']=('projected from a shim receipt and bound to the unpatched adapter failure; see cli-replay.golden' if row['status']=='replayed' else row.get('reason','not reproduced'))
   listed.append(entry)
  listing=json.dumps(listed,indent=2)+'\n';obligation_file=ROOT/'test/vectors/cli-obligations.golden'
  if a.write:target.write_text(data);obligation_file.write_text(listing)
  elif obligation_file.read_text()!=listing:print('STALE test/vectors/cli-obligations.golden');return 1
  elif not target.exists() or target.read_text()!=data:print('STALE test/vectors/cli-replay.golden');return 1
  counts={s:sum(r['status']==s for r in result) for s in sorted({r['status'] for r in result})}
  print(json.dumps({'recorded':len(result),**counts},sort_keys=True));return int(any(r['status']=='not-reproduced' for r in result))
 base=evaluate(run_child(ROOT/'oracle',a.cook_source),entries,errors);replayed={r['id'] for r in base if r['status']=='replayed'};report=[];scratch=ROOT/'.cache/shim-mutants'
 for name,before,after in SHIM_MUTANTS:
  shutil.rmtree(scratch,ignore_errors=True);shutil.copytree(ROOT/'oracle',scratch/'oracle',ignore=shutil.ignore_patterns('__pycache__','.cache'))
  target=scratch/'oracle/shim/kitchen_v1.py';source=target.read_text()
  if source.count(before)!=1:raise ValueError('ambiguous shim anchor: '+name)
  target.write_text(source.replace(before,after))
  mutated=evaluate(run_child(scratch/'oracle',a.cook_source),entries,errors);still={r['id'] for r in mutated if r['status']=='replayed'}
  report.append({'id':name,'replays_failing':len(replayed-still),'replays_still_passing':len(replayed&still),'status':'killed' if replayed-still else 'survived'})
 print(json.dumps({'baseline_replayed':len(replayed),'mutants':report},indent=1));return int(any(r['status']!='killed' for r in report))
if __name__=='__main__':sys.exit(main())
