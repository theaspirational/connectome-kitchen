#!/usr/bin/env python3
"""Extract A calls, B test scenarios and C subprocess traffic from the frozen suite.

Tagged values are lossless for Path, bytes and streams. Named fakes and opaque
objects are explicit, never silently serialized as strings. Patch-dependent
calls are not advertised as independently replayable function vectors (B3).
"""
import argparse, base64, functools, hashlib, importlib, inspect, io, json, os, gzip
from pathlib import Path
import subprocess, sys, unittest, ast, tarfile

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def encode(value,seen=None):
 seen=set() if seen is None else seen
 if value is None or isinstance(value,(str,bool,int)):return value
 if isinstance(value,float):return {'$float':repr(value)}
 if isinstance(value,Path):return {'$path':str(value)}
 if isinstance(value,bytes):return {'$bytes':base64.b64encode(value).decode()}
 if isinstance(value,(io.BytesIO,io.StringIO)):
  return {'$stream':encode(value.getvalue()),'position':value.tell()}
 if isinstance(value,io.IOBase):return {'$open-stream':{'name':encode(value.name),'position':value.tell() if value.seekable() else None}}
 if id(value) in seen:return {'$cycle':type(value).__name__}
 seen.add(id(value))
 try:
  if isinstance(value,(list,tuple)):return [encode(x,seen) for x in value]
  if isinstance(value,dict):return {'$mapping':[[encode(k,seen),encode(v,seen)] for k,v in value.items()]}
  if callable(value):return {'$fake':getattr(value,'__module__',type(value).__module__)+'.'+getattr(value,'__qualname__',type(value).__qualname__)}
  return {'$object':type(value).__module__+'.'+type(value).__qualname__}
 finally:seen.remove(id(value))

