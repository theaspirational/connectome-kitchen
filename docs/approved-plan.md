# connectome-kitchen: moving the engine out of the Ansible role

Status: **proposal, plan only**. Nothing here authorizes creating a repository,
pushing, deploying, changing pins or touching a host. No code was written.

Revision 2, after the first review. The fail-closed inventory (section 4) went
from 47 to 74 rows, every row now has a mutant id, and the inventory has a
completeness check. Smaller changes: the oracle shim (3.3), the `plan`/`apply`
receipt shape (2.4), number kind (3.2), the kitchen pin and the Node bootstrap
(5.2), cook dependency cost (6.4), the `[T]` re-port and the estimate (8.2,
10.3), and a backlog (10.4).

## 0. Reading guide

**Refs.** An untagged `path:line` is this repository at `origin/main` `a49b5dc`.
Tagged citations:

| Tag | Ref | Review state (private ledger) |
| --- | --- | --- |
| `[S]` | `seamless-update-p1g` `7883806` | accepted with four LOW findings |
| `[T]` | `spawn-t5b-executor-r11` `5714021` | blocked at round 10 (no admitted cold start) |
| `[Z]` | `spawn-zulip-frontend-r4` `e393318` | approved, six LOW; based on an older `[T]` round |
| `[K]` | cook `1b874d9` (the commit `versions.yml:6` pins) | n/a |

All branch and cook content was read with `git show`/`git log` only; nothing was
checked out. A dagger (†) marks a line number taken from a delegated read-only
survey that I did not reopen myself. "Unverified" means exactly that.

**Names.** This tree is under the publication gate (`scripts/check_public.py`),
so adopters are anonymous here: **cohort 1** = our four agents (profiles A-D of
`docs/plans/unify-agents.md` on branch `plan-unify-agents`), **cohort 2** = the
two partner agents, **cohort 3** = the external fleet that runs its own Ansible.
The private dispatch report carries the mapping.

**Three findings that shape the whole plan**

1. **The first kitchen apply cannot be a no-op.** The release identity hashes
   the adapter argv: `request_id = sha256(canonical({'spec', 'cook':
   cook_command}))` (`roles/connectome_agent/module_utils/connectome_deploy.py:482`),
   and the build directory is named after it (`:484`). Replacing
   `scripts/cook-adapter.py` changes that argv, so every agent gets a new build
   directory, a real cook run and a unit restart on adoption. Each adoption is
   therefore an ordinary update window (section 9), not a silent engine swap.
2. **"Byte-identical" is a real constraint, not a slogan.** Python and
   JavaScript disagree on exactly the primitives the engine hashes and compares
   (section 3.2: float text, non-ASCII escaping, path ordering, `\w`/`\d`,
   `splitlines`, `$`). The harness has to be built before the first module.
3. **Importing cook as a library does not by itself remove the T5b blockers.**
   They are literals inside cook's generators at the pinned commit
   (section 6). What removes them is a cook change; the library seam is the
   vehicle for it, not the fix.

Module paths below are relative to `roles/connectome_agent/module_utils/`
unless they start with another directory. `deploy.py` means
`connectome_deploy.py`, and likewise for the other `connectome_*` modules;
`spawn/x.py` means `connectome_spawn/x.py`. `tasks/...` and `library/...` are
under `roles/connectome_agent/`.

## 1. Repository, package, licence, publication gate

### 1.1 What moves

At `a49b5dc` the engine is 5,132 lines of Python in 19 files†. It touches
Ansible in only three places:

- `deploy.py:13-28`, `archive.py:10-15`, `catalog.py:19-22`: `try: from
  ansible.module_utils ... except ImportError:` fallbacks to plain imports.
- `config.py:17-20`: uses Ansible's `combine` when present, else a local
  stand-in documented as "Test-only" (`config.py:5-6`).
- `library/connectome_release.py:3-5`: the only real Ansible boundary, 51 lines.

`cook.py` and `spawn/*` already import nothing from Ansible (`cook.py:15-20`,
`spawn/cli.py:1`). `scripts/connectome-spawn` and `scripts/cook-adapter.py` are
ten-line launchers that put `module_utils` on `sys.path`
(`scripts/connectome-spawn:7`, `scripts/cook-adapter.py:6`).

### 1.2 Layout

```
connectome-kitchen/
  package.json      name connectome-kitchen, type module, bin kitchen,
                    exports ".", "./contract"; engines.node >=24
  CONTRACT.md       normative CLI/JSON contract (section 2)
  PORTING.md        generated map: TS symbol -> Python file:line at freeze commit
  src/
    pylike/         json (3 canonical encoders + strict parser that keeps
                    number kind), path, text, regex helpers, shlex, urlsplit,
                    tar reader  (section 3.2)
    io/             fs-safe (lstat walk, no-follow open, atomic write, fsync),
                    flock, proc (redacting child runner)
    errors.ts lock.ts inputs.ts archive.ts config.ts catalog.ts
    cook.ts deploy.ts                       <- connectome_* modules, 1:1
    cutover.ts hostprobe.ts                 <- [S], after it lands
    spawn/                                  <- connectome_spawn/*, 1:1
    cli.ts          argv -> request -> receipt; no logic
  test/
    unit/           ported tests
    vectors/        goldens extracted from the Python suite (section 3.3)
    differential/   runner: Python oracle vs TS on the same vectors
    mutants/        guard mutants for both engines (section 3.4)
  oracle/           frozen Python engine, history preserved (section 7);
                    shim/ drives it through contract v1 (section 3.3)
  scripts/          check_public, inventory-sites (4.3), build, pack,
                    verify-artifact
```

One Python module maps to one TS module with the same name and the same public
function names. That keeps review a side-by-side read and makes `PORTING.md`
mechanical.

**Runtime.** Node, not Bun, executes kitchen in production: the role already
makes pinned Node the trust anchor for every unit start (`catalog.py:801-805`
builds `node --eval EXEC_ENV`; `catalog.py:111` requires Node v24;
`versions.yml:45-49` pins 24.14.0). Bun stays what it is today, an input to
cook and the host. Tests may run under either.

**Dependencies.** The Python engine is "Standard library only"
(`deploy.py:1`, `cook.py:1`); the one exception is PyYAML in spawn
(`pyproject.toml:10`, `spawn/data.py:7`). Kitchen keeps that property: zero
runtime npm dependencies, a small in-tree tar reader, and the YAML decision in
section 10 (Q5). The artifact is then a single bundled file whose sha256 is the
pin (section 5.2).

### 1.3 Package name and registry

`connectome-kitchen`, unscoped, mirroring cook (`[K] package.json` name
`connectome-cook`). Recommendation: make it npm-*shaped* (installable from a
tarball or a git ref, `bin` and `exports` declared) but **do not publish to the
registry in the first release**. Every existing input is a local, digest-pinned
file with "No acquisition" as the stated rule (`catalog.py:1`, `archive.py:1`);
a registry dependency would be the first exception. Reserving the name is an
owner call (Q2).

### 1.4 Public or private

Recommendation: **public, after a private incubation that ends at the first
green differential run**. Reasons:

- Cohort 3 does not use our role; what they consume is the artifact plus the
  contract, and they cannot be given our private repository.
- The code is already public here, under the same gate.
- The private side keeps everything that is private today: denylist,
  inventory, pin overrides, names.

Incubate privately because the filtered history carries 36 commit messages and
identities that must pass `check_public --range` first (`docs/publication.md:5-8`),
and because the gate needs changes before it fits a TS tree (1.6).

### 1.5 Licence

This repository is MIT (`LICENSE:1`). Cook is Apache-2.0 (`[K] package.json`
licence field). The sibling packages I could read locally declare MIT
(agent-framework, context-manager) or nothing (connectome-host has no licence
field; unverified whether that is intended).

Recommendation: **MIT**, because the history-preserved code is MIT. Keep cook
a separately pinned package and do not vendor its source into kitchen, so no
Apache notice obligations enter the tree. If the owner prefers one licence
across the toolchain, relicensing to Apache-2.0 is possible while there is a
single author (36 of 36 commits; three carry a co-author trailer); that is Q3.

### 1.6 `check_public` coverage

The gate stays, copied with its history. Facts and required changes:

| Item | Today | Needed for kitchen |
| --- | --- | --- |
| Baseline | `python3 scripts/check_public.py` **fails on `a49b5dc`**: the e-mail rule rejects `tests/test_spawn.py:111` and `tests/test_spawn_lifecycle.py:104,105,108,124,125,130` (run in this worktree, generic rules only) | Fix before filtering history. `[Z]`'s tip commit title says it reworks fixture e-mails; unverified whether it covers these lines |
| Multiline exceptions | `MULTILINE_SCALARS` names two nodes in `roles/connectome_agent/tasks/main.yml` (`scripts/check_public.py:80-85`) | Stays here; empty set in kitchen |
| Repository allowlist | `ALLOWED_REPOS` (`:17-23`) has neither this repository nor kitchen | Add kitchen before any document links to it |
| Config policy | `.json`/`.yml` scalars may not contain a backslash or be multiline (`:86`, `:104-110`) | Golden vectors contain escapes; store them as `.jsonl`/`.golden` or in tar files, never as `.json` |
| Scan scope | tracked + untracked-unignored files (`:195`) | Also scan the **built artifact**: bundlers embed absolute build paths, which the private-path rule (`:42`) would catch only if the bundle is scanned |
| Heuristics | IPv6 and e-mail rules are tuned on Python/YAML (`:16`, `:44`) | Dry-run on the first TS files in phase 0; `name` + at-sign + `version` strings and `hh:mm:ss` literals trip them |
| New metadata | none | `package.json` `repository`/`author`/`bugs`, lockfile |

`check_public.py` itself stays Python for now: it is a development tool, not
part of the artifact, and Python is needed for the oracle anyway until phase 8.

## 2. The versioned CLI/JSON contract

### 2.1 Shape

```
kitchen <command> --input <absolute.json> [--secrets-fd 0] [--receipt <absolute path>]
```

