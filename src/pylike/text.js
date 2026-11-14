// CPython str whitespace differs from ECMAScript whitespace (notably BOM).
import {space as spaces, digit, contains} from './unicode.js';
const space={test: c=>contains(spaces,c)};
export const isspace = text => text.length>0 && Array.from(text).every(c=>space.test(c));
export function strip(text) {
  const chars=Array.from(text); while(chars.length&&space.test(chars[0])) chars.shift();
  while(chars.length&&space.test(chars.at(-1))) chars.pop(); return chars.join('');
}
export function splitlines(text, keepends=false) {
  const lines=[]; let start=0;
  const pattern=/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/gu;
  for(const m of text.matchAll(pattern)) { lines.push(text.slice(start,m.index+(keepends?m[0].length:0))); start=m.index+m[0].length; }
  if(start<text.length) lines.push(text.slice(start)); return lines;
}
export const isdigit = text => text.length>0 && Array.from(text).every(c=>contains(digit,c));
