# M-D2 plan: analysis — replicates, statistics, run manifests, tables and figures (round 2)

Pipeline `m-d2-plan`, step plan, round 2, epoch 1. Card `t_f5bc14c6`, Opus 5.5 @ high (runtime check
`model_handoff_runtime_read.py … claude-opus-5.5` exit 0, session `20261028_133024_6e2a0c`). Round 1
was card `t_d0714ace` (session `20261028_125403_40aed3`). Written 2026-10-28. Previous review:
`reviews/m-d2-plan-review-r1.md` (CHANGES, F1–F3, advisories A1–A3); every finding is closed in this
round — see section 0a. Size L: Part A is the spec, Part B the
implementation plan (repo rule: an L milestone gets a spec and a plan under `docs/superpowers/`).

This is a plan only. No code changed, no commit, no push, no merge, no live attempt, no network call
except the read-only `git ls-remote` that fixes the reference main.

## 0. Inputs and references used

Immutable inputs (sha256 re-computed on this card; all match the card):

| Input | sha256 |
|---|---|
| `handoffs/gap-eval-20261023/HANDOFF-next-after-G6.md` | f97be2ce…e68a |
| `handoffs/gap-eval-20261023/DECISIONS.md` | 3f643c1b…47da |
| `handoffs/gap-eval-20261023/GAP-ANALYSIS.md` | 833f1f25…cc8d |
| `continuity/roadmap-20261024.md` | 193452d5…2238 |
| `reviews/m-a-plan.md` (format example) | 0a2c6135…ae3f |
| `pipelines/ma-run.yml` (format example) | 1153c040…c85c |
| `continuity/milestones.yml` (not pinned) | de3bf17f…2f8b (m-d2 entry line 20: `after: [m-d1]`) |

Scope source: GAP-ANALYSIS.md section 8 row **M-D2** ("Replicates/seeds, `scieflow.experiments.stats`
→ `stats.json`, run `manifest.json` (git SHA + dirty, input/reference/artifact sha256, seed, package list),
tidy `results.csv`, figures/tables from `stats.json` with profile sizing, cross-campaign compare";
acceptance "Stats vs scipy reference; deterministic figure hashes; manifest fields present and hashed;
replicate budget semantics tested (Q8)"; depends on M-D1; conflicts `experiments/*`, not in-flight) and
**DECISIONS D21** ("One replicate = one experiment run; a remote array job of N tasks = N runs"). The core
roadmap's Milestone 4 (`docs/superpowers/specs/2026-09-22-core-roadmap.md:265-285` on M) is the
background the row condenses; where the two differ, the GAP row and DECISIONS win (section 1, M-D2-D12).

Prerequisite plan read (approved, not merged yet; this run starts only after it merged):

| File | sha256 | Used for |
|---|---|---|
| `reviews/m-d1-plan.md` | 8afadcae…c2a3 | the design/SAP API M-D2 binds to (its sections 5.1, 5.5, 5.6) |
| `reviews/m-d1-plan-review-r3.md` | cc2bb151…ce05 | PASS; clarification C1 (power NaN rule) does not touch M-D2 |

Planning artifacts written by this card (run-root files; the run pipeline pins them as inputs with their
sha256; implementers copy, nobody edits them):

| Artifact | sha256 | What | Used by |
|---|---|---|---|
| `reviews/m-d2-stats-reference.json` | 1724e82e…0a8b | 18 reference cases computed on this host (section 5.4) | t3 (copied to `tests/fixtures/stats/reference.json`) |
| `reviews/m-d2-stats-reference.py` | 43f72ddf…3dfd | the generator; asserts scipy == hand formula before writing | reviewers (reproduce), not copied |

Code read read-only from git objects (`git show <ref>:<path>`, `git grep <ref>`); no checkout was touched.
`M` = remote main = `6ab9bde94b68173159668860d87b118db053907b` (`git ls-remote
git@github.com:jedimik/ScieFlow.git refs/heads/main`, 2026-10-28; the M-C1 merge commit, PR #20,
`RUN/scratch/m-c1/publish.json` `main_after_merge`). Every `path:line` below is on `M` unless it is
marked "M-D1 plan §x" (code that exists only after M-D1 merges; t0 re-verifies it on the real base).
Probes run on this host (`/home/ubuntu/ScieFlow-claude-f2976f52/.venv`, python 3.12.3): scipy 1.18.1,
numpy 2.5.3, matplotlib 3.11.2 present; statsmodels, pandas, pyarrow, pingouin, lifelines absent.
Round 2 re-ran `git ls-remote`: main is still `6ab9bde94b68173159668860d87b118db053907b`; M-D1 is not
merged yet (`RUN/scratch/m-d1/` holds junit and logs only, no `publish.json`), so the plan is still
written ahead against main as it will be after M-D1.

## 0a. Round 2: review r1 findings and how each is closed

| Finding | Closed by | Where |
|---|---|---|
| **F1** `scieflow experiment metrics` (`cli.py:155-164` → `runner.recompute_metrics` `runner.py:114-123`) rewrites `metrics.json`, so `verify` would say `metrics-hash` and `results.json`/`stats.json` would silently disagree | Decided (Rule D): a recompute through the tool is a **recorded, coherent update**. `recompute_metrics` rewrites `metrics.json`, refreshes the run's `manifest.json` (`metrics_sha256` + an appended `metrics_history` entry), updates that run's entry in the campaign's `results.json` (`metrics`, `manifest_sha256`) and re-writes `results.csv`, all atomically. `stats.json` is then *stale* (its `results_sha256` no longer matches), so tables/figures/compare-campaigns refuse until `stats` is re-run, and the report shows "not computed". `verify` reports the recompute as the informational note `metrics-recomputed` (exit 0); a hand edit of `metrics.json` still fails with `metrics-hash` | 5.3 (new bullet "Recomputing metrics"), 5.4 Staleness, 5.9, 5.11 (docs), section 3 file table, t2 case 9, t3 case 6, R2 |
| **F2** budget pre-check with no `budget.yml` | `_guard_capacity` never refuses when the work is not inside a run, `budget.yml` is missing, or `max_experiment_runs` is absent, null or 0; a missing `spent.experiment_runs` counts as 0 | 5.2, t1 case 6 (two new sub-cases) |
| **F3** completed runs without a value for a metric | Such runs are left out of `n`/`values` and counted per configuration × metric as `n_missing` (no value: no reference, or the metric absent) or `n_nonfinite` (value is ±inf or NaN — e.g. PSNR of identical images is `inf`, `metrics/image.py:23-24`), and listed under `excluded`; `results.csv` keeps their rows (empty value, or `inf`/`-inf`/`nan`). A campaign without `reference` gives `n` 0 everywhere, CIs null, tests `n<2`, no crash | 5.4 (Runs), 5.6, 5.7, 5.8, t3 case 8, t5 case 3, t6 case 1 |
| A1 scipy floor (`bootstrap(..., rng=)` needs scipy ≥ 1.15) | Taken: `stats` refuses with "needs the experiments extra (scipy >= 1.15)" when scipy is older; tested by monkeypatching the version; R1 names it | 5.4 Refusals, t3 case 8, R1 |
| A2 `git_state` of an installed copy inside another repository's ignored `.venv` | Taken: `git_state(dir, probe)` returns a SHA only when `probe` (the package's `__init__.py`; the stage's `stage.yaml`) is tracked by that work tree (`git ls-files --error-unmatch`), else `null` with reason `not-tracked` | 5.3, t2 case 2 |
| A3 TOCTOU between pre-check and spend | Taken: one sentence in 5.2 (single local writer; the pre-check is advisory against concurrent sweeps; `record_spend` stays authoritative) | 5.2 |

No finding needed a human decision; none changes scope, exposure, the pipeline shape, the protected edit
or the stats reference artifacts (their sha256 are unchanged).

## 1. For the human: what M-D2 adds, in plain words

Today an experiment campaign runs each parameter setting once, ranks the runs by one metric and draws one
plot. There are no repeats, no statistics, no record of exactly which code and data produced a run, and
no tables or figures fit for a paper. M-D2 adds the analysis layer.

1. **Repeats (replicates) with seeds.** A campaign can say `replicates: 5`. Each setting then runs five
   times, each time with its own seed passed to the stage (`--seed`), and the run ids end in `_r0 … _r4`.
   Replicate *k* gets the same seed in every setting, so settings can be compared pair-wise on the same
   noise. A stage must declare that it accepts a seed; otherwise repeats would be identical and the
   campaign is refused. A campaign without `replicates` behaves exactly as today.
2. **Budget: one repeat = one run (your D21).** A campaign with 4 settings × 5 repeats spends 20
   experiment runs. New: a sweep that would need more runs than the budget has left is refused **before
   anything runs**, instead of overspending. (The exhausted-budget rule is unchanged.) The
   experiment-cycle skill also stops telling the coordinator to record campaign runs a second time — the
   sweep already records them, so today local campaigns would be counted twice.
3. **A manifest per run.** Every run gets `manifest.json`: ScieFlow's version and git commit (and whether
   the code had uncommitted changes), the pipeline's git commit, sha256 of the input, the reference and
   every output file, the seed, the parameters, the container image hash and the installed package list,
   the run's data level. The campaign's `results.json` records each manifest's own hash, and
   `scieflow experiment verify` re-checks every hash, so a changed output or manifest is caught.
   Recomputing a run's metrics with `scieflow experiment metrics` updates the manifest and
   `results.json` together and is recorded as a recompute (verify shows it as a note, not an error); the
   statistics then count as out of date until `stats` is run again.
4. **A tidy table of all results**: `results.csv`, one row per run and metric, ready for any statistics
   tool. (No parquet: it would need a new dependency.)
5. **Statistics in code: `scieflow experiment stats`.** Per setting and metric: n, mean, sd, median,
   min, max and a bootstrap 95% confidence interval. If the campaign names a baseline and a test (or the
   locked analysis plan from M-D1 does), each setting is compared with the baseline — Welch or Student t,
   Mann-Whitney, paired t or Wilcoxon on seed-paired repeats — or all settings at once (ANOVA,
   Kruskal-Wallis), or the metric is correlated with a numeric parameter (Pearson, Spearman). Effect
   sizes (Hedges' g, Cliff's delta, the mean difference, and d_z for paired tests) and corrected p-values
   (Holm, Hochberg, Benjamini-Hochberg or Bonferroni) are reported, with method, n and assumptions, in
   `stats.json`. Agents read this file; they never do the arithmetic themselves. Failed repeats, and
   repeats that produced no usable value for a metric (no reference image, or an infinite value), are
   counted and listed — never silently dropped.
6. **The locked analysis plan binds the statistics.** When the run's design is locked (M-D1), the test,
   the correction and alpha written in the plan are used for its outcomes. A campaign that asks for a
   different test is refused before it runs (for clinical study types; a computational design only
   warns). Exploratory outcomes, and metrics the plan does not name, are corrected separately (BH) and
   labelled exploratory.
7. **Tables and figures from `stats.json`.** `scieflow experiment tables` writes Markdown and LaTeX
   (booktabs) tables; `scieflow experiment figures` writes a multi-panel figure (mean with CI error bars
   and the individual repeats) as PNG/SVG/PDF, sized in millimetres for a single or double journal
   column. Same input → byte-identical files. Each table and figure gets an id and a small provenance
   file (which `stats.json` it came from, its own hash). The campaign report gains a Statistics section.
8. **Compare campaigns: `scieflow experiment compare-campaigns`** lists, for each shared metric, every
   campaign's best setting with its mean and CI. (No test across campaigns: their inputs differ.)

Everything is local: no network, no new dependency, and nothing leaves the run. Outputs of a run that
holds declared L2 data are L2-derived; they leave only through `scieflow data release` (M-B).

**Decisions taken under Rule D that you may veto at the approval gate:**

- **M-D2-D1 No new dependency.** Statistics use scipy and numpy, which the `experiments` extra already
  brings (`pyproject.toml:10-15`; scipy via scikit-image). statsmodels/pandas/pyarrow are not installed and
  adding them needs an online lock refresh and changes the virtual environment both lanes share. So:
  CSV only (no parquet); tests that need a regression model (ANCOVA, linear/logistic/mixed models, Cox,
  log-rank, ROC, chi-square/Fisher on counts, one-sample t) are recorded as "not computed — not supported
  in stats v1" when a plan names them, never approximated. M-D1's statsmodels cross-check of the power
  formulas stays skipped (M-D1 residual R1). (Alternative: add statsmodels now; say so.)
- **M-D2-D2 Replicates are opt-in and seed-based.** `replicates: N` (or `{n: N, seed: S}`) runs every
  configuration N times with seed `S + k` for replicate k (S defaults to 0); the stage must declare
  `accepts_seed: true` in `stage.yaml`; a `seed` parameter cannot be set at the same time. Same seed
  across configurations = common random numbers, which makes paired tests possible.
- **M-D2-D3 Budget pre-check without a checkpoint.** A sweep that needs more runs than `experiment_runs`
  has left is refused before any run (`job.refused`, `reason: budget`, `rule: insufficient`). Unlike the
  exhausted case it does **not** checkpoint the run: the budget is not spent, so the coordinator can
  propose a smaller campaign or ask you to raise the budget. (Alternative: checkpoint as for exhaustion.)
- **M-D2-D4 No test unless one is named.** Descriptives and CIs are always computed; a test runs only if
  the campaign's `analysis` block or the locked SAP names it. All tests are two-sided; alpha defaults to
  0.05; a rejection means `p_adjusted <= alpha`.
