#!/usr/bin/env python3
"""The 203 semantic obligations, distinct from generated refusal-site mutants.

Each name has its own source anchor(s). Anchoring is not a claim that an
independent semantic mutant has been implemented or killed. Pending entries
remain blocking; in particular a shared compound guard is not several kills.
"""
import hashlib, importlib.machinery, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
inv=importlib.machinery.SourceFileLoader('sites',str(ROOT/'scripts/inventory-sites')).load_module()
ALIASES={'c':'catalog.py','d':'deploy.py','k':'cook.py','i':'inputs.py','l':'lock.py','sl':'spawn/lifecycle.py','si':'spawn/identity.py','sp':'spawn/persistence.py','ss':'spawn/schema.py','sc':'spawn/catalog.py','sr':'spawn/sources.py','sd':'spawn/data.py','sn':'spawn/seed.py','spl':'spawn/planner.py','cli':'spawn/cli.py','cfg':'config.py'}
# In inventory order, not lexical order. Multiple anchors mean the named check
# has more than one implementation site; none may be silently dropped.
ANCHORS={
48:'c:845 c:846 c:849 c:842 c:825+843 c:810 c:851 c:844 c:854 c:802',
49:'i:97 c:329 c:328 d:408 d:410 d:418 k:136+188',
50:'k:157 k:174 k:200 c:296+298 spl:87 spl:94',
51:'k:37 k:37 k:317 k:48 k:83 k:83 k:84 k:84',
52:'d:541 d:541 d:542 d:548 d:947 d:953 d:496',
53:'c:620+624 c:631 c:621 c:637 c:639 c:642 c:643 c:646 d:796',
54:'c:570+573+576 k:300+301 c:628 c:753+755 k:239',
55:'c:679 c:677 c:676',
56:'sl:45+47 sl:96 sl:151 sl:161+162 sl:175+180 sl:190+194+198',
57:'si:158 si:190 si:118+155 si:145 si:177 si:132+sl:260 si:49 sl:202',
58:'sp:17 sp:19 sp:49 sp:56 sp:58 sp:57 sp:68',
59:'l:137+140 l:142 l:154+155 l:160 l:170 l:177 l:185+188+193+199 l:201+204+205 l:208 l:211 l:214+216 l:221+226 l:290 l:294 l:299',
60:'c:55 c:73 c:74 c:77 c:79 c:82 c:84+86 c:88 c:90 c:124 c:146 c:109',
61:'c:345 c:347+348 c:356 c:359 c:253 spl:101+104',
62:'d:574+583 c:615+616 c:647+648 c:460 c:698+700+701+702+703 c:706+707 c:720+721+722+726+728 c:729+731 k:327+329+332',
63:'i:63 i:67+70+72+77 i:81+87 i:83+86 i:91+94+95+97 i:37 i:21 i:30 i:144+148',
64:'d:342 d:358 d:972 sl:33',
65:'d:758 d:769 d:773 d:775 d:1043 d:787',
66:'d:827+830 d:844 d:846 d:850+851 d:853 d:1145 d:981',
67:'d:820 d:691+707+729 d:740 d:1207 d:731',
68:'d:1213 d:1160+1182 d:1185 d:674 d:898 d:921 d:927',
69:'cfg:32 cfg:39 cfg:48 cfg:50 cfg:59 k:148 c:77',
70:'k:117 k:124 k:123 k:126 k:155 k:162 k:172 k:66+223+225+226 k:249 k:263+264+268 k:270',
71:'ss:65+66 ss:62 ss:124+125 ss:146+151 ss:190+191 ss:210+213 sn:26+32 si:23',
72:'sc:47 sc:82+84 sc:103 sc:114+116+119+122+124+126+128 sc:154 sc:137 spl:59+62+64+67+68 spl:80+83 spl:134 ss:180 sr:196',
73:'sc:183+210 sc:170+171+174+175 sc:191 sr:216 sr:220 sr:208+211+223',
74:'cli:15 cli:38+43+48 cli:64 sl:100',
}

def parse(token):
 # c:627+k:300 (cross-file) and c:825+843 (same-file) are both explicit.
 refs=[];file=None
 for part in token.split('+'):
  if ':' in part:alias,line=part.split(':');file=ALIASES[alias]
  else:line=part
  refs.append((file,int(line)))
 return refs

