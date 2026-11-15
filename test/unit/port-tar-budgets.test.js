import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import v8 from 'node:v8';
import zlib from 'node:zlib';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import * as tarfile from '../../dist/pylike/tarfile.js';
import * as archive from '../../dist/archive.js';
// The admission budgets of the tar reader (divergence D4) on bare tar streams, with budgets small enough to meet from
// both sides in a few blocks. The production values are met by test/differential/port_budget.py, against the oracle;
// the last tests here hold that archive_manifest really reads under them, and under the heap they are sized for.
const header=(name,size=0,type='0',patch=[])=>{
 const block=Buffer.alloc(512);block.write(name,0,'latin1');
 block.write('0000644\0',100);block.write('0000000\0',108);block.write('0000000\0',116);
 block.write(size.toString(8).padStart(11,'0')+'\0',124);block.write('00000000000\0',136);block.fill(0x20,148,156);
 block.write(type,156);block.write('ustar\x0000',257,'latin1');
 for(const [at,bytes] of patch)Buffer.from(bytes,'latin1').copy(block,at);
 block.write(block.reduce((sum,byte)=>sum+byte,0).toString(8).padStart(6,'0')+'\0 ',148);
 return block;
};
const padded=data=>Buffer.concat([data,Buffer.alloc(-data.length&511)]);
const file=(name,text='')=>Buffer.concat([header(name,Buffer.byteLength(text)),padded(Buffer.from(text))]);
const END=Buffer.alloc(1024);
const record=(keyword,value)=>{
 const body=' '+keyword+'='+value+'\n';
 let length=body.length+1;
 while(String(length).length+body.length!==length)length++;
 return Buffer.from(length+body,'latin1');
};
const pax=(records,type='x')=>{const body=Buffer.concat(records);return Buffer.concat([header('PaxHeader',body.length,type),padded(body)]);};
const longName=(size,type='L')=>Buffer.concat([header('././@LongLink',size,type),padded(Buffer.alloc(size,0x61))]);
const tar=(...parts)=>Buffer.concat([...parts,END]);
const numbers=count=>Array(count).fill('7').join(',');
// One sparse map of `regions` regions in each of the four forms, and the member it belongs to.
const sparse={
 '0.1':regions=>Buffer.concat([pax([record('GNU.sparse.map',numbers(2*regions))]),file('sparse')]),
 '0.0':regions=>Buffer.concat([pax([record('GNU.sparse.size','0'),...Array(regions).fill([record('GNU.sparse.offset','7'),record('GNU.sparse.numbytes','7')]).flat()]),file('sparse')]),
 '1.0':regions=>Buffer.concat([pax([record('GNU.sparse.major','1'),record('GNU.sparse.minor','0')]),file('sparse',regions+'\n'+'7\n'.repeat(2*regions))]),
 // An old GNU header holds four structs itself, zero or not; the rest sit in extension blocks of up to 21.
 old:regions=>{
  const pair='00000000007\x0000000000007\0',rest=regions-4,blocks=Math.ceil(rest/21);
  const parts=[header('sparse',0,'S',[[482,blocks?'\x01':'\0'],[483,'00000000000\0']])];
  for(let index=0;index<blocks;index++){
   const count=Math.min(21,rest-21*index),block=Buffer.alloc(512);
   block.write(pair.repeat(count),0,'latin1');block[504]=index+1<blocks?1:0;
   parts.push(block);
  }
  return Buffer.concat(parts);
 },
};
const outcome=(stream,budgets)=>{
 try{return tarfile.tarMembers(stream,{budgets}).length;}
 catch(error){return error instanceof tarfile.MetadataBudget?'refused: '+error.budget:error?.pyName??String(error);}
};
// `build(count)` makes an archive whose listing brings the count of `budget` to exactly `count`.
const sides=(budget,bound,build,what)=>{
 test(what+': one under the budget and at it the archive is listed, one over it is refused',()=>{
  for(const count of [bound-1,bound]){
   const listed=outcome(build(count),{[budget]:bound});
   assert.equal(typeof listed,'number',what+' at '+count+' of '+bound+': '+listed);
  }
  assert.equal(outcome(build(bound+1),{[budget]:bound}),'refused: '+budget);
  // The same archive against a budget of exactly what it holds, and of one less: the count is the one stated.
  assert.equal(typeof outcome(build(bound+1),{[budget]:bound+1}),'number');
  assert.equal(outcome(build(bound),{[budget]:bound-1}),'refused: '+budget);
 });
};

