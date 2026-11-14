#!/usr/bin/env python3
"""Offline history rehearsal; source clone objects are local and read-only upstream."""
import os, subprocess, json, argparse
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--repo',type=Path,required=True);parser.add_argument('--tip',default='f0d40741');args=parser.parse_args();repo=args.repo.resolve()
def git(*args, input=None, env=None):
 return subprocess.check_output(['git', '-C', str(repo), *args], input=input, env=env)
old=git('rev-parse',args.tip).decode().strip()
if not (repo/'.git').is_dir() or git('status','--porcelain').strip() or git('rev-parse','HEAD').decode().strip()!=old or git('remote').strip():
 raise SystemExit('requires a clean fresh standalone clone at the requested tip with no remotes')
if git('ls-tree','--name-only','HEAD','oracle').strip():raise SystemExit('refusing to rewrite an existing kitchen repository')
commits=git('rev-list','--reverse','--topo-order',old).decode().splitlines()
index=repo.parent/'filter.index'
env=dict(os.environ,GIT_INDEX_FILE=str(index))
mapping={}
for commit in commits:
 git('read-tree','--empty',env=env)
 entries=[]
 for entry in git('ls-tree','-r','-z',commit).split(b'\0'):
  if not entry: continue
  info,path=entry.split(b'\t'); name=path.decode()
  for prefix,target in [('roles/connectome_agent/module_utils/','oracle/module_utils/'),('scripts/','oracle/scripts/'),('tests/','oracle/tests/')]:
   if name.startswith(prefix): name=target+name[len(prefix):]; break
  else:
   if name not in ('LICENSE','pyproject.toml'): continue
  entries.append(info+b'\t'+name.encode()+b'\0')
 git('update-index','-z','--index-info',input=b''.join(entries),env=env)
 tree=git('write-tree',env=env).decode().strip()
 raw=git('cat-file','commit',commit); header,message=raw.split(b'\n\n',1)
 ce=dict(os.environ)
 parents=[]
 for line in header.decode().splitlines():
  if line.startswith('parent '): parents += ['-p',mapping[line[7:]]]
  if line.startswith(('author ','committer ')):
   role,identity=line.split(' ',1); person,stamp=identity.rsplit('> ',1); name,email=person.rsplit(' <',1)
   ce.update({f'GIT_{role.upper()}_NAME':name,f'GIT_{role.upper()}_EMAIL':email,f'GIT_{role.upper()}_DATE':stamp})
 mapping[commit]=git('commit-tree',tree,*parents,input=message,env=ce).decode().strip()
git('update-ref','refs/heads/main',mapping[old]); git('reset','--hard',mapping[old])
# No remote, old refs, tags or backup branches are retained in the seed.
for ref in git('for-each-ref','--format=%(refname)').decode().splitlines():
 if ref!='refs/heads/main': git('update-ref','-d',ref)
(repo.parent/'commit-map.json').write_text(json.dumps(mapping,indent=2)+'\n')
print(f'Filtered {len(mapping)} commits; topology and metadata retained')
