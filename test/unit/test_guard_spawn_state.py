"""Purpose-built vectors for the spawn state guards: lifecycle, identity, schema, CLI, data, persistence, seed."""
import contextlib, errno, io, json, os, re, sys, tempfile, unittest
from collections import UserDict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'oracle/module_utils'),str(ROOT/'oracle/tests')]
from connectome_spawn import FileIdentityProvider, Lifecycle, SpawnError
from connectome_spawn import cli, data, identity, persistence, schema, seed
from connectome_spawn.lifecycle import STATES
import test_spawn
from test_spawn_lifecycle import HOME, IDENTITY, PAYLOAD, FakeAdapter, FakeImporter

AGENT='example-agent'
REFUSAL='{"error": "spawn input refused", "filesWritten": false}\n'
HEX32=r'^[0-9a-f]{32}$'

class HookAdapter(FakeAdapter):
 """Fake adapter that lets a test stage an outside change at one step."""
 def __init__(self,hook):
  super().__init__();self.hook=hook
 def perform(self,step,context,operation_id):
  super().perform(step,context,operation_id);self.hook(step,context)

class ReplacingRename(FileIdentityProvider):
 """Fake provider fault: a rename that answers with a different handle."""
 def rename(self,handle,name_record_value,operation_id):
  super().rename(handle,name_record_value,operation_id)
  return dict(handle,id='0'*32)

