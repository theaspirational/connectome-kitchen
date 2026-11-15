# Modules 1–5 in TypeScript: `errors`, `lock`, `inputs`, `archive`, `config`

Plan section 3.1, modules 1–5 (plan phase 3), ported from the frozen oracle at
`a49b5dc`. This note says how the port keeps the oracle's behaviour, what the
oracle does that the plan's divergence table (section 3.2) did not list, how
the lazy imports are kept, where the port deliberately differs from the oracle,
what was ruled on those points and what is still open. Counts are in the
dispatch report, not here.

## Shape of the port

- One oracle module is one TS module with the same public names:
  `src/errors.ts`, `src/lock.ts`, `src/inputs.ts`, `src/archive.ts`,
  `src/config.ts`. `lock` owns
  `credential_shaped`, `repository` and `names_only`, as in the oracle and in
  plan section 3.1. (The inventory table names `pylike/credential.ts` as TS
  owner of row 1; that file does not exist. Row 1 is marked at `src/lock.ts`.)
- Values read from JSON use one model, `src/pylike/py.ts`: `null`, `boolean`,
  `Int` (arbitrary precision), `Float`, `NonFinite`, `string`, arrays, and
  `Map` for a dict. A `Map` keeps the document's key order for every key; a
  plain object would move integer-like keys to the front, and the order decides
  which of two refusals `names_only` reports.
- A guard is always `require(<condition>, <message>)` or
  `need(<condition>, <message>)`, with the oracle's message, under a comment
  `// row N <mutant id>: ... <module>.py:L`. `scripts/porting-map.py` builds
  `PORTING.md` from those comments and `test/mutants/ts_mutants.py` finds the
  TS guard of an oracle mutant through them.
- Refusals are by construction (plan 3.2 row 17). Where a Python operator can
  raise, the helper in `py.ts` raises the same class on purpose (`KeyError`,
  `TypeError`, `AttributeError`, `OSError`, `RuntimeError`, `RecursionError`,
  `ValueError` and its subclasses), and a TS `catch` tests for exactly the
  classes the oracle's `except` names. No handler catches a JavaScript runtime
  error; one that occurs is a bug and surfaces as a crash.
- `io/fs-safe.ts` is not used for the two reads these modules make
  (`inputs.read_file`, the unit digests in `lock.map_lock`). It refuses hard
  links, foreign owners and files over a bound; the oracle does not, so using
  it would change behaviour. The reads follow the oracle: `resolve() == path`,
  `lstat` regular, then a plain read.

## Plan section 3.2, row by row, for these modules

| Row | Where it bites here | How the port handles it | Evidence |
| --- | --- | --- | --- |
| 1, 7 number kind and size | `type(x) is int` for `version`, output `mode`, dependency `size`; any number in a lock or a recipe | `Int`/`Float` from `pyjson.ts`; a float `2.0` is not version 2 | vectors `lock.map.top.version.*`, `output.mode.*`, `deps.item.size.*` |
| 2, 3 key order | `names_only` reports the first violation in document order; `map_lock` returns the document | `Map` everywhere | vectors `lock.map.names.order.*`, `valid.integer-like-keys`; every accepted result is compared with its key order |
| 5 `\w`, `\b` | the four credential patterns | classes built from the oracle interpreter's tables; `\b` written as a look-behind on that class | mutants `lock.credential.unicode-word`, `.unicode-boundary`, `.word-boundary`; code-point sweep in `port_primitives.py` |
| 6 `.`, `$` | `.*` in the key marker; every `re.fullmatch` | `.` is `[^\n]`; `fullmatch` is `^…$` without the `m` flag, which has no final-newline exception | mutant `lock.credential.dot`; vectors ending in `\n` for every grammar |
| 8 duplicate keys, non-finite numbers | `read_lock`, `verify_bytes` | `pyjson.loads` takes the two hooks and calls them in CPython's order: a constant where it stands, the pairs hook when its object closes | vectors `lock.read.duplicate.*`, `nonfinite.*`, `duplicate.before-nonfinite`, `syntax.before-duplicate` |
| 9 paths | `external_command`, `external_data_path`, build identity, `contained_file`, `read_file` | `pathlib.ts`: `PurePosixPath` text rules, non-strict `resolve()`, `is_file`/`is_symlink` error rules, `relative_to` | vectors `lock.map.identity.*`, `launch.script.*`, `user-unit.*`, `inputs.read.*`; pathlib kinds in `port_primitives.py` |
| 10 URLs | `repository` | `url.ts`: `urlsplit` of the oracle interpreter, lazy `.port`, bracketed hosts, NFKC check, `unquote` | vectors `lock.repository.*`; url kinds in `port_primitives.py` |
| 16 whitespace sets | `\s`/`\S` in the patterns, `isspace` in `repository` | class from the oracle's table (JavaScript `\s` has U+FEFF and lacks U+001C–U+001F and U+0085) | mutant `lock.credential.unicode-space`; vectors `assign.nbsp`, `assign.fs`, `assign.bom` |
| 17 exception class decides | `read_lock` collapses six classes into one refusal; four `except ValueError` blocks in `map_lock` | modelled classes, see above | vectors `lock.read.unhashable-*`, `lock.read.config-number`, `lock.read.loop`, `operator.config-*` |

