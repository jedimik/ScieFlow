# M-H systematic-review vertical — design (Part A of docs/superpowers/plans/2026-10-31-m-h-systematic-review.md)

## Part A — Spec

### 5.1 Overview and data flow

```
design/design.yml (study_type systematic-review, review: block)
   └─ scieflow design lock  (human gate, M-D1)            → design/lock.jsonl, design/locked/v<n>/
scieflow sr search  → Gateway (leak check, L2 rules, replay) → sr/rounds/<n>/{hits.jsonl,records.jsonl,round.json}
                                                              events: sr.retrieval, sr.dedup_decision, sr.committed
scieflow sr screen prepare → sr/screening/<stage>/<batch>/batch.json + prompts/sr-<stage>-<batch>-<agent>.md
scieflow agent run --role research.sr-screeners <agent> …   (twice, two agents)
scieflow sr screen import → events: sr.screening_decision ×2n, sr.committed
scieflow sr conflicts → sr/adjudication/<stage>/<gate>/conflicts.{json,md} + gate sr-adjudication (human)
scieflow sr adjudicate (human) → decisions.json;  gate answer resolved → events: sr.adjudication, sr.committed
scieflow sr rob|grade … → sr/rob/<R>.yml, sr/grade.yml; events: sr.appraisal, sr.committed
scieflow sr verify | prisma | kappa | eval | status   (read-only derivations)
```

All SR files live under `workspace/<slug>/sr/` (prompts under `workspace/<slug>/prompts/`, research rule 2).
Stages are exactly `title-abstract` and `full-text` (the vendored `SCREENING_STAGES`).

### 5.2 The protocol: the `review:` block of the locked design (M-H-D1)

`schemas/design.yml` gains: `top_keys.optional += [review]`; `item_keys`:
`review: {required: [eligibility, strategies, screening, appraisal, synthesis], optional: [framework, registration, living]}`,
`eligibility: {required: [include, exclude], optional: []}`, `criterion: {required: [id, text], optional: []}`,
`strategy: {required: [id, query, sources], optional: [from_year, limit]}`,
`screening: {required: [], optional: [batch_size, prioritise]}`,
`appraisal: {required: [rob_tool, grade], optional: []}`, `synthesis: {required: [method], optional: [notes]}`,
`registration: {required: [registry], optional: [id]}`; `enums`:
`review_sources: [openalex, crossref, europepmc, arxiv, pubmed, semantic-scholar, datacite]` (must equal
`fanout.ORDER`, test in t3), `rob_tools: [rob2, robins-i, quadas-2, none]`,
`synthesis_methods: [narrative, meta-analysis]`, `registries: [prospero, osf, inplasy, other, none]`,
`frameworks: [pico, pecos, spider, other]`.

Example:

```yaml
review:
  framework: pico
  eligibility:
    include: [{id: i1, text: "Adults with isolated REM sleep behaviour disorder"}]
    exclude: [{id: e1, text: "Wrong population"}, {id: e2, text: "No outcome of interest"}]
  strategies:
    - {id: s1, query: "REM sleep behavior disorder AND alpha-synuclein", sources: [openalex, europepmc], from_year: 2000, limit: 200}
  screening: {batch_size: 50, prioritise: false}
  appraisal: {rob_tool: robins-i, grade: true}
  synthesis: {method: narrative, notes: "..."}
  registration: {registry: prospero, id: "CRD42…"}
  living: false
```

Checks in `model.check` (rule names in brackets; `core/design` must not import `scieflow.research`):
`review` required when `study_type == systematic-review` and an error for any other type [`review`];
`include` and `exclude` each a non-empty list; criterion ids match `ID_PATTERN` (`model.py:32`) and are
unique across both lists [`ids`]; `text` non-empty, ≤ 500 characters; `strategies` non-empty, unique ids,
`query` non-empty ≤ 1000 characters, `sources` a non-empty list of distinct `review_sources`, `from_year`
int 1900–2100, `limit` int 1–200 (default 100); `batch_size` int 1–500 (default 50); `prioritise` bool
(default false); `rob_tool` enum; `grade` bool; `synthesis.method` enum, `meta-analysis` adds a warning
"M-H computes no pooled effect" [`synthesis`]; `registration.registry` enum; `living` bool. A
systematic-review design without `prisma-2020` in `reporting_guidelines` gets a warning [`guideline-sr`].
TODO values give only `todo` errors (existing pattern), so the fresh template still passes
`test_init_template_reports_only_todo_errors`.

