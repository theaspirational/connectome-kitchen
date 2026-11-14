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

### What each recorded B scenario actually reaches

`test/vectors/scenario-classes.golden` classifies all 279 records by the entry
points their test method executed in process (from the recorder's line
coverage) and by whether the method patches engine internals. The status of
every record is unchanged: pending.

| Class | Records | Of these, patching internals | What stands between it and a contract request |
| --- | ---: | ---: | --- |
| `deployment-entry-point` (`Deployment.prepare` and/or `activate`) | 84 | 12 | Every one constructs `Deployment` with the fake adapter `tests/fixtures/cook.py` as `cook_command` and a fake `analyze_command`. Contract v1 has a closed `engines.cook` record and no key for either |
| `adapter-only` (`connectome_cook.adapt` without a deployment) | 15 | 12 | The adapter is an internal stage, not a contract command; 12 patch `verify_cook` or `command` |
| `spawn-lifecycle` (`Lifecycle.create`, `transition`, `status`) | 28 | 5 | `create` and `transition` have no command at this freeze; only `status` does |
| `spawn-plan` (`make_plan`) | 13 | 1 | Expressible as `spawn` `plan`/`dry-run` requests, but each method issues several requests against one snapshot |
| `publication-gate` (`test_public`) | 17 | 3 | Not engine behaviour; no contract command |
| `no-entry-point` | 122 | 26 | The method calls individual functions (kind-A material) or a subprocess; it never reaches a contract entry point in process |

So at most 125 of the 279 records (84 + 28 + 13) touch something a contract
command stands for, and none of those is a single request. The 279 denominator
counts unittest methods that own a scratch directory; it is not a count of
kind-B scenarios in the plan's sense.

Two further facts block a same-root replay on this checkout:

- **Fakes.** Plan 3.3 says both engines call the same fake tools, addressed
  through one fixed launcher path. Contract v1 replaces the free-form
  `cook_command` by `engines.cook = {entrypoint, runtime, bun, node}` and has
  no verifier key, so the recorded fake adapter and fake `systemd-analyze`
  cannot be named in a request. The C replay below shows the alternative that
  needs no contract change: the real adapter with a fake *runtime*. Scenarios
  built that way are new vectors, not replays of these recordings.
- **Ancestors.** The engine refuses a root with a group- or other-writable
  ancestor. Every directory of this checkout is group-writable, and the frozen
  suite passes only because it models the directories above its scratch root
  as root-owned 0755 in process (`tests/deploy_fixtures.py`, `safe_ancestors`).
  A second engine in another process cannot use that seam. A fixed scenario
  root therefore needs a location with safe ancestry outside the checkout, or
  an agreed harness seam.

Deciding the denominator, the fake-tool route and the scenario root is a plan
decision. Until then B stays 0/279.

## C audit: the 60 recorded C entries

All 60 are in-process `connectome_cook.main` calls under patched `adapt`:
2 fallback cases, 20 closed engine codes, 38 cook-code pass-through cases.
The three source tests are individually identified in `cli-obligations.golden`.
Their synthetic argv do not name readable standalone specs. Re-running those
argv without reconstructing the named fake does not reproduce the recording.
The other 576 entries in `cli.jsonl` are subprocess/tool captures, not
classified independent C requests. Spawn CLI traffic is among those captures.

### C replay through the unpatched adapter: 49 of 60

`test/differential/replay_c.py` replays these entries without patching the
engine, the adapter or the shim. One `apply` request (phase `prepare`) goes
through the shim; the shim starts the real `scripts/cook-adapter.py`; the
adapter verifies a detached checkout of the reviewed cook commit and runs the
cook command line. The one fake is the named `runtime` executable. Its mode
file makes it fail with a cook code, fail with none, exit without a lock, or
write a lock with or without the requested layout. Request edits produce the
archive, budget, adapter-input and catalog refusals.

A fixed projection turns the receipt back into adapter output: drop the
envelope and `phase`, restore the key order `error_code`, `msg`,
`cook_error_code`, write one line to stderr, status 1, empty stdout. An entry
counts as replayed only when both hold:

1. the projected bytes, placed in the record with the entry's own synthetic
   argv, test and function fields, hash to the committed `raw_sha256`;
2. calling the unpatched `adapt` on the same build fails with the kind of
   exception the frozen test injected: a `SafeError` with that code (and cook
   code), a `LockError`, or another exception.

