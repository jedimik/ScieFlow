# M-G plan (M-G1 scope): claim ledger and journal revision (core M5 rest) — round 2, verdict DONE

Pipeline `m-g-plan`, step plan, **round 2**, epoch 1. Card `t_327fdc2c`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261031_150418_7409a2`). Round 1
(card `t_d87103d0`, 2026-10-30) returned BLOCKED; this round re-plans under the human's ruling of
2026-10-30 (`approvals/auto-rulings/m-g-plan-plan-e1-1-answer.md`, choice `split-m-g1`). Written
2026-10-31. Size L: Part A is the spec, Part B the implementation plan (repo rule: an L milestone gets a
spec and a plan under `docs/superpowers/`).

This is a plan only. No code changed, no commit, no push, no merge, no live attempt, no network call
except the read-only `git ls-remote` and the object-only `git fetch` that fix the reference main. The
in-flight checkout `/home/ubuntu/Github/ScieFlow` was only read (`git --no-optional-locks status`, `git
log`); nothing in it was changed.

## 0. Round 2: what changed since round 1 (read this first)

**The human's ruling (binding, ruling 1 of the card).** Round 1 returned BLOCKED because the queue entry
for M-G (`continuity/milestones.yml:24`) says it waits for the in-flight paper-lifecycle work in
`/home/ubuntu/Github/ScieFlow` to land on main. The human answered `split-m-g1`: *"run M-G now, do not
wait for the paper-lifecycle work. Re-plan as M-G1 without t8 (waits only for M-C2). t8 (claim check in
paper-draft verify + paper-revision workflow) becomes a later milestone M-G2 that waits for the
paper-lifecycle work to land on main; the operator adds it to the queue."* The operator has added it:
`continuity/milestones.yml:25` `{id: m-g2, after: [m-g], title: "M-G2 Claim check in paper-draft verify
and paper-revision workflow (M-G ticket t8)", …}` with the landing note. The landing note still printed
on the `m-g` entry (line 24) is superseded for this milestone by that ruling.

**So this plan is M-G1.** It keeps every ticket of round 1 except **t8**. Ticket names stay `t0`–`t7`
and `t9` (no renumbering), so that the M-G2 queue entry ("M-G ticket t8") and the round-1 references
stay unambiguous; the pipeline, branch and worktree keep the names the supervisor fixed (`m-g-run`,
`dev/m-g`, `RUN/scratch/m-g-wt`). What moved out:

- section 1 item 6 (verify-phase gate, `paper-revision` workflow) → M-G2;
- spec 5.13 → section 10 (the M-G2 handoff, kept as design input for the M-G2 plan);
- ticket t8 and its review/fix steps → section 10; the pipeline has no `t8`, `t8r`, `t8f`;
- the three paper-lifecycle base checks of t0 → gone (M-G1 does not depend on that work);
- decision M-G-D17 → "deferred to M-G2"; new decision M-G-D19 records the split.

**M-G1 touches none of the nine paper-lifecycle paths** (section 3): the guard is now "unchanged over the
whole branch" for every step. Nothing else in Parts A and B needed the in-flight work (round 1 said so;
re-checked: no remaining ticket reads `workflows:`, `phases_for`, `require_loop` or `run adopt`, and the
skill/doc edits of t9 are outside the nine paths).

**Dependencies are now all merged.** M-C2 merged on 2026-10-31: `RUN/scratch/m-c2/publish.json`
`{"pushed_head": "a59df13561a4ee115354a82f95dc301060853c24", "merged": true, "main_after_merge":
"8b374803c2eb6cad0df981d466ba097b973f3c84"}`; `git ls-remote git@github.com:jedimik/ScieFlow.git
refs/heads/main` = `8b374803c2eb6cad0df981d466ba097b973f3c84` (merge of PR #24, `dev/m-c2`). M-D2 and
M-F were already merged. The plan is therefore written against real main `8b37480`, not against "main as
it will be". The paper-lifecycle work is still not on main (2026-10-31: `/home/ubuntu/Github/ScieFlow` at
`2e93b14` with the same nine modified paths and untracked `tests/core/test_paper_lifecycle.py`); that
only matters for M-G2.

**Previous review.** Round 1 was never reviewed (it stopped at BLOCKED), so there are no CHANGES findings
to close. The round-1 BLOCKED cause is closed by the ruling above. Everything round 1 cited was
re-verified on `8b37480`; section 0a lists the cites that moved because M-C2 changed their file, and one
substantive refinement that M-C2's merged code required (C005 key matching, 5.6).

## 0a. Inputs and references used

Immutable inputs (sha256 re-computed on this round-2 card; all match the card):

| Input | sha256 |
|---|---|
| `handoffs/gap-eval-20261023/HANDOFF-next-after-G6.md` | f97be2ce…e68a |
| `handoffs/gap-eval-20261023/DECISIONS.md` | 3f643c1b…47da |
| `handoffs/gap-eval-20261023/GAP-ANALYSIS.md` | 833f1f25…cc8d |
| `continuity/roadmap-20261024.md` | 193452d5…2238 |
| `reviews/m-a-plan.md` (format example) | 0a2c6135…ae3f |
| `pipelines/ma-run.yml` (format example) | 1153c040…c85c |
| `continuity/milestones.yml` (not pinned) | m-g entry line 24: `after: [m-d2, m-c2, m-f]`; m-g2 entry line 25 (added by the operator after the ruling) |
| `approvals/auto-rulings/m-g-plan-plan-e1-1-answer.md` (ruling 1) | choice `split-m-g1`, human note quoted in section 0 |

Scope source: GAP-ANALYSIS.md section 8 row **M-G** — scope "#5, #13, #18: `claims.jsonl` resolver over
`run:`/`data:`/`doi:`, numeric claims mechanical (number must appear in the artifact), C001–C006
taxonomy, decision-letter ingestion to `manuscript-review` JSON, `latexdiff` between provenance points,
RO-Crate export"; acceptance "Planted wrong number fails; planted retracted DOI blocks; letter → JSON →
coverage loop green on a fixture"; depends on "M-D2, M-C2, M-F; rebase on the in-flight paper-lifecycle
diff (`workflows:` map)"; conflicts "`core/run/*` in-flight → wait until that diff lands". GAP section 2
rows #5 (route: reimplement, schema fields ported with attribution), #13 (vendor templates, reimplement
ingestion onto `manuscript-review` JSON) and #18 (RO-Crate). Decisions that apply: **D7** (CLI parity),
**D9–D11, D23** (data protection), **D15** (vendored code verbatim and credited; only transport, cache and
ledger reimplemented), **D18** (a refusal-grade identity verdict blocks through the human-only
`review-blocker` gate).

The GAP row's "rebase on the in-flight diff" and its `core/run/*` conflict apply only to the lifecycle
wiring (round-1 t8), which the ruling moved to M-G2; M-G1 touches no `core/run/*` file, so it has
neither the rebase nor the conflict. The GAP acceptance criteria are all M-G1's (tickets t3 and t6).

Prerequisite plans read: `reviews/m-c2-plan.md` (approved round 4, **merged** as PR #24; its 5.5 identity
blockers are now code: `src/scieflow/core/review/identity_gates.py`), `reviews/m-f-plan.md` (merged;
manuscript reader, check, package, letters), `reviews/m-d2-plan.md` (merged; run manifests). Code read
read-only from git objects on **`M` = `8b374803c2eb6cad0df981d466ba097b973f3c84`** (current remote main,
fetched object-only); every `path:line` below is on `M`. The researcher snapshot read is
`/home/ubuntu/ScieFlow-worktrees/gap-eval/src/researcher` (`R:`; the e4ddbf4 snapshot M-C1 and M-F
pinned). Host probes: `latexdiff` **absent**, `latexmk`, `pdflatex`, `perl` present.

**Round-2 re-verification on `M`.** `git diff --name-only 0cfba0b 8b37480` (the M-C2 merge) touches,
among the files this plan cites, only: `src/scieflow/core/events.py`, `src/scieflow/core/gates.py`,
`src/scieflow/core/review/service.py`, `src/scieflow/core/review/cli.py`,
`src/scieflow/research/AGENTS.md`, `src/scieflow/research/skills/paper-draft/SKILL.md`, `CREDITS.md`,
`VENDORED.md`, `docs/cli.md`. Every other cited file is byte-identical to round 1's `0cfba0b`, and a
spot-check of 29 cites on `M` (tex.py, package.py, cli.py, manifest.py, evidence.py, decisions.py,
journal/adopt.py, journal/html.py, review/claims.py, nblm/claims.py, data/release.py, data/levels.py,
citations.py, defaults.yml, letters.py, checklist.py, revise.py, data/cli.py) found each where cited.
Cites that moved:

| Round-1 cite (on `0cfba0b`) | On `M` |
|---|---|
| `core/events.py:50-52` (`note.*` accepted) | `core/events.py:53-54` |
| `core/review/service.py:422-472` (`blockers`) | `_evidence_blockers` `:423-470`; `blockers` `:473-483` = evidence blockers + `identity_gates.blockers(project, slug)` |
| M-C2 plan §5.5 (identity blockers) | `core/review/identity_gates.py:267-285` (`blockers`), `:37` `REASON_PREFIX = "identity-"`, `:52-57` `_token` (a key that is not `[A-Za-z0-9_.:/+-]{1,200}` appears only as `k-<16 hex>`), module doc `:1-19` (`mismatch`/`unresolvable` block, `inconclusive` never) |
| `core/review/cli.py:384-401` (review-prompt pattern) | `:384-403` (`prompt_cmd`, the `how = … agent run --role …` line `:402-403`) |
| `research/skills/paper-draft/SKILL.md:336-339` (hand spot-check) | `:339-342` (M-C2 added the verify-bib step to Phase 5 step 0) |
| `research/AGENTS.md` lines 166-185 (protected edit 1 region) | lines 175-194 (M-C2 rewrote rule 6; the old text still occurs exactly once) |

## 1. For the human: what M-G adds, in plain words

Today ScieFlow asks agents to put `% source: [data:<id>]` behind every number they write, and the
paper-draft skill then asks a coordinator to *spot-check 3–5 numbers by hand*
(`src/scieflow/research/skills/paper-draft/SKILL.md:339-342`). Nothing checks every number, nothing
notices that a results table was regenerated after the text was written, and a journal's decision letter
has no place in a run at all. M-G closes that:

1. **A claim ledger: `scieflow manuscript claims build <run>`.** Every sentence, table and figure of the
   manuscript that states a number, carries a provenance mark or cites a reference becomes one ledger
   line in `report/claims/claims.jsonl`: where it is (file and line), which numbers it states, and what
   it rests on — `[data:<id>]` (a file of your data package or a released result), `[run:<id>]` (an
   experiment run or campaign) or a cited DOI. The ledger also pins the exact bytes of that evidence.
