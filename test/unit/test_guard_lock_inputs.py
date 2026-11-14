"""Killing tests for the lock reader, the operator-input schemas and the two lookup plugins."""
import copy, hashlib, importlib.util, json, os, re, sys, tempfile, types, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'oracle/module_utils'),str(ROOT/'oracle/tests')]
import connectome_archive as archive
import connectome_inputs as inputs
import connectome_lock as lock

RECIPE=b'{"name": "example"}\n'
DATA_ARGS='^invalid external_data_args$'
PROVENANCE='^invalid operator recipe provenance$'


class Guards(unittest.TestCase):
 # ---- fixtures -------------------------------------------------------
 def scratch(self):
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
  return Path(tmp.name).resolve()

 def lock_fixture(self):
  """The frozen valid v2 lock, bound to a real build directory with its launcher."""
  build=self.scratch()/'build';build.mkdir()
  (build/'run.sh').write_text('#!/bin/sh\nexit 0\n')
  data=json.loads((ROOT/'oracle/tests/fixtures/locks/v2.json').read_text())
  data['build']['dir']=str(build);data['launch']['script']='run.sh'
  self.assertEqual(lock.map_lock(copy.deepcopy(data),build),data)
  return data,build

 def lock_file(self,data,build,rewrite=lambda text:text):
  path=build/'connectome.lock';path.write_text(rewrite(json.dumps(data)))
  return path

 def lock_refused(self,message,data,build,**kwargs):
  with self.assertRaisesRegex(lock.LockError,'^'+re.escape(message)+'$'):lock.map_lock(copy.deepcopy(data),build,**kwargs)

 def dependency(self,name='example-package'):
  return {'name':name,'originalRange':'^1.0.0','version':'1.0.0','sha256':'e'*64,'size':1,'snapshot':'snapshots/example.tgz',
   'packageJsonDelta':{'direct':{'section':'dependencies','name':name,'before':'^1.0.0','after':'file:snapshots/example.tgz'},
    'override':{'section':'overrides','name':name,'before':None,'after':'file:snapshots/example.tgz'}}}

 def provenance(self,dependency):
  return {'name':dependency['name'],'archive':'/fixture/archives/example.tgz','sha256':dependency['sha256'],'kind':'artifact',
   'version':dependency['version'],'commit_verifiable':False,'payload':archive.ARTIFACT_PAYLOAD}

 def dependency_lock(self):
  data,build=self.lock_fixture();data['hostDependencies']=[self.dependency()]
  data['hostDependencies'][0]['provenance']=self.provenance(data['hostDependencies'][0])
  self.assertEqual(lock.map_lock(copy.deepcopy(data),build),data)
  return data,build

 def unit_lock(self,name='example.service'):
  data,build=self.lock_fixture();body=b'[Service]\nExecStart=/bin/true\n'
  (build/name).write_bytes(body)
  return data,build,{'path':name,'sha256':hashlib.sha256(body).hexdigest()}

 def recipe_lock(self):
  """A lock bound to an installed operator recipe, accepted together with its config."""
  data,build=self.lock_fixture();installed=build/'cook-recipe.json';installed.write_bytes(RECIPE)
  config={'recipe_file':{'path':'/fixture/operator/recipe.json','sha256':hashlib.sha256(RECIPE).hexdigest()}}
  data['recipe']={'path':str(installed),'sha256':config['recipe_file']['sha256']};data['recipePath']=str(installed)
  self.assertEqual(lock.map_lock(copy.deepcopy(data),build,config=config),data)
  return data,build,config,installed

 def input_refused(self,message,config,cls=ValueError):
  with self.assertRaisesRegex(cls,'^'+re.escape(message)+'$') as caught:inputs.validate(copy.deepcopy(config))
  self.assertIs(type(caught.exception),cls)

 def data_arg(self,**changes):
  record={'server':'example','command':'/fixture/bin/tool','args':['--data','/fixture/state/data'],
   'path':'/fixture/state/data','kind':'file','flag':'--data'}
  self.assertIsNone(inputs.validate({'external_data_args':[dict(record)]}))
  record.update(changes)
  return record

 def data_arg_refused(self,*records):
  with self.assertRaisesRegex(inputs.ExternalDataArgError,DATA_ARGS):inputs.validate({'external_data_args':list(records)})

 def recipe_record(self,directory,name='cook-recipe.json'):
  path=directory/name;path.write_bytes(RECIPE)
  return path,{'path':str(path),'sha256':hashlib.sha256(RECIPE).hexdigest()}

 def plugin(self,name):
  """Load a lookup plugin under a fake `ansible` package (boundary fake; Ansible is not installed)."""
  class AnsibleError(Exception):pass
  class LookupBase:pass
  fake={'ansible':types.ModuleType('ansible'),'ansible.errors':types.SimpleNamespace(AnsibleError=AnsibleError),
   'ansible.plugins':types.ModuleType('ansible.plugins'),'ansible.plugins.lookup':types.SimpleNamespace(LookupBase=LookupBase)}
  spec=importlib.util.spec_from_file_location('guard_lookup_'+name,ROOT/'oracle/lookup_plugins'/(name+'.py'))
  module=importlib.util.module_from_spec(spec)
  with patch.dict(sys.modules,fake):spec.loader.exec_module(module)
  return module.LookupModule(),AnsibleError

 # ---- connectome_lock: top level, strict JSON ------------------------
 def test_lock_L129_4(self):
  data,build=self.lock_fixture()
  self.assertEqual(lock.read_lock(self.lock_file(data,build)),data)
  del data['launch']
  with self.assertRaisesRegex(lock.LockError,'^lock missing required v2 fields$'):lock.read_lock(self.lock_file(data,build))

 def test_lock_L132_4(self):
  data,build=self.lock_fixture();data['incomplete']=True
  self.lock_refused('incomplete cook install refused',data,build)

 def test_lock_L319_12(self):
  data,build=self.lock_fixture()
  path=self.lock_file(data,build,lambda text:'{"backend": "host", '+text[1:])
  self.assertEqual(json.loads(path.read_text()),data)
  with self.assertRaisesRegex(lock.LockError,'^duplicate lock fields refused$'):lock.read_lock(path)

 def test_lock_L324_8(self):
  data,build=self.lock_fixture()
  path=self.lock_file(data,build,lambda text:text.replace('"mode": 448','"mode": NaN'))
  self.assertIn('NaN',path.read_text())
  with self.assertRaisesRegex(lock.LockError,'^nonfinite lock number refused$'):lock.read_lock(path)

 # ---- connectome_lock: host dependencies -----------------------------
 def test_lock_L246_8(self):
  data,build=self.lock_fixture();data['hostDependencies']={}
  self.lock_refused('invalid host dependency inventory',data,build)

 def test_lock_L259_12(self):
  data,build=self.dependency_lock()
  invalid=copy.deepcopy(data);invalid['hostDependencies']=[self.dependency('Example_Package')]
  self.lock_refused('invalid or duplicate host dependency name',invalid,build)
  data['hostDependencies']=[self.dependency(),self.dependency()]
  self.lock_refused('invalid or duplicate host dependency name',data,build)

 def test_lock_L266_20(self):
  data,build=self.dependency_lock()
  for field,value in (('sha256','f'*64),('version','2.0.0'),('name','other-package')):
   changed=copy.deepcopy(data);changed['hostDependencies'][0]['provenance'][field]=value
   # The record itself stays a valid dependency record; only its identity differs.
   archive.validate_dependency_record(changed['hostDependencies'][0]['provenance'])
   self.lock_refused('invalid host dependency provenance',changed,build)

 def test_lock_L269_20(self):
  data,build=self.dependency_lock()
  del data['hostDependencies'][0]['provenance']['commit_verifiable']
  self.lock_refused('invalid host dependency provenance',data,build)

 def test_lock_L271_12(self):
  data,build=self.dependency_lock()
  data['hostDependencies'][0]['packageJsonDelta']['extra']=data['hostDependencies'][0]['packageJsonDelta']['direct']
  self.lock_refused('invalid dependency delta',data,build)

 # ---- connectome_lock: units -----------------------------------------
 def test_lock_L299_8(self):
  data,build,unit=self.unit_lock()
  exported=copy.deepcopy(data);exported['launch']['userUnit']=unit['path']
  self.assertEqual(lock.map_lock(copy.deepcopy(exported),build)['units'],[unit])
  exported['units']=[unit]
  self.lock_refused('ambiguous lock unit declarations',exported,build)

 def test_lock_L301_8(self):
  data,build,unit=self.unit_lock('bad name.service')
  data['launch']['userUnit']=unit['path']
  self.lock_refused('invalid exported user unit',data,build)

 # ---- connectome_lock: operator recipe binding -----------------------
 def test_lock_operator_recipe_installed_bytes(self):
  data,build,config,installed=self.recipe_lock()
  installed.write_bytes(b'{"name": "changed"}\n')
  self.lock_refused('invalid operator recipe provenance',data,build,config=config)

 def test_lock_operator_recipe_digest(self):
  data,build,config,installed=self.recipe_lock()
  data['recipe']['sha256']='a'*64
  self.lock_refused('invalid operator recipe provenance',data,build,config=config)

 def test_lock_operator_recipe_path(self):
  data,build,config,installed=self.recipe_lock()
  data['recipe']['path']=data['recipePath']='/fixture/operator/recipe.json'
  self.lock_refused('invalid operator recipe provenance',data,build,config=config)

 def test_lock_L155_12(self):
  data,build,config,installed=self.recipe_lock()
  # Neither the digest nor the path of the lock's recipe record is the installed operator recipe.
  data['recipe']={'path':'/fixture/operator/recipe.json','sha256':'a'*64};data['recipePath']='/fixture/operator/recipe.json'
  self.lock_refused('invalid operator recipe provenance',data,build,config=config)

 # ---- connectome_inputs: validate ------------------------------------
 def test_inputs_L63_4(self):
  self.assertIsNone(inputs.validate({'activation':'install_only'}))
  self.input_refused('activation must be start or install_only',{'activation':'restart'})

 def test_inputs_L67_4(self):
  self.input_refused('exclusive_with must be a list',{'exclusive_with':{}})

 def test_inputs_L70_8(self):
  record={'scope':'user','unit':'other.service'}
  self.assertIsNone(inputs.validate({'exclusive_with':[record]}))
  self.input_refused('closed exclusive_with record required',{'exclusive_with':[dict(record,extra=True)]})

 def test_inputs_L81_8(self):
  record={'path':'/fixture/operator/recipe.json','sha256':'a'*64}
  self.assertIsNone(inputs.validate({'recipe_file':record}))
  self.input_refused('closed recipe_file record required',{'recipe_file':dict(record,extra=True)})

 def test_inputs_L91_8(self):
  self.input_refused('secret_files must be a list',{'secret_files':{}})

 def test_inputs_L94_12(self):
  record={'secret':'EXAMPLE_SECRET','env':'EXAMPLE_FILE'}
  self.assertIsNone(inputs.validate({'secret_files':[record]}))
  self.input_refused('closed secret_files record required',{'secret_files':[dict(record,mode='PRIVATE')]})

 def test_inputs_secret_file_names(self):
  for record in ({'secret':'bad-name','env':'EXAMPLE_FILE'},{'secret':'EXAMPLE_SECRET','env':'example_file'},
                 {'secret':'EXAMPLE_SECRET','env':'9FILE'}):
   self.input_refused('invalid secret file name',{'secret_files':[record]})

 def test_inputs_secret_file_duplicate_env(self):
  records=[{'secret':'EXAMPLE_SECRET','env':'EXAMPLE_FILE'},{'secret':'OTHER_SECRET','env':'OTHER_FILE'}]
  self.assertIsNone(inputs.validate({'secret_files':records}))
  records[1]['env']='EXAMPLE_FILE'
  self.input_refused('duplicate or reserved secret file environment name',{'secret_files':records})

 def test_inputs_secret_file_reserved_env(self):
  for name in ('DATA_DIR','WORKSPACE_DIR','PATH','HOME','LANG','LC_ALL','TMPDIR'):
   self.input_refused('duplicate or reserved secret file environment name',
    {'secret_files':[{'secret':'EXAMPLE_SECRET','env':name}]})

 # ---- connectome_inputs: external data arguments ---------------------
 def test_inputs_data_args_list(self):
  with self.assertRaisesRegex(inputs.ExternalDataArgError,DATA_ARGS):inputs.validate({'external_data_args':{}})

 def test_inputs_data_args_closed_keys(self):
  self.data_arg_refused(self.data_arg(extra=True))

 def test_inputs_data_args_server(self):
  for server in ('-example','.example','exa mple',''):self.data_arg_refused(self.data_arg(server=server))

 def test_inputs_data_args_args_list(self):
  self.data_arg_refused(self.data_arg(args={}))
  self.data_arg_refused(self.data_arg(args='--data'))

 def test_inputs_data_args_args_strings(self):
  self.data_arg_refused(self.data_arg(args=['--data',1]))
  self.data_arg_refused(self.data_arg(args=['--data',None]))

 def test_inputs_data_args_kind(self):
  for kind in ('socket','','files'):self.data_arg_refused(self.data_arg(kind=kind))

 def test_inputs_data_args_duplicate_server(self):
  first=self.data_arg();second=self.data_arg(path='/fixture/state/other',args=[])
  self.assertIsNone(inputs.validate({'external_data_args':[first,dict(second,server='other')]}))
  self.data_arg_refused(first,second)

 def data_path_refused(self,*values):
  for value in values:
   self.assertFalse(inputs.external_data_path(value))
   for field in ('path','command'):self.data_arg_refused(self.data_arg(**{field:value}))

 def test_inputs_data_path_absolute(self):
  self.data_path_refused('relative/data','fixture')

 def test_inputs_data_path_double_slash(self):
  self.data_path_refused('//fixture/data')

 def test_inputs_data_path_root(self):
  self.data_path_refused('/')

 def test_inputs_data_path_normal(self):
  self.data_path_refused('/fixture/./data','/fixture/data/','/fixture//data')

 def test_inputs_data_path_parent(self):
  self.data_path_refused('/fixture/../data','/fixture/data/..')

 def test_inputs_data_path_control(self):
  self.data_path_refused('/fixture/da\x7fta','/fixture/da\x1fta','/fixture/da\x9fta')

 def data_flag_refused(self,*flags):
  for flag in flags:
   self.assertFalse(inputs.external_data_flag(flag))
   self.data_arg_refused(self.data_arg(flag=flag))

 def test_inputs_data_flag_grammar(self):
  self.data_flag_refused('data','--','---data','--data=file','--9data','')

 def test_inputs_data_flag_denied(self):
  self.data_flag_refused('-e','--eval','-p','--print','-c','-r','-x','-m')

 def test_inputs_data_flag_code_loading_prefix(self):
  self.data_flag_refused('--require-module','--import-map','--loader-hook','--experimental-loader-hook')

 # ---- connectome_inputs: recipe reader and transport -----------------
 def test_inputs_L123_8(self):
  directory=self.scratch();path,record=self.recipe_record(directory)
  self.assertEqual(inputs.read_file(path,record),RECIPE)
  link=directory/'alias.json';link.symlink_to(path)
  with self.assertRaisesRegex(ValueError,'^recipe must be a regular file without symlink aliases$'):inputs.read_file(link,record)

 def test_inputs_L127_8(self):
  directory=self.scratch();path,record=self.recipe_record(directory)
  path.unlink()
  with self.assertRaisesRegex(ValueError,'^recipe input unavailable$'):inputs.read_file(path,record)

 def test_inputs_L144_4(self):
  self.assertEqual(inputs.payloads({},{}),{})
  for supplied in ({'cook-recipe.json':'e30='},{'extra':'e30='},['cook-recipe.json']):
   with self.assertRaisesRegex(ValueError,'^recipe transport inventory mismatch$'):inputs.payloads({},supplied)

 def test_inputs_transport_strict_base64(self):
  directory=self.scratch();path,record=self.recipe_record(directory);config={'recipe_file':record}
  transport=inputs.collect(config)
  self.assertEqual(inputs.payloads(config,transport),{'cook-recipe.json':RECIPE})
  encoded=transport['cook-recipe.json']
  for loose in (encoded[:4]+'\n'+encoded[4:],'!'+encoded,encoded+' '):
   with self.assertRaisesRegex(ValueError,'^invalid recipe transport$'):inputs.payloads(config,{'cook-recipe.json':loose})

 def test_inputs_L161_8(self):
  build=self.scratch();path,record=self.recipe_record(build);config={'recipe_file':dict(record,path='/fixture/operator/recipe.json')}
  path.chmod(0o600)
  self.assertIsNone(inputs.installed(build,config))
  path.chmod(0o640)
  with self.assertRaisesRegex(ValueError,'^installed recipe mode or owner mismatch$'):inputs.installed(build,config)

 # ---- lookup plugins --------------------------------------------------
 def test_lookup_file_L14_16(self):
  lookup,error=self.plugin('connectome_file');path=self.scratch()/'values.json'
  path.write_text(json.dumps({'EXAMPLE_SECRET':'synthetic','EXAMPLE_NUMBER':7}))
  self.assertEqual(lookup.run(['EXAMPLE_SECRET'],path=str(path)),['synthetic'])
  with self.assertRaisesRegex(error,r'^local secret lookup failed \(values redacted\)$'):lookup.run(['EXAMPLE_NUMBER'],path=str(path))

 def test_lookup_file_L17_12(self):
  lookup,error=self.plugin('connectome_file');path=self.scratch()/'values.json'
  path.write_text(json.dumps({'EXAMPLE_SECRET':'synthetic'}))
  with self.assertRaisesRegex(error,r'^local secret lookup failed \(values redacted\)$'):lookup.run(['ABSENT_SECRET'],path=str(path))

 def test_lookup_inputs_L18_12(self):
  lookup,error=self.plugin('connectome_inputs')
  self.assertEqual(lookup.run([{'activation':'start'}]),[{}])
  with self.assertRaisesRegex(error,r'^E_EXTERNAL_DATA_ARG: invalid external data argument input \(content redacted\)$'):
   lookup.run([{'external_data_args':'not a list'}])

 def test_lookup_inputs_L20_12(self):
  lookup,error=self.plugin('connectome_inputs')
  self.assertEqual(lookup.run([{'activation':'start'}]),[{}])
  with self.assertRaisesRegex(error,r'^operator input validation failed \(content redacted\)$'):
   lookup.run([{'activation':'restart'}])
if __name__=='__main__':unittest.main()
