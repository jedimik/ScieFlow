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
scieflow experiment run -c <campaign.yaml> -p sigma=1.5 [-p k=v ...] [--direct]
```

One run with explicit parameters (run id `manual_<slug>`). `--direct`
bypasses the container — development and tests only.

## scieflow experiment sweep

```bash
scieflow experiment sweep -c <campaign.yaml> [--direct]
```

Runs every grid combination / scenario, records each run, writes
`experiments/<name>/results.json`. Failed runs are recorded, not fatal.

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
