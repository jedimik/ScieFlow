# Systematic and living reviews

A systematic review in ScieFlow is a locked protocol carried out by commands, not by
prose. The protocol (question, eligibility, sources, search strategies) is locked by a
human before any search. Two different screening agents screen every record. A human
adjudicates every blinded conflict. Counts, Cohen's kappa and the PRISMA 2020 flow are
derived from the committed ledger each time they are read; nothing stores a count and
reads it back. The coordinator skill is `src/scieflow/research/skills/systematic-review/SKILL.md`
(also reached as `skills/systematic-review`).

The command group is `scieflow sr` (extra `research`). Its reference is in
[the CLI reference](../cli.md#scieflow-sr). Exit codes: `0` ok, `1` refused or invalid
(the message names the rule and an id or path, never record text or a query), `2` usage,
`3` a search query refused by the leak check (nothing was sent), `6` a replay miss.

## The loop

| Step | Who | Command |
| --- | --- | --- |
| Staff the screeners | user, coordinator | `agent configure --assign research.sr-screeners=codex,claude` |
| Design and lock the protocol | user (gate) | `design init --type systematic-review`, `design check`, `design lock` |
| Check the strategies | coordinator | `sr protocol show`, `sr protocol check` |
| Set the network mode | user | `research network <slug> record` |
| Search rounds | coordinator | `sr search`, `sr records` |
| Title-abstract screening | two screener agents | `sr screen prepare`, `agent run --role research.sr-screeners`, `sr screen import` |
| Agreement | coordinator | `sr kappa`, `sr screen status` |
| Blinded conflicts | coordinator, then user (gate) | `sr conflicts`, `sr adjudicate` (human only) |
| Full-text screening | as title-abstract | `sr screen prepare --stage full-text` after reports are placed in `sr/reports/` |
| Risk of bias and GRADE | user judges, coordinator checks | `sr rob init`, `sr rob check`, `sr rob record`; `sr grade init`, `sr grade check`, `sr grade record`, `sr grade table` |
| Integrity and report | coordinator | `sr verify`, `sr prisma --write` |
| Evaluate screening against humans | developer or user | `sr eval --gold <file> --stage <stage>` |

A living review repeats `sr search` for each new round and the screening for the new
records only; the rounds and the PRISMA flow record every round.

## Protocol and the lock

The review block of the run's `design/design.yml` (type `systematic-review`) holds the
question, the eligibility criteria (`include` and `exclude`, each with an id), the
sources and the search strategies. `design lock` opens the `design-lock` gate; once a
human answers it, `design/lock.jsonl` records the locked version and its hash. A
change to a locked protocol is an amendment (`design amend`, human-gated). A modified
protocol refuses every `sr` writer before anything is written.

`sr protocol check` runs the strategies through the search leak check and shows the
result before any query is sent. A refused query is never retried around.

## Search rounds and the corpus

`sr search` runs the next round of the locked strategies through the search registry and
the fan-out, under the run's network mode. Each round writes `sr/rounds/<n>/`
(`hits.jsonl`, `records.jsonl`, `round.json`) and commits a `sr.retrieval` event only when
it is complete. A round that crashed before its commit is ignored. `sr records` lists the
corpus of committed rounds in round order.

Each source is read one page per strategy (see R3). A source with more hits than the page
is marked `capped` in the round and in the PRISMA report.

## Dual screening and kappa

`sr screen prepare --stage title-abstract|full-text` writes one batch
(`sr/screening/<stage>/<batch>/batch.json`) and one prompt per screener under `prompts/`.
It dispatches nothing. Each screener is a separate dispatch,
`scieflow agent run --role research.sr-screeners`, and writes only its own output file
(`sr/screening/<stage>/<batch>/<agent>.json`). `sr screen import` reads both outputs
together, after every check passes, and commits the `sr.screening_decision` events.
Screener output is validated against closed sets: record ids, `include` or `exclude`,
and criterion ids.

`sr kappa` computes Cohen's kappa between the two screeners from the committed decisions.
It measures agreement between two screeners, not accuracy against truth. Fleiss' kappa is
not computed (see "What it does not do").

## Blinded conflicts and adjudication

`sr conflicts --stage <stage>` writes the blinded conflicts of the stage to
`sr/adjudication/<stage>/<gate-id>/` and opens the `sr-adjudication` gate. Conflicts carry no
screener name, no decision and no reason of either screener. A human records each decision
with `sr adjudicate <slug> <gate-id> <record-id> --decision include|exclude --rationale …
[--reason <criterion-id>]`. The command is human only and refuses inside a dispatch; it has no
`--as-agent`. The adjudicated record joins the flow with its own exclusion reason.

## Verify and PRISMA

`sr verify` is a read-only integrity check of the committed ledger: events against files,
batch and round identities, and the invariants of each stage. It exits `1` with the rule
that failed; warnings never fail it. `sr prisma` runs `verify` first, then derives the
PRISMA 2020 flow (identified, duplicates, screened, excluded by stage and by reason, sought,
assessed, included, and round-by-round counts) from the events. `--write` writes
`sr/prisma/flow.json`, `flow.md` and `checklist.md`. Mermaid text is the only diagram
(no image rendering).

## Appraisal: risk of bias and GRADE

`sr rob init` writes one unjudged worksheet per included study under `sr/rob/`; existing
worksheets are never overwritten. Tool definitions ship in `config/sr/` (`rob2.yml`,
`robins-i.yml`, `quadas-2.yml`, `grade.yml`) and are loaded strictly. Worksheets are
single-assessor: a person writes the judgements and the support text. `sr rob check`
checks them deterministically; `sr rob record` commits the appraisal events. `sr grade`
computes certainty from the recorded levels, and `sr grade table --write` renders
`sr/grade/summary-of-findings.md`. The tool definitions are marked `unverified` until a
human checks them against the original papers (R6).

## Events and files

The `sr.*` event types are `sr.retrieval`, `sr.dedup_decision`, `sr.screening_decision`,
`sr.adjudication`, `sr.appraisal` and `sr.committed` (the commit that makes an operation
count). Events carry ids, decisions and levels from closed vocabularies. They never carry
record text, a query, or the reasons of a screener.

Files under the run: `design/` (the design, its lock), `sr/rounds/`, `sr/screening/`,
`sr/adjudication/`, `sr/reports/` (full texts or their extracts, placed by the user),
`sr/rob/`, `sr/grade.yml`, `sr/grade/`, `sr/prisma/`. Every path is built from validated ids,
and symlinks are refused where files are read or written.

Agents never write these files or emit `sr.*` events themselves; the commands do.

## Data protection

Search goes only through the registry's gateway and the fan-out. The Gateway's network
rules and the run's data level (`L2` runs reach no live host and no live provider) apply
unchanged. Screener dispatches are ordinary `agent run` dispatches, so the dispatch
refusal for declared L2 data and the sandbox apply. Record text is untrusted: it is fenced
in prompts and printed through the JSON sanitiser. Synthetic fixtures only in the tests.

## Residual limits

These are stated, not hidden. Each is also in the coordinator skill.

- **R1 Screener independence is procedural.** Both screeners can read the run folder. Identical
  prompts, separate output files, the prompt rule and the dispatch-evidence check reduce this,
  but do not exclude it. A per-dispatch sandbox mask is follow-up F3.
- **R2 File-based integrity.** `sr verify` detects disagreement between events and files. An
  actor that rewrites outputs, commits and events coherently is not caught, and the interactive
  coordinator can answer a human-only gate on the CLI (true for every `requires_human` gate).
  The dispatch heuristic stops dispatched agents only.
- **R3 One page per source.** Identification is incomplete when a source has more hits than one
  page. `capped` makes this visible in the round and in the PRISMA report. Pagination is F1.
- **R4 Search cache.** A living update inside the Gateway's search cache TTL returns the cached
  answer and can find nothing new. Check the cache age before reporting "no new records". A cache
  bypass for `sr search` is F2.
- **R5 Leak check on strategy queries.** Boolean or numeric strategy syntax can be refused by the
  search leak check. `sr protocol check` shows this before locking. It is never bypassed.
- **R6 Tool definitions unverified.** RoB 2, ROBINS-I, QUADAS-2 and GRADE labels were written from
  the cited papers offline. They are marked `unverified` until a human checks them.
- **R7 Agents as screeners.** Agent screening accuracy is not established. `sr eval` against a
  human calibration sample measures it, and dual screening plus human adjudication is the safeguard.
  ScieFlow does not certify an agent as a screener.
- **R8 PRISMA scope.** The flow covers database searches. "Other methods" (citation searching,
  websites) are not recorded yet: `scieflow research snowball` exists but writes no `sr.*` events (F5).

## What it does not do

No meta-analysis or pooled effect and no forest plots (F6). No pagination beyond one page per
source per strategy. No data-extraction forms. Single-assessor RoB and GRADE only (F7 would add
dual assessment). No agent adjudicator, ever. No sandbox mask between screeners yet (F3). No
automatic full-text retrieval: the user or existing tools place reports in `sr/reports/`. No PRISMA
image rendering. No registry submission. No web panel yet (F4; the CLI is complete). No Fleiss'
kappa.