def snapshot(root):
 rows=[]
 if not root.is_dir():return rows
 for p in sorted(root.rglob('*')):
  s=p.lstat(); row={'path':p.relative_to(root).as_posix(),'mode':s.st_mode&0o777}
  if p.is_symlink():row.update(kind='link',target=os.readlink(p))
  elif p.is_dir():row.update(kind='directory')
  elif p.is_file():row.update(kind='file',sha256=hashlib.sha256(p.read_bytes()).hexdigest())
  else:row.update(kind='special')
  rows.append(row)
 return rows

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
 source=args.source.resolve();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
 sys.path[:0]=[str(source/'roles/connectome_agent/module_utils'),str(source/'tests')]
 modules=[]
 for path in sorted((source/'roles/connectome_agent/module_utils').glob('connectome_*.py')):
  modules.append(importlib.import_module(path.stem))
 for path in sorted((source/'roles/connectome_agent/module_utils/connectome_spawn').glob('*.py')):
  modules.append(importlib.import_module('connectome_spawn'+('' if path.stem=='__init__' else '.'+path.stem)))
 os.environ.update(GIT_AUTHOR_DATE='2000-01-01T00:00:00+00:00',GIT_COMMITTER_DATE='2000-01-01T00:00:00+00:00')
 state={'test':None,'calls':[],'cli':[],'scenario':[]};vectors={};coverage={};current_sites=set()
 def wrap(fn,name):
  @functools.wraps(fn)
  def call(*a,**kw):
   before={'args':encode(a),'kwargs':encode(kw)}
   cli=name in ('connectome_cook.main','connectome_spawn.cli.main')
   stdout,stderr=sys.stdout,sys.stderr
   class Tee:
    def __init__(self,stream):self.stream=stream;self.parts=[]
    def write(self,text):self.parts.append(text);return self.stream.write(text)
    def flush(self):return self.stream.flush()
    def __getattr__(self,key):return getattr(self.stream,key)
   if cli:sys.stdout,sys.stderr=Tee(stdout),Tee(stderr)
   try:result=fn(*a,**kw)
   except BaseException as exc:
    outcome={'exception':type(exc).__name__,'message':str(exc)}
    if hasattr(exc,'public'):outcome['public']=encode(exc.public())
    raise
   else:outcome={'result':encode(result)};return result
   finally:
    if cli:
     actual_argv=a[0] if a else sys.argv[1:]
     state['cli'].append({'kind':'C','test':state['test'],'function':name,'argv':encode(actual_argv),'stdin':None,'stdout':''.join(sys.stdout.parts),'stderr':''.join(sys.stderr.parts),'status':int(outcome.get('message') or 0) if outcome.get('exception')=='SystemExit' and (outcome.get('message') or '0').isdigit() else outcome.get('result',0) or 0})
     sys.stdout,sys.stderr=stdout,stderr
    row={'kind':'A','function':name,**before,'outcome':outcome,'test':state['test']}
    digest=hashlib.sha256(canonical({k:v for k,v in row.items() if k!='test'}).encode()).hexdigest()
    vectors.setdefault(digest,row);state['calls'].append(digest)
  return call
 for module in modules:
  for name,obj in list(vars(module).items()):
   if name.startswith('_'):continue
   if inspect.isfunction(obj) and obj.__module__==module.__name__:setattr(module,name,wrap(obj,module.__name__+'.'+name))
   elif inspect.isclass(obj) and obj.__module__==module.__name__:
    for method,fn in list(vars(obj).items()):
     if not method.startswith('_') and inspect.isfunction(fn):setattr(obj,method,wrap(fn,module.__name__+'.'+name+'.'+method))
 real_run=subprocess.run
 def run(*a,**kw):
  result=real_run(*a,**kw)
  if kw.get('capture_output') or kw.get('stdout')==subprocess.PIPE:
   state['cli'].append({'kind':'subprocess','test':state['test'],'argv':encode(a[0] if a else kw.get('args')),'stdin':encode(kw.get('input')),'stdout':encode(result.stdout),'stderr':encode(result.stderr),'status':result.returncode})
  return result
 subprocess.run=run
 real_method=unittest.TestCase._callTestMethod
 def scenario(self,method):
  roots=[]
  for name in ('tmp','work'):
   value=getattr(self,name,None)
   if not isinstance(value,(str,Path)):value=getattr(value,'name',value)
   if isinstance(value,(str,Path)) and Path(value).is_dir():roots.append(Path(value))
  root=next(iter(roots),None)
  initial=snapshot(root) if root else []
  archive=None
  if root:
   raw=io.BytesIO()
   with tarfile.open(fileobj=raw,mode='w',format=tarfile.PAX_FORMAT) as tar:
    for path in sorted(root.rglob('*')):
     info=tar.gettarinfo(str(path),arcname=path.relative_to(root).as_posix());info.mtime=0;info.uid=info.gid=0;info.uname=info.gname=''
     if info.isfile():
      with path.open('rb') as stream:tar.addfile(info,stream)
     else:tar.addfile(info)
   packed=gzip.compress(raw.getvalue(),mtime=0);digest=hashlib.sha256(packed).hexdigest();folder=out/'initial';folder.mkdir(exist_ok=True);archive='initial/'+digest+'.tar.gz';(out/archive).write_bytes(packed)
  try:return real_method(self,method)
  finally:
   if root:
    state['scenario'].append({'kind':'B','test':self.id(),'root':str(root),'archive':archive,'initial':initial,'final':snapshot(root),'calls':state['calls'][:], 'request':encode({k:getattr(self,k) for k in ('config','versions','cook','systemctl') if hasattr(self,k)})})
 unittest.TestCase._callTestMethod=scenario
 def trace(frame,event,arg):
  if event=='line':
   filename=frame.f_code.co_filename
   marker='roles/connectome_agent/module_utils/'
   if marker in filename:current_sites.add((filename.split(marker,1)[1],frame.f_lineno))
  return trace
 class Result(unittest.TextTestResult):
  def startTest(self,test):
   super().startTest(test);state['test']=test.id();state['calls']=[];current_sites.clear();sys.settrace(trace)
  def stopTest(self,test):
   sys.settrace(None);coverage[test.id()]=sorted(current_sites);super().stopTest(test)
 suite=unittest.defaultTestLoader.discover(str(source/'tests'))
 with (out/'suite.log').open('w') as stream:result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Result).run(suite)
 for name,rows in [('functions',list(vectors.values())),('scenarios',state['scenario']),('cli',state['cli'])]:
  with (out/(name+'.jsonl')).open('w') as f:
   for row in rows:f.write(canonical(row)+'\n')
 (out/'coverage.golden').write_text(canonical(coverage)+'\n')
 patches=[]
 for path in sorted((source/'tests').glob('test_*.py')):
  tree=ast.parse(path.read_text())
  for node in ast.walk(tree):
   if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name.startswith('test_'):
    uses=[n.lineno for n in ast.walk(node) if isinstance(n,ast.Call) and ('patch' in ast.unparse(n.func) or 'Mock' in ast.unparse(n.func))]
    if uses:patches.append({'file':path.name,'test':node.name,'patch_lines':uses,'disposition':'B scenario or future module unit test; not standalone A replay'})
 (out/'patch-dependent.golden').write_text(canonical(patches)+'\n')
 summary={'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'A':len(vectors),'B':len(state['scenario']),'C':sum(x['kind']=='C' for x in state['cli']),'subprocess':sum(x['kind']=='subprocess' for x in state['cli']),'patch_dependent_tests':len(patches),'uid':os.getuid(),'gid':os.getgid(),'python':sys.version.split()[0]}
 (out/'summary.golden').write_text(canonical(summary)+'\n');print(canonical(summary));return 0 if result.wasSuccessful() else 1
if __name__=='__main__':sys.exit(main())
