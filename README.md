# ScieFlow

Agent-driven research loop: computational experiments (the **experiments**
module) hand in hand with literature research (the **research** module).
Each iteration runs hypothesis → experiment → literature grounding →
synthesis, accumulating a research notebook that can be handed to the
research module's paper-draft workflow. Either module also works on its own.

## Quick start

```bash
git clone git@github.com:jedimik/ScieFlow.git
cd ScieFlow && setup/install.sh      # uv sync --all-extras + tool checks
setup/doctor.sh                       # environment check (--agents pings agent CLIs)
```

Then ask your agent (e.g. `claude`) to start a research run — it reads
`AGENTS.md` and follows `skills/research-loop/SKILL.md`. Choose the
approval mode per run: `per-campaign` (you approve every experiment
campaign) or `autonomous` (you approve the goal + scope + budget once).

## Layout

- `AGENTS.md` — coordinator contract (read this first)
- `skills/` — loop protocols (research-loop, experiment-cycle,
  literature-cycle, synthesis, notebook) plus the optional modules
  (remote-exec, claim-check)
- `src/scieflow/` — the `scieflow` package and CLI:
  - `core/` — agent dispatch (`scieflow agent run`), stub agent, config loader
  - `experiments/` — campaigns, sweeps, metrics, reports
    (`scieflow experiment`; own `AGENTS.md` + skills)
  - `research/` — literature search, review, gap discovery, paper drafting
    (`scieflow research`; own `AGENTS.md` + skills)
- `scripts/` — research-loop mechanics (workspace init, status, budget,
  validation, checkpoint, DVC sync); optional modules `scripts/remote/`,
  `scripts/nblm/`
- `pipelines/` — experiment pipelines (reference: `pipelines/denoise/`);
  `envs/` — conda environment for container builds
- `config/` — agent registry (tiered: claude/codex primary, agy support) +
  loop and research defaults, cached journal profiles; optional per-module configs you create from the shipped
  `*.example.yml` (`remotes.yml`, `notebooklm.yml`) and which stay
  gitignored
- `workspace/` — one folder per research run (gitignored; synced via DVC)

## Citation checking (optional, opt-in)

ScieFlow accepts a DOI as provenance for any non-experimental claim — but
nothing checks that the cited paper actually says the thing. The
**claim-check** module closes that gap: it pulls each citing sentence out of
a manuscript, downloads the open-access sources, uploads them to **your own
NotebookLM account**, and asks whether each sentence is supported — with a
verbatim quote as evidence.

It is off until you turn it on, and the agent must ask before using it.

```bash
uv sync --group notebooklm                       # optional dependency
cp config/notebooklm.example.yml config/notebooklm.yml   # then edit it
python -m notebooklm.notebooklm_cli login --storage ~/.research_hub/nlm_sessions/state.json
```

`config/notebooklm.yml` is yours and deny-by-default: it names the operations
the agent may perform, the hosts it may download from, the notebook-title
prefix it may touch, and hard ceilings on questions per day and per run
(NotebookLM's free tier allows roughly 50 chats a day). Anything not listed
is refused. The agent never logs in, never reads your session file, and never
deletes a notebook or a source.

**Consent.** Each run carries a `claim_check` setting (`config/defaults.yml`,
copied into `workspace/<slug>/config.yml`):

| Value | Behaviour |
|---|---|
| `never` | Never used, never offered. |
| `ask` *(default)* | The agent proposes an audit with its question cost and waits for your explicit yes. |
| `approved` | You pre-authorized audits for this run. Only you may set this. |

Verdicts are **advisory**: `supported`, `partial`, `not-addressed`,
`unsupported`, `contradicted`, or `unparseable`. They never fail a phase or
block a draft, and a positive verdict with no quote is automatically
downgraded — a verdict without evidence is not evidence.

```bash
# audit an existing paper (extract costs nothing and needs no account)
uv run scripts/nblm/nblm.py extract --manuscript manuscript/main.tex \
    --bib manuscript/references.bib --out workspace/<slug>/nblm/claims.yml
uv run scripts/nblm/nblm.py resolve default --workspace workspace/<slug> \
    --claims workspace/<slug>/nblm/claims.yml --out workspace/<slug>/nblm/sources.yml
uv run scripts/nblm/nblm.py fetch   default --workspace workspace/<slug> \
    --sources workspace/<slug>/nblm/sources.yml
uv run scripts/nblm/nblm.py upload  default --workspace workspace/<slug>
uv run scripts/nblm/nblm.py verify  default --workspace workspace/<slug> \
    --claims workspace/<slug>/nblm/claims.yml
# or check one sentence inline
uv run scripts/nblm/nblm.py verify default --workspace workspace/<slug> \
    --claim "<sentence>" --doi 10.1234/abc
```

`resolve` looks each DOI up in Europe PMC; expect partial coverage (64% on a
59-source neuroimaging bibliography — paywalled publishers often have no
open-access full text at all). Every DOI it cannot reach is listed with a
reason and simply not audited.

The report lands at `workspace/<slug>/nblm/citation-audit.md`, worst verdicts
first. Protocol: [`skills/claim-check/SKILL.md`](skills/claim-check/SKILL.md).
NotebookLM has no official public API; this uses the unofficial
[`notebooklm-py`](https://github.com/teng-lin/notebooklm-py) client, so it can
break when Google changes things — all of it is contained in
`scripts/nblm/session.py`.

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
uv sync --all-extras --all-groups
uv run pytest -q                  # offline test suite (stub agent, no LLM calls)
uv run pytest -q -m slow          # container-build tests (needs apptainer)
uv run --group docs mkdocs serve  # documentation site
```

Coming from the former standalone experiment or research repositories? See
[`docs/MIGRATION.md`](docs/MIGRATION.md).

Design spec: `docs/superpowers/specs/2026-07-11-scieflow-design.md`.

