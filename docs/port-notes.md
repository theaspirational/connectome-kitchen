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

| 12 tar | `archive_manifest` lists a `.tgz` and reads one member | `pylike/tarfile.ts`: the read path of the installed `tarfile` on the decompressed bytes (formats, pax and GNU extensions, sparse members, its termination rules). Three deliberate divergences, see below: the gzip container is read strictly (D1), a long-name or pax block above 2^28 bytes is refused (D3), and a listing is read under four admission budgets (D4) | `tar` and `targz` kinds in `port_primitives.py`; vectors `archive.manifest.*`; `port_large.py` |

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
reason). D1 and D2 were ruled after the second review, D3 after the delta
review of the first fix round. D4 was ruled as a divergence after the delta
review of the second; its numbers are a proposal that is not approved yet.
The rulings are quoted in `test/port-rulings.golden` and summarized under
"Rulings" below.

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
  - A header that states exactly the limit passes it. One byte more is refused
    with an `OSError` whose message is the constant "tar metadata block above
    268435456 bytes", which `archive_manifest` turns into its closed refusal,
    "unreadable archive or manifest", code `E_ADAPTER_INPUT`.
- *A bound of its own, which production never reaches.* Since the closing
  step after the third fix round the byte budget of D4 is 2^24, and the limit
  stays at 2^28 by the technical owner's decision. The reader asks the limit
  first and charges the budget second. So through `archive_manifest`:
  - a block that states more than 2^28 bytes is refused under D3, as before;
  - a block that states more than the budget has left, one of exactly 2^28
    included, is refused under D4 (metadata bytes);
  - no block of the limit's size is ever read. "At the limit both engines
    accept" no longer holds there.

  The limit's own two sides show only under a byte budget raised to the
  limit. That is an option of the reader for tests; `archive_manifest` passes
  none.
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
  `path` record of exactly 2^28 bytes and of 2^28 + 1. It asks the oracle and
  the port through `archive_manifest`, asks the oracle separately, with a
  `TarInfo` that looks on, whether its listing reads a block above the limit,
  and asks the reader itself twice: under the production budgets and under a
  byte budget of 2^28.

  | Block | Oracle | Port, `archive_manifest` | Reader, byte budget raised to 2^28 |
  | --- | --- | --- | --- |
  | exactly 2^28 | accepts | refuses: D4, metadata bytes | lists the members the oracle lists |
  | 2^28 + 1 | accepts | refuses: D3 | refuses: D3 |

  On all four archives the port's peak memory through `archive_manifest` is
  under 100 MiB on Node 22.23.3, which shows the block is not read;
  `port_large.py` fails above 256 MiB. Under the raised budget the block of
  exactly 2^28 is really read (0.6 to 0.9 GiB resident).
  `test/unit/port-tar-limits.test.js` holds the comparison itself for each of
  the five header types on a bare tar stream, without the data, under the
  raised budget and under the production ones.
- *Direction.* Fail closed only: the port refuses archives the oracle accepts
  and accepts none the oracle refuses.
- *Consequence for adoption.* None beyond D4's: an archive with more than
  16 MiB of names and pax headers is refused there before D3 is ever met,
  unless its one block is above 256 MiB, in which case the refusal is D3's.
  No package holds either.
- *What D3 does not bound: memory.* That is D4, next.

**D4. A listing is read under four admission budgets** (`pylike/tarfile.ts`,
`BUDGETS`; three entries `tar-budget.*`, and `test/vectors/port-budget.golden`).
Ruled as a declared divergence; it is how finding F4 is built. The numbers
are approved for the success path on trusted input: "The admission budgets"
below has them with their evidence and says where each comes from.

- *Oracle.* `tarfile` keeps what an archive makes it keep: every name, every
  pax record, every number of a sparse map, every member. It ends in
  `MemoryError`, which no handler names, when the machine has no more.
- *Why the port cannot follow.* Its memory ends at the heap limit of the
  runtime, and then the process aborts ("JavaScript heap out of memory", no
  receipt). An archive of 3.3 kB did that (finding F4).
- *Port.* Four counts over one listing, each held against a constant:

  | Budget | Unit | Value |
  | --- | --- | ---: |
  | metadata bytes | the size field of every `L`, `K`, `x`, `g` and `X` header whose blocks are read, as D3 counts it; plus 512 for every block read for a sparse map (data blocks of form 1.0, old GNU extension blocks) | 2^24 |
  | pax records | every record of every pax header, as the reader comes to look at it | 2^21 |
  | sparse numbers | every integer appended to a sparse list; a region is two, in all four forms | 2^16 |
  | members | every entry of the listing | 2^17 |

  A listing that ends exactly at a budget is read. One unit more is refused
  with an `OSError` whose message is constant, "tar metadata budget exceeded:
  " and the name of the budget; it holds no number and nothing from the
  archive. `archive_manifest` answers "unreadable archive or manifest", code
  `E_ADAPTER_INPUT`. Nothing is credited back when a value is replaced, so no
  count depends on the collector. Each count is taken before the read or the
  allocation it stands for.
- *Which inputs.* An archive in whose listing `tarfile` itself comes to pass
  one of the four counts. `test/differential/tar_budget.py` says where: it
  walks the archive with the oracle's own `tarfile` and counts at the same
  points. Nothing else is D4. From that point on the oracle's answer is
  replaced; it can be acceptance, the same closed refusal, or a class no
  handler names. What `tarfile` refuses before that point comes first in the
  port too: a `GNU.sparse.map` is read number by number, so an invalid number
  before the budget is `ValueError` as before.
- *D3 is a separate bound on one block*, asked before the byte budget is
  charged, with its own refusal and vectors. It lies above the byte budget,
  so a block between the two is refused here (see D3).
- *One unit for sparse maps.* The budget is in numbers. Old GNU: a header's
  four structs are 8, an extension struct 2. Form 0.0: one for each offset
  and each numbytes record. Form 0.1: one for each number of the map, every
  time the map is expanded. Form 1.0: one for each number read. The count a
  1.0 map states is a count of regions; it is never held against the budget,
  the numbers are counted as they are read. So a map whose count is damaged
  ends as it does in the oracle.
- *A map in a global header is counted every time it is expanded*, which is
  for the member after the header and for every later extended header. This
  is the route the delta review of the second fix round found.
- *Under small budgets, against the oracle.* The helper differential reads
  12,000 generated archives under budgets of a few units (kind `tarbudget`):
  where the oracle's walk passes a budget the port refuses with that budget's
  error, and where it passes none the two listings are equal.
- *Under the production budgets, in the helper differential.* The kind `tar`
  lists as `archive_manifest` does, under the production values. Its
  archives are small, but a header may state any size: one that states more
  than 2^24 bytes and no more than 2^28 passes the byte budget before a block
  is read. The kind judges those by the oracle's own walk, which says whether
  the limit of D3 or a budget of D4 comes first (18 of 12,000 archives pass
  the byte budget, 31 meet D3). Before the closing step it knew only D3,
  which was the same thing while the byte budget was 2^28; the full run of
  `npm run qualify` found the one archive that told them apart.
