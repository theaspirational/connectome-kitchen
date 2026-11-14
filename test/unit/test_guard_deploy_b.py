"""Purpose-built killing tests for the deployment engine's lock, prepare, unit and activation guards."""
import hashlib, importlib.util, json, os, shutil, stat, subprocess, sys, threading, types, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'oracle/module_utils'),str(ROOT/'oracle/tests')]
import connectome_deploy as deploy
from connectome_deploy import DeployError
from connectome_errors import SafeError
import test_deploy

UNIT='[Service]\nExecStart=/bin/true\n'
REDACTED='E_DEPLOY_FAILED: Deployment failed; previous build retained/restored where possible (output redacted).'
# Adapter double: the frozen offline cook stub, then lock fields and build files a real cook would emit.
COOK_WRAPPER='''import json,runpy,sys
from pathlib import Path
extra=json.loads(Path(sys.argv[1]).read_text());fake=sys.argv[2];out=Path(sys.argv[4]);sys.argv=[fake]+sys.argv[3:]
runpy.run_path(fake,run_name='__main__')
path=out/'connectome.lock';lock=json.loads(path.read_text());lock.update(extra['lock']);path.write_text(json.dumps(lock))
for name,content in extra['files'].items():(out/name).write_text(content)
'''

def sha(text):return hashlib.sha256(text.encode()).hexdigest()