| 12 tar | `archive_manifest` lists a `.tgz` and reads one member | `pylike/tarfile.ts`: the read path of the installed `tarfile` on the decompressed bytes (formats, pax and GNU extensions, sparse members, its termination rules). Two deliberate divergences, see below: the gzip container is read strictly (D1), and a long-name or pax block above 2^28 bytes is refused (D3) | `tar` and `targz` kinds in `port_primitives.py`; vectors `archive.manifest.*`; `port_large.py` |

Rows 4 (path sort), 11 (shlex) and 13–15 (YAML, `openat`, flock) do not occur
in these modules. The phase-0 `pylike/tar.ts` is a different thing: a strict
reader for extraction (row 16) that refuses what it does not know. A listing
that refused a member type the oracle merely skips would refuse archives the
oracle accepts, so `archive` does not use it.

## Oracle behaviour the plan did not list

Each item was found while porting, is reproduced by the port, and has vectors.
None was changed. Items marked **ruled** look unintended in the oracle; they
were accepted as reproduced, each with a bound and a backlog entry (ruling O1,
see "Rulings" below).

1. **`re.I` is not JavaScript `/i`.** Under `re.I` a letter also matches what
   lowercases to it in CPython plus CPython's own equivalence list: U+0130 and
   U+0131 for `i`, U+017F for `s`, U+212A for `k`, inside classes as well.
   JavaScript's case folding does not include the two dotted/dotless `i`
   forms. No pattern of the port uses `/i`; the alternatives are spelled out.
   Mutants `lock.credential.ignore-case`, `.case-equivalents`,
   `.class-equivalents`.
2. **`json.loads` on bytes guesses the encoding** (**ruled**, O1.encodings).
   `verify_bytes` hands the recipe bytes to `json.loads`, which accepts UTF-8
   with a byte order mark, UTF-16 and UTF-32 with or without one, and decodes
   with `surrogatepass`. So a UTF-16 recipe with the right digest is accepted
   and installed, although cook reads the file as UTF-8. Vectors
   `inputs.verify.bytes.*`.
3. **Integer literals over 4300 digits are refused**
   (`sys.int_info.default_max_str_digits`), floats of any length are not.
   Vectors `*.integer.4300`, `.4301`, `float.long-digits`.
4. **A float literal that overflows is accepted** (`1E400` is `inf`, no hook is
   called). A lock refuses it later through its schema, with the schema's
   message; a recipe containing it is accepted. This is waiver W1's oddity seen
   from the reader's side. Vectors `overflow-float*`.
5. **Recursion bounds** (**ruled**, O1.recursion). CPython refuses deep nesting with
   `RecursionError`, which `read_lock` and `verify_bytes` turn into a refusal.
   The JSON scanner is bounded by the C recursion counter: on the oracle
   interpreter, called from a script's top level, a container may open at depth
   9997, a hook can still be called from depth 9995 and can still raise from
   depth 9994 (five measured bounds in all, listed in `pyjson.ts`).
   `names_only` is bounded by the frame limit of 1000. The port models these
   bounds as constants for that caller (`pyjson.ts`, `lock.ts`). In the oracle
   they move with the caller: all JSON bounds drop by exactly three levels
   under `exec` or `python -m`, and the frame bound by the caller's own frame
   count. Documents within that band of a bound are oracle-dependent, and no
   fixed constant can match every caller. Every such lock is refused by both
   engines, with one of two messages; only a recipe nested about ten thousand
   levels deep could be accepted by one and refused by the other.
6. **A symlink loop is a `RuntimeError`**, which `read_lock`'s handler does not
   name (lock.py:332), so it leaves `read_lock` and `read_file` as a crash,
   not as a refusal. Reproduced by class. Vectors `lock.read.loop`,
   `inputs.read.loop`, `lock.map.identity.dir.loop`.
7. **`Path.is_file()` re-raises** every `OSError` except four errno values. A
   launch script under an unreadable directory is a `PermissionError` out of
   `map_lock` and the generic refusal out of `read_lock`. Vectors
   `*.unreadable-parent`.
8. **Strict base64 of this CPython accepts padding after a complete quad**
   (`AAAA=`), and refuses whitespace. `codec.ts` follows
   `binascii.a2b_base64(strict_mode=True)` of 3.12 statement by statement.
