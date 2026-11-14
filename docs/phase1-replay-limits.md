# Phase-1 replay and determinism audit

This file describes deficits, not waivers or conformance approval. No engine
port is qualified. See the generated `test/mutants/phase1-summary.golden`.

## Independent negative vectors

`test/differential/guard_vectors.py` has an explicit API allowlist. It never
executes argv, downloads, or replays arbitrary recorded function names. It
perturbs successful inputs and independently records the frozen oracle's
answers. Only returns and explicit ValueError-family data refusals are vectors.
TypeError, KeyError, AttributeError, OSError, recursion, driver errors and
process timeouts do not kill. A valid mismatch on another independent vector
can kill even when a different vector crashes; the stored witness identifies
that valid mismatch, not the crash.

Three reconstructable fixture profiles are explicit in vector metadata:

- `lock-files-v1`: build marker substituted at invocation; fixed launcher,
  unit and recipe bytes in a worker-local fixture; returned paths substituted
  back. No arbitrary incoming file is restored. This is a filesystem fixture,
  not a claim that `map_lock` is pure.
- `identity-capabilities-v1`: the JSON descriptor becomes a named data-only
  capability fake, with no provider effects.
- `fixed-identity-clock-v1`: `now` is parsed into a datetime and passed as a
  library argument; the default is a fixed timezone-aware instant. No ambient
  clock is consulted by this profile.

Path-audit vectors require the unallocated `/fixture` namespace to be absent.
These functions inspect path resolution, not file contents; no directory is
created there. The absolute fixture namespace and worker-local lock profile
are not a substitute for B's sequential same-root requirement. Non-refusal
unit-isolation, sandbox and persistence effects have separate named tests.

The committed negative corpus is a witness-minimized subset. An exploratory
lock-fixture collision was found during minimization: the initial worker path
selected a shared parent, so concurrent unit-file truncation could produce a
false digest mismatch. Those lock results were discarded, the fixture was made
worker-local, and a regression test pins that isolation requirement. Each retained
mutation kill is rerun against that subset; the large exploratory input corpus
is ignored private scratch. Given the preserved `recording-v2/functions.jsonl`,
the builder's choices, order, clock and fake descriptors are deterministic.
This is not a claim that a *fresh raw extraction* is byte-reproducible.

## Exact residual nondeterminism in raw extraction

`record.py` leaves these sources unnormalised:

1. `tempfile.TemporaryDirectory` in legacy fixture setup selects random test
   roots. They enter A args/results, C/subprocess argv, B `root`/`request`,
   deployment request hashes and build-directory names, generated document
   bytes, `.git` object payloads where those bytes are committed, manifests'
   content hashes, and the initial tar's digest/name. Replacing a visible path
   after hashing cannot repair the derived hashes.
2. `spawn/identity.py:30-34` reads UTC time when `now` is omitted; lifecycle
   creation calls it without injection (`lifecycle.py:143`). Temporary identity
   names reach lifecycle/provider documents, A outcomes and B final hashes.
   `lifecycle.py:243-244` also stamps the seed receipt with current UTC time.
3. `spawn/identity.py:158` calls `uuid.uuid4()` for provider handles. Handles
   reach provider and lifecycle JSON, operation receipts, A outcomes and final
   manifests. The raw recorder does not inject a UUID generator.
4. The framework-archive test builder uses `tarfile.open(..., 'w:gz')` without
   fixing the gzip timestamp. Archive bytes, their SHA-256 values and records
   derived from those values can differ even with fixed fixture Git dates.
5. The recorder does not pin PYTHONHASHSEED. Legacy set iteration can change
   call order, first-refusal order, A dedup insertion order and resulting record
   order, even when each eventual canonical record is otherwise equal.
6. The interpreter/checkout/launcher absolute paths, uid/gid, Python version,
   account access checks, umask, platform executables and optional integration
   test availability are host prerequisites, not normalised transport fields.
   The recording header captures uid/gid/Python, not a portable host model.

What *is* fixed: recorder fixture Git author/committer dates, initial snapshot
TarInfo mtime/uid/gid/user/group names, gzip mtime on the recorder's own initial
snapshot archives, and manifest exclusion of mtimes/temp inode identifiers.
Those fixes do not remove time/UUID/path-derived *file contents*. Tar member
modes and symlink text intentionally remain observable.

## B audit: 279 recorded unittest scenarios, not 279 requests

| Legacy test module | Scenarios |
| --- | ---: |
| catalog | 36 |
| cook | 45 |
| deploy | 75 |
| external | 41 |
| operator_inputs | 10 |
| public | 17 |
| spawn | 21 |
| spawn_lifecycle | 34 |

The recorder's `request` is a tagged selection of unittest attributes, not a
contract envelope. The snapshot precedes the *test method*, not each prepare,
activate, lifecycle transition or mutated input within it. Method bodies can
perform several requests and patch internal functions/providers. Full named
fake reconstruction and per-request I/O/tool logs are absent. Publication
checks and direct lifecycle create/transition calls are not themselves
supported contract commands at this freeze. These records must be split and
reconstructed, not merely relabelled. `scenario-obligations.golden` accounts
for every record; none is silently counted as an independent replay.

## C audit: the 60 recorded C entries

All 60 are in-process `connectome_cook.main` calls under patched `adapt`:
2 fallback cases, 20 closed engine codes, 38 cook-code pass-through cases.
The three source tests are individually identified in `cli-obligations.golden`.
Their synthetic argv do not name readable standalone specs. Re-running those
argv without reconstructing the named fake does not reproduce the recording.
The other 576 entries in `cli.jsonl` are subprocess/tool captures, not
classified independent C requests. Spawn CLI traffic is among those captures.

The existing successful validate and refused status projection tests remain
useful, but are not projection of these 60 entries. Injecting the recorded
answer as shim `core`, or treating a patched whole Deployment as the oracle,
would hide engine differences and is not a repair. Each source fake must be
reconstructed at its actual boundary, with a fixed projection and shim.core
mutation coverage, before a C entry can be marked replayed.
