#!/usr/bin/env node
// The TS engine on one case of the deploy corpus. Test only; driven by corpus.py.
//
//   ts_driver.mjs <job.json> <outcome.json>
//
// A job is what corpus.py asks the oracle in-process, written down: a contract v1 request (`op: dispatch`) or the role's
// own check-mode call (`op: role`), with the sandbox it runs in. The answer is the same record the oracle side makes.
// The two seams are the ones corpus.py puts around the oracle, no more:
//   - the host model (`host_model` there): who owns a path under the sandbox, and where the account's home is;
//   - the adapter (`adapter_through_seam` there): what is started in its place is adapter_seam.mjs.
// The name of the build is no seam. A job with `identity` hands the engine the two words the oracle's shim hashes, so that
// run has the oracle's build name and its tree is held byte for byte; a job without it is the engine as a kitchen runs
// it, which names the build by its adapter contract (ruling D5). The driver finds `dist` beside itself, so a copy of
// both elsewhere is an install elsewhere.
import fs from 'node:fs';
import { createHash } from 'node:crypto';
import * as deploy from '../../dist/deploy.js';
import { ADAPTER_CONTRACT } from '../../dist/cook.js';
import { adapter_command, dispatch } from '../../dist/engine.js';
import * as inputs from '../../dist/inputs.js';
import { realHost } from '../../dist/io/host.js';
import * as pathio from '../../dist/pylike/pathio.js';
import * as pathlib from '../../dist/pylike/pathlib.js';
import { PyValueError } from '../../dist/pylike/py.js';
import { canonical, dict } from '../../dist/pylike/pydump.js';
import { loads } from '../../dist/pylike/pyjson.js';

const [jobFile, outFile] = process.argv.slice(2);
if (!jobFile || !outFile) { process.stderr.write('usage: ts_driver.mjs <job.json> <outcome.json>\n'); process.exit(2); }
const job = loads(fs.readFileSync(jobFile));
const sandbox = job.get('sandbox'), account = job.get('account');

/** `host_model` of corpus.py: every lstat reaches a real file; what is changed is who owns it. */
function hostModel() {
  const above = new Set(pathio.parents(sandbox));
  const asRoot = job.get('root_owned').map(name => sandbox + '/' + name);
  return {
    ...realHost,
    lstat(path) {
      const info = realHost.lstat(path);
      if (above.has(path)) return { mode: fs.constants.S_IFDIR | 0o755, uid: 0, gid: 0 };
      if (path === sandbox || path === sandbox + '/home' || asRoot.some(top => path === top || pathlib.isRelativeTo(path, top))) return { mode: info.mode, uid: 0, gid: 0 };
      return info;
    },
    home: uid => (uid === process.geteuid() ? sandbox + '/home/' + account : realHost.home(uid)),
  };
}
const host = hostModel();
const seam = job.get('seam');
const started = [];
const throughSeam = (argv, options = {}) => {
  if (options.adapter) started.push(argv.slice(0, 2));
  return deploy.run(options.adapter ? [process.execPath, seam, ...argv.slice(2)] : argv, options);
};
const unexpected = error => { if (process.env.KITCHEN_CORPUS_TRACE) process.stderr.write(String(error?.stack ?? error) + '\n'); };

let outcome;
if (job.get('op') === 'dispatch') {
  const data = dict({ contract: 'connectome.kitchen/v1', command: job.get('command'), request: job.get('request') });
  const [receipt, status] = dispatch(data, job.get('secrets'), { host, ...(job.has('identity') ? { identity: job.get('identity') } : {}), run: throughSeam, unexpected });
  outcome = dict({ status: loads(String(status)), core: receipt.get('core') });
  // The first two words of each adapter command the engine asked for, before the seam took the adapter's place.
  if (job.get('tell_adapter') === true) outcome.set('adapter_started', started);
} else if (job.get('op') === 'where') {
  // What this engine names its adapter in a build's identity, and the two words it starts it with: this Node, this install.
  outcome = dict({ contract: ADAPTER_CONTRACT, adapter: adapter_command() });
} else if (job.get('op') === 'role') {
  // The role's own call: its cook command, check mode. Nothing is run and nothing is written.
  const role = job.get('role');
  try {
    const files = inputs.collect(role.get('effective'));
    const deployment = new deploy.Deployment({
      root: role.get('root'), unit_dir: role.get('unit_dir'), config: role.get('effective'), versions: role.get('versions'),
      cook_command: role.get('cook_command'), systemctl: ['systemctl', '--user'], check: true, base: role.get('base'), operator_files: files, host,
      run: () => { throw new Error('check mode ran a tool'); },
    });
    outcome = dict({
      request_id: deployment.request_id, build_dir: deployment.build, pins: deployment.pins,
      spec_sha256: createHash('sha256').update(deploy.canonical(deployment.spec)).digest('hex'), cook_command: role.get('cook_command'),
      prepare: deployment.prepare(), activate: deployment.activate(),
    });
  } catch (error) {
    if (!(error instanceof PyValueError)) throw error;
    outcome = dict({ refused: typeof error.public === 'function' ? new Map(Object.entries(error.public())) : dict({ exception: error.pyName, message: error.message }) });
  }
} else if (job.get('op') === 'install_only') {
  // `World.install_only` of corpus.py: the engine's own class, no verifier, the steps of the module boundary, twice.
  const role = job.get('role'), applies = [];
  outcome = dict({ applies });
  try {
    for (let round = 0; round < 2; round++) {
      const deployment = new deploy.Deployment({
        root: role.get('root'), unit_dir: role.get('unit_dir'), config: role.get('effective'), versions: role.get('versions'),
        cook_command: role.get('cook_command'), cook_identity: role.get('cook_identity'), systemctl: role.get('systemctl'), base: role.get('base'), operator_files: job.get('files'),
        analyze_command: [], host, run: throughSeam,
      });
      deployment.validate_paths();
      deployment.preflight_activation();
      deploy.make_directory(deployment.root);
      const step = dict({ prepare: deployment.prepare() });
      applies.push(step);
      step.set('activate', deployment.activate(job.get('secrets')));
    }
  } catch (error) {
    if (!(error instanceof PyValueError) || typeof error.public !== 'function') throw error;
    outcome.set('refused', new Map(Object.entries(error.public())));
  }
} else if (job.get('op') === 'identity') {
  // `World.identity` of corpus.py: the request of a build and its name, from a configuration as merged. No file is touched.
  // This is the harness's formula, not the engine's answer: no `Deployment` is constructed. The engine's parts
  // (`resolve_pins`, `canonical`) are put together here in the order of src/deploy.ts (the constructor, `build_config` and
  // `request_id`), as `request_of` of corpus.py does for the oracle. That the engine itself names a build so is what the
  // `role` op shows, with `Deployment.request_id`.
  const effective = job.get('effective');
  const config = new Map([...effective].filter(([key]) => key !== 'activation' && key !== 'exclusive_with'));
  const spec = dict({ config, pins: deploy.resolve_pins(effective, job.get('versions')) });
  const digest = text => createHash('sha256').update(text).digest('hex');
  outcome = dict({ request_id: digest(deploy.canonical(dict({ spec, cook: job.get('cook_command') }))), deployment_json_sha256: digest(deploy.canonical(spec)) });
} else throw new Error('unknown op');
fs.writeFileSync(outFile, canonical(outcome) + '\n');
