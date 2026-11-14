#!/usr/bin/env python3
"""Verify map topology, exact commit metadata and all preserved/renamed blobs."""
import argparse,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def git(root,*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)
def tree(root,commit):
 result={}
 for entry in git(root,'ls-tree','-r','-z',commit).split(b'\0'):
  if entry:
   info,name=entry.split(b'\t');mode,kind,blob=info.split();result[name.decode()]=(mode,kind,blob)
 return result
parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);args=parser.parse_args()
mapping=json.loads((ROOT/'oracle/commit-map.golden').read_text());expected=git(args.source,'rev-list','f0d40741').decode().splitlines();assert set(mapping)==set(expected)
for old,new in mapping.items():
 before=git(args.source,'cat-file','commit',old);after=git(ROOT,'cat-file','commit',new)
 old_header,old_message=before.split(b'\n\n',1);new_header,new_message=after.split(b'\n\n',1)
 assert old_message==new_message
 old_lines=old_header.splitlines();new_lines=new_header.splitlines()
 for prefix in (b'author ',b'committer '):assert [x for x in old_lines if x.startswith(prefix)]==[x for x in new_lines if x.startswith(prefix)]
 assert [mapping[x[7:].decode()] for x in old_lines if x.startswith(b'parent ')]==[x[7:].decode() for x in new_lines if x.startswith(b'parent ')]
 expected={}
 for name,(mode,kind,blob) in tree(args.source,old).items():
  for prefix,target in [('roles/connectome_agent/module_utils/','oracle/module_utils/'),('scripts/','oracle/scripts/'),('tests/','oracle/tests/')]:
   if name.startswith(prefix):name=target+name[len(prefix):];break
  else:
   if name not in ('LICENSE','pyproject.toml'):continue
  expected[name]=(mode,kind,blob)
 assert expected==tree(ROOT,new), 'filtered tree mismatch'
print(f'PASS: {len(mapping)} commit mappings; topology, metadata, retained modes and blobs verified')
