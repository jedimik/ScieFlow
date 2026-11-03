---
name: systematic-review
description: Use when the human asks for a systematic or living review. A locked protocol, dual screening by two agents, blinded adjudication, PRISMA flow and checklist, RoB and GRADE worksheets. Coordinator-side protocol; read AGENTS.md first.
---

# Systematic Review

You are the **coordinator**. A systematic review in ScieFlow is a sequence of
`scieflow` commands: the design locks the question, `sr` runs the search, the
two screeners and the adjudication, the appraisal worksheets and the PRISMA
flow. Your job is to run the commands in order, dispatch the screeners with the
right role, wait at the human gates and report what the commands return. Full
reference: `docs/research/systematic-review.md`; CLI: `docs/cli.md` (`scieflow sr`).

## Hard rules

- **Never compute counts or kappa yourself.** Counts, kappa and PRISMA numbers come only
  from the `uv run scieflow sr` commands; never copy a number from a transcript into the
  manuscript without the command that derives it.
- **Never edit `sr/` files or emit `sr.*` events yourself.** The ledger is written by
  the commands. Worksheet judgements (RoB, GRADE) are a human assessor's act: you
  name the worksheet path and ask the user to fill it; you run `check` and
  `record` only when the user asks.
- **Never adjudicate a screening conflict.** `sr adjudicate` is human-only and
  refuses inside a dispatch. A human answers the `sr-adjudication` gate. Show the
  user the blinded conflict list and wait.
- **Screeners never open another screener's files.** Each screener reads only its
  own prompt and writes only its own output file.
- **Dual screening means two distinct agents.** A batch with one screener is not
  a screened batch; `sr screen import` refuses it.
- **Search only through `sr search`** (and `research network` for its mode). Never
  search by hand, never paste records in from memory, never invent a DOI.
- **Ask before loosening the network.** `research network <slug> record` allows
  live requests that are then recorded; set it only when the user agrees. An agent
  may only set `replay` or `offline` (`--as-agent`).
- **Gates are the human's.** Every `requires_human` gate (`design-lock`,
  `sr-adjudication`, `external-sharing`) is answered by the user with
  `uv run scieflow gate answer`, never by you.

## 1. Staffing gate (run configuration gate, before any dispatch)

Run the research run configuration gate from `src/scieflow/research/AGENTS.md`.
The screening role is `research.sr-screeners`; its registry default is `codex`
and `claude`, two different agents.

```bash
uv run scieflow agent show --workspace <slug>
uv run scieflow gate open <slug> --kind staffing --question "Screeners for the systematic review" --option "codex and claude (default)" --option "adjust"
uv run scieflow gate wait <slug> <id>
uv run scieflow agent configure --workspace <slug> --assign research.sr-screeners=codex,claude --yes
```

Record the choice in `log.md`. Two different screener agents are required.

## 2. Protocol: design, lock, check

The review question, eligibility criteria, sources and strategies live in the
run's design (`design/design.yml`, type `systematic-review`, review block).

```bash
uv run scieflow design init <slug> --type systematic-review --title "<title>"
uv run scieflow design check <slug>
uv run scieflow design lock <slug>            # human gate: the user answers design-lock
uv run scieflow sr protocol show <slug>
uv run scieflow sr protocol check <slug>      # shows strategy problems before anything is sent
```

Fill the review block with the user, one criterion at a time. `design lock` opens
a human gate; wait for the user. A locked protocol cannot change: a change goes
through `scieflow design amend`, which is also human-gated.

Before the first search, set the network mode with the user:

```bash
uv run scieflow research network <slug> record   # only after the user agrees
```

## 3. Search rounds and the corpus

```bash
uv run scieflow sr search <slug> --as-agent      # one round of the locked strategies
uv run scieflow sr records <slug>                # the committed corpus, in round order
uv run scieflow sr records <slug> --round 1
```

Exit 3 means a query was refused by the leak check: nothing was sent, and the
refusal is a boundary. Fix the strategy with the user (`sr protocol check` shows
why), never bypass it. A capped source (more hits than one page) is flagged in the
round and in the PRISMA report.

A living update is another `sr search`. The Gateway's search cache can return an
old answer inside its TTL and find nothing new: check the cache age before you
report "no new records".

