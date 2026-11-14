import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomBytes } from 'node:crypto';
const C=fs.constants;
const linux=()=>{if(process.platform!=='linux')throw new Error('Linux required');};
/** Pin every parent by fd; never follow a link while opening the leaf. */
export function openParent(filename) {
  linux();if(!path.isAbsolute(filename)||filename.includes('\0')||filename.split('/').some(p=>p==='..'||p==='.')||filename.includes('//'))throw new Error('literal absolute path required');
  const parts=filename.slice(1).split('/');const leaf=parts.pop();if(!leaf)throw new Error('leaf required');
  let fd=fs.openSync('/',C.O_RDONLY|C.O_DIRECTORY|C.O_NOFOLLOW);
  try { for(const component of parts){const next=fs.openSync('/proc/self/fd/'+fd+'/'+component,C.O_RDONLY|C.O_DIRECTORY|C.O_NOFOLLOW);fs.closeSync(fd);fd=next;}return {fd,leaf}; }
  catch(error){fs.closeSync(fd);throw error;}
}
export function readFile(filename,{maxBytes=1024*1024,owner=process.getuid(),mode=null,root=path.dirname(filename),log=()=>{}}={}) {
  if(!path.isAbsolute(root)||root.split('/').some(p=>p==='..'||p==='.')||!filename.startsWith(root.replace(/\/$/u,'')+'/'))throw new Error('input escapes root');
  const parent=openParent(filename);let fd;
  try {
    fd=fs.openSync('/proc/self/fd/'+parent.fd+'/'+parent.leaf,C.O_RDONLY|C.O_NOFOLLOW|C.O_NONBLOCK);
    const resolved=fs.readlinkSync('/proc/self/fd/'+fd);
    if(!resolved.startsWith(root.replace(/\/$/u,'')+'/')||resolved.endsWith(' (deleted)'))throw new Error('input escapes root');
    const stat=fs.fstatSync(fd);if(!stat.isFile()||stat.nlink!==1||stat.uid!==owner||stat.size>maxBytes||(mode!==null&&(stat.mode&0o777)!==mode))throw new Error('unsafe file');
    const chunks=[];let size=0;const buffer=Buffer.alloc(Math.min(65536,maxBytes+1));
    while(true){const n=fs.readSync(fd,buffer,0,Math.min(buffer.length,maxBytes-size+1),null);if(!n)break;size+=n;if(size>maxBytes)throw new Error('file bound');chunks.push(Buffer.from(buffer.subarray(0,n)));}
    log({op:'read',path:filename});return Buffer.concat(chunks);
  } finally {if(fd!==undefined)fs.closeSync(fd);fs.closeSync(parent.fd);}
}
export function atomicWrite(filename,bytes,{maxBytes=1024*1024,log=()=>{}}={}) {
  if(bytes.length>maxBytes)throw new Error('file bound');
  const parent=openParent(filename);const base='/proc/self/fd/'+parent.fd+'/';const temp='.'+parent.leaf+'.'+randomBytes(16).toString('hex');let fd;
  try {
    try {const s=fs.lstatSync(base+parent.leaf);if(!s.isFile()||s.nlink!==1||s.uid!==process.getuid())throw new Error('unsafe target');}catch(e){if(e.code!=='ENOENT')throw e;}
    fd=fs.openSync(base+temp,C.O_WRONLY|C.O_CREAT|C.O_EXCL|C.O_NOFOLLOW,0o600);
    let offset=0;while(offset<bytes.length)offset+=fs.writeSync(fd,bytes,offset,bytes.length-offset);
    fs.fchmodSync(fd,0o600);fs.fsyncSync(fd);log({op:'fsync-file'});fs.closeSync(fd);fd=undefined;
    fs.renameSync(base+temp,base+parent.leaf);log({op:'rename'});fs.fsyncSync(parent.fd);log({op:'fsync-dir'});
  } finally {if(fd!==undefined)fs.closeSync(fd);try{fs.unlinkSync(base+temp);}catch(e){if(e.code!=='ENOENT')throw e;}fs.closeSync(parent.fd);}
}
export function manifest(root) {
  const out=[];
  function walk(dir,relative='') {for(const name of fs.readdirSync(dir).sort()) {const absolute=path.join(dir,name), rel=relative?relative+'/'+name:name,s=fs.lstatSync(absolute);let kind=s.isDirectory()?'directory':s.isFile()?'file':s.isSymbolicLink()?'link':'special';out.push({path:rel,kind,mode:s.mode&0o777,...(kind==='link'?{target:fs.readlinkSync(absolute)}:kind==='file'?{sha256:createHash('sha256').update(fs.readFileSync(absolute)).digest('hex')}:{})});if(kind==='directory')walk(absolute,rel);}}
  walk(root);return out;
}
