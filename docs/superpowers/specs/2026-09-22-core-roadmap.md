# ScieFlow core roadmap — service core, web app, scite, analysis, manuscripts

## Context

ScieFlow today is a **file-and-CLI system steered by a coordinating agent**. It works because a
human sits in a terminal: agents dispatch as blocking subprocesses, run state is YAML the agent
edits by hand, history is prose in `log.md`, approvals happen in chat, and provenance
(`[run:<id>]`, `[data:<id>]`, DOI) is a convention nobody parses. The analysis layer ranks runs
by one metric and draws one PNG; the paper pipeline produces LaTeX whose numbers are
spot-checked by eye.

The goal — agent-driven research that **delivers analyzed data and trustworthy draft papers**,
controllable from a **local web app**, integrated with **scite** and other services — needs a
core that is observable, controllable and machine-checkable. Nothing above the foundation can be
built well without it: a web app cannot render prose, cancel a blocking call, or answer an
approval that only exists in a chat window.

**Decided with the user:** scite on both tiers (public works now, a Pro key upgrades it
automatically — Pro wins when present); web app v1 = **full control**, including **headless
coordinators with a per-run autonomous option**; FastAPI + htmx; **localhost only**;
**foundation first**; analysis gaps = statistics, figures + tables, dataset package, cross-run
comparison; draft gaps = submission + revision, claim-evidence binding, **quality benchmarked
against target-journal norms**; claim gate = **numbers hard, citations soft, retracted sources
block**; drafting pair = **codex-paper + claude-paper** writing and cross-reviewing, with the
user free to pick any provider (agy included), **model, effort and tasks per role**;
integrations after scite = **Zenodo, Overleaf/git, a ScieFlow MCP server**, plus candidates below.

**Model policy.** The July memory "Fable-only, no model switching" predates the Codex profiles
and rejected *automatic* routing. This plan keeps that line: **the user chooses provider, model
and effort per role; ScieFlow never picks or downgrades a model by itself.** Update that memory
when execution starts.

---

## What the exploration found (the constraints this plan answers)

- **No service seam.** `config.repo_root()` reads `Path.cwd()` (`core/config.py:28`); lifecycle
  code (`status`, `budget`, `checkpoint`, `sfx_init`, `validate`) lives in `scripts/` behind
  `sys.exit`; `status.py` resolves the repo at import time; `menu.launch` ends in `os.execvp`.
- **Dispatch is unobservable.** `agent_run` is one blocking `subprocess.run(capture_output=True)`
  (`core/agent_run.py:135`); no PID, no cancel, no streaming, and on timeout the output is
  discarded (`:139`). Codex calls may block for 180 minutes with no signal.
- **No history, ids or locks.** `advance_iteration` wipes phase state; the slug is the only id;
  zero file locks in core, every writer is non-atomic `write_text`.
- **Research runs have no state schema** — `workspace.describe` returns English strings.
- **The only real job model** is `scripts/remote/jobs.py` (PBS, per workspace) — a good template.
- **Literature records are thin and closed.** `papers.FIELDS` is 8 fields and *raises* on
  anything else (`research/lib/papers.py:6,11`); authors, OA status, type and retractions are
  dropped; dedup and ranking are prose; no cache, no rate limiter; source registry is a
  hard-coded list (`research/cli.py:32`). No scite anywhere.
- **Analysis is minimal.** One `rank_by` metric, min/max thresholds, one plot. No replicates or
  seeds, no statistics (no scipy/statsmodels/pandas), no CSV, no cross-campaign view, no data
  hashes or git SHA in run records. The experiments → paper handoff is entirely manual.
- **Manuscripts:** LaTeX only on a 25-line `article` skeleton; no journal classes, BibTeX only via
  the external `zot`; `target_length` and journal limits are read by nobody; provenance checks
  are advisory or manual at every layer; paper-review uses prose while paper-draft uses JSON.
- **Latent defects found on the way** (fixed in Milestone 0):
  `research.draft-authors: [codex-paper]` has one author, so the dual-draft cross-review always
  degrades to `skipped-quorum`; `citations.py` misses `\parencite`/`\autocite`/`\textcite`;
  `europepmc.py` keeps the *last* PDF link; `docs/research/workflows/paper-draft.md` documents the
  old six-phase design; OpenAlex OA fields are fetched then thrown away.

