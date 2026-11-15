/**
 * The filesystem calls of `pathlib`, `os`, `shutil` and `tempfile` that the deployment engine and the cook adapter make
 * and `pathlib.ts` does not already have. Paths are `str(Path(value))` strings; an `OSError` is a `PyOSError` with the
 * errno name, so that a caller catches by class exactly where the oracle has an `except` clause.
 */
import fs from 'node:fs';
import { randomBytes } from 'node:crypto';
import { fsencode } from './codec.js';
import * as pathlib from './pathlib.js';
import { PyOSError, PyValueError } from './py.js';

function osError(error: unknown): never {
  const code = (error as NodeJS.ErrnoException | null)?.code;
  if (typeof code !== 'string' || !/^E[A-Z0-9]+$/u.test(code)) throw error;
  throw new PyOSError(code, code + ': ' + String((error as Error).message));
}
function call<T>(operation: () => T): T {
  try { return operation(); } catch (error) { return osError(error); }
}

const IGNORED = new Set(['ENOENT', 'ENOTDIR', 'EBADF', 'ELOOP']);
function probe(read: () => boolean): boolean {
  try { return read(); } catch (error) {
    if (error instanceof PyOSError) { if (IGNORED.has(error.errno)) return false; throw error; }
    if (error instanceof PyValueError) return false;
    throw error;
  }
}
/** `Path.exists()`: follows links. */
export const exists = (path: string): boolean => probe(() => { pathlib.stat(path); return true; });
/** `Path.is_dir()`: follows links. */
export const isDir = (path: string): boolean => probe(() => pathlib.stat(path).isDirectory());
/** `os.path.exists`: every `OSError` and `ValueError` is False. */
export function osPathExists(path: string): boolean {
  try { pathlib.stat(path); return true; } catch (error) { if (error instanceof PyOSError || error instanceof PyValueError) return false; throw error; }
}

/** `Path.parents`, nearest first. */
export function parents(path: string): string[] {
  const result: string[] = [];
  for (let current = pathlib.pathStr(path); ;) {
    const next = pathlib.parent(current);
    if (next === current) return result;
    result.push(next);
    current = next;
  }
}

/** `Path.mkdir(mode=mode)`: one level, `FileExistsError` when the name is taken. */
export const mkdir = (path: string, mode: number): void => call(() => fs.mkdirSync(fsencode(path), { mode }));
export const chmod = (path: string, mode: number): void => call(() => fs.chmodSync(fsencode(path), mode));
export const unlink = (path: string): void => call(() => fs.unlinkSync(fsencode(path)));
/** `Path.unlink(missing_ok=True)`. */
export function unlinkMissingOk(path: string): void {
  try { unlink(path); } catch (error) { if (!(error instanceof PyOSError && error.errno === 'ENOENT')) throw error; }
}
export const rmdir = (path: string): void => call(() => fs.rmdirSync(fsencode(path)));
export const symlink = (path: string, target: string): void => call(() => fs.symlinkSync(fsencode(target), fsencode(path)));
export const readlink = (path: string): string => call(() => fs.readlinkSync(fsencode(path), { encoding: 'utf8' }));
/** `os.replace`. */
export const replace = (source: string, target: string): void => call(() => fs.renameSync(fsencode(source), fsencode(target)));
/** `Path.iterdir()` as names; the order is the directory's, as in the oracle. */
export const listdir = (path: string): string[] => call(() => fs.readdirSync(fsencode(path), { encoding: 'utf8' }));
/** `shutil.rmtree`. */
export const rmtree = (path: string): void => call(() => { fs.lstatSync(fsencode(path)); fs.rmSync(fsencode(path), { recursive: true }); });
/** `os.access(path, os.X_OK)`. */
export function executable(path: string): boolean {
  try { fs.accessSync(fsencode(path), fs.constants.X_OK); return true; } catch { return false; }
}

/** `Path.write_bytes` / `Path.write_text`: create or truncate, mode 0666 under the umask. */
export function writeBytes(path: string, content: Uint8Array | string): void {
  call(() => fs.writeFileSync(fsencode(path), content, { mode: 0o666 }));
}
/** `open(path, 'xb')` + `os.fchmod` + write: the name must be free. */
export function writeExclusive(path: string, content: Uint8Array, mode: number): void {
  call(() => {
    const fd = fs.openSync(fsencode(path), 'wx', 0o666);
    try { fs.fchmodSync(fd, mode); fs.writeFileSync(fd, content); } finally { fs.closeSync(fd); }
  });
}

const NAME_CHARACTERS = 'abcdefghijklmnopqrstuvwxyz0123456789_';
/** `tempfile.mkstemp(dir=directory, prefix=prefix)`: a new 0600 file with eight random characters; the name is transient. */
export function mkstemp(directory: string, prefix: string): { fd: number; path: string } {
  for (let attempt = 0; attempt < 10000; attempt++) {
    const name = prefix + Array.from(randomBytes(8), byte => NAME_CHARACTERS[byte % NAME_CHARACTERS.length]).join('');
    const path = pathlib.joinPath(directory, name);
    try {
      return { fd: fs.openSync(fsencode(path), fs.constants.O_RDWR | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600), path };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'EEXIST') continue;
      return osError(error);
    }
  }
  throw new PyOSError('EEXIST', 'No usable temporary file name found');
}
