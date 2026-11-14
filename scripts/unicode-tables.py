#!/usr/bin/env python3
"""Regenerate tables using the frozen oracle's CPython Unicode database."""
from pathlib import Path
import json, unicodedata
predicates={'space':str.isspace,'digit':str.isdigit,'decimal':str.isdecimal,'word':lambda c:c.isalnum() or c=='_'}
out=['// Generated from CPython Unicode '+unicodedata.unidata_version+'; do not edit.','export type Range = readonly [number, number];']
for name,predicate in predicates.items():
 ranges=[]
 for n in range(0x110000):
  if predicate(chr(n)):
   if ranges and n==ranges[-1][1]+1:ranges[-1][1]=n
   else:ranges.append([n,n])
 out.append('export const '+name+': readonly Range[] = '+json.dumps(ranges,separators=(',',':'))+';')
out.append('export function contains(ranges: readonly Range[], char: string): boolean { const n=char.codePointAt(0)!; let a=0,b=ranges.length; while(a<b){ const m=(a+b)>>>1; if(n<ranges[m]![0])b=m;else if(n>ranges[m]![1])a=m+1;else return true; } return false; }')
Path('src/pylike/unicode.ts').write_text('\n'.join(out)+'\n')
