"""Purpose-built observable vectors for guards invisible in receipts/goldens."""
import json, os, stat, subprocess, sys, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'oracle/module_utils'),str(ROOT/'oracle/tests')]
import connectome_cook as cook
from connectome_spawn import persistence
import connectome_deploy as deploy
import test_deploy

class InvisibleGuards(unittest.TestCase):
 def test_row51_stdio_and_environment(self):
  calls=[]
  def child(argv,**kw):
   calls.append(kw)
   self.assertIs(kw['stdin'],subprocess.DEVNULL)
   self.assertIs(kw['stdout'],subprocess.DEVNULL)
   self.assertTrue(hasattr(kw['stderr'],'fileno'))
   self.assertEqual(kw['env'],{'LANG':'C'})
   return SimpleNamespace(returncode=0,stdout=None)
  with patch.object(cook.subprocess,'run',side_effect=child):cook.command(['/bin/true'],env={'LANG':'C'})
  self.assertEqual(len(calls),1)
  with patch.dict(os.environ,{'UNTRUSTED_SETTING':'private'}):self.assertNotIn('UNTRUSTED_SETTING',cook.clean_environment())
 def test_row51_git_options(self):
  directory=ROOT/'.cache/git-options';(directory/'bin').mkdir(parents=True,exist_ok=True);entry=directory/'bin/cook';entry.write_text('fixture')
  calls=[]
  def command(argv,**kw):
   calls.append(argv)
   for option in ('safe.directory='+str(directory.resolve()),'core.fsmonitor=false','core.hooksPath=/dev/null','--no-optional-locks'):self.assertIn(option,argv)
   self.assertTrue(set((1,3,5,7,8))<=set(kw['flag_indices']))
   return b'' if 'status' in argv else (cook.COOK_COMMIT+'\n').encode()
  with patch.object(cook,'command',side_effect=command):cook.verify_cook(str(entry),cook.COOK_COMMIT,{})
  self.assertEqual(len(calls),4)
 def test_row58_syscall_order(self):
  directory=ROOT/'.cache/persistence';directory.mkdir(parents=True,exist_ok=True);path=directory/'state'
  log=[];real_sync=os.fsync;real_replace=os.replace
  def sync(fd):
   log.append('fsync-dir' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'fsync-file');return real_sync(fd)
  def replace(*a,**kw):log.append('rename');return real_replace(*a,**kw)
  with patch.object(persistence.os,'fsync',side_effect=sync),patch.object(persistence.os,'replace',side_effect=replace):persistence.write(path,{'schema':'fixture'})
  self.assertEqual(log,['fsync-file','rename','fsync-dir'])
  self.assertEqual(path.stat().st_mode&0o777,0o600)
 def test_row65_ownership_durable_before_cook(self):
  fixture=test_deploy.DeployTests('test_install_only_static_health_and_status');fixture.setUp();self.addCleanup(fixture.doCleanups)
  dep=fixture.deployment();calls=[];original=deploy.run
  def run(argv,**kw):
   if kw.get('adapter'):
    ownership=dep.state/'ownership.json';self.assertTrue(ownership.is_file())
    self.assertIn(str(dep.build),ownership.read_text());calls.append('ownership-before-cook')
   return original(argv,**kw)
  with patch.object(deploy,'run',side_effect=run):dep.prepare()
  self.assertEqual(calls,['ownership-before-cook'])
 def test_row53_build_sandbox_stdio_and_environment(self):
  import test_catalog
  import connectome_catalog as catalog
  fixture=test_catalog.CatalogTests('test_build_and_cook_environment_never_copy_operator_environment');fixture.setUp();self.addCleanup(fixture.doCleanups)
  original=catalog.subprocess.run;calls=[]
  def run(argv,**kw):
   if kw.get('cwd')==fixture.build/'catalog/extra':
    env=kw['env'];calls.append('catalog-build')
    self.assertEqual(env['npm_config_offline'],'true')
    for key in ('HOME','TMPDIR','npm_config_cache'):self.assertTrue(Path(env[key]).is_relative_to(fixture.build))
    self.assertNotIn('OPERATOR_ONLY',env)
    for key in ('stdin','stdout','stderr'):self.assertEqual(kw[key],subprocess.DEVNULL)
   return original(argv,**kw)
  with patch.dict(os.environ,{'OPERATOR_ONLY':'private'}),patch.object(catalog.subprocess,'run',side_effect=run):fixture.install()
  self.assertEqual(calls,['catalog-build'])
 def test_row13_exec_environment_rechecks_tree(self):
  import base64,shutil
  import connectome_catalog as catalog
  node=shutil.which('node');self.assertIsNotNone(node)
  root=ROOT/'.cache/exec-env';root.mkdir(parents=True,exist_ok=True);root.chmod(0o755)
  leaf=root/'file'
  if leaf.exists():leaf.chmod(0o644)
  leaf.write_text('data');leaf.chmod(0o444);root.chmod(0o555)
  self.addCleanup(root.chmod,0o755)
  payload={'build':str(root),'platform':[],'env':{},'secrets':[],'argv':['/bin/true']}
  script="process.execve=()=>{process.stdout.write('exec');};"+catalog.EXEC_ENV
  def run():return subprocess.run([node,'--eval',script,base64.b64encode(json.dumps(payload).encode()).decode()],capture_output=True,env={})
  self.assertEqual(run().stdout,b'exec')
  leaf.chmod(0o644);self.assertNotEqual(run().returncode,0);leaf.chmod(0o444)
  root.chmod(0o755);link=root/'link';link.symlink_to('file');root.chmod(0o555)
  try:self.assertNotEqual(run().returncode,0)
  finally:root.chmod(0o755);link.unlink();root.chmod(0o555)
  root.chmod(0o755);hard=root/'hard';os.link(leaf,hard);root.chmod(0o555)
  try:self.assertNotEqual(run().returncode,0)
  finally:root.chmod(0o755);hard.unlink()
 def test_row37_role_lock_before_effect(self):
  import importlib.util,types
  log=[];lock=ROOT/'.cache/role-lock';lock.parent.mkdir(exist_ok=True)
  class Module:
   check_mode=False
   params={'operation':'prepare','secrets':None}
   def exit_json(self,**kw):log.append('exit')
   def fail_json(self,**kw):raise AssertionError('unexpected boundary refusal')
  class Dep:
   root=ROOT/'.cache/role-root'
   def __init__(self,**kw):pass
   def validate_paths(self):log.append('validate')
   def preflight_activation(self):log.append('preflight')
   def lock_path(self):return lock
   def prepare(self):log.append('prepare');return {'changed':False}
  fake={'ansible':types.ModuleType('ansible'),'ansible.module_utils':types.ModuleType('ansible.module_utils'),'ansible.module_utils.basic':types.SimpleNamespace(AnsibleModule=lambda **kw:Module()),'ansible.module_utils.connectome_deploy':types.SimpleNamespace(Deployment=Dep,make_directory=lambda root:log.append('directory')),'ansible.module_utils.connectome_errors':sys.modules['connectome_errors']}
  spec=importlib.util.spec_from_file_location('role_boundary',ROOT/'oracle/library/connectome_release.py');module=importlib.util.module_from_spec(spec)
  with patch.dict(sys.modules,fake):spec.loader.exec_module(module)
  with patch.object(module.fcntl,'flock',side_effect=lambda *a:log.append('flock')):module.main()
  self.assertEqual(log,['validate','preflight','directory','flock','prepare','exit'])
 def test_row47_publication_rule_table(self):
  import test_public
  case=test_public.PublicTests('test_single_rule_table_on_plain_config_keys_and_values');case.setUp()
  try:case.test_single_rule_table_on_plain_config_keys_and_values()
  finally:case.doCleanups()
if __name__=='__main__':unittest.main()