- **M-D2-D5 One correction family per `stats.json`.** All confirmatory tests of a campaign form one family
  (conservative across metrics). With a locked SAP, its exploratory outcomes and any metric it does not
  name form a second family, always corrected with BH (rr D14 / M-D1 SAP `exploratory.fdr: bh`).
- **M-D2-D6 The locked SAP wins.** For SAP outcomes the SAP's test, multiplicity method and alpha are
  used. A campaign `analysis` setting that contradicts them is refused at sweep/run time and by `stats`
  for non-computational designs, and only warned (SAP still used) for `computational` designs — the same
  split M-D1 uses (M-D1 plan §5.6). Hierarchical and gatekeeping corrections are not implemented: they are
  reported as "not applied", with no reject decision, never silently replaced.
- **M-D2-D7 Package lists are captured at run time, inside the run.** For container runs ScieFlow asks
  the exact `.sif` once per sweep (`apptainer exec <sif> python -m pip list --format=freeze`) and stores
  the list in the campaign folder; direct runs list the host interpreter's packages. `envbuild.py` is not
  changed (its test pins the last subprocess call, `tests/experiments/test_envbuild.py:41-49`). A failed
  capture is recorded as "not captured" and never fails the run. The host name is not recorded (only
  OS, machine type and Python version): manifests travel with archives.
- **M-D2-D8 Figure sizing contract instead of a journal profile.** Journal profiles do not exist until
  M-F. M-D2 defines a small `figures:` block (column widths in mm, font size, dpi, formats; defaults
  89/183 mm, 8 pt, 300 dpi, PNG+SVG) read from `--style-file`; M-F's journal profile will carry the same
  block. Nothing in M-D2 reads `config/journals/`.
- **M-D2-D9 No new event types.** The M-C2 plan edits the closed event list (`src/scieflow/core/events.py:20-42`).
  M-D2 records with `job.refused` (existing, `:25`) and the free-form `note.experiment_analysis`
  (`:51` accepts `note.*`) — counts, names and hashes only, never values.
- **M-D2-D10 Budget semantics documented outside `docs/runs.md`.** GAP Q8 says "document in
  `docs/runs.md`", but that file is in-flight (section 3). M-D2 documents it in the new
  `docs/experiments/analysis.md` and the experiment-cycle skill; a pointer in `docs/runs.md` is follow-up
  F1 once the paper-lifecycle work lands.
- **M-D2-D11 One protected edit.** `src/scieflow/experiments/AGENTS.md` rule 3 names the new generated
  files and a new rule 6 says statistics come from `scieflow experiment stats` (section 3). Root `AGENTS.md`
  is not changed: its rule 5 ("an exhausted `experiment_runs` refuses the next sweep", `AGENTS.md:61-64`)
  stays true.
- **M-D2-D12 Out of scope (core M4 items not in the GAP row):** the dataset package builder
  (`scieflow package build`), Pareto-front figures, parquet, a project-level metric tracker across
  iterations, and remote/array campaigns (M-D3). Listed as follow-ups in section 9.

**Proposed PR (the operator's publish step pushes and merges after the final review PASS):** head
`dev/m-d2` → base `main`, one PR, merged with a merge commit. Title "M-D2 Analysis: replicates, stats,
manifests, tables and figures". The body (written by the branch step) lists the eight capabilities,
decisions M-D2-D1..D12, absolute test counts and the residuals.

## 2. Base, dependency and worktree

- **Dependency M-D1** (`continuity/milestones.yml:20` `after: [m-d1]`): approved (`m-d1-plan` state
  `done`, install), its run `m-d1-run` is at t0 now. M-D2's code depends on M-D1's `scieflow.core.design`
  package (`lock.state`, `model.load`, `campaign_verdict`, M-D1 plan §5.2, §5.5) and on M-D1's
  `_design_guard` in `experiments/cli.py` (M-D1 plan §5.6). The installer holds this run until M-D1 is
  merged (`tools/milestone_chain.py` lanes); t0 checks it again.
- **Lanes**: `parallel: pairs` with pairs `[m-e, m-b]`, `[m-c1, m-d1]` only
  (`continuity/milestones.yml:13-14`), so no other milestone runs beside M-D2. If M-C2 merged before M-D2
  starts, base includes it; its files do not overlap M-D2's (M-C2 plan file table: research, vendor,
  news, `events.py`, `docs/cli.md`; M-D2 edits none of these).
- **Worktree**: t0 runs `git ls-remote git@github.com:jedimik/ScieFlow.git refs/heads/main` → `base`,
  the object-only fetch `git -C /home/ubuntu/ScieFlow-claude-f2976f52 fetch --no-tags
  --no-write-fetch-head git@github.com:jedimik/ScieFlow.git refs/heads/main`, then requires
  `git merge-base --is-ancestor` of: H-4 `6152d733728b9ed5bf5b571aef475f8071426386`, M-A
  `be99db2ace0e560fcdb7c00231e906e4aaae67e2`, M-E `9eeb4fad8c01d7c008acd155e81dac3976d62aa7`, M-B
  `05d4d1087bb9c3b5b1bcc7fc8df089787f40a7ab`, M-C1 `aa7c3792b0b374ccfa6868bb13018a1974ac3435` (its
  pushed head, `RUN/scratch/m-c1/publish.json`) and **M-D1**: `RUN/scratch/m-d1/publish.json` must exist
  with `merged: true`, and its `pushed_head` must be an ancestor of `base`. Any failure → verdict
  `BASE_RED` with the reason (nothing else is done). Then
  `git -C /home/ubuntu/ScieFlow-claude-f2976f52 worktree add -b dev/m-d2 RUN/scratch/m-d2-wt <base>`.
- Commits are local on `dev/m-d2`; no rebase, squash, amend or force; review fixes are new commits.
- RUN = `/home/ubuntu/ScieFlow-claude-f2976f52/workspace/cloud-dev-tickets-20261008`; scratch output in
  `RUN/scratch/m-d2/` (junit, logs, `executions.jsonl`, `pr-body.md`); pytest temp dirs under
  `~/.cache/scieflow-m-d2-<step>/`.

## 3. In-flight, protected and changed files

**INFLIGHT** (the uncommitted paper-lifecycle work in `/home/ubuntu/Github/ScieFlow`; the same 9 paths as
M-A section 3 and M-D1 section 3). M-D2 edits **none** of them:

```
config/agents.yml
docs/runs.md
schemas/status.yml
src/scieflow/core/run/actions.py
src/scieflow/core/run/checkpoint.py
src/scieflow/core/run/cli.py
src/scieflow/core/run/init.py
src/scieflow/core/run/status.py
src/scieflow/core/run/validate.py
```

Guard (must exit 0): `git -C WT diff --exit-code <base> -- <INFLIGHT>` (committed range) and, on a dirty
tree during a card, `git -C WT diff --exit-code -- <INFLIGHT>`. M-D2 *calls* `actions.run_for_path`
(`src/scieflow/core/run/actions.py:85-91`), `actions.guard_budget` (`:65-73`), `actions.record_spend`
(`:48-54`) and `status.read_status` (read only); it changes none of them.

