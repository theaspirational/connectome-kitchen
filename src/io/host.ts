/**
 * The host seam of the deployment engine: who owns a path, which account the engine runs as, where that account's
 * home is, and how the per-agent lock is taken.
 *
 * The oracle reads these through `Path.lstat`, `os.geteuid`/`os.getegid`/`os.getgroups` and `pwd.getpwuid`, and its
 * tests replace exactly those (`oracle/tests/deploy_fixtures.py`, `host_model` in `test/corpus/corpus.py`). A JavaScript
 * module cannot be patched from outside, so the engine takes a `Host` and asks it wherever the oracle calls
 * `Path.lstat` or `pwd.getpwuid`. `realHost` is the host itself; nothing else is used outside tests.
 */
import fs from 'node:fs';
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import * as pathlib from '../pylike/pathlib.js';
import { PyKeyError, PyOSError } from '../pylike/py.js';

/** What the engine reads of an `os.stat_result`: `st_mode` (type and permission bits), `st_uid`, `st_gid`. */
export interface PathInfo { readonly mode: number; readonly uid: number; readonly gid: number; }
/** `(os.geteuid(), {os.getegid(), *os.getgroups()})`. */
export interface Account { readonly uid: number; readonly groups: ReadonlySet<number>; }

export interface Host {
  /** `Path(path).lstat()`: `PyOSError` when the path cannot be read. */
  lstat(path: string): PathInfo;
  /** The account the engine runs as. */
  account(): Account;
  /** `pwd.getpwuid(uid).pw_dir`: `PyKeyError` when the account has no record. */
  home(uid: number): string;
  /** `fcntl.flock(fd, LOCK_EX)` on an open descriptor: returns when the lock is held; it is released when `fd` is closed. */
  lock(fd: number): void;
}

const FLOCK = '/usr/bin/flock';

export const realHost: Host = Object.freeze({
  lstat: (path: string): PathInfo => pathlib.lstat(path),
  account: (): Account => ({ uid: process.geteuid!(), groups: new Set([process.getegid!(), ...process.getgroups!()]) }),
  home(uid: number): string {
    // Node reads the password database for the effective user only (`uv_os_get_passwd`); the engine asks for no other.
    if (uid !== process.geteuid!()) throw new PyKeyError('getpwuid(): uid not found: ' + uid);
    try { return os.userInfo().homedir; } catch { throw new PyKeyError('getpwuid(): uid not found: ' + uid); }
  },
  lock(fd: number): void {
    // Node has no flock(2). util-linux `flock <fd>` locks the open file description it inherits and exits; the
    // description is shared with `fd` here, so the lock lasts until this process closes it. It is the same lock the
    // oracle takes with `fcntl.flock`, so the two engines exclude each other on one root.
    const done = spawnSync(FLOCK, ['--exclusive', '3'], { stdio: ['ignore', 'ignore', 'ignore', fd] });
    if (done.error || done.status !== 0) throw new PyOSError('ENOLCK', 'cannot take the deployment lock');
  },
});

export const S_IFMT = 0o170000;
export const isDirectory = (info: PathInfo): boolean => (info.mode & S_IFMT) === fs.constants.S_IFDIR;
export const isRegular = (info: PathInfo): boolean => (info.mode & S_IFMT) === fs.constants.S_IFREG;
export const isLink = (info: PathInfo): boolean => (info.mode & S_IFMT) === fs.constants.S_IFLNK;
/** `stat.S_IMODE`. */
export const permissions = (info: PathInfo): number => info.mode & 0o7777;
