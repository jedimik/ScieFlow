# Data Packages

A **data package** is how you hand your own results to ScieFlow research agents.
It gives every delivered artifact a stable id, so reports and drafts can
cite *your data* the same way they cite DOIs.

## What agents will and won't do with it

- Agents **read** your results, processing description, and scripts.
- Agents **never execute** your scripts and never write new analysis code.
- Agents may **build on** your results analytically — compare them to the
  literature, reinterpret them, derive hypotheses — but every number they
  repeat must exist in a delivered file (AGENTS.md hard rule 8).

## Layout

```
workspace/<slug>/inputs/
  manifest.yml          # the index (validated, schema below)
  processing.md         # your description of how results were produced
  results/              # tables, figures, stats — any format
  scripts/              # optional: your processing scripts (reference only)
  article/              # optional: your draft article (.md or .tex)
```

## The manifest

The coordinator writes `manifest.yml` during intake — you just deliver the
files and answer its questions about anything unclear:

```yaml
package: 2026-07-my-study
delivered: 2026-07-08
processing: processing.md
article: article/draft.md      # omit if no article
artifacts:
  - id: tbl-metrics            # cited in reports/drafts as [data:tbl-metrics]
    file: results/metrics.csv
    kind: table                # table | figure | stats | model-output | other
    description: Per-model accuracy and F1 on the held-out split
    produced_by: scripts/analyze.py
```

Validate manually anytime:

```bash
uv run scieflow research validate workspace/<slug>/inputs/manifest.yml --schema manifest
```

## Recommendations

- **One id per claimable thing.** If a CSV holds two unrelated result sets,
  split it or register it twice with distinct descriptions.
- **Write `processing.md` for a stranger.** Agents weave it into Methods
  sections; name the scripts, parameters, and data splits explicitly.
- **Deliver small, final artifacts** (summary tables, stats), not raw dumps
  — agents quote from these files, they don't recompute them.
- **No data?** `gap-discovery` still runs on literature + article alone;
  the report will say so. `paper-draft` requires a data package.
