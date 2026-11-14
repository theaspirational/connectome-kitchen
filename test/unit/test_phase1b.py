"""Evidence rules of the phase-1 deficit tooling: sites, waivers, purpose kills, C projection."""
import hashlib, importlib.machinery, importlib.util, json, re, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'test/mutants'),str(ROOT/'test/differential')]
import assemble, crash_only, named_checks, named_sites, portable, purpose, sweep
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
    self.assertRegex(row['build_identity_sha256'],'^[0-9a-f]{64}$');self.assertRegex(row['fake_tool_log_sha256'],'^[0-9a-f]{64}$')
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

class HostNamingTests(unittest.TestCase):
 """The C golden names four host values. Naming them must not hide any other difference."""
 PYTHON='/usr/bin/python3'
 def call(self,root,build,archive='6'*64,out=None,extra=()):
  base=root+'/.cache/scenario-root'
  argv=[base+'/cook/bin/cook','install',base+'/agent/builds/'+build+'/cook-recipe.json','--out',out or base+'/agent/builds/'+build,'--strict','--host-dependency','@example/package='+base+'/inputs/framework.tgz#sha256='+archive,*extra]
  return json.dumps({'tool':'runtime','argv':argv,'stdin_tty':False})+'\n'
 def names(self,root,build=None):return [(self.PYTHON,'${PYTHON}',True),(root+'/','${REPO}/',False)]+([(build,'${BUILD_ID}',False)] if build else [])
 def test_same_calls_from_two_roots_have_one_digest(self):
  one=replay_c.tool_log(self.call('/fixture/one','a'*64),self.names('/fixture/one','a'*64))
  for root,build in (('/tmp/deeper/second','b'*64),('/e','c'*64),('/fixture/d\u00e9p\u00f4t "quoted"','d'*64)):
   self.assertEqual(replay_c.tool_log(self.call(root,build),self.names(root,build)),one,root)
  self.assertEqual(one[0],1);self.assertEqual(replay_c.tool_log('',self.names('/fixture/one')),(0,hashlib.sha256(b'').hexdigest()))
 def test_every_other_difference_changes_the_digest(self):
  root,build='/fixture/one','a'*64;names=self.names(root,build);base=replay_c.tool_log(self.call(root,build),names)
  different={
   'archive digest':self.call(root,build,archive='7'*64),
   'another build directory':self.call(root,build,out=root+'/.cache/scenario-root/agent/builds/'+'f'*64),
   'output outside the checkout':self.call(root,build,out='/fixture/elsewhere/'+build),
   'extra argument':self.call(root,build,extra=('--force',)),
   'second call':self.call(root,build)*2,
   'path of another checkout':self.call('/fixture/other',build),
  }
  for label,text in different.items():self.assertNotEqual(replay_c.tool_log(text,names),base,label)
  # A build id that was not verified gets no name, so it stays in the digest.
  self.assertNotEqual(replay_c.tool_log(self.call(root,build),self.names(root)),base)
  self.assertNotEqual(replay_c.tool_log(self.call(root,build),self.names(root)),replay_c.tool_log(self.call(root,'b'*64),self.names(root)))
 def test_names_are_exact(self):
  names=self.names('/e')
  self.assertEqual(replay_c.named([self.PYTHON,self.PYTHON+'.12','run '+self.PYTHON,'https://example.invalid/extra.git','/e/x','/e',{'/e/key':7,'n':None}],names),
   ['${PYTHON}',self.PYTHON+'.12','run '+self.PYTHON,'https://example.invalid/extra.git','${REPO}/x','/e',{'${REPO}/key':7,'n':None}])
  # An interpreter inside the checkout is still the interpreter.
  self.assertEqual(replay_c.named(['/e/venv/bin/python','/e/venv/bin/python/x'],[('/e/venv/bin/python','${PYTHON}',True),('/e/','${REPO}/',False)]),['${PYTHON}','${REPO}/venv/bin/python/x'])
 def test_request_digest_names_interpreter_checkout_and_platform_digest(self):
  data=replay_c.request({'path':str(replay_c.SCENARIO/'inputs/framework.tgz'),'sha256':'0'*64},'catalog-checkout-outside-agent','e'*40)
  shell=hashlib.sha256(Path('/bin/sh').read_bytes()).hexdigest();text=replay_c.canonical(replay_c.named(data,replay_c.host_names(data)))
  for host in (str(ROOT),shell,json.dumps(sys.executable)):self.assertNotIn(host,text)
  self.assertEqual((text.count('"${PYTHON}"'),text.count('${PLATFORM_DIGEST}')),(1,1));self.assertIn('"root":"${REPO}/.cache/scenario-root/agent"',text)
  self.assertEqual(replay_c.request_digest(data),hashlib.sha256(text.encode()).hexdigest())
  changed=json.loads(json.dumps(data));changed['request']['config']['name']='other';self.assertNotEqual(replay_c.request_digest(changed),replay_c.request_digest(data))
 def test_build_identity_is_the_preimage_of_the_directory_name(self):
  cook={'entrypoint':'/fixture/cook/bin/cook','runtime':'/fixture/bin/runtime','bun':'/fixture/bin/bun','node':'/fixture/bin/node'}
  data={'request':{'engines':{'cook':cook}}};spec={'config':{'name':'example'},'pins':{'runtime':{'cook':'e'*40}}}
  with tempfile.TemporaryDirectory() as scratch:
   oracle=Path(scratch)/'oracle'
   # Written out here on purpose: the engine's request id over the shim's literal adapter command line.
   argv=[sys.executable,str(oracle/'scripts/cook-adapter.py'),'--spec','{recipe}','--out','{build_dir}','--state','{state_dir}','--runtime','/fixture/bin/runtime','--entrypoint','/fixture/cook/bin/cook','--bun-path','/fixture/bin/bun','--node-path','/fixture/bin/node']
   name=hashlib.sha256(json.dumps({'spec':spec,'cook':argv},sort_keys=True,separators=(',',':')).encode()).hexdigest()
   build=Path(scratch)/'builds'/name;build.mkdir(parents=True);(build/'deployment.json').write_text(json.dumps(spec,sort_keys=True,separators=(',',':')))
   self.assertEqual(replay_c.build_identity(oracle,data,build),{'spec':spec,'cook':argv})
   for other in (dict(cook,bun=cook['node']),dict(cook,entrypoint='/fixture/other/bin/cook')):
    with self.assertRaisesRegex(ValueError,'not the hash'):replay_c.build_identity(oracle,{'request':{'engines':{'cook':other}}},build)
   with self.assertRaisesRegex(ValueError,'not the hash'):replay_c.build_identity(Path(scratch)/'moved-oracle',data,build)
   (build/'deployment.json').write_text(json.dumps(dict(spec,config={'name':'other'})))
   with self.assertRaisesRegex(ValueError,'not the hash'):replay_c.build_identity(oracle,data,build)
   (build/'deployment.json').write_text('not json')
   with self.assertRaises(ValueError):replay_c.build_identity(oracle,data,build)
 def test_a_replay_counts_only_with_a_bound_build(self):
  entries,errors=replay_c.expected_records();entry=next(e for e in entries if e['id']=='C.0')
  row={'projected':{'stdout':'','stderr':json.dumps(entry['expected'])+'\n','status':1},'adapter_failure':'LockError','expected_adapter_failure':'LockError'}
  unbound=replay_c.evaluate({'fallback:LockError':dict(row,build_identity_error='fixture reason')},[entry],errors)[0]
  self.assertEqual((unbound['status'],unbound['reason']),('not-reproduced','tool log is not bound to one build: fixture reason'))
  self.assertEqual(replay_c.evaluate({'fallback:LockError':dict(row,build_identity_sha256='a'*64)},[entry],errors)[0]['status'],'replayed')
 def test_a_root_the_engine_refuses_is_a_missing_prerequisite(self):
  for path in ('/fixture/plain/.cache/scenario-root','/tmp/a-b_c.d:e@f+g/x'):self.assertIsNone(replay_c.root_refusal(path))
  for path in ('/fixture/with space/.cache/scenario-root','/fixture/d\u00e9p\u00f4t/x','/fixture/100%/x','/fixture/a"b/x'):
   self.assertIn('E_SYSTEMD_PATH',replay_c.root_refusal(path))
  # The check comes before the scenarios and before the cook clone is looked at.
  source=(ROOT/'test/differential/replay_c.py').read_text();self.assertLess(source.index("if refused:print(json.dumps({'prerequisite':refused}));return 3"),source.index("'cat-file','-e'"))
 def test_golden_holds_no_host_value(self):
  text=(ROOT/'test/vectors/cli-replay.golden').read_text()
  for host in (str(ROOT),sys.executable,'host','build_id'):self.assertNotIn(json.dumps(host),text)
  for row in json.loads(text):self.assertLessEqual(set(row),{'id','test','raw_sha256','scenario','request_sha256','fake_runtime_mode','fake_runtime_calls','fake_tool_log_sha256','build_identity_sha256','adapter_failure','expected_adapter_failure','projected_record_sha256','status','reason'})

