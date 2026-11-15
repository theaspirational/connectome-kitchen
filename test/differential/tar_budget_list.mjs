#!/usr/bin/env node
// usage: tar_budget_list.mjs <archive.tgz> [budgets json]   (driven by port_budget.py)
// One listing by the TS reader itself, below archive_manifest, so that a refusal shows which budget made it.
// Prints one JSON line: {members} or {error, budget?, message}, and the reader's constants.
import fs from 'node:fs';
import v8 from 'node:v8';
import * as tarfile from '../../dist/pylike/tarfile.js';
const [file, budgets] = process.argv.slice(2);
const out = { defaults: tarfile.BUDGETS, heap_limit_required_mib: tarfile.HEAP_LIMIT_REQUIRED_MIB, heap_size_limit_mib: Math.floor(v8.getHeapStatistics().heap_size_limit / 1048576) };
if (file) {
  try {
    out.members = tarfile.gzipMembers(fs.readFileSync(file), budgets ? { budgets: JSON.parse(budgets) } : {}).length;
  } catch (error) {
    if (!(error instanceof Error) || !('pyName' in error)) throw error;
    out.error = error.pyName;
    out.message = error.message;
    if (error instanceof tarfile.MetadataBudget) out.budget = error.budget;
  }
}
console.log(JSON.stringify(out));
