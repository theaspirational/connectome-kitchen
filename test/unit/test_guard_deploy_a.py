"""Killing tests for the first part of the deployment engine (connectome_deploy.py lines 1-700)."""
import copy, hashlib, importlib.util, json, os, stat, sys, types, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'oracle/module_utils'),str(ROOT/'oracle/tests')]
import connectome_deploy as deploy
import connectome_errors as errors
from connectome_archive import ArchiveError, FRAMEWORK
from connectome_deploy import DeployError, ExternalCommandUnsafe, UnitVerificationFailed
from deploy_fixtures import private_scratch
import test_deploy

UNSAFE='^unsafe external server path: '
STATE='^state_dir must be a real directory, mode 0700, owned by the agent account; not repaired$'
PIN_A,PIN_B='a'*40,'b'*40
# A cook double for framework-archive requests: the frozen fixture cook, plus the
# framework host dependency and the role sidecar that the real adapter records.
COOK=r'''
import json, runpy, sys
from pathlib import Path
fixture, recipe, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
sys.argv = sys.argv[1:]
runpy.run_path(fixture, run_name='__main__')
spec = json.loads(recipe.read_text())
artifact = spec['config']['cook']['framework_archive']
name, snapshot = 'FRAMEWORK', 'file:/fixture/snapshots/framework.tgz'
lock = json.loads((out / 'connectome.lock').read_text())
lock['hostDependencies'] = [{'name': name, 'sha256': artifact['sha256'], 'originalRange': '^1.0.0',
    'version': '1.0.0', 'size': 123, 'snapshot': '/fixture/snapshots/framework.tgz',
    'packageJsonDelta': {'direct': {'section': 'dependencies', 'name': name, 'before': '^1.0.0', 'after': snapshot},
                         'override': {'section': 'overrides', 'name': name, 'before': None, 'after': snapshot}}}]
(out / 'connectome.lock').write_text(json.dumps(lock))
(out / 'framework.lock.json').write_text(json.dumps({'schema': 'connectome.framework-archive/v1',
    'sha256': artifact['sha256'], 'name': name, 'version': '1.0.0',
    'requested_commit': spec['pins']['runtime']['agent-framework'],
    'registry_release': False, 'commit_verifiable': True}))
'''


class Refused(Exception):
 """Raised by the fake Ansible module so the boundary's closed result can be asserted."""


