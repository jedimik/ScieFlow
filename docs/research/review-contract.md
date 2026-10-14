# Review contract (scite-grounded draft review)

This page is the contract between ScieFlow's core and anything that shows or
drives draft review — the web app, the CLI and the research protocols. It was
frozen with milestone M0 (`dev/scite-m0`). After M0 merges, everything listed
here changes **additively only**: new optional fields, new functions, new
event types and gate kinds. A breaking change bumps the schema `version` and is
announced in the changelog of
`docs/superpowers/handoffs/2026-10-13-scite-review-web-handoff.md`.

Design: `docs/superpowers/specs/2026-10-13-scite-review-and-discovery-design.md`.

## File layout

Everything lives inside the run's workspace, `workspace/<slug>/`:

| Path | Written by | Schema |
|---|---|---|
| `config.yml` → `review:` block | `scieflow review configure`, `service.set_review_settings` | see [Settings](#settings) |
| `review/round-N/assignment.json` | `scieflow review assign`; `pairs[].blind_sha256` added by `scieflow review freeze` / `evidence` (M2) | `review-assignment` |
| `review/round-N/<reviewer>-on-<author>.blind.json` | reviewer, pass A | `manuscript-review` (`pass: blind`, or no `pass`) |
| `review/round-N/<reviewer>-on-<author>.evidence.json` | reviewer, pass B | `manuscript-review` (`pass: evidence`) |
| `review/round-N/evidence/bundle.json` | core (`scieflow review evidence`, M1) | `evidence-bundle` |
| `review/round-N/evidence/calls.json` | core: this build's scite ledger rows, free-source lookups, reuse counts | — |
| `review/round-N/evidence/state.json` | core: successful answers, reused by later builds (round diff) | — |
| `review/round-N/matrix.skeleton.json` | core (`scieflow review matrix`, M2): every comment of every valid pass + every bundle flag | — |
| `review/round-N/matrix.json` (+ `matrix.md`) | coordinator; checked and rendered by `scieflow review matrix --check` (M2) | `review-matrix` |
| `review/round-N/decisions.json` | **only** `service.decide()` | `review-decisions` |

**paper-review runs** (M2, ticket 13): when the run's `status.yml` says
`workflow: paper-review`, the reviewed author is `research.submitter` (the
only author in `assignment.json`), the default pool is `research.reviewer`,
and the reviewed text is the manuscript under `manuscript/` (`main.tex` and
its inputs, else the top-level `.tex` files) instead of
`manuscript/drafts/<author>/` — for the prompts, the evidence claims and the
revision prompt alike. The file layout above is unchanged; the same family
and self switches apply.

`N` is an integer ≥ 1. Agent names (`reviewer`, `author`) become file names:
they pass `drafts.check_name` and may not contain `/`, `\`, whitespace, line
breaks or bidi controls, and are never `.`/`..`. A round directory that is a
symlink leading outside the run is not read.

Every schema lives in `src/scieflow/research/schemas/`, has
`$id: https://scieflow.local/schemas/<name>.schema.json`, and its documents
carry `version: 1`. Validate any file with
`scieflow research validate <file> --schema <name>`.

## Service API — `scieflow.core.review.service`

Pure functions over a `Project` and a run slug; no web framework imports.
Readers return `None` (or `[]`) for an ordinary absent state — the run does
not exist, has no review yet, or the round lacks that file — and never raise
for it. Refusals raise a `ValueError` subclass whose message is meant for the
user:

| Exception | Raised by |
|---|---|
| `service.ReviewError` | readers: path-shaped names, a bad round, an unknown pass, a corrupt or off-schema file, a path leading outside the run |
| `settings.SettingsError` | `review_settings`, `set_review_settings`, `preview_assignment` (also for a run that does not exist: settings belong to a run) |
| `assign.AssignmentError` | `preview_assignment` (e.g. a draft with no eligible reviewer — the message names the switch that fixes it) |
| `decisions.DecisionError` | `decide` (nothing was written) |

```python
review_settings(project, slug) -> dict
set_review_settings(project, slug, changes: dict, actor: str) -> dict
preview_assignment(project, slug, changes: dict | None = None) -> dict   # writes nothing
assignment(project, slug, round: int | None = None) -> dict | None       # None = latest round
rounds(project, slug) -> list[dict]
review_pass(project, slug, round, reviewer, author, pass_) -> dict | None
evidence_summary(project, slug, round) -> dict | None
matrix(project, slug, round) -> dict | None
decisions(project, slug, round) -> dict | None
decide(project, slug, round, decisions: dict[str, dict], actor: str) -> dict
blockers(project, slug) -> list[dict]
scite_status(project) -> dict
```

`preview_assignment` and `decisions` are additions to the design's §6 list
(the web needs a live assignment preview and the decisions already made).

### Returned shapes

Golden outputs for the fixture run are in `tests/fixtures/review/golden/`.

- **`review_settings` / `set_review_settings`** — every key of the
  [settings](#settings), effective values.
- **`preview_assignment` / `assignment`** — the `review-assignment` document
  (`preview_assignment` has no `round`): `authors`, `reviewers` (with
  `covers` and `reviews`), `pairs` (`reviewer`, `author`, families, `covers`,
  `self`, `same_family`, `fresh_dispatch: true`), `coverage` and `warnings`.
- **`rounds`** — per round, oldest first:
  `{"round", "assignment": bool, "pairs": [{"reviewer", "author", "blind": bool, "evidence": bool}], "evidence": bool, "scite_status": str | None, "matrix": bool, "decisions": bool}`.
  `pairs` come from the round's assignment; `decisions` is true only for a
  decisions file `decide()` wrote.
- **`review_pass`** — the file as written, with `pass` filled in as `blind`
  when absent.
- **`evidence_summary`** —
  `{"round", "generated", "manuscript_hash", "scite_status", "references": {"total", "by_status": {ok, corrected, retracted, unresolvable}}, "claims", "passages", "abstract_only", "contested", "searches": [{"id", "kind", "source", "results"}], "flags": [...], "blockers": [flag ids], "calls": {"mcp", "rest", "cached", "skipped"}, "quota": int | None}`.
- **`matrix`** — the validated `review-matrix` document.
- **`decisions`** — the `review-decisions` document, or `None` when there is
  none, it does not match the latest `review.decided` event (hand-written or
  edited), or the matrix changed since the decisions were made (stale).
- **`blockers`** — open blockers of the latest round that has a matrix or a
  bundle: `{"round", "cluster": str | None, "reason": "retracted" | "unresolvable", "title", "flags": [ids], "references": [{"doi"?, "key"?, "detail"}], "decision": str | None}`.
  A bundle blocker flag that no blocker cluster covers is listed with
  `cluster: None`. A human overrule holds for the same reference (kind + DOI
  or bib key) in later rounds.
- **`scite_status`** — `{"configured": bool, "tier", "quota_remaining", "last_error", "reason"}`.
  Filled since M1: `tier` is `public` (REST only, no token) or `mcp` (`None` when not
  configured); `quota_remaining` is the MCP calls left today under
  `max_mcp_calls_per_day` (`None` without a day budget); `last_error` is the latest
  failed scite call today, `{"ts", "source", "op", "error"}` (`error` = classification),
  or `None`; `reason` says why scite or its MCP tier is unavailable (`None` when fully
  usable). Never the token.
  Until the M1 integration lands it always says `configured: false`.

## Decisions

`decide(project, slug, round, {cluster: {decision, note?, overrule_reason?, rationale?}}, actor)`:

- `decision` is `accept`, `reject` or `defer`; cluster ids must exist in the
  round's matrix, which must exist and validate. Unknown fields are refused.
- `actor="human"` may decide any cluster. `actor="agent"` may decide only when
  the run's `review_decisions` is `coordinator`, must give a `rationale`,
  may never decide a blocker, and may never change a cluster a human decided.
- **Blockers.** Any decision on a blocker cluster needs a human
  `overrule_reason`. Only `reject` with that reason overrules the blocker
  (`blockers()` drops it); `accept` and `defer` keep it open until a later
  round's bundle no longer flags the reference. `overrule_reason` on a cluster
  that is not a blocker is refused.
- One refused entry refuses the whole batch; nothing is written.
- Decisions accumulate per round. Earlier ones are kept only while the matrix
  is byte-identical (`matrix_hash`); after the coordinator rewrites the matrix
  they read as absent and must be made again.
- The file is written atomically under a lock, then `review.decided` is
  emitted with `round`, `clusters`, `decisions_hash` (sha256 of the file),
  `matrix_hash` and the whole `decisions` payload. Earlier decisions are
  carried over from that event log, not from the file, so a crash between
  the write and the event loses only the decision being made.
- A `decisions.json` that does not match the latest `review.decided` event
  of its round (hash, payload, matrix, and each entry's actor against the
  event that decided it) is ignored by every reader (decision forgery). The
  event log itself is trusted: it is written by core, outside any dispatch
  sandbox.
- The CLI actor is declared like `scieflow gate answer`: without
  `--as-agent` the caller is the user. Agents must use `--as-agent`.

CLI: `scieflow review decide <slug> --round N --decision K2=accept [--note K2=TEXT] [--overrule K1=REASON] [--as-agent --rationale TEXT] --yes`.
`scieflow review status <slug> [--json]` prints `rounds`, `blockers` and
`scite_status`.

## Settings

The `review:` block of `workspace/<slug>/config.yml`, merged over
`config/defaults.yml` `review:` and the built-in defaults:

| Key | Values | Default | Who may set it |
|---|---|---|---|
| `scite` | `off` / `ask` / `approved` | `ask` | human; an agent only `off` |
| `reviews_per_draft` | integer ≥ 1 | 2 | human or agent |
| `allow_same_family_review` | bool | false | human |
| `allow_self_review` | bool | false | human |
| `review_decisions` | `human` / `coordinator` | `human` | human |
| `pool` | `[{agent, covers?: all \| auto}]` or absent | `research.draft-reviewers` | human |
| `matrix_override` | `{reviewer: [authors]}` | `{}` | human |

## Events

Closed vocabulary in `core/events.py`, on the run timeline
(`workspace/<slug>/events.jsonl`): `review.assigned`, `review.pass_done`,
`review.evidence_built`, `review.matrix_ready`, `review.decided`,
`review.blocker`, `scite.quota_low`, `scite.unavailable`. M0 emits
`review.decided`; the producers of the others arrive in M1–M2.
`review.blocker` is emitted when its `review-blocker` gate is opened.
`scite.unavailable` (`{reason, ...}`) is emitted once per run, by the MCP
client on an auth or quota error or by the evidence build when a spent budget
leaves the round at `scite_status: quota`. `review.evidence_built` carries
`backfill: true` for a `scieflow research scite backfill` build.
`review.pass_done` (`{round, reviewer, author, pass, sha256}`) is emitted by
`scieflow review check`, `review.matrix_ready` (`{round, clusters, blockers}`)
by `scieflow review matrix --check`. A pass A changed after it was frozen
emits the free-form `note.review_pass_a_tampered` (`{round, reviewer,
author}`) when pass B is refused for that pair.

## Gates

`schemas/gates.yml`: `review-decision` (an autonomous coordinator may answer
it, with a rationale, when `review_decisions: coordinator`),
`review-blocker` and `scite-consent` (human only, in every approval mode),
plus the existing `external-sharing` (human only).

Core opens the review gates of a round (`scieflow review gates`, also run by
`scieflow review matrix --check`; Python `core/review/decision_gates.py`
`open_gates(project, ws, round)`), only for a matrix that checks:

- one `review-decision` gate per undecided non-blocker cluster, options
  `accept | reject | defer`, `in_scope` only when the run sets
  `review_decisions: coordinator`;
- one `review-blocker` gate per open blocker cluster, options
  `overrule | fixed`, plus a `review.blocker` event
  (`{round, cluster, reason, references: [{doi, key, detail}], gate}`).

Questions start with `[review round N · cluster K · matrix H]` (H = the
first 12 hex digits of the matrix's sha256); `decision_gates.parse(gate)`
returns `{round, cluster, matrix}`. Answering such a gate (`gates.answer`, so
`scieflow gate answer` and `core/service.answer_gate`) first records the
decision through `decide()` and leaves the gate open when that is refused:
`review-decision` → the answer is the decision, the gate note its `note`, an
agent's rationale its `rationale`; `review-blocker` `overrule` → `reject` with
the note as the user's `overrule_reason` (a blank note is refused);
`fixed` → no decision, the blocker stays open until a later round's bundle no
longer flags the reference. Only a gate core opened counts: its
`gate.opened` event has actor `system` and its kind, question and options are
exactly what core writes for that cluster of the current matrix; a look-alike
or edited gate is refused. A gate of an earlier matrix is refused too.
Re-running `open_gates` (serialised per round) withdraws (`gate.withdrawn`)
stale and look-alike gates and decision gates of clusters decided meanwhile,
and opens what is missing. A review-kind gate opened by hand without the
prefix is an ordinary gate and records nothing.

## Revision and merge

`scieflow review revise SLUG --round N --author A [--write]`
(`core/review/revise.py` `revision_prompt`) gives the author only the
accepted clusters that concern its draft — clusters with a comment on that
draft, or flag-only clusters whose flagged reference the draft cites — and
refuses when the round has no decisions recorded by `decide()`.
`clusters_for(ws, round, author)` returns `{accepted, rejected, deferred,
undecided}`. `scieflow review coverage` checks that every accepted id heads a
section (`## K2`) with text under it in `review/round-N/response-<author>.md`;
an author with nothing accepted needs no letter.
`scieflow review merge-check SLUG` exits 1 while `blockers()` is non-empty;
every merge path should refuse on the same condition.

## Fixture run

`tests/fixtures/review/run/` is a complete round to build views against —
copy it to `workspace/<slug>/` of a test project:

- authors `claude-paper` (claude), `codex-paper` (openai), `agy` (gemini);
  extra reviewer `codex-review` (`covers: all`, with
  `allow_same_family_review: true` so it also reviews `codex-paper`);
  `reviews_per_draft: 1`;
- `review/round-1/`: assignment, 6 blind and 6 evidence passes, the bundle
  (one retracted reference, a contested one, an abstract-only passage, a
  novelty hit) and a matrix of 7 clusters covering every comment and flag
  exactly once, with blocker cluster `K1`; no decisions.

## Ownership

`scripts/check_ownership.sh [BASE]` fails when the branch touches files owned
by the web session (`src/scieflow/web/**`, `tests/web/**`) or the K16 files,
and warns when a shared file loses lines.