- No other flags, no positional data, no environment defaults. This keeps the
  existing rule of the spawn CLI ("No Ansible imports, environment defaults or
  writes", `spawn/cli.py:1`) and the adapter's five-name environment allowlist
  (`cook.py:23`, `cook.py:60-63`).
- **Secrets never appear in argv, the input file or the receipt.** Today they
  arrive as a `no_log` module parameter (`library/connectome_release.py:20`) or
  from the target-side file (`deploy.py:564-570`). In the CLI they arrive as one
  JSON object on a file descriptor, and must be exactly the declared names
  (`deploy.py:1226-1232`).
- stdout carries exactly one receipt line. stderr carries nothing a caller may
  parse and never raw tool output (`deploy.py:380-398`, `cook.py:42-57`).
- `--receipt` additionally writes the same bytes atomically with mode 0600;
  `[T]` already fetches such a file to the controller
  (`[T] roles/connectome_agent/tasks/spawn_entry.yml:25-33`).

Exit status: `0` success (changed or not), `1` engine refusal or failure with
an error receipt, `2` unusable invocation or contract. This matches the adapter
(`cook.py:382` exits 2 for argument errors, `cook.py:393-397` exit 1) and
changes the spawn CLI, which uses 2 for every refusal (`spawn/cli.py:63-68`).

### 2.2 Envelopes

Request:

```
{ "contract": "connectome.kitchen/v1",
  "command":  "apply",
  "request":  { ...command-specific, closed set of keys... } }
```

Receipt:

```
{ "contract": "connectome.kitchen/v1",
  "command":  "apply",
  "core":     { ...exactly today's result or error dictionary... },
  "meta":     { "kitchen": "<version>", "artifact_sha256": "<hex or null>" } }
```

`core` is the differential surface: Python oracle and TS must produce the same
canonical bytes for it. `meta` is never compared and never hashed. `core` is a
closed schema per command; `meta` may grow.

### 2.3 Commands, derived from today's entry points

| Command | Today | Mutates | Request keys |
| --- | --- | --- | --- |
| `plan` | `Deployment(check=True)`; `prepare()` then `activate()` (`deploy.py:767-768`, `:1233-1234`, `:1252-1253`). No lock is taken in check mode (`library/connectome_release.py:30-31`) | nothing | `base`, `root`, `unit_dir`, `layers` or `config`, `versions`, `engines`, `systemctl` |
| `apply` | `prepare()` + `activate(secrets)` under the per-agent flock (`library/connectome_release.py:32-39`); `phase` selects `prepare`, `activate` or both | builds, units, pointer | as `plan` + `operator_files`, `phase`; secrets on the fd |
| `verify` | no single entry point on main. Composed from the read-only `_activation_plan()` (`deploy.py:1188-1215`), `_lock()` (`:687-747`), `catalog.verify` (`catalog.py:684-735`) and the install-only health branch (`deploy.py:897-914`). `[T]` adds `health`/`mcp` operations (`[T] roles/connectome_agent/tasks/engine.yml:76-88`) | nothing | as `plan` + `build` (`requested` or `current`) |
| `recover` | spawn `recover`/`reconcile` (`[S] spawn/cli.py:36`†), `abort` (`[T] spawn/cli.py:68`); deploy-level `cutover.reconciled` (`[S] inputs.py:136-148`, `[S] deploy.py:1039-1066`) | bookkeeping only | `target` (`deploy` or `spawn`), `operation`, `reason`, `observed`, `receipt_sha256` |
| `spawn` | `validate`, `plan`, `dry-run`, `status` (`spawn/cli.py:20`); `[T]` adds `apply`, `verify` | per sub-command | `action`, `template`, `instance`, `catalog`, `stores`, `observed`, `seed`, `name`, `state_root`, `agent` |
| `cutover` | `[S]`: prestage with `activation: prepare_only` (`[S] deploy.py:1461-1472`), then apply under a cutover policy (`[S] inputs.py:68-134`) | units, pointer, `state/cutover.json` | as `apply` + required `cutover` policy |

`engines` replaces the free-form `cook_command` argv
(`roles/connectome_agent/defaults/main.yml:10-12`): a closed record
`{cook: {entrypoint, runtime, bun, node}}`, which is what the adapter parses out
of that argv today (`cook.py:387-391`).

### 2.4 Receipt `core` today (to be frozen as v1)

| Outcome | Keys | Source |
| --- | --- | --- |
| prepare, reuse | `changed:false`, `build_dir`, `secret_names`, `plan` | `deploy.py:762` |
| prepare, check | `changed:true`, same keys | `deploy.py:768` |
| prepare, built | `changed:true`, same keys | `deploy.py:816` |
| `plan` object | `state_dir`, `secrets_file`, `budgets`; optional `activation`, `exclusive_with`, `external_data_args`, `recipe_file`, `secret_files` (with `path`), `framework_archive`, `host_dependencies` | `deploy.py:586-617` |
| activate, check | `changed` only | `deploy.py:1234`, `:1253` |
| activate, no-op | `changed:false` + status keys | `deploy.py:1260` |
| activate, done | `changed:true`, `build_dir`, `build_id` + status keys | `deploy.py:1301` |
| status keys | `external_data_args`; for install-only: `activation`, `unit_state`, `exclusive_with[].state` | `deploy.py:678-685` |
| `[S]` additions | `activation:'prepare_only'`, `prestaged`; `cutover` on success | `[S] deploy.py:1461-1472`, `:1733`† |
| error | `error_code`, `msg`; optional `cook_error_code`, `unit`+`scope`, `ancestor_path`, `external_server` | `errors.py:68-74`, `deploy.py:42-100` |
| spawn validate | `valid`, `templateHash`, `instanceHash`, `catalogResolved` | `spawn/cli.py:50-51` |
| spawn plan | `plan`, `planHash`, `idempotenceKey`; `dry-run` adds `wouldWrite` | `spawn/planner.py:186-210`, `spawn/cli.py:59-60` |
| spawn status | persisted state + `recovery[]`, `identityNotice` | `spawn/lifecycle.py:99-115` |
| spawn refusal | `error:'spawn input refused'`, `filesWritten:false`, optional `migration` | `spawn/cli.py:64-67` |

`msg` is part of `core` and is closed text: the fixed message plus, for three
classes, a bounded suffix (`deploy.py:42-45`, `:55-58`, `:67-70`).

**`core` of `plan` and `apply` (v1, decided here).** Today prepare and activate
are two module calls that return two separate dictionaries (rows above). A
combined command needs one shape. v1 nests them unchanged:

```
success   { "changed":  <prepare.changed or activate.changed>,
            "prepare":  { ...today's prepare dictionary... },
            "activate": { ...today's activate dictionary... } }

failure   { "error_code": ..., "msg": ..., ...today's optional error keys...,
            "phase":   "prepare" | "activate",
            "prepare": { ... } }     only when prepare had already succeeded
```

- `phase: prepare` omits `activate`; `phase: activate` omits `prepare`. `plan`
  is the same shape computed in check mode, so its `activate` holds `changed`
  only (`deploy.py:1234`, `:1253`).
- The two inner dictionaries are byte for byte what the Python calls return.
  Nothing is merged or renamed, so each is compared against one oracle call.
- A failure after a successful prepare keeps `prepare`, because a build was
  created and the caller must see `build_dir` and `changed`.
- The new keys are `phase` and the two wrapper keys. They are closed.
- `secret_names` is read from `prepare` of a prior `plan`, as the role does
  today between its two tasks (`tasks/main.yml:44-52`).

Documents the engine writes, which are also compared byte for byte:

| File | Schema marker | Writer |
| --- | --- | --- |
| `builds/<id>/deployment.json` | canonical `{config, pins}` | `deploy.py:789` |
| `builds/<id>/.prepared` | request id | `deploy.py:809` |
| `state/ownership.json` | `version: 1` | `deploy.py:1057-1059` |
| `state/activation` | literal `install_only` line | `deploy.py:1275` |
| `builds/<id>/cook-recipe.json`, `cook-layout.json` | `connectome.host-layout/v1` | `cook.py:282`, `:287-293` |
| `builds/<id>/framework.lock.json` | `connectome.framework-archive/v1` | `cook.py:276`, `archive.py:186` |
| `builds/<id>/catalog.lock.json` | `connectome.catalog-lock/v1` | `catalog.py:650-654` |
| `builds/<id>/connectome.lock` (provenance rewrite) | cook lock v2 | `cook.py:362-368` |
| unit drop-in with `EXEC_ENV` | text | `catalog.py:784-798`, `:814-855` |
| secrets environment file | `NAME="value"` lines | `deploy.py:1243-1244` |
| `<agent>/lifecycle.json` | `connectome.spawn-state/v1` | `spawn/lifecycle.py:141-147` |
| `[S] state/cutover.json` | `connectome.cutover-receipt/v2` | `[S] cutover.py:28`, `[S] deploy.py:1105` |
| `[T]` role receipt | `connectome.spawn-role-receipt/v1` | `[T] connectome_spawn_role.py:98-105` |

### 2.5 Stable error codes

**Engine codes on main (20, `errors.py:5-26`):** `E_UNIT_VERIFY`,
`E_CATALOG_INVALID`, `E_UNIT_ACTIVE`, `E_UNIT_EXCLUSIVE`, `E_ANCESTOR_WRITABLE`,
`E_EXTERNAL_COMMAND`, `E_EXTERNAL_ACCESS`, `E_EXTERNAL_DATA_ARG`,
`E_SYSTEMD_PATH`, `E_ENVIRONMENT_FILE`, `E_ARCHIVE_GITHEAD`,
`E_ARCHIVE_IDENTITY`, `E_ARCHIVE_HASH`, `E_ADAPTER_INPUT`, `E_LOCK_INVALID`,
`E_LOCK_STATE_PATH`, `E_SECRETS_MODE`, `E_BUDGETS_UNSUPPORTED`, `E_COOK_FAILED`,
`E_DEPLOY_FAILED`. Any unknown code collapses to `E_DEPLOY_FAILED`
(`errors.py:69`).

**Added by `[S]` (9, diff of `errors.py`):** `E_NOT_PRESTAGED`, `E_NOT_IDLE`,
`E_IDLE_PROBE`, `E_HOST_PROBE_UNAVAILABLE`, `E_NOT_READY`, `E_UNIT_TRANSITION`,
`E_INSTANCE_CHANGED`, `E_INTENT_NOT_RECORDED`, `E_RECONCILE_REQUIRED`.

**Cook pass-through (38, `errors.py:29-44`), reported only as
`cook_error_code` under `E_COOK_FAILED`:** `E_HOST_LAYOUT`, `E_HOST_UNIT`;
`E_PRIVATE_` + `ANCESTOR`, `FILE`, `LINK_OWNER`, `MODE`, `OWNER`, `PATH`,
`UNSUPPORTED`; `E_HOST_DEPENDENCY_` + `ARCHIVE`, `DUPLICATE`, `FILE`, `HASH`,
`NAME`, `OPTION`, `OVERLAP`, `POLICY_FIELDS`, `RANGE`, `REINSTALL`,
`RESOLUTION`, `SOURCE`, `STORAGE`, `TRUSTED`, `UNDECLARED`, `VERSION`;
`E_HOST_ENGINE_` + `CHANGED`, `CHECKOUT`, `CLONE`, `DEPENDENCY`, `EXEC`,
`OBJECT_FORMAT`, `PACKAGE`, `PATH`, `PLAN`, `RANGE`, `REQUIREMENT`, `RESERVED`,
`VERSION`.

**New in the contract (2):** `E_CONTRACT` (unsupported contract major or
malformed envelope, exit 2) and `E_SPAWN_REFUSED` as the code for today's
value-free spawn refusal, whose `error` text is kept unchanged.

Rules that must survive: codes are a closed set; no free text from a tool,
exception or input crosses the boundary (`errors.py:77-97`); only the three
suffix classes above extend `msg`.

### 2.6 Versioning

- `connectome.kitchen/v<major>`. Removing or renaming a `core` key, changing
  the bytes of an emitted document, or changing an error code's meaning is a
  major bump. New optional request keys and new `meta` keys are minor.
- Kitchen refuses an unknown major with `E_CONTRACT`; it never guesses.
- On-disk schema markers (table in 2.4) version independently. Kitchen N reads
  what N-1 and the frozen Python engine wrote and never migrates silently.
- A `compilerVersion` integer (spawn already has one, `spawn/planner.py:186`)
  is bumped whenever emitted recipe or unit bytes change, and enters the
  release identity so that such a change shows up as an explicit rebuild in
  `plan` (Q1 decides whether the artifact digest enters it too).

## 3. Port order and the differential harness

### 3.1 Order

Import order on main, read from the files:

| # | Module (lines) | Depends on | Notes |
| --- | --- | --- | --- |
| 0 | `pylike/*`, `io/*` (new) | nothing | section 3.2; gate for everything else |
| 1 | `errors` (97) | none | closed tables; `cook_code_from_stream` window `errors.py:55-65` |
| 2 | `lock` (333) | lazy `inputs`, `archive` (`lock.py:149-152`, `:256-258`, `:282-284`) | owns `credential_shaped`, `repository`, `names_only` |
| 3 | `inputs` (162) | none | controller/target split in 5.1 |
| 4 | `archive` (244) | `errors`, `lock` | first tar consumer (`archive.py:131-135`) |
| 5 | `config` (72) | optional Ansible `combine` | needs a differential against real `combine` (R6) |
| 6 | `catalog` (855) | `archive`, lazy `lock.repository` | audits, seal, `EXEC_ENV` text |
| 7 | `deploy` (1301) | `inputs`, `catalog`, `lock`, `errors`, `archive` | transaction, ownership, rollback |
| 8 | `cook` adapter (397) | all of the above | cook stays a subprocess here (section 6) |
| 9 | `spawn` core (1671 in 11 files) | PyYAML, git | independent of 1-8 on main |
| 10 | `cutover`, `hostprobe` `[S]` | `deploy`, `inputs` | after `[S]` is on main |
| 11 | spawn execution, providers, real executor `[T]`; bot `[Z]` | 7-10, Zulip HTTP | last; see section 8 |

Steps 1-6 can start before the branches land, but not all of them stay
untouched. Measured with `git diff --stat a49b5dc <tip>` on `module_utils`:

| Module | `[S]` | `[T]` |
| --- | --- | --- |
| `errors`, `lock`, `archive`, `config` | `errors` gains nine codes | unchanged |
| `inputs` | changed (cutover policy) | +53 lines |
| `catalog` | unchanged | **+433 lines** (build normalization, engine links) |
| `cook` | unchanged | +11 lines |
| `deploy` | changed | +30 lines |

So `catalog` is ported in phase 4 from `a49b5dc` and `[T]` then changes it by
half its size. That delta is planned, not ignored: phase 11a re-ports it at
freeze-2 (8.2 step 5, 10.3). If the cook seam of phase 10 lands first and the
Python normalization is removed before freeze-2, the delta shrinks to whatever
is left; that is decided at the freeze, not assumed here.

### 3.2 Where Python and JavaScript diverge on bytes

Each row is a place where a naive port produces different receipts, hashes or
refusals. `pylike/` exists to make each a tested, single-owner function.
Rows 1, 2, 4, 5, 6, 8 and 15 were confirmed with one-liners on both CPython
3.12.3 and Node 22.23.3 in this session; the other rows are read from the code
and language documentation, not executed. The pinned Node 24.14.0 and Bun
1.4.4 were not available.

| # | Python behaviour | Used at | JavaScript default |
| --- | --- | --- | --- |
| 1 | `json.dumps(1.0)` is `1.0`; `1e16` is `1e+16`; `1e-05` | `deploy.py:309-310` feeding the request id (`:482`); spawn ratios are float (`spawn/schema.py:101`) | `1`, `10000000000000000`, `0.00001` |
| 2 | `ensure_ascii=True`: each non-ASCII character becomes a six-character backslash-u escape | `deploy.py:310`, `catalog.py:35`, `cook.py:276/282/293/368`, `spawn/cli.py:41` | raw UTF-8 |
| 3 | `ensure_ascii=False` | `spawn/data.py:107`, `spawn/cli.py:61` | matches, but key order is by code point in Python, UTF-16 unit in JS |
| 4 | `sorted()` of `Path` objects compares component lists: `/a/b` before `/a-b` | `deploy.py:1059` (ownership manifest order) | string sort puts `/a-b` first |
| 5 | `\d` matches any Unicode digit, `\w` any Unicode letter, in `str` patterns | `lock.py:40-46`, `catalog.py:95`, `spawn/catalog.py:14`, `spawn/schema.py:157` | ASCII only |
| 6 | `$` also matches before a final newline; `splitlines()` splits on eleven separators | `deploy.py:108`; `deploy.py:1094`, `catalog.py:489`, `:529` | end only; `split('\n')` |
| 7 | Arbitrary-precision integers | any config number | lossy above 2^53 |
| 8 | `json.loads` hooks refuse duplicate keys and non-finite numbers | `lock.py:315-333`, `inputs.py:102-117`, `spawn/data.py:48-53`, `:78-80` | `JSON.parse` keeps the last duplicate |
| 9 | `str(Path(v))` collapses `//` and `.` but keeps `..`; `resolve()`, `is_relative_to` | `inputs.py:20-23`, `lock.py:101-104`, `deploy.py:365-377` | `path.normalize` folds `..` |
| 10 | `urlsplit`/`unquote`, `.port` raising on bad syntax | `lock.py:49-76`, `archive.py:38-48` | WHATWG `URL` normalizes and accepts different inputs |
| 11 | `shlex.split`/`shlex.join` | `catalog.py:477-483`, `cook.py:102`, `spawn/planner.py:112` | none built in |
| 12 | `tarfile` (`r:gz`, `r:*`), member typing, size accounting | `archive.py:131-135`, `catalog.py:581-607` | none built in |
| 13 | PyYAML SafeLoader (YAML 1.1 scalars) with tags, aliases, merges refused | `spawn/data.py:56-97`; `spawn/catalog.py:91-106` reads `versions.yml` | common JS YAML libraries default to 1.2 |
| 14 | `os.open(..., dir_fd=)` with `O_NOFOLLOW` per component, `pass_fds` to git | `spawn/sources.py:39-67`, `:83-105` | no `openat`; emulate through `/proc/self/fd/N/name`, which the Python code already relies on (`spawn/sources.py:57`, `:91`, `:97`) |
| 15 | `fcntl.flock` | `library/connectome_release.py:37-39`, `spawn/persistence.py:64-73` | Node 22.23.3 has no `fs.flock` (checked); section 10 Q6 |
| 16 | `str.strip()`, `isspace()`, `isdigit()` Unicode sets | `deploy.py:1095`, `lock.py:58`, `catalog.py:546` | `trim()` strips a different set |
| 17 | Exception class decides the code: `RecursionError`, `UnicodeError`, `KeyError`, `TypeError` are caught and collapsed | `lock.py:330-333`, `catalog.py:732-735`, `spawn/data.py:96-97` | stack overflow is a `RangeError`; a missing key is `undefined`, not an exception |

Row 17 is the quiet one: many Python guards are "any exception here is a
refusal". The TS port must make each such block refuse by construction (typed
decoders), not by catching whatever the runtime throws.

