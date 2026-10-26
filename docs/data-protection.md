# Data protection

ScieFlow can hold research data that must not leave the machine or reach a
cloud model: record-level or re-identifiable data (an L2 run). This page
explains how you declare what a run holds, what ScieFlow then refuses, and
where the protection stops. Read it before a run touches patient or
participant data.

The rules are backed by code, not by the agents' good behaviour. The limits
are stated in [What this does not do](#what-this-does-not-do). They matter as
much as the rules.

## Levels

Every run has one level. The level is the highest of the run's declarations
and its default.

| Level | Meaning |
|---|---|
| `L0` | code and metadata only (the default for a run with no declarations) |
| `L1` | aggregates: counts and summaries that do not identify anyone |
| `L1.5` | a released aggregate imported into this run from another run (see [Release and import](#release-and-import)) |
| `L2` | record-level or re-identifiable data, and everything derived from it |

Only a human can lower or remove a declaration. An agent (`--as-agent`) may
add one or raise the default, but never change or remove an existing
declaration.

## Declaring a run's data

A declaration names a path (a glob inside the run), a level, a kind and a
reason. The run's declarations live in `workspace/<slug>/data.yml`.

```bash
uv run scieflow data declare <slug> 'inputs/*.csv' --level L2 --kind tabular --reason "REDCap export"
uv run scieflow data levels <slug>        # the run's level, declarations, boundaries
uv run scieflow data levels <slug> --json
```

`--kind` is one of `tabular`, `imaging`, `eeg`, `other`. Lowering or removing a
declaration needs a human `--reason` and is recorded as `lowered: true`. The
agent may add a new entry or raise the default (`--as-agent`), never change or
remove one. Nothing an agent writes can lower a level: `data.yml` is read-only
inside every dispatch (see [Sandbox hiding](#sandbox-hiding)).

A `data.yml` that cannot be read is treated as L2 with a reason. It fails
closed (M-B-D5), so a broken file never silently weakens a run.

The overview band on the run page shows the level, the number of warnings and,
where it applies, the suggestion to set `enforcement: refuse`. An L2 run reads
"safe agents only".

## The policy file

The project's policy is `config/data-policy.yml`. It is user-owned: ScieFlow
never writes it, and no agent may edit it. Copy `config/data-policy.example.yml`
to start. Every key is optional and absent means "nothing is allowed for L2".

| Key | Purpose |
|---|---|
| `enforcement` | `warn` (default) or `refuse`. ScieFlow never sets `refuse` for you. |
| `safe_endpoints` | the model endpoints a safe agent may use (`https`, or `http` only for `127.0.0.1`, `::1`, `localhost`) |
| `safe_agents` | agents that may run on L2 data, each pinned to an endpoint and to the SHA-256 of its command templates |
| `l2_remotes` | remotes in `config/remotes.yml` that may hold L2 data |
| `l2_providers` | literature and scite services an L2 run may still query |
| `project_levels` | data folders outside any run, with a level (for example `~/data`) |
| `min_cell` | the smallest publishable count in a released aggregate (default 5) |
| `scan` | scan limits: `max_files`, `max_bytes_per_file`, `max_seconds` |
| `ingest.extra_drop` | extra identifier column names for `scieflow data ingest` |

An invalid policy file refuses every request until it is fixed. A dispatch is
refused with rule `policy-invalid` and exit 78; literature, scite, NotebookLM
and remote requests are refused with exit 3. Messages name the key, never a
value from the file.

## Setting up a safe agent

A safe agent is an agent you have decided is allowed to see L2 data. ScieFlow
ships none. You write the entry, and you decide whether an endpoint is safe for
your data.

Example for an institutional OpenAI-compatible endpoint (the e-INFRA endpoint
is shown as an example; whether it is safe for your data is your decision):

```yaml
version: 1
safe_endpoints:
  - url: https://llm.ai.e-infra.cz/v1
    reason: "institutional endpoint approved for our L2 data"
safe_agents:
  my-einfra-agent:
    endpoint: https://llm.ai.e-infra.cz/v1
    templates_sha256: "<64 hex digits from: uv run scieflow data pin my-einfra-agent>"
    reason: "approved for record-level data"
```

Example for a local model served on the same machine:

```yaml
safe_endpoints:
  - url: http://127.0.0.1:11434/v1
    reason: "local model, never leaves the machine"
safe_agents:
  my-local-agent:
    endpoint: http://127.0.0.1:11434/v1
    templates_sha256: "<64 hex digits from: uv run scieflow data pin my-local-agent>"
    reason: "local"
```

The agent's command template must use `{endpoint}`, so ScieFlow can hand the
CLI the endpoint it is allowed to use. `uv run scieflow data pin <agent>`
prints the hash of the agent's current templates. Any later change to those
templates (an override, a re-pinned command, an edited launcher) no longer
matches the pin, and the agent is treated as cloud again. Re-pin only after you
have read the new command.

A launcher script the agent could edit is not a safe launcher. If the launcher
is writable from an agent-writable folder, the agent is treated as a cloud agent
(rule id `launcher-writable`), whatever its policy entry says.

`uv run scieflow data levels <slug>` shows each agent's boundary. A cloud
boundary gives its reason: `not-listed` (the agent has no `safe_agents` entry),
`no-endpoint-placeholder` (a command template does not use `{endpoint}`),
`pin-mismatch` (the templates no longer match the pinned hash) or
`launcher-writable` (the launcher is writable from an agent-writable folder).

## Dispatch rules

Before any agent dispatch, ScieFlow decides from the run's level, the agent's
boundary and the project's policy. The first refusal wins. Each refusal is a
`job.refused` event with `reason: data-level` and the rule id below, and exits
with code 78.

| Rule id | When it applies | Result |
|---|---|---|
| `policy-invalid` | `config/data-policy.yml` is invalid | refused |
| `endpoint-without-safe-entry` | a template uses `{endpoint}` and the agent is not a safe agent | refused |
| `l2-cloud` | the run is L2 and the agent is a cloud agent | refused, in every enforcement mode |
| `l2-unsandboxed` | the dispatch is not sandboxed, the agent is cloud, and the project has another L2 run or an L2 `project_levels` folder | refused |
| `l1-external-sharing` | the run is L1, the agent is cloud, and no human answered an `external-sharing` gate with yes | warning; refused under `enforcement: refuse` |
| `scan-warning` | the agent is cloud, the run is below L2, and the scan found something (see [Scan warnings](#scan-warnings)) | warnings; refused under `refuse` |
| `scan-incomplete` | as above, but the scan stopped at a limit | warning; refused under `refuse` |

On a refusal the transcript and stderr say `<agent>: data protection refused
(<rule>): <message>`. A refusal is a boundary. Never copy, summarise or paste
L2 content somewhere else to get past it.

## Sandbox hiding

Every cloud dispatch runs inside the [agent sandbox](sandbox.md) with more
hidden from it than before:

- every **other** L2 run of the project is hidden (the whole directory reads as
  empty), and so is every L2 `project_levels` folder;
- the run's own `data.yml` and its `released/` folder are bound read-only, so
  the agent cannot raise, lower, or forge a release;
- a safe agent is not hidden from the run's own data, because it is allowed to
  see it.

The sandbox proves these before the agent starts (`verify()`): a marker
written on the host must be absent inside a hidden directory, a hidden file
must read empty, and a protected path must not be writable. A failed proof
refuses the dispatch (exit 77).

## Scan warnings

`scieflow data scan <slug>` looks at the run's files, and every cloud dispatch
looks at them before it starts. It reads a bounded amount and reports rule ids
and column names, never a value.

| Rule id | Detects |
|---|---|
| `redcap-export` | a CSV/TSV header with `record_id` and a REDCap column such as `redcap_event_name` or a `*_complete` column |
| `identifier-column` | a header column named like an identifier (English and Czech names such as `jmeno`, `rodne_cislo`, `datum_narozeni`, `email`, `telefon`) |
| `identifier-values` | a value shaped like a birth number, an e-mail address or an international phone number |
| `free-text-column` | a column of long text, which can carry identifiers |
| `dicom-header` | a DICOM file (the `DICM` marker or a `.dcm` name) |
| `imaging-file` | a NIfTI or MGZ image |
| `eeg-header` | an EDF, BDF or REC file, or an EDF header with a patient field |
| `binary-tabular` | a spreadsheet or statistics file (`.xlsx`, `.sav`, `.parquet` and similar) that cannot be inspected |
| `unreadable` | a file the scan could not read |

Scan warnings are advice to you, not a verdict. A warning names a file so you
can declare it. The scan is heuristic: it finds what it knows how to recognise,
and it does not read everything in a large file. A `scan-incomplete` warning
means the scan stopped at a limit. Warnings are never silenced by ScieFlow.
Under `enforcement: refuse` they stop the dispatch.

## Egress

Every path that can send data out of the machine is listed below, with what
leaves it and what an L2 run does there. The "In an L2 run" column is the rule
ScieFlow applies. ScieFlow does not send telemetry.

| Destination | What leaves | When | Control | In an L2 run |
|---|---|---|---|---|
| `pubtator` (PubTator3) | the query terms and identifiers the run asks `www.ncbi.nlm.nih.gov` for | a literature search | `l2_providers`; enabled in `config/integrations.yml` | refused (exit 3) unless `pubtator` is in `l2_providers` |
| `ctgov` (ClinicalTrials.gov) | the query terms the run asks `clinicaltrials.gov` for | a trial search | `l2_providers`; enabled in `config/integrations.yml` | refused (exit 3) unless `ctgov` is in `l2_providers` |
| `openalex` (OpenAlex) | the identifiers and queries the run asks `api.openalex.org` for | a lookup | `l2_providers`; enabled in `config/integrations.yml` | refused (exit 3) unless `openalex` is in `l2_providers` |
| `europepmc` (Europe PMC) | the identifiers and queries the run asks `www.ebi.ac.uk` for | a lookup | `l2_providers`; enabled in `config/integrations.yml` | refused (exit 3) unless `europepmc` is in `l2_providers` |
| `search-openalex` (OpenAlex search) | search terms sent to `api.openalex.org` | a discovery search | `l2_providers`; enabled in `config/integrations.yml` | refused (exit 3) unless `search-openalex` is in `l2_providers` |
| `search-arxiv` (arXiv search) | search terms sent to `export.arxiv.org` | a discovery search | `l2_providers`; enabled in `config/integrations.yml` | refused (exit 3) unless `search-arxiv` is in `l2_providers` |
| `search-europepmc` (Europe PMC search) | search terms sent to `www.ebi.ac.uk` | a discovery search | `l2_providers`; enabled in `config/integrations.yml` | refused (exit 3) unless `search-europepmc` is in `l2_providers` |
| `search-crossref` (Crossref search) | search terms sent to `api.crossref.org` | a discovery search | `l2_providers`; enabled in `config/integrations.yml` | refused (exit 3) unless `search-crossref` is in `l2_providers` |
| scite (REST and MCP) | DOIs and key terms of citing sentences, sanitised (rule 17) | a scite request | rule 17 gates; `l2_providers` | refused (exit 3) before the cache or transport unless `scite` is in `l2_providers` |
| NotebookLM | PDF downloads (`fetch`), uploads, `verify`, `ask`, `resolve` (DOI to PDF URL), `check` (opens your session) | a claim check or a PDF download | rule 12; `config/notebooklm.yml`; `l2_providers` | refused (exit 3) for an L2 `--workspace` unless `notebooklm` is in `l2_providers`; `extract` and `report` are local and stay allowed |
| Remote push (`scripts/remote/remote.py`) | files from a run, or from a folder that holds a run | a push to a remote in `config/remotes.yml` | `l2_remotes` | refused (exit 3) to a remote not in `l2_remotes`, except a copy under the run's `released/` folder |
| Remote fetch | files from a remote into a run | a fetch | `l2_remotes` | from a remote in `l2_remotes` into a run below L2: recorded as a warning (`l2-remote-fetch`), or refused under `enforcement: refuse`. Fetching into an L2 run is not restricted here. |
| Agent dispatch (`scieflow agent run`, service, conversation) | the prompt and the files the agent is allowed to read, sent to the agent's model endpoint | every dispatch | the decision in [Dispatch rules](#dispatch-rules) | only safe agents run; every other dispatch is refused (`l2-cloud`, exit 78) |
| DVC push (`scripts/dvc_sync.py`) | the run's data archive | when you push | you run it; `config/` | not refused by M-B (M-B-D13). The archive can contain L2 data: push an L2 run only to a store you trust. |
| Chat backup (`scieflow chats push`) | agent chat stores | when you push | you run it; `config/chats.yml` | not refused by M-B (M-B-D13). Chat history can quote L2 content. |
| News (`scieflow news run`) | web-research prompts built from the topics in `config/news.yml`, sent to the news agent | when you run it | you run it | not refused by M-B (M-B-D13). Its prompts come from the topic list, not from run files. |

A sandboxed agent's network is open (see [What this does not do](#what-this-does-not-do)).
An agent can reach a service ScieFlow does not check, in which case the egress
table above does not apply to that traffic.

## Release and import

A result leaves an L2 run only as a released aggregate.

```bash
uv run scieflow data release <slug> outputs/table1.csv --counts n --reason "descriptive counts"
```

`release` runs checks on the file and refuses it, naming the path, column and
rule (never a value), if any check fails. On a pass it opens a `data-release`
gate. Only a human can answer that gate. Answering `release` re-hashes the
source, re-runs the checks, and copies the file to `released/<id>/`. A changed
file or a failed check keeps the gate open.

| Check id | Applies to | Refuses when |
|---|---|---|
| `small-cell` | CSV/TSV | a count column has a value between 0 and `min_cell` |
| `counts-unknown` | CSV/TSV | no count column is found, and neither `--counts` nor `--no-counts` is given |
| `record-level` | CSV/TSV | an id-like column (`record_id`, `subject_id`, `patient_id`, `pid` and similar) |
| `identifier-column` | CSV/TSV | an identifier column (see [Scan warnings](#scan-warnings)) |
| `identifier-values` | CSV/TSV/JSON | a value shaped like an identifier |
| `free-text-column` | CSV/TSV | a column of long text |
| `path-values` | CSV/TSV/JSON | a value that looks like a file path |
| `dicom-header` | any | a DICOM file |
| `eeg-header` | any | an EEG file with a patient field |
| `binary-tabular` | any | a file that cannot be inspected |
| `not-inspectable` | `.png`, `.svg`, `.pdf`, `.jpg` | never refuses; the gate says the file needs human review only |

Another run can use a released aggregate:

```bash
uv run scieflow data import <dest> --from <src> --release <id>
```

The imported copy is recorded in the destination's `data.yml` at level `L1.5`,
and the source hash is checked first. `release`, `import` and `ingest` are not
available inside a dispatch.

## Tabular de-identification

`scieflow data ingest` turns a CSV export into a de-identified table:

```bash
uv run scieflow data ingest <slug> raw/export.csv --out inputs/clean.csv --profile redcap --date-anchor visit_date
```

It drops identifier columns, free text (unless `--keep-free-text`), and any
columns you list with `--drop`. It converts dates to days since an anchor
column, and replaces the id column with a keyed hash whose salt is kept in your
home folder (never in the run). It writes `<out>.ingest.yml` with counts and
hashes (never the salt) and declares the output L2, because a de-identified
record-level table is still record-level. Only `generic` and `redcap` profiles
exist. Imaging and EEG de-identification are not in M-B (M-B-D11): `--profile
imaging` and `--profile eeg` exit 2.

## Commands and exit codes

| Command | Writes | Inside a dispatch |
|---|---|---|
| `data declare <slug> <glob> --level L0\|L1\|L2 --reason R` | `data.yml` | refused |
| `data levels <slug>` | nothing | allowed |
| `data scan <slug>` | `data.warning` events | allowed |
| `data release <slug> <artifact>` | `data.yml`, a gate | refused |
| `data import <dest> --from <src> --release <id>` | `data.yml`, one copied file | refused |
| `data ingest <slug> <source> --out <path>` | the output and its manifest | refused |
| `data audit <slug>` | nothing | allowed |
| `data pin <agent>` | nothing | allowed |

Every `scieflow data` subcommand accepts `--json`. Exit codes: `0` ok, `1`
refused or a check failed, `2` usage or not available, `3` invalid policy file.
`scieflow agent run` exits `78` when a dispatch is refused by a data rule (see
the [CLI reference](cli.md)).

## What this does not do

- **It cannot know what a file holds.** The scan and the checks are heuristics.
  They find identifiers they recognise. A column named `x3` with birth numbers
  in it is not found unless its values match. Declare what you hold (D9).
- **It cannot prove a third-party CLI's traffic.** A cloud agent's CLI talks to
  its provider. ScieFlow decides whether the CLI is started, and with which
  endpoint. It does not inspect the traffic.
- **The sandbox network is open.** A sandboxed agent can open network connections
  that ScieFlow's own clients do not make. Until the sandbox network is
  restricted (B6), a hostile or careless agent can reach a service ScieFlow
  does not check.
- **Safe-agent trust rests on the pinned template, the endpoint ScieFlow hands
  the CLI, and a launcher outside agent-writable folders.** A binary outside the
  writable areas is trusted as installed.
- **Changes made while a dispatch is running take effect at the next
  dispatch.** A declaration made during a running agent does not hide data from
  that agent.
- **Not in M-B:** imaging and EEG de-identification, a `level:` key in the input
  manifest, refusing DVC and chat backups of L2 runs, and per-file taint
  tracking.

The user owns the decisions that matter most: what a run holds, which agents
are safe, which remotes and services may take L2 data, and whether a release is
acceptable. ScieFlow enforces the decisions you record. It does not make them
for you.
