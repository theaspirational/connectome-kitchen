/**
 * `urllib.parse` of CPython 3.12.3, without WHATWG normalization (plan section
 * 3.2 row 10). `split` is `urlsplit`; `hostname`, `port`, `username` and
 * `password` are the lazy properties of its result, kept lazy because the
 * oracle only reads `.port` (which can raise) for some schemes.
 */
import { decodeUtf8 } from './codec.js';
import { PyValueError } from './py.js';

export interface SplitParts { scheme: string; netloc: string; path: string; query: string; fragment: string; }
export interface SplitResult extends SplitParts { port: number | null; }

const ASCII_DIGITS = /^[0-9]+$/u;

/** `ipaddress.IPv4Address(text)` accepts it. */
function ipv4(text: string): boolean {
  const octets = text.split('.');
  // No leading zeros, at most three ASCII digits, at most 255.
  return octets.length === 4 && octets.every(octet => ASCII_DIGITS.test(octet) && octet.length <= 3 && (octet === '0' || octet[0] !== '0') && Number(octet) <= 255);
}
/** `ipaddress.IPv6Address(text)` accepts it. */
function ipv6(text: string): boolean {
  const percent = text.indexOf('%');
  let address = text;
  if (percent >= 0) {
    const scope = text.slice(percent + 1);
    if (!scope || scope.includes('%')) return false;
    address = text.slice(0, percent);
  }
  if (!address) return false;
  const parts = address.split(':');
  if (parts.length < 3) return false;
  if (parts.at(-1)!.includes('.')) {
    if (!ipv4(parts.pop()!)) return false;
    parts.push('0', '0'); // two hextets; their value is irrelevant to validity
  }
  if (parts.length > 9) return false;
  let skip: number | null = null;
  for (let index = 1; index < parts.length - 1; index++) {
    if (!parts[index]) { if (skip !== null) return false; skip = index; }
  }
  let high: number, low: number;
  if (skip !== null) {
    high = skip; low = parts.length - skip - 1;
    if (!parts[0]) { high--; if (high) return false; }
    if (!parts.at(-1)) { low--; if (low) return false; }
    if (8 - (high + low) < 1) return false;
  } else {
    if (parts.length !== 8 || !parts[0] || !parts.at(-1)) return false;
    high = parts.length; low = 0;
  }
  const hextet = (part: string): boolean => /^[0-9a-fA-F]{1,4}$/u.test(part);
  for (let index = 0; index < high; index++) if (!hextet(parts[index]!)) return false;
  for (let index = parts.length - low; index < parts.length; index++) if (!hextet(parts[index]!)) return false;
  return true;
}
/** `urllib.parse._check_bracketed_host`. An IPv4 address is refused as well, so only IPv6 and IPvFuture pass. */
function checkBracketedHost(host: string): void {
  if (host.startsWith('v')) {
    if (!/^v[a-fA-F0-9]+\.[^\n]+$/u.test(host)) throw new PyValueError('IPvFuture address is invalid');
  } else if (ipv4(host)) throw new PyValueError('An IPv4 address cannot be in brackets');
  else if (!ipv6(host)) throw new PyValueError(host + ' does not appear to be an IPv4 or IPv6 address');
}
/**
 * `urllib.parse._check_bracketed_netloc`, called when the netloc holds both a
 * `[` and a `]`. The oracle interpreter (3.12.3 as the distribution ships it)
 * mirrors `_hostinfo` here: nothing may stand before the bracket or between
 * the bracket and the port, and when the brackets are all in the userinfo the
 * plain host is checked as if it had been bracketed. Upstream 3.12.3 only
 * looked at the text between the first `[` and the first `]`.
 */
function checkBracketedNetloc(netloc: string): void {
  const info = netloc.slice(netloc.lastIndexOf('@') + 1);
  const open = info.indexOf('[');
  if (open < 0) { const colon = info.indexOf(':'); checkBracketedHost(colon < 0 ? info : info.slice(0, colon)); return; }
  if (open > 0) throw new PyValueError('Invalid IPv6 URL');
  const bracketed = info.slice(1), shut = bracketed.indexOf(']');
  const tail = shut < 0 ? '' : bracketed.slice(shut + 1);
  if (tail && !tail.startsWith(':')) throw new PyValueError('Invalid IPv6 URL');
  checkBracketedHost(shut < 0 ? bracketed : bracketed.slice(0, shut));
}

