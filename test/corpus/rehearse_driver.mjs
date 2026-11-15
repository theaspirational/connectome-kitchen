#!/usr/bin/env node
// scripts/rehearse.mjs on a sandbox of the deploy corpus. Test only; driven by test/unit/test_rehearse.py.
//
//   rehearse_driver.mjs <mode> <rehearsal.json> <sandbox> <account>
//
// The runner is the one a rehearsal uses, behind the two seams of ts_driver.mjs: the host model (who owns a path under
// the sandbox, and where the account's home is), and adapter_seam.mjs started in the adapter's place, since no sandbox
// holds a cook checkout with the pinned commit id. On a real account the runner is started by itself and has neither.
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import * as deploy from '../../dist/deploy.js';
import { realHost } from '../../dist/io/host.js';
import * as pathio from '../../dist/pylike/pathio.js';
import * as pathlib from '../../dist/pylike/pathlib.js';
import { rehearse } from '../../scripts/rehearse.mjs';

const [mode, input, sandbox, account] = process.argv.slice(2);
const above = new Set(pathio.parents(sandbox));
const asRoot = ['opt', 'srv', 'usr'].map(name => sandbox + '/' + name);
const host = {
  ...realHost,
  lstat(path) {
    const info = realHost.lstat(path);
    if (above.has(path)) return { mode: fs.constants.S_IFDIR | 0o755, uid: 0, gid: 0 };
    if (path === sandbox || path === sandbox + '/home' || asRoot.some(top => path === top || pathlib.isRelativeTo(path, top))) return { mode: info.mode, uid: 0, gid: 0 };
    return info;
  },
  home: uid => (uid === process.geteuid() ? sandbox + '/home/' + account : realHost.home(uid)),
};
const seam = fileURLToPath(new URL('./adapter_seam.mjs', import.meta.url));
const start = (argv, options = {}) => deploy.run(options.adapter ? [argv[0], seam, ...argv.slice(2)] : argv, options);
process.exitCode = rehearse(mode, input, { host, start, echo: () => {} });
