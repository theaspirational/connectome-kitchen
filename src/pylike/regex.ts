/** Deliberately small helpers; do not mechanically translate arbitrary regexes. */
import {decimal, word as words, contains} from './unicode.js';
export const digit = (c: string) => Array.from(c).length===1 && contains(decimal,c);
export const word = (c: string) => Array.from(c).length===1 && contains(words,c);
export function fullmatch(pattern: string, text: string): boolean {
  const re=new RegExp('^(?:'+pattern+')$(?![\\s\\S])','u'); return re.test(text);
}
export function dollarMatch(pattern: string, text: string): boolean {
  return new RegExp('^(?:'+pattern+')(?:\\n)?$(?![\\s\\S])','u').test(text);
}
import {space as spaces} from './unicode.js';
import type {Range} from './unicode.js';
const escape = (n: number) => '\\u{'+n.toString(16)+'}';
/** Body of a `u`-flag character class with exactly these code points. */
export const classBody = (ranges: readonly Range[]) => ranges.map(([a,b])=>a===b?escape(a):escape(a)+'-'+escape(b)).join('');
/** Python `\w` and `\s` of a str pattern, as class bodies: write `[${W}]`, `[^${S}]`, `[${W}-]`. */
export const W = classBody(words);
export const S = classBody(spaces);