class Guards(unittest.TestCase):
 # ---- fixtures -------------------------------------------------------
 def fixture(self):
  """The frozen deployment fixture: private scratch, fake cook/systemctl/analyze commands."""
  fixture=test_deploy.DeployTests('test_install_only_static_health_and_status');fixture.setUp();self.addCleanup(fixture.doCleanups)
  return fixture

 def lstat_as(self,target,**changes):
  """Change single lstat facts of one path (as test_external's metadata seam does); everything else stays real."""
  original=Path.lstat;index={'st_mode':0,'st_uid':4,'st_gid':5}
  def metadata(path,*args,**kwargs):
   info=original(path,*args,**kwargs)
   if path==target:
    fields=list(info)
    for key,value in changes.items():fields[index[key]]=value
    return os.stat_result(fields)
   return info
  return patch.object(Path,'lstat',metadata)

 def root_owned(self,work,tree):
  """Model only root ownership (uid 0) of the scratch directory and one subtree, as fakeroot would.

  Kinds, modes, links and missing paths stay real; the group stays the agent's, so its group bits apply."""
  original=Path.lstat
  def metadata(path,*args,**kwargs):
   info=original(path,*args,**kwargs)
   if path==work:
    fields=list(info);fields[0]=stat.S_IFDIR|0o755;fields[4]=fields[5]=0
    return os.stat_result(fields)
   if path==tree or tree in path.parents:
    fields=list(info);fields[4]=0
    return os.stat_result(fields)
   return info
  boundary=patch.object(Path,'lstat',metadata);boundary.start();self.addCleanup(boundary.stop)

 def tree(self):
  """work/opt/bin/server: a real 0755 file below real 0755 directories, all modelled as root-owned."""
  old=os.umask(0o022);self.addCleanup(os.umask,old)
  work=Path(private_scratch(self).name).resolve();directory=work/'opt/bin';directory.mkdir(parents=True)
  server=directory/'server';server.write_text('#!/bin/sh\nexit 0\n');server.chmod(0o755)
  self.root_owned(work,work/'opt')
  self.assertEqual(deploy.check_external_command('alpha',str(server)),server)
  return directory,server

 def unsafe(self,reason,command,code='E_EXTERNAL_COMMAND'):
  with self.assertRaisesRegex(ExternalCommandUnsafe,UNSAFE+reason+'$') as error:deploy.check_external_command('alpha',str(command))
  self.assertEqual(error.exception.code,code)
  self.assertEqual(error.exception.public()['external_server'],'alpha')

 def framework_fixture(self):
  """A prepared legacy build whose request pins the framework archive at PIN_A."""
  from test_cook import framework_archive
  fixture=self.fixture();tmp=Path(fixture.tmp.name)
  wrapper=tmp/'framework-cook.py';wrapper.write_text(COOK.replace('FRAMEWORK',FRAMEWORK))
  fixture.cook=[sys.executable,str(wrapper),str(ROOT/'oracle/tests/fixtures/cook.py'),'{recipe}','{build_dir}','{state_dir}']
  fixture.config['cook']={'framework_archive':framework_archive(tmp/'framework.tgz',PIN_A)}
  fixture.versions['runtime']['agent-framework']=PIN_A
  first=fixture.deployment();plan=first.prepare()
  self.assertEqual(plan['plan']['framework_archive']['requested_commit'],PIN_A)
  return fixture,first

 # ---- check_external_command (row 33) -----------------------------------
 def test_deploy_L134_12(self):
  # The missing path makes the mutated walk raise UnboundLocalError, so the
  # closed code is asserted at the role boundary, which maps every exception.
  fixture=self.fixture();work=Path(fixture.tmp.name).resolve();(work/'opt').mkdir()
  self.root_owned(work,work/'opt');missing=work/'opt/missing-server'
  content=json.dumps({'name':'example','agent':{'systemPrompt':'Verbatim'},'mcpServers':{'alpha':{'command':str(missing),'args':[],'source':{'external':True}}}}).encode()
  recipe=work/'operator.json';recipe.write_bytes(content)
  fixture.config['recipe_file']={'path':str(recipe),'sha256':hashlib.sha256(content).hexdigest()};fixture.versions.pop('connectors',None)
  class Module:
   check_mode=False
   params={'operation':'prepare','secrets':None,'base':None,'root':str(fixture.root),'unit_dir':str(fixture.units),'config':fixture.config,
    'operator_files':None,'versions':fixture.versions,'cook_command':fixture.cook,'systemctl':fixture.systemctl}
   def exit_json(self,**result):raise Refused({'exit':result})
   def fail_json(self,**result):raise Refused(result)
  fake={'ansible':types.ModuleType('ansible'),'ansible.module_utils':types.ModuleType('ansible.module_utils'),
   'ansible.module_utils.basic':SimpleNamespace(AnsibleModule=lambda **kw:Module()),
   'ansible.module_utils.connectome_deploy':deploy,'ansible.module_utils.connectome_errors':errors}
  spec=importlib.util.spec_from_file_location('role_boundary_deploy_a',ROOT/'oracle/library/connectome_release.py');module=importlib.util.module_from_spec(spec)
  with patch.dict(sys.modules,fake):spec.loader.exec_module(module)
  with self.assertRaises(Refused) as refusal:module.main()
  self.assertEqual(refusal.exception.args[0],dict(errors.diagnostic('E_EXTERNAL_COMMAND'),invocation={},exception=None,external_server='alpha'))
  self.assertTrue((fixture.root/'cook-calls').is_file());self.assertFalse(any(fixture.root.glob('builds/*/.prepared')))
  self.unsafe('missing or unreadable path',missing)

 def test_deploy_L141_12(self):
  directory,server=self.tree()
  self.unsafe('not a regular file',directory)
  self.unsafe('not a directory',server/'child')

 def test_deploy_L143_12(self):
  directory,server=self.tree();server.chmod(0o775)
  self.unsafe('group/other-writable',server)

 def test_deploy_L145_12(self):
  # root:<agent group> 0744 passes ownership and writability, but the agent cannot execute it.
  directory,server=self.tree();server.chmod(0o744)
  self.unsafe('not readable/executable by the agent account',server,'E_EXTERNAL_ACCESS')

 def test_deploy_L166_16(self):
  # A finite chain of 41 links ends at a safe command: only the link budget refuses it.
  directory,server=self.tree()
  for index in range(41):(directory/('link'+str(index))).symlink_to('link'+str(index+1) if index<40 else 'server')
  self.assertEqual(deploy.check_external_command('alpha',str(directory/'link1')),server)
  self.unsafe('too many symbolic links',directory/'link0')

 def test_deploy_L172_16(self):
  # A final link to "." leaves nothing to inspect: it names the directory, not a command.
  directory,server=self.tree();(directory/'self').symlink_to('.')
  self.unsafe('not a regular file',directory/'self')

 def test_deploy_L176_8(self):
  self.tree()
  self.unsafe('not a regular file','/')

 # ---- external_paths, make_directory, run --------------------------------
 def test_deploy_L276_16(self):
  lock={'external':[{'name':'alpha','command':'/fixture/bin/server'}]}
  def recipe(*args):return {'mcpServers':{'alpha':{'command':'/fixture/bin/server','args':list(args),'source':{'external':True}}}}
  self.assertEqual(deploy.external_paths(lock,recipe('--root=/fixture/data','serve')),[('alpha','/fixture/bin/server',False,False),('alpha','/fixture/data',True,False)])
  for argument in ('./server','--script=lib/index.js'):
   with self.assertRaisesRegex(ExternalCommandUnsafe,UNSAFE+'relative path argument$') as error:deploy.external_paths(lock,recipe(argument))
   self.assertEqual(error.exception.public()['external_server'],'alpha')

 def test_deploy_L303_12(self):
  work=Path(private_scratch(self).name);occupied=work/'occupied';occupied.write_text('not a directory')
  with self.assertRaisesRegex(DeployError,'^refusing non-directory or symlinked directory$'):deploy.make_directory(occupied)
  self.assertEqual(occupied.read_text(),'not a directory')
  real=work/'real';real.mkdir();alias=work/'alias';alias.symlink_to(real,target_is_directory=True)
  with self.assertRaisesRegex(DeployError,'^refusing non-directory or symlinked directory$'):deploy.make_directory(alias)

 def test_deploy_L389_16(self):
  missing=Path(private_scratch(self).name)/'missing-analyze'
  with self.assertRaises(DeployError) as error:deploy.run([str(missing),'--user','verify'],unit_verify=True)
  self.assertIsInstance(error.exception,UnitVerificationFailed)
  self.assertEqual(str(error.exception),'systemd-analyze unavailable');self.assertEqual(error.exception.code,'E_UNIT_VERIFY')

 def test_deploy_L390_12(self):
  # run() cannot continue without a result under the mutant; activation's
  # closed diagnostic shows whether the declared refusal or a crash was the cause.
  fixture=self.fixture();fixture.config['health']={'command':[str(Path(fixture.tmp.name)/'missing-health-command')]}
  dep=fixture.deployment();plan=dep.prepare()
  with self.assertRaises(DeployError) as error:dep.activate(fixture.provider.get(plan['secret_names']))
  self.assertEqual(str(error.exception),'activation failed at health: '+errors.diagnostic('E_DEPLOY_FAILED')['msg']+'; previous deployment restored')
  self.assertEqual(error.exception.code,'E_DEPLOY_FAILED');self.assertFalse(dep.current.is_symlink())

 # ---- secret names and pins ----------------------------------------------
 def test_secrets_reserved_direct(self):
  self.assertEqual(deploy.direct_secret_names({'required_secrets':['EXAMPLE_SECRET'],'connectors':{'alpha':{'required_secrets':['DATA_DIRECTORY']}}}),['DATA_DIRECTORY','EXAMPLE_SECRET'])
  for config in ({'required_secrets':['DATA_DIR']},{'connectors':{'alpha':{'required_secrets':['WORKSPACE_DIR']}}},{'modules':{'beta':{'required_secrets':['EXAMPLE_SECRET','DATA_DIR']}}}):
   with self.assertRaisesRegex(DeployError,'^secret names overlap runtime state environment$'):deploy.direct_secret_names(config)

 def test_deploy_L443_12(self):
  versions={'runtime':{'connectome-host':'c'*40,'agent-framework':'d'*40,'cook':'e'*40},'connectors':{'alpha':'f'*40}}
  self.assertEqual(deploy.resolve_pins({'connectors':{'alpha':{'pin_ref':'connectors.alpha'}}},versions)['connectors'],{'alpha':'f'*40})
  for reference in ('connectors.missing','connectors.alpha.commit'):
   with self.assertRaisesRegex(DeployError,'^unknown connector pin reference$'):deploy.resolve_pins({'connectors':{'alpha':{'pin_ref':reference}}},versions)

 # ---- constructor path checks (rows 11, 52) ------------------------------
 def test_deploy_L491_12(self):
  fixture=self.fixture();tmp=fixture.tmp.name
  for value in (tmp+'/other/../runtime-state',tmp+'//runtime-state',tmp+'/runtime-state/','runtime-state'):
   fixture.config['state_dir']=value
   with self.assertRaisesRegex(DeployError,'^state_dir must be a canonical absolute path outside the deployment root$'):fixture.deployment()

 def test_deploy_private_canonical(self):
  fixture=self.fixture();canonical=fixture.config['secrets_file'];parent=str(Path(canonical).parent)
  self.assertEqual(fixture.deployment().secrets_file,Path(canonical))
  for value in (parent+'/../connectome-secrets/example.json',parent+'//example.json',parent+'/./example.json','connectome-secrets/example.json'):
   fixture.config['secrets_file']=value
   with self.assertRaisesRegex(DeployError,'^secrets_file must be a canonical absolute path$'):fixture.deployment()

 def test_deploy_L536_12(self):
  fixture=self.fixture()
  fixture.config['state_dir']=str(fixture.units/'runtime-state')
  with self.assertRaisesRegex(DeployError,'^state_dir must be separate from unit_dir$'):fixture.deployment()
  fixture.config['state_dir']=str(Path(fixture.tmp.name)/'runtime-state');fixture.units=Path(fixture.config['state_dir'])/'units'
  with self.assertRaisesRegex(DeployError,'^state_dir must be separate from unit_dir$'):fixture.deployment()

 # ---- private state directory (row 52) -----------------------------------
 def state(self,mode=0o700):
  fixture=self.fixture();state=Path(fixture.config['state_dir']);state.mkdir(mode=0o700)
  self.assertEqual(fixture.deployment().state_dir,state)
  state.chmod(mode)
  return fixture,state

 def test_deploy_private_state_kind(self):
  # Earlier parent checks already refuse a visible non-directory, so the lstat
  # fact models the directory being replaced by a private file after them.
  fixture,state=self.state()
  with self.lstat_as(state,st_mode=stat.S_IFREG|0o700),self.assertRaisesRegex(DeployError,STATE):fixture.deployment()

 def test_deploy_private_state_mode(self):
  fixture,state=self.state(0o750)
  with self.assertRaisesRegex(DeployError,STATE):fixture.deployment()
  self.assertEqual(stat.S_IMODE(state.stat().st_mode),0o750)

 def test_deploy_private_state_owner(self):
  fixture,state=self.state()
  with self.lstat_as(state,st_uid=54321),self.assertRaisesRegex(DeployError,STATE):fixture.deployment()

 def test_deploy_private_state_recheck(self):
  # Another actor wins the creation race with a wider mode: mkdir reports the
  # directory as existing, nothing is repaired and the second check refuses it.
  fixture=self.fixture();dep=fixture.deployment();state=dep.state_dir;real=Path.mkdir
  def racing(path,*args,**kwargs):
   if path==state:
    real(path);os.chmod(path,0o755)
    raise FileExistsError(str(path))
   return real(path,*args,**kwargs)
  with patch.object(Path,'mkdir',racing),self.assertRaisesRegex(DeployError,STATE):dep._ensure_state()
  self.assertEqual(stat.S_IMODE(state.stat().st_mode),0o755)

 # ---- secrets lookup, unit inspection ------------------------------------
 def test_deploy_L570_12(self):
  fixture=self.fixture();fixture.config['required_secrets']=['EXAMPLE_SECRET'];path=Path(fixture.config['secrets_file'])
  path.write_text(json.dumps({'EXAMPLE_SECRET':'synthetic-value','UNUSED_SECRET':'never-requested'}))
  dep=fixture.deployment();self.assertEqual(dep._read_secrets(),{'EXAMPLE_SECRET':'synthetic-value'})
  for content in ('{}','{"EXAMPLE_SECRET": ','["EXAMPLE_SECRET"]'):
   path.write_text(content)
   with self.assertRaisesRegex(DeployError,r'^agent secrets file lookup failed \(values redacted\)$'):dep._read_secrets()

 def test_deploy_L635_20(self):
  # An unrecognised manager answer is "cannot inspect", not "the unit is active".
  fixture=self.fixture();fixture.config['exclusive_with']=[{'scope':'user','unit':'other.service'}]
  fixture.track_units({'user:other.service':'inactive'});fixture.deployment().preflight_activation()
  fixture.track_units({'user:other.service':'maintenance'})
  with self.assertRaises(DeployError) as error:fixture.deployment().preflight_activation()
  self.assertEqual(str(error.exception),'cannot inspect exclusive unit');self.assertEqual(error.exception.code,'E_DEPLOY_FAILED')
  self.assertNotIsInstance(error.exception,deploy.UnitActive)

 def test_deploy_L649_16(self):
  # The manager disappears between two units; the first answer must not be reused.
  fixture=self.fixture();dep=fixture.deployment();calls=[]
  def manager(argv,**kwargs):
   calls.append(argv[-1])
   if len(calls)>1:raise OSError('manager unavailable')
   return SimpleNamespace(returncode=0,stdout=b'inactive\n')
  with patch.object(deploy.subprocess,'run',side_effect=manager),self.assertRaisesRegex(DeployError,'^cannot inspect unit state$'):
   dep._refuse_active({'connectome-example.service','connectome-other.service'})
  self.assertEqual(calls,['connectome-example.service','connectome-other.service'])

 def test_deploy_L651_16(self):
  # A failing manager that still prints "inactive" is not evidence of inactivity.
  fixture=self.fixture();fixture.config['activation']='install_only'
  fixture.systemctl=[sys.executable,'-c','import sys; print("inactive"); sys.exit(1)']
  with self.assertRaisesRegex(DeployError,'^cannot inspect unit state$'):fixture.deployment().preflight_activation()

 # ---- activation marker (row 68) -----------------------------------------
 def test_deploy_activate_marker(self):
  fixture=self.fixture();fixture.track_units();fixture.config['activation']='install_only'
  dep=fixture.deployment();fixture.apply(dep);marker=dep.state/'activation'
  self.assertEqual(marker.read_text(),'install_only\n')
  self.assertEqual(dep.activate({}),{'changed':False,'activation':'install_only','unit_state':'inactive'})
  for content in ('install_only\nstart\n','install_only','start\n',''):
   marker.write_text(content)
   with self.assertRaisesRegex(DeployError,'^invalid activation bookkeeping$'):dep.activate({})
  marker.unlink();marker.mkdir()
  with self.assertRaisesRegex(DeployError,'^invalid activation bookkeeping$'):dep.activate({})

 # ---- plan audit call sites (row 62) -------------------------------------
 def catalog_fixture(self):
  import test_catalog
  fixture=test_catalog.CatalogTests('test_role_prepare_adapter_build_lock_reuse_and_preflight_dropin');fixture.setUp();self.addCleanup(fixture.doCleanups)
  secrets=fixture.work/'secrets';secrets.mkdir(mode=0o700);path=secrets/'example.json';path.write_text('{}');path.chmod(0o600)
  fixture.config['secrets_file']=str(path)
  return fixture

 def catalog_plan(self,fixture):
  return deploy.Deployment(fixture.root,fixture.work/'units',fixture.config,fixture.versions,[],check=True).plan()

 def test_catalog_audit_site_plan_descriptors(self):
  # Health argv is covered by the descriptor audit only, never by the settings audit.
  fixture=self.catalog_fixture()
  self.assertEqual(self.catalog_plan(fixture)['state_dir'],fixture.config['state_dir'])
  fixture.config['health']={'command':['/bin/sh',str(fixture.root/'inputs/health.sh')]}
  with self.assertRaisesRegex(ArchiveError,'^command dependency outside build and pinned platform closure$') as error:self.catalog_plan(fixture)
  self.assertEqual(error.exception.code,'E_CATALOG_INVALID')

 def test_catalog_audit_site_plan_recipe(self):
  # Agent provider settings are covered by the plan's recipe audit only.
  fixture=self.catalog_fixture()
  self.assertEqual(self.catalog_plan(fixture)['state_dir'],fixture.config['state_dir'])
  fixture.config['agent']['provider']={'command':str(fixture.root/'inputs/provider'),'args':[]}
  with self.assertRaisesRegex(ArchiveError,'^absolute dependency outside agent tree and pinned platform closure$') as error:self.catalog_plan(fixture)
  self.assertEqual(error.exception.code,'E_CATALOG_INVALID')

 # ---- retained builds use their recorded pins (rows 39, 67) --------------
 def test_deploy_L698_16(self):
  fixture,first=self.framework_fixture()
  self.assertEqual(first._lock()['hostDependencies'][0]['name'],FRAMEWORK)
  (first.build/'framework.lock.json').write_text('{"schema": ')
  with self.assertRaisesRegex(DeployError,'^invalid framework provenance$') as error:first._lock()
  self.assertEqual(error.exception.code,'E_LOCK_INVALID')

 def test_deploy_retained_pins_framework(self):
  # Today's request pins another framework commit; the retained build still
  # verifies against the commit recorded in its own deployment.json.
  fixture,first=self.framework_fixture();expected=first._lock()
  versions=copy.deepcopy(fixture.versions);versions['runtime']['agent-framework']=PIN_B
  today=fixture.deployment(versions=versions)
  self.assertNotEqual(today.build,first.build);self.assertEqual(today.pins['runtime']['agent-framework'],PIN_B)
  try:retained=today._lock(first.build)
  except errors.SafeError as error:self.fail('retained build judged by today\'s framework pin: '+str(error))
  self.assertEqual(retained,expected)

 def test_deploy_retained_pins_catalog(self):
  # Today's catalog pins a newer preflight artifact; the retained build's
  # inventory still verifies against the pins recorded with it.
  import connectome_cook as cook
  fixture=self.catalog_fixture()
  entrypoint=fixture.root/'inputs/cook/bin/cook';entrypoint.parent.mkdir(parents=True);Path(fixture.config['cook']['host_repo']).mkdir()
  entrypoint.write_bytes((ROOT/'oracle/tests/fixtures/catalog-cook.py').read_bytes())
  command=[sys.executable,str(ROOT/'oracle/scripts/cook-adapter.py'),'--spec','{recipe}','--out','{build_dir}','--state','{state_dir}',
   '--entrypoint',str(entrypoint),'--runtime',sys.executable,'--bun-path',sys.executable,'--node-path',sys.executable]
  manager=[sys.executable,str(ROOT/'oracle/tests/fixtures/systemctl.py'),str(fixture.root)]
  first=deploy.Deployment(fixture.root,fixture.work/'units',fixture.config,fixture.versions,command,manager)
  original=deploy.run
  def invoke(argv,**kwargs):
   # As the frozen catalog test does: the pinned adapter runs in process, without the cook checkout verification.
   if not kwargs.get('adapter'):return original(argv,**kwargs)
   options={argv[i]:argv[i+1] for i in range(2,len(argv),2)}
   with patch.object(cook,'verify_cook'):cook.adapt(options['--spec'],options['--out'],options['--state'],options['--runtime'],options['--entrypoint'],options['--bun-path'],options['--node-path'])
  with patch.object(deploy,'run',side_effect=invoke):self.assertTrue(first.prepare()['changed'])
  expected=first._lock()
  versions=copy.deepcopy(fixture.versions);versions['artifacts']['readiness']['version']='1.0.1'
  today=deploy.Deployment(fixture.root,fixture.work/'units',fixture.config,versions,command,manager)
  self.assertNotEqual(today.build,first.build);self.assertNotEqual(today.pins['artifacts'],first.pins['artifacts'])
  try:retained=today._lock(first.build)
  except errors.SafeError as error:self.fail('retained build judged by today\'s catalog pins: '+str(error))
  self.assertEqual(retained,expected)
if __name__=='__main__':unittest.main()
