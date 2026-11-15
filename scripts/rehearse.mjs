#!/usr/bin/env node
// A rehearsal of contract v1 `plan` and `apply` (src/engine.ts) on a real account, with a real cook.
//
//   rehearse.mjs check|apply|verify --input <absolute rehearsal.json>
//
// Kitchen has no operational CLI yet (src/cli.ts refuses; phase 7). This is not it: it runs one deployment that is
// installed and never started, logs every step, and keeps what cook wrote before the adapter rewrites it.
//
//   check   read-only questions, the placeholder secrets file if it is absent, then `plan`. The engine writes nothing.
//   apply   the same, then `apply` (prepare and activate), then what was left.
//   verify  the read-only questions, `plan`, and what is there. Writes only its log.
//
// What it refuses to do, whatever the input says:
//   - run as root, or for an account or a home other than the ones the input names;
//   - any activation but `install_only`: the engine then installs the unit and its drop-in, reloads the user
//     manager and starts, stops and enables nothing (src/deploy.ts, `_install` and `_health`);
//   - ask the service manager anything but `is-active`, `show` and `daemon-reload` (rehearse/systemctl-readonly
//     stands in front of it and refuses the rest, also for the engine's own direct calls);
//   - start a tool that is not the adapter, that guard, or `systemd-analyze --user verify`;
//   - overwrite a secrets file: one that is there must be the placeholder file, byte for byte.
//
// Exit status: 0 done; 1 the engine refused (the receipt says why); 2 unusable invocation or contract; 3 this
// runner refused. Everything is logged under <home>/kitchen-rehearsal/<name>/: `rehearsal.log` (one JSON object per
// line), the receipts, what cook printed, cook's lock as cook wrote it, and after `apply` a copy of the build's files
// in the layout `test/corpus/corpus.py import-build` takes. A receipt is never written over: each call of the engine
// leaves `<NNN>-<mode>-<command>.receipt.json`, numbered on from the receipts already there, so a second `apply` keeps
// the first one's receipt beside its own.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { merge_layers } from '../dist/config.js';
import * as deploy from '../dist/deploy.js';
import { ContractError, dispatch } from '../dist/engine.js';
import * as inputs from '../dist/inputs.js';
import { realHost } from '../dist/io/host.js';
import { canonical, dict } from '../dist/pylike/pydump.js';
import { loads } from '../dist/pylike/pyjson.js';

export const SCHEMA = 'connectome.kitchen-rehearsal/v1';
const HERE = path.dirname(fileURLToPath(import.meta.url));
export const COOK_KEEP = path.join(HERE, 'rehearse', 'cook-keep');
export const SYSTEMCTL_GUARD = path.join(HERE, 'rehearse', 'systemctl-readonly');
export const ADAPTER = fileURLToPath(new URL('../dist/cook-adapter.js', import.meta.url));
const MODES = ['check', 'apply', 'verify'];
const INPUT_KEYS = ['schema', 'request', 'systemctl', 'secrets_template', 'files', 'expect'];
const REQUEST_KEYS = ['base', 'root', 'unit_dir', 'layers', 'versions', 'engines'];
const PLACEHOLDER = 'placeholder';
const TEXT_LIMIT = 1 << 20;
const RECEIPT = /^(\d{3,})-[a-z]+-[a-z]+\.receipt\.json$/u;
/** The files of a build `import-build` takes, beside the launcher and the unit the lock names. */
const BUILD_FILES = ['connectome.lock', 'deployment.json', 'cook-recipe.json', 'cook-layout.json', '.cook-layout.json', '.prepared'];

/** This runner's own refusal: exit status 3. */
export class Refusal extends Error {}
const need = (condition, message) => { if (!condition) throw new Refusal(message); };
const sha256 = content => createHash('sha256').update(content).digest('hex');
function sha256File(file) {
  const hash = createHash('sha256'), fd = fs.openSync(file, 'r'), buffer = Buffer.alloc(1 << 20);
  try { for (let read; (read = fs.readSync(fd, buffer, 0, buffer.length, null)) > 0;) hash.update(buffer.subarray(0, read)); } finally { fs.closeSync(fd); }
  return hash.digest('hex');
}
/** A value of the engine (dicts are Maps, numbers may be Python floats) as plain JSON for a log line. */
const plain = value => JSON.parse(canonical(value));
const octal = mode => (mode & 0o7777).toString(8).padStart(4, '0');