class CrashOnlyTests(unittest.TestCase):
 VECTOR={'id':'inputs.L150.8','function':'connectome_inputs.payloads','args':[{'recipe_file':{'path':'/fixture/recipe.json','sha256':'a'*64}},{'cook-recipe.json':'not strict base64!'}]}
 def test_original_refuses_as_declared_and_outcome_names_class_code_message(self):
  outcome=crash_only.invoke(ROOT/'oracle',self.VECTOR)
  self.assertEqual(outcome,{'exception':'ValueError','code':None,'message':'invalid recipe transport','declared_refusal':True})
  accepted=crash_only.invoke(ROOT/'oracle',dict(self.VECTOR,function='connectome_inputs.external_data_path',args=['/fixture/data']))
  self.assertEqual(accepted,{'returned':'bool'})
 def test_vector_declarations_are_closed(self):
  def declared(*rows):
   with patch.object(crash_only,'fragments',return_value={'fixture':{'crash_only':list(rows)}}):return crash_only.declared()
  self.assertEqual(declared(self.VECTOR)[0]['area'],'fixture')
  for bad in (dict(self.VECTOR,function='subprocess.run'),dict(self.VECTOR,test='x'),{k:v for k,v in self.VECTOR.items() if k!='args'}):
   with self.assertRaises(ValueError):declared(bad)
  with self.assertRaises(ValueError):declared(self.VECTOR,dict(self.VECTOR))
 def test_crash_only_is_a_separate_category_never_a_clean_kill(self):
  counts=json.loads((ROOT/'test/mutants/phase1-summary.golden').read_text());review=json.loads((ROOT/'test/mutants/guard-review.golden').read_text())
  crash=[r for r in review if r['status']==crash_only.CATEGORY]
  self.assertEqual(len(crash),counts['crash_only_kind_A'])
  self.assertEqual(counts['combined_killed'],sum(r['status']=='killed' for r in review))
  self.assertEqual(counts['combined_killed']+counts['equivalent']+counts['crash_only_kind_A']+counts['remaining_unreviewed'],counts['guard_site_mutants'])
  results={r['id']:r for r in json.loads((ROOT/'test/mutants/crash-only-results.golden').read_text())}
  for row in crash:
   self.assertEqual(row['evidence'],[]);self.assertTrue(results[row['id']]['mutated'].get('exception'))
  kills=purpose.kills()
  for vector in crash_only.declared():self.assertNotIn(vector['id'],kills)
  names=json.loads((ROOT/'test/mutants/named-checks.golden').read_text())
  self.assertEqual(counts['named_killed']+counts['named_crash_only_kind_A']+counts['named_remaining'],203)
  self.assertEqual(counts['named_killed'],sum(n['status']=='killed' for n in names))
 def test_readiness_gate_keeps_a_separate_blocking_line(self):
  text=(ROOT/'scripts/check-module-ready').read_text()
  self.assertIn("if counts['crash_only_kind_A']:missing.append(",text);self.assertIn("if counts['named_crash_only_kind_A']:missing.append(",text)

