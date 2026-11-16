/**
 * A build shares no file with anything outside itself. Not in the oracle (`connectome_cook.py` has no counterpart).
 *
 * Bun hard-links package files from its cache into `app/node_modules`. With the cache of the account, every build of
 * that account shares inodes with it and so with each other, and an install into a new build writes to them: it sets
 * the mode of each `bin` file from its own umask, on the shared inode, which is a file of a build that may be running.
 * So the adapter gives each build a Bun cache of its own beside the builds, for the one cook run, and removes it
 * afterwards; and then holds what that is for: no file of the build has a name outside the build.
 *
 * Git does the same with a repository named by a local path: `git clone /a/repository` hard-links the object files. Two
 * builds with a connector from one local repository then share them. Cook's connector clone has no `--no-hardlinks`, so
 * the adapter hands Git, through cook, one `url.file://<path>.insteadOf = <path>` for each such repository: Git then
 * takes it over its transport and writes objects of the clone's own (`git_environment`). The check afterwards is the
 * same and is what holds; the rewrite only keeps a legitimate build from running into it.
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

const CACHE_PREFIX = '.bun-cache-';
/**
 * Remove every `ROOT/.bun-cache-*`: the cache of this request, and one a killed run left under any other request (a
 * signal ends the adapter before it removes its cache). Returns the names that were there. Whoever calls it holds the
 * root: the engine starts the adapter under the deployment's lock, so no other cook run of this root has a cache now.
 * The cache of `request` must go, or the call fails; another one that cannot be removed is left and not named.
 */
export function sweep(root: string, request: string): string[] {
  const found = fs.readdirSync(root).filter(name => name.startsWith(CACHE_PREFIX)).sort(), removed: string[] = [];
  for (const name of found) {
    try { remove(path.join(root, name)); } catch (error) {
      if (name === CACHE_PREFIX + request) throw error;
      continue;
    }
    removed.push(name);
  }
  return removed;
}

/**
 * The environment that keeps Git from hard-linking the objects of the local repositories `paths` into a clone: counted
 * `GIT_CONFIG_KEY_<n>`/`GIT_CONFIG_VALUE_<n>` entries, which cook hands its Git subprocesses as they are (cook
 * `src/process-env.ts`, `gitEnvironment`). Each says `url.file://<path>.insteadOf = <path>`: the repository is the same
 * and the clone records the path as its origin, as before; only the transport is Git's own and not a link of each file.
 * Empty for no path, so a deployment without a local source repository has no Git name in cook's environment.
 *
 * A path with `%` is left out (Git would decode it in a `file://` URL and look elsewhere), as is one with a line break.
 * Such a repository is cloned as before, and a build that then shares a file with another is refused as before.
 */
export function git_environment(paths: readonly string[]): Record<string, string> {
  const local = [...new Set(paths)].filter(name => name.startsWith('/') && !/[%\n\r\0]/u.test(name)).sort();
  if (!local.length) return {};
  const env: Record<string, string> = { GIT_CONFIG_COUNT: String(local.length) };
  local.forEach((name, index) => {
    env['GIT_CONFIG_KEY_' + index] = 'url.file://' + name + '.insteadOf';
    env['GIT_CONFIG_VALUE_' + index] = name;
  });
  return env;
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
