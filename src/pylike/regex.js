/** Deliberately small helpers; do not mechanically translate arbitrary regexes. */
import {decimal, word as words, contains} from './unicode.js';
export const digit = c => Array.from(c).length===1 && contains(decimal,c);
export const word = c => Array.from(c).length===1 && contains(words,c);
export function fullmatch(pattern, text) {
  const re=new RegExp('^(?:'+pattern+')$(?![\\s\\S])','u'); return re.test(text);
}
export function dollarMatch(pattern, text) {
  return new RegExp('^(?:'+pattern+')(?:\\n)?$(?![\\s\\S])','u').test(text);
}
