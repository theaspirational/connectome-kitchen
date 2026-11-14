#!/usr/bin/env python3
"""Observe what each recorded B scenario's test method calls, without changing it.

`test/vectors/scenario-classes.golden` says which entry points a recorded
unittest method reached. This adds how often, and what every `Deployment` the
method built was given as `cook_command` and `analyze_command`.
`scripts/unreachable-note` words its reasons from these facts, so a group
reason cannot claim something one of its records contradicts.

Observation only. A `sys.setprofile` hook counts calls of the seven entry
points and reads the two constructor arguments. Nothing is patched or
replaced, and no status changes. A call the method replaced by a mock is not
a call of the entry point and is not counted.

Commands are recorded as labels, never as argv: the path inside `oracle/` of
the first argument that lies there (`tests/fixtures/cook.py`,
`scripts/cook-adapter.py`), `unset` for None, `empty` for an empty argv,
`inline` for an interpreter `-c` script, else `other`.

Some frozen tests skip unless the host has their integration prerequisites.
A skipped method is recorded as `observed: false` and nothing is claimed about
it. `--check` therefore compares the records observed both here and in the
committed file, reports how many it could not compare, and fails on any
difference among the rest. It takes about two minutes.
"""
import argparse, collections, io, json, sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
ORACLE=ROOT/'oracle'
TARGET=ROOT/'test/vectors/scenario-calls.golden'
ENTRY={('connectome_cook.py','adapt'):'cook:adapt',('connectome_deploy.py','Deployment.prepare'):'deploy:Deployment.prepare',('connectome_deploy.py','Deployment.activate'):'deploy:Deployment.activate',
 ('planner.py','make_plan'):'planner:make_plan',('lifecycle.py','Lifecycle.create'):'lifecycle:Lifecycle.create',('lifecycle.py','Lifecycle.transition'):'lifecycle:Lifecycle.transition',('lifecycle.py','Lifecycle.status'):'lifecycle:Lifecycle.status'}

def label(argv):
 if argv is None:return 'unset'
 if not isinstance(argv,(list,tuple)):return 'other'
 if not argv:return 'empty'
 for item in argv:
  if isinstance(item,str) and item.startswith(str(ORACLE)+'/'):return Path(item).relative_to(ORACLE).as_posix()
 return 'inline' if '-c' in argv else 'other'

def observe(ident):
 calls=collections.Counter();built=[]
 def hook(frame,event,arg):
  if event!='call':return
  code=frame.f_code;where=(Path(code.co_filename).name,code.co_qualname)
  if where in ENTRY:calls[ENTRY[where]]+=1
  elif where==('connectome_deploy.py','Deployment.__init__'):built.append((label(frame.f_locals.get('cook_command')),label(frame.f_locals.get('analyze_command'))))
 suite=unittest.defaultTestLoader.loadTestsFromName(ident);sys.setprofile(hook)
 try:result=unittest.TextTestRunner(stream=io.StringIO()).run(suite)
 finally:sys.setprofile(None)
 if result.testsRun!=1 or result.failures or result.errors:raise ValueError('frozen test did not pass on its own: '+ident)
 if result.skipped:return {'id':ident,'observed':False}
 count=lambda labels:dict(sorted(collections.Counter(labels).items()))
 return {'id':ident,'observed':True,'calls':dict(sorted(calls.items())),'deployments':len(built),'cook_command':count(c for c,_ in built),'analyze_command':count(a for _,a in built)}

def build():
 sys.path[:0]=[str(ORACLE/'tests'),str(ORACLE/'module_utils')]
 classes=json.loads((ROOT/'test/vectors/scenario-classes.golden').read_text());rows=[]
 for record in classes:
  row=observe(record['id'])
  if row['observed'] and sorted(row['calls'])!=record['entry_points']:raise ValueError('entry points differ from scenario-classes.golden: '+record['id'])
  rows.append(row)
 return rows

def main():
 p=argparse.ArgumentParser();p.add_argument('--write',action='store_true');a=p.parse_args();rows=build()
 if a.write:TARGET.write_text(json.dumps(rows,indent=1)+'\n');print(json.dumps({'records':len(rows),'observed':sum(r['observed'] for r in rows)}));return 0
 committed=json.loads(TARGET.read_text())
 if [r['id'] for r in committed]!=[r['id'] for r in rows]:print('STALE test/vectors/scenario-calls.golden: record list');return 1
 both=[(old,new) for old,new in zip(committed,rows) if old['observed'] and new['observed']];differing=[old['id'] for old,new in both if old!=new]
 report={'records':len(rows),'compared':len(both),'skipped_here':sum(not r['observed'] for r in rows),'skipped_when_committed':sum(not r['observed'] for r in committed),'differing':differing}
 print(json.dumps(report,sort_keys=True))
 if differing or not both:print('STALE test/vectors/scenario-calls.golden');return 1
 return 0
if __name__=='__main__':sys.exit(main())
