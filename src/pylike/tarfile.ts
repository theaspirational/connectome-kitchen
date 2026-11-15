/**
 * `tarfile.open(path, 'r:gz')`, `getmembers()` and `extractfile(member).read()`
 * as the CPython 3.12 installed on the oracle host runs them (plan section 3.2
 * row 12). This is the reading half of `tarfile.py` only, ported statement by
 * statement from the tar byte stream onward: header parsing with both number
 * encodings and both checksums, GNU long names, old GNU sparse headers, pax
 * extended and global headers with the three GNU sparse forms, and the rules
 * by which a listing ends silently or fails.
 *
 * Unlike `tar.ts` (the strict reader of phase 0, which refuses what it does not
 * know) this file accepts what CPython accepts, because `archive.py` judges an
 * archive by what `tarfile` reports.
 *
 * Three deliberate differences from the oracle, stated here and measured in
 * test/differential/port_primitives.py and test/differential/port_large.py:
 *
 *   1. The gzip container is checked in full before the tar stream is read
 *      (divergence D1). CPython decompresses lazily, only as far as the listing
 *      and one member need, so damage behind the last byte it reads goes
 *      unseen, and a truncated stream surfaces as `EOFError` or `zlib.error`,
 *      which `archive.py` does not catch. Here any container damage is an
 *      `OSError` (`BadGzipFile` where gzip.py says so). D1 is about damage
 *      only: a container CPython's gzip module reads to its end without error
 *      is read identically, whatever its size (test/differential/port_large.py
 *      holds that at 4 GiB and above).
 *   2. Exceptions that depend on the machine are modelled with constants:
 *      `MemoryError` for a read above MEMORY_LIMIT, `RecursionError` at
 *      Python's default frame limit for a caller at CALLER_FRAME.
 *   3. A long-name, long-link or pax header whose size field is above
 *      METADATA_LIMIT bytes is refused as an `OSError` before its blocks are
 *      read (divergence D3). CPython has no such limit: it reads the block
 *      and decodes it into a `str` of any length the machine can hold. A
 *      JavaScript string cannot be longer than the runtime allows, so without
 *      the limit such an archive ended in a `RangeError` of the runtime.
 *
 * Memory. The decompressed stream is never held as a whole. The container is
 * inflated in steps of INPUT_STEP compressed bytes, once to check it and again
 * for the listing and for each backward seek, as CPython's `GzipFile` rewinds.
 * What is held is the compressed file (the oracle holds it too, archive.py:125),
 * one step of output, a record of eight bytes per step, and whatever a single
 * `read` returns: a header block, a long-name or pax block of at most
 * METADATA_LIMIT bytes, the member that is read. The oracle holds the same
 * `read` results.
 *
 * `tarfile.ENCODING` is taken to be UTF-8 (a UTF-8 locale, or the C locale
 * under PEP 538/540), with the `surrogateescape` handler `tarfile` uses.
 */
import { constants as bufferLimits } from 'node:buffer';
import zlib from 'node:zlib';
import { decodeUtf8 } from './codec.js';
import { PyAttributeError, PyError, PyOSError, PyRecursionError, PyValueError } from './py.js';
import { contains, decimal, space } from './unicode.js';

/** `tarfile.TarError`. Not a `ValueError`, not an `OSError`. */
export class TarError extends PyError { override get pyName(): string { return 'TarError'; } }
export class ReadError extends TarError { override get pyName(): string { return 'ReadError'; } }
export class HeaderError extends TarError { override get pyName(): string { return 'HeaderError'; } }
class EmptyHeaderError extends HeaderError { override get pyName(): string { return 'EmptyHeaderError'; } }
class TruncatedHeaderError extends HeaderError { override get pyName(): string { return 'TruncatedHeaderError'; } }
class EOFHeaderError extends HeaderError { override get pyName(): string { return 'EOFHeaderError'; } }
class InvalidHeaderError extends HeaderError { override get pyName(): string { return 'InvalidHeaderError'; } }
class SubsequentHeaderError extends HeaderError { override get pyName(): string { return 'SubsequentHeaderError'; } }
/** Classes the oracle lets escape `tarfile`; none of them is caught at archive.py:138. */
export class PyIndexError extends PyError { override get pyName(): string { return 'IndexError'; } }
export class PyOverflowError extends PyError { override get pyName(): string { return 'OverflowError'; } }
export class PyMemoryError extends PyError { override get pyName(): string { return 'MemoryError'; } }
/** `gzip.BadGzipFile`, an `OSError`. */
export class BadGzipFile extends PyOSError {
  constructor(message: string) { super('EGZIP', message); }
  override get pyName(): string { return 'BadGzipFile'; }
}
/**
 * A gzip container that ends early or whose deflate data is invalid. The
 * oracle raises `EOFError` or `zlib.error` for these, if it reads that far at
 * all; here they are an `OSError` (difference 1 above).
 */
export class GzipDamage extends PyOSError {
  constructor(message: string) { super('EGZIP', message); }
  override get pyName(): string { return 'OSError'; }
}

/**
 * A long-name, long-link or pax block above METADATA_LIMIT (difference 3
 * above). An `OSError`, so that `archive_manifest` answers with its closed
 * refusal. The message is constant.
 */
export class MetadataLimit extends PyOSError {
  constructor() { super('ETARMETADATA', 'tar metadata block above ' + METADATA_LIMIT.toString() + ' bytes'); }
  override get pyName(): string { return 'OSError'; }
}
/**
 * The host is not one this reader is verified on, or its engine did not do
 * what the reader relies on. A plain `Error`: it says nothing about the
 * archive, and no handler of the engine catches it.
 */
export class UnsupportedRuntime extends Error {
  constructor(message: string) { super('unsupported runtime: ' + message); }
  override get name(): string { return 'UnsupportedRuntime'; }
}

export interface TarMember {
  /** `TarInfo.name`. */
  readonly name: string;
  /** `TarInfo.isfile()`. */
  readonly isfile: boolean;
  /** `TarInfo.size`. */
  readonly size: bigint;
  /** `TarInfo.type`, one byte. */
  readonly type: number;
  /**
   * `tar.extractfile(member).read()`. Exact for a regular member and for one of
   * an unknown type, which `tarfile` reads as a file; `archive.py` reads only
   * members with `isfile`. A link member is not resolved here.
   */
  read(): Uint8Array;
}

export interface OpenOptions {
  /**
   * Python frame depth of the function that calls `tarfile.open`, the script's
   * top level being 1. The default is a function a script calls directly.
   */
  callerFrame?: number;
  /** Compressed bytes per inflate step, INPUT_STEP by default. It changes no result. */
  inputStep?: number;
}