**Number kind is part of the value (rows 1 and 7).** `JSON.parse` cannot be
used anywhere a value may later be hashed or re-emitted: it turns `1.0` into
`1`, and the request id is a hash over text in which Python writes `1.0`
(`deploy.py:309-310`, `:482`). The strict parser in `pylike/json` therefore
returns typed numbers: an integer literal becomes an arbitrary-precision
integer, and a literal with a fraction or an exponent becomes a float that
remembers it is a float. The three encoders print floats the way Python's
`repr` does. Checked here on CPython 3.12.3 against Node 22.23.3:

| Literal | Python reads, then writes | `JSON.parse` then `stringify` |
| --- | --- | --- |
| `1.0` | float, `1.0` | `1` |
| `1e2` | float, `100.0` | `100` |
| `1e16` | float, `1e+16` | `10000000000000000` |
| `0.1e-6` | float, `1e-07` | `1e-7` |
| `-0.0` | float, `-0.0` | `0` |
| `12345678901234567890123` | integer, exact | `1.2345678901234568e+22` |
| `1E400` | float infinity, written as `Infinity` | `null` |

The last row is an oracle oddity, not a goal: Python's reader overflows to
infinity without calling the non-finite hook, and `deploy.py:310` then emits
`Infinity` because it does not set `allow_nan=False`. Kitchen refuses such a
literal at the envelope with `E_CONTRACT`, and the oracle shim (3.3) refuses it
the same way, so the two stay equal. This is the first named waiver.

### 3.3 Goldens: what exists and how vectors are extracted

What the suite is today (survey†, counts by `ast`): 12 files, 294 test methods,
stdlib `unittest`, 1,363 assertions. Run in this session on `a49b5dc`, in a
neutral scratch clone: 294 tests, OK, 12 skipped, 68 s. Two facts matter:

- **No test compares against a committed golden file.** Only 17 assertions
  compare a whole dictionary. The suite is mostly property checks and 350
  `assertRaises`/`assertRaisesRegex` refusals.
- **The repository already has the differential idiom.**
  `tests/test_compatibility.py:15` extracts the engine at baseline `64d0e3a`
  with `git show` (`:72-83`) and asserts current output is byte-identical;
  `tests/test_catalog.py:946` does the same against `0ec19aa`. The kitchen
  harness generalizes this from "old Python vs new Python" to "Python vs TS".

Extraction, in three vector kinds:

| Kind | Content | How it is extracted |
| --- | --- | --- |
| A. Function vectors | `(module.function, JSON args)` -> `(JSON result)` or `(exception class, code, message)` | Run the existing suite once under a recorder that wraps every public function of each module and logs each call. Deduplicate by digest. Refusal tests become refusal vectors automatically, including the fuzz loops (`tests/test_cook.py:98-113`, `:170-234`†; `tests/test_spawn.py:184-294`†) |
| B. Scenario vectors | initial tree (tar), request JSON, fake tools, then: receipt `core` bytes, final tree manifest (path, kind, mode, sha256, link text), fake-tool call logs | Each engine-level test (`test_deploy`, `test_external`, `test_operator_inputs`, `test_catalog`) is re-run with a snapshot hook at the end; `tests/test_deploy.py:1124-1130`† already has a whole-tree `snapshot()` helper |
| C. CLI vectors | argv + stdin -> stdout, stderr, exit status | from the subprocess calls in `test_errors`, `test_spawn`, `test_spawn_lifecycle`, `test_cook` |

**The oracle is driven through a contract-v1 shim.** Kind-C vectors are bytes
of today's CLIs, and kitchen changes those bytes on purpose: every result gains
the envelope, spawn output goes from `indent=2` to one line (`spawn/cli.py:41`,
`:61`), a spawn refusal moves from stderr to stdout (`:67`), and its exit
status from 2 to 1 (`:68`). Raw CLI output can therefore never be equal. So:

- `oracle/shim/kitchen_v1.py` is a small Python program with no logic of its
  own. It reads the same `--input` request and secrets descriptor as kitchen,
  calls the same public functions today's entry points call
  (`Deployment.prepare`/`activate`, the spawn functions behind `spawn/cli.py`,
  `Lifecycle.status`), builds `core` as in 2.4 and prints the v1 envelope.
- The differential compares the shim's `core` with kitchen's `core`. The
  receipt line itself uses one transport encoding for both: sorted keys,
  compact, ASCII-escaped (`deploy.py:309-310`). Hashes inside `core` keep the
  encoder of the module that computes them.
- The shim must not be able to hide a difference. Kind-C vectors stay exactly
  as recorded from today's CLIs and are replayed against the oracle. A fixed
  projection (drop the envelope, re-indent, map stream and status) must turn
  each shim result into those recorded bytes. The shim is also in the mutant
  set (`shim.core`): any edit of `core` between the engine call and the
  envelope must fail that replay.

The fake executables are argv-injected Python scripts
(`tests/fixtures/cook.py`, `systemctl.py`, `analyze.py`, `catalog-cook.py`,
wired at `tests/test_deploy.py:62-69`†). Both engines call the same fakes, so
they stay as they are.

Determinism rules for kind B, each tied to a source of variation found in the
code or the survey:

| Source | Where | Rule |
| --- | --- | --- |
| Scratch path enters the request id and several documents | `deploy.py:482-484`, `cook.py:287-293`, `deploy.py:1058` | Both engines run **sequentially at the same fixed absolute root**; snapshot, wipe, run the other |
| Root path may look like a credential | `lock.py:41` matches `sk-` plus 12 characters; `cook.py:66-68` then refuses the path | The harness asserts `credential_shaped(root)` is false before starting. Worktree paths of the form `task-...-impl` do trip it |
| Interpreter path in argv | `tests/test_deploy.py:62`† | Fakes are addressed through one fixed launcher path |
| Clock | `spawn/identity.py:30-34`, `spawn/lifecycle.py:243-244` | Injected clock in both engines (library parameter, not an environment switch) |
| Random handles | `spawn/identity.py:158`† `uuid4` | Injected generator |
| Git commit ids of fixtures | fixture commits without pinned dates† | Fixed author/committer dates in the vector builder |
| uid, gid, home | `deploy.py:124`, `:493` | Same account for both runs; recorded in the vector header |
| mtimes, temp names | transient | excluded from manifests |

**Acceptance for "byte-identical":** for every vector, `canonical(core)` and
every document in the 2.4 table are equal byte for byte between the oracle and
TS, the final tree manifests are equal, and the fake-tool call logs are equal.
Any waiver is a named entry in a reviewed file, with a reason.

### 3.4 Guard tests and mutants

`[T]` carries three mutation scripts with 94 mutants (26 execution, 29
provider, 39 real†). Mechanism (`[T] scripts/test-spawn-real-mutations.py:144-177`):
copy the tree, apply one exact `str.replace(before, after)`, run one named test,
and count the mutant killed only if exactly one test ran, it failed, and nothing
errored. Main has no mutation scripts.

Because anchors are exact Python substrings, they cannot be reused on TS. Plan:

1. **Inventory-driven mutants.** Every row of the section 4 table gets at
   least one mutant per engine, and one per independent check where the row
   lists several: delete the check, or replace its condition with `true`.
   Mutant ids are in the table and are shared between Python and TS
   (`lock.credential`, `catalog.unit.overlap`, ...); anchors are per language.
2. **Mutate the oracle first.** Before any TS exists, run each Python mutant
   against the *vectors*. A guard is covered only if its mutant changes at
   least one golden. This measures whether the goldens are good enough; today
   3 of 20 codes are asserted only by a generic loop (`E_CATALOG_INVALID`,
   `E_SYSTEMD_PATH`, `E_DEPLOY_FAILED`†), so gaps are expected.
3. **Then mutate TS.** The same ids applied to TS must be killed by the TS unit
   suite and by the differential run. CI gate: 100% of inventory mutants killed
   in both engines, plus `[T]`'s 94 re-anchored when phases 11a and 11b port that code.
4. **Guard tests port as vectors.** Every refusal test has a kind-A or kind-C
   vector, so "the existing guard tests must also fail the TS port" becomes
   "TS must reproduce every refusal vector", checked mechanically.

5. **Guards a golden cannot see.** Some rows protect something that leaves no
   trace in a receipt or a tree: an fsync (row 58), a discarded output stream
   or a child's environment (rows 51, 53), a git option that only matters when
   a hook exists (row 51), the order "ownership saved before cook runs" (row
   65). Their mutants survive ordinary vectors by construction. Each needs a
   purpose-built vector: fake tools that record their environment and stdio, a
   fixture checkout with a hook that leaves a mark, and an `io/` call log that
   both engines emit in test mode and that is compared like a fake-tool log.
   Phase 1 lists every mutant that survives and names the vector that kills
   it; a mutant with no vector blocks the module.

A module is done when all its vectors pass with zero unreviewed waivers, its
mutants are killed in both engines, the site check of 4.3 passes, and
`PORTING.md` maps every public Python function to its rows.

## 4. Fail-closed guarantees that must survive

This table is the mutant set of 3.4. A guarantee that is missing here gets no
coverage measurement and no mutant, so a port could drop it and still pass. The
table therefore has a completeness check of its own (4.3). How to read it:

- **Numbering is stable.** Rows 1-47 are the first revision of this plan. Rows
  48-52 are the five blocking findings of its review, rows 53-59 the six
  sub-survey items of that review, rows 60-74 the result of the sweep in 4.2.
- **Mutant id.** The id is shared by the Python mutant and the TS mutant. A row
  with several independent checks gets one mutant per check, named
  `<id>.<check>`. Rows 48-74 list their checks (203 named mutants). Rows 1-47
  carry the id only; their checks are enumerated in phase 1, at least one per
  cited line range.
- **Scope.** An untagged source is main at `a49b5dc`, re-read for this
  revision. Rows tagged `[S]` or `[T]` are the ones known today; 4.3 says how
  the table grows at each freeze. TS owner paths are under `src/`.

### 4.1 Inventory

