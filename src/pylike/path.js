import { realpathSync } from 'node:fs';
import { compare } from './json.js';
/** PurePosixPath normalization, preserving .. and exactly two leading slashes. */
export function path(value) {
  if(typeof value!=='string'||value.includes('\0')) throw new Error('invalid path');
  const prefix=value.startsWith('//')&&!value.startsWith('///')?'//':value.startsWith('/')?'/':'';
  const parts=value.split('/').filter(c=>c&&c!=='.'); return prefix+parts.join('/')||'.';
}
export function parts(value) { const p=path(value); return p==='.'?[]:p.startsWith('//')?['//',...p.slice(2).split('/').filter(Boolean)]:p.startsWith('/')?['/',...p.slice(1).split('/').filter(Boolean)]:p.split('/'); }
export function comparePaths(a,b) { const x=parts(a), y=parts(b); for(let i=0;i<Math.min(x.length,y.length);i++){const c=compare(x[i],y[i]);if(c)return c;}return x.length-y.length; }
export function isRelativeTo(value, parent) { const a=parts(value), b=parts(parent);return b.every((c,i)=>a[i]===c); }
export const resolve = value => realpathSync(value);