/** Python's default recursion limit, as lock.ts states it: a frame may sit at depth 1000. */
const FRAME_LIMIT = 1000;
export const CALLER_FRAME = 2;
/**
 * A buffered read of more bytes than are available allocates the requested
 * size first. On the oracle host a request of 2**36 bytes succeeds and comes
 * back short, one of 2**38 is `MemoryError`; the bound follows the machine's
 * memory, so requests between the two are machine-dependent.
 */
export const MEMORY_LIMIT = 2n ** 36n;
const SSIZE_MAX = 2n ** 63n - 1n;
const BYTES_OBJECT_OVERHEAD = 33n;
/** Largest member `read()` returns here; `archive.py` reads at most 1 MiB. */
const READ_LIMIT = 2n ** 31n;
/**
 * Largest long-name, long-link or pax block that is read (divergence D3).
 *
 * Unit: bytes, as the size field of a header of type `L`, `K`, `x`, `g` or `X`
 * states them, before `tarfile` rounds the count up to whole 512-byte blocks.
 * 2**28 bytes are 268,435,456 bytes, 256 MiB. A header that states exactly
 * this size is read; one byte more is refused.
 *
 * Every string the reader builds from such a block has at most as many UTF-16
 * units as the block has bytes, so none can exceed the runtime's longest
 * string (2**29 - 24 units on the two verified runtimes; `listing` checks it).
 */
export const METADATA_LIMIT = 2n ** 28n;

function enter(frame: number): void {
  if (frame > FRAME_LIMIT) throw new PyRecursionError('maximum recursion depth exceeded');
}

/* ---- gzip container ------------------------------------------------------ */

const FHCRC = 2, FEXTRA = 4, FNAME = 8, FCOMMENT = 16;
const TRUNCATED = 'Compressed file ended before the end-of-stream marker was reached';
/**
 * Compressed bytes handed to zlib in one step. Deflate expands at most 1032
 * times, so a step yields a little over 4 MiB at most, however the archive
 * was built. No result depends on this number; the helper differential runs
 * every container at several values of it.
 */
export const INPUT_STEP = 4 * 1024;
/** Largest step a caller may ask for: at most a little over 1 GiB of output. */
const INPUT_STEP_MAX = 1024 * 1024;

/**
 * The Node versions on which the stepwise call of `Inflater` is verified: its
 * source in `lib/zlib.js` (`processChunkSync`, `_close`, `zlibOnError`) was
 * read for both, and the gates of the port run on both.
 *
 *   24.14.0  the pin. `artifacts.node.version` in oracle/versions.yml: the
 *            Node the role places on a host and starts every unit with, and
 *            the one that runs kitchen in production.
 *   22.23.3  no pin. The Node installed on the development host, on which the
 *            gates run as well.
 *
 * `engines.node` in package.json allows every Node from 24 up; this list is
 * narrower on purpose. The call is no documented interface, so on any other
 * version the reader refuses before it touches the engine rather than trust
 * that the call still means the same. To move the pin: read those three
 * functions in the new `lib/zlib.js`, run the gates on the new Node, add the
 * version here. test/unit/port-runtime.test.js holds this list against the
 * pin in oracle/versions.yml.
 */
export const STEPWISE_RUNTIMES: readonly string[] = ['22.23.3', '24.14.0'];

function verifiedRuntime(): void {
  const versions: Readonly<Record<string, string | undefined>> = process.versions;
  const version = versions['node'];
  if (typeof version !== 'string' || !STEPWISE_RUNTIMES.includes(version) || versions['bun'] !== undefined || versions['deno'] !== undefined) {
    throw new UnsupportedRuntime('the stepwise gzip reader is verified on Node ' + STEPWISE_RUNTIMES.join(' and ') + ' only');
  }
}

/** What `zlib.inflateRawSync` itself drives (Node lib/zlib.js, `zlibBufferSync`), as far as it is used here. */
interface SyncEngine {
  _handle: { close: () => void } | null;
  _processChunk(chunk: Buffer, flush: number): Buffer;
  bytesWritten: number;
  removeAllListeners(event: string): unknown;
}
const held = (): void => { /* the engine stays open between steps */ };

/**
 * One raw deflate stream, inflated forward in steps of bounded size.
 *
 * Node documents no synchronous inflate that can be resumed: `inflateRawSync`
 * returns the whole output at once, which for an archive is the whole
 * decompressed stream in memory. This class makes the call `inflateRawSync`
 * makes (`_processChunk` without a callback), on one slice of the input at a
 * time. The call was written for one-shot use, and three things follow:
 *
 *   - it closes the engine when it returns. The handle's `close` is held back
 *     for the duration of the call and the handle is put back afterwards;
 *   - it adds an `error` listener each time. After a call that succeeded the
 *     listeners are removed; after one that failed they stay, because the
 *     engine reports that error once more on the next tick;
 *   - the Buffer it returns can be the engine's own output buffer, which the
 *     next call overwrites. `step` therefore lends its result until the next
 *     `step`, and every caller copies what it keeps.
 *
 * npm's gzip layer (minizlib, shipped inside the Node 22 and 24 distributions)
 * drives Node's zlib the same way. The call is tied to the Node versions of
 * STEPWISE_RUNTIMES; `scan` refuses any other before an engine is made. An
 * engine that has another shape all the same ends here with
 * `UnsupportedRuntime`, which no handler of the engine catches.
 *
 * A piece of output that is lost, repeated or changed cannot pass `scan`,
 * which checks every member's CRC-32 and length through this class. `scan`
 * also records each step it saw, and `GzipSource` holds every later read of
 * the same container to that record, so the bytes the tar reader works on are
 * the bytes that were checked.
 */