**Scite facts that shape the design** ([docs](https://docs.scite.ai/introduction)):
`/papers` and `/tallies` are **public, no key**, bulk POST available — tallies give supporting /
contradicting / mentioning / unclassified counts per DOI. A **Pro key** (`Authorization: Bearer`)
adds full-text search with citation statements, citations-as-target/source, collections, journal
index + editorial notices, author stats and the assistant. **Reference Check needs a separate
paid license**, not standard Pro. Limits vary by account — read `RateLimit-*` headers, never
hard-code. Retraction data is free through Crossref's REST API (`update-to` relations, Retraction
Watch since 2023), which lets a reference audit work without any scite license.

---

## Architecture target

```
            browser (htmx)      terminal (CLI, menu)      agents (skills)      other apps
                 │                     │                        │                  │
                 └──── HTTP /api/v1 ───┴────── service layer (scieflow.core.service) ──┘
                                              │
     ┌──────────────┬──────────────┬──────────┴──────┬───────────────┬────────────────┐
  Project ctx    run state       job runner        gates          integrations     analysis /
  (explicit     (status +       (agent, campaign, (approvals as   (scite, crossref, manuscript
   root)         events.jsonl,   sync, remote)     data)           zotero, …)       products
                 ids, locks)
```

One rule: **every action exists once, as a service function**. The CLI, the TUI menu, the agent
skill JSON and the HTTP API are all thin callers. Terminal-only behaviour (`execvp` handover)
stays as an option for humans, never on the service path.

---

## Milestone 0 — correctness fixes (small, first)

- Draft authors: add a `claude-paper` profile to `config/agents.yml` (mirrors `codex-paper`) and
  set `research.draft-authors: [codex-paper, claude-paper]` in `config/defaults.yml`, so the two
  drafts are written by different model families and actually cross-review each other.
- `research/citations.py:17` — match the full `\*cite*` family (reuse `scripts/nblm/claims.py:18`).
- `research/search/europepmc.py:12-14` — first PDF wins (`break`), as `nblm/resolve.py` already does.
- `research/search/openalex.py:20` — keep `is_oa`, `oa_status`, license, type, authors.
- `docs/research/workflows/paper-draft.md`, `docs/research/architecture.md`,
  `docs/research/configuration.md` — align with the current skill and defaults.
- `experiments/model-routing.yaml` `as_of` is stale by its own rule — re-verify.

## Milestone 1 — the service core (foundation)

**1.1 Explicit project context.** `scieflow.core.project.Project(root)` carries the repo root,
config loaders and workspace paths. `repo_root()` stays as the CLI's default constructor; all new
code takes a `Project`. Tests stop depending on `monkeypatch.chdir`.

**1.2 Lifecycle into the package.** Move `scripts/{status,budget,checkpoint,sfx_init,validate}.py`
into `scieflow.core.run` as importable functions (no `sys.exit`, no import-time I/O); the
scripts become shims using the existing `core/legacy.py` pattern so old chats keep working.

**1.3 Safe writes.** One `store.write_yaml(path, data)` = file lock + temp file + `os.replace`,
round-trip ruamel where comments matter. Apply to status, budget, remote jobs, agent config,
news config. `filelock` moves from the news extra into core.

**1.4 Run identity and structured state.** Each run gets a stable ULID `id` in `status.yml`
(lazily added to existing runs; slug stays the path). Research runs get a declared schema
(`schemas/status-research.yml`). `workspace.describe` returns structured fields
(`kind, phase, phase_state, iteration, stopped_reason, updated` as a full timestamp) and renders
English only at the edge.

**1.5 Event log.** `workspace/<slug>/events.jsonl`, append-only, one schema'd event per line:
`{id, ts, run, type, actor, data}`. Types: run.created, phase.started/done/failed, iteration.advanced,
job.queued/started/output/finished/cancelled/timeout, gate.opened/answered, checkpoint,
integration.call, sync.*. Status stays the snapshot; events are the history the dashboard
replays. `scieflow run events <slug> [--follow] [--json]`; `scieflow run log <slug> <type> …`
gives agents a structured alternative to prose `log.md` (which stays).

**1.6 Job runner.** `scieflow.core.jobs` generalises dispatch: `Job{id, kind (agent|campaign|
sync|remote|integration), run, argv, cwd, state, pid, started, finished, exit_code}` persisted as
`workspace/<slug>/jobs/<id>.json` with output **streamed line by line** to `jobs/<id>.log`.
`Popen` in its own process group → real cancel; timeout keeps everything already written.
`agent_run.main` becomes a blocking wrapper with identical exit codes (124 on timeout), so skills
and old chats are unaffected. Per-project concurrency limit; reconcile by PID liveness on
restart. The remote PBS ledger (`scripts/remote/jobs.py`) is adapted as `kind: remote`.

**1.7 Gates — approvals as data.** Every human decision the protocols require (campaign approval,
outline approval, run staffing, claim-check consent, sync/upload consent, external-provider
sharing) becomes `workspace/<slug>/gates/<id>.json`: question, options, attached files, state,
answer, who, when. Agents call `scieflow gate open …` then `scieflow gate wait <id>`; the human
answers in the terminal (`scieflow gate answer`) or the browser. This is what lets a coordinator
run headless while the human stays in control.

*Autonomous runs* (`approval: autonomous`, today's mode): the user's approval of `goal.md` —
question, scope, budget — lets the coordinator answer **in-scope** gates itself; each such
answer is recorded with `actor: agent` and a rationale, and shown on the timeline. Gates marked
`requires_human` always block, in every mode: anything leaving the approved scope, tier
promotions, uploads and external sharing (DVC, scite collections, Zenodo, Overleaf), spending
beyond budget. The gate kinds and their `requires_human` flag are data
(`schemas/gates.yml`), not agent judgement.

*Budgets enforced in code.* Headless and autonomous runs mean nobody watches the terminal, so
budget limits move from agent discipline into the job runner: a dispatch or campaign job is
refused (and a checkpoint written) when a budget dimension is exhausted, and wall time is
measured by the runner, not reported by the agent.

**1.9 Staffing per role — provider, model, effort, tasks.** Today model and effort belong to an
agent, so per-task tuning needs extra profiles (`codex-paper`, `codex-review`). Assignment
entries become optionally rich, backwards compatible:

```yaml
assignments:
  research.draft-authors:
    - {agent: codex, model: gpt-6-astra, reasoning: xhigh}
    - {agent: claude, model: claude-opus-5, reasoning: extended-thinking}
  research.reviewer: codex-review          # plain strings keep working
```

`agent_config.resolve_data` merges role-level overrides over agent defaults with source
attribution; `validate` still enforces tiers, so choosing **agy for a primary-only role**
(drafting, reviewing) goes through the existing explicit, per-role `--promote` exception, which
the menu and web app offer as a visible choice with its consequence stated. The web app's
staffing screen shows, per role: which tasks it performs, provider, model, effort — for all
projects, one workspace, or news (the scope model already built).

**1.8 Service layer.** `scieflow.core.service` — list/get/create runs, start workflow, dispatch,
cancel, answer gate, plan/apply agent config (reusing `agent_configure.Op → Plan → diff`),
sync status, events, papers, artifacts. CLI, menu and `menu --json` are refactored onto it.

## Milestone 2 — local web app (FastAPI + htmx), full control

- `scieflow serve [--port]` → uvicorn on **127.0.0.1 only**, random per-session token in the
  printed URL (Jupyter-style), then a cookie; CSRF token on every POST; no CORS; refuses a
  non-loopback bind. Remote use = SSH tunnel / Tailscale (documented).
- Pages: **Dashboard** (runs, phases, budget gauges, open gates); **Run** (timeline from events,
  gates to answer, jobs with live logs over SSE, artifact browser with md/pdf/image preview,
  notebook); **Start** (workflow wizard from `menu.WORKFLOWS`, scope-first staffing);
  **Agents** (plan → diff → apply); **Literature** (paper store with scite badges);
  **Experiments** (campaigns, stats, figures, cross-run); **Manuscript** (build, quality report,
  reference audit, claims); **Integrations** (tier detected, quotas); **Sync & chats**.
- `/api/v1` JSON mirrors the service layer with the OpenAPI schema FastAPI generates — the
  integration surface for other apps.
- Full control = the browser can start a coordinator **headless** as a job
  (`claude -p` / `codex exec` with the workflow prompt); its questions arrive as gates. Per run,
  the user picks *gated* (every gate waits) or *autonomous* (in-scope gates answered by the
  coordinator and logged; `requires_human` gates still wait).
- **Open-gate notifications** — a headless run waiting on you is useless if you don't know:
  a desktop notification from the browser tab, plus an optional push channel (ntfy or e-mail)
  as a small integration.
- New extra `web` (fastapi, uvicorn, jinja2, sse). The news GUI keeps running and is linked;
  folded in later.

## Milestone 3 — integration layer + scite

**3.1 Framework** `scieflow.integrations`: `Provider` protocol (`name`, `capabilities()`,
`detect_tier()`), deny-by-default `config/integrations.yml` (the proven `remotes.yml` /
`notebooklm.yml` pattern), credentials from environment / `.env` only (reuse the loader in
`scripts/dvc_sync.py:35`, moved to core), one HTTP client with rate limiting that honours
`RateLimit-*` and `Retry-After`, jittered backoff, on-disk cache with TTL, a call/quota ledger,
and an `integration.call` event per request. Search sources become a registry instead of the
hard-coded list.

**3.2 Paper store.** A project-level store keyed by DOI (normalised title fallback) that merges
records from every source and holds enrichment in an open, namespaced `signals` block
(`signals.scite`, `signals.crossref`, `signals.openalex`) while the 8 core fields stay strict.
Runs keep their own *selection* of papers. Dedup and the lit-review consensus/disputed rules move
from prose into code. `scieflow research papers add|list|enrich <slug>`.

**3.3 scite provider — tier `auto`: Pro if a key is set and a probe succeeds, else public.**

| Capability | Public | Pro key | Reference-check license |
|---|---|---|---|
| Tallies per DOI (bulk) → contested / well-supported flags, ranking signal | ✓ | ✓ | ✓ |
| Paper metadata enrichment | ✓ | ✓ | ✓ |
| `scieflow research search scite` — full-text + citation-statement search | | ✓ | ✓ |
| Citation statements for a DOI → evidence for claim checks | | ✓ | ✓ |
| Journal index + editorial notices → journal selection, audit | | ✓ | ✓ |
| Collections sync of a run's selected papers (opt-in) | | ✓ | ✓ |
| Reference Check on the built manuscript | | | ✓ |

Every surface shows which tier produced a number. A new AGENTS.md rule makes scite, like the
other sources, reachable only through `scieflow research …` commands. Scite's MCP server is
documented for interactive coordinator use; the pipeline itself uses the HTTP client so results
are cached, reproducible and testable offline.

**3.4 Committed providers after scite** (each a `Provider` under 3.1, each gated where it
publishes anything):

- **Crossref retractions** (free) — backbone of the public-tier reference audit.
- **Zenodo** — deposit the dataset package (M4), mint a DOI, write it into the
  data-availability statement. Sandbox first; publishing is a `requires_human` gate.
- **Overleaf / git** — push `manuscript/` to an Overleaf project through its git bridge so
  co-authors edit there; pull their edits back as a reviewable diff, never a silent overwrite.
- **ScieFlow MCP server** — expose runs, events, gates, papers, claims and artifacts as MCP
  tools/resources on top of the service layer, so Claude Desktop and other agents can query and
  steer ScieFlow. Reuses the HTTP API's auth; read tools by default, write tools opt-in.

**3.5 Candidates found in the overview** (not committed — pick later):

| Candidate | Why it fits ScieFlow |
|---|---|
| OpenCitations (COCI) | Free, open citation graph → forward/backward snowballing for lit-review, which today only does keyword search |
| Semantic Scholar | Influential-citation counts and TL;DRs; a free complement when no scite Pro key is present |
| Unpaywall | Reliable legal OA PDF links → fewer failed source fetches in claim-check (today Europe PMC only) |
| ORCID | Author names, affiliations, ORCID iDs for the submission package instead of typing them |
| Obsidian vault export | You already run a Zotero/Obsidian/NotebookLM research workflow; papers + findings as linked notes |
| LanguageTool (local) | Grammar/style pass on the manuscript without sending text to a cloud service |
| MLflow / W&B export | Mirror campaign metrics into a tracker you may already use for other ML work |
| ntfy / e-mail | Gate and run-finished notifications for headless runs (see M2) |

## Milestone 4 — delivered, analyzed data

- **Replicates and seeds:** campaign `replicates: n`; a `--seed` injected into each stage run and
  recorded; run ids gain `_r<k>`.
- **Statistics** (`scieflow.experiments.stats`, deterministic code, not agent prose): per
  configuration mean / sd / bootstrap CI; comparison against a baseline with effect sizes
  (Hedges' g, Cliff's delta), Welch / Mann-Whitney / paired Wilcoxon, Holm or BH correction;
  written to `stats.json` with method, n and assumptions. The evaluator skill reads it.
- **Tables:** tidy `results.csv` (+ parquet) per campaign; LaTeX `booktabs` and markdown tables
  with CIs.
- **Figures:** publication-quality, sized from the journal profile (column widths), multi-metric
  panels, CI error bars, Pareto fronts; each figure/table gets an id and a provenance sidecar.
- **Cross-run comparison:** `scieflow experiment compare-campaigns` and a project-level metric
  tracker across iterations and related runs (lineage).
- **Reproducibility capture** in the runner: repo and pipeline git SHA, input/reference sha256,
  host and wall time, package list captured at container build, seed.
- **Dataset package builder:** `scieflow package build <slug>` generates the `inputs/` package the
  paper pipeline already expects — validated `manifest.yml` whose artifacts list the producing
  `[run:<id>]`s, generated `processing.md`, results, stats, figures, tables, data dictionary with
  units, checksums, README, CITATION.cff. This removes the manual handoff and joins the `run:` and
  `data:` provenance namespaces. Zenodo deposit is an optional gated integration.

## Milestone 5 — manuscripts you can trust and submit

- **Claim–evidence ledger:** `workspace/<slug>/claims.jsonl` extracted from the `.tex` (reusing
  `scripts/nblm/claims.py`) plus the `% source: [data:<id>]` comments. One resolver for all
  namespaces (`run:`, `data:`, `doi:`, review `M1`, gap `G1`). **Numeric claims are verified
  mechanically** — the number must appear in the cited artifact — replacing the 3–5 claim manual
  spot-check. Literature claims gather scite statements, retraction status and NotebookLM
  verdicts as evidence. **Gate:** a numeric claim with no binding or a value not found in its
  artifact **fails** the draft (rule 8, now enforced); literature claims are **flagged, not
  blocking** — except a claim citing a **retracted** source, which blocks.
- **Reference audit:** Crossref retractions + scite tallies on the public tier; citation
  statements + editorial notices with Pro; scite Reference Check when licensed →
  `report/reference-audit.md` and a gate.
- **Quality against target-journal norms:** journal profiles gain a structured
  `config/journals/<slug>.yml` (limits, section structure, figure/table caps, reference range,
  citation style, class). Norms are derived from a sample of recent papers in that journal
  (OpenAlex by ISSN; Europe PMC full text for structure). `scieflow manuscript check <slug>`
  measures the draft (words per section, figures, tables, reference count and recency,
  cross-references, limits) against the norms, and the adversarial review gains a journal-norm
  rubric; scores are tracked across rounds.
- **Journal templates:** template registry (`article`, `elsarticle`, …) chosen by the profile,
  bibliography style switch, biber, enforced length limits.
- **Submission package:** PDF, source zip, cover letter, highlights, data-availability statement
  (from the dataset package), journal checklist.
- **Revision workflow:** ingest a real decision letter → parse into `manuscript-review` JSON
  (`M1…`, `m1…`) → reuse paper-draft's response machinery (coverage check, edit verification) →
  `latexdiff` tracked changes → next round. paper-review moves onto the same JSON contract.

---

## Order and dependencies

```
M0 fixes ─► M1 service core ─► M2 web app ───────────────┐
                    │                                     ├─► pages light up per milestone
                    ├─► M3 integrations + scite ─┐        │
                    └─► M4 analysis ─────────────┴─► M5 manuscripts
```

M3 and M4 are independent and can run in parallel after M1. M5 needs both (evidence from M3,
dataset package from M4). Of the committed integrations: the **MCP server** follows M2 (it reuses
the API's auth), **Zenodo** follows M4 (it deposits the dataset package), **Overleaf/git** follows
M5 (it syncs the manuscript). Each milestone gets its own spec + implementation plan under
`docs/superpowers/` before code, as the repo already does.

## Verification (per milestone)

- Unit tests for every service function, with `Project(tmp_path)` instead of `chdir`.
- M1: `tests/test_dry_run.py` extended — a full iteration through the service layer with the stub
  agent produces the expected events, a job record with streamed output, and a gate answered
  programmatically; a timeout keeps partial output; cancel kills the process group; concurrent
  writers never corrupt `status.yml`. Staffing: a rich assignment
  (`{agent, model, reasoning}`) resolves with source attribution; plain strings still resolve;
  agy on `research.draft-authors` is refused without `--promote` and accepted with it. Autonomy:
  in an autonomous run an in-scope gate is answered by the agent and logged as `actor: agent`;
  a `requires_human` gate still blocks; an exhausted budget refuses the next dispatch and
  checkpoints.
- M2: FastAPI `TestClient` for every endpoint; token/CSRF/loopback refusal tests; an SSE log test;
  one browser smoke run with the stub agent end to end.
- M3: recorded HTTP fixtures (`responses`, already a dev dependency) for public and Pro scite,
  tier detection (key present + probe OK → Pro, else public), 429 back-off honouring
  `Retry-After`, cache hits; Zenodo against its sandbox fixtures; MCP server tools listed and
  callable over stdio with the stub agent; no network in CI.
- M4: stats against known values (scipy reference results), deterministic figures (image hashes),
  a package built from the denoise pipeline validates against `manifest.schema.json`.
- M5: a manuscript with a planted wrong number fails the numeric gate; a planted retracted DOI
  appears in the reference audit; the journal check flags an over-length abstract.
- Existing suite (765) stays green throughout; `scripts/check_legacy.sh` keeps passing.
