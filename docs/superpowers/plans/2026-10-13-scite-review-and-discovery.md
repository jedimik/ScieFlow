# Scite-grounded review and discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Test-first: every task writes its failing test before the code.

**Goal:** Any number of user-chosen draft authors are reviewed by a user-chosen reviewer pool in two fresh passes — blind, then grounded in one shared, cheap evidence bundle (free sources first, scite MCP ≤3 calls per round) — and the coordinator hands the user a per-comment decision matrix. The same machinery drives `paper-review` and a Robin / Co-Scientist-style gap and hypothesis pipeline.

**Architecture:** New packages `core/review/` (assignment, prompts, evidence, passes, matrix, decisions, web-facing service API) and `integrations/scite/` (policy, ledger, cache, public REST, MCP client), plus free-source helpers in `research/lib/`. Core performs every external call; sub-agents only read files. Protocol changes live in the research skills.

**Tech Stack:** Python 3.12, existing `research/lib/http.py` + `requests`/`httpx`, `jsonschema` validation through `scieflow research validate`, the official `mcp` Python SDK as optional dependency group `scite`, `responses`/recorded fixtures for offline tests, the stub agent for e2e.

**Spec:** `docs/superpowers/specs/2026-10-13-scite-review-and-discovery-design.md`

**Web half:** `docs/superpowers/handoffs/2026-10-13-scite-review-web-handoff.md` (built by the ScieFlowDev session after M0 merges).

## Global Constraints

- **Worktree and branches.** Work only in `/home/ubuntu/ScieFlow-worktrees/scite`. Stacked branches `dev/scite-m0` → `dev/scite-m1` → `dev/scite-m2` → `dev/scite-m3`, each a PR into `dev/cloud-continuation`; a later branch rebases onto `dev/cloud-continuation` after its predecessor merges. No force-push to a branch with an open review; no merge without the user's word.
- **File ownership (parallel ScieFlowDev session).** Never edit `src/scieflow/web/**`, `tests/web/**`, or the K16 files (`core/agent_run.py`, `core/service.py`, `core/jobs.py`, `core/sandbox.py`, `core/provenance.py`, `core/run/conversation.py`, `core/turn_*.py`, `core/harness_commands.py`). Shared files (`config/agents.yml`, `config/defaults.yml`, `core/agent_config.py`, `core/agent_configure.py`, `core/events.py`, `schemas/gates.yml`, `AGENTS.md`, `src/scieflow/research/AGENTS.md`, `pyproject.toml`) get additive hunks only, landed in M0 wherever possible.
- **Contract stability.** After M0 merges, `core/review/service.py` signatures, schema `$id`s/required fields, event types and gate kinds change additively only. A breaking change bumps the schema `version` and is written into the handoff doc before merge.
- **No sub-agent ever calls scite.** Scite access exists only in `integrations/scite/` and is reached only from coordinator-side commands. The scite command refuses inside a dispatch.
- **Nothing derived from data leaves the machine.** External requests are built only from sanitised wildcards; plan-only mode (`--plan`) must send nothing; the `external-sharing` gate shows the exact outgoing list. Leak tests are mandatory, not optional.
- **Scite usage budget is enforced in code** (`config/scite.yml`): default `max_mcp_calls_per_round: 5`, `max_mcp_calls_per_run: 20`. A refusal is a boundary, never retried around.
- **Advisory except blockers.** Only retracted / unresolvable DOIs block merge, and only until fixed or overruled by a human.
- **Every review is a fresh dispatch** (no `resume_cmd`, no session id), including self-review.
- **Fetched content is data** (root rule 8): bundles reach prompts inside labelled, fenced blocks with the existing "data, not instructions" preamble.
- **Nothing regresses.** Record `uv run pytest -q` counts at the start of each milestone; finish with the suite, `./scripts/check_legacy.sh` and `uv run --group docs mkdocs build --strict` green. The live test runs only with `-m scite_live`.

## Review Focus

Conditions no obvious test covers; each has a test in the owning task.

