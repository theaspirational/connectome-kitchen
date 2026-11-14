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
 * Two deliberate differences from the oracle, both stated here and measured in
 * test/differential/port_primitives.py:
 *
 *   1. The gzip container is checked in full before the tar stream is read.
 *      CPython decompresses lazily, only as far as the listing and one member
 *      need, so damage behind the last byte it reads goes unseen, and a
 *      truncated stream surfaces as `EOFError` or `zlib.error`, which
 *      `archive.py` does not catch. Here any container damage is an `OSError`
 *      (`BadGzipFile` where gzip.py says so). A container CPython reads to its
 *      end without error is read identically.
 *   2. Exceptions that depend on the machine are modelled with constants:
 *      `MemoryError` for a read above MEMORY_LIMIT, `RecursionError` at
 *      Python's default frame limit for a caller at CALLER_FRAME.
 *
 * `tarfile.ENCODING` is taken to be UTF-8 (a UTF-8 locale, or the C locale
 * under PEP 538/540), with the `surrogateescape` handler `tarfile` uses.
 */
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

function enter(frame: number): void {
  if (frame > FRAME_LIMIT) throw new PyRecursionError('maximum recursion depth exceeded');
}

/* ---- gzip container ------------------------------------------------------ */

const FHCRC = 2, FEXTRA = 4, FNAME = 8, FCOMMENT = 16;
const TRUNCATED = 'Compressed file ended before the end-of-stream marker was reached';

/**
 * What `gzip.GzipFile(...).read()` returns when it reads the whole file:
 * every member's header as `_read_gzip_header` accepts it, a complete deflate
 * stream, the CRC-32 and the length modulo 2**32, zero padding after a member,
 * then the next member or the end. An empty file is an empty stream.
 */
export function gunzip(data: Uint8Array): Buffer {
  const input = Buffer.from(data.buffer, data.byteOffset, data.byteLength);
  const parts: Buffer[] = [];
  let at = 0;
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
    let body: Buffer, consumed: number;
    try {
      const result = zlib.inflateRawSync(input.subarray(at), { info: true }) as unknown as { buffer: Buffer; engine: { bytesWritten: number } };
      body = result.buffer; consumed = result.engine.bytesWritten;
    } catch (error) {
      const code = (error as NodeJS.ErrnoException | null)?.code;
      if (code === 'Z_BUF_ERROR') throw new GzipDamage(TRUNCATED);
      if (code === 'ERR_BUFFER_TOO_LARGE' || error instanceof RangeError) throw new PyMemoryError('decompressed archive too large');
      if (typeof code === 'string' && code.startsWith('Z_')) throw new GzipDamage('Error while decompressing data');
      throw error;
    }
    at += consumed;
    const trailer = exact(8);
    if (input.readUInt32LE(trailer) !== zlib.crc32(body)) throw new BadGzipFile('CRC check failed');
    if (input.readUInt32LE(trailer + 4) !== body.length % 4294967296) throw new BadGzipFile('Incorrect length of data produced');
    parts.push(body);
    while (at < input.length && input[at] === 0) at++;
  }
  return parts.length === 1 ? parts[0]! : Buffer.concat(parts);
}

/* ---- Python number parsing ------------------------------------------------ */

const OCTAL = /^[+-]?(?:0[oO]_?)?[0-7]+(?:_[0-7]+)*$/u;
const DECIMAL = /^[+-]?[0-9]+(?:_[0-9]+)*$/u;
/** `sys.int_info.default_max_str_digits`. */
const MAX_INT_DIGITS = 4300;