**Never edited**: `config/sandbox.yml`, the DD1-sealed files (`tests/probes/k_task8_binding.py`,
`tests/probes/test_k_task8_binding.py`, `tests/fixtures/k_task8_stream_fixture.py`), `CLAUDE.md`, root
`AGENTS.md`, every other `AGENTS.md` except through the protected edit below, `CREDITS.md`, `VENDORED.md`,
`src/scieflow/core/events.py`, `src/scieflow/core/design/*` (M-D1's package: called, not changed),
`src/scieflow/experiments/envbuild.py`, `pyproject.toml`, `uv.lock`.

**Protected edit (operator-applied before t6, `pipelines/drafts/m-d2-protected-edits.json`)** —
`src/scieflow/experiments/AGENTS.md`, rules 3–5 (`src/scieflow/experiments/AGENTS.md:18-23` on M; no
M-D1 or M-C2 change touches this file). Exact text:

old:
```
3. **Never bypass `scieflow experiment` for run bookkeeping.** Do not
   hand-write run directories, metrics.json, or results.json.
4. **Report honestly.** Failed runs stay in the report. Do not delete or
   rerun-until-green.
5. **Run outputs live in the run's workspace.** Always pass
   `--experiments-dir workspace/<slug>/experiments` (the command requires it).
```
new:
```
3. **Never bypass `scieflow experiment` for run bookkeeping.** Do not
   hand-write run directories, metrics.json, results.json, manifest.json,
   results.csv, stats.json, or the generated tables and figures.
4. **Report honestly.** Failed runs stay in the report. Do not delete or
   rerun-until-green.
5. **Run outputs live in the run's workspace.** Always pass
   `--experiments-dir workspace/<slug>/experiments` (the command requires it).
6. **Statistics come from code.** Means, confidence intervals, tests,
   effect sizes and corrected p-values come from `scieflow experiment
   stats` (`stats.json`); quote them, never compute or re-round them
   yourself. Every replicate is one run of the `experiment_runs` budget.
```
The old text occurs exactly once on M (the tool refuses otherwise; t0 re-checks it on base). t6 commits
the applied file in its own commit ("docs(agents): experiments rules 3 and 6 (human-approved protected
edit) (M-D2 t6)").

**Complete file list** (nothing else changes; existing tests only gain lines — no existing assertion is
changed or removed):

| File | Change | Ticket |
|---|---|---|
| `docs/superpowers/plans/2026-10-28-m-d2-analysis.md`, `docs/superpowers/specs/2026-10-28-m-d2-analysis-design.md` | copies of this plan / its Part A | t0 |
| `src/scieflow/experiments/campaign.py` | t1: `replicates` key, `Replicates`, `RunSpec.config_id/replicate/seed`, `expand`, `to_dict`; t3: `analysis` key and its validation | t1, t3 |
| `src/scieflow/experiments/stage.py` | `accepts_seed` field (`stage.yaml` key, default false) | t1 |
| `src/scieflow/experiments/runner.py` | t1: `--seed` in `build_command`, seed/replicate in `config.yaml`; t2: `manifest.json`, `recompute_metrics` refreshes manifest and campaign records (5.3) | t1, t2 |
| `src/scieflow/experiments/sweep.py` | t1: replicate fields in `results.json`; t2: manifest context, `manifest_sha256`, `results.csv`, atomic `update_run_entry` for a recompute | t1, t2 |
| `src/scieflow/experiments/compare.py` | `configs` / `best_config` aggregation for replicate campaigns | t1 |
| `src/scieflow/experiments/cli.py` | t1 `run --seed`, replicate/stage check, budget pre-check; t2 `verify`, `metrics` recompute note; t3 `stats`; t4 `_analysis_guard`; t5 `tables`, `figures`; t6 `compare-campaigns` | t1–t6 |
| `src/scieflow/experiments/manifest.py` | new: hashing, git state, package capture, manifest build/write, `verify_campaign` | t2 |
| `src/scieflow/experiments/tidy.py` | new: `results.csv` writer | t2 |
| `src/scieflow/experiments/stats.py` | new: pure statistics (describe, bootstrap CI, tests, effect sizes, corrections) | t3 |
| `src/scieflow/experiments/analysis.py` | new: campaign → `stats.json` (t3), SAP-aware families and labels (t4) | t3, t4 |
| `src/scieflow/experiments/sap.py` | new: binding to the M-D1 design, conflict check | t4 |
| `src/scieflow/experiments/style.py`, `tables.py`, `figures.py`, `schemas/figure-style.yml` | new | t5 |
| `src/scieflow/experiments/report.py` | Statistics section when a fresh `stats.json` exists | t5 |
| `src/scieflow/experiments/crosscompare.py` | new: `compare-campaigns` | t6 |
| `tests/fixtures/stats/reference.json` | byte copy of `reviews/m-d2-stats-reference.json` | t3 |
| `tests/experiments/test_replicates.py`, `test_budget_replicates.py`, `test_compare_configs.py` | new | t1 |
| `tests/experiments/test_manifest.py`, `test_verify.py`, `test_results_csv.py`, `test_recompute_records.py` | new | t2 |
| `tests/experiments/test_stats_reference.py`, `test_stats_guards.py`, `test_analysis.py`, `test_stats_cli.py` | new | t3 |
| `tests/experiments/test_sap_binding.py`, `test_analysis_guard.py` | new | t4 |
| `tests/experiments/test_style.py`, `test_tables.py`, `test_figures.py`, `test_report_stats.py` | new | t5 |
| `tests/experiments/test_crosscompare.py`, `tests/test_docs_analysis.py` | new | t6 |
| `docs/experiments/analysis.md` (new), `docs/experiments/cli.md`, `docs/experiments/concepts.md`, `mkdocs.yml` (one nav line) | docs | t6 |
| `skills/experiment-cycle/SKILL.md`, `src/scieflow/experiments/skills/{evaluator,experiment-designer,experiment-runner}/SKILL.md` | analysis and budget sections | t6 |
| `src/scieflow/experiments/AGENTS.md` | rules 3 and 6, operator-applied, committed by t6 | t6 |

## 4. Current code, verified (on M = 6ab9bde)

**Campaigns.** `KNOWN_KEYS` `src/scieflow/experiments/campaign.py:16-20` (M-A-D5 unknown-key refusal
`:28-40`); `RunSpec(run_id, params)` `:43-46`; `Campaign` fields `:49-62`, `load` `:64-94` (`rank_by`
defaults to the first metric `:91`); `expand` `:96-123` builds run ids `"{i:03d}_{slug}"` (grid, `:121-122`)
or `"{i:03d}_{name}"` (scenarios, `:101-102`); `to_dict` `:125-138` is what `sweep` copies into
`<campaign>/campaign.yaml`. No `replicates`, `seed` or `analysis` concept anywhere
(`git grep -n -i "replicate\|seed" 6ab9bde -- src/scieflow/experiments` finds none).

**Stages and runs.** `Stage` `src/scieflow/experiments/stage.py:21-62` (keys name, description, script,
output_file, environment; no unknown-key check, so a new optional key is backward compatible).
`build_command` `src/scieflow/experiments/runner.py:21-47` appends `--<key> <value>` for every param,
sorted (`:32-33`). `execute_run` `:65-111` writes `config.yaml` (`:80-92`, `:100-101`), logs,
`environment.json` from `_environment_info` (`:50-62`: mode, command, recorded, python | sif + sif_sha256 +
apptainer version) and `metrics.json` (`:107-110`, only when the run completed **and** the campaign has a
`reference`). No input/reference/artifact hash, no git SHA, no seed, no package list.
`recompute_metrics` `:114-123` re-reads `config.yaml`, refuses a run without a reference (`:117-118`)
and rewrites `runs/<id>/metrics.json` (`:122`) — it does not touch `results.json`; the CLI command
`scieflow experiment metrics <run_dir>` (`src/scieflow/experiments/cli.py:155-164`) calls it.
`tests/experiments/test_runner.py:51-60` (recompute after deleting `metrics.json`, no sweep) and
`tests/experiments/test_cli.py:83-86` (`metrics` on a swept run) pin it. Metric values can be
non-finite: `psnr` (`src/scieflow/experiments/metrics/image.py:23-24`) of identical images is `inf`,
which `json.dumps` writes as `Infinity`.

**Sweep and results.** `run_sweep` `src/scieflow/experiments/sweep.py:13-57`: writes `campaign.yaml`
(`:21-26`), runs every spec, writes `results.json` `{campaign, rank_by, mode, runs: [{run_id, params,
mode, status, metrics}]}` (`:32-56`). Non-atomic writes; no CSV.

**Compare and report.** `compare_campaign` `src/scieflow/experiments/compare.py:40-102` ranks runs by
`rank_by`, excludes `--direct` runs unless `include_direct` (M-A A-6, `:68-73`), writes `comparison.json`
with `best` = a run id (`:86-101`). `generate_report` `src/scieflow/experiments/report.py:67-142` writes
`report.md` (results table `:93-117`, one QC plot `:119-121`, preserves a trailing `## Evaluation` section
`:129-141`). Metric direction: `Metric.higher_is_better` `src/scieflow/experiments/metrics/__init__.py:9-26`.

**Experiments CLI.** `_budget_run` `src/scieflow/experiments/cli.py:58-62` (owning run via
`actions.run_for_path`), `_guard` `:65-71` (exhausted only); `run` `:79-104` records 1 run (`:100-101`);
`sweep` `:107-130` records `len(campaign.expand())` runs **after** the sweep (`:128-129`). After M-D1:
`power` command and `_design_guard(budget_run, campaign)` after `_guard` in `run` and `sweep` (M-D1 plan
§5.6), refusing with `job.refused` `reason: design`.

**Budget.** `budget.is_exhausted` = cap set and `spent >= cap` (`src/scieflow/core/run/budget.py:48-50`;
cap 0 = unlimited); `actions.guard_budget` refuses only an exhausted dimension, checkpoints once and emits
`job.refused` `reason: budget` (`src/scieflow/core/run/actions.py:57-73`). So today a sweep of 20 runs with
2 runs left runs all 20 and overspends by 18. Root `AGENTS.md:61-64` ("exhausted `experiment_runs`
refuses the next sweep … each refusal checkpoints the run"). `docs/cli.md:93-96`: "`scieflow experiment
run`/`sweep` runs are recorded automatically already". But `skills/experiment-cycle/SKILL.md:73-76`
tells the coordinator, after a local campaign, to "extract `K` = number of runs and record it in the
budget (`experiment_runs: +K`)" — a double count for local campaigns (the sweep inside a run already
recorded K). M-D2 fixes the skill text (t6).

**Events and data levels.** Closed `TYPES` `src/scieflow/core/events.py:20-42`, `note.<name>` accepted
`:51`, `job.refused` in the list (`:25`). `levels.run_level(ws)` `src/scieflow/core/data/levels.py:178`
(M-B; `ORDER` `:20`).

**Dependencies.** `experiments` extra = numpy, scikit-image, imageio, matplotlib (`pyproject.toml:10-15`);
scipy only transitively (M-D1 plan §4 cites the `uv.lock` scikit-image block); no statsmodels/pandas
(probe above). pytest default addopts `-m 'not slow and not live and not scite_live'`
(`pyproject.toml:75`).

**Tests that pin experiments behaviour** (M-D2 keeps every assertion): `tests/experiments/conftest.py:49-85`
(`project` fixture: stage `scale` with `--gain` only, campaign `gain-sweep`, metrics `[ssim, mse]`);
`tests/experiments/test_budget_counting.py:25-46` (spend == `len(expand())` == 2; exhausted refused; no
`experiments/` dir after refusal); `tests/experiments/test_envbuild.py:41-49` (last `subprocess.run` call
is `apptainer build`); `tests/experiments/test_runner.py:19-35`; `tests/experiments/test_sweep.py:10-26`.
No test pins `src/scieflow/experiments/AGENTS.md` or `docs/experiments/*.md` text
(`git grep` for both paths in `tests/` on M: no hit).

**Docs and skills.** `docs/experiments/cli.md:1-75` (sections per command), `docs/experiments/concepts.md:43-59`
(run directory tree), `mkdocs.yml:64-73` (Experiments nav), `docs/cli.md:385-386` (pointer to the
experiments CLI page). Skills: `skills/experiment-cycle/SKILL.md:12-24,68-76`,
`src/scieflow/experiments/skills/evaluator/SKILL.md:8-27`,
`src/scieflow/experiments/skills/experiment-designer/SKILL.md:20-55`,
`src/scieflow/experiments/skills/experiment-runner/SKILL.md:12-38`. The menu's experiments items
(`src/scieflow/core/menu.py:97-100`) are not changed (follow-up F5).

**M-D1 surface M-D2 binds to (after M-D1 merges; M-D1 plan sections, t0 re-verifies names):**
`scieflow.core.design.model.load(ws) -> Design | Absent | Invalid` (§5.2); `lock.state(project, ws) ->
DesignState` with `status` ∈ {none, invalid, draft, pending, locked, modified, ledger-invalid},
`version`, `in_force_hash`, `study_type`, `sap_metrics` (§5.5); SAP outcomes
`{id, variable, priority, test, metric?}` and `multiplicity: {method, alpha}`, `exploratory: {split,
fdr: bh}` (§5.1); the tests enumeration of `schemas/design.yml` (§5.1); the project for a run =
`Project(run_dir.parent.parent)` (§5.6); `_design_guard` in `experiments/cli.py` run/sweep (§5.6).

---

## Part A — Spec

### 5.1 Replicates and seeds

**Campaign key** (added to `KNOWN_KEYS`):

```yaml
replicates: 5                 # short form: n = 5, seed = 0
replicates: {n: 5, seed: 1000}
```

`Replicates(n: int, seed: int)`; `n` an int 1..1000, `seed` an int 0..2**31-1 with `seed + n - 1 <=
2**31-1`; bool is not an int here. Any other shape or key → `CampaignError` naming `replicates` and the
rule. A campaign with `replicates` and a `seed` entry in `parameters` (fixed or grid) or in any scenario
→ `CampaignError("'seed' is set by replicates; remove it from parameters/scenarios")`.

**Stage key**: `accepts_seed: true|false` in `stage.yaml` (default false; a non-bool → `StageError`).
`Stage.accepts_seed`. `replicates` with a stage whose `accepts_seed` is false is refused before any run
or spend (`check_replicates(campaign, stage)` raises `CampaignError`: "stage <name> does not declare
accepts_seed: true; replicates of a deterministic stage would be identical"; the CLI turns it into a
`ClickException`, exit 1, and emits no event).

**Expansion.** `RunSpec` gains `config_id: str` (default = `run_id`), `replicate: int | None = None`,
`seed: int | None = None` (keyword defaults keep every existing `RunSpec(run_id=…, params=…)` call working).
Without `replicates`, `expand()` is unchanged (same ids, same order, `config_id == run_id`). With it, for
each configuration in today's order (its id `cid` as today) and each `k` in `0..n-1`:
`RunSpec(run_id=f"{cid}_r{k}", params=<same>, config_id=cid, replicate=k, seed=seed + k)` — configurations
outer, replicates inner.

**Command.** `build_command` appends `--seed <int>` after the sorted params when `spec.seed is not None`.
`config.yaml` gains `config_id`, `replicate`, `seed` only when `spec.replicate is not None` (otherwise the
file is unchanged). `results.json` run entries gain `config_id`, `replicate`, `seed` (null when not a
replicate run) and the top level gains `replicates: {n, seed}` only for replicate campaigns.
`campaign.yaml` (`to_dict`) carries `replicates` (normalised `{n, seed}`) and `analysis` only when set, so
existing campaigns produce the same `campaign.yaml` as today.

**Single run.** `scieflow experiment run … [--seed INT]`: allowed only when the stage accepts a seed
(else exit 1 with the rule); the seed is passed and recorded (`replicate: null`, `config_id: manual_<slug>`).

### 5.2 Budget semantics (D21, GAP Q8)

- One executed run (any replicate, failed or completed) = one `experiment_runs` unit. `sweep` keeps
  recording `len(campaign.expand())` after the sweep (`cli.py:128-129`), which now equals
  configurations × replicates; `run` records 1.
- **Pre-check** `_guard_capacity(budget_run, planned)` in `sweep` (planned = `len(expand())`) and `run`
  (planned = 1), as the last guard before any run (order in 5.9). **No refusal** (returns silently, no
  event) when any of these holds: `budget_run` is `None` (not inside a run); `budget.yml` is missing
  (`budget.read_budget` → `store.read_yaml` returns `None`, `src/scieflow/core/store.py:70-74` — the same
  "no file, no budget" rule as `actions.guard_budget`/`record_spend`, `actions.py:48-73`); the `budgets`
  mapping or its `max_experiment_runs` is absent, null or 0 (0 = unlimited, `budget.py:48-50`). A missing
  `spent.experiment_runs` counts as 0. (A malformed `budget.yml` — including one whose `budgets` lacks the
  `max_experiment_runs` key, on which `budget.is_exhausted` raises `KeyError`, `budget.py:49` — is met
  first by the existing `_guard`, whose behaviour M-D2 does not change; `_guard_capacity` itself treats
  that case as "no cap".) Otherwise, if `spent + planned > cap` →
  emit `job.refused` (`actor: system`, `reason: budget`, `rule: insufficient`, `dims: [experiment_runs]`,
  `planned`, `remaining`) and raise `ClickException("experiment_runs budget insufficient: this campaign
  needs <planned> runs, <remaining> remain — propose a smaller campaign or ask the user to raise the
  budget")`. No checkpoint (M-D2-D3). No run directory, no spend. Uses only `budget.read_budget`,
  `events.emit` (public); `actions.py` is not changed.
- **Concurrency (stated):** a run has a single local writer; the pre-check and the later `record_spend`
  are not one transaction, so two sweeps started at the same moment in the same run could both pass the
  pre-check. The pre-check is advisory against that case; `record_spend` (under the `store.update_yaml`
  lock) stays the authoritative ledger, and the existing exhausted-budget guard refuses the next sweep.
- A remote array job of N tasks = N runs (D21) is M-D3's; M-D2 documents it only.
- The experiment-cycle skill "Afterwards" step is corrected (t6): local campaigns recorded through
  `scieflow experiment run|sweep` inside the run are already counted — do not record them again; only
  work the runner cannot see (remote jobs driven by the coordinator, one unit per real job/array task)
  goes through `scieflow run spend`.

### 5.3 Run manifest (`manifest.json`), packages and `verify`

`src/scieflow/experiments/manifest.py` (stdlib only):

- `file_digest(path) -> {"kind": "file", "sha256", "size"}`; `tree_digest(dir) -> {"kind": "dir",
  "sha256", "files"}` where sha256 is over the sorted lines `"<posix relpath>\0<file sha256>\n"` of every
  regular file below (symlinks are recorded as `"<relpath>\0symlink\n"`, never followed); a missing path →
  `{"kind": "missing"}`. Read in 1 MiB chunks.
- `git_state(path, probe) -> {"sha", "dirty"} | None` (+ `reason`): `git -C <path> rev-parse HEAD`,
  `git -C <path> ls-files --error-unmatch -- <probe>` and
  `git -C <path> status --porcelain --untracked-files=no -- <path>`, fixed argv, no shell, `timeout=10`,
  environment `GIT_OPTIONAL_LOCKS=0`; not a work tree, git missing or timeout → `None` with the reason;
  `probe` not tracked by that work tree → `None` with reason `not-tracked` (an installed copy of
  ScieFlow inside another repository's ignored `.venv`, or an untracked pipeline folder, must not report
  that repository's HEAD). The probe is the package's `__init__.py` for `code.scieflow` and the stage's
  `stage.yaml` for `code.pipeline`.
  `dirty` = tracked changes under `path` only (untracked data, `.sif` files and run outputs do not count).
- `packages(mode, sif) -> {"status": "captured"|"not-captured", "source", "sha256", "count", "list"} `:
  direct → `importlib.metadata.distributions()` of the running interpreter as sorted unique
  `"name==version"` (normalised lower-case names); apptainer → `apptainer exec <sif> python -m pip list
  --format=freeze` (fixed argv, `timeout=120`), output capped at 1 MiB / 20,000 lines, lines that do not
  look like `name==version` dropped; non-zero exit, timeout or missing binary → `not-captured` with the
  reason (never an exception).
- **Context, computed once per sweep** (and once per `run`): `{"code": {"scieflow": {"version":
  scieflow.__version__, "git": git_state(<dir of the scieflow package>, <its __init__.py>)}, "pipeline":
  {"path", "git": git_state(pipeline_dir, <stage.yaml>)}}, "inputs": {"input": digest(campaign.input), "reference":
  digest(campaign.reference) | null}, "stage": {"name", "script_sha256", "stage_yaml_sha256"},
  "packages": packages(mode, sif), "environment": {"mode", "python": "<major.minor.micro>", "platform":
  {"system", "machine"}, "sif_sha256" | null, "apptainer_version" | null}, "data_level":
  levels.run_level(run_dir) | null}`. For apptainer sweeps the package list is also written once to
  `<campaign_dir>/packages/<sif sha256[:12]>.txt` (inside the run; the manifest stores its relative path
  and sha256 instead of the full list). Host name, user name and environment variables are not recorded.
- `execute_run(…, context=None)` writes `runs/<id>/manifest.json` after `metrics.json` (failed runs too):
  `{"version": 1, "run_id", "campaign", "config_id", "replicate", "seed", "params", "mode", "status",
  "returncode", "started", "finished", "wall_seconds", "code", "stage", "inputs", "environment",
  "packages", "data_level", "artifacts": [{"path", "sha256", "size"}…] (every regular file under
  `artifacts/`, sorted, symlinks listed with `"symlink": true` and no hash), "metrics_sha256": <sha256 of
  metrics.json bytes> | null, "metrics_history": []}`. Written as canonical JSON (`sort_keys=True, indent=1`, trailing newline)
  through a temp file and `os.replace`. When `context` is `None` (old callers, tests) it is computed
  for that one run.
- `results.json` run entries gain `manifest_sha256` = sha256 of the manifest file bytes.
- **Recomputing metrics (`scieflow experiment metrics <run_dir>`, `runner.recompute_metrics`).** A
  recompute through the tool is a recorded, coherent update of every record that holds the metrics; it
  is never left for `verify` to flag as tampering and never leaves `results.json` behind. In this order,
  each file through a temp file + `os.replace`:
  1. `metrics.json` is rewritten (today's behaviour, `runner.py:122`; the no-reference refusal
     `:117-118` is unchanged).
  2. If `runs/<id>/manifest.json` exists: `metrics_sha256` := sha256 of the new `metrics.json` bytes and
     one entry is appended to `metrics_history`: `{"previous_sha256": <old metrics_sha256 | null>,
     "sha256": <new>, "recomputed": "<UTC ISO-8601 seconds>", "tool": "scieflow experiment metrics"}`.
     Nothing else in the manifest changes. A run without a manifest (recorded before M-D2) gets none —
     a manifest written at recompute time would claim code/input state the run did not have.
  3. If the campaign folder (`run_dir.parent.parent`) has a `results.json` listing this `run_id`
     (`sweep.update_run_entry(campaign_dir, run_id, metrics=…, manifest_sha256=…)`): that entry's
     `metrics` := the new metrics and, when a manifest exists, its `manifest_sha256` := the new manifest's
     sha256; every other field of that entry, every other run and the top-level keys are kept; then `results.csv` is re-written
     from the new `results.json` (5.6). A run that is not listed (a single `scieflow experiment run`, or
     no `results.json`) changes only its own files.
  4. When the run folder lies inside a run workspace, `note.experiment_analysis` (`kind:
     metrics-recompute`, `campaign`, `run_id`, `sha256` of the new `metrics.json`) — no values.
  Consequences, stated: any `stats.json` of that campaign becomes *stale* (its `source.results_sha256` no
  longer matches, 5.4), so `tables`, `figures` and `compare-campaigns` refuse it and the report shows
  "Statistics: not computed" until `scieflow experiment stats` is run again; `comparison.json` and
  `report.md` are not rewritten (they are re-generated by `compare`/`report` as today).
- `verify_campaign(campaign_dir) -> Report(ok: bool, problems: [(rule, run_id, path)], notes: [(rule,
  run_id, path)])`: for each run in
  `results.json`: `manifest-missing`, `manifest-hash` (≠ `manifest_sha256`), `manifest-invalid`
  (unparsable or missing keys), `artifact-hash` / `artifact-missing` / `artifact-extra` (a file under
  `artifacts/` not in the manifest), `metrics-hash` (the `metrics.json` bytes ≠ the manifest's
  `metrics_sha256` — a change that did not go through the tool). Notes, which never make `ok` false:
  runs recorded before M-D2 (no `manifest_sha256`) once as `legacy-run`; a run whose manifest has a
  non-empty `metrics_history` (and whose hashes all match) as `metrics-recomputed`. CLI `scieflow
  experiment verify <campaign_dir> [--json]`: exit 0 when no problem (notes allowed), 1 otherwise;
  messages name rule, run id and relative path, never file contents.

### 5.4 Statistics (`stats.py`, `analysis.py`, `scieflow experiment stats`)

**Campaign key `analysis`** (added to `KNOWN_KEYS`, validated in `Campaign.load`):

```yaml
analysis:
  baseline: {sigma: 1.0}          # a params mapping equal to exactly one configuration, or a configuration id
  test: welch-t-test              # one test for every metric, or {<metric>: <test>, ...}
  correction: holm                # none | bonferroni | holm | hochberg | bh      (default holm)
  alpha: 0.05                     # 0 < alpha < 1                                  (default 0.05)
  ci: 0.95                        # 0.5 <= ci < 1                                   (default 0.95)
  bootstrap: {resamples: 9999, seed: 0}   # resamples 1000..100000                 (defaults shown)
```
Unknown keys, a test name outside the M-D1 `tests` enumeration (`schemas/design.yml`; constant
`TESTS` in `analysis.py`, pinned equal to the schema by a test), a metric key not in `metrics`, or a
baseline matching zero or several configurations → `CampaignError` naming the key path.

**Kinds** (every name is from the M-D1 tests enumeration):

| Test | Kind | scipy call (exact; implementation must match) | Needs |
|---|---|---|---|
| `welch-t-test` | pairwise | `ttest_ind(config, baseline, equal_var=False)` | n ≥ 2 each, not both constant |
| `t-test-ind` | pairwise | `ttest_ind(config, baseline, equal_var=True)` | as above |
| `mann-whitney` | pairwise | `mannwhitneyu(config, baseline, use_continuity=True, alternative="two-sided", method="auto")` | n ≥ 2 each, not all values equal |
| `t-test-paired` | paired | `ttest_rel(config_pairs, baseline_pairs)` | replicate campaign, ≥ 2 shared seeds, differences not constant |
| `wilcoxon-signed-rank` | paired | `wilcoxon(config_pairs, baseline_pairs, zero_method="wilcox", correction=False, alternative="two-sided", method="auto")` | ≥ 1 shared seed with a non-zero difference |
| `anova-oneway` | omnibus | `f_oneway(*groups)` (configurations in campaign order) | ≥ 2 configurations with n ≥ 2, not all constant |
| `kruskal-wallis` | omnibus | `kruskal(*groups)` | ≥ 2 configurations with n ≥ 1, not all values equal |
| `pearson` | correlation | `pearsonr(x, y)` over every usable value (all configurations) | exactly one varying parameter, all its values numbers (not bool), n ≥ 3, x and y not constant |
| `spearman` | correlation | `spearmanr(x, y)` | as pearson |
| `descriptive` | none | — | — |
| every other enumeration value | unsupported | — (`not-computed`, reason `test-unsupported`) | — (M-D2-D1) |

Pairing: by `seed` — pairs are the seeds that have a usable value (below) in both the configuration and the
baseline; unpaired runs are left out and counted (`n_unpaired`). Runs: completed runs only, `--direct`
runs excluded unless `--include-direct` (as `compare`, M-A A-6); failed runs counted per configuration
(`n_failed`), never dropped silently.

**Usable values (closes review F3).** For each configuration × metric, a completed, included run
contributes a value only when its `results.json` `metrics` has the metric **and** the value is a finite
number (not bool). Otherwise the run is excluded from `n`, `values`, the CI and every test, and counted:
`n_missing` (no value — the campaign has no `reference`, so `metrics.json` was never written,
`runner.py:107-110`; or the metric is absent) or `n_nonfinite` (`inf`, `-inf`, `NaN` — e.g. PSNR of
identical images); each excluded run is listed under `excluded: [{"run_id", "seed", "reason":
"missing"|"non-finite"}]` (no value is written: `allow_nan=False`). So `n = n_runs − n_failed −
n_direct_excluded − n_missing − n_nonfinite` for each metric. A campaign without `reference` therefore
yields `n` 0 for every configuration and metric, `ci: null` (`ci_note: n<2`), every test `computed:
false` (`n<2`), and exits 0 — no crash. When any run is excluded this way, `stats.json` `warnings` gains
one entry per metric (`rule: values-missing` or `values-nonfinite`, with the counts).

**Guards (decided, not left to scipy):** every precondition in the "Needs" column is checked before the
call; a violated one → `{"computed": false, "reason": "<rule>"}` (`n<2`, `constant`, `no-pairs`,
`all-differences-zero`, `not-replicates`, `no-numeric-parameter`, `test-unsupported`, `no-baseline-run`).
scipy warnings are caught (`warnings.catch_warnings(record=True)`) and listed in the result; **any NaN or
infinite statistic or p-value → not computed** (reason `numerical`), never written as a number. Rationale,
measured on this host: `wilcoxon` of identical samples returns statistic 0, p 1.0 with only a
RuntimeWarning; `mannwhitneyu` of constant equal samples returns p NaN; `ttest_ind` of constant samples
returns NaN; `bootstrap` of constant data returns a NaN interval with `DegenerateDataWarning`, and raises
`ValueError` for n = 1.

**Descriptives** per configuration and metric: `n`, `n_missing`, `n_nonfinite`, `mean`, `sd` (ddof 1;
null for n < 2), `median`, `min`, `max` (all null for n = 0), `values` (the usable replicate values in
replicate order, with their `run_id`s), `excluded`, and `ci`:
`scipy.stats.bootstrap((np.asarray(values, dtype=float),), np.mean, n_resamples=R,
confidence_level=ci, method="BCa", rng=np.random.default_rng(seed))` → `{"low", "high", "level",
"method": "BCa", "resamples", "seed"}`; null with `ci_note` `n<2` or `degenerate` (NaN interval).

**Effect sizes** (pairwise and paired tests; config minus baseline): `mean_difference`; `hedges_g` =
J·(mean_c − mean_b)/s_pooled with s_pooled = √(((n_c−1)s_c² + (n_b−1)s_b²)/(n_c+n_b−2)) and
J = 1 − 3/(4(n_c+n_b) − 9) (null when s_pooled = 0 or n < 2); `cliffs_delta` = (#(c > b) − #(c < b))/(n_c·n_b);
paired tests also `d_z` = mean(d)/sd(d) (null when sd(d) = 0). `better` = sign of `mean_difference`
agrees with the metric's `higher_is_better` (null when the difference is 0). Correlation tests report
`r`/`rho` as their statistic.

**Corrections** (own code, the formulas of the generator): `bonferroni` min(1, m·p); `holm` step-down
with running max; `hochberg` step-up with running min; `bh` step-up with running min of p·m/rank;
`none`. Applied to the computed tests of one family (M-D2-D5); `p_adjusted` null for tests not computed;
`reject` = `p_adjusted <= alpha` (null when not computed or the correction is not applied).

**Reference values** (`reviews/m-d2-stats-reference.json` → `tests/fixtures/stats/reference.json`, 18
cases, synthetic data): descriptives (3), Welch, Student, paired t, Mann-Whitney (2), Wilcoxon, ANOVA,
Kruskal-Wallis, Pearson, Spearman, effect sizes (2), the five corrections on one p-vector, bootstrap CI
(2). Tolerance rel 1e-9, abs 1e-12. Each value except the bootstrap pins was computed by the scipy call
above **and** by an independent hand formula in the generator, which stops unless both agree; the
bootstrap intervals are a regression pin of the one fully specified call (no independent route). Integer
cross-checks a reviewer can redo by hand: Mann-Whitney U(B vs A) = 36 (every B above every A) and
U(C vs A) = 19; Wilcoxon W(C vs A) = 9; Cliff's delta(B vs A) = 1.0; Holm of p = [0.01, 0.04, 0.03, 0.005,
0.2] = [0.04, 0.09, 0.09, 0.025, 0.2]; Hochberg = [0.04, 0.08, 0.08, 0.025, 0.2]; BH = [0.025, 0.05,
0.05, 0.025, 0.2]; Bonferroni = [0.05, 0.2, 0.15, 0.025, 1.0]. Welch B vs A: t 5.0382, df 8.533,
p 0.000825.

**`stats.json`** (written by `scieflow experiment stats <campaign_dir> [--include-direct] [--json]`
into the campaign folder; deterministic: `json.dumps(sort_keys=True, indent=1, allow_nan=False)`, no
timestamp, a symlinked target refused, written via temp file + `os.replace`; `results.csv` is
re-written by the same command):

```json
{"version": 1, "tool": "scieflow experiment stats", "campaign": "...",
 "source": {"results_sha256": "...", "campaign_sha256": "..."},
 "settings": {"include_direct": false, "alpha": 0.05, "ci": 0.95, "correction": "holm",
              "bootstrap": {"resamples": 9999, "seed": 0}, "baseline": "001_sigma-1.0", "tests": {"ssim": "welch-t-test"}},
 "design": {"status": "none", "version": null, "in_force_hash": null, "study_type": null},
 "data_level": "L0",
 "metrics": {"ssim": {"higher_is_better": true}},
 "configs": [{"config_id": "...", "params": {...}, "n_runs": 5, "n_failed": 0, "n_direct_excluded": 0,
              "metrics": {"ssim": {"n": 5, "n_missing": 0, "n_nonfinite": 0, "mean": 0, "sd": 0, "median": 0, "min": 0, "max": 0,
                                   "values": [{"run_id": "...", "seed": 0, "value": 0}], "excluded": [], "ci": {...}}}}],
 "tests": [{"id": "ssim:000_sigma-0.5", "metric": "ssim", "kind": "pairwise", "test": "welch-t-test",
            "source": "campaign|sap", "config_id": "...", "baseline": "...", "n": 5, "n_baseline": 5,
            "n_unpaired": 0, "computed": true, "statistic": 0, "df": 0, "p": 0, "p_adjusted": 0,
            "reject": true, "family": "campaign|confirmatory|exploratory", "confirmatory": false,
            "effect": {"mean_difference": 0, "hedges_g": 0, "cliffs_delta": 0, "d_z": null, "better": true},
            "warnings": []}],
 "families": [{"name": "campaign", "correction": "holm", "alpha": 0.05, "size": 4}],
 "method": {"scipy": "1.18.1", "numpy": "2.5.3", "assumptions": {"welch-t-test": ["independent runs", "approximately normal metric per configuration"], "...": []}},
 "warnings": [{"rule": "...", "message": "..."}]}
```

Test ids are `<metric>:<config_id>` (pairwise/paired) or `<metric>:<kind>` (omnibus/correlation); tests
are ordered by metric (campaign order), then configuration (campaign order); the baseline is never
compared with itself. Refusals (exit 1, nothing written): no or unreadable `results.json`/`campaign.yaml`
(rule and file named); a pairwise/paired test resolved for some metric and no `baseline` (`no-baseline`);
an SAP conflict (5.5); scipy/numpy not importable ("needs the experiments extra"), or an importable
scipy older than 1.15 — the first release whose `scipy.stats.bootstrap` takes `rng=` — ("needs the
experiments extra (scipy >= 1.15); found <version>"; the check compares the numeric major/minor of
`scipy.__version__` before any computation). Exit 2: usage.
Without `--json` it prints one line per configuration × metric (`mean`, CI) and the test lines.

**Staleness**: a `stats.json` is *fresh* when `source.results_sha256` and `source.campaign_sha256` equal
the current files' sha256. `tables`, `figures`, `compare-campaigns` refuse a stale or missing one (exit 1,
"run scieflow experiment stats first"); `report` skips its Statistics section with a note. A metrics
recompute (5.3) rewrites `results.json`, so it always makes the campaign's `stats.json` stale.

### 5.5 Binding to the locked SAP (`sap.py`)

`binding(campaign_dir) -> Binding(status, version, in_force_hash, study_type, computational,
outcomes: {metric: {"outcome", "test", "priority"}}, multiplicity: {"method", "alpha"} | None)`:
`run_dir = actions.run_for_path(campaign_dir)`; none → `status "none"`. Else `lock.state(Project(run_dir.parent.parent),
run_dir)` (M-D1 plan §5.5) and, when `locked`, the SAP of the in-force design (`model.load(run_dir)`;
current == in-force when locked). Outcomes without `metric` are ignored (they are not campaign metrics).
Reading never writes and never raises past the caller: an unexpected exception → `status
"error"` with a warning (fail closed: no SAP binding is claimed).

Resolution per metric, when `status == "locked"`:

| Situation | Test used | Family |
|---|---|---|
| metric is an SAP outcome, priority primary/secondary | SAP `test` (`source: sap`) | `confirmatory` (SAP method, SAP alpha) |
| metric is an SAP outcome, priority exploratory | SAP `test` | `exploratory` (BH, SAP alpha) |
| metric not named by the SAP (possible only for computational designs, M-D1 refuses it otherwise) | campaign `analysis.test` | `exploratory`, `unplanned: true` |

SAP multiplicity → correction: none→none, bonferroni→bonferroni, holm→holm, hochberg→hochberg,
bh-fdr→bh; hierarchical/gatekeeping → correction `not-applied`, `p_adjusted`/`reject` null, warning
`sap-correction-unsupported` (M-D2-D6). SAP `descriptive` → no test; an unsupported SAP test → the test
entry with `computed: false, reason: test-unsupported`.

Any other status (draft, pending, modified, invalid, ledger-invalid, error) → campaign settings, family
`campaign`, every test `confirmatory: false`, warning `design-<status>` ("the analysis is not
confirmatory: the design is not locked"). `stats.json` `design` records status, version, hash, study type.

**Conflicts** `conflicts(binding, campaign) -> [(rule, metric)]`, only when locked: `sap-test-mismatch`
(campaign `analysis.test` for an SAP metric ≠ SAP test), `sap-correction-mismatch` (`analysis.correction`
set and ≠ the mapped SAP correction), `sap-alpha-mismatch` (`analysis.alpha` set and ≠ SAP alpha).
Non-computational → refusal; computational → warning, SAP used.

- **At sweep/run time** `_analysis_guard(budget_run, campaign)` right after M-D1's `_design_guard`:
  refusal → `job.refused` (`reason: design`, `rule`, `campaign`, `metrics`) + `ClickException` naming the
  rule and metric names ("the locked analysis plan says <test> for <metric>; remove `analysis.test` or
  amend the design with `scieflow design amend`"); warning → stderr + `note.design_warning` (M-D1's note
  name, same shape). Not inside a run, or no design → nothing (unchanged behaviour).
- **In `stats`**: the same conflicts refuse (non-computational) or warn (computational), because a
  campaign may have run before the design was locked.

### 5.6 Tidy `results.csv` (`tidy.py`)

Written by `sweep` after `results.json`, re-written by `stats` and by a metrics recompute (5.3). One row
per run × campaign metric, runs in `results.json` order, metrics in campaign order. Every run keeps all
its rows: a failed run, and a completed run without a value for the metric (no `reference`, or the metric
absent), have an empty `value`; a non-finite value is written as `inf`, `-inf` or `nan` (float `repr()`),
so the table never drops a run (the statistics' exclusion rule of 5.4 applies only to `stats.json`).
Columns: `campaign, config_id, run_id, replicate, seed, mode, status, metric, value`, then
`param.<name>` for the sorted union of parameter names. Cell encoding: float `repr()`, int `str()`, bool
`true|false`, None empty, str as is, other values compact JSON (`separators=(",", ":"), sort_keys=True`).
`csv.writer(lineterminator="\n", quoting=csv.QUOTE_MINIMAL)`, UTF-8, temp file + `os.replace`.
Deterministic (byte-identical for the same `results.json`).

### 5.7 Tables, figures and the sizing contract (`tables.py`, `figures.py`, `style.py`)

**Tables** — `scieflow experiment tables <campaign_dir>` (fresh `stats.json` required) writes
`tables/summary.{md,tex}` (rows = configurations in campaign order, columns = metrics; cell
`mean ± sd [low, high] (n)`, `—` when n = 0) and, when tests exist, `tables/tests.{md,tex}` (metric,
configuration or kind, test, statistic, df, p, p_adjusted, reject, hedges_g, cliffs_delta, family).
Number format: means/sd/CI `f"{x:.4f}"`; statistic `.3f`; p and p_adjusted `"< 0.001"` below 0.001 else
`.3f`; effect sizes `.2f`; not computed → `n/c (<reason>)`. LaTeX: `booktabs` (`\toprule \midrule
\bottomrule`, a `% requires \usepackage{booktabs}` first line), every text cell escaped (`\ & % $ # _ { }
~ ^`). Each table has a sidecar `tables/<id>.json` `{"id", "kind": "table", "campaign", "files": {"md":
sha256, "tex": sha256}, "source": {"stats_sha256"}, "tool", "version": 1}` (`id` = `summary` | `tests`).

**Figure** — `scieflow experiment figures <campaign_dir> [--metric M]… [--width single|double]
[--style-file PATH]` (fresh `stats.json` required) writes `figures/metrics.<ext>` for each configured
format and `figures/metrics.json` (sidecar: id, kind `figure`, campaign, metrics, files {ext: sha256},
source stats sha256, the resolved style, tool, version). One panel per metric (campaign order, or the
`--metric` selection), `ncols = min(3, k)`, at most 9 panels (more → exit 1 "select metrics with
--metric"). Per panel: x = the single varying numeric parameter (line through the means) or, otherwise,
the configuration index with config-id tick labels (rotated 45°); y = mean with asymmetric CI error bars
(no bar when the CI is null); every usable replicate value (`stats.json` `values`) as a small grey dot at x + offset_k
(`offset_k = linspace(-0.15, 0.15, n)[k]` × the x spacing; deterministic, no random jitter); the baseline
mean as a dashed horizontal line; title `<metric> (higher is better|lower is better)`. A configuration
with n = 0 for the metric gets no marker; a panel whose metric has n = 0 in every configuration (e.g. a
campaign without `reference`) is drawn as empty axes with the centred text "no values" — never an
exception. Width:
`--width` (default `single` for ≤ 2 panels, `double` otherwise) → mm from the style; height = width ×
0.62 per panel row; `figsize` = mm / 25.4.

Determinism (probed on this host with matplotlib 3.11.2: two processes with different `PYTHONHASHSEED`
and `MPLCONFIGDIR` produced identical sha256 for PNG, SVG and PDF): render inside
`matplotlib.rc_context({"svg.hashsalt": "scieflow", "svg.fonttype": "path", "pdf.fonttype": 42,
"font.family": "DejaVu Sans", "font.size": <font_pt>})` with the Agg backend, and save with
`metadata={"Software": None}` (PNG), `{"Date": None, "Creator": None}` (SVG), `{"CreationDate": None,
"Creator": None, "Producer": None}` (PDF), `dpi` from the style, after `fig.tight_layout()` and without
`bbox_inches="tight"` (the saved size must equal the style's size). No timestamps anywhere.

**Sizing contract** `figures:` block (`schemas/figure-style.yml` describes it; `style.py` holds the same
constants, pinned equal by a test): `single_column_mm` (default 89, range 30–200), `double_column_mm`
(183, 60–300), `font_pt` (8, 5–14), `dpi` (300, 72–1200), `formats` (default `[png, svg]`, subset of
png/svg/pdf, non-empty). `--style-file` = a YAML file whose top-level `figures:` mapping overrides
defaults; unknown keys, out-of-range values, a symlinked or unreadable file → exit 1 naming the key.
M-F's journal profile is expected to carry the same `figures:` block (M-D2-D8).

**Report** — `generate_report` adds `## Statistics` after the results table when `stats.json` is fresh:
the summary table (Markdown, same function as `tables`), the tests table when present, one line per
family (correction, alpha, size), the design status, and links to `figures/metrics.png` and
`tables/*.md` when they exist. A stale or missing `stats.json` adds only the line "Statistics: not
computed (run `scieflow experiment stats`)" — the rest of the report is unchanged, and the `## Evaluation`
preservation (`report.py:129-141`) is untouched. With replicates, the results table gains a configuration
summary (`configs` from `compare`) before the per-run rows.

### 5.8 Compare with replicates, and across campaigns

- `compare_campaign`: when `results.json` has `replicates`, `comparison.json` gains `configs`
  (`[{config_id, params, n, n_failed, mean, runs: [run ids]}]` over completed, ranked runs, mean of
  `rank_by`, ordered by mean in the metric's direction, ties by config id) and `best_config`. `best`
  stays the best single run (unchanged meaning); the CLI prints the configuration ranking first. Without
  `replicates` the output is unchanged.
- `scieflow experiment compare-campaigns DIR DIR… [--metric M]… [--json] [--out PATH [--force]]`
  (`crosscompare.py`): every DIR needs a fresh `stats.json`; the metrics compared are those in every
  campaign (or `--metric`, which must be shared); per campaign and metric: the best configuration by mean
  in the metric's direction (`stats.json` `metrics.<m>.higher_is_better`) among the configurations with
  n ≥ 1, its params, n, mean, CI, the
  design status and the `stats.json` sha256; a campaign with n = 0 in every configuration for that metric
  gets `best: null, reason: "no-values"` (reported, not a refusal). Output `{"version": 1, "tool", "metrics", "campaigns": […],
  "note": "no test across campaigns: inputs and stages differ"}`. Refusals (exit 1): fewer than two
  directories, a missing/stale `stats.json`, no shared metric, an unshared `--metric`, an existing
  `--out` without `--force`, a symlinked `--out`. Read-only otherwise; nothing written without `--out`.

### 5.9 CLI surface, exit codes and events

Guard order in `run` and `sweep` (every refusal happens before any run directory, spend or container
start): (1) `Campaign.load`, `find_stage`, `check_replicates` (configuration errors, no event);
(2) `_guard` (exhausted budget, existing); (3) M-D1's `_design_guard`; (4) `_analysis_guard` (5.5, t4);
(5) `_guard_capacity` (5.2). t1 adds (5) directly after (3); t4 inserts (4) between them.

New `scieflow experiment` subcommands: `stats`, `verify`, `tables`, `figures`, `compare-campaigns`; new
options `run --seed`; new behaviour in `run`/`sweep` (replicates, budget pre-check, analysis guard) and in
`metrics` (a recompute also refreshes the manifest, `results.json` and `results.csv`, 5.3; its output and
exit codes are unchanged).
Exit codes: 0 ok; 1 refused / check failed (`ClickException`, the existing `docs/cli.md` row "1 any
other refusal"); 2 usage. No new exit code (the `docs/cli.md` exit table is not edited). `--json` on
`stats`, `verify`, `compare-campaigns`. When the campaign folder lies inside a run, `stats`, `tables`,
`figures`, `verify` and `metrics` emit `note.experiment_analysis` (`actor: system`, `campaign`, `kind`
∈ {stats, tables, figures, verify, metrics-recompute}, `run_id` for a recompute, `sha256` of the main
output or `problems` count) — no values.
`job.refused` (`reason: budget` + `rule: insufficient`; `reason: design` + `rule: sap-…`) as in 5.2/5.5.

### 5.10 Data protection (M-B, D9–D11, D23)

- Everything in M-D2 runs locally on files inside the run; no network client, no new egress. Git and
  apptainer are called with fixed argv, no shell, with timeouts, read-only.
- `manifest.json` and `stats.json` record `data_level` (M-B `levels.run_level`), so a reader sees that the
  numbers of an L2 run are L2-derived. M-D2 never declares, lowers or releases a level; sharing a result of
  an L2 run goes through `scieflow data release` (M-B), unchanged. D23 (L2 analysis local by default) holds
  trivially: `stats` is local.
- Refusal and warning messages name rules, keys, metric names, configuration ids and relative paths —
  never metric values or file contents. (The human-readable `stats` output prints aggregates to the
  terminal of whoever ran it, like `compare` today.)
- M-D2 adds no file to the dispatch `protected` list (`core/data/dispatch.py`): its outputs are
  regenerable records, and their tamper evidence is `verify` (residual R2).

### 5.11 Docs, skills and the experiments rule

- `docs/experiments/analysis.md` (new; nav "Analysis and Statistics" after "CLI Reference",
  `mkdocs.yml`): replicates and seeds; budget semantics (D21, the pre-check, the remote-array rule for
  M-D3); manifest fields and `verify` (problems vs notes; recomputing metrics with `scieflow experiment
  metrics` and why it makes `stats.json` stale); `results.csv` columns; statistics — kinds table, guards,
  usable values (`n_missing`, `n_nonfinite`, `excluded`), formulas
  of effect sizes and corrections, families, references (Welch 1947; Mann & Whitney 1947; Wilcoxon 1945;
  Kruskal & Wallis 1952; Hedges & Olkin 1985; Cliff 1993; Holm 1979; Hochberg 1988; Benjamini & Hochberg
  1995; Efron 1987 for BCa), assumptions, the small-n limits of rank tests (e.g. the exact two-sided
  Wilcoxon p cannot go below 0.0625 with 5 pairs — so pick n accordingly); SAP binding; tables, figures
  and the `figures:` contract (for M-F); `compare-campaigns`; the data-level note; residuals.
- `docs/experiments/cli.md`: `--seed` and replicate run ids under `run`/`sweep`; new sections `stats`,
  `verify`, `tables`, `figures`, `compare-campaigns`.
- `docs/experiments/concepts.md`: stage key `accepts_seed`; campaign keys `replicates`, `analysis`; the
  run tree gains `manifest.json`, and the campaign folder `results.csv`, `stats.json`, `tables/`,
  `figures/`, `packages/`.
- `skills/experiment-cycle/SKILL.md`: "Afterwards" corrected (5.2); the results-summary contract quotes
  `stats.json` (means, CIs, tests) when the campaign has replicates; never compute statistics.
- `src/scieflow/experiments/skills/evaluator/SKILL.md`: after `compare`/`report`, run `scieflow
  experiment stats`, `verify`, `tables`, `figures`; interpret `stats.json` (CIs, effect sizes, adjusted
  p, not-computed reasons, `n_missing`/`n_nonfinite`); after `scieflow experiment metrics` re-run `stats`
  (the old `stats.json` is stale); never compute or round numbers yourself.
- `src/scieflow/experiments/skills/experiment-designer/SKILL.md`: schema block gains `replicates` and
  `analysis`; run count = configurations × replicates (budget); replicates need a stage with
  `accepts_seed: true`; under a locked design the SAP decides tests (do not set `analysis.test` for SAP
  outcomes).
- `src/scieflow/experiments/skills/experiment-runner/SKILL.md`: `scieflow experiment verify` after a sweep.
- `src/scieflow/experiments/AGENTS.md`: the protected edit of section 3.

### 5.12 Security and integrity properties (reviewed in every ticket that touches them)

| Threat | Property |
|---|---|
| An agent reports numbers it computed itself | rule 6 + skills: numbers come from `stats.json`; `tables`/`report` render from it |
| A test chosen after seeing results, against the locked SAP | SAP test/correction/alpha always used when locked; conflicting campaign settings refused before the sweep and by `stats` (non-computational) |
| A changed output or manifest after the fact | sha256 of every artifact in the manifest, manifest sha256 in `results.json`, `verify`; stale `stats.json` refused by tables/figures/compare; a metrics recompute goes through the tool, is recorded in `metrics_history` and stales `stats.json`, while a hand edit of `metrics.json` fails `verify` (`metrics-hash`) |
| Runs without a usable value silently shrinking n | `n_missing`/`n_nonfinite`/`excluded` per configuration × metric, a `values-*` warning, rows kept in `results.csv` |
| Overspending the run budget with replicates | pre-check refuses before any run; spend = runs executed |
| A degenerate input producing a fake p-value | guards before every call; NaN/inf → not computed; warnings recorded |
| Silent loss of failed replicates | `n_failed` per configuration; failed runs stay in `results.json`, `results.csv`, the report |
| Leaking host or user details in shared records | no host name, user name or environment in manifests; paths as already recorded in `config.yaml` |
| Patient data in messages | messages carry rules, keys, names, ids and relative paths only; `data_level` recorded; release stays M-B's |
| Subprocess abuse | fixed argv lists, no shell, timeouts, capped output (git, apptainer) |
| Writing outside the run | every output path is inside the campaign folder; symlinked outputs refused; `--out` refuses symlinks and existing files |
| Fail-open parsing | malformed `results.json`/`campaign.yaml`/`stats.json`/style file → exit 1 naming file and rule |

### 5.13 What M-D2 does not do (stated)

No dataset package builder, Pareto fronts, parquet, project-level tracker or remote campaigns (M-D2-D12);
no regression/mixed/survival models (M-D2-D1); no one-sided tests; no test across campaigns; no
discovery/confirmation data split (that is about data rows, M-D1 SAP `exploratory.split`; campaigns have
runs, not rows); no journal profile (M-F); no menu or web page changes (D7: the CLI is complete; F5).

## Part B — Implementation plan

## 6. Tickets

Staffing: Haiku 5.5 @ high implements test-first (`claude-haiku-5-5`, runtime check `claude-haiku-5.5`);
Sonnet 5.5 @ high reviews (`claude-sonnet-5-5`, `claude-sonnet-5.5`); provider anthropic. A usage limit is
a checkpointed pause: no model swap.

Every card: no claude/codex/agy, no live probe, no network (git ls-remote/fetch in t0 only), no push/PR,
no credentials, no patient data (synthetic fixtures only), never edits an `INFLIGHT` path, a sealed file,
`config/sandbox.yml`, `events.py`, `src/scieflow/core/design/*`, `envbuild.py`, `pyproject.toml`,
`uv.lock`, `CREDITS.md`/`VENDORED.md`, `CLAUDE.md` or any `AGENTS.md` (the t6 protected edit is applied by
the operator). Every implementation and fix card runs the `INFLIGHT` guard and reports its exit code.

### Test commands (all memory-capped)

- **RED/GREEN while editing (dirty tree)**, from the worktree `RUN/scratch/m-d2-wt` (WT):
  `cd WT && systemd-run --user --scope --quiet -p MemoryMax=2500M -p MemorySwapMax=0 env PYTHONPATH=WT/src:WT:WT/scripts PYTHONDONTWRITEBYTECODE=1 /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -m pytest -q -p no:cacheprovider --basetemp ~/.cache/scieflow-m-d2-<step>/pt <paths>`.
  Once per card, the import-origin check first:
  `env PYTHONPATH=WT/src:WT /home/ubuntu/ScieFlow-claude-f2976f52/.venv/bin/python3.12 -B -c 'import os,scieflow;print(os.path.realpath(scieflow.__file__))'`;
  its output must start with `WT/src/`.
- **At each committed checkpoint (clean tree):**
  `G6_OUT_DIR=RUN/scratch/m-d2/junit G6_CACHE_DIR=~/.cache/scieflow-m-d2 RUN/tools/g6_pytest.sh WT <head-sha> m-d2-<step> -- -m 'not slow and not live' <paths>`.
- **Full default suite:** the same runner with `tests` as the path, under the shared lock of the standing
  rules (`flock -w 7200 /home/ubuntu/.cache/scieflow-full-suite.lock …`), started in the background with
  completion notification and a log file.
- **Sealed probes:** `… g6_pytest.sh WT <head> m-d2-sealed -- tests/probes` and the explicit file
  `tests/probes/test_k_task8_binding.py`, also under the lock.
- **Load-flake nodes**: the four named in the installer's standing rules, one rerun, both results
  recorded. No rerun-until-green.
- Every card appends `{step, command, exit_code, passed, failed, errors, skipped, head}` to
  `RUN/scratch/m-d2/executions.jsonl` and states RED and GREEN evidence in its result.
- Focused sets per ticket (clean-tree runner on the ticket's final commit): t1, t2 `tests/experiments`;
  t3, t4 `tests/experiments tests/core/design`; t5 `tests/experiments`; t6 `tests/experiments
  tests/test_docs_analysis.py tests/test_docs_design.py tests/test_docs_role.py`.
- Test sweeps run `--direct` (no apptainer on this host). `stats` and `compare` exclude direct runs by
  default (M-A A-6), so every test that analyses such a campaign passes `--include-direct` (or the API's
  `include_direct=True`), except the tests about that exclusion.
- Tests that need git create a throw-away repository under the pytest temp dir with
  `git -c user.name=t -c user.email=t@example.invalid commit`; tests never touch the real repository's git
  state. Apptainer is always mocked (patch `subprocess.run` inside `scieflow.experiments.manifest` only).

### t0 — worktree, plan and spec copy, green base (Haiku)

1. Section 2: `ls-remote` → base; object-only fetch; the five ancestor checks and the M-D1 publish
   receipt (`RUN/scratch/m-d1/publish.json`, `merged: true`, `pushed_head` ancestor of base); otherwise
   `BASE_RED` with the reason. `worktree add -b dev/m-d2 RUN/scratch/m-d2-wt <base>` (on a retry reuse it;
   never reset).
2. **Green-base gate on `base`** (HEAD = base, clean): full default suite **0 failed, 0 errors**; sealed
   probes 0 failed. No expected counts are given (base = main after M-D1, not measured yet); t0 records
   what it measures. Only the load-flake rule may clear a failure; otherwise `BASE_RED` with node ids and
   junit paths, nothing else.
3. Re-verify every `path:line` cite of section 4 on `base` (lines in `experiments/cli.py` move with M-D1)
   and the M-D1 surface listed at the end of section 4 (module paths, function names, `DesignState`
   fields, the `tests` enumeration in `schemas/design.yml`, `_design_guard`'s place in `run`/`sweep`);
   write `reviews/m-d2-t0-baseline.md` (per cite: still present / moved (new line) / changed, and the real
   M-D1 names when they differ from the plan's). Inventories: (a) whether base contains M-C2 (its
   `RUN/scratch/m-c2/publish.json` if present); (b) every existing test that pins `experiments` CLI
   commands, `results.json`/`comparison.json` keys or `campaign.yaml` content; (c) whether the protected
   edit's `old` text occurs exactly once in `src/scieflow/experiments/AGENTS.md`.
4. Copy `reviews/m-d2-plan.md` byte for byte to `docs/superpowers/plans/2026-10-28-m-d2-analysis.md`,
   and its Part A (from the line `## Part A — Spec` up to, not including, the line
   `## Part B — Implementation plan`) to `docs/superpowers/specs/2026-10-28-m-d2-analysis-design.md`
   with this first line:
   `# M-D2 analysis — design (Part A of docs/superpowers/plans/2026-10-28-m-d2-analysis.md)` and one blank
   line. Commit both alone ("docs(plans): M-D2 analysis spec and plan").
Verdicts `DONE` | `BASE_RED`.

### t1 — replicates, seeds, budget semantics, configuration ranking (Haiku) → t1r (Sonnet)

Files: `campaign.py` (`replicates` only), `stage.py`, `runner.py` (`--seed`, config fields), `sweep.py`
(replicate fields), `compare.py`, `cli.py` (`run --seed`, `check_replicates`, `_guard_capacity`), tests
`tests/experiments/test_replicates.py`, `test_budget_replicates.py`, `test_compare_configs.py`. Test
fixture: a local seeded stage written by the tests (adds `rng(seed)` noise of σ 5 to the image; declares
`accepts_seed: true`), next to the existing `project` fixture (unchanged).

RED → GREEN:
1. `replicates: 3` on a 2-configuration grid → 6 specs, ids `000_gain-0.5_r0 … 001_gain-1.0_r2`, seeds
   0,1,2 per configuration, `config_id` the old id; `{n: 2, seed: 100}` → seeds 100, 101; a campaign
   without `replicates` expands exactly as before (ids, order, `config_id == run_id`).
2. Validation: `replicates: 0`, `1001`, `true`, `"3"`, `{n: 2, x: 1}`, `{n: 2, seed: -1}` → `CampaignError`
   naming `replicates`; `seed` in parameters or a scenario with `replicates` → refused.
3. `stage.yaml` `accepts_seed: "yes"` → `StageError`; a replicate sweep on the fixture's `scale` stage
   (no `accepts_seed`) → exit 1 with the rule, no `experiments/` folder, no spend, no event.
4. Command and records: `build_command` ends with `--seed 1` for a seeded spec; `config.yaml` and
   `results.json` carry `config_id`, `replicate`, `seed`; `results.json` has `replicates: {n, seed}`; a
   non-replicate sweep's `config.yaml` and `campaign.yaml` equal today's (no new keys).
5. Seeds reach the stage: the seeded stage's outputs differ across replicates and are identical for the
   same seed across two sweeps (byte-equal artifact).
6. **Budget (GAP acceptance "replicate budget semantics tested", D21):** a 2 × 3 replicate sweep inside a
   run with cap 10 records `experiment_runs` 6 and one `budget.recorded` event; with cap 10 and 5 already
   spent (5 remaining < 6 planned) → exit 1 "insufficient", no `experiments/` folder, spend unchanged, one
   `job.refused` event with `reason: budget`, `rule: insufficient`, `planned: 6`, `remaining: 5`, **no
   checkpoint** (status not stopped); cap 0 (unlimited) → runs; **(review F2)** the same 2 × 3 sweep in
   a run folder that has `status.yml` but **no `budget.yml`** → runs all 6, no refusal, no `job.refused`
   and no `budget.recorded` event, no `budget.yml` created; a `budget.yml` with `max_experiment_runs:
   null` → runs, no refusal; `_guard_capacity` called directly (unit level, because the existing `_guard`
   runs first and is unchanged) with a budget whose `budgets` lacks `max_experiment_runs`, with one whose
   `spent` lacks `experiment_runs` (cap 10, planned 6 → passes; planned 11 → refused, `remaining: 10`),
   and with `budget_run=None` → as specified in 5.2; `run --seed 3` records 1 and passes
   `--seed 3`; `run --seed` on a stage without `accepts_seed` → exit 1. The three tests of
   `tests/experiments/test_budget_counting.py` stay unchanged and green.
7. Compare (`--include-direct`, the test sweep being direct): on a replicate campaign `comparison.json`
   has `configs` ordered by mean of `rank_by` and `best_config`; `best` is still a run id; on a non-replicate campaign `comparison.json` has no new keys.

t1r checklist: refusals happen before any run/spend; old campaigns byte-compatible; seeds deterministic;
budget numbers exact; no `actions.py` edit; INFLIGHT guard 0.

### t2 — run manifest, package capture, `verify`, `results.csv` (Haiku) → t2r (Sonnet)

Files: `manifest.py`, `tidy.py` (new), `runner.py` (manifest write, `context` parameter,
`recompute_metrics` refresh), `sweep.py` (context once, `manifest_sha256`, `results.csv`,
`update_run_entry`), `cli.py` (`verify`; `run` passes a context; `metrics` note), tests
`test_manifest.py`, `test_verify.py`, `test_results_csv.py`, `test_recompute_records.py`.

RED → GREEN:
1. **(GAP acceptance "manifest fields present and hashed")** A direct sweep in a temporary git
   repository: every run has `manifest.json` with every key of 5.3; `code.pipeline.git.sha` equals
   `git rev-parse HEAD`; `dirty` false, then true after editing a tracked pipeline file (an untracked file
   does not make it dirty); `inputs.input.sha256`/`reference.sha256` equal `hashlib.sha256` of the files;
   each `artifacts[]` hash equals the file's; `metrics_sha256` equals the metrics file's; `results.json`
   `manifest_sha256` equals the manifest file's sha256; `seed`/`replicate` match the spec.
2. Outside git → `git: null` with a reason; a git timeout (monkeypatched) → null with reason, run still
   completes; a failed run still gets a manifest (`status: failed`, `metrics_sha256: null`);
   **(review A2)** a pipeline folder that lies inside a temporary git repository but is ignored there
   (listed in its `.gitignore`, so `stage.yaml` is untracked) → `code.pipeline.git: null`, reason
   `not-tracked` (not that repository's HEAD).
3. Directory input → `kind: dir` with a tree hash that changes when a file changes and when a file is
   renamed; a symlink inside is recorded, not followed.
4. Packages: direct → `captured`, sorted, unique, contains `numpy==<version>`; apptainer (mocked
   `subprocess.run` in `manifest`) → list parsed, written once to `packages/<sha12>.txt` for a 3-run
   sweep (one exec call), manifest references it; mocked non-zero exit / `FileNotFoundError` / timeout →
   `not-captured` with reason, run unaffected. No host name, user name or environment variable in any
   manifest (string search for `socket.gethostname()` and `getpass.getuser()` values).
5. Context computed once per sweep: input hashing and git called once for a 4-run sweep (counted with a
   spy).
6. `verify`: a clean campaign → exit 0; tampered artifact → `artifact-hash`; deleted artifact →
   `artifact-missing`; added file → `artifact-extra`; edited manifest → `manifest-hash`; edited
   metrics.json → `metrics-hash`; a pre-M-D2 `results.json` without `manifest_sha256` → `legacy-run`,
   exit 0; `--json` deterministic; messages contain no file contents.
7. `results.csv`: exact expected text for a small synthetic `results.json` (failed run rows with empty
   value, param union columns, bool/None/str/dict encodings); byte-identical across two writes; written by
   `sweep`.
8. Existing `tests/experiments/test_runner.py`, `test_sweep.py`, `test_envbuild.py` unchanged and green
   (`envbuild.py` untouched).
9. **(review F1) Recompute keeps the records coherent** (`test_recompute_records.py`): after a direct
   2-run sweep, monkeypatch `scieflow.experiments.runner.compute_metrics` so the recompute yields a
   different value (in-process `CliRunner`), then
   `scieflow experiment metrics <run_dir>`: `metrics.json` holds the new value; the manifest's
   `metrics_sha256` equals the new file's sha256 and `metrics_history` has exactly one entry whose
   `previous_sha256` is the old hash and `sha256` the new; `results.json`'s entry for that run has the new
   `metrics` and `manifest_sha256` equal to the new manifest file's sha256, while the other run's entry
   and the top-level keys are equal (parsed) to before; `results.csv` shows the new value;
   `verify` exits 0 and lists the run under the note `metrics-recomputed`; the sha256 of `results.json`
   changed (the key `stats` freshness uses; the end-to-end staleness check is t3 case 6). Contrast: editing
   `metrics.json` by hand → `verify` exit 1 `metrics-hash`. A run made by `scieflow experiment run` (not in `results.json`)
   → only its own `metrics.json` and manifest change, no `results.json` created. A legacy run folder
   (no `manifest.json`) → `metrics.json` and its `results.json` entry are updated, no manifest is created.
   Inside a run workspace one `note.experiment_analysis` with `kind: metrics-recompute` and no metric
   value in the event. `tests/experiments/test_runner.py::test_recompute_metrics` and
   `tests/experiments/test_cli.py` stay unchanged and green.

t2r checklist: hashes correct and complete; capture failures never fail a run; no host/user details;
subprocess calls fixed argv with timeouts; atomic writes; a metrics recompute updates metrics.json,
manifest, results.json and results.csv coherently and `verify` tells it apart from a hand edit;
INFLIGHT guard 0.

### t3 — statistics core, `analysis` block, `scieflow experiment stats` (Haiku) → t3r (Sonnet)

Files: `stats.py` (new; scipy/numpy imported lazily inside functions), `analysis.py` (new; no SAP yet:
design block `{"status": "none", …}` unless t4), `campaign.py` (`analysis` key), `cli.py` (`stats`),
`tests/fixtures/stats/reference.json` (byte copy; sha256 checked against the run input first), tests
`test_stats_reference.py`, `test_stats_guards.py`, `test_analysis.py`, `test_stats_cli.py`.

RED → GREEN:
1. **(GAP acceptance "stats vs scipy reference")** every case of `reference.json` reproduces through the
   `stats.py` API within its tolerance: descriptives, the five two-sample/paired tests, ANOVA,
   Kruskal-Wallis, Pearson, Spearman (statistic, df where given, p), effect sizes, the five corrections,
   the two bootstrap CIs (same call, seed 0); the hand cross-check integers of 5.4 hold (U 36 and 19,
   W 9, delta 1.0, the Holm/Hochberg/BH/Bonferroni vectors).
2. Guards: constant samples, n = 1, identical paired samples (the scipy "p = 1.0" case), no shared seeds,
   a non-replicate campaign with a paired test, two varying parameters for a correlation, a bool
   parameter, n = 2 for a correlation, a monkeypatched scipy result with NaN p → each gives
   `computed: false` with its reason; bootstrap of constant data → `ci: null`, `ci_note: degenerate`;
   no NaN or inf ever reaches `stats.json` (`allow_nan=False` dump succeeds; a test parses and walks it).
3. Campaign `analysis` validation: each bad key/value/test name/metric/baseline of 5.4 → `CampaignError`
   naming the key path; `TESTS` equals the `tests` enumeration of `schemas/design.yml` (pin test).
4. End to end: a seeded 3-configuration × 6-replicate direct sweep with `analysis: {baseline: …, test:
   welch-t-test}`, `stats --include-direct` → `stats.json` has 3 configs with n 6, two tests (baseline excluded), family `campaign`
   with Holm, `p_adjusted` = Holm of the two p-values, `reject` per alpha; `design.status: none`.
5. Failed and direct runs: a configuration with one failed replicate has `n` 5, `n_failed` 1; a direct
   sweep without `--include-direct` gives n 0 per configuration, `n_direct_excluded` 6 and every test
   `computed: false` (`n<2`); with `--include-direct` the runs count and `settings.include_direct` is true.
6. Determinism and freshness: two `stats` runs give byte-identical `stats.json`; it records the sha256 of
   `results.json` and `campaign.yaml`; `results.csv` is re-written identically; **(review F1)** after
   `scieflow experiment metrics <run_dir>` on one of its runs the `stats.json` is stale (the freshness
   helper returns false) and a new `stats` run reflects the recomputed value.
7. CLI: missing `results.json` → exit 1 naming the file; pairwise test without baseline → exit 1
   `no-baseline`; `--json` prints the document; no analysis block → descriptives only, `tests: []`.
8. **(review F3, A1) Usable values and the scipy floor.** (a) A seeded 2-configuration × 3-replicate
   direct campaign **without `reference`** (no `metrics.json` anywhere) and `analysis.test:
   welch-t-test` → `stats --include-direct` exits 0; every configuration × metric has `n` 0, `n_missing`
   3, `mean`/`sd`/`median`/`min`/`max` null, `ci: null` with `ci_note: n<2`, `excluded` lists the three
   run ids with reason `missing`; the test is `computed: false`, reason `n<2`; one `values-missing`
   warning per metric; `results.csv` still has 2 × 3 × k rows with empty `value`. (b) A synthetic
   `results.json` where one completed run lacks `ssim` and one has `psnr: Infinity` → that configuration's
   `ssim` `n` drops by 1 with `n_missing` 1, its `psnr` `n` drops by 1 with `n_nonfinite` 1, the paired
   seeds exclude those runs (`n_unpaired` counts them), the `allow_nan=False` dump succeeds, and
   `results.csv` writes `inf` for the PSNR cell. (c) `scipy.__version__` monkeypatched to `"1.14.1"` →
   `stats` exit 1 "needs the experiments extra (scipy >= 1.15); found 1.14.1", nothing written.

t3r checklist: every scipy call exactly as the 5.4 table (arguments included); guards precede calls;
NaN never becomes a number; missing and non-finite values are excluded and counted, never dropped
silently; corrections match the generator's formulas; the fixture is a byte copy (not
regenerated); no new dependency; INFLIGHT guard 0.

### t4 — binding to the locked SAP (Haiku) → t4r (Sonnet)

Files: `sap.py` (new), `analysis.py` (families, labels, design block, `data_level`), `cli.py`
(`_analysis_guard` in `run` and `sweep`, after M-D1's `_design_guard`; `stats` conflict handling), tests
`test_sap_binding.py`, `test_analysis_guard.py`. Designs are created and locked only through M-D1's own
API (`scieflow design init/check`, `lock.lock` + `service.answer_gate` as a human), never by writing the
ledger; the fixture root gets a copy of the repo's `schemas/` (as M-D1 t5 does).

RED → GREEN:
1. Binding: no run → `none`; run without design → `none`; locked RCT design whose outcomes name
   `metric: ssim` (primary, `mann-whitney`) and `metric: mse` (exploratory, `welch-t-test`), multiplicity
   holm α 0.04 → outcomes map, method/alpha as given; draft and modified designs → their status.
2. `stats` on that locked run: `ssim` tests use `mann-whitney` (`source: sap`, family `confirmatory`,
   Holm at α 0.04), `mse` tests `welch-t-test` in family `exploratory` (BH); `design` block carries
   status, version, hash, study type; `data_level` equals `levels.run_level`.
3. Conflicts, non-computational: campaign `analysis.test: welch-t-test` for `ssim` → `sweep` exit 1
   `sap-test-mismatch` before any run (no folder, no spend, one `job.refused` `reason: design`);
   `analysis.correction: bh` → `sap-correction-mismatch`; `analysis.alpha: 0.05` → `sap-alpha-mismatch`;
   `stats` on an earlier campaign with the same conflict → exit 1 with the same rule.
4. Computational design with the same conflicts → sweep runs, stderr warning, one `note.design_warning`;
   `stats` uses the SAP test and records the warning; a metric not in the SAP → family `exploratory`,
   `unplanned: true`.
5. SAP multiplicity `gatekeeping` → correction `not-applied`, `p_adjusted`/`reject` null, warning
   `sap-correction-unsupported`; SAP test `cox-regression` → `computed: false`, `test-unsupported`; SAP
   `descriptive` → no test for that metric.
6. Unlocked (draft) design → campaign settings, every test `confirmatory: false`, warning
   `design-draft`; an exception inside the M-D1 call (monkeypatched) → status `error`, warning, campaign
   settings, no crash.
7. Runs without `design/` and campaigns outside a run behave exactly as in t3 (all t1–t3 tests green);
   M-D1's tests in `tests/core/design` and `tests/experiments/test_design_guard.py` unchanged and green.

t4r checklist: SAP always wins when locked; refusal only for non-computational; refusal before any
run/spend; no write into `design/`; M-D1 code unchanged; INFLIGHT guard 0.

### t5 — tables, figures, sizing contract, report section (Haiku) → t5r (Sonnet)

Files: `style.py`, `tables.py`, `figures.py`, `schemas/figure-style.yml` (new), `report.py`, `cli.py`
(`tables`, `figures`), tests `test_style.py`, `test_tables.py`, `test_figures.py`, `test_report_stats.py`.

RED → GREEN:
1. Tables: for a fixed synthetic `stats.json` the exact expected `summary.md`, `summary.tex`, `tests.md`,
   `tests.tex` text (formatting rules of 5.7, LaTeX escaping of `_ % & #`, `n/c (reason)` cells); the
   sidecars carry the right sha256s and the source `stats.json` sha256; byte-identical on re-run.
2. **(GAP acceptance "deterministic figure hashes")** `figures` run twice in two subprocesses with
   different `PYTHONHASHSEED` and `MPLCONFIGDIR` → identical sha256 for each of png, svg, pdf; the sidecar
   hashes equal the files'; the PNG has no `Software` text chunk, the SVG no `<dc:date>`, the PDF no
   `/CreationDate`.
3. Figure content: PNG pixel size = width_mm/25.4 × dpi (± 1 px) for single and double; panel count and
   order (read back from the SVG `<g id="axes_…">` groups); 10 metrics without `--metric` → exit 1; the
   single-numeric-parameter case draws a line; no CI → no error bar (no exception); **(review F3)** a
   `stats.json` in which one metric has n = 0 in every configuration → that panel shows "no values", the
   command exits 0 and the output is still byte-deterministic.
4. Style: defaults; a `--style-file` override; each out-of-range value, unknown key, symlinked file → exit
   1 naming the key; `style.py` constants equal `schemas/figure-style.yml` (pin test).
5. Staleness: after changing `results.json`, `tables` and `figures` exit 1 "run scieflow experiment stats
   first"; a symlinked `figures/` or `tables/` folder → exit 1.
6. Report: with a fresh `stats.json` the report has `## Statistics` with the summary table and family
   line; stale/missing → the one-line note; a hand-written `## Evaluation` section is still preserved;
   `tests/experiments/test_report.py` unchanged and green.

t5r checklist: render only from `stats.json` (no recomputation); determinism settings as 5.7; paths
inside the campaign folder; INFLIGHT guard 0.

### t6 — compare-campaigns, docs, skills, experiments rules 3 and 6 (Haiku) → t6r (Sonnet)

Before t6 the operator applies the protected edit (installer step `pe_t6`); t6 commits that file alone.

Files: `crosscompare.py` (new), `cli.py` (`compare-campaigns`), `docs/experiments/analysis.md` (new),
`docs/experiments/cli.md`, `docs/experiments/concepts.md`, `mkdocs.yml`, `skills/experiment-cycle/SKILL.md`,
`src/scieflow/experiments/skills/{evaluator,experiment-designer,experiment-runner}/SKILL.md`,
`src/scieflow/experiments/AGENTS.md` (commit of the applied edit), tests `test_crosscompare.py`,
`tests/test_docs_analysis.py`.

RED → GREEN:
1. `compare-campaigns` on two seeded campaigns sharing `ssim` → the best configuration per campaign in
   the metric's direction (a `mse`-only comparison picks the lowest mean), deterministic `--json`; the
   five refusals of 5.8 each exit 1; `--out` writes atomically, refuses an existing file without `--force`
   and a symlink always; **(review F3)** a campaign whose `stats.json` has n = 0 for a shared metric in
   every configuration → `best: null, reason: "no-values"` for that campaign, exit 0.
2. Docs test: `docs/experiments/analysis.md` exists, is in the `mkdocs.yml` nav, names every new
   subcommand and campaign/stage key, every supported test, the five corrections, the budget rule
   ("one replicate = one experiment run") and the pre-check, the `figures:` keys, the data-level note;
   `docs/experiments/cli.md` has sections for `stats`, `verify`, `tables`, `figures`,
   `compare-campaigns`; `concepts.md` shows `manifest.json`, `results.csv`, `stats.json`.
3. Skills test (same file): experiment-cycle no longer instructs recording local campaign runs again
   (the string `experiment_runs: +K` is gone, and the new text says sweeps record their own runs);
   the evaluator names `scieflow experiment stats` and says to re-run it after `scieflow experiment
   metrics`; the designer schema block shows `replicates` and
   `analysis`; the runner names `verify`.
4. `src/scieflow/experiments/AGENTS.md` contains rule 6's first line
   (`6. **Statistics come from code.**`) and equals the approved text of section 3 byte for byte in the
   edited region; `tests/test_docs_role.py` and `tests/test_docs_design.py` still pass.
5. `mkdocs build --strict` into `RUN/scratch/m-d2/site` if mkdocs is in the venv (else recorded "not
   available").

t6r checklist: docs ↔ code (every documented flag exists in `--help`); skill budget text correct; rule 6
byte-exact; no root `AGENTS.md`/`docs/runs.md` change; INFLIGHT guard 0.

### branch — whole-branch checks and PR body (Haiku)

On the clean committed tip: full default suite 0 failed / 0 errors and sealed probes 0 failed (absolute
counts, and the delta against t0 explained by added tests); `mkdocs build --strict` (or "not
available"); INFLIGHT guard over `base..tip`; linear history (no merge commits), at least one commit per
ticket t0..t6; `git diff base..tip -- tests/` removes no line (existing tests only gain lines);
`config/sandbox.yml`, sealed files, `events.py`, `src/scieflow/core/design/`, `envbuild.py`, `CREDITS.md`,
`VENDORED.md`, `CLAUDE.md`, root `AGENTS.md` and every other `AGENTS.md` except the t6 commit of
`src/scieflow/experiments/AGENTS.md` absent from the diff; `git diff base..tip -- pyproject.toml uv.lock`
empty. Writes `RUN/scratch/m-d2/pr-body.md` (plain English: the eight capabilities, decisions
M-D2-D1..D12, absolute counts, residuals R1–R7, the method references, "please merge with a merge
commit"; no credentials, prompts or patient data) and `reviews/m-d2-branch-report.md`. Any failure →
verdict `RED` with node ids.

### final — independent whole-branch review (Sonnet) → final_fix (Haiku)

Whole branch `base..tip`: spec (every Part A item; the GAP acceptance criteria — stats vs the scipy
reference, deterministic figure hashes, manifest fields present and hashed, replicate budget semantics
(D21) — and decisions M-D2-D1..D12; the protected edit byte-exact), code and security (section 5.12,
guards before scipy calls, SAP binding and refusal order, budget pre-check before any run, no host/user
details, fixed-argv subprocesses), evidence (re-run memory-capped: `tests/experiments tests/core/design
tests/test_docs_analysis.py`, confirm the branch result's absolute counts, re-run the INFLIGHT guard,
`pr-body.md` accurate). Output `reviews/m-d2-final-review-r<N>.md`. PASS → the installer's publish step;
CHANGES → final_fix (new commits, full suites re-run) → final; FAIL → escalate.

## 7. Pipeline (`pipelines/drafts/m-d2-run.yml`, linear)

```
t0        Haiku   worktree, plan/spec copy, green base   DONE→t1, BASE_RED→escalate
t1        Haiku   replicates, seeds, budget, configs     DONE→t1r
t1r       Sonnet  review                                 PASS→t2, CHANGES→t1f, FAIL→escalate (max 3)
t1f       Haiku   fix (new commits)                      DONE→t1r
t2/t2r/t2f        manifest, packages, verify, csv        same shape → t3
t3/t3r/t3f        stats core, analysis block, stats CLI  same shape → t4
t4/t4r/t4f        SAP binding and guard                  same shape → t5
t5/t5r/t5f        tables, figures, style, report         same shape → t6   (installer inserts pe_t6)
t6/t6r/t6f        compare-campaigns, docs, skills, rule  same shape → branch
branch    Haiku   full suites + PR body                  DONE→final, RED→escalate
final     Sonnet  whole-branch review                    PASS→publish, CHANGES→final_fix, FAIL→escalate (max 3)
final_fix Haiku   fix (new commits)                      DONE→final
(pe_t6, publish, refresh, refresh_review, chain: added by tools/milestone_chain.py install)
```

Caps `{max_cards: 75, bad_result_retries: 1, stall_minutes: 20, question_cards: 3}`. Results at
`pipelines/results/m-d2-run/<step>-r${round}.json`; every step requires `verdict, summary, head, base`;
implementation, fix, branch and final cards also report `inflight_guard_exit`. Inputs: handoff,
decisions, gap, `statsref` (`reviews/m-d2-stats-reference.json`), `statsgen`
(`reviews/m-d2-stats-reference.py`), `md1plan` (`reviews/m-d1-plan.md`); the installer adds `plan`.
Protected edits: `pipelines/drafts/m-d2-protected-edits.json`, one edit, `before_step: t6`.

## 8. Risks and residuals (stated, not hidden)

- **R1 Reference values are scipy 1.18.1 on this host.** The hand formulas confirm every statistic but the
  bootstrap pins; a different scipy may move the last digits (tolerance 1e-9) or the BCa interval. The
  test names the scipy version on failure; re-pinning is a deliberate, reviewed change. The `experiments`
  extra declares no scipy floor (scipy arrives through scikit-image; `uv.lock` pins 1.17.1/1.18.1), so an
  unlocked install could bring a scipy older than 1.15; `stats` then refuses with a clear message
  instead of failing inside `bootstrap(rng=…)` (adding an explicit floor is a dependency change, F6).
- **R2 File-based integrity.** `verify` detects changes relative to `results.json`, but an agent that
  rewrites outputs, manifests and `results.json` coherently is not caught (same class as M-D1 R2, M-B
  `data.yml`). A recompute through `scieflow experiment metrics` is such a coherent rewrite by design; it
  is made visible by `metrics_history`, the `metrics-recomputed` note and (inside a run) an event, not
  prevented. A host-side signed record is follow-up F2.
- **R3 Package lists are what `pip` reports.** Conda-only or system packages inside a container are not
  listed; a container without pip yields `not-captured`.
- **R4 Small-n tests.** With few replicates, rank tests cannot reach small p-values (documented with an
  example); the tool reports, it does not choose n for you (`scieflow experiment power`, M-D1, does).
- **R5 Unsupported SAP tests.** Model-based tests are `not-computed` until a later milestone adds a
  dependency (M-D2-D1); a clinical SAP that names them gets descriptives and honest "not computed" entries.
- **R6 Figure determinism is per environment.** Byte equality holds for the same matplotlib/freetype
  build; a different matplotlib version may render differently (the hashes in the sidecar make that
  visible).
- **R7 Guard reach.** Like the budget and design guards, the analysis guard applies only when
  `--experiments-dir` lies inside a run (M-D1 R3).

## 9. After M-D2

Queue (`continuity/milestones.yml`): M-F (`after: [m-c1, m-d2]`) uses `tables/`, `figures/` and the
`figures:` sizing block for manuscripts; M-D3 (`after: [m-d2]`) makes remote campaigns first-class with
the same manifests and the D21 array rule; M-G (`after: [m-d2, m-c2, m-f]`) resolves `[run:<id>]`
claims against manifests and `stats.json`. Follow-ups surfaced while planning: F1 a pointer in
`docs/runs.md` to the budget semantics once the paper-lifecycle work lands (M-D2-D10); F2 host-side
integrity for run records (R2); F3 the dataset package builder (`scieflow package build`) and RO-Crate
export (core M4/M-G); F4 Pareto fronts and a project-level metric tracker; F5 menu items and a
run-page statistics panel in the web app (D7: the CLI is complete); F6 statsmodels-based models (ANCOVA,
mixed, logistic, Cox) behind the same `stats.json` format once a dependency change is approved, which also
turns on M-D1's statsmodels power cross-check.
