# workspace/

One folder per run, `workspace/<slug>/`. Run folders are local data: gitignored,
synced only as zips (`docs/DVC_STORAGE.md`). See what is here with
`uv run scieflow workspace list`; check one run with
`uv run scieflow workspace doctor <slug>`.

## Naming

- `YYYY-MM-<kebab-topic>`, e.g. `2026-09-job1-posthoc-wta`.
- **Never rename or move a run.** Old agent chats cite its path, and many runs
  contain absolute links into themselves.
- A leading `_` means *not a run*: `_archives/` (zips for DVC), `_misc/`
  (notes, moved non-runs, duplicate copies).
- `news/` and `chats/` belong to their modules.
- A symlinked slug is an **alias**: it keeps an old path working and points at
  the real run (e.g. `…-research` → the plain slug).

## Two kinds of run

Research loop (`scripts/sfx_init.py`; `status.yml` has `run:`):

```
<slug>/
  goal.md  config.yml  status.yml  budget.yml  notebook.md
  iterations/<n>/{hypothesis,results-summary,literature,synthesis}.md
  experiments/<campaign>/{campaign.yaml,results.json,runs/…}
  remote/            metacentrum jobs (optional)
  logs/              prompts and transcripts only
  tools/             scripts the run writes
  scratch/           tests, pytest temp, envs, clones, caches (not archived)
```

Research module (`status.yml` has `workflow:` — lit-review, gap-discovery,
paper-review, paper-draft):

```
<slug>/
  brief.md  config.yml  status.yml  log.md
  prompts/  logs/
  findings/ gaps/ debate/ outline/ manuscript/ review/ report/   (per workflow)
```

## What archives leave out

Rebuildable directories are skipped when a run is zipped: `scratch/`, `tmp/`,
`pytest-*`, `.snakemake/`, `__pycache__/`, `.cache/`, `.uv-cache/`,
`mpl-cache/`, `.venv/` and conda prefixes under `runtime/host/`. Symlinks are
stored as links, never followed, so `Data -> /mnt/d/Data` stays a pointer.

## Related runs

Mark retries of one campaign with a shared `lineage:` in each run's
`config.yml` (e.g. `lineage: job1`); `scieflow workspace index` groups them in
the generated `INDEX.md`.
