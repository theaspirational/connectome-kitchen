#!/usr/bin/env python3
"""Canonical inventory names for independently observed non-refusal effects."""
import json, os, shutil, subprocess, sys
from pathlib import Path
from invisible import MUTANTS, ROOT
ALIASES={
 'cook.argv.stdin':'cook.argv.stdin',
 'cook.argv.safe-directory':'cook.git.safe-directory',
 'cook.argv.fsmonitor':'cook.git.fsmonitor',
 'cook.argv.hooks':'cook.git.hooks',
 'cook.argv.optional-locks':'cook.git.optional-locks',
 'catalog.sandbox.home':'catalog.build.env',
 'catalog.sandbox.offline':'catalog.build.offline',
 'catalog.sandbox.stdout':'catalog.build.output',
 'spawn.persist.fsync-file':'spawn.persist.fsync-file',
 'spawn.persist.fsync-dir':'spawn.persist.fsync-dir',
 'spawn.persist.atomic':'spawn.persist.atomic',
 'deploy.prepare.ownership-first':'deploy.prepare.ownership-first',
}
SPECS=[(ALIASES[n],r,f,b,a,t) for n,r,f,b,a,t in MUTANTS if n in ALIASES]
SPECS += [(name,48,'connectome_catalog.py',before,after,'test_row48_isolation_golden') for name,before,after in [
 ('catalog.unit.reset',"lines = ['Environment=', 'PassEnvironment=', 'EnvironmentFile=',", "lines = ['Environment=', 'EnvironmentFile=',"),
 ('catalog.unit.unset',"'UnsetEnvironment=' + ' '.join(sorted(CODE_ENV))","'UnsetEnvironment='"),
 ('catalog.unit.envfile',"lines.append('EnvironmentFile=' + systemd_words([str(secret_env_path(build))]))",'pass'),
 ('catalog.unit.overlap',"need(not env.keys() & base.keys(), 'unit environment overlaps constructed environment')",'pass'),
 ('catalog.unit.base-env',"base.update({'DATA_DIR': str(state), 'TMPDIR': str(state)})",'pass'),
 ('catalog.unit.escape',".replace('%', '%%')",''),
 ('catalog.unit.exec-clear',"lines.append(key + '=')",'pass'),
 ('catalog.unit.preflights',"commands.setdefault('ExecStartPre', []).extend(preflights(config, build))",'pass'),
 ('catalog.unit.wrap',"systemd_words(isolated_argv(config, build, argv, env, pins['platform']))",'systemd_words(argv)'),
 ('catalog.unit.payload-names',"'secrets': secret_environment_names(config)","'secrets': []"),
]]
SPECS.append(('catalog.build.path',53,'connectome_catalog.py',"""env['PATH'] = os.pathsep.join(str(directory / n / Path(e['exports'][n]).parent)
                                      for n, e in entries.items() if n in ('node', 'bun') and
                                      (directory / n).exists())""","env['PATH'] = ''",'test_row53_build_sandbox_stdio_and_environment'))

def main():
 import hashlib
 scratch=ROOT/'.cache/named-effect-mutants';scratch.mkdir(parents=True,exist_ok=True)
 shutil.copytree(ROOT/'oracle',scratch/'oracle',ignore=shutil.ignore_patterns('.cache','__pycache__'),dirs_exist_ok=True)
 shutil.copytree(ROOT/'test/unit',scratch/'test/unit',ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
 results=[];anchors=[]
 for name,row,file,before,after,test in SPECS:
  target=scratch/'oracle/module_utils'/file;original=target.read_text()
  if original.count(before)!=1:raise ValueError('ambiguous source anchor: '+name)
  anchors.append({'id':name,'row':row,'file':'module_utils/'+file,'line':original[:original.index(before)].count('\n')+1,'before':before,'after':after,'source_sha256':hashlib.sha256(original.encode()).hexdigest(),'vector':test})
  target.write_text(original.replace(before,after));shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
  try:
   result=subprocess.run([sys.executable,'-B','-m','unittest','test_invisible.InvisibleGuards.'+test],cwd=scratch/'test/unit',capture_output=True,timeout=30,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
   text=result.stderr.decode();killed='Ran 1 test' in text and 'FAILED (failures=1)' in text and 'ERROR' not in text
   results.append({'id':name,'row':row,'vector':test,'status':'killed' if killed else 'not-killed','exit':result.returncode})
  except subprocess.TimeoutExpired:results.append({'id':name,'row':row,'status':'timeout'})
  finally:target.write_text(original);shutil.rmtree(target.parent/'__pycache__',ignore_errors=True)
 (ROOT/'test/mutants/named-effect-anchors.golden').write_text(json.dumps(anchors,indent=2)+'\n')
 (ROOT/'test/mutants/named-effect-results.golden').write_text(json.dumps(results,indent=2)+'\n')
 print(json.dumps({'mutants':len(results),'killed':sum(r['status']=='killed' for r in results)}));return int(any(r['status']!='killed' for r in results))
if __name__=='__main__':sys.exit(main())
