/**
 * STUB of one seam. This is NOT a port of `connectome_archive.py`; that module
 * is the next slice (plan section 3.1, module 4).
 *
 * `lock.py` imports three names from the archive module lazily, inside
 * `map_lock`: `npm_name` and `validate_dependency_record` (lock.py:256-258) and
 * `check_dependencies` (lock.py:282-284). `lock.ts` loads this file lazily at
 * the same three places, and this file holds exactly those three names.
 *
 * Until the port lands:
 *   - unbound, every call raises `ArchiveNotPorted`. It is an `ImportError`,
 *     the class the oracle would raise if its module were missing: not a
 *     `ValueError`, so no handler in `lock.ts` turns it into a lock refusal or
 *     an acceptance. A lock with host dependencies, and any call with a
 *     `config`, therefore fails loudly instead of being judged.
 *   - `bind_seam` installs an implementation for tests and for the
 *     differential harness, which replays the oracle's own answers and checks
 *     that `lock.ts` asks the same questions in the same order.
 *
 * The archive port replaces this whole file, `bind_seam` included. `lock.ts`
 * does not change.
 */
import { PyImportError } from './pylike/py.js';
import type { PyDict, PyValue } from './pylike/py.js';

export class ArchiveNotPorted extends PyImportError {
  constructor() { super('connectome_archive is not ported yet; only the lock seam is declared'); }
}

/** The three functions `lock.py` imports from `connectome_archive`. A refusal is a `ValueError` (`ArchiveError` in the oracle). */
export interface ArchiveSeam {
  /** archive.py:32 */
  npm_name(value: PyValue): boolean;
  /** archive.py:192 */
  validate_dependency_record(record: PyValue): void;
  /** archive.py:206 */
  check_dependencies(data: PyDict, config: PyValue, pin: PyValue, framework_record: PyValue): void;
}

let bound: ArchiveSeam | null = null;
/** Test and harness hook; `null` restores the fail-closed default. Goes away with the stub. */
export function bind_seam(seam: ArchiveSeam | null): void { bound = seam; }
function seam(): ArchiveSeam {
  if (bound === null) throw new ArchiveNotPorted();
  return bound;
}

export function npm_name(value: PyValue): boolean { return seam().npm_name(value); }
export function validate_dependency_record(record: PyValue): void { seam().validate_dependency_record(record); }
export function check_dependencies(data: PyDict, config: PyValue, pin: PyValue, framework_record: PyValue = null): void {
  seam().check_dependencies(data, config, pin, framework_record);
}
