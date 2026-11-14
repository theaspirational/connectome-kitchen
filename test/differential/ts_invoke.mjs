// TS side of the module differential (the ported engine modules). No expected values
// live here: a job is `{id, function, args, kwargs}` in the recorder's
// tagged encoding (test/differential/record.py `encode`), the answer is the
// outcome in the same encoding. `ts_port.py` and the unit suite compare it with
// the oracle's outcome byte for byte.
import fs from 'node:fs';
import path from 'node:path';
import * as json from '../../dist/pylike/json.js';
import * as py from '../../dist/pylike/py.js';
import { loads } from '../../dist/pylike/pyjson.js';
import * as errors from '../../dist/errors.js';
import * as lock from '../../dist/lock.js';
import * as inputs from '../../dist/inputs.js';
import * as archive from '../../dist/archive.js';
import * as config from '../../dist/config.js';

/** A value the TS signature cannot take. The driver checks what the oracle did with it. */
export class Unsupported extends Error {}
/** A Python object that is neither text nor a container nor a number (a Path or bytes where data is expected). */
class Opaque { constructor(tag, value) { this.tag = tag; this.value = value; } }
class PathArg { constructor(value) { this.value = value; } }

const single = (value, tag) => value instanceof Map && value.size >= 1 && value.has(tag);

/** Tagged job value -> TS value. `$mapping` and plain objects are dicts; non-string keys cannot be expressed. */
export function decode(value) {
  if (Array.isArray(value)) return value.map(decode);
  if (!(value instanceof Map)) return value;
  if (single(value, '$path')) return new PathArg(value.get('$path'));
  if (single(value, '$bytes')) return Buffer.from(value.get('$bytes'), 'base64');
  if (single(value, '$float')) {
    const text = value.get('$float');
    const number = text === 'inf' ? Infinity : text === '-inf' ? -Infinity : text === 'nan' ? NaN : Number(text);
    return Number.isFinite(number) ? new py.Float(number) : new py.NonFinite(number);
  }
  if (single(value, '$mapping')) {
    const result = new Map();
    for (const [key, item] of value.get('$mapping')) {
      if (typeof key !== 'string') throw new Unsupported('non-string mapping key');
      result.set(key, decode(item));
    }
    return result;
  }
  if (single(value, '$stream')) {
    const content = decode(value.get('$stream'));
    if (!(content instanceof Uint8Array)) throw new Unsupported('text stream');
    const stream = new py.BytesIO(content); stream.seek(Number(value.get('position').value)); return stream;
  }
  for (const key of value.keys()) if (key.startsWith('$')) throw new Unsupported('tag ' + key);
  return new Map([...value].map(([key, item]) => [key, decode(item)]));
}

const floatRepr = value => value instanceof py.NonFinite ? (Number.isNaN(value.value) ? 'nan' : value.value > 0 ? 'inf' : '-inf') : json.dumps(value);

/** TS value -> tagged, `json.dumps`-able value; mirrors record.py `encode`. */
export function encode(value) {
  if (value === undefined || value === null) return null;
  if (typeof value === 'boolean' || typeof value === 'string' || value instanceof py.Int) return value;
  if (value instanceof py.Float || value instanceof py.NonFinite) return { $float: floatRepr(value) };
  if (value instanceof PathArg) return { $path: value.value };
  if (value instanceof Opaque) return { ['$' + value.tag]: value.value };
  if (value instanceof Uint8Array) return { $bytes: Buffer.from(value.buffer, value.byteOffset, value.byteLength).toString('base64') };
  if (Array.isArray(value)) return value.map(encode);
  if (value instanceof Map) return { $mapping: [...value].map(([key, item]) => [key, encode(item)]) };
  if (typeof value === 'object') return { $mapping: Object.entries(value).map(([key, item]) => [key, encode(item)]) };
  throw new Error('value cannot be encoded');
}

