# a49 refusal-site count erratum

The plan's prose total **586 / 579 cited** is not reproducible at its stated
freeze. The reproducible correction is **585 / 578 cited**, plus the same
**seven helper bodies**. There is no additional unreviewed or uncited site.

`test/mutants/site-manifest.golden` enumerates every actual matching line, its
source text, row memberships, file digest and helper disposition. It totals
585 across all 26 globbed engine/adaptor files. The independent AST sweep
contains no need/require/unsafe call or raise on an unlisted line.

`python3 scripts/site-manifest --source /path/to/local/source` compares all
engine/boundary file bytes to original commit a49b5dc. The relocated launchers
and the later publication-gate script differ, but both original and relocated
versions contain **zero** matching sites; the command verifies that explicitly.
All files with sites match the original freeze byte for byte. Thus this is a
correction to the plan's reported arithmetic, not a missing file, mutated
oracle, branch-only guard or new exemption. The original plan text remains
preserved; no historical document or authorship is rewritten.

Rows 14 and 43–46 remain unavailable at a49. This correction does not waive any
of the 573 generated guard mutants, or any semantic named-check obligation.
