# ScieFlow

Agent-driven research loop: computational experiments (via
[ExperimentX](vendors/ExperimentX)) hand in hand with literature research
(via [ResearchX](vendors/ResearchX)). Each iteration runs
hypothesis → experiment → literature grounding → synthesis, accumulating a
research notebook that can be handed to ResearchX's paper-draft workflow.

## Quick start

```bash
git clone --recurse-submodules git@github.com:jedimik/ScieFlow.git
cd ScieFlow && setup/install.sh
```

Then ask your agent (e.g. `claude`) to start a research run — it reads
`AGENTS.md` and follows `skills/research-loop/SKILL.md`. Choose the
approval mode per run: `per-campaign` (you approve every experiment
campaign) or `autonomous` (you approve the goal + scope + budget once).

## Layout

- `AGENTS.md` — coordinator contract (read this first)
- `skills/` — loop protocols (research-loop, experiment-cycle,
  literature-cycle, synthesis, notebook)
- `scripts/` — deterministic core (workspace init, dispatch, status,
  budget, validation, checkpoint)
- `config/` — agent registry (Fable-only in v1) + loop defaults
- `vendors/` — ExperimentX and ResearchX submodules
- `workspace/` — one folder per research run (gitignored; synced via DVC)

## Data & Workspace Storage (DVC + S3)

Experiment runs, agent plans, and validation outputs inside `workspace/` are managed via [DVC](https://dvc.org/) and backed by S3 (or S3-compatible storage like MinIO, Ceph, or Cloudflare R2), keeping large data out of Git while preserving versioned metadata.

### 1. Configure S3 Remote

Copy the template to `.env` and enter your bucket and credentials:

```bash
cp .env_template .env
# Edit .env with your credentials and S3 endpoint
```

`.env` is ignored by both Git and DVC to prevent credential leaks. Then run:

```bash
# Automatically loads DVC_S3_URL, AWS credentials, and endpoint from .env:
uv run scripts/dvc_setup_s3.py
```

You can also pass or override parameters via the CLI:
```bash
# Custom S3-compatible endpoint (e.g. CESNET):
uv run scripts/dvc_setup_s3.py \
  --url s3.cl4.du.cesnet.cz://dvc-projects/scieflow \
  --region eu-central-1
```



### 2. Track, Push, and Pull Workspaces

Use `scripts/dvc_sync.py` to manage run directories:

```bash
# Check tracking status of all workspace runs
uv run scripts/dvc_sync.py status

# Track a specific workspace run (or all with --all)
uv run scripts/dvc_sync.py track <run-slug>
git add workspace/<run-slug>.dvc .dvc/config
git commit -m "chore(dvc): track <run-slug>"

# Push workspace data to S3
uv run scripts/dvc_sync.py push <run-slug>   # or: uv run scripts/dvc_sync.py push --all

# Pull workspace data from S3 (on fresh clone / other workstation)
uv run scripts/dvc_sync.py pull <run-slug>   # or: uv run scripts/dvc_sync.py pull --all
```

See [`docs/DVC_STORAGE.md`](docs/DVC_STORAGE.md) for full configuration details.

## Development

```bash
uv run pytest -q        # offline test suite (stub agent, no LLM calls)
```

Design spec: `docs/superpowers/specs/2026-07-11-scieflow-design.md`.

