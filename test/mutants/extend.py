#!/usr/bin/env python3
"""Retry pending guards using ranked covering legacy tests (optionally bounded).

Each trial is still exactly one unittest; errors/skips/timeouts are not kills.
Results are checkpointed so interruption does not erase completed evidence.
"""
import argparse, concurrent.futures, hashlib, json, os, re, shutil, subprocess, sys, threading
from pathlib import Path
from oracle import DRIVER, ROOT

def main():
 p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--anchors',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--workers',type=int,default=4);p.add_argument('--max-trials',type=int);a=p.parse_args()
 out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);anchors=json.loads(a.anchors.read_text());results=[]
 checkpoint=out/'results.golden'
 if checkpoint.exists():results=json.loads(checkpoint.read_text())
 done={x['id'] for x in results};local=getattr(threading,'local')();lock=threading.Lock();counter=0
 def work(m):
  nonlocal counter
  if not m['tests']:return {'id':m['id'],'status':'uncovered','rows':m['rows'],'trials':[]}
  if not hasattr(local,'tree'):
   with lock:index=counter;counter+=1
   local.tree=out/('worker-'+str(index));shutil.copytree(a.source,local.tree,ignore=shutil.ignore_patterns('__pycache__','.cache'),dirs_exist_ok=True)
  root=local.tree;f=m['file'];target=root/('roles/connectome_agent/module_utils/'+f.removeprefix('module_utils/') if f.startswith('module_utils/') else 'roles/connectome_agent/'+f if f.startswith(('library/','lookup_plugins/','filter_plugins/')) else f)
  original=target.read_text()
  if hashlib.sha256(original.encode()).hexdigest()!=m['source_sha256']:return {'id':m['id'],'status':'anchor-mismatch','trials':[]}
  trials=[];status='survived'
  try:
   target.write_text(original[:m['start']]+m['after']+original[m['end']:]);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
   words=set(re.findall(r'[a-z]{4,}',m['before'].lower()))-{'true','none','type','isinstance','value','data','config','self','entry','required','invalid'}
   tests=sorted(m['tests'],key=lambda t:-len(words & set(re.findall(r'[a-z]{4,}',t.lower().replace('_',' ')))))
   if a.max_trials:tests=tests[:a.max_trials]
   for test in tests:
    result_file=root/'.cache/mutation-result';result_file.parent.mkdir(exist_ok=True);result_file.unlink(missing_ok=True)
    try:
     result=subprocess.run([sys.executable,'-B','-c',DRIVER,str(root),test,str(result_file)],env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,cwd=root)
     verdict=json.loads(result_file.read_text()) if result.returncode==0 else {'driver_error':True}
    except subprocess.TimeoutExpired:verdict={'timeout':True}
    trials.append({'test':test,**verdict})
    if verdict.get('tests')==1 and verdict.get('failures',0)>0 and verdict.get('errors')==0:status='killed';break
    if verdict.get('errors',0)>0 or verdict.get('driver_error') or verdict.get('timeout'):status='incompetent'
  finally:target.write_text(original);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
  return {'id':m['id'],'status':status,'rows':m['rows'],'trials':trials,'eligible_tests':len(m['tests']),'trial_limit':a.max_trials}
 with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
  for result in pool.map(work,[m for m in anchors if m['id'] not in done]):
   results.append(result);temporary=checkpoint.with_suffix('.tmp');temporary.write_text(json.dumps(results,indent=2)+'\n');temporary.replace(checkpoint)
   print(json.dumps({'completed':len(results),'id':result['id'],'status':result['status']}),flush=True)
 print(json.dumps({'mutants':len(results),'statuses':{s:sum(x['status']==s for x in results) for s in sorted({x['status'] for x in results})}}))
if __name__=='__main__':main()
