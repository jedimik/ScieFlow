# Manuscript provenance — design

**Status:** design approved in conversation 2026-09-27; awaiting spec review before planning.
**Position:** A1 (web control) → C (the draft workbench, merged 2026-09-27) → **this (programme item D)**. B and E remain.

## Why this exists

A run's manuscript has no history. `workspace/*` is gitignored, so nothing under
`workspace/<slug>/` is ever seen by this project's git, and the only sync that exists is
DVC's — `scripts/dvc_sync.py push <slug>`, one zip per run. That preserves bytes and
discards history: you cannot ask what the merging agent changed between rounds, or how far
the current paper has drifted from what one agent originally proposed.

Programme item C made this worse in a useful way. The draft workbench now produces exactly
the artifact that most wants a history — `manuscript/curation/rounds/<n>/<section>.tex`, a
convergence sequence the author steered — and it lands where git never looks.

## What already exists, and what that saves

The research workflow already writes per-agent, and in two places already writes per-round:

| Artifact | Attributed to | Round-structured |
|---|---|---|
| `findings/<agent>.json` | that agent | no |
| `gaps/<agent>.json` | that agent | no |
| `manuscript/drafts/<agent>/<section>.tex` | that agent | no (Phase 3, once) |
| `reviews/<R>-on-<A>.json` | the reviewer `R` | no |
| `review/draft-round-<N>/<reviewer>-on-<author>.json` | the reviewer | **yes** |
| `review/draft-round-<N>/response-<author>.md` | the author | **yes** |
| `review/round-<N>/review.md`, `response.md` | the run | **yes** |
| `manuscript/curation/rounds/<n>/<section>.tex` | the run | **yes** |
| `outline/outline.md` | the run | no |

So the attribution and the round structure this design needs are already on disk. Nothing
new is produced; this reads what is there and gives it a history.

## Decisions taken (user, 2026-09-27)

1. **Provenance inside ScieFlow, not an escape hatch.** The history is for reading and
   auditing — local, no remote, no push, no Overleaf, no collaboration. All sharing
   machinery is out of scope.
2. **Commits cover the agents' own material and each round**, not every file change and not
   only on request.
3. **Read it in the browser**, on the workbench page — the round sequence and a diff
   between any two points. Consistent with the programme's aim of not needing the CLI.
4. **Both round cycles are kept and named distinctly** — `merge_N/` for the workbench's
   merge rounds, `review_N/` for `paper-review`'s draft rounds. They are different counters
   and will not stay in step.

## The layout

The load-bearing decision, and the one everything else follows from. A single rule:
**`main` carries what the run produced collectively; `draft/<agent>` carries what that agent
produced alone.** Round-structured sources go in round directories; once-only sources sit at
the branch root.

```
main (branch)
  outline.md                    ← outline/outline.md
  merge_1/
    sections/*.tex              ← manuscript/curation/rounds/1/*.tex
    curation.yml                ← the curation document as it stood for that round
  merge_2/ …
  review_1/
    review.md                   ← review/round-1/review.md
    response.md                 ← review/round-1/response.md
  review_2/ …

draft/claude (branch)
  findings.json                 ← findings/claude.json
  gaps.json                     ← gaps/claude.json
  sections/*.tex                ← manuscript/drafts/claude/*.tex
  reviews/claude-on-codex.json  ← reviews/claude-on-codex.json
  review_1/
    claude-on-codex.json        ← review/draft-round-1/claude-on-codex.json
    response-claude.md          ← review/draft-round-1/response-claude.md
  review_2/ …

draft/codex (branch)            — the same shape, codex's own material
```

`merge_N/` appears only on `main`: a merge round produces one merged manuscript, not
per-agent output. `curation.yml` is included in it because "which passages were kept, from
whom, at that round" is the provenance most worth having, and it otherwise exists only in
the live file.