test('the production budgets, the heap they are sized for, and the refusal',()=>{
 assert.deepEqual({...tarfile.BUDGETS},{metadataBytes:2**24,paxRecords:2**21,sparseNumbers:2**16,members:2**17});
 assert.ok(Object.isFrozen(tarfile.BUDGETS));
 // The limit of D3 is a bound on one block and lies above the byte budget: archive_manifest never reads a block of that size.
 assert.equal(tarfile.METADATA_LIMIT,2n**28n);
 assert.ok(BigInt(tarfile.BUDGETS.metadataBytes)<tarfile.METADATA_LIMIT);
 // The pax-record budget can be met within the byte budget: the shortest record, `5 x=\n`, is five bytes.
 assert.ok(5*tarfile.BUDGETS.paxRecords<=tarfile.BUDGETS.metadataBytes);
 assert.equal(tarfile.HEAP_LIMIT_REQUIRED_MIB,2048);
 const messages={metadataBytes:'tar metadata budget exceeded: metadata bytes',paxRecords:'tar metadata budget exceeded: pax records',
  sparseNumbers:'tar metadata budget exceeded: sparse numbers',members:'tar metadata budget exceeded: members'};
 for(const [budget,message] of Object.entries(messages)){
  const refusal=new tarfile.MetadataBudget(budget);
  assert.equal(refusal.message,message);
  assert.equal(refusal.budget,budget);
  assert.equal(refusal.pyName,'OSError');
  assert.ok(!(refusal instanceof tarfile.TarError)&&!(refusal instanceof tarfile.MetadataLimit));
 }
 // The message is the same whatever the archive holds: it has no number and no name in it.
 const thrown=[];
 for(const stream of [tar(file('a'),file('b')),tar(file('other-name'),file('x'.repeat(90)),file('c'))]){
  try{tarfile.tarMembers(stream,{budgets:{members:1}});}catch(error){thrown.push(error.message);}
 }
 assert.deepEqual(thrown,[messages.members,messages.members]);
 assert.doesNotMatch(messages.members+messages.metadataBytes+messages.paxRecords+messages.sparseNumbers,/[0-9]/u);
});

sides('members',8,count=>tar(...Array(count).fill(file('member'))),'members');
sides('paxRecords',16,count=>tar(pax(Array(count).fill(record('x',''))),file('a')),'pax records of one header');
sides('paxRecords',16,count=>tar(pax(Array(5).fill(record('x','')),'g'),file('a'),pax(Array(5).fill(record('comment','c'))),file('b'),pax(Array(count-10).fill(record('x','')),'X'),file('c')),'pax records of a global, an extended and a Solaris header together');
// A long name that states `size` bytes counts `size`, whatever the 512-byte blocks round it to.
sides('metadataBytes',4096,count=>tar(longName(count),file('named')),'metadata bytes of one long name');
sides('metadataBytes',4096,count=>tar(longName(1000),file('a'),longName(1000,'K'),file('b'),pax([record('path','p'.repeat(count-2000-11))]),file('c')),'metadata bytes of a long name, a long link and a pax header together');
sides('metadataBytes',4096,count=>tar(...Array(8).fill([pax([record('comment','c'.repeat(500-13))],'g'),file('m')]).flat(),longName(count-4000),file('z')),'metadata bytes of eight global headers that replace one another: nothing is credited back');
// The two pax forms that list their numbers one by one can end on any count.
sides('sparseNumbers',64,count=>tar(file('first'),pax([record('GNU.sparse.map',numbers(count))]),file('s')),'sparse numbers of a map of form 0.1');
sides('sparseNumbers',64,count=>tar(file('first'),pax([record('GNU.sparse.size','0'),...Array(count).fill(0).map((_,index)=>record(index%2?'GNU.sparse.numbytes':'GNU.sparse.offset','7'))]),file('s')),'sparse numbers of records of form 0.0');