class ScenarioAccountingTests(unittest.TestCase):
 def test_every_recorded_scenario_is_classified_and_none_is_counted(self):
  B=json.loads((ROOT/'test/vectors/scenario-obligations.golden').read_text());classes=json.loads((ROOT/'test/vectors/scenario-classes.golden').read_text())
  self.assertEqual([b['id'] for b in B],[c['id'] for c in classes]);self.assertEqual(len(B),279)
  self.assertEqual({b['status'] for b in B},{'pending-independent-contract-request'})
  counts={}
  for c in classes:counts[c['class']]=counts.get(c['class'],0)+1
  self.assertEqual(counts,{'no-entry-point':122,'deployment-entry-point':84,'spawn-lifecycle':28,'publication-gate':17,'adapter-only':15,'spawn-plan':13})
 def test_unreachability_note_is_generated_and_adds_up(self):
  note=importlib.machinery.SourceFileLoader('unreachable_note',str(ROOT/'scripts/unreachable-note')).load_module()
  text=(ROOT/'docs/phase1-unreachable.md').read_text();self.assertEqual(text,note.build())
  for entry in json.loads((ROOT/'test/vectors/scenario-obligations.golden').read_text())+json.loads((ROOT/'test/vectors/cli-obligations.golden').read_text()):
   self.assertEqual(text.count('- `'+entry['id']+'`'),1,entry['id'])
  self.assertIn('**B replayed: 0/279.**',text);self.assertIn('| **Total** | **279** |',text);self.assertIn('| **Total** | **60** |',text)
 def test_unresolved_notes_need_a_real_analysis_and_a_known_id(self):
  def notes(*rows):
   with patch.object(assemble,'fragments',return_value={'fixture':{'unresolved':list(rows)}}):return assemble.unresolved()
  good={'id':'archive.L140.4','analysis':'z'*80}
  self.assertEqual(notes(good)[0]['kind'],'guard-site');self.assertEqual(notes({'id':'cook.argv.dash','analysis':'z'*80})[0]['kind'],'named-check')
  for bad in ({'id':'unknown','analysis':'z'*80},{'id':'archive.L140.4','analysis':'short'},{'id':'archive.L140.4'}):
   with self.assertRaises(ValueError):notes(bad)
  with self.assertRaises(ValueError):notes(good,dict(good))
 def test_unresolved_items_stay_unreviewed(self):
  review={r['id']:r for r in json.loads((ROOT/'test/mutants/guard-review.golden').read_text())}
  named={r['id']:r for r in json.loads((ROOT/'test/mutants/named-checks.golden').read_text())}
  for note in json.loads((ROOT/'test/mutants/unresolved.golden').read_text()):
   if note['kind']=='guard-site':self.assertEqual(review[note['id']]['status'],'unreviewed')
   else:self.assertNotEqual(named[note['id']]['status'],'killed')
 def grouped(self):
  note=importlib.machinery.SourceFileLoader('unreachable_note',str(ROOT/'scripts/unreachable-note')).load_module()
  classes=json.loads((ROOT/'test/vectors/scenario-classes.golden').read_text());facts=json.loads((ROOT/'test/vectors/scenario-calls.golden').read_text())
  return note,classes,facts,lambda key:[(c,f) for c,f in zip(classes,facts) if c['class']==key]
 def test_observed_calls_agree_with_the_classes(self):
  note,classes,facts,rows=self.grouped()
  self.assertEqual([f['id'] for f in facts],[c['id'] for c in classes])
  for c,f in zip(classes,facts):
   if f['observed']:
    self.assertEqual(sorted(f['calls']),c['entry_points'],c['id'])
    self.assertEqual((sum(f['cook_command'].values()),sum(f['analyze_command'].values())),(f['deployments'],f['deployments']),c['id'])
   else:self.assertEqual((set(f),c['entry_points']),({'id','observed'},[]),c['id'])
  self.assertEqual(sum(not f['observed'] for f in facts),11)
 def test_group_reasons_claim_nothing_a_record_contradicts(self):
  note,classes,facts,rows=self.grouped();doc=(ROOT/'docs/phase1-unreachable.md').read_text();total=lambda f:sum(f['calls'].values())
  text,own=note.deployment(rows('deployment-entry-point'))
  for c,f in rows('deployment-entry-point'):
   self.assertEqual(c['id'] not in own,list(f['cook_command'])==[note.FAKE_ADAPTER] and list(f['analyze_command'])==[note.FAKE_VERIFIER],c['id'])
  real=own['test_catalog.CatalogTests.test_role_prepare_adapter_build_lock_reuse_and_preflight_dropin']
  self.assertIn('the real adapter `scripts/cook-adapter.py`',real);self.assertNotIn('fake adapter',real);self.assertIn('78 of the 84',text);owned=dict(own)
  text,own=note.planning(rows('spawn-plan'));self.assertEqual(set(own),{c['id'] for c,f in rows('spawn-plan') if total(f)==1})
  self.assertIn('test_spawn.SpawnTests.test_identity_and_computed_digests',own);self.assertIn('11 of the 13 methods make 2 to 13',text);owned.update(own)
  text,own=note.none(rows('no-entry-point'));self.assertEqual(set(own),{c['id'] for c,f in rows('no-entry-point') if not f['observed']});owned.update(own)
  self.assertEqual(len(owned),19)
  for ident,reason in owned.items():self.assertEqual(sum(line.startswith('- `'+ident+'`') and line.endswith(reason) for line in doc.splitlines()),1,ident)
  for old in ('always constructed with the fake adapter','one snapshot precedes several different plan calls','most of these methods patch `verify_cook`'):self.assertNotIn(old,doc)
 def test_generator_refuses_facts_its_wording_does_not_cover(self):
  note,classes,facts,rows=self.grouped();edit=lambda group,change:[(c,dict(f,**change)) for c,f in group[:1]]+group[1:]
  with self.assertRaisesRegex(ValueError,'without create'):note.lifecycle(edit(rows('spawn-lifecycle'),{'calls':{'lifecycle:Lifecycle.status':1}}))
  with self.assertRaisesRegex(ValueError,'without a description'):note.deployment(edit(rows('deployment-entry-point'),{'cook_command':{'other':1}}))
  with self.assertRaisesRegex(ValueError,'not observed'):note.deployment(edit(rows('deployment-entry-point'),{'observed':False}))
 def test_hand_written_limits_cite_the_observed_numbers(self):
  note,classes,facts,rows=self.grouped();text=' '.join((ROOT/'docs/phase1-replay-limits.md').read_text().split());total=lambda f:sum(f['calls'].values())
  dep=rows('deployment-entry-point');plan=rows('spawn-plan');none=rows('no-entry-point');life=rows('spawn-lifecycle')
  plain=sum(list(f['cook_command'])==[note.FAKE_ADAPTER] and list(f['analyze_command'])==[note.FAKE_VERIFIER] for c,f in dep)
  single=[sum(total(f)==1 for c,f in group) for group in (dep,plan,life)];ran=[f for c,f in none if f['observed']]
  self.assertEqual((plain,single,len(ran),sum(f['deployments']>0 for f in ran)),(78,[8,2,0],111,16))
  self.assertTrue(all(list(f['cook_command'])==[note.FAKE_ADAPTER] for c,f in dep if total(f)==1))
  self.assertEqual(sum('lifecycle:Lifecycle.transition' in f['calls'] for c,f in life),25)
  for claim in ('78 build every `Deployment` with the fake adapter','Of the other 6, one uses the real adapter','All 28 call `create` and 25 call `transition`','11 methods issue 2 to 13 plan calls against one snapshot; 2 issue one',
   '111 ran that way (16 of them build a `Deployment` without using it); 11 real-cook integration tests skip','Ten of those make a single entry-point call: eight deployment methods, all with the fake adapter, and two planning methods','The other 115 make several calls','19 records differ'):
   self.assertIn(claim,text)