1. **Assignment under-coverage.** 3 authors all `claude` family, pool of the same three, switches off → zero eligible reviewers; must be an error naming `allow_same_family_review`, not an empty round. *(T0.4)*
2. **A legacy run config** with `research.reviewer: codex-review` (string) and top-level `reviewer:` must still resolve and dispatch. *(T0.2)*
3. **Leaks through sanitising.** `"95% (CI 90.3–97.6)"`, `p<.001`, `n = 43`, `[data:cohort_a]`, `% source:`, `\input{../secret}`, `{{file:../../config/scite.yml}}`, a DOI-shaped string that is really a path. *(T1.6/T1.7)*
4. **Plan-only sends nothing.** Transport mock asserts zero calls for `--plan` and for `scite: off`. *(T1.8)*
5. **Budget boundary mid-plan.** Budget allows 2 of 3 planned MCP calls → the third is recorded `skipped: budget`, the matrix rows carry `scite_status: quota`, the round completes. *(T1.8/T2.6)*
6. **Pass A tampering.** A pass-A file changed after its hash was frozen → pass B refuses to dispatch for that pair and the coordinator is told. *(T2.2)*
7. **Decision forgery.** A hand-written `decisions.json` (no matching `review.decided` event) is ignored by the revision step. *(T2.4)*
8. **Quota error mid-call** classified `QUOTA`, never retried, `scite.unavailable` emitted once per run. *(T1.4)*

## File structure

| File | Responsibility |
|---|---|
| `config/agents.yml` | `model_family:` on every agent (additive) |
| `config/defaults.yml` | `review:` defaults, `research.draft-reviewers` default assignment, M3 gap keys |
| `config/scite.example.yml` | policy template (copied by the user to gitignored `config/scite.yml`) |
| `config/scite-prompts.yml` | default prompt list + wildcard catalogue |
| `core/agent_config.py` | `research.draft-reviewers` role, `research.reviewer` many, `model_family()` |
| `core/review/__init__.py` | package |
| `core/review/settings.py` | `review:` block load/validate/write, actor rules |
| `core/review/assign.py` | pure reviewer-assignment planner |
| `core/review/claims.py` | citing / novelty sentence extraction (reuses `scripts/nblm/claims.py`), sanitiser |
| `core/review/prompts.py` | prompt + wildcard loader and renderer |
| `core/review/evidence.py` | query planner + executor + round diff |
| `core/review/passes.py` | pass A/B prompt building, freezing, validation |
| `core/review/matrix.py` | matrix skeleton + validation |
| `core/review/decisions.py` | decisions + blockers |
| `core/review/service.py` | web/CLI-facing API (the contract) |
| `core/review/cli.py` | `scieflow review configure|assign|evidence|matrix|decide|status` |
| `integrations/scite/{policy,ledger,cache,public,mcp}.py` | scite access |
| `research/lib/retractions.py`, `research/lib/fulltext.py` | Crossref, Europe PMC |
| `research/lib/{pubtator,ctgov,openalex_trends}.py` | M3 providers |
| `research/discovery/{detectors,tournament,abstraction}.py` | M3 pipeline pieces |
| `src/scieflow/research/schemas/{manuscript-review,evidence-bundle,review-matrix,review-decisions,scite-prompts,review-assignment,hypothesis-tournament}.schema.json` | contracts |
| `src/scieflow/research/templates/review-pass-{blind,evidence}.md`, `revise-accepted.md` | prompt templates |
| `src/scieflow/research/skills/{paper-draft,paper-review,gap-discovery}/SKILL.md` | protocols |
| `docs/research/scite.md` | setup, budgets, limitations, interactive MCP snippets |
| `tests/core/review/**`, `tests/integrations/scite/**`, `tests/research/**`, `tests/fixtures/{scite,review}/**` | tests and fixtures |

---

## Milestone M0 — contract (branch `dev/scite-m0`) → then hand off the web half

### Task 0.1: `model_family` on every agent

**Files:** `config/agents.yml`, `core/agent_config.py`, `tests/core/test_agent_config.py`
- [ ] Test: every enabled agent in the shipped registry resolves `model_family()`; `claude-paper`/`claude` → `claude`, `codex*` → `openai`, `agy` → `gemini`; an agent with neither key returns `None`; `sessions.can_converse` results for all registry agents are unchanged (snapshot before/after).
- [ ] Add `model_family:` lines (additive; do not touch `family:`); implement `agent_config.model_family(cfg) -> str | None` (`model_family` → `family` fallback).

### Task 0.2: Reviewer roles

