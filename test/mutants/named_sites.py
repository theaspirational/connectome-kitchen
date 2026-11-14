#!/usr/bin/env python3
"""One independent mutant per implementation site of each named check.

`named_guards.py` covers only names with a single unshared guard line. This
builder covers every name that those earlier results did not kill:

- a name whose anchor lines each hold exactly one guard that no other name
  shares gets the standard site mutation (`need(X)` -> `need(True)`,
  `raise` -> `pass`, `if X: raise` -> `if False: raise`), one per anchor line;
- every other name needs a hand-written entry in `test/mutants/purpose/*.golden`
  (`named_sites`), each an exact, unique source substring and its replacement.
  A hand-written entry replaces the automatic ones for that name.

A name counts as killed only when every one of its site mutants is killed, and
no two names may use the same mutation. A name without a usable mutant stays
listed as `needs-manual`; nothing is inferred for it.
"""
import ast, hashlib, json, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from oracle import offset
ROOT=Path(__file__).resolve().parents[2]
M=ROOT/'test/mutants'

def fragments(only=None):
 result={}
 for path in sorted((M/'purpose').glob('*.golden')):
  if only and path.stem!=only:continue
  data=json.loads(path.read_text())
  if set(data)-{'named_sites','kills','waivers','unresolved','crash_only'}:raise ValueError('unknown fragment key: '+path.name)
  result[path.stem]=data
 return result

def automatic(source,line):
 for node in ast.walk(ast.parse(source)):
  if getattr(node,'lineno',None)!=line:continue
  if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('need','require') and node.args:return node.args[0],'True'
  if isinstance(node,ast.Raise):return node,'pass'
  if isinstance(node,ast.If) and any(isinstance(n,ast.Raise) for n in node.body):return node.test,'False'
 return None

def build(only=None):
 checks=json.loads((M/'named-checks.golden').read_text())
 earlier=set()
 for file in ('named-guard-results.golden','named-legacy-results.golden','named-effect-results.golden'):
  for row in json.loads((M/file).read_text()):
   if row['status']=='killed' and (file!='named-legacy-results.golden' or any(t.get('tests')==1 and t.get('failures',0)>0 and t.get('errors')==0 and not t.get('timeout') and not t.get('driver_error') for t in row.get('trials',[]))):earlier.add(row['id'])
 manual={}
 for name,data in fragments(only).items():
  for check,sites in data.get('named_sites',{}).items():
   if check in manual:raise ValueError('duplicate manual named sites: '+check)
   manual[check]=sites
 shared=Counter((a['file'],a['line']) for c in checks for a in c['anchors'])
 known={c['id'] for c in checks}
 if set(manual)-known:raise ValueError('manual sites for unknown name')
 result=[];missing=[]
 for check in checks:
  name=check['id']
  if name in earlier and name not in manual:continue
  sites=[]
  if name in manual:
   for spec in manual[name]:
    if set(spec)-{'file','before','after','occurrence','why'} or not spec.get('why'):raise ValueError('invalid manual site: '+name)
    source=(ROOT/'oracle'/spec['file']).read_text();count=source.count(spec['before'])
    index=spec.get('occurrence',0)
    if count==0 or (count>1 and 'occurrence' not in spec) or index>=count:raise ValueError('ambiguous or missing manual anchor: '+name)
    start=-1
    for _ in range(index+1):start=source.index(spec['before'],start+1)
    sites.append((spec['file'],start,start+len(spec['before']),spec['after'],spec['why']))
  else:
   for anchor in check['anchors']:
    source=(ROOT/'oracle'/anchor['file']).read_text();found=automatic(source,anchor['line'])
    if found is None or shared[anchor['file'],anchor['line']]!=1:sites=None;break
    node,after=found
    sites.append((anchor['file'],offset(source,node.lineno,node.col_offset),offset(source,node.end_lineno,node.end_col_offset),after,'standard guard-site mutation at the anchored line'))
  if not sites:missing.append(name);continue
  for index,(file,start,end,after,why) in enumerate(sites,1):
   source=(ROOT/'oracle'/file).read_text();mutated=source[:start]+after+source[end:]
   compile(mutated,file,'exec')
   if mutated==source:raise ValueError('no-op mutation: '+name)
   result.append({'id':name+'#'+str(index),'name':name,'row':check['row'],'file':file,'line':source[:start].count('\n')+1,'start':start,'end':end,'before':source[start:end],'after':after,'why':why,'source_sha256':hashlib.sha256(source.encode()).hexdigest()})
 owners={}
 for row in result:
  key=(row['file'],row['start'],row['end'],row['after'])
  if owners.setdefault(key,row['name'])!=row['name']:raise ValueError('two names share one mutation: '+row['name']+' / '+owners[key])
 return result,missing

if __name__=='__main__':
 result,missing=build();(M/'named-site-anchors.golden').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'names_with_site_mutants':len({r['name'] for r in result}),'site_mutants':len(result),'needs_manual':missing},indent=1))
