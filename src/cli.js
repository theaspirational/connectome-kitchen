#!/usr/bin/env node
import { CONTRACT, VERSION } from './contract.js';
// Operational CLI is deliberately deferred to phase 7. Never consume inputs here.
process.stdout.write(JSON.stringify({contract: CONTRACT, command: null,
  core: {error_code: 'E_CONTRACT', msg: 'incubation scaffold; engine unavailable'},
  meta: {kitchen: VERSION, artifact_sha256: null}}) + '\n');
process.exitCode = 2;