9. **`external_data_flag` answers `None`, `False` or `True`**, depending on
   which conjunct stopped; the port returns `null`, `false`, `true` alike.
10. **Text decoding of the lock file** follows the locale in the oracle
    (`Path.read_text()`); the port assumes UTF-8, strict.
11. **Filesystem paths** are encoded with `surrogateescape`; a NUL or an
    unencodable surrogate is a `ValueError` in the oracle, not an `OSError`,
    and so takes a different handler. `codec.ts` `fsencode` reproduces class
    and message.
12. **The oracle interpreter's `urlsplit` is not upstream 3.12.3** (**ruled**, O1.interpreter).
    The installed `urllib/parse.py` carries a distribution backport for
    bracketed hosts (`_check_bracketed_netloc`). `repository` therefore
    answers differently from upstream CPython 3.12.3 for URLs with stray
    brackets. `url.ts` follows the installed file, since that is what the
    recorded oracle ran; "CPython 3.12.3" alone does not pin this behaviour.
    The files the port follows, on the oracle host (distribution package
    `3.12.3-1ubuntu0.17`), by sha256:
    `urllib/parse.py` `a67b5694763137dbac085adbf0c22821d435ec8ca22d3bd3786d2c9f4ee748d5`,
    `tarfile.py` `664f862558196d80a46663a81bc16cd51061ccec6c1fdda5950a106e97e12512`,
    `gzip.py` `31e7275c5c20d1b414063c28088b68e7a3e657af60c9c23435bf92e77a1fd1e5`,
    `json/decoder.py` `9f02654649816145bc76f8c210a5fe3ba1de142d4d97a1c93105732e747c285b`.
13. **`map_lock` can raise a bare `ValueError`** whose message is a `repr` of
    two paths: when `build` is reached through a symlink and `launch.userUnit`
    is an absolute path into the real directory, `relative_to` fails
    (lock.py:302). `read_lock` turns it into the generic refusal.
    `pathlib.ts` reproduces class and text.
14. **Unicode versions differ.** The oracle interpreter has Unicode 15.0, Node
    22 and 24 a newer one. Every class the port tests against (`\w`, `\s`,
    `isprintable` for `repr`) is a table generated from the oracle
    interpreter, not a `\p{...}` class. Two things still follow the runtime:
    lowercasing of a host name, of which callers observe only emptiness and
    the first character, and NFKC in the netloc check, where only a character
    that normalizes to one of `/?#@:` matters. Both were swept over every code
    point the oracle assigns.

One limit of the value model: Python can hold a high surrogate followed by a
low one as two characters, JavaScript reads the pair as one. Only bytes decoded
with `surrogatepass` can produce that (`verify_bytes`), where nothing but "is it
an object" is observed.

## Lazy imports

`lock.py` imports `connectome_inputs` (lock.py:149-152) and
`connectome_archive` (lock.py:256-258, :282-284) inside `map_lock`, and the
archive module imports `lock` at its top. The port keeps both properties.
`lock.ts` has no static import of either module, only type imports, which
leave no trace in the compiled file. At the same three places it loads the
module through `createRequire(import.meta.url)`. Node 22.12 and later load an
ES module synchronously this way and return the same module instance an
`import` gets; `test/unit/port-lazy.test.js` asserts that on the compiled
files. `archive.ts` imports `lock.ts` statically. A bundler has to be told
about the three lazy edges (phase 7).

The first slice shipped `src/archive.ts` as a stub of the three names
`lock.py` imports, with the differential replaying the oracle's own archive
calls. The archive port replaced the stub; `lock.ts` did not change.

## Where the port deliberately differs from the oracle

Each entry is a named, tested record in `test/vectors/port-divergences.golden`
(the oracle's outcome and the port's outcome for the same input, with the
reason). All three divergences are ruled: D1 and D2 after the second review,
D3 after the delta review of the first fix round. The rulings are quoted in
`test/port-rulings.golden` and summarized under "Rulings" below.

**D1. The gzip container of an archive is read strictly** (`archive_manifest`,
archive.py:131-135; ten entries `gzip.*`). Accepted by ruling as an explicit,
stricter refusal.

- *Oracle.* `tarfile.open(path, 'r:gz')` reads the compressed stream lazily, in
  8 KiB steps, and only as far as the tar listing and the one manifest need.
  It never checks a CRC-32 or length trailer it does not reach, never sees
  bytes after the point where it stops, and lets a truncated stream escape as
  `EOFError` or `zlib.error`, which archive.py:138 does not name. So an archive
  with a wrong checksum, with junk after the stream or cut short behind the
  manifest is accepted, and one cut short earlier is a runtime error instead of
  a refusal.