function describe(file) {
  let info;
  try { info = fs.lstatSync(file); } catch (error) { return { path: file, absent: error.code ?? true }; }
  const row = { path: file, mode: octal(info.mode), uid: info.uid };
  if (info.isSymbolicLink()) return { ...row, link: fs.readlinkSync(file) };
  if (info.isDirectory()) return { ...row, kind: 'directory' };
  if (!info.isFile()) return { ...row, kind: 'other' };
  row.size = info.size;
  try { if (info.size <= TEXT_LIMIT) row.sha256 = sha256File(file); } catch (error) { row.unreadable = error.code ?? true; }
  return row;
}
function list(directory) {
  try { return fs.readdirSync(directory).sort().map(name => describe(path.join(directory, name))); } catch (error) { return { path: directory, absent: error.code ?? true }; }
}

/**
 * One rehearsal. Returns the exit status. `host` is the engine's host seam (src/io/host.ts) and `start` is what starts
 * a tool the runner has let through; a test hands in a model of a host and an adapter that does not ask git about the
 * cook checkout (test/corpus/rehearse_driver.mjs). The command line never does.
 */
export function rehearse(mode, inputFile, { host = realHost, start = deploy.run, echo = line => process.stderr.write(line + '\n') } = {}) {
  let log = () => {};
  try {
    need(MODES.includes(mode) && typeof inputFile === 'string' && path.isAbsolute(inputFile), 'usage: rehearse.mjs check|apply|verify --input <absolute rehearsal.json>');
    const text = fs.readFileSync(inputFile);
    const read = () => loads(text);
    const input = read();
    need(input instanceof Map && [...input.keys()].every(key => INPUT_KEYS.includes(key)) && INPUT_KEYS.every(key => input.has(key)) && input.get('schema') === SCHEMA,
      'the input is not a ' + SCHEMA + ' document with exactly the keys ' + INPUT_KEYS.join(', '));
    const request = input.get('request');
    need(request instanceof Map && REQUEST_KEYS.every(key => request.has(key)) && request.size === REQUEST_KEYS.length, 'the request must hold exactly ' + REQUEST_KEYS.join(', '));
    const real = input.get('systemctl');
    need(Array.isArray(real) && real.length === 2 && path.isAbsolute(String(real[0])) && real[1] === '--user', 'systemctl must be [<absolute systemctl>, "--user"]');
    const expect = input.get('expect');
    need(expect instanceof Map, 'expect must be an object');

    // Who and where. Nothing has been written yet.
    const uid = process.geteuid();
    need(uid !== 0, 'a rehearsal never runs as root');
    const home = host.home(uid);
    need(expect.get('home') === home && process.env.HOME === home, 'the input is for another home than this account\'s, or HOME is not that home');
    if (expect.has('account')) need(os.userInfo().username === expect.get('account'), 'the input is for another account');
    const effective = merge_layers(read().get('request').get('layers'));
    const name = effective.get('name');
    need(typeof name === 'string' && /^[a-z0-9][a-z0-9-]{0,62}$/u.test(name), 'the deployment has no usable name');
    need(effective.get('activation') === 'install_only', 'a rehearsal installs and never starts: activation must be install_only');
    const unit = 'connectome-' + name + '.service';
    const exclusive = (effective.get('exclusive_with') ?? []).map(record => [record.get('scope'), record.get('unit')]);
    need(exclusive.every(([, other]) => other !== unit), 'the deployment excludes its own unit');
    const cook = request.get('engines')?.get?.('cook');
    need(cook instanceof Map && cook.get('runtime') === COOK_KEEP, 'the cook runtime must be ' + COOK_KEEP + ': it runs Bun and keeps cook\'s lock as cook wrote it');
    // The user manager's runtime directory: without it the unit verifier cannot run, and the engine would learn that only
    // after cook, at the end of activation (E_UNIT_VERIFY, and everything taken back).
    const runtime = process.env.XDG_RUNTIME_DIR;
    need(typeof runtime === 'string' && fs.existsSync(runtime) && fs.statSync(runtime).isDirectory(), 'XDG_RUNTIME_DIR must name the runtime directory of this account\'s user manager');
    const root = request.get('root'), unitDir = request.get('unit_dir'), stateDir = effective.get('state_dir'), secretsFile = effective.get('secrets_file');
    need([root, unitDir, stateDir, secretsFile].every(value => typeof value === 'string' && value.startsWith(home + '/')), 'root, unit_dir, state_dir and secrets_file must lie in this account\'s home');

    // The log. From here on every step is written down before the next one starts.
    const keep = path.join(home, 'kitchen-rehearsal', name);
    fs.mkdirSync(keep, { recursive: true, mode: 0o700 });
    need(fs.lstatSync(keep).isDirectory() && fs.lstatSync(keep).uid === uid && (fs.lstatSync(keep).mode & 0o077) === 0, 'the rehearsal directory must be this account\'s, mode 0700');
    const logFile = path.join(keep, 'rehearsal.log'), guardLog = path.join(keep, 'systemctl-calls.log');
    let sequence = 0;
    log = (step, detail = {}) => {
      const line = JSON.stringify({ at: new Date().toISOString(), n: ++sequence, run: mode, step, ...detail });
      fs.appendFileSync(logFile, line + '\n', { mode: 0o600 });
      echo(line);
    };
    // One receipt per call of the engine, numbered on from the highest number in the directory, and never written over.
    const saveReceipt = (command, content) => {
      const last = Math.max(0, ...fs.readdirSync(keep).map(leaf => Number(RECEIPT.exec(leaf)?.[1] ?? 0)));
      const file = path.join(keep, String(last + 1).padStart(3, '0') + '-' + mode + '-' + command + '.receipt.json');
      fs.writeFileSync(file, content, { mode: 0o600, flag: 'wx' });
      return file;
    };
    log('start', {
      input: inputFile, input_sha256: sha256(text), runner: fileURLToPath(import.meta.url), adapter: [process.execPath, ADAPTER], node: process.version,
      uid, account: os.userInfo().username, home, keep, name, unit, cwd: process.cwd(),
      environment_names: Object.keys(process.env).sort(), xdg_runtime_dir: process.env.XDG_RUNTIME_DIR ?? null,
      // No address is asked for and nothing is sent: only whether this process has an interface besides loopback.
      interfaces_besides_loopback: Object.entries(os.networkInterfaces()).filter(([, addresses]) => addresses.some(({ internal }) => !internal)).map(([label]) => label).sort(),
    });

    // The pinned inputs, read and never written.
    const files = input.get('files');
    need(files instanceof Map, 'files must map each pinned input to its sha256');
    for (const [file, digest] of files) {
      const found = sha256File(file);
      log('pinned input', { path: file, sha256: found, as_pinned: found === digest });
      need(found === digest, 'a pinned input is not the bytes the rehearsal was prepared with: ' + file);
    }
    for (const [key, tool] of cook) {
      const row = describe(tool);
      log('engine path', { key, ...row });
      need(!row.absent, 'an engine path is missing: ' + key);
    }

    // The target, before anything is asked of the engine.
    const dropin = path.join(unitDir, unit + '.d');
    const target = () => ({ root: describe(root), current: describe(path.join(root, 'current')), unit: describe(path.join(unitDir, unit)), dropin: list(dropin),
      state_dir: describe(stateDir), secrets_file: describe(secretsFile), bookkeeping: list(path.join(root, 'state')), builds: list(path.join(root, 'builds')) });
    const before = target();
    log('target before', before);
    const ours = !before.root.absent && fs.existsSync(path.join(root, '.deploy-lock'));
    need(before.root.absent || ours, 'the deployment root exists and is not one an engine made: ' + root);
    need(ours || (before.unit.absent && before.dropin.absent), 'a unit or a drop-in of this name exists and no deployment of ours owns it');

    // The service manager: read-only questions through the guard.
    const guard = [SYSTEMCTL_GUARD, guardLog, real[0], '--user'];
    const ask = args => {
      const done = spawnSync(guard[0], [guardLog, real[0], ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
      const answer = { argv: args, status: done.status, stdout: (done.stdout ?? '').trim() };
      log('service manager asked', answer);
      return answer;
    };
    const refusedVerb = spawnSync(guard[0], [guardLog, real[0], '--user', 'start', unit], { stdio: 'ignore' }).status;
    log('guard self-test', { asked: 'start ' + unit, status: refusedVerb, refused: refusedVerb === 97 });
    need(refusedVerb === 97, 'the service-manager guard did not refuse a start');
    const exclusion = exclusive.map(([scope, other]) => ask(['--' + scope, 'is-active', other]));
    // The engine's own question (src/deploy.ts, `_refuse_active`), asked once before it is.
    const own = ask(['--user', 'show', '-p', 'ActiveState', '--value', unit]);
    need(own.status === 0, 'the user service manager did not answer; is XDG_RUNTIME_DIR set for this account?');
    need(own.stdout === 'inactive' || own.stdout === 'failed', 'the unit of this deployment is not inactive');
    void exclusion;

    // The secrets file: placeholders under the declared names. Written only when absent; never changed.
    const names = deploy.secret_names(effective);
    const template = fs.readFileSync(input.get('secrets_template'));
    const values = JSON.parse(template.toString('utf8'));
    need(Object.keys(values).sort().join('\n') === [...names].sort().join('\n') && Object.values(values).every(value => typeof value === 'string' && value.includes(PLACEHOLDER)),
      'the secrets template must hold exactly the declared names, each with a value that says ' + PLACEHOLDER);
    if (before.secrets_file.absent) {
      need(mode !== 'verify', 'verify writes nothing, and the secrets file is absent');
      const directory = path.dirname(secretsFile);
      if (!fs.existsSync(directory)) {
        // As the engine makes a directory (src/deploy.ts, `make_directory`): missing parents 0755, the directory itself 0700.
        const missing = [];
        for (let current = directory; !fs.existsSync(current); current = path.dirname(current)) missing.unshift(current);
        for (const current of missing) { fs.mkdirSync(current); fs.chmodSync(current, current === directory ? 0o700 : 0o755); log('made directory', describe(current)); }
      }
      fs.writeFileSync(secretsFile, template, { mode: 0o600, flag: 'wx' });
      fs.chmodSync(secretsFile, 0o600);
      log('wrote placeholder secrets file', { ...describe(secretsFile), names });
    } else {
      need(Buffer.compare(fs.readFileSync(secretsFile), template) === 0, 'a secrets file is there and is not the placeholder file; it is left alone');
      log('placeholder secrets file already there', { ...describe(secretsFile), names });
    }

    // What the engine may start: its adapter, the guarded service manager, the unit verifier. Each call is logged.
    const run = (argv, options = {}) => {
      const kind = options.adapter && argv[0] === process.execPath && argv[1] === ADAPTER ? 'adapter'
        : argv[0] === SYSTEMCTL_GUARD && argv.length === 5 && argv[4] === 'daemon-reload' ? 'daemon-reload'
          : options.unit_verify && path.basename(argv[0]) === 'systemd-analyze' && argv[1] === '--user' && argv[2] === 'verify' ? 'unit verifier' : null;
      if (kind === null) { log('tool refused', { argv }); throw new Error('the rehearsal does not start this tool'); }
      const started = Date.now();
      log('tool started', { kind, argv, cwd: options.cwd ?? null });
      try { start(argv, options); } catch (error) {
        log('tool failed', { kind, seconds: (Date.now() - started) / 1000, error: typeof error?.public === 'function' ? error.public() : String(error) });
        throw error;
      }
      log('tool done', { kind, seconds: (Date.now() - started) / 1000 });
    };
    const unexpected = error => log('engine failure that is not a refusal', { error: String(error?.stack ?? error) });
    const call = (command, extra = {}) => {
      const body = read().get('request');
      body.set('systemctl', guard);
      for (const [key, value] of Object.entries(extra)) body.set(key, value);
      log(command + ' asked', { keys: [...body.keys()].sort() });
      const [receipt, status] = dispatch(dict({ contract: 'connectome.kitchen/v1', command, request: body }), null, { host, run, unexpected });
      const file = saveReceipt(command, canonical(receipt) + '\n');
      log(command + ' answered', { status, receipt: file, core: plain(receipt.get('core')) });
      return [receipt.get('core'), status];
    };

    const [planned, planStatus] = call('plan');
    if (planStatus !== 0) { log('end', { status: 1, why: 'plan was refused' }); return 1; }
    const build = planned.get('prepare').get('build_dir'), requestId = path.basename(build);
    log('build named', { build_dir: build, request_id: requestId, expected: expect.get('request_id') ?? null });
    need(!expect.has('request_id') || expect.get('request_id') === requestId,
      'the engine names the build otherwise than the rehearsal was prepared for: an input or an engine path of the request is not what it was taken to be');

    let status = 0;
    if (mode === 'apply') {
      const [, applyStatus] = call('apply', { operator_files: inputs.collect(effective), phase: 'both' });
      status = applyStatus === 0 ? 0 : 1;
    }
    if (mode !== 'check') {
      const after = target();
      log('target after', after);
      log('build directory', { entries: list(build) });
      log('unit directory entries of this deployment', { entries: (Array.isArray(list(unitDir)) ? list(unitDir) : []).filter(row => path.basename(row.path).startsWith(unit)), wants: list(path.join(unitDir, 'default.target.wants')) });
      for (const property of ['ActiveState', 'LoadState', 'UnitFileState', 'FragmentPath', 'DropInPaths', 'EnvironmentFiles']) ask(['--user', 'show', '-p', property, '--value', unit]);
      exclusive.forEach(([scope, other]) => ask(['--' + scope, 'is-active', other]));
      log('service manager calls', { log: guardLog, verbs: fs.existsSync(guardLog) ? fs.readFileSync(guardLog, 'utf8').trim().split('\n').map(line => line.split(' ').slice(1, 5).join(' ')) : [] });
      if (fs.existsSync(path.join(build, '.prepared'))) status = Math.max(status, keepBuild(build, requestId, root, keep, log));
    }
    log('end', { status });
    return status;
  } catch (error) {
    const status = error instanceof ContractError ? 2 : 3;
    const why = error instanceof ContractError ? 'the request is not a contract v1 request' : error instanceof Refusal ? error.message : String(error?.stack ?? error);
    try { log('end', { status, why }); } catch { /* the log itself failed */ }
    echo('rehearse: ' + why);
    return status;
  }
}

/** A copy of the build's files for `import-build`, cook's lock as cook wrote it beside it, and what the two locks show. */
function keepBuild(build, requestId, root, keep, log) {
  const copy = path.join(keep, 'build-' + requestId.slice(0, 8));
  fs.rmSync(copy, { recursive: true, force: true });
  fs.mkdirSync(copy, { mode: 0o700 });
  const lockText = fs.readFileSync(path.join(build, 'connectome.lock'), 'utf8');
  const lock = JSON.parse(lockText);
  const sums = [];
  const put = (source, leaf) => {
    if (!fs.existsSync(source)) { log('not in the build', { path: source }); return; }
    fs.copyFileSync(source, path.join(copy, leaf));
    fs.chmodSync(path.join(copy, leaf), 0o600);
    sums.push(sha256File(path.join(copy, leaf)) + '  ' + leaf);
  };
  for (const leaf of [...BUILD_FILES, path.basename(lock.launch.script), path.basename(lock.launch.userUnit)]) put(path.join(build, leaf), leaf);
  fs.writeFileSync(path.join(copy, 'SHA256SUMS'), sums.join('\n') + '\n', { mode: 0o600 });
  // Beside the copy, not in it: what cook wrote and the adapter then rewrote, and the host's own recipe the lock names first.
  const cooked = fs.readdirSync(keep).filter(leaf => /^cook-.*\.lock$/u.test(leaf)).sort().pop();
  const outside = lock.outputs.map(entry => entry.path).filter(entry => entry.startsWith('../'));
  for (const entry of outside) {
    const source = path.resolve(build, entry);
    if (source.startsWith(root + '/') && fs.existsSync(source)) { fs.copyFileSync(source, path.join(keep, 'output-outside-build.' + path.basename(source))); log('output outside the build', { ...describe(source), named: entry }); }
  }
  if (!cooked) { log('cook lock as written', { kept: false }); return 3; }
  const raw = fs.readFileSync(path.join(keep, cooked), 'utf8');
  const order = value => (Array.isArray(value) ? value.map(order) : value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).map(([key, item]) => [key, order(item)])) : null);
  const sorted = value => (Array.isArray(value) ? value.map(sorted) : value && typeof value === 'object' ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])])) : value);
  const parsed = JSON.parse(raw);
  const without = JSON.parse(lockText);
  for (const dependency of without.hostDependencies ?? []) delete dependency.provenance;
  log('cook lock as written', {
    kept: path.join(keep, cooked), sha256: sha256(raw), bytes: raw.length, top_level_keys_in_order: Object.keys(parsed),
    keys_sorted_at_every_level: JSON.stringify(order(parsed)) === JSON.stringify(order(sorted(parsed))),
    ends_with_newline: raw.endsWith('\n'), indent: /^\{\n( +)"/u.exec(raw)?.[1].length ?? null,
    lock_after_adapter: { sha256: sha256(lockText), top_level_keys_in_order: Object.keys(lock), keys_sorted_at_every_level: JSON.stringify(order(lock)) === JSON.stringify(order(sorted(lock))) },
    same_content_but_for_the_provenance_the_adapter_adds: JSON.stringify(sorted(parsed)) === JSON.stringify(sorted(without)),
  });
  log('build copy', { directory: copy, files: sums });
  return 0;
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  // What the engine and the adapter write without naming a mode must not depend on the caller's umask.
  process.umask(0o022);
  const [mode, flag, inputFile, ...rest] = process.argv.slice(2);
  process.exitCode = flag === '--input' && !rest.length ? rehearse(mode, inputFile) : rehearse(null, null);
}
