/**
 * Closed, value-free diagnostics crossing subprocess and wrapper boundaries.
 * Port of `oracle/module_utils/connectome_errors.py` (a49b5dc), one to one.
 */
import { PyRecursionError, PyValueError, eq, get, isDict } from './pylike/py.js';
import type { PyValue } from './pylike/py.js';
import { loads } from './pylike/pyjson.js';

// row 28 errors.closed: the closed code table. errors.py:5-26
export const MESSAGES: Readonly<Record<string, string>> = Object.freeze({
  E_UNIT_VERIFY: 'Static user unit verification failed; systemd-analyze requires a usable user manager runtime.',
  E_CATALOG_INVALID: 'Catalog pin, installed closure or launch path validation failed.',
  E_UNIT_ACTIVE: 'Install-only deployment refuses an active or transitioning unit; no instance is stopped.',
  E_UNIT_EXCLUSIVE: 'A mutually exclusive unit is active or activating; stop it explicitly before starting this agent.',
  E_ANCESTOR_WRITABLE: 'Existing ancestor is group/other-writable; inspect ancestor_path and repair explicitly; not changed.',
  E_EXTERNAL_COMMAND: 'External MCP server command must be an existing root-owned regular file, not group/other-writable, under root-owned non-writable directories; not changed.',
  E_EXTERNAL_ACCESS: 'External MCP server command, path argument or an ancestor is not readable and executable/traversable by the agent account; not changed.',
  E_EXTERNAL_DATA_ARG: 'External data argument requires an exact declared position or safe data flag after a root-owned script: normal absolute path, matching agent-owned file 0600 or directory 0700 under the agent home with safe ancestry; not changed.',
  E_SYSTEMD_PATH: 'Systemd paths must be absolute literals without whitespace or special characters.',
  E_ENVIRONMENT_FILE: 'Required unit environment file was not accepted by the user service manager.',
  E_ARCHIVE_GITHEAD: 'Archive gitHead is missing, invalid or differs from the requested commit.',
  E_ARCHIVE_IDENTITY: 'Archive package name or version differs from the declared identity.',
  E_ARCHIVE_HASH: 'Archive sha256 verification failed.',
  E_ADAPTER_INPUT: 'Adapter input or local prerequisite is invalid.',
  E_LOCK_INVALID: 'Cook lock validation failed.',
  E_LOCK_STATE_PATH: 'Cook lock does not preserve the configured external state and layout.',
  E_SECRETS_MODE: 'Secrets require a non-symlink directory 0700 and regular file 0600 owned by the agent account; not repaired.',
  E_BUDGETS_UNSUPPORTED: 'Step/cost budgets are unsupported; remove budgets explicitly.',
  E_COOK_FAILED: 'Cook command failed (output redacted).',
  E_DEPLOY_FAILED: 'Deployment failed; previous build retained/restored where possible (output redacted).',
});

// Literal codes from the reviewed cook tree, not arbitrary E_* strings. Never
// forward the rest of a tool's line, even when it starts with a known code.
// row 28 errors.closed: the closed pass-through set. errors.py:29-44
export const COOK_CODES: ReadonlySet<string> = new Set([
  'E_HOST_LAYOUT', 'E_HOST_UNIT',
  'E_PRIVATE_ANCESTOR', 'E_PRIVATE_FILE', 'E_PRIVATE_LINK_OWNER',
  'E_PRIVATE_MODE', 'E_PRIVATE_OWNER', 'E_PRIVATE_PATH', 'E_PRIVATE_UNSUPPORTED',
  'E_HOST_DEPENDENCY_ARCHIVE', 'E_HOST_DEPENDENCY_DUPLICATE',
  'E_HOST_DEPENDENCY_FILE', 'E_HOST_DEPENDENCY_HASH', 'E_HOST_DEPENDENCY_NAME',
  'E_HOST_DEPENDENCY_OPTION', 'E_HOST_DEPENDENCY_OVERLAP', 'E_HOST_DEPENDENCY_POLICY_FIELDS',
  'E_HOST_DEPENDENCY_RANGE', 'E_HOST_DEPENDENCY_REINSTALL', 'E_HOST_DEPENDENCY_RESOLUTION',
  'E_HOST_DEPENDENCY_SOURCE', 'E_HOST_DEPENDENCY_STORAGE', 'E_HOST_DEPENDENCY_TRUSTED',
  'E_HOST_DEPENDENCY_UNDECLARED', 'E_HOST_DEPENDENCY_VERSION',
  'E_HOST_ENGINE_CHANGED', 'E_HOST_ENGINE_CHECKOUT', 'E_HOST_ENGINE_CLONE',
  'E_HOST_ENGINE_DEPENDENCY', 'E_HOST_ENGINE_EXEC', 'E_HOST_ENGINE_OBJECT_FORMAT',
  'E_HOST_ENGINE_PACKAGE', 'E_HOST_ENGINE_PATH', 'E_HOST_ENGINE_PLAN',
  'E_HOST_ENGINE_RANGE', 'E_HOST_ENGINE_REQUIREMENT', 'E_HOST_ENGINE_RESERVED',
  'E_HOST_ENGINE_VERSION',
]);