## 4. Title-abstract screening (two screeners)

```bash
uv run scieflow sr screen prepare <slug> --stage title-abstract --as-agent
```

`prepare` writes one batch and one prompt per screener under `prompts/`; it
dispatches nothing. Dispatch each screener with the screener role, one command
each (sequentially or in parallel):

```bash
uv run scieflow agent run --role research.sr-screeners codex workspace/<slug>/prompts/sr-title-abstract-<batch>-codex.md workspace/<slug>/logs/sr-title-abstract-<batch>-codex.txt
uv run scieflow agent run --role research.sr-screeners claude workspace/<slug>/prompts/sr-title-abstract-<batch>-claude.md workspace/<slug>/logs/sr-title-abstract-<batch>-claude.txt
```

Then import both outputs together, check the progress and the agreement, and
open the blinded conflicts:

```bash
uv run scieflow sr screen import <slug> --stage title-abstract --batch <batch> --as-agent
uv run scieflow sr screen status <slug>
uv run scieflow sr kappa <slug> --stage title-abstract
uv run scieflow sr conflicts <slug> --stage title-abstract --as-agent   # opens sr-adjudication
```

A screener's output is validated against closed sets (record ids, include or
exclude, criterion ids). A failed import is reported with its rule, not fixed by
hand. Repeat `prepare` and the dispatches for each batch until the queue is empty.

Kappa is a measure of agreement between the two screeners, not of accuracy.
Agent accuracy is not established: `sr eval --gold <file> --stage <stage>` against
a human calibration sample is the way to measure it.

## 5. Adjudication (human only)

`sr conflicts` writes the blinded conflicts (no screener name, decision or reason)
and opens the `sr-adjudication` gate. Show the user the gate question and the
record ids. The user records each decision:

```bash
uv run scieflow sr adjudicate <slug> <gate-id> <record-id> --decision include --rationale "<why>"
uv run scieflow sr adjudicate <slug> <gate-id> <record-id> --decision exclude --reason <criterion-id> --rationale "<why>"
```

You never run `sr adjudicate`. When the user has finished, check the state with
`uv run scieflow sr screen status <slug>`.

## 6. Full-text screening

Full texts are not fetched by ScieFlow. The user places each report under
`sr/reports/` (existing full-text tools or the user). Then repeat section 4 with
`--stage full-text`. Exclusion reasons are criterion ids and reach the PRISMA
exclusion counts.

## 7. Appraisal: risk of bias and GRADE

Single assessor. The human judges each study; you never write the judgement.

```bash
uv run scieflow sr rob init <slug> --as-agent    # one worksheet per included study; never overwrites
uv run scieflow sr rob check <slug>              # ask the user to fill the worksheets first
uv run scieflow sr rob record <slug> --as-agent  # only when the user asks; commits the worksheets
uv run scieflow sr grade init <slug> --as-agent
uv run scieflow sr grade check <slug>
uv run scieflow sr grade record <slug> --as-agent
uv run scieflow sr grade table <slug> --write
```

Tool definitions (RoB 2, ROBINS-I, QUADAS-2, GRADE) are marked `unverified`
until a human has checked them against the original papers. Say so in any report.

## 8. Verify, PRISMA and the report

```bash
uv run scieflow sr verify <slug>                 # read-only integrity check; exit 1 names the rule
uv run scieflow sr prisma <slug> --write         # PRISMA 2020 flow, checklist and Mermaid text
uv run scieflow sr status <slug> --json
```

Numbers in a manuscript come from `sr prisma`, never from you. `sr verify` runs
first inside `prisma`. Report the residual limits with the results: one page per
source (R3), the search cache (R4), the leak check (R5), unverified tools (R6),
agent screening accuracy (R7), and "other methods" not recorded (R8). The method,
its files, its events and its residuals are in `docs/research/systematic-review.md`.

## When something refuses

A refusal (exit 1) or a usage error (exit 2) is a finding. Report the rule and the
path or id the command names, fix what the user asks you to fix, and rerun the same
command. Do not work around a refusal by writing the file yourself, by changing the
design under a lock, or by dispatching a third agent to hide a disagreement.
