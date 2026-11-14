# connectome-kitchen — private incubation

Offline phase-0/1/2 seed at Python baseline `a49b5dc`. **Not deployable. Phase 1
is incomplete and its acceptance gate is red.** No engine port, acquisition,
remotes or publication are included. The only runtime imports are Node builtins
and in-tree modules; Python/PyYAML are development/oracle tools already present
on this machine. The TypeScript compiler and Node typings are development-only
pins, installed offline from sealed tarballs; there are still zero runtime
dependencies.

## Layout and status

- `src/`: TypeScript, `strict`. `tsc` emits ESM JavaScript and declarations to
  ignored `dist/`; `bin` and `exports` name `dist` only. The unit tests import
  the compiled output, never the sources.
- `src/pylike/`: typed strict JSON, Python encoders, Unicode tables, text,
  component-wise paths, limited regex helpers, shlex, URL split/unquote, bounded
  tar reader. USTAR and a conservative per-file PAX subset are supported;
  unsupported tar extensions refuse. YAML belongs to the later spawn phase.
- `src/io/`: Linux descriptor-pinned no-follow reads, private atomic writes,
  fsync and tree manifests; explicit platform-flock child runner; redacting
  child runner. The platform tool is injectable and still needs role-side pins.
- `CONTRACT.md`: v1 draft with N1 clarified. B1 and B2 retain their later owners.
- `oracle/`: history-preserved engine/tests/scripts; boundary references and
  fixture inputs are added in an ordinary commit. Python engine blobs are not
  ported or edited. The phase-2 history is filtered through `f0d40741`, with all retained blobs unchanged; the working oracle is then frozen back to `a49b5dc`. Working test fixtures use neutral noreply identities.
- `oracle/commit-map.golden`: all 46 main commit mappings through `f0d40741`. Tests resolve the
  two historical baselines through this map. `PORTING.md` marks public engine
  symbols oracle-only; it does not pretend they have JS equivalents.
- `test/differential/`: tagged recorder/decoder (B3), pure-A replay, tar scenario
  runner, CPython property generator. `oracle/shim/kitchen_v1.py` adapts plan,
  apply and baseline spawn; verify/recover/cutover are unavailable at this freeze.
- `test/mutants/`: exact-anchor guard-site mutation runners and results, plus
  named observable-effect mutants. `scripts/check-module-ready` refuses completion
  while coverage gaps remain. The 203 semantic names are now individually source-anchored; 113 have
  executable independent mutants and 69 are killed. Compound and uncovered
  obligations remain blocking. `phase1-summary.golden` is authoritative; the
  older `combined-summary.golden` preserves the previous slice baseline.
- `scripts/inventory-sites`: globs all engine/adaptor files and checks all 74
  rows. Branch-only rows 14, 43–46 are unavailable on this baseline. The chosen
  line matcher finds 585 sites, seven reviewed helper exemptions, no uncovered
  sites. The committed site manifest reproduces the same 585 against original
  a49 source; `docs/site-count-erratum.md` corrects the plan's 586/579 prose
  total to 585/578, plus the same seven helpers.

## Offline commands

Production metadata keeps Node >=24. The compiler is TypeScript 5.9.3 with the
Node 24.19.2 typings (and their dependency, undici-types 7.24.6), pinned by
sha512 in `package-lock.json`. Install them from a directory holding the three
sealed tarballs; no registry is contacted and no install script runs:

```
scripts/install-offline.sh /path/to/typescript-pins
npm run typecheck
npm run build
```

The installer also switches off npm's update notifier, audit and fund lookups;
`--offline` alone leaves the notifier free to query the registry on a fresh
cache. `scripts/check-offline-install.sh /path/to/typescript-pins` is the
regression check: it runs the installer in a scratch copy with a fresh cache and
an emptied environment under `strace -f -e trace=network`, and fails on any IPv4
or IPv6 socket call, loopback and DNS included.

`npm run build` deletes `dist/` and recompiles, so stale output cannot satisfy a
test; `npm test` builds first. `scripts/build` is a different thing: it adds the
older publication smoke fixture to `dist/` and must run after `npm run build`.
`test/public-syntax.ts` still exercises the publication gate on TS syntax, and
`npm run check:public:dist` applies the gate to every compiled file.
`scripts/unicode-tables.py` regenerates `src/pylike/unicode.ts`.

```
node --test test/unit/*.test.js
python3 -m unittest discover -s test/unit -p 'test_*.py'
python3 -m unittest discover -s oracle/tests
python3 scripts/inventory-sites
python3 scripts/verify-history.py --source /path/to/local/source
python3 scripts/check_public.py --range main --history-blobs
python3 scripts/build
python3 scripts/check_public.py test/public-syntax.ts dist/kitchen-scaffold.mjs
npm run check:public:dist
python3 scripts/check-module-ready
```

