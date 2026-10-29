# Analysis and Statistics

This page covers replicates, the run budget, run manifests, `results.csv`,
the statistics in `stats.json`, the locked-design binding, tables, figures
and comparisons across campaigns. Every number an agent reports comes from
these files. Nothing here is computed by hand.

## Replicates and seeds

A campaign may repeat each configuration with different seeds:

```yaml
replicates: 5                # n = 5, seed 0
# or
replicates: {n: 5, seed: 1000}
```

Each configuration gets `n` replicate runs with run ids `<config>_r<k>`.
Every replicate records its `replicate` and `seed` in `results.json` and
`results.csv`. Replicates need a stage that declares `accepts_seed: true` in
its `stage.yaml`; otherwise the campaign is refused before anything runs,
because replicates of a deterministic stage would be identical. A single
run can be seeded with `scieflow experiment run --seed N`.

## Budget

Each replicate is one run of the `experiment_runs` budget. In short:
one replicate = one experiment run. Before any run starts, `sweep` and `run` do a budget
pre-check of the planned runs (configurations × replicates) against the
remaining `experiment_runs`. A campaign that does not fit is refused, and
nothing runs or spends. A sweep records its runs in the budget as they
execute, so the coordinator does not record them a second time.

## Run manifest and `verify`

Each run directory gets a `manifest.json` with the sha256 of its artifacts
and metrics, the git state of the ScieFlow checkout, the captured package
list (`packages/<sif-sha12>.txt` for container runs), the run's
`data_level`, and `metrics_history`. `results.json` records the manifest's
sha256 for each run.

```bash
scieflow experiment verify workspace/<slug>/experiments/<campaign> [--json]
```

`verify` re-checks every recorded hash. A mismatch is a **problem** and
makes `verify` exit 1; an expected absence (for example, no package capture
for a direct run) is a **note**. Run `verify` after every sweep.

`scieflow experiment metrics <run-dir>` recomputes one run's metrics. The
recompute goes through the tool: it refreshes the run's manifest,
`results.json` and `results.csv`, appends to `metrics_history`, and makes
the campaign's `stats.json` stale. Run `scieflow experiment stats` again
after a recompute. A hand edit of `metrics.json` fails `verify` with the
rule `metrics-hash`.

## `results.csv`

One row per run and campaign metric. Columns:

`campaign, config_id, run_id, replicate, seed, mode, status, metric, value`,
then one `param.<name>` column per swept parameter. A missing value is an
empty cell and the row is kept, so a failed or unusable run stays visible.

## Statistics (`stats.json`)

```bash
scieflow experiment stats workspace/<slug>/experiments/<campaign> [--include-direct] [--json]
```

`stats` writes `stats.json` (strict JSON, sorted keys) and re-writes
`results.csv`. It excludes `--direct` runs unless `--include-direct` is
given, because development runs are not results. The document records
`source` (sha256 of `results.json` and `campaign.yaml`), `settings`,
`design`, `data_level`, `metrics`, `configs`, `tests`, `families`, `method`
(scipy and numpy versions and the assumptions of each test used) and
`warnings`. Tables, figures and `compare-campaigns` refuse a `stats.json`
whose `source` hashes no longer match the campaign.

### Descriptives and intervals

Per configuration and metric: `n`, mean, standard deviation (`sd`), median,
minimum and maximum, plus `n_missing` (no value recorded), `n_nonfinite` (NaN or
infinite values, excluded), `excluded` (each excluded run with its reason)
and `n_failed` per configuration. The confidence interval of the mean is a
BCa bootstrap (Efron 1987) at `ci` (default 0.95) with `bootstrap.resamples`
(default 9999, 1000 to 100000) and `bootstrap.seed` (default 0). An
interval with fewer than two values is not computed (`n<2`); a constant
sample is `degenerate`. A value that is not computed is stated with its
reason, never filled in.

### Tests

Set the test with `analysis.test` (one name for every metric, or a
`{metric: test}` mapping). Every name is a test of the design vocabulary
(`schemas/design.yml`); names the code does not compute are reported as
`unsupported`, not run.

| Test | Kind | Compares | Assumptions |
|---|---|---|---|
| `welch-t-test` | pairwise | each configuration with the baseline (Welch 1947) | independent runs; approximately normal metric |
| `t-test-ind` | pairwise | as Welch, pooled variance | independent runs; normal; equal variances |
| `mann-whitney` | pairwise | rank test against the baseline (Mann & Whitney 1947) | independent runs; same-shaped distributions under the null |
| `t-test-paired` | paired | replicates paired by seed with the baseline | paired seeds; normal paired differences |
| `wilcoxon-signed-rank` | paired | signed-rank test of paired differences (Wilcoxon 1945) | paired seeds; symmetric paired differences |
| `anova-oneway` | omnibus | all configurations at once | independent runs; normal; equal variances |
| `kruskal-wallis` | omnibus | rank omnibus across configurations (Kruskal & Wallis 1952) | independent runs; same-shaped distributions |
| `pearson` | correlation | linear association of a parameter and the metric | bivariate normal |
| `spearman` | correlation | monotone association (rank) | monotone association |
| `descriptive` | none | descriptives only | — |

