/**
 * A build shares no file with anything outside itself. Not in the oracle (`connectome_cook.py` has no counterpart).
 *
 * Bun hard-links package files from its cache into `app/node_modules`. With the cache of the account, every build of
 * that account shares inodes with it and so with each other, and an install into a new build writes to them: it sets
 * the mode of each `bin` file from its own umask, on the shared inode, which is a file of a build that may be running.
 * So the adapter gives each build a Bun cache of its own beside the builds, for the one cook run, and removes it
 * afterwards; and then holds what that is for: no file of the build has a name outside the build.
 */
import fs from 'node:fs';
import path from 'node:path';

/** The names the adapter adds to cook's environment. Cook passes a name on to Bun only where `COOK_PASS_ENV` selects it (cook `src/process-env.ts`). */
export const CACHE_ENV = 'BUN_INSTALL_CACHE_DIR';
export const PASS_ENV = 'COOK_PASS_ENV';

/** Where the Bun cache of one build is while cook runs: beside `builds`, on its file system, so that Bun links and does not copy. */
export function cache_dir(root: string, request: string): string {
  return path.join(root, '.bun-cache-' + request);
}

/** Remove a cache directory, or whatever has its name. A symbolic link is removed and not followed. */
export function remove(directory: string): void {
  fs.rmSync(directory, { recursive: true, force: true });
}

export interface Shared { files: number; outside: number; with_other_builds: number; examples: string[]; }

/** Every regular file under `directory`, with what the file system says of it. Symbolic links are not followed. */
function* regular_files(directory: string): Generator<[name: string, info: fs.BigIntStats]> {
  const pending = [directory];
  while (pending.length) {
    const here = pending.pop()!;
    for (const entry of fs.readdirSync(here, { withFileTypes: true })) {
      const full = path.join(here, entry.name);
      if (entry.isDirectory()) pending.push(full);
      else if (entry.isFile()) yield [full, fs.lstatSync(full, { bigint: true })];
    }
  }
}
const inode = (info: fs.BigIntStats): string => info.dev + ':' + info.ino;

/**
 * The files of the build `request` under `builds` that have a name elsewhere.
 *
 * `outside`: files with more links than names found in the build, so with a name somewhere outside it (two names of one
 * file inside the build are not a share). `with_other_builds`: those of them that are a file of another directory under
 * `builds`. `examples` names the first of the latter, by their names in this build.
 */
export function shared(builds: string, request: string, limit = 5): Shared {
  const found = new Map<string, { links: bigint; names: string[] }>();
  let files = 0;
  for (const [name, info] of regular_files(path.join(builds, request))) {
    files += 1;
    if (info.nlink < 2n) continue;
    const row = found.get(inode(info)) ?? { links: info.nlink, names: [] };
    row.names.push(name);
    found.set(inode(info), row);
  }
  const outside = new Map([...found].filter(([, row]) => BigInt(row.names.length) < row.links).map(([key, row]) => [key, row.names.sort()[0]!]));
  const others = new Set<string>();
  if (outside.size) {
    for (const entry of fs.readdirSync(builds, { withFileTypes: true })) {
      if (entry.name === request || !entry.isDirectory()) continue;
      for (const [, info] of regular_files(path.join(builds, entry.name))) if (info.nlink > 1n && outside.has(inode(info))) others.add(outside.get(inode(info))!);
    }
  }
  return { files, outside: outside.size, with_other_builds: others.size, examples: [...others].sort().slice(0, limit) };
}