The last command intentionally fails. `scripts/build` is only a publication smoke
fixture, **not** a kitchen artifact; the compiled `dist/` is not one either. The CLI scaffold refuses with exit 2.
Operational CLI/schema/packaging work is phase 7, not part of this seed.

Extract against a **fresh local clone** of the original source at `a49b5dc`, with
its remotes removed. Keep that clone and all generated records under ignored
scratch. The unmodified historical tests need their source-layout fixtures.

```
python3 test/differential/record.py --source /path/to/scratch/baseline --out .cache/recording
python3 scripts/export-vectors.py .cache/recording
python3 test/differential/replay_a.py --modules oracle/module_utils --vectors .cache/recording/functions.jsonl
python3 test/mutants/oracle.py --source /path/to/scratch/baseline --recording .cache/recording --out .cache/mutations
python3 test/mutants/replay.py --anchors .cache/mutations/anchors.golden --vectors .cache/recording/functions.jsonl --results .cache/a-results.golden
python3 test/mutants/invisible.py
```

Recorder tests that patch internals are explicitly listed rather than silently
promoted to independent A vectors. Full raw records and initial tar snapshots
remain private ignored scratch; only the portable canonical subset is committed.
All 279 recorded B scenarios and all 60 recorded C entries remain pending
independent conversion/replay through the contract shim. The latter are patched
cook-error calls, not independently runnable CLI requests. The precise audit
and residual raw nondeterminism are in `docs/phase1-replay-limits.md`.
Tests and subprocess captures are not automatically conformance vectors.

For history rehearsal, clone locally into a new scratch repo, remove remotes,
check out `main` at `f0d40741`, then run `scripts/filter-history.py --repo <clone> --tip f0d40741`.
It refuses a dirty/already-filtered checkout, preserves metadata/topology, keeps
only the approved paths, and records `commit-map.json` beside the clone. The
source repository is never rewritten. In-flight branches were intentionally not
included because the coordinator explicitly requested main through f0d40741 for phase 2. The gate is based on f0d40741, while phases 0–1 remain a49b5dc.

## Publication and limitations

The generic gate scans the working tree and all unique historical text blobs,
not just commit metadata. A scratch denylist including historical author names
rejects preserved identities/content; there is no publication clearance and no
push. No private denylist is committed and diagnostics never print matched values.

W1: float literals overflowing to infinity, including `1E400`, refuse in both
JS and the oracle shim. Fuzzing is evidence, not proof of full Python float
formatting parity. Node 24 production runtime, actual platform tool digests,
real Ansible `combine`, cook execution, independent replay of every scenario,
complete named-check mutants, and review approval remain unverified/unmet.
No phase-3+ engine work has started.

## Phase-1 continuation

Guard kills increased from 165 to **296/573**. **277 remain unreviewed**; the
committed equivalence waiver list is deliberately empty. The 154 committed
negative vectors pass with zero mismatches/runtime errors and independently
kill 155 guard-site mutants; these overlap prior unit kills. Errors and timeouts
are never kills. The 151,885-case exploratory builder is byte-reproducible from
the preserved recording (also checked with a different Python hash seed), not
from a fresh legacy extraction. A fixture collision detected during minimization
was repaired and its exploratory results discarded before final counting.

Additional offline reproduction (the original `.cache/kitchen` is preserved
outside this nested repository):

```
python3 scripts/site-manifest --source /path/to/local/source
python3 test/differential/guard_vectors.py replay --vectors test/vectors/guard-negative.golden
python3 test/mutants/guard_vectors.py --anchors test/mutants/guard-anchors.golden --vectors test/vectors/guard-negative.golden --out .cache/guard-reproduction
python3 test/mutants/named_effects.py
python3 test/mutants/guard_vectors.py --anchors test/mutants/named-guard-anchors.golden --vectors test/vectors/guard-negative.golden --out .cache/named-reproduction
python3 test/mutants/named_checks.py
python3 scripts/summarize-phase1
python3 scripts/check-module-ready
```

To reconstruct the large exploratory corpus, use `guard_vectors.py build
--recording ../recording-v2/functions.jsonl --out .cache/exploratory.golden`.
For bounded legacy retries, derive covering tests from `coverage.golden`, then
run `test/mutants/extend.py --source ../baseline --anchors <anchors> --out
.cache/legacy-reproduction --max-trials 8`. The committed guard anchors retain
the original three candidates, not the full coverage-derived candidate list;
see the dispatch report for the exact preparation command.
