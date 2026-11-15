import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import zlib from 'node:zlib';
import {createHash} from 'node:crypto';
import * as archive from '../../dist/archive.js';
// Which member archive_manifest takes for the package manifest: `re.fullmatch(r'[^/]+/package.json', name)` (archive.py:133).
// src/archive.ts spells that out without a pattern, because V8 answers the pattern with a RangeError on a long name that is
// not Latin-1. The pattern is the specification here: on short names the two must agree, and on long ones only one answers.
const SPEC=/^[^/]+\/package[^\n]json$/u;
const header=(name,size=0,type='0')=>{
 const block=Buffer.alloc(512);block.write(name,0,'latin1');
 block.write('0000644\0',100);block.write('0000000\0',108);block.write('0000000\0',116);
 block.write(size.toString(8).padStart(11,'0')+'\0',124);block.write('00000000000\0',136);block.fill(0x20,148,156);
 block.write(type,156);block.write('ustar\x0000',257,'latin1');
 block.write(block.reduce((sum,byte)=>sum+byte,0).toString(8).padStart(6,'0')+'\0 ',148);
 return block;
};
const padded=data=>Buffer.concat([data,Buffer.alloc(-data.length&511)]);
const MANIFEST=Buffer.from('{"name": "example-package", "version": "1.0.0"}');
// One member whose name is given by a pax `path` record, so that it may hold any bytes, with the manifest as its data.
const named=bytes=>{
 const body=Buffer.concat([Buffer.from(' path='),bytes,Buffer.from('\n')]);
 let length=body.length+1;
 while(String(length).length+body.length!==length)length++;
 const block=Buffer.concat([Buffer.from(String(length)),body]);
 return Buffer.concat([header('PaxHeader',block.length,'x'),padded(block),header('placeholder',MANIFEST.length),padded(MANIFEST),Buffer.alloc(1024)]);
};
const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'kitchen-manifest-name-'));
test.after(()=>fs.rmSync(temporary,{recursive:true,force:true}));
let serial=0;
const taken=bytes=>{
 const location=path.join(temporary,(serial++)+'.tgz'),data=zlib.gzipSync(named(bytes),{level:1});
 fs.writeFileSync(location,data);
 try{
  archive.archive_manifest(new Map(Object.entries({name:'example-package',archive:location,sha256:createHash('sha256').update(data).digest('hex'),kind:'artifact',version:'1.0.0'})));
  return true;
 }catch(error){
  if(error.constructor.name==='ArchiveError'&&error.message==='archive manifest required')return false;
  throw error;
 }finally{fs.rmSync(location);}
};
// A piece of a name as the bytes in the archive and as the text tarfile makes of them (a byte that is no UTF-8 is escaped).
const PIECES=[['/','/'],['package','package'],['.','.'],['json','json'],['\n','\n'],['x','x'],['package.json','package.json'],['packag','packag'],['e','e'],['jso','jso'],['n','n'],
 [Buffer.from('é'),'é'],[Buffer.from('一'),'一'],[Buffer.from('😀'),'😀'],[Buffer.from([0xff]),'\udcff'],[Buffer.from([0xed,0xa0,0x80]),'\udced\udca0\udc80'],['\r','\r'],[' ',' '],['',''],['\0','\0']];

test('the manifest name is one component, a slash, and package.json with any one character for the dot',()=>{
 const cases=[['package/package.json',true],['p/package.json',true],['/package.json',false],['package.json',false],['a/b/package.json',false],['a/package/json',true],['a/package\njson',false],
  ['a/packageXjson',true],['a/package.json/',true],['a/package.json//',true],['a/package.jsonx',false],['a/xpackage.json',false],['a/package..json',false],['a/packagejson',false],['a\nb/package.json',true],
  ['a/package\rjson',true],['a/package json',true],['a/package😀json',true],['a/package😀😀json',false],['一/package.json',true],['a/package一json',true],['a/PACKAGE.json',false],['a/package.json\n',false],['',false],['/',false],
  ['a/package.jsox',false],['a/package.JSON',false],['a/package.xson',false],['a/packagx.json',false],['a/xackage.json',false],['a/package.jso',false],['a/package.',false],['a/package',false],['a/',false],['a/packagejson.json',false],['a/json',false]];
 for(const [name,expected] of cases){
  // tarfile strips trailing slashes from a pax path before the name is looked at.
  assert.equal(SPEC.test(name.replace(/\/+$/u,'')),expected,'the specification on '+JSON.stringify(name));
  assert.equal(taken(Buffer.from(name)),expected,JSON.stringify(name));
 }
 // A byte that is no UTF-8 is one character: as the dot it is taken, and two of them are not.
 assert.equal(taken(Buffer.concat([Buffer.from('a/package'),Buffer.from([0xff]),Buffer.from('json')])),true);
 assert.equal(taken(Buffer.concat([Buffer.from('a/package'),Buffer.from([0xff,0xfe]),Buffer.from('json')])),false);
 assert.equal(taken(Buffer.concat([Buffer.from([0xff,0xfe]),Buffer.from('/package.json')])),true);
});

test('on generated names the spelled-out test answers as the pattern does',()=>{
 let state=0x9e3779b9;
 const random=bound=>{state^=state<<13;state>>>=0;state^=state>>>17;state^=state<<5;state>>>=0;return state%bound;};
 let matches=0,total=0;
 for(let round=0;round<600;round++){
  const parts=[],text=[];
  // Half of the names are built around a match, so that near misses on either side of it are common.
  const pieces=round%2?[PIECES[random(PIECES.length)],['/','/'],random(8)?['package','package']:PIECES[random(PIECES.length)],PIECES[random(PIECES.length)],random(8)?['json','json']:PIECES[random(PIECES.length)]]:Array.from({length:1+random(6)},()=>PIECES[random(PIECES.length)]);
  if(round%2&&random(4)===0)pieces.splice(random(pieces.length+1),0,PIECES[random(PIECES.length)]);
  for(const [bytes,decoded] of pieces){parts.push(Buffer.from(bytes));text.push(decoded);}
  const name=text.join('').replace(/\/+$/u,''),expected=SPEC.test(name);
  assert.equal(taken(Buffer.concat(parts)),expected,JSON.stringify(name));
  matches+=expected;total++;
 }
 assert.ok(matches>60&&total-matches>60,matches+' of '+total+' generated names match');
});

test('a name of nine million characters that are not Latin-1 is answered, where the pattern is a RangeError',()=>{
 const long=9_000_000;
 for(const unit of [Buffer.from('一'),Buffer.from([0xff])]){
  const run=Buffer.alloc(long*unit.length,unit);
  assert.equal(taken(Buffer.concat([run,Buffer.from('/package.json')])),true);
  assert.equal(taken(Buffer.concat([run,Buffer.from('/package.jsonx')])),false);
  assert.equal(taken(Buffer.concat([Buffer.from('a/package.json'),run])),false);
  assert.equal(taken(run),false);
 }
 // A long run where the one character belongs is refused without being taken apart.
 assert.equal(taken(Buffer.concat([Buffer.from('a/package'),Buffer.alloc(3*long,Buffer.from('一')),Buffer.from('json')])),false);
 // The pattern itself on such a name, on this runtime: the reason it is not used.
 assert.throws(()=>SPEC.test('一'.repeat(long)+'/package.json'),RangeError);
});