class Inflater {
  private engine: SyncEngine | null;
  private ended = false;
  /** Offset in the file of the next compressed byte; once the stream has ended, of the first byte behind it. */
  at: number;
  constructor(private readonly input: Buffer, start: number, private readonly inputStep: number) {
    // One step of output must stay far below 4 GiB: `zlib.crc32` and the step record count a piece in 32 bits.
    if (!Number.isInteger(inputStep) || inputStep < 1 || inputStep > INPUT_STEP_MAX) throw new Error('inflate step must be an integer from 1 to ' + String(INPUT_STEP_MAX));
    this.at = start;
    this.engine = zlib.createInflateRaw({ chunkSize: 64 * 1024 }) as unknown as SyncEngine;
  }
  /** The next piece of output, valid until the next call; `null` when the deflate stream has ended. */
  step(): Buffer | null {
    while (!this.ended) {
      if (this.at >= this.input.length) throw new GzipDamage(TRUNCATED);
      const slice = this.input.subarray(this.at, Math.min(this.at + this.inputStep, this.input.length));
      const piece = this.feed(slice);
      const taken = this.engine!.bytesWritten;
      if (!Number.isInteger(taken) || taken < 0 || taken > slice.length) throw new UnsupportedRuntime('zlib engine: input accounting');
      this.at += taken;
      // zlib takes all the input it is given unless the stream ends inside it.
      if (taken < slice.length) { this.ended = true; this.close(); }
      if (piece.length > 0) return piece;
    }
    return null;
  }
  private feed(slice: Buffer): Buffer {
    const engine = this.engine;
    const handle = engine === null ? null : engine._handle;
    if (engine === null || typeof engine._processChunk !== 'function' || handle === null || typeof handle !== 'object' || typeof handle.close !== 'function') {
      throw new UnsupportedRuntime('zlib engine: no synchronous step');
    }
    const close = handle.close;
    handle.close = held;
    let piece: Buffer;
    try {
      piece = engine._processChunk(slice, zlib.constants.Z_NO_FLUSH);
    } catch (error) {
      handle.close = close;
      handle.close();
      this.engine = null;
      const code = (error as NodeJS.ErrnoException | null)?.code;
      if (typeof code === 'string' && code.startsWith('Z_')) throw new GzipDamage('Error while decompressing data');
      throw error;
    }
    handle.close = close;
    engine._handle = handle;
    engine.removeAllListeners('error');
    if (!Buffer.isBuffer(piece)) throw new UnsupportedRuntime('zlib engine: output');
    return piece;
  }
  /** Frees the engine; a stream left unfinished must not wait for the collector. */
  close(): void {
    const engine = this.engine;
    this.engine = null;
    if (engine !== null && engine._handle !== null) { engine._handle.close(); engine._handle = null; }
  }
}

/**
 * What `scan` saw the engine produce, step by step: for every piece of output
 * its length and the CRC-32 of its member up to and including it. Two 32-bit
 * numbers per step, which is eight bytes per INPUT_STEP of compressed input
 * at the most.
 */
class StepRecord {
  private cells = new Uint32Array(256);
  count = 0;
  add(length: number, crc: number): void {
    if (2 * this.count === this.cells.length) { const grown = new Uint32Array(2 * this.cells.length); grown.set(this.cells); this.cells = grown; }
    this.cells[2 * this.count] = length;
    this.cells[2 * this.count + 1] = crc;
    this.count++;
  }
  /** Whether step `index` gave a piece of this length that brought its member's CRC-32 to this value. */
  matches(index: number, length: number, crc: number): boolean {
    return index < this.count && this.cells[2 * index] === length && this.cells[2 * index + 1] === crc >>> 0;
  }
}

/**
 * One gzip member: where its deflate data starts and ends in the file, which
 * decompressed bytes it holds, and which steps of the record are its own.
 */
interface GzipMember { readonly deflate: number; readonly tail: number; readonly start: number; readonly end: number; readonly firstStep: number; readonly steps: number; }
/** A checked container: its members and the record of the steps that produced them. */
interface Scanned { readonly members: GzipMember[]; readonly record: StepRecord; }

/**
 * What `gzip.GzipFile(...).read()` checks when it reads the whole file: every
 * member's header as `_read_gzip_header` accepts it, a complete deflate
 * stream, the CRC-32 and the length modulo 2**32, zero padding after a member,
 * then the next member or the end. An empty file is an empty stream.
 *
 * The CRC is accumulated over the pieces. `zlib.crc32` of Node 22.23.3 and of
 * 24.14.0 takes the length of its argument modulo 2**32, so on a buffer of
 * 4 GiB or more it answers for a prefix; no piece here comes near that size.
 */
function scan(input: Buffer, inputStep: number, sink?: (piece: Buffer) => void): Scanned {
  verifiedRuntime();
  const members: GzipMember[] = [];
  const record = new StepRecord();
  let at = 0, total = 0;
  const exact = (count: number): number => {
    if (input.length - at < count) throw new GzipDamage(TRUNCATED);
    const start = at; at += count; return start;
  };
  const text = (): void => { while (at < input.length) if (input[at++] === 0) return; };
  while (at < input.length) {
    if (input.length - at < 2 || input[at] !== 0x1f || input[at + 1] !== 0x8b) throw new BadGzipFile('Not a gzipped file');
    at += 2;
    const fixed = exact(8);
    if (input[fixed] !== 8) throw new BadGzipFile('Unknown compression method');
    const flag = input[fixed + 1]!;
    if (flag & FEXTRA) { const length = exact(2); exact(input[length]! | (input[length + 1]! << 8)); }
    if (flag & FNAME) text();
    if (flag & FCOMMENT) text();
    if (flag & FHCRC) exact(2);
    const deflate = at, start = total, firstStep = record.count;
    const inflater = new Inflater(input, deflate, inputStep);
    let crc = 0, length = 0;
    try {
      for (let piece = inflater.step(); piece !== null; piece = inflater.step()) {
        crc = zlib.crc32(piece, crc);
        length += piece.length;
        record.add(piece.length, crc);
        sink?.(piece);
      }
    } finally { inflater.close(); }
    at = inflater.at;
    const tail = at;
    total += length;
    const trailer = exact(8);
    if (input.readUInt32LE(trailer) !== crc) throw new BadGzipFile('CRC check failed');
    if (input.readUInt32LE(trailer + 4) !== length % 4294967296) throw new BadGzipFile('Incorrect length of data produced');
    members.push({ deflate, tail, start, end: total, firstStep, steps: record.count - firstStep });
    while (at < input.length && input[at] === 0) at++;
  }
  return { members, record };
}

/** `gzip.GzipFile(...).read()` of the whole file, in memory. The archive reader does not use it; the helper differential does. */
export function gunzip(data: Uint8Array, inputStep: number = INPUT_STEP): Buffer {
  const parts: Buffer[] = [];
  scan(Buffer.from(data.buffer, data.byteOffset, data.byteLength), inputStep, piece => { parts.push(Buffer.from(piece)); });
  return Buffer.concat(parts);
}

/** The bytes a tar reader works on: a length, and a copy of any range. */
interface Source {
  readonly length: number;
  /** The bytes `[position, position + count)`, which lie inside the stream. */
  read(position: number, count: number): Buffer;
  close(): void;
}

