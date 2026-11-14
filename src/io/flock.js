import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import { openParent } from './fs-safe.js';
/** Platform flock is an explicit dependency; caller pins its digest (Q6). */
export function withFlock(lockPath,argv,{flock,timeout=30,env={},log=()=>{}}={}) {
  if(process.platform!=='linux'||!flock?.startsWith('/')||!argv?.[0]?.startsWith('/'))throw new Error('explicit platform tool required');
  const parent=openParent(lockPath);let fd;
  try {
    fd=fs.openSync('/proc/self/fd/'+parent.fd+'/'+parent.leaf,fs.constants.O_RDWR|fs.constants.O_CREAT|fs.constants.O_NOFOLLOW,0o600);
    const s=fs.fstatSync(fd);if(!s.isFile()||s.nlink!==1||s.uid!==process.getuid()||(s.mode&0o777)!==0o600)throw new Error('unsafe lock');
    const result=spawnSync(flock,['--exclusive','--timeout',String(timeout),'/proc/self/fd/3',...argv],{env,stdio:['ignore','ignore','ignore',fd]});
    log({op:'flock',status:result.status});if(result.error||result.status!==0)throw new Error('locked child refused');return {status:0};
  } finally {if(fd!==undefined)fs.closeSync(fd);fs.closeSync(parent.fd);}
}
