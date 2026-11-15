import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import zlib from 'node:zlib';
import {createHash} from 'node:crypto';
import * as tarfile from '../../dist/pylike/tarfile.js';
// The stepwise gzip reader drives a call of Node's zlib that is no documented interface (src/pylike/tarfile.ts, Inflater).
// Two things are held here: it runs only on the Node versions it is verified on, the pinned one among them, and every
// pass after the checking one is held to what the checking pass saw.
const header=(name,size,type='0')=>{
 const block=Buffer.alloc(512);block.write(name,0,'latin1');
 block.write('0000644\0',100);block.write('0000000\0',108);block.write('0000000\0',116);
 block.write(size.toString(8).padStart(11,'0')+'\0',124);block.write('00000000000\0',136);block.fill(0x20,148,156);
 block.write(type,156);block.write('ustar\x0000',257,'latin1');
 block.write(block.reduce((sum,byte)=>sum+byte,0).toString(8).padStart(6,'0')+'\0 ',148);
 return block;
};
const member=(name,data)=>Buffer.concat([header(name,data.length),data,Buffer.alloc(-data.length&511)]);
const MANIFEST=Buffer.from('{"name": "example-package", "version": "1.0.0"}');
// A body that deflate cannot shrink much, so that the container is many steps long at every step size used here.
const noise=length=>{const out=Buffer.alloc(length);let state=0x2545f491;for(let index=0;index<length;index++){state^=state<<13;state^=state>>>17;state^=state<<5;out[index]=state&0xff;}return out;};
const archive=(bodyLength=40000)=>zlib.gzipSync(Buffer.concat([member('package/package.json',MANIFEST),member('package/blob.bin',noise(bodyLength)),member('package/zeros.bin',Buffer.alloc(30000)),Buffer.alloc(1024)]));
const digest=data=>createHash('sha256').update(data).digest('hex');
const listing=(data,inputStep)=>tarfile.gzipMembers(data,{inputStep}).map(entry=>[entry.name,entry.size.toString(),digest(entry.read())]);

test('the reader is tied to the pinned Node, and the pin is read from oracle/versions.yml',()=>{
 const pins=fs.readFileSync('oracle/versions.yml','utf8');
 const pinned=/^artifacts:\n(?:(?:  .*)?\n)*?  node:\n(?:    .*\n)*?    version: (\S+)\n/mu.exec(pins)?.[1];
 assert.match(pinned??'',/^\d+\.\d+\.\d+$/u);
 assert.ok(tarfile.STEPWISE_RUNTIMES.includes(pinned),'the pinned Node '+pinned+' is not among the verified runtimes: read lib/zlib.js of it, run the gates on it, then add it');
 // The second entry is the Node of the development host. A change of this list is a reviewed change.
 assert.deepEqual([...tarfile.STEPWISE_RUNTIMES],['22.23.3','24.14.0']);
 assert.ok(tarfile.STEPWISE_RUNTIMES.includes(process.versions.node),'this suite runs on a Node the reader refuses');
});

const onRuntime=(versions,run)=>{
 const real=Object.getOwnPropertyDescriptor(process,'versions');
 Object.defineProperty(process,'versions',{...real,value:Object.freeze({...process.versions,...versions})});
 try{return run();}finally{Object.defineProperty(process,'versions',real);}
};
test('another runtime is refused before the engine is touched, and not as a damaged archive',async()=>{
 const data=archive();
 const proto=Object.getPrototypeOf(zlib.createInflateRaw()),original=proto._processChunk;
 let calls=0;
 proto._processChunk=function(...args){calls++;return original.apply(this,args);};
 try{
  for(const versions of [{node:'24.14.1'},{node:'24.15.0'},{node:'22.23.2'},{node:'25.0.0'},{node:'24.14.0-pre'},{node:undefined},{bun:'1.4.4'},{deno:'2.0.0'}]){
   for(const run of [()=>tarfile.gzipMembers(data),()=>tarfile.gunzip(data),()=>tarfile.gzipMembers(Buffer.alloc(0))]){
    assert.throws(()=>onRuntime(versions,run),error=>error instanceof tarfile.UnsupportedRuntime&&error.name==='UnsupportedRuntime'&&!('pyName' in error)
     &&error.message==='unsupported runtime: the stepwise gzip reader is verified on Node 22.23.3 and 24.14.0 only',JSON.stringify(versions));
   }
  }
  assert.equal(calls,0);
  // The reader of an uncompressed tar stream uses no engine and is not tied to a runtime.
  assert.equal(onRuntime({node:'25.0.0'},()=>tarfile.tarMembers(zlib.gunzipSync(data)).length),3);
  assert.equal(listing(data).length,3);
  assert.ok(calls>0);
 }finally{proto._processChunk=original;}
 // archive_manifest lets it escape: it is not one of the classes archive.py:138 names.
 const archiveModule=await import('../../dist/archive.js');
 const path=fs.mkdtempSync('.cache/runtime-')+'/case.tgz';
 try{
  fs.writeFileSync(path,data);
  const entry=new Map(Object.entries({name:'example-package',archive:fs.realpathSync(path),sha256:digest(data),kind:'artifact',version:'1.0.0'}));
  assert.equal(archiveModule.archive_manifest(entry).get('name'),'example-package');
  assert.throws(()=>onRuntime({node:'24.14.1'},()=>archiveModule.archive_manifest(entry)),error=>error instanceof tarfile.UnsupportedRuntime&&!(error instanceof archiveModule.ArchiveError));
 }finally{fs.rmSync(path.slice(0,path.lastIndexOf('/')),{recursive:true,force:true});}
});