class Guards(unittest.TestCase):
 def fixture(self):
  f=test_deploy.DeployTests('test_check_mode_has_no_writes_or_provider_calls');f.setUp();self.addCleanup(f.doCleanups);return f
 def relock(self,dep,change):
  path=dep.build/'connectome.lock';data=json.loads(path.read_text());change(data);path.write_text(json.dumps(data))
 def planned(self,dep,units):
  for name,body in units.items():(dep.build/name).write_text(body)
  self.relock(dep,lambda data:data.update(units=[{'path':name,'sha256':sha(body)} for name,body in units.items()]))
 def cook_calls(self,f):return (f.root/'cook-calls').read_text().splitlines()
 def tool_hook(self,when,action):
  # The process boundary models another actor: `action` runs once, when the manager double receives the matching call.
  real=subprocess.run;done=[]
  def run(argv,*args,**kwargs):
   if not done and when(list(argv)):done.append(True);action()
   return real(argv,*args,**kwargs)
  return patch.object(deploy.subprocess,'run',side_effect=run)
 def boundary(self,f,operation):
  # The Ansible boundary turns every refusal into a closed code; an unexpected exception becomes E_DEPLOY_FAILED.
  class Refused(Exception):pass
  outcome={}
  class Module:
   check_mode=False
   params=dict(operation=operation,root=str(f.root),unit_dir=str(f.units),config=f.config,versions=f.versions,cook_command=f.cook,systemctl=f.systemctl,secrets={})
   def exit_json(self,**kw):outcome['exit']=kw
   def fail_json(self,**kw):
    outcome['fail']=kw;raise Refused()
  fake={'ansible':types.ModuleType('ansible'),'ansible.module_utils':types.ModuleType('ansible.module_utils'),'ansible.module_utils.basic':SimpleNamespace(AnsibleModule=lambda **kw:Module()),'ansible.module_utils.connectome_deploy':deploy,'ansible.module_utils.connectome_errors':sys.modules['connectome_errors']}
  spec=importlib.util.spec_from_file_location('release_boundary',ROOT/'oracle/library/connectome_release.py');module=importlib.util.module_from_spec(spec)
  with patch.dict(sys.modules,fake):
   spec.loader.exec_module(module)
   try:module.main()
   except Refused:pass
  return outcome
 def framework(self,f,registry,sidecar=True):
  from connectome_archive import FRAMEWORK
  artifact={'path':'/fixture/framework.tgz','sha256':'a'*64}
  if registry:artifact.update(registry_release=True,name=FRAMEWORK,version='1.0.0')
  f.config['cook']={'framework_archive':artifact}
  edit={'name':FRAMEWORK,'after':'file:snapshots/framework.tgz'}
  dependency={'name':FRAMEWORK,'originalRange':'^1.0.0','version':'1.0.0','sha256':'a'*64,'size':1,'snapshot':'snapshots/framework.tgz',
   'packageJsonDelta':{'direct':dict(edit,section='dependencies',before='^1.0.0'),'override':dict(edit,section='overrides',before=None)}}
  record={'schema':'connectome.framework-archive/v1','sha256':'a'*64,'name':FRAMEWORK,'version':'1.0.0','requested_commit':f.versions['runtime']['agent-framework'],'registry_release':registry,'commit_verifiable':not registry}
  wrapper=Path(f.tmp.name)/'cook-wrapper.py';wrapper.write_text(COOK_WRAPPER)
  extra=Path(f.tmp.name)/'cook-extra.json';extra.write_text(json.dumps({'lock':{'hostDependencies':[dependency]},'files':{'framework.lock.json':json.dumps(record)} if sidecar else {}}))
  f.cook=[sys.executable,str(wrapper),str(extra),f.cook[1],'{recipe}','{build_dir}','{state_dir}']
  return record

 # ---- _lock ----
 def test_deploy_L705_12(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  self.relock(dep,lambda data:data.update(version=1))
  self.assertEqual(self.boundary(f,'activate'),{'fail':{'invocation':{},'exception':None,'error_code':'E_LOCK_INVALID','msg':'E_LOCK_INVALID: Cook lock validation failed.'}})
  self.assertFalse(dep.current.exists())
 def test_deploy_L713_12(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  self.relock(dep,lambda data:data.pop('state'))
  with self.assertRaisesRegex(DeployError,'^cook lock must declare configured external state_dir$') as caught:dep.activate({})
  self.assertEqual(caught.exception.code,'E_LOCK_STATE_PATH');self.assertFalse(dep.current.exists())
 def retained(self,f):
  first=f.deployment();f.apply(first)
  second=f.deployment(dict(f.config,description='second request'));second.prepare()
  return first,second
 def test_deploy_L721_16(self):
  # A retained build is judged by its own recorded state path, which the constructor never saw.
  f=self.fixture();first,second=self.retained(f);inner=str(f.root/'inner-state')
  recorded=json.loads((first.build/'deployment.json').read_text());recorded['config']['state_dir']=inner
  (first.build/'deployment.json').write_text(json.dumps(recorded));self.relock(first,lambda data:data['state'].update(path=inner))
  with self.assertRaisesRegex(DeployError,'^state must be outside deployment root/builds$') as caught:second.activate({})
  self.assertEqual(caught.exception.code,'E_LOCK_STATE_PATH');self.assertEqual(second.current.resolve(),first.build)
 def test_deploy_L733_16_and_retained_sidecar(self):
  f=self.fixture();self.framework(f,registry=True,sidecar=False);dep=f.deployment()
  with self.assertRaisesRegex(SafeError,'^framework provenance lock missing or malformed$') as caught:dep.prepare()
  self.assertIs(type(caught.exception),DeployError);self.assertEqual(caught.exception.code,'E_LOCK_INVALID')
  self.assertFalse((dep.build/'.prepared').exists())
 def test_deploy_L738_16(self):
  f=self.fixture();record=self.framework(f,registry=False);dep=f.deployment();dep.prepare()
  sidecar=dep.build/'framework.lock.json';real=deploy.read_lock
  def reread(*args,**kwargs):
   # Another actor rewrites the sidecar between the engine's two reads; the new record is valid on its own.
   data=real(*args,**kwargs);sidecar.write_text(json.dumps(dict(record,version='2.0.0')));return data
  with patch.object(deploy,'read_lock',side_effect=reread):
   with self.assertRaisesRegex(DeployError,'^framework provenance lock mismatch$') as caught:dep._lock()
  self.assertEqual(caught.exception.code,'E_LOCK_INVALID')
 def test_retained_recipe(self):
  f=self.fixture();content=b'{"name": "example"}\n';path=Path(f.tmp.name)/'operator.json';path.write_bytes(content)
  f.config['recipe_file']={'path':str(path),'sha256':hashlib.sha256(content).hexdigest()}
  first,second=self.retained(f);(first.build/'cook-recipe.json').chmod(0o644)
  with self.assertRaisesRegex(DeployError,'^installed recipe mode or owner mismatch$') as caught:second.activate({})
  self.assertEqual(caught.exception.code,'E_LOCK_INVALID');self.assertEqual(second.current.resolve(),first.build)
 def test_retained_config(self):
  f=self.fixture();first,second=self.retained(f)
  recorded=json.loads((first.build/'deployment.json').read_text());recorded['config']['connectors']['Bad_Connector']={}
  (first.build/'deployment.json').write_text(json.dumps(recorded))
  with self.assertRaisesRegex(DeployError,'^invalid agent or connector identifier$'):second.activate({})
  self.assertEqual(second.current.resolve(),first.build);self.assertFalse((second.build/'secrets.env').exists())
 def test_retained_previous_first(self):
  f=self.fixture();first,second=self.retained(f)
  self.relock(first,lambda data:data.update(unsupported=True))
  with self.assertRaisesRegex(DeployError,'^lock contains unsupported fields$') as caught:second.activate({})
  self.assertEqual(caught.exception.code,'E_LOCK_INVALID')
  self.assertEqual(second.current.resolve(),first.build);self.assertFalse((second.build/'secrets.env').exists())

 # ---- prepare ----
 def test_prepare_marker(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  (dep.build/'.prepared').write_text('f'*64);(dep.build/'stale-output').write_text('from another request')
  self.assertTrue(f.deployment().prepare()['changed'])
  self.assertEqual(len(self.cook_calls(f)),2);self.assertFalse((dep.build/'stale-output').exists())
  self.assertEqual((dep.build/'.prepared').read_text(),dep.request_id)
 def test_deploy_L766_12(self):
  f=self.fixture();dep=deploy.Deployment(f.root,f.units,f.config,f.versions,f.cook,f.systemctl,operator_files={'unexpected.json':'e30='})
  with self.assertRaisesRegex(DeployError,'^recipe transport inventory mismatch$'):dep.prepare()
  self.assertFalse(f.root.exists())
 def test_deploy_L770_12_and_no_adapter(self):
  f=self.fixture();f.cook=[];dep=f.deployment();calls=[]
  def run(argv,**kwargs):
   calls.append(list(argv));return SimpleNamespace(returncode=0,stdout=b'')
  with patch.object(deploy.subprocess,'run',side_effect=run):
   with self.assertRaisesRegex(DeployError,'^configure a pinned cook adapter command$'):dep.prepare()
  self.assertEqual(calls,[]);self.assertFalse(f.root.exists())
 def test_deploy_L774_12_and_prepare_active(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  # The pointer is already recorded and switched, but no unit file is owned inside the build yet.
  dep.current.symlink_to(dep.build);manifest=json.loads(dep.manifest_path.read_text())
  manifest['entries'].append({'path':str(dep.current),'kind':'link','target':str(dep.build)});dep.manifest_path.write_text(json.dumps(manifest))
  (dep.build/'.prepared').unlink()
  with self.assertRaisesRegex(DeployError,'^refusing to rebuild active directory$'):f.deployment().prepare()
  self.assertEqual(len(self.cook_calls(f)),1);self.assertTrue((dep.build/'run.sh').is_file())
 def test_deploy_L870_12(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  target=dep.build/'operator-input.json';target.write_bytes(b'existing');real=os.mkdir
  def mkdir(path,*args,**kwargs):
   # Another actor removes the destination after the engine looked at it, so only the engine's own
   # check (not the exclusive create behind it) can refuse the destination that existed.
   if Path(path)==dep.build:target.unlink(missing_ok=True)
   return real(path,*args,**kwargs)
  with patch.object(os,'mkdir',side_effect=mkdir):
   with self.assertRaisesRegex(DeployError,'^operator input destination already exists$'):dep._write_input(target,b'replacement',0o600)
  self.assertEqual(target.read_bytes(),b'existing')

 # ---- ownership manifest ----
 def test_deploy_L1012_12(self):
  f=self.fixture();dep=f.deployment();dep.state.mkdir(parents=True)
  dep.manifest_path.symlink_to(Path(f.tmp.name)/'elsewhere.json')
  with self.assertRaisesRegex(DeployError,'^refusing symlinked ownership manifest$'):dep.prepare()
  self.assertTrue(dep.manifest_path.is_symlink());self.assertFalse(dep.builds.exists())
 def test_deploy_L1016_12(self):
  f=self.fixture();dep=f.deployment();dep.state.mkdir(parents=True);os.mkfifo(dep.manifest_path)
  def feed():
   # Blocks until a reader opens the pipe; a reader then sees a well-formed empty manifest.
   with open(dep.manifest_path,'w') as stream:stream.write('{"version":1,"entries":[]}')
  writer=threading.Thread(target=feed,daemon=True);writer.start()
  def release():
   reader=os.open(dep.manifest_path,os.O_RDONLY|os.O_NONBLOCK);writer.join(5);os.close(reader)
  self.addCleanup(release)
  with self.assertRaisesRegex(DeployError,'^invalid ownership manifest; regular file required$'):dep.prepare()
  self.assertTrue(stat.S_ISFIFO(dep.manifest_path.lstat().st_mode));self.assertFalse(dep.builds.exists())
 def test_deploy_L1021_16(self):
  f=self.fixture();dep=f.deployment();dep.state.mkdir(parents=True);dep.manifest_path.write_text('{"version":2,"entries":[]}')
  with self.assertRaisesRegex(DeployError,'^invalid ownership manifest; operator recovery required$'):dep.prepare()
  self.assertEqual(dep.manifest_path.read_text(),'{"version":2,"entries":[]}');self.assertFalse(dep.builds.exists())
 def test_deploy_L1036_20(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  outside=Path(f.tmp.name)/'outside.conf';outside.write_text('not a deployment artifact')
  manifest=json.loads(dep.manifest_path.read_text());manifest['entries'].append({'path':str(outside),'kind':'file','sha256':sha('not a deployment artifact')})
  dep.manifest_path.write_text(json.dumps(manifest))
  with self.assertRaisesRegex(DeployError,'^invalid ownership manifest; operator recovery required$'):f.deployment().activate({})
  self.assertFalse(dep.current.exists())
 def test_rollback_refuses_unowned_pointer(self):
  # Not mapped as a kill of deploy.L1075.12: under that mutant activate() raises the identical
  # ActivationFailed and only the suppressed context below changes (KeyError instead of the refusal).
  f=self.fixture();dep=f.deployment();dep.prepare()
  f.root.chmod(0o500);self.addCleanup(f.root.chmod,0o700)  # the release pointer cannot be created
  with self.assertRaises(DeployError) as caught:dep.activate({})
  error=caught.exception
  self.assertEqual(str(error),'activation failed at release pointer: unexpected activation exception (details redacted); rollback recovery also failed')
  # Recovery stopped at the declared refusal to remove a pointer the manifest does not own.
  self.assertIs(type(error.__context__),DeployError);self.assertEqual(str(error.__context__),'refusing to remove unmanaged or modified artifact')
  self.assertFalse(dep.current.is_symlink())
 def test_deploy_L1081_12(self):
  f=self.fixture();dep=f.deployment();f.apply(dep)
  dropin=f.units/'connectome-example.service.d/90-connectome-env.conf';dropin.write_text('changed by another actor')
  with self.assertRaisesRegex(DeployError,'^refusing to remove unmanaged or modified artifact$'):dep._remove(dropin)
  self.assertEqual(dropin.read_text(),'changed by another actor');self.assertIn(str(dropin),dep.manifest_path.read_text())

 # ---- units ----
 def test_deploy_L828_16_and_launcher_kind(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  with self.assertRaisesRegex(DeployError,'^host script launcher required$') as caught:dep._units(dep.build,{'launch':{'kind':'binary','script':'run.sh'}})
  self.assertEqual(caught.exception.code,'E_DEPLOY_FAILED')
 def test_deploy_L831_16_and_launcher_file(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  with self.assertRaisesRegex(DeployError,'^missing launcher$'):dep._units(dep.build,{'launch':{'kind':'script','script':'absent.sh'}})
 def test_deploy_L845_16_and_units_name(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  (dep.build/'example.service').write_text(UNIT);entry={'path':'example.service','sha256':sha(UNIT)}
  self.relock(dep,lambda data:data.update(units=[entry,entry]))
  with self.assertRaisesRegex(DeployError,'^invalid or duplicate user unit$'):dep.activate({})
  self.planned(dep,{'not literal.service':UNIT})
  with self.assertRaisesRegex(DeployError,'^invalid or duplicate user unit$'):dep.activate({})
  self.assertFalse(dep.current.exists());self.assertFalse(f.units.exists())
 def test_deploy_L847_16_and_units_digest(self):
  f=self.fixture();dep=f.deployment();dep.prepare();(dep.build/'example.service').write_text(UNIT)
  with self.assertRaisesRegex(DeployError,'^unit output digest mismatch$') as caught:dep._units(dep.build,{'units':[{'path':'example.service','sha256':sha('other bytes')}]})
  self.assertEqual(caught.exception.code,'E_DEPLOY_FAILED')
 def test_units_own_copy_location(self):
  f=self.fixture();dep=f.deployment();dep.prepare();self.planned(dep,{'example.service':UNIT})
  installed=dep.build/'managed-units/example.service'
  self.assertEqual(dep._units(dep.build,dep._lock()),{'example.service':installed})
  self.assertTrue(dep.activate({})['changed'])
  self.assertEqual((f.units/'example.service').resolve(),installed);self.assertEqual(installed.read_text(),UNIT)
  self.assertIn({'path':str(installed),'kind':'file','sha256':sha(UNIT)},json.loads(dep.manifest_path.read_text())['entries'])
 def test_units_own_copy_content(self):
  # A file already sitting at the role's own install path is never adopted as the installed unit.
  f=self.fixture();dep=f.deployment();dep.prepare();self.planned(dep,{'example.service':UNIT})
  installed=dep.build/'managed-units/example.service';installed.parent.mkdir();installed.write_text('[Service]\nExecStart=/bin/false\n')
  with self.assertRaisesRegex(DeployError,'^refusing unmanaged artifact; no matching ownership manifest entry$'):dep.activate({})
  self.assertFalse((f.units/'example.service').is_symlink());self.assertFalse(dep.current.exists())
 def test_deploy_L854_12_and_units_empty(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  with self.assertRaisesRegex(DeployError,'^no units to activate$'):dep._units(dep.build,{'units':[]})
 def test_deploy_L973_12_and_unit_grammar(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  self.planned(dep,{'example.service':UNIT+'[Install]\nAlias=example-alias.target\n'})
  with self.assertRaisesRegex(DeployError,'^invalid user unit$'):dep.activate({})
  self.assertFalse(f.units.exists());self.assertFalse(dep.current.exists())
 def test_deploy_L1114_20(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  self.planned(dep,{'example.service':UNIT+'[Install]\nWantedBy=multi.user.target\n'})
  with self.assertRaisesRegex(DeployError,'^unsupported unit Install target or alias$'):dep.activate({})
  self.assertFalse(f.units.exists());self.assertFalse(dep.current.exists())
 def test_deploy_L1146_20_and_units_conflict(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  self.planned(dep,{'first.service':UNIT+'[Install]\nAlias=second.service\n','second.service':UNIT})
  with self.assertRaisesRegex(DeployError,'^conflicting unit installation paths$'):dep.activate({})
  self.assertFalse(f.units.exists());self.assertFalse(dep.current.exists())

 # ---- activation ----
 def test_deploy_L1197_16(self):
  f=self.fixture();dep=f.deployment();dep.prepare()
  self.relock(dep,lambda data:data.update(external=[{'name':'example-server','command':'/fixture/bin/server'}]))
  refusal=lambda code:{'fail':{'invocation':{},'exception':None,'error_code':code,'msg':code+': '+sys.modules['connectome_errors'].MESSAGES[code]}}
  self.assertEqual(self.boundary(f,'activate'),refusal('E_LOCK_INVALID'))
  # The lock itself is valid: with the recipe installed the same request reaches the server path checks.
  (dep.build/'cook-recipe.json').write_text(json.dumps({'mcpServers':{'example-server':{'source':{'external':True},'command':'/fixture/bin/server'}}}))
  expected=refusal('E_EXTERNAL_COMMAND');expected['fail']['external_server']='example-server'
  self.assertEqual(self.boundary(f,'activate'),expected)
 def test_deploy_L1229_12(self):
  f=self.fixture();f.config['required_secrets']=['EXAMPLE_SECRET'];dep=f.deployment(check=True)
  with self.assertRaisesRegex(DeployError,'^secret provider must return exactly the declared names$'):dep.activate({'EXAMPLE_SECRET':['synthetic']})
 def test_activate_preflight(self):
  f=self.fixture();dep=f.deployment();dep.prepare();f.units.mkdir()
  unit=f.units/'connectome-example.service';unit.write_text('unmanaged')
  with self.assertRaisesRegex(DeployError,'^refusing unmanaged artifact; no matching ownership manifest entry$'):dep.activate({})
  self.assertFalse((dep.build/'secrets.env').exists());self.assertFalse(dep.current.exists());self.assertEqual(unit.read_text(),'unmanaged')
  self.assertFalse((f.root/'systemctl-calls').exists())
 def exclusive(self,f):
  f.track_units();f.config['exclusive_with']=[{'scope':'system','unit':'old.service'}]
  dep=f.deployment();dep.prepare();return dep
 def test_activate_exclusive_recheck_before_install(self):
  f=self.fixture();dep=self.exclusive(f);real=subprocess.run;asked=[]
  def run(argv,*args,**kwargs):
   result=real(argv,*args,**kwargs)
   # The other unit starts right after the entry preflight has seen it inactive.
   if 'is-active' in argv and '--system' in argv and not asked:
    asked.append(True);f.track_units({'system:old.service':'active'})
   return result
  with patch.object(deploy.subprocess,'run',side_effect=run):
   with self.assertRaises(DeployError) as caught:dep.activate({})
  self.assertEqual(caught.exception.__cause__.code,'E_UNIT_EXCLUSIVE')
  self.assertEqual([call for call in f.lifecycle_calls() if call[0] in ('daemon-reload','restart')],[])
  self.assertFalse(f.units.exists())
 def test_activate_exclusive_recheck_before_restart(self):
  f=self.fixture();dep=self.exclusive(f)
  # The other unit starts while this agent's units are being installed and reloaded.
  with self.tool_hook(lambda argv:argv[-1]=='daemon-reload',lambda:f.track_units({'system:old.service':'active'})):
   with self.assertRaises(DeployError) as caught:dep.activate({})
  self.assertEqual(caught.exception.__cause__.code,'E_UNIT_EXCLUSIVE')
  self.assertIn(['daemon-reload'],f.lifecycle_calls());self.assertNotIn(['restart','connectome-example.service'],f.lifecycle_calls())
 def staged(self,f):
  f.track_units();f.config['activation']='install_only';f.analyze=[]
  dep=f.deployment();f.apply(dep);lock=dep._lock();return dep,dep._units(dep.build,lock),lock
 def test_deploy_L901_16(self):
  f=self.fixture();dep,units,lock=self.staged(f);dep.current.unlink()
  with self.assertRaisesRegex(DeployError,'^current does not point at installed release$'):dep._health(units,lock,dep.build)
 def test_deploy_L904_20(self):
  f=self.fixture();dep,units,lock=self.staged(f);(f.units/'connectome-example.service').unlink()
  with self.assertRaisesRegex(DeployError,'^installed unit file missing$'):dep._health(units,lock,dep.build)
 def test_activate_install_only_health(self):
  f=self.fixture();f.config['activation']='install_only'
  f.analyze=[sys.executable,'-c','import sys; sys.exit(7)'];dep=f.deployment();dep.prepare()
  with self.assertRaises(DeployError) as caught:dep.activate({})
  self.assertEqual(caught.exception.code,'E_UNIT_VERIFY')
  self.assertEqual(str(caught.exception),'activation failed at health: systemd-analyze exited 7; previous deployment restored')
  calls=f.lifecycle_calls()
  self.assertIn(['show','-p','EnvironmentFiles','--value','connectome-example.service'],calls)
  self.assertFalse(any(call[0] in ('is-active','restart') for call in calls),calls);self.assertFalse(dep.current.exists())
 def test_deploy_L889_12(self):
  f=self.fixture();f.config['required_secrets']=['EXAMPLE_SECRET'];dep=f.deployment();dep.prepare();real=subprocess.run
  def run(argv,*args,**kwargs):
   if 'EnvironmentFiles' in argv:raise OSError('manager unavailable')
   return real(argv,*args,**kwargs)
  with patch.object(deploy.subprocess,'run',side_effect=run):
   with self.assertRaises(DeployError) as caught:dep.activate(f.provider.get(dep.names))
  error=caught.exception
  self.assertEqual(error.code,'E_ENVIRONMENT_FILE')
  self.assertEqual(str(error),'activation failed at health: E_ENVIRONMENT_FILE: Required unit environment file was not accepted by the user service manager.; previous deployment restored')
  self.assertIs(type(error.__context__),DeployError);self.assertEqual(str(error.__context__),'cannot inspect unit environment file')
 def test_deploy_L922_16_and_health_argv(self):
  f=self.fixture();true=shutil.which('true');self.assertIsNotNone(true)
  f.config['health']={'command':true};dep=f.deployment();dep.prepare()
  with self.assertRaises(DeployError) as caught:dep.activate({})
  self.assertEqual(str(caught.exception),'activation failed at health: '+REDACTED+'; previous deployment restored')
  self.assertEqual(str(caught.exception.__context__),'health command must be argv');self.assertFalse(dep.current.exists())
 # ---- catalog mode ----
 def catalog(self,configure=None):
  # The frozen catalog test's role wiring: synthetic archives, descriptors and a synthetic cook entrypoint.
  import test_catalog
  c=test_catalog.CatalogTests('test_role_prepare_adapter_build_lock_reuse_and_preflight_dropin');c.setUp();self.addCleanup(c.doCleanups)
  oracle=ROOT/'oracle';secrets=c.work/'secrets';secrets.mkdir(mode=0o700)
  path=secrets/'example.json';path.write_text('{}');path.chmod(0o600);c.config['secrets_file']=str(path)
  if configure:configure(c.config)
  entrypoint=c.root/'inputs/cook/bin/cook';entrypoint.parent.mkdir(parents=True);Path(c.config['cook']['host_repo']).mkdir()
  entrypoint.write_bytes((oracle/'tests/fixtures/catalog-cook.py').read_bytes())
  command=[sys.executable,str(oracle/'scripts/cook-adapter.py'),'--spec','{recipe}','--out','{build_dir}','--state','{state_dir}',
   '--entrypoint',str(entrypoint),'--runtime',sys.executable,'--bun-path',sys.executable,'--node-path',sys.executable]
  dep=deploy.Deployment(c.root,c.work/'units',c.config,c.versions,command,[sys.executable,str(oracle/'tests/fixtures/systemctl.py'),str(c.root)])
  self.addCleanup(lambda:dep.build.exists() and c.thaw(dep.build))
  return c,dep
 def adapter(self,before=None):
  # As in the frozen test: the adapter runs in-process without the cook checkout check; every other command is the real `run`.
  import connectome_cook as cook
  real=deploy.run
  def invoke(argv,**kwargs):
   if before:before(list(argv))
   if not kwargs.get('adapter'):return real(argv,**kwargs)
   options={argv[i]:argv[i+1] for i in range(2,len(argv),2)}
   with patch.object(cook,'verify_cook'):cook.adapt(options['--spec'],options['--out'],options['--state'],options['--runtime'],options['--entrypoint'],options['--bun-path'],options['--node-path'])
  return patch.object(deploy,'run',side_effect=invoke)
 def manager_calls(self,c):return [json.loads(line) for line in (c.root/'systemctl-calls').read_text().splitlines()]
 def test_adapter_env(self):
  c,dep=self.catalog();children=[]
  def child(argv,**kwargs):
   children.append(kwargs.get('env'));return SimpleNamespace(returncode=1,stdout=None)
  with patch.dict(os.environ,{'OPERATOR_ONLY':'private'}),patch.object(deploy.subprocess,'run',side_effect=child):
   with self.assertRaises(DeployError) as caught:dep.prepare()
  self.assertEqual(caught.exception.code,'E_COOK_FAILED')
  self.assertEqual(children,[{'PATH':'/usr/bin:/bin','HOME':str(dep.build),'LANG':'C.UTF-8','NO_UPDATE_NOTIFIER':'1'}])
 def test_activate_reverify(self):
  c,dep=self.catalog();done=[]
  def rewrite(argv):
   # Another actor rewrites an installed catalog file while this agent's units are being reloaded.
   if argv[-1]=='daemon-reload' and not done:
    done.append(True);c.thaw(dep.build);(dep.build/'catalog/extra/server.py').write_text('print(43)\n')
  with self.adapter(rewrite):
   dep.prepare()
   with self.assertRaises(DeployError) as caught:dep.activate({})
  self.assertEqual(caught.exception.code,'E_CATALOG_INVALID')
  self.assertEqual(str(caught.exception),'activation failed at unit installation: E_CATALOG_INVALID: Catalog pin, installed closure or launch path validation failed.; previous deployment restored')
  self.assertEqual([call for call in self.manager_calls(c) if call[0]=='restart'],[]);self.assertFalse(dep.current.exists())
 def test_activate_health_env(self):
  c,dep=self.catalog(lambda config:config.update(health={'command':['/bin/sh','run.sh']}));real=subprocess.run;children=[]
  def child(argv,*args,**kwargs):
   if list(argv)==['/bin/sh','run.sh']:children.append((kwargs.get('cwd'),kwargs.get('env')))
   return real(argv,*args,**kwargs)
  with patch.dict(os.environ,{'OPERATOR_ONLY':'private'}),self.adapter(),patch.object(deploy.subprocess,'run',side_effect=child):
   dep.prepare();self.assertTrue(dep.activate({})['changed'])
  build=dep.build
  self.assertEqual(children,[(build,{'PATH':os.pathsep.join(str(build/part) for part in ('catalog/bun/bin','catalog/node/bin','platform-bin')),
   'HOME':str(build/'catalog-home'),'TMPDIR':str(build/'catalog-tmp'),'LANG':'C.UTF-8','NO_UPDATE_NOTIFIER':'1',
   'npm_config_offline':'true','npm_config_cache':str(build/'catalog-cache'),'BUN_INSTALL_CACHE_DIR':str(build/'catalog-cache')})])
if __name__=='__main__':unittest.main()
