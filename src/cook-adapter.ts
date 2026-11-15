#!/usr/bin/env node
/** Run the bundled cook adapter. Port of `oracle/scripts/cook-adapter.py`: arguments in, one diagnostic line on stderr, an exit status. */
import { main } from './cook.js';

const [status, stderr] = main(process.argv.slice(2));
if (stderr) process.stderr.write(stderr);
process.exitCode = status;