| # | Guarantee | Source | TS owner | Mutant id |
| --- | --- | --- | --- | --- |
| 1 | `credential_shaped` on every string, not only labelled fields | `lock.py:34-46`; callers `cook.py:34-39`, `:66-68`, `:99-101`, `archive.py:39`, `:66-67`, `:82` | `pylike/credential.ts` | `lock.credential` |
| 2 | Lock is names-only: value-looking keys and strings refused at any depth | `lock.py:79-94`, `:131` | `lock.ts` | `lock.names-only` |
| 3 | Repository identity: credential-free HTTPS, SSH/scp or absolute local only | `lock.py:49-76`; `cook.py:105-106` | `lock.ts` | `lock.repository` |
| 4 | Lock v2 is a closed schema: exact version/backend, required and optional key sets (nested records: row 59) | `lock.py:122-130` | `lock.ts` | `lock.schema.top` |
| 5 | `incomplete` locks refused | `lock.py:132` | `lock.ts` | `lock.incomplete` |
| 6 | Lock build identity equals the build directory | `lock.py:133-136` | `lock.ts` | `lock.build-identity` |
| 7 | Duplicate JSON keys and non-finite numbers refused | `lock.py:315-333`, `inputs.py:102-117` | `pylike/json.ts` | `json.strict` |
| 8 | Host pin, required secret names, sorted unique external servers with literal names and commands | `lock.py:97-104`, `:164-168`, `:180-181`, `:229-244` | `lock.ts` | `lock.pin-names-external` |
| 9 | Host-dependency records closed; bound to config without reopening archives | `lock.py:245-287`, `archive.py:206-226` | `lock.ts`, `archive.ts` | `lock.host-deps` |
| 10 | Launch script and units are contained regular files with matching digests | `lock.py:107-112`, `:293-311` | `lock.ts` | `lock.launch-units` |
| 11 | State path: declared, canonical, equal to config, outside and separate from the root, the builds and the unit directory, no alias (mode and owner: row 52) | `deploy.py:488-491`, `:530-538`, `:711-721`; `cook.py:228-229`, `:261`, `:335-337`; `catalog.py:460-461` | `deploy.ts`, `cook.ts` | `deploy.state-path` |
| 12 | Catalog link rules: no symlinks, no hard links, no special files, no write bits in a sealed build | `catalog.py:383-396`, `:399-410` | `catalog.ts` | `catalog.seal` |
| 13 | Same rules re-checked by pinned Node on every unit start, before `execve` | `catalog.py:784-798` | `catalog.ts` (string kept byte-identical) | `catalog.exec-env` |
| 14 | `[T]` engine links only as relative, digest-bound links; package bin links only as declared in the lock; normalization recorded and verified | `[T] catalog.py:422-461`, `:464-585`, `:596-689`† | `catalog.ts` (see section 6 for removing the need) | `catalog.engine-links` |
| 15 | Shebangs are code positions: absolute, audited interpreter | `catalog.py:552-566` | `catalog.ts` | `catalog.shebang` |
| 16 | Archive extraction: digest of the same open file, no links/devices/duplicates/traversal, size and count caps, modes normalized | `catalog.py:581-607` | `pylike/tar.ts`, `catalog.ts` | `catalog.extract` |
| 17 | Absolute-path audit: only agent tree, state and pinned platform files | `catalog.py:174-200`, `:203-223` | `catalog.ts` | `catalog.audit.abs` |
| 18 | argv audit: explicit executable path, no inline command language, no response files | `catalog.py:226-258` | `catalog.ts` | `catalog.audit.argv` |
| 19 | Environment audit: code-loading names refused, not stripped | `catalog.py:263-317` | `catalog.ts` | `catalog.audit.env` |
| 20 | Unit and launcher audit: literal commands, explicit working directory, no environment indirection | `catalog.py:477-549` | `catalog.ts` | `catalog.audit.unit` |
| 21 | Pins: full nonzero digests and commits, descriptor digest, Node major, platform tool digests and non-writable ancestry | `catalog.py:91-118`, `:165-171`, `deploy.py:432-450` | `catalog.ts`, `deploy.ts` | `catalog.pins` |
| 22 | Installed inventory equals `catalog.lock.json`; engine paths and versions equal pins; lock closure equals the catalog | `catalog.py:684-735` | `catalog.ts` | `catalog.verify` |
| 23 | Strict operator recipe reader: no symlink alias, regular file, digest, duplicate keys; the staged copy is created exclusively, byte for byte | `inputs.py:102-127`; installed copy mode/owner `:154-162`; `deploy.py:867-875` | `inputs.ts` | `inputs.recipe` |
| 24 | Strict local reader: per-component no-follow, single link, stays under the boundary, bounded | `spawn/sources.py:30-67` | `io/fs-safe.ts` | `fs.read-file` |
| 25 | Strict git reader: explicit object store, no ambient config, hooks, transports or replace refs; bounded output; repository top or declared bare store | `spawn/sources.py:70-121` | `spawn/sources.ts` | `spawn.git-reader` |
| 26 | Commit pins: full id, no ref-name collision, exact commit object, regular blobs only | `spawn/sources.py:124-135`, `:158-164` | `spawn/sources.ts` | `spawn.commit-pin` |
| 27 | Strict JSON/YAML decode: size, depth, no tags/aliases/merges, finite numbers, object root | `spawn/data.py:31-97` | `pylike/json.ts`, `spawn/data.ts` | `spawn.decode` |
| 28 | Closed, value-free diagnostics; only table codes pass through; adapter envelope accepted only when exactly equal to a closed literal | `errors.py:47-97`, `deploy.py:380-398`, `cook.py:42-57`, `:380-397` | `errors.ts`, `io/proc.ts` | `errors.closed` |
| 29 | Ansible boundary hides invocation and traceback; lookup plugins fail with fixed texts | `library/connectome_release.py:40-46`; `lookup_plugins/connectome_file.py:13-17`, `lookup_plugins/connectome_inputs.py:17-20` | stays in the role (section 5) | `role.boundary` |
| 30 | Ownership manifest: closed schema, verified before use; unmanaged or modified artifacts never replaced or removed | `deploy.py:1000-1083`, `:1150-1156` | `deploy.ts` | `deploy.ownership` |
| 31 | Path safety: writable ancestors, symlinked bases, containment, exact modes only on created directories, atomic replacement | `deploy.py:83-90`, `:294-338`, `:365-377`, `:505-528`, `:993-998` | `io/fs-safe.ts`, `deploy.ts` | `deploy.path-safety` |
| 32 | systemd paths are literal; enable links computed, never `systemctl enable` | `deploy.py:857-865`, `:1085-1121` | `deploy.ts` | `deploy.systemd-path` |
| 33 | External commands: root-owned chain, not group/other-writable, agent-readable; private data arguments bound by position or flag | `deploy.py:113-291` | `deploy.ts` | `deploy.external` |
| 34 | Never stop an instance implicitly: install-only refuses active units; exclusivity refused, not resolved; an unreadable unit state refuses | `deploy.py:622-668` | `deploy.ts` | `deploy.no-implicit-stop` |
| 35 | Secrets: file and directory modes not repaired; exact declared names; string values; no multiline; quoted environment file, mode 0600; environment file acceptance checked through the manager (reserved names: row 49; private directories: row 52) | `deploy.py:550-570`, `:883-895`, `:1226-1232`, `:1243-1247` | `deploy.ts` | `deploy.secrets` |
| 36 | Activation failure restores the previous release and reports only closed causes | `deploy.py:1278-1300` | `deploy.ts` | `deploy.rollback` |
| 37 | One writer per agent root; validation and the read-only activation preflight run before the root or the lock file is created; the lock path is contained | `library/connectome_release.py:33-39`, `deploy.py:456-457`, `:619-620`, `spawn/persistence.py:64-73` | `io/flock.ts` | `io.single-writer` |
| 38 | Adapter: fresh build directory only, clean environment, cook tree equals the reviewed commit and is clean, no value-bearing env file | `cook.py:60-63`, `:74-94`, `:258`, `:371-374` | `cook.ts` | `cook.adapter-fresh` |
| 39 | Component and dependency provenance equals inputs, for the requested and for retained builds | `cook.py:338-361`, `archive.py:32-118`, `:121-163`, `:192-203`, `:229-244`; `deploy.py:693-698` | `cook.ts`, `archive.ts` | `cook.provenance` |
| 40 | Spawn lifecycle: intent durable before each effect; uncertain operations never replayed; idempotent only by id *and* digest; seed never re-ingested and its receipt bound to the planned digest | `spawn/lifecycle.py:117-124`, `:131-140`, `:169-173`, `:186-190`, `:206-221`, `:238-239` | `spawn/lifecycle.ts` | `spawn.lifecycle.intent` |
| 41 | Spawn plans are not authorization: `activationAllowed:false`, unbound build token refused at apply | `spawn/planner.py:31-49`, `:198` | `spawn/planner.ts` | `spawn.plan-not-auth` |
| 42 | Templates are data only: command-like keys and placeholders refused | `spawn/schema.py:15-48` | `spawn/schema.ts` | `spawn.template-data` |
| 43 | `[S]` reconcile-only holds: a stored receipt that owes reconciliation refuses the next apply; cleared only by naming that receipt's sha256 | `[S] deploy.py:1021-1037` (what blocks), `:1039-1066` (guard; refusal at `:1054-1055`), `[S] inputs.py:136-148` | `deploy.ts`, `inputs.ts` | `s.reconcile-hold` |
| 44 | `[S]` cutover intent recorded durably before any probe; instance identity (`MainPID`, `InvocationID`) compared | `[S] deploy.py:972-1000`, `:1119-1133`† | `deploy.ts` | `s.cutover-intent` |
| 45 | `[S]` probe answers are exit status only; anything undefined is "not idle" | `[S] cutover.py:99-106`, `:7-11`† | `cutover.ts` | `s.probe-status` |
| 46 | `[T]` receipt binding: role receipt bound to action, operation, input digest, build digest and identity; a failed transport is "uncertain", not "failed" | `[T] spawn/real.py:403-409`, `:418-440`†, `[T] connectome_spawn_role.py:94-105` | `spawn/executor.ts` | `t.receipt-binding` |
| 47 | Publication gate rule table | `scripts/check_public.py:41-55` | `scripts/` (unchanged) | `gate.rules` |
| 48 | Catalog unit isolation rewrite. The drop-in written for every exported unit resets `Environment=`, `PassEnvironment=` and `EnvironmentFile=`; unsets every `CODE_ENV` name; names exactly one secrets `EnvironmentFile`, which lies outside the sealed build; refuses a unit environment that overlaps the constructed one; puts the constructed `PATH`, `HOME`, caches, `DATA_DIR` and `TMPDIR` over unit values; escapes `%` and `$`; clears each `Exec*` key and re-emits every command, preflights included, wrapped through pinned Node with secret *names* only in the payload | `catalog.py:814-855`; `systemd_words` `:808-811`; constructed environment `:759-776`; payload `:801-805`; secrets file path `:413-414`, `deploy.py:877-881`; caller `deploy.py:1136-1143` | `catalog.ts` | `catalog.unit` (line numbers here are in the catalog module) + `.reset` (`:845`), `.unset` (`:846`), `.envfile` (`:849`), `.overlap` (`:842`), `.base-env` (`:825`, `:843`), `.escape` (`:810`), `.exec-clear` (`:851`), `.preflights` (`:844`), `.wrap` (`:854`), `.payload-names` (`:802`) |
| 49 | Secret names may not take over engine-set variables. A secret-file env name may not be `DATA_DIR`, `WORKSPACE_DIR`, `PATH`, `HOME`, `LANG`, `LC_ALL` or `TMPDIR`. In catalog mode no secret name may be a code-loading name or overlap the constructed environment. A direct secret may not be `DATA_DIR` or `WORKSPACE_DIR`, nor collide with a secret-file env name. The adapter refuses the same overlaps on its own | `inputs.py:95-98`; `catalog.py:320-331`; `deploy.py:408-411`, `:415-419`, `:423-429`; `cook.py:135-137`, `:187-189` | `inputs.ts`, `catalog.ts`, `deploy.ts`, `cook.ts` | `secrets.reserved` + `.file-env`, `.catalog`, `.catalog-code-env` (`catalog.py:327-328`), `.grammar` (`deploy.py:408-409`), `.direct`, `.collision`, `.adapter` |
| 50 | Secrets in a recipe are name references, never values. A connector `token` must be exactly `${NAME}`. An `env` key that is declared, or whose name contains TOKEN, SECRET, PASSWORD or API_KEY, must be declared and equal `${KEY}`. The catalog audit accepts `${NAME}` only for a declared name under its own key and refuses every other `$` | `cook.py:157-158`, `:174-176`, `:196-201`; `catalog.py:296-298`; `spawn/planner.py:85-94` | `cook.ts`, `catalog.ts`, `spawn/planner.ts` | `recipe.secret-ref` + `.token`, `.catalog-token`, `.env`, `.audit`, `.spawn-token`, `.spawn-env` |
| 51 | Cook adapter argv and git hardening. An argument starting with `-` is refused unless its index is a recorded flag position; a value equal to a flag is not exempt. Positions are recorded while argv is assembled, and the whole argv is validated before the first subprocess. Git runs with `safe.directory=<exact resolved path>`, `core.fsmonitor=false`, `core.hooksPath=/dev/null` and `--no-optional-locks`. Children get no stdin | `cook.py:34-39`, `:42-43`, `:48`, `:80-89`, `:93`, `:303-317` | `cook.ts`, `io/proc.ts` | `cook.argv` + `.dash`, `.flag-position`, `.validate-first` (`:317`), `.stdin`; `cook.git` + `.safe-directory`, `.fsmonitor`, `.hooks`, `.optional-locks` |
| 52 | Existing private directories are refused, not repaired. `state_dir` must be a real directory, mode 0700, owned by the agent, checked before and after creation. `state/secret-files/` must be 0700 and agent-owned and hold only declared, regular, agent-owned 0600 files. `secrets_file` must be a canonical absolute path without control characters | `deploy.py:539-548`, `:941-957`, `:494-498` | `deploy.ts`, `io/fs-safe.ts` | `deploy.private` + `.state-kind`, `.state-mode`, `.state-owner`, `.state-recheck`, `.secret-dir`, `.secret-entry`, `.canonical` |
| 53 | Catalog build sandbox. `HOME`, `TMPDIR` and the npm cache are fresh directories inside the build; `PATH` holds only the pinned runtimes already installed; npm is offline; each build argv is audited and its executable must be an installed file; stdin, stdout and stderr are discarded; a non-zero status refuses; every declared export must exist as a file afterwards. The cook adapter itself runs with a fixed four-name environment in catalog mode | `catalog.py:620-624`, `:630-646`; `deploy.py:794-797` | `catalog.ts`, `deploy.ts`, `io/proc.ts` | `catalog.build` + `.env`, `.path`, `.offline`, `.audit`, `.executable`, `.output`, `.status`, `.export`; `deploy.adapter-env` |
| 54 | Catalog mode runs only staged code and reads only agent-owned inputs. `stage_checkout` refuses a directory alias, symlinks and special files before copying. It does not check hard links; the copy breaks them and the seal checks link counts (row 12). The adapter executes the staged copies, never the input checkouts. An archive, the cook checkout and every cook input must lie inside the agent tree without `..` or alias | `catalog.py:738-742`, `:569-578`; `cook.py:299-302`; `catalog.py:627-628`, `:745-756`; `cook.py:238-240`; `deploy.py:462-463` | `catalog.ts`, `cook.ts` | `catalog.stage` + `.inventory`, `.staged-exec`; `catalog.contained` + `.archive`, `.inputs`, `.checkout` |
| 55 | Preflight `ExecStartPre` argv: audited, absolute executable, and no `%`, `$`, `"`, backslash, CR or LF in any word | `catalog.py:669-681` (run by `verify`, `:703`, `:722`) | `catalog.ts` | `catalog.preflight` + `.chars`, `.absolute`, `.audit` |
| 56 | Spawn lifecycle is a closed state machine. There are six states. Persisted state is a closed record, fully re-validated on every read and bound to its store path. Only the listed transitions are legal for each seed branch. A name record is accepted only for `named`, a seed payload only for a seeded `active`. An effect whose adapter, importer or provider is missing refuses before any intent is written | `spawn/lifecycle.py:16`, `:44-79`, `:93-97`, `:151-153`, `:161-163`, `:174-180`, `:190-198` | `spawn/lifecycle.ts` | `spawn.lifecycle` + `.state-schema`, `.store-binding`, `.target`, `.input-for-target`, `.table`, `.adapter-required` |
| 57 | Identities are per agent and a handle is never reused. Every provision mints a new handle. A live identity with the same name, or the same agent and kind, refuses ("reconcile, never reuse"). Retirement leaves a tombstone; nothing is deleted. An operation id replays only with the same request digest. Rename works only on a live temporary identity and must keep the handle; replace must produce a different one. Names are never truncated. A changed provider capability refuses | `spawn/identity.py:1-4`, `:37-50`, `:82-85`, `:101-161`, `:170-191`; `spawn/lifecycle.py:37-41`, `:199-204`, `:252-253`, `:260-261` | `spawn/identity.ts`, `spawn/lifecycle.ts` | `spawn.identity` + `.no-reuse`, `.tombstone`, `.duplicate-active`, `.op-digest`, `.rename`, `.handle`, `.no-truncate`, `.capability` |
| 58 | Spawn persistence. `..` and a symlink in any path component, the leaf included, are refused. Reads go through the strict reader (row 24). A write is bounded at 1 MiB, goes to a temporary file that is fsynced and renamed, and the directory is fsynced. Directories are created 0700 with the parent fsynced. The lock file is opened no-follow, mode 0600 | `spawn/persistence.py:15-20`, `:23-38`, `:41-43`, `:46-61`, `:64-73` | `io/fs-safe.ts`, `spawn/persistence.ts` | `spawn.persist` + `.traversal`, `.symlink`, `.bound`, `.fsync-file`, `.fsync-dir`, `.atomic`, `.lock-nofollow` |
| 59 | Nested lock records are closed: cook provenance; recipe provenance and its binding to the installed operator recipe; timestamp grammar; requirement records with a closed `origin` set; credential names only; git and npm component key sets and roles; output records; local extensions; state, engine and layout records; health; launch; no double unit declaration | `lock.py:137-163`, `:169-179`, `:182-228`, `:288-295`, `:299` | `lock.ts` | `lock.schema` + `.cook`, `.recipe`, `.operator-recipe`, `.timestamp`, `.requirements`, `.credentials`, `.components`, `.outputs`, `.extensions`, `.state`, `.engines`, `.layout`, `.health`, `.launch`, `.units-ambiguous` |
| 60 | Catalog descriptors are closed and resolved before any IO: unique names across catalog and connectors; name grammar; closed key set; `enabled` true and a known kind; dependencies must be selected; exports relative; commands are argv lists without control characters; `run` if and only if preflight; `pin_ref` equals the artifact identity; cycles refused; `{artifact:...}` only for selected entries; pinned `node` and `bun` runtime exports required | `catalog.py:53-57`, `:68-90`, `:107-110`, `:121-134`, `:137-150` | `catalog.ts` | `catalog.descriptor` + `.duplicate`, `.name`, `.keys`, `.kind`, `.deps`, `.exports`, `.argv`, `.preflight-run`, `.pin-ref`, `.cycle`, `.reference`, `.runtimes` |
| 61 | Catalog data arguments: only `--data-*` flags, an absolute value inside the state directory, attached to a command, one declaration per command, and the argv value must equal the declaration | `catalog.py:341-348`, `:351-368`, `:252-255`, `:442-448`; `spawn/planner.py:97-105` | `catalog.ts`, `spawn/planner.ts` | `catalog.data-args` + `.flag`, `.state`, `.command`, `.conflict`, `.binding`, `.spawn` |
| 62 | Audit coverage, meaning the call sites and not the primitives of rows 15-20. Descriptors and recipe are audited at plan, before the build and again after it. State and build must be separate. `verify` re-audits tree, descriptors, recipe, health, preflights, lock, every unit and the launcher, and repeats the recipe and unit environment audit for each launch directory. The assembled cook argv and its three code paths are audited | `deploy.py:573-585`; `catalog.py:417-475`, `:615-617`, `:647-649`, `:697-731`; `cook.py:180-186`, `:318-332` | `catalog.ts`, `deploy.ts`, `cook.ts` | `catalog.audit-site` + `.plan`, `.pre-build`, `.post-build`, `.separate`, `.verify-tree`, `.verify-lock`, `.verify-units`, `.launch-cwd`, `.cook-argv` |
| 63 | Operator input records are closed. `activation` is one of two values. `exclusive_with`, `recipe_file`, `secret_files` and `external_data_args` are closed records with literal grammars and no duplicates. A recipe path is absolute, canonical and free of braces. A data-argument flag can never be a code-loading flag. The transported file set equals the expected set and is strict base64 | `inputs.py:20-58`, `:61-99`, `:139-151`; mapped to codes at `deploy.py:347-353`, `:763-766` | `inputs.ts` | `inputs.record` + `.activation`, `.exclusive`, `.recipe-file`, `.recipe-path`, `.secret-files`, `.data-args`, `.data-path`, `.data-flag`, `.transport` |
| 64 | Names that become path or unit-name components are closed grammars: agent and connector identifiers, unit names, spawn agent identifiers | `deploy.py:341-344`, `:354-359`, `:971-973`; `spawn/lifecycle.py:32-34`, `:88-91` | `deploy.ts`, `spawn/lifecycle.ts` | `name.grammar` + `.agent`, `.connector`, `.unit`, `.spawn-agent` |
| 65 | Prepare never destroys what it does not own. A build is reused only when its marker equals the request id. The active build and a directory holding manifest-owned units are never rebuilt. Only the caller's own manifest-recorded request directory may be removed; an unknown directory refuses even with a plausible marker. Ownership is saved before cook runs. Without an adapter command it refuses | `deploy.py:757-758`, `:769-781`, `:786-787`, `:1042-1044` | `deploy.ts` | `deploy.prepare` + `.marker`, `.no-adapter`, `.active`, `.owned-units`, `.unmanaged-build`, `.ownership-first` |
| 66 | Units taken from the lock. The fallback unit exists only for a script launcher that is a file. Unit names are literal and unique. The digest is re-checked at use. The role installs its own copy, so every installed unit is manifest-owned. An empty unit set refuses. Two artifacts may not claim one path. A symlinked drop-in directory refuses | `deploy.py:823-855`, `:978-983`, `:1144-1147` | `deploy.ts` | `deploy.units` + `.launcher`, `.name`, `.digest`, `.own-copy`, `.empty`, `.conflict`, `.dropin-symlink` |
| 67 | A retained build is judged by its own recorded request. Its recorded config is re-validated. Its recorded pins, not today's, verify its lock and catalog. Its installed operator recipe is re-verified. The previous release must verify before anything changes. The registry exception is not adopted without a provenance record | `deploy.py:687-710`, `:722-742`, `:818-821`, `:1206-1208` | `deploy.ts` | `deploy.retained` + `.config`, `.pins`, `.recipe`, `.previous-first`, `.sidecar` |
| 68 | Activation checks everything before the first change and again before the restart. The whole artifact set and the pointer are preflighted before the environment file, pointer or units change. Exclusivity is re-checked before installing and immediately before restarting. In catalog mode the lock and tree are re-verified before each restart. The activation marker is a closed one-line file. Install-only health checks pointer, unit files, environment file, inactivity and the unit verifier. A health command must be argv and runs in the constructed environment | `deploy.py:1188-1215`, `:1061-1063`, `:1158-1160`, `:1181-1186`, `:670-676`, `:897-928` | `deploy.ts` | `deploy.activate` + `.preflight`, `.exclusive-recheck`, `.reverify`, `.marker`, `.install-only-health`, `.health-argv`, `.health-env` |
| 69 | Layer merge: exactly four mapping layers; a left fold, so a null in an upper layer cannot be undone by a lower key; `enabled` must be boolean; disabled entries are removed before the engine sees them, and the adapter and the catalog refuse one that arrives anyway; public defaults may not carry connectors | `config.py:31-54`, `:57-60`; `cook.py:148`; `catalog.py:77` | `config.ts`, `cook.ts`, `catalog.ts` | `config.layers` + `.count`, `.fold`, `.enabled-type`, `.prune`, `.public-connectors`; `cook.disabled`; `catalog.disabled` |
| 70 | Adapter input closure. The spec is exactly `{config, pins}` and the pins must equal a fresh resolution. A connector `pin_ref` names its own pin. Budgets are refused, not dropped. Connector settings are a closed set with a command. Source-backed modules refuse. A catalog connector needs an absolute command and no source. Adapter paths are absolute, alias-free, shaped `ROOT/builds/REQUEST`, without `?` or `#`. Cook settings are closed, and `connector_state` may not name an external server | `cook.py:66-71`, `:117-134`, `:139-141`, `:149-156`, `:162-163`, `:168`, `:172-173`, `:208`, `:222-236`, `:245-257`, `:261-271` | `cook.ts` | `cook.input` + `.spec`, `.pins`, `.pin-ref`, `.budgets`, `.settings`, `.module-source`, `.catalog-connector`, `.paths`, `.staged-recipe`, `.cook-settings`, `.connector-state` |
| 71 | Spawn documents are closed schemas: template v2 (v1 refused with a migration hint), instance v1, name record (length limits, no control-category characters), persisted name receipt, home binding, seed policy and intent (bounded, non-empty, consistent with the mode), identity policy | `spawn/schema.py:50-58`, `:61-139`, `:142-158`, `:187-216`; `spawn/seed.py:24-37`; `spawn/identity.py:19-34` | `spawn/schema.ts`, `spawn/seed.ts`, `spawn/identity.ts` | `spawn.schema` + `.template`, `.template-v1`, `.instance`, `.name-record`, `.name-receipt`, `.home`, `.seed`, `.identity-policy` |
| 72 | Spawn reads the role's one catalog exactly: closed descriptors and version sections; one descriptor per file, named after the file; pin shape, full nonzero digests and descriptor digest; selection by kind, with unknown names and cycles refused; artifact references only to selected entries and never escaping their root; the home transport must be selected; a file ingress root does not authorize a repository source; exactly one source per input | `spawn/catalog.py:33-88`, `:98-106`, `:109-157`; `spawn/planner.py:52-70`, `:73-84`, `:107-109`, `:131-135`, `:146-148`; `spawn/schema.py:173-181`; `spawn/sources.py:185-196` | `spawn/catalog.ts`, `spawn/planner.ts` | `spawn.catalog` + `.descriptor`, `.versions`, `.file-name`, `.pin`, `.select`, `.cycle`, `.reference`, `.connector-settings`, `.home-transport`, `.ingress`, `.one-source` |
| 73 | Spawn artifact identity comes from local pinned bytes, never from operator digest fields. Every selected artifact and runtime pin needs an explicit local store. Payload paths are literal, have no symlinked ancestor and stay under the root. The archive digest is computed and compared. Trees admit only regular blobs with literal paths. Sizes are bounded | `spawn/catalog.py:160-211`; `spawn/sources.py:16-27`, `:138-150`, `:167-182`, `:205-223` | `spawn/catalog.ts`, `spawn/sources.ts` | `spawn.artifact` + `.store`, `.payload-path`, `.digest`, `.tree-entries`, `.limit`, `.provenance` |
| 74 | The spawn CLI is closed and value-free. The argument parser never echoes a value. Each command refuses arguments that belong to another. Every refusal is one fixed message with `filesWritten:false`. `status` never creates a file or a lock | `spawn/cli.py:12-15`, `:37-54`, `:63-68`; `spawn/lifecycle.py:99-100` | `cli.ts`, `spawn/lifecycle.ts` | `spawn.cli` + `.parser`, `.combos`, `.refusal`, `.status-readonly` |