// Argument shapes. `data` is anything JSON can hold; the others are what the TS signature declares.
const data = value => value instanceof PathArg ? new Opaque('path', value.value) : value instanceof Uint8Array ? new Opaque('bytes', '') : value;
const deep = value => {
  if (value instanceof PathArg || value instanceof Uint8Array || value instanceof py.BytesIO) throw new Unsupported('non-JSON value inside data');
  if (Array.isArray(value)) value.forEach(deep); else if (value instanceof Map) for (const item of value.values()) deep(item);
  return value;
};
// A path parameter takes a str or a Path; anything else goes through as it is, for the port's own runtime check to answer.
const anyPath = value => value instanceof PathArg ? value.value : value instanceof py.BytesIO ? new Opaque('stream', '') : value;
const pathOnly = value => { if (value instanceof PathArg) return value.value; throw new Unsupported('Path argument'); };
const bytes = value => value instanceof PathArg ? new Opaque('path', value.value) : value;
const string = value => { if (typeof value === 'string') return value; throw new Unsupported('str argument'); };
const stream = value => value instanceof PathArg ? new Opaque('path', value.value) : value;
const size = value => { if (value instanceof py.Int) return Number(value.value); throw new Unsupported('int argument'); };
const supplied = value => {
  if (value instanceof Map) return new Map([...value].map(([key, item]) => [key, item instanceof PathArg ? (() => { throw new Unsupported('Path in transport'); })() : item]));
  return deep(value);
};

function lockOptions(kwargs) {
  const options = {};
  for (const [key, value] of kwargs) {
    if (!['host_pin', 'required_names', 'config', 'framework_pin', 'framework_record'].includes(key)) throw new Unsupported('keyword ' + key);
    options[key] = deep(value);
  }
  return options;
}
const none = kwargs => { if (kwargs.size) throw new Unsupported('keyword arguments'); };
const arity = (args, low, high = low) => { if (args.length < low || args.length > high) throw new Unsupported('arity'); };