class PythonOnlyTests(unittest.TestCase):
 """Kills by an input no JSON document decodes to are real, declared, and counted apart."""
 def test_declared_witnesses_are_exactly_the_marked_tests(self):
  marked=set()
  for path in sorted((ROOT/'test/unit').glob('test_guard_*.py')):
   lines=path.read_text().splitlines()
   for index,line in enumerate(lines):
    found=re.match(r'\s*def (test_\w+)\(self\):(.*)',line)
    if found and '# non-JSON input' in found.group(2)+lines[index+1]:marked.add(path.stem+'.Guards.'+found.group(1))
  self.assertEqual(set(portable.python_only()),marked);self.assertEqual(len(marked),8)
 def test_python_only_kills_are_counted_apart(self):
  counts=json.loads((ROOT/'test/mutants/phase1-summary.golden').read_text());review=json.loads((ROOT/'test/mutants/guard-review.golden').read_text());declared=portable.python_only();kills=purpose.kills()
  only=[r for r in review if r['status']=='killed' and all('python_only' in e for e in r['evidence'])]
  self.assertEqual((counts['killed_ordinary'],counts['killed_python_only_witness'],counts['python_only_witnesses'],counts['portable_replacement_vectors']),(557,len(only),8,1))
  self.assertEqual(counts['killed_ordinary']+counts['killed_python_only_witness'],counts['combined_killed']);self.assertEqual(len(only),7)
  for row in only:self.assertIn(kills[row['id']],declared)
  self.assertEqual({r['id'] for r in review if any('python_only' in e for e in r['evidence'])},{ident for ident,test in kills.items() if test in declared and '#' not in ident})
  # The integer-key mutant also has a portable vector, so it is ordinary evidence.
  key=next(r for r in review if r['id']=='spawn.data.L34.8');self.assertEqual([e['kind'] for e in key['evidence']],['unit','portable-vector']);self.assertNotIn(key,only)
  names={n['id']:n for n in json.loads((ROOT/'test/mutants/named-checks.golden').read_text())}
  self.assertEqual((counts['named_killed_ordinary'],counts['named_killed_python_only_witness']),(199,1))
  self.assertEqual([e['vector'] for e in names['spawn.catalog.cycle']['evidence']],['test_guard_spawn_sources.Guards.test_catalog_L137_8'])
 def test_portable_vector_is_the_oracles_own_refusal_and_kills_cleanly(self):
  lines=portable.build();self.assertEqual((ROOT/'test/vectors/guard-portable.golden').read_text(),''.join(line+'\n' for line in lines.values()))
  row=json.loads(lines['spawn.data.L34.8'])
  self.assertEqual((row['function'],row['args'],row['outcome']),('connectome_spawn.data.json_data',[{'$mapping':[[1,'value']]}],{'exception':'SpawnError','message':'string keys required'}))
  results=json.loads((ROOT/'test/mutants/portable-results.golden').read_text());self.assertEqual([r['id'] for r in results],list(lines))
  for result in results:
   self.assertEqual((result['status'],result['runtime_errors'],result['vector_sha256']),('killed',0,hashlib.sha256(lines[result['id']].encode()).hexdigest()));self.assertIn(result['vector_sha256'],result['witnesses'])
 def test_declarations_are_closed(self):
  def using(data,call):
   with patch.object(portable,'fragments',return_value={'fixture':data}):return call()
  kills={'example.L1.4':'test_guard_fixture.Guards.test_one'};reason='A mapping that is not a dict.'
  self.assertEqual(using({'kills':kills,'python_only':{'test_guard_fixture.Guards.test_one':reason}},portable.python_only),{'test_guard_fixture.Guards.test_one':reason})
  for bad in ({'test_guard_fixture.Guards.test_other':reason},{'test_guard_fixture.Guards.test_one':'short'},{'test_guard_fixture.Guards.test_one':None}):
   with self.assertRaises(ValueError):using({'kills':kills,'python_only':bad},portable.python_only)
  good={'id':'spawn.data.L34.8','function':'connectome_spawn.data.json_data','args':[{'$mapping':[[1,'value']]}]}
  self.assertEqual(using({'portable_vectors':[good]},portable.declared),[good])
  for bad in (dict(good,id='not.a.mutant'),dict(good,id='spawn.catalog.cycle#1'),dict(good,outcome={}),{k:v for k,v in good.items() if k!='args'}):
   with self.assertRaises(ValueError):using({'portable_vectors':[bad]},portable.declared)
  with self.assertRaises(ValueError):using({'portable_vectors':[good,dict(good)]},portable.declared)
  # An input the oracle accepts, a crash, or an unaudited function is not a portable refusal vector.
  for bad in (dict(good,args=[{'$mapping':[['1','value']]}]),dict(good,args=[]),dict(good,function='connectome_spawn.data.canonical'),dict(good,function='connectome_lock.map_lock')):
   with self.assertRaises(ValueError):using({'portable_vectors':[bad]},portable.build)
 def test_summary_refuses_a_portable_vector_without_its_kill(self):
  real=Path.read_text
  def read_text(path,*a,**kw):
   text=real(path,*a,**kw)
   if path.name!='portable-results.golden':return text
   rows=json.loads(text);rows[0]['status']='survived';return json.dumps(rows)
  with patch.object(Path,'read_text',read_text):
   with self.assertRaisesRegex(ValueError,'portable vector without its kill'):summary.summarize()
 def test_readiness_gate_shows_them_in_a_note_not_a_blocking_line(self):
  text=(ROOT/'scripts/check-module-ready').read_text()
  self.assertIn("print('NOTE: killed only by a Python-only witness",text);self.assertNotIn("missing.append('python",text.lower().replace('python-only witness',''))
if __name__=='__main__':unittest.main()