class MemorySource implements Source {
  readonly length: number;
  constructor(private readonly data: Buffer) { this.length = data.length; }
  read(position: number, count: number): Buffer { return this.data.subarray(position, position + count); }
  close(): void { /* nothing is held open */ }
}

/**
 * A checked gzip container as the stream of its decompressed bytes, without
 * holding them. Reading forward inflates forward and drops what was passed;
 * reading behind the piece at hand starts again at the member that holds the
 * position, as `GzipFile.seek` rewinds.
 *
 * Inflating again is not checking again: the trailer was compared once, in
 * `scan`. So every piece a later pass produces is held to the record `scan`
 * made of that step, its length and the member's CRC-32 up to it, and a
 * member must end after the same number of steps at the same byte of the
 * file. A pass that differs is the engine's fault, not the archive's, which
 * has passed its check: it ends in `UnsupportedRuntime` and nothing is
 * returned from it.
 */
class GzipSource implements Source {
  readonly length: number;
  private readonly members: GzipMember[];
  private readonly record: StepRecord;
  private inflater: Inflater | null = null;
  private member = 0;
  private piece: Buffer = Buffer.alloc(0);
  /** Decompressed offset of `piece[0]`. */
  private pieceStart = 0;
  /** Steps taken in the member at hand, and its CRC-32 over them. */
  private step = 0;
  private crc = 0;
  constructor(private readonly input: Buffer, private readonly inputStep: number) {
    const scanned = scan(input, inputStep);
    this.members = scanned.members;
    this.record = scanned.record;
    this.length = this.members.length === 0 ? 0 : this.members[this.members.length - 1]!.end;
  }
  read(position: number, count: number): Buffer {
    if (!Number.isInteger(position) || !Number.isInteger(count) || position < 0 || count < 0 || position + count > this.length) throw new Error('read outside the stream');
    const out = Buffer.allocUnsafe(count);
    let filled = 0;
    while (filled < count) {
      const wanted = position + filled;
      if (wanted < this.pieceStart || wanted >= this.pieceStart + this.piece.length) this.reach(wanted);
      const from = wanted - this.pieceStart;
      filled += this.piece.copy(out, filled, from, Math.min(this.piece.length, from + count - filled));
    }
    return out;
  }
  /** Makes `piece` the piece that holds `position`. */
  private reach(position: number): void {
    if (this.inflater === null || position < this.pieceStart) {
      this.close();
      this.member = this.members.findIndex(member => position < member.end);
      this.open();
    }
    while (position >= this.pieceStart + this.piece.length) {
      const member = this.members[this.member]!;
      let next: Buffer | null;
      try {
        next = this.inflater!.step();
      } catch (error) {
        // `scan` read this member to its end, so damage reported now is not the archive's.
        if (error instanceof GzipDamage) throw this.differs();
        throw error;
      }
      this.pieceStart += this.piece.length;
      if (next !== null) {
        this.crc = zlib.crc32(next, this.crc);
        if (this.step >= member.steps || !this.record.matches(member.firstStep + this.step, next.length, this.crc)) throw this.differs();
        this.step++;
        this.piece = next;
        continue;
      }
      // The member must end where `scan` saw it end; the next one continues the stream.
      if (this.step !== member.steps || this.inflater!.at !== member.tail) throw this.differs();
      this.inflater!.close();
      this.member++;
      this.open();
    }
  }
  private differs(): UnsupportedRuntime {
    this.close();
    return new UnsupportedRuntime('zlib engine: a repeated read of the container differs from the read that was checked');
  }
  private open(): void {
    const member = this.members[this.member];
    if (member === undefined) throw new Error('read outside the stream');
    this.inflater = new Inflater(this.input, member.deflate, this.inputStep);
    this.piece = Buffer.alloc(0);
    this.pieceStart = member.start;
    this.step = 0;
    this.crc = 0;
  }
  close(): void {
    this.inflater?.close();
    this.inflater = null;
    this.piece = Buffer.alloc(0);
    this.pieceStart = 0;
  }
}

/* ---- Python number parsing ------------------------------------------------ */

const OCTAL = /^[+-]?(?:0[oO]_?)?[0-7]+(?:_[0-7]+)*$/u;
/** `sys.int_info.default_max_str_digits`. */
const MAX_INT_DIGITS = 4300;

/**
 * `int(text)` in base 10 for text already reduced to ASCII, one character at
 * a time: blanks, a sign, digits with single underscores between them, blanks.
 * It is one pass with early exit, and it keeps no more than MAX_INT_DIGITS
 * digits: a pax value may be as long as METADATA_LIMIT, and a pattern with a
 * trailing `+$` takes time quadratic in a run that is not at the end.
 */
class DecimalScanner {
  /** 0 before the number, 1 after a sign, 2 after a digit, 3 after an underscore, 4 behind the number. */
  private state = 0;
  private negative = false;
  private digits = '';
  /** Takes one character; `false` where no integer can come of it any more. */
  take(code: number): boolean {
    const blank = code === 0x20 || (code >= 0x09 && code <= 0x0d);
    if (code >= 0x30 && code <= 0x39 && this.state <= 3) {
      this.digits += String.fromCharCode(code);
      this.state = 2;
      return this.digits.length <= MAX_INT_DIGITS;
    }
    if (blank && (this.state === 0 || this.state === 4)) return true;
    if (blank && this.state === 2) { this.state = 4; return true; }
    if ((code === 0x2b || code === 0x2d) && this.state === 0) { this.negative = code === 0x2d; this.state = 1; return true; }
    if (code === 0x5f && this.state === 2) { this.state = 3; return true; }
    return false;
  }
  value(): bigint | null {
    if (this.state !== 2 && this.state !== 4) return null;
    return this.negative ? -BigInt(this.digits) : BigInt(this.digits);
  }
}
/** `int(text)` for a str; `null` where Python raises `ValueError`. */
export function pyIntStr(text: string): bigint | null {
  const scanner = new DecimalScanner();
  for (let index = 0; index < text.length; index++) {
    let code = text.codePointAt(index)!;
    if (code >= 127) {
      const char = String.fromCodePoint(code);
      if (code > 0xffff) index++;
      if (contains(space, char)) code = 0x20;
      else {
        const range = decimal.find(([low, high]) => code >= low && code <= high);
        if (!range) return null;
        code = 0x30 + (code - range[0]) % 10;
      }
    }
    if (!scanner.take(code)) return null;
  }
  return scanner.value();
}
/** `int(data)` for bytes; `null` where Python raises `ValueError`. */
export function pyIntBytes(data: Uint8Array): bigint | null {
  const scanner = new DecimalScanner();
  // A byte of 0x80 or more is no blank, no sign and no digit, so the scanner refuses it.
  for (let index = 0; index < data.length; index++) if (!scanner.take(data[index]!)) return null;
  return scanner.value();
}

