/**
 * `pathlib.Path` and `posixpath` of CPython 3.12 as the engine uses them, on
 * plain strings. A "path" below is always `str(Path(value))`.
 *
 * Plan section 3.2 row 9. The parts that matter for refusals:
 *   - `resolve()` is not `fs.realpathSync`: it is non-strict (a missing tail is
 *     kept lexically) and raises `RuntimeError` on a symlink loop;
 *   - `is_file()`/`is_symlink()` answer False only for four errno values and
 *     re-raise every other `OSError`;
 *   - every `os` call first encodes the path, so a NUL or an unencodable
 *     surrogate is a `ValueError`, never an `OSError`.
 */
import fs from 'node:fs';
import { decodeUtf8, fsdecode, fsencode } from './codec.js';
import { PyOSError, PyRuntimeError, PyTypeError, PyValueError, isprintable } from './py.js';

const ERRNO: Record<string, [number, string]> = {
  EPERM: [1, 'Operation not permitted'], ENOENT: [2, 'No such file or directory'], EIO: [5, 'Input/output error'],
  EBADF: [9, 'Bad file descriptor'], EACCES: [13, 'Permission denied'], EEXIST: [17, 'File exists'],
  ENOTDIR: [20, 'Not a directory'], EISDIR: [21, 'Is a directory'], EINVAL: [22, 'Invalid argument'],
  ENAMETOOLONG: [36, 'File name too long'], ELOOP: [40, 'Too many levels of symbolic links'],
};

/** `repr()` of a str, as CPython's `unicode_repr` writes it. */
export function repr(value: string): string {
  const quote = value.includes("'") && !value.includes('"') ? '"' : "'";
  let out = quote;
  for (const char of value) {
    const code = char.codePointAt(0)!;
    if (char === quote || char === '\\') out += '\\' + char;
    else if (char === '\n') out += '\\n'; else if (char === '\r') out += '\\r'; else if (char === '\t') out += '\\t';
    else if (isprintable(char)) out += char;
    else out += code < 0x100 ? '\\x' + code.toString(16).padStart(2, '0') : code < 0x10000 ? '\\u' + code.toString(16).padStart(4, '0') : '\\U' + code.toString(16).padStart(8, '0');
  }
  return out + quote;
}

function osError(error: unknown, path: string): never {
  const code = (error as NodeJS.ErrnoException | null)?.code;
  if (typeof code !== 'string' || !/^E[A-Z0-9]+$/u.test(code)) throw error;
  const [number, text] = ERRNO[code] ?? [0, code];
  throw new PyOSError(code, '[Errno ' + number + '] ' + text + ': ' + repr(path));
}

/**
 * `os.fspath(value)` for the one path type the port has. The signatures say
 * `string`; a caller that is not type-checked (plain JavaScript, a decoded
 * JSON value) gets the oracle's own answer for a wrong type, `TypeError`,
 * instead of whatever the runtime would do with it.
 */
export function fspath(value: unknown): string {
  if (typeof value !== 'string') throw new PyTypeError('expected str, bytes or os.PathLike object');
  return value;
}

/** `str(PurePosixPath(value))`: `//` and `.` collapse, `..` stays, exactly two leading slashes stay. Never raises. */
export function pathStr(value: string): string {
  const root = value.startsWith('//') && !value.startsWith('///') ? '//' : value.startsWith('/') ? '/' : '';
  return (root + value.split('/').filter(part => part && part !== '.').join('/')) || '.';
}
/** `PurePosixPath(value).parts`. */
export function pathParts(value: string): string[] {
  const path = pathStr(value);
  if (path === '.') return [];
  const root = path.startsWith('//') ? '//' : path.startsWith('/') ? '/' : '';
  const tail = path.slice(root.length).split('/').filter(Boolean);
  return root ? [root, ...tail] : tail;
}
export const isAbsolute = (value: string): boolean => value.startsWith('/');
/** `posixpath.join`. */
export function posixJoin(first: string, ...rest: string[]): string {
  let path = first;
  for (const part of rest) {
    if (part.startsWith('/')) path = part;
    else if (!path || path.endsWith('/')) path += part;
    else path += '/' + part;
  }
  return path;
}
/** `str(Path(base) / value)`. */
export const joinPath = (base: string, value: string): string => pathStr(posixJoin(base, value));
/** `Path(value).name`. */
export function name(value: string): string {
  const parts = pathParts(value);
  const last = parts.at(-1);
  return last === undefined || (parts.length === 1 && isAbsolute(last)) ? '' : last;
}
/** `Path(value).suffix`. */
export function suffix(value: string): string {
  const leaf = name(value), dot = leaf.lastIndexOf('.');
  return dot > 0 && dot < leaf.length - 1 ? leaf.slice(dot) : '';
}
/** `str(Path(value).parent)`. */
export function parent(value: string): string {
  const parts = pathParts(value);
  if (!parts.length) return '.';
  const rooted = isAbsolute(parts[0]!);
  if (parts.length === 1) return rooted ? parts[0]! : '.';
  const head = parts.slice(0, -1);
  return rooted ? head[0]! + head.slice(1).join('/') : head.join('/');
}
/** `Path(value).is_relative_to(other)`: component-wise, never a string prefix; an absolute path is never relative to a relative one. */
export function isRelativeTo(value: string, other: string): boolean {
  const mine = pathParts(value), theirs = pathParts(other);
  const root = (parts: string[]): string => (parts[0] !== undefined && isAbsolute(parts[0]) ? parts[0] : '');
  return root(mine) === root(theirs) && theirs.length <= mine.length && theirs.every((part, index) => mine[index] === part);
}
/** `str(Path(value).relative_to(other))`; `ValueError` when `other` is not a lexical ancestor. */
export function relativeTo(value: string, other: string): string {
  if (!isRelativeTo(value, other)) throw new PyValueError(repr(pathStr(value)) + ' is not in the subpath of ' + repr(pathStr(other)));
  return pathParts(value).slice(pathParts(other).length).join('/') || '.';
}
/** `posixpath.normpath`. */
export function normpath(value: string): string {
  if (value === '') return '.';
  const slashes = value.startsWith('//') && !value.startsWith('///') ? 2 : value.startsWith('/') ? 1 : 0;
  const parts: string[] = [];
  for (const part of value.split('/')) {
    if (part === '' || part === '.') continue;
    if (part !== '..' || (!slashes && !parts.length) || (parts.length && parts.at(-1) === '..')) parts.push(part);
    else if (parts.length) parts.pop();
  }
  return ('/'.repeat(slashes) + parts.join('/')) || '.';
}
/** `os.path.abspath`. */
export const abspath = (value: string): string => normpath(isAbsolute(value) ? value : posixJoin(process.cwd(), value));