### 4.2 How this revision was checked

**The five blocking findings** were re-read line by line at `a49b5dc` and are
rows 48-52. Three of them turned out wider than reported, and the rows say so:
row 48 adds the forced `DATA_DIR`/`TMPDIR` (`catalog.py:825`), the per-key
`Exec` reset (`:851`) and the names-only payload (`:802`); row 49 adds the
adapter's own overlap check (`cook.py:135-137`, `:187-189`); row 50 adds the
same rule in the spawn planner (`spawn/planner.py:85-94`).

**The six sub-survey items** were each confirmed in code:

| Item | Result | Where |
| --- | --- | --- |
| 6. Catalog build sandbox | confirmed, new row | 53 |
| 7. `stage_checkout`, archives inside the agent tree | confirmed with one correction: hard links are not checked at staging, only symlinks, aliases and special files (`catalog.py:569-578`) | 54 |
| 8. `ExecStartPre` argv characters | confirmed, new row | 55 |
| 9. Lifecycle state machine; identity not reused | confirmed, two rows. Correction: what is never reused is the *handle*. A retired *name* may be provisioned again, because the collision check skips retired rows (`spawn/identity.py:155-157`) | 56, 57 |
| 10. Persistence symlink refusal, bound, fsync | confirmed, new row; the lock-file part also folded into row 37 | 58, 37 |
| 11. Nested lock records | confirmed, new row; row 4 stays the top-level key sets | 59 |

