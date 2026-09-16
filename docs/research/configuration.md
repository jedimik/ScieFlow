# Configuration

Research workflows read three layers — nothing else hardcodes models, flags,
roles, or Zotero targets:

1. `config/agents.yml` — the agent registry (shared by all ScieFlow modules).
2. `config/defaults.yml` — role assignments (`assignments:`) and the research
   workflow defaults (`research:`).
3. `workspace/<slug>/config.yml` — per-run overrides.

Inspect the effective result with `scieflow agent show [--workspace <slug>]`
and change agents with `scieflow agent configure`; see the
[agent configuration guide](../agents.md).

## Global: `config/agents.yml` and `config/defaults.yml`

```yaml title="config/agents.yml (excerpt)"
agents:
  claude:
    cmd: "claude -p --dangerously-skip-permissions --model {model} {prompt}"
    model: claude-fable-5
    tier: primary
    timeout_min: 30
    enabled: true
  codex:
    cmd: "codex exec --sandbox workspace-write --model {model} -c model_reasoning_effort={reasoning} {prompt}"
    model: gpt-5.6-sol
    reasoning: medium
    tier: primary
    timeout_min: 180
    enabled: true
  agy:
    cmd: "agy --print {prompt} --model {model} --effort {reasoning} --print-timeout 55m --dangerously-skip-permissions"
    model: gemini-3.1-pro-high
    reasoning: high
    tier: support
    capabilities: [web-search, large-context]
    timeout_min: 60
    enabled: true
```

```yaml title="config/defaults.yml (research excerpt)"
research:
  max_papers: 40
  max_review_rounds: 3
  max_debate_rounds: 2          # perspective debate: discussion rounds after propose
  max_gaps: 10                  # gap-discovery: per-agent cap
  max_hypotheses: 5             # gap-discovery: per-agent cap
  auto_approve_outline: false   # paper-draft: pause for user outline approval
  zotero:
    library: user               # or "group:<id>"

assignments:
  research.search: [claude, codex, agy]
  research.cross-review: [claude, codex]
  research.gap-analysis: [claude, codex]
  research.debate: [claude, codex]
  research.journal-profile: [agy, claude]
  research.reviewer: codex
  research.submitter: claude
  research.outline: claude
  research.draft-authors: [claude, codex]
  research.consistency: codex
```

### Agent fields

| Field | Meaning |
| --- | --- |
| `cmd` | Command template. `{model}` and `{prompt}` are substituted as single argv tokens (safe for multi-line prompts and model names with spaces). |
| `stdin_cmd` | *Optional.* Alternate template used when a prompt is too large for argv (delivered via stdin instead). Needed only for CLIs like `agy` whose flag parsing breaks when the prompt token is dropped. |
| `model` | Model passed as `{model}`. Omit if the CLI should use its account default. |
| `tier` | `primary` or `support` — tier routing (root AGENTS.md rule 10): support agents only in support roles, paired with a primary. |
| `capabilities` | *Optional.* Free-form hints (e.g. `web-search`) coordinators use when routing support-tier tasks. |
| `reasoning` | *Optional.* Substituted as `{reasoning}` in the cmd template (e.g. codex `model_reasoning_effort`). |
| `timeout_min` | Per-dispatch timeout in minutes. A timed-out agent is logged and the phase continues without it. |
| `enabled` | `false` excludes the agent everywhere; `configure` refuses to assign a disabled agent. |
| `menu` | *Optional, read by coordinators, not code.* The provider menu shown to the user at the run configuration gate and by the `configure` wizard: available models, reasoning levels, and how each choice maps to `agent_overrides`. |

### How the runner picks argv vs. stdin

`scieflow agent run` embeds the prompt in argv until it exceeds
`SCIEFLOW_PROMPT_ARGV_LIMIT` bytes (default 100000; set the env var to
override). Above that it switches to stdin delivery — using `stdin_cmd` if the
agent defines one, otherwise dropping the `{prompt}` token from `cmd`. This
prevents the `Argument list too long` (E2BIG) failure on large manuscript
prompts.

## Per-run: `workspace/<slug>/config.yml`

Any key here overrides the global default **for that research only**.

Agent choices (`assignments:`, `agent_overrides:`) are written by
`scieflow agent configure --workspace <slug>` after the **run configuration
gate** (research AGENTS.md), where the coordinator recommends an
agent/model/reasoning assignment and asks you to confirm or adjust it. Only
differences from the defaults are stored.

```yaml
assignments:                  # written by scieflow agent configure
  research.search: [claude, agy]
  research.reviewer: codex
  research.submitter: claude
  research.outline: claude
agent_overrides:              # per-agent settings for this run only
  claude:
    model: claude-opus-5
  codex:
    reasoning: high           # fills {reasoning} in the cmd template
max_papers: 30
journal: "Nature Methods"     # paper-review + paper-draft: target journal
scope: full                   # or: sections: [Introduction, Methods]
max_debate_rounds: 1          # gap-discovery: shorter debate for this run
max_gaps: 6                   # gap-discovery: per-agent caps
max_hypotheses: 3
literature_from: workspace/2026-07-other-topic   # gap-discovery: reuse a lit-review's findings
gaps_from: workspace/2026-07-other-topic         # paper-draft: import a gap report + DOI list
title: "My Article Title"     # paper-draft: filled into the LaTeX skeleton
authors: "A. Author, B. Author"
auto_approve_outline: false   # paper-draft: keep the outline approval gate
zotero:
  library: "group:1234567"    # personal ("user") or a group ID
  collection: "ScieFlow/2026-07-topic"
```

## Zotero target resolution

Each research — and each paper — can save citations to a **different** Zotero
library or group. The target is resolved in order:

1. Workspace `config.yml` → `zotero:` block
2. Global `config/defaults.yml` → `research.zotero`
3. Fallback: your personal library (`user`)

The collection defaults to `ScieFlow/<slug>` when not specified. Export uses
the `zot` CLI (`--library user` or `--library group:<id>`), so anything `zot`
can reach, ScieFlow can target.

## Environment variables

| Variable | Effect |
| --- | --- |
| `SCIEFLOW_MAILTO` | Appended to the HTTP User-Agent for polite scholarly-API access (recommended). |
| `SCIEFLOW_PROMPT_ARGV_LIMIT` | Byte threshold above which prompts go via stdin (default 100000). |