/** `os.lstat`. */
export function lstat(path: string): fs.Stats {
  const encoded = fsencode(path);
  try { return fs.lstatSync(encoded); } catch (error) { return osError(error, path); }
}
/** `os.stat`. */
export function stat(path: string): fs.Stats {
  const encoded = fsencode(path);
  try { return fs.statSync(encoded); } catch (error) { return osError(error, path); }
}
function readlink(path: string): string {
  const encoded = fsencode(path);
  try { return fsdecode(fs.readlinkSync(encoded, { encoding: 'buffer' })); } catch (error) { return osError(error, path); }
}

function split(path: string): [string, string] {
  const cut = path.lastIndexOf('/') + 1;
  let head = path.slice(0, cut);
  if (head && head !== '/'.repeat(head.length)) head = head.replace(/\/+$/u, '');
  return [head, path.slice(cut)];
}
/** `posixpath._joinrealpath(path, rest, strict=False, seen)`. */
function joinReal(path: string, rest: string, seen: Map<string, string | null>): [string, boolean] {
  if (rest.startsWith('/')) { rest = rest.slice(1); path = '/'; }
  while (rest) {
    const cut = rest.indexOf('/');
    const part = cut < 0 ? rest : rest.slice(0, cut);
    rest = cut < 0 ? '' : rest.slice(cut + 1);
    if (!part || part === '.') continue;
    if (part === '..') {
      if (path) {
        const [head, last] = split(path);
        path = last === '..' ? posixJoin(head, '..', '..') : head;
      } else path = '..';
      continue;
    }
    const next = posixJoin(path, part);
    let link = false;
    // Non-strict: any OSError means "not a link, keep the name". A ValueError (NUL) is not caught.
    try { link = lstat(next).isSymbolicLink(); } catch (error) { if (!(error instanceof PyOSError)) throw error; }
    if (!link) { path = next; continue; }
    if (seen.has(next)) {
      const cached = seen.get(next)!;
      if (cached !== null) { path = cached; continue; }
      return [posixJoin(next, rest), false]; // loop: resolved part + the rest, unchanged
    }
    seen.set(next, null);
    const [resolved, ok] = joinReal(path, readlink(next), seen);
    path = resolved;
    if (!ok) return [posixJoin(path, rest), false];
    seen.set(next, path);
  }
  return [path, true];
}
/** `os.path.realpath(value, strict=False)`. */
export const realpath = (value: string): string => abspath(joinReal('', value, new Map())[0]);

/** `str(Path(value).resolve())`, non-strict. A symlink loop is `RuntimeError`, which no engine handler catches. */
export function resolve(value: string): string {
  const loop = (error: unknown): void => {
    if (error instanceof PyOSError && error.errno === 'ELOOP') throw new PyRuntimeError('Symlink loop from ' + repr(pathStr(value)));
  };
  let real: string;
  try { real = pathStr(realpath(pathStr(value))); } catch (error) { loop(error); throw error; }
  try { stat(real); } catch (error) { if (!(error instanceof PyOSError)) throw error; if (error.errno === 'ELOOP') throw new PyRuntimeError('Symlink loop from ' + repr(real)); }
  return real;
}

const IGNORED = new Set(['ENOENT', 'ENOTDIR', 'EBADF', 'ELOOP']);
function probe(read: () => boolean): boolean {
  try { return read(); } catch (error) {
    if (error instanceof PyOSError) { if (IGNORED.has(error.errno)) return false; throw error; }
    if (error instanceof PyValueError) return false;
    throw error;
  }
}
/** `Path(value).is_file()`: follows links. */
export const isFile = (value: string): boolean => probe(() => stat(value).isFile());
/** `Path(value).is_symlink()`. */
export const isSymlink = (value: string): boolean => probe(() => lstat(value).isSymbolicLink());
/** `Path(value).read_bytes()`. */
export function readBytes(value: string): Buffer {
  const encoded = fsencode(value);
  try { return fs.readFileSync(encoded); } catch (error) { return osError(error, value); }
}
/** `Path(value).read_text()` under a UTF-8 locale: strict UTF-8, universal newlines. */
export const readText = (value: string): string => decodeUtf8(readBytes(value), 'strict').replace(/\r\n?/gu, '\n');