**Rows broadened instead of adding a row** (the guarantee was the same, the
citation was too narrow): 3, 8, 11, 16, 17, 20, 22, 23, 25, 28, 29, 30, 31, 34,
35, 37, 39, 40. Row 21 was narrowed to the pins; its descriptor half is row 60.
Row 43 had two different ranges for one guard; it now cites the helper and the
guard separately, read at `7883806`. Folded rather than
added: the seed receipt check went into row 40, secret-value quoting into row
35, "values never in argv or payload" into row 48, bounded git output and the
bare-store rule into row 25.

**The sweep.** Every file below was read in full at `a49b5dc`:

| File | Lines | Rows that now cite it |
| --- | --- | --- |
| `catalog.py` | 855 | 11-13, 15-22, 48-50, 53-55, 60-62, 69 |
| `deploy.py` | 1,301 | 11, 21, 23, 28, 30-37, 39, 48, 49, 52-54, 62-68 |
| `cook.py` | 397 | 1, 3, 11, 28, 38, 39, 49-51, 54, 62, 69, 70 |
| `lock.py` | 333 | 1-10, 59 |
| `archive.py` | 244 | 1, 9, 39 |
| `inputs.py` | 162 | 7, 23, 49, 63 |
| `errors.py` | 97 | 28 |
| `config.py` | 72 | 69 |
| `spawn/` (11 files) | 1,671 | 24-27, 37, 40-42, 50, 56-58, 61, 64, 71-74 |
| `library/connectome_release.py`, both lookup plugins, the filter plugin, both launchers | 122 | 29, 37 |

After the read, a script listed every refusal site in those files and compared
the list with the line ranges in the Source column of 4.1. A site is a line
that starts a `need(`, `require(`, `raise` or `unsafe()` statement, or an
`if`/`except` line with an inline `raise`. Result: **586 sites, 579 inside a
cited range.** The other seven are the bodies of the checking helpers
themselves, not checks: `catalog.py:27`, `cook.py:31`, `lock.py:16`,
`archive.py:29`, `inputs.py:13`, `spawn/data.py:19` and `:23`. The first run of
the script found six real gaps after my read (an export check, a lock error
mapping, an adapter recipe error, a non-string secret value, an operation id
grammar and the adapter-required check); they are in rows 53, 67, 70, 29, 40
and 56. The script also checks that every cited range exists in its file.

**Limits of the sweep.** It covers main only. `[S]`, `[T]` and `[Z]` were not
swept; rows 14 and 43-46 are what the first revision found there. `tests/` was
not read for guarantees that exist only as a test. A refusal site inside a
cited range is counted as covered even if the row's text does not name it, so
the per-check enumeration in phase 1 can still find checks that need their own
mutant. Guards that are not a refusal (an environment that is *constructed*, an
fsync, a discarded stream) have no site to grep; rows 48, 51, 53 and 58 hold
the ones I found by reading.

### 4.3 Keeping the inventory complete

1. **The site check becomes a CI gate.** The script above moves to
   `scripts/inventory-sites` in kitchen and runs against `oracle/`: every
   refusal site must lie in a range cited by a row, or be in a reviewed
   exception file. A security fix that adds a `need(` then fails CI until the
   inventory names it.
2. **Each freeze re-sweeps.** Freeze-1 adds the `[S]` rows and freeze-2 the
   `[T]` and `[Z]` rows, by the same full read plus site check, before the
   affected module's port is accepted. `[T]`'s 94 mutants map onto rows where
   they fit and become rows where they do not.
3. **`PORTING.md` carries the row numbers.** Each TS function lists the rows it
   implements, so a row without a TS owner is visible.
4. **A guarantee found later** is added with a mutant in both engines in the
   same change that reports it.

## 5. The Ansible wrapper

### 5.1 What stays in the role

`tasks/main.yml`, `defaults/`, the lookup plugins and a rewritten
`library/connectome_release.py`. The module stays a module rather than becoming
`command:` tasks, for four properties it already has: check-mode support
(`:21`), `no_log` parameters (`:16`, `:20`), suppression of the invocation echo
on failure (`:40-46`), and a place to return `diff`.

The rewritten module contains no engine logic: build the request, run pinned
Node with the pinned kitchen file, pass secrets on stdin, parse one receipt
line, map it. Target size is about the current 51 lines.

Two controller-side pieces need a decision because they are engine code that
runs on the controller today:

| Piece | Today | Recommendation |
| --- | --- | --- |
| Layer merge | filter plugin loads `config.py` by path (`roles/connectome_agent/filter_plugins/connectome.py:5-8`); `tasks/main.yml:18-22` | Send the four raw layers in the request; kitchen merges on the target and returns the effective `name`. The filter plugin goes away. Transitional fallback: keep the 72-line `config.py` frozen in the role until wrapper v2 |
| Operator recipe read | lookup plugin calls `inputs.collect` on the controller (`roles/connectome_agent/lookup_plugins/connectome_inputs.py:16`, `inputs.py:130-136`) | `kitchen plan` returns the list of controller files it needs (`path`, `sha256`); the wrapper transports bytes with stock Ansible; kitchen verifies them (`inputs.py:139-151` already separates transport from verification) |

Cohort 3 then needs neither plugin: they send a final `config`.

### 5.2 Getting the artifact and Node onto the host

- **Kitchen** is one bundled file, `kitchen-<version>.mjs`, pinned in
  `versions.yml` as `artifacts.kitchen` with its **own closed shape**:
  `{file, version, sha256}`. It cannot use the catalog artifact shape, because
  that shape requires `descriptor_sha256` (`catalog.py:92-96`) and kitchen has
  no descriptor. Why this place and this shape, each checked in code:
  - `artifacts` is the only existing section where it is inert. The whole
    `runtime` mapping enters the request id (`deploy.py:433-434`, `:480-482`)
    and the spawn planner demands a local store for every `runtime` entry
    (`spawn/catalog.py:204-211`). A new top-level key is refused by the spawn
    reader, whose version sections are closed (`spawn/catalog.py:82`).
  - Under `artifacts` it does not enter the request id: `catalog.resolve`
    reads pins only for selected entries (`catalog.py:72`, `:91`), and the
    spawn reader inspects an `artifacts` entry only when it is selected
    (`spawn/catalog.py:109-113`).
  - The name is reserved by the shape itself. A catalog entry called `kitchen`
    would fail the exact key-set test at `catalog.py:92` and be refused.

  The wrapper places the file with a checksum-verifying copy at
  `<base>/.kitchen/<sha256>/kitchen.mjs`, mode 0555. A single file needs no
  extraction step. The digest in the path makes upgrades additive and engine
  rollback a path change.
- **Node.** The brief states pinned Node placement is already done. What I
  could verify on main is narrower: Node is a catalog artifact
  (`versions.yml:45-49`), extracted **inside each build** by `catalog.install`
  (`catalog.py:625-629`, `:658-660`) and only in catalog mode; in recipe-file
  mode the adapter receives an operator-supplied `--node-path`
  (`cook.py:303-308`, `docs/lock-contract.md:52-58`). I found no task on main or
  `[T]` that places a standalone Node before a build exists
  (`[T] roles/connectome_agent/tasks/engine.yml` opens with three
  `include_vars` tasks and has no Node task anywhere).
  Kitchen needs Node before the first build, so either that placement lives in
  the private repository (unverified) or the wrapper must add it: the same
  `inputs/node.tar.gz`, verified against the same digest, extracted once to
  `<base>/.kitchen/node/<sha256>/`. This is Q4.
- **The extractor for that fallback is named, because it is trusted code.**
  Extracting Node cannot be done by kitchen, which needs Node to run. It is
  done by today's Python `extract` (`catalog.py:581-607`, with `relative`
  `:46-50` and `stream_sha256` `:153-157`): about 40 lines, kept in the role
  as `module_utils/connectome_bootstrap.py` and called by the wrapper module,
  which is Python on the target anyway. It is the one piece of engine code
  that stays Python for good. It keeps row 16 of section 4 and the mutant
  `catalog.extract`, with its existing tests. Stock `unarchive` is not used:
  it runs the system `tar`, which honours links and has none of row 16's
  refusals. The archive is already link-free by the payload rules
  (`docs/catalog.md:73-75`). Cohort 3 has no role, so for them pinned Node
  placement is a caller obligation (9.3); the same helper is offered in the
  conformance kit. If Q4 is answered "Node is already placed privately", this
  helper is not needed and the answer names what extracts it there.
- **Self-check.** On start kitchen verifies its own interpreter the way
  `catalog.verify` verifies engines (`catalog.py:712-715`): real path under the
  expected directory, version equal to the pin, no group/other write bit on the
  file or its ancestors (`deploy.py:83-90`). The wrapper verifies the kitchen
  file's digest; kitchen reports it in `meta`.

### 5.3 check, diff, changed

| Ansible mode | Today | With kitchen |
| --- | --- | --- |
| `--check` | module runs with `check=True`, takes no lock, writes nothing (`library/connectome_release.py:25`, `:30-31`; `deploy.py:755-756`, `:767-768`); secrets are not resolved (`tasks/main.yml:51`); the plan is shown by a debug task (`tasks/main.yml:72-75`) | `kitchen plan`; `changed` = `core.changed`; plan shown from the receipt |
| `--diff` | not supported: the module returns no `diff` key | `plan` returns `before`/`after` for: request id, `current` target, unit and drop-in digests, retired artifacts, activation mode. All value-free. The module maps them to Ansible's `diff` |
| normal | two module calls, `prepare` then `activate`, each taking the lock separately (`tasks/main.yml:24-35`, `:54-65`) | `kitchen plan` (for `secret_names`), secret lookup on the controller as today (`tasks/main.yml:44-52`), then one `kitchen apply` holding the lock across prepare and activate |
| `changed` | `prepare.changed` or `activate.changed` | `core.changed` of `apply`; never inferred from exit status |
| failure | `fail_json` with the closed diagnostic (`library/connectome_release.py:43-46`) | exit 1 -> `fail_json(**core)`; exit 2 or unparsable stdout -> `E_DEPLOY_FAILED`, never raw text |

One real improvement falls out: today the activation task is `no_log: true`
(`tasks/main.yml:65`) because its parameters carry secrets, which also hides
its result. With secrets on stdin and a value-free receipt, the module can
return the receipt openly. Whether Ansible fully censors a failed `no_log`
result today was not tested here.

### 5.4 Rollback

| Level | Mechanism | Evidence it works today |
| --- | --- | --- |
| Activation | automatic restore of pointer, units and secret files inside `apply` | `deploy.py:1278-1300` |
| Release | re-apply the previous inputs: same request id, `.prepared` matches, build reused, pointer switched back | `deploy.py:757-762`, `:1268` |
| Engine | wrapper variable selects the frozen Python module or a previous kitchen digest; never automatic | new; relies on both engines reading each other's `ownership.json` v1 (`deploy.py:1019-1020`) and retained builds through their recorded `deployment.json` (`deploy.py:690-692`, `:818-821`) |

Engine rollback is the reason the Python engine stays in the role, frozen,
until every cohort-1 agent has soaked on kitchen (phase 8), and the reason the
differential harness compares on-disk documents and not only receipts.

## 6. Cook: library or CLI

### 6.1 Evidence at the pinned commit `[K]`

The local cook checkout's HEAD is on a different line from the pin (`1b874d9`
is not an ancestor of it), so everything below was read from `1b874d9` itself.

