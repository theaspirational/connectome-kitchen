"""Resolve review-era commit ids into the local filtered oracle history."""
import json
from pathlib import Path

def mapped(prefix):
 mapping=json.loads((Path(__file__).resolve().parents[1]/'commit-map.golden').read_text())
 matches=[new for old,new in mapping.items() if old.startswith(prefix)]
 if len(matches)!=1:raise ValueError('ambiguous or missing history baseline')
 return matches[0]
