import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {atomicWrite,readFile,manifest} from '../../src/io/fs-safe.js';
import {run} from '../../src/io/proc.js';
import {withFlock} from '../../src/io/flock.js';
const base=path.resolve('.cache/io');
fs.mkdirSync(base,{recursive:true,mode:0o700});
test('private atomic write, syscall ordering and no-follow reads',()=>{
 const file=base+'/value'; const log=[];atomicWrite(file,Buffer.from('value'),{log:x=>log.push(x.op)});
 assert.deepEqual(log,['fsync-file','rename','fsync-dir']);assert.equal(fs.statSync(file).mode&0o777,0o600);
 assert.equal(readFile(file).toString(),'value');assert.throws(()=>readFile(file,{maxBytes:2}));
 fs.symlinkSync(file,base+'/link');assert.throws(()=>readFile(base+'/link'));assert.throws(()=>atomicWrite(base+'/link',Buffer.from('bad')));
 fs.linkSync(file,base+'/hard');assert.throws(()=>readFile(file));fs.unlinkSync(base+'/hard');
 fs.symlinkSync(base,base+'/alias');assert.throws(()=>readFile(base+'/alias/value'));
 assert.throws(()=>readFile(base+'/../io/value'));assert.ok(manifest(base).some(x=>x.kind==='link'));
 fs.unlinkSync(base+'/link');fs.unlinkSync(base+'/alias');
});
test('redacted child, explicit empty environment, discarded output',()=>{
 const log=[];run(['/bin/sh','-c','test -z "$HOME"; printf protected; printf protected >&2'],{log:x=>log.push(x)});
 assert.deepEqual(log[0].envNames,[]);assert.equal(log[0].stdout,'discard');
 assert.throws(()=>run(['/bin/sh','-c','printf protected >&2; exit 7']),e=>e.message==='child refused');
 assert.throws(()=>run(['sh']));
});
test('flock uses a pinned descriptor and refuses aliases',()=>{
 const log=[];withFlock(base+'/lock',['/bin/true'],{flock:'/usr/bin/flock',log:x=>log.push(x)});assert.equal(log[0].status,0);
 fs.symlinkSync(base+'/lock',base+'/lock-link');assert.throws(()=>withFlock(base+'/lock-link',['/bin/true'],{flock:'/usr/bin/flock'}));fs.unlinkSync(base+'/lock-link');
});
test('contending flock refuses a second writer',async()=>{
 const {spawn}=await import('node:child_process');
 const lock=base+'/contended',ready=base+'/ready';
 fs.closeSync(fs.openSync(lock,'w',0o600));
 const child=spawn('/usr/bin/flock',['--exclusive',lock,'/bin/sh','-c','printf ready > "$1"; sleep 2','child',ready],{stdio:'ignore',env:{PATH:'/usr/bin:/bin'}});
 const done=new Promise(resolve=>child.on('exit',resolve));
 try {
  for(let n=0;n<100&&!fs.existsSync(ready);n++)await new Promise(r=>setTimeout(r,10));
  assert.ok(fs.existsSync(ready));
  assert.throws(()=>withFlock(lock,['/bin/true'],{flock:'/usr/bin/flock',timeout:0}));
 }finally{await done;fs.unlinkSync(ready);}
});
