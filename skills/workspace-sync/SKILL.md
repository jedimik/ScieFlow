---
name: workspace-sync
description: Hand the current project's work over to storage before a session stops — push the run's workspace data as a zip to DVC and back up this project's agent chat. Use when the user says pause now, sync with dvc, upload agent chat, upload/finalize the workspace, wrap up, or we're done for today; also before a long break or when a run reaches a checkpoint. Always reports what changed and asks before uploading anything large or numerous.
---

# Sync this project before stopping

Work that only exists on this machine is lost work. When the user pauses or
asks to sync, two things go to storage:

1. **The run's data** — `workspace/<slug>/` as one zip (`docs/DVC_STORAGE.md`).
2. **The agent chat** — this conversation, so it can be resumed on the other
   machine (`docs/chats/remote.md`).

Never upload either without the user's yes. Uploading is outward-facing and
can be expensive; this skill exists to make the decision informed, not automatic.

## 1. Know which run you are in

The run is the `workspace/<slug>/` you have been writing to. If you are not
sure, `uv run scieflow workspace list` shows every run with its state, and
`status.yml` in the run you touched names it. If your work touched no run
(you were editing module code), there is no workspace to push — say so and go
straight to step 4.

## 2. Look before you ask

```bash
uv run scieflow workspace sync-status <slug>
```

It reports, excluding rebuildable directories (`scratch/`, `tmp/`,
`.snakemake/`, caches, conda prefixes — what archives skip anyway):

- whether the run has ever been synced, and when;
- how many files are new since then, and how many bytes;
- every file of 1 GB or more, largest first;
- the run's total size and symlink count.

Add `--json` when you want to reason over it, `--big-gb 0.5` to lower the bar.

## 3. Report, then ask — especially when it is big

Tell the user in one short block: run, files new since last sync, total size,
and the big files by name. Then ask what to include. **Ask explicitly, do not
assume, when any of these is true:**

| Condition | Why it matters |
|---|---|
| any file ≥ 1 GB | one such file can dominate the upload |
| more than ~500 new files | usually generated output, not results |
| total over ~5 GB | slow upload, real storage cost |
| the run has never been synced | the first push sends everything |

Offer the concrete choices rather than a yes/no: push everything; push after
moving the big or generated files into `scratch/` (excluded from archives);
or skip the data this time and push only the chat. If something large looks
rebuildable — container images, `.sif` files, conda prefixes, test output,
downloaded datasets — say so and suggest `scratch/`, but let the user decide.

## 4. Push, one run at a time

```bash
uv run scripts/dvc_sync.py push <slug>          # single zip, archive mode
```

`push` takes named runs only and refuses per-file directory mode, so a run
cannot become tens of thousands of remote objects by accident. It prints the
`git add -A …` line for the pointer; run it, commit, and tell the user to push
the branch (or ask whether to push it for them).

If the zip build fails because a file is unreadable or the disk is short, report
it as it is — never retry with a narrower selection the user did not choose.

## 5. Back up this project's agent chat

```bash
./scripts/chats-push.sh --tool <agent> --project <project path> --yes
```

`<agent>` is the CLI you are running in (`claude`, `codex`, `agy`, `gemini`);
`<project path>` is the directory this session is working in, so only this
project's chats travel. The script bundles, encrypts and uploads.

**The user types the passphrase**, so this runs in their terminal, not yours:
gpg cannot prompt through a tool call. Give them the exact line and say why.
Add `--commit` if they want the pointer committed in the same step.

Before that, `--tool <agent> --project <path>` on its own (no `--yes`) shows
what would be included; the secret scan reports counts per chat. If it flags
something, name the counts and let the user decide before anything is uploaded.

## 6. Close the loop

Say plainly what went where: run pushed or not and why, pointer committed or
not, chat bundle uploaded or waiting on the user. If anything was deliberately
left out, name it, so nobody believes it is backed up when it is not.

## Rules that still bind

- AGENTS.md rule 1: run artifacts stay in `workspace/<slug>/`; `scratch/` is
  for rebuildable things; never rename or move a run.
- AGENTS.md rule 14: chat bundles are the user's. You never push one yourself
  and never transmit a bundle anywhere else.
- A refusal from `dvc_sync.py` (directory mode, no slug named) is a boundary,
  not an obstacle: report it.