| Recorded group | Entries | Replayed | How |
| --- | ---: | ---: | --- |
| cook code pass-through | 38 | 38 | fake runtime exits 1 with the code on stderr |
| closed engine codes | 20 | 9 | `E_COOK_FAILED`, three `E_ARCHIVE_*`, `E_BUDGETS_UNSUPPORTED`, `E_ADAPTER_INPUT`, `E_LOCK_STATE_PATH`, `E_LOCK_INVALID`, `E_CATALOG_INVALID` |
| fallbacks | 2 | 2 | no lock written (`LockError`); a connector `env` that is not a mapping (`AttributeError`) |

The other 11 engine-code entries cannot be produced at the adapter stage by any
contract request, so they stay pending:

- `E_UNIT_VERIFY`, `E_UNIT_ACTIVE`, `E_UNIT_EXCLUSIVE`, `E_ANCESTOR_WRITABLE`,
  `E_EXTERNAL_COMMAND`, `E_EXTERNAL_ACCESS`, `E_SYSTEMD_PATH`,
  `E_ENVIRONMENT_FILE`, `E_SECRETS_MODE`: raised only by deployment-engine code
  that the adapter never calls.
- `E_EXTERNAL_DATA_ARG`, `E_DEPLOY_FAILED`: the adapter can raise them only
  from `validate_config`, `resolve_pins` or `environment_names`, which the
  engine has already applied to the same request before it starts the adapter.

The frozen test reached those 11 only by patching `adapt`. They show that
`main` forwards any closed code; no request can make it happen. Whether they
leave the C denominator, or become a unit vector of `main`, is a plan decision.

`shim.core` is in the mutant set: `replay_c.py shim-mutants` edits `core`
between the engine call and the envelope in five ways (message, code, cook
code, phase, an extra key) and each edit breaks the replay.

### What the C golden hashes, and what it names

`cli-replay.golden` is compared byte for byte, so it must come out the same
from any checkout root and any interpreter path. Section "Exact residual
nondeterminism" above says why replacing a visible path is not enough: the
engine's request id hashes absolute paths, and the build directory is named
after it. The replay therefore treats four values as host values. Each is
replaced by a name before hashing, in every string of the request and of the
fake-tool log, and nothing else is replaced:

| Name | Value | Where it occurs |
| --- | --- | --- |
| `${PYTHON}` | the interpreter path, only as a whole string | `systemctl` argv of the request; the adapter command line |
| `${REPO}/` | the checkout root with its trailing slash | scenario paths, the fixture `systemctl.py`, `cook-adapter.py` |
| `${PLATFORM_DIGEST}` | SHA-256 of the host's `/bin/sh` | catalog scenario pins |
| `${BUILD_ID}` | the name of the one build directory the request created | tool-log argv only |

`${BUILD_ID}` is derived, so it is named only after a check. The harness
rebuilds the id's preimage, `{spec, cook}`, from the `deployment.json` the
engine wrote and the adapter command line the shim passes for the request's
`engines.cook`, and requires its SHA-256 to equal the directory name. If it
does not, or there is not exactly one build directory, the id keeps its literal
value, the entry is `not-reproduced`, and the golden goes stale. The preimage,
with the other three names applied, is committed as `build_identity_sha256`.
So the golden still pins what the id is a hash of (config, resolved pins and
the full adapter command line), and `fake_tool_log_sha256` still pins every
argv element of every fake-tool call: an archive digest, a second build
directory, a path outside the checkout or an extra argument all change it.

The literal build ids and the digest of the unnamed tool log are evidence about
one host, not something another checkout can reproduce. Each `run` writes them
to the ignored `.cache/replay-c-host.json` and never compares them.

`replay_c.py relocated` is the standing check for this property. It copies the
oracle, the harness and the two committed goldens to a new root, runs `run`
there through a symlinked interpreter, and passes only if that copy accepts the
goldens unchanged while the root, the interpreter path and every build id
differ from this checkout's.

Limits of this replay:

- It needs a local clone holding the reviewed cook commit (`--cook-source`).
  Nothing is fetched; without the clone the command exits 3 and counts nothing.
- The shim is called in process under the frozen suite's `safe_ancestors` seam,
  for the ancestor reason given in the B section. The adapter itself runs as a
  real, unpatched subprocess.
- The synthetic argv of the recording names no readable file. The replay does
  not reproduce it; it reproduces the output triple and binds it to the record
  by hash.

The existing successful validate and refused status projection tests remain
useful, but are not projection of these 60 entries. Injecting the recorded
answer as shim `core`, or treating a patched whole Deployment as the oracle,
would hide engine differences and is not a repair. The replay above follows
that rule: the fake sits at the runtime boundary, the projection is fixed, and
`shim.core` mutants break it.
