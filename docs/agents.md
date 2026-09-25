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

The current user-selected OpenAI defaults are `codex` → `gpt-6-astra` with
`high` reasoning, `codex-paper` → the same model with `xhigh`, and
`codex-review` → the same model with `xhigh`. Paper drafting, outline and
revision use `codex-paper`; review, cross-review and consistency use
`codex-review`. These are separate CLI sessions/profiles, not independent
model families. `agy` remains a paired support agent for search and journal
profiling. Claude remains registered but has no default role assignment.
Existing run overrides still take precedence; use `agent show --workspace`
to check them. These are ScieFlow defaults, not changes to the user's global
Codex application settings.

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

## Per-role model and effort

A role's assignment can be more than an agent name: an entry may be
`{agent, model, reasoning}`, pinning that agent's model and effort **for
that role only** — every other role the agent performs keeps its own
setting (`config/agents.yml`, or its own role override):

```yaml
assignments:
  research.draft-authors:
    - {agent: codex, model: gpt-6-astra, reasoning: xhigh}
    - {agent: claude, model: claude-opus-5, reasoning: extended-thinking}
  research.reviewer: codex-review          # a plain string still works
```

This goes in `config/defaults.yml` for every run, or in a run's
`workspace/<slug>/config.yml` for that run only, under the same
`assignments:` key `--assign` already writes.

From the command line the same thing is the `--assign` shorthand
`ROLE=AGENT[@MODEL][/EFFORT][,…]` — model and effort are both optional, and
the option is repeatable for several roles at once:

```bash
uv run scieflow agent configure --assign \
  research.draft-authors=codex@gpt-6-astra/xhigh,claude@claude-opus-5/extended-thinking --yes
```

Dispatching for a role goes through `--role`, not a hard-coded agent name:

```bash
uv run scieflow agent run --role research.draft-authors codex-paper <prompt> <transcript>
```

`--role` looks up the role's assignment and applies its model/effort
override to that dispatch. If the named agent is **not** assigned to that
role, the dispatch is refused before anything runs — dispatching with
`--role` is how a wrong agent for a role gets caught, rather than silently
running under someone else's settings. Tier routing (rule 10) still applies
on top: a support agent on a primary-only role still needs the `--promote`
exception described above.

### Effort per provider

A role override's `reasoning` field means different things depending on
the agent's `cmd` template (`apply_role_override` in
`src/scieflow/core/agent_config.py`):

- **Codex** (`cmd` contains a `{reasoning}` placeholder): the value is
  substituted straight into the command line, e.g. `xhigh`.
- **Claude** (no `{reasoning}` placeholder — Claude has no effort flag):
  `reasoning: extended-thinking` prefixes the command with
  `env MAX_THINKING_TOKENS=32000 `; any other value (`default`, say)
  removes that prefix if it was there. `extended-thinking` is the only
  value that turns anything *on* for Claude — the effort levels a provider
  understands are listed per agent under `menu.reasoning` in
  `config/agents.yml`.

`model`, when set, always replaces the agent's configured model for that
role's dispatch. `agent show [--workspace <slug>] --json` includes
`role_overrides` in its output, so a coordinator agent can check what a
role will actually run before dispatching.

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

## The Agents page

`scieflow serve` (see [The local web app](web.md)) has a browser page at
`/agents` for the one piece of this that comes up often enough to want a
form: **which agent performs which role**. `GET /agents` shows the
assignments in effect for the project defaults, or for one run with
`?slug=<run>`; picking a role and an agent and choosing Preview calls
`service.plan_staffing`, the same planning step `scieflow agent configure`
uses, and shows the same unified diff `configure` would print. Nothing is
written until you choose Apply, which posts to `/agents` and calls
`service.apply_staffing` — the same validation, the same diff, the same
`assignments:` key in `config/defaults.yml` or the run's `config.yml`.
`?assign=role=agent` can be repeated in the URL to preview more than one
change at once, the same way `--assign` is repeatable on the command line.

The page is deliberately narrower than `configure`: it covers role
assignment only. Per-agent field edits (`model`, `reasoning`,
`timeout_min`, `cmd`, `stdin_cmd`, `enabled`), and granting or removing a
`--promote` exception (see ["Support agents as primary, per
role"](#support-agents-as-primary-per-role) above), stay on the CLI —
`scieflow agent configure --set ...`, `--promote` and `--demote`. A
one-click promotion would work against the whole point of `--promote`
being a deliberate, visible exception, so the page never offers it.

Older research runs may carry top-level `agents:`, `reviewer:`, `submitter:`,
`outline_agent:` or `consistency_agent:` keys. They are still read, and any
assignment written by `configure` takes precedence over them.

## The charter is already in the prompt

None of the above changes what an agent is told to do for a given run — that
is the run's [charter](runs.md#the-charter-what-this-run-agreed-to-do).
Whichever agent, model and effort a role resolves to, the prompt it is
dispatched with already opens with that run's current charter
(`agent_run.compose_prompt` prepends it independent of any role override —
role and model resolution never touch the charter). A coordinator or
sub-agent composing a prompt for a run should not restate the goal or paste
the charter back in — it is already at the top of what it receives, on
every turn.

### A coordinator may propose a charter, but cannot adopt its own proposal

An agent that wants to change the run's standing goal writes its proposed
plan to a file inside its own run (for example
`workspace/<slug>/proposals/charter.md`) and opens a gate naming that file —
**relative to the run's own directory**, not to wherever `gate open` was
invoked from, because that is the path an adoption later resolves against:

```bash
uv run scieflow gate open <slug> --kind charter-adoption \
    --question "Adopt this plan as the run's charter?" \
    --option adopt --option decline --file proposals/charter.md
```

An absolute path works too; either way, it must resolve inside the run — a
path that escapes it (`../..`, or an absolute path elsewhere) is refused,
never read, the same containment rule the artifact browser applies to a
browser-requested path.

`charter-adoption` is `requires_human: true`, the same as `scope-change` —
adopting a charter redefines what the run is *for*, so do not expect to
answer this gate yourself, even in an autonomous run and even with
`--in-scope`; `gate answer --as-agent` is refused for any gate marked this
way. Open the gate, then `gate wait` (or simply stop and let the human find
it) — the run page shows your proposal's full text next to the question, so
write it for a human to actually read, not just to satisfy a schema. If a
human answers `adopt`, ScieFlow validates and reads your proposal file
*before* recording that answer, then writes it as the run's new charter
version; if the file is missing, unreadable, empty, or outside the run, the
adoption is refused and the gate stays open rather than being recorded as
answered. Answering anything else leaves the charter unchanged.
