#!/usr/bin/env python3
"""Build/replay independent negative function vectors from audited data APIs and an explicit lock-file fixture.

Mutation of input data is deterministic, not mutation of expected answers.
Only normal returns and declared value/refusal exceptions are goldens. A mutant
crash, decoder error or timeout is NOT a kill. No patch registry is used.
"""
import argparse, copy, hashlib, importlib, json, re, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from codec import decode
from record import encode, canonical
ROOT=Path(__file__).resolve().parents[2]
PURE={
 'connectome_inputs':{'validate','validate_external_data_args','external_data_path','external_data_flag','verify_bytes'},
 'connectome_deploy':{'identifier','validate_config','direct_secret_names','secret_names','environment_names','resolve_pins'},
 'connectome_catalog':{'entries_for','resolve','order','secret_environment_names','environment_name','systemd_words','compile_data_args','audit','audit_path','audit_location','audit_argv','environment_name','audit_environment','audit_recipe','audit_descriptors','audit_data_args','data_options_for','preflight_unit','audit_unit','audit_launcher','literal_words','isolated_unit'},
 'connectome_archive':{'validate_dependency','validate_dependency_record','check_dependencies','validate_provenance','dependency_inputs','legacy_dependency','provenance_record','records_metadata'},
 'connectome_cook':{'validate_arguments','external_servers','build_commands','source'},
 'connectome_config':{'merge_layers','public_catalog'},
 'connectome_lock':{'names_only','repository','map_lock'},
 'connectome_spawn.schema':{'template','instance','name_record','name_receipt','activation_identity','concrete','data_only','names','positive'},
 'connectome_spawn.catalog':{'descriptor','argv','artifact_relative','local_roots'},
 'connectome_spawn.data':{'json_data','fields','unique','decode'},
 'connectome_spawn.seed':{'seed_policy','seed_intent'},
 'connectome_spawn.identity':{'identity_policy','validate_capabilities','validate_provider_name','temporary_name'},
 'connectome_spawn.lifecycle':{'_state','_agent','_handle'},
 'connectome_spawn.planner':{'require_bound','expand','connector_recipe'},
 'connectome_spawn.sources':{'absolute','relative','parse_repo_source'},
}
FUNCTIONS={m+'.'+f for m,fs in PURE.items() for f in fs}
PROFILES={
 'connectome_spawn.identity.validate_capabilities':'identity-capabilities-v1',
 'connectome_spawn.identity.validate_provider_name':'identity-capabilities-v1',
 'connectome_spawn.identity.temporary_name':'fixed-identity-clock-v1',
 'connectome_lock.map_lock':'lock-files-v1',
}

def initialize(modules):
 sys.path.insert(0,str(modules.resolve()))
 for module in PURE:importlib.import_module(module)

def invoke(name,args,kwargs):
 module,method=name.rsplit('.',1);loaded=importlib.import_module(module);fn=getattr(loaded,method)
 root=None
 if name=='connectome_lock.map_lock':
  root=Path(loaded.__file__).resolve().parent/'.cache/guard-lock-fixture'
  root.mkdir(parents=True,exist_ok=True)
  (root/'run.sh').write_text('#!/bin/sh\nexit 0\n')
  (root/'example.service').write_text('[Service]\nExecStart=/bin/true\n')
  (root/'cook-recipe.json').write_text('{}')
  def bind(v):
   if isinstance(v,str):return v.replace('@BUILD@',str(root))
   if isinstance(v,list):return [bind(x) for x in v]
   if isinstance(v,dict):return {k:bind(x) for k,x in v.items()}
   return v
  args=bind(args);kwargs=bind(kwargs)
 try:
  if PROFILES.get(name)=='identity-capabilities-v1':
   from types import SimpleNamespace
   args=[SimpleNamespace(**args[0]),*args[1:]]
  if PROFILES.get(name)=='fixed-identity-clock-v1':
   from datetime import datetime, timezone
   kwargs=dict(kwargs,now=datetime.fromisoformat(kwargs.get('now','2000-01-01T00:00:00+00:00')))
  result=fn(*copy.deepcopy(args),**copy.deepcopy(kwargs))
 except Exception as error:
  # These are data outcomes, not harness/runtime errors. Other exception types
  # (TypeError, KeyError, AttributeError, OSError, recursion, etc.) are excluded.
  if not isinstance(error,ValueError):return None
  outcome={'exception':type(error).__name__,'message':str(error)}
  if hasattr(error,'public'):outcome['public']=encode(error.public())
  return outcome
 outcome={'result':encode(result)}
 if root:outcome=json.loads(json.dumps(outcome).replace(str(root),'@BUILD@'))
 return outcome

