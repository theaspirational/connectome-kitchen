#!/usr/bin/env node
// TS side of test/differential/port_primitives.py. One JSON job per line in, one JSON answer per line out.
// usage: port_primitives.mjs <jobs.jsonl> <answers.jsonl>
// KITCHEN_DIST names another compiled tree (default: the package's dist).
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';

// The sweeps raise millions of refusals; their stack traces are never read.
Error.stackTraceLimit = 0;
const here = path.dirname(fileURLToPath(import.meta.url));
const dist = process.env.KITCHEN_DIST ? path.resolve(process.env.KITCHEN_DIST) : path.resolve(here, '../../dist');
const load = name => import(pathToFileURL(path.join(dist, name)).href);
const [url, codec, datetime, pyjson, json, pathlib, py, text, lock, errors] = await Promise.all(
  ['pylike/url.js', 'pylike/codec.js', 'pylike/datetime.js', 'pylike/pyjson.js', 'pylike/json.js', 'pylike/pathlib.js', 'pylike/py.js', 'pylike/text.js', 'lock.js', 'errors.js'].map(load));

/** Tagged transport value -> engine value (see `tag` in port_primitives.py). */
function untag(value) {
  if (value === null || typeof value !== 'object') return value;
  if ('i' in value) return new py.Int(value.i);
  if ('f' in value) {
    const number = value.f === 'inf' ? Infinity : value.f === '-inf' ? -Infinity : value.f === 'nan' ? NaN : Number(value.f);
    return Number.isFinite(number) ? new py.Float(number) : new py.NonFinite(number);
  }
  if ('l' in value) return value.l.map(untag);
  return new Map(value.d.map(([key, item]) => [key, untag(item)]));
}
function tag(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') return value;
  if (value instanceof py.Int) return { i: value.value.toString() };
  if (value instanceof py.Float) return { f: json.dumps(value) };
  if (value instanceof py.NonFinite) return { f: Number.isNaN(value.value) ? 'nan' : value.value > 0 ? 'inf' : '-inf' };
  if (Array.isArray(value)) return { l: value.map(tag) };
  if (value instanceof Map) return { d: [...value].map(([key, item]) => [key, tag(item)]) };
  throw new Error('value outside the JSON model');
}
const failure = (error, message = false) => {
  if (!(error instanceof py.PyError)) throw error;
  return { error: message ? [error.pyName, error.message] : error.pyName };
};
const bytes = job => Buffer.from(job.b, 'base64');
const pairs = record => Object.entries(record);

class HookDuplicate extends py.PyValueError { get pyName() { return 'HookDuplicate'; } }
class HookConstant extends py.PyValueError { get pyName() { return 'HookConstant'; } }
const hookRequire = (condition, message) => { if (!condition) throw new HookDuplicate(message); };
const hookPairs = items => { const result = new Map(); for (const [key, value] of items) { hookRequire(!result.has(key), 'duplicate'); result.set(key, value); } return result; };
const hookConstant = () => { throw new HookConstant('constant'); };
const OPTIONS = { plain: {}, hooks: { object_pairs_hook: hookPairs, parse_constant: hookConstant }, 'pairs-dict': { object_pairs_hook: items => new Map(items) } };

const SWEEPS = {
  repository: lock.repository, credential_shaped: lock.credential_shaped, text: lock.text, external_name: lock.external_name, version: lock.version, isspace: text.isspace,
  names_only_key: value => { try { lock.names_only(new Map([[value, new py.Int(1n)]])); return null; } catch (error) { if (error instanceof lock.LockError) return error.message; throw error; } },
  lowered_host: value => { const host = url.hostname(value); return host === value ? 'same' : host; },
  netloc: value => { let parts; try { parts = url.split(value); } catch (error) { if (error instanceof py.PyValueError) return 'ValueError'; throw error; } return parts.netloc === value.slice(2) ? 'kept' : 'cut'; },
  repr: value => {
    const out = pathlib.repr(value), code = value.codePointAt(0);
    if (out === "'\\x" + code.toString(16).padStart(2, '0') + "'") return 'x';
    if (out === "'\\u" + code.toString(16).padStart(4, '0') + "'") return 'u';
    if (out === "'\\U" + code.toString(16).padStart(8, '0') + "'") return 'U';
    return out === "'" + value + "'" ? 'raw' : out;
  },
};

function streamBytes(job) {
  const plant = Buffer.from(job.plant, 'latin1');
  const data = Buffer.alloc(Math.max(job.size, job.at + plant.length), Buffer.from(job.fill, 'latin1'));
  plant.copy(data, job.at);
  return (job.clip ?? true) ? data.subarray(0, job.size) : data;
}