- *Port.* `pylike/tarfile.ts` checks the whole container first (every member
  header, the deflate data, CRC-32 and length; concatenated members and zero
  padding as `gzip.py` allows them) and reports any damage as an `OSError`,
  which `archive_manifest` turns into its closed refusal, "unreadable archive
  or manifest", code `E_ADAPTER_INPUT`.
- *Which inputs.* Exactly the containers on which a full read by CPython's
  `gzip` module fails. Nothing else is D1.
- *The public code can change.* The port answers for the container before it
  looks at the content, so a refusal the oracle would have given for the
  content is replaced. Where the oracle says `E_ARCHIVE_IDENTITY` ("archive
  package identity mismatch"), the port says `E_ADAPTER_INPUT`: entry
  `gzip.crc-trailer.identity-mismatch`. Where the oracle already says
  `E_ADAPTER_INPUT` for the content ("archive manifest required", "archive
  manifest must be a mapping"), only the message changes: entries
  `gzip.crc-trailer.no-manifest` and `.manifest-not-mapping`. In the second
  reviewer's fuzz the oracle's side of 6,659 D1 inputs was: accepted 1,434,
  uncaught `EOFError` 1,645, "archive manifest required" 2,594,
  `E_ARCHIVE_IDENTITY` 915, "must be a mapping" 71.
- *Size is not damage.* A container CPython reads to its end is read
  identically at any size. At `ac5e89c` that was false from 4 GiB of
  decompressed data upward: `zlib.crc32` of Node 22.23.3 and 24.14.0 takes the
  length of a buffer modulo 2**32, the port checked the CRC of the whole
  stream in one call, and so refused every such archive as "unreadable".
  The reader now accumulates the CRC over pieces of a few MiB.
  `test/differential/port_large.py` builds an archive of 4.1 GiB, asks both
  engines, and fails unless the valid one is read as the oracle reads it and
  the one with a flipped CRC bit is refused (`test/vectors/port-large.golden`).
- *Why not reproduce the oracle.* It would mean modelling CPython's buffer
  sizes and read pattern to decide which damaged bytes it happens not to look
  at. The archive is an input the operator pinned by digest; an archive whose
  own integrity trailer is wrong is damaged whatever the pin says.
- *Direction.* Fail closed only. In the helper fuzz (`targz` kind, 4,000
  containers) the port accepts no container the oracle's gzip module rejects on
  a full read; for a valid container the two agree on every case. On the
  damaged ones the oracle accepted 1,788, let an uncaught class escape on 376
  and refused 1,009; the port refused all of them.
- *Consequence for adoption.* An archive that today's engine accepts despite a
  damaged container is refused by kitchen. `plan` in shadow mode shows it
  before any window.

The tar level is not part of D1: on the decompressed bytes the port follows
the oracle, including its leniency (a bad header after the first member ends
the listing silently, data after the end marker is ignored).

**D2. `required_names` refuses a NaN among numbers** (config.py:68-72; four
entries `required-names.*`). Named by ruling.

- *Oracle.* `sorted(set(...))` does not fail on a NaN. Every comparison with
  NaN is false, so the list that comes out follows the set's iteration order,
  and for a NaN that follows the address of the float object: `[1.5, nan]`
  for one input, `[nan, 1, 2]` for another, and not the same in every process.
  The entries therefore pin the oracle's members, not their order.
- *Port.* `TypeError`, as for members Python cannot compare.
- *Reach.* No engine module calls `required_names` at this freeze; the
  oracle's tests do. Were its result handed to `read_lock`, a NaN is never
  equal to a declared name, so the oracle refuses there ("lock omits a
  declared required secret"). Nothing is opened in either engine.

**D3. A long-name, long-link or pax block above 2^28 bytes is refused**
(`pylike/tarfile.ts`, `METADATA_LIMIT`; five entries `tar-metadata.*`, and the
`metadata.*` cases of `test/vectors/port-large.golden`). Ruled as a declared
refusal; it closes finding F1 of the first fix round.

- *Oracle.* `tarfile` reads whatever such a header states
  (`fileobj.read(self._block(self.size))`) and decodes it into a `str` of any
  length: the name of the next member, or the records of a pax header. It has
  no limit but the machine's memory.
- *Why the port cannot follow.* A JavaScript string has a longest length, which
  is a property of the runtime (2^29 − 24 UTF-16 units on Node 22.23.3 and
  24.14.0, less on a 32-bit build). Up to `a198042` a name or pax value beyond
  it ended in `RangeError: Invalid string length`, a runtime error at a bound
  the port did not choose.
- *Port.* A constant limit, checked before the block is read:
  - **The limit is 2^28 = 268,435,456.**
  - **Unit: bytes, as the size field of the header states them**, before
    `tarfile` rounds the count up to whole 512-byte blocks.
  - It applies to headers of type `L` (GNU long name), `K` (GNU long link),
    `x` (pax extended), `g` (pax global) and `X` (Solaris extended). A header
    of any other type may state any size.
  - A header that states exactly the limit is read. One byte more is refused
    with an `OSError` whose message is the constant "tar metadata block above
    268435456 bytes", which `archive_manifest` turns into its closed refusal,
    "unreadable archive or manifest", code `E_ADAPTER_INPUT`.
- *Where the refusal stands.* At the point where the oracle makes the read.
  What the oracle refuses about the header before that point comes first in
  the port too (a negative size is "invalid offset"; the frame limit is
  `RecursionError`). What the oracle answers from the read on is replaced.
- *Which inputs.* An archive in whose listing `tarfile` comes to read the
  blocks of such a header that states more than the limit. Nothing else is D3.
  On these the oracle's answer is one of:
  - acceptance: the blocks are there and well formed (the two `above-limit`
    cases of `port-large.golden`); or the short block is no pax record and the
    header is not the first member, which ends a listing silently (O1,
    tar listing);
  - the same closed refusal: the data is missing, `ReadError`. The public
    outcome does not change (case `metadata.longname.above-limit.cut`);
  - `MemoryError` or `OverflowError`, which no handler of the engine names: a
    size the machine cannot allocate (the five entries `tar-metadata.*`, which
    state 2^62 bytes).
- *Both sides of the limit, in both engines.* `test/differential/port_large.py`
  builds four archives that really hold such a block, a long name and a pax
  `path` record of exactly 2^28 bytes and of 2^28 + 1, asks the oracle and the
  port, and asks the oracle separately, with a `TarInfo` that looks on,
  whether its listing reads a block above the limit. At the limit both accept
  and give the same manifest; above it the oracle accepts and the port
  refuses. The port's peak memory on the two refused archives is 92 and 94
  MiB against 886 and 907 at the limit, which shows the block is not read.
  `test/unit/port-tar-limits.test.js` holds the comparison itself for each of
  the five header types on a bare tar stream, without the data.
- *Direction.* Fail closed only: the port refuses archives the oracle accepts
  and accepts none the oracle refuses.
- *Consequence for adoption.* An archive with a single name or pax header of
  more than 256 MiB is refused by kitchen. No package holds one; the archive
  is pinned by digest, so a refusal costs nothing that was meant to work.
- *What D3 does not bound: memory.* Open finding F4; see "Below the limit"
  under the next heading.

## Memory of the archive reader

`archive_manifest` no longer holds the decompressed archive. At `ac5e89c` it
did, twice over: 1.1 GB resident for an archive of 512 MiB, 8.7 GB for 4.1 GiB,
against 18 to 22 MB in the oracle, which streams.

- *Now.* The container is inflated in steps of 4 KiB of compressed input, at
  most a little over 4 MiB of output each: once to check it, again for the
  listing, and again from the front for a backward seek, which is what
  CPython's `GzipFile` does when it rewinds. Measured on the 4.1 GiB archive:
  about 100 MiB resident at the peak, of which about 50 is the runtime with
  the engine loaded. `port_large.py` fails above 256 MiB.
- *What is still held.* The compressed file (the oracle holds it as well,
  archive.py:125); one step of output; a record of eight bytes per step (see
  "Later passes"); and the result of each single `read` the tar reader makes:
  a header block, the manifest (at most 1 MiB, by archive.py:134), and a
  long-name or pax block of at most 2^28 bytes (D3). The oracle holds the
  same blocks, without the limit. Measured at the limit: a long name, oracle
  0.8 GiB, port 0.9 GiB; a pax `path` record, oracle 1.0 GiB, port 0.9 GiB.
- *How.* Node documents no synchronous inflate that can be resumed, so
  `pylike/tarfile.ts` makes the call `inflateRawSync` itself makes on its
  engine (`_processChunk`), one slice at a time, and undoes what that call
  assumes about one-shot use; the three points are listed at the class
  `Inflater`. npm's gzip layer (minizlib 3.1.0, shipped inside both Node
  distributions) drives Node's zlib the same way. It is an undocumented
  surface all the same, and it is tied to a runtime: see "The runtime the
  reader is tied to".
- *No result depends on the step.* The helper differential reads every
  container at three step sizes, down to one byte, and requires the same
  answer from each (`port_primitives.mjs`, `stepwise`). A step is at most
  1 MiB of compressed input, so that one piece of output stays far below
  4 GiB, where the runtime's `crc32` and the step record stop counting.

### The runtime the reader is tied to (ruling B9)

The stepwise call is no documented interface, so it means what one version of
`lib/zlib.js` makes it mean. The reader says which versions it was read and
run on, and refuses the rest.

- *The pin.* Kitchen has one Node pin: `artifacts.node` in
  `oracle/versions.yml`, **version 24.14.0**, payload sha256
  `d56b7f02ecb6e18905eda074d356ff9856ac0bce029cd0aa39a79f030ebabfcd`. It is
  the Node the role places on a host and starts every unit with, and the one
  that runs kitchen in production. `engines.node` in `package.json` (`>=24`)
  is a range, not a pin; the Node typings (24.19.2) are a compiler input.
- *The second version.* **22.23.3** is not a pin. It is the Node installed on
  the development host, and the gates run on it as well as on 24.14.0.
- *In the code.* `STEPWISE_RUNTIMES` in `pylike/tarfile.ts` lists the two. On
  any other `process.versions.node`, or where the runtime reports itself as
  Bun or Deno, reading a gzip container ends in `UnsupportedRuntime` before an
  engine is made: "unsupported runtime: the stepwise gzip reader is verified
  on Node 22.23.3 and 24.14.0 only". That is a plain `Error`. It says nothing
  about the archive, no handler of the engine catches it, and it has no
  public code (backlog B10: report it at start, as a missing prerequisite).
  The reader of an uncompressed tar stream uses no engine and is not tied.
- *Held against the pin.* `test/unit/port-runtime.test.js` reads the version
  from `oracle/versions.yml` and fails if the list does not contain it;
  `scripts/check-port-ready` does the same and compares both with the rulings
  record. So moving the pin turns both red until someone has read
  `processChunkSync`, `_close` and `zlibOnError` of the new `lib/zlib.js`, run
  the gates on the new Node, and added the version.
- *Narrower than the package metadata.* A Node 24.15 satisfies `engines.node`
  and is refused by the reader. That is intended.
- *What could not be checked here.* The development host does not hold
  `inputs/node.tar.gz`, so the payload digest of the pin was not compared.
  The Node 24.14.0 the gates ran on is the binary of the upstream
  `node-v24.14.0-linux-x64.tar.xz` (archive sha256 `41cd79bb…29df`, `bin/node`
  `e237a283…d8a8`).
- *An engine that changes all the same.* Its shape is checked at every step
  (no synchronous step, no handle, no Buffer, wrong input accounting) and ends
  in `UnsupportedRuntime`. Two modes end as a crash instead (review finding
  L2, kept as a note on B9): an engine whose errors arrive only
  asynchronously leaves an unhandled `error` event, and a change these checks
  do not name surfaces as whatever the runtime throws. Both fail closed.

### Later passes (review finding L1)

The container is inflated more than once: to check it, for the listing, from
the front for a backward seek, and for a member read. Only the first pass
compares the trailer. Up to `a198042` the later ones were trusted to produce
the same bytes, and the delta review showed by fault injection that a piece
dropped, repeated or changed in a later pass alone was accepted.

- *Now.* The checking pass records each step: the length of the piece and the
  CRC-32 of its member up to and including it, eight bytes per step
  (`StepRecord`). Every later pass is held to that record piece by piece, and
  a member must end after the same number of steps at the same byte of the
  file (`GzipSource`).
- *A pass that differs* ends in `UnsupportedRuntime` and returns nothing. So
  does damage reported in a later pass. The archive has passed its check by
  then, so the fault is the engine's and is not answered as "unreadable
  archive".
- *Evidence.* `test/unit/port-runtime.test.js` patches the engine's step and
  drops, repeats, zeroes, changes or cuts one piece of output, or stops the
  input early, at calls spread over every pass of four containers: of the
  faults in a later pass, 3,116 end in `UnsupportedRuntime` and none is
  accepted with other content. The reviewer's own probe gives the same
  answer (dispatch report).
- *What it costs.* One more CRC-32 over the bytes a later pass produces,
  which includes the bytes it skips.
- *What the record does not cover.* A fault that changes a piece and leaves
  both its length and the running CRC-32 as they were.

### Below the limit

D3 bounds one block. Checking that this is enough turned up three things
below the limit, all present at `ac5e89c` as well. Two are fixed. The third
is a limit of the runtime that no bound on a single block removes; it is an
open finding.

- *F2, fixed: time.* Two patterns of the tar reader ended in `+$`:
  `rstrip('/')` of a pax path and the blanks around a pax number. A pattern
  like that takes time quadratic in a run that is not at the end. Measured at
  `a198042`: 6 to 8 s for a run of 160,000 characters, so hours for a few MiB
  of slashes or blanks followed by one other character, where the oracle
  takes milliseconds. No wrong answer came of it, and no answer either. Both
  are one pass now (`rstripSlash`, `DecimalScanner`), and no more than 4,300
  digits of a number are kept, the most `int()` takes.
- *F3, fixed: many pax records.* The port kept every record of every header,
  as two Buffers in a list and an entry in a `Map`, the way the oracle keeps
  them in a dict; global headers accumulate over the archive. One header of
  2^24 + 4,096 distinct keywords (185 MB of records): the oracle accepts in
  8 s at 2.2 GiB, the build of `a198042` aborts with "JavaScript heap out of
  memory" at 4.7 GiB. A `Map` of the runtime also ends at 2^24 keys. Now only
  the nine keywords the reader looks at are kept (`PAX_OBSERVED`), in the
  order and with the values the dict would give them. Decoding a record
  cannot fail (the handler is `surrogateescape`), so no other record can
  reach a name, a size or an exception. The same header now: accepted in 3 s
  at 0.3 GiB.
- *F4, open: memory.* Either engine needs memory in proportion to the
  metadata it keeps, and neither models running out of it. The oracle ends in
  `MemoryError`, which no handler names, when the machine has no more. The
  port's heap ends at the limit of the runtime (about 4 GiB by default on
  both verified versions, less on a small machine, more with
  `--max-old-space-size`), and then the process aborts: "JavaScript heap out
  of memory", no receipt. That limit can come before the machine's memory
  does, so there are valid archives the oracle reads and the port does not.
  Measured on the development host (86 GiB), Node 22.23.3:
  - one pax header just below the limit that holds a `GNU.sparse.map` of 2^27
    numbers: the oracle accepts in 9 s at 7.9 GiB; the port aborts after 29 s
    at 6.0 GiB resident;
  - sixteen members, each with a long name of exactly 2^28 bytes: the oracle
    accepts in 5 s at 4.6 GiB; the port aborts after 31 s at 4.4 GiB. With
    eight such members both accept (oracle 2.6 GiB, port 4.2 GiB).

  Other costly blocks at the limit are read by both: 53.6 million records of
  five bytes (oracle 27 s, port 7 s), 5.9 million records of the sparse form
  0.0 (7 s, 4 s). F4 fails closed. No limit on a single block removes it. It
  is not ruled and blocks `scripts/check-port-ready` (see "Still open").

The recursion bound for a chain of several hundred pax or long-name headers is
exact for `archive_manifest` called directly and shifts with the caller's
depth, as it does in the oracle.

## `config` and Ansible's `combine` (plan risk R6)

`config.py` merges layers with `ansible.plugins.filter.core.combine` when
ansible-core is importable and with a stand-in of its own otherwise, which its
docstring calls test-only. ansible-core is not installed on the machine all
vectors were produced on, so the oracle that was recorded and replayed is the
stand-in, and `config.ts` is a port of the stand-in.

The first reviewer ran the oracle against real ansible-core 2.21.6;
`test/differential/r6_combine.py --ansible <lib>` reproduces that run and
`test/vectors/r6-combine.golden` is its result. By ruling, R6 is **closed for
the shape `merge_layers` uses and for nothing else**:

- *Closed.* `merge_layers` is the one production merge site and calls
  `combine` on two mappings with `recursive=True`. All 802 vectors of
  `merge_layers` agree (791 equal, 11 with the same runtime error), as do the
  vectors of `public_catalog` and `required_names`, which do not merge. A
  seeded fuzz of 4,000 four-layer merges built to reach the shortcuts of
  Ansible's `merge_hash` has no difference in value and none in acceptance.
- *A bound inside the closure.* In 643 of those 4,000 merges the result has
  the same value under another mapping key order. `merge_hash` returns the
  upper mapping when the two are equal (`if x == {} or x == y: return
  y.copy()`), the stand-in keeps the lower mapping's order. Whether that order
  is observable is a question for the modules that consume the merged
  configuration (backlog B5).
- *Not closed.* `combine` called on its own. Four vectors differ:
  `config.combine.three` (Ansible folds three layers in the reverse order, so
  a key removed by an intermediate null comes back, which is the reason
  `merge_layers` folds pairwise), and `config.combine.list-layer`,
  `.null-layer`, `.string-layer` (Ansible flattens or accepts a layer that is
  not a mapping, the stand-in raises `AttributeError`). The export `combine`
  of `src/config.ts` is the stand-in and must not get a second caller as it is
  (backlog B6).

## Rulings

Received in two rounds, from the technical owner through the dispatch briefs:
on 2026-11-14 after the second review of `ac5e89c` (APPROVE, three LOW
findings), and on 2026-11-15 after the delta review of the first fix round,
`a198042` (APPROVE, two LOW findings and one INFO). `test/port-rulings.golden`
quotes each one as received and is what `scripts/check-port-ready` reads. The
implementer records rulings and makes none. Three rulings of the second round
left a choice open; the record names each choice as the implementer's
(`left_to_the_implementer`).

| Ruling | What it accepts | Bound |
| --- | --- | --- |
| D1 | the strict gzip read, as an explicit stricter refusal | only containers a full CPython gzip read rejects; the public code may change from `E_ARCHIVE_IDENTITY` to `E_ADAPTER_INPUT`; a large valid archive is not D1 |
| D2 | `TypeError` for a NaN among numbers in `required_names` | two or more numeric members, one a NaN |
| M1 | the eight mutants that are not killed, under their own classification | 5 redundant survivors, 2 compiler, 1 crash-only; none is a kill, 262 of 270 stays 262; the end-to-end witness for `archive.L140.4` is owed with cook or deploy |
| O1 | four oracle behaviours reproduced as they are | items 2, 5 and 12 above and the silent end of a tar listing; each with the bound stated there and a backlog entry |
| R6 | the stand-in as the port of `combine` | `merge_layers` only; four known mismatches of the helper; key order may differ |
| D3 | a declared refusal of a long-name, long-link or pax block above a constant limit; closes F1 | 2^28 bytes of the header's size field; only where the oracle comes to read such a block; the constant was left open and is the one the reviewer suggested |
| B9 | the undocumented zlib call, tied to the pinned Node | Node 24.14.0 (the pin of `oracle/versions.yml`) and 22.23.3 (the development host); any other runtime is refused, which was the choice left open (refuse or flag) |
| L1 | the later passes of the gzip reader | every later pass is held to a record of the checking pass; closing the gap, not narrowing the wording, was the choice left open. L2 stays a note on B9 |

The delta review of `a198042` is recorded as passed, with its commit and the
sha256 of its report, by the implementer on the technical owner's quoted
instruction: the reviewer approved and was not allowed to edit the tree.

The mutant classification:

- *Redundant survivor* (5): `lock.repository.text`, `lock.repository.password`,
  `cook.provenance.origin-password`, `-query`, `-fragment`. Another check of the
  same function implies the one removed, so no input tells the mutant from the
  original. `test/mutants/shadowed_repository.py` tests each implication on the
  oracle.
- *Compiler* (2): `errors.closed.envelope-mapping`, `-type`. The strict
  compiler rejects the program without the check.
- *Crash-only* (1): `archive.L140.4`. Only a runtime error distinguishes it, in
  both engines. `check-port-ready` turns red as soon as `src/cook.ts` or
  `src/deploy.ts` exists without the end-to-end vector.

## Backlog

| Id | For | Entry |
| --- | --- | --- |
| B1 | oracle | refuse a recipe that is not UTF-8 in `verify_bytes`, in both engines, by a reviewed waiver |
| B2 | oracle, plan | bound document depth explicitly, well below the interpreter limits, so that no refusal depends on the caller's depth |
| B3 | plan | pin the oracle interpreter by the sha256 of its files, not by "CPython 3.12.3" |
| B4 | oracle | refuse an archive whose tar listing ends on an invalid header instead of an end marker |
| B5 | port, with cook and catalog | decide whether the key order of the merged configuration is observable; if so, port the order rule of `merge_hash` |
| B6 | port | `combine` matches Ansible only in the `merge_layers` shape; a second caller needs Ansible's semantics |
| B7 | port, with cook or deploy | the end-to-end witness for `archive.L140.4` |
| B8 | closed | finding F1, ruled as divergence D3 |
| B9 | port | replace the undocumented zlib call when Node documents a synchronous inflate that can be resumed. Until then the reader runs on the Node versions of `STEPWISE_RUNTIMES` only, and two modes of a changed engine end as a crash, not as `UnsupportedRuntime` (L2) |
| B10 | port, with the command line (plan phase 7) | check the runtime once at start and report an unverified one as a missing prerequisite with a public code, before any archive is read |

## Still open

- **The delta review of the second fix round** (D3, the runtime check, the
  step record, F2 and F3). The review recorded as passed is the one of
  `a198042`; a file cannot record the review of the commit that contains it.
  `scripts/check-port-ready` prints the commits that review does not cover.
  The port is not port-ready before the second one.
- **Open finding F4: memory below the metadata limit** (backlog B11; "Below
  the limit" above has the measurements). Valid archives on which the oracle
  answers and the port aborts when the heap of the runtime is full. It fails
  closed, and it is not ruled: `scripts/check-port-ready` is red on it.
- A function without a `require`, `need` or `raise` has no oracle mutant:
  `credential_shaped`, `repository`, the errors module, the archive
  predicates, the clauses inside one-`raise` functions. The TS mutants for
  them carry new ids (`lock.credential.*`, `lock.repository.*`,
  `errors.closed.*`, `cook.provenance.*`, some `config.layers.*`); the oracle
  side has no counterpart to share an id with.
- Oracle runtime errors on malformed arguments are compared by class only and
  counted separately, in line with the open phase-1 question about crash-only
  evidence; the report gives the counts.
