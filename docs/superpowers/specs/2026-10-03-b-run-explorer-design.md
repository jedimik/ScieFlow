# The run explorer — design (programme item B)

**Status:** design approved in conversation 2026-10-03; awaiting spec review before planning.
**Position:** A1 → C → D (PR #7) → **this (item B)**. B2 (DVC-archived browsing), and F–K from
[Web/CLI coverage](2026-10-03-web-cli-coverage.md), remain.

## Problem statement

Two complaints, measured rather than assumed.

**The run list reads far too much.** `dashboard()` calls `service.list_runs(project)` — which uses
the cheap `workspace.describe()`, reading only `status.yml` and `config.yml` — and then loops
`service.run_detail(project, slug)` once per run. `run_detail` reads that run's status, budget,
`workspace.describe` again, open gates *with proposal previews*, its job list, and **its entire
event log**. All of that to render two columns: budget left, and stopped. `describe()` already
returns `stopped`. The cost is O(runs × events) to display O(runs) small numbers.

**The run page makes you click away to answer basic questions.** It already has nine sections —
charter, conversation, phases, budget, actions, gates, jobs, timeline — and links out to drafts and
files. But four questions a researcher asks constantly have no answer on it: what this run has
actually produced, where the manuscript stands, who contributed what, and where the time and budget
went.

There is no summary, overview or inventory function anywhere in `src/scieflow/core/`. This is a new
seam, not a rearrangement of an existing one.

## What this is not

Not a filter/sort/search surface. That was considered and rejected: this workspace holds one run,
and designing pagination for a scale that does not exist is machinery with no caller. If run counts
ever make finding one hard, that is a separate, later item — and the cheap list seam below is what
makes it affordable then.

## Decisions taken (user, 2026-10-03)

1. **Fix the list cost and add a richer single-run view.** Both, as one item.
2. **All four overview panels** are wanted: what exists on disk, where the manuscript stands, who
   contributed what, and spend/progress over time.
3. **Approach A — live composition.** Compose readers that already exist, per page load. A derived
   summary file (approach B) was **rejected**: it adds a cache that can drift from disk and needs
   invalidating at every writer, and this codebase derives rather than stores — item D's entire
   design rests on "the repo is derived, never authoritative; disk is the truth". Lazy client-side
   panels (approach C) were rejected as client-side complexity in a deliberately server-rendered
   app.
4. **One compact band at the top of the run page**, not a separate `/runs/<slug>/overview` route.
   The complaint was having to click away; another page is another click. The nine existing sections
   stay below, as detail.

## Architecture — two seams, opposite constraints

Treating these as one problem is what produced the present N+1.

### Seam 1 — the list stays cheap

`dashboard()` stops calling `run_detail` entirely. `stopped` comes from `describe()`, which it
already has. Budget remaining comes from one additional small read per run.

Per-run cost becomes **three small YAML reads and no event-log read at all**: the two
`describe()` already does (`status.yml`, `config.yml`) plus one for budget.

This is a strict reduction. It introduces no abstraction, and it is separable from everything else
here — it could land alone.

### Seam 2 — one new overview read

`service.run_overview(project, slug) -> dict`, composing:

| Panel | Source | Already exists |
|---|---|---|
| Inventory | `drafts.agents`, `drafts.sections`, `drafts.rounds`, `drafts.round_sections` | yes |
| Manuscript | `provenance.points` | yes (item D) |
| Attribution | `curation.read` | yes (item C) |
| Progress | `events.read` | yes |

One function, in the service layer, because that is where this project keeps every action exactly
once.

### Read-cost budget — a constraint the tests pin, not an aspiration

- **List page:** O(runs) × three small YAML reads (`status.yml`, `config.yml`, `budget.yml`).
  Zero event-log reads, zero gate reads, zero proposal previews, zero job listings.
- **Run page:** one run, target **under ~0.2s** for the band.

### Four hard constraints

1. **`run_overview` must never call `provenance.sync` — only `points()`.** The drafts page runs a
   full sync on every GET, measured at **0.81s** of git subprocesses on a 20-round / 4-agent /
   6-section run even when nothing changed. That is a recorded, deferred cost and must not be
   replicated on a second page. `points()` alone measured **0.06s**.
2. **No summary or cache file.** Recorded here so it is a decision, not an omission a later session
   "fixes".
3. **Read-only.** No new route, no POST, therefore no `MUTATING_PATHS` entry, no `SAMPLES` case and
   no CSRF surface. The web footprint is one band's worth of rendering on an existing GET.
4. **`run_overview` never raises for an ordinary state.** Same contract as `manuscript_history`: it
   catches the typed errors its sources raise and reports a reason per panel. A run mid-phase, with
   no manuscript, or written by an older ScieFlow renders stated reasons — it does not fail the page.

## The four panels

Each specifies its source, what it shows, and how it degrades. Degradation is not an afterthought
here: most runs will be missing at least one source most of the time.

### 1. What exists on disk

**Source:** `drafts.agents(ws)`, `drafts.sections(ws, agent)`, `drafts.rounds(ws)`,
`drafts.round_sections(ws, n)`; `findings/` and `gaps/` are directory listings.

**Shows:** counts, with the agent names the run actually used — never a hardcoded list. Findings and
gaps per agent; drafts as agents × sections; merge rounds present; review rounds present.

**Degrades:** a run before Phase 3 has no `manuscript/drafts/` at all. The panel says "no drafts
yet" and names the phase, rather than rendering zeros that read like failure. A directory that
exists but is empty is distinguished from one that is absent.

### 2. Where the manuscript stands

**Source:** `provenance.points(ws)` only. Never `sync`.

**Shows:** the current merge round; which sections are present in it against which sections the
agents drafted; and which files the latest merge changed, from the two most recent `merge_N` points.
Links to the workbench for the full diff rather than reproducing it.

**Degrades:** three distinct states, each stated plainly rather than collapsed into one:
`git` absent (`provenance.available()` false); the repo not yet created (no merge round has
happened and the workbench has never been opened); and a run with artifacts but no points yet. The
last of these is why the panel must not sync — the honest message is "history appears after the
first merge round or workbench visit", not a 0.8s stall to create it.

### 3. Who contributed what

**Source:** `curation.read(ws)` → `blocks`, each `{id, kind, text, agent, section, round}`.

**Shows:** per agent, how many kept passages survived into the current curation document, and from
which sections.

**Degrades, and this is a real gap rather than a hypothetical:** a block of `kind: "mine"` carries
**no agent and no section** — by design, those are the researcher's own words, which "claim no
provenance". So per-agent counts **cannot sum to the whole manuscript**, and the panel must show the
`mine` count explicitly as unattributed rather than silently omitting it. A panel that quietly
dropped them would misrepresent how much of the paper is the researcher's own writing — the opposite
of what an attribution panel is for.

### 4. Spend and progress over time

**Source:** `events.read(ws)`. Every event carries `ts` (ISO, millisecond precision, UTC), `type`,
`actor` and `data`. The closed vocabulary already supplies everything needed — **no new event type
is required**, which matters because `events.TYPES` is a closed frozenset:

- `budget.recorded` — spend, over time
- `phase.pending` / `started` / `done` / `failed` — phase durations
- `gate.opened` → `gate.answered` — where a run stalled waiting on a human
- `checkpoint`, `iteration.advanced` — loop progress

**Shows:** spend per phase rather than only the current remaining fraction, and the longest gate
stall. This is the one panel that aggregates rather than counts.

**Degrades:** a run with a truncated or absent event log shows what it can and says the log is
incomplete. Timestamps are UTC on disk; rendering must not imply local time it cannot know.

**Cost note:** this is the only panel that reads the whole event log, and it is the reason seam 1
must not. On the run page, one log for one run is acceptable; multiplied across a list it is the
present defect.

## Failure and degradation

- Every panel fails independently. One unreadable source degrades one panel, never the page.
- `run_overview` returns a per-panel reason string rather than raising, so the template needs no
  error branching beyond rendering the reason.
- A corrupt YAML in one source is a stated reason, not a traceback.
- There is **no `StrictUndefined`** configured anywhere in `src/scieflow/`, so a missing template key
  fails quietly through `Undefined.__getattr__`. Tests must therefore assert on rendered output, not
  merely on the absence of an exception.

## Testing

Prior art to follow: `tests/web/test_provenance_panel.py` for a read-only panel on an existing page,
and `tests/core/test_service.py` for the service-layer read.

- **The list page's cost is pinned by a test that counts reads**, so the N+1 cannot return silently.
  This is the single most important test here: the defect being fixed was invisible for exactly as
  long as nothing counted.
- `run_overview` on a fresh run, a mid-phase run, a run with drafts but no merge, and a complete run.
- Each panel's degradation path, asserted on rendered output.
- `kind: "mine"` blocks are counted as unattributed and visibly reported — falsified by a test that
  fails if they are silently dropped.
- `run_overview` does **not** call `provenance.sync` — pinned by monkeypatching `sync` to raise, which
  fails if anything reaches it.
- `git` absent degrades to a stated reason, tested rather than skipped.
- Every new handler path is `def` (`tests/web/test_async_routes.py` enforces this globally).
- An adversarial agent name or section name renders escaped; nothing reaches the template via `|safe`.

## Out of scope

Filter, sort, search and pagination (see *What this is not*). DVC-archived run browsing — that is
B2, split out by decision 4 of the coverage record. Fixing the drafts page's sync-on-every-GET: real,
measured at 0.81s, and recorded as a follow-up, but it is a question about *when* sync should run and
belongs with whoever answers that. Any new event type. Any mutating action: this item adds reads only.
