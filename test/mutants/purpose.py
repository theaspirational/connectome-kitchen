#!/usr/bin/env python3
"""Run purpose-built killing tests (`test/unit/test_guard_*.py`) against exact mutants.

The map of mutant id -> test id lives in `test/mutants/purpose/*.json` (`kills`).
A mutant id is a guard-site id from `guard-anchors.golden` or a named site id
(`<name>#<n>`) from `named-site-anchors.golden`.

A kill needs both halves, each as a solo run of exactly that one test in a
scratch copy of `oracle/` and `test/unit/`:
  unmutated: 1 test ran, 0 failures, 0 errors, 0 skipped;
  mutated:   1 test ran, at least 1 failure, 0 errors.
An error, skip or timeout under the mutant is recorded and is not a kill.

  purpose.py try --id ID --test TEST     one pair, printed, nothing written
  purpose.py run [--only guard|named]    every mapped pair -> results goldens
"""
import argparse, concurrent.futures, hashlib, json, os, shutil, subprocess, sys, threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from named_sites import fragments
ROOT=Path(__file__).resolve().parents[2]
M=ROOT/'test/mutants'
SOLO=r'''
import json,sys,unittest
from pathlib import Path
suite=unittest.defaultTestLoader.loadTestsFromName(sys.argv[1])
with open(sys.argv[2]+'.log','w') as stream:r=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
Path(sys.argv[2]).write_text(json.dumps({'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skipped':len(r.skipped)}))
'''
ENV=dict(os.environ,PYTHONDONTWRITEBYTECODE='1')

def anchors(area=None):
 # With an area, named sites are rebuilt in memory from that one fragment, so
 # a fragment under edit can be tried before the committed golden is refreshed.
 import named_sites
 result={}
 for rows in (json.loads((M/'guard-anchors.golden').read_text()),named_sites.build(area)[0] if area else json.loads((M/'named-site-anchors.golden').read_text())):
  for row in rows:
   if row['id'] in result:raise ValueError('duplicate mutant id')
   result[row['id']]=row
 return result

def read_sites():
 import named_sites
 return named_sites.build()[0]

def kills():
 result={}
 for name,data in fragments().items():
  for ident,test in data.get('kills',{}).items():
   if ident in result:raise ValueError('mutant mapped twice: '+ident)
   result[ident]=test
 return result

class Scratch:
 def __init__(self,out):self.out=out;self.slot=getattr(threading,'local')();self.lock=threading.Lock();self.counter=0
 def tree(self):
  if not hasattr(self.slot,'tree'):
   with self.lock:index=self.counter;self.counter+=1
   base=self.out/('worker-'+str(index));shutil.rmtree(base,ignore_errors=True)
   shutil.copytree(ROOT/'oracle',base/'oracle',ignore=shutil.ignore_patterns('__pycache__','.cache'))
   shutil.copytree(ROOT/'test/unit',base/'test/unit',ignore=shutil.ignore_patterns('__pycache__'))
   for extra in ('test/differential','test/mutants','scripts','docs','test/vectors'):
    shutil.copytree(ROOT/extra,base/extra,ignore=shutil.ignore_patterns('__pycache__'))
   self.slot.tree=base
  return self.slot.tree

def solo(tree,test,timeout=120):
 result=tree/'result.json';result.unlink(missing_ok=True)
 try:done=subprocess.run([sys.executable,'-B','-c',SOLO,test,str(result)],cwd=tree/'test/unit',env=ENV,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout)
 except subprocess.TimeoutExpired:return {'timeout':True}
 if done.returncode!=0 or not result.exists():return {'driver_error':True}
 return json.loads(result.read_text())

def passes(v):return v.get('tests')==1 and v.get('failures')==0 and v.get('errors')==0 and v.get('skipped')==0
def kill(v):return v.get('tests')==1 and v.get('failures',0)>0 and v.get('errors')==0 and not v.get('timeout') and not v.get('driver_error')

def pair(tree,mutant,test):
 base=solo(tree,test)
 if not passes(base):return {'id':mutant['id'],'status':'baseline-not-passing','test':test,'baseline':base,'trials':[]}
 target=tree/'oracle'/mutant['file'];original=target.read_text()
 if hashlib.sha256(original.encode()).hexdigest()!=mutant['source_sha256'] or original[mutant['start']:mutant['end']]!=mutant['before']:return {'id':mutant['id'],'status':'anchor-mismatch','test':test,'trials':[]}
 try:
  target.write_text(original[:mutant['start']]+mutant['after']+original[mutant['end']:]);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
  verdict=solo(tree,test)
  log=(tree/'result.json.log').read_text()[-1500:] if (tree/'result.json.log').exists() else ''
 finally:target.write_text(original);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
 status='killed' if kill(verdict) else 'incompetent' if verdict.get('errors') or verdict.get('timeout') or verdict.get('driver_error') else 'survived'
 return {'id':mutant['id'],'status':status,'test':test,'trials':[{'test':test,**verdict}],'_log':log}

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 t=sub.add_parser('try');t.add_argument('--id',required=True);t.add_argument('--test',required=True);t.add_argument('--area');t.add_argument('--out',type=Path,default=ROOT/'.cache/purpose-try')
 r=sub.add_parser('run');r.add_argument('--out',type=Path,default=ROOT/'.cache/purpose-run');r.add_argument('--workers',type=int,default=8);r.add_argument('--check',action='store_true',help='compare with committed results instead of writing')
 a=p.parse_args();known=anchors(getattr(a,'area',None))
 if a.command=='try':
  out=(a.out/str(os.getpid())).resolve();out.mkdir(parents=True,exist_ok=True)
  try:
   result=pair(Scratch(out).tree(),known[a.id],a.test);log=result.pop('_log','')
   print(json.dumps(result));
   if result['status']!='killed':print(log)
  finally:shutil.rmtree(out,ignore_errors=True)
  return int(result['status']!='killed')
 if read_sites()!=json.loads((M/'named-site-anchors.golden').read_text()):raise ValueError('stale named-site-anchors.golden; run named_sites.py')
 out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);scratch=Scratch(out);mapping=kills()
 if set(mapping)-set(known):raise ValueError('kill mapped to unknown mutant: '+', '.join(sorted(set(mapping)-set(known))))
 def work(item):
  result=pair(scratch.tree(),known[item[0]],item[1]);result.pop('_log',None);return result
 with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:results=list(pool.map(work,sorted(mapping.items())))
 bad=False
 for name,rows in [('purpose-results.golden',[x for x in results if '#' not in x['id']]),('named-purpose-results.golden',[x for x in results if '#' in x['id']])]:
  data=json.dumps(rows,indent=2)+'\n'
  if a.check:
   if (M/name).read_text()!=data:print('STALE '+name);bad=True
  else:(M/name).write_text(data)
 statuses={s:sum(x['status']==s for x in results) for s in sorted({x['status'] for x in results})}
 print(json.dumps({'mapped':len(results),'statuses':statuses,'not_killed':[x['id'] for x in results if x['status']!='killed']}))
 return int(bad or any(x['status']!='killed' for x in results))
if __name__=='__main__':sys.exit(main())
