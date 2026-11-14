import contextlib, importlib.util, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'test/differential'))
from codec import decode
from record import encode
spec=importlib.util.spec_from_file_location('shim',ROOT/'oracle/shim/kitchen_v1.py');shim=importlib.util.module_from_spec(spec);spec.loader.exec_module(shim)
sys.path.insert(0,str(ROOT/'oracle/module_utils'))
from connectome_spawn.cli import main as legacy
from connectome_errors import SafeError

class HarnessTests(unittest.TestCase):
 def test_tagged_roundtrip(self):
  stream=io.BytesIO(b'abc');stream.seek(1)
  value=[Path('/example'),b'bytes',stream,2.0,{'x':3}];result=decode(encode(value))
  self.assertEqual(result[:2],value[:2]);self.assertEqual(result[2].read(),b'bc');self.assertEqual(result[3:],value[3:])
  self.assertRaises(ValueError,decode,{'$fake':'not.registered'})
  fake=lambda:None;self.assertIs(decode({'$fake':'fake'},fakes={'fake':fake}),fake)
 def test_number_waiver_and_duplicate(self):
  for text in ('1E400','NaN','{"x":1,"x":2}'):
   self.assertRaises(shim.ContractError,shim.strict,text)
  self.assertIsInstance(shim.strict('1.0'),float);self.assertIsInstance(shim.strict('12345678901234567890123'),int)
 def test_projection_status_preserves_core(self):
  state=ROOT/'.cache/no-state'
  request={'contract':shim.CONTRACT,'command':'spawn','request':{'action':'status','state_root':str(state),'agent':'example'}}
  receipt,status=shim.dispatch(request);out=io.StringIO();err=io.StringIO()
  with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):old_status=legacy(['status','example','--state-root',str(state)])
  self.assertEqual((status,old_status),(1,2));core=dict(receipt['core']);core.pop('error_code');self.assertEqual(json.dumps(core)+'\n',err.getvalue());self.assertEqual(out.getvalue(),'');self.assertFalse(state.exists())
  # shim.core mutant: changing the engine result must fail the fixed projection.
  mutated=dict(core,changed=True)
  self.assertNotEqual(json.dumps(mutated)+'\n',err.getvalue())
 def test_failure_keeps_prepare_only_after_success_N1(self):
  prepared={'changed':True,'build_dir':'/example/build','secret_names':[],'plan':{}}
  class Dep:
   def __init__(self,**kw):pass
   def prepare(self):return prepared
   def activate(self,secrets):raise SafeError('private')
  request={'contract':shim.CONTRACT,'command':'plan','request':{'root':'/example','unit_dir':'/example-units','config':{},'versions':{},'engines':{'cook':{'entrypoint':'/bin/true','runtime':'/bin/true','bun':'/bin/true','node':'/bin/true'}}}}
  with patch.object(shim,'Deployment',Dep):receipt,status=shim.dispatch(request)
  self.assertEqual(status,1);self.assertEqual(receipt['core']['prepare'],prepared);self.assertEqual(receipt['core']['phase'],'activate');self.assertNotIn('activate',receipt['core'])
  request['request']['phase']='activate';request['command']='apply';Dep.validate_paths=lambda self:None;Dep.preflight_activation=lambda self:None
  # Single activate can be tested in check mode without a mutating lock.
  request['command']='plan';request['request'].pop('phase')
  Dep.prepare=lambda self:(_ for _ in ()).throw(SafeError('private'))
  with patch.object(shim,'Deployment',Dep):receipt,status=shim.dispatch(request)
  self.assertEqual(receipt['core']['phase'],'prepare');self.assertNotIn('prepare',receipt['core']);self.assertNotIn('private',json.dumps(receipt))
 def test_cli_refusal_and_receipt_file(self):
  work=ROOT/'.cache/shim';work.mkdir(parents=True,exist_ok=True);input_path=work/'request.json';receipt=work/'receipt'
  input_path.write_text('{"contract":"bad","command":"plan","request":{}}')
  result=subprocess.run([sys.executable,str(ROOT/'oracle/shim/kitchen_v1.py'),'plan','--input',str(input_path),'--receipt',str(receipt)],capture_output=True)
  self.assertEqual(result.returncode,2);self.assertEqual(result.stderr,b'');self.assertEqual(result.stdout,receipt.read_bytes());self.assertEqual(receipt.stat().st_mode&0o777,0o600)
 def test_cli_unknown_command_never_echoes_input(self):
  result=subprocess.run([sys.executable,str(ROOT/'oracle/shim/kitchen_v1.py'),'protected-value','--input','/nonexistent'],capture_output=True)
  self.assertEqual(result.returncode,2);self.assertNotIn(b'protected-value',result.stdout+result.stderr)
 def test_successful_validate_cli_projection(self):
  import yaml
  spec=yaml.safe_load((ROOT/'oracle/examples/spawn/template.yml').read_text())
  declaration=yaml.safe_load((ROOT/'oracle/examples/spawn/instance.yml').read_text())
  directory=ROOT/'.cache/validate-projection';directory.mkdir(parents=True,exist_ok=True)
  instance_path=directory/'instance.json';instance_path.write_text(json.dumps(declaration))
  request={'contract':shim.CONTRACT,'command':'spawn','request':{'action':'validate','template':spec,'instance':declaration}}
  receipt,status=shim.dispatch(request);out=io.StringIO();err=io.StringIO()
  with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):old_status=legacy(['validate','--inline',json.dumps(spec),'--instance',str(instance_path)])
  self.assertEqual((status,old_status),(0,0));self.assertEqual(json.dumps(receipt['core'],sort_keys=True,indent=2)+'\n',out.getvalue());self.assertEqual(err.getvalue(),'')
 def test_tar_scenario_same_root_and_manifest(self):
  import tarfile
  from scenario import compare
  path=ROOT/'.cache/scenario-fixture.tar'
  with tarfile.open(path,'w'):pass
  request={'contract':shim.CONTRACT,'command':'spawn','request':{'action':'status','state_root':'${ROOT}/state','agent':'example'}}
  argv=[sys.executable,str(ROOT/'oracle/shim/kitchen_v1.py')]
  result=compare(path,request,argv,argv)
  self.assertEqual(result['status'],1);self.assertEqual(result['manifest'],[])
if __name__=='__main__':unittest.main()
