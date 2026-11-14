#!/usr/bin/env python3
"""Exhaustive legacy-suite sweep for exact-anchor mutants, self-contained in this repository.

`search` applies each mutant to a worker-local copy of `oracle/`, runs every
frozen test module, and then re-runs each newly failing test on its own. Only
that solo run is evidence: exactly one test ran, it failed, nothing errored.
A test is eligible only if its solo run passes on the unmutated copy.

`verify` re-runs just the recorded killing test of each committed kill, and
fails unless every one still passes unmutated and still fails mutated.

Errors, skips, timeouts and driver failures are never kills.
"""
import argparse, concurrent.futures, hashlib, json, os, shutil, subprocess, sys, threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
SOLO=r'''
import json,sys,unittest
from pathlib import Path
root=Path(sys.argv[1]);sys.path[:0]=[str(root/'tests'),str(root/'module_utils')]
suite=unittest.defaultTestLoader.loadTestsFromName(sys.argv[2])
with open(sys.argv[3]+'.log','w') as stream:r=unittest.TextTestRunner(stream=stream,verbosity=0).run(suite)
Path(sys.argv[3]).write_text(json.dumps({'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skipped':len(r.skipped)}))
'''
MODULE=r'''
import json,sys,unittest
from pathlib import Path
root=Path(sys.argv[1]);sys.path[:0]=[str(root/'tests'),str(root/'module_utils')]
suite=unittest.defaultTestLoader.loadTestsFromName(sys.argv[2])
def names(s):
 for t in s:
  if isinstance(t,unittest.TestSuite):yield from names(t)
  else:yield t.id()
listed=list(names(suite))
with open(sys.argv[3]+'.log','w') as stream:r=unittest.TextTestRunner(stream=stream,verbosity=0).run(suite)
Path(sys.argv[3]).write_text(json.dumps({'listed':listed,'failures':[t.id() for t,_ in r.failures],'errors':[str(t.id()) if hasattr(t,'id') else str(t) for t,_ in r.errors],'skipped':[t.id() for t,_ in r.skipped]}))
'''
ENV=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')

def modules():return sorted(p.stem for p in (ROOT/'oracle/tests').glob('test_*.py'))

def run(script,tree,name,timeout):
 result=tree.parent/'result.json';result.unlink(missing_ok=True)
 try:
  done=subprocess.run([sys.executable,'-B','-c',script,str(tree),name,str(result)],env=ENV,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout,cwd=tree)
 except subprocess.TimeoutExpired:return {'timeout':True}
 if done.returncode!=0 or not result.exists():return {'driver_error':True}
 return json.loads(result.read_text())

def solo_kill(verdict):
 return verdict.get('tests')==1 and verdict.get('failures',0)>0 and verdict.get('errors')==0 and not verdict.get('timeout') and not verdict.get('driver_error')
def solo_pass(verdict):
 return verdict.get('tests')==1 and verdict.get('failures')==0 and verdict.get('errors')==0 and verdict.get('skipped')==0

def target_of(tree,mutant):return tree/mutant['file']

class Mutated:
 def __init__(self,tree,mutant):
  self.target=target_of(tree,mutant);self.mutant=mutant
 def __enter__(self):
  self.original=self.target.read_text()
  if hashlib.sha256(self.original.encode()).hexdigest()!=self.mutant['source_sha256']:raise ValueError('anchor-mismatch')
  if self.original[self.mutant['start']:self.mutant['end']]!=self.mutant['before']:raise ValueError('anchor-mismatch')
  self.target.write_text(self.original[:self.mutant['start']]+self.mutant['after']+self.original[self.mutant['end']:])
  shutil.rmtree(self.target.parent/'__pycache__',ignore_errors=True)
 def __exit__(self,*exc):
  self.target.write_text(self.original);shutil.rmtree(self.target.parent/'__pycache__',ignore_errors=True)

class Workers:
 def __init__(self,out):
  self.out=out;self.slot=getattr(threading,'local')();self.lock=threading.Lock();self.counter=0
 def tree(self):
  if not hasattr(self.slot,'tree'):
   with self.lock:index=self.counter;self.counter+=1
   base=self.out/('worker-'+str(index));shutil.rmtree(base,ignore_errors=True)
   shutil.copytree(ROOT/'oracle',base/'oracle',ignore=shutil.ignore_patterns('__pycache__','.cache'))
   self.slot.tree=base/'oracle'
  return self.slot.tree

