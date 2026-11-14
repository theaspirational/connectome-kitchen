# connectome-kitchen — private incubation

Offline phase-0/1/2 seed at Python baseline `a49b5dc`. **Not deployable. Phase 1
is incomplete and its acceptance gate is red.** No engine port, acquisition,
remotes or publication are included. The only runtime imports are Node builtins
and in-tree modules; Python/PyYAML are development/oracle tools already present
on this machine. Nothing was installed.

## Layout and status

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
  while coverage gaps remain. Site-generated mutants are **not** a completed
  semantic enumeration of every named independent check in the plan.
- `scripts/inventory-sites`: globs all engine/adaptor files and checks all 74
  rows. Branch-only rows 14, 43–46 are unavailable on this baseline. The chosen
  line matcher finds 585 sites, seven reviewed helper exemptions, no uncovered
  sites (one less than the plan's reported 586; definition/count needs review).

## Offline commands

Use the supplied Node 22 test executable explicitly. Production metadata keeps
Node >=24. There is no offline `tsc`, so this seed uses ESM JS with JSDoc instead
of experimental stripping: no compiler/bundler or type-erasure dependency is
needed. `test/public-syntax.ts` also exercises the publication gate on TS syntax.

```
node --test test/unit/*.test.js
python3 -m unittest discover -s test/unit -p 'test_*.py'
python3 -m unittest discover -s oracle/tests
python3 scripts/inventory-sites
python3 scripts/verify-history.py --source /path/to/local/source
python3 scripts/check_public.py --range main --history-blobs
python3 scripts/build
python3 scripts/check_public.py test/public-syntax.ts dist/kitchen-scaffold.mjs
python3 scripts/check-module-ready
```

The last command intentionally fails. The build is only a publication smoke
fixture, **not** a kitchen artifact. The CLI scaffold refuses with exit 2.
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
Most legacy B/C vectors still need conversion/replay through the contract shim.
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