`templates.template("systematic-review", …)` adds a `review:` skeleton (one include and one exclude
criterion with `text: TODO`, one strategy with `query: TODO` and `sources: [openalex, europepmc]`,
`appraisal: {rob_tool: TODO, grade: true}`, `synthesis: {method: TODO}`, `screening: {batch_size: 50}`)
and `reporting_guidelines: [prisma-2020]`. `render.render` adds a "Review protocol" section (criteria
table, strategies table, screening, appraisal, synthesis, registration, living) to `design.md`.

The hash and lock cover the `review` block automatically (`locked_content`, `lock.py:85-87`), so a protocol
change is a design amendment with a diff (`lock.py:408-445`) and appears as `amendment` in the PRISMA
checklist (5.3).

`scieflow.research.sr.protocol.require_locked(project, ws) -> Locked(version, content_hash, review,
design, criteria_include, criteria_exclude)`: reads `lock.state(project, ws)` and refuses (`SRError`, exit
1) unless `status == "locked"` and the study type is `systematic-review`; messages: `none|draft|pending` →
"lock the protocol first: scieflow design lock <slug>"; `modified` → "design.yml differs from its lock
(version N); amend it with scieflow design amend or restore it"; `ledger-invalid`/`invalid` → refuse with
the state's problem. The review block is read from the in-force locked copy
`design/locked/v<n>/design.yml` (equal to the working file when the state is `locked`). `sr protocol
show <slug> [--json]` prints the locked review block, version, hash and amendment trail; `sr protocol
check <slug>` additionally runs `registry.check_query` (and `check_pubmed_query` for strategies naming
`pubmed`) on every strategy query and reports which would be refused (nothing is sent).

### 5.3 The review ledger: events, commits, adapter (M-H-D2)

New event types in `core/events.py` `TYPES` (comment "Added by M-H (systematic review), additive."):

| Type | data (besides `op`, `protocol_version`) | Written by |
|---|---|---|
| `sr.retrieval` | `round, strategy, source, query, record_ids` (hit ids), `capped` | `sr search` |
| `sr.dedup_decision` | `round, losers` (hit ids that are not new records) | `sr search` |
| `sr.screening_decision` | `screener_id, record_id, stage, decision, reason, batch` | `sr screen import` (vendored `record_screening_decision`) |
| `sr.adjudication` | `record_id, stage, decision, reason, rationale, resolves, gate` | gate hook (vendored `record_adjudication`) |
| `sr.appraisal` | `kind` (`rob`/`grade`), `record_id`/`outcome`, judgements, `overall`/`certainty` | `sr rob record`, `sr grade record` |
| `sr.committed` | `kind` (`search|screening|adjudication|rob|grade`), `files` ({relpath: sha256}), counts, kind-specific ids (`round`, `stage`, `batch`, `gate`), `protocol_hash` | every writer, last |

