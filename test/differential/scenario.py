#!/usr/bin/env python3
"""Sequential same-root kind-B runner, tar fixtures + receipt/tree/tool/io logs.

Engine argv is injected. No engine implementation lives in this harness.
"""
import argparse, hashlib, json, os, shutil, subprocess, tarfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]

def manifest(root):
 rows=[]
 for path in sorted(root.rglob('*')):
  stat=path.lstat();row={'path':path.relative_to(root).as_posix(),'mode':stat.st_mode&0o777}
  if path.is_symlink():row.update(kind='link',target=os.readlink(path))
  elif path.is_dir():row.update(kind='directory')
  elif path.is_file():row.update(kind='file',sha256=hashlib.sha256(path.read_bytes()).hexdigest())
  else:row.update(kind='special')
  rows.append(row)
 return rows

def restore(archive,root):
 if root.exists():shutil.rmtree(root)
 root.mkdir(mode=0o700,parents=True)
 with tarfile.open(archive,'r:*') as tar:
  members=tar.getmembers();names=set();total=0
  for member in members:
   p=Path(member.name);total+=member.size
   if p.is_absolute() or '..' in p.parts or member.name in names or not (member.isfile() or member.isdir()) or total>64*1024*1024 or len(members)>10000:raise ValueError('unsafe scenario archive')
   names.add(member.name)
  tar.extractall(root,members=members,filter='data')

def run(archive,root,request,argv):
 # Same literal root for both engines. Never place caller-selected roots here.
 if root!=ROOT/'.cache/scenario-root':raise ValueError('fixed harness root required')
 restore(archive,root)
 request=json.loads(json.dumps(request).replace('${ROOT}',str(root)))
 directory=ROOT/'.cache/scenario-input';directory.mkdir(mode=0o700,parents=True,exist_ok=True)
 if directory.stat().st_mode&0o777!=0o700:raise ValueError('private input directory required')
 input_path=directory/'request.json'
 fd=os.open(input_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 try:
  with os.fdopen(fd,'w') as stream:stream.write(json.dumps(request))
  result=subprocess.run(argv+[request['command'],'--input',str(input_path)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={'PATH':os.defpath,'LANG':'C'},timeout=60)
 finally:input_path.unlink(missing_ok=True)
 lines=result.stdout.splitlines()
 if len(lines)!=1 or result.stderr:raise ValueError('non-contract engine output')
 receipt=json.loads(lines[0]);core=json.dumps(receipt['core'],sort_keys=True,separators=(',',':'),ensure_ascii=True)
 calls={}
 for name in ('fake-tools.jsonl','io-calls.jsonl'):
  p=root/name;calls[name]=p.read_bytes().hex() if p.exists() else ''
 return {'core':core,'status':result.returncode,'manifest':manifest(root),'logs':calls}

def compare(archive,request,left,right):
 root=ROOT/'.cache/scenario-root';a=run(archive,root,request,left);b=run(archive,root,request,right)
 if a!=b:raise AssertionError('scenario differential mismatch')
 return a
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('vector',type=Path);parser.add_argument('--left',nargs='+',required=True);parser.add_argument('--right',nargs='+',required=True);args=parser.parse_args();v=json.loads(args.vector.read_text());compare(args.vector.parent/v['archive'],v['request'],args.left,args.right);print('PASS scenario differential')
