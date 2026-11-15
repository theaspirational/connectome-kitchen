import test from 'node:test';
import assert from 'node:assert/strict';
import * as tarfile from '../../dist/pylike/tarfile.js';
// The metadata limit of the tar reader (divergence D3) on a bare tar stream, and the cost of what stays below it.
// The archives that really hold a block at the limit are built by test/differential/port_large.py.
const header=(name,size,type='0')=>{
 const block=Buffer.alloc(512);block.write(name,0,'latin1');
 block.write('0000644\0',100);block.write('0000000\0',108);block.write('0000000\0',116);
 block.write(size.toString(8).padStart(11,'0')+'\0',124);block.write('00000000000\0',136);block.fill(0x20,148,156);
 block.write(type,156);block.write('ustar\x0000',257,'latin1');
 block.write(block.reduce((sum,byte)=>sum+byte,0).toString(8).padStart(6,'0')+'\0 ',148);
 return block;
};
const padded=data=>Buffer.concat([data,Buffer.alloc(-data.length&511)]);
const file=(name,text)=>Buffer.concat([header(name,Buffer.byteLength(text)),padded(Buffer.from(text))]);
const END=Buffer.alloc(1024);
const LIMIT=2**28;
const record=(keyword,value)=>{
 const body=' '+keyword+'='+value+'\n';
 let length=body.length+1;
 while(String(length).length+body.length!==length)length++;
 return Buffer.from(length+body,'latin1');
};
const pax=(records,type='x')=>{const body=Buffer.concat(records);return Buffer.concat([header('PaxHeader',body.length,type),padded(body)]);};
const outcome=run=>{try{return run();}catch(error){return error;}};

test('the limit is 2**28 bytes of the size field, and the diagnostic is constant',()=>{
 assert.equal(tarfile.METADATA_LIMIT,268435456n);
 const refusal=new tarfile.MetadataLimit();
 assert.equal(refusal.message,'tar metadata block above 268435456 bytes');
 assert.equal(refusal.pyName,'OSError');
 assert.ok(!(refusal instanceof tarfile.TarError));
});

for(const [type,what] of [['L','long name'],['K','long link'],['x','pax extended'],['g','pax global'],['X','Solaris extended']]){
 test('a '+what+' header one byte above the limit is refused where its blocks would be read; at the limit it is read',()=>{
  // Nothing follows either header. At the limit the reader reads a short block and goes on as tarfile does; above it, it refuses.
  const first=file('package/package.json','{}');
  const above=outcome(()=>tarfile.tarMembers(Buffer.concat([first,header('meta',LIMIT+1,type)])));
  assert.ok(above instanceof tarfile.MetadataLimit,String(above));
  const at=outcome(()=>tarfile.tarMembers(Buffer.concat([first,header('meta',LIMIT,type)])));
  assert.ok(at instanceof tarfile.ReadError,String(at));
  // As the first member too, where tarfile turns header errors into ReadError: the refusal is not one of them.
  assert.ok(outcome(()=>tarfile.tarMembers(header('meta',LIMIT+1,type))) instanceof tarfile.MetadataLimit);
  assert.ok(outcome(()=>tarfile.tarMembers(header('meta',LIMIT,type))) instanceof tarfile.ReadError);
 });
}

test('a header of another type may state any size',()=>{
 // A regular member above the limit whose data is missing: tarfile's own refusal, not the limit's.
 const short=outcome(()=>tarfile.tarMembers(Buffer.concat([file('package/package.json','{}'),header('package/big.bin',LIMIT+1),END])));
 assert.ok(short instanceof tarfile.ReadError&&short.message==='unexpected end of data',String(short));
 // A directory states a size that tarfile does not skip: the listing goes on.
 const members=tarfile.tarMembers(Buffer.concat([file('package/package.json','{}'),header('package/dir',LIMIT+1,'5'),END]));
 assert.deepEqual(members.map(member=>[member.name,member.size]),[['package/package.json',2n],['package/dir',BigInt(LIMIT)+1n]]);
});

test('what the oracle refuses about the header itself still comes first',()=>{
 // A negative size (base 256) is "invalid offset" in tarfile before any read; as the first member that is a ReadError.
 const negative=header('meta',0,'L');
 negative.fill(0xff,124,136);negative.fill(0x20,148,156);
 negative.write(negative.reduce((sum,byte)=>sum+byte,0).toString(8).padStart(6,'0')+'\0 ',148);
 assert.ok(outcome(()=>tarfile.tarMembers(negative)) instanceof tarfile.ReadError);
 // A chain of extension headers deeper than the frame limit is RecursionError before the last one is looked at.
 const chain=[];
 for(let index=0;index<400;index++)chain.push(header('meta',1,'L'),padded(Buffer.from('a')));
 const deep=outcome(()=>tarfile.tarMembers(Buffer.concat([...chain,header('meta',LIMIT+1,'L')])));
 assert.equal(deep?.pyName,'RecursionError');
});

