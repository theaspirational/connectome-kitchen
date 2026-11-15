#!/usr/bin/env node
// A kitchen takes over a deployment the role made and that is running: contract v1 `plan` and `apply` (src/engine.ts)
// on the account of the agent, with a real cook, in two steps that two operators can run.
//
//   takeover.mjs check|prepare|verify|start --input <absolute takeover.json>
//
// Kitchen has no operational CLI yet (src/cli.ts refuses; phase 7). This is not it: it runs one deployment whose
// activation is `start`, logs every step, and keeps the build apart from the restart.
//
//   check    read-only questions, then `plan`. The engine writes nothing.
//   prepare  the same, then `apply` with `phase: prepare`: cook makes the new build beside the running one. The engine
//            adds the build directory and one entry of `state/ownership.json`. The pointer `current`, the unit, its
//            drop-in, the enable link and the secrets are not touched, and the service manager is only asked.
//   verify   the read-only questions, `plan`, what is there, and whether the secrets file gives the new build the
//            environment the running build has (names and yes or no; never a value). Writes only its log.
//   start    `apply` with both phases on a build that `prepare` made: the environment file, the pointer, the unit
//            links and the drop-in, one `daemon-reload`, one `restart` of the unit, health. The engine takes all of
//            it back and restarts the previous build if a step fails (src/deploy.ts, `activate`).
//
// Why not `activation: install_only` for the first step: the engine refuses it while the unit is active
// (`E_UNIT_ACTIVE`, src/deploy.ts `preflight_activation`), and it would move the pointer and the unit.
//
// What it refuses to do, whatever the input says:
//   - run as root, or for an account or a home other than the ones the input names;
//   - any activation but `start`, or a deployment root no engine made;
//   - in `check`, `prepare` and `verify`: ask the service manager anything but `is-active` and `show`
//     (takeover/systemctl-guard stands in front of it, also for the engine's own direct calls), and start any tool but
//     the adapter (`prepare` only);
//   - in `start`: anything of the service manager but those two, `daemon-reload` and `restart` of this deployment's
//     one unit; cook; a build that is not prepared;
//   - write, copy or log a secret value. The engine reads the secrets file in place in `start`.
//
// Exit status: 0 done; 1 the engine refused (the receipt says why); 2 unusable invocation or contract; 3 this
// runner refused. Everything is logged under <home>/kitchen-takeover/<name>/: `takeover.log` (one JSON object per
// line), `systemctl-calls.log`, and one receipt per call of the engine, `<NNN>-<mode>-<command>.receipt.json`,
// numbered on and never written over.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { merge_layers } from '../dist/config.js';
import * as deploy from '../dist/deploy.js';
import { ContractError, dispatch } from '../dist/engine.js';
import { realHost } from '../dist/io/host.js';
import { canonical, dict } from '../dist/pylike/pydump.js';
import { loads } from '../dist/pylike/pyjson.js';

export const SCHEMA = 'connectome.kitchen-takeover/v1';
const HERE = path.dirname(fileURLToPath(import.meta.url));
export const SYSTEMCTL_GUARD = path.join(HERE, 'takeover', 'systemctl-guard');
export const ADAPTER = fileURLToPath(new URL('../dist/cook-adapter.js', import.meta.url));
const MODES = ['check', 'prepare', 'verify', 'start'];
const INPUT_KEYS = ['schema', 'request', 'systemctl', 'files', 'recipe', 'expect'];
const REQUEST_KEYS = ['base', 'root', 'unit_dir', 'layers', 'versions', 'engines'];
const EXPECT_KEYS = ['home', 'account', 'request_id', 'live_build'];
const TEXT_LIMIT = 1 << 20;
const RECEIPT = /^(\d{3,})-[a-z]+-[a-z]+\.receipt\.json$/u;
const HEX = /^[a-f0-9]{64}$/u;
const SHOWN = ['ActiveState', 'SubState', 'MainPID', 'NRestarts', 'ExecMainStartTimestamp', 'LoadState', 'UnitFileState', 'FragmentPath', 'DropInPaths', 'EnvironmentFiles'];

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