test('a region is two numbers in every sparse form: 32 regions are 64 numbers, no more and no less',()=>{
 for(const form of ['0.1','0.0','1.0','old']){
  const stream=tar(file('first'),sparse[form](32));
  assert.equal(outcome(stream,{sparseNumbers:64}),2,form);
  assert.equal(outcome(stream,{sparseNumbers:63}),'refused: sparseNumbers',form);
  assert.equal(outcome(tar(file('first'),sparse[form](33)),{sparseNumbers:65}),'refused: sparseNumbers',form);
  assert.equal(outcome(tar(file('first'),sparse[form](33)),{sparseNumbers:66}),2,form);
 }
 // An odd count of numbers is possible in the two pax forms that list numbers one by one, and is counted as it stands.
 assert.equal(outcome(tar(pax([record('GNU.sparse.map',numbers(65))]),file('s')),{sparseNumbers:65}),1);
 assert.equal(outcome(tar(pax([record('GNU.sparse.map',numbers(65))]),file('s')),{sparseNumbers:64}),'refused: sparseNumbers');
 assert.equal(outcome(tar(pax([record('GNU.sparse.size','0'),record('GNU.sparse.offset','7'),record('GNU.sparse.offset','7'),record('GNU.sparse.numbytes','7')]),file('s')),{sparseNumbers:3}),1);
 assert.equal(outcome(tar(pax([record('GNU.sparse.size','0'),record('GNU.sparse.offset','7'),record('GNU.sparse.offset','7'),record('GNU.sparse.numbytes','7')]),file('s')),{sparseNumbers:2}),'refused: sparseNumbers');
});

test('a sparse map in a global header is counted every time it is expanded',()=>{
 // tarfile expands the map for the member after the global header and again for every later extended header.
 const amplified=members=>tar(pax([record('GNU.sparse.map',numbers(10))],'g'),file('first'),...Array(members).fill([pax([record('comment','x')]),file('next')]).flat());
 assert.equal(outcome(amplified(5),{sparseNumbers:60}),6);
 assert.equal(outcome(amplified(5),{sparseNumbers:59}),'refused: sparseNumbers');
 assert.equal(outcome(amplified(6),{sparseNumbers:60}),'refused: sparseNumbers');
 // A member without an extended header of its own expands nothing.
 assert.equal(outcome(tar(pax([record('GNU.sparse.map',numbers(10))],'g'),file('first'),...Array(50).fill(file('plain'))),{sparseNumbers:10}),51);
 // The offset and numbytes records of form 0.0 are read from their own header only: a global one is never expanded again.
 const global00=tar(pax([record('GNU.sparse.size','0'),record('GNU.sparse.offset','7'),record('GNU.sparse.numbytes','7')],'g'),file('first'),...Array(20).fill([pax([record('comment','x')]),file('next')]).flat());
 assert.equal(outcome(global00,{sparseNumbers:2}),21);
});

test('the blocks of a sparse map are metadata bytes: form 1.0 and old GNU extension blocks, 512 each',()=>{
 // Form 1.0, 300 regions: the count line and 600 lines of two bytes are 1,204 bytes, three blocks.
 const stream=tar(file('first'),sparse['1.0'](300));
 const own=Buffer.concat([record('GNU.sparse.major','1'),record('GNU.sparse.minor','0')]).length;
 assert.equal(outcome(stream,{metadataBytes:own+3*512}),2);
 assert.equal(outcome(stream,{metadataBytes:own+3*512-1}),'refused: metadataBytes');
 // An old GNU header with 46 structs: four in the header, 42 in two extension blocks.
 assert.equal(outcome(tar(file('first'),sparse.old(46)),{metadataBytes:1024}),2);
 assert.equal(outcome(tar(file('first'),sparse.old(46)),{metadataBytes:1023}),'refused: metadataBytes');
 // Extension blocks whose structs are zero add no numbers, and are counted as bytes all the same.
 const zeros=Buffer.alloc(512);zeros[504]=1;
 const chain=tar(header('sparse',0,'S',[[482,'\x01'],[483,'00000000000\0']]),...Array(9).fill(zeros),Buffer.alloc(512));
 assert.equal(outcome(chain,{sparseNumbers:8,metadataBytes:5120}),1);
 assert.equal(outcome(chain,{sparseNumbers:8,metadataBytes:5119}),'refused: metadataBytes');
 assert.equal(outcome(chain,{sparseNumbers:7}),'refused: sparseNumbers');
});