test('a long run of slashes or of blanks in a pax value costs time in proportion to its length',()=>{
 const run=1<<21;
 const started=process.hrtime.bigint();
 const slashes='package/'+'/'.repeat(run)+'x';
 const named=tarfile.tarMembers(Buffer.concat([pax([record('path',slashes)]),file('short','{}'),END]));
 assert.equal(named[0].name.length,slashes.length);
 assert.ok(named[0].name===slashes);
 const stripped=tarfile.tarMembers(Buffer.concat([pax([record('path','package/x'+'/'.repeat(run))]),file('short','{}'),END]));
 assert.equal(stripped[0].name,'package/x');
 // `int()` of a pax size: blanks around a number are allowed, blanks inside are not, and neither is looked at twice.
 const sized=tarfile.tarMembers(Buffer.concat([pax([record('size',' '.repeat(run)+'2'+' '.repeat(run))]),file('package/package.json','{}'),END]));
 assert.equal(sized[0].size,2n);
 const unsized=tarfile.tarMembers(Buffer.concat([pax([record('size','2'+' '.repeat(run)+'x')]),file('package/package.json','{}'),END]));
 assert.equal(unsized[0].size,0n);
 assert.equal(tarfile.pyIntStr('1'+' '.repeat(run)+'x'),null);
 assert.equal(tarfile.pyIntStr(' '.repeat(run)+'-1_0 '),-10n);
 assert.equal(tarfile.pyIntStr('7'.repeat(run)),null);
 assert.equal(tarfile.pyIntBytes(Buffer.from(' '.repeat(run)+'+42\n')),42n);
 assert.equal(tarfile.pyIntBytes(Buffer.from('4'+' '.repeat(run)+'2')),null);
 const seconds=Number(process.hrtime.bigint()-started)/1e9;
 // The patterns these replaced took minutes on runs of this length (quadratic); one pass takes well under a second.
 assert.ok(seconds<30,seconds+' s');
});

test('only the observed pax keywords are kept, in the order and with the values a dict of all records would give',()=>{
 const noise=[];
 for(let index=0;index<50000;index++)noise.push(record('k'+index,'v'));
 const members=tarfile.tarMembers(Buffer.concat([
  pax([record('comment','global'),record('path','from-global'),...noise],'g'),
  file('first','{}'),
  // A later record of the same keyword replaces the value and keeps the place; GNU.sparse.name was set after path, so it wins.
  pax([record('GNU.sparse.name','sparse-name'),record('path','from-extended/'),record('unknown','x'),record('path','replaced//')]),
  file('second','{}'),
  pax([record('path','again')],'g'),
  file('third','{}'),
  // Of two records of one keyword the later value counts.
  pax([record('path','earlier'),record('path','later')]),
  file('fourth','{}'),
  END]));
 assert.deepEqual(members.map(member=>member.name),['from-global','sparse-name','again','later']);
});

test('pax records: a repeated keyword keeps the place of its first record, and a keyword is never empty',()=>{
 // The dict tarfile fills has `path` first and GNU.sparse.name second, whatever comes later; applied in that order, the sparse name wins.
 const repeated=tarfile.tarMembers(Buffer.concat([pax([record('path','first'),record('GNU.sparse.name','sparse'),record('path','again')]),file('short','{}'),END]));
 assert.deepEqual(repeated.map(member=>member.name),['sparse']);
 const reversed=tarfile.tarMembers(Buffer.concat([pax([record('GNU.sparse.name','sparse'),record('path','first'),record('GNU.sparse.name','again')]),file('short','{}'),END]));
 assert.deepEqual(reversed.map(member=>member.name),['first']);
 // Across a global and an extended header as well: the global record holds the place.
 const inherited=tarfile.tarMembers(Buffer.concat([pax([record('path','global'),record('GNU.sparse.name','global-sparse')],'g'),pax([record('GNU.sparse.name','own-sparse'),record('path','own')]),file('short','{}'),END]));
 assert.deepEqual(inherited.map(member=>member.name),['own-sparse']);
 // `5 =x\n` has no keyword: "invalid header". As the first member that is a ReadError; later it ends the listing silently.
 const empty=Buffer.concat([header('PaxHeader',5,'x'),padded(Buffer.from('5 =x\n'))]);
 assert.ok(outcome(()=>tarfile.tarMembers(Buffer.concat([empty,file('after','{}'),END]))) instanceof tarfile.ReadError);
 assert.deepEqual(tarfile.tarMembers(Buffer.concat([file('before','{}'),empty,file('after','{}'),END])).map(member=>member.name),['before']);
 assert.deepEqual(tarfile.tarMembers(Buffer.concat([file('before','{}'),pax([record('x','=')]),file('after','{}'),END])).map(member=>member.name),['before','after']);
});

test('int() of a pax number takes single underscores between digits and nothing else of them',()=>{
 for(const [text,value] of [['1_0',10n],['1__0',null],['_10',null],['10_',null],['1_0_0',100n],['1_ 0',null],['+_1',null],['-1_0',-10n],['1___0',null],[' 1_0 ',10n],['0_0',0n],['1_',null],['_',null]]){
  assert.equal(tarfile.pyIntStr(text),value,JSON.stringify(text));
  assert.equal(tarfile.pyIntBytes(Buffer.from(text)),value,JSON.stringify(text));
 }
 // Blanks are the six of ASCII, around the number only; a number has at most 4,300 digits.
 for(const [text,value] of [['\v1',1n],['\f1',1n],['\r1',1n],['1\v',1n],['1\f\r\n\t ',1n],['+ 1',null],['- 1',null],['+\t1',null],['1 1',null],['9'.repeat(4300),BigInt('9'.repeat(4300))],['9'.repeat(4301),null],['0'.repeat(4301),null]]){
  assert.equal(tarfile.pyIntStr(text),value,JSON.stringify(text.slice(0,20)));
  assert.equal(tarfile.pyIntBytes(Buffer.from(text)),value,JSON.stringify(text.slice(0,20)));
 }
 // In a pax size a failed conversion is 0; in a sparse size it is a ValueError.
 const sized=value=>tarfile.tarMembers(Buffer.concat([pax([record('size',value)]),file('package/package.json','{}'),END]))[0].size;
 assert.deepEqual([sized('1__0'),sized('1_0'),sized('5_')],[0n,10n,0n]);
 assert.equal(outcome(()=>tarfile.tarMembers(Buffer.concat([pax([record('GNU.sparse.realsize','1__0')]),file('a','{}'),END])))?.pyName,'ValueError');
});