function decimalValue(text: string): bigint | null {
  const stripped = text.replace(/^[ \t\n\v\f\r]+/u, '').replace(/[ \t\n\v\f\r]+$/u, '');
  if (!DECIMAL.test(stripped)) return null;
  const digits = stripped.replace(/[+_-]/gu, '');
  if (digits.length > MAX_INT_DIGITS) return null;
  return stripped.startsWith('-') ? -BigInt(digits) : BigInt(digits);
}
/** `int(text)` for a str; `null` where Python raises `ValueError`. */
export function pyIntStr(text: string): bigint | null {
  let ascii = '';
  for (const char of text) {
    const code = char.codePointAt(0)!;
    if (code < 127) { ascii += char; continue; }
    if (contains(space, char)) { ascii += ' '; continue; }
    const range = decimal.find(([low, high]) => code >= low && code <= high);
    if (!range) return null;
    ascii += String((code - range[0]) % 10);
  }
  return decimalValue(ascii);
}
/** `int(data)` for bytes; `null` where Python raises `ValueError`. */
export function pyIntBytes(data: Uint8Array): bigint | null {
  for (const byte of data) if (byte >= 0x80) return null;
  return decimalValue(Buffer.from(data.buffer, data.byteOffset, data.byteLength).toString('latin1'));
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
  constructor(readonly data: Buffer) {}
  /** `fileobj.read(count)`; `frame` is the caller's. */
  read(count: bigint | number, frame: number): Buffer {
    enter(frame + 2); // GzipFile.read, _check_not_closed
    const wanted = BigInt(count);
    const left = this.data.length - this.position;
    if (wanted > BigInt(left)) {
      if (wanted > SSIZE_MAX) throw new PyOverflowError("cannot fit 'int' into an index-sized integer");
      if (wanted > SSIZE_MAX - BYTES_OBJECT_OVERHEAD) throw new PyOverflowError('byte string is too large');
      if (wanted > MEMORY_LIMIT) throw new PyMemoryError();
    }
    const end = wanted > BigInt(left) ? this.data.length : this.position + Number(wanted);
    const chunk = this.data.subarray(this.position, end);
    this.position = end;
    return chunk;
  }
  /** `fileobj.seek(target)`: past the end stops at the end, before the start at the start. */
  seek(target: bigint, frame: number): void {
    enter(frame + 2); // GzipFile.seek, _check_not_closed
    if (target > SSIZE_MAX || target < -SSIZE_MAX - 1n) throw new PyValueError("cannot fit 'int' into an offset-sized integer");
    this.position = target < 0n ? 0 : target > BigInt(this.data.length) ? this.data.length : Number(target);
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
const rstripSlash = (name: string): string => name.replace(/\/+$/u, '');

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

/** The blocks that hold a GNU longname or longlink member. */
function procGnulong(info: TarInfo, tar: TarFile, frame: number): TarInfo {
  enter(frame);
  const buf = tar.file.read(info.block(info.size, frame), frame);
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

const ascii = (value: string): Buffer => Buffer.from(value, 'latin1');
const SPARSE_OFFSET = ascii('GNU.sparse.offset'), SPARSE_NUMBYTES = ascii('GNU.sparse.numbytes');
type RawHeader = [keyword: Buffer, value: Buffer];

/** An extended or global header as described in POSIX.1-2008. */
function procPax(info: TarInfo, tar: TarFile, frame: number): TarInfo {
  enter(frame);
  const buf = tar.file.read(info.block(info.size, frame), frame);
  // A pax header stores supplemental information for either the following
  // file (extended) or all following files (global).
  const pax = info.type === XGLTYPE ? tar.pax_headers : new Map(tar.pax_headers);
  // A record looks like "%d %s=%s\n" % (length, keyword, value).
  let pos = 0;
  const raw: RawHeader[] = [];
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
    const record = buf.subarray(start, Math.max(start, last));
    const equals = record.indexOf(0x3d);
    const keyword = equals < 0 ? record : record.subarray(0, equals);
    // Check the framing of the header. The last character must be '\n' (0x0A)
    if (keyword.length === 0 || equals < 0 || buf[last] !== 0x0a) throw new InvalidHeaderError('invalid header');
    raw.push([keyword, record.subarray(equals + 1)]);
    pos += Number(length);
  }
  // A `hdrcharset` record selects between UTF-8 and the archive encoding for the
  // name fields. Both are UTF-8 here, and a field that is not valid UTF-8 falls
  // back to the archive encoding with surrogateescape either way.
  for (const [keyword, value] of raw) {
    enter(frame + 1); // _decode_pax_field, twice
    pax.set(text(keyword), text(value));
  }
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
    const offsets: bigint[] = [], numbytes: bigint[] = [];
    for (const [keyword, value] of raw) {
      const list = keyword.equals(SPARSE_OFFSET) ? offsets : keyword.equals(SPARSE_NUMBYTES) ? numbytes : null;
      if (list === null) continue;
      let number: bigint | null;
      try { number = pyIntStr(decodeUtf8(value, 'strict')); } catch (error) { if (!(error instanceof PyValueError)) throw error; number = null; }
      if (number === null) throw new InvalidHeaderError('invalid header');
      list.push(number);
    }
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

/** `tarfile.open(path, 'r:gz')` on these bytes followed by `getmembers()`. */
export function gzipMembers(data: Uint8Array, options: OpenOptions = {}): TarMember[] {
  return tarMembers(gunzip(data), options);
}

/** The same on an already decompressed tar stream. */
export function tarMembers(stream: Uint8Array, options: OpenOptions = {}): TarMember[] {
  const caller = options.callerFrame ?? CALLER_FRAME;
  const file = new Stream(Buffer.from(stream.buffer, stream.byteOffset, stream.byteLength));
  const tar: TarFile = { file, offset: 0n, pax_headers: new Map(), members: [] };
  // open, gzopen, taropen, TarFile.__init__, then the first next().
  enter(caller + 4);
  let member = next(tar, caller + 5);
  // getmembers, _load, then next() until it returns None.
  while (member !== null) member = next(tar, caller + 3);
  return tar.members.map(info => ({
    name: info.name,
    isfile: REGULAR_TYPES.has(info.type),
    size: info.size,
    type: info.type,
    read(): Uint8Array {
      if (REGULAR_TYPES.has(info.type) || !SUPPORTED_TYPES.has(info.type)) return readMember(info, file);
      if (info.type === LNKTYPE || info.type === SYMTYPE) throw new Error('link members are not read by the engine');
      throw new PyAttributeError("'NoneType' object has no attribute 'read'");
    },
  }));
}
