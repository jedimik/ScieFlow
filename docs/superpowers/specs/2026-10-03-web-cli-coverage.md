# Web/CLI coverage — what "work with ScieFlow as a web application" requires

**Written:** 2026-10-03, from a measured comparison of the CLI surface against the web surface.
**Status:** scope record, user-confirmed. Not a design for any single feature.

## Why this document exists

The programme has been running against the A–E item table in
[`2026-09-24-web-control-a1-design.md`](2026-09-24-web-control-a1-design.md). **That table is not
the roadmap.** It was A1's own scoping of what should follow A1, and A1 was *run control* — so the
table only ever covered the research-run surface. Finishing A, C, D, B and E yields a complete
web surface for *driving a run*, and leaves two entire CLI modules and a second GUI outside the
browser.

This was noticed by the user, not by the plan, after a backlog had already been written from that
table. The purpose of this document is to make the real target explicit so no future session
inherits the A–E table as if it were the goal.

## The goal, in the user's words

> Anything I was doing in CLI can also be done via the new web application. Basically, I want to
> work with ScieFlow as a web application — with buttons, chats. But also an option which
> simulates CLI chat, if needed in some cases.

Three requirements, not one:

1. **Parity.** Every CLI capability is reachable from the browser.
2. **Affordance.** The primary mode is a graphical one — buttons and forms — plus the
   conversational surface that already exists.
3. **An in-browser CLI.** A terminal-like surface for cases the graphical one does not serve.

## Measured coverage, 2026-10-03

CLI groups from `uv run scieflow --help`; web routes from `src/scieflow/web/pages.py` and
`api.py`.

| CLI group | Web surface today | Status |
|---|---|---|
| `run` | `/`, `/runs/{slug}` and its sub-routes, SSE | covered |
| `gate` | `/runs/{slug}/gates/{gate_id}` | covered |
| `agent` | `/agents` — **role assignment only** | partial |
| `workspace` | the dashboard lists runs; no `index`, no `doctor` | partial |
| `experiment` | **none** | gap |
| `research` | **none** | gap |
| `news` | a separate app at `src/scieflow/news/gui/app.py` | outside `scieflow serve` |
| `chats` | none | see below |
| `menu`, `serve` | not applicable | — |

**The `experiment` gap was checked rather than assumed.** The only matches for
`experiment|campaign|sweep` under `src/scieflow/web/` are budget counters — `max_experiment_runs`
on the Start form and `experiment_runs` as a spend field. Campaigns, sweeps, metrics and reports
have no browser surface. Likewise `research`: no literature search, no validation, no Zotero.

## What this adds to the programme

Beyond the existing A–E items, with no design yet for any of them:

- **F — agent configuration in the browser.** Per-agent field edits such as `timeout_min`, and
  `--promote` exceptions. `docs/web.md` currently records these as "still `scieflow agent
  configure`", which reads as *not yet* rather than *never*.
- **G — workspace health in the browser.** `workspace index` and `workspace doctor`.
- **H — the experiments module in the browser.** Campaigns, sweeps, metrics, reports.
- **I — literature research in the browser.** Search, validation, citations, Zotero.
- **J — news inside the main web app**, rather than a second GUI the user must start separately.
- **K — an in-browser CLI surface.** The third requirement above.

## How K changes the "deliberately CLI-only" list

`docs/web.md:495` lists four things as terminal-only *on purpose*. An in-browser CLI forces each
to be re-examined, because for three of them **the objection was never that a terminal is
privileged — it was that a button or form is the wrong affordance.** A terminal in a browser is
the same affordance as a terminal.

- **`--promote`** exists to be deliberate and explicit. Typing it in a terminal preserves that
  friction *better* than a button would. K plausibly resolves this item rather than violating it.
- **DVC uploads** need consent before gigabytes move. A terminal shows the same prompt the CLI
  shows. Rule 15 is satisfied by the prompt, not by the surface.
- **`chats push` / `pull`** is the hard one, and splits in two. AGENTS.md **rule 16** forbids an
  *agent* starting a backup or restore on its own initiative — a human typing the command into a
  web terminal is the user's own initiative, so rule 16 is satisfied by construction. What does
  *not* follow is the passphrase: today the user types it into their own terminal, and K would move
  it over loopback HTTP into a browser process. That is a real change in exposure and needs a
  decision, not an assumption. Until it is decided, `chats` stays CLI-only.
- **Per-agent field edits** are item F and are not a permanent exception at all.

## The weight K carries

An arbitrary-command surface in a web app is remote code execution by design. The existing app is
loopback-only, token-authenticated and CSRF-protected on every mutating route, with every handler
`def` and `MUTATING_PATHS` asserted equal to `SAMPLES`. K must extend that model rather than sit
beside it — Origin checking on any socket, no bypass of the existing auth, and an explicit decision
about whether commands run inside the sandbox the rest of the system uses or outside it. K's design
spec is where that is settled; it is not a small feature and must not be treated as one.

## Sequencing

Nothing here reorders the work already in flight. Item D lands first; B, then the split-out B2, then
E, remain as scoped. F through K are added to the programme, each needing its own design spec before
any implementation, in keeping with how C and D were built.

F and G are small, bounded, and close gaps in surfaces that already exist. H, I, J and K are each
large enough that their implementation tickets must come from their own specs rather than be guessed
at now.
