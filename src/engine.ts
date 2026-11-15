/**
 * Contract v1 `plan` and `apply` (CONTRACT.md 2.3, 2.4) on the TS engine.
 * The counterpart of `dispatch` in `oracle/shim/kitchen_v1.py`, which wraps the Python engine the same way.
 *
 * STATUS: library entry for the deploy corpus and for a rehearsal. It is not the operational CLI (`src/cli.ts` still
 * refuses; phase 7), and `spawn` is not ported.
 */
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { CONTRACT, VERSION } from './contract.js';
import { merge_layers } from './config.js';
import { ADAPTER_CONTRACT } from './cook.js';
import { Deployment, make_directory } from './deploy.js';
import type { Run } from './deploy.js';
import { SafeError, diagnostic } from './errors.js';
import type { Host } from './io/host.js';
import { realHost } from './io/host.js';
import { isDict, isStr, item, keysEqual, keysWithin } from './pylike/py.js';
import type { PyDict, PyValue } from './pylike/py.js';
import { dict } from './pylike/pydump.js';

const KEYS: Readonly<Record<string, ReadonlySet<string>>> = {
  plan: new Set(['base', 'root', 'unit_dir', 'layers', 'config', 'versions', 'engines', 'systemctl']),
  apply: new Set(['base', 'root', 'unit_dir', 'layers', 'config', 'versions', 'engines', 'systemctl', 'operator_files', 'phase']),
};
const ENVELOPE = new Set(['contract', 'command', 'request']);
const COOK = new Set(['cook']);
const TOOLS = new Set(['entrypoint', 'runtime', 'bun', 'node']);

/** An unusable invocation or envelope: exit status 2, `E_CONTRACT`. */
export class ContractError extends Error {}

export interface EngineOptions {
  host?: Host;
  /**
   * The first two words of the cook command, `[interpreter, adapter script]`: what is started. Default: this Node and
   * `cook-adapter.js` beside this file. They are no part of the request identity (ruling D5).
   */
  adapter?: readonly [string, string];
  /**
   * The words that stand for the adapter in the request identity. Default, and the only value a kitchen runs with:
   * `[ADAPTER_CONTRACT]`. The deploy corpus hands in the two words of the oracle's shim for one of its runs, so that the
   * build has the oracle's name and the two trees can be held byte for byte.
   */
  identity?: readonly string[];
  /** `connectome_deploy.run`, replaceable as the oracle's tests replace it. */
  run?: Run;
  /** Told of a failure that is not a `SafeError`, before it becomes the generic refusal. For tests; nothing is returned. */
  unexpected?: (error: unknown) => void;
}

/** The first two words of the cook command a kitchen starts: the Node it runs on and the adapter of its own install. */
export const adapter_command = (): [string, string] => [process.execPath, fileURLToPath(new URL('./cook-adapter.js', import.meta.url))];

export const envelope = (command: PyValue, core: PyDict): PyDict =>
  dict({ contract: CONTRACT, command, core, meta: dict({ kitchen: VERSION, artifact_sha256: null }) });

const get = (request: PyDict, key: string): PyValue => { const value = request.get(key); return value === undefined ? null : value; };

/** kitchen_v1.py:39-91, the `plan` and `apply` commands. Returns the receipt and the exit status. */
export function dispatch(data: PyValue, secrets: PyValue = null, options: EngineOptions = {}): [receipt: PyDict, status: number] {
  if (!isDict(data) || !keysEqual(data, ENVELOPE) || item(data, 'contract') !== CONTRACT) throw new ContractError();
  const command = item(data, 'command'), request = item(data, 'request');
  if (!isStr(command) || !Object.hasOwn(KEYS, command) || !isDict(request) || !keysWithin(request, KEYS[command]!)) throw new ContractError();
  const phase = request.has('phase') ? item(request, 'phase') : 'both';
  let prepared: PyDict | null = null;
  let stage = phase !== 'activate' ? 'prepare' : 'activate';
  if (!(phase === 'prepare' || phase === 'activate' || phase === 'both')) throw new ContractError();
  if (request.has('layers') === request.has('config')) throw new ContractError();
  const engines = get(request, 'engines');
  if (!isDict(engines) || !keysEqual(engines, COOK)) throw new ContractError();
  const cook = item(engines, 'cook');
  if (!isDict(cook) || !keysEqual(cook, TOOLS)) throw new ContractError();
  if ([...cook.values()].some(value => !isStr(value) || !value.startsWith('/'))) throw new ContractError();
  // This is the existing adapter's literal CLI, with no free-form shell command.
  const words: PyValue[] = ['--spec', '{recipe}', '--out', '{build_dir}', '--state', '{state_dir}', '--runtime', item(cook, 'runtime'),
    '--entrypoint', item(cook, 'entrypoint'), '--bun-path', item(cook, 'bun'), '--node-path', item(cook, 'node')];
  // What is started names this Node and this install. What the build is named by does not (ruling D5): the oracle's shim
  // hashes its interpreter and script path there (deploy.py:482), and every other word is the same in both.
  const argv: PyValue[] = [...(options.adapter ?? adapter_command()), ...words];
  const identity: PyValue[] = [...(options.identity ?? [ADAPTER_CONTRACT]), ...words];
  const host = options.host ?? realHost;
  let core: PyDict;
  try {
    const config = request.has('config') ? item(request, 'config') : merge_layers(item(request, 'layers'));
    const dep = new Deployment({
      root: item(request, 'root') as string, unit_dir: item(request, 'unit_dir') as string, config, versions: item(request, 'versions'),
      cook_command: argv, cook_identity: identity, systemctl: get(request, 'systemctl'), check: command === 'plan', base: get(request, 'base') as string | null,
      operator_files: get(request, 'operator_files'), host, ...(options.run ? { run: options.run } : {}),
    });
    let fd: number | null = null;
    try {
      if (command !== 'plan') {
        dep.validate_paths(); dep.preflight_activation(); make_directory(dep.root);
        // Serialize mutations of this agent's user units and current pointer.
        fd = fs.openSync(dep.lock_path(), 'a', 0o666);
        host.lock(fd);
      }
      const done: PyDict = new Map();
      if (phase === 'prepare' || phase === 'both') { prepared = dep.prepare(); done.set('prepare', prepared); }
      if (phase === 'activate' || phase === 'both') { stage = 'activate'; done.set('activate', dep.activate(secrets)); }
      done.set('changed', [...done.values()].some(part => item(part as PyDict, 'changed') === true));
      return [envelope(command, done), 0];
    } finally {
      if (fd !== null) fs.closeSync(fd);
    }
  } catch (error) {
    if (error instanceof SafeError) core = new Map(Object.entries(error.public()));
    else { options.unexpected?.(error); core = new Map(Object.entries(diagnostic('E_DEPLOY_FAILED'))); }
  }
  core.set('phase', stage);
  if (prepared !== null) core.set('prepare', prepared);
  return [envelope(command, core), 1];
}
