#!/usr/bin/env python3
"""Package raw private extraction without claiming it is publication-ready.

A portable A subset is committed as JSONL. Full recordings stay in ignored
scratch until path, fake and scenario normalization has been reviewed.
"""
import argparse, hashlib, json, base64
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('recording',type=Path);args=parser.parse_args()
functions={'connectome_deploy.canonical','connectome_catalog.canonical','connectome_spawn.data.canonical','connectome_spawn.data.digest'}
rows=[]
for line in (args.recording/'functions.jsonl').read_text().splitlines():
 row=json.loads(line)
 if row['function'] in functions and '$path' not in line and ('/'+'home'+'/') not in line and ('/'+'srv'+'/') not in line and '/tmp/' not in line:rows.append(row)
# Explicit input/output JSON texts keep float kinds for the JS runner.
portable=[]
for row in rows:
 # These are canonical() calls with exactly one plain dictionary/list argument.
 def untag(v):
  if isinstance(v,list):return [untag(x) for x in v]
  if isinstance(v,dict):
   if '$mapping' in v:return {untag(k):untag(x) for k,x in v['$mapping']}
   if '$float' in v:return float(v['$float'])
   if '$bytes' in v:return base64.b64decode(v['$bytes']).decode('utf-8')
   raise ValueError()
  return v
 try:
  values=untag(row['args']);kwargs=untag(row['kwargs']);result=untag(row['outcome']['result'])
  if len(values)!=1 or kwargs or not isinstance(result,str) or not row['function'].endswith('.canonical'):continue
  source=json.dumps(values[0],ensure_ascii=True)
  # Spawn canonical uses ensure_ascii=False; separate encoder is explicit.
  portable.append({'kind':'A','function':row['function'],'source':source,'expected':result,'encoder':'unicode' if '.spawn.' in row['function'] else 'canonical','test':row['test']})
 except (KeyError,ValueError,TypeError):continue
unique={json.dumps(r,sort_keys=True):r for r in portable}
out=ROOT/'test/vectors/canonical.jsonl';out.write_text(''.join(json.dumps(r,sort_keys=True,ensure_ascii=True)+'\n' for r in unique.values()))
manifest={'recording':json.loads((args.recording/'summary.golden').read_text()),'files':{p.name:{'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in sorted(args.recording.iterdir()) if p.is_file()},'portable_vectors':len(unique),'raw_recordings':'private ignored scratch; not publication-cleared'}
(ROOT/'test/vectors/extraction.golden').write_text(json.dumps(manifest,indent=2)+'\n');print('portable vectors',len(unique))