class Guards(unittest.TestCase):
 def setUp(self):
  (ROOT/'.cache').mkdir(parents=True,exist_ok=True)
  temp=tempfile.TemporaryDirectory(dir=ROOT/'.cache');self.addCleanup(temp.cleanup)
  self.work=Path(temp.name);self.root=self.work/'agents';self.identities=self.work/'identities'
  self.name_file=self.work/'name-record.json';self.name_file.write_text(json.dumps(IDENTITY))

 # ---- helpers -------------------------------------------------------
 def planned(self,mode='clean',agent=AGENT,**parts):
  life=Lifecycle(self.root,**parts)
  life.create(agent,seed={'mode':mode},identity=IDENTITY,home=HOME,operation_id='create',payload=PAYLOAD if mode=='required' else None)
  return life
 def persisted(self,agent=AGENT):
  return json.loads((self.root/agent/'lifecycle.json').read_text())
 def rewrite(self,changes,agent=AGENT):
  path=self.root/agent/'lifecycle.json';path.write_text(json.dumps(dict(json.loads(path.read_text()),**changes)))
 def refused(self,message,call,*args,**kwargs):
  with self.assertRaisesRegex(SpawnError,'^'+re.escape(message)+'$'):call(*args,**kwargs)
 def provider_file(self,**changes):
  path=self.identities/'identities.json'
  if not path.exists():
   FileIdentityProvider(self.identities);self.pristine=json.loads(path.read_text())
  path.write_text(json.dumps(dict(self.pristine,**changes)))
 def run_cli(self,*argv):
  out,err=io.StringIO(),io.StringIO()
  with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):code=cli.main([str(item) for item in argv])
  return code,out.getvalue(),err.getvalue()
 def documents(self):
  instance=self.work/'instance.json';instance.write_text(json.dumps(test_spawn.example_instance()))
  return ['--inline',json.dumps(test_spawn.example_template()),'--instance',instance]
 def tree(self,root):
  return sorted((str(path.relative_to(root)),path.lstat().st_mode,path.lstat().st_size,path.lstat().st_mtime_ns) for path in root.rglob('*'))

 # ---- CLI (row 74) --------------------------------------------------
 def test_spawn_cli_L15_8(self):
  documents=self.documents()
  accepted=self.run_cli('validate',*documents)
  self.assertEqual((accepted[0],accepted[2]),(0,''));self.assertTrue(json.loads(accepted[1])['valid'])
  self.assertEqual(self.run_cli('validate',*documents,'--unknown-option=protected-value'),(2,'',REFUSAL))
 def test_spawn_cli_L40_16(self):
  self.planned()
  accepted=self.run_cli('status',AGENT,'--state-root',self.root)
  self.assertEqual((accepted[0],accepted[2]),(0,''));self.assertEqual(json.loads(accepted[1])['state'],'planned')
  for option in ('--inline','--file','--repo','--instance','--catalog','--stores','--observed','--seed-file','--name-file','--name-repo'):
   self.assertEqual(self.run_cli('status',AGENT,'--state-root',self.root,option,'value'),(2,'',REFUSAL),option)
 def test_spawn_cli_L44_12(self):
  documents=self.documents()
  self.assertEqual(self.run_cli('validate',*documents)[0],0)
  self.assertEqual(self.run_cli('validate',AGENT,*documents),(2,'',REFUSAL))
  self.assertEqual(self.run_cli('validate','--state-root',self.root,*documents),(2,'',REFUSAL))
 def test_spawn_cli_L49_16(self):
  documents=self.documents()
  self.assertEqual(self.run_cli('validate',*documents)[0],0)
  for option in ('--catalog','--stores','--observed','--seed-file','--name-file','--name-repo'):
   self.assertEqual(self.run_cli('validate',*documents,option,'value'),(2,'',REFUSAL),option)
 def test_spawn_cli_L54_16(self):
  # An empty catalog argument must not fall back to the working directory,
  # even when that directory happens to be a complete role catalog.
  fixture=test_spawn.SpawnTests('test_cli_read_only_independent_of_ansible_and_working_directory');fixture.setUp();self.addCleanup(fixture.doCleanups)
  base=['plan','--file',fixture.work/'template.json','--instance',fixture.work/'instance.json','--stores',fixture.work/'stores.json']
  self.addCleanup(os.chdir,os.getcwd());os.chdir(fixture.catalog_root)
  accepted=self.run_cli(*base,'--catalog',fixture.catalog_root)
  self.assertEqual((accepted[0],accepted[2]),(0,''));self.assertIn('planHash',json.loads(accepted[1]))
  self.assertEqual(self.run_cli(*base,'--catalog',''),(2,'',REFUSAL))
 def test_spawn_cli_refusal_fixed_message(self):
  documents=self.documents()
  refusals=[self.run_cli('validate','--inline','{"schema": "private-template-text"}','--instance',documents[3]),
   self.run_cli('validate','--file',self.work/'absent-template.yml','--instance',documents[3]),
   self.run_cli('status',AGENT,'--state-root',self.work/'absent-root'),
   self.run_cli('plan',*documents),
   self.run_cli('validate',*documents,'--catalog','value'),
   self.run_cli('unknown-command')]
  for refusal in refusals:self.assertEqual(refusal,(2,'',REFUSAL))
 def test_spawn_cli_refusal_files_written_false(self):
  code,out,err=self.run_cli('status',AGENT,'--state-root',self.work/'absent-root')
  self.assertEqual((code,out),(2,''))
  self.assertEqual(json.loads(err),{'error':'spawn input refused','filesWritten':False})
  self.assertEqual(err.encode(),REFUSAL.encode())
 def test_spawn_cli_status_readonly(self):
  absent=self.work/'absent-root'
  self.assertEqual(self.run_cli('status',AGENT,'--state-root',absent),(2,'',REFUSAL))
  self.assertFalse(absent.exists(),'status of an unknown agent created its state root')
  self.planned();(self.root/AGENT/'.lock').unlink()
  before=self.tree(self.work)
  code,out,err=self.run_cli('status',AGENT,'--state-root',self.root)
  self.assertEqual((code,err),(0,''));self.assertEqual(json.loads(out)['state'],'planned')
  self.assertEqual(self.tree(self.work),before)
  self.assertEqual(sorted(path.name for path in (self.root/AGENT).iterdir()),['lifecycle.json'])

 # ---- strict decoding (row 27) --------------------------------------
 def test_spawn_data_L34_8(self):
  # non-JSON input
  self.assertEqual(data.canonical({'1':'value'}),b'{"1":"value"}')
  self.refused('string keys required',data.canonical,{1:'value'})
 def test_spawn_data_L74_8(self):
  # non-JSON input
  class Text(str):pass
  self.assertEqual(data.decode('{"key": 1}'),{'key':1})
  self.refused('invalid JSON/YAML document',data.decode,Text('{"key": 1}'))
 def test_spawn_data_L76_8(self):
  def document(count):return '{"key": "'+chr(0xe9)*count+'"}'
  small=document(500000);large=document(600000)
  self.assertLessEqual(len(large),data.LIMIT);self.assertGreater(len(large.encode('utf-8')),data.LIMIT)
  self.assertEqual(len(data.decode(small)['key']),500000)
  self.refused('invalid JSON/YAML document',data.decode,large)
 def test_spawn_data_L89_20(self):
  def document(lists):return 'key: '+'['*lists+']'*lists
  value=data.decode(document(31))['key']
  for _ in range(30):value=value[0]
  self.assertEqual(value,[])
  # Root mapping plus 32 sequences is 33 open collections in the YAML event stream.
  self.refused('invalid JSON/YAML document',data.decode,document(32))

 # ---- identity provider (rows 57, 71) -------------------------------
 def test_spawn_identity_L32_4(self):
  self.refused('timezone-aware temporary identity timestamp required',identity.temporary_name,None,datetime(2026,1,2,3,4,5))
 def test_spawn_identity_L107_8(self):
  # non-JSON input
  FileIdentityProvider(self.identities)
  stored=json.loads((self.identities/'identities.json').read_text())
  for field in ('identities','operations'):
   with patch.object(identity,'read',return_value=dict(stored,**{field:UserDict()})):
    self.refused('invalid provider state',FileIdentityProvider,self.identities)
 def test_spawn_identity_L112_12(self):
  row={'agent':AGENT,'name':'ExampleAgent','kind':'temporary','retired':False}
  self.provider_file(identities={'a'*32:row});self.assertEqual(len(FileIdentityProvider(self.identities).list()),1)
  for key,bad in (('not-hex',row),('a'*32,dict(row,agent='Not An Agent')),('a'*32,dict(row,kind='pooled')),('a'*32,dict(row,retired='no'))):
   self.provider_file(identities={key:bad})
   self.refused('invalid identity record',FileIdentityProvider,self.identities)
 def test_spawn_identity_L118_16(self):
  row={'agent':AGENT,'name':'ExampleAgent','kind':'temporary','retired':False}
  self.provider_file(identities={'a'*32:row,'b'*32:dict(row,retired=True)});self.assertEqual(len(FileIdentityProvider(self.identities).list()),2)
  for other in (dict(row,agent='other-agent'),dict(row,name='OtherName')):
   self.provider_file(identities={'a'*32:row,'b'*32:other})
   self.refused('duplicate active identity',FileIdentityProvider,self.identities)
 def test_spawn_identity_L123_12(self):
  self.provider_file(operations={AGENT+':retire':{'digest':'0'*64,'result':None}});FileIdentityProvider(self.identities)
  for key,digest in (('Not A Key','0'*64),(AGENT+':retire','short')):
   self.provider_file(operations={key:{'digest':digest,'result':None}})
   self.refused('invalid provider operation',FileIdentityProvider,self.identities)
 def test_spawn_identity_L132_8(self):
  provider=FileIdentityProvider(self.identities)
  self.refused('invalid identity handle',provider.retire,{'id':'not-a-handle','agent':AGENT},'retire')
 def test_spawn_identity_L134_8(self):
  provider=FileIdentityProvider(self.identities)
  handle=provider.provision_temporary(AGENT,'ExampleNewborn','provision')
  self.refused('unknown identity handle',provider.retire,dict(handle,agent='other-agent'),'retire')
  self.assertEqual([row['retired'] for row in provider.list()],[False])
 def test_spawn_identity_L178_12(self):
  provider=FileIdentityProvider(self.identities,supports_rename=True)
  handle=provider.provision_temporary(AGENT,'ExampleNewborn','provision')
  provider.provision_permanent('other-agent',IDENTITY,'provision')
  self.refused('permanent identity collision',provider.rename,handle,IDENTITY,'rename')
  self.assertEqual(sorted(row['name'] for row in provider.list()),['ExampleAgent','ExampleNewborn'])
 def test_spawn_identity_no_reuse(self):
  provider=FileIdentityProvider(self.identities)
  first=provider.provision_temporary(AGENT,'ExampleNewborn','provision')
  provider.retire(first,'retire')
  second=provider.provision_temporary('other-agent','OtherNewborn','provision')
  self.assertRegex(first['id'],HEX32);self.assertRegex(second['id'],HEX32)
  self.assertNotEqual(second['id'],first['id'],'a provision reused an existing handle')
  third=provider.provision_temporary(AGENT,'ExampleNewborn','provision-again')
  self.assertEqual(len({first['id'],second['id'],third['id']}),3)
  stored=json.loads((self.identities/'identities.json').read_text())['identities']
  self.assertEqual(sorted(stored),sorted((first['id'],second['id'],third['id'])))
  self.assertEqual([stored[handle['id']]['retired'] for handle in (first,second,third)],[True,False,False])
 def test_spawn_identity_tombstone_marks(self):
  provider=FileIdentityProvider(self.identities)
  handle=provider.provision_temporary(AGENT,'ExampleNewborn','provision')
  self.assertIsNone(provider.retire(handle,'retire'))
  stored=json.loads((self.identities/'identities.json').read_text())['identities']
  self.assertEqual(stored.get(handle['id'],{}).get('retired'),True)
  self.assertEqual([row['recovery'] for row in provider.list()],['retired; never reused'])
 def test_spawn_identity_tombstone_keeps_row(self):
  provider=FileIdentityProvider(self.identities)
  handle=provider.provision_temporary(AGENT,'ExampleNewborn','provision')
  provider.retire(handle,'retire')
  stored=json.loads((self.identities/'identities.json').read_text())['identities']
  self.assertEqual(stored,{handle['id']:{'agent':AGENT,'name':'ExampleNewborn','kind':'temporary','retired':True}})

 # ---- lifecycle (rows 40, 56, 57) -----------------------------------
 def test_spawn_lifecycle_L54_4(self):
  life=self.planned()
  for intent in ({'mode':'clean','branch':'seeded','digest':'a'*64},{'mode':'required','branch':'clean','digest':None}):
   self.rewrite({'seed':intent})
   self.refused('inconsistent persisted seed intent',life.status,AGENT)
 def test_spawn_lifecycle_L96_8(self):
  life=self.planned()
  self.assertEqual(life.status(AGENT)['store'],str(self.root/AGENT/'state/store'))
  self.rewrite({'store':str(self.root/'other-agent/state/store')})
  self.refused('store binding mismatch',life.status,AGENT)
 def test_spawn_lifecycle_L131_8(self):
  life=Lifecycle(self.root)
  self.refused('invalid operation id',life.create,AGENT,seed={'mode':'clean'},identity=IDENTITY,home=HOME,operation_id='not an id')
  self.assertFalse(self.root.exists())
 def test_spawn_lifecycle_L136_16(self):
  life=self.planned()
  self.refused('agent exists or operation id conflicts',life.create,AGENT,seed={'mode':'clean'},identity=IDENTITY,home=HOME,operation_id='another-create')
  self.assertEqual(sorted(self.persisted()['operations']),['create'])
 def test_spawn_lifecycle_L151_8(self):
  life=self.planned()
  for target in ('planned','unknown-state'):
   self.refused('illegal lifecycle target',life.transition,AGENT,target,'move')
 def test_spawn_lifecycle_L152_8(self):
  life=self.planned()
  self.refused('invalid operation id',life.transition,AGENT,'failed','not an id')
  self.assertEqual(self.persisted()['state'],'planned')
 def test_spawn_lifecycle_L161_12(self):
  life=self.planned()
  self.refused('name record required only for named transition',life.transition,AGENT,'failed','fail',name_file=self.name_file)
  self.refused('name record required only for named transition',life.transition,AGENT,'named','name')
  self.assertEqual(self.persisted()['state'],'planned')
 def test_spawn_lifecycle_L162_12(self):
  life=self.planned()
  self.refused('seed payload not accepted for this transition',life.transition,AGENT,'failed','fail',payload=PAYLOAD)
  self.assertEqual(self.persisted()['state'],'planned')
 def test_spawn_lifecycle_L186_16(self):
  adapter,importer=FakeAdapter(),FakeImporter()
  life=self.planned('required',adapter=adapter,importer=importer)
  self.refused('seed payload does not match planned digest',life.transition,AGENT,'active','activate',payload=b'different seed bytes')
  self.assertEqual((adapter.events,importer.calls),([],0))
  self.assertEqual(life.transition(AGENT,'active','activate',payload=PAYLOAD)['state'],'active')
 def test_spawn_lifecycle_L190_16(self):
  # Without an importer the refusal comes before the intent and the first
  # effect. If it did not, the build and hold effects would run; the fake
  # host then finds a store in place, which the engine itself refuses.
  def hook(step,context):
   if step=='hold':Path(context['store']).mkdir(parents=True)
  adapter=HookAdapter(hook)
  life=self.planned('required',adapter=adapter)
  self.refused('seed importer adapter required',life.transition,AGENT,'active','activate',payload=PAYLOAD)
  self.assertEqual(adapter.events,[]);self.assertIsNone(self.persisted()['pending'])
 def test_spawn_lifecycle_L192_16(self):
  adapter=FakeAdapter()
  life=self.planned(adapter=adapter,identity_provider=FileIdentityProvider(self.identities))
  self.rewrite({'state':'named'})
  self.refused('temporary identity missing',life.transition,AGENT,'active','activate')
  self.assertEqual(adapter.events,[]);self.assertIsNone(self.persisted()['pending'])
 def test_spawn_lifecycle_L194_12(self):
  # The permanent name is already live for another agent, so an engine that
  # went on without an adapter would meet the provider's own refusal.
  provider=FileIdentityProvider(self.identities)
  life=self.planned(adapter=FakeAdapter(),identity_provider=provider)
  life.transition(AGENT,'initiation','initiation');life.transition(AGENT,'named','named',name_file=self.name_file)
  provider.provision_permanent('other-agent',IDENTITY,'provision')
  bare=Lifecycle(self.root,identity_provider=provider)
  self.refused('lifecycle adapter required',bare.transition,AGENT,'active','activate')
  self.assertIsNone(self.persisted()['pending']);self.assertEqual(self.persisted()['state'],'named')
 def test_spawn_lifecycle_L252_24(self):
  provider=ReplacingRename(self.identities,supports_rename=True)
  life=self.planned(adapter=FakeAdapter(),identity_provider=provider)
  life.transition(AGENT,'initiation','initiation');life.transition(AGENT,'named','named',name_file=self.name_file)
  self.refused('rename must preserve identity handle',life.transition,AGENT,'active','activate')
  self.assertEqual(self.persisted()['state'],'named')
 def test_spawn_lifecycle_state_schema_closed_record(self):
  life=self.planned()
  self.assertEqual(life.status(AGENT)['state'],'planned')
  self.rewrite({'unlisted':True})
  self.refused('missing or unknown fields',life.status,AGENT)
 def test_spawn_lifecycle_state_schema_values(self):
  life=self.planned();valid=self.persisted()
  for change in ({'state':'limbo'},{'schema':'connectome.spawn-state/v2'},{'agent':'other-agent'}):
   self.rewrite(dict(valid,**change))
   self.refused('invalid lifecycle state',life.status,AGENT)
 def table(self,first):
  legal={('clean','planned','initiation'),('clean','initiation','named'),('clean','named','active'),('seeded','planned','active')}
  provider=FileIdentityProvider(self.identities);adapter=FakeAdapter()
  life=Lifecycle(self.root,adapter=adapter,identity_provider=provider,importer=FakeImporter())
  refused=0
  for branch in first:
   for source in STATES:
    agent=branch+'-'+source
    life.create(agent,seed={'mode':'required' if branch=='seeded' else 'clean'},identity=IDENTITY,home=HOME,operation_id='create',payload=PAYLOAD if branch=='seeded' else None)
    self.rewrite({'state':source},agent)
    for target in STATES[1:]:
     allowed=(branch,source,target) in legal or target=='failed' and source in STATES[:4] or target=='retired' and source in STATES[:5]
     if allowed:continue
     arguments={'name_file':self.name_file} if target=='named' else {}
     with self.assertRaisesRegex(SpawnError,'^illegal lifecycle transition$',msg=(branch,source,target)):life.transition(agent,target,'move-'+target,**arguments)
     refused+=1
    self.assertEqual((self.persisted(agent)['state'],self.persisted(agent)['pending']),(source,None))
  self.assertEqual((adapter.events,provider.list()),([],[]))
  return refused
 def test_spawn_lifecycle_table_seeded_rows(self):
  # The clean rows (initiation, named, active after named) are closed to a seeded agent.
  self.assertEqual(self.table(('seeded','clean')),38)
 def test_spawn_lifecycle_table_enforced(self):
  self.assertEqual(self.table(('clean','seeded')),38)
  life=self.planned(adapter=FakeAdapter())
  self.assertEqual(life.transition(AGENT,'failed','fail')['state'],'failed')
  self.assertEqual(life.transition(AGENT,'retired','retire')['state'],'retired')

 # ---- persistence (row 58) ------------------------------------------
 def test_spawn_persistence_L17_4(self):
  self.assertEqual(persistence.safe_path(self.root/'state.json'),self.root/'state.json')
  self.refused('path traversal refused',persistence.safe_path,self.root/'..'/'escape.json')
 def test_spawn_persistence_L19_8(self):
  real=self.work/'real';real.mkdir();link=self.work/'link';link.symlink_to(real,target_is_directory=True)
  self.assertEqual(persistence.safe_path(real/'state.json'),real/'state.json')
  self.refused('symlink persistence path refused',persistence.safe_path,link/'state.json')
  self.refused('symlink persistence path refused',persistence.safe_path,link)
 def test_spawn_persistence_L37_4(self):
  occupied=self.work/'occupied';occupied.write_text('not a directory')
  self.assertEqual(persistence.directory(self.work),self.work)
  self.refused('directory required',persistence.directory,occupied)
 def test_spawn_persistence_L49_4(self):
  path=self.work/'state.json'
  persistence.write(path,{'value':'x'*1024});self.assertEqual(persistence.read(path),{'value':'x'*1024})
  large=self.work/'large.json'
  self.refused('state size limit exceeded',persistence.write,large,{'value':'x'*(1024*1024)})
  self.assertEqual(sorted(item.name for item in self.work.iterdir()),['name-record.json','state.json'])
 def test_spawn_persist_lock_nofollow_symlink(self):
  # The link appears right after the path check, as in a swap between check and open.
  root=self.work/'locked';root.mkdir();lock=root/'.lock';outside=self.work/'outside-target'
  real=Path.is_symlink
  def swapped(path):
   answer=real(path)
   if path==lock and not answer:os.symlink(outside,lock)
   return answer
  with patch.object(Path,'is_symlink',swapped):
   with self.assertRaises(OSError) as caught:
    with persistence.locked(root):pass
  self.assertEqual(caught.exception.errno,errno.ELOOP)
  self.assertTrue(lock.is_symlink());self.assertFalse(outside.exists())
 def test_spawn_persist_lock_mode(self):
  self.addCleanup(os.umask,os.umask(0))
  root=self.work/'locked'
  with persistence.locked(root):self.assertEqual((root/'.lock').lstat().st_mode&0o7777,0o600)
  self.assertEqual((root/'.lock').lstat().st_mode&0o7777,0o600)

 # ---- closed documents (rows 42, 71) --------------------------------
 def test_spawn_schema_L35_4(self):
  value=test_spawn.example_template();self.assertEqual(schema.template(value)['schema'],value['schema'])
  value['agent']['systemPrompt']='x'*(1024*1024)
  self.refused('document too large',schema.template,value)
 def test_spawn_schema_L39_16(self):
  self.assertIsNone(schema.data_only({'settings':{'label':'value'}}))
  for key in ('command','Com-Mand','ENV','entry_point'):
   self.refused('executable fields forbidden in data',schema.data_only,{'settings':{key:'value'}})
 def test_spawn_schema_L104_8(self):
  value=test_spawn.example_template();value['agent']['strategy']['recentWindowTokens']=value['agent']['contextBudgetTokens']
  self.assertEqual(schema.template(value)['agent'],value['agent'])
  value['agent']['strategy']['recentWindowTokens']+=1
  self.refused('tail exceeds context budget',schema.template,value)
 FILE_RECEIPT={'recordHash':'a'*64,'source':{'kind':'file','path':'/fixture/names/agent.json'}}
 REPO_RECEIPT={'recordHash':'a'*64,'source':{'kind':'repo','repo':'/fixture/config-repo','commit':'c'*40,'path':'names/agent.json','blob':'b'*40,'author':'Fixture Author','committer':'Fixture Committer'}}
 def receipt(self,base,**source):
  value=deepcopy(base);value['source'].update(source);return value
 def test_spawn_schema_L191_4(self):
  self.assertEqual(schema.name_receipt(deepcopy(self.FILE_RECEIPT)),self.FILE_RECEIPT)
  for bad in ('a'*63,'A'*64,''):
   self.refused('invalid name record hash',schema.name_receipt,dict(deepcopy(self.FILE_RECEIPT),recordHash=bad))
 def test_spawn_schema_L193_4(self):
  self.assertEqual(schema.name_receipt(deepcopy(self.REPO_RECEIPT)),self.REPO_RECEIPT)
  self.refused('invalid name source',schema.name_receipt,self.receipt(self.REPO_RECEIPT,kind='network'))
 def test_spawn_schema_L196_8(self):
  for bad in ('names/agent.json','/fixture/../agent.json'):
   self.refused('invalid name file path',schema.name_receipt,self.receipt(self.FILE_RECEIPT,path=bad))
 def test_spawn_schema_L202_8(self):
  for change in ({'commit':'0'*40},{'blob':'0'*40},{'commit':'c'*39},{'blob':'main'}):
   self.refused('invalid pinned name object',schema.name_receipt,self.receipt(self.REPO_RECEIPT,**change))
 def test_spawn_schema_L204_8(self):
  for change in ({'author':7},{'committer':None}):
   self.refused('invalid provenance',schema.name_receipt,self.receipt(self.REPO_RECEIPT,**change))
 def test_spawn_schema_L213_4(self):
  self.assertEqual(schema.activation_identity(IDENTITY,HOME),IDENTITY)
  for home in ({'transport':'Console','channel':'primary'},{'transport':'console','channel':'#primary'},{'transport':'console','channel':'team/../primary'}):
   self.refused('unsafe home binding',schema.activation_identity,IDENTITY,home)
 def test_spawn_schema_home_closed(self):
  self.refused('missing or unknown fields',schema.activation_identity,IDENTITY,dict(HOME,topic='initiation'))
 def test_spawn_schema_template_closed(self):
  value=test_spawn.example_template();value['unlisted']='value'
  self.refused('missing or unknown fields',schema.template,value)
 def test_spawn_schema_template_version(self):
  value=test_spawn.example_template();value['schema']='connectome.spawn-template/v3'
  self.refused('unsupported template schema',schema.template,value)
 def test_spawn_schema_instance_closed(self):
  value=test_spawn.example_instance();self.assertEqual(schema.instance(value),value)
  value['unlisted']='value'
  self.refused('missing or unknown fields',schema.instance,value)
 def test_spawn_schema_instance_version(self):
  value=test_spawn.example_instance();value['schema']='connectome.spawn-instance/v2'
  self.refused('unsupported instance schema',schema.instance,value)
 def test_spawn_schema_name_record_closed(self):
  self.assertEqual(schema.name_record(IDENTITY),IDENTITY)
  self.refused('missing or unknown fields',schema.name_record,dict(IDENTITY,unlisted='value'))
 def test_spawn_schema_name_record_concrete(self):
  for key,bad in (('displayName','TODO'),('systemPrompt','null'),('displayName','Example Agent '),('name','placeholder')):
   self.refused('nonempty concrete value required',schema.name_record,dict(IDENTITY,**{key:bad}))
 def test_spawn_schema_name_receipt_closed(self):
  self.refused('missing or unknown fields',schema.name_receipt,dict(deepcopy(self.FILE_RECEIPT),unlisted='value'))
 def test_spawn_seed_L32_4(self):
  self.assertEqual(seed.seed_intent({'mode':'optional'},b'x')['branch'],'seeded')
  for payload in (b'',b'x'*(1024*1024+1)):
   self.refused('bounded nonempty seed bytes required',seed.seed_intent,{'mode':'optional'},payload)
if __name__=='__main__':unittest.main()