def baseline(workers,pool):
 """Solo status of every frozen test on an unmutated worker copy."""
 def listing(module):
  tree=workers.tree();verdict=run(MODULE,tree,module,600)
  if 'listed' not in verdict:raise ValueError('baseline module failed: '+module)
  return verdict
 eligible=set()
 for verdict in pool.map(listing,modules()):
  eligible.update(set(verdict['listed'])-set(verdict['failures'])-set(verdict['errors'])-set(verdict['skipped']))
 def one(test):return test,run(SOLO,workers.tree(),test,120)
 return sorted(test for test,verdict in pool.map(one,sorted(eligible)) if solo_pass(verdict))

def search(args):
 out=args.out.resolve();out.mkdir(parents=True,exist_ok=True);anchors=json.loads(args.anchors.read_text())
 if args.only:
  wanted=set(json.loads(args.only.read_text()));anchors=[m for m in anchors if m['id'] in wanted]
 checkpoint=out/'results.golden';results=json.loads(checkpoint.read_text()) if checkpoint.exists() else []
 done={r['id'] for r in results};workers=Workers(out)
 with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
  cache=out/'eligible.golden'
  if cache.exists():eligible=json.loads(cache.read_text())
  else:eligible=baseline(workers,pool);cache.write_text(json.dumps(eligible,indent=1)+'\n')
  allowed=set(eligible);print(json.dumps({'eligible_tests':len(eligible)}),flush=True)
  def work(mutant):
   tree=workers.tree();trials=[];status='survived';candidates=[]
   try:
    with Mutated(tree,mutant):
     for module in modules():
      verdict=run(MODULE,tree,module,600)
      if 'listed' not in verdict:
       # A hung or crashed module is retried test by test below.
       candidates+=[t for t in eligible if t.startswith(module+'.')];continue
      candidates+=[t for t in verdict['failures'] if t in allowed]
     for test in candidates:
      verdict=run(SOLO,tree,test,120);trials.append({'test':test,**verdict})
      if solo_kill(verdict):status='killed';break
   except ValueError:return {'id':mutant['id'],'status':'anchor-mismatch','trials':[]}
   if status=='killed':trials=[trials[-1]]
   return {'id':mutant['id'],'status':status,'rows':mutant.get('rows',[]),'trials':trials,'eligible_tests':len(eligible),'scope':'every eligible frozen test'}
  for result in pool.map(work,[m for m in anchors if m['id'] not in done]):
   results.append(result);temporary=checkpoint.with_suffix('.tmp');temporary.write_text(json.dumps(results,indent=2)+'\n');temporary.replace(checkpoint)
   print(json.dumps({'completed':len(results),'id':result['id'],'status':result['status']}),flush=True)
 print(json.dumps({'mutants':len(results),'statuses':{s:sum(x['status']==s for x in results) for s in sorted({x['status'] for x in results})}}))
 return 0

def verify(args):
 out=args.out.resolve();out.mkdir(parents=True,exist_ok=True);anchors={m['id']:m for m in json.loads(args.anchors.read_text())}
 rows=[r for r in json.loads(args.results.read_text()) if r['status']=='killed'];workers=Workers(out)
 def work(row):
  tree=workers.tree();test=row['trials'][-1]['test']
  if not solo_pass(run(SOLO,tree,test,120)):return row['id'],'baseline-not-passing'
  try:
   with Mutated(tree,anchors[row['id']]):verdict=run(SOLO,tree,test,120)
  except ValueError:return row['id'],'anchor-mismatch'
  return row['id'],'killed' if solo_kill(verdict) else 'not-reproduced'
 with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:outcome=dict(pool.map(work,rows))
 bad={k:v for k,v in outcome.items() if v!='killed'}
 print(json.dumps({'recorded_kills':len(rows),'reproduced':len(rows)-len(bad),'not_reproduced':bad},sort_keys=True))
 return int(bool(bad))

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 s=sub.add_parser('search');s.add_argument('--anchors',type=Path,required=True);s.add_argument('--only',type=Path,help='JSON list of mutant ids');s.add_argument('--out',type=Path,required=True);s.add_argument('--workers',type=int,default=4)
 v=sub.add_parser('verify');v.add_argument('--anchors',type=Path,required=True);v.add_argument('--results',type=Path,required=True);v.add_argument('--out',type=Path,required=True);v.add_argument('--workers',type=int,default=4)
 a=p.parse_args();return search(a) if a.command=='search' else verify(a)
if __name__=='__main__':sys.exit(main())
