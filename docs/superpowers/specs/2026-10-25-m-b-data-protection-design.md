# M-B data-protection model — design (Part A of docs/superpowers/plans/2026-10-25-m-b-data-protection.md)

## Part A — Spec

(t0 copies everything from this heading up to, not including, the heading
"## Part B — Implementation plan" to `docs/superpowers/specs/2026-10-25-m-b-data-protection-design.md`.)

### 5.1 Levels and the run's declaration file

Levels, ordered: `L0` < `L1` < `L1.5` < `L2`.

| Level | Meaning | Declared by |
|---|---|---|
| `L0` | code, docs, metadata; the default | default |
| `L1` | aggregates with small cells suppressed | user |
| `L1.5` | a released or imported artifact (human-approved aggregate) | ScieFlow only, never declarable |
| `L2` | record-level or re-identifiable data (tables, imaging, EEG/PSG, raw outputs) and everything derived from it that is not released | user |

`workspace/<slug>/data.yml` (module `scieflow.core.data.levels`):

```yaml
version: 1
default_level: L0            # L0 | L1 | L2
levels:                      # each entry: one declaration
  - path: inputs/export.csv  # glob relative to the run; no "..", not absolute, not empty
    level: L2                # L0 | L1 | L2
    kind: tabular            # tabular | imaging | eeg | other
    reason: "REDCap export, record level"
    declared_by: human       # human | agent
    declared_at: "2026-10-26T10:00:00Z"
pending_releases: []         # t5: {id, source, sha256, gate, checks, requested_at}
released: []                 # t5: {id, file, sha256, source, gate, approved_by, approved_at, checks}
imports: []                  # t5: {id, file, sha256, from, release, imported_at}
```

- `run_level(ws) -> Level`: the maximum of `default_level`, every `levels[].level`, and `L1.5`
  if `imports` is non-empty. A declared glob counts whether or not it matches a file today.
- `read(ws) -> DataFile | Invalid`: unknown keys, wrong types, a bad path, an unknown level or
  YAML errors give `Invalid(reason)`, and `run_level` of an invalid file is `L2` with that reason
  (M-B-D5). No `data.yml` = `L0`, no declarations.
- Writes go through one function under `store.locked(<ws>/data.yml)` with an atomic replace
  (`store._atomic_write` pattern). Only `scieflow data` commands write it.
- `project_l2_paths(root, policy)` and `l2_runs(root, except_run)` list what is L2 in the
  project: every run under `workspace/` whose `run_level` is `L2` (a symlinked run entry is
  skipped, the same way `_other_run_homes` skips it), and every existing `project_levels` path
  of level `L2` from the policy.

### 5.2 The project policy file (user-owned)

`config/data-policy.yml` (module `scieflow.core.data.policy`; template
`config/data-policy.example.yml`, everything in it commented out; real file gitignored):

```yaml
version: 1
enforcement: warn            # warn | refuse   (D10, D23; default warn)
safe_endpoints:              # D11: only the user adds entries
  - url: https://llm.example.org/v1     # https, or http only for 127.0.0.1 / ::1 / localhost
    reason: "..."
safe_agents:
  my-safe-agent:
    endpoint: https://llm.example.org/v1   # must be one of safe_endpoints
    templates_sha256: "<64 hex>"           # from `scieflow data pin my-safe-agent`
    reason: "..."
l2_remotes:                  # D23: config/remotes.yml entries that may hold L2
  - remote: meta
    reason: "..."
l2_providers:                # services an L2 run may still query (default: none)
  - name: search-europepmc   # a providers.CATALOGUE key, "scite" or "notebooklm"
    reason: "..."
project_levels:              # data outside any run (repo-relative, absolute or ~/ path)
  - path: ~/datasets/rbd-exports
    level: L2
    kind: tabular
    reason: "..."
min_cell: 5                  # release: smallest publishable cell count (int >= 1)
scan: {max_files: 2000, max_bytes_per_file: 65536, max_seconds: 5}
ingest: {extra_drop: []}     # extra identifier column names for `data ingest`
```

- `load(root) -> Policy`. No file → defaults (`warn`, nothing safe, nothing allowed for L2,
  `min_cell` 5, the scan defaults). Unknown keys, a missing `reason`, a bad URL, an agent whose
  endpoint is not in `safe_endpoints`, an unknown `l2_providers` name, a non-token remote name, a
  bad level or path → `PolicyInvalid(message)`; messages name the key, never a value from a data
  file.
