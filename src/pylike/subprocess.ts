/**
 * `subprocess.run(argv, check=False)` as the engine calls it: no shell, the first argument looked up on `PATH`, the
 * three standard streams named explicitly. A child that cannot be started is an `OSError`; a child killed by a signal
 * has the negative signal number as its return code.
 */
import os from 'node:os';
import { spawnSync } from 'node:child_process';
import { PyOSError, PyTypeError } from './py.js';

export type Stream = 'inherit' | 'ignore' | 'pipe';
export interface RunOptions { cwd?: string | null; env?: Readonly<Record<string, string>> | null; stdin?: Stream; stdout?: Stream; stderr?: Stream; }
export interface Completed { returncode: number; stdout: Buffer; stderr: Buffer; }

export function run(argv: readonly string[], { cwd = null, env = null, stdin = 'inherit', stdout = 'inherit', stderr = 'inherit' }: RunOptions = {}): Completed {
  if (!Array.isArray(argv) || !argv.length || argv.some(arg => typeof arg !== 'string')) throw new PyTypeError('expected str, bytes or os.PathLike object');
  if (argv.some(arg => arg.includes('\0'))) throw new PyTypeError('embedded null byte'); // ValueError in the oracle; neither is an OSError
  const done = spawnSync(argv[0]!, argv.slice(1), {
    ...(cwd === null ? {} : { cwd }), ...(env === null ? {} : { env: { ...env } }),
    stdio: [stdin, stdout, stderr], maxBuffer: Infinity, shell: false, windowsHide: true,
  });
  if (done.error) throw new PyOSError((done.error as NodeJS.ErrnoException).code ?? 'EIO', 'child could not be started');
  const signal = done.signal ? (os.constants.signals as Record<string, number>)[done.signal] ?? 1 : 0;
  return { returncode: done.status ?? -signal, stdout: done.stdout ?? Buffer.alloc(0), stderr: done.stderr ?? Buffer.alloc(0) };
}
