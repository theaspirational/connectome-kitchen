#!/usr/bin/env python3
"""Measure generated guard mutants directly against recorded pure A goldens."""
import argparse,concurrent.futures,hashlib,json,os,shutil,subprocess,sys,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser();parser.add_argument('--anchors',type=Path,required=True);parser.add_argument('--vectors',type=Path,required=True);parser.add_argument('--results',type=Path,required=True);args=parser.parse_args()
anchors=json.loads(args.anchors.read_text());scratch=ROOT/'.cache/a-mutants';scratch.mkdir(parents=True,exist_ok=True)
local=getattr(threading,'local')();counter=0;mutex=threading.Lock()
def work(mutant):
 global counter
 if not mutant['file'].startswith('module_utils/'):return {'id':mutant['id'],'status':'not-pure-engine'}
 if not hasattr(local,'tree'):
  with mutex:index=counter;counter+=1
  local.tree=scratch/str(index);shutil.copytree(ROOT/'oracle/module_utils',local.tree,ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
 target=local.tree/mutant['file'][len('module_utils/'):];original=target.read_text()
 if hashlib.sha256(original.encode()).hexdigest()!=mutant['source_sha256']:return {'id':mutant['id'],'status':'anchor-mismatch'}
 target.write_text(original[:mutant['start']]+mutant['after']+original[mutant['end']:]);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
 try:
  result=subprocess.run([sys.executable,'-B',str(ROOT/'test/differential/replay_a.py'),'--modules',str(local.tree),'--vectors',str(args.vectors.resolve())],capture_output=True,timeout=30)
  try:summary=json.loads(result.stdout)
  except ValueError:return {'id':mutant['id'],'status':'incompetent'}
  return {'id':mutant['id'],'status':'killed' if summary['mismatches'] else 'survived',**summary}
 finally:target.write_text(original);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 for result in pool.map(work,anchors):
  results.append(result)
  if len(results)%100==0:print('completed',len(results),flush=True)
args.results.write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps({'mutants':len(results),'statuses':{s:sum(r['status']==s for r in results) for s in sorted({r['status'] for r in results})}}))