test('a refusal stands where the count is passed: what tarfile refuses before that point comes first',()=>{
 const map=list=>tar(pax([record('GNU.sparse.map',list)]),file('s'));
 // An invalid number among the first four is tarfile's ValueError; behind the budget it is never looked at.
 assert.equal(outcome(map('7,7,x,7,7,7'),{sparseNumbers:4}),'ValueError');
 assert.equal(outcome(map('7,7,7,7,7,x'),{sparseNumbers:4}),'refused: sparseNumbers');
 assert.equal(outcome(map('7,7,7,7,x'),{sparseNumbers:4}),'ValueError');
 assert.equal(outcome(map(''),{sparseNumbers:0}),'ValueError');
 // Form 1.0 states a count of regions that its data need not hold. The numbers are counted as they are read, so a count
 // without the data is tarfile's ValueError as before, however large it is, and the budget is met only by numbers that are there.
 const stated=(regions,lines)=>tar(pax([record('GNU.sparse.major','1'),record('GNU.sparse.minor','0')]),file('s',regions+'\n'+'7\n'.repeat(lines)));
 assert.equal(outcome(stated('9'.repeat(40),3),{sparseNumbers:1000}),'ValueError');
 assert.equal(outcome(stated('9'.repeat(40),3),{sparseNumbers:3}),'ValueError');
 assert.equal(outcome(stated('9'.repeat(40),3),{sparseNumbers:2}),'refused: sparseNumbers');
 assert.equal(outcome(stated('x',3),{sparseNumbers:0}),'ValueError');
 assert.equal(outcome(stated('-1',3),{sparseNumbers:0}),1);
 // Form 0.0: a record that is no number is "invalid header" where tarfile says so, in the order of the records.
 const records=values=>tar(file('first'),pax([record('GNU.sparse.size','0'),...values.map((value,index)=>record(index%2?'GNU.sparse.numbytes':'GNU.sparse.offset',value))]),file('s'));
 assert.equal(outcome(records(['7','7','x','7']),{sparseNumbers:4}),1);  // InvalidHeaderError behind the first member ends the listing silently
 assert.equal(outcome(records(['7','7','7','x']),{sparseNumbers:2}),'refused: sparseNumbers');
 // A pax record that is not framed is "invalid header" before it is counted against anything but the records.
 assert.equal(outcome(tar(file('first'),pax([record('x',''),record('x',''),Buffer.from('4 =\n')]),file('s')),{paxRecords:3}),1);
 assert.equal(outcome(tar(file('first'),pax([record('x',''),record('x',''),Buffer.from('4 =\n')]),file('s')),{paxRecords:2}),'refused: paxRecords');
 // D3 is asked before the byte budget is charged and keeps its own refusal, whatever the budget.
 const above=Buffer.concat([file('first'),header('meta',2**28+1,'L')]);
 assert.ok((()=>{try{tarfile.tarMembers(above,{budgets:{metadataBytes:0}});}catch(error){return error instanceof tarfile.MetadataLimit;}return false;})());
 // A negative size is tarfile's "invalid offset" and counts nothing.
 const negative=header('meta',0,'L');negative.fill(0xff,124,136);negative.fill(0x20,148,156);
 negative.write(negative.reduce((sum,byte)=>sum+byte,0).toString(8).padStart(6,'0')+'\0 ',148);
 assert.equal(outcome(negative,{metadataBytes:0}),'ReadError');
});

