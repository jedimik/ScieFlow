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
git add workspace/*.dvc .dvc/config
git commit -m "chore(dvc): track workspace run data"
```

### 3.3 Push Data to S3
Sync your tracked workspace files to the S3 bucket:
```bash
# Push a specific run
uv run scripts/dvc_sync.py push 2026-09-segsnake-paper1-technical-reproducibility

# Push all tracked runs
uv run scripts/dvc_sync.py push --all
```

### 3.4 Pull Data from S3 (e.g. on another machine or fresh clone)
On a fresh clone or another workstation:
```bash
# Pull a specific run
uv run scripts/dvc_sync.py pull 2026-09-segsnake-paper1-technical-reproducibility

# Pull all tracked runs
uv run scripts/dvc_sync.py pull --all
```

---

## 4. Cache & Ignore Policies

- `.gitignore` ignores all raw contents inside `workspace/*`, while explicitly allowing `.dvc` files and DVC-generated ignore files.
- `.dvcignore` ignores non-essential cache and temporary files (`__pycache__`, `.pytest_cache`, `*.pyc`, `*.tmp`, `*.sif`, `.venv`, `.superpowers`), ensuring DVC does not waste S3 bandwidth or storage on cache.
