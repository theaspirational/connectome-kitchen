#!/usr/bin/env python3
"""Seeded CPython property oracle. The outer protocol contains JSON source text."""
import json, random, struct, math, pathlib, shlex, urllib.parse, io, tarfile, base64
rng=random.Random(42027)
rows=[]
for literal in ['1.0','1e2','1e16','0.1e-6','-0.0','12345678901234567890123','0','-0','1e-4','1e-5']:
 value=json.loads(literal)
 rows.append(dict(kind='json',source=literal,canonical=json.dumps(value,sort_keys=True,separators=(',',':')),unicode=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False),spaced=json.dumps(value,sort_keys=True)))
for n in range(12000):
 value=struct.unpack('>d',rng.randbytes(8))[0]
 if not math.isfinite(value):continue
 source=json.dumps(value)
 rows.append(dict(kind='json',source=source,canonical=source,unicode=source,spaced=source))
for n in range(1500):
 text=''.join(chr(rng.randrange(0x110000)) for _ in range(rng.randrange(20)))
 value={text:[text,10**rng.randrange(50),rng.random(),None,True], '\U00010000':'astral','\ue000':'bmp'}
 source=json.dumps(value)
 rows.append(dict(kind='json',source=source,canonical=json.dumps(value,sort_keys=True,separators=(',',':')),unicode=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False),spaced=json.dumps(value,sort_keys=True)))
for n in range(5000):
 text=''.join(rng.choice(['\r','\n','\r\n','\x1c','\x85','\u2028','x','\ufeff',chr(rng.randrange(0x110000))]) for _ in range(rng.randrange(15)))
 rows.append(dict(kind='text',text=text,strip=text.strip(),split=text.splitlines(),keep=text.splitlines(True),space=text.isspace(),digit=text.isdigit()))
for n in range(3000):
 text=''.join(rng.choice(['a','b','/','.','-','\U00010000']) for _ in range(rng.randrange(1,30)))
 rows.append(dict(kind='path',text=text,path=str(pathlib.PurePosixPath(text))))
for n in range(1500):
 words=[''.join(rng.choice("abc \t\n'\"\\$%_-é") for _ in range(rng.randrange(15))) for _ in range(rng.randrange(8))]
 rows.append(dict(kind='shlex',words=words,joined=shlex.join(words)))
for text in ['https://example.invalid/a%20b?q=x#y','git+ssh://example.invalid:22/repo','//host/a','file:///a/../b','HTTPS://Example.invalid:443/a','a%ff%20b','a%2Fb%ZZ','https://['+':'+':1]:42/a']:
 u=urllib.parse.urlsplit(text)
 rows.append(dict(kind='url',text=text,split=dict(scheme=u.scheme,netloc=u.netloc,path=u.path,query=u.query,fragment=u.fragment,port=u.port),unquote=urllib.parse.unquote(text)))
for n in range(20):
 buf=io.BytesIO()
 with tarfile.open(fileobj=buf,mode='w:gz' if n%2 else 'w',format=tarfile.USTAR_FORMAT) as tar:
  for j in range(5):
   data=rng.randbytes(rng.randrange(2000));entry=tarfile.TarInfo('dir/file'+str(j));entry.size=len(data);tar.addfile(entry,io.BytesIO(data))
 rows.append(dict(kind='tar',data=base64.b64encode(buf.getvalue()).decode()))
print(json.dumps(rows,ensure_ascii=True))
