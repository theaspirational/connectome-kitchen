#!/usr/bin/env python3
"""Function-level kind-A refusal vectors for mutants that can only crash.

Some guard mutants cannot be made to accept bad input or to reach a different
declared refusal: with the guard gone, every input that reached it makes Python
raise a runtime error (TypeError, KeyError, UnboundLocalError, ...). Engine
boundaries collapse that crash and the original refusal into the same closed
code, so only a function-level vector sees the difference. Plan 3.4.2 counts a
changed kind-A golden (exception class, code, message) as coverage.

This is a SEPARATE evidence category, `killed-kind-A-crash-only`. It is counted
on its own, never in the clean killed total, and `check-module-ready` keeps a
separate blocking line for it until the category is accepted in review.

A vector is declared in `test/mutants/purpose/*.golden` under `crash_only`:
  {"id": "<mutant id>", "function": "connectome_inputs.payloads", "args": [...], "kwargs": {...},
   "tree": {"dir/file": {"text": "...", "mode": 384}, "dir/data.json": {"json": {...}}, "dir": {"dir": 448}, "link": {"link": "target"}}}
`args`/`kwargs` use the tagged encoding of `test/differential/codec.py`; the
text `${ROOT}` stands for a fresh scratch directory holding the optional `tree`.

`build` records the frozen oracle's outcome for each vector. `run` then
requires, for every vector:
  original:  the recorded outcome again, and it is a declared refusal
             (an exception derived from ValueError: SafeError, LockError, SpawnError, ...);
  mutated:   an exception that is NOT derived from ValueError (a crash).
A mutant that returns normally or meets another declared refusal under the
vector is not crash-only: it needs an ordinary killing test instead, and `run`
reports it as `not-crash-only` without counting it. A crash of the harness
itself (driver error, timeout) counts as nothing.
"""
import argparse, concurrent.futures, hashlib, json, os, shutil, subprocess, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from named_sites import fragments
ROOT=Path(__file__).resolve().parents[2]
M=ROOT/'test/mutants'
VECTORS=ROOT/'test/vectors/crash-only.golden'
CATEGORY='killed-kind-A-crash-only'
CHILD=r'''
import importlib, json, os, sys, tempfile
from pathlib import Path
oracle=Path(sys.argv[1]);sys.path[:0]=[str(oracle/'module_utils'),sys.argv[2]]
from codec import decode
vector=json.loads(sys.stdin.read())
with tempfile.TemporaryDirectory() as folder:
 root=Path(folder).resolve()
 for name,entry in sorted(vector.get('tree',{}).items()):
  path=root/name
  if '..' in Path(name).parts or Path(name).is_absolute():raise SystemExit(3)
  path.parent.mkdir(parents=True,exist_ok=True)
  if 'dir' in entry:path.mkdir(exist_ok=True);path.chmod(entry['dir'])
  elif 'link' in entry:path.symlink_to(entry['link'].replace('${ROOT}',str(root)))
  else:path.write_text((json.dumps(entry['json']) if 'json' in entry else entry['text']).replace('${ROOT}',str(root)));path.chmod(entry.get('mode',0o600))
 bound=json.loads(json.dumps([vector['args'],vector.get('kwargs',{})]).replace('${ROOT}',str(root)))
 module,name=vector['function'].rsplit('.',1);target=importlib.import_module(module)
 for part in name.split(':'):target=getattr(target,part)
 try:
  result=target(*decode(bound[0]),**decode(bound[1]))
  outcome={'returned':type(result).__name__}
 except BaseException as error:
  outcome={'exception':type(error).__name__,'code':getattr(error,'code',None) if isinstance(error,ValueError) else None,'message':str(error).replace(str(root),'${ROOT}'),'declared_refusal':isinstance(error,ValueError)}
print('\n'+json.dumps(outcome))
'''

def declared():
 result=[];seen=set()
 for area,data in fragments().items():
  for vector in data.get('crash_only',[]):
   if set(vector)-{'id','function','args','kwargs','tree'} or not {'id','function','args'}<=set(vector):raise ValueError('invalid crash-only vector: '+str(vector.get('id')))
   if vector['id'] in seen:raise ValueError('two crash-only vectors for one mutant: '+vector['id'])
   if not vector['function'].startswith('connectome_'):raise ValueError('crash-only vectors call oracle module functions only')
   seen.add(vector['id']);result.append(dict(vector,area=area))
 return sorted(result,key=lambda v:v['id'])

def anchors():
 result={}
 for file in ('guard-anchors.golden','named-site-anchors.golden'):
  for row in json.loads((M/file).read_text()):result[row['id']]=row
 return result

