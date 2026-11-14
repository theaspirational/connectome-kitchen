"""B3 tagged argument decoding; injected fakes require an explicit registry."""
import base64, io
from pathlib import Path

def decode(value, *, fakes=None):
 fakes=fakes or {}
 if isinstance(value,list):return [decode(v,fakes=fakes) for v in value]
 if not isinstance(value,dict):return value
 if '$path' in value:return Path(value['$path'])
 if '$bytes' in value:return base64.b64decode(value['$bytes'],validate=True)
 if '$float' in value:return float(value['$float'])
 if '$mapping' in value:return {decode(k,fakes=fakes):decode(v,fakes=fakes) for k,v in value['$mapping']}
 if '$stream' in value:
  content=decode(value['$stream'],fakes=fakes);stream=io.BytesIO(content) if isinstance(content,bytes) else io.StringIO(content);stream.seek(value['position']);return stream
 if '$fake' in value:
  if value['$fake'] not in fakes:raise ValueError('unregistered named fake')
  return fakes[value['$fake']]
 if any(k.startswith('$') for k in value):raise ValueError('not an independently replayable argument')
 return {k:decode(v,fakes=fakes) for k,v in value.items()}