test('a pass after the checking one cannot hand over other bytes than were checked',()=>{
 const proto=Object.getPrototypeOf(zlib.createInflateRaw()),original=proto._processChunk;
 let mode=null,target=-1,calls=0,changed=false;
 const faulty=out=>{
  const copy=Buffer.from(out);
  switch(mode){
   case 'drop':changed=copy.length>0;return Buffer.alloc(0);
   case 'repeat':changed=copy.length>0;return Buffer.concat([copy,copy]);
   case 'zero':changed=copy.some(byte=>byte!==0);return Buffer.alloc(copy.length);
   case 'flip':changed=copy.length>0;if(copy.length)copy[copy.length>>1]^=0x10;return copy;
   case 'cut':changed=copy.length>1;return copy.subarray(0,copy.length>>1);
   default:throw new Error('unknown fault');
  }
 };
 proto._processChunk=function(chunk,flush,callback){
  if(typeof callback==='function')return original.call(this,chunk,flush,callback);
  const hit=calls++===target&&mode!==null;
  if(!hit)return original.call(this,chunk,flush);
  if(mode==='early-stop'){if(chunk.length<2)return original.call(this,chunk,flush);changed=true;return original.call(this,chunk.subarray(0,chunk.length>>1),flush);}
  // What zlib reports for invalid deflate data, as lib/zlib.js throws it from a synchronous step.
  if(mode==='zlib-error'){changed=true;throw Object.assign(new Error('invalid block type'),{code:'Z_DATA_ERROR',errno:-3});}
  return faulty(original.call(this,chunk,flush));
 };
 const seen={refusedAsRuntime:0,harmless:0,checkingPass:0};
 try{
  for(const [data,inputStep] of [[archive(),997],[archive(3000),61],[archive(200000),tarfile.INPUT_STEP],[Buffer.concat([archive(5000),archive(0)]),211]]){
   mode=null;calls=0;tarfile.gunzip(data,inputStep);const checking=calls;
   calls=0;const reference=listing(data,inputStep);const all=calls;
   // The listing and the three member reads each inflate again: at least four more passes over the first member.
   assert.ok(all>=3*checking,all+' calls for '+checking+' in the checking pass');
   const later=[];
   for(let index=checking;index<all;index+=Math.max(1,Math.floor((all-checking)/120)))later.push(index);
   for(const fault of ['drop','repeat','zero','flip','cut','early-stop','zlib-error']){
    for(const index of [0,checking>>1,checking-1,...later,all-1]){
     mode=fault;target=index;calls=0;changed=false;
     let outcome,error=null;
     try{outcome=listing(data,inputStep);}catch(caught){error=caught;}
     mode=null;
     if(error===null){
      // Accepted: then it is what the undisturbed reader gives, because the fault changed no byte (zeroing zeros, an empty piece).
      assert.deepEqual(outcome,reference,fault+' at call '+index+' was accepted with other content');
      seen.harmless++;
     }else if(index<checking){
      // In the checking pass a fault is damage as far as the reader can tell, or an engine it does not know.
      assert.ok(error instanceof tarfile.BadGzipFile||error instanceof tarfile.GzipDamage||error instanceof tarfile.UnsupportedRuntime,fault+' at call '+index+': '+error);
      seen.checkingPass++;
     }else{
      // After it the archive has passed its check; the fault is the engine's and must not be reported as the archive's.
      assert.ok(error instanceof tarfile.UnsupportedRuntime,fault+' at call '+index+' of a later pass: '+error);
      assert.ok(changed,fault+' at call '+index);
      seen.refusedAsRuntime++;
     }
    }
   }
  }
 }finally{proto._processChunk=original;}
 assert.ok(seen.refusedAsRuntime>1500,JSON.stringify(seen));
 assert.ok(seen.checkingPass>30,JSON.stringify(seen));
 console.log('faults injected into the stepwise call: '+JSON.stringify(seen));
});

test('the step record costs eight bytes a step and a step is bounded',()=>{
 const data=archive();
 assert.throws(()=>tarfile.gzipMembers(data,{inputStep:0}),/inflate step must be an integer from 1 to 1048576/u);
 assert.throws(()=>tarfile.gzipMembers(data,{inputStep:1048577}),/inflate step must be an integer from 1 to 1048576/u);
 assert.deepEqual(listing(data,1048576),listing(data,1));
});