- No ScieFlow code path writes this file (D11). A test runs every `scieflow data` subcommand and
  asserts the file's bytes are unchanged.

### 5.3 Agent boundary (`safe` or `cloud`)

`policy.boundary(policy, agent, final_cfg, *, root, writable_areas) -> Boundary(kind, endpoint,
reason)`, computed on the **final** agent configuration that will be launched (registry entry +
run `agent_overrides` + role override). Kinds and reasons, checked in this order:

| Result | Condition |
|---|---|
| `cloud`, reason `not-listed` | agent not in `safe_agents` |
| `cloud`, reason `no-endpoint-placeholder` | any present template among `cmd`, `stdin_cmd`, `session_cmd`, `resume_cmd` lacks `{endpoint}` |
| `cloud`, reason `pin-mismatch` | sha256 of `json.dumps({k: cfg[k] for those keys present}, sort_keys=True, separators=(",", ":"))` ≠ `templates_sha256` (so any override that changes a template makes the agent cloud) |
| `cloud`, reason `launcher-writable` | after substitution, an argv token that is an absolute path lies under `<root>/workspace` or under a `writable:` grant of `config/sandbox.yml` (A-13) |
| `safe`, `endpoint` set | otherwise |

- `{endpoint}` becomes a template placeholder (`_template_token`). It is filled with the policy
  endpoint for a safe agent. A template with `{endpoint}` for a cloud agent is refused (rule
  `endpoint-without-safe-entry`, below) instead of being filled with an empty string.
- `scieflow data pin <agent>` prints the sha256 of the registry entry's templates (no overrides)
  and the line to paste; it writes nothing.
- Honest limit (stated in the docs): ScieFlow proves which endpoint it handed the CLI and what the
  sandbox exposed, not what a third-party binary sends. The sandbox network stays open (A-7, B6).

### 5.4 The dispatch decision

`scieflow.core.data.dispatch.decide(project, run_dir, agent, final_cfg, *, sandboxed: bool,
templates_use_endpoint: bool) -> Decision` is pure apart from reading files (it writes nothing).

`Decision` fields: `allowed`, `rule` (refusal rule or `None`), `message`, `level`, `boundary`,
`endpoint` (`""` for cloud), `data_hidden` (tuple of absolute Paths), `protected` (tuple),
`warnings` (tuple of `Finding(path, rule, detail)`), `enforcement`.

Rules, first refusal wins (all refusals raise `data.DataRefused(decision)` in the caller):

| Rule id | Condition | Result |
|---|---|---|
| `policy-invalid` | `config/data-policy.yml` invalid | refuse |
| `endpoint-without-safe-entry` | a template uses `{endpoint}` and the boundary is `cloud` | refuse |
| `l2-cloud` | `run_level == L2` and boundary `cloud` | refuse, in every enforcement mode |
| `l2-unsandboxed` | not sandboxed, boundary `cloud`, and the project has any L2 run (other than this one) or L2 `project_levels` path | refuse |
| `l1-external-sharing` | `run_level == L1`, boundary `cloud`, and no gate of kind `external-sharing` in the run with `state: answered`, `answered_by: human` and an answer in {`yes`, `approve`, `approved`, `share`} | warning; refuse under `enforcement: refuse` |
| `scan-warning` | boundary `cloud`, `run_level < L2`, the bounded scan (5.6) has findings | warnings; refuse under `refuse` |
| `scan-incomplete` | as above, scan hit a limit | warning; refuse under `refuse` |

Sandbox inputs (only when sandboxed):
- `data_hidden`: for a `cloud` boundary, every L2 run of the project other than this one (whole
  directory) and every existing L2 `project_levels` path; for `safe`, empty (M-B-D18). A path
  inside another listed directory is dropped (the outer overlay covers it).
- `protected`: `<run>/data.yml` if it is a regular file, `<run>/released` if it is a directory,
  for every boundary (M-B-D9).

Callers (`agent_run.prepare`, `agent_run.resolve_turn`) call `decide` after the final agent
configuration and the sandbox on/off state are known and **before** argv and layout are built; they
put the endpoint into the templates and `data_hidden`/`protected` into the layout, and attach the
decision to `Dispatch.data` / `ResolvedTurn.data` (a new trailing defaulted field).
`DataRefused` is defined as `class DataRefused(Exception)` carrying the decision — not a subclass of
`ValueError`, `OSError`, `RuntimeError` (so not of `SandboxError`) or `DispatchError` — so neither
`resolve_turn`'s wrapper (`agent_run.py:641-642`) nor the existing `SandboxError`/`DispatchError`
handlers swallow it; each caller catches it explicitly.