const KINDS = {
  unquote: job => url.unquote(job.s),
  url: job => {
    let parts;
    try { parts = url.split(job.s); } catch (error) { failure(error); return { split: 'ValueError' }; }
    const out = { split: [parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment], hostname: url.hostname(parts.netloc), username: url.username(parts.netloc), password: url.password(parts.netloc) };
    try { out.port = url.port(parts.netloc); } catch (error) { failure(error); out.port = 'ValueError'; }
    return out;
  },
  repository: job => { try { return lock.repository(job.s); } catch (error) { return failure(error); } },
  b64: job => { try { return { b: codec.b64decodeStrict(job.s).toString('base64') }; } catch (error) { return failure(error); } },
  iso: job => { try { datetime.fromisoformat(job.s); return 'ok'; } catch (error) { failure(error); return 'ValueError'; } },
  json: job => { try { return { v: tag(pyjson.loads('s' in job ? job.s : bytes(job), OPTIONS[job.mode])) }; } catch (error) { return failure(error); } },
  depth: job => {
    const source = job.open.repeat(job.depth) + job.leaf + job.close.repeat(job.depth);
    try { pyjson.loads(job.bytes ? Buffer.from(source) : source, OPTIONS[job.mode]); return 'ok'; } catch (error) { return failure(error).error; }
  },
  utf8: job => { try { return { s: codec.decodeUtf8(bytes(job), job.mode) }; } catch (error) { return failure(error); } },
  fsencode: job => { try { return { b: codec.fsencode(job.s).toString('base64') }; } catch (error) { return failure(error, true); } },
  pathpure: job => {
    const { a, b, c } = job;
    const out = { pathStr: pathlib.pathStr(a), pathParts: pathlib.pathParts(a), joinPath: pathlib.joinPath(pathlib.pathStr(a), b), name: pathlib.name(a), suffix: pathlib.suffix(a), parent: pathlib.parent(a),
      isRelativeTo: pathlib.isRelativeTo(a, b), isAbsolute: pathlib.isAbsolute(a), posixJoin: pathlib.posixJoin(a, b, c), repr: pathlib.repr(a), normpath: pathlib.normpath(a) };
    try { out.relativeTo = pathlib.relativeTo(a, b); } catch (error) { out.relativeTo = failure(error, true); }
    return out;
  },
  pathfs: job => {
    const attempt = call => { try { return call(); } catch (error) { return failure(error, error instanceof py.PyValueError); } };
    process.chdir(job.cwd);
    const normal = pathlib.pathStr(job.p);
    return { resolve: attempt(() => pathlib.resolve(job.p)), realpath: attempt(() => pathlib.realpath(job.p)), isFile: attempt(() => pathlib.isFile(normal)), isSymlink: attempt(() => pathlib.isSymlink(normal)),
      lstat: attempt(() => pathlib.lstat(normal).isFile()), readBytes: attempt(() => createHash('sha256').update(pathlib.readBytes(normal)).digest('hex')), abspath: attempt(() => pathlib.abspath(job.p)) };
  },
  readtext: job => { try { return { s: pathlib.readText(job.p) }; } catch (error) { return failure(error); } },
  cook_code: job => errors.cook_code(bytes(job)),
  cook_stream: job => errors.cook_code_from_stream(new py.BytesIO(streamBytes(job))),
  diagnostic: job => pairs(errors.diagnostic(untag(job.code), untag(job.upstream))),
  adapter: job => pairs(errors.adapter_diagnostic('s' in job ? job.s : bytes(job))),
  eq: job => py.eq(untag(job.a), untag(job.b)),
  pyops: job => {
    const value = untag(job.v), names = new Set(['a', '1', 'git', 'E_HOST_UNIT', '']);
    const attempt = call => { try { return call(); } catch (error) { return failure(error); } };
    return { truthy: py.truthy(value), typeName: py.typeName(value), strIn: attempt(() => py.strIn(job.key, value)), inStrSet: attempt(() => py.inStrSet(value, names)),
      subscript: attempt(() => tag(py.subscript(value, job.key))), get: py.isDict(value) ? tag(py.get(value, job.key)) : null, isInt: py.isInt(value) };
  },
  sweep: job => {
    const call = SWEEPS[job.fn], runs = [];
    let last = null;
    for (const [low, high, step] of job.domain) {
      for (let code = low; code < high; code += step) {
        const outcome = call(job.prefix + String.fromCodePoint(code) + job.suffix);
        if (outcome !== last || !runs.length) { runs.push([code, outcome]); last = outcome; }
      }
    }
    return runs;
  },
};

const [jobs, answers] = process.argv.slice(2);
if (!jobs || !answers) { process.stderr.write('usage: port_primitives.mjs <jobs.jsonl> <answers.jsonl>\n'); process.exit(2); }
const start = process.cwd();
const out = fs.openSync(answers, 'w');
for (const line of fs.readFileSync(jobs, 'utf8').split('\n')) {
  if (!line) continue;
  const job = JSON.parse(line);
  let answer;
  try { answer = KINDS[job.k](job); } catch (error) { answer = { error: 'JS:' + (error?.constructor?.name ?? 'unknown') + ':' + String(error?.message).slice(0, 160) }; }
  process.chdir(start);
  fs.writeSync(out, JSON.stringify(answer ?? null) + '\n');
}
fs.closeSync(out);