**Agent names are never hardcoded.** Branch and directory names come from the agents the run
actually used, discovered from the run's own artifacts — the `drafts/<agent>/` directories,
the `findings/<agent>.json` and `gaps/<agent>.json` filenames, and the `<reviewer>-on-<author>`
pairs. Adding an agent to `config/agents.yml` needs no change here; a run that used one agent
gets one branch. There is deliberately no config key for choosing a different layout: it would
be machinery with one caller.

**Diffs remain single git commands**, because git diffs tree-to-tree rather than only
commit-to-commit:

- `git diff main:merge_1 main:merge_2` — what the merging agent changed between rounds
- `git diff draft/claude:sections main:merge_1/sections` — how the merge altered
  claude's proposal
- `git diff draft/claude:review_1 draft/claude:review_2` — how claude's reviewing changed

## A bare repo, built with plumbing

The repo is **bare**, at `workspace/<slug>/provenance.git`, one per run, and is populated
entirely through git plumbing (`hash-object`, `mktree`, `commit-tree`, `update-ref`). Three
reasons, each of which independently justifies it:

1. **Nothing for an agent to corrupt.** A sandboxed dispatch has the whole run writable. A
   `.git` inside `manuscript/` is something an agent can write into, commit into or destroy,
   and a working tree is something it can leave dirty. A bare repo beside the manuscript is
   object storage with no working tree and no reason for an agent to touch it.
2. **No checkout dance.** This design commits to `refs/heads/main` and to
   `refs/heads/draft/<agent>`. With a working tree that means switching branches — stateful,
   and able to fail halfway. Building trees directly and moving refs is stateless: every
   commit is the same four operations whichever branch it lands on.
3. **No new dependency.** `git` is driven by subprocess, exactly as `jobs` and `sandbox`
   already drive `bwrap` and `latexmk`. No git library.

Verified on this host: git 2.43.0 builds and reads such commits with no working tree.

**`git` becomes a new optional runtime dependency.** ScieFlow currently never shells out to
git, so this needs the degradation shape `latexmk` already has — see *Failure* below.

## One idempotent `sync`, not commit hooks

The artifacts are written by agent dispatches scattered across the whole workflow. Hooking
each writer would mean touching every skill and every service path that dispatches, for a
feature that only reads.

Instead one function, `sync(ws)`, projects the **current** workspace state into trees,
compares each against its branch tip and commits only what changed. It is a pure function of
the workspace, so it is safe to call from more than one place and safe to call repeatedly. It
is called after a successful merge round — so a merge round normally gets its own commit —
and on each workbench page load, which catches everything else.

**Why coalescing does not matter here.** Because rounds are preserved as *paths*
(`main:merge_2`), the history lives in the tree structure rather than the commit graph. If two
merge rounds land in a single commit, both `merge_1/` and `merge_2/` are still present, still
addressable and still diffable. A missed or coalesced sync therefore loses nothing, which
removes the need to engineer around dropped hooks at all.

**The repo is derived, never authoritative.** The files on disk are the truth. Deleting
`provenance.git` costs history only, and the next read rebuilds what the workspace still
supports. This is what makes best-effort committing the right trade rather than a compromise.

## Components

- `src/scieflow/core/provenance.py` — the git layer:
  `REPO_DIR = "provenance.git"`; `available() -> bool`; `ensure_repo(ws) -> Path`;
  `sync(ws) -> dict`; `points(ws) -> list[dict]`; `diff(ws, a, b) -> str`;
  `ProvenanceError`. Private helpers wrap the four plumbing calls.

  **A "point" is a `<ref>:<tree path>` pair** — `main:merge_2`, `draft/claude:sections` —
  carrying a human label, the commit it resolves to and when that commit was made. It is
  the unit the panel lists, the unit `diff` accepts, and the whitelist the query
  parameters are checked against: `a` and `b` must each equal a string `points()`
  returned, compared as whole strings.

  `sync` reports what it did — the branches it touched, the commits it created (empty
  when nothing changed) and the agents it discovered — so a caller can log it and a test
  can assert idempotency without reading the repo.