On refusal, on each path:
- CLI (`agent_run.main`): transcript and stderr get
  `<agent>: data protection refused (<rule>): <message>`; `job.refused` with
  `reason="data-level", rule, level, agent, boundary`; exit **78** (`agent_run.DATA_EXIT`). No job
  is created.
- Service (`service.dispatch_agent`) and conversation (`service._say_prepared`): the same
  `job.refused` event, then `ServiceError(message)`.
On success, the caller emits `data.level_resolved` (`level, agent, boundary, hidden: <count>,
protected: <count>`) and one `data.warning` per new warning (5.6 dedupe), then continues.

### 5.5 Sandbox: hiding and read-only protection

`Layout` gains two trailing fields, both default `()` (so every existing construction is valid):
- `data_hidden: tuple[Path, ...]` — overlaid exactly like `hidden` (tmpfs for a directory,
  `/dev/null` for a file) in the same position (after readable binds, before writable binds), with
  the same refusals (symlink, missing, inside a writable grant). A path in both lists is emitted
  once.
- `protected: tuple[Path, ...]` — each bound read-only onto itself (`--ro-bind p p`) **after** the
  writable binds, scratch pins and masks and **before** the agent home. Each must exist, must not be
  a symlink and must lie inside a writable grant; otherwise `SandboxError`. A read-only bind mount
  cannot be written, renamed or unlinked from inside (EROFS/EBUSY).

`dispatch_layout(..., data_hidden=(), protected=())` passes them through. `verify` proves them:
- for each `data_hidden` directory, a host marker file is created inside it (the existing
  `create_marker` pattern; removed in `finally`) and must be **absent** inside the sandbox;
- for each `data_hidden` file, it must be empty inside (`[ -s p ]` false);
- for each `protected` path, it must not be writable inside (`[ -w p ]` false).
A failed proof raises `SandboxUnavailable`, which every path already turns into a `job.refused
reason=sandbox` and exit 77 / `ServiceError`.

A coordinator-scope dispatch (writable = the whole `workspace/`) with any L2 run would have a hidden
path inside its writable grant and is refused by the existing `_hidden_args` rule; no such dispatch
exists on `M` (`writable_for(coordinator=True)` has no launch caller).

### 5.6 Scan (warnings, D10)

`scieflow.core.data.scan.scan(ws, policy, *, clock) -> ScanResult(findings, files_seen,
truncated)`. Deterministic order (sorted relative paths). Skips: `scratch/agent-home/`, any
`.uv-cache/`, `provenance.git/`, `.git/`, `logs/`, `gates/`, `scite/`, `events.jsonl`. Reads at
most `max_bytes_per_file` per file and the first 200 data rows of a table; stops at `max_files` or
`max_seconds` (`truncated: true`). Rules:

