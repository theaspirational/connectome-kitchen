#!/usr/bin/env python3
"""Replay only explicitly pure A vectors; unknown fakes/objects fail closed.

This is not a sandbox and is not a license to replay arbitrary recorded argv.
The audited function allowlist excludes child runners and all filesystem APIs.
"""
import argparse,importlib,json,sys,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from codec import decode
from record import encode
PURE={
 'connectome_lock':{'credential_shaped','names_only','repository','need','text','names'},
 'connectome_config':{'merge_layers','public_catalog'},
 'connectome_deploy':{'canonical'},
 'connectome_catalog':{'canonical'},
 'connectome_cook':{'validate_arguments'},
 'connectome_errors':{'cook_code','cook_code_from_stream','diagnostic','adapter_diagnostic'},
 'connectome_spawn.data':{'canonical','digest','decode','fields','match','need'},
 'connectome_spawn.schema':{'template','instance','name_record','name_receipt','home','identity_policy'},
}
def replay(row):
 module,name=row['function'].rsplit('.',1)
 if name not in PURE.get(module,set()):return None
 try:args=decode(row['args']);kwargs=decode(row['kwargs'])
 except (ValueError,TypeError):return None
 function=getattr(importlib.import_module(module),name,None)
 if function is None:return None
 try:result=function(*args,**kwargs)
 except Exception as exc:
  actual={'exception':type(exc).__name__,'message':str(exc)}
  if hasattr(exc,'public'):actual['public']=encode(exc.public())
 else:actual={'result':encode(result)}
 return actual==row['outcome']
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--modules',type=Path,required=True);parser.add_argument('--vectors',type=Path,required=True);args=parser.parse_args();sys.path.insert(0,str(args.modules.resolve()))
 count=0;failed=0;witnesses=[]
 for line in args.vectors.read_text().splitlines():
  result=replay(json.loads(line))
  if result is not None:
   count+=1;failed+=not result
   if not result and len(witnesses)<10:witnesses.append(hashlib.sha256(line.encode()).hexdigest())
 print(json.dumps({'replayed':count,'mismatches':failed,'witnesses':witnesses}));return int(bool(failed) or not count)
if __name__=='__main__':sys.exit(main())
