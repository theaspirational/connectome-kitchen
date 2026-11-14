#!/usr/bin/env python3
"""Named oracle mutants for effects ordinary receipts cannot observe (§3.4.5)."""
import json, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
MUTANTS=[
 ('cook.argv.stdin',51,'connectome_cook.py','stdin=subprocess.DEVNULL,','stdin=None,','test_row51_stdio_and_environment'),
 ('cook.argv.stdout',51,'connectome_cook.py','stdout=subprocess.PIPE if capture else subprocess.DEVNULL,','stdout=subprocess.PIPE,','test_row51_stdio_and_environment'),
 ('cook.argv.safe-directory',51,'connectome_cook.py',"'safe.directory=' + checkout","'safe.directory=*'",'test_row51_git_options'),
 ('cook.argv.fsmonitor',51,'connectome_cook.py',"'core.fsmonitor=false'","'core.fsmonitor=true'",'test_row51_git_options'),
 ('cook.argv.hooks',51,'connectome_cook.py',"'core.hooksPath=/dev/null'","'core.hooksPath=/tmp'",'test_row51_git_options'),
 ('cook.argv.optional-locks',51,'connectome_cook.py',"'--no-optional-locks'","'--optional-locks'",'test_row51_git_options'),
 ('catalog.sandbox.stdin',53,'connectome_catalog.py','stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL','stdin=None, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL','test_row53_build_sandbox_stdio_and_environment'),
 ('catalog.sandbox.stdout',53,'connectome_catalog.py','stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL','stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL','test_row53_build_sandbox_stdio_and_environment'),
 ('catalog.sandbox.stderr',53,'connectome_catalog.py','stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL','stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE','test_row53_build_sandbox_stdio_and_environment'),
 ('catalog.sandbox.offline',53,'connectome_catalog.py',"'npm_config_offline': 'true'","'npm_config_offline': 'false'",'test_row53_build_sandbox_stdio_and_environment'),
 ('catalog.sandbox.home',53,'connectome_catalog.py',"'HOME': str(build / 'catalog-home')","'HOME': str(build.parent / 'catalog-home')",'test_row53_build_sandbox_stdio_and_environment'),
 ('catalog.sandbox.tmp',53,'connectome_catalog.py',"'TMPDIR': str(build / 'catalog-tmp')","'TMPDIR': str(build.parent / 'catalog-tmp')",'test_row53_build_sandbox_stdio_and_environment'),
 ('catalog.sandbox.cache',53,'connectome_catalog.py',"'npm_config_cache': str(build / 'catalog-cache')","'npm_config_cache': str(build.parent / 'catalog-cache')",'test_row53_build_sandbox_stdio_and_environment'),
 ('spawn.persist.fsync-file',58,'connectome_spawn/persistence.py','os.fsync(stream.fileno())','pass','test_row58_syscall_order'),
 ('spawn.persist.fsync-dir',58,'connectome_spawn/persistence.py','        sync_dir(path.parent)\n    finally:', '        pass\n    finally:','test_row58_syscall_order'),
 ('spawn.persist.atomic',58,'connectome_spawn/persistence.py','os.replace(temporary, path)','Path(path).write_bytes(Path(temporary).read_bytes())','test_row58_syscall_order'),
 ('deploy.prepare.ownership-first',65,'connectome_deploy.py','        self._save_ownership()  # persist ownership before invoking cook','        pass  # mutant: omit pre-cook ownership','test_row65_ownership_durable_before_cook'),
]
MUTANTS += [
 ('catalog.exec-env.walk',13,'connectome_catalog.py','check(s.build);',';','test_row13_exec_environment_rechecks_tree'),
 ('catalog.exec-env.hardlink',13,'connectome_catalog.py','(t.isFile()&&t.nlink!==1)','false','test_row13_exec_environment_rechecks_tree'),
 ('catalog.exec-env.writable',13,'connectome_catalog.py','(t.mode&0o222)','false','test_row13_exec_environment_rechecks_tree'),
 ('io.single-writer',37,'!library/connectome_release.py','fcntl.flock(stream, fcntl.LOCK_EX)','pass','test_row37_role_lock_before_effect'),
 ('gate.rules',47,'!scripts/check_public.py','for name, rule in (RULES if rules is None else rules)','for name, rule in []','test_row47_publication_rule_table'),
]
SANDBOX = """env = {'HOME': str(build / 'catalog-home'), 'TMPDIR': str(build / 'catalog-tmp'),
           'PATH': '', 'LANG': 'C.UTF-8', 'NO_UPDATE_NOTIFIER': '1', 'npm_config_offline': 'true',
           'npm_config_cache': str(build / 'catalog-cache')}"""
MUTANTS=[(name,row,file,SANDBOX,SANDBOX.replace(before,after),test) if name in
 {'catalog.sandbox.offline','catalog.sandbox.home','catalog.sandbox.tmp','catalog.sandbox.cache'}
 else (name,row,file,before,after,test) for name,row,file,before,after,test in MUTANTS]
def main():
 scratch=ROOT/'.cache/invisible-mutants';scratch.mkdir(parents=True,exist_ok=True)
 copy=scratch/'repo';copy.mkdir(exist_ok=True)
 shutil.copytree(ROOT/'oracle',copy/'oracle',ignore=shutil.ignore_patterns('.cache','__pycache__'),dirs_exist_ok=True)
 shutil.copytree(ROOT/'test/unit',copy/'test/unit',ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
 results=[]
 for name,row,file,before,after,test in MUTANTS:
  target=copy/'oracle'/file[1:] if file.startswith('!') else copy/'oracle/module_utils'/file;original=target.read_text()
  if original.count(before)!=1:results.append({'id':name,'row':row,'status':'anchor-mismatch'});continue
  target.write_text(original.replace(before,after));shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
  try:
   result=subprocess.run([sys.executable,'-B','-m','unittest','test_invisible.InvisibleGuards.'+test],cwd=copy/'test/unit',capture_output=True,timeout=30)
   # unittest writes its machine-stable summary to stderr; never print raw failures.
   text=result.stderr.decode();killed='Ran 1 test' in text and 'FAILED (failures=1)' in text and 'ERROR' not in text
   results.append({'id':name,'row':row,'vector':test,'status':'killed' if killed else 'not-killed','exit':result.returncode})
  finally:target.write_text(original);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
 (ROOT/'test/mutants/invisible-results.golden').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps({'mutants':len(results),'killed':sum(r['status']=='killed' for r in results)}));return int(any(r['status']!='killed' for r in results))
if __name__=='__main__':sys.exit(main())