- *At the production values, against the oracle* (part of
  `npm run qualify`, not of `npm test`).
  `test/differential/port_budget.py` builds archives that hold one unit less
  than a budget, exactly a budget and one unit more, for each budget, the
  four sparse forms with the same number of regions, and two archives one
  under all budgets at once. Below and at a budget both engines give the same
  manifest; above it the oracle accepts and the port refuses.
- *Direction.* Fail closed only.
- *Consequence for adoption.* An archive with more than 131,072 members, more
  than 16 MiB of names and pax headers together, more than 2,097,152 pax
  records or more than 65,536 sparse numbers is refused by kitchen. Of 323
  package archives on the development host the largest has 8,271 members,
  333 kB of such metadata and 21 pax records, and none has a sparse member.
- *What D4 does not bound: time.* See "What is not bounded" below.

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
  long-name or pax block of at most 2^24 bytes, the byte budget of D4. The
  oracle holds the same blocks, without a bound. What a listing keeps beyond
  that (names, sparse lists, members) is bounded by the admission budgets
  (D4); see "The admission budgets" below. A block of exactly 2^28 bytes, the
  limit of D3, is read only under a raised test budget: a long name, oracle
  0.8 GiB, reader 0.6 GiB; a pax `path` record, oracle 1.0 GiB, reader
  0.9 GiB.
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
  input early, or lets the step fail as zlib fails on invalid data, at calls
  spread over every pass of four containers. Every fault in a later pass that
  changes a byte ends in `UnsupportedRuntime`, and none is accepted with other
  content; the test prints the counts. The reviewer's own probe, run again on
  this reader, gives the same answer (dispatch report).
- *What it costs.* One more CRC-32 over the bytes a later pass produces,
  which includes the bytes it skips.
- *What the record does not cover.* A fault that changes a piece and leaves
  both its length and the running CRC-32 as they were.

### Below the limit

D3 bounds one block. Checking that this is enough turned up three things
below the limit, all present at `ac5e89c` as well. Two are fixed. The third
is a limit of the runtime that no bound on a single block removes; it became
divergence D4.

- *F2, fixed: time.* Two patterns of the tar reader ended in `+$`:
  `rstrip('/')` of a pax path and the blanks around a pax number. A pattern
  like that takes time quadratic in a run that is not at the end. Measured at
  `a198042`: 6 to 9 s for a run of 160,000 characters, so hours for a few MiB
  of slashes or blanks followed by one other character, where the oracle
  takes milliseconds. No wrong answer came of it, and no answer either. Both
  are one pass now (`rstripSlash`, `DecimalScanner`), and no more than 4,300
  digits of a number are kept, the most `int()` takes.
- *F3, fixed: many pax records.* The port kept every record of every header,
  as two Buffers in a list and an entry in a `Map`, the way the oracle keeps
  them in a dict; global headers accumulate over the archive. One header of
  2^24 + 4,096 distinct keywords (176 MiB of records): the oracle accepts in
  8 s at 2.1 GiB, the build of `a198042` aborts with "JavaScript heap out of
  memory" at 4.5 GiB. A `Map` of the runtime also ends at 2^24 keys. Now only
  the nine keywords the reader looks at are kept (`PAX_OBSERVED`), in the
  order and with the values the dict would give them. Decoding a record
  cannot fail (the handler is `surrogateescape`), so no other record can
  reach a name, a size or an exception. The same header now: accepted in 3 s
  at 0.3 GiB.
- *F4, built as D4: memory.* Either engine needs memory in proportion to the
  metadata it keeps, and neither models running out of it. The oracle ends in
  `MemoryError` when the machine has no more. The port's heap ends at the
  limit of the runtime, and the process aborts: "JavaScript heap out of
  memory", no receipt. That limit can come before the machine's memory does.
  Measured before the budgets, on Node 22.23.3:
  - one pax header just below the limit that holds a `GNU.sparse.map` of 2^27
    numbers: the oracle accepts in 9 s at 7.6 GiB; the port aborted after 29 s
    at 5.7 GiB resident;
  - sixteen members, each with a long name of exactly 2^28 bytes: the oracle
    accepts in 5 s at 4.6 GiB; the port aborted after 31 s at 4.4 GiB;
  - a `GNU.sparse.map` of 2^20 numbers in a global header and 80 members
    behind extended headers, 3.3 kB compressed (the delta review of the
    second fix round): the oracle accepts at 2.9 GiB; the port aborted at
    4.1 GiB.

  Under the budgets the third fix round proposed, all three were refused, on
  both Node versions: the first in 1.5 s at 0.9 to 1.0 GiB resident (its one
  block was within the byte budget of 2^28 and was read before its numbers
  were counted), the second in 2.4 s at 0.6 to 0.8 GiB, with one name held,
  the third in 0.2 s at 153 to 173 MiB. Under the approved budgets each is
  over a budget at its first block or its first expansion (the first two
  state 2^28 bytes against a budget of 2^24, the third 2^20 numbers against
  2^16), so each is refused before that block is read or that list built.
  They were not timed again.
  F4 is ruled as D4 in `test/port-rulings.golden` since the closing step: its
  numbers are approved and its round is reviewed. Ruled is not fixed: what D4
  leaves is recorded there as residual risk, accepted for trusted input by
  ruling C1.

The recursion bound for a chain of several hundred pax or long-name headers is
exact for `archive_manifest` called directly and shifts with the caller's
depth, as it does in the oracle.

### The admission budgets (divergence D4)

**Status: approved for the success path.** The four numbers and the heap
requirement below are the ones `test/port-rulings.golden` records as approved
(ruling D4, `defaults`). They were set in the closing step after the third
fix round, on the technical owner's decision of 2^24 metadata bytes and the
owner's direction to finish the success path on trusted input and leave
hardening for later. The third fix round had proposed 2^28 bytes, 2^24
records, 2^20 sparse numbers and a heap limit of 3,072 MiB; figures from that
round are kept below only where they still say something, and are marked.
Everything was measured on the development host, on Node 22.23.3 and on
24.14.0, the pin. The probes and logs are in the evidence of the rounds, not
in the tree; the tree holds `test/differential/port_budget.py`, which
reproduces the at-budget cases.

**The numbers, and where each comes from.** "Kept" is the heap still in use
after a full collection with the listing referenced. "Peak" is the most heap
in use at any moment of the listing.

| Budget | Value | Where it comes from | Cost at the budget, port | Largest of 323 package archives |
| --- | ---: | --- | --- | ---: |
| metadata bytes | 2^24 | the technical owner's decision | up to 32 MiB kept and 64 MiB at the peak | 333,319 |
| pax records | 2^21 | follows from the byte budget | nothing kept; time only | 21 |
| sparse numbers | 2^16 | the closing step, after LOW-2 of the delta review | 4 MiB kept for short numbers; about 115 MiB for the widest numbers in a global map | 0 |
| members | 2^17 | unchanged from the third fix round | kept: 44 to 143 MiB; 0.8 to 1.2 s | 8,271 |
| heap limit required | 2,048 MiB | measured, below | | |

- *Metadata bytes.* What a byte costs depends on what it decodes to. A text
  of bytes that are no UTF-8 is the costliest: every byte becomes one UTF-16
  unit, two bytes of heap. The reader builds a long text in pieces and its
  first use makes one flat copy, so for a moment both exist (backlog B13):
  up to 2 bytes kept and 4 at the peak for every byte read. Those per-byte
  figures are from blocks of 2^28 bytes in the third fix round; at 2^24 they
  give 32 MiB and 64 MiB. 2^24 is 50 times the largest package archive
  found. The limit of D3, 2^28, is a separate bound on one block and lies
  above this budget (see D3).