VALUES=[None,False,True,0,-1,10000001,0.5,'','invalid','../escape','/outside','a\n','${UNDECLARED}','{artifact:missing}','0'*40,'0'*64,[],{},['duplicate','duplicate']]
def variants(value,path=()):
 yield path,value
 if isinstance(value,dict):
  for key in value:
   for p,v in variants(value[key],path+(key,)):yield p,v
 elif isinstance(value,list):
  for i,v in enumerate(value):
   for p,w in variants(v,path+(i,)):yield p,w

def replace(value,path,new):
 result=copy.deepcopy(value);target=result
 for key in path[:-1]:target=target[key]
 if not path:return copy.deepcopy(new)
 target[path[-1]]=copy.deepcopy(new);return result

def normalize(value):
 if isinstance(value,str):
  value=re.sub(r'/home/[^\s"\'{}]+','/fixture',value)
  return re.sub(r'/tmp/tmp[^/\s"\'{}]+','/fixture/tmp',value)
 if isinstance(value,Path):return Path(normalize(str(value)))
 if isinstance(value,list):return [normalize(x) for x in value]
 if isinstance(value,dict):return {normalize(k):normalize(v) for k,v in value.items()}
 return value

def generate(recording,out):
 seeds={};vectors={};rejected=0
 def add(name,args,kwargs):
  nonlocal rejected
  outcome=invoke(name,args,kwargs)
  if outcome is None:rejected+=1;return
  row={'function':name,'args':encode(args),'kwargs':encode(kwargs),'outcome':outcome}
  if name in PROFILES:row['profile']=PROFILES[name]
  line=canonical(row);vectors.setdefault(hashlib.sha256(line.encode()).hexdigest(),line)
 for line in recording.read_text().splitlines():
  row=json.loads(line);name=row['function']
  if name not in FUNCTIONS or name=='connectome_lock.map_lock' or 'result' not in row['outcome']:continue
  try:args=normalize(decode(row['args']));kwargs=normalize(decode(row['kwargs']))
  except (TypeError,ValueError):continue
  # Keep at most four structurally different successes for each API.
  outcome=invoke(name,args,kwargs)
  if outcome is None or 'result' not in outcome:continue
  def structure(v):
   if isinstance(v,dict):return {k:structure(x) for k,x in v.items()}
   if isinstance(v,list):return [structure(x) for x in v[:2]]
   return type(v).__name__
  shape=canonical(structure(args))
  seeds.setdefault(name,{})
  if len(seeds[name])<8:seeds[name].setdefault(shape,(args,kwargs))
 # The recorder misses direct aliases imported before wrapping; seed the two
 # top-level schema entry points explicitly from the frozen example documents.
 import yaml
 for fn,file in [('template','template.yml'),('instance','instance.yml')]:
  value=yaml.safe_load((ROOT/'oracle/examples/spawn'/file).read_text());name='connectome_spawn.schema.'+fn
  seeds.setdefault(name,{})['example']=([value],{})
 lock=json.loads((ROOT/'oracle/tests/fixtures/locks/v2.json').read_text())
 lock['build']['dir']='@BUILD@';lock['launch']['script']='run.sh'
 lock['cook']['commit']='c'*40
 lock.update(state={'path':'/state'},engines={k:{'path':'/bin/'+k,'realPath':'/bin/'+k,'version':'1.0.0'} for k in ('node','bun')},health={'command':['/bin/true']})
 lock['components']=[{'kind':'git','key':'example','role':'mcp','url':'/local/repo','ref':'main','commit':'a'*40,'path':'/app','install':''},{'kind':'npm','key':'package','role':'extension','name':'example','spec':'1.0.0','version':'1.0.0','integrity':'sha256-YWJj'}]
 lock['localExtensions']=[{'name':'example','hostDir':'/extension','path':'/app/extension'}]
 lock['units']=[{'path':'example.service','sha256':hashlib.sha256(b'[Service]\nExecStart=/bin/true\n').hexdigest()}]
 lock['layout']={'schema':'connectome.host-layout/v1',**{k:'/'+k for k in ('runtime','config','state','workspace','credentials')},'connectorState':{'example':{'DATA_DIR':'/state'}}}
 seeds['connectome_lock.map_lock']={'fixture':([lock,'@BUILD@'],{})}
 # Absolute fixture paths are deliberately unallocated: these APIs only inspect
 # path resolution, never read/write contents. Refuse a host with that root.
 if Path('/fixture').exists():raise ValueError('fixture namespace must be absent')
 root=Path('/fixture/build');state=Path('/fixture/state');cwd=root/'catalog/example';exe=str(cwd/'run')
 cat='connectome_catalog.'
 samples={
  'audit':[['/fixture/build/file',root,state,{}]],
  'audit_path':[['./file',cwd,root,state,{}]],
  'audit_location':[['./file',cwd,root,state,{}]],
  'audit_argv':[[[exe,'--flag=value'],cwd,root,state,{}]],
  'audit_environment':[[{'SAFE':'value'},cwd,root,state,{}]],
  'audit_recipe':[[{'command':exe,'args':['--flag=value'],'env':{'SAFE':'value'}},root,state,{}]],
  'audit_data_args':[[{'--data-file':str(state/'file')},state]],
  'audit_unit':[['[Service]\nWorkingDirectory='+str(cwd)+'\nEnvironment=SAFE=value\nExecStart='+exe+'\n',root,state,{}]],
  'audit_launcher':[['#!/fixture/build/sh\ncd '+str(cwd)+'\nexec '+exe+'\nexit 0\n',cwd,root,state,{}]],
  'literal_words':[['/fixture/build/run argument']],
 }
 for fn,sample in samples.items():seeds.setdefault(cat+fn,{})['fixture']=(sample[0],{})
 # Exercise optional schema blocks, not just the lean example template.
 augmented=copy.deepcopy(seeds['connectome_spawn.schema.template']['example'][0][0])
 agent=augmented['agent'];agent.update(contextBudgetTokens=1024,maxStreamTokens=1024,responses={'reasoningEffort':'high'},codex={'fastMode':False},proseRouting='locus')
 agent['strategy'].update(recentWindowTokens=100,headWindowTokens=100,targetChunkTokens=100,maxMessageTokens=100,maxSpeculativeL1s=1,mergeThreshold=1,compressionRecallBudgetTokens=100,adaptiveResolution=True,enforceBudget=False,compressionSlackRatio=0.5,overBudgetGraceRatio=0.5,foldingStrategy='kv-unified',compressionModel='model')
 seeds['connectome_spawn.schema.template']['optional']=([augmented],{})
 policy={'temporaryNameTemplate':'Example{timestamp}'}
 caps={'supports_rename':True,'max_name_length':63,'name_pattern':r'[A-Za-z][A-Za-z0-9_-]*'}
 seeds['connectome_spawn.identity.validate_capabilities']={'named-fake':([caps],{})}
 seeds['connectome_spawn.identity.validate_provider_name']={'named-fake':([caps,'Example'],{})}
 seeds['connectome_spawn.identity.temporary_name']={'clock':([policy],{'now':'2000-01-01T00:00:00+00:00'})}
 identity={'name':'Example','displayName':'Example','summaryParticipant':'Example'}
 value={'schema':'connectome.spawn-state/v1','agent':'example','state':'planned','seed':{'mode':'optional','branch':'clean','digest':None},'home':{'transport':'console','channel':'example'},'identity':identity,'identityPolicy':policy,'temporaryName':'Example2000','supportsRename':None,'temporaryIdentity':None,'permanentIdentity':None,'store':'/fixture/store','operations':{'create':{'digest':'a'*64,'target':'planned'}},'pending':None}
 seeds['connectome_spawn.lifecycle._state']={'planned':([value,'example'],{})}
 active=copy.deepcopy(value);active.update(state='active',supportsRename=True,temporaryIdentity={'id':'a'*32,'agent':'example'},permanentIdentity={'id':'b'*32,'agent':'example'},pending={'operation':'activate','digest':'a'*64,'target':'active','step':'prepared'})
 from connectome_spawn.data import digest
 active['nameRecord']={'recordHash':digest(identity),'source':{'kind':'file','path':'/fixture/name'}}
 seeds['connectome_spawn.lifecycle._state']['active']=([active,'example'],{})
 seeds['connectome_spawn.lifecycle._agent']={'identifier':(['example'],{})}
 seeds['connectome_spawn.lifecycle._handle']={'handle':([{'id':'a'*32,'agent':'example'},'example'],{})}
 for name,items in sorted(seeds.items()):
  for args,kwargs in items.values():
   bundle=[args,kwargs];add(name,args,kwargs)
   for path,value in variants(bundle):
    # Do not change invocation arity; perturb each actual argument recursively.
    if len(path)<2:continue
    choices=list(VALUES)
    if isinstance(value,str):choices += [value+'\n',value+'/',value+'/..',value+'\x00',value*2]
    if isinstance(value,dict):
     choices += [{k:v for k,v in value.items() if k!=drop} for drop in value]
     choices += [dict(value,unexpected='extra')]
    if isinstance(value,list) and value:choices += [value+value[:1],value[1:]]
    for choice in choices:
     changed=replace(bundle,path,choice);add(name,*changed)
 out.write_text('\n'.join(vectors.values())+'\n')
 print(canonical({'functions':len(seeds),'vectors':len(vectors),'excluded_runtime_errors':rejected}))