def unit_kill(row):
 return row.get('status')=='killed' and any(t.get('tests')==1 and t.get('failures',0)>0 and t.get('errors')==0 and not t.get('timeout') and not t.get('driver_error') for t in row.get('trials',[]))

def site_proof():
 """Names whose every site mutant (named-site-anchors.golden) has its own kill."""
 M=ROOT/'test/mutants';path=M/'named-site-anchors.golden'
 if not path.exists():return {},set()
 names={}
 for site in json.loads(path.read_text()):names.setdefault(site['name'],[]).append(site['id'])
 ids={i for group in names.values() for i in group}
 witnesses={hashlib.sha256(line.encode()).hexdigest() for line in (ROOT/'test/vectors/guard-negative.golden').read_text().splitlines()}
 evidence={}
 for file,kind in (('named-site-negative-results.golden','negative'),('named-site-sweep-results.golden','unit'),('named-purpose-results.golden','unit')):
  if not (M/file).exists():continue
  for row in json.loads((M/file).read_text()):
   if row['id'] not in ids:raise ValueError('result for unknown named site: '+row['id'])
   if kind=='unit':
    if unit_kill(row):evidence.setdefault(row['id'],[]).append({'file':file,'site':row['id'],'vector':row['trials'][-1]['test'],'witnesses':[]})
   elif row.get('status')=='killed' and row.get('witnesses'):
    if not set(row['witnesses'])<=witnesses:raise ValueError('missing committed killing witness')
    evidence.setdefault(row['id'],[]).append({'file':file,'site':row['id'],'vector':None,'witnesses':row['witnesses']})
 return {name:[evidence[i][0] for i in group] for name,group in names.items() if all(i in evidence for i in group)},set(names)

def build():
 files=inv.files(ROOT/'oracle');result=[];proof={};implemented=set()
 for file in ('named-guard-anchors.golden','named-effect-anchors.golden'):
  path=ROOT/'test/mutants'/file
  if path.exists():implemented.update(r['id'] for r in json.loads(path.read_text()))
 for file in ('named-guard-results.golden','named-legacy-results.golden','named-effect-results.golden'):
  path=ROOT/'test/mutants'/file
  if not path.exists():continue
  for r in json.loads(path.read_text()):
   valid=r['status']=='killed'
   if file=='named-legacy-results.golden':valid=valid and any(t.get('tests')==1 and t.get('failures',0)>0 and t.get('errors')==0 and not t.get('timeout') and not t.get('driver_error') for t in r.get('trials',[]))
   if valid:proof.setdefault(r['id'],[]).append({'file':file,'vector':r.get('vector'),'witnesses':r.get('witnesses',[])})
 sites,site_names=site_proof()
 implemented|=site_names
 for name,evidence in sites.items():proof.setdefault(name,[]).extend(evidence)
 for row in inv.inventory(ROOT/'docs/approved-plan.md')[47:]:
  anchors=ANCHORS[row['row']].split()
  if len(row['ids'])!=len(anchors):raise ValueError('name/anchor count mismatch')
  for name,token in zip(row['ids'],anchors):
   references=[]
   for file,line in parse(token):
    source=files[file].read_text();text=source.splitlines()[line-1]
    references.append({'file':str(files[file].relative_to(ROOT/'oracle')),'line':line,'text':text,'source_sha256':hashlib.sha256(source.encode()).hexdigest()})
   status='killed' if name in proof else 'not-killed' if name in implemented else 'pending-semantic-mutant'
   result.append({'id':name,'row':row['row'],'anchors':references,'status':status,'evidence':proof.get(name,[]),'reason':'independent mutation killed' if name in proof else 'source anchored; independent killing vector still required'})
 if len(result)!=203 or len({x['id'] for x in result})!=203:raise ValueError('expected exactly 203 unique names')
 return result
if __name__=='__main__':
 (ROOT/'test/mutants/named-checks.golden').write_text(json.dumps(build(),indent=2)+'\n')
 rows=build();print(json.dumps({'named_checks':203,'anchored':203,'killed':sum(r['status']=='killed' for r in rows),'not_killed':sum(r['status']!='killed' for r in rows)}))
