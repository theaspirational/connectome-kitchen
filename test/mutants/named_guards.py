#!/usr/bin/env python3
"""Conservative executable subset of semantic names: one unshared guard.

Compound/multi-site obligations are deliberately not relabelled as several
independent kills. They retain anchors but need purpose-built mutations.
"""
import ast, hashlib, json
from collections import Counter
from pathlib import Path
from named_checks import ROOT, build
from oracle import offset

def mutants():
 checks=build();counts=Counter((a['file'],a['line']) for c in checks for a in c['anchors']);result=[]
 for check in checks:
  if len(check['anchors'])!=1:continue
  anchor=check['anchors'][0];file=anchor['file'];line=anchor['line']
  if counts[file,line]!=1:continue
  source=(ROOT/'oracle'/file).read_text();target=None;after=None
  for node in ast.walk(ast.parse(source)):
   if getattr(node,'lineno',None)!=line:continue
   if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('need','require') and node.args:
    target=node.args[0];after='True';break
   if isinstance(node,ast.Raise):target=node;after='pass';break
   if isinstance(node,ast.If) and any(isinstance(n,ast.Raise) for n in node.body):target=node.test;after='False';break
  if target is None:continue
  start=offset(source,target.lineno,target.col_offset);end=offset(source,target.end_lineno,target.end_col_offset)
  before=source[start:end];compile(source[:start]+after+source[end:],file,'exec')
  result.append({'id':check['id'],'rows':[check['row']],'file':file,'line':target.lineno,'start':start,'end':end,'before':before,'after':after,'source_sha256':hashlib.sha256(source.encode()).hexdigest()})
 return result
if __name__=='__main__':
 result=mutants();(ROOT/'test/mutants/named-guard-anchors.golden').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'independent_named_guard_mutants':len(result)}))
