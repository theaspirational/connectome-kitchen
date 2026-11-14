"""Evidence rules of the phase-1 deficit tooling: sites, waivers, purpose kills, C projection."""
import hashlib, importlib.machinery, importlib.util, json, sys, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'test/mutants'),str(ROOT/'test/differential')]
import assemble, named_checks, named_sites, purpose, sweep
spec=importlib.util.spec_from_file_location('replay_c',ROOT/'test/differential/replay_c.py');replay_c=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay_c)
summary=importlib.machinery.SourceFileLoader('phase1b_summary',str(ROOT/'scripts/summarize-phase1')).load_module()

class SiteTests(unittest.TestCase):
 def test_site_mutants_are_exact_unique_and_compile(self):
  sites,missing=named_sites.build();names={c['id'] for c in json.loads((ROOT/'test/mutants/named-checks.golden').read_text())}
  self.assertEqual(len({s['id'] for s in sites}),len(sites));self.assertEqual(json.loads((ROOT/'test/mutants/named-site-anchors.golden').read_text()),sites)
  owners={}
  for s in sites:
   self.assertIn(s['name'],names);self.assertNotIn(s['name'],missing)
   source=(ROOT/'oracle'/s['file']).read_text();self.assertEqual(source[s['start']:s['end']],s['before'])
   self.assertEqual(hashlib.sha256(source.encode()).hexdigest(),s['source_sha256'])
   compile(source[:s['start']]+s['after']+source[s['end']:],s['file'],'exec')
   key=(s['file'],s['start'],s['end'],s['after']);self.assertEqual(owners.setdefault(key,s['name']),s['name'])
 def test_two_names_cannot_share_one_mutation(self):
  one={'file':'module_utils/connectome_cook.py','before':"need(isinstance(settings, dict), 'cook settings must be a mapping')",'after':'pass','why':'fixture'}
  fake={'fixture':{'named_sites':{'cook.argv.dash':[one],'cook.argv.flag-position':[dict(one)]}}}
  with patch.object(named_sites,'fragments',return_value=fake):
   with self.assertRaisesRegex(ValueError,'share one mutation'):named_sites.build()
 def test_manual_anchor_must_be_unique_and_explained(self):
  for bad in ({'file':'module_utils/connectome_cook.py','before':'need(','after':'pass','why':'ambiguous'},{'file':'module_utils/connectome_cook.py','before':'not in this file at all','after':'pass','why':'missing'},{'file':'module_utils/connectome_cook.py','before':'def adapt(','after':'def adapt2('}):
   with patch.object(named_sites,'fragments',return_value={'fixture':{'named_sites':{'cook.argv.dash':[bad]}}}):
    with self.assertRaises(ValueError):named_sites.build()
 def test_name_needs_every_site_killed(self):
  sites=[{'id':'example.name#1','name':'example.name'},{'id':'example.name#2','name':'example.name'}]
  trial={'tests':1,'failures':1,'errors':0}
  def reader(rows):
   def read_text(path,*a,**kw):
    if path.name=='named-site-anchors.golden':return json.dumps(sites)
    if path.name=='named-purpose-results.golden':return json.dumps(rows)
    if path.name=='guard-negative.golden':return ''
    return '[]'
   return read_text
  one=[{'id':'example.name#1','status':'killed','trials':[dict(trial,test='t')]}]
  with patch.object(Path,'read_text',reader(one)),patch.object(Path,'exists',lambda p:True):self.assertEqual(named_checks.site_proof()[0],{})
  both=one+[{'id':'example.name#2','status':'killed','trials':[dict(trial,test='t')]}]
  with patch.object(Path,'read_text',reader(both)),patch.object(Path,'exists',lambda p:True):self.assertEqual(set(named_checks.site_proof()[0]),{'example.name'})
  errored=one+[{'id':'example.name#2','status':'killed','trials':[dict(trial,errors=1,test='t')]}]
  with patch.object(Path,'read_text',reader(errored)),patch.object(Path,'exists',lambda p:True):self.assertEqual(named_checks.site_proof()[0],{})

class WaiverTests(unittest.TestCase):
 GOOD={'id':'archive.L140.4','class':'equivalent','justification':'x'*80,'no_observable_difference':'y'*40}
 def build(self,*waivers):
  with patch.object(assemble,'fragments',return_value={'fixture':{'waivers':list(waivers)}}):return assemble.build()
 def test_waiver_shape_class_and_real_text_required(self):
  self.assertEqual(self.build(self.GOOD)[0]['anchor']['line'],140)
  for change in ({'class':'hard-to-test'},{'justification':'too short'},{'no_observable_difference':'short'},{'id':'not.a.mutant'},{'id':'secrets.reserved.catalog#1'}):
   with self.assertRaises(ValueError):self.build(dict(self.GOOD,**change))
  with self.assertRaises(ValueError):self.build({k:v for k,v in self.GOOD.items() if k!='class'})
 def test_no_bulk_or_duplicate_waivers(self):
  with self.assertRaisesRegex(ValueError,'bulk'):self.build(self.GOOD,dict(self.GOOD,id='archive.L193.4'))
  with self.assertRaises(ValueError):self.build(self.GOOD,dict(self.GOOD,justification='z'*80))
 def test_committed_waivers_are_current_and_never_on_a_killed_mutant(self):
  committed=json.loads((ROOT/'test/mutants/equivalence-waivers.golden').read_text());self.assertEqual(committed,assemble.build())
  review={r['id']:r for r in json.loads((ROOT/'test/mutants/guard-review.golden').read_text())}
  for waiver in committed:
   self.assertEqual(review[waiver['id']]['status'],'equivalent');self.assertEqual(review[waiver['id']]['evidence'],[])