- *Pax records.* A record is at least five bytes (`5 x=` and a newline), so
  2^24 bytes of metadata hold at most 3,355,443 records. The 2^24 records
  proposed before could not be reached any more, or shown from both sides.
  2^21 is the largest power of two that can. It bounds time only: the reader
  keeps nothing per record since F3.
- *Sparse numbers.* 58 bytes a number kept where the numbers are short, the
  same in all four forms. **The budget counts numbers, not their size**
  (LOW-2 of the delta review of the third fix round; not fixed in the reader,
  backlog B17):
  - a number can have 4,300 digits, the most `int` reads, and is then about
    1.8 kB;
  - in the three forms that are local to one member every digit is a metadata
    byte, so the byte budget bounds them;
  - a `GNU.sparse.map` in a global header is different: it is converted anew
    at every expansion, while its digits are charged as bytes once, with the
    global header;
  - under the 2^20 proposed before, the reviewer's archive kept 1,907 MiB
    that no budget counted, and aborted Node 24.14.0 at the heap limit then
    required with 524 MiB of caller data.

  The closing step lowers the budget instead of sharing the integers: at
  2^16 numbers the same shape costs about 115 MiB. It is part of
  `combined.heap-worst` and of every figure below. Do not raise this budget
  before B17 is fixed.
- *Members.* 336 bytes kept for a member with a short ASCII name, 763 for one
  whose name, prefix and link name fill the header, 1,091 if those bytes are
  no UTF-8. 2^17 is above the engine's own bound on a catalog archive,
  100,000 entries (`catalog.py:595`), and sixteen times the largest package
  archive found.

**All budgets at once.** Two archives of `port_budget.py`, read through
`archive_manifest` at a heap limit of exactly 2,048 MiB:

| Archive | Port, resident at the peak (22.23.3 / 24.14.0) | Oracle, resident |
| --- | ---: | ---: |
| `combined.all-below`: every count one under its budget | 199 / 283 MiB | 264 MiB |
| `combined.heap-worst`: members, sparse numbers and bytes one under their budgets; member names and one pax path that are no UTF-8; a map of 15 numbers of 4,300 digits in a global header, expanded 4,369 times | 501 / 583 MiB | 407 MiB |

Both are read as the oracle reads them. One listing of the second, with the
heap sampled inside the process, has 321 / 341 MiB in use at its peak, keeps
282 MiB and holds 46 / 78 MiB of Buffers. It is the costliest archive I could
build within the budgets; that it is the costliest there is, is an argument
from the per-unit costs above, not a proof.

