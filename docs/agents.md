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

| Role | Takes | Support tier allowed (without an exception) |
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

### Support agents as primary, per role

agy is a support-tier agent by default. You can still let it do a
primary-only role — or stand in for the primary partner in a support role —
for one role at a time:

```bash
# this run: agy reviews the manuscript
uv run scieflow agent configure --workspace <slug> \
    --assign research.reviewer=agy --promote research.reviewer --yes

# every run: agy does the consistency pass
uv run scieflow agent configure --assign research.consistency=agy \
    --promote research.consistency --yes

# remove the exception again
uv run scieflow agent configure --workspace <slug> --demote research.reviewer \
    --assign research.reviewer=codex --yes
```

The exception is stored as `support_as_primary: [role, …]` in
`config/defaults.yml` or the run's `config.yml`; run exceptions add to the
default ones, and one inherited from the defaults can only be removed there.
It covers that role only: agy stays support-tier everywhere else. `show`
marks promoted roles, warns while the exception is in use, and warns about an
exception whose role has no support agent assigned. Coordinator agents never
promote a role unless you ask for it (AGENTS.md rules 10 and 13). The
interactive wizard asks before adding the exception.

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
