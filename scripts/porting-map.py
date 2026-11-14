#!/usr/bin/env python3
import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
lines=['# Porting map — baseline a49b5dc','', 'No engine module is ported yet (phases 3+). The following frozen public symbols', 'remain oracle-only; primitive helpers have no one-to-one Python owner.', '', '| Status | Symbol | Frozen source |','| --- | --- | --- |']
for p in sorted((ROOT/'oracle/module_utils').rglob('*.py')):
 tree=ast.parse(p.read_text())
 for node in tree.body:
  if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and not node.name.startswith('_'):
   lines.append('| oracle-only | `'+p.stem+'.'+node.name+'` | `'+p.relative_to(ROOT).as_posix()+':'+str(node.lineno)+'` |')
(ROOT/'PORTING.md').write_text('\n'.join(lines)+'\n')