test('a budget of zero admits nothing of its kind, and the other kinds as before',()=>{
 assert.equal(outcome(tar(file('a')),{members:0}),'refused: members');
 assert.equal(outcome(END,{members:0}),0);
 assert.equal(outcome(tar(file('a'),file('b')),{metadataBytes:0,paxRecords:0,sparseNumbers:0}),2);
 assert.equal(outcome(tar(pax([]),file('a')),{metadataBytes:0,paxRecords:0}),1);
 assert.equal(outcome(tar(pax([record('x','')]),file('a')),{metadataBytes:5,paxRecords:0}),'refused: paxRecords');
 assert.equal(outcome(tar(pax([record('x','')]),file('a')),{metadataBytes:4,paxRecords:1}),'refused: metadataBytes');
 for(const bad of [{members:-1},{members:1.5},{paxRecords:2**53},{sparseNumbers:Number.NaN},{metadataBytes:'9'}]){
  assert.throws(()=>tarfile.tarMembers(END,{budgets:bad}),error=>!(error instanceof tarfile.MetadataBudget)&&/a budget is a non-negative safe integer/u.test(error.message));
 }
});

test('the gzip reader counts as the tar reader does',()=>{
 const packed=zlib.gzipSync(tar(...Array(9).fill(file('member'))));
 assert.equal(tarfile.gzipMembers(packed,{budgets:{members:9}}).length,9);
 assert.throws(()=>tarfile.gzipMembers(packed,{budgets:{members:8}}),error=>error instanceof tarfile.MetadataBudget&&error.budget==='members');
 assert.equal(tarfile.gzipMembers(packed).length,9);
});

test('a name in a global header is one text among the members it reaches: no member holds a copy',()=>{
 // A path of 4 MiB with a trailing slash in a global header, and 2,000 members. tarfile strips the slash for each member; here
 // each member's name is a view of the one text. 2,000 copies would be 8 GiB; the listing keeps the text once and a little a member.
 const script=`import * as tarfile from ${JSON.stringify(new URL('../../dist/pylike/tarfile.js',import.meta.url).href)};
const header=${header.toString()};const padded=${padded.toString()};
const value=Buffer.concat([Buffer.alloc(4*1024*1024,0x70),Buffer.from('/')]);
const body=Buffer.concat([Buffer.from((value.length+14)+' path='),value,Buffer.from('\\n')]);
const stream=Buffer.concat([header('PaxHeader',body.length,'g'),padded(body),...Array(2000).fill(header('member')),Buffer.alloc(1024)]);
globalThis.gc();const before=process.memoryUsage().heapUsed;
const members=tarfile.tarMembers(stream);
globalThis.gc();
console.log(JSON.stringify({members:members.length,length:members[1999].name.length,same:members[0].name===members[1999].name,keptMiB:(process.memoryUsage().heapUsed-before)/1048576}));`;
 const done=spawnSync(process.execPath,['--expose-gc','--input-type=module','-e',script],{encoding:'utf8'});
 assert.equal(done.stderr,'');
 const report=JSON.parse(done.stdout);
 assert.deepEqual([report.members,report.length,report.same],[2000,4*1024*1024,true]);
 assert.ok(report.keptMiB<16,report.keptMiB+' MiB kept');
});

