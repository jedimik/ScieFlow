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
| `review/round-N/assignment.json` | `scieflow review assign` | `review-assignment` |
| `review/round-N/<reviewer>-on-<author>.blind.json` | reviewer, pass A | `manuscript-review` (`pass: blind`, or no `pass`) |
| `review/round-N/<reviewer>-on-<author>.evidence.json` | reviewer, pass B | `manuscript-review` (`pass: evidence`) |
| `review/round-N/evidence/bundle.json` | core (`scieflow review evidence`, M1) | `evidence-bundle` |
| `review/round-N/matrix.json` (+ `matrix.md`) | core skeleton + coordinator (M2) | `review-matrix` |
| `review/round-N/decisions.json` | **only** `service.decide()` | `review-decisions` |

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
  Until the M1 integration lands it always says `configured: false`.

## Decisions

`decide(project, slug, round, {cluster: {decision, note?, overrule_reason?, rationale?}}, actor)`:

- `decision` is `accept`, `reject` or `defer`; cluster ids must exist in the
  round's matrix, which must exist and validate. Unknown fields are refused.
- `actor="human"` may decide any cluster. `actor="agent"` may decide only when
  the run's `review_decisions` is `coordinator`, must give a `rationale`, and
  may never decide a blocker.
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

## Gates

`schemas/gates.yml`: `review-decision` (an autonomous coordinator may answer
it, with a rationale, when `review_decisions: coordinator`),
`review-blocker` and `scite-consent` (human only, in every approval mode),
plus the existing `external-sharing` (human only).

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