**Archives listed one after another.** `dependency_provenance` lists several
archives in one process. The sizing below is deliberately conservative: three
listings of `combined.heap-worst` in a row, with the listing before still
referenced while the next one runs. `archive_manifest` holds less than that
between its calls: three calls in a row leave 387 / 389 MiB in use (the
delta review's INFO-1). At a limit of exactly 2,048 MiB, Node 22.23.3 /
24.14.0:

| Caller's own data on the heap | Heap in use at the peak | Heap committed | Outcome |
| ---: | ---: | ---: | --- |
| none | 801 / 631 MiB | 831 / 727 MiB | read |
| 536 MiB | 1,337 / 1,168 MiB | 1,377 / 1,264 MiB | read, inside the limit |
| 1,076 MiB | 1,799 / 1,720 MiB | 1,847 / 1,804 MiB | read, inside the limit |
| 1,344 MiB | 1,931 MiB / none | 1,993 MiB / none | read on 22.23.3; **the process aborts on 24.14.0** |

The peak that sizes the heap is therefore 801 MiB (606 to 801 across runs on
22.23.3), not the 321 to 341 MiB of one listing.

**The runtime contract.**

- *What the number is.* `HEAP_LIMIT_REQUIRED_MIB` = 2,048 is a bound on
  `v8.getHeapStatistics().heap_size_limit`. That is the old space
  `--max-old-space-size` sets plus the young generation, so it is not the
  flag: on the development host a limit of exactly 2,048 MiB is
  `--max-old-space-size=2000` on Node 22.23.3 and `=1856` on 24.14.0. The
  reader checks the limit itself, at run time, and ends in
  `UnsupportedRuntime` before anything is parsed where it is lower. The
  figures above were measured at exactly 2,048 MiB; `port_budget.py` asks the
  runtime which flag gives it.
- *Why 2,048.* It is what Node gives by default in a unit of 4 GiB of memory
  or more, so such a unit needs no flag. Measured with no flag, in a systemd
  scope with the given `MemoryMax` and no swap, three calls of
  `archive_manifest` on `combined.heap-worst`:

  | `MemoryMax` | Default limit (22.23.3 / 24.14.0) | Outcome |
  | --- | ---: | --- |
  | 2G | 1,048 / 1,120 MiB | every call ends in `UnsupportedRuntime` |
  | 4G | 2,096 / 2,240 MiB | read |
  | 6G | 2,096 / 2,240 MiB | read |
  | 8G | 2,096 / 2,240 MiB | read |

- *Margin.* 1,247 MiB lie between the highest peak of consecutive listings
  and the limit, 61% of the heap. They are the caller's and the collector's.
  The margin is measured on the costliest archive found, not proven for every
  archive within the budgets.
- *Why not 1,024 MiB*, which a unit of 2 GiB would meet by default. Measured
  for information at exactly that limit: one listing completes, with 536 MiB
  of caller data too, but three with the listing before still referenced
  abort on Node 24.14.0 with 269 MiB of caller data. A unit of less than
  4 GiB needs the flag (backlog B14).
- *What the limit does not promise.*
  - It reserves no memory: a process is killed by its cgroup below the limit.
  - V8 passes it when the heap is nearly full. Both of these were measured
    in the third fix round, under the budgets then proposed and a limit of
    3,072 MiB (3,136 MiB committed under it), and not again.
  - It does not count Buffers or the compressed archive: 46 to 79 MiB of
    Buffers at the peak here, plus the size of the archive, which
    `archive_manifest` holds as a whole (the oracle does as well).
  - What the caller holds on the heap comes out of the same limit: the last
    row of the table above.
- *The requirement on the environment.* A V8 heap limit of at least
  2,048 MiB, which is Node's default in a unit of 4 GiB or more, and memory
  for what the process comes to use: about 0.5 to 0.6 GiB resident for the
  costliest archive found, plus what the caller holds.
- *Not known: the target.* No unit runs kitchen yet. This is a requirement,
  not a statement that any host meets it, and the role was not changed.
- *The test suites* pass this check wherever the default limit is 2,048 MiB
  or more. On a host or in a unit where it is lower they fail at the first
  archive with `UnsupportedRuntime`; run them with
  `NODE_OPTIONS=--max-old-space-size=2048`.

**The input this contract is for.** By the owner's decision during the third
fix round (ruling C1), D4 is the first version of a contract for input that
comes from a trusted source and is checked against its pinned digest before
it is parsed. That is a precondition on the caller, to be confirmed at
catalog integration; it is not a proven property. `archive_manifest` itself
compares the sha256 of the bytes with the digest of its entry before the tar
reader is called; where the entry comes from is the caller's. The limits are
chosen with a clear margin above the package archives surveyed, not to accept
as much as the heap can take, and after that decision no further
amplification routes were searched for. What D4 leaves of finding F4 is
recorded as residual risk of this version, not as fixed.

**What is shared between members.** Two things, and neither is a copy. The
integer of a size in a global header is converted once (F6 below). The text
of a name in a global header is one text; a member holds a reference to it,
and a name whose trailing slashes are stripped is a view of it. Both are
immutable values of the runtime. `test/unit/port-tar-budgets.test.js`
measures both: 20,000 members under a size of 4,300 digits keep under
20 MiB, 2,000 members under a path of 4 MiB keep under 16 MiB. A sparse list
is not shared: every expansion makes its own, and every expansion is counted.
The integers of a map in a global header are not shared either, and their
size is not counted (backlog B17, above under "Sparse numbers").

**What is not bounded.**

- *Time* (backlog B12). A name in a global pax header is applied to every
  later member, and both engines look at the whole name for each one. The
  byte budget bounds the name, so this cost fell sixteenfold with it. With
  one `path` of 2^24 bytes in a global header, on a shared host:

  | Path | Port, a member | Oracle, a member | At 2^17 members |
  | --- | ---: | ---: | --- |
  | a run of slashes | 0.013 to 0.016 s | 0.031 s | about half an hour in the port, a little over an hour in the oracle |
  | no trailing slash | 0.0002 s | 0.0065 s | half a minute in the port, a quarter of an hour in the oracle |

  The archive is within every budget. A real package archive has no global
  name. Nothing is built against it; it needs a ruling.
- *The archive itself.* Its compressed bytes are held as a whole, in both
  engines.
- *The caller.* What the calling process holds on the heap comes out of the
  limit the reader requires (table above).
- *Routes not searched for* (backlog B15), by the owner's decision.

**Two things closed on the way, without a budget.**

- *F5, the manifest-name pattern.* `archive_manifest` chose the manifest with
  the pattern `/^[^/]+\/package[^\n]json$/u`. On a name that is not Latin-1
  V8 answers it with "RangeError: Maximum call stack size exceeded" from
  8,387,585 UTF-16 units on, on both Node versions; on a Latin-1 name it
  answers up to 2^28. An archive with one name of 8.4 million CJK characters
  therefore ended in a runtime error, where the oracle accepts it. Present at
  `ac5e89c` as well. The test is now spelled out in one pass
  (`MANIFEST_NAME` in `src/archive.ts`); `test/unit/port-manifest-name.test.js`
  holds it against the pattern on generated names. Under the byte budget of
  2^24 a name that long still reaches the test where a character is one
  byte: nine million bytes that are no UTF-8 are nine million units, and the
  unit test reads such a name. A name of that many characters of two bytes
  or more is over the budget.
- *F6, a size in a global header.* A `size`, `GNU.sparse.size` or
  `GNU.sparse.realsize` of a global header was converted anew for every
  member. An integer of 4,300 digits is 1.8 kB: 2^20 members under one record
  of 4.3 kB were 1.9 GiB that no budget counted. It is converted once now and
  shared.

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

## `deploy` and `cook`: the success path (not qualified)

`src/deploy.ts`, `src/cook.ts`, `src/engine.ts` and `src/io/host.ts` are a
different kind of port from the five modules above. They follow the oracle
function by function for the branches a deployment **without a catalog**
takes, and they are held against it on whole deployments, not on vectors.
They have no mutants, no vectors for hostile input and no review. PORTING.md
therefore still lists `connectome_deploy` and `connectome_cook` as
oracle-only, and `scripts/porting-map.py` was not changed.

**What holds them.** `test/corpus/corpus.py compare` runs the oracle on a
case, then lays the case out again at the same root and runs the TS engine
(`test/corpus/ts_driver.mjs`). The two records must be equal as text: the
role's own check-mode call, contract v1 `plan` and `apply`, every file, link
and directory with modes and text, how the adapter called cook, every call to
the service manager in order, a second apply, a plan after it, a later
release beside the current one (another cook pin), the first release taken
back, the deployment installed and not started (twice), and four refusals. Nine cases: the three public ones in `npm test`, six
of real deployments kept outside the repository. A deliberately broken build
(drop-in text, cook argv, a refusal's code) fails the comparison.
`test/unit/engine.test.js` covers what the corpus puts a seam over: the
adapter's entry script, `verify_cook`'s refusals on a scratch repository, the
lock, the JSON writer, each against the oracle.

**The host seam.** The oracle reads owners, the account and its home through
`Path.lstat`, `os.geteuid`, `os.getgroups` and `pwd.getpwuid`, and its tests
replace those. The TS engine takes a `Host` and asks it at the same call
sites. `realHost` is the host itself. Two things differ from the oracle
there, both outside what a deployment asks:

- the home of an account other than the one the engine runs as is not looked
  up (`KeyError`, which the one caller turns into its refusal);
- the per-agent lock is `flock(2)` on the same file, taken by util-linux
  `flock` on the engine's open descriptor, because Node has no `flock`. The
  two engines exclude each other on one root (tested). The tool is found at a
  fixed path and its digest is not pinned (plan Q6). Without it `apply`
  refuses with the generic code.

**The build's name (ruling D5, plan question Q1).** The oracle's request id
hashes the cook command (`deploy.py:482`), and that command begins with the
interpreter and the path of the adapter script. Until D5 the TS engine did
the same with its own two words (`process.execPath`, `dist/cook-adapter.js`),
so every upgrade or move of Node and every move of kitchen would have given
every build a new name: a cook run and a restart for every agent. Since D5 a
kitchen hashes one word in their place, the name of its adapter contract
(`ADAPTER_CONTRACT` in `src/cook.ts`, first `kitchen-cook-adapter/1` and since
ruling D6 `kitchen-cook-adapter/2`), and then
the same words as the oracle's shim: the three fields and the paths of cook's
runtime, its entrypoint, Bun and Node. The command that is started is still
this Node and the adapter of this install; `Deployment` takes what is hashed
as an option of its own (`cook_identity`) and without it hashes the command,
as the oracle does.

This is a deliberate divergence from the oracle, and the corpus holds it as
one (`test/corpus/README.md`, "The name of a build"). The oracle keeps its
name. The TS engine is run twice on each case: once handed the oracle's two
words, where its record is the oracle's byte for byte, and once as a kitchen
runs it, where the record is the oracle's but for the adapter words and the
digests that follow from the name.

The number in the name is raised when the adapter puts something else into a
build, and for nothing else. `test/corpus/adapter_contract.py` holds the
adapter's output on the public cases against a record kept under the name and
fails on a difference until the number is raised
(`test/unit/adapter-contract.test.js`). It sees what those three cases
exercise; a change that shows on other input only is left to review.

**The number was raised once, to `/2` (ruling D6).** The adapter first got a
Bun cache of the build's own under `/1`, with the number kept, on the ground
that the two names it adds to cook's environment say where Bun keeps its
downloads and cannot change what comes into a build. The delta review of
`40378ef..22caa8d` showed the ground false (finding M1): with an empty cache
Bun asks the registry for every package, and a range that no lockfile pins
resolves to the newest version that fits; a warm account cache can answer the
same request with an earlier version and no request to the registry at all.
So the same recipe and the same pins can give other package bytes, and the
owner had the number raised (poll 2535). What `/2` says, and what it does
not:

- A build named under `/2` was installed from an empty Bun cache of its own.
  That acquisition is part of the contract. So is how Git takes a source
  repository the recipe names by a local path: over its transport, with
  object files of the clone's own (below, "Where the port knowingly
  differs").
- It does not make a build reproducible from its request. Cook's install
  resolves the host's other packages at install time (backlog B26), and two
  `/2` builds of one request made on two days can still differ. `/2` tells
  the two acquisitions apart; it pins nothing.
- No exception to the number is left. Every name in cook's environment is
  under it, and the record of `/2` holds the names as they are handed over
  (`test/corpus/adapter-contract/kitchen-cook-adapter-2.golden`). The record
  of `/1` stays as it was made, without those names, and is not checked any
  more.
- Every build a kitchen made under `/1` gets another name: one cook run and
  one restart for each agent, once. That is accepted (D6) and is not done by
  this change: no account was touched. The decision counts four running
  agents.

What it means for taking over an agent the role deployed: the role's build
has the role's name, so the first `apply` of a kitchen is a new build beside
it, one cook run and one restart, once for each agent (finding 1 of the
plan; `plan` says so beforehand). After that a move or an upgrade of Node or
of kitchen causes no build and no restart. A raised contract number does,
once again for each agent. `compare` shows the sequence on each public case,
with the same Node under another path and a copy of the install elsewhere.
The rehearsal build on the trial account was named before D5, with the paths
in its name; the next `apply` there is a new build too.

**A real cook lock.** One case outside the repository replays the lock of a
live build of one agent, with that build's recipe, on the deploy inputs the
corpus already had for it (`import-build` and `replay_cook.py`,
`test/corpus/README.md`). Cook did not run; the files are a copy. What it
shows:

- The oracle takes the real lock, and the port answers as the oracle does on
  every call and leaves the same tree, on Node 22.23.3 and 24.14.0. Nothing
  in the port had to change.
- The case reproduces the live build. From the layers, the committed recipe
  pin and the role's cook command both engines get the build's request id and
  the bytes of its `deployment.json`. After `apply`, the lock as the adapter
  rewrote it, the layout file, the staged recipe and the marker are the live
  build's files byte for byte, once paths and the request id are carried to
  the sandbox.
- A deliberately broken build fails the case: a launcher path joined without
  regard to its being absolute, and the provenance left out.

Where the real lock differs from the stand-in's, and what reads it:

| Field | Stand-in | Real cook | Read by |
| --- | --- | --- | --- |
| `launch.script`, `launch.userUnit` | names in the build | absolute paths into the build | the reader (`contained_file`), `_units` |
| `engines` | absent | Bun and Node with `path`, `realPath`, `version` | the reader |
| `connectomeHost.path` | absent | the checkout inside the build | the reader |
| `credentials` | the declared names | those and one more that cook adds itself | the reader: a superset is accepted |
| `requirements` | every declared name, `process-env` | the recipe's requirements only, `unresolved`; a secret file's name is in `credentials` alone | the reader |
| `hostDependencies[].snapshot` | `host-dependencies/<archive name>` | `host-inputs/<sha256>.tgz` | the reader (text) |
| `hostDependencies[].originalRange` | `^` and the pinned version | the host's own range, another patch level | the reader (text) |
| `outputs` | the launcher, 0755 | the launcher 0700, the unit 0600, and the host's recipe under the per-request configuration directory, outside the build | the reader (shape only) |
| unit text | `ExecStart=` and a path | `ExecStart=` an interpreter, the quoted launcher and an argument; start limits, `UMask` | copied to `managed-units` as it is |
| `cook` | a made-up version | a version, no commit | the reader |

None of these is handled differently by the two engines. The public case
`example-replay` holds the same shape with made-up values, so `npm test` runs
it. What that copy could not show (the key order cook writes, the application
and engine trees, whether cook's install runs without the network) the
rehearsal below shows.

**A rehearsal: the TS engine with a real cook (plan step 5).** One deployment,
installed and never started (`activation: install_only`), was made on a trial
account of a host by `scripts/rehearse.mjs`: contract v1 `plan` and `apply`
through `src/engine.ts`, the real adapter (`dist/cook-adapter.js`), the real
`verify_cook`, the pinned cook, the host's own service manager behind
`scripts/rehearse/systemctl-readonly`. Its inputs have the shape of the live
build of the section above (a recipe handed over as a pinned file, five
host-installed servers, two archives, six secrets and a secret file, a unit to
exclude), with the trial account's paths and placeholders for every secret.
The inputs and the result stay outside the repository.

What it showed:

- *The engine names the build as the oracle would.* The request id was worked
  out beforehand with the oracle's formula and the cook command as
  `src/engine.ts` worded it then, with the paths of Node and of the adapter
  (before ruling D5). `plan` gave that id on the host, and the
  `deployment.json` the engine wrote is the oracle's canonical request, byte
  for byte (`import-build` checks both).
- *`verify_cook` passes on a real checkout.* The port's four git questions,
  asked by an account that does not own the root-owned checkout, were
  answered and held: the pinned commit, the reviewed tree, a clean tree.
- *Cook ran, and the adapter took its lock.* The layout, the state path, the
  external servers and the dependency inventory held; the provenance was
  added; the reader accepted the result with the configuration.
- *Activation without a start.* The unit copy under `managed-units`, the link
  in the unit directory, the environment drop-in, `secrets.env`, the secret
  file, the pointer and the `install_only` marker were written. No link under
  `default.target.wants`. The manager reported the unit loaded, linked and
  inactive, with the drop-in and the environment file accepted.
  `systemd-analyze --user verify` passed on cook's unit text. The service
  manager was asked `is-active`, `show` and one `daemon-reload`, nothing else.
- *Again, nothing.* The same request a second time: `changed: false` in both
  halves, no cook run, the verifier once more.
- *The oracle agrees with what was left.* The build came back as a case
  (`import-build --build --cooked`). On the replay both engines leave the
  build's lock, layout files, staged recipe, request and marker byte for byte,
  and the oracle's drop-in, unit link, marker and ownership entries are the
  ones on the host. Two digests of the manifest are of texts that hold the
  root's path, which a golden does not pin.

**The lock as cook writes it.** `scripts/rehearse/cook-keep` took it before
the adapter read it:

- It is `JSON.stringify(lock, null, 2)` and a final newline.
- The keys are not sorted. At the top: `version`, `backend`, `engines`,
  `layout`, `state`, `hostDependencies`, `external`, `recipePath`, `createdAt`,
  `connectomeHost`, `components`, `cook`, `recipe`, `build`, `credentials`,
  `requirements`, `localExtensions`, `outputs`, `launch`. A dependency:
  `name`, `originalRange`, `version`, `sha256`, `size`, `snapshot`,
  `packageJsonDelta`. Only `layout` is sorted, because the adapter's layout
  file is.
- The adapter's rewrite is that document with the provenance added, sorted at
  every level, indented by two, with a final newline. Nothing else changes:
  the kept lock equals the build's lock without the provenance.
- The file stays 0600 through the rewrite, in both engines.
- It has the shape of the live build's lock, key for key. Home, name, digests
  and the time set aside, the values are the same.
- `outputs[0]` names the host's own recipe under the per-request
  configuration directory, outside the build. Its digest in the lock is the
  digest of that file. The file is cook's rendering, not the staged recipe's
  bytes.

So the question of the key order is settled: the reader and the adapter do not
depend on it, and a case can now hold cook's own bytes (`cooked` in
`cook_build`).

**What the rehearsal found that is not the port's.**

| Finding | What was seen | Consequence |
| --- | --- | --- |
| Cook's install needs the network | In a network namespace without an interface `bun install` in the host checkout failed: no package manifest for six packages. The engine answered `E_COOK_FAILED` with no cook code; the reason is only in cook's own output. With the network the same request built in seconds | "No network" does not hold for a build as cook makes it today, even with the cache an earlier build of the account had left |
| The build is not pinned by its name alone | Cook replaces two dependencies of the host by the pinned archives. Bun then resolves anew and saves a lockfile that is not the host's committed one | Two builds of one request at different times may hold different versions of the packages nobody pins. The request id does not cover them |
| Builds share inodes through Bun's cache | Bun links files from its per-account cache into `app/node_modules`. An install into one deployment raised the link count and the change time of every shared file of an older deployment of the same account. It also changed the mode of the files a package names as `bin`, from 0775 to 0755: Bun sets that mode from the umask of the install at hand, on the shared inode. Reproduced with the same Bun, two directories and one cache: of the listed fields (path, type, mode, size, modification time, link target) only that mode changed | A retained build is not immutable. Its files' metadata follows later installs, and a write in place to a linked file would reach every build and the cache. Contents and modification times were not changed here |
| The unit verifier needs the user manager's runtime directory | Without `XDG_RUNTIME_DIR`, `systemd-analyze --user verify` fails. The engine learns that after cook, at the end of activation, and takes the activation back (`E_UNIT_VERIFY`); the build stays prepared | The runner refuses beforehand. The engine could ask in its preflight |
| Modes follow the caller's umask | The adapter's `cook-layout.json` has no mode of its own: 0644 under umask 022, 0664 under 002 | The runner and the corpus set 022 |

**Where the port knowingly differs.**

| What | Oracle | Port |
| --- | --- | --- |
| Active catalog | goes on into `connectome_catalog` | refused in `validate_config`: `E_DEPLOY_FAILED`, nothing read or written |
| `~user` in `root`, `base`, `unit_dir` | expanded | refused; `~` and `~/...` are expanded |
| Adapter options | argparse: unique prefixes, `-h` | written out in full or `--name=value`; anything else is `E_ADAPTER_INPUT`, exit 2 |
| Cook command fields | `str.format_map` in full | `{recipe}`, `{build_dir}`, `{state_dir}` and doubled braces; another field is the generic failure, as an unknown name is in the oracle |
| A wrongly typed value where the oracle ends in its generic failure | a specific Python exception class | the same public code; the class is not modelled everywhere |
| The adapter's stderr | spooled to a temporary file, first 65537 bytes read | held in memory, first 65537 bytes read |
| Temporary names of atomic writes | `tempfile` alphabet | eight random characters of another alphabet; never left behind |
| Bun's cache during cook's install | the account's (`$HOME/.bun/install/cache`): every build of the account shares inodes with it, and an install sets modes on them | a cache of the build's own, `ROOT/.bun-cache-REQUEST`, empty, named to cook by `BUN_INSTALL_CACHE_DIR` with `COOK_PASS_ENV` and removed when cook returns; then `E_COOK_FAILED` where a file of the new build is a file of another build under `builds` (`src/isolation.ts`). An empty cache also means Bun resolves every unpinned range against the registry anew, so it can change which package versions a build gets: the acquisition is part of the adapter contract, `/2` (ruling D6). Before cook starts, every `ROOT/.bun-cache-*` is removed, also one a killed run left under another request. The corpus holds the names apart from the oracle's in every cook call (`corpus.cook_calls`) and records them under the contract |
| Git's clone of a source repository the recipe names by a local path | `git clone PATH`: the object files are hard links of the repository's, so two builds from one repository share them | one `url.file://PATH.insteadOf = PATH` for each such repository, handed to cook as `GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>`, `GIT_CONFIG_VALUE_<n>`, which cook passes to its Git as they are: the clone goes over Git's transport, has object files of its own (a pack) and still names the path as its origin. Only for a `source.url` in the recipe handed to cook that begins with `/` and has no line break; a `%` of the path is written `%25` in the `file://` URL, which Git decodes. `host_repo` is not rewritten (cook already clones it with `--no-hardlinks`), and a deployment with no such source has no Git name in cook's environment. Git holds `insteadOf` against the beginning of a URL, so one repository's entry also matches a longer path. Each named path has an entry of its own and Git takes the longest, so a named path goes to itself. No entry is given where the recipe refers to further recipes (`modules.fleet.children`), whose repositories the adapter does not know. And `E_ADAPTER_INPUT`, before cook, where `host_repo` or a named path with a line break begins with a rewritten path and has a `%` after it: Git would be sent to another repository. Part of `/2`. The refusal of a build that shares a file is unchanged and is what holds (backlog B30) |
| A secret named `BUN_INSTALL_CACHE_DIR` or `COOK_PASS_ENV`, or one that begins with `GIT_CONFIG_` | accepted | refused as a name the adapter owns (`E_ADAPTER_INPUT`) |

**Ported, and reached by no case.** These branches were written from the
oracle and nothing has run them against it: the rollback after a failed
activation where a previous release is put back, and where the recovery
fails too; a unit
that is retired or renamed between releases; a secret file that is dropped;
a `health` command; a unit the engine renders itself (a lock without a unit
export); connectors compiled from descriptors (the recipe the adapter writes
with sources and build commands; the fifth case has none enabled); the
refusals of the ownership manifest; `E_UNIT_EXCLUSIVE`.

Reached since, in every case and through the contract: the discovery and
the run of `systemd-analyze` under `install_only` (a stand-in found first on
the search path), its refusal with the rollback of a first install
(`E_UNIT_VERIFY`), the `E_UNIT_ACTIVE` refusal, and the switch between
`install_only` and `start` on one build in both directions, also for a unit
without an `[Install]` section, where the marker `state/activation` alone
decides (`test/corpus/README.md`, `test/unit/deploy-activation.test.js`).

**Obligation `archive.L140.4` (ruling M1).** It came due with these files:
`scripts/check-port-ready` turns red on it as soon as `src/cook.ts` or
`src/deploy.ts` exists. The vector it asks for is
`test/unit/engine.test.js`, "archive.L140.4 end to end". A package manifest
that is a list is refused at the deployment boundary with `E_ADAPTER_INPUT`
by both engines, and with `E_DEPLOY_FAILED` by both mutants. The record in
`test/port-rulings.golden` now says `discharged`, names the test, and says
who entered it: the implementer, with no review yet. Confirming it is part of
the review this slice is owed (backlog B25).

## Rulings

Received in three rounds, from the technical owner through the dispatch
briefs, and in the third also from the owner through the manager: on 2026-11-14 after the second review of `ac5e89c` (APPROVE, three LOW
findings); on 2026-11-15 after the delta review of the first fix round,
`a198042` (APPROVE, two LOW findings and one INFO); and on 2026-11-15 after
the delta review of the second fix round, `621d78d` (APPROVE, three LOW
findings and three INFO). `test/port-rulings.golden`
quotes each one as received and is what `scripts/check-port-ready` reads. The
implementer records rulings and makes none. Three rulings of the second round
left a choice open; the record names each choice as the implementer's
(`left_to_the_implementer`). D5 and D6 came later, from the owner through
the briefs of the steps that built them: D5 on 2026-11-15, and D6 on
2026-11-16 after the delta review of `40378ef..22caa8d` (REJECT as
submitted, one MEDIUM and one LOW finding).

| Ruling | What it accepts | Bound |
| --- | --- | --- |
| D1 | the strict gzip read, as an explicit stricter refusal | only containers a full CPython gzip read rejects; the public code may change from `E_ARCHIVE_IDENTITY` to `E_ADAPTER_INPUT`; a large valid archive is not D1 |
| D2 | `TypeError` for a NaN among numbers in `required_names` | two or more numeric members, one a NaN |
| M1 | the eight mutants that are not killed, under their own classification | 5 redundant survivors, 2 compiler, 1 crash-only; none is a kill, 262 of 270 stays 262; the end-to-end witness for `archive.L140.4` is owed with cook or deploy |
| O1 | four oracle behaviours reproduced as they are | items 2, 5 and 12 above and the silent end of a tar listing; each with the bound stated there and a backlog entry |
| R6 | the stand-in as the port of `combine` | `merge_layers` only; four known mismatches of the helper; key order may differ |
| D3 | a declared refusal of a long-name, long-link or pax block above a constant limit; closes F1 | 2^28 bytes of the header's size field; only where the oracle comes to read such a block; the constant was left open and is the one the reviewer suggested. Kept in the closing step as a separate bound on one block, above the byte budget of D4, so `archive_manifest` never reads a block of that size |
| B9 | the undocumented zlib call, tied to the pinned Node | Node 24.14.0 (the pin of `oracle/versions.yml`) and 22.23.3 (the development host); any other runtime is refused, which was the choice left open (refuse or flag) |
| L1 | the later passes of the gzip reader | every later pass is held to a record of the checking pass; closing the gap, not narrowing the wording, was the choice left open. L2 stays a note on B9 |
| D4 | admission budgets as a declared divergence; it builds F4 | four counts over one listing and a heap limit they are sized for. **The numbers are approved** for the success path on trusted input: 2^24 metadata bytes, 2^21 pax records, 2^16 sparse numbers, 2^17 members, 2,048 MiB. The record says where each comes from |
| C1 | the input of the archive reader is trusted and digest-checked before it is parsed | a precondition on the caller, to be confirmed at catalog integration, not a proven property; what D4 leaves of F4 is residual risk of this version |
| D5 | the name of a build by the adapter's contract, as a declared divergence; it decides plan question Q1 (the owner, option C) | one word, `kitchen-cook-adapter/1`, where the oracle hashes its interpreter and script path; every other hashed word and the command that is started are unchanged. The number is raised only when the adapter's output into a build changes, and a check holds that output under the name. Received after the three rounds above, on 2026-11-15, through the dispatch brief |

The delta review of the third fix round (`621d78d..06ee0cc`) is recorded as
passed, with its commit and the sha256 of its report. The implementer
recorded it on a quoted instruction of the manager of the dispatch, as it did
the review of the second fix round (`a198042..621d78d`). That one and the
review of `a198042`, recorded on the technical owner's instruction, are kept
under `earlier_delta_reviews`, and so is the review of `621d78d..06ee0cc`
itself since the record below replaced it.

The closing step (`06ee0cc..05298e9`) had no delta review. The technical
owner accepted it for the agreed success path, and the record holds that as
what it is: her bounded technical acceptance, quoted as the brief relays it
(Zulip Kitchen 2359), not an independent review. `delta_review` carries it
with `kind` saying so. There is no review report; the digest the readiness
check requires is the sha256 of the quotation, and the entry says that too.
The implementer recorded it on the manager's quoted instruction. It is kept
under `earlier_delta_reviews` since the record below replaced it.

The line after `05298e9` up to `e62b4cf` was read by five independent reviews
in a row, and a part of it by a sixth before them. `delta_review` now holds
the last one, an APPROVE with no findings that "approves the reviewed code
line `22caa8d..e62b4cf`, not a live-host deployment window", and lists all
six under `chain` with their verdicts, findings and report digests:

| Range | Verdict | Findings | Closed by |
| --- | --- | --- | --- |
| `1b4f3e9..04d7735` | APPROVE | two MEDIUM (test gaps), four LOW | `15ee4a1` and ruling D5, by the implementer's report; LOW 4 open |
| `05298e9..40378ef` | REJECT | H1; L1, L2 | the review of `40378ef..22caa8d` |
| `40378ef..22caa8d` | REJECT as submitted | M1; W1 | ruling D6; the reviews after it |
| `22caa8d..d454870` | REJECT | W1-R; the Git rewrite | the two reviews after it |
| `d454870..4ab9d38` | REJECT | the rest of W1-R | the review after it |
| `4ab9d38..e62b4cf` | APPROVE | none | |

So `scripts/check-port-ready` passes at the commit that records this, and
its one status stands for more than one review: no APPROVE names
`05298e9..22caa8d` by itself. Those commits were read, their reviews ended in
REJECT, and each finding is stated closed by a later review of the list. The
record says so in `kind` and `covers`, and the line the check prints carries
it. The implementer entered it on the manager's quoted instruction ("Extend
it honestly from these reviews; don't invent reviews. Record unreviewed parts
as unreviewed."). What no review covers is listed under `not_covered`: a live
host; the closing step `b4a9271`, `05298e9` (the technical owner's
acceptance, B11); the witness of `archive.L140.4` (B25); the parts of
`src/deploy.ts` the corpus does not run; `qualify`, mutants and the socket
check on `e62b4cf`; and every later commit.

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
| B11 | review | finding F4, built as D4: the numbers are approved and the third fix round is reviewed; the finding is ruled with residual risk, not fixed. Owed: a review of the closing step's own commit, on which `check-port-ready` blocks |
| B12 | ruling | open, nothing built: time is not bounded by D4. A name in a global pax header costs both engines a pass over it for every member; at the budgets about half an hour in the port and an hour in the oracle. Does not block use on trusted input |
| B13 | port | open, low value now: decode a metadata block into one flat text, so that a block costs once its text and not twice (at most about 32 MiB under the byte budget of 2^24) |
| B14 | whoever starts kitchen | open for units under 4 GiB only: there Node's default heap is below the 2,048 MiB the reader requires, and kitchen needs `--max-old-space-size=2048` on the command line of Node. At 4 GiB and more the default passes (measured). No launcher exists and the role was not changed; the obligation the review suggested (readiness red when a launcher lacks the flag) is not built |
| B15 | ruling, at catalog integration | known and not closed for the trusted-input version: the archive is held whole, the caller's data comes out of the same heap limit, further amplification routes were not searched for |
| B16 | readiness check | open, not fixed (LOW-1 of the delta review of the third fix round): `check-port-ready` reads a source file moved under `docs/` as a change of the notes, in a commit or in the working tree. The fix is `--no-renames` on both git calls, with a test case |
| B17 | port | open, not fixed in the reader (LOW-2 of the same review): the sparse budget counts numbers, not their size, and a map in a global header is converted anew at every expansion. Bounded by the default instead: at 2^16 numbers about 115 MiB, inside the measured peak. Fix it (share the integers of a global map) before raising the sparse budget |
| B18 | port | `catalog.ts`, and with it the catalog branches of `deploy.ts` and `cook.ts`. Until then an active catalog is refused. None of the five deployments activates it |
| B19 | port, before any use on input that is not ours | `deploy.ts` and `cook.ts` have no mutants and no hostile vectors; the inventory rows they own (plan section 4.1) are not marked; exception classes on wrongly typed values are not modelled throughout. They are not listed as ported |
| D6 | the adapter contract raised to `kitchen-cook-adapter/2`: the empty Bun cache of the build's own, and Git's transport for a local source repository, are part of the contract (the owner, poll 2535, after finding M1 of the delta review of `40378ef..22caa8d`) | the exception made under `/1` for the two cache names is withdrawn, since an empty cache can change which package versions enter a build. `/2` distinguishes the acquisition and pins nothing (B26 stays open). Every agent built under `/1` is built anew once, in a later window; no account was touched for this ruling. Received on 2026-11-16 through the dispatch brief |
| B20 | done (plan steps 4 and 5) | `verify_cook` has met a real cook checkout and cook has run under the TS engine, once, installed and not started, on a trial account (`scripts/rehearse.mjs`; "A rehearsal" above). The build is a case outside the repository, with the lock as cook wrote it, and both engines take it alike. Not done: a started agent, a later release on a real host, a release taken back there |
| B21 | corpus | cases for the branches no case reaches: rollback onto a previous release, a retired unit, a dropped secret file, a health command, an engine-rendered unit, connectors from descriptors, the ownership refusals |
| B22 | port, with the command line (plan phase 7) | there is no operational entry: `src/cli.ts` refuses. `src/engine.ts` is a library call; secrets on a descriptor, the receipt file and exit statuses are not built. `scripts/rehearse.mjs` is the runner of a rehearsal and nothing more: it lets the engine read the secrets file, and it installs without a start only |
| B23 | ruling (plan Q6) | the lock tool is `/usr/bin/flock`, undigested |
| B24 | closed | plan Q1, ruled as divergence D5: a kitchen names a build by its adapter contract. It still differs from the role's name, so taking an agent over is one build and one restart; the corpus shows both names and no longer makes them equal without saying so |
| B25 | review | obligation `archive.L140.4` is recorded as discharged by the implementer, with the witness in `test/unit/engine.test.js`. A review has to confirm it |
| B26 | ruling, with cook | cook's install is neither offline nor pinned beyond the two archives: Bun resolves the host's other packages at install time and saves a new lockfile (rehearsal finding). Decide whether a build must be reproducible from its request, and if so how the host's lockfile is kept or the packages are supplied |
| B27 | closed for new builds | builds of one account shared inodes through Bun's cache (rehearsal finding; H1 of the review of `05298e9..40378ef`). Ruled: a new build shares no file with a build that is there. The adapter gives Bun a cache of the build's own and refuses a build that shares a file with another (`src/isolation.ts`); the takeover runner measures every build that was there before and after a step. Not changed: builds made before this still share inodes with the account's cache and with each other, so another Bun install of that account, outside kitchen, can still set modes on them. A new build costs a download of every package (no cache is kept) and no disk beyond the build. The takeover runner's witness covers each measured build's own directory and, in `verify`, the requested build too; an entry it could not read makes it null, not true (finding W1 of the delta review), and so does a unit file or drop-in file it could not read and a property the service manager did not answer for (W1-R). A cache a killed run left is removed by the next cook run of that root, whatever its request; until then it stays, and nothing removes one where no build follows |
| B30 | with cook | cook's connector clone is `git clone URL TARGET` with no `--no-hardlinks` (cook `src/backends/host.ts`, the branch beside the host's own, which has it). The adapter works around it for a repository the recipe handed to cook names by a local path, through Git's configuration in cook's environment. Known limits, where a legitimate build is still refused with `E_COOK_FAILED` because its clone shares object files with another build's: any local repository of a recipe that refers to further recipes (the adapter reads the one recipe, and gives Git no rewrite where it does not know every repository, since a rewrite matches every path that begins with its own); a path with a line break; a Git that does not apply `insteadOf` to a path given to `clone` (seen to work with Git 2.43.0; no other version was tried, and no live host's Git was asked). The rewrite also changes how that repository is reached: Git's `file://` transport runs `upload-pack` in it, and a repository only a plain local clone could read would now fail, closed. The clean way is `--no-hardlinks` in cook's connector clone; then the rewrite can go, under a new contract number. No present agent is known to take this route: every source repository of the cases at hand is HTTPS, and only `host_repo` is local |
| B28 | port | the engine's preflight does not ask whether the unit verifier can run; under `install_only` it fails after cook when the user manager's runtime directory is not named |
| B29 | port | the adapter writes `cook-layout.json` with no mode of its own, as the oracle does, so its mode follows the caller's umask |

