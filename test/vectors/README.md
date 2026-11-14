# Vector extraction status

`canonical.jsonl` is the portable, committed subset extracted by `record.py`.
`extraction.golden` records counts and SHA-256 digests of the full extraction.
Full A/B/C records are in private ignored scratch, not publication-cleared:
function calls contain machine paths, opaque objects, and deliberately unsafe
negative fixtures. They must not be treated as independently replayable by default.

B3: `patch-dependent.golden` in the raw extraction lists 62 patched tests. A
named-fake registry is mandatory; unknown objects/fakes fail decoding. Current
B captures initial tarballs and initial/final manifests.
The generic tar scenario runner exists, but converting all 279 legacy scenarios
into independent contract requests remains incomplete. CLI recordings retain
raw bytes; only the status-refusal fixed projection currently has a shim test.

This limitation is explicit: no engine module can pass the module-complete
gate on the present harness. No phase-3+ implementation is present.
