import ast, hashlib, importlib.machinery, importlib.util, json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'test/differential'),str(ROOT/'test/mutants')]
spec=importlib.util.spec_from_file_location('guard_api_vectors',ROOT/'test/differential/guard_vectors.py');vectors=importlib.util.module_from_spec(spec);spec.loader.exec_module(vectors)
vectors.initialize(ROOT/'oracle/module_utils')
inv=importlib.machinery.SourceFileLoader('phase1_inventory',str(ROOT/'scripts/inventory-sites')).load_module()
summary=importlib.machinery.SourceFileLoader('phase1_summary',str(ROOT/'scripts/summarize-phase1')).load_module()
from named_checks import build
from named_guards import mutants

class Phase1EvidenceTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):(ROOT/'.cache').mkdir(exist_ok=True)
 def test_lock_fixture_is_per_worker_module_not_shared_parent(self):
  import connectome_lock
  with tempfile.TemporaryDirectory(dir=ROOT/'.cache') as folder:
   parent=Path(folder)
   for worker in ('worker-a','worker-b'):
    module=parent/worker/'connectome_lock.py';module.parent.mkdir()
    with patch.object(connectome_lock,'__file__',str(module)):
     vectors.invoke('connectome_lock.map_lock',[{},'@BUILD@'],{})
    self.assertTrue((module.parent/'.cache/guard-lock-fixture/example.service').is_file())
   self.assertFalse((parent/'.cache/guard-lock-fixture').exists())
 def test_exactly_203_expanded_names_not_citations(self):
  rows=inv.inventory(ROOT/'docs/approved-plan.md');names=[n for r in rows[47:] for n in r['ids']]
  self.assertEqual(len(names),203);self.assertEqual(len(set(names)),203)
  self.assertTrue(all(':' not in n and not n.startswith('.') for n in names))
  self.assertEqual(rows[47]['ids'][0],'catalog.unit.reset')
  self.assertIn('cook.git.hooks',names)
 def test_all_named_checks_have_actual_source_anchors(self):
  names=build();self.assertEqual(len(names),203)
  for row in names:
   self.assertTrue(row['anchors'])
   for a in row['anchors']:
    source=(ROOT/'oracle'/a['file']).read_text()
    self.assertEqual(source.splitlines()[a['line']-1],a['text'])
    self.assertTrue(a['text'].strip());self.assertFalse(a['text'].lstrip().startswith('#'))
    self.assertEqual(hashlib.sha256(source.encode()).hexdigest(),a['source_sha256'])
 def test_named_guard_mutations_compile_and_have_unique_names(self):
  rows=mutants();self.assertEqual(len(rows),len({r['id'] for r in rows}))
  for row in rows:
   source=(ROOT/'oracle'/row['file']).read_text();self.assertEqual(source[row['start']:row['end']],row['before'])
   compile(source[:row['start']]+row['after']+source[row['end']:],row['file'],'exec')
 def test_ast_confirms_no_omitted_call_or_raise_line(self):
  count=0
  for name,file in inv.files(ROOT/'oracle').items():
   source=file.read_text();lines=set(inv.sites(source));count+=len(lines)
   for node in ast.walk(ast.parse(source)):
    if isinstance(node,ast.Raise) or isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in ('need','require','unsafe'):
     self.assertIn(node.lineno,lines,(name,node.lineno))
  self.assertEqual(count,585)
 def test_errors_and_timeouts_are_not_unit_kills(self):
  trial={'tests':1,'failures':1,'errors':0}
  self.assertTrue(summary.unit_kill({'status':'killed','trials':[trial]}))
  for change in ({'errors':1},{'tests':2},{'failures':0},{'timeout':True},{'driver_error':True}):
   self.assertFalse(summary.unit_kill({'status':'killed','trials':[dict(trial,**change)]}))
 def test_unexpected_runtime_exception_is_not_a_vector_kill(self):
  import connectome_inputs
  with patch.object(connectome_inputs,'validate',side_effect=TypeError('runtime')):
   self.assertIsNone(vectors.invoke('connectome_inputs.validate',[{}],{}))
  with patch.object(connectome_inputs,'validate',side_effect=ValueError('declared refusal')):
   self.assertEqual(vectors.invoke('connectome_inputs.validate',[{}],{}),{'exception':'ValueError','message':'declared refusal'})
 def test_explicit_clock_and_named_capability_fake(self):
  name='connectome_spawn.identity.temporary_name';args=[{'temporaryNameTemplate':'Example{timestamp}'}]
  a=vectors.invoke(name,args,{});b=vectors.invoke(name,args,{})
  self.assertEqual(a,b);self.assertEqual(a,{'result':'Example20000101T000000000000Z'})
  self.assertEqual(vectors.invoke(name,args,{'now':'2000-01-01T00:00:00'})['exception'],'SpawnError')
  caps={'supports_rename':True,'max_name_length':63,'name_pattern':'[A-Za-z]+'}
  self.assertEqual(vectors.invoke('connectome_spawn.identity.validate_provider_name',[caps,'Example'],{}),{'result':None})
 def test_missing_or_unknown_profile_refuses(self):
  with tempfile.TemporaryDirectory(dir=ROOT/'.cache') as folder:
   path=Path(folder)/'vector.golden';path.write_text(json.dumps({'function':'connectome_lock.map_lock','args':[],'kwargs':{},'outcome':{}})+'\n')
   with self.assertRaisesRegex(ValueError,'profile'):vectors.replay(path)
   path.write_text(json.dumps({'function':'subprocess.run'})+'\n')
   with self.assertRaisesRegex(ValueError,'unaudited'):vectors.replay(path)
 def test_committed_negative_corpus_replays_without_errors(self):
  result=vectors.replay(ROOT/'test/vectors/guard-negative.golden')
  self.assertGreater(result['replayed'],150);self.assertEqual(result['mismatches'],0);self.assertEqual(result['runtime_errors'],0)
 def test_runtime_error_does_not_become_mismatch(self):
  import connectome_inputs
  row={'function':'connectome_inputs.validate','args':[{'$mapping':[]}],'kwargs':{'$mapping':[]},'outcome':{'result':None}}
  with tempfile.TemporaryDirectory(dir=ROOT/'.cache') as folder:
   path=Path(folder)/'vector.golden';path.write_text(json.dumps(row)+'\n')
   with patch.object(connectome_inputs,'validate',side_effect=TypeError('runtime')):result=vectors.replay(path)
   self.assertEqual((result['runtime_errors'],result['mismatches']),(1,0))
if __name__=='__main__':unittest.main()