/** What a path is. `digest: false` for a file whose content is a secret: its mode, owner and size only. */
function describe(file, digest = true) {
  let info;
  try { info = fs.lstatSync(file); } catch (error) { return { path: file, absent: error.code ?? true }; }
  const row = { path: file, mode: octal(info.mode), uid: info.uid };
  if (info.isSymbolicLink()) return { ...row, link: fs.readlinkSync(file) };
  if (info.isDirectory()) return { ...row, kind: 'directory' };
  if (!info.isFile()) return { ...row, kind: 'other' };
  row.size = info.size;
  if (!digest) return row;
  try { if (info.size <= TEXT_LIMIT) row.sha256 = sha256File(file); } catch (error) { row.unreadable = error.code ?? true; }
  return row;
}
function list(directory, digest = true) {
  try { return fs.readdirSync(directory).sort().map(name => describe(path.join(directory, name), digest)); } catch (error) { return { path: directory, absent: error.code ?? true }; }
}

/**
 * Whether the secrets file gives the build the environment file the engine would write, and whether that is the one
 * of the build `current` names. Returns names and yes or no. No value, no length and no digest of a value leaves here.
 */
function secretsView(effective, names, secretsFile, root, current) {
  let values;
  try { values = JSON.parse(fs.readFileSync(secretsFile, 'utf8')); } catch { return { readable: false }; }
  if (values === null || typeof values !== 'object' || Array.isArray(values)) return { readable: false };
  const missing = names.filter(name => !Object.hasOwn(values, name));
  const notText = names.filter(name => Object.hasOwn(values, name) && typeof values[name] !== 'string');
  const direct = deploy.direct_secret_names(effective);
  const multiline = direct.filter(name => typeof values[name] === 'string' && /[\u0000\n\r]/u.test(values[name]));
  const view = { readable: true, declared: names, missing, not_text: notText, multiline_or_nul: multiline, names_beside_the_declared: Object.keys(values).filter(name => !names.includes(name)).length };
  if (missing.length || notText.length || multiline.length) return view;
  // The environment file as `activate` words it (src/deploy.ts): the direct names, and each secret file's name with its path.
  const records = (effective.get('secret_files') ?? []).map(record => [record.get('env'), record.get('secret')]);
  const env = new Map(direct.map(name => [name, values[name]]));
  for (const [name] of records) env.set(name, path.join(root, 'state', 'secret-files', name));
  const text = [...env].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).map(([name, value]) => name + '="' + value.replaceAll('\\', '\\\\').replaceAll('"', '\\"') + '"\n').join('');
  const same = file => { try { return fs.readFileSync(file, 'utf8') === text; } catch { return null; } };
  view.environment_file_of_current_build_is_what_start_would_write = current === null ? null : same(path.join(current, 'secrets.env'));
  view.secret_files_are_what_start_would_write = Object.fromEntries(records.map(([name, secret]) => {
    try { return [name, fs.readFileSync(path.join(root, 'state', 'secret-files', name)).equals(Buffer.from(values[secret], 'utf8'))]; } catch { return [name, null]; }
  }));
  return view;
}

/**
 * One step of a takeover. Returns the exit status. `host` is the engine's host seam (src/io/host.ts) and `start` is
 * what starts a tool the runner has let through; a test hands in a model of a host and an adapter that does not ask
 * git about the cook checkout (test/corpus/takeover_driver.mjs). The command line never does.
 */
