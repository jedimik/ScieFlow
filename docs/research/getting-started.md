# Getting Started

## Prerequisites

| Tool | Purpose | Install |
| --- | --- | --- |
| [uv](https://docs.astral.sh/uv/) | Python environment & deps | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `claude` | Claude Code CLI (agent) | `npm install -g @anthropic-ai/claude-code` |
| `codex` | OpenAI Codex CLI (agent) | `npm install -g @openai/codex` |
| `agy` | Antigravity / Gemini CLI (agent) | see [antigravity.google](https://antigravity.google) |
| `zot` | Zotero CLI (reference export) | see the zot-cli project README, then `zot config init` |
| `latexmk` *(optional)* | LaTeX builds for the writing phase | `sudo apt-get install latexmk texlive-latex-extra biber` |

You need **at least one** agent CLI logged in for anything to work, and at
least **two** for cross-review to happen. Each CLI handles its own
authentication (`claude` login on first run, `codex login`, `agy install`).

!!! note "Fewer than three agents?"
    That's fine. Disable missing agents in `config/agents.yml`
    (`enabled: false`) or pick a subset per run in the workspace's
    `config.yml`. With a single agent the pipeline still runs; the report is
    marked *single-agent, unreviewed*.

## Install

```bash
git clone https://github.com/jedimik/ScieFlow
cd ScieFlow
setup/install.sh
```

`install.sh` is idempotent: it runs `uv sync` and prints the exact install
command for anything missing. It never installs agent CLIs behind your back.

## Verify with doctor

```bash
setup/doctor.sh            # environment checks (free)
setup/doctor.sh --agents   # + live headless ping of each agent CLI (costs a few tokens)
```

Doctor checks: binaries, Python deps, the framework's own test suite, Zotero
reachability, all four scholarly APIs, and (if `latexmk` exists) a trivial
LaTeX compile. Exit code 0 means healthy; a missing `latexmk` is only a
warning.

Expected output on a healthy machine:

```text
Binaries:
  ✓ uv
  ✓ claude
  ✓ codex
  ✓ agy
  ✓ zot
  ⚠ latexmk missing — only needed for LaTeX builds: ...
Python deps:
  ✓ importable
Framework self-test:
  ✓ test suite passes
Zotero:
  ✓ zot reachable
Scholarly APIs:
  ✓ openalex
  ✓ crossref
  ✓ europepmc
  ✓ arxiv

All checks passed.
```

## First run

Open any agent CLI at the repo root — it reads `CLAUDE.md` / `GEMINI.md` /
`AGENTS.md` automatically — and ask for what you want in plain language:

=== "Literature review"

    ```text
    Do a literature overview on <topic>.
    Focus on <subquestions>, from <year> onwards, max <N> papers.
    Save citations to my Zotero group <id>.
    ```

=== "Paper review"

    ```text
    Review my manuscript in ~/papers/draft/ as a reviewer for <journal>.
    Run up to 3 revision rounds.
    ```

=== "Journal profile only"

    ```text
    I'm aiming for Nature Methods — how does it accept papers?
    ```

The coordinator creates `workspace/<YYYY-MM-topic-slug>/`, and everything the
run produces — prompts, findings, reviews, reports, logs — lives there. See
[Examples](examples.md) for what the results look like.

## Running the test suite

```bash
uv run pytest -q
```

All tests are offline: HTTP is mocked, agents are exercised through a
zero-token stub, and `zot` is faked on `PATH`. If you change any script, this
must stay green.

## Setting up on a new machine

1. Install the prerequisites above and log in to each agent CLI.
2. `setup/install.sh && setup/doctor.sh`
3. Optionally set `SCIEFLOW_MAILTO=you@example.com` in your environment —
   it's appended to the User-Agent for polite scholarly-API access.
4. Run `setup/doctor.sh --agents` once to confirm all agent CLIs respond
   headless.