test('a size in a global header is the same for every member it reaches, and one integer among them',()=>{
 const members=tarfile.tarMembers(tar(pax([record('GNU.sparse.realsize','5')],'g'),file('a','xx'),file('b'),pax([record('GNU.sparse.realsize','3')]),file('c'),file('d'),pax([record('GNU.sparse.realsize','5')],'x'),file('e'),
  pax([record('GNU.sparse.realsize','9')],'g'),file('f'),pax([record('size','x')],'g'),file('g')));
 assert.deepEqual(members.map(member=>[member.name,member.size]),[['a',5n],['b',5n],['c',3n],['d',5n],['e',5n],['f',9n],['g',0n]]);
 assert.equal(outcome(tar(pax([record('GNU.sparse.realsize','x')],'g'),file('a')),{}),'ValueError');
 // 20,000 members under a global size of 4,300 digits: one integer of 1.8 kB, not 20,000 of them (36 MiB).
 const script=`import * as tarfile from ${JSON.stringify(new URL('../../dist/pylike/tarfile.js',import.meta.url).href)};
const header=${header.toString()};const padded=${padded.toString()};const record=${record.toString()};
const body=record('GNU.sparse.realsize','7'.repeat(4300));
const stream=Buffer.concat([header('PaxHeader',body.length,'g'),padded(body),...Array(20000).fill(header('member')),Buffer.alloc(1024)]);
globalThis.gc();const before=process.memoryUsage().heapUsed;
const members=tarfile.tarMembers(stream);
globalThis.gc();
console.log(JSON.stringify({members:members.length,digits:members[19999].size.toString().length,keptMiB:(process.memoryUsage().heapUsed-before)/1048576}));`;
 const done=spawnSync(process.execPath,['--expose-gc','--input-type=module','-e',script],{encoding:'utf8'});
 assert.equal(done.stderr,'');
 const report=JSON.parse(done.stdout);
 assert.equal(report.members,20000);
 assert.equal(report.digits,4300);
 assert.ok(report.keptMiB<20,report.keptMiB+' MiB kept');
});

const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'kitchen-budget-'));
test.after(()=>fs.rmSync(temporary,{recursive:true,force:true}));
const manifestOf=(name,stream)=>{
 const location=path.join(temporary,name+'.tgz'),data=zlib.gzipSync(stream,{level:1});
 fs.writeFileSync(location,data);
 try{return Object.fromEntries(archive.archive_manifest(new Map(Object.entries({name:'example-package',archive:location,sha256:createHash('sha256').update(data).digest('hex'),kind:'artifact',version:'1.0.0'}))));}
 catch(error){return error.constructor.name+': '+error.message;}
};
const MANIFEST=file('package/package.json','{"name": "example-package", "version": "1.0.0"}');
test('archive_manifest reads under the production budgets: sparse numbers',()=>{
 const accepted={name:'example-package',version:'1.0.0'},refused='ArchiveError: unreadable archive or manifest';
 const map=count=>tar(MANIFEST,pax([record('GNU.sparse.map',numbers(count))]),file('package/sparse.bin'));
 assert.deepEqual(manifestOf('sparse-at',map(2**16)),accepted);
 assert.equal(manifestOf('sparse-above',map(2**16+1)),refused);
 // The same sum by amplification: a global map of 1,024 numbers, expanded 64 times, then once more.
 const amplified=members=>tar(MANIFEST,pax([record('GNU.sparse.map',numbers(1024))],'g'),file('package/first'),Buffer.concat(Array(members).fill(Buffer.concat([pax([record('comment','x')]),file('package/next')]))));
 assert.deepEqual(manifestOf('amplified-at',amplified(63)),accepted);
 assert.equal(manifestOf('amplified-above',amplified(64)),refused);
});
test('archive_manifest reads under the production budgets: metadata bytes',()=>{
 const accepted={name:'example-package',version:'1.0.0'},refused='ArchiveError: unreadable archive or manifest';
 // One long name of exactly 2**24 bytes, and of one byte more.
 const named=size=>tar(MANIFEST,longName(size),file('package/named'));
 assert.deepEqual(manifestOf('bytes-at',named(2**24)),accepted);
 assert.equal(manifestOf('bytes-above',named(2**24+1)),refused);
 // The same sum over several blocks: fifteen long names of 1 MiB and a pax header that brings it to the budget, then one byte more.
 const spread=last=>tar(MANIFEST,...Array(15).fill([longName(2**20),file('package/named')]).flat(),pax([record('comment','c'.repeat(last-17))]),file('package/last'));
 assert.deepEqual([record('comment','c'.repeat(2**20-17)).length,record('comment','c'.repeat(2**20-16)).length],[2**20,2**20+1]);
 assert.deepEqual(manifestOf('bytes-spread-at',spread(2**20)),accepted);
 assert.equal(manifestOf('bytes-spread-above',spread(2**20+1)),refused);
});
test('archive_manifest reads under the production budgets: pax records',()=>{
 // One extended header of 2**21 records `5 x=\n`, the shortest there is: 10 MiB of the 16 MiB the byte budget admits.
 const records=count=>tar(MANIFEST,Buffer.concat([header('PaxHeader',5*count,'x'),padded(Buffer.alloc(5*count,'5 x=\n','latin1'))]),file('package/described'));
 assert.deepEqual(manifestOf('records-at',records(2**21)),{name:'example-package',version:'1.0.0'});
 assert.equal(manifestOf('records-above',records(2**21+1)),'ArchiveError: unreadable archive or manifest');
});
test('archive_manifest reads under the production budgets: members',()=>{
 const members=count=>Buffer.concat([MANIFEST,Buffer.alloc(512*(count-1),header('package/member.js')),END]);
 assert.deepEqual(manifestOf('members-at',members(2**17)),{name:'example-package',version:'1.0.0'});
 assert.equal(manifestOf('members-above',members(2**17+1)),'ArchiveError: unreadable archive or manifest');
});