**Files:** `core/agent_config.py`, `core/agent_configure.py`, `config/defaults.yml`, tests
- [ ] Test: `research.draft-reviewers` exists (`many`, primary-only, support refused unless promoted); `research.reviewer` accepts a string (legacy) and a list; `LEGACY_ROLE_KEYS["reviewer"]` still maps; `configure --assign research.draft-reviewers=codex-review,claude-paper` writes a list; a reviewer pool member without a resolvable `model_family` is refused with a message naming the key.
- [ ] Implement; default `research.draft-reviewers: [codex-paper, claude-paper]` (= today's authors, so default behaviour stays "authors review each other").

### Task 0.3: Review settings

**Files:** `core/review/settings.py`, `core/review/cli.py` (`configure` only), `config/defaults.yml` (`review:` defaults), `src/scieflow/cli.py` (register `review` group), tests
- [ ] Test: defaults merge (`scite: ask`, `reviews_per_draft: 2`, switches false, `review_decisions: human`); invalid values refused with the allowed set; `actor="agent"` refused for `scite=approved`, `allow_*`, `review_decisions`, `pool` (agents may set `scite=off` only — a de-escalation); writes go to `workspace/<slug>/config.yml` under `review:` only, preserving other keys and comments-free YAML ordering like `agent configure`.
- [ ] Implement `load(project, slug)`, `validate(dict)`, `write(project, slug, changes, actor)`; CLI `scieflow review configure <slug> --set KEY=VALUE … [--as-agent] --yes`.

### Task 0.4: Assignment planner

**Files:** `core/review/assign.py`, `src/scieflow/research/schemas/review-assignment.schema.json`, tests
- [ ] Table tests: 2 authors (default pool) → each reviews the other; 3 authors + pool of the 3 → every draft gets 2 reviewers, nobody reviews self, balanced ±1; + extra `codex-review covers: all` → it reviews all 3; same-family authors with switch off → error naming the switch; switch on → allowed; `allow_self_review` on → self pairs eligible but marked `self: true`; `matrix_override` respected and still validated against the rules; identical input → byte-identical JSON.
- [ ] Implement `plan(authors, pool, settings, families) -> dict` (pure) and `write(ws, round, plan)`; record `fresh_dispatch: true` per pair.

### Task 0.5: Schemas

**Files:** `src/scieflow/research/schemas/*.schema.json` (manuscript-review extended; new evidence-bundle, review-matrix, review-decisions, scite-prompts), `src/scieflow/research/validate.py` registry, tests
- [ ] Test: old manuscript-review files still validate (new fields optional; `pass` defaults to `blind` when absent); each new schema has a valid and an invalid fixture; `scieflow research validate <f> --schema <name>` knows every new name.
- [ ] Write schemas with `"version": 1` and stable `$id`s. Matrix rows: `cluster`, `members[]` (`reviewer`, `author`, `pass`, `id`), `agreement`, `evidence_for[]`, `evidence_against[]`, `blocker`, `scite_status`, `recommendation`, `rationale`.

### Task 0.6: Gates and events

**Files:** `schemas/gates.yml`, `core/events.py`, tests
- [ ] Test: kinds `review-decision` (requires_human false), `review-blocker` (true), `scite-consent` (true) open/answer through `scieflow gate`; `--as-agent` answer refused for the two human kinds even in autonomous runs; event types `review.assigned|pass_done|evidence_built|matrix_ready|decided|blocker`, `scite.quota_low|unavailable` accepted, unknown still refused.
- [ ] Additive edits only.

### Task 0.7: Service API (the contract) with readers and the two writers

**Files:** `core/review/service.py`, `core/review/decisions.py`, `core/review/cli.py` (`status`, `decide`), tests
- [ ] Test every function in the spec's §6 against `tests/fixtures/review/run/` and against an empty run (returns `None`/`[]`, never raises); `decide()` validates cluster ids against the matrix, refuses decisions on a blocker cluster without `overrule_reason` from a human actor, writes `decisions.json` atomically and emits `review.decided`; path inputs (`reviewer`, `author`) go through `drafts.check_name`.
- [ ] Implement readers over the file layout; `scite_status()` returns `configured: false` until M1 fills it.

### Task 0.8: Fixture run and contract doc

**Files:** `tests/fixtures/review/run/**` (3 authors, 1 extra reviewer, round 1 with assignment, 6 pass-A + 6 pass-B files, bundle, matrix with one blocker, no decisions), `docs/research/review-contract.md`, `scripts/check_ownership.sh` (fails if the branch diff touches ScieFlowDev-owned or K16 files), `docs/superpowers/handoffs/2026-10-13-scite-review-web-handoff.md` (finalised), mkdocs nav
- [ ] Test: every fixture file validates against its schema; `service` readers return the documented shapes (golden JSON).
- [ ] Write the contract doc (file layout, schemas, service functions, events, gates, settings, actor rules). **PR M0.** After merge: deliver the handoff (see "Coordination").

## Milestone M1 — sources, client, prompts, evidence (branch `dev/scite-m1`)

### Task 1.1: Policy

**Files:** `integrations/scite/policy.py`, `config/scite.example.yml`, `.gitignore`, tests
- [ ] Test: absent `config/scite.yml` → `Policy.disabled` with one reason; unknown keys refused; tools outside `allowed_tools` refused (`REFUSED_POLICY`, exit 3 like nblm); token presence checked by env name or file *existence* — the token value is never logged, printed or written.
- [ ] Implement; `config/scite.yml` gitignored.

### Task 1.2: Ledger and cache

**Files:** `integrations/scite/{ledger,cache}.py`, tests
- [ ] Test: per-run (`workspace/<slug>/scite/ledger.jsonl`) and per-day (`~/.cache/scieflow/scite/ledger-YYYY-MM-DD.jsonl`) counters; calls vs results recorded separately; `remaining()` against all three budgets; 10 % threshold emits `scite.quota_low` once; cache hit costs nothing and is recorded as `cached`; TTL expiry; cache key normalisation (DOI case, term whitespace, sorted DOI lists).
- [ ] Implement; each real call emits `integration.call`.

### Task 1.3: Public REST

**Files:** `integrations/scite/public.py`, `tests/fixtures/scite/rest/*.json`, tests
- [ ] Test (recorded): bulk `POST /tallies` for N DOIs in one request (chunked at a configured max), `GET /papers/{doi}`; 404 → `unknown`; 429 → honours `Retry-After`; rate limit per second.
- [ ] Implement over `research/lib/http.py`.

### Task 1.4: MCP client

**Files:** `integrations/scite/mcp.py`, `pyproject.toml` (`[dependency-groups] scite = ["mcp>=…"]`), `tests/fixtures/scite/mcp/*.json`, tests
- [ ] Test (recorded JSON-RPC): `initialize`, `tools/call` for `search_literature` and `citation_graph`; auth error → `AUTH`; quota error → `QUOTA` (no retry, `scite.unavailable` once per run); malformed → `TRANSIENT` bounded retry; group not installed → `NOT_INSTALLED` with the `uv sync --group scite` hint; inside a dispatch → refused.
- [ ] Implement a thin wrapper (`call(tool, args) -> dict`) using the SDK's streamable-HTTP client; Bearer token from policy.

### Task 1.5: Free sources

**Files:** `research/lib/retractions.py`, `research/lib/fulltext.py`, fixtures, tests
- [ ] Test: Crossref `update-to` retraction/correction detection; unresolvable DOI → `unresolvable`; Europe PMC DOI→PMCID resolution and `fullTextXML` parse; passage extraction returns ≤N windows around key terms, deterministic; non-OA → abstract with `abstract-only`.
- [ ] Implement.

### Task 1.6: Claims and sanitiser

**Files:** `core/review/claims.py`, tests
- [ ] Test: reuses `scripts/nblm/claims.py` parsing (no fork); novelty detector phrases; sanitiser removes the Review Focus #3 patterns; residual-leak check refuses; key-term extraction for OR queries is stable.
- [ ] Implement.

### Task 1.7: Prompts and wildcards

**Files:** `core/review/prompts.py`, `config/scite-prompts.yml`, tests
- [ ] Test: shipped file validates; unknown wildcard → load error; run override may disable/add prompts but not add wildcard types; `{{section:x}}`/`{{file:x}}` refused inside `queries`; `{{file:…}}` confined to the run; `max_chars`/`max_items` enforced with Results/Discussion priority; rendering is deterministic.
- [ ] Ship the default prompts from the spec (`claim-support`, `contested-source`, `novelty-claim`, `missing-literature`, `claim-strength`, `disputes`, `stated-open-questions`, `bridges`, `hypothesis-novelty`, `hypothesis-counterevidence`), each with `instruction` + `queries`.

### Task 1.8: Evidence planner and executor

**Files:** `core/review/evidence.py`, `core/review/cli.py` (`evidence`), `research/cli.py` (`scite status|plan|run|backfill`), tests
- [ ] Test: plan for the fixture run uses 0 MCP calls for metadata/tallies/claim support and ≤3 for contested/topic/novelty; plan-only and `scite: off` → zero transport calls; round 2 with unchanged bib/claims → 0 calls (hash diff); budget boundary (Review Focus #5); follow-ups deduped across reviewers and capped; bundle validates; `calls.json` matches the ledger.
- [ ] Implement `plan(ws, round) -> Plan`, `execute(plan, policy) -> Bundle`, `backfill(ws, round)`.

### Task 1.9: Doctor and docs

**Files:** `core/workspace.py` doctor hook (additive), `docs/research/scite.md`, mkdocs nav, tests
- [ ] Test: doctor reports scite configured / not, group installed / not, `SCITE_TOKEN` present in the ScieFlow process env (warning: dispatches inherit it), and any dispatch harness config that registers a scite MCP (`~/.claude.json`, `~/.codex/config.toml`, active Hermes profile used as harness) — names only, never contents of secrets.
- [ ] Docs: setup, budgets, call table, limitations (blindness vs hostile agents, abstract-only for paywalled papers), interactive MCP snippets for Hermes (`mcp_servers.scite`), Claude Code (`claude mcp add --transport http scite https://api.scite.ai/mcp --header "Authorization: Bearer $SCITE_TOKEN"`), Codex (`[mcp_servers.scite]`) — marked "coordinator chats only".

### Task 1.10: Live smoke test

- [ ] `tests/integrations/scite/test_live.py` marked `scite_live`, skipped without token/group: one `POST /tallies`, one `search_literature` with `limit: 1`. Registered marker excluded by default in `pyproject.toml`. **PR M1.**

## Milestone M2 — protocols (branch `dev/scite-m2`)

### Task 2.1: Pass templates

**Files:** `src/scieflow/research/templates/review-pass-{blind,evidence}.md`, `revise-accepted.md`, `core/review/passes.py`, tests
- [ ] Test: blind prompt contains no bundle, no scite wording beyond "do not use external literature services"; evidence prompt contains the frozen pass-A JSON, the bundle in a fenced data block, the rendered instructions; both carry the rule-8 preamble.

### Task 2.2: Freezing and validation

- [ ] Test: pass-A hash recorded in `assignment.json` before `evidence` runs; tampering detected (Review Focus #6); pass-B validation requires `evidence[]` quotes for every `strengthened`/`new` comment that cites evidence.

### Task 2.3: Matrix builder

**Files:** `core/review/matrix.py`, `core/review/cli.py` (`matrix`), tests
- [ ] Test: skeleton includes every comment of every pass plus bundle flags (retracted, unresolvable, contested, abstract-only); coordinator-filled matrix validates; `.md` rendering is deterministic; `scite: off` → evidence columns say `off`.

### Task 2.4: Decisions and filtered revision

- [ ] Test: `review-decision` gate options per cluster; human answer writes through `decide()`; `review_decisions: coordinator` lets the agent answer with rationale except blockers; revision prompts include only accepted clusters (Review Focus #7); coverage check over accepted ids.

### Task 2.5: Blockers

- [ ] Test: planted retracted DOI → `review.blocker` + `review-blocker` gate; merge step refuses while open; human overrule with reason clears it and is recorded.

### Task 2.6: Quota exhaustion path

- [ ] Test: `QUOTA` mid-round → remaining MCP queries skipped, rows `scite_status: quota`, notification event, round completes; `scite backfill` later fills only missing parts within budget.

### Task 2.7: `paper-draft` protocol

**Files:** `src/scieflow/research/skills/paper-draft/SKILL.md`, `src/scieflow/research/AGENTS.md`
- [ ] Rewrite Phase 1 step 3 (staffing adds the reviewer pool, switches and the scite question — `scite-consent` + `external-sharing` gates), Phase 4 (assign → pass A → evidence → pass B → follow-ups → matrix → decision → revise accepted → coverage → next round), Phase 5 (merge or workbench reads the matrix; blockers refuse merge), Phase 6 step 4 (claim-check stays optional and separate). Keep single-author fallback.

### Task 2.8: `paper-review` protocol

- [ ] Reviewer pool + two passes + matrix + decision; submitter separation by `model_family` (same switches).

### Task 2.9: Root rules

**Files:** `AGENTS.md`, `src/scieflow/research/AGENTS.md`
- [ ] New rule 17 (scite): reachable only via `scieflow research scite` / `scieflow review evidence`, never from a sub-agent; `scite` setting semantics (mirrors rule 12); budget refusals are boundaries; only sanitised content leaves; blockers need a human. Update rule 10/tier text for `research.draft-reviewers`.

### Task 2.10: End-to-end with the stub

**Files:** `tests/research/test_e2e_review_pool.py`
- [ ] 3 authors + 1 extra reviewer, switches off/on, `scite: off`, quota exhausted, retracted DOI, decisions filtering, `paper-review` pool. **PR M2.**

## Milestone M3 — gap and hypothesis discovery (branch `dev/scite-m3`)

### Task 3.1: Free providers

**Files:** `research/lib/{pubtator,ctgov,openalex_trends}.py`, policy entries in `config/integrations.example.yml` (new, deny-by-default host list), fixtures, tests
- [ ] PubTator3 entity autocomplete + relations (A→B, B→C, missing A→C with publication counts); ClinicalTrials.gov v2 search for completed trials without linked publications; OpenAlex counts-by-year and co-citation bridges. Cache + ledger reused (provider name in the ledger).

### Task 3.2: Detectors and verification gate

**Files:** `research/discovery/detectors.py`, `gaps.schema.json` (additive `detector`, `evidence[].quote`), tests
- [ ] Each detector produces candidate gaps with quoted evidence; verification drops unquoted ones (mechanical).

### Task 3.3: Tournament

**Files:** `research/discovery/tournament.py`, `hypothesis-tournament.schema.json`, tests
- [ ] Elo with fixed K and seed; pairing schedule capped by `max_tournament_comparisons`; judges chosen from model families ≠ both hypothesis authors where possible; debate status as tie-break; deterministic.

### Task 3.4: Abstraction and novelty

**Files:** `research/discovery/abstraction.py`, tests
- [ ] Hypothesis abstraction filter (numbers, cohort descriptors, effect sizes) + residual-leak refusal; free novelty pre-check (OpenAlex + Europe PMC) for all; scite novelty + counter-evidence for top-k only, OR-batched, ≤2 MCP calls.

### Task 3.5: `gap-discovery` protocol and e2e

**Files:** `src/scieflow/research/skills/gap-discovery/SKILL.md`, `config/defaults.yml` (`evolution_rounds: 1`, `tournament_top_k: 8`, `max_tournament_comparisons: 28`), `tests/research/test_e2e_gap_discovery.py`
- [ ] Phases per spec §7 (evidence map → detectors → verification → hypotheses → debate → reflection → tournament → scite top-k → evolution → meta-review → decision gate); e2e on the stub within budget. **PR M3.**

---

## Coordination with the ScieFlowDev session

- **Now (before M0 merges):** nothing is sent into ScieFlowDev's kanban, ledgers or worktrees; its K16 Task 8 continues undisturbed. This plan and the handoff draft live only on `dev/scite-m0`.
- **When M0 merges:** the user (or this session, on the user's word) points the ScieFlowDev owner at `docs/superpowers/handoffs/2026-10-13-scite-review-web-handoff.md`. ScieFlowDev schedules it after its current K16 checkpoint and rebases its web branch onto `dev/cloud-continuation` containing M0. Its Desktop-only reporting policy is unchanged.
- **During M1–M3:** contract changes are additive and listed in the handoff doc's changelog section; ScieFlowDev keeps building against `tests/fixtures/review/run/`.
- **After K16 merges:** follow-up task (either session, by agreement): add `SCIEFLOW_DISPATCH=1` to `sandbox.environment` and switch the scite dispatch-refusal from the `UV_CACHE_DIR` heuristic to it.
- **Conflict check before every PR:** `git diff --name-only dev/cloud-continuation...HEAD` must not list any ScieFlowDev-owned or K16 file; CI-style check script `scripts/check_ownership.sh` (added in M0) fails the branch otherwise.