def replay(vectors,module=None):
 count=errors=0;witnesses=[]
 for line in vectors.read_text().splitlines():
  row=json.loads(line)
  if row['function'] not in FUNCTIONS:raise ValueError('unaudited API')
  if row.get('profile')!=PROFILES.get(row['function']):raise ValueError('missing or unknown fixture profile')
  if module and row['function'].rsplit('.',1)[0]!=module:continue
  try:args=decode(row['args']);kwargs=decode(row['kwargs']);actual=invoke(row['function'],args,kwargs)
  except Exception:errors+=1;continue
  count+=1
  if actual is None:errors+=1;continue
  if actual!=row['outcome']:witnesses.append(hashlib.sha256(line.encode()).hexdigest())
 return {'replayed':count,'runtime_errors':errors,'mismatches':len(witnesses),'witnesses':witnesses}

def main():
 p=argparse.ArgumentParser();p.add_argument('--modules',type=Path,default=ROOT/'oracle/module_utils');sub=p.add_subparsers(dest='command',required=True)
 b=sub.add_parser('build');b.add_argument('--recording',type=Path,required=True);b.add_argument('--out',type=Path,required=True)
 r=sub.add_parser('replay');r.add_argument('--vectors',type=Path,required=True);r.add_argument('--module')
 a=p.parse_args();initialize(a.modules)
 if a.command=='build':generate(a.recording,a.out);return 0
 result=replay(a.vectors,a.module);print(canonical(result));return int(bool(result['mismatches'] or result['runtime_errors']))
if __name__=='__main__':sys.exit(main())
