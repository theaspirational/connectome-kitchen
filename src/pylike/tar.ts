import { gunzipSync } from 'node:zlib';
export interface TarOptions { maxBytes?: number; maxMembers?: number; }
export interface TarMember { name: string; kind: 'directory' | 'file'; mode: number; data: Buffer; }
/** Bounded in-memory tar reader. Unsupported extensions refuse, never extract. */
export function readTar(input: Buffer, {maxBytes=64*1024*1024,maxMembers=10000}: TarOptions={}): TarMember[] {
  const bytes=input[0]===31&&input[1]===139?gunzipSync(input,{maxOutputLength:maxBytes}):input;
  if(bytes.length>maxBytes)throw new Error('tar bound');
  const result: TarMember[]=[]; const names=new Set<string>(); let offset=0, total=0, pending: Record<string, string>={};
  const text=(b: Uint8Array)=>new TextDecoder('utf-8',{fatal:true}).decode(b).split('\0')[0]!;
  const oct=(b: Uint8Array)=>{const t=text(b).trim();if(!/^[0-7]*$/u.test(t))throw new Error('tar number');return t?parseInt(t,8):0;};
  while(offset+512<=bytes.length) {
    const header=bytes.subarray(offset,offset+512);offset+=512;
    if(header.every(n=>n===0)) { if(bytes.subarray(offset).some(n=>n!==0))throw new Error('trailing tar data');return result; }
    let sum=0;for(let i=0;i<512;i++)sum+=i>=148&&i<156?32:header[i]!;
    if(sum!==oct(header.subarray(148,156)))throw new Error('tar checksum');
    const size=oct(header.subarray(124,136));total+=size;
    if(total>maxBytes||offset+size>bytes.length)throw new Error('tar bound');
    const data=bytes.subarray(offset,offset+size);offset+=Math.ceil(size/512)*512;
    const type=header[156]!;
    if(type===120) { // POSIX per-file extended header: path and size only.
      let i=0;pending={};while(i<data.length){const space=data.indexOf(32,i);const n=Number(data.subarray(i,space).toString());if(!Number.isSafeInteger(n)||n<=space-i+1||i+n>data.length)throw new Error('PAX record');const record=text(data.subarray(space+1,i+n-1));const eq=record.indexOf('=');const key=record.slice(0,eq);if(!['path','size','mtime','atime','ctime'].includes(key)||Object.hasOwn(pending,key))throw new Error('PAX field');pending[key]=record.slice(eq+1);i+=n;}continue;
    }
    const prefix=text(header.subarray(345,500));const raw=text(header.subarray(0,100));
    const name=pending.path??(prefix?prefix+'/':'')+raw;
    if(pending.size!==undefined&&Number(pending.size)!==size)throw new Error('PAX size');pending={};
    const parts=name.replace(/\/$/u,'').split('/');
    if(!name||name.startsWith('/')||parts.some(p=>!p||p==='.'||p==='..')||name.includes('\\')||names.has(name))throw new Error('tar path');
    names.add(name);if(names.size>maxMembers)throw new Error('tar count');
    if(![0,48,53].includes(type))throw new Error('tar member type');
    if(type===53&&size!==0)throw new Error('tar directory payload');
    result.push({name,kind:type===53?'directory':'file',mode:type===53?0o755:0o644,data:Buffer.from(data)});
  }
  throw new Error('truncated tar');
}