export function takeover(mode, inputFile, { host = realHost, start = deploy.run, echo = line => process.stderr.write(line + '\n') } = {}) {
  let log = () => {};
  try {
    need(MODES.includes(mode) && typeof inputFile === 'string' && path.isAbsolute(inputFile), 'usage: takeover.mjs check|prepare|verify|start --input <absolute takeover.json>');
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
    need(expect instanceof Map && EXPECT_KEYS.every(key => typeof expect.get(key) === 'string') && expect.size === EXPECT_KEYS.length, 'expect must hold exactly ' + EXPECT_KEYS.join(', '));
    const wanted = expect.get('request_id'), live = expect.get('live_build');
    need(HEX.test(wanted) && HEX.test(live) && wanted !== live, 'expect.request_id and expect.live_build must be two different build names');

    // Who and where. Nothing has been written yet.
    const uid = process.geteuid();
    need(uid !== 0, 'a takeover never runs as root: the engine runs as the account of the agent');
    const home = host.home(uid);
    need(expect.get('home') === home && process.env.HOME === home, 'the input is for another home than this account\'s, or HOME is not that home');
    need(os.userInfo().username === expect.get('account'), 'the input is for another account');
    const effective = merge_layers(read().get('request').get('layers'));
    const name = effective.get('name');
    need(typeof name === 'string' && /^[a-z0-9][a-z0-9-]{0,62}$/u.test(name), 'the deployment has no usable name');
    need((effective.get('activation') ?? 'start') === 'start', 'a takeover is of a deployment that runs: activation must be start');
    const unit = 'connectome-' + name + '.service';
    const exclusive = (effective.get('exclusive_with') ?? []).map(record => [record.get('scope'), record.get('unit')]);
    need(exclusive.every(([, other]) => other !== unit), 'the deployment excludes its own unit');
    const cook = request.get('engines')?.get?.('cook');
    need(cook instanceof Map, 'the request names no cook engines');
    const runtime = process.env.XDG_RUNTIME_DIR;
    need(typeof runtime === 'string' && fs.existsSync(runtime) && fs.statSync(runtime).isDirectory(), 'XDG_RUNTIME_DIR must name the runtime directory of this account\'s user manager');
    const root = request.get('root'), unitDir = request.get('unit_dir'), stateDir = effective.get('state_dir'), secretsFile = effective.get('secrets_file');
    need([root, unitDir, stateDir, secretsFile].every(value => typeof value === 'string' && value.startsWith(home + '/')), 'root, unit_dir, state_dir and secrets_file must lie in this account\'s home');
    const builds = path.join(root, 'builds'), build = path.join(builds, wanted), liveBuild = path.join(builds, live), current = path.join(root, 'current');
    // The deployment to take over, before the first write of this runner: an engine's root whose pointer names a build we know.
    need(fs.existsSync(path.join(root, 'state', 'ownership.json')), 'the deployment root is not one an engine made: ' + root);
    const pointer = () => { try { return fs.readlinkSync(current); } catch { return null; } };
    need(pointer() === liveBuild || (pointer() === build && (mode === 'verify' || mode === 'start')),
      mode === 'check' || mode === 'prepare' ? 'current does not name the build this takeover was prepared against' : 'current names neither the build this takeover was prepared against nor the new one');
    const recipe = input.get('recipe');
    need(effective.has('recipe_file') ? recipe instanceof Map && recipe.size === 1 && typeof recipe.get('source') === 'string' && path.isAbsolute(recipe.get('source')) : recipe === null,
      'recipe must be {"source": <absolute path of the recipe\'s bytes>} for a deployment with a recipe file, and null for any other');

    // The log. From here on every step is written down before the next one starts.
    const keep = path.join(home, 'kitchen-takeover', name);
    fs.mkdirSync(keep, { recursive: true, mode: 0o700 });
    need(fs.lstatSync(keep).isDirectory() && fs.lstatSync(keep).uid === uid && (fs.lstatSync(keep).mode & 0o077) === 0, 'the takeover directory must be this account\'s, mode 0700');
    const logFile = path.join(keep, 'takeover.log'), guardLog = path.join(keep, 'systemctl-calls.log');
    let sequence = 0;
    log = (step, detail = {}) => {
      const line = JSON.stringify({ at: new Date().toISOString(), n: ++sequence, run: mode, step, ...detail });
      fs.appendFileSync(logFile, line + '\n', { mode: 0o600 });
      echo(line);
    };
    const saveReceipt = (command, content) => {
      const last = Math.max(0, ...fs.readdirSync(keep).map(leaf => Number(RECEIPT.exec(leaf)?.[1] ?? 0)));
      const file = path.join(keep, String(last + 1).padStart(3, '0') + '-' + mode + '-' + command + '.receipt.json');
      fs.writeFileSync(file, content, { mode: 0o600, flag: 'wx' });
      return file;
    };
    log('start', {
      input: inputFile, input_sha256: sha256(text), runner: fileURLToPath(import.meta.url), adapter: [process.execPath, ADAPTER], node: process.version,
      uid, account: os.userInfo().username, home, keep, name, unit, cwd: process.cwd(), new_build: wanted, live_build: live,
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
      need(found === digest, 'a pinned input is not the bytes the takeover was prepared with: ' + file);
    }
    for (const [key, tool] of cook) {
      const row = describe(tool);
      log('engine path', { key, ...row });
      need(!row.absent, 'an engine path is missing: ' + key);
    }
    // The recipe: its bytes where they already are, held against the pin of the configuration. Never copied by the runner;
    // the engine stages them in the new build (src/deploy.ts `prepare`), as it did in the running one.
    let operatorFiles = new Map();
    if (recipe !== null) {
      const bytes = fs.readFileSync(recipe.get('source')), pin = effective.get('recipe_file').get('sha256');
      log('recipe', { source: recipe.get('source'), bytes: bytes.length, sha256: sha256(bytes), as_pinned: sha256(bytes) === pin });
      need(sha256(bytes) === pin, 'the recipe source is not the bytes the configuration pins');
      operatorFiles = new Map([['cook-recipe.json', bytes.toString('base64')]]);
    }

    // The service manager behind the guard. `start` alone may reload it and restart this one unit.
    const guard = [SYSTEMCTL_GUARD, mode === 'start' ? 'start' : 'readonly', guardLog, unit, real[0], '--user'];
    const viaGuard = (level, args) => spawnSync(guard[0], [level, guardLog, unit, real[0], ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
    const ask = args => {
      const done = viaGuard('readonly', args);
      return { argv: args, status: done.status, stdout: (done.stdout ?? '').trim() };
    };
    for (const [level, args] of [['readonly', ['--user', 'restart', unit]], ['readonly', ['--user', 'daemon-reload']], ['start', ['--user', 'stop', unit]], ['start', ['--user', 'restart', 'another-' + unit]]]) {
      const status = viaGuard(level, args).status;
      log('guard self-test', { level, asked: args.slice(1).join(' '), status, refused: status === 97 });
      need(status === 97, 'the service-manager guard did not refuse: ' + level + ' ' + args.join(' '));
    }
    const dropin = path.join(unitDir, unit + '.d');
    const shown = () => Object.fromEntries(SHOWN.map(property => [property, ask(['--user', 'show', '-p', property, '--value', unit]).stdout]));
    const target = () => ({
      current: describe(current), unit: describe(path.join(unitDir, unit)), dropin: list(dropin), wants: describe(path.join(unitDir, 'default.target.wants', unit)),
      state_dir: describe(stateDir), secrets_file: describe(secretsFile, false), bookkeeping: list(path.join(root, 'state')), secret_files: list(path.join(root, 'state', 'secret-files'), false),
      builds: list(builds), new_build_prepared: fs.existsSync(path.join(build, '.prepared')),
    });
    const before = { target: target(), unit_state: shown(), exclusive: exclusive.map(([scope, other]) => ask(['--' + scope, 'is-active', other])) };
    log('before', before);
    need(before.unit_state.ActiveState !== '', 'the user service manager did not answer; is XDG_RUNTIME_DIR set for this account?');
    const running = { current: before.target.current, unit: before.target.unit, dropin: before.target.dropin, wants: before.target.wants,
      main_pid: before.unit_state.MainPID, restarts: before.unit_state.NRestarts, started: before.unit_state.ExecMainStartTimestamp, active: before.unit_state.ActiveState };

    const names = deploy.secret_names(effective);
    if (mode === 'verify' || mode === 'start') {
      const view = secretsView(effective, names, secretsFile, root, pointer());
      log('secrets file', view);
      need(view.readable && !view.missing.length && !view.not_text.length && !view.multiline_or_nul.length, 'the secrets file does not hold every declared name as one line of text; nothing was changed');
    }

    // What the engine may start. Each call is logged.
    const run = (argv, options = {}) => {
      const guarded = argv.length > guard.length && guard.every((word, index) => argv[index] === word) ? argv.slice(guard.length) : null;
      const kind = mode === 'prepare' && options.adapter && argv[0] === process.execPath && argv[1] === ADAPTER ? 'adapter'
        : mode !== 'start' || guarded === null ? null
          : guarded.length === 1 && guarded[0] === 'daemon-reload' ? 'daemon-reload'
            : guarded.length === 2 && guarded[0] === 'restart' && guarded[1] === unit ? 'restart'
              : guarded.length === 3 && guarded[0] === 'is-active' && guarded[1] === '--quiet' && guarded[2] === unit ? 'is-active' : null;
      if (kind === null) { log('tool refused', { argv }); throw new Error('the takeover does not start this tool in ' + mode); }
      const started = Date.now();
      log('tool started', { kind, argv, cwd: options.cwd ?? null });
      try { start(argv, options); } catch (error) {
        log('tool failed', { kind, seconds: (Date.now() - started) / 1000, error: typeof error?.public === 'function' ? error.public() : String(error) });
        throw error;
      }
      log('tool done', { kind, seconds: (Date.now() - started) / 1000 });
    };
    const unexpected = error => log('engine failure that is not a refusal', { error: String(error?.stack ?? error) });
    // The engine's own fixed words for a refusal. A receipt holds the closed code only; this log is the account's, mode 0600.
    const refused = error => log('engine refused', { code: error.code ?? null, reason: String(error?.message ?? '') });
    const call = (command, extra = {}) => {
      const body = read().get('request');
      body.set('systemctl', guard);
      for (const [key, value] of Object.entries(extra)) body.set(key, value);
      log(command + ' asked', { keys: [...body.keys()].sort(), phase: extra.phase ?? null });
      const [receipt, status] = dispatch(dict({ contract: 'connectome.kitchen/v1', command, request: body }), null, { host, run, unexpected, refused });
      const file = saveReceipt(command, canonical(receipt) + '\n');
      log(command + ' answered', { status, receipt: file, core: plain(receipt.get('core')) });
      return [receipt.get('core'), status];
    };

    // `plan` is handed the recipe as `apply` is: the configured path may be one this account cannot read.
    const [planned, planStatus] = call('plan', { operator_files: operatorFiles });
    if (planStatus !== 0) { log('end', { status: 1, why: 'plan was refused' }); return 1; }
    const named = planned.get('prepare').get('build_dir');
    log('build named', { build_dir: named, request_id: path.basename(named), expected: wanted });
    need(named === build, 'the engine names the build otherwise than the takeover was prepared for: an input or an engine path of the request is not what it was taken to be');

    let status = 0;
    if (mode === 'prepare') {
      const [, applyStatus] = call('apply', { operator_files: operatorFiles, phase: 'prepare' });
      status = applyStatus === 0 ? 0 : 1;
    }
    if (mode === 'start') {
      need(planned.get('prepare').get('changed') === false, 'the build is not prepared: run prepare first. Nothing was changed');
      const [, applyStatus] = call('apply', { operator_files: operatorFiles, phase: 'both' });
      status = applyStatus === 0 ? 0 : 1;
    }
    if (mode !== 'check') {
      const after = { target: target(), unit_state: shown(), exclusive: exclusive.map(([scope, other]) => ask(['--' + scope, 'is-active', other])) };
      log('after', after);
      log('new build', { entries: list(build, false) });
      const now = { current: after.target.current, unit: after.target.unit, dropin: after.target.dropin, wants: after.target.wants,
        main_pid: after.unit_state.MainPID, restarts: after.unit_state.NRestarts, started: after.unit_state.ExecMainStartTimestamp, active: after.unit_state.ActiveState };
      const untouched = JSON.stringify(now) === JSON.stringify(running);
      log('running deployment', { untouched_by_this_run: untouched, before: running, after: now });
      if (mode !== 'start' && !untouched) {
        // Not this runner's doing by any path it knows: the pointer, the unit or the process changed while it ran.
        log('end', { status: 3, why: 'the running deployment changed during a step that does not touch it' });
        echo('takeover: the running deployment changed during a step that does not touch it');
        return 3;
      }
      log('service manager calls', { log: guardLog, verbs: fs.existsSync(guardLog) ? fs.readFileSync(guardLog, 'utf8').trim().split('\n').map(line => line.split(' ').slice(1, 6).join(' ')) : [] });
    }
    log('end', { status });
    return status;
  } catch (error) {
    const status = error instanceof ContractError ? 2 : 3;
    const why = error instanceof ContractError ? 'the request is not a contract v1 request' : error instanceof Refusal ? error.message : String(error?.stack ?? error);
    try { log('end', { status, why }); } catch { /* the log itself failed */ }
    echo('takeover: ' + why);
    return status;
  }
}

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  // What the engine and the adapter write without naming a mode must not depend on the caller's umask.
  process.umask(0o022);
  const [mode, flag, inputFile, ...rest] = process.argv.slice(2);
  process.exitCode = flag === '--input' && !rest.length ? takeover(mode, inputFile) : takeover(null, null);
}
