# Modules 1–5 in TypeScript: `errors`, `lock`, `inputs`, `archive`, `config`

Plan section 3.1, modules 1–5 (plan phase 3), ported from the frozen oracle at
`a49b5dc`. This note says how the port keeps the oracle's behaviour, what the
oracle does that the plan's divergence table (section 3.2) did not list, how
the lazy imports are kept, where the port deliberately differs from the oracle,
and which points are left for review. Counts are in the dispatch report, not
here.

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

| 12 tar | `archive_manifest` lists a `.tgz` and reads one member | `pylike/tarfile.ts`: the read path of the installed `tarfile` on the decompressed bytes (formats, pax and GNU extensions, sparse members, its termination rules). The gzip container is read strictly, which is a deliberate divergence, see below | `tar` and `targz` kinds in `port_primitives.py`; vectors `archive.manifest.*` |

Rows 4 (path sort), 11 (shlex) and 13–15 (YAML, `openat`, flock) do not occur
in these modules. The phase-0 `pylike/tar.ts` is a different thing: a strict
reader for extraction (row 16) that refuses what it does not know. A listing
that refused a member type the oracle merely skips would refuse archives the
oracle accepts, so `archive` does not use it.

## Oracle behaviour the plan did not list

Each item was found while porting, is reproduced by the port, and has vectors.
None was changed. Items marked **review** look unintended in the oracle.

1. **`re.I` is not JavaScript `/i`.** Under `re.I` a letter also matches what
   lowercases to it in CPython plus CPython's own equivalence list: U+0130 and
   U+0131 for `i`, U+017F for `s`, U+212A for `k`, inside classes as well.
   JavaScript's case folding does not include the two dotted/dotless `i`
   forms. No pattern of the port uses `/i`; the alternatives are spelled out.
   Mutants `lock.credential.ignore-case`, `.case-equivalents`,
   `.class-equivalents`.
2. **`json.loads` on bytes guesses the encoding** (**review**).
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
5. **Recursion bounds** (**review**). CPython refuses deep nesting with
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
12. **The oracle interpreter's `urlsplit` is not upstream 3.12.3** (**review**).
    The installed `urllib/parse.py` carries a distribution backport for
    bracketed hosts (`_check_bracketed_netloc`). `repository` therefore
    answers differently from upstream CPython 3.12.3 for URLs with stray
    brackets. `url.ts` follows the installed file, since that is what the
    recorded oracle ran; "CPython 3.12.3" alone does not pin this behaviour.
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
reason). None is reviewed yet; `scripts/check-port-ready` blocks on that.

**D1. The gzip container of an archive is read strictly** (`archive_manifest`,
archive.py:131-135; seven entries `gzip.*`).

- *Oracle.* `tarfile.open(path, 'r:gz')` reads the compressed stream lazily, in
  8 KiB steps, and only as far as the tar listing and the one manifest need.
  It never checks a CRC-32 or length trailer it does not reach, never sees
  bytes after the point where it stops, and lets a truncated stream escape as
  `EOFError` or `zlib.error`, which archive.py:138 does not name. So an archive
  with a wrong checksum, with junk after the stream or cut short behind the
  manifest is accepted, and one cut short earlier is a runtime error instead of
  a refusal.
- *Port.* `pylike/tarfile.ts` decompresses and checks the whole container
  first (every member header, the deflate data, CRC-32 and length; concatenated
  members and zero padding as `gzip.py` allows them) and reports any damage as
  an `OSError`, which `archive_manifest` turns into its closed refusal,
  "unreadable archive or manifest", code `E_ADAPTER_INPUT`.
- *Why not reproduce it.* Reproducing the oracle would mean modelling CPython's
  buffer sizes and read pattern to decide which damaged bytes it happens not to
  look at. The archive is an input the operator pinned by digest; an archive
  whose own integrity trailer is wrong is damaged whatever the pin says, and
  "not looked at" is not a property worth freezing into a contract.
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

Two smaller limits of the same reader, not divergences on any realistic
input: the decompressed stream is held in memory (the oracle streams), and
the recursion bound for a chain of several hundred pax or long-name headers is
exact for `archive_manifest` called directly, shifting with the caller's depth
as it does in the oracle.

## `config` and Ansible's `combine` (plan risk R6)

`config.py` merges layers with `ansible.plugins.filter.core.combine` when
ansible-core is importable and with a stand-in of its own otherwise, which its
docstring calls test-only. ansible-core is not installed on the machine all
vectors were produced on, so the oracle that was recorded and replayed is the
stand-in, and `config.ts` is a port of the stand-in. Whether real `combine`
agrees with it (list handling, copying, non-mapping operands) is exactly R6
and is still open: it needs a host with ansible-core. Until then the merge is
verified against the stand-in only.

## Open points for review

Nothing below was decided in this slice.

- The mutants that are not killed: checks no input can isolate because another
  check implies them, two the compiler enforces, one that only a runtime error
  distinguishes. `test/mutants/ts-unkilled.golden` has the analysis of each.
- A function without a `require`, `need` or `raise` has no oracle mutant:
  `credential_shaped`, `repository`, the errors module, the archive
  predicates, the clauses inside one-`raise` functions. The TS mutants for
  them carry new ids (`lock.credential.*`, `lock.repository.*`,
  `errors.closed.*`, `cook.provenance.*`, some `config.layers.*`); the oracle
  side has no counterpart to share an id with.
- Plan risk R6 (above).
- Items 2, 5 and 12 above: keep the oracle's behaviour, or refuse earlier in
  both engines by a reviewed waiver; for item 12, which `urlsplit` is the
  reference.
- `scripts/check-port-ready` stays red until these mutants and the deliberate
  divergences are ruled on.
- Oracle runtime errors on malformed arguments are compared by class only and
  counted separately, in line with the open phase-1 question about crash-only
  evidence; the report gives the counts.
