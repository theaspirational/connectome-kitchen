#!/usr/bin/env python3
"""Write `equivalence-waivers.golden` from the per-area fragments.

A waiver is one guard-site mutant, one written justification. The only classes
are `equivalent` (the mutated module behaves identically on every input) and
`unreachable` (the mutated condition cannot be false, or the statement cannot
run, because of checks that dominate it). Named checks cannot be waived.
"""
import json, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from named_sites import fragments
ROOT=Path(__file__).resolve().parents[2]
M=ROOT/'test/mutants'
CLASSES=('equivalent','unreachable')

def build():
 anchors={a['id']:a for a in json.loads((M/'guard-anchors.golden').read_text())};result=[];seen=set()
 for area,data in fragments().items():
  for w in data.get('waivers',[]):
   if set(w)!={'id','class','justification','no_observable_difference'}:raise ValueError('waiver keys: '+str(w.get('id')))
   if w['id'] not in anchors or w['id'] in seen or w['class'] not in CLASSES:raise ValueError('invalid waiver: '+str(w['id']))
   if len(w['justification'])<60 or len(w['no_observable_difference'])<30:raise ValueError('waiver without a real justification: '+w['id'])
   seen.add(w['id']);a=anchors[w['id']]
   result.append({**w,'area':area,'anchor':{'file':a['file'],'line':a['line'],'before':a['before'],'after':a['after']}})
 texts=[w['justification'] for w in result]
 if len(set(texts))!=len(texts):raise ValueError('bulk waiver: identical justification used twice')
 return sorted(result,key=lambda w:w['id'])
def unresolved():
 """Written analysis of items that are neither killed nor waived. They stay blocking."""
 anchors={a['id'] for a in json.loads((M/'guard-anchors.golden').read_text())}
 names={c['id'] for c in json.loads((M/'named-checks.golden').read_text())};result=[];seen=set()
 for area,data in fragments().items():
  for row in data.get('unresolved',[]):
   if set(row)!={'id','analysis'} or row['id'] in seen or row['id'] not in anchors|names or len(row['analysis'])<60:raise ValueError('invalid unresolved note: '+str(row.get('id')))
   seen.add(row['id']);result.append({**row,'area':area,'kind':'guard-site' if row['id'] in anchors else 'named-check'})
 return sorted(result,key=lambda r:r['id'])
if __name__=='__main__':
 result=build();(M/'equivalence-waivers.golden').write_text(json.dumps(result,indent=2)+'\n')
 notes=unresolved();(M/'unresolved.golden').write_text(json.dumps(notes,indent=2)+'\n');print(json.dumps({'waivers':len(result),'unresolved_notes':len(notes)}))
