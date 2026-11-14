#!/usr/bin/env python3
"""Python-only witnesses, and the portable kind-A vectors that can replace them.

Some purpose-built killing tests give their function an input that no JSON
document decodes to: a `UserDict`, an `OrderedDict`, a look-alike object, a
`str` subclass, a one-shot iterator, a mapping with an integer key, a patched
reader. Each is a clean unit-test kill of the Python oracle, but a second
engine cannot be given the same input. They are declared in
`test/mutants/purpose/*.golden` under `python_only` (test id -> what the input
is) and counted apart from ordinary clean kills: a guard mutant or named check
whose only evidence is such a test is reported as killed by a Python-only
witness. Whether that evidence is enough is not decided here.

A Python-only witness can be replaced when the tagged argument encoding of
`test/differential/codec.py` expresses its input. Such a vector is declared
under `portable_vectors`:
  {"id": "<guard mutant id>", "function": "connectome_spawn.data.json_data", "args": [...], "kwargs": {...}}

`build` records the frozen oracle's outcome, which must be a declared refusal,
in `test/vectors/guard-portable.golden`. The row format is that of
`guard-negative.golden`; that file is not touched, because its header pins how
it was built. `run` replays the vectors against each declared mutant with the
runner and kill rule of the negative vectors (`test/mutants/guard_vectors.py`):
a valid mismatch kills, a mutant crash does not.
"""
import argparse, hashlib, importlib.util, json, subprocess, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from named_sites import fragments
ROOT=Path(__file__).resolve().parents[2]
M=ROOT/'test/mutants'
VECTORS=ROOT/'test/vectors/guard-portable.golden'
RESULTS=M/'portable-results.golden'

def python_only():
 """Test id -> what makes its input Python-only, for every declared witness."""
 result={}
 for area,data in fragments().items():
  mapped=set(data.get('kills',{}).values())
  for test,reason in data.get('python_only',{}).items():
   if test not in mapped:raise ValueError('python-only declaration for a test that kills nothing: '+test)
   if test in result or not isinstance(reason,str) or len(reason)<20:raise ValueError('invalid python-only declaration: '+test)
   result[test]=reason
 return result

def declared():
 guards={row['id'] for row in json.loads((M/'guard-anchors.golden').read_text())};result=[];seen=set()
 for area,data in fragments().items():
  for vector in data.get('portable_vectors',[]):
   if set(vector)-{'id','function','args','kwargs'} or not {'id','function','args'}<=set(vector):raise ValueError('invalid portable vector: '+str(vector.get('id')))
   if vector['id'] not in guards or vector['id'] in seen:raise ValueError('portable vector needs one known guard mutant: '+vector['id'])
   seen.add(vector['id']);result.append(vector)
 return sorted(result,key=lambda vector:vector['id'])

def build():
 """One line per declared vector: {mutant id: line}. The outcome is the frozen oracle's, never written by hand."""
 spec=importlib.util.spec_from_file_location('differential_guard_vectors',ROOT/'test/differential/guard_vectors.py');vectors=importlib.util.module_from_spec(spec);spec.loader.exec_module(vectors)
 vectors.initialize(ROOT/'oracle/module_utils');lines={}
 for vector in declared():
  if vector['function'] not in vectors.FUNCTIONS or vector['function'] in vectors.PROFILES:raise ValueError('portable vectors call audited data functions without a fixture profile: '+vector['id'])
  args=vectors.decode(vector['args']);kwargs=vectors.decode(vector.get('kwargs',{}));outcome=vectors.invoke(vector['function'],args,kwargs)
  if not outcome or 'exception' not in outcome:raise ValueError('not a declared refusal on the frozen oracle: '+vector['id'])
  lines[vector['id']]=vectors.canonical({'function':vector['function'],'args':vectors.encode(args),'kwargs':vectors.encode(kwargs),'outcome':outcome})
 if len(set(lines.values()))!=len(lines):raise ValueError('two mutants share one portable vector')
 return lines

def run(out):
 lines=build()
 if VECTORS.read_text()!=''.join(line+'\n' for line in lines.values()):raise ValueError('stale test/vectors/guard-portable.golden')
 out.mkdir(parents=True,exist_ok=True);anchors=[row for row in json.loads((M/'guard-anchors.golden').read_text()) if row['id'] in lines]
 (out/'anchors.json').write_text(json.dumps(anchors))
 subprocess.run([sys.executable,'-B',str(M/'guard_vectors.py'),'--anchors',str(out/'anchors.json'),'--vectors',str(VECTORS),'--out',str(out/'run')],check=True,stdout=subprocess.DEVNULL,timeout=600)
 results=json.loads((out/'run/results.golden').read_text())
 for row in results:
  own=hashlib.sha256(lines[row['id']].encode()).hexdigest()
  # The mutant must be killed by the vector declared for it, not by a neighbour's.
  row['vector_sha256']=own;row['status']=row['status'] if row['status']!='killed' or own in row['witnesses'] else 'killed-by-another-vector'
 return results

def main():
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
 for name in ('build','run'):
  s=sub.add_parser(name);s.add_argument('--check',action='store_true')
  if name=='run':s.add_argument('--out',type=Path,default=ROOT/'.cache/portable')
 a=p.parse_args()
 if a.command=='build':
  data=''.join(line+'\n' for line in build().values())
  if a.check:
   if not VECTORS.exists() or VECTORS.read_text()!=data:print('STALE test/vectors/guard-portable.golden');return 1
  else:VECTORS.write_text(data)
  print(json.dumps({'portable_vectors':len(data.splitlines()),'python_only_witnesses':len(python_only())}));return 0
 results=run(a.out.resolve());data=json.dumps(results,indent=2)+'\n'
 if a.check:
  if not RESULTS.exists() or RESULTS.read_text()!=data:print('STALE test/mutants/portable-results.golden');return 1
 else:RESULTS.write_text(data)
 print(json.dumps({'vectors':len(results),'statuses':{s:sum(r['status']==s for r in results) for s in sorted({r['status'] for r in results})}}));return int(any(r['status']!='killed' for r in results))
if __name__=='__main__':sys.exit(main())
