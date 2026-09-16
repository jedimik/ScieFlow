# Agent configuration

ScieFlow dispatches agent CLIs (`claude`, `codex`, `agy`) for roles such as
running an experiment campaign or reviewing a manuscript. Who does what is
configured in layers; each layer only states what differs from the one below.

| Layer | File | Holds |
|---|---|---|
| Registry | `config/agents.yml` | per agent: `cmd`, `model`, `reasoning`, `timeout_min`, `tier`, `enabled`, `menu` |
| Defaults | `config/defaults.yml` → `assignments:` | which agent performs each role |
| Run | `workspace/<slug>/config.yml` → `assignments:`, `agent_overrides:` | this run's differences |
| News | `config/news.yml` → `agent`, `model`, `reasoning`, `timeout` | the news module's own settings |

## Roles

| Role | Takes | Support tier allowed |
|---|---|---|
| `loop.experiment` | one agent | no |
| `loop.literature` | one agent | no |
| `loop.paper-draft` | one agent | no |
| `research.search` | list | yes, alongside a primary |
| `research.cross-review` | list | no |
| `research.gap-analysis` | list | no |
| `research.debate` | list | no |
| `research.journal-profile` | list | yes, alongside a primary |
| `research.reviewer` | one agent | no |
| `research.submitter` | one agent (differs from reviewer) | no |
| `research.outline` | one agent | no |
| `research.draft-authors` | list | no |
| `research.consistency` | one agent | no |

## See what is in effect

```bash
uv run scieflow agent show                       # defaults
uv run scieflow agent show --workspace <slug>    # a run: every value marked default or workspace
uv run scieflow agent show --news                # news module
uv run scieflow agent show --workspace <slug> --json   # for coordinator agents
```

`show` exits with status 1 when the configuration has a problem: an unknown or
disabled agent, a support-tier agent in a primary-only role or without a
primary alongside it, or the same agent as reviewer and submitter. Warnings,
for example a reasoning value the agent's `cmd` never uses, are shown but do
not fail.

## Change it

Interactive — answer the questions, review the diff, confirm:

```bash
uv run scieflow agent configure                      # asks: default, workspace or news
uv run scieflow agent configure --workspace <slug>
uv run scieflow agent configure --news
```

Non-interactive — what a coordinator agent runs after asking you in chat:

```bash
# this run only: codex runs experiments with high reasoning
uv run scieflow agent configure --workspace <slug> \
    --assign loop.experiment=codex --set codex.reasoning=high --yes

# back to the default for this run
uv run scieflow agent configure --workspace <slug> --unset loop.experiment --unset codex.reasoning --yes

# change the defaults for every run that does not override them
uv run scieflow agent configure --assign research.reviewer=claude --assign research.submitter=codex --yes
uv run scieflow agent configure --set claude.model=claude-opus-5 --yes

# news module
uv run scieflow agent configure --news --set model=claude-sonnet-5 --set timeout=900 --yes
```

Every change is validated against the resulting configuration and shown as a
diff before it is written; without `--yes` a non-interactive call writes
nothing. Comments and untouched lines in the YAML files are kept. In a
workspace, setting a value equal to its default removes the override, so
later changes to the defaults still reach that run.

Settable agent fields: `model`, `reasoning`, `timeout_min`, `cmd`,
`stdin_cmd`, and — in the defaults only — `enabled`. New agents are added to
`config/agents.yml` by hand.

Older research runs may carry top-level `agents:`, `reviewer:`, `submitter:`,
`outline_agent:` or `consistency_agent:` keys. They are still read, and any
assignment written by `configure` takes precedence over them.
