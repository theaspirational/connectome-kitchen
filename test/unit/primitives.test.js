import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import * as json from '../../dist/pylike/json.js';
import * as text from '../../dist/pylike/text.js';
import * as paths from '../../dist/pylike/path.js';
import * as shlex from '../../dist/pylike/shlex.js';
import * as url from '../../dist/pylike/url.js';
import * as regex from '../../dist/pylike/regex.js';
import {readTar} from '../../dist/pylike/tar.js';
const python=spawnSync('python3',['test/differential/primitives.py'],{encoding:'utf8',maxBuffer:64*1024*1024});
assert.equal(python.status,0,python.stderr);
const rows=JSON.parse(python.stdout);
for(const kind of ['json','text','path','shlex','url','tar'])test('CPython properties: '+kind,()=>{
 let count=0;
 for(const row of rows.filter(x=>x.kind===kind)) {
  count++;
  if(kind==='json') {const v=json.loads(row.source);assert.equal(json.canonical(v),row.canonical);assert.equal(json.unicode(v),row.unicode);assert.equal(json.spaced(v),row.spaced);}
  if(kind==='text') {assert.equal(text.strip(row.text),row.strip);assert.deepEqual(text.splitlines(row.text),row.split);assert.deepEqual(text.splitlines(row.text,true),row.keep);assert.equal(text.isspace(row.text),row.space);assert.equal(text.isdigit(row.text),row.digit);}
  if(kind==='path')assert.equal(paths.path(row.text),row.path);
  if(kind==='shlex'){assert.equal(shlex.join(row.words),row.joined);assert.deepEqual(shlex.split(row.joined),row.words);}
  if(kind==='url'){assert.deepEqual(url.urlsplit(row.text),row.split);assert.equal(url.unquote(row.text),row.unquote);}
  if(kind==='tar'){const bytes=Buffer.from(row.data,'base64');assert.equal(readTar(bytes).length,5);const bad=Buffer.from(bytes);bad[0]^=127;assert.throws(()=>readTar(bad));assert.throws(()=>readTar(bytes,{maxBytes:1}));assert.throws(()=>readTar(bytes,{maxMembers:1}));}
 }
 console.log(kind+' property cases: '+count);
});
test('strict number kind, duplicate keys, non-finite waiver and grammar',()=>{
 for(const source of ['{"x":1,"x":2}','[1,]','{"x":1,}','01','NaN','Infinity','1E400','-1e400','"\n"','[','true false','{"__proto__":0,"__proto__":1}'])assert.throws(()=>json.loads(source));
 assert.ok(json.loads('1') instanceof json.Int);assert.ok(json.loads('1.0') instanceof json.Float);
 assert.equal(json.canonical(json.loads('{"__proto__":1}')),'{"__proto__":1}');
 assert.throws(()=>json.dumps(1));assert.throws(()=>json.loads('['.repeat(600)+'0'+']'.repeat(600)));
});
test('Python path sort and regex semantics',()=>{
 assert.deepEqual(['/a-b','/a/b'].sort(paths.comparePaths),['/a/b','/a-b']);
 assert.equal(paths.path('//a/./b/../c'),'//a/b/../c');
 assert.ok(regex.digit('٤'));assert.ok(regex.word('²'));assert.ok(!regex.digit('²'));
 assert.ok(regex.dollarMatch('a','a\n'));assert.ok(!regex.fullmatch('a','a\n'));
});
test('URL malformed authorities refuse without WHATWG normalization',()=>{
 for(const source of ['https://[junk]/','https://[vZ.a]/','https://example.invalid:a:2/a','https://example.invalid:70000/a','https://example.invalid：22/a'])assert.throws(()=>url.urlsplit(source));
});
