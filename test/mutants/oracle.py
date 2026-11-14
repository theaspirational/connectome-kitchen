#!/usr/bin/env python3
"""Exact-anchor oracle guard mutants, selected by recorded line coverage.

A test failure kills a mutant; skipped tests, import/runtime errors, timeouts
and missing coverage never count as kills. Source anchors are hashed and
validated before any mutation. Workers use separate local scratch trees.
"""
import argparse, ast, concurrent.futures, hashlib, importlib.machinery, json, os
from pathlib import Path
import shutil, subprocess, sys, threading, time
ROOT=Path(__file__).resolve().parents[2]
loader=importlib.machinery.SourceFileLoader('inventory',str(ROOT/'scripts/inventory-sites'));inventory=loader.load_module()

def offset(source,line,column):return sum(len(s) for s in source.splitlines(keepends=True)[:line-1])+column

def build(root,coverage):
 rows=inventory.inventory(ROOT/'docs/approved-plan.md');engine=inventory.files(root);mutants={}
 for filename,path in sorted(engine.items()):
  source=path.read_text();tree=ast.parse(source)
  for node in ast.walk(tree):
   replacement=None;target=node
   if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('need','require') and node.args:
    target=node.args[0];replacement='True'
   elif isinstance(node,ast.Raise):replacement='pass'
   if replacement is None:continue
   memberships=[r['row'] for r in rows if any(f==filename and a<=node.lineno<=b for f,a,b in r['refs'])]
   if not memberships:continue
   a=offset(source,target.lineno,target.col_offset);b=offset(source,target.end_lineno,target.end_col_offset);before=source[a:b]
   if before==replacement:continue
   # A Raise can be a statement but an argument is always an expression.
   mutated=source[:a]+replacement+source[b:]
   try:compile(mutated,str(path),'exec')
   except SyntaxError:continue
   tests=[]
   for test,sites in coverage.items():
    key='connectome_spawn/'+filename[6:] if filename.startswith('spawn/') else 'connectome_'+filename
    if [key,node.lineno] in sites:tests.append((len(sites),test))
   tests=[test for _,test in sorted(tests)]
   ident=filename.replace('/','.').removesuffix('.py')+'.L'+str(node.lineno)+'.'+str(node.col_offset)
   mutants[ident]={'id':ident,'rows':memberships,'file':str(path.relative_to(root)),'line':node.lineno,'start':a,'end':b,'before':before,'after':replacement,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'tests':tests[:3]}
 return list(mutants.values()),rows
DRIVER=r'''
import json,sys,unittest
from pathlib import Path
root=Path(sys.argv[1]);sys.path[:0]=[str(root/'tests'),str(root/'roles/connectome_agent/module_utils')]
suite=unittest.defaultTestLoader.loadTestsFromName(sys.argv[2])
with open(sys.argv[3]+'.log','w') as stream:r=unittest.TextTestRunner(stream=stream,verbosity=0).run(suite)
Path(sys.argv[3]).write_text(json.dumps({'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skipped':len(r.skipped)}))
'''
local=getattr(threading,'local')();counter=0;mutex=threading.Lock()
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--recording',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--workers',type=int,default=4);parser.add_argument('--limit',type=int);args=parser.parse_args()
 source=args.source.resolve();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
 # Inventory paths are oracle-relative, while tests require their original layout.
 mutants,rows=build(ROOT/'oracle',json.loads((args.recording/'coverage.golden').read_text()))
 if args.limit:mutants=mutants[:args.limit]
 (out/'anchors.golden').write_text(json.dumps(mutants,indent=2)+'\n')
 def work(mutant):
  global counter
  if not hasattr(local,'root'):
   with mutex:index=counter;counter+=1
   local.tree=out/('worker-'+str(index));shutil.copytree(source,local.tree,ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
  root=local.tree;file=mutant['file']
  target=root/('roles/connectome_agent/module_utils/'+file[len('module_utils/'):] if file.startswith('module_utils/') else 'roles/connectome_agent/'+file if file.startswith(('library/','lookup_plugins/','filter_plugins/')) else file)
  original=target.read_text();reference=(ROOT/'oracle'/file).read_text()
  # Only fixture identities differ in the full baseline; engine must match bytewise.
  if original!=reference:return {'id':mutant['id'],'status':'anchor-mismatch','rows':mutant['rows']}
  trials=[];status='uncovered' if not mutant['tests'] else 'survived'
  try:
   target.write_text(original[:mutant['start']]+mutant['after']+original[mutant['end']:]);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
   for test in mutant['tests']:
    result_file=root/'.cache/mutation-result';result_file.parent.mkdir(exist_ok=True)
    try:
     result=subprocess.run([sys.executable,'-B','-c',DRIVER,str(root),test,str(result_file)],env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30,cwd=root)
     verdict=json.loads(result_file.read_text()) if result.returncode==0 else {'driver_error':True}
    except subprocess.TimeoutExpired:verdict={'timeout':True}
    trials.append({'test':test,**verdict})
    if verdict.get('tests')==1 and verdict.get('failures',0)>0 and verdict.get('errors')==0:status='killed';break
    if verdict.get('errors',0)>0 or verdict.get('driver_error') or verdict.get('timeout'):status='incompetent'
  finally:target.write_text(original);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
  return {'id':mutant['id'],'status':status,'rows':mutant['rows'],'trials':trials}
 results=[]
 with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
  for result in pool.map(work,mutants):
   results.append(result)
   if len(results)%50==0:print('completed',len(results),'of',len(mutants),flush=True)
 (out/'results.golden').write_text(json.dumps(results,indent=2)+'\n')
 summary={'mutants':len(results),'statuses':{s:sum(x['status']==s for x in results) for s in sorted({x['status'] for x in results})},'rows':[]}
 for row in rows:
  matches=[r for r in results if row['row'] in r['rows']]
  summary['rows'].append({'row':row['row'],'named_ids':row['ids'],'generated':len(matches),'killed':sum(x['status']=='killed' for x in matches),'future':row['future']})
 (out/'summary.golden').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps({k:v for k,v in summary.items() if k!='rows'}));return int(any(x['status']!='killed' for x in results))
if __name__=='__main__':sys.exit(main())