Pairwise and paired tests need `analysis.baseline`: a configuration id, or a
parameter mapping that matches exactly one configuration. Without it the
campaign is refused (`no-baseline`).

Effect sizes accompany the pairwise and paired tests: Hedges' g (Hedges &
Olkin 1985) and Cliff's delta (Cliff 1993) where it is defined.

Small samples limit rank tests. The exact two-sided Wilcoxon signed-rank
p-value cannot go below 0.0625 with 5 pairs, so choose the number of
replicates for the test you intend.

### Corrections and families

`analysis.correction` is one of `none`, `bonferroni`, `holm` (default),
`hochberg` or `bh` (Benjamini & Hochberg 1995). Holm (1979) and Hochberg
(1988) are step-down and step-up procedures over the p-values of one family.
Tests are grouped into families: `confirmatory` (SAP outcomes of a locked
design), `exploratory` (other SAP-listed outcomes, corrected with `bh`) and
`campaign` (an unlocked campaign). `analysis.alpha` (default 0.05) sets the
rejection threshold per family. A test whose guard fails is not computed,
is not corrected, and is named with its reason.

### Guards and warnings

Every test checks its inputs before its statistic is called. Too few
values, zero variance, non-finite values and a missing baseline all end
in a named reason. Warnings in `stats.json` carry a rule and a metric:
`values-missing`, `values-nonfinite`, `design-<status>` and the SAP rules
below. Warnings name rules, keys and counts, never the metric values.

## Binding to a locked design

When the run holds a locked `design/design.yml`, the locked analysis plan
decides the tests: its outcomes, test, correction and alpha are used, and
`analysis.test` must not set a different test for an SAP outcome. A campaign
whose settings conflict with the SAP is refused before the sweep (`sap-…`
rules) and again by `stats`. A design that is not locked, or differs from
its lock, gives a `design-<status>` warning and the analysis is not
confirmatory. See [Study Design and SAP](design.md).

## Tables

```bash
scieflow experiment tables workspace/<slug>/experiments/<campaign>
```

Writes `tables/summary.md` and `tables/tests.md`, each with a `.tex`
version. Tables are rendered only from a fresh `stats.json`; a value that
was not computed is shown as `n/c` with its reason. A stale or malformed
`stats.json` is refused, naming the rule.

## Figures

```bash
scieflow experiment figures workspace/<slug>/experiments/<campaign> \
  [--metric M]... [--width single|double] [--style-file FILE]
```

Writes `figures/metrics.<ext>` for each configured format and
`figures/metrics.json`, the sidecar that records the stats sha256, the
width and the files. The figure style is the resolved default, overridden by
the top-level `figures:` block of a `--style-file` (the keys are in
`schemas/figure-style.yml`). The figure shows the configurations with their
confidence intervals per metric. Like the tables, it renders from a fresh
`stats.json` only.

## Comparing campaigns

```bash
scieflow experiment compare-campaigns DIR DIR [DIR...] \
  [--metric M]... [--json] [--out PATH [--force]]
```

For each campaign and each metric shared by every campaign (or the
`--metric` names, which must be shared), it reports the best configuration
by mean in the metric's direction, with its parameters, `n`, mean, interval,
design status and the sha256 of its `stats.json`. Ties go to the lower
configuration id. A campaign whose configurations have no values for a
metric is reported with `reason: no-values`; it is not an error. Every
input needs a fresh `stats.json`. Nothing is written unless `--out` is
given; `--out` refuses a symlink and an existing file unless `--force`.

No test runs across campaigns: their inputs and stages differ, and the
document says so.

## Data level

`manifest.json` and `stats.json` record `data_level` from the run's
declaration. The numbers of an L2 run are L2-derived. M-D2 never declares,
lowers or releases a level, and analysis is local. Sharing a result of an
L2 run goes through `scieflow data release`, unchanged. Messages name rules,
keys, metric names, configuration ids and relative paths, never metric
values or file contents.

## Not in this release

No regression, mixed-effects or survival models; no one-sided tests; no
test across campaigns; no discovery/confirmation split of data rows; no
dataset package builder, Pareto fronts or remote campaigns; no journal
profile.

## References

- Cliff, N. (1993). Dominance statistics: ordinal analyses to answer ordinal questions.
- Efron, B. (1987). Better bootstrap confidence intervals. *JASA*.
- Hedges, L. V. & Olkin, I. (1985). *Statistical Methods for Meta-Analysis*.
- Hochberg, Y. (1988). A sharper Bonferroni procedure for multiple tests of significance. *Biometrika*.
- Holm, S. (1979). A simple sequentially rejective multiple test procedure. *Scand. J. Stat.*
- Kruskal, W. H. & Wallis, W. A. (1952). Use of ranks in one-criterion variance analysis. *JASA*.
- Mann, H. B. & Whitney, D. R. (1947). On a test of whether one of two random variables is stochastically larger than the other. *Ann. Math. Stat.*
- Benjamini, Y. & Hochberg, Y. (1995). Controlling the false discovery rate. *JRSS B*.
- Welch, B. L. (1947). The generalization of "Student's" problem when several different population variances are involved. *Biometrika*.
- Wilcoxon, F. (1945). Individual comparisons by ranking methods. *Biometrics Bulletin*.
