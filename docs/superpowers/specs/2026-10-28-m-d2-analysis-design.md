# M-D2 analysis — design (Part A of docs/superpowers/plans/2026-10-28-m-d2-analysis.md)

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