## Still open

- **A review of the closing step.** The closing step (the approved defaults
  of D4, the vectors and tests that follow from them, the split of the test
  commands; `b4a9271`, `05298e9`) has the technical owner's bounded
  acceptance and no independent review. The reviews recorded since start
  after it. `scripts/check-port-ready` is green on the record of the line up
  to `e62b4cf` (see "The line after `05298e9`" above for what that status
  stands for) and blocks again on any later commit that changes anything but
  `docs/` and the record of rulings.
- **What no review of the line covers**: `not_covered` of `delta_review` in
  `test/port-rulings.golden`.
- **Finding F4** is ruled as D4, not fixed. What D4 leaves of it is recorded
  as residual risk of the trusted-input version: the limits bound the
  success path, not what an archive built to be costly can do.
- **The precondition of ruling C1**: that the archives come from a trusted
  source and are digest-checked before they are parsed. It comes due with
  `src/catalog.ts`; `scripts/check-port-ready` turns red then until it is
  confirmed or dropped.
- **Time** (backlog B12), **a unit of less than 4 GiB** (backlog B14),
  **the routes not searched for** (backlog B15), **the rename the readiness
  check misses** (backlog B16) and **the size of sparse numbers** (backlog
  B17): open, and none blocks use on trusted input.
- A function without a `require`, `need` or `raise` has no oracle mutant:
  `credential_shaped`, `repository`, the errors module, the archive
  predicates, the clauses inside one-`raise` functions. The TS mutants for
  them carry new ids (`lock.credential.*`, `lock.repository.*`,
  `errors.closed.*`, `cook.provenance.*`, some `config.layers.*`); the oracle
  side has no counterpart to share an id with.
- Oracle runtime errors on malformed arguments are compared by class only and
  counted separately, in line with the open phase-1 question about crash-only
  evidence; the report gives the counts.