| Rule id | Detects |
|---|---|
| `redcap-export` | CSV/TSV header with `record_id` and one of `redcap_event_name`, `redcap_repeat_instrument`, a `*_complete` column |
| `identifier-column` | a header column whose normalised name (NFKD, accents stripped, lower case, non-alphanumerics → `_`) is in `IDENTIFIER_COLUMNS` (English and Czech names: name/first/last/surname, jmeno, prijmeni, rc, rodne_cislo, birth_number, dob, date_of_birth, birth_date, datum_narozeni, address, adresa, ulice, zip, postcode, postal_code, psc, phone, telefon, tel, mobile, mobil, email, e_mail, mail, insurance_number, cislo_pojistence, mrn, medical_record_number, ssn, national_id, passport, ip_address, plus the policy's `ingest.extra_drop`) |
| `identifier-values` | a column with a value shaped like a birth number `^\d{6}/?\d{3,4}$`, an e-mail `^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$` or an international phone number `^\+\d{1,3}[ \d]{8,14}$` |
| `free-text-column` | ≥ 20 non-empty values and (mean length ≥ 40 characters, or ≥ 30 % of values with ≥ 5 words) |
| `dicom-header` | bytes 128–131 are `DICM`, or extension `.dcm` |
| `imaging-file` | `.nii`, `.nii.gz`, `.mgz`, `.img`/`.hdr` |
| `eeg-header` | `.edf`/`.bdf`/`.rec`, or an EDF header (`0` + 7 spaces) whose patient field (bytes 8–87) is not blank or `X X X X`, or `\xffBIOSEMI` |
| `binary-tabular` | `.xlsx`, `.xls`, `.sav`, `.dta`, `.sas7bdat`, `.rds`, `.feather`, `.parquet` ("cannot be inspected") |
| `unreadable` | a file the scan could not read |

A finding records `path` (run-relative), `rule` and `detail` (a column name or a byte offset).
**Never a value**: a test plants a unique value and asserts it appears in no output, event or
cache file.

Warning dedupe: `<run>/scratch/data-scan-reported.json` maps `(path, size, mtime_ns, rule)` to the
event id already emitted. It only suppresses repeated events in `warn` mode; under `refuse` the
pre-dispatch scan never trusts it (scratch is agent-writable).

### 5.7 Egress points

| Egress | Rule (L2 = `run_level` of the run concerned) | Where |
|---|---|---|
| Provider Gateway (literature search, discovery providers) | For an L2 run, `Gateway.check` raises `ProviderRefused("data protection: run <slug> is L2; <provider> is not in l2_providers (config/data-policy.yml)")` unless the provider is listed. An unscoped Gateway inside a dispatch (`in_dispatch()`) whose `UV_CACHE_DIR` is `<root>/workspace/<slug>/.uv-cache` uses that run's level. | `providers.Gateway._setup`/`check` |
| scite (REST and MCP) | `gateway.request` raises `PolicyError` for an L2 `ledger.ws` unless `scite` is listed, before cache or transport | `integrations/scite/gateway.py` |
| NotebookLM (`fetch` = PDF downloads (D17), `upload`, `verify`, `ask`) | refused with `PolicyError` (exit 3) for an L2 `--workspace` unless `notebooklm` is listed, before any session or download | `scripts/nblm/nblm.py` `main` |
| Remote push | from an L2 run to a remote not in `l2_remotes` → `PolicyError` before any transport call, unless the source lies under `<run>/released/` | `remote.py cmd_push` |
| Remote fetch | from a remote **in** `l2_remotes` into a run below L2 → `data.warning` rule `l2-remote-fetch` ("declare the fetched data"); `PolicyError` under `refuse` | `remote.py cmd_fetch` |
| Agent dispatch | section 5.4 | `agent_run`, `service` |
| DVC push, chats push, news | not refused in M-B (M-B-D13); documented in the egress table | — |

An invalid policy file makes every rule above refuse (exit 3), M-B-D5.

### 5.8 Release and import

`scieflow data release <slug> <artifact> [--id ID] [--counts COL[,COL…] | --no-counts]
[--reason TEXT]` (refused inside a dispatch):
1. The artifact must be a regular file inside the run, not a symlink, not under `released/`.
2. Checks (`release.check(path, policy, counts)`), each with an id; any `refuse` stops here and
   prints a report naming path, column and rule, never a value:

   | Check id | Applies to | Refuses when |
   |---|---|---|
   | `small-cell` | CSV/TSV | a count column (named with `--counts`, else any integer column named `n`, `count`, `freq`, `n_*`, `*_n`, `*_count`) has a value `0 < v < min_cell` |
   | `counts-unknown` | CSV/TSV | no count column found and neither `--counts` nor `--no-counts` given |
   | `record-level` | CSV/TSV | an id-like column (`record_id`, `id`, `subject`, `subject_id`, `patient`, `patient_id`, `pid`, `participant`, `participant_id`, or `*_id`) |
   | `identifier-column`, `identifier-values`, `free-text-column` | CSV/TSV | as in 5.6 |
   | `path-values` | CSV/TSV/JSON | a value that looks like a file path (`/…/x.ext`, `C:\…`, `~/…`) |
   | `dicom-header`, `eeg-header`, `binary-tabular` | any | as in 5.6 |
   | `not-inspectable` | `.png`, `.svg`, `.pdf`, `.jpg` | never refuses; the gate question says "human review only" |

3. On pass: `data.yml` gains a `pending_releases` entry (`id`, `source`, `sha256`, `checks`,
   `gate`, `requested_at`), and a gate of the new kind **`data-release`**
   (`schemas/gates.yml`: `{requires_human: true, help: "Release a checked aggregate from a run"}`)
   is opened with options `[release, reject]` and a question that names the source, sha256 and
   check summary.
4. `gates.answer` hook for kind `data-release` (runs before the gate is marked answered, like the
   review hook): only `actor == "human"` can reach it (the kind requires a human). On `release`:
   find the pending entry by gate id in `data.yml`; recompute the source sha256 and re-run the
   checks — a mismatch or a failed check raises `GateError` and the gate stays open; copy the
   source to `released/<id>/<basename>`; move the entry to `released` with `approved_by: human`,
   `approved_at`; emit `data.released` (`id, sha256, outcome: released`). On `reject`: drop the
   pending entry, emit `data.released` with `outcome: rejected`.

`scieflow data import <dest> --from <src> --release <id>` (refused inside a dispatch): the `src`
`released` record must exist and `src/released/<id>/<file>` must hash to its sha256; the file is
copied to `<dest>/inputs/released/<src>-<id>/<basename>`; `<dest>/data.yml` gains an `imports`
entry; `data.imported` is emitted in `<dest>`. An imported artifact is `L1.5`.

### 5.9 Tabular de-identification (`scieflow data ingest`)

`scieflow data ingest <slug> <source> --out <run-relative path> [--profile generic|redcap]
[--id-col record_id] [--date-anchor COL] [--drop COL]… [--keep-free-text]`:
- Refused inside a dispatch; refused for `--profile imaging|eeg` with exit 2 and a pointer to the
  docs (M-B-D11). `--out` must be inside the run, not existing, not under `released/`.
- Reads the CSV with `csv` (dialect sniffed, UTF-8 with BOM accepted). The source is never copied
  into the run.
- Drops: every `IDENTIFIER_COLUMNS` match, the policy's `ingest.extra_drop`, `--drop` columns,
  and always `rc`/`rodne_cislo`; free-text columns (5.6 rule) unless `--keep-free-text`; with the
  `redcap` profile also `redcap_survey_identifier` and `*_timestamp` columns are treated as dates.
- Dates: a column where ≥ 90 % of non-empty values match ISO `YYYY-MM-DD[ HH:MM[:SS]]` or Czech
  `D. M. YYYY` is converted to integer days since that row's `--date-anchor` value. Date columns
  without `--date-anchor` → refused, naming the columns. The anchor column is dropped afterwards.
- Pseudonyms: the `--id-col` value becomes `hmac_sha256(salt, value)[:16]` (hex). The salt is
  `~/.config/scieflow/salts/<project-dir-name>.salt` (32 bytes from `secrets`, created mode 0600,
  directory 0700; a module constant `SALTS_OVERRIDE` lets tests point elsewhere, like
  `sandbox.CREDENTIALS_OVERRIDE`). A salt file that is a symlink, not 0600, or not 32 bytes is
  refused.
- Writes the output CSV and `<out>.ingest.yml`: `source_name` (basename), `source_sha256`,
  `source_mtime`, `scieflow_version`, `rows_in`, `rows_out`, `columns_kept`, `columns_dropped`
  (name + rule), `dates_converted`, `id_column`, `salt_id` (first 8 hex of sha256 of the salt;
  never the salt).
- Declares the output `L2` (`kind: tabular`, `reason: "ingest output"`, `declared_by: human`) and
  emits `data.ingested` (counts only).

### 5.10 CLI (`scieflow data`, D7)

New lazy group `data` (`src/scieflow/cli.py` `GROUPS`). Every subcommand has `--json`. Errors name
the path and the rule, never a value. Exit codes: 0 ok, 1 refused or check failed, 2 usage or not
available, 3 invalid policy file.

| Command | Writes | In a dispatch |
|---|---|---|
| `data declare <slug> <glob> --level L0\|L1\|L2 [--kind K] --reason R [--as-agent] [--remove]` | `data.yml`, `data.declared` | refused |
| `data levels <slug>` | nothing | allowed |
| `data scan <slug>` | `data.warning` events, dedupe file | allowed |
| `data release <slug> <artifact> …` | `data.yml`, gate | refused |
| `data import <dest> --from <src> --release <id>` | dest `data.yml`, file copy | refused |
| `data ingest <slug> <source> --out …` | output, manifest, `data.yml` | refused |
| `data audit <slug>` | nothing | allowed |
| `data pin <agent>` | nothing | allowed |

- `declare`: a human may add, change, lower or `--remove` an entry (lowering or removing needs
  `--reason`; the event records `lowered: true`). `--as-agent` may only add a new entry or raise
  `default_level` (`--default`), never change or remove one. After any change that leaves L2 anywhere in the project while `enforcement` is
  `warn`, it prints `suggestion: set enforcement: refuse in config/data-policy.yml (ScieFlow never
  sets it)` (D23).
- `levels`: run level, `default_level`, declarations, enforcement, the suggestion, the policy
  path, the boundary of every agent assigned in the run (with reason), pending/released/imports
  counts.
- `audit`: declarations, releases (each released file re-hashed: mismatch → exit 1), imports,
  `job.refused` events with `reason: data-level`, `data.warning` count.
- "In a dispatch" means `scieflow.integrations.scite.mcp.in_dispatch()` is true.

### 5.11 Events and exit codes

- New event types (additive): `data.declared`, `data.level_resolved`, `data.warning`,
  `data.released`, `data.imported`, `data.ingested`. `job.refused` gains `reason: data-level`.
- New exit code **78** for `scieflow agent run` (`agent_run.DATA_EXIT`), documented in
  `docs/cli.md`'s exit-code table.

### 5.12 Web (D7: interface only)

`service._PANELS` gains `"data": _data_panel` — a private builder returning `{reason, level,
enforcement, declarations, warnings, released, imports, suggestion}` (it never raises for an
ordinary state, like the other panels). `run.html` renders one line: the level, "safe agents
only" for L2, the warning count and the suggestion. `data-release` gates appear on the dashboard
through the existing gate list. `data.*` events appear on the timeline through the existing event
rendering. No logic in the web layer.

### 5.13 Docs

- `docs/data-protection.md` (nav "Data protection", after "Remote execution"): levels; declaring;
  the policy file reference; setting up a safe agent (an e-INFRA example and a local Ollama example,
  both written as *your* entries); the dispatch rule table (5.4) with every rule id; sandbox hiding;
  scan rule ids; release/import; ingest; exit codes; **the egress table** (destination, what
  leaves, when, control, behaviour in an L2 run) with one row per `providers.CATALOGUE` key and for
  scite, NotebookLM, remote push, remote fetch, agent dispatch, DVC push, chats push and news;
  honest limits (5.15).
- `docs/sandbox.md`: what a cloud dispatch no longer sees; the two new verify proofs.
- `docs/cli.md`: exit code 78; a `scieflow data` section.
- `SECURITY.md` (repo root): what ScieFlow protects and what it does not (sandbox, open network,
  data levels, third-party CLIs), how to report a vulnerability privately (a private GitHub security
  advisory on the repository or the maintainer directly; never a public issue).
- `PRIVACY.md` (repo root): ScieFlow sends no telemetry; every destination is in the egress table;
  the user decides what data enters and declares it (D9); salts and credentials stay in the home
  folder.

### 5.14 Threats and how M-B answers them

| Threat | Answer | Residual |
|---|---|---|
| Cloud agent dispatched into an L2 run | `l2-cloud`, exit 78, on all three paths | — |
| Cloud agent reads another run's L2 data through the read-only project bind | whole L2 run hidden; `verify()` proves a marker inside it is absent | an L2 declaration made *during* a running dispatch takes effect at the next dispatch |
| Agent lowers its own run's level | `data.yml` read-only in every dispatch; `--as-agent` cannot lower | an unsandboxed process (the human's coordinator) can edit files — same trust as `config/` |
| Agent forges a release | release only via a human-answered gate hook; source re-hashed at answer time; records in read-only `data.yml`; import re-hashes | — |
| Agent swaps a safe agent's command for a cloud CLI | pin over the final templates; overrides make the agent cloud | the human can re-pin a wrong command |
| Safe launcher script edited by an agent | `launcher-writable` | a binary outside writable areas is trusted as installed |
| L2 query text sent to a literature service | Gateway/scite/NotebookLM L2 rules | sandbox network is open (B6): a hostile agent can bypass ScieFlow's own clients |
| L2 files pushed to an unapproved remote | `cmd_push` rule | `git-push` of a remote repo is not data from the run |
| Undeclared patient data | scan warnings; `refuse` per project | heuristics miss what they do not know (D9) |

### 5.15 What M-B does not do (stated in the docs)

It cannot know what a file holds (D9). It cannot prove a third-party CLI's traffic. The sandbox
network stays open until B6. Imaging/EEG de-identification, a `level:` in the input manifest,
refusing DVC/chat backups of L2 runs, and per-file taint tracking are not in M-B.

---