/* ---- tar stream ------------------------------------------------------------ */

const BLOCK = 512;
const REGTYPE = 0x30, AREGTYPE = 0x00, LNKTYPE = 0x31, SYMTYPE = 0x32, DIRTYPE = 0x35, CONTTYPE = 0x37;
const GNUTYPE_LONGNAME = 0x4c, GNUTYPE_LONGLINK = 0x4b, GNUTYPE_SPARSE = 0x53;
const XHDTYPE = 0x78, XGLTYPE = 0x67, SOLARIS_XHDTYPE = 0x58;
const SUPPORTED_TYPES = new Set([REGTYPE, AREGTYPE, LNKTYPE, SYMTYPE, DIRTYPE, 0x36, CONTTYPE, 0x33, 0x34, GNUTYPE_LONGNAME, GNUTYPE_LONGLINK, GNUTYPE_SPARSE]);
const REGULAR_TYPES = new Set([REGTYPE, AREGTYPE, CONTTYPE, GNUTYPE_SPARSE]);
const GNU_TYPES = new Set([GNUTYPE_LONGNAME, GNUTYPE_LONGLINK, GNUTYPE_SPARSE]);

type Sparse = [offset: bigint, size: bigint][];

/** The `GzipFile` the tar reader sees: the decompressed bytes with a position. */
class Stream {
  position = 0;
  constructor(readonly source: Source) {}
  /** `fileobj.read(count)`; `frame` is the caller's. */
  read(count: bigint | number, frame: number): Buffer {
    enter(frame + 2); // GzipFile.read, _check_not_closed
    const wanted = BigInt(count);
    const left = this.source.length - this.position;
    if (wanted > BigInt(left)) {
      if (wanted > SSIZE_MAX) throw new PyOverflowError("cannot fit 'int' into an index-sized integer");
      if (wanted > SSIZE_MAX - BYTES_OBJECT_OVERHEAD) throw new PyOverflowError('byte string is too large');
      if (wanted > MEMORY_LIMIT) throw new PyMemoryError();
    }
    const length = wanted > BigInt(left) ? left : Number(wanted);
    const chunk = this.source.read(this.position, length);
    this.position += length;
    return chunk;
  }
  /** `fileobj.seek(target)`: past the end stops at the end, before the start at the start. */
  seek(target: bigint, frame: number): void {
    enter(frame + 2); // GzipFile.seek, _check_not_closed
    if (target > SSIZE_MAX || target < -SSIZE_MAX - 1n) throw new PyValueError("cannot fit 'int' into an offset-sized integer");
    this.position = target < 0n ? 0 : target > BigInt(this.source.length) ? this.source.length : Number(target);
  }
  /** `fileobj.tell()`. */
  tell(frame: number): bigint {
    enter(frame + 3); // GzipFile.tell, GzipFile.seek, _check_not_closed
    return BigInt(this.position);
  }
}

class TarInfo {
  name = '';
  size = 0n;
  type = REGTYPE;
  linkname = '';
  offset = 0n;
  offset_data = 0n;
  sparse: Sparse | null = null;
  pending: { structs: Sparse; isextended: boolean; origsize: bigint } | null = null;
  isreg(frame: number): boolean { enter(frame + 1); return REGULAR_TYPES.has(this.type); }
  isdir(frame: number): boolean { enter(frame + 1); return this.type === DIRTYPE; }
  /** `self._block(count)`. */
  block(count: bigint, frame: number): bigint {
    enter(frame + 1);
    // Only non-negative offsets are allowed
    if (count < 0n) throw new InvalidHeaderError('invalid offset');
    return (count + 511n) / 512n * 512n;
  }
}

interface TarFile {
  file: Stream;
  offset: bigint;
  pax_headers: Map<string, string>;
  members: TarInfo[];
}

const text = (bytes: Uint8Array): string => decodeUtf8(bytes, 'surrogateescape');
/** `nts(s, encoding, errors)` with the archive encoding. */
function nts(bytes: Uint8Array, frame: number): string {
  enter(frame + 1);
  const end = bytes.indexOf(0);
  return text(end < 0 ? bytes : bytes.subarray(0, end));
}
/** `nti(s)`: a number field, octal text or base 256. */
function nti(field: Uint8Array, frame: number): bigint {
  enter(frame + 1);
  if (field.length === 0) throw new PyIndexError('index out of range');
  if (field[0] === 0o200 || field[0] === 0o377) {
    let value = 0n;
    for (let index = 1; index < field.length; index++) value = (value << 8n) + BigInt(field[index]!);
    if (field[0] === 0o377) value = -(256n ** BigInt(field.length - 1) - value);
    return value;
  }
  enter(frame + 2); // nts
  const end = field.indexOf(0);
  const cut = end < 0 ? field : field.subarray(0, end);
  for (const byte of cut) if (byte >= 0x80) throw new InvalidHeaderError('invalid header');
  const digits = Buffer.from(cut.buffer, cut.byteOffset, cut.byteLength).toString('latin1').replace(/^[\t-\r\x1c-\x20]+/u, '').replace(/[\t-\r\x1c-\x20]+$/u, '') || '0';
  if (!OCTAL.test(digits)) throw new InvalidHeaderError('invalid header');
  const magnitude = BigInt('0o' + digits.replace(/^[+-]/u, '').replace(/^0[oO]/u, '').replace(/_/gu, ''));
  return digits.startsWith('-') ? -magnitude : magnitude;
}
/** `name.rstrip('/')`, in one pass from the end: a pattern `/+$` is quadratic in a run of slashes that is not at the end. */
function rstripSlash(name: string): string {
  let end = name.length;
  while (end > 0 && name.charCodeAt(end - 1) === 0x2f) end--;
  return end === name.length ? name : name.slice(0, end);
}

