# ScieFlow DVC & S3 Storage Manual

This repository uses [Data Version Control (DVC)](https://dvc.org/) to track and synchronize experiment runs, agent plans, logs, and artifacts in `workspace/` without committing large files or sensitive data directly to Git.

Git tracks code and `.dvc` pointer files; DVC tracks and transfers large datasets and workspace outputs to/from S3.

---

## 1. Prerequisites

Ensure `dvc` is available:
```bash
dvc version
```
*(DVC is installed with S3 support via s3fs on this machine).*

---

## 2. Configure Your S3 Remote

### Option A: Using `.env` and Helper Script (Recommended)

1. Copy `.env_template` to `.env`:
   ```bash
   cp .env_template .env
   ```
2. Fill in your S3 parameters and credentials in `.env`:
   ```ini
   DVC_S3_URL=s3.cl4.du.cesnet.cz://dvc-projects/scieflow
   DVC_S3_ENDPOINT_URL=https://s3.cl4.du.cesnet.cz
   DVC_S3_REGION=eu-central-1
   AWS_ACCESS_KEY_ID=your_access_key
   AWS_SECRET_ACCESS_KEY=your_secret_key
   ```
3. Run the setup script without arguments (it automatically reads from `.env` and places credentials in `.dvc/config.local`):
   ```bash
   uv run scripts/dvc_setup_s3.py
   ```

*(Note: `.env` and `.dvc/config.local` are both ignored by Git and DVC).*

### Option B: CLI Flags Overrides

```bash
# Custom S3 endpoint (e.g. CESNET) with CLI arguments:
uv run scripts/dvc_setup_s3.py \
  --url s3.cl4.du.cesnet.cz://dvc-projects/scieflow \
  --region eu-central-1 \
  --access-key-id <YOUR_KEY> \
  --secret-access-key <YOUR_SECRET>
```


### Option B: Native DVC Commands

```bash
# 1. Set remote URL
dvc config remote.s3remote.url s3://my-bucket/scieflow-workspace

# 2. Set default remote
dvc config core.remote s3remote

# 3. (Optional) Custom endpoint / region
dvc config remote.s3remote.endpointurl https://s3.example.com
dvc config remote.s3remote.region eu-central-1

# 4. (Optional) Local credentials (kept in .dvc/config.local, never committed to git)
dvc config --local remote.s3remote.access_key_id <KEY>
dvc config --local remote.s3remote.secret_access_key <SECRET>
```

Alternatively, standard AWS environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`) or `~/.aws/credentials` profiles (`--profile`) are recognized automatically.

---

## 3. Workflow & Usage

We provide `scripts/dvc_sync.py` to simplify tracking, pushing, and pulling workspaces.

### 3.1 Check Status
See which workspace runs exist locally and whether they are tracked by DVC:
```bash
uv run scripts/dvc_sync.py status
```

### 3.2 Track Workspace Run(s)
When a workspace run is ready to be tracked with DVC:
```bash
# Track a specific run
uv run scripts/dvc_sync.py track 2026-09-segsnake-paper1-technical-reproducibility

# Or track all existing runs in workspace/
uv run scripts/dvc_sync.py track --all
```
This runs `dvc add workspace/<slug>`, creating `workspace/<slug>.dvc`.
Then commit the `.dvc` file to Git:
```bash
git add workspace/*.dvc
git commit -m "chore(dvc): track workspace run data"
```
`.dvc/config` is gitignored: the remote URL and endpoint are machine-local, so
they do not follow a branch into every checkout. Recreate it on a new machine
with `dvc remote add` (see §2), and keep credentials in `.dvc/config.local`.

### 3.3 Push Data to S3

**Name the runs you mean.** `push` and `pull` have no `--all`: moving hundreds
of gigabytes should never be one flag away. `--all` remains on `track`.

```bash
uv run scripts/dvc_sync.py push 2026-09-segsnake-paper1-technical-reproducibility
```

Runs go up as a **single zip** (archive mode, §3.5) — that is the default.
Per-file directory tracking turns one run into tens of thousands of S3
objects, so it is never implicit: a run that would use it is refused with a
message telling you to pass `--archive` or, if you really want per-file
tracking, `--no-archive`.

### 3.4 Pull Data from S3 (e.g. on another machine or fresh clone)
On a fresh clone or another workstation:
```bash
uv run scripts/dvc_sync.py pull 2026-09-segsnake-paper1-technical-reproducibility
```

### 3.5 Archive Mode (single zip per workspace)

Large finished runs with many small files transfer much faster as one S3
object. Archive mode packs `workspace/<slug>` into
`workspace/_archives/<slug>.zip` (uncompressed, Zip64) and tracks that zip
instead of the directory.

**Trade-off.** DVC deduplicates per file. An archive is one blob, so changing
any file re-uploads the whole run. Use archive mode for finished runs, not
runs you are still iterating on.

**This is the default** (`archive: true` in `config/defaults.yml`). Opt *out*
per run, and then say so on every push:
```bash
uv run scripts/dvc_sync.py push 2026-09-job1-posthoc-wta --no-archive
```
```yaml
# workspace/<slug>/config.yml
archive: false   # keep DVC's per-file dedup for this run
```
After the first archive push, the pointer `workspace/_archives/<slug>.zip.dvc`
decides the mode on its own; later `push` and `pull` need no flag.

**Push** checks free disk (the workspace size + 5%), builds the zip, uploads it
with `dvc add --to-remote` (no local cache copy), deletes the local zip
(`--keep-zip` keeps it), and replaces any old `workspace/<slug>.dvc` pointer
with `dvc remove`. Old per-file data in S3 is left untouched. The command
prints the `git add -A …` line to run; it never commits.

**Pull** downloads the zip, verifies it, extracts it into `workspace/<slug>`,
and keeps the zip, hardlinked to its DVC cache object so it takes no extra
disk. If `workspace/<slug>` already has content, pull refuses; rerun with
`--force` to replace it.

**Back to directory mode:** `push <slug> --no-archive`.

**Limits.** Building an archive needs free disk equal to the workspace size.
Symlinks inside a workspace are refused, not followed.

---

## 4. Cache & Ignore Policies

- `.gitignore` ignores all raw contents inside `workspace/*`, while explicitly allowing `.dvc` files and DVC-generated ignore files.
- `.dvcignore` ignores non-essential cache and temporary files (`__pycache__`, `.pytest_cache`, `*.pyc`, `*.tmp`, `*.sif`, `.venv`, `.superpowers`), ensuring DVC does not waste S3 bandwidth or storage on cache.


## 5. Chat bundles (separate, optional)

`scieflow chats push` / `pull` can move encrypted chat bundles through the same
remote. It is a different thing from workspace sync: one bundle file at a time,
never a directory, off until `remote.enabled` is set in `config/chats.yml`.
See [chats/remote.md](chats/remote.md).
