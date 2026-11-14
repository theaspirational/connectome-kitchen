#!/usr/bin/env python3
"""Contract-v1 oracle adapter, never an alternative engine implementation."""
import argparse, contextlib, fcntl, json, math, os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'module_utils'))
from connectome_deploy import Deployment, make_directory
from connectome_config import merge_layers
from connectome_errors import SafeError, diagnostic
from connectome_spawn import SpawnError, template, instance, digest, make_plan, load_catalog
from connectome_spawn.lifecycle import Lifecycle

CONTRACT='connectome.kitchen/v1'
KEYS={
 'plan':{'base','root','unit_dir','layers','config','versions','engines','systemctl'},
 'apply':{'base','root','unit_dir','layers','config','versions','engines','systemctl','operator_files','phase'},
 'spawn':{'action','template','instance','catalog','stores','observed','seed','name','state_root','agent'},
}
class ContractError(ValueError):pass

def strict(source):
 def pairs(items):
  result={}
  for key,value in items:
   if key in result:raise ContractError()
   result[key]=value
  return result
 def refuse(*a):raise ContractError()
 def number(text):
  value=float(text)
  if not math.isfinite(value):raise ContractError()
  return value
 try:return json.loads(source,object_pairs_hook=pairs,parse_constant=refuse,parse_float=number)
 except (ValueError,TypeError,RecursionError,UnicodeError):raise ContractError() from None

def envelope(command,core):
 return {'contract':CONTRACT,'command':command,'core':core,'meta':{'kitchen':'oracle-a49b5dc','artifact_sha256':None}}

def dispatch(data,secrets=None):
 if type(data) is not dict or set(data)!={'contract','command','request'} or data['contract']!=CONTRACT:raise ContractError()
 command=data['command'];request=data['request']
 if command not in KEYS or type(request) is not dict or not request.keys()<=KEYS[command]:raise ContractError()
 if command=='spawn':
  action=request.get('action')
  try:
   if action=='status':
    if set(request)!={'action','state_root','agent'}:raise ContractError()
    core=Lifecycle(request['state_root']).status(request['agent'])
   else:
    if action not in ('validate','plan','dry-run') or any(k in request for k in ('agent','state_root')):raise ContractError()
    spec=template(request['template']);declaration=instance(request['instance'])
    if action=='validate':
     if set(request)!={'action','template','instance'}:raise ContractError()
     core={'valid':True,'templateHash':digest(spec),'instanceHash':digest(declaration),'catalogResolved':False}
    else:
     core=make_plan(spec,declaration,load_catalog(request['catalog']),request['stores'],observed=request.get('observed'),seed_payload=request.get('seed'),name_source=request.get('name'))
     if action=='dry-run':core['wouldWrite']=core['plan']['files']
   return envelope(command,core),0
  except (SpawnError,OSError,UnicodeError,RecursionError) as error:
   core={'error':'spawn input refused','filesWritten':False,'error_code':'E_SPAWN_REFUSED'}
   if isinstance(error,SpawnError) and str(error).startswith('migrate template v1'):core['migration']=str(error)
   return envelope(command,core),1
 phase=request.get('phase','both');prepared=None;stage='prepare' if phase!='activate' else 'activate'
 if phase not in ('prepare','activate','both'):raise ContractError()
 if ('layers' in request)==('config' in request):raise ContractError()
 engines=request.get('engines')
 if type(engines) is not dict or set(engines)!={'cook'} or type(engines['cook']) is not dict or set(engines['cook'])!={'entrypoint','runtime','bun','node'}:raise ContractError()
 cook=engines['cook']
 if any(type(v) is not str or not Path(v).is_absolute() for v in cook.values()):raise ContractError()
 # This is the existing adapter's literal CLI, with no free-form shell command.
 argv=[sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/cook-adapter.py'),'--spec','{recipe}','--out','{build_dir}','--state','{state_dir}','--runtime',cook['runtime'],'--entrypoint',cook['entrypoint'],'--bun-path',cook['bun'],'--node-path',cook['node']]
 try:
  config=request.get('config') if 'config' in request else merge_layers(request['layers'])
  dep=Deployment(root=request['root'],unit_dir=request['unit_dir'],config=config,versions=request['versions'],cook_command=argv,systemctl=request.get('systemctl'),check=command=='plan',base=request.get('base'),operator_files=request.get('operator_files'))
  @contextlib.contextmanager
  def lock():
   if command=='plan':yield;return
   dep.validate_paths();dep.preflight_activation();make_directory(dep.root)
   with open(dep.lock_path(),'a') as stream:
    fcntl.flock(stream,fcntl.LOCK_EX);yield
  with lock():
   core={}
   if phase in ('prepare','both'):prepared=dep.prepare();core['prepare']=prepared
   if phase in ('activate','both'):stage='activate';core['activate']=dep.activate(secrets)
   core['changed']=any(part['changed'] for part in core.values())
   return envelope(command,core),0
 except SafeError as error:core=error.public()
 except Exception:core=diagnostic('E_DEPLOY_FAILED')
 core['phase']=stage
 if prepared is not None:core['prepare']=prepared
 return envelope(command,core),1

class Parser(argparse.ArgumentParser):
 def error(self,message):raise ContractError()
def main(argv=None):
 command=None;safe_receipt=None
 try:
  parser=Parser(allow_abbrev=False,add_help=False);parser.add_argument('command');parser.add_argument('--input',required=True);parser.add_argument('--secrets-fd',type=int);parser.add_argument('--receipt');args=parser.parse_args(argv)
  if args.command not in KEYS:raise ContractError()
  command=args.command
  if not Path(args.input).is_absolute() or args.receipt and not Path(args.receipt).is_absolute():raise ContractError()
  safe_receipt=args.receipt
  data=strict(Path(args.input).read_bytes())
  if data.get('command')!=command:raise ContractError()
  secrets=strict(os.read(args.secrets_fd,1024*1024+1)) if args.secrets_fd is not None else None
  result,status=dispatch(data,secrets)
 except (ContractError,OSError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError):
  result=envelope(command,{'error_code':'E_CONTRACT','msg':'Contract input refused.'});status=2
 line=(json.dumps(result,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode()
 if safe_receipt:
  # Reuse the frozen engine's atomic writer at the shim boundary.
  from connectome_deploy import atomic_write
  try:atomic_write(Path(safe_receipt),line,mode=0o600)
  except Exception: return 2
 sys.stdout.buffer.write(line);return status
if __name__=='__main__':sys.exit(main())