/** `TarInfo._frombuf(buf, encoding, errors, dircheck=...)`. */
function frombuf(buf: Buffer, frame: number, dircheck: boolean): TarInfo {
  enter(frame);
  if (buf.length === 0) throw new EmptyHeaderError('empty header');
  if (buf.length !== BLOCK) throw new TruncatedHeaderError('truncated header');
  if (buf.every(byte => byte === 0)) throw new EOFHeaderError('end of file header');
  const chksum = nti(buf.subarray(148, 156), frame);
  enter(frame + 1); // calc_chksums
  let unsigned = 256n, signed = 256n;
  for (let index = 0; index < BLOCK; index++) {
    if (index >= 148 && index < 156) continue;
    unsigned += BigInt(buf[index]!); signed += BigInt(buf[index]! << 24 >> 24);
  }
  if (chksum !== unsigned && chksum !== signed) throw new InvalidHeaderError('bad checksum');
  const info = new TarInfo();
  info.name = nts(buf.subarray(0, 100), frame);
  nti(buf.subarray(100, 108), frame); // mode
  nti(buf.subarray(108, 116), frame); // uid
  nti(buf.subarray(116, 124), frame); // gid
  info.size = nti(buf.subarray(124, 136), frame);
  nti(buf.subarray(136, 148), frame); // mtime
  info.type = buf[156]!;
  info.linkname = nts(buf.subarray(157, 257), frame);
  nts(buf.subarray(265, 297), frame); // uname
  nts(buf.subarray(297, 329), frame); // gname
  nti(buf.subarray(329, 337), frame); // devmajor
  nti(buf.subarray(337, 345), frame); // devminor
  const prefix = nts(buf.subarray(345, 500), frame);
  // Old V7 tar format represents a directory as a regular file with a trailing slash.
  if (dircheck && info.type === AREGTYPE && info.name.endsWith('/')) info.type = DIRTYPE;
  // The old GNU sparse format occupies some of the unused space in the buffer
  // for up to 4 sparse structures.
  if (info.type === GNUTYPE_SPARSE) {
    let pos = 386;
    const structs: Sparse = [];
    for (let index = 0; index < 4; index++) {
      // `except ValueError: break` in the oracle never fires: nti raises InvalidHeaderError.
      const offset = nti(buf.subarray(pos, pos + 12), frame);
      const numbytes = nti(buf.subarray(pos + 12, pos + 24), frame);
      structs.push([offset, numbytes]);
      pos += 24;
    }
    info.pending = { structs, isextended: buf[482] !== 0, origsize: nti(buf.subarray(483, 495), frame) };
  }
  // Remove redundant slashes from directories.
  if (info.isdir(frame)) info.name = rstripSlash(info.name);
  // Reconstruct a ustar longname.
  if (prefix && !GNU_TYPES.has(info.type)) info.name = prefix + '/' + info.name;
  return info;
}

/** `TarInfo._fromtarfile(tarfile, dircheck=...)`. */
function fromtarfile(tar: TarFile, frame: number, dircheck: boolean): TarInfo {
  enter(frame);
  const buf = tar.file.read(BLOCK, frame);
  const info = frombuf(buf, frame + 1, dircheck);
  info.offset = tar.file.tell(frame) - BigInt(BLOCK);
  return procMember(info, tar, frame + 1);
}

function procMember(info: TarInfo, tar: TarFile, frame: number): TarInfo {
  enter(frame);
  if (info.type === GNUTYPE_LONGNAME || info.type === GNUTYPE_LONGLINK) return procGnulong(info, tar, frame + 1);
  if (info.type === GNUTYPE_SPARSE) return procSparse(info, tar, frame + 1);
  if (info.type === XHDTYPE || info.type === XGLTYPE || info.type === SOLARIS_XHDTYPE) return procPax(info, tar, frame + 1);
  return procBuiltin(info, tar, frame + 1);
}

/** A builtin type, or an unknown type which is treated as a regular file. */
function procBuiltin(info: TarInfo, tar: TarFile, frame: number): TarInfo {
  enter(frame);
  info.offset_data = tar.file.tell(frame);
  let offset = info.offset_data;
  // Skip the following data blocks.
  if (info.isreg(frame) || !SUPPORTED_TYPES.has(info.type)) offset += info.block(info.size, frame);
  tar.offset = offset;
  // Patch the TarInfo object with saved global header information.
  applyPaxInfo(info, tar.pax_headers, frame + 1);
  if (info.isdir(frame)) info.name = rstripSlash(info.name);
  return info;
}

/**
 * `tarfile.fileobj.read(self._block(self.size))` for the blocks of a
 * long-name, long-link or pax header. A header that states more than
 * METADATA_LIMIT bytes is refused here and its blocks are not read
 * (divergence D3). The refusal stands where the oracle makes the read, so
 * what the oracle refuses about the header before that point comes first, and
 * whatever the oracle would have answered from the read on is replaced.
 */
function metadataBlocks(info: TarInfo, tar: TarFile, frame: number): Buffer {
  const count = info.block(info.size, frame);
  enter(frame + 2); // GzipFile.read, _check_not_closed
  if (info.size > METADATA_LIMIT) throw new MetadataLimit();
  return tar.file.read(count, frame);
}

/** The blocks that hold a GNU longname or longlink member. */
function procGnulong(info: TarInfo, tar: TarFile, frame: number): TarInfo {
  enter(frame);
  const buf = metadataBlocks(info, tar, frame);
  let next: TarInfo;
  try {
    next = fromtarfile(tar, frame + 1, false);
  } catch (error) {
    if (error instanceof HeaderError) throw new SubsequentHeaderError(error.message);
    throw error;
  }
  next.offset = info.offset;
  if (info.type === GNUTYPE_LONGNAME) next.name = nts(buf, frame);
  else next.linkname = nts(buf, frame);
  if (next.isdir(frame) && next.name.endsWith('/')) next.name = next.name.slice(0, -1);
  return next;
}

/** A GNU sparse header plus extra headers. */
function procSparse(info: TarInfo, tar: TarFile, frame: number): TarInfo {
  enter(frame);
  const { structs, origsize } = info.pending!;
  let { isextended } = info.pending!;
  info.pending = null;
  while (isextended) {
    const buf = tar.file.read(BLOCK, frame);
    let pos = 0;
    for (let index = 0; index < 21; index++) {
      let offset: bigint, numbytes: bigint;
      try {
        offset = nti(buf.subarray(pos, pos + 12), frame);
        numbytes = nti(buf.subarray(pos + 12, pos + 24), frame);
      } catch (error) {
        if (error instanceof PyValueError) break;
        throw error;
      }
      if (offset && numbytes) structs.push([offset, numbytes]);
      pos += 24;
    }
    if (buf.length <= 504) throw new PyIndexError('index out of range');
    isextended = buf[504] !== 0;
  }
  info.sparse = structs;
  info.offset_data = tar.file.tell(frame);
  tar.offset = info.offset_data + info.block(info.size, frame);
  info.size = origsize;
  return info;
}

