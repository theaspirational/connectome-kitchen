# Vector extraction status

`canonical.jsonl` is the portable, committed subset extracted by `record.py`.
`extraction.golden` records counts and SHA-256 digests of the full extraction.
Full A/B/C records are in private ignored scratch, not publication-cleared:
function calls contain machine paths, opaque objects, and deliberately unsafe
negative fixtures. They must not be treated as independently replayable by default.

B3: `patch-dependent.golden` in the raw extraction lists 62 patched tests. A
named-fake registry is mandatory; unknown objects/fakes fail decoding. Current
B captures initial tarballs and initial/final manifests.
The generic tar scenario runner exists, but none of the 279 legacy scenarios is
an independent contract request; `scenario-classes.golden` classifies each one,
`scenario-calls.golden` holds the observed call counts and `Deployment`
commands per record, and `docs/phase1-unreachable.md` gives the reason per
record. Of the 60 recorded
C entries, 49 are reproduced through the unpatched adapter (`cli-replay.golden`)
and 11 cannot be produced by any contract request. `crash-only.golden` holds the
function-level refusal vectors of the separate crash-only category.
`guard-portable.golden` holds portable kind-A vectors that replace a
Python-only witness; it is built by `test/mutants/portable.py` from declared
inputs and is separate from `guard-negative.golden`, whose header pins its own
construction. `cli-replay.golden` names host values instead of hashing them
(`docs/phase1-replay-limits.md`) and is the same from any checkout root.

This limitation is explicit: no engine module can pass the module-complete
gate on the present harness.

`port.golden` and `port.trees.golden` belong to the TS port of `errors`,
`lock`, `inputs`, `archive` and `config`: hand-written inputs (`test/differential/port_seeds.py`)
with the outcome the oracle gave for each, written by `test/differential/ts_port.py build`.
A row names its file tree by digest; a string the publication gate would refuse
is stored in pieces (`$join`) and large inputs as a generator (`$generate`).
`port-divergences.golden` names every input on which the port deliberately
answers differently from the oracle, with both outcomes and the reason.
