# News

**The ScieFlow news module keeps you current on the tools and topics you care about.** You
declare your interests (e.g. `Snakemake`, `DuckDB`) in a YAML file; each run
delegates the research to an AI agent CLI already installed on your machine
(`claude`, `codex`, or `agy`), which searches the web for what changed since
your last check. Results are stored in an embedded database and presented as
structured markdown reports — News, Updates, New Use Cases, Fixes,
Improvements, a 🔥 Highlight when something big happened, and (when you
describe how you use a tool) a **Gaps & Blind Spots** section pointing out
adjacent things you may not have considered.

Everything runs locally. No accounts, no server, no API keys of its own —
the agent CLIs bring their own authentication.

## How it works

1. `config/news.yml` lists your interests, each with optional context and
   source hints (GitHub repo, URLs, keywords).
2. Each run processes interests one by one: a research prompt is sent to the
   agent CLI, which does the web searching and returns a structured markdown
   block. Every claim must carry a date and a source link.
3. Per-interest state (`last checked`) is stored in an embedded TinyDB
   database, so the next run only reports what's new since the last one.
   The first check uses a configurable lookback window.
4. Reports live in the database; markdown files are generated on demand
   (`scieflow news export` or the GUI's download button).

## Requirements

- ScieFlow installed with the news extra: `uv sync --extra news`
  (add `--extra news-gui` for the web GUI; `setup/install.sh` installs all extras)
- At least one agent CLI installed **and logged in**: `claude`, `codex`, or `agy`

The `claude` adapter passes `--allowedTools WebSearch WebFetch` so web
research works non-interactively without any other tool permissions; `codex`
and `agy` allow web search by default. These command lines are the news
module's own and deliberately narrower than the research-loop registry.

Data locations, all inside the ScieFlow repo:

- interests: `config/news.yml` (override with `--config PATH`)
- database: `workspace/news/news.json` (override with `--db PATH` or
  `SCIEFLOW_NEWS_DB`)
- exported reports: `workspace/news/reports/` (override with `-o DIR`)

## Using the CLI

```bash
uv run scieflow news init            # write an example config/news.yml — then edit it
uv run scieflow news run             # research all interests, print the report
uv run scieflow news status          # interests + last-checked dates
uv run scieflow news export --latest # write workspace/news/reports/YYYY-MM-DD-news.md
uv run scieflow news models [--refresh]  # list cached models per agent; --refresh queries the agent CLI
```

Useful flags for `run`:

- `--interest Snakemake` — run a subset (repeatable)
- `--group biology` — run a named group from config (repeatable)
- `--agent codex` — override the config's agent for this run
- `--model claude-sonnet-5` — override the config's model for this run
- `--reasoning high` — reasoning effort: low | medium | high (supported by claude and codex)
- `--since 2026-07-01` or `--days 14` — one-off window override
- Exit code is `1` when **every** interest failed (useful for cron alerts).

## Research templates

Each interest is researched against a template that shapes its report
sections and priority sources. `scieflow news templates` lists them:

| key        | audience                    | sections |
|------------|------------------------------|----------|
| `tool`     | software tool or library     | News, Updates, New Use Cases, Fixes, Improvements |
| `science`  | scientific/research topic    | New Papers & Preprints, Key Findings, Methods & Datasets, Reviews & Perspectives, Community & Events |
| `coding`   | developer library/language   | Releases, Breaking Changes & Migrations, Security (CVEs), Tooling & Ecosystem, Best Practices & Patterns |
| `platform` | technology platform/service  | Platform Updates, API & Breaking Changes, Security & Advisories, Pricing & Limits, Ecosystem |
| `keyword`  | broad topic/keyword watch    | Developments, Notable Publications & Posts, Community Pulse, Key Players & Moves |

```bash
uv run scieflow news templates   # list all templates with descriptions and sections
```

Set `template:` on an interest in `config/news.yml` (or via the GUI's
Interests editor) to pick one; it defaults to `tool` when omitted.

## Using the GUI

```bash
uv run scieflow news gui             # needs the news-gui extra; opens http://127.0.0.1:8080
```

- **Interests** — add, edit, and delete interests; manage groups (create,
  edit, delete); changes are written back to `config/news.yml`, so CLI and GUI
  always agree. (YAML comments are not preserved when the GUI rewrites the file.)
- **Run** — select a group or individual interests, pick the agent, optionally
  override the model and reasoning settings, and start a run. Refresh available
  models from the agent CLI. The queue progresses live, one interest at a time.
- **Reports** — browse past runs, filter by interest, read rendered markdown,
  edit a result (the original agent output is always kept), download individual
  reports, and view failure details including raw agent output when available.
  A search box above the run history searches every stored report's text
  across all runs and interests, and jumps straight to a matching one.

The GUI binds to localhost only. To reach it from another machine, use an
SSH tunnel: `ssh -L 8080:localhost:8080 yourhost`.

## Config reference (`config/news.yml`)

```yaml
agent: claude          # default agent: claude | codex | agy
model: claude-sonnet-5  # optional, overrides agent's default
reasoning: high        # optional reasoning effort: low | medium | high
lookback_days: 30      # window for interests never checked before
# timeout: 900         # optional — overrides the per-template default (science 900s, most others 300s)

interests:
  - name: Snakemake                       # required, unique
    context: >                            # optional — enables Gaps & Blind Spots
      HPC pipelines with SLURM, containerized rules
    repo: snakemake/snakemake             # optional GitHub hint
    urls:                                 # optional extra sources
      - https://snakemake.readthedocs.io
    keywords: [workflow, bioinformatics]  # optional disambiguation
    template: tool                        # optional: tool | science | coding | platform | keyword (default tool)
    lookback_days: 60                     # optional — overrides the global lookback for this interest's first run

groups:                                    # optional, GUI-managed (max 3 levels)
  - name: biology
    interests: [Snakemake, DuckDB]       # curated subset
  - name: infrastructure
    interests: [Rust, Docker]
```

Unknown keys are rejected with a clear error, so typos can't silently
disable settings.

Research templates carry their own default agent timeout (science 900s,
keyword 600s, others 300s); set `timeout:` to override it for all interests.

## Choosing the agent

`agent`, `model`, `reasoning` and `timeout` at the top of `config/news.yml`
pick who researches your interests. Change them with the shared agent
configuration command, which validates the file and keeps its comments:

```bash
uv run scieflow agent show --news
uv run scieflow agent configure --news          # interactive questions
uv run scieflow agent configure --news --set agent=codex --set reasoning=high --yes
```

Model suggestions for `claude` and `codex` come from each agent's `menu.models`
in `config/agents.yml`, so model ids are maintained in one place.

## Automating with cron

```cron
# Every Monday at 08:00: research everything, export the report
0 8 * * 1  cd /path/to/ScieFlow && uv run scieflow news run && uv run scieflow news export --latest
```

A fully failed run (agent logged out, no network) exits nonzero and skips
the export, so stale reports are never silently produced.

## Development

```bash
uv run pytest -q tests/news          # full suite (live agent test excluded)
uv run pytest -q -m live tests/news  # smoke test against a real installed agent CLI
```

Future directions: [Future ideas](future-ideas.md).