/** `urllib.parse.urlsplit(value)` without the lazy properties. `ValueError` exactly where CPython raises it. */
export function split(value: string): SplitParts {
  let url = value.replace(/^[\x00-\x20]+/u, '').replace(/[\t\r\n]/gu, '');
  let scheme = '', netloc = '', query = '', fragment = '';
  const colon = /^([A-Za-z][A-Za-z0-9+.-]*):/u.exec(url);
  if (colon) { scheme = colon[1]!.toLowerCase(); url = url.slice(colon[0].length); }
  if (url.startsWith('//')) {
    const end = url.slice(2).search(/[/?#]/u);
    const cut = end < 0 ? url.length : end + 2;
    netloc = url.slice(2, cut); url = url.slice(cut);
    const open = netloc.includes('['), shut = netloc.includes(']');
    if (open !== shut) throw new PyValueError('Invalid IPv6 URL');
    if (open) checkBracketedNetloc(netloc);
  }
  const hash = url.indexOf('#');
  if (hash >= 0) { fragment = url.slice(hash + 1); url = url.slice(0, hash); }
  const mark = url.indexOf('?');
  if (mark >= 0) { query = url.slice(mark + 1); url = url.slice(0, mark); }
  // _checknetloc: characters that NFKC turns into a delimiter.
  if (netloc && /[^\x00-\x7f]/u.test(netloc)) {
    const bare = netloc.replace(/[@:#?]/gu, ''), folded = bare.normalize('NFKC');
    if (bare !== folded && /[/?#@:]/u.test(folded)) throw new PyValueError('netloc contains invalid characters under NFKC normalization');
  }
  return { scheme, netloc, path: url, query, fragment };
}

function hostinfo(netloc: string): [host: string, port: string | null] {
  const info = netloc.slice(netloc.lastIndexOf('@') + 1);
  const open = info.indexOf('[');
  let host: string, port: string;
  if (open >= 0) {
    const bracketed = info.slice(open + 1), shut = bracketed.indexOf(']');
    host = shut < 0 ? bracketed : bracketed.slice(0, shut);
    const tail = shut < 0 ? '' : bracketed.slice(shut + 1), colon = tail.indexOf(':');
    port = colon < 0 ? '' : tail.slice(colon + 1);
  } else {
    const colon = info.indexOf(':');
    host = colon < 0 ? info : info.slice(0, colon);
    port = colon < 0 ? '' : info.slice(colon + 1);
  }
  return [host, port || null];
}
function userinfo(netloc: string): [user: string | null, password: string | null] {
  const at = netloc.lastIndexOf('@');
  if (at < 0) return [null, null];
  const info = netloc.slice(0, at), colon = info.indexOf(':');
  return colon < 0 ? [info, null] : [info.slice(0, colon), info.slice(colon + 1)];
}
/** `SplitResult.hostname`: lowercased up to a `%` zone, `None` when empty. */
export function hostname(netloc: string): string | null {
  const host = hostinfo(netloc)[0];
  if (!host) return null;
  const percent = host.indexOf('%');
  return percent < 0 ? pyLower(host) : pyLower(host.slice(0, percent)) + host.slice(percent);
}
/** `SplitResult.port`: `ValueError` unless absent or ASCII digits in 0..65535. */
export function port(netloc: string): number | null {
  const text = hostinfo(netloc)[1];
  if (text === null) return null;
  if (!ASCII_DIGITS.test(text)) throw new PyValueError('Port could not be cast to integer value');
  // More digits than `int()` converts is a ValueError as well; either way the port is refused.
  if (text.replace(/^0+/u, '').length > 5 || Number(text) > 65535) throw new PyValueError('Port out of range 0-65535');
  return Number(text);
}
export const username = (netloc: string): string | null => userinfo(netloc)[0];
export const password = (netloc: string): string | null => userinfo(netloc)[1];

/**
 * `str.lower()`. Exact for every code point the oracle interpreter's Unicode
 * version assigns (swept in test/differential/port_primitives.py); a letter
 * that only a newer Unicode version knows is lowered here and left alone
 * there. Callers observe only whether the host is empty and how it starts.
 */
const pyLower = (text: string): string => text.toLowerCase();

/** `urlsplit(value)` with `.port` evaluated eagerly, as phase 0 defined it. */
export function urlsplit(value: string): SplitResult {
  const parts = split(value);
  return { ...parts, port: port(parts.netloc) };
}

/**
 * `urllib.parse.unquote(value)`: every `%XX` becomes a byte and each maximal
 * byte run is decoded as UTF-8 with replacement. A decoded byte order mark is
 * kept, as CPython keeps it.
 */
export function unquote(value: string): string {
  return value.replace(/(?:%[0-9a-fA-F]{2})+/gu, run => replaceInvalid(Uint8Array.from(run.match(/%[0-9a-fA-F]{2}/gu)!, hex => parseInt(hex.slice(1), 16))));
}
/** `bytes.decode('utf-8', 'replace')`. */
function replaceInvalid(bytes: Uint8Array): string {
  try { return decodeUtf8(bytes); } catch { return new TextDecoder('utf-8', { ignoreBOM: true }).decode(bytes); }
}