test('the reader refuses to list under a heap limit below the one its budgets are sized for',async()=>{
 const required=tarfile.HEAP_LIMIT_REQUIRED_MIB;
 assert.ok(v8.getHeapStatistics().heap_size_limit>=required*2**20,'this suite runs under a heap limit of '+Math.floor(v8.getHeapStatistics().heap_size_limit/2**20)+' MiB, below the '+required+' MiB the reader requires: run it with node --max-old-space-size='+required);
 const script=`import v8 from 'node:v8';import zlib from 'node:zlib';
import * as tarfile from ${JSON.stringify(new URL('../../dist/pylike/tarfile.js',import.meta.url).href)};
import * as archive from ${JSON.stringify(new URL('../../dist/archive.js',import.meta.url).href)};
const outcome=run=>{try{return 'listed '+run().length;}catch(error){return error.name+'|'+('pyName' in error)+'|'+error.message;}};
const entry=new Map(Object.entries({name:'example-package',archive:process.argv[1],sha256:process.argv[2],kind:'artifact',version:'1.0.0'}));
let manifest;try{manifest='manifest '+archive.archive_manifest(entry).get('name');}catch(error){manifest=error.name+'|'+error.constructor.name;}
console.log(JSON.stringify({limit:Math.floor(v8.getHeapStatistics().heap_size_limit/1048576),tar:outcome(()=>tarfile.tarMembers(Buffer.alloc(1024))),gzip:outcome(()=>tarfile.gzipMembers(zlib.gzipSync(Buffer.alloc(1024)))),manifest}));`;
 const location=path.join(temporary,'small.tgz'),data=zlib.gzipSync(tar(MANIFEST));
 fs.writeFileSync(location,data);
 const under=size=>{
  const done=spawnSync(process.execPath,['--max-old-space-size='+size,'--input-type=module','-e',script,location,createHash('sha256').update(data).digest('hex')],{encoding:'utf8'});
  assert.equal(done.stderr,'');
  return JSON.parse(done.stdout);
 };
 const message='UnsupportedRuntime|false|unsupported runtime: the V8 heap limit is below the 2048 MiB the tar reader is sized for; start Node with --max-old-space-size=2048';
 // The limit is the old space the flag sets plus the young generation, which is 48 MiB on Node 22.23.3 and 192 MiB on 24.14.0
 // on this host: under every flag here the limit is below the requirement on both.
 for(const size of [256,1024,1536,1800]){
  const report=under(size);
  assert.ok(report.limit<required,size+' gives '+report.limit);
  // Not an archive refusal: no class of the engine, and archive_manifest lets it escape.
  assert.deepEqual([report.tar,report.gzip,report.manifest],[message,message,'UnsupportedRuntime|UnsupportedRuntime'],String(size));
 }
 for(const size of [required,4096]){
  const report=under(size);
  assert.ok(report.limit>=required);
  assert.deepEqual([report.tar,report.gzip,report.manifest],['listed 0','listed 0','manifest example-package'],String(size));
 }
});
