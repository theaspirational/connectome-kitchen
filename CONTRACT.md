# Contract v1 — DRAFT (not frozen)

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
| `plan` | `Deployment(check=True)`; `prepare()` then `activate()` (`deploy.py:767-768`, `:1233-1234`, `:1252-1253`). No lock is taken in check mode (`library/connectome_release.py:30-31`) | nothing | `base`, `root`, `unit_dir`, `layers` or `config`, `versions`, `engines`, `systemctl`, optional `operator_files` |
| `apply` | `prepare()` + `activate(secrets)` under the per-agent flock (`library/connectome_release.py:32-39`); `phase` selects `prepare`, `activate` or both | builds, units, pointer | as `plan` + `operator_files`, `phase`; secrets on the fd |
| `verify` | no single entry point on main. Composed from the read-only `_activation_plan()` (`deploy.py:1188-1215`), `_lock()` (`:687-747`), `catalog.verify` (`catalog.py:684-735`) and the install-only health branch (`deploy.py:897-914`). `[T]` adds `health`/`mcp` operations (`[T] roles/connectome_agent/tasks/engine.yml:76-88`) | nothing | as `plan` + `build` (`requested` or `current`) |
| `recover` | spawn `recover`/`reconcile` (`[S] spawn/cli.py:36`†), `abort` (`[T] spawn/cli.py:68`); deploy-level `cutover.reconciled` (`[S] inputs.py:136-148`, `[S] deploy.py:1039-1066`) | bookkeeping only | `target` (`deploy` or `spawn`), `operation`, `reason`, `observed`, `receipt_sha256` |
| `spawn` | `validate`, `plan`, `dry-run`, `status` (`spawn/cli.py:20`); `[T]` adds `apply`, `verify` | per sub-command | `action`, `template`, `instance`, `catalog`, `stores`, `observed`, `seed`, `name`, `state_root`, `agent` |
| `cutover` | `[S]`: prestage with `activation: prepare_only` (`[S] deploy.py:1461-1472`), then apply under a cutover policy (`[S] inputs.py:68-134`) | units, pointer, `state/cutover.json` | as `apply` + required `cutover` policy |

`operator_files` on `plan` is what it is on `apply`: the recipe's bytes as the controller read them. The role hands
them to its check-mode call too (`tasks/main.yml`), and without them `plan` reads the recipe at its configured path
itself (`inputs.py:139-143`), which an account that is not the controller's may not be able to read. An optional
request key, so a minor change (2.6).

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

- For successful single-phase requests, `prepare` omits `activate` and `activate` omits `prepare`. A failure never carries `activate`; a failure in prepare has no `prepare`. A failure in activate retains `prepare` if this invocation successfully prepared first. `plan`
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
- Q1 is decided (ruling D5, `test/port-rulings.golden`): neither the artifact
  digest nor a path of Node or of kitchen enters the release identity. What
  stands for the cook adapter in it is the name of a versioned contract,
  `kitchen-cook-adapter/<n>`, and `n` is raised whenever what the adapter puts
  into a build changes.


## Waiver W1
Refuse float literals overflowing to infinity, including 1E400, with E_CONTRACT.

B1 (spawn refusal reconciliation) is reserved for freeze-2. B2 (input file lifecycle) is reserved for phase 7; do not use this draft operationally. Migration hints on v1-template spawn refusals are retained (N3).