/**
 * The pax keywords whose values the reader looks at: the ones
 * `_apply_pax_info` turns into a name or a size and the ones the three GNU
 * sparse forms read. The oracle keeps every record of every header in a
 * dict, the global ones for the rest of the archive. Nothing but these nine
 * can reach a name, a size or an exception (decoding a record cannot fail,
 * the handler is `surrogateescape`), so only these are kept here, in the
 * order the dict would hold them. A `Map` of all records would also end at
 * 2**24 keys, the most a `Map` of the runtime takes.
 */
const PAX_OBSERVED = new Set(['GNU.sparse.map', 'GNU.sparse.size', 'GNU.sparse.major', 'GNU.sparse.minor', 'GNU.sparse.name', 'GNU.sparse.realsize', 'path', 'linkpath', 'size']);
const PAX_KEYWORD_MAX = 19; // 'GNU.sparse.realsize'
/** Byte ranges `[start, end)` inside a pax block, flat: two numbers per record. */
type Ranges = number[];

/** An extended or global header as described in POSIX.1-2008. */
function procPax(info: TarInfo, tar: TarFile, frame: number): TarInfo {
  enter(frame);
  const buf = metadataBlocks(info, tar, frame);
  // A pax header stores supplemental information for either the following
  // file (extended) or all following files (global).
  const pax = info.type === XGLTYPE ? tar.pax_headers : new Map(tar.pax_headers);
  // A record looks like "%d %s=%s\n" % (length, keyword, value).
  let pos = 0, records = 0;
  // For each observed keyword of this header the value of its last record, in the order of the first.
  const values = new Map<string, [start: number, end: number]>();
  // The values of the two keywords the sparse form 0.0 reads from the raw records, every record in order.
  const offsetValues: Ranges = [], numbytesValues: Ranges = [];
  while (buf.length > pos && buf[pos] !== 0) {
    let digits = 0;
    while (digits < 20 && buf[pos + digits]! >= 0x30 && buf[pos + digits]! <= 0x39) digits++;
    if (digits === 0 || buf[pos + digits] !== 0x20) throw new InvalidHeaderError('invalid header');
    const length = BigInt(buf.toString('latin1', pos, pos + digits));
    // Headers must be at least 5 bytes, shortest being '5 x=\n'.
    if (length < 5n) throw new InvalidHeaderError('invalid header');
    if (BigInt(pos) + length > BigInt(buf.length)) throw new InvalidHeaderError('invalid header');
    const last = pos + Number(length) - 1; // Last byte of the header
    const start = pos + digits + 1;
    const end = Math.max(start, last);
    const found = buf.subarray(start, end).indexOf(0x3d);
    // Check the framing of the header. The last character must be '\n' (0x0A)
    if (found <= 0 || buf[last] !== 0x0a) throw new InvalidHeaderError('invalid header');
    const equals = start + found;
    if (found <= PAX_KEYWORD_MAX) {
      // Keywords this short are compared as text; the observed ones are ASCII, so equal text is equal bytes.
      const keyword = buf.toString('latin1', start, equals);
      if (PAX_OBSERVED.has(keyword)) values.set(keyword, [equals + 1, end]);
      else if (keyword === 'GNU.sparse.offset') offsetValues.push(equals + 1, end);
      else if (keyword === 'GNU.sparse.numbytes') numbytesValues.push(equals + 1, end);
    }
    records++;
    pos += Number(length);
  }
  // A `hdrcharset` record selects between UTF-8 and the archive encoding for the
  // name fields. Both are UTF-8 here, and a field that is not valid UTF-8 falls
  // back to the archive encoding with surrogateescape either way.
  // The oracle decodes every record here, in two calls of _decode_pax_field
  // each; the frames are entered once, as they are the same for every record.
  if (records > 0) enter(frame + 1);
  for (const [keyword, [start, end]] of values) pax.set(keyword, text(buf.subarray(start, end)));
  // Fetch the next header.
  let next: TarInfo;
  try {
    next = fromtarfile(tar, frame + 1, false);
  } catch (error) {
    if (error instanceof HeaderError) throw new SubsequentHeaderError(error.message);
    throw error;
  }
  // Process GNU sparse information.
  if (pax.has('GNU.sparse.map')) {
    // GNU extended sparse format version 0.1.
    enter(frame + 1);
    const numbers = pax.get('GNU.sparse.map')!.split(',').map(part => {
      const value = pyIntStr(part);
      if (value === null) throw new PyValueError('invalid literal for int() with base 10');
      return value;
    });
    next.sparse = pairs(numbers);
  } else if (pax.has('GNU.sparse.size')) {
    // GNU extended sparse format version 0.0.
    enter(frame + 1);
    // `int(value.decode())` for each record of the two keywords; which of them fails first is not observable.
    const numbers = (ranges: Ranges): bigint[] => {
      const list: bigint[] = [];
      for (let index = 0; index < ranges.length; index += 2) {
        let number: bigint | null;
        try { number = pyIntStr(decodeUtf8(buf.subarray(ranges[index]!, ranges[index + 1]!), 'strict')); } catch (error) { if (!(error instanceof PyValueError)) throw error; number = null; }
        if (number === null) throw new InvalidHeaderError('invalid header');
        list.push(number);
      }
      return list;
    };
    const offsets = numbers(offsetValues), numbytes = numbers(numbytesValues);
    next.sparse = offsets.slice(0, numbytes.length).map((offset, index) => [offset, numbytes[index]!]);
  } else if (pax.get('GNU.sparse.major') === '1' && pax.get('GNU.sparse.minor') === '0') {
    // GNU extended sparse format version 1.0.
    enter(frame + 1);
    let rest: Buffer = tar.file.read(BLOCK, frame + 1);
    const line = (): bigint => {
      const end = rest.indexOf(0x0a);
      if (end < 0) throw new PyValueError('not enough values to unpack (expected 2, got 1)');
      const value = pyIntBytes(rest.subarray(0, end));
      rest = rest.subarray(end + 1);
      if (value === null) throw new PyValueError('invalid literal for int() with base 10');
      return value;
    };
    const fields = line();
    const numbers: bigint[] = [];
    while (BigInt(numbers.length) < fields * 2n) {
      if (!rest.includes(0x0a)) rest = Buffer.concat([rest, tar.file.read(BLOCK, frame + 1)]);
      numbers.push(line());
    }
    next.offset_data = tar.file.tell(frame + 1);
    next.sparse = pairs(numbers);
  }
  if (info.type === XHDTYPE || info.type === SOLARIS_XHDTYPE) {
    // Patch the TarInfo object with the extended header info.
    applyPaxInfo(next, pax, frame + 1);
    next.offset = info.offset;
    if (pax.has('size')) {
      // If the extended header replaces the size field, we need to
      // recalculate the offset where the next header starts.
      let offset = next.offset_data;
      if (next.isreg(frame) || !SUPPORTED_TYPES.has(next.type)) offset += next.block(next.size, frame);
      tar.offset = offset;
    }
  }
  return next;
}

