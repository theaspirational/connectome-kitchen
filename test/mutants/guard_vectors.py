#!/usr/bin/env python3
"""Exact-anchor guard mutations against independently executable data vectors."""
import argparse, concurrent.futures, hashlib, json, os, shutil, subprocess, sys, threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def main():
 p=argparse.ArgumentParser();p.add_argument('--anchors',type=Path,required=True);p.add_argument('--vectors',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 out=a.out.resolve();out.mkdir(exist_ok=True,parents=True);anchors=json.loads(a.anchors.read_text());local=getattr(threading,'local')();lock=threading.Lock();counter=0
 # A killing mismatch is meaningful only after this corpus passes unchanged.
 baseline=subprocess.run([sys.executable,'-B',str(ROOT/'test/differential/guard_vectors.py'),'replay','--vectors',str(a.vectors.resolve())],capture_output=True,timeout=120)
 if baseline.returncode!=0:raise ValueError('negative-vector baseline failed')
 modules={}
 for line in a.vectors.read_text().splitlines():
  row=json.loads(line);module=row['function'].rsplit('.',1)[0];modules.setdefault(module,[]).append(line)
 for module,lines in modules.items():(out/(module+'.golden')).write_text('\n'.join(lines)+'\n')
 def work(m):
  nonlocal counter
  if not m['file'].startswith('module_utils/'):return {'id':m['id'],'status':'not-pure-engine'}
  module=m['file'].removeprefix('module_utils/').removesuffix('.py').replace('/','.')
  if module not in modules:return {'id':m['id'],'status':'no-vectors'}
  if not hasattr(local,'tree'):
   with lock:index=counter;counter+=1
   local.tree=out/('modules-'+str(index));shutil.copytree(ROOT/'oracle/module_utils',local.tree,ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
  target=local.tree/m['file'].removeprefix('module_utils/');original=target.read_text()
  if hashlib.sha256(original.encode()).hexdigest()!=m['source_sha256']:return {'id':m['id'],'status':'anchor-mismatch'}
  try:
   target.write_text(original[:m['start']]+m['after']+original[m['end']:]);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
   try:
    r=subprocess.run([sys.executable,'-B',str(ROOT/'test/differential/guard_vectors.py'),'--modules',str(local.tree),'replay','--vectors',str(out/(module+'.golden'))],capture_output=True,timeout=60,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
   except subprocess.TimeoutExpired:return {'id':m['id'],'status':'timeout'}
   try:summary=json.loads(r.stdout)
   except ValueError:return {'id':m['id'],'status':'driver-error'}
   witnesses=summary.pop('witnesses')
   return {'id':m['id'],'status':'killed' if witnesses else 'runtime-error' if summary['runtime_errors'] else 'survived',**summary,'witnesses':witnesses[:3]}
  finally:target.write_text(original);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
 results=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
  for result in pool.map(work,anchors):
   results.append(result)
   if len(results)%25==0:print('completed',len(results),flush=True)
   (out/'results.golden').write_text(json.dumps(results,indent=2)+'\n')
 print(json.dumps({'mutants':len(results),'statuses':{s:sum(x['status']==s for x in results) for s in sorted({x['status'] for x in results})}}))
if __name__=='__main__':main()