class KillRuleTests(unittest.TestCase):
 def test_only_a_clean_failure_of_one_test_kills(self):
  for module in (purpose,sweep):
   kill=module.kill if module is purpose else module.solo_kill
   self.assertTrue(kill({'tests':1,'failures':1,'errors':0,'skipped':0}))
   for bad in ({'tests':1,'failures':0,'errors':1},{'tests':1,'failures':1,'errors':1},{'tests':2,'failures':1,'errors':0},{'tests':1,'failures':0,'errors':0},{'timeout':True},{'driver_error':True}):
    self.assertFalse(kill(bad))
  self.assertTrue(purpose.passes({'tests':1,'failures':0,'errors':0,'skipped':0}))
  for bad in ({'tests':1,'failures':0,'errors':0,'skipped':1},{'tests':0,'failures':0,'errors':0,'skipped':0},{'tests':1,'failures':1,'errors':0,'skipped':0}):self.assertFalse(purpose.passes(bad))
 def test_every_purpose_kill_names_an_existing_test(self):
  import ast
  known=purpose.anchors()
  for ident,test in purpose.kills().items():
   self.assertIn(ident,known);module,cls,method=test.split('.')
   tree=ast.parse((ROOT/'test/unit'/(module+'.py')).read_text())
   methods={n.name for c in tree.body if isinstance(c,ast.ClassDef) and c.name==cls for n in c.body if isinstance(n,ast.FunctionDef)}
   self.assertIn(method,methods,test)

class ProjectionTests(unittest.TestCase):
 RECEIPT={'contract':'connectome.kitchen/v1','command':'apply','meta':{},'core':{'error_code':'E_COOK_FAILED','msg':'m','cook_error_code':'E_HOST_UNIT','phase':'prepare'}}
 def test_fixed_projection_restores_adapter_bytes(self):
  self.assertEqual(replay_c.project(self.RECEIPT,1),{'stdout':'','stderr':'{"error_code": "E_COOK_FAILED", "msg": "m", "cook_error_code": "E_HOST_UNIT"}\n','status':1})
 def test_projection_refuses_anything_but_an_adapter_stage_refusal(self):
  core=self.RECEIPT['core']
  for receipt,status in ((self.RECEIPT,0),(self.RECEIPT,2),(dict(self.RECEIPT,command='plan'),1),(dict(self.RECEIPT,core=dict(core,phase='activate')),1),(dict(self.RECEIPT,core=dict(core,prepare={})),1),(dict(self.RECEIPT,core=dict(core,changed=False)),1),({k:v for k,v in self.RECEIPT.items() if k!='meta'},1)):
   with self.assertRaises(ValueError):replay_c.project(receipt,status)
 def test_all_sixty_recorded_entries_are_closed_diagnostics_bound_by_hash(self):
  entries,errors=replay_c.expected_records();self.assertEqual(len(entries),60)
  self.assertEqual(sum(1 for e in entries if 'cook_error_code' in e['expected']),38)
  self.assertEqual({e['expected']['error_code'] for e in entries},set(errors.MESSAGES))
 def test_replayed_entries_carry_projection_and_branch_evidence(self):
  replay={r['id']:r for r in json.loads((ROOT/'test/vectors/cli-replay.golden').read_text())}
  for entry in json.loads((ROOT/'test/vectors/cli-obligations.golden').read_text()):
   row=replay[entry['id']]
   if entry['status']=='replayed':
    self.assertEqual(row['projected_record_sha256'],entry['raw_sha256']);self.assertEqual(row['adapter_failure'],row['expected_adapter_failure'])
   else:
    self.assertEqual(row['status'],'unreachable-without-patching');self.assertIn(row['reason'].split(':')[0],replay_c.DEPLOY_ONLY+replay_c.SAME_VALIDATION)
 def test_summary_refuses_a_relabelled_replay(self):
  real=Path.read_text
  def relabel(name):
   def read_text(path,*a,**kw):
    text=real(path,*a,**kw)
    if path.name!=name:return text
    rows=json.loads(text);target=next(r for r in rows if r['status']!='replayed');target['status']='replayed';return json.dumps(rows)
   return read_text
  for name in ('cli-obligations.golden','scenario-obligations.golden'):
   with patch.object(Path,'read_text',relabel(name)):
    with self.assertRaisesRegex(ValueError,'marked replayed'):summary.summarize()
if __name__=='__main__':unittest.main()