const pairs = (numbers: bigint[]): Sparse => {
  const result: Sparse = [];
  for (let index = 0; index + 1 < numbers.length; index += 2) result.push([numbers[index]!, numbers[index + 1]!]);
  return result;
};

/** `TarInfo._apply_pax_info`: only the fields that reach a name, a size or an exception. */
function applyPaxInfo(info: TarInfo, pax: Map<string, string>, frame: number): void {
  enter(frame);
  for (const [keyword, value] of pax) {
    if (keyword === 'GNU.sparse.name') { enter(frame + 1); info.name = value; }
    else if (keyword === 'GNU.sparse.size' || keyword === 'GNU.sparse.realsize') {
      const size = pyIntStr(value);
      if (size === null) throw new PyValueError('invalid literal for int() with base 10');
      info.size = size;
    } else if (keyword === 'path') { enter(frame + 1); info.name = rstripSlash(value); }
    else if (keyword === 'linkpath') { enter(frame + 1); info.linkname = value; }
    else if (keyword === 'size') info.size = pyIntStr(value) ?? 0n;
    // mtime, uid, gid, uname, gname: a failed conversion becomes 0 and none is observed.
  }
}

/** `TarFile.next()` after the first member. */
function next(tar: TarFile, frame: number): TarInfo | null {
  enter(frame + 1); // next, _check
  // Advance the file pointer.
  if (tar.offset !== tar.file.tell(frame)) {
    if (tar.offset === 0n) return null;
    tar.file.seek(tar.offset - 1n, frame);
    if (tar.file.read(1, frame).length === 0) throw new ReadError('unexpected end of data');
  }
  // Read the next block.
  let info: TarInfo | null = null;
  try {
    enter(frame + 1); // TarInfo.fromtarfile
    info = fromtarfile(tar, frame + 2, true);
  } catch (error) {
    if (error instanceof EOFHeaderError) { /* the end of the archive */ }
    else if (error instanceof InvalidHeaderError) { if (tar.offset === 0n) throw new ReadError(error.message); }
    else if (error instanceof EmptyHeaderError) { if (tar.offset === 0n) throw new ReadError('empty file'); }
    else if (error instanceof TruncatedHeaderError) { if (tar.offset === 0n) throw new ReadError(error.message); }
    else if (error instanceof SubsequentHeaderError) throw new ReadError(error.message);
    else throw error;
  }
  if (info !== null) tar.members.push(info);
  return info;
}

/** `_FileInFile(...)` under an `io.BufferedReader`, read to the end. */
function readMember(info: TarInfo, file: Stream): Uint8Array {
  const map: [data: boolean, start: bigint, stop: bigint, offset: bigint][] = [];
  let lastpos = 0n, realpos = info.offset_data;
  for (const [offset, size] of info.sparse ?? [[0n, info.size]] as Sparse) {
    if (offset > lastpos) map.push([false, lastpos, offset, 0n]);
    map.push([true, offset, offset + size, realpos]);
    realpos += size;
    lastpos = offset + size;
  }
  if (lastpos < info.size) map.push([false, lastpos, info.size, 0n]);
  let size = info.size, position = 0n, index = 0;
  if (size > READ_LIMIT) throw new PyMemoryError();
  const chunks: Buffer[] = [];
  while (size > 0n) {
    let entry = map[index]!;
    while (!(entry[1] <= position && position < entry[2])) {
      index = index + 1 === map.length ? 0 : index + 1;
      entry = map[index]!;
    }
    const [data, start, stop, offset] = entry;
    const length = size < stop - position ? size : stop - position;
    if (data) {
      file.seek(offset + (position - start), 0);
      const chunk = file.read(length, 0);
      if (BigInt(chunk.length) !== length) throw new ReadError('unexpected end of data');
      chunks.push(chunk);
    } else chunks.push(Buffer.alloc(Number(length)));
    size -= length;
    position += length;
  }
  return Buffer.concat(chunks);
}

/**
 * `tarfile.open(path, 'r:gz')` on these bytes followed by `getmembers()`. The
 * container is checked first; nothing stays open between the listing and a
 * member's `read`, which starts at the front of the container again.
 */
export function gzipMembers(data: Uint8Array, options: OpenOptions = {}): TarMember[] {
  return listing(new GzipSource(Buffer.from(data.buffer, data.byteOffset, data.byteLength), options.inputStep ?? INPUT_STEP), options);
}

/** The same on an already decompressed tar stream. */
export function tarMembers(stream: Uint8Array, options: OpenOptions = {}): TarMember[] {
  return listing(new MemorySource(Buffer.from(stream.buffer, stream.byteOffset, stream.byteLength)), options);
}

function listing(source: Source, options: OpenOptions): TarMember[] {
  const caller = options.callerFrame ?? CALLER_FRAME;
  const file = new Stream(source);
  const tar: TarFile = { file, offset: 0n, pax_headers: new Map(), members: [] };
  try {
    // METADATA_LIMIT keeps every string below the longest one the runtime holds only where that is long enough.
    if (METADATA_LIMIT > BigInt(bufferLimits.MAX_STRING_LENGTH)) throw new UnsupportedRuntime('the longest string of this runtime is shorter than a tar metadata block may be');
    // open, gzopen, taropen, TarFile.__init__, then the first next().
    enter(caller + 4);
    let member = next(tar, caller + 5);
    // getmembers, _load, then next() until it returns None.
    while (member !== null) member = next(tar, caller + 3);
  } finally { source.close(); }
  return tar.members.map(info => ({
    name: info.name,
    isfile: REGULAR_TYPES.has(info.type),
    size: info.size,
    type: info.type,
    read(): Uint8Array {
      if (REGULAR_TYPES.has(info.type) || !SUPPORTED_TYPES.has(info.type)) {
        try { return readMember(info, file); } finally { source.close(); }
      }
      if (info.type === LNKTYPE || info.type === SYMTYPE) throw new Error('link members are not read by the engine');
      throw new PyAttributeError("'NoneType' object has no attribute 'read'");
    },
  }));
}
