#!/usr/bin/env node
// The TS engine on one case of the deploy corpus. Test only; driven by corpus.py.
//
//   ts_driver.mjs <job.json> <outcome.json>
//
// A job is what corpus.py asks the oracle in-process, written down: a contract v1 request (`op: dispatch`) or the role's
// own check-mode call (`op: role`), with the sandbox it runs in. The answer is the same record the oracle side makes.
// The two seams are the ones corpus.py puts around the oracle, no more:
//   - the host model (`host_model` there): who owns a path under the sandbox, and where the account's home is;
//   - the adapter (`adapter_through_seam` there): the cook command keeps the words it has for the oracle, so both
//     engines name the build alike, and what is started in its place is adapter_seam.mjs.
import fs from 'node:fs';
import { createHash } from 'node:crypto';
import * as deploy from '../../dist/deploy.js';
import { dispatch } from '../../dist/engine.js';
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
const throughSeam = (argv, options = {}) => deploy.run(options.adapter ? [process.execPath, seam, ...argv.slice(2)] : argv, options);
const unexpected = error => { if (process.env.KITCHEN_CORPUS_TRACE) process.stderr.write(String(error?.stack ?? error) + '\n'); };

let outcome;
if (job.get('op') === 'dispatch') {
  const data = dict({ contract: 'connectome.kitchen/v1', command: job.get('command'), request: job.get('request') });
  const [receipt, status] = dispatch(data, job.get('secrets'), { host, adapter: job.get('adapter'), run: throughSeam, unexpected });
  outcome = dict({ status: loads(String(status)), core: receipt.get('core') });
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
} else throw new Error('unknown op');
fs.writeFileSync(outFile, canonical(outcome) + '\n');