/** The closed error `core` of contract section 2.4. Key order is the oracle's insertion order. */
export interface Diagnostic { error_code: string; msg: string; cook_error_code?: string; }

/** What `cook_code_from_stream` needs of a binary stream: `seek(0)` and `read(size)`. */
export interface ByteStream { seek(position: number): unknown; read(size: number): Uint8Array; }

// A bytes pattern: one byte is one character, so the scan runs over a latin-1 view.
const CODE = /(?<![A-Za-z0-9_])(E_[A-Z_]+)(?![A-Za-z0-9_])/g;

/** errors.py:47-52 */
export function cook_code(output: Uint8Array): string | null {
  const view = Buffer.from(output.buffer, output.byteOffset, output.byteLength).toString('latin1');
  for (const match of view.matchAll(CODE)) {
    const code = match[1]!;
    // row 28 errors.closed: only table codes pass through. errors.py:50
    if (COOK_CODES.has(code)) return code;
  }
  return null;
}

/** errors.py:55-65 */
export function cook_code_from_stream(stream: ByteStream): string | null {
  stream.seek(0);
  let tail: Uint8Array = new Uint8Array(0);
  for (;;) {
    const chunk = stream.read(65536);
    if (!chunk.length) return null;
    const window = new Uint8Array(tail.length + chunk.length);
    window.set(tail); window.set(chunk, tail.length);
    const code = cook_code(window);
    if (code) return code;
    // row 28 errors.closed: 128-byte overlap of the scan window. errors.py:65
    tail = chunk.slice(Math.max(0, chunk.length - 128));
  }
}

/** errors.py:68-74 */
export function diagnostic(code: PyValue, upstream: PyValue = null): Diagnostic {
  // row 28 errors.closed: an unknown code collapses. errors.py:69
  const known = typeof code === 'string' && Object.hasOwn(MESSAGES, code) ? code : 'E_DEPLOY_FAILED';
  const result: Diagnostic = { error_code: known, msg: known + ': ' + MESSAGES[known]! };
  // row 28 errors.closed: upstream only under E_COOK_FAILED and only from the table. errors.py:71
  if (known === 'E_COOK_FAILED' && typeof upstream === 'string' && COOK_CODES.has(upstream)) {
    result.cook_error_code = upstream;
    result.msg += ' (cook: ' + upstream + ')';
  }
  return result;
}

/** Internal context is never serialized; public() uses only closed literals. errors.py:77-84 */
export class SafeError extends PyValueError {
  declare code: PyValue;
  declare cook_error_code: PyValue;
  constructor(message: string, code: PyValue = 'E_DEPLOY_FAILED', cook_error_code: PyValue = null) {
    super(message);
    this.code = code; this.cook_error_code = cook_error_code;
  }
  override get pyName(): string { return 'SafeError'; }
  public(): Diagnostic { return diagnostic(this.code, this.cook_error_code); }
}

/** Accept only an exact closed diagnostic envelope; discard every raw byte. errors.py:87-97 */
export function adapter_diagnostic(output: unknown): Diagnostic {
  const fallback = diagnostic('E_COOK_FAILED');
  // `json.loads` raises TypeError for anything but text or bytes; the oracle maps that to the fallback too.
  if (typeof output !== 'string' && !(output instanceof Uint8Array)) return fallback;
  let data: PyValue;
  try {
    data = loads(output);
  } catch (error) {
    // errors.py:96: ValueError (JSON and Unicode errors included) and RecursionError.
    if (error instanceof PyValueError || error instanceof PyRecursionError) return fallback;
    throw error;
  }
  if (!isDict(data)) return fallback;
  const safe = diagnostic(get(data, 'error_code'), get(data, 'cook_error_code'));
  // row 28 errors.closed: the envelope must equal the closed literal exactly. errors.py:95
  return eq(data, new Map(Object.entries(safe))) ? safe : fallback;
}
