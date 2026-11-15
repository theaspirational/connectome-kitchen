#!/usr/bin/env node
// The TS cook adapter with one check taken out. Test only; the counterpart of adapter_seam.py.
//
// `verify_cook` asks git whether the cook checkout is the pinned commit with the reviewed tree. No sandbox can hold a
// checkout with that commit id, so the corpus records that the check was asked for, and with what, and lets the adapter
// go on. Everything else is the adapter's own `main`, argument for argument (src/cook-adapter.ts).
import fs from 'node:fs';
import path from 'node:path';
import { main } from '../../dist/cook.js';
import { dict, dumps } from '../../dist/pylike/pydump.js';

function recorded(entrypoint, pin, env) {
  const build = process.argv[process.argv.indexOf('--out') + 1];
  const line = dumps(dict({ entrypoint, pin, environment_names: Object.keys(env).sort() }), { sort_keys: true });
  fs.appendFileSync(path.join(path.dirname(path.dirname(build)), 'verify-cook-calls.jsonl'), line + '\n');
}

const [status, stderr] = main(process.argv.slice(2), { verify_cook: recorded });
if (stderr) process.stderr.write(stderr);
process.exitCode = status;
