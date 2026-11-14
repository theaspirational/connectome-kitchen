#!/usr/bin/env node
// usage: ts_runner.mjs <jobs.jsonl> <outcomes.jsonl>   (driven by ts_port.py; see ts_invoke.mjs)
import fs from 'node:fs';
import { parse, run } from './ts_invoke.mjs';
const [jobs, out] = process.argv.slice(2);
if (!jobs || !out) { process.stderr.write('usage: ts_runner.mjs <jobs.jsonl> <outcomes.jsonl>\n'); process.exit(2); }
const lines = fs.readFileSync(jobs, 'utf8').split('\n').filter(Boolean);
const fd = fs.openSync(out, 'w');
for (const line of lines) fs.writeSync(fd, run(parse(line)) + '\n');
fs.closeSync(fd);