def invoke(oracle,vector):
 try:done=subprocess.run([sys.executable,'-B','-c',CHILD,str(oracle),str(ROOT/'test/differential')],input=json.dumps(vector).encode(),capture_output=True,timeout=60,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
 except subprocess.TimeoutExpired:return {'harness':'timeout'}
 lines=done.stdout.decode(errors='replace').splitlines()
 if done.returncode or not lines:return {'harness':'driver-error'}
 try:return json.loads(lines[-1])
 except ValueError:return {'harness':'driver-error'}

def build():
 rows=[]
 for vector in declared():
  outcome=invoke(ROOT/'oracle',vector)
  if not outcome.get('declared_refusal'):raise ValueError('not a declared refusal on the frozen oracle: '+vector['id']+' '+json.dumps(outcome))
  rows.append({**{k:vector[k] for k in ('id','function','args') },'kwargs':vector.get('kwargs',{}),**({'tree':vector['tree']} if 'tree' in vector else {}),'outcome':{k:outcome[k] for k in ('exception','code','message')}})
 return rows

def run(out,workers):
 vectors=[json.loads(line) for line in VECTORS.read_text().splitlines()];known=anchors();slot=getattr(threading,'local')();lock=threading.Lock();counter=[0]
 def work(vector):
  if not hasattr(slot,'tree'):
   with lock:index=counter[0];counter[0]+=1
   slot.tree=out/('worker-'+str(index));shutil.rmtree(slot.tree,ignore_errors=True);shutil.copytree(ROOT/'oracle',slot.tree/'oracle',ignore=shutil.ignore_patterns('__pycache__','.cache'))
  oracle=slot.tree/'oracle';mutant=known[vector['id']];expected=dict(vector['outcome'],declared_refusal=True)
  row={'id':vector['id'],'vector_sha256':hashlib.sha256(json.dumps(vector,sort_keys=True).encode()).hexdigest()}
  original=invoke(oracle,vector)
  if original!=expected:return {**row,'status':'baseline-mismatch','original':original}
  target=oracle/mutant['file'];source=target.read_text()
  if hashlib.sha256(source.encode()).hexdigest()!=mutant['source_sha256'] or source[mutant['start']:mutant['end']]!=mutant['before']:return {**row,'status':'anchor-mismatch'}
  try:
   target.write_text(source[:mutant['start']]+mutant['after']+source[mutant['end']:]);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
   mutated=invoke(oracle,vector)
  finally:target.write_text(source);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
  if 'harness' in mutated:status='harness-'+mutated['harness']
  elif 'exception' in mutated and not mutated['declared_refusal']:status=CATEGORY
  elif mutated==expected:status='survived'
  else:status='not-crash-only'
  return {**row,'status':status,'original':vector['outcome'],'mutated':{k:v for k,v in mutated.items() if k in ('exception','returned')}}
 with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:return list(pool.map(work,vectors))

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 b=sub.add_parser('build');b.add_argument('--check',action='store_true')
 r=sub.add_parser('run');r.add_argument('--check',action='store_true');r.add_argument('--out',type=Path,default=ROOT/'.cache/crash-only');r.add_argument('--workers',type=int,default=8)
 t=sub.add_parser('try');t.add_argument('--area',required=True);t.add_argument('--id',required=True)
 a=p.parse_args()
 if a.command=='try':
  import named_sites
  vector=next(v for v in fragments(a.area)[a.area].get('crash_only',[]) if v['id']==a.id)
  known={r['id']:r for r in json.loads((M/'guard-anchors.golden').read_text())};known.update({r['id']:r for r in named_sites.build(a.area)[0]})
  original=invoke(ROOT/'oracle',vector);print('original:',json.dumps(original))
  with tempfile.TemporaryDirectory(dir=str((ROOT/'.cache'))) as folder:
   oracle=Path(folder)/'oracle';shutil.copytree(ROOT/'oracle',oracle,ignore=shutil.ignore_patterns('__pycache__','.cache'));mutant=known[a.id];target=oracle/mutant['file'];source=target.read_text()
   target.write_text(source[:mutant['start']]+mutant['after']+source[mutant['end']:]);mutated=invoke(oracle,vector)
  print('mutated: ',json.dumps(mutated))
  ok=bool(original.get('declared_refusal')) and 'exception' in mutated and not mutated.get('declared_refusal')
  print(CATEGORY if ok else 'NOT a crash-only kill (original must be a declared refusal, mutated must be a non-refusal exception)');return int(not ok)
 if a.command=='build':
  data=''.join(json.dumps(row,sort_keys=True)+'\n' for row in build())
  if a.check:
   if not VECTORS.exists() or VECTORS.read_text()!=data:print('STALE test/vectors/crash-only.golden');return 1
  else:VECTORS.write_text(data)
  print(json.dumps({'crash_only_vectors':len(data.splitlines())}));return 0
 out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);results=run(out,a.workers);data=json.dumps(results,indent=2)+'\n';target=M/'crash-only-results.golden'
 if a.check:
  if not target.exists() or target.read_text()!=data:print('STALE crash-only-results.golden');return 1
 else:target.write_text(data)
 statuses={s:sum(r['status']==s for r in results) for s in sorted({r['status'] for r in results})}
 print(json.dumps({'vectors':len(results),'statuses':statuses,'not_counted':[r['id'] for r in results if r['status']!=CATEGORY]}));return int(any(r['status']!=CATEGORY for r in results))
if __name__=='__main__':sys.exit(main())