Rules: every write operation draws one `op` id (`store.new_id()`), emits its detail events, then exactly one
`sr.committed` with the same `op`. A detail event counts only if a later `sr.committed` names its `op`;
detail events of an uncommitted `op` (a crashed operation) are ignored and listed by `sr status`/`sr
verify` as a warning. Two `sr.committed` with the same `op` → ledger-invalid. Event data never carries a
record title, abstract, worksheet support text or a screener's free text; queries appear in
`sr.retrieval` (they are the locked protocol's text and passed the leak check); the human adjudication
rationale appears in `sr.adjudication` (the vendored payload requires it). Payload keys `ws`, `type_` and
`actor` are refused by the adapter (they would collide with `events.emit`'s parameters).

`scieflow.research.sr.ledger`:
- `EventsLedger(ws, *, actor, op, extra=None)` implements the vendored ledger interface (5.4): `append`
  maps the vendored type (`retrieval`, `dedup_decision`, `screening_decision`, `adjudication`) to `sr.<type>`,
  adds `op` and `extra` (for example `batch`, `gate`, `reason`, `protocol_version`) to the payload, calls
  `events.emit(ws, …, actor, **payload)` and returns the event as stored (its `event_id` = the emitted
  `id`); any other type is refused. `commit(kind, **data)` emits `sr.committed`.
- `view(ws)` returns a read-only ledger whose `iter_events(run_id=None, type=None)` yields, in file order:
  first one synthesized `protocol_locked` (design ledger record 1) and one `amendment` per later design
  record (payload `{version, content_hash}`, `event_id` `design-v<seq>`; never written to `events.jsonl`),
  then every committed `sr.*` detail event mapped back to its vendored type. Its `append` refuses.
- `run_context(op, extra)` returns the `_ledger.RunContext` the vendored writers take; `ts` passed to the
  vendored functions is the current UTC time (the stored `ts` is `events.emit`'s own).

### 5.4 Vendoring (M-H-D3, D15)

| File (in `researcher_core/`) | Upstream | Upstream sha256 | Patch |
|---|---|---|---|
| `screen.py` | `core/researcher_core/screen.py` | 28072bf60184544de4c1e3d6f4f78e3c9597ffdd2ec8dfb8b181bfea4eeda01f | `from rapidfuzz import fuzz` → `from ._fuzz import fuzz`; `from .provenance import ProvenanceEvent, ProvenanceLedger, RunContext` → `from ._ledger import ProvenanceEvent, ProvenanceLedger, RunContext` |
| `sr_prisma.py` | `core/researcher_core/sr_prisma.py` | def64bad0fcd69d132ac69df47dfe5d47fc1412a071b021e8e861490daf8d3ca | `from .provenance import ProvenanceEvent, ProvenanceLedger, derive_prisma` → `from ._ledger import ProvenanceEvent, ProvenanceLedger, derive_prisma` |
| `prisma_counts.py` (excerpt) | `core/researcher_core/provenance.py` lines 527–625 | file 6c77c217…2002, excerpt 29ebb44a0a1c9c410f8cee341c683734b4943b422bab14dee693125d94e71836 | ScieFlow header only (docstring naming the excerpt, `from __future__ import annotations`, `from collections.abc import Iterable, Sequence`, `from dataclasses import dataclass, field`, `from typing import Any`) |

`_ledger.py` is ScieFlow-authored and unpinned (like `_deps.py`), standard library plus `.prisma_counts`
only: a frozen `ProvenanceEvent(run_id, type, ts, payload, event_id="")`; `RunContext(run_id, extra=())`
whose `event(type, payload, ts, *, source_response_hashes=(), event_id="")` returns a `ProvenanceEvent`
with `payload = {**payload, **extra}`; `ProvenanceLedger` (base: `append`, `iter_events(run_id=None,
type=None)`, `events(run_id=None, type=None)`; `append`/`iter_events` raise `NotImplementedError` naming
`scieflow.research.sr.ledger`); `MemoryLedger(ProvenanceLedger)` (list-backed, append order, assigns
`event_id = sha256(canonical JSON of (seq, type, payload, ts))` when empty, for tests and the harness);
re-exports `PrismaCounts`, `derive_prisma` from `prisma_counts`. `protocol.py` is not vendored (M-H-D1);
VENDORED.md says so and names `core/design/lock.py`.

Ported upstream tests: `tests/vendor/test_researcher_screen.py` (from `R:core/tests/test_screen.py`) and
`tests/vendor/test_researcher_sr_prisma.py` (from `test_sr_prisma.py`) with `ledger` fixtures replaced by
`MemoryLedger` and imports rewritten; every removed test (for example one that depends on SQLite
content-addressed ids or `DuplicateEventError`) is listed with its reason in the file header (the M-C2
practice). `tests/vendor/test_sr_ledger_interface.py` covers `_ledger.py`.

### 5.5 Search rounds and the corpus (M-H-D6, D7, D13)

`fanout.collect(gw, query, *, sources=None, limit=25, from_year=None) -> (chosen, statuses, answers)` is
the loop of `run_all` lines 58–84 moved into a function; `run_all` calls it and is otherwise unchanged
(existing fan-out tests pass unchanged; a new test compares `search all` output before/after on a recorded
fixture byte for byte).

`scieflow sr search <slug> [--network MODE] [--as-agent] [--json]`:
1. `require_locked`; `sr verify` must pass; round number `n` = committed search operations + 1. If `n ≥ 2`
   the protocol must have `living: true` or its version must differ from the last round's version (an
   amendment); otherwise refuse "a non-living review searches once; amend the protocol or set living".
2. Every strategy query is checked first (`registry.check_query`; `check_pubmed_query` for strategies with
   `pubmed`); any refusal → exit 3, nothing sent for any strategy.
3. One Gateway for the run (`registry.gateway(slug, network_mode=…)`, so L2 and replay rules apply); for
   each strategy in protocol order `fanout.collect(…, sources=strategy.sources, limit=strategy.limit,
   from_year=strategy.from_year)`. If any status is not `ok`, nothing is written and the command exits with
   `fanout.exit_code(all statuses)` (3 refused/off, 6 replay miss, 1 failure).
4. Hits get ids `<n>/<strategy>/<source>:<rank>`; each becomes a `CSLRecord` via `kernel.to_csl` with its
   `id` set to the hit id. One vendored `dedupe` call over all hits of the round **and** the CSL records of
   the existing corpus (ids `R…`). Connected components of the decisions (`kept_id`↔`duplicate_id`) are
   groups. A group containing a corpus record maps to that record (the earliest if several; a warning
   names the pair, M-H-D7); every hit in it is a loser. A group without a corpus record becomes one new
   record (next `R` id in order of its first hit: strategy order, canonical source order, rank); its other
   hits are losers.
5. Invariant (bug guard, refuse if broken): `len(hits) − len(losers) == len(new records)`.
6. Files (atomic writes): `sr/rounds/<n>/hits.jsonl` (hit id, strategy, source, rank, and the paper-record
   fields `doi, title, year, venue, abstract, url`), `sr/rounds/<n>/records.jsonl` (new records: `id`,
   `round`, `hits`, `doi, title, year, venue, abstract, url`, `sources`), `sr/rounds/<n>/round.json`
   (`round`, `protocol_version`, `protocol_hash`, per strategy and source: status, count, `capped`,
   totals `identified`, `duplicates`, `new_records`). `capped` = count ≥ the requested limit, or ≥ the
   module's page cap (t0 inventory (e) lists each module's cap).
7. Events: per (strategy, source) one `sr.retrieval`; one `sr.dedup_decision`; one `sr.committed`
   (`kind: search`, `round`, `files` = the three files' sha256, counts).

The corpus is the concatenation of `sr/rounds/*/records.jsonl` in round order (`sr records <slug>
[--round N] [--json]` lists it; records are printed through `sanitize.sanitize_json_strings`). Cached
search answers (Gateway TTL) are a stated residual for living updates (R4).

### 5.6 Dual screening (M-H-D4, D8)

Role `research.sr-screeners` (`Role(True, False, "systematic-review dual screeners (exactly two agents)")`),
default `config/defaults.yml` `research.sr-screeners: [codex, claude]`. The run's effective assignment
(`agent_config`) must list exactly two distinct enabled primary agents (refuse otherwise); the same
`model_family`/`family` prints a warning (independent errors are the point of dual screening).

`scieflow sr screen prepare <slug> --stage title-abstract|full-text [--size N] [--prioritise/--no-prioritise] [--as-agent]`:
- `require_locked`; `sr verify` passes; refuses while a batch of the same stage is prepared but not
  committed. `full-text` refuses while any title-abstract conflict lacks a committed adjudication.
- Queue: title-abstract — corpus records without committed decisions of both screeners at that stage;
  full-text — records whose resolved title-abstract verdict is `include`, without committed full-text
  decisions, **and** with a report file `sr/reports/<R>.{pdf,txt,md,html}` (a regular file, not a
  symlink). Records sought but without a report are not queued (they become "reports not retrieved").
- Order: corpus order; with prioritise (flag, else `review.screening.prioritise`) the vendored
  `rank_queue` (order only, nothing dropped). Batch = the first `N` (`--size`, else `batch_size`).
- Writes `sr/screening/<stage>/<batch>/batch.json` (`batch` = `store.new_id()`, `stage`, `record_ids`,
  `screeners`, `protocol_version`, `protocol_hash`, `prepared_at`) and two prompts
  `prompts/sr-<stage>-<batch>-<agent>.md`, identical except the output path line. The prompt holds: the
  task; the locked criteria (ids and text); the stage rules (title-abstract: when unsure, include; reason
  optional; full-text: an exclusion names exactly one exclude criterion id; read the report file named per
  record); the records as untrusted data (`sanitize.fence_untrusted` around the JSON list of `{id, title,
  year, venue, abstract, doi, report}`; research rule 7 restated); the output format; "do not open any
  other file under sr/".
- Output `sr/screening/<stage>/<batch>/<agent>.json`: `{"format": 1, "screener": "<agent>", "batch":
  "<id>", "decisions": [{"record_id": "R00001", "decision": "include"|"exclude", "reason": "<criterion id>"|""}]}`.

`scieflow sr screen import <slug> --stage S --batch B [--as-agent]` validates everything before writing
anything: batch exists and is not committed; both files are regular files, parse, `format == 1`,
`screener` equals the file stem and is one of `batch.screeners`, `batch` matches; decisions cover exactly
the batch ids, once each; `decision ∈ {include, exclude}`; `reason` is `""` for an include, an exclude
criterion id or `""` for a title-abstract exclude, an exclude criterion id for a full-text exclude.
**Dispatch evidence**: for each screener a job in `jobs.list_jobs(project, run_dir=ws)` with `kind ==
"agent"`, `label == <agent>`, `state == "done"` and `started ≥ prepared_at`; otherwise refuse ("no finished
dispatch of <agent> since the batch was prepared; dispatch it with scieflow agent run --role
research.sr-screeners"). Then every decision through the vendored `record_screening_decision` on an
`EventsLedger` (`extra = {batch, protocol_version}`), then `sr.committed` (`kind: screening`, `stage`,
`batch`, `files` = sha256 of `batch.json`, both prompts, both outputs, `jobs` = {agent: job id}).

Resolved verdicts (`screening.resolved(view, stage) -> {record_id: include|exclude|pending}`): agreement →
that decision; disagreement → the committed adjudication if one resolves exactly the current two decision
event ids, else `pending`. A test asserts that the counts equal the vendored `derive_prisma_flow` stage
summary on the same events.

`scieflow sr kappa <slug> --stage S [--json]`: vendored `cohens_kappa` and `screening_streams` over
`view(ws)`; prints `n`, the 2×2 table, observed and expected agreement and kappa to 6 decimals, the note
(undefined cases). `scieflow sr screen status <slug> [--json]`: per stage corpus size, decided by both,
open batches, conflicts, adjudicated, pending, kappa; uncommitted operations.

### 5.7 Blinded conflicts and the adjudication gate (M-H-D5)

`schemas/gates.yml` gains `sr-adjudication: {requires_human: true, help: "Adjudicate blinded screening
conflicts of a systematic review"}` (comment line "Systematic review (M-H 5.7)").

`scieflow sr conflicts <slug> --stage S [--as-agent]`: refuses while an `sr-adjudication` gate for that
stage is open. Conflicts = vendored `screen_conflicts(view, …, stage, corpus=<record id → record>,
profile=<locked criteria>)` minus records with a committed adjudication resolving their current decision
ids. None → prints "no conflicts", exit 0. Else writes `sr/adjudication/<stage>/<gate>/conflicts.json`
(`Conflict.to_json_dict()` per record: record, criteria and the opaque decision event ids) and
`conflicts.md` (per record: id, title, year, venue, abstract inside a fence, the criteria; no event ids,
**no screener names, decisions or reasons**), and opens the gate (`gates.open_gate`, actor per
`--as-agent`, `gate_id` drawn first so the folder name equals the gate id) with question "Adjudicate <k>
blinded <stage> conflicts in <slug> (sha256 <first 12 hex of conflicts.json>)?", options
`[resolved, reject]`, files the two conflict files.

`scieflow sr adjudicate <slug> <gate-id> <record-id> --decision include|exclude [--reason <exclude id>] --rationale TEXT`:
human only — `refuse_in_dispatch()` first, no `--as-agent` option; the gate is open and of kind
`sr-adjudication`; the record is in its `conflicts.json`; reason rules as in 5.6; rationale 1–2000
characters. Writes or replaces the entry in `decisions.json` (atomic) and prints how many remain.

Gate hook `scieflow.research.sr.adjudication.on_answer(project, ws, gate, answer, actor)`, called from
`gates.answer` for kind `sr-adjudication` before the gate is marked answered (lazy import, the
`design-lock` pattern; an `ImportError` becomes `GateError("answering sr-adjudication gates needs the
research extra")`, so the gate is never answered without its hook):
- `actor != "human"` → `GateError`; `refuse_in_dispatch()` → `GateError`.
- `reject` → nothing recorded (the files stay for audit).
- `resolved` → the sha256 of `conflicts.json` starts with the digest in the question; every conflict has a
  decision; each conflict's `decision_event_ids` still equal the two screeners' latest committed decisions
  (else `GateError` "the screening changed since the gate opened; reject this gate and run sr conflicts
  again"); idempotent if an `sr.committed` with this `gate` exists (crash window); then the vendored
  `record_adjudication` per conflict on an `EventsLedger(actor="human", extra={gate, reason,
  protocol_version})`, then `sr.committed` (`kind: adjudication`, `stage`, `gate`, `files` = sha256 of
  `conflicts.json` and `decisions.json`).

### 5.8 `sr verify` (integrity, fail closed)

`scieflow sr verify <slug> [--json]`, read-only. Errors: a duplicate `sr.committed` op; a committed file
whose sha256 differs or that is missing or a symlink; committed decision events that differ from the output
files they were imported from (screener, record, stage, decision, reason, count); adjudication events that
differ from `decisions.json`, or whose gate file is not `sr-adjudication` answered `resolved` by `human`
(an open gate with an existing commit is accepted only as the crash window and reported); a commit whose
`protocol_version`/`protocol_hash` does not match a design ledger record; corpus ids not a gapless
sequence over rounds; `round.json` totals that disagree with the round's events. Warnings: uncommitted
detail events. Exit 0 clean, 1 with errors. Every SR write command and `sr prisma` run it first and refuse
with "sr-ledger-invalid: <problem>" (exit 1) until a human repairs the run.

### 5.9 PRISMA flow and checklist

`scieflow sr prisma <slug> [--json] [--write]`: `sr verify` first; flow = vendored `derive_prisma_flow`
over `view(ws)` events; checklist = vendored `derive_prisma_checklist` over the same events and flow.
Additions computed by ScieFlow: per-round identification (from committed `sr.retrieval` events), capped
sources, kappa per stage, the screening statement ("two independent agent screeners: <a>, <b>; conflicts
adjudicated by a human; screener independence is procedural (docs)"), protocol version trail. Output
`--json` or text; `--write` writes `sr/prisma/flow.json`, `sr/prisma/flow.md` (text boxes and a Mermaid
`flowchart TD` block with the PRISMA 2020 boxes), `sr/prisma/checklist.md` (every item of
`config/reporting/prisma-2020.yml`: id, topic, status `derived|missing|author-supplied` from the vendored
checklist, evidence text; items the vendored checklist does not cover are `author-supplied`), each stamped
`derived_from: {events: <count>, sha256: <hash of the committed sr events and design ledger records>,
at: <ts>}`. Nothing in `sr/prisma/` is read back as a source of counts.

### 5.10 Risk-of-bias and GRADE worksheets (M-H-D9)

Tool definitions (`config/sr/<tool>.yml`, format: `version: 1`, `id`, `name`, `source: {citation, doi}`,
`verification: {status: unverified, note, verified_by: null, verified_at: null}`, `domains: [{id, label}]`,
`judgements` (ordered, mildest first), optional `applicability: {domains, judgements}`, `overall: {rule:
worst|none, extra: [...]}`):

| Tool | Domains | Judgements | Overall rule |
|---|---|---|---|
| `rob2` (Sterne et al., BMJ 2019;366:l4898) | D1 randomization process, D2 deviations from intended interventions, D3 missing outcome data, D4 measurement of the outcome, D5 selection of the reported result | low, some-concerns, high | `worst`: overall ≥ worst domain; higher only with a rationale |
| `robins-i` (Sterne et al., BMJ 2016;355:i4919) | confounding, selection of participants, classification of interventions, deviations from intended interventions, missing data, measurement of outcomes, selection of the reported result | low, moderate, serious, critical (+ `no-information`, outside the order) | `worst` over the ordered judgements; `no-information` overall only if some domain is `no-information` and none is serious or critical |
| `quadas-2` (Whiting et al., Ann Intern Med 2011;155:529-536) | patient selection, index test(s), reference standard, flow and timing; applicability for the first three | low, high, unclear | `none`: overall optional; `low` only if every domain is `low` |
| `grade` (Guyatt et al., BMJ 2008;336:924-926; Balshem et al., J Clin Epidemiol 2011;64:401-406) | downgrade: risk of bias, inconsistency, indirectness, imprecision, publication bias; upgrade: large effect, dose-response, plausible confounding | downgrade none/serious/very-serious (0/−1/−2); large effect none/large/very-large (0/+1/+2); dose-response and plausible confounding none/present (0/+1) | start 4 (randomised) or 2 (non-randomised); certainty = clamp(start − down + up, 1, 4) → very low/low/moderate/high; upgrades only for non-randomised evidence |

Commands: `sr rob init <slug> [--as-agent]` (tool from the locked protocol; refuses when `none`; one
`sr/rob/<R>.yml` per included study — resolved `include` at full-text — that has none yet; never
overwrites); `sr rob check <slug> [--json]` (domains exactly the tool's; judgement in the tool's list;
`support` non-empty per domain; overall rule; errors name file, domain and rule, never the support text);
`sr rob record <slug> [--as-agent]` (check passes for every included study → one `sr.appraisal` per study
with judgements and overall, then `sr.committed` with every worksheet's sha256; a later record supersedes).
`sr grade init|check|record|table <slug>`: `sr/grade.yml` has one entry per SAP outcome id of the locked
design (`outcome`, `studies` ⊆ included, `design: randomised|non-randomised`, downgrade/upgrade levels each
with a `reason` when not none, `effect_text` author-supplied); `check` computes certainty and refuses an
upgrade on randomised evidence; `table --write` renders `sr/grade/summary-of-findings.md`.

Pinned examples (t7 tests): rob2 domains `[low, some-concerns, low, low, low]` → overall `low` is an error,
`some-concerns` is fine, `high` needs a rationale; grade randomised + risk of bias serious + imprecision
serious → `low`; non-randomised + large effect large → `moderate`; randomised + three very-serious →
`very low` (clamped); randomised + dose-response present → error.

### 5.11 Evaluation harness, gold sets and Wilson intervals (M-H-D10)

`scieflow.research.evals`: `Z95 = statistics.NormalDist().inv_cdf(0.975)` (1.9599639845400536);
`wilson(k, n, z=Z95) -> Interval(k, n, point, low, high)` (Wilson 1927, J Am Stat Assoc 22:209-212;
`n == 0` → `ValueError`; bounds clipped to [0, 1]); `Confusion` for binary labels with `sensitivity`,
`specificity`, `accuracy`, `ppv`, each an `Interval`; `format_interval` → `"k/n = 0.900000 [0.595850,
0.982124]"`. Pinned (6 decimals, abs tol 1e-6): 8/10 → [0.490162, 0.943318]; 9/10 → [0.595850, 0.982124];
10/10 → [0.722467, 1]; 0/50 → [0, 0.071348]; 0/100 → [0, 0.036993]; 27/30 → [0.743789, 0.965400];
24/30 → [0.626943, 0.904949]; 36/40 → [0.769482, 0.960420]; 34/40 → [0.709277, 0.929388];
110/110 → [0.966256, 1].

`scieflow sr eval <slug> --gold PATH --stage S [--json]`: the gold file must resolve inside the run (no
symlink, no `..`), format `{format: 1, stage, labels: {R00001: include|exclude, …}}` (a human-labelled
calibration sample; synthetic in tests); for each screener stream and for the resolved verdict, the
confusion over records that are both labelled and decided, printed with Wilson intervals; labelled records
not yet decided are counted and named.

`scripts/evals/run_axes.py [--axis dedup|kappa|prisma|screening|all] [--json]` (developer harness, repo
checkout, offline, imports `scieflow` from the repo): `dedup` — the 210 pairs of
`tests/fixtures/kernel/dedup.yaml` through the vendored dedupe: same-pair merge rate 110/110 [0.966256, 1],
different-pair false merges 0/100 [0, 0.036993]; `kappa` — the tables of `tests/fixtures/sr/kappa.yml`
(5.12) reproduce exactly; `prisma` — `tests/fixtures/sr/prisma-scenario.yml` replayed on a `MemoryLedger`
reproduces the hand-computed flow (5.12); `screening` — `tests/fixtures/sr/screening-calibration.yml` (40
synthetic records, 10 include / 30 exclude; screener A TP 9 FN 1 FP 3 TN 27, screener B TP 10 FN 0 FP 6
TN 24) prints sensitivity 9/10 and 10/10, specificity 27/30 and 24/30, accuracy 36/40 and 34/40 with the
pinned intervals. Exit 0 when every axis meets its gate, 1 otherwise; each line names the axis.

### 5.12 Hand-checked reference values (gold, synthetic)

Cohen's kappa (Cohen 1960, Educ Psychol Meas 20:37-46), table cells a = both include, b = A include/B
exclude, c = A exclude/B include, d = both exclude:

| a, b, c, d | n | p_o | p_e | kappa |
|---|---|---|---|---|
| 20, 5, 10, 15 | 50 | 0.7 | 0.5 | 0.4 |
| 40, 10, 5, 45 | 100 | 0.85 | 0.5 | 0.7 |
| 10, 5, 3, 30 | 48 | 40/48 | 1350/2304 | 95/159 = 0.597484 |
| 5, 1, 1, 6 | 13 | 11/13 | 85/169 | 58/84 = 0.690476 |
| 10, 0, 0, 0 | 10 | 1 | 1 | undefined (note), vendored |

PRISMA scenario (round 1): strategy s1 sources openalex 30 hits and europepmc 20 hits, strategy s2 source
crossref 10 hits → identified 60 (by source 30/20/10); duplicates 12 → 48 records R00001–R00048.
Title-abstract: both screeners decide all 48; agree include 10, agree exclude 30, conflicts 8 (A include/B
exclude 5, A exclude/B include 3; kappa = row 3), adjudicated 4 include, 4 exclude → screened 48, excluded
34, reports sought 14. Reports present for 13 → reports not retrieved 1, assessed 13. Full-text: agree
include 5, agree exclude 6 (reasons e1 ×4, e2 ×2), conflicts 2 (kappa = row 4), adjudicated 1 include and
1 exclude with reason e2 → excluded 7 (e1 4, e2 3), studies included 6. Round 2 (living): openalex 5 hits,
2 duplicates of the corpus → 3 new records R00049–R00051; cumulative identified 65, duplicates 14, after
deduplication 51.

### 5.13 CLI surface, exit codes and events

`scieflow sr` (registered in `src/scieflow/cli.py` `GROUPS`, extra `research`, help "Systematic review:
protocol, search, dual screening, adjudication, PRISMA, appraisal."):
`protocol show|check`, `search`, `records`, `screen prepare|import|status`, `kappa`, `conflicts`,
`adjudicate`, `verify`, `prisma`, `rob init|check|record`, `grade init|check|record|table`, `eval`,
`status`. Every read command has `--json`. Exit codes: 0 ok; 1 refused or invalid (`SRError`, message names
the rule and the path or id, never record text or a query); 2 usage (click); 3 search policy refusal; 6
replay miss (search only, as `search all`). Writers take `--as-agent` (events actor `agent`, else `human`),
except `sr adjudicate` (human only).

### 5.14 Data protection and untrusted content

Search goes only through `registry.gateway`/`fanout.collect`, so the M-B Gateway rules (an L2 run may reach
no live host, `l2_providers`) and the M-C1 network modes apply unchanged; no other network path is added.
Screener dispatches are ordinary `scieflow agent run` dispatches, so M-B's dispatch refusal for declared L2
data and sandbox masking apply unchanged. Record text (titles, abstracts) is untrusted: fenced in prompts,
printed through `sanitize_json_strings`, never written into events or messages. Screener outputs are
validated against closed sets (ids, decisions, criterion ids), so an injected record can at most flip a
decision, which dual screening and human adjudication are there to catch.

### 5.15 Docs, skill, menu and workflow

Skill `src/scieflow/research/skills/systematic-review/SKILL.md` (symlinked as `skills/systematic-review`):
staffing gate (research AGENTS run-configuration gate, role `research.sr-screeners`); `scieflow design init
<slug> --type systematic-review`, fill the review block, `design check`, `design lock` (human gate);
`scieflow research network <slug> record` before searching (replayable); `sr protocol check`, `sr search`;
title-abstract loop (`sr screen prepare` → two dispatches `uv run scieflow agent run --role
research.sr-screeners <agent> workspace/<slug>/prompts/sr-…-<agent>.md workspace/<slug>/logs/…` → `sr screen
import` → `sr conflicts` → wait for the human gate); place reports under `sr/reports/` (existing full-text
tools or the user); full-text loop; `sr rob …`, `sr grade …`; `sr prisma --write`; living updates;
the rules (never edit `sr/` files or emit `sr.*` events; never adjudicate; never compute counts or kappa
yourself; screeners never open other screeners' files; numbers in a manuscript come from `sr prisma`).
`docs/research/systematic-review.md`: the method, commands, files, events, gate, verify, residuals R1–R8.
`docs/cli.md` gains a `scieflow sr` section; `mkdocs.yml` one nav line ("Systematic Review:
research/systematic-review.md") after line 93. `schemas/status-research.yml` workflow `systematic-review:
[protocol, search, screen-title-abstract, screen-full-text, appraise, report]`; menu `WORKFLOWS` entry
`systematic-review` (skill path, ask "Review question for the systematic review", roles
`["research.sr-screeners"]`; `sr search` dispatches no agent).

### 5.16 Security and integrity properties (reviewed in every ticket that touches them)

| Property | Where |
|---|---|
| Adjudication only by a human: gate `requires_human`, hook refuses non-human and dispatched callers, `sr adjudicate` refuses in a dispatch and has no `--as-agent` | 5.7 |
| Blinding: `conflicts.md` and the gate question carry no screener name, decision or reason (test greps both files for every screener name and for `include`/`exclude` outside the criteria text) | 5.7 |
| Dual screening: exactly two distinct agents, both outputs imported together, dispatch evidence required | 5.6 |
| Fail closed: unlocked or modified protocol, verify errors, partial search rounds, invariant breaks all refuse before any write | 5.2, 5.5, 5.8 |
| Derived numbers: counts, kappa and certainty computed from events/worksheets each time; no stored count is read back | 5.3, 5.9, 5.10 |
| No untrusted text in events or messages; prompts fence record text | 5.3, 5.14 |
| Paths: every path is built from validated ids (`R\d{5}`, `store.new_id`, stage enum, agent names from the assignment); `--gold` must resolve inside the run; symlinks refused where files are read or written | all |
| No new network path, no new dependency (`_fuzz`, `statistics`), no sandbox change | 5.4, 5.11, 5.14 |
| Vendored files byte-identical except the recorded patches (`test_vendored_integrity`) | 5.4 |

### 5.17 What M-H does not do (stated)

No meta-analysis computation or forest plots; no pagination beyond one page per source per strategy
(capped sources flagged); no data-extraction forms; single-assessor RoB/GRADE; no agent adjudicator; no
sandbox mask between screeners; no automatic full-text retrieval (existing tools or the user place reports
in `sr/reports/`); no PRISMA image rendering (Mermaid text only); no registry submission; no web panel; no
Fleiss' kappa.

