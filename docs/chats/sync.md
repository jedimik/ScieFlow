# Pausing: sync the project before you stop

Work that exists only on one machine is lost work. When a session pauses,
two things belong in storage: the run's **data** and the **agent chat** that
produced it.

## Say the word

Tell the agent any of: *pause now*, *sync with dvc*, *upload the agent chat*,
*upload/finalize the workspace*, *let's wrap up*, *we're done for today*. A
`UserPromptSubmit` hook (`scripts/hooks/dvc-sync-reminder.py`, registered in
`.claude/settings.json`) recognises those and reminds the agent to follow
`skills/workspace-sync/SKILL.md`. Ordinary prompts are untouched, and the hook
never uploads anything itself — it only starts the conversation.

## What the agent does

1. Works out which run it has been writing to.
2. Runs `uv run scieflow workspace sync-status <slug>` and reports what
   changed.
3. **Asks before uploading** when the numbers are large.
4. Pushes the run as a single zip: `uv run scripts/dvc_sync.py push <slug>`,
   then commits the pointer.
5. Gives you the chat command to run — gpg needs your passphrase, so that one
   is yours to type:
   `./scripts/chats-push.sh --tool claude --project <path> --yes`
6. Says what went where, and what was deliberately left out.

## What counts as "large"

`sync-status` excludes rebuildable directories (`scratch/`, `tmp/`,
`.snakemake/`, caches, conda prefixes), so the figures match what a push
really carries. The agent must stop and ask when any of these holds:

| Condition | Default |
|---|---|
| a single file | ≥ 1 GB (`--big-gb` changes it) |
| new files since the last sync | more than ~500 |
| total run size | over ~5 GB |
| never synced before | the first push sends everything |

```console
$ uv run scieflow workspace sync-status 2026-09-job1-posthoc-wta
2026-09-job1-posthoc-wta     never synced      3184 new /  3184 files    1.4G new /    1.4G
    big:     1.1G  containers/posthoc_analysis.sif
```

A `.sif` image, a conda prefix or a downloaded dataset is usually rebuildable:
move it into `scratch/` and it stays out of every future zip. That is a choice
the agent offers and you make.

Without a slug, `sync-status` reports every run.
