# ScieFlow

Agent-driven research loop: computational experiments (the **experiments**
module) hand in hand with literature research (the **research** module).
Each iteration runs hypothesis → experiment → literature grounding →
synthesis, accumulating a research notebook that can be handed to the
research module's paper-draft workflow. Either module also works on its own.
A third module, **news**, keeps you current on the tools and topics you follow,
and a fourth, **chats**, backs up your agent conversations and restores them
on another machine.

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
  - `news/` — what changed in your tools and topics (`scieflow news`, optional
    web GUI; own `AGENTS.md`)
  - `chats/` — selective backup and cross-machine restore of agent chats,
    skills and plugins (`scieflow chats`; own `AGENTS.md`)
- `scripts/` — research-loop mechanics (workspace init, status, budget,
  validation, checkpoint, DVC sync); optional modules `scripts/remote/`,
  `scripts/nblm/`
- `pipelines/` — experiment pipelines (reference: `pipelines/denoise/`);
  `envs/` — conda environment for container builds
- `config/` — agent registry (tiered: claude/codex primary, agy support),
  role assignments and loop/research defaults, news interests (`news.yml`),
  cached journal profiles; optional per-module configs you create from the shipped
  `*.example.yml` (`remotes.yml`, `notebooklm.yml`, `chats.yml`) and which stay
  gitignored; `.dvc/config` is gitignored too (machine-local remote)
- `workspace/` — one folder per research run, plus `workspace/news/`
  (gitignored; synced via DVC)

## Agents: who does what

Each role (`loop.experiment`, `research.reviewer`, …) is assigned an agent in
`config/defaults.yml`; a run can override roles and per-agent model, reasoning
and timeout in its own `config.yml`. Unset values inherit.

```bash
uv run scieflow agent show --workspace <slug>     # effective config, value by value
uv run scieflow agent configure                   # interactive: defaults, a workspace, or news
uv run scieflow agent configure --workspace <slug> --assign loop.experiment=codex --yes
```

Changes are validated (tier routing, disabled agents), shown as a diff, and
keep your YAML comments. Guide: [`docs/agents.md`](docs/agents.md).

## News

```bash
uv run scieflow news status                  # interests from config/news.yml
uv run scieflow news run --interest Snakemake
uv run scieflow news export --latest         # → workspace/news/reports/
uv run scieflow news gui                     # local web GUI (extra: news-gui)
```

Guide: [`docs/news/index.md`](docs/news/index.md).

## Chat backups (optional, user-invoked)

Agent chats live in per-tool HOME directories keyed by absolute path, so they
neither survive a machine move nor travel selectively. `scieflow chats` picks
the conversations you want across Claude Code, Codex CLI, Antigravity (`agy`)
and Gemini CLI, works out which skills and plugins those chats used, and packs
them into one encrypted bundle that restores on another PC with the paths
rewritten.

```bash
uv run scieflow chats init                   # config/chats.yml (deny-by-default)
uv run scieflow chats scan                   # read-only inventory
uv run scieflow chats backup                 # pick chats → encrypted bundle
uv run scieflow chats inspect BUNDLE         # manifest only, restores nothing
uv run scieflow chats restore BUNDLE --map /home/you=/home/me   # dry run; --apply to write
```

Credential files are never bundled, and nothing is transmitted on its own —
moving a bundle between machines is your action. Optionally, the repo's DVC
storage can carry encrypted bundles (`scieflow chats push` / `pull`, off until
you enable `remote` in `config/chats.yml`; bundle files only, never a
workspace).
Guide: [`docs/chats/index.md`](docs/chats/index.md).

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

Coming from the former standalone experiment, research or news repositories? See
[`docs/MIGRATION.md`](docs/MIGRATION.md).

Design spec: `docs/superpowers/specs/2026-07-11-scieflow-design.md`.