/** function name -> (args, kwargs) => result. Positional/keyword binding follows the Python signatures. */
const CALLS = {
  'connectome_errors.cook_code': (a, k) => { arity(a, 1); none(k); return errors.cook_code(bytes(a[0])); },
  'connectome_errors.cook_code_from_stream': (a, k) => { arity(a, 1); none(k); return errors.cook_code_from_stream(stream(a[0])); },
  'connectome_errors.diagnostic': (a, k) => {
    arity(a, 1, 2); const upstream = a.length > 1 ? a[1] : k.has('upstream') ? k.get('upstream') : null;
    for (const key of k.keys()) if (key !== 'upstream' || a.length > 1) throw new Unsupported('keyword ' + key);
    return errors.diagnostic(data(a[0]), data(upstream));
  },
  'connectome_errors.adapter_diagnostic': (a, k) => { arity(a, 1); none(k); return errors.adapter_diagnostic(a[0] instanceof PathArg ? new Opaque('path', '') : a[0]); },
  // Constructed, not recorded: [message, code?, cook_error_code?] -> SafeError(...).public()
  'connectome_errors.SafeError.public': (a, k) => { arity(a, 1, 3); none(k); return new errors.SafeError(string(a[0]), ...a.slice(1).map(data)).public(); },
  'connectome_lock.require': (a, k) => { arity(a, 2); none(k); return lock.require(deep(a[0]), string(a[1])); },
  'connectome_lock.text': (a, k) => { arity(a, 1); none(k); return lock.text(data(a[0])); },
  'connectome_lock.digest': (a, k) => {
    arity(a, 1, 2); for (const key of k.keys()) if (key !== 'size' || a.length > 1) throw new Unsupported('keyword ' + key);
    const width = a.length > 1 ? a[1] : k.get('size');
    return width === undefined ? lock.digest(data(a[0])) : lock.digest(data(a[0]), size(width));
  },
  'connectome_lock.version': (a, k) => { arity(a, 1); none(k); return lock.version(data(a[0])); },
  'connectome_lock.credential_shaped': (a, k) => { arity(a, 1); none(k); if (a[0] instanceof Uint8Array) throw new Unsupported('bytes argument'); return lock.credential_shaped(data(a[0])); },
  'connectome_lock.repository': (a, k) => { arity(a, 1); none(k); return lock.repository(data(a[0])); },
  'connectome_lock.names_only': (a, k) => { arity(a, 1); none(k); return lock.names_only(deep(a[0])); },
  'connectome_lock.external_name': (a, k) => { arity(a, 1); none(k); return lock.external_name(data(a[0])); },
  'connectome_lock.external_command': (a, k) => { arity(a, 1); none(k); return lock.external_command(data(a[0])); },
  'connectome_lock.contained_file': (a, k) => { arity(a, 2); none(k); return new PathArg(lock.contained_file(pathOnly(a[0]), deep(a[1]))); },
  'connectome_lock.map_lock': (a, k) => { arity(a, 2); return lock.map_lock(deep(a[0]), anyPath(a[1]), lockOptions(k)); },
  'connectome_lock.read_lock': (a, k) => { arity(a, 1); return lock.read_lock(anyPath(a[0]), lockOptions(k)); },
  'connectome_inputs.need': (a, k) => { arity(a, 1, 2); none(k); return a.length > 1 ? inputs.need(deep(a[0]), string(a[1])) : inputs.need(deep(a[0])); },
  'connectome_inputs.external_data_path': (a, k) => { arity(a, 1); none(k); return inputs.external_data_path(data(a[0])); },
  'connectome_inputs.external_data_flag': (a, k) => { arity(a, 1); none(k); return inputs.external_data_flag(data(a[0])); },
  'connectome_inputs.validate_external_data_args': (a, k) => { arity(a, 1); none(k); return inputs.validate_external_data_args(deep(a[0])); },
  'connectome_inputs.validate': (a, k) => { arity(a, 1); none(k); return inputs.validate(deep(a[0])); },
  'connectome_inputs.verify_bytes': (a, k) => { arity(a, 2); none(k); return inputs.verify_bytes(bytes(a[0]), deep(a[1])); },
  'connectome_inputs.read_file': (a, k) => { arity(a, 2); none(k); return inputs.read_file(anyPath(a[0]), deep(a[1])); },
  'connectome_inputs.collect': (a, k) => { arity(a, 1); none(k); return inputs.collect(deep(a[0])); },
  'connectome_inputs.payloads': (a, k) => {
    arity(a, 1, 2); for (const key of k.keys()) if (key !== 'supplied' || a.length > 1) throw new Unsupported('keyword ' + key);
    const transport = a.length > 1 ? a[1] : k.has('supplied') ? k.get('supplied') : null;
    return inputs.payloads(deep(a[0]), supplied(transport));
  },
  'connectome_inputs.installed': (a, k) => { arity(a, 2); none(k); return inputs.installed(anyPath(a[0]), deep(a[1])); },
  'connectome_archive.need': (a, k) => { arity(a, 2, 3); none(k); return archive.need(deep(a[0]), string(a[1]), ...a.slice(2).map(deep)); },
  'connectome_archive.npm_name': (a, k) => { arity(a, 1); none(k); return archive.npm_name(data(a[0])); },
  'connectome_archive.registry_origin': (a, k) => { arity(a, 1); none(k); return archive.registry_origin(data(a[0])); },
  'connectome_archive.sha512_integrity': (a, k) => { arity(a, 1); none(k); return archive.sha512_integrity(data(a[0])); },
  'connectome_archive.validate_dependency': (a, k) => { arity(a, 1); none(k); return archive.validate_dependency(deep(a[0])); },
  'connectome_archive.legacy_dependency': (a, k) => { arity(a, 2); none(k); return archive.legacy_dependency(deep(a[0]), deep(a[1])); },
  'connectome_archive.dependency_inputs': (a, k) => { arity(a, 2); none(k); return archive.dependency_inputs(deep(a[0]), deep(a[1])); },
  'connectome_archive.archive_manifest': (a, k) => { arity(a, 1); none(k); return archive.archive_manifest(deep(a[0])); },
  'connectome_archive.dependency_provenance': (a, k) => { arity(a, 2); none(k); return archive.dependency_provenance(deep(a[0]), deep(a[1])); },
  'connectome_archive.provenance_record': (a, k) => { arity(a, 1); none(k); return archive.provenance_record(deep(a[0])); },
  'connectome_archive.records_metadata': (a, k) => { arity(a, 1); none(k); return archive.records_metadata(deep(a[0])); },
  'connectome_archive.framework_provenance': (a, k) => { arity(a, 2); none(k); return archive.framework_provenance(deep(a[0]), deep(a[1])); },
  'connectome_archive.validate_dependency_record': (a, k) => { arity(a, 1); none(k); return archive.validate_dependency_record(deep(a[0])); },
  'connectome_archive.check_dependencies': (a, k) => {
    arity(a, 3, 4); for (const key of k.keys()) if (key !== 'framework_record' || a.length > 3) throw new Unsupported('keyword ' + key);
    return archive.check_dependencies(deep(a[0]), deep(a[1]), deep(a[2]), deep(a.length > 3 ? a[3] : k.has('framework_record') ? k.get('framework_record') : null));
  },
  'connectome_archive.validate_provenance': (a, k) => { arity(a, 3); none(k); return archive.validate_provenance(deep(a[0]), deep(a[1]), deep(a[2])); },
  'connectome_config.combine': (a, k) => {
    for (const key of k.keys()) if (key !== 'recursive') throw new Unsupported('keyword ' + key);
    const recursive = k.has('recursive') ? k.get('recursive') : false;
    if (typeof recursive !== 'boolean') throw new Unsupported('recursive');
    return config.combine(a.map(deep), recursive);
  },
  'connectome_config.merge_layers': (a, k) => { arity(a, 1); none(k); return config.merge_layers(deep(a[0])); },
  'connectome_config.public_catalog': (a, k) => { arity(a, 2); none(k); return config.public_catalog(deep(a[0]), deep(a[1])); },
  'connectome_config.required_names': (a, k) => { arity(a, 1); none(k); return config.required_names(deep(a[0])); },
};
export const FUNCTIONS = Object.keys(CALLS);

