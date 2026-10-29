# CLI Reference

All commands accept `--help`. `run` and `sweep` require `--experiments-dir`
(normally `workspace/<slug>/experiments`); `--pipelines-dir` defaults to the
repo's `pipelines/`.

## scieflow experiment env build

```bash
scieflow experiment env build pipelines/<p>/stages/<s> [--force]
```

Generates `env/<s>.def` from the stage's environment spec and builds
`env/<s>.sif`. Skips the build if the sif exists; `--force` rebuilds.

## scieflow experiment run

```bash
scieflow experiment run -c <campaign.yaml> -p sigma=1.5 [-p k=v ...] [--seed N] [--direct]
```

One run with explicit parameters (run id `manual_<slug>`). `--seed N` sets
the seed of a seeded stage (`accepts_seed: true`). `--direct` bypasses the
container — development and tests only.

## scieflow experiment sweep

```bash
scieflow experiment sweep -c <campaign.yaml> [--direct]
```

Runs every grid combination / scenario, records each run, writes
`experiments/<name>/results.json`. Failed runs are recorded, not fatal.

With `replicates:` in the campaign, each configuration runs `n` times with
its seeds; run ids are `<config>_r<k>`, and each run records its replicate
and seed. The budget pre-check counts configurations × replicates and refuses
before any run starts. The sweep records its runs in the budget.

## scieflow experiment metrics

```bash
scieflow experiment metrics workspace/<slug>/experiments/<campaign>/runs/<run-id>
```

Recomputes metrics for one run from its `config.yaml`.

## scieflow experiment compare

```bash
scieflow experiment compare workspace/<slug>/experiments/<campaign> [--include-direct]
```

Ranks completed runs by `rank_by`, checks validation rules, writes
`comparison.json`, prints the ranking.

Runs recorded with `--direct` (development only) are **not ranked**: they
are listed as `DIRECT, NOT RANKED` and never become `best`. Pass
`--include-direct` to rank them too (development comparisons only). Runs
whose mode cannot be determined stay ranked, as before, and are listed
under `mode_unknown` in `comparison.json`.

## scieflow experiment report

```bash
scieflow experiment report workspace/<slug>/experiments/<campaign> [--include-direct]
```

Writes `report.md` and `plots/<rank_by>.png`. Single swept numeric
parameter → metric-vs-parameter line plot with the best run starred;
otherwise a per-run bar chart. Direct runs are counted and marked in the
report and excluded from the ranking unless `--include-direct` is given.

## scieflow experiment new

```bash
scieflow experiment new pipeline <name>
scieflow experiment new stage <name> --pipeline <p>
```

Scaffolds a working pipeline/stage (passthrough script) to edit.

## scieflow experiment power

```bash
scieflow experiment power <family> [--alpha 0.05] [--power 0.8] [--n N]
    [--effect-size D] [--alternative two-sided|larger] [--json] [--out FILE] [--force]
```

Sample size, power or effect size for a planned study. Statistics only: it reads
no data. Give every input you know and leave **exactly one** unknown; the
unknown is solved, and the assumptions and method are printed with the result.
Two or zero unknowns is a usage error (exit 2); a refused or unsafe input is
exit 1 and names the input.

Families and their effect input:

| Family | Effect input | Unknown `--n` means |
|---|---|---|
| `t-ind` | `--effect-size` d | first group (`--ratio` sets the second) |
| `t-paired` | `--effect-size` d | pairs |
| `t-one` | `--effect-size` d | subjects |
| `anova` | `--effect-size` f, `--groups` k ≥ 2 | total subjects |
| `proportions` | `--p1` and `--p2`, or `--effect-size` h | first group (`--ratio`) |
| `correlation` | `--r` | pairs |
| `survival` | `--hazard-ratio` (`--allocation`, default 0.5) | events (`--events`); with `--event-probability` the subject count |

Solve modes: the sample size (`--power` and the effect given), the power
(`--n` and the effect given), or the effect (`--n` and `--power` given; t-ind,
t-paired, t-one, anova and proportions only). `--alternative larger` gives a
one-sided test. `smaller` is refused: state the effect as a positive size and
use `larger`. ANOVA and survival are two-sided only.

Formulas follow the statsmodels power semantics, implemented over scipy
(statsmodels is not a dependency). A value that cannot be computed reliably is
refused, never reported as a power. Reference values and cross-checks are in
`tests/fixtures/power/reference.json`.

`--json` prints a deterministic document (`inputs`, `result`, `method`,
`assumptions`). `--out FILE` writes that document; an existing file needs
`--force`, and a symlink is never written.

    uv run scieflow experiment power t-ind --alpha 0.05 --power 0.8 --effect-size 0.5

## scieflow experiment stats

```bash
scieflow experiment stats workspace/<slug>/experiments/<campaign> [--include-direct] [--json]
```

Computes descriptives, confidence intervals, the campaign's tests, effect
sizes and corrected p-values, and writes `stats.json` (and re-writes
`results.csv`). Development (`--direct`) runs are excluded unless
`--include-direct` is given. `--json` prints the document. A refusal names
its rule (for example `no-baseline`, `sap-…`) and writes nothing. See
[Analysis and Statistics](analysis.md).

## scieflow experiment verify

```bash
scieflow experiment verify workspace/<slug>/experiments/<campaign> [--json]
```

Re-checks every recorded hash of the campaign's runs: manifests, artifacts
and metrics. Problems make it exit 1; notes do not. Run it after a sweep.
A hand edit of `metrics.json` is reported as `metrics-hash`.

## scieflow experiment tables

```bash
scieflow experiment tables workspace/<slug>/experiments/<campaign>
```

Writes `tables/summary.md` and `tables/tests.md` (with `.tex` versions)
from a fresh `stats.json`. Values that were not computed are shown as
`n/c`. A missing, stale or malformed `stats.json` is refused.

## scieflow experiment figures

```bash
scieflow experiment figures workspace/<slug>/experiments/<campaign> \
  [--metric M]... [--width single|double] [--style-file FILE]
```

Writes `figures/metrics.<ext>` and the sidecar `figures/metrics.json`, from a
fresh `stats.json`. `--metric` limits the metrics (repeatable); `--width`
selects the single or double column size; `--style-file` overrides the
`figures:` block of the figure style (`schemas/figure-style.yml`).

## scieflow experiment compare-campaigns

```bash
scieflow experiment compare-campaigns DIR DIR [DIR...] \
  [--metric M]... [--json] [--out PATH [--force]]
```

Reports the best configuration by mean for each campaign and each shared
metric, from each campaign's fresh `stats.json`. Nothing is written unless
`--out` is given. `--out` refuses a symlink, and an existing file unless
`--force`. No test runs across campaigns.