| Fact | Where |
| --- | --- |
| No library entry: `package.json` has `bin` but no `main` or `exports` | `[K] package.json` |
| The executable is TS source run by Bun: `#!/usr/bin/env bun`, importing `../src/cli.js` while the file on disk is `cli.ts`; there is no `dist` | `[K] bin/cook:1-2` |
| `runHostBackend(plan, options)` is exported and takes explicit options | `[K] src/backends/host.ts:84-110`, `:569` |
| ...but it prompts, installs process signal handlers, logs to stdout, and stamps `createdAt` from the wall clock | `[K] host.ts:885` (prompt), `:597` and `:650` (signal handlers), `:878` (stdout), `:1155` (clock) |
| Engine links are created as symlinks to the **absolute** engine paths | `[K] host.ts:916-917` |
| The launcher puts that link directory on `PATH` | `[K] host.ts:465-520` (`renderLauncher`) |
| Host dependencies are written as `file:` + absolute snapshot path, in both the direct section and an override | `[K] src/host-dependency.ts:283-286`, applied at `:327-336` |
| The host install is `bun install` with no frozen-lockfile flag | `[K] host.ts:189`, `:241`, `:391-393` |
| The exported unit uses the state directory as `WorkingDirectory` and `/bin/bash run.sh` as `ExecStart` | `[K] src/host-unit.ts:37-38`, `[K] src/cli.ts:231` |
| The 38 pass-through codes are thrown as message prefixes | e.g. `[K] src/host-engines.ts:23-46` |

How the role consumes cook today: one subprocess, argv assembled at
`cook.py:303-315`, stderr scanned for a known code through a 128-byte overlap
window (`errors.py:47-65`), then the lock is re-read strictly (`cook.py:335`).

### 6.2 What blocked T5b (review record in the private ledger; cook lines verified above)

| Label | Finding | Root cause in cook output | `[T]` response |
| --- | --- | --- | --- |
| M8 | Catalog mode refuses real cook output: env-based shebangs, git sample hooks, "build symlinks are refused" | `.cook-engines/bin` links (`[K] host.ts:916-917`) meet the no-link rule (`catalog.py:393`, `:406`, `:556`) | allow exactly two engine links, relative and digest-bound (`[T] catalog.py:422-461`) |
| r6-r8 | Offline `bun install` fails inside cook | absolute `file:` rewrite forces re-resolution; install is not frozen (`[K] host-dependency.ts:283`, `[K] host.ts:391`) | none in the role; an offline closure was supplied for a probe only |
| r9 | Cook completes; output has 2 absolute engine links, 8 package bin links, CRLF shebang copies, sample hooks | same, plus Bun's cache inside the build because the role points `HOME` and the cache inside the build (`catalog.py:773-776`) | - |
| r10 / r11 | Post-process the build: rewrite the two links, delete the build-local cache and sample hooks, admit declared bin links (`[T] catalog.py:596-669`†, called at `[T] cook.py:380`) | - | still **blocked**: state-directory `WorkingDirectory`, shell launcher outside the literal grammar, and hard links left by the installer (`[T] docs/spawn.md:426-433`) |

Main never met these because its catalog tests use a fake cook
(`tests/fixtures/catalog-cook.py`); `[T]` added the first real-cook catalog test.

### 6.3 Would a library import avoid them?

| Blocker | Avoidable by calling `runHostBackend` with different arguments today? | What actually removes it |
| --- | --- | --- |
| Absolute engine links | No: unconditional when engines are given (`[K] host.ts:912-917`) | cook option for link strategy (`none` or relative); kitchen passes an explicit `PATH`, which the role's isolated unit already constructs (`catalog.py:771-776`) |
| Bun cache links inside the build | Partly: the cache location is the caller's environment (`catalog.py:776`), so kitchen can already place it outside the sealed build; link backend choice is Bun's (unverified which flags the pinned Bun honours) | kitchen-owned build environment with the cache outside the build |
| `file:` absolute rewrite | No: literal at `[K] host-dependency.ts:283` | cook option for a build-relative specifier, which makes the lockfile location-independent |
| Non-frozen install | No: literal command (`[K] host.ts:189`, `:241`) | follows from the previous row: once the specifier is relative, a lockfile shipped in the pinned host archive stays valid and the install can be frozen |
| State-directory `WorkingDirectory`, bash launcher | No: `[K] host-unit.ts:37-38` | kitchen already renders its own unit when cook exports none (`deploy.py:823-840`) and replaces every `Exec` line in catalog mode (`catalog.py:814-855`); let kitchen own the unit entirely. "Own" removes cook's export, not the isolation rewrite: whatever renders the unit must keep row 48 of section 4, with all ten `catalog.unit.*` mutants killed, and rows 13, 20 and 55 |
| Stderr scraping, wall-clock `createdAt`, prompts | Yes for prompts (`noPrompts`, `yes`); no for the rest | typed errors and an injectable clock in cook |

So the honest answer: **a library import avoids almost none of them as cook
stands**. Every row needs a cook change. Once cook changes, both a library
call and new CLI flags can deliver it.

### 6.4 Recommendation

1. **Port with the CLI, unchanged** (phase 8). The differential harness needs
   identical observable behaviour and the fakes are argv-driven. It also keeps
   kitchen's runtime (Node) independent of cook's (Bun, TS source).
2. **Then change cook once, behind a narrow seam** (phase 10, a pull request
   to the cook fork): link strategy, relative dependency specifier, frozen
   install, no unit export, typed error codes, injectable clock and logger, and
   a typed result instead of `{exitCode}`. Expose each as a flag *and* through
   an exported function.
3. **Adopt the library call only when** cook ships a Node-loadable build with
   an `exports` map and the seam above. Until then kitchen calls the flags.
   Kitchen validates cook's lock with `lock.ts` either way; an in-process
   result is never trusted more than a file.

Benefits that are specific to the library path, once available: no stderr
scanning, the lock as an object, cook's own tests importable into kitchen's
conformance run, one process for signal handling. Costs:

- Kitchen's supported Node range becomes cook's.
- The cook tree-equality check (`cook.py:91-92`) has to become a
  package-digest check.
- A cook bug runs inside the process that holds the agent lock.
- **Dependencies.** Cook has four runtime npm dependencies at the pin (`mri`,
  `picocolors`, `prompts`, `semver`: `[K] package.json`). Importing cook ends
  kitchen's "zero runtime dependencies, one file" property (1.2) one way or
  the other. Either the artifact stops being a single file and needs an
  install step and a lockfile on the host, or the bundle absorbs cook and its
  four packages. Bundling brings cook's Apache-2.0 notice duty and four more
  licences into the artifact, which 1.5 avoids today, and it puts five
  packages inside the one digest that is reviewed. Two of the four
  (`prompts`, `picocolors`) serve only the interactive path that kitchen
  never uses; the seam of step 2 should let a library build leave them out.

This cost is a second reason for step 3's condition, and for keeping the CLI
path as the default until cook ships a build that states its own closure.

Not attempted here: building cook with `tsc`, or running it. Whether
`1b874d9` compiles to a runnable `dist` is unverified.

## 7. History

Facts: 36 commits on `origin/main`, 35 touch `module_utils`, `scripts` or
`tests`; no renames in history (`git log --diff-filter=R` is empty); one author
identity; `git filter-repo` is not installed on this machine.

Procedure, on a fresh clone and never on an existing checkout:

1. Filter `main` **and** the in-flight branches in one pass, so branch commits
   are rewritten consistently and can still be rebased afterwards:
   keep `roles/connectome_agent/module_utils/`, `scripts/`, `tests/`,
   `pyproject.toml`, `LICENSE`; rename to `oracle/module_utils/`,
   `oracle/scripts/`, `oracle/tests/`.
2. Commit the old-to-new commit map that `filter-repo` writes, so ids quoted
   in reviews and in `tests/test_compatibility.py:15` /
   `tests/test_catalog.py:946` remain resolvable. Those two tests need their
   baselines re-pointed through the map.
3. Run `check_public --require-denylist --range` over the filtered history
   from the private side before the first push (`docs/publication.md:29-31`).
4. Blame: Python blame is preserved under `oracle/`. TS files are new, so their
   line-level origin is `PORTING.md` (symbol -> `oracle/...:line` at the freeze
   commit), generated and checked in CI.
5. `tests/` and `scripts/check_public.py` are needed on both sides. They are
   kept by the filter in kitchen and stay untouched here; each side later
   deletes what it does not use with ordinary commits.

**This repository's history is not rewritten.** It is public and pinned by
commit id from the private side; the moved files are removed with a normal
commit at the end of phase 9.

## 8. Sequencing against in-flight work, and pins

### 8.1 The branches

| | `[S]` seamless | `[T]` T5b | `[Z]` Zulip frontend |
| --- | --- | --- | --- |
| Diff vs main | +10,372 / -150, 36 files | +8,556 / -184, 57 files | +8,658 / -149, 56 files |
| Engine files changed | `cutover`, `hostprobe` (new); `deploy`, `errors`, `inputs` | `deploy`, `inputs`, `cook`, `catalog`, most of `spawn/`, role tasks, module | as `[T]` at an older round, plus `spawn/bot_*` |
| Shared base | all three share six unmerged commits up to `bfee54c`†; `[T]` and `[Z]` share fourteen up to `9f7085a`† | | |
| Divergence | `deploy.py` and `inputs.py` differ on all three; `execution`, `providers`, `lifecycle`, `cli` differ between `[T]` and `[Z]`† | | |

**A merge hazard to fix regardless of kitchen.** `[S]` excludes `cutover` from
the build identity (`[S] deploy.py:506-507`). `[T]` moved the same computation
into `request_identity()`, which still excludes only `activation` and
`exclusive_with` (`[T] deploy.py:454-460`), and the spawn executor calls that
function. A textual merge that keeps `[T]`'s function changes the request id of
every config with a `cutover` section. This needs a compatibility test in the
style of `tests/test_compatibility.py` before the two meet.

**An architectural consequence of `[T]`.** The real executor shells out to
`ansible-playbook` from inside the engine (`[T] spawn/real.py:403-405`). Kitchen
"knows nothing about Ansible", so this inverts: `kitchen spawn apply` drives the
lifecycle and, for each host action, runs an operator-configured **transport
command** with a JSON action request and expects a receipt. The six actions
already exist as a closed set (`cook-build`, `install-user-unit`,
`switch-start`, `service-health`, `stop-unit`, `mcp-handshake`:
`[T] roles/connectome_agent/tasks/engine.yml:35-38`, `:78-87`). For us the
transport is a small script in this repository that calls our playbook; for
cohort 3 it is theirs. The privileged preparation in
`[T] roles/connectome_agent/tasks/spawn.yml` stays role-side.

### 8.2 Recommendation: land, then freeze, in two steps

1. **Start phase 0-4 now** against `a49b5dc`. They cover `pylike`, the harness
   and six modules. `[S]` does not touch `lock`, `archive`, `config` or
   `catalog`; `[T]` later changes `catalog` and `inputs` (table in 3.1).
2. **Land the shared base and `[S]`**, with the identity test above. Tag
   **freeze-1**. `deploy`, `cook`, `cutover`, `hostprobe` are ported from that
   tag. From freeze-1 the Python engine takes security fixes only, each landed
   with new vectors in the same change.
3. **Do not extend `[T]`'s build normalization in Python.** Its remaining
   blockers are cook output shape (6.2), which phase 10 addresses at the
   source. Let `[T]` and `[Z]` finish their current review rounds (`[Z]` needs a
   rebase onto `[T]`†), land them behind their existing opt-in gates, tag
   **freeze-2**, and port spawn execution, providers and the bot from there in
   phase 11b.
4. If `[T]` cannot close without the cook change, land it in its present
   "refuses real cook output" state (its opt-in test asserts exactly that
   refusal, `[T] tests/test_catalog_real_cook.py:76-99`†) so freeze-2 is not
   held hostage.
5. **At freeze-2, re-port what `[T]` changed in modules that are already TS**
   (phase 11a): `catalog` +433, `inputs` +53, `deploy` +30, `cook` +11 lines.
   The work is the diff `freeze-1..freeze-2` of each file, new vectors for it,
   the inventory re-sweep of 4.3, and `[T]`'s mutants re-anchored. The
   `catalog` part is the build normalization of row 14. If phase 10 has
   already removed the need for it, the Python side deletes it before
   freeze-2 and only the deletion is ported.

The alternative, freezing all three now and finishing them in TS, means
reviewing roughly 9,000 lines of unmerged Python as TS without an oracle that
has passed review. I do not recommend it.

### 8.3 Pins

Today the private repository pins this public repository by gitlink at
`0ec19aa` (per the brief and the ledger; the private file was not read). That
is 16 commits behind `a49b5dc`. The engine additionally hard-binds cook:
`versions.yml:6` and the constant at `cook.py:22`, enforced by tree equality
(`cook.py:91-92`).

After the split:

| Pin | Where | Consumer |
| --- | --- | --- |
| Role commit | private gitlink, as today | cohorts 1 and 2 |
| Kitchen artifact (`file`, `version`, `sha256`; shape and placement argued in 5.2) | `versions.yml` `artifacts.kitchen` in the role commit; overridable privately like any pin | role; cohort 3 pins it directly |
| Cook commit | `versions.yml` `runtime.cook`, and a constant inside the kitchen artifact | kitchen |

Keeping the kitchen pin inside the role's `versions.yml` means the private side
still moves **one** pin in the normal case and the pair is reviewed together.

Compatibility rule:

1. The role declares the contract major it speaks and a minimum kitchen
   version; kitchen's receipt states its own. The module refuses a mismatch
   before sending a request.
2. Kitchen refuses a request whose contract major it does not implement
   (`E_CONTRACT`).