2. **A mechanical check: `scieflow manuscript claims check <run>`.** It resolves every reference and
   reports problems with the six codes researcher uses (C001–C006), so a report reads the same in both
   tools:
   - **C001 orphan claim** — a number in the Abstract, Results, a table or a figure with no evidence,
     or a reference that does not resolve;
   - **C002 altered number** — a number that is not in the cited file (allowing only honest rounding,
     percent ↔ fraction and stated inequalities like `p < 0.001`), or evidence whose bytes no longer
     match its own record (run manifest, release record);
   - **C003 stale evidence** — the evidence changed after the claim was ledgered (re-confirm, then
     `claims build --repin`); a cited paper with a published correction is a warning;
   - **C004 qualifier mismatch** — *not checked mechanically* (it needs reading the source; that stays
     the opt-in claim-check module's job);
   - **C005 retraction** — the claim cites a reference that is an open review blocker (retracted,
     unresolvable, or failing identity verification by `scieflow research verify-bib`, merged with
     M-C2). It clears exactly when the blocker
     clears: you fix the reference or overrule the `review-blocker` gate;
   - **C006 artifact-code drift** — a cited run was produced from a dirty work tree or from a commit
     that is not in the current history.
   It never prints a value read from your data files, only the manuscript's own number and the file's
   path. **`scieflow manuscript package` now refuses while the check has errors** (in addition to its
   M-F refusals). You alone can waive one finding of one claim (`claims waive`, with a reason, bound to
   that sentence's exact text; never a retraction or an unreleased-L2 finding).
3. **Journal decision letters: `scieflow manuscript revision …`.** You add the letter
   (`revision ingest --letter letter.txt`; text, Markdown or HTML). ScieFlow splits it into the editor's
   and each reviewer's part and snapshots the manuscript you submitted. A primary agent (role
   `research.submitter`) then structures every reviewer comment into ScieFlow's existing
   `manuscript-review` JSON — **each comment must be a verbatim quote of the letter with its line
   range, and every paragraph of the letter must be accounted for** (an agent cannot quietly drop an
   inconvenient comment). `revision respond` starts `response.md` with one section per comment;
   `revision coverage` refuses until every comment has a response and either a `Change:` naming a
   manuscript file that really changed since submission, or a `No change:` with a reason;
   `revision letter` fills researcher's response-to-reviewers LaTeX template (vendored by M-F).
4. **Provenance points and diffs: `scieflow manuscript point` and `manuscript diff`.** Name a version of
   the manuscript (or take the one inside your last submission package) and diff any two versions:
   a file list, a plain-text diff, and — when `latexdiff` is installed (it is not on this server) — the
   marked-up `diff.tex` journals ask for.
5. **An RO-Crate: `scieflow manuscript crate <run>`.** One `ro-crate-metadata.json` (RO-Crate 1.1) that
   describes the manuscript files, authors (ORCID, never e-mail), every claim, the DOIs it cites, the runs
   and data files it rests on, with sha256 identifiers — for a data-availability statement or a deposit.
6. **Not in this milestone: wiring into the paper workflow.** Refusing `scieflow run mark <run> verify
   done` in a native `paper-draft` run while the check has errors, and a `paper-revision` workflow, need
   the paper-lifecycle work that is not on main yet. They are M-G2 (queue entry `m-g2`, section 10). Until
   then the paper-draft skill tells the coordinator to run `claims check` in Phase 5 (t9), and
   `manuscript package` refuses on claim errors (item 2).

Everything runs locally. No new dependency. No network.

**Decisions taken under Rule D that you may veto at the approval gate:**

- **M-G-D1 Reimplement, credit researcher (D15).** researcher's lineage code (`R:core/researcher_core/
  lineage/{compile,graph,model,passport}.py`) imports its own provenance ledger, snapshot store and cache
  (`graph.py:16` `from ..provenance import ProvenanceEvent`; `provenance.py:54-65` sqlite, cache). Vendoring
  it verbatim would bring a second ledger into ScieFlow, which D15 excludes. M-G keeps researcher's
  defect codes C001–C006 and its RO-Crate 1.1 shape (`passport.py:42-131`) and writes ScieFlow's own code
  over ScieFlow's data; `CREDITS.md` says so and `VENDORED.md` lists the lineage package as "not
  vendored". (GAP #5's route is "reimplement".)
- **M-G-D2 All commands under `scieflow manuscript`** (`claims`, `point`, `diff`, `revision`, `crate`).
  `src/scieflow/cli.py` `GROUPS` is not edited.
- **M-G-D3 C004 is reserved, never emitted.** Qualifier matching needs the source text; the opt-in
  claim-check module (root AGENTS.md rule 12) stays the only place for it, advisory.
- **M-G-D4 C005 mirrors the review blockers.** A ledgered DOI or bib key that appears in
  `review.service.blockers()` (evidence: `retracted`/`unresolvable`, `src/scieflow/core/review/
  evidence.py:765`; identity `identity-mismatch`/`identity-unresolvable`, merged with M-C2,
  `core/review/identity_gates.py:267-285`) is C005. No separate retraction lookup, no
  network: the status is as fresh as the last evidence round or identity check, and the check says when
  there is none (`doi-status-unknown`).
- **M-G-D5 The number rule** (5.5): a manuscript number matches an evidence number when it equals it
  rounded to the manuscript's precision (half-up or half-even), integers exactly, `x%` also against
  `x/100`, and `<`/`≤`/`>`/`≥` against the inequality. Figure/table/section/equation numbers, years
  1900–2099 and numbers inside `\cite`, `\ref`, `\label`, display math and URLs are not claims.
- **M-G-D6 Severity.** C001 is an error in the Abstract, Results, tables and figures, a warning
  elsewhere; C002, C003 (evidence changed), C005, C006 and `data-unreleased` are errors; the rest are
  warnings. Exit 1 on any error.
- **M-G-D7 Waivers are the user's CLI action, not a gate.** `claims waive` is refused inside a
  dispatch, recorded as a `note.claims_waived` event by a human actor and bound to the claim's text
  hash. C005 and `data-unreleased` cannot be waived (they have their own human paths: the
  `review-blocker` gate and `scieflow data release`).
- **M-G-D8 Package refuses on claim errors.** After all M-F refusals (so existing refusal tests keep
  their outcome), `package_run` builds the ledger in memory and refuses on any error. No new key in
  `package.json`. The M-F test manuscripts contain no number in their document body
  (`tests/research/manuscript/conftest.py:26-70`: no digit in the abstract or body text; the bib entries have no DOI, which gives only the `doi-missing` warning), so no existing test changes; t0 re-checks that.
- **M-G-D9 Decision letters are text only** (`.txt`, `.md`, `.html`/`.htm` through M-F's
  `research/journal/html.py:101` `to_text`), ≤ 1 MB; PDF letters are a follow-up. `ingest` and `adopt`
  are refused inside a dispatch (`SCIEFLOW_DISPATCH`, the M-F pattern of
  `research/journal/adopt.py:124`).
- **M-G-D10 Every journal comment needs an answer.** Unlike internal review rounds (matrix, accept /
  reject / defer decisions, `src/scieflow/core/review/revise.py:115-142`), a journal letter has no
  clustering and no rejection: coverage requires every comment. The internal round machinery
  (`core/review/*`) is called, never changed.
- **M-G-D11 No new gate kind, no new event type.** Events are `note.claims_built`,
  `note.claims_checked`, `note.claims_repinned`, `note.claims_waived`, `note.manuscript_point`,
  `note.manuscript_diff`, `note.manuscript_crate`, `note.revision_ingested`, `note.revision_adopted`,
  `note.revision_coverage` (counts, ids and hashes only; `src/scieflow/core/events.py:53-54` accepts
  `note.*`). `schemas/gates.yml` and the closed gate-kind set pinned in
  `tests/core/data/test_release.py` are untouched.
- **M-G-D12 `latexdiff` is optional.** Not installed here and not a dependency: when found on `PATH`
  it runs with a fixed argv, no shell, a timeout, in a scratch folder, on two files ScieFlow flattened
  itself (so it never follows `\input` paths); otherwise `diff.txt` and `changes.json` are still
  written and the output says `latexdiff not installed`. No PDF of the diff (follow-up).
- **M-G-D13 RO-Crate is metadata only.** No files copied into the crate, no W3C PROV (follow-up);
  hashes as schema.org `identifier` values `sha256:<hex>`; in an L2 run a data file that was not
  released appears only as `#data-<id>` with no path or hash.
- **M-G-D14 No new agent role.** Letter structuring and response drafting use `research.submitter`
  (primary; `config/defaults.yml:67`).
- **M-G-D15 `[run:]` references stay inside the run.** Evidence from another run comes in as a data
  package or a release import (`docs/research/data-packages.md`), never by reading another run.
- **M-G-D16 One additive keyword in M-F's TeX reader.** `tex.read_tree(…, keep_comments=False)`; the
  default path is byte-for-byte unchanged (`src/scieflow/research/manuscript/tex.py:202-232` strips
  comments at `:218`, which loses `% source:`).
- **M-G-D17 Lifecycle wiring is deferred to M-G2** (human ruling, section 0). M-G1 edits none of the
  nine paper-lifecycle paths; the round-1 design is kept in section 10 as M-G2's starting point.
- **M-G-D18 Two protected edits** (section 3): rule 12, a workflow row and a routing sentence in
  `src/scieflow/research/AGENTS.md`; one skills-table row in the root `AGENTS.md`.
- **M-G-D19 Scope split (human, 2026-10-30).** This run is M-G1: tickets t0–t7 and t9 of round 1, names
  unchanged; t8 is M-G2. The pipeline, branch and worktree keep the `m-g` names.

**Proposed PR (the operator's publish step pushes and merges after the final review PASS):** head
`dev/m-g` → base `main`, one PR, merged with a merge commit, title "M-G1 Claim ledger and journal
revision". The body (written by the branch step) lists the five capabilities of section 1 (items 1–5),
says that item 6 is M-G2, lists decisions M-G-D1..D19, the absolute test counts and the residuals.

## 2. Base, dependencies and worktree

- **Dependencies** (`continuity/milestones.yml:24` `after: [m-d2, m-c2, m-f]`; the landing note is
  superseded by the ruling, section 0). All three are merged (section 0). The paper-lifecycle work is
  **not** a dependency of M-G1. t0 checks every dependency again and refuses (`BASE_RED`) without them.
- **Lanes**: `parallel: any` (`continuity/milestones.yml:13`, human 2026-10-30), so M-G may share the
  two lanes with any other run whose prerequisites are merged (today M-D3 is installed). Overlap with
  M-D3 (`reviews/m-d3-plan.md` file list): `mkdocs.yml` (a different nav line) and the root `AGENTS.md`
  (its rule 11 vs M-G's skills-table row: disjoint regions); the installer's refresh-before-merge
  handles either order.
- **Worktree**: t0 runs `git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main` → `base`,
  the object-only fetch `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags
  --no-write-fetch-head git@github.com:jedimik/ScieFlow.git refs/heads/main`, then requires `git
  merge-base --is-ancestor <sha> <base>` for H-4 `6152d733728b9ed5bf5b571aef475f8071426386`, M-A
  `be99db2ace0e560fcdb7c00231e906e4aaae67e2`, M-E `9eeb4fad8c01d7c008acd155e81dac3976d62aa7`, M-B
  `05d4d1087bb9c3b5b1bcc7fc8df089787f40a7ab`, M-C1 `aa7c3792b0b374ccfa6868bb13018a1974ac3435`, M-D1
  `c350593ee6235049073ab5e7207f881fb60ba307`, M-D2 `5f051d04989a80b340e0f1542f543c0ffa68a760`, M-F
  `9634f2874e5af4b6569926f1beeda69fdf9bc456` and M-C2 `a59df13561a4ee115354a82f95dc301060853c24` (all
  nine verified ancestors of `8b37480` on this card), and `RUN/scratch/m-c2/publish.json` with
  `merged: true`. Any failure → verdict `BASE_RED` with the reason. Then `git -C
  /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/m-g RUN/scratch/m-g-wt <base>`. (If the
  paper-lifecycle work has landed by then, nothing changes for M-G1: the nine paths stay untouched.)
- Commits are local on `dev/m-g`; no rebase, squash, amend or force; review fixes are new commits.
- RUN = `/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`; scratch output in
  `RUN/scratch/m-g/` (junit, logs, `executions.jsonl`, `pr-body.md`); pytest temp dirs under
  `~/.cache/scieflow-m-g-<step>/`.

## 3. In-flight, protected and changed files

**In-flight paths (never edited by M-G1).** `config/agents.yml`, `docs/runs.md`, `schemas/status.yml`,
`src/scieflow/core/run/{actions,checkpoint,cli,init,status,validate}.py` are the paper-lifecycle work's
files (uncommitted in `/home/ubuntu/Github/ScieFlow`; the standing rules name them too). **No M-G1 step
edits them** (their wiring is M-G2). Guard, run by every implementation, fix, branch and final card and
reported as `inflight_guard_exit` (must be 0): `git -C WT diff --exit-code <base> HEAD -- <the nine
paths>` and `git -C WT diff --exit-code -- <the nine paths>` on the dirty tree.

**Never edited**: `config/sandbox.yml`, `config/agents.yml`, the DD1-sealed files
(`tests/probes/k_task8_binding.py`, `tests/probes/test_k_task8_binding.py`,
`tests/fixtures/k_task8_stream_fixture.py`), `CLAUDE.md`, every `AGENTS.md` except through the two
protected edits below, `src/scieflow/core/events.py`, `src/scieflow/core/gates.py`,
`src/scieflow/core/service.py`, `src/scieflow/core/review/*` (called: `service.blockers`,
`decisions.schema_error`, `passes`), `src/scieflow/core/data/*` (called: `levels`, `release`),
`src/scieflow/core/design/*`, `src/scieflow/experiments/*` (called: `manifest.load_results`,
`manifest.verify_campaign`), `src/scieflow/research/journal/*` (called: `html.to_text`, `state`),
`src/scieflow/research/manuscript/{check,checklist,judgement,build,render,templates,metadata,letters,
ziputil}.py` (called), `src/scieflow/research/citations.py`, `scripts/nblm/*` (called:
`claims.split_sentences`, `parse_bib`), `src/scieflow/_vendor/researcher_core/*` (the response template
is read, not changed), `schemas/gates.yml`, `schemas/status-research.yml`,
`src/scieflow/research/schemas/manuscript-review.schema.json`, `src/scieflow/web/*`,
`src/scieflow/cli.py`, `config/defaults.yml`, `pyproject.toml`, `uv.lock`.

**Protected edits (operator-applied before t9, `pipelines/drafts/m-g-protected-edits.json`).** Both
`old` texts occur exactly once on `M` = `8b37480` (`str.count` on `git show M:<path>` = 1; the `new`
texts occur 0 times; re-run on this round-2 card). M-C2's merged edit of rule 6 is above this region
and M-F's rule 11 text is kept verbatim as context.

Edit 1 — `src/scieflow/research/AGENTS.md` (M lines 175-194): adds rule 12, one workflow row and one
routing sentence. old:
```
    and the reporting guideline come from the profile; before calling a
    manuscript submission-ready run `uv run scieflow manuscript check
    <slug>`, and build the package only with `uv run scieflow manuscript
    package <slug>`.

## Workflows

| Workflow | Protocol |
| --- | --- |
| Literature review | `src/scieflow/research/skills/lit-review/SKILL.md` |
| Paper review | `src/scieflow/research/skills/paper-review/SKILL.md` |
| Gap discovery | `src/scieflow/research/skills/gap-discovery/SKILL.md` |
| Paper draft | `src/scieflow/research/skills/paper-draft/SKILL.md` |
| Journal research and manuscript check | `src/scieflow/research/skills/journal-research/SKILL.md` |

When the human asks for a literature overview / paper research, follow
`src/scieflow/research/skills/lit-review/SKILL.md`. When they ask for a manuscript review, journal
targeting, or a reviewer/submitter loop, follow `src/scieflow/research/skills/paper-review/SKILL.md`.
When they ask how a journal accepts papers, to profile a target journal, or to check or package a
manuscript for submission, follow `src/scieflow/research/skills/journal-research/SKILL.md`.
```
new:
```
    and the reporting guideline come from the profile; before calling a
    manuscript submission-ready run `uv run scieflow manuscript check
    <slug>`, and build the package only with `uv run scieflow manuscript
    package <slug>`.

12. **Claim ledger and journal revision** (`docs/research/claims.md`,
    `docs/research/revision.md`). Every number in a manuscript traces to
    `% source: [data:<id>]`, `[run:<id>]` or a cited DOI, and `uv run
    scieflow manuscript claims check <slug>` proves it mechanically: a
    number missing from its evidence, an orphan claim, stale or altered
    evidence, a retracted or blocking reference, or a run from changed
    code is an error. Fix the text or the evidence; never edit
    `report/claims/`, never change a number only to make it match, and
    never waive a finding (`scieflow manuscript claims waive` is the
    user's). A journal's decision letter enters a run only through `uv run
    scieflow manuscript revision ingest` (the user's). Structure it from
    `uv run scieflow manuscript revision prompt` (`--role
    research.submitter`), quoting every reviewer comment verbatim; the
    coordinator adopts the result with `revision adopt`. Answer every
    comment in `revision/round-<N>/response.md` until `uv run scieflow
    manuscript revision coverage <slug> --round <N>` passes.

## Workflows

| Workflow | Protocol |
| --- | --- |
| Literature review | `src/scieflow/research/skills/lit-review/SKILL.md` |
| Paper review | `src/scieflow/research/skills/paper-review/SKILL.md` |
| Gap discovery | `src/scieflow/research/skills/gap-discovery/SKILL.md` |
| Paper draft | `src/scieflow/research/skills/paper-draft/SKILL.md` |
| Journal research and manuscript check | `src/scieflow/research/skills/journal-research/SKILL.md` |
| Claim ledger and journal revision | `src/scieflow/research/skills/journal-revision/SKILL.md` |

When the human asks for a literature overview / paper research, follow
`src/scieflow/research/skills/lit-review/SKILL.md`. When they ask for a manuscript review, journal
targeting, or a reviewer/submitter loop, follow `src/scieflow/research/skills/paper-review/SKILL.md`.
When they ask how a journal accepts papers, to profile a target journal, or to check or package a
manuscript for submission, follow `src/scieflow/research/skills/journal-research/SKILL.md`.
When they share a journal's decision letter, ask to answer reviewers, or ask whether every number in a
manuscript traces to its evidence, follow `src/scieflow/research/skills/journal-revision/SKILL.md`.
```

Edit 2 — root `AGENTS.md` (M line 242, unchanged by M-C2), one skills-table row. old:
```
| Research a target journal; check and package a manuscript | `skills/journal-research/SKILL.md` |
```
new:
```
| Research a target journal; check and package a manuscript | `skills/journal-research/SKILL.md` |
| Check a manuscript's claims; answer a journal decision letter | `skills/journal-revision/SKILL.md` |
```
`skills/journal-revision` is a new symlink `../src/scieflow/research/skills/journal-revision` (the form
of `skills/journal-research` and `skills/paper-draft`), created by t9. t9 commits both applied files in
one commit ("docs(agents): research rule 12 and the journal-revision skill row (human-approved protected
edits) (M-G t9)").

**Complete file list** (nothing else changes; existing tests only gain lines — no existing assertion is
changed by M-G):

| File | Change | Ticket |
|---|---|---|
| `docs/superpowers/plans/2026-10-31-m-g1-claim-ledger-revision.md`, `docs/superpowers/specs/2026-10-31-m-g1-claim-ledger-revision-design.md` | copies of this plan / its Part A | t0 |
| `src/scieflow/research/manuscript/tex.py` | `read_tree(…, keep_comments=False)` keyword threaded to `_expand` (M-G-D16) | t1 |
| `schemas/claims-ledger.yml` | new: ledger line and check-report vocabulary (keys, units, codes, rules, severities) | t1 |
| `src/scieflow/research/manuscript/claims/{__init__,units,numbers,refs,ledger}.py` | new | t1 |
| `src/scieflow/research/manuscript/claims/{resolve,evidence}.py` | new | t2 |
| `src/scieflow/research/manuscript/claims/{compile,waivers,report}.py` | new | t3 |
| `src/scieflow/research/manuscript/package.py` | one call `_refuse_claims(project, run)` after `_no_links(folder)` (`:294`) and the helper | t3 |
| `src/scieflow/research/manuscript/cli.py` | `claims` subgroup (t1 `build`, `show`; t3 `check`, `waive`), `point`/`diff` (t4), `revision` subgroup (t5, t6), `crate` (t7) | t1, t3–t7 |
| `CREDITS.md`, `VENDORED.md` | one paragraph (ported codes and RO-Crate shape, not code); one "not vendored" line for `lineage/` | t3, t7 |
| `src/scieflow/research/manuscript/points.py`, `…/diff.py` | new | t4 |
| `src/scieflow/research/revision/{__init__,letter,draft,adopt,respond,coverage,render}.py` | new | t5, t6 |
| `src/scieflow/research/manuscript/crate.py` | new | t7 |
| `tests/research/manuscript/claims/*`, `tests/research/manuscript/points/*`, `tests/research/revision/*`, `tests/research/manuscript/crate/*` | new tests and synthetic fixtures | t1–t7 |
| `tests/research/manuscript/test_tex_tree.py` | gains tests for `keep_comments` (no line removed) | t1 |
| `tests/research/manuscript/test_package_preconditions.py` | gains the claims refusal tests (no line removed) | t3 |
| `docs/research/claims.md`, `docs/research/revision.md` (new), `docs/research/manuscript.md`, `docs/cli.md`, `docs/research/workflows/paper-draft.md`, `docs/research/data-packages.md`, `mkdocs.yml` (two nav lines) | docs | t9 |
| `src/scieflow/research/skills/journal-revision/SKILL.md` (new), `skills/journal-revision` (new symlink), `src/scieflow/research/skills/paper-draft/SKILL.md` (Phase 5 step 3, Phase 7), `src/scieflow/research/skills/journal-research/SKILL.md` (one pointer line) | skills | t9 |
| `src/scieflow/research/AGENTS.md`, `AGENTS.md` | the two protected edits, operator-applied, committed by t9 | t9 |
| `tests/test_docs_claims_revision.py` | new docs/skills/rule test | t9 |

**Hotspots (flagged for the operator):** `src/scieflow/research/manuscript/cli.py` and `package.py`
(M-F's, merged; M-G adds commands and one refusal), `CREDITS.md`, `VENDORED.md`, `docs/cli.md` (M-C2
edited them, merged; M-G appends), `src/scieflow/research/AGENTS.md` (M-C2 rule 6, M-F rule 11, M-G
rule 12 — disjoint regions), `mkdocs.yml` and root `AGENTS.md` (M-D3 also edits them, other regions,
section 2). The nine paper-lifecycle paths are untouched by M-G1 and become M-G2's hotspot.

## 4. Current code, verified (on M = 8b37480, M-C2 merged)

**Provenance today is a convention.** The paper-draft author prompt requires `% source: [data:<id>]`
"at the end of the line (tables/figures: one comment line inside the environment)"
(`src/scieflow/research/skills/paper-draft/SKILL.md:151-153`); Phase 5 step 3 samples "3-5 numeric
claims" by hand (`:339-342`); research rule 8 binds every agent to data provenance
(`src/scieflow/research/AGENTS.md`, rule 8); root rule 9 binds notebook claims to `[run:<id>]` or a DOI.
`[data:<id>]` ids come from `inputs/manifest.yml` (`docs/research/data-packages.md:31-42`; schema keys
`schemas/manifest.yml:3-5`: `artifact_keys: [id, file, kind, description]`, no hash). No code reads
`% source:` comments: the review claims module removes them before anything leaves
(`src/scieflow/core/review/claims.py:13-14`) and M-F's reader strips all comments
(`src/scieflow/research/manuscript/tex.py:218`).

**TeX reader (M-F).** `read_tree(folder, main)` (`tex.py:202-207`) flattens `\input`/`\include`
(`INCLUDE_RE`, `:27`) into `Line(file, lineno, text)` entries (`:75-79`, `:225-231`) under limits
(`MAX_FILES` 200, `MAX_DEPTH` 20, `MAX_BYTES` 5 MB, `:19-25`), reading only regular files inside the
folder (`read_regular_text`, `:156`); `source_tree_hash` (`:235-249`) excludes `build/`, `drafts/`,
`letters/` (`TREE_EXCLUDED_DIRS`, `:56`), by-products and `.bak-` files; `excerpt` caps quoted text at 60
characters (`:26`, `:59`). Section kinds come from `checklist.section_kinds(title)`
(`src/scieflow/research/manuscript/checklist.py:51`). Sentences: `scripts/nblm/claims.py`
`split_sentences` (`:109`) with its abbreviation list (`:27-33`), `parse_bib` (`:62`, entry-scoped DOI
regex `:24`), loaded by `core/review/claims.parser()` (`src/scieflow/core/review/claims.py:60`); `safe_doi` is `:270`.

**Evidence stores.** Experiment runs live in `workspace/<slug>/experiments/<campaign>/runs/<run_id>/`
with `manifest.json` (`src/scieflow/experiments/manifest.py:27`; required keys `:35-39` include `code`,
`artifacts`, `metrics_sha256`, `data_level`), `metrics.json` and `artifacts/`; the campaign holds
`results.json`, `results.csv`, `stats.json` and `tables/` (`experiments/cli.py:340-383`). Run ids may
contain `/` (`manifest.py:380-389` `_is_run_id`). `verify_campaign(campaign_dir)` (`:447-490`) returns
problems `manifest-hash`, `manifest-missing`, `manifest-invalid`, `artifact-missing`, `artifact-hash`,
`artifact-extra`, `metrics-hash` and notes `legacy-run`, `metrics-recomputed`, never reading contents into
messages. The manifest's `code` block is `{scieflow: {version, git: {sha, dirty} | None, reason},
pipeline: {name, git, reason}}` (`:251-264`; `git_state` `:132-154`). Released data: `data.yml`
records `released: [{id, file: released/<id>/<name>, sha256, …}]` and `imports: [{id, file:
inputs/released/<run>-<release>/<name>, sha256, from, release}]` (`src/scieflow/core/data/release.py:
369-372`, `:449-452`), read with `levels.read(ws)` (`levels.py:138`); `release._verify` re-hashes a
record (`release.py:411-418`); `levels.run_level(ws)` gives the run's level (`levels.py:178`).

**References and blockers.** `review.service.blockers(project, slug)` (`src/scieflow/core/review/
service.py:473-483`) returns `_evidence_blockers(ws)` (`:423-470`) followed by
`identity_gates.blockers(project, slug)`: entries `{round, cluster, reason, title, flags, references:
[{doi, key, detail}], decision}`; bundle flags with `blocker: True` are `retracted` and `unresolvable`
(`evidence.py:765`), `corrected` is a non-blocking kind (`:884`). The identity entries (merged with M-C2,
`src/scieflow/core/review/identity_gates.py:267-285`) carry `reason` `identity-<verdict>` (`:37`,
`:282`; verdict `mismatch` or `unresolvable`, `inconclusive` never blocks, module doc `:1-7`),
`references: [{key, doi?, detail}]` and a `gate` id; their `key` is the **safe token** of the bib key
(`_token`, `:52-57`: the key itself when it matches `[A-Za-z0-9_.:/+-]{1,200}`, else `k-` + the first
16 hex of its sha256). The test fixture `tests/fixtures/review/run/` flags
`novak2020` / `10.5555/rbd.2020.014` as `retracted`, `blocker: True` (its `round-1/evidence/bundle.json`).
`citations.py` checks cite↔bib↔`report/selected_dois.txt` (`src/scieflow/research/citations.py:62-76`).

**Manuscript review JSON.** `src/scieflow/research/schemas/manuscript-review.schema.json`: required
`agent, reviewed, recommendation, major, minor, summary` (`:7-14`), `additionalProperties: false`
(`:15`); recommendation `ACCEPT|MINOR REVISION|MAJOR REVISION` (`:25-31`); major items `{id ^M[0-9]+$,
location, problem, why_it_matters, resolution}` (`:36-67`), minor `{id ^m[0-9]+$, location, comment}`
(`:84-105`); optional `pass`, `round`, `scite_status`, `followup_requests`. Validated with
`core/review/decisions.schema_error("manuscript-review", data)` (`decisions.py:58`; used by
`passes.py:468`). Internal revision: `revise.coverage()` requires a `## <id>` section with text per
accepted cluster (`revise.py:208-241`).

**Package (M-F).** `package_run` (`src/scieflow/research/manuscript/package.py:282-342`) refuses in order:
manuscript folder, open blockers (`_refuse_blockers`, `:72-82`, fail closed), metadata, profile, check,
build, links (`:289-294`); writes `report/submission/` with `source.zip` (`ziputil.zip_bytes`, `:304`) and
`package.json` (`:325-336`); emits `note.manuscript_package` (`:339`). Letters: the vendored
`templates/response-to-reviewers.tex` (`src/scieflow/_vendor/researcher_core/templates/`) is filled by
`letters.response_text` (`letters.py:74-79`); `render.escape` escapes text.

**Events, CLI.** `events.emit` accepts any `note.<name>` (`src/scieflow/core/events.py:53-54`).
`scieflow manuscript` is `src/scieflow/research/manuscript/cli.py` (group `:38-40`, exit codes `:20`
`OK, REFUSED, USAGE = 0, 1, 2`). The dispatch marker is `SCIEFLOW_DISPATCH` (refusal pattern
`src/scieflow/research/journal/adopt.py:124`, `src/scieflow/core/data/cli.py:49`).

**researcher.** `R:core/researcher_core/lineage/compile.py:3-17` defines C001 orphan claim, C002 altered
number, C003 stale evidence, C004 qualifier mismatch, C005 retraction, C006 artifact-code drift, and
"a `source_error` … is NEVER a C003 or C005: it yields an `inconclusive` line item" (`:12-15`) — M-G keeps
that rule (an unreadable status is a warning, never C005). `passport.py:42-131` builds an RO-Crate 1.1
document (descriptor `conformsTo` `https://w3id.org/ro/crate/1.1`, root `./` Dataset with `hasPart`,
claims as `CreativeWork`, DOIs as `ScholarlyArticle`, runs as `CreateAction`). sha256:
`compile.py` 58d7e586…4bd, `passport.py` 7aabe31c…1a2 (read, not vendored).

**In-flight paper-lifecycle work (not used by M-G1).** Still uncommitted in `/home/ubuntu/Github/ScieFlow`
on 2026-10-31 (section 0). Its round-1 reading is kept in section 10 for M-G2; no M-G1 ticket reads or
changes it.

## Part A — Spec

### 5.1 Layout and files

```
workspace/<slug>/
  manuscript/ …                      (M-F: main.tex, sections, references.bib, metadata.yml)
  report/claims/claims.jsonl         one ledger line per claim (5.3), sorted by (file order, line, id)
  report/claims/ledger.json          {version, parser_version, main, manuscript_tree_sha256, count, claims_sha256}
  report/claims/check.json|check.md  the last check (5.6)
  revision/points/<label>/           a provenance point (5.9): point.json + files/…
  revision/round-<N>/                a decision-letter round (5.10–5.12)
  report/diff/<from>..<to>/          changes.json, diff.txt, diff.tex (when latexdiff exists)
  report/ro-crate/ro-crate-metadata.json   (5.14)
```

All writes are atomic (`journal.state.atomic_write`, M-F) and refuse a symbolic link on the path; every
read is confined to the run (`resolve()` + `is_relative_to(run)`), regular files only. Outputs are
deterministic: sorted keys, no timestamps, `\n` line ends, UTF-8.

### 5.2 Claim units (`claims/units.py`)

Input: `tex.read_tree(manuscript/, main, keep_comments=True)` (M-G-D16), document body only (from
`\begin{document}` to `\end{document}` or the first `\bibliography`, `\printbibliography` or
`\begin{thebibliography}`; a file without `\begin{document}` is all body, as M-F treats it).

- **Environment units**: each `figure`, `figure*`, `table`, `table*` environment is one unit (`unit:
  figure|table`); its text is the whole environment (caption and cells).
- **Abstract unit(s)**: sentences of `\begin{abstract}…\end{abstract}` (`unit: abstract`).
- **Sentence units**: the rest, split into paragraphs at blank lines and sectioning commands
  (`\section`, `\subsection`, `\subsubsection`, `\paragraph`, starred or not), then into sentences with
  `nblm.claims.split_sentences` on comment-stripped text (`unit: sentence`). Display-math environments
  (`tex.DISPLAY_ENVS`) belong to their sentence but contribute no numbers.
- **Section**: the title of the enclosing `\section` and `checklist.section_kinds(title)` (abstract units
  get kind `abstract`; units before the first section get `front`).
- **Comments**: a comment on line L (`%` not escaped, `tex.strip_line` rule) belongs to every unit whose
  span includes L; inside an environment it belongs to the environment unit.
- A unit is a **claim** when it has at least one checkable number (5.5), a reference (5.4) or a `\cite`.
  Other units are not ledgered.
- **Id**: `c-` + the first 12 hex of sha256(`file` + `\n` + normalised text) where normalised = NFC,
  comments removed, whitespace collapsed; a repeat within the ledger gets `-2`, `-3` in document order.
- **Limits**: at most 5,000 claims (more → exit 1 `claims-limit`); text stored up to 2,000 characters
  (`text_truncated: true` beyond).

### 5.3 Ledger line (`claims/ledger.py`, `schemas/claims-ledger.yml`)

```json
{"version": 1, "id": "c-1a2b3c4d5e6f", "file": "sections/results.tex", "line": 12, "end_line": 13,
 "unit": "sentence", "section": "Results", "section_kinds": ["results"],
 "text": "<normalised text>", "text_sha256": "<64 hex>",
 "numbers": [{"raw": "0.91", "value": "0.91", "decimals": 2, "percent": false, "relation": "="}],
 "refs": [{"type": "data", "id": "tbl-metrics", "via": "comment"},
          {"type": "run", "id": "denoise/sigma-1.5", "via": "inline"},
          {"type": "doi", "id": "10.5555/x.1", "key": "smith2019", "via": "cite"}],
 "cites": ["smith2019"],
 "pins": {"data:tbl-metrics": {"files": {"inputs/results/metrics.csv": "<sha256>"}}}}
```

- `build` writes `claims.jsonl` (one canonical JSON object per line) and `ledger.json`. Pins of a claim
  are carried from the previous ledger when its `id` and `refs` are unchanged; otherwise (and with
  `--repin`, `--repin <id>`) they are taken from the current evidence (5.4). An unresolved reference
  has no pin. `--repin` emits `note.claims_repinned` (count). Re-running `build` on an unchanged
  manuscript and unchanged evidence gives byte-identical files.
- `show <run> [--id ID] [--json]` prints the ledger (excerpts ≤ 60 characters in the table view).
- `ledger.json` `manuscript_tree_sha256` is `tex.source_tree_hash(manuscript/)`; `check` refuses a
  ledger whose hash differs from the current tree (`ledger-stale`, exit 1: "run `scieflow manuscript
  claims build`").

### 5.4 References and resolution (`claims/refs.py`, `claims/resolve.py`, `claims/evidence.py`)

Markers: `\[(data|run|doi):([^\]\s]{1,200})\]` in a comment of the unit (`via: comment`; the
paper-draft form is `% source: [data:<id>]`) or in its text (`via: inline`). Every `\cite`-family key
(`citations.CITE_RE`) is a `cite`; a key whose bib entry has a DOI (`nblm.claims.parse_bib`) adds a
`doi` ref (`via: cite`). DOIs are normalised (`https://doi.org/` prefix removed, lower case) and must pass
`core.review.claims.safe_doi`.

- **`data:<id>`** (id pattern `release.ID_PATTERN`): (1) an `inputs/manifest.yml` artifact with that
  `id` → `inputs/<file>`; (2) a `data.yml` `released` record → its `file` (sha256 re-checked with the
  `release._verify` rule; mismatch → C002 `artifact-altered`); (3) an `imports` record whose `release`
  equals the id → its `file` (same re-check). Two or more matches → `ref-ambiguous`; none →
  `ref-unresolved`; a manifest path that is absolute, contains `..`, leaves `inputs/` or is a symlink →
  `ref-unsafe` (all C001, error). **L2 rule (M-B 5.2 "only released artifacts … in a draft"):** when
  `levels.run_level(run)` is `L2`, a data ref resolved through the manifest (not a release or an import)
  is `data-unreleased` (error, not waivable).
- **`run:<ref>`**, inside `experiments/` of this run only (M-G-D15): (1) `<ref>` names a campaign folder
  with `results.json` → the **campaign**; (2) `<campaign>/<run_id>` with that campaign's
  `results.json` listing `run_id` → the **run**; (3) a bare `run_id` listed in exactly one campaign → the
  run; more than one → `ref-ambiguous`; none → `ref-unresolved`. Run ids pass `manifest._is_run_id`.
  Evidence files: a run's `metrics.json`, `manifest.json` and text-like files under `artifacts/`; a
  campaign's `results.json`, `results.csv`, `stats.json` and text-like files under `tables/`.
  Integrity: `manifest.verify_campaign(campaign)` problems naming that run (or any run, for a campaign
  ref) → C002 `artifact-altered`; a `legacy-run` note → warning `run-unverifiable`.
- **`doi:<doi>`**: the bib must contain the key (else `ref-unresolved`, C001); a bib entry without DOI →
  warning `doi-missing`; when `report/selected_dois.txt` exists and lacks the DOI → warning
  `doi-unselected` (the `citations.py:62-74` rule). Status: see 5.6 C005/C003. Every `\cite` key of
  a claim is also matched against the blockers, with or without a DOI (5.6 C005).
- **Text-like evidence**: extensions `.csv .tsv .json .txt .md .tex .yml .yaml`, ≤ 20 MB each, at most
  200 files per ref; other files (PNG, PDF, binaries) are listed and hashed but give no numbers
  (`number-uncheckable` warning when a claim's numbers can only be checked against them).
- **Pins**: `{"<type>:<id>": {"files": {<run-relative path>: <sha256>}}}` over the evidence files of
  the ref (sorted). DOI refs have no pin.

### 5.5 Numbers (`claims/numbers.py`) — M-G-D5

- Token: `[-−+]?(?:\d{1,3}(?:[,\u202f\u00a0 ]\d{3})+|\d+)(?:\.\d+)?` optionally followed by `\s*\\?%`;
  `−` and `\u2212` read as minus; thousands separators removed. Scientific notation in evidence
  (`1.2e-05`) is parsed; in the manuscript a `\times 10^{…}` form is not (follow-up; such a claim gets
  `number-uncheckable`).
- Relation: `<`, `\lt`, `≤`, `\le`, `\leq`, `>`, `\gt`, `≥`, `\ge`, `\geq` directly before the token (only
  spaces, `$` or `=` between) set `relation`; otherwise `=`.
- Not claims: tokens inside `\cite…{}`, `\ref{}`, `\eqref{}`, `\autoref{}`, `\label{}`, `\url{}`,
  `\href{…}{}` (first argument), `\includegraphics[…]{…}`, `\begin{…}[…]` options, `\documentclass`,
  display math; tokens directly after `Figure`, `Fig.`, `Figs.`, `Table`, `Tab.`, `Section`, `Sec.`,
  `Eq.`, `Equation`, `Supplementary`, `Appendix`, `Chapter`, `Step`, `Phase`, `Version`, `v` (with optional
  `~`/space, case-insensitive); integers 1900–2099 without `%` (years); ordinals (`1st`, `2nd`, …);
  tokens glued to letters on either side (`H2O`, `SF-36` → not checked).
- Match (C002 when a checked number has no match in the union of the claim's data/run evidence):
  manuscript value `t` with `d` decimals matches evidence value `a` when `round_half_up(a, d) == t` or
  `round_half_even(a, d) == t`; for `d = 0` `a` must be integral and equal; with `percent` also
  `a × 100` under the same rule; with a relation `<`/`≤`/`>`/`≥`, `a` must satisfy it. Decimal
  arithmetic (`decimal.Decimal`), never floats.
- A claim with data/run refs **and** a DOI cite: an unmatched number is the warning
  `number-unmatched-cited` (it may come from the cited paper), not C002.
- A claim with only DOI refs: numbers are not checked (`numbers_checked: false` in the report).
- Messages name the manuscript number, the claim's file:line and the evidence path; **never a value read
  from an evidence file** (M-B-D9).

### 5.6 The check (`claims/compile.py`, `claims/report.py`) — C001–C006

| Code | Rule ids | When | Severity |
|---|---|---|---|
| C001 orphan claim | `orphan`, `ref-unresolved`, `ref-ambiguous`, `ref-unsafe` | a checkable number and no data/run/doi ref; a ref that does not resolve | error in units with kinds `abstract`, `results`, or unit `table`/`figure`; warning elsewhere (`orphan` only; the `ref-*` rules are always errors) |
| C002 altered number | `number-missing`, `artifact-altered` | 5.5 match fails; evidence differs from its own record (5.4) | error |
| C003 stale evidence | `evidence-changed`, `source-corrected` | current evidence hashes ≠ pins; a cited DOI with a `corrected` flag in the latest evidence bundle | error / warning |
| C004 qualifier mismatch | — | reserved, never emitted (M-G-D3) | — |
| C005 retraction | `blocked-reference` | a blocker of `review.service.blockers()` names a reference of the claim: its `doi` (normalised as in 5.4) equals a ledgered DOI, or its `key` equals a cite key of the claim or that key's safe token (`k-` + first 16 hex of sha256(key) when the key is not `[A-Za-z0-9_.:/+-]{1,200}`; the `identity_gates._token` rule, reimplemented in `compile.py` because `_token` is private, and pinned equal to it by a test) | error, not waivable |
| C006 artifact-code drift | `code-dirty`, `code-not-ancestor` | a cited run's manifest `code.scieflow.git` or `code.pipeline.git` has `dirty: true`; its `sha` is not an ancestor of the repository HEAD (`git -C <repo root> merge-base --is-ancestor <sha> HEAD`, fixed argv, 10 s timeout, `GIT_OPTIONAL_LOCKS=0`; exit 1 or 128 → drift) | error |
| — | `data-unreleased` | 5.4 L2 rule | error, not waivable |
| — | `code-state-unknown`, `run-unverifiable`, `doi-missing`, `doi-unselected`, `doi-status-unknown`, `number-uncheckable`, `number-unmatched-cited` | as named in 5.4/5.5; `code-state-unknown` when a manifest `git` is null or git is unavailable; `doi-status-unknown` once per check when the run has no evidence round and no identity check | warning |

- The C005 message names the claim, the reference (DOI or key token) and the blocker `reason`
  (`retracted`, `unresolvable`, `identity-mismatch`, …) and says how it clears (fix the reference, or
  the user overrules the `review-blocker` gate); never the blocker's `detail` text.
- A status lookup that fails (`blockers()` raises `ValueError`/`OSError`) is the warning
  `status-unreadable`, never C005 (researcher's rule, section 4) — but the **package** treats it as a
  refusal (fail closed, 5.8).
- Output: `report/claims/check.json` `{version, manuscript_tree_sha256, ledger_sha256, counts: {by_code,
  errors, warnings, waived}, findings: [{code, rule, severity, claim, file, line, ref?, message}],
  waived: [{claim, code, rule}]}` sorted by (file order, line, code, rule); `check.md` the same as a
  table. `--json` prints the document; `--no-write` writes nothing. Exit 0 without errors, 1 with
  errors or bad input. One `note.claims_checked` event (counts) when written.

### 5.7 Waivers (`claims/waivers.py`) — M-G-D7

`scieflow manuscript claims waive <run> <claim-id> --code C00x --reason TEXT`: refused when
`SCIEFLOW_DISPATCH` is set ("the user waives; sub-agents never do"); the claim must exist in the
current ledger and currently have a finding with that code; C005 and `data-unreleased` are refused;
`--reason` 10–500 characters. Records `note.claims_waived` with actor `human`, `{claim, code,
text_sha256, reason}`. The check applies a waiver only when a `note.claims_waived` event with actor
`human` has the claim's current `text_sha256` (a changed sentence voids it); waived findings move to
`waived` and do not count as errors.

### 5.8 Package refusal (`package.py`) — M-G-D8

`_refuse_claims(project, run)` is called right after `_no_links(folder)` (`package.py:294`), so every
M-F refusal still comes first. It calls `claims.compile.check_run(project, run)` (public; M-G2's verify
gate will call the same function), which builds the ledger in memory (pins carried from an existing, readable
`claims.jsonl`; none otherwise), runs the check in memory and raises `PackageError("claims", "<n>
claim error(s): run scieflow manuscript claims check <run>")` on any error. Any exception while building
or checking → `PackageError("claims", "the claim ledger could not be built (<ExceptionType>); refused
(fail closed)")`. Nothing is written by this step. `package.json` is unchanged.

### 5.9 Provenance points and diffs (`points.py`, `diff.py`) — M-G-D12

- `scieflow manuscript point add <run> <label> [--from-package]`: label `^[a-z0-9][a-z0-9.-]{0,39}$`,
  refused if it exists. Without `--from-package` it copies the files `tex.source_tree_hash` counts
  (same exclusions) into `revision/points/<label>/files/` and writes `point.json` `{version, label,
  origin: "current", tree_sha256, files: {rel: sha256}}`. With `--from-package` (refused in a dispatch)
  it reads `report/submission/package.json` and `source.zip`, requires `sha256(source.zip) ==
  package.json files["source.zip"]`, and extracts entries whose names are relative, without `..`,
  backslash or NUL, at most 200 entries and 50 MB in total (`origin: "package"`, `package_sha256`). A
  symlink anywhere, more than 200 files or 50 MB → exit 1. `point list <run> [--json]`. Event
  `note.manuscript_point`.
- `scieflow manuscript diff <run> --from <label> [--to <label>|current] [--main main.tex] [--json]`:
  writes `report/diff/<from>..<to>/changes.json` (`added`, `removed`, `changed` with both sha256s),
  `diff.txt` (`difflib.unified_diff` over each side's flattened text, comments kept, `n=3`), and, when
  `shutil.which("latexdiff")` finds it, `diff.tex`: both sides are flattened by ScieFlow into
  `scratch/manuscript-diff/{old,new}.tex`, then `latexdiff --encoding=utf8 old.tex new.tex` runs with
  `cwd` that folder, `env={"PATH": "/usr/bin:/bin", "HOME": <that folder>}`, no shell, timeout 120 s,
  stdout capped at 20 MB, stderr tail kept for errors. Absent → `notes: ["latexdiff-not-installed"]`,
  exit 0. latexdiff failure → exit 1 with the last 20 stderr lines. Event `note.manuscript_diff`
  (counts).

### 5.10 Decision-letter ingest (`revision/letter.py`) — M-G-D9

`scieflow manuscript revision ingest <run> --letter FILE [--round N] [--decision accept|minor|major|reject]`:

- Refused in a dispatch. FILE: a regular file (no symlink), ≤ 1 MB, suffix `.txt`, `.md`, `.html` or
  `.htm` (HTML via `journal.html.to_text`); decoded UTF-8 (`errors="strict"`; invalid → exit 1), NFC,
  CRLF/CR → LF, trailing spaces removed. Round: `--round` or 1 + the highest existing; an existing
  round folder → exit 1.
- Writes `revision/round-<N>/letter.txt` and `letter.json` `{version, round, letter_sha256,
  source_suffix, decision, decision_source: "flag"|"letter"|"unknown", blocks: [{id, label_line,
  start, end, paragraphs: [{id, start, end}]}]}`.
- **Blocks**: a header is a line ≤ 120 characters matching (case-insensitive)
  `^\s*(?:reviewer|referee)\s*#?\s*(\d{1,2})\b` → `R<n>`, or
  `^\s*(?:(?:associate|academic|handling|section)\s+)?editor(?:-in-chief)?\b` → `E` (a later editor
  header → `E2`, `E3`). Text before the first header belongs to `E`. A repeated `R<n>` header is
  treated as text (continuation). Paragraphs: maximal runs of non-blank lines; ids `<block>.p<k>`.
  An empty block is dropped.
- **Decision**: `--decision` wins (`flag`); otherwise the first match of `accept(ed)? (for
  publication|as is)`, `minor revision`, `major revision`, `reject(ed)?` in the `E` block; two different
  matches → `unknown`.
- Then point `submitted-r<N>`: from the package when `report/submission/package.json` exists and its
  `source.zip` checks (5.9), else from the current manuscript with the printed note `point taken from
  the current manuscript, not from a submission package`; an existing point of that name is kept.
- Event `note.revision_ingested` `{round, blocks, paragraphs, decision, letter_sha256}` — never letter
  text.

### 5.11 Structuring prompt, draft and adopt (`revision/draft.py`, `revision/adopt.py`)

- `revision prompt <run> --round N --agent NAME [--write]`: a prompt for `--role research.submitter`
  that contains the output path `revision/round-<N>/draft/<NAME>.json`, the draft format below, the
  rules, and the letter **as an untrusted fenced block with line numbers** (`passes._fenced(…,
  untrusted=True)`), plus the block/paragraph table. `--write` stores it in `prompts/` and prints the
  `scieflow agent run --role research.submitter …` line (the review-prompt pattern,
  `core/review/cli.py:384-401`).
- Draft format:
  ```json
  {"version": 1, "round": 1, "letter_sha256": "<64 hex>",
   "reviews": [{"block": "R1", "review": {<manuscript-review document>}}],
   "not_comments": [{"paragraph": "R1.p1", "reason": "greeting"}]}
  ```
  Each `review` must validate with `decisions.schema_error("manuscript-review", …)` and in addition:
  `agent` = `journal-<block in lower case>`, `reviewed` = `manuscript`, no `pass`, `evidence`,
  `followup_requests`, `scite_status`, `changed_from_blind`; ids unique within the review;
  `location` = `letter:<a>-<b>` (1-based lines of `letter.txt`) inside that block; `problem` (major) /
  `comment` (minor) is a **verbatim** substring of letter lines a..b after NFC and whitespace collapse,
  at least 20 characters or the whole range text; `why_it_matters` / `resolution` are free text (shown
  as "agent's reading" in the response starter).
- `revision adopt <run> --round N --draft PATH`: refused in a dispatch; PATH inside the run, regular, ≤
  1 MB. Checks in order, nothing written on any failure: `letter.txt` sha = `letter.json` = draft
  `letter_sha256` (`letter-changed`); block set equal (`blocks-differ`); schema and the extra rules
  (`review-invalid` with the JSON path); verbatim (`quote-not-in-letter`, naming comment id and line
  range, never quoting the letter beyond 60 characters); **every paragraph of every block is overlapped
  by a comment range or listed once in `not_comments`** with a reason of 3–200 characters
  (`letter-uncovered`, `not-comment-conflict` when listed and commented). Writes
  `revision/round-<N>/reviews/<block>.json` (canonical JSON, one manuscript-review document each) and
  `adopted.json` `{version, round, letter_sha256, draft_sha256, comments: ["R1.M1", "R1.m1", …],
  not_comments}`; an existing `adopted.json` → `--replace` required. Event `note.revision_adopted`
  (counts).

### 5.12 Response, coverage and the response letter (`revision/respond.py`, `coverage.py`, `render.py`) — M-G-D10

- `revision respond <run> --round N [--force]` writes `revision/round-<N>/response.md`: a header with the
  decision and the `not_comments` list, then per comment id in block order `## R1.M1 (major) —
  letter:12-15`, the comment as a `> ` quote, the agent's reading (major only), and the lines
  `Response: [write your response]` and `Change: [file — what changed]` / `No change: [why]`.
  Refuses to overwrite without `--force`.
- `revision coverage <run> --round N [--json]`: parses `response.md` headings (`^#{1,6}\s*([ERE0-9.Mm]+)`
  for ids of `adopted.json`); per comment id: `missing` (no section), `no-response` (no `Response:` line
  with text other than a `[…]` placeholder), `no-change-line` (neither line), `both-change-lines`,
  `no-change-reason` (`No change:` text < 10 characters), `change-path` (the `Change:` text must begin
  with a manuscript-relative path that `tex`'s rules accept), `change-not-found` (that file is equal in
  point `submitted-r<N>` and the current manuscript, or absent from both). Unknown ids → warning
  `unknown-comment`. Writes `coverage.json` `{ok, comments, problems}`; exit 0 when ok, else 1. Event
  `note.revision_coverage`.
- `revision letter <run> --round N [--force]`: refused unless coverage is ok. Writes
  `manuscript/letters/response-to-reviewers-r<N>.tex`: the vendored template up to and including its
  `\newpage` after `\tableofcontents`, filled by `letters.response_text` (title), then per block
  `\section{Reviewer n}` (`Editor` for E blocks) and per comment `\subsection{Comment R1.M1}`,
  `\reviewercomment{…}`, `\response{…}`, and the `Change:` text, all through `render.escape`, then
  `\end{document}`. `letters/` is outside the source-tree hash (`tex.py:56`).
- Acceptance loop (GAP "letter → JSON → coverage loop green on a fixture"): ingest → draft → adopt →
  respond → answer and change a section file → coverage exit 0 → letter written.

### 5.13 Lifecycle wiring — moved to M-G2 (M-G-D17, M-G-D19)

Not part of M-G1. The round-1 design (verify-phase claim gate, `paper-revision` workflow) is section
10.2; the M-G2 plan re-designs it against the landed paper-lifecycle code. M-G1 provides what M-G2 will
call: the in-memory build-and-check used by the package refusal (5.8) is a public function
`claims.compile.check_run(project, run) -> Report` so that M-G2 needs no change in the claims package.

### 5.14 RO-Crate (`crate.py`) — M-G-D13

`scieflow manuscript crate <run> [--json] [--no-write]` → `report/ro-crate/ro-crate-metadata.json`:

- `@context` `https://w3id.org/ro/crate/1.1/context`; entity 1 the metadata descriptor `{"@id":
  "ro-crate-metadata.json", "@type": "CreativeWork", "conformsTo": {"@id":
  "https://w3id.org/ro/crate/1.1"}, "about": {"@id": "./"}}`; entity 2 the root `{"@id": "./",
  "@type": "Dataset", "name": <metadata.yml title or the run slug>, "hasPart": [...], "author": [...]}`.
- Paths in `@id` are relative to the run folder (the crate root). Manuscript source files (the
  source-tree selection) and every evidence file of 5.4 are `File` entities `{name, identifier:
  "sha256:<hex>", encodingFormat}`; authors from `metadata.yml` are `Person` `#person-<k>` `{name,
  identifier: <ORCID URL when valid>}` — no e-mail, no affiliation address.
- Claims are `CreativeWork` `#claim-<id>` `{name: "claim <id>", text, isPartOf: {"@id": <file>},
  additionalType: <unit>, citation: [DOI entities], isBasedOn: [data/run evidence entities]}`.
  DOIs are `ScholarlyArticle` `https://doi.org/<doi>` `{identifier: <doi>}`. Runs are `CreateAction`
  `#run-<campaign>-<run_id with / → ->` `{name, endTime: manifest finished, instrument: {"@id":
  "#stage-<name>"}, result: [artifact files]}` with `SoftwareApplication` `#stage-<name>` `{name,
  identifier: "sha256:<script sha256>"}`.
- L2 run: a data ref resolved through the manifest (not released) becomes `#data-<id>` `{"@type":
  "CreativeWork", name: <id>, conditionsOfAccess: "L2 data, not released"}` with no path or hash.
- Order: descriptor, root, then entities sorted by `@id`; every `{"@id": …}` reference points to an
  entity in `@graph`; no absolute path, no `..`. Deterministic bytes. Event `note.manuscript_crate`.

### 5.15 CLI surface and exit codes

```
scieflow manuscript claims build <run> [--main F] [--repin] [--repin-claim ID ...] [--json]
scieflow manuscript claims show  <run> [--id ID] [--json]
scieflow manuscript claims check <run> [--json] [--no-write]
scieflow manuscript claims waive <run> <claim-id> --code C00x --reason TEXT
scieflow manuscript point add  <run> <label> [--from-package]
scieflow manuscript point list <run> [--json]
scieflow manuscript diff <run> --from LABEL [--to LABEL|current] [--main F] [--json]
scieflow manuscript revision ingest   <run> --letter FILE [--round N] [--decision D]
scieflow manuscript revision prompt   <run> --round N --agent NAME [--write]
scieflow manuscript revision adopt    <run> --round N --draft PATH [--replace]
scieflow manuscript revision respond  <run> --round N [--force]
scieflow manuscript revision coverage <run> --round N [--json]
scieflow manuscript revision letter   <run> --round N [--force]
scieflow manuscript revision show     <run> [--round N] [--json]
scieflow manuscript crate <run> [--json] [--no-write]
```

Exit codes: the module's `0` ok, `1` refused or failed check, `2` usage (`cli.py:20`). Refusals in a
dispatch name the command and say it is the user's or the coordinator's.

### 5.16 Data protection, security and integrity (reviewed in every ticket that touches them)

| Property | How |
|---|---|
| No egress | M-G makes no network call; `doi` status comes from local review state only; no new dependency |
| No data values in messages or events | messages name manuscript numbers, files and rule ids; evidence values never printed (M-B-D9); excerpts ≤ 60 chars; events carry counts, ids, hashes |
| Confinement | every path resolved inside the run; symlinks refused; run ids per `_is_run_id`; zip names validated; ≤ limits of 5.2, 5.4, 5.9, 5.10 |
| Untrusted letters | letter text is data (root rule 8): fenced as untrusted in the prompt; HTML through the existing text extractor; never executed |
| Quotes, not trust | every adopted comment re-checked verbatim against the stored `letter.txt`, not the prompt copy; every paragraph accounted for |
| Human-only actions | `revision ingest`, `revision adopt`, `point add --from-package`, `claims waive` refused when `SCIEFLOW_DISPATCH` is set; waivers count only from `actor: human` events |
| L2 | `data-unreleased` error; RO-Crate lists unreleased L2 data without path or hash; nothing leaves the run |
| Fail closed | package refuses when the ledger or blocker state cannot be read (the verify gate is M-G2) |
| Subprocesses | `git merge-base` and `latexdiff` only: fixed argv, no shell, timeouts, minimal env, capped output |
| Determinism | sorted JSON, no timestamps, byte-identical on re-run |

### 5.17 What M-G does not do (stated)

No lifecycle wiring (verify gate, `paper-revision` workflow: M-G2, section 10); no C004 (M-G-D3); no PROV-JSON-LD; no PDF of the diff; no PDF letters; no web page or menu item (D7: the
CLI is complete); no cross-run `[run:]` (M-G-D15); no change to internal review rounds; numbers written as
words ("forty-three") are not recognised (same limit as `core/review/claims.py:21-22`); a number that
also happens to appear elsewhere in the evidence file is accepted ("appears in" is weaker than "is the
value of row X"); retraction status is only as fresh as the last evidence round or identity check.

## Part B — Implementation plan

## 6. Tickets

Staffing: Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`);
Sonnet 5.5 @ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`); provider anthropic. A usage limit is
a checkpointed pause: no model swap.

Every card: no claude/codex/agy, no live probe, no network (git ls-remote/fetch in t0 only), no push/PR,
no credentials, no patient data (synthetic fixtures only: invented manuscripts, data files, runs and
letters), never edits a "never edited" file of section 3 (the t9 protected edits are applied by the
operator) and never edits the nine paper-lifecycle paths. Every implementation, fix, branch and final card
runs the lifecycle-path guard of section 3 and reports its exit code as `inflight_guard_exit`. There is
no ticket t8 in this run (section 0); t7r PASS goes to t9.

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from the worktree `RUN/scratch/m-g-wt` (WT):
  `cd WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=WT/src:WT:WT/scripts PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp ~/.cache/scieflow-m-g-<step>/pt <paths>`.
  Once per card, the import-origin check first:
  `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`;
  its output must start with `WT/src/`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/m-g/junit G6_CACHE_DIR=~/.cache/scieflow-m-g RUN/tools/g6_pytest.sh WT <head-sha> m-g-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path, under the shared lock of the standing
  rules (`flock -w 7200 /home/ubuntu/.cache/scieflow-full-suite.lock …`), started in the background with
  completion notification and a log file.
- **Sealed probes:** `… g6_pytest.sh WT <head> m-g-sealed -- tests/probes` and the explicit file
  `tests/probes/test_k_task8_binding.py`, also under the lock.
- **Load-flake nodes**: the ones named in the installer's standing rules, one rerun, both results
  recorded. No rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/m-g/executions.jsonl` and states RED and GREEN evidence in its result.
- **Focused sets** per ticket (clean-tree runner on the ticket's final commit): t1
  `tests/research/manuscript/claims tests/research/manuscript/test_tex_tree.py tests/test_nblm_claims.py`;
  t2 `tests/research/manuscript/claims tests/core/data tests/experiments/test_manifest.py`; t3
  `tests/research/manuscript tests/core/review/test_review_service.py
  tests/core/review/test_identity_blockers.py tests/vendor`; t4
  `tests/research/manuscript/points tests/research/manuscript/test_package_outputs.py`; t5
  `tests/research/revision tests/core/review`; t6 `tests/research/revision
  tests/research/manuscript/test_letters.py`; t7 `tests/research/manuscript/crate tests/vendor`; t9
  `tests/test_docs_claims_revision.py
  tests/test_docs_journal.py tests/test_docs_role.py tests/test_docs_data_protection.py tests/research`.
- Projects in tests are temporary folders with a copy of the repo's `schemas/` (as M-F tests do).
  Dispatch is simulated with `SCIEFLOW_DISPATCH=1`. Git ancestry tests use a throwaway `git init`
  repository in the test temp dir. The real-latexdiff test is
  `@pytest.mark.skipif(shutil.which("latexdiff") is None)` (it skips on this host).

### t0 — worktree, plan and spec copy, green base (Haiku)

1. Section 2: `ls-remote` → base; object-only fetch; the nine ancestor checks (M-C2 included) and the
   M-C2 publish receipt (`merged: true`); otherwise `BASE_RED` with the reason. `worktree add -b dev/m-g
   RUN/scratch/m-g-wt <base>` (on a retry reuse it; never reset).
2. **Green-base gate on `base`** (HEAD = base, clean): full default suite **0 failed, 0 errors**; sealed
   probes 0 failed. t0 records what it measures. Only the load-flake rule may clear a failure; otherwise
   `BASE_RED` with node ids and junit paths, nothing else.
3. Re-verify every `path:line` cite of section 4 on `base` and write `reviews/m-g-t0-baseline.md` (per
   cite: still present / moved (new line) / changed; base may be newer than `8b37480` if another run
   merged first). Inventories: (a) whether the paper-lifecycle work has landed on base (`workflows:` in
   `schemas/status.yml`, `def phases_for(` in `core/run/status.py`) — recorded for M-G2 only, it changes
   nothing in M-G1; (b) M-C2 surface: `service.blockers` = evidence + identity entries
   (`identity_gates.blockers`, reason prefix, `_token` rule) — record function, lines and reason strings;
   (c) each protected edit's `old` text occurs exactly once on base;
   (d) every manuscript fixture used by `tests/research/manuscript/test_package_*.py` and
   `tests/research/manuscript/t8_support.py` (M-F's test helper; the name is M-F's ticket t8, unrelated
   to this plan's former t8): list any digit token in its document body (M-G-D8 expects none outside
   `\ref`/`\cite`/years); (e) the researcher files of section 4 still have their sha256.
4. Copy `reviews/m-g-plan.md` byte for byte to
   `docs/superpowers/plans/2026-10-31-m-g1-claim-ledger-revision.md`, and its Part A (from the line
   `## Part A — Spec` up to, not including, the line `## Part B — Implementation plan`) to
   `docs/superpowers/specs/2026-10-31-m-g1-claim-ledger-revision-design.md` with this first line:
   `# M-G1 claim ledger and journal revision — design (Part A of docs/superpowers/plans/2026-10-31-m-g1-claim-ledger-revision.md)`
   and one blank line. Commit both alone ("docs(plans): M-G1 claim ledger and journal revision spec and
   plan").
Verdicts `DONE` | `BASE_RED`.

### t1 — claim units, numbers, references, ledger build/show (Haiku) → t1r (Sonnet)

Files: `src/scieflow/research/manuscript/tex.py` (`keep_comments`), `schemas/claims-ledger.yml`,
`src/scieflow/research/manuscript/claims/{__init__,units,numbers,refs,ledger}.py`, `cli.py` (`claims
build`, `claims show`; resolution in `build` is stubbed to "no pins" until t2), tests
`tests/research/manuscript/claims/{conftest.py, test_units.py, test_numbers.py, test_refs.py,
test_ledger_build.py}` and `tests/research/manuscript/test_tex_tree.py` (added tests only). Fixture: a
synthetic manuscript (invented study "synthetic gait timing") with abstract, Introduction (a cited
number), Methods, Results (three numeric sentences with `% source:` comments, one with an inline
`[run:…]`), a table with a `% source:` comment inside, a figure with a caption number, display math, a
`Figure~\ref`, a year, and `references.bib`.

RED → GREEN:
1. `read_tree(…, keep_comments=True)` keeps `% source:` lines and the same `Line` file/lineno mapping;
   `read_tree(…)` output byte-identical to base for the M-F fixtures (compare to the base function on
   every file under `tests/research/manuscript/`).
2. Units: exact expected unit list (unit kind, file, line, end_line, section, section_kinds) for the
   fixture; environment units; abstract units; comments attached to the right units; text
   normalisation; ids and the `-2` duplicate rule.
3. Numbers: a table-driven test over at least 40 cases of 5.5 (tokens, thousands separators, minus
   signs, percent, relations, every exclusion: `\cite`, `\ref`, labels, URLs, display math, Figure/Table
   prefixes, years, ordinals, glued tokens).
4. Refs: comment and inline markers, `\cite` → bib DOI with `safe_doi`, a key without DOI, a malformed
   marker ignored, a 201-character id refused.
5. Ledger: `claims build` writes `claims.jsonl` + `ledger.json` with the exact expected first line;
   byte-identical re-run; 5,001 claims → exit 1 `claims-limit`; `claims show --json`; schema pin test
   (`schemas/claims-ledger.yml` keys equal the writer's keys).

t1r checklist: the M-F default path of `read_tree` unchanged; number rule exactly 5.5 (no float); no
evidence read yet; no network; deterministic; lifecycle guard 0.

### t2 — resolver and evidence pins (Haiku) → t2r (Sonnet)

Files: `src/scieflow/research/manuscript/claims/{resolve,evidence}.py`, `ledger.py` (pins), tests
`tests/research/manuscript/claims/{test_resolve_data.py, test_resolve_run.py, test_resolve_doi.py,
test_pins.py}`. Fixtures: a synthetic `inputs/manifest.yml` + CSV/JSON results; a `data.yml` with one
released and one imported record (M-B fixture style); an `experiments/` tree with two campaigns written
through `manifest.write_manifest` (M-D2 test helpers in `tests/experiments/manifest_support.py`, imported,
not changed); a bib and `report/selected_dois.txt`.

RED → GREEN:
1. data: manifest id → file; released id → file with sha check (tamper → `artifact-altered`); import
   id; both manifest and release → `ref-ambiguous`; unknown → `ref-unresolved`; `../x`, absolute,
   symlink → `ref-unsafe`; an L2 run (M-B declaration fixture) with a manifest-only ref →
   `data-unreleased`, with a released ref → no finding.
2. run: campaign name; `campaign/run`; bare unique run id; bare id in two campaigns → `ref-ambiguous`;
   a run id containing `/`; `verify_campaign` problem (tampered artifact) → `artifact-altered`;
   `legacy-run` → `run-unverifiable`; evidence file selection exactly as 5.4.
3. doi: key without bib entry → `ref-unresolved`; entry without DOI → `doi-missing`; not in
   `selected_dois.txt` → `doi-unselected`; file absent → no warning.
4. Pins: build pins evidence; second build carries pins for unchanged claims; changed refs → new pins;
   `--repin` and `--repin-claim` re-pin and emit one `note.claims_repinned`; files > 20 MB and > 200 files
   per ref handled per 5.4; no evidence value appears in any message (search every message for the
   fixture's numbers).

t2r checklist: confinement and symlink refusals; L2 rule; no experiments/data/review code edited
(imported only); messages without evidence values; lifecycle guard 0.

### t3 — the check (C001–C006), waivers, package refusal (Haiku) → t3r (Sonnet)

Files: `src/scieflow/research/manuscript/claims/{compile,waivers,report}.py`, `cli.py` (`claims check`,
`claims waive`), `src/scieflow/research/manuscript/package.py` (`_refuse_claims`), `CREDITS.md`,
`VENDORED.md`, tests `tests/research/manuscript/claims/{test_check_codes.py, test_number_match.py,
test_blocked_reference.py, test_code_drift.py, test_waivers.py, test_check_cli.py}` and
`tests/research/manuscript/test_package_preconditions.py` (added tests only).

RED → GREEN:
1. **(GAP acceptance "planted wrong number fails")** a Results sentence `SSIM was 0.93 % source:
   [data:tbl-metrics]` whose CSV holds `0.9134` → `claims check` exit 1 with C002 `number-missing` at
   that file:line; `0.91` → no finding; the message contains `0.93` and the CSV path and no value from
   the CSV.
2. Matching table (≥ 30 cases): half-up/half-even, integers exact, percent ↔ fraction, `p < 0.001`
   against `0.0004` (match) and `0.002` (C002), `≥`, negative numbers, thousands separators, scientific
   notation in evidence; `number-unmatched-cited`; `number-uncheckable` for a PNG-only ref.
3. **(GAP acceptance "planted retracted DOI blocks")** the run gets `tests/fixtures/review/run/review`
   (copied, as `t8_support.add_review` does); a Results sentence `\cite{novak2020}` → C005
   `blocked-reference` error, exit 1; after the human overrule decision of the fixture (`OVERRULE` of
   `t8_support.py`) → no C005; `claims waive … --code C005` → refused; `blockers()` raising → warning
   `status-unreadable` in `check`, refusal in package; identity blockers (monkeypatch
   `scieflow.core.review.identity_gates.blockers` to return entries in the exact shape of
   `identity_gates.py:282-284`): `identity-mismatch` on a safe key → C005; on a key with a space, given
   as its `k-…` token → C005 on the claim citing that raw key; `identity-unresolvable` with only a DOI →
   C005; the local token rule equals `identity_gates._token` for a safe and an unsafe key.
4. C001: orphan number in Results → error; in Introduction → warning; `ref-unresolved` → error.
   C003: change the CSV after build → `evidence-changed`; `--repin` clears it; a `corrected` flag in the
   bundle → `source-corrected` warning. C006 (throwaway git repo): dirty manifest → `code-dirty`; a sha
   not in the repo → `code-not-ancestor`; an ancestor → none; git missing (PATH without git) →
   `code-state-unknown` warning. C004 never appears (search all reports).
5. Waivers: refused with `SCIEFLOW_DISPATCH=1`; unknown claim; a code the claim does not have; reason
   length; a waived C002 moves to `waived` and the exit becomes 0; editing the sentence voids it; an
   `actor: agent` waiver event is ignored.
6. Reports: `check.json`/`check.md` exact for the fixture, deterministic; `--no-write`; `--json`;
   `ledger-stale` when the manuscript changed after build; one `note.claims_checked`.
7. Package: a run that passes every M-F precondition but has a C002 → `PackageError` rule `claims`,
   nothing written; all existing `tests/research/manuscript/test_package_*.py` tests green unchanged;
   a ledger exception → refused fail closed.
8. CREDITS/VENDORED: the new paragraph and "not vendored: `core/researcher_core/lineage/`" line;
   `tests/vendor/test_vendored_integrity.py` green unchanged.

t3r checklist: severity table exactly 5.6; C005 only from `service.blockers` (evidence and identity
entries, token keys matched); `check_run` public and used by the package; fail-closed package; no
evidence values or blocker `detail` text in messages; existing tests unchanged; lifecycle guard 0.

### t4 — provenance points and diff (Haiku) → t4r (Sonnet)

Files: `src/scieflow/research/manuscript/{points,diff}.py`, `cli.py` (`point add|list`, `diff`), tests
`tests/research/manuscript/points/{test_points.py, test_points_from_package.py, test_diff.py,
test_diff_latexdiff.py}`.

RED → GREEN:
1. `point add` copies exactly the source-tree selection; `point.json` hashes; existing label → exit 1;
   label pattern; symlink in the tree → refused; 201 files → refused.
2. `--from-package` (package fixture built with M-F's t8 helpers): extracts `source.zip`; a tampered
   zip (sha ≠ `package.json`) → refused; an entry `../x`, `/abs`, `a\b` → refused; refused with
   `SCIEFLOW_DISPATCH=1`.
3. `diff`: `changes.json` and `diff.txt` exact for a fixture pair (one changed, one added, one removed
   file); deterministic; `--to current`; flattening follows `\input` on each side.
4. latexdiff (subprocess mocked): argv exactly `["latexdiff", "--encoding=utf8", "old.tex",
   "new.tex"]`, cwd and env per 5.9, timeout; absent → note, exit 0; non-zero → exit 1 with stderr tail;
   the real-binary test skips here.

t4r checklist: zip extraction safety; fixed argv, no shell, minimal env; deterministic; lifecycle
guard 0.

### t5 — decision-letter ingest, prompt and adopt (Haiku) → t5r (Sonnet)

Files: `src/scieflow/research/revision/{__init__,letter,draft,adopt}.py`, `cli.py` (`revision ingest`,
`prompt`, `adopt`, `show`), tests `tests/research/revision/{conftest.py, test_ingest.py,
test_blocks.py, test_prompt.py, test_adopt.py}`. Fixture: an invented decision letter ("Journal of
Synthetic Neurology", editor + two reviewers, numbered comments, a greeting, a closing), as `.txt` and as
`.html`, and a valid agent draft.

RED → GREEN:
1. Ingest: `letter.txt` normalised; `letter.json` blocks `E`, `R1`, `R2` with exact line ranges and
   paragraphs; decision `major` from the letter; `--decision minor` wins; two decisions → `unknown`; HTML
   gives the same blocks; a symlink, a `.pdf`, 1 MB + 1 byte, invalid UTF-8 → exit 1; refused with
   `SCIEFLOW_DISPATCH=1`; existing round → exit 1; point `submitted-r1` created (from the package when
   present, else current with the note); one `note.revision_ingested` without letter text.
2. Prompt: contains the output path, the format, the fenced untrusted letter with line numbers and the
   paragraph table; `--write` stores it and prints the `--role research.submitter` dispatch line.
3. Adopt: the valid draft → `reviews/R1.json`, `reviews/R2.json`, `reviews/E.json` each passing
   `decisions.schema_error("manuscript-review", …)`, and `adopted.json`; refusals each with nothing
   written: changed letter, block set differs, schema error, `pass` present, bad `agent`, location
   outside the block, a paraphrased comment (`quote-not-in-letter`), a dropped paragraph
   (`letter-uncovered`), a paragraph both commented and in `not_comments`, a draft outside the run or
   > 1 MB, `SCIEFLOW_DISPATCH=1`; existing `adopted.json` without `--replace`.

t5r checklist: quotes checked against the stored letter; every paragraph accounted for; letter fenced as
untrusted; no letter text beyond 60 characters in any message or event; `core/review/*` unchanged;
lifecycle guard 0.

### t6 — response, coverage and the response letter (Haiku) → t6r (Sonnet)

Files: `src/scieflow/research/revision/{respond,coverage,render}.py`, `cli.py` (`revision respond`,
`coverage`, `letter`), tests `tests/research/revision/{test_respond.py, test_coverage.py,
test_letter_render.py, test_loop.py}`.

RED → GREEN:
1. Respond: exact `response.md` for the fixture; `--force` rule.
2. Coverage: every rule of 5.12 with one case each; `change-not-found` when the named file equals the
   `submitted-r1` point; unknown ids warning; `coverage.json` deterministic.
3. **(GAP acceptance "letter → JSON → coverage loop green on a fixture")** `test_loop.py`: ingest →
   adopt (fixture draft) → respond → fill every section and change `sections/results.tex` → coverage
   exit 0 → `revision letter` writes `manuscript/letters/response-to-reviewers-r1.tex`; removing one
   section → exit 1 naming that id; `revision letter` then refused.
4. Letter: exact expected TeX for a two-comment fixture (escaping `_ & % # $`), the template preamble
   byte-equal to the vendored file's head; `--force` rule; the manuscript source-tree hash unchanged by
   the letter.

t6r checklist: every comment required (M-G-D10); change claims verified against the point; vendored
template not modified; lifecycle guard 0.

### t7 — RO-Crate export (Haiku) → t7r (Sonnet)

Files: `src/scieflow/research/manuscript/crate.py`, `cli.py` (`crate`), `CREDITS.md` (RO-Crate sentence),
tests `tests/research/manuscript/crate/{test_crate_structure.py, test_crate_l2.py}`.

RED → GREEN:
1. Structure: descriptor and root per 5.14; every `@id` reference resolves inside `@graph`; entity sort
   order; `identifier` sha256 values equal the files; claims, DOIs, runs, stage, persons present for the
   t2/t3 fixture; no e-mail (string search), no absolute path, no `..`; byte-identical re-run.
2. L2: an L2 run with one released and one unreleased data ref → the released one as `File` with hash,
   the unreleased as `#data-<id>` without path or hash.
3. `--no-write`, `--json`; one `note.manuscript_crate`.

t7r checklist: RO-Crate 1.1 required properties; no personal data beyond name and ORCID; L2 rule;
lifecycle guard 0.

### t8 — not in this run (M-G2)

Ticket t8 (paper-lifecycle wiring) is milestone M-G2 (section 10). The pipeline goes from t7r PASS to t9.

### t9 — docs, skills, protected edits (Haiku) → t9r (Sonnet)

Before t9 the operator applies the two protected edits (installer step `pe_t9`); t9 commits both files in
one commit and creates the `skills/journal-revision` symlink.

Files: section 3's docs and skills rows, `mkdocs.yml`, `src/scieflow/research/AGENTS.md` and
`AGENTS.md` (commit of the applied edits), `tests/test_docs_claims_revision.py`.

RED → GREEN:
1. Docs test: `docs/research/claims.md` and `docs/research/revision.md` exist and are in the `mkdocs.yml`
   nav; they name every command and flag of 5.15, every code and rule id of 5.6, the number rule, the
   waiver limits, the L2 rule, the letter formats, the coverage rules and the residuals of section 8,
   and that the verify-phase gate is not yet wired (M-G2);
   `docs/cli.md` has the new `scieflow manuscript` subcommands; every documented flag exists in
   `--help` (CliRunner walk); `docs/research/data-packages.md` mentions `claims check`.
2. Skills test (same file): the journal-revision skill names `claims build`/`check`, that `waive` and
   `ingest` are the user's, `revision prompt` with `--role research.submitter`, the verbatim rule,
   `adopt` by the coordinator, `respond`, `coverage`, `letter`, `point`, `diff`, `crate`; paper-draft
   Phase 5 step 3 runs `scieflow manuscript claims build` and `claims check` instead of the hand
   spot-check, and Phase 7 notes that `package` refuses on claim errors; `skills/journal-revision`
   resolves to the skill; `tests/test_docs_role.py` green.
3. Rules: `src/scieflow/research/AGENTS.md` and `AGENTS.md` equal the approved texts of section 3 byte
   for byte in the edited regions; `tests/test_docs_journal.py`, `tests/test_docs_role.py`,
   `tests/test_docs_data_protection.py` still pass.
4. `mkdocs build --strict` into `RUN/scratch/m-g/site` if mkdocs is in the venv (else recorded "not
   available").

t9r checklist: docs ↔ code (every documented flag exists); the skill never lets an agent waive, ingest,
adopt from a dispatch or edit `report/claims/`; protected edits byte-exact; no other AGENTS.md change;
lifecycle guard 0.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip: full default suite 0 failed / 0 errors and sealed probes 0 failed (absolute
counts, the delta against t0 explained by added tests); `mkdocs build --strict` (or "not available");
the nine paper-lifecycle paths unchanged (`git diff --exit-code base..tip -- <the nine paths>`, which
includes `config/agents.yml`); linear history (no merge commits), at least one commit per ticket t0–t7
and t9;
`git diff base..tip -- tests/` removes no line; every "never edited" file of section 3 absent from the
diff; the two AGENTS.md files changed only by the t9 protected-edit commit and equal to the approved
text; no other AGENTS.md changed; `git diff base..tip -- pyproject.toml uv.lock` empty; the vendored
files unchanged. Writes `RUN/scratch/m-g/pr-body.md` (plain English: the five capabilities of section 1 items 1–5, that the
lifecycle wiring is M-G2, decisions M-G-D1..D19, absolute counts, residuals R1–R9, follow-ups, "please
merge with a merge commit"; no credentials, prompts or patient data) and `reviews/m-g-branch-report.md`. Any failure → verdict `RED`
with node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

Whole branch `base..tip`: spec (every Part A item; the GAP acceptance criteria — planted wrong number
fails, planted retracted DOI blocks, letter → JSON → coverage loop green; decisions M-G-D1..D19; no
existing test line removed; the protected edits byte-exact; the nine paper-lifecycle paths untouched),
code and security (section 5.16: no egress, no evidence values in messages, confinement, untrusted
letters, verbatim quotes against the stored letter, human-only commands, L2 rule, fail-closed package,
subprocess safety, determinism), evidence (re-run memory-capped on the tip `tests/research/manuscript
tests/research/revision tests/core/review tests/test_docs_claims_revision.py tests/vendor`, confirm the branch result's absolute counts,
`pr-body.md` accurate). Output `reviews/m-g-final-review-r<N>.md`. PASS → the installer's publish step;
CHANGES → final_fix (new commits, full suites re-run) → final; FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/m-g-run.yml`, linear)

```
t0        Haiku   worktree, plan/spec copy, green base   DONE→t1, BASE_RED→escalate
t1        Haiku   units, numbers, refs, ledger build     DONE→t1r
t1r       Sonnet  review                                 PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                      DONE→t1r
t2/t2r/t2f        resolver and pins                      same shape → t3
t3/t3r/t3f        check C001–C006, waivers, package      same shape → t4
t4/t4r/t4f        provenance points and diff             same shape → t5
t5/t5r/t5f        letter ingest, prompt, adopt           same shape → t6
t6/t6r/t6f        response, coverage, letter             same shape → t7
t7/t7r/t7f        RO-Crate                               same shape → t9   (installer inserts pe_t9)
(no t8: M-G2)
t9/t9r/t9f        docs, skills, protected edits          same shape → branch
branch    Haiku   full suites + PR body                  DONE→final, RED→escalate
final     Sonnet  whole-branch review                    PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                      DONE→final
(pe_t9, publish, refresh, refresh_review, chain: added by tools/milestone_chain.py install)
```

Caps `{max_cards: 100, bad_result_retries: 1, stall_minutes: 20, question_cards: 3}` (28 drafted steps). Results at
`pipelines/results/m-g-run/<step>-r${round}.json`; every step requires `verdict, summary, head, base`;
implementation, fix and branch cards also report `inflight_guard_exit` (the final review re-runs the guard). Inputs: handoff,
decisions, gap, `mc2plan` (`reviews/m-c2-plan.md`), `mfplan` (`reviews/m-f-plan.md`); the installer adds
`plan`. Protected edits: `pipelines/drafts/m-g-protected-edits.json`, two edits, `before_step: t9`.

## 8. Risks and residuals (stated, not hidden)

- **R1 Number heuristics.** The rule of 5.5 can miss (numbers as words, `\times 10^{…}`) and can match by
  coincidence (the same digits elsewhere in a file). It is deterministic and documented; it replaces a
  3–5-sentence hand spot-check, not a human reading.
- **R2 File-based integrity.** The ledger, `data.yml`, manifests and events are files a run's own agents
  can write; a coherent forgery is not caught (same class as M-B R2, M-D2 R2, M-F R2). Waivers count only
  from human-actor events, and `waive`/`ingest`/`adopt` refuse in a dispatch, but an events line can be
  forged. Host-side signing stays a follow-up.
- **R3 Letter splitting is heuristic.** Unusual headers ("Comments from the first referee") end up in the
  editor block; the paragraph-coverage rule still forces every paragraph to be answered or explicitly
  set aside, and the human sees the block table in `revision show`.
- **R4 latexdiff is absent here.** Only the mocked argv and the plain diff are tested on this host; the
  real-binary test runs where latexdiff is installed.
- **R5 RO-Crate is checked structurally**, offline, against the 1.1 required properties; no external
  validator runs.
- **R6 C004 is not mechanical** (M-G-D3).
- **R7 No verify-phase gate until M-G2.** Until the paper-lifecycle work lands and M-G2 runs, nothing
  stops a paper-draft run from recording its verify phase as done with claim errors; the skill asks for
  `claims check` (t9) and `manuscript package` refuses on errors (5.8), so nothing is packaged for
  submission with an error.
- **R8 Retraction freshness.** C005 knows only what the last evidence round or identity check found; a
  paper retracted later is not seen until a new round (no network in the check, by design).
- **R9 Shallow clones** can make `code-not-ancestor` fire for a run made from a commit the clone lacks;
  the user may waive it with a reason.

## 9. After M-G1

Queue (`continuity/milestones.yml`): M-G2 (`after: [m-g]`, line 25) waits for M-G1 **and** for the
paper-lifecycle landing (its note); M-H (`after: [m-c2, m-d1]`) and M-I (`after: [m-c1]`) do not depend
on M-G; F-2 waits for M-G (`after: [m-g, m-h, m-i, m-d3]`). Follow-ups surfaced while planning:
F1 W3C PROV-JSON-LD export next to the crate; F2 a PDF of the latexdiff output through `manuscript
build`; F3 PDF decision letters (text extraction); F4 web pages for the ledger and revision rounds (D7:
CLI complete); F5 C004 from claim-check verdicts (advisory); F6 cross-run `[run:]` references with an
explicit import; F7 host-side integrity for ledger, waivers and adopted letters (with M-F F4).

## 10. M-G2 handoff (not part of this run)

Kept from round 1 so that the M-G2 plan starts from that design (it was never reviewed: round 1 stopped
at BLOCKED); the M-G2 plan must re-verify all
of it against the paper-lifecycle code **as landed** (round 1 read an uncommitted diff).

### 10.1 What round 1 read in the in-flight diff (uncommitted, 2026-10-30)

`schemas/status.yml` gains `workflows: {paper-draft: [intake, outline, draft, cross-review, merge, verify,
handoff]}`; `status.phases_for(workflow)`, `require_loop`; `new_status(run, approval, workflow)` writes
`workflow:` and `slug:` for non-loop runs; `init.init_workspace` treats `overrides["workflow"] ==
"paper-draft"` specially and `adopt_workspace` accepts only `paper-draft`;
`checkpoint.checkpoint/resume_info` special-case `paper-draft`; `run init --workflow
[research-loop|paper-draft]`, `run adopt --workflow paper-draft`; `actions.mark_phase` emits
`phase.<state>` without an iteration for non-loop runs; `validate.validate_status` checks phases with
`phases_for`. (`paper-draft: [intake, …]` appears on main only in the older, lenient
`schemas/status-research.yml:6`, a different file.)

### 10.2 Round-1 design of the wiring (former spec 5.13)

- `schemas/status.yml` `workflows:` gains `paper-revision: [intake, structure, respond, revise, verify,
  resubmit]`.
- `core/run/status.py` gains `PAPER_WORKFLOWS = ("paper-draft", "paper-revision")`; every
  `== "paper-draft"` test in `init.py`, `checkpoint.py` and the `cli.py` choice lists becomes membership
  in it (`adopt_workspace` accepts both; `run init --workflow` and `run adopt --workflow` offer both;
  resume texts name the workflow).
- **Verify gate**: `actions.mark_phase(ws, "verify", "done", …)` on a run whose workflow is in
  `PAPER_WORKFLOWS` first runs `claims.compile.check_run` (M-G1 5.8, 5.13) on the run's manuscript; on
  any error, or any exception, it raises `ValueError("verify refused: <n> claim error(s); run scieflow
  manuscript claims check <slug>")` before the status is written (CLI exit 1), and emits
  `note.claims_checked` either way. The research-loop path and every other phase are unchanged. The
  import of the manuscript package is lazy (an `ImportError` → refused, fail closed).
- `docs/runs.md` documents both; the paper-draft skill's Phase 5 note (t9 of M-G1) then points at the gate.

### 10.3 Round-1 ticket t8 (for the M-G2 plan to adapt)

Files: `schemas/status.yml`, `src/scieflow/core/run/{status,init,cli,checkpoint,actions,validate}.py`
(only the lines 10.2 names), `docs/runs.md`, tests `tests/core/test_paper_revision_lifecycle.py`.
RED → GREEN: (1) `run init <slug> --workflow paper-revision …` creates status with the six phases; `run
adopt --workflow paper-revision`; `validate_status` accepts it; `run advance` refused (`require_loop`);
checkpoint/resume texts name `paper-revision`. (2) Verify gate: a paper-draft run whose manuscript has a
C002 → `run mark <slug> verify done` exit 1, status unchanged, one `note.claims_checked`; fixed
manuscript → done; `verify running` and every other phase unaffected; a research-loop run unaffected; an
`ImportError` (monkeypatched) → refused. (3) Every existing test of the landed lifecycle green unchanged.
Review checklist: only the 10.2 lines changed; no behaviour change for research-loop runs or paper-draft
phases other than verify; fail closed; `config/agents.yml` untouched. Focused set:
`tests/core/test_paper_lifecycle.py tests/core/test_paper_revision_lifecycle.py
tests/core/test_run_actions.py tests/core/test_create_run.py tests/test_status.py
tests/test_checkpoint.py tests/research/manuscript/claims`.
