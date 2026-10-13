# Scite-grounded draft review, reviewer pools, and gap/hypothesis discovery — design

**Status:** design settled with the user in conversation 2026-10-13 (grilling rounds Q1–Q31); awaiting plan approval before any code.
**Base:** `dev/cloud-continuation` at `0d7df13`. Worked on in a separate worktree (`/home/ubuntu/ScieFlow-worktrees/scite`), stacked branches `dev/scite-m0` … `dev/scite-m3`.
**Relation to the roadmap:** implements the scite part of `2026-09-22-core-roadmap.md` M3 (provider, public tier, cache, ledger) and the reference-audit part of M5, narrowed to what draft review and gap discovery need. The roadmap rule "the pipeline uses ScieFlow's own client; scite's MCP server is for interactive coordinator use" is kept: ScieFlow's client speaks to scite's MCP endpoint (the transport the user holds a token for) *and* its public REST endpoints, but no sub-agent calls scite directly.

## Why

`paper-draft` already produces several independent full drafts and cross-reviews them. Two things are missing:

1. **Review staffing is hard-wired to "two authors review each other".** The protocol text says "each author reviews the OTHER author's draft", which only works for exactly two authors; there is no way to add a reviewer who did not write a draft, no reviewer role for drafts at all (`research.cross-review` is lit-review's; `research.reviewer` is a single agent used only by `paper-review`), and no notion that `claude-paper` and `claude` are the same model family.
2. **Nothing checks the drafts against the literature they cite** beyond the opt-in NotebookLM claim audit. Scite's distinctive data — citation statements classified as supporting / contrasting, retraction notices, citation-intent graph — is exactly the evidence a reviewer lacks.

The user's target flow, validated against the code:

```
authors (any N, user-chosen) ──► full drafts (Phase 3, unchanged)
        │
        ▼
reviewer pool (user-chosen; authors and/or extra non-author agents)
  pass A: blind review (fresh dispatch, no scite)                 ┐ per reviewer × assigned draft
  core:   ONE evidence bundle per manuscript version (free first, scite MCP ≤3 calls)
  pass B: evidence review (fresh dispatch, pass A + bundle + prompt instructions) ┘
        │
        ▼
coordinator: review matrix (all passes, agreement, evidence for/against, recommendation)
        │
        ▼
user decides per comment (accept / reject / defer)  ── gate now; web matrix view later
        │
        ▼
authors revise accepted comments only (address-or-rebut) ──► next round ──► merge (coordinator or workbench)
```

## What scite is, as verified 2026-10-13

- The MCP server (`https://api.scite.ai/mcp`) exposes retrieval tools only: `search_literature`, `read_fulltext`, `citation_graph`, `bibliography`, collections, `report_citations`/`citation_report`, plus four server prompts (`literature-review`, `fact-check-claim`, `systematic-review-screen`, `verify-bibliography`) that use `{{arg}}` placeholders. `get_more_tools` confirms there is no generative assistant tool. **Scite returns evidence; an agent writes the review.**
- `search_literature` with `dois` and no `term` returns, per paper, metadata, abstract, tally, OA flags and a `citations` list — but those are statements **the paper itself makes about others** (`sourceDoi` = the paper). What *other* papers say about a cited DOI comes only from `citation_graph` with `direction: in` (+ `include_snippets`).
- Public REST, no key, not counted against the MCP token: `GET /papers/{doi}`, `GET /tallies/{doi}`, **bulk `POST /tallies`** (one request for a whole reference list). Verified HTTP 200 without credentials.
- Crossref (metadata, `update-to` retractions incl. Retraction Watch data) and Europe PMC OA full text (`/{PMCID}/fullTextXML`) are free; verified HTTP 200.
- How scite meters MCP usage (per call vs per result) is not documented to us. M1 measures it; the ledger records both calls and result counts, and every call uses a small `limit`.

## Decisions (user, 2026-10-13)

| # | Decision |
|---|---|
| Q1 | Scite review plugs into `paper-draft` Phase 4 cross-review **and** `paper-review`. Not into the outline. |
| Q2/Q12 | Reviewers are a **user-chosen pool**: draft authors and/or extra agents who wrote no draft. ScieFlow auto-assigns drafts to reviewers, balanced, `reviews_per_draft` (default 2); an explicit matrix may override. Extra reviewers default to `covers: all`. Every author still reviews the other authors' drafts when it is in the pool. |
| Q13/Q22 | Default: no reviewer reviews a draft of its own **model family**; no agent reviews its **own** draft. Two per-run switches, both off: `allow_same_family_review`, `allow_self_review`. **Every review is a fresh dispatch** (never the authoring thread/session), including self-review when allowed. Every agent declares its model family (`model_family:`). |
| Q3/Q29 | Transport is ScieFlow-owned: `scieflow research scite …` and `scieflow review evidence …`. **Core performs every scite call**; reviewers never call scite. Per-harness MCP registration is documented only for interactive coordinator chats (Hermes, Claude Code, Codex), never for dispatches. |
| Q4/Q24/Q31 | Free sources first; scite MCP only where nothing else has the data (contrasting statements, topic search, novelty). Prompts = `queries` (planned by core, deduplicated, batched) + `instruction` (the user's string prompt with wildcards, read by the reviewer in pass B, costs no call). Reviewers may request ≤2 follow-ups; core dedups and runs them once per round. |
| Q5 | Only literature claims leave the machine: citing sentences with numbers, `[data:…]`, `% source:` and file references stripped, plus DOIs and topic terms. Wildcards resolving to result/data content are refused for any external source. A human-answered `external-sharing` gate per run shows the exact outgoing query plan. |
| Q6/Q16 | Run setting `scite: off \| ask \| approved` (default `ask`; one yes covers all rounds; only the user sets `approved`). CLI asks in the run configuration gate; web: checkbox. No token → `off` with one notice. `off` removes pass B and the bundle; blind reviews still run. |
| Q7/Q17 | Advisory, except **retracted** or **unresolvable DOI** → blocks merge until fixed or explicitly overruled by the user. Core emits `review.blocker` and opens a `review-blocker` gate (requires human). Hermes/Discord delivery is outside core (lifecycle relay consumes events). |
| Q8 | `config/scite-prompts.yml` (project defaults) with an allowlisted wildcard catalogue; a run may disable or add prompts, never add wildcard types. `{{name}}` / `{{name:arg}}`. |
| Q9/Q14 | Coordinator writes `review/round-N/matrix.{json,md}`; user decides per comment accept/reject/defer through a `review-decision` gate; authors address only accepted comments (rebuttal allowed). `review_decisions: coordinator` (per run, user-set) lets an autonomous coordinator decide with rationale — never for blockers. |
| Q15/Q30 | Pass A and pass B are separate dispatches; pass A is hashed and frozen before core builds or releases the bundle. Blindness is enforced against **accidental** scite use (no scite in dispatch harnesses, scite CLI refuses inside a dispatch, token never read from the dispatch environment by ScieFlow). Not enforced against a deliberately hostile agent — the sandbox confines writes, not reads or network; documented as a limitation. |
| Q23 | Quota exhausted → scite passes stop, affected matrix rows say "scite unavailable (quota)", user notified, run continues blind-only; `scieflow research scite backfill <slug> --round N` later. Low-quota warning at 10 % remaining. |
| Q10/Q19/Q20/Q25 | Gap discovery adopts the Robin / Co-Scientist pattern (below) with providers scite + PubTator3 + ClinicalTrials.gov + OpenAlex; PaperQA2 and Edison/FutureHouse later and optional. Elo tournament after the existing debate. |
| Q21 | Hypotheses sent for novelty checks are abstracted (no numbers, cohort descriptors, effect sizes), mechanically filtered, shown verbatim in the gate. |
| Q26 | `paper-review` uses the same reviewer pool and two passes; submitter separation follows the same family rule. |
| Q27 | Offline recorded fixtures; one opt-in live smoke test (`-m scite_live`). |
| Q11/Q28 | Feature branches with PRs, stacked per milestone; parallel development with the ScieFlowDev session under the ownership split below. |

## Architecture

### 1. Agent families and the reviewer pool (`core/agent_config.py`, `core/review/assign.py`)

- `config/agents.yml`: every agent gets **`model_family:`** (`claude`, `claude-paper` → `claude`; `codex`, `codex-paper`, `codex-review` → `openai`; `agy` → `gemini`; `stub` → `stub`). A separate key on purpose: the existing `family:` is the *output dialect* `sessions.family_of` uses to parse conversation transcripts (`FAMILIES = {claude, codex, agy}`, and `can_converse` depends on it), so overloading it would change which agents can host a conversation. `agent_config.model_family(cfg)` returns `model_family`, falling back to `family`, and `configure` refuses an enabled agent that resolves to neither when it is put into a review role. Unknown agents added later by the user must declare it.
- `agent_config.ROLES` gains `research.draft-reviewers` (`many=True`, primary only). `research.reviewer` becomes `many=True`; a string value in older configs normalises to a one-element list (read-compatible, `configure` writes lists).
- Run `config.yml` gains a `review:` block, written only through `scieflow review configure <slug>` (validated; `--as-agent` refuses `scite=approved`, `allow_*` switches and `review_decisions`):

```yaml
review:
  scite: ask                      # off | ask | approved
  reviews_per_draft: 2
  allow_same_family_review: false
  allow_self_review: false
  review_decisions: human         # human | coordinator
  pool:                           # optional; default = research.draft-reviewers assignment
    - {agent: codex-review, covers: all}
  matrix_override: {}             # optional explicit reviewer -> [authors]
```

- `assign.plan(authors, pool, settings) -> Assignment` is a pure, deterministic function: eligibility (self / family switches), `covers: all` first, then balanced round-robin for `covers: auto` until each draft has `reviews_per_draft` reviewers; under-coverage is reported, never silently accepted (a draft with zero eligible reviewers is an error naming the switch that would fix it). Output is persisted as `review/round-N/assignment.json` and is identical for identical input.

### 2. Scite and free sources (`integrations/scite/`, `research/lib/`)

```
src/scieflow/integrations/__init__.py
src/scieflow/integrations/scite/policy.py    config/scite.yml loader, deny-by-default
src/scieflow/integrations/scite/ledger.py    per-run + per-day calls/results, quota state
src/scieflow/integrations/scite/cache.py     on-disk cache keyed by normalised request, TTL
src/scieflow/integrations/scite/public.py    REST: bulk tallies, papers (no key)
src/scieflow/integrations/scite/mcp.py       MCP streamable-HTTP client (optional `mcp` group)
src/scieflow/research/lib/retractions.py     Crossref metadata + update-to retractions
src/scieflow/research/lib/fulltext.py        Europe PMC OA full text + local passage extraction
```

- `config/scite.yml` (user-owned, gitignored; `config/scite.example.yml` committed): `token_env` (default `SCITE_TOKEN`) or `token_file`; `allowed_tools` (default `search_literature`, `citation_graph`); budgets `max_mcp_calls_per_round: 5`, `max_mcp_calls_per_run: 20`, `max_mcp_calls_per_day` (user sets); `max_followups_per_round: 2`; `contested_seeds: 10`; `rest_rate_per_s`; cache TTLs (metadata 30 d, searches 7 d). Absent file → scite is `off` for every run, said once.
- Every request goes through policy → cache → ledger → transport, and emits the existing `integration.call` event. Errors are classified: `QUOTA`, `AUTH`, `RATE` (honour `Retry-After`), `TRANSIENT` (bounded jittered retry), `REFUSED_POLICY`.
- **Dispatch refusal.** The scite command refuses when it detects it runs inside a ScieFlow dispatch. Until K16 lands an explicit marker, detection uses the sandbox's per-dispatch `UV_CACHE_DIR` (`sandbox.cache_dir`) — a heuristic, documented as such; after K16 merges, a one-line `SCIEFLOW_DISPATCH=1` in `sandbox.environment` replaces it (tracked follow-up, not in M0/M1 to stay off K16's files).

### 3. Prompts and wildcards (`core/review/prompts.py`, `config/scite-prompts.yml`)

Each prompt: `id`, `stage` (`review` | `gap` | `hypothesis`), `applies_to` (sections / workflow), `instruction` (string with wildcards — what the user asked for: "a list of string prompts"), `queries` (what core must fetch; `source` ∈ `crossref | scite_public | europepmc | scite_mcp | pubtator | ctgov | openalex`).

Wildcard catalogue (allowlist; anything else is a load-time error):

| Wildcard | Type | Resolves to | May leave the machine |
|---|---|---|---|
| `{{claim}}` | list | citing sentence, sanitised | yes (as key terms) |
| `{{claim_dois}}` | scalar per claim | DOIs it cites | yes |
| `{{doi}}` | list | every DOI in `references.bib` | yes |
| `{{doi_usage}}` | scalar per doi | manuscript sentences citing it, sanitised | no (instruction only) |
| `{{novelty_claim}}` | list | sentences matching first/novel/unprecedented/to our knowledge, sanitised | yes |
| `{{topic}}` | scalar | title + keywords + research question | yes |
| `{{cited_dois}}`, `{{latest_cited_year}}`, `{{seed_dois}}` | scalar | from bib / selected_dois | yes |
| `{{gap}}`, `{{hypothesis}}` | list | gap statements; abstracted hypotheses (Q21 filter) | yes (abstracted only) |
| `{{section:<name>}}` | scalar | section text | **no** — instruction only |
| `{{file:<run-relative path>}}` | scalar | file inside the run, confined | **no** — instruction only |

The sanitiser removes digits-with-units, percentages, p-values, CIs, `[data:…]`, `% source:` comments, `\ref`/`\input`, paths; a value that still matches the leak patterns after sanitising is refused, not sent. Every wildcard has `max_chars`; list wildcards have `max_items` (default 40, Results/Discussion first).

### 4. The evidence bundle (`core/review/evidence.py`)

One bundle per **manuscript version** (all drafts share `references.bib`; the claim set is the union over drafts), reused by every reviewer and pass:

| Need | Source | scite MCP calls |
|---|---|---|
| Reference exists, metadata, retraction/correction | Crossref + scite `/papers` | 0 |
| Supporting/contrasting counts for every cited DOI | bulk `POST /tallies` | 0 |
| Does the source support the claim? | Europe PMC OA full text → local passages around the claim's key terms; abstract otherwise (flagged `abstract-only`) | 0 |
| What others say against the contested sources | `citation_graph` `in` + `include_snippets`, seeds = ≤10 DOIs with the highest contradicting share | 1 |
| Missing / contrasting / recent literature on the topic | one `search_literature` on `{{topic}}`; ranked locally by tally and year | 1 |
| Novelty claims | all novelty sentences OR-combined into one query; hits matched back locally | 1 (only if any) |
| Reviewer follow-ups | dedup across reviewers, capped | ≤2 |

Round 1 ≤3 (+2) MCP calls; later rounds re-query only new or changed DOIs/claims (content hashes) — usually 0. `scieflow review evidence <slug> --round N --plan` prints the exact outgoing request list (attached to the `external-sharing` gate) without sending anything. Output: `review/round-N/evidence/bundle.json` (validated) + `calls.json` (ledger excerpt). The bundle is data, never instructions (rule 8): reviewer prompts quote it inside a fenced, labelled block.

### 5. Two-pass review and the matrix (`core/review/{passes,matrix,decisions}.py`)

- **Pass A** (`review/round-N/<reviewer>-on-<author>.blind.json`): the existing `adversarial-review.md` persona and manuscript-review schema, extended with `pass: blind`. Fresh dispatch, `--role research.draft-reviewers`. Frozen: its sha256 is recorded in `assignment.json` before the bundle is released.
- **Pass B** (`…evidence.json`): fresh dispatch; receives the draft, its own pass-A JSON, the bundle and the rendered `instruction`s of applicable prompts; schema adds `pass: evidence`, per-comment `evidence[]` (`doi`, verbatim `quote`, `stance` supporting/contrasting/neutral, `source`), `changed_from_blind` (new / strengthened / weakened / withdrawn), `followup_requests[]` (≤2), `scite_status` (`ok | off | quota | unavailable`). A finding without a verbatim quote is not evidence (rule 9).
- **Matrix** (`review/round-N/matrix.json` + `.md`): core builds the skeleton mechanically (every comment of every pass with ids `R<reviewer>:<pass>:<id>`, plus all bundle flags); the coordinator clusters duplicates (`cluster`), states agreement, evidence for/against and a recommendation per cluster. Validated against `review-matrix.schema.json`.
- **Decision** (`review/round-N/decisions.json`): per cluster `accept | reject | defer` + optional note, written only by `review.decide()` — from `scieflow gate answer` / the web — never hand-edited. Authors receive only accepted clusters; the coverage check (existing Phase 4 step 5) runs over accepted ids.
- **Blockers**: retracted or unresolvable DOIs become `blocker: true` clusters; `review.blocker` event + `review-blocker` gate (requires human); merge refuses while one is open unless the answer is an explicit overrule recorded with the user's reason.

New gate kinds: `review-decision` (`requires_human: false`, but the protocol answers it as agent only when `review_decisions: coordinator`), `review-blocker` (`requires_human: true`), `scite-consent` (`requires_human: true`). New events (closed vocabulary, additive): `review.assigned`, `review.pass_done`, `review.evidence_built`, `review.matrix_ready`, `review.decided`, `review.blocker`, `scite.quota_low`, `scite.unavailable`.

### 6. Read/write API for the web (`core/review/service.py`)

The contract ScieFlowDev builds against (pure functions over a `Project` + slug; no FastAPI imports):

```python
review_settings(project, slug) -> dict
set_review_settings(project, slug, changes: dict, actor: str) -> dict      # validates; agent refusals
assignment(project, slug, round: int | None = None) -> dict | None
rounds(project, slug) -> list[dict]                                        # per round: passes present, scite_status
review_pass(project, slug, round, reviewer, author, pass_) -> dict | None
evidence_summary(project, slug, round) -> dict | None                      # counts, flags, calls, quota state
matrix(project, slug, round) -> dict | None
decide(project, slug, round, decisions: dict[str, dict], actor: str) -> dict   # writes decisions.json + event
blockers(project, slug) -> list[dict]
scite_status(project) -> dict                                              # configured, tier, quota remaining, last error
```

New file, not `core/service.py` (K16 rewrites that file). Every reader returns `None`/empty for an ordinary absent state, mirroring `core/drafts.py`.

### 7. Gap discovery, Robin / Co-Scientist style (M3)

```
1 evidence map      existing search + scite tallies (public) + OpenAlex topics/counts by year
2 typed detectors   fan-out over research.gap-analysis agents, each fed one detector's evidence:
                    disputes (1 citation_graph with intent over seeds) · stated-open-questions (1 OR search)
                    · PubTator3 A→B→C open links (free) · ClinicalTrials.gov registered-unpublished (free)
                    · OpenAlex bridges / trend breaks (free) · gaps from the user's own data (existing)
3 verification gate a gap without a quoted evidence item is dropped (mechanical)
4 hypotheses        from verified gaps only (existing fan-out, existing caps)
5 debate            existing perspective debate (unchanged)
6 reflection        blind critique, then free novelty pre-check (OpenAlex + Europe PMC) for all
7 tournament        Elo, pairwise, judges from families ≠ both authors where possible,
                    tournament_top_k 8, max_tournament_comparisons 28, debate status as tie-break
8 scite check       top-k only: novelty + counter-evidence, OR-batched, ≤2 MCP calls
9 evolution         refine top-k once (evolution_rounds: 1), re-run 6–8 on changed items only
10 meta-review      report/meta-review.md (patterns across critiques)
11 decision gate    user picks hypotheses to carry into paper-draft (`gaps_from`)
```

Gap run ≈ 4–5 scite MCP calls. PaperQA2 (local deep reading) and Edison/FutureHouse (paid) are named extension points, not built.

## Parallel development with the ScieFlowDev session

ScieFlowDev (profile `scieflow-dev`) is mid-way through K16 on `dev/k16-accepted-foundation` (Task 8 next), which rewrites `core/agent_run.py`, `core/service.py`, `core/jobs.py`, `core/sandbox.py`, `core/provenance.py`, `core/run/conversation.py` and much of `web/`. Rules that keep the two sessions conflict-free:

| Area | Owner | Rule |
|---|---|---|
| `src/scieflow/core/review/**`, `src/scieflow/integrations/**`, `src/scieflow/research/**`, `config/scite*.yml`, new schemas, research skills, `tests/core/review/**`, `tests/integrations/**`, `tests/research/**`, `tests/fixtures/scite/**`, `tests/fixtures/review/**` | this session | ScieFlowDev reads, never edits |
| `src/scieflow/web/**`, `tests/web/**` | ScieFlowDev | this session never edits |
| K16 files listed above | ScieFlowDev | this session never edits until K16 is merged |
| Shared, additive-only: `config/agents.yml` (`model_family:` lines), `config/defaults.yml` (new keys), `core/agent_config.py` (one role + family rule), `core/events.py` (new types), `schemas/gates.yml` (new kinds), `AGENTS.md`, `src/scieflow/research/AGENTS.md`, `pyproject.toml` (optional group) | this session | additive edits only, in M0, so later rebases are trivial |

- Separate checkout: `/home/ubuntu/ScieFlow-worktrees/scite`. Never the main checkout, never ScieFlowDev's worktrees under `workspace/cloud-dev-tickets-20261008/scratch/`.
- **Contract freeze = M0 merged.** M0 ships schemas, `core/review/service.py` signatures with working readers, the settings writer, the decision writer, event/gate names and a fixture run (`tests/fixtures/review/run/`) with a complete round (assignment, both passes, bundle, matrix). From then on ScieFlowDev can build every web view against the fixture while M1–M3 fill in the producers. Contract changes after M0 are additive only; a breaking change needs a version bump in the schemas and a note in the handoff doc.
- Handoff: `docs/superpowers/handoffs/2026-10-13-scite-review-web-handoff.md`, delivered to ScieFlowDev after M0 merges. ScieFlowDev's owner schedules it after its current K16 checkpoint; this session does not dispatch into ScieFlowDev's kanban or edit its ledgers. ScieFlowDev's Desktop-only output policy is unchanged.
- Merge order is free: M0 avoids K16 files, so whichever lands second rebases without conflicts in code; `events.py`/`gates.yml` hunks are additive.

## Non-goals

- No generative "scite reviewer"; no scite calls from sub-agents; no per-harness MCP for dispatches.
- No network isolation of dispatches (separate sandbox project).
- No PaperQA2 / Edison integration beyond named extension points.
- No change to Phase 3 drafting, the outline phase, or the workbench's curation model.
- No patient-level data, result values or data-package content sent to any external service.

## Verification

- M0: assignment table tests (2/3/5 authors, extra reviewers, each switch, zero-eligible error, determinism); schema round-trips; `configure` refusals; fixture run validates end to end; `uv run pytest -q`, `./scripts/check_legacy.sh`, `uv run --group docs mkdocs build --strict` green.
- M1: recorded fixtures for MCP `initialize`/`tools/call` (each allowed tool, quota error, auth error, malformed), REST tallies/papers, Crossref retraction, Europe PMC XML; sanitiser leak tests (numbers, `[data:]`, paths, `\input`); budget/ledger/cache tests; plan-only mode sends nothing (transport mock asserts zero calls); dispatch refusal; `-m scite_live` smoke test excluded by default.
- M2: e2e with the stub agent — 3 authors + 1 extra reviewer, self-review switch on/off, `scite: off`, quota exhausted mid-round (rows marked, run continues), planted retracted DOI blocks merge, decision filtering reaches revision prompts, paper-review pool.
- M3: detectors on fixtures; verification gate drops unquoted gaps; Elo determinism with a fixed seed and comparison cap; hypothesis abstraction filter; top-k scite budget respected; e2e gap run on the stub.