3. A kitchen artifact embeds exactly one reviewed cook tree; `runtime.cook`
   must equal it, as today.
4. State compatibility is one-directional and tested: kitchen N reads
   everything N-1 and freeze-2 Python wrote. Reading newer state is refused.

## 9. Migration windows and rollback

### 9.1 Per agent, cohort 1

Preconditions for every window: the agent is already on the role-managed path
and its release line is at or after freeze-1. The unify plan describes
profiles A, B and C as role-managed (C with an instance-specific activation
wrapper and readiness drop-in that must be reconciled first) and D as a legacy
instance that needs its own migration onto the role; this plan does not
replace that migration.

| Step | What | Downtime |
| --- | --- | --- |
| Shadow, at least three days | The wrapper runs the Python module in check mode and `kitchen plan` side by side on the live root and compares receipts. Both are read-only (`deploy.py:755-768`, no lock in check mode). Only the request id and build directory may differ (finding 1) | none |
| Prestage | `kitchen apply` phase `prepare`: builds the new request directory next to the live one. Refuses to touch the active directory (`deploy.py:773-774`). With `[S]` landed, use `activation: prepare_only` | none |
| Switch | `kitchen apply` (or `kitchen cutover` with `[S]`): pointer, units, restart, health | one restart. Measured update windows today are 17 s to 2 min 42 s per agent (ledger record of the seamless task); the `[S]` receipt reports `downtime_ms` per attempt |
| Soak | 24 h before the next agent | none |

Order: the least critical role-managed agent first, one at a time, each window
with a named person able to run the rollback.

Rollback inside the window, in increasing reach:

1. Health fails: automatic restore to the Python-built release
   (`deploy.py:1278-1300`). Kitchen reads that release through its recorded
   `deployment.json` (`deploy.py:690-692`), so it needs no knowledge of how the
   old request id was computed.
2. Later regression: set the engine selector back to Python and re-apply the
   previous inputs. The old request id still matches its `.prepared` marker
   (`deploy.py:757-758`), so nothing is rebuilt. No code on main deletes old
   builds; the only `rmtree` is of the caller's own incomplete request
   directory (`deploy.py:777-781`).
3. With `[S]` in use: an uncertain cutover leaves a receipt that blocks further
   applies until reconciled (`[S] deploy.py:1039-1066`, refusal at `:1054-1055`). That holds for both
   engines and is why `cutover.json` is in the byte-compared set.

### 9.2 Cohort 2

Adopt through our role after all four cohort-1 agents have soaked. I could not
verify anything about their current state from this repository; the earlier
unify plan does not describe them. Before scheduling: confirm they are on the
role-managed path, which secret lookup they use (`tasks/main.yml:44-52` vs the
target-side file), and whether they were created through `[T]`'s spawn path,
which adds lifecycle state under a separate root (`spawn/lifecycle.py:88-91`).

### 9.3 Cohort 3

They consume the contract, not the role. Deliverables for them:

- The artifact and its digest; `CONTRACT.md`; JSON Schemas for request and
  receipt per command; the error-code table.
- A **conformance kit**: a subset of kind-C vectors they can run in their own
  pipeline against the artifact they pinned.
- A short "what the caller must guarantee" list: pinned Node placement, the
  account kitchen runs as, how secrets reach the file descriptor, the flock
  prerequisite (Q6), and that `changed` comes only from the receipt.
- No role variables, no layer model: they send a final `config`.

Their first adoption should wait for contract v1 to have survived cohort 1,
so that a contract change found there does not reach an external consumer.

## 10. Risks, questions, estimate

### 10.1 Risks

| # | Risk | Likelihood | Mitigation |
| --- | --- | --- | --- |
| R1 | A subtle Python/JS difference changes a hash or weakens a refusal (section 3.2) | high without the harness | `pylike` first; differential fuzzing against CPython per helper; inventory mutants in both engines |
| R2 | Goldens do not actually cover a guard | medium; 3 codes have no specific assertion today | mutate the oracle against the vectors before porting (3.4 step 2) |
| R3 | Adoption is mistaken for a no-op and done outside a window | medium | finding 1 stated in the contract and shown by `plan` as an explicit identity change |
| R4 | Python keeps changing during the port | high; three branches, about 27,000 changed lines | two freeze tags; security-only rule with dual landing |
| R5 | Mixed-engine operation on one root (shadow, rollback) corrupts state | low if both take the same lock | same flock file (`deploy.py:619-620`); documents byte-compared; engine selector is explicit |
| R6 | Layer merge differs between Ansible's `combine` and the stand-in | unknown; the stand-in is labelled test-only (`config.py:5-6`) | differential against real `combine` in the oracle environment before moving the merge |
| R7 | No native flock in the runtime | open | Q6 |
| R8 | Node cannot express a guard as strongly (no `openat`) | medium | `/proc/self/fd` emulation with the existing symlink and hard-link tests as vectors; refuse on non-Linux |
| R9 | Publication gate rejects TS sources or vectors | medium | gate dry-run in phase 0; vectors not stored as `.json` |
| R10 | Cook seam slips; T5b stays blocked | medium | T5b lands in its refusing state (8.2 step 4); seam is its own phase |
| R11 | A bundler makes the artifact non-reproducible | medium | pinned build tool, reproducibility check in CI, digest of the bundle is the only identity |
| R12 | Review load: `[T]` took eleven rounds | high | estimate includes a review factor; smaller modules first |
| R13 | The inventory misses a guarantee, so the port drops it and the mutant gate still passes. The first revision of this plan missed 27 rows | medium after the sweep; high for branch code not yet swept | site check as a CI gate, re-sweep at each freeze, row numbers in `PORTING.md` (4.3); mutants that goldens cannot see get their own vectors (3.4 step 5) |

### 10.2 Questions for the owner, in priority order

- **Q1. Release identity.** Should a kitchen upgrade force a rebuild of every
  agent (artifact digest in the identity), or only when emitted bytes change
  (`compilerVersion`)? Recommendation: the latter; the digest goes to `meta`.
  Feeding the old adapter argv into the hash to keep today's ids would avoid
  the adoption rebuild, but the identity would then name a command that no
  longer runs; not recommended.
- **Q2. Public and registry.** Public after incubation, and no registry
  publication at first: agreed? Reserve the package name?
- **Q3. Licence.** MIT as inherited, or Apache-2.0 to match cook?
- **Q4. Node bootstrap.** Where is standalone pinned Node placed today? I
  found it only inside catalog builds. If it is private-side, kitchen reuses
  it; if not, the wrapper adds it, extracted by the retained Python helper
  named in 5.2.
- **Q5. YAML.** Kitchen core takes JSON only. Spawn templates and the catalog
  are YAML today (`spawn/cli.py:28`, `spawn/catalog.py:91-106`). Options:
  (a) callers convert, kitchen stays YAML-free; (b) a strict in-tree reader
  that refuses every scalar on which YAML 1.1 and 1.2 disagree. Recommendation:
  (a) for deploy, (b) for spawn.
- **Q6. Locking.** No native flock in Node 22 (unverified for the pinned 24 and
  for Bun). Options: re-execute under the platform `flock` tool, which then
  joins `versions.yml` `platform` pins (`versions.yml:65-68`); or a native
  addon (breaks the single-file artifact). Recommendation: the platform tool.
- **Q7. Branch order.** Agree to 8.2, including landing `[T]` in its refusing
  state if needed?
- **Q8. Cook seam ownership.** May kitchen work include a pull request to the
  cook fork (phase 10), and who reviews it?
- **Q9. The bot.** `[Z]` is a long-running process. Port it into kitchen
  (phase 11b) or leave it Python until the rest is done?
- **Q10. Engine rollback horizon.** How long does the frozen Python engine stay
  selectable in the role after the last cohort-1 window? Proposal: 14 days.

### 10.3 Estimate

Unit: dispatch-hours, meaning worker wall-clock including its own verification;
the review column is separate reviewer time. Ranges assume the current
two-reviewer practice.

| Phase | Scope | Work | Review |
| --- | --- | --- | --- |
| 0 | Repository scaffold (private), contract draft, `pylike` + `io`, gate dry-run | 10-14 | 4-6 |
| 1 | Recorder, vector extraction from 294 tests, scenario runner, oracle shim, oracle mutants for 74 rows, vectors for the mutants goldens cannot see, site check | 13-18 | 5-8 |
| 2 | History filter rehearsal and commit map | 2-3 | 1 |
| 3 | `errors`, `lock`, `inputs`, `archive`, `config` | 9-13 | 4-6 |
| 4 | `catalog` | 12-17 | 6-8 |
| 5 | Land shared base + `[S]`, identity test, freeze-1, inventory re-sweep of `[S]` | 5-7 | 3-4 |
| 6 | `deploy`, `cutover`, `hostprobe` | 20-29 | 9-13 |
| 7 | CLI, receipts, schemas, artifact build and reproducibility | 6-10 | 3-4 |
| 8 | `cook` adapter over the CLI; wrapper rewrite with the bootstrap helper; shadow mode | 13-18 | 6-9 |
| 9 | Cohort 1: four windows with shadow and receipts; remove Python from the role at the end | 10-16 | 4-6 |
| 10 | Cook seam (flags + export), kitchen adoption, T5b unblock | 12-18 | 6-10 |
| 11a | freeze-2; re-port the `[T]` deltas to `catalog`, `inputs`, `deploy`, `cook`; inventory re-sweep of `[T]` and `[Z]` | 7-11 | 4-6 |
| 11b | Spawn core, execution, providers, executor transport, bot | 32-46 | 14-20 |
| 12 | Cohort 2 adoption; cohort 3 conformance kit and support | 8-14 | 3-5 |
| | **Total** | **159-234** | **72-106** |

The first revision said 138-203 and 62-93. The total moved by about 15% for
three reasons, each visible in a row:

- The inventory is 74 rows with 203 named checks for rows 48-74 alone, not 47
  rows with one mutant each. That costs in phase 1 and in every module phase.
- The `[T]` delta to already-ported modules is now its own phase (11a). If
  phase 10 removes the build normalization first, 11a falls to about 3-5.
- Spawn (11b) was light: it has to carry `[T]`'s 94 mutants, rows 56-58 and
  71-74, and a long-running bot.

The review found the low ends optimistic. I agree: plan on the upper half of
each range.

Phases 0-4 need no decision except Q2 (private incubation) and can start
immediately. Phase 6 is blocked on phase 5, phases 11a and 11b on freeze-2,
phase 10 on Q8. First production use (phase 9) is reachable without phases
10-12.

### 10.4 Backlog (not blocking this plan; each has an owner phase)

| # | Item | Decide in |
| --- | --- | --- |
| B1 | **Spawn refusal shape against `[T]`.** 2.5 adds `E_SPAWN_REFUSED` to a `core` that is otherwise "exactly today's". `[T]` already changes that refusal: a closed table maps twelve lifecycle messages to lowercase `code` values with a `detail` text, one more code covers unsupported AI access, and `filesWritten` becomes `null` once a mutation was attempted (`[T] spawn/cli.py:13-24`, `:183-192`). Neither `code` nor `detail` is in 2.5. Options: adopt `[T]`'s `code`/`detail` as the v1 spawn error and drop `E_SPAWN_REFUSED`; or keep both with a fixed mapping | freeze-2, before 11b; contract stays v1 only if decided before cohort 3 adopts |
| B2 | **Lifecycle of the `--input` file.** An `apply` request can carry `operator_files`, which are `no_log` today (`library/connectome_release.py:16`). The contract must say who creates the file, where (a 0700 directory owned by the agent account, not a shared temporary directory), with which mode (0600), and who deletes it and when (the wrapper, in an `always` block, also on failure). Alternative: send `operator_files` on the secrets descriptor and keep `--input` value-free | phase 7 (CLI and receipts) |
| B3 | **Recorder encoding for kind-A vectors.** Function arguments are not all JSON: `Path` objects, bytes, open streams (`cook_code_from_stream`, `errors.py:55-65`), injected runners and providers. The recorder needs a tagged encoding for the first three and a named fake for the last. Tests that patch engine internals do not transfer as vectors; they are listed and re-expressed as kind-B scenarios or as TS unit tests, and the list is reviewed so none is lost silently | phase 1 |

## 11. Not verified

- No private repository, inventory, pin file or host was read. The `0ec19aa`
  pin, the placement of pinned Node and everything about cohorts 2 and 3 come
  from the brief or the ledger.
- The inventory sweep of section 4 covers main at `a49b5dc` only. `[S]`, `[T]`
  and `[Z]` were not swept. The site check proves that every refusal site lies
  inside a cited range, not that each row's wording names every check in that
  range, and it cannot see guards that are not refusals.
- The mutant ids are names. No mutant was written or run; whether today's
  tests kill them is exactly what phase 1 measures.
- The oracle shim, the bootstrap helper and the `artifacts.kitchen` shape are
  designs argued from the cited lines; none was prototyped.
- The 12 skipped tests in the baseline run (opt-in real cook and verifier, or
  missing local tools) were not run or examined one by one. No branch's suite
  was run.
- Line numbers marked † were not reopened.
- Cook was read at the pinned commit only for the files cited; it was not
  built or run. Bun's cache and link behaviour was not tested.
- Node checks used the locally installed 22.23.3, not the pinned 24.14.0.
- Whether a failed `no_log` task hides the closed diagnostic today was not
  tested.