- `src/scieflow/core/service.py` — `manuscript_history(project, slug)` and
  `manuscript_diff(project, slug, a, b)`; a guarded `provenance.sync` after a successful
  merge round.
- `src/scieflow/web/` — a history panel and a diff panel on `/runs/<slug>/drafts`, driven by
  query parameters on the existing GET route.

**No new mutating route.** Nothing here is a POST, so there is no `MUTATING_PATHS` entry, no
`SAMPLES` case and no CSRF surface. The web footprint is one page's worth of read-only
rendering.

## Write-path safety

- `a` and `b` arrive from the query string and reach `git diff`. They are **whitelisted
  against what `points()` returned** — never passed through raw. Unvalidated, `a` could be
  argument-shaped (`--output=…`) rather than merely path-shaped, which a containment check
  alone would not catch.
- Diff output is capped, the way the compiler log is: an enormous diff is truncated with the
  fact stated, not read whole into memory and rendered.
- Every artifact committed is text the workflow wrote; nothing here interprets it. Diff text
  reaches HTML and is escaped by Jinja like everything else — no `|safe`.
- Nothing is uploaded or transmitted, so AGENTS.md rule 15 does not bind this feature. It
  also does not replace DVC: the archive still carries the bytes.

## Failure and degradation

- **`git` absent** — `available()` is false, the panel says so in plain words, and the
  workbench is otherwise unaffected. This is a supported state and gets a test, not a skip.
- **A sync failure never fails a round.** The round happened and cost budget; losing a commit
  is not worth losing that. The call is guarded and records a closed-vocabulary event —
  `provenance.synced` and `provenance.skipped` join `events.TYPES`.
- **A corrupted repo is rebuilt.** `ensure_repo` probes with `rev-parse`; on failure it
  discards the directory and re-inits, which is safe precisely because the repo is derived.
- **A run with nothing to commit** — no drafts, no rounds — is an ordinary state: `points()`
  is empty and the panel says there is no history yet.

## Testing

Against real git in `tmp_path`, with no mocks; git 2.43.0 is present on this host.

- `sync` twice in a row produces one commit, not two.
- A round's commit diffs against the previous round's showing only the changed hunk.
- Agent branches are discovered from artifacts rather than a fixed list, including a run that
  used a single agent, and a run whose agent names are not in `config/agents.yml` at all.
- `git` missing degrades as described — tested, not skipped.
- A sync failure leaves the round intact, written so that unguarding the call makes it fail.
- Ref validation refuses argument-shaped input (`--output=/tmp/x`) as well as path-shaped
  (`../../etc`).
- The repo deleted mid-run is rebuilt on the next read, with the history it can still derive.
- Diff truncation is pinned, including that the truncation is stated rather than silent.

## Risks

| Risk | Handling |
|---|---|
| Two counters named "round" | Kept distinct as `merge_N/` and `review_N/`; the layout never uses a bare `round_N` |
| A missed or coalesced sync | Costs a commit boundary, never content, because rounds are paths |
| An agent corrupting the repo | Bare, beside the manuscript, no working tree, nothing an agent has reason to write |
| `git` absent on a user's machine | Degrades to a stated reason, as `latexmk` already does |
| Ref injection through the diff query | Whitelisted against `points()`; argument-shaped input tested |
| A large diff rendered into a page | Capped and stated, as the compiler log is |
| The repo diverging from disk | It is derived and rebuilt; disk is always the truth |

## Out of scope

Any remote: no push, no fetch, no Overleaf, no GitHub, no collaboration — the user chose
provenance over sharing, and every one of those is a separate feature with its own consent
questions. Rewriting history. A CLI surface (`manuscript log`/`diff`) — the browser is the
chosen reading surface, and adding a second surface for one feature is unneeded. Replacing
DVC: the archive keeps carrying the bytes, and this carries the history. Programme items B
and E.