/** Runs one parsed job and returns its outcome as a canonical JSON line. */
export function run(job) {
  const name = job.get('function');
  let outcome;
  try {
    const call = CALLS[name];
    if (!call) throw new Unsupported('function');
    const args = decode(job.get('args')), kwargs = decode(job.get('kwargs'));
    outcome = { result: encode(call(args, kwargs)) };
    json.canonical(outcome); // a value outside the model (possible only in a mutant) must count as a crash, not stop the run
  } catch (error) {
    if (error instanceof Unsupported) outcome = { unsupported: error.message };
    else if (error instanceof py.PyValueError) {
      outcome = { exception: error.pyName, message: error.message };
      if (typeof error.public === 'function') outcome.public = encode(error.public());
    } else if (error instanceof py.PyError) outcome = { crash: error.pyName };
    else outcome = { crash: 'JS:' + (error?.constructor?.name ?? 'unknown') + ':' + String(error?.message).slice(0, 120) };
  }
  return json.canonical(outcome);
}

/** Parses one job line. The transport is JSON; integers keep their kind through the engine's own decoder. */
export const parse = line => loads(line);

/**
 * Creates a fixture tree: `{relative path: {dir: mode} | {file: base64, mode} | {$bytes: base64} | {text: str, mode} | {link: target}}`.
 * Parents are created 0755. `${ROOT}` inside a link target or a text is the tree root.
 */
export function materialize(root, tree) {
  remove(root); // also clears what an interrupted run left behind without permissions
  fs.mkdirSync(root, { recursive: true, mode: 0o755 });
  const later = [];
  for (const [relative, spec] of Object.entries(tree)) {
    const target = path.join(root, relative);
    fs.mkdirSync(path.dirname(target), { recursive: true, mode: 0o755 });
    if ('link' in spec) fs.symlinkSync(spec.link.replaceAll('${ROOT}', root), target);
    else if ('dir' in spec) { fs.mkdirSync(target, { recursive: true }); later.push([target, spec.dir]); }
    else {
      const binary = spec.file ?? spec.$bytes;
      fs.writeFileSync(target, binary !== undefined ? Buffer.from(binary, 'base64') : spec.text.replaceAll('${ROOT}', root)); later.push([target, spec.mode ?? 0o644]);
    }
  }
  // Modes last and deepest first, so that an unreadable directory can still be filled.
  for (const [target, mode] of later.sort((a, b) => b[0].length - a[0].length)) fs.chmodSync(target, mode);
}
export function remove(root) {
  const open = dir => { try { fs.chmodSync(dir, 0o700); for (const entry of fs.readdirSync(dir, { withFileTypes: true })) if (entry.isDirectory()) open(path.join(dir, entry.name)); } catch { /* best effort */ } };
  if (fs.existsSync(root)) open(root);
  fs.rmSync(root, { recursive: true, force: true });
}
